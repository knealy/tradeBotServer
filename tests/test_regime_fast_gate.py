"""Unit tests for core.regime_fast_gate."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from core.regime_fast_gate import (
    RegimeFastGate,
    regime_fast_gate_enabled,
    rolling_mean_max_from_env,
    session_halt_usd_from_env,
)


@pytest.fixture(autouse=True)
def _enable_fast_gate(monkeypatch, tmp_path):
    monkeypatch.setenv("REGIME_FAST_GATE_ENABLED", "1")
    monkeypatch.setenv("REGIME_FAST_DLL_USD", "1000")
    monkeypatch.setenv("REGIME_FAST_SESSION_HALT_PCT", "0.40")
    monkeypatch.setenv("REGIME_FAST_WEEKLY_MGC_LOSS_PCT", "0.30")
    monkeypatch.setenv("REGIME_FAST_ROLLING_WINDOW", "3")
    monkeypatch.setenv("REGIME_FAST_ROLLING_WARMUP", "3")
    monkeypatch.setenv("REGIME_FAST_THROTTLE_DURATION", "5")


def test_disabled_passthrough(monkeypatch):
    monkeypatch.delenv("REGIME_FAST_GATE_ENABLED", raising=False)
    assert not regime_fast_gate_enabled()
    gate = RegimeFastGate("1", state_dir=Path("/tmp"))
    mult, _ = gate.resolve_multiplier("morning_range_reversion", "MGC")
    assert mult == 1.0


def test_dll_threshold_defaults(monkeypatch):
    monkeypatch.setenv("REGIME_FAST_SESSION_HALT_PCT", "0.50")
    assert session_halt_usd_from_env() == pytest.approx(500.0)
    assert rolling_mean_max_from_env() == pytest.approx(-300.0)


def test_session_halt_blocks(tmp_path):
    gate = RegimeFastGate(
        "acct",
        state_dir=tmp_path,
        session_halt_usd=400.0,
        weekly_mgc_loss_usd=300.0,
    )
    ts = datetime(2026, 7, 7, 15, 0, tzinfo=timezone.utc)
    gate.record_trade("morning_range_reversion", "MGC", -450.0, exit_time=ts)
    mult, reason = gate.resolve_multiplier("morning_range_reversion", "MGC")
    assert mult == 0.0
    assert "session halt" in reason


def test_rolling_throttle_after_three_bad_sessions(tmp_path):
    gate = RegimeFastGate(
        "acct",
        state_dir=tmp_path,
        session_halt_usd=500.0,
        rolling_window=3,
        rolling_warmup=3,
        rolling_mean_max=0.0,
        throttle_duration=5,
    )
    base = datetime(2026, 6, 1, 14, 0, tzinfo=timezone.utc)
    for i in range(3):
        gate.record_trade(
            "morning_range_reversion",
            "MNQ",
            -100.0,
            exit_time=base.replace(day=1 + i),
        )
    # Fourth session finalizes the third completed session and arms throttle.
    gate.record_trade(
        "morning_range_reversion",
        "MNQ",
        -50.0,
        exit_time=base.replace(day=4),
    )
    mult, reason = gate.resolve_multiplier("morning_range_reversion", "MNQ")
    assert mult == 0.5
    assert "roll3" in reason


def test_weekly_mgc_throttle(tmp_path):
    gate = RegimeFastGate(
        "acct",
        state_dir=tmp_path,
        session_halt_usd=500.0,
        weekly_mgc_loss_usd=300.0,
        weekly_mgc_enabled=True,
    )
    # Week 23 — bad MGC week
    gate.record_trade(
        "morning_range_reversion",
        "MGC",
        -350.0,
        exit_time=datetime(2026, 6, 3, 14, 0, tzinfo=timezone.utc),
    )
    # Week 24 — roll week via first trade, then MGC should throttle
    gate.record_trade(
        "morning_range_reversion",
        "MNQ",
        50.0,
        exit_time=datetime(2026, 6, 10, 14, 0, tzinfo=timezone.utc),
    )
    mult, reason = gate.resolve_multiplier("morning_range_reversion", "MGC")
    assert mult == 0.5
    assert "mgc week" in reason


def test_apply_quantity_min_one_contract(tmp_path):
    gate = RegimeFastGate(
        "acct",
        state_dir=tmp_path,
        session_halt_usd=500.0,
    )
    st = gate._strategy_state("morning_range_reversion")
    st.throttle_sessions_left = 3
    qty, _ = gate.apply_quantity("morning_range_reversion", "MGC", 2)
    assert qty == 1


def test_record_trade_persists(tmp_path):
    gate = RegimeFastGate("acct1", state_dir=tmp_path, session_halt_usd=500.0)
    ts = datetime(2026, 7, 7, 15, 0, tzinfo=timezone.utc)
    gate.record_trade("morning_range_reversion", "MGC", 120.0, exit_time=ts)
    gate2 = RegimeFastGate("acct1", state_dir=tmp_path, session_halt_usd=500.0)
    st = gate2._strategy_state("morning_range_reversion")
    assert st.current.net_pnl == 120.0
