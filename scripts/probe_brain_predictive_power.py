#!/usr/bin/env python3
"""Validate the PA/SMC synthesis engine's bias signal predictive power.

The brain (``core.market_synthesizer``) emits a ``MarketContextSnapshot`` on
every bar close.  Its headline output is a ``bias`` label (bullish / bearish /
neutral) plus a ``confidence`` in [0, 1].  Both are derived from hand-coded
scoring weights (BoS=0.40, CHoCH=0.35, swing-trend=0.20, sweep=0.25).

This probe answers ONE question before we integrate the brain into any
production strategy: **does the bias signal actually predict future returns?**

Methodology:
1. Walk historical 5m bars chronologically.  At each bar ``i`` (after a
   warmup of ``--warmup-bars``, default 50), build a snapshot from the
   prior ``--window`` bars [i-window, i].  Look-ahead-safe by construction.
2. Compute forward returns at horizons 1 / 3 / 6 bars (close-to-close).
3. Group the (snapshot, forward_returns) pairs by bias label AND confidence
   bucket.  Report per-group:
     * count
     * mean forward return per bar (in points)
     * sign-agreement WR (bullish → return > 0; bearish → return < 0)
4. Verdict:
     SIGNAL_FOUND      — sign-agreement WR ≥ 55 % on AT LEAST 2 of 3 symbols
                         at the most-confident bucket (confidence ≥ 0.50), and
                         on AT LEAST one horizon.
     WEAK_SIGNAL       — WR ∈ [50, 55 %] on the most-confident bucket.
     NO_SIGNAL         — otherwise.

A SIGNAL_FOUND verdict is the green light to integrate the brain as
confluence.  A NO_SIGNAL verdict means the scoring weights need rework
before any strategy consumes the brain.

Usage::

    .venv/bin/python scripts/probe_brain_predictive_power.py \\
        --since 2025-09-01 --until 2026-06-12

    # JSON output for downstream tooling
    .venv/bin/python scripts/probe_brain_predictive_power.py --json
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.market_synthesizer import build_snapshot  # type: ignore
from scripts.simulate_price_action_trades import CandleBar, _read_csv  # type: ignore


# ──────────────────── per-symbol conventions ────────────────────


SYMBOL_SPECS: Dict[str, Dict[str, Any]] = {
    "MGC": {"csv_5m": "historical_data/price/MGC_5m_databento.csv"},
    "MNQ": {"csv_5m": "historical_data/price/MNQ_5m_databento.csv"},
    "MES": {"csv_5m": "historical_data/price/MES_5m_databento.csv"},
}


# ────────────────── confidence bucket assignment ────────────────


_CONF_BUCKETS = [
    (0.0, 0.20, "neutral_low"),
    (0.20, 0.40, "weak"),
    (0.40, 0.60, "moderate"),
    (0.60, 1.01, "strong"),
]


def _bucket_for(conf: float) -> str:
    for lo, hi, name in _CONF_BUCKETS:
        if lo <= conf < hi:
            return name
    return "strong"


# ─────────────────────── aggregation types ──────────────────────


@dataclass
class BiasGroupStats:
    label: str        # "bullish_strong" / "bearish_weak" / "neutral_low" / ...
    count: int = 0
    fwd_sum_1: float = 0.0
    fwd_sum_3: float = 0.0
    fwd_sum_6: float = 0.0
    wins_1: int = 0   # sign-agreement: bias_dir × forward_return > 0
    wins_3: int = 0
    wins_6: int = 0

    @property
    def mean_fwd_1(self) -> float:
        return self.fwd_sum_1 / self.count if self.count else 0.0

    @property
    def mean_fwd_3(self) -> float:
        return self.fwd_sum_3 / self.count if self.count else 0.0

    @property
    def mean_fwd_6(self) -> float:
        return self.fwd_sum_6 / self.count if self.count else 0.0

    @property
    def wr_1(self) -> float:
        return 100.0 * self.wins_1 / self.count if self.count else 0.0

    @property
    def wr_3(self) -> float:
        return 100.0 * self.wins_3 / self.count if self.count else 0.0

    @property
    def wr_6(self) -> float:
        return 100.0 * self.wins_6 / self.count if self.count else 0.0


@dataclass
class SymbolResult:
    symbol: str
    snapshots: int = 0
    groups: Dict[str, BiasGroupStats] = field(default_factory=dict)


def _bias_dir(bias: str) -> int:
    if bias == "bullish":
        return +1
    if bias == "bearish":
        return -1
    return 0


# ─────────────────────────── core walk ──────────────────────────


# Group-by modes — selects which snapshot field(s) define the bucket.
GROUP_BY_BIAS_BUCKET = "bias_bucket"     # default: bias × confidence bucket
GROUP_BY_STRUCTURE = "structure"         # snap.structure_event alone (bos_up / bos_down / choch_up / choch_down / none)
GROUP_BY_SWEEP = "sweep"                 # snap.recent_sweep_direction alone (sweep_low / sweep_high / no_sweep)


def _group_key_for(snap: Any, *, group_by: str) -> Tuple[str, int]:
    """Return (group_label, expected_sign).

    expected_sign is +1 / -1 / 0 — the direction in which a predictive
    primitive WOULD point.  walk_symbol uses this to compute sign-agreement
    WR against the realised forward return.  expected_sign == 0 means
    "no directional thesis" (e.g. the structure_event=='none' bucket) and
    win counts stay at 0 for that group.
    """
    if group_by == GROUP_BY_STRUCTURE:
        ev = snap.structure_event
        if ev == "bos_up":
            return "bos_up", +1
        if ev == "bos_down":
            return "bos_down", -1
        if ev == "choch_up":
            return "choch_up", +1
        if ev == "choch_down":
            return "choch_down", -1
        return "none", 0
    if group_by == GROUP_BY_SWEEP:
        d = snap.recent_sweep_direction
        if d == +1:
            return "sweep_low_fade", +1  # liquidity sweep below swing low → long bias
        if d == -1:
            return "sweep_high_fade", -1  # liquidity sweep above swing high → short bias
        return "no_sweep", 0
    # Default: bias × confidence bucket (the brain's composite scoring)
    bucket = _bucket_for(snap.confidence)
    return f"{snap.bias}_{bucket}", _bias_dir(snap.bias)


def walk_symbol(
    symbol: str,
    bars: Sequence[CandleBar],
    *,
    window: int,
    warmup_bars: int,
    swing_lookback: int,
    group_by: str = GROUP_BY_BIAS_BUCKET,
) -> SymbolResult:
    """Walk bars chronologically, build snapshots, accumulate forward-return stats.

    Snapshot at bar ``i`` is built from bars [max(0, i-window+1) ... i].
    Forward returns are bars[i+k].close - bars[i].close (in points) for
    k ∈ {1, 3, 6}.  Snapshots at the tail of the series with insufficient
    forward bars are skipped.

    ``group_by`` selects the grouping dimension:
      * ``"bias_bucket"`` — composite brain bias × confidence (default)
      * ``"structure"``   — snap.structure_event only
      * ``"sweep"``       — snap.recent_sweep_direction only
    """
    result = SymbolResult(symbol=symbol)
    n = len(bars)
    if n < warmup_bars + 6:
        return result

    for i in range(warmup_bars, n - 6):
        win_start = max(0, i - window + 1)
        win = bars[win_start : i + 1]
        snap = build_snapshot(
            symbol=symbol,
            timeframe="5m",
            bars=win,
            swing_lookback=swing_lookback,
        )
        result.snapshots += 1
        cur_close = bars[i].close
        fwd_1 = bars[i + 1].close - cur_close
        fwd_3 = bars[i + 3].close - cur_close
        fwd_6 = bars[i + 6].close - cur_close

        group_label, expected_sign = _group_key_for(snap, group_by=group_by)
        grp = result.groups.setdefault(group_label, BiasGroupStats(label=group_label))
        grp.count += 1
        grp.fwd_sum_1 += fwd_1
        grp.fwd_sum_3 += fwd_3
        grp.fwd_sum_6 += fwd_6
        if expected_sign != 0:
            if expected_sign * fwd_1 > 0:
                grp.wins_1 += 1
            if expected_sign * fwd_3 > 0:
                grp.wins_3 += 1
            if expected_sign * fwd_6 > 0:
                grp.wins_6 += 1

    return result


# ─────────────────────────── reporting ──────────────────────────


def _format_table(symbol: str, result: SymbolResult) -> str:
    rows = [f"\n{symbol} — {result.snapshots} snapshots"]
    header = (
        f"  {'group':<22} {'n':>5} "
        f"{'mean1':>7} {'mean3':>7} {'mean6':>7} "
        f"{'WR1':>6} {'WR3':>6} {'WR6':>6}"
    )
    rows.append(header)
    rows.append("  " + "-" * (len(header) - 2))
    # Sort: bullish_strong → bullish_moderate → ... → bearish_strong → neutral_*
    def _sort_key(label: str) -> Tuple[int, int]:
        bias_order = {"bullish": 0, "bearish": 1, "neutral": 2}
        conf_order = {"strong": 0, "moderate": 1, "weak": 2, "neutral_low": 3}
        bias, conf = label.rsplit("_", 1) if "_" in label else (label, "neutral_low")
        return (bias_order.get(bias, 99), conf_order.get(conf, 99))

    for key in sorted(result.groups.keys(), key=_sort_key):
        g = result.groups[key]
        if g.count == 0:
            continue
        rows.append(
            f"  {key:<22} {g.count:>5} "
            f"{g.mean_fwd_1:>7.3f} {g.mean_fwd_3:>7.3f} {g.mean_fwd_6:>7.3f} "
            f"{g.wr_1:>5.1f}% {g.wr_3:>5.1f}% {g.wr_6:>5.1f}%"
        )
    return "\n".join(rows)


def _verdict_per_symbol(
    result: SymbolResult, *, min_n: int = 30, group_by: str = GROUP_BY_BIAS_BUCKET
) -> str:
    """Return per-symbol verdict: SIGNAL / WEAK / NONE / INSUFFICIENT_N.

    For each grouping mode, picks the most-directional bucket combinations
    (or the union of all directional buckets) and computes combined
    sign-agreement WR across the three horizons.

    Threshold: ≥ ``min_n`` samples in the combined pool to avoid
    noise-driven false positives.
    """
    if group_by == GROUP_BY_STRUCTURE:
        # Combine BoS_UP + BoS_DOWN + CHoCH_UP + CHoCH_DOWN groups.
        directional_keys = ("bos_up", "bos_down", "choch_up", "choch_down")
    elif group_by == GROUP_BY_SWEEP:
        directional_keys = ("sweep_low_fade", "sweep_high_fade")
    else:
        directional_keys = ("bullish_strong", "bearish_strong")

    pool = [
        result.groups[k]
        for k in directional_keys
        if k in result.groups and result.groups[k].count > 0
    ]
    combined_n = sum(g.count for g in pool)
    if combined_n < min_n:
        return "INSUFFICIENT_N"

    def _combined_wr(w_attr: str) -> float:
        wins = sum(getattr(g, w_attr) for g in pool)
        return 100.0 * wins / combined_n if combined_n else 0.0

    best_wr = max(_combined_wr("wins_1"), _combined_wr("wins_3"), _combined_wr("wins_6"))
    if best_wr >= 55.0:
        return "SIGNAL"
    if best_wr >= 50.0:
        return "WEAK_SIGNAL"
    return "NO_SIGNAL"


def _overall_verdict(per_symbol: Dict[str, str]) -> str:
    signal_count = sum(1 for v in per_symbol.values() if v == "SIGNAL")
    weak_count = sum(1 for v in per_symbol.values() if v == "WEAK_SIGNAL")
    if signal_count >= 2:
        return "SIGNAL_FOUND"
    if signal_count >= 1 or weak_count >= 2:
        return "WEAK_SIGNAL"
    return "NO_SIGNAL"


# ────────────────────────────── CLI ─────────────────────────────


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--since", type=str, default=None)
    p.add_argument("--until", type=str, default=None)
    p.add_argument("--symbols", type=str, default="MGC,MNQ,MES")
    p.add_argument("--window", type=int, default=200, help="Rolling window for snapshot pipeline")
    p.add_argument("--warmup-bars", type=int, default=50, help="Bars to skip before first snapshot")
    p.add_argument("--swing-lookback", type=int, default=3)
    p.add_argument(
        "--group-by",
        choices=[GROUP_BY_BIAS_BUCKET, GROUP_BY_STRUCTURE, GROUP_BY_SWEEP],
        default=GROUP_BY_BIAS_BUCKET,
        help=(
            "Grouping mode: 'bias_bucket' tests the composite brain bias, "
            "'structure' isolates BoS/CHoCH events, 'sweep' isolates "
            "liquidity-sweep direction.  Use the latter two to validate "
            "individual primitives in isolation."
        ),
    )
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

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
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]

    results: Dict[str, SymbolResult] = {}
    per_symbol_verdict: Dict[str, str] = {}

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
        if len(bars) < args.warmup_bars + 10:
            print(f"⚠️  {sym}: only {len(bars)} bars in window, skipping", file=sys.stderr)
            continue
        results[sym] = walk_symbol(
            sym,
            bars,
            window=args.window,
            warmup_bars=args.warmup_bars,
            swing_lookback=args.swing_lookback,
            group_by=args.group_by,
        )
        per_symbol_verdict[sym] = _verdict_per_symbol(
            results[sym], group_by=args.group_by
        )

    overall = _overall_verdict(per_symbol_verdict)

    if args.json:
        out: Dict[str, Any] = {
            "verdict": overall,
            "per_symbol_verdict": per_symbol_verdict,
            "params": {
                "since": args.since,
                "until": args.until,
                "window": args.window,
                "warmup_bars": args.warmup_bars,
                "swing_lookback": args.swing_lookback,
                "group_by": args.group_by,
            },
            "symbols": [],
        }
        for sym in symbols:
            if sym not in results:
                continue
            r = results[sym]
            sym_out = {
                "symbol": sym,
                "snapshots": r.snapshots,
                "verdict": per_symbol_verdict.get(sym, "MISSING"),
                "groups": [],
            }
            for key in sorted(r.groups.keys()):
                g = r.groups[key]
                sym_out["groups"].append({
                    "group": key,
                    "n": g.count,
                    "mean_fwd_1": round(g.mean_fwd_1, 4),
                    "mean_fwd_3": round(g.mean_fwd_3, 4),
                    "mean_fwd_6": round(g.mean_fwd_6, 4),
                    "wr_1": round(g.wr_1, 2),
                    "wr_3": round(g.wr_3, 2),
                    "wr_6": round(g.wr_6, 2),
                })
            out["symbols"].append(sym_out)
        print(json.dumps(out, indent=2))
    else:
        print()
        print("PA/SMC synthesis engine — bias signal predictive-power probe")
        print("=" * 64)
        print(f"  range: {args.since or '(all)'} → {args.until or '(all)'}")
        print(
            f"  window={args.window} warmup={args.warmup_bars} "
            f"swing_lb={args.swing_lookback} group_by={args.group_by}"
        )
        for sym in symbols:
            if sym in results:
                print(_format_table(sym, results[sym]))
                print(f"  → {sym} verdict: {per_symbol_verdict.get(sym, 'MISSING')}")
        print()
        print(f"  OVERALL VERDICT: {overall}")
        print()

    if overall == "SIGNAL_FOUND":
        return 0
    if overall == "WEAK_SIGNAL":
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
