"""Bar builder tick aggregation (Python reference path; Phase 2.11)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from core.bar_aggregator import BarBuilder

pytestmark = pytest.mark.bench

_START = datetime(2024, 6, 1, 9, 30, tzinfo=timezone.utc)


def _many_ticks():
    b = BarBuilder("MNQ", "1m", _START)
    t = _START
    for i in range(2000):
        b.add_tick(21_000.0 + i * 0.01, 1, t)
        t += timedelta(milliseconds=500)


def test_bar_builder_add_tick(benchmark):
    benchmark(_many_ticks)
