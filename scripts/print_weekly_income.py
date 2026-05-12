#!/usr/bin/env python3
"""
Print **weekly realized PnL** from ``core/backtest_executor.py --format=json`` output.

- With ``result.trades`` (``--include-trades``): bucket by **ISO calendar week**
  (Monday week-start, UTC) using each trade's ``exit_time``, sum ``pnl``,
  print a table + per-week win rate.
- Without trades: print **naive average weekly** = ``total_pnl`` / week span
  from ``result.period`` (linear allocation — only for quick scans).

Examples::

  .venv/bin/python scripts/print_weekly_income.py /tmp/body_rev_gate_ab/full_2024_2026/body_rev_MNQ_A.json
  .venv/bin/python scripts/print_weekly_income.py --csv weekly_mnq_a.tsv full.json
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple


def _parse_period_weeks(period: str) -> float:
    """Return approximate ISO weeks between first and last date in 'YYYY-MM-DD to YYYY-MM-DD'."""
    m = re.match(
        r"^\s*(\d{4}-\d{2}-\d{2})\s+to\s+(\d{4}-\d{2}-\d{2})\s*$",
        (period or "").strip(),
        re.I,
    )
    if not m:
        return 0.0
    a = datetime.fromisoformat(m.group(1)).replace(tzinfo=timezone.utc)
    b = datetime.fromisoformat(m.group(2)).replace(tzinfo=timezone.utc)
    days = max(1.0, (b - a).total_seconds() / 86400.0)
    return days / 7.0


def _iso_week_key_exit(tr: Dict[str, Any]) -> str:
    raw = tr.get("exit_time") or tr.get("exitTime") or ""
    if not raw:
        return "unknown"
    s = str(raw).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return "unknown"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    iso = dt.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def weekly_from_trades(trades: List[Dict[str, Any]]) -> List[Tuple[str, float, int, int]]:
    """Rows: (week_key, pnl_sum, n_trades, n_wins)."""
    pnl_by: Dict[str, float] = defaultdict(float)
    n_by: Dict[str, int] = defaultdict(int)
    wins_by: Dict[str, int] = defaultdict(int)
    for tr in trades:
        wk = _iso_week_key_exit(tr)
        pnl = float(tr.get("pnl") or 0.0)
        pnl_by[wk] += pnl
        n_by[wk] += 1
        if pnl > 0:
            wins_by[wk] += 1
    keys = sorted(k for k in pnl_by if k != "unknown")
    unk_pnl = pnl_by.get("unknown", 0.0)
    unk_n = n_by.get("unknown", 0)
    rows = [(k, pnl_by[k], n_by[k], wins_by[k]) for k in keys]
    if unk_n:
        rows.append(("unknown", unk_pnl, unk_n, wins_by.get("unknown", 0)))
    return rows


def print_report(doc: Dict[str, Any], csv_path: Path | None) -> int:
    res = doc.get("result") or {}
    trades = res.get("trades")
    sym = res.get("symbol", "?")
    strat = res.get("strategy", "?")
    period = res.get("period", "")

    if isinstance(trades, list) and trades:
        rows = weekly_from_trades(trades)
        total = sum(r[1] for r in rows)
        print(f"# {sym}  {strat}  |  weekly PnL (exit week, UTC ISO)  |  {period}")
        print(f"# trades={len(trades)}  sum_week_buckets=${total:,.2f}")
        print("iso_week\tpnl_usd\ttrades\twins\twin_pct")
        if csv_path:
            csv_path.parent.mkdir(parents=True, exist_ok=True)
            f = csv_path.open("w", newline="", encoding="utf-8")
            w = csv.writer(f, delimiter="\t")
            w.writerow(["iso_week", "pnl_usd", "trades", "wins", "win_pct"])
        else:
            f = None
            w = None
        for wk, pnl, n, nw in rows:
            wp = (100.0 * nw / n) if n else 0.0
            line = f"{wk}\t{pnl:,.2f}\t{n}\t{nw}\t{wp:.1f}%"
            print(line)
            if w:
                w.writerow([wk, f"{pnl:.2f}", n, nw, f"{wp:.1f}"])
        if f:
            f.close()
        return 0

    # Summary-only fallback
    total_pnl = float(res.get("total_pnl") or 0.0)
    weeks = _parse_period_weeks(str(period))
    avg = total_pnl / weeks if weeks > 0 else 0.0
    print(f"# {sym}  {strat}  |  NO per-trade rows — naive weekly average from period")
    print(f"# period={period!r}  (~{weeks:.1f} calendar weeks)")
    print(f"total_pnl_usd\t{total_pnl:,.2f}")
    print(f"avg_pnl_per_week_usd\t{avg:,.2f}")
    if csv_path:
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            cw = csv.writer(f, delimiter="\t")
            cw.writerow(["metric", "value"])
            cw.writerow(["note", "no trades array; linear avg over period weeks"])
            cw.writerow(["period", str(period)])
            cw.writerow(["approx_weeks", f"{weeks:.4f}"])
            cw.writerow(["total_pnl_usd", f"{total_pnl:.2f}"])
            cw.writerow(["avg_pnl_per_week_usd", f"{avg:.2f}"])
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("json_path", type=Path, help="Backtest JSON file")
    ap.add_argument(
        "--csv",
        type=Path,
        default=None,
        help="Also write the weekly table as TSV (trades path) or two-row summary (no trades)",
    )
    args = ap.parse_args()
    try:
        doc = json.loads(args.json_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"error reading JSON: {e}", file=sys.stderr)
        return 1
    if not isinstance(doc, dict):
        print("error: root must be object", file=sys.stderr)
        return 1
    return print_report(doc, args.csv)


if __name__ == "__main__":
    raise SystemExit(main())
