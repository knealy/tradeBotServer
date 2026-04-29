"""Smoke: ``import core.backtest`` stays light; loader/engine instantiate without pandas."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_subprocess_import_core_backtest_before_numpy_pandas():
    code = r"""
import sys
from pathlib import Path
root = Path(sys.argv[1])
sys.path.insert(0, str(root))
import core.backtest
assert "pandas" not in sys.modules, "pandas loaded on package import"
assert "numpy" not in sys.modules, "numpy loaded on package import"
from core.backtest import HistoricalDataLoader, BacktestEngine
assert "pandas" not in sys.modules
assert "numpy" not in sys.modules
_ = HistoricalDataLoader()
_ = BacktestEngine()
assert "pandas" not in sys.modules
assert "numpy" not in sys.modules
"""
    proc = subprocess.run(
        [sys.executable, "-c", code, str(ROOT)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_backtest_lazy_exports_performance_metrics_no_pandas_until_use():
    """Importing MonteCarloSimulator loads monte_carlo module; metrics not loaded until simulation runs."""
    code = r"""
import sys
from pathlib import Path
root = Path(sys.argv[1])
sys.path.insert(0, str(root))
from core.backtest import MonteCarloSimulator
from core.backtest.models import BacktestTrade, OrderSide
from datetime import datetime, timezone
# monte_carlo should not import metrics at import time
assert "core.backtest.metrics" not in sys.modules
mc = MonteCarloSimulator(initial_capital=10000.0)
now = datetime.now(timezone.utc)
t = BacktestTrade(
    trade_id="1",
    symbol="MNQ",
    side=OrderSide.BUY,
    entry_time=now,
    exit_time=now,
    entry_price=100.0,
    exit_price=101.0,
    quantity=1,
    pnl=10.0,
    pnl_percent=1.0,
    commission=0.0,
    slippage=0.0,
    bars_held=1,
    exit_reason="signal",
)
mc.run_simulations([t], num_simulations=3, seed=42)
assert "pandas" in sys.modules or "numpy" in sys.modules
"""
    proc = subprocess.run(
        [sys.executable, "-c", code, str(ROOT)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
