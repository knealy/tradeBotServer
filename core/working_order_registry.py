"""Working-order registry — the cancel-on-staleness backbone.

The 2026-06-11 MGC incident exposed a second failure mode beyond the
zombie-data-feed-at-decision-time problem (which the health gate now
catches): once a STOP-ENTRY order has been placed and is sitting on the
broker waiting to fill, the data feed can go zombie AFTER placement.
If price then reaches the trigger, the order fills silently and the bot
has no idea — exact replay of the original incident.

This registry is the "what's currently working on the broker" book
that strategies populate at place-time and User Hub events keep
in-sync.  The ``DataFeedWatchdog`` reads it and, when forcing a
zombie reconnect, ALSO cancels every still-working entry order via
``broker_adapter.cancel_order`` so the next fill can't happen blind.

Contract:

  * Strategies call ``register(...)`` immediately after a successful
    ``place_oco_bracket_with_stop_entry`` return value.  The optional
    ``oco_sibling_ids`` list ties the entry to its SL/TP siblings so a
    cancel of the entry can also cancel the siblings if the broker
    doesn't OCO-cancel them automatically.
  * The User Hub order handler calls ``mark_status(order_id, status)``
    on every order update; when the status indicates the order is no
    longer working (Filled / Cancelled / Rejected) the entry is dropped
    from the registry.
  * The watchdog calls ``snapshot()`` to read all currently-working
    entries.  After cancellation it should call
    ``mark_status(..., "Cancelled")`` to keep the registry in sync even
    if the User Hub event lags.
  * Backtests use a no-op singleton (the bot wires the real one in
    ``trading_bot.__init__`` only; backtest paths never touch this).

Thread safety: a single ``threading.RLock`` guards the dict.  All
public methods are safe to call from the asyncio loop OR from a
SignalR worker thread.  No allocation on the read path beyond the
returned snapshot list.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set

logger = logging.getLogger(__name__)


# Status values that indicate the order has LEFT the working state.
# Strings come from the TopStepX User Hub; integers come from the same
# hub when the payload is a numeric enum.  See ``user_hub_handlers.on_order``.
_TERMINAL_STATUSES: Set[object] = {
    "Filled", 2,
    "Cancelled", 3,
    "Rejected", 4,
    "Expired", 5,
    # Be liberal: anything explicitly "not working" should drop.
}


@dataclass
class WorkingOrder:
    """One placed-but-still-working entry order."""
    order_id: str
    account_id: str
    symbol: str
    side: str
    strategy_name: str
    placed_at_mono: float
    placed_at_wall: float
    oco_sibling_ids: List[str] = field(default_factory=list)
    last_status: str = "Working"

    def is_terminal(self) -> bool:
        return self.last_status in {
            "Filled", "Cancelled", "Rejected", "Expired",
        }


class WorkingOrderRegistry:
    """Single-instance registry of placed-but-unfilled entry orders."""

    def __init__(self, *, clock_mono=time.monotonic, clock_wall=time.time) -> None:
        self._lock = threading.RLock()
        self._orders: Dict[str, WorkingOrder] = {}
        self._mono = clock_mono
        self._wall = clock_wall

    # ───────── mutation ─────────

    def register(
        self,
        *,
        order_id: str,
        account_id: str,
        symbol: str,
        side: str,
        strategy_name: str,
        oco_sibling_ids: Optional[Sequence[str]] = None,
    ) -> None:
        if not order_id:
            return
        oid = str(order_id)
        with self._lock:
            self._orders[oid] = WorkingOrder(
                order_id=oid,
                account_id=str(account_id) if account_id is not None else "",
                symbol=str(symbol or "").upper(),
                side=str(side or "").upper(),
                strategy_name=str(strategy_name or "unknown"),
                placed_at_mono=self._mono(),
                placed_at_wall=self._wall(),
                oco_sibling_ids=[str(x) for x in (oco_sibling_ids or [])],
            )
        logger.debug(
            "working_order_registry: registered %s (%s %s, strategy=%s, siblings=%s)",
            oid, side, symbol, strategy_name, oco_sibling_ids or [],
        )

    def mark_status(self, order_id: str, status: object) -> None:
        """Update the order's last-known status.  If it's terminal,
        drop the entry from the registry."""
        if not order_id:
            return
        oid = str(order_id)
        with self._lock:
            wo = self._orders.get(oid)
            if wo is None:
                return
            wo.last_status = str(status) if status is not None else wo.last_status
            if status in _TERMINAL_STATUSES:
                self._orders.pop(oid, None)
                logger.debug(
                    "working_order_registry: %s reached terminal status %s — unregistered",
                    oid, status,
                )

    def unregister(self, order_id: str) -> None:
        """Explicit unregister (idempotent)."""
        if not order_id:
            return
        with self._lock:
            self._orders.pop(str(order_id), None)

    # ───────── queries ─────────

    def get(self, order_id: str) -> Optional[WorkingOrder]:
        with self._lock:
            return self._orders.get(str(order_id))

    def snapshot(self) -> List[WorkingOrder]:
        """Atomic copy of all currently-working orders.  Returned as a
        list, NOT a generator, so callers can iterate without holding
        the lock."""
        with self._lock:
            return list(self._orders.values())

    def working_count(self) -> int:
        with self._lock:
            return len(self._orders)

    def working_for_symbol(self, symbol: str) -> List[WorkingOrder]:
        sym = (symbol or "").upper()
        with self._lock:
            return [wo for wo in self._orders.values() if wo.symbol == sym]

    def working_age_seconds(self, order_id: str) -> Optional[float]:
        """Seconds since the order was registered."""
        with self._lock:
            wo = self._orders.get(str(order_id))
            if wo is None:
                return None
            return self._mono() - wo.placed_at_mono

    # ───────── diagnostics ─────────

    def snapshot_dict(self) -> dict:
        with self._lock:
            return {
                oid: {
                    "symbol": wo.symbol,
                    "side": wo.side,
                    "strategy_name": wo.strategy_name,
                    "age_s": self._mono() - wo.placed_at_mono,
                    "last_status": wo.last_status,
                    "oco_sibling_ids": list(wo.oco_sibling_ids),
                }
                for oid, wo in self._orders.items()
            }


# ─────────────────── global singleton ───────────────────────────────


_INSTANCE: Optional[WorkingOrderRegistry] = None
_INSTANCE_LOCK = threading.Lock()


def get_registry() -> WorkingOrderRegistry:
    global _INSTANCE
    with _INSTANCE_LOCK:
        if _INSTANCE is None:
            _INSTANCE = WorkingOrderRegistry()
        return _INSTANCE


def set_registry(registry: Optional[WorkingOrderRegistry]) -> None:
    """Inject (or reset by passing ``None``).  Tests use this."""
    global _INSTANCE
    with _INSTANCE_LOCK:
        _INSTANCE = registry


def reset_registry_for_tests() -> None:
    set_registry(None)


# ───────────────── cancel helper (used by watchdog) ─────────────────


async def cancel_working_orders_for_stale_symbols(
    registry: WorkingOrderRegistry,
    broker_adapter,
    monitor,
    *,
    max_silence_s: float,
    reason: str = "unspecified",
    include_siblings: bool = True,
) -> int:
    """Cancel working entries only for symbols where BOTH data paths are stale.

    2026-06-17 fix: blind ``cancel_all_working_orders`` pulled MRR brackets
    while REST bars were still flowing — the operator never saw working
    orders.  Only cancel when quote AND bar activity are both dead for
    the order's symbol.
    """
    snapshot = registry.snapshot()
    if not snapshot:
        return 0

    stale_symbols = {
        wo.symbol.upper()
        for wo in snapshot
        if monitor.symbol_both_paths_stale(wo.symbol, max_silence_s)
    }
    if not stale_symbols:
        logger.info(
            "watchdog: zombie reconnect due but no working orders on "
            "fully-stale symbols — skipping cancel-on-staleness"
        )
        return 0

    to_cancel = [wo for wo in snapshot if wo.symbol.upper() in stale_symbols]
    logger.warning(
        "🛑 cancel_working_orders_for_stale_symbols: cancelling %d/%d "
        "working entry order(s) on stale symbols %s — reason: %s",
        len(to_cancel), len(snapshot), sorted(stale_symbols), reason,
    )

    issued = 0
    for wo in to_cancel:
        issued += 1
        try:
            resp = await broker_adapter.cancel_order(
                order_id=wo.order_id, account_id=wo.account_id,
            )
            ok = bool(getattr(resp, "success", False) or (isinstance(resp, dict) and resp.get("success")))
            logger.warning(
                "🛑 cancelled entry order %s (%s %s, strategy=%s) -> success=%s",
                wo.order_id, wo.side, wo.symbol, wo.strategy_name, ok,
            )
        except Exception as exc:
            logger.error(
                "❌ cancel of entry order %s raised %s: %s",
                wo.order_id, type(exc).__name__, exc,
            )

        if include_siblings:
            for sib in wo.oco_sibling_ids:
                issued += 1
                try:
                    await broker_adapter.cancel_order(
                        order_id=sib, account_id=wo.account_id,
                    )
                    logger.warning(
                        "🛑 cancelled OCO sibling %s (parent=%s)", sib, wo.order_id,
                    )
                except Exception as exc:
                    logger.debug(
                        "sibling cancel %s raised %s: %s",
                        sib, type(exc).__name__, exc,
                    )

        registry.mark_status(wo.order_id, "Cancelled")
        for sib in wo.oco_sibling_ids:
            registry.mark_status(sib, "Cancelled")

    return issued


async def cancel_all_working_orders(
    registry: WorkingOrderRegistry,
    broker_adapter,
    *,
    reason: str = "unspecified",
    include_siblings: bool = True,
) -> int:
    """Cancel every entry currently in ``registry`` via the broker
    adapter.  Optionally also cancels OCO siblings.

    Args:
        registry: the working-order registry.
        broker_adapter: must expose ``async cancel_order(order_id, account_id)``.
        reason: free-text reason logged with each cancellation.
        include_siblings: also cancel any ``oco_sibling_ids`` for each
            entry (defence-in-depth for brokers that don't OCO-cancel
            the entry's children automatically).

    Returns:
        Number of cancel requests issued (entries + siblings).
    """
    snapshot = registry.snapshot()
    if not snapshot:
        return 0

    logger.warning(
        "🛑 cancel_all_working_orders: cancelling %d working entry order(s) — reason: %s",
        len(snapshot), reason,
    )

    issued = 0
    for wo in snapshot:
        # Cancel entry — count the ATTEMPT (so callers can tell how many
        # cancels we issued, including failed ones that still leave the
        # broker in an uncertain state).
        issued += 1
        try:
            resp = await broker_adapter.cancel_order(
                order_id=wo.order_id, account_id=wo.account_id,
            )
            ok = bool(getattr(resp, "success", False) or (isinstance(resp, dict) and resp.get("success")))
            logger.warning(
                "🛑 cancelled entry order %s (%s %s, strategy=%s) -> success=%s",
                wo.order_id, wo.side, wo.symbol, wo.strategy_name, ok,
            )
        except Exception as exc:
            logger.error(
                "❌ cancel of entry order %s raised %s: %s",
                wo.order_id, type(exc).__name__, exc,
            )

        # Cancel siblings.
        if include_siblings:
            for sib in wo.oco_sibling_ids:
                issued += 1
                try:
                    await broker_adapter.cancel_order(
                        order_id=sib, account_id=wo.account_id,
                    )
                    logger.warning(
                        "🛑 cancelled OCO sibling %s (parent=%s)", sib, wo.order_id,
                    )
                except Exception as exc:
                    logger.debug(
                        "sibling cancel %s raised %s: %s",
                        sib, type(exc).__name__, exc,
                    )

        # Keep registry in sync even if User Hub never delivers
        # the cancellation event (the same SignalR-zombie scenario).
        registry.mark_status(wo.order_id, "Cancelled")
        for sib in wo.oco_sibling_ids:
            registry.mark_status(sib, "Cancelled")

    return issued
