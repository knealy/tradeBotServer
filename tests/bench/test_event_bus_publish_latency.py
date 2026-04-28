"""EventBus publish → async subscriber drain (Phase 2.11)."""

from __future__ import annotations

import asyncio

import pytest

from core.event_bus import EventBus
from core.events import Event, EventType

pytestmark = pytest.mark.bench

_N = 200


async def _publish_burst():
    bus = EventBus()
    done = asyncio.Event()
    count = 0

    async def cb(_ev: Event) -> None:
        nonlocal count
        count += 1
        if count >= _N:
            done.set()

    bus.subscribe(EventType.QUOTE_UPDATED, cb)
    await bus.start()
    try:
        for i in range(_N):
            await bus.publish(Event(EventType.QUOTE_UPDATED, {"i": i}))
        await asyncio.wait_for(done.wait(), timeout=5.0)
    finally:
        await bus.stop()


def test_event_bus_publish_and_drain(benchmark):
    benchmark(lambda: asyncio.run(_publish_burst()))
