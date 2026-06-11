"""Tests for ``scripts/simulate_price_action_trades.py`` exit mechanics.

Focused on the adaptive-exit additions: trailing stop + partial profit
+ stress-test matrix.  The baseline fixed-TP path is tested implicitly
via the broader integration runs.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

# Load the script as a module so we can call its private helpers.
_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "simulate_price_action_trades.py"
spec = importlib.util.spec_from_file_location("simulate_pa", _SCRIPT)
simulate_pa = importlib.util.module_from_spec(spec)
sys.modules["simulate_pa"] = simulate_pa
spec.loader.exec_module(simulate_pa)

_simulate_trade = simulate_pa._simulate_trade
CandleBar = simulate_pa.CandleBar


T0 = datetime(2026, 6, 10, 9, 30)


def _bar(open_, high, low, close, offset: int = 0) -> CandleBar:
    return CandleBar(
        timestamp=T0 + timedelta(minutes=offset * 5),
        open=open_, high=high, low=low, close=close, volume=100.0,
    )


# ─────────────────────── baseline (fixed TP) ────────────────────────


def test_fixed_tp_long_take_profit():
    bars = [_bar(100, 101, 99, 100, 0)]
    # 5 forward bars; TP is +2 points at 102.  Bar 3 high reaches 102.
    bars += [_bar(100, 100.5, 99.5, 100, 1),
              _bar(100, 101.5, 99.5, 101, 2),
              _bar(101, 102.5, 100.5, 102, 3),  # tp hit
              _bar(102, 103, 101, 102, 4)]
    r, reason, held = _simulate_trade(bars, 0, side=+1, stop_dist=1.0, tp_dist=2.0, max_bars=10)
    assert reason == "take_profit"
    assert r == pytest.approx(2.0)
    assert held == 3


def test_fixed_tp_long_stop_loss():
    bars = [_bar(100, 101, 99, 100, 0),
            _bar(100, 100, 98.9, 99, 1)]   # bar 1 low pierces SL (entry-1=99)
    r, reason, _ = _simulate_trade(bars, 0, side=+1, stop_dist=1.0, tp_dist=2.0, max_bars=10)
    assert reason == "stop_loss"
    assert r == pytest.approx(-1.0)


def test_fixed_tp_timed_exit_partial_pnl():
    # Trade runs to end without hitting SL or TP.
    bars = [_bar(100, 100.5, 99.5, 100, 0)]
    bars += [_bar(100, 101, 99.5, 100.5, i) for i in range(1, 6)]
    r, reason, held = _simulate_trade(bars, 0, side=+1, stop_dist=1.0, tp_dist=2.0, max_bars=5)
    assert reason == "timed_exit"
    # Final close = 100.5; entry = 100; r_pnl = 0.5
    assert r == pytest.approx(0.5)
    assert held == 5


# ─────────────────────── trailing stop ──────────────────────────────


def test_trailing_activates_after_trigger_mfe():
    """Verify trailing stop activates after MFE >= trail_trigger_r * stop_dist."""
    # Entry at 100, stop_dist=1.0 (SL at 99), trail at 0.5pt after 1R MFE.
    # Trade should run up: bar 1 high=102 (MFE >= 2R → trailing on),
    # close=101.5 → trailing SL = 101.5 - 0.5 = 101.0.
    # Bar 2 drops to low=100.9 — TRIGGERS trailing SL at 101.0 (sl_hit).
    bars = [_bar(100, 100.5, 99.5, 100, 0),
            _bar(100, 102, 99.5, 101.5, 1),   # MFE = 2 (2R), trailing activates
            _bar(101.5, 101.7, 100.9, 101, 2),  # low = 100.9 hits sl=101.0
            _bar(101, 102, 100.8, 101.5, 3)]
    r, reason, held = _simulate_trade(
        bars, 0, side=+1, stop_dist=1.0, tp_dist=5.0, max_bars=10,
        trail_atr_dist=0.5, trail_trigger_r=1.0,
    )
    # Trailing locked in 101.0; (101.0 - 100.0) / 1.0 = +1.0 R
    assert reason == "trailed_out"
    assert r == pytest.approx(1.0)
    assert held == 2


def test_trailing_does_not_activate_below_trigger():
    """If MFE never reaches trigger, trailing should not activate and
    the trade exits via normal SL/TP/timed mechanism."""
    bars = [_bar(100, 100.2, 99.5, 100, 0),
            _bar(100, 100.4, 99.5, 100.2, 1),  # MFE=0.4 (0.4R), below trigger=1R
            _bar(100.2, 100.5, 99.5, 100.3, 2),
            _bar(100.3, 100.4, 99.5, 100.2, 3)]
    r, reason, _ = _simulate_trade(
        bars, 0, side=+1, stop_dist=1.0, tp_dist=2.0, max_bars=10,
        trail_atr_dist=0.5, trail_trigger_r=1.0,
    )
    # No trailing, no SL hit, no TP hit → timed exit at final close 100.2
    assert reason == "timed_exit"
    assert r == pytest.approx(0.2)


def test_trailing_only_moves_favorably():
    """Trailing SL must never move AGAINST the position (e.g., after a
    pullback the SL shouldn't drop back down)."""
    # Entry 100, SL 99.  bar1 hits 102 (2R MFE), close=101.5 →
    # trail_sl = 101 (101.5 - 0.5).  bar2 drops to close=100.5 →
    # would naively be trail_sl = 100 (100.5 - 0.5) BUT must stay at 101.
    # bar3 then pierces 100.5 — should NOT trigger 100 SL; should
    # trigger 101 SL.
    bars = [_bar(100, 100.5, 99.5, 100, 0),
            _bar(100, 102, 99.5, 101.5, 1),    # trail SL set to 101.0
            _bar(101.5, 101.6, 100.5, 100.7, 2),  # close=100.7 → would set SL=100.2; but kept at 101 → SL hit
            _bar(100.7, 100.8, 99.0, 99.5, 3)]
    r, reason, held = _simulate_trade(
        bars, 0, side=+1, stop_dist=1.0, tp_dist=5.0, max_bars=10,
        trail_atr_dist=0.5, trail_trigger_r=1.0,
    )
    # SL at 101.0 was hit on bar 2 (low=100.5).
    assert reason == "trailed_out"
    assert r == pytest.approx(1.0)
    assert held == 2


# ─────────────────────── partial profit ─────────────────────────────


def test_partial_profit_at_1r_then_breakeven():
    """Close 50% at 1R, runner at BE.  Bar 2 hits the partial level
    (close runs to 101.5 with high=102 = +2R MFE).  Then bar 3 drops
    to entry (100) - SL hits BE → runner exits at 0.  Total = 1*0.5 + 0*0.5 = 0.5R."""
    bars = [_bar(100, 100.5, 99.5, 100, 0),
            _bar(100, 100.8, 99.7, 100.5, 1),  # below partial 1R level (101)
            _bar(100.5, 102, 100, 101.5, 2),   # hits partial at 101 → half closed; SL → 100 (BE)
            _bar(101.5, 101.7, 99.5, 100, 3),  # bar3 low=99.5 hits BE sl=100; runner = 0R
            _bar(100, 100, 99, 99.5, 4)]
    r, reason, held = _simulate_trade(
        bars, 0, side=+1, stop_dist=1.0, tp_dist=5.0, max_bars=10,
        partial_tp_r=1.0, partial_frac=0.5,
    )
    assert r == pytest.approx(0.5)
    # Runner exited at BE (sl_px == entry), so r_runner = 0; reason
    # should be "breakeven_out".
    assert reason == "breakeven_out"
    assert held == 3


def test_partial_profit_runner_to_tp():
    """Close 50% at 1R; runner runs to full TP (2R).  Total = 1*0.5 + 2*0.5 = 1.5R."""
    bars = [_bar(100, 100.5, 99.5, 100, 0),
            _bar(100, 100.8, 99.7, 100.5, 1),
            _bar(100.5, 102.5, 100, 102, 2),  # bar2 high=102.5 hits TP=102 (entry+2)
            _bar(102, 103, 101.5, 102.5, 3)]
    r, reason, _ = _simulate_trade(
        bars, 0, side=+1, stop_dist=1.0, tp_dist=2.0, max_bars=10,
        partial_tp_r=1.0, partial_frac=0.5,
    )
    # Bar 2 hits partial (high=102.5 >= 101) → partial 0.5R; SL→100.
    # Bar 2 ALSO hits TP (high>=102 in same bar).  Algorithm checks partial
    # before SL/TP, so partial fires.  Then TP check fires same bar →
    # runner gets +2R; total = 1*0.5 + 2*0.5 = 1.5R.
    assert reason == "take_profit"
    assert r == pytest.approx(1.5)


# ─────────────────────── partial + trail combined ───────────────────


def test_partial_and_trailing_combined():
    """Both modes on: partial fires at 1R, then trailing kicks in for the runner."""
    bars = [_bar(100, 100.5, 99.5, 100, 0),
            _bar(100, 100.8, 99.7, 100.5, 1),
            _bar(100.5, 102, 100, 101.5, 2),   # partial at 101 → 0.5R, sl_px = entry=100; trailing activates (MFE=2 >= 1R), trail sl_px=101
            _bar(101.5, 101.7, 100.5, 100.7, 3),  # low=100.5 hits trail sl=101 → runner = +1R
            _bar(100.7, 100.8, 100, 100.5, 4)]
    r, reason, _ = _simulate_trade(
        bars, 0, side=+1, stop_dist=1.0, tp_dist=5.0, max_bars=10,
        trail_atr_dist=0.5, trail_trigger_r=1.0,
        partial_tp_r=1.0, partial_frac=0.5,
    )
    # Partial 0.5 * 1.0 = 0.5R, runner 0.5 * 1.0 = 0.5R, total = 1.0R
    assert r == pytest.approx(1.0)
    assert reason == "trailed_out"
