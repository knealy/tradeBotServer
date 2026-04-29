"""Smoke tests for extracted UserHubHandlers and bracket_orders delegation."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.bracket_orders import create_bracket_order_improved
from core.user_hub_handlers import UserHubHandlers


@pytest.mark.asyncio
async def test_user_hub_handlers_on_account_no_crash():
    bot = MagicMock()
    bot.account_tracker = None
    bot.selected_account = None

    handlers = UserHubHandlers(bot)
    await handlers.on_account({"id": "12345", "balance": 50000.0})


@pytest.mark.asyncio
async def test_create_bracket_order_improved_requires_account():
    bot = MagicMock()
    bot.selected_account = None
    bot.session_token = "tok"

    out = await create_bracket_order_improved(
        bot, "MNQ", "BUY", 1, 18000.0, 17900.0, 18200.0, account_id=None
    )
    assert out.get("error") == "No account selected"


@pytest.mark.asyncio
async def test_create_bracket_order_improved_delegates_to_place_stop():
    bot = MagicMock()
    bot.selected_account = {"id": "99"}
    bot.session_token = "tok"
    bot.place_stop_order = AsyncMock(
        return_value={"orderId": "entry-1", "success": True}
    )

    def _discard_background(coro):
        coro.close()
        return MagicMock()

    with patch(
        "core.bracket_orders.asyncio.create_task", side_effect=_discard_background
    ) as create_task:
        out = await create_bracket_order_improved(
            bot, "MNQ", "BUY", 1, 18000.0, 17900.0, 18200.0, account_id=None
        )
    create_task.assert_called_once()
    assert out.get("success") is True
    assert out.get("entry_order_id") == "entry-1"
    bot.place_stop_order.assert_awaited_once()
    call_kw = bot.place_stop_order.await_args.kwargs
    assert call_kw["symbol"] == "MNQ"
    assert call_kw["side"] == "BUY"
    assert call_kw["quantity"] == 1
    assert call_kw["stop_price"] == 18000.0
