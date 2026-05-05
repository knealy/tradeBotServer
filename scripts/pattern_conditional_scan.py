#!/usr/bin/env python3
"""
Scan 1m OHLCV CSVs for conditional probabilities (5m bars, NY time).

Uses ``HistoricalDataLoader`` + ``core.research.pattern_conditional``:
Fisher exact tests on 2×2 tables, Benjamini–Hochberg FDR, plus RTH
prior-day-high/low breakout → re-touch stats.

Examples:

  .venv/bin/python scripts/pattern_conditional_scan.py --symbol MNQ \\
    --csv historical_data/price/MNQ_1m_databento_GLBX-20260504-UDPDE7PWXR.csv \\
    --output docs/alpha/pattern_scan_MNQ.md

  .venv/bin/python scripts/pattern_conditional_scan.py --symbols MNQ MES MGC \\
    --start 2024-01-01 --output-dir docs/alpha
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional
from zoneinfo import ZoneInfo

import pandas as pd

REPO = Path(__file__).resolve().parent.parent
_NY = ZoneInfo("America/New_York")
sys.path.insert(0, str(REPO))

from core.backtest.data_loader import HistoricalDataLoader
from core.research.pattern_conditional import (
    apply_fdr_to_rows,
    collect_pattern_results,
    ensure_ny_index,
    prior_day_level_reversion_stats,
    render_pattern_report,
    resample_ohlcv,
)


def _default_databento_csv(symbol: str) -> Path:
    root = REPO / "historical_data" / "price"
    matches = sorted(root.glob(f"{symbol}_1m_databento_*.csv"))
    if not matches:
        raise FileNotFoundError(f"No {symbol}_1m_databento_*.csv under {root}")
    return matches[-1]


def _parse_date(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _abs(p: Path) -> Path:
    return p if p.is_absolute() else (REPO / p)


def run_one(
    symbol: str,
    csv_path: Path,
    start: Optional[datetime],
    end: Optional[datetime],
    fdr_alpha: float,
) -> str:
    loader = HistoricalDataLoader()
    df = loader.load_from_csv(str(csv_path), symbol)
    df = ensure_ny_index(df)
    if start is not None:
        t0 = pd.Timestamp(start)
        if t0.tz is None:
            t0 = t0.tz_localize(_NY)
        df = df[df.index >= t0]
    if end is not None:
        t1 = pd.Timestamp(end)
        if t1.tz is None:
            t1 = t1.tz_localize(_NY)
        df = df[df.index <= t1]
    n1 = len(df)
    if n1 < 500:
        raise ValueError(f"Too few 1m bars after filter: {n1}")
    df5 = resample_ohlcv(df, "5min")
    n5 = len(df5)
    raw_rows = collect_pattern_results(df5)
    rows_fdr = apply_fdr_to_rows(raw_rows, alpha=fdr_alpha)
    lvl = prior_day_level_reversion_stats(df5)
    try:
        csv_rel = str(csv_path.relative_to(REPO))
    except ValueError:
        csv_rel = str(csv_path)
    return render_pattern_report(
        symbol=symbol,
        csv_path=csv_rel,
        start=str(start) if start else None,
        end=str(end) if end else None,
        n_bars_1m=n1,
        n_bars_5m=n5,
        rows_fdr=rows_fdr,
        level_stats=lvl,
        alpha=fdr_alpha,
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbol", help="Single symbol (e.g. MNQ)")
    ap.add_argument(
        "--symbols",
        nargs="+",
        help="Multiple symbols; uses latest databento CSV per symbol if --csv omitted",
    )
    ap.add_argument("--csv", type=Path, help="Path to 1m OHLCV CSV (single --symbol only)")
    ap.add_argument("--start", default=None, help="Inclusive start (ISO date, interpreted as NY day boundary)")
    ap.add_argument("--end", default=None, help="Inclusive end (ISO datetime/date)")
    ap.add_argument("--output", type=Path, help="Markdown output path (single symbol)")
    ap.add_argument("--output-dir", type=Path, help="Write pattern_scan_{SYM}.md here for each symbol")
    ap.add_argument("--fdr-alpha", type=float, default=0.05)
    args = ap.parse_args()

    if not args.symbol and not args.symbols:
        args.symbols = ["MNQ", "MES", "MGC"]

    syms: List[str]
    if args.symbol:
        syms = [args.symbol.upper()]
    else:
        syms = [s.upper() for s in (args.symbols or [])]

    start = _parse_date(args.start)
    end = _parse_date(args.end)

    if args.output and len(syms) > 1:
        ap.error("--output is only valid with a single symbol")
    if not args.output and not args.output_dir:
        args.output_dir = Path("docs/alpha")

    out_paths: List[Path] = []
    for sym in syms:
        csv_p = args.csv if args.csv and len(syms) == 1 else _default_databento_csv(sym)
        if args.csv and len(syms) > 1:
            ap.error("Pass per-symbol --csv with --symbol, or omit --csv for --symbols auto-glob")
        text = run_one(sym, csv_p, start, end, args.fdr_alpha)
        if args.output:
            outp = _abs(args.output)
        else:
            d = _abs(args.output_dir or Path("docs/alpha"))
            d.mkdir(parents=True, exist_ok=True)
            outp = d / f"pattern_scan_{sym}.md"
        outp.write_text(text, encoding="utf-8")
        print(f"Wrote {outp.relative_to(REPO)}")
        out_paths.append(outp)

    # lightweight index
    if len(out_paths) > 1:
        idx = _abs(Path("docs/alpha")) / "pattern_scan_INDEX.md"
        lines = [
            "# Pattern scan outputs",
            "",
            f"Generated multi-symbol conditional scans (Fisher + BH FDR).",
            "",
        ]
        for p in out_paths:
            lines.append(f"- [`{p.name}`]({p.name})")
        idx.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"Wrote {idx.relative_to(REPO)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
