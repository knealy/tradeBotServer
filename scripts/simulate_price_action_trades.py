#!/usr/bin/env python3
"""Trade-simulating validator for ``core.price_action`` patterns.

Where ``scripts/validate_price_action_patterns.py`` measured raw
1-bar conditional probabilities (no trade, no stops, no costs), this
script SIMULATES the full trade lifecycle on each pattern detection:

    For every bar that fires pattern P:
        decide direction (continuation OR fade, per --bias)
        place a stop-bracket entry at the pattern bar's close
        compute stop_loss = entry ± (atr × stop_atr) OR (range × stop_frac)
        compute take_profit = entry ± (atr × tp_atr)
        walk the next ``--max-bars`` bars; on each:
            if SL hit first → -1R
            if TP hit first → +R_multiple
            else when last bar → exit at close, return (px - entry)/risk
    Aggregate: n trades, WR, mean R, total R, Sharpe-ish

Then layers contextual filters so we surface where the edge LIVES:

    --time-buckets   split trades by ET hour (09-10, 10-11, ...)
    --session        only take patterns in a specific session bucket
                     (asia, london, premarket, nyam, lunch, nypm, close, afterhours)
    --vwap-band      require pattern WITHIN ±band points of session VWAP
    --rsi-buckets    split by RSI(14) bucket on the pattern bar
    --at-swing       require pattern within ±N points of most recent
                     confirmed swing high (bearish patterns) or swing
                     low (bullish patterns) — the "pattern at S/R" filter
    --at-prior-day-hl  require pattern within ±N points of prior day H or L

Usage:
    .venv/bin/python scripts/simulate_price_action_trades.py \\
        --csv historical_data/price/MES_5m_databento.csv \\
        --since 2025-01-01 --bias contrarian --max-bars 12 \\
        --atr-period 14 --stop-atr 1.0 --tp-atr 2.0 \\
        --session nyam --at-swing 5

The "headline" output is per pattern (and per (pattern × time bucket))
the **expected R per trade** — that's the directly tradeable metric.
Anything > +0.20 R is interesting; > +0.40 R is a candidate strategy
(after slippage / commission consideration).
"""

from __future__ import annotations

import argparse
import csv
import math
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.price_action import CandleBar, Pattern, detect_patterns
from core.market_structure import (
    LiquiditySweep,
    Session,
    SwingKind,
    SwingTracker,
    classify_session,
    find_fair_value_gaps,
    find_liquidity_sweeps,
    find_order_blocks,
    find_swing_pivots,
    is_inside_fvg,
    is_inside_order_block,
    prior_session_levels,
)
from core.smc_setups import find_all_smc_setups


# ──────────────────────── CSV reader ────────────────────────


def _parse_ts(s: str) -> datetime:
    s = s.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    return datetime.fromisoformat(s)


def _read_csv(path: Path, since: Optional[datetime], until: Optional[datetime]) -> List[CandleBar]:
    bars: List[CandleBar] = []
    with path.open() as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            ts = _parse_ts(row["timestamp"])
            if since and ts < since:
                continue
            if until and ts > until:
                break
            bars.append(CandleBar(
                timestamp=ts,
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row.get("volume", 0) or 0),
            ))
    return bars


# ──────────────────── indicator helpers ─────────────────────


def _wilder_atr(bars: Sequence[CandleBar], period: int) -> List[Optional[float]]:
    """Wilder's smoothed ATR.  Returns None until period bars accumulated."""
    if not bars:
        return []
    trs: List[float] = []
    out: List[Optional[float]] = [None] * len(bars)
    prev_close = bars[0].close
    for i, b in enumerate(bars):
        tr = max(b.high - b.low, abs(b.high - prev_close), abs(b.low - prev_close))
        trs.append(tr)
        prev_close = b.close
        if i + 1 < period:
            continue
        if i + 1 == period:
            atr = sum(trs[: period]) / period
        else:
            atr = (out[i - 1] * (period - 1) + tr) / period  # type: ignore[operator]
        out[i] = atr
    return out


def _rsi(bars: Sequence[CandleBar], period: int = 14) -> List[Optional[float]]:
    """Wilder RSI.  Returns None until period+1 bars accumulated."""
    if not bars:
        return []
    out: List[Optional[float]] = [None] * len(bars)
    gains: List[float] = []
    losses: List[float] = []
    for i in range(1, len(bars)):
        delta = bars[i].close - bars[i - 1].close
        gains.append(max(delta, 0))
        losses.append(max(-delta, 0))
        if i < period:
            continue
        if i == period:
            avg_gain = sum(gains[: period]) / period
            avg_loss = sum(losses[: period]) / period
        else:
            prev_gain = (out_avg_gain if "out_avg_gain" in dir() else 0)
            avg_gain = (avg_gain * (period - 1) + gains[-1]) / period  # type: ignore
            avg_loss = (avg_loss * (period - 1) + losses[-1]) / period  # type: ignore
        if avg_loss == 0:
            rsi = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))
        out[i] = rsi
    return out


def _session_vwap(bars: Sequence[CandleBar]) -> List[Optional[float]]:
    """Daily-reset VWAP keyed off the bar timestamp's calendar date.

    Naive but matches how live VWAP is computed in this codebase
    (``core.research.pattern_conditional.session_vwap``).
    """
    out: List[Optional[float]] = [None] * len(bars)
    if not bars:
        return out
    cur_day = None
    pv_cum = 0.0
    v_cum = 0.0
    for i, b in enumerate(bars):
        day = b.timestamp.date()
        if day != cur_day:
            cur_day = day
            pv_cum = 0.0
            v_cum = 0.0
        tp = (b.high + b.low + b.close) / 3.0
        pv_cum += tp * b.volume
        v_cum += b.volume
        if v_cum > 0:
            out[i] = pv_cum / v_cum
    return out


# ────────────────────── trade simulator ─────────────────────


@dataclass
class _TradeResult:
    pattern: str
    entry_time: datetime
    entry_price: float
    side: int  # +1 long, -1 short
    r_pnl: float
    exit_reason: str
    bars_held: int


def _simulate_trade(
    bars: Sequence[CandleBar],
    entry_idx: int,
    side: int,
    stop_dist: float,
    tp_dist: float,
    max_bars: int,
    *,
    trail_atr_dist: Optional[float] = None,
    trail_trigger_r: float = 1.0,
    partial_tp_r: Optional[float] = None,
    partial_frac: float = 0.5,
) -> Tuple[float, str, int]:
    """Walk forward up to ``max_bars`` and return (r_pnl, exit_reason, bars_held).

    Entry is the close of ``bars[entry_idx]``.  Stop and TP distances are in
    absolute price points.  Conservative tie-break: if a bar's high/low spans
    BOTH stop and TP, count stop (worst case — common assumption in price-only
    simulators where intra-bar order is unknown).

    Adaptive-exit modes (optional, additive — all default off):

    1. **Trailing stop**: when set, once unrealised profit reaches
       ``trail_trigger_r × stop_dist`` (default 1R), the SL begins
       trailing the close by ``trail_atr_dist`` points.  SL only moves
       in the favorable direction (never against the position).  This
       is the "lock in profit + let runner ride" mechanic.

    2. **Partial profit + breakeven**: when set, a fraction
       ``partial_frac`` of the position is closed at
       ``partial_tp_r × stop_dist`` (default 0.5 closed at 1R), and the
       SL on the remaining ``(1 - partial_frac)`` is moved to breakeven.
       Final r_pnl = partial_tp_r × partial_frac + runner_pnl × (1 - frac).

    Trailing and partial-profit can be enabled simultaneously; the
    runner (after partial close) is what gets trailed.
    """
    if stop_dist <= 0:
        return 0.0, "invalid", 0
    entry = bars[entry_idx].close
    if side > 0:
        sl_px = entry - stop_dist
        tp_px = entry + tp_dist
    else:
        sl_px = entry + stop_dist
        tp_px = entry - tp_dist

    end_idx = min(entry_idx + max_bars, len(bars) - 1)

    # Adaptive-exit state.
    partial_taken = False
    partial_pnl_r = 0.0
    runner_frac = 1.0
    trailing_active = False

    for j in range(entry_idx + 1, end_idx + 1):
        b = bars[j]

        # Intra-bar logic order (matters for realism):
        #   1. Partial-profit check is INTRA-BAR — once the bar's high/low
        #      touches the partial target, the partial fill executes (we
        #      assume good fill in backtest land).
        #   2. SL / TP hit check uses sl_px / tp_px AS OF END OF PRIOR
        #      BAR.  Trailing SL updates do NOT apply within the same
        #      bar that produced the favorable MFE — otherwise a single
        #      bar could simultaneously create MFE, raise SL, and stop
        #      out at the new SL (an unrealistic same-bar feedback loop).
        #   3. At END of bar, the trailing SL is updated based on this
        #      bar's close (effective for NEXT bar).
        if partial_tp_r is not None and not partial_taken:
            partial_target = (
                entry + partial_tp_r * stop_dist if side > 0
                else entry - partial_tp_r * stop_dist
            )
            partial_hit = (
                b.high >= partial_target if side > 0
                else b.low <= partial_target
            )
            if partial_hit:
                partial_taken = True
                partial_pnl_r = partial_tp_r * partial_frac
                runner_frac = 1.0 - partial_frac
                # BE-SL move is deferred until END of bar so this bar's
                # remaining range can't trigger the new BE-SL.  We do it
                # below in the end-of-bar block.

        # Hit checks using start-of-bar sl_px / tp_px.
        sl_hit = (b.low <= sl_px) if side > 0 else (b.high >= sl_px)
        tp_hit = (b.high >= tp_px) if side > 0 else (b.low <= tp_px)
        if sl_hit:
            if side > 0:
                runner_r = (sl_px - entry) / stop_dist
            else:
                runner_r = (entry - sl_px) / stop_dist
            total_r = partial_pnl_r + runner_r * runner_frac
            if trailing_active:
                reason = "trailed_out"
            elif partial_taken:
                reason = "breakeven_out"
            else:
                reason = "stop_loss"
            return total_r, reason, j - entry_idx
        if tp_hit:
            r_mult = tp_dist / stop_dist
            runner_r = r_mult
            total_r = partial_pnl_r + runner_r * runner_frac
            return total_r, "take_profit", j - entry_idx

        # End-of-bar SL adjustments (effective NEXT bar).
        if partial_taken:
            # Move runner to breakeven exactly once (subsequent iterations
            # detect this by sl_px == entry).
            if (side > 0 and sl_px < entry) or (side < 0 and sl_px > entry):
                sl_px = entry
        if trail_atr_dist is not None and trail_atr_dist > 0:
            mfe = (b.high - entry) if side > 0 else (entry - b.low)
            if not trailing_active and mfe >= trail_trigger_r * stop_dist:
                trailing_active = True
            if trailing_active:
                new_sl_candidate = (
                    b.close - trail_atr_dist if side > 0
                    else b.close + trail_atr_dist
                )
                if side > 0:
                    sl_px = max(sl_px, new_sl_candidate)
                else:
                    sl_px = min(sl_px, new_sl_candidate)

    # Timed exit at last bar's close.
    exit_px = bars[end_idx].close
    if side > 0:
        runner_r = (exit_px - entry) / stop_dist
    else:
        runner_r = (entry - exit_px) / stop_dist
    total_r = partial_pnl_r + runner_r * runner_frac
    return total_r, "timed_exit", end_idx - entry_idx


# ───────────────────── pattern → direction ──────────────────────


# Direction policy.
#
# +1 = LONG under "continuation" bias (i.e., pattern is intrinsically
#       bullish so continuation = long, contrarian = short).
# -1 = SHORT under "continuation" bias (intrinsically bearish).
#  0 = neutral pattern (doji, inside, marubozu, etc.) — handled below
#      by treating LONG as the "continuation" direction so it surfaces
#      in the table.
#
# Notes per pattern:
#  - DOJI family + LONG_LEGGED: indecision; under contrarian bias we
#    expect mean-reversion → fade the prior bar's direction (we use
#    LONG as the placeholder "continuation"; meaning of contrarian
#    will then be SHORT).  Inadequate without context, but useful to
#    measure baseline.
#  - GRAVESTONE: bearish-reversal-leaning (top wick rejected) → -1 base
#  - DRAGONFLY:  bullish-reversal-leaning (bottom wick rejected) → +1 base
#  - MARUBOZU bull/bear: strong directional → +1/-1 base (continuation)
#  - OUTSIDE BAR: directional by close → +1/-1 base
#  - TWEEZER top/bottom: 2-bar reversal → -1 / +1 base
#  - HARAMI: small body after big body opposite color → reversal → +1/-1
#  - PIERCING / DARK CLOUD: 2-bar reversal → +1 / -1
#  - MORNING / EVENING STAR: 3-bar reversal → +1 / -1
#  - THREE WHITE SOLDIERS / BLACK CROWS: continuation → +1 / -1
_DIRECTIONS: Dict[str, int] = {
    # single-bar
    Pattern.BULLISH_PIN: +1,
    Pattern.BEARISH_PIN: -1,
    Pattern.DOJI: 0,
    Pattern.LONG_LEGGED_DOJI: 0,
    Pattern.GRAVESTONE_DOJI: -1,
    Pattern.DRAGONFLY_DOJI: +1,
    Pattern.BULLISH_MARUBOZU: +1,
    Pattern.BEARISH_MARUBOZU: -1,
    # two-bar
    Pattern.BULLISH_ENGULFING: +1,
    Pattern.BEARISH_ENGULFING: -1,
    Pattern.INSIDE_BAR: 0,
    Pattern.BULLISH_OUTSIDE_BAR: +1,
    Pattern.BEARISH_OUTSIDE_BAR: -1,
    Pattern.TWEEZER_TOP: -1,
    Pattern.TWEEZER_BOTTOM: +1,
    Pattern.BULLISH_HARAMI: +1,
    Pattern.BEARISH_HARAMI: -1,
    Pattern.PIERCING: +1,
    Pattern.DARK_CLOUD_COVER: -1,
    # three-bar
    Pattern.MORNING_STAR: +1,
    Pattern.EVENING_STAR: -1,
    Pattern.THREE_WHITE_SOLDIERS: +1,
    Pattern.THREE_BLACK_CROWS: -1,
}


def _direction_for(pattern: str, bias: str) -> int:
    base = _DIRECTIONS.get(pattern, 0)
    if base == 0:
        # Neutral pattern: treat as LONG under "continuation" so it
        # appears in tables.  contrarian flips to SHORT.
        base = +1
    if bias == "contrarian":
        return -base
    return base


# ──────────────────────── aggregation ──────────────────────────


@dataclass
class _BucketStats:
    label: str
    n: int = 0
    wins: int = 0
    losses: int = 0
    r_total: float = 0.0
    r_sq_total: float = 0.0
    exits: Dict[str, int] = field(default_factory=lambda: defaultdict(int))

    def add(self, r: float, exit_reason: str) -> None:
        self.n += 1
        self.r_total += r
        self.r_sq_total += r * r
        if r > 0:
            self.wins += 1
        elif r < 0:
            self.losses += 1
        self.exits[exit_reason] += 1

    @property
    def wr_pct(self) -> float:
        return (self.wins / self.n * 100.0) if self.n else 0.0

    @property
    def mean_r(self) -> float:
        return self.r_total / self.n if self.n else 0.0

    @property
    def std_r(self) -> float:
        if self.n < 2:
            return 0.0
        m = self.mean_r
        var = (self.r_sq_total / self.n) - (m * m)
        return math.sqrt(max(var, 0.0))

    @property
    def sharpe_ish(self) -> float:
        s = self.std_r
        return self.mean_r / s if s > 0 else 0.0


# ─────────────────────────── main ──────────────────────────────


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--csv", required=True, help="OHLCV CSV (timestamp,open,high,low,close,volume)")
    p.add_argument("--since", help="ISO date — drop bars older than this")
    p.add_argument("--until", help="ISO date — drop bars newer than this")
    p.add_argument("--bias", choices=["continuation", "contrarian"], default="contrarian",
                   help="Pattern direction policy (default: contrarian — based on the validate_* result)")
    p.add_argument("--max-bars", type=int, default=12, help="Trade max-hold horizon (default 12 bars)")
    p.add_argument("--atr-period", type=int, default=14)
    p.add_argument("--stop-atr", type=float, default=1.0, help="stop_dist = ATR × this")
    p.add_argument("--tp-atr", type=float, default=2.0, help="tp_dist = ATR × this  (R-multiple = tp_atr/stop_atr)")
    p.add_argument("--time-buckets", action="store_true",
                   help="Show per (pattern × ET hour) breakdown")
    p.add_argument("--vwap-band", type=float, default=0.0,
                   help="If > 0, restrict to patterns within ±band points of session VWAP")
    p.add_argument("--rsi-buckets", action="store_true",
                   help="Show per (pattern × RSI quintile) breakdown")
    p.add_argument("--session", choices=[s.value for s in Session], default=None,
                   help="Restrict to patterns in this ET session bucket "
                        "(asia/london/premarket/nyam/lunch/nypm/close/afterhours)")
    p.add_argument("--at-swing", type=float, default=0.0,
                   help="If > 0, require pattern within ±N points of the most recent "
                        "confirmed swing low (bullish-base) or swing high (bearish-base). "
                        "Implements the 'pattern at S/R' filter.")
    p.add_argument("--at-prior-day-hl", type=float, default=0.0,
                   help="If > 0, require pattern within ±N points of prior day's H or L. "
                        "Major liquidity-magnet filter.")
    p.add_argument("--swing-lookback", type=int, default=3,
                   help="Williams-fractal lookback used by --at-swing (default 3)")
    p.add_argument("--pattern", action="append", default=None,
                   help="Restrict to specific pattern(s); repeat the flag. "
                        "Default = all patterns from core.price_action.Pattern.ALL")
    # ── SMC / ICT structural filters ────────────────────────────────
    p.add_argument("--require-fvg", action="store_true",
                   help="Require pattern bar's close to lie inside an unmitigated "
                        "Fair Value Gap in the direction of the intended trade")
    p.add_argument("--require-order-block", action="store_true",
                   help="Require pattern bar's close to lie inside an unmitigated "
                        "Order Block in the direction of the intended trade")
    p.add_argument("--ob-impulse-atr", type=float, default=2.0,
                   help="Order block impulse threshold in ATR multiples (default 2.0)")
    p.add_argument("--ob-window", type=int, default=5,
                   help="Order block impulse window in bars (default 5)")
    p.add_argument("--include-sweep-as-pattern", action="store_true",
                   help="Treat liquidity sweeps as standalone patterns "
                        "(bullish_sweep / bearish_sweep); simulator trades them "
                        "with the contrarian-fade convention")
    p.add_argument("--sweep-min-poke", type=float, default=0.0,
                   help="Minimum poke distance (points) for sweep detection")
    # ── Compound SMC setups ────────────────────────────────────────
    p.add_argument("--include-smc-setups", action="store_true",
                   help="Run the three compound SMC setup detectors "
                        "(sweep_into_fvg, fvg_in_ob, choch_then_ob_retest) "
                        "and merge their signals into the simulator as "
                        "standalone 'patterns' with direction set by the "
                        "setup itself (overrides --bias)")
    p.add_argument("--smc-sweep-max-bars", type=int, default=20,
                   help="Max bars after a sweep to look for FVG return (default 20)")
    p.add_argument("--smc-choch-max-retest-bars", type=int, default=50,
                   help="Max bars after CHoCH's first OB to wait for retest (default 50)")
    # ── Adaptive-exit modes ─────────────────────────────────────────
    p.add_argument("--trail-atr", type=float, default=0.0,
                   help="If > 0, enable trailing stop at this ATR-distance "
                        "after MFE reaches --trail-trigger-r × stop_dist")
    p.add_argument("--trail-trigger-r", type=float, default=1.0,
                   help="MFE threshold (R) at which trailing activates (default 1.0)")
    p.add_argument("--partial-r", type=float, default=0.0,
                   help="If > 0, close --partial-frac of position at this R "
                        "and move runner SL to breakeven")
    p.add_argument("--partial-frac", type=float, default=0.5,
                   help="Fraction of position to close at partial-r (default 0.5)")
    # ── Stress-test mode ────────────────────────────────────────────
    p.add_argument("--stress-test", action="store_true",
                   help="Run a (TP-ATR × max-bars) matrix per pattern instead "
                        "of single config; outputs a heatmap of mean R / n per cell")
    p.add_argument("--stress-tp-list", default="1.0,1.5,2.0,3.0",
                   help="Comma-separated TP-ATR ratios for stress test (default 1,1.5,2,3)")
    p.add_argument("--stress-bars-list", default="12,24,48",
                   help="Comma-separated max-bars values for stress test (default 12,24,48)")
    args = p.parse_args()

    csv_path = Path(args.csv)
    if not csv_path.exists():
        sys.exit(f"❌ CSV not found: {csv_path}")
    since = _parse_ts(args.since) if args.since else None
    until = _parse_ts(args.until) if args.until else None

    print()
    print("━" * 100)
    print(f" trade-simulator over {csv_path.name}  bias={args.bias}  max_bars={args.max_bars}  "
           f"R={args.tp_atr/args.stop_atr:.2f}")
    if args.vwap_band > 0:
        print(f"  filter: within ±{args.vwap_band} pts of session VWAP")
    print("━" * 100)

    bars = _read_csv(csv_path, since, until)
    if len(bars) < 100:
        sys.exit(f"❌ Too few bars: {len(bars)}")
    print(f"  bars loaded: {len(bars):,}  ({bars[0].timestamp.date()} → {bars[-1].timestamp.date()})")

    atrs = _wilder_atr(bars, args.atr_period)
    rsis = _rsi(bars, 14) if args.rsi_buckets else [None] * len(bars)
    vwaps = _session_vwap(bars) if args.vwap_band > 0 else [None] * len(bars)
    pdh_pdl = prior_session_levels(bars, session_zone="America/New_York") if args.at_prior_day_hl > 0 else {}

    # Online swing tracker for the --at-swing filter.  Updated bar-by-bar
    # so we only consult swings confirmed at or before the pattern bar
    # (no look-ahead).
    swing_tracker = SwingTracker(lookback=args.swing_lookback) if args.at_swing > 0 else None

    # ── SMC structures (pre-computed over the full history) ────────
    # FVGs and order blocks include a mitigation pass after the fact;
    # is_inside_*() takes a ``bar_index`` arg so we never consult a
    # structure formed AFTER the current bar (no look-ahead).
    fvgs = find_fair_value_gaps(bars) if args.require_fvg else []
    order_blocks = (
        find_order_blocks(bars, impulse_threshold_atr=args.ob_impulse_atr,
                          window=args.ob_window, atr_period=args.atr_period)
        if args.require_order_block else []
    )

    # Optional standalone-sweep pattern: detect sweeps over the whole
    # series (offline) and index by bar to merge into the per-bar event
    # stream below.  Same look-ahead-safe semantics as patterns.
    sweep_events_by_bar: Dict[int, List[LiquiditySweep]] = {}
    if args.include_sweep_as_pattern:
        all_swings = find_swing_pivots(bars, lookback=args.swing_lookback)
        for sw in find_liquidity_sweeps(
            bars, all_swings, min_poke_points=args.sweep_min_poke,
            require_close_back_inside=True,
        ):
            sweep_events_by_bar.setdefault(sw.bar_index, []).append(sw)

    # Compound SMC setups: pre-compute and index by trigger bar.  Each
    # setup carries its own direction (NO --bias flip applied), so they
    # flow through the simulator as direction-locked "patterns".
    smc_setup_events_by_bar: Dict[int, list] = {}
    smc_setup_names = set()
    if args.include_smc_setups:
        setups = find_all_smc_setups(
            bars, swing_lookback=args.swing_lookback,
            sweep_max_bars=args.smc_sweep_max_bars,
            sweep_min_poke=args.sweep_min_poke,
            ob_impulse_atr=args.ob_impulse_atr,
            ob_window=args.ob_window, atr_period=args.atr_period,
            choch_max_retest_bars=args.smc_choch_max_retest_bars,
        )
        for name, sigs in setups.items():
            smc_setup_names.add(name)
            for s in sigs:
                smc_setup_events_by_bar.setdefault(s.bar_index, []).append(s)

    # Pattern whitelist (CLI --pattern can repeat).  Sweep names are
    # accepted alongside core Pattern.ALL when --include-sweep-as-pattern.
    sweep_names = {"bullish_sweep", "bearish_sweep"} if args.include_sweep_as_pattern else set()
    all_pattern_names = set(Pattern.ALL) | sweep_names | smc_setup_names
    allowed_patterns = set(args.pattern) if args.pattern else all_pattern_names
    target_session = Session(args.session) if args.session else None

    pattern_stats: Dict[str, _BucketStats] = {p: _BucketStats(label=p) for p in sorted(allowed_patterns)}
    hour_stats: Dict[Tuple[str, int], _BucketStats] = {}
    rsi_stats: Dict[Tuple[str, str], _BucketStats] = {}
    session_stats: Dict[Tuple[str, str], _BucketStats] = {}

    # ── Stress-test (TP × max-bars) matrix ──────────────────────────
    stress_tps: List[float] = []
    stress_bars: List[int] = []
    stress_stats: Dict[str, Dict[Tuple[float, int], _BucketStats]] = {}
    if args.stress_test:
        stress_tps = [float(x.strip()) for x in args.stress_tp_list.split(",") if x.strip()]
        stress_bars = [int(x.strip()) for x in args.stress_bars_list.split(",") if x.strip()]
        for name in allowed_patterns:
            stress_stats[name] = {
                (tp, mb): _BucketStats(label=f"{name} TP={tp}R MB={mb}")
                for tp in stress_tps for mb in stress_bars
            }

    # Adaptive-exit args resolved once per main call (atr-dependent values
    # are still recomputed per-bar inside the loop).
    trail_atr_mult = args.trail_atr if args.trail_atr > 0 else None
    partial_tp_r_val = args.partial_r if args.partial_r > 0 else None

    # Lightweight shim mimicking a PatternEvent for sweep-as-pattern.
    class _SweepEvent:
        __slots__ = ("name", "bar", "extras")
        def __init__(self, name: str, bar: CandleBar):
            self.name = name
            self.bar = bar
            self.extras = {}

    # Shim for compound SMC setups — carries a fixed direction (no
    # --bias flip applied because the setup already has a direction).
    class _SmcEvent:
        __slots__ = ("name", "bar", "extras", "direction")
        def __init__(self, name: str, bar: CandleBar, direction: int):
            self.name = name
            self.bar = bar
            self.extras = {}
            self.direction = direction

    window: List[CandleBar] = []
    n_signals = 0
    n_filtered = 0
    n_filtered_session = 0
    n_filtered_swing = 0
    n_filtered_pdh = 0
    n_filtered_fvg = 0
    n_filtered_ob = 0
    for i, b in enumerate(bars):
        window.append(b)
        if len(window) > 5:
            window.pop(0)
        if swing_tracker is not None:
            swing_tracker.update(b)
        atr = atrs[i]
        if atr is None or atr <= 0:
            continue
        events = list(detect_patterns(window))
        # Merge sweep-as-pattern events for this bar (if enabled).
        for sw in sweep_events_by_bar.get(i, []):
            name = "bearish_sweep" if sw.direction == -1 else "bullish_sweep"
            events.append(_SweepEvent(name, b))
        # Merge compound SMC setup events for this bar.
        for setup in smc_setup_events_by_bar.get(i, []):
            events.append(_SmcEvent(setup.name, b, setup.direction))
        if not events:
            continue
        # ── Session filter (pre-pattern: applies to ALL events on this bar)
        if target_session is not None:
            if classify_session(b.timestamp) != target_session:
                n_filtered_session += 1
                continue
        for ev in events:
            if ev.name not in allowed_patterns:
                continue
            # Sweep-as-pattern: a bearish sweep (price poked above swing
            # high then closed back below) is the classic stop-hunt
            # reversal — trade SHORT.  A bullish sweep (poke below low,
            # close back above) → trade LONG.  Independent of --bias.
            if ev.name == "bearish_sweep":
                side = -1
            elif ev.name == "bullish_sweep":
                side = +1
            elif hasattr(ev, "direction"):
                # SMC setup events carry their own direction; --bias does
                # not apply (the setup already has a direction by design).
                side = ev.direction
            else:
                side = _direction_for(ev.name, args.bias)
            if side == 0:
                continue
            # ── VWAP confluence filter
            if args.vwap_band > 0:
                v = vwaps[i]
                if v is None or abs(b.close - v) > args.vwap_band:
                    n_filtered += 1
                    continue
            # ── At-swing (S/R proximity) filter.  For LONG signals require
            # pattern's low within ±N of the most recent confirmed swing
            # low (bullish-base trade off support); for SHORT, pattern's
            # high within ±N of the most recent swing high (off resistance).
            if swing_tracker is not None:
                if side > 0:
                    sw = swing_tracker.most_recent_low
                    if sw is None or abs(b.low - sw.price) > args.at_swing:
                        n_filtered_swing += 1
                        continue
                else:
                    sw = swing_tracker.most_recent_high
                    if sw is None or abs(b.high - sw.price) > args.at_swing:
                        n_filtered_swing += 1
                        continue
            # ── Prior-day H/L filter
            if args.at_prior_day_hl > 0:
                lv = pdh_pdl.get(b.timestamp.date())
                if lv is None:
                    n_filtered_pdh += 1
                    continue
                near_pdh = abs(b.close - lv.high) <= args.at_prior_day_hl
                near_pdl = abs(b.close - lv.low) <= args.at_prior_day_hl
                if not (near_pdh or near_pdl):
                    n_filtered_pdh += 1
                    continue
            # ── Fair-Value-Gap filter: pattern close must be inside an
            # unmitigated FVG in the direction of the trade.
            if args.require_fvg:
                hit = is_inside_fvg(fvgs, bar_index=i, price=b.close,
                                     direction=side, require_unmitigated=True)
                if hit is None:
                    n_filtered_fvg += 1
                    continue
            # ── Order-Block filter
            if args.require_order_block:
                hit_ob = is_inside_order_block(order_blocks, bar_index=i, price=b.close,
                                                direction=side, require_unmitigated=True)
                if hit_ob is None:
                    n_filtered_ob += 1
                    continue
            stop_dist = atr * args.stop_atr
            tp_dist = atr * args.tp_atr
            trail_dist = (atr * trail_atr_mult) if trail_atr_mult is not None else None
            r_pnl, exit_reason, _ = _simulate_trade(
                bars, i, side, stop_dist, tp_dist, args.max_bars,
                trail_atr_dist=trail_dist,
                trail_trigger_r=args.trail_trigger_r,
                partial_tp_r=partial_tp_r_val,
                partial_frac=args.partial_frac,
            )
            pattern_stats[ev.name].add(r_pnl, exit_reason)
            n_signals += 1

            # ── Stress-test matrix: same event, multiple (TP × max-bars).
            if args.stress_test:
                cell_map = stress_stats.get(ev.name)
                if cell_map:
                    for tp_mult in stress_tps:
                        cell_tp_dist = atr * tp_mult
                        for mb in stress_bars:
                            cr, cre, _ = _simulate_trade(
                                bars, i, side, stop_dist, cell_tp_dist, mb,
                            )
                            cell_map[(tp_mult, mb)].add(cr, cre)
            if args.time_buckets:
                key = (ev.name, b.timestamp.hour)
                hour_stats.setdefault(key, _BucketStats(label=f"{ev.name}@{b.timestamp.hour:02d}h")).add(r_pnl, exit_reason)
            if args.rsi_buckets:
                r = rsis[i]
                if r is not None:
                    bucket = (
                        "rsi<30" if r < 30 else
                        "rsi<45" if r < 45 else
                        "rsi<55" if r < 55 else
                        "rsi<70" if r < 70 else
                        "rsi>=70"
                    )
                    key2 = (ev.name, bucket)
                    rsi_stats.setdefault(key2, _BucketStats(label=f"{ev.name}@{bucket}")).add(r_pnl, exit_reason)
            # ── Per (pattern × session) breakdown
            sess = classify_session(b.timestamp).value
            sk = (ev.name, sess)
            session_stats.setdefault(sk, _BucketStats(label=f"{ev.name}@{sess}")).add(r_pnl, exit_reason)

    print(f"  pattern signals taken: {n_signals:,}   filtered "
          f"vwap={n_filtered:,} session={n_filtered_session:,} swing={n_filtered_swing:,} "
          f"pdh={n_filtered_pdh:,} fvg={n_filtered_fvg:,} ob={n_filtered_ob:,}")
    print()
    hdr = f"  {'pattern':<24}  {'n':>5}  {'WR%':>6}  {'meanR':>7}  {'stdR':>6}  {'totalR':>8}  {'sharpe':>7}  {'sl/tp/timed':>14}"
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    pattern_rows = []
    # Pattern.ALL canonical order first, then any sweep-as-pattern names appended.
    extra_names = sorted([n for n in pattern_stats.keys() if n not in Pattern.ALL])
    for name in list(Pattern.ALL) + extra_names:
        if name not in pattern_stats:
            continue
        s = pattern_stats[name]
        if s.n == 0:
            print(f"  {name:<24}  {0:>5}  {'─':>6}  {'─':>7}  {'─':>6}  {'─':>8}  {'─':>7}  {'─':>14}")
            continue
        exits = f"{s.exits.get('stop_loss', 0)}/{s.exits.get('take_profit', 0)}/{s.exits.get('timed_exit', 0)}"
        pattern_rows.append((name, s))
        print(f"  {name:<24}  {s.n:>5}  {s.wr_pct:>6.1f}  {s.mean_r:>+7.3f}  {s.std_r:>6.2f}  "
               f"{s.r_total:>+8.1f}  {s.sharpe_ish:>+7.3f}  {exits:>14}")

    if not pattern_rows:
        print("\n  ⚠ No tradeable patterns under current filter.")
        return

    # Headline: rank by mean R × √n (penalize tiny-sample noise).
    pattern_rows.sort(key=lambda r: r[1].mean_r * math.sqrt(r[1].n), reverse=True)
    print()
    print("Top by mean R × √n  (positive = candidate edge worth deeper investigation):")
    for name, s in pattern_rows[:5]:
        score = s.mean_r * math.sqrt(s.n)
        print(f"  {name:<22}  mean_R={s.mean_r:+.3f}  n={s.n}  WR={s.wr_pct:.1f}%  score={score:+.2f}")

    if args.time_buckets and hour_stats:
        print()
        print("─" * 100)
        print(f" Per (pattern × ET hour)  — sorted by mean R")
        print("─" * 100)
        rows = sorted(hour_stats.values(), key=lambda s: s.mean_r, reverse=True)
        print(f"  {'pattern@hour':<32}  {'n':>5}  {'WR%':>6}  {'meanR':>7}  {'totalR':>8}")
        print("  " + "-" * 66)
        for s in rows[:15]:
            print(f"  {s.label:<32}  {s.n:>5}  {s.wr_pct:>6.1f}  {s.mean_r:>+7.3f}  {s.r_total:>+8.1f}")
        print(f"  ... ({len(rows) - 15} more)" if len(rows) > 15 else "")

    if args.rsi_buckets and rsi_stats:
        print()
        print("─" * 100)
        print(f" Per (pattern × RSI bucket)  — sorted by mean R")
        print("─" * 100)
        rows = sorted(rsi_stats.values(), key=lambda s: s.mean_r, reverse=True)
        print(f"  {'pattern@rsi':<34}  {'n':>5}  {'WR%':>6}  {'meanR':>7}  {'totalR':>8}")
        print("  " + "-" * 66)
        for s in rows[:15]:
            print(f"  {s.label:<34}  {s.n:>5}  {s.wr_pct:>6.1f}  {s.mean_r:>+7.3f}  {s.r_total:>+8.1f}")

    if session_stats:
        print()
        print("─" * 100)
        print(f" Per (pattern × session)  — sorted by mean R  (min n ≥ 30)")
        print("─" * 100)
        rows = [s for s in session_stats.values() if s.n >= 30]
        rows.sort(key=lambda s: s.mean_r, reverse=True)
        print(f"  {'pattern@session':<40}  {'n':>5}  {'WR%':>6}  {'meanR':>7}  {'totalR':>8}")
        print("  " + "-" * 76)
        for s in rows[:20]:
            print(f"  {s.label:<40}  {s.n:>5}  {s.wr_pct:>6.1f}  {s.mean_r:>+7.3f}  {s.r_total:>+8.1f}")

    # ── Stress-test matrix (only patterns with enough samples) ─────
    if args.stress_test:
        print()
        print("═" * 100)
        print(f" Stress-test: (TP-ATR × max-bars) matrix per pattern")
        print(f"   stop_atr={args.stop_atr}  ATR_period={args.atr_period}")
        print(f"   TP-ATR list = {stress_tps}    max-bars list = {stress_bars}")
        print("═" * 100)
        # Sort patterns by best-cell mean R to surface the strongest ones first.
        def _pattern_best_cell(name: str) -> float:
            cells = stress_stats.get(name, {})
            best = max((c.mean_r for c in cells.values() if c.n >= 30), default=-99.0)
            return best
        sorted_names = sorted(
            (n for n in stress_stats if any(c.n >= 30 for c in stress_stats[n].values())),
            key=_pattern_best_cell, reverse=True,
        )
        for name in sorted_names[:10]:
            cells = stress_stats[name]
            print(f"\n  ── {name}  (best mean R = {_pattern_best_cell(name):+.3f}) ──")
            # Header
            hdr = f"  {'max-bars':<10}" + "".join(f"  {'TP=' + str(tp) + 'R':>14}" for tp in stress_tps)
            print(hdr)
            for mb in stress_bars:
                row = f"  {mb:<10}"
                for tp in stress_tps:
                    s = cells.get((tp, mb))
                    if s is None or s.n < 30:
                        row += f"  {'─':>14}"
                    else:
                        row += f"  {s.mean_r:+.3f} n={s.n:<4}".rjust(16)
                print(row)

    print()


if __name__ == "__main__":
    main()
