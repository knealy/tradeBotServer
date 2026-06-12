#!/usr/bin/env python3
"""Multi-primitive combo probe — does combining sweep_low with other PA/SMC
primitives produce stronger edge than sweep_low alone?

Phase-3 follow-up to ``scripts/probe_sweep_low_fade_optimize.py``.  That
probe established that sweep_low_fade (LONG) has weak/marginal edge in
isolation:
* MES 15m nyam structural 1.5R: 56.8% WR, +0.29 R per trade (n=44).
* MGC / MNQ: negative across nearly every config.

The thesis here is that COMBINING sweep_low with other structural primitives
may produce a stronger / more consistent signal — even if those other
primitives are zero-signal in isolation (per the earlier brain primitive
probe).  Concretely:

  1. **sweep_low + bullish FVG below close**
     A bullish FVG (3-bar imbalance, ``bars[i-2].high < bars[i].low``)
     created BEFORE the sweep, located BELOW the sweep close, gives the
     trade a natural take-profit magnet on the way up.  Filter: require an
     unmitigated bullish FVG within ``--fvg-distance-points`` below the
     sweep close, formed within the last ``--fvg-lookback-bars`` bars.

  2. **sweep_low + recent CHoCH up**
     A change of character UP (downtrend's first HL) within
     ``--choch-lookback-bars`` BEFORE the sweep indicates the trend is
     already shifting — the sweep is the buyer's first stop-hunt of the
     new regime.  Filter: require a CHoCH_UP within the trailing window
     of confirmed structure events on the bars[..idx] subwindow.

  3. **sweep_low at prior-day-low**
     A sweep where the swept swing low coincides (within
     ``--pdl-tol-points``) with the prior trading day's RTH low.  This is
     a major liquidity magnet — the sweep is institutional accumulation
     at a known buying zone.  Filter: |swing_low - prior_day_low| ≤ tol.

  4. **sweep_low + RSI(14) oversold**
     An RSI on the snapshot bar below ``--rsi-oversold`` indicates the
     swing low got swept while price was already exhausted to the
     downside — classic capitulation setup.

For each filter (and combinations of two filters), the probe:
  * Walks 5m / 15m bars chronologically.
  * Detects ALL sweep_low candidates (no filtering).
  * Applies the combo's filter set; the surviving candidates are the
    "combo signal".
  * Simulates a LONG fade trade (truth-mode) with default
    ``--stop-policy structural --sl-buffer-ticks 2 --tp-r 1.5 --max-hold-bars 12``.
  * Aggregates n / WR / mean R / Sharpe per combo per symbol.

Decision rule printed at the end (per combo):
  * **EDGE_FOUND** — mean R ≥ +0.20 AND n ≥ 25 AND WR ≥ 50% on AT LEAST
    2 of the 3 symbols.
  * **MARGINAL** — mean R ≥ +0.10 on AT LEAST 1 symbol with n ≥ 25.
  * **NO_EDGE** — otherwise.

Usage::

    .venv/bin/python scripts/probe_sweep_combos.py \\
        --since 2025-09-01 --until 2026-06-01

    # JSON output for downstream tooling
    .venv/bin/python scripts/probe_sweep_combos.py --json
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.market_structure import (  # type: ignore
    StructureEvent,
    SwingKind,
    find_fair_value_gaps,
    find_liquidity_sweeps,
    find_swing_pivots,
)
from scripts.probe_sweep_low_fade_optimize import (  # type: ignore
    SYMBOL_SPECS,
    ComboStats,
    SweepCandidate,
    _aggregate_to_timeframe,
    _load_bars,
    _load_1m_index,
    detect_all_sweep_candidates,
    simulate_long_fade_trade,
)
from scripts.probe_prior_day_sweep_fade import (  # type: ignore
    compute_prior_day_rth_hl,
    _bar_et_date,
)
from scripts.simulate_price_action_trades import (  # type: ignore
    CandleBar,
    _read_csv,
)

try:
    from zoneinfo import ZoneInfo

    ET = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover
    ET = None  # type: ignore


# ──────────────────── filter implementations ─────────────────────────


def _bar_close(b: CandleBar) -> float:
    return float(b.close)


def has_recent_bullish_fvg_below(
    bars: Sequence[CandleBar],
    sweep_idx: int,
    *,
    distance_points: float,
    lookback_bars: int,
) -> bool:
    """True if there's an UNMITIGATED bullish FVG below ``bars[sweep_idx].close``
    within ``distance_points`` formed within the last ``lookback_bars`` bars.
    """
    if sweep_idx < 2:
        return False
    win_start = max(0, sweep_idx - lookback_bars)
    win = bars[win_start : sweep_idx + 1]
    if len(win) < 3:
        return False
    gaps = find_fair_value_gaps(win)
    sweep_close = _bar_close(bars[sweep_idx])
    for g in gaps:
        if g.direction != +1:
            continue
        # Must be below sweep close and within distance band.
        if g.upper > sweep_close:
            continue
        if sweep_close - g.upper > distance_points:
            continue
        # Unmitigated by the time of sweep_idx.
        if g.mitigated:
            continue
        return True
    return False


def has_recent_choch_up(
    bars: Sequence[CandleBar],
    sweep_idx: int,
    *,
    lookback_bars: int,
    swing_lookback: int,
) -> bool:
    """True if a CHoCH_UP event was confirmed within the lookback window.

    A CHoCH_UP fires when, in the most-recent sequence of swing pivots
    confirmed by ``swing_lookback``, the latest swing-low's label transitions
    from LL → HL (i.e. trend's first higher low after a downtrend).  We
    scan the sub-window bars[max(0, sweep_idx - lookback):sweep_idx] and
    return True if ANY bar in that window produced a CHoCH_UP.
    """
    if sweep_idx < 2 * swing_lookback + 1:
        return False
    win_start = max(0, sweep_idx - lookback_bars)
    win = bars[win_start : sweep_idx + 1]
    if len(win) < 2 * swing_lookback + 1:
        return False
    swings = find_swing_pivots(win, lookback=swing_lookback)
    lows = [s for s in swings if s.kind == SwingKind.LOW]
    if len(lows) < 2:
        return False
    # Check the MOST-RECENT low transition.
    prev = lows[-2].label.value
    cur = lows[-1].label.value
    return prev == "LL" and cur == "HL"


def is_at_prior_day_low(
    bars: Sequence[CandleBar],
    sweep_idx: int,
    rth_levels: Dict[date, Tuple[float, float]],
    swing_low: float,
    *,
    tol_points: float,
) -> bool:
    """True if the swept swing-low is within ``tol_points`` of the prior-day RTH low."""
    bar = bars[sweep_idx]
    d = _bar_et_date(bar)
    if d is None:
        return False
    candidates = [k for k in rth_levels.keys() if k < d]
    if not candidates:
        return False
    prior_d = max(candidates)
    _pdh, pdl = rth_levels[prior_d]
    return abs(swing_low - pdl) <= tol_points


def is_oversold_rsi(
    bars: Sequence[CandleBar],
    sweep_idx: int,
    *,
    period: int,
    threshold: float,
) -> bool:
    """True if RSI(period) at sweep_idx is below threshold.

    RSI is computed on bar closes via the standard Wilder's smoothing
    formula.  Returns False on insufficient history.
    """
    if sweep_idx < period:
        return False
    gains = 0.0
    losses = 0.0
    for j in range(sweep_idx - period + 1, sweep_idx + 1):
        delta = bars[j].close - bars[j - 1].close
        if delta > 0:
            gains += delta
        else:
            losses += -delta
    avg_gain = gains / period
    avg_loss = losses / period
    if avg_loss == 0:
        return False
    rs = avg_gain / avg_loss
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return rsi < threshold


# ──────────────────────── combo definitions ──────────────────────────


@dataclass(frozen=True)
class ComboFilter:
    """A named filter combination to evaluate."""
    name: str
    has_fvg: bool = False
    has_choch: bool = False
    has_pdl: bool = False
    has_rsi: bool = False


def default_combos() -> List[ComboFilter]:
    """The phase-3 combos catalogued in the docstring."""
    return [
        # Singletons (for comparison vs sweep-only baseline)
        ComboFilter("sweep_only"),
        ComboFilter("sweep+fvg", has_fvg=True),
        ComboFilter("sweep+choch_up", has_choch=True),
        ComboFilter("sweep+pdl", has_pdl=True),
        ComboFilter("sweep+rsi_oversold", has_rsi=True),
        # Doubles (the more interesting hypothesis)
        ComboFilter("sweep+fvg+pdl", has_fvg=True, has_pdl=True),
        ComboFilter("sweep+fvg+choch_up", has_fvg=True, has_choch=True),
        ComboFilter("sweep+choch_up+pdl", has_choch=True, has_pdl=True),
        ComboFilter("sweep+rsi+pdl", has_rsi=True, has_pdl=True),
        ComboFilter("sweep+rsi+choch_up", has_rsi=True, has_choch=True),
    ]


def matches_filter(
    bars: Sequence[CandleBar],
    cand: SweepCandidate,
    combo: ComboFilter,
    *,
    fvg_distance_points: float,
    fvg_lookback_bars: int,
    choch_lookback_bars: int,
    swing_lookback: int,
    pdl_rth_levels: Dict[date, Tuple[float, float]],
    pdl_tol_points: float,
    rsi_period: int,
    rsi_threshold: float,
) -> bool:
    """Return True iff the candidate satisfies the combo's full filter stack."""
    if combo.has_fvg:
        if not has_recent_bullish_fvg_below(
            bars, cand.bar_idx,
            distance_points=fvg_distance_points,
            lookback_bars=fvg_lookback_bars,
        ):
            return False
    if combo.has_choch:
        if not has_recent_choch_up(
            bars, cand.bar_idx,
            lookback_bars=choch_lookback_bars,
            swing_lookback=swing_lookback,
        ):
            return False
    if combo.has_pdl:
        if not is_at_prior_day_low(
            bars, cand.bar_idx, pdl_rth_levels, cand.swing_low,
            tol_points=pdl_tol_points,
        ):
            return False
    if combo.has_rsi:
        if not is_oversold_rsi(
            bars, cand.bar_idx,
            period=rsi_period, threshold=rsi_threshold,
        ):
            return False
    return True


# ──────────────────────── aggregation types ──────────────────────────


@dataclass
class ComboResult:
    symbol: str
    timeframe: str
    combo_name: str
    n: int = 0
    wins: int = 0
    r_sum: float = 0.0
    r_sq_sum: float = 0.0
    r_values: List[float] = field(default_factory=list)

    def add(self, r: float) -> None:
        self.n += 1
        if r > 0:
            self.wins += 1
        self.r_sum += r
        self.r_sq_sum += r * r
        self.r_values.append(r)

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
        sd = self.stdev_r
        if sd <= 0:
            return 0.0
        return self.mean_r / sd

    def as_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "combo": self.combo_name,
            "n": self.n,
            "wins": self.wins,
            "wr_pct": round(self.wr, 2),
            "mean_r": round(self.mean_r, 4),
            "stdev_r": round(self.stdev_r, 4),
            "sharpe_r": round(self.sharpe_r, 4),
        }


# ────────────────────────────── orchestration ────────────────────────


def evaluate_combos(
    symbol: str,
    spec: Dict[str, Any],
    timeframe: str,
    bars: Sequence[CandleBar],
    bars_1m_by_ns: Optional[Dict[int, CandleBar]],
    combos: Sequence[ComboFilter],
    *,
    swing_lookback: int,
    fvg_distance_points: float,
    fvg_lookback_bars: int,
    choch_lookback_bars: int,
    pdl_tol_points: float,
    rsi_period: int,
    rsi_threshold: float,
    stop_policy: str,
    sl_buffer_ticks: float,
    tp_r: float,
    max_hold_bars: int,
    force_flat_et_min: int,
    min_poke_ticks: float,
    min_strength: float,
    session: str,
) -> List[ComboResult]:
    """Evaluate ALL combos against the SAME bar series + sweep candidates."""
    tick = spec["tick_size"]
    pv = spec["point_value"]
    comm = spec["commission_per_trade"]
    agg_minutes = {"1m": 1, "5m": 5, "15m": 15, "30m": 30}.get(timeframe, 5)

    base = detect_all_sweep_candidates(bars, swing_lookback=swing_lookback)
    # Apply the BASE (non-combo-specific) sweep filters once.
    min_poke_points = min_poke_ticks * tick
    from scripts.probe_sweep_low_fade_optimize import _filter_candidates  # noqa
    candidates = _filter_candidates(
        base, bars,
        min_poke_points=min_poke_points,
        min_strength=min_strength,
        session=session,
    )

    pdl_levels = compute_prior_day_rth_hl(bars)

    results = {c.name: ComboResult(symbol=symbol, timeframe=timeframe, combo_name=c.name)
               for c in combos}

    for cand in candidates:
        # Simulate the trade ONCE — every combo that this candidate matches
        # gets the same r_pnl.
        r = simulate_long_fade_trade(
            bars=bars, cand=cand,
            sl_buffer_ticks=sl_buffer_ticks, stop_policy=stop_policy,
            tp_r=tp_r, max_hold_bars=max_hold_bars,
            force_flat_et_min=force_flat_et_min,
            tick_size=tick, point_value=pv,
            commission_per_trade=comm,
            bars_1m_by_ns=bars_1m_by_ns,
            agg_minutes=agg_minutes,
        )
        if r is None:
            continue
        for combo in combos:
            if matches_filter(
                bars, cand, combo,
                fvg_distance_points=fvg_distance_points,
                fvg_lookback_bars=fvg_lookback_bars,
                choch_lookback_bars=choch_lookback_bars,
                swing_lookback=swing_lookback,
                pdl_rth_levels=pdl_levels,
                pdl_tol_points=pdl_tol_points,
                rsi_period=rsi_period,
                rsi_threshold=rsi_threshold,
            ):
                results[combo.name].add(r.r_pnl)
    return list(results.values())


# ──────────────────────────── verdict logic ──────────────────────────


VERDICT_EDGE = "EDGE_FOUND"
VERDICT_MARGINAL = "MARGINAL"
VERDICT_NONE = "NO_EDGE"


def aggregate_verdicts(
    results: List[ComboResult], *, min_n: int = 25
) -> Dict[Tuple[str, str], str]:
    """Per (combo, timeframe) verdict aggregated across symbols.

    Returns map from (combo_name, timeframe) → verdict label.
    """
    grouped: Dict[Tuple[str, str], List[ComboResult]] = defaultdict(list)
    for r in results:
        grouped[(r.combo_name, r.timeframe)].append(r)
    out: Dict[Tuple[str, str], str] = {}
    for key, symbol_results in grouped.items():
        edge_hits = sum(
            1 for r in symbol_results
            if r.n >= min_n and r.mean_r >= 0.20 and r.wr >= 50.0
        )
        marginal_hits = sum(
            1 for r in symbol_results
            if r.n >= min_n and r.mean_r >= 0.10
        )
        if edge_hits >= 2:
            out[key] = VERDICT_EDGE
        elif marginal_hits >= 1:
            out[key] = VERDICT_MARGINAL
        else:
            out[key] = VERDICT_NONE
    return out


# ──────────────────────────── CLI ──────────────────────────────────


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    p.add_argument("--symbols", nargs="+", default=["MGC", "MNQ", "MES"])
    p.add_argument("--timeframes", nargs="+", default=["5m", "15m"],
                   choices=["1m", "5m", "15m", "30m"])
    p.add_argument("--since", default="2025-09-01")
    p.add_argument("--until", default=None)
    p.add_argument("--swing-lookback", type=int, default=3)
    # Combo filter params
    p.add_argument("--fvg-distance-points", type=float, default=10.0)
    p.add_argument("--fvg-lookback-bars", type=int, default=30)
    p.add_argument("--choch-lookback-bars", type=int, default=30)
    p.add_argument("--pdl-tol-points", type=float, default=3.0)
    p.add_argument("--rsi-period", type=int, default=14)
    p.add_argument("--rsi-threshold", type=float, default=30.0)
    # Base sweep filters
    p.add_argument("--min-poke-ticks", type=float, default=4.0)
    p.add_argument("--min-strength", type=float, default=0.5)
    p.add_argument("--session", default="any")
    # Trade params
    p.add_argument("--stop-policy", default="structural",
                   choices=["wick", "structural"])
    p.add_argument("--sl-buffer-ticks", type=float, default=2.0)
    p.add_argument("--tp-r", type=float, default=1.5)
    p.add_argument("--max-hold-bars", type=int, default=12)
    p.add_argument("--force-flat-et-min", type=int, default=16 * 60)
    p.add_argument("--no-intrabar", action="store_true")
    p.add_argument("--min-n", type=int, default=25)
    p.add_argument("--json", action="store_true")
    return p.parse_args(argv)


def _format_report(
    results: List[ComboResult],
    verdicts: Dict[Tuple[str, str], str],
    *,
    min_n: int,
    base_combo: str = "sweep_only",
) -> str:
    lines: List[str] = []
    lines.append("\n══════════════ sweep_low_fade COMBO RESULTS ══════════════")
    by_combo_tf: Dict[Tuple[str, str], Dict[str, ComboResult]] = defaultdict(dict)
    for r in results:
        by_combo_tf[(r.combo_name, r.timeframe)][r.symbol] = r

    # Order combos so the baseline ("sweep_only") prints first.
    combo_tf_order = sorted(
        by_combo_tf.keys(),
        key=lambda k: (0 if k[0] == base_combo else 1, k[1], k[0]),
    )

    for combo_tf in combo_tf_order:
        combo_name, tf = combo_tf
        by_sym = by_combo_tf[combo_tf]
        verdict = verdicts.get(combo_tf, "?")
        lines.append(f"\n{combo_name} ({tf})  verdict={verdict}")
        lines.append(
            f"  {'symbol':<5} {'n':>5} {'WR':>6} {'meanR':>8} {'shrp':>7}"
        )
        for sym in sorted(by_sym.keys()):
            r = by_sym[sym]
            note = " *insufficient n*" if r.n < min_n else ""
            lines.append(
                f"  {sym:<5} {r.n:>5} {r.wr:>5.1f}% {r.mean_r:>+7.4f} "
                f"{r.sharpe_r:>+6.3f}{note}"
            )
    return "\n".join(lines)


def _to_dt(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    return datetime.fromisoformat(s)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    since = _to_dt(args.since)
    until = _to_dt(args.until)
    combos = default_combos()

    all_results: List[ComboResult] = []
    for symbol in args.symbols:
        spec = SYMBOL_SPECS.get(symbol)
        if spec is None:
            print(f"unknown symbol: {symbol}", file=sys.stderr)
            continue
        bars_1m_by_ns = None if args.no_intrabar else _load_1m_index(spec, since, until)
        bars_1m_list: Optional[List[CandleBar]] = None
        if bars_1m_by_ns:
            bars_1m_list = sorted(bars_1m_by_ns.values(), key=lambda b: b.timestamp)
        elif any(tf in {"15m", "30m"} for tf in args.timeframes):
            p_1m = ROOT / spec["csv_1m"]
            if p_1m.exists():
                bars_1m_list = _read_csv(p_1m, since, until)
        for tf in args.timeframes:
            bars = _load_bars(spec, tf, since, until, bars_1m=bars_1m_list)
            if not bars:
                continue
            print(f"[{symbol} {tf}] evaluating {len(combos)} combos on {len(bars)} bars...",
                  flush=True)
            results = evaluate_combos(
                symbol=symbol, spec=spec, timeframe=tf,
                bars=bars, bars_1m_by_ns=bars_1m_by_ns,
                combos=combos,
                swing_lookback=args.swing_lookback,
                fvg_distance_points=args.fvg_distance_points,
                fvg_lookback_bars=args.fvg_lookback_bars,
                choch_lookback_bars=args.choch_lookback_bars,
                pdl_tol_points=args.pdl_tol_points,
                rsi_period=args.rsi_period,
                rsi_threshold=args.rsi_threshold,
                stop_policy=args.stop_policy,
                sl_buffer_ticks=args.sl_buffer_ticks,
                tp_r=args.tp_r,
                max_hold_bars=args.max_hold_bars,
                force_flat_et_min=args.force_flat_et_min,
                min_poke_ticks=args.min_poke_ticks,
                min_strength=args.min_strength,
                session=args.session,
            )
            all_results.extend(results)

    verdicts = aggregate_verdicts(all_results, min_n=args.min_n)

    if args.json:
        payload = {
            "min_n": args.min_n,
            "results": [r.as_dict() for r in all_results],
            "verdicts": {f"{k[0]}@{k[1]}": v for k, v in verdicts.items()},
        }
        print(json.dumps(payload, indent=2, default=str))
    else:
        print(_format_report(all_results, verdicts, min_n=args.min_n))

    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
