"""
Overnight range **reversion** (failed-breakout fade).

Same session clock and filters as :class:`OvernightRangeStrategy`, but **signal flow**
matches ``morning_range_reversion`` applied to the **overnight** high/low:

1. Track the overnight range (evening → ``timing.overnight_end``).
2. After **``timing.market_open``** on the session-end ET date, wait for the **first**
   bar (see ``signal.fade_signal_timeframe``) whose **close** is **above** the overnight
   high or **below** the overnight low.
3. **High sweep** (close > H) → **SHORT** stop-entry at **H**, stop beyond the high
   (ATR × ``stop_atr_multiplier``), take-profit at the overnight **midpoint**.
4. **Low sweep** (close < L) → **LONG** stop-entry at **L**, symmetric.

This strategy **does not** use ``breakout_levels`` / ``monitor_breakout_levels`` proximity
arming (that path is for true breakouts on ``overnight_range`` only). The market-open
sequence refreshes range cache and clears any stray proximity templates.

Load ``config/strategies/overnight_reversion.toml``.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, date, time, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

try:
    import pytz
except ImportError:
    pytz = None  # Optional dependency

from strategies.overnight_range_strategy import (
    ATRData,
    OvernightRangeStrategy,
    RangeBreakOrder,
)

logger = logging.getLogger(__name__)


class OvernightReversionStrategy(OvernightRangeStrategy):
    """Failed-breakout fade vs the overnight box (first qualifying close after open)."""

    STRATEGY_CONFIG_ENV = "overnight_reversion"
    STRATEGY_TOML_STEM = "overnight_reversion"
    ANALYZE_SIGNAL_REASON = "Overnight range failed-breakout reversion (first close outside → fade to mid)"

    def __init__(self, trading_bot, config=None):
        super().__init__(trading_bot, config)
        # Proximity dual-side staging is for ``overnight_range`` breakouts only.
        self.breakout_monitor_enabled = False
        self.fade_signal_timeframe: str = self._cfg.get_str("signal.fade_signal_timeframe", "1m") or "1m"
        self.fade_lookback_bars: int = int(self._cfg.get_int("signal.fade_lookback_bars", 500))
        self.allow_long: bool = bool(self._cfg.get_bool("signal.allow_long", True))
        self.allow_short: bool = bool(self._cfg.get_bool("signal.allow_short", True))
        self._orv_state: Dict[str, Dict[str, Any]] = {}
        self._orv_last_signal_bar: Dict[str, datetime] = {}

    def _orv_get_state(self, symbol: str) -> Dict[str, Any]:
        sym = symbol.upper()
        if sym not in self._orv_state:
            self._orv_state[sym] = {"sess_end": None, "faded_this_session": False}
        return self._orv_state[sym]

    def _session_open_dt_et(self, sess_end: date) -> datetime:
        mo = self._parse_cfg_time(self.market_open_time)
        try:
            if pytz is not None and hasattr(self.timezone, "localize"):
                return self.timezone.localize(datetime.combine(sess_end, mo))
            tz = self.timezone
            return datetime.combine(sess_end, mo, tzinfo=tz)
        except Exception:
            return datetime.combine(sess_end, mo, tzinfo=timezone.utc)

    def _bar_timestamp_et(self, bar: Dict[str, Any]) -> Optional[datetime]:
        ts = bar.get("timestamp") or bar.get("time") or bar.get("t")
        if ts is None:
            return None
        if isinstance(ts, datetime):
            dt = ts
        else:
            try:
                import pandas as pd

                dt = pd.to_datetime(ts, utc=True).to_pydatetime()
            except Exception:
                try:
                    dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                except Exception:
                    return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(self.timezone)

    def _live_order_is_pending_entry(self, order: Dict[str, Any], sym: str) -> bool:
        osym = (order.get("symbol") or order.get("Symbol") or "") or ""
        if osym.upper() != sym.upper() and not osym.upper().startswith(sym.upper()):
            return False
        status = str(order.get("status", "") or "").lower()
        if status and status not in ("working", "pending", "submitted", "open", "new"):
            return False
        ot = str(order.get("type", "") or order.get("orderType", "") or "").lower()
        if "stop" in ot or ot in ("stop", "stopmarket"):
            return True
        return False

    def _has_open_position_or_pending_entry(self, symbol: str) -> bool:
        bot = self.trading_bot
        positions = getattr(bot, "active_positions", None)
        if positions:
            for p in positions if isinstance(positions, list) else positions.values():
                psym = (p.get("symbol") if isinstance(p, dict) else getattr(p, "symbol", "")) or ""
                if psym.upper() == symbol.upper():
                    return True

        engine = getattr(bot, "backtest_engine", None) or getattr(self, "_replay_engine", None)
        if engine is not None:
            poss = getattr(engine, "positions", None) or {}
            if symbol.upper() in {k.upper() for k in poss}:
                return True
            pend = getattr(engine, "pending_orders", None) or []
            for o in pend:
                osym = getattr(o, "symbol", "") or ""
                if osym.upper() != symbol.upper():
                    continue
                if getattr(o, "oco_group", None):
                    ot = getattr(o, "order_type", None)
                    name = getattr(ot, "name", str(ot)).upper()
                    if name in ("STOP", "LIMIT") and "EXIT" not in name:
                        return True
        return False

    async def _has_open_position_or_pending_entry_async(self, symbol: str) -> bool:
        bot = self.trading_bot
        engine = getattr(bot, "backtest_engine", None) or getattr(self, "_replay_engine", None)
        if engine is not None:
            return self._has_open_position_or_pending_entry(symbol)
        if getattr(bot, "_is_strategy_replay", False):
            return self._has_open_position_or_pending_entry(symbol)

        account_id = None
        if isinstance(bot.selected_account, dict):
            account_id = bot.selected_account.get("id")
        elif bot.selected_account:
            account_id = str(bot.selected_account)
        if not account_id:
            return False
        try:
            positions = await bot.get_open_positions(account_id=account_id)
            for p in positions:
                if (p.get("symbol") or "").upper() == symbol.upper():
                    return True
            orders = await bot.get_open_orders(account_id=account_id)
            for o in orders:
                if self._live_order_is_pending_entry(o, symbol):
                    return True
        except Exception as exc:
            logger.debug("overnight_reversion live lockout check failed: %s", exc)
            return False
        return False

    async def _execute_market_open_sequence(self, symbols=None) -> None:
        """Refresh overnight range cache; do **not** arm ``overnight_range`` proximity templates."""
        trade_symbols = self._get_trade_symbols(symbols)
        if not trade_symbols:
            return

        d_et = datetime.now(self.timezone).date()
        try:
            from core.market_calendar import equity_futures_session_note

            cal = equity_futures_session_note(d_et)
        except Exception as exc:
            logger.debug("Calendar check skipped: %s", exc, exc_info=True)
            cal = {"trade_recommended": True, "reason": ""}
        if not cal.get("trade_recommended", True):
            logger.warning(
                "Skipping overnight_reversion market-open housekeeping for %s: %s",
                d_et,
                cal.get("reason", "calendar"),
            )
            return

        await self._cancel_previous_session_orders(trade_symbols)
        logger.info(
            "overnight_reversion: refreshing overnight range cache for %s (no proximity breakout templates)",
            ", ".join(trade_symbols),
        )
        for symbol in trade_symbols:
            try:
                await self.track_overnight_range(symbol)
            except Exception as exc:
                logger.error("overnight_reversion track_overnight_range %s: %s", symbol, exc)
            await asyncio.sleep(0.2)

        for sym in trade_symbols:
            self.breakout_levels.pop(sym, None)
            self.breakout_active_orders.pop(sym, None)

    def _build_fade_signal(
        self,
        symbol: str,
        sweep: str,
        H: float,
        L: float,
        mid: float,
        atr_data: ATRData,
        bar_et: datetime,
        tick_size: float,
    ) -> Optional[Dict[str, Any]]:
        stop_mult = max(0.1, float(self._overnight_symbol_stop_atr_multiplier(symbol)))
        atr_v = max(1e-9, float(atr_data.current_atr))
        width = max(H - L, tick_size * 2)

        if sweep == "high":
            if not self.allow_short:
                return None
            action = "SHORT"
            entry = self.round_to_tick(float(H), tick_size)
            stop = self.round_to_tick(float(H) + atr_v * stop_mult, tick_size)
            tp = self.round_to_tick(float(mid), tick_size)
            if not (stop > entry > tp):
                logger.warning(
                    "overnight_reversion invalid SHORT fade %s: H=%.4f entry=%.4f stop=%.4f tp=%.4f",
                    symbol,
                    H,
                    entry,
                    stop,
                    tp,
                )
                return None
        else:
            if not self.allow_long:
                return None
            action = "LONG"
            entry = self.round_to_tick(float(L), tick_size)
            stop = self.round_to_tick(float(L) - atr_v * stop_mult, tick_size)
            tp = self.round_to_tick(float(mid), tick_size)
            if not (stop < entry < tp):
                logger.warning(
                    "overnight_reversion invalid LONG fade %s: L=%.4f entry=%.4f stop=%.4f tp=%.4f",
                    symbol,
                    L,
                    entry,
                    stop,
                    tp,
                )
                return None

        reason = (
            f"overnight_reversion sweep={sweep} first_close_outside H={H:.2f} L={L:.2f} "
            f"mid={mid:.2f} W={width:.2f} bar_et={bar_et.isoformat()}"
        )
        logger.info(
            "🎯 overnight_reversion %s on %s @ %.2f (%s)",
            action,
            symbol,
            entry,
            reason,
        )
        return {
            "action": action,
            "symbol": symbol,
            "entry_price": entry,
            "stop_loss": stop,
            "take_profit": tp,
            "confidence": 0.55,
            "reason": reason,
        }

    async def calculate_range_break_orders(self, symbol: str) -> Tuple[Optional[RangeBreakOrder], Optional[RangeBreakOrder]]:
        """Not used for order flow (see :meth:`analyze`). Kept for CLI / tooling compatibility."""
        return None, None

    async def analyze(self, symbol: str) -> Optional[Dict[str, Any]]:
        try:
            symbol = symbol.upper()

            now_et = self._effective_now_et()
            if self.filter_skip_weekdays and now_et is not None:
                if now_et.weekday() in self.filter_skip_weekdays:
                    logger.debug("Skipping %s — skip_weekdays (weekday=%s)", symbol, now_et.weekday())
                    return None

            if self._is_strategy_replay_mode():
                if now_et is None:
                    logger.debug("Skipping %s — replay mode but no bar timestamp on bot", symbol)
                    return None
                if not self._replay_in_order_placement_window(now_et):
                    return None
                _sess_start, sess_end = self._overnight_session_dates_et(now_et)
                _sess_key = (symbol.upper(), sess_end)
                if _sess_key not in self._replay_open_session_cancel_done:
                    self._replay_cancel_pending_stop_entries([symbol])
                    self._replay_open_session_cancel_done.add(_sess_key)
                if (symbol, sess_end) in self._replay_sessions_signaled:
                    return None

            range_data = await self.track_overnight_range(symbol)
            if not range_data:
                return None

            use_dynamic_atr = self._cfg.get_bool("position_management.use_dynamic_atr_for_orders", True)
            atr_data = await self.calculate_atr(symbol)
            if not atr_data:
                return None
            if use_dynamic_atr:
                dynamic_current_atr = await self.recalculate_current_atr(symbol)
                if dynamic_current_atr:
                    atr_data = ATRData(
                        current_atr=dynamic_current_atr,
                        daily_atr=atr_data.daily_atr,
                        atr_zone_high=atr_data.atr_zone_high,
                        atr_zone_low=atr_data.atr_zone_low,
                        period=atr_data.period,
                        market_open_price=atr_data.market_open_price,
                        day_bull_price=atr_data.day_bull_price,
                        day_bull_price1=atr_data.day_bull_price1,
                        day_bear_price=atr_data.day_bear_price,
                        day_bear_price1=atr_data.day_bear_price1,
                    )

            should_trade, reason = await self.check_market_conditions(symbol, range_data, atr_data)
            if not should_trade:
                logger.info("❌ overnight_reversion skipping %s: %s", symbol, reason)
                return None

            bars = await self.trading_bot.get_historical_data(
                symbol=symbol,
                timeframe=self.fade_signal_timeframe,
                limit=self.fade_lookback_bars,
            )
            if not bars or len(bars) < 2:
                return None

            last = bars[-1]
            bar_et = self._bar_timestamp_et(last)
            if bar_et is None:
                return None

            _, sess_end = self._overnight_session_dates_et(bar_et)
            open_dt = self._session_open_dt_et(sess_end)
            if bar_et < open_dt:
                return None

            st = self._orv_get_state(symbol)
            if st.get("sess_end") != sess_end:
                st.clear()
                st["sess_end"] = sess_end
                st["faded_this_session"] = False
                self._orv_last_signal_bar.pop(symbol, None)

            if st.get("faded_this_session"):
                return None

            prev = self._orv_last_signal_bar.get(symbol)
            if prev is not None and bar_et <= prev:
                return None

            if await self._has_open_position_or_pending_entry_async(symbol):
                return None

            H = float(range_data.high)
            L = float(range_data.low)
            mid = float(range_data.midpoint)
            if H <= L:
                return None

            c = float(last.get("close", last.get("c", 0)) or 0.0)

            sweep: Optional[str] = None
            if c > H:
                sweep = "high"
            elif c < L:
                sweep = "low"
            else:
                return None

            tick_size = await self.get_tick_size(symbol)
            sig = self._build_fade_signal(symbol, sweep, H, L, mid, atr_data, bar_et, tick_size)
            if not sig:
                return None

            self._orv_last_signal_bar[symbol] = bar_et
            st["faded_this_session"] = True
            return sig

        except Exception as exc:
            logger.error("overnight_reversion.analyze error for %s: %s", symbol, exc, exc_info=True)
            return None

    async def execute(self, signal: Dict[str, Any]) -> bool:
        if not isinstance(signal, dict) or "action" not in signal:
            return False
        try:
            side = "BUY" if signal["action"] == "LONG" else "SELL"
            qty = max(int(self._overnight_symbol_position_size(signal["symbol"])), 1)
            r0 = abs(float(signal["entry_price"]) - float(signal["stop_loss"]))
            be_thr = (
                float(self.breakeven_profit_points)
                if self.breakeven_enabled and r0 > 0 and self.breakeven_profit_points > 0
                else None
            )
            partial_on = bool(self._cfg.get_bool("signal.partial_tp_enabled", False))
            partial_r = float(self._cfg.get_float("signal.partial_tp_scalp_r", 1.0) or 1.0)
            result = await self.place_bracket_order(
                symbol=signal["symbol"],
                side=side,
                quantity=qty,
                entry_price=float(signal["entry_price"]),
                stop_loss_price=float(signal["stop_loss"]),
                take_profit_price=float(signal["take_profit"]),
                enable_breakeven=False,
                breakeven_profit_threshold=be_thr,
                partial_tp_enabled=partial_on and qty >= 2,
                partial_tp_scalp_r=partial_r,
            )
            if result and result.get("error"):
                logger.warning(
                    "overnight_reversion execute rejected %s %s: %s",
                    signal["action"],
                    signal["symbol"],
                    result.get("error"),
                )
                return False
            ok = bool(result and not result.get("error"))
            if ok and self._is_strategy_replay_mode():
                now_et = self._effective_now_et()
                if now_et is not None:
                    _sd, ed = self._overnight_session_dates_et(now_et)
                    self._replay_sessions_signaled.add((str(signal["symbol"]).upper(), ed))
            if ok:
                self.daily_trades += 1
            return ok
        except Exception as exc:
            logger.error("overnight_reversion.execute error: %s", exc, exc_info=True)
            return False
