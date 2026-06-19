"""Unit tests for the SignalR reconnect weekend-survival logic (2026-06-15).

The original ``_handle_network_interruption_and_reconnect`` had a hard-coded
``max_attempts = 10`` with 30s-capped backoff — ~5 minutes total before the
connection died silently.  These tests pin the two-phase reconnect rework:

  * Fast phase: original 2/4/8/16/30s backoff for the first N attempts.
  * Extended phase: configurable slow polling (default 5 min) up to a
    much larger cap (default 1000 attempts ≈ 83 h).

Both phases honour env-tunable knobs:
  ``SIGNALR_FAST_RECONNECT_ATTEMPTS``,
  ``SIGNALR_MAX_RECONNECT_ATTEMPTS``,
  ``SIGNALR_EXTENDED_RECONNECT_DELAY_SEC``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import List
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.websocket_manager import WebSocketManager


def _build_manager(**overrides) -> WebSocketManager:
    """Construct a manager skipping __init__ — we only exercise the
    reconnect loop, so we don't need a real auth_manager / hub_url."""
    mgr = WebSocketManager.__new__(WebSocketManager)
    mgr._reconnecting = False
    mgr._connected = False
    mgr._hub = None
    mgr._lock = MagicMock()
    # Make the lock a no-op context manager.
    mgr._lock.__enter__ = MagicMock(return_value=None)
    mgr._lock.__exit__ = MagicMock(return_value=None)
    mgr.auth_manager = MagicMock()
    mgr.auth_manager.ensure_valid_token = AsyncMock()
    mgr.start = AsyncMock(return_value=False)
    mgr._resubscribe_all_symbols = AsyncMock()
    for k, v in overrides.items():
        setattr(mgr, k, v)
    return mgr


@pytest.mark.asyncio
async def test_stop_preserves_subscribed_symbols_for_resubscribe():
    """2026-06-17: stop() must not clear _subscribed_symbols."""
    mgr = _build_manager()
    mgr._subscribed_symbols = {"MNQ", "MGC"}
    mgr._hub = MagicMock()
    mgr._hub.stop = MagicMock()
    mgr._connect_ready = MagicMock()
    mgr._connect_ready.clear = MagicMock()
    mgr._pending_symbols = set()
    mgr._connected = True
    await mgr.stop()
    assert mgr._subscribed_symbols == {"MNQ", "MGC"}
    assert mgr._hub is None


@pytest.fixture(autouse=True)
def _patch_asyncio_sleep(monkeypatch):
    """Patch asyncio.sleep so the reconnect loop runs instantly.  The
    actual delay values are still passed through and we capture them on the
    manager via the ``sleep_calls`` attribute so tests can assert on them."""

    calls: List[float] = []

    async def _fast_sleep(d):
        calls.append(float(d))

    # Patch the module-level asyncio.sleep used by websocket_manager so the
    # tests don't sit through real backoff delays.
    monkeypatch.setattr(
        "core.websocket_manager.asyncio.sleep", _fast_sleep,
    )
    # Expose the call list for assertions.
    yield calls


class TestReconnectFastPhase:
    """First N attempts use the original 2/4/8/16/30s exponential backoff."""

    @pytest.mark.asyncio
    async def test_fast_phase_uses_exponential_backoff(
        self, monkeypatch, _patch_asyncio_sleep
    ):
        monkeypatch.setenv("SIGNALR_FAST_RECONNECT_ATTEMPTS", "5")
        monkeypatch.setenv("SIGNALR_MAX_RECONNECT_ATTEMPTS", "5")
        mgr = _build_manager()
        # Force start() to always fail so we exhaust the loop.
        mgr.start = AsyncMock(return_value=False)
        await mgr._handle_network_interruption_and_reconnect()

        # First 5 sleeps: 2, 4, 8, 16, 30 (capped at 30).
        assert _patch_asyncio_sleep[:5] == [2.0, 4.0, 8.0, 16.0, 30.0]

    @pytest.mark.asyncio
    async def test_fast_phase_success_reconnects_and_resubscribes(
        self, monkeypatch
    ):
        monkeypatch.setenv("SIGNALR_FAST_RECONNECT_ATTEMPTS", "10")
        monkeypatch.setenv("SIGNALR_MAX_RECONNECT_ATTEMPTS", "10")
        mgr = _build_manager()
        # Succeed on the 2nd attempt.
        mgr.start = AsyncMock(side_effect=[False, True])
        await mgr._handle_network_interruption_and_reconnect()

        assert mgr.start.await_count == 2
        mgr._resubscribe_all_symbols.assert_awaited_once()
        # _reconnecting flag reset.
        assert mgr._reconnecting is False


class TestReconnectExtendedPhase:
    """After the fast phase exhausts, switches to slow polling at the
    ``SIGNALR_EXTENDED_RECONNECT_DELAY_SEC`` cadence."""

    @pytest.mark.asyncio
    async def test_extended_phase_uses_configured_delay(
        self, monkeypatch, _patch_asyncio_sleep
    ):
        monkeypatch.setenv("SIGNALR_FAST_RECONNECT_ATTEMPTS", "2")
        monkeypatch.setenv("SIGNALR_MAX_RECONNECT_ATTEMPTS", "5")
        monkeypatch.setenv("SIGNALR_EXTENDED_RECONNECT_DELAY_SEC", "120")
        mgr = _build_manager()
        mgr.start = AsyncMock(return_value=False)
        await mgr._handle_network_interruption_and_reconnect()

        # First 2 sleeps are the fast phase (2s, 4s).
        # The remaining 3 sleeps must use the extended delay (120s each).
        assert _patch_asyncio_sleep[:2] == [2.0, 4.0]
        assert _patch_asyncio_sleep[2:5] == [120.0, 120.0, 120.0]

    @pytest.mark.asyncio
    async def test_extended_phase_announces_transition_at_warning(
        self, monkeypatch, caplog
    ):
        monkeypatch.setenv("SIGNALR_FAST_RECONNECT_ATTEMPTS", "2")
        monkeypatch.setenv("SIGNALR_MAX_RECONNECT_ATTEMPTS", "4")
        monkeypatch.setenv("SIGNALR_EXTENDED_RECONNECT_DELAY_SEC", "60")
        mgr = _build_manager()
        mgr.start = AsyncMock(return_value=False)
        with caplog.at_level(logging.WARNING, logger="core.websocket_manager"):
            await mgr._handle_network_interruption_and_reconnect()

        # The announcement banner fires exactly once.
        announcements = [
            r for r in caplog.records
            if "extended retry mode" in r.message and r.levelno >= logging.WARNING
        ]
        assert len(announcements) == 1

    @pytest.mark.asyncio
    async def test_extended_phase_logs_return_to_normal_loudly(
        self, monkeypatch, caplog
    ):
        """When a reconnect SUCCEEDS in the extended phase, the operator
        wants a loud WARNING-level confirmation."""
        monkeypatch.setenv("SIGNALR_FAST_RECONNECT_ATTEMPTS", "2")
        monkeypatch.setenv("SIGNALR_MAX_RECONNECT_ATTEMPTS", "5")
        monkeypatch.setenv("SIGNALR_EXTENDED_RECONNECT_DELAY_SEC", "60")
        mgr = _build_manager()
        # Fail through both fast attempts, succeed on the 4th attempt
        # (i.e. extended attempt #2).
        mgr.start = AsyncMock(side_effect=[False, False, False, True])
        with caplog.at_level(logging.WARNING, logger="core.websocket_manager"):
            await mgr._handle_network_interruption_and_reconnect()

        recovery = [
            r for r in caplog.records
            if "after extended outage" in r.message
        ]
        assert len(recovery) == 1
        assert recovery[0].levelno >= logging.WARNING


class TestReconnectConfigClamping:
    """Sanity guards: env-vars set to nonsense or inconsistent values must
    still produce a usable configuration, never a crash."""

    @pytest.mark.asyncio
    async def test_garbage_env_values_fall_back_to_defaults(
        self, monkeypatch, _patch_asyncio_sleep
    ):
        monkeypatch.setenv("SIGNALR_FAST_RECONNECT_ATTEMPTS", "not_a_number")
        monkeypatch.setenv("SIGNALR_MAX_RECONNECT_ATTEMPTS", "")
        monkeypatch.setenv("SIGNALR_EXTENDED_RECONNECT_DELAY_SEC", "garbage")
        mgr = _build_manager()
        mgr.start = AsyncMock(return_value=True)  # immediate success
        # Should not raise, should reconnect on attempt 1.
        await mgr._handle_network_interruption_and_reconnect()
        assert mgr.start.await_count == 1

    @pytest.mark.asyncio
    async def test_max_clamped_above_fast_phase(
        self, monkeypatch, _patch_asyncio_sleep
    ):
        """If the operator sets max < fast, we honour the larger of the
        two — never silently retry fewer times than the fast budget."""
        monkeypatch.setenv("SIGNALR_FAST_RECONNECT_ATTEMPTS", "10")
        monkeypatch.setenv("SIGNALR_MAX_RECONNECT_ATTEMPTS", "3")
        mgr = _build_manager()
        mgr.start = AsyncMock(return_value=False)
        await mgr._handle_network_interruption_and_reconnect()
        # 10 fast-phase sleeps must have fired (max effectively bumped to fast).
        assert len(_patch_asyncio_sleep) == 10

    @pytest.mark.asyncio
    async def test_reconnecting_flag_prevents_double_entry(self):
        mgr = _build_manager()
        mgr._reconnecting = True
        await mgr._handle_network_interruption_and_reconnect()
        # start() never called because the flag short-circuited.
        mgr.start.assert_not_called()
