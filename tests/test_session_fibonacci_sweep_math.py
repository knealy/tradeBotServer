"""Unit tests for session_fibonacci_sweep_math (Pine bot-port mirror)."""

from __future__ import annotations

import pytest

from strategies.session_fibonacci_sweep_math import (
    FIB_RATIOS,
    ZONES,
    ZONE_ORDER,
    DEFAULT_SESSIONS,
    avg_like_session_ranges,
    build_fade_setup,
    detect_fvg,
    first_swept_zone,
    fvg_inverted,
    ib_anchor_ready,
    ifvg_dir_after_invert,
    next_wider_zone,
    overlaps_band,
    price_touches_zone,
    project_levels,
    resolve_distance,
    sweep_confirmed,
    sweep_ifvg_confirmed,
    sweep_pierced,
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
    # Default mode is ATR of last 5
    assert resolve_distance(9.0, ranges) == pytest.approx(12.0)
    assert resolve_distance(None, [], mode="previous") is None
    assert resolve_distance(0.0, ranges, mode="previous", min_tick=0.25) is None


def test_default_sessions_match_pine():
    assert DEFAULT_SESSIONS["Tokyo"] == ("18:30", "00:00")
    assert DEFAULT_SESSIONS["London"] == ("01:30", "05:00")
    assert DEFAULT_SESSIONS["NY AM"] == ("08:00", "11:00")
    assert DEFAULT_SESSIONS["NY PM"] == ("13:00", "16:00")


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


def test_sweep_confirmation_up_and_down():
    up = zone_band(100.0, 10.0, "inner", "up")
    # Wick through far edge, close back below near edge
    assert sweep_pierced(102.9, 100.0, up, "up", full_pierce=True)
    assert sweep_confirmed(102.9, 100.0, 102.0, up, "up", full_pierce=True)
    # Pierce but close still in/above zone — not confirmed
    assert not sweep_confirmed(102.9, 100.0, 102.5, up, "up", full_pierce=True)
    # Touch near edge only — not a full pierce
    assert not sweep_pierced(102.4, 100.0, up, "up", full_pierce=True)
    assert sweep_pierced(102.4, 100.0, up, "up", full_pierce=False)
    assert sweep_confirmed(102.4, 100.0, 102.0, up, "up", full_pierce=False)

    dn = zone_band(100.0, 10.0, "inner", "down")
    assert sweep_confirmed(100.0, 97.1, 98.0, dn, "down", full_pierce=True)
    assert not sweep_confirmed(100.0, 97.1, 97.5, dn, "down", full_pierce=True)


def test_ifvg_detect_invert_and_sweep_confirm():
    # Bullish FVG: low > high[2]
    fvg = detect_fvg(high=103.0, low=102.5, high_2=102.0, low_2=101.0)
    assert fvg == ("up", 102.5, 102.0)
    assert fvg_inverted(101.5, 102.5, 102.0, "up")
    assert ifvg_dir_after_invert("up") == "down"
    assert overlaps_band(102.0, 102.5, 102.36, 102.795)

    up = zone_band(100.0, 10.0, "inner", "up")
    # Sweep confirm + overlapping bearish IFVG
    assert sweep_ifvg_confirmed(
        102.9, 100.0, 102.0, up, "up", ifvg_top=102.5, ifvg_bottom=102.0, ifvg_side="down"
    )
    # Wrong IFVG direction
    assert not sweep_ifvg_confirmed(
        102.9, 100.0, 102.0, up, "up", ifvg_top=102.5, ifvg_bottom=102.0, ifvg_side="up"
    )
    # Non-overlapping IFVG
    assert not sweep_ifvg_confirmed(
        102.9, 100.0, 102.0, up, "up", ifvg_top=110.0, ifvg_bottom=109.0, ifvg_side="down"
    )
