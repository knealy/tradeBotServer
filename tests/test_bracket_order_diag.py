"""Unit tests for bracket place/reject diagnostic logging helpers."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from brokers.topstepx_adapter import TopStepXAdapter


def test_bracket_price_vs_last_ticks():
    assert TopStepXAdapter._bracket_price_vs_last_ticks(4120.4, 4126.9, 0.1) == -65
    assert TopStepXAdapter._bracket_price_vs_last_ticks(4148.4, 4126.9, 0.1) == 215
    assert TopStepXAdapter._bracket_price_vs_last_ticks(None, 4126.9, 0.1) is None
    assert TopStepXAdapter._bracket_price_vs_last_ticks(4120.4, None, 0.1) is None


@pytest.mark.asyncio
async def test_snapshot_quote_prefers_signalr_cache():
    adapter = object.__new__(TopStepXAdapter)
    adapter.get_market_quote = AsyncMock(side_effect=AssertionError("should not call REST"))

    class _Bot:
        def __init__(self):
            self._quote_cache = {
                "MGC": {"last": 4126.9, "bid": 4126.8, "ask": 4127.0, "ts": "t0"}
            }
            self._quote_cache_lock = __import__("threading").Lock()

    adapter._trading_bot = _Bot()
    snap = await TopStepXAdapter._snapshot_quote_for_bracket_diag(adapter, "mgc")
    assert snap["source"] == "signalr_cache"
    assert snap["last"] == 4126.9
    assert snap["bid"] == 4126.8
    assert snap["ask"] == 4127.0
    adapter.get_market_quote.assert_not_awaited()


@pytest.mark.asyncio
async def test_log_bracket_order_diag_emits_payload(caplog):
    import logging

    adapter = object.__new__(TopStepXAdapter)
    adapter._snapshot_quote_for_bracket_diag = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "symbol": "MGC",
            "last": 4126.9,
            "bid": 4126.8,
            "ask": 4127.0,
            "source": "signalr_cache",
            "ts": None,
        }
    )
    order_data = {
        "accountId": 1,
        "contractId": "CON.F.US.MGC.Q26",
        "type": 4,
        "side": 1,
        "size": 2,
        "stopPrice": 4120.4,
        "stopLossBracket": {"ticks": 280, "type": 4, "size": 2},
        "takeProfitBracket": {"ticks": -127, "type": 1, "size": 2},
    }
    with caplog.at_level(logging.INFO, logger="brokers.topstepx_adapter"):
        await TopStepXAdapter._log_bracket_order_diag(
            adapter,
            event="reject",
            symbol="MGC",
            side="SELL",
            order_data=order_data,
            entry_price=4120.4,
            stop_loss_price=4148.4,
            take_profit_price=4107.7,
            tick_size=0.1,
            error="Code 2: Invalid price. Price is outside allowed range.",
            path="python_stop_entry",
        )
    joined = " ".join(r.message for r in caplog.records)
    assert "BRACKET_DIAG event=reject" in joined
    assert "last=4126.9" in joined
    assert "bid=4126.8" in joined
    assert "ask=4127.0" in joined
    assert "vs_last_ticks entry=-65" in joined
    assert '"stopPrice": 4120.4' in joined or '"stopPrice":4120.4' in joined
    assert "Invalid price" in joined
