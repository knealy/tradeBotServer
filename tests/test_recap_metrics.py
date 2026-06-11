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


def test_extended_insights_r_outlier_cap():
    """Variable-stop strategies (overnight_reversion, MGC vs MNQ size
    deltas) can produce trades whose ``initial_risk_dollars`` is
    near-zero, blowing up the per-trade R multiple to absurd values.

    The 2026-06-11 arsenal sanity check found ``overnight_reversion``
    showing ``avg_r_winners=+850`` from a single trade with tiny
    recorded risk dollars and a normal-sized win.  The recap now
    clips per-trade R at ±10R before averaging, plus surfaces median
    and the unclipped raw mean for transparency.

    This pins the contract: with one trade at +850R (tiny risk) and
    one trade at +2R (normal risk), the CLIPPED avg_r_winners must
    be (10 + 2) / 2 = 6.0 (NOT 426 which is the unclipped mean).
    The unclipped mean must still be available via
    ``avg_r_winners_unclipped`` for transparency.
    """
    # Trade A: $850 win on $1 of recorded risk → +850R (pathological)
    # Trade B: $200 win on $100 of recorded risk → +2R (normal)
    trades = [
        _trade(
            entry_iso="2026-01-01T10:00:00+00:00",
            exit_iso="2026-01-01T11:00:00+00:00",
            pnl=850.0, risk=1.0,
        ),
        _trade(
            entry_iso="2026-01-02T10:00:00+00:00",
            exit_iso="2026-01-02T11:00:00+00:00",
            pnl=200.0, risk=100.0,
        ),
    ]
    ext = extended_performance_insights(trades)
    # Primary metric: clipped mean = (10 + 2) / 2 = 6.0
    assert ext["avg_r_winners"] == 6.0, ext
    # Unclipped diagnostic: (850 + 2) / 2 = 426.0
    assert ext["avg_r_winners_unclipped"] == 426.0, ext
    # Median is unaffected by single tail: median of {850, 2} = 426.
    # Note: median of 2 values is the mean of the two — so median == unclipped mean here.
    # The point of MEDIAN is to be robust as N grows.
    assert ext["median_r_winners"] == 426.0, ext
    # Exactly one trade was clipped.
    assert ext["n_clipped_r_winners"] == 1, ext
    assert ext["n_clipped_r_losers"] == 0, ext
    # No losses → losers field is None (None < anything is undefined; just check no crash).
    assert ext["avg_r_losers"] is None, ext
    assert ext["r_cap"] == 10.0


def test_extended_insights_r_cap_symmetric_for_losers():
    """A loser whose initial_risk_dollars was tiny shows up as huge
    negative R.  Cap must apply symmetrically at -10R."""
    trades = [
        _trade(
            entry_iso="2026-01-01T10:00:00+00:00",
            exit_iso="2026-01-01T11:00:00+00:00",
            pnl=-500.0, risk=2.0,  # -250R, capped to -10R
        ),
        _trade(
            entry_iso="2026-01-02T10:00:00+00:00",
            exit_iso="2026-01-02T11:00:00+00:00",
            pnl=-100.0, risk=100.0,  # -1R normal
        ),
    ]
    ext = extended_performance_insights(trades)
    # Clipped mean: (-10 + -1) / 2 = -5.5
    assert ext["avg_r_losers"] == -5.5, ext
    # Unclipped: (-250 + -1) / 2 = -125.5
    assert ext["avg_r_losers_unclipped"] == -125.5, ext
    assert ext["n_clipped_r_losers"] == 1, ext
    assert ext["n_clipped_r_winners"] == 0, ext


def test_extended_insights_r_metrics_unchanged_when_no_outliers():
    """For well-behaved strategies (every trade has reasonable
    initial_risk_dollars), clipped == unclipped — no behavioural
    change from the cap, so existing dashboards see identical values."""
    trades = [
        _trade(entry_iso="2026-01-01T10:00:00+00:00", exit_iso="2026-01-01T11:00:00+00:00",
               pnl=200.0, risk=100.0),  # +2R
        _trade(entry_iso="2026-01-02T10:00:00+00:00", exit_iso="2026-01-02T11:00:00+00:00",
               pnl=-100.0, risk=100.0),  # -1R
        _trade(entry_iso="2026-01-03T10:00:00+00:00", exit_iso="2026-01-03T11:00:00+00:00",
               pnl=300.0, risk=150.0),  # +2R
    ]
    ext = extended_performance_insights(trades)
    assert ext["avg_r_winners"] == ext["avg_r_winners_unclipped"], ext
    assert ext["avg_r_losers"] == ext["avg_r_losers_unclipped"], ext
    assert ext["n_clipped_r_winners"] == 0
    assert ext["n_clipped_r_losers"] == 0


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
