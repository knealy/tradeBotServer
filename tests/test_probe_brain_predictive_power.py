"""Tests for ``scripts/probe_brain_predictive_power.py``.

Pin:
- Confidence bucket boundary semantics.
- Forward-return / sign-agreement accounting on synthetic bars.
- Per-symbol + overall verdict aggregation logic.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.probe_brain_predictive_power import (  # type: ignore
    GROUP_BY_BIAS_BUCKET,
    GROUP_BY_STRUCTURE,
    GROUP_BY_SWEEP,
    BiasGroupStats,
    SymbolResult,
    _bucket_for,
    _group_key_for,
    _overall_verdict,
    _verdict_per_symbol,
    walk_symbol,
)


@dataclass
class _Bar:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 1.0


def _utc(year: int, month: int, day: int, hour: int = 10, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


def _make_bars(closes: List[float], ts0: datetime = None) -> List[_Bar]:
    ts0 = ts0 or _utc(2026, 6, 1)
    out: List[_Bar] = []
    for i, c in enumerate(closes):
        out.append(_Bar(
            timestamp=ts0 + timedelta(minutes=5 * i),
            open=c - 0.5, high=c + 1.0, low=c - 1.0, close=c,
        ))
    return out


# ────────────────── bucket boundaries ──────────────────


class TestBucketFor:
    def test_neutral_low_band(self):
        assert _bucket_for(0.0) == "neutral_low"
        assert _bucket_for(0.10) == "neutral_low"
        assert _bucket_for(0.199) == "neutral_low"

    def test_weak_band(self):
        assert _bucket_for(0.20) == "weak"
        assert _bucket_for(0.30) == "weak"
        assert _bucket_for(0.399) == "weak"

    def test_moderate_band(self):
        assert _bucket_for(0.40) == "moderate"
        assert _bucket_for(0.50) == "moderate"
        assert _bucket_for(0.599) == "moderate"

    def test_strong_band(self):
        assert _bucket_for(0.60) == "strong"
        assert _bucket_for(0.85) == "strong"
        assert _bucket_for(1.00) == "strong"


# ────────────────── walk_symbol mechanics ──────────────────


class TestWalkSymbol:
    def test_too_few_bars_returns_empty(self):
        bars = _make_bars([100, 101, 102])
        result = walk_symbol("MGC", bars, window=10, warmup_bars=50, swing_lookback=3)
        assert result.snapshots == 0
        assert result.groups == {}

    def test_basic_walk_produces_snapshots(self):
        # 80 bars: range(warmup=50, n - 6 = 74) → i ∈ [50, 74) → 24 iterations.
        bars = _make_bars([100 + 0.5 * i for i in range(80)])
        result = walk_symbol("MGC", bars, window=30, warmup_bars=50, swing_lookback=3)
        assert result.snapshots == 24
        # Monotonic uptrend → no swings → mostly neutral.  Just check the
        # plumbing routed something into the groups dict.
        total_in_groups = sum(g.count for g in result.groups.values())
        assert total_in_groups == 24

    def test_forward_return_accounting(self):
        # Stairstep up: bars[i].close = i.  fwd_1 = +1, fwd_3 = +3, fwd_6 = +6
        # for every entry.
        bars = _make_bars([100 + i for i in range(80)])
        result = walk_symbol("MGC", bars, window=30, warmup_bars=50, swing_lookback=3)
        for g in result.groups.values():
            if g.count == 0:
                continue
            assert g.mean_fwd_1 == pytest.approx(1.0, abs=0.01)
            assert g.mean_fwd_3 == pytest.approx(3.0, abs=0.01)
            assert g.mean_fwd_6 == pytest.approx(6.0, abs=0.01)


# ────────────────── group_key_for ──────────────────


class _FakeSnap:
    """Minimal duck-typed snapshot for _group_key_for testing."""
    def __init__(self, **kw):
        self.bias = kw.get("bias", "neutral")
        self.confidence = kw.get("confidence", 0.0)
        self.structure_event = kw.get("structure_event", "none")
        self.recent_sweep_direction = kw.get("recent_sweep_direction", None)


class TestGroupKeyFor:
    def test_default_uses_bias_bucket(self):
        snap = _FakeSnap(bias="bullish", confidence=0.75)
        label, sign = _group_key_for(snap, group_by=GROUP_BY_BIAS_BUCKET)
        assert label == "bullish_strong"
        assert sign == +1

    def test_structure_mode_bos_up(self):
        snap = _FakeSnap(structure_event="bos_up", bias="bearish", confidence=0.30)
        label, sign = _group_key_for(snap, group_by=GROUP_BY_STRUCTURE)
        # group key ignores bias entirely in structure mode
        assert label == "bos_up"
        assert sign == +1

    def test_structure_mode_choch_down(self):
        snap = _FakeSnap(structure_event="choch_down")
        label, sign = _group_key_for(snap, group_by=GROUP_BY_STRUCTURE)
        assert label == "choch_down"
        assert sign == -1

    def test_structure_mode_none_no_direction(self):
        snap = _FakeSnap(structure_event="none")
        label, sign = _group_key_for(snap, group_by=GROUP_BY_STRUCTURE)
        assert label == "none"
        assert sign == 0

    def test_sweep_mode_low_is_long_bias(self):
        snap = _FakeSnap(recent_sweep_direction=+1)
        label, sign = _group_key_for(snap, group_by=GROUP_BY_SWEEP)
        assert label == "sweep_low_fade"
        assert sign == +1

    def test_sweep_mode_high_is_short_bias(self):
        snap = _FakeSnap(recent_sweep_direction=-1)
        label, sign = _group_key_for(snap, group_by=GROUP_BY_SWEEP)
        assert label == "sweep_high_fade"
        assert sign == -1

    def test_sweep_mode_no_sweep_no_direction(self):
        snap = _FakeSnap(recent_sweep_direction=None)
        label, sign = _group_key_for(snap, group_by=GROUP_BY_SWEEP)
        assert label == "no_sweep"
        assert sign == 0


# ────────────────── verdict aggregation by group_by mode ──────────────────


class TestVerdictPerSymbolWithGroupBy:
    def test_structure_mode_combines_bos_groups(self):
        result = SymbolResult(symbol="MGC")
        # bos_up: 20 samples, 13 wins fwd_1 (65 % WR)
        # bos_down: 20 samples, 13 wins fwd_1 (65 % WR)
        # → combined 40 samples, 26 wins, 65 % → SIGNAL
        g_up = BiasGroupStats(label="bos_up")
        g_up.count = 20; g_up.wins_1 = 13
        g_dn = BiasGroupStats(label="bos_down")
        g_dn.count = 20; g_dn.wins_1 = 13
        result.groups["bos_up"] = g_up
        result.groups["bos_down"] = g_dn
        assert _verdict_per_symbol(result, group_by=GROUP_BY_STRUCTURE) == "SIGNAL"

    def test_sweep_mode_combines_sweep_groups(self):
        result = SymbolResult(symbol="MGC")
        # Combined 40 samples, 22 wins (55 %) → SIGNAL boundary
        g_lo = BiasGroupStats(label="sweep_low_fade")
        g_lo.count = 20; g_lo.wins_1 = 11
        g_hi = BiasGroupStats(label="sweep_high_fade")
        g_hi.count = 20; g_hi.wins_1 = 11
        result.groups["sweep_low_fade"] = g_lo
        result.groups["sweep_high_fade"] = g_hi
        assert _verdict_per_symbol(result, group_by=GROUP_BY_SWEEP) == "SIGNAL"

    def test_structure_mode_ignores_bias_buckets(self):
        """If structure groups are empty but bias buckets aren't, structure-
        mode verdict must be INSUFFICIENT_N (it doesn't peek at bias buckets)."""
        result = SymbolResult(symbol="MGC")
        result.groups["bullish_strong"] = _grp("bullish_strong", count=40, wins_1=40)
        assert _verdict_per_symbol(result, group_by=GROUP_BY_STRUCTURE) == "INSUFFICIENT_N"


# ────────────────── verdict aggregation ──────────────────


def _grp(label: str, count: int, *, wins_1: int = 0, wins_3: int = 0, wins_6: int = 0) -> BiasGroupStats:
    g = BiasGroupStats(label=label)
    g.count = count
    g.wins_1 = wins_1
    g.wins_3 = wins_3
    g.wins_6 = wins_6
    return g


class TestPerSymbolVerdict:
    def test_insufficient_n_when_strong_count_small(self):
        result = SymbolResult(symbol="MGC")
        result.groups["bullish_strong"] = _grp("bullish_strong", count=5, wins_1=5)
        result.groups["bearish_strong"] = _grp("bearish_strong", count=5, wins_1=5)
        # 10 < min_n=30 default
        assert _verdict_per_symbol(result) == "INSUFFICIENT_N"

    def test_signal_when_strong_bucket_wr_above_55(self):
        result = SymbolResult(symbol="MGC")
        # 20 + 20 = 40 samples, 30 wins → 75 % WR on fwd_1
        result.groups["bullish_strong"] = _grp("bullish_strong", count=20, wins_1=15)
        result.groups["bearish_strong"] = _grp("bearish_strong", count=20, wins_1=15)
        assert _verdict_per_symbol(result) == "SIGNAL"

    def test_weak_signal_between_50_and_55(self):
        result = SymbolResult(symbol="MGC")
        # 40 samples, 21 wins → 52.5 % WR
        result.groups["bullish_strong"] = _grp("bullish_strong", count=20, wins_1=11)
        result.groups["bearish_strong"] = _grp("bearish_strong", count=20, wins_1=10)
        assert _verdict_per_symbol(result) == "WEAK_SIGNAL"

    def test_no_signal_below_50(self):
        result = SymbolResult(symbol="MGC")
        # 40 samples, 16 wins → 40 % WR
        result.groups["bullish_strong"] = _grp("bullish_strong", count=20, wins_1=8)
        result.groups["bearish_strong"] = _grp("bearish_strong", count=20, wins_1=8)
        assert _verdict_per_symbol(result) == "NO_SIGNAL"

    def test_best_of_3_horizons_used(self):
        """WR is the best across the three horizons — picks the strongest."""
        result = SymbolResult(symbol="MGC")
        # fwd_1 WR = 40 %, fwd_3 WR = 60 %, fwd_6 WR = 45 % → SIGNAL via fwd_3
        result.groups["bullish_strong"] = _grp(
            "bullish_strong", count=20, wins_1=8, wins_3=12, wins_6=9
        )
        result.groups["bearish_strong"] = _grp(
            "bearish_strong", count=20, wins_1=8, wins_3=12, wins_6=9
        )
        assert _verdict_per_symbol(result) == "SIGNAL"


class TestOverallVerdict:
    def test_signal_when_2_of_3_symbols_signal(self):
        per = {"MGC": "SIGNAL", "MNQ": "SIGNAL", "MES": "NO_SIGNAL"}
        assert _overall_verdict(per) == "SIGNAL_FOUND"

    def test_signal_when_3_of_3_symbols_signal(self):
        per = {"MGC": "SIGNAL", "MNQ": "SIGNAL", "MES": "SIGNAL"}
        assert _overall_verdict(per) == "SIGNAL_FOUND"

    def test_weak_signal_with_one_signal_one_other(self):
        per = {"MGC": "SIGNAL", "MNQ": "NO_SIGNAL", "MES": "NO_SIGNAL"}
        assert _overall_verdict(per) == "WEAK_SIGNAL"

    def test_weak_signal_with_two_weak(self):
        per = {"MGC": "WEAK_SIGNAL", "MNQ": "WEAK_SIGNAL", "MES": "NO_SIGNAL"}
        assert _overall_verdict(per) == "WEAK_SIGNAL"

    def test_no_signal_when_none_meet(self):
        per = {"MGC": "NO_SIGNAL", "MNQ": "NO_SIGNAL", "MES": "INSUFFICIENT_N"}
        assert _overall_verdict(per) == "NO_SIGNAL"

    def test_no_signal_on_empty(self):
        assert _overall_verdict({}) == "NO_SIGNAL"


# ────────────────── E2E smoke (against live CSVs) ──────────────────


@pytest.mark.skipif(
    not (ROOT / "historical_data" / "price" / "MGC_5m_databento.csv").exists(),
    reason="historical CSV unavailable in this checkout",
)
def test_e2e_smoke(monkeypatch, capsys):
    from scripts import probe_brain_predictive_power as mod

    argv = [
        "probe_brain_predictive_power.py",
        "--symbols", "MGC",
        "--since", "2026-05-01",
        "--until", "2026-06-12",
        "--warmup-bars", "30",
        "--window", "100",
        "--json",
    ]
    monkeypatch.setattr(sys, "argv", argv)
    rc = mod.main()
    out = capsys.readouterr().out
    assert "verdict" in out.lower()
    assert rc in (0, 1, 2)
