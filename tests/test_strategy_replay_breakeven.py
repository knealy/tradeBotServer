"""BONGO §1B breakeven mechanism inside ``StrategyReplayEngine``.

Verifies the simulated equivalent of ``trading_bot._generic_breakeven_monitor_loop``
is wired into the backtest replay path. Without this, every loss in a recap was the
full SL distance even when MFE > breakeven_trigger_r × |entry-stop| had clearly
been reached intra-bar.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, List
from unittest.mock import MagicMock

import pandas as pd
import pytest

from core.backtest.engine import BacktestEngine
from core.backtest.models import OrderSide, OrderType
from core.backtest.strategy_replay import StrategyReplayEngine


def _bar(ts: datetime, o: float, h: float, l: float, c: float) -> pd.Series:
    """Return a sub-bar pandas Series shaped like ``intrabar_series_iter`` yields."""
    return pd.Series({"open": o, "high": h, "low": l, "close": c, "volume": 1}, name=pd.Timestamp(ts))


def _make_replay_engine() -> StrategyReplayEngine:
    """Bare ``StrategyReplayEngine`` wired with a stub strategy/bot.

    The strategy / bot here are not exercised — only the engine's bracket-replay
    plumbing is. Every test seeds the engine state by hand and calls
    ``_process_subbar_fills`` directly so we can assert the breakeven side-effects
    without round-tripping through ``replay()``.
    """
    strategy = MagicMock()
    bot = MagicMock()
    eng = StrategyReplayEngine(strategy, bot, point_value=2.0, commission_per_contract=0.0, slippage_ticks=0.0)
    return eng


def _seed_long_entry(eng: StrategyReplayEngine, *, entry: float, stop: float, tp: float,
                    bar: Dict[str, Any], qty: int = 1) -> str:
    """Open a LONG position synthetically: place STOP entry, fill it, run bracket placement."""
    be = eng.backtest_engine
    be.set_last_close(entry - 1.0)  # market is below the BUY STOP entry
    entry_oid = be.place_order(
        symbol="MNQ", side=OrderSide.BUY, quantity=qty, order_type=OrderType.STOP,
        stop_price=entry, price=entry,
    )
    # Replicate the replay shim: bracket prices are stashed on the entry order so
    # ``_process_subbar_fills`` places the SL/TP exits at the end of the bar.
    for o in be.pending_orders:
        if o.order_id == entry_oid:
            o.stop_loss_price = stop
            o.take_profit_price = tp
            o.custom_tag = "TB-stop_bracket-test-replay"
            break
    return entry_oid


def _seed_short_entry(eng: StrategyReplayEngine, *, entry: float, stop: float, tp: float,
                     bar: Dict[str, Any], qty: int = 1) -> str:
    """Open a SHORT position synthetically. Mirrors ``_seed_long_entry``."""
    be = eng.backtest_engine
    be.set_last_close(entry + 1.0)
    entry_oid = be.place_order(
        symbol="MNQ", side=OrderSide.SELL, quantity=qty, order_type=OrderType.STOP,
        stop_price=entry, price=entry,
    )
    for o in be.pending_orders:
        if o.order_id == entry_oid:
            o.stop_loss_price = stop
            o.take_profit_price = tp
            o.custom_tag = "TB-stop_bracket-test-replay"
            break
    return entry_oid


# ── Watch registration ──────────────────────────────────────────────────────


def test_register_breakeven_watch_records_normalized_side():
    eng = _make_replay_engine()
    eng._simulate_register_breakeven_watch(
        "ENT123", symbol="mnq", side="BUY",
        entry_price=100.0, profit_threshold=5.0, strategy_name="x",
    )
    w = eng._breakeven_watches["ENT123"]
    assert w["symbol"] == "MNQ"
    assert w["side"] == "LONG"
    assert w["entry_price"] == 100.0
    assert w["profit_threshold"] == 5.0
    assert w["triggered"] is False
    assert w["sl_order_id"] is None
    assert w["entry_bar_index"] is None


def test_register_breakeven_watch_skips_zero_threshold():
    eng = _make_replay_engine()
    eng._simulate_register_breakeven_watch(
        "ENT0", symbol="MNQ", side="SELL",
        entry_price=100.0, profit_threshold=0.0,
    )
    assert "ENT0" not in eng._breakeven_watches


def test_register_breakeven_watch_skips_missing_order_id():
    eng = _make_replay_engine()
    eng._simulate_register_breakeven_watch(
        "", symbol="MNQ", side="BUY",
        entry_price=100.0, profit_threshold=5.0,
    )
    assert eng._breakeven_watches == {}


# ── End-to-end via _process_subbar_fills ────────────────────────────────────


def test_breakeven_triggers_and_moves_long_stop_to_entry():
    """LONG: bar.high - entry >= threshold → SL stop_price snaps to entry; later
    reversal exits at ~$0 (entry price), not the original SL distance."""
    eng = _make_replay_engine()
    t0 = datetime(2026, 5, 21, 13, 0, tzinfo=timezone.utc)

    # Bar 0: BUY STOP 100 fills (high=101 crosses), SL=90, TP=120, breakeven thr=5
    bar0 = _bar(t0, 99, 101, 99, 100.5)
    entry_oid = _seed_long_entry(eng, entry=100.0, stop=90.0, tp=120.0, bar=bar0)
    eng._simulate_register_breakeven_watch(
        entry_oid, symbol="MNQ", side="BUY",
        entry_price=100.0, profit_threshold=5.0,
    )
    eng._process_subbar_fills(bar0, tick_size=0.25)

    # Entry filled, watch linked to SL exit, position open.
    assert "MNQ" in eng.backtest_engine.positions
    watch = eng._breakeven_watches[entry_oid]
    assert watch["sl_order_id"] is not None
    assert watch["entry_bar_index"] == 0
    assert watch["triggered"] is False
    sl_oid = watch["sl_order_id"]
    sl = next(o for o in eng.backtest_engine.filled_orders + eng.backtest_engine.pending_orders if o.order_id == sl_oid)
    assert sl.stop_price == pytest.approx(90.0)

    # Bar 1: high pushes to 106 (>= entry+5=105) but low stays at 102 (above entry=100)
    # so the moved stop is armed but doesn't fire in the same bar.
    eng.backtest_engine.current_bar_index = 1
    bar1 = _bar(t0, 100.5, 106.0, 102.0, 105.0)
    eng._process_subbar_fills(bar1, tick_size=0.25)

    assert eng._breakeven_watches[entry_oid]["triggered"] is True
    sl = next(o for o in eng.backtest_engine.pending_orders if o.order_id == sl_oid)
    assert sl.stop_price == pytest.approx(100.0)
    assert eng.backtest_engine.positions["MNQ"].stop_loss == pytest.approx(100.0)

    # Bar 2: low dips to 99 → moved stop fires at 100 → exit reason stop_loss, ~$0 PnL
    eng.backtest_engine.current_bar_index = 2
    bar2 = _bar(t0, 105, 105, 99.0, 99.5)
    eng._process_subbar_fills(bar2, tick_size=0.25)

    assert "MNQ" not in eng.backtest_engine.positions
    assert len(eng.backtest_engine.trades) == 1
    trade = eng.backtest_engine.trades[0]
    # Retagged from "stop_loss" → "breakeven" so recaps can show this as a distinct
    # category instead of indistinguishable from a real-loss SL hit.
    assert trade.exit_reason == "breakeven"
    assert trade.exit_price == pytest.approx(100.0)
    # PnL is gross (no commission/slippage on this fixture) → 0.0
    assert trade.pnl == pytest.approx(0.0, abs=0.01)


def test_breakeven_triggers_short_side_on_low():
    """SHORT mirror: entry - bar.low >= threshold."""
    eng = _make_replay_engine()
    t0 = datetime(2026, 5, 21, 13, 0, tzinfo=timezone.utc)

    bar0 = _bar(t0, 101, 102, 99.5, 100.0)
    entry_oid = _seed_short_entry(eng, entry=100.0, stop=110.0, tp=80.0, bar=bar0)
    eng._simulate_register_breakeven_watch(
        entry_oid, symbol="MNQ", side="SELL",
        entry_price=100.0, profit_threshold=5.0,
    )
    eng._process_subbar_fills(bar0, tick_size=0.25)
    assert "MNQ" in eng.backtest_engine.positions

    # Bar 1: low dips to 94 (entry - 6 ≥ thr=5) but high stays at 98 (below entry=100)
    # so the moved stop is armed but doesn't fire same bar.
    eng.backtest_engine.current_bar_index = 1
    bar1 = _bar(t0, 99.5, 98.0, 94.0, 95.0)
    eng._process_subbar_fills(bar1, tick_size=0.25)

    watch = eng._breakeven_watches[entry_oid]
    assert watch["triggered"] is True
    sl = next(o for o in eng.backtest_engine.pending_orders if o.order_id == watch["sl_order_id"])
    assert sl.stop_price == pytest.approx(100.0)


def test_breakeven_does_not_trigger_when_mfe_below_threshold():
    """When MFE never crosses thr, the original SL must remain unchanged."""
    eng = _make_replay_engine()
    t0 = datetime(2026, 5, 21, 13, 0, tzinfo=timezone.utc)

    bar0 = _bar(t0, 99, 101, 99, 100.5)
    entry_oid = _seed_long_entry(eng, entry=100.0, stop=90.0, tp=120.0, bar=bar0)
    eng._simulate_register_breakeven_watch(
        entry_oid, symbol="MNQ", side="BUY",
        entry_price=100.0, profit_threshold=5.0,
    )
    eng._process_subbar_fills(bar0, tick_size=0.25)
    sl_oid = eng._breakeven_watches[entry_oid]["sl_order_id"]

    # Bar 1: high only reaches 103 (entry+3 < thr=5) → no trigger
    eng.backtest_engine.current_bar_index = 1
    eng._process_subbar_fills(_bar(t0, 100.5, 103.0, 100.0, 101.0), tick_size=0.25)
    assert eng._breakeven_watches[entry_oid]["triggered"] is False
    sl = next(o for o in eng.backtest_engine.pending_orders if o.order_id == sl_oid)
    assert sl.stop_price == pytest.approx(90.0)

    # Bar 2: low slams 89 → original SL fires at 90, exit_reason stop_loss, full -10*pv loss
    eng.backtest_engine.current_bar_index = 2
    eng._process_subbar_fills(_bar(t0, 100, 100, 89.0, 89.5), tick_size=0.25)
    assert "MNQ" not in eng.backtest_engine.positions
    trade = eng.backtest_engine.trades[0]
    assert trade.exit_reason == "stop_loss"
    assert trade.exit_price == pytest.approx(90.0)
    assert trade.pnl == pytest.approx(-20.0, abs=0.01)  # 10 pts × 2.0 point_value


def test_breakeven_same_bar_ambiguity_defers_move_and_keeps_original_sl():
    """**Same-bar ambiguity policy**: when a bar's range covers BOTH the BE-trigger
    level (high for LONG) AND the proposed moved-SL level (entry+offset) on the
    same bar, the BE move is **deferred** — the original SL stays in place for
    the duration of this bar.

    Without 1m sub-bar resolution we don't know whether the high (trigger) preceded
    the low (would-test moved SL) or the other way around. The previous logic
    snapped the SL to ``entry`` *before* the per-bar fill check ran, which then
    fired the moved SL on the same bar's low → trade reported as a clean
    ``breakeven`` exit at ~$0. In practice — see the 2026-05-26 MNQ chart the
    user shared — that often misrepresents large impulsive bars that simply
    punched through both levels without ever giving the BE machinery a chance to
    arm in real time.

    New behaviour on bar 1 (entry=100, original SL=90, thr=5, offset=0, bar=
    (101,106,95,96)):
      • bar.high=106 ≥ entry+thr=105 → trigger condition met
      • bar.low=95 ≤ new_stop=100   → **ambiguous, defer the move**
      • original SL stays at 90 — bar.low=95 > 90 so it doesn't fire either
      • position still open at end of bar 1, watch still armed (``triggered``=False)

    The legacy regression target (the placement_price guard from 2026-03-30 MNQ)
    is now exercised by ``test_breakeven_clean_bar_moved_stop_fills_when_close_below_new_stop``
    below, which uses a bar whose low stays above the moved-SL level so the BE
    move is not deferred.
    """
    eng = _make_replay_engine()
    t0 = datetime(2026, 5, 21, 13, 0, tzinfo=timezone.utc)

    bar0 = _bar(t0, 99, 101, 99, 100.5)
    entry_oid = _seed_long_entry(eng, entry=100.0, stop=90.0, tp=120.0, bar=bar0)
    eng._simulate_register_breakeven_watch(
        entry_oid, symbol="MNQ", side="BUY",
        entry_price=100.0, profit_threshold=5.0,
    )
    eng._process_subbar_fills(bar0, tick_size=0.25)
    sl_oid = eng._breakeven_watches[entry_oid]["sl_order_id"]

    # Bar 1: high triggers BE *and* low reaches the moved-SL level → ambiguous.
    eng.backtest_engine.current_bar_index = 1
    bar1 = _bar(t0, 101, 106.0, 95.0, 96.0)
    eng._process_subbar_fills(bar1, tick_size=0.25)

    # Position is still open with the ORIGINAL stop level (90) intact; no trade closed.
    assert "MNQ" in eng.backtest_engine.positions
    assert eng.backtest_engine.positions["MNQ"].stop_loss == pytest.approx(90.0)
    assert eng.backtest_engine.trades == []
    # Watch is NOT triggered — it stays armed so the next bar can re-evaluate.
    watch = eng._breakeven_watches[entry_oid]
    assert watch["triggered"] is False
    sl = next(o for o in eng.backtest_engine.pending_orders if o.order_id == sl_oid)
    assert sl.stop_price == pytest.approx(90.0)
    assert sl.exit_reason in (None, "stop_loss")  # still the original SL semantics


def test_breakeven_same_bar_ambiguity_lets_original_sl_fire_at_real_loss():
    """When the ambiguous bar's low *also* tags the original SL, the deferral
    means the engine fills the original SL as a normal ``stop_loss`` — at the
    original SL price, NOT at the moved ``entry`` price. This is the user's
    headline ask: large reversal bars should NOT be recorded as flat
    ``breakeven`` exits.

    Setup mirrors the 2026-05-26 MNQ screenshot: bar = (open above entry,
    high spikes well above entry+thr, low collapses through the original SL,
    close below original SL).
    """
    eng = _make_replay_engine()
    t0 = datetime(2026, 5, 21, 13, 0, tzinfo=timezone.utc)

    bar0 = _bar(t0, 99, 101, 99, 100.5)
    entry_oid = _seed_long_entry(eng, entry=100.0, stop=90.0, tp=120.0, bar=bar0)
    eng._simulate_register_breakeven_watch(
        entry_oid, symbol="MNQ", side="BUY",
        entry_price=100.0, profit_threshold=5.0,
    )
    eng._process_subbar_fills(bar0, tick_size=0.25)

    # Bar 1: high=110 (BE trigger), low=85 (BELOW original SL=90), close=86.
    # Under the old code: BE moves first → SL@100 → fill at 100 → "breakeven" at $0.
    # Under the new code: BE deferred (ambiguous), original SL@90 fires normally
    # → ``stop_loss`` at 90 → -$20 (10 pts × $2 point value).
    eng.backtest_engine.current_bar_index = 1
    eng._process_subbar_fills(_bar(t0, 101, 110.0, 85.0, 86.0), tick_size=0.25)

    assert "MNQ" not in eng.backtest_engine.positions
    assert len(eng.backtest_engine.trades) == 1
    trade = eng.backtest_engine.trades[0]
    assert trade.exit_reason == "stop_loss"
    assert trade.exit_price == pytest.approx(90.0)
    assert trade.pnl == pytest.approx(-20.0, abs=0.01)


def test_breakeven_deferral_allows_clean_move_on_next_bar():
    """After a deferral, the watch stays armed. A subsequent CLEAN bar (high
    crosses trigger, low stays above the moved-SL level) snaps the SL as usual.
    """
    eng = _make_replay_engine()
    t0 = datetime(2026, 5, 21, 13, 0, tzinfo=timezone.utc)

    bar0 = _bar(t0, 99, 101, 99, 100.5)
    entry_oid = _seed_long_entry(eng, entry=100.0, stop=90.0, tp=120.0, bar=bar0)
    eng._simulate_register_breakeven_watch(
        entry_oid, symbol="MNQ", side="BUY",
        entry_price=100.0, profit_threshold=5.0,
    )
    eng._process_subbar_fills(bar0, tick_size=0.25)
    sl_oid = eng._breakeven_watches[entry_oid]["sl_order_id"]

    # Bar 1: ambiguous → defer (low=95 ≤ new_stop=100).
    eng.backtest_engine.current_bar_index = 1
    eng._process_subbar_fills(_bar(t0, 101, 106.0, 95.0, 96.0), tick_size=0.25)
    assert eng._breakeven_watches[entry_oid]["triggered"] is False

    # Bar 2: CLEAN BE bar (high=107 ≥ trigger=105, low=103 > new_stop=100). Snap fires.
    eng.backtest_engine.current_bar_index = 2
    eng._process_subbar_fills(_bar(t0, 105, 107.0, 103.0, 106.0), tick_size=0.25)
    assert eng._breakeven_watches[entry_oid]["triggered"] is True
    sl = next(o for o in eng.backtest_engine.pending_orders if o.order_id == sl_oid)
    assert sl.stop_price == pytest.approx(100.0)
    assert eng.backtest_engine.positions["MNQ"].stop_loss == pytest.approx(100.0)


def test_breakeven_clean_bar_moved_stop_fills_when_close_below_new_stop():
    """Regression for the 2026-03-30 ``placement_price`` guard (preserved coverage
    after the same-bar ambiguity rule was added).

    Uses a CLEAN bar where the moved-SL level isn't touched (low > new_stop on
    the trigger bar) so the BE move actually fires, then proves the moved SL
    fills on the NEXT bar even when that bar's close is below the new stop.

    Old buggy behaviour anchored ``placement_price`` to ``bar_close``, which
    made the engine treat a moved SELL STOP as wrong-side (``placement_price
    < stop_price``) and silently rejected the fill — trade then timed out at
    ``max_hold_bars`` instead of exiting at ~$0.
    """
    eng = _make_replay_engine()
    t0 = datetime(2026, 5, 21, 13, 0, tzinfo=timezone.utc)

    bar0 = _bar(t0, 99, 101, 99, 100.5)
    entry_oid = _seed_long_entry(eng, entry=100.0, stop=90.0, tp=120.0, bar=bar0)
    eng._simulate_register_breakeven_watch(
        entry_oid, symbol="MNQ", side="BUY",
        entry_price=100.0, profit_threshold=5.0,
    )
    eng._process_subbar_fills(bar0, tick_size=0.25)
    sl_oid = eng._breakeven_watches[entry_oid]["sl_order_id"]

    # Bar 1: CLEAN move — high=106 ≥ trigger=105, low=101 > new_stop=100. BE snaps.
    eng.backtest_engine.current_bar_index = 1
    eng._process_subbar_fills(_bar(t0, 102, 106.0, 101.0, 104.0), tick_size=0.25)
    assert eng._breakeven_watches[entry_oid]["triggered"] is True
    sl = next(o for o in eng.backtest_engine.pending_orders if o.order_id == sl_oid)
    assert sl.stop_price == pytest.approx(100.0)
    assert sl.placement_price == pytest.approx(100.0)  # the 2026-03-30 guard

    # Bar 2: gap-down to 96 close → low=95 dips BELOW the moved SL=100. The bar's
    # close (96) is below the new stop too — old code rejected this fill via the
    # placement_price < stop_price check. Now it must fire.
    eng.backtest_engine.current_bar_index = 2
    eng._process_subbar_fills(_bar(t0, 99, 99, 95.0, 96.0), tick_size=0.25)
    assert "MNQ" not in eng.backtest_engine.positions
    trade = eng.backtest_engine.trades[0]
    assert trade.exit_reason == "breakeven"
    assert trade.exit_price == pytest.approx(100.0)
    assert trade.pnl == pytest.approx(0.0, abs=0.01)


def test_breakeven_same_bar_ambiguity_defers_short_side():
    """SHORT mirror of the ambiguity guard.

    For a SHORT: trigger = ``entry - bar.low ≥ thr``, new_stop = ``entry - offset``,
    ambiguous when ``bar.high ≥ new_stop``.

    Setup: entry=100, original SL=110, thr=5, offset=0, bar=(99,105,90,91).
      • bar.low=90 ≤ entry-thr=95 → trigger condition met
      • bar.high=105 ≥ new_stop=100 → ambiguous → defer
      • original SL=110, bar.high=105 < 110 → original SL doesn't fire
      • trade stays open with original SL=110, watch still armed
    """
    eng = _make_replay_engine()
    t0 = datetime(2026, 5, 21, 13, 0, tzinfo=timezone.utc)

    bar0 = _bar(t0, 101, 102, 99.5, 100.0)
    entry_oid = _seed_short_entry(eng, entry=100.0, stop=110.0, tp=80.0, bar=bar0)
    eng._simulate_register_breakeven_watch(
        entry_oid, symbol="MNQ", side="SELL",
        entry_price=100.0, profit_threshold=5.0,
    )
    eng._process_subbar_fills(bar0, tick_size=0.25)
    sl_oid = eng._breakeven_watches[entry_oid]["sl_order_id"]

    eng.backtest_engine.current_bar_index = 1
    eng._process_subbar_fills(_bar(t0, 99, 105, 90, 91), tick_size=0.25)

    assert "MNQ" in eng.backtest_engine.positions
    assert eng.backtest_engine.positions["MNQ"].stop_loss == pytest.approx(110.0)
    assert eng._breakeven_watches[entry_oid]["triggered"] is False
    sl = next(o for o in eng.backtest_engine.pending_orders if o.order_id == sl_oid)
    assert sl.stop_price == pytest.approx(110.0)


def test_breakeven_skipped_on_entry_bar_to_avoid_lookahead():
    """Even if bar.high already exceeds entry+thr ON THE FILL BAR, we MUST NOT
    move the stop — that would be look-ahead because we don't know whether the
    high happened before or after the entry stop fired."""
    eng = _make_replay_engine()
    t0 = datetime(2026, 5, 21, 13, 0, tzinfo=timezone.utc)

    # Same bar both fills entry @ 100 AND prints high=110 (>> entry+5)
    bar0 = _bar(t0, 99, 110, 99, 102)
    entry_oid = _seed_long_entry(eng, entry=100.0, stop=90.0, tp=120.0, bar=bar0)
    eng._simulate_register_breakeven_watch(
        entry_oid, symbol="MNQ", side="BUY",
        entry_price=100.0, profit_threshold=5.0,
    )
    eng._process_subbar_fills(bar0, tick_size=0.25)

    # Position open, watch linked, but NOT yet triggered (entry bar skipped).
    assert eng._breakeven_watches[entry_oid]["triggered"] is False
    sl_oid = eng._breakeven_watches[entry_oid]["sl_order_id"]
    sl = next(o for o in eng.backtest_engine.pending_orders if o.order_id == sl_oid)
    assert sl.stop_price == pytest.approx(90.0)


def test_breakeven_state_isolated_between_replays():
    """``replay()`` clears ``_breakeven_watches`` so back-to-back walk-forward folds
    don't carry stale watches. We exercise the clear path directly here."""
    eng = _make_replay_engine()
    eng._simulate_register_breakeven_watch(
        "ENT_OLD", symbol="MNQ", side="BUY",
        entry_price=100.0, profit_threshold=5.0,
    )
    assert "ENT_OLD" in eng._breakeven_watches
    # Mimic the start of a fresh replay() call — this is what replay() does after
    # the symbol-state setup.
    eng._breakeven_watches.clear()
    assert eng._breakeven_watches == {}


def test_breakeven_watch_dropped_when_position_already_closed():
    """If TP fires before BE threshold ever hits, the watch should be silently
    pruned the next time ``_process_subbar_fills`` looks at it."""
    eng = _make_replay_engine()
    t0 = datetime(2026, 5, 21, 13, 0, tzinfo=timezone.utc)

    bar0 = _bar(t0, 99, 101, 99, 100.5)
    entry_oid = _seed_long_entry(eng, entry=100.0, stop=90.0, tp=120.0, bar=bar0)
    eng._simulate_register_breakeven_watch(
        entry_oid, symbol="MNQ", side="BUY",
        entry_price=100.0, profit_threshold=50.0,  # huge thr → never triggers
    )
    eng._process_subbar_fills(bar0, tick_size=0.25)
    assert entry_oid in eng._breakeven_watches

    # Bar 1: high tags TP at 120 → position closes via take_profit
    eng.backtest_engine.current_bar_index = 1
    eng._process_subbar_fills(_bar(t0, 101, 121, 100, 120.5), tick_size=0.25)
    assert "MNQ" not in eng.backtest_engine.positions
    assert eng.backtest_engine.trades[-1].exit_reason == "take_profit"

    # Bar 2: any further bar should sweep the stale watch even though it never triggered.
    eng.backtest_engine.current_bar_index = 2
    eng._process_subbar_fills(_bar(t0, 120, 122, 119, 121), tick_size=0.25)
    assert entry_oid not in eng._breakeven_watches


def test_intercept_swaps_register_breakeven_method_during_replay():
    """``_intercept_trading_bot_methods`` should redirect the bot's
    ``register_generic_breakeven_watch`` to the simulated path; restore puts it back."""
    eng = _make_replay_engine()
    sentinel = MagicMock(name="real_register")
    eng.trading_bot.register_generic_breakeven_watch = sentinel

    eng._intercept_trading_bot_methods()
    # Bound methods don't compare with ``is`` (each attribute access produces a fresh
    # bound-method object). Compare by descriptor function + instance instead.
    swapped = eng.trading_bot.register_generic_breakeven_watch
    assert swapped.__func__ is StrategyReplayEngine._simulate_register_breakeven_watch
    assert swapped.__self__ is eng

    eng._restore_trading_bot_methods()
    assert eng.trading_bot.register_generic_breakeven_watch is sentinel


def test_simulate_place_bracket_order_registers_breakeven_watch():
    """Regression: ``_simulate_place_bracket_order`` replaces the live
    ``BaseStrategy.place_bracket_order``, but originally it never called
    ``register_generic_breakeven_watch`` — meaning every backtest watch was
    silently dropped on the floor and ``_breakeven_watches`` stayed empty for
    the entire replay. This guards against regressing back to that state.
    """
    eng = _make_replay_engine()
    eng.backtest_engine.set_last_close(99.0)

    res = asyncio.run(eng._simulate_place_bracket_order(
        symbol="MNQ", side="BUY", quantity=1,
        entry_price=100.0, stop_loss_price=90.0, take_profit_price=120.0,
        breakeven_profit_threshold=5.0,
        strategy_name="morning_range_reversion",
    ))
    assert res["success"] is True
    entry_oid = res["orderId"]
    assert entry_oid in eng._breakeven_watches
    w = eng._breakeven_watches[entry_oid]
    assert w["side"] == "LONG"
    assert w["entry_price"] == pytest.approx(100.0)
    assert w["profit_threshold"] == pytest.approx(5.0)
    assert w["triggered"] is False


def test_simulate_place_bracket_order_skips_watch_when_threshold_zero():
    """No watch should be armed when the strategy passes ``breakeven_profit_threshold=None``
    or 0 — that's the disabled state, and arming a 0-threshold watch would fire
    at the very first favourable tick."""
    eng = _make_replay_engine()
    eng.backtest_engine.set_last_close(99.0)

    res = asyncio.run(eng._simulate_place_bracket_order(
        symbol="MNQ", side="BUY", quantity=1,
        entry_price=100.0, stop_loss_price=90.0, take_profit_price=120.0,
        breakeven_profit_threshold=None,  # disabled
    ))
    assert res["success"] is True
    assert eng._breakeven_watches == {}

    res2 = asyncio.run(eng._simulate_place_bracket_order(
        symbol="MES", side="SELL", quantity=1,
        entry_price=200.0, stop_loss_price=210.0, take_profit_price=180.0,
        breakeven_profit_threshold=0.0,  # zero → also disabled
    ))
    assert res2["success"] is True
    assert eng._breakeven_watches == {}


def test_breakeven_watch_triggers_in_realistic_two_bar_sequence_pnl_positive():
    """Sanity: when MFE crosses thr but price never returns to entry, the trade
    eventually hits TP — breakeven should be a no-op on PnL because the moved
    stop just sits there harmlessly."""
    eng = _make_replay_engine()
    t0 = datetime(2026, 5, 21, 13, 0, tzinfo=timezone.utc)

    bar0 = _bar(t0, 99, 101, 99, 100.5)
    entry_oid = _seed_long_entry(eng, entry=100.0, stop=90.0, tp=120.0, bar=bar0)
    eng._simulate_register_breakeven_watch(
        entry_oid, symbol="MNQ", side="BUY",
        entry_price=100.0, profit_threshold=5.0,
    )
    eng._process_subbar_fills(bar0, tick_size=0.25)

    # Bar 1: high reaches 110 → BE triggers
    eng.backtest_engine.current_bar_index = 1
    eng._process_subbar_fills(_bar(t0, 101, 110, 101, 109), tick_size=0.25)
    assert eng._breakeven_watches[entry_oid]["triggered"] is True

    # Bar 2: high tags TP at 120 — position closes profitably, no SL miss
    eng.backtest_engine.current_bar_index = 2
    eng._process_subbar_fills(_bar(t0, 110, 121, 109, 119), tick_size=0.25)
    trade = eng.backtest_engine.trades[-1]
    assert trade.exit_reason == "take_profit"
    assert trade.pnl == pytest.approx(40.0, abs=0.01)  # 20 pts × 2.0 point_value


# ── breakeven_offset (position_management.breakeven_offset) ─────────────────


def test_register_breakeven_watch_stores_offset_in_row():
    """Watch row carries the normalized ``breakeven_offset`` alongside ``profit_threshold``."""
    eng = _make_replay_engine()
    eng._simulate_register_breakeven_watch(
        "ENT_O1", symbol="MNQ", side="BUY",
        entry_price=100.0, profit_threshold=5.0,
        breakeven_offset=2.0,
    )
    w = eng._breakeven_watches["ENT_O1"]
    assert w["breakeven_offset"] == pytest.approx(2.0)


def test_register_breakeven_watch_clamps_negative_offset_to_zero():
    """A negative ``breakeven_offset`` would tighten the stop into a loss — clamp it to 0."""
    eng = _make_replay_engine()
    eng._simulate_register_breakeven_watch(
        "ENT_O2", symbol="MNQ", side="BUY",
        entry_price=100.0, profit_threshold=5.0,
        breakeven_offset=-3.5,
    )
    assert eng._breakeven_watches["ENT_O2"]["breakeven_offset"] == 0.0


def test_register_breakeven_watch_defaults_offset_to_zero_when_missing():
    """Backward-compat: callers not passing ``breakeven_offset`` get the legacy snap-to-entry behaviour."""
    eng = _make_replay_engine()
    eng._simulate_register_breakeven_watch(
        "ENT_O3", symbol="MNQ", side="BUY",
        entry_price=100.0, profit_threshold=5.0,
    )
    assert eng._breakeven_watches["ENT_O3"]["breakeven_offset"] == 0.0


def test_breakeven_long_with_offset_snaps_sl_above_entry_and_locks_profit():
    """LONG: ``breakeven_offset=3`` → moved SL = entry+3.

    Reversal bar that prints low=99 still leaves the moved SL un-triggered (103 > 99),
    so the next bar dipping to 102 fires the SL at 103 for a small PROFIT — exactly
    the "lock in a couple ticks above commission" use case the user asked for.
    """
    eng = _make_replay_engine()
    t0 = datetime(2026, 5, 21, 13, 0, tzinfo=timezone.utc)

    bar0 = _bar(t0, 99, 101, 99, 100.5)
    entry_oid = _seed_long_entry(eng, entry=100.0, stop=90.0, tp=120.0, bar=bar0)
    eng._simulate_register_breakeven_watch(
        entry_oid, symbol="MNQ", side="BUY",
        entry_price=100.0, profit_threshold=5.0,
        breakeven_offset=3.0,
    )
    eng._process_subbar_fills(bar0, tick_size=0.25)
    sl_oid = eng._breakeven_watches[entry_oid]["sl_order_id"]

    # Bar 1: high=106 (≥ entry+thr=105) **and** low=104 stays above entry+offset=103,
    # so the moved SL arms without firing same-bar.
    eng.backtest_engine.current_bar_index = 1
    eng._process_subbar_fills(_bar(t0, 105, 106, 104, 105), tick_size=0.25)
    watch = eng._breakeven_watches[entry_oid]
    assert watch["triggered"] is True
    sl = next(o for o in eng.backtest_engine.pending_orders if o.order_id == sl_oid)
    assert sl.stop_price == pytest.approx(103.0)
    assert eng.backtest_engine.positions["MNQ"].stop_loss == pytest.approx(103.0)

    # Bar 2: low slips to 102 → moved SL at 103 fires for a +$6 win (3 pts × $2/pt).
    eng.backtest_engine.current_bar_index = 2
    eng._process_subbar_fills(_bar(t0, 105, 105, 102, 102.5), tick_size=0.25)
    assert "MNQ" not in eng.backtest_engine.positions
    trade = eng.backtest_engine.trades[-1]
    assert trade.exit_reason == "breakeven"
    assert trade.exit_price == pytest.approx(103.0)
    assert trade.pnl == pytest.approx(6.0, abs=0.01)


def test_breakeven_short_with_offset_snaps_sl_below_entry_and_locks_profit():
    """SHORT mirror: ``breakeven_offset=3`` → moved SL = entry-3 → profit on stop-out."""
    eng = _make_replay_engine()
    t0 = datetime(2026, 5, 21, 13, 0, tzinfo=timezone.utc)

    bar0 = _bar(t0, 101, 102, 99.5, 100.0)
    entry_oid = _seed_short_entry(eng, entry=100.0, stop=110.0, tp=80.0, bar=bar0)
    eng._simulate_register_breakeven_watch(
        entry_oid, symbol="MNQ", side="SELL",
        entry_price=100.0, profit_threshold=5.0,
        breakeven_offset=3.0,
    )
    eng._process_subbar_fills(bar0, tick_size=0.25)
    sl_oid = eng._breakeven_watches[entry_oid]["sl_order_id"]

    # Bar 1: low=94 (≤ entry-thr=95) **and** high=96 stays below entry-offset=97 → moved SL arms.
    eng.backtest_engine.current_bar_index = 1
    eng._process_subbar_fills(_bar(t0, 95, 96, 94, 95), tick_size=0.25)
    watch = eng._breakeven_watches[entry_oid]
    assert watch["triggered"] is True
    sl = next(o for o in eng.backtest_engine.pending_orders if o.order_id == sl_oid)
    assert sl.stop_price == pytest.approx(97.0)
    assert eng.backtest_engine.positions["MNQ"].stop_loss == pytest.approx(97.0)

    # Bar 2: price bounces to 98 → moved SL at 97 fires (SHORT → BUY STOP triggers
    # when high crosses up through stop). +$6 PnL (3 pts × $2/pt).
    eng.backtest_engine.current_bar_index = 2
    eng._process_subbar_fills(_bar(t0, 95, 98, 95, 97.5), tick_size=0.25)
    assert "MNQ" not in eng.backtest_engine.positions
    trade = eng.backtest_engine.trades[-1]
    assert trade.exit_reason == "breakeven"
    assert trade.exit_price == pytest.approx(97.0)
    assert trade.pnl == pytest.approx(6.0, abs=0.01)


def test_breakeven_offset_zero_preserves_snap_to_entry_behaviour():
    """Regression: ``breakeven_offset=0`` (the default) must keep the legacy entry-snap.

    Same fixture as ``test_breakeven_triggers_and_moves_long_stop_to_entry`` to prove
    the offset wiring didn't accidentally shift the default. Moved SL must equal
    ``entry_price`` (100.0), not something like ``entry + ε``.
    """
    eng = _make_replay_engine()
    t0 = datetime(2026, 5, 21, 13, 0, tzinfo=timezone.utc)

    bar0 = _bar(t0, 99, 101, 99, 100.5)
    entry_oid = _seed_long_entry(eng, entry=100.0, stop=90.0, tp=120.0, bar=bar0)
    eng._simulate_register_breakeven_watch(
        entry_oid, symbol="MNQ", side="BUY",
        entry_price=100.0, profit_threshold=5.0,
        breakeven_offset=0.0,
    )
    eng._process_subbar_fills(bar0, tick_size=0.25)
    sl_oid = eng._breakeven_watches[entry_oid]["sl_order_id"]

    eng.backtest_engine.current_bar_index = 1
    eng._process_subbar_fills(_bar(t0, 100.5, 106.0, 102.0, 105.0), tick_size=0.25)
    sl = next(o for o in eng.backtest_engine.pending_orders if o.order_id == sl_oid)
    assert sl.stop_price == pytest.approx(100.0)


def test_simulate_place_bracket_order_forwards_breakeven_offset_to_watch():
    """Regression for the plumbing: when the strategy passes ``breakeven_offset`` to
    ``place_bracket_order``, the simulated bracket helper must propagate it onto the
    watch row so ``_evaluate_breakeven_watches`` can apply it at trigger time."""
    eng = _make_replay_engine()
    eng.backtest_engine.set_last_close(99.0)

    res = asyncio.run(eng._simulate_place_bracket_order(
        symbol="MNQ", side="BUY", quantity=1,
        entry_price=100.0, stop_loss_price=90.0, take_profit_price=120.0,
        breakeven_profit_threshold=5.0,
        breakeven_offset=2.5,
        strategy_name="morning_range_reversion",
    ))
    entry_oid = res["orderId"]
    assert entry_oid in eng._breakeven_watches
    assert eng._breakeven_watches[entry_oid]["breakeven_offset"] == pytest.approx(2.5)


# ── Bracket-exit placement_price direction-guard regression ─────────────────
#
# Reproduces the 2026-03-04 MNQ SHORT-timeout user reported on 2026-05-26.
# Failure mode: threshold-mode SHORT enters via SELL STOP on a bar whose
# CLOSE has already bounced back above the SL level. The bracket SL exit
# inherits ``placement_price = bar.close`` from ``BacktestEngine.last_close``,
# which trips ``_check_order_fill``'s "wrong-side BUY STOP" guard
# (``pp > stop_price`` → reject). Result: the SL silently never fires, the
# position rides ``max_hold_bars`` to a timeout exit hundreds of points later
# (the reported −$1,839.75 on 3 contracts vs the ~−$130/contract a clean SL
# would have produced).
#
# Fix: anchor each bracket exit's ``placement_price`` to its own
# ``stop_price`` / ``limit_price`` so the direction-guard check is a no-op
# (``pp == stop_price`` makes the strict inequality false).


def test_bracket_short_sl_fires_when_entry_bar_close_runs_above_sl_level():
    """The 2026-03-04 MNQ regression. A SHORT bracket whose entry bar closes
    ABOVE the SL level must still let the SL fire on a subsequent bar that
    tags ``stop_price``. The pre-fix code rejected the bracket SL because
    ``placement_price`` was ``bar.close`` (= 24950) and ``stop_price`` was the
    SL level (= 24900), tripping the BUY STOP wrong-side guard.
    """
    eng = _make_replay_engine()
    t0 = datetime(2026, 3, 4, 14, 25, tzinfo=timezone.utc)  # 09:25 ET, signal bar

    # ── Signal bar (sweep_close): strategy places the entry SELL STOP at trigger.
    # Bracket children are stored as metadata; they don't go live until the entry
    # fills. Sweep_close fired around 24850, so set last_close there.
    eng.backtest_engine.set_last_close(24850.0)
    entry_trigger = 24837.625
    sl_level = 24900.125  # entry + 62.5 (matching 90d ModeB recap's 62.5 pt risk)
    tp_level = 24775.125  # entry − 62.5 (1:1 target)
    entry_oid = eng.backtest_engine.place_order(
        symbol="MNQ", side=OrderSide.SELL, quantity=1, order_type=OrderType.STOP,
        stop_price=entry_trigger, price=entry_trigger,
    )
    for o in eng.backtest_engine.pending_orders:
        if o.order_id == entry_oid:
            o.stop_loss_price = sl_level
            o.take_profit_price = tp_level
            o.custom_tag = "TB-stop_bracket-morning_range_reversion-replay"
            break

    # ── Entry bar (~09:30 ET): wick down to the trigger (low=24837) but CLOSE
    # has already bounced sharply above the SL level (close=24950). Without the
    # placement_price anchor the bracket SL inherits pp=24950 and is rejected as
    # "wrong-side BUY STOP" forever after.
    eng.backtest_engine.current_bar_index = 1
    entry_bar = _bar(t0, 24850.0, 24960.0, 24830.0, 24950.0)
    eng._process_subbar_fills(entry_bar, tick_size=0.25)

    # Entry filled, position is open SHORT at 24837.625
    assert "MNQ" in eng.backtest_engine.positions
    pos = eng.backtest_engine.positions["MNQ"]
    assert pos.side == OrderSide.SELL
    assert pos.entry_price == pytest.approx(entry_trigger)

    # The bracket SL exit must have been placed with ``placement_price == stop_price``
    # so the direction guard treats it as neutral, not wrong-side.
    sl_order = next(
        (o for o in eng.backtest_engine.pending_orders
         if o.stop_price == pytest.approx(sl_level)
         and o.order_type == OrderType.STOP
         and o.side == OrderSide.BUY),
        None,
    )
    assert sl_order is not None, "Bracket SL exit was not placed"
    assert sl_order.placement_price == pytest.approx(sl_level), (
        f"placement_price should anchor to stop_price ({sl_level}), "
        f"got {sl_order.placement_price} (likely fell back to last_close=bar.close)."
    )

    # ── Next bar: bar.high pushes through the SL level. The SL must fire and
    # close the position as a real ``stop_loss`` exit at ~24900.125, not get
    # silently rejected by the direction guard.
    eng.backtest_engine.current_bar_index = 2
    eng._process_subbar_fills(_bar(t0, 24950.0, 25010.0, 24945.0, 25000.0), tick_size=0.25)

    assert "MNQ" not in eng.backtest_engine.positions, (
        "SL must fire when bar.high >= sl_level; if it didn't, the direction "
        "guard rejected the bracket child for being on the 'wrong side' of last_close."
    )
    assert len(eng.backtest_engine.trades) == 1
    trade = eng.backtest_engine.trades[0]
    assert trade.exit_reason == "stop_loss"
    assert trade.exit_price == pytest.approx(sl_level)
    # PnL ≈ (entry − sl) × point_value × qty = (24837.625 − 24900.125) × 2 × 1 = −$125
    assert trade.pnl == pytest.approx(-125.0, abs=0.5)


def test_bracket_long_sl_fires_when_entry_bar_close_runs_below_sl_level():
    """Mirror of the SHORT regression: a LONG bracket whose entry bar reverses
    sharply below the SL level (close < sl_level for a LONG → pp < sl_stop_price)
    used to trip the SELL STOP wrong-side guard (``pp < stop_price`` → reject).
    With the placement_price anchored to the SL level itself, the SL fires
    normally on the next bar's low.
    """
    eng = _make_replay_engine()
    t0 = datetime(2026, 3, 4, 14, 25, tzinfo=timezone.utc)

    eng.backtest_engine.set_last_close(24850.0)
    entry_trigger = 24862.375
    sl_level = 24799.875   # entry − 62.5
    tp_level = 24924.875   # entry + 62.5
    entry_oid = eng.backtest_engine.place_order(
        symbol="MNQ", side=OrderSide.BUY, quantity=1, order_type=OrderType.STOP,
        stop_price=entry_trigger, price=entry_trigger,
    )
    for o in eng.backtest_engine.pending_orders:
        if o.order_id == entry_oid:
            o.stop_loss_price = sl_level
            o.take_profit_price = tp_level
            o.custom_tag = "TB-stop_bracket-morning_range_reversion-replay"
            break

    # Entry bar: wick up to the trigger (high=24862) but close has plunged BELOW
    # the SL level (close=24795). Pre-fix the SELL STOP SL would inherit pp=24795
    # < stop_price=24799.875 → rejected as wrong-side forever.
    eng.backtest_engine.current_bar_index = 1
    entry_bar = _bar(t0, 24850.0, 24870.0, 24790.0, 24795.0)
    eng._process_subbar_fills(entry_bar, tick_size=0.25)

    assert "MNQ" in eng.backtest_engine.positions
    pos = eng.backtest_engine.positions["MNQ"]
    assert pos.side == OrderSide.BUY
    assert pos.entry_price == pytest.approx(entry_trigger)

    sl_order = next(
        (o for o in eng.backtest_engine.pending_orders
         if o.stop_price == pytest.approx(sl_level)
         and o.order_type == OrderType.STOP
         and o.side == OrderSide.SELL),
        None,
    )
    assert sl_order is not None
    assert sl_order.placement_price == pytest.approx(sl_level)

    # Next bar: low pierces the SL.
    eng.backtest_engine.current_bar_index = 2
    eng._process_subbar_fills(_bar(t0, 24795.0, 24798.0, 24780.0, 24785.0), tick_size=0.25)

    assert "MNQ" not in eng.backtest_engine.positions
    trade = eng.backtest_engine.trades[0]
    assert trade.exit_reason == "stop_loss"
    assert trade.exit_price == pytest.approx(sl_level)


def test_bracket_tp_placement_price_also_anchored_for_symmetry():
    """The LIMIT direction guard isn't enforced today but the placement_price
    must still be anchored to the limit_price for consistency. Documents the
    behaviour so a future LIMIT-side direction guard doesn't reintroduce the
    same silent-reject bug for TPs.
    """
    eng = _make_replay_engine()
    t0 = datetime(2026, 3, 4, 14, 25, tzinfo=timezone.utc)

    eng.backtest_engine.set_last_close(24850.0)
    entry_oid = eng.backtest_engine.place_order(
        symbol="MNQ", side=OrderSide.SELL, quantity=1, order_type=OrderType.STOP,
        stop_price=24837.625, price=24837.625,
    )
    for o in eng.backtest_engine.pending_orders:
        if o.order_id == entry_oid:
            o.stop_loss_price = 24900.125
            o.take_profit_price = 24775.125
            o.custom_tag = "TB-stop_bracket-test-replay"
            break

    eng.backtest_engine.current_bar_index = 1
    eng._process_subbar_fills(_bar(t0, 24850.0, 24960.0, 24830.0, 24950.0), tick_size=0.25)

    tp_order = next(
        (o for o in eng.backtest_engine.pending_orders
         if o.limit_price == pytest.approx(24775.125)
         and o.order_type == OrderType.LIMIT),
        None,
    )
    assert tp_order is not None
    assert tp_order.placement_price == pytest.approx(24775.125)
