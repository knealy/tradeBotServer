"""Research orchestration on top of core.backtest + BacktestExecutor (grid, OOS, MC gate).

Import symbols from ``core.research.runner`` to avoid import-order side effects when using
``python -m core.research.runner``.
"""

__all__ = ("ResearchRunConfig", "run_research", "parse_param_grid")


def __getattr__(name: str):
    if name in __all__:
        from core.research import runner as _runner

        return getattr(_runner, name)
    raise AttributeError(name)
