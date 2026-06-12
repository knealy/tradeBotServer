"""Tests for ``scripts/probe_prior_day_sweep_fade.py``.

Pin the prior-day RTH H/L computation + sweep-event detection so the
viability probe's numbers are reproducible across refactors.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.probe_prior_day_sweep_fade import (  # type: ignore
    SymbolSummary,
    _prior_trading_date,
    _verdict,
    compute_prior_day_rth_hl,
    find_sweep_events,
    summarise,
)
from scripts.simulate_price_action_trades import CandleBar  # type: ignore


# ──────────────────────── fixture helpers ────────────────────────


def _et_to_utc(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    """Build a UTC-naive timestamp that corresponds to the given ET wall time.

    Databento CSVs in this repo are timezone-naive but represent UTC clocks.
    For the tests we just hard-code: ET = UTC-4 during summer (no need for
    full tz handling because the probe converts the stored UTC time → ET
    inside ``_bar_et_*``).
    """
    return datetime(year, month, day, hour + 4, minute)


def _bar(
    year: int, month: int, day: int, hour_et: int, minute: int,
    open_: float, high: float, low: float, close: float, volume: float = 1.0,
) -> CandleBar:
    return CandleBar(
        timestamp=_et_to_utc(year, month, day, hour_et, minute),
        open=open_, high=high, low=low, close=close, volume=volume,
    )


# ──────────────────────── prior-day RTH H/L ─────────────────────────


class TestComputePriorDayRthHl:
    def test_empty_input(self):
        assert compute_prior_day_rth_hl([]) == {}

    def test_single_day_picks_rth_extremes(self):
        bars = [
            # pre-market: should be ignored
            _bar(2026, 6, 10, 8, 0, 100, 105, 99, 102),
            # RTH
            _bar(2026, 6, 10, 10, 0, 102, 110, 101, 108),
            _bar(2026, 6, 10, 12, 0, 108, 112, 103, 105),
            _bar(2026, 6, 10, 14, 0, 105, 109, 100, 107),
            # post-RTH: should be ignored
            _bar(2026, 6, 10, 17, 0, 107, 115, 95, 110),
        ]
        out = compute_prior_day_rth_hl(bars)
        assert len(out) == 1
        hi, lo = out[next(iter(out))]
        assert hi == 112.0  # max RTH high (12:00 bar)
        assert lo == 100.0  # min RTH low (14:00 bar)

    def test_pre_market_bars_excluded(self):
        bars = [
            _bar(2026, 6, 10, 8, 0, 100, 200, 50, 150),  # extreme pre-mkt — must be ignored
            _bar(2026, 6, 10, 10, 0, 100, 105, 99, 102),
        ]
        hi, lo = next(iter(compute_prior_day_rth_hl(bars).values()))
        assert hi == 105.0
        assert lo == 99.0

    def test_after_hours_bars_excluded(self):
        bars = [
            _bar(2026, 6, 10, 10, 0, 100, 105, 99, 102),
            _bar(2026, 6, 10, 17, 0, 100, 200, 50, 150),  # extreme post-RTH
            _bar(2026, 6, 10, 16, 0, 100, 200, 50, 150),  # 16:00 is END-EXCLUSIVE
        ]
        hi, lo = next(iter(compute_prior_day_rth_hl(bars).values()))
        assert hi == 105.0
        assert lo == 99.0

    def test_15_59_included_16_00_excluded(self):
        """The RTH window is [9:30, 16:00) — 15:59 in, 16:00 out."""
        bars = [
            _bar(2026, 6, 10, 15, 59, 100, 200, 99, 150),  # included (199)
            _bar(2026, 6, 10, 16, 0, 100, 300, 1, 150),  # excluded
        ]
        hi, lo = next(iter(compute_prior_day_rth_hl(bars).values()))
        assert hi == 200.0
        assert lo == 99.0

    def test_multi_day_separate_keys(self):
        bars = [
            _bar(2026, 6, 9, 10, 0, 100, 110, 95, 105),
            _bar(2026, 6, 10, 10, 0, 200, 210, 195, 205),
        ]
        out = compute_prior_day_rth_hl(bars)
        assert len(out) == 2
        keys = sorted(out.keys())
        assert out[keys[0]] == (110.0, 95.0)
        assert out[keys[1]] == (210.0, 195.0)


class TestPriorTradingDate:
    def test_returns_most_recent_before_d(self):
        from datetime import date as _d
        levels = {_d(2026, 6, 8): (1, 1), _d(2026, 6, 9): (1, 1), _d(2026, 6, 10): (1, 1)}
        assert _prior_trading_date(levels, _d(2026, 6, 10)) == _d(2026, 6, 9)
        assert _prior_trading_date(levels, _d(2026, 6, 9)) == _d(2026, 6, 8)
        assert _prior_trading_date(levels, _d(2026, 6, 8)) is None

    def test_skips_gaps(self):
        from datetime import date as _d
        levels = {_d(2026, 6, 5): (1, 1), _d(2026, 6, 8): (1, 1)}  # weekend gap
        assert _prior_trading_date(levels, _d(2026, 6, 9)) == _d(2026, 6, 8)


# ───────────────────────── sweep detection ──────────────────────────


class TestFindSweepEvents:
    def _build_two_days(self) -> tuple[list[CandleBar], dict]:
        """Day-1: RTH H=110, L=95.  Day-2: sweep scenarios.

        We use 0.25 tick size and require 2-tick (= 0.5) penetration by default.

        Sweep-above test = bar.high > pdh + 0.5 AND bar.close ≤ pdh.
        Sweep-below test = bar.low  < pdl - 0.5 AND bar.close ≥ pdl.

        Note: a bar that spikes ABOVE pdh and then CRASHES THROUGH pdl on
        the same bar still satisfies sweep-above (close ≤ pdh, just on the
        far side).  That's intentional — it's an even stronger short setup.
        These tests pin the close-side-must-have-rejected check by varying
        which side the close ends up on.
        """
        bars = [
            # Day 1 (2026-06-09) RTH bars to build H/L
            _bar(2026, 6, 9, 10, 0, 100, 110, 95, 105),
        ]
        bars.append(_bar(2026, 6, 10, 10, 0, 105, 109, 104, 108))  # idx 1: no sweep
        bars.append(_bar(2026, 6, 10, 11, 0, 108, 113, 107, 109))  # idx 2: sweep above → SHORT (close 109 ≤ 110)
        bars.append(_bar(2026, 6, 10, 12, 0, 109, 110, 93, 96))    # idx 3: sweep below → LONG  (close 96 ≥ 95)
        bars.append(_bar(2026, 6, 10, 13, 0, 109, 113, 108, 112))  # idx 4: sweep above BUT close 112 > 110 → no event
        bars.append(_bar(2026, 6, 10, 14, 0, 100, 102, 93, 94))    # idx 5: sweep below BUT close 94 < 95 → no event
        rth = compute_prior_day_rth_hl(bars[:1])
        return bars, rth

    def test_detects_sweep_above_and_below(self):
        bars, rth = self._build_two_days()
        events = find_sweep_events(
            bars,
            rth,
            entry_window_start_min=9 * 60 + 30,
            entry_window_end_min=16 * 60,
            min_penetration_ticks=2.0,
            tick_size=0.25,
        )
        assert len(events) == 2
        # First event: sweep above (idx 2 in our fixture)
        assert events[0].side == -1
        assert events[0].sweep_level == 110.0
        assert events[0].sweep_extreme == 113.0
        # Second event: sweep below (idx 3)
        assert events[1].side == +1
        assert events[1].sweep_level == 95.0
        assert events[1].sweep_extreme == 93.0

    def test_close_not_back_inside_is_not_a_sweep(self):
        """Bars where close stays OUTSIDE the prior-day range don't count."""
        bars, rth = self._build_two_days()
        events = find_sweep_events(
            bars,
            rth,
            entry_window_start_min=9 * 60 + 30,
            entry_window_end_min=16 * 60,
            min_penetration_ticks=2.0,
            tick_size=0.25,
        )
        # idx 4 (close 111 > pdh) and idx 5 (close 93 < pdl) must NOT appear.
        # The 2 detected events must be the back-inside cases only.
        extremes = sorted(ev.sweep_extreme for ev in events)
        assert extremes == [93.0, 113.0]

    def test_min_penetration_filter(self):
        bars, rth = self._build_two_days()
        # Tighten penetration to 100 ticks (25.0 pts) — nothing should match.
        events = find_sweep_events(
            bars,
            rth,
            entry_window_start_min=9 * 60 + 30,
            entry_window_end_min=16 * 60,
            min_penetration_ticks=100.0,
            tick_size=0.25,
        )
        assert events == []

    def test_entry_window_filters_out_of_window(self):
        bars, rth = self._build_two_days()
        # Restrict to 13:00-14:00 only — neither of our genuine sweeps falls
        # in this window (they're at 11:00 and 12:00).  idx 4 is at 13:00 but
        # its close 111 stays outside pdh so it's not a sweep either.
        events = find_sweep_events(
            bars,
            rth,
            entry_window_start_min=13 * 60,
            entry_window_end_min=14 * 60,
            min_penetration_ticks=2.0,
            tick_size=0.25,
        )
        assert events == []

    def test_no_prior_day_means_no_events(self):
        """First trading day in the dataset has no prior-day H/L → no events."""
        bars = [
            _bar(2026, 6, 10, 11, 0, 108, 113, 107, 109),
        ]
        events = find_sweep_events(
            bars,
            {},  # no prior-day levels
            entry_window_start_min=9 * 60 + 30,
            entry_window_end_min=16 * 60,
            min_penetration_ticks=2.0,
            tick_size=0.25,
        )
        assert events == []


# ───────────────────────── summary / verdict ────────────────────────


class TestSummarise:
    def _spec(self) -> dict:
        return {"point_value": 10.0, "tick_size": 0.1, "commission_per_trade": 1.55}

    def test_empty_trades(self):
        s = summarise("MGC", [], self._spec())
        assert s.symbol == "MGC"
        assert s.n == 0
        assert s.mean_r == 0.0

    def test_mean_wr_split_long_short(self):
        from scripts.probe_prior_day_sweep_fade import TradeResult

        ts = [
            TradeResult("MGC", +1, datetime(2026, 6, 10, 10), 100.0, 1.0, +1.0, "take_profit", 5),
            TradeResult("MGC", -1, datetime(2026, 6, 10, 11), 110.0, 1.0, -0.5, "stop_loss", 3),
            TradeResult("MGC", +1, datetime(2026, 6, 11, 10), 100.0, 2.0, +0.8, "take_profit", 4),
        ]
        s = summarise("MGC", ts, self._spec())
        assert s.n == 3
        assert s.wins == 2
        assert s.wr == pytest.approx(66.67, abs=0.01)
        assert s.mean_r == pytest.approx((1.0 - 0.5 + 0.8) / 3, abs=1e-6)
        assert s.n_long == 2 and s.n_short == 1
        assert s.mean_r_long == pytest.approx(0.9, abs=1e-6)
        assert s.mean_r_short == pytest.approx(-0.5, abs=1e-6)
        # $ PnL = sum(r * stop_dist * point_value)
        # = (1.0 * 1.0 * 10) + (-0.5 * 1.0 * 10) + (0.8 * 2.0 * 10) = 21.0
        assert s.total_dollar == pytest.approx(21.0, abs=1e-6)


class TestVerdict:
    def _s(self, n: int, wr: float, mr: float) -> SymbolSummary:
        return SymbolSummary(symbol="X", n=n, wr=wr, mean_r=mr)

    def test_productionable_when_2_of_3_meet_bar(self):
        ss = [
            self._s(50, 55.0, 0.25),
            self._s(60, 60.0, 0.30),
            self._s(20, 40.0, -0.10),
        ]
        assert _verdict(ss) == "PRODUCTIONABLE"

    def test_marginal_when_only_1_meets_bar(self):
        ss = [
            self._s(50, 55.0, 0.25),
            self._s(60, 40.0, -0.05),
            self._s(20, 40.0, -0.10),
        ]
        assert _verdict(ss) == "MARGINAL"

    def test_marginal_when_any_symbol_in_marginal_band(self):
        ss = [
            self._s(40, 45.0, 0.10),
            self._s(50, 40.0, -0.05),
        ]
        assert _verdict(ss) == "MARGINAL"

    def test_no_edge_when_all_below_marginal(self):
        ss = [
            self._s(50, 30.0, -0.20),
            self._s(60, 35.0, 0.02),
        ]
        assert _verdict(ss) == "NO EDGE"

    def test_no_edge_on_empty(self):
        assert _verdict([]) == "NO EDGE"


# ─────────────────────────── smoke E2E test ──────────────────────────


@pytest.mark.skipif(
    not (ROOT / "historical_data" / "price" / "MGC_5m_databento.csv").exists(),
    reason="historical CSV unavailable in this checkout",
)
def test_e2e_probe_smoke(monkeypatch, capsys):
    """End-to-end: run main() on a tiny window — only checks it doesn't crash."""
    from scripts import probe_prior_day_sweep_fade as mod

    test_argv = [
        "probe_prior_day_sweep_fade.py",
        "--symbols", "MGC",
        "--since", "2026-06-01",
        "--until", "2026-06-12",
        "--no-1m",
        "--json",
    ]
    monkeypatch.setattr(sys, "argv", test_argv)
    rc = mod.main()
    captured = capsys.readouterr()
    assert "verdict" in captured.out.lower()
    assert rc in (0, 1, 2)
