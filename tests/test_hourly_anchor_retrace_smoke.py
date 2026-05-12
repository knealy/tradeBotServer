"""Smoke tests for hourly_anchor_retrace (anchor hour sweep → stop-entry)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone


def _utc(*args, **kwargs):
    return datetime(*args, tzinfo=timezone.utc, **kwargs)


class _MockBot:
    def __init__(self, bars):
        self.bars = bars
        self.selected_account = {"id": "test", "name": "TEST"}
        self._is_strategy_replay = True
        self._current_bar_timestamp = bars[-1]["timestamp"] if bars else None

    async def get_historical_data(self, symbol, timeframe=None, limit=None, **_):
        if limit:
            return self.bars[-limit:]
        return self.bars

    async def get_open_positions(self, account_id=None):
        return []


def test_strategy_registered_in_manager():
    from strategies.strategy_manager import BUILTIN_STRATEGY_SPECS

    assert "hourly_anchor_retrace" in BUILTIN_STRATEGY_SPECS
    module, cls, _d = BUILTIN_STRATEGY_SPECS["hourly_anchor_retrace"]
    assert module == "strategies.hourly_anchor_retrace_strategy"
    assert cls == "HourlyAnchorRetraceStrategy"


def test_strategy_registered_in_backtest_executor():
    from core.backtest_executor import BacktestExecutor

    klass = BacktestExecutor()._get_strategy_class("hourly_anchor_retrace")
    assert klass is not None
    assert klass.__name__ == "HourlyAnchorRetraceStrategy"


def test_analyze_emits_short_on_first_close_above_anchor_high():
    from strategies.hourly_anchor_retrace_strategy import HourlyAnchorRetraceStrategy
    from strategies.strategy_base import StrategyConfig

    cfg = StrategyConfig(
        name="hourly_anchor_retrace",
        enabled=True,
        symbols=["MNQ"],
        max_positions=1,
        position_size=1,
        risk_per_trade_percent=0.5,
        max_daily_trades=2,
        preferred_conditions=[],
        avoid_conditions=[],
        trading_start_time="00:00",
        trading_end_time="23:59",
        no_trade_start="",
        no_trade_end="",
    )

    # 2026-01-07 7:00 ET = 12:00 UTC (EST)
    t0 = _utc(2026, 1, 7, 12, 0)
    bars = []

    # Build 07:00–07:55 ET anchor hour (12 bars).
    for i in range(12):
        bars.append(
            {
                "timestamp": t0 + timedelta(minutes=5 * i),
                "open": 28720.0,
                "high": 28751.0,
                "low": 28705.5,
                "close": 28730.0,
                "volume": 1,
            }
        )

    # First bar after 08:00 ET with close above H triggers SHORT arming at H.
    bars.append(
        {
            "timestamp": t0 + timedelta(minutes=5 * 12),
            "open": 28745.0,
            "high": 28760.0,
            "low": 28740.0,
            "close": 28755.0,
            "volume": 1,
        }
    )

    bot = _MockBot(bars[:1])
    strat = HourlyAnchorRetraceStrategy(bot, cfg)

    sig = None
    for k in range(1, len(bars) + 1):
        bot.bars = bars[:k]
        bot._current_bar_timestamp = bars[k - 1]["timestamp"]
        sig = asyncio.run(strat.analyze("MNQ"))
        if sig is not None:
            break

    assert sig is not None
    assert sig["action"] == "SHORT"
    # Entry at breached extreme (H), rounded to ticks.
    assert sig["entry_price"] == 28751.0
    # half-range = (28751 - 28705.5)/2 = 22.75
    assert sig["stop_loss"] == 28773.75
    assert sig["take_profit"] == 28728.25


def test_entry_offset_ticks_moves_entry_inside_range():
    from strategies.hourly_anchor_retrace_strategy import HourlyAnchorRetraceStrategy
    from strategies.strategy_base import StrategyConfig

    cfg = StrategyConfig(
        name="hourly_anchor_retrace",
        enabled=True,
        symbols=["MNQ"],
        max_positions=1,
        position_size=1,
        risk_per_trade_percent=0.5,
        max_daily_trades=2,
        preferred_conditions=[],
        avoid_conditions=[],
        trading_start_time="00:00",
        trading_end_time="23:59",
        no_trade_start="",
        no_trade_end="",
    )

    t0 = _utc(2026, 1, 7, 12, 0)
    bars = []
    for i in range(12):
        bars.append(
            {
                "timestamp": t0 + timedelta(minutes=5 * i),
                "open": 100,
                "high": 110,
                "low": 100,
                "close": 105,
                "volume": 1,
            }
        )
    bars.append(
        {
            "timestamp": t0 + timedelta(minutes=5 * 12),
            "open": 109,
            "high": 112,
            "low": 108,
            "close": 111,
            "volume": 1,
        }
    )
    bot = _MockBot(bars[:1])
    strat = HourlyAnchorRetraceStrategy(bot, cfg)
    strat.entry_offset_ticks = 1.0

    sig = None
    for k in range(1, len(bars) + 1):
        bot.bars = bars[:k]
        bot._current_bar_timestamp = bars[k - 1]["timestamp"]
        sig = asyncio.run(strat.analyze("MNQ"))
        if sig is not None:
            break
    assert sig is not None
    assert sig["entry_price"] == 109.75

