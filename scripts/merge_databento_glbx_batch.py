#!/usr/bin/env python3
"""
Merge a Databento GLBX batch folder (``split_symbols=true``) of ``ohlcv-1m`` CSVs
into one OHLCV file per root symbol (MNQ, MES, MGC) for ``core/backtest`` loaders.

Databento filenames look like::

    glbx-mdp3-<start>-<end>.ohlcv-1m.MNQM5.csv
    glbx-mdp3-<start>-<end>.ohlcv-1m.MNQM5-MNQU5.csv   # roll / aggregate — excluded by default

The ``*-X-Y`` suffix (hyphen between two month codes) is **not** outright micro
futures OHLCV at index scale; those files are skipped unless you pass
``--include-hyphenated``.

Deduping: after concatenating all outright files for a root, rows are sorted by
``timestamp`` then ``volume`` descending and the first row per timestamp is kept
(so at calendar overlaps the more liquid bar wins).

Output columns: ``timestamp,open,high,low,close,volume`` (naive UTC), compatible
with ``historical_data/csv_merger.py`` and ``HistoricalDataLoader``.

Examples::

    # One job folder under historical_data/price/
    python scripts/merge_databento_glbx_batch.py \\
        historical_data/price/GLBX-20260504-UDPDE7PWXR

    # Explicit outputs
    python scripts/merge_databento_glbx_batch.py ./GLBX-20260504-UDPDE7PWXR \\
        --output-mnq historical_data/price/MNQ_1m_databento_2023-2026.csv \\
        --output-mes historical_data/price/MES_1m_databento_2023-2026.csv \\
        --output-mgc historical_data/price/MGC_1m_databento_2023-2026.csv

    # Optional calendar clip (inclusive start, inclusive end day UTC)
    python scripts/merge_databento_glbx_batch.py ./batch --start 2024-01-01 --end 2025-12-31
"""

from __future__ import annotations

import argparse
import importlib.util
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

# Root symbols we support (micro equity index + micro gold on CME).
_ROOTS = ("MNQ", "MES", "MGC")
# Outright month code (one letter + one digit), e.g. MNQM5, MESZ4.
_SUFFIX_RE = re.compile(r"^(" + "|".join(_ROOTS) + r")([FGHJKMNQUVXZ]\d)$")


def _load_csv_merger_normalize():
    root = Path(__file__).resolve().parent.parent
    path = root / "historical_data" / "csv_merger.py"
    spec = importlib.util.spec_from_file_location("csv_merger", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.normalize_ohlcv_columns


_normalize = None


def normalize_ohlcv_columns(df: pd.DataFrame) -> pd.DataFrame:
    global _normalize
    if _normalize is None:
        _normalize = _load_csv_merger_normalize()
    return _normalize(df)


def _instrument_suffix(csv_path: Path) -> str:
    """Return e.g. ``MNQM5`` or ``MNQM5-MNQU5`` from filename."""
    name = csv_path.name
    marker = ".ohlcv-1m."
    if marker not in name:
        return ""
    rest = name.split(marker, 1)[1]
    return rest.removesuffix(".csv")


def _root_from_suffix(suffix: str) -> Optional[str]:
    """Map ``MNQM5`` / ``MNQM5-MNQU5`` → ``MNQ`` (first leg only for hyphenated)."""
    first = suffix.split("-", 1)[0] if "-" in suffix else suffix
    m = _SUFFIX_RE.match(first)
    return m.group(1) if m else None


def _read_one_csv(path: Path) -> pd.DataFrame:
    raw = pd.read_csv(path)
    if "ts_event" in raw.columns:
        raw = raw.rename(columns={"ts_event": "timestamp"})
    elif "timestamp" not in raw.columns:
        raise ValueError(f"{path}: expected ts_event or timestamp column, got {list(raw.columns)}")
    cols = ["timestamp", "open", "high", "low", "close", "volume"]
    missing = [c for c in cols if c not in raw.columns]
    if missing:
        raise ValueError(f"{path}: missing {missing}")
    raw = raw[cols].copy()
    return normalize_ohlcv_columns(raw)


def _merge_root(
    paths: List[Path],
    start: Optional[pd.Timestamp],
    end: Optional[pd.Timestamp],
) -> pd.DataFrame:
    if not paths:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
    parts = [_read_one_csv(p) for p in paths]
    merged = pd.concat(parts, ignore_index=True)
    if start is not None:
        merged = merged[merged["timestamp"] >= start]
    if end is not None:
        merged = merged[merged["timestamp"] < end + pd.Timedelta(days=1)]
    merged = merged.sort_values(["timestamp", "volume"], ascending=[True, False])
    merged = merged.drop_duplicates(subset=["timestamp"], keep="first")
    merged = merged.sort_values("timestamp")
    return merged.reset_index(drop=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "batch_dir",
        type=Path,
        help="Folder containing glbx-mdp3-*.ohlcv-1m.*.csv (Databento batch extract)",
    )
    parser.add_argument(
        "--roots",
        type=str,
        default=",".join(_ROOTS),
        help=f"Comma-separated roots to emit (default: {','.join(_ROOTS)})",
    )
    parser.add_argument(
        "--include-hyphenated",
        action="store_true",
        help="Also merge files whose instrument suffix contains a hyphen (roll aggregates); "
        "not recommended for single-price backtests.",
    )
    parser.add_argument("--start", type=str, default="", help="Inclusive YYYY-MM-DD lower bound (UTC)")
    parser.add_argument("--end", type=str, default="", help="Inclusive YYYY-MM-DD upper bound (UTC)")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("historical_data/price"),
        help="Directory for default output names",
    )
    parser.add_argument("--output-mnq", type=Path, default=None)
    parser.add_argument("--output-mes", type=Path, default=None)
    parser.add_argument("--output-mgc", type=Path, default=None)
    args = parser.parse_args()

    batch_dir: Path = args.batch_dir
    if not batch_dir.is_dir():
        print(f"Error: not a directory: {batch_dir}", file=sys.stderr)
        return 1

    roots = tuple(r.strip().upper() for r in args.roots.split(",") if r.strip())
    for r in roots:
        if r not in _ROOTS:
            print(f"Error: unknown root {r!r}; choose from {_ROOTS}", file=sys.stderr)
            return 1

    start = pd.Timestamp(args.start) if args.start.strip() else None
    end = pd.Timestamp(args.end) if args.end.strip() else None

    by_root: Dict[str, List[Path]] = {k: [] for k in roots}
    skipped_hyphen = 0
    skipped_unknown = 0

    for path in sorted(batch_dir.glob("*.csv")):
        if path.suffix.lower() != ".csv":
            continue
        suf = _instrument_suffix(path)
        if not suf:
            skipped_unknown += 1
            continue
        if "-" in suf and not args.include_hyphenated:
            skipped_hyphen += 1
            continue
        root = _root_from_suffix(suf)
        if root is None or root not in by_root:
            skipped_unknown += 1
            continue
        leg0 = suf.split("-", 1)[0]
        if not _SUFFIX_RE.match(leg0):
            skipped_unknown += 1
            continue
        by_root[root].append(path)

    tag = batch_dir.name.replace("/", "_")
    out_dir: Path = args.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    def default_out(r: str) -> Path:
        return out_dir / f"{r}_1m_databento_{tag}.csv"

    out_map = {
        "MNQ": args.output_mnq or default_out("MNQ"),
        "MES": args.output_mes or default_out("MES"),
        "MGC": args.output_mgc or default_out("MGC"),
    }

    print(f"Batch: {batch_dir} (tag={tag})")
    if skipped_hyphen:
        print(f"  Skipped {skipped_hyphen} hyphenated instrument files (use --include-hyphenated to merge them)")
    if skipped_unknown:
        print(f"  Skipped {skipped_unknown} non-matching CSV names")

    for root in roots:
        paths = by_root[root]
        print(f"  {root}: merging {len(paths)} files …")
        merged = _merge_root(paths, start, end)
        outp = out_map[root]
        outp.parent.mkdir(parents=True, exist_ok=True)
        merged.to_csv(outp, index=False)
        t0 = merged["timestamp"].min() if len(merged) else None
        t1 = merged["timestamp"].max() if len(merged) else None
        print(f"     → {outp}  rows={len(merged)}  range={t0} … {t1}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
