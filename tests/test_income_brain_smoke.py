"""Smoke tests for core.income_brain — sizing, halt, persistence, CLI."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from core.income_brain import (
    DailySessionGoal,
    EquityTierSizer,
    IncomeBrain,
    build_from_env,
    is_enabled,
)


REPO = Path(__file__).resolve().parent.parent


def test_sizer_clamps_to_min_n_under_cushion():
    s = EquityTierSizer(divisor=2500, cushion=2000, min_n=1, max_n=20)
    assert s.size(1500.0) == 1
    assert s.size(2000.0) == 1


def test_sizer_steps_per_divisor():
    s = EquityTierSizer(divisor=2500, cushion=2000, min_n=1, max_n=20)
    # equity 4500 → usable 2500 → 1
    assert s.size(4500.0) == 1
    # equity 7000 → usable 5000 → 2
    assert s.size(7000.0) == 2
    # equity 9500 → usable 7500 → 3
    assert s.size(9500.0) == 3


def test_sizer_caps_at_max_n():
    s = EquityTierSizer(divisor=2500, cushion=2000, min_n=1, max_n=4)
    assert s.size(1_000_000.0) == 4


def test_sizer_falls_back_to_min_under_hwm_breach():
    s = EquityTierSizer(
        divisor=2500, cushion=2000, min_n=1, max_n=20, hwm_breach_dollars=2000
    )
    # equity 12000 alone → N=4
    assert s.size(12000.0) == 4
    # equity 12000 with hwm 14500 → drawdown 2500 > 2000 → falls back to min_n
    assert s.size(12000.0, hwm=14500.0) == 1


def test_daily_goal_target_and_stop():
    g = DailySessionGoal(daily_target_dollars=400, daily_stop_dollars=-300)
    halt, _ = g.should_halt(0.0)
    assert halt is False
    halt, reason = g.should_halt(401.0)
    assert halt is True and "target" in reason
    halt, reason = g.should_halt(-301.0)
    assert halt is True and "stop" in reason


def test_daily_goal_disabled_when_zero():
    g = DailySessionGoal(daily_target_dollars=0, daily_stop_dollars=0)
    assert g.should_halt(99999.0) == (False, "")
    assert g.should_halt(-99999.0) == (False, "")


def test_brain_persists_state_round_trip(tmp_path: Path):
    brain = IncomeBrain(
        account_id="9",
        sizer=EquityTierSizer(divisor=2500, cushion=2000),
        goal=DailySessionGoal(daily_target_dollars=400, daily_stop_dollars=-300),
        state_dir=tmp_path,
    )
    n = brain.update_equity(10000.0)
    assert n == 3  # (10000 - 2000) / 2500 = 3
    assert brain.state.hwm == 10000.0

    halt, _ = brain.record_realized_pnl(450.0)
    assert halt is True
    assert brain.state.halted_today is True

    # New instance reads same state
    brain2 = IncomeBrain(account_id="9", state_dir=tmp_path)
    assert brain2.state.hwm == 10000.0
    assert brain2.state.realized_pnl_today == 450.0
    assert brain2.state.halted_today is True


def test_brain_decide_size_respects_halt(tmp_path: Path):
    brain = IncomeBrain(
        account_id="9",
        sizer=EquityTierSizer(divisor=2500, cushion=2000),
        goal=DailySessionGoal(daily_target_dollars=200, daily_stop_dollars=-300),
        state_dir=tmp_path,
    )
    brain.update_equity(20000.0)
    assert brain.decide_size() >= 1
    brain.record_realized_pnl(250.0)  # over target
    assert brain.decide_size() == 0


def test_brain_sync_session_pnl_from_broker_sets_halt(tmp_path: Path):
    brain = IncomeBrain(
        account_id="9",
        sizer=EquityTierSizer(divisor=2500, cushion=2000),
        goal=DailySessionGoal(daily_target_dollars=100, daily_stop_dollars=-500),
        state_dir=tmp_path,
    )
    brain.update_equity(8000.0)
    halted, _ = brain.sync_session_pnl_from_broker(150.0)
    assert halted is True
    assert brain.decide_size() == 0


def test_brain_session_roll_archives_pnl(tmp_path: Path):
    brain = IncomeBrain(account_id="9", state_dir=tmp_path)
    brain.update_equity(5000.0)
    brain.record_realized_pnl(123.0)
    prior_session = brain.state.session_date
    assert prior_session != ""
    brain.state.session_date = "1999-01-01"  # simulate stale session
    brain._persist()
    brain.update_equity(5050.0)  # triggers ensure_session → roll
    assert brain.state.session_date != "1999-01-01"
    assert "1999-01-01" in brain.state.history


def test_build_from_env_reads_overrides(monkeypatch):
    monkeypatch.setenv("INCOME_BRAIN", "1")
    monkeypatch.setenv("INCOME_BRAIN_DIVISOR", "1500")
    monkeypatch.setenv("INCOME_BRAIN_CUSHION", "0")
    monkeypatch.setenv("INCOME_BRAIN_DAILY_TARGET", "777")
    monkeypatch.setenv("INCOME_BRAIN_DAILY_STOP", "-555")
    assert is_enabled()
    brain = build_from_env("99")
    assert brain.sizer.divisor == 1500
    assert brain.sizer.cushion == 0
    assert brain.goal.daily_target_dollars == 777
    assert brain.goal.daily_stop_dollars == -555


def test_cli_status_size_record_pnl(tmp_path: Path):
    args_common = [
        sys.executable,
        "-m",
        "core.income_brain",
        "--account",
        "9",
        "--state-dir",
        str(tmp_path),
    ]
    out = subprocess.check_output(args_common + ["status"], cwd=REPO, text=True)
    assert "income_brain status" in out
    out = subprocess.check_output(
        args_common + ["size", "--equity", "10000"], cwd=REPO, text=True
    )
    assert "size at equity" in out
    out = subprocess.check_output(
        args_common + ["record-pnl", "--delta", "100"], cwd=REPO, text=True
    )
    assert "realized_pnl_today" in out
