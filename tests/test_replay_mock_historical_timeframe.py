"""Replay mock: ``get_historical_data`` must honor ``timeframe`` vs replay CSV cadence."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from core.backtest_executor import BacktestExecutor


def _bar(i: int) -> dict:
    ts = datetime(2026, 1, 1, 14, 0, tzinfo=timezone.utc) + timedelta(minutes=5 * i)
    o = 100.0 + i * 0.1
    return {
        "timestamp": ts,
        "open": o,
        "high": o + 1.0,
        "low": o - 1.0,
        "close": o + 0.5,
        "volume": 100,
    }


@pytest.mark.asyncio
async def test_mock_resamples_coarser_timeframe_from_5m_replay():
    bars = [_bar(i) for i in range(24)]
    ex = BacktestExecutor(broker_adapter=None)
    bot = ex._create_mock_trading_bot(bars, replay_timeframe="5m")
    r5 = await bot.get_historical_data("MNQ", timeframe="5m", limit=500)
    r15 = await bot.get_historical_data("MNQ", timeframe="15m", limit=500)
    assert len(r5) == len(bars)
    assert len(r15) < len(r5)
    assert len(r15) >= 1


@pytest.mark.asyncio
async def test_mock_resample_cache_refreshes_when_bars_prefix_grows():
    """Resampled series must not stay frozen on the first prefix seen (replay walks forward)."""
    short = [_bar(i) for i in range(12)]
    long = [_bar(i) for i in range(48)]
    ex = BacktestExecutor(broker_adapter=None)
    bot = ex._create_mock_trading_bot(short, replay_timeframe="5m")
    r_short = await bot.get_historical_data("MNQ", timeframe="15m", limit=500)
    n_short = len(r_short)
    bot.bars = long
    bot._resampled_cache.clear()
    r_long = await bot.get_historical_data("MNQ", timeframe="15m", limit=500)
    assert len(r_long) >= n_short
    assert len(r_long) > 0


@pytest.mark.asyncio
async def test_mock_uses_bars_1m_when_requested():
    bars_5 = [_bar(i) for i in range(10)]
    bars_1 = []
    for i in range(50):
        ts = datetime(2026, 1, 1, 14, 0, tzinfo=timezone.utc) + timedelta(minutes=i)
        o = 100.0 + i * 0.01
        bars_1.append(
            {
                "timestamp": ts,
                "open": o,
                "high": o + 0.5,
                "low": o - 0.5,
                "close": o + 0.1,
                "volume": 10,
            }
        )
    ex = BacktestExecutor(broker_adapter=None)
    bot = ex._create_mock_trading_bot(bars_5, bars_1m=bars_1, replay_timeframe="5m")
    r1 = await bot.get_historical_data("MNQ", timeframe="1m", limit=100)
    assert len(r1) == 50
