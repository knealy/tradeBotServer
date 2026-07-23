"""MRR fade counter only advances on successful broker placement."""

from datetime import date, datetime
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

from strategies.morning_range_reversion_strategy import MorningRangeReversionStrategy
from strategies.strategy_base import StrategyConfig


def _mrr_bot(**kwargs):
    defaults = {"_is_strategy_replay": False}
    defaults.update(kwargs)
    return type("B", (), defaults)()


def _mrr_cfg():
    return StrategyConfig(
        name="morning_range_reversion",
        enabled=True,
        symbols=["MGC"],
        max_positions=2,
        position_size=1,
        risk_per_trade_percent=0.5,
        max_daily_trades=12,
        preferred_conditions=[],
        avoid_conditions=[],
        trading_start_time="00:00",
        trading_end_time="23:59",
        no_trade_start="",
        no_trade_end="",
    )


def test_fade_signal_does_not_count_until_execute_success():
    """Broker reject must not consume max_fades_per_session (2026-06-25 MGC bug)."""
    strat = MorningRangeReversionStrategy(_mrr_bot(), _mrr_cfg())
    strat.max_fades_per_session = 1
    bar_et = datetime(2026, 6, 25, 8, 35, tzinfo=ZoneInfo("America/New_York"))
    sig = strat._fade_signal_after_sweep(
        "MGC",
        bar_et,
        "high",
        4003.10,
        3988.60,
        3995.85,
        14.50,
        "immediate_stop",
        bars=[{"close": 4005.0, "high": 4006.0, "low": 4000.0, "open": 4001.0}],
    )
    assert sig is not None
    st = strat._get_state("MGC")
    assert st.get("fades_this_session", 0) == 0
    assert st.get("sweep_fired_high") is not True

    strat._commit_successful_fade("MGC", date(2026, 6, 25), "high")
    assert strat._get_state("MGC")["fades_this_session"] == 1
    assert strat._get_state("MGC")["sweep_fired_high"] is True


@pytest.mark.asyncio
async def test_execute_invalid_price_reject_does_not_mark_sweep():
    """Invalid-price rejects must stay retriable (2026-07-09 MGC ↑ swept / 0 orders)."""
    strat = MorningRangeReversionStrategy(_mrr_bot(), _mrr_cfg())
    strat.max_fades_per_session = 1
    strat.place_bracket_order = AsyncMock(
        return_value={"error": "Invalid price. Price is outside allowed range. (Code: 2)"}
    )
    ok = await strat.execute(
        {
            "action": "SHORT",
            "symbol": "MGC",
            "entry_price": 4003.10,
            "stop_loss": 4031.10,
            "take_profit": 3989.70,
            "sweep": "high",
        }
    )
    assert ok is False
    st = strat._get_state("MGC")
    assert st.get("fades_this_session", 0) == 0
    assert st.get("sweep_fired_high") is not True
    assert st.get("immediate_block_until_inside") is True


@pytest.mark.asyncio
async def test_invalid_price_reject_blocks_until_inside_then_allows_retry():
    """While outside the box, invalid-price block suppresses re-signals; clearing allows retry."""
    strat = MorningRangeReversionStrategy(_mrr_bot(), _mrr_cfg())
    strat.max_fades_per_session = 1
    strat.place_bracket_order = AsyncMock(
        return_value={"error": "Invalid price. Price is outside allowed range. (Code: 2)"}
    )
    await strat.execute(
        {
            "action": "SHORT",
            "symbol": "MGC",
            "entry_price": 4003.10,
            "stop_loss": 4031.10,
            "take_profit": 3989.70,
            "sweep": "high",
        }
    )
    bar_et = datetime(2026, 6, 25, 8, 40, tzinfo=ZoneInfo("America/New_York"))
    # Still blocked (simulates price still outside [L,H]).
    sig_blocked = strat._fade_signal_after_sweep(
        "MGC",
        bar_et,
        "high",
        4003.10,
        3988.60,
        3995.85,
        14.50,
        "immediate_stop",
        bars=[{"close": 4005.0, "high": 4006.0, "low": 4000.0, "open": 4001.0}],
    )
    assert sig_blocked is None

    # Price closed back inside — analyze clears this; emulate that here.
    strat._get_state("MGC")["immediate_block_until_inside"] = False
    sig_retry = strat._fade_signal_after_sweep(
        "MGC",
        bar_et.replace(minute=45),
        "high",
        4003.10,
        3988.60,
        3995.85,
        14.50,
        "immediate_stop",
        bars=[{"close": 4005.0, "high": 4006.0, "low": 4000.0, "open": 4001.0}],
    )
    assert sig_retry is not None
    assert strat._get_state("MGC").get("fades_this_session", 0) == 0


@pytest.mark.asyncio
async def test_non_invalid_price_reject_still_marks_sweep():
    """Non-retriable rejects keep permanent sweep_fired marking (anti-spam)."""
    strat = MorningRangeReversionStrategy(_mrr_bot(), _mrr_cfg())
    strat.max_fades_per_session = 1
    strat.place_bracket_order = AsyncMock(
        return_value={"error": "Insufficient buying power"}
    )
    ok = await strat.execute(
        {
            "action": "SHORT",
            "symbol": "MGC",
            "entry_price": 4003.10,
            "stop_loss": 4031.10,
            "take_profit": 3989.70,
            "sweep": "high",
        }
    )
    assert ok is False
    st = strat._get_state("MGC")
    assert st.get("fades_this_session", 0) == 0
    assert st.get("sweep_fired_high") is True
    assert st.get("immediate_block_until_inside") is True


@pytest.mark.asyncio
async def test_execute_success_commits_fade():
    strat = MorningRangeReversionStrategy(_mrr_bot(), _mrr_cfg())
    strat.place_bracket_order = AsyncMock(return_value={"orderId": "123"})
    ok = await strat.execute(
        {
            "action": "SHORT",
            "symbol": "MGC",
            "entry_price": 4003.10,
            "stop_loss": 4031.10,
            "take_profit": 3989.70,
            "sweep": "high",
        }
    )
    assert ok is True
    assert strat._get_state("MGC")["fades_this_session"] == 1
    assert strat._get_state("MGC")["sweep_fired_high"] is True
