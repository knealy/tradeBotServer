"""Position-Brackets / Auto OCO reject handling for place_oco_bracket_with_stop_entry."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from trading_bot import TopStepXTradingBot


def test_is_position_brackets_mode_error_detects_broker_message():
    msg = (
        "Bracket order failed: Brackets cannot be used with Position Brackets. "
        "You must enable Auto OCO Brackets. (Code: 2)"
    )
    assert TopStepXTradingBot._is_position_brackets_mode_error(msg) is True
    assert TopStepXTradingBot._is_position_brackets_mode_error("Invalid price") is False


def test_should_prefer_hybrid_from_env_only(monkeypatch):
    bot = object.__new__(TopStepXTradingBot)
    bot._prefer_hybrid_brackets = True  # sticky must NOT force hybrid anymore
    monkeypatch.setenv("TOPSTEPX_BRACKET_MODE", "position")
    assert bot._should_prefer_hybrid_brackets() is True
    monkeypatch.setenv("TOPSTEPX_BRACKET_MODE", "hybrid")
    assert bot._should_prefer_hybrid_brackets() is True
    monkeypatch.setenv("TOPSTEPX_BRACKET_MODE", "auto_oco")
    assert bot._should_prefer_hybrid_brackets() is False
    monkeypatch.delenv("TOPSTEPX_BRACKET_MODE", raising=False)
    assert bot._should_prefer_hybrid_brackets() is False


@pytest.mark.asyncio
async def test_place_oco_refuses_and_alerts_on_position_brackets_reject(monkeypatch):
    """Default: Discord alert + refuse — no silent hybrid fallback."""
    monkeypatch.delenv("TOPSTEPX_BRACKET_MODE", raising=False)
    bot = object.__new__(TopStepXTradingBot)
    bot._prefer_hybrid_brackets = False
    bot._position_brackets_alerted = False
    bot.selected_account = {"id": "22182502", "name": "test"}
    bot.data_feed_monitor = None
    bot._last_bracket_error = None
    bot.discord_notifier = MagicMock()
    bot.discord_notifier.enabled = True
    bot.discord_notifier.send_bracket_mode_notification = AsyncMock(return_value=True)

    class _Resp:
        success = False
        error = (
            "Bracket order failed: Brackets cannot be used with Position Brackets. "
            "You must enable Auto OCO Brackets. (Code: 2)"
        )
        order_id = None
        message = None
        raw_response = {"errorCode": 2}

    bot.broker_adapter = MagicMock()
    bot.broker_adapter.place_oco_bracket_with_stop_entry = AsyncMock(return_value=_Resp())
    bot._stop_bracket_hybrid = AsyncMock(
        return_value={"success": True, "orderId": "hybrid-123"}
    )

    out = await TopStepXTradingBot.place_oco_bracket_with_stop_entry(
        bot,
        symbol="MGC",
        side="SELL",
        quantity=1,
        entry_price=4126.8,
        stop_loss_price=4150.0,
        take_profit_price=4110.0,
        account_id="22182502",
        strategy_name="morning_range_reversion",
    )
    assert out.get("success") is False
    assert out.get("method") == "refused_position_brackets"
    assert out.get("action_required") == "enable_auto_oco_brackets"
    assert "Auto OCO" in (out.get("error") or "")
    assert bot._position_brackets_alerted is True
    bot._stop_bracket_hybrid.assert_not_awaited()
    bot.discord_notifier.send_bracket_mode_notification.assert_awaited_once()


@pytest.mark.asyncio
async def test_place_oco_hybrid_when_mode_position(monkeypatch):
    """Opt-in hybrid via env still works for smoke/debug."""
    monkeypatch.setenv("TOPSTEPX_BRACKET_MODE", "position")
    bot = object.__new__(TopStepXTradingBot)
    bot._prefer_hybrid_brackets = False
    bot._position_brackets_alerted = False
    bot.selected_account = {"id": "1", "name": "acct"}
    bot.discord_notifier = MagicMock()
    bot.discord_notifier.enabled = True
    bot.discord_notifier.send_bracket_mode_notification = AsyncMock(return_value=True)
    bot.broker_adapter = MagicMock()
    bot.broker_adapter.place_oco_bracket_with_stop_entry = AsyncMock(
        side_effect=AssertionError("native OCO must not be called")
    )
    bot._stop_bracket_hybrid = AsyncMock(
        return_value={"success": True, "orderId": "h1", "method": "hybrid_auto_bracket"}
    )
    out = await TopStepXTradingBot.place_oco_bracket_with_stop_entry(
        bot,
        symbol="MNQ",
        side="BUY",
        quantity=1,
        entry_price=29100.0,
        stop_loss_price=29050.0,
        take_profit_price=29150.0,
        account_id="1",
    )
    assert out["orderId"] == "h1"
    bot.broker_adapter.place_oco_bracket_with_stop_entry.assert_not_awaited()
    bot.discord_notifier.send_bracket_mode_notification.assert_awaited_once()


@pytest.mark.asyncio
async def test_bracket_mode_alert_is_one_shot(monkeypatch):
    monkeypatch.delenv("TOPSTEPX_BRACKET_MODE", raising=False)
    bot = object.__new__(TopStepXTradingBot)
    bot._prefer_hybrid_brackets = False
    bot._position_brackets_alerted = False
    bot.selected_account = {"id": "1", "name": "acct"}
    bot.discord_notifier = MagicMock()
    bot.discord_notifier.enabled = True
    bot.discord_notifier.send_bracket_mode_notification = AsyncMock(return_value=True)

    await bot._maybe_alert_position_brackets_mode(source="broker_reject", detail="first")
    await bot._maybe_alert_position_brackets_mode(source="broker_reject", detail="second")
    bot.discord_notifier.send_bracket_mode_notification.assert_awaited_once()
