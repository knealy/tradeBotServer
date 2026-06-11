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
    Session,
    SwingKind,
    SwingTracker,
    classify_session,
    prior_session_levels,
)


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
) -> Tuple[float, str, int]:
    """Walk forward up to ``max_bars`` and return (r_pnl, exit_reason, bars_held).

    Entry is the close of ``bars[entry_idx]``.  Stop and TP distances are in
    absolute price points.  Conservative tie-break: if a bar's high/low spans
    BOTH stop and TP, count stop (worst case — common assumption in price-only
    simulators where intra-bar order is unknown).
    """
    entry = bars[entry_idx].close
    if side > 0:
        sl_px = entry - stop_dist
        tp_px = entry + tp_dist
    else:
        sl_px = entry + stop_dist
        tp_px = entry - tp_dist
    end_idx = min(entry_idx + max_bars, len(bars) - 1)
    for j in range(entry_idx + 1, end_idx + 1):
        b = bars[j]
        sl_hit = (b.low <= sl_px) if side > 0 else (b.high >= sl_px)
        tp_hit = (b.high >= tp_px) if side > 0 else (b.low <= tp_px)
        if sl_hit:
            return -1.0, "stop_loss", j - entry_idx
        if tp_hit:
            r_mult = tp_dist / stop_dist if stop_dist > 0 else 0.0
            return r_mult, "take_profit", j - entry_idx
    # Timed exit at last bar's close.
    exit_px = bars[end_idx].close
    if side > 0:
        r_pnl = (exit_px - entry) / stop_dist if stop_dist > 0 else 0.0
    else:
        r_pnl = (entry - exit_px) / stop_dist if stop_dist > 0 else 0.0
    return r_pnl, "timed_exit", end_idx - entry_idx


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

    # Pattern whitelist (CLI --pattern can repeat).
    allowed_patterns = set(args.pattern) if args.pattern else set(Pattern.ALL)
    target_session = Session(args.session) if args.session else None

    pattern_stats: Dict[str, _BucketStats] = {p: _BucketStats(label=p) for p in sorted(allowed_patterns)}
    hour_stats: Dict[Tuple[str, int], _BucketStats] = {}
    rsi_stats: Dict[Tuple[str, str], _BucketStats] = {}
    session_stats: Dict[Tuple[str, str], _BucketStats] = {}

    window: List[CandleBar] = []
    n_signals = 0
    n_filtered = 0
    n_filtered_session = 0
    n_filtered_swing = 0
    n_filtered_pdh = 0
    for i, b in enumerate(bars):
        window.append(b)
        if len(window) > 5:
            window.pop(0)
        if swing_tracker is not None:
            swing_tracker.update(b)
        atr = atrs[i]
        if atr is None or atr <= 0:
            continue
        events = detect_patterns(window)
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
            stop_dist = atr * args.stop_atr
            tp_dist = atr * args.tp_atr
            r_pnl, exit_reason, _ = _simulate_trade(
                bars, i, side, stop_dist, tp_dist, args.max_bars,
            )
            pattern_stats[ev.name].add(r_pnl, exit_reason)
            n_signals += 1
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
          f"vwap={n_filtered:,} session={n_filtered_session:,} swing={n_filtered_swing:,} pdh={n_filtered_pdh:,}")
    print()
    hdr = f"  {'pattern':<24}  {'n':>5}  {'WR%':>6}  {'meanR':>7}  {'stdR':>6}  {'totalR':>8}  {'sharpe':>7}  {'sl/tp/timed':>14}"
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    pattern_rows = []
    for name in [n for n in Pattern.ALL if n in pattern_stats]:
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

    print()


if __name__ == "__main__":
    main()
