"""Unit tests for :func:`core.income_brain.income_brain_entry_quantity_for_bot` with a mock bot."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.income_brain import (
    income_brain_account_id_from_bot,
    income_brain_entry_quantity_for_bot,
)


class _Tracker:
    def __init__(self, pnl: float):
        self._pnl = pnl

    def get_daily_pnl(self, account_id=None) -> float:
        return float(self._pnl)


class _MiniBot:
    def __init__(self, account_id: str, balance: float, pnl: float):
        self.selected_account = {"id": account_id, "balance": balance}
        self._income_brain_cache: dict = {}
        self.account_tracker = _Tracker(pnl)


def test_account_id_from_bot_none_when_missing():
    b = _MiniBot("7", 5000.0, 0.0)
    b.selected_account = None
    assert income_brain_account_id_from_bot(b) is None


def test_entry_quantity_passthrough_when_brain_disabled(monkeypatch):
    monkeypatch.setenv("INCOME_BRAIN", "0")
    b = _MiniBot("7", 10_000.0, 0.0)
    assert income_brain_entry_quantity_for_bot(b, "body_reversion", 3) == 3


def test_entry_quantity_zero_when_daily_target_hit(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("INCOME_BRAIN", "true")
    monkeypatch.setenv("INCOME_BRAIN_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("INCOME_BRAIN_DAILY_TARGET", "100")
    monkeypatch.setenv("INCOME_BRAIN_DAILY_STOP", "-500")
    monkeypatch.setenv("INCOME_BRAIN_DIVISOR", "2500")
    monkeypatch.setenv("INCOME_BRAIN_CUSHION", "2000")
    b = _MiniBot("42", 12_000.0, pnl=150.0)
    assert income_brain_entry_quantity_for_bot(b, "overnight_range", 5) == 0


def test_entry_quantity_clamped_to_brain_size(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("INCOME_BRAIN", "true")
    monkeypatch.setenv("INCOME_BRAIN_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("INCOME_BRAIN_DAILY_TARGET", "0")
    monkeypatch.setenv("INCOME_BRAIN_DAILY_STOP", "0")
    monkeypatch.setenv("INCOME_BRAIN_DIVISOR", "2500")
    monkeypatch.setenv("INCOME_BRAIN_CUSHION", "2000")
    monkeypatch.setenv("INCOME_BRAIN_MAX_N", "2")
    b = _MiniBot("99", 10_000.0, pnl=0.0)
    # (10000 - 2000) / 2500 = 3 but max_n=2
    assert income_brain_entry_quantity_for_bot(b, "body_reversion", 99) == 2
