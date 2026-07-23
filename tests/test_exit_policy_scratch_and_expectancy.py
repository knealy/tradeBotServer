"""Unit tests for MRR scratch / unrealized-R helper."""
from types import SimpleNamespace

from core.backtest.models import OrderSide
from strategies.morning_range_reversion_strategy import MorningRangeReversionStrategy


def test_unrealized_r_long_short():
    long_pos = SimpleNamespace(
        side=OrderSide.BUY,
        entry_price=100.0,
        stop_loss=90.0,
        current_price=105.0,
    )
    assert MorningRangeReversionStrategy._unrealized_r(long_pos) == 0.5

    short_pos = SimpleNamespace(
        side=OrderSide.SELL,
        entry_price=100.0,
        stop_loss=110.0,
        current_price=95.0,
    )
    assert MorningRangeReversionStrategy._unrealized_r(short_pos) == 0.5

    underwater = SimpleNamespace(
        side=OrderSide.BUY,
        entry_price=100.0,
        stop_loss=90.0,
        current_price=95.0,
    )
    assert MorningRangeReversionStrategy._unrealized_r(underwater) == -0.5


def test_conditional_expectancy_bars_bucket():
    from scripts.conditional_expectancy_report import build_report

    trades = [
        {"strategy": "morning_range_reversion", "symbol": "MGC", "pnl": 100, "bars_held": 5, "exit_reason": "take_profit", "side": "BUY", "entry_time": "2026-02-01T15:00:00Z"},
        {"strategy": "morning_range_reversion", "symbol": "MGC", "pnl": -50, "bars_held": 20, "exit_reason": "timeout", "side": "SELL", "entry_time": "2026-02-02T15:00:00Z"},
        {"strategy": "overnight_range", "symbol": "MNQ", "pnl": -10, "bars_held": 1, "exit_reason": "stop_loss", "side": "BUY", "entry_time": "2026-02-03T15:00:00Z"},
    ]
    rep = build_report(trades)
    assert rep["n_trades"] == 3
    assert "4-8" in rep["by_strategy"]["morning_range_reversion"]["bars_bucket"]
    assert "16+" in rep["by_strategy"]["morning_range_reversion"]["bars_bucket"]
