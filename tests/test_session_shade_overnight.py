"""Overnight session shade segments for trade recap charts."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from core.backtest.session_shade import overnight_range_baseline_segments


def _utc_ts(y: int, mo: int, d: int, h: int, mi: int) -> int:
    dt = datetime(y, mo, d, h, mi, 0, tzinfo=timezone.utc)
    return int(dt.timestamp())


def test_overnight_segments_evening_and_morning_share_hi_lo():
    # Monday May 12 2025 evening ET = 2025-05-12 23:30 UTC is 19:30 EDT; morning May 13 13:00 UTC = 09:00 EDT
    bars = [
        {"time": _utc_ts(2025, 5, 12, 23, 30), "high": 21010.0, "low": 20990.0},
        {"time": _utc_ts(2025, 5, 13, 13, 0), "high": 21020.0, "low": 20980.0},
    ]
    segs = overnight_range_baseline_segments(
        bars,
        overnight_start="19:00",
        overnight_end="9:29",
        zone="America/New_York",
        reference_unix=bars[1]["time"],
    )
    assert len(segs) >= 1
    hi = max(s["hi"] for s in segs)
    lo = min(s["lo"] for s in segs)
    assert hi == 21020.0
    assert lo == 20980.0
