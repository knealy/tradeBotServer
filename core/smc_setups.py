"""Compound SMC / ICT setup detectors.

Where ``core/market_structure.py`` provides the PRIMITIVES (swings,
FVG, Order Block, Liquidity Sweep), this module provides the
COMPOSITIONS — the multi-primitive confluences that practitioners
actually trade.

Three setups built here:

1. ``sweep_into_fvg`` — a liquidity sweep (stop-hunt) followed by
   price re-entering an unmitigated FVG on the reversal side.  The
   sweep proves stops were run; the FVG provides the high-probability
   entry zone.  Tradeable in either direction.

2. ``fvg_in_ob`` — an FVG whose zone OVERLAPS an unmitigated Order
   Block in the same direction.  Both structural signals agree the
   level matters → trade the first retest.

3. ``choch_then_ob_retest`` — a Change-of-Character event (trend
   flip) followed by the first Order Block formed in the new trend's
   first impulse.  Trade the retest of that OB in the new direction
   — this is the canonical "trend-reversal-entry" SMC setup.

All three emit ``SetupSignal`` events at the bar at which the setup
COMPLETES (price has entered the entry zone and is actionable).  No
look-ahead: every detector consults primitives whose confirmation
index is strictly ≤ the trigger bar.

Simulator integration: ``scripts/simulate_price_action_trades.py``
calls these detectors once over the full bar history, indexes the
signals by bar, and merges them into the per-bar event stream
alongside ordinary patterns.  ``SetupSignal.name`` becomes the
"pattern name" the simulator aggregates by.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Sequence, Tuple

from core.market_structure import (
    FairValueGap,
    LiquiditySweep,
    OrderBlock,
    StructureEvent,
    SwingKind,
    SwingTracker,
    detect_change_of_character,
    find_fair_value_gaps,
    find_liquidity_sweeps,
    find_order_blocks,
    find_swing_pivots,
    is_inside_fvg,
    is_inside_order_block,
)

__all__ = [
    "SetupSignal",
    "find_sweep_into_fvg",
    "find_fvg_in_ob",
    "find_choch_then_ob_retest",
    "find_all_smc_setups",
]


@dataclass(frozen=True)
class SetupSignal:
    """Compound SMC setup — emitted when the multi-primitive confluence
    completes and a trade is actionable.

    ``bar_index`` is the trigger bar — the bar at which all components
    are confirmed and price has entered the entry zone.  Components
    consulted by the detector are stored in ``components`` for audit
    / debugging.

    ``invalidation`` is the suggested stop-loss reference (e.g. the
    far side of the sweep wick for sweep_into_fvg, or the far edge of
    the OB for choch_then_ob_retest).  Strategy code may override.
    """

    name: str                # "sweep_into_fvg" / "fvg_in_ob" / "choch_then_ob_retest"
    direction: int           # +1 long, -1 short
    bar_index: int
    timestamp: Optional[datetime]
    entry_zone_upper: float
    entry_zone_lower: float
    invalidation: float
    components: Dict[str, object] = field(default_factory=dict)


# ─────────────────────── sweep_into_fvg ─────────────────────────────


def find_sweep_into_fvg(
    bars: Sequence,
    *,
    swing_lookback: int = 3,
    max_bars_after_sweep: int = 20,
    sweep_min_poke: float = 0.0,
) -> List[SetupSignal]:
    """Detect "sweep → return into FVG" reversal setups.

    Algorithm:
      1. Find all liquidity sweeps and all FVGs over ``bars``.
      2. For each sweep at bar ``s``:
           - Reversal direction = opposite of sweep direction.
             (Sweep above swing high → SHORT bias.  Sweep below
              swing low → LONG bias.)
           - Find the first subsequent bar ``j`` (within
             ``max_bars_after_sweep`` of ``s``) whose close lies inside
             an unmitigated FVG in the reversal direction.
           - Emit a SetupSignal at ``j`` with entry zone = the FVG.
      3. A single sweep produces at most one signal (the first FVG
         it re-enters); subsequent retests fire only if a new sweep
         occurs.

    ``sweep_min_poke``: minimum sweep pierce distance (points) — pass
    through to ``find_liquidity_sweeps``.
    """
    if len(bars) < 3:
        return []

    def _h(b) -> float: return float(getattr(b, "high"))
    def _l(b) -> float: return float(getattr(b, "low"))
    def _c(b) -> float: return float(getattr(b, "close"))
    def _ts(b): return getattr(b, "timestamp", None)

    swings = find_swing_pivots(bars, lookback=swing_lookback)
    sweeps = find_liquidity_sweeps(
        bars, swings, min_poke_points=sweep_min_poke,
        require_close_back_inside=True,
    )
    fvgs = find_fair_value_gaps(bars)

    signals: List[SetupSignal] = []
    for sw in sweeps:
        # ``LiquiditySweep.direction`` IS the suggested trade direction:
        #   +1 = swept the low (long-bias reversal)
        #   -1 = swept the high (short-bias reversal)
        # So we use it directly — no negation.
        trade_dir = sw.direction

        # Pre-filter FVGs to those:
        #   - matching trade direction
        #   - formed at or before the sweep bar
        #   - NOT mitigated BEFORE the sweep (a sweep that lands in an
        #     FVG IS the trade entry — the sweep-bar mitigation is
        #     intended, not disqualifying)
        candidates: List[FairValueGap] = []
        for fvg in fvgs:
            if fvg.direction != trade_dir:
                continue
            if fvg.formation_index > sw.bar_index:
                continue
            if fvg.mitigated and fvg.mitigation_index is not None and fvg.mitigation_index < sw.bar_index:
                continue
            candidates.append(fvg)
        if not candidates:
            continue

        # First bar in [sweep_bar, sweep_bar + max_bars_after_sweep]
        # whose CLOSE lies inside any candidate FVG.  Includes the
        # sweep bar itself (j = sw.bar_index) so a sweep+return-in-the-
        # same-bar fires immediately.
        end_idx = min(sw.bar_index + max_bars_after_sweep, len(bars) - 1)
        emitted = False
        for j in range(sw.bar_index, end_idx + 1):
            px = _c(bars[j])
            for fvg in candidates:
                if fvg.contains(px):
                    if trade_dir == +1:
                        invalidation = min(_l(bars[sw.bar_index]) - 0.5, fvg.lower - 0.5)
                    else:
                        invalidation = max(_h(bars[sw.bar_index]) + 0.5, fvg.upper + 0.5)
                    signals.append(SetupSignal(
                        name="sweep_into_fvg",
                        direction=trade_dir,
                        bar_index=j,
                        timestamp=_ts(bars[j]),
                        entry_zone_upper=fvg.upper,
                        entry_zone_lower=fvg.lower,
                        invalidation=invalidation,
                        components={"sweep": sw, "fvg": fvg},
                    ))
                    emitted = True
                    break
            if emitted:
                break
    return signals


# ─────────────────────── fvg_in_ob ───────────────────────────────────


def _zones_overlap(a_lo: float, a_hi: float, b_lo: float, b_hi: float) -> bool:
    """Standard interval overlap test (inclusive)."""
    return a_lo <= b_hi and b_lo <= a_hi


def find_fvg_in_ob(
    bars: Sequence,
    *,
    impulse_threshold_atr: float = 2.0,
    window: int = 5,
    atr_period: int = 14,
) -> List[SetupSignal]:
    """Detect "FVG inside Order Block" double-confluence setups.

    Algorithm:
      1. Compute all OBs and all FVGs.
      2. For each FVG, find any OB that:
           - has confirmation_index ≤ FVG's formation_index (no look-ahead)
           - is unmitigated as of the FVG's formation
           - has direction equal to the FVG's direction
           - has a zone that OVERLAPS the FVG's zone.
      3. When price first enters the OVERLAP region (after the FVG
         is formed), emit a SetupSignal.

    The signal direction = the OB / FVG direction.  Entry zone is the
    overlap (most conservative); invalidation is the OB's far edge.
    """
    def _h(b) -> float: return float(getattr(b, "high"))
    def _l(b) -> float: return float(getattr(b, "low"))
    def _c(b) -> float: return float(getattr(b, "close"))
    def _ts(b): return getattr(b, "timestamp", None)

    obs = find_order_blocks(
        bars, impulse_threshold_atr=impulse_threshold_atr,
        window=window, atr_period=atr_period,
    )
    fvgs = find_fair_value_gaps(bars)

    # Index OBs by direction for cheap filtering.
    obs_by_dir: Dict[int, List[OrderBlock]] = {+1: [], -1: []}
    for ob in obs:
        obs_by_dir[ob.direction].append(ob)

    signals: List[SetupSignal] = []
    used_keys: set = set()
    for fvg in fvgs:
        candidates = obs_by_dir.get(fvg.direction, [])
        for ob in candidates:
            if ob.confirmation_index > fvg.formation_index:
                continue  # OB not yet observable when FVG formed
            if ob.mitigated and ob.mitigation_index is not None and ob.mitigation_index <= fvg.formation_index:
                continue  # OB already invalidated before FVG existed
            if not _zones_overlap(fvg.lower, fvg.upper, ob.lower, ob.upper):
                continue
            # Overlap zone.
            overlap_lo = max(fvg.lower, ob.lower)
            overlap_hi = min(fvg.upper, ob.upper)
            invalidation = ob.lower - 0.5 if fvg.direction == +1 else ob.upper + 0.5
            # First bar AFTER FVG formation whose close enters overlap.
            # Guard against firing on bars where the FVG or OB was
            # mitigated EARLIER — those mitigations invalidate the zone.
            for j in range(fvg.formation_index + 1, len(bars)):
                if fvg.mitigated and fvg.mitigation_index is not None and fvg.mitigation_index < j:
                    break
                if ob.mitigated and ob.mitigation_index is not None and ob.mitigation_index < j:
                    break
                px = _c(bars[j])
                if overlap_lo <= px <= overlap_hi:
                    key = (j, fvg.formation_index, ob.formation_index)
                    if key in used_keys:
                        break
                    used_keys.add(key)
                    signals.append(SetupSignal(
                        name="fvg_in_ob",
                        direction=fvg.direction,
                        bar_index=j,
                        timestamp=_ts(bars[j]),
                        entry_zone_upper=overlap_hi,
                        entry_zone_lower=overlap_lo,
                        invalidation=invalidation,
                        components={"fvg": fvg, "ob": ob},
                    ))
                    break  # only first entry per FVG×OB pair
    return signals


# ───────────────────── choch_then_ob_retest ─────────────────────────


def find_choch_then_ob_retest(
    bars: Sequence,
    *,
    swing_lookback: int = 3,
    impulse_threshold_atr: float = 2.0,
    impulse_window: int = 5,
    atr_period: int = 14,
    max_bars_to_retest: int = 50,
) -> List[SetupSignal]:
    """Detect "CHoCH → first-OB retest" trend-reversal setups.

    Algorithm:
      1. Walk bars with a SwingTracker.  On each new pivot, evaluate
         ``detect_change_of_character``.  When CHoCH_UP / CHoCH_DOWN
         fires at bar ``c``, the trend has flipped.
      2. Find the FIRST OB in the NEW trend's direction whose
         confirmation_index is ≥ ``c``.  That's the OB created by the
         new trend's first impulse.
      3. Wait for price to RETEST that OB (close inside the zone)
         within ``max_bars_to_retest`` bars of OB confirmation.
      4. Emit SetupSignal at the retest bar.

    Each CHoCH event produces at most one signal — the first retest of
    the post-CHoCH first OB.  Later retests are ignored (the structural
    edge is at the FIRST retest; subsequent retests are diminishing).
    """
    def _c(b) -> float: return float(getattr(b, "close"))
    def _ts(b): return getattr(b, "timestamp", None)

    obs = find_order_blocks(
        bars, impulse_threshold_atr=impulse_threshold_atr,
        window=impulse_window, atr_period=atr_period,
    )
    # Sort OBs by confirmation_index for fast "first OB after X" lookups.
    obs_sorted = sorted(obs, key=lambda o: o.confirmation_index)

    tracker = SwingTracker(lookback=swing_lookback)
    signals: List[SetupSignal] = []
    consumed_obs: set = set()  # OB ids already used by a signal

    for i, b in enumerate(bars):
        new_pivot = tracker.update(b)
        if new_pivot is None:
            continue
        # Re-evaluate character on every new pivot.
        ev = detect_change_of_character(tracker)
        if ev not in (StructureEvent.CHOCH_UP, StructureEvent.CHOCH_DOWN):
            continue
        choch_dir = +1 if ev == StructureEvent.CHOCH_UP else -1
        # Find the first OB matching direction whose confirmation is at
        # or after `i` and which we haven't already consumed.
        target_ob: Optional[OrderBlock] = None
        for ob in obs_sorted:
            if ob.direction != choch_dir:
                continue
            if ob.confirmation_index < i:
                continue
            if id(ob) in consumed_obs:
                continue
            target_ob = ob
            break
        if target_ob is None:
            continue
        # Wait for retest.
        retest_end = min(target_ob.confirmation_index + max_bars_to_retest, len(bars) - 1)
        for j in range(target_ob.confirmation_index + 1, retest_end + 1):
            if target_ob.lower <= _c(bars[j]) <= target_ob.upper:
                invalidation = (target_ob.lower - 0.5) if choch_dir == +1 else (target_ob.upper + 0.5)
                signals.append(SetupSignal(
                    name="choch_then_ob_retest",
                    direction=choch_dir,
                    bar_index=j,
                    timestamp=_ts(bars[j]),
                    entry_zone_upper=target_ob.upper,
                    entry_zone_lower=target_ob.lower,
                    invalidation=invalidation,
                    components={"choch_event": ev, "choch_bar_index": i, "ob": target_ob},
                ))
                consumed_obs.add(id(target_ob))
                break

    return signals


# ─────────────────────── convenience runner ─────────────────────────


def find_all_smc_setups(
    bars: Sequence,
    *,
    swing_lookback: int = 3,
    sweep_max_bars: int = 20,
    sweep_min_poke: float = 0.0,
    ob_impulse_atr: float = 2.0,
    ob_window: int = 5,
    atr_period: int = 14,
    choch_max_retest_bars: int = 50,
) -> Dict[str, List[SetupSignal]]:
    """One-call runner for all three compounds.  Returns a dict keyed
    by setup name."""
    return {
        "sweep_into_fvg": find_sweep_into_fvg(
            bars, swing_lookback=swing_lookback,
            max_bars_after_sweep=sweep_max_bars,
            sweep_min_poke=sweep_min_poke,
        ),
        "fvg_in_ob": find_fvg_in_ob(
            bars, impulse_threshold_atr=ob_impulse_atr,
            window=ob_window, atr_period=atr_period,
        ),
        "choch_then_ob_retest": find_choch_then_ob_retest(
            bars, swing_lookback=swing_lookback,
            impulse_threshold_atr=ob_impulse_atr,
            impulse_window=ob_window, atr_period=atr_period,
            max_bars_to_retest=choch_max_retest_bars,
        ),
    }
