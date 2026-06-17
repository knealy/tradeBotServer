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

# Cancel working bracket orders before a zombie reconnect?  Default OFF
# (2026-06-17 operator policy): range-breakout strategies (MRR, ORB) place
# stop-entry brackets that should stay on the broker until filled or
# session-end flatten — NOT be pulled because local SignalR hiccuped.
# Set ``DATA_FEED_CANCEL_ON_STALENESS=true`` for strategies that need the
# 2026-06-11 blind-fill protection (continuous monitoring styles).
def _cancel_on_staleness_enabled() -> bool:
    return os.getenv(
        "DATA_FEED_CANCEL_ON_STALENESS", "false",
    ).strip().lower() in ("true", "1", "yes", "on")


def _discord_feed_alerts_enabled() -> bool:
    return os.getenv(
        "DATA_FEED_DISCORD_ALERTS", "true",
    ).strip().lower() in ("true", "1", "yes", "on")


DATA_FEED_DISCORD_ALERT_COOLDOWN_S: float = _env_float(
    "DATA_FEED_DISCORD_ALERT_COOLDOWN_S", 900.0
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
        discord_notifier=None,
        account_name_getter: Optional[Callable[[], str]] = None,
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
        self.discord_notifier = discord_notifier
        self._account_name_getter = account_name_getter
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
        self._feed_alert_active: bool = False
        self._last_discord_alert_mono: float = 0.0

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

        # Find symbols where BOTH quote and bar paths are stale.
        worst_age: Optional[float] = None
        worst_symbol: Optional[str] = None
        for sym in symbols:
            if not self.monitor.symbol_both_paths_stale(sym, self._max_silence):
                continue
            qa = self.monitor.market_hub_age_seconds(sym)
            ba = self.monitor.bar_activity_age_seconds(sym)
            age = max(
                qa if qa is not None else self._max_silence + 1.0,
                ba if ba is not None else self._max_silence + 1.0,
            )
            if worst_age is None or age > worst_age:
                worst_age = age
                worst_symbol = sym

        if worst_age is None or worst_age <= self._max_silence:
            # All symbols healthy → reset backoff to initial.
            self._backoff_s = WATCHDOG_BACKOFF_INITIAL_SECONDS
            if self._feed_alert_active:
                await self._discord_feed_recovered(worst_symbol or "")
                self._feed_alert_active = False
            return

        # ZOMBIE DETECTED — force reconnect.
        logger.warning(
            "🧟 Market Hub ZOMBIE: %s silent for %.1fs (threshold %.0fs) — "
            "forcing reconnect (zombie reconnect #%d, backoff=%.0fs)",
            worst_symbol, worst_age, self._max_silence,
            self._reconnects_total + 1, self._backoff_s,
        )

        await self._discord_feed_down(worst_symbol or "", worst_age)
        self._feed_alert_active = True

        # CANCEL-ON-STALENESS (opt-in): before reconnect, optionally cancel
        # working entries.  OFF by default — range-breakout brackets should
        # stay on the broker until fill or session-end (operator 2026-06-17).
        if _cancel_on_staleness_enabled():
            await self._cancel_working_orders_due_to_zombie(worst_symbol or "", worst_age)
        else:
            logger.info(
                "watchdog: zombie reconnect for %s (%.0fs) — "
                "DATA_FEED_CANCEL_ON_STALENESS=false, leaving working "
                "brackets on the broker",
                worst_symbol, worst_age,
            )

        await self._force_reconnect_market_hub()
        self._last_reconnect_at_mono = self._mono()
        self._reconnects_total += 1
        # Bump backoff so repeated-failure scenarios don't thrash.
        self._backoff_s = min(self._backoff_s * 2.0, 480.0)
        # Reset monitor's grace so the next check waits for genuine data.
        self.monitor.reset_clock_for_grace()

    # ─── reconnection ────────────────────────────────────────────────

    def _account_name(self) -> str:
        if self._account_name_getter is None:
            return ""
        try:
            return str(self._account_name_getter() or "")
        except Exception:
            return ""

    async def _discord_feed_down(self, symbol: str, silence_s: float) -> None:
        if not _discord_feed_alerts_enabled():
            return
        notifier = self.discord_notifier
        if notifier is None or not getattr(notifier, "enabled", False):
            return
        now = self._mono()
        if (
            self._last_discord_alert_mono > 0.0
            and (now - self._last_discord_alert_mono) < DATA_FEED_DISCORD_ALERT_COOLDOWN_S
        ):
            return
        self._last_discord_alert_mono = now
        try:
            await notifier.send_data_feed_alert(
                status="down",
                symbol=symbol,
                silence_s=silence_s,
                threshold_s=self._max_silence,
                account_name=self._account_name(),
                reconnect_count=self._reconnects_total + 1,
                cancel_on_staleness=_cancel_on_staleness_enabled(),
                detail="Both quote and bar paths stale; forcing SignalR reconnect.",
            )
        except Exception as exc:
            logger.debug("watchdog discord feed-down alert failed: %s", exc)

    async def _discord_feed_recovered(self, symbol: str) -> None:
        if not _discord_feed_alerts_enabled():
            return
        notifier = self.discord_notifier
        if notifier is None or not getattr(notifier, "enabled", False):
            return
        try:
            await notifier.send_data_feed_alert(
                status="recovered",
                symbol=symbol,
                silence_s=0.0,
                threshold_s=self._max_silence,
                account_name=self._account_name(),
                reconnect_count=self._reconnects_total,
                cancel_on_staleness=_cancel_on_staleness_enabled(),
                detail="Quote or bar path fresh again after zombie reconnect.",
            )
        except Exception as exc:
            logger.debug("watchdog discord feed-recovered alert failed: %s", exc)

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
            from core.working_order_registry import cancel_working_orders_for_stale_symbols
            count = await cancel_working_orders_for_stale_symbols(
                self.working_order_registry,
                self.broker_adapter,
                self.monitor,
                max_silence_s=self._max_silence,
                reason=(
                    f"zombie data paths — {worst_symbol or 'unknown'} "
                    f"quote+bar both silent >{self._max_silence:.0f}s; "
                    f"refusing to leave working entries while reconnecting"
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
        # Snapshot symbols BEFORE stop — stop() must preserve the intent
        # list but we also keep the watchdog getter as belt-and-suspenders.
        symbols_backup = self._current_symbols()
        try:
            await self.market_hub.stop()
        except Exception as exc:
            logger.warning("watchdog: market_hub.stop() raised: %s", exc)
        await asyncio.sleep(0.5)
        try:
            ok = await self.market_hub.start()
            if ok:
                logger.info("🛡️  Market Hub reconnect OK (re-subscribing to symbols)")
                resub = getattr(self.market_hub, "_resubscribe_all_symbols", None)
                if resub is not None:
                    await resub()
                else:
                    for sym in symbols_backup:
                        sub = getattr(self.market_hub, "subscribe_quote", None)
                        if sub is not None:
                            await sub(sym)
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
