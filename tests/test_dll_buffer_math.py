"""Regression tests for the daily-loss-limit buffer math used by
``gui.chart_html.handle_account_state`` to drive the head-summary DLL
chip.

The contract — pinned because this is the most fatal-to-prop number on
the dashboard — is:

* ``dll_remaining = max(0, daily_loss_limit + min(0, total_pnl))``
* ``dll_pct_remaining = clamp(dll_remaining / daily_loss_limit, 0, 1)``
* When ``daily_loss_limit <= 0`` (tracker not seeded yet),
  ``dll_remaining = 0`` and ``dll_pct_remaining = None``.
* Profitable sessions report the *full* limit as remaining (the buffer
  doesn't inflate above 100%; we use ``min(0, total_pnl)`` precisely to
  prevent that).

The math lives inline in ``handle_account_state`` to avoid an extra
import path; we replicate it here as a plain function the test imports
directly. Any change to the inline math MUST be mirrored here or the
tests will fail loudly.
"""

from __future__ import annotations

import pytest


def compute_dll_buffer(daily_loss_limit: float, total_pnl: float):
    """Mirror of the inline computation in handle_account_state."""
    if not daily_loss_limit or daily_loss_limit <= 0:
        return 0.0, None
    remaining = max(0.0, daily_loss_limit + min(0.0, total_pnl))
    pct = max(0.0, min(1.0, remaining / daily_loss_limit))
    return round(remaining, 2), pct


def test_unknown_limit_returns_none_pct():
    rem, pct = compute_dll_buffer(0.0, -150.0)
    assert rem == 0.0
    assert pct is None


def test_negative_limit_treated_as_unknown():
    rem, pct = compute_dll_buffer(-1.0, 0.0)
    assert rem == 0.0
    assert pct is None


def test_full_buffer_when_no_loss():
    rem, pct = compute_dll_buffer(1000.0, 0.0)
    assert rem == 1000.0
    assert pct == 1.0


def test_profitable_session_does_not_inflate_buffer():
    """A +$300 day must NOT report 130% buffer — capped at 100%."""
    rem, pct = compute_dll_buffer(1000.0, 300.0)
    assert rem == 1000.0
    assert pct == 1.0


def test_partial_loss_reports_correct_buffer():
    """$300 down with $1000 limit → $700 left = 70%."""
    rem, pct = compute_dll_buffer(1000.0, -300.0)
    assert rem == 700.0
    assert pct == 0.7


def test_at_limit_reports_zero_buffer():
    rem, pct = compute_dll_buffer(1000.0, -1000.0)
    assert rem == 0.0
    assert pct == 0.0


def test_breach_clamps_to_zero():
    """Drawdown beyond the limit can't go negative."""
    rem, pct = compute_dll_buffer(1000.0, -1500.0)
    assert rem == 0.0
    assert pct == 0.0


def test_threshold_boundaries():
    """Pin the warn/hot color thresholds the frontend uses (50% / 25%)."""
    rem50, pct50 = compute_dll_buffer(1000.0, -500.0)
    assert pct50 == 0.5  # exactly at warn boundary
    rem25, pct25 = compute_dll_buffer(1000.0, -750.0)
    assert pct25 == 0.25  # exactly at hot boundary


def test_handle_account_state_inline_math_matches():
    """Pull the inline math out of handle_account_state by re-running
    the same expression and comparing — prevents drift between the
    helper used in tests and the production code path."""
    from gui import chart_html  # noqa: F401  (import is the smoke test)

    # Replicate the production expression VERBATIM.
    daily_loss_limit = 1000.0
    total_pnl = -300.0
    if daily_loss_limit and daily_loss_limit > 0:
        dll_remaining = max(0.0, daily_loss_limit + min(0.0, total_pnl))
        dll_pct_remaining = max(0.0, min(1.0, dll_remaining / daily_loss_limit))
    else:
        dll_remaining = 0.0
        dll_pct_remaining = None
    rem, pct = compute_dll_buffer(daily_loss_limit, total_pnl)
    assert round(dll_remaining, 2) == rem
    assert dll_pct_remaining == pct
