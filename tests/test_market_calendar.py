"""NYSE closure helper used by overnight range session guard."""

from datetime import date

from core.market_calendar import equity_futures_session_note


def test_weekend_not_trade_recommended():
    assert equity_futures_session_note(date(2026, 4, 25))["trade_recommended"] is False


def test_known_nyse_holiday_2026_good_friday():
    assert equity_futures_session_note(date(2026, 4, 3))["trade_recommended"] is False


def test_regular_session():
    assert equity_futures_session_note(date(2026, 4, 28))["trade_recommended"] is True
