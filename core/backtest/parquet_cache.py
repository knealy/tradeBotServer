"""Transparent Parquet sidecar cache for canonical OHLCV CSVs.

Reads / writes a sibling ``<csv>.parquet`` file next to each historical-data
CSV. On the first call the CSV is parsed and the parquet sidecar is written;
every subsequent call reads parquet directly, skipping the text-parse cost
entirely (~10× faster on multi-month / 1-minute files).

The cache is content-addressed by the CSV's modification time recorded in the
parquet file's metadata. If the CSV is touched / regenerated, the sidecar is
treated as stale and rebuilt on the next read.

Design constraints:

- Callers receive the same shape they would get from ``pd.read_csv`` +
  normalization: lowercase OHLCV columns, naive-UTC ``DatetimeIndex`` named
  ``timestamp``, sorted ascending, NaN rows dropped.
- Parquet is the source of truth at read time, but the **CSV stays canonical**
  on disk — Git diffs, manual edits, ``Edit-Recordings`` workflows, and
  databento append scripts continue to operate on the text file. The parquet
  sidecar is just a derived speedup artifact (and is ``.gitignore``-able).
- Disabled by setting ``BACKTEST_PARQUET_CACHE=0`` (rare; emergency-fallback).

Used by:
- ``core/backtest/data_loader.py::HistoricalDataLoader.load_from_csv``
- ``scripts/walkforward_*.py`` via ``load_ohlcv_cached`` helper below

This module deliberately has **no fallback** for missing pyarrow — pyarrow is
a hard dep (see ``requirements.txt``). If you're hitting an ImportError here,
``pip install pyarrow`` in your venv.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

# Metadata key written into the parquet file pointing back at the source CSV's
# mtime_ns. Read on every lookup; mismatch invalidates the sidecar.
_SIDECAR_META_KEY = b"_canonical_csv_mtime_ns"
_SIDECAR_SUFFIX = ".parquet"


def _parquet_cache_enabled() -> bool:
    """``BACKTEST_PARQUET_CACHE=0`` disables sidecar usage (debugging fallback)."""
    v = os.environ.get("BACKTEST_PARQUET_CACHE", "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def _sidecar_path(csv_path: Path) -> Path:
    """``foo.csv`` → ``foo.csv.parquet`` (keep ``.csv`` so symbol detection still works)."""
    return csv_path.with_suffix(csv_path.suffix + _SIDECAR_SUFFIX)


def _read_sidecar_mtime_ns(parquet_path: Path) -> Optional[int]:
    """Return mtime_ns embedded in the parquet file's metadata, or None on any failure."""
    try:
        import pyarrow.parquet as pq

        meta = pq.read_metadata(str(parquet_path))
        if meta.metadata is None:
            return None
        raw = meta.metadata.get(_SIDECAR_META_KEY)
        if raw is None:
            return None
        return int(raw.decode("utf-8"))
    except Exception as exc:
        logger.debug("parquet sidecar mtime read failed for %s: %s", parquet_path, exc)
        return None


def _write_sidecar(df: pd.DataFrame, parquet_path: Path, csv_mtime_ns: int) -> None:
    """Persist normalized OHLCV frame to parquet with the source CSV's mtime in metadata.

    Failure here is non-fatal: callers still got their DataFrame from the CSV
    parse; we just lose the speedup for next time. Logs a warning so a broken
    write surface (read-only mount, permission flap, disk full) isn't silent.
    """
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq

        table = pa.Table.from_pandas(df.reset_index(), preserve_index=False)
        schema = table.schema
        existing = dict(schema.metadata or {})
        existing[_SIDECAR_META_KEY] = str(int(csv_mtime_ns)).encode("utf-8")
        table = table.replace_schema_metadata(existing)
        pq.write_table(
            table,
            str(parquet_path),
            compression="zstd",
            compression_level=3,
        )
        logger.debug("wrote parquet sidecar %s (%d rows)", parquet_path.name, len(df))
    except Exception as exc:
        logger.warning("parquet sidecar write failed for %s: %s", parquet_path, exc)


_CONTRACT_ROLL_GUARD_ENV = "BACKTEST_QUARANTINE_ROLL_DAYS"
_CONTRACT_ROLL_GAP_RATIO = 5.0       # next_open vs close gap > 5× typical-bar-range = impossible
_CONTRACT_ROLL_MAX_DT_MIN = 5.0      # only flag gaps between bars ≤ 5 min apart
_CONTRACT_ROLL_MIN_FLAGS_PER_DAY = 5 # a date is only "rolled" if it has ≥N impossible gaps


def _quarantine_contract_roll_dates(df: pd.DataFrame) -> pd.DataFrame:
    """Drop calendar days where the bar-to-bar |next_open − close| exceeds a
    symbol-agnostic outlier threshold — the signature of two contract months
    being interleaved into one timestream (the standard databento corruption
    near quarterly futures rolls, e.g. 2025-09-15 = Sep→Dec MNQ roll).

    Heuristic (gap_ratio):
      • ``typical_range = median(high − low)`` across the first 5000 bars
        gives a stable per-symbol normaliser without taking the symbol as
        an argument.
      • ``gap = |open_t+1 − close_t|`` measured between consecutive bars
        whose timestamp delta ≤ ``_CONTRACT_ROLL_MAX_DT_MIN`` (default 5 min).
      • Any bar with ``gap / typical_range > _CONTRACT_ROLL_GAP_RATIO``
        (default 5×) is *flagged*.
      • A calendar date is only quarantined when it has
        ``≥ _CONTRACT_ROLL_MIN_FLAGS_PER_DAY`` flagged bars (default 5) —
        a real roll day typically has 30–100+ flags, while a one-off
        fat-finger print or isolated bad tick has just 1.
      • All bars on quarantined dates are dropped.

    Rationale — overnight session-open gaps (CME 18:00 ET reopen, holiday
    early closes) routinely produce 0.5–2× typical-range gaps; real liquid
    futures basically never produce a > 5× typical-range gap in ≤ 5 min
    between adjacent bars. The Sep 15 2025 MNQ corruption produced 535
    such gaps in one calendar day, each ~240pt (≈ 24× typical 10pt 1m bar
    range). A future databento refresh that fixes the CSV will naturally
    have no flagged dates → no-op.

    Disable via env ``BACKTEST_QUARANTINE_ROLL_DAYS=0`` (e.g. for unit
    tests that intentionally feed pathological data).

    Idempotent: a frame already free of flagged dates passes through with
    O(N) cost (one shifted subtraction + one boolean reduction).
    """
    if os.environ.get(_CONTRACT_ROLL_GUARD_ENV, "1").strip().lower() in ("0", "false", "no", "off"):
        return df
    if len(df) < 3 or "open" not in df.columns or "close" not in df.columns:
        return df

    # Per-symbol typical-bar-range normaliser: use the head 5000 rows (stable,
    # avoids paying O(N) on every load for huge frames). Even on a roll-day
    # the median is dominated by clean bars.
    head = df.iloc[:5000] if len(df) > 5000 else df
    typical = float((head["high"] - head["low"]).median())
    if not typical > 0:
        return df

    next_open = df["open"].shift(-1)
    gap = (next_open - df["close"]).abs()
    # dt between rows
    ts_diff_min = df.index.to_series().diff(-1).dt.total_seconds().abs() / 60.0
    impossible = (gap / typical > _CONTRACT_ROLL_GAP_RATIO) & (
        ts_diff_min <= _CONTRACT_ROLL_MAX_DT_MIN
    )
    if not impossible.any():
        return df

    # Per-date flag count — only quarantine days with ≥ MIN_FLAGS_PER_DAY
    # impossible gaps. Roll days carry 30-100+ flags; isolated bad ticks
    # carry exactly 1.
    flagged_dates = df.index[impossible].normalize()
    counts = flagged_dates.value_counts()
    bad_dates = set(counts[counts >= _CONTRACT_ROLL_MIN_FLAGS_PER_DAY].index)
    if not bad_dates:
        return df

    keep_mask = ~df.index.normalize().isin(bad_dates)
    n_dropped = int((~keep_mask).sum())
    sample = sorted({d.date().isoformat() for d in bad_dates})
    head_sample = sample[:6]
    tail = f" ... +{len(sample) - 6} more" if len(sample) > 6 else ""
    logger.warning(
        "Quarantined %d roll-corrupted calendar day(s), %d bars dropped (gap/typical_range > %.0f× within ≤%.0f min, ≥%d flags/day). "
        "Sample dates: %s%s. Disable via %s=0.",
        len(bad_dates), n_dropped, _CONTRACT_ROLL_GAP_RATIO,
        _CONTRACT_ROLL_MAX_DT_MIN, _CONTRACT_ROLL_MIN_FLAGS_PER_DAY,
        head_sample, tail, _CONTRACT_ROLL_GUARD_ENV,
    )
    return df.loc[keep_mask]


def _normalize_ohlcv_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Canonical shape every caller expects: lowercase cols, naive-UTC sorted index, no NaN.

    Same normalization the long-standing ``HistoricalDataLoader.load_from_csv``
    path applies, lifted into a helper so the parquet/CSV branches stay in
    sync. Idempotent — safe to call on a frame already in canonical shape.

    Also runs ``_quarantine_contract_roll_dates`` at the end to drop calendar
    days corrupted by contract-roll interleaving (the databento Sep/Dec/Mar/Jun
    quarterly-roll issue). Disable via env ``BACKTEST_QUARANTINE_ROLL_DAYS=0``.
    """
    column_mapping = {}
    for col in df.columns:
        cl = str(col).lower()
        if cl in ("timestamp", "time", "date", "datetime"):
            column_mapping[col] = "timestamp"
        elif cl == "open":
            column_mapping[col] = "open"
        elif cl == "high":
            column_mapping[col] = "high"
        elif cl == "low":
            column_mapping[col] = "low"
        elif cl == "close":
            column_mapping[col] = "close"
        elif cl == "volume":
            column_mapping[col] = "volume"
    if column_mapping:
        df = df.rename(columns=column_mapping)

    if "timestamp" not in df.columns:
        if df.index.name == "timestamp" or isinstance(df.index, pd.DatetimeIndex):
            df = df.reset_index()
        else:
            raise ValueError(f"No timestamp column found. Available columns: {list(df.columns)}")

    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=False, errors="coerce")
    df = df.set_index("timestamp").sort_index()
    if getattr(df.index, "tz", None) is not None:
        df.index = df.index.tz_convert("UTC").tz_localize(None)

    required = ["open", "high", "low", "close", "volume"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"CSV missing required columns: {missing}. Available: {list(df.columns)}")
    for col in required:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=required)
    df = _quarantine_contract_roll_dates(df)
    return df


def load_ohlcv_cached(csv_path: Path | str, *, force_csv: bool = False) -> pd.DataFrame:
    """Return canonical OHLCV DataFrame for ``csv_path``, using parquet sidecar when fresh.

    First read on a given CSV: ``pd.read_csv`` → normalize → write
    ``<csv>.parquet`` sidecar → return frame. Subsequent reads: load the
    sidecar and skip CSV parsing entirely.

    The sidecar carries the source CSV's ``mtime_ns`` in its parquet metadata;
    a mismatch (CSV regenerated by databento, manually edited, etc.) forces a
    rebuild on the next call. Set ``BACKTEST_PARQUET_CACHE=0`` to disable
    sidecar use entirely.

    Returned frame: naive-UTC ``DatetimeIndex`` named ``timestamp``, lowercase
    ``open / high / low / close / volume`` columns, sorted, NaN rows dropped.
    """
    p = Path(csv_path).expanduser().resolve()
    if not p.is_file():
        raise FileNotFoundError(f"OHLCV CSV not found: {p}")
    mtime_ns = int(p.stat().st_mtime_ns)

    if _parquet_cache_enabled() and not force_csv:
        sidecar = _sidecar_path(p)
        if sidecar.is_file():
            recorded = _read_sidecar_mtime_ns(sidecar)
            if recorded == mtime_ns:
                try:
                    df = pd.read_parquet(sidecar, engine="pyarrow")
                except Exception as exc:
                    logger.warning(
                        "parquet sidecar read failed for %s (%s); falling back to CSV",
                        sidecar, exc,
                    )
                else:
                    if "timestamp" in df.columns:
                        df = df.set_index("timestamp")
                    df = df.sort_index()
                    if getattr(df.index, "tz", None) is not None:
                        df.index = df.index.tz_convert("UTC").tz_localize(None)
                    # Older sidecars were written before the contract-roll
                    # quarantine existed; re-apply on every read so stale
                    # sidecars cannot leak roll-corrupted bars back into the
                    # engine. Idempotent + O(N) — no-op once sidecars are
                    # rebuilt fresh.
                    df = _quarantine_contract_roll_dates(df)
                    logger.debug("parquet sidecar hit %s (%d rows)", sidecar.name, len(df))
                    return df
            else:
                logger.debug(
                    "parquet sidecar stale for %s (mtime_ns %s != %s); rebuilding",
                    sidecar.name, recorded, mtime_ns,
                )

    raw = pd.read_csv(p)
    df = _normalize_ohlcv_frame(raw)

    if _parquet_cache_enabled() and not force_csv:
        _write_sidecar(df, _sidecar_path(p), mtime_ns)

    return df


__all__ = [
    "load_ohlcv_cached",
    "_normalize_ohlcv_frame",  # exported for HistoricalDataLoader reuse
]
