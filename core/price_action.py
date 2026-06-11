"""Price-action pattern primitive — pure-function detectors + probability emitter.

This module is the foundation for a future `price_action` strategy that
emits trade signals based on candlestick formations rather than time
windows.  The deliverable here is intentionally narrow: **detect named
patterns** + **measure their historical conditional edge** so the next
session can decide which patterns are tradeable before writing strategy
code.

Three pieces:

1. ``CandleBar`` — frozen dataclass with the canonical OHLCV fields the
   detectors operate on.  Strategies can wrap ``core.bar_aggregator.Bar``
   instances trivially (see ``CandleBar.from_aggregator``).
2. ``detect_patterns(history, *, min_history=2)`` — pure function.
   Given the most recent ``N`` bars (newest last), returns the list of
   patterns that fire on the *latest* bar.
3. ``ProbabilityEmitter`` — stateful rolling tally that, given a stream
   of bars, maintains the per-pattern conditional probability of the
   next bar closing higher / lower and the mean next-bar return.  Use
   this online (subscribes the next bar's outcome to the previous bar's
   pattern set) for live signal probabilities.

The patterns are intentionally classic + unambiguous so the detectors
can be unit-tested with hand-crafted fixtures.  Tightening / loosening
the thresholds (e.g. the doji body ratio) is a *strategy*-level
decision and lives in TOML; the detectors here use sensible defaults.

Run ``scripts/validate_price_action_patterns.py --help`` for the
historical-edge validator.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

__all__ = [
    "CandleBar",
    "Pattern",
    "PatternEvent",
    "detect_patterns",
    "ProbabilityEmitter",
]


# ─────────────────────────── data models ────────────────────────────


@dataclass(frozen=True)
class CandleBar:
    """Minimal OHLCV bar.  Newest-last convention everywhere in this module.

    Volume is optional (``0`` is fine for synthetic / fixture data); the
    detectors don't currently use it but the validator + emitter pass it
    through.  ``timestamp`` may be naive or tz-aware — neither is
    inspected here.
    """

    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def body_top(self) -> float:
        return max(self.open, self.close)

    @property
    def body_bottom(self) -> float:
        return min(self.open, self.close)

    @property
    def upper_wick(self) -> float:
        return self.high - self.body_top

    @property
    def lower_wick(self) -> float:
        return self.body_bottom - self.low

    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def is_bullish(self) -> bool:
        return self.close > self.open

    @property
    def is_bearish(self) -> bool:
        return self.close < self.open

    @classmethod
    def from_aggregator(cls, bar) -> "CandleBar":
        """Coerce a ``core.bar_aggregator.Bar`` into our shape."""
        return cls(
            timestamp=getattr(bar, "timestamp", None) or getattr(bar, "start_time", None),
            open=float(bar.open),
            high=float(bar.high),
            low=float(bar.low),
            close=float(bar.close),
            volume=float(getattr(bar, "volume", 0.0) or 0.0),
        )


# Canonical names — kept short, lowercase_with_underscores, stable
# because the probability emitter and downstream strategies key on them.
#
# Grouping (informational):
#   Single-bar  : pin (bull/bear), doji (4 sub-types), marubozu (bull/bear)
#   Two-bar     : engulfing, inside_bar, outside_bar (bull/bear),
#                 tweezer_top/bottom, harami (bull/bear),
#                 piercing, dark_cloud_cover
#   Three-bar   : morning_star, evening_star,
#                 three_white_soldiers, three_black_crows
class Pattern:
    # ── single-bar ──────────────────────────────────────────────────
    BULLISH_PIN = "bullish_pin"
    BEARISH_PIN = "bearish_pin"
    DOJI = "doji"
    LONG_LEGGED_DOJI = "long_legged_doji"
    GRAVESTONE_DOJI = "gravestone_doji"
    DRAGONFLY_DOJI = "dragonfly_doji"
    BULLISH_MARUBOZU = "bullish_marubozu"
    BEARISH_MARUBOZU = "bearish_marubozu"
    # ── two-bar ─────────────────────────────────────────────────────
    BULLISH_ENGULFING = "bullish_engulfing"
    BEARISH_ENGULFING = "bearish_engulfing"
    INSIDE_BAR = "inside_bar"
    BULLISH_OUTSIDE_BAR = "bullish_outside_bar"
    BEARISH_OUTSIDE_BAR = "bearish_outside_bar"
    TWEEZER_TOP = "tweezer_top"
    TWEEZER_BOTTOM = "tweezer_bottom"
    BULLISH_HARAMI = "bullish_harami"
    BEARISH_HARAMI = "bearish_harami"
    PIERCING = "piercing"
    DARK_CLOUD_COVER = "dark_cloud_cover"
    # ── three-bar ──────────────────────────────────────────────────
    MORNING_STAR = "morning_star"
    EVENING_STAR = "evening_star"
    THREE_WHITE_SOLDIERS = "three_white_soldiers"
    THREE_BLACK_CROWS = "three_black_crows"

    ALL: Tuple[str, ...] = (
        # single-bar
        BULLISH_PIN, BEARISH_PIN,
        DOJI, LONG_LEGGED_DOJI, GRAVESTONE_DOJI, DRAGONFLY_DOJI,
        BULLISH_MARUBOZU, BEARISH_MARUBOZU,
        # two-bar
        BULLISH_ENGULFING, BEARISH_ENGULFING,
        INSIDE_BAR,
        BULLISH_OUTSIDE_BAR, BEARISH_OUTSIDE_BAR,
        TWEEZER_TOP, TWEEZER_BOTTOM,
        BULLISH_HARAMI, BEARISH_HARAMI,
        PIERCING, DARK_CLOUD_COVER,
        # three-bar
        MORNING_STAR, EVENING_STAR,
        THREE_WHITE_SOLDIERS, THREE_BLACK_CROWS,
    )


@dataclass(frozen=True)
class PatternEvent:
    """Fired by the detector when a pattern matches on the latest bar."""

    name: str  # one of Pattern.*
    bar: CandleBar
    extras: Dict[str, float]  # pattern-specific metrics (body ratio, etc.)


# ──────────────────────────── detectors ─────────────────────────────


# Default thresholds.  Picked from classic price-action literature
# (Steve Nison, Bulkowski).  All overridable per call.
DEFAULT_PIN_WICK_BODY_MULTIPLE = 2.0  # wick must be ≥ 2× body
DEFAULT_PIN_OPPOSITE_WICK_MAX_BODY = 1.0  # opposite wick ≤ 1× body
DEFAULT_PIN_BODY_RANGE_MAX = 0.4  # body must be ≤ 40 % of total range

# Doji: body must be very small relative to total range.  Standard is
# 5 %; tweaking up to 10 % captures more "near-doji" bars in noisy data.
DEFAULT_DOJI_BODY_RANGE_MAX = 0.10
# Doji shape sub-classifiers (relative to total range):
#   gravestone — body at bottom, all wick on top
#   dragonfly  — body at top, all wick on bottom
#   long-legged — body in middle, both wicks long
DEFAULT_DOJI_GRAVESTONE_BOTTOM_WICK_MAX = 0.10  # bottom wick ≤ 10 % range
DEFAULT_DOJI_DRAGONFLY_TOP_WICK_MAX = 0.10  # top wick ≤ 10 % range
DEFAULT_DOJI_LONGLEG_MIN_BOTH_WICKS = 0.30  # both wicks ≥ 30 % range

# Marubozu: full-body bar with negligible wicks (≤ 5 % of range either side).
DEFAULT_MARUBOZU_WICK_RANGE_MAX = 0.05

# Tweezer tolerance: highs (or lows) considered "equal" when within this
# fraction of the **first bar's range** (so a 10pt-range bar tolerates
# ~1pt of slack).  Robust across instruments without per-symbol tuning.
DEFAULT_TWEEZER_TOL_RANGE_FRAC = 0.10

# Harami: prior bar must have a meaningful body for the small-body-inside
# to mean compression.  Skip if prior body is tiny (likely a doji itself).
DEFAULT_HARAMI_PRIOR_BODY_RANGE_MIN = 0.40
# Current bar's body must be < this fraction of prior bar's body to count.
DEFAULT_HARAMI_BODY_RATIO_MAX = 0.60

# Piercing / dark-cloud-cover: current bar must close beyond the 50 %
# midpoint of the prior bar's body (the canonical Steve Nison threshold).
DEFAULT_PIERCING_PENETRATION = 0.50

# Morning/evening star: middle bar must be a small body (≤ this fraction
# of either flanking bar's body).
DEFAULT_STAR_MIDDLE_BODY_MAX = 0.50

# Three-soldier/crow progression: each bar's body must be ≥ this fraction
# of the prior bar's body (avoids tiny → tiny → tiny noise candles).
DEFAULT_THREE_BAR_MIN_BODY_PROGRESSION = 0.50


def _engulfing(prev: CandleBar, curr: CandleBar, bullish: bool) -> Optional[PatternEvent]:
    if prev.body <= 0 or curr.body <= 0:
        return None
    if bullish:
        # Prior bar bearish; current bullish; current body engulfs prior body.
        if not (prev.is_bearish and curr.is_bullish):
            return None
        if not (curr.body_top >= prev.body_top and curr.body_bottom <= prev.body_bottom):
            return None
        name = Pattern.BULLISH_ENGULFING
    else:
        if not (prev.is_bullish and curr.is_bearish):
            return None
        if not (curr.body_top >= prev.body_top and curr.body_bottom <= prev.body_bottom):
            return None
        name = Pattern.BEARISH_ENGULFING
    return PatternEvent(
        name=name,
        bar=curr,
        extras={"prev_body": prev.body, "curr_body": curr.body, "ratio": curr.body / max(prev.body, 1e-9)},
    )


def _inside(prev: CandleBar, curr: CandleBar) -> Optional[PatternEvent]:
    if not (curr.high < prev.high and curr.low > prev.low):
        return None
    return PatternEvent(
        name=Pattern.INSIDE_BAR,
        bar=curr,
        extras={
            "range_ratio": curr.range / max(prev.range, 1e-9),
        },
    )


def _pin(curr: CandleBar, bullish: bool) -> Optional[PatternEvent]:
    if curr.range <= 0:
        return None
    body = curr.body
    if bullish:
        # Long lower wick (rejection of lows).  Body sits in the upper third.
        if curr.lower_wick < DEFAULT_PIN_WICK_BODY_MULTIPLE * max(body, 1e-9):
            return None
        if curr.upper_wick > DEFAULT_PIN_OPPOSITE_WICK_MAX_BODY * max(body, 1e-9):
            return None
        if body / curr.range > DEFAULT_PIN_BODY_RANGE_MAX:
            return None
        name = Pattern.BULLISH_PIN
    else:
        if curr.upper_wick < DEFAULT_PIN_WICK_BODY_MULTIPLE * max(body, 1e-9):
            return None
        if curr.lower_wick > DEFAULT_PIN_OPPOSITE_WICK_MAX_BODY * max(body, 1e-9):
            return None
        if body / curr.range > DEFAULT_PIN_BODY_RANGE_MAX:
            return None
        name = Pattern.BEARISH_PIN
    return PatternEvent(
        name=name,
        bar=curr,
        extras={
            "wick_body_ratio": (curr.lower_wick if bullish else curr.upper_wick) / max(body, 1e-9),
            "body_range_ratio": body / curr.range,
        },
    )


def _doji_family(curr: CandleBar) -> Optional[PatternEvent]:
    """Classify a small-body bar into one of four doji sub-types.

    Returns the most-specific match (long-legged > gravestone > dragonfly
    > generic doji) so a bar is never tagged with multiple doji variants
    on the same pass.
    """
    if curr.range <= 0:
        return None
    body_frac = curr.body / curr.range
    if body_frac > DEFAULT_DOJI_BODY_RANGE_MAX:
        return None
    upper_frac = curr.upper_wick / curr.range
    lower_frac = curr.lower_wick / curr.range
    # Long-legged: both wicks meaningful — body sits in the middle.
    if upper_frac >= DEFAULT_DOJI_LONGLEG_MIN_BOTH_WICKS and lower_frac >= DEFAULT_DOJI_LONGLEG_MIN_BOTH_WICKS:
        return PatternEvent(
            name=Pattern.LONG_LEGGED_DOJI, bar=curr,
            extras={"body_range_ratio": body_frac, "upper_wick_frac": upper_frac, "lower_wick_frac": lower_frac},
        )
    # Gravestone: body at the bottom, top wick dominates.
    if lower_frac <= DEFAULT_DOJI_GRAVESTONE_BOTTOM_WICK_MAX and upper_frac >= 0.50:
        return PatternEvent(
            name=Pattern.GRAVESTONE_DOJI, bar=curr,
            extras={"body_range_ratio": body_frac, "upper_wick_frac": upper_frac},
        )
    # Dragonfly: body at the top, bottom wick dominates.
    if upper_frac <= DEFAULT_DOJI_DRAGONFLY_TOP_WICK_MAX and lower_frac >= 0.50:
        return PatternEvent(
            name=Pattern.DRAGONFLY_DOJI, bar=curr,
            extras={"body_range_ratio": body_frac, "lower_wick_frac": lower_frac},
        )
    # Generic doji — small body, no specific wick concentration.
    return PatternEvent(
        name=Pattern.DOJI, bar=curr,
        extras={"body_range_ratio": body_frac, "upper_wick_frac": upper_frac, "lower_wick_frac": lower_frac},
    )


def _marubozu(curr: CandleBar) -> Optional[PatternEvent]:
    if curr.range <= 0 or curr.body <= 0:
        return None
    upper_frac = curr.upper_wick / curr.range
    lower_frac = curr.lower_wick / curr.range
    if upper_frac > DEFAULT_MARUBOZU_WICK_RANGE_MAX or lower_frac > DEFAULT_MARUBOZU_WICK_RANGE_MAX:
        return None
    name = Pattern.BULLISH_MARUBOZU if curr.is_bullish else Pattern.BEARISH_MARUBOZU
    return PatternEvent(
        name=name, bar=curr,
        extras={"body_range_ratio": curr.body / curr.range,
                "upper_wick_frac": upper_frac, "lower_wick_frac": lower_frac},
    )


def _outside_bar(prev: CandleBar, curr: CandleBar) -> Optional[PatternEvent]:
    """High > prev.high AND low < prev.low (current bar engulfs prev range).

    Classified bull/bear by current close direction.  Distinct from
    engulfing — engulfing is BODY containment with opposite color; outside
    bar is RANGE containment, color-agnostic on the prior bar.
    """
    if not (curr.high > prev.high and curr.low < prev.low):
        return None
    if curr.range <= 0:
        return None
    name = Pattern.BULLISH_OUTSIDE_BAR if curr.is_bullish else Pattern.BEARISH_OUTSIDE_BAR
    return PatternEvent(
        name=name, bar=curr,
        extras={"range_ratio": curr.range / max(prev.range, 1e-9)},
    )


def _tweezer(prev: CandleBar, curr: CandleBar) -> Optional[PatternEvent]:
    """Two-bar pattern: equal highs (top) or equal lows (bottom).

    Tweezer top = matching highs after an up-move → reversal signal.
    Tweezer bottom = matching lows after a down-move → reversal signal.
    """
    tol = DEFAULT_TWEEZER_TOL_RANGE_FRAC * max(prev.range, 1e-9)
    if abs(curr.high - prev.high) <= tol and prev.is_bullish and curr.is_bearish:
        return PatternEvent(
            name=Pattern.TWEEZER_TOP, bar=curr,
            extras={"high_diff": abs(curr.high - prev.high), "tolerance": tol},
        )
    if abs(curr.low - prev.low) <= tol and prev.is_bearish and curr.is_bullish:
        return PatternEvent(
            name=Pattern.TWEEZER_BOTTOM, bar=curr,
            extras={"low_diff": abs(curr.low - prev.low), "tolerance": tol},
        )
    return None


def _harami(prev: CandleBar, curr: CandleBar, bullish: bool) -> Optional[PatternEvent]:
    """Small current body inside prior (opposite-color, large) body.

    Bullish harami: prior bearish big body, current small body INSIDE
    the prior body.  Bearish harami: opposite.
    """
    if prev.range <= 0 or prev.body / prev.range < DEFAULT_HARAMI_PRIOR_BODY_RANGE_MIN:
        return None
    if curr.body > DEFAULT_HARAMI_BODY_RATIO_MAX * prev.body:
        return None
    if not (curr.body_top <= prev.body_top and curr.body_bottom >= prev.body_bottom):
        return None
    if bullish:
        if not prev.is_bearish:
            return None
        name = Pattern.BULLISH_HARAMI
    else:
        if not prev.is_bullish:
            return None
        name = Pattern.BEARISH_HARAMI
    return PatternEvent(
        name=name, bar=curr,
        extras={"body_ratio": curr.body / max(prev.body, 1e-9)},
    )


def _piercing_or_dark_cloud(prev: CandleBar, curr: CandleBar) -> Optional[PatternEvent]:
    """Piercing line (bullish reversal) or dark cloud cover (bearish).

    Piercing: prev bearish, curr bullish, curr opens below prev low and
    closes above the 50 % midpoint of prev's body.

    Dark cloud cover: prev bullish, curr bearish, curr opens above prev
    high and closes below the 50 % midpoint of prev's body.
    """
    if prev.body <= 0 or curr.body <= 0:
        return None
    if prev.is_bearish and curr.is_bullish:
        mid = (prev.body_top + prev.body_bottom) / 2.0
        if curr.open < prev.low and curr.close > mid and curr.close < prev.body_top:
            penetration = (curr.close - prev.body_bottom) / max(prev.body, 1e-9)
            return PatternEvent(
                name=Pattern.PIERCING, bar=curr,
                extras={"penetration": penetration},
            )
    if prev.is_bullish and curr.is_bearish:
        mid = (prev.body_top + prev.body_bottom) / 2.0
        if curr.open > prev.high and curr.close < mid and curr.close > prev.body_bottom:
            penetration = (prev.body_top - curr.close) / max(prev.body, 1e-9)
            return PatternEvent(
                name=Pattern.DARK_CLOUD_COVER, bar=curr,
                extras={"penetration": penetration},
            )
    return None


def _star(prev2: CandleBar, prev1: CandleBar, curr: CandleBar) -> Optional[PatternEvent]:
    """Morning star (bullish) or evening star (bearish) — 3-bar reversal.

    Morning star: bear big body → small body (gap-down ok) → bull big
    body closing above the midpoint of bar 1.
    Evening star: mirror — bull → small → bear closing below midpoint.
    """
    if prev2.body <= 0 or curr.body <= 0:
        return None
    star_max = DEFAULT_STAR_MIDDLE_BODY_MAX * min(prev2.body, curr.body)
    if prev1.body > star_max:
        return None
    if prev2.is_bearish and curr.is_bullish:
        mid = (prev2.body_top + prev2.body_bottom) / 2.0
        if curr.close > mid:
            return PatternEvent(
                name=Pattern.MORNING_STAR, bar=curr,
                extras={"prev2_body": prev2.body, "star_body": prev1.body, "curr_body": curr.body},
            )
    if prev2.is_bullish and curr.is_bearish:
        mid = (prev2.body_top + prev2.body_bottom) / 2.0
        if curr.close < mid:
            return PatternEvent(
                name=Pattern.EVENING_STAR, bar=curr,
                extras={"prev2_body": prev2.body, "star_body": prev1.body, "curr_body": curr.body},
            )
    return None


def _three_in_a_row(prev2: CandleBar, prev1: CandleBar, curr: CandleBar, bullish: bool) -> Optional[PatternEvent]:
    """Three-white-soldiers or three-black-crows — 3-bar continuation.

    Bullish: three consecutive bullish bars, each closing higher than
    the prior, each body ≥ DEFAULT_THREE_BAR_MIN_BODY_PROGRESSION × the
    prior's body (no dwindling-momentum tails).

    Bearish: mirror.
    """
    if any(b.body <= 0 for b in (prev2, prev1, curr)):
        return None
    if bullish:
        if not (prev2.is_bullish and prev1.is_bullish and curr.is_bullish):
            return None
        if not (prev1.close > prev2.close and curr.close > prev1.close):
            return None
        if prev1.body < DEFAULT_THREE_BAR_MIN_BODY_PROGRESSION * prev2.body:
            return None
        if curr.body < DEFAULT_THREE_BAR_MIN_BODY_PROGRESSION * prev1.body:
            return None
        name = Pattern.THREE_WHITE_SOLDIERS
    else:
        if not (prev2.is_bearish and prev1.is_bearish and curr.is_bearish):
            return None
        if not (prev1.close < prev2.close and curr.close < prev1.close):
            return None
        if prev1.body < DEFAULT_THREE_BAR_MIN_BODY_PROGRESSION * prev2.body:
            return None
        if curr.body < DEFAULT_THREE_BAR_MIN_BODY_PROGRESSION * prev1.body:
            return None
        name = Pattern.THREE_BLACK_CROWS
    return PatternEvent(
        name=name, bar=curr,
        extras={"total_move": abs(curr.close - prev2.close)},
    )


def detect_patterns(
    history: Sequence[CandleBar],
    *,
    min_history: int = 2,
) -> List[PatternEvent]:
    """Detect all configured patterns that fire on the latest bar.

    Two-bar patterns (engulfing) need ``history[-2]`` and ``history[-1]``;
    one-bar patterns (pin) only need ``history[-1]``.

    Returns an empty list if ``len(history) < min_history``.  The order
    of the returned events is stable across calls.
    """
    out: List[PatternEvent] = []
    if len(history) < min_history:
        return out
    curr = history[-1]
    prev = history[-2] if len(history) >= 2 else None
    prev2 = history[-3] if len(history) >= 3 else None

    # ── single-bar ──────────────────────────────────────────────────
    e = _pin(curr, bullish=True)
    if e:
        out.append(e)
    e = _pin(curr, bullish=False)
    if e:
        out.append(e)
    # Doji + marubozu are mutually exclusive with pin via the body/range
    # gate so we list them all — only the matching one (if any) fires.
    e = _doji_family(curr)
    if e:
        out.append(e)
    e = _marubozu(curr)
    if e:
        out.append(e)

    # ── two-bar ────────────────────────────────────────────────────
    if prev is not None:
        e = _engulfing(prev, curr, bullish=True)
        if e:
            out.append(e)
        e = _engulfing(prev, curr, bullish=False)
        if e:
            out.append(e)
        e = _inside(prev, curr)
        if e:
            out.append(e)
        e = _outside_bar(prev, curr)
        if e:
            out.append(e)
        e = _tweezer(prev, curr)
        if e:
            out.append(e)
        e = _harami(prev, curr, bullish=True)
        if e:
            out.append(e)
        e = _harami(prev, curr, bullish=False)
        if e:
            out.append(e)
        e = _piercing_or_dark_cloud(prev, curr)
        if e:
            out.append(e)

    # ── three-bar ──────────────────────────────────────────────────
    if prev2 is not None and prev is not None:
        e = _star(prev2, prev, curr)
        if e:
            out.append(e)
        e = _three_in_a_row(prev2, prev, curr, bullish=True)
        if e:
            out.append(e)
        e = _three_in_a_row(prev2, prev, curr, bullish=False)
        if e:
            out.append(e)

    return out


# ─────────────────────── probability emitter ────────────────────────


@dataclass
class _PatternTally:
    n: int = 0
    n_up: int = 0  # next bar closed > current bar close
    n_down: int = 0
    sum_ret: float = 0.0  # cumulative (next_close / curr_close - 1)
    sum_ret_squared: float = 0.0  # for std-dev / Sharpe later


class ProbabilityEmitter:
    """Stateful tally that maintains conditional probabilities online.

    Feed bars in chronological order via :meth:`update`.  Each call
    detects patterns on the bar that was previously latest (``self._prev_latest``)
    and credits its outcome to the tally for those patterns.

    Read out via :meth:`snapshot` (returns the live conditional metrics
    per pattern) or :meth:`edge` (returns the per-pattern edge vs the
    unconditional baseline).
    """

    def __init__(self) -> None:
        self._tallies: Dict[str, _PatternTally] = {p: _PatternTally() for p in Pattern.ALL}
        self._baseline = _PatternTally()
        # The bar that fired patterns on the previous call to update().
        # When the next bar arrives we credit those patterns with the
        # observed outcome.
        self._prev_bar: Optional[CandleBar] = None
        self._prev_patterns: List[str] = []
        # 2-bar lookback for engulfing requires holding the previous bar
        # too — managed by the caller via ``update(history=[...])``.

    def update(self, history: Sequence[CandleBar]) -> List[str]:
        """Ingest the next bar (newest at ``history[-1]``).

        Returns the list of pattern names detected on the newest bar
        (these will be credited on the *following* call).
        """
        if len(history) < 1:
            return []
        new_bar = history[-1]
        if self._prev_bar is not None:
            # Credit the previously detected patterns with the outcome
            # observed at ``new_bar``.
            ret = (new_bar.close / max(self._prev_bar.close, 1e-9)) - 1.0
            up = new_bar.close > self._prev_bar.close
            self._baseline.n += 1
            if up:
                self._baseline.n_up += 1
            else:
                self._baseline.n_down += 1
            self._baseline.sum_ret += ret
            self._baseline.sum_ret_squared += ret * ret
            for name in self._prev_patterns:
                t = self._tallies[name]
                t.n += 1
                if up:
                    t.n_up += 1
                else:
                    t.n_down += 1
                t.sum_ret += ret
                t.sum_ret_squared += ret * ret

        # Detect patterns on this bar; defer crediting to the next call.
        events = detect_patterns(history)
        self._prev_bar = new_bar
        self._prev_patterns = [e.name for e in events]
        return self._prev_patterns

    def snapshot(self) -> Dict[str, Dict[str, float]]:
        """Per-pattern live snapshot.  Returns a dict keyed by pattern name."""
        out: Dict[str, Dict[str, float]] = {}
        base = self._baseline
        baseline_p_up = (base.n_up / base.n) if base.n else 0.0
        baseline_mean = (base.sum_ret / base.n) if base.n else 0.0
        out["__baseline__"] = {
            "n": float(base.n),
            "p_up": baseline_p_up,
            "mean_ret": baseline_mean,
        }
        for name in Pattern.ALL:
            t = self._tallies[name]
            if t.n == 0:
                out[name] = {
                    "n": 0.0, "p_up": 0.0, "p_down": 0.0,
                    "mean_ret": 0.0, "edge_p_up": 0.0, "edge_mean_ret": 0.0,
                }
                continue
            p_up = t.n_up / t.n
            p_down = t.n_down / t.n
            mean_ret = t.sum_ret / t.n
            out[name] = {
                "n": float(t.n),
                "p_up": p_up,
                "p_down": p_down,
                "mean_ret": mean_ret,
                "edge_p_up": p_up - baseline_p_up,
                "edge_mean_ret": mean_ret - baseline_mean,
            }
        return out

    def edge(self, name: str) -> Dict[str, float]:
        """Per-pattern edge.  Empty dict if pattern unknown / unseen."""
        snap = self.snapshot()
        return snap.get(name, {})


def detect_all_in_csv(
    bars: Iterable[CandleBar],
) -> Tuple[List[Tuple[CandleBar, List[str]]], ProbabilityEmitter]:
    """Run the emitter over an iterable of bars.

    Returns ``(detections, emitter)`` where ``detections`` is the list
    of ``(bar, patterns_detected_on_that_bar)`` pairs.  Convenient for
    offline / batch validation.
    """
    detections: List[Tuple[CandleBar, List[str]]] = []
    emitter = ProbabilityEmitter()
    window: List[CandleBar] = []
    # Three-bar patterns (morning_star, three_white_soldiers) need a
    # 3-bar lookback.  Keep 5 to give the emitter slack on edge cases.
    for bar in bars:
        window.append(bar)
        if len(window) > 5:
            window.pop(0)
        names = emitter.update(window)
        detections.append((bar, names))
    return detections, emitter
