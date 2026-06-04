"""Tests for the fixed-dollar-risk position sizer (`core/risk_sizer.py`)."""

from __future__ import annotations

import pytest

from core.risk_sizer import (
    DEFAULT_MAX_CONTRACTS,
    _strip_month_code,
    dollar_risk_at,
    point_value,
    size_for,
    tick_size,
)


# ----------------------------- pure-table tests -----------------------------


def test_point_value_known_symbols():
    """Sanity-check the per-symbol $/pt table matches the canonical
    `core.risk_management.RiskManager.POINT_VALUES`."""
    assert point_value("MNQ") == 2.0
    assert point_value("MES") == 5.0
    assert point_value("MGC") == 10.0
    assert point_value("NQ") == 20.0
    assert point_value("ES") == 50.0
    assert point_value("GC") == 100.0


def test_point_value_strips_month_code():
    """Quarterly futures contracts have trailing month/year codes; the sizer
    must strip them before lookup or every live order would silently fall
    through to the 2.0 default."""
    assert point_value("MNQZ25") == 2.0
    assert point_value("MESH26") == 5.0
    assert point_value("MGCM26") == 10.0


def test_point_value_unknown_falls_back():
    assert point_value("ZZZ") == 2.0  # safe default; not silently broken


def test_tick_size_basics():
    assert tick_size("MNQ") == 0.25
    assert tick_size("MES") == 0.25
    assert tick_size("MGC") == 0.10
    assert tick_size("MGCM26") == 0.10


def test_strip_month_code():
    assert _strip_month_code("MNQ") == "MNQ"
    assert _strip_month_code("MNQZ25") == "MNQ"
    assert _strip_month_code("MES") == "MES"
    assert _strip_month_code("mes") == "MES"


# ----------------------------- size_for tests -----------------------------


def test_size_for_basic_mnq():
    """$200 budget / 10-pt MNQ stop = 200 / (10 * 2) = 10 contracts."""
    assert size_for("MNQ", dollar_risk=200.0, stop_points=10.0) == 10


def test_size_for_basic_mes():
    """$200 / (5-pt × $5) = 8 contracts (caps at MES default 8)."""
    assert size_for("MES", dollar_risk=200.0, stop_points=5.0) == 8


def test_size_for_basic_mgc():
    """$200 / (4-pt × $10) = 5 contracts (caps at MGC default 5)."""
    assert size_for("MGC", dollar_risk=200.0, stop_points=4.0) == 5


def test_size_for_floor_truncation():
    """200 / (7 × 2) = 14.285… → 14, not 15.  Floor matters: a sizer that
    rounded UP would breach the operator's dollar-risk cap silently.
    Pass an explicit ``max_contracts`` so the default cap (10) doesn't kick
    in before the floor check."""
    assert size_for("MNQ", dollar_risk=200.0, stop_points=7.0, max_contracts=100) == 14


def test_size_for_clamps_to_max_contracts():
    """$10,000 dollar risk on a 5-pt MNQ stop is 1000 contracts raw — must
    clamp to MNQ's DEFAULT_MAX_CONTRACTS (10)."""
    assert size_for("MNQ", dollar_risk=10_000.0, stop_points=5.0) == DEFAULT_MAX_CONTRACTS["MNQ"]


def test_size_for_override_max_contracts():
    """Caller can pass a tighter cap (e.g. strategy TOML pin)."""
    assert size_for("MNQ", dollar_risk=10_000.0, stop_points=5.0, max_contracts=3) == 3


def test_size_for_zero_inputs_return_zero():
    assert size_for("MNQ", dollar_risk=0.0, stop_points=10.0) == 0
    assert size_for("MNQ", dollar_risk=-100.0, stop_points=10.0) == 0
    assert size_for("MNQ", dollar_risk=100.0, stop_points=0.0) == 0
    assert size_for("MNQ", dollar_risk=100.0, stop_points=-5.0) == 0


def test_size_for_below_one_contract_returns_zero():
    """Risk budget too small for even one contract → 0, not negative or 1."""
    assert size_for("MNQ", dollar_risk=10.0, stop_points=100.0) == 0  # 10 / 200 = 0.05


# ----------------------------- inverse helper -----------------------------


def test_dollar_risk_at_round_trip():
    """size_for(...) → dollar_risk_at(...) should reconstruct the exposure
    actually being taken (≤ the requested budget by construction of floor)."""
    sym = "MNQ"
    budget = 200.0
    stop = 7.0
    qty = size_for(sym, dollar_risk=budget, stop_points=stop)
    exposure = dollar_risk_at(sym, stop_points=stop, quantity=qty)
    assert exposure <= budget + 1e-6  # NEVER exceed the budget
    # And it should be the natural exposure: qty * stop * $/pt
    assert exposure == pytest.approx(qty * stop * point_value(sym))


def test_dollar_risk_at_degenerate_inputs():
    assert dollar_risk_at("MNQ", stop_points=0.0, quantity=1) == 0.0
    assert dollar_risk_at("MNQ", stop_points=5.0, quantity=0) == 0.0


# ----------------------------- worst-case-day arithmetic ----------------------


def test_worst_case_day_budget_fits_under_1000():
    """The arsenal portfolio worst-case-day budget needs ≤ $1000.  Verify the
    sizer's defaults (taken from STRATEGY_ARSENAL.md's portfolio sizing) don't
    individually exceed sane per-trade caps that would breach the budget."""
    # Use sane stop_points consistent with each strategy's typical config.
    cases = [
        ("MNQ", 300.0, 10.0),   # ~$ cap morning_range MNQ slice
        ("MES",  50.0, 5.0),    # tight body_reversion MES
        ("MGC", 220.0, 5.0),    # MGC cap from worst-case-day arithmetic
    ]
    total = 0.0
    for sym, budget, stop in cases:
        qty = size_for(sym, dollar_risk=budget, stop_points=stop)
        total += dollar_risk_at(sym, stop_points=stop, quantity=qty)
    # All four legs together should not breach the operator's $1000 ceiling.
    assert total <= 1000.0, f"per-trade aggregate {total:.2f} > $1000 cap"
