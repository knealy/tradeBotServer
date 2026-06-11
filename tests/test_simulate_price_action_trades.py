"""Tests for ``scripts/simulate_price_action_trades.py`` exit mechanics.

Focused on the adaptive-exit additions: trailing stop + partial profit
+ stress-test matrix.  The baseline fixed-TP path is tested implicitly
via the broader integration runs.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

# Load the script as a module so we can call its private helpers.
_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "simulate_price_action_trades.py"
spec = importlib.util.spec_from_file_location("simulate_pa", _SCRIPT)
simulate_pa = importlib.util.module_from_spec(spec)
sys.modules["simulate_pa"] = simulate_pa
spec.loader.exec_module(simulate_pa)

_simulate_trade = simulate_pa._simulate_trade
_simulate_trade_truth = simulate_pa._simulate_trade_truth
CandleBar = simulate_pa.CandleBar


T0 = datetime(2026, 6, 10, 9, 30)


def _bar(open_, high, low, close, offset: int = 0) -> CandleBar:
    return CandleBar(
        timestamp=T0 + timedelta(minutes=offset * 5),
        open=open_, high=high, low=low, close=close, volume=100.0,
    )


# ─────────────────────── baseline (fixed TP) ────────────────────────


def test_fixed_tp_long_take_profit():
    bars = [_bar(100, 101, 99, 100, 0)]
    # 5 forward bars; TP is +2 points at 102.  Bar 3 high reaches 102.
    bars += [_bar(100, 100.5, 99.5, 100, 1),
              _bar(100, 101.5, 99.5, 101, 2),
              _bar(101, 102.5, 100.5, 102, 3),  # tp hit
              _bar(102, 103, 101, 102, 4)]
    r, reason, held = _simulate_trade(bars, 0, side=+1, stop_dist=1.0, tp_dist=2.0, max_bars=10)
    assert reason == "take_profit"
    assert r == pytest.approx(2.0)
    assert held == 3


def test_fixed_tp_long_stop_loss():
    bars = [_bar(100, 101, 99, 100, 0),
            _bar(100, 100, 98.9, 99, 1)]   # bar 1 low pierces SL (entry-1=99)
    r, reason, _ = _simulate_trade(bars, 0, side=+1, stop_dist=1.0, tp_dist=2.0, max_bars=10)
    assert reason == "stop_loss"
    assert r == pytest.approx(-1.0)


def test_fixed_tp_timed_exit_partial_pnl():
    # Trade runs to end without hitting SL or TP.
    bars = [_bar(100, 100.5, 99.5, 100, 0)]
    bars += [_bar(100, 101, 99.5, 100.5, i) for i in range(1, 6)]
    r, reason, held = _simulate_trade(bars, 0, side=+1, stop_dist=1.0, tp_dist=2.0, max_bars=5)
    assert reason == "timed_exit"
    # Final close = 100.5; entry = 100; r_pnl = 0.5
    assert r == pytest.approx(0.5)
    assert held == 5


# ─────────────────────── trailing stop ──────────────────────────────


def test_trailing_activates_after_trigger_mfe():
    """Verify trailing stop activates after MFE >= trail_trigger_r * stop_dist."""
    # Entry at 100, stop_dist=1.0 (SL at 99), trail at 0.5pt after 1R MFE.
    # Trade should run up: bar 1 high=102 (MFE >= 2R → trailing on),
    # close=101.5 → trailing SL = 101.5 - 0.5 = 101.0.
    # Bar 2 drops to low=100.9 — TRIGGERS trailing SL at 101.0 (sl_hit).
    bars = [_bar(100, 100.5, 99.5, 100, 0),
            _bar(100, 102, 99.5, 101.5, 1),   # MFE = 2 (2R), trailing activates
            _bar(101.5, 101.7, 100.9, 101, 2),  # low = 100.9 hits sl=101.0
            _bar(101, 102, 100.8, 101.5, 3)]
    r, reason, held = _simulate_trade(
        bars, 0, side=+1, stop_dist=1.0, tp_dist=5.0, max_bars=10,
        trail_atr_dist=0.5, trail_trigger_r=1.0,
    )
    # Trailing locked in 101.0; (101.0 - 100.0) / 1.0 = +1.0 R
    assert reason == "trailed_out"
    assert r == pytest.approx(1.0)
    assert held == 2


def test_trailing_does_not_activate_below_trigger():
    """If MFE never reaches trigger, trailing should not activate and
    the trade exits via normal SL/TP/timed mechanism."""
    bars = [_bar(100, 100.2, 99.5, 100, 0),
            _bar(100, 100.4, 99.5, 100.2, 1),  # MFE=0.4 (0.4R), below trigger=1R
            _bar(100.2, 100.5, 99.5, 100.3, 2),
            _bar(100.3, 100.4, 99.5, 100.2, 3)]
    r, reason, _ = _simulate_trade(
        bars, 0, side=+1, stop_dist=1.0, tp_dist=2.0, max_bars=10,
        trail_atr_dist=0.5, trail_trigger_r=1.0,
    )
    # No trailing, no SL hit, no TP hit → timed exit at final close 100.2
    assert reason == "timed_exit"
    assert r == pytest.approx(0.2)


def test_trailing_only_moves_favorably():
    """Trailing SL must never move AGAINST the position (e.g., after a
    pullback the SL shouldn't drop back down)."""
    # Entry 100, SL 99.  bar1 hits 102 (2R MFE), close=101.5 →
    # trail_sl = 101 (101.5 - 0.5).  bar2 drops to close=100.5 →
    # would naively be trail_sl = 100 (100.5 - 0.5) BUT must stay at 101.
    # bar3 then pierces 100.5 — should NOT trigger 100 SL; should
    # trigger 101 SL.
    bars = [_bar(100, 100.5, 99.5, 100, 0),
            _bar(100, 102, 99.5, 101.5, 1),    # trail SL set to 101.0
            _bar(101.5, 101.6, 100.5, 100.7, 2),  # close=100.7 → would set SL=100.2; but kept at 101 → SL hit
            _bar(100.7, 100.8, 99.0, 99.5, 3)]
    r, reason, held = _simulate_trade(
        bars, 0, side=+1, stop_dist=1.0, tp_dist=5.0, max_bars=10,
        trail_atr_dist=0.5, trail_trigger_r=1.0,
    )
    # SL at 101.0 was hit on bar 2 (low=100.5).
    assert reason == "trailed_out"
    assert r == pytest.approx(1.0)
    assert held == 2


# ─────────────────────── partial profit ─────────────────────────────


def test_partial_profit_at_1r_then_breakeven():
    """Close 50% at 1R, runner at BE.  Bar 2 hits the partial level
    (close runs to 101.5 with high=102 = +2R MFE).  Then bar 3 drops
    to entry (100) - SL hits BE → runner exits at 0.  Total = 1*0.5 + 0*0.5 = 0.5R."""
    bars = [_bar(100, 100.5, 99.5, 100, 0),
            _bar(100, 100.8, 99.7, 100.5, 1),  # below partial 1R level (101)
            _bar(100.5, 102, 100, 101.5, 2),   # hits partial at 101 → half closed; SL → 100 (BE)
            _bar(101.5, 101.7, 99.5, 100, 3),  # bar3 low=99.5 hits BE sl=100; runner = 0R
            _bar(100, 100, 99, 99.5, 4)]
    r, reason, held = _simulate_trade(
        bars, 0, side=+1, stop_dist=1.0, tp_dist=5.0, max_bars=10,
        partial_tp_r=1.0, partial_frac=0.5,
    )
    assert r == pytest.approx(0.5)
    # Runner exited at BE (sl_px == entry), so r_runner = 0; reason
    # should be "breakeven_out".
    assert reason == "breakeven_out"
    assert held == 3


def test_partial_profit_runner_to_tp():
    """Close 50% at 1R; runner runs to full TP (2R).  Total = 1*0.5 + 2*0.5 = 1.5R."""
    bars = [_bar(100, 100.5, 99.5, 100, 0),
            _bar(100, 100.8, 99.7, 100.5, 1),
            _bar(100.5, 102.5, 100, 102, 2),  # bar2 high=102.5 hits TP=102 (entry+2)
            _bar(102, 103, 101.5, 102.5, 3)]
    r, reason, _ = _simulate_trade(
        bars, 0, side=+1, stop_dist=1.0, tp_dist=2.0, max_bars=10,
        partial_tp_r=1.0, partial_frac=0.5,
    )
    # Bar 2 hits partial (high=102.5 >= 101) → partial 0.5R; SL→100.
    # Bar 2 ALSO hits TP (high>=102 in same bar).  Algorithm checks partial
    # before SL/TP, so partial fires.  Then TP check fires same bar →
    # runner gets +2R; total = 1*0.5 + 2*0.5 = 1.5R.
    assert reason == "take_profit"
    assert r == pytest.approx(1.5)


# ─────────────────────── partial + trail combined ───────────────────


def test_partial_and_trailing_combined():
    """Both modes on: partial fires at 1R, then trailing kicks in for the runner."""
    bars = [_bar(100, 100.5, 99.5, 100, 0),
            _bar(100, 100.8, 99.7, 100.5, 1),
            _bar(100.5, 102, 100, 101.5, 2),   # partial at 101 → 0.5R, sl_px = entry=100; trailing activates (MFE=2 >= 1R), trail sl_px=101
            _bar(101.5, 101.7, 100.5, 100.7, 3),  # low=100.5 hits trail sl=101 → runner = +1R
            _bar(100.7, 100.8, 100, 100.5, 4)]
    r, reason, _ = _simulate_trade(
        bars, 0, side=+1, stop_dist=1.0, tp_dist=5.0, max_bars=10,
        trail_atr_dist=0.5, trail_trigger_r=1.0,
        partial_tp_r=1.0, partial_frac=0.5,
    )
    # Partial 0.5 * 1.0 = 0.5R, runner 0.5 * 1.0 = 0.5R, total = 1.0R
    assert r == pytest.approx(1.0)
    assert reason == "trailed_out"


# ─────────────────────── truth-mode (engine-accurate) ──────────────────
#
# These tests pin the THREE engine-mirror effects that make truth-mode
# diverge from the legacy simulator's optimism:
#
#   1. Entry at next-bar OPEN (+ slip) — not pattern close
#   2. Stop gap-through clamp — fills at max(stop, bar.open) on gap
#   3. Commission converted to R units and subtracted
#
# Plus the 1m intrabar resolution path that lets TP win over SL within
# the same aggregate bar if 1m order is favorable.

# Defaults used in most tests: MES geometry.
_MES = dict(
    commission_per_trade=5.0, slippage_ticks=0.5,
    tick_size=0.25, point_value=5.0,
)


def test_truth_long_take_profit_includes_slip_and_commission():
    """SHORT-form: entry at next-bar open + slip, TP fills clean at tp_px.

    Entry at next bar open (=100) + slip (0.125) = 100.125.  TP at +2pt:
    target = 102.125.  Bar 3 high reaches 102.5 (> 102.125 → TP fills at
    102.125).  R = (102.125 - 100.125) / 1.0 = +2.0 (gross), then
    commission_r = $5 / ($1 × $5/pt) = $5 / $5 = 1.0 R.  Net = +1.0 R.
    """
    bars = [_bar(100, 101, 99, 100, 0),       # pattern bar (entry signal here)
            _bar(100, 100.5, 99.5, 100, 1),   # entry bar — fill at open=100 + slip
            _bar(100, 100.5, 99.5, 100, 2),
            _bar(100, 102.5, 100, 102, 3),    # TP hit (high=102.5 >= 102.125)
            _bar(102, 103, 101, 102, 4)]
    r, reason, held = _simulate_trade_truth(
        bars, 0, side=+1, stop_dist=1.0, tp_dist=2.0, max_bars=10,
        **_MES,
    )
    assert reason == "take_profit"
    # Gross +2R minus 1R commission cost (because the geometry is tight:
    # $1 stop × $5/pt = $5 risk per contract; $5 commission = 1R).
    # In practice strategies use wider stops; this is a STRESS test of
    # the cost model itself.
    assert r == pytest.approx(1.0)
    assert held == 3


def test_truth_short_stop_gap_through_penalty():
    """SHORT trade.  Entry at next-bar open=100 - slip = 99.875.
    SL at entry + stop_dist = 99.875 + 4 = 103.875.  Bar 2 GAPS up
    through SL (open=105, high=106).  Engine fill = max(sl_px, bar.open)
    + slip = max(103.875, 105) + 0.125 = 105.125.  Loss = (99.875 -
    105.125) / 4.0 = -1.3125 R, MINUS commission_r = $5 / ($4 × $5) =
    $5 / $20 = 0.25 R.  Net = -1.5625 R.

    This is the engine's "median R of -1.146 < -1.0" smoking gun.
    """
    bars = [_bar(100, 101, 99, 100, 0),       # pattern bar
            _bar(100, 101, 99, 100, 1),       # entry bar — fill SHORT at 99.875
            _bar(100, 106, 99, 105, 2),       # gap-through: open=100 wait...
            _bar(105, 106, 104, 105, 3)]
    # Hmm the gap-through needs open ABOVE sl_px=103.875 for the clamp
    # to bite.  Restructure: bar 2 opens at 105 (gap up), so SHORT SL
    # at 103.875 fills at max(103.875, 105) + slip = 105.125.
    bars = [_bar(100, 101, 99, 100, 0),
            _bar(100, 101, 99, 100, 1),       # entry SHORT @ 100 - 0.125 = 99.875; SL = 103.875
            _bar(105, 106, 104.5, 105, 2)]    # gap up open=105 → SL fills at 105.125
    r, reason, _ = _simulate_trade_truth(
        bars, 0, side=-1, stop_dist=4.0, tp_dist=8.0, max_bars=10,
        **_MES,
    )
    assert reason == "stop_loss"
    # Gross R = (99.875 - 105.125) / 4.0 = -1.3125
    # Commission R = 5 / (4 × 5) = 0.25
    # Net = -1.5625
    assert r == pytest.approx(-1.5625)


def test_truth_no_gap_clean_stop_minus_slip_minus_commission():
    """LONG, bar 2 LOW pierces SL but bar 2 OPEN is above SL — clean
    stop fill at sl_px - slip.

    Entry at next-bar open=100 + slip = 100.125.  SL = 100.125 - 4 = 96.125.
    Bar 2 opens at 99 (above SL), low=95 (pierces SL).  Engine fill =
    min(sl_px, bar.open) - slip (for LONG-SL is a SELL stop) =
    min(96.125, 99) - 0.125 = 96.0.  Gross R = (96.0 - 100.125) / 4.0 =
    -1.03125.  Commission R = 0.25.  Net = -1.28125.
    """
    bars = [_bar(100, 101, 99, 100, 0),
            _bar(100, 100.5, 99.5, 100, 1),   # entry LONG @ 100.125
            _bar(99, 99.5, 95, 96, 2)]        # low=95 pierces SL=96.125
    r, reason, _ = _simulate_trade_truth(
        bars, 0, side=+1, stop_dist=4.0, tp_dist=8.0, max_bars=10,
        **_MES,
    )
    assert reason == "stop_loss"
    assert r == pytest.approx(-1.28125)


def test_truth_intrabar_tp_wins_over_sl_when_1m_data_available():
    """Same aggregate bar contains BOTH SL+TP touches.  Without intrabar
    data the conservative tie-break (SL wins) applies; WITH 1m data
    showing TP touched first, TP wins.

    Setup: LONG entry at 100.125, SL=96.125, TP=108.125.  Bar 2
    aggregate: high=110, low=95 → both SL and TP hit.  1m series shows
    minute-by-minute: TP touched at minute 2 (high=110), SL not
    touched until minute 8 (low=95)."""
    agg_bars = [_bar(100, 101, 99, 100, 0),
                _bar(100, 100.5, 99.5, 100, 1),
                _bar(99, 110, 95, 100, 2),  # both extremes — SL wins without 1m
                _bar(100, 100, 99, 99, 3)]
    # Without intrabar → SL fills (conservative tie-break).
    r_no_intra, reason_no_intra, _ = _simulate_trade_truth(
        agg_bars, 0, side=+1, stop_dist=4.0, tp_dist=8.0, max_bars=10,
        **_MES,
    )
    assert reason_no_intra == "stop_loss"

    # With intrabar: TP touched first (minute 2).
    # Build 1m sub-bars covering bar 2's 5m window [09:40, 09:45)
    bar2_open = agg_bars[2].timestamp
    m_bars = []
    for k in range(5):
        ts = bar2_open + timedelta(minutes=k)
        if k == 2:
            # TP touch at minute 2 — high=110, low=99.5 (no SL touch)
            m_bars.append(CandleBar(timestamp=ts, open=100, high=110, low=99.5,
                                     close=109, volume=10))
        elif k == 4:
            # SL touch at minute 4 — high=109, low=95 (after TP already filled)
            m_bars.append(CandleBar(timestamp=ts, open=109, high=109, low=95,
                                     close=99, volume=10))
        else:
            m_bars.append(CandleBar(timestamp=ts, open=100, high=100.5,
                                     low=99.5, close=100, volume=10))
    bars_1m_by_ns = {int(b.timestamp.timestamp() * 1e9): b for b in m_bars}
    r_intra, reason_intra, _ = _simulate_trade_truth(
        agg_bars, 0, side=+1, stop_dist=4.0, tp_dist=8.0, max_bars=10,
        bars_1m_by_ns=bars_1m_by_ns, agg_minutes=5,
        **_MES,
    )
    assert reason_intra == "take_profit"
    # Gross R = (108.125 - 100.125) / 4.0 = 2.0; commission_r = 0.25
    assert r_intra == pytest.approx(1.75)


def test_truth_no_next_bar_returns_invalid():
    """If the entry bar is the last bar, there's no NEXT bar to fill
    the MARKET order against — return invalid (mirrors engine behaviour
    where the order would sit pending until cancelled)."""
    bars = [_bar(100, 101, 99, 100, 0)]  # only one bar
    r, reason, held = _simulate_trade_truth(
        bars, 0, side=+1, stop_dist=1.0, tp_dist=2.0, max_bars=5,
        **_MES,
    )
    assert reason == "invalid"
    assert r == pytest.approx(0.0)


def test_truth_commission_scales_inverse_with_stop_dist():
    """Wider stops dilute commission's per-R impact.  Same trade with
    stop=1pt → 1R commission; stop=10pt → 0.1R commission.

    Concrete arithmetic for the LONG case (entry next-bar open + slip):
      - entry_px = 100 + 0.125 = 100.125
      - stop=1: sl_px = 99.125. Bar 2 opens at 99 (< sl_px) →
        fill = min(99.125, 99) - slip = 98.875.
        Gross R = (98.875 - 100.125) / 1.0 = -1.25.
        commission_r = $5 / ($1 × $5) = 1.0.  Net = -2.25.
      - stop=10: sl_px = 90.125. Bar 2 opens at 99 (>> sl_px) →
        clean fill at min(90.125, 99) - slip = 90.0.
        Gross R = (90.0 - 100.125) / 10.0 = -1.0125.
        commission_r = $5 / ($10 × $5) = 0.1.  Net = -1.1125.
      - Delta = -1.1125 - (-2.25) = +1.1375 (wider stop → less negative R).

    This pins both the commission scaling AND the engine's gap-through
    behaviour (tighter stops on a hostile next-bar open get DOUBLY
    penalised: by gap fill AND by commission/R ratio).
    """
    bars_template = lambda stop_dist: [
        _bar(100, 101, 99, 100, 0),
        _bar(100, 100.5, 99.5, 100, 1),  # entry at 100.125
        _bar(99, 99.5, 100.125 - stop_dist - 0.5, 99, 2),  # gap-pierce SL
    ]

    r1, reason1, _ = _simulate_trade_truth(
        bars_template(1.0), 0, side=+1, stop_dist=1.0, tp_dist=2.0, max_bars=5,
        **_MES,
    )
    r10, reason10, _ = _simulate_trade_truth(
        bars_template(10.0), 0, side=+1, stop_dist=10.0, tp_dist=20.0, max_bars=5,
        **_MES,
    )
    assert reason1 == "stop_loss"
    assert reason10 == "stop_loss"
    assert r1 == pytest.approx(-2.25, abs=0.001)
    assert r10 == pytest.approx(-1.1125, abs=0.001)
    assert (r10 - r1) == pytest.approx(1.1375, abs=0.001)


def test_truth_force_flat_at_16_et_closes_position_at_bar_open():
    """When ``force_flat_et_minutes`` is set, a position held at/past the
    cutoff ET wall-clock should market-exit at THAT bar's open (with
    appropriate slip).  Mirrors engine ``_maybe_replay_force_flat_et``.
    """
    from datetime import timezone as _tz
    try:
        from zoneinfo import ZoneInfo
        ET = ZoneInfo("America/New_York")
    except Exception:
        pytest.skip("zoneinfo unavailable")

    # Build bars timestamped at ET 15:55, 16:00, 16:05 (UTC=20:55, 21:00, 21:05 standard).
    # The 16:00 ET bar must trigger the force-flat.
    base_et = datetime(2026, 6, 10, 15, 55, tzinfo=ET)
    def _bar_et(open_, high, low, close, off):
        return CandleBar(
            timestamp=(base_et + timedelta(minutes=off * 5)).astimezone(_tz.utc),
            open=open_, high=high, low=low, close=close, volume=100,
        )

    bars = [_bar_et(100, 101, 99, 100, 0),    # 15:55 ET — pattern bar
            _bar_et(100, 100.5, 99.5, 100, 1),  # 16:00 ET — entry bar, force-flat trigger
            _bar_et(99, 99.5, 95, 95, 2)]     # 16:05 ET — would-be SL hit (95 << SL=96)
    # Force flat at 16:00 ET = 960 minutes.  Entry bar IS at 16:00 ET → the
    # force-flat check fires on this bar BEFORE checking SL/TP fills, so
    # the position exits at the entry bar's open with slip.
    # Hmm but the entry bar is start_idx — the check at j=start_idx will fire.
    r, reason, held = _simulate_trade_truth(
        bars, 0, side=+1, stop_dist=4.0, tp_dist=8.0, max_bars=5,
        force_flat_et_minutes=960,  # 16:00 ET
        **_MES,
    )
    assert reason == "replay_force_flat_et"
    # Entry at bar 1 open + slip = 100 + 0.125 = 100.125.
    # Force flat at bar 1 (16:00 ET) open - slip = 100 - 0.125 = 99.875.
    # Gross R = (99.875 - 100.125) / 4.0 = -0.0625.  Commission_r = 0.25.
    # Net = -0.3125.
    assert r == pytest.approx(-0.3125, abs=0.001)
    assert held == 1


def test_truth_force_flat_disabled_runs_to_max_bars():
    """With force_flat_et_minutes=None the original timed-exit behaviour applies."""
    bars = [_bar(100, 101, 99, 100, 0)]
    bars += [_bar(100, 100.5, 99.5, 100, i) for i in range(1, 6)]
    r, reason, _ = _simulate_trade_truth(
        bars, 0, side=+1, stop_dist=4.0, tp_dist=8.0, max_bars=5,
        force_flat_et_minutes=None,
        **_MES,
    )
    assert reason == "timed_exit"
