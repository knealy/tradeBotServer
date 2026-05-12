"""CSV cache + OHLCV replay bar conversion."""

import os
from pathlib import Path

import pandas as pd
import pytest

from core.backtest.data_loader import HistoricalDataLoader, clear_backtest_csv_cache, csv_cache_stats
from core.backtest.ohlcv import replay_bars_from_ohlcv_df


def test_replay_bars_from_ohlcv_df_matches_iterrows() -> None:
    idx = pd.date_range("2024-01-02", periods=12, freq="5min", tz="UTC")
    df = pd.DataFrame(
        {
            "open": range(12),
            "high": range(1, 13),
            "low": range(12),
            "close": range(2, 14),
            "volume": [1] * 12,
        },
        index=idx,
    )
    fast = replay_bars_from_ohlcv_df(df)
    slow = []
    for ts, row in df.iterrows():
        slow.append(
            {
                "timestamp": pd.Timestamp(ts).to_pydatetime(),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": int(row["volume"]),
            }
        )
    assert len(fast) == len(slow)
    for a, b in zip(fast, slow):
        assert pd.Timestamp(a["timestamp"]) == pd.Timestamp(b["timestamp"])
        assert abs(a["open"] - b["open"]) < 1e-9
        assert abs(a["close"] - b["close"]) < 1e-9
        assert a["volume"] == b["volume"]


def test_csv_cache_second_load_hits(tmp_path: Path) -> None:
    clear_backtest_csv_cache()
    os.environ["BACKTEST_CSV_CACHE"] = "1"
    os.environ["BACKTEST_CSV_CACHE_SIZE"] = "2"
    p = tmp_path / "bars.csv"
    p.write_text(
        "timestamp,open,high,low,close,volume\n"
        "2024-01-02 10:00:00,1,2,0.5,1.5,10\n"
        "2024-01-02 10:05:00,1.5,2.5,1,2,11\n",
        encoding="utf-8",
    )
    ld = HistoricalDataLoader()
    a = ld.load_from_csv(str(p), "MNQ")
    b = ld.load_from_csv(str(p), "MNQ")
    assert len(a) == len(b) == 2
    st = csv_cache_stats()
    assert st["hits"] >= 1
    clear_backtest_csv_cache()
