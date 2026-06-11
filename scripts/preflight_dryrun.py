#!/usr/bin/env python3
"""Dry-run ``analyze()`` for MRR and overnight_range against recent bars.

Loads the last ~3 ET sessions of bars from the canonical Databento CSVs,
spins up each strategy with a minimal mock trading bot, calls ``analyze()``
on each symbol, and reports whether the call completed without exceptions
and what signal (if any) was produced.  This validates the END-TO-END
pipeline (bars → indicators → analyze → signal dict) without touching the
broker.

Output contract — stdout starts with ``DRYRUN_OK`` on success, anything
else means failure.  This contract is consumed by
``scripts/preflight_live_deploy.sh``.
"""

from __future__ import annotations

import asyncio
import os
import sys
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("ENABLE_SIGNALR", "false")
os.environ.setdefault("DATABASE_URL", "")  # don't try Postgres


def _csv_path(symbol: str) -> Path:
    return ROOT / "historical_data" / "price" / f"{symbol}_5m_databento.csv"


def _load_recent_bars(symbol: str, n: int = 1000) -> List[Dict[str, Any]]:
    """Load the most recent ``n`` 5m bars from the canonical Databento CSV."""
    import csv
    p = _csv_path(symbol)
    if not p.exists():
        return []
    rows: List[Dict[str, Any]] = []
    with p.open() as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                ts = datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00"))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                rows.append({
                    "timestamp": ts,
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": float(row.get("volume", 0) or 0),
                })
            except Exception:
                continue
    return rows[-n:]


def _make_mock_bot(bars_by_symbol: Dict[str, List[Dict[str, Any]]]):
    """Build a minimal MagicMock trading_bot that satisfies the strategy
    interface for ``analyze()`` to run.  Returns the canned bars for each
    symbol on every ``get_historical_data()`` call."""
    bot = MagicMock()
    bot._is_strategy_replay = True
    # MRR's ``_now_eastern()`` checks ``self.trading_bot._current_bar_timestamp``;
    # a MagicMock child here breaks the time comparison.  Set explicit fields
    # that match what a real replay engine sets.
    bot._current_bar_timestamp = None
    bot.backtest_engine = None

    async def _get_hist(symbol: str, timeframe: str = "5m", limit: int = 500, **_):
        bars = bars_by_symbol.get(symbol.upper(), [])
        return bars[-limit:] if limit else bars

    bot.get_historical_data = _get_hist
    bot.calculate_atr = AsyncMock(return_value=10.0)
    bot.get_position = MagicMock(return_value=None)
    bot.get_pending_orders = MagicMock(return_value=[])

    # MRR (and others) call ``_get_current_time_et()`` / similar.  Return
    # the timestamp of the most recent bar so the strategy's wall-clock
    # gates behave consistently with the replay data.
    try:
        from zoneinfo import ZoneInfo
        et = ZoneInfo("America/New_York")
    except Exception:
        from datetime import timezone as _tz
        et = _tz.utc
    latest_ts = None
    for bars in bars_by_symbol.values():
        if bars:
            ts = bars[-1]["timestamp"]
            if latest_ts is None or ts > latest_ts:
                latest_ts = ts
    if latest_ts is None:
        latest_ts = datetime.now(timezone.utc)
    et_now = latest_ts.astimezone(et)
    bot._get_current_time_et = MagicMock(return_value=et_now)
    bot.get_current_time_et = MagicMock(return_value=et_now)
    bot.now_et = MagicMock(return_value=et_now)

    # ``manage_positions`` / ``place_bracket_order`` should never run in
    # dry-run — but if a strategy calls them defensively, no-op them.
    bot.place_bracket_order = AsyncMock(return_value={"error": None, "dry_run": True})
    bot.cancel_order = AsyncMock(return_value=True)
    bot.flatten_position = AsyncMock(return_value=True)
    return bot


def _print_signal(sig: Optional[Dict[str, Any]], *, strategy: str, symbol: str) -> None:
    if sig is None:
        print(f"  {strategy:<28} {symbol:<4}  no signal (expected — most bars don't fire)")
    else:
        side = sig.get("action") or sig.get("side") or "?"
        entry = sig.get("entry_price") or "?"
        sl = sig.get("stop_loss") or "?"
        tp = sig.get("take_profit") or "?"
        print(f"  {strategy:<28} {symbol:<4}  SIGNAL: {side} entry={entry} sl={sl} tp={tp}")


async def _run_strategy(strat_module: str, strat_class_name: str, strategy_name: str,
                         symbols: List[str], bars_by_symbol: Dict[str, List[Dict[str, Any]]]) -> List[str]:
    """Import + instantiate + analyze for one strategy.  Returns error list."""
    errors: List[str] = []
    try:
        mod = __import__(strat_module, fromlist=[strat_class_name])
        klass = getattr(mod, strat_class_name)
    except Exception as exc:
        errors.append(f"  {strategy_name}: import failed: {exc}")
        return errors

    bot = _make_mock_bot(bars_by_symbol)
    try:
        strat = klass(bot)
    except Exception as exc:
        errors.append(f"  {strategy_name}: instantiation failed: {exc}\n{traceback.format_exc(limit=2)}")
        return errors

    for sym in symbols:
        if not bars_by_symbol.get(sym.upper()):
            errors.append(f"  {strategy_name} {sym}: no CSV bars available")
            continue
        try:
            sig = await strat.analyze(sym)
            _print_signal(sig, strategy=strategy_name, symbol=sym)
        except Exception as exc:
            errors.append(f"  {strategy_name} {sym}: analyze raised: {exc}\n{traceback.format_exc(limit=2)}")
    return errors


async def main():
    print("Loading recent 5m bars for MNQ + MGC + MES…")
    bars_by_symbol: Dict[str, List[Dict[str, Any]]] = {}
    for sym in ("MNQ", "MGC", "MES"):
        bars = _load_recent_bars(sym, n=1500)
        bars_by_symbol[sym] = bars
        if bars:
            print(f"  {sym}: {len(bars)} bars, latest ts = {bars[-1]['timestamp']}")
        else:
            print(f"  {sym}: NO CSV at {_csv_path(sym)} — skipping symbol")

    # Sanity: latest bar should be within the last ~7 days.  Otherwise the
    # CSV is stale and the dry-run won't catch live-bar regressions.
    stale: List[str] = []
    now = datetime.now(timezone.utc)
    for sym, bars in bars_by_symbol.items():
        if not bars:
            continue
        age = now - bars[-1]["timestamp"]
        if age > timedelta(days=14):
            stale.append(f"{sym}: latest bar {age.days}d old (refresh historical_data/price/{sym}_5m_databento.csv)")

    print()
    print("Calling analyze() on each (strategy, symbol)…")

    all_errors: List[str] = []
    # MRR — MNQ + MGC.
    all_errors += await _run_strategy(
        "strategies.morning_range_reversion_strategy",
        "MorningRangeReversionStrategy",
        "morning_range_reversion",
        ["MNQ", "MGC"],
        bars_by_symbol,
    )
    # overnight_range — MNQ + MGC.
    all_errors += await _run_strategy(
        "strategies.overnight_range_strategy",
        "OvernightRangeStrategy",
        "overnight_range",
        ["MNQ", "MGC"],
        bars_by_symbol,
    )

    if stale:
        print()
        print("⚠ Stale CSVs (analyze still ran but live signals won't match recent regime):")
        for s in stale:
            print(f"  {s}")

    print()
    if all_errors:
        print("DRYRUN_FAIL  errors encountered:")
        for e in all_errors:
            print(e)
        sys.exit(1)
    print("DRYRUN_OK  all analyze() calls completed without exceptions")


if __name__ == "__main__":
    asyncio.run(main())
