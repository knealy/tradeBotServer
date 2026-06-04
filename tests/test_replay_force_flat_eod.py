"""Regression test for the EOD force-flat guard in
``core.backtest.strategy_replay.StrategyReplayEngine._maybe_replay_force_flat_et``.

Pinpoints two invariants:

1. ``cutoff_minutes`` trigger: any bar whose ET clock crosses
   ``timing.replay_force_flat_et`` (default 16:00 ET) flat-files all
   open positions + cancels pending orders.

2. **Date-rollover trigger** (the round-14 ``overnight_range`` fix):
   any bar whose ET *date* differs from the previously-recorded bar's
   ET date also fires the flat, even when no bar at the configured
   cutoff appears in the data (CME settlement gap from 16:00 → 18:00
   ET).  This is the regression net for the user-reported scenario
   where ``overnight_range`` positions persisted for 7-15 calendar
   days on fold 8 of the 9m walkforward.
"""

from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace
from typing import Any, List

import pandas as pd
import pytest


def _make_engine() -> Any:
    """Construct a minimal engine wrapped by a stub StrategyReplayEngine
    just enough to exercise ``_maybe_replay_force_flat_et``."""
    from core.backtest.strategy_replay import StrategyReplayEngine

    # We don't need a real strategy — bypass __init__ and inject only the
    # attributes the flat path touches.
    eng = StrategyReplayEngine.__new__(StrategyReplayEngine)

    # Stub backtest engine surface.
    be = SimpleNamespace(
        positions={"MNQ": SimpleNamespace(
            symbol="MNQ",
            side="BUY",
            quantity=1,
            entry_price=15000.0,
            entry_time=datetime(2026, 1, 8, 15, 25),  # 15:25 ET fill
            current_price=15050.0,
        )},
        pending_orders=[
            SimpleNamespace(
                order_id="exit_tp", status="PENDING", symbol="MNQ",
                oco_group="oco_1",
            ),
            SimpleNamespace(
                order_id="exit_sl", status="PENDING", symbol="MNQ",
                oco_group="oco_1",
            ),
        ],
    )
    eng.backtest_engine = be

    # The flat helper places synthetic market orders + force-fills them.
    # We monkey-patch the helpers to record the call without needing the
    # full engine machinery (covered elsewhere in test_replay_engine.py).
    eng._fired_calls = []  # type: ignore[attr-defined]

    def _cancel_all():
        eng._fired_calls.append("cancel_all")
        be.pending_orders.clear()

    def _flat_all(bar):
        eng._fired_calls.append("flat_all")
        be.positions.clear()

    eng._replay_cancel_all_pending_orders = _cancel_all
    eng._replay_market_flat_all_open_positions = _flat_all
    return eng


def _bar(ts_iso: str, open_px: float = 15000.0) -> pd.Series:
    """Build a single OHLCV ``pd.Series`` indexed by a UTC timestamp."""
    return pd.Series(
        {"open": open_px, "high": open_px, "low": open_px,
         "close": open_px, "volume": 1.0},
        name=pd.Timestamp(ts_iso),
    )


# ---------------------------------------------------------------- cutoff trigger


def test_force_flat_fires_at_cutoff():
    """Bar at 16:00 ET (cutoff_minutes=960) flat-files the position."""
    eng = _make_engine()
    bar = _bar("2026-01-08T21:00:00+00:00")  # 16:00 ET (EST)
    eng._maybe_replay_force_flat_et(
        bar, cutoff_minutes=960, bar_minutes_et=960, bar_et_date=date(2026, 1, 8),
    )
    assert "cancel_all" in eng._fired_calls
    assert "flat_all" in eng._fired_calls


def test_force_flat_does_not_fire_before_cutoff():
    eng = _make_engine()
    bar = _bar("2026-01-08T20:00:00+00:00")  # 15:00 ET
    eng._maybe_replay_force_flat_et(
        bar, cutoff_minutes=960, bar_minutes_et=900, bar_et_date=date(2026, 1, 8),
    )
    assert eng._fired_calls == []


def test_force_flat_no_op_when_no_positions_or_pendings():
    eng = _make_engine()
    eng.backtest_engine.positions.clear()
    eng.backtest_engine.pending_orders.clear()
    bar = _bar("2026-01-08T22:00:00+00:00")  # 17:00 ET, past cutoff
    eng._maybe_replay_force_flat_et(
        bar, cutoff_minutes=960, bar_minutes_et=1020, bar_et_date=date(2026, 1, 8),
    )
    assert eng._fired_calls == []


def test_force_flat_disabled_when_cutoff_is_none():
    eng = _make_engine()
    bar = _bar("2026-01-08T22:00:00+00:00")
    eng._maybe_replay_force_flat_et(
        bar, cutoff_minutes=None, bar_minutes_et=1020, bar_et_date=date(2026, 1, 8),
    )
    assert eng._fired_calls == []


# ---------------------------------------------------------------- date-rollover trigger


def test_force_flat_fires_on_date_rollover_even_below_cutoff():
    """Regression: position opens 15:25 ET on Jan 8, next bar in the data
    is at 18:00 ET on Jan 8 — at bar_minutes 1080 the cutoff branch fires.
    But if the CSV had a gap that landed the next bar at 09:30 ET on
    Jan 9 (bar_minutes 570 < 960), the existing cutoff check would NOT
    have fired.  The date-rollover guard catches it.
    """
    eng = _make_engine()
    # Bar 1: 15:25 ET on Jan 8 — under cutoff, no flat, prev_date tracked.
    bar1 = _bar("2026-01-08T20:25:00+00:00")
    eng._maybe_replay_force_flat_et(
        bar1, cutoff_minutes=960, bar_minutes_et=925, bar_et_date=date(2026, 1, 8),
    )
    assert eng._fired_calls == []
    assert eng._last_replay_bar_et_date == date(2026, 1, 8)

    # Bar 2: 09:30 ET on Jan 9 — under cutoff BUT new date → must flat.
    bar2 = _bar("2026-01-09T14:30:00+00:00")
    eng._maybe_replay_force_flat_et(
        bar2, cutoff_minutes=960, bar_minutes_et=570, bar_et_date=date(2026, 1, 9),
    )
    assert "cancel_all" in eng._fired_calls
    assert "flat_all" in eng._fired_calls


def test_force_flat_does_not_fire_on_same_date_below_cutoff():
    eng = _make_engine()
    bar1 = _bar("2026-01-08T20:00:00+00:00")
    eng._maybe_replay_force_flat_et(
        bar1, cutoff_minutes=960, bar_minutes_et=900, bar_et_date=date(2026, 1, 8),
    )
    bar2 = _bar("2026-01-08T20:05:00+00:00")
    eng._maybe_replay_force_flat_et(
        bar2, cutoff_minutes=960, bar_minutes_et=905, bar_et_date=date(2026, 1, 8),
    )
    assert eng._fired_calls == []


def test_force_flat_handles_none_bar_et_date_gracefully():
    """Bad timestamps that fail tz parsing should NOT crash the loop;
    the rollover guard simply skips for that bar."""
    eng = _make_engine()
    bar = _bar("2026-01-08T20:00:00+00:00")
    eng._maybe_replay_force_flat_et(
        bar, cutoff_minutes=960, bar_minutes_et=900, bar_et_date=None,
    )
    assert eng._fired_calls == []


def test_force_flat_first_bar_does_not_fire_rollover():
    """Very first bar of replay has no ``_last_replay_bar_et_date`` —
    the rollover guard must not fire spuriously."""
    eng = _make_engine()
    eng._last_replay_bar_et_date = None
    bar = _bar("2026-01-08T20:00:00+00:00")
    eng._maybe_replay_force_flat_et(
        bar, cutoff_minutes=960, bar_minutes_et=900, bar_et_date=date(2026, 1, 8),
    )
    assert eng._fired_calls == []


# ---------------------------------------------------------------- fast-loop _BarRow parity


def test_replay_market_flat_accepts_barrow_fast_loop():
    """Regression for round-14 fast-loop bypass.

    ``BACKTEST_FAST_LOOP=1`` produces ``_BarRow`` instances (not ``pd.Series``)
    in the per-bar hot loop. The original ``_replay_market_flat_all_open_positions``
    had an ``isinstance(bar, pd.Series)`` early-return that silently no-op'd
    the EOD flat → ``overnight_range`` positions persisted across calendar
    days in fast-loop walkforwards.

    This test wires the **real** ``_replay_market_flat_all_open_positions``
    against a minimal BacktestEngine stub and asserts the synthetic close
    order is placed + filled when handed a ``_BarRow`` (not a Series).
    """
    from core.backtest.strategy_replay import StrategyReplayEngine, _BarRow
    from core.backtest.engine import OrderSide, OrderStatus, OrderType

    eng = StrategyReplayEngine.__new__(StrategyReplayEngine)
    placed: List[Any] = []

    def _place(symbol, side, quantity, order_type, price):
        order = SimpleNamespace(
            order_id=f"close_{len(placed)}",
            symbol=symbol, side=side, quantity=quantity,
            order_type=order_type, price=price,
            status=OrderStatus.PENDING,
            filled_price=None, filled_timestamp=None,
        )
        placed.append(order)
        be.pending_orders.append(order)
        return order.order_id

    def _update_position(order, px):
        be.positions.pop(order.symbol, None)

    be = SimpleNamespace(
        positions={
            "MNQ": SimpleNamespace(symbol="MNQ", side=OrderSide.BUY, quantity=1),
        },
        pending_orders=[],
        filled_orders=[],
        place_order=_place,
        _update_position=_update_position,
    )
    eng.backtest_engine = be

    bar = _BarRow(
        name=pd.Timestamp("2026-05-07T13:30:00+00:00"),
        open_=15000.0, high=15010.0, low=14990.0, close=15005.0, volume=10,
    )
    eng._replay_market_flat_all_open_positions(bar)

    assert placed, "synthetic flatten order must be placed for _BarRow"
    assert placed[0].status == OrderStatus.FILLED
    assert getattr(placed[0], "exit_reason", "") == "replay_force_flat_et"
    assert "MNQ" not in be.positions, "position must be cleared after flatten"
