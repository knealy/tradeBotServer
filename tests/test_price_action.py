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
