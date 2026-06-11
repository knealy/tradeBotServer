"""Market-structure primitives: swing pivots, BoS, CHoCH, levels, sessions.

This module sits beside ``core.price_action`` and provides the
*structural context* that turns raw candlestick patterns into tradeable
signals.  The two compose:

    pattern: "bullish_pin"
    structure: "at recent swing low + after CHoCH down→up"
    ────────────────────────────────────────────────────────────
    tradeable signal: long with stop below the pin's low

Five orthogonal primitives, all pure functions / lightweight stateful
classes — designed to be cheap enough to run on every bar in live or
backtest mode:

1. ``find_swing_pivots(bars, lookback)`` — fractal-style pivot detector.
   A swing high at index ``i`` requires ``bars[i].high`` to exceed all
   highs in ``[i-lookback, i+lookback]``.  Confirmed only after
   ``lookback`` bars elapse (no look-ahead bias).

2. ``SwingTracker`` — online wrapper that maintains the most recent N
   swing highs / lows and labels them with structural tags (HH = higher
   high than the prior swing high, LH = lower high, HL = higher low,
   LL = lower low).  Foundation for BoS + CHoCH.

3. ``detect_break_of_structure(swings, last_close)`` — returns BoS_UP
   when the latest close exceeds the most recent confirmed swing high
   (during a sequence trending up), BoS_DOWN for the inverse.  Used to
   confirm trend continuation.

4. ``detect_change_of_character(tracker)`` — returns CHoCH_UP when a
   downtrend (LH-LL sequence) prints its first HL (i.e. the most recent
   swing low is HIGHER than the prior one).  Used to flag trend
   reversal.

5. ``classify_session(timestamp_et)`` — categorises a bar by US/Eastern
   wall clock into "asia", "london", "premarket", "nyam", "lunch",
   "nypm", "close", "afterhours".  Strategy filters use this to gate
   patterns by session character (e.g. take pin-bar fades only in
   "nyam" because that's when the price-action sim found the edge).

6. ``prior_session_levels(bars, session_zone="America/New_York")`` —
   returns the most recent COMPLETED calendar day's high + low + close
   in ET, mapped onto each bar.  These are major liquidity magnets;
   patterns near prior-day H or L are higher-probability setups.

7. ``find_equal_highs(swings, tol_points)`` — flag pairs of swing highs
   within ``tol_points`` of each other.  Equal highs are stop-hunt
   targets — a wick that pokes through then closes back inside is the
   liquidity-sweep signature.

Run ``scripts/simulate_price_action_trades.py`` with the new
``--at-swing``, ``--at-prior-day-hl``, ``--session`` flags to validate
which (pattern × structure) combinations have a real edge.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
from enum import Enum
from typing import Deque, Dict, Iterable, List, Optional, Sequence, Tuple

try:
    import pytz  # type: ignore
except ImportError:  # pragma: no cover
    pytz = None


__all__ = [
    # swings + structure
    "SwingKind",
    "SwingPoint",
    "find_swing_pivots",
    "SwingTracker",
    "StructureEvent",
    "detect_break_of_structure",
    "detect_change_of_character",
    "Session",
    "classify_session",
    "prior_session_levels",
    "find_equal_highs",
    "find_equal_lows",
    # SMC / ICT primitives
    "FairValueGap",
    "find_fair_value_gaps",
    "OrderBlock",
    "find_order_blocks",
    "LiquiditySweep",
    "find_liquidity_sweeps",
    "is_inside_fvg",
    "is_inside_order_block",
]


# ─────────────────────────── data models ────────────────────────────


class SwingKind(str, Enum):
    HIGH = "high"
    LOW = "low"


class _SwingLabel(str, Enum):
    """Structural label assigned to a swing relative to the previous
    swing of the same kind."""
    HH = "HH"  # Higher High
    LH = "LH"  # Lower High
    HL = "HL"  # Higher Low
    LL = "LL"  # Lower Low
    NONE = "?"  # unlabeled (first of its kind)


@dataclass(frozen=True)
class SwingPoint:
    """A confirmed swing high or low.

    ``index`` is the bar index in the original sequence where the
    pivot is located (NOT the bar where it was confirmed — that's
    ``index + lookback``).  ``label`` is HH/LH/HL/LL relative to the
    prior swing of the same kind, ``"?"`` for the first.
    """

    index: int
    kind: SwingKind
    price: float
    timestamp: Optional[datetime] = None
    label: _SwingLabel = _SwingLabel.NONE


# ───────────────────── fractal pivot detection ──────────────────────


def find_swing_pivots(
    bars: Sequence,
    *,
    lookback: int = 3,
) -> List[SwingPoint]:
    """Williams-fractal-style pivot detection.

    A bar at index ``i`` is a **swing high** when:
        ``bars[i].high > bars[j].high`` for all ``j`` in
        ``[i-lookback, i+lookback]``, ``j != i``.

    Swing low is the mirror condition.  Lookback of 3 means a pivot is
    confirmed 3 bars *after* it occurs — there is no look-ahead bias as
    long as the caller only consults pivots whose index satisfies
    ``i + lookback < current_bar_index``.

    ``bars`` may be any object with ``.high`` / ``.low`` attributes
    (``CandleBar``, aggregator bars, dicts wrapped in dataclasses, etc.)
    or 2-element tuples ``(high, low)``.  Returns the list in
    chronological pivot order (mixing highs + lows).
    """
    if lookback < 1:
        raise ValueError("lookback must be >= 1")
    n = len(bars)
    if n < 2 * lookback + 1:
        return []

    def _h(b) -> float:
        return float(getattr(b, "high", None) if not isinstance(b, tuple) else b[0])

    def _l(b) -> float:
        return float(getattr(b, "low", None) if not isinstance(b, tuple) else b[1])

    def _ts(b):
        return getattr(b, "timestamp", None)

    pivots: List[SwingPoint] = []
    prev_high: Optional[float] = None
    prev_low: Optional[float] = None

    for i in range(lookback, n - lookback):
        h_i = _h(bars[i])
        l_i = _l(bars[i])
        # Window neighbours (exclusive of i).
        is_high = True
        is_low = True
        for j in range(i - lookback, i + lookback + 1):
            if j == i:
                continue
            if _h(bars[j]) >= h_i:
                is_high = False
            if _l(bars[j]) <= l_i:
                is_low = False
            if not (is_high or is_low):
                break

        if is_high:
            if prev_high is None:
                label = _SwingLabel.NONE
            elif h_i > prev_high:
                label = _SwingLabel.HH
            else:
                label = _SwingLabel.LH
            pivots.append(SwingPoint(index=i, kind=SwingKind.HIGH, price=h_i,
                                     timestamp=_ts(bars[i]), label=label))
            prev_high = h_i

        if is_low:
            if prev_low is None:
                label = _SwingLabel.NONE
            elif l_i > prev_low:
                label = _SwingLabel.HL
            else:
                label = _SwingLabel.LL
            pivots.append(SwingPoint(index=i, kind=SwingKind.LOW, price=l_i,
                                     timestamp=_ts(bars[i]), label=label))
            prev_low = l_i

    return pivots


# ────────────────── online swing tracker (stateful) ─────────────────


class SwingTracker:
    """Stateful online swing tracker.

    Feed bars in chronological order via :meth:`update`; pivots become
    "confirmed" ``lookback`` bars after they occur.  Maintains a
    bounded history of the most recent N swing highs + N swing lows.
    """

    def __init__(self, *, lookback: int = 3, max_history: int = 32) -> None:
        if lookback < 1:
            raise ValueError("lookback must be >= 1")
        self.lookback = lookback
        self.max_history = max_history
        # Buffer keeps the last (2*lookback+1) bars; once it's full we
        # check whether bars[lookback] is a swing.
        self._buf: Deque = deque(maxlen=2 * lookback + 1)
        self._bar_count: int = 0
        # Confirmed pivots.
        self._highs: Deque[SwingPoint] = deque(maxlen=max_history)
        self._lows: Deque[SwingPoint] = deque(maxlen=max_history)

    @property
    def swing_highs(self) -> List[SwingPoint]:
        return list(self._highs)

    @property
    def swing_lows(self) -> List[SwingPoint]:
        return list(self._lows)

    @property
    def most_recent_high(self) -> Optional[SwingPoint]:
        return self._highs[-1] if self._highs else None

    @property
    def most_recent_low(self) -> Optional[SwingPoint]:
        return self._lows[-1] if self._lows else None

    def update(self, bar) -> Optional[SwingPoint]:
        """Ingest the next bar.  Returns the newly-confirmed pivot or None.

        The pivot's ``index`` field is the original bar index; we track
        it through ``self._bar_count`` so callers know where the pivot
        sits in their own bar list.
        """
        self._buf.append((self._bar_count, bar))
        self._bar_count += 1
        if len(self._buf) < self._buf.maxlen:
            return None

        # Pivot candidate is the middle of the buffer.
        mid_idx, mid_bar = self._buf[self.lookback]
        mid_h = float(getattr(mid_bar, "high"))
        mid_l = float(getattr(mid_bar, "low"))

        is_high = True
        is_low = True
        for offset, (_, b) in enumerate(self._buf):
            if offset == self.lookback:
                continue
            if float(getattr(b, "high")) >= mid_h:
                is_high = False
            if float(getattr(b, "low")) <= mid_l:
                is_low = False

        new_pivot: Optional[SwingPoint] = None
        if is_high:
            label = self._label_for_new_high(mid_h)
            new_pivot = SwingPoint(
                index=mid_idx, kind=SwingKind.HIGH, price=mid_h,
                timestamp=getattr(mid_bar, "timestamp", None), label=label,
            )
            self._highs.append(new_pivot)
        if is_low:
            label = self._label_for_new_low(mid_l)
            new_pivot = SwingPoint(
                index=mid_idx, kind=SwingKind.LOW, price=mid_l,
                timestamp=getattr(mid_bar, "timestamp", None), label=label,
            )
            self._lows.append(new_pivot)
        return new_pivot

    def _label_for_new_high(self, price: float) -> _SwingLabel:
        if not self._highs:
            return _SwingLabel.NONE
        return _SwingLabel.HH if price > self._highs[-1].price else _SwingLabel.LH

    def _label_for_new_low(self, price: float) -> _SwingLabel:
        if not self._lows:
            return _SwingLabel.NONE
        return _SwingLabel.HL if price > self._lows[-1].price else _SwingLabel.LL


# ─────────────────── structure-break detection ──────────────────────


class StructureEvent(str, Enum):
    BOS_UP = "bos_up"        # close breaks above most recent swing high
    BOS_DOWN = "bos_down"    # close breaks below most recent swing low
    CHOCH_UP = "choch_up"    # downtrend → first HL printed
    CHOCH_DOWN = "choch_down"  # uptrend → first LH printed
    NONE = "none"


def detect_break_of_structure(
    tracker: SwingTracker,
    last_close: float,
) -> StructureEvent:
    """Return BOS_UP / BOS_DOWN / NONE based on the most recent close.

    BOS_UP: ``last_close`` > most recent confirmed swing high price.
    BOS_DOWN: ``last_close`` < most recent confirmed swing low price.

    Note: caller is responsible for the "first time this triggers"
    semantics if needed (this primitive returns True every bar the
    condition holds).
    """
    if tracker.most_recent_high is not None and last_close > tracker.most_recent_high.price:
        return StructureEvent.BOS_UP
    if tracker.most_recent_low is not None and last_close < tracker.most_recent_low.price:
        return StructureEvent.BOS_DOWN
    return StructureEvent.NONE


def detect_change_of_character(tracker: SwingTracker) -> StructureEvent:
    """Detect CHoCH — the first counter-trend pivot of opposite label.

    Defined as:
        Was sequence of LH / LL (downtrend) and most recent low is
        labelled HL → CHoCH_UP.
        Was sequence of HH / HL (uptrend) and most recent high is
        labelled LH → CHoCH_DOWN.

    Requires at least two of each kind to be meaningful.  Returns NONE
    if not enough history or no character change detected.
    """
    highs = tracker.swing_highs
    lows = tracker.swing_lows
    if len(highs) < 2 or len(lows) < 2:
        return StructureEvent.NONE

    # CHoCH_UP: prior trend was bearish (recent LH, prior LL pattern);
    # most recent LOW is now HL — flipping the structure.
    prior_low_label = lows[-2].label
    curr_low_label = lows[-1].label
    if prior_low_label == _SwingLabel.LL and curr_low_label == _SwingLabel.HL:
        return StructureEvent.CHOCH_UP

    # CHoCH_DOWN: prior trend was bullish (HH, HL); most recent HIGH is
    # now LH — flipping bearish.
    prior_high_label = highs[-2].label
    curr_high_label = highs[-1].label
    if prior_high_label == _SwingLabel.HH and curr_high_label == _SwingLabel.LH:
        return StructureEvent.CHOCH_DOWN

    return StructureEvent.NONE


# ─────────────────── equal highs / equal lows ──────────────────────


def find_equal_highs(
    swings: Sequence[SwingPoint],
    *,
    tol_points: float,
) -> List[Tuple[SwingPoint, SwingPoint]]:
    """Return pairs of swing highs within ``tol_points`` of each other.

    Equal highs cluster stop-loss orders — institutions and retail alike
    park stops just above the most recent swing high.  A wick that pokes
    above the cluster then closes back inside is the classic liquidity
    sweep / stop-hunt signature, often followed by a sharp reversal.
    """
    highs = [s for s in swings if s.kind == SwingKind.HIGH]
    pairs: List[Tuple[SwingPoint, SwingPoint]] = []
    for i, a in enumerate(highs):
        for b in highs[i + 1:]:
            if abs(a.price - b.price) <= tol_points:
                pairs.append((a, b))
    return pairs


def find_equal_lows(
    swings: Sequence[SwingPoint],
    *,
    tol_points: float,
) -> List[Tuple[SwingPoint, SwingPoint]]:
    lows = [s for s in swings if s.kind == SwingKind.LOW]
    pairs: List[Tuple[SwingPoint, SwingPoint]] = []
    for i, a in enumerate(lows):
        for b in lows[i + 1:]:
            if abs(a.price - b.price) <= tol_points:
                pairs.append((a, b))
    return pairs


# ─────────────────────── session classifier ─────────────────────────


class Session(str, Enum):
    """US/Eastern wall-clock session buckets — chosen to align with the
    intraday character documented in ``docs/STRATEGY_ARSENAL.md``."""

    ASIA = "asia"           # 18:00 → 03:00 ET  (Tokyo + Asian open)
    LONDON = "london"       # 03:00 → 08:00 ET  (London open + AM session)
    PREMARKET = "premarket" # 08:00 → 09:30 ET  (pre-cash-open positioning)
    NYAM = "nyam"           # 09:30 → 12:00 ET  (NY morning drive)
    LUNCH = "lunch"         # 12:00 → 13:30 ET  (NY lunch lull / London close)
    NYPM = "nypm"           # 13:30 → 15:00 ET  (NY afternoon drive)
    CLOSE = "close"         # 15:00 → 16:00 ET  (power hour + close)
    AFTERHOURS = "afterhours"  # 16:00 → 18:00 ET  (settlement / re-open)


_SESSION_BOUNDARIES: List[Tuple[time, Session]] = [
    # Sorted by start time within a 24h ET clock.
    (time(0, 0), Session.ASIA),       # ASIA wraps midnight
    (time(3, 0), Session.LONDON),
    (time(8, 0), Session.PREMARKET),
    (time(9, 30), Session.NYAM),
    (time(12, 0), Session.LUNCH),
    (time(13, 30), Session.NYPM),
    (time(15, 0), Session.CLOSE),
    (time(16, 0), Session.AFTERHOURS),
    (time(18, 0), Session.ASIA),      # ASIA re-engages at 18:00 ET
]


def classify_session(ts_et: datetime) -> Session:
    """Map an ET timestamp to its session bucket.

    Caller is responsible for handing in an ET-localized datetime; if a
    naive datetime is passed it's assumed to already be ET.
    """
    t = ts_et.time()
    # Walk the boundary table; the last entry whose start <= t wins.
    current = Session.ASIA  # midnight default
    for start, sess in _SESSION_BOUNDARIES:
        if t >= start:
            current = sess
        else:
            break
    return current


# ─────────────────── prior-session level mapping ───────────────────


@dataclass(frozen=True)
class SessionLevels:
    """High + low + close from a completed calendar session (ET date)."""
    session_date: date
    high: float
    low: float
    close: float


def prior_session_levels(
    bars: Sequence,
    *,
    session_zone: str = "America/New_York",
) -> Dict[date, SessionLevels]:
    """Build a {today_et_date → prior_session_levels} mapping.

    Aggregates each bar's high/low/close by ET calendar date and returns
    a dict whose value for ``date X`` is the (high, low, close) of the
    most recent FULL session ending strictly before ``X``.  Use this to
    annotate each live bar with "yesterday's high / low" — major
    liquidity targets.

    ``bars`` may be CandleBars or any object with ``.high`` / ``.low`` /
    ``.close`` and a ``.timestamp`` that is either naive UTC or
    tz-aware.
    """
    if pytz is not None:
        try:
            tz = pytz.timezone(session_zone)
        except Exception:
            tz = None
    else:
        tz = None
    try:
        from zoneinfo import ZoneInfo
        if tz is None:
            tz = ZoneInfo(session_zone)
    except Exception:
        if tz is None:
            tz = timezone.utc

    # First pass: aggregate by ET date.
    by_date: Dict[date, Dict[str, float]] = {}
    for b in bars:
        ts = getattr(b, "timestamp", None)
        if ts is None:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        try:
            ts_et = ts.astimezone(tz)
        except Exception:
            continue
        d = ts_et.date()
        entry = by_date.setdefault(d, {"high": -float("inf"), "low": float("inf"), "close": 0.0})
        h = float(getattr(b, "high", 0.0))
        l = float(getattr(b, "low", 0.0))
        c = float(getattr(b, "close", 0.0))
        if h > entry["high"]:
            entry["high"] = h
        if l < entry["low"]:
            entry["low"] = l
        entry["close"] = c  # last seen wins (bars iterated chronologically)

    # Second pass: each date maps to PRIOR date's levels.
    sorted_dates = sorted(by_date.keys())
    out: Dict[date, SessionLevels] = {}
    for i, d in enumerate(sorted_dates):
        if i == 0:
            continue
        prior = sorted_dates[i - 1]
        agg = by_date[prior]
        out[d] = SessionLevels(
            session_date=prior, high=agg["high"], low=agg["low"], close=agg["close"],
        )
    return out


# ════════════════════════════════════════════════════════════════════
# SMC / ICT primitives (2026-06-10)
#
# Three structural concepts that turn raw pattern detection into
# higher-conviction trade setups by adding **institutional context**:
#
#   - Fair Value Gap (FVG / Imbalance)  — 3-bar gap; price tends to return
#     to fill it (mean-revert magnet).
#   - Order Block (OB)                  — last opposite-color bar before
#     a strong impulsive move; future S/R level (institutional footprint).
#   - Liquidity Sweep (Stop Hunt)       — wick that pokes through a prior
#     swing high/low then closes back inside; classic stop-run reversal
#     signature.
#
# All three are stateful in the sense that they evolve over time — a FVG
# forms on bar i+2, may be tested on bar i+50.  The detectors emit
# **formation events** (when the structure forms) and the consumer
# typically maintains a per-bar "active / unfilled" list for trade
# filtering.  ``is_inside_fvg`` / ``is_inside_order_block`` are the cheap
# point-in-zone helpers the simulator uses.
# ════════════════════════════════════════════════════════════════════


# ─────────────────────────── Fair Value Gap ────────────────────────────


@dataclass
class FairValueGap:
    """A 3-bar imbalance.

    Bullish FVG: ``bars[i-2].high < bars[i].low`` — the middle bar (i-1)
    moved so quickly upward that no trading happened in the gap zone
    ``[bars[i-2].high, bars[i].low]``.  Price tends to RETURN to this
    zone to "fill the gap" — making it a magnet for mean-reversion
    setups.  When a future bar's low touches ``upper`` from above, the
    FVG is considered mitigated.

    Bearish FVG is the mirror — gap zone ``[bars[i].high, bars[i-2].low]``.

    ``mitigated`` toggles to True once price returns to the zone.  The
    typical SMC trade is to enter when price first touches an unmitigated
    FVG (long for bullish, short for bearish) with stop on the far side.
    """

    formation_index: int   # bar i where the gap was confirmed (3-bar pattern)
    direction: int          # +1 bullish, -1 bearish
    upper: float            # top edge of the gap zone
    lower: float            # bottom edge of the gap zone
    formation_ts: Optional[datetime] = None
    mitigated: bool = False
    mitigation_index: Optional[int] = None

    @property
    def width(self) -> float:
        return self.upper - self.lower

    def contains(self, price: float) -> bool:
        return self.lower <= price <= self.upper


def find_fair_value_gaps(bars: Sequence) -> List[FairValueGap]:
    """Walk ``bars`` and collect every formed FVG with mitigation state.

    Marks each FVG as ``mitigated=True`` if any subsequent bar's high/low
    re-enters the gap zone, recording the first mitigation index.  This
    gives the caller a complete history; for live "is bar X inside an
    unmitigated FVG?" queries use ``is_inside_fvg(fvgs, bar_index, price)``.

    ``bars`` must expose ``.high`` and ``.low`` (and optionally
    ``.timestamp``).  Indices in the returned list are positions in the
    input sequence.
    """
    if len(bars) < 3:
        return []

    def _h(b) -> float: return float(getattr(b, "high"))
    def _l(b) -> float: return float(getattr(b, "low"))
    def _ts(b): return getattr(b, "timestamp", None)

    gaps: List[FairValueGap] = []
    for i in range(2, len(bars)):
        b_prev2 = bars[i - 2]
        b_curr = bars[i]
        # Bullish FVG
        if _h(b_prev2) < _l(b_curr):
            gaps.append(FairValueGap(
                formation_index=i, direction=+1,
                upper=_l(b_curr), lower=_h(b_prev2),
                formation_ts=_ts(b_curr),
            ))
        # Bearish FVG
        elif _l(b_prev2) > _h(b_curr):
            gaps.append(FairValueGap(
                formation_index=i, direction=-1,
                upper=_l(b_prev2), lower=_h(b_curr),
                formation_ts=_ts(b_curr),
            ))

    # Mitigation pass — for each gap, find the first subsequent bar
    # whose high/low pierces the zone.
    for g in gaps:
        for j in range(g.formation_index + 1, len(bars)):
            hi = _h(bars[j])
            lo = _l(bars[j])
            # Any overlap with [lower, upper] counts as mitigation.
            if hi >= g.lower and lo <= g.upper:
                g.mitigated = True
                g.mitigation_index = j
                break
    return gaps


def is_inside_fvg(
    fvgs: Sequence[FairValueGap],
    bar_index: int,
    price: float,
    *,
    direction: Optional[int] = None,
    require_unmitigated: bool = True,
) -> Optional[FairValueGap]:
    """Return the first FVG that contains ``price`` at ``bar_index``.

    Filters:
      - ``direction``: if set (+1 / -1), only that FVG kind is considered.
      - ``require_unmitigated``: when True, ignore FVGs whose mitigation
        index is at or before ``bar_index`` (they've already been filled).

    O(N) over the gap list per call — fine for batch validation; for
    live use, maintain a separate "active" list keyed by recency.
    """
    for g in fvgs:
        if g.formation_index > bar_index:
            continue  # not formed yet
        if direction is not None and g.direction != direction:
            continue
        if require_unmitigated and g.mitigated and g.mitigation_index is not None and g.mitigation_index <= bar_index:
            continue
        if g.contains(price):
            return g
    return None


# ───────────────────────────── Order Block ──────────────────────────────


@dataclass
class OrderBlock:
    """The last opposite-color bar before a strong impulsive move.

    Bullish OB: the last BEARISH bar before a strong up-move (≥
    ``impulse_threshold`` × ATR within the next ``window`` bars).  The
    bar's high/low define a zone that often acts as future support.

    Bearish OB: the last BULLISH bar before a strong down-move.

    ``formation_index`` is the OB BAR ITSELF.  ``confirmation_index``
    is the bar at which the impulse threshold was met — i.e. the
    EARLIEST bar at which the OB is observable without look-ahead.
    Consumers MUST gate on ``confirmation_index`` (not formation) when
    deciding whether the OB is "known" at a given live bar.

    ``mitigated`` toggles when price re-enters the OB zone — the
    standard SMC trade is to enter ON the first retest of an
    unmitigated OB in the impulse direction.
    """

    formation_index: int       # the OB bar itself (last opposite-color before impulse)
    confirmation_index: int    # earliest bar at which the impulse is observable
    direction: int             # +1 bullish OB (long-bias), -1 bearish OB
    upper: float               # zone top (formation bar's high)
    lower: float               # zone bottom (formation bar's low)
    impulse_size: float        # how strong the move that followed was (in points)
    formation_ts: Optional[datetime] = None
    mitigated: bool = False
    mitigation_index: Optional[int] = None

    def contains(self, price: float) -> bool:
        return self.lower <= price <= self.upper


def find_order_blocks(
    bars: Sequence,
    *,
    impulse_threshold_atr: float = 2.0,
    window: int = 5,
    atr_period: int = 14,
) -> List[OrderBlock]:
    """Detect order blocks across ``bars``.

    Algorithm: walk forward, maintain a rolling ATR.  For each bar that
    is BEARISH (close < open), check whether the next ``window`` bars
    achieve a high ≥ bar.low + ``impulse_threshold_atr`` × ATR (this is
    a "strong up-move" — a bullish OB).  Mirror for bullish bars +
    down-moves.  When a candidate qualifies, emit an OB whose zone is
    the candidate bar's [low, high].

    Mitigation pass tags each OB with the first subsequent bar that
    re-enters its zone.
    """
    if len(bars) < window + atr_period + 1:
        return []

    def _h(b) -> float: return float(getattr(b, "high"))
    def _l(b) -> float: return float(getattr(b, "low"))
    def _c(b) -> float: return float(getattr(b, "close"))
    def _o(b) -> float: return float(getattr(b, "open"))
    def _ts(b): return getattr(b, "timestamp", None)
    def _is_bullish(b) -> bool: return _c(b) > _o(b)
    def _is_bearish(b) -> bool: return _c(b) < _o(b)

    # Pre-compute rolling Wilder ATR.
    atrs: List[Optional[float]] = [None] * len(bars)
    trs: List[float] = []
    prev_close = _c(bars[0])
    for i in range(len(bars)):
        h = _h(bars[i]); l = _l(bars[i]); c = _c(bars[i])
        tr = max(h - l, abs(h - prev_close), abs(l - prev_close))
        trs.append(tr)
        prev_close = c
        if i + 1 < atr_period:
            continue
        if i + 1 == atr_period:
            atrs[i] = sum(trs[:atr_period]) / atr_period
        else:
            atrs[i] = (atrs[i - 1] * (atr_period - 1) + tr) / atr_period

    blocks: List[OrderBlock] = []
    # NOTE: outer loop deliberately walks to ``len(bars) - 1`` so OB
    # candidates whose impulse confirms WITHIN a partial future-window
    # (i.e. the impulse fired in the last 1-4 bars of the input) are
    # still detected.  This matters for LIVE detection where the bar
    # slice always ends at NOW — without this, the strategy can never
    # see an OB whose confirmation bar IS the current bar (the most
    # recent and tradeable OBs).  Offline simulation results are
    # unaffected because there's always a full future window for any
    # bar except the very last few of the entire dataset.
    for i in range(atr_period, len(bars) - 1):
        atr = atrs[i]
        if atr is None or atr <= 0:
            continue
        impulse = impulse_threshold_atr * atr
        cand = bars[i]
        # Cap the inner window at ``len(bars)`` so we never index past
        # the slice's last bar.  The inner loop still ``break``s on
        # first confirmation, so the partial-window case is handled
        # exactly like the full-window case.
        j_end = min(i + 1 + window, len(bars))

        # Bullish OB: last bearish bar before impulse-up.
        # confirmation_index is the FIRST bar within the window whose
        # high crosses the impulse threshold (the earliest bar at
        # which the OB is observable in real time, no look-ahead).
        if _is_bearish(cand):
            cand_low = _l(cand)
            confirm_at: Optional[int] = None
            max_high = -float("inf")
            for j in range(i + 1, j_end):
                hj = _h(bars[j])
                if hj > max_high:
                    max_high = hj
                if hj - cand_low >= impulse:
                    confirm_at = j
                    break
            if confirm_at is not None:
                blocks.append(OrderBlock(
                    formation_index=i, confirmation_index=confirm_at, direction=+1,
                    upper=_h(cand), lower=cand_low,
                    impulse_size=max_high - cand_low,
                    formation_ts=_ts(cand),
                ))
        # Bearish OB: last bullish bar before impulse-down.
        if _is_bullish(cand):
            cand_high = _h(cand)
            confirm_at = None
            min_low = float("inf")
            for j in range(i + 1, j_end):
                lj = _l(bars[j])
                if lj < min_low:
                    min_low = lj
                if cand_high - lj >= impulse:
                    confirm_at = j
                    break
            if confirm_at is not None:
                blocks.append(OrderBlock(
                    formation_index=i, confirmation_index=confirm_at, direction=-1,
                    upper=cand_high, lower=_l(cand),
                    impulse_size=cand_high - min_low,
                    formation_ts=_ts(cand),
                ))

    # Mitigation pass — only count retests AFTER confirmation (no
    # look-ahead) and we explicitly skip the impulse bars themselves
    # since they're what created the OB.
    for ob in blocks:
        for j in range(ob.confirmation_index + 1, len(bars)):
            if _h(bars[j]) >= ob.lower and _l(bars[j]) <= ob.upper:
                ob.mitigated = True
                ob.mitigation_index = j
                break
    return blocks


def is_inside_order_block(
    blocks: Sequence[OrderBlock],
    bar_index: int,
    price: float,
    *,
    direction: Optional[int] = None,
    require_unmitigated: bool = True,
) -> Optional[OrderBlock]:
    """Point-in-OB-zone query.

    A bar at ``bar_index`` may only see OBs whose IMPULSE has already
    been confirmed (``confirmation_index <= bar_index``) — using
    formation_index would leak the impulse-window into the live signal
    and produce inflated edge measurements.
    """
    for ob in blocks:
        if ob.confirmation_index > bar_index:
            continue  # impulse not yet confirmed — would be look-ahead
        if direction is not None and ob.direction != direction:
            continue
        if require_unmitigated and ob.mitigated and ob.mitigation_index is not None and ob.mitigation_index <= bar_index:
            continue
        if ob.contains(price):
            return ob
    return None


# ──────────────────────── Liquidity Sweep ───────────────────────────


@dataclass
class LiquiditySweep:
    """A wick that pokes through a prior swing high/low then closes back inside.

    The classic stop-hunt signature.  When price spikes above a recent
    swing high, the cluster of stop-losses just above that high are
    triggered (long stops + short entries on breakout).  If price then
    immediately reverses and closes BACK BELOW the swing high, the
    breakout was a fake — institutions used retail's stops as their
    liquidity to enter the OPPOSITE direction.  The trade is to SHORT
    in the direction of the reversal (or LONG on the mirror — sweep
    below swing low then close back above).
    """

    bar_index: int          # the sweeping bar
    direction: int          # -1 = swept high (short bias), +1 = swept low (long bias)
    swept_price: float      # the swing high/low that was pierced
    poke_amount: float      # how far past the swing level the wick went
    close_distance: float   # |close - swept_price|; larger = stronger reversal
    timestamp: Optional[datetime] = None


def find_liquidity_sweeps(
    bars: Sequence,
    swings: Sequence[SwingPoint],
    *,
    min_poke_points: float = 0.0,
    require_close_back_inside: bool = True,
) -> List[LiquiditySweep]:
    """Walk ``bars`` and emit sweep events.

    For each bar, check whether its wick pierced the most recent
    confirmed swing high (or low) and whether its close returned to the
    "inside" of that level.  Uses ``swings`` (caller computes via
    ``find_swing_pivots``) to identify levels — only swings whose
    ``index`` is strictly less than the current bar index are eligible
    (no look-ahead).

    ``min_poke_points``: minimum pierce distance to count.
    ``require_close_back_inside``: when False, any wick that exceeds the
    level counts even if the bar closed beyond — useful for measuring
    raw sweep frequency vs reversal-confirmed sweeps separately.
    """
    if not bars or not swings:
        return []

    def _h(b) -> float: return float(getattr(b, "high"))
    def _l(b) -> float: return float(getattr(b, "low"))
    def _c(b) -> float: return float(getattr(b, "close"))
    def _ts(b): return getattr(b, "timestamp", None)

    # Sort swings by index for efficient "most recent before bar i" lookup.
    sorted_swings = sorted(swings, key=lambda s: s.index)
    sweeps: List[LiquiditySweep] = []

    # Walk bars; for each bar i, find the most recent swing high + swing
    # low whose index < i and check the sweep conditions.
    last_high: Optional[SwingPoint] = None
    last_low: Optional[SwingPoint] = None
    swing_ptr = 0
    for i in range(len(bars)):
        # Advance swing pointer to include all swings with index < i.
        while swing_ptr < len(sorted_swings) and sorted_swings[swing_ptr].index < i:
            s = sorted_swings[swing_ptr]
            if s.kind == SwingKind.HIGH:
                last_high = s
            else:
                last_low = s
            swing_ptr += 1

        b = bars[i]
        # Sweep of swing HIGH: wick pierces above, close back below.
        if last_high is not None:
            poke = _h(b) - last_high.price
            if poke > min_poke_points:
                closed_inside = _c(b) < last_high.price
                if not require_close_back_inside or closed_inside:
                    sweeps.append(LiquiditySweep(
                        bar_index=i, direction=-1,
                        swept_price=last_high.price,
                        poke_amount=poke,
                        close_distance=last_high.price - _c(b),
                        timestamp=_ts(b),
                    ))
        # Sweep of swing LOW: wick pierces below, close back above.
        if last_low is not None:
            poke = last_low.price - _l(b)
            if poke > min_poke_points:
                closed_inside = _c(b) > last_low.price
                if not require_close_back_inside or closed_inside:
                    sweeps.append(LiquiditySweep(
                        bar_index=i, direction=+1,
                        swept_price=last_low.price,
                        poke_amount=poke,
                        close_distance=_c(b) - last_low.price,
                        timestamp=_ts(b),
                    ))

    return sweeps
