"""In-process backtest runner for walkforward scripts.

The walkforward scripts (``scripts/walkforward_*.py``) historically fan-out
backtests by ``subprocess.run([python, core/backtest_executor.py, ...])`` once
per fold-strategy-symbol task. With Tier 1 in place the per-replay engine work
is ~0.5-0.8 s on a 3-month MNQ slice, but each subprocess still pays a fixed
~250-400 ms Python interpreter + import startup tax (pandas + numpy + pyarrow +
strategy module bytecode). On a 36-task matrix that's ~10 s of pure overhead
serialized across 8 parallel workers — comparable to the actual replay cost.

This module exposes :func:`run_backtest_inprocess`, a function with the same
input/output contract as the subprocess CLI's ``--format=json --include-trades``
mode. It is designed to be the per-task callable for a
:class:`concurrent.futures.ProcessPoolExecutor`. Workers are warmed once at pool
startup (see :func:`worker_init`) and amortize all import costs across the
dozens of tasks they handle.

Parity guarantee
----------------
The function instantiates a fresh :class:`BacktestExecutor` per call, runs the
same async ``run_backtest`` pipeline the CLI uses, and serializes the bundle via
the same ``_serialize_backtest_bundle`` helper. The only difference from the
subprocess path is that env-var overrides (``BACKTEST_FAST_LOOP``, etc.) must be
applied via :func:`worker_init` at pool startup rather than as a per-call
``env=`` dict, because env vars are process-wide. The walkforward scripts
already use a single ``extra_env`` for an entire run, so this is not a
limitation in practice.

Why ``asyncio.run`` per call instead of a long-lived loop?
----------------------------------------------------------
``run_backtest`` uses ``asyncio`` only for compatibility with the live broker
adapter interface; the backtest itself is synchronous. ``asyncio.run`` builds
and tears down a loop in ~1 ms, far less than any of the replay cost, and
keeps each task fully isolated (no event-loop state carries forward, no
unawaited tasks linger between folds).
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional


def worker_init(env_overrides: Optional[Dict[str, str]] = None) -> None:
    """Pre-warm imports and apply env overrides in a freshly-spawned worker process.

    Called via ``ProcessPoolExecutor(initializer=worker_init, initargs=(env,))``.
    Each call runs **once per worker**, not once per task. The savings vs.
    re-importing per task: ~250-400 ms on a Mac M-series (pandas + numpy +
    pyarrow + strategy_replay + strategies/* all loaded once and held warm).

    Env-var overrides applied here propagate to every task the worker
    subsequently handles — the walkforward scripts batch a single ``extra_env``
    for an entire run, so this matches the subprocess semantics where each
    process received the same env.
    """
    if env_overrides:
        for k, v in env_overrides.items():
            os.environ[str(k)] = str(v)

    # Pre-warm the heavy imports the replay path will need. The first import in
    # any worker is the slow one; subsequent in-worker imports are cheap.
    import numpy  # noqa: F401
    import pandas  # noqa: F401

    try:
        import pyarrow  # noqa: F401
    except ImportError:
        pass

    # Walkforward fan-out generates a lot of strategy WARN-level noise (e.g.
    # ``morning_range_reversion`` logs every skipped sweep). We've already
    # validated correctness — drop logs to ERROR to keep stdout clean and
    # cut a few hundred ms of formatting + write() syscalls on big matrices.
    logging.disable(logging.WARNING)

    # Ensure repo root is on sys.path so ``core.*`` / ``strategies.*`` resolve
    # when workers are spawned from a different cwd (``ProcessPoolExecutor``
    # inherits the parent's cwd but not necessarily its ``sys.path[0]``).
    root = Path(__file__).resolve().parents[2]
    root_str = str(root)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)

    # Eagerly import the executor — first import is the slow one.
    from core import backtest_executor  # noqa: F401


def run_backtest_inprocess(
    *,
    strategy: str,
    symbol: str,
    csv_path: str,
    start: str,
    end: str,
    timeframe: str = "5m",
    initial_capital: float = 50000.0,
    slippage_ticks: float = 0.5,
    include_trades: bool = True,
    replay_csv_1m: Optional[str] = None,
) -> Dict[str, Any]:
    """Drop-in in-process replacement for the subprocess JSON-mode CLI.

    Args mirror the ``core/backtest_executor.py`` CLI's
    ``--replay --format=json --include-trades`` invocation:

    Returns the same dict shape as ``json.loads(subprocess.stdout.splitlines()[-1])``:
    ``{"ok": True, "cache_key": ..., "result": {...with optional trades list...}}``
    on success, ``{"ok": False, "error": str, "strategy": ..., "symbol": ...}``
    on failure.
    """
    import asyncio

    # Lazy imports so this module is importable without dragging in pandas/numpy
    # at module load time — keeps ``from core.backtest.inprocess_runner import ...``
    # cheap in the parent walkforward script.
    from core.backtest_executor import BacktestExecutor, _serialize_backtest_bundle

    try:
        start_dt = datetime.fromisoformat(start)
        end_dt = datetime.fromisoformat(end)
    except ValueError as exc:
        return {
            "ok": False,
            "error": f"invalid date: {exc}",
            "strategy": strategy,
            "symbol": symbol,
        }

    async def _go() -> Dict[str, Any]:
        executor = BacktestExecutor(broker_adapter=None)
        try:
            bundle = await executor.run_backtest(
                strategy_name=strategy,
                symbol=symbol,
                timeframe=timeframe,
                start_date=start_dt,
                end_date=end_dt,
                initial_capital=initial_capital,
                use_sample_data=False,
                csv_file=csv_path,
                replay_csv_1m=replay_csv_1m,
                slippage_ticks=slippage_ticks,
            )
        except Exception as exc:  # pragma: no cover — defensive
            return {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
                "strategy": strategy,
                "symbol": symbol,
            }
        if bundle is None:
            return {
                "ok": False,
                "error": "run_backtest returned no result",
                "strategy": strategy,
                "symbol": symbol,
            }
        return _serialize_backtest_bundle(
            bundle, ok=True, include_trades=include_trades
        )

    return asyncio.run(_go())
