"""Shared consecutive-loss circuit breaker for strategies.

Background
----------
``morning_range_reversion`` shipped the original implementation in
``strategies/morning_range_reversion_strategy.py::_consec_loss_breaker_status``
in Round 19 (see ``docs/CHANGELOG.md`` for the empirical motivation).
That implementation is regression-pinned by the morning-range smoke +
walk-forward suite and is left in place verbatim — porting it would risk a
parity drift on the live-traded strategy.

This module extracts the *pure* count + magnitude breaker walk into a
reusable helper that ``body_reversion`` and ``overnight_range`` consume.
Both strategies are simpler than morning-range (no KER regime gate yet) so
the helper sticks to count + cumulative-PnL magnitude and exposes a hook
that strategy-specific code can compose around when (and if) a regime
gate is added.

Live vs backtest
----------------
* **Backtest**: strategies hold a ``self._replay_engine`` reference whose
  ``trades`` attribute is the closed-trade list maintained by
  :class:`StrategyReplayEngine`. ``evaluate(trade_iter=reversed(engine.trades), ...)``
  walks newest-first.
* **Live**: ``StrategyManager.start_strategy`` calls
  :meth:`BaseStrategy.start_live_trade_bridge`, which subscribes the
  strategy to ``EventType.TRADE_CLOSED`` events. Each event appends to
  ``self._live_trade_history`` (a per-symbol bounded deque). The breaker
  consumes ``self._live_trade_history.trades_for(symbol)`` (already
  newest-first).

Both data sources expose the same ``symbol`` / ``pnl`` / ``exit_time``
accessors so the walk loop is identical — exactly mirroring the design
already validated for ``morning_range_reversion``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Iterable, Optional


@dataclass(frozen=True)
class BreakerConfig:
    """Per-symbol breaker knobs.

    Mirrors the TOML schema that ``morning_range_reversion`` uses so a
    config-aware reader (`from_strategy_config`) can be added later
    without rewriting strategy call sites.

    Attributes:
        max_losses: Streak length (≥ this many consecutive losing trades
            on a single symbol → breaker trips). ``0`` disables.
        cooldown_sessions: Calendar days the symbol stays blocked once
            tripped. ``0`` means "block permanently until a winning trade
            resets the streak" — useful in live; never use in backtest
            because the breaker would never release.
        magnitude_dollars: Optional cumulative-PnL filter. When > 0, the
            breaker only trips if the streak's ``sum(pnl)`` is
            ``<= -magnitude_dollars`` *as well as* the count gate. Lets
            shallow noise streaks (recover next day) ride while deep
            regime-change streaks halt. ``0`` disables.
    """

    max_losses: int = 0
    cooldown_sessions: int = 0
    magnitude_dollars: float = 0.0

    @property
    def enabled(self) -> bool:
        return self.max_losses > 0


def _normalize_to_date(value: Any) -> Optional[date]:
    """Best-effort coerce ``str | datetime | date`` → ``date``.

    The replay engine writes ``Trade.exit_time`` as a python ``datetime``;
    the live ``TRADE_CLOSED`` event payload uses an ISO string. Both
    must reduce to a ``date`` for cooldown math.
    """
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).date()
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).date()
        except ValueError:
            return None
    return None


def evaluate(
    *,
    symbol: str,
    bar_session_date: date,
    trade_iter: Iterable[Any],
    config: BreakerConfig,
) -> dict:
    """Walk a newest-first trade iterable and return the breaker status.

    The status dict mirrors ``MorningRangeReversionStrategy._consec_loss_breaker_status``
    so logging + diagnostic code can be shared (drop-in compatible).

    Args:
        symbol: Trading symbol the caller is asking about. Trades with a
            different ``symbol`` attribute are skipped (does not interrupt
            the streak walk).
        bar_session_date: ET date of the bar that triggered this call.
            Used as a fallback when the most-recent loss has no recoverable
            timestamp.
        trade_iter: Iterable yielding closed-trade-like objects with at
            least ``symbol`` (str) + ``pnl`` (float) + one of
            ``exit_time`` / ``entry_time`` attributes. Newest-first.
        config: :class:`BreakerConfig` for this symbol.

    Returns:
        ``dict`` with keys ``blocked`` (bool), ``streak`` (int),
        ``trip_session_date`` (date | None), ``days_since_trip`` (int | None),
        ``cooldown`` (int), ``reason`` (str). Pure read — no state writes
        anywhere, safe to call every bar.
    """
    if not config.enabled:
        return {"blocked": False, "streak": 0, "reason": "ok"}

    sym_upper = str(symbol).upper()
    streak = 0
    streak_pnl_sum = 0.0
    latest_loss_date: Optional[date] = None
    for trade in trade_iter:
        t_sym = str(getattr(trade, "symbol", "") or "").upper()
        if t_sym != sym_upper:
            continue
        try:
            pnl = float(getattr(trade, "pnl", 0.0) or 0.0)
        except (TypeError, ValueError):
            pnl = 0.0
        if pnl >= 0:
            break  # winner ends the contiguous-loss walk
        if latest_loss_date is None:
            exit_ts = getattr(trade, "exit_time", None) or getattr(trade, "entry_time", None)
            latest_loss_date = _normalize_to_date(exit_ts) or bar_session_date
        streak += 1
        streak_pnl_sum += pnl

    trip_session_date = latest_loss_date
    if streak < config.max_losses:
        return {"blocked": False, "streak": streak, "reason": "ok"}

    if config.magnitude_dollars > 0.0 and streak_pnl_sum > -config.magnitude_dollars:
        return {
            "blocked": False,
            "streak": streak,
            "streak_pnl": streak_pnl_sum,
            "magnitude_threshold": -config.magnitude_dollars,
            "reason": "shallow_streak_skipped",
        }

    if config.cooldown_sessions <= 0:
        return {
            "blocked": True,
            "streak": streak,
            "streak_pnl": streak_pnl_sum,
            "trip_session_date": trip_session_date,
            "days_since_trip": None,
            "cooldown": 0,
            "reason": "trip_active",
        }

    days_since = None
    if trip_session_date is not None:
        days_since = max(0, (bar_session_date - trip_session_date).days)
    if days_since is not None and days_since >= config.cooldown_sessions:
        return {
            "blocked": False,
            "streak": streak,
            "streak_pnl": streak_pnl_sum,
            "trip_session_date": trip_session_date,
            "days_since_trip": days_since,
            "cooldown": config.cooldown_sessions,
            "reason": "trip_cooldown_elapsed",
        }
    return {
        "blocked": True,
        "streak": streak,
        "streak_pnl": streak_pnl_sum,
        "trip_session_date": trip_session_date,
        "days_since_trip": days_since,
        "cooldown": config.cooldown_sessions,
        "reason": "trip_active",
    }


def trade_iter_for_strategy(strategy: Any, symbol: str) -> Optional[Iterable[Any]]:
    """Return the newest-first trade iterator a strategy should walk.

    Backtest path: ``strategy._replay_engine.trades`` (reversed).
    Live path: ``strategy._live_trade_history.trades_for(symbol)``.
    Returns ``None`` when neither is populated — caller treats that as
    "no engine, can't evaluate, don't block".
    """
    engine = getattr(strategy, "_replay_engine", None)
    if engine is not None and hasattr(engine, "trades"):
        return reversed(getattr(engine, "trades", []))
    history = getattr(strategy, "_live_trade_history", None)
    if history is not None:
        return iter(history.trades_for(symbol))
    return None
