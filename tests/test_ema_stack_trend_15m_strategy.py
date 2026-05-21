"""Unit tests for EMA reaction + RSI extreme helpers."""

from strategies.ema_stack_trend_15m_strategy import (
    _ema_last,
    _resistance_reject_ema,
    _rsi_wilder_last,
    _support_bounce_ema,
)


def test_rsi_strong_uptrend_is_high():
    closes = [float(i) for i in range(120)]
    r = _rsi_wilder_last(closes, 14)
    assert r is not None
    assert r > 70.0


def test_support_bounce_detects_reclaim():
    emas = {8: 100.0, 21: 99.0, 50: 98.0}
    # Low tags EMA21, close reclaims above
    hit = _support_bounce_ema(99.5, 101.0, 98.8, 100.5, emas, tol_pts=1.0, require_green=True)
    assert hit is not None
    period, level = hit
    assert period == 21
    assert level == 99.0


def test_support_bounce_rejects_no_reclaim():
    emas = {21: 100.0}
    assert _support_bounce_ema(100.0, 101.0, 99.0, 99.5, emas, tol_pts=1.0, require_green=True) is None


def test_resistance_reject_detects():
    emas = {8: 100.0, 21: 110.0}
    # High tags EMA8 at 100, close rejects below
    hit = _resistance_reject_ema(100.5, 100.4, 99.0, 99.8, emas, tol_pts=1.0, require_red=True)
    assert hit is not None
    assert hit[0] == 8


def test_ema_last_returns_all_periods():
    closes = [float(100 + i * 0.1) for i in range(250)]
    emas = _ema_last(closes, (8, 21, 50))
    assert set(emas.keys()) == {8, 21, 50}
    assert all(v == v for v in emas.values())
