"""7:00–7:59 US/Eastern anchor-hour sweep → stop-entry at breached extreme.

Spec (from operator):
- Anchor range = high/low of 5m bars whose **open** in US/Eastern is in ``[07:00, 08:00)``.
- Trigger = first 5m bar **close** outside that range after 08:00.
- On trigger:
  - Close above range high ⇒ arm a SHORT bracket with stop-entry SELL at range high (anticipate re-entry).
  - Close below range low  ⇒ arm a LONG bracket with stop-entry BUY at range low.
- TP = 50% (midline) of anchor range; SL = 1:1 (same distance as TP from entry).

Notes:
- Uses the standard BaseStrategy bracket path (`place_oco_bracket_with_stop_entry`) so replay matches live wiring.
- One sequence per session date (first sweep only). After a bracket is armed, the strategy will not arm another
  until the next session date.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, time, timezone
from typing import Any, Dict, Optional

import pandas as pd

from strategies.strategy_base import BaseStrategy, StrategyConfig
from core.strategy_config import load_strategy_config

logger = logging.getLogger(__name__)


def _load_tz(name: str):
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    except Exception:
        try:
            import pytz

            return pytz.timezone(name)
        except Exception:
            return None


def _parse_hhmm(s: Optional[str], fallback: time) -> time:
    if not s:
        return fallback
    try:
        parts = str(s).strip().split(":")
        if len(parts) != 2:
            return fallback
        return time(int(parts[0]), int(parts[1]))
    except Exception:
        return fallback


@dataclass
class _SessionState:
    session_date: Optional[datetime.date] = None
    range_hi: Optional[float] = None
    range_lo: Optional[float] = None
    range_ready: bool = False
    armed: bool = False  # bracket entry placed (pending) for the day


class HourlyAnchorRetraceStrategy(BaseStrategy):
    """Hourly anchor retrace (7–8 ET anchor hour)."""

    NAME = "hourly_anchor_retrace"

    def __init__(self, trading_bot, config: Optional[StrategyConfig] = None):
        if config is None:
            config = StrategyConfig.from_env(self.NAME)
        super().__init__(trading_bot, config)

        self._cfg = load_strategy_config(self.NAME, env_prefix=f"{self.NAME.upper()}_")
        self.timeframe: str = self._cfg.get_str("signal.timeframe", "5m") or "5m"
        self.lookback_bars: int = int(self._cfg.get_int("signal.lookback_bars", 400))
        self.session_zone: str = self._cfg.get_str("signal.session_timezone", "America/New_York")
        self._tz = _load_tz(self.session_zone)
        self.anchor_start: time = _parse_hhmm(self._cfg.get_str("signal.anchor_start", "07:00"), time(7, 0))
        self.anchor_end_open: time = _parse_hhmm(self._cfg.get_str("signal.anchor_end_open", "08:00"), time(8, 0))
        self.flat_before: time = _parse_hhmm(self._cfg.get_str("signal.flat_before", "16:00"), time(16, 0))
        self.allow_long: bool = bool(self._cfg.get_bool("signal.allow_long", True))
        self.allow_short: bool = bool(self._cfg.get_bool("signal.allow_short", True))
        self.entry_offset_ticks: float = float(self._cfg.get_float("signal.entry_offset_ticks", 0.0) or 0.0)
        self.min_range_pts: float = float(self._cfg.get_float("signal.min_range_pts", 0.0) or 0.0)
        self.max_range_pts: float = float(self._cfg.get_float("signal.max_range_pts", 0.0) or 0.0)

        self.tick_sizes: Dict[str, float] = {
            "MNQ": 0.25,
            "NQ": 0.25,
            "MES": 0.25,
            "ES": 0.25,
            "MGC": 0.10,
            "GC": 0.10,
        }

        self._state: Dict[str, _SessionState] = {}

        logger.info(
            "✅ hourly_anchor_retrace init: tf=%s session=%s anchor=%s-%s flat_before=%s offset_ticks=%.2f range=[%.1f, %.1f]",
            self.timeframe,
            self.session_zone,
            self.anchor_start,
            self.anchor_end_open,
            self.flat_before,
            self.entry_offset_ticks,
            self.min_range_pts,
            self.max_range_pts,
        )

    def _tick_size(self, symbol: str) -> float:
        return float(self.tick_sizes.get(symbol.upper(), 0.25))

    def _round_px(self, symbol: str, x: float) -> float:
        t = self._tick_size(symbol)
        return round(round(float(x) / t) * t, 6)

    def _bar_timestamp_eastern(self, bar: Dict[str, Any]) -> Optional[datetime]:
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
        if not self._tz:
            return dt
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

    async def _has_open_position_or_pending_entry_async(self, symbol: str) -> bool:
        bot = self.trading_bot
        account_id = None
        if isinstance(bot.selected_account, dict):
            account_id = bot.selected_account.get("id")
        elif bot.selected_account:
            account_id = str(bot.selected_account)
        try:
            positions = await bot.get_open_positions(account_id=account_id) if account_id else await bot.get_open_positions()
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
                # Any pending STOP without oco_group is an entry stop in our replay model.
                if getattr(o, "oco_group", None):
                    continue
                ot = getattr(getattr(o, "order_type", None), "name", "")
                if str(ot).upper() == "STOP":
                    return True

        return False

    def _get_state(self, symbol: str) -> _SessionState:
        sym = symbol.upper()
        if sym not in self._state:
            self._state[sym] = _SessionState()
        return self._state[sym]

    async def analyze(self, symbol: str) -> Optional[Dict[str, Any]]:
        try:
            bars = await self.trading_bot.get_historical_data(
                symbol=symbol,
                timeframe=self.timeframe,
                limit=self.lookback_bars,
            )
            if not bars or len(bars) < 10:
                return None

            last = bars[-1]
            bar_et = self._bar_timestamp_eastern(last)
            if bar_et is None:
                return None

            st = self._get_state(symbol)
            d = bar_et.date()
            if st.session_date != d:
                st.session_date = d
                st.range_hi = None
                st.range_lo = None
                st.range_ready = False
                st.armed = False

            # Prevent duplicates while there is an open position or pending entry.
            if await self._has_open_position_or_pending_entry_async(symbol):
                return None
            if st.armed:
                return None

            hi = self._val(last, "high", "h")
            lo = self._val(last, "low", "l")
            c = self._val(last, "close", "c")
            t_open = bar_et.time()

            # Build anchor range during [07:00, 08:00) based on bar OPEN time.
            if not st.range_ready:
                if self.anchor_start <= t_open < self.anchor_end_open:
                    st.range_hi = hi if st.range_hi is None else max(float(st.range_hi), hi)
                    st.range_lo = lo if st.range_lo is None else min(float(st.range_lo), lo)
                    return None

                # Pre-anchor bars: do nothing.
                if t_open < self.anchor_start:
                    return None

                # First bar at/after 08:00: finalize range.
                if st.range_hi is None or st.range_lo is None:
                    st.range_ready = True
                    return None
                H = float(st.range_hi)
                L = float(st.range_lo)
                if H <= L:
                    st.range_ready = True
                    return None
                st.range_hi = H
                st.range_lo = L
                st.range_ready = True

            H = float(st.range_hi or 0.0)
            L = float(st.range_lo or 0.0)
            if H <= L:
                return None
            width = H - L
            if self.min_range_pts and width < float(self.min_range_pts):
                return None
            if self.max_range_pts and float(self.max_range_pts) > 0 and width > float(self.max_range_pts):
                return None
            half = width / 2.0
            off = max(0.0, float(self.entry_offset_ticks or 0.0)) * self._tick_size(symbol)

            # Trigger: first 5m close outside anchor range AFTER anchor window.
            if t_open >= self.anchor_end_open:
                if c > H and self.allow_short:
                    entry = self._round_px(symbol, H - off)
                    stop = self._round_px(symbol, entry + half)
                    tp = self._round_px(symbol, entry - half)
                    st.armed = True
                    return {
                        "action": "SHORT",
                        "symbol": symbol,
                        "entry_price": entry,
                        "stop_loss": stop,
                        "take_profit": tp,
                        "confidence": 0.5,
                        "reason": f"hourly_anchor_retrace close>{H:.2f} anchor H={H:.2f} L={L:.2f}",
                    }
                if c < L and self.allow_long:
                    entry = self._round_px(symbol, L + off)
                    stop = self._round_px(symbol, entry - half)
                    tp = self._round_px(symbol, entry + half)
                    st.armed = True
                    return {
                        "action": "LONG",
                        "symbol": symbol,
                        "entry_price": entry,
                        "stop_loss": stop,
                        "take_profit": tp,
                        "confidence": 0.5,
                        "reason": f"hourly_anchor_retrace close<{L:.2f} anchor H={H:.2f} L={L:.2f}",
                    }

            return None
        except Exception as exc:
            logger.error("hourly_anchor_retrace.analyze error for %s: %s", symbol, exc, exc_info=True)
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
                logger.warning("hourly_anchor_retrace execute rejected %s: %s", signal.get("action"), result.get("error"))
                return False
            self.daily_trades += 1
            return True
        except Exception as exc:
            logger.error("hourly_anchor_retrace.execute error: %s", exc, exc_info=True)
            return False

    async def manage_positions(self) -> None:
        # Brackets handle exits; optional time-based flat can be added later.
        return None

    async def cleanup(self) -> None:
        self._state.clear()
        return None

