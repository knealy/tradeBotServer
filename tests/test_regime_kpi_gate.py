"""Unit tests for core.regime_kpi_gate."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from core.regime_kpi_gate import (
    KpiBaseline,
    PercentileBand,
    RegimeKpiGate,
    SessionSummary,
    regime_kpi_gate_enabled,
)


@pytest.fixture(autouse=True)
def _enable_kpi_gate(monkeypatch, tmp_path):
    monkeypatch.setenv("REGIME_KPI_GATE_ENABLED", "1")
    monkeypatch.setenv("REGIME_KPI_MIN_SESSIONS", "3")
    monkeypatch.setenv("REGIME_KPI_WINDOW_SESSIONS", "5")


def _baseline() -> KpiBaseline:
    return KpiBaseline(
        session_pnl=PercentileBand(p10=-100.0, p25=-50.0, p50=100.0),
        expectancy=PercentileBand(p10=-80.0, p25=-40.0, p50=80.0),
    )


def test_disabled_passthrough(monkeypatch):
    monkeypatch.delenv("REGIME_KPI_GATE_ENABLED", raising=False)
    assert not regime_kpi_gate_enabled()
    gate = RegimeKpiGate("1", baselines={"s:MGC": _baseline()}, state_dir=Path("/tmp"))
    mult, _ = gate.resolve_multiplier("s", "MGC")
    assert mult == 1.0


def test_warmup_full_size(tmp_path):
    gate = RegimeKpiGate(
        "acct1",
        baselines={"morning_range_reversion:MGC": _baseline()},
        state_dir=tmp_path,
        min_sessions_warmup=5,
    )
    mult, reason = gate.resolve_multiplier("morning_range_reversion", "MGC")
    assert mult == 1.0
    assert "warmup" in reason


def test_below_p25_throttles(tmp_path):
    gate = RegimeKpiGate(
        "acct1",
        baselines={"morning_range_reversion": _baseline()},
        state_dir=tmp_path,
        min_sessions_warmup=3,
        window_sessions=5,
    )
    st = gate._strategy_state("morning_range_reversion")
    st.completed = [
        SessionSummary(session_date=f"2026-06-{10+i}", net_pnl=-80.0, trade_count=1)
        for i in range(4)
    ]
    mult, reason = gate.resolve_multiplier("morning_range_reversion", "MNQ")
    assert mult == 0.5
    assert "below_p25" in reason


def test_below_p10_severe(tmp_path):
    gate = RegimeKpiGate(
        "acct1",
        baselines={"morning_range_reversion": _baseline()},
        state_dir=tmp_path,
        min_sessions_warmup=3,
    )
    st = gate._strategy_state("morning_range_reversion")
    st.completed = [
        SessionSummary(session_date=f"2026-06-{10+i}", net_pnl=-150.0, trade_count=1)
        for i in range(4)
    ]
    mult, _ = gate.resolve_multiplier("morning_range_reversion", "MNQ")
    assert mult == 0.25


def test_record_trade_persists(tmp_path):
    gate = RegimeKpiGate(
        "acct1",
        baselines={"morning_range_reversion": _baseline()},
        state_dir=tmp_path,
    )
    ts = datetime(2026, 7, 7, 15, 0, tzinfo=timezone.utc)
    gate.record_trade("morning_range_reversion", "MGC", 120.0, exit_time=ts)
    gate2 = RegimeKpiGate(
        "acct1",
        baselines={"morning_range_reversion": _baseline()},
        state_dir=tmp_path,
    )
    st = gate2._strategy_state("morning_range_reversion")
    assert st.current.net_pnl == 120.0
    assert st.current.trade_count == 1


def test_apply_quantity_blocks_on_halt(tmp_path, monkeypatch):
    monkeypatch.setenv("REGIME_KPI_HALT_BELOW_P10", "1")
    gate = RegimeKpiGate(
        "acct1",
        baselines={"morning_range_reversion": _baseline()},
        state_dir=tmp_path,
        min_sessions_warmup=2,
    )
    st = gate._strategy_state("morning_range_reversion")
    st.completed = [
        SessionSummary(session_date="2026-06-10", net_pnl=-200.0, trade_count=1),
        SessionSummary(session_date="2026-06-11", net_pnl=-200.0, trade_count=1),
    ]
    qty, reason = gate.apply_quantity("morning_range_reversion", "MGC", 4)
    assert qty == 0
    assert "blocked" in reason
