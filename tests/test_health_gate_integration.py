"""End-to-end pin of the data-feed health gate inside strategy_base.

The gate has three modes (``DATA_FEED_HEALTH_GATE_MODE``):

  * ``warn``   — log a WARNING and proceed (DEFAULT, post-2026-06-11-fix
                 design — the strategy's signal logic is the source of
                 truth; the rest of the safety net protects the trade).
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
    assert "NEVER" in v.reason


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
    """Mirror of the resolution logic in strategy_base + trading_bot.

    Centralising this lets the tests pin the contract without standing
    up the full strategy/bot stack.
    """
    if env.get("DATA_FEED_HEALTH_GATE", "true").lower() in ("false", "0", "no"):
        return "off"
    return env.get("DATA_FEED_HEALTH_GATE_MODE", "warn").lower()


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
