"""Smoke tests for scripts/strategy_parameter_sweep.py manifest planning."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "config" / "perf_sweep" / "default_wave_manifest.json"


def test_default_manifest_loads():
    with MANIFEST.open("r", encoding="utf-8") as f:
        data = json.load(f)
    assert "waves" in data and len(data["waves"]) >= 1
    w0 = data["waves"][0]
    assert w0.get("id")
    assert w0.get("strategy")
    assert w0.get("runs")


@pytest.mark.skipif(not MANIFEST.is_file(), reason="default manifest missing")
def test_parameter_sweep_dry_run_one_wave():
    proc = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "strategy_parameter_sweep.py"),
            "--manifest",
            str(MANIFEST),
            "--waves",
            "overnight_atr_bracket",
            "--dry-run",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "overnight_atr_bracket" in proc.stderr


def test_backtest_result_avg_reward_risk_from_trades():
    from datetime import datetime, timezone

    from core.backtest.engine import BacktestEngine
    from core.backtest.models import BacktestTrade, OrderSide

    ts = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    e = BacktestEngine(initial_capital=50_000.0, point_value=2.0)
    e.trades = [
        BacktestTrade(
            trade_id="T1",
            symbol="MNQ",
            side=OrderSide.BUY,
            entry_time=ts,
            exit_time=ts,
            entry_price=100.0,
            exit_price=101.0,
            quantity=1,
            pnl=100.0,
            pnl_percent=0.0,
            commission=0.0,
            slippage=0.0,
            bars_held=1,
            exit_reason="take_profit",
            initial_risk_dollars=50.0,
        ),
        BacktestTrade(
            trade_id="T2",
            symbol="MNQ",
            side=OrderSide.BUY,
            entry_time=ts,
            exit_time=ts,
            entry_price=100.0,
            exit_price=99.0,
            quantity=1,
            pnl=-50.0,
            pnl_percent=0.0,
            commission=0.0,
            slippage=0.0,
            bars_held=1,
            exit_reason="stop_loss",
            initial_risk_dollars=50.0,
        ),
    ]
    e.equity_curve = [(ts, 50_000.0), (ts, 50_050.0)]
    r = e._build_result("MNQ", "unit", ts, ts)
    assert r.avg_reward_risk == pytest.approx(0.5)
    d = r.to_dict()
    assert d["avg_reward_risk"] == pytest.approx(0.5)
