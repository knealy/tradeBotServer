"""
Backtesting package — ``import core.backtest`` does not load pandas/numpy (PEP 562 lazy exports).
"""

from __future__ import annotations

import importlib
from typing import Any

__all__ = (
    "HistoricalDataLoader",
    "BacktestEngine",
    "PerformanceMetrics",
    "MonteCarloSimulator",
)

# submodule -> exported symbol name (same as class name)
_LAZY_EXPORTS: dict[str, str] = {
    "HistoricalDataLoader": "core.backtest.data_loader",
    "BacktestEngine": "core.backtest.engine",
    "PerformanceMetrics": "core.backtest.metrics",
    "MonteCarloSimulator": "core.backtest.monte_carlo",
}


def __getattr__(name: str) -> Any:
    mod_path = _LAZY_EXPORTS.get(name)
    if mod_path is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    mod = importlib.import_module(mod_path)
    obj = getattr(mod, name)
    globals()[name] = obj
    return obj


def __dir__() -> list[str]:
    return sorted(set(__all__) | set(globals().keys()))
