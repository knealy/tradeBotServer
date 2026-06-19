"""Regression tests for ``gui.chart_html._compute_next_launch``.

Pin the timezone math + day-of-week skip logic so the dashboard's
"arms in 9h 12m" countdown stays correct across DST transitions and
weekend boundaries.

Notes:
* All ``now`` inputs are explicit UTC datetimes — the function converts
  internally to America/New_York.
* Schedules are weekday-only for all three range strategies; we
  exercise the Friday-evening / Saturday / Sunday boundaries.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from gui.chart_html import _compute_next_launch


def _utc(y, mo, d, h=0, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=timezone.utc)


def test_unknown_strategy_returns_manual():
    out = _compute_next_launch("simple_candle", now=_utc(2026, 6, 16, 12, 0))
    assert out["eta_iso"] is None
    assert out["eta_seconds"] is None
    assert out["label"] == "manual launch"
    assert out["schedule"] == "manual"
    assert out["kind"] == "manual"


def test_overnight_range_same_day_when_before_window():
    """Tuesday 6:00 AM ET (10:00 UTC during EDT) → arms today at 9:29 AM ET."""
    # 2026-06-16 is a Tuesday. 06:00 ET → 10:00 UTC (EDT, UTC-4).
    out = _compute_next_launch("overnight_range", now=_utc(2026, 6, 16, 10, 0))
    assert out["label"].endswith("today"), out["label"]
    assert out["kind"] == "signal"
    # 9:29 AM - 6:00 AM = 3h 29m = 12,540 seconds
    assert 12_530 <= out["eta_seconds"] <= 12_550


def test_overnight_range_advances_to_next_day_after_window():
    """Tuesday 11:00 AM ET → arms tomorrow (Wednesday) 9:29 AM ET."""
    out = _compute_next_launch("overnight_range", now=_utc(2026, 6, 16, 15, 0))
    assert out["label"].endswith("tomorrow"), out["label"]
    # ~22h 29m
    assert 80_000 < out["eta_seconds"] < 82_000


def test_weekend_skip_friday_evening_to_monday():
    """Friday 8:00 PM ET → arms Monday 9:29 AM ET (skips Sat + Sun)."""
    # 2026-06-19 is a Friday. 20:00 ET → 00:00 UTC Saturday (EDT, UTC-4).
    out = _compute_next_launch("overnight_range", now=_utc(2026, 6, 20, 0, 0))
    assert out["label"].endswith("Monday"), out["label"]
    # Friday 20:00 ET → Monday 09:29 ET = 2 days + 13h 29m = 221,340 s
    assert 221_000 < out["eta_seconds"] < 222_000


def test_saturday_advances_to_monday():
    """Saturday afternoon → Monday morning (skipping Sunday)."""
    out = _compute_next_launch("overnight_range", now=_utc(2026, 6, 20, 18, 0))
    # Saturday 14:00 ET → Monday 09:29 ET = 2 days minus a few hours = "Monday"
    assert out["label"].endswith("Monday"), out["label"]


def test_sunday_advances_to_monday():
    """Sunday afternoon → Monday morning (Monday is *tomorrow* relative
    to ``now``, so the label says ``tomorrow`` rather than the day name —
    that's the contract: ``today`` / ``tomorrow`` win over weekday name
    when applicable since they're more glanceable)."""
    out = _compute_next_launch("overnight_range", now=_utc(2026, 6, 21, 18, 0))
    assert out["label"].endswith("tomorrow"), out["label"]


def test_mrr_schedule_uses_7am_et():
    """MRR's range build starts at 7:00 AM ET, not 9:29."""
    # Tuesday 6:00 AM ET → arms today at 7:00 AM ET (1 hour).
    out = _compute_next_launch("morning_range_reversion", now=_utc(2026, 6, 16, 10, 0))
    assert out["label"].startswith("7:00 AM ET"), out["label"]
    assert 3_590 <= out["eta_seconds"] <= 3_610  # ~ 1 hour


def test_orb_schedule_uses_930_am_et():
    out = _compute_next_launch("opening_range_breakout", now=_utc(2026, 6, 16, 10, 0))
    assert out["label"].startswith("9:30 AM ET"), out["label"]
    # 9:30 AM - 6:00 AM = 3h 30m = 12,600s
    assert 12_590 <= out["eta_seconds"] <= 12_610


def test_label_format_has_when_suffix():
    """Label must end in ``today``, ``tomorrow``, or a weekday name."""
    for now in [
        _utc(2026, 6, 16, 10, 0),  # Tuesday before window
        _utc(2026, 6, 16, 20, 0),  # Tuesday after window
        _utc(2026, 6, 20, 18, 0),  # Saturday
    ]:
        for name in ("overnight_range", "morning_range_reversion", "opening_range_breakout"):
            out = _compute_next_launch(name, now=now)
            assert any(out["label"].endswith(s) for s in (
                "today", "tomorrow",
                "Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
                "Saturday", "Sunday",
            )), f"{name}@{now}: {out['label']}"
