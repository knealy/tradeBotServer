"""Shared regime classifier.

Single source of truth for "is the market trending vs ranging".  Each
new strategy (planned: ``vwap_pullback_continuation``,
``opening_drive_continuation``; first real consumer: the 2026-06-09
``overnight_reversion`` revival regime-gate tune-up) consults this
module to gate signals by regime.  Strategies in the production tier
(``morning_range_reversion`` etc.) carry their own internal regime
gates and are NOT being retro-fitted to this module — that's
intentional: parity tests pin the production-tier behaviour.

NOTE: the original docstring referenced ``nr_compression_break`` and
``globex_drift_continuation`` as planned consumers — both were
retired 2026-06-09 after the post-engine-fix walk-forward truth
recap confirmed they were dead.  See ``docs/CHANGELOG.md``.

Indicators
----------
- **KER** (Kaufman Efficiency Ratio): ratio of net price change to
  total path length over a lookback window.  ``KER = 1`` ⇒ perfectly
  trending (all moves in same direction); ``KER ≈ 0`` ⇒ chop
  (price ends back near start after equal up/down movement).
- **ADX (Wilder, 14)**: classic Welles Wilder trend strength
  oscillator; ``ADX ≥ 25`` is the textbook "trending" threshold.
- **realised-vol percentile rank**: rank of the last 1-day rolling
  std-dev of log-returns against the last ``vol_lookback`` bars.
  Optional context for the snapshot — not part of the label decision
  but reported so strategies can implement vol-sensitive overlays.

Labels
------
- ``"trend"``: KER ≥ ``trend_ker`` AND ADX ≥ ``trend_adx``
- ``"chop"``:  KER ≤ ``chop_ker``  AND ADX ≤ ``chop_adx``
- ``"mixed"`` everything else (the default bucket — the typical bar)

Backtest vs live
----------------
- **Backtest**: strategies call :func:`classify` directly with the
  current bar window (slice of the replay DataFrame or a deque the
  strategy maintains).  No event bus involvement.
- **Live**: strategies that want push-notification can subscribe to
  ``EventType.REGIME_UPDATE`` once it's wired through
  ``StrategyManager``.  The polling-style ``classify(bars)`` API
  works in both modes and is the recommended path for the new
  strategies (avoids the live/backtest asymmetry that plagued the
  consec-loss breaker bridge in earlier rounds).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence

import numpy as np


@dataclass(frozen=True)
class RegimeConfig:
    """Tunable thresholds.  Defaults chosen against MNQ 5m data; the trend
    bucket is intentionally narrow (~10-15 % of bars) and the chop bucket
    similar, so the modal label is ``mixed``."""
    ker_lookback: int = 50         # ~250 min on 5m TF
    adx_period: int = 14
    adx_window_mult: int = 4       # use last 4 × adx_period bars for stability
    vol_lookback_bars: int = 14 * 78  # 14 trading days × 78 5m RTH bars
    vol_rolling_bars: int = 78        # 1-day rolling realised vol

    # Label thresholds
    trend_ker: float = 0.55
    chop_ker: float = 0.30
    trend_adx: float = 25.0
    chop_adx: float = 18.0


@dataclass(frozen=True)
class RegimeSnapshot:
    """Result of :func:`classify`.  Stable schema — downstream code can pin
    field names."""
    label: str          # one of {"trend", "chop", "mixed"}
    ker: float          # 0.0 .. 1.0
    adx: float          # 0.0 .. 100.0
    vol_pct: float      # 0.0 .. 1.0 (rank of latest 1-day vol vs lookback)
    n_bars: int         # bars actually consumed (≤ len(bars))


# ----------------------------- indicators ----------------------------------


def kaufman_efficiency_ratio(closes: Sequence[float]) -> float:
    """``|close_t - close_0| / sum(|close_i - close_{i-1}|)``.

    ``len(closes) < 2`` → 0.0.  Flat path (all closes equal) → 0.0 (no
    divisor).
    """
    arr = np.asarray(closes, dtype=float)
    if arr.size < 2:
        return 0.0
    net = abs(float(arr[-1] - arr[0]))
    abs_moves = float(np.sum(np.abs(np.diff(arr))))
    if abs_moves == 0.0:
        return 0.0
    return net / abs_moves


def _wilder_smooth(values: np.ndarray, period: int) -> np.ndarray:
    """Wilder's smoothing: first ``period`` values summed, then
    ``out_t = out_{t-1} - out_{t-1}/period + value_t``.  Leading
    ``period-1`` outputs are 0."""
    n = values.size
    out = np.zeros(n, dtype=float)
    if n < period:
        return out
    out[period - 1] = float(np.sum(values[:period]))
    for i in range(period, n):
        out[i] = out[i - 1] - out[i - 1] / period + values[i]
    return out


def adx(
    high: Sequence[float],
    low: Sequence[float],
    close: Sequence[float],
    period: int = 14,
) -> float:
    """Welles Wilder ADX (averaged DI difference).  Returns the LATEST
    value, not a series.  Insufficient data → 0.0.
    """
    h = np.asarray(high, dtype=float)
    l = np.asarray(low, dtype=float)
    c = np.asarray(close, dtype=float)
    n = c.size
    # Need 2*period bars: period for the Wilder warmup + period for the ADX average.
    if n < 2 * period + 1:
        return 0.0

    up = h[1:] - h[:-1]                # n-1
    dn = l[:-1] - l[1:]                # n-1
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = np.maximum.reduce([
        h[1:] - l[1:],
        np.abs(h[1:] - c[:-1]),
        np.abs(l[1:] - c[:-1]),
    ])

    atr_ = _wilder_smooth(tr, period)
    smooth_plus_dm = _wilder_smooth(plus_dm, period)
    smooth_minus_dm = _wilder_smooth(minus_dm, period)

    with np.errstate(divide="ignore", invalid="ignore"):
        plus_di = 100.0 * np.divide(smooth_plus_dm, atr_, out=np.zeros_like(atr_), where=atr_ > 0)
        minus_di = 100.0 * np.divide(smooth_minus_dm, atr_, out=np.zeros_like(atr_), where=atr_ > 0)
        di_sum = plus_di + minus_di
        dx = 100.0 * np.divide(np.abs(plus_di - minus_di), di_sum, out=np.zeros_like(di_sum), where=di_sum > 0)

    tail = dx[-period:]
    if tail.size < period or not np.all(np.isfinite(tail)):
        return 0.0
    val = float(np.mean(tail))
    return val if np.isfinite(val) else 0.0


def vol_percentile(closes: Sequence[float], rolling_bars: int, lookback_bars: int) -> float:
    """Rank of last ``rolling_bars`` log-return std-dev against the same
    metric computed over every rolling window in the last ``lookback_bars``
    bars.  Returns 0..1 (1 = current vol is highest in lookback).
    """
    arr = np.asarray(closes, dtype=float)
    if arr.size < rolling_bars + 1:
        return 0.0
    rets = np.diff(np.log(np.where(arr > 0, arr, 1.0)))
    tail = rets[-min(lookback_bars, rets.size):]
    if tail.size < rolling_bars:
        return 0.0
    # Rolling std with stride trick.
    rolled = np.lib.stride_tricks.sliding_window_view(tail, rolling_bars)
    stds = np.std(rolled, axis=1, ddof=0)
    current = float(stds[-1])
    if not np.isfinite(current):
        return 0.0
    rank = float(np.mean(stds < current))
    return rank


# ----------------------------- classify ------------------------------------


def _bar_get(bar, key: str, default: float = 0.0) -> float:
    """Tolerate both dict-like bars and pandas Series rows."""
    if isinstance(bar, dict):
        return float(bar.get(key, default))
    try:
        return float(bar[key])
    except (KeyError, IndexError, TypeError):
        return float(default)


def classify(bars: Iterable, config: Optional[RegimeConfig] = None) -> RegimeSnapshot:
    """Classify the regime as of the LAST bar in ``bars``.

    ``bars`` is an iterable of bar dicts (or pandas rows) with keys
    ``high``, ``low``, ``close``.  Order: oldest first, newest last
    (the natural append-only order strategies build up).

    Returns a :class:`RegimeSnapshot` with the label + the underlying
    indicator values.  Insufficient-data path returns ``label="mixed",
    ker=0, adx=0, vol_pct=0`` so callers can treat ``mixed`` as the safe
    "no information" default.
    """
    cfg = config or RegimeConfig()
    bar_list: List = list(bars)
    n = len(bar_list)
    min_required = max(cfg.ker_lookback, 2 * cfg.adx_period + 1)
    if n < min_required:
        return RegimeSnapshot(label="mixed", ker=0.0, adx=0.0, vol_pct=0.0, n_bars=n)

    closes_ker = [_bar_get(b, "close") for b in bar_list[-cfg.ker_lookback:]]
    ker_val = kaufman_efficiency_ratio(closes_ker)

    adx_window = bar_list[-(cfg.adx_period * cfg.adx_window_mult + 1):]
    adx_val = adx(
        [_bar_get(b, "high") for b in adx_window],
        [_bar_get(b, "low") for b in adx_window],
        [_bar_get(b, "close") for b in adx_window],
        period=cfg.adx_period,
    )

    vol_window = bar_list[-cfg.vol_lookback_bars:]
    vol = vol_percentile(
        [_bar_get(b, "close") for b in vol_window],
        rolling_bars=cfg.vol_rolling_bars,
        lookback_bars=cfg.vol_lookback_bars,
    )

    if ker_val >= cfg.trend_ker and adx_val >= cfg.trend_adx:
        label = "trend"
    elif ker_val <= cfg.chop_ker and adx_val <= cfg.chop_adx:
        label = "chop"
    else:
        label = "mixed"

    return RegimeSnapshot(label=label, ker=ker_val, adx=adx_val, vol_pct=vol, n_bars=n)


# ----------------------------- live publisher ------------------------------
# 2026-06-09 — minimal live publisher.  Off by default; opt-in per
# operator via env var.  Strategies subscribe to ``REGIME_UPDATE``
# events; the publisher reclassifies on every ``BarClosedEvent`` for
# the configured reference symbol/timeframe and emits when the label
# changes (or when ``always_emit=True``, e.g. for richer downstream
# vol-regime overlays).
#
# Why minimal: changing live behaviour of production strategies that
# carry their own internal regime gates (e.g. ``morning_range_reversion``)
# would invalidate the recent walk-forward tunes.  This publisher is
# strictly a NEW EVENT STREAM that strategies can opt into; no
# implicit subscription / no auto-wiring.  See
# ``docs/STRATEGY_ARSENAL.md`` → "Phase 1 wiring status" for the policy.

import asyncio
import logging
import os
from typing import Any, Optional

_logger = logging.getLogger(__name__)


class RegimePublisherService:
    """Optional service: classify regime on bar-close + publish events.

    Wiring example (off by default — operator must opt-in):

        # in trading_bot.py boot path:
        from core.regime import RegimePublisherService, RegimeConfig
        self.regime_publisher = RegimePublisherService(
            self,
            reference_symbol="MNQ",
            reference_timeframe="5m",
            config=RegimeConfig(),
        )
        await self.regime_publisher.start()

    Subscribers:

        bus.subscribe(EventType.REGIME_UPDATE, my_callback)
        # event.data = {"label": "trend"|"chop"|"mixed",
        #               "ker": float, "adx": float, "vol_pct": float,
        #               "symbol": str, "timeframe": str,
        #               "n_bars": int, "previous_label": str|None}

    Polling-style alternative (backtest + live, no event bus needed):

        snap = classify(self._recent_bars)
        if snap.label != "trend":
            return None
    """

    def __init__(
        self,
        trading_bot: Any,
        reference_symbol: str = "MNQ",
        reference_timeframe: str = "5m",
        config: Optional[RegimeConfig] = None,
        always_emit: bool = False,
        min_bars_between_emits: int = 12,  # ~1 hour on 5m
    ):
        self.trading_bot = trading_bot
        self.reference_symbol = str(reference_symbol).upper()
        self.reference_timeframe = str(reference_timeframe)
        self.config = config or RegimeConfig()
        self.always_emit = bool(always_emit)
        self.min_bars_between_emits = max(0, int(min_bars_between_emits))
        self._last_label: Optional[str] = None
        self._bars_since_last_emit: int = 0
        self._unsub: Optional[Any] = None

    async def start(self) -> bool:
        """Subscribe to ``BAR_COMPLETED`` on the bot's event bus."""
        bus = getattr(self.trading_bot, "event_bus", None)
        if bus is None:
            _logger.debug("RegimePublisher: no event_bus on bot; live publishing skipped")
            return False
        try:
            from core.events import EventType
            bus.subscribe(EventType.BAR_COMPLETED, self._on_bar_closed)
        except Exception as exc:
            _logger.warning("RegimePublisher subscribe failed: %s", exc)
            return False

        def _unsub():
            try:
                from core.events import EventType as _ET
                bus.unsubscribe(_ET.BAR_COMPLETED, self._on_bar_closed)
            except Exception as exc:
                _logger.debug("RegimePublisher unsubscribe failed: %s", exc)

        self._unsub = _unsub
        _logger.info(
            "🌡️  RegimePublisher active — ref=%s/%s (cfg: KER %.2f/%.2f, ADX %.0f/%.0f)",
            self.reference_symbol, self.reference_timeframe,
            self.config.trend_ker, self.config.chop_ker,
            self.config.trend_adx, self.config.chop_adx,
        )
        return True

    async def stop(self) -> None:
        unsub = self._unsub
        self._unsub = None
        if unsub is not None:
            try:
                unsub()
            except Exception as exc:
                _logger.debug("RegimePublisher.stop: unsub raised %s", exc)

    async def _on_bar_closed(self, event: Any) -> None:
        """Bus callback — re-classify on each reference-symbol bar close."""
        try:
            data = getattr(event, "data", None) or {}
            sym = str(data.get("symbol", "")).upper()
            tf = str(data.get("timeframe", ""))
            if sym != self.reference_symbol or tf != self.reference_timeframe:
                return
            bars = data.get("bars") or data.get("history") or []
            if not bars:
                fetch = getattr(self.trading_bot, "get_historical_data", None)
                if callable(fetch):
                    bars = await fetch(
                        symbol=self.reference_symbol,
                        timeframe=self.reference_timeframe,
                        limit=max(self.config.vol_lookback_bars, 300),
                    ) or []
            if not bars:
                return
            snap = classify(bars, self.config)
            self._bars_since_last_emit += 1
            label_changed = (snap.label != self._last_label)
            emit_now = label_changed or (
                self.always_emit and self._bars_since_last_emit >= self.min_bars_between_emits
            )
            if emit_now:
                await self._publish(snap)
                self._last_label = snap.label
                self._bars_since_last_emit = 0
        except Exception as exc:
            _logger.error("RegimePublisher._on_bar_closed crashed: %s", exc, exc_info=True)

    async def _publish(self, snap: RegimeSnapshot) -> None:
        bus = getattr(self.trading_bot, "event_bus", None)
        if bus is None:
            return
        try:
            from core.events import Event, EventType
            await bus.publish(Event(
                type=EventType.REGIME_UPDATE,
                data={
                    "label": snap.label,
                    "ker": snap.ker,
                    "adx": snap.adx,
                    "vol_pct": snap.vol_pct,
                    "n_bars": snap.n_bars,
                    "symbol": self.reference_symbol,
                    "timeframe": self.reference_timeframe,
                    "previous_label": self._last_label,
                },
                source="regime_publisher",
            ))
        except Exception as exc:
            _logger.error("RegimePublisher publish failed: %s", exc, exc_info=True)


def maybe_start_regime_publisher(trading_bot: Any) -> Optional[RegimePublisherService]:
    """Operator opt-in helper.  Returns the service if env enables it,
    else ``None``.  Call from the bot boot path:

        from core.regime import maybe_start_regime_publisher
        self.regime_publisher = maybe_start_regime_publisher(self)
        if self.regime_publisher is not None:
            await self.regime_publisher.start()

    Env vars:
      - ``REGIME_PUBLISHER_ENABLED`` ∈ {"1","true","yes","on"}: enable
      - ``REGIME_PUBLISHER_SYMBOL`` (default ``MNQ``)
      - ``REGIME_PUBLISHER_TIMEFRAME`` (default ``5m``)
      - ``REGIME_PUBLISHER_ALWAYS_EMIT`` ∈ {"1","true","yes","on"}: emit
        on every bar (default off; emits only when label changes)

    NOTE: even when started, the publisher has NO effect on production
    strategies unless those strategies explicitly subscribe to
    ``EventType.REGIME_UPDATE``.  No strategy currently does — this
    infrastructure is intentionally inert until the first opt-in
    consumer ships.
    """
    enabled = os.environ.get("REGIME_PUBLISHER_ENABLED", "0").strip().lower() in (
        "1", "true", "yes", "on",
    )
    if not enabled:
        return None
    symbol = os.environ.get("REGIME_PUBLISHER_SYMBOL", "MNQ").strip() or "MNQ"
    timeframe = os.environ.get("REGIME_PUBLISHER_TIMEFRAME", "5m").strip() or "5m"
    always_emit = os.environ.get("REGIME_PUBLISHER_ALWAYS_EMIT", "0").strip().lower() in (
        "1", "true", "yes", "on",
    )
    return RegimePublisherService(
        trading_bot,
        reference_symbol=symbol,
        reference_timeframe=timeframe,
        always_emit=always_emit,
    )
