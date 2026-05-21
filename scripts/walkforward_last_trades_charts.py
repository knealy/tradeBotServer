#!/usr/bin/env python3
"""Walk-forward replay + **last N trades** candlestick charts per fold.

Runs the same calendar folds as ``scripts/walkforward_strategy_competition.py``, then for each
(strategy, symbol, fold) runs ``core/backtest_executor.py --replay --include-trades``, keeps the
**last** ``--last-trades`` completed round-trips (by ``exit_time``), and writes one
``gui.chart_html.generate_chart_html`` page per trade (1m OHLCV slice when
``historical_data/price/{sym}_1m_databento.csv`` exists, else 5m).

Default replay env matches ``scripts/generate_signal_walkthrough_report.py`` (morning TP recipe +
overnight partial TP / sizing).

Example::

  ENABLE_SIGNALR=false .venv/bin/python scripts/walkforward_last_trades_charts.py \\
    --strategies morning_range_reversion,overnight_range --symbols MNQ \\
    --days 60 --folds 5 --last-trades 5 \\
    --out-dir docs/perf/walkforward_mnq_last5_charts

Use ``--timeframe 1m --csv-template ...`` when replaying from an updated 1m canonical (same as
``walkforward_strategy_competition``).
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

# Match ``generate_signal_walkthrough_report._REPLAY_ENV`` for overnight + morning MNQ.
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


def _csv_last_date(csv_path: Path) -> date:
    df = pd.read_csv(csv_path)
    ts_col = next(c for c in df.columns if str(c).lower() in ("timestamp", "time", "date", "datetime"))
    ts = pd.to_datetime(df[ts_col].iloc[-1])
    if getattr(ts, "tz", None) is not None:
        ts = ts.tz_convert("UTC").tz_localize(None)
    return pd.Timestamp(ts).date()


def _fold_ranges(anchor_end: date, *, total_days: int, folds: int) -> List[Tuple[date, date, int]]:
    width = max(1, total_days // folds)
    ranges: List[Tuple[date, date, int]] = []
    global_start = anchor_end - timedelta(days=total_days)
    for i in range(folds):
        fs = global_start + timedelta(days=i * width)
        fe = fs + timedelta(days=width - 1)
        if i == folds - 1:
            fe = anchor_end
        ranges.append((fs, fe, i))
    return ranges


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


def _run_backtest_json(
    *,
    strategy: str,
    symbol: str,
    csv_path: Path,
    start: date,
    end: date,
    timeframe: str,
    extra_env: Dict[str, str],
) -> Dict[str, Any]:
    cmd = [
        sys.executable,
        str(ROOT / "core" / "backtest_executor.py"),
        f"--strategy={strategy}",
        f"--symbol={symbol}",
        f"--timeframe={timeframe}",
        f"--csv={csv_path}",
        f"--start={start.isoformat()}",
        f"--end={end.isoformat()}",
        "--replay",
        "--format=json",
        "--include-trades",
    ]
    env = os.environ.copy()
    env.update(_REPLAY_ENV)
    env.update(extra_env)
    proc = subprocess.run(cmd, cwd=str(ROOT), env=env, capture_output=True, text=True, check=False)
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
        return {"ok": False, "error": str(e), "strategy": strategy, "symbol": symbol, "tail": text[-1500:]}


def _resolve_csv(csv_dir: Path, csv_template: str, sym: str) -> Path:
    return Path(csv_template.format(csv_dir=str(csv_dir), sym=sym.lower(), SYM=sym))


def _ohlcv_path_for_chart(symbol: str) -> Tuple[Path, str]:
    p1 = ROOT / "historical_data" / "price" / f"{symbol.lower()}_1m_databento.csv"
    p1u = ROOT / "historical_data" / "price" / f"{symbol}_1m_databento.csv"
    for p in (p1, p1u):
        if p.is_file():
            return p, "1m"
    p5 = ROOT / "historical_data" / "price" / f"{symbol.lower()}_5m_databento.csv"
    p5u = ROOT / "historical_data" / "price" / f"{symbol}_5m_databento.csv"
    for p in (p5, p5u):
        if p.is_file():
            return p, "5m"
    return p5, "5m"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=60, help="Total calendar span ending at CSV last date")
    ap.add_argument("--folds", type=int, default=5, help="Number of non-overlapping walk-forward folds")
    ap.add_argument(
        "--strategies",
        type=str,
        default="morning_range_reversion,overnight_range",
        help="Comma-separated strategy ids",
    )
    ap.add_argument("--symbols", type=str, default="MNQ", help="Comma-separated symbols")
    ap.add_argument("--timeframe", type=str, default="5m", help="Replay bar interval (must match --csv)")
    ap.add_argument(
        "--csv-dir",
        type=Path,
        default=ROOT / "historical_data" / "price",
        help="Directory for csv-template",
    )
    ap.add_argument(
        "--csv-template",
        type=str,
        default="{csv_dir}/{sym}_5m_databento.csv",
        help="Path template with {csv_dir}, {sym} (lower), {SYM}",
    )
    ap.add_argument("--last-trades", type=int, default=5, help="How many most recent trades per fold to chart")
    ap.add_argument("--padding-minutes", type=int, default=360, help="OHLC slice padding around entry/exit")
    ap.add_argument(
        "--out-dir",
        type=Path,
        default=ROOT / "docs" / "perf" / "walkforward_last_trades_charts",
    )
    ap.add_argument("--dry-run", action="store_true", help="Print folds + planned jobs only")
    ap.add_argument(
        "--env",
        action="append",
        default=[],
        metavar="KEY=VAL",
        help="Extra env for each backtest subprocess (repeatable)",
    )
    args = ap.parse_args()

    extra_env: Dict[str, str] = {}
    for raw in args.env or []:
        if "=" not in raw:
            print(f"error: --env must be KEY=VAL, got {raw!r}", file=sys.stderr)
            return 2
        k, v = raw.split("=", 1)
        extra_env[k.strip()] = v.strip()

    strategies = [s.strip() for s in args.strategies.split(",") if s.strip()]
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    out_dir = args.out_dir if args.out_dir.is_absolute() else ROOT / args.out_dir
    charts_dir = out_dir / "charts"
    charts_dir.mkdir(parents=True, exist_ok=True)

    csv_paths: Dict[str, Path] = {}
    anchor_dates: Dict[str, date] = {}
    for sym in symbols:
        p = _resolve_csv(args.csv_dir, args.csv_template, sym)
        if not p.is_file():
            print(f"error: missing CSV for {sym}: {p}", file=sys.stderr)
            return 2
        csv_paths[sym] = p
        anchor_dates[sym] = _csv_last_date(p)

    anchor_end = min(anchor_dates.values())
    folds = _fold_ranges(anchor_end, total_days=args.days, folds=args.folds)
    n_keep = max(1, int(args.last_trades))

    print(
        f"anchor_end={anchor_end} folds={len(folds)} strategies={strategies} symbols={symbols} "
        f"timeframe={args.timeframe} last_trades={n_keep}",
        file=sys.stderr,
    )
    for fs, fe, ix in folds:
        print(f"  fold {ix}: {fs} .. {fe}", file=sys.stderr)

    if args.dry_run:
        return 0

    sys.path.insert(0, str(ROOT))
    from core.backtest.session_shade import (
        load_overnight_range_timing_from_toml,
        overnight_recap_df_slice_bounds,
    )
    from gui.chart_html import generate_chart_html

    _or_ost, _or_oen, _or_oz = load_overnight_range_timing_from_toml()

    pad = timedelta(minutes=args.padding_minutes)
    ohlcv_cache: Dict[str, Tuple[pd.DataFrame, str]] = {}
    for sym in symbols:
        csv_used, tf_chart = _ohlcv_path_for_chart(sym)
        if not csv_used.is_file():
            print(f"error: no 1m/5m chart CSV for {sym}", file=sys.stderr)
            return 2
        df = pd.read_csv(csv_used)
        ts_col = next(c for c in df.columns if str(c).lower() in ("timestamp", "time", "date", "datetime"))
        df["timestamp"] = pd.to_datetime(df[ts_col], utc=True, format="mixed")
        ohlcv_cache[sym] = (df.set_index("timestamp").sort_index(), tf_chart)

    sections_html: List[str] = []
    chart_count = 0

    for fs, fe, fold_ix in folds:
        fold_rows: List[str] = []
        for strat in strategies:
            for sym in symbols:
                payload = _run_backtest_json(
                    strategy=strat,
                    symbol=sym,
                    csv_path=csv_paths[sym],
                    start=fs,
                    end=fe,
                    timeframe=args.timeframe,
                    extra_env=extra_env,
                )
                if payload.get("ok") is False or "result" not in payload:
                    err = payload.get("error", "no result")
                    print(f"FAIL {strat} {sym} fold{fold_ix}: {err}", file=sys.stderr)
                    fold_rows.append(
                        f"<tr><td>{strat}</td><td>{sym}</td><td colspan='4'><code>{err}</code></td></tr>"
                    )
                    continue
                res = payload.get("result") or {}
                trades = [t for t in (res.get("trades") or []) if isinstance(t, dict)]
                trades.sort(key=lambda t: _parse_iso_utc(str(t.get("exit_time") or "")))
                picked = trades[-n_keep:] if len(trades) > n_keep else trades

                for ti, trade in enumerate(picked):
                    df_idx, tf = ohlcv_cache[sym]
                    et = _parse_iso_utc(str(trade["entry_time"]))
                    xt = _parse_iso_utc(str(trade["exit_time"]))
                    if strat == "overnight_range":
                        lo_ix, hi_ix = overnight_recap_df_slice_bounds(
                            et, xt, pad, _or_ost, _or_oen, _or_oz
                        )
                        sub = df_idx.loc[(df_idx.index >= lo_ix) & (df_idx.index <= hi_ix)]
                    else:
                        sub = df_idx.loc[(df_idx.index >= et - pad) & (df_idx.index <= xt + pad)]
                    if sub.empty:
                        fold_rows.append(
                            f"<tr><td>{strat}</td><td>{sym}</td><td>—</td><td colspan='3'>empty OHLC slice</td></tr>"
                        )
                        continue
                    bars, bar_times = dataframe_to_chart_bars_unix(sub)
                    overlay = _overlay_for_trade(trade, bar_times)
                    tid = str(trade.get("trade_id", f"T{ti}"))
                    slug = f"fold{fold_ix}_{strat}_{sym}_{tid}_{ti}".replace(" ", "_")
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
                        morning_range_et_shade=(strat == "morning_range_reversion"),
                        overnight_range_et_shade=(strat == "overnight_range"),
                    )
                    chart_count += 1
                    pnl = float(trade.get("pnl") or 0)
                    fold_rows.append(
                        "<tr>"
                        f"<td><code>{strat}</code></td><td><code>{sym}</code></td>"
                        f"<td>{trade.get('side','')}</td>"
                        f"<td>{trade.get('entry_time','')}</td><td>{trade.get('exit_time','')}</td>"
                        f"<td>{pnl:.2f}</td>"
                        f'<td><a href="charts/{slug}.html">chart</a></td>'
                        "</tr>"
                    )

        sections_html.append(
            f"<h2 id='fold{fold_ix}'>Fold {fold_ix}: {fs} → {fe}</h2>"
            "<table><thead><tr><th>strategy</th><th>sym</th><th>side</th><th>entry</th><th>exit</th>"
            "<th>pnl</th><th>chart</th></tr></thead><tbody>"
            f"{''.join(fold_rows) if fold_rows else '<tr><td colspan=7>No rows</td></tr>'}"
            "</tbody></table>"
        )

    index = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <title>Walk-forward last trades — {', '.join(strategies)} / {', '.join(symbols)}</title>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 1.25rem 2rem; max-width: 110rem; color: #111; }}
    table {{ border-collapse: collapse; width: 100%; font-size: 0.82rem; margin-bottom: 2rem; }}
    th, td {{ border: 1px solid #ddd; padding: 0.35rem 0.45rem; text-align: left; }}
    th {{ background: #f6f6f7; }}
    a {{ color: #2563eb; }}
    code {{ background: #f4f4f5; padding: 0.1rem 0.3rem; border-radius: 4px; }}
    .muted {{ color: #555; font-size: 0.9rem; }}
  </style>
</head>
<body>
  <h1>Walk-forward — last {n_keep} trades per fold</h1>
  <p class="muted">Generated by <code>scripts/walkforward_last_trades_charts.py</code>. Replay: <code>{args.timeframe}</code> from template <code>{args.csv_template}</code>. Charts prefer 1m canonical OHLCV when present.</p>
  <p><strong>Anchor end:</strong> {anchor_end} · <strong>Folds:</strong> {len(folds)} · <strong>Days span:</strong> {args.days}</p>
  <ul>{''.join(f"<li><a href='#fold{ix}'>Fold {ix}: {fs} → {fe}</a></li>" for fs, fe, ix in folds)}</ul>
  {''.join(sections_html)}
</body>
</html>
"""

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "index.html").write_text(index, encoding="utf-8")
    print(f"wrote {out_dir / 'index.html'}", file=sys.stderr)
    print(f"wrote {chart_count} charts under {charts_dir}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
