"""Unit tests for session_fibonacci_sweep_math (Pine bot-port mirror)."""

from __future__ import annotations

import pytest

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from strategies.session_fibonacci_sweep_math import (
    FIB_RATIOS,
    ZONES,
    ZONE_ORDER,
    DEFAULT_SESSIONS,
    avg_like_session_ranges,
    build_fade_setup,
    build_session_fib_overlays,
    closer_zones_toward_zero,
    detect_fvg,
    first_swept_zone,
    fvg_inverted,
    ib_anchor_ready,
    ifvg_dir_after_invert,
    maybe_session_fade,
    minutes_in_session,
    next_wider_zone,
    normalize_zone_names,
    overlaps_band,
    parse_hhmm_to_minutes,
    path_tp_raws,
    price_touches_zone,
    pull_take_profit,
    project_levels,
    resolve_distance,
    session_allows_signal,
    session_duration_minutes,
    sweep_confirmed,
    sweep_ifvg_confirmed,
    sweep_pierced,
    true_range,
    wilder_atr,
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
    # Default mode is ATR of last 2
    assert resolve_distance(9.0, ranges) == pytest.approx(15.0)
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


def test_path_tps_are_next_zones_toward_zero():
    assert closer_zones_toward_zero("mid") == ("inner",)
    assert closer_zones_toward_zero("full") == ("mid", "inner")
    assert closer_zones_toward_zero("inner") == ()

    # 0.5 up-sweep short: TP1 at 0.236 zone (same side), TP2 at fib 0 — not 0 / -0.5
    classic = build_fade_setup(100.0, 10.0, "up", "mid")
    path = build_fade_setup(100.0, 10.0, "up", "mid", tp_structure="path")
    assert path.stop == pytest.approx(classic.stop)
    assert path.entry == pytest.approx(classic.entry)
    assert path.tp1 == pytest.approx(zone_band(100.0, 10.0, "inner", "up").mid)
    assert path.tp2 == pytest.approx(100.0)
    assert path.tp1 != pytest.approx(classic.tp1)
    r1, r2, r3 = path_tp_raws(100.0, 10.0, "up", "mid")
    assert (r1, r2, r3) == pytest.approx((path.tp1, path.tp2, path.tp3))

    inner = build_fade_setup(100.0, 10.0, "up", "inner", tp_structure="path")
    assert inner.tp1 == pytest.approx(100.0)
    assert inner.tp2 == pytest.approx(zone_band(100.0, 10.0, "inner", "down").mid)

    long_mid = build_fade_setup(100.0, 10.0, "down", "mid", tp_structure="path")
    assert long_mid.tp1 == pytest.approx(zone_band(100.0, 10.0, "inner", "down").mid)
    assert long_mid.tp2 == pytest.approx(100.0)
    assert long_mid.stop < long_mid.entry


def test_tp_pull_sits_in_front_of_fibs():
    assert pull_take_profit(100.0, 102.5, "short", 0.0) == 100.0
    assert pull_take_profit(100.0, 102.5, "short", 0.4) == pytest.approx(100.4)
    assert pull_take_profit(105.0, 102.5, "long", 0.4) == pytest.approx(104.6)
    # Do not pull through entry
    assert pull_take_profit(100.0, 100.2, "short", 5.0, min_tick=0.1) == pytest.approx(100.1)

    short = build_fade_setup(100.0, 10.0, "up", "inner", tp_offset=0.5)
    assert short.tp1 == pytest.approx(100.5)
    assert short.tp1 < short.entry
    assert short.tp2 < short.tp1  # opposite-side TP is still beyond fib 0
    long = build_fade_setup(100.0, 10.0, "down", "mid", tp_offset=0.5)
    assert long.tp1 == pytest.approx(99.5)
    assert long.tp1 > long.entry

    pulled = maybe_session_fade(
        in_session=True,
        high=102.9,
        low=100.0,
        close=102.0,
        anchor=100.0,
        distance=10.0,
        zone="inner",
        side="up",
        tp_offset=0.3,
    )
    assert pulled is not None
    assert pulled.tp1 == pytest.approx(100.3)


def test_wilder_atr_and_true_range():
    assert true_range(10.0, 8.0) == pytest.approx(2.0)
    assert true_range(10.0, 8.0, 11.0) == pytest.approx(3.0)
    assert wilder_atr([], 1) == 0.0
    assert wilder_atr([2.0, 4.0, 6.0], 1) == pytest.approx(6.0)
    # Length 3 seed = mean of first 3
    assert wilder_atr([2.0, 4.0, 6.0], 3) == pytest.approx(4.0)


def test_overlay_fade_tps_pulled_in_front_of_fibs():
    day0 = datetime(2024, 6, 3, tzinfo=ZoneInfo("America/New_York"))  # Monday
    bars = []
    for d in range(3):
        day = day0 + timedelta(days=d)
        start = _et_unix(day.year, day.month, day.day, 8, 0)
        for i in range(180):
            t = start + i * 60
            if d == 2 and i == 40:
                # Wick through inner high, close back toward fib 0
                bars.append(
                    {"time": t, "open": 100.0, "high": 103.0, "low": 99.8, "close": 100.1}
                )
            else:
                bars.append(
                    {"time": t, "open": 100.0, "high": 100.4, "low": 99.6, "close": 100.0}
                )
        after = start + 180 * 60
        for i in range(5):
            bars.append(
                {"time": after + i * 60, "open": 100.0, "high": 100.1, "low": 99.9, "close": 100.0}
            )

    kwargs = dict(
        atr_len=2,
        ib_minutes=30,
        delay_until_ib=True,
        max_sessions=5,
        sessions={"NY AM": ("08:00", "11:00")},
        zone_names=("inner",),
        min_tick=0.01,
    )
    pulled = build_session_fib_overlays(bars, tp_pull_mult=0.1, **kwargs)
    unpulled = build_session_fib_overlays(bars, tp_pull_mult=0.0, **kwargs)
    assert pulled and unpulled
    pf = next((f for g in pulled for f in (g.get("fades") or []) if f.get("fade") == "short"), None)
    uf = next((f for g in unpulled for f in (g.get("fades") or []) if f.get("fade") == "short"), None)
    assert pf is not None and uf is not None
    assert uf["tp1"] == pytest.approx(unpulled[0]["anchor"])
    assert pf["tp1"] > uf["tp1"]
    assert pf["tp1"] < pf["entry"]
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


def test_session_allows_signal_and_maybe_fade_gated():
    assert session_allows_signal(8 * 60 + 30, "08:00", "11:00")
    assert not session_allows_signal(12 * 60, "08:00", "11:00")
    # Perfect up-sweep of inner — still no setup outside the session clock
    up = zone_band(100.0, 10.0, "inner", "up")
    assert sweep_confirmed(102.9, 100.0, 102.0, up, "up", full_pierce=True)
    assert maybe_session_fade(
        in_session=False,
        high=102.9,
        low=100.0,
        close=102.0,
        anchor=100.0,
        distance=10.0,
        zone="inner",
        side="up",
    ) is None
    live = maybe_session_fade(
        in_session=True,
        high=102.9,
        low=100.0,
        close=102.0,
        anchor=100.0,
        distance=10.0,
        zone="inner",
        side="up",
    )
    assert live is not None
    assert live.fade == "short"


def test_parse_and_session_membership():
    assert parse_hhmm_to_minutes("18:30") == 18 * 60 + 30
    assert parse_hhmm_to_minutes("0130") == 90
    assert session_duration_minutes("18:30", "00:00") == 330
    assert minutes_in_session(19 * 60, "18:30", "00:00")
    assert minutes_in_session(23 * 60 + 59, "18:30", "00:00")
    assert not minutes_in_session(0, "18:30", "00:00")  # end exclusive
    assert minutes_in_session(8 * 60 + 30, "08:00", "11:00")
    assert not minutes_in_session(11 * 60, "08:00", "11:00")


def test_normalize_zone_names():
    assert normalize_zone_names(["in", "ext", "mid"]) == ("inner", "extension", "mid")
    assert normalize_zone_names([]) == ("inner", "mid")
    assert normalize_zone_names(["full", "full", "nope"]) == ("full",)


def _et_unix(y, m, d, hh, mm):
    tz = ZoneInfo("America/New_York")
    return int(datetime(y, m, d, hh, mm, tzinfo=tz).timestamp())


def test_build_session_fib_overlays_ny_am_atr():
    # Seed several prior NY AM sessions so ATR(2) has distance, then print one.
    bars = []
    # 7 weekdays of NY AM 08:00–11:00 ET with distinct H–L so ATR is non-zero
    day0 = datetime(2024, 6, 3, tzinfo=ZoneInfo("America/New_York"))  # Monday
    for d in range(7):
        day = day0 + timedelta(days=d)
        start = _et_unix(day.year, day.month, day.day, 8, 0)
        # 180 one-minute bars covering the session
        for i in range(180):
            px = 100.0 + d * 0.5
            half = 1.0 + d * 0.2
            t = start + i * 60
            bars.append(
                {
                    "time": t,
                    "open": px,
                    "high": px + half,
                    "low": px - half,
                    "close": px + 0.1,
                }
            )
        # pad after session so finalizeRange fires
        after = start + 180 * 60
        for i in range(5):
            bars.append(
                {
                    "time": after + i * 60,
                    "open": 100.0,
                    "high": 100.1,
                    "low": 99.9,
                    "close": 100.0,
                }
            )

    # Only NY AM so other sessions don't clutter
    overlays = build_session_fib_overlays(
        bars,
        atr_len=2,
        ib_minutes=30,
        delay_until_ib=True,
        max_sessions=10,
        sessions={"NY AM": ("08:00", "11:00")},
        zone_names=("inner", "mid", "extension"),
    )
    assert overlays, "expected at least one printed NY AM grid after ATR warmup"
    latest = overlays[0]
    assert latest["name"] == "NY AM"
    assert latest["distance"] > 0
    assert latest["grid_end"] > latest["grid_start"]
    names = {(z["name"], z["side"]) for z in latest["zones"]}
    assert ("inner", "up") in names
    assert ("extension", "down") in names
    assert ("full", "up") not in names

    inner_only = build_session_fib_overlays(
        bars,
        atr_len=2,
        ib_minutes=30,
        delay_until_ib=True,
        max_sessions=4,
        sessions={"NY AM": ("08:00", "11:00")},
        session_zones={"NY AM": ("mid",)},
    )
    assert inner_only
    znames = {z["name"] for z in inner_only[0]["zones"]}
    assert znames == {"mid"}
