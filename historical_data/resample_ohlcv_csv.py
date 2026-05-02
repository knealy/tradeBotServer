#!/usr/bin/env python3
"""
Resample OHLCV bars in a CSV to a coarser timeframe (e.g. 1m → 5m).

Use this **before** csv_merger when combining archives that used different intervals,
so the merged file has one consistent bar size for backtests / alpha discovery.

Rules: open=first, high=max, low=min, close=last, volume=sum per bucket.
Timestamps are written as naive UTC (same convention as csv_merger).

Usage:
  python historical_data/resample_ohlcv_csv.py -i historical_data/price/merged.csv \\
    --to 5m -o historical_data/price/merged_5m.csv
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

# Same directory → import merger normalizer
_MERGER_DIR = Path(__file__).resolve().parent
if str(_MERGER_DIR) not in sys.path:
    sys.path.insert(0, str(_MERGER_DIR))

from csv_merger import normalize_ohlcv_columns  # noqa: E402


def parse_target_freq(s: str) -> str:
    """Map CLI shorthand (5m, 1h, 30s) to a pandas offset string."""
    t = s.strip().lower()
    if t.endswith("ms"):
        raise ValueError("Use s/m/h/d suffixes only (e.g. 5m not 5ms).")
    if t.endswith("s") and t[:-1].isdigit():
        return f"{int(t[:-1])}s"
    if t.endswith("m") and t[:-1].isdigit():
        return f"{int(t[:-1])}min"
    if t.endswith("h") and t[:-1].isdigit():
        return f"{int(t[:-1])}h"
    if t.endswith("d") and t[:-1].isdigit():
        return f"{int(t[:-1])}d"
    return t


def resample_ohlcv(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    work = df.set_index("timestamp", drop=True).sort_index()
    agg = work.resample(rule).agg(
        {
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        }
    )
    agg = agg.dropna(subset=["open", "close"], how="any")
    agg = agg.reset_index()
    if agg.columns[0] != "timestamp":
        agg = agg.rename(columns={agg.columns[0]: "timestamp"})
    return agg


def main() -> int:
    ap = argparse.ArgumentParser(description="Resample OHLCV CSV to a coarser timeframe.")
    ap.add_argument("-i", "--input", type=Path, required=True, help="Input CSV")
    ap.add_argument("-o", "--output", type=Path, required=True, help="Output CSV")
    ap.add_argument(
        "--to",
        required=True,
        metavar="TF",
        help="Target bar size, e.g. 5m, 15m, 1h, 30s (pandas-compatible)",
    )
    args = ap.parse_args()

    if not args.input.is_file():
        print(f"Error: not a file: {args.input}", file=sys.stderr)
        return 1

    rule = parse_target_freq(args.to)
    raw = pd.read_csv(args.input)
    df = normalize_ohlcv_columns(raw)
    n_in = len(df)

    out = resample_ohlcv(df, rule)
    n_out = len(out)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(args.output, index=False)
    print(f"Resampled {n_in} → {n_out} rows ({rule}) → {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
