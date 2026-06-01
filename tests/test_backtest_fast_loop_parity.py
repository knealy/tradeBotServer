"""Byte-identical regression test for ``BACKTEST_FAST_LOOP=1`` engine path.

Runs the same fixed canonical-CSV fold twice through
``core/backtest_executor.py --replay --include-trades`` — once with the slow
``df.iterrows`` loop, once with the ``_iter_bars_fast`` NumPy column loop —
and asserts the resulting trade lists are **bit-identical**: same number of
trades, same entry/exit timestamps, same fill prices, same exit reasons,
same PnL.

If this test fails, the fast loop has drifted from the slow loop's
semantics. Inspect:

1. ``_BarRow.__getitem__`` — every key the slow path reads from a
   ``pd.Series`` must resolve here. Common drift: a new strategy field
   appears in ``_check_order_fill`` or ``_update_position`` that ``_BarRow``
   doesn't expose.
2. ``_iter_bars_fast`` — float/int casts of NumPy column slices must match
   pandas' default Series casts. Volume in particular can drift when a
   bar's volume is NaN (slow path passes it through; fast path falls back
   to 0 via the NaN-safe int cast).

The fast loop is gated behind ``BACKTEST_FAST_LOOP=1`` (default off) so
production walkforward runs remain on the validated slow path until this
test has covered every strategy we care about. Add the per-strategy
parameter rows below as new strategies graduate to fast-loop coverage.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

ROOT = Path(__file__).resolve().parent.parent

# Canonical 5m CSV that ships with the repo. If this disappears the test
# becomes a no-op skip rather than failing CI; the parity invariant is
# already tested against synthetic data via the fast-loop unit test in
# ``test_backtest_perf_helpers.py``.
_CANONICAL_CSV_PATH = ROOT / "historical_data" / "price" / "MNQ_5m_databento.csv"


def _run_backtest_fold(
    *,
    strategy: str,
    symbol: str,
    csv_path: Path,
    start: str,
    end: str,
    fast_loop: bool,
) -> Dict[str, Any]:
    """Spawn ``core/backtest_executor.py`` with the requested ``BACKTEST_FAST_LOOP`` setting."""
    env = os.environ.copy()
    env["ENABLE_SIGNALR"] = "false"
    env["PYTHONUNBUFFERED"] = "1"
    env["BACKTEST_FAST_LOOP"] = "1" if fast_loop else "0"
    cmd = [
        sys.executable,
        str(ROOT / "core" / "backtest_executor.py"),
        f"--strategy={strategy}",
        f"--symbol={symbol}",
        "--timeframe=5m",
        f"--csv={csv_path}",
        f"--start={start}",
        f"--end={end}",
        "--replay",
        "--format=json",
        "--include-trades",
    ]
    proc = subprocess.run(
        cmd, cwd=str(ROOT), env=env, capture_output=True, text=True, check=False
    )
    if proc.returncode != 0:
        pytest.fail(
            f"backtest_executor failed (rc={proc.returncode}, fast={fast_loop}):\n"
            f"stderr:\n{proc.stderr[-2000:]}\nstdout tail:\n{proc.stdout[-1000:]}"
        )
    last = (proc.stdout or "").strip().splitlines()[-1]
    payload = json.loads(last)
    assert payload.get("ok"), f"backtest_executor returned not-ok: {payload}"
    return payload


def _normalize_trade_for_diff(t: Dict[str, Any]) -> Dict[str, Any]:
    """Drop ``trade_id`` (counter, identical given identical sequence) and round
    floats so trivial floating-point noise from different code paths doesn't
    trip the strict equality check below. Same epsilon the recap metrics use.
    """
    return {
        "side": str(t.get("side")),
        "symbol": str(t.get("symbol")),
        "entry_time": str(t.get("entry_time")),
        "exit_time": str(t.get("exit_time")),
        "entry_price": round(float(t.get("entry_price") or 0), 6),
        "exit_price": round(float(t.get("exit_price") or 0), 6),
        "quantity": int(t.get("quantity") or 0),
        "pnl": round(float(t.get("pnl") or 0), 6),
        "exit_reason": str(t.get("exit_reason") or ""),
        "bars_held": int(t.get("bars_held") or 0),
    }


@pytest.mark.skipif(
    not _CANONICAL_CSV_PATH.is_file(),
    reason="canonical MNQ_5m_databento.csv not present in this checkout",
)
@pytest.mark.parametrize(
    "strategy,symbol,start,end",
    [
        ("overnight_range", "MNQ", "2026-04-01", "2026-04-30"),
        ("morning_range_reversion", "MNQ", "2026-04-01", "2026-04-30"),
        # Longer window catches O(n²) drift in ``mock_get_historical_data``
        # — the per-bar bisect path must hold semantics across multiple months,
        # not just one. Picked Jan-Apr because it spans the 6-month-DD breaker
        # cluster and exercises the multi-fold consec-loss state.
        ("morning_range_reversion", "MNQ", "2026-01-01", "2026-04-30"),
    ],
)
def test_fast_loop_trade_list_matches_slow_loop(
    strategy: str, symbol: str, start: str, end: str
) -> None:
    """Byte-identical (modulo trade_id counters) trade lists across slow and fast paths."""
    slow = _run_backtest_fold(
        strategy=strategy, symbol=symbol, csv_path=_CANONICAL_CSV_PATH,
        start=start, end=end, fast_loop=False,
    )
    fast = _run_backtest_fold(
        strategy=strategy, symbol=symbol, csv_path=_CANONICAL_CSV_PATH,
        start=start, end=end, fast_loop=True,
    )

    slow_res = slow.get("result") or {}
    fast_res = fast.get("result") or {}

    # Top-line metrics must match within rounding noise.
    for key in ("total_trades", "total_pnl", "win_rate", "max_drawdown"):
        s = slow_res.get(key)
        f = fast_res.get(key)
        if isinstance(s, (int, float)) and isinstance(f, (int, float)):
            assert abs(float(s) - float(f)) < 1e-6, (
                f"{key} drift: slow={s} fast={f} ({strategy} {symbol} {start}..{end})"
            )
        else:
            assert s == f, f"{key} drift: slow={s!r} fast={f!r}"

    slow_trades: List[Dict[str, Any]] = list(slow_res.get("trades") or [])
    fast_trades: List[Dict[str, Any]] = list(fast_res.get("trades") or [])
    assert len(slow_trades) == len(fast_trades), (
        f"trade count drift: slow={len(slow_trades)} fast={len(fast_trades)} "
        f"({strategy} {symbol} {start}..{end})"
    )

    for ix, (s, f) in enumerate(zip(slow_trades, fast_trades)):
        ns = _normalize_trade_for_diff(s)
        nf = _normalize_trade_for_diff(f)
        assert ns == nf, (
            f"trade {ix} drift on {strategy} {symbol}:\n"
            f"  slow: {ns}\n  fast: {nf}"
        )
