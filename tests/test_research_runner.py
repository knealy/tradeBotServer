"""Tests for core.research runner (grid parse, MC gate, small sample run)."""

import pytest

from core.research.runner import (
    ResearchRunConfig,
    parse_param_grid,
    _mc_gate,
    run_research,
)


def test_parse_param_grid():
    g = parse_param_grid("fast_period=10:14:2,slow_period=50:50:1")
    assert g["fast_period"] == [10.0, 12.0, 14.0]
    assert g["slow_period"] == [50.0]


def test_mc_gate():
    ok, _ = _mc_gate({"probability_of_profit": 0.9, "mean_max_drawdown": 5.0}, 0.5, 30.0)
    assert ok
    bad, reason = _mc_gate({"probability_of_profit": 0.1, "mean_max_drawdown": 5.0}, 0.5, 30.0)
    assert not bad and "profit_prob" in reason
    bad2, reason2 = _mc_gate({"probability_of_profit": 0.9, "mean_max_drawdown": 50.0}, 0.5, 30.0)
    assert not bad2 and "mean_max_dd" in reason2


@pytest.mark.asyncio
async def test_run_research_smoke():
    cfg = ResearchRunConfig(
        strategy="ma_crossover",
        symbol="MNQ",
        timeframe="5m",
        days=10,
        min_oos_bars=15,
        oos_fraction=0.25,
        param_grid={},
        mc_simulations=15,
        mc_min_profit_prob=0.0,
        mc_max_mean_dd_pct=100.0,
        persist_db=False,
    )
    out = await run_research(cfg)
    assert out["strategy"] == "ma_crossover"
    assert len(out["grid_results"]) == 1
    assert "git_sha" in out
