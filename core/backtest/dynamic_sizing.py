"""Walk-forward dynamic position sizing from drawdown vs a prop-style base.

Enabled when ``BACKTEST_DYNAMIC_SIZING=1`` (or walkforward ``--dynamic-sizing``).

Reference notional defaults to **$2,000** (``BACKTEST_DYNAMIC_SIZING_BASE`` /
walk-forward ``--sim-start-cash``). Equity for sizing is **base + Σ trade PnL**
(with open-position unrealized), **not** the replay engine's ``initial_capital``
(often $50k).

Rules (drawdown from running equity peak, steps as % of reference base):
  - Every 5% drawdown from peak → −1 contract vs base (floor 1)
  - When at peak (zero drawdown): +1 per +10% equity above reference base
  - Hard ceiling ``BACKTEST_DYNAMIC_SIZING_MAX`` (default 15 contracts)
"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional


def dynamic_sizing_enabled() -> bool:
    return os.environ.get("BACKTEST_DYNAMIC_SIZING", "0").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def reference_equity(default: float = 2000.0) -> float:
    """Prop-style account base for drawdown/gain step math (not replay engine capital)."""
    raw = os.environ.get("BACKTEST_DYNAMIC_SIZING_BASE", "").strip()
    if not raw:
        return float(default)
    try:
        v = float(raw)
        return v if v > 0 else float(default)
    except (TypeError, ValueError):
        return float(default)


def max_qty(default: int = 15) -> int:
    """Upper bound on dynamically sized contract count (prop risk cap)."""
    raw = os.environ.get("BACKTEST_DYNAMIC_SIZING_MAX", "").strip()
    if not raw:
        return max(1, int(default))
    try:
        v = int(float(raw))
        return v if v >= 1 else max(1, int(default))
    except (TypeError, ValueError):
        return max(1, int(default))


def initial_carry(base: Optional[float] = None) -> Dict[str, float]:
    """Starting prop-account state for the first fold in a walk-forward chain."""
    ref = float(base) if base is not None else reference_equity()
    return {"equity": ref, "peak": ref}


def carry_from_mapping(raw: Optional[Dict[str, Any]]) -> Dict[str, float]:
    """Normalize carry dict from walk-forward / cache payloads."""
    ref = reference_equity()
    if not raw:
        return initial_carry(ref)
    try:
        eq = float(raw.get("equity", ref))
        peak = float(raw.get("peak", eq))
    except (TypeError, ValueError):
        return initial_carry(ref)
    return {"equity": eq, "peak": max(peak, eq)}


def end_carry(current_equity: float, peak_equity: float) -> Dict[str, float]:
    eq = float(current_equity)
    peak = max(float(peak_equity), eq)
    return {"equity": eq, "peak": peak}


def apply_dynamic_sizing(
    base_qty: int,
    current_equity: float,
    peak_equity: float,
    reference_base: float,
    *,
    gain_step_pct: float = 0.10,
    drawdown_step_pct: float = 0.05,
    qty_ceiling: Optional[int] = None,
) -> int:
    """Return adjusted contract count from drawdown (and peak-only gain bonus)."""
    ceiling = max_qty() if qty_ceiling is None else max(1, int(qty_ceiling))
    try:
        base = max(1, int(base_qty))
    except (TypeError, ValueError):
        base = 1
    try:
        ref = float(reference_base)
        current = float(current_equity)
        peak = float(peak_equity)
    except (TypeError, ValueError):
        return base
    if ref <= 0:
        return base

    peak = max(peak, current, ref)
    drawdown = max(0.0, peak - current)
    step_dollars = ref * drawdown_step_pct
    penalty = int(drawdown // step_dollars) if step_dollars > 0 and drawdown > 0 else 0

    bonus = 0
    if drawdown <= 1e-9 and current > ref:
        gain_dollars = current - ref
        gain_step_dollars = ref * gain_step_pct
        bonus = int(gain_dollars // gain_step_dollars) if gain_step_dollars > 0 else 0

    return min(ceiling, max(1, base + bonus - penalty))
