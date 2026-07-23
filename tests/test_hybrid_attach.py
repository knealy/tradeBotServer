"""Hybrid pending registry + post-fill attach for Position Brackets accounts."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from trading_bot import TopStepXTradingBot


def _bot():
    bot = object.__new__(TopStepXTradingBot)
    bot._prefer_hybrid_brackets = False
    bot._position_brackets_alerted = False
    bot._hybrid_pending_brackets = {}
    bot._hybrid_bracket_tasks = {}
    bot._hybrid_attach_locks = {}
    bot.selected_account = {"id": "1", "name": "test"}
    return bot


def test_register_hybrid_pending_bracket():
    bot = _bot()
    bot.register_hybrid_pending_bracket(
        order_id="99",
        symbol="MNQ",
        side="BUY",
        quantity=1,
        stop_loss_price=100.0,
        take_profit_price=110.0,
        account_id="1",
        strategy_name="mrr",
        entry_price=105.0,
    )
    assert "99" in bot._hybrid_pending_brackets
    assert bot._hybrid_pending_brackets["99"]["symbol"] == "MNQ"
    assert bot._hybrid_pending_brackets["99"]["attached"] is False


@pytest.mark.asyncio
async def test_try_attach_places_protective_orders_when_modify_fails():
    bot = _bot()
    bot.register_hybrid_pending_bracket(
        order_id="42",
        symbol="MNQ",
        side="BUY",
        quantity=1,
        stop_loss_price=29000.0,
        take_profit_price=29200.0,
        account_id="1",
        strategy_name="smoke",
        entry_price=29100.0,
    )
    bot.get_open_orders = AsyncMock(return_value=[])  # filled / gone
    bot.get_order_history = AsyncMock(
        return_value=[{"id": "42", "status": 2}]
    )
    bot._find_hybrid_position_id = AsyncMock(return_value="pos-1")
    bot.modify_stop_loss = AsyncMock(return_value={"error": "no linked stop"})
    bot.modify_take_profit = AsyncMock(return_value={"error": "no linked tp"})
    bot.place_stop_order = AsyncMock(
        return_value={"success": True, "orderId": "sl-1"}
    )
    bot.place_market_order = AsyncMock(
        return_value={"success": True, "orderId": "tp-1"}
    )

    out = await bot.try_attach_hybrid_brackets_on_fill(
        "42", reason="unit", order_status=2
    )
    assert out.get("attached") is True
    assert out.get("sl_order_id") == "sl-1"
    assert out.get("tp_order_id") == "tp-1"
    assert "42" not in bot._hybrid_pending_brackets
    bot.place_stop_order.assert_awaited_once()
    bot.place_market_order.assert_awaited_once()
    kwargs = bot.place_market_order.await_args.kwargs
    assert kwargs.get("order_type") == "limit"
    assert kwargs.get("limit_price") == 29200.0


@pytest.mark.asyncio
async def test_try_attach_waits_while_order_still_working():
    bot = _bot()
    bot.register_hybrid_pending_bracket(
        order_id="7",
        symbol="MGC",
        side="SELL",
        quantity=1,
        stop_loss_price=4100.0,
        take_profit_price=4000.0,
        account_id="1",
    )
    bot.get_open_orders = AsyncMock(
        return_value=[{"id": "7", "status": 1}]
    )
    bot._find_hybrid_position_id = AsyncMock(return_value=None)
    bot.modify_stop_loss = AsyncMock()
    out = await bot.try_attach_hybrid_brackets_on_fill("7", reason="unit")
    assert out.get("waiting") is True
    assert out.get("attached") is not True
    bot.modify_stop_loss.assert_not_awaited()
    assert "7" in bot._hybrid_pending_brackets


@pytest.mark.asyncio
async def test_attach_brackets_to_open_position_by_symbol():
    bot = _bot()
    bot.get_open_positions = AsyncMock(
        return_value=[
            {
                "id": "pos-9",
                "symbol": "MNQ",
                "side": 0,
                "quantity": 1,
                "entryPrice": 29100.0,
            }
        ]
    )
    bot._attach_hybrid_protective_orders = AsyncMock(
        return_value={
            "success": True,
            "position_id": "pos-9",
            "sl_order_id": "sl",
            "tp_order_id": "tp",
        }
    )
    bot._position_symbol_matches = TopStepXTradingBot._position_symbol_matches
    out = await TopStepXTradingBot.attach_brackets_to_open_position(
        bot,
        "MNQ",
        stop_loss_price=29050.0,
        take_profit_price=29150.0,
    )
    assert out["success"] is True
    bot._attach_hybrid_protective_orders.assert_awaited_once()
    pending = bot._attach_hybrid_protective_orders.await_args.args[0]
    assert pending["side"] == "BUY"
    assert pending["stop_loss_price"] == 29050.0


@pytest.mark.asyncio
async def test_stop_bracket_hybrid_registers_pending_and_strong_task(monkeypatch):
    monkeypatch.setenv("TOPSTEPX_BRACKET_MODE", "position")
    bot = _bot()
    bot.place_stop_order = AsyncMock(
        return_value={"success": True, "orderId": "hyb-1"}
    )
    started = []

    def _start(oid):
        started.append(oid)

    bot._start_hybrid_bracket_monitor = _start  # type: ignore
    out = await TopStepXTradingBot._stop_bracket_hybrid(
        bot,
        symbol="MNQ",
        side="BUY",
        quantity=1,
        entry_price=29100.0,
        stop_loss_price=29050.0,
        take_profit_price=29150.0,
        account_id="1",
        strategy_name="mrr",
    )
    assert out.get("success") is True
    assert out.get("orderId") == "hyb-1"
    assert "hyb-1" in bot._hybrid_pending_brackets
    assert started == ["hyb-1"]
