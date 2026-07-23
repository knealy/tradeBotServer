"""Unit tests for core.regime_sizing calendar-era multipliers."""

from __future__ import annotations

from datetime import date

import pytest

from core import regime_sizing as rs


@pytest.fixture(autouse=True)
def _enable_regime_sizing(monkeypatch):
    monkeypatch.setenv("REGIME_SIZING_ENABLED", "1")


def test_disabled_passthrough(monkeypatch):
    monkeypatch.delenv("REGIME_SIZING_ENABLED", raising=False)
    qty, reason = rs.apply_regime_sizing_quantity(
        "morning_range_reversion", "MGC", 2, session_date=date(2025, 6, 1),
    )
    assert qty == 2
    assert "disabled" in reason


def test_mgc_pre_2026_halved():
    qty, _ = rs.apply_regime_sizing_quantity(
        "morning_range_reversion", "MGC", 2, session_date=date(2025, 12, 1),
    )
    assert qty == 1


def test_mgc_post_2026_full():
    qty, _ = rs.apply_regime_sizing_quantity(
        "morning_range_reversion", "MGC", 2, session_date=date(2026, 2, 1),
    )
    assert qty == 2


def test_or_pre_oct_2025_blocked():
    qty, reason = rs.apply_regime_sizing_quantity(
        "overnight_range", "MNQ", 1, session_date=date(2025, 8, 1),
    )
    assert qty == 0
    assert "blocked" in reason


def test_or_post_oct_2025_full():
    qty, _ = rs.apply_regime_sizing_quantity(
        "overnight_range", "MGC", 1, session_date=date(2026, 1, 15),
    )
    assert qty == 1


def test_regime_trend_blocks_or():
    mult, _ = rs.resolve_regime_sizing_multiplier(
        "overnight_range", "MNQ",
        session_date=date(2026, 3, 1),
        regime_label="trend",
    )
    assert mult == 0.0
