"""15m RSI extreme **switch** — hook entries, oscillation exits.

**Enter (flat):** RSI **hook** (turning back while still oversold/overbought), EMA
support/resistance reaction, and not fighting a full opposing stack below/above the
200 EMA.

**Exit (in position):** take profit when RSI reaches ``rsi_profit_exit_*`` with
minimum favorable move; cut losers on ``loss_cut_atr``; full exit at opposite extreme.

Cycle lockout after each side until RSI returns to neutral band.

Config: ``config/strategies/rsi_switch_15m.toml``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional, Tuple

import pandas as pd

from strategies.strategy_base import BaseStrategy, StrategyConfig
from strategies.ema_stack_trend_15m_strategy import (
    _ema_last,
    _resistance_reject_ema,
    _rsi_wilder_last,
    _support_bounce_ema,
)
from core.strategy_config import load_strategy_config

logger = logging.getLogger(__name__)


def _bull_stack(emas: Dict[int, float], periods: Tuple[int, ...]) -> bool:
    ps = sorted(periods)
    return all(emas.get(ps[i], 0) > emas.get(ps[i + 1], 0) for i in range(len(ps) - 1))


def _bear_stack(emas: Dict[int, float], periods: Tuple[int, ...]) -> bool:
    ps = sorted(periods)
    return all(emas.get(ps[i], 0) < emas.get(ps[i + 1], 0) for i in range(len(ps) - 1))


def _rsi_values(
    closes: List[float],
    period: int,
) -> Tuple[Optional[float], Optional[float]]:
    if len(closes) < period + 3:
        return None, None
    now = _rsi_wilder_last(closes, period)
    prev = _rsi_wilder_last(closes[:-1], period)
    return now, prev


def _rsi_hook(
    rsi_now: float,
    rsi_prev: Optional[float],
    *,
    long_max: float,
    short_min: float,
    hook_buffer: float,
) -> Tuple[bool, bool]:
    """Oversold/overbought hook: RSI turning back while still in the extreme zone."""
    if rsi_prev is None:
        return False, False
    hook_long = (
        rsi_now > rsi_prev
        and rsi_now <= long_max + hook_buffer
        and min(rsi_now, rsi_prev) <= long_max
    )
    hook_short = (
        rsi_now < rsi_prev
        and rsi_now >= short_min - hook_buffer
        and max(rsi_now, rsi_prev) >= short_min
    )
    return hook_long, hook_short


def _rsi_edge(
    rsi_now: float,
    rsi_prev: Optional[float],
    *,
    long_max: float,
    short_min: float,
) -> Tuple[bool, bool]:
    """Cross into extreme zone (used for exit timing)."""
    low_edge = rsi_now <= long_max and (rsi_prev is None or rsi_prev > long_max)
    high_edge = rsi_now >= short_min and (rsi_prev is None or rsi_prev < short_min)
    return low_edge, high_edge


def decide_exit_or_hold(
    *,
    side: str,
    rsi: float,
    entry_price: float,
    bars_held: int,
    atr: float,
    c: float,
    rsi_short_min: float,
    rsi_long_max: float,
    rsi_profit_exit_long: float,
    rsi_profit_exit_short: float,
    min_hold_bars: int,
    flat_move_atr: float,
    loss_cut_atr: float,
) -> Literal["EXIT", "HOLD"]:
    """Take the RSI oscillation leg: scalp mid-zone profits, always switch at opposite extreme."""
    if bars_held < min_hold_bars:
        return "HOLD"

    move = (c - entry_price) if side == "LONG" else (entry_price - c)
    profit_thr = flat_move_atr * atr
    loss_thr = loss_cut_atr * atr

    if move <= -loss_thr:
        return "EXIT"

    if side == "LONG":
        if rsi >= rsi_short_min:
            return "EXIT"
        if move >= profit_thr and rsi >= rsi_profit_exit_long:
            return "EXIT"
    else:
        if rsi <= rsi_long_max:
            return "EXIT"
        if move >= profit_thr and rsi <= rsi_profit_exit_short:
            return "EXIT"

    return "HOLD"


@dataclass
class _PositionTrack:
    side: str
    entry_bar_seq: int
    entry_price: float
    entry_rsi: float


class RsiSwitch15mStrategy(BaseStrategy):
    """15m RSI threshold switch with quality entries and oscillation exits."""

    NAME = "rsi_switch_15m"

    def __init__(self, trading_bot, config: Optional[StrategyConfig] = None):
        if config is None:
            config = StrategyConfig.from_env(self.NAME)
        super().__init__(trading_bot, config)

        self._cfg = load_strategy_config(self.NAME, env_prefix=f"{self.NAME.upper()}_")
        self.timeframe: str = self._cfg.get_str("signal.timeframe", "15m") or "15m"
        self.lookback_bars: int = int(self._cfg.get_int("signal.lookback_bars", 500))

        raw_periods = self._cfg.get_list("signal.ema_periods", [8, 21, 50, 100, 200])
        self.ema_periods: Tuple[int, ...] = tuple(int(x) for x in raw_periods)

        self.rsi_period: int = int(self._cfg.get_int("signal.rsi_period", 14))
        self.rsi_long_max: float = float(self._cfg.get_float("signal.rsi_long_max", 30.0))
        self.rsi_short_min: float = float(self._cfg.get_float("signal.rsi_short_min", 70.0))
        self.rsi_neutral_high: float = float(self._cfg.get_float("signal.rsi_neutral_high", 48.0))
        self.rsi_neutral_low: float = float(self._cfg.get_float("signal.rsi_neutral_low", 52.0))
        self.rsi_profit_exit_long: float = float(
            self._cfg.get_float("signal.rsi_profit_exit_long", 58.0)
        )
        self.rsi_profit_exit_short: float = float(
            self._cfg.get_float("signal.rsi_profit_exit_short", 42.0)
        )
        self.rsi_hook_buffer: float = float(self._cfg.get_float("signal.rsi_hook_buffer", 4.0))
        self.rsi_deep_extra: float = float(self._cfg.get_float("signal.rsi_deep_extra", 2.0))
        self.rsi_timeframe: str = self._cfg.get_str("signal.rsi_timeframe", "15m") or "15m"
        self.require_confirm_candle: bool = bool(
            self._cfg.get_bool("signal.require_confirm_candle", True)
        )

        self.atr_period: int = int(self._cfg.get_int("signal.atr_period", 14))
        self.stop_atr_multiplier: float = float(self._cfg.get_float("signal.stop_atr_multiplier", 2.0))
        self.tp_r_multiple: float = float(self._cfg.get_float("signal.tp_r_multiple", 1.25))
        self.reaction_tol_atr: float = float(self._cfg.get_float("signal.reaction_tol_atr", 0.45))
        self.require_ema_reaction: bool = bool(
            self._cfg.get_bool("signal.require_ema_reaction", True)
        )
        self.block_counter_trend: bool = bool(self._cfg.get_bool("signal.block_counter_trend", True))

        self.min_hold_bars: int = int(self._cfg.get_int("signal.min_hold_bars", 2))
        self.flat_move_atr: float = float(self._cfg.get_float("signal.flat_move_atr", 0.12))
        self.loss_cut_atr: float = float(self._cfg.get_float("signal.loss_cut_atr", 0.85))
        self.min_bars_between_entries: int = int(
            self._cfg.get_int("signal.min_bars_between_entries", 6)
        )
        self.entry_offset_ticks: float = float(
            self._cfg.get_float("signal.entry_offset_ticks", 0.0) or 0.0
        )

        self.tick_sizes: Dict[str, float] = {
            "MNQ": 0.25,
            "MES": 0.25,
            "MGC": 0.10,
        }

        self._bar_seq: int = 0
        self._last_entry_bar_count: Dict[str, int] = {}
        self._track: Dict[str, _PositionTrack] = {}
        self._last_bar_ts: Dict[str, datetime] = {}
        # 'neutral' | 'cooldown_long' | 'cooldown_short' — one swing per side until RSI resets
        self._cycle: Dict[str, str] = {}

        logger.info(
            "✅ rsi_switch_15m: hook rsi<=%.0f/>=%.0f ema_rx=%s tp_r=%.2f",
            self.rsi_long_max,
            self.rsi_short_min,
            self.require_ema_reaction,
            self.tp_r_multiple,
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

    def _engine(self):
        bot = self.trading_bot
        return getattr(bot, "backtest_engine", None) or getattr(self, "_replay_engine", None)

    def _position_side_from_engine(self, symbol: str) -> Optional[str]:
        engine = self._engine()
        if engine is None:
            return None
        poss = getattr(engine, "positions", None) or {}
        for key, pos in poss.items():
            if str(key).upper() != symbol.upper():
                continue
            from core.backtest.models import OrderSide

            if getattr(pos, "side", None) == OrderSide.BUY:
                return "LONG"
            return "SHORT"
        return None

    def _update_cycle(self, sym: str, rsi: float, pos_side: Optional[str]) -> None:
        if pos_side is not None:
            return
        cycle = self._cycle.get(sym, "neutral")
        if cycle == "cooldown_long" and rsi >= self.rsi_neutral_high:
            self._cycle[sym] = "neutral"
        elif cycle == "cooldown_short" and rsi <= self.rsi_neutral_low:
            self._cycle[sym] = "neutral"

    def _can_enter_long(self, sym: str) -> bool:
        return self._cycle.get(sym, "neutral") == "neutral"

    def _can_enter_short(self, sym: str) -> bool:
        return self._cycle.get(sym, "neutral") == "neutral"

    async def _calculate_atr(self, symbol: str) -> float:
        bars = await self.trading_bot.get_historical_data(
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

    async def _close_position_market(self, symbol: str, *, reason: str) -> bool:
        engine = self._engine()
        if engine is None:
            return False
        poss = getattr(engine, "positions", None) or {}
        pos = poss.get(symbol)
        sym = symbol
        if pos is None:
            for k, p in list(poss.items()):
                if str(k).upper() == symbol.upper():
                    pos = p
                    sym = k
                    break
            else:
                return False

        pending = getattr(engine, "pending_orders", None) or []
        for o in list(pending):
            if getattr(o, "symbol", "") != sym:
                continue
            if getattr(o, "oco_group", None):
                try:
                    pending.remove(o)
                except ValueError:
                    pass

        from core.backtest.models import OrderSide, OrderType

        close_side = (
            OrderSide.SELL if getattr(pos, "side", None) == OrderSide.BUY else OrderSide.BUY
        )
        px = float(getattr(pos, "current_price", getattr(pos, "entry_price", 0.0)))
        close_id = engine.place_order(
            symbol=sym,
            side=close_side,
            quantity=int(getattr(pos, "quantity", 1)),
            order_type=OrderType.MARKET,
            price=px,
        )
        for o in engine.pending_orders:
            if o.order_id == close_id:
                o.exit_reason = reason
                break
        self._track.pop(symbol.upper(), None)
        return True

    def _entry_quality(
        self,
        action: str,
        *,
        rsi_now: float,
        rsi_prev: Optional[float],
        o: float,
        h: float,
        l: float,
        c: float,
        emas: Dict[int, float],
        tol_pts: float,
    ) -> Optional[str]:
        """Return None if OK else skip reason."""
        if self.require_confirm_candle:
            if action == "LONG" and c <= o:
                return "no_confirm_candle"
            if action == "SHORT" and c >= o:
                return "no_confirm_candle"

        if rsi_prev is not None and self.rsi_deep_extra > 0:
            deep_long = self.rsi_long_max - self.rsi_deep_extra
            deep_short = self.rsi_short_min + self.rsi_deep_extra
            if action == "LONG" and min(rsi_now, rsi_prev) > deep_long:
                return "rsi_not_deep"
            if action == "SHORT" and max(rsi_now, rsi_prev) < deep_short:
                return "rsi_not_deep"

        if self.block_counter_trend:
            ema_slow = emas.get(200, emas.get(max(self.ema_periods, default=200), c))
            if action == "LONG" and _bear_stack(emas, self.ema_periods) and c < ema_slow:
                return "bear_stack"
            if action == "SHORT" and _bull_stack(emas, self.ema_periods) and c > ema_slow:
                return "bull_stack"

        if self.require_ema_reaction:
            if action == "LONG":
                if _support_bounce_ema(o, h, l, c, emas, tol_pts=tol_pts, require_green=False) is None:
                    return "no_ema_support"
            elif _resistance_reject_ema(o, h, l, c, emas, tol_pts=tol_pts, require_red=False) is None:
                return "no_ema_resistance"
        return None

    async def analyze(self, symbol: str) -> Optional[Dict[str, Any]]:
        try:
            if not self._in_trading_window():
                return None

            max_p = max(self.ema_periods) if self.ema_periods else 200
            ema_need = max(self.lookback_bars, max_p * 3, self.atr_period + 20)
            rsi_need = self.rsi_period + 30

            bars_ema = await self.trading_bot.get_historical_data(
                symbol=symbol,
                timeframe=self.timeframe,
                limit=int(ema_need),
            )
            bars_rsi = await self.trading_bot.get_historical_data(
                symbol=symbol,
                timeframe=self.rsi_timeframe,
                limit=int(max(rsi_need, 80)),
            )
            if not bars_ema or len(bars_ema) < max_p + 5:
                return None
            if not bars_rsi or len(bars_rsi) < self.rsi_period + 5:
                return None

            last = bars_ema[-1]
            bar_ts = self._bar_timestamp_utc(last)
            if bar_ts is None:
                return None

            sym = symbol.upper()
            prev_ts = self._last_bar_ts.get(sym)
            if prev_ts is not None and bar_ts <= prev_ts:
                return None
            self._last_bar_ts[sym] = bar_ts
            self._bar_seq += 1

            closes = [self._val(b, "close", "c") for b in bars_rsi]
            rsi_now, rsi_prev = _rsi_values(closes, self.rsi_period)
            if rsi_now is None:
                return None
            low_edge, high_edge = _rsi_edge(
                rsi_now,
                rsi_prev,
                long_max=self.rsi_long_max,
                short_min=self.rsi_short_min,
            )
            hook_long, hook_short = _rsi_hook(
                rsi_now,
                rsi_prev,
                long_max=self.rsi_long_max,
                short_min=self.rsi_short_min,
                hook_buffer=self.rsi_hook_buffer,
            )

            ema_closes = [self._val(b, "close", "c") for b in bars_ema]
            emas = _ema_last(ema_closes, self.ema_periods)
            o = self._val(last, "open", "o")
            h = self._val(last, "high", "h")
            l = self._val(last, "low", "l")
            c = self._val(last, "close", "c")
            atr = await self._calculate_atr(symbol)
            if atr <= 0:
                return None
            tol_pts = max(self.reaction_tol_atr * atr, self._tick_size(symbol))

            pos_side = self._position_side_from_engine(symbol)
            self._update_cycle(sym, rsi_now, pos_side)
            track = self._track.get(sym)
            if pos_side is None:
                self._track.pop(sym, None)
                track = None
            elif track is None:
                entry_px = c
                engine = self._engine()
                if engine:
                    poss = getattr(engine, "positions", None) or {}
                    p = poss.get(symbol) or poss.get(sym)
                    if p is not None:
                        entry_px = float(getattr(p, "entry_price", c))
                track = _PositionTrack(
                    side=pos_side,
                    entry_bar_seq=self._bar_seq,
                    entry_price=entry_px,
                    entry_rsi=rsi_now,
                )
                self._track[sym] = track

            # --- Manage open position (any bar, not only edges) ---
            if pos_side is not None and track is not None:
                bars_held = self._bar_seq - track.entry_bar_seq
                ema_closes_exit = [self._val(b, "close", "c") for b in bars_ema]
                rsi_exit, _ = _rsi_values(ema_closes_exit, self.rsi_period)
                exit_rsi = rsi_exit if rsi_exit is not None else rsi_now
                choice = decide_exit_or_hold(
                    side=pos_side,
                    rsi=exit_rsi,
                    entry_price=track.entry_price,
                    bars_held=bars_held,
                    atr=atr,
                    c=c,
                    rsi_short_min=self.rsi_short_min,
                    rsi_long_max=self.rsi_long_max,
                    rsi_profit_exit_long=self.rsi_profit_exit_long,
                    rsi_profit_exit_short=self.rsi_profit_exit_short,
                    min_hold_bars=self.min_hold_bars,
                    flat_move_atr=self.flat_move_atr,
                    loss_cut_atr=self.loss_cut_atr,
                )
                if choice == "EXIT":
                    await self._close_position_market(symbol, reason="rsi_switch_exit")
                    if pos_side == "LONG":
                        self._cycle[sym] = "cooldown_long"
                    else:
                        self._cycle[sym] = "cooldown_short"
                    logger.info(
                        "🔁 rsi_switch_15m EXIT %s rsi=%.1f held=%d",
                        symbol,
                        exit_rsi,
                        bars_held,
                    )
                return None

            if not hook_long and not hook_short:
                return None

            last_entry = self._last_entry_bar_count.get(sym)
            if last_entry is not None and (self._bar_seq - last_entry) < self.min_bars_between_entries:
                return None

            action: Optional[str] = None
            if hook_long and self._can_enter_long(sym):
                action = "LONG"
            elif hook_short and self._can_enter_short(sym):
                action = "SHORT"
            if action is None:
                return None

            skip = self._entry_quality(
                action,
                rsi_now=rsi_now,
                rsi_prev=rsi_prev,
                o=o,
                h=h,
                l=l,
                c=c,
                emas=emas,
                tol_pts=tol_pts,
            )
            if skip:
                logger.debug("rsi_switch_15m %s skip entry: %s", symbol, skip)
                return None

            stop_dist = self.stop_atr_multiplier * atr
            tick = self._tick_size(symbol)
            offset = self.entry_offset_ticks * tick
            reaction = (
                _support_bounce_ema(o, h, l, c, emas, tol_pts=tol_pts, require_green=False)
                if action == "LONG"
                else _resistance_reject_ema(o, h, l, c, emas, tol_pts=tol_pts, require_red=False)
            )
            ema_level = reaction[1] if reaction else c

            if action == "LONG":
                entry = c + offset
                stop = min(entry - stop_dist, ema_level - tol_pts)
                if stop >= entry - tick:
                    stop = entry - stop_dist
                tp = entry + self.tp_r_multiple * (entry - stop)
            else:
                entry = c - offset
                stop = max(entry + stop_dist, ema_level + tol_pts)
                if stop <= entry + tick:
                    stop = entry + stop_dist
                tp = entry - self.tp_r_multiple * (stop - entry)

            self._last_entry_bar_count[sym] = self._bar_seq
            self._track[sym] = _PositionTrack(
                side=action,
                entry_bar_seq=self._bar_seq,
                entry_price=entry,
                entry_rsi=rsi_now,
            )

            reason = f"rsi_switch_{action.lower()} rsi={rsi_now:.1f}"
            logger.info("🎯 rsi_switch_15m %s %s @ %.2f (%s)", action, symbol, entry, reason)
            return {
                "action": action,
                "symbol": symbol,
                "entry_price": round(entry, 4),
                "stop_loss": round(stop, 4),
                "take_profit": round(tp, 4),
                "confidence": min(1.0, abs(50.0 - rsi_now) / 50.0),
                "reason": reason,
            }
        except Exception as exc:
            logger.error("rsi_switch_15m.analyze error for %s: %s", symbol, exc, exc_info=True)
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
                    "rsi_switch_15m execute rejected %s %s: %s",
                    signal["action"],
                    signal["symbol"],
                    result.get("error"),
                )
                self._track.pop(str(signal["symbol"]).upper(), None)
                self._cycle[str(signal["symbol"]).upper()] = "neutral"
                return False
            sym = str(signal["symbol"]).upper()
            act = str(signal.get("action", "")).upper()
            self._cycle[sym] = "cooldown_long" if act == "LONG" else "cooldown_short"
            self.daily_trades += 1
            return True
        except Exception as exc:
            logger.error("rsi_switch_15m.execute error: %s", exc, exc_info=True)
            return False

    async def manage_positions(self) -> None:
        return None

    async def cleanup(self) -> None:
        pass
