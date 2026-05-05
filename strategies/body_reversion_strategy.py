"""Body-reversion (research-grade, v3).

**Hypothesis** (cross-instrument: MNQ / MES / MGC, 5m bars):
A bar with body (`|close - open|`) consuming **> body_pct_min** of the bar
range mean-reverts over the next 15-30 minutes. See discovery reports:

    docs/alpha/deep_scan_INDEX.md
    docs/alpha/deep_scan_MNQ.md
    docs/alpha/deep_scan_MES.md
    docs/alpha/deep_scan_MGC.md

**Effect-size summary (forward-return diff in points, h=1 = 5m forward):**

| body_pct_min | direction | MNQ diff | MES diff | MGC diff |
|--------------|-----------|----------|----------|----------|
| `> 0.70`     | bear → LONG  | +5.28 | +1.48 | +1.16 |
| `> 0.85`     | bear → LONG  | +14.7 | +4.5  | +2.4 |
| `> 0.90`     | bear → LONG  | **+26.6** | **+6.9** | +2.6 |
| `> 0.90`     | bull → SHORT | **-20.1** | -5.4 | **-3.0** |

**v3 — co-trigger amplification.** The combo audit
(``docs/alpha/body_reversion_combos.md``) showed that filtering the anchor
to bars that **also** clear two regime conditions multiplies realised R
across every (symbol × direction) cell:

- **`atr_high_q4`**: current 14-bar ATR is in the **top quartile** of the
  trailing ATR distribution (default 500 bars ≈ 3 trading days on 5m).
- **`range_expand_1.5x`**: current bar range > 1.5 × the trailing 20-bar
  range MA.

Both default ON. Signal counts drop ~85 % vs v2 anchor-only, but mean R
roughly **4× higher**, PF roughly **3-5× higher**:

| symbol  | direction      | n     | mean_R_SO | PF_SO | delta_R |
|---------|----------------|-------|-----------|-------|---------|
| MNQ     | bear → LONG    |   557 |  +1.977   |  6.15 | +1.47   |
| MES     | bear → LONG    |   699 |  +2.732   | 10.56 | +2.38   |
| MGC     | bear → LONG    |   526 |  +1.958   |  6.52 | +1.53   |
| MNQ     | bull → SHORT   |   501 |  +0.772   |  2.99 | +0.56   |
| MES     | bull → SHORT   |   604 |  +1.656   |  6.65 | +1.53   |
| MGC     | bull → SHORT   |   512 |  +2.346   |  7.75 | +1.89   |

The v2 SHORT-leg loss problem (overlay during sustained rallies) becomes
manageable in v3 because the high-ATR + expansion gate isolates *event*
bars where reversion dominates trend continuation. Empirically v3 turns
MES SHORT from a bleed into a winner.

**Workflow per bar (default 5m):**

1. Pull at least ``signal.lookback_bars`` recent bars at ``signal.timeframe``.
2. Compute ``body_pct = |close - open| / (high - low)`` for the **last
   completed** bar.
3. If ``body_pct > body_pct_min`` AND ``require_high_atr`` is satisfied
   (current ATR ≥ rolling 75th percentile) AND ``require_range_expand``
   is satisfied (range > 1.5 × range_ma20), place a stop-bracket entry
   **one tick** past close on the *opposite* side
   (`bull → SHORT`, `bear → LONG`).
   - SL: ``stop_atr_multiplier × ATR(period)`` away on the wrong side.
   - TP: ``tp_r_multiple × stop_distance`` (defaults to a wide 3.0R so we
     do not truncate the mean-reversion run-out; the practical exit is the
     time-based unwind in :meth:`manage_positions` after
     ``max_hold_bars`` 5 m bars).
4. Skip when ``rth_only`` is set and we are outside the configured RTH window.
5. ``min_bars_between_signals`` enforces a per-symbol cooldown.

The strategy is **research-grade** — `meta.enabled = false` by default.
"""

from __future__ import annotations

import logging
from datetime import datetime, time, timedelta, timezone
from typing import Any, Dict, List, Optional, Set

from core.risk_management import _order_counts_as_working_entry_for_risk
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


class BodyReversionStrategy(BaseStrategy):
    """5m big-body reversion with ATR-based stops."""

    NAME = "body_reversion"

    def __init__(self, trading_bot, config: Optional[StrategyConfig] = None):
        if config is None:
            config = StrategyConfig.from_env(self.NAME)
        super().__init__(trading_bot, config)

        self._cfg = load_strategy_config(self.NAME, env_prefix=f"{self.NAME.upper()}_")

        self.timeframe: str = self._cfg.get_str("signal.timeframe", "5m") or "5m"
        # v3: bumped default from 100 to 500 so the rolling ATR-percentile
        # has enough samples (~3 trading days at 5m) to be stable.
        self.lookback_bars: int = int(self._cfg.get_int("signal.lookback_bars", 500))
        self.body_pct_min: float = float(self._cfg.get_float("signal.body_pct_min", 0.90))
        self.allow_long: bool = bool(self._cfg.get_bool("signal.allow_long", True))
        # v3: bracket-grid showed SHORT becomes profitable when both regime
        # gates fire (delta_R +0.56 to +1.89 cross-symbol); flip default ON.
        self.allow_short: bool = bool(self._cfg.get_bool("signal.allow_short", True))
        self.atr_period: int = int(self._cfg.get_int("signal.atr_period", 14))
        self.stop_atr_multiplier: float = float(
            self._cfg.get_float("signal.stop_atr_multiplier", 0.5)
        )
        self.tp_r_multiple: float = float(self._cfg.get_float("signal.tp_r_multiple", 3.0))
        self.entry_offset_ticks: float = float(
            self._cfg.get_float("signal.entry_offset_ticks", 1.0)
        )
        self.min_bars_between_signals: int = int(
            self._cfg.get_int("signal.min_bars_between_signals", 6)
        )
        self.max_hold_bars: int = int(self._cfg.get_int("signal.max_hold_bars", 6))

        # v3 co-trigger gates — see docs/alpha/body_reversion_combos.md.
        #
        # Important: these can be overridden per symbol via:
        #   [symbols.MES.signal]
        #   require_range_expand = true
        #
        # We keep global defaults here and resolve per-symbol values at signal
        # time using `StrategyConfig.symbol_override(...)`.
        self.require_high_atr_default: bool = bool(self._cfg.get_bool("signal.require_high_atr", True))
        self.atr_regime_lookback_default: int = int(self._cfg.get_int("signal.atr_regime_lookback", 500))
        self.atr_regime_quantile_default: float = float(self._cfg.get_float("signal.atr_regime_quantile", 0.75))

        self.require_range_expand_default: bool = bool(
            self._cfg.get_bool("signal.require_range_expand", True)
        )
        self.range_ma_period_default: int = int(self._cfg.get_int("signal.range_ma_period", 20))
        self.range_expand_mult_default: float = float(self._cfg.get_float("signal.range_expand_mult", 1.5))

        # Instance-level force overrides (used by tests / experiments). When
        # set, these take precedence over TOML and per-symbol overrides.
        self._force_require_high_atr: Optional[bool] = None
        self._force_require_range_expand: Optional[bool] = None

        self.rth_only: bool = bool(self._cfg.get_bool("signal.rth_only", False))
        self.rth_start: time = _parse_hhmm(
            self._cfg.get_str("signal.rth_start", "09:35"), time(9, 35)
        )
        self.rth_end: time = _parse_hhmm(
            self._cfg.get_str("signal.rth_end", "15:30"), time(15, 30)
        )
        self.skip_open_minutes: int = int(
            self._cfg.get_int("signal.skip_open_minutes", 30)
        )
        self.session_zone: str = self._cfg.get_str("signal.session_timezone", "US/Eastern")
        self._tz = self._load_tz(self.session_zone)

        self.tick_sizes: Dict[str, float] = {
            "MNQ": 0.25, "NQ": 0.25,
            "MES": 0.25, "ES": 0.25,
            "MGC": 0.10, "GC": 0.10,
            "MYM": 1.0, "YM": 1.0,
            "M2K": 0.10, "RTY": 0.10,
        }

        self._last_signal_bar: Dict[str, datetime] = {}
        self._last_signal_bar_count: Dict[str, int] = {}
        self._bar_seq: int = 0
        self._entry_bar_seq: Dict[str, int] = {}
        # Live-only: wall-clock max-hold (strategy loop is ~60s, not per bar).
        self._live_hold_start_utc: Dict[str, datetime] = {}
        self._live_managed_symbols: Set[str] = set()

        logger.info(
            "✅ body_reversion init: tf=%s body_pct_min=%.2f stop_atr=%.2f tp_r=%.2f hold=%d rth=%s "
            "high_atr=%s range_exp=%s long=%s short=%s",
            self.timeframe,
            self.body_pct_min,
            self.stop_atr_multiplier,
            self.tp_r_multiple,
            self.max_hold_bars,
            "on" if self.rth_only else "off",
            "on" if self.require_high_atr_default else "off",
            "on" if self.require_range_expand_default else "off",
            "on" if self.allow_long else "off",
            "on" if self.allow_short else "off",
        )

    # ------------------------------------------------------------------ v3.1 compat shims
    #
    # Earlier v3 tests and scripts accessed `require_high_atr` /
    # `require_range_expand` as direct attributes. We now support per-symbol
    # overrides, so the real value is resolved at signal time. Keep these as
    # properties that map to the global defaults so existing code can still
    # toggle the behavior for the whole strategy instance (used heavily in
    # smoke tests).

    @property
    def require_high_atr(self) -> bool:
        return bool(self.require_high_atr_default)

    @require_high_atr.setter
    def require_high_atr(self, value: bool) -> None:
        self._force_require_high_atr = bool(value)
        self.require_high_atr_default = bool(value)

    @property
    def require_range_expand(self) -> bool:
        return bool(self.require_range_expand_default)

    @require_range_expand.setter
    def require_range_expand(self, value: bool) -> None:
        self._force_require_range_expand = bool(value)
        self.require_range_expand_default = bool(value)

    @property
    def atr_regime_quantile(self) -> float:
        return float(self.atr_regime_quantile_default)

    @property
    def atr_regime_lookback(self) -> int:
        return int(self.atr_regime_lookback_default)

    @property
    def range_expand_mult(self) -> float:
        return float(self.range_expand_mult_default)

    @property
    def range_ma_period(self) -> int:
        return int(self.range_ma_period_default)

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

    def _in_rth_window(self, ts_eastern: datetime) -> bool:
        if not self.rth_only:
            return True
        t = ts_eastern.time()
        if t < self.rth_start or t > self.rth_end:
            return False
        if self.skip_open_minutes > 0:
            open_cutoff = time(
                hour=self.rth_start.hour,
                minute=min(59, self.rth_start.minute + self.skip_open_minutes),
            )
            if t < open_cutoff:
                return False
        return True

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

    def _hold_bar_minutes(self) -> int:
        """Minutes per bar for ``timeframe`` (used for live max-hold duration)."""
        tf = (self.timeframe or "5m").strip().lower()
        try:
            if tf.endswith("m"):
                return max(1, int(tf[:-1] or "5"))
            if tf.endswith("h"):
                return max(1, int(tf[:-1] or "1") * 60)
        except ValueError:
            pass
        return 5

    @staticmethod
    def _order_symbol_upper(order: Dict[str, Any]) -> str:
        order_symbol = (order.get("symbol") or "").upper()
        if not order_symbol:
            symbol_id = order.get("symbolId", "")
            if symbol_id:
                parts = str(symbol_id).split(".")
                order_symbol = parts[-1].upper() if parts else ""
        return order_symbol

    def _live_tag_is_body_reversion_entry(self, tag: str) -> bool:
        t = str(tag).lower()
        if "body_reversion" not in t and "body-reversion" not in t:
            return False
        if "-sl" in t or "-tp" in t:
            return False
        return (
            "stop_entry" in t
            or "stop_bracket" in t
            or "stop-entry" in t
            or "stop-bracket" in t
        )

    def _live_order_is_pending_entry(self, order: Dict[str, Any], sym: str) -> bool:
        if not _order_counts_as_working_entry_for_risk(order):
            return False
        if self._order_symbol_upper(order) != sym.upper():
            return False
        tag = order.get("customTag") or order.get("custom_tag") or ""
        return self._live_tag_is_body_reversion_entry(tag)

    def _live_orders_include_entry(self, sym: str, orders: List[Dict[str, Any]]) -> bool:
        for o in orders:
            if self._live_order_is_pending_entry(o, sym):
                return True
        return False

    async def _has_open_position_or_pending_entry_async(self, symbol: str) -> bool:
        """Replay uses engine + optional ``bot.active_positions``; live uses REST."""
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
            logger.debug("body_reversion live lockout check failed: %s", exc)
            return False
        return False

    def _has_open_position_or_pending_entry(self, symbol: str) -> bool:
        """Return True if we already have an open position **or** any pending
        entry stop order for this symbol — in either live or replay mode.

        Live: read ``trading_bot.active_positions`` (TopStepX adapter populates
        it). Replay: read the BacktestEngine's positions / pending_orders via
        ``trading_bot.backtest_engine`` set on the strategy by
        :class:`StrategyReplayEngine`."""
        bot = self.trading_bot

        positions = getattr(bot, "active_positions", None)
        if positions:
            for p in positions if isinstance(positions, list) else positions.values():
                psym = (p.get("symbol") if isinstance(p, dict) else getattr(p, "symbol", "")) or ""
                if psym.upper() == symbol.upper():
                    return True

        engine = getattr(bot, "backtest_engine", None) or getattr(self, "_replay_engine", None)
        if engine is None and hasattr(bot, "backtest_engine"):
            engine = bot.backtest_engine
        if engine is not None:
            poss = getattr(engine, "positions", None)
            if poss and symbol in poss:
                return True
            pend = getattr(engine, "pending_orders", None) or []
            for o in pend:
                osym = getattr(o, "symbol", "") or ""
                if osym.upper() != symbol.upper():
                    continue
                # Treat any stop-entry (no oco_group) as "entry pending"
                if getattr(o, "oco_group", None):
                    continue
                ot = getattr(o, "order_type", None)
                if ot is not None and getattr(ot, "name", str(ot)).upper() in ("STOP", "LIMIT"):
                    return True
        return False

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

    def _atr_series(self, bars: List[Dict[str, Any]]) -> List[float]:
        """Wilder-style ATR series (simple MA of TR over ``self.atr_period``).

        Returned list has the same length as ``bars``; entries before the
        warm-up period are ``NaN`` (`float("nan")`)."""
        period = self.atr_period
        out: List[float] = [float("nan")] * len(bars)
        if len(bars) < period + 1:
            return out
        trs: List[float] = []
        prev_close = self._val(bars[0], "close", "c")
        for i in range(1, len(bars)):
            hi = self._val(bars[i], "high", "h")
            lo = self._val(bars[i], "low", "l")
            cl = self._val(bars[i], "close", "c")
            tr = max(hi - lo, abs(hi - prev_close), abs(lo - prev_close))
            trs.append(tr)
            prev_close = cl
            if len(trs) >= period:
                out[i] = sum(trs[-period:]) / period
        return out

    def _check_regime_gates(self, symbol: str, bars: List[Dict[str, Any]]) -> Optional[str]:
        """Return ``None`` if all required regime gates pass, else a string
        describing the first failed gate (for debug logging)."""
        sym = symbol.upper()
        if self._force_require_high_atr is not None:
            require_high_atr = bool(self._force_require_high_atr)
        else:
            require_high_atr = bool(
                self._cfg.symbol_override(
                    sym, "signal.require_high_atr", self.require_high_atr_default, hint=bool
                )
            )
        if self._force_require_range_expand is not None:
            require_range_expand = bool(self._force_require_range_expand)
        else:
            require_range_expand = bool(
                self._cfg.symbol_override(
                    sym, "signal.require_range_expand", self.require_range_expand_default, hint=bool
                )
            )
        if not require_high_atr and not require_range_expand:
            return None

        last = bars[-1]
        rng = self._val(last, "high", "h") - self._val(last, "low", "l")

        range_ma_period = int(
            self._cfg.symbol_override(sym, "signal.range_ma_period", self.range_ma_period_default, hint=int)
        )
        range_expand_mult = float(
            self._cfg.symbol_override(sym, "signal.range_expand_mult", self.range_expand_mult_default, hint=float)
        )

        if require_range_expand:
            tail = bars[-(range_ma_period + 1) : -1]
            if len(tail) < range_ma_period:
                return f"range_expand: need {range_ma_period} prior bars, have {len(tail)}"
            ranges = [self._val(b, "high", "h") - self._val(b, "low", "l") for b in tail]
            range_ma = sum(ranges) / len(ranges) if ranges else 0.0
            if range_ma <= 0 or rng <= range_expand_mult * range_ma:
                return (
                    f"range_expand fail: rng={rng:.4f} "
                    f"<= {range_expand_mult:.2f} × ma{range_ma_period}={range_ma:.4f}"
                )

        atr_regime_lookback = int(
            self._cfg.symbol_override(sym, "signal.atr_regime_lookback", self.atr_regime_lookback_default, hint=int)
        )
        atr_regime_quantile = float(
            self._cfg.symbol_override(sym, "signal.atr_regime_quantile", self.atr_regime_quantile_default, hint=float)
        )

        if require_high_atr:
            atrs = self._atr_series(bars)
            window = atrs[-atr_regime_lookback:]
            finite = [a for a in window if a == a]  # NaN-safe
            current_atr = atrs[-1] if atrs else float("nan")
            # 50 samples is the minimum for a reasonably stable Q75 estimate;
            # going much higher than that just delays warm-up without
            # improving the percentile in any meaningful way.
            if not (current_atr == current_atr) or len(finite) < 50:
                return (
                    f"high_atr: not enough finite ATR samples "
                    f"({len(finite)} / lookback={self.atr_regime_lookback})"
                )
            sorted_finite = sorted(finite)
            cut_idx = int(len(sorted_finite) * atr_regime_quantile)
            if cut_idx >= len(sorted_finite):
                cut_idx = len(sorted_finite) - 1
            quantile_threshold = sorted_finite[cut_idx]
            if current_atr <= quantile_threshold:
                return (
                    f"high_atr fail: atr={current_atr:.4f} "
                    f"<= q{int(atr_regime_quantile * 100)}={quantile_threshold:.4f} "
                    f"(over {len(finite)} samples)"
                )
        return None

    async def analyze(self, symbol: str) -> Optional[Dict[str, Any]]:
        try:
            now_et = self._now_eastern()
            # Always run the time-based exit check first so it has a chance to
            # fire even when we are outside the RTH window (positions can be
            # opened in ETH and need to close at hold-bars cutoff).
            await self.manage_positions()
            if not self._in_rth_window(now_et):
                return None

            # Lock-out: do not emit a new signal while we already have an open
            # position **or** a pending entry stop order for this symbol. In
            # backtest replay, stacking buy-stop entries lets multiple OCO
            # brackets compete; if one bracket's stop-loss fires after another
            # bracket's stop-loss has already taken position quantity to zero,
            # the residual orphan stop-loss order opens a phantom **SHORT**
            # against intent (no allow_short check there because it is an exit
            # in disguise). Easiest reliable fix is to never stack.
            if await self._has_open_position_or_pending_entry_async(symbol):
                return None

            bars = await self.trading_bot.get_historical_data(
                symbol=symbol,
                timeframe=self.timeframe,
                limit=self.lookback_bars,
            )
            # Keep warmup minimums conservative because regime gates may be
            # enabled/disabled per-symbol via `[symbols.<SYM>]` overrides.
            min_required = max(
                self.atr_period + 51,  # enough for ≥50 finite ATR samples
                self.range_ma_period_default + 5,
                30,
            )
            if not bars or len(bars) < min_required:
                return None

            last = bars[-1]
            bar_et = self._bar_timestamp_eastern(last)
            if bar_et is None:
                return None
            self._bar_seq += 1
            prev_bar_et = self._last_signal_bar.get(symbol)
            if prev_bar_et is not None and bar_et <= prev_bar_et:
                return None
            last_count = self._last_signal_bar_count.get(symbol)
            if last_count is not None and (self._bar_seq - last_count) < self.min_bars_between_signals:
                return None

            o = self._val(last, "open", "o")
            h = self._val(last, "high", "h")
            l = self._val(last, "low", "l")
            c = self._val(last, "close", "c")
            rng = h - l
            if rng <= 0:
                return None
            body = abs(c - o) / rng
            if body < self.body_pct_min:
                return None

            # v3 regime gates: require the bar to also be in the high-ATR
            # quartile and an expanded-range bar (see combo audit).
            gate_fail = self._check_regime_gates(symbol, bars)
            if gate_fail is not None:
                logger.debug("body_reversion %s regime gate skip: %s", symbol, gate_fail)
                return None

            atr = await self._calculate_atr(symbol)
            if atr <= 0:
                return None
            stop_dist = self.stop_atr_multiplier * atr
            tick = self._tick_size(symbol)
            offset = self.entry_offset_ticks * tick

            if c > o and self.allow_short:
                action = "SHORT"
                entry = c - offset
                stop = entry + stop_dist
                tp = entry - self.tp_r_multiple * stop_dist
            elif c < o and self.allow_long:
                action = "LONG"
                entry = c + offset
                stop = entry - stop_dist
                tp = entry + self.tp_r_multiple * stop_dist
            else:
                return None

            self._last_signal_bar[symbol] = bar_et
            self._last_signal_bar_count[symbol] = self._bar_seq
            self._entry_bar_seq[symbol] = self._bar_seq

            reason = (
                f"body={body:.2f} bar={'bull' if c > o else 'bear'} "
                f"atr={atr:.2f} stop_dist={stop_dist:.2f}"
            )
            logger.info(
                "🎯 body_reversion %s on %s @ %.2f (%s)",
                action,
                symbol,
                entry,
                reason,
            )
            return {
                "action": action,
                "symbol": symbol,
                "entry_price": round(entry, 4),
                "stop_loss": round(stop, 4),
                "take_profit": round(tp, 4),
                "confidence": min(1.0, (body - self.body_pct_min) / max(1e-6, 1.0 - self.body_pct_min)),
                "reason": reason,
            }
        except Exception as exc:
            logger.error("body_reversion.analyze error for %s: %s", symbol, exc, exc_info=True)
            return None

    async def _calculate_atr(self, symbol: str) -> float:
        # Compute ATR locally from time-filtered bars at our timeframe.
        # We deliberately skip ``bot.calculate_atr`` here: in replay mocks it
        # often returns a non-time-filtered value (e.g. ATR of the last 14 bars
        # of the entire input CSV instead of the last 14 bars *up to the
        # current replay time*), which inflates the stop distance by 10-50×.
        bot = self.trading_bot
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
                    "body_reversion execute rejected %s %s: %s",
                    signal["action"],
                    signal["symbol"],
                    result.get("error"),
                )
                return False
            if not getattr(self.trading_bot, "_is_strategy_replay", False):
                self._live_managed_symbols.add(str(signal["symbol"]).upper())
            self.daily_trades += 1
            return True
        except Exception as exc:
            logger.error("body_reversion.execute error: %s", exc, exc_info=True)
            return False

    async def manage_positions(self) -> None:
        """Time-based exit: close any position that has been held longer than
        ``max_hold_bars`` 5 m bars at our timeframe.

        Discovery showed that big-body 5 m bars mean-revert sharply within 3-6
        bars; a wide 3 R bracket TP rarely fires before the move completes,
        leaving the position to drift back to break-even. A time-based unwind
        at the close of the Nth bar after entry captures the alpha.

        In replay we close via the BacktestEngine; in live we call
        ``TopStepXTradingBot.close_position`` after wall-clock
        ``max_hold_bars × bar_duration`` (the executor loop is ~60s, not
        one call per 5m bar)."""
        if self.max_hold_bars <= 0:
            return None

        bot = self.trading_bot
        engine = getattr(bot, "backtest_engine", None) or getattr(self, "_replay_engine", None)
        if engine is None:
            if getattr(bot, "_is_strategy_replay", False):
                return None
            await self._manage_positions_live()
            return None

        positions = getattr(engine, "positions", None) or {}
        # Cancel orphan OCO exit orders for any symbol that no longer has a
        # position. This is the engine bug we hit empirically: when one
        # bracket of a stacked LONG closes the LONG to flat, the *other*
        # bracket's stop_loss / take_profit orders remain in pending_orders.
        # If those orphans later fire, the engine has no position and opens
        # a phantom SHORT (because filled_order.side=SELL on a SL exit).
        pending_orders = getattr(engine, "pending_orders", None)
        if pending_orders is not None and positions is not None:
            for o in list(pending_orders):
                if not getattr(o, "oco_group", None):
                    continue
                osym = getattr(o, "symbol", "")
                if osym in positions:
                    continue
                try:
                    pending_orders.remove(o)
                except ValueError:
                    pass

        for sym, pos in list(positions.items()):
            entry_seq = self._entry_bar_seq.get(sym)
            if entry_seq is None:
                continue
            held = self._bar_seq - entry_seq
            if held < self.max_hold_bars:
                continue
            logger.debug(
                "body_reversion timeout-close %s after %d bars (max=%d)",
                sym,
                held,
                self.max_hold_bars,
            )

            # Cancel any pending OCO bracket exits for this position so the
            # close at market does not race with stop / TP fills.
            pending = getattr(engine, "pending_orders", None) or []
            for o in list(pending):
                if getattr(o, "symbol", "") != sym:
                    continue
                if not getattr(o, "oco_group", None):
                    continue
                try:
                    pending.remove(o)
                except ValueError:
                    pass

            from core.backtest.models import OrderSide, OrderType
            close_side = (
                OrderSide.SELL if getattr(pos, "side", None) == OrderSide.BUY else OrderSide.BUY
            )
            try:
                close_id = engine.place_order(
                    symbol=sym,
                    side=close_side,
                    quantity=int(getattr(pos, "quantity", 1)),
                    order_type=OrderType.MARKET,
                    price=float(getattr(pos, "current_price", getattr(pos, "entry_price", 0.0))),
                )
                for o in engine.pending_orders:
                    if o.order_id == close_id:
                        o.exit_reason = "timeout"
                        break
            except Exception as exc:
                logger.debug("body_reversion timeout-close failed for %s: %s", sym, exc)
            finally:
                self._entry_bar_seq.pop(sym, None)
        return None

    async def _manage_positions_live(self) -> None:
        """Time-exit for live: ``max_hold_bars`` × timeframe duration (wall clock)."""
        bot = self.trading_bot
        account_id = None
        if isinstance(bot.selected_account, dict):
            account_id = bot.selected_account.get("id")
        elif bot.selected_account:
            account_id = str(bot.selected_account)
        if not account_id:
            return

        try:
            positions = await bot.get_open_positions(account_id=account_id)
            orders = await bot.get_open_orders(account_id=account_id)
        except Exception as exc:
            logger.warning("body_reversion live manage_positions fetch failed: %s", exc)
            return

        now = datetime.now(timezone.utc)
        bar_mins = self._hold_bar_minutes()
        max_hold = timedelta(minutes=bar_mins * self.max_hold_bars)
        open_syms = {(p.get("symbol") or "").upper() for p in positions if p.get("symbol")}

        for sym in list(self._live_managed_symbols):
            if sym in open_syms:
                continue
            if self._live_orders_include_entry(sym, orders):
                continue
            self._live_managed_symbols.discard(sym)
            self._live_hold_start_utc.pop(sym, None)

        for pos in positions:
            sym = (pos.get("symbol") or "").upper()
            if not sym or sym not in self._live_managed_symbols:
                continue
            if sym not in self._live_hold_start_utc:
                self._live_hold_start_utc[sym] = now
            started = self._live_hold_start_utc[sym]
            if now - started < max_hold:
                continue

            pid = str(pos.get("id") or pos.get("position_id") or "").strip()
            if not pid:
                logger.warning("body_reversion live timeout-close: missing position id for %s", sym)
                continue

            logger.info(
                "body_reversion live timeout-close %s after %s (max_hold_bars=%d, bar=%dm)",
                sym,
                max_hold,
                self.max_hold_bars,
                bar_mins,
            )
            try:
                res = await bot.close_position(position_id=pid, account_id=account_id)
                if isinstance(res, dict) and res.get("error"):
                    logger.warning(
                        "body_reversion close_position %s: %s", sym, res.get("error")
                    )
            except Exception as exc:
                logger.warning("body_reversion close_position failed %s: %s", sym, exc)
            finally:
                self._live_hold_start_utc.pop(sym, None)
                self._live_managed_symbols.discard(sym)
                self._entry_bar_seq.pop(sym, None)

    async def cleanup(self) -> None:
        self.status = StrategyStatus.IDLE

    def _in_trading_window(self) -> bool:
        if getattr(self.trading_bot, "_is_strategy_replay", False):
            return True
        return super()._in_trading_window()

    def get_market_condition(self, symbol: str) -> MarketCondition:
        return MarketCondition.RANGING
