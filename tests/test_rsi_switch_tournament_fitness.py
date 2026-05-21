"""Fitness scoring for RSI switch tournament."""

from scripts.walkforward_rsi_switch_tournament import FoldRow, compute_fitness


def test_fitness_zero_trades_is_very_low():
    score, _ = compute_fitness([])
    assert score < -1e5


def test_fitness_rewards_win_rate_and_trades():
    rows = [
        FoldRow("c", "MNQ", 0, None, None, 100.0, 5, 60.0, 1.0, 50.0),
        FoldRow("c", "MNQ", 1, None, None, 80.0, 5, 55.0, 0.5, 40.0),
    ]
    good, meta = compute_fitness(rows)
    bad_rows = [
        FoldRow("c", "MNQ", 0, None, None, -200.0, 5, 25.0, -1.0, 200.0),
        FoldRow("c", "MNQ", 1, None, None, -150.0, 5, 20.0, -0.5, 180.0),
    ]
    poor, _ = compute_fitness(bad_rows)
    assert good > poor
    assert meta["total_trades"] == 10
