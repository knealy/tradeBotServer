"""``load_from_csv`` normalizes tz-aware timestamps to naive UTC (replay date filters)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest


def test_load_from_csv_strips_timezone_to_naive_utc(tmp_path: Path) -> None:
    from core.backtest.data_loader import HistoricalDataLoader

    p = tmp_path / "sample.csv"
    p.write_text(
        "Time,Open,High,Low,Close,Volume\n"
        "2026-05-01T14:30:00+00:00,1,1,1,1,10\n"
        "2026-05-01T14:31:00+00:00,2,2,2,2,11\n",
        encoding="utf-8",
    )
    ld = HistoricalDataLoader()
    df = ld.load_from_csv(str(p), "MNQ")
    assert getattr(df.index, "tz", None) is None
    assert str(df.index[0]) == "2026-05-01 14:30:00"
    assert pd.Timestamp(df.index[0]).tz is None
