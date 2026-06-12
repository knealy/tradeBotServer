"""Pinning tests for ``cap_quantity_by_dollar_risk`` in core.risk_sizer.

The per-trade $ loss cap is the equity-curve smoother that turns a 2-ct
26.70-pt MGC SL ($534) into a 1-ct trade ($267) automatically.  These
tests pin the math + edge cases so future TOML tweaks don't silently
break the cap.

MGC point value = $10/pt.  MNQ = $2/pt.  MES = $5/pt.
"""

from __future__ import annotations

import pytest

from core.risk_sizer import (
    cap_quantity_by_dollar_risk,
    dollar_risk_at,
    point_value,
)


# ───────────────── disabled / no-op paths ───────────────────────────


def test_cap_disabled_returns_requested_qty():
    qty, reason = cap_quantity_by_dollar_risk(
        symbol="MGC", entry_price=4100.0, stop_loss_price=4073.4,
        requested_quantity=2, max_dollar_risk=0.0,
    )
    assert qty == 2
    assert "disabled" in reason.lower()


def test_cap_negative_treated_as_disabled():
    qty, reason = cap_quantity_by_dollar_risk(
        symbol="MGC", entry_price=4100.0, stop_loss_price=4073.4,
        requested_quantity=2, max_dollar_risk=-100,
    )
    assert qty == 2


def test_cap_within_budget_returns_requested_qty():
    # 26.70 pt × $10/pt × 2 ct = $534 ; cap $1000 → no trim.
    qty, reason = cap_quantity_by_dollar_risk(
        symbol="MGC", entry_price=4100.0, stop_loss_price=4073.4,
        requested_quantity=2, max_dollar_risk=1000.0,
    )
    assert qty == 2
    assert "within cap" in reason.lower()


# ───────────────── the 2026-06-11 MGC scenario ─────────────────────


def test_mgc_20260611_scenario_caps_to_one_contract_at_300():
    """The actual trade: LONG 2× MGC @ 4100.10, SL @ 4073.40 → $534 risk.
    With a $300 cap, the cap should reduce to 1 ct ($267 ≤ $300)."""
    qty, reason = cap_quantity_by_dollar_risk(
        symbol="MGC", entry_price=4100.10, stop_loss_price=4073.40,
        requested_quantity=2, max_dollar_risk=300.0,
    )
    assert qty == 1
    assert "capped" in reason.lower()
    # Verify the math the cap used:
    assert dollar_risk_at("MGC", stop_points=26.70, quantity=1) == pytest.approx(267.0)


def test_mgc_20260611_scenario_at_400_still_caps_to_one():
    # $400 cap, 1 ct = $267, 2 ct = $534 → cap allows only 1.
    qty, _ = cap_quantity_by_dollar_risk(
        symbol="MGC", entry_price=4100.10, stop_loss_price=4073.40,
        requested_quantity=2, max_dollar_risk=400.0,
    )
    assert qty == 1


def test_mgc_20260611_scenario_at_600_allows_two():
    qty, _ = cap_quantity_by_dollar_risk(
        symbol="MGC", entry_price=4100.10, stop_loss_price=4073.40,
        requested_quantity=2, max_dollar_risk=600.0,
    )
    assert qty == 2


# ───────────────── micro-futures sweep ──────────────────────────────


@pytest.mark.parametrize("symbol,stop_pts,req_qty,cap,expected_qty", [
    # MNQ ($2/pt): 10pt × $2 × 5 ct = $100; cap $50 → 2 ct ($40).
    ("MNQ", 10.0, 5, 50.0, 2),
    # MES ($5/pt): 8pt × $5 × 3 ct = $120; cap $80 → 2 ct ($80).
    ("MES", 8.0, 3, 80.0, 2),
    # MGC ($10/pt): 15pt × $10 × 4 ct = $600; cap $200 → 1 ct ($150).
    ("MGC", 15.0, 4, 200.0, 1),
    # No trim needed: 5pt × $2 × 1 ct = $10 ≤ $100 cap.
    ("MNQ", 5.0, 1, 100.0, 1),
])
def test_cap_math_across_symbols(symbol, stop_pts, req_qty, cap, expected_qty):
    qty, _ = cap_quantity_by_dollar_risk(
        symbol=symbol,
        entry_price=10000.0,
        stop_loss_price=10000.0 - stop_pts,
        requested_quantity=req_qty,
        max_dollar_risk=cap,
    )
    assert qty == expected_qty


# ───────────────── strict vs warn-min-qty behaviour ─────────────────


def test_min_qty_over_risk_strict_returns_zero():
    # 100pt × $10/pt × 1 ct = $1000 ; cap $50 ; min_quantity=1 still $1000.
    qty, reason = cap_quantity_by_dollar_risk(
        symbol="MGC", entry_price=4100.0, stop_loss_price=4000.0,
        requested_quantity=2, max_dollar_risk=50.0, strict=True,
    )
    assert qty == 0
    assert "REFUSED" in reason


def test_min_qty_over_risk_warn_overrides_to_min_qty():
    qty, reason = cap_quantity_by_dollar_risk(
        symbol="MGC", entry_price=4100.0, stop_loss_price=4000.0,
        requested_quantity=2, max_dollar_risk=50.0, strict=False,
    )
    assert qty == 1
    assert "overriding" in reason.lower()


# ───────────────── defensive / malformed inputs ─────────────────────


def test_zero_stop_distance_pass_through():
    qty, reason = cap_quantity_by_dollar_risk(
        symbol="MGC", entry_price=4100.0, stop_loss_price=4100.0,
        requested_quantity=2, max_dollar_risk=100.0,
    )
    assert qty == 2
    assert "stop distance ≤ 0" in reason


def test_negative_quantity_returns_zero():
    qty, _ = cap_quantity_by_dollar_risk(
        symbol="MGC", entry_price=4100.0, stop_loss_price=4070.0,
        requested_quantity=0, max_dollar_risk=100.0,
    )
    assert qty == 0


def test_string_quantity_handled():
    qty, _ = cap_quantity_by_dollar_risk(
        symbol="MGC", entry_price=4100.0, stop_loss_price=4070.0,
        requested_quantity="not a number",  # type: ignore[arg-type]
        max_dollar_risk=100.0,
    )
    assert qty == 0


def test_unknown_symbol_falls_back_to_default_pv():
    # Unknown symbol falls back to $2/pt (matches existing point_value behaviour).
    pv = point_value("XYZ123")
    assert pv == 2.0
    # 50pt × $2 × 2 ct = $200; cap $100 → 1 ct ($100).
    qty, _ = cap_quantity_by_dollar_risk(
        symbol="XYZ123", entry_price=100.0, stop_loss_price=50.0,
        requested_quantity=2, max_dollar_risk=100.0,
    )
    assert qty == 1
