"""Software OCO for hybrid protective SL/TP legs."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from trading_bot import TopStepXTradingBot


def _bot():
    bot = object.__new__(TopStepXTradingBot)
    bot._hybrid_pending_brackets = {}
    bot._hybrid_bracket_tasks = {}
    bot._hybrid_attach_locks = {}
    bot._hybrid_oco_legs = {}
    bot._hybrid_oco_tasks = {}
    bot._hybrid_oco_locks = {}
    bot._hybrid_orphan_sweeper_task = None
    bot._hybrid_price_oco_tasks = {}
    bot._hybrid_cancel_claimed = {}
    bot._quote_cache = {}
    bot._quote_cache_lock = __import__("threading").Lock()
    bot._quote_ready_events = {}
    bot.selected_account = {"id": "1", "name": "test"}
    return bot


def test_register_hybrid_oco_pair_is_bidirectional(monkeypatch):
    bot = _bot()
    bot._start_hybrid_oco_monitor = lambda *a, **k: None  # type: ignore
    bot._start_hybrid_price_oco_watch = lambda *a, **k: None  # type: ignore
    monkeypatch.setattr(
        "core.working_order_registry.get_registry",
        lambda: type(
            "R",
            (),
            {"register": staticmethod(lambda **kw: None), "unregister": staticmethod(lambda *a: None)},
        )(),
    )
    bot.register_hybrid_oco_pair(
        sl_order_id="100",
        tp_order_id="200",
        position_id="pos1",
        account_id="1",
        symbol="MNQ",
    )
    assert bot._hybrid_oco_legs["100"]["sibling_id"] == "200"
    assert bot._hybrid_oco_legs["200"]["sibling_id"] == "100"
    assert bot._hybrid_oco_legs["100"]["leg"] == "sl"
    assert bot._hybrid_oco_legs["200"]["leg"] == "tp"


@pytest.mark.asyncio
async def test_cancel_hybrid_oco_sibling_cancels_other_leg(monkeypatch):
    bot = _bot()
    bot._start_hybrid_oco_monitor = lambda *a, **k: None  # type: ignore
    bot._start_hybrid_price_oco_watch = lambda *a, **k: None  # type: ignore
    monkeypatch.setattr(
        "core.working_order_registry.get_registry",
        lambda: type(
            "R",
            (),
            {
                "register": staticmethod(lambda **kw: None),
                "unregister": staticmethod(lambda *a: None),
            },
        )(),
    )
    bot.register_hybrid_oco_pair(
        sl_order_id="sl1",
        tp_order_id="tp1",
        position_id="p1",
        account_id="1",
        symbol="MNQ",
    )
    bot.cancel_order = AsyncMock(return_value={"success": True, "orderId": "tp1"})

    out = await bot.cancel_hybrid_oco_sibling(
        "sl1", reason="unit_fill", status=2
    )
    assert out.get("success") is True
    assert out.get("cancelled_sibling") == "tp1"
    bot.cancel_order.assert_awaited_once()
    assert "sl1" not in bot._hybrid_oco_legs
    assert "tp1" not in bot._hybrid_oco_legs


@pytest.mark.asyncio
async def test_cancel_hybrid_oco_for_position_clears_pair(monkeypatch):
    bot = _bot()
    bot._start_hybrid_oco_monitor = lambda *a, **k: None  # type: ignore
    bot._start_hybrid_price_oco_watch = lambda *a, **k: None  # type: ignore
    monkeypatch.setattr(
        "core.working_order_registry.get_registry",
        lambda: type(
            "R",
            (),
            {
                "register": staticmethod(lambda **kw: None),
                "unregister": staticmethod(lambda *a: None),
            },
        )(),
    )
    bot.register_hybrid_oco_pair(
        sl_order_id="sl2",
        tp_order_id="tp2",
        position_id="posX",
        account_id="1",
        symbol="MGC",
    )
    bot.cancel_order = AsyncMock(return_value={"success": True})
    out = await bot.cancel_hybrid_oco_for_position("posX", reason="flat")
    assert out.get("success") is True
    assert "sl2" not in bot._hybrid_oco_legs
    assert "tp2" not in bot._hybrid_oco_legs


@pytest.mark.asyncio
async def test_attach_registers_oco_pair(monkeypatch):
    bot = _bot()
    bot._start_hybrid_oco_monitor = lambda *a, **k: None  # type: ignore
    bot._start_hybrid_price_oco_watch = lambda *a, **k: None  # type: ignore
    bot._ensure_hybrid_orphan_sweeper = lambda: None  # type: ignore
    bot._resolve_hybrid_legs_by_tag = AsyncMock(return_value={})
    bot._ensure_hybrid_price_feed = AsyncMock()
    monkeypatch.setattr(
        "core.working_order_registry.get_registry",
        lambda: type(
            "R",
            (),
            {
                "register": staticmethod(lambda **kw: None),
                "unregister": staticmethod(lambda *a: None),
            },
        )(),
    )
    bot.place_stop_order = AsyncMock(
        return_value={"success": True, "orderId": "SL-9"}
    )
    bot.place_market_order = AsyncMock(
        return_value={"success": True, "orderId": "TP-9"}
    )
    pending = {
        "symbol": "MNQ",
        "side": "BUY",
        "quantity": 1,
        "stop_loss_price": 100.0,
        "take_profit_price": 110.0,
        "account_id": "1",
        "strategy_name": "t",
    }
    out = await bot._attach_hybrid_protective_orders(pending, position_id="P1")
    assert out["success"] is True
    assert "SL-9" in bot._hybrid_oco_legs
    assert bot._hybrid_oco_legs["SL-9"]["sibling_id"] == "TP-9"
    assert bot._hybrid_oco_legs["SL-9"]["stop_loss_price"] == 100.0
    assert bot._hybrid_oco_legs["SL-9"]["take_profit_price"] == 110.0
    # Tagged custom tags passed through
    assert bot.place_stop_order.await_args.kwargs.get("custom_tag", "").startswith("TB-hyb-")
    assert bot.place_market_order.await_args.kwargs.get("custom_tag", "").startswith("TB-hyb-")


def test_hybrid_protective_tag_roundtrip():
    tag = TopStepXTradingBot.hybrid_protective_tag("abcd1234ffff", "sl")
    assert tag == "TB-hyb-abcd1234-sl"
    assert TopStepXTradingBot.is_hybrid_protective_tag(tag)
    assert TopStepXTradingBot.hybrid_group_from_tag(tag) == "abcd1234"


@pytest.mark.asyncio
async def test_sweep_cancels_hyb_tag_when_flat():
    bot = _bot()
    bot.selected_account = {"id": "1"}
    bot.get_open_positions = AsyncMock(return_value=[])  # flat
    bot.get_open_orders = AsyncMock(
        return_value=[
            {
                "id": "orphan-sl",
                "customTag": "TB-hyb-deadbeef-sl",
                "contractId": "CON.F.US.MNQ.U26",
                "type": 4,
            },
            {
                "id": "orphan-tp",
                "customTag": "TB-hyb-deadbeef-tp",
                "contractId": "CON.F.US.MNQ.U26",
                "type": 1,
            },
            {
                "id": "other",
                "customTag": "TB-stop_entry-mrr-xxx",
                "type": 4,
            },
        ]
    )
    bot.cancel_order = AsyncMock(return_value={"success": True})
    out = await bot.sweep_hybrid_orphan_orders(account_id="1")
    cancelled = set(out.get("cancelled") or [])
    assert cancelled == {"orphan-sl", "orphan-tp"}
    assert bot.cancel_order.await_count == 2


@pytest.mark.asyncio
async def test_on_hybrid_leg_terminal_cancels_by_tag(monkeypatch):
    """Fill event may not match registry ids — cancel remaining via TB-hyb group tag."""
    bot = _bot()
    bot._start_hybrid_oco_monitor = lambda *a, **k: None  # type: ignore
    monkeypatch.setattr(
        "core.working_order_registry.get_registry",
        lambda: type(
            "R",
            (),
            {
                "register": staticmethod(lambda **kw: None),
                "unregister": staticmethod(lambda *a: None),
            },
        )(),
    )
    bot.get_open_orders = AsyncMock(
        return_value=[
            {
                "id": "still-sl",
                "customTag": "TB-hyb-aabbccdd-sl",
                "type": 4,
            }
        ]
    )
    bot.cancel_order = AsyncMock(return_value={"success": True})
    out = await bot.on_hybrid_protective_leg_terminal(
        order_id="unknown-broker-id",
        custom_tag="TB-hyb-aabbccdd-tp",
        account_id="1",
        status=2,
        reason="unit_tag",
    )
    assert out.get("group_cancel", {}).get("cancelled") == ["still-sl"]
    bot.cancel_order.assert_awaited_once_with("still-sl", account_id="1")


@pytest.mark.asyncio
async def test_sweep_cancels_lone_leg_when_peer_registered_missing():
    """Position may still show open briefly after TP fill — cancel SL immediately."""
    bot = _bot()
    bot.selected_account = {"id": "1"}
    bot._hybrid_oco_legs = {
        "sl-left": {
            "sibling_id": "tp-gone",
            "position_id": "pos1",
            "account_id": "1",
            "leg": "sl",
            "group_id": "aabbccdd",
        },
        "tp-gone": {
            "sibling_id": "sl-left",
            "position_id": "pos1",
            "account_id": "1",
            "leg": "tp",
            "group_id": "aabbccdd",
        },
    }
    bot.get_open_positions = AsyncMock(
        return_value=[
            {
                "id": "pos1",
                "size": 1,
                "contractId": "CON.F.US.MNQ.U26",
                "symbol": "MNQ",
            }
        ]
    )
    bot.get_open_orders = AsyncMock(
        return_value=[
            {
                "id": "sl-left",
                "customTag": "TB-hyb-aabbccdd-sl",
                "contractId": "CON.F.US.MNQ.U26",
                "type": 4,
            }
        ]
    )
    bot.cancel_order = AsyncMock(return_value={"success": True})
    out = await bot.sweep_hybrid_orphan_orders(account_id="1")
    assert "sl-left" in (out.get("cancelled") or [])
    bot.cancel_order.assert_awaited()


@pytest.mark.asyncio
async def test_price_oco_cancels_sl_on_tp_touch(monkeypatch):
    """Quote touch on TP must cancel SL by known id — no Order/search."""
    bot = _bot()
    bot._start_hybrid_oco_monitor = lambda *a, **k: None  # type: ignore
    bot._start_hybrid_price_oco_watch = lambda *a, **k: None  # type: ignore
    bot._ensure_hybrid_orphan_sweeper = lambda: None  # type: ignore
    monkeypatch.setattr(
        "core.working_order_registry.get_registry",
        lambda: type(
            "R",
            (),
            {
                "register": staticmethod(lambda **kw: None),
                "unregister": staticmethod(lambda *a: None),
            },
        )(),
    )
    bot.register_hybrid_oco_pair(
        sl_order_id="sl-px",
        tp_order_id="tp-px",
        position_id="p1",
        account_id="1",
        symbol="MNQ",
        stop_loss_price=100.0,
        take_profit_price=110.0,
        entry_side="BUY",
    )
    bot.cancel_order = AsyncMock(return_value={"success": True})

    # Simulate bid touching TP
    bot._hybrid_price_oco_evaluate("MNQ", last=110.25, bid=110.25, ask=110.50)
    # Allow scheduled task to run
    await asyncio.sleep(0.05)

    bot.cancel_order.assert_awaited()
    assert bot.cancel_order.await_args.args[0] == "sl-px"
    assert "sl-px" not in bot._hybrid_oco_legs
