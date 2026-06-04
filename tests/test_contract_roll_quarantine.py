"""Unit tests for the contract-roll quarantine in `core/backtest/parquet_cache.py`.

Background — quarterly futures rolls (MNQ/MES on Sep/Dec/Mar/Jun, MGC on Feb/Apr/
Jun/Aug/Oct/Dec) sometimes corrupt databento bar exports by interleaving the
front-month and back-month contracts into the same timestream. This produces
adjacent 5m bars at totally different price levels (e.g. 2025-09-15 alternated
between ~24280 (Sep contract) and ~24515 (Dec contract) every 5min). Trades
recorded on those days are fake — entries fill at one contract's price and exits
fill at the other's, manufacturing impossible PnL.

The quarantine drops the entire calendar day when ≥5 bar-to-bar gaps exceed
5× the typical bar range. These tests pin that behaviour.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from core.backtest.parquet_cache import (
    _CONTRACT_ROLL_GUARD_ENV,
    _CONTRACT_ROLL_MIN_FLAGS_PER_DAY,
    _normalize_ohlcv_frame,
    _quarantine_contract_roll_dates,
)


def _make_clean_5m_day(date: str, base_px: float = 25_000.0, n_bars: int = 288) -> pd.DataFrame:
    """Generate a synthetic clean 5m OHLCV day at ``base_px`` ± small noise."""
    rng = np.random.default_rng(seed=42)
    idx = pd.date_range(start=f"{date} 00:00", periods=n_bars, freq="5min")
    drift = rng.normal(0.0, 0.5, size=n_bars).cumsum()
    closes = base_px + drift
    opens = np.concatenate(([base_px], closes[:-1]))
    highs = np.maximum(opens, closes) + rng.uniform(0.5, 2.5, size=n_bars)
    lows = np.minimum(opens, closes) - rng.uniform(0.5, 2.5, size=n_bars)
    return pd.DataFrame(
        {
            "open": opens, "high": highs, "low": lows, "close": closes,
            "volume": rng.integers(100, 1000, size=n_bars),
        },
        index=pd.DatetimeIndex(idx, name="timestamp"),
    )


def _inject_roll_corruption(df: pd.DataFrame, date: str, alt_px: float = 24_750.0) -> pd.DataFrame:
    """Replace every other bar on ``date`` with a row pinned at ``alt_px`` —
    the classic contract-interleave pattern."""
    out = df.copy()
    mask = out.index.normalize() == pd.Timestamp(date)
    affected = out.index[mask]
    every_other = affected[::2]
    out.loc[every_other, ["open", "high", "low", "close"]] = alt_px
    return out


class TestQuarantineHeuristic:
    def test_clean_frame_passes_through_unchanged(self):
        df = _make_clean_5m_day("2025-09-14", base_px=25_000.0)
        out = _quarantine_contract_roll_dates(df)
        assert len(out) == len(df)
        pd.testing.assert_index_equal(out.index, df.index)

    def test_roll_day_is_dropped_entirely(self):
        clean_a = _make_clean_5m_day("2025-09-14")
        rolled = _inject_roll_corruption(_make_clean_5m_day("2025-09-15"), "2025-09-15")
        clean_b = _make_clean_5m_day("2025-09-16")
        df = pd.concat([clean_a, rolled, clean_b]).sort_index()

        out = _quarantine_contract_roll_dates(df)
        # Sept 15 dropped; 14 + 16 preserved
        assert (out.index.normalize() == pd.Timestamp("2025-09-15")).sum() == 0
        assert (out.index.normalize() == pd.Timestamp("2025-09-14")).sum() == len(clean_a)
        assert (out.index.normalize() == pd.Timestamp("2025-09-16")).sum() == len(clean_b)

    def test_single_outlier_does_not_quarantine_day(self):
        """A single fat-finger / bad-tick print on a day should not nuke the
        whole day — must clear the ≥MIN_FLAGS_PER_DAY threshold."""
        df = _make_clean_5m_day("2025-09-15")
        # Plant exactly ONE 1000pt anomaly mid-day
        df.iloc[100, df.columns.get_loc("open")] = 26_500.0
        out = _quarantine_contract_roll_dates(df)
        assert len(out) == len(df), "Day should survive a single isolated outlier"

    def test_threshold_constant_matches_documentation(self):
        """Pinning constant — if you change MIN_FLAGS_PER_DAY, expect downstream
        test/log/doc updates."""
        assert _CONTRACT_ROLL_MIN_FLAGS_PER_DAY == 5

    def test_env_disable_skips_quarantine(self):
        clean_a = _make_clean_5m_day("2025-09-14")
        rolled = _inject_roll_corruption(_make_clean_5m_day("2025-09-15"), "2025-09-15")
        df = pd.concat([clean_a, rolled]).sort_index()

        with patch.dict(os.environ, {_CONTRACT_ROLL_GUARD_ENV: "0"}):
            out = _quarantine_contract_roll_dates(df)
        assert len(out) == len(df), "Env=0 must short-circuit the guard"

    def test_logger_warns_with_sample_dates(self, caplog):
        clean = _make_clean_5m_day("2025-09-14")
        rolled = _inject_roll_corruption(_make_clean_5m_day("2025-09-15"), "2025-09-15")
        df = pd.concat([clean, rolled]).sort_index()

        with caplog.at_level("WARNING", logger="core.backtest.parquet_cache"):
            _quarantine_contract_roll_dates(df)
        msgs = "\n".join(rec.message for rec in caplog.records)
        assert "Quarantined" in msgs
        assert "2025-09-15" in msgs

    def test_normalize_ohlcv_frame_runs_quarantine(self):
        """End-to-end: the canonical normaliser must invoke the guard so every
        caller (load_from_csv / parquet sidecar / load_ohlcv_cached) benefits."""
        clean_a = _make_clean_5m_day("2025-09-14")
        rolled = _inject_roll_corruption(_make_clean_5m_day("2025-09-15"), "2025-09-15")
        clean_b = _make_clean_5m_day("2025-09-16")
        # Surface as the loader sees it: timestamp column, not index
        raw = pd.concat([clean_a, rolled, clean_b]).sort_index().reset_index()
        out = _normalize_ohlcv_frame(raw)
        assert (out.index.normalize() == pd.Timestamp("2025-09-15")).sum() == 0
        assert (out.index.normalize() == pd.Timestamp("2025-09-14")).sum() > 0
        assert (out.index.normalize() == pd.Timestamp("2025-09-16")).sum() > 0


class TestQuarantineEdgeCases:
    def test_empty_frame_passes_through(self):
        df = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        df.index = pd.DatetimeIndex([], name="timestamp")
        out = _quarantine_contract_roll_dates(df)
        assert len(out) == 0

    def test_two_row_frame_passes_through(self):
        idx = pd.DatetimeIndex(["2025-09-15 00:00", "2025-09-15 00:05"], name="timestamp")
        df = pd.DataFrame(
            {"open": [25_000, 25_001], "high": [25_005, 25_006], "low": [24_995, 24_996],
             "close": [25_001, 25_002], "volume": [100, 100]},
            index=idx,
        )
        out = _quarantine_contract_roll_dates(df)
        assert len(out) == 2

    def test_zero_typical_range_passes_through(self):
        """Synthetic data with constant prices (zero range) must not div-by-zero."""
        idx = pd.date_range("2025-09-15 00:00", periods=200, freq="5min", name="timestamp")
        df = pd.DataFrame(
            {"open": 25_000.0, "high": 25_000.0, "low": 25_000.0, "close": 25_000.0, "volume": 100},
            index=idx,
        )
        out = _quarantine_contract_roll_dates(df)
        assert len(out) == len(df)
