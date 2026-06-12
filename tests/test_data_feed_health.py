"""Pinning tests for ``core.data_feed_health``.

These tests are the executable specification of the data-feed health
gate that prevents repeats of the 2026-06-11 MGC zombie-SignalR incident.
"""

from __future__ import annotations

from datetime import datetime, timezone
import time

import pytest

from core.data_feed_health import (
    DataFeedHealthMonitor,
    HealthVerdict,
    is_within_market_hours,
)


class FakeClock:
    """Hand-controlled monotonic clock for deterministic tests."""
    def __init__(self, start: float = 0.0) -> None:
        self._t = start

    def __call__(self) -> float:
        return self._t

    def advance(self, dt: float) -> None:
        self._t += dt


def _wall_clock_zero() -> datetime:
    return datetime(2026, 6, 11, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def monitor():
    """Fresh monitor with grace=10 s, market-silence=60 s, user-silence-after-order=30 s."""
    clk = FakeClock(start=1000.0)
    m = DataFeedHealthMonitor(
        market_hub_max_silence_s=60.0,
        user_hub_max_silence_after_order_s=30.0,
        startup_grace_s=10.0,
        clock_mono=clk,
        clock_wall=_wall_clock_zero,
    )
    return m, clk


# ───────────────────── basic recording ─────────────────────────────


def test_records_market_ticks(monitor):
    m, _ = monitor
    m.record_market_tick("MGC")
    assert m.total_market_ticks("MGC") == 1
    m.record_market_tick("MGC")
    m.record_market_tick("MNQ")
    assert m.total_market_ticks("MGC") == 2
    assert m.total_market_ticks("MNQ") == 1
    assert m.total_market_ticks("ES") == 0


def test_records_user_events(monitor):
    m, _ = monitor
    assert m.total_user_events() == 0
    m.record_user_event("order")
    m.record_user_event("position")
    assert m.total_user_events() == 2


def test_callback_shims_record_state(monitor):
    m, _ = monitor
    # Market Hub callback shim accepts (symbol, quote_dict).
    m.record_market_tick_callback("MGC", {"bid": 4100, "ask": 4101})
    assert m.total_market_ticks("MGC") == 1
    # User Hub callback shims each accept a single payload arg.
    m.record_user_order_callback({"order_id": "abc"})
    m.record_user_account_callback({"balance": 100000})
    m.record_user_position_callback({"position_size": 1})
    assert m.total_user_events() == 3


# ───────────────────── age tracking ────────────────────────────────


def test_market_hub_age_seconds_returns_none_before_any_tick(monitor):
    m, _ = monitor
    assert m.market_hub_age_seconds("MGC") is None


def test_market_hub_age_seconds_increases_with_time(monitor):
    m, clk = monitor
    m.record_market_tick("MGC")
    assert m.market_hub_age_seconds("MGC") == pytest.approx(0.0, abs=1e-6)
    clk.advance(45.0)
    assert m.market_hub_age_seconds("MGC") == pytest.approx(45.0, abs=1e-6)


def test_market_hub_age_resets_on_new_tick(monitor):
    m, clk = monitor
    m.record_market_tick("MGC")
    clk.advance(30.0)
    m.record_market_tick("MGC")
    assert m.market_hub_age_seconds("MGC") == pytest.approx(0.0, abs=1e-6)


# ───────────────────── safe-to-trade verdicts ──────────────────────


def test_startup_grace_bypasses_all_checks(monitor):
    m, _ = monitor
    # Grace = 10 s; we're at 0 s elapsed.
    v = m.is_safe_to_trade("MGC")
    assert v.ok is True
    assert "startup grace" in v.reason


def test_market_hub_never_ticked_blocks_trade_after_grace(monitor):
    m, clk = monitor
    clk.advance(15.0)  # past grace window
    v = m.is_safe_to_trade("MGC")
    assert v.ok is False
    assert "NEVER delivered a tick" in v.reason


def test_market_hub_fresh_tick_allows_trade(monitor):
    m, clk = monitor
    clk.advance(15.0)  # past grace
    m.record_market_tick("MGC")
    v = m.is_safe_to_trade("MGC")
    assert v.ok is True


def test_market_hub_stale_tick_blocks_trade(monitor):
    m, clk = monitor
    clk.advance(15.0)
    m.record_market_tick("MGC")
    clk.advance(75.0)  # past 60 s silence threshold
    v = m.is_safe_to_trade("MGC")
    assert v.ok is False
    assert "Market Hub silent" in v.reason
    assert "MGC" in v.reason


def test_per_symbol_isolation_in_safety_check(monitor):
    m, clk = monitor
    clk.advance(15.0)
    m.record_market_tick("MGC")
    clk.advance(75.0)
    # MGC is stale, but MNQ would also be "never ticked" (which also blocks).
    assert m.is_safe_to_trade("MGC").ok is False
    # Refresh MNQ specifically — its age check is independent.
    m.record_market_tick("MNQ")
    assert m.is_safe_to_trade("MGC").ok is False
    assert m.is_safe_to_trade("MNQ").ok is True


def test_user_hub_silence_after_order_blocks_new_orders(monitor):
    m, clk = monitor
    clk.advance(15.0)  # past grace
    m.record_market_tick("MGC")

    # Place an order; immediately afterward, no User Hub event arrives.
    m.record_order_placed()
    clk.advance(45.0)  # > 30 s user_max_silence_after_order

    v = m.is_safe_to_trade("MGC")
    assert v.ok is False
    assert "User Hub silent" in v.reason


def test_user_hub_event_after_order_clears_silence(monitor):
    m, clk = monitor
    clk.advance(15.0)
    m.record_market_tick("MGC")

    m.record_order_placed()
    clk.advance(5.0)
    m.record_user_event("order")  # User Hub came back
    clk.advance(60.0)  # plenty of time later

    # Still fresh: Market Hub stale check is what would catch a fresh problem.
    # But user-hub-silence is reset because last event > last order.
    m.record_market_tick("MGC")  # keep market fresh
    v = m.is_safe_to_trade("MGC")
    assert v.ok is True


def test_user_hub_silent_since_last_order_helper(monitor):
    m, clk = monitor
    assert m.user_hub_silent_since_last_order() is None  # no order ever
    m.record_order_placed()
    assert m.user_hub_silent_since_last_order() == pytest.approx(0.0, abs=1e-6)
    clk.advance(40.0)
    assert m.user_hub_silent_since_last_order() == pytest.approx(40.0, abs=1e-6)
    m.record_user_event("order")
    assert m.user_hub_silent_since_last_order() == pytest.approx(0.0, abs=1e-6)


# ───────────────────── pause + reset ───────────────────────────────


def test_pause_bypasses_all_checks(monitor):
    m, clk = monitor
    clk.advance(15.0)  # past grace
    # Nothing ticked, would normally block.
    assert m.is_safe_to_trade("MGC").ok is False
    m.pause()
    assert m.is_safe_to_trade("MGC").ok is True
    m.resume()
    assert m.is_safe_to_trade("MGC").ok is False


def test_reset_clock_for_grace_extends_grace_window(monitor):
    m, clk = monitor
    clk.advance(15.0)
    assert m.is_safe_to_trade("MGC").ok is False  # past grace, never ticked
    m.reset_clock_for_grace()
    # Now we're back inside grace.
    assert m.is_safe_to_trade("MGC").ok is True


# ───────────────────── snapshot ────────────────────────────────────


def test_snapshot_shape_and_content(monitor):
    m, clk = monitor
    m.record_market_tick("MGC")
    clk.advance(5.0)  # still inside 10 s grace
    m.record_user_event("order")
    snap = m.snapshot()
    assert "symbols" in snap
    assert "MGC" in snap["symbols"]
    assert snap["symbols"]["MGC"]["total_ticks"] == 1
    assert snap["symbols"]["MGC"]["age_s"] == pytest.approx(5.0, abs=1e-6)
    assert snap["user_event_count"] == 1
    assert snap["in_startup_grace"] is True


# ───────────────────── HealthVerdict bool semantics ────────────────


def test_health_verdict_truthy_when_ok():
    assert bool(HealthVerdict(True, "all good")) is True
    assert bool(HealthVerdict(False, "nope")) is False
    # Cleanly drops into ``if not verdict:`` patterns used by strategies.
    v = HealthVerdict(False, "feed down")
    assert not v
    assert v.reason == "feed down"


# ───────────────────── market-hours helper ─────────────────────────


def test_is_within_market_hours_during_active_trading():
    # Tuesday 14:00 ET = 18:00 UTC = active session
    t = datetime(2026, 6, 9, 18, 0, 0, tzinfo=timezone.utc)
    assert is_within_market_hours(t) is True


def test_is_within_market_hours_during_daily_maintenance():
    # Tuesday 17:30 ET = 21:30 UTC = maintenance pause
    t = datetime(2026, 6, 9, 21, 30, 0, tzinfo=timezone.utc)
    assert is_within_market_hours(t) is False


def test_is_within_market_hours_during_weekend():
    # Saturday noon ET = 16:00 UTC = closed
    t = datetime(2026, 6, 13, 16, 0, 0, tzinfo=timezone.utc)
    assert is_within_market_hours(t) is False
