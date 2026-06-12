"""Pinning tests for ``core.data_feed_watchdog``.

The watchdog's job is to detect a "zombie" SignalR connection (no
ticks flowing despite ``_connected=True``) and force a stop+start
cycle to bring it back to life.  These tests pin the policy without
requiring real SignalR plumbing.
"""

from __future__ import annotations

import asyncio
from typing import List

import pytest

from core.data_feed_health import DataFeedHealthMonitor
from core.data_feed_watchdog import DataFeedWatchdog
from core.working_order_registry import WorkingOrderRegistry


class FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self._t = start

    def __call__(self) -> float:
        return self._t

    def advance(self, dt: float) -> None:
        self._t += dt


class FakeMarketHub:
    """Minimal stand-in with stop/start/resubscribe + a flag."""
    def __init__(self) -> None:
        self.stops: int = 0
        self.starts: int = 0
        self.resubscribes: int = 0
        self._subscribed_symbols = {"MGC", "MNQ"}
        self._start_succeeds = True

    async def stop(self) -> None:
        self.stops += 1

    async def start(self) -> bool:
        self.starts += 1
        return self._start_succeeds

    async def _resubscribe_all_symbols(self) -> None:
        self.resubscribes += 1


@pytest.fixture
def setup():
    clk = FakeClock(start=1000.0)
    monitor = DataFeedHealthMonitor(
        market_hub_max_silence_s=60.0,
        user_hub_max_silence_after_order_s=30.0,
        startup_grace_s=10.0,
        clock_mono=clk,
    )
    hub = FakeMarketHub()
    wd = DataFeedWatchdog(
        monitor=monitor,
        market_hub_manager=hub,
        subscribed_symbols_getter=lambda: list(hub._subscribed_symbols),
        poll_interval_s=0.01,           # fast for tests
        reconnect_cooldown_s=60.0,
        max_silence_s=60.0,
        clock_mono=clk,                 # shared fake clock for determinism
    )
    return monitor, hub, wd, clk


# ───────────────── pure _tick() behaviour ──────────────────────────


@pytest.mark.asyncio
async def test_tick_noop_during_startup_grace(setup, monkeypatch):
    monitor, hub, wd, _ = setup
    # Force "within market hours" so the only thing protecting us is grace.
    monkeypatch.setattr(
        "core.data_feed_watchdog.is_within_market_hours", lambda: True
    )
    await wd._tick()
    assert hub.stops == 0
    assert hub.starts == 0


@pytest.mark.asyncio
async def test_tick_noop_outside_market_hours(setup, monkeypatch):
    monitor, hub, wd, clk = setup
    # Skip grace.
    clk.advance(20.0)
    # Force "outside market hours".
    monkeypatch.setattr(
        "core.data_feed_watchdog.is_within_market_hours", lambda: False
    )
    await wd._tick()
    assert hub.stops == 0


@pytest.mark.asyncio
async def test_tick_reconnects_when_market_hub_zombied(setup, monkeypatch):
    monitor, hub, wd, clk = setup
    monkeypatch.setattr(
        "core.data_feed_watchdog.is_within_market_hours", lambda: True
    )
    # Get out of grace.
    clk.advance(20.0)
    # Simulate one normal tick, then go silent past threshold.
    monitor.record_market_tick("MGC")
    monitor.record_market_tick("MNQ")
    clk.advance(75.0)  # >60 s silence

    await wd._tick()

    assert hub.stops == 1
    assert hub.starts == 1
    assert hub.resubscribes == 1
    assert wd.reconnects_total == 1


@pytest.mark.asyncio
async def test_tick_does_not_reconnect_during_cooldown(setup, monkeypatch):
    monitor, hub, wd, clk = setup
    monkeypatch.setattr(
        "core.data_feed_watchdog.is_within_market_hours", lambda: True
    )
    clk.advance(20.0)
    monitor.record_market_tick("MGC")
    monitor.record_market_tick("MNQ")
    clk.advance(75.0)

    # First reconnect fires (initial backoff is 60 s).
    await wd._tick()
    assert wd.reconnects_total == 1

    # The watchdog has reset the monitor's grace and recorded its
    # reconnect time.  Now advance the silence threshold AGAIN but stay
    # INSIDE the cooldown (cooldown is initial 60 s; backoff doubled to
    # 120 s after the first reconnect).  No second reconnect should fire.
    monitor.record_market_tick("MGC")
    monitor.record_market_tick("MNQ")
    clk.advance(11.0)         # past grace=10 s reset
    clk.advance(75.0)         # past silence threshold
    # Only 86 s elapsed since last reconnect; backoff is 120 s.  Cool-down still active.
    pre = wd.reconnects_total
    await wd._tick()
    assert wd.reconnects_total == pre

    # Now advance past the doubled backoff — second reconnect can fire.
    clk.advance(60.0)
    await wd._tick()
    assert wd.reconnects_total == pre + 1


@pytest.mark.asyncio
async def test_tick_resets_backoff_when_feed_healthy(setup, monkeypatch):
    monitor, hub, wd, clk = setup
    monkeypatch.setattr(
        "core.data_feed_watchdog.is_within_market_hours", lambda: True
    )
    clk.advance(20.0)
    monitor.record_market_tick("MGC")
    monitor.record_market_tick("MNQ")
    # Manually inflate backoff to non-default value.
    wd._backoff_s = 240.0  # type: ignore[attr-defined]
    # Healthy feed.
    await wd._tick()
    # No reconnect AND backoff reset to initial.
    assert hub.stops == 0
    assert wd._backoff_s == 60.0  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_subscribed_symbols_getter_fallback_used(monkeypatch, setup):
    monitor, hub, _, clk = setup
    monkeypatch.setattr(
        "core.data_feed_watchdog.is_within_market_hours", lambda: True
    )
    # Build a watchdog WITHOUT explicit getter; should peek at hub._subscribed_symbols.
    wd = DataFeedWatchdog(
        monitor=monitor,
        market_hub_manager=hub,
        poll_interval_s=0.01,
        reconnect_cooldown_s=60.0,
        max_silence_s=60.0,
    )
    clk.advance(20.0)
    monitor.record_market_tick("MGC")
    monitor.record_market_tick("MNQ")
    clk.advance(75.0)
    await wd._tick()
    assert hub.stops == 1


@pytest.mark.asyncio
async def test_lifecycle_start_stop(setup, monkeypatch):
    monitor, hub, wd, clk = setup
    monkeypatch.setattr(
        "core.data_feed_watchdog.is_within_market_hours", lambda: False
    )
    wd.start()
    # Start again is idempotent.
    wd.start()
    # Let the event loop schedule the task at least once.
    await asyncio.sleep(0.05)
    await wd.stop()
    # Calling stop twice doesn't blow up.
    await wd.stop()


@pytest.mark.asyncio
async def test_never_ticked_symbol_triggers_reconnect_after_grace(setup, monkeypatch):
    monitor, hub, wd, clk = setup
    monkeypatch.setattr(
        "core.data_feed_watchdog.is_within_market_hours", lambda: True
    )
    # No symbol has ever ticked.  But fallback subscribed set has MGC + MNQ.
    clk.advance(20.0)  # past grace
    await wd._tick()
    # Never-ticked symbols also count as zombied → reconnect fires.
    assert hub.stops == 1


# ───────────── cancel-on-staleness (working orders) ─────────────────


class _FakeBroker:
    def __init__(self) -> None:
        self.cancels: list[dict] = []

    async def cancel_order(self, order_id, account_id):
        self.cancels.append({"order_id": str(order_id), "account_id": str(account_id)})
        return {"success": True, "orderId": str(order_id)}


@pytest.mark.asyncio
async def test_watchdog_cancels_working_orders_before_reconnect(monkeypatch):
    monkeypatch.setattr(
        "core.data_feed_watchdog.is_within_market_hours", lambda: True
    )
    clk = FakeClock(start=1000.0)
    monitor = DataFeedHealthMonitor(
        market_hub_max_silence_s=60.0,
        user_hub_max_silence_after_order_s=30.0,
        startup_grace_s=10.0,
        clock_mono=clk,
    )
    hub = FakeMarketHub()
    registry = WorkingOrderRegistry()
    registry.register(
        order_id="ENTRY-1", account_id="acc-1", symbol="MGC",
        side="BUY", strategy_name="mrr", oco_sibling_ids=["SL-1", "TP-1"],
    )
    registry.register(
        order_id="ENTRY-2", account_id="acc-1", symbol="MNQ",
        side="SELL", strategy_name="mrr", oco_sibling_ids=[],
    )
    broker = _FakeBroker()
    wd = DataFeedWatchdog(
        monitor=monitor,
        market_hub_manager=hub,
        broker_adapter=broker,
        working_order_registry=registry,
        subscribed_symbols_getter=lambda: list(hub._subscribed_symbols),
        poll_interval_s=0.01,
        reconnect_cooldown_s=60.0,
        max_silence_s=60.0,
        clock_mono=clk,
    )
    clk.advance(20.0)  # past grace
    monitor.record_market_tick("MGC")
    monitor.record_market_tick("MNQ")
    clk.advance(75.0)  # zombie threshold exceeded
    await wd._tick()

    # Both entries + the one OCO sibling pair = 3 cancel requests.
    cancelled_ids = {c["order_id"] for c in broker.cancels}
    assert cancelled_ids == {"ENTRY-1", "ENTRY-2", "SL-1", "TP-1"}
    # And the reconnect still happened.
    assert hub.stops == 1
    assert hub.starts == 1
    # Registry is empty after cancellation.
    assert registry.working_count() == 0


@pytest.mark.asyncio
async def test_watchdog_without_registry_does_not_cancel(monkeypatch):
    """If no registry is wired, the watchdog must still reconnect — it
    just skips the cancel-on-staleness step.  Defends against deployments
    that haven't yet wired the registry."""
    monkeypatch.setattr(
        "core.data_feed_watchdog.is_within_market_hours", lambda: True
    )
    clk = FakeClock(start=1000.0)
    monitor = DataFeedHealthMonitor(
        market_hub_max_silence_s=60.0,
        user_hub_max_silence_after_order_s=30.0,
        startup_grace_s=10.0,
        clock_mono=clk,
    )
    hub = FakeMarketHub()
    broker = _FakeBroker()
    wd = DataFeedWatchdog(
        monitor=monitor,
        market_hub_manager=hub,
        broker_adapter=broker,
        working_order_registry=None,  # ← intentionally omitted
        subscribed_symbols_getter=lambda: list(hub._subscribed_symbols),
        poll_interval_s=0.01,
        reconnect_cooldown_s=60.0,
        max_silence_s=60.0,
        clock_mono=clk,
    )
    clk.advance(20.0)
    monitor.record_market_tick("MGC")
    monitor.record_market_tick("MNQ")
    clk.advance(75.0)
    await wd._tick()

    assert broker.cancels == []        # nothing to cancel
    assert hub.stops == 1              # reconnect still fired
