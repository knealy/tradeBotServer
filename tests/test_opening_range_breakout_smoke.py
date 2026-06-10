"""Pinning tests for ``OpeningRangeBreakoutStrategy``.

Locks the committed TOML defaults + the core analyze/execute mechanics
so future config sweeps can't silently drift the strategy out of spec.
"""

from __future__ import annotations

import pytest

import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
CFG = ROOT / "config/strategies/opening_range_breakout.toml"


def _load_toml():
    with CFG.open("rb") as fh:
        return tomllib.load(fh)


# ─────────────────────── TOML pinning tests ────────────────────────


def test_committed_toml_exists():
    assert CFG.exists(), f"Missing TOML: {CFG}"


def test_committed_meta_block():
    data = _load_toml()
    meta = data.get("meta", {})
    # PROMOTED 2026-06-09 — R1 MNQ-focused sweep found 60-min + long-only +
    # skip-Fri beats overnight_range R24 on 3m truth (RF 4.67 vs 2.20).
    assert meta.get("enabled") is True
    # Live breaker bridge present from day 1 (don't have a silent no-op
    # like the overnight_reversion bug found 2026-06-09).
    assert meta.get("live_breaker_enabled") is True
    # MNQ-only at promotion — MGC ORB consistently loses on current data.
    assert meta.get("symbols") == ["MNQ"]
    assert meta.get("timeframe") == "5m"


def test_committed_signal_block_defaults():
    data = _load_toml()
    sig = data.get("signal", {})
    assert sig.get("timeframe") == "5m"
    assert sig.get("session_timezone") == "America/New_York"
    # Promoted window: 60-min ORB (09:30 → 10:30).  Sweep evidence in
    # docs/perf/_opt_runs/orb_strategy_mnq_focused/ — shorter windows
    # (15/30/45 min) all fail to beat overnight_range R24.
    assert sig.get("range_start") == "09:30"
    assert sig.get("range_end_open") == "10:30"
    assert sig.get("flat_before") == "15:55"
    # Default geometry: range-width fraction (self-scaling per session).
    assert sig.get("use_atr_geometry") is False
    assert pytest.approx(sig.get("stop_range_pct"), abs=1e-6) == 0.5
    assert pytest.approx(sig.get("tp_range_pct"), abs=1e-6) == 1.0
    # LONG ONLY — R1 confirmed short-side ORB is broken in current regime
    # (both-sides RF 0.47 vs long-only RF 0.86 on 9m).
    assert sig.get("allow_long") is True
    assert sig.get("allow_short") is False


def test_committed_filters_skip_friday():
    data = _load_toml()
    filt = data.get("filters", {})
    # Friday WR is materially lower than Mon-Thu (49 % vs 56 %) — same
    # Friday-fade pattern documented across the arsenal.
    assert filt.get("skip_weekdays") == [4]


def test_committed_risk_block():
    data = _load_toml()
    risk = data.get("risk", {})
    # Max-positions cap forces only one ORB per session (per symbol).
    assert risk.get("position_size") == 1
    assert risk.get("max_positions") == 1
    assert risk.get("max_daily_trades") == 1


def test_committed_executor_window():
    data = _load_toml()
    # Executor wall-clock window MUST start before signal.range_start
    # so the bar feed is alive when build window opens.
    assert data.get("start_time") == "09:25"
    assert data.get("end_time") == "16:00"


def test_committed_mgc_mes_disabled():
    """Per-symbol position_size=0 explicitly disables MGC and MES.

    R1 sweep: MGC ORB consistently negative (~-$45 / fold).  MES not yet
    tested — left disabled pending a MES-focused sweep.  Promotion-time
    invariant: if you add a symbol back, prove it on truth first.
    """
    data = _load_toml()
    assert data.get("symbols", {}).get("MGC", {}).get("risk", {}).get("position_size") == 0
    assert data.get("symbols", {}).get("MES", {}).get("risk", {}).get("position_size") == 0


# ────────────────────── strategy module sanity ─────────────────────


def test_strategy_module_imports():
    from strategies.opening_range_breakout_strategy import OpeningRangeBreakoutStrategy
    assert OpeningRangeBreakoutStrategy.NAME == "opening_range_breakout"


def test_strategy_registered_in_manager():
    from strategies.strategy_manager import BUILTIN_STRATEGY_SPECS
    spec = BUILTIN_STRATEGY_SPECS.get("opening_range_breakout")
    assert spec is not None
    assert spec[0] == "strategies.opening_range_breakout_strategy"
    assert spec[1] == "OpeningRangeBreakoutStrategy"


def test_strategy_registered_in_backtest_executor():
    from core.backtest_executor import BacktestExecutor
    # _get_strategy_class is on the class; it walks an elif chain.
    # Cheap sanity: try to grab the class through it.
    bx = BacktestExecutor.__new__(BacktestExecutor)
    cls = bx._get_strategy_class("opening_range_breakout")
    assert cls is not None
    assert cls.__name__ == "OpeningRangeBreakoutStrategy"
