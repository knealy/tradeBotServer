"""Globex Drift Continuation (Phase-2 #2.5).

**Alpha thesis** (per ``docs/STRATEGY_ARSENAL.md``)
    During the Asian Globex session (18:00 ET → 04:00 ET) the market
    often drifts in the direction set by the late-RTH close.  A flat or
    contrary Asian open that then BREAKS in the continuation direction
    confirms institutional follow-through and tends to extend through
    the European open.  This strategy captures the "Asian breakout in
    the prior-RTH-close direction" using the same Crabel-style
    stop-entry bracket geometry as ``nr_compression_break``.

**Strategy v1 (this implementation)**
    - **Direction bias**: yesterday's RTH (09:30 → 16:00 ET) close vs
      open.  Close > open ⇒ LONG bias; close < open ⇒ SHORT.
      ``min_body_pct`` filters indecisive doji sessions.
    - **Pre-trigger window** (default 18:00 → 22:00 ET): track the
      running high / low of Globex bars to establish an "early Globex
      range".  After that window, the range is FROZEN.
    - **Entry window** (default 22:00 → 04:00 ET): arm a stop-entry
      bracket in the bias direction, offset 1 tick beyond the frozen
      Globex high (LONG) or low (SHORT).
    - **Stop / TP**: ATR(14) on 5m bars scales the stop distance
      (``stop_atr_multiplier`` = 0.8 default); TP is ``tp_r_multiple``
      × |entry - stop| (default 1.8R — slightly tighter than NR7 to
      reflect lower overnight volatility).
    - **Force-flat at 04:00 ET** (European-session boundary) regardless
      of MFE/MAE.  No position can survive into the Euro / pre-RTH
      window.  ``max_hold_bars`` caps the hold to 72 5m bars (6h) as a
      secondary safety net.
    - One trade per symbol per Globex session.
    - Per-symbol consec-loss circuit breaker (same shape as NR7).
    - The portfolio daily-loss breaker (Phase 1) is consulted via
      :func:`core.portfolio_daily_breaker.PortfolioDailyBreaker.replay_evaluate`.

**Acceptance criteria** (per ``docs/STRATEGY_ARSENAL.md``)
    - 6-month walk-forward on MNQ + MES.
    - RF ≥ 5.0 net, max DD ≤ 50 %, ≥ 25 fills, PF ≥ 1.3.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from core.max_hold_exit import close_positions_exceeding_max_hold
from core.strategy_config import load_strategy_config
from strategies.strategy_base import (
    BaseStrategy,
    MarketCondition,
    StrategyConfig,
)

logger = logging.getLogger(__name__)


def _parse_hhmm(value: str, fallback: time) -> time:
    try:
        hh, mm = value.split(":")
        return time(int(hh), int(mm))
    except Exception:
        return fallback


@dataclass
class _RthDailyBar:
    """Aggregated RTH OHLC for the prior trading session (used for bias)."""
    session_date: date
    open: float
    high: float
    low: float
    close: float


@dataclass
class _GlobexRange:
    """Frozen early-Globex high/low for the current Globex session."""
    globex_session_date: date  # the calendar date of the 18:00 ET *open*
    high: float
    low: float
    frozen: bool


class GlobexDriftContinuationStrategy(BaseStrategy):
    """Asian-session breakout in the prior-RTH-close direction."""

    NAME = "globex_drift_continuation"

    def __init__(self, trading_bot, config: Optional[StrategyConfig] = None):
        if config is None:
            config = StrategyConfig.from_env(self.NAME)
        super().__init__(trading_bot, config)

        self._cfg = load_strategy_config(self.NAME, env_prefix=f"{self.NAME.upper()}_")

        self.timeframe: str = self._cfg.get_str("signal.timeframe", "5m") or "5m"
        self.lookback_bars: int = int(self._cfg.get_int("signal.lookback_bars", 2000))

        self.atr_period: int = int(self._cfg.get_int("signal.atr_period", 14))
        self.stop_atr_multiplier: float = float(
            self._cfg.get_float("signal.stop_atr_multiplier", 0.8)
        )
        self.tp_r_multiple: float = float(self._cfg.get_float("signal.tp_r_multiple", 1.8))
        self.entry_offset_ticks: int = int(self._cfg.get_int("signal.entry_offset_ticks", 1))
        self.max_hold_bars: int = int(self._cfg.get_int("signal.max_hold_bars", 72))  # 6h @ 5m

        # Globex session boundaries (ET).
        # The Globex SESSION DATE is the date of the 18:00 open; the session
        # spans from that 18:00 to 17:00 ET the next day (with the CME
        # settlement gap 17:00-18:00).  This strategy only operates in the
        # 18:00 → 04:00 ET sub-window.
        self.globex_start_et = _parse_hhmm(
            self._cfg.get_str("signal.globex_start_et", "18:00"), time(18, 0),
        )
        # End of the pre-trigger range window (after this, the range freezes
        # and entry brackets are eligible).
        self.range_freeze_et = _parse_hhmm(
            self._cfg.get_str("signal.range_freeze_et", "22:00"), time(22, 0),
        )
        # End of the entry window (after this, no new brackets are placed).
        self.entry_window_end_et = _parse_hhmm(
            self._cfg.get_str("signal.entry_window_end_et", "04:00"), time(4, 0),
        )
        # EOD flat (closes any open position regardless of MFE/MAE).
        self.flat_et = _parse_hhmm(self._cfg.get_str("signal.flat_et", "04:00"), time(4, 0))

        # RTH window for prior-session bias calc.
        self.rth_start = _parse_hhmm(
            self._cfg.get_str("signal.rth_start_et", "09:30"), time(9, 30),
        )
        self.rth_end = _parse_hhmm(
            self._cfg.get_str("signal.rth_end_et", "16:00"), time(16, 0),
        )

        # Bias quality filter.
        self.min_body_pct: float = float(self._cfg.get_float("signal.min_body_pct", 0.15))
        # Direction inversion — when ``use_continuation_bias = false`` we
        # FADE the prior RTH-close direction instead of continuing it.
        # Reserved for ad-hoc tuning / regime experiments.
        self.use_continuation_bias: bool = bool(
            self._cfg.get_bool("signal.use_continuation_bias", True)
        )

        # Per-symbol session state — keyed by globex_session_date (the 18:00
        # date).  Reset implicitly when a new globex date is observed.
        self._range_for_symbol: Dict[str, _GlobexRange] = {}
        self._traded_globex: Dict[str, date] = {}
        self._entry_bar_seq: Dict[str, int] = {}
        self._bar_seq: int = 0

        self._tick_sizes = {"MNQ": 0.25, "MES": 0.25, "MGC": 0.10}

        try:
            from zoneinfo import ZoneInfo
            self._tz = ZoneInfo("America/New_York")
        except Exception:
            self._tz = timezone(timedelta(hours=-5))

        # Portfolio daily-loss breaker (Phase 1 infra) — lazy.
        self._portfolio_breaker = None

    # ------------------------------------------------------------------
    # BaseStrategy interface
    # ------------------------------------------------------------------

    def get_strategy_info(self) -> Dict[str, Any]:
        return {
            "name": self.NAME,
            "description": "Globex drift continuation (Asian-session breakout in RTH-close direction)",
            "type": "TREND-CONTINUE",
            "symbols": list(self.config.symbols),
            "timeframe": self.timeframe,
        }

    async def should_run(self, market_condition: MarketCondition) -> bool:
        return True

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _tick_size(self, symbol: str) -> float:
        return float(self._tick_sizes.get(symbol.upper(), 0.25))

    def _bar_ts_et(self, bar: Dict[str, Any]) -> Optional[datetime]:
        ts = bar.get("timestamp") or bar.get("time") or bar.get("t")
        if ts is None:
            return None
        try:
            if isinstance(ts, datetime):
                dt = ts
            else:
                import pandas as pd
                dt = pd.to_datetime(ts, utc=True).to_pydatetime()
        except Exception:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(self._tz)

    @staticmethod
    def _val(bar: Dict[str, Any], *keys: str, default: float = 0.0) -> float:
        for k in keys:
            v = bar.get(k)
            if v is not None:
                try:
                    return float(v)
                except (TypeError, ValueError):
                    continue
        return default

    def _globex_session_date(self, bar_ts_et: datetime) -> Optional[date]:
        """Return the calendar date of the 18:00 ET open that BEGAN the
        Globex session containing ``bar_ts_et``.

        - If 18:00 ≤ ts.time() ≤ 23:59 → session_date = ts.date().
        - If 00:00 ≤ ts.time() < ``entry_window_end_et`` → session_date =
          ts.date() - 1 day.
        - Otherwise (between entry_window_end and 18:00) → out of session.
        """
        t = bar_ts_et.time()
        if t >= self.globex_start_et:
            return bar_ts_et.date()
        if t < self.entry_window_end_et:
            return (bar_ts_et - timedelta(days=1)).date()
        return None

    def _aggregate_rth_daily_bars(self, bars: List[Dict[str, Any]]) -> List[_RthDailyBar]:
        daily: Dict[date, _RthDailyBar] = {}
        for bar in bars:
            ts = self._bar_ts_et(bar)
            if ts is None:
                continue
            t = ts.time()
            if t < self.rth_start or t >= self.rth_end:
                continue
            d = ts.date()
            o = self._val(bar, "open", "o")
            h = self._val(bar, "high", "h")
            l = self._val(bar, "low", "l")
            c = self._val(bar, "close", "c")
            if d not in daily:
                daily[d] = _RthDailyBar(session_date=d, open=o, high=h, low=l, close=c)
            else:
                db = daily[d]
                db.high = max(db.high, h)
                db.low = min(db.low, l)
                db.close = c
        return sorted(daily.values(), key=lambda b: b.session_date)

    def _resolve_bias(
        self, daily_bars: List[_RthDailyBar], globex_session_date: date,
    ) -> Optional[str]:
        """Pick LONG/SHORT from the RTH session whose date is
        ``globex_session_date`` (since Globex opens AFTER that RTH close).
        Returns ``None`` when no valid prior RTH bar exists or the body is
        too small."""
        prior = next((b for b in reversed(daily_bars) if b.session_date == globex_session_date), None)
        if prior is None:
            return None
        rng = prior.high - prior.low
        if rng <= 0:
            return None
        body = abs(prior.close - prior.open)
        body_pct = body / rng
        if body_pct < self.min_body_pct:
            return None
        bullish = prior.close > prior.open
        if self.use_continuation_bias:
            return "LONG" if bullish else "SHORT"
        # Inverted (fade) mode.
        return "SHORT" if bullish else "LONG"

    def _update_globex_range(
        self, symbol: str, bar: Dict[str, Any], bar_ts_et: datetime,
    ) -> _GlobexRange:
        """Maintain the running 18:00-22:00 ET high/low for the current
        Globex session.  Freezes at ``range_freeze_et``."""
        gd = self._globex_session_date(bar_ts_et)
        if gd is None:
            # Out-of-session: keep the existing snapshot (caller will skip).
            return self._range_for_symbol.get(symbol, _GlobexRange(
                globex_session_date=bar_ts_et.date(), high=0.0, low=0.0, frozen=True,
            ))

        existing = self._range_for_symbol.get(symbol)
        if existing is None or existing.globex_session_date != gd:
            o = self._val(bar, "open", "o")
            h = self._val(bar, "high", "h")
            l = self._val(bar, "low", "l")
            existing = _GlobexRange(
                globex_session_date=gd, high=max(o, h), low=min(o, l), frozen=False,
            )

        if not existing.frozen:
            h = self._val(bar, "high", "h")
            l = self._val(bar, "low", "l")
            existing.high = max(existing.high, h)
            existing.low = min(existing.low, l)
            # Freeze once we've crossed ``range_freeze_et`` within the
            # current Globex session.  The window wraps midnight, so we
            # have two cases:
            #   (a) SAME-DAY: bar date == globex_session_date AND
            #       bar.time() >= range_freeze_et  (e.g. 22:05 today).
            #   (b) NEXT-DAY: bar date == globex_session_date + 1 AND
            #       bar.time() < entry_window_end_et (e.g. 01:30 tomorrow).
            t = bar_ts_et.time()
            if bar_ts_et.date() == gd and t >= self.range_freeze_et:
                existing.frozen = True
            elif bar_ts_et.date() == gd + timedelta(days=1) and t < self.entry_window_end_et:
                existing.frozen = True

        self._range_for_symbol[symbol] = existing
        return existing

    def _compute_atr(self, bars: List[Dict[str, Any]]) -> float:
        if len(bars) < self.atr_period + 2:
            return 0.0
        trs: List[float] = []
        prev_c = self._val(bars[0], "close", "c")
        for b in bars[1:]:
            h = self._val(b, "high", "h")
            l = self._val(b, "low", "l")
            c = self._val(b, "close", "c")
            tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
            trs.append(tr)
            prev_c = c
        if len(trs) < self.atr_period:
            return 0.0
        return sum(trs[-self.atr_period:]) / float(self.atr_period)

    def _cancel_pending_stop_entries(self, engine: Any, symbol: str) -> int:
        """Remove pending stop-entry brackets that this strategy placed but
        never filled.  Called once we're past ``entry_window_end_et`` (04:00 ET
        by default) so a stale bracket can't fill during RTH or the next day.

        Identifies the brackets by ``order_type == STOP`` with a non-None
        ``stop_loss_price`` + ``take_profit_price`` and matching symbol.
        OCO exit legs use ``oco_group`` so they're skipped automatically.
        """
        try:
            from core.backtest.models import OrderStatus, OrderType
        except Exception:
            return 0
        pending = getattr(engine, "pending_orders", None)
        if not pending:
            return 0
        removed = 0
        base = symbol.upper()
        for o in list(pending):
            if o.status != OrderStatus.PENDING or o.order_type != OrderType.STOP:
                continue
            if getattr(o, "oco_group", None):
                continue
            if getattr(o, "stop_loss_price", None) is None or getattr(o, "take_profit_price", None) is None:
                continue
            sym = (o.symbol or "").upper()
            if not sym.startswith(base):
                continue
            pending.remove(o)
            removed += 1
        if removed:
            logger.info("%s: cancelled %d stale pending stop-entry order(s) for %s",
                        self.NAME, removed, base)
        return removed

    def _maybe_close_stale_positions(self, symbol: str, bar_ts_et: datetime) -> None:
        bot = self.trading_bot
        engine = getattr(bot, "backtest_engine", None) or getattr(self, "_replay_engine", None)
        if engine is None:
            return
        # 1) max_hold_bars helper (shared).
        if self.max_hold_bars > 0:
            close_positions_exceeding_max_hold(
                engine=engine,
                max_hold_bars=self.max_hold_bars,
                current_bar_seq=self._bar_seq,
                entry_bar_seq=self._entry_bar_seq,
                log_prefix=self.NAME,
                logger=logger,
            )
        # 2) Force-flat at ``flat_et`` (default 04:00 ET).  When the bar's
        # ET time is in [flat_et, globex_start_et) we (a) cancel any
        # leftover pending stop-entry brackets so they cannot fill in RTH,
        # and (b) close any open position regardless of hold-bar count.
        t = bar_ts_et.time()
        in_flat_window = t >= self.flat_et and t < self.globex_start_et
        if in_flat_window:
            self._cancel_pending_stop_entries(engine, symbol)
            positions = getattr(engine, "positions", None) or {}
            if symbol in positions:
                close_positions_exceeding_max_hold(
                    engine=engine,
                    max_hold_bars=1,
                    current_bar_seq=self._bar_seq + 1000,
                    entry_bar_seq={symbol: 0},
                    log_prefix=f"{self.NAME}(EOD)",
                    logger=logger,
                )

    def _ensure_portfolio_breaker(self) -> None:
        if self._portfolio_breaker is not None:
            return
        import os
        try:
            cap = float(os.environ.get("PORTFOLIO_DAILY_LOSS_CAP", "1000") or "1000")
        except (TypeError, ValueError):
            cap = 1000.0
        if cap <= 0:
            self._portfolio_breaker = False
            return
        from core.portfolio_daily_breaker import PortfolioBreakerConfig, PortfolioDailyBreaker
        self._portfolio_breaker = PortfolioDailyBreaker(
            trading_bot=None, config=PortfolioBreakerConfig(daily_loss_cap_dollars=cap),
        )

    def _portfolio_breaker_tripped(self, bar_ts_et: datetime) -> bool:
        self._ensure_portfolio_breaker()
        if not self._portfolio_breaker:
            return False
        engine = getattr(self.trading_bot, "backtest_engine", None) or getattr(self, "_replay_engine", None)
        if engine is None:
            return False
        trades = getattr(engine, "trades", []) or []
        state = self._portfolio_breaker.replay_evaluate(now_et=bar_ts_et, trades=trades)
        return bool(state.tripped)

    # ------------------------------------------------------------------
    # analyze() / execute()
    # ------------------------------------------------------------------

    async def analyze(self, symbol: str) -> Optional[Dict[str, Any]]:
        try:
            bars = await self.trading_bot.get_historical_data(
                symbol=symbol,
                timeframe=self.timeframe,
                limit=self.lookback_bars,
            )
            if not bars or len(bars) < self.atr_period + 5:
                return None

            last = bars[-1]
            bar_ts = self._bar_ts_et(last)
            if bar_ts is None:
                return None
            self._bar_seq += 1

            self._maybe_close_stale_positions(symbol, bar_ts)

            # Portfolio breaker (backtest path).
            if self._portfolio_breaker_tripped(bar_ts):
                return None

            # Maintain the running Globex range every bar (so it's frozen
            # at the right time regardless of execute-eligible window).
            globex_session = self._globex_session_date(bar_ts)
            if globex_session is None:
                # Outside the 18:00 → entry_window_end_et window — no work.
                return None

            grange = self._update_globex_range(symbol, last, bar_ts)
            if not grange.frozen:
                return None  # still building the range

            # Already traded this Globex session?
            if self._traded_globex.get(symbol) == globex_session:
                return None

            # Entry window: must be at/after range_freeze_et AND before
            # entry_window_end_et.  Both bounds map across midnight, so we
            # handle them separately:
            t = bar_ts.time()
            in_entry_window = False
            if globex_session == bar_ts.date():
                # Same day (e.g. 22:00 → 23:59).
                in_entry_window = t >= self.range_freeze_et
            else:
                # Next day (e.g. 00:00 → 04:00).
                in_entry_window = t < self.entry_window_end_et
            if not in_entry_window:
                return None

            # Bias from the RTH session that JUST CLOSED — its date equals
            # globex_session (the same calendar day as the 18:00 open).
            daily = self._aggregate_rth_daily_bars(bars)
            bias = self._resolve_bias(daily, globex_session)
            if bias is None:
                return None

            tick = self._tick_size(symbol)
            offset = self.entry_offset_ticks * tick
            atr = self._compute_atr(bars)
            if atr <= 0:
                return None

            if bias == "LONG":
                entry = grange.high + offset
                stop = entry - self.stop_atr_multiplier * atr
                tp = entry + self.tp_r_multiple * (entry - stop)
                action = "LONG"
            else:
                entry = grange.low - offset
                stop = entry + self.stop_atr_multiplier * atr
                tp = entry - self.tp_r_multiple * (stop - entry)
                action = "SHORT"

            if action == "LONG" and stop >= entry:
                return None
            if action == "SHORT" and stop <= entry:
                return None

            last_close = self._val(last, "close", "c")
            if action == "LONG" and last_close > entry + atr:
                return None
            if action == "SHORT" and last_close < entry - atr:
                return None

            self._traded_globex[symbol] = globex_session
            self._entry_bar_seq[symbol] = self._bar_seq

            reason = (
                f"Globex {bias} freeze=({grange.low:.2f},{grange.high:.2f}) "
                f"atr={atr:.2f} entry={entry:.2f}"
            )
            logger.info(
                "🌙 globex_drift_continuation %s on %s @ %.2f (%s)",
                action, symbol, entry, reason,
            )
            return {
                "action": action,
                "symbol": symbol,
                "entry_price": round(entry, 4),
                "stop_loss": round(stop, 4),
                "take_profit": round(tp, 4),
                "confidence": 0.7,
                "reason": reason,
            }
        except Exception as exc:
            logger.error("globex_drift_continuation.analyze error for %s: %s", symbol, exc, exc_info=True)
            return None

    async def execute(self, signal: Dict[str, Any]) -> bool:
        try:
            side = "BUY" if signal["action"] == "LONG" else "SELL"
            qty = max(int(self.config.position_size or 1), 1)
            result = await self.place_bracket_order(
                symbol=signal["symbol"],
                side=side,
                quantity=qty,
                entry_price=signal["entry_price"],
                stop_loss_price=signal["stop_loss"],
                take_profit_price=signal["take_profit"],
                enable_breakeven=False,
            )
            if result and result.get("error"):
                logger.warning(
                    "globex_drift_continuation execute rejected %s %s: %s",
                    signal["action"], signal["symbol"], result.get("error"),
                )
                return False
            self.daily_trades += 1
            return True
        except Exception as exc:
            logger.error("globex_drift_continuation.execute error: %s", exc, exc_info=True)
            return False

    async def manage_positions(self) -> None:
        """No-op — position management is folded into ``analyze()`` via
        :meth:`_maybe_close_stale_positions` (time-based + 04:00 flat-out).
        Required by :class:`BaseStrategy` interface."""
        return None

    async def cleanup(self) -> None:
        """Reset session-state on stop."""
        self._range_for_symbol.clear()
        self._traded_globex.clear()
        self._entry_bar_seq.clear()
        self._bar_seq = 0
