#!/usr/bin/env python3
"""Build replay KPI baselines for ``core/regime_kpi_gate`` from walk-forward trades."""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.regime_performance_report import load_trades_from_insights

_ET = ZoneInfo("America/New_York")


def _parse_iso(s: str) -> datetime | None:
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _percentile(values: List[float], q: float) -> float:
    if not values:
        return 0.0
    xs = sorted(values)
    if len(xs) == 1:
        return float(xs[0])
    idx = q * (len(xs) - 1)
    lo = int(idx)
    hi = min(lo + 1, len(xs) - 1)
    frac = idx - lo
    return float(xs[lo] * (1 - frac) + xs[hi] * frac)


def _session_stats(
    trades: List[Dict[str, Any]],
    *,
    strategy: str | None = None,
    symbol: str | None = None,
) -> Tuple[List[float], List[float]]:
    """Return (session_pnls, per_trade_pnls)."""
    by_day: Dict[str, Dict[str, float]] = defaultdict(lambda: {"pnl": 0.0, "n": 0.0})
    per_trade: List[float] = []
    for t in trades:
        if strategy and str(t.get("strategy") or "") != strategy:
            continue
        sym = str(t.get("symbol") or "").upper()
        if symbol and sym != symbol.upper():
            continue
        et = _parse_iso(str(t.get("exit_time") or t.get("entry_time") or ""))
        if not et:
            continue
        pnl = float(t.get("pnl") or 0)
        day = et.astimezone(_ET).date().isoformat()
        by_day[day]["pnl"] += pnl
        by_day[day]["n"] += 1
        per_trade.append(pnl)
    session_pnls = [v["pnl"] for v in by_day.values() if v["n"] > 0]
    return session_pnls, per_trade


def build_baselines(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    strategies = sorted({str(t.get("strategy") or "") for t in trades if t.get("strategy")})
    out: Dict[str, Any] = {"meta": {"source": "walkforward_trades", "n_trades": len(trades)}}

    for strat in strategies:
        sess, per = _session_stats(trades, strategy=strat)
        if not sess:
            continue
        out[strat] = {
            "session_pnl": {
                "p10": round(_percentile(sess, 0.10), 2),
                "p25": round(_percentile(sess, 0.25), 2),
                "p50": round(_percentile(sess, 0.50), 2),
                "n_sessions": len(sess),
            },
            "expectancy": {
                "p10": round(_percentile(per, 0.10), 2),
                "p25": round(_percentile(per, 0.25), 2),
                "p50": round(_percentile(per, 0.50), 2),
                "n_trades": len(per),
            },
        }
        symbols = sorted(
            {str(t.get("symbol") or "").upper() for t in trades if t.get("strategy") == strat}
        )
        for sym in symbols:
            if not sym:
                continue
            s_sess, s_per = _session_stats(trades, strategy=strat, symbol=sym)
            if not s_sess:
                continue
            out[f"{strat}:{sym}"] = {
                "session_pnl": {
                    "p10": round(_percentile(s_sess, 0.10), 2),
                    "p25": round(_percentile(s_sess, 0.25), 2),
                    "p50": round(_percentile(s_sess, 0.50), 2),
                    "n_sessions": len(s_sess),
                },
                "expectancy": {
                    "p10": round(_percentile(s_per, 0.10), 2),
                    "p25": round(_percentile(s_per, 0.25), 2),
                    "p50": round(_percentile(s_per, 0.50), 2),
                    "n_trades": len(s_per),
                },
            }
    return out


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Generate regime KPI replay baselines")
    ap.add_argument(
        "--insights",
        type=Path,
        default=ROOT / "docs/perf/regime_longspan_450d/metrics_insights.json",
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=ROOT / "config/regime_kpi_baselines.json",
    )
    args = ap.parse_args(argv)
    trades = load_trades_from_insights(args.insights)
    if not trades:
        print(f"no trades in {args.insights}", file=sys.stderr)
        return 1
    doc = build_baselines(trades)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {args.out} ({len(trades)} trades, {len(doc) - 1} baseline keys)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
