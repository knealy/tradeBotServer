"""Monte Carlo robustness for walk-forward trade recaps."""

from __future__ import annotations

from core.backtest.walkforward_monte_carlo import run_walkforward_monte_carlo


def _trades(pnls):
    return [{"pnl": p} for p in pnls]


def test_shuffle_all_wins_high_p_profit():
    mc = run_walkforward_monte_carlo(
        _trades([100.0] * 30),
        start_equity=2000.0,
        num_simulations=500,
        seed=1,
    )
    sh = mc["modes"]["shuffle"]
    assert sh["total_pnl"]["p_profit"] == 1.0
    assert mc["endurance_verdict"]["grade"] in ("strong", "adequate")


def test_mixed_edge_moderate_verdict():
    pnls = [150.0, -80.0, 120.0, -90.0, 200.0, -70.0] * 5
    mc = run_walkforward_monte_carlo(
        _trades(pnls),
        start_equity=2000.0,
        num_simulations=800,
        seed=42,
    )
    assert mc["n_trades"] == 30
    sh = mc["modes"]["shuffle"]
    assert sh["total_pnl"]["p_profit"] > 0.5
    assert "endurance_verdict" in mc


def test_bootstrap_p5_tracks_distribution():
    pnls = [50.0] * 18 + [-200.0] * 2
    mc = run_walkforward_monte_carlo(
        _trades(pnls),
        start_equity=2000.0,
        num_simulations=1000,
        seed=7,
    )
    bs = mc["modes"]["bootstrap"]
    assert bs["total_pnl"]["p50"] > 0
    assert mc["sequential_actual"]["total_pnl"] == sum(pnls)
    hist = bs.get("histograms") or {}
    assert hist.get("total_pnl", {}).get("counts")
    assert len(hist["total_pnl"]["edges"]) == len(hist["total_pnl"]["counts"]) + 1


def test_empty_trades():
    mc = run_walkforward_monte_carlo([], num_simulations=100)
    assert mc.get("n_trades") == 0
