"""Tests for ``core.smc_setups`` — compound SMC setup detectors."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import List

import pytest

from core.smc_setups import (
    SetupSignal,
    find_all_smc_setups,
    find_choch_then_ob_retest,
    find_fvg_in_ob,
    find_sweep_into_fvg,
)


class _Bar:
    """Minimal bar shim — same shape as tests/test_market_structure.py."""

    def __init__(self, high: float, low: float, *, ts=None,
                 close: float = None, open: float = None):
        self.high = high
        self.low = low
        self.timestamp = ts or datetime(2026, 6, 10, 9, 30)
        self.close = close if close is not None else (high + low) / 2
        self.open = open if open is not None else self.close


def _build_bullish_sweep_fixture() -> List[_Bar]:
    """Build a fixture with: clear swing low → sweep below → FVG above.

    Structure:
      - Bars 0-4:  swing-low forms at idx 2 (low = 90)
      - Bars 5-8:  uptrend
      - Bars 9-11: BULLISH FVG forms (bar 9.high < bar 11.low gap)
      - Bars 12-14: pullback
      - Bar 15: sweep below the swing low (low pokes < 90, close back > 90)
      - Bars 16+: price re-enters the bullish FVG zone for the long trade
    """
    bars: List[_Bar] = []
    # 0-4: descend then form swing low
    bars.append(_Bar(110, 105, close=106))
    bars.append(_Bar(108, 100, close=101))
    bars.append(_Bar(102, 90, close=92))     # swing low candidate (lookback=2)
    bars.append(_Bar(105, 95, close=104))
    bars.append(_Bar(108, 100, close=107))
    # 5-8: uptrend (no new swings)
    bars.append(_Bar(110, 105, close=109))
    bars.append(_Bar(112, 108, close=111))
    bars.append(_Bar(115, 110, close=114))
    bars.append(_Bar(118, 113, close=117))
    # 9-11: form a bullish FVG between bar 9 high and bar 11 low.
    bars.append(_Bar(120, 118, close=119))   # bar 9: high = 120
    bars.append(_Bar(132, 122, close=130))   # bar 10: impulsive middle
    bars.append(_Bar(135, 125, close=134))   # bar 11: low = 125 → gap [120, 125]
    # 12-14: pullback (low stays > 125, doesn't mitigate FVG yet)
    bars.append(_Bar(133, 128, close=129))
    bars.append(_Bar(130, 126, close=127))
    bars.append(_Bar(128, 126, close=127))
    # 15: sweep below swing low (low < 90, close > 90)
    bars.append(_Bar(125, 88, close=95))
    # 16-17: rally back up — close ENTERS the FVG zone (price drifts up,
    # eventually closes inside [120, 125])
    bars.append(_Bar(110, 92, close=108))
    bars.append(_Bar(125, 105, close=122))    # close = 122 inside [120, 125]
    return bars


# ───────────────────────── sweep_into_fvg ───────────────────────────


def test_sweep_into_fvg_long_signal_emitted():
    bars = _build_bullish_sweep_fixture()
    signals = find_sweep_into_fvg(bars, swing_lookback=2,
                                    max_bars_after_sweep=20,
                                    sweep_min_poke=0.5)
    longs = [s for s in signals if s.direction == +1 and s.name == "sweep_into_fvg"]
    assert len(longs) >= 1
    sig = longs[0]
    # Trigger bar must come AFTER the sweep (sweep at idx 15).
    assert sig.bar_index >= 15
    # Entry zone is a valid FVG sub-range (lower < upper).
    assert sig.entry_zone_lower < sig.entry_zone_upper
    # The trigger bar's close must lie INSIDE the reported entry zone.
    px = bars[sig.bar_index].close
    assert sig.entry_zone_lower <= px <= sig.entry_zone_upper
    # Invalidation should be BELOW the sweep low (sweep low = 88).
    assert sig.invalidation < 88
    # Component metadata is populated.
    assert "sweep" in sig.components
    assert "fvg" in sig.components


def test_sweep_into_fvg_no_signal_when_no_sweep():
    bars = _build_bullish_sweep_fixture()
    # Replace the sweep bar (idx 15) so no LOW-side sweep occurs.
    # New bar has its low above the swing low (90), so no sweep fires.
    bars[15] = _Bar(125, 124, close=124.5)
    signals = find_sweep_into_fvg(bars, swing_lookback=2,
                                    max_bars_after_sweep=20,
                                    sweep_min_poke=0.5)
    # No bullish sweep_into_fvg signal should fire.
    bull = [s for s in signals if s.direction == +1]
    assert bull == []


def test_sweep_into_fvg_no_signal_when_fvg_already_mitigated():
    """If the FVG was visited (mitigated) BEFORE the sweep, the setup
    is invalidated — the gap has already been "filled" and lost its
    magnet status."""
    bars = _build_bullish_sweep_fixture()
    # Insert a mitigation bar between FVG formation and sweep — bar 13
    # already dips to 121 (mitigates the FVG before sweep).
    bars[12] = _Bar(133, 121, close=125)
    signals = find_sweep_into_fvg(bars, swing_lookback=2,
                                    max_bars_after_sweep=20,
                                    sweep_min_poke=0.5)
    # No bullish sweep_into_fvg should fire because the FVG is mitigated
    # before the sweep returns to it.
    assert all(s.bar_index != 17 for s in signals if s.direction == +1)


# ───────────────────────── fvg_in_ob ────────────────────────────────


def _build_fvg_in_ob_fixture() -> List[_Bar]:
    """Construct a sequence where:
      - A bullish OB forms at bar 15: bearish bar before strong up
        impulse over bars 16-20.  OB zone = [98, 102].
      - The impulse itself creates a bullish FVG (bar 15.high=102 <
        bar 17.low=105 → FVG zone [102, 105], no overlap with OB by
        definition since FVG.lower = OB.upper).  Need another FVG
        whose zone overlaps OB.
      - Bar 14 (last of ATR warm-up) has high=101.4 (from the warm-up
        loop), so bar 16's high MUST exceed 101.4 + min wick;
        bar 16 starts at low=105 so the gap is [101.4, 105].  This
        OVERLAPS OB [98, 102] at [101.4, 102].
      - Critical: NO bar between FVG formation (idx 16) and trigger
        bar must drop low enough to mitigate the OB (zone [98, 102])
        OR mitigate the FVG (zone [101.4, 105]) before the trigger.
    """
    bars: List[_Bar] = []
    # 0-14: ATR warm-up (15 bars, small ranges, ATR ≈ 1)
    for i in range(15):
        bars.append(_Bar(100 + i*0.1, 99 + i*0.1, open=99.5 + i*0.1, close=100 + i*0.1))
    # 15: bearish OB candidate; zone [98, 102]
    bars.append(_Bar(102, 98, open=102, close=98.5))
    # 16-20: strong impulse up — never dips below 102 (preserves OB)
    for i in range(5):
        bars.append(_Bar(108 + i*2, 105 + i*2, open=105 + i*2, close=107 + i*2))
    # 21-29: drift in [105, 115] — never dips below 102 (OB intact),
    # never dips below 105 (FVG zone [101.4, 105] still alive)
    for i in range(9):
        bars.append(_Bar(112 + (i % 3), 108 + (i % 3), close=110 + (i % 3)))
    # 30: TRIGGER — close drops cleanly into overlap [101.4, 102].
    # Bar low must be ≥ 101.4 to AVOID mitigating the FVG before close
    # is checked (otherwise the bar both mitigates and triggers in same
    # tick, ambiguous).  Use low=101.5, close=101.7.
    bars.append(_Bar(108, 101.5, close=101.7))
    return bars


def test_fvg_in_ob_signal_when_overlap_exists():
    bars = _build_fvg_in_ob_fixture()
    signals = find_fvg_in_ob(bars, impulse_threshold_atr=2.0, window=5, atr_period=14)
    bull = [s for s in signals if s.direction == +1 and s.name == "fvg_in_ob"]
    # At least one FVG-in-OB signal must fire (the impulse off the OB
    # itself creates an FVG whose zone overlaps the OB top; later
    # retraces fire the signal).
    assert len(bull) >= 1
    sig = bull[0]
    # Entry zone is the OVERLAP between FVG and OB.  Bounds depend on
    # exactly which FVG triggered first; assert structural invariants
    # rather than exact prices.
    assert sig.entry_zone_lower < sig.entry_zone_upper
    # Overlap must lie within both source zones.
    fvg = sig.components["fvg"]
    ob = sig.components["ob"]
    assert sig.entry_zone_lower == max(fvg.lower, ob.lower)
    assert sig.entry_zone_upper == min(fvg.upper, ob.upper)
    # Invalidation is 0.5 below OB's lower edge (long signal).
    assert sig.invalidation == pytest.approx(ob.lower - 0.5)


def test_fvg_in_ob_no_signal_when_no_overlap():
    """Build an FVG far from the OB — the detector must not emit."""
    bars: List[_Bar] = []
    for i in range(15):
        bars.append(_Bar(100 + i*0.1, 99 + i*0.1, open=99.5 + i*0.1, close=100 + i*0.1))
    # Bullish OB zone [98, 102]
    bars.append(_Bar(102, 98, open=102, close=98.5))
    for i in range(5):
        bars.append(_Bar(108 + i, 105 + i, open=105 + i, close=107 + i))
    # FVG far above: [120, 122]
    bars.append(_Bar(118, 115, close=117))
    bars.append(_Bar(120, 119, close=119.5))
    bars.append(_Bar(125, 124, close=124))
    bars.append(_Bar(126, 122, close=124))    # bullish FVG: bar22.high=120 < bar24.low=122
    bars.append(_Bar(125, 122.5, close=123))
    # 26: retrace into FVG only
    bars.append(_Bar(122, 119, close=121))
    signals = find_fvg_in_ob(bars, impulse_threshold_atr=2.0, window=5, atr_period=14)
    # Either no signal, or the signal isn't from this FVG/OB pair (zones don't overlap).
    bull = [s for s in signals if s.direction == +1 and s.name == "fvg_in_ob"]
    # The FVG zone [120, 122] doesn't overlap OB [98, 102], so no fvg_in_ob.
    assert bull == []


# ──────────────────── choch_then_ob_retest ──────────────────────────


def test_choch_then_ob_retest_smoke():
    """Smoke test: detector runs without error on a realistic fixture
    and the API contract holds (returns list of SetupSignal with
    expected fields).  Full multi-condition validation requires very
    specific fixtures so we only assert the structural contract here."""
    bars: List[_Bar] = []
    # Build a downtrend (LH, LL) → reversal up (HL → CHoCH_UP).
    base = 100
    # Downtrend with pivots
    for chunk in range(3):
        peak = base - chunk * 5
        trough = base - 10 - chunk * 5
        bars.append(_Bar(peak, peak - 2, close=peak - 1))
        bars.append(_Bar(peak - 1, peak - 3, close=peak - 2))
        bars.append(_Bar(peak, trough, close=trough + 2))  # pivot low
        bars.append(_Bar(peak - 1, trough + 1, close=peak - 2))
        bars.append(_Bar(peak, peak - 3, close=peak - 1))
    # Reversal: print a higher low (HL)
    for _ in range(3):
        bars.append(_Bar(90, 87, close=88))
        bars.append(_Bar(92, 88, close=91))
        bars.append(_Bar(95, 92, close=94))
    # ATR warm-up + drift up for OB candidate
    for i in range(10):
        bars.append(_Bar(96 + i*0.1, 95 + i*0.1, close=95.5 + i*0.1, open=95 + i*0.1))
    # Bearish bar candidate then strong impulse up
    bars.append(_Bar(100, 96, open=100, close=96.5))
    for i in range(5):
        bars.append(_Bar(106 + i, 103 + i, open=103 + i, close=105 + i))
    # Retest of OB
    bars.append(_Bar(105, 98, close=99))

    signals = find_choch_then_ob_retest(
        bars, swing_lookback=2, impulse_threshold_atr=2.0,
        impulse_window=5, atr_period=14,
    )
    # Structural contract: detector returns a list, each entry has the
    # required fields and consistent semantics.
    assert isinstance(signals, list)
    for s in signals:
        assert s.name == "choch_then_ob_retest"
        assert s.direction in (+1, -1)
        assert s.entry_zone_lower <= s.entry_zone_upper
        # Invalidation must be on the FAR side of the entry zone
        # relative to the trade direction.
        if s.direction == +1:
            assert s.invalidation <= s.entry_zone_lower
        else:
            assert s.invalidation >= s.entry_zone_upper


# ───────────────────────── find_all runner ──────────────────────────


def test_find_all_returns_dict_with_three_keys():
    bars = _build_bullish_sweep_fixture()
    out = find_all_smc_setups(bars, swing_lookback=2, sweep_min_poke=0.5)
    assert set(out.keys()) == {"sweep_into_fvg", "fvg_in_ob", "choch_then_ob_retest"}
    assert isinstance(out["sweep_into_fvg"], list)
    assert isinstance(out["fvg_in_ob"], list)
    assert isinstance(out["choch_then_ob_retest"], list)
