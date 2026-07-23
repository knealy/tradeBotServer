"""Calendar-era + regime-label position-size multipliers (prototype).

Derives guardrails from the 450-day walk-forward study
(``docs/perf/regime_longspan_450d/``):

- **MRR · MGC** was net negative before 2026-01-14; strong after.
- **MRR · MNQ** was weaker pre-2026 but less extreme than MGC.
- **OR** was flat/negative pre-2025-10-01; edge post-Oct-2025.
- **Regime at entry** (all strategies): chop + mixed profitable; trend rare.

Wiring is opt-in via ``REGIME_SIZING_ENABLED`` (env) — same pattern as
``core.income_brain``. Strategies do **not** import this module; call
:func:`regime_sizing_entry_quantity_for_bot` from ``trading_bot`` /
``strategy_base.place_bracket_order``.

When disabled, requested quantity passes through unchanged.
When multiplier resolves to ``0``, the entry is blocked (daily halt style).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return str(raw).strip().strip('"').strip("'").lower() in ("1", "true", "yes", "on")


def _load_tz(name: str = "America/New_York"):
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    except Exception:
        return timezone.utc


def regime_sizing_enabled() -> bool:
    return _env_flag("REGIME_SIZING_ENABLED", False)


@dataclass(frozen=True)
class EraRule:
    """Apply ``multiplier`` when session date (ET) is strictly before ``before``."""

    before: date
    multiplier: float


@dataclass(frozen=True)
class SymbolEraConfig:
    default_multiplier: float = 1.0
    eras: Tuple[EraRule, ...] = ()
    regime_multipliers: Tuple[Tuple[str, float], ...] = ()


# Committed defaults from regime_longspan_450d boundary splits.
_DEFAULT_CONFIGS: Dict[str, Dict[str, SymbolEraConfig]] = {
    "morning_range_reversion": {
        "MGC": SymbolEraConfig(
            default_multiplier=1.0,
            eras=(EraRule(date(2026, 1, 14), 0.5),),
            regime_multipliers=(("trend", 0.5), ("mixed", 0.9), ("chop", 1.0)),
        ),
        "MNQ": SymbolEraConfig(
            default_multiplier=1.0,
            eras=(EraRule(date(2026, 1, 1), 0.75),),
            regime_multipliers=(("trend", 0.5), ("mixed", 0.9), ("chop", 1.0)),
        ),
    },
    "overnight_range": {
        "MGC": SymbolEraConfig(
            default_multiplier=1.0,
            eras=(EraRule(date(2025, 10, 1), 0.0),),
            regime_multipliers=(("trend", 0.0), ("mixed", 1.0), ("chop", 1.0)),
        ),
        "MNQ": SymbolEraConfig(
            default_multiplier=1.0,
            eras=(EraRule(date(2025, 10, 1), 0.0),),
            regime_multipliers=(("trend", 0.0), ("mixed", 1.0), ("chop", 1.0)),
        ),
    },
}


def _parse_date_env(name: str) -> Optional[date]:
    raw = os.getenv(name)
    if not raw or not str(raw).strip():
        return None
    try:
        return date.fromisoformat(str(raw).strip())
    except ValueError:
        logger.warning("Invalid date for %s=%r — ignoring", name, raw)
        return None


def _era_multiplier(cfg: SymbolEraConfig, session_d: date) -> float:
    mult = cfg.default_multiplier
    for rule in cfg.eras:
        if session_d < rule.before:
            mult = min(mult, rule.multiplier)
    return mult


def _regime_multiplier(cfg: SymbolEraConfig, regime_label: Optional[str]) -> float:
    if not regime_label:
        return 1.0
    label = str(regime_label).strip().lower()
    for key, mult in cfg.regime_multipliers:
        if key == label:
            return mult
    return 1.0


def resolve_regime_sizing_multiplier(
    strategy_name: str,
    symbol: str,
    *,
    session_date: Optional[date] = None,
    regime_label: Optional[str] = None,
) -> Tuple[float, str]:
    """Return ``(multiplier, reason)`` for the given strategy/symbol/day."""
    if not regime_sizing_enabled():
        return 1.0, "disabled"

    strat = str(strategy_name or "").strip()
    sym = str(symbol or "").upper()
    per_strat = _DEFAULT_CONFIGS.get(strat, {})
    cfg = per_strat.get(sym)
    if cfg is None:
        return 1.0, "no rule"

    tz = _load_tz()
    session_d = session_date or datetime.now(timezone.utc).astimezone(tz).date()

    # Optional env overrides for operator experiments.
    override_before = _parse_date_env(f"REGIME_SIZING_{sym}_BEFORE")
    override_mult = os.getenv(f"REGIME_SIZING_{sym}_MULT")
    if override_before is not None and session_d < override_before:
        try:
            return float(override_mult or "0.5"), f"env override before {override_before}"
        except ValueError:
            pass

    era_mult = _era_multiplier(cfg, session_d)
    regime_mult = _regime_multiplier(cfg, regime_label)
    combined = era_mult * regime_mult

    parts = []
    if era_mult != 1.0:
        parts.append(f"era={era_mult:.2f}@{session_d.isoformat()}")
    if regime_mult != 1.0:
        parts.append(f"regime={regime_label}:{regime_mult:.2f}")
    reason = ", ".join(parts) if parts else "full size"
    return combined, reason


def apply_regime_sizing_quantity(
    strategy_name: str,
    symbol: str,
    requested: int,
    *,
    session_date: Optional[date] = None,
    regime_label: Optional[str] = None,
) -> Tuple[int, str]:
    """Clamp contract count by era/regime multiplier (min 0, round down)."""
    try:
        req = max(0, int(requested))
    except (TypeError, ValueError):
        return 0, "invalid requested qty"

    mult, reason = resolve_regime_sizing_multiplier(
        strategy_name,
        symbol,
        session_date=session_date,
        regime_label=regime_label,
    )
    if mult <= 0:
        return 0, f"blocked ({reason})"
    if mult >= 0.999:
        return req, reason

    adjusted = max(0, int(req * mult))
    if adjusted < 1 and req >= 1 and mult > 0:
        adjusted = 1
    return adjusted, f"{reason} → {req}×{mult:.2f}={adjusted}"


def _last_regime_label_from_bot(bot: Any) -> Optional[str]:
    label = getattr(bot, "_last_regime_label", None)
    if label:
        return str(label)
    pub = getattr(bot, "regime_publisher", None)
    if pub is not None:
        return getattr(pub, "_last_label", None)
    return None


def regime_sizing_entry_quantity_for_bot(
    bot: Any,
    strategy_name: str,
    symbol: str,
    requested: int,
) -> int:
    """Bot-facing helper used from ``strategy_base.place_bracket_order``."""
    if not regime_sizing_enabled():
        try:
            return max(0, int(requested))
        except (TypeError, ValueError):
            return 0

    regime_label = _last_regime_label_from_bot(bot)
    qty, reason = apply_regime_sizing_quantity(
        strategy_name,
        symbol,
        requested,
        regime_label=regime_label,
    )
    if qty != requested:
        logger.info(
            "📊 regime sizing %s %s: %s",
            strategy_name,
            symbol,
            reason,
        )
    return qty
