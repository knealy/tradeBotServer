"""SignalR zombie-connection watchdog.

The 2026-06-11 MGC incident: the bot ran for 5 hours with both SignalR
hubs in a "zombie" state (connection looked alive, ZERO ticks/events
flowing).  No ``reconnect`` or ``on_close`` ever fired so the hub managers'
built-in recovery never kicked in.

This watchdog is a SEPARATE asyncio task that polls the
``DataFeedHealthMonitor`` every N seconds and FORCES a stop+start cycle
on the affected hub when activity has been silent past its threshold —
even though the SignalR library still thinks the connection is fine.

Design:

  * Single ``run()`` coroutine, ``asyncio.create_task`` from trading_bot startup.
  * Polls at ``poll_interval_s`` (default 30 s) — cheap, just inspects
    in-memory timestamps.
  * Per-symbol Market Hub age check: if any subscribed symbol exceeds
    ``MARKET_HUB_MAX_SILENCE_SECONDS`` AND we're inside market hours
    (``is_within_market_hours()``), trigger ``stop()`` + ``start()`` +
    resubscribe.
  * Backoff on repeated failures: 60 s → 120 s → 240 s → 480 s.
  * After every successful reconnect, ``monitor.reset_clock_for_grace()``
    so the next health window starts clean.

NOT YET handled (filed as separate TODOs):
  - User Hub force-reconnect (rarer; the order-placement path will
    surface User-Hub-zombie via ``is_safe_to_trade`` *before* placing
    a new order).
  - Cross-correlated reconnect (if Market Hub goes zombie, User Hub
    likely also did; reconnect both together).  The current impl
    reconnects them independently as zombies are detected.

The watchdog is observer-of-observer — it knows nothing about strategy
logic.  All trading decisions still flow through ``is_safe_to_trade``;
the watchdog merely repairs the underlying transport so future calls
to ``is_safe_to_trade`` get a clean answer instead of "always zombie".
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timezone
from typing import Callable, Optional

from core.data_feed_health import (
    DataFeedHealthMonitor,
    MARKET_HUB_MAX_SILENCE_SECONDS,
    is_within_market_hours,
)

logger = logging.getLogger(__name__)


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


WATCHDOG_POLL_INTERVAL_SECONDS: float = _env_float(
    "DATA_FEED_WATCHDOG_POLL_S", 30.0
)
# How long the post-reconnect cool-down is before another reconnect can fire.
# Prevents thrashing if the hub keeps coming up dead.
WATCHDOG_RECONNECT_COOLDOWN_SECONDS: float = _env_float(
    "DATA_FEED_WATCHDOG_RECONNECT_COOLDOWN_S", 90.0
)
# Initial backoff after a reconnect that ALSO came up dead.
WATCHDOG_BACKOFF_INITIAL_SECONDS: float = _env_float(
    "DATA_FEED_WATCHDOG_BACKOFF_INITIAL_S", 60.0
)


class DataFeedWatchdog:
    """Async task that auto-reconnects zombied SignalR hubs."""

    def __init__(
        self,
        monitor: DataFeedHealthMonitor,
        *,
        market_hub_manager,
        user_hub_manager=None,
        subscribed_symbols_getter=None,
        broker_adapter=None,
        working_order_registry=None,
        poll_interval_s: float = WATCHDOG_POLL_INTERVAL_SECONDS,
        reconnect_cooldown_s: float = WATCHDOG_RECONNECT_COOLDOWN_SECONDS,
        max_silence_s: float = MARKET_HUB_MAX_SILENCE_SECONDS,
        clock_mono: Callable[[], float] = time.monotonic,
    ) -> None:
        """Args:
            monitor: the singleton DataFeedHealthMonitor.
            market_hub_manager: the ``WebSocketManager`` instance.
            user_hub_manager: the ``UserHubManager`` instance (optional).
            subscribed_symbols_getter: callable -> iterable of symbol
                strings currently subscribed.  Used to know which symbols
                to check for zombie state.  When None, falls back to
                ``market_hub_manager._subscribed_symbols`` (private but
                stable in the existing codebase).
            poll_interval_s: seconds between health polls.
            reconnect_cooldown_s: min seconds between successive reconnect
                attempts on the same hub.
            max_silence_s: trigger threshold (matches monitor's).
        """
        self.monitor = monitor
        self.market_hub = market_hub_manager
        self.user_hub = user_hub_manager
        self.broker_adapter = broker_adapter
        self.working_order_registry = working_order_registry
        self._poll_interval = float(poll_interval_s)
        self._cooldown = float(reconnect_cooldown_s)
        self._max_silence = float(max_silence_s)
        self._subscribed_getter = subscribed_symbols_getter
        self._mono = clock_mono

        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._last_reconnect_at_mono: float = 0.0
        self._backoff_s: float = WATCHDOG_BACKOFF_INITIAL_SECONDS
        self._reconnects_total: int = 0

    # ─── lifecycle ───────────────────────────────────────────────────

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run(), name="data_feed_watchdog")
        logger.info(
            "🛡️  DataFeedWatchdog started (poll=%ss, max_silence=%ss, cooldown=%ss)",
            self._poll_interval, self._max_silence, self._cooldown,
        )

    async def stop(self) -> None:
        self._running = False
        t = self._task
        self._task = None
        if t and not t.done():
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        logger.info(
            "🛡️  DataFeedWatchdog stopped (%d reconnect(s) fired this run)",
            self._reconnects_total,
        )

    # ─── main loop ───────────────────────────────────────────────────

    async def _run(self) -> None:
        try:
            while self._running:
                try:
                    await self._tick()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # never let an iteration kill the loop
                    logger.error("watchdog tick error (continuing): %s", exc, exc_info=True)
                await asyncio.sleep(self._poll_interval)
        except asyncio.CancelledError:
            pass

    async def _tick(self) -> None:
        """One health-poll cycle."""
        # Outside trading hours → no expectation of data flow.
        if not is_within_market_hours():
            return

        # Still in the monitor's startup grace window → wait.
        if self.monitor.in_startup_grace():
            return

        # Cool-down: don't fire reconnects too quickly.
        now_m = self._mono()
        since_last_reconnect = now_m - self._last_reconnect_at_mono
        if since_last_reconnect < self._backoff_s:
            return

        symbols = self._current_symbols()
        if not symbols:
            return  # nothing subscribed yet — nothing to check

        # Find the WORST-aged symbol; reconnect once for all of them.
        worst_age: Optional[float] = None
        worst_symbol: Optional[str] = None
        for sym in symbols:
            age = self.monitor.market_hub_age_seconds(sym)
            if age is None:
                # No tick ever seen for this symbol; treat as max silence.
                worst_age = max(worst_age or 0.0, self._max_silence + 1.0)
                worst_symbol = sym
                continue
            if worst_age is None or age > worst_age:
                worst_age = age
                worst_symbol = sym

        if worst_age is None or worst_age <= self._max_silence:
            # All symbols healthy → reset backoff to initial.
            self._backoff_s = WATCHDOG_BACKOFF_INITIAL_SECONDS
            return

        # ZOMBIE DETECTED — force reconnect.
        logger.warning(
            "🧟 Market Hub ZOMBIE: %s silent for %.1fs (threshold %.0fs) — "
            "forcing reconnect (zombie reconnect #%d, backoff=%.0fs)",
            worst_symbol, worst_age, self._max_silence,
            self._reconnects_total + 1, self._backoff_s,
        )

        # CANCEL-ON-STALENESS: before the reconnect, eagerly cancel every
        # placed-but-unfilled entry order so it can't fill silently while
        # the bot is reconnecting (the 2026-06-11 failure mode — see
        # core/working_order_registry.py).
        await self._cancel_working_orders_due_to_zombie(worst_symbol or "", worst_age)

        await self._force_reconnect_market_hub()
        self._last_reconnect_at_mono = self._mono()
        self._reconnects_total += 1
        # Bump backoff so repeated-failure scenarios don't thrash.
        self._backoff_s = min(self._backoff_s * 2.0, 480.0)
        # Reset monitor's grace so the next check waits for genuine data.
        self.monitor.reset_clock_for_grace()

    # ─── reconnection ────────────────────────────────────────────────

    async def _cancel_working_orders_due_to_zombie(
        self, worst_symbol: str, worst_age: float,
    ) -> int:
        """Cancel every entry order currently sitting on the broker.

        Called immediately BEFORE forcing the SignalR reconnect so any
        stop-entry that would otherwise fill blind while we're cycling
        the connection gets pulled.  Returns the number of cancel
        requests issued (0 if no registry / adapter wired).
        """
        if self.working_order_registry is None or self.broker_adapter is None:
            return 0
        try:
            from core.working_order_registry import cancel_all_working_orders
            count = await cancel_all_working_orders(
                self.working_order_registry,
                self.broker_adapter,
                reason=(
                    f"zombie SignalR detected — {worst_symbol or 'unknown'} "
                    f"silent for {worst_age:.1f}s; refusing to leave working "
                    f"entries on the broker while reconnecting"
                ),
                include_siblings=True,
            )
            if count:
                logger.warning(
                    "🛑 watchdog issued %d cancel request(s) prior to reconnect",
                    count,
                )
            return count
        except Exception as exc:
            logger.error(
                "watchdog: cancel-on-staleness raised %s (continuing reconnect): %s",
                type(exc).__name__, exc,
            )
            return 0

    async def _force_reconnect_market_hub(self) -> None:
        try:
            await self.market_hub.stop()
        except Exception as exc:
            logger.warning("watchdog: market_hub.stop() raised: %s", exc)
        # Brief breath so SignalR really tears down.
        await asyncio.sleep(0.5)
        try:
            ok = await self.market_hub.start()
            if ok:
                logger.info("🛡️  Market Hub reconnect OK (re-subscribing to symbols)")
                # WebSocketManager has its own resubscribe path — call if present.
                resub = getattr(self.market_hub, "_resubscribe_all_symbols", None)
                if resub is not None:
                    await resub()
            else:
                logger.error("🛡️  Market Hub reconnect returned False")
        except Exception as exc:
            logger.error("🛡️  Market Hub reconnect raised: %s", exc, exc_info=True)

    # ─── helpers ─────────────────────────────────────────────────────

    def _current_symbols(self):
        if self._subscribed_getter is not None:
            try:
                return list(self._subscribed_getter())
            except Exception:
                return []
        # Fallback: peek into WebSocketManager's known-stable attribute.
        subs = getattr(self.market_hub, "_subscribed_symbols", None)
        if subs is None:
            return []
        try:
            return list(subs)
        except Exception:
            return []

    # ─── diagnostics ─────────────────────────────────────────────────

    @property
    def reconnects_total(self) -> int:
        return self._reconnects_total
