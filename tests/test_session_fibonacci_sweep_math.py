"""Unit tests for session_fibonacci_sweep_math (Pine bot-port mirror)."""

from __future__ import annotations

import pytest

from strategies.session_fibonacci_sweep_math import (
    FIB_RATIOS,
    ZONES,
    ZONE_ORDER,
    avg_like_session_ranges,
    build_fade_setup,
    first_swept_zone,
    ib_anchor_ready,
    next_wider_zone,
    price_touches_zone,
    project_levels,
    resolve_distance,
    zone_band,
)


def test_project_levels_symmetric():
    book = project_levels(100.0, 10.0)
    assert book.anchor == 100.0
    assert book.up[0.5] == pytest.approx(105.0)
    assert book.down[0.5] == pytest.approx(95.0)
    assert book.up[1.0] == pytest.approx(110.0)
    assert book.down[1.618] == pytest.approx(100.0 - 16.18)
    assert set(book.up) == set(FIB_RATIOS)


def test_project_levels_rejects_bad_distance():
    with pytest.raises(ValueError):
        project_levels(100.0, 0.0)


def test_zone_band_up_and_down():
    up = zone_band(100.0, 10.0, "inner", "up")
    dn = zone_band(100.0, 10.0, "inner", "down")
    assert up.lo == pytest.approx(102.36)
    assert up.hi == pytest.approx(102.795)
    assert dn.hi == pytest.approx(97.64)  # closer to anchor
    assert dn.lo == pytest.approx(97.205)
    assert up.mid == pytest.approx((up.lo + up.hi) / 2)


def test_zone_order_and_wider():
    assert ZONE_ORDER[0] == "inner"
    assert next_wider_zone("inner") == "mid"
    assert next_wider_zone("mid") == "full"
    assert next_wider_zone("full") == "extension"
    assert next_wider_zone("extension") == "extension"
    assert set(ZONES) == set(ZONE_ORDER)


def test_resolve_distance_modes():
    ranges = [8.0, 10.0, 12.0, 14.0, 16.0]
    assert resolve_distance(9.0, ranges, mode="previous") == 9.0
    assert resolve_distance(9.0, ranges, mode="atr", atr_len=3) == pytest.approx(14.0)
    assert resolve_distance(None, [], mode="previous") is None
    assert resolve_distance(0.0, ranges, mode="previous", min_tick=0.25) is None


def test_avg_like_session_ranges():
    assert avg_like_session_ranges([1, 2, 3, 4], 2) == pytest.approx(3.5)
    assert avg_like_session_ranges([], 5) is None


def test_price_touches_and_first_swept():
    band = zone_band(100.0, 10.0, "inner", "up")
    assert price_touches_zone(102.5, 102.4, band)
    assert not price_touches_zone(101.0, 100.5, band)

    hit = first_swept_zone(102.5, 102.4, 100.0, 10.0)
    assert hit == ("up", "inner")

    hit_dn = first_swept_zone(99.0, 97.5, 100.0, 10.0)
    assert hit_dn == ("down", "inner")

    assert first_swept_zone(100.5, 99.5, 100.0, 10.0) is None


def test_build_fade_setup_short_and_long():
    short = build_fade_setup(100.0, 10.0, "up", "inner", stop_buffer_ratio=0.05)
    assert short.fade == "short"
    assert short.tp1 == 100.0
    assert short.entry == pytest.approx(zone_band(100.0, 10.0, "inner", "up").mid)
    assert short.stop > short.entry
    assert short.tp2 == pytest.approx(zone_band(100.0, 10.0, "inner", "down").mid)
    assert short.tp3 == pytest.approx(zone_band(100.0, 10.0, "mid", "down").mid)

    long = build_fade_setup(100.0, 10.0, "down", "mid", stop_buffer_ratio=0.1)
    assert long.fade == "long"
    assert long.stop < long.entry
    assert long.tp2 > long.tp1  # opposite mid is above anchor


def test_ib_anchor_ready():
    start = 1_000_000
    ib_min = 30
    end = start + ib_min * 60 * 1000
    assert not ib_anchor_ready(start, end - 1, end - 60_000, ib_min)
    assert ib_anchor_ready(start, end, end - 60_000, ib_min)
    assert not ib_anchor_ready(start, end + 60_000, end, ib_min)  # already past first bar
    assert ib_anchor_ready(start, end, None, ib_min)
