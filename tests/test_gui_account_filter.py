"""Tests for GUI account blocklist / default-selection helpers."""
import os
from unittest.mock import patch

import pytest

from gui.chart_html import (
    _account_id_str,
    _gui_account_blocklist,
    _gui_eligible_accounts,
    _pick_default_gui_account,
)


def _acc(acc_id: str, name: str = "Test", balance: float = 1000.0, status: str = "active"):
    return {"id": acc_id, "name": name, "balance": balance, "status": status}


class TestGuiAccountBlocklist:
    def test_empty_when_unset(self, monkeypatch):
        monkeypatch.delenv("GUI_ACCOUNT_BLOCKLIST", raising=False)
        assert _gui_account_blocklist() == set()

    def test_parses_comma_list(self, monkeypatch):
        monkeypatch.setenv("GUI_ACCOUNT_BLOCKLIST", "22182502, 99999")
        assert _gui_account_blocklist() == {"22182502", "99999"}


class TestGuiEligibleAccounts:
    def test_drops_blocklisted(self, monkeypatch):
        monkeypatch.setenv("GUI_ACCOUNT_BLOCKLIST", "22182502")
        accounts = [_acc("22182502", "EMPTY"), _acc("111", "PRAC-V2")]
        eligible = _gui_eligible_accounts(accounts)
        assert len(eligible) == 1
        assert _account_id_str(eligible[0]) == "111"

    def test_drops_inactive_status(self, monkeypatch):
        monkeypatch.delenv("GUI_ACCOUNT_BLOCKLIST", raising=False)
        accounts = [_acc("1", status="closed"), _acc("2")]
        eligible = _gui_eligible_accounts(accounts)
        assert len(eligible) == 1
        assert _account_id_str(eligible[0]) == "2"


class TestPickDefaultGuiAccount:
    def test_prefers_env_default(self, monkeypatch):
        monkeypatch.delenv("GUI_ACCOUNT_BLOCKLIST", raising=False)
        monkeypatch.setenv("GUI_DEFAULT_ACCOUNT_ID", "222")
        accounts = [_acc("111", "PRAC-A", 50000), _acc("222", "PRAC-B", 1000)]
        pick = _pick_default_gui_account(accounts)
        assert _account_id_str(pick) == "222"

    def test_prefers_practice_highest_balance(self, monkeypatch):
        monkeypatch.delenv("GUI_ACCOUNT_BLOCKLIST", raising=False)
        monkeypatch.delenv("GUI_DEFAULT_ACCOUNT_ID", raising=False)
        accounts = [
            _acc("1", "50KTC EVAL", 50000),
            _acc("2", "PRAC-V2", 48000),
            _acc("3", "PRAC-OLD", 10000),
        ]
        pick = _pick_default_gui_account(accounts)
        assert _account_id_str(pick) == "2"
