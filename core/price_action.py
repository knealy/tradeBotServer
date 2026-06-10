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
class Pattern:
    BULLISH_ENGULFING = "bullish_engulfing"
    BEARISH_ENGULFING = "bearish_engulfing"
    INSIDE_BAR = "inside_bar"
    BULLISH_PIN = "bullish_pin"
    BEARISH_PIN = "bearish_pin"

    ALL: Tuple[str, ...] = (
        BULLISH_ENGULFING,
        BEARISH_ENGULFING,
        INSIDE_BAR,
        BULLISH_PIN,
        BEARISH_PIN,
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

    e = _pin(curr, bullish=True)
    if e:
        out.append(e)
    e = _pin(curr, bullish=False)
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
    # Engulfing / inside need 2-bar lookback so we keep a small window.
    for bar in bars:
        window.append(bar)
        if len(window) > 4:
            window.pop(0)
        names = emitter.update(window)
        detections.append((bar, names))
    return detections, emitter
