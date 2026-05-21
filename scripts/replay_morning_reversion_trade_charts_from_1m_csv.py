#!/usr/bin/env python3
"""Replay ``morning_range_reversion`` on a **1m** OHLCV CSV and write per-trade LWC HTML.

Use this for TopStepX ``history`` exports (often ``Time`` column with ``+00:00``) or any OHLCV
file readable by ``HistoricalDataLoader.load_from_csv`` (timestamps are normalized to **naive UTC**
so ``--start`` / ``--end`` filters match canonical Databento-style replay).

Examples::

  ENABLE_SIGNALR=false .venv/bin/python scripts/replay_morning_reversion_trade_charts_from_1m_csv.py \\
    --csv MNQ_1m_20260514_132946.csv \\
    --out-dir docs/perf/morning_reversion_mnq_export_20260514

  # Only the last 10 calendar days in the file + last 8 trades charted
  .venv/bin/python scripts/replay_morning_reversion_trade_charts_from_1m_csv.py \\
    --csv ./MNQ_1m_20260514_132946.csv --recent-days 10 --last-trades 8

  .venv/bin/python scripts/replay_morning_reversion_trade_charts_from_1m_csv.py \\
    --csv data.csv --start 2026-05-10 --end 2026-05-14 --env MORNING_RANGE_REVERSION_SIGNAL_TP_MULT=0.7

  # Regenerate charts from an existing ``backtest.json`` (skip the long executor pass)
  .venv/bin/python scripts/replay_morning_reversion_trade_charts_from_1m_csv.py \\
    --csv MNQ_1m_20260514_132946.csv --out-dir docs/perf/my_replay --reuse-json

Writes ``backtest.json`` (stdout capture) and ``index.html`` + ``charts/*.html`` under ``--out-dir``.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core.backtest.ohlcv import dataframe_to_chart_bars_unix, snap_trade_unix_to_chart_bar_open


def _parse_iso_utc(s: str) -> datetime:
    s = str(s).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _overlay_for_trade(trade: Dict[str, Any], bar_times: List[int]) -> Dict[str, Any]:
    et = _parse_iso_utc(str(trade["entry_time"]))
    xt = _parse_iso_utc(str(trade["exit_time"]))
    eu = int(et.timestamp())
    xu = int(xt.timestamp())
    return {
        "trade_id": trade.get("trade_id", ""),
        "side": str(trade.get("side", "")).upper(),
        "entry_time": snap_trade_unix_to_chart_bar_open(eu, bar_times),
        "exit_time": snap_trade_unix_to_chart_bar_open(xu, bar_times),
        "entry_price": float(trade.get("entry_price", 0)),
        "exit_price": float(trade.get("exit_price", 0)),
        "exit_reason": str(trade.get("exit_reason") or ""),
    }


def _csv_date_bounds(path: Path) -> Tuple[date, date]:
    col = pd.read_csv(path, nrows=0).columns[0]
    ts = pd.read_csv(path, usecols=[col])
    t = pd.to_datetime(ts[col], utc=True, format="mixed")
    return t.min().date(), t.max().date()


def _naive_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", type=str, required=True, help="Path to 1m OHLCV CSV (repo-relative or absolute)")
    ap.add_argument(
        "--out-dir",
        type=Path,
        default=ROOT / "docs" / "perf" / "morning_reversion_1m_csv_replay",
        help="Output directory for index.html, charts/, backtest.json",
    )
    ap.add_argument("--symbol", type=str, default="MNQ")
    ap.add_argument("--recent-days", type=int, default=21, help="Replay window width when --start omitted (calendar days)")
    ap.add_argument(
        "--full-span",
        action="store_true",
        help="Replay from first bar date in CSV through last (can be slow on large exports)",
    )
    ap.add_argument("--start", type=str, default=None, help="YYYY-MM-DD (optional)")
    ap.add_argument("--end", type=str, default=None, help="YYYY-MM-DD inclusive (optional)")
    ap.add_argument("--last-trades", type=int, default=15, help="Chart only the N most recent trades by exit_time")
    ap.add_argument("--padding-minutes", type=int, default=360, help="1m slice padding around entry/exit")
    ap.add_argument(
        "--reuse-json",
        action="store_true",
        help="Skip backtest subprocess; read existing ``backtest.json`` in ``--out-dir`` (must be valid).",
    )
    ap.add_argument(
        "--env",
        action="append",
        default=[],
        metavar="KEY=VAL",
        help="Extra env for backtest subprocess (repeatable)",
    )
    args = ap.parse_args()

    csv_path = Path(args.csv).expanduser()
    if not csv_path.is_absolute():
        csv_path = ROOT / csv_path
    if not csv_path.is_file():
        print(f"error: CSV not found: {csv_path}", file=sys.stderr)
        return 2

    lo, hi = _csv_date_bounds(csv_path)
    if getattr(args, "full_span", False):
        start_d, end_d = lo, hi
    elif args.start:
        start_d = date.fromisoformat(args.start)
        end_d = date.fromisoformat(args.end) if args.end else hi
    else:
        start_d = max(lo, hi - timedelta(days=max(1, int(args.recent_days)) - 1))
        end_d = date.fromisoformat(args.end) if args.end else hi

    if start_d > end_d:
        print("error: start > end", file=sys.stderr)
        return 2

    out_dir = args.out_dir if args.out_dir.is_absolute() else ROOT / args.out_dir
    charts_dir = out_dir / "charts"
    out_dir.mkdir(parents=True, exist_ok=True)
    charts_dir.mkdir(parents=True, exist_ok=True)

    extra_env: Dict[str, str] = {"ENABLE_SIGNALR": "false", "PYTHONUNBUFFERED": "1", "BACKTEST_CSV_CACHE": "0"}
    for raw in args.env or []:
        if "=" not in raw:
            print(f"error: --env must be KEY=VAL, got {raw!r}", file=sys.stderr)
            return 2
        k, v = raw.split("=", 1)
        extra_env[k.strip()] = v.strip()

    json_path = out_dir / "backtest.json"
    if args.reuse_json:
        if not json_path.is_file():
            print(f"error: --reuse-json but missing {json_path}", file=sys.stderr)
            return 2
        line = json_path.read_text(encoding="utf-8").strip().splitlines()[-1]
    else:
        cmd = [
            sys.executable,
            str(ROOT / "core" / "backtest_executor.py"),
            "--strategy=morning_range_reversion",
            f"--symbol={args.symbol}",
            "--timeframe=1m",
            f"--csv={csv_path}",
            f"--start={start_d.isoformat()}",
            f"--end={end_d.isoformat()}",
            "--replay",
            "--format=json",
            "--include-trades",
        ]
        print("Running:", " ".join(cmd), file=sys.stderr)
        proc = subprocess.run(cmd, cwd=str(ROOT), env={**os.environ, **extra_env}, capture_output=True, text=True)
        text = (proc.stdout or "").strip()
        if proc.returncode != 0:
            print(proc.stderr[-6000:] or text[-2000:], file=sys.stderr)
            return 1
        if not text:
            print("error: empty backtest stdout", file=sys.stderr)
            return 1
        line = text.splitlines()[-1]
        json_path.write_text(line + "\n", encoding="utf-8")

    try:
        doc = json.loads(line)
    except json.JSONDecodeError as e:
        print(f"error: bad json: {e}", file=sys.stderr)
        return 1
    if not doc.get("ok", True):
        print(json.dumps(doc, indent=2)[:4000], file=sys.stderr)
        return 1
    res = doc.get("result") or {}
    trades = [t for t in (res.get("trades") or []) if isinstance(t, dict)]
    trades.sort(key=lambda t: _parse_iso_utc(str(t.get("exit_time") or "")))
    n_keep = max(1, int(args.last_trades))
    picked = trades[-n_keep:] if len(trades) > n_keep else trades

    from gui.chart_html import generate_chart_html

    pad = timedelta(minutes=args.padding_minutes)
    df = pd.read_csv(csv_path)
    ts_col = next(c for c in df.columns if str(c).lower() in ("timestamp", "time", "date", "datetime"))
    ts = pd.to_datetime(df[ts_col], utc=True, format="mixed")
    df["timestamp"] = ts.dt.tz_convert("UTC").dt.tz_localize(None)
    df = df.set_index("timestamp").sort_index()

    rows_html: List[str] = []
    for i, trade in enumerate(picked):
        et = _parse_iso_utc(str(trade["entry_time"]))
        xt = _parse_iso_utc(str(trade["exit_time"]))
        lo_ts = _naive_utc(et - pad)
        hi_ts = _naive_utc(xt + pad)
        sub = df.loc[(df.index >= lo_ts) & (df.index <= hi_ts)]
        if sub.empty:
            rows_html.append(
                f"<tr><td>{trade.get('trade_id')}</td><td colspan='6'>empty OHLC slice</td></tr>"
            )
            continue
        bars, bar_times = dataframe_to_chart_bars_unix(sub)
        overlay = _overlay_for_trade(trade, bar_times)
        tid = str(trade.get("trade_id", f"T{i}"))
        slug = f"morning_reversion_{args.symbol}_{tid}_{i}".replace(" ", "_")
        html_path = charts_dir / f"{slug}.html"
        generate_chart_html(
            symbol=args.symbol,
            timeframe="1m",
            bars=bars,
            output_path=str(html_path),
            realtime=False,
            backtest=False,
            trade_overlays=[overlay],
            axis_time_zone="America/New_York",
            morning_range_et_shade=True,
        )
        pnl = float(trade.get("pnl") or 0)
        rows_html.append(
            "<tr>"
            f"<td>{i+1}</td>"
            f"<td>{trade.get('trade_id','')}</td>"
            f"<td>{trade.get('side','')}</td>"
            f"<td>{trade.get('entry_time','')}</td>"
            f"<td>{trade.get('exit_time','')}</td>"
            f"<td>{pnl:.2f}</td>"
            f"<td>{trade.get('exit_reason','')}</td>"
            f'<td><a href="charts/{slug}.html">chart</a></td>'
            "</tr>"
        )

    n_all = len(trades)
    try:
        csv_disp = csv_path.relative_to(ROOT)
    except ValueError:
        csv_disp = csv_path

    index = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <title>morning_range_reversion replay — {args.symbol} ({start_d} → {end_d})</title>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 1.25rem 2rem; max-width: 100rem; color: #111; }}
    table {{ border-collapse: collapse; width: 100%; font-size: 0.85rem; }}
    th, td {{ border: 1px solid #ddd; padding: 0.35rem 0.45rem; text-align: left; }}
    th {{ background: #f6f6f7; }}
    a {{ color: #2563eb; }}
    code {{ background: #f4f4f5; padding: 0.1rem 0.35rem; border-radius: 4px; }}
    .muted {{ color: #555; font-size: 0.9rem; }}
  </style>
</head>
<body>
  <h1>morning_range_reversion — {args.symbol} 1m replay</h1>
  <p class="muted">CSV: <code>{csv_disp}</code> ·
  Replay window: <strong>{start_d}</strong> → <strong>{end_d}</strong> ·
  Completed trades in window: <strong>{n_all}</strong> · Charts: last <strong>{len(picked)}</strong> by exit time.</p>
  <p class="muted">Charts use <code>axis_time_zone=America/New_York</code>. Trade timestamps in the table are JSON (UTC ``Z``).</p>
  <table>
    <thead><tr><th>#</th><th>id</th><th>side</th><th>entry (UTC)</th><th>exit (UTC)</th><th>pnl</th><th>reason</th><th>chart</th></tr></thead>
    <tbody>{''.join(rows_html) if rows_html else '<tr><td colspan="8">No trades in window.</td></tr>'}</tbody>
  </table>
</body>
</html>
"""
    (out_dir / "index.html").write_text(index, encoding="utf-8")
    print(f"wrote {out_dir / 'index.html'}", file=sys.stderr)
    if not args.reuse_json:
        print(f"wrote {json_path.relative_to(ROOT)}", file=sys.stderr)
    else:
        print(f"reused {json_path.relative_to(ROOT)}", file=sys.stderr)
    print(f"charts: {len(picked)} files under {charts_dir.relative_to(ROOT)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
