"""Smoke tests for overnight_reversion (failed-breakout fade to range mid)."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

pytest.importorskip("pytz")


def test_overnight_reversion_fade_signal_geometry():
    """First close above H → SHORT stop-entry at H; below L → LONG at L (morning-style fade)."""
    from strategies.overnight_reversion_strategy import OvernightReversionStrategy
    from strategies.overnight_range_strategy import ATRData
    from strategies.strategy_base import StrategyConfig

    cfg = StrategyConfig(
        name="overnight_reversion",
        enabled=True,
        symbols=["MNQ"],
        max_positions=2,
        position_size=1,
        risk_per_trade_percent=0.5,
        max_daily_trades=4,
        preferred_conditions=[],
        avoid_conditions=[],
        trading_start_time="00:00",
        trading_end_time="23:59",
        no_trade_start="",
        no_trade_end="",
    )
    bot = MagicMock()
    strat = OvernightReversionStrategy(bot, cfg)
    strat.allow_long = True
    strat.allow_short = True
    now = datetime.now(timezone.utc)
    hi, lo = 10010.0, 9990.0
    mid = (hi + lo) / 2.0
    atr = ATRData(
        current_atr=10.0,
        daily_atr=20.0,
        atr_zone_high=0.0,
        atr_zone_low=0.0,
        period=14,
    )
    strat._overnight_symbol_stop_atr_multiplier = MagicMock(return_value=1.5)

    sh = strat._build_fade_signal("MNQ", "high", hi, lo, mid, atr, now, 0.25)
    assert sh is not None
    assert sh["action"] == "SHORT"
    assert sh["entry_price"] == pytest.approx(hi, abs=0.01)
    assert sh["stop_loss"] == pytest.approx(hi + 10.0 * 1.5, abs=0.01)
    assert sh["take_profit"] == pytest.approx(mid, abs=0.01)
    assert sh["stop_loss"] > sh["entry_price"] > sh["take_profit"]

    lg = strat._build_fade_signal("MNQ", "low", hi, lo, mid, atr, now, 0.25)
    assert lg is not None
    assert lg["action"] == "LONG"
    assert lg["entry_price"] == pytest.approx(lo, abs=0.01)
    assert lg["stop_loss"] == pytest.approx(lo - 10.0 * 1.5, abs=0.01)
    assert lg["take_profit"] == pytest.approx(mid, abs=0.01)
    assert lg["stop_loss"] < lg["entry_price"] < lg["take_profit"]


@pytest.mark.asyncio
async def test_overnight_reversion_calculate_range_break_orders_unused():
    from strategies.overnight_reversion_strategy import OvernightReversionStrategy
    from strategies.strategy_base import StrategyConfig

    cfg = StrategyConfig(
        name="overnight_reversion",
        enabled=True,
        symbols=["MNQ"],
        max_positions=2,
        position_size=1,
        risk_per_trade_percent=0.5,
        max_daily_trades=4,
        preferred_conditions=[],
        avoid_conditions=[],
        trading_start_time="00:00",
        trading_end_time="23:59",
        no_trade_start="",
        no_trade_end="",
    )
    strat = OvernightReversionStrategy(MagicMock(), cfg)
    lo, sh = await strat.calculate_range_break_orders("MNQ")
    assert lo is None and sh is None


def test_builtin_registry_has_overnight_reversion():
    from strategies.strategy_manager import BUILTIN_STRATEGY_SPECS

    assert "overnight_reversion" in BUILTIN_STRATEGY_SPECS
    assert BUILTIN_STRATEGY_SPECS["overnight_reversion"][0] == "strategies.overnight_reversion_strategy"
