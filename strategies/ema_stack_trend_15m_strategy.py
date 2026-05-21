"""15m EMA levels + 5m RSI extremes (mean-reversion at structure).

**RSI (5m, Wilder):** LONG when RSI < ``rsi_long_max`` (default 32); SHORT when RSI >
``rsi_short_min`` (default 68).

**EMAs (``signal.timeframe``, default 15m):** not stack/unstack entries — each period is a
support/resistance rail. **LONG** requires an oversold 5m RSI **and** a completed bar that
**tags** an EMA from above and **reclaims** it (low near EMA, close back above). **SHORT**
requires overbought RSI **and** a bar that **tags** EMA resistance and **rejects** (high
near EMA, close back below).

Replay on ``*_5m_databento.csv``: native **5m** for RSI; **15m** via mock resample for EMAs.

Config: ``config/strategies/ema_stack_trend_15m.toml`` (``meta.enabled = false``).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from strategies.strategy_base import BaseStrategy, StrategyConfig
from core.strategy_config import load_strategy_config

logger = logging.getLogger(__name__)


def _ema_last(closes: List[float], periods: Tuple[int, ...]) -> Dict[int, float]:
    """Last-bar EMA per period."""
    s = pd.Series(closes, dtype="float64")
    out: Dict[int, float] = {}
    for p in periods:
        out[int(p)] = float(s.ewm(span=int(p), adjust=False).mean().iloc[-1])
    return out


def _rsi_wilder_last(closes: List[float], period: int) -> Optional[float]:
    if period < 2 or len(closes) < period + 2:
        return None
    s = pd.Series(closes, dtype="float64")
    delta = s.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta.clip(upper=0.0))
    avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    ag = float(avg_gain.iloc[-1])
    al = float(avg_loss.iloc[-1])
    if ag <= 1e-15 and al <= 1e-15:
        return 50.0
    if al <= 1e-15:
        return 100.0
    rs = ag / al
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return float(rsi) if rsi == rsi else None


def _support_bounce_ema(
    o: float,
    h: float,
    l: float,
    c: float,
    emas: Dict[int, float],
    *,
    tol_pts: float,
    require_green: bool,
) -> Optional[Tuple[int, float]]:
    """Return (ema_period, ema_level) if bar bounced off EMA support."""
    best: Optional[Tuple[int, float]] = None
    best_dist = float("inf")
    for period in sorted(emas.keys()):
        level = emas[period]
        if level <= 0 or level != level:
            continue
        if l > level + tol_pts:
            continue
        if c <= level:
            continue
        if require_green and c <= o:
            continue
        dist = abs(l - level)
        if dist < best_dist:
            best_dist = dist
            best = (period, level)
    return best


def _resistance_reject_ema(
    o: float,
    h: float,
    l: float,
    c: float,
    emas: Dict[int, float],
    *,
    tol_pts: float,
    require_red: bool,
) -> Optional[Tuple[int, float]]:
    """Return (ema_period, ema_level) if bar rejected at EMA resistance."""
    best: Optional[Tuple[int, float]] = None
    best_dist = float("inf")
    for period in sorted(emas.keys()):
        level = emas[period]
        if level <= 0 or level != level:
            continue
        if h < level - tol_pts:
            continue
        if c >= level:
            continue
        if require_red and c >= o:
            continue
        dist = abs(h - level)
        if dist < best_dist:
            best_dist = dist
            best = (period, level)
    return best


class EmaStackTrend15mStrategy(BaseStrategy):
    """5m RSI extremes + 15m EMA support/resistance reactions."""

    NAME = "ema_stack_trend_15m"

    def __init__(self, trading_bot, config: Optional[StrategyConfig] = None):
        if config is None:
            config = StrategyConfig.from_env(self.NAME)
        super().__init__(trading_bot, config)

        self._cfg = load_strategy_config(self.NAME, env_prefix=f"{self.NAME.upper()}_")
        self.timeframe: str = self._cfg.get_str("signal.timeframe", "15m") or "15m"
        self.rsi_timeframe: str = self._cfg.get_str("signal.rsi_timeframe", "5m") or "5m"
        self.lookback_bars: int = int(self._cfg.get_int("signal.lookback_bars", 500))

        raw_periods = self._cfg.get_list("signal.ema_periods", [8, 21, 50, 100, 200])
        self.ema_periods: Tuple[int, ...] = tuple(int(x) for x in raw_periods)

        self.atr_period: int = int(self._cfg.get_int("signal.atr_period", 14))
        self.stop_atr_multiplier: float = float(self._cfg.get_float("signal.stop_atr_multiplier", 2.0))
        self.tp_r_multiple: float = float(self._cfg.get_float("signal.tp_r_multiple", 2.5))
        self.allow_long: bool = bool(self._cfg.get_bool("signal.allow_long", True))
        self.allow_short: bool = bool(self._cfg.get_bool("signal.allow_short", True))
        self.min_bars_between_signals: int = int(
            self._cfg.get_int("signal.min_bars_between_signals", 4)
        )
        self.entry_offset_ticks: float = float(
            self._cfg.get_float("signal.entry_offset_ticks", 0.0) or 0.0
        )

        self.rsi_period: int = int(self._cfg.get_int("signal.rsi_period", 14))
        self.rsi_long_max: float = float(self._cfg.get_float("signal.rsi_long_max", 32.0))
        self.rsi_short_min: float = float(self._cfg.get_float("signal.rsi_short_min", 68.0))
        self.reaction_tol_atr: float = float(self._cfg.get_float("signal.reaction_tol_atr", 0.35))
        self.reaction_require_reclaim_bar: bool = bool(
            self._cfg.get_bool("signal.reaction_require_reclaim_bar", True)
        )

        self.tick_sizes: Dict[str, float] = {
            "MNQ": 0.25,
            "NQ": 0.25,
            "MES": 0.25,
            "ES": 0.25,
            "MGC": 0.10,
            "GC": 0.10,
        }

        self._last_signal_bar: Dict[str, datetime] = {}
        self._bar_seq: int = 0
        self._last_signal_bar_count: Dict[str, int] = {}

        logger.info(
            "✅ ema_stack_trend_15m init: ema_tf=%s rsi_tf=%s periods=%s "
            "rsi_long<%.0f rsi_short>%.0f reaction_tol_atr=%.2f",
            self.timeframe,
            self.rsi_timeframe,
            self.ema_periods,
            self.rsi_long_max,
            self.rsi_short_min,
            self.reaction_tol_atr,
        )

    def _tick_size(self, symbol: str) -> float:
        return float(self.tick_sizes.get(symbol.upper(), 0.25))

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

    async def _has_open_position_or_pending_entry_async(self, symbol: str) -> bool:
        bot = self.trading_bot
        account_id = None
        if isinstance(bot.selected_account, dict):
            account_id = bot.selected_account.get("id")
        elif bot.selected_account:
            account_id = str(bot.selected_account)
        try:
            positions = (
                await bot.get_open_positions(account_id=account_id)
                if account_id
                else await bot.get_open_positions()
            )
            if positions:
                for p in positions if isinstance(positions, list) else positions.values():
                    psym = (p.get("symbol") if isinstance(p, dict) else getattr(p, "symbol", "")) or ""
                    if psym.upper() == symbol.upper():
                        return True
        except Exception:
            pass

        engine = getattr(bot, "backtest_engine", None) or getattr(self, "_replay_engine", None)
        if engine is not None:
            poss = getattr(engine, "positions", None) or {}
            if symbol.upper() in {k.upper() for k in poss}:
                return True
            pend = getattr(engine, "pending_orders", None) or []
            for o in pend:
                if (getattr(o, "symbol", "") or "").upper() != symbol.upper():
                    continue
                if getattr(o, "oco_group", None):
                    continue
                ot = getattr(getattr(o, "order_type", None), "name", "")
                if str(ot).upper() == "STOP":
                    return True
        return False

    async def _calculate_atr(self, symbol: str, timeframe: Optional[str] = None) -> float:
        tf = timeframe or self.timeframe
        bars = await self.trading_bot.get_historical_data(
            symbol=symbol,
            timeframe=tf,
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

    def _bar_timestamp_utc(self, bar: Dict[str, Any]) -> Optional[datetime]:
        ts = bar.get("timestamp") or bar.get("time") or bar.get("t")
        if ts is None:
            return None
        if isinstance(ts, datetime):
            dt = ts
        else:
            try:
                dt = pd.to_datetime(ts, utc=True).to_pydatetime()
            except Exception:
                try:
                    dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                except Exception:
                    return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    async def analyze(self, symbol: str) -> Optional[Dict[str, Any]]:
        try:
            if not self._in_trading_window():
                return None

            if await self._has_open_position_or_pending_entry_async(symbol):
                return None

            max_p = max(self.ema_periods) if self.ema_periods else 200
            rsi_need = self.rsi_period + 30
            ema_need = max(self.lookback_bars, max_p * 3, self.atr_period + 20)

            bars_rsi = await self.trading_bot.get_historical_data(
                symbol=symbol,
                timeframe=self.rsi_timeframe,
                limit=int(max(rsi_need, 80)),
            )
            bars_ema = await self.trading_bot.get_historical_data(
                symbol=symbol,
                timeframe=self.timeframe,
                limit=int(ema_need),
            )
            if not bars_rsi or len(bars_rsi) < self.rsi_period + 5:
                return None
            if not bars_ema or len(bars_ema) < max_p + 5:
                return None

            last_rsi_bar = bars_rsi[-1]
            last_ema_bar = bars_ema[-1]
            bar_ts = self._bar_timestamp_utc(last_rsi_bar)
            if bar_ts is None:
                return None

            self._bar_seq += 1
            prev_ts = self._last_signal_bar.get(symbol)
            if prev_ts is not None and bar_ts <= prev_ts:
                return None
            last_count = self._last_signal_bar_count.get(symbol)
            if last_count is not None and (self._bar_seq - last_count) < self.min_bars_between_signals:
                return None

            rsi_closes = [self._val(b, "close", "c") for b in bars_rsi]
            rsi_val = _rsi_wilder_last(rsi_closes, self.rsi_period)
            if rsi_val is None:
                return None

            ema_closes = [self._val(b, "close", "c") for b in bars_ema]
            emas = _ema_last(ema_closes, self.ema_periods)

            o = self._val(last_ema_bar, "open", "o")
            h = self._val(last_ema_bar, "high", "h")
            l = self._val(last_ema_bar, "low", "l")
            c = self._val(last_ema_bar, "close", "c")

            atr = await self._calculate_atr(symbol, self.timeframe)
            if atr <= 0:
                return None
            tol_pts = max(self.reaction_tol_atr * atr, self._tick_size(symbol))
            reclaim = self.reaction_require_reclaim_bar

            action: Optional[str] = None
            reaction: Optional[Tuple[int, float]] = None

            if self.allow_long and rsi_val < self.rsi_long_max:
                reaction = _support_bounce_ema(
                    o, h, l, c, emas, tol_pts=tol_pts, require_green=reclaim
                )
                if reaction is not None:
                    action = "LONG"

            if action is None and self.allow_short and rsi_val > self.rsi_short_min:
                reaction = _resistance_reject_ema(
                    o, h, l, c, emas, tol_pts=tol_pts, require_red=reclaim
                )
                if reaction is not None:
                    action = "SHORT"

            if action is None or reaction is None:
                return None

            ema_period, ema_level = reaction
            stop_dist = self.stop_atr_multiplier * atr
            tick = self._tick_size(symbol)
            offset = self.entry_offset_ticks * tick
            buffer = tol_pts

            if action == "LONG":
                entry = c + offset
                stop = min(entry - stop_dist, ema_level - buffer)
                if stop >= entry - tick:
                    stop = entry - stop_dist
                tp = entry + self.tp_r_multiple * (entry - stop)
            else:
                entry = c - offset
                stop = max(entry + stop_dist, ema_level + buffer)
                if stop <= entry + tick:
                    stop = entry + stop_dist
                tp = entry - self.tp_r_multiple * (stop - entry)

            self._last_signal_bar[symbol] = bar_ts
            self._last_signal_bar_count[symbol] = self._bar_seq

            reason = (
                f"rsi5m={rsi_val:.1f} ema{ema_period}@{ema_level:.2f} "
                f"reaction_{action.lower()} tol={tol_pts:.2f}"
            )
            logger.info("🎯 ema_stack_trend_15m %s %s @ %.2f (%s)", action, symbol, entry, reason)
            return {
                "action": action,
                "symbol": symbol,
                "entry_price": round(entry, 4),
                "stop_loss": round(stop, 4),
                "take_profit": round(tp, 4),
                "confidence": min(1.0, abs(50.0 - rsi_val) / 50.0),
                "reason": reason,
            }
        except Exception as exc:
            logger.error("ema_stack_trend_15m.analyze error for %s: %s", symbol, exc, exc_info=True)
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
                breakeven_profit_threshold=None,
            )
            if result and result.get("error"):
                logger.warning(
                    "ema_stack_trend_15m execute rejected %s %s: %s",
                    signal["action"],
                    signal["symbol"],
                    result.get("error"),
                )
                return False
            self.daily_trades += 1
            return True
        except Exception as exc:
            logger.error("ema_stack_trend_15m.execute error: %s", exc, exc_info=True)
            return False

    async def manage_positions(self) -> None:
        return None

    async def cleanup(self) -> None:
        pass
