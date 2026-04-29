"""
US cash-session calendar helpers for strategy timing.

Conservative NYSE full-closure dates (observed weekends) used to skip
session-boundary logic when there is no regular cash RTH session.
Does not model CME Globex maintenance; extend as needed.
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta
from typing import Any, Dict


def _western_easter_sunday(year: int) -> date:
    """Anonymous Gregorian algorithm (Meeus/Jones/Butcher)."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _nyse_observed_close(civil: date) -> date:
    """NYSE-style observed closure for a fixed civil holiday date."""
    wd = civil.weekday()
    if wd == 5:  # Saturday -> preceding Friday
        return civil - timedelta(days=1)
    if wd == 6:  # Sunday -> following Monday
        return civil + timedelta(days=1)
    return civil


def _nth_weekday_of_month(year: int, month: int, weekday: int, n: int) -> date:
    """``weekday``: Monday=0 … Sunday=6; ``n`` 1-based (1 = first)."""
    first = date(year, month, 1)
    shift = (weekday - first.weekday()) % 7
    return first + timedelta(days=shift + 7 * (n - 1))


def _last_weekday_of_month(year: int, month: int, weekday: int) -> date:
    last_day = calendar.monthrange(year, month)[1]
    d = date(year, month, last_day)
    while d.weekday() != weekday:
        d -= timedelta(days=1)
    return d


def _nyse_full_close_dates_for_year(year: int) -> set[date]:
    s: set[date] = set()
    s.add(_nyse_observed_close(date(year, 1, 1)))
    s.add(_nth_weekday_of_month(year, 1, 0, 3))  # MLK
    s.add(_nth_weekday_of_month(year, 2, 0, 3))  # Presidents
    s.add(_western_easter_sunday(year) - timedelta(days=2))  # Good Friday
    s.add(_last_weekday_of_month(year, 5, 0))  # Memorial (last Monday May)
    if year >= 2021:
        s.add(_nyse_observed_close(date(year, 6, 19)))  # Juneteenth
    s.add(_nyse_observed_close(date(year, 7, 4)))
    s.add(_nth_weekday_of_month(year, 9, 0, 1))  # Labor
    s.add(_nth_weekday_of_month(year, 11, 3, 4))  # Thanksgiving (4th Thu)
    s.add(_nyse_observed_close(date(year, 12, 25)))
    return s


_NYSE_FULL_CLOSURE: frozenset[date] = frozenset(
    d for y in range(2020, 2036) for d in _nyse_full_close_dates_for_year(y)
)


def equity_futures_session_note(d: date) -> Dict[str, Any]:
    """
    Return whether a regular US equity cash session is expected on ``d`` (local ET date).

    ``trade_recommended`` False means skip session-open range logic that assumes a normal RTH open.
    """
    if d.weekday() >= 5:
        return {"trade_recommended": False, "reason": "weekend"}
    if d in _NYSE_FULL_CLOSURE:
        return {
            "trade_recommended": False,
            "reason": "NYSE full closure (observed calendar)",
        }
    return {"trade_recommended": True, "reason": ""}
