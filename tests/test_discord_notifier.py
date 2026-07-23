"""Discord notifier env gating and helpers."""

import os

import pytest

from core.discord_notifier import (
    _env_flag,
    notify_fills_enabled,
    notify_orders_enabled,
    notify_signals_enabled,
)


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, False),
        ("", False),
        ("1", True),
        ('"1"', True),
        ("'1'", True),
        ("0", False),
        ('"0"', False),
        ("true", True),
        ("false", False),
        ("yes", True),
        ("off", False),
    ],
)
def test_env_flag_parses_quoted_values(monkeypatch, value, expected):
    monkeypatch.delenv("TEST_FLAG", raising=False)
    if value is not None:
        monkeypatch.setenv("TEST_FLAG", value)
    assert _env_flag("TEST_FLAG", False) is expected


def test_notify_signals_default_on_when_unset(monkeypatch):
    monkeypatch.delenv("DISCORD_NOTIFY_SIGNALS", raising=False)
    assert notify_signals_enabled() is True


def test_notify_signals_respects_explicit_off(monkeypatch):
    monkeypatch.setenv("DISCORD_NOTIFY_SIGNALS", '"0"')
    assert notify_signals_enabled() is False


def test_notify_fills_quoted_one(monkeypatch):
    monkeypatch.setenv("DISCORD_NOTIFY_FILLS", '"1"')
    assert notify_fills_enabled() is True


def test_notify_orders_default_off(monkeypatch):
    monkeypatch.delenv("DISCORD_NOTIFY_ORDERS", raising=False)
    assert notify_orders_enabled() is False
