"""Shared time-based exit for strategies with bracket-order risk.

Background
----------
``body_reversion``'s ``manage_positions`` (see
``strategies/body_reversion_strategy.py``) closes any position that has been
held longer than ``max_hold_bars`` bars by:

1. Cancelling the OCO stop / TP exit legs (so they don't race with the
   close-at-market order)
2. Cancelling orphan OCO legs from any *previously* fully-closed bracket
   (the engine bug where a partial stack-exit leaves an orphan stop that
   would otherwise open a phantom counter-position)
3. Submitting a MARKET order in the opposite direction at the current bar's
   close price and tagging it ``exit_reason = "timeout"``.

This module extracts that logic so the experimental strategies
(``trend_following``, ``mean_reversion``, ``simple_candle``) can adopt the
same exit without duplicating the +90 lines of engine-aware code in every
file. The function is a pure backtest helper — live strategies have their
own ``_manage_positions_live`` paths and the live broker enforces brackets.

Live note
---------
The ``engine`` argument is the ``StrategyReplayEngine`` instance (which
exposes ``backtest_engine.positions`` / ``backtest_engine.pending_orders``)
or, equivalently for backwards compatibility, the underlying
``BacktestEngine`` itself. ``trading_bot.backtest_engine`` is the canonical
live-replay handle but the replay engine wires ``self._replay_engine`` on
the strategy directly so callers usually pass that.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


def close_positions_exceeding_max_hold(
    engine: Any,
    *,
    max_hold_bars: int,
    current_bar_seq: int,
    entry_bar_seq: Dict[str, int],
    log_prefix: str = "max_hold_exit",
    logger: Optional[Any] = None,
) -> int:
    """Close any positions held longer than ``max_hold_bars`` at MARKET.

    Returns the number of positions closed. ``0`` when no positions exceed
    the threshold, when ``max_hold_bars <= 0``, or when there is no engine
    (live mode falls through; caller should handle live exits separately).

    Args:
        engine: ``StrategyReplayEngine`` or ``BacktestEngine``. ``None``
            short-circuits to 0 (live-only context).
        max_hold_bars: Number of bars a position may stay open. ``0`` /
            negative disables the exit (always returns 0).
        current_bar_seq: Strategy's monotonically increasing per-bar counter.
            Compared against ``entry_bar_seq[symbol]`` to compute held bars.
        entry_bar_seq: Mutable dict (``{symbol: bar_seq_at_entry}``). The
            function ``pop()``s closed symbols so re-entries can re-arm.
        log_prefix: Label used in debug logs (typically the strategy name).
        logger: Optional logger; when None, logs are silently dropped.
    """
    if max_hold_bars <= 0 or engine is None:
        return 0

    be = getattr(engine, "backtest_engine", None) or engine
    positions = getattr(be, "positions", None) or {}
    if not positions:
        return 0

    # Orphan-leg cleanup — see body_reversion for the empirical bug this
    # guards against. A stacked-bracket close can leave an orphan OCO leg
    # in pending_orders pointing at a symbol that no longer has a
    # position; if that leg later fires the engine opens a phantom
    # counter-position. Removing those orphans up front is cheap and
    # eliminates the class of bug.
    pending_orders = getattr(be, "pending_orders", None)
    if pending_orders is not None and positions is not None:
        for o in list(pending_orders):
            if not getattr(o, "oco_group", None):
                continue
            osym = getattr(o, "symbol", "")
            if osym in positions:
                continue
            try:
                pending_orders.remove(o)
            except ValueError:
                pass

    closed = 0
    from core.backtest.models import OrderSide, OrderType

    for sym, pos in list(positions.items()):
        entry_seq = entry_bar_seq.get(sym)
        if entry_seq is None:
            continue
        held = current_bar_seq - entry_seq
        if held < max_hold_bars:
            continue

        if logger is not None:
            logger.debug(
                "%s timeout-close %s after %d bars (max=%d)",
                log_prefix, sym, held, max_hold_bars,
            )

        # Cancel any pending OCO bracket exits on this symbol so the
        # close-at-market does not race with stop / TP fills.
        pending = getattr(be, "pending_orders", None) or []
        for o in list(pending):
            if getattr(o, "symbol", "") != sym:
                continue
            if not getattr(o, "oco_group", None):
                continue
            try:
                pending.remove(o)
            except ValueError:
                pass

        close_side = (
            OrderSide.SELL if getattr(pos, "side", None) == OrderSide.BUY else OrderSide.BUY
        )
        try:
            close_id = be.place_order(
                symbol=sym,
                side=close_side,
                quantity=int(getattr(pos, "quantity", 1)),
                order_type=OrderType.MARKET,
                price=float(getattr(pos, "current_price", getattr(pos, "entry_price", 0.0))),
            )
            for o in be.pending_orders:
                if o.order_id == close_id:
                    o.exit_reason = "timeout"
                    break
        except Exception as exc:
            if logger is not None:
                logger.debug("%s timeout-close failed for %s: %s", log_prefix, sym, exc)
        finally:
            entry_bar_seq.pop(sym, None)
            closed += 1

    return closed
