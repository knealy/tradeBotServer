"""Price-action fade (MVP) — fade dragonfly-doji reversals at Order Block confluence.

This is the minimum-viable productionization of the cross-symbol edge we
characterised in ``scripts/simulate_price_action_trades.py``:

* Pattern: ``dragonfly_doji`` (small body at top, long lower wick — the
  classical "bullish reversal" candle).
* Edge: the bullish-reversal signal **fails** when it forms inside a
  confirmed bearish Order Block — institutions sold here, the wick-rejection
  doesn't stick, price drops.  Trade direction = ``SHORT`` (contrarian fade).
* Geometry: SL = ``stop_atr_multiplier × ATR(period)`` above entry;
  TP = ``tp_r_multiple × stop_dist`` below.  Defaults 1.0 × ATR SL,
  2.0 R TP — matches the simulator's baseline (mean R = +0.907 on MES).
* Max hold 12 bars (configurable) — time-based unwind so a winner doesn't
  drift back to BE.

The MVP is **single-signal, single-direction, single-symbol** (MES) on
purpose.  Once the live path is proved we layer:
* Bullish counterpart (gravestone_doji + bullish OB → LONG).
* MNQ (works in simulator) and MGC (neutral, may drop).
* Adaptive exit: 3R TP + 0.5 × ATR trail after 1R MFE (MES +46 % vs baseline).

Walk-forward truth lives in ``docs/perf/_opt_runs/price_action_fade/`` once
the first sweep is committed.  All knobs in
``config/strategies/price_action_fade.toml``.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

try:
    import pytz  # type: ignore
except ImportError:  # pragma: no cover
    pytz = None

from core.market_structure import (
    OrderBlock,
    find_order_blocks,
    is_inside_order_block,
)
from core.price_action import CandleBar, Pattern, detect_patterns
from core.strategy_config import load_strategy_config
from strategies.strategy_base import BaseStrategy, StrategyConfig

logger = logging.getLogger(__name__)


__all__ = ["PriceActionFadeStrategy"]


# ───────────────────────── helpers ──────────────────────────


def _load_tz(name: str):
    if pytz is not None:
        try:
            return pytz.timezone(name)
        except Exception:
            pass
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:
        return timezone.utc


def _parse_hhmm(s: str, default: time) -> time:
    if not s:
        return default
    try:
        h, m = s.strip().split(":")
        return time(int(h), int(m))
    except Exception:
        logger.warning("price_action_fade: invalid HH:MM %r — using %s", s, default)
        return default


def _bar_to_candle(b: Dict[str, Any]) -> Optional[CandleBar]:
    """Convert a bot ``bars`` dict into the ``CandleBar`` that the
    market-structure / price-action detectors consume.  Returns None when
    OHLC is malformed (zero/negative)."""
    try:
        ts = b.get("timestamp") or b.get("time") or b.get("t")
        o = float(b.get("open", b.get("o", 0.0)) or 0.0)
        h = float(b.get("high", b.get("h", 0.0)) or 0.0)
        lo = float(b.get("low", b.get("l", 0.0)) or 0.0)
        c = float(b.get("close", b.get("c", 0.0)) or 0.0)
        v = float(b.get("volume", b.get("v", 0.0)) or 0.0)
        if not (h > 0 and lo > 0 and c > 0 and o > 0):
            return None
        if isinstance(ts, datetime):
            dt = ts
        elif isinstance(ts, str):
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        elif isinstance(ts, (int, float)):
            dt = datetime.fromtimestamp(ts / 1000 if ts > 1e12 else ts, tz=timezone.utc)
        else:
            return None
        return CandleBar(timestamp=dt, open=o, high=h, low=lo, close=c, volume=v)
    except Exception:
        return None


# ────────────────────── strategy class ───────────────────────


class PriceActionFadeStrategy(BaseStrategy):
    """Fade a dragonfly_doji formed inside a confirmed bearish Order Block.

    The signal is symmetric (we'll add the gravestone_doji + bullish OB
    counterpart in v2 once MVP earns its keep).  Entry is a market-style
    bracket placed at the pattern bar's close.
    """

    NAME = "price_action_fade"
    ANALYZE_SIGNAL_REASON = "dragonfly_doji at bearish OB — contrarian SHORT fade"

    def __init__(self, trading_bot, config: Optional[StrategyConfig] = None):
        if config is None:
            config = StrategyConfig.from_env(self.NAME)
        super().__init__(trading_bot, config)

        self._cfg = load_strategy_config(self.NAME, env_prefix=f"{self.NAME.upper()}_")
        self.timeframe: str = self._cfg.get_str("signal.timeframe", "5m") or "5m"
        self.lookback_bars: int = int(self._cfg.get_int("signal.lookback_bars", 300) or 300)
        self.session_zone: str = self._cfg.get_str("signal.session_timezone", "America/New_York") or "America/New_York"
        self._tz = _load_tz(self.session_zone)

        # Active hours (executor wall-clock); the strategy itself doesn't
        # care about session unless ``rth_only`` is set.
        self.rth_only: bool = bool(self._cfg.get_bool("signal.rth_only", False))
        self.rth_start: time = _parse_hhmm(self._cfg.get_str("signal.rth_start", "09:30"), time(9, 30))
        self.rth_end: time = _parse_hhmm(self._cfg.get_str("signal.rth_end", "15:55"), time(15, 55))

        # ATR-based geometry (always — this is a momentum-exhaustion fade,
        # range-fraction doesn't apply).
        self.atr_period: int = int(self._cfg.get_int("signal.atr_period", 14) or 14)
        self.stop_atr_multiplier: float = float(
            self._cfg.get_float("signal.stop_atr_multiplier", 1.0) or 1.0
        )
        self.tp_r_multiple: float = float(self._cfg.get_float("signal.tp_r_multiple", 2.0) or 2.0)
        self.entry_offset_ticks: int = int(self._cfg.get_int("signal.entry_offset_ticks", 0) or 0)

        # Order-block detector knobs.  Defaults match the simulator
        # findings — 2.0 × ATR impulse over 5 bars within 14-bar ATR.
        self.ob_impulse_threshold_atr: float = float(
            self._cfg.get_float("signal.ob_impulse_threshold_atr", 2.0) or 2.0
        )
        self.ob_window: int = int(self._cfg.get_int("signal.ob_window", 5) or 5)

        # Direction-mode controls (MVP = short-only fade).
        self.allow_short: bool = bool(self._cfg.get_bool("signal.allow_short", True))
        self.allow_long: bool = bool(self._cfg.get_bool("signal.allow_long", False))

        self.max_hold_bars: int = int(self._cfg.get_int("signal.max_hold_bars", 12) or 12)
        self.min_bars_between_signals: int = int(
            self._cfg.get_int("signal.min_bars_between_signals", 3) or 3
        )

        # Executor active window hint — let analyze() skip outside RTH if rth_only.
        try:
            if self.rth_only:
                self.replay_active_window_et = [(self.rth_start, self.rth_end)]
            else:
                self.replay_active_window_et = None
        except Exception:
            self.replay_active_window_et = None

        # Per-symbol cooldown / dedup state.
        self._last_signal_bar_ts: Dict[str, datetime] = {}
        self._bar_seq: int = 0
        self._last_signal_seq: Dict[str, int] = defaultdict(lambda: -10_000)
        self._entry_bar_seq: Dict[str, int] = {}
        self.daily_trades: int = 0
        self._daily_trades_date: Optional[date] = None

        self._breaker_registered: bool = False
        self._maybe_register_breaker()

    # ─────────────────── consec-loss breaker bridge ────────────────────

    def _maybe_register_breaker(self) -> None:
        try:
            from core.consec_loss_breaker import register_strategy_breaker
        except Exception:
            return
        try:
            max_losses = int(self._cfg.get_int("signal.max_consecutive_losses", 0) or 0)
            cooldown = int(self._cfg.get_int("signal.loss_streak_cooldown_sessions", 0) or 0)
            threshold = float(
                self._cfg.get_float("signal.rolling_pnl_loss_threshold_dollars", 0.0) or 0.0
            )
            if max_losses <= 0:
                return
            register_strategy_breaker(
                strategy_name=self.NAME,
                max_consecutive_losses=max_losses,
                cooldown_sessions=cooldown,
                loss_threshold_dollars=threshold,
            )
            self._breaker_registered = True
        except Exception as exc:
            logger.debug("price_action_fade: breaker registration skipped: %s", exc)

    def _evaluate_breaker(self, symbol: str, sess_date: date) -> Dict[str, Any]:
        if not self._breaker_registered:
            return {"blocked": False}
        try:
            from core.consec_loss_breaker import evaluate_breaker
            return evaluate_breaker(
                strategy_name=self.NAME, symbol=symbol, bar_session_date=sess_date,
                trading_bot=self.trading_bot,
            )
        except Exception as exc:
            logger.debug("price_action_fade: breaker eval failed: %s", exc)
            return {"blocked": False}

    # ──────────────────────── helpers ───────────────────────────

    def _is_replay_mode(self) -> bool:
        return bool(getattr(self.trading_bot, "_is_strategy_replay", False))

    def _bar_timestamp_et(self, bar: Dict[str, Any]) -> Optional[datetime]:
        ts = bar.get("timestamp") or bar.get("time") or bar.get("t")
        if ts is None:
            return None
        if isinstance(ts, datetime):
            dt = ts
        elif isinstance(ts, str):
            try:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except ValueError:
                return None
        elif isinstance(ts, (int, float)):
            dt = datetime.fromtimestamp(ts / 1000 if ts > 1e12 else ts, tz=timezone.utc)
        else:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        try:
            return dt.astimezone(self._tz)
        except Exception:
            return None

    def _position_size(self, symbol: str) -> int:
        try:
            raw = self._cfg.symbol_override(
                symbol.upper(), "risk.position_size",
                default=int(self._cfg.get_int("risk.position_size", 1) or 1), hint=int,
            )
            return max(1, int(raw))
        except Exception:
            return max(1, int(self.config.position_size or 1))

    def _tick_size(self, symbol: str) -> float:
        sym = symbol.upper()
        # Conservative defaults; not critical for ATR-scaled geometry but
        # used for the entry-offset tick rounding.
        return {"MNQ": 0.25, "MES": 0.25, "MGC": 0.10}.get(sym, 0.25)

    @staticmethod
    def _atr_from_bars(bars: List[Dict[str, Any]], period: int) -> Optional[float]:
        if not bars or len(bars) < period + 1:
            return None
        trs: List[float] = []
        prev_close = float(bars[-period - 1].get("close", 0.0) or 0.0)
        if prev_close <= 0:
            return None
        for b in bars[-period:]:
            h = float(b.get("high", 0.0) or 0.0)
            l = float(b.get("low", 0.0) or 0.0)
            c = float(b.get("close", 0.0) or 0.0)
            tr = max(h - l, abs(h - prev_close), abs(l - prev_close))
            trs.append(tr)
            prev_close = c
        return sum(trs) / len(trs) if trs else None

    def _detect_signal(
        self, candles: List[CandleBar]
    ) -> Optional[Tuple[str, str, OrderBlock]]:
        """Look at the LAST completed candle for a dragonfly/gravestone doji
        whose close sits inside a confirmed unmitigated Order Block of the
        opposing direction.

        Returns ``(action, pattern_name, ob)`` or None.
        ``action`` is ``"SHORT"`` or ``"LONG"``.
        """
        if len(candles) < max(self.ob_window + self.atr_period + 5, 20):
            return None

        last_idx = len(candles) - 1
        last = candles[last_idx]

        # detect_patterns inspects ``history[-1]`` (and earlier as needed
        # for multi-bar patterns); every returned event applies to the
        # last bar.  Pass a small slice so detection cost stays bounded.
        events = detect_patterns(candles[-5:])
        if not events:
            return None

        # Order block detection — use full available history for accurate impulse stats.
        try:
            obs = find_order_blocks(
                candles,
                impulse_threshold_atr=self.ob_impulse_threshold_atr,
                window=self.ob_window,
                atr_period=self.atr_period,
            )
        except Exception as exc:
            logger.debug("price_action_fade: OB detection failed: %s", exc)
            return None

        for ev in events:
            if self.allow_short and ev.name == Pattern.DRAGONFLY_DOJI:
                # Bullish-reversal pattern → contrarian SHORT → need BEARISH OB
                # (direction = -1) at the bar's close.
                ob = self._find_containing_ob(obs, last_idx, last.close, direction=-1)
                if ob is not None:
                    return ("SHORT", ev.name, ob)
            if self.allow_long and ev.name == Pattern.GRAVESTONE_DOJI:
                # Bearish-reversal pattern → contrarian LONG → need BULLISH OB
                # (direction = +1) at the bar's close.
                ob = self._find_containing_ob(obs, last_idx, last.close, direction=+1)
                if ob is not None:
                    return ("LONG", ev.name, ob)
        return None

    @staticmethod
    def _find_containing_ob(
        obs: List[OrderBlock], bar_index: int, price: float, direction: int
    ) -> Optional[OrderBlock]:
        """Return the first confirmed, unmitigated, direction-matching OB
        whose [lower, upper] zone contains ``price`` at ``bar_index``."""
        for ob in obs:
            if ob.direction != direction:
                continue
            if ob.confirmation_index > bar_index:
                continue  # not yet observable
            if ob.mitigation_index is not None and ob.mitigation_index < bar_index:
                continue  # already mitigated
            if ob.contains(price):
                return ob
        return None

    # ─────────────────────── analyze ───────────────────────────

    async def analyze(self, symbol: str) -> Optional[Dict[str, Any]]:
        try:
            await self.manage_positions()

            bars = await self.trading_bot.get_historical_data(
                symbol=symbol, timeframe=self.timeframe, limit=self.lookback_bars,
            )
            min_required = max(self.ob_window + self.atr_period + 10, 30)
            if not bars or len(bars) < min_required:
                return None

            last = bars[-1]
            bar_et = self._bar_timestamp_et(last)
            if bar_et is None:
                return None

            # Per-bar dedup.
            prev_ts = self._last_signal_bar_ts.get(symbol)
            if prev_ts is not None and bar_et <= prev_ts:
                return None

            self._bar_seq += 1
            if (self._bar_seq - self._last_signal_seq[symbol]) < self.min_bars_between_signals:
                return None

            # Daily trade counter reset on date roll.
            d = bar_et.date()
            if self._daily_trades_date != d:
                self._daily_trades_date = d
                self.daily_trades = 0

            # RTH filter (optional).
            if self.rth_only:
                t = bar_et.time()
                if not (self.rth_start <= t < self.rth_end):
                    return None

            # Consec-loss breaker.
            breaker = self._evaluate_breaker(symbol, d)
            if breaker.get("blocked"):
                return None

            # Convert bars → candles for the detectors.  We only need the
            # tail (e.g. last 200 bars) for OB / pattern detection; using
            # the full ``lookback_bars`` here is fine — detect_patterns is
            # called on a 5-bar slice and find_order_blocks is O(N×W).
            candles: List[CandleBar] = []
            for b in bars[-self.lookback_bars:]:
                cb = _bar_to_candle(b)
                if cb is not None:
                    candles.append(cb)
            if len(candles) < min_required:
                return None

            detection = self._detect_signal(candles)
            if detection is None:
                return None
            action, pattern_name, ob = detection

            atr = self._atr_from_bars(bars, self.atr_period)
            if atr is None or atr <= 0:
                return None

            tick = self._tick_size(symbol)
            offset = self.entry_offset_ticks * tick
            close = float(last.get("close", 0.0) or 0.0)
            if close <= 0:
                return None

            stop_dist = self.stop_atr_multiplier * atr
            tp_dist = self.tp_r_multiple * stop_dist

            if action == "SHORT":
                entry = close - offset  # slight better-than-market for marketable order
                stop = entry + stop_dist
                tp = entry - tp_dist
            else:  # LONG
                entry = close + offset
                stop = entry - stop_dist
                tp = entry + tp_dist

            self._last_signal_bar_ts[symbol] = bar_et
            self._last_signal_seq[symbol] = self._bar_seq
            self._entry_bar_seq[symbol] = self._bar_seq

            reason = (
                f"{pattern_name} at {('bear' if action == 'SHORT' else 'bull')} OB "
                f"[{ob.lower:.2f},{ob.upper:.2f}] atr={atr:.2f} stop_dist={stop_dist:.2f}"
            )
            logger.info(
                "🎯 price_action_fade %s on %s @ %.2f (%s)",
                action, symbol, entry, reason,
            )
            return {
                "action": action,
                "symbol": symbol,
                "entry_price": round(entry, 4),
                "stop_loss": round(stop, 4),
                "take_profit": round(tp, 4),
                "confidence": 0.6,
                "reason": reason,
            }
        except Exception as exc:
            logger.error("price_action_fade.analyze error for %s: %s", symbol, exc, exc_info=True)
            return None

    async def execute(self, signal: Dict[str, Any]) -> bool:
        try:
            side = "BUY" if signal["action"] == "LONG" else "SELL"
            qty = self._position_size(signal["symbol"])
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
                    "price_action_fade execute rejected %s %s: %s",
                    signal["action"], signal["symbol"], result.get("error"),
                )
                return False
            self.daily_trades += 1
            return True
        except Exception as exc:
            logger.error("price_action_fade.execute error: %s", exc, exc_info=True)
            return False

    async def manage_positions(self) -> None:
        """Time-based exit: close any position held longer than
        ``max_hold_bars``.  Mirrors body_reversion's approach — bracket
        TP / SL still control normal exits; this just bounds the time-risk
        for the time-decay-sensitive doji edge.
        """
        if self.max_hold_bars <= 0:
            return None
        bot = self.trading_bot
        engine = getattr(bot, "backtest_engine", None) or getattr(self, "_replay_engine", None)
        if engine is None:
            return None  # live time-based exit deferred (managed by bracket)

        positions = getattr(engine, "positions", None) or {}
        for sym, pos in list(positions.items()):
            entry_seq = self._entry_bar_seq.get(sym)
            if entry_seq is None:
                continue
            held = self._bar_seq - entry_seq
            if held < self.max_hold_bars:
                continue
            try:
                # Close at market via engine; cleanup pending OCO.
                pending = getattr(engine, "pending_orders", None) or []
                for o in list(pending):
                    osym = getattr(o, "symbol", "")
                    if osym == sym:
                        try:
                            pending.remove(o)
                        except ValueError:
                            pass
                last_px = getattr(pos, "current_price", None) or getattr(pos, "entry_price", None)
                if last_px is None:
                    continue
                qty = getattr(pos, "quantity", 0)
                if qty == 0:
                    continue
                # Use the engine's order placement API (same as body_reversion).
                close_side = "SELL" if qty > 0 else "BUY"
                if hasattr(engine, "place_order"):
                    engine.place_order(
                        symbol=sym, side=close_side, quantity=abs(int(qty)),
                        order_type="MARKET", price=float(last_px),
                    )
                self._entry_bar_seq.pop(sym, None)
            except Exception as exc:
                logger.debug("price_action_fade timeout-close skipped %s: %s", sym, exc)
        return None

    async def cleanup(self) -> None:
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.NAME,
            "timeframe": self.timeframe,
            "atr_period": self.atr_period,
            "stop_atr_multiplier": self.stop_atr_multiplier,
            "tp_r_multiple": self.tp_r_multiple,
            "max_hold_bars": self.max_hold_bars,
            "ob_impulse_threshold_atr": self.ob_impulse_threshold_atr,
            "ob_window": self.ob_window,
            "allow_short": self.allow_short,
            "allow_long": self.allow_long,
            "daily_trades": self.daily_trades,
        }
