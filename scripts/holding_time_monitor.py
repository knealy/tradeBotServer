#!/usr/bin/env python3
"""Rolling holding-time monitor for MGC morning_range_reversion edge decay.

Rising average hold on MGC MRR is an early signal that reversion is slowing
(450d study: losers median 12 bars vs winners 6; 16+ bucket is −$17/trade).

Usage::

    .venv/bin/python scripts/holding_time_monitor.py \\
        --insights docs/perf/regime_longspan_450d/metrics_insights.json \\
        --strategy morning_range_reversion --symbol MGC \\
        --window 20 --alert-bars 12
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

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


def monitor(
    trades: List[Dict[str, Any]],
    *,
    strategy: str,
    symbol: str,
    window: int,
    alert_bars: float,
) -> Dict[str, Any]:
    rows = [
        t
        for t in trades
        if str(t.get("strategy") or "") == strategy
        and str(t.get("symbol") or "").upper() == symbol.upper()
        and t.get("bars_held") is not None
    ]
    rows.sort(key=lambda t: str(t.get("exit_time") or t.get("entry_time") or ""))
    series: List[Dict[str, Any]] = []
    alert = False
    for i, t in enumerate(rows):
        start = max(0, i + 1 - window)
        chunk = rows[start : i + 1]
        avg = sum(float(x.get("bars_held") or 0) for x in chunk) / len(chunk)
        point = {
            "i": i,
            "exit_time": t.get("exit_time"),
            "bars_held": t.get("bars_held"),
            "pnl": t.get("pnl"),
            "rolling_avg_bars": round(avg, 2),
            "n_window": len(chunk),
        }
        if len(chunk) >= max(5, window // 2) and avg >= alert_bars:
            point["alert"] = True
            alert = True
        series.append(point)

    last = series[-1] if series else None
    return {
        "strategy": strategy,
        "symbol": symbol,
        "window": window,
        "alert_bars": alert_bars,
        "n_trades": len(rows),
        "alert": alert,
        "last": last,
        "series": series,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--insights", type=Path, required=True)
    ap.add_argument("--strategy", default="morning_range_reversion")
    ap.add_argument("--symbol", default="MGC")
    ap.add_argument("--window", type=int, default=20)
    ap.add_argument(
        "--alert-bars",
        type=float,
        default=12.0,
        help="Flag when rolling avg bars_held >= this (default 12 = loser median)",
    )
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    doc = json.loads(args.insights.read_text(encoding="utf-8"))
    trades = list(doc.get("trades_flat") or [])
    result = monitor(
        trades,
        strategy=args.strategy,
        symbol=args.symbol,
        window=args.window,
        alert_bars=args.alert_bars,
    )
    last = result.get("last") or {}
    print(
        f"{args.strategy}/{args.symbol}: n={result['n_trades']} "
        f"last_rolling_avg_bars={last.get('rolling_avg_bars')} "
        f"alert={result['alert']}"
    )
    if result["alert"]:
        # Show last few alert points
        alerts = [p for p in result["series"] if p.get("alert")][-5:]
        for p in alerts:
            print(
                f"  ALERT {p.get('exit_time')} rolling_avg={p['rolling_avg_bars']} "
                f"(trade bars={p['bars_held']} pnl={p.get('pnl')})"
            )

    out = args.out or args.insights.with_name(
        f"holding_time_monitor_{args.symbol}_{args.strategy}.json"
    )
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"wrote {out}")
    return 0 if not result["alert"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
