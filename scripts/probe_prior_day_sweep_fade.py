#!/usr/bin/env python3
"""Truth-mode viability probe for a prior-day RTH high/low sweep-fade strategy.

Thesis (see ``docs/STRATEGY_ARSENAL.md`` 2026-06-12 PM section):

  Prior-day RTH 9:30-16:00 ET high/low are visible institutional liquidity
  levels.  When price prints beyond one of those levels then closes back
  inside the prior-day range on the SAME 5m bar, that's a stop-run that
  exhausted — fade the close-back-inside.

This probe answers ONE question: does that mechanic have a positive
truth-mode edge across MGC + MNQ + MES on the available historical data?

Truth-mode is always on (mirrors the engine's fill model):
  * entry-slippage (default 0.5 tick)
  * 1m intrabar resolution when the matching ``*_1m_databento.csv`` exists
  * per-symbol commission and point-value conventions
  * force-flat at 16:00 ET (no overnight holding)

Decision rule printed at the end:

  * **PRODUCTIONABLE** → mean R ≥ +0.20 AND n ≥ 40 AND WR ≥ 50% on AT LEAST
    2 of the 3 symbols.  Worth promoting to an MVP strategy.
  * **MARGINAL**       → at least one symbol with mean R ∈ [+0.05, +0.20].
    Track but don't deploy.
  * **NO EDGE**        → otherwise.

Usage::

    .venv/bin/python scripts/probe_prior_day_sweep_fade.py \\
        --since 2025-09-01 --until 2026-06-01

    # With JSON output for downstream tooling
    .venv/bin/python scripts/probe_prior_day_sweep_fade.py \\
        --since 2025-12-01 --until 2026-06-01 --json
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Reuse canonical helpers so the truth-mode fill model lives in ONE place.
from scripts.simulate_price_action_trades import (  # type: ignore
    CandleBar,
    _read_csv,
    _simulate_trade_truth,
)

try:
    from zoneinfo import ZoneInfo

    ET = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover — zoneinfo is stdlib since 3.9
    ET = None  # type: ignore


# ──────────────────────── per-symbol conventions ────────────────────────


SYMBOL_SPECS: Dict[str, Dict[str, Any]] = {
    "MGC": {
        "csv_5m": "historical_data/price/MGC_5m_databento.csv",
        "csv_1m": "historical_data/price/MGC_1m_databento.csv",
        "tick_size": 0.1,
        "point_value": 10.0,
        "commission_per_trade": 1.55,
    },
    "MNQ": {
        "csv_5m": "historical_data/price/MNQ_5m_databento.csv",
        "csv_1m": "historical_data/price/MNQ_1m_databento.csv",
        "tick_size": 0.25,
        "point_value": 2.0,
        "commission_per_trade": 1.55,
    },
    "MES": {
        "csv_5m": "historical_data/price/MES_5m_databento.csv",
        "csv_1m": "historical_data/price/MES_1m_databento.csv",
        "tick_size": 0.25,
        "point_value": 5.0,
        "commission_per_trade": 1.55,
    },
}


# ───────────────────── prior-day RTH H/L computation ─────────────────────


def _bar_et_date(b: CandleBar) -> Optional[date]:
    """Trading date in ET for a bar.  None when ET conversion fails."""
    if ET is None:
        return None
    try:
        ts = b.timestamp
        if ts.tzinfo is None:
            # Databento CSVs are UTC-naive — treat them as UTC.
            from datetime import timezone as _tz

            ts = ts.replace(tzinfo=_tz.utc)
        return ts.astimezone(ET).date()
    except Exception:
        return None


def _bar_et_minutes(b: CandleBar) -> Optional[int]:
    """Minutes past midnight ET for a bar.  None when ET conversion fails."""
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


def compute_prior_day_rth_hl(
    bars: Sequence[CandleBar],
    rth_start_min: int = 9 * 60 + 30,
    rth_end_min: int = 16 * 60,
) -> Dict[date, Tuple[float, float]]:
    """Return ``{trading_date: (rth_high, rth_low)}`` for each ET trading date.

    A bar belongs to a trading date's RTH window iff its ET time is in
    ``[rth_start_min, rth_end_min)``.  Days with zero RTH bars are omitted.
    """
    daily: Dict[date, List[CandleBar]] = defaultdict(list)
    for b in bars:
        d = _bar_et_date(b)
        m = _bar_et_minutes(b)
        if d is None or m is None:
            continue
        if rth_start_min <= m < rth_end_min:
            daily[d].append(b)

    out: Dict[date, Tuple[float, float]] = {}
    for d, day_bars in daily.items():
        if not day_bars:
            continue
        hi = max(b.high for b in day_bars)
        lo = min(b.low for b in day_bars)
        out[d] = (hi, lo)
    return out


def _prior_trading_date(
    rth_levels: Dict[date, Tuple[float, float]], d: date
) -> Optional[date]:
    """Return the most-recent date < ``d`` that has RTH levels.  None if none."""
    candidates = [k for k in rth_levels.keys() if k < d]
    if not candidates:
        return None
    return max(candidates)


# ──────────────────────── sweep event detection ──────────────────────────


@dataclass
class SweepEvent:
    bar_idx: int
    bar_timestamp: datetime
    side: int  # +1 = long fade (sweep below), -1 = short fade (sweep above)
    sweep_level: float
    sweep_extreme: float  # the bar high (for sweep above) or low (for sweep below)


def find_sweep_events(
    bars: Sequence[CandleBar],
    rth_levels: Dict[date, Tuple[float, float]],
    entry_window_start_min: int,
    entry_window_end_min: int,
    min_penetration_ticks: float,
    tick_size: float,
) -> List[SweepEvent]:
    """Scan ``bars`` and emit a SweepEvent for each prior-day H/L sweep-fade.

    Definition (per 5m bar):
      sweep above prior-day high  → bar.high > pdh + min_pen
                                    AND bar.close ≤ pdh        → SHORT fade
      sweep below prior-day low   → bar.low  < pdl - min_pen
                                    AND bar.close ≥ pdl        → LONG  fade

    A bar can produce at most ONE event (sweep-above takes precedence if a
    pathological bar swept both levels, though that's effectively impossible
    for a 5m bar).  Only bars whose ET time falls within
    ``[entry_window_start_min, entry_window_end_min)`` are considered.
    """
    min_pen = min_penetration_ticks * tick_size
    out: List[SweepEvent] = []
    for i, b in enumerate(bars):
        d = _bar_et_date(b)
        m = _bar_et_minutes(b)
        if d is None or m is None:
            continue
        if not (entry_window_start_min <= m < entry_window_end_min):
            continue
        prior_d = _prior_trading_date(rth_levels, d)
        if prior_d is None:
            continue
        pdh, pdl = rth_levels[prior_d]
        # Sweep above prior-day high.
        if b.high > pdh + min_pen and b.close <= pdh:
            out.append(
                SweepEvent(
                    bar_idx=i,
                    bar_timestamp=b.timestamp,
                    side=-1,
                    sweep_level=pdh,
                    sweep_extreme=b.high,
                )
            )
            continue
        # Sweep below prior-day low.
        if b.low < pdl - min_pen and b.close >= pdl:
            out.append(
                SweepEvent(
                    bar_idx=i,
                    bar_timestamp=b.timestamp,
                    side=+1,
                    sweep_level=pdl,
                    sweep_extreme=b.low,
                )
            )
    return out


# ──────────────────────────── trade simulation ───────────────────────────


@dataclass
class TradeResult:
    symbol: str
    side: int
    bar_timestamp: datetime
    sweep_level: float
    stop_dist: float
    r_pnl: float
    exit_reason: str
    bars_held: int


def _load_1m_index(spec: Dict[str, Any], since: Optional[datetime], until: Optional[datetime]) -> Optional[Dict[int, CandleBar]]:
    p = ROOT / spec["csv_1m"]
    if not p.exists():
        return None
    bars = _read_csv(p, since, until)
    return {int(b.timestamp.timestamp() * 1e9): b for b in bars}


def simulate_sweep_trades(
    symbol: str,
    bars_5m: Sequence[CandleBar],
    events: Sequence[SweepEvent],
    spec: Dict[str, Any],
    *,
    sl_buffer_ticks: float,
    tp_r: float,
    max_hold_bars: int,
    force_flat_et_min: int,
    bars_1m_by_ns: Optional[Dict[int, CandleBar]],
) -> List[TradeResult]:
    """Simulate each sweep event as a fade trade, returning per-trade results.

    SL = sweep_extreme ± sl_buffer_ticks (just beyond the sweep wick).  This is
    the most conservative stop placement — invalidates the thesis if price
    re-runs the wick.  TP = tp_r × stop_dist.
    """
    tick_size = float(spec["tick_size"])
    point_value = float(spec["point_value"])
    commission = float(spec["commission_per_trade"])
    sl_buffer = sl_buffer_ticks * tick_size

    results: List[TradeResult] = []
    for ev in events:
        # Entry is at the NEXT bar open (mirrors engine MARKET-order semantics
        # in _simulate_trade_truth, which adds slip and walks from entry_idx+1).
        if ev.bar_idx + 1 >= len(bars_5m):
            continue
        next_bar = bars_5m[ev.bar_idx + 1]
        # Hypothetical entry price (slippage applied inside _simulate_trade_truth).
        entry_px_est = next_bar.open
        if ev.side > 0:  # LONG fade: SL beneath the sweep low
            stop_px = ev.sweep_extreme - sl_buffer
            stop_dist = max(entry_px_est - stop_px, tick_size)
        else:  # SHORT fade: SL above the sweep high
            stop_px = ev.sweep_extreme + sl_buffer
            stop_dist = max(stop_px - entry_px_est, tick_size)
        tp_dist = tp_r * stop_dist
        r_pnl, exit_reason, bars_held = _simulate_trade_truth(
            bars_5m,
            ev.bar_idx,
            ev.side,
            stop_dist,
            tp_dist,
            max_hold_bars,
            commission_per_trade=commission,
            slippage_ticks=0.5,
            tick_size=tick_size,
            point_value=point_value,
            bars_1m_by_ns=bars_1m_by_ns,
            agg_minutes=5,
            force_flat_et_minutes=force_flat_et_min,
        )
        if exit_reason == "invalid":
            continue
        results.append(
            TradeResult(
                symbol=symbol,
                side=ev.side,
                bar_timestamp=ev.bar_timestamp,
                sweep_level=ev.sweep_level,
                stop_dist=stop_dist,
                r_pnl=r_pnl,
                exit_reason=exit_reason,
                bars_held=bars_held,
            )
        )
    return results


# ─────────────────────────────── summary ─────────────────────────────────


@dataclass
class SymbolSummary:
    symbol: str
    n: int = 0
    n_long: int = 0
    n_short: int = 0
    wins: int = 0
    mean_r: float = 0.0
    median_r: float = 0.0
    total_r: float = 0.0
    total_dollar: float = 0.0
    mean_r_long: float = 0.0
    mean_r_short: float = 0.0
    wr: float = 0.0
    exit_reasons: Dict[str, int] = field(default_factory=dict)


def summarise(symbol: str, trades: Sequence[TradeResult], spec: Dict[str, Any]) -> SymbolSummary:
    s = SymbolSummary(symbol=symbol)
    s.n = len(trades)
    if not trades:
        return s
    rs = [t.r_pnl for t in trades]
    s.mean_r = sum(rs) / len(rs)
    rs_sorted = sorted(rs)
    mid = len(rs_sorted) // 2
    s.median_r = (
        rs_sorted[mid]
        if len(rs_sorted) % 2 == 1
        else 0.5 * (rs_sorted[mid - 1] + rs_sorted[mid])
    )
    s.total_r = sum(rs)
    # Per-trade $ PnL = r_pnl × stop_dist × point_value.
    s.total_dollar = sum(
        t.r_pnl * t.stop_dist * float(spec["point_value"]) for t in trades
    )
    s.wins = sum(1 for r in rs if r > 0)
    s.wr = 100.0 * s.wins / s.n
    longs = [t.r_pnl for t in trades if t.side > 0]
    shorts = [t.r_pnl for t in trades if t.side < 0]
    s.n_long = len(longs)
    s.n_short = len(shorts)
    s.mean_r_long = sum(longs) / len(longs) if longs else 0.0
    s.mean_r_short = sum(shorts) / len(shorts) if shorts else 0.0
    reasons: Dict[str, int] = defaultdict(int)
    for t in trades:
        reasons[t.exit_reason] += 1
    s.exit_reasons = dict(reasons)
    return s


# ────────────────────────────── reporting ────────────────────────────────


def _format_table(summaries: Sequence[SymbolSummary]) -> str:
    head = (
        f"{'symbol':<8} {'n':>4} {'n_long':>6} {'n_short':>7} {'WR':>6} "
        f"{'meanR':>8} {'medR':>7} {'totR':>7} {'$PnL':>11}"
    )
    rows = [head, "-" * len(head)]
    for s in summaries:
        rows.append(
            f"{s.symbol:<8} {s.n:>4} {s.n_long:>6} {s.n_short:>7} "
            f"{s.wr:>5.1f}% {s.mean_r:>8.3f} {s.median_r:>7.3f} "
            f"{s.total_r:>7.2f} {s.total_dollar:>+11.2f}"
        )
    return "\n".join(rows)


def _verdict(summaries: Sequence[SymbolSummary]) -> str:
    """PRODUCTIONABLE / MARGINAL / NO EDGE per the docstring decision rule."""
    prod_count = sum(
        1
        for s in summaries
        if s.mean_r >= 0.20 and s.n >= 40 and s.wr >= 50.0
    )
    marginal_count = sum(
        1 for s in summaries if 0.05 <= s.mean_r < 0.20
    )
    if prod_count >= 2:
        return "PRODUCTIONABLE"
    if prod_count >= 1 or marginal_count >= 1:
        return "MARGINAL"
    return "NO EDGE"


# ──────────────────────────────── CLI ────────────────────────────────────


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--since", type=str, default=None, help="ISO date / datetime (inclusive)")
    p.add_argument("--until", type=str, default=None, help="ISO date / datetime (exclusive)")
    p.add_argument(
        "--symbols",
        type=str,
        default="MGC,MNQ,MES",
        help="Comma-separated symbols (subset of MGC,MNQ,MES)",
    )
    p.add_argument(
        "--entry-window-start",
        type=str,
        default="10:00",
        help="Earliest ET wall-clock for sweep-event entries (HH:MM)",
    )
    p.add_argument(
        "--entry-window-end",
        type=str,
        default="15:00",
        help="Latest ET wall-clock for sweep-event entries (HH:MM, exclusive)",
    )
    p.add_argument(
        "--min-penetration-ticks",
        type=float,
        default=2.0,
        help="Minimum ticks past prior-day H/L to count as a sweep (default 2)",
    )
    p.add_argument(
        "--sl-buffer-ticks",
        type=float,
        default=2.0,
        help="Ticks beyond the sweep wick for SL placement (default 2)",
    )
    p.add_argument("--tp-r", type=float, default=1.5, help="TP distance in R (default 1.5)")
    p.add_argument(
        "--max-hold-bars",
        type=int,
        default=12,
        help="Max 5m bars before time-stop (default 12 = 60 min)",
    )
    p.add_argument(
        "--force-flat-et",
        type=str,
        default="16:00",
        help="ET wall-clock cutoff for force-flat (default 16:00)",
    )
    p.add_argument("--no-1m", action="store_true", help="Skip 1m intrabar resolution")
    p.add_argument("--json", action="store_true", help="Emit machine-readable summary")
    args = p.parse_args()

    def _parse_hhmm(s: str) -> int:
        h, m = s.split(":")
        return int(h) * 60 + int(m)

    def _parse_dt(s: Optional[str]) -> Optional[datetime]:
        if not s:
            return None
        for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.strptime(s, fmt)
            except ValueError:
                pass
        return datetime.fromisoformat(s)

    since = _parse_dt(args.since)
    until = _parse_dt(args.until)
    entry_start = _parse_hhmm(args.entry_window_start)
    entry_end = _parse_hhmm(args.entry_window_end)
    force_flat = _parse_hhmm(args.force_flat_et)
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]

    all_summaries: List[SymbolSummary] = []
    per_symbol_trades: Dict[str, List[TradeResult]] = {}

    for sym in symbols:
        spec = SYMBOL_SPECS.get(sym)
        if spec is None:
            print(f"⚠️  unknown symbol {sym}, skipping", file=sys.stderr)
            continue
        csv_path = ROOT / spec["csv_5m"]
        if not csv_path.exists():
            print(f"⚠️  missing CSV {csv_path}, skipping {sym}", file=sys.stderr)
            continue
        bars = _read_csv(csv_path, since, until)
        if len(bars) < 200:
            print(f"⚠️  {sym}: only {len(bars)} bars in window, skipping", file=sys.stderr)
            continue
        rth = compute_prior_day_rth_hl(bars)
        events = find_sweep_events(
            bars,
            rth,
            entry_window_start_min=entry_start,
            entry_window_end_min=entry_end,
            min_penetration_ticks=args.min_penetration_ticks,
            tick_size=float(spec["tick_size"]),
        )
        bars_1m_idx = None
        if not args.no_1m:
            bars_1m_idx = _load_1m_index(spec, since, until)
        trades = simulate_sweep_trades(
            sym,
            bars,
            events,
            spec,
            sl_buffer_ticks=args.sl_buffer_ticks,
            tp_r=args.tp_r,
            max_hold_bars=args.max_hold_bars,
            force_flat_et_min=force_flat,
            bars_1m_by_ns=bars_1m_idx,
        )
        per_symbol_trades[sym] = trades
        all_summaries.append(summarise(sym, trades, spec))

    verdict = _verdict(all_summaries)

    if args.json:
        out = {
            "verdict": verdict,
            "params": {
                "since": args.since,
                "until": args.until,
                "entry_window_start": args.entry_window_start,
                "entry_window_end": args.entry_window_end,
                "min_penetration_ticks": args.min_penetration_ticks,
                "sl_buffer_ticks": args.sl_buffer_ticks,
                "tp_r": args.tp_r,
                "max_hold_bars": args.max_hold_bars,
                "force_flat_et": args.force_flat_et,
            },
            "symbols": [
                {
                    "symbol": s.symbol,
                    "n": s.n,
                    "n_long": s.n_long,
                    "n_short": s.n_short,
                    "wr_pct": round(s.wr, 2),
                    "mean_r": round(s.mean_r, 4),
                    "median_r": round(s.median_r, 4),
                    "total_r": round(s.total_r, 3),
                    "total_dollar": round(s.total_dollar, 2),
                    "mean_r_long": round(s.mean_r_long, 4),
                    "mean_r_short": round(s.mean_r_short, 4),
                    "exit_reasons": s.exit_reasons,
                }
                for s in all_summaries
            ],
        }
        print(json.dumps(out, indent=2))
    else:
        print()
        print("prior-day RTH H/L sweep-fade — viability probe")
        print("=" * 60)
        print(
            f"  window={args.entry_window_start}-{args.entry_window_end} ET  "
            f"sl_buffer={args.sl_buffer_ticks}t  tp_r={args.tp_r}  "
            f"max_hold={args.max_hold_bars}bars  flat={args.force_flat_et}ET"
        )
        if since or until:
            print(f"  range: {args.since or '(all)'} → {args.until or '(all)'}")
        print()
        print(_format_table(all_summaries))
        print()
        for s in all_summaries:
            print(
                f"  {s.symbol}: long mean R = {s.mean_r_long:+.3f} (n={s.n_long}) | "
                f"short mean R = {s.mean_r_short:+.3f} (n={s.n_short})"
            )
        print()
        print(f"  VERDICT: {verdict}")
        print()

    if verdict == "PRODUCTIONABLE":
        return 0
    if verdict == "MARGINAL":
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
