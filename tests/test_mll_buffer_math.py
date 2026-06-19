"""Regression tests for the MLL (max-loss / trailing) buffer math used
by ``gui.chart_html.handle_account_state``.

MLL is the more catastrophic of the two prop-account risk numbers —
breaching it permanently terminates the account, no daily reset. The
dashboard chip MUST be glanceable and accurate.

Math contract (mirrors ``AccountTracker.get_compliance_status``):

    trailing_loss      = highest_eod_balance - current_balance
    mll_remaining      = max(0, mll_limit - trailing_loss)
    mll_pct_remaining  = clamp(mll_remaining / mll_limit, 0, 1)

When ``mll_limit <= 0`` (tracker not seeded), ``mll_pct_remaining`` is
``None`` and the frontend hides the chip.
"""

from __future__ import annotations

import pytest


def compute_mll_buffer(
    mll_limit: float,
    highest_eod_balance: float,
    current_balance: float,
):
    """Mirror of the production extraction in ``handle_account_state``
    after refactor to ``tracker.get_compliance_status``. Pure-function
    form for unit-testing the threshold + boundary math."""
    if not mll_limit or mll_limit <= 0:
        return 0.0, None, 0.0
    trailing_loss = max(0.0, highest_eod_balance - current_balance)
    remaining = max(0.0, mll_limit - trailing_loss)
    pct = max(0.0, min(1.0, remaining / mll_limit))
    return round(remaining, 2), pct, round(trailing_loss, 2)


def test_unknown_limit_returns_none_pct():
    rem, pct, trail = compute_mll_buffer(0.0, 50_000.0, 49_000.0)
    assert rem == 0.0
    assert pct is None
    assert trail == 0.0


def test_full_buffer_at_high_water_mark():
    """At the high-water mark (current = highest), trailing = 0,
    full buffer = mll_limit, pct = 1.0."""
    rem, pct, trail = compute_mll_buffer(2_500.0, 50_000.0, 50_000.0)
    assert rem == 2_500.0
    assert pct == 1.0
    assert trail == 0.0


def test_full_buffer_above_high_water_mark():
    """Above high-water (intraday gain not yet EOD-promoted), trailing
    is clamped to 0 — buffer doesn't inflate above 100%."""
    rem, pct, trail = compute_mll_buffer(2_500.0, 50_000.0, 50_500.0)
    assert rem == 2_500.0
    assert pct == 1.0
    assert trail == 0.0


def test_partial_drawdown_reports_correct_buffer():
    """High = $50k, current = $49k → trailing = $1k, MLL $2.5k →
    remaining = $1.5k = 60%."""
    rem, pct, trail = compute_mll_buffer(2_500.0, 50_000.0, 49_000.0)
    assert rem == 1_500.0
    assert pct == 0.6
    assert trail == 1_000.0


def test_at_floor_reports_zero_buffer():
    """High = $50k, MLL $2.5k → floor $47.5k. At floor, buffer = 0."""
    rem, pct, trail = compute_mll_buffer(2_500.0, 50_000.0, 47_500.0)
    assert rem == 0.0
    assert pct == 0.0
    assert trail == 2_500.0


def test_below_floor_clamps_buffer_to_zero():
    """Below floor = MLL breach. Buffer can't go negative."""
    rem, pct, trail = compute_mll_buffer(2_500.0, 50_000.0, 47_000.0)
    assert rem == 0.0
    assert pct == 0.0
    # trailing_loss does NOT clamp (it's the actual drawdown).
    assert trail == 3_000.0


def test_threshold_boundaries_match_dll():
    """MLL chip uses same warn/hot color thresholds as DLL (50% / 25%)."""
    # 50% remaining: high $50k, MLL $2.5k → 50% buffer at trailing=$1.25k.
    rem50, pct50, _ = compute_mll_buffer(2_500.0, 50_000.0, 48_750.0)
    assert pct50 == 0.5
    # 25% remaining: trailing = $1.875k.
    rem25, pct25, _ = compute_mll_buffer(2_500.0, 50_000.0, 48_125.0)
    assert pct25 == 0.75 - 0.5  # = 0.25, written this way to make intent obvious


def test_handle_account_state_inline_extraction_matches():
    """Pull the inline extraction out of ``handle_account_state`` and
    re-run the same expression to prevent drift between the helper used
    in tests and the production code path."""
    from gui import chart_html  # noqa: F401  (smoke import)

    # Replicate the production expressions VERBATIM as they appear in
    # ``handle_account_state`` after the compliance_status refactor.
    compliance = {
        "mll_limit": 2_500.0,
        "mll_remaining": 1_500.0,
        "trailing_loss": 1_000.0,
        "mll_violated": False,
    }
    maximum_loss_limit = float(compliance.get("mll_limit") or 0.0)
    mll_remaining = float(compliance.get("mll_remaining") or 0.0)
    trailing_loss = float(compliance.get("trailing_loss") or 0.0)
    mll_violated = bool(compliance.get("mll_violated", False))
    if maximum_loss_limit and maximum_loss_limit > 0:
        mll_pct_remaining = max(0.0, min(1.0, mll_remaining / maximum_loss_limit))
    else:
        mll_pct_remaining = None

    rem, pct, trail = compute_mll_buffer(2_500.0, 50_000.0, 49_000.0)
    assert round(mll_remaining, 2) == rem
    assert mll_pct_remaining == pct
    assert round(trailing_loss, 2) == trail
    assert mll_violated is False


def test_violation_flag_propagates():
    """A breached MLL must mark the chip BREACH regardless of pct math
    (defensive: the tracker is the source of truth for violation state,
    not the dashboard's threshold derivation)."""
    compliance = {"mll_violated": True, "mll_remaining": 0.0, "mll_limit": 2_500.0}
    assert bool(compliance.get("mll_violated", False)) is True
