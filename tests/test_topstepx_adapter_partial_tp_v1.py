"""Unit tests for ``TopStepXAdapter.place_oco_bracket_stop_entry_partial_tp_v1``."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from brokers.topstepx_adapter import TopStepXAdapter
from core.interfaces import OrderResponse


@pytest.mark.asyncio
async def test_partial_tp_v1_places_two_brackets_and_combines_ids():
    adapter = object.__new__(TopStepXAdapter)
    mock_place = AsyncMock(
        side_effect=[
            OrderResponse(success=True, order_id="111", raw_response={"leg": "scalp"}),
            OrderResponse(success=True, order_id="222", raw_response={"leg": "runner"}),
        ]
    )
    adapter.place_oco_bracket_with_stop_entry = mock_place  # type: ignore[method-assign]

    resp = await TopStepXAdapter.place_oco_bracket_stop_entry_partial_tp_v1(
        adapter,
        symbol="MNQ",
        side="BUY",
        quantity=2,
        entry_price=100.0,
        stop_loss_price=99.0,
        take_profit_full_price=103.0,
        scalp_r_multiple=1.0,
        account_id="acc-1",
        enable_breakeven=False,
        strategy_name="overnight_range",
    )
    assert resp.success is True
    assert resp.order_id == "111|222"
    assert mock_place.await_count == 2
    a0 = mock_place.await_args_list[0]
    a1 = mock_place.await_args_list[1]
    assert a0.args[2] == 1 and a0.args[5] == pytest.approx(101.0)
    assert a1.args[2] == 1 and a1.args[5] == pytest.approx(103.0)
    assert a0.kwargs.get("strategy_name") == "overnight_range_ptp_scalp"
    assert a1.kwargs.get("strategy_name") == "overnight_range_ptp_runner"


@pytest.mark.asyncio
async def test_partial_tp_v1_runner_failure_surfaces_scalp_id():
    adapter = object.__new__(TopStepXAdapter)
    adapter.place_oco_bracket_with_stop_entry = AsyncMock(
        side_effect=[
            OrderResponse(success=True, order_id="a"),
            OrderResponse(success=False, error="venue_reject"),
        ]
    )  # type: ignore[method-assign]

    resp = await TopStepXAdapter.place_oco_bracket_stop_entry_partial_tp_v1(
        adapter,
        symbol="MES",
        side="SELL",
        quantity=2,
        entry_price=5000.0,
        stop_loss_price=5010.0,
        take_profit_full_price=4970.0,
        account_id="x",
        strategy_name="s",
    )
    assert resp.success is False
    assert "runner" in (resp.error or "").lower()
    assert isinstance(resp.raw_response, dict)
    assert resp.raw_response.get("partial_tp_scalp_order_id") == "a"


@pytest.mark.asyncio
async def test_partial_tp_v1_rejects_qty_one():
    adapter = object.__new__(TopStepXAdapter)
    adapter.place_oco_bracket_with_stop_entry = AsyncMock()  # type: ignore[method-assign]

    resp = await TopStepXAdapter.place_oco_bracket_stop_entry_partial_tp_v1(
        adapter,
        symbol="MNQ",
        side="BUY",
        quantity=1,
        entry_price=1.0,
        stop_loss_price=0.0,
        take_profit_full_price=2.0,
        account_id="acc",
    )
    assert resp.success is False
    assert adapter.place_oco_bracket_with_stop_entry.await_count == 0  # type: ignore[union-attr]
