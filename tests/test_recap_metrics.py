"""Unit tests for walk-forward recap metrics (equity sim + loss diagnostics)."""

from __future__ import annotations

from core.backtest.recap_metrics import (
    equity_curve_from_trades,
    extended_performance_insights,
    loss_pattern_analysis,
    sort_trades_by_exit_time,
)


def _trade(
    *,
    exit_iso: str,
    entry_iso: str,
    pnl: float,
    side: str = "BUY",
    bars: int = 5,
    reason: str = "take_profit",
    risk: float = 100.0,
) -> dict:
    return {
        "trade_id": "T1",
        "symbol": "MNQ",
        "side": side,
        "entry_time": entry_iso,
        "exit_time": exit_iso,
        "entry_price": 25000.0,
        "exit_price": 25010.0,
        "quantity": 1,
        "pnl": pnl,
        "pnl_percent": 0.0,
        "commission": 0.0,
        "slippage": 0.0,
        "bars_held": bars,
        "exit_reason": reason,
        "max_favorable_excursion": 20.0,
        "max_adverse_excursion": 10.0,
        "initial_risk_dollars": risk,
    }


def test_sort_trades_by_exit_time():
    a = _trade(entry_iso="2026-01-01T10:00:00+00:00", exit_iso="2026-01-01T12:00:00+00:00", pnl=1)
    b = _trade(entry_iso="2026-01-01T09:00:00+00:00", exit_iso="2026-01-01T11:00:00+00:00", pnl=2)
    out = sort_trades_by_exit_time([a, b])
    assert out[0]["exit_time"].endswith("T11:00:00+00:00")


def test_equity_curve_start_and_steps():
    t0 = _trade(
        entry_iso="2026-03-01T14:00:00+00:00",
        exit_iso="2026-03-01T15:00:00+00:00",
        pnl=50.0,
    )
    t1 = _trade(
        entry_iso="2026-03-01T16:00:00+00:00",
        exit_iso="2026-03-01T17:00:00+00:00",
        pnl=-30.0,
    )
    pts, summ = equity_curve_from_trades([t0, t1], start_equity=2000.0)
    assert summ["start_equity"] == 2000.0
    assert summ["final_equity"] == 2020.0
    assert summ["n_trades"] == 2
    assert pts[0]["equity"] == 2000.0
    assert pts[-1]["equity"] == 2020.0


def test_extended_insights_breakeven_and_recovery():
    trades = [
        _trade(
            entry_iso=f"2026-01-0{i}T10:00:00+00:00",
            exit_iso=f"2026-01-0{i}T11:00:00+00:00",
            pnl=100.0 if i % 2 == 0 else -40.0,
        )
        for i in range(1, 7)
    ]
    ext = extended_performance_insights(trades)
    assert ext["n_trades"] == 6
    assert ext["breakeven_win_rate"] is not None
    assert ext["recovery_factor_pnl_vs_seq_dd"] is not None


def test_loss_patterns_exit_reason_and_side():
    trades = [
        _trade(
            entry_iso="2026-01-01T10:00:00+00:00",
            exit_iso="2026-01-01T11:00:00+00:00",
            pnl=-10,
            side="BUY",
            reason="stop_loss",
        ),
        _trade(
            entry_iso="2026-01-02T10:00:00+00:00",
            exit_iso="2026-01-02T11:00:00+00:00",
            pnl=-10,
            side="SELL",
            reason="stop_loss",
        ),
        _trade(
            entry_iso="2026-01-03T10:00:00+00:00",
            exit_iso="2026-01-03T11:00:00+00:00",
            pnl=50,
            side="BUY",
            reason="take_profit",
        ),
    ]
    lp = loss_pattern_analysis(trades)
    assert lp["n_losses"] == 2
    assert "stop_loss" in lp["by_exit_reason"]
    assert lp["by_side_loss_rate"]["BUY"]["loss_rate_pct"] == 50.0
