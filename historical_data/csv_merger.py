#!/usr/bin/env python3
"""
Merge one or more OHLCV CSV files into a single sorted, deduplicated CSV.

Input files may use any mix of headers compatible with core/backtest/data_loader:
  timestamp,open,high,low,close,volume
  Time,Open,High,Low,Close,Volume

Duplicate timestamps: rows from files later on the command line win (so you can list
archive first and a fresh API pull second).

All timestamps are normalized to naive UTC (mixed tz-aware / tz-naive inputs sort and dedupe safely).

Usage:
  python historical_data/csv_merger.py archive.csv recent.csv -o historical_data/merged.csv
  python historical_data/csv_merger.py a.csv b.csv c.csv --output merged.csv
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd


def _warn_if_mixed_intervals(dfs: list[pd.DataFrame], paths: list[Path]) -> None:
    """Warn when inputs look like different bar sizes (e.g. 1m stitched to 5m)."""
    items: list[tuple[str, float]] = []
    for df, p in zip(dfs, paths):
        ts = df["timestamp"].sort_values()
        if len(ts) < 3:
            continue
        delta = ts.diff().dt.total_seconds().median()
        if delta is None or (isinstance(delta, float) and delta != delta):
            continue
        d = float(delta)
        if d <= 0 or d > 86400:
            continue
        items.append((p.name, d))
    if len(items) < 2:
        return
    vals = [x[1] for x in items]
    lo, hi = min(vals), max(vals)
    if hi / lo >= 2.5:
        print(
            "Warning: median bar spacing differs a lot between input files "
            f"(~{lo:.0f}s vs ~{hi:.0f}s). You may be merging different timeframes — "
            "use historical_data/resample_ohlcv_csv.py to homogenize before merge.",
            file=sys.stderr,
        )
        for name, m in items:
            print(f"  {name}: median Δt ≈ {m:.0f}s", file=sys.stderr)


def normalize_ohlcv_columns(df: pd.DataFrame) -> pd.DataFrame:
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
    # Unify tz-aware vs naive columns across files (naive treated as UTC), output naive UTC.
    ts = pd.to_datetime(out["timestamp"], format="mixed", utc=True)
    out["timestamp"] = ts.dt.tz_convert(None)
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
        dfs.append(normalize_ohlcv_columns(raw))

    _warn_if_mixed_intervals(dfs, paths)

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
