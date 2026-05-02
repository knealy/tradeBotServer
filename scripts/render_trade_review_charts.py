#!/usr/bin/env python3
"""
Build per-trade review artifacts from backtest JSON + MNQ OHLCV CSV.

For each completed trade:
1. Slices ``historical_data/price/MNQ_1m_complete.csv`` (or ``--csv``) around entry/exit
   with ``--padding-minutes`` context.
2. Writes a standalone **Lightweight Charts** HTML file via ``gui.chart_html.generate_chart_html``
   with entry/exit **markers** and **price lines** (same engine as the chart server).
3. Optionally writes a **matplotlib** PNG (close + vertical lines at entry/exit) for
   quick snapshots without opening a browser.

Input JSON may be:
- A single ``--format=json`` backtest blob (``result.trades``), or
- A chunk summary file with ``all_trades`` (each row may include a ``chunk`` field).

Examples:

  .venv/bin/python scripts/render_trade_review_charts.py \\
    --json response.json \\
    --csv historical_data/price/MNQ_1m_complete.csv \\
    --out-dir docs/perf/trade_review --png

  .venv/bin/python scripts/render_trade_review_charts.py \\
    --json docs/perf/overnight_range_MNQ_1m_chunks.json \\
    --trade-id T000001
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _parse_iso_utc(s: str) -> datetime:
    s = str(s).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _load_trades(doc: Dict[str, Any]) -> List[Dict[str, Any]]:
    if isinstance(doc.get("result"), dict) and isinstance(doc["result"].get("trades"), list):
        return list(doc["result"]["trades"])
    if isinstance(doc.get("trades"), list):
        return list(doc["trades"])
    if isinstance(doc.get("all_trades"), list):
        return list(doc["all_trades"])
    raise ValueError(
        "JSON must contain result.trades, trades, or all_trades (chunk export)"
    )


def _snap_bar_time(target_unix: int, bar_times: List[int]) -> int:
    if not bar_times:
        return target_unix
    import bisect

    i = bisect.bisect_left(bar_times, target_unix)
    if i <= 0:
        return bar_times[0]
    if i >= len(bar_times):
        return bar_times[-1]
    before, after = bar_times[i - 1], bar_times[i]
    return before if (target_unix - before) <= (after - target_unix) else after


def _df_slice_to_bars(df: pd.DataFrame) -> Tuple[List[Dict[str, Any]], List[int]]:
    """Bars for ``generate_chart_html`` + sorted unix bar open times."""
    rows: List[Dict[str, Any]] = []
    times: List[int] = []
    for ts, row in df.iterrows():
        if hasattr(ts, "to_pydatetime"):
            ts = ts.to_pydatetime()
        if getattr(ts, "tzinfo", None) is None:
            ts = ts.replace(tzinfo=timezone.utc)
        u = int(ts.timestamp())
        times.append(u)
        rows.append(
            {
                "timestamp": u,
                "open": float(row.get("open", 0)),
                "high": float(row.get("high", 0)),
                "low": float(row.get("low", 0)),
                "close": float(row.get("close", 0)),
                "volume": int(row.get("volume", 0) or 0),
            }
        )
    return rows, times


def _overlay_for_trade(
    trade: Dict[str, Any], bar_times: List[int]
) -> Dict[str, Any]:
    et = _parse_iso_utc(str(trade["entry_time"]))
    xt = _parse_iso_utc(str(trade["exit_time"]))
    eu = int(et.timestamp())
    xu = int(xt.timestamp())
    return {
        "trade_id": trade.get("trade_id", ""),
        "side": str(trade.get("side", "")).upper(),
        "entry_time": _snap_bar_time(eu, bar_times),
        "exit_time": _snap_bar_time(xu, bar_times),
        "entry_price": float(trade.get("entry_price", 0)),
        "exit_price": float(trade.get("exit_price", 0)),
    }


def _save_mpl_png(
    df: pd.DataFrame,
    trade: Dict[str, Any],
    path: Path,
    title: str,
) -> None:
    import matplotlib.pyplot as plt

    et = _parse_iso_utc(str(trade["entry_time"]))
    xt = _parse_iso_utc(str(trade["exit_time"]))
    x = [ts.timestamp() for ts in df.index]
    y = df["close"].astype(float).values
    fig, ax = plt.subplots(figsize=(12, 5), dpi=120)
    ax.plot(x, y, color="#90caf9", linewidth=0.8, label="Close")
    ax.axvline(et.timestamp(), color="#66bb6a", linestyle="--", linewidth=1.2, label="Entry")
    ax.axvline(xt.timestamp(), color="#ffeb3b", linestyle="--", linewidth=1.2, label="Exit")
    ax.scatter(
        [et.timestamp()],
        [float(trade["entry_price"])],
        color="#26a69a",
        s=36,
        zorder=5,
        label="Entry px",
    )
    ax.scatter(
        [xt.timestamp()],
        [float(trade["exit_price"])],
        color="#fbc02d",
        s=36,
        zorder=5,
        label="Exit px",
    )
    ax.set_title(title)
    ax.set_xlabel("Unix time (s)")
    ax.set_ylabel("Price")
    ax.legend(loc="upper left", fontsize=8)
    ax.grid(True, alpha=0.25)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path)
    plt.close(fig)


def main() -> int:
    root = _repo_root()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", required=True, help="Backtest JSON or chunks JSON path")
    ap.add_argument(
        "--csv",
        default="historical_data/price/MNQ_1m_complete.csv",
        help="OHLCV CSV (repo-relative or absolute)",
    )
    ap.add_argument(
        "--out-dir",
        default="docs/perf/trade_review",
        help="Output directory for HTML (+ optional PNG)",
    )
    ap.add_argument("--symbol", default="MNQ")
    ap.add_argument("--timeframe", default="1m")
    ap.add_argument(
        "--padding-minutes",
        type=int,
        default=240,
        help="Minutes of candles before entry and after exit (default 240 = 4h)",
    )
    ap.add_argument(
        "--trade-id",
        action="append",
        dest="trade_ids",
        metavar="ID",
        help="Only render trades with this trade_id (repeatable)",
    )
    ap.add_argument(
        "--png",
        action="store_true",
        help="Also write matplotlib PNG snapshots (close + entry/exit lines)",
    )
    args = ap.parse_args()

    json_path = Path(args.json)
    if not json_path.is_absolute():
        json_path = root / json_path
    csv_path = Path(args.csv)
    if not csv_path.is_absolute():
        csv_path = root / csv_path
    out_dir = Path(args.out_dir)
    if not out_dir.is_absolute():
        out_dir = root / out_dir

    if not json_path.is_file():
        print(f"Missing JSON: {json_path}", file=sys.stderr)
        return 1
    if not csv_path.is_file():
        print(f"Missing CSV: {csv_path}", file=sys.stderr)
        return 1

    doc = json.loads(json_path.read_text(encoding="utf-8"))
    try:
        trades = _load_trades(doc)
    except ValueError as e:
        print(e, file=sys.stderr)
        return 1

    if args.trade_ids:
        want = set(args.trade_ids)
        trades = [t for t in trades if t.get("trade_id") in want]
        if not trades:
            print("No trades matched --trade-id", file=sys.stderr)
            return 1

    df = pd.read_csv(csv_path)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, format="mixed")
    df = df.set_index("timestamp").sort_index()

    sys.path.insert(0, str(root))
    from gui.chart_html import generate_chart_html

    out_dir.mkdir(parents=True, exist_ok=True)
    pad = timedelta(minutes=args.padding_minutes)

    for trade in trades:
        tid = str(trade.get("trade_id", "trade"))
        chunk = trade.get("chunk", "")
        et = _parse_iso_utc(str(trade["entry_time"]))
        xt = _parse_iso_utc(str(trade["exit_time"]))
        sub = df.loc[(df.index >= et - pad) & (df.index <= xt + pad)]
        if sub.empty:
            print(f"skip {tid}: no bars in CSV window", file=sys.stderr)
            continue

        bars, bar_times = _df_slice_to_bars(sub)
        overlay = _overlay_for_trade(trade, bar_times)
        slug = tid.replace(" ", "_")
        if chunk:
            slug = f"{chunk.replace('..', '_')}_{slug}"
        html_path = out_dir / f"{args.symbol}_{slug}.html"
        title_bits = [args.symbol, args.timeframe, tid]
        if chunk:
            title_bits.append(chunk)
        header = " ".join(title_bits)

        generate_chart_html(
            symbol=args.symbol,
            timeframe=args.timeframe,
            bars=bars,
            output_path=str(html_path),
            realtime=False,
            backtest=False,
            trade_overlays=[overlay],
        )
        print(f"wrote {html_path.relative_to(root)}", flush=True)

        if args.png:
            png_path = out_dir / f"{args.symbol}_{slug}.png"
            _save_mpl_png(
                sub,
                trade,
                png_path,
                title=f"{header} | matplotlib close + entry/exit",
            )
            print(f"wrote {png_path.relative_to(root)}", flush=True)

    print(
        "\nOpen each .html in a browser for full LWC candles + markers + price lines. "
        "Use **Load All Bars** only on generated files that set ``backtest=False`` (already full window). "
        "PNG files are matplotlib close-price snapshots (not pixel-identical to LWC).",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
