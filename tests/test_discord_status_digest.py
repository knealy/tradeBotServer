"""Tests for Discord status digest builder."""

from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from core.discord_status_digest import (
    _build_daily_briefing,
    discord_status_interval_seconds,
)
from strategies.morning_range_reversion_strategy import MorningRangeReversionStrategy


@pytest.mark.parametrize(
    "value,expected",
    [
        ("3600", 3600),
        ('"1800"', 1800),
        ("0", 0),
        ("bad", 0),
    ],
)
def test_discord_status_interval_seconds(monkeypatch, value, expected):
    monkeypatch.setenv("DISCORD_STATUS_INTERVAL_SECONDS", value)
    assert discord_status_interval_seconds() == expected


def test_mrr_discord_daily_brief_skip_friday(monkeypatch):
    """Friday skip gate surfaces in the digest."""
    bot = SimpleNamespace(strategy_manager=None)
    cfg = SimpleNamespace(symbols=["MNQ"])
    strat = MorningRangeReversionStrategy.__new__(MorningRangeReversionStrategy)
    strat.config = cfg
    strat.trading_bot = bot
    strat._cfg = MorningRangeReversionStrategy(trading_bot=bot)._cfg
    strat._tz = ZoneInfo("America/New_York")
    strat._state = {}
    strat._live_trade_history = None
    strat._replay_engine = None
    # Copy init attrs used by brief
    base = MorningRangeReversionStrategy(trading_bot=bot)
    for attr in (
        "range_start", "range_end_open", "flat_before", "timeframe",
        "allow_long", "allow_short", "require_reentry_close",
        "max_fades_per_session", "_WEEKDAY_ALIASES_INV",
    ):
        setattr(strat, attr, getattr(base, attr))

    monkeypatch.setattr(
        strat, "_skip_weekdays", lambda sym: frozenset({4})  # Fri
    )
    monkeypatch.setattr(
        strat, "_consec_loss_breaker_status",
        lambda sym, d, bars=None: {"blocked": False, "streak": 0, "reason": "ok"},
    )
    monkeypatch.setattr(strat, "_reentry_threshold_pts", lambda sym: 7.0)

    # 2026-06-19 is a Friday
    now_et = datetime(2026, 6, 19, 8, 30, tzinfo=ZoneInfo("America/New_York"))
    lines = strat.discord_daily_brief(now_et=now_et)
    assert any("SKIP today" in ln and "Fri" in ln for ln in lines)


def test_build_daily_briefing_no_manager():
    bot = SimpleNamespace(strategy_manager=None)
    lines = _build_daily_briefing(bot)  # type: ignore[arg-type]
    assert any("Today (ET)" in ln for ln in lines)
    assert any("manager not loaded" in ln for ln in lines)
