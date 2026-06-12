#!/usr/bin/env python3
"""Optimization probe for the sweep_low_fade LONG primitive.

Background:
    The 2026-06-12 PM primitive-isolation probe
    (``scripts/probe_brain_predictive_power.py --group-by sweep``) found that
    a SWING-LOW LIQUIDITY SWEEP (price wicks below the most-recent confirmed
    swing low and closes back inside) carries the ONLY consistent
    cross-symbol positive sign-agreement WR on 5m bars (51.7 – 52.9% on
    6-bar forward returns across MGC / MNQ / MES).

    That probe answered "does the signal predict direction?".  It did NOT
    measure TRADEABLE edge (no stops, no targets, no costs, no time-of-day
    filters).  Before promoting sweep_low_fade to either a v2 brain event
    or a standalone strategy we need to know:

    1. What is the truth-mode edge (R per trade) on the bare signal?
    2. Which filter combinations push it from 52% directional WR to 55%+
       *tradeable* WR or +0.20R+ mean R per trade?

This probe answers BOTH questions by:

  * Building the SAME sweep_low signal the v2 brain emits (using
    ``core.market_structure.find_liquidity_sweeps`` against the rolling
    swing pivot from ``find_swing_pivots``).
  * For each detected sweep event, simulating a LONG fade trade with the
    canonical truth-mode fill model (``scripts.simulate_price_action_trades
    ._simulate_trade_truth``) — entry slip + commission + intrabar 1m
    resolution + force-flat at 16:00 ET.
  * Sweeping a grid of filter variables and reporting mean R / WR / trade
    count for each combination, ranked.

Filter variables explored:

  * **Timeframe**          1m / 5m / 15m / 30m bars (when CSVs exist)
  * **Min poke ticks**     wick must pierce the swing low by ≥ this many
                           ticks before counting as a real sweep
                           (raw probe used 0 → noise floor too low)
  * **Strength**           ``close_distance / poke_amount`` ratio ≥
                           threshold (filter-out shallow recoveries)
  * **Session**            only take sweeps fired during specified ET
                           session window (nyam / lunch / nypm / extended)
  * **Stop policy**        SL placed at ``sweep_extreme - buffer`` (tight
                           wick stop, original probe finding) versus
                           ``swing_low - buffer`` (wider structural stop)
  * **TP multiple (R)**    1.0 / 1.5 / 2.0 / 3.0 R-multiple targets
  * **Min hold bars**      0 (no time stop) vs 12 vs 24
  * **Swing lookback**     3 (default) vs 5 (stronger pivots)

Decision rule printed at the end:

  * **EDGE_FOUND**        any combo with n ≥ 30 AND mean R ≥ +0.20 AND
                          WR ≥ 50 % across ≥ 2 symbols → promote that
                          combo to MVP standalone or boost confidence
                          coefficient in v2 brain confluence helper.
  * **MARGINAL_EDGE**     any combo with n ≥ 30 AND mean R ∈ [+0.05, +0.20].
                          Track but don't deploy.
  * **NO_EDGE**           otherwise.

Usage::

    .venv/bin/python scripts/probe_sweep_low_fade_optimize.py \\
        --since 2025-09-01 --until 2026-06-01

    # Restrict the grid (faster iteration)
    .venv/bin/python scripts/probe_sweep_low_fade_optimize.py \\
        --since 2025-12-01 --until 2026-06-01 \\
        --timeframes 5m \\
        --min-poke-ticks 2 4 8 \\
        --tp-r 1.5 2.0

    # JSON output for downstream tooling
    .venv/bin/python scripts/probe_sweep_low_fade_optimize.py --json
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from itertools import product
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.market_structure import (  # type: ignore
    SwingKind,
    find_liquidity_sweeps,
    find_swing_pivots,
)
from scripts.simulate_price_action_trades import (  # type: ignore
    CandleBar,
    _read_csv,
    _simulate_trade_truth,
)

try:
    from zoneinfo import ZoneInfo

    ET = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover
    ET = None  # type: ignore


# ──────────────────────── per-symbol conventions ────────────────────────


SYMBOL_SPECS: Dict[str, Dict[str, Any]] = {
    "MGC": {
        "csv_1m": "historical_data/price/MGC_1m_databento.csv",
        "csv_5m": "historical_data/price/MGC_5m_databento.csv",
        "csv_15m": "historical_data/price/MGC_15m_databento.csv",
        "csv_30m": "historical_data/price/MGC_30m_databento.csv",
        "tick_size": 0.1,
        "point_value": 10.0,
        "commission_per_trade": 1.55,
    },
    "MNQ": {
        "csv_1m": "historical_data/price/MNQ_1m_databento.csv",
        "csv_5m": "historical_data/price/MNQ_5m_databento.csv",
        "csv_15m": "historical_data/price/MNQ_15m_databento.csv",
        "csv_30m": "historical_data/price/MNQ_30m_databento.csv",
        "tick_size": 0.25,
        "point_value": 2.0,
        "commission_per_trade": 1.55,
    },
    "MES": {
        "csv_1m": "historical_data/price/MES_1m_databento.csv",
        "csv_5m": "historical_data/price/MES_5m_databento.csv",
        "csv_15m": "historical_data/price/MES_15m_databento.csv",
        "csv_30m": "historical_data/price/MES_30m_databento.csv",
        "tick_size": 0.25,
        "point_value": 5.0,
        "commission_per_trade": 1.55,
    },
}


# ──────────────────────────── session helpers ────────────────────────────


def _bar_et_minutes(b: CandleBar) -> Optional[int]:
    if ET is None:
        return None
    try:
        ts = b.timestamp
        if ts.tzinfo is None:
            from datetime import timezone as _tz
            ts = ts.replace(tzinfo=_tz.utc)
        et = ts.astimezone(ET)
        return et.hour * 60 + et.minute
    except Exception:
        return None


_SESSION_WINDOWS: Dict[str, Tuple[int, int]] = {
    # ET minute-of-day half-open windows.
    "premarket": (4 * 60, 9 * 60 + 30),
    "nyam":      (9 * 60 + 30, 12 * 60),
    "lunch":     (12 * 60, 13 * 60 + 30),
    "nypm":      (13 * 60 + 30, 16 * 60),
    "rth":       (9 * 60 + 30, 16 * 60),
    "extended":  (4 * 60, 20 * 60),
    "any":       (0, 24 * 60),
}


def _in_session(b: CandleBar, session: str) -> bool:
    m = _bar_et_minutes(b)
    if m is None:
        return session == "any"
    lo, hi = _SESSION_WINDOWS.get(session, (0, 24 * 60))
    return lo <= m < hi


# ───────────────────── sweep event detection ─────────────────────────────


@dataclass
class SweepCandidate:
    """A swing-low sweep event detected on the bars[..idx] window."""
    bar_idx: int                  # index of the closing bar (sweep confirmation)
    swing_low: float              # the swept level
    sweep_extreme: float          # bar's low — how deep the wick went
    close_distance: float         # close - swing_low (positive ≥ 0)
    poke_amount: float            # swing_low - sweep_extreme (positive)
    strength: float               # close_distance / poke_amount


def find_sweep_low_candidates(
    bars: Sequence[CandleBar],
    *,
    swing_lookback: int,
    min_poke_points: float,
    min_strength: float,
    session_filter: str,
) -> List[SweepCandidate]:
    """Scan ``bars`` for swing-low sweep events satisfying the filter set.

    Look-ahead-safe: a sweep is only emitted on a bar B whose preceding
    pivot has been confirmed by the lookback-3 fractal logic — i.e. the
    swing low used was committed ≥ swing_lookback bars before B.
    """
    if len(bars) < 2 * swing_lookback + 2:
        return []
    out: List[SweepCandidate] = []
    swings = find_swing_pivots(bars, lookback=swing_lookback)
    if not swings:
        return []
    # find_liquidity_sweeps does the heavy lifting; we then filter.
    sweeps = find_liquidity_sweeps(
        bars,
        swings,
        min_poke_points=max(min_poke_points, 0.0),
        require_close_back_inside=True,
    )
    for sw in sweeps:
        if sw.direction != +1:
            # we only care about long-side sweeps in this probe
            continue
        # session filter is applied to the BAR that confirmed the sweep
        bar = bars[sw.bar_index]
        if session_filter != "any" and not _in_session(bar, session_filter):
            continue
        strength = (
            sw.close_distance / sw.poke_amount if sw.poke_amount > 0 else 0.0
        )
        if strength < min_strength:
            continue
        # We need the corresponding swing low to know where to place the stop.
        # In find_liquidity_sweeps, sw.swept_price == swing_low for a +1 dir.
        out.append(SweepCandidate(
            bar_idx=sw.bar_index,
            swing_low=sw.swept_price,
            sweep_extreme=bar.low,
            close_distance=sw.close_distance,
            poke_amount=sw.poke_amount,
            strength=round(strength, 4),
        ))
    return out


# ─────────────────────────── trade simulation ────────────────────────────


@dataclass
class TradeResult:
    symbol: str
    timeframe: str
    bar_timestamp: datetime
    swing_low: float
    sweep_extreme: float
    stop_dist: float
    r_pnl: float
    exit_reason: str
    bars_held: int


_STOP_POLICY_WICK = "wick"          # SL = sweep_extreme - buffer
_STOP_POLICY_STRUCT = "structural"  # SL = swing_low_minus_buffer (deeper but lower whipsaw)


def simulate_long_fade_trade(
    bars: Sequence[CandleBar],
    cand: SweepCandidate,
    *,
    sl_buffer_ticks: float,
    stop_policy: str,
    tp_r: float,
    max_hold_bars: int,
    force_flat_et_min: int,
    tick_size: float,
    point_value: float,
    commission_per_trade: float,
    bars_1m_by_ns: Optional[Dict[int, CandleBar]],
    agg_minutes: int,
) -> Optional[TradeResult]:
    """Simulate a LONG fade entry at bar.close + 1's open.

    Stop placement depends on ``stop_policy``:
      * "wick": SL = sweep_extreme - sl_buffer_ticks × tick_size
        (assumes the wick is the bottom — most conservative)
      * "structural": SL = swing_low - sl_buffer_ticks × tick_size
        (above the wick — wider, but less prone to wick-game stop-runs)
    """
    entry_idx = cand.bar_idx
    if entry_idx + 1 >= len(bars):
        return None
    # Entry price ≈ next-bar open; truth-mode simulator handles fill.
    entry_bar = bars[entry_idx + 1]
    entry_price = entry_bar.open
    if stop_policy == _STOP_POLICY_WICK:
        sl_price = cand.sweep_extreme - sl_buffer_ticks * tick_size
    elif stop_policy == _STOP_POLICY_STRUCT:
        sl_price = cand.swing_low - sl_buffer_ticks * tick_size
    else:
        raise ValueError(f"unknown stop_policy: {stop_policy}")
    stop_dist = entry_price - sl_price
    if stop_dist <= 0:
        # Entry already below the stop — invalid signal (rare; fast reversal).
        return None
    tp_dist = stop_dist * tp_r

    r_pnl, exit_reason, bars_held = _simulate_trade_truth(
        bars=bars,
        entry_idx=entry_idx,
        side=+1,
        stop_dist=stop_dist,
        tp_dist=tp_dist,
        max_bars=max_hold_bars,
        commission_per_trade=commission_per_trade,
        slippage_ticks=0.5,
        tick_size=tick_size,
        point_value=point_value,
        bars_1m_by_ns=bars_1m_by_ns,
        agg_minutes=agg_minutes,
        force_flat_et_minutes=force_flat_et_min,
    )
    if exit_reason == "invalid":
        return None
    return TradeResult(
        symbol="",  # filled in by caller
        timeframe="",
        bar_timestamp=bars[entry_idx].timestamp,
        swing_low=cand.swing_low,
        sweep_extreme=cand.sweep_extreme,
        stop_dist=round(stop_dist, 4),
        r_pnl=r_pnl,
        exit_reason=exit_reason,
        bars_held=bars_held,
    )


# ──────────────────────── per-combo aggregation ──────────────────────────


@dataclass
class ComboKey:
    symbol: str
    timeframe: str
    min_poke_ticks: float
    min_strength: float
    session: str
    stop_policy: str
    tp_r: float
    max_hold_bars: int
    swing_lookback: int

    def as_label(self) -> str:
        return (
            f"{self.symbol} {self.timeframe} "
            f"poke≥{self.min_poke_ticks}t str≥{self.min_strength:.2f} "
            f"sess={self.session} stop={self.stop_policy} tp={self.tp_r:.1f}R "
            f"hold≤{self.max_hold_bars} lk={self.swing_lookback}"
        )


@dataclass
class ComboStats:
    key: ComboKey
    n: int = 0
    wins: int = 0
    r_sum: float = 0.0
    r_sq_sum: float = 0.0

    def add(self, r: float) -> None:
        self.n += 1
        if r > 0:
            self.wins += 1
        self.r_sum += r
        self.r_sq_sum += r * r

    @property
    def mean_r(self) -> float:
        return self.r_sum / self.n if self.n else 0.0

    @property
    def wr(self) -> float:
        return 100.0 * self.wins / self.n if self.n else 0.0

    @property
    def stdev_r(self) -> float:
        if self.n < 2:
            return 0.0
        m = self.mean_r
        var = (self.r_sq_sum / self.n) - (m * m)
        return max(0.0, var) ** 0.5

    @property
    def sharpe_r(self) -> float:
        """Per-trade Sharpe (mean R / stdev R)."""
        sd = self.stdev_r
        if sd <= 0:
            return 0.0
        return self.mean_r / sd

    def as_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.key.symbol,
            "timeframe": self.key.timeframe,
            "min_poke_ticks": self.key.min_poke_ticks,
            "min_strength": self.key.min_strength,
            "session": self.key.session,
            "stop_policy": self.key.stop_policy,
            "tp_r": self.key.tp_r,
            "max_hold_bars": self.key.max_hold_bars,
            "swing_lookback": self.key.swing_lookback,
            "n": self.n,
            "wins": self.wins,
            "wr_pct": round(self.wr, 2),
            "mean_r": round(self.mean_r, 4),
            "stdev_r": round(self.stdev_r, 4),
            "sharpe_r": round(self.sharpe_r, 4),
        }


# ────────────────────────────── orchestration ────────────────────────────


def _aggregate_to_timeframe(
    bars_1m: Sequence[CandleBar], minutes: int
) -> List[CandleBar]:
    """Aggregate 1m bars into N-minute bars on aligned boundaries.

    Uses ET-aligned bucket = floor(epoch_minute / minutes) so adjacent bars
    in the result are exactly N minutes apart.  Volume is summed.

    Look-ahead-safe: only emits a bar once its bucket window is FULLY closed
    (i.e. we've seen a bar whose minute falls in the NEXT bucket).
    """
    if not bars_1m or minutes <= 0:
        return []
    out: List[CandleBar] = []
    cur_bucket: Optional[int] = None
    cur_o = cur_h = cur_l = cur_c = 0.0
    cur_v = 0.0
    cur_ts: Optional[datetime] = None
    for b in bars_1m:
        epoch_min = int(b.timestamp.timestamp() // 60)
        bucket = epoch_min // minutes
        if cur_bucket is None:
            cur_bucket = bucket
            cur_o, cur_h, cur_l, cur_c = b.open, b.high, b.low, b.close
            cur_v = b.volume
            cur_ts = b.timestamp
            continue
        if bucket != cur_bucket:
            out.append(CandleBar(
                timestamp=cur_ts or b.timestamp,
                open=cur_o, high=cur_h, low=cur_l, close=cur_c,
                volume=cur_v,
            ))
            cur_bucket = bucket
            cur_o, cur_h, cur_l, cur_c = b.open, b.high, b.low, b.close
            cur_v = b.volume
            cur_ts = b.timestamp
        else:
            cur_h = max(cur_h, b.high)
            cur_l = min(cur_l, b.low)
            cur_c = b.close
            cur_v += b.volume
    # Final bar is omitted — we can only emit a bar whose bucket has FULLY closed.
    return out


def _load_bars(
    spec: Dict[str, Any],
    timeframe: str,
    since,
    until,
    *,
    bars_1m: Optional[List[CandleBar]] = None,
) -> Optional[List[CandleBar]]:
    """Load bars for a timeframe.

    1m and 5m have direct CSVs.  15m and 30m are synthesized from 1m when
    no native CSV is available, so the optimization sweep can cover them
    without forcing a fresh datapull.
    """
    p = ROOT / spec.get(f"csv_{timeframe}", "")
    if p.exists() and p.suffix == ".csv":
        return _read_csv(p, since, until)
    # Synthesize from 1m.
    minutes = {"15m": 15, "30m": 30}.get(timeframe)
    if minutes is None:
        return None
    src = bars_1m
    if src is None:
        p_1m = ROOT / spec["csv_1m"]
        if not p_1m.exists():
            return None
        src = _read_csv(p_1m, since, until)
    return _aggregate_to_timeframe(src, minutes)


def _load_1m_index(spec: Dict[str, Any], since, until) -> Optional[Dict[int, CandleBar]]:
    p = ROOT / spec["csv_1m"]
    if not p.exists():
        return None
    bars = _read_csv(p, since, until)
    return {int(b.timestamp.timestamp() * 1e9): b for b in bars}


def detect_all_sweep_candidates(
    bars: Sequence[CandleBar],
    *,
    swing_lookback: int,
) -> List[SweepCandidate]:
    """Detect EVERY swing-low sweep with no filtering, for downstream filtering.

    Cached upstream so the same detection is reused across the entire
    (poke × strength × session × stop_policy × tp × hold) sub-grid for
    a fixed (symbol, timeframe, swing_lookback).
    """
    return find_sweep_low_candidates(
        bars,
        swing_lookback=swing_lookback,
        min_poke_points=0.0,
        min_strength=0.0,
        session_filter="any",
    )


def _filter_candidates(
    candidates: Sequence[SweepCandidate],
    bars: Sequence[CandleBar],
    *,
    min_poke_points: float,
    min_strength: float,
    session: str,
) -> List[SweepCandidate]:
    out: List[SweepCandidate] = []
    for c in candidates:
        if c.poke_amount < min_poke_points:
            continue
        if c.strength < min_strength:
            continue
        bar = bars[c.bar_idx]
        if session != "any" and not _in_session(bar, session):
            continue
        out.append(c)
    return out


def evaluate_combo(
    symbol: str,
    spec: Dict[str, Any],
    timeframe: str,
    bars: Sequence[CandleBar],
    bars_1m_by_ns: Optional[Dict[int, CandleBar]],
    *,
    min_poke_ticks: float,
    min_strength: float,
    session: str,
    stop_policy: str,
    sl_buffer_ticks: float,
    tp_r: float,
    max_hold_bars: int,
    swing_lookback: int,
    force_flat_et_min: int,
    candidates_cache: Optional[Sequence[SweepCandidate]] = None,
) -> ComboStats:
    """Run one (symbol × timeframe × filter combo) and return aggregated stats.

    ``candidates_cache`` lets the caller share a single
    ``detect_all_sweep_candidates`` result across many combos that only
    vary in their post-detection filters / trade params.  When omitted,
    we detect inline (slow, but convenient for one-shot tests).
    """
    tick = spec["tick_size"]
    pv = spec["point_value"]
    comm = spec["commission_per_trade"]
    min_poke_points = min_poke_ticks * tick
    agg_minutes = {"1m": 1, "5m": 5, "15m": 15, "30m": 30}.get(timeframe, 5)

    base = candidates_cache if candidates_cache is not None else (
        detect_all_sweep_candidates(bars, swing_lookback=swing_lookback)
    )
    candidates = _filter_candidates(
        base,
        bars,
        min_poke_points=min_poke_points,
        min_strength=min_strength,
        session=session,
    )
    key = ComboKey(
        symbol=symbol, timeframe=timeframe,
        min_poke_ticks=min_poke_ticks, min_strength=min_strength,
        session=session, stop_policy=stop_policy,
        tp_r=tp_r, max_hold_bars=max_hold_bars,
        swing_lookback=swing_lookback,
    )
    stats = ComboStats(key=key)
    for cand in candidates:
        r = simulate_long_fade_trade(
            bars=bars,
            cand=cand,
            sl_buffer_ticks=sl_buffer_ticks,
            stop_policy=stop_policy,
            tp_r=tp_r,
            max_hold_bars=max_hold_bars,
            force_flat_et_min=force_flat_et_min,
            tick_size=tick,
            point_value=pv,
            commission_per_trade=comm,
            bars_1m_by_ns=bars_1m_by_ns,
            agg_minutes=agg_minutes,
        )
        if r is None:
            continue
        stats.add(r.r_pnl)
    return stats


# ────────────────────────────── verdict logic ────────────────────────────


VERDICT_EDGE = "EDGE_FOUND"
VERDICT_MARGINAL = "MARGINAL_EDGE"
VERDICT_NONE = "NO_EDGE"


def aggregate_verdict(combo_results: List[ComboStats], *, min_n: int = 30) -> str:
    """Find best cross-symbol combo and decide overall verdict.

    A combo is keyed by (timeframe, min_poke_ticks, min_strength, session,
    stop_policy, tp_r, max_hold_bars, swing_lookback) — same filter spec
    applied to ALL symbols.  We require ≥ 2 of 3 symbols at the combo to
    hit the EDGE thresholds.
    """
    # Group by everything EXCEPT symbol.
    grouped: Dict[Tuple, List[ComboStats]] = defaultdict(list)
    for s in combo_results:
        k = (
            s.key.timeframe, s.key.min_poke_ticks, s.key.min_strength,
            s.key.session, s.key.stop_policy, s.key.tp_r,
            s.key.max_hold_bars, s.key.swing_lookback,
        )
        grouped[k].append(s)

    edge_combos: List[Tuple] = []
    marginal_combos: List[Tuple] = []
    for k, symbol_stats in grouped.items():
        hits_edge = 0
        hits_marginal = 0
        for s in symbol_stats:
            if s.n < min_n:
                continue
            if s.mean_r >= 0.20 and s.wr >= 50.0:
                hits_edge += 1
            elif s.mean_r >= 0.05:
                hits_marginal += 1
        if hits_edge >= 2:
            edge_combos.append(k)
        elif hits_edge + hits_marginal >= 2:
            marginal_combos.append(k)

    if edge_combos:
        return VERDICT_EDGE
    if marginal_combos:
        return VERDICT_MARGINAL
    return VERDICT_NONE


# ────────────────────────────────── CLI ─────────────────────────────────


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    p.add_argument("--symbols", nargs="+", default=["MGC", "MNQ", "MES"],
                   help="Symbols to probe (default all three).")
    p.add_argument("--timeframes", nargs="+", default=["5m"],
                   choices=["1m", "5m", "15m", "30m"],
                   help="Bar timeframes to probe.  Each requires the matching CSV.")
    p.add_argument("--since", default="2025-09-01",
                   help="UTC start date (inclusive).")
    p.add_argument("--until", default=None,
                   help="UTC end date (exclusive); default = end of data.")
    p.add_argument("--min-poke-ticks", nargs="+", type=float,
                   default=[0.0, 2.0, 4.0, 8.0],
                   help="Min wick penetration (in ticks) to count as a sweep.")
    p.add_argument("--min-strength", nargs="+", type=float,
                   default=[0.0, 0.5, 1.0],
                   help="Min close_distance / poke_amount ratio (1.0 = full recovery).")
    p.add_argument("--sessions", nargs="+",
                   default=["any", "nyam", "rth", "nypm"],
                   choices=list(_SESSION_WINDOWS.keys()),
                   help="Session filters to test.")
    p.add_argument("--stop-policies", nargs="+",
                   default=["wick", "structural"],
                   choices=["wick", "structural"],
                   help="Stop placement policy.")
    p.add_argument("--sl-buffer-ticks", type=float, default=2.0,
                   help="Buffer added below the stop reference (ticks).")
    p.add_argument("--tp-r", nargs="+", type=float,
                   default=[1.0, 1.5, 2.0, 3.0],
                   help="Take-profit R-multiples to test.")
    p.add_argument("--max-hold-bars", nargs="+", type=int,
                   default=[12, 24],
                   help="Max bars before time-stop.")
    p.add_argument("--swing-lookback", nargs="+", type=int,
                   default=[3, 5],
                   help="Swing-pivot lookback (lookback=5 → stronger pivots).")
    p.add_argument("--force-flat-et-min", type=int, default=16 * 60,
                   help="Force-flat at this ET minute-of-day (default 16:00).")
    p.add_argument("--min-n", type=int, default=30,
                   help="Minimum trades required for a combo to count.")
    p.add_argument("--top-n", type=int, default=20,
                   help="How many top combos to print in the human report.")
    p.add_argument("--no-intrabar", action="store_true",
                   help="Skip 1m intrabar resolution — much faster but less "
                        "accurate.  Use for initial grid sweeps; re-run the "
                        "top-K combos with intrabar for final R numbers.")
    p.add_argument("--json", action="store_true",
                   help="Emit JSON instead of human-readable report.")
    return p.parse_args(argv)


def _cross_symbol_aggregate(
    all_stats: List[ComboStats], *, min_n: int
) -> List[Tuple[Tuple, Dict[str, ComboStats], float, float, int]]:
    """Group by filter-combo and compute cross-symbol average mean R + WR.

    Returns rows of (combo_key_tuple, per_symbol_stats_map, avg_mean_r,
    avg_wr, total_n) sorted by avg_mean_r desc, with only rows where ALL
    per-symbol entries have ``n ≥ min_n``.
    """
    grouped: Dict[Tuple, Dict[str, ComboStats]] = defaultdict(dict)
    for s in all_stats:
        k = (
            s.key.timeframe, s.key.min_poke_ticks, s.key.min_strength,
            s.key.session, s.key.stop_policy, s.key.tp_r,
            s.key.max_hold_bars, s.key.swing_lookback,
        )
        grouped[k][s.key.symbol] = s

    rows: List[Tuple[Tuple, Dict[str, ComboStats], float, float, int]] = []
    for k, by_sym in grouped.items():
        if not all(s.n >= min_n for s in by_sym.values()):
            continue
        if len(by_sym) < 2:
            continue
        avg_r = sum(s.mean_r for s in by_sym.values()) / len(by_sym)
        avg_wr = sum(s.wr for s in by_sym.values()) / len(by_sym)
        total_n = sum(s.n for s in by_sym.values())
        rows.append((k, by_sym, avg_r, avg_wr, total_n))
    rows.sort(key=lambda r: (r[2], r[3]), reverse=True)
    return rows


def _format_report(
    all_stats: List[ComboStats],
    *,
    top_n: int,
    min_n: int,
    verdict: str,
) -> str:
    """Human-readable report — top combos by mean R, then by WR."""
    eligible = [s for s in all_stats if s.n >= min_n]
    eligible.sort(key=lambda s: (s.mean_r, s.wr), reverse=True)

    lines: List[str] = []
    lines.append("\n══════════════ sweep_low_fade OPTIMIZATION RESULTS ══════════════")
    lines.append(f"Total combos evaluated: {len(all_stats)}  "
                 f"(eligible n ≥ {min_n}: {len(eligible)})\n")

    lines.append("── PER-SYMBOL TOP combos (by mean R) ──")
    lines.append(
        f"  {'symbol':<5} {'tf':<4} {'poke':>5} {'str':>5} "
        f"{'session':<10} {'stop':<10} {'tp':>4} {'hold':>5} {'lk':>3} "
        f"{'n':>4} {'WR':>6} {'meanR':>8} {'shrp':>7}"
    )
    lines.append("  " + "-" * 92)
    for s in eligible[:top_n]:
        k = s.key
        lines.append(
            f"  {k.symbol:<5} {k.timeframe:<4} {k.min_poke_ticks:>4.0f}t "
            f"{k.min_strength:>4.2f} {k.session:<10} {k.stop_policy:<10} "
            f"{k.tp_r:>3.1f}R {k.max_hold_bars:>4} {k.swing_lookback:>3} "
            f"{s.n:>4} {s.wr:>5.1f}% {s.mean_r:>+7.4f} {s.sharpe_r:>+6.3f}"
        )

    lines.append("")
    lines.append("── CROSS-SYMBOL TOP combos (same filter across ALL probed symbols) ──")
    cross = _cross_symbol_aggregate(all_stats, min_n=min_n)
    lines.append(
        f"  {'tf':<4} {'poke':>5} {'str':>5} {'session':<10} {'stop':<10} "
        f"{'tp':>4} {'hold':>5} {'lk':>3} "
        f"{'avg_n':>6} {'avg_WR':>7} {'avg_R':>8} "
        f"(per-symbol meanR / WR)"
    )
    lines.append("  " + "-" * 110)
    for (k, by_sym, avg_r, avg_wr, total_n) in cross[:top_n]:
        (tf, poke, strength, sess, stop_pol, tp_r, hold, lk) = k
        per_sym = " | ".join(
            f"{sym}:{s.mean_r:+.3f}({s.wr:.0f}%n={s.n})"
            for sym, s in sorted(by_sym.items())
        )
        lines.append(
            f"  {tf:<4} {poke:>4.0f}t {strength:>4.2f} {sess:<10} "
            f"{stop_pol:<10} {tp_r:>3.1f}R {hold:>4} {lk:>3} "
            f"{total_n // len(by_sym):>6} {avg_wr:>6.1f}% {avg_r:>+7.4f} "
            f"  {per_sym}"
        )

    lines.append("")
    lines.append(f"VERDICT: {verdict}")
    return "\n".join(lines)


def _to_dt(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    return datetime.fromisoformat(s)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    since = _to_dt(args.since)
    until = _to_dt(args.until)

    all_stats: List[ComboStats] = []
    grid = list(product(
        args.timeframes,
        args.min_poke_ticks,
        args.min_strength,
        args.sessions,
        args.stop_policies,
        args.tp_r,
        args.max_hold_bars,
        args.swing_lookback,
    ))
    print(f"\nGrid size: {len(grid)} combos × {len(args.symbols)} symbols = "
          f"{len(grid) * len(args.symbols)} runs\n", flush=True)

    for symbol in args.symbols:
        spec = SYMBOL_SPECS.get(symbol)
        if spec is None:
            print(f"unknown symbol: {symbol}", file=sys.stderr)
            continue
        # 1m index is cheap to load ONCE per symbol and reuse across the grid.
        bars_1m_by_ns = None if args.no_intrabar else _load_1m_index(spec, since, until)
        # Cache raw 1m bars (list form) — drives the aggregator for 15m/30m
        # and is needed regardless of intrabar mode when those timeframes
        # appear in the grid.
        bars_1m_list: Optional[List[CandleBar]] = None
        if bars_1m_by_ns:
            bars_1m_list = sorted(bars_1m_by_ns.values(), key=lambda b: b.timestamp)
        elif any(tf in {"15m", "30m"} for tf, *_ in grid):
            p_1m = ROOT / spec["csv_1m"]
            if p_1m.exists():
                bars_1m_list = _read_csv(p_1m, since, until)
        bars_cache: Dict[str, Optional[List[CandleBar]]] = {}
        # candidate_cache: (timeframe, swing_lookback) → full unfiltered list
        cand_cache: Dict[Tuple[str, int], List[SweepCandidate]] = {}
        print(f"\n[{symbol}] processing combos...", flush=True)
        for combo_idx, (tf, poke, strength, session, stop_pol, tp_r,
             hold, lookback) in enumerate(grid):
            if tf not in bars_cache:
                bars_cache[tf] = _load_bars(
                    spec, tf, since, until, bars_1m=bars_1m_list,
                )
            bars = bars_cache[tf]
            if not bars:
                continue
            ck = (tf, lookback)
            if ck not in cand_cache:
                cand_cache[ck] = detect_all_sweep_candidates(
                    bars, swing_lookback=lookback,
                )
            stats = evaluate_combo(
                symbol=symbol, spec=spec, timeframe=tf,
                bars=bars, bars_1m_by_ns=bars_1m_by_ns,
                min_poke_ticks=poke, min_strength=strength,
                session=session, stop_policy=stop_pol,
                sl_buffer_ticks=args.sl_buffer_ticks,
                tp_r=tp_r, max_hold_bars=hold,
                swing_lookback=lookback,
                force_flat_et_min=args.force_flat_et_min,
                candidates_cache=cand_cache[ck],
            )
            all_stats.append(stats)
            if (combo_idx + 1) % 50 == 0:
                print(f"  [{symbol}] {combo_idx + 1}/{len(grid)} combos done", flush=True)

    verdict = aggregate_verdict(all_stats, min_n=args.min_n)

    if args.json:
        payload = {
            "verdict": verdict,
            "min_n": args.min_n,
            "combos": [s.as_dict() for s in all_stats],
        }
        print(json.dumps(payload, indent=2, default=str))
    else:
        print(_format_report(
            all_stats, top_n=args.top_n, min_n=args.min_n, verdict=verdict,
        ))

    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
