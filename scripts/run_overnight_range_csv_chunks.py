#!/usr/bin/env python3
"""
Run overnight_range strategy replay on a large OHLCV CSV in calendar chunks
(same idea as chunked history export: e.g. 30-day windows).

Writes one JSON file with per-chunk metrics from ``--format=json`` backtests.
By default requests ``--include-trades`` and prints each completed round-trip
(entry / exit timestamps in ISO UTC, prices, PnL, exit_reason).

Example:
  ./scripts/run_overnight_range_csv_chunks.py \\
    --csv historical_data/price/MNQ_1m_complete.csv --chunk-days 30
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _csv_date_bounds(csv_path: Path) -> tuple[date, date]:
    import pandas as pd

    df = pd.read_csv(csv_path, usecols=["timestamp"])
    ts = pd.to_datetime(df["timestamp"], utc=True, format="mixed")
    return ts.min().date(), ts.max().date()


def _parse_json_stdout(text: str) -> dict:
    for line in reversed(text.strip().splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            return json.loads(line)
    raise ValueError("no JSON object found in subprocess output")


def _print_trade_table(
    chunk_label: str,
    trades: list[dict],
    *,
    show_chunk_col: bool = False,
) -> None:
    if not trades:
        print(f"  ({chunk_label}: no completed round-trip trades)", flush=True)
        return
    print(f"\n  === Trades {chunk_label} ===", flush=True)
    chunk_hdr = f"{'chunk':<24} " if show_chunk_col else ""
    print(
        f"  {chunk_hdr}{'trade_id':<10} {'side':<6} {'entry_time':<32} {'exit_time':<32} "
        f"{'entry':>10} {'exit':>10} {'pnl':>14} {'reason':<12}",
        flush=True,
    )
    dash = f"{'-'*24} " if show_chunk_col else ""
    print(
        f"  {dash}{'-'*10} {'-'*6} {'-'*32} {'-'*32} {'-'*10} {'-'*10} {'-'*14} {'-'*12}",
        flush=True,
    )
    for t in trades:
        ch = f"{str(t.get('chunk', '')):<24} " if show_chunk_col else ""
        print(
            f"  {ch}{t.get('trade_id', ''):<10} {str(t.get('side', '')):<6} "
            f"{str(t.get('entry_time', '')):<32} {str(t.get('exit_time', '')):<32} "
            f"{t.get('entry_price', 0):>10.2f} {t.get('exit_price', 0):>10.2f} "
            f"{t.get('pnl', 0):>14.2f} {str(t.get('exit_reason', '')):<12}",
            flush=True,
        )


def _chunk_ranges(
    first: date, last: date, chunk_days: int
) -> list[tuple[date, date]]:
    if chunk_days < 1:
        raise ValueError("chunk_days must be >= 1")
    out: list[tuple[date, date]] = []
    cur = first
    while cur <= last:
        end = min(cur + timedelta(days=chunk_days - 1), last)
        out.append((cur, end))
        cur = end + timedelta(days=1)
    return out


def main() -> int:
    root = _repo_root()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--csv",
        default="historical_data/price/MNQ_1m_complete.csv",
        help="Path to OHLCV CSV (repo-relative or absolute)",
    )
    ap.add_argument(
        "--chunk-days",
        type=int,
        default=30,
        help="Inclusive calendar days per chunk (default 30)",
    )
    ap.add_argument("--symbol", default="MNQ")
    ap.add_argument("--timeframe", default="1m")
    ap.add_argument("--capital", type=float, default=50_000.0)
    ap.add_argument(
        "--out",
        default="docs/perf/overnight_range_MNQ_1m_chunks.json",
        help="Output JSON path (repo-relative or absolute)",
    )
    ap.add_argument(
        "--omit-trades",
        action="store_true",
        help="Do not pass --include-trades (smaller JSON, no per-trade listing)",
    )
    ap.add_argument(
        "--no-print-trades",
        action="store_true",
        help="Still store trades in JSON when included, but do not print tables to stdout",
    )
    args = ap.parse_args()

    csv_path = Path(args.csv)
    if not csv_path.is_absolute():
        csv_path = root / csv_path
    if not csv_path.is_file():
        print(f"CSV not found: {csv_path}", file=sys.stderr)
        return 1

    out_path = Path(args.out)
    if not out_path.is_absolute():
        out_path = root / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)

    py = root / ".venv" / "bin" / "python"
    exe = str(py) if py.is_file() else sys.executable

    first_d, last_d = _csv_date_bounds(csv_path)
    ranges = _chunk_ranges(first_d, last_d, args.chunk_days)
    rows: list[dict] = []

    for a, b in ranges:
        cmd = [
            exe,
            str(root / "core" / "backtest_executor.py"),
            "--strategy=overnight_range",
            f"--symbol={args.symbol}",
            f"--timeframe={args.timeframe}",
            f"--csv={csv_path}",
            f"--start={a.isoformat()}",
            f"--end={b.isoformat()}",
            f"--capital={args.capital}",
            "--format=json",
        ]
        if not args.omit_trades:
            cmd.append("--include-trades")
        proc = subprocess.run(
            cmd,
            cwd=str(root),
            capture_output=True,
            text=True,
        )
        text = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
        try:
            doc = _parse_json_stdout(proc.stdout or "")
        except Exception as e:
            rows.append(
                {
                    "start": a.isoformat(),
                    "end": b.isoformat(),
                    "ok": False,
                    "error": f"parse_error: {e}; exit={proc.returncode}",
                    "raw_tail": (proc.stdout or "")[-2000:],
                }
            )
            print(f"{a}..{b} FAIL {e}", file=sys.stderr, flush=True)
            continue

        row: dict = {"start": a.isoformat(), "end": b.isoformat(), "ok": doc.get("ok")}
        if doc.get("ok") and doc.get("result"):
            r = doc["result"]
            row.update(
                {
                    "total_trades": r.get("total_trades"),
                    "total_pnl": r.get("total_pnl"),
                    "total_return_pct": r.get("total_return_pct"),
                    "initial_capital": r.get("initial_capital"),
                    "final_capital": r.get("final_capital"),
                    "win_rate": r.get("win_rate"),
                    "max_drawdown": r.get("max_drawdown"),
                    "max_drawdown_pct": r.get("max_drawdown_pct"),
                    "sharpe_ratio": r.get("sharpe_ratio"),
                    "profit_factor": r.get("profit_factor"),
                }
            )
            trades_list = r.get("trades")
            if isinstance(trades_list, list):
                row["trades"] = trades_list
        else:
            row["error"] = doc.get("error", proc.stderr or "unknown")

        rows.append(row)
        tr = row.get("total_trades")
        pnl = row.get("total_pnl")
        print(f"{a} .. {b}  trades={tr}  pnl={pnl}", flush=True)
        if not args.omit_trades and not args.no_print_trades:
            _print_trade_table(f"{a} .. {b}", row.get("trades") or [])

        if proc.returncode != 0 and doc.get("ok"):
            print(proc.stderr[-1500:], file=sys.stderr)

    all_trades: list[dict] = []
    for row in rows:
        chunk_span = f"{row.get('start')}..{row.get('end')}"
        for t in row.get("trades") or []:
            if isinstance(t, dict):
                merged = dict(t)
                merged["chunk"] = chunk_span
                all_trades.append(merged)

    payload = {
        "csv": str(csv_path),
        "chunk_days": args.chunk_days,
        "symbol": args.symbol,
        "timeframe": args.timeframe,
        "capital": args.capital,
        "csv_first_date": first_d.isoformat(),
        "csv_last_date": last_d.isoformat(),
        "chunks": rows,
        "all_trades": all_trades,
    }
    # Whole-period rollups (same starting capital each chunk — diagnostic only).
    ok_rows = [r for r in rows if r.get("ok") and "total_pnl" in r]
    payload["rollup"] = {
        "note": "Each chunk restarts at initial_capital; sum_total_pnl is not compounded equity.",
        "chunks_ok": len(ok_rows),
        "chunks_total": len(rows),
        "sum_total_trades": sum(int(r["total_trades"] or 0) for r in ok_rows),
        "sum_total_pnl": sum(float(r["total_pnl"] or 0) for r in ok_rows),
    }
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Wrote {out_path}", file=sys.stderr)
    if not args.omit_trades and not args.no_print_trades and all_trades:
        print(f"\n  *** All trades ({len(all_trades)} round-trips) ***", flush=True)
        _print_trade_table("full run", all_trades, show_chunk_col=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
