"""VWAP Z-score reversion strategy.

Hypothesis: in liquid index/commodity micros (MNQ/MES/MGC), the intraday
volume-weighted average price (VWAP) is a strong daily attractor. Sharp
deviations of the close from VWAP — measured in standard deviations of the
``close - VWAP`` series — tend to mean-revert toward VWAP within the same
session.

Workflow per bar (RTH only, 1m default):

1. Build the session VWAP from bars whose timestamp falls in the current
   US/Eastern RTH window.
2. Compute the residual ``r_t = close_t - VWAP_t`` and its rolling standard
   deviation over ``window_bars``.
3. Trigger on Z = ``r_t / sigma`` crossing thresholds:
     - ``Z <= -z_entry``  → LONG
     - ``Z >=  z_entry``  → SHORT
4. Place a stop-bracket entry **one tick** beyond the current close (so a
   tiny confirmation move opens the position both in live and in replay,
   where ``StrategyReplayEngine`` only models stop-entry brackets), with:
     - **TP** at the current VWAP price (mean-reversion target).
     - **SL** at ``stop_atr_multiplier * ATR`` away on the wrong side.

The strategy is **research-grade**: it does not modify any live trading hot
path and is opt-in via ``[meta] enabled = false`` by default in the TOML.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, time
from typing import Any, Dict, List, Optional

from strategies.strategy_base import (
    BaseStrategy,
    MarketCondition,
    StrategyConfig,
    StrategyStatus,
)
from core.strategy_config import load_strategy_config

logger = logging.getLogger(__name__)


def _parse_hhmm(value: str, fallback: time) -> time:
    try:
        hh, mm = value.split(":")
        return time(int(hh), int(mm))
    except Exception:
        return fallback


class VwapZscoreReversionStrategy(BaseStrategy):
    """Intraday VWAP mean reversion with ATR-based stops."""

    NAME = "vwap_zscore_reversion"

    def __init__(self, trading_bot, config: Optional[StrategyConfig] = None):
        if config is None:
            config = StrategyConfig.from_env(self.NAME)
        super().__init__(trading_bot, config)

        self._cfg = load_strategy_config(self.NAME, env_prefix=f"{self.NAME.upper()}_")

        self.timeframe: str = self._cfg.get_str("signal.timeframe", "1m") or "1m"
        self.lookback_bars: int = int(self._cfg.get_int("signal.lookback_bars", 600))
        self.window_bars: int = int(self._cfg.get_int("signal.window_bars", 30))
        self.z_entry: float = float(self._cfg.get_float("signal.z_entry", 2.0))
        self.z_max: float = float(self._cfg.get_float("signal.z_max", 5.0))
        self.stop_atr_multiplier: float = float(
            self._cfg.get_float("signal.stop_atr_multiplier", 1.5)
        )
        self.atr_period: int = int(self._cfg.get_int("signal.atr_period", 14))
        self.min_session_bars: int = int(
            self._cfg.get_int("signal.min_session_bars", 15)
        )

        rth_only = self._cfg.get_bool("signal.rth_only", True)
        self.rth_start: time = _parse_hhmm(
            self._cfg.get_str("signal.rth_start", "09:35"), time(9, 35)
        )
        self.rth_end: time = _parse_hhmm(
            self._cfg.get_str("signal.rth_end", "15:30"), time(15, 30)
        )
        self.rth_only = rth_only

        self.session_zone = self._cfg.get_str("signal.session_timezone", "US/Eastern")
        self._tz = self._load_tz(self.session_zone)

        self.entry_offset_ticks: float = float(
            self._cfg.get_float("signal.entry_offset_ticks", 1.0)
        )
        self.tick_sizes: Dict[str, float] = {
            "MNQ": 0.25, "NQ": 0.25,
            "MES": 0.25, "ES": 0.25,
            "MGC": 0.10, "GC": 0.10,
            "MYM": 1.0, "YM": 1.0,
            "M2K": 0.10, "RTY": 0.10,
        }

        self._last_signal_session: Dict[str, str] = {}
        self._last_signal_minute: Dict[str, datetime] = {}

        logger.info(
            "✅ VWAP Z-score reversion initialized: tf=%s window=%d z_entry=%.2f stop_atr=%.2f rth=%s",
            self.timeframe,
            self.window_bars,
            self.z_entry,
            self.stop_atr_multiplier,
            "on" if rth_only else "off",
        )

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _load_tz(name: str):
        try:
            from zoneinfo import ZoneInfo

            return ZoneInfo(name)
        except Exception:
            try:
                import pytz

                return pytz.timezone(name)
            except Exception:
                return timezone.utc

    def _tick_size(self, symbol: str) -> float:
        return self.tick_sizes.get(symbol.upper(), 0.25)

    def _now_eastern(self) -> datetime:
        anchor = getattr(self.trading_bot, "_current_bar_timestamp", None)
        if anchor is not None:
            if anchor.tzinfo is None:
                anchor = anchor.replace(tzinfo=timezone.utc)
            return anchor.astimezone(self._tz)
        return datetime.now(self._tz)

    def _session_key(self, ts_eastern: datetime) -> str:
        return ts_eastern.strftime("%Y-%m-%d")

    def _in_rth_window(self, ts_eastern: datetime) -> bool:
        if not self.rth_only:
            return True
        t = ts_eastern.time()
        return self.rth_start <= t <= self.rth_end

    def _bar_timestamp_eastern(self, bar: Dict[str, Any]) -> Optional[datetime]:
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

    # ------------------------------------------------------------------ analysis

    async def analyze(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Return a signal dict or ``None``.

        Side-effect free: only reads recent bars, computes VWAP / Z-score,
        and emits a candidate trade for ``execute()`` (or the replay engine).
        """
        try:
            now_et = self._now_eastern()
            session = self._session_key(now_et)
            if not self._in_rth_window(now_et):
                return None

            last_session = self._last_signal_session.get(symbol)
            if last_session == session:
                return None

            bars = await self.trading_bot.get_historical_data(
                symbol=symbol,
                timeframe=self.timeframe,
                limit=self.lookback_bars,
            )
            if not bars or len(bars) < max(self.window_bars + 5, self.min_session_bars):
                return None

            session_bars: List[Dict[str, Any]] = []
            for bar in bars:
                bar_et = self._bar_timestamp_eastern(bar)
                if bar_et is None:
                    continue
                if self._session_key(bar_et) != session:
                    continue
                if self.rth_only and not self._in_rth_window(bar_et):
                    continue
                session_bars.append(bar)

            if len(session_bars) < self.min_session_bars:
                return None

            cum_pv = 0.0
            cum_vol = 0.0
            residuals: List[float] = []
            last_close = 0.0
            last_vwap = 0.0
            for bar in session_bars:
                hi = self._val(bar, "high", "h")
                lo = self._val(bar, "low", "l")
                cl = self._val(bar, "close", "c")
                vol = self._val(bar, "volume", "v", default=1.0)
                if vol <= 0:
                    vol = 1.0
                tp = (hi + lo + cl) / 3.0
                cum_pv += tp * vol
                cum_vol += vol
                vwap = cum_pv / cum_vol if cum_vol > 0 else cl
                residuals.append(cl - vwap)
                last_close = cl
                last_vwap = vwap

            tail = residuals[-self.window_bars :]
            if len(tail) < self.window_bars:
                return None
            mean = sum(tail) / len(tail)
            var = sum((x - mean) ** 2 for x in tail) / max(len(tail) - 1, 1)
            sigma = var ** 0.5
            if sigma <= 0:
                return None

            z = (last_close - last_vwap) / sigma
            if abs(z) < self.z_entry or abs(z) > self.z_max:
                return None

            atr = await self._calculate_atr(symbol)
            if atr <= 0:
                return None
            stop_dist = self.stop_atr_multiplier * atr
            tick = self._tick_size(symbol)
            offset = self.entry_offset_ticks * tick

            if z <= -self.z_entry:
                action = "LONG"
                entry = last_close + offset
                stop = entry - stop_dist
                take = max(last_vwap, entry + tick)
            else:
                action = "SHORT"
                entry = last_close - offset
                stop = entry + stop_dist
                take = min(last_vwap, entry - tick)

            self._last_signal_session[symbol] = session
            self._last_signal_minute[symbol] = now_et

            reason = (
                f"Z={z:+.2f} VWAP={last_vwap:.2f} close={last_close:.2f} "
                f"sigma={sigma:.2f} atr={atr:.2f}"
            )
            logger.info(
                "🎯 vwap_zscore signal %s on %s: %s",
                action,
                symbol,
                reason,
            )

            return {
                "action": action,
                "symbol": symbol,
                "entry_price": round(entry, 4),
                "stop_loss": round(stop, 4),
                "take_profit": round(take, 4),
                "confidence": min(1.0, abs(z) / max(self.z_max, self.z_entry + 1.0)),
                "reason": reason,
            }
        except Exception as exc:
            logger.error("vwap_zscore.analyze error for %s: %s", symbol, exc, exc_info=True)
            return None

    async def _calculate_atr(self, symbol: str) -> float:
        bot = self.trading_bot
        if hasattr(bot, "calculate_atr"):
            try:
                value = await bot.calculate_atr(symbol, period=self.atr_period)
                if value:
                    return float(value)
            except Exception:
                pass
        bars = await bot.get_historical_data(
            symbol=symbol,
            timeframe=self.timeframe,
            limit=self.atr_period + 5,
        )
        if not bars or len(bars) < self.atr_period + 1:
            return 0.0
        trs: List[float] = []
        prev_close = self._val(bars[0], "close", "c")
        for bar in bars[1:]:
            hi = self._val(bar, "high", "h")
            lo = self._val(bar, "low", "l")
            cl = self._val(bar, "close", "c")
            tr = max(hi - lo, abs(hi - prev_close), abs(lo - prev_close))
            trs.append(tr)
            prev_close = cl
        if not trs:
            return 0.0
        return sum(trs[-self.atr_period :]) / min(len(trs), self.atr_period)

    # ------------------------------------------------------------------ execute

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
                    "vwap_zscore execute rejected %s %s: %s",
                    signal["action"],
                    signal["symbol"],
                    result.get("error"),
                )
                return False
            self.daily_trades += 1
            return True
        except Exception as exc:
            logger.error("vwap_zscore.execute error: %s", exc, exc_info=True)
            return False

    async def manage_positions(self) -> None:
        # Bracket OCO drives exits; nothing to do here.
        return None

    async def cleanup(self) -> None:
        self.status = StrategyStatus.IDLE

    def _in_trading_window(self) -> bool:
        # Replay drives time via _current_bar_timestamp; skip the live wall-clock gate.
        if getattr(self.trading_bot, "_is_strategy_replay", False):
            return True
        return super()._in_trading_window()

    def get_market_condition(self, symbol: str) -> MarketCondition:
        return MarketCondition.RANGING
