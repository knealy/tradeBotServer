"""PositionManager stop/TP modify helpers."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from core.position_management import PositionManager, _position_is_long


@pytest.mark.parametrize(
    "side,expected",
    [
        (0, True),
        ("0", True),
        ("LONG", True),
        ("long", True),
        (1, False),
        ("1", False),
        ("SHORT", False),
        ("SELL", False),
    ],
)
def test_position_is_long_normalizes_side(side, expected):
    assert _position_is_long(side) is expected


@pytest.mark.asyncio
async def test_modify_stop_loss_creates_sell_stop_for_long_string_side():
    adapter = MagicMock()
    pos = MagicMock()
    pos.position_id = "p1"
    pos.symbol = "MNQ"
    pos.side = "LONG"  # string — old bug treated this as short
    pos.quantity = 1
    adapter.get_positions = AsyncMock(return_value=[pos])
    adapter.get_linked_orders = AsyncMock(return_value=[])
    adapter.place_stop_order = AsyncMock(
        return_value=MagicMock(success=True, order_id="sl1", error=None)
    )
    adapter._get_tick_size = AsyncMock(return_value=0.25)
    adapter._round_to_tick_size = lambda p, t: p

    mgr = PositionManager(adapter)
    out = await mgr.modify_stop_loss("p1", 29000.0, "acct")
    assert out.get("success") is True
    kwargs = adapter.place_stop_order.await_args.kwargs
    assert kwargs["side"] == "SELL"


@pytest.mark.asyncio
async def test_modify_take_profit_creates_sell_limit_for_long_string_side():
    adapter = MagicMock()
    pos = MagicMock()
    pos.position_id = "p1"
    pos.symbol = "MNQ"
    pos.side = "LONG"
    pos.quantity = 1
    adapter.get_positions = AsyncMock(return_value=[pos])
    adapter.get_linked_orders = AsyncMock(return_value=[])
    adapter.place_limit_order = AsyncMock(
        return_value=MagicMock(success=True, order_id="tp1", error=None)
    )
    adapter._get_tick_size = AsyncMock(return_value=0.25)
    adapter._round_to_tick_size = lambda p, t: p

    mgr = PositionManager(adapter)
    out = await mgr.modify_take_profit("p1", 29200.0, "acct")
    assert out.get("success") is True
    kwargs = adapter.place_limit_order.await_args.kwargs
    assert kwargs["side"] == "SELL"
