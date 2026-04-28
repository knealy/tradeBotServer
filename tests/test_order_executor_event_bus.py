"""Regression: OrderExecutor publishes ORDER_PLACED on core.event_bus (Phase 3 event-bus-collision)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from core.events import EventType
from core.interfaces import OrderResponse
from core.order_execution import OrderExecutor


@pytest.mark.asyncio
async def test_place_market_order_publishes_to_event_bus():
    bus = MagicMock()
    bus.publish = AsyncMock(return_value=None)

    broker = MagicMock()
    broker.place_market_order = AsyncMock(
        return_value=OrderResponse(
            success=True,
            order_id="ord-test-1",
            message="ok",
        )
    )

    ex = OrderExecutor(
        broker_adapter=broker,
        event_bus=bus,
        selected_account={"id": "acct-1"},
    )
    result = await ex.place_market_order("MNQ", "BUY", 2)

    assert result.get("success") is True
    broker.place_market_order.assert_awaited_once()
    bus.publish.assert_awaited_once()
    event = bus.publish.await_args[0][0]
    assert event.type == EventType.ORDER_PLACED
    assert event.data.get("order_id") == "ord-test-1"
    assert event.data.get("symbol") == "MNQ"


@pytest.mark.asyncio
async def test_place_market_order_skips_publish_when_no_bus():
    broker = MagicMock()
    broker.place_market_order = AsyncMock(
        return_value=OrderResponse(success=True, order_id="x", message="ok")
    )
    ex = OrderExecutor(broker_adapter=broker, event_bus=None, selected_account={"id": "a"})
    await ex.place_market_order("MES", "SELL", 1)
    # No crash; broker still called
    broker.place_market_order.assert_awaited_once()
