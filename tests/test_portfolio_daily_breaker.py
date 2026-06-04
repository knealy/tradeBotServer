"""Tests for ``core.portfolio_daily_breaker``.

Test plan:
1. Session-date math (ET rollover at 18:00).
2. Pure ``evaluate()`` accumulation + trip detection.
3. Session rollover resets state.
4. Backtest path (`replay_evaluate`) walks BacktestTrade objects.
5. Live path: bus subscription + fire-once trip behaviour.

Modelled after ``tests/test_live_trade_breaker_bridge.py`` — uses a
``_StubBus`` so we don't need to spin up the real ``EventBus`` async
processor.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime, time
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from core.portfolio_daily_breaker import (
    PortfolioBreakerConfig,
    PortfolioBreakerState,
    PortfolioDailyBreaker,
    _PnlRecord,
    evaluate,
    session_date_for,
)


# ----------------------------- session-date math -----------------------------


def test_session_date_before_rollover():
    """A 14:00 ET timestamp belongs to its own calendar date."""
    ts = datetime(2026, 6, 2, 14, 0)
    assert session_date_for(ts) == date(2026, 6, 2)


def test_session_date_at_rollover():
    """18:00 ET is the boundary — exactly 18:00 belongs to NEXT day."""
    ts = datetime(2026, 6, 2, 18, 0)
    assert session_date_for(ts) == date(2026, 6, 3)


def test_session_date_after_rollover():
    """22:00 ET on Mon belongs to Tue's session."""
    ts = datetime(2026, 6, 2, 22, 30)
    assert session_date_for(ts) == date(2026, 6, 3)


def test_session_date_custom_reset_time():
    """Operator can pin a different reset time."""
    ts = datetime(2026, 6, 2, 16, 30)
    assert session_date_for(ts, reset_at_et=time(16, 0)) == date(2026, 6, 3)
    assert session_date_for(ts, reset_at_et=time(17, 0)) == date(2026, 6, 2)


# ----------------------------- pure evaluator -----------------------------


def _rec(hour: int, pnl: float, *, day: int = 2, sym: str = "MNQ") -> _PnlRecord:
    return _PnlRecord(
        exit_time_et=datetime(2026, 6, day, hour, 0),
        net_pnl=pnl,
        symbol=sym,
        source="test",
    )


def test_evaluate_accumulates_same_session_pnl():
    cfg = PortfolioBreakerConfig(daily_loss_cap_dollars=1000.0)
    state = PortfolioBreakerState()
    recs = [_rec(10, -200), _rec(11, -300), _rec(14, +100)]
    out = evaluate(records=recs, now_et=datetime(2026, 6, 2, 15, 0), config=cfg, state=state)
    assert out.realised_pnl_today == pytest.approx(-400)
    assert out.tripped is False
    assert out.session_date == date(2026, 6, 2)


def test_evaluate_trips_when_cap_breached():
    cfg = PortfolioBreakerConfig(daily_loss_cap_dollars=500.0)
    state = PortfolioBreakerState()
    recs = [_rec(10, -300), _rec(12, -250)]  # total -550 <= -500
    out = evaluate(records=recs, now_et=datetime(2026, 6, 2, 15, 0), config=cfg, state=state)
    assert out.tripped is True
    assert out.trip_pnl == pytest.approx(-550)
    assert out.trip_time_et == datetime(2026, 6, 2, 15, 0)


def test_evaluate_does_not_trip_when_just_under_cap():
    cfg = PortfolioBreakerConfig(daily_loss_cap_dollars=500.0)
    state = PortfolioBreakerState()
    recs = [_rec(10, -499.99)]
    out = evaluate(records=recs, now_et=datetime(2026, 6, 2, 15, 0), config=cfg, state=state)
    assert out.tripped is False


def test_evaluate_disabled_when_cap_is_zero():
    cfg = PortfolioBreakerConfig(daily_loss_cap_dollars=0.0)
    state = PortfolioBreakerState()
    recs = [_rec(10, -100_000)]
    out = evaluate(records=recs, now_et=datetime(2026, 6, 2, 15, 0), config=cfg, state=state)
    assert out.tripped is False
    assert cfg.enabled is False


def test_evaluate_idempotent_for_same_records():
    """Calling evaluate twice with the same records must NOT double-count."""
    cfg = PortfolioBreakerConfig(daily_loss_cap_dollars=1000.0)
    state = PortfolioBreakerState()
    recs = [_rec(10, -200), _rec(11, -300)]
    evaluate(records=recs, now_et=datetime(2026, 6, 2, 15, 0), config=cfg, state=state)
    first = state.realised_pnl_today
    evaluate(records=recs, now_et=datetime(2026, 6, 2, 15, 0), config=cfg, state=state)
    second = state.realised_pnl_today
    assert first == pytest.approx(second)
    assert first == pytest.approx(-500)


def test_evaluate_skips_records_from_other_sessions():
    """A trade from yesterday's session is ignored for today's PnL."""
    cfg = PortfolioBreakerConfig(daily_loss_cap_dollars=1000.0)
    state = PortfolioBreakerState()
    recs = [
        _rec(10, -800, day=1),  # June 1 trade
        _rec(11, -100, day=2),  # June 2 trade
    ]
    out = evaluate(records=recs, now_et=datetime(2026, 6, 2, 12, 0), config=cfg, state=state)
    assert out.realised_pnl_today == pytest.approx(-100)


def test_evaluate_session_rollover_resets_state():
    """When session_date advances, accumulators reset."""
    cfg = PortfolioBreakerConfig(daily_loss_cap_dollars=500.0)
    state = PortfolioBreakerState()
    # Day 1: tripped
    recs_d1 = [_rec(10, -600, day=2)]
    evaluate(records=recs_d1, now_et=datetime(2026, 6, 2, 12, 0), config=cfg, state=state)
    assert state.tripped is True
    # Day 2: fresh session — no carry-over even if old records linger
    recs_d2 = [_rec(10, -100, day=3)]
    evaluate(records=recs_d2, now_et=datetime(2026, 6, 3, 12, 0), config=cfg, state=state)
    assert state.tripped is False
    assert state.realised_pnl_today == pytest.approx(-100)


# ----------------------------- backtest path -----------------------------


@dataclass
class _StubTrade:
    """Mimics the relevant fields of ``core.backtest.models.BacktestTrade``."""
    exit_time: Any
    pnl: float
    symbol: str = "MNQ"


def test_replay_evaluate_walks_backtest_trades():
    cfg = PortfolioBreakerConfig(daily_loss_cap_dollars=600.0)
    breaker = PortfolioDailyBreaker(trading_bot=None, config=cfg)
    trades = [
        _StubTrade(exit_time=datetime(2026, 6, 2, 10, 0), pnl=-400.0),
        _StubTrade(exit_time=datetime(2026, 6, 2, 12, 0), pnl=-250.0),
    ]
    state = breaker.replay_evaluate(
        now_et=datetime(2026, 6, 2, 14, 0),
        trades=trades,
    )
    assert state.realised_pnl_today == pytest.approx(-650.0)
    assert state.tripped is True


def test_replay_evaluate_handles_empty_trade_list():
    cfg = PortfolioBreakerConfig(daily_loss_cap_dollars=600.0)
    breaker = PortfolioDailyBreaker(trading_bot=None, config=cfg)
    state = breaker.replay_evaluate(now_et=datetime(2026, 6, 2, 14, 0), trades=[])
    assert state.tripped is False
    assert state.realised_pnl_today == 0.0


def test_replay_evaluate_handles_none_iterator():
    cfg = PortfolioBreakerConfig(daily_loss_cap_dollars=600.0)
    breaker = PortfolioDailyBreaker(trading_bot=None, config=cfg)
    state = breaker.replay_evaluate(now_et=datetime(2026, 6, 2, 14, 0), trades=None)
    assert state.tripped is False


# ----------------------------- live path -----------------------------


class _StubBus:
    """Minimal event-bus stub.  Same pattern as
    `tests/test_live_trade_breaker_bridge.py::_StubBus`."""

    def __init__(self):
        self.subs: Dict[Any, List[Any]] = {}
        self.published: List[Any] = []

    def subscribe(self, event_type, callback):
        self.subs.setdefault(event_type, []).append(callback)

    def unsubscribe(self, event_type, callback):
        try:
            self.subs.get(event_type, []).remove(callback)
        except ValueError:
            pass

    async def publish(self, event):
        self.published.append(event)


def _make_stub_bot(*, flatten_calls: list) -> SimpleNamespace:
    async def _flatten(interactive: bool = True):
        flatten_calls.append(interactive)
        return {"success": True}
    bot = SimpleNamespace(
        event_bus=_StubBus(),
        flatten_all_positions=_flatten,
        strategy_manager=None,
    )
    return bot


def test_start_and_stop_subscribe_unsubscribe():
    cfg = PortfolioBreakerConfig(daily_loss_cap_dollars=500.0)
    bot = _make_stub_bot(flatten_calls=[])
    breaker = PortfolioDailyBreaker(bot, cfg)
    asyncio.run(breaker.start())
    from core.events import EventType
    assert len(bot.event_bus.subs.get(EventType.TRADE_CLOSED, [])) == 1
    asyncio.run(breaker.stop())
    assert len(bot.event_bus.subs.get(EventType.TRADE_CLOSED, [])) == 0


def test_start_disabled_when_no_event_bus():
    cfg = PortfolioBreakerConfig(daily_loss_cap_dollars=500.0)
    breaker = PortfolioDailyBreaker(trading_bot=SimpleNamespace(event_bus=None), config=cfg)
    assert asyncio.run(breaker.start()) is False


def test_trade_closed_event_accumulates():
    cfg = PortfolioBreakerConfig(daily_loss_cap_dollars=500.0)
    flatten_calls: list = []
    bot = _make_stub_bot(flatten_calls=flatten_calls)
    breaker = PortfolioDailyBreaker(bot, cfg)
    asyncio.run(breaker.start())

    # Build a TRADE_CLOSED event payload matching user_hub_handlers.py:415-446
    from core.events import Event, EventType

    def _evt(pnl: float, exit_h: int) -> Event:
        return Event(
            type=EventType.TRADE_CLOSED,
            data={
                "symbol": "MNQ",
                "side": "BUY",
                "net_pnl": pnl,
                "exit_time": datetime(2026, 6, 2, exit_h, 0),
                "entry_time": datetime(2026, 6, 2, exit_h - 1, 0),
            },
        )

    # Two losses still under the cap → no trip.
    asyncio.run(breaker._on_trade_closed(_evt(-200, 10)))
    asyncio.run(breaker._on_trade_closed(_evt(-100, 11)))
    assert breaker.state.tripped is False
    assert flatten_calls == []

    # Third loss breaches → trip.
    asyncio.run(breaker._on_trade_closed(_evt(-300, 12)))
    assert breaker.state.tripped is True
    assert flatten_calls == [False]  # interactive=False
    # PORTFOLIO_KILL must have been published.
    kinds = {getattr(e, "type", None) for e in bot.event_bus.published}
    assert EventType.PORTFOLIO_KILL in kinds


def test_trip_fires_once_per_session():
    """Once tripped, additional losing fills on the same day MUST NOT
    trigger another flatten — the broker call must be idempotent."""
    cfg = PortfolioBreakerConfig(daily_loss_cap_dollars=500.0)
    flatten_calls: list = []
    bot = _make_stub_bot(flatten_calls=flatten_calls)
    breaker = PortfolioDailyBreaker(bot, cfg)
    asyncio.run(breaker.start())

    from core.events import Event, EventType

    def _evt(pnl: float) -> Event:
        return Event(
            type=EventType.TRADE_CLOSED,
            data={
                "symbol": "MNQ",
                "side": "BUY",
                "net_pnl": pnl,
                "exit_time": datetime(2026, 6, 2, 10, 0),
                "entry_time": datetime(2026, 6, 2, 9, 0),
            },
        )

    asyncio.run(breaker._on_trade_closed(_evt(-1000)))
    asyncio.run(breaker._on_trade_closed(_evt(-100)))
    asyncio.run(breaker._on_trade_closed(_evt(-100)))
    assert len(flatten_calls) == 1


def test_malformed_event_does_not_crash_bus():
    cfg = PortfolioBreakerConfig(daily_loss_cap_dollars=500.0)
    bot = _make_stub_bot(flatten_calls=[])
    breaker = PortfolioDailyBreaker(bot, cfg)
    asyncio.run(breaker.start())

    from core.events import Event, EventType
    # No exit_time field
    bad = Event(type=EventType.TRADE_CLOSED, data={"symbol": "MNQ"})
    asyncio.run(breaker._on_trade_closed(bad))
    assert breaker.state.tripped is False  # no crash, no spurious trip
