"""Tests for core.research.pattern_conditional."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from core.research.pattern_conditional import (
    benjamini_hochberg,
    collect_pattern_results,
    ensure_ny_index,
    resample_ohlcv,
)


def _synth_1m(n_days: int = 3) -> pd.DataFrame:
    rng = pd.date_range("2024-01-02", periods=n_days * 24 * 60, freq="1min", tz="America/New_York")
    np.random.seed(42)
    px = 100 + np.cumsum(np.random.randn(len(rng)) * 0.02)
    df = pd.DataFrame(
        {
            "open": px,
            "high": px + 0.05,
            "low": px - 0.05,
            "close": px + np.random.randn(len(rng)) * 0.02,
            "volume": np.random.randint(10, 100, len(rng)),
        },
        index=rng,
    )
    return df


def test_bh_monotonic():
    p = np.array([0.001, 0.02, 0.5, 0.9])
    rej = benjamini_hochberg(p, alpha=0.05)
    assert rej[0] and rej[1]
    assert not rej[3]


def test_resample_and_patterns_smoke():
    df = ensure_ny_index(_synth_1m())
    df5 = resample_ohlcv(df, "5min")
    rows = collect_pattern_results(df5, min_feat=10, min_rest=10, min_cell=1)
    assert isinstance(rows, list)
    for r in rows:
        assert r.n_feat + r.n_rest > 0


def test_fisher_table_shape():
    from core.research.pattern_conditional import _fisher_table

    feat = np.array([1, 1, 0, 0], dtype=bool)
    out = np.array([1, 0, 1, 0], dtype=bool)
    tab = _fisher_table(feat, out)
    assert tab == (1, 1, 1, 1)
