"""Tests for ``scripts/probe_sweep_low_fade_optimize.py``.

Pin three layers:
* Sweep candidate detection mirrors the v2 brain's contract (long-side only).
* Session/strength/poke filters cull the candidate list correctly.
* The aggregator + verdict aggregation behaves predictably on synthetic data.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.probe_sweep_low_fade_optimize import (  # noqa: E402
    VERDICT_EDGE,
    VERDICT_MARGINAL,
    VERDICT_NONE,
    ComboKey,
    ComboStats,
    SweepCandidate,
    _aggregate_to_timeframe,
    aggregate_verdict,
    find_sweep_low_candidates,
    simulate_long_fade_trade,
)
from scripts.simulate_price_action_trades import CandleBar  # noqa: E402


# ────────────────────────── fixtures ────────────────────────────────


def _utc(year: int, month: int, day: int, hour: int = 9, minute: int = 30) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


def _bar(ts: datetime, o: float, h: float, low: float, c: float) -> CandleBar:
    return CandleBar(timestamp=ts, open=o, high=h, low=low, close=c, volume=1.0)


def _build_sweep_low_pattern(ts0: datetime) -> List[CandleBar]:
    """Build bars that produce one clean swing-low sweep on the last bar.

    Same pattern shape as the synthesizer's _sweep_low_bars fixture so the
    detector logic is exercised against a realistic V + sweep.
    """
    pattern = [
        (100.0, 100.5, 99.5, 100.0),
        (100.0, 100.5, 99.5, 100.0),
        (100.0, 100.5, 99.5, 100.0),
        (100.0, 100.5, 98.0, 98.0),
        (98.0, 98.5, 96.0, 96.0),
        (96.0, 96.5, 95.5, 95.5),
        (95.5, 95.7, 95.0, 95.2),
        (95.2, 95.5, 94.5, 95.0),  # pivot low (= 94.5)
        (95.0, 96.5, 95.5, 96.0),
        (96.0, 97.5, 96.5, 97.0),
        (97.0, 98.5, 97.5, 98.0),
        (98.0, 99.5, 98.5, 99.0),
        (99.0, 99.5, 98.5, 99.0),
        (99.0, 99.5, 98.5, 99.0),
        (99.0, 99.5, 98.5, 99.0),
        # SWEEP bar: pierces 94.5 with low=93.5 and closes back to 98.0
        (99.0, 99.5, 93.5, 98.0),
    ]
    out: List[CandleBar] = []
    for i, (o, h, low, c) in enumerate(pattern):
        out.append(_bar(ts0 + timedelta(minutes=5 * i), o, h, low, c))
    return out


# ───────────────────── candidate detection ──────────────────────────


class TestFindSweepLowCandidates:
    def test_basic_sweep_detected(self):
        bars = _build_sweep_low_pattern(_utc(2026, 6, 1, 14, 0))
        cands = find_sweep_low_candidates(
            bars,
            swing_lookback=3,
            min_poke_points=0.0,
            min_strength=0.0,
            session_filter="any",
        )
        assert len(cands) == 1
        c = cands[0]
        assert c.swing_low == pytest.approx(94.5, abs=0.01)
        # Sweep bar pierced to low=93.5, closed at 98.0.
        assert c.poke_amount == pytest.approx(1.0, abs=0.01)
        assert c.close_distance == pytest.approx(3.5, abs=0.01)
        assert c.strength == pytest.approx(3.5, abs=0.01)

    def test_too_short_window_yields_nothing(self):
        bars = _build_sweep_low_pattern(_utc(2026, 6, 1))[:3]
        cands = find_sweep_low_candidates(
            bars, swing_lookback=3, min_poke_points=0.0,
            min_strength=0.0, session_filter="any",
        )
        assert cands == []

    def test_min_poke_filter_culls_shallow_sweeps(self):
        bars = _build_sweep_low_pattern(_utc(2026, 6, 1, 14, 0))
        # poke_amount is 1.0 — a 2.0-pt min cull eliminates it.
        cands = find_sweep_low_candidates(
            bars, swing_lookback=3, min_poke_points=2.0,
            min_strength=0.0, session_filter="any",
        )
        assert cands == []
        # 0.5-pt floor keeps it.
        cands = find_sweep_low_candidates(
            bars, swing_lookback=3, min_poke_points=0.5,
            min_strength=0.0, session_filter="any",
        )
        assert len(cands) == 1

    def test_min_strength_filter_culls_weak_recoveries(self):
        bars = _build_sweep_low_pattern(_utc(2026, 6, 1, 14, 0))
        # Strength in this fixture = 3.5; a 5.0 floor eliminates it.
        cands = find_sweep_low_candidates(
            bars, swing_lookback=3, min_poke_points=0.0,
            min_strength=5.0, session_filter="any",
        )
        assert cands == []
        cands = find_sweep_low_candidates(
            bars, swing_lookback=3, min_poke_points=0.0,
            min_strength=1.0, session_filter="any",
        )
        assert len(cands) == 1

    def test_session_filter_culls_off_session_sweeps(self):
        # Sweep that fires at 03:00 ET — outside RTH.
        # 03:00 ET = 08:00 UTC (standard time).  Use Feb to avoid DST drift.
        ts0 = _utc(2026, 2, 5, 5, 0)  # 00:00 ET; last bar at +75 min = 01:15 ET
        bars = _build_sweep_low_pattern(ts0)
        cands = find_sweep_low_candidates(
            bars, swing_lookback=3, min_poke_points=0.0,
            min_strength=0.0, session_filter="rth",
        )
        assert cands == []
        cands = find_sweep_low_candidates(
            bars, swing_lookback=3, min_poke_points=0.0,
            min_strength=0.0, session_filter="any",
        )
        assert len(cands) == 1


# ───────────────────────── trade simulation ─────────────────────────


class TestSimulateLongFadeTrade:
    def test_wick_stop_dist_makes_sense(self):
        bars = _build_sweep_low_pattern(_utc(2026, 6, 1, 14, 0))
        cand = SweepCandidate(
            bar_idx=len(bars) - 1,
            swing_low=94.5,
            sweep_extreme=93.5,
            close_distance=3.5,
            poke_amount=1.0,
            strength=3.5,
        )
        # Last bar in fixture — no next bar to enter against → returns None.
        result = simulate_long_fade_trade(
            bars=bars, cand=cand,
            sl_buffer_ticks=2.0, stop_policy="wick",
            tp_r=1.5, max_hold_bars=12, force_flat_et_min=16 * 60,
            tick_size=0.25, point_value=5.0, commission_per_trade=1.55,
            bars_1m_by_ns=None, agg_minutes=5,
        )
        assert result is None

    def test_structural_stop_wider_than_wick_stop(self):
        # Force entry early enough that there are forward bars to walk.
        bars = _build_sweep_low_pattern(_utc(2026, 6, 1, 14, 0))
        # Inject 12 more bars after the sweep so the simulator has runway.
        last_ts = bars[-1].timestamp
        for i in range(1, 13):
            bars.append(_bar(
                last_ts + timedelta(minutes=5 * i),
                98.0, 100.0, 97.5, 99.5,
            ))
        cand = SweepCandidate(
            bar_idx=15,
            swing_low=94.5,
            sweep_extreme=93.5,
            close_distance=3.5,
            poke_amount=1.0,
            strength=3.5,
        )
        r_wick = simulate_long_fade_trade(
            bars=bars, cand=cand,
            sl_buffer_ticks=2.0, stop_policy="wick",
            tp_r=1.5, max_hold_bars=12, force_flat_et_min=16 * 60,
            tick_size=0.25, point_value=5.0, commission_per_trade=1.55,
            bars_1m_by_ns=None, agg_minutes=5,
        )
        r_struct = simulate_long_fade_trade(
            bars=bars, cand=cand,
            sl_buffer_ticks=2.0, stop_policy="structural",
            tp_r=1.5, max_hold_bars=12, force_flat_et_min=16 * 60,
            tick_size=0.25, point_value=5.0, commission_per_trade=1.55,
            bars_1m_by_ns=None, agg_minutes=5,
        )
        assert r_wick is not None
        assert r_struct is not None
        # Structural stop sits ABOVE the wick → smaller stop_dist → larger R per $ risked.
        assert r_struct.stop_dist < r_wick.stop_dist

    def test_unknown_stop_policy_raises(self):
        bars = _build_sweep_low_pattern(_utc(2026, 6, 1, 14, 0))
        cand = SweepCandidate(
            bar_idx=10, swing_low=94.5, sweep_extreme=93.5,
            close_distance=3.5, poke_amount=1.0, strength=3.5,
        )
        with pytest.raises(ValueError):
            simulate_long_fade_trade(
                bars=bars, cand=cand,
                sl_buffer_ticks=2.0, stop_policy="not-a-thing",
                tp_r=1.5, max_hold_bars=12, force_flat_et_min=16 * 60,
                tick_size=0.25, point_value=5.0, commission_per_trade=1.55,
                bars_1m_by_ns=None, agg_minutes=5,
            )


# ──────────────────────── combo aggregation ─────────────────────────


class TestComboStats:
    def test_add_tracks_count_wins_sum(self):
        s = ComboStats(key=_make_key())
        for r in [+1.0, -0.5, +2.0, -1.0]:
            s.add(r)
        assert s.n == 4
        assert s.wins == 2
        assert s.mean_r == pytest.approx(0.375)
        assert s.wr == 50.0

    def test_empty_returns_zero(self):
        s = ComboStats(key=_make_key())
        assert s.n == 0
        assert s.mean_r == 0.0
        assert s.wr == 0.0
        assert s.sharpe_r == 0.0

    def test_sharpe_is_mean_over_stdev(self):
        s = ComboStats(key=_make_key())
        for r in [+1.0, -1.0, +1.0, -1.0]:
            s.add(r)
        # mean=0 → sharpe = 0 (no edge, perfect coin flip).
        assert s.sharpe_r == 0.0
        s = ComboStats(key=_make_key())
        for r in [+1.0, +1.0, +1.0]:
            s.add(r)
        # All wins → stdev=0 → sharpe protected = 0.0
        assert s.sharpe_r == 0.0


# ────────────────────────── aggregator ──────────────────────────────


class TestAggregateToTimeframe:
    def test_empty_input(self):
        assert _aggregate_to_timeframe([], 5) == []

    def test_invalid_minutes(self):
        bars = [_bar(_utc(2026, 6, 1, 9, 30), 1, 1, 1, 1)]
        assert _aggregate_to_timeframe(bars, 0) == []

    def test_5_one_minute_bars_into_one_5m_bar(self):
        # 5 consecutive 1m bars in the same 5m bucket → 0 emitted bars
        # (last bucket still open).  6th 1m bar in next bucket → 1 emitted.
        ts0 = _utc(2026, 6, 1, 9, 30)
        ones = []
        for i in range(6):
            ones.append(_bar(
                ts0 + timedelta(minutes=i),
                o=100 + i, h=101 + i, low=99 + i, c=100.5 + i,
            ))
        out = _aggregate_to_timeframe(ones, 5)
        assert len(out) == 1
        agg = out[0]
        # First five 1m bars: open=100, high=104 (idx4), low=99, close=104.5
        assert agg.open == 100
        assert agg.close == 104.5
        assert agg.high == 105
        assert agg.low == 99

    def test_volume_summed(self):
        ts0 = _utc(2026, 6, 1, 9, 30)
        ones = [
            CandleBar(timestamp=ts0, open=1, high=1, low=1, close=1, volume=10),
            CandleBar(timestamp=ts0 + timedelta(minutes=1),
                      open=1, high=1, low=1, close=1, volume=20),
            CandleBar(timestamp=ts0 + timedelta(minutes=5),
                      open=1, high=1, low=1, close=1, volume=5),  # next bucket
        ]
        out = _aggregate_to_timeframe(ones, 5)
        assert len(out) == 1
        assert out[0].volume == 30


# ────────────────────────── verdict logic ───────────────────────────


def _make_key(symbol: str = "MGC") -> ComboKey:
    return ComboKey(
        symbol=symbol, timeframe="5m",
        min_poke_ticks=2.0, min_strength=0.5,
        session="nyam", stop_policy="wick",
        tp_r=1.5, max_hold_bars=12, swing_lookback=3,
    )


def _stats_for(symbol: str, n: int, wins: int, r_total: float) -> ComboStats:
    s = ComboStats(key=_make_key(symbol))
    s.n = n
    s.wins = wins
    s.r_sum = r_total
    s.r_sq_sum = r_total * (r_total / n) if n > 0 else 0.0
    return s


class TestAggregateVerdict:
    def test_edge_when_two_symbols_strong(self):
        # MGC: n=40, WR=55%, meanR=+0.25 → EDGE
        # MNQ: same → EDGE
        # MES: weak
        results = [
            _stats_for("MGC", n=40, wins=22, r_total=10.0),  # WR 55 %, meanR 0.25
            _stats_for("MNQ", n=40, wins=22, r_total=10.0),
            _stats_for("MES", n=40, wins=18, r_total=-2.0),
        ]
        assert aggregate_verdict(results, min_n=30) == VERDICT_EDGE

    def test_marginal_when_two_symbols_marginal(self):
        results = [
            _stats_for("MGC", n=40, wins=20, r_total=3.0),  # meanR=0.075
            _stats_for("MNQ", n=40, wins=20, r_total=3.0),
            _stats_for("MES", n=40, wins=20, r_total=0.0),
        ]
        assert aggregate_verdict(results, min_n=30) == VERDICT_MARGINAL

    def test_no_edge_when_n_too_low(self):
        results = [
            _stats_for("MGC", n=10, wins=8, r_total=10.0),  # n < min_n → ignored
            _stats_for("MNQ", n=10, wins=8, r_total=10.0),
            _stats_for("MES", n=10, wins=8, r_total=10.0),
        ]
        assert aggregate_verdict(results, min_n=30) == VERDICT_NONE

    def test_no_edge_when_only_one_symbol_strong(self):
        results = [
            _stats_for("MGC", n=40, wins=22, r_total=10.0),
            _stats_for("MNQ", n=40, wins=18, r_total=-2.0),
            _stats_for("MES", n=40, wins=18, r_total=-2.0),
        ]
        assert aggregate_verdict(results, min_n=30) == VERDICT_NONE

    def test_groups_by_filter_not_symbol(self):
        # Two different filter combos; only the SECOND has edge across symbols.
        s1_mgc = ComboStats(key=ComboKey(
            symbol="MGC", timeframe="5m",
            min_poke_ticks=0.0, min_strength=0.0,
            session="any", stop_policy="wick",
            tp_r=1.5, max_hold_bars=12, swing_lookback=3,
        ))
        s1_mgc.n = 40; s1_mgc.wins = 18; s1_mgc.r_sum = -2.0
        s1_mnq = ComboStats(key=ComboKey(
            symbol="MNQ", timeframe="5m",
            min_poke_ticks=0.0, min_strength=0.0,
            session="any", stop_policy="wick",
            tp_r=1.5, max_hold_bars=12, swing_lookback=3,
        ))
        s1_mnq.n = 40; s1_mnq.wins = 18; s1_mnq.r_sum = -2.0
        s2_mgc = ComboStats(key=_make_key("MGC"))  # default combo
        s2_mgc.n = 40; s2_mgc.wins = 22; s2_mgc.r_sum = 10.0
        s2_mnq = ComboStats(key=_make_key("MNQ"))
        s2_mnq.n = 40; s2_mnq.wins = 22; s2_mnq.r_sum = 10.0
        assert aggregate_verdict(
            [s1_mgc, s1_mnq, s2_mgc, s2_mnq], min_n=30
        ) == VERDICT_EDGE
