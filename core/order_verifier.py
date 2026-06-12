"""Place-and-verify order workflow.

After ``bot.place_oco_bracket_with_stop_entry()`` returns success, the
strategy used to assume the order would land in the working/filled state
purely on the strength of SignalR User Hub events.  The 2026-06-11 MGC
incident proved that assumption can fail: SignalR went zombie, the
trade entered + stopped out on the broker side, and the bot's internal
state never reflected ANY of it.

This module adds a fire-and-forget verification task that runs alongside
the strategy loop:

  1. Caller schedules ``verify_order_landed(...)`` immediately after a
     successful ``place_*`` call.
  2. The verifier waits ``soft_timeout_s`` (default 30 s) for the User
     Hub to deliver any event since the order placement (signalled
     through ``DataFeedHealthMonitor.user_hub_silent_since_last_order``).
  3. If User Hub stayed silent, the verifier falls back to REST polling
     (``broker_adapter.get_open_orders``).
  4. If REST reveals the order EXISTS (working or pending) → fine, just
     User Hub lag; emit an INFO log.
  5. If REST reveals the order DOES NOT EXIST → SignalR is unreliable
     AND the order may already be filled/cancelled silently.  Emit a
     LOUD ERROR with the order id so the operator can reconcile from
     the broker UI.

This is NOT a hard reconciliation tool (it doesn't synthesise TRADE_OPENED
events from a found-but-not-notified order).  It is a TRIPWIRE: when the
SignalR transport is failing, this verifier surfaces the failure within
a minute instead of 5 hours later.

Why fire-and-forget: the strategy is in the middle of its analyze() loop;
blocking on a 30 s verification would jam the per-bar evaluation cadence.
The verifier runs as a separate task and logs its findings independently.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Optional

from core.data_feed_health import DataFeedHealthMonitor

logger = logging.getLogger(__name__)


@dataclass
class OrderVerifyResult:
    """Outcome of one verification run."""
    order_id: str
    user_hub_event_seen: bool
    rest_found_order: Optional[bool]   # None if REST not consulted
    elapsed_s: float
    verdict: str   # "ok_user_hub" / "ok_rest" / "missing"


async def verify_order_landed(
    *,
    order_id: str,
    account_id: Optional[str],
    broker_adapter,
    monitor: DataFeedHealthMonitor,
    strategy_name: str,
    symbol: str,
    soft_timeout_s: float = 30.0,
    rest_poll_timeout_s: float = 20.0,
    rest_poll_interval_s: float = 5.0,
) -> OrderVerifyResult:
    """Verify that ``order_id`` is reachable via either SignalR or REST.

    Returns a verdict; LOGS the outcome.  Never raises.
    """
    if not order_id:
        return OrderVerifyResult(
            order_id="",
            user_hub_event_seen=False,
            rest_found_order=None,
            elapsed_s=0.0,
            verdict="missing_id",
        )

    started = asyncio.get_event_loop().time()

    # Phase 1: wait for any User Hub event to fire AT OR AFTER the order
    # placement.  ``user_event_received_since_last_order()`` is the crisp
    # boolean — unlike the seconds variant, it's unambiguous when the
    # event and placement share a single monotonic tick.
    waited = 0.0
    poll_step = 0.1  # 100 ms granularity keeps tests fast and is fine in prod.
    while waited < soft_timeout_s:
        confirmed = monitor.user_event_received_since_last_order()
        if confirmed is True:
            elapsed = asyncio.get_event_loop().time() - started
            logger.info(
                "✅ order verify (%s %s): User Hub confirmed activity within %.1fs "
                "(order_id=%s)",
                strategy_name, symbol, elapsed, order_id,
            )
            return OrderVerifyResult(
                order_id=order_id, user_hub_event_seen=True,
                rest_found_order=None, elapsed_s=elapsed,
                verdict="ok_user_hub",
            )
        await asyncio.sleep(poll_step)
        waited += poll_step

    # Phase 2: User Hub did NOT confirm.  Fall back to REST polling.
    logger.warning(
        "⏱️  order verify (%s %s): User Hub silent for %.0fs after order placement — "
        "falling back to REST poll (order_id=%s)",
        strategy_name, symbol, soft_timeout_s, order_id,
    )

    found = False
    poll_started = asyncio.get_event_loop().time()
    while (asyncio.get_event_loop().time() - poll_started) < rest_poll_timeout_s:
        try:
            orders = await broker_adapter.get_open_orders(account_id=account_id)
        except Exception as exc:
            logger.warning(
                "⚠️  order verify (%s %s): REST poll raised %s — retrying",
                strategy_name, symbol, type(exc).__name__,
            )
            orders = []
        for o in orders or []:
            oid = o.get("id") or o.get("orderId") or o.get("order_id")
            if str(oid) == str(order_id):
                found = True
                break
        if found:
            break
        await asyncio.sleep(rest_poll_interval_s)

    elapsed = asyncio.get_event_loop().time() - started
    if found:
        logger.warning(
            "⚠️  order verify (%s %s): order_id=%s found via REST after %.1fs — "
            "User Hub IS UNRELIABLE for this run; operator should monitor manually",
            strategy_name, symbol, order_id, elapsed,
        )
        return OrderVerifyResult(
            order_id=order_id, user_hub_event_seen=False,
            rest_found_order=True, elapsed_s=elapsed,
            verdict="ok_rest",
        )

    logger.error(
        "🚨 ORDER VERIFY FAILED: order_id=%s NOT FOUND via REST for %s %s after %.1fs.  "
        "EITHER the order filled+exited silently OR was cancelled OR SignalR + REST both "
        "lost it.  CHECK BROKER UI IMMEDIATELY.  Strategy state will be incorrect until "
        "reconciled.",
        order_id, strategy_name, symbol, elapsed,
    )
    return OrderVerifyResult(
        order_id=order_id, user_hub_event_seen=False,
        rest_found_order=False, elapsed_s=elapsed,
        verdict="missing",
    )


def schedule_order_verification(
    *,
    order_id: str,
    account_id: Optional[str],
    broker_adapter,
    monitor: DataFeedHealthMonitor,
    strategy_name: str,
    symbol: str,
    soft_timeout_s: float = 30.0,
) -> Optional[asyncio.Task]:
    """Fire-and-forget wrapper.  Returns the Task so callers can wait
    on it in tests; ignore the return value in production."""
    try:
        loop = asyncio.get_event_loop()
        if not loop.is_running():
            return None
    except RuntimeError:
        return None
    return loop.create_task(
        verify_order_landed(
            order_id=str(order_id),
            account_id=account_id,
            broker_adapter=broker_adapter,
            monitor=monitor,
            strategy_name=strategy_name,
            symbol=symbol,
            soft_timeout_s=soft_timeout_s,
        ),
        name=f"order_verify_{order_id}",
    )
