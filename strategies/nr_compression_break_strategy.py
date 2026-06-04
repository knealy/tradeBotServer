"""NR Compression Break (Toby Crabel NR7 daily-TF compression breakout).

**Alpha thesis**
    A daily bar whose true range is the **narrowest of the last 7 daily
    bars** ("NR7" in Crabel's *Day Trading with Short Term Price
    Patterns*) signals that intraday volatility has compressed.  The
    market is coiling.  The first directional breakout of the following
    session's range has a measurable continuation edge, particularly
    when paired with a continuation bias from the prior session's
    body direction.

**Strategy v1 (this implementation)**
    - Aggregate 5m RTH bars into per-symbol per-ET-date daily OHLC.
    - Each session-end (last bar of RTH ≥ 15:55 ET), check whether
      "yesterday" was an NR7 (true range was the min of the last
      ``nr_lookback`` daily bars, default 7).
    - **Directional bias**: yesterday's RTH close vs open picks today's
      side.  Close > open → LONG only; close < open → SHORT only.
      (V2 may emit symmetric brackets with OCO management; V1 picks one
      direction for simplicity + cleaner risk.)
    - On the next session's first eligible bar inside
      ``entry_window_et_start`` … ``entry_window_et_end``, place a stop-
      entry bracket:
        * entry  = NR-high + entry_offset_ticks (LONG)  /  NR-low − offset (SHORT)
        * stop   = entry ∓ ``stop_atr_multiplier × ATR(14)``  (default 0.8 ×)
        * TP     = entry ± ``tp_r_multiple × |entry − stop|`` (default 2.0 R)
    - **Force-flat** at ``flat_et`` (default 15:55 ET) via the shared
      :mod:`core.max_hold_exit` helper so all positions are closed
      regardless of MFE / MAE.
    - One trade per symbol per day; consec-loss breaker per-symbol; the
      portfolio daily-loss breaker (Phase 1) is consulted via
      :func:`core.portfolio_daily_breaker.PortfolioDailyBreaker.replay_evaluate`.

**Acceptance criteria (per ``docs/STRATEGY_ARSENAL.md``)**
    - 9-month walk-forward on MNQ + MES + MGC.
    - RF ≥ 10.0 net, max DD ≤ 50 %, ≥ 25 fills, PF ≥ 1.5.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, List, Optional

from core.max_hold_exit import close_positions_exceeding_max_hold
from core.strategy_config import load_strategy_config
from strategies.strategy_base import (
    BaseStrategy,
    MarketCondition,
    StrategyConfig,
    StrategyStatus,
)

logger = logging.getLogger(__name__)


def _parse_hhmm(value: str, fallback: time) -> time:
    try:
        hh, mm = value.split(":")
        return time(int(hh), int(mm))
    except Exception:
        return fallback


@dataclass
class _DailyBar:
    session_date: date
    open: float
    high: float
    low: float
    close: float
    @property
    def range_(self) -> float:
        return self.high - self.low


class NrCompressionBreakStrategy(BaseStrategy):
    """NR7 compression-break (Crabel) on MNQ / MES / MGC."""

    NAME = "nr_compression_break"

    def __init__(self, trading_bot, config: Optional[StrategyConfig] = None):
        if config is None:
            config = StrategyConfig.from_env(self.NAME)
        super().__init__(trading_bot, config)

        self._cfg = load_strategy_config(self.NAME, env_prefix=f"{self.NAME.upper()}_")

        self.timeframe: str = self._cfg.get_str("signal.timeframe", "5m") or "5m"
        self.lookback_bars: int = int(self._cfg.get_int("signal.lookback_bars", 2000))
        self.nr_lookback: int = int(self._cfg.get_int("signal.nr_lookback", 7))
        self.atr_period: int = int(self._cfg.get_int("signal.atr_period", 14))
        self.stop_atr_multiplier: float = float(
            self._cfg.get_float("signal.stop_atr_multiplier", 0.8)
        )
        self.tp_r_multiple: float = float(self._cfg.get_float("signal.tp_r_multiple", 2.0))
        self.entry_offset_ticks: int = int(self._cfg.get_int("signal.entry_offset_ticks", 1))
        self.max_hold_bars: int = int(self._cfg.get_int("signal.max_hold_bars", 78))  # full RTH

        # ET entry / flat-out windows.  Crabel's spec is "next-day open"; we
        # widen to allow the break to fire any time during RTH so a delayed
        # break still gets caught.
        self.entry_window_start = _parse_hhmm(
            self._cfg.get_str("signal.entry_window_et_start", "09:30"), time(9, 30),
        )
        self.entry_window_end = _parse_hhmm(
            self._cfg.get_str("signal.entry_window_et_end", "14:30"), time(14, 30),
        )
        self.flat_et = _parse_hhmm(
            self._cfg.get_str("signal.flat_et", "15:55"), time(15, 55),
        )
        # RTH window for daily-bar aggregation.  Default matches CME equity-
        # index regular session: 09:30 - 16:00 ET.
        self.rth_start = _parse_hhmm(
            self._cfg.get_str("signal.rth_start_et", "09:30"), time(9, 30),
        )
        self.rth_end = _parse_hhmm(
            self._cfg.get_str("signal.rth_end_et", "16:00"), time(16, 0),
        )

        # Use yesterday's close vs open as a continuation cue.
        self.use_continuation_bias: bool = bool(
            self._cfg.get_bool("signal.use_continuation_bias", True)
        )
        # Optional: skip when yesterday's body is too small (a doji NR7 has no
        # clear bias).  Body / range minimum to keep a signal.
        self.min_body_pct: float = float(self._cfg.get_float("signal.min_body_pct", 0.10))

        # Per-symbol day state — reset each new session.  Tracks whether we
        # already attempted a trade today (so only one fire per day).
        self._traded_today: Dict[str, date] = {}
        # Per-symbol setup (yesterday's NR7).  Built at session rollover.
        self._setup_for_symbol: Dict[str, Optional[Dict[str, Any]]] = {}
        # Per-symbol entry-bar counter for max_hold_bars exits.
        self._entry_bar_seq: Dict[str, int] = {}
        self._bar_seq: int = 0

        # Tick sizes — overlap with risk_sizer; canonical source is
        # ``core.risk_management.RiskManager.TICK_SIZES``.
        self._tick_sizes = {"MNQ": 0.25, "MES": 0.25, "MGC": 0.10}

        # ET timezone reference
        try:
            from zoneinfo import ZoneInfo
            self._tz = ZoneInfo("America/New_York")
        except Exception:
            self._tz = timezone(timedelta(hours=-5))

        # Portfolio daily-loss breaker (Phase 1 infra).  Lazy — only
        # instantiates on first analyze() call so unit tests + import-only
        # checks stay cheap.
        self._portfolio_breaker = None

    # ------------------------------------------------------------------
    # Required BaseStrategy interface
    # ------------------------------------------------------------------

    def get_strategy_info(self) -> Dict[str, Any]:
        return {
            "name": self.NAME,
            "description": "NR7 compression breakout (Toby Crabel daily-TF)",
            "type": "BREAKOUT-CONTINUE",
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

    def _aggregate_rth_daily_bars(self, bars: List[Dict[str, Any]]) -> List[_DailyBar]:
        """Group RTH 5m bars by ET session date → :class:`_DailyBar`.

        Only bars within ``[rth_start, rth_end)`` ET contribute.  Returns
        a list sorted by session_date ascending.
        """
        daily: Dict[date, _DailyBar] = {}
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
                daily[d] = _DailyBar(session_date=d, open=o, high=h, low=l, close=c)
            else:
                db = daily[d]
                db.high = max(db.high, h)
                db.low = min(db.low, l)
                db.close = c  # last bar of the session wins
        return sorted(daily.values(), key=lambda b: b.session_date)

    def _detect_nr7_setup(self, daily_bars: List[_DailyBar]) -> Optional[Dict[str, Any]]:
        """If the LAST completed daily bar is an NR7 (range min over the
        last ``nr_lookback`` daily bars), return the setup dict.  Else
        ``None``.

        Setup dict keys: ``session_date`` (the NR7 day), ``armed_long``,
        ``armed_short``, ``nr_range``, ``body_pct``, ``bias`` ("LONG"/"SHORT"/None).
        """
        if len(daily_bars) < self.nr_lookback:
            return None
        window = daily_bars[-self.nr_lookback:]
        ranges = [b.range_ for b in window]
        if not all(r > 0 for r in ranges):
            return None
        if window[-1].range_ != min(ranges):
            return None

        nr = window[-1]
        body = abs(nr.close - nr.open)
        body_pct = body / nr.range_ if nr.range_ > 0 else 0.0
        bias: Optional[str] = None
        if self.use_continuation_bias:
            if body_pct < self.min_body_pct:
                return None  # doji NR7 — no bias to lean on
            bias = "LONG" if nr.close > nr.open else "SHORT"

        return {
            "session_date": nr.session_date,
            "nr_open": nr.open,
            "nr_high": nr.high,
            "nr_low": nr.low,
            "nr_close": nr.close,
            "nr_range": nr.range_,
            "body_pct": body_pct,
            "bias": bias,  # ``None`` ⇒ no directional preference
        }

    def _compute_atr(self, bars: List[Dict[str, Any]]) -> float:
        """Simple ATR (mean of last ``atr_period`` true ranges) over the
        full bar buffer.  Insufficient data → 0."""
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

    def _maybe_close_stale_positions(self, symbol: str, bar_ts_et: datetime) -> None:
        """Time-based + EOD exit:
        1. If the position has been held ≥ ``max_hold_bars``, close.
        2. If we're at/after ``flat_et`` and a position exists, close it
           regardless of hold-bar count.
        """
        bot = self.trading_bot
        engine = getattr(bot, "backtest_engine", None) or getattr(self, "_replay_engine", None)
        if engine is None:
            return
        # 1) max_hold_bars helper (shared with mean_reversion etc.)
        if self.max_hold_bars > 0:
            close_positions_exceeding_max_hold(
                engine=engine,
                max_hold_bars=self.max_hold_bars,
                current_bar_seq=self._bar_seq,
                entry_bar_seq=self._entry_bar_seq,
                log_prefix=self.NAME,
                logger=logger,
            )
        # 2) Force-flat at EOD — use a one-shot dict that forces the close.
        if bar_ts_et.time() >= self.flat_et:
            positions = getattr(engine, "positions", None) or {}
            if symbol in positions:
                # Synthesize an entry_bar_seq that guarantees held > 1, then
                # call with max_hold_bars=1 to force the close on this symbol.
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
            self._portfolio_breaker = False  # sentinel: disabled
            return
        from core.portfolio_daily_breaker import PortfolioBreakerConfig, PortfolioDailyBreaker
        self._portfolio_breaker = PortfolioDailyBreaker(
            trading_bot=None, config=PortfolioBreakerConfig(daily_loss_cap_dollars=cap),
        )

    def _portfolio_breaker_tripped(self, bar_ts_et: datetime) -> bool:
        """Backtest-only consult of the portfolio breaker (live path goes
        through the event bus + StrategyManager.PORTFOLIO_KILL listener)."""
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
            min_required = max(self.atr_period + 5, self.nr_lookback * 78 + 10)
            if not bars or len(bars) < min_required:
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

            today = bar_ts.date()

            # Already traded today on this symbol?  one-per-day rule.
            if self._traded_today.get(symbol) == today:
                return None

            # Build/refresh setup: aggregate daily bars from RTH 5m bars,
            # detect NR7 on the LAST COMPLETED day (i.e. yesterday).
            daily = self._aggregate_rth_daily_bars(bars)
            # We need at least nr_lookback complete prior sessions + today
            # (today's incomplete bar would otherwise be mistaken as NR7).
            prior = [b for b in daily if b.session_date < today]
            if len(prior) < self.nr_lookback:
                return None
            setup = self._detect_nr7_setup(prior)
            if setup is None:
                return None

            # Entry window check.
            t = bar_ts.time()
            if t < self.entry_window_start or t >= self.entry_window_end:
                return None

            bias = setup["bias"]  # 'LONG' / 'SHORT' / None
            if bias is None:
                # symmetric mode not yet implemented in v1
                return None

            tick = self._tick_size(symbol)
            offset = self.entry_offset_ticks * tick
            atr = self._compute_atr(bars)
            if atr <= 0:
                return None

            if bias == "LONG":
                entry = setup["nr_high"] + offset
                stop = entry - self.stop_atr_multiplier * atr
                tp = entry + self.tp_r_multiple * (entry - stop)
                action = "LONG"
            else:
                entry = setup["nr_low"] - offset
                stop = entry + self.stop_atr_multiplier * atr
                tp = entry - self.tp_r_multiple * (stop - entry)
                action = "SHORT"

            # Sanity: refuse degenerate brackets where stop ≥ entry for LONG
            # (or stop ≤ entry for SHORT).
            if action == "LONG" and stop >= entry:
                return None
            if action == "SHORT" and stop <= entry:
                return None

            # Reasonable distance — refuse trades where the bar is already
            # WAY past the armed level (chasing).  Skip if current close is
            # already > entry by > 1 ATR (LONG) or < entry by > 1 ATR (SHORT).
            last_close = self._val(last, "close", "c")
            if action == "LONG" and last_close > entry + atr:
                return None
            if action == "SHORT" and last_close < entry - atr:
                return None

            self._traded_today[symbol] = today
            self._entry_bar_seq[symbol] = self._bar_seq

            reason = (
                f"NR7 bias={bias} nr_range={setup['nr_range']:.2f} "
                f"body_pct={setup['body_pct']:.2f} atr={atr:.2f} entry={entry:.2f}"
            )
            logger.info(
                "🎯 nr_compression_break %s on %s @ %.2f (%s)",
                action, symbol, entry, reason,
            )
            return {
                "action": action,
                "symbol": symbol,
                "entry_price": round(entry, 4),
                "stop_loss": round(stop, 4),
                "take_profit": round(tp, 4),
                "confidence": min(1.0, setup["body_pct"]),
                "reason": reason,
            }
        except Exception as exc:
            logger.error("nr_compression_break.analyze error for %s: %s", symbol, exc, exc_info=True)
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
                    "nr_compression_break execute rejected %s %s: %s",
                    signal["action"], signal["symbol"], result.get("error"),
                )
                return False
            self.daily_trades += 1
            return True
        except Exception as exc:
            logger.error("nr_compression_break.execute error: %s", exc, exc_info=True)
            return False

    async def manage_positions(self) -> None:
        """Per-bar position management is handled inside ``analyze()`` via
        :meth:`_maybe_close_stale_positions` (time-based + EOD flat-out).
        Required by :class:`BaseStrategy` interface — no-op here because
        the work is folded into the analyze loop."""
        return None

    async def cleanup(self) -> None:
        """Idle the strategy — clear armed-setup state.  Required by
        :class:`BaseStrategy` interface."""
        self._traded_today.clear()
        self._setup_for_symbol.clear()
        self._entry_bar_seq.clear()
        self._bar_seq = 0
