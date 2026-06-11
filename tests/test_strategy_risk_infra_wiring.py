"""Audit: every registered strategy must correctly wire risk infrastructure.

The 2026-06-11 ``opening_range_breakout`` / ``price_action_fade`` debug
session uncovered a class of bug where a strategy imports
NON-EXISTENT functions from ``core.consec_loss_breaker`` (e.g.
``register_strategy_breaker``, ``evaluate_breaker``) inside a
``try / except Exception`` wrapper.  The except branch silently
swallows the ``ImportError``, leaving the strategy with NO active
consec-loss-breaker bridge — a CATASTROPHIC failure mode for live
deployment that produced ZERO error logs.

This test prevents the bug class from re-occurring by enforcing two
invariants on every strategy in ``BUILTIN_STRATEGY_SPECS``:

1. **Canonical-API import audit (AST static scan)** — every
   ``from core.consec_loss_breaker import X, Y, Z`` statement may
   only import names from the canonical API surface:
   ``{BreakerConfig, evaluate, trade_iter_for_strategy}``.  Any
   other imported name is a TYPO / out-of-date refactor that will
   silently fail at runtime.

2. **Instantiation + canonical-method audit** — every strategy
   instantiates against a minimal mock bot.  The wiring of the
   consec-loss breaker bridge is verified by calling the canonical
   ``_breaker_config_for_symbol()`` method (when present) and
   asserting it returns a valid ``BreakerConfig`` instance.

Future-proofing: ``CANONICAL_CONSEC_LOSS_API`` is the SINGLE source of
truth.  When the breaker API surface changes, update this set; the
test will then surface every strategy still using the old names.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path
from typing import Set
from unittest.mock import AsyncMock, MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent

# Single source of truth for the consec-loss-breaker API surface.
CANONICAL_CONSEC_LOSS_API: Set[str] = {
    "BreakerConfig",
    "evaluate",
    "trade_iter_for_strategy",
}


def _strategy_specs():
    """Resolve ``BUILTIN_STRATEGY_SPECS`` lazily to avoid full strategy_manager init."""
    from strategies.strategy_manager import BUILTIN_STRATEGY_SPECS
    return BUILTIN_STRATEGY_SPECS


def _strategy_source_paths():
    """(strategy_name, module_path, source_file_path) for each spec."""
    specs = _strategy_specs()
    out = []
    for name, (module_path, _class_name, _desc) in specs.items():
        # ``strategies.morning_range_reversion_strategy`` → ``strategies/morning_range_reversion_strategy.py``
        rel = module_path.replace(".", "/") + ".py"
        out.append((name, module_path, ROOT / rel))
    return out


# ────────────────────────── 1. AST static scan ─────────────────────────────


@pytest.mark.parametrize(
    "strategy_name,module_path,source_path",
    _strategy_source_paths(),
    ids=lambda v: v if isinstance(v, str) else "",
)
def test_consec_loss_breaker_imports_use_canonical_api(
    strategy_name: str, module_path: str, source_path: Path
):
    """Every ``from core.consec_loss_breaker import X`` in any strategy must
    import ONLY names in ``CANONICAL_CONSEC_LOSS_API``.

    Catches the silent-bypass bug class: misspelled / out-of-date /
    invented names that ``try / except`` swallows at runtime.
    """
    if not source_path.exists():
        pytest.skip(f"Source file missing: {source_path}")

    tree = ast.parse(source_path.read_text())
    offenders: list[tuple[int, str]] = []  # (lineno, name)

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module != "core.consec_loss_breaker":
                continue
            for alias in node.names:
                if alias.name not in CANONICAL_CONSEC_LOSS_API:
                    offenders.append((node.lineno, alias.name))

    if offenders:
        msg = (
            f"Strategy {strategy_name!r} imports non-canonical names from "
            f"core.consec_loss_breaker — these will silently fail at runtime "
            f"and disable the consec-loss breaker bridge entirely:\n"
        )
        for lineno, nm in offenders:
            msg += f"  line {lineno}: {nm!r} (not in {sorted(CANONICAL_CONSEC_LOSS_API)})\n"
        msg += (
            f"Fix: replace with the canonical API surface and call "
            f"``evaluate(...)`` + ``BreakerConfig(...)`` + ``trade_iter_for_strategy(...)``. "
            f"See ``strategies/overnight_range_strategy.py`` for the reference pattern."
        )
        pytest.fail(msg)


def test_canonical_consec_loss_api_is_exported():
    """Sanity check: the canonical API surface this test enforces actually
    exists in ``core.consec_loss_breaker``. If this fails, the canonical
    set above is stale and needs updating."""
    mod = importlib.import_module("core.consec_loss_breaker")
    missing = [n for n in CANONICAL_CONSEC_LOSS_API if not hasattr(mod, n)]
    assert not missing, (
        f"CANONICAL_CONSEC_LOSS_API claims these names exist in "
        f"core.consec_loss_breaker but they don't: {missing}. "
        f"Update the canonical set in this test."
    )


# ─────────────────────── 2. Instantiation audit ───────────────────────────


def _mock_bot():
    """Minimal mock that satisfies most strategy constructors."""
    bot = MagicMock()
    bot._is_strategy_replay = True
    bot._current_bar_timestamp = None
    bot.backtest_engine = None
    bot.get_historical_data = AsyncMock(return_value=[])
    bot.calculate_atr = AsyncMock(return_value=10.0)
    bot.get_position = MagicMock(return_value=None)
    bot.get_pending_orders = MagicMock(return_value=[])
    bot.place_bracket_order = AsyncMock(return_value={"error": None})
    bot.cancel_order = AsyncMock(return_value=True)
    bot.flatten_position = AsyncMock(return_value=True)
    return bot


# Strategies known to require specialised mock surfaces beyond what
# ``_mock_bot`` provides.  These are skipped from the runtime audit
# but STILL covered by the AST static scan above.
_INSTANTIATION_SKIP: Set[str] = {
    # Live-only strategies that touch the broker on construction.
    # (Currently none, but reserved for future use.)
}


@pytest.mark.parametrize(
    "strategy_name,module_path,source_path",
    _strategy_source_paths(),
    ids=lambda v: v if isinstance(v, str) else "",
)
def test_strategy_instantiates_and_has_canonical_breaker_method(
    strategy_name: str, module_path: str, source_path: Path
):
    """Every strategy must instantiate without exceptions AND — if it
    declares any consec-loss breaker bridge — that bridge must call the
    canonical API at runtime, NOT a non-existent helper.

    Runtime semantics check: invoke ``_breaker_config_for_symbol()`` if
    present; assert it returns a ``core.consec_loss_breaker.BreakerConfig``
    instance.  This catches the case where a strategy DEFINES the method
    but mis-builds the config (e.g. calling a non-existent helper inside).
    """
    if strategy_name in _INSTANTIATION_SKIP:
        pytest.skip(f"{strategy_name} requires live-only mocks")
    try:
        mod = importlib.import_module(module_path)
    except Exception as exc:
        pytest.fail(f"{strategy_name}: import failed: {exc}")
    _spec_name = _strategy_specs()[strategy_name][1]
    try:
        klass = getattr(mod, _spec_name)
    except AttributeError as exc:
        pytest.fail(f"{strategy_name}: class {_spec_name} not in module: {exc}")
    try:
        instance = klass(_mock_bot())
    except Exception as exc:
        pytest.fail(f"{strategy_name}: instantiation failed: {exc}")

    if hasattr(instance, "_breaker_config_for_symbol"):
        from core.consec_loss_breaker import BreakerConfig
        try:
            cfg = instance._breaker_config_for_symbol("MNQ")
        except Exception as exc:
            pytest.fail(
                f"{strategy_name}: _breaker_config_for_symbol('MNQ') raised "
                f"{type(exc).__name__}: {exc}. "
                f"Likely a non-canonical import or missing helper.")
        assert isinstance(cfg, BreakerConfig), (
            f"{strategy_name}: _breaker_config_for_symbol returned "
            f"{type(cfg).__name__}, expected BreakerConfig"
        )
