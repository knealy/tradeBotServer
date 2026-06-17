"""End-to-end pin of the data-feed health gate inside strategy_base.

The gate has three modes (``DATA_FEED_HEALTH_GATE_MODE``):

  * ``warn``   — mild degradation logs WARNING and proceeds; severe
                 degradation (>300s silence by default) refuses (DEFAULT).
  * ``off``    — gate silent.
  * ``refuse`` — refuse the order outright (legacy strict mode).

These tests pin each mode end-to-end through ``is_safe_to_trade`` /
``HealthVerdict`` so the contract can't silently regress.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.data_feed_health import (
    DataFeedHealthMonitor,
    get_monitor,
    set_monitor,
    reset_monitor_for_tests,
    resolve_health_gate_decision,
    resolve_health_gate_mode,
)


@pytest.fixture(autouse=True)
def isolate_monitor():
    """Each test gets a fresh, fake-clock monitor; reset on teardown."""
    class _Clk:
        def __init__(self) -> None:
            self.t = 0.0
        def __call__(self) -> float:
            return self.t

    clk = _Clk()
    m = DataFeedHealthMonitor(
        market_hub_max_silence_s=60.0,
        user_hub_max_silence_after_order_s=30.0,
        startup_grace_s=0.0,        # zero grace so we can test reject path immediately
        clock_mono=clk,
    )
    set_monitor(m)
    yield m, clk
    reset_monitor_for_tests()


# ───────────── direct monitor contract ─────────────────────────────


def test_singleton_round_trips(isolate_monitor):
    m, _ = isolate_monitor
    assert get_monitor() is m


def test_safety_blocks_when_market_hub_never_ticked(isolate_monitor):
    m, _ = isolate_monitor
    v = m.is_safe_to_trade("MGC")
    assert v.ok is False
    assert "startup grace" in v.reason or "No quote ticks" in v.reason


def test_safety_allows_when_freshly_ticked(isolate_monitor):
    m, _ = isolate_monitor
    m.record_market_tick("MGC")
    assert m.is_safe_to_trade("MGC").ok is True


def test_safety_blocks_when_user_hub_silent_after_order(isolate_monitor):
    m, clk = isolate_monitor
    m.record_market_tick("MGC")
    m.record_order_placed()
    clk.t = 45.0  # 45 s > 30 s threshold
    v = m.is_safe_to_trade("MGC")
    assert v.ok is False
    assert "User Hub silent" in v.reason


# ───────────── gate-mode policy (warn / off / refuse) ──────────────


def _resolve_gate_mode(env: dict) -> str:
    """Mirror of the resolution logic — now lives in core.data_feed_health."""
    import os
    saved = {k: os.environ.pop(k, None) for k in (
        "DATA_FEED_HEALTH_GATE", "DATA_FEED_HEALTH_GATE_MODE",
    )}
    try:
        for k, v in env.items():
            os.environ[k] = v
        return resolve_health_gate_mode()
    finally:
        for k in ("DATA_FEED_HEALTH_GATE", "DATA_FEED_HEALTH_GATE_MODE"):
            os.environ.pop(k, None)
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v


def test_gate_mode_default_is_warn():
    assert _resolve_gate_mode({}) == "warn"


def test_gate_mode_explicit_refuse():
    assert _resolve_gate_mode({"DATA_FEED_HEALTH_GATE_MODE": "refuse"}) == "refuse"


def test_gate_mode_legacy_off_via_kill_switch():
    # Legacy env var ``DATA_FEED_HEALTH_GATE=false`` must still disable.
    assert _resolve_gate_mode({"DATA_FEED_HEALTH_GATE": "false"}) == "off"
    assert _resolve_gate_mode({"DATA_FEED_HEALTH_GATE": "no"}) == "off"


def test_gate_mode_explicit_off():
    assert _resolve_gate_mode({"DATA_FEED_HEALTH_GATE_MODE": "off"}) == "off"


def test_warn_mode_refuses_severe_market_hub_silence(isolate_monitor):
    """2026-06-17 regression: warn mode must refuse when silence >300s."""
    m, clk = isolate_monitor
    clk.t = 15.0  # past startup grace
    m.record_market_tick("MGC")
    clk.t = 400.0  # 385s since tick — severe (>300) and past 60s threshold
    verdict = m.is_safe_to_trade("MGC")
    assert verdict.ok is False
    decision, reason = resolve_health_gate_decision(
        verdict, m, "MGC", gate_mode="warn", severe_silence_s=300.0,
    )
    assert decision == "refuse"
    assert "severe silence" in reason


def test_warn_mode_proceeds_mild_market_hub_silence(isolate_monitor):
    """Mild transients (120-300s) still warn-and-proceed in warn mode."""
    m, clk = isolate_monitor
    clk.t = 15.0
    m.record_market_tick("MGC")
    clk.t = 90.0  # 75s since tick — unhealthy (>60) but not severe (<300)
    verdict = m.is_safe_to_trade("MGC")
    assert verdict.ok is False
    decision, _ = resolve_health_gate_decision(
        verdict, m, "MGC", gate_mode="warn", severe_silence_s=300.0,
    )
    assert decision == "warn"


def test_reset_clock_does_not_mask_stale_feed_after_ticks(isolate_monitor):
    """Post-reconnect grace must not let a previously-ticking zombie pass."""
    m, clk = isolate_monitor
    clk.t = 15.0
    m.record_market_tick("MGC")
    clk.t = 90.0  # stale
    assert m.is_safe_to_trade("MGC").ok is False
    m.reset_clock_for_grace()
    # Still stale — grace only helps symbols awaiting their first tick.
    assert m.is_safe_to_trade("MGC").ok is False
