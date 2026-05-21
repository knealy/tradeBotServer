#!/usr/bin/env python3
"""Build an index + per-trade Lightweight Charts for overnight + morning replay legs.

Uses ``core/backtest_executor.py --replay`` (same stack as ``scripts/bongo_portfolio_walkforward_equity.py``)
and ``gui.chart_html.generate_chart_html`` (see ``docs/MAP.md`` → ``gui/chart_html.py``).

If ``--start`` / ``--end`` have **no overlap** with the loaded 5m CSV calendar (e.g. canonical
``MNQ_5m_databento.csv`` ends before mid-May 2026), the script **falls back** to the last
``--fallback-calendar-days`` calendar days present in the CSV and writes a red banner in the HTML.

Example::

  ENABLE_SIGNALR=false .venv/bin/python scripts/generate_signal_walkthrough_report.py \\
    --start 2026-05-06 --end 2026-05-13 \\
    --out-dir docs/perf/signal_walkthrough_overnight_morning
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

# Same replay env as ``bongo_portfolio_walkforward_equity._portfolio_env`` for morning + overnight.
_REPLAY_ENV: Dict[str, str] = {
    "ENABLE_SIGNALR": "false",
    "PYTHONUNBUFFERED": "1",
    "MORNING_RANGE_REVERSION_SIGNAL_REQUIRE_HIGH_ATR": "true",
    "MORNING_RANGE_REVERSION_SIGNAL_TP_MULT": "0.7",
    "MORNING_RANGE_REVERSION_SIGNAL_PARTIAL_TP_ENABLED": "false",
    "MORNING_RANGE_REVERSION_RISK_POSITION_SIZE": "1",
    "OVERNIGHT_RANGE_SIGNAL_PARTIAL_TP_ENABLED": "true",
    "OVERNIGHT_RANGE_RISK_POSITION_SIZE": "2",
}

_LEGS: Tuple[Tuple[str, str], ...] = (
    ("overnight_range", "MNQ"),
    ("overnight_range", "MES"),
    ("overnight_range", "MGC"),
    ("morning_range_reversion", "MNQ"),
    ("morning_range_reversion", "MGC"),
)


def _csv_path_5m(symbol: str) -> Path:
    return ROOT / "historical_data" / "price" / f"{symbol.lower()}_5m_databento.csv"


def _csv_path_1m(symbol: str) -> Path:
    return ROOT / "historical_data" / "price" / f"{symbol.lower()}_1m_databento.csv"


def _read_csv_date_bounds_5m(sym: str) -> Tuple[Optional[date], Optional[date]]:
    p = _csv_path_5m(sym)
    if not p.is_file():
        return None, None
    col = pd.read_csv(p, nrows=0).columns[0]
    ts = pd.to_datetime(pd.read_csv(p, usecols=[col])[col], utc=True, format="mixed")
    return ts.min().date(), ts.max().date()


def _parse_iso_utc(s: str) -> datetime:
    s = str(s).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _run_backtest_json(strategy: str, symbol: str, start: date, end: date) -> Dict[str, Any]:
    cmd = [
        sys.executable,
        str(ROOT / "core" / "backtest_executor.py"),
        f"--strategy={strategy}",
        f"--symbol={symbol}",
        "--timeframe=5m",
        f"--csv={_csv_path_5m(symbol)}",
        f"--start={start.isoformat()}",
        f"--end={end.isoformat()}",
        "--replay",
        "--format=json",
        "--include-trades",
    ]
    env = os.environ.copy()
    env.update(_REPLAY_ENV)
    proc = subprocess.run(
        cmd,
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    text = (proc.stdout or "").strip()
    if proc.returncode != 0:
        return {"ok": False, "error": proc.stderr[-4000:] or text[-2000:], "strategy": strategy, "symbol": symbol}
    if not text:
        return {"ok": False, "error": "empty stdout", "strategy": strategy, "symbol": symbol}
    try:
        line = text.splitlines()[-1]
        out = json.loads(line)
        if not isinstance(out, dict):
            return {"ok": False, "error": "json root not object", "strategy": strategy, "symbol": symbol}
        return out
    except json.JSONDecodeError as e:
        return {
            "ok": False,
            "error": str(e),
            "strategy": strategy,
            "symbol": symbol,
            "tail": text[-1500:],
        }


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


def _trade_overlaps_window(
    trade: Dict[str, Any], win_lo: datetime, win_hi: datetime
) -> bool:
    et = _parse_iso_utc(str(trade.get("entry_time") or ""))
    xt = _parse_iso_utc(str(trade.get("exit_time") or ""))
    if et > xt:
        et, xt = xt, et
    return et <= win_hi and xt >= win_lo


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", type=str, default="2026-05-06", help="Requested window start (YYYY-MM-DD, ET day tag)")
    ap.add_argument("--end", type=str, default="2026-05-13", help="Requested window end (YYYY-MM-DD, inclusive)")
    ap.add_argument(
        "--out-dir",
        type=Path,
        default=ROOT / "docs" / "perf" / "signal_walkthrough_overnight_morning",
    )
    ap.add_argument("--padding-minutes", type=int, default=360, help="1m chart padding around entry/exit")
    ap.add_argument(
        "--fallback-calendar-days",
        type=int,
        default=14,
        help="When requested dates are past CSV data, use this many calendar days ending at CSV max",
    )
    args = ap.parse_args()

    req_start = date.fromisoformat(args.start)
    req_end = date.fromisoformat(args.end)

    mnq_lo, mnq_hi = _read_csv_date_bounds_5m("MNQ")
    if mnq_hi is None:
        print("error: missing MNQ_5m_databento.csv", file=sys.stderr)
        return 2

    win_lo = datetime(req_start.year, req_start.month, req_start.day, tzinfo=timezone.utc)
    win_hi = datetime(req_end.year, req_end.month, req_end.day, 23, 59, 59, tzinfo=timezone.utc)

    used_fallback = False
    eff_start, eff_end = req_start, req_end
    if mnq_hi < req_start:
        eff_end = mnq_hi
        eff_start = eff_end - timedelta(days=max(7, args.fallback_calendar_days))
        if mnq_lo and eff_start < mnq_lo:
            eff_start = mnq_lo
        used_fallback = True
        win_lo = datetime(eff_start.year, eff_start.month, eff_start.day, tzinfo=timezone.utc)
        win_hi = datetime(eff_end.year, eff_end.month, eff_end.day, 23, 59, 59, tzinfo=timezone.utc)

    out_dir: Path = args.out_dir
    if not out_dir.is_absolute():
        out_dir = ROOT / out_dir
    charts_dir = out_dir / "charts"
    charts_dir.mkdir(parents=True, exist_ok=True)

    all_tagged: List[Dict[str, Any]] = []
    for strategy, symbol in _LEGS:
        p5 = _csv_path_5m(symbol)
        if not p5.is_file():
            print(f"error: missing {p5}", file=sys.stderr)
            return 2
        payload = _run_backtest_json(strategy, symbol, eff_start, eff_end)
        if payload.get("ok") is False or "result" not in payload:
            print(
                f"FAIL {strategy} {symbol}: {payload.get('error', 'no result')}",
                file=sys.stderr,
            )
            continue
        res = payload.get("result") or {}
        for t in res.get("trades") or []:
            if not isinstance(t, dict):
                continue
            if not _trade_overlaps_window(t, win_lo, win_hi):
                continue
            row = dict(t)
            row["_strategy"] = strategy
            row["_symbol"] = symbol
            all_tagged.append(row)

    all_tagged.sort(key=lambda x: _parse_iso_utc(str(x.get("exit_time") or "")))

    sys.path.insert(0, str(ROOT))
    from core.backtest.session_shade import (
        load_overnight_range_timing_from_toml,
        overnight_recap_df_slice_bounds,
    )
    from gui.chart_html import generate_chart_html

    _or_ost, _or_oen, _or_oz = load_overnight_range_timing_from_toml()

    pad = timedelta(minutes=args.padding_minutes)
    chart_links: List[Tuple[str, str, str]] = []

    ohlcv_cache: Dict[str, Tuple[pd.DataFrame, str]] = {}
    for sym in sorted({s for _, s in _LEGS}):
        p1 = _csv_path_1m(sym)
        p5 = _csv_path_5m(sym)
        csv_used = p1 if p1.is_file() else p5
        tf = "1m" if p1.is_file() else "5m"
        df = pd.read_csv(csv_used)
        ts_col = next(c for c in df.columns if str(c).lower() in ("timestamp", "time", "date", "datetime"))
        df["timestamp"] = pd.to_datetime(df[ts_col], utc=True, format="mixed")
        ohlcv_cache[sym] = (df.set_index("timestamp").sort_index(), tf)

    for i, trade in enumerate(all_tagged):
        sym = str(trade["_symbol"])
        st = str(trade["_strategy"])
        tid = str(trade.get("trade_id", f"T{i:06d}"))
        slug = f"{st}_{sym}_{tid}_{i}".replace(" ", "_")
        df_idx, tf = ohlcv_cache[sym]

        et = _parse_iso_utc(str(trade["entry_time"]))
        xt = _parse_iso_utc(str(trade["exit_time"]))
        if st == "overnight_range":
            lo_ix, hi_ix = overnight_recap_df_slice_bounds(et, xt, pad, _or_ost, _or_oen, _or_oz)
            sub = df_idx.loc[(df_idx.index >= lo_ix) & (df_idx.index <= hi_ix)]
        else:
            sub = df_idx.loc[(df_idx.index >= et - pad) & (df_idx.index <= xt + pad)]
        if sub.empty:
            continue
        bars, bar_times = dataframe_to_chart_bars_unix(sub)
        overlay = _overlay_for_trade(trade, bar_times)
        html_path = charts_dir / f"{slug}.html"
        generate_chart_html(
            symbol=sym,
            timeframe=tf,
            bars=bars,
            output_path=str(html_path),
            realtime=False,
            backtest=False,
            trade_overlays=[overlay],
            axis_time_zone="America/New_York",
            morning_range_et_shade=(st == "morning_range_reversion"),
            overnight_range_et_shade=(st == "overnight_range"),
        )
        chart_links.append((slug + ".html", st, sym))

    # --- index.html ---
    total_pnl = sum(float(t.get("pnl") or 0) for t in all_tagged)
    wins = sum(1 for t in all_tagged if float(t.get("pnl") or 0) > 0)
    n = len(all_tagged)
    wr = 100.0 * wins / n if n else 0.0

    by_st: Dict[str, Dict[str, float]] = {}
    for t in all_tagged:
        st = t["_strategy"]
        by_st.setdefault(st, {"n": 0, "pnl": 0.0})
        by_st[st]["n"] += 1
        by_st[st]["pnl"] += float(t.get("pnl") or 0)

    banner = ""
    if used_fallback:
        banner = (
            f'<p class="warn"><strong>Data window note:</strong> Requested <code>{req_start}</code>–<code>{req_end}</code> '
            f"lies beyond canonical <code>MNQ_5m_databento.csv</code> (last day <code>{mnq_hi}</code>). "
            f"Replay + charts use <strong>{eff_start}</strong> → <strong>{eff_end}</strong> instead.</p>"
        )

    rows_html = []
    for i, t in enumerate(all_tagged):
        slug = f"{t['_strategy']}_{t['_symbol']}_{t.get('trade_id', '')}_{i}".replace(" ", "_")
        chart_href = f"charts/{slug}.html"
        rows_html.append(
            "<tr>"
            f"<td>{i+1}</td>"
            f"<td>{t['_strategy']}</td>"
            f"<td>{t['_symbol']}</td>"
            f"<td>{t.get('side','')}</td>"
            f"<td>{t.get('entry_time','')}</td>"
            f"<td>{t.get('exit_time','')}</td>"
            f"<td>{t.get('quantity','')}</td>"
            f"<td>{float(t.get('pnl') or 0):.2f}</td>"
            f"<td>{t.get('exit_reason','')}</td>"
            f"<td>{t.get('initial_risk_dollars','')}</td>"
            f"<td>{t.get('bars_held','')}</td>"
            f"<td>{float(t.get('max_favorable_excursion') or 0):.2f}</td>"
            f"<td>{float(t.get('max_adverse_excursion') or 0):.2f}</td>"
            f'<td><a href="{chart_href}">chart</a></td>'
            "</tr>"
        )

    overlap_section = """
<h2>Do body_reversion and morning_range_reversion overlap?</h2>
<p><strong>Yes, in clock time they can.</strong> Shipped TOML gives <code>body_reversion</code> a wide executor window
(<code>start_time=00:00</code> / <code>end_time=23:59</code> US/Eastern in <code>config/strategies/body_reversion.toml</code>)
with <code>rth_only=false</code> by default, so a big-body 5m bar can signal anytime. <code>morning_range_reversion</code>
builds its anchor from 5m bars whose <strong>opens</strong> fall in <strong>07:00–08:00 ET</strong>, then arms fades on
<strong>sweeps after that window</strong> (<code>config/strategies/morning_range_reversion.toml</code>, module docstring in
<code>strategies/morning_range_reversion_strategy.py</code>). So you routinely get <strong>morning activity 08:00–late morning ET</strong>
while <strong>body signals can still fire on the same session date</strong> on other 5m bars.</p>
<p><strong>What happens typically:</strong> strategies do not share signal state. On the same symbol/account, the next
signal usually <strong>loses to risk / position limits</strong> (<code>max_positions</code>, pending brackets, executor gates) if the first
strategy already has exposure—there is no merge of the two edges; you get <strong>whichever path the bot executes first</strong> plus
cooldown / max-daily-trade caps per strategy config.</p>
"""

    index = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <title>Overnight + morning walk-through ({eff_start} → {eff_end})</title>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 1.25rem 2rem; max-width: 110rem; color: #111; }}
    h1 {{ font-size: 1.35rem; }}
    h2 {{ font-size: 1.05rem; margin-top: 2rem; }}
    table {{ border-collapse: collapse; width: 100%; font-size: 0.78rem; }}
    th, td {{ border: 1px solid #ddd; padding: 0.35rem 0.45rem; text-align: left; }}
    th {{ background: #f6f6f7; position: sticky; top: 0; }}
    .muted {{ color: #555; font-size: 0.9rem; }}
    .warn {{ background: #fff4e5; border: 1px solid #f59e0b; padding: 0.75rem 1rem; border-radius: 6px; }}
    code {{ background: #f4f4f5; padding: 0.1rem 0.35rem; border-radius: 4px; }}
    a {{ color: #2563eb; }}
  </style>
</head>
<body>
  <h1>Overnight range + morning range reversion — trade walk-through</h1>
  <p class="muted">Generated by <code>scripts/generate_signal_walkthrough_report.py</code> using
  <code>core/backtest_executor.py --replay</code> and <code>gui/chart_html.generate_chart_html</code>
  (see <code>docs/MAP.md</code>). Charts prefer each symbol's <code>*_1m_databento.csv</code> slice, else <code>*_5m_databento.csv</code>.</p>
  {banner}
  <p><strong>Replay window used:</strong> <code>{eff_start}</code> → <code>{eff_end}</code> (inclusive end-of-day UTC filter on overlapping trades).</p>
  <p><strong>Legs:</strong> overnight_range MNQ/MES/MGC; morning_range_reversion MNQ/MGC. Env matches BONGO portfolio replay for partial TP / sizing on overnight and morning TP recipe.</p>

  <h2>Performance (completed round-trips in window)</h2>
  <p>Trades: <strong>{n}</strong> · Win rate: <strong>{wr:.2f}%</strong> · Sum PnL: <strong>{total_pnl:,.2f}</strong></p>
  <ul>
    {"".join(f"<li><code>{k}</code>: {int(v['n'])} trades, PnL {v['pnl']:,.2f}</li>" for k, v in sorted(by_st.items()))}
  </ul>

  {overlap_section}

  <h2>Trade table + chart links</h2>
  <div style="overflow:auto; max-height: 36rem;">
  <table>
    <thead><tr>
      <th>#</th><th>strategy</th><th>sym</th><th>side</th><th>entry (UTC)</th><th>exit (UTC)</th><th>qty</th><th>pnl</th>
      <th>exit_reason</th><th>init risk $</th><th>bars</th><th>MFE</th><th>MAE</th><th>chart</th>
    </tr></thead>
    <tbody>
      {''.join(rows_html) if rows_html else '<tr><td colspan="14">No trades overlapped this window.</td></tr>'}
    </tbody>
  </table>
  </div>
</body>
</html>
"""

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "index.html").write_text(index, encoding="utf-8")
    print(f"wrote {out_dir / 'index.html'}", file=sys.stderr)
    print(f"wrote {len(chart_links)} charts under {charts_dir}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
