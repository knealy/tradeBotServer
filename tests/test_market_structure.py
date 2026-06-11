"""Tests for ``core.market_structure`` — swing pivots, BoS, CHoCH, sessions."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import List

import pytest

from core.market_structure import (
    Session,
    StructureEvent,
    SwingKind,
    SwingPoint,
    SwingTracker,
    _SwingLabel,
    classify_session,
    detect_break_of_structure,
    detect_change_of_character,
    find_equal_highs,
    find_equal_lows,
    find_swing_pivots,
    prior_session_levels,
)


# ───────────────────────────── helpers ──────────────────────────────


class _Bar:
    """Minimal bar shim for testing — doesn't depend on CandleBar."""

    def __init__(self, high: float, low: float, *, ts: datetime = None,
                 close: float = None, open: float = None):
        self.high = high
        self.low = low
        self.timestamp = ts
        self.close = close if close is not None else (high + low) / 2
        self.open = open if open is not None else self.close


def _bars(highs_lows):
    """[(high, low), ...] → [_Bar]."""
    return [_Bar(h, l) for h, l in highs_lows]


# ─────────────────────── swing-pivot detection ──────────────────────


def test_find_pivots_simple_v_shape():
    # Bars: high goes 10, 11, 12, 13, 12, 11, 10 — pivot HIGH at index 3.
    # low goes  5,  6,  7,  8,  7,  6,  5 — pivot LOW NOT formed (highs
    # dominate); use a separate dip for lows.
    bars = _bars([(10, 5), (11, 4), (12, 3), (13, 2), (12, 3), (11, 4), (10, 5)])
    pivots = find_swing_pivots(bars, lookback=3)
    # Expect one high (at index 3 = 13) and one low (at index 3 = 2).
    highs = [p for p in pivots if p.kind == SwingKind.HIGH]
    lows = [p for p in pivots if p.kind == SwingKind.LOW]
    assert len(highs) == 1
    assert len(lows) == 1
    assert highs[0].index == 3
    assert highs[0].price == 13
    assert lows[0].index == 3
    assert lows[0].price == 2


def test_find_pivots_no_pivot_when_monotone():
    bars = _bars([(i, i - 5) for i in range(10, 20)])  # monotone rising
    pivots = find_swing_pivots(bars, lookback=3)
    assert pivots == []


def test_find_pivots_labels_consecutive_swings():
    # Two consecutive swing highs: first at 13 (idx 3), second at 15 (idx 9).
    # With lookback=3 the pivots must be at least 4 bars apart AND the
    # ramp toward the second pivot can't exceed the first pivot's height
    # within the first pivot's confirmation window [idx-3, idx+3].
    bars = _bars([
        (10, 5), (11, 5), (12, 4),
        (13, 5),                # pivot high #1 (idx 3, price 13)
        (12, 5), (11, 6), (10, 5),
        (12, 5), (13.5, 6),
        (15, 7),                # pivot high #2 (idx 9, price 15)
        (14, 8), (13, 7), (12, 6),
    ])
    pivots = find_swing_pivots(bars, lookback=3)
    highs = [p for p in pivots if p.kind == SwingKind.HIGH]
    assert len(highs) == 2
    assert highs[0].label == _SwingLabel.NONE  # first ever
    assert highs[1].label == _SwingLabel.HH    # 15 > 13


def test_find_pivots_rejects_invalid_lookback():
    with pytest.raises(ValueError):
        find_swing_pivots(_bars([(10, 5)]), lookback=0)


# ─────────────────────────── SwingTracker ───────────────────────────


def test_swing_tracker_emits_same_pivots_as_offline():
    bars = _bars([
        (10, 5), (11, 5), (12, 4),
        (13, 5),
        (12, 5), (11, 6),
        (14, 7), (14.5, 8),
        (15, 9),
        (14, 8), (13, 7), (12, 6),
    ])
    tracker = SwingTracker(lookback=3)
    for b in bars:
        tracker.update(b)
    offline = find_swing_pivots(bars, lookback=3)
    online_highs = [(p.index, p.price) for p in tracker.swing_highs]
    expected_highs = [(p.index, p.price) for p in offline if p.kind == SwingKind.HIGH]
    assert online_highs == expected_highs


def test_swing_tracker_most_recent_high_low():
    bars = _bars([
        (10, 5), (11, 4), (12, 3),
        (13, 2),     # pivot LOW at index 3 (2)
        (12, 3), (11, 4),
        (14, 5),     # pivot HIGH at index 6 (14)
        (13, 4), (12, 3), (11, 2),
    ])
    tracker = SwingTracker(lookback=3)
    for b in bars:
        tracker.update(b)
    assert tracker.most_recent_low is not None
    assert tracker.most_recent_low.price == 2
    assert tracker.most_recent_high is not None
    assert tracker.most_recent_high.price == 14


def test_swing_tracker_bounded_history():
    tracker = SwingTracker(lookback=2, max_history=3)
    # Manufacture 4 distinct swing highs to test the deque cap.
    bars = []
    for offset in (0, 10, 20, 30):
        # Each chunk: ramp up, peak, ramp down — creates one swing high.
        bars += _bars([(offset + 5, offset), (offset + 6, offset),
                       (offset + 10, offset),  # peak
                       (offset + 6, offset), (offset + 5, offset)])
    for b in bars:
        tracker.update(b)
    assert len(tracker.swing_highs) <= 3


# ────────────────────── BoS / CHoCH detection ───────────────────────


def test_bos_up_detects_break_above_recent_high():
    tracker = SwingTracker(lookback=2)
    # Build a swing high at 100, then close 105.
    bars = _bars([
        (90, 80), (95, 80), (100, 80), (95, 80), (90, 80),  # pivot high at idx 2
        (100, 80),
    ])
    for b in bars:
        tracker.update(b)
    assert tracker.most_recent_high is not None
    ev = detect_break_of_structure(tracker, last_close=105)
    assert ev == StructureEvent.BOS_UP


def test_bos_down_detects_break_below_recent_low():
    tracker = SwingTracker(lookback=2)
    bars = _bars([
        (110, 100), (108, 95), (105, 90), (108, 95), (110, 100),  # pivot low at idx 2
        (108, 95),
    ])
    for b in bars:
        tracker.update(b)
    ev = detect_break_of_structure(tracker, last_close=85)
    assert ev == StructureEvent.BOS_DOWN


def test_bos_none_when_close_within_range():
    tracker = SwingTracker(lookback=2)
    bars = _bars([
        (90, 80), (95, 80), (100, 80), (95, 80), (90, 80),
        (95, 85),
    ])
    for b in bars:
        tracker.update(b)
    ev = detect_break_of_structure(tracker, last_close=95)
    assert ev == StructureEvent.NONE


def test_choch_up_flags_first_HL_after_LL_sequence():
    """Downtrend: LH, LL.  Then a new LOW that's HIGHER than the prior
    LL → CHoCH_UP."""
    tracker = SwingTracker(lookback=2)
    bars = _bars([
        # Pivot LOW #1 at idx 2 (low=50)
        (60, 55), (60, 55), (60, 50), (60, 55), (60, 55),
        # Pivot LOW #2 at idx 7 (low=45) → LL
        (60, 55), (60, 55), (60, 45), (60, 55), (60, 55),
        # Pivot LOW #3 at idx 12 (low=48) → HL (48 > 45) → CHoCH_UP
        (60, 55), (60, 55), (60, 48), (60, 55), (60, 55),
        # Also need at least 2 highs (for the CHoCH detector's gate)
        (70, 55), (75, 55), (80, 55),  # pivot HIGH around here
        (75, 55), (70, 55),
        (75, 55), (78, 55), (82, 55),  # pivot HIGH
        (78, 55), (75, 55),
    ])
    for b in bars:
        tracker.update(b)
    lows = tracker.swing_lows
    assert len(lows) >= 3
    assert lows[-1].label == _SwingLabel.HL
    assert lows[-2].label == _SwingLabel.LL
    ev = detect_change_of_character(tracker)
    assert ev == StructureEvent.CHOCH_UP


def test_choch_none_when_no_label_flip():
    tracker = SwingTracker(lookback=2)
    bars = _bars([
        (60, 55), (60, 55), (60, 50), (60, 55), (60, 55),
        (60, 55), (60, 55), (60, 45), (60, 55), (60, 55),  # LL
        (60, 55), (60, 55), (60, 40), (60, 55), (60, 55),  # another LL
        (70, 55), (75, 55), (80, 55), (75, 55), (70, 55),  # pivot HIGH
        (70, 55), (75, 55), (85, 55), (75, 55), (70, 55),  # HH
    ])
    for b in bars:
        tracker.update(b)
    ev = detect_change_of_character(tracker)
    # Both lows should be LL, both highs should be HH → no character change.
    assert ev == StructureEvent.NONE


# ──────────────────────── equal highs / lows ────────────────────────


def test_find_equal_highs_pairs_within_tolerance():
    swings = [
        SwingPoint(index=10, kind=SwingKind.HIGH, price=100.0),
        SwingPoint(index=20, kind=SwingKind.HIGH, price=100.3),
        SwingPoint(index=30, kind=SwingKind.HIGH, price=110.0),
        SwingPoint(index=40, kind=SwingKind.HIGH, price=100.1),
    ]
    pairs = find_equal_highs(swings, tol_points=0.5)
    # Expect three pairs: (0,1), (0,3), (1,3).  110 is not equal to any.
    assert len(pairs) == 3
    prices = {(round(a.price, 1), round(b.price, 1)) for a, b in pairs}
    assert (100.0, 100.3) in prices
    assert (100.0, 100.1) in prices
    assert (100.3, 100.1) in prices


def test_find_equal_lows_pairs():
    swings = [
        SwingPoint(index=5, kind=SwingKind.LOW, price=50.0),
        SwingPoint(index=15, kind=SwingKind.LOW, price=49.9),
        SwingPoint(index=25, kind=SwingKind.LOW, price=60.0),
    ]
    pairs = find_equal_lows(swings, tol_points=0.2)
    assert len(pairs) == 1
    assert pairs[0][0].price == 50.0
    assert pairs[0][1].price == 49.9


def test_find_equal_highs_excludes_lows():
    swings = [
        SwingPoint(index=5, kind=SwingKind.LOW, price=50.0),
        SwingPoint(index=15, kind=SwingKind.LOW, price=50.0),  # equal but LOW
    ]
    pairs = find_equal_highs(swings, tol_points=0.5)
    assert pairs == []


# ─────────────────────── session classifier ─────────────────────────


@pytest.mark.parametrize("hh,mm,expected", [
    (0, 0, Session.ASIA),
    (2, 59, Session.ASIA),
    (3, 0, Session.LONDON),
    (7, 59, Session.LONDON),
    (8, 0, Session.PREMARKET),
    (9, 29, Session.PREMARKET),
    (9, 30, Session.NYAM),
    (11, 59, Session.NYAM),
    (12, 0, Session.LUNCH),
    (13, 29, Session.LUNCH),
    (13, 30, Session.NYPM),
    (14, 59, Session.NYPM),
    (15, 0, Session.CLOSE),
    (15, 59, Session.CLOSE),
    (16, 0, Session.AFTERHOURS),
    (17, 59, Session.AFTERHOURS),
    (18, 0, Session.ASIA),
    (23, 59, Session.ASIA),
])
def test_classify_session_boundaries(hh, mm, expected):
    ts = datetime(2026, 6, 10, hh, mm)
    assert classify_session(ts) == expected


# ──────────────────── prior-session H / L levels ────────────────────


def test_prior_session_levels_basic():
    """Two-day fixture: day 1 [H=110, L=100, close=105], day 2 should
    map to day 1's levels."""
    bars = []
    day1 = datetime(2026, 6, 9, 14, 0, tzinfo=timezone.utc)
    bars.append(_Bar(110, 100, ts=day1, close=105))
    bars.append(_Bar(108, 105, ts=day1 + timedelta(hours=1), close=107))

    day2 = datetime(2026, 6, 10, 14, 0, tzinfo=timezone.utc)
    bars.append(_Bar(120, 115, ts=day2, close=118))

    levels = prior_session_levels(bars, session_zone="UTC")
    # Day 2's entry should be present, Day 1's should not (no prior).
    et_day1 = day1.date()
    et_day2 = day2.date()
    assert et_day1 not in levels
    assert et_day2 in levels
    lv = levels[et_day2]
    assert lv.high == 110
    assert lv.low == 100
    # last close of day 1 is the second bar's close (chronological)
    assert lv.close == 107


def test_prior_session_levels_handles_naive_timestamps():
    bars = []
    bars.append(_Bar(100, 90, ts=datetime(2026, 6, 9, 14, 0), close=95))
    bars.append(_Bar(105, 95, ts=datetime(2026, 6, 10, 14, 0), close=100))
    levels = prior_session_levels(bars, session_zone="UTC")
    # The 2026-06-10 ET date (which equals UTC date here) should map to
    # 2026-06-09's high/low.
    assert any(lv.high == 100 and lv.low == 90 for lv in levels.values())
