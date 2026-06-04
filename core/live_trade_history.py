"""Per-symbol live trade history buffer for strategies.

Strategies with cross-session circuit breakers (e.g.
``morning_range_reversion`` and ``overnight_range``) need to walk the most
recent N trades on a symbol to decide whether to block new entries.  In
backtest mode the breaker reads ``StrategyReplayEngine.trades`` directly
(see ``MorningRangeReversionStrategy._consec_loss_breaker_status``).  In
live mode there is no replay engine, so this module provides the
equivalent buffer fed by ``EventType.TRADE_CLOSED`` events published from
``core/user_hub_handlers.on_trade``.

Design notes
------------

* The buffer is **per-strategy** (held on the strategy instance, not
  globally).  Two strategies trading the same symbol get independent
  histories so the breaker for strategy A is not tripped by strategy B's
  losses.
* The buffer is bounded (default 200 trades / symbol) — the consec-loss
  breaker only walks until it finds a winner, so a small cap is sufficient
  and prevents an unbounded list during long live runs.
* The buffer's element shape mirrors the bits of ``Trade`` that the
  backtest ``trade`` object exposes (``symbol``, ``pnl``, ``entry_time``,
  ``exit_time``, ``side``) so the breaker can read either source with the
  same accessor logic.
* The bridge is **opt-in per strategy** via
  ``StrategyConfig.live_breaker_enabled`` (env: ``STRATEGY_LIVE_BREAKER``)
  so operators can flip it for individual strategies during early
  rollout.  When OFF the strategy's ``record_trade_outcome`` is a no-op
  and the breaker falls through with ``blocked=False`` — same observable
  behaviour the codebase had before this bridge existed.
"""
from __future__ import annotations

import logging
import os
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Deque, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)


# 200 trades / symbol covers the worst observed live-mode loss streaks
# many times over (longest streak seen in 9m walkforward: 4 trades).
# Buffer is per-symbol so total memory is O(symbols × 200 × ~120 B) ≈ tiny.
_DEFAULT_MAX_HISTORY = 200

_TRUE = frozenset({"1", "true", "yes", "on"})


def live_breaker_default_enabled() -> bool:
    """Process-wide default for the live breaker bridge.

    Defaults to OFF.  Operators flip ``STRATEGY_LIVE_BREAKER=1`` after the
    backtest-vs-live parity validation pass; per-strategy overrides win
    over this default via ``StrategyConfig.live_breaker_enabled``.
    """
    return os.environ.get("STRATEGY_LIVE_BREAKER", "").strip().lower() in _TRUE


@dataclass(slots=True)
class LiveTradeRecord:
    """A single completed live trade.

    Field shape mirrors the relevant accessors on the backtest
    ``Trade``/``BacktestTrade`` object so a breaker that walks the
    backtest's ``engine.trades`` list can walk this list with identical
    code.  ``symbol`` is upper-cased on ingest.
    """

    symbol: str
    pnl: float
    entry_time: Optional[datetime]
    exit_time: Optional[datetime]
    side: str
    trade_id: Optional[str] = None
    quantity: int = 0
    entry_price: float = 0.0
    exit_price: float = 0.0
    session_id: Optional[str] = None


class LiveTradeHistory:
    """Per-symbol bounded deque of ``LiveTradeRecord`` for a single strategy.

    Thread-safety: callers should only invoke from the strategy's own
    asyncio event loop (event-bus subscribers run there).  We do not
    take a lock because there is no cross-loop access; concurrent
    ``record()`` from a different thread would race the deque's
    ``append`` but Python's ``deque`` is itself atomic for ``append``
    so the worst-case is out-of-order insertion that the breaker still
    handles correctly (it walks backward from the most-recent entry).
    """

    __slots__ = ("_by_symbol", "_max_per_symbol")

    def __init__(self, max_per_symbol: int = _DEFAULT_MAX_HISTORY) -> None:
        self._by_symbol: Dict[str, Deque[LiveTradeRecord]] = {}
        self._max_per_symbol = max(1, int(max_per_symbol))

    def record(self, rec: LiveTradeRecord) -> None:
        """Append a new completed trade record to the symbol's buffer."""
        sym = (rec.symbol or "").upper()
        if not sym:
            return
        rec.symbol = sym
        buf = self._by_symbol.get(sym)
        if buf is None:
            buf = deque(maxlen=self._max_per_symbol)
            self._by_symbol[sym] = buf
        buf.append(rec)

    def trades_for(self, symbol: str) -> List[LiveTradeRecord]:
        """Most-recent-first list of recorded trades for ``symbol``.

        Returns a fresh list (not a view) so callers can safely mutate
        without poisoning the deque.  The list is **already ordered
        most-recent-first** to match the
        ``for trade in reversed(engine.trades)`` walk pattern.
        """
        buf = self._by_symbol.get((symbol or "").upper())
        if not buf:
            return []
        return list(reversed(buf))

    def all_trades(self) -> Iterable[LiveTradeRecord]:
        """Iterate every record across symbols (insertion order)."""
        for buf in self._by_symbol.values():
            yield from buf

    def clear(self, symbol: Optional[str] = None) -> None:
        """Reset history.  ``symbol=None`` clears every symbol."""
        if symbol is None:
            self._by_symbol.clear()
            return
        self._by_symbol.pop(symbol.upper(), None)

    def __len__(self) -> int:
        return sum(len(buf) for buf in self._by_symbol.values())


def trade_record_from_event(data: Dict[str, Any]) -> Optional[LiveTradeRecord]:
    """Best-effort coercion of a ``TRADE_CLOSED`` event payload into a record.

    Returns ``None`` when ``symbol`` is missing or ``net_pnl`` is not
    finite (covers paper-trade synthetic fills with no pnl info — those
    have no meaning to the breaker).
    """
    if not isinstance(data, dict):
        return None
    sym = data.get("symbol") or ""
    if not sym:
        return None
    try:
        pnl = float(data.get("net_pnl", data.get("pnl", 0.0)) or 0.0)
    except (TypeError, ValueError):
        return None
    return LiveTradeRecord(
        symbol=str(sym).upper(),
        pnl=pnl,
        entry_time=_coerce_dt(data.get("entry_time")),
        exit_time=_coerce_dt(data.get("exit_time")),
        side=str(data.get("side") or ""),
        trade_id=data.get("trade_id"),
        quantity=int(data.get("quantity") or 0),
        entry_price=float(data.get("entry_price") or 0.0),
        exit_price=float(data.get("exit_price") or 0.0),
        session_id=data.get("session_id"),
    )


def _coerce_dt(value: Any) -> Optional[datetime]:
    """Best-effort ISO / datetime-ish → ``datetime``."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None
