"""
Strategy Replay Engine - Replays historical data through actual strategy classes.

This module allows backtesting using the same strategy code that runs live,
ensuring identical logic between backtest and production.
"""

from __future__ import annotations

import bisect
import logging
import os
import asyncio
import numpy as np
import pandas as pd
from typing import Optional, Dict, Any, List, Tuple, Iterator
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from .engine import BacktestEngine
from .models import BacktestResult, OrderSide, OrderType, OrderStatus
from .ohlcv import sanitize_replay_bars_list

# Sentinel used by ``_replay_force_flat_cutoff_minutes_et``'s lazy cache to
# distinguish "not yet computed" from "computed and resolved to None" (which
# is a valid config state, meaning "no force-flat cutoff").
_UNSET = object()

logger = logging.getLogger(__name__)


# ── BACKTEST_FAST_LOOP fast-path ──────────────────────────────────────────────
# Opt-in (``BACKTEST_FAST_LOOP=1``) replacement for the ``df.iterrows()`` hot
# loop. ``iterrows`` allocates a fresh ``pd.Series`` per bar (~70 µs each, plus
# metadata GC pressure); on a 5000-bar fold that's ~350 ms of pure iteration
# overhead before any strategy logic runs.
#
# The fast loop pulls OHLCV columns out as NumPy arrays once, then on each
# iteration wraps them in a ``_BarRow`` — a ``__slots__``-only class that
# exposes both ``bar['high']`` (dict-style, used by ``BacktestEngine._check_order_fill``,
# ``_update_unrealized_pnl``, ``_evaluate_breakeven_watches``, etc.) and
# ``bar.name`` (attribute style, used by ``BacktestEngine`` when stamping
# ``order.filled_timestamp``). Drop-in for ``pd.Series`` at the call sites the
# replay engine touches; produces byte-identical trade lists vs the slow loop
# (pinned by ``tests/test_backtest_fast_loop_parity.py``).
#
# Default is OFF so existing optimization runs / cached perf reports stay
# identical to git history. Enable per-run via env var; will be flipped to
# default-on after a wider validation pass.
_FAST_LOOP_TRUE = frozenset({"1", "true", "yes", "on"})


def _fast_loop_enabled() -> bool:
    return os.environ.get("BACKTEST_FAST_LOOP", "0").strip().lower() in _FAST_LOOP_TRUE


class _BarRow:
    """Drop-in OHLCV row for the replay hot loop. ~10× lighter than ``pd.Series``.

    Supports both Series-style access patterns the engine relies on:
    ``bar['high']`` (via ``__getitem__``) and ``bar.name`` (the bar's timestamp,
    used when stamping fills). ``__slots__`` avoids the per-instance ``__dict__``
    allocation that makes ``pd.Series`` expensive to construct at scale.
    """

    __slots__ = ("name", "open", "high", "low", "close", "volume")

    def __init__(
        self,
        name: Any,
        open_: float,
        high: float,
        low: float,
        close: float,
        volume: int,
    ) -> None:
        self.name = name
        self.open = open_
        self.high = high
        self.low = low
        self.close = close
        self.volume = volume

    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)

    def __repr__(self) -> str:  # pragma: no cover — debug only
        return (
            f"_BarRow(name={self.name!r}, open={self.open}, high={self.high}, "
            f"low={self.low}, close={self.close}, volume={self.volume})"
        )


def _iter_bars_fast(df: pd.DataFrame) -> Iterator[Tuple[int, Any, _BarRow]]:
    """Yield ``(i, timestamp, _BarRow)`` triples without a per-row Series alloc.

    NumPy column slices are extracted once; the per-iteration cost is just the
    ``_BarRow.__init__`` call plus an int index advance. Constructed lazily so
    callers can early-break without converting the tail.

    **Hot-loop note:** Materializing ``df.index`` to a Python list of pd.Timestamp
    once up front avoids ``DatetimeIndex.__getitem__`` per iteration (which costs
    ~70 µs each — confirmed by cProfile, used to be ~3.4 s of a 6 s replay).
    The list comprehension below pays a one-time O(n) cost (~50-150 ms for 17k
    bars) but every per-iter access is a cheap PyList_GetItem (~50 ns).
    """
    opens = df["open"].to_numpy()
    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()
    closes = df["close"].to_numpy()
    volumes = df["volume"].to_numpy()
    timestamps = list(df.index)  # one-shot conversion; per-iter access is cheap
    n = len(df)
    for i in range(n):
        ts = timestamps[i]
        yield i, ts, _BarRow(
            ts,
            float(opens[i]),
            float(highs[i]),
            float(lows[i]),
            float(closes[i]),
            int(volumes[i]) if volumes[i] == volumes[i] else 0,  # NaN-safe int cast
        )


# ── Fast-mode strategy-mock helpers (BACKTEST_FAST_LOOP=1) ────────────────────
#
# Profile of a 1-month MNQ ``morning_range_reversion`` replay (slow path):
#
#     mock_get_historical_data            16.0 s   (91% of total)
#       _bar_time_utc                      5.7 s   (12.3M calls)
#       _parse_dt                          3.2 s   (12.3M calls)
#
# The dominant cost is the slow path re-parsing every bar's ``timestamp`` field
# every single time the strategy asks for "the last N bars" — which morning_range
# does once per bar (~3000 mock calls per replay bar). That's O(n²) parsing
# of strings that pandas already parsed once when it built the OHLCV DataFrame.
#
# The fast mocks below replace the slow ``mock_get_historical_data`` with a
# ``np.searchsorted``-driven version that bisects a precomputed
# ``bar_times_ns: np.ndarray[int64]`` (extracted directly from the DataFrame's
# DatetimeIndex via ``.view('int64')`` — zero parsing cost). Per-call: O(log n)
# bisect + O(k) slice. Per-bar: a single integer cursor update.
#
# Correctness invariants the slow path enforces that the fast path must also
# enforce:
#
# 1. **No look-ahead.** The strategy must not see bars whose open time exceeds
#    the current replay cursor's timestamp. Slow path: ``bars_list`` is the
#    prefix, plus ``cur_utc`` redundancy guard. Fast path: ``bars_list`` is
#    the FULL list, clipped to ``[0, cursor_index + 1)`` via the int cursor.
# 2. **start_time / end_time honored.** Bisect with searchsorted gives the
#    same inclusive-open / inclusive-close semantics as the slow path's
#    ``bt < start_utc`` / ``bt > end_utc`` guards.
# 3. **limit semantics preserved.** Last ``limit`` bars after filtering.
# 4. **Resample path (rare).** ``MockTradingBot._select_historical_source``
#    delegates to ``_resample_native_to`` for coarser TFs. The fast path
#    leaves ``MockTradingBot.bars`` as the full list and sets a parallel
#    ``_fast_cursor_index`` attribute; ``_resample_native_to`` reads that
#    cursor and slices the bars list on demand (re-introducing the per-call
#    slice, but only when the resample path is actually exercised — most
#    strategies don't request a coarser TF every bar).
#
# Parity is pinned by ``tests/test_backtest_fast_loop_parity.py``: a fixed
# canonical-CSV fold runs through the slow path AND the fast path and the
# resulting trade lists are diffed byte-for-byte.


def _bars_to_utc_ns(df: pd.DataFrame) -> np.ndarray:
    """Extract UTC nanoseconds from a parsed-once DatetimeIndex.

    Both ``data_loader.HistoricalDataLoader.load_from_csv`` and
    ``parquet_cache.load_ohlcv_cached`` return frames with naive-UTC
    ``DatetimeIndex`` (no tz). The underlying ``datetime64[ns]`` storage
    aliases directly to int64 nanoseconds. No copy, no parsing.
    """
    idx = df.index
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_convert("UTC").tz_localize(None)
    return idx.to_numpy(dtype="datetime64[ns]").view("int64")


def _dt_to_utc_ns(value: Any) -> Optional[int]:
    """Coerce ``str | datetime | int | float | pd.Timestamp`` → UTC nanoseconds.

    Matches the semantics of ``_parse_dt`` in the slow path but returns int64
    nanoseconds suitable for ``np.searchsorted`` against the precomputed
    bar_times_ns array. Returns ``None`` only when the input is None or unparseable.
    """
    if value is None:
        return None
    if isinstance(value, (int, np.integer)):
        # Treat as epoch seconds (slow path's _parse_dt does the same).
        return int(value) * 1_000_000_000
    if isinstance(value, float):
        return int(value * 1_000_000_000)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        else:
            value = value.astimezone(timezone.utc)
        # pd.Timestamp handles ns precision; plain datetime is only µs but
        # the slow path also operates at datetime precision so this matches.
        return int(value.timestamp() * 1_000_000_000)
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            else:
                dt = dt.astimezone(timezone.utc)
            return int(dt.timestamp() * 1_000_000_000)
        except ValueError:
            return None
    # pd.Timestamp / numpy.datetime64 fall-through via to_pydatetime / item()
    try:
        ts = pd.Timestamp(value)
        if ts.tz is None:
            ts = ts.tz_localize("UTC")
        else:
            ts = ts.tz_convert("UTC")
        return int(ts.value)  # ns
    except Exception:
        return None


def replay_timeframe_to_minutes(replay_timeframe: Optional[str]) -> int:
    """Parse ``5m`` / ``15m`` / ``1h`` style replay bar width for intrabar 1m alignment."""
    if not replay_timeframe:
        return 1
    s = str(replay_timeframe).strip().lower()
    try:
        if s.endswith("m") and s[:-1].isdigit():
            return max(1, int(s[:-1]))
        if s.endswith("h"):
            return max(1, int(float(s[:-1]) * 60))
        if s.endswith("d"):
            return max(1, int(s[:-1]) * 1440)
    except ValueError:
        pass
    return 1


def sort_bars_1m_for_replay(bars_1m: Optional[List[Dict]]) -> Tuple[List[Dict], List[int]]:
    """Return 1m bar dicts sorted by open time and parallel int64 nanosecond keys for bisect."""
    import pandas as pd

    if not bars_1m:
        return [], []

    def _ts_ns(b: Dict[str, Any]) -> int:
        return int(pd.Timestamp(b.get("timestamp")).value)

    sorted_bars = sorted(bars_1m, key=_ts_ns)
    ns = [_ts_ns(b) for b in sorted_bars]
    return sorted_bars, ns


def intrabar_series_iter(
    t_open: Any,
    agg_bar: Any,
    bars_1m_sorted: List[Dict],
    bars_1m_ns: List[int],
    window_minutes: int,
) -> Iterator[Any]:
    """
    Yield OHLC sub-series for simulating fills inside one aggregate bar.

    Uses 1m bars whose open timestamp falls in ``[t_open, t_open + window_minutes)``.
    If none match, yields the aggregate bar once (5m-only behavior).
    """
    import pandas as pd

    if not bars_1m_sorted or not bars_1m_ns:
        yield agg_bar
        return
    t0 = pd.Timestamp(t_open)
    t_end = t0 + pd.Timedelta(minutes=max(1, int(window_minutes)))
    lo = bisect.bisect_left(bars_1m_ns, t0.value)
    hi = bisect.bisect_left(bars_1m_ns, t_end.value)
    if lo >= hi:
        yield agg_bar
        return
    for j in range(lo, hi):
        b = bars_1m_sorted[j]
        ts = pd.Timestamp(b.get("timestamp"))
        yield pd.Series(
            {
                "open": float(b.get("open", b.get("o", 0))),
                "high": float(b.get("high", b.get("h", 0))),
                "low": float(b.get("low", b.get("l", 0))),
                "close": float(b.get("close", b.get("c", 0))),
                "volume": int(b.get("volume", b.get("v", 0)) or 0),
            },
            name=ts,
        )


def bar_minutes_since_midnight_et(ts: Any) -> int:
    """US/Eastern minutes from local midnight for the bar clock (``timestamp`` / index)."""
    import pandas as pd

    t = pd.Timestamp(ts)
    if t.tzinfo is None:
        t = t.tz_localize("UTC")
    et = t.tz_convert(ZoneInfo("America/New_York"))
    return int(et.hour) * 60 + int(et.minute)


def parse_replay_force_flat_et_minutes(raw: Any) -> Optional[int]:
    """
    Parse ``timing.replay_force_flat_et`` (``\"16:00\"`` US/Eastern wall clock).

    Returns minutes since local midnight in US/Eastern, or ``None`` if disabled.
    """
    s = str(raw or "").strip()
    if not s or s.lower() in ("off", "none", "false", "0"):
        return None
    parts = s.replace(" ", "").split(":")
    if len(parts) < 2:
        return None
    try:
        hh = int(parts[0])
        mm = int(parts[1])
    except ValueError:
        return None
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        return None
    return hh * 60 + mm


class StrategyReplayEngine:
    """
    Replay engine that runs actual strategy classes on historical data.
    
    Intercepts strategy order placement calls and simulates execution
    using the BacktestEngine.
    """
    
    def __init__(
        self,
        strategy_instance,
        trading_bot,
        initial_capital: float = 50000.0,
        commission_per_contract: float = 2.50,
        slippage_ticks: float = 0.5,
        point_value: float = 2.0
    ):
        """
        Initialize strategy replay engine.
        
        Args:
            strategy_instance: Actual strategy instance (e.g., SimpleCandleStrategy)
            trading_bot: Trading bot instance (for data fetching)
            initial_capital: Starting capital
            commission_per_contract: Commission per contract
            slippage_ticks: Slippage in ticks
            point_value: Point value for symbol
        """
        self.strategy = strategy_instance
        self.trading_bot = trading_bot
        self.backtest_engine = BacktestEngine(
            initial_capital=initial_capital,
            commission_per_contract=commission_per_contract,
            slippage_ticks=slippage_ticks,
            point_value=point_value
        )
        
        # Track original methods to restore later
        self._original_place_bracket_order = None
        self._original_place_market_order = None
        
        # Track original trading bot methods
        self._original_trading_bot_place_oco = None
        self._original_trading_bot_place_oco_partial = None
        self._original_trading_bot_place_stop = None
        self._original_trading_bot_place_limit = None
        self._original_register_breakeven = None

        # ── BONGO §1B: simulated breakeven watches (replay equivalent of the live
        # ``_generic_breakeven_monitor_loop`` in trading_bot). Keyed by entry order id.
        # Each row: {symbol, side ("LONG"|"SHORT"), entry_price, profit_threshold,
        # entry_bar_index, sl_order_id, triggered, position_filled}.
        self._breakeven_watches: Dict[str, Dict[str, Any]] = {}

        # Dynamic sizing tracks peak equity for drawdown steps (reference = $2k default).
        self._dynamic_sizing_peak: Optional[float] = None
        self._dynamic_sizing_start_equity: Optional[float] = None
        self._dynamic_sizing_end_carry: Optional[Dict[str, float]] = None

        # Track current bar data for strategy
        self._current_bars: List[Dict] = []
        self._current_symbol: Optional[str] = None
        self._replay_timeframe: Optional[str] = None
        # Cache passthrough historical requests that are stable within a trading day (e.g., 1d)
        # Keyed by (symbol, timeframe, limit, start_iso, end_day_iso)
        self._passthrough_cache: Dict[Tuple[str, str, int, str, str], List[Dict]] = {}
        # Trade-outcome hook dispatch hand-off counter (see note below).
        self._dispatched_trade_count: int = 0
        # ── 2026-05-29 NOTE ──────────────────────────────────────────────
        # An earlier iteration of this engine dispatched closed trades to
        # ``strategy.record_trade_outcome(symbol, pnl)`` for per-symbol
        # circuit-breaker bookkeeping (consec-loss-streak etc.).  A 9m MNQ
        # walk-forward (270d / 9 folds) showed that even with the hook
        # short-circuiting on ``max_consecutive_losses <= 0``, MNQ trade
        # count drifted +4 (140 → 144) versus a no-hook run, dropping ret
        # 143% → 113% and RF 1.74 → 1.29.  Root cause not yet identified
        # (no in-strategy state writes, no cfg mutation observed); the
        # most likely culprit is some shared / cached attribute access on
        # the strategy that subtly changes evaluation order.  Until the
        # exact source is bisected, the hook is intentionally *NOT* wired
        # here — keeping replay strictly engine→fill→analyze.  Strategies
        # that need consec-loss bookkeeping can be fed via the live
        # bot's fill handler in production.
        # ─────────────────────────────────────────────────────────────────
    
    def _process_subbar_fills(self, bar: Any, tick_size: float) -> None:
        """Pending-order simulation for one OHLC row (1m sub-bar or full aggregate bar)."""
        # Refresh the reference price for STOP direction validation. Any SL/TP orders the bracket
        # path places below from a freshly-filled entry will inherit this as their placement_price,
        # which is correct because the entry just filled at (or very near) this price.
        try:
            self.backtest_engine.set_last_close(float(bar["close"]))
        except (KeyError, TypeError, ValueError):
            pass

        # ── BONGO §1B: evaluate breakeven watches BEFORE the per-bar fill check so that
        # any moved stop is in effect for the same bar's fill loop. We deliberately skip
        # the entry bar itself: on the bar that filled the entry the SL order didn't
        # exist yet (it's created at the END of this method's previous invocation), so
        # there's no order to move and no look-ahead risk. From the next bar onward we
        # use OHLC-conservative MFE: bar.high for LONG, bar.low for SHORT. ──
        self._evaluate_breakeven_watches(bar)

        # Process pending orders (check fills)
        filled_this_bar: List[Any] = []
        for order in self.backtest_engine.pending_orders[:]:
            if order not in self.backtest_engine.pending_orders:
                # Cancelled by an earlier fill's OCO sibling-cancel below.
                continue
            if self.backtest_engine._check_order_fill(order, bar, tick_size):
                filled_this_bar.append(order)
                self.backtest_engine.pending_orders.remove(order)
                self.backtest_engine.filled_orders.append(order)
                self.backtest_engine._update_position(order, bar["close"])
                # Eagerly cancel OCO siblings the instant an exit fills.
                # If we wait for the post-loop OCO sweep, both the SL
                # and the TP from the same bracket can fill in one bar
                # (low <= stop AND high >= limit) — the second fill
                # then opens a phantom reverse position because no
                # long position remains.
                oco_group = getattr(order, "oco_group", None)
                if oco_group:
                    for sibling in self.backtest_engine.pending_orders[:]:
                        if getattr(sibling, "oco_group", None) == oco_group:
                            sibling.status = OrderStatus.CANCELLED
                            self.backtest_engine.pending_orders.remove(sibling)

        # When a simulated bracket *entry* stop fills, cancel the opposite pending
        # entry stop for the same symbol (overnight_range places both long and short
        # stops; live broker removes the unfilled side — stale stops caused wrong fills).
        if filled_this_bar:
            for filled in filled_this_bar:
                if (
                    getattr(filled, "stop_loss_price", None) is None
                    or getattr(filled, "take_profit_price", None) is None
                ):
                    continue
                for pending in self.backtest_engine.pending_orders[:]:
                    if pending.order_id == filled.order_id:
                        continue
                    if pending.symbol != filled.symbol:
                        continue
                    if pending.status != OrderStatus.PENDING:
                        continue
                    if pending.order_type != OrderType.STOP:
                        continue
                    if getattr(pending, "oco_group", None):
                        continue
                    if (
                        getattr(pending, "stop_loss_price", None) is None
                        or getattr(pending, "take_profit_price", None) is None
                    ):
                        continue
                    pending.status = OrderStatus.CANCELLED
                    self.backtest_engine.pending_orders.remove(pending)

        # OCO handling: if an exit order fills, cancel the sibling order(s)
        if filled_this_bar and self.backtest_engine.pending_orders:
            for filled in filled_this_bar:
                oco_group = getattr(filled, "oco_group", None)
                if not oco_group:
                    continue
                # Cancel any remaining orders in the same OCO group
                for pending in self.backtest_engine.pending_orders[:]:
                    if getattr(pending, "oco_group", None) == oco_group:
                        pending.status = OrderStatus.CANCELLED
                        self.backtest_engine.pending_orders.remove(pending)

        # Orphan-bracket cleanup: when stacked entries on the same symbol
        # build qty>1 and one bracket's stop_loss fires, the *other*
        # bracket's exit orders remain alive. If position quantity has
        # since dropped to zero, those orphan exits would otherwise fire
        # later and open phantom reverse positions (e.g. an orphan
        # sell-stop opens a SHORT against intent). Cancel any oco-tagged
        # pending orders whose symbol currently has no open position.
        if filled_this_bar and self.backtest_engine.pending_orders:
            flat_symbols = {f.symbol for f in filled_this_bar}
            for sym in flat_symbols:
                if sym in self.backtest_engine.positions:
                    continue
                for pending in self.backtest_engine.pending_orders[:]:
                    if pending.symbol != sym:
                        continue
                    if not getattr(pending, "oco_group", None):
                        continue
                    pending.status = OrderStatus.CANCELLED
                    self.backtest_engine.pending_orders.remove(pending)

        # If an entry order filled and carried bracket prices, place TP/SL exit orders
        # Note: we intentionally do NOT allow these exits to fill in the *same* bar to avoid look-ahead bias.
        for filled in filled_this_bar:
            stop_loss_price = getattr(filled, "stop_loss_price", None)
            take_profit_price = getattr(filled, "take_profit_price", None)
            if stop_loss_price is None or take_profit_price is None:
                continue

            tp_scalp = getattr(filled, "partial_tp_scalp_price", None)
            q_scalp_attr = getattr(filled, "partial_tp_scalp_qty", None)

            pos = self.backtest_engine.positions.get(filled.symbol)
            if pos:
                pos.stop_loss = float(stop_loss_price)
                pos.take_profit = float(take_profit_price)

            exit_side = OrderSide.SELL if filled.side == OrderSide.BUY else OrderSide.BUY

            # BONGO §1A — stage 1: full-size protective stop + scalp TP (OCO); runner armed after scalp fills.
            if (
                tp_scalp is not None
                and q_scalp_attr is not None
                and int(q_scalp_attr) > 0
                and int(q_scalp_attr) < int(filled.quantity)
            ):
                oco_group = f"{filled.order_id}_PTP1"
                q_scalp = int(q_scalp_attr)
                tp_runner = float(getattr(filled, "partial_tp_runner_price", take_profit_price))
                if pos:
                    setattr(pos, "_partial_tp_runner_price", tp_runner)
                    setattr(pos, "_partial_tp_stage2_armed", False)

                # See "Bracket-exit placement_price anchor" rationale below in the
                # standard-bracket branch: anchor the SL's placement_price to the
                # SL stop_price itself so the engine's STOP direction guard doesn't
                # silently reject a same-bar bracket child whose stop level happens
                # to be on the "wrong side" of bar.close. Same anchor on the TP
                # scalp limit for symmetry.
                sl_order_id = self.backtest_engine.place_order(
                    symbol=filled.symbol,
                    side=exit_side,
                    quantity=int(filled.quantity),
                    order_type=OrderType.STOP,
                    stop_price=float(stop_loss_price),
                    price=float(stop_loss_price),
                    placement_price=float(stop_loss_price),
                )
                for o in self.backtest_engine.pending_orders:
                    if o.order_id == sl_order_id:
                        o.oco_group = oco_group
                        o.exit_reason = "stop_loss"
                        break

                tp_scalp_id = self.backtest_engine.place_order(
                    symbol=filled.symbol,
                    side=exit_side,
                    quantity=q_scalp,
                    order_type=OrderType.LIMIT,
                    limit_price=float(tp_scalp),
                    price=float(tp_scalp),
                    placement_price=float(tp_scalp),
                )
                for o in self.backtest_engine.pending_orders:
                    if o.order_id == tp_scalp_id:
                        o.oco_group = oco_group
                        o.exit_reason = "partial_tp_scalp"
                        break
                continue

            # Standard single-target bracket
            oco_group = f"{filled.order_id}_BRACKET"

            # ── Bracket-exit placement_price anchor ───────────────────────────────────
            # These exit orders are *contingent* — they come into existence only after
            # the entry has filled, as part of an OCO bracket. The engine's STOP
            # direction guard (``_check_order_fill``) rejects a BUY STOP whose
            # ``placement_price > stop_price`` (treats it as a "wrong-side BUY STOP,
            # below market"). That guard exists to stop a strategy from posting a
            # naked BUY STOP below the current bid, where a real broker would reject
            # it. But bracket exits are NOT naked: the entry just filled at
            # ``filled.filled_price``, the bracket SL is on the other side of that
            # fill, and there's no scenario in which the broker would refuse it.
            #
            # If we let ``place_order`` fall back to ``self.last_close`` (= the
            # current bar's close), a SHORT entry whose retracement bar closes back
            # above the new SL — exactly the 2026-03-04 MNQ scenario the user
            # flagged — gets its bracket SL silently rejected. Symptom: the SL
            # never fires, the trade rides ``max_hold_bars`` to the timeout exit
            # hundreds of points later (−$1,839.75 on 3 contracts vs the ~−$130
            # / contract a clean SL exit would have produced).
            #
            # Fix: anchor ``placement_price`` to the bracket level itself. For the
            # SL that's ``stop_loss_price``; for the TP it's ``take_profit_price``.
            # With ``pp == stop_price`` the strict-inequality direction-guard check
            # is false → no rejection, the engine fills the order normally as soon
            # as ``bar.high`` / ``bar.low`` touches the level. The TP (LIMIT) has
            # no direction guard today but we anchor it symmetrically anyway so a
            # future LIMIT-side guard wouldn't reintroduce the same bug. ──
            sl_order_id = self.backtest_engine.place_order(
                symbol=filled.symbol,
                side=exit_side,
                quantity=filled.quantity,
                order_type=OrderType.STOP,
                stop_price=float(stop_loss_price),
                price=float(stop_loss_price),
                placement_price=float(stop_loss_price),
            )
            for o in self.backtest_engine.pending_orders:
                if o.order_id == sl_order_id:
                    o.oco_group = oco_group
                    o.exit_reason = "stop_loss"
                    break

            # Take-profit exit (LIMIT)
            tp_order_id = self.backtest_engine.place_order(
                symbol=filled.symbol,
                side=exit_side,
                quantity=filled.quantity,
                order_type=OrderType.LIMIT,
                limit_price=float(take_profit_price),
                price=float(take_profit_price),
                placement_price=float(take_profit_price),
            )
            for o in self.backtest_engine.pending_orders:
                if o.order_id == tp_order_id:
                    o.oco_group = oco_group
                    o.exit_reason = "take_profit"
                    break

            # ── BONGO §1B: link this freshly-placed SL exit to its breakeven watch ──
            # The watch was registered against the *entry* order id (``filled.order_id``)
            # before the entry filled. Now that the SL exit order exists, we can move
            # its stop_price on subsequent bars when MFE crosses the threshold.
            #
            # Multi-stage step-trail support (R23+): strategies that register multiple
            # stage watches per trade use synthetic keys ``{entry_id}_trail{N}``. Link
            # ALL of them to the same SL order so each stage fires once at its own
            # MFE threshold and ratchets the SL price progressively.
            entry_id_str = str(filled.order_id)
            for watch_key, watch in self._breakeven_watches.items():
                if watch_key == entry_id_str or watch_key.startswith(entry_id_str + "_"):
                    watch["sl_order_id"] = str(sl_order_id)
                    watch["entry_bar_index"] = self.backtest_engine.current_bar_index
                    watch["position_filled"] = True

        self._maybe_arm_partial_tp_stage2(filled_this_bar, bar)

    def _maybe_arm_partial_tp_stage2(self, filled_this_bar: List[Any], bar: Any) -> None:
        """After partial scalp leg fills, place breakeven stop + runner TP (OCO) on remaining qty."""
        _ = bar
        for filled in filled_this_bar:
            if getattr(filled, "exit_reason", None) != "partial_tp_scalp":
                continue
            sym = filled.symbol
            pos = self.backtest_engine.positions.get(sym)
            if not pos or pos.quantity <= 0:
                continue
            if getattr(pos, "_partial_tp_stage2_armed", False):
                continue
            runner_px = getattr(pos, "_partial_tp_runner_price", None)
            if runner_px is None:
                continue

            entry_px = float(pos.entry_price)
            qty = int(pos.quantity)
            if qty <= 0:
                continue

            # Partial-TP stage-2 ("runner") siblings — same placement_price anchor
            # rationale as the standard bracket below: SL stop_price for the STOP,
            # TP limit_price for the LIMIT, so the engine's STOP direction guard
            # never silently rejects these contingent OCO children when bar.close
            # has run past the breakeven level by the time stage 2 arms.
            oco2 = f"{filled.order_id}_PTP2_{sym}"
            if pos.side == OrderSide.BUY:
                ex = OrderSide.SELL
                sl_id = self.backtest_engine.place_order(
                    symbol=sym,
                    side=ex,
                    quantity=qty,
                    order_type=OrderType.STOP,
                    stop_price=entry_px,
                    price=entry_px,
                    placement_price=entry_px,
                )
                for o in self.backtest_engine.pending_orders:
                    if o.order_id == sl_id:
                        o.oco_group = oco2
                        o.exit_reason = "stop_loss"
                        break
                tp_id = self.backtest_engine.place_order(
                    symbol=sym,
                    side=ex,
                    quantity=qty,
                    order_type=OrderType.LIMIT,
                    limit_price=float(runner_px),
                    price=float(runner_px),
                    placement_price=float(runner_px),
                )
                for o in self.backtest_engine.pending_orders:
                    if o.order_id == tp_id:
                        o.oco_group = oco2
                        o.exit_reason = "take_profit"
                        break
            else:
                ex = OrderSide.BUY
                sl_id = self.backtest_engine.place_order(
                    symbol=sym,
                    side=ex,
                    quantity=qty,
                    order_type=OrderType.STOP,
                    stop_price=entry_px,
                    price=entry_px,
                    placement_price=entry_px,
                )
                for o in self.backtest_engine.pending_orders:
                    if o.order_id == sl_id:
                        o.oco_group = oco2
                        o.exit_reason = "stop_loss"
                        break
                tp_id = self.backtest_engine.place_order(
                    symbol=sym,
                    side=ex,
                    quantity=qty,
                    order_type=OrderType.LIMIT,
                    limit_price=float(runner_px),
                    price=float(runner_px),
                    placement_price=float(runner_px),
                )
                for o in self.backtest_engine.pending_orders:
                    if o.order_id == tp_id:
                        o.oco_group = oco2
                        o.exit_reason = "take_profit"
                        break
            setattr(pos, "_partial_tp_stage2_armed", True)

    def _replay_force_flat_cutoff_minutes_et(self) -> Optional[int]:
        """US/Eastern ``HH:MM`` after which replay must be flat (prop day cap). Default ``16:00``.

        Cached on first call per replay run: the value is derived from
        immutable TOML keys (``timing.replay_force_flat_et`` falling back to
        ``signal.flat_before``) that never change mid-replay. Caching this
        once cuts the per-bar cost from ~6 µs × 17.4 k bars = ~103 ms
        (third-largest hotpath after Tier 5) down to a single resolve.
        ``_install_fast_strategy_mocks_restore`` clears the cache as part of
        teardown so a follow-up replay with a different config is correct.
        """
        cached = getattr(self, "_replay_force_flat_minutes_cache", _UNSET)
        if cached is not _UNSET:
            return cached
        raw = "16:00"
        cfg = getattr(self.strategy, "_cfg", None)
        if cfg is not None and hasattr(cfg, "get_str"):
            v = cfg.get_str("timing.replay_force_flat_et", "16:00")
            if v is not None and str(v).strip():
                raw = str(v).strip()
            else:
                fb = cfg.get_str("signal.flat_before", None)
                if fb is not None and str(fb).strip():
                    raw = str(fb).strip()
        result = parse_replay_force_flat_et_minutes(raw)
        self._replay_force_flat_minutes_cache = result
        return result

    def _replay_cancel_all_pending_orders(self) -> None:
        for order in self.backtest_engine.pending_orders[:]:
            order.status = OrderStatus.CANCELLED
        self.backtest_engine.pending_orders.clear()

    def _replay_market_flat_all_open_positions(self, bar: Any) -> None:
        """Market-close every open position at this bar's open; tag exit reason for JSON.

        Accepts both ``pd.Series`` (slow loop) and ``_BarRow`` (fast loop). The
        early-return guard validates only that the bar exposes the OHLC fields
        we actually need (``open`` access + ``name`` attribute). Previously
        this required ``isinstance(bar, pd.Series)``, which silently no-op'd
        when ``BACKTEST_FAST_LOOP=1`` was in effect — bypassing both the EOD
        cutoff and the date-rollover guard. Symptom: ``overnight_range``
        positions persisted across calendar days in fast-loop walkforwards
        (round-14 regression).
        """
        try:
            px = float(bar["open"])
            ts_name = bar.name
        except (KeyError, AttributeError, TypeError, ValueError):
            return
        for _ in range(32):  # guard against pathological loops
            if not self.backtest_engine.positions:
                break
            pos = next(iter(self.backtest_engine.positions.values()))
            closing_side = OrderSide.SELL if pos.side == OrderSide.BUY else OrderSide.BUY
            close_order_id = self.backtest_engine.place_order(
                symbol=pos.symbol,
                side=closing_side,
                quantity=pos.quantity,
                order_type=OrderType.MARKET,
                price=px,
            )
            filled = False
            for order in self.backtest_engine.pending_orders[:]:
                if order.order_id == close_order_id:
                    order.filled_price = px
                    order.filled_timestamp = ts_name
                    order.status = OrderStatus.FILLED
                    setattr(order, "exit_reason", "replay_force_flat_et")
                    self.backtest_engine._update_position(order, px)
                    self.backtest_engine.filled_orders.append(order)
                    self.backtest_engine.pending_orders.remove(order)
                    filled = True
                    break
            if not filled:
                logger.warning("replay force-flat: failed to fill synthetic market for %s", pos.symbol)
                break

    def _maybe_replay_force_flat_et(
        self,
        bar: pd.Series,
        *,
        cutoff_minutes: Optional[int],
        bar_minutes_et: int,
        bar_et_date: Optional[date] = None,
    ) -> None:
        """Force-flat replay positions when the bar clock crosses the
        configured EOD cutoff OR when the bar date advances past the
        previous bar's date.

        Date-rollover guard: a position that opens late in the day
        (e.g. 15:55 ET) and is followed by a multi-hour gap (16:00 ET
        through 18:00 ET — the CME settlement window) would otherwise
        bypass the 16:00-cutoff check entirely if the dataset's first
        bar after the gap is on the NEXT trading day at 18:00 ET. That
        bar's ``bar_minutes_et`` is 1080 (≥ 960), so the existing branch
        catches it — but to make the invariant explicit, we also fire
        on any bar whose ET date differs from the previously-recorded
        ``_last_replay_bar_et_date``. This is the regression fix for
        the round-14 ``overnight_range`` walkforward where some MNQ/MGC
        positions persisted for 7-15 calendar days.
        """
        if cutoff_minutes is None:
            return
        if not self.backtest_engine.positions and not self.backtest_engine.pending_orders:
            self._last_replay_bar_et_date = bar_et_date
            return

        past_cutoff = bar_minutes_et >= cutoff_minutes
        # Date-rollover guard: if today's date doesn't match the previous
        # bar's date AND we have open state, flat unconditionally.
        prev_date = getattr(self, "_last_replay_bar_et_date", None)
        date_rolled = (
            prev_date is not None
            and bar_et_date is not None
            and prev_date != bar_et_date
        )

        if past_cutoff or date_rolled:
            self._replay_cancel_all_pending_orders()
            if self.backtest_engine.positions:
                self._replay_market_flat_all_open_positions(bar)

        self._last_replay_bar_et_date = bar_et_date

    async def replay(
        self,
        symbol: str,
        bars: List[Dict],
        tick_size: float = 0.25,
        replay_timeframe: Optional[str] = None,
        bars_1m: Optional[List[Dict]] = None,
        dynamic_sizing_carry: Optional[Dict[str, Any]] = None,
    ) -> BacktestResult:
        """
        Replay strategy on historical bars.

        Args:
            symbol: Trading symbol
            bars: List of historical bar dicts with OHLCV data (or DataFrame)
            tick_size: Minimum price increment
            bars_1m: Optional 1m bars for intrabar fill simulation inside each aggregate bar

        Returns:
            BacktestResult with performance metrics

        Note:
            For callers that already hold a parsed OHLCV DataFrame (the
            walk-forward executor + parquet cache path), prefer :meth:`replay_df`
            which skips the list-of-dicts → DataFrame round-trip entirely.
            This method exists for backward compatibility with sample-data
            and ad-hoc test callers; it builds the DataFrame from ``bars``
            and forwards to the shared core.
        """
        # Handle DataFrame input (from sample data)
        if isinstance(bars, pd.DataFrame):
            # Convert DataFrame to list of dicts
            bars_list = []
            for idx, row in bars.iterrows():
                bars_list.append({
                    'timestamp': idx if isinstance(idx, datetime) else pd.to_datetime(idx),
                    'open': float(row.get('open', row.get('Open', 0))),
                    'high': float(row.get('high', row.get('High', 0))),
                    'low': float(row.get('low', row.get('Low', 0))),
                    'close': float(row.get('close', row.get('Close', 0))),
                    'volume': int(row.get('volume', row.get('Volume', 0)))
                })
            bars = bars_list

        if isinstance(bars, list) and bars and isinstance(bars[0], dict):
            bars = sanitize_replay_bars_list(bars)

        # Convert bars to DataFrame for BacktestEngine.
        df = self._bars_to_dataframe(bars)
        return await self.replay_df(
            symbol=symbol,
            df=df,
            bars=bars,
            tick_size=tick_size,
            replay_timeframe=replay_timeframe,
            bars_1m=bars_1m,
            dynamic_sizing_carry=dynamic_sizing_carry,
        )

    async def replay_df(
        self,
        symbol: str,
        df: pd.DataFrame,
        bars: Optional[List[Dict]] = None,
        tick_size: float = 0.25,
        replay_timeframe: Optional[str] = None,
        bars_1m: Optional[List[Dict]] = None,
        dynamic_sizing_carry: Optional[Dict[str, Any]] = None,
    ) -> BacktestResult:
        """
        Replay entry point that accepts a pre-parsed OHLCV DataFrame directly.

        **Tier 1.1 fast path.** The walk-forward + parquet-cache pipeline already
        loads bars as a DataFrame; this method skips the
        ``list[dict] → _bars_to_dataframe → DataFrame`` round-trip the legacy
        ``replay(bars=...)`` path pays. Saves a vectorized DataFrame rebuild
        per replay (~5-10 ms × N folds across a sweep).

        ``bars`` is optional and only needed by the slow ``_call_strategy_analyze``
        path (BACKTEST_FAST_LOOP=0) and by the fast-mode strategy mocks that slice
        ``bars[start:cutoff]`` to satisfy ``get_historical_data`` requests. When
        omitted, it is derived from ``df`` via
        :func:`core.backtest.ohlcv.replay_bars_from_ohlcv_df` on first need.

        The hot replay loop, intrabar fills, equity bookkeeping, and result
        construction are byte-identical to ``replay()`` — both methods share
        the same engine code below.
        """
        if bars is None:
            from core.backtest.ohlcv import replay_bars_from_ohlcv_df
            bars = replay_bars_from_ohlcv_df(df, deroll=False)
        elif isinstance(bars, list) and bars and isinstance(bars[0], dict):
            bars = sanitize_replay_bars_list(bars)

        logger.info(f"🔄 Starting strategy replay: {self.strategy.config.name} on {symbol}")
        logger.info(f"   Bars: {len(df)}")
        if len(df) > 0:
            logger.info(f"   Period: {df.index[0]} to {df.index[-1]}")

        self._current_symbol = symbol
        self._current_bars = bars
        self._replay_timeframe = replay_timeframe
        self._seed_dynamic_sizing_carry(dynamic_sizing_carry)

        # Tier 4: invalidate per-replay caches that key off the strategy's
        # TOML config. A new replay() invocation may be for the same engine
        # instance but a different fold's config — flush so the lazy resolve
        # picks up the new values on first per-bar call.
        self._replay_force_flat_minutes_cache = _UNSET
        # Date-rollover tracker for the EOD force-flat guard.  Reset per replay
        # so a new fold starts clean (no carryover from prior fold's last bar).
        self._last_replay_bar_et_date = None

        # Reset breakeven-watch state so back-to-back replays (e.g. walk-forward folds
        # sharing a StrategyReplayEngine) don't carry watches across runs.
        self._breakeven_watches.clear()
        
        # Intercept strategy's order placement methods
        self._intercept_strategy_methods()
        
        # Also intercept trading bot's order placement methods (for strategies that call them directly)
        self._intercept_trading_bot_methods()
        
        try:
            if bars_1m:
                bars_1m_sorted, bars_1m_ns = sort_bars_1m_for_replay(
                    sanitize_replay_bars_list(list(bars_1m))
                )
            else:
                bars_1m_sorted, bars_1m_ns = [], []
            agg_minutes = replay_timeframe_to_minutes(replay_timeframe)
            if bars_1m_sorted:
                logger.info(
                    "   Intrabar fills: %d 1m bars inside each %s aggregate bar (%d min window)",
                    len(bars_1m_sorted),
                    replay_timeframe or "?",
                    agg_minutes,
                )

            # Run strategy on each bar. The ``BACKTEST_FAST_LOOP=1`` opt-in swaps
            # the per-bar ``pd.Series`` allocation that ``df.iterrows`` emits for
            # a lightweight ``_BarRow`` view over pre-extracted NumPy column
            # arrays — same dict-key / ``.name`` surface the engine uses, ~10×
            # lighter to construct. Regression-pinned in
            # ``tests/test_backtest_fast_loop_parity.py``.
            fast_loop = _fast_loop_enabled()
            if fast_loop:
                bar_iter: Iterator[Tuple[int, Any, Any]] = _iter_bars_fast(df)
            else:
                bar_iter = (
                    (i, ts, row) for i, (ts, row) in enumerate(df.iterrows())
                )

            # Fast-mode mocks installed ONCE per replay (instead of per-bar).
            # The closure captures the full bars list + a precomputed
            # int64-ns timestamp array and reads ``self.backtest_engine.current_bar_index``
            # as the live cursor. Bisect-driven; collapses the per-call cost
            # from O(n × parse) to O(log n). See ``_install_fast_strategy_mocks``.
            fast_mocks_restore = None
            if fast_loop:
                fast_mocks_restore = self._install_fast_strategy_mocks(
                    replay_symbol=symbol,
                    bars=bars,
                    df=df,
                )

            # ── Tier 5: Active-window gate ─────────────────────────────────
            # If the strategy declares an ET active window via
            # ``replay_active_window_et``, precompute the per-bar mask once so
            # the hot loop's gate check is a single bool indexed lookup. Bars
            # outside ALL windows AND with no open positions skip ``analyze()``
            # entirely (engine still processes fills + equity for the bar).
            # Strategies leaving the attribute None pay zero overhead.
            active_window_mask = self._build_replay_active_window_mask(df)

            # ── Tier 3: Precompute hook ────────────────────────────────────
            # Strategies can override ``replay_precompute_indicators(df)`` to
            # vectorize per-bar work (ATR / EMA / session ranges) into a
            # single pass before the loop starts. Default base impl is a
            # no-op; any exception falls back to the per-bar path so a bad
            # override can't break the run.
            try:
                precomp = getattr(self.strategy, "replay_precompute_indicators", None)
                if callable(precomp):
                    precomp(df)
            except Exception as exc:
                logger.warning(
                    "Strategy %s.replay_precompute_indicators failed: %s — falling back to per-bar path",
                    type(self.strategy).__name__, exc,
                )

            # Tier 4: Resolve the prop-day force-flat cutoff ONCE per replay
            # (was profiled at 103 ms / 17.4 k calls). The value is derived
            # from immutable TOML keys, so caching it before the loop is
            # functionally identical and saves ~6 µs × n_bars of overhead.
            cutoff_minutes = self._replay_force_flat_cutoff_minutes_et()
            for i, timestamp, bar in bar_iter:
                self.backtest_engine.current_bar_index = i
                self.backtest_engine.current_timestamp = timestamp

                # Bar clock before intrabar fills so strategies can purge stale resting orders
                # (e.g. overnight_range stop entries) before OHLC is applied to pending brackets.
                if hasattr(self.trading_bot, "_current_bar_timestamp"):
                    self.trading_bot._current_bar_timestamp = timestamp
                else:
                    setattr(self.trading_bot, "_current_bar_timestamp", timestamp)

                bar_minutes_et = bar_minutes_since_midnight_et(timestamp)
                past_cutoff = cutoff_minutes is not None and bar_minutes_et >= cutoff_minutes

                try:
                    hook = getattr(self.strategy, "replay_before_bar_fills", None)
                    if hook is not None:
                        await hook()
                except Exception as hook_err:
                    logger.error("Strategy replay_before_bar_fills failed: %s", hook_err, exc_info=True)

                # ET date of this bar — used by ``_maybe_replay_force_flat_et``'s
                # rollover guard to flatten any position that survives across
                # a calendar-day boundary even if no bar at the configured
                # cutoff (e.g. 16:00 ET) appeared in the data.
                try:
                    _ts = pd.Timestamp(timestamp)
                    if _ts.tzinfo is None:
                        _ts = _ts.tz_localize("UTC")
                    bar_et_date = _ts.tz_convert(ZoneInfo("America/New_York")).date()
                except Exception:
                    bar_et_date = None

                self._maybe_replay_force_flat_et(
                    bar,
                    cutoff_minutes=cutoff_minutes,
                    bar_minutes_et=bar_minutes_et,
                    bar_et_date=bar_et_date,
                )

                if not past_cutoff:
                    for sub in intrabar_series_iter(
                        timestamp, bar, bars_1m_sorted, bars_1m_ns, agg_minutes
                    ):
                        self._process_subbar_fills(sub, tick_size)
                
                # Update unrealized P&L
                if self.backtest_engine.positions:
                    self.backtest_engine._update_unrealized_pnl(bar)
                
                # Record equity
                equity = self.backtest_engine._calculate_equity()
                self.backtest_engine.equity_curve.append((timestamp, equity))

                if past_cutoff:
                    continue

                # Call strategy's analyze() method with current bar data
                try:
                    # Update strategy's active_positions from backtest engine
                    self._sync_strategy_positions()

                    # ── Tier 5 active-window gate ──────────────────────────
                    # Skip analyze() when ALL of these hold:
                    #   1. Strategy declared a replay_active_window_et
                    #   2. This bar's ET time is outside every declared window
                    #   3. No open positions (so the strategy has nothing to manage)
                    # The engine has already processed fills + equity above, so
                    # skipping only suppresses signal-detection work. Strategies
                    # without a window declaration always pass through.
                    if (
                        active_window_mask is not None
                        and not active_window_mask[i]
                        and not self.backtest_engine.positions
                    ):
                        continue

                    if fast_loop:
                        # Fast path: no per-bar ``bars[:i+1]`` allocation, no
                        # ``MockTradingBot.bars`` reassignment, no resample-cache
                        # clear. Mocks were installed once at the top of replay()
                        # and read the cursor via ``self.backtest_engine.current_bar_index``.
                        # The rare resample path (``MockTradingBot._resample_native_to``)
                        # reads ``_fast_cursor_index`` and slices on demand for correctness.
                        if hasattr(self.trading_bot, "_fast_cursor_index"):
                            self.trading_bot._fast_cursor_index = i
                        if hasattr(self.trading_bot, "bars_1m") and bars_1m_sorted:
                            t_excl = pd.Timestamp(timestamp) + pd.Timedelta(minutes=agg_minutes)
                            self.trading_bot.bars_1m = [
                                b
                                for b in bars_1m_sorted
                                if pd.Timestamp(b.get("timestamp")) < t_excl
                            ]
                        signal = await self.strategy.analyze(symbol)
                    else:
                        # Get bars up to current point for strategy analysis
                        current_bars_for_strategy = bars[:i+1]

                        # Update mock trading bot's bars to current set
                        if hasattr(self.trading_bot, 'bars'):
                            self.trading_bot.bars = current_bars_for_strategy
                            # Coarser TF requests delegate to MockTradingBot resampling, which caches by
                            # target timeframe only; invalidate when the prefix grows.
                            rc = getattr(self.trading_bot, "_resampled_cache", None)
                            if isinstance(rc, dict):
                                rc.clear()
                        if hasattr(self.trading_bot, "bars_1m") and bars_1m_sorted:
                            t_excl = pd.Timestamp(timestamp) + pd.Timedelta(minutes=agg_minutes)
                            self.trading_bot.bars_1m = [
                                b
                                for b in bars_1m_sorted
                                if pd.Timestamp(b.get("timestamp")) < t_excl
                            ]

                        # Call strategy analyze (it will use trading_bot.get_historical_data internally)
                        # Also mock get_open_positions / get_market_quote so the strategy behaves like live
                        signal = await self._call_strategy_analyze(symbol, current_bars_for_strategy)

                    if signal:
                        # Strategy wants to place an order - call execute which uses place_bracket_order
                        # This will be intercepted by our _simulate_place_bracket_order
                        try:
                            await self.strategy.execute(signal)
                        except Exception as exec_err:
                            logger.error(f"Error in strategy.execute: {exec_err}", exc_info=True)
                
                except Exception as e:
                    logger.error(f"Strategy error at bar {i}: {e}", exc_info=True)
            
            # Close any remaining positions at end
            if self.backtest_engine.positions:
                final_bar = df.iloc[-1]
                for pos in list(self.backtest_engine.positions.values()):
                    close_order_id = self.backtest_engine.place_order(
                        symbol=pos.symbol,
                        side=OrderSide.SELL if pos.side == OrderSide.BUY else OrderSide.BUY,
                        quantity=pos.quantity,
                        order_type=OrderType.MARKET,
                        price=final_bar['close']
                    )
                    # Force fill at final price
                    for order in self.backtest_engine.pending_orders:
                        if order.order_id == close_order_id:
                            order.filled_price = final_bar['close']
                            order.filled_timestamp = final_bar.name
                            order.status = OrderStatus.FILLED
                            self.backtest_engine._update_position(order, final_bar['close'])
                            self.backtest_engine.filled_orders.append(order)
                            self.backtest_engine.pending_orders.remove(order)
                            break
            
            # Build result
            result = self.backtest_engine._build_result(
                symbol=symbol,
                strategy_name=self.strategy.config.name,
                start_date=df.index[0],
                end_date=df.index[-1]
            )

            from core.backtest.dynamic_sizing import dynamic_sizing_enabled, end_carry

            if dynamic_sizing_enabled():
                eq = self._sim_equity_for_dynamic_sizing()
                peak = float(self._dynamic_sizing_peak or eq)
                self._dynamic_sizing_end_carry = end_carry(eq, peak)
            
            logger.info(f"✅ Strategy replay complete:")
            logger.info(f"   Total Trades: {result.total_trades}")
            logger.info(f"   Win Rate: {result.win_rate:.1f}%")
            logger.info(f"   Total P&L: ${result.total_pnl:,.2f}")
            
            return result
        
        finally:
            # Restore original methods
            if fast_mocks_restore is not None:
                try:
                    fast_mocks_restore()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("fast strategy-mocks restore failed: %s", exc)
            self._restore_strategy_methods()
            self._restore_trading_bot_methods()
    
    def _build_replay_active_window_mask(
        self, df: pd.DataFrame
    ) -> Optional[np.ndarray]:
        """Build a per-bar boolean array marking bars inside ``replay_active_window_et``.

        Returns ``None`` when the strategy hasn't declared a window (the common
        case for legacy strategies) — callers treat ``None`` as "always
        active" and pay zero per-bar gate overhead.

        The mask is computed vectorized via NumPy so a 17 k-bar 3-month
        replay costs ~2 ms once, vs ~5 µs × 17 k = ~85 ms if checked
        per-bar inside the Python loop. The savings show up for any strategy
        whose active window is a strict subset of the bar stream (most are —
        ``morning_range_reversion`` is 9 hours of a 24h bar stream).

        Args:
            df: Replay DataFrame with naive-UTC index. The strategy's
                ``session_zone`` (or ``session_timezone`` / a hard-coded
                ``America/New_York`` default) is used to convert each bar
                timestamp to ET before the window check.

        Returns:
            Boolean ``np.ndarray`` of shape ``(len(df),)`` or ``None``.
            ``mask[i] == True`` means bar ``i`` is INSIDE at least one
            declared window.
        """
        window = getattr(self.strategy, "replay_active_window_et", None)
        if not window:  # None or empty list both mean "always active"
            return None

        # Normalize: accept a single (start, end) tuple OR a list of them.
        if isinstance(window, tuple) and len(window) == 2 and not isinstance(
            window[0], (list, tuple)
        ):
            windows: List[Tuple[Any, Any]] = [window]  # type: ignore[list-item]
        else:
            windows = list(window)  # type: ignore[arg-type]

        # Resolve the ET timezone (prefer the strategy's own session_zone
        # so it stays in sync with the strategy's own clock; fall back to
        # America/New_York). Robust to strategies that store the zone as a
        # pytz tz, a zoneinfo, or a str.
        tz_attr = getattr(self.strategy, "_tz", None) or getattr(
            self.strategy, "timezone", None
        )
        try:
            if tz_attr is not None:
                # Both pytz and zoneinfo expose .zone or .key respectively
                # — but tz_convert accepts the object directly.
                tz = tz_attr
            else:
                from zoneinfo import ZoneInfo
                tz = ZoneInfo("America/New_York")
        except Exception:
            return None  # bail safely if tz resolution fails

        try:
            # df.index is naive-UTC (the canonical replay shape). Localize
            # to UTC then convert to ET — single vectorized pass.
            idx = df.index
            if getattr(idx, "tz", None) is None:
                idx_utc = idx.tz_localize("UTC")
            else:
                idx_utc = idx
            idx_et = idx_utc.tz_convert(tz)
            # ``hour`` / ``minute`` are vectorized accessors on DatetimeIndex
            # — extract minute-of-day once and compare against window bounds.
            mins_of_day = idx_et.hour * 60 + idx_et.minute
        except Exception:
            return None

        mask = np.zeros(len(df), dtype=bool)
        for w in windows:
            try:
                start_t, end_t = w
                start_min = int(start_t.hour) * 60 + int(start_t.minute)
                end_min = int(end_t.hour) * 60 + int(end_t.minute)
            except Exception:
                continue
            if start_min <= end_min:
                # Same-day window (e.g. 07:00-16:00 ET)
                mask |= (mins_of_day >= start_min) & (mins_of_day < end_min)
            else:
                # Wrapping window (e.g. 19:00 ET → 09:30 ET next day)
                # — flagged as either >= start OR < end.
                mask |= (mins_of_day >= start_min) | (mins_of_day < end_min)

        return mask

    def _bars_to_dataframe(self, bars: List[Dict]) -> pd.DataFrame:
        """Convert bar dicts to pandas DataFrame.

        Profile-driven rewrite: the legacy implementation called ``pd.to_datetime``
        on every bar **twice** (once per loop iter, once in the index
        comprehension), which dominated replay() wall time at ~2.9 s of a
        ~6 s 3-month MNQ replay even though the timestamps coming from
        :func:`core.backtest.ohlcv.replay_bars_from_ohlcv_df` are already native
        Python ``datetime`` objects.

        The fast path here:
          1. Pulls OHLCV values into preallocated NumPy arrays in a single Python loop.
          2. Builds the index via ``pd.DatetimeIndex(list)`` — pandas detects
             pre-parsed datetimes and skips per-element parsing.
          3. Assembles the DataFrame from the arrays (column-oriented), which is
             materially faster than DataFrame-from-list-of-dicts.

        The slow path (pd.to_datetime + dict assembly) is only taken when a
        non-datetime timestamp appears (string/int/float) — rare in production
        but kept for back-compat with tests and ad-hoc callers.
        """
        import pandas as pd
        import numpy as np
        from datetime import datetime

        n = len(bars)
        if n == 0:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

        # Fast path: timestamps are already datetime / pd.Timestamp objects.
        # Detect on the first bar (cheap) and fall back if any bar lies about
        # its shape mid-stream. Numbers/strings hit the slow path so we don't
        # silently mis-parse epoch seconds vs ms.
        first_ts = bars[0].get("timestamp")
        if isinstance(first_ts, (datetime, pd.Timestamp)):
            opens = np.empty(n, dtype=np.float64)
            highs = np.empty(n, dtype=np.float64)
            lows = np.empty(n, dtype=np.float64)
            closes = np.empty(n, dtype=np.float64)
            volumes = np.empty(n, dtype=np.int64)
            timestamps: List = [None] * n
            for i, bar in enumerate(bars):
                ts = bar.get("timestamp")
                if not isinstance(ts, (datetime, pd.Timestamp)):
                    timestamps = None  # type: ignore[assignment]
                    break
                timestamps[i] = ts
                opens[i] = bar.get("open", bar.get("o", 0.0))
                highs[i] = bar.get("high", bar.get("h", 0.0))
                lows[i] = bar.get("low", bar.get("l", 0.0))
                closes[i] = bar.get("close", bar.get("c", 0.0))
                volumes[i] = int(bar.get("volume", bar.get("v", 0)))
            if timestamps is not None:
                idx = pd.DatetimeIndex(timestamps)
                return pd.DataFrame(
                    {"open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes},
                    index=idx,
                )

        # Legacy slow path — only for non-datetime timestamps (string/int/float).
        data = []
        for bar in bars:
            timestamp = bar.get('timestamp')
            if isinstance(timestamp, str):
                timestamp = pd.to_datetime(timestamp)
            elif isinstance(timestamp, (int, float)):
                timestamp = pd.to_datetime(timestamp, unit='s')

            data.append({
                'open': float(bar.get('open', bar.get('o', 0))),
                'high': float(bar.get('high', bar.get('h', 0))),
                'low': float(bar.get('low', bar.get('l', 0))),
                'close': float(bar.get('close', bar.get('c', 0))),
                'volume': int(bar.get('volume', bar.get('v', 0)))
            })

        df = pd.DataFrame(data, index=[pd.to_datetime(b.get('timestamp')) for b in bars])
        return df
    
    def _intercept_strategy_methods(self):
        """Intercept strategy's order placement methods to simulate execution."""
        # Expose the backtest engine on the strategy + mock bot so analyze()
        # can interrogate live positions / pending orders without going
        # through the broker.
        try:
            setattr(self.trading_bot, "backtest_engine", self.backtest_engine)
            setattr(self.strategy, "_replay_engine", self.backtest_engine)
        except Exception:
            pass

        # Intercept place_bracket_order
        if hasattr(self.strategy, 'place_bracket_order'):
            self._original_place_bracket_order = self.strategy.place_bracket_order
            self.strategy.place_bracket_order = self._simulate_place_bracket_order
        
        # Intercept place_market_order if it exists
        if hasattr(self.strategy, 'place_market_order'):
            self._original_place_market_order = self.strategy.place_market_order
            self.strategy.place_market_order = self._simulate_place_market_order
    
    def _restore_strategy_methods(self):
        """Restore original strategy methods."""
        if self._original_place_bracket_order:
            self.strategy.place_bracket_order = self._original_place_bracket_order
        if self._original_place_market_order:
            self.strategy.place_market_order = self._original_place_market_order
    
    def _intercept_trading_bot_methods(self):
        """Intercept trading bot's order placement methods for backtest simulation."""
        # Intercept place_oco_bracket_with_stop_entry
        if hasattr(self.trading_bot, 'place_oco_bracket_with_stop_entry'):
            self._original_trading_bot_place_oco = self.trading_bot.place_oco_bracket_with_stop_entry
            self.trading_bot.place_oco_bracket_with_stop_entry = self._simulate_place_oco_bracket
        
        if hasattr(self.trading_bot, "place_oco_bracket_with_stop_entry_partial_tp"):
            self._original_trading_bot_place_oco_partial = (
                self.trading_bot.place_oco_bracket_with_stop_entry_partial_tp
            )
            self.trading_bot.place_oco_bracket_with_stop_entry_partial_tp = (
                self._simulate_place_oco_partial_tp
            )
        
        # Intercept place_stop_order
        if hasattr(self.trading_bot, 'place_stop_order'):
            self._original_trading_bot_place_stop = self.trading_bot.place_stop_order
            self.trading_bot.place_stop_order = self._simulate_place_stop_order
        
        # Intercept place_limit_order
        if hasattr(self.trading_bot, 'place_limit_order'):
            self._original_trading_bot_place_limit = self.trading_bot.place_limit_order
            self.trading_bot.place_limit_order = self._simulate_place_limit_order

        # ── BONGO §1B: intercept the live breakeven-watch registration so the
        # replay engine handles it deterministically instead of letting the live
        # ``_generic_breakeven_monitor_loop`` make REST calls against the broker. ──
        if hasattr(self.trading_bot, "register_generic_breakeven_watch"):
            self._original_register_breakeven = self.trading_bot.register_generic_breakeven_watch
            self.trading_bot.register_generic_breakeven_watch = self._simulate_register_breakeven_watch
    
    def _restore_trading_bot_methods(self):
        """Restore original trading bot methods."""
        if self._original_trading_bot_place_oco:
            self.trading_bot.place_oco_bracket_with_stop_entry = self._original_trading_bot_place_oco
        if self._original_trading_bot_place_oco_partial:
            self.trading_bot.place_oco_bracket_with_stop_entry_partial_tp = (
                self._original_trading_bot_place_oco_partial
            )
        if self._original_trading_bot_place_stop:
            self.trading_bot.place_stop_order = self._original_trading_bot_place_stop
        if self._original_trading_bot_place_limit:
            self.trading_bot.place_limit_order = self._original_trading_bot_place_limit
        if self._original_register_breakeven:
            self.trading_bot.register_generic_breakeven_watch = self._original_register_breakeven
    
    def _seed_dynamic_sizing_carry(self, carry: Optional[Dict[str, Any]] = None) -> None:
        from core.backtest.dynamic_sizing import carry_from_mapping, dynamic_sizing_enabled

        self._dynamic_sizing_end_carry = None
        if not dynamic_sizing_enabled():
            self._dynamic_sizing_start_equity = None
            self._dynamic_sizing_peak = None
            return
        state = carry_from_mapping(carry)
        self._dynamic_sizing_start_equity = float(state["equity"])
        self._dynamic_sizing_peak = float(state["peak"])

    def get_dynamic_sizing_carry(self) -> Optional[Dict[str, float]]:
        from core.backtest.dynamic_sizing import dynamic_sizing_enabled, end_carry

        if not dynamic_sizing_enabled():
            return None
        if self._dynamic_sizing_end_carry is not None:
            return dict(self._dynamic_sizing_end_carry)
        if self._dynamic_sizing_start_equity is None:
            return None
        eq = self._sim_equity_for_dynamic_sizing()
        peak = float(self._dynamic_sizing_peak or self._dynamic_sizing_start_equity)
        return end_carry(eq, peak)

    def _sim_equity_for_dynamic_sizing(self) -> float:
        """Prop-style equity ($2k chain + fold PnL), not $50k engine capital."""
        from core.backtest.dynamic_sizing import dynamic_sizing_enabled, reference_equity

        if not dynamic_sizing_enabled():
            return float(self.backtest_engine._calculate_equity())
        start = self._dynamic_sizing_start_equity
        if start is None:
            start = reference_equity()
        realized = sum(float(t.pnl) for t in self.backtest_engine.trades)
        unrealized = 0.0
        if self.backtest_engine.positions:
            for pos in self.backtest_engine.positions.values():
                unrealized += float(getattr(pos, "unrealized_pnl", 0.0) or 0.0)
        return float(start) + realized + unrealized

    def _adjust_dynamic_sizing(self, quantity: int) -> int:
        """Scale qty from drawdown vs peak on prop notional ($2k), not replay engine capital."""
        from core.backtest.dynamic_sizing import (
            apply_dynamic_sizing,
            dynamic_sizing_enabled,
            reference_equity,
        )

        if not dynamic_sizing_enabled():
            return int(quantity)
        try:
            qty = max(1, int(quantity))
        except (TypeError, ValueError):
            qty = 1
        ref = reference_equity()
        eq = self._sim_equity_for_dynamic_sizing()
        if self._dynamic_sizing_peak is None:
            self._dynamic_sizing_peak = max(
                float(self._dynamic_sizing_start_equity or ref),
                eq,
            )
        else:
            self._dynamic_sizing_peak = max(float(self._dynamic_sizing_peak), eq)
        return apply_dynamic_sizing(qty, eq, float(self._dynamic_sizing_peak), ref)

    async def _simulate_place_bracket_order(
        self,
        symbol: str,
        side: str,
        quantity: int,
        entry_price: float,
        stop_loss_price: float,
        take_profit_price: float,
        enable_breakeven: bool = False,
        breakeven_profit_threshold: Optional[float] = None,
        breakeven_offset: float = 0.0,
        *,
        partial_tp_enabled: bool = False,
        partial_tp_scalp_r: float = 1.0,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """
        Simulate bracket order placement in backtest.
        
        This intercepts the strategy's place_bracket_order call and
        simulates execution using the BacktestEngine.
        """
        quantity = self._adjust_dynamic_sizing(quantity)

        market_entry = bool(kwargs.get("market_entry", False))
        strategy_name = kwargs.get("strategy_name")
        if partial_tp_enabled and int(quantity) >= 2:
            return await self._simulate_place_oco_partial_tp(
                symbol,
                side,
                quantity,
                entry_price,
                stop_loss_price,
                take_profit_price,
                account_id=None,
                enable_breakeven=enable_breakeven,
                strategy_name=strategy_name,
                scalp_r_multiple=float(partial_tp_scalp_r or 1.0),
            )
        logger.debug(f"📝 Simulating bracket order: {side} {quantity} {symbol} @ {entry_price:.2f}")
        
        # Convert side to OrderSide
        order_side = OrderSide.BUY if side.upper() == "BUY" else OrderSide.SELL
        
        if market_entry:
            if self.backtest_engine.current_timestamp and self._current_bars:
                idx = min(
                    max(0, self.backtest_engine.current_bar_index),
                    len(self._current_bars) - 1,
                )
                current_bar = self._current_bars[idx]
                fill_px = float(current_bar.get("close", current_bar.get("c", entry_price)) or entry_price)
            else:
                fill_px = float(entry_price)
            entry_order_id = self.backtest_engine.place_order(
                symbol=symbol,
                side=order_side,
                quantity=quantity,
                order_type=OrderType.MARKET,
                price=fill_px,
            )
            filled_order = None
            for order in self.backtest_engine.pending_orders[:]:
                if order.order_id == entry_order_id:
                    order.filled_price = fill_px
                    order.filled_timestamp = self.backtest_engine.current_timestamp
                    order.status = OrderStatus.FILLED
                    order.stop_loss_price = stop_loss_price
                    order.take_profit_price = take_profit_price
                    sn = str(strategy_name or "").strip().lower() or "unknown"
                    order.custom_tag = f"TB-market_bracket-{sn}-replay"
                    self.backtest_engine._update_position(order, fill_px)
                    self.backtest_engine.filled_orders.append(order)
                    self.backtest_engine.pending_orders.remove(order)
                    filled_order = order
                    break
            if filled_order is not None:
                pos = self.backtest_engine.positions.get(symbol)
                if pos:
                    pos.stop_loss = float(stop_loss_price)
                    pos.take_profit = float(take_profit_price)
                exit_side = OrderSide.SELL if order_side == OrderSide.BUY else OrderSide.BUY
                oco_group = f"{entry_order_id}_BRACKET"
                sl_order_id = self.backtest_engine.place_order(
                    symbol=symbol,
                    side=exit_side,
                    quantity=quantity,
                    order_type=OrderType.STOP,
                    stop_price=float(stop_loss_price),
                    price=float(stop_loss_price),
                    placement_price=float(stop_loss_price),
                )
                for o in self.backtest_engine.pending_orders:
                    if o.order_id == sl_order_id:
                        o.oco_group = oco_group
                        o.exit_reason = "stop_loss"
                        break
                tp_order_id = self.backtest_engine.place_order(
                    symbol=symbol,
                    side=exit_side,
                    quantity=quantity,
                    order_type=OrderType.LIMIT,
                    limit_price=float(take_profit_price),
                    price=float(take_profit_price),
                    placement_price=float(take_profit_price),
                )
                for o in self.backtest_engine.pending_orders:
                    if o.order_id == tp_order_id:
                        o.oco_group = oco_group
                        o.exit_reason = "take_profit"
                        break
            return {
                "success": True,
                "orderId": entry_order_id,
                "message": "Market bracket entry simulated in backtest",
                "method": "backtest_simulation",
            }

        # Place stop order for entry
        entry_order_id = self.backtest_engine.place_order(
            symbol=symbol,
            side=order_side,
            quantity=quantity,
            order_type=OrderType.STOP,
            stop_price=entry_price,
            price=entry_price
        )
        
        # Track bracket orders for stop loss and take profit
        # These will be placed when entry order fills
        # Store bracket info in order metadata
        entry_order = None
        for order in self.backtest_engine.pending_orders:
            if order.order_id == entry_order_id:
                entry_order = order
                # Store bracket prices in order (we'll use these when entry fills)
                entry_order.stop_loss_price = stop_loss_price
                entry_order.take_profit_price = take_profit_price
                sn = str(strategy_name or "").strip().lower() or "unknown"
                entry_order.custom_tag = f"TB-stop_bracket-{sn}-replay"
                break

        # ── BONGO §1B: register the breakeven watch in replay ─────────────────────────
        # The live ``BaseStrategy.place_bracket_order`` calls
        # ``bot.register_generic_breakeven_watch`` after a successful order_id is
        # returned. We bypass that whole method via the ``self.strategy.place_bracket_order``
        # intercept, so we have to mirror the registration here. Without this block,
        # ``self._breakeven_watches`` stays empty and ``_evaluate_breakeven_watches``
        # never fires — which is exactly why no backtest trades ever exited at
        # ``breakeven`` despite ``breakeven_trigger_r=0.2`` in the TOML.
        if (
            entry_order_id
            and breakeven_profit_threshold is not None
            and float(breakeven_profit_threshold) > 0
        ):
            try:
                self._simulate_register_breakeven_watch(
                    str(entry_order_id),
                    symbol=symbol,
                    side=side,
                    entry_price=float(entry_price),
                    profit_threshold=float(breakeven_profit_threshold),
                    breakeven_offset=float(breakeven_offset or 0.0),
                    strategy_name=str(strategy_name or kwargs.get("strategy_name") or ""),
                )
            except Exception as exc:
                logger.warning(
                    "replay: could not register breakeven watch for %s: %s",
                    entry_order_id, exc,
                )

        return {
            'success': True,
            'orderId': entry_order_id,
            'message': 'Bracket order simulated in backtest',
            'method': 'backtest_simulation'
        }
    
    async def _simulate_place_market_order(
        self,
        symbol: str,
        side: str,
        quantity: int,
        **kwargs
    ) -> Dict[str, Any]:
        """Simulate market order placement in backtest."""
        order_side = OrderSide.BUY if side.upper() == "BUY" else OrderSide.SELL
        
        # Get current price from latest bar
        if self.backtest_engine.current_timestamp and self._current_bars:
            current_bar = self._current_bars[self.backtest_engine.current_bar_index]
            current_price = float(current_bar.get('close', current_bar.get('c', 0)))
        else:
            current_price = 0.0
        
        order_id = self.backtest_engine.place_order(
            symbol=symbol,
            side=order_side,
            quantity=quantity,
            order_type=OrderType.MARKET,
            price=current_price
        )
        
        return {
            'success': True,
            'orderId': order_id,
            'message': 'Market order simulated in backtest',
            'method': 'backtest_simulation'
        }
    
    def _simulate_register_breakeven_watch(
        self,
        order_id: str,
        *,
        symbol: str,
        side: str,
        entry_price: float,
        profit_threshold: float,
        strategy_name: str = "",
        breakeven_offset: float = 0.0,
    ) -> None:
        """Replay-mode equivalent of ``trading_bot.register_generic_breakeven_watch``.

        ``BaseStrategy.place_bracket_order`` calls this *after* the simulated entry
        order is placed, so we already have the entry order id. We can't link the SL
        order id yet (it doesn't exist until the entry fills and ``_handle_filled_orders``
        creates the bracket exits) — that linkage happens in ``_process_subbar_fills``
        after the entry fills.

        ``breakeven_offset`` (price pts, clamped ≥ 0) is applied **in the trade's
        favour** when the watch eventually triggers: LONG snaps the SL to
        ``entry + offset``, SHORT to ``entry − offset``. Default 0 preserves the
        legacy "snap exactly to entry" behaviour.
        """
        if not order_id:
            return
        try:
            thr = float(profit_threshold)
        except (TypeError, ValueError):
            return
        if thr <= 0:
            return
        try:
            be_offset = max(0.0, float(breakeven_offset or 0.0))
        except (TypeError, ValueError):
            be_offset = 0.0
        su = str(side).upper()
        normalized_side = "LONG" if su in ("BUY", "LONG") else "SHORT"
        self._breakeven_watches[str(order_id)] = {
            "symbol": str(symbol).upper().strip(),
            "side": normalized_side,
            "entry_price": float(entry_price),
            "profit_threshold": thr,
            "breakeven_offset": be_offset,
            "entry_bar_index": None,    # set when the entry fills
            "sl_order_id": None,        # linked after _handle_filled_orders creates SL/TP
            "triggered": False,
            "position_filled": False,
            "strategy_name": str(strategy_name or ""),
        }
        logger.debug(
            "replay breakeven watch armed: order=%s sym=%s side=%s thr=%.4f offset=%.4f entry=%.4f",
            order_id, symbol, normalized_side, thr, be_offset, float(entry_price),
        )

    def _evaluate_breakeven_watches(self, bar: Any) -> None:
        """Check all active breakeven watches against this bar; move SL to entry on trigger.

        Mirrors the live ``trading_bot._generic_breakeven_monitor_loop`` semantics:
          • Skip the entry bar itself (``current_bar_index <= entry_bar_index``).
          • For LONG: trigger when ``bar.high - entry_price >= profit_threshold``.
          • For SHORT: trigger when ``entry_price - bar.low >= profit_threshold``.
          • On trigger: locate the linked SL pending order, snap ``stop_price`` (and
            ``placement_price`` to keep the BUY/SELL stop direction-validation in
            ``BacktestEngine._check_order_fill`` happy) to ``entry_price``.
          • If the position is gone (already closed by SL/TP earlier this run), drop
            the watch silently.
        """
        if not self._breakeven_watches:
            return
        try:
            bar_high = float(bar["high"])
            bar_low = float(bar["low"])
            bar_close = float(bar["close"])
        except (KeyError, TypeError, ValueError):
            return

        for oid, watch in list(self._breakeven_watches.items()):
            if watch.get("triggered"):
                # Already moved on a previous bar; let the engine handle the rest.
                continue
            sl_oid = watch.get("sl_order_id")
            if not sl_oid:
                # Entry hasn't filled yet — nothing to move.
                continue
            entry_bar = watch.get("entry_bar_index")
            if entry_bar is not None and self.backtest_engine.current_bar_index <= int(entry_bar):
                # Skip the entry bar itself to avoid look-ahead on the high/low ordering.
                continue

            sym = watch["symbol"]
            if sym not in self.backtest_engine.positions:
                # Position already closed — drop the watch.
                self._breakeven_watches.pop(oid, None)
                continue

            entry_price = float(watch["entry_price"])
            thr = float(watch["profit_threshold"])
            side = watch["side"]
            be_offset = max(0.0, float(watch.get("breakeven_offset") or 0.0))

            if side == "LONG":
                profit_move = bar_high - entry_price
            else:
                profit_move = entry_price - bar_low

            if profit_move < thr:
                continue

            new_stop = entry_price + be_offset if side == "LONG" else entry_price - be_offset

            # ── Same-bar ambiguity guard (replay-only) ────────────────────────────────
            # A single OHLC bar tells us nothing about the *order* of the high and the
            # low within it. If the bar's range covers BOTH the BE-trigger level (high
            # for LONG / low for SHORT) AND the proposed moved-SL level (low for LONG /
            # high for SHORT), the previous code unconditionally snapped the SL to
            # ``new_stop`` and then let the per-bar fill loop fill it — producing a
            # "breakeven" exit on what was visibly a large directional bar that more
            # likely punched straight through both levels without any breakeven
            # protection ever existing.
            #
            # Conservative policy: DEFER the snap when we can't disambiguate the
            # intra-bar ordering. The watch stays armed (``triggered=False``) so the
            # next bar gets a fresh evaluation. The original SL stays in place — if
            # that level is *also* touched this bar, the engine's normal fill loop
            # fires it as a regular ``stop_loss`` exit at the original SL price.
            #
            # LONG  → ambiguous when ``bar.low  <= new_stop`` (= entry + offset).
            # SHORT → ambiguous when ``bar.high >= new_stop`` (= entry - offset).
            if side == "LONG":
                ambiguous = bar_low <= new_stop
            else:
                ambiguous = bar_high >= new_stop
            if ambiguous:
                logger.info(
                    "↪ replay breakeven deferred (same-bar ambiguity): %s %s bar=[h=%.4f l=%.4f] "
                    "trigger=%.4f new_stop=%.4f — watch stays armed for next bar",
                    sym, side, bar_high, bar_low, thr, new_stop,
                )
                continue

            # Trigger: find the SL pending order and snap its trigger to entry ± offset
            # (offset shifts the stop in the trade's favour: LONG above entry, SHORT below).
            sl_order = None
            for pending in self.backtest_engine.pending_orders:
                if pending.order_id == sl_oid:
                    sl_order = pending
                    break
            if sl_order is None:
                # SL exit already filled or cancelled — nothing to do.
                self._breakeven_watches.pop(oid, None)
                continue

            # ── One-way ratchet (R23+) ────────────────────────────────────────────
            # Multi-stage step-trail registers N watches that may fire on the same
            # bar if MFE jumps multiple stages at once. Each watch overwrites the
            # SL ``stop_price`` — so a higher-trigger stage with a LOWER lock would
            # silently regress the SL.  Guard: a LONG stop only moves up; a SHORT
            # stop only moves down. Prevents regressions while still letting the
            # last-firing stage advance the stop.
            cur_stop: Optional[float] = None
            try:
                cur_stop = float(sl_order.stop_price)
            except (TypeError, ValueError, AttributeError):
                cur_stop = None
            if cur_stop is not None:
                if side == "LONG" and new_stop <= cur_stop:
                    watch["triggered"] = True
                    continue
                if side == "SHORT" and new_stop >= cur_stop:
                    watch["triggered"] = True
                    continue

            try:
                sl_order.stop_price = float(new_stop)
                # Anchor placement_price to the new stop_price itself so the engine's
                # STOP-direction guard in ``BacktestEngine._check_order_fill`` neither
                # rejects nor short-circuits the fill:
                #   • SELL STOP rejects when ``pp < stop_price`` (would-be wrong-side).
                #     With pp == stop_price the strict inequality is false → pass.
                #   • BUY STOP rejects when ``pp > stop_price`` — same logic.
                # Using ``bar_close`` here was the prior (buggy) behaviour: after a
                # profitable LONG move, bar_close is typically *below* the new stop
                # (price retraced through entry on the same bar), which made the SELL
                # STOP look "wrong-side" and the engine silently refused to fill it.
                # Result: the breakeven SL stayed pending forever and the trade only
                # exited when ``manage_positions`` finally tripped ``max_hold_bars``,
                # exactly the symptom the user observed on 2026-03-30 MNQ — moved
                # SL was at 23407, low went all the way to 23117, but exit_reason
                # was logged as ``timeout``.
                sl_order.placement_price = float(new_stop)
                # Keep the synthetic ``price`` field in sync (used by some helpers
                # for slippage / post-fill bookkeeping).
                sl_order.price = float(new_stop)
                # Retag the exit reason so the recap pipeline can distinguish a moved-stop
                # ("breakeven") fill from a real stop_loss hit. ``BacktestEngine._update_position``
                # copies ``filled_order.exit_reason`` straight onto ``BacktestTrade.exit_reason``,
                # which is what walk-forward recaps render in the trade table. Without this
                # retag, every breakeven exit would show as ``stop_loss`` with PnL ≈ 0,
                # visually indistinguishable from a real loss.
                sl_order.exit_reason = "breakeven"
            except Exception as exc:
                logger.warning("replay breakeven: could not modify SL %s: %s", sl_oid, exc)
                continue

            # Also reflect the moved stop on the open position so MFE/MAE bookkeeping
            # and any downstream consumers (recap metrics, trade-chart shading) see
            # the new value.
            pos = self.backtest_engine.positions.get(sym)
            if pos is not None:
                try:
                    pos.stop_loss = float(new_stop)
                except Exception:
                    pass

            watch["triggered"] = True
            logger.info(
                "🔒 replay breakeven triggered: %s %s move=%.4f ≥ thr=%.4f — SL moved to %.4f (entry=%.4f offset=%.4f order=%s)",
                sym, side, profit_move, thr, new_stop, entry_price, be_offset, sl_oid,
            )

    async def _simulate_place_oco_bracket(
        self,
        symbol: str,
        side: str,
        quantity: int,
        entry_price: float,
        stop_loss_price: float,
        take_profit_price: float,
        account_id: Optional[str] = None,
        enable_breakeven: bool = False,
        strategy_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Simulate OCO bracket order with stop entry in backtest.
        
        This intercepts trading_bot.place_oco_bracket_with_stop_entry calls
        and simulates execution using the BacktestEngine.
        """
        quantity = self._adjust_dynamic_sizing(quantity)
        logger.debug(f"📝 Simulating OCO bracket order: {side} {quantity} {symbol} @ {entry_price:.2f}")
        
        # Convert side to OrderSide
        order_side = OrderSide.BUY if side.upper() == "BUY" else OrderSide.SELL
        
        # Place stop order for entry
        entry_order_id = self.backtest_engine.place_order(
            symbol=symbol,
            side=order_side,
            quantity=quantity,
            order_type=OrderType.STOP,
            stop_price=entry_price,
            price=entry_price
        )
        
        # Track bracket orders for stop loss and take profit
        # These will be placed when entry order fills
        entry_order = None
        for order in self.backtest_engine.pending_orders:
            if order.order_id == entry_order_id:
                entry_order = order
                # Store bracket prices in order (we'll use these when entry fills)
                entry_order.stop_loss_price = stop_loss_price
                entry_order.take_profit_price = take_profit_price
                sn = str(strategy_name or "").strip().lower() or "unknown"
                entry_order.custom_tag = f"TB-stop_bracket-{sn}-replay"
                break
        
        return {
            'success': True,
            'orderId': entry_order_id,
            'message': 'OCO bracket order simulated in backtest',
            'method': 'backtest_simulation'
        }

    async def _simulate_place_oco_partial_tp(
        self,
        symbol: str,
        side: str,
        quantity: int,
        entry_price: float,
        stop_loss_price: float,
        take_profit_full_price: float,
        account_id: Optional[str] = None,
        enable_breakeven: bool = False,
        strategy_name: Optional[str] = None,
        *,
        scalp_r_multiple: float = 1.0,
    ) -> Dict[str, Any]:
        """Simulate BONGO §1A partial TP stop-entry (two-stage exits in ``_process_subbar_fills``)."""
        from core.bracket_orders import build_partial_tp_stop_entry_plan

        quantity = self._adjust_dynamic_sizing(quantity)
        _ = (account_id, enable_breakeven, strategy_name)
        if int(quantity) < 2:
            return {"success": False, "error": "partial_tp_requires_quantity_ge_2", "orderId": None}
        logger.debug(
            "📝 Simulating partial-TP OCO stop entry: %s %s %s @ %.2f",
            side,
            quantity,
            symbol,
            entry_price,
        )
        plan = build_partial_tp_stop_entry_plan(
            symbol=symbol,
            side=side,
            quantity=int(quantity),
            entry_stop_price=float(entry_price),
            stop_loss_price=float(stop_loss_price),
            take_profit_full_price=float(take_profit_full_price),
            scalp_r_multiple=float(scalp_r_multiple or 1.0),
        )
        order_side = OrderSide.BUY if side.upper() == "BUY" else OrderSide.SELL
        entry_order_id = self.backtest_engine.place_order(
            symbol=symbol,
            side=order_side,
            quantity=int(quantity),
            order_type=OrderType.STOP,
            stop_price=float(entry_price),
            price=float(entry_price),
        )
        for order in self.backtest_engine.pending_orders:
            if order.order_id == entry_order_id:
                order.stop_loss_price = float(stop_loss_price)
                order.take_profit_price = float(take_profit_full_price)
                order.partial_tp_scalp_price = float(plan.scalp.take_profit_price)
                order.partial_tp_runner_price = float(plan.runner.take_profit_price)
                order.partial_tp_scalp_qty = int(plan.scalp.quantity)
                order.partial_tp_runner_qty = int(plan.runner.quantity)
                sn = str(strategy_name or "").strip().lower() or "unknown"
                order.custom_tag = f"TB-stop_bracket-{sn}-replay"
                break
        return {
            "success": True,
            "orderId": entry_order_id,
            "message": "Partial-TP OCO bracket simulated in backtest",
            "method": "backtest_simulation_partial_tp",
        }
    
    async def _simulate_place_stop_order(
        self,
        symbol: str,
        side: str,
        quantity: int,
        stop_price: float,
        account_id: Optional[str] = None,
        reduce_only: bool = False,
        strategy_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """Simulate stop order placement in backtest."""
        order_side = OrderSide.BUY if side.upper() == "BUY" else OrderSide.SELL
        
        order_id = self.backtest_engine.place_order(
            symbol=symbol,
            side=order_side,
            quantity=quantity,
            order_type=OrderType.STOP,
            stop_price=stop_price,
            price=stop_price
        )
        
        return {
            'success': True,
            'orderId': order_id,
            'message': 'Stop order simulated in backtest',
            'method': 'backtest_simulation'
        }
    
    async def _simulate_place_limit_order(
        self,
        symbol: str,
        side: str,
        quantity: int,
        limit_price: float,
        account_id: Optional[str] = None,
        reduce_only: bool = False,
        strategy_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """Simulate limit order placement in backtest."""
        order_side = OrderSide.BUY if side.upper() == "BUY" else OrderSide.SELL
        
        order_id = self.backtest_engine.place_order(
            symbol=symbol,
            side=order_side,
            quantity=quantity,
            order_type=OrderType.LIMIT,
            limit_price=limit_price,
            price=limit_price
        )
        
        return {
            'success': True,
            'orderId': order_id,
            'message': 'Limit order simulated in backtest',
            'method': 'backtest_simulation'
        }
    
    def _install_fast_strategy_mocks(
        self,
        *,
        replay_symbol: str,
        bars: List[Dict],
        df: pd.DataFrame,
    ):
        """Install bisect-driven mocks once at the top of ``replay()`` when
        ``BACKTEST_FAST_LOOP=1`` is set.

        Mirrors the contract of the per-bar ``_call_strategy_analyze`` install
        block, but installs ONCE for the whole replay instead of per iteration.
        Returns a ``restore`` callable the caller invokes in its ``finally``
        block to revert the trading_bot to its original methods.

        Mock specifics:

        - ``mock_get_historical_data``: replaces the O(n × parse) intraday-csv
          slow path with O(log n) ``np.searchsorted`` over a precomputed UTC-ns
          array carved out of ``df.index`` (already-parsed). Non-intraday TFs
          delegate to the original ``MockTradingBot.get_historical_data``,
          which honors a new ``self.trading_bot._fast_cursor_index`` to keep
          the prefix-resample semantics correct in fast mode.
        - ``mock_get_open_positions``: identical to slow path.
        - ``mock_get_market_quote``: indexes ``bars[_fast_cursor_index]``
          (instead of slow path's ``bars_list[-1]`` where ``bars_list`` was
          the prefix). Behaviorally identical.

        Look-ahead safety: every call clips to bars whose UTC-ns timestamp is
        ``<=`` the cursor's UTC-ns. The cursor is the bar timestamp at
        ``self.backtest_engine.current_bar_index``.
        """
        original_get_historical = getattr(self.trading_bot, "get_historical_data", None)
        original_get_open_positions = getattr(self.trading_bot, "get_open_positions", None)
        original_get_market_quote = getattr(self.trading_bot, "get_market_quote", None)
        # Stash the original ``bars`` value so we can restore it. We intentionally
        # leave ``self.trading_bot.bars = bars`` (the FULL list) for the duration
        # of the replay so the resample path sees the same data the slow path
        # would have seen (clipped via ``_fast_cursor_index``).
        original_bars = getattr(self.trading_bot, "bars", None)
        original_fast_cursor = getattr(self.trading_bot, "_fast_cursor_index", None)

        bar_times_ns = _bars_to_utc_ns(df)
        n_bars = len(bars)
        replay_tf = (self._replay_timeframe or "").lower()

        # Surface the full bars list + a live cursor to ``MockTradingBot``.
        # ``MockTradingBot._select_historical_source`` reads ``_fast_cursor_index``
        # when present (see ``core/backtest_executor.py``) and slices on
        # demand for the rare coarser-TF resample path. The intraday-csv path
        # bypasses this entirely via the closure below.
        self.trading_bot.bars = bars
        self.trading_bot._fast_cursor_index = -1  # advanced per bar by the replay loop

        async def fast_mock_get_historical_data(
            symbol: str,
            timeframe: str = "1m",
            limit: int = 100,
            start_time: Optional[datetime] = None,
            end_time: Optional[datetime] = None,
            **kwargs,
        ) -> List[Dict]:
            if symbol.upper() != replay_symbol.upper():
                logger.warning(f"Backtest requested different symbol: {symbol} != {replay_symbol}")
                return []

            tf = (timeframe or "1m").lower()
            cursor_ix = self.backtest_engine.current_bar_index
            if cursor_ix is None or cursor_ix < 0:
                cursor_ix = -1

            # Intraday-csv fast path: same TF as replay (or no replay_tf set).
            intraday_from_csv = (not replay_tf) or (tf == replay_tf)
            if intraday_from_csv:
                # End index = min(cursor + 1, n_bars). Bisect for any caller-
                # supplied end_time. We use side='right' so a timestamp equal
                # to a bar's open time INCLUDES that bar (matches slow path's
                # ``bt > end_utc`` strict inequality which keeps equal bars).
                cutoff = cursor_ix + 1
                if cutoff > n_bars:
                    cutoff = n_bars
                if cutoff <= 0:
                    return []
                if end_time is not None:
                    end_ns = _dt_to_utc_ns(end_time)
                    if end_ns is not None:
                        end_cutoff = int(np.searchsorted(bar_times_ns, end_ns, side="right"))
                        if end_cutoff < cutoff:
                            cutoff = end_cutoff
                start_ix = 0
                if start_time is not None:
                    start_ns = _dt_to_utc_ns(start_time)
                    if start_ns is not None:
                        start_ix = int(np.searchsorted(bar_times_ns, start_ns, side="left"))
                if start_ix >= cutoff:
                    return []
                if limit and limit < (cutoff - start_ix):
                    start_ix = cutoff - limit
                # Slice the bars list — O(k) memcpy of pointers, no parsing.
                return bars[start_ix:cutoff]

            # Coarser-TF delegate path. Hand off to MockTradingBot which knows
            # how to resample; it reads ``_fast_cursor_index`` to honor the
            # no-look-ahead invariant.
            if original_get_historical is None:
                return []
            effective_end = end_time
            cursor_ns = (
                int(bar_times_ns[cursor_ix]) if 0 <= cursor_ix < n_bars else None
            )
            if effective_end is None and cursor_ns is not None:
                effective_end = datetime.fromtimestamp(cursor_ns / 1e9, tz=timezone.utc)
            elif effective_end is not None and cursor_ns is not None:
                eff_end_ns = _dt_to_utc_ns(effective_end)
                if eff_end_ns is not None and eff_end_ns > cursor_ns:
                    effective_end = datetime.fromtimestamp(cursor_ns / 1e9, tz=timezone.utc)
            # Reuse the daily-bars passthrough cache.
            start_iso = start_time.isoformat() if isinstance(start_time, datetime) else ""
            end_day_iso = ""
            if effective_end is not None:
                end_dt = (
                    effective_end.astimezone(timezone.utc)
                    if effective_end.tzinfo
                    else effective_end.replace(tzinfo=timezone.utc)
                )
                end_day_iso = end_dt.date().isoformat()
            cache_key = (symbol.upper(), tf, int(limit or 0), start_iso, end_day_iso)
            if tf.endswith("d") and cache_key in self._passthrough_cache:
                return self._passthrough_cache[cache_key]
            result = await original_get_historical(
                symbol=symbol,
                timeframe=timeframe,
                limit=limit,
                start_time=start_time,
                end_time=effective_end,
                **kwargs,
            )
            if tf.endswith("d"):
                self._passthrough_cache[cache_key] = result or []
            return result or []

        async def fast_mock_get_open_positions(account_id=None):
            positions = []
            for sym, pos in self.backtest_engine.positions.items():
                positions.append({
                    "symbol": sym,
                    "side": "LONG" if pos.side == OrderSide.BUY else "SHORT",
                    "quantity": pos.quantity,
                    "entry_price": pos.entry_price,
                })
            return positions

        async def fast_mock_get_market_quote(symbol: str):
            cursor_ix = self.backtest_engine.current_bar_index
            if cursor_ix is not None and 0 <= cursor_ix < n_bars:
                last_bar = bars[cursor_ix]
                close_price = float(last_bar.get("close", last_bar.get("c", 0)) or 0)
                vol = int(last_bar.get("volume", last_bar.get("v", last_bar.get("Volume", 0))) or 0)
                return {
                    "bid": close_price, "ask": close_price, "last": close_price,
                    "volume": vol, "Volume": vol,
                }
            return {"bid": 0, "ask": 0, "last": 0, "volume": 0, "Volume": 0}

        if original_get_historical is not None:
            self.trading_bot.get_historical_data = fast_mock_get_historical_data
        if original_get_open_positions is not None or hasattr(self.trading_bot, "get_open_positions"):
            self.trading_bot.get_open_positions = fast_mock_get_open_positions
        if original_get_market_quote is not None or hasattr(self.trading_bot, "get_market_quote"):
            self.trading_bot.get_market_quote = fast_mock_get_market_quote

        def restore() -> None:
            if original_get_historical is not None:
                self.trading_bot.get_historical_data = original_get_historical
            if original_get_open_positions is not None:
                self.trading_bot.get_open_positions = original_get_open_positions
            if original_get_market_quote is not None:
                self.trading_bot.get_market_quote = original_get_market_quote
            if original_bars is not None:
                self.trading_bot.bars = original_bars
            if original_fast_cursor is None:
                try:
                    delattr(self.trading_bot, "_fast_cursor_index")
                except AttributeError:
                    pass
            else:
                self.trading_bot._fast_cursor_index = original_fast_cursor

        return restore

    async def _call_strategy_analyze(self, replay_symbol: str, bars_list: List[Dict]) -> Optional[Dict]:
        """
        Call strategy's analyze method with mocked historical data.
        
        We need to mock trading_bot.get_historical_data to return our bars.
        This prevents look-ahead bias by only returning bars up to the current point.
        
        Args:
            replay_symbol: The symbol being backtested (e.g., "MNQ")
            bars_list: List of bars available up to current replay position
        """
        # Store original methods
        original_get_historical = getattr(self.trading_bot, "get_historical_data", None)
        original_get_open_positions = getattr(self.trading_bot, "get_open_positions", None)
        original_get_market_quote = getattr(self.trading_bot, "get_market_quote", None)
        
        def _parse_dt(value) -> Optional[datetime]:
            if value is None:
                return None
            if isinstance(value, datetime):
                return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
            if isinstance(value, str):
                dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
                return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
            if isinstance(value, (int, float)):
                # Assume seconds epoch
                return datetime.fromtimestamp(value, tz=timezone.utc)
            return None

        def _bar_time_utc(bar: Dict) -> Optional[datetime]:
            return _parse_dt(bar.get("timestamp") or bar.get("time") or bar.get("t"))

        def _current_replay_time_utc() -> Optional[datetime]:
            ts = getattr(self.trading_bot, "_current_bar_timestamp", None) or self.backtest_engine.current_timestamp
            dt = _parse_dt(ts)
            return dt.astimezone(timezone.utc) if dt else None

        # Mock get_historical_data:
        # - If strategy asks for the replay timeframe, serve from bars_list (no look-ahead).
        # - For other timeframes (e.g., 1m/5m/1d), delegate to the underlying get_historical_data
        #   but cap end_time to the current replay bar timestamp to prevent look-ahead.
        # This is a plain function (not bound method), so no 'self' parameter.
        # Parameter names MUST match the keyword arguments passed by strategies.
        async def mock_get_historical_data(
            symbol: str,
            timeframe: str = "1m",
            limit: int = 100,
            start_time: Optional[datetime] = None,
            end_time: Optional[datetime] = None,
            **kwargs,
        ) -> List[Dict]:
            """Mock that returns historical bars only up to current replay position."""
            if symbol.upper() != replay_symbol.upper():
                # If different symbol requested (shouldn't happen in backtest)
                logger.warning(f"Backtest requested different symbol: {symbol} != {replay_symbol}")
                return []

            tf = (timeframe or "1m").lower()
            replay_tf = (self._replay_timeframe or "").lower()
            cur_utc = _current_replay_time_utc()

            # Serve from bars_list only for the native replay cadence (or legacy runs with no
            # replay_tf). Do not treat ``15m``/``30m`` as "same CSV" when replay is ``5m`` — those
            # must go through ``MockTradingBot.get_historical_data`` so OHLCV resample applies.
            intraday_from_csv = (not replay_tf) or (tf == replay_tf)
            if intraday_from_csv:
                filtered = []
                start_utc = start_time.astimezone(timezone.utc) if (start_time and start_time.tzinfo) else start_time
                end_utc = end_time.astimezone(timezone.utc) if (end_time and end_time.tzinfo) else end_time
                for b in bars_list:
                    bt = _bar_time_utc(b)
                    if not bt:
                        continue
                    bt = bt.astimezone(timezone.utc)
                    if cur_utc and bt > cur_utc:
                        continue
                    if start_utc and bt < start_utc:
                        continue
                    if end_utc and bt > end_utc:
                        continue
                    filtered.append(b)
                if limit and limit < len(filtered):
                    return filtered[-limit:]
                return filtered

            # Delegate other timeframes to underlying data source (adapter / higher-res history)
            if original_get_historical is None:
                return []

            # Cap end_time to current replay timestamp to avoid look-ahead
            effective_end = end_time
            if effective_end is None and cur_utc is not None:
                effective_end = cur_utc
            elif effective_end is not None and cur_utc is not None:
                eff_end_utc = effective_end.astimezone(timezone.utc) if effective_end.tzinfo else effective_end.replace(tzinfo=timezone.utc)
                if eff_end_utc > cur_utc:
                    effective_end = cur_utc

            # Optional caching for daily bars (stable within a trading day)
            start_iso = (effective_end - (effective_end - effective_end)).isoformat() if False else ""  # placeholder for type checker
            start_iso = start_time.isoformat() if isinstance(start_time, datetime) else ""
            end_day_iso = ""
            if effective_end is not None:
                end_dt = effective_end.astimezone(timezone.utc) if effective_end.tzinfo else effective_end.replace(tzinfo=timezone.utc)
                end_day_iso = end_dt.date().isoformat()

            cache_key = (symbol.upper(), tf, int(limit or 0), start_iso, end_day_iso)
            if tf.endswith("d") and cache_key in self._passthrough_cache:
                return self._passthrough_cache[cache_key]

            result = await original_get_historical(
                symbol=symbol,
                timeframe=timeframe,
                limit=limit,
                start_time=start_time,
                end_time=effective_end,
                **kwargs,
            )

            if tf.endswith("d"):
                self._passthrough_cache[cache_key] = result or []
            return result or []

        async def mock_get_open_positions(account_id=None):
            """
            Return positions derived from the backtest engine.
            This is critical because many live strategies (including simple_candle)
            gate signals based on current positions.
            """
            positions = []
            for sym, pos in self.backtest_engine.positions.items():
                positions.append({
                    "symbol": sym,
                    "side": "LONG" if pos.side == OrderSide.BUY else "SHORT",
                    "quantity": pos.quantity,
                    "entry_price": pos.entry_price,
                })
            return positions

        async def mock_get_market_quote(symbol: str):
            """Return quote derived from the current replay bar (close + volume)."""
            if self.backtest_engine.current_bar_index is not None and bars_list:
                last_bar = bars_list[-1]
                close_price = float(last_bar.get("close", last_bar.get("c", 0)) or 0)
                vol = int(last_bar.get("volume", last_bar.get("v", last_bar.get("Volume", 0))) or 0)
                return {
                    "bid": close_price,
                    "ask": close_price,
                    "last": close_price,
                    "volume": vol,
                    "Volume": vol,
                }
            return {"bid": 0, "ask": 0, "last": 0, "volume": 0, "Volume": 0}
        
        # Temporarily replace methods with our mocks
        if original_get_historical is not None:
            self.trading_bot.get_historical_data = mock_get_historical_data
        if original_get_open_positions is not None or hasattr(self.trading_bot, "get_open_positions"):
            self.trading_bot.get_open_positions = mock_get_open_positions
        if original_get_market_quote is not None or hasattr(self.trading_bot, "get_market_quote"):
            self.trading_bot.get_market_quote = mock_get_market_quote
        
        try:
            # Call strategy's analyze method
            signal = await self.strategy.analyze(replay_symbol)
            return signal
        finally:
            # Restore original methods
            if original_get_historical is not None:
                self.trading_bot.get_historical_data = original_get_historical
            if original_get_open_positions is not None:
                self.trading_bot.get_open_positions = original_get_open_positions
            if original_get_market_quote is not None:
                self.trading_bot.get_market_quote = original_get_market_quote
    
    async def _execute_strategy_signal(self, signal: Dict, current_bar: pd.Series, tick_size: float):
        """Execute a strategy signal by placing orders in backtest engine."""
        action = signal.get('action')
        symbol = signal.get('symbol')
        entry_price = signal.get('entry_price')
        stop_loss = signal.get('stop_loss')
        take_profit = signal.get('take_profit')
        quantity = signal.get('quantity', 1)
        
        if action == "LONG":
            side = OrderSide.BUY
        elif action == "SHORT":
            side = OrderSide.SELL
        else:
            return
        
        # Place stop order for entry
        entry_order_id = self.backtest_engine.place_order(
            symbol=symbol,
            side=side,
            quantity=quantity,
            order_type=OrderType.STOP,
            stop_price=entry_price,
            price=entry_price
        )
        
        logger.debug(f"📊 Signal executed: {action} {quantity} {symbol} @ {entry_price:.2f}")
    
    def _sync_strategy_positions(self):
        """Sync strategy's active_positions from backtest engine positions."""
        if not hasattr(self.strategy, 'active_positions'):
            return
        
        # Convert backtest positions to strategy format
        strategy_positions = []
        for pos in self.backtest_engine.positions.values():
            strategy_positions.append({
                'symbol': pos.symbol,
                'side': 'LONG' if pos.side == OrderSide.BUY else 'SHORT',
                'quantity': pos.quantity,
                'entry_price': pos.entry_price,
                'unrealized_pnl': pos.unrealized_pnl
            })
        
        self.strategy.active_positions = strategy_positions

