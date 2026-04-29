#!/usr/bin/env python3
"""
Merge one or more OHLCV CSV files into a single sorted, deduplicated CSV.

Input files may use any mix of headers compatible with core/backtest/data_loader:
  timestamp,open,high,low,close,volume
  Time,Open,High,Low,Close,Volume

Duplicate timestamps: rows from files later on the command line win (so you can list
archive first and a fresh API pull second).

Usage:
  python historical_data/csv_merger.py archive.csv recent.csv -o historical_data/merged.csv
  python historical_data/csv_merger.py a.csv b.csv c.csv --output merged.csv
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd


def _normalize_ohlcv_columns(df: pd.DataFrame) -> pd.DataFrame:
    column_mapping: dict[str, str] = {}
    for col in df.columns:
        cl = col.strip().lower()
        if cl in ("timestamp", "time", "date", "datetime"):
            column_mapping[col] = "timestamp"
        elif cl in ("open", "high", "low", "close", "volume"):
            column_mapping[col] = cl
    out = df.rename(columns=column_mapping)
    required = ["timestamp", "open", "high", "low", "close", "volume"]
    missing = [c for c in required if c not in out.columns]
    if missing:
        raise ValueError(
            f"After normalizing headers, missing columns {missing}. "
            f"Got: {list(df.columns)}"
        )
    out = out[required].copy()
    out["timestamp"] = pd.to_datetime(out["timestamp"], format="mixed")
    for c in ("open", "high", "low", "close", "volume"):
        out[c] = pd.to_numeric(out[c], errors="coerce")
    out = out.dropna(subset=["timestamp"])
    return out


def merge_csvs(paths: list[Path], output: Path) -> int:
    if not paths:
        print("Error: no input CSV files", file=sys.stderr)
        return 1

    dfs: list[pd.DataFrame] = []
    for p in paths:
        if not p.is_file():
            print(f"Error: not a file: {p}", file=sys.stderr)
            return 1
        raw = pd.read_csv(p)
        dfs.append(_normalize_ohlcv_columns(raw))

    merged = pd.concat(dfs, ignore_index=True)
    merged = merged.sort_values(by="timestamp")
    # Later rows in file order for same timestamp win (concat order = argv order)
    merged = merged.drop_duplicates(subset=["timestamp"], keep="last")
    merged = merged.sort_values(by="timestamp")

    output.parent.mkdir(parents=True, exist_ok=True)
    merged.to_csv(output, index=False)
    print(f"Merged {len(merged)} rows -> {output}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Merge OHLCV CSVs; duplicate timestamps keep the row from the later file."
    )
    parser.add_argument(
        "inputs",
        nargs="+",
        type=Path,
        help="CSV files in order (archive first, newest export last to overwrite overlaps)",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("merged.csv"),
        help="Output path (default: ./merged.csv)",
    )
    args = parser.parse_args()
    return merge_csvs(list(args.inputs), args.output)


if __name__ == "__main__":
    raise SystemExit(main())
