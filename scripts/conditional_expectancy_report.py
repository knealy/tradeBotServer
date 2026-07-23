#!/usr/bin/env python3
"""Conditional expectancy report — find which trade subsets actually pay.

Reads ``metrics_insights.json`` ``trades_flat`` (or a richer trade list) and
buckets expectancy / WR / PnL by feature, with a simple fold-level t-stat when
``_fold`` is present.

Usage::

    .venv/bin/python scripts/conditional_expectancy_report.py \\
        --insights docs/perf/regime_longspan_450d/metrics_insights.json \\
        --out docs/perf/regime_longspan_450d/conditional_expectancy.json
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _parse_dt(raw: Any) -> Optional[datetime]:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _enrich(trade: Dict[str, Any]) -> Dict[str, Any]:
    t = dict(trade)
    pnl = float(t.get("pnl") or 0.0)
    risk = t.get("initial_risk_dollars")
    try:
        risk_f = float(risk) if risk is not None else None
    except (TypeError, ValueError):
        risk_f = None
    if risk_f and risk_f > 1e-9:
        t["r_multiple"] = pnl / risk_f
    else:
        t["r_multiple"] = None

    et = _parse_dt(t.get("entry_time"))
    if et is not None:
        if et.tzinfo is None:
            et = et.replace(tzinfo=timezone.utc)
        et_ny = et.astimezone(__import__("zoneinfo").ZoneInfo("America/New_York"))
        t["entry_hour_et"] = et_ny.hour
        t["entry_weekday"] = et_ny.weekday()  # Mon=0
        t["entry_month"] = f"{et_ny.year:04d}-{et_ny.month:02d}"
    else:
        t["entry_hour_et"] = None
        t["entry_weekday"] = None
        t["entry_month"] = None

    bars = t.get("bars_held")
    try:
        b = int(bars) if bars is not None else None
    except (TypeError, ValueError):
        b = None
    if b is None:
        t["bars_bucket"] = "unknown"
    elif b <= 3:
        t["bars_bucket"] = "0-3"
    elif b <= 8:
        t["bars_bucket"] = "4-8"
    elif b <= 15:
        t["bars_bucket"] = "9-15"
    else:
        t["bars_bucket"] = "16+"

    return t


def _bucket_stats(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    n = len(rows)
    if n == 0:
        return {"n": 0, "pnl": 0.0, "expectancy": None, "win_rate": None}
    pnls = [float(r.get("pnl") or 0.0) for r in rows]
    wins = sum(1 for p in pnls if p > 0)
    total = sum(pnls)
    return {
        "n": n,
        "pnl": round(total, 2),
        "expectancy": round(total / n, 2),
        "win_rate": round(wins / n, 4),
    }


def _fold_tstat(rows: Sequence[Dict[str, Any]]) -> Optional[float]:
    """t-stat of per-fold mean expectancy vs 0 (Welch-ish, small-n safe)."""
    by_fold: Dict[Any, List[float]] = defaultdict(list)
    for r in rows:
        fold = r.get("_fold")
        if fold is None:
            continue
        by_fold[fold].append(float(r.get("pnl") or 0.0))
    if len(by_fold) < 3:
        return None
    fold_means = [sum(v) / len(v) for v in by_fold.values() if v]
    m = len(fold_means)
    if m < 3:
        return None
    mean = sum(fold_means) / m
    var = sum((x - mean) ** 2 for x in fold_means) / (m - 1)
    if var <= 1e-18:
        return None
    return round(mean / math.sqrt(var / m), 3)


def _group(
    rows: Sequence[Dict[str, Any]], key: str
) -> List[Tuple[str, Dict[str, Any]]]:
    buckets: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in rows:
        raw = r.get(key)
        label = "null" if raw is None else str(raw)
        buckets[label].append(r)
    out: List[Tuple[str, Dict[str, Any]]] = []
    for label, items in sorted(buckets.items(), key=lambda kv: -sum(float(x.get("pnl") or 0) for x in kv[1])):
        st = _bucket_stats(items)
        st["fold_t"] = _fold_tstat(items)
        out.append((label, st))
    return out


def build_report(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    enriched = [_enrich(t) for t in trades]
    report: Dict[str, Any] = {
        "n_trades": len(enriched),
        "overall": _bucket_stats(enriched),
        "by_strategy": {},
        "features": {},
    }
    for strat in sorted({str(t.get("strategy") or "?") for t in enriched}):
        srows = [t for t in enriched if str(t.get("strategy") or "?") == strat]
        report["by_strategy"][strat] = {
            "overall": _bucket_stats(srows),
            "bars_bucket": dict(_group(srows, "bars_bucket")),
            "exit_reason": dict(_group(srows, "exit_reason")),
            "symbol": dict(_group(srows, "symbol")),
            "side": dict(_group(srows, "side")),
            "entry_hour_et": dict(_group(srows, "entry_hour_et")),
            "entry_weekday": dict(_group(srows, "entry_weekday")),
            "entry_month": dict(_group(srows, "entry_month")),
        }
    # Cross-strategy feature tables
    for feat in ("bars_bucket", "exit_reason", "symbol", "strategy", "entry_hour_et"):
        report["features"][feat] = dict(_group(enriched, feat))
    return report


def _print_table(title: str, rows: Dict[str, Dict[str, Any]], *, min_n: int = 5) -> None:
    print(f"\n=== {title} ===")
    print(f"{'bucket':24s} {'n':>5} {'pnl':>10} {'exp':>8} {'WR%':>6} {'fold_t':>7}")
    for label, st in rows.items():
        if int(st.get("n") or 0) < min_n and label != "overall":
            continue
        ft = st.get("fold_t")
        ft_s = f"{ft:.2f}" if isinstance(ft, (int, float)) else "—"
        wr = st.get("win_rate")
        wr_s = f"{100 * wr:.1f}" if isinstance(wr, float) else "—"
        exp = st.get("expectancy")
        exp_s = f"{exp:.1f}" if isinstance(exp, (int, float)) else "—"
        print(
            f"{label:24s} {st.get('n', 0):5d} {st.get('pnl', 0):10.1f} "
            f"{exp_s:>8} {wr_s:>6} {ft_s:>7}"
        )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--insights", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--strategy", default=None, help="Filter to one strategy id")
    ap.add_argument("--min-n", type=int, default=5)
    args = ap.parse_args()

    doc = json.loads(args.insights.read_text(encoding="utf-8"))
    trades = list(doc.get("trades_flat") or [])
    if args.strategy:
        trades = [t for t in trades if t.get("strategy") == args.strategy]
    if not trades:
        print("error: no trades_flat rows", file=sys.stderr)
        return 1

    report = build_report(trades)
    _print_table("OVERALL FEATURES · bars_bucket", report["features"]["bars_bucket"], min_n=args.min_n)
    _print_table("OVERALL FEATURES · exit_reason", report["features"]["exit_reason"], min_n=1)
    for strat, block in report["by_strategy"].items():
        _print_table(f"{strat} · bars_bucket", block["bars_bucket"], min_n=args.min_n)
        _print_table(f"{strat} · exit_reason", block["exit_reason"], min_n=1)

    out = args.out or args.insights.with_name("conditional_expectancy.json")
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
