"""Pinning tests for ``core.working_order_registry``.

This registry is the bookkeeping layer for cancel-on-staleness.  These
tests pin the contract a strategy + User Hub handler + watchdog all
rely on.
"""

from __future__ import annotations

import asyncio
from typing import List

import pytest

from core.working_order_registry import (
    WorkingOrder,
    WorkingOrderRegistry,
    cancel_all_working_orders,
    get_registry,
    reset_registry_for_tests,
    set_registry,
)


class FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self._t = start

    def __call__(self) -> float:
        return self._t

    def advance(self, dt: float) -> None:
        self._t += dt


@pytest.fixture(autouse=True)
def _isolate_singleton():
    reset_registry_for_tests()
    yield
    reset_registry_for_tests()


@pytest.fixture
def reg():
    clk = FakeClock(1000.0)
    wall = FakeClock(1700000000.0)
    return WorkingOrderRegistry(clock_mono=clk, clock_wall=wall), clk


# ───────────────── basic CRUD ───────────────────────────────────────


def test_register_and_get(reg):
    r, _ = reg
    r.register(
        order_id="A1", account_id="acc-1", symbol="MGC",
        side="BUY", strategy_name="mrr", oco_sibling_ids=["A2", "A3"],
    )
    wo = r.get("A1")
    assert wo is not None
    assert wo.symbol == "MGC"
    assert wo.side == "BUY"
    assert wo.strategy_name == "mrr"
    assert wo.oco_sibling_ids == ["A2", "A3"]
    assert wo.last_status == "Working"


def test_register_empty_id_is_noop(reg):
    r, _ = reg
    r.register(order_id="", account_id="x", symbol="MGC", side="BUY", strategy_name="s")
    assert r.working_count() == 0


def test_register_normalizes_symbol_and_side(reg):
    r, _ = reg
    r.register(order_id="A1", account_id="acc", symbol="mgc", side="buy", strategy_name="s")
    wo = r.get("A1")
    assert wo.symbol == "MGC"
    assert wo.side == "BUY"


def test_snapshot_returns_independent_list(reg):
    r, _ = reg
    r.register(order_id="A1", account_id="acc", symbol="MGC", side="BUY", strategy_name="s")
    r.register(order_id="A2", account_id="acc", symbol="MNQ", side="SELL", strategy_name="s")
    snap = r.snapshot()
    assert len(snap) == 2
    # Mutating snap doesn't affect registry.
    snap.clear()
    assert r.working_count() == 2


def test_working_for_symbol(reg):
    r, _ = reg
    r.register(order_id="A1", account_id="a", symbol="MGC", side="BUY", strategy_name="s")
    r.register(order_id="A2", account_id="a", symbol="MNQ", side="BUY", strategy_name="s")
    r.register(order_id="A3", account_id="a", symbol="MGC", side="SELL", strategy_name="s")
    mgc = r.working_for_symbol("mgc")
    assert {wo.order_id for wo in mgc} == {"A1", "A3"}


def test_working_age_seconds(reg):
    r, clk = reg
    r.register(order_id="A1", account_id="a", symbol="MGC", side="BUY", strategy_name="s")
    assert r.working_age_seconds("A1") == pytest.approx(0.0)
    clk.advance(45.0)
    assert r.working_age_seconds("A1") == pytest.approx(45.0)
    assert r.working_age_seconds("Z9") is None


# ───────────────── status transitions ───────────────────────────────


@pytest.mark.parametrize("terminal_status", ["Filled", 2, "Cancelled", 3, "Rejected", 4, "Expired", 5])
def test_mark_status_terminal_unregisters(reg, terminal_status):
    r, _ = reg
    r.register(order_id="A1", account_id="a", symbol="MGC", side="BUY", strategy_name="s")
    assert r.working_count() == 1
    r.mark_status("A1", terminal_status)
    assert r.working_count() == 0
    assert r.get("A1") is None


def test_mark_status_non_terminal_keeps_entry(reg):
    r, _ = reg
    r.register(order_id="A1", account_id="a", symbol="MGC", side="BUY", strategy_name="s")
    r.mark_status("A1", "Working")
    r.mark_status("A1", "PendingNew")
    assert r.working_count() == 1
    assert r.get("A1").last_status == "PendingNew"


def test_mark_status_unknown_id_is_noop(reg):
    r, _ = reg
    r.mark_status("ghost", "Filled")  # must not raise


def test_unregister_is_idempotent(reg):
    r, _ = reg
    r.register(order_id="A1", account_id="a", symbol="MGC", side="BUY", strategy_name="s")
    r.unregister("A1")
    r.unregister("A1")  # second call OK
    assert r.working_count() == 0


# ───────────────── singleton wiring ─────────────────────────────────


def test_singleton_round_trip():
    r1 = get_registry()
    r2 = get_registry()
    assert r1 is r2


def test_set_and_reset_singleton():
    custom = WorkingOrderRegistry()
    set_registry(custom)
    assert get_registry() is custom
    reset_registry_for_tests()
    assert get_registry() is not custom


# ───────────────── cancel_all_working_orders ────────────────────────


class FakeBroker:
    def __init__(self) -> None:
        self.cancels: List[dict] = []
        self.raise_on_id: set = set()
        self.success_for_id: dict = {}

    async def cancel_order(self, order_id, account_id):
        self.cancels.append({"order_id": str(order_id), "account_id": str(account_id)})
        if order_id in self.raise_on_id:
            raise RuntimeError("broker err")
        success = self.success_for_id.get(order_id, True)
        return {"success": success, "orderId": str(order_id)}


@pytest.mark.asyncio
async def test_cancel_all_working_orders_cancels_entries_and_siblings(reg):
    r, _ = reg
    r.register(
        order_id="P1", account_id="a", symbol="MGC", side="BUY",
        strategy_name="mrr", oco_sibling_ids=["S1", "S2"],
    )
    r.register(
        order_id="P2", account_id="a", symbol="MNQ", side="SELL",
        strategy_name="mrr", oco_sibling_ids=["S3"],
    )
    broker = FakeBroker()
    n = await cancel_all_working_orders(r, broker, reason="test")
    # 2 entries + 3 siblings = 5 cancels
    assert n == 5
    ids = [c["order_id"] for c in broker.cancels]
    assert set(ids) == {"P1", "P2", "S1", "S2", "S3"}
    # Both entries marked as Cancelled in the registry (and dropped).
    assert r.working_count() == 0


@pytest.mark.asyncio
async def test_cancel_all_working_orders_skips_siblings_when_disabled(reg):
    r, _ = reg
    r.register(
        order_id="P1", account_id="a", symbol="MGC", side="BUY",
        strategy_name="s", oco_sibling_ids=["S1", "S2"],
    )
    broker = FakeBroker()
    n = await cancel_all_working_orders(r, broker, reason="test", include_siblings=False)
    assert n == 1
    assert [c["order_id"] for c in broker.cancels] == ["P1"]


@pytest.mark.asyncio
async def test_cancel_all_working_orders_handles_broker_exception(reg, caplog):
    import logging
    caplog.set_level(logging.ERROR)
    r, _ = reg
    r.register(
        order_id="P1", account_id="a", symbol="MGC", side="BUY",
        strategy_name="s", oco_sibling_ids=[],
    )
    r.register(
        order_id="P2", account_id="a", symbol="MNQ", side="BUY",
        strategy_name="s", oco_sibling_ids=[],
    )
    broker = FakeBroker()
    broker.raise_on_id = {"P1"}
    n = await cancel_all_working_orders(r, broker, reason="test")
    # P1 attempted (raises), P2 succeeds -> 2 total attempts.
    assert n == 2
    # Error logged for the failure.
    assert any("cancel of entry order P1" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_cancel_all_working_orders_with_empty_registry_is_noop(reg):
    r, _ = reg
    broker = FakeBroker()
    n = await cancel_all_working_orders(r, broker, reason="test")
    assert n == 0
    assert broker.cancels == []
