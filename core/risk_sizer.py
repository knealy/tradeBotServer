"""Fixed-dollar-risk position sizer.

The arsenal-roadmap Phase-1 prerequisite for every new strategy: turns
"I want to risk $X with a Y-point stop" into a contract count, clamped by
per-symbol ``max_contracts``.  Replaces the per-strategy hard-coded
``position_size = N`` knob that's currently scattered across TOMLs.

Design notes
------------
- Single source of truth for ``$/point`` is ``core.risk_management.RiskManager``
  (the only existing lookup table that includes MGC at $10/pt).  We delegate
  to it via :func:`point_value` rather than copying the dict.
- ``max_contracts`` defaults are intentionally conservative (~5-10 micros, 2-3
  full-size).  Caller can override per call (e.g. when the strategy TOML
  pins a tighter cap).
- Returns ``int(floor(...))`` so a $200 budget with a 7-pt MNQ stop becomes
  14 contracts (200 / (7 × 2) = 14.28 → 14), not 15.
- A ``stop_points`` of 0 or negative returns ``0`` — defensive against
  malformed strategy outputs rather than raising; the strategy layer should
  not place an order with no stop in the first place.
- Trading-bot integration: strategies pass ``trading_bot.account_tracker``
  values into the dollar-risk budget calculation upstream (the sizer itself
  is pure — no live-state coupling).
"""

from __future__ import annotations

import math
from typing import Optional

# Conservative defaults — caller can override.  Numbers chosen so that the
# worst-case-day arithmetic stays bounded even at maximum risk per strategy:
#   MNQ:   10 ct × ($2 × stop) = max ~$200 at typical 10pt stop
#   MES:    8 ct × ($5 × stop) = max ~$200 at typical 5pt stop
#   MGC:    5 ct × ($10 × stop) = max ~$200 at typical 4pt stop
DEFAULT_MAX_CONTRACTS = {
    "MNQ": 10, "MES": 8,  "MGC": 5,
    "MYM": 10, "M2K": 10,
    "NQ":  3,  "ES":  3,  "GC":  2,
    "YM":  3,  "RTY": 3,
    "CL":  2,  "MCL": 5,
}


_MONTH_CODE_RE = None


def _strip_month_code(symbol: str) -> str:
    """``MNQZ25`` → ``MNQ``.  Strips exactly one month code letter
    (CME futures months ``F G H J K M N Q U V X Z``) followed by 1-2
    digits at the END of the symbol.  Naive char-by-char stripping
    breaks on symbols like ``MNQ`` where ``N`` itself is a month code.
    """
    import re
    global _MONTH_CODE_RE
    if _MONTH_CODE_RE is None:
        _MONTH_CODE_RE = re.compile(r"^(.+?)([FGHJKMNQUVXZ]\d{1,2})$")
    s = str(symbol).upper().strip()
    m = _MONTH_CODE_RE.match(s)
    return m.group(1) if m else s


def point_value(symbol: str) -> float:
    """Return $/point for ``symbol``.  Falls back to 2.0 for unknown symbols
    (matches :py:meth:`core.risk_management.RiskManager.get_point_value`)."""
    from core.risk_management import RiskManager
    # Use the class-level dict directly; RiskManager() has a heavy ctor.
    base = _strip_month_code(symbol)
    return float(RiskManager.POINT_VALUES.get(base, 2.0))


def tick_size(symbol: str) -> float:
    """Return tick size for ``symbol`` (smallest price increment)."""
    from core.risk_management import RiskManager
    base = _strip_month_code(symbol)
    return float(RiskManager.TICK_SIZES.get(base, 0.25))


def size_for(
    symbol: str,
    *,
    dollar_risk: float,
    stop_points: float,
    max_contracts: Optional[int] = None,
) -> int:
    """Compute contract count for a fixed-dollar-risk trade.

    ``contracts = floor( dollar_risk / (stop_points × $/pt) )``,
    clamped to ``[0, max_contracts]``.

    Args:
        symbol: Futures symbol (e.g. ``"MNQ"`` or ``"MNQZ25"``).
        dollar_risk: Maximum $ risk you're willing to take if the stop fills.
            Typically derived from per-strategy budget × current equity.
        stop_points: Distance from entry to stop in price points.
        max_contracts: Optional override of the per-symbol cap.  Defaults to
            :data:`DEFAULT_MAX_CONTRACTS`.

    Returns:
        Integer contract count.  ``0`` when inputs are non-positive or when
        the rounded contract count is below 1.
    """
    if dollar_risk <= 0 or stop_points <= 0:
        return 0
    pv = point_value(symbol)
    if pv <= 0:
        return 0
    raw = dollar_risk / (stop_points * pv)
    if max_contracts is None:
        max_contracts = DEFAULT_MAX_CONTRACTS.get(_strip_month_code(symbol), 5)
    qty = int(math.floor(raw))
    return max(0, min(int(max_contracts), qty))


def dollar_risk_at(symbol: str, *, stop_points: float, quantity: int) -> float:
    """Inverse helper: actual $ exposure for a given (symbol, stop_pts, qty)."""
    if stop_points <= 0 or quantity <= 0:
        return 0.0
    return float(stop_points) * point_value(symbol) * int(quantity)
