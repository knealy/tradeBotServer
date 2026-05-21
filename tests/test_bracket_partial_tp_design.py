"""Tests for BONGO §1A partial-TP price/plan helpers (``core.bracket_orders``)."""

from __future__ import annotations

import pytest

from core.bracket_orders import (
    build_partial_tp_stop_entry_plan,
    compute_partial_tp_prices,
    compute_risk_distance,
)


def test_compute_risk_distance_long():
    assert compute_risk_distance("BUY", 100.0, 99.0) == pytest.approx(1.0)


def test_compute_risk_distance_short():
    assert compute_risk_distance("SELL", 100.0, 101.0) == pytest.approx(1.0)


def test_compute_partial_tp_prices_long_1r():
    tp_s, tp_r = compute_partial_tp_prices("BUY", 100.0, 99.0, 103.0, scalp_r_multiple=1.0)
    assert tp_s == pytest.approx(101.0)
    assert tp_r == pytest.approx(103.0)


def test_build_plan_splits_quantity():
    plan = build_partial_tp_stop_entry_plan(
        symbol="MNQ",
        side="BUY",
        quantity=4,
        entry_stop_price=100.0,
        stop_loss_price=99.0,
        take_profit_full_price=103.0,
        scalp_r_multiple=1.0,
    )
    assert plan.scalp.quantity == 2
    assert plan.runner.quantity == 2
    assert plan.scalp.take_profit_price == pytest.approx(101.0)
    assert plan.runner.take_profit_price == pytest.approx(103.0)


def test_build_plan_rejects_qty_one():
    with pytest.raises(ValueError):
        build_partial_tp_stop_entry_plan(
            symbol="MNQ",
            side="BUY",
            quantity=1,
            entry_stop_price=100.0,
            stop_loss_price=99.0,
            take_profit_full_price=103.0,
        )


async def test_place_partial_tp_stub_returns_structured_error_without_bot_method():
    from core.bracket_orders import place_partial_tp_oco_stop_entry_v1

    plan = build_partial_tp_stop_entry_plan(
        symbol="MES",
        side="SELL",
        quantity=2,
        entry_stop_price=5000.0,
        stop_loss_price=5010.0,
        take_profit_full_price=4970.0,
    )
    out = await place_partial_tp_oco_stop_entry_v1(object(), plan, strategy_name="body_reversion")
    assert out["success"] is False
    assert "partial_tp" in out.get("error", "")


@pytest.mark.asyncio
async def test_place_partial_tp_forwards_to_bot_method():
    from core.bracket_orders import place_partial_tp_oco_stop_entry_v1

    plan = build_partial_tp_stop_entry_plan(
        symbol="MNQ",
        side="BUY",
        quantity=2,
        entry_stop_price=100.0,
        stop_loss_price=99.0,
        take_profit_full_price=103.0,
        scalp_r_multiple=1.0,
    )

    class _Bot:
        async def place_oco_bracket_with_stop_entry_partial_tp(
            self,
            symbol,
            side,
            quantity,
            entry_price,
            stop_loss_price,
            take_profit_full_price,
            account_id=None,
            *,
            scalp_r_multiple=1.0,
            enable_breakeven=False,
            strategy_name=None,
        ):
            assert symbol == "MNQ"
            assert quantity == 2
            assert take_profit_full_price == 103.0
            return {"success": True, "orderId": "abc", "message": "ok"}

    out = await place_partial_tp_oco_stop_entry_v1(_Bot(), plan, strategy_name="x", scalp_r_multiple=1.0)
    assert out.get("success") is True
    assert out.get("orderId") == "abc"
