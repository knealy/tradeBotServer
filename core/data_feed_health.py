"""Real-time data-feed health monitor.

The 2026-06-11 MGC -$534 stop-out post-mortem proved that the bot can place
orders while BOTH SignalR hubs are silently zombied (connection appears alive,
zero data flowing).  The bot had no idea its order had filled, no idea SL had
hit, and continued running for >4 hours unaware of its own position.

This module is the **single source of truth** for "is our data feed healthy
enough to place a trade right now?".  It is wired into both SignalR hubs:

  * ``WebSocketManager.on_quote``      -> ``record_market_tick(symbol)``
  * ``UserHubManager._handle_*_update`` -> ``record_user_event(kind)``

…and is consulted by strategies via ``is_safe_to_trade(symbol)`` before any
``place_*_bracket()`` call.  When either hub has been silent for longer than
its configured threshold, ``is_safe_to_trade`` returns ``False`` with a
human-readable ``reason`` so the strategy can log + skip the entry.

The contract is intentionally narrow: this module DOES NOT initiate
reconnects (the watchdog task does that).  It DOES NOT cancel orders
(strategies do that based on its verdict).  It is a pure observer + policy
helper.

Thread-safety: every public method may be called from the asyncio loop OR
from a SignalR worker thread; all mutating ops go through ``self._lock``
(a regular ``threading.Lock`` because SignalR fires synchronously).
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, time as dtime, timezone
from typing import Dict, Optional, Tuple
try:
    from zoneinfo import ZoneInfo
    _ET: Optional[ZoneInfo] = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover
    _ET = None

logger = logging.getLogger(__name__)


# ─────────────────── tunables (env-overridable) ──────────────────────


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


# When MARKET HUB has not delivered a tick for this symbol in
# ``MARKET_HUB_MAX_SILENCE_SECONDS``, the feed is considered ZOMBIED.
# Default 120 s — tight enough to catch silent-failure within one
# 5-min bar, loose enough that thin pre-market gaps don't cause
# false alarms.  Tunable via env.
MARKET_HUB_MAX_SILENCE_SECONDS: float = _env_float(
    "DATA_FEED_MARKET_HUB_MAX_SILENCE_S", 120.0
)

# USER HUB max silence — order/position/account events naturally
# come less frequently (only when something happens), so we can't
# treat "no events for 60 s" as a problem during a quiet session.
# Instead, we check ONLY when we expect events (e.g. immediately
# after placing an order).  This threshold is the "no events since
# the most recent order was placed" tolerance.
USER_HUB_MAX_SILENCE_AFTER_ORDER_SECONDS: float = _env_float(
    "DATA_FEED_USER_HUB_MAX_SILENCE_AFTER_ORDER_S", 90.0
)

# Initial grace period after WebSocketManager.start() before the
# market-hub-silence check fires (gives the hub time to negotiate
# its first subscription).
MARKET_HUB_STARTUP_GRACE_SECONDS: float = _env_float(
    "DATA_FEED_STARTUP_GRACE_S", 60.0
)


# ─────────────────────── data classes ────────────────────────────────


@dataclass
class SymbolFeedState:
    """Per-symbol Market Hub state."""
    last_tick_at_mono: Optional[float] = None
    last_tick_at_wall: Optional[datetime] = None
    total_ticks: int = 0


@dataclass
class HealthVerdict:
    """Returned by ``is_safe_to_trade`` — explicit (ok, reason) tuple."""
    ok: bool
    reason: str = ""

    def __bool__(self) -> bool:
        return self.ok


# ───────────────────────── monitor ───────────────────────────────────


class DataFeedHealthMonitor:
    """Single-instance health tracker for both SignalR hubs.

    Lifecycle:
        bot startup → ``monitor = DataFeedHealthMonitor()``
                    → ``websocket_manager.add_quote_callback(monitor.record_market_tick_callback)``
                    → ``user_hub_manager.add_*_callback(monitor.record_user_event_callback)``
        per-trade  → ``verdict = monitor.is_safe_to_trade(symbol)``
                    → if not verdict.ok: log(verdict.reason); skip
        bot ticks  → consumed by ``core.data_feed_watchdog`` (separate task)
    """

    def __init__(
        self,
        *,
        market_hub_max_silence_s: float = MARKET_HUB_MAX_SILENCE_SECONDS,
        user_hub_max_silence_after_order_s: float = USER_HUB_MAX_SILENCE_AFTER_ORDER_SECONDS,
        startup_grace_s: float = MARKET_HUB_STARTUP_GRACE_SECONDS,
        clock_mono=time.monotonic,
        clock_wall=lambda: datetime.now(timezone.utc),
    ) -> None:
        self._market_max_silence = float(market_hub_max_silence_s)
        self._user_max_silence_after_order = float(user_hub_max_silence_after_order_s)
        self._startup_grace = float(startup_grace_s)
        self._mono = clock_mono
        self._wall = clock_wall

        # RLock so ``snapshot()`` (which holds the lock) can safely call
        # other lock-acquiring methods like ``user_hub_silent_since_last_order``.
        self._lock = threading.RLock()
        self._symbols: Dict[str, SymbolFeedState] = {}

        # User Hub: we don't enforce a constant-flow rule (it can be
        # legitimately silent for hours), only an "expected activity
        # since last order placement" rule.
        self._last_user_event_at_mono: Optional[float] = None
        self._last_order_placed_at_mono: Optional[float] = None
        self._user_event_count: int = 0

        # Startup timestamp for the grace period.
        self._started_at_mono: float = self._mono()

        # Optional pause: when True, ``is_safe_to_trade`` returns OK
        # regardless of feed state.  Used by backtest / replay paths.
        self._paused: bool = False

    # ─── recording (called from SignalR worker threads) ──────────────

    def record_market_tick(self, symbol: str) -> None:
        """Called on every quote update for ``symbol``.

        Must be CHEAP — runs on every tick.  Hot path = one dict
        lookup + two timestamp writes under the lock.
        """
        if not symbol:
            return
        sym = symbol.upper()
        now_m = self._mono()
        now_w = self._wall()
        with self._lock:
            st = self._symbols.get(sym)
            if st is None:
                st = SymbolFeedState()
                self._symbols[sym] = st
            st.last_tick_at_mono = now_m
            st.last_tick_at_wall = now_w
            st.total_ticks += 1

    def record_user_event(self, kind: str = "unspecified") -> None:
        """Called on every order / position / account hub event."""
        now_m = self._mono()
        with self._lock:
            self._last_user_event_at_mono = now_m
            self._user_event_count += 1

    def record_order_placed(self) -> None:
        """Strategies call this immediately AFTER ``place_*_bracket()``
        returns success.  Used by ``user_hub_silent_since_last_order``
        to detect "no fill confirmation arriving" within the threshold.
        """
        with self._lock:
            self._last_order_placed_at_mono = self._mono()

    # ─── SignalR-style adapter shims ─────────────────────────────────

    def record_market_tick_callback(self, symbol: str, _quote_data: dict) -> None:
        """Drop-in shim for ``WebSocketManager.add_quote_callback``."""
        self.record_market_tick(symbol)

    def record_user_account_callback(self, _payload) -> None:
        self.record_user_event("account")

    def record_user_position_callback(self, _payload) -> None:
        self.record_user_event("position")

    def record_user_order_callback(self, _payload) -> None:
        self.record_user_event("order")

    # ─── query (called from any thread) ──────────────────────────────

    def market_hub_age_seconds(self, symbol: str) -> Optional[float]:
        """Seconds since the most recent tick for ``symbol``.

        ``None`` if no tick has ever been recorded for this symbol.
        """
        sym = symbol.upper()
        now_m = self._mono()
        with self._lock:
            st = self._symbols.get(sym)
            if st is None or st.last_tick_at_mono is None:
                return None
            return now_m - st.last_tick_at_mono

    def user_hub_silent_since_last_order(self) -> Optional[float]:
        """Seconds elapsed between the last order placement and the
        most recent user-hub event.

        Returns:
          * ``None`` if no order has been placed yet (nothing to check).
          * ``0.0`` if a user-hub event arrived AFTER the most recent order.
          * positive seconds if no user-hub event has arrived since
            the most recent order (i.e. user hub may be zombied).
        """
        now_m = self._mono()
        with self._lock:
            placed = self._last_order_placed_at_mono
            user_last = self._last_user_event_at_mono
            if placed is None:
                return None
            if user_last is not None and user_last >= placed:
                return 0.0
            return now_m - placed

    def user_event_received_since_last_order(self) -> Optional[bool]:
        """Crisp boolean form of the silence check.

        Returns:
          * ``None`` if no order has been placed yet.
          * ``True`` if a user-hub event arrived AT OR AFTER the last
            order placement (the User Hub is responsive).
          * ``False`` if NO user-hub event has been recorded since the
            most recent order (User Hub may be zombied).

        This helper is unambiguous when monotonic clocks deliver identical
        timestamps (e.g. test fakes); ``user_hub_silent_since_last_order``
        could not distinguish "0.0 seconds elapsed" from "confirmed".
        """
        with self._lock:
            placed = self._last_order_placed_at_mono
            user_last = self._last_user_event_at_mono
            if placed is None:
                return None
            if user_last is None:
                return False
            return user_last >= placed

    def total_market_ticks(self, symbol: str) -> int:
        sym = symbol.upper()
        with self._lock:
            st = self._symbols.get(sym)
            return st.total_ticks if st else 0

    def total_user_events(self) -> int:
        with self._lock:
            return self._user_event_count

    def in_startup_grace(self) -> bool:
        return (self._mono() - self._started_at_mono) < self._startup_grace

    # ─── policy (the canonical question strategies ask) ──────────────

    def is_safe_to_trade(self, symbol: str) -> HealthVerdict:
        """Verdict: ``ok`` iff both hubs look healthy enough to place an order.

        Checks (in order):

          1. ``paused`` (backtest/replay opt-out)         -> ok
          2. Still in startup grace window                 -> ok (with note)
          3. Market Hub age for ``symbol`` exceeds limit   -> NOT ok
          4. Market Hub never received a tick (and not in
             grace)                                        -> NOT ok
          5. User Hub silent since last order (>limit)     -> NOT ok
          6. Otherwise                                     -> ok
        """
        if self._paused:
            return HealthVerdict(True, "paused (backtest/replay)")

        if self.in_startup_grace():
            return HealthVerdict(
                True,
                f"startup grace ({self._startup_grace:.0f}s) — health checks bypassed",
            )

        sym = symbol.upper()
        age = self.market_hub_age_seconds(sym)
        if age is None:
            return HealthVerdict(
                False,
                f"Market Hub has NEVER delivered a tick for {sym} since startup; "
                f"refusing to trade until at least one quote arrives",
            )
        if age > self._market_max_silence:
            return HealthVerdict(
                False,
                f"Market Hub silent for {sym} for {age:.1f}s "
                f"(threshold {self._market_max_silence:.0f}s); feed is likely zombied",
            )

        user_silent = self.user_hub_silent_since_last_order()
        if user_silent is not None and user_silent > self._user_max_silence_after_order:
            return HealthVerdict(
                False,
                f"User Hub silent for {user_silent:.1f}s since the last order was placed "
                f"(threshold {self._user_max_silence_after_order:.0f}s); "
                f"order tracking is unreliable",
            )

        return HealthVerdict(True, f"market_age={age:.1f}s user_silent={user_silent}")

    # ─── control ──────────────────────────────────────────────────────

    def pause(self) -> None:
        self._paused = True

    def resume(self) -> None:
        self._paused = False

    def reset_clock_for_grace(self) -> None:
        """Reset the startup grace window — used after a deliberate
        reconnect so the watchdog gives the new connection time to
        deliver its first tick before flagging it as zombied again."""
        self._started_at_mono = self._mono()

    # ─── diagnostics ─────────────────────────────────────────────────

    def snapshot(self) -> Dict[str, object]:
        """Read-only dict for logging / health endpoints."""
        with self._lock:
            return {
                "symbols": {
                    sym: {
                        "total_ticks": st.total_ticks,
                        "last_tick_iso": (
                            st.last_tick_at_wall.isoformat()
                            if st.last_tick_at_wall else None
                        ),
                        "age_s": (
                            (self._mono() - st.last_tick_at_mono)
                            if st.last_tick_at_mono else None
                        ),
                    }
                    for sym, st in self._symbols.items()
                },
                "user_event_count": self._user_event_count,
                "user_silent_since_last_order_s": self.user_hub_silent_since_last_order(),
                "in_startup_grace": self.in_startup_grace(),
                "paused": self._paused,
            }


# ───────────────────── global singleton accessor ─────────────────────


_INSTANCE: Optional[DataFeedHealthMonitor] = None
_INSTANCE_LOCK = threading.Lock()


def get_monitor() -> DataFeedHealthMonitor:
    """Return the process-singleton monitor, creating it if needed.

    Strategies + watchdog + hub managers all share this single instance.
    Tests can override by calling ``set_monitor()``.
    """
    global _INSTANCE
    with _INSTANCE_LOCK:
        if _INSTANCE is None:
            _INSTANCE = DataFeedHealthMonitor()
        return _INSTANCE


def set_monitor(monitor: Optional[DataFeedHealthMonitor]) -> None:
    """Inject a custom monitor (or ``None`` to reset).  Tests use this
    to install a deterministic mono-clock monitor."""
    global _INSTANCE
    with _INSTANCE_LOCK:
        _INSTANCE = monitor


def reset_monitor_for_tests() -> None:
    """Drop the singleton so the next ``get_monitor()`` builds a fresh one."""
    set_monitor(None)


# ──────────────── market-hours helper (for watchdog) ─────────────────


def is_within_market_hours(
    now_utc: Optional[datetime] = None,
    *,
    open_minutes_et: int = 18 * 60,   # 18:00 ET = futures Globex Sunday-Friday open
    close_minutes_et: int = 17 * 60,  # 17:00 ET = daily close
) -> bool:
    """Coarse "should we expect data flowing right now?" gate.

    CME-listed futures (MES/MNQ/MGC) trade Sunday 18:00 ET → Friday 17:00 ET
    with a daily maintenance pause 17:00-18:00 ET.  Outside these windows,
    "no Market Hub ticks" is EXPECTED, so the watchdog should not force
    reconnects.

    This is a coarse-but-safe gate; it doesn't model holidays.
    Holiday-day false positives are benign (the watchdog merely reconnects
    a healthy connection — wasteful but not dangerous).
    """
    if _ET is None:
        return True  # cannot tell — be permissive

    now_utc = now_utc or datetime.now(timezone.utc)
    et = now_utc.astimezone(_ET)
    minutes = et.hour * 60 + et.minute
    weekday = et.weekday()  # 0 Mon … 6 Sun

    # Maintenance pause every day 17:00-18:00 ET.
    if close_minutes_et <= minutes < open_minutes_et:
        return False

    # Friday after 17:00 ET → Sunday before 18:00 ET = closed.
    if weekday == 4 and minutes >= close_minutes_et:
        return False
    if weekday == 5:
        return False
    if weekday == 6 and minutes < open_minutes_et:
        return False

    return True
