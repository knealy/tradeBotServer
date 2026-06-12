"""Pinning tests for ``core.order_verifier.verify_order_landed``.

This module is the SignalR-zombie tripwire from the 2026-06-11 incident:
when a placed order's confirmation never arrives via the User Hub, we
fall back to REST polling and emit a loud log line if the order is
nowhere to be found.
"""

from __future__ import annotations

import asyncio
from typing import List

import pytest

from core.data_feed_health import DataFeedHealthMonitor
from core.order_verifier import (
    OrderVerifyResult,
    verify_order_landed,
)


class FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self._t = start

    def __call__(self) -> float:
        return self._t

    def advance(self, dt: float) -> None:
        self._t += dt


class FakeBrokerAdapter:
    """Stand-in with a programmable ``get_open_orders`` queue."""
    def __init__(self) -> None:
        self.calls: int = 0
        # Queue of responses; each call pops the next.  If empty, return [].
        self.responses: List[list] = []
        # Optional: raise this exception on the next call (then clear).
        self.raise_next: Exception | None = None

    async def get_open_orders(self, account_id):  # noqa: ARG002
        self.calls += 1
        if self.raise_next is not None:
            exc = self.raise_next
            self.raise_next = None
            raise exc
        if self.responses:
            return self.responses.pop(0)
        return []


@pytest.fixture
def monitor():
    clk = FakeClock(start=1000.0)
    return DataFeedHealthMonitor(
        market_hub_max_silence_s=60.0,
        user_hub_max_silence_after_order_s=30.0,
        startup_grace_s=10.0,
        clock_mono=clk,
    ), clk


# ───────────────── happy path: User Hub confirms ──────────────────


@pytest.mark.asyncio
async def test_returns_ok_user_hub_when_event_arrives(monitor):
    m, clk = monitor
    broker = FakeBrokerAdapter()
    m.record_order_placed()
    # Schedule the User Hub event after 0.5 s real time.
    async def _confirm():
        await asyncio.sleep(0.1)
        m.record_user_event("order")

    task = asyncio.create_task(_confirm())
    result = await verify_order_landed(
        order_id="ABC123",
        account_id="acct-1",
        broker_adapter=broker,
        monitor=m,
        strategy_name="test",
        symbol="MGC",
        soft_timeout_s=5.0,
    )
    await task

    assert result.verdict == "ok_user_hub"
    assert result.user_hub_event_seen is True
    assert result.rest_found_order is None
    assert broker.calls == 0  # never had to poll REST


# ───────────────── REST-fallback path ─────────────────────────────


@pytest.mark.asyncio
async def test_returns_ok_rest_when_rest_finds_order_after_user_silence(monitor):
    m, _ = monitor
    broker = FakeBrokerAdapter()
    broker.responses = [
        [],  # first poll: order not here yet
        [{"id": "ABC123", "status": 1}],  # second poll: order present
    ]
    m.record_order_placed()  # but no record_user_event ever fires

    result = await verify_order_landed(
        order_id="ABC123",
        account_id="acct-1",
        broker_adapter=broker,
        monitor=m,
        strategy_name="test",
        symbol="MGC",
        soft_timeout_s=0.5,        # tight to keep test fast
        rest_poll_timeout_s=5.0,
        rest_poll_interval_s=0.1,
    )

    assert result.verdict == "ok_rest"
    assert result.user_hub_event_seen is False
    assert result.rest_found_order is True
    assert broker.calls >= 1


@pytest.mark.asyncio
async def test_returns_missing_when_rest_does_not_find_order(monitor, caplog):
    import logging
    caplog.set_level(logging.ERROR)
    m, _ = monitor
    broker = FakeBrokerAdapter()
    # All REST polls return empty.
    m.record_order_placed()

    result = await verify_order_landed(
        order_id="XYZ999",
        account_id="acct-1",
        broker_adapter=broker,
        monitor=m,
        strategy_name="test",
        symbol="MGC",
        soft_timeout_s=0.3,
        rest_poll_timeout_s=0.5,
        rest_poll_interval_s=0.1,
    )
    assert result.verdict == "missing"
    assert result.user_hub_event_seen is False
    assert result.rest_found_order is False

    # A loud ERROR log should have been emitted with the order id.
    err_msgs = [r.message for r in caplog.records if r.levelno == logging.ERROR]
    assert any("ORDER VERIFY FAILED" in m for m in err_msgs)
    assert any("XYZ999" in m for m in err_msgs)


# ───────────────── REST resilience ────────────────────────────────


@pytest.mark.asyncio
async def test_rest_exception_is_swallowed_and_retried(monitor):
    m, _ = monitor
    broker = FakeBrokerAdapter()
    broker.raise_next = RuntimeError("transient network error")
    broker.responses = [
        [{"id": "ABC123"}],  # second call succeeds
    ]
    m.record_order_placed()

    result = await verify_order_landed(
        order_id="ABC123",
        account_id="acct-1",
        broker_adapter=broker,
        monitor=m,
        strategy_name="test",
        symbol="MGC",
        soft_timeout_s=0.3,
        rest_poll_timeout_s=5.0,
        rest_poll_interval_s=0.1,
    )
    assert result.verdict == "ok_rest"
    assert broker.calls >= 2


# ───────────────── ID matching variants ───────────────────────────


@pytest.mark.asyncio
async def test_matches_orderid_camelcase_key(monitor):
    m, _ = monitor
    broker = FakeBrokerAdapter()
    broker.responses = [[{"orderId": "ABC123", "status": "Working"}]]
    m.record_order_placed()
    result = await verify_order_landed(
        order_id="ABC123",
        account_id="acct-1",
        broker_adapter=broker,
        monitor=m,
        strategy_name="test", symbol="MGC",
        soft_timeout_s=0.3, rest_poll_timeout_s=2.0, rest_poll_interval_s=0.1,
    )
    assert result.verdict == "ok_rest"


@pytest.mark.asyncio
async def test_empty_order_id_returns_missing_id(monitor):
    m, _ = monitor
    broker = FakeBrokerAdapter()
    result = await verify_order_landed(
        order_id="",
        account_id="acct-1",
        broker_adapter=broker,
        monitor=m,
        strategy_name="test", symbol="MGC",
        soft_timeout_s=0.1,
    )
    assert result.verdict == "missing_id"
    assert broker.calls == 0
