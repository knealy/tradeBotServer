"""Lifecycle console filter for ``core.logging_setup`` (2026-05-29 fix).

When the morning_range_reversion executor runs headless, the user wants its
high-signal lifecycle lines (``🌅 new session``, ``📐 anchor range built``,
``🔁 anchor backfill from history``, ``🎯 SHORT/LONG``, ``⏰ deadline reached``,
``🛑 max_fades reached``) to appear in the **terminal** as well as the file,
without making EVERY INFO log from the entire bot show up.

The filter implements: ``record reaches the console iff record.name is in the
lifecycle set AND record.levelno >= INFO``, OR ``record.levelno >=
console_level (default WARNING)``. This test exercises the filter in
isolation so we can pin the matrix without touching real handlers.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import logging as _logging
from core.logging_setup import (
    _LifecycleConsoleFilter,
    _LifecycleConsoleFormatter,
    _parse_lifecycle_env,
)


def _make_record(name: str, level: int, msg: str = "test") -> logging.LogRecord:
    return logging.LogRecord(
        name=name,
        level=level,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=None,
        exc_info=None,
    )


# ─── Filter behaviour matrix ─────────────────────────────────────────────────


def test_lifecycle_logger_info_passes_when_listed():
    f = _LifecycleConsoleFilter(["strategies.morning_range_reversion_strategy"], logging.WARNING)
    rec = _make_record("strategies.morning_range_reversion_strategy", logging.INFO, "📐 anchor range built")
    assert f.filter(rec) is True


def test_lifecycle_logger_warning_passes_when_listed():
    """WARNING from a lifecycle logger passes through ABOVE-INFO check."""
    f = _LifecycleConsoleFilter(["strategies.morning_range_reversion_strategy"], logging.WARNING)
    rec = _make_record("strategies.morning_range_reversion_strategy", logging.WARNING, "📏 too narrow")
    assert f.filter(rec) is True


def test_lifecycle_logger_debug_is_blocked_even_when_listed():
    """DEBUG < INFO — not a 'lifecycle' record. Filter must still reject it."""
    f = _LifecycleConsoleFilter(["strategies.morning_range_reversion_strategy"], logging.WARNING)
    rec = _make_record("strategies.morning_range_reversion_strategy", logging.DEBUG, "noisy")
    assert f.filter(rec) is False


def test_non_lifecycle_info_is_blocked():
    """The whole point: noisy modules don't get to push INFO to the terminal just
    because we relaxed the handler level."""
    f = _LifecycleConsoleFilter(["strategies.morning_range_reversion_strategy"], logging.WARNING)
    rec = _make_record("trading_bot", logging.INFO, "📊 last bar timestamp")
    assert f.filter(rec) is False


def test_non_lifecycle_warning_passes_via_default_threshold():
    """WARNING from anywhere should always reach the console — the operator
    needs to see those regardless of the lifecycle whitelist."""
    f = _LifecycleConsoleFilter(["strategies.morning_range_reversion_strategy"], logging.WARNING)
    rec = _make_record("brokers.topstepx_adapter", logging.WARNING, "⏳ HTTP 429")
    assert f.filter(rec) is True


def test_non_lifecycle_error_passes():
    f = _LifecycleConsoleFilter(["strategies.morning_range_reversion_strategy"], logging.WARNING)
    rec = _make_record("core.auth", logging.ERROR, "Request failed")
    assert f.filter(rec) is True


def test_child_logger_inherits_lifecycle_status():
    """``strategies.foo.bar`` must inherit when ``strategies.foo`` is whitelisted.
    Many strategies use ``logger = getLogger(__name__)`` which yields module-dotted
    children for helpers."""
    f = _LifecycleConsoleFilter(["strategies.morning_range_reversion_strategy"], logging.WARNING)
    rec = _make_record(
        "strategies.morning_range_reversion_strategy.helpers",
        logging.INFO,
        "child INFO",
    )
    assert f.filter(rec) is True


def test_prefix_only_matches_dotted_boundary():
    """A logger ``strategies.morning_range_reversion_strategy_v2`` must NOT match
    ``strategies.morning_range_reversion_strategy`` — the boundary is a dot.
    Otherwise renaming/versioning a strategy could accidentally inherit lifecycle
    visibility."""
    f = _LifecycleConsoleFilter(["strategies.morning_range_reversion_strategy"], logging.WARNING)
    rec = _make_record(
        "strategies.morning_range_reversion_strategy_v2",
        logging.INFO,
        "different logger",
    )
    assert f.filter(rec) is False


def test_empty_lifecycle_list_falls_back_to_default_threshold():
    """When no lifecycle loggers are configured, the filter degrades to the
    default WARNING gate — same behaviour as no filter at all."""
    f = _LifecycleConsoleFilter([], logging.WARNING)
    rec_info = _make_record("trading_bot", logging.INFO)
    rec_warn = _make_record("trading_bot", logging.WARNING)
    rec_err = _make_record("trading_bot", logging.ERROR)
    assert f.filter(rec_info) is False
    assert f.filter(rec_warn) is True
    assert f.filter(rec_err) is True


def test_multiple_lifecycle_loggers_all_match():
    """Multi-strategy setups (master executor) need multiple loggers in the
    allow-list. All listed loggers must independently pass."""
    f = _LifecycleConsoleFilter(
        [
            "strategies.morning_range_reversion_strategy",
            "strategies.overnight_range_strategy",
        ],
        logging.WARNING,
    )
    a = _make_record("strategies.morning_range_reversion_strategy", logging.INFO)
    b = _make_record("strategies.overnight_range_strategy", logging.INFO)
    c = _make_record("strategies.mean_reversion_strategy", logging.INFO)  # not listed
    assert f.filter(a) is True
    assert f.filter(b) is True
    assert f.filter(c) is False


# ─── LIFECYCLE_LOGGERS env-var parsing ───────────────────────────────────────


def test_parse_lifecycle_env_returns_empty_when_unset(monkeypatch):
    monkeypatch.delenv("LIFECYCLE_LOGGERS", raising=False)
    assert _parse_lifecycle_env() == []


def test_parse_lifecycle_env_handles_empty_string(monkeypatch):
    monkeypatch.setenv("LIFECYCLE_LOGGERS", "")
    assert _parse_lifecycle_env() == []


def test_parse_lifecycle_env_handles_single_logger(monkeypatch):
    monkeypatch.setenv("LIFECYCLE_LOGGERS", "strategies.morning_range_reversion_strategy")
    assert _parse_lifecycle_env() == ["strategies.morning_range_reversion_strategy"]


def test_parse_lifecycle_env_handles_csv_with_whitespace(monkeypatch):
    monkeypatch.setenv(
        "LIFECYCLE_LOGGERS",
        " strategies.foo_strategy , strategies.bar_strategy ,  ",
    )
    assert _parse_lifecycle_env() == [
        "strategies.foo_strategy",
        "strategies.bar_strategy",
    ]


# ─── _LifecycleConsoleFormatter (the 2026-05-29 "pretty terminal" formatter) ─


def _full_fmt() -> _logging.Formatter:
    return _logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")


def test_lifecycle_info_renders_as_bare_message():
    """Lifecycle INFO must print JUST the message — no timestamp, no logger name,
    no level prefix. That's the whole point of the formatter: ``📐 anchor range
    built ...`` lines should align cleanly across symbols in the terminal."""
    f = _LifecycleConsoleFormatter(["strategies.morning_range_reversion_strategy"], _full_fmt())
    rec = _make_record(
        "strategies.morning_range_reversion_strategy",
        _logging.INFO,
        "📐 MNQ anchor range built for 2026-05-29:  H= 30372.25  L= 30299.00",
    )
    out = f.format(rec)
    assert out == "📐 MNQ anchor range built for 2026-05-29:  H= 30372.25  L= 30299.00"
    # Critical: no timestamp leak (would break column alignment in terminal).
    assert " - " not in out, f"bare message must not contain ' - ' separator: {out!r}"


def test_lifecycle_warning_keeps_full_prefix():
    """WARNING from a lifecycle logger must keep the full prefix — timestamp and
    level matter for diagnostics. The bare-message path is INFO-only."""
    f = _LifecycleConsoleFormatter(["strategies.morning_range_reversion_strategy"], _full_fmt())
    rec = _make_record(
        "strategies.morning_range_reversion_strategy",
        _logging.WARNING,
        "📏 anchor range too narrow",
    )
    out = f.format(rec)
    # Full format ALWAYS includes the level name and " - " separators.
    assert "WARNING" in out
    assert " - strategies.morning_range_reversion_strategy - " in out


def test_lifecycle_error_keeps_full_prefix():
    f = _LifecycleConsoleFormatter(["strategies.morning_range_reversion_strategy"], _full_fmt())
    rec = _make_record(
        "strategies.morning_range_reversion_strategy",
        _logging.ERROR,
        "⛔ STALE DATA for MNQ",
    )
    out = f.format(rec)
    assert "ERROR" in out
    assert "strategies.morning_range_reversion_strategy" in out


def test_non_lifecycle_info_uses_full_prefix():
    """An INFO from a NON-lifecycle logger — even if it somehow leaks through
    the upstream filter — must still print with full format, not bare. This
    way a careful operator can still spot "wait, why is trading_bot INFO showing
    on the terminal?" via the prefix instead of seeing an unprefixed line
    posing as lifecycle output."""
    f = _LifecycleConsoleFormatter(["strategies.morning_range_reversion_strategy"], _full_fmt())
    rec = _make_record("trading_bot", _logging.INFO, "📊 last bar timestamp")
    out = f.format(rec)
    assert "INFO" in out
    assert "trading_bot" in out


def test_no_lifecycle_names_falls_back_to_full_format():
    """Defensive: an empty lifecycle list shouldn't bare-print ANYTHING."""
    f = _LifecycleConsoleFormatter([], _full_fmt())
    rec = _make_record(
        "strategies.morning_range_reversion_strategy",
        _logging.INFO,
        "📐 MNQ anchor range built",
    )
    out = f.format(rec)
    assert "INFO" in out, "with no whitelist, every record uses the full format"


def test_lifecycle_child_logger_info_renders_bare():
    """Children of lifecycle loggers (``foo_strategy.helpers``) also bare-print
    INFO so multi-module strategies look uniform in the terminal."""
    f = _LifecycleConsoleFormatter(["strategies.morning_range_reversion_strategy"], _full_fmt())
    rec = _make_record(
        "strategies.morning_range_reversion_strategy.helpers",
        _logging.INFO,
        "helper INFO from sub-module",
    )
    out = f.format(rec)
    assert out == "helper INFO from sub-module"
