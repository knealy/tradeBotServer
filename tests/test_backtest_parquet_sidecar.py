"""Parquet sidecar cache: transparent CSV/parquet round-trip with mtime invalidation.

Covers ``core/backtest/parquet_cache.py`` and its integration with
``HistoricalDataLoader.load_from_csv`` (used by every walkforward / replay
script). Every test isolates a fresh ``tmp_path`` so sidecars from one test
never bleed into another.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pandas as pd
import pytest

from core.backtest.data_loader import HistoricalDataLoader, clear_backtest_csv_cache
from core.backtest.parquet_cache import (
    _SIDECAR_SUFFIX,
    _sidecar_path,
    load_ohlcv_cached,
)


CANONICAL_CSV = """timestamp,open,high,low,close,volume
2024-01-02 09:00:00,17500.25,17510.5,17498.0,17505.75,1234
2024-01-02 09:05:00,17505.75,17512.0,17502.5,17508.25,987
2024-01-02 09:10:00,17508.25,17515.0,17506.5,17512.0,1567
2024-01-02 09:15:00,17512.0,17518.5,17509.0,17516.75,2103
"""


def _write_csv(tmp_path: Path, name: str = "mnq_test.csv") -> Path:
    p = tmp_path / name
    p.write_text(CANONICAL_CSV, encoding="utf-8")
    return p


def test_sidecar_round_trip_matches_csv(tmp_path: Path) -> None:
    """Cold read parses CSV; warm read uses parquet; both return identical frames."""
    csv = _write_csv(tmp_path)
    sidecar = _sidecar_path(csv)
    assert not sidecar.exists()

    cold = load_ohlcv_cached(csv)
    assert sidecar.exists(), "first call should have written the sidecar"
    assert sidecar.suffix == _SIDECAR_SUFFIX
    warm = load_ohlcv_cached(csv)

    assert cold.shape == warm.shape == (4, 5)
    for col in ("open", "high", "low", "close", "volume"):
        assert (cold[col].to_numpy() == warm[col].to_numpy()).all(), f"col {col} drift"
    assert (cold.index == warm.index).all()
    assert getattr(cold.index, "tz", None) is None
    assert getattr(warm.index, "tz", None) is None


def test_sidecar_invalidates_on_csv_mtime_bump(tmp_path: Path) -> None:
    """Touching the CSV (newer mtime) forces the sidecar to be rebuilt."""
    csv = _write_csv(tmp_path)
    first = load_ohlcv_cached(csv)
    sidecar = _sidecar_path(csv)
    sidecar_mtime_v1 = sidecar.stat().st_mtime_ns

    # Append a new bar and bump mtime
    time.sleep(0.01)
    with csv.open("a", encoding="utf-8") as fh:
        fh.write("2024-01-02 09:20:00,17516.75,17520.0,17514.5,17519.0,1456\n")
    os.utime(csv, None)

    second = load_ohlcv_cached(csv)
    assert len(second) == len(first) + 1, "rebuilt frame should include the appended bar"
    sidecar_mtime_v2 = sidecar.stat().st_mtime_ns
    assert sidecar_mtime_v2 > sidecar_mtime_v1, "sidecar should have been rewritten"


def test_historical_data_loader_uses_sidecar(tmp_path: Path) -> None:
    """``HistoricalDataLoader.load_from_csv`` goes through the same parquet path."""
    clear_backtest_csv_cache()
    os.environ["BACKTEST_CSV_CACHE"] = "0"  # disable in-process LRU; isolate the parquet path
    try:
        csv = _write_csv(tmp_path)
        ldr = HistoricalDataLoader()
        df1 = ldr.load_from_csv(str(csv), symbol="MNQ")
        sidecar = _sidecar_path(csv)
        assert sidecar.exists(), "HistoricalDataLoader should write the sidecar on first load"
        df2 = ldr.load_from_csv(str(csv), symbol="MNQ")
        # Equal frames out of two loader calls (one cold, one warm).
        for col in ("open", "high", "low", "close", "volume"):
            assert (df1[col].to_numpy() == df2[col].to_numpy()).all()
        assert (df1.index == df2.index).all()
        assert getattr(df1.index, "tz", None) is None
    finally:
        os.environ.pop("BACKTEST_CSV_CACHE", None)
        clear_backtest_csv_cache()


def test_sidecar_disabled_when_env_off(tmp_path: Path) -> None:
    """``BACKTEST_PARQUET_CACHE=0`` skips sidecar writes entirely (debugging fallback)."""
    csv = _write_csv(tmp_path)
    os.environ["BACKTEST_PARQUET_CACHE"] = "0"
    try:
        df = load_ohlcv_cached(csv)
        assert len(df) == 4
        assert not _sidecar_path(csv).exists(), "sidecar must not be written when cache disabled"
    finally:
        os.environ.pop("BACKTEST_PARQUET_CACHE", None)


def test_sidecar_pinned_date_format_still_writes_sidecar(tmp_path: Path) -> None:
    """``date_format=…`` path through ``HistoricalDataLoader`` still emits a sidecar."""
    clear_backtest_csv_cache()
    os.environ["BACKTEST_CSV_CACHE"] = "0"
    try:
        csv = _write_csv(tmp_path)
        sidecar = _sidecar_path(csv)
        ldr = HistoricalDataLoader()
        # Note: explicitly passing the format exercises the date_format-pinned branch
        # in ``load_from_csv`` which falls outside the default parquet path but must
        # still leave behind a usable sidecar for subsequent default-format reads.
        df = ldr.load_from_csv(str(csv), symbol="MNQ", date_format="%Y-%m-%d %H:%M:%S")
        assert sidecar.exists()
        assert len(df) == 4
        # Subsequent default-format read should hit the freshly-written sidecar.
        df2 = load_ohlcv_cached(csv)
        for col in ("open", "high", "low", "close", "volume"):
            assert (df[col].to_numpy() == df2[col].to_numpy()).all()
    finally:
        os.environ.pop("BACKTEST_CSV_CACHE", None)
        clear_backtest_csv_cache()
