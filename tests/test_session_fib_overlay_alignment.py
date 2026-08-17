"""Mirror checks for frontend session fib overlay defaults (keeps TS/Python aligned)."""

from strategies.session_fibonacci_sweep_math import (
    DEFAULT_SESSIONS,
    ZONES,
    ZONE_ORDER,
    project_levels,
    resolve_distance,
    zone_band,
)


def test_frontend_default_sessions_aligned():
    assert DEFAULT_SESSIONS["Tokyo"] == ("18:30", "00:00")
    assert DEFAULT_SESSIONS["London"] == ("01:30", "05:00")
    assert DEFAULT_SESSIONS["NY AM"] == ("08:00", "11:00")
    assert DEFAULT_SESSIONS["NY PM"] == ("13:00", "16:00")


def test_overlay_default_zones_exist():
    # Frontend overlay defaults to inner/mid/extension (full off)
    for name in ("inner", "mid", "extension"):
        assert name in ZONES
        assert name in ZONE_ORDER


def test_resolve_distance_atr_default_matches_overlay():
    ranges = [8.0, 10.0, 12.0, 14.0, 16.0]
    assert resolve_distance(9.0, ranges) == 12.0  # atr n=5 default


def test_zone_band_for_overlay_fill():
    up = zone_band(100.0, 10.0, "inner", "up")
    dn = zone_band(100.0, 10.0, "inner", "down")
    assert up.lo > 100
    assert dn.hi < 100
    book = project_levels(100.0, 10.0)
    assert book.up[1.0] == 110.0
