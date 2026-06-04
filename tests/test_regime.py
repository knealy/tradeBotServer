"""Tests for the shared regime classifier (`core/regime.py`)."""

from __future__ import annotations

import numpy as np
import pytest

from core.regime import (
    RegimeConfig,
    RegimeSnapshot,
    adx,
    classify,
    kaufman_efficiency_ratio,
    vol_percentile,
)


# ---------------------------- KER ----------------------------


def test_ker_perfect_trend_returns_one():
    """Monotonic ascending closes ⇒ KER = 1 (net move == sum of moves)."""
    closes = list(range(100))
    assert kaufman_efficiency_ratio(closes) == pytest.approx(1.0)


def test_ker_pure_chop_returns_near_zero():
    """Alternating up/down with the same magnitude ⇒ net move ≈ 0, sum > 0."""
    closes = [100.0]
    for _ in range(50):
        closes.append(closes[-1] + 1.0)
        closes.append(closes[-1] - 1.0)
    # End back at 100; KER should be small (~0)
    assert kaufman_efficiency_ratio(closes) < 0.05


def test_ker_short_input_returns_zero():
    assert kaufman_efficiency_ratio([100.0]) == 0.0
    assert kaufman_efficiency_ratio([]) == 0.0


def test_ker_flat_path_returns_zero():
    """No movement = no information; must NOT divide by zero."""
    assert kaufman_efficiency_ratio([100.0] * 20) == 0.0


# ---------------------------- ADX ----------------------------


def test_adx_strong_trend_above_threshold():
    """A monotonic uptrend with a small ATR floor should ADX well above 25."""
    closes = np.arange(100.0, 200.0, 1.0)  # 100 bars, +1 each
    highs = closes + 0.5
    lows = closes - 0.5
    val = adx(highs.tolist(), lows.tolist(), closes.tolist(), period=14)
    assert val > 25.0


def test_adx_random_walk_below_threshold():
    """Gaussian random walk ⇒ ADX typically below 25 (no persistent direction)."""
    rng = np.random.default_rng(42)
    n = 200
    rets = rng.normal(0, 1, n)
    closes = 100.0 + np.cumsum(rets)
    highs = closes + 0.5
    lows = closes - 0.5
    val = adx(highs.tolist(), lows.tolist(), closes.tolist(), period=14)
    assert val < 30.0  # generally well below; loose bound for randomness


def test_adx_insufficient_data_returns_zero():
    closes = [100.0, 101.0, 102.0]
    assert adx(closes, closes, closes, period=14) == 0.0


def test_adx_handles_constant_price():
    """Flat prices ⇒ no DI, no DX → 0.0 (no NaN propagation)."""
    closes = [100.0] * 50
    val = adx(closes, closes, closes, period=14)
    assert val == 0.0
    assert np.isfinite(val)


# ---------------------------- vol percentile ----------------------------


def test_vol_percentile_rank_in_unit_interval():
    rng = np.random.default_rng(7)
    closes = (100.0 + np.cumsum(rng.normal(0, 1, 1000))).tolist()
    val = vol_percentile(closes, rolling_bars=20, lookback_bars=500)
    assert 0.0 <= val <= 1.0


def test_vol_percentile_short_input_returns_zero():
    assert vol_percentile([100.0, 101.0], rolling_bars=20, lookback_bars=500) == 0.0


# ---------------------------- classify ----------------------------


def _synthetic_bars(closes):
    """Build a list of bar dicts with high/low straddling each close."""
    return [{"high": c + 1.0, "low": c - 1.0, "close": c} for c in closes]


def test_classify_strong_trend_labels_as_trend():
    """Strong monotonic uptrend ⇒ ker ≥ trend_ker AND adx ≥ trend_adx ⇒ trend."""
    bars = _synthetic_bars(list(np.arange(100.0, 200.0, 0.5)))
    snap = classify(bars)
    assert isinstance(snap, RegimeSnapshot)
    assert snap.label == "trend"
    assert snap.ker > 0.5
    assert snap.adx > 25.0


def test_classify_pure_chop_labels_as_chop():
    """Pure 2-bar oscillation should hit the chop bucket."""
    osc = []
    for i in range(200):
        osc.append(100.0 + (i % 2) * 0.5)
    bars = _synthetic_bars(osc)
    snap = classify(bars)
    # Should be chop (KER low + ADX low).  Loose assertion because the
    # narrow oscillation can occasionally hit "mixed".
    assert snap.label in {"chop", "mixed"}
    assert snap.ker < 0.30


def test_classify_insufficient_data_returns_mixed():
    bars = _synthetic_bars([100.0, 101.0, 100.5])
    snap = classify(bars)
    assert snap.label == "mixed"
    assert snap.ker == 0.0
    assert snap.adx == 0.0
    assert snap.n_bars == 3


def test_classify_respects_custom_thresholds():
    """A noisy mixed series with strict thresholds should land in 'mixed'
    even when default thresholds would have labelled it 'chop'."""
    rng = np.random.default_rng(11)
    rets = rng.normal(0, 1, 200)
    closes = (100.0 + np.cumsum(rets)).tolist()
    bars = _synthetic_bars(closes)
    strict = RegimeConfig(chop_ker=0.01, chop_adx=1.0)  # nearly-impossible chop
    snap = classify(bars, config=strict)
    # With the strict ceiling on chop, the random walk should NOT be labelled
    # chop — it must fall into 'mixed' (or 'trend' for the lucky run).
    assert snap.label != "chop"


def test_classify_accepts_pandas_rows():
    """Tolerate dict-like keying via pandas .iloc rows.  We use a list of
    plain dicts here (Series rows behave the same for [] access)."""
    bars = [{"high": 100.0 + i + 1, "low": 100.0 + i - 1, "close": 100.0 + i, "volume": 1000}
            for i in range(80)]
    snap = classify(bars)
    assert snap.label in {"trend", "chop", "mixed"}
    assert snap.n_bars == 80


def test_classify_default_label_set():
    """The classifier MUST only emit one of three labels — never None,
    never a synonym.  Downstream code pins these strings."""
    bars = _synthetic_bars(list(np.arange(100.0, 200.0, 0.5)))
    snap = classify(bars)
    assert snap.label in {"trend", "chop", "mixed"}
