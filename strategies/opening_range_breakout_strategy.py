"""Opening Range Breakout — same-day breakout-of-range strategy.

This module is the clean replacement for trying to bend
``OvernightRangeStrategy`` into short same-day windows.  That strategy
hard-codes a 1-minute history fetch with a 10-bar minimum
(``track_overnight_range``) because its alpha window is 14.5 h wide;
applying it to a 15-min ORB silently produces zero trades.

``opening_range_breakout`` is a purpose-built same-day strategy:

* Lifecycle mirrors ``morning_range_reversion`` — iterate bars in
  ``analyze()``, accumulate the (high, low) box while ET clock is in
  ``[range_start, range_end_open]``, signal after.
* Entry mirrors ``overnight_range`` — at the first bar AFTER
  ``range_end_open`` with a valid box, submit stop-bracket entries on
  BOTH sides: a BUY stop just above the box high and a SELL stop just
  below the box low.  Whichever the next move hits fires the bracket;
  the other expires harmlessly when the session flattens.
* Bracket geometry is configurable per-symbol (stop / TP either as
  range-width fraction OR ATR multiplier).
* Single signal per (symbol, session_date) — same as ``overnight_range``.

Use cases this enables:
- 15-min ORB:  range_start=09:30  range_end_open=09:45
- 30-min ORB:  range_start=09:30  range_end_open=10:00
- 60-min ORB:  range_start=09:30  range_end_open=10:30
- 09:00-09:30 European-open box, 14:00-14:30 power-hour box, etc.

Tuning rationale lives in ``config/strategies/opening_range_breakout.toml``
``[meta]`` block.  Walk-forward evidence sits in
``docs/perf/_opt_runs/orb_strategy/``.
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

try:
    import pytz  # type: ignore
except ImportError:  # pragma: no cover
    pytz = None

from core.strategy_config import load_strategy_config
from strategies.strategy_base import BaseStrategy, StrategyConfig

logger = logging.getLogger(__name__)


__all__ = ["OpeningRangeBreakoutStrategy"]


# ───────────────────────── helpers ──────────────────────────


def _load_tz(name: str):
    """``pytz`` if available, else stdlib ``zoneinfo`` (Python 3.9+)."""
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
        logger.warning("opening_range_breakout: invalid HH:MM %r — using %s", s, default)
        return default


def _localize(tz, dt: datetime) -> datetime:
    """Attach tz to a naive ``dt``; pass through if already aware."""
    if dt.tzinfo is not None:
        return dt
    if pytz is not None and hasattr(tz, "localize"):
        return tz.localize(dt)
    return dt.replace(tzinfo=tz)


# ───────────────────── per-symbol session state ──────────────────────


@dataclass
class _SessionState:
    """One per (symbol).  Reset at the top of each new ET session."""

    session_date: Optional[date] = None
    phase: str = "idle"  # "idle" → "build" → "armed" → "signaled" / "done"
    range_hi: Optional[float] = None
    range_lo: Optional[float] = None
    bars_in_build: int = 0
    # Logged-once-per-session flags so the operator sees one INFO per event.
    _logged_new_session: bool = False
    _logged_range_built: bool = False
    _logged_skip_weekday: bool = False
    _logged_skip_width: bool = False
    _logged_breaker: bool = False


# ───────────────────────── strategy class ───────────────────────────


class OpeningRangeBreakoutStrategy(BaseStrategy):
    """Opening Range Breakout — symmetric stop-bracket entries on box break.

    See module docstring for the high-level mechanic.  All knobs live in
    ``config/strategies/opening_range_breakout.toml``; the TOML keys
    here mirror ``morning_range_reversion`` where they share semantics.
    """

    NAME = "opening_range_breakout"
    ANALYZE_SIGNAL_REASON = "Opening range breakout — both-sides stop-bracket"

    def __init__(self, trading_bot, config: Optional[StrategyConfig] = None):
        if config is None:
            config = StrategyConfig.from_env(self.NAME)
        super().__init__(trading_bot, config)

        self._cfg = load_strategy_config(self.NAME, env_prefix=f"{self.NAME.upper()}_")
        self.timeframe: str = self._cfg.get_str("signal.timeframe", "5m") or "5m"
        self.lookback_bars: int = int(self._cfg.get_int("signal.lookback_bars", 400) or 400)
        self.session_zone: str = self._cfg.get_str("signal.session_timezone", "America/New_York") or "America/New_York"
        self._tz = _load_tz(self.session_zone)

        self.range_start: time = _parse_hhmm(self._cfg.get_str("signal.range_start", "09:30"), time(9, 30))
        self.range_end_open: time = _parse_hhmm(self._cfg.get_str("signal.range_end_open", "09:45"), time(9, 45))
        self.flat_before: time = _parse_hhmm(self._cfg.get_str("signal.flat_before", "15:55"), time(15, 55))

        # Range-width guards (applied as POINTS — symbol-aware via overrides).
        self.min_range_width_points: float = float(
            self._cfg.get_float("signal.min_range_width_points", 0.0) or 0.0
        )
        self.max_range_width_points: float = float(
            self._cfg.get_float("signal.max_range_width_points", 0.0) or 0.0
        )

        # Bracket geometry — two modes:
        #   1) range-width fraction: stop = width × stop_range_pct; tp = width × tp_range_pct
        #   2) ATR multiplier (when use_atr_geometry=true).
        self.use_atr_geometry: bool = bool(self._cfg.get_bool("signal.use_atr_geometry", False))
        self.stop_range_pct: float = float(self._cfg.get_float("signal.stop_range_pct", 0.5) or 0.5)
        self.tp_range_pct: float = float(self._cfg.get_float("signal.tp_range_pct", 1.0) or 1.0)
        self.stop_atr_multiplier: float = float(self._cfg.get_float("signal.stop_atr_multiplier", 1.0) or 1.0)
        self.tp_atr_multiplier: float = float(self._cfg.get_float("signal.tp_atr_multiplier", 2.0) or 2.0)
        self.atr_period: int = int(self._cfg.get_int("signal.atr_period", 14) or 14)

        # Stop-entry buffer in price points beyond the box (microstructure noise).
        self.stop_entry_buffer_points: float = float(
            self._cfg.get_float("signal.stop_entry_buffer_points", 0.0) or 0.0
        )

        # ``stop_bracket`` (default): place both-side stop brackets at box ± buffer when armed.
        # ``close_breakout``: wait for a 5m bar **close** beyond the box, then market entry.
        self.entry_mode: str = str(
            self._cfg.get_str("signal.entry_mode", "stop_bracket") or "stop_bracket"
        ).strip().lower()

        self.allow_long: bool = bool(self._cfg.get_bool("signal.allow_long", True))
        self.allow_short: bool = bool(self._cfg.get_bool("signal.allow_short", True))

        self.max_hold_bars: int = int(self._cfg.get_int("signal.max_hold_bars", 0) or 0)

        # Active-window hint for the in-process replay engine — only let
        # analyze() fire during the session window.  Outside this the
        # strategy short-circuits anyway, so skipping the call entirely
        # is a free win.
        try:
            self.replay_active_window_et = [(self.range_start, self.flat_before)]
        except Exception:
            self.replay_active_window_et = None

        # Per-symbol session state.
        self._sessions: Dict[str, _SessionState] = defaultdict(_SessionState)
        # Per (symbol, session_date) marker so each session signals at most once.
        self._signaled: set = set()
        self.daily_trades: int = 0

    # ──────────────────── consec-loss breaker bridge ────────────────────
    # Mirrors overnight_range / body_reversion / MRR — all delegate to the
    # shared helper in ``core/consec_loss_breaker.py``.  Per-symbol
    # overrides supported under ``[symbols.<SYM>.signal]``.

    def _breaker_config_for_symbol(self, symbol: str):
        from core.consec_loss_breaker import BreakerConfig
        sym_upper = str(symbol).upper()

        def _resolve_int(key: str, default: int = 0) -> int:
            v = self._cfg.symbol_override(sym_upper, key, default=None)
            if v is None:
                v = self._cfg.get_int(key, default)
            try:
                return max(0, int(v or 0))
            except (TypeError, ValueError):
                return default

        def _resolve_float(key: str, default: float = 0.0) -> float:
            v = self._cfg.symbol_override(sym_upper, key, default=None)
            if v is None:
                v = self._cfg.get_float(key, default)
            try:
                return max(0.0, float(v or 0.0))
            except (TypeError, ValueError):
                return default

        return BreakerConfig(
            max_losses=_resolve_int("signal.max_consecutive_losses", 0),
            cooldown_sessions=_resolve_int("signal.loss_streak_cooldown_sessions", 0),
            magnitude_dollars=_resolve_float("signal.rolling_pnl_loss_threshold_dollars", 0.0),
        )

    def _evaluate_breaker(self, symbol: str, sess_date: date) -> Dict[str, Any]:
        from core.consec_loss_breaker import evaluate, trade_iter_for_strategy
        cfg = self._breaker_config_for_symbol(symbol)
        if not cfg.enabled:
            return {"blocked": False, "streak": 0, "reason": "ok"}
        trade_iter = trade_iter_for_strategy(self, symbol)
        if trade_iter is None:
            return {"blocked": False, "streak": 0, "reason": "no_engine"}
        return evaluate(
            symbol=symbol, bar_session_date=sess_date,
            trade_iter=trade_iter, config=cfg,
        )

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
            if ts > 1e12:
                dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
            else:
                dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        elif hasattr(ts, "to_pydatetime"):
            try:
                dt = ts.to_pydatetime()
            except Exception:
                return None
        else:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        try:
            return dt.astimezone(self._tz)
        except Exception:
            return None

    def _bars_are_stale(self, symbol: str, bars: List[Dict[str, Any]]) -> bool:
        """Live-mode heuristic: drop if the latest bar is older than 2 bar-intervals.

        Backtest mode (``_is_strategy_replay`` or no real-time clock) skips
        the check — the replay engine controls bar timing.
        """
        if self._is_replay_mode() or not bars:
            return False
        try:
            last_et = self._bar_timestamp_et(bars[-1])
            if last_et is None:
                return False
            now_et = datetime.now(self._tz)
            # 5m bars → tolerate up to 10 minutes lag before declaring stale.
            tf_minutes = 5 if self.timeframe.endswith("m") else 60
            try:
                tf_minutes = int(self.timeframe[:-1])
            except (ValueError, IndexError):
                pass
            return (now_et - last_et) > timedelta(minutes=tf_minutes * 2)
        except Exception:
            return False

    def _skip_weekdays(self, symbol: str) -> set:
        """Per-symbol ``filters.skip_weekdays`` (falls back to root)."""
        try:
            raw = self._cfg.symbol_override(
                symbol.upper(), "filters.skip_weekdays",
                default=self._cfg.get_list("filters.skip_weekdays", []),
                hint=list,
            )
            out: set = set()
            for v in (raw or []):
                if isinstance(v, int):
                    out.add(v)
                elif isinstance(v, str):
                    out.add({"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}.get(
                        v.strip().lower()[:3], -1
                    ))
            return {d for d in out if 0 <= d <= 6}
        except Exception:
            return set()

    def _position_size(self, symbol: str) -> int:
        try:
            raw = self._cfg.symbol_override(
                symbol.upper(), "risk.position_size",
                default=int(self._cfg.get_int("risk.position_size", 1) or 1), hint=int,
            )
            return max(1, int(raw))
        except Exception:
            return max(1, int(self.config.position_size or 1))

    def _width_band(self, symbol: str) -> Tuple[float, float]:
        try:
            lo = float(self._cfg.symbol_override(
                symbol.upper(), "signal.min_range_width_points",
                default=self.min_range_width_points, hint=float,
            ))
            hi = float(self._cfg.symbol_override(
                symbol.upper(), "signal.max_range_width_points",
                default=self.max_range_width_points, hint=float,
            ))
            return lo, hi
        except Exception:
            return self.min_range_width_points, self.max_range_width_points

    def _geom(self, symbol: str) -> Dict[str, float]:
        """Per-symbol geometry knobs (with root fallback)."""
        def _g(key: str, default: float) -> float:
            return float(self._cfg.symbol_override(
                symbol.upper(), f"signal.{key}", default=default, hint=float,
            ))
        return {
            "stop_range_pct": _g("stop_range_pct", self.stop_range_pct),
            "tp_range_pct": _g("tp_range_pct", self.tp_range_pct),
            "stop_atr_multiplier": _g("stop_atr_multiplier", self.stop_atr_multiplier),
            "tp_atr_multiplier": _g("tp_atr_multiplier", self.tp_atr_multiplier),
            "stop_entry_buffer_points": _g("stop_entry_buffer_points", self.stop_entry_buffer_points),
        }

    @staticmethod
    def _atr(bars: List[Dict[str, Any]], period: int) -> Optional[float]:
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

    # ─────────────────────── core analyze() ───────────────────────────

    async def analyze(self, symbol: str) -> Optional[Dict[str, Any]]:
        try:
            await self.manage_positions()

            bars = await self.trading_bot.get_historical_data(
                symbol=symbol, timeframe=self.timeframe, limit=self.lookback_bars,
            )
            if not bars or len(bars) < 5:
                return None
            if self._bars_are_stale(symbol, bars):
                return None

            last = bars[-1]
            bar_et = self._bar_timestamp_et(last)
            if bar_et is None:
                return None
            d = bar_et.date()
            t_close = bar_et.time()

            st = self._sessions[symbol]
            sym_skip = self._skip_weekdays(symbol)

            # ── New ET session: reset.
            if st.session_date != d:
                if st.session_date is not None and not st._logged_new_session:
                    logger.info(
                        "🌅 %-3s ORB new session %s (build %s–%s ET, flat by %s ET)",
                        symbol, d, self.range_start, self.range_end_open, self.flat_before,
                    )
                st.session_date = d
                st.phase = "idle"
                st.range_hi = st.range_lo = None
                st.bars_in_build = 0
                st._logged_new_session = False
                st._logged_range_built = False
                st._logged_skip_weekday = False
                st._logged_skip_width = False
                st._logged_breaker = False
                self.daily_trades = 0

            # ── Weekday skip-gate.
            if d.weekday() in sym_skip:
                if not st._logged_skip_weekday:
                    logger.info(
                        "🚫 %-3s ORB session skipped %s (weekday %s in skip set)",
                        symbol, d, d.strftime("%a"),
                    )
                    st._logged_skip_weekday = True
                st.phase = "done"
                return None

            # ── Consec-loss breaker.
            breaker = self._evaluate_breaker(symbol, d)
            if breaker.get("blocked"):
                if not st._logged_breaker:
                    logger.info(
                        "🚨 %-3s ORB consec-loss breaker active — streak=%d, cooldown=%d",
                        symbol, breaker.get("streak", 0), breaker.get("cooldown", 0),
                    )
                    st._logged_breaker = True
                return None

            # ── Range build window.  Iterate bars in current session, accumulate H/L
            # for any whose ET close-time is in [range_start, range_end_open).
            # We re-scan from scratch each call (cheap — bars list is small) so the
            # state is robust against missed bars / executor mid-session restarts.
            if st.phase != "signaled" and st.phase != "done":
                hi = lo = None
                count = 0
                for b in bars:
                    bt = self._bar_timestamp_et(b)
                    if bt is None or bt.date() != d:
                        continue
                    tc = bt.time()
                    if not (self.range_start <= tc < self.range_end_open):
                        continue
                    h = float(b.get("high", 0.0) or 0.0)
                    l = float(b.get("low", 0.0) or 0.0)
                    if h <= 0 or l <= 0:
                        continue
                    hi = h if hi is None else max(hi, h)
                    lo = l if lo is None else min(lo, l)
                    count += 1
                if hi is not None and lo is not None and count > 0:
                    st.range_hi = hi
                    st.range_lo = lo
                    st.bars_in_build = count
                    if t_close < self.range_end_open:
                        st.phase = "build"
                    else:
                        st.phase = "armed"
                        if not st._logged_range_built:
                            logger.info(
                                "📐 %-3s ORB range built  H=%.2f  L=%.2f  W=%.2f  (n=%d bars)",
                                symbol, hi, lo, hi - lo, count,
                            )
                            st._logged_range_built = True
                    # Mirror the live ORB box to ``strategy_states.settings.orb_ranges``
                    # so the dashboard chart can render the shaded overlay even when
                    # the strategy runs in a separate executor process.
                    self._persist_orb_ranges_to_db()

            # ── Past flat_before? cooled session.
            if t_close >= self.flat_before:
                st.phase = "done"
                return None

            # ── Need to be ARMED to signal (stop-bracket mode fires once at box close).
            if st.phase != "armed":
                return None
            if (symbol.upper(), d) in self._signaled:
                return None
            if st.range_hi is None or st.range_lo is None:
                return None

            width = float(st.range_hi - st.range_lo)
            lo_band, hi_band = self._width_band(symbol)
            if width <= 0:
                return None
            if lo_band > 0 and width < lo_band:
                if not st._logged_skip_width:
                    logger.info("⏭️  %-3s ORB skip — width %.2f < min %.2f", symbol, width, lo_band)
                    st._logged_skip_width = True
                st.phase = "done"
                return None
            if hi_band > 0 and width > hi_band:
                if not st._logged_skip_width:
                    logger.info("⏭️  %-3s ORB skip — width %.2f > max %.2f", symbol, width, hi_band)
                    st._logged_skip_width = True
                st.phase = "done"
                return None

            g = self._geom(symbol)
            buf = g["stop_entry_buffer_points"]
            bar_close = float(last.get("close", 0.0) or 0.0)

            # Close-breakout mode: stay armed until a bar closes beyond the box.
            if self.entry_mode == "close_breakout":
                if t_close < self.range_end_open:
                    return None
                long_order = None
                short_order = None
                if self.allow_long and bar_close > st.range_hi + buf:
                    entry = bar_close
                    if self.use_atr_geometry:
                        atr = self._atr(bars, self.atr_period)
                        if atr is None or atr <= 0:
                            return None
                        stop_dist = atr * g["stop_atr_multiplier"]
                        tp_dist = atr * g["tp_atr_multiplier"]
                    else:
                        stop_dist = width * g["stop_range_pct"]
                        tp_dist = width * g["tp_range_pct"]
                    long_order = {
                        "side": "BUY",
                        "entry_price": entry,
                        "stop_loss": entry - stop_dist,
                        "take_profit": entry + tp_dist,
                        "market_entry": True,
                    }
                elif self.allow_short and bar_close < st.range_lo - buf:
                    entry = bar_close
                    if self.use_atr_geometry:
                        atr = self._atr(bars, self.atr_period)
                        if atr is None or atr <= 0:
                            return None
                        stop_dist = atr * g["stop_atr_multiplier"]
                        tp_dist = atr * g["tp_atr_multiplier"]
                    else:
                        stop_dist = width * g["stop_range_pct"]
                        tp_dist = width * g["tp_range_pct"]
                    short_order = {
                        "side": "SELL",
                        "entry_price": entry,
                        "stop_loss": entry + stop_dist,
                        "take_profit": entry - tp_dist,
                        "market_entry": True,
                    }
                if not long_order and not short_order:
                    return None
                return {
                    "symbol": symbol,
                    "long_order": long_order,
                    "short_order": short_order,
                    "range_hi": st.range_hi,
                    "range_lo": st.range_lo,
                    "width": width,
                    "session_date": d,
                    "entry_mode": "close_breakout",
                    "confidence": 0.7,
                    "reason": "Opening range close breakout",
                }

            # ── Bracket geometry (default stop-bracket entry at box ± buffer).
            long_entry = st.range_hi + buf
            short_entry = st.range_lo - buf
            if self.use_atr_geometry:
                atr = self._atr(bars, self.atr_period)
                if atr is None or atr <= 0:
                    return None
                stop_dist = atr * g["stop_atr_multiplier"]
                tp_dist = atr * g["tp_atr_multiplier"]
            else:
                stop_dist = width * g["stop_range_pct"]
                tp_dist = width * g["tp_range_pct"]
            long_stop = long_entry - stop_dist
            long_tp = long_entry + tp_dist
            short_stop = short_entry + stop_dist
            short_tp = short_entry - tp_dist

            long_order = None
            short_order = None
            if self.allow_long:
                long_order = {
                    "side": "BUY", "entry_price": long_entry,
                    "stop_loss": long_stop, "take_profit": long_tp,
                }
            if self.allow_short:
                short_order = {
                    "side": "SELL", "entry_price": short_entry,
                    "stop_loss": short_stop, "take_profit": short_tp,
                }
            if not long_order and not short_order:
                return None

            return {
                "symbol": symbol,
                "long_order": long_order,
                "short_order": short_order,
                "range_hi": st.range_hi,
                "range_lo": st.range_lo,
                "width": width,
                "session_date": d,
                "confidence": 0.7,
                "reason": self.ANALYZE_SIGNAL_REASON,
            }
        except Exception as exc:
            logger.error("opening_range_breakout.analyze error for %s: %s", symbol, exc, exc_info=True)
            return None

    async def execute(self, signal: Dict[str, Any]) -> bool:
        try:
            symbol = signal["symbol"]
            qty = self._position_size(symbol)
            placed_any = False
            for order in (signal.get("long_order"), signal.get("short_order")):
                if order is None:
                    continue
                side = order["side"]
                entry = float(order["entry_price"])
                sl = float(order["stop_loss"])
                tp = float(order["take_profit"])
                result = await self.place_bracket_order(
                    symbol=symbol, side=side, quantity=qty,
                    entry_price=entry, stop_loss_price=sl, take_profit_price=tp,
                    enable_breakeven=False,
                    market_entry=bool(order.get("market_entry", False)),
                )
                if result and result.get("error"):
                    logger.warning(
                        "opening_range_breakout execute rejected %s %s: %s",
                        side, symbol, result.get("error"),
                    )
                else:
                    placed_any = True
            if placed_any:
                d = signal.get("session_date") or datetime.now(self._tz).date()
                self._signaled.add((symbol.upper(), d))
                st = self._sessions[symbol]
                st.phase = "signaled"
                self.daily_trades += 1
            return placed_any
        except Exception as exc:
            logger.error("opening_range_breakout.execute error: %s", exc, exc_info=True)
            return False

    async def manage_positions(self) -> None:
        """No-op for now — bracket OCO + flat_before handled by executor."""
        return None

    async def cleanup(self) -> None:
        """Strategy-level cleanup.  Sessions / signaled markers retained for diag."""
        return None

    def _persist_orb_ranges_to_db(self) -> None:
        """Snapshot today's ORB ranges into ``strategy_states.settings.orb_ranges`` so
        the dashboard chart server can render the shaded box even when this strategy
        runs in a separate executor process. Mirrors
        ``OvernightRangeStrategy._persist_or_ranges_to_db`` via
        ``BaseStrategy.persist_range_snapshot``.
        """
        snap: Dict[str, Dict[str, Any]] = {}
        for sym, st in self._sessions.items():
            try:
                hi = getattr(st, "range_hi", None)
                lo = getattr(st, "range_lo", None)
                if hi is None or lo is None:
                    continue
                hi_f = float(hi)
                lo_f = float(lo)
            except (TypeError, ValueError):
                continue
            entry: Dict[str, Any] = {
                "high": hi_f,
                "low": lo_f,
                "mid": (hi_f + lo_f) / 2.0,
                "width": hi_f - lo_f,
                "phase": getattr(st, "phase", None),
                "bars_in_build": int(getattr(st, "bars_in_build", 0) or 0),
            }
            sd = getattr(st, "session_date", None)
            if sd is not None and hasattr(sd, "isoformat"):
                entry["session_date"] = sd.isoformat()
                try:
                    from datetime import timedelta as _td
                    start_et = datetime.combine(sd, self.range_start, tzinfo=self._tz)
                    end_open = datetime.combine(sd, self.range_end_open, tzinfo=self._tz)
                    entry["session_start_et"] = start_et.isoformat()
                    entry["session_end_et"] = (end_open - _td(microseconds=1)).isoformat()
                except Exception:
                    pass
            key = str(sym).upper()
            snap[key] = entry
            if "." in key:
                short = key.split(".")[-1].strip()
                if short and short != key:
                    snap[short] = entry
        self.persist_range_snapshot(
            snap, key="orb_ranges",
            attr="_orb_ranges_db_last_mono",
        )

    def to_dict(self) -> Dict[str, Any]:
        """Diagnostic snapshot — used by ``master`` CLI and dashboard."""
        return {
            "name": self.NAME,
            "timeframe": self.timeframe,
            "range_start": str(self.range_start),
            "range_end_open": str(self.range_end_open),
            "flat_before": str(self.flat_before),
            "use_atr_geometry": self.use_atr_geometry,
            "sessions": {
                sym: {
                    "session_date": str(st.session_date) if st.session_date else None,
                    "phase": st.phase,
                    "range_hi": st.range_hi, "range_lo": st.range_lo,
                    "bars_in_build": st.bars_in_build,
                }
                for sym, st in self._sessions.items()
            },
            "signaled_sessions": [{"symbol": s, "date": str(d)} for s, d in sorted(self._signaled)],
        }
