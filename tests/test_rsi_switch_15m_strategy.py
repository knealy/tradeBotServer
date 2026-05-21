"""Tests for RSI switch helpers."""

from strategies.rsi_switch_15m_strategy import (
    _rsi_edge,
    _rsi_hook,
    decide_exit_or_hold,
)


def test_decide_exit_long_at_opposite_extreme():
    choice = decide_exit_or_hold(
        side="LONG",
        rsi=66.0,
        entry_price=100.0,
        bars_held=4,
        atr=2.0,
        c=100.2,
        rsi_short_min=65.0,
        rsi_long_max=35.0,
        rsi_profit_exit_long=52.0,
        rsi_profit_exit_short=48.0,
        min_hold_bars=2,
        flat_move_atr=0.12,
        loss_cut_atr=0.85,
    )
    assert choice == "EXIT"


def test_decide_exit_long_in_profit_at_mid_rsi():
    choice = decide_exit_or_hold(
        side="LONG",
        rsi=53.0,
        entry_price=100.0,
        bars_held=4,
        atr=2.0,
        c=101.5,
        rsi_short_min=65.0,
        rsi_long_max=35.0,
        rsi_profit_exit_long=52.0,
        rsi_profit_exit_short=48.0,
        min_hold_bars=2,
        flat_move_atr=0.08,
        loss_cut_atr=0.85,
    )
    assert choice == "EXIT"


def test_decide_hold_long_too_young():
    choice = decide_exit_or_hold(
        side="LONG",
        rsi=65.0,
        entry_price=100.0,
        bars_held=1,
        atr=2.0,
        c=100.5,
        rsi_short_min=70.0,
        rsi_long_max=30.0,
        rsi_profit_exit_long=58.0,
        rsi_profit_exit_short=42.0,
        min_hold_bars=3,
        flat_move_atr=0.12,
        loss_cut_atr=0.85,
    )
    assert choice == "HOLD"


def test_decide_cut_loss():
    choice = decide_exit_or_hold(
        side="LONG",
        rsi=25.0,
        entry_price=100.0,
        bars_held=3,
        atr=2.0,
        c=97.0,
        rsi_short_min=70.0,
        rsi_long_max=30.0,
        rsi_profit_exit_long=58.0,
        rsi_profit_exit_short=42.0,
        min_hold_bars=2,
        flat_move_atr=0.12,
        loss_cut_atr=0.85,
    )
    assert choice == "EXIT"


def test_rsi_hook_long_turning_up_from_oversold():
    assert _rsi_hook(31.0, 28.0, long_max=33.0, short_min=66.0, hook_buffer=4.0) == (True, False)


def test_rsi_hook_short_turning_down_from_overbought():
    assert _rsi_hook(64.0, 68.0, long_max=33.0, short_min=66.0, hook_buffer=4.0) == (False, True)


def test_rsi_edge_low_cross():
    low, high = _rsi_edge(32.0, 35.0, long_max=33.0, short_min=66.0)
    assert low is True
    assert high is False
