"""Tests for ``scripts/probe_sweep_combos.py``.

Pin the four primitive-filter helpers, the combo aggregation, and the
verdict logic.  Each filter is exercised on an isolated synthetic series
that produces the corresponding primitive deterministically.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Tuple

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.probe_sweep_combos import (  # noqa: E402
    VERDICT_EDGE,
    VERDICT_MARGINAL,
    VERDICT_NONE,
    ComboFilter,
    ComboResult,
    aggregate_verdicts,
    default_combos,
    has_recent_bullish_fvg_below,
    has_recent_choch_up,
    is_at_prior_day_low,
    is_oversold_rsi,
    matches_filter,
)
from scripts.probe_sweep_low_fade_optimize import SweepCandidate  # noqa: E402
from scripts.simulate_price_action_trades import CandleBar  # noqa: E402


# ────────────────────────── fixtures ────────────────────────────────


def _utc(year: int, month: int, day: int, hour: int = 9, minute: int = 30) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


def _bar(ts: datetime, o: float, h: float, low: float, c: float, v: float = 1.0) -> CandleBar:
    return CandleBar(timestamp=ts, open=o, high=h, low=low, close=c, volume=v)


# ──────────────────────── FVG filter ─────────────────────────────────


class TestFVGFilter:
    def _make_bars_with_bullish_fvg(self) -> List[CandleBar]:
        """Bars[2].low > bars[0].high → bullish FVG at index 2."""
        ts0 = _utc(2026, 6, 1, 14, 0)
        return [
            _bar(ts0, 100, 100.5, 99.5, 100.0),    # gap_lower=100.5
            _bar(ts0 + timedelta(minutes=5), 100, 105, 99.5, 104.5),
            _bar(ts0 + timedelta(minutes=10), 104.5, 106, 102, 105.5),  # gap_upper=102
            # FVG = [100.5, 102].  Need an unmitigated state.
            _bar(ts0 + timedelta(minutes=15), 105.5, 106.5, 104.5, 106),
            _bar(ts0 + timedelta(minutes=20), 106, 107, 105.5, 106.5),
            # Sweep bar (last one) — close above the FVG, so FVG is "below close"
            _bar(ts0 + timedelta(minutes=25), 106.5, 107.5, 103.0, 107.0),
        ]

    def test_unmitigated_bullish_fvg_below_close(self):
        bars = self._make_bars_with_bullish_fvg()
        # Sweep at idx 5 (last bar), close=107.  FVG upper=102 → 5pts below.
        assert has_recent_bullish_fvg_below(
            bars, sweep_idx=5,
            distance_points=10.0, lookback_bars=10,
        ) is True

    def test_no_fvg_yields_false(self):
        # Flat bars → no FVG.
        ts0 = _utc(2026, 6, 1, 14, 0)
        bars = [_bar(ts0 + timedelta(minutes=5 * i),
                     100, 100.5, 99.5, 100) for i in range(10)]
        assert has_recent_bullish_fvg_below(
            bars, sweep_idx=9,
            distance_points=10.0, lookback_bars=10,
        ) is False

    def test_distance_filter_rejects_far_fvgs(self):
        bars = self._make_bars_with_bullish_fvg()
        # Distance band of 1 pt → FVG at 102 is 5 pts away → rejected.
        assert has_recent_bullish_fvg_below(
            bars, sweep_idx=5,
            distance_points=1.0, lookback_bars=10,
        ) is False

    def test_lookback_filter_rejects_old_fvgs(self):
        bars = self._make_bars_with_bullish_fvg()
        # Lookback=2 → only bars[3..5] visible → no FVG (FVG formed at 2).
        assert has_recent_bullish_fvg_below(
            bars, sweep_idx=5,
            distance_points=10.0, lookback_bars=2,
        ) is False

    def test_short_window_returns_false(self):
        ts0 = _utc(2026, 6, 1, 14, 0)
        bars = [_bar(ts0, 100, 100.5, 99.5, 100)]
        assert has_recent_bullish_fvg_below(
            bars, sweep_idx=0,
            distance_points=10.0, lookback_bars=10,
        ) is False


# ────────────────────────── prior-day-low filter ─────────────────────


class TestPriorDayLowFilter:
    def test_within_tolerance_passes(self):
        bars = [_bar(_utc(2026, 6, 5, 14, 0), 100, 101, 99, 100)]
        # June 4 prior-day low = 95.
        rth_levels = {
            datetime(2026, 6, 4).date(): (100.0, 95.0),
            datetime(2026, 6, 3).date(): (98.0, 90.0),
        }
        assert is_at_prior_day_low(
            bars, sweep_idx=0, rth_levels=rth_levels,
            swing_low=95.5, tol_points=1.0,
        ) is True

    def test_outside_tolerance_fails(self):
        bars = [_bar(_utc(2026, 6, 5, 14, 0), 100, 101, 99, 100)]
        rth_levels = {datetime(2026, 6, 4).date(): (100.0, 95.0)}
        assert is_at_prior_day_low(
            bars, sweep_idx=0, rth_levels=rth_levels,
            swing_low=97.0, tol_points=1.0,
        ) is False

    def test_no_prior_day_data_fails(self):
        bars = [_bar(_utc(2026, 6, 5, 14, 0), 100, 101, 99, 100)]
        assert is_at_prior_day_low(
            bars, sweep_idx=0, rth_levels={}, swing_low=95.0, tol_points=1.0,
        ) is False


# ───────────────────────── RSI filter ────────────────────────────────


class TestRSIFilter:
    def test_oversold_returns_true(self):
        # 14 consecutive declining bars → RSI near 0 → oversold.
        ts0 = _utc(2026, 6, 1, 14, 0)
        bars: List[CandleBar] = [
            _bar(ts0 - timedelta(minutes=5), 200, 200.5, 199, 200.0),
        ]
        for i in range(14):
            close = 200.0 - (i + 1) * 1.0
            bars.append(_bar(
                ts0 + timedelta(minutes=5 * i),
                bars[-1].close, bars[-1].close + 0.1,
                close - 0.1, close,
            ))
        assert is_oversold_rsi(
            bars, sweep_idx=len(bars) - 1,
            period=14, threshold=30.0,
        ) is True

    def test_overbought_returns_false(self):
        # 14 consecutive rising bars → RSI near 100 → not oversold.
        ts0 = _utc(2026, 6, 1, 14, 0)
        bars: List[CandleBar] = [_bar(ts0 - timedelta(minutes=5), 100, 100.5, 99, 100.0)]
        for i in range(14):
            close = 100.0 + (i + 1) * 1.0
            bars.append(_bar(
                ts0 + timedelta(minutes=5 * i),
                bars[-1].close, close + 0.1,
                bars[-1].close - 0.1, close,
            ))
        assert is_oversold_rsi(
            bars, sweep_idx=len(bars) - 1,
            period=14, threshold=30.0,
        ) is False

    def test_short_history_returns_false(self):
        ts0 = _utc(2026, 6, 1, 14, 0)
        bars = [_bar(ts0 + timedelta(minutes=5 * i), 100, 101, 99, 100) for i in range(5)]
        assert is_oversold_rsi(
            bars, sweep_idx=4, period=14, threshold=30.0,
        ) is False


# ────────────────────────── matches_filter ──────────────────────────


class TestMatchesFilter:
    def _baseline_args(self) -> Dict:
        return dict(
            fvg_distance_points=10.0, fvg_lookback_bars=30,
            choch_lookback_bars=30, swing_lookback=3,
            pdl_rth_levels={}, pdl_tol_points=3.0,
            rsi_period=14, rsi_threshold=30.0,
        )

    def test_sweep_only_always_matches(self):
        cand = SweepCandidate(
            bar_idx=0, swing_low=95.0, sweep_extreme=93.0,
            close_distance=4.0, poke_amount=2.0, strength=2.0,
        )
        bars = [_bar(_utc(2026, 6, 1, 14, 0), 100, 101, 99, 100)]
        assert matches_filter(
            bars, cand, ComboFilter("sweep_only"), **self._baseline_args(),
        ) is True

    def test_fvg_combo_rejects_no_fvg(self):
        cand = SweepCandidate(
            bar_idx=0, swing_low=95.0, sweep_extreme=93.0,
            close_distance=4.0, poke_amount=2.0, strength=2.0,
        )
        bars = [_bar(_utc(2026, 6, 1, 14, 0), 100, 101, 99, 100)]
        assert matches_filter(
            bars, cand, ComboFilter("sweep+fvg", has_fvg=True),
            **self._baseline_args(),
        ) is False

    def test_pdl_combo_rejects_no_rth_levels(self):
        cand = SweepCandidate(
            bar_idx=0, swing_low=95.0, sweep_extreme=93.0,
            close_distance=4.0, poke_amount=2.0, strength=2.0,
        )
        bars = [_bar(_utc(2026, 6, 5, 14, 0), 100, 101, 99, 100)]
        assert matches_filter(
            bars, cand, ComboFilter("sweep+pdl", has_pdl=True),
            **self._baseline_args(),
        ) is False


# ────────────────────────── combo aggregator ────────────────────────


def _result(symbol: str, combo: str, tf: str,
            n: int, wins: int, r_total: float) -> ComboResult:
    r = ComboResult(symbol=symbol, timeframe=tf, combo_name=combo)
    r.n = n; r.wins = wins; r.r_sum = r_total
    r.r_sq_sum = r_total * (r_total / n) if n else 0.0
    return r


class TestAggregateVerdicts:
    def test_edge_when_two_symbols_strong(self):
        results = [
            _result("MGC", "sweep+pdl", "15m", n=30, wins=18, r_total=8.0),  # WR 60%, R 0.27
            _result("MNQ", "sweep+pdl", "15m", n=30, wins=16, r_total=7.0),  # WR 53%, R 0.23
            _result("MES", "sweep+pdl", "15m", n=30, wins=8, r_total=-2.0),
        ]
        v = aggregate_verdicts(results, min_n=25)
        assert v[("sweep+pdl", "15m")] == VERDICT_EDGE

    def test_marginal_when_one_symbol_only(self):
        results = [
            _result("MGC", "sweep+fvg", "15m", n=30, wins=15, r_total=4.0),  # R 0.13
            _result("MNQ", "sweep+fvg", "15m", n=30, wins=14, r_total=0.0),
            _result("MES", "sweep+fvg", "15m", n=30, wins=14, r_total=0.0),
        ]
        v = aggregate_verdicts(results, min_n=25)
        assert v[("sweep+fvg", "15m")] == VERDICT_MARGINAL

    def test_no_edge_when_n_too_low(self):
        results = [
            _result("MGC", "sweep+pdl", "15m", n=10, wins=10, r_total=20.0),
        ]
        v = aggregate_verdicts(results, min_n=25)
        assert v[("sweep+pdl", "15m")] == VERDICT_NONE

    def test_per_timeframe_independent(self):
        results = [
            _result("MGC", "sweep+pdl", "5m", n=30, wins=15, r_total=2.0),
            _result("MGC", "sweep+pdl", "15m", n=30, wins=20, r_total=10.0),
        ]
        v = aggregate_verdicts(results, min_n=25)
        # 5m: only one symbol, marginal threshold (0.10) hit at 2/30=0.067 → no edge.
        # 15m: only one symbol, 10/30=0.33 → marginal (single-symbol).
        assert v[("sweep+pdl", "5m")] == VERDICT_NONE
        assert v[("sweep+pdl", "15m")] == VERDICT_MARGINAL


class TestDefaultCombos:
    def test_includes_baseline(self):
        names = {c.name for c in default_combos()}
        assert "sweep_only" in names

    def test_includes_doubles(self):
        names = {c.name for c in default_combos()}
        assert "sweep+fvg+pdl" in names
        assert "sweep+rsi+pdl" in names
