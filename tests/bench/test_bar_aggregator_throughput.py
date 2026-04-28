"""Quote → bar builder hot path (Phase 2.11)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from core.bar_aggregator import BarAggregator

pytestmark = pytest.mark.bench

_BASE = datetime(2024, 6, 1, 14, 0, tzinfo=timezone.utc)
_TICKS = 250


def _run_add_quotes():
    agg = BarAggregator(broadcast_callback=None, default_timeframes=["1m"])
    agg.register_timeframes("MNQ", ["1m"])
    ts = _BASE
    for i in range(_TICKS):
        agg.add_quote("MNQ", 18_250.0 + (i % 20) * 0.25, 1, ts)
        ts += timedelta(seconds=1)


def test_bar_aggregator_add_quote_burst(benchmark):
    benchmark(_run_add_quotes)
