"""
Bounded sequential queue for heavy work kicked off from SignalR User Hub paths.

Hub callbacks must stay cheap: invalidate caches / schedule coroutines here so the
event loop is not flooded with unbounded ``asyncio.create_task`` fan-out.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class HubDeferredWorkQueue:
    """
    Single worker draining a bounded ``asyncio.Queue`` of awaitables (coroutines).

    On saturation the oldest pending item is dropped (``close()``) so backpressure
    is explicit instead of piling up tasks.
    """

    __slots__ = ("_maxsize", "_queue", "_worker", "_started")

    def __init__(self, maxsize: int = 128) -> None:
        self._maxsize = max(8, int(maxsize))
        self._queue: Optional[asyncio.Queue] = None
        self._worker: Optional[asyncio.Task] = None
        self._started = False

    async def ensure_started(self) -> None:
        """Idempotent: start background worker on the running loop."""
        if self._started:
            return
        self._queue = asyncio.Queue(maxsize=self._maxsize)
        self._worker = asyncio.create_task(self._run_worker(), name="hub_deferred_worker")
        self._started = True
        logger.debug("HubDeferredWorkQueue started (maxsize=%s)", self._maxsize)

    async def schedule(self, coro) -> None:
        """Enqueue a coroutine object for the worker to ``await`` (must be called from async context)."""
        await self.ensure_started()
        assert self._queue is not None
        try:
            self._queue.put_nowait(coro)
        except asyncio.QueueFull:
            try:
                stale = self._queue.get_nowait()
                if asyncio.iscoroutine(stale):
                    stale.close()
            except asyncio.QueueEmpty:
                pass
            try:
                self._queue.put_nowait(coro)
            except asyncio.QueueFull:
                logger.warning(
                    "HubDeferredWorkQueue saturated; dropping new work (maxsize=%s)",
                    self._maxsize,
                )
                if asyncio.iscoroutine(coro):
                    coro.close()

    def schedule_from_thread(self, loop: asyncio.AbstractEventLoop, coro) -> None:
        """
        Schedule ``schedule(coro)`` on *loop* from a non-async thread (e.g. sync SignalR path).

        If the loop is missing or not running, the coroutine is closed to avoid
        \"never awaited\" warnings.
        """
        if loop is None or not loop.is_running():
            if asyncio.iscoroutine(coro):
                coro.close()
            return

        async def _wrap() -> None:
            await self.schedule(coro)

        try:
            asyncio.run_coroutine_threadsafe(_wrap(), loop)
        except Exception:
            logger.debug("schedule_from_thread failed", exc_info=True)
            if asyncio.iscoroutine(coro):
                coro.close()

    async def _run_worker(self) -> None:
        assert self._queue is not None
        while True:
            work = await self._queue.get()
            try:
                await work
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Hub deferred work item failed")

    async def stop(self) -> None:
        if self._worker and not self._worker.done():
            self._worker.cancel()
            try:
                await self._worker
            except asyncio.CancelledError:
                pass
        self._worker = None
        self._queue = None
        self._started = False
