"""Tests for the live consec-loss breaker bridge.

Covers the wiring between ``EventType.TRADE_CLOSED`` events (published by
``core/user_hub_handlers.on_trade`` when ``SessionTradeTracker`` finishes
matching a fill pair) and the per-strategy
``_live_trade_history`` buffer that
``MorningRangeReversionStrategy._consec_loss_breaker_status`` walks in
live mode.

Backtest parity is **not** retested here — the backtest path reads
``self._replay_engine.trades`` directly and is pinned by
``tests/test_morning_range_consec_loss_breaker_*`` files.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from core.events import Event, EventType
from core.live_trade_history import (
    LiveTradeHistory,
    LiveTradeRecord,
    live_breaker_default_enabled,
    trade_record_from_event,
)


# ── LiveTradeHistory unit tests ────────────────────────────────────────────


def test_record_and_walk_most_recent_first():
    h = LiveTradeHistory(max_per_symbol=5)
    h.record(LiveTradeRecord(symbol="mnq", pnl=-50.0, entry_time=None, exit_time=None, side="LONG"))
    h.record(LiveTradeRecord(symbol="MNQ", pnl=-30.0, entry_time=None, exit_time=None, side="SHORT"))
    h.record(LiveTradeRecord(symbol="MGC", pnl=+100.0, entry_time=None, exit_time=None, side="LONG"))
    mnq = h.trades_for("MNQ")
    assert [t.pnl for t in mnq] == [-30.0, -50.0]
    mgc = h.trades_for("MGC")
    assert len(mgc) == 1 and mgc[0].pnl == 100.0
    # Symbol normalisation: lower-case input still walks under upper key.
    assert h.trades_for("mnq") == h.trades_for("MNQ")


def test_max_per_symbol_eviction():
    h = LiveTradeHistory(max_per_symbol=3)
    for i in range(5):
        h.record(LiveTradeRecord(symbol="MNQ", pnl=-float(i), entry_time=None, exit_time=None, side="LONG"))
    walked = h.trades_for("MNQ")
    # Most recent 3 of 5 (i=2,3,4) survive, ordered most-recent-first.
    assert [t.pnl for t in walked] == [-4.0, -3.0, -2.0]


def test_trade_record_from_event_minimal():
    rec = trade_record_from_event(
        {
            "symbol": "mnq",
            "net_pnl": -42.5,
            "side": "LONG",
            "exit_time": "2026-05-30T14:30:00+00:00",
            "trade_id": "abc",
        }
    )
    assert rec is not None
    assert rec.symbol == "MNQ"
    assert rec.pnl == -42.5
    assert rec.side == "LONG"
    assert rec.exit_time.isoformat() == "2026-05-30T14:30:00+00:00"
    assert rec.trade_id == "abc"


def test_trade_record_from_event_drops_missing_symbol():
    assert trade_record_from_event({"net_pnl": -1.0}) is None
    # Bad pnl coerces gracefully.
    bad = trade_record_from_event({"symbol": "MNQ", "net_pnl": "oops"})
    assert bad is None


def test_default_env_off(monkeypatch):
    monkeypatch.delenv("STRATEGY_LIVE_BREAKER", raising=False)
    assert live_breaker_default_enabled() is False
    monkeypatch.setenv("STRATEGY_LIVE_BREAKER", "1")
    assert live_breaker_default_enabled() is True
    monkeypatch.setenv("STRATEGY_LIVE_BREAKER", "off")
    assert live_breaker_default_enabled() is False


# ── BaseStrategy.record_trade_outcome / event subscriber ───────────────────


class _StubBus:
    """Minimal event-bus mimic for testing subscribe/unsubscribe."""

    def __init__(self) -> None:
        self.subscribers: dict[EventType, list] = {}

    def subscribe(self, event_type: EventType, cb) -> None:
        self.subscribers.setdefault(event_type, []).append(cb)

    def unsubscribe(self, event_type: EventType, cb) -> None:
        subs = self.subscribers.get(event_type, [])
        if cb in subs:
            subs.remove(cb)


def _make_strategy_with_bus(symbols=("MNQ", "MGC")):
    """Build a minimal MorningRangeReversionStrategy with a stub bus."""
    from strategies.morning_range_reversion_strategy import MorningRangeReversionStrategy
    from strategies.strategy_base import StrategyConfig

    bus = _StubBus()
    bot = SimpleNamespace(event_bus=bus)
    cfg = StrategyConfig(
        name="morning_range_reversion",
        enabled=True,
        symbols=list(symbols),
        max_positions=2,
        position_size=1,
        risk_per_trade_percent=0.5,
        max_daily_trades=10,
        preferred_conditions=[],
        avoid_conditions=[],
        trading_start_time="07:00",
        trading_end_time="08:30",
        no_trade_start="15:30",
        no_trade_end="16:00",
    )
    strat = MorningRangeReversionStrategy(bot, cfg)
    return strat, bus


def test_record_trade_outcome_populates_live_history():
    strat, _bus = _make_strategy_with_bus()
    strat.record_trade_outcome(symbol="MNQ", pnl=-25.0, side="LONG")
    strat.record_trade_outcome(symbol="MGC", pnl=+15.0, side="SHORT")
    assert strat._live_trade_history is not None
    mnq = strat._live_trade_history.trades_for("MNQ")
    assert len(mnq) == 1 and mnq[0].pnl == -25.0
    assert strat._live_trade_history.trades_for("MGC")[0].pnl == 15.0


def test_subscribe_and_unsubscribe_to_trade_closed():
    strat, bus = _make_strategy_with_bus()
    asyncio.run(strat.start_live_trade_bridge())
    assert EventType.TRADE_CLOSED in bus.subscribers
    assert len(bus.subscribers[EventType.TRADE_CLOSED]) == 1
    asyncio.run(strat.stop_live_trade_bridge())
    assert bus.subscribers[EventType.TRADE_CLOSED] == []


def test_event_dispatch_writes_history_through_subscriber():
    strat, _bus = _make_strategy_with_bus()
    asyncio.run(strat.start_live_trade_bridge())
    evt = Event(
        type=EventType.TRADE_CLOSED,
        data={
            "symbol": "MNQ",
            "net_pnl": -55.0,
            "side": "LONG",
            "exit_time": "2026-05-29T13:45:00+00:00",
            "trade_id": "t1",
        },
        source="test",
    )
    asyncio.run(strat._on_trade_closed_event(evt))
    rec = strat._live_trade_history.trades_for("MNQ")
    assert len(rec) == 1
    assert rec[0].pnl == -55.0
    assert rec[0].trade_id == "t1"


def test_event_filters_by_strategy_symbols():
    strat, _bus = _make_strategy_with_bus(symbols=("MNQ",))
    asyncio.run(strat.start_live_trade_bridge())
    # MGC trade should be ignored — not in this strategy's symbols.
    evt = Event(
        type=EventType.TRADE_CLOSED,
        data={"symbol": "MGC", "net_pnl": -1000.0, "side": "LONG"},
        source="test",
    )
    asyncio.run(strat._on_trade_closed_event(evt))
    if strat._live_trade_history is not None:
        assert strat._live_trade_history.trades_for("MGC") == []
    # MNQ trade lands fine.
    evt2 = Event(
        type=EventType.TRADE_CLOSED,
        data={"symbol": "MNQ", "net_pnl": -10.0, "side": "LONG"},
        source="test",
    )
    asyncio.run(strat._on_trade_closed_event(evt2))
    assert strat._live_trade_history is not None
    assert len(strat._live_trade_history.trades_for("MNQ")) == 1


# ── _consec_loss_breaker_status: live-mode fallback ────────────────────────


def test_breaker_walks_live_history_when_no_replay_engine():
    """In live mode (no _replay_engine), the breaker reads _live_trade_history."""
    strat, _bus = _make_strategy_with_bus(symbols=("MNQ",))
    # Two consecutive MNQ losses → breaker should trip (max_consecutive_losses=2 default for MGC/MES
    # — but the symbol_override returns the root value otherwise).
    # We need MNQ-specific override OR root override.  For the test we monkeypatch the resolver.
    strat._max_consecutive_losses = lambda symbol: 2  # type: ignore[method-assign]
    strat._loss_streak_cooldown_sessions = lambda symbol: 10  # type: ignore[method-assign]
    strat._rolling_loss_threshold_dollars = lambda symbol: 0.0  # type: ignore[method-assign]
    strat._breaker_min_efficiency_ratio = lambda symbol: 0.0  # type: ignore[method-assign]
    # Record three losses in chronological order; most-recent walk picks up last two as the streak.
    base = datetime(2026, 5, 28, 14, 0, tzinfo=timezone.utc)
    strat.record_trade_outcome(symbol="MNQ", pnl=-20.0, side="LONG", exit_time=base)
    strat.record_trade_outcome(
        symbol="MNQ",
        pnl=-30.0,
        side="LONG",
        exit_time=datetime(2026, 5, 29, 14, 0, tzinfo=timezone.utc),
    )
    status = strat._consec_loss_breaker_status("MNQ", date(2026, 5, 30))
    assert status["blocked"] is True
    assert status["streak"] == 2
    assert status["reason"] == "trip_active"


def test_breaker_clears_when_winner_breaks_streak():
    strat, _bus = _make_strategy_with_bus(symbols=("MNQ",))
    strat._max_consecutive_losses = lambda symbol: 2  # type: ignore[method-assign]
    strat._loss_streak_cooldown_sessions = lambda symbol: 10  # type: ignore[method-assign]
    strat._rolling_loss_threshold_dollars = lambda symbol: 0.0  # type: ignore[method-assign]
    strat._breaker_min_efficiency_ratio = lambda symbol: 0.0  # type: ignore[method-assign]
    base = datetime(2026, 5, 28, 14, 0, tzinfo=timezone.utc)
    strat.record_trade_outcome(symbol="MNQ", pnl=-20.0, side="LONG", exit_time=base)
    strat.record_trade_outcome(symbol="MNQ", pnl=-30.0, side="LONG", exit_time=base)
    # Winner resets streak from the breaker's perspective (walk stops at winner).
    strat.record_trade_outcome(
        symbol="MNQ",
        pnl=+45.0,
        side="LONG",
        exit_time=datetime(2026, 5, 29, 14, 0, tzinfo=timezone.utc),
    )
    status = strat._consec_loss_breaker_status("MNQ", date(2026, 5, 30))
    assert status["blocked"] is False
    assert status["streak"] == 0
