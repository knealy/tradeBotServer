"""Pinning tests for ``core.price_action``.

Each detector gets at least one positive and one negative fixture so a
threshold tweak can't silently widen / narrow the pattern definition.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from core.price_action import (
    CandleBar,
    Pattern,
    PatternEvent,
    ProbabilityEmitter,
    detect_all_in_csv,
    detect_patterns,
)


T0 = datetime(2026, 6, 9, 9, 30)


def _bar(open_, high, low, close, *, offset_min: int = 0) -> CandleBar:
    return CandleBar(
        timestamp=T0 + timedelta(minutes=offset_min),
        open=open_, high=high, low=low, close=close, volume=100.0,
    )


# ─────────────────────── CandleBar properties ───────────────────────


def test_candlebar_derived_properties():
    b = _bar(100, 110, 95, 105)
    assert b.body == 5
    assert b.body_top == 105
    assert b.body_bottom == 100
    assert b.upper_wick == 5  # 110 - 105
    assert b.lower_wick == 5  # 100 - 95
    assert b.range == 15
    assert b.is_bullish
    assert not b.is_bearish


def test_candlebar_bearish_props():
    b = _bar(105, 110, 95, 100)
    assert b.is_bearish
    assert b.body_top == 105
    assert b.body_bottom == 100


# ───────────────────────── engulfing tests ──────────────────────────


def test_bullish_engulfing_positive():
    prev = _bar(105, 106, 100, 101, offset_min=0)  # bearish small body 105→101
    curr = _bar(100, 110, 99, 108, offset_min=5)   # bullish large body 100→108 engulfs 101-105
    events = detect_patterns([prev, curr])
    names = [e.name for e in events]
    assert Pattern.BULLISH_ENGULFING in names
    assert Pattern.BEARISH_ENGULFING not in names


def test_bullish_engulfing_negative_when_prev_bullish():
    prev = _bar(100, 110, 99, 108, offset_min=0)   # bullish
    curr = _bar(101, 112, 100, 110, offset_min=5)  # bullish, doesn't qualify
    events = detect_patterns([prev, curr])
    assert all(e.name != Pattern.BULLISH_ENGULFING for e in events)


def test_bullish_engulfing_negative_when_body_does_not_engulf():
    prev = _bar(105, 106, 100, 101, offset_min=0)  # bearish 105→101
    curr = _bar(102, 110, 101, 104, offset_min=5)  # bullish body 102→104 does NOT engulf 101-105
    events = detect_patterns([prev, curr])
    assert all(e.name != Pattern.BULLISH_ENGULFING for e in events)


def test_bearish_engulfing_positive():
    prev = _bar(100, 105, 99, 104, offset_min=0)   # bullish 100→104
    curr = _bar(105, 106, 95, 99, offset_min=5)    # bearish 105→99 engulfs 100-104
    events = detect_patterns([prev, curr])
    names = [e.name for e in events]
    assert Pattern.BEARISH_ENGULFING in names


# ────────────────────────── inside-bar tests ────────────────────────


def test_inside_bar_positive():
    prev = _bar(100, 120, 90, 110, offset_min=0)
    curr = _bar(105, 115, 95, 108, offset_min=5)  # high 115 < 120 AND low 95 > 90
    events = detect_patterns([prev, curr])
    names = [e.name for e in events]
    assert Pattern.INSIDE_BAR in names


def test_inside_bar_negative_when_high_equal():
    prev = _bar(100, 120, 90, 110, offset_min=0)
    curr = _bar(105, 120, 95, 108, offset_min=5)  # high 120 NOT strictly less
    events = detect_patterns([prev, curr])
    assert all(e.name != Pattern.INSIDE_BAR for e in events)


# ─────────────────────────── pin-bar tests ──────────────────────────


def test_bullish_pin_positive():
    # Open=104, High=105, Low=90, Close=103.  Body=1, lower_wick=13 (≥2×1),
    # upper_wick=1 (≤1×1), body/range = 1/15 ≈ 0.067 (≤0.4).
    curr = _bar(104, 105, 90, 103)
    events = detect_patterns([_bar(100, 101, 99, 100, offset_min=-5), curr])
    names = [e.name for e in events]
    assert Pattern.BULLISH_PIN in names


def test_bullish_pin_negative_when_body_too_large():
    # body 5, range 7, ratio > 0.4 -> not a pin
    curr = _bar(101, 102, 95, 100)  # body=1 actually small; let me think...
    # Build a clear non-pin: body 5, lower wick 3 (less than 2×5=10)
    curr = _bar(105, 110, 95, 100)
    events = detect_patterns([_bar(95, 96, 94, 95, offset_min=-5), curr])
    assert all(e.name not in (Pattern.BULLISH_PIN, Pattern.BEARISH_PIN) for e in events)


def test_bearish_pin_positive():
    # Open=101, High=115, Low=100, Close=102.  Body=1, upper_wick=13 (≥2×1),
    # lower_wick=1 (≤1×1), body/range = 1/15.
    curr = _bar(101, 115, 100, 102)
    events = detect_patterns([_bar(100, 101, 99, 100, offset_min=-5), curr])
    names = [e.name for e in events]
    assert Pattern.BEARISH_PIN in names


# ─────────────────────── detection over series ──────────────────────


def test_detect_patterns_returns_empty_for_short_history():
    assert detect_patterns([]) == []
    assert detect_patterns([_bar(100, 101, 99, 100)]) == []  # only 1 bar; engulfing requires 2


def test_detect_patterns_only_inspects_latest_bar():
    # First two bars form a bullish engulfing, third bar is neutral.
    # Latest bar should fire NO patterns even though the prior two formed one.
    prev = _bar(105, 106, 100, 101, offset_min=0)
    eng = _bar(100, 110, 99, 108, offset_min=5)
    neutral = _bar(108, 109, 107, 108.5, offset_min=10)
    events = detect_patterns([prev, eng, neutral])
    # latest is `neutral` — it has prior `eng`, but those two don't form a pattern.
    assert all(e.name != Pattern.BULLISH_ENGULFING for e in events)


# ─────────────────── ProbabilityEmitter (online) ────────────────────


def test_emitter_credits_outcome_to_previous_pattern():
    emitter = ProbabilityEmitter()
    # Bar 0: ordinary
    emitter.update([_bar(100, 101, 99, 100, offset_min=0)])
    # Bar 1: forms a bullish pin — pattern detected, NOT yet credited.
    emitter.update([_bar(100, 101, 99, 100, offset_min=0),
                     _bar(104, 105, 90, 103, offset_min=5)])
    # Bar 2: closes UP — credit the pin with an "up" outcome.
    emitter.update([_bar(104, 105, 90, 103, offset_min=5),
                     _bar(103, 110, 102, 109, offset_min=10)])
    snap = emitter.snapshot()
    pin = snap.get(Pattern.BULLISH_PIN, {})
    assert pin["n"] == 1
    assert pin["p_up"] == 1.0
    assert pin["mean_ret"] > 0


def test_emitter_baseline_accumulates_independent_of_patterns():
    emitter = ProbabilityEmitter()
    window = []
    bars = [
        _bar(100, 101, 99, 100, offset_min=0),
        _bar(100, 102, 99, 101, offset_min=5),
        _bar(101, 103, 100, 102, offset_min=10),
        _bar(102, 104, 101, 103, offset_min=15),
    ]
    for b in bars:
        window.append(b)
        emitter.update(window)
    snap = emitter.snapshot()
    base = snap["__baseline__"]
    # 4 bars in → 3 outcomes credited (each successor vs predecessor).
    assert base["n"] == 3
    assert base["p_up"] == 1.0  # all 3 successive closes went up
    assert base["mean_ret"] > 0


def test_detect_all_in_csv_returns_emitter_and_per_bar_detections():
    bars = [
        _bar(105, 106, 100, 101, offset_min=0),
        _bar(100, 110, 99, 108, offset_min=5),   # bullish engulfing
        _bar(108, 109, 107, 108.5, offset_min=10),
        _bar(108.5, 109, 107.5, 108, offset_min=15),
    ]
    detections, emitter = detect_all_in_csv(bars)
    assert len(detections) == 4
    # Second bar should fire bullish engulfing.
    assert Pattern.BULLISH_ENGULFING in detections[1][1]
    # First bar can't fire any 2-bar pattern.
    assert detections[0][1] == []
    snap = emitter.snapshot()
    assert snap[Pattern.BULLISH_ENGULFING]["n"] >= 1


# ════════════════════════════════════════════════════════════════════
# Expanded pattern library (2026-06-10) — doji / marubozu / outside bar /
# tweezer / harami / piercing / dark cloud / star / soldiers / crows
# ════════════════════════════════════════════════════════════════════


# ─────────────────────── doji family ──────────────────────────────


def test_generic_doji_positive():
    # Goal: small body, wicks present but NEITHER ≥ 30 % (which would
    # trigger long-legged) AND not gravestone (lower wick ≤ 10 %) AND not
    # dragonfly (upper wick ≤ 10 %).  Geometry chosen to be deterministic
    # under float arithmetic (no edge-of-threshold values):
    #   body = 0.02 / range = 0.21 → body_frac ≈ 9.5 % (clearly < 10 %)
    #   upper wick = 0.04 → 19 %  (between 10 and 30 — disqualifies all
    #                              specific sub-classifiers but counts
    #                              as "wick present" so still a doji)
    #   lower wick = 0.15 → 71 %  (≥ 50 % but upper > 10 %, so NOT dragonfly)
    events = detect_patterns([
        _bar(100, 100.5, 99.5, 100, offset_min=0),
        _bar(100, 100.06, 99.85, 100.02, offset_min=5),
    ])
    names = {e.name for e in events}
    assert Pattern.DOJI in names
    assert Pattern.LONG_LEGGED_DOJI not in names
    assert Pattern.DRAGONFLY_DOJI not in names
    assert Pattern.GRAVESTONE_DOJI not in names


def test_long_legged_doji_positive():
    # tall wicks on BOTH sides (≥30% of range each), tiny body
    events = detect_patterns([
        _bar(100, 100.5, 99.5, 100, offset_min=0),
        _bar(100, 105, 95, 100.05, offset_min=5),  # body 0.05 / range 10 = 0.5%
    ])
    names = {e.name for e in events}
    assert Pattern.LONG_LEGGED_DOJI in names
    assert Pattern.DOJI not in names  # specificity: long-legged shadows generic


def test_gravestone_doji_positive():
    # body at bottom, tall top wick, tiny bottom wick
    events = detect_patterns([
        _bar(100, 100.5, 99.5, 100, offset_min=0),
        _bar(100, 110, 99.95, 100.05, offset_min=5),
    ])
    names = {e.name for e in events}
    assert Pattern.GRAVESTONE_DOJI in names


def test_dragonfly_doji_positive():
    # body at top, tall bottom wick, tiny top wick
    events = detect_patterns([
        _bar(100, 100.5, 99.5, 100, offset_min=0),
        _bar(100, 100.05, 90, 99.95, offset_min=5),
    ])
    names = {e.name for e in events}
    assert Pattern.DRAGONFLY_DOJI in names


def test_doji_negative_when_body_large():
    events = detect_patterns([
        _bar(100, 100.5, 99.5, 100, offset_min=0),
        _bar(100, 105, 99, 104, offset_min=5),  # body 4 / range 6 → 67% way over 10%
    ])
    names = {e.name for e in events}
    assert not (names & {Pattern.DOJI, Pattern.LONG_LEGGED_DOJI,
                          Pattern.GRAVESTONE_DOJI, Pattern.DRAGONFLY_DOJI})


# ───────────────────────── marubozu ───────────────────────────────


def test_bullish_marubozu_positive():
    # No wicks: open == low, close == high
    events = detect_patterns([
        _bar(100, 100.5, 99.5, 100, offset_min=0),
        _bar(100, 105, 100, 105, offset_min=5),
    ])
    names = {e.name for e in events}
    assert Pattern.BULLISH_MARUBOZU in names


def test_bearish_marubozu_positive():
    events = detect_patterns([
        _bar(100, 100.5, 99.5, 100, offset_min=0),
        _bar(105, 105, 100, 100, offset_min=5),
    ])
    names = {e.name for e in events}
    assert Pattern.BEARISH_MARUBOZU in names


def test_marubozu_negative_with_wicks():
    # Even small wicks (>5% range) disqualify
    events = detect_patterns([
        _bar(100, 100.5, 99.5, 100, offset_min=0),
        _bar(100, 106, 99, 105, offset_min=5),  # 1pt wick on top, 1pt on bottom → 14% of range
    ])
    names = {e.name for e in events}
    assert Pattern.BULLISH_MARUBOZU not in names


# ──────────────────────── outside bar ─────────────────────────────


def test_bullish_outside_bar_positive():
    events = detect_patterns([
        _bar(100, 102, 99, 101, offset_min=0),
        _bar(101, 104, 98, 103, offset_min=5),  # high > 102, low < 99, close > open
    ])
    names = {e.name for e in events}
    assert Pattern.BULLISH_OUTSIDE_BAR in names


def test_bearish_outside_bar_positive():
    events = detect_patterns([
        _bar(100, 102, 99, 101, offset_min=0),
        _bar(101, 104, 98, 99, offset_min=5),  # range engulfs, close < open
    ])
    names = {e.name for e in events}
    assert Pattern.BEARISH_OUTSIDE_BAR in names


def test_outside_bar_negative_when_inside():
    events = detect_patterns([
        _bar(100, 105, 95, 102, offset_min=0),
        _bar(101, 103, 99, 102, offset_min=5),
    ])
    names = {e.name for e in events}
    assert not (names & {Pattern.BULLISH_OUTSIDE_BAR, Pattern.BEARISH_OUTSIDE_BAR})


# ──────────────────────── tweezer ─────────────────────────────────


def test_tweezer_top_positive():
    # First bar bullish closing at high, second bar bearish opening near
    # first high — equal highs within tolerance, opposite colors.
    events = detect_patterns([
        _bar(100, 110, 99, 109, offset_min=0),       # bull
        _bar(108, 110.1, 102, 103, offset_min=5),    # bear, high within 0.1 of 110
    ])
    names = {e.name for e in events}
    assert Pattern.TWEEZER_TOP in names


def test_tweezer_bottom_positive():
    events = detect_patterns([
        _bar(110, 111, 100, 101, offset_min=0),      # bear
        _bar(102, 108, 99.9, 107, offset_min=5),     # bull, low within 0.1 of 100
    ])
    names = {e.name for e in events}
    assert Pattern.TWEEZER_BOTTOM in names


def test_tweezer_negative_when_highs_too_far():
    events = detect_patterns([
        _bar(100, 110, 99, 109, offset_min=0),       # range = 11 → tol = 1.1
        _bar(108, 113, 102, 103, offset_min=5),      # high diff = 3 > tol
    ])
    names = {e.name for e in events}
    assert Pattern.TWEEZER_TOP not in names


# ───────────────────────── harami ─────────────────────────────────


def test_bullish_harami_positive():
    # Prior bar: big bear body 110→100.  Current: small bull body 102→105 (INSIDE prev body).
    events = detect_patterns([
        _bar(110, 110.5, 99.5, 100, offset_min=0),   # body=10, range=11 → 91%
        _bar(102, 105.2, 101.5, 105, offset_min=5),  # body=3, well inside [100,110]
    ])
    names = {e.name for e in events}
    assert Pattern.BULLISH_HARAMI in names


def test_bearish_harami_positive():
    events = detect_patterns([
        _bar(100, 110.5, 99.5, 110, offset_min=0),   # big bull body
        _bar(108, 109, 105, 106, offset_min=5),      # small bear inside
    ])
    names = {e.name for e in events}
    assert Pattern.BEARISH_HARAMI in names


def test_harami_negative_when_curr_outside_prev_body():
    events = detect_patterns([
        _bar(110, 110.5, 99.5, 100, offset_min=0),
        _bar(105, 115, 104, 112, offset_min=5),  # close above prev body top
    ])
    names = {e.name for e in events}
    assert Pattern.BULLISH_HARAMI not in names


# ────────────────── piercing / dark cloud cover ───────────────────


def test_piercing_positive():
    # Prior: bear 110→100 (body=10).  Mid = 105.
    # Current: opens below 99.5, closes above 105 (penetrates >50% body).
    events = detect_patterns([
        _bar(110, 110.5, 99.5, 100, offset_min=0),
        _bar(98, 108, 97, 107, offset_min=5),
    ])
    names = {e.name for e in events}
    assert Pattern.PIERCING in names


def test_dark_cloud_cover_positive():
    # Prior: bull 100→110.  Mid = 105.
    # Current: opens above 110.5, closes below 105.
    events = detect_patterns([
        _bar(100, 110.5, 99.5, 110, offset_min=0),
        _bar(112, 113, 102, 103, offset_min=5),
    ])
    names = {e.name for e in events}
    assert Pattern.DARK_CLOUD_COVER in names


def test_piercing_negative_when_no_penetration():
    events = detect_patterns([
        _bar(110, 110.5, 99.5, 100, offset_min=0),
        _bar(98, 104, 97, 103, offset_min=5),   # close 103 < mid 105 → no piercing
    ])
    names = {e.name for e in events}
    assert Pattern.PIERCING not in names


# ─────────────────── morning / evening star ───────────────────────


def test_morning_star_positive():
    # Bar 1: big bear 110→100.  Bar 2: small body 99→99.5 (star).
    # Bar 3: big bull closing above mid of bar 1 (105).
    events = detect_patterns([
        _bar(110, 110.5, 99.5, 100, offset_min=0),
        _bar(99, 100, 98.5, 99.5, offset_min=5),
        _bar(99.5, 108, 99, 107, offset_min=10),
    ])
    names = {e.name for e in events}
    assert Pattern.MORNING_STAR in names


def test_evening_star_positive():
    events = detect_patterns([
        _bar(100, 110.5, 99.5, 110, offset_min=0),
        _bar(110.5, 111, 110, 110.5, offset_min=5),
        _bar(110, 110.5, 102, 103, offset_min=10),
    ])
    names = {e.name for e in events}
    assert Pattern.EVENING_STAR in names


def test_morning_star_negative_when_middle_body_too_large():
    events = detect_patterns([
        _bar(110, 110.5, 99.5, 100, offset_min=0),
        _bar(99, 105, 98, 104, offset_min=5),      # middle body = 5 (too big)
        _bar(99.5, 108, 99, 107, offset_min=10),
    ])
    names = {e.name for e in events}
    assert Pattern.MORNING_STAR not in names


# ─────────────── three white soldiers / black crows ───────────────


def test_three_white_soldiers_positive():
    events = detect_patterns([
        _bar(100, 101, 99.5, 101, offset_min=0),
        _bar(101, 102, 100.5, 102, offset_min=5),
        _bar(102, 103.5, 101.5, 103, offset_min=10),
    ])
    names = {e.name for e in events}
    assert Pattern.THREE_WHITE_SOLDIERS in names


def test_three_black_crows_positive():
    events = detect_patterns([
        _bar(103, 103.5, 102, 102, offset_min=0),
        _bar(102, 102.5, 101, 101, offset_min=5),
        _bar(101, 101.5, 100, 100, offset_min=10),
    ])
    names = {e.name for e in events}
    assert Pattern.THREE_BLACK_CROWS in names


def test_three_white_soldiers_negative_when_not_all_bullish():
    events = detect_patterns([
        _bar(100, 101, 99.5, 101, offset_min=0),
        _bar(102, 102.5, 100, 100.5, offset_min=5),  # bearish bar in the middle
        _bar(101, 102.5, 100, 102, offset_min=10),
    ])
    names = {e.name for e in events}
    assert Pattern.THREE_WHITE_SOLDIERS not in names
