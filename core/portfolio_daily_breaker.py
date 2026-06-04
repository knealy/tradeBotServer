"""Portfolio-level daily-loss circuit breaker.

The arsenal-roadmap Phase-1 prerequisite that turns the worst-case-day
arithmetic in ``docs/STRATEGY_ARSENAL.md`` into a HARD ceiling.

Today every strategy has its own ``core.consec_loss_breaker`` instance
(per-symbol, per-strategy).  None of them see each other.  A trip-day
where all three production strategies hit their max single-day loss
simultaneously is ~$985 — about 99 % of the operator's $1000 cap — and
the per-strategy breakers are oblivious to the aggregate.

This module closes that gap:

1. **Live mode**: subscribes to ``EventType.TRADE_CLOSED`` on
   ``trading_bot.event_bus``.  Every fill that hits ``user_hub_handlers``
   already publishes one of these (see ``core/user_hub_handlers.py:415``).
   On each event, it aggregates per-ET-session realised PnL across
   ALL strategies / symbols / accounts.  On breach of
   ``daily_loss_cap_dollars`` it:
     a. Calls ``trading_bot.flatten_all_positions(interactive=False)``
     b. Publishes ``EventType.PORTFOLIO_KILL`` (``StrategyManager`` listens
        and sets ``strategy.config.enabled = False`` for every active
        strategy until rollover).
     c. Stays tripped until the ET session rollover (default 18:00 ET).

2. **Backtest mode**: strategies running through the replay engine call
   :meth:`PortfolioDailyBreaker.replay_evaluate` once per bar with
   ``(now_et, replay_engine.trades)``.  Same logic; no event bus.

Design notes
------------
- ET session rollover at 18:00 (CME futures globex day boundary).  Bars
  AT or AFTER 18:00 ET belong to the NEXT calendar session.  This matches
  the existing ``core.consec_loss_breaker`` convention so live and
  backtest see the same "what day is this?" answer.
- Trade attribution by strategy is NOT required — the cap is a portfolio
  ceiling on TOTAL realised PnL.  The TRADE_CLOSED payload has no
  ``strategy`` field today, which would have been a blocker if we needed
  to bucket per-strategy.  We don't, so this is fine.
- Reset semantics: when ``session_date`` advances, ALL state resets
  (running PnL = 0, tripped = False, trade_records cleared).  No carry-
  over from the previous session.
- The flatten action is opt-in via the constructor: when ``trading_bot``
  is ``None`` (test / dry-run), the breaker still publishes
  ``PORTFOLIO_KILL`` but skips the broker call.  Same pattern as the
  consec-loss breaker bridge.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Callable, Iterable, List, Optional

logger = logging.getLogger(__name__)


# Default ET session-reset time (CME futures globex day boundary).
ET_SESSION_RESET = time(18, 0)


# ----------------------------- config + state -----------------------------


@dataclass(frozen=True)
class PortfolioBreakerConfig:
    """Operator-facing knobs.

    ``daily_loss_cap_dollars`` is the POSITIVE magnitude — the breaker
    trips when daily realised PnL ≤ ``-cap``.  A value of 0 disables
    the breaker entirely.

    ``reset_at_et`` is the time-of-day in America/New_York at which the
    running session-date advances by one calendar day.  Bars at or after
    this time belong to the next session.  Default 18:00 ET matches the
    CME futures globex rollover.
    """
    daily_loss_cap_dollars: float = 0.0
    reset_at_et: time = ET_SESSION_RESET

    @property
    def enabled(self) -> bool:
        return self.daily_loss_cap_dollars > 0.0


@dataclass
class PortfolioBreakerState:
    """In-memory accumulator.  One instance per breaker."""
    session_date: Optional[date] = None
    realised_pnl_today: float = 0.0
    tripped: bool = False
    trip_time_et: Optional[datetime] = None
    trip_pnl: float = 0.0
    # Records that fall on the current session_date.  Cleared on rollover.
    _records: List["_PnlRecord"] = field(default_factory=list)


@dataclass(frozen=True)
class _PnlRecord:
    """Internal trade record shape.  Built from either a TRADE_CLOSED
    Event payload or a ``BacktestTrade`` from the replay engine."""
    exit_time_et: datetime
    net_pnl: float
    symbol: str
    source: str  # "live" | "backtest"


# ----------------------------- helpers -----------------------------


def session_date_for(ts_et: datetime, reset_at_et: time = ET_SESSION_RESET) -> date:
    """ET session date.  Bars at/after ``reset_at_et`` belong to the
    NEXT calendar day's session.

    Accepts naive ET datetime or aware ET datetime.  Aware datetime in any
    other zone is converted to ET first.
    """
    if ts_et.tzinfo is not None:
        # If aware, convert to ET-naive for the comparison.
        from zoneinfo import ZoneInfo
        try:
            ts_et = ts_et.astimezone(ZoneInfo("America/New_York")).replace(tzinfo=None)
        except Exception:
            ts_et = ts_et.replace(tzinfo=None)
    if ts_et.time() >= reset_at_et:
        return (ts_et + timedelta(days=1)).date()
    return ts_et.date()


def _now_et() -> datetime:
    """Naive-ET 'now' — matches the strategy convention."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(timezone.utc).astimezone(ZoneInfo("America/New_York")).replace(tzinfo=None)
    except Exception:
        return datetime.now()


def _coerce_exit_time(value: Any) -> Optional[datetime]:
    """Tolerate datetime, ISO-string, or pandas Timestamp.  Returns
    naive ET datetime; falls back to now() on unparseable input."""
    if value is None:
        return None
    if isinstance(value, datetime):
        ts = value
    else:
        try:
            ts = datetime.fromisoformat(str(value))
        except Exception:
            return None
    if ts.tzinfo is not None:
        try:
            from zoneinfo import ZoneInfo
            ts = ts.astimezone(ZoneInfo("America/New_York")).replace(tzinfo=None)
        except Exception:
            ts = ts.replace(tzinfo=None)
    return ts


def _record_from_event_data(data: dict) -> Optional[_PnlRecord]:
    """Build a `_PnlRecord` from a ``TRADE_CLOSED`` event payload.

    Payload keys per ``core/user_hub_handlers.py:415-446``:
        symbol, net_pnl, side, entry_time, exit_time, ...
    """
    exit_time = _coerce_exit_time(data.get("exit_time"))
    if exit_time is None:
        return None
    try:
        pnl = float(data.get("net_pnl", 0.0))
    except (TypeError, ValueError):
        return None
    symbol = str(data.get("symbol", "")).upper()
    return _PnlRecord(exit_time_et=exit_time, net_pnl=pnl, symbol=symbol, source="live")


def _record_from_backtest_trade(trade: Any) -> Optional[_PnlRecord]:
    """Convert a ``BacktestTrade`` from ``_replay_engine.trades`` into the
    breaker's internal record shape."""
    exit_time = getattr(trade, "exit_time", None)
    if exit_time is None:
        return None
    et = _coerce_exit_time(exit_time)
    if et is None:
        return None
    try:
        pnl = float(getattr(trade, "pnl", 0.0))
    except (TypeError, ValueError):
        return None
    symbol = str(getattr(trade, "symbol", "")).upper()
    return _PnlRecord(exit_time_et=et, net_pnl=pnl, symbol=symbol, source="backtest")


# ----------------------------- pure evaluator -----------------------------


def evaluate(
    *,
    records: Iterable[_PnlRecord],
    now_et: datetime,
    config: PortfolioBreakerConfig,
    state: PortfolioBreakerState,
) -> PortfolioBreakerState:
    """Walk ``records`` (all known trades for this session) and update
    ``state`` in place — sets ``realised_pnl_today``, ``tripped``,
    ``trip_time_et``, ``trip_pnl``.

    Pure function — no side effects beyond the mutations to ``state``.
    Returns the same ``state`` for chaining.
    """
    if not config.enabled:
        return state

    today = session_date_for(now_et, config.reset_at_et)

    # Session rollover: discard last session's accumulation entirely.
    if state.session_date is None or state.session_date != today:
        state.session_date = today
        state.realised_pnl_today = 0.0
        state.tripped = False
        state.trip_time_et = None
        state.trip_pnl = 0.0
        state._records = []

    # Accumulate from scratch on every call so a re-emitted/dedupe-aware
    # caller can pass the same records twice without double-counting.
    total = 0.0
    fresh: List[_PnlRecord] = []
    for rec in records:
        if session_date_for(rec.exit_time_et, config.reset_at_et) != today:
            continue
        total += rec.net_pnl
        fresh.append(rec)
    state.realised_pnl_today = total
    state._records = fresh

    if total <= -config.daily_loss_cap_dollars and not state.tripped:
        state.tripped = True
        state.trip_time_et = now_et
        state.trip_pnl = total

    return state


# ----------------------------- live + replay -----------------------------


class PortfolioDailyBreaker:
    """Singleton-style portfolio breaker.

    Typical wiring (production):

        # in trading_bot.py boot path:
        from core.portfolio_daily_breaker import (
            PortfolioBreakerConfig, PortfolioDailyBreaker,
        )
        cfg = PortfolioBreakerConfig(
            daily_loss_cap_dollars=float(os.environ.get(
                "PORTFOLIO_DAILY_LOSS_CAP", "1000")),
        )
        self.portfolio_breaker = PortfolioDailyBreaker(self, cfg)
        await self.portfolio_breaker.start()

    Backtest usage (strategies call this directly each bar):

        from core.portfolio_daily_breaker import (
            PortfolioBreakerConfig, PortfolioDailyBreaker,
        )
        # Per-bar, in analyze():
        if not self._pbreaker:
            self._pbreaker = PortfolioDailyBreaker(None, PortfolioBreakerConfig(1000))
        snap = self._pbreaker.replay_evaluate(now_et=bar_time,
                                              trades=self._replay_engine.trades)
        if snap.tripped:
            return None
    """

    def __init__(self, trading_bot: Any, config: PortfolioBreakerConfig):
        self.trading_bot = trading_bot
        self.config = config
        self.state = PortfolioBreakerState()
        # Live mode buffer — append-only on TRADE_CLOSED, pruned by evaluate().
        self._live_records: List[_PnlRecord] = []
        self._unsub: Optional[Callable[[], None]] = None

    # ------------------- live mode -------------------

    async def start(self) -> bool:
        """Subscribe to ``TRADE_CLOSED`` on the bot's event bus.  Idempotent."""
        if self._unsub is not None:
            return True
        bus = getattr(self.trading_bot, "event_bus", None) if self.trading_bot else None
        if bus is None:
            logger.debug("PortfolioDailyBreaker: no event_bus on trading_bot; live mode skipped")
            return False
        try:
            from core.events import EventType
            bus.subscribe(EventType.TRADE_CLOSED, self._on_trade_closed)
        except Exception as exc:
            logger.warning("PortfolioDailyBreaker subscribe failed: %s", exc)
            return False

        def _unsub() -> None:
            try:
                from core.events import EventType as _ET
                bus.unsubscribe(_ET.TRADE_CLOSED, self._on_trade_closed)
            except Exception as exc:
                logger.debug("PortfolioDailyBreaker unsubscribe failed: %s", exc)

        self._unsub = _unsub
        cap = self.config.daily_loss_cap_dollars
        logger.info(
            "🛡️  PortfolioDailyBreaker active — daily realised-PnL cap = -$%.2f (reset %s ET)",
            cap, self.config.reset_at_et.strftime("%H:%M"),
        )
        return True

    async def stop(self) -> None:
        unsub = self._unsub
        self._unsub = None
        if unsub is not None:
            try:
                unsub()
            except Exception as exc:
                logger.debug("PortfolioDailyBreaker.stop: unsub raised %s", exc)

    async def _on_trade_closed(self, event: Any) -> None:
        """Bus callback — dispatched serially per the event-bus contract."""
        try:
            data = getattr(event, "data", None) or {}
            rec = _record_from_event_data(data)
            if rec is None:
                return
            self._live_records.append(rec)
            # Prune records older than the current session — bounded memory.
            now_et = _now_et()
            today = session_date_for(now_et, self.config.reset_at_et)
            self._live_records = [
                r for r in self._live_records
                if session_date_for(r.exit_time_et, self.config.reset_at_et) == today
            ]
            was_tripped = self.state.tripped
            evaluate(
                records=self._live_records,
                now_et=now_et,
                config=self.config,
                state=self.state,
            )
            if self.state.tripped and not was_tripped:
                await self._fire_kill()
        except Exception as exc:
            logger.error(
                "PortfolioDailyBreaker._on_trade_closed crashed: %s",
                exc, exc_info=True,
            )

    async def _fire_kill(self) -> None:
        """Flat-file the account + disable every active strategy."""
        cap = self.config.daily_loss_cap_dollars
        logger.warning(
            "🚨 PORTFOLIO DAILY BREAKER TRIPPED — realised PnL today = $%.2f (cap = -$%.2f). "
            "Flattening account + disabling all strategies until next session rollover.",
            self.state.trip_pnl, cap,
        )

        if self.trading_bot is not None:
            try:
                flatten = getattr(self.trading_bot, "flatten_all_positions", None)
                if callable(flatten):
                    if asyncio.iscoroutinefunction(flatten):
                        await flatten(interactive=False)
                    else:
                        flatten(interactive=False)
            except Exception as exc:
                logger.error("PortfolioDailyBreaker flatten failed: %s", exc, exc_info=True)

            # Publish PORTFOLIO_KILL so StrategyManager (and any other
            # subscribers, e.g. the GUI) can react in parallel.
            try:
                from core.event_bus import EventBus
                from core.events import Event, EventType
                bus = getattr(self.trading_bot, "event_bus", None)
                if bus is not None:
                    await bus.publish(Event(
                        type=EventType.PORTFOLIO_KILL,
                        data={
                            "realised_pnl_today": self.state.realised_pnl_today,
                            "cap_dollars": cap,
                            "session_date": str(self.state.session_date),
                            "trip_time_et": self.state.trip_time_et.isoformat() if self.state.trip_time_et else None,
                        },
                        source="portfolio_daily_breaker",
                    ))
            except Exception as exc:
                logger.error("PortfolioDailyBreaker publish PORTFOLIO_KILL failed: %s", exc, exc_info=True)

            # Best-effort: directly disable strategies if SM is reachable.
            try:
                sm = getattr(self.trading_bot, "strategy_manager", None)
                if sm is not None and getattr(sm, "strategies", None):
                    for strat in list(sm.strategies.values()):
                        try:
                            if hasattr(strat, "config"):
                                strat.config.enabled = False
                        except Exception:
                            pass
            except Exception as exc:
                logger.debug("PortfolioDailyBreaker direct-disable raised %s", exc)

    # ------------------- backtest mode -------------------

    def replay_evaluate(self, *, now_et: datetime, trades: Iterable[Any]) -> PortfolioBreakerState:
        """Backtest entry point.

        Strategies call this once per ``analyze`` bar, passing
        ``self._replay_engine.trades`` (the engine's append-only list of
        closed ``BacktestTrade`` instances).  Returns the updated
        state.  When ``state.tripped`` is True, the strategy should
        return ``None`` from ``analyze`` for the rest of the session.
        """
        recs: List[_PnlRecord] = []
        for t in trades or ():
            r = _record_from_backtest_trade(t)
            if r is not None:
                recs.append(r)
        evaluate(records=recs, now_et=now_et, config=self.config, state=self.state)
        return self.state
