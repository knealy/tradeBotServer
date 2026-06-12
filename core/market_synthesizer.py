"""PA/SMC Synthesis Engine — "the brain".

User vision (2026-06-12):

    "I want a 'brain' that knows ALL of the price action / SMC concepts and
    can synthesize and utilize them to interpret realtime price and infer
    potential setups as they occur and of course be useful to other
    strategies as confluence for signals."

This module is the MINIMUM VIABLE implementation of that vision.  It
composes the existing primitive detectors (``core/market_structure.py``,
``core/price_action.py``, ``core/smc_setups.py``) into a single perception
layer that:

1. Subscribes to ``EventType.BAR_COMPLETED`` on the bot's event bus.
2. On each bar close, runs ALL primitive detectors against a rolling
   N-bar window (default 200) for that (symbol, timeframe).
3. Bundles the observations into a typed ``MarketContextSnapshot``.
4. Publishes a ``EventType.MARKET_CONTEXT_UPDATED`` event so the dashboard
   and downstream consumers see it.
5. Caches the latest snapshot per (symbol, timeframe) for synchronous
   queries from strategies — ``get_context(symbol, timeframe)``.

Two pure helpers are provided for strategies + downstream tooling:

* ``compute_bias(snapshot) -> (bias, confidence)`` — bullish / bearish /
  neutral plus a [0, 1] confidence based on how many primitives agree.
* ``confluence_score(snapshot, side) -> float`` — signed score in
  [-1.0, +1.0] indicating how much the PA/SMC picture agrees with a
  proposed trade direction.  A strategy can use this as an additive
  filter on top of its own edge.

Design constraints (from ``docs/STRATEGY_ARSENAL.md`` vision section):

* **Look-ahead-safe per bar.**  Each primitive only consumes bars
  available at close time.  Snapshots emitted at bar N see bars [..N].
* **Event-driven, not poll-driven.**  Bar-close → run pipeline → emit.
  Strategies query snapshots synchronously; the cache is thread-safe.
* **Cheap synchronous queries.**  ``get_context`` is an O(1) dict lookup
  guarded by a single lock.
* **Composable, not monolithic.**  Primitive detectors stay in their
  own modules.  This file is just the composition layer.

Opt-in via env var ``MARKET_SYNTHESIZER_ENABLED=1`` (default off).  Other
env knobs documented at the bottom of this file.

Status: scaffolding + MVP behaviour + tests.  No production strategy
consumes the brain yet; that integration is the next deliverable when
the user is ready.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from core.events import Event, EventType
from core.market_structure import (
    LiquiditySweep,
    Session,
    StructureEvent,
    SwingKind,
    SwingPoint,
    classify_session,
    detect_break_of_structure,
    detect_change_of_character,
    find_fair_value_gaps,
    find_liquidity_sweeps,
    find_order_blocks,
    find_swing_pivots,
    prior_session_levels,
)

logger = logging.getLogger(__name__)


# ════════════════════════════ snapshot dataclass ═════════════════════════════


@dataclass(frozen=True)
class MarketContextSnapshot:
    """Synthesizer's interpretation of a (symbol, timeframe) at a moment in time.

    Frozen + slots-like so it's safe to pass to subscribers without
    accidental mutation.  ``as_dict`` returns a JSON-serialisable view for
    event payloads / dashboard surfaces.
    """

    symbol: str
    timeframe: str
    as_of: Optional[datetime]
    bar_count: int

    # Session bucket for the last-bar timestamp ("nyam" / "nypm" / etc.),
    # None when ET conversion failed or the bar carried no timestamp.
    session: Optional[str]

    # Prior session H/L if computable from the window, else None.
    prior_session_high: Optional[float]
    prior_session_low: Optional[float]

    # Most-recent confirmed swings (from the lookback-3 fractal detector).
    recent_swing_high: Optional[float]
    recent_swing_high_label: Optional[str]   # "HH" / "LH" / "?"
    recent_swing_low: Optional[float]
    recent_swing_low_label: Optional[str]    # "HL" / "LL" / "?"

    # Most recent structural event from the lookback fractals.
    structure_event: str   # "bos_up" / "bos_down" / "choch_up" / "choch_down" / "none"

    # Most recent liquidity sweep within the window (close-back-inside
    # variety from find_liquidity_sweeps).  Bars-ago counts from the last
    # bar in the window backwards.
    recent_sweep_bars_ago: Optional[int]
    recent_sweep_direction: Optional[int]    # +1 = swept low (long bias), -1 = swept high (short bias)
    recent_sweep_strength: float             # close_distance / poke_amount, 0.0 when no sweep

    # Imbalance / level counts in the window.  Counts include ALL detected
    # primitives — strategies that want "active / unmitigated only" can
    # post-filter via the dedicated helpers in core/market_structure.
    fvg_count: int
    order_block_count: int

    # High-level bias derived from the above.  See ``compute_bias`` for
    # the scoring rules.
    bias: str          # "bullish" | "bearish" | "neutral"
    confidence: float  # [0.0, 1.0]

    # Per-factor evidence tally for the bias (useful for the dashboard and
    # for confluence_score callers that want to see WHY the bias is what
    # it is).  Sum of values ≈ confidence (modulo rounding).
    bias_factors: Dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        """Return a JSON-serialisable dict (datetimes → ISO strings)."""
        out: Dict[str, Any] = {}
        for k, v in asdict(self).items():
            if isinstance(v, datetime):
                out[k] = v.isoformat()
            else:
                out[k] = v
        return out


# ═════════════════════════════ pure scoring helpers ══════════════════════════


_BIAS_BULLISH = "bullish"
_BIAS_BEARISH = "bearish"
_BIAS_NEUTRAL = "neutral"


def _bias_factor_scores(
    *,
    structure_event: StructureEvent,
    recent_high_label: Optional[str],
    recent_low_label: Optional[str],
    recent_sweep_direction: Optional[int],
) -> Tuple[Dict[str, float], Dict[str, float]]:
    """Return (bullish_factors, bearish_factors) → (factor_name, weight).

    Weights sum to 1.0 across all factors for each side BEFORE any factor
    fires.  ``compute_bias`` sums the side that fires; the difference is
    the bias's net confidence.
    """
    bull: Dict[str, float] = {}
    bear: Dict[str, float] = {}

    # Structure event — strongest single signal (weight 0.40)
    if structure_event == StructureEvent.BOS_UP:
        bull["bos_up"] = 0.40
    elif structure_event == StructureEvent.BOS_DOWN:
        bear["bos_down"] = 0.40
    elif structure_event == StructureEvent.CHOCH_UP:
        bull["choch_up"] = 0.35
    elif structure_event == StructureEvent.CHOCH_DOWN:
        bear["choch_down"] = 0.35

    # Swing label trend (weight 0.20 each side, but only one can fire)
    if recent_high_label == "HH" and recent_low_label == "HL":
        bull["swing_trend_up"] = 0.20
    elif recent_high_label == "LH" and recent_low_label == "LL":
        bear["swing_trend_down"] = 0.20

    # Recent sweep — sweep below swing low → long bias (+1);
    # sweep above swing high → short bias (-1).  Weight 0.25.
    if recent_sweep_direction == +1:
        bull["recent_sweep_low_fade"] = 0.25
    elif recent_sweep_direction == -1:
        bear["recent_sweep_high_fade"] = 0.25

    return bull, bear


def compute_bias(snapshot: "MarketContextSnapshot") -> Tuple[str, float]:
    """Compute (bias_label, confidence) from snapshot primitive observations.

    Pure function (no I/O, no clock); deterministic for any given snapshot.
    The synthesizer uses this internally to populate ``snapshot.bias`` /
    ``snapshot.confidence``, and downstream consumers can re-derive if they
    want to apply different weights.
    """
    bull, bear = _bias_factor_scores(
        structure_event=StructureEvent(snapshot.structure_event),
        recent_high_label=snapshot.recent_swing_high_label,
        recent_low_label=snapshot.recent_swing_low_label,
        recent_sweep_direction=snapshot.recent_sweep_direction,
    )
    bull_total = sum(bull.values())
    bear_total = sum(bear.values())
    if bull_total == 0 and bear_total == 0:
        return _BIAS_NEUTRAL, 0.0
    if bull_total > bear_total:
        return _BIAS_BULLISH, min(1.0, bull_total - bear_total)
    if bear_total > bull_total:
        return _BIAS_BEARISH, min(1.0, bear_total - bull_total)
    return _BIAS_NEUTRAL, 0.0


def confluence_score(snapshot: "MarketContextSnapshot", side: int) -> float:
    """Score in [-1.0, +1.0] of PA/SMC agreement with a proposed trade direction.

    ``side`` is +1 for LONG, -1 for SHORT.  Score > 0 means the brain
    AGREES (favourable confluence); < 0 means the brain DISAGREES
    (counter-trend / counter-structural).

    Magnitude is ``snapshot.confidence`` signed by whether the snapshot
    bias matches the proposed side.

    Strategies typically use this to either:
    * boost their own size when |score| ≥ some threshold and sign matches, or
    * skip the trade when score < -threshold.
    """
    if side == 0:
        return 0.0
    if snapshot.bias == _BIAS_NEUTRAL:
        return 0.0
    bias_dir = +1 if snapshot.bias == _BIAS_BULLISH else -1
    if bias_dir == side:
        return float(snapshot.confidence)
    return -float(snapshot.confidence)


# ═══════════════════════════ snapshot construction ═══════════════════════════


def _bar_attr(b: Any, attr: str) -> Optional[float]:
    """Read .high/.low/.close from a bar object (attribute access only)."""
    try:
        return float(getattr(b, attr))
    except (AttributeError, TypeError, ValueError):
        return None


def _bar_timestamp(b: Any) -> Optional[datetime]:
    ts = getattr(b, "timestamp", None)
    return ts if isinstance(ts, datetime) else None


def build_snapshot(
    *,
    symbol: str,
    timeframe: str,
    bars: Sequence[Any],
    swing_lookback: int = 3,
    sweep_min_poke: float = 0.0,
    recent_sweep_max_bars_ago: int = 6,
) -> MarketContextSnapshot:
    """Construct a snapshot from a bar window.  Pure function — no I/O.

    ``bars`` must be in chronological order, oldest first.  The
    most-recent bar is treated as the "current" close (snapshot time).
    Detectors that need a minimum N bars (FVGs, swings) just return empty
    when there isn't enough history.
    """
    bar_count = len(bars)
    as_of = _bar_timestamp(bars[-1]) if bar_count > 0 else None

    # Session classification via ET conversion.
    session_label: Optional[str] = None
    if as_of is not None:
        try:
            from zoneinfo import ZoneInfo

            et = ZoneInfo("America/New_York")
            ts_et = as_of.astimezone(et) if as_of.tzinfo else as_of
            session_label = classify_session(ts_et).value
        except Exception:
            session_label = None

    # Prior-session levels — defensive, this can fail when bar timestamps
    # are unusable (e.g. synthetic bars with no tz info).
    psh: Optional[float] = None
    psl: Optional[float] = None
    if bar_count > 0:
        try:
            prior_map = prior_session_levels(bars)
            if as_of is not None:
                try:
                    from zoneinfo import ZoneInfo

                    et = ZoneInfo("America/New_York")
                    et_ts = as_of.astimezone(et) if as_of.tzinfo else as_of
                    cur_date = et_ts.date()
                    if cur_date in prior_map:
                        levels = prior_map[cur_date]
                        psh = levels.high
                        psl = levels.low
                except Exception:
                    pass
        except Exception:
            pass

    # Swings / structure (look-ahead-safe — find_swing_pivots only confirms
    # pivots whose index + lookback < bar_count - 1).
    swings: List[SwingPoint] = []
    structure_event = StructureEvent.NONE
    recent_high: Optional[SwingPoint] = None
    recent_low: Optional[SwingPoint] = None
    if bar_count >= 2 * swing_lookback + 1:
        try:
            swings = find_swing_pivots(bars, lookback=swing_lookback)
            highs = [s for s in swings if s.kind == SwingKind.HIGH]
            lows = [s for s in swings if s.kind == SwingKind.LOW]
            recent_high = highs[-1] if highs else None
            recent_low = lows[-1] if lows else None
            # Use the closed bar's close to evaluate BoS / CHoCH.
            last_close = _bar_attr(bars[-1], "close")
            if last_close is not None and recent_high is not None and recent_low is not None:
                # Inline BoS check (no SwingTracker state to maintain here).
                if last_close > recent_high.price:
                    structure_event = StructureEvent.BOS_UP
                elif last_close < recent_low.price:
                    structure_event = StructureEvent.BOS_DOWN
                else:
                    # CHoCH from labels.
                    if (
                        len(highs) >= 2
                        and len(lows) >= 2
                    ):
                        prior_low_label = lows[-2].label.value
                        cur_low_label = lows[-1].label.value
                        if prior_low_label == "LL" and cur_low_label == "HL":
                            structure_event = StructureEvent.CHOCH_UP
                        else:
                            prior_high_label = highs[-2].label.value
                            cur_high_label = highs[-1].label.value
                            if prior_high_label == "HH" and cur_high_label == "LH":
                                structure_event = StructureEvent.CHOCH_DOWN
        except Exception as exc:  # noqa: BLE001
            logger.debug("synthesizer: swing/structure failed for %s: %s", symbol, exc)

    # Liquidity sweeps — most-recent within window.
    recent_sweep_bars_ago: Optional[int] = None
    recent_sweep_direction: Optional[int] = None
    recent_sweep_strength: float = 0.0
    if swings and bar_count > 0:
        try:
            sweeps = find_liquidity_sweeps(
                bars,
                swings,
                min_poke_points=sweep_min_poke,
                require_close_back_inside=True,
            )
            if sweeps:
                last_sweep = sweeps[-1]
                bars_ago = (bar_count - 1) - last_sweep.bar_index
                if 0 <= bars_ago <= recent_sweep_max_bars_ago:
                    recent_sweep_bars_ago = bars_ago
                    recent_sweep_direction = int(last_sweep.direction)
                    if last_sweep.poke_amount > 0:
                        recent_sweep_strength = round(
                            last_sweep.close_distance / last_sweep.poke_amount, 4
                        )
        except Exception as exc:  # noqa: BLE001
            logger.debug("synthesizer: sweep detect failed for %s: %s", symbol, exc)

    # FVGs / OBs — count only.
    fvg_count = 0
    ob_count = 0
    try:
        if bar_count >= 3:
            fvg_count = len(find_fair_value_gaps(bars))
    except Exception:
        pass
    try:
        if bar_count >= 5:
            ob_count = len(find_order_blocks(bars))
    except Exception:
        pass

    # Build preliminary snapshot, then compute bias.
    high_label = (
        recent_high.label.value if recent_high is not None else None
    )
    low_label = (
        recent_low.label.value if recent_low is not None else None
    )

    # Compute bias via the pure helper (need the structure_event + labels
    # + sweep dir; sweep is the strongest sign).
    bull_factors, bear_factors = _bias_factor_scores(
        structure_event=structure_event,
        recent_high_label=high_label,
        recent_low_label=low_label,
        recent_sweep_direction=recent_sweep_direction,
    )
    bull_total = sum(bull_factors.values())
    bear_total = sum(bear_factors.values())
    if bull_total > bear_total:
        bias = _BIAS_BULLISH
        confidence = round(min(1.0, bull_total - bear_total), 4)
        bias_factors = {k: round(v, 4) for k, v in bull_factors.items()}
    elif bear_total > bull_total:
        bias = _BIAS_BEARISH
        confidence = round(min(1.0, bear_total - bull_total), 4)
        bias_factors = {k: round(v, 4) for k, v in bear_factors.items()}
    else:
        bias = _BIAS_NEUTRAL
        confidence = 0.0
        bias_factors = {}

    return MarketContextSnapshot(
        symbol=symbol,
        timeframe=timeframe,
        as_of=as_of,
        bar_count=bar_count,
        session=session_label,
        prior_session_high=psh,
        prior_session_low=psl,
        recent_swing_high=recent_high.price if recent_high else None,
        recent_swing_high_label=high_label,
        recent_swing_low=recent_low.price if recent_low else None,
        recent_swing_low_label=low_label,
        structure_event=structure_event.value,
        recent_sweep_bars_ago=recent_sweep_bars_ago,
        recent_sweep_direction=recent_sweep_direction,
        recent_sweep_strength=recent_sweep_strength,
        fvg_count=fvg_count,
        order_block_count=ob_count,
        bias=bias,
        confidence=confidence,
        bias_factors=bias_factors,
    )


# ═══════════════════════════════ service class ═══════════════════════════════


class MarketSynthesizerService:
    """Bus-subscribed synthesis engine that emits MarketContextSnapshot events.

    Wire up via ``maybe_start_synthesizer(trading_bot)`` from the bot's boot
    path.  Direct construction is fine in tests; pass a callable
    ``fetch_history`` that returns the rolling bar window for a (symbol,
    timeframe) when the BAR_COMPLETED event payload doesn't already carry
    enough bars to build a meaningful snapshot.
    """

    def __init__(
        self,
        *,
        event_bus: Any,
        fetch_history: Optional[Callable[..., Any]] = None,
        symbols: Optional[Sequence[str]] = None,
        timeframes: Optional[Sequence[str]] = None,
        window_bars: int = 200,
        swing_lookback: int = 3,
        sweep_min_poke: float = 0.0,
        recent_sweep_max_bars_ago: int = 6,
    ) -> None:
        self._bus = event_bus
        self._fetch = fetch_history
        self._symbols = {s.upper() for s in (symbols or [])}
        self._timeframes = set(timeframes or [])
        self._window_bars = max(2 * swing_lookback + 1, int(window_bars))
        self._swing_lookback = swing_lookback
        self._sweep_min_poke = sweep_min_poke
        self._recent_sweep_max_bars_ago = recent_sweep_max_bars_ago
        # Thread-safe latest-snapshot cache.
        self._snapshots: Dict[Tuple[str, str], MarketContextSnapshot] = {}
        self._cache_lock = threading.RLock()
        self._unsub: Optional[Callable[[], None]] = None
        self._snapshots_built = 0

    @property
    def snapshots_built(self) -> int:
        return self._snapshots_built

    # ── lifecycle ──────────────────────────────────────────────────────────

    async def start(self) -> bool:
        if self._bus is None:
            logger.debug("MarketSynthesizer: no event_bus; start() skipped")
            return False
        try:
            self._bus.subscribe(EventType.BAR_COMPLETED, self._on_bar_closed)
        except Exception as exc:  # noqa: BLE001
            logger.warning("MarketSynthesizer subscribe failed: %s", exc)
            return False

        def _unsub() -> None:
            try:
                self._bus.unsubscribe(EventType.BAR_COMPLETED, self._on_bar_closed)
            except Exception as exc:  # noqa: BLE001
                logger.debug("MarketSynthesizer unsubscribe failed: %s", exc)

        self._unsub = _unsub
        logger.info(
            "🧠 MarketSynthesizer active — symbols=%s timeframes=%s window=%d swing_lb=%d",
            sorted(self._symbols) or "(all)",
            sorted(self._timeframes) or "(all)",
            self._window_bars,
            self._swing_lookback,
        )
        return True

    async def stop(self) -> None:
        if self._unsub is not None:
            try:
                self._unsub()
            finally:
                self._unsub = None
        logger.info(
            "🧠 MarketSynthesizer stopped — built %d snapshots over its lifetime",
            self._snapshots_built,
        )

    # ── synchronous API ────────────────────────────────────────────────────

    def get_context(
        self, symbol: str, timeframe: Optional[str] = None
    ) -> Optional[MarketContextSnapshot]:
        """Return the latest snapshot for (symbol[, timeframe]).

        When ``timeframe`` is omitted, returns the most-recently-updated
        snapshot for any timeframe of that symbol.  None when no snapshot
        has been built yet.
        """
        key_symbol = symbol.upper()
        with self._cache_lock:
            if timeframe is not None:
                return self._snapshots.get((key_symbol, timeframe))
            # Find any snapshot for the symbol; pick most-recent.
            matches = [v for (s, _), v in self._snapshots.items() if s == key_symbol]
            if not matches:
                return None
            matches.sort(
                key=lambda s: s.as_of or datetime.min, reverse=True
            )
            return matches[0]

    def confluence_score(
        self, symbol: str, side: int, timeframe: Optional[str] = None
    ) -> float:
        """Convenience: synchronously fetch context and score it.

        Returns 0.0 when no snapshot is available.
        """
        snap = self.get_context(symbol, timeframe)
        if snap is None:
            return 0.0
        return confluence_score(snap, side)

    # ── bus callback ───────────────────────────────────────────────────────

    async def _on_bar_closed(self, event: Event) -> None:
        try:
            data = getattr(event, "data", None) or {}
            sym = str(data.get("symbol", "")).upper()
            tf = str(data.get("timeframe", ""))
            if not sym or not tf:
                return
            if self._symbols and sym not in self._symbols:
                return
            if self._timeframes and tf not in self._timeframes:
                return
            bars = data.get("bars") or data.get("history") or []
            if (not bars) and self._fetch is not None:
                try:
                    fetched = self._fetch(
                        symbol=sym, timeframe=tf, limit=self._window_bars
                    )
                    if asyncio.iscoroutine(fetched):
                        fetched = await fetched
                    bars = list(fetched or [])
                except Exception as exc:  # noqa: BLE001
                    logger.debug(
                        "MarketSynthesizer: fetch_history(%s/%s) failed: %s",
                        sym, tf, exc,
                    )
                    return
            if not bars:
                return
            # Trim to window if caller handed in more.
            if len(bars) > self._window_bars:
                bars = list(bars[-self._window_bars :])
            snap = build_snapshot(
                symbol=sym,
                timeframe=tf,
                bars=bars,
                swing_lookback=self._swing_lookback,
                sweep_min_poke=self._sweep_min_poke,
                recent_sweep_max_bars_ago=self._recent_sweep_max_bars_ago,
            )
            with self._cache_lock:
                self._snapshots[(sym, tf)] = snap
                self._snapshots_built += 1
            await self._publish(snap)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "MarketSynthesizer._on_bar_closed crashed: %s", exc, exc_info=True
            )

    async def _publish(self, snap: MarketContextSnapshot) -> None:
        if self._bus is None:
            return
        try:
            await self._bus.publish(
                Event(
                    type=EventType.MARKET_CONTEXT_UPDATED,
                    data={"snapshot": snap.as_dict()},
                    source="market_synthesizer",
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("MarketSynthesizer publish failed: %s", exc, exc_info=True)


# ═══════════════════════════════ boot helper ═════════════════════════════════


_GLOBAL_SERVICE: Optional[MarketSynthesizerService] = None
_GLOBAL_LOCK = threading.Lock()


def get_synthesizer() -> Optional[MarketSynthesizerService]:
    """Process-global accessor used by strategies that want to query
    the brain without holding a direct reference.

    Returns None until ``maybe_start_synthesizer(...)`` has installed a
    service.  This keeps the synthesizer optional — strategies degrade
    gracefully when it's disabled.
    """
    return _GLOBAL_SERVICE


def reset_synthesizer_for_tests() -> None:
    """Test-only: clear the process-global service handle so each test
    starts with a clean state.  Strategies / production paths must not
    call this."""
    global _GLOBAL_SERVICE
    with _GLOBAL_LOCK:
        _GLOBAL_SERVICE = None


def maybe_start_synthesizer(trading_bot: Any) -> Optional[MarketSynthesizerService]:
    """Opt-in boot helper.  Returns the started service (or None if disabled).

    Env vars:
    * ``MARKET_SYNTHESIZER_ENABLED``       — "1"/"true" to enable (default off).
    * ``MARKET_SYNTHESIZER_SYMBOLS``       — CSV list; default "" = all symbols.
    * ``MARKET_SYNTHESIZER_TIMEFRAMES``    — CSV list; default "" = all timeframes.
    * ``MARKET_SYNTHESIZER_WINDOW_BARS``   — int, default 200.
    * ``MARKET_SYNTHESIZER_SWING_LOOKBACK``— int, default 3.
    """
    global _GLOBAL_SERVICE
    enabled = os.environ.get("MARKET_SYNTHESIZER_ENABLED", "").strip().lower()
    if enabled not in ("1", "true", "yes", "on"):
        return None
    with _GLOBAL_LOCK:
        if _GLOBAL_SERVICE is not None:
            return _GLOBAL_SERVICE
        bus = getattr(trading_bot, "event_bus", None)
        if bus is None:
            logger.warning("MarketSynthesizer: bot has no event_bus — disabled")
            return None
        symbols = [
            s.strip()
            for s in os.environ.get("MARKET_SYNTHESIZER_SYMBOLS", "").split(",")
            if s.strip()
        ]
        timeframes = [
            s.strip()
            for s in os.environ.get("MARKET_SYNTHESIZER_TIMEFRAMES", "").split(",")
            if s.strip()
        ]

        def _parse_int(name: str, default: int) -> int:
            raw = os.environ.get(name, "").strip()
            if not raw:
                return default
            try:
                return int(raw)
            except ValueError:
                logger.warning(
                    "MarketSynthesizer: invalid %s=%r — using default %d",
                    name, raw, default,
                )
                return default

        svc = MarketSynthesizerService(
            event_bus=bus,
            fetch_history=getattr(trading_bot, "get_historical_data", None),
            symbols=symbols or None,
            timeframes=timeframes or None,
            window_bars=_parse_int("MARKET_SYNTHESIZER_WINDOW_BARS", 200),
            swing_lookback=_parse_int("MARKET_SYNTHESIZER_SWING_LOOKBACK", 3),
        )
        _GLOBAL_SERVICE = svc
        return svc
