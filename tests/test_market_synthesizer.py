"""Tests for the PA/SMC synthesis engine (``core/market_synthesizer.py``).

Pin three layers:

1. Pure scoring helpers (``compute_bias``, ``confluence_score``) — should be
   deterministic for any frozen snapshot.
2. Snapshot construction (``build_snapshot``) — synthetic bar streams produce
   the expected primitive observations + bias.
3. Service lifecycle (``MarketSynthesizerService``) — bus subscribe / unsubscribe,
   per-symbol filtering, snapshot cache, thread-safe synchronous queries,
   event publishing.
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.events import Event, EventType  # noqa: E402
from core.market_synthesizer import (  # noqa: E402
    SWEEP_FRESHNESS_WINDOW_BARS,
    MarketContextSnapshot,
    MarketSynthesizerService,
    _sweep_freshness_weight,
    build_snapshot,
    compute_bias,
    confluence_for_long_signal,
    confluence_score,
    get_synthesizer,
    maybe_start_synthesizer,
    reset_synthesizer_for_tests,
)


# ════════════════════════════ bar fixtures ════════════════════════════════


@dataclass
class _Bar:
    """Minimal bar shape compatible with the primitive detectors."""
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 1.0


def _utc(year: int, month: int, day: int, hour: int = 9, minute: int = 30) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


def _flat_bars(n: int, *, price: float = 100.0, ts0: Optional[datetime] = None) -> List[_Bar]:
    """N identical bars (no swings, no sweeps, no FVGs)."""
    ts0 = ts0 or _utc(2026, 6, 1, 10, 0)
    return [
        _Bar(
            timestamp=ts0 + timedelta(minutes=5 * i),
            open=price, high=price + 0.1, low=price - 0.1, close=price,
        )
        for i in range(n)
    ]


def _zigzag_up_bars(*, ts0: Optional[datetime] = None) -> List[_Bar]:
    """Zigzag uptrend with confirmed swing highs + lows.

    Pattern (closes): 100, 102, 104, 106, 103, 101, 103, 108, 110, 112, 109, 107,
                       109, 114, 116, 118, 120, 130
    → Multiple swing pivots confirmed at lookback=3.
      Most recent close (130) prints above the last swing high → BoS_UP.
    """
    ts0 = ts0 or _utc(2026, 6, 1, 10, 0)
    closes = [
        100, 102, 104, 106, 103, 101, 103, 108, 110, 112, 109, 107,
        109, 114, 116, 118, 120, 130,
    ]
    out: List[_Bar] = []
    for i, c in enumerate(closes):
        out.append(_Bar(
            timestamp=ts0 + timedelta(minutes=5 * i),
            open=float(c - 1),
            high=float(c + 1.5),
            low=float(c - 1.5),
            close=float(c),
        ))
    return out


def _sweep_low_bars(*, ts0: Optional[datetime] = None) -> List[_Bar]:
    """Bars that produce a fresh long-side liquidity sweep on the last bar.

    First establishes a swing low via a clear V-shape (so the lookback-3
    fractal confirms a pivot), then the most-recent bar wicks BELOW the
    swing low and closes back inside it — a textbook sweep_low_fade signal.
    """
    ts0 = ts0 or _utc(2026, 6, 1, 10, 0)
    # Build a clear V: down-up-up confirms a swing low at index 7.
    # Then drift sideways and finally pierce the low + close back above.
    #
    #         pierce-and-close-back
    # H ┐                       ┌─
    # M │   ↓        ↑        │
    # L └─────V─────────────────└─
    #         ↑
    #       swing low
    pattern = [
        # warm-up flat
        (100.0, 100.5, 99.5, 100.0),
        (100.0, 100.5, 99.5, 100.0),
        (100.0, 100.5, 99.5, 100.0),
        # descent into pivot low at index 7 (close=95)
        (100.0, 100.5, 98.0, 98.0),
        (98.0, 98.5, 96.0, 96.0),
        (96.0, 96.5, 95.5, 95.5),
        (95.5, 95.7, 95.0, 95.2),
        # pivot low — lowest low in the V
        (95.2, 95.5, 94.5, 95.0),
        # ascent (3 bars upward — confirms low pivot via lookback=3)
        (95.0, 96.5, 95.5, 96.0),
        (96.0, 97.5, 96.5, 97.0),
        (97.0, 98.5, 97.5, 98.0),
        (98.0, 99.5, 98.5, 99.0),
        # consolidation above the low
        (99.0, 99.5, 98.5, 99.0),
        (99.0, 99.5, 98.5, 99.0),
        (99.0, 99.5, 98.5, 99.0),
        # SWEEP: wick to 93.5 (below 94.5 low), close back to 98.0
        (99.0, 99.5, 93.5, 98.0),
    ]
    out: List[_Bar] = []
    for i, (o, h, low, c) in enumerate(pattern):
        out.append(_Bar(
            timestamp=ts0 + timedelta(minutes=5 * i),
            open=o, high=h, low=low, close=c,
        ))
    return out


def _zigzag_down_bars(*, ts0: Optional[datetime] = None) -> List[_Bar]:
    """Mirror of zigzag_up — most recent close prints below last swing low → BoS_DOWN."""
    ts0 = ts0 or _utc(2026, 6, 1, 10, 0)
    closes = [
        130, 128, 126, 124, 127, 129, 127, 122, 120, 118, 121, 123,
        121, 116, 114, 112, 110, 100,
    ]
    out: List[_Bar] = []
    for i, c in enumerate(closes):
        out.append(_Bar(
            timestamp=ts0 + timedelta(minutes=5 * i),
            open=float(c + 1),
            high=float(c + 1.5),
            low=float(c - 1.5),
            close=float(c),
        ))
    return out


# ════════════════════════════ pure scoring helpers ══════════════════════════


class TestSweepFreshnessWeight:
    """v2: freshness decay drives sweep-derived bias confidence."""

    def test_just_closed_bar_is_full_weight(self):
        assert _sweep_freshness_weight(0) == pytest.approx(1.0)

    def test_linear_decay_within_window(self):
        # SWEEP_FRESHNESS_WINDOW_BARS == 3 ⇒ 1, 2/3, 1/3, 0
        assert _sweep_freshness_weight(1) == pytest.approx(2 / 3, abs=0.001)
        assert _sweep_freshness_weight(2) == pytest.approx(1 / 3, abs=0.001)
        assert _sweep_freshness_weight(3) == pytest.approx(0.0, abs=0.001)

    def test_out_of_window_is_zero(self):
        assert _sweep_freshness_weight(SWEEP_FRESHNESS_WINDOW_BARS + 1) == 0.0
        assert _sweep_freshness_weight(99) == 0.0

    def test_none_or_negative_is_zero(self):
        assert _sweep_freshness_weight(None) == 0.0
        assert _sweep_freshness_weight(-1) == 0.0


class TestComputeBiasV2:
    """v2 bias is driven SOLELY by fresh long-side sweeps.

    The 2026-06-12 primitive-isolation probe showed BoS / CHoCH / swing
    labels carry no useful isolated signal and short-side sweeps are
    anti-predictive — none of them should influence the v2 bias.
    """

    def _snap(self, **kw: Any) -> MarketContextSnapshot:
        defaults = dict(
            symbol="MGC", timeframe="5m", as_of=None, bar_count=0,
            session=None, prior_session_high=None, prior_session_low=None,
            recent_swing_high=None, recent_swing_high_label=None,
            recent_swing_low=None, recent_swing_low_label=None,
            structure_event="none",
            recent_sweep_bars_ago=None, recent_sweep_direction=None,
            recent_sweep_strength=0.0,
            fvg_count=0, order_block_count=0,
            bias="neutral", confidence=0.0, bias_factors={},
        )
        defaults.update(kw)
        return MarketContextSnapshot(**defaults)

    def test_neutral_when_no_signals(self):
        bias, conf = compute_bias(self._snap())
        assert bias == "neutral"
        assert conf == 0.0

    def test_bos_alone_does_not_drive_bias(self):
        """v1 weighted BoS at 0.40 — v2 ignores it entirely."""
        bias, conf = compute_bias(self._snap(structure_event="bos_up"))
        assert bias == "neutral"
        assert conf == 0.0
        bias, conf = compute_bias(self._snap(structure_event="bos_down"))
        assert bias == "neutral"
        assert conf == 0.0

    def test_choch_alone_does_not_drive_bias(self):
        bias, _ = compute_bias(self._snap(structure_event="choch_up"))
        assert bias == "neutral"
        bias, _ = compute_bias(self._snap(structure_event="choch_down"))
        assert bias == "neutral"

    def test_swing_labels_alone_do_not_drive_bias(self):
        bias, _ = compute_bias(self._snap(
            recent_swing_high_label="HH",
            recent_swing_low_label="HL",
        ))
        assert bias == "neutral"
        bias, _ = compute_bias(self._snap(
            recent_swing_high_label="LH",
            recent_swing_low_label="LL",
        ))
        assert bias == "neutral"

    def test_fresh_sweep_low_drives_bullish_full_weight(self):
        bias, conf = compute_bias(self._snap(
            recent_sweep_direction=+1, recent_sweep_bars_ago=0,
        ))
        assert bias == "bullish"
        assert conf == pytest.approx(1.0, abs=0.001)

    def test_sweep_low_decays_with_age(self):
        b0, c0 = compute_bias(self._snap(
            recent_sweep_direction=+1, recent_sweep_bars_ago=0,
        ))
        b1, c1 = compute_bias(self._snap(
            recent_sweep_direction=+1, recent_sweep_bars_ago=1,
        ))
        b2, c2 = compute_bias(self._snap(
            recent_sweep_direction=+1, recent_sweep_bars_ago=2,
        ))
        b3, c3 = compute_bias(self._snap(
            recent_sweep_direction=+1, recent_sweep_bars_ago=3,
        ))
        assert b0 == b1 == b2 == "bullish"
        assert c0 > c1 > c2 > 0.0
        # Window boundary → neutral.
        assert b3 == "neutral"

    def test_stale_sweep_low_is_neutral(self):
        bias, conf = compute_bias(self._snap(
            recent_sweep_direction=+1, recent_sweep_bars_ago=20,
        ))
        assert bias == "neutral"
        assert conf == 0.0

    def test_sweep_high_does_NOT_drive_bearish(self):
        """v2: short-side sweeps were anti-predictive in isolation."""
        bias, conf = compute_bias(self._snap(
            recent_sweep_direction=-1, recent_sweep_bars_ago=0,
        ))
        assert bias == "neutral"
        assert conf == 0.0

    def test_sweep_low_with_missing_bars_ago_falls_back_to_zero_weight(self):
        bias, conf = compute_bias(self._snap(
            recent_sweep_direction=+1, recent_sweep_bars_ago=None,
        ))
        assert bias == "neutral"
        assert conf == 0.0


class TestConfluenceScore:
    """v2: confluence only fires on the LONG side."""

    def _snap(self, bias: str, confidence: float) -> MarketContextSnapshot:
        return MarketContextSnapshot(
            symbol="MGC", timeframe="5m", as_of=None, bar_count=0,
            session=None, prior_session_high=None, prior_session_low=None,
            recent_swing_high=None, recent_swing_high_label=None,
            recent_swing_low=None, recent_swing_low_label=None,
            structure_event="none",
            recent_sweep_bars_ago=None, recent_sweep_direction=None,
            recent_sweep_strength=0.0,
            fvg_count=0, order_block_count=0,
            bias=bias, confidence=confidence, bias_factors={},
        )

    def test_long_agrees_with_bullish_bias(self):
        s = self._snap("bullish", 0.50)
        assert confluence_score(s, side=+1) == pytest.approx(0.50)

    def test_short_always_returns_zero(self):
        """v2 has NO validated short edge — confluence_score is silent on shorts."""
        s = self._snap("bullish", 0.50)
        assert confluence_score(s, side=-1) == 0.0
        s2 = self._snap("bearish", 0.30)
        assert confluence_score(s2, side=-1) == 0.0

    def test_long_against_bullish_returns_zero_when_neutral_bias(self):
        s = self._snap("neutral", 0.0)
        assert confluence_score(s, side=+1) == 0.0

    def test_side_zero_returns_zero(self):
        s = self._snap("bullish", 0.50)
        assert confluence_score(s, side=0) == 0.0


class TestConfluenceForLongSignal:
    """Module-level helper strategies actually call."""

    def _snap(self, bias: str, confidence: float) -> MarketContextSnapshot:
        return MarketContextSnapshot(
            symbol="MGC", timeframe="5m", as_of=None, bar_count=0,
            session=None, prior_session_high=None, prior_session_low=None,
            recent_swing_high=None, recent_swing_high_label=None,
            recent_swing_low=None, recent_swing_low_label=None,
            structure_event="none",
            recent_sweep_bars_ago=None, recent_sweep_direction=None,
            recent_sweep_strength=0.0,
            fvg_count=0, order_block_count=0,
            bias=bias, confidence=confidence, bias_factors={},
        )

    def test_returns_bullish_confidence(self):
        s = self._snap("bullish", 0.75)
        assert confluence_for_long_signal(s) == pytest.approx(0.75)

    def test_neutral_snapshot_returns_zero(self):
        s = self._snap("neutral", 0.0)
        assert confluence_for_long_signal(s) == 0.0

    def test_bearish_snapshot_returns_zero(self):
        """v2 doesn't emit bearish anyway, but defensive — never boost a long
        signal when bias is bearish."""
        s = self._snap("bearish", 0.50)
        assert confluence_for_long_signal(s) == 0.0


# ════════════════════════════ snapshot construction ════════════════════════


class TestBuildSnapshot:
    def test_empty_bars_returns_neutral(self):
        snap = build_snapshot(symbol="MGC", timeframe="5m", bars=[])
        assert snap.symbol == "MGC"
        assert snap.bar_count == 0
        assert snap.bias == "neutral"
        assert snap.confidence == 0.0

    def test_flat_bars_no_signals(self):
        snap = build_snapshot(symbol="MGC", timeframe="5m", bars=_flat_bars(30))
        assert snap.bar_count == 30
        assert snap.bias == "neutral"
        assert snap.confidence == 0.0
        # Flat bars: no swings should be confirmed.
        assert snap.recent_swing_high is None
        assert snap.recent_swing_low is None

    def test_sweep_low_fixture_drives_bullish_bias(self):
        """Integration: sweep_low pattern → recent_sweep_direction == +1 →
        bullish bias (the ONE primitive that survives v2 scoring)."""
        bars = _sweep_low_bars()
        snap = build_snapshot(symbol="MGC", timeframe="5m", bars=bars)
        assert snap.recent_sweep_direction == +1
        assert snap.recent_sweep_bars_ago == 0
        assert snap.bias == "bullish"
        assert snap.confidence == pytest.approx(1.0, abs=0.001)

    def test_zigzag_uptrend_emits_bos_event_but_neutral_bias(self):
        """v2: BoS_UP is detected as a structural fact (informational), but
        does NOT drive bias on its own — that's the v1 mistake."""
        bars = _zigzag_up_bars()
        snap = build_snapshot(symbol="MGC", timeframe="5m", bars=bars)
        assert snap.structure_event == "bos_up"
        # No fresh long-side sweep present → bias must be neutral.
        assert snap.bias == "neutral"
        assert snap.confidence == 0.0

    def test_zigzag_downtrend_emits_bos_event_but_neutral_bias(self):
        bars = _zigzag_down_bars()
        snap = build_snapshot(symbol="MGC", timeframe="5m", bars=bars)
        assert snap.structure_event == "bos_down"
        assert snap.bias == "neutral"
        assert snap.confidence == 0.0

    def test_swing_labels_emitted_when_pivots_present(self):
        bars = _zigzag_up_bars()
        snap = build_snapshot(symbol="MGC", timeframe="5m", bars=bars)
        # With multiple confirmed swing highs + lows, both labels must be set.
        assert snap.recent_swing_high_label is not None
        assert snap.recent_swing_low_label is not None

    def test_as_of_carries_last_bar_timestamp(self):
        bars = _flat_bars(5)
        snap = build_snapshot(symbol="MGC", timeframe="5m", bars=bars)
        assert snap.as_of == bars[-1].timestamp

    def test_as_dict_is_json_serialisable(self):
        import json

        bars = _flat_bars(10)
        snap = build_snapshot(symbol="MGC", timeframe="5m", bars=bars)
        # Round-trip through JSON; must not raise.
        s = json.dumps(snap.as_dict())
        round_tripped = json.loads(s)
        assert round_tripped["symbol"] == "MGC"
        assert round_tripped["timeframe"] == "5m"

    def test_window_too_small_for_swings_is_graceful(self):
        bars = _flat_bars(3)
        snap = build_snapshot(symbol="MGC", timeframe="5m", bars=bars)
        # Below 2*lookback+1 = 7 bars → no swings detected — must not crash.
        assert snap.recent_swing_high is None
        assert snap.structure_event == "none"

    def test_session_label_set_when_timestamp_has_tz(self):
        bars = _flat_bars(5, ts0=_utc(2026, 6, 10, 14, 30))  # 14:30 UTC = 10:30 ET
        snap = build_snapshot(symbol="MGC", timeframe="5m", bars=bars)
        assert snap.session == "nyam"


# ════════════════════════════ service lifecycle ════════════════════════════


class _FakeBus:
    """Minimal pub/sub bus capturing subscriptions + published events.

    Mirrors EventBus's surface area used by the synthesizer
    (subscribe / unsubscribe / publish) without the asyncio queue plumbing.
    """

    def __init__(self) -> None:
        self.subs: Dict[EventType, List[Any]] = {}
        self.published: List[Event] = []

    def subscribe(self, event_type: EventType, cb: Any) -> None:
        self.subs.setdefault(event_type, []).append(cb)

    def unsubscribe(self, event_type: EventType, cb: Any) -> None:
        if event_type in self.subs and cb in self.subs[event_type]:
            self.subs[event_type].remove(cb)

    async def publish(self, evt: Event) -> None:
        self.published.append(evt)


@pytest.fixture(autouse=True)
def _reset_global_synthesizer():
    reset_synthesizer_for_tests()
    yield
    reset_synthesizer_for_tests()


class TestServiceLifecycle:
    @pytest.mark.asyncio
    async def test_start_subscribes_to_bar_completed(self):
        bus = _FakeBus()
        svc = MarketSynthesizerService(event_bus=bus)
        ok = await svc.start()
        assert ok is True
        assert EventType.BAR_COMPLETED in bus.subs
        assert len(bus.subs[EventType.BAR_COMPLETED]) == 1

    @pytest.mark.asyncio
    async def test_stop_unsubscribes(self):
        bus = _FakeBus()
        svc = MarketSynthesizerService(event_bus=bus)
        await svc.start()
        await svc.stop()
        assert bus.subs[EventType.BAR_COMPLETED] == []

    @pytest.mark.asyncio
    async def test_filters_by_symbol(self):
        bus = _FakeBus()
        svc = MarketSynthesizerService(event_bus=bus, symbols=["MGC"])
        await svc.start()
        bars = _flat_bars(20)
        await svc._on_bar_closed(Event(
            type=EventType.BAR_COMPLETED,
            data={"symbol": "MNQ", "timeframe": "5m", "bars": bars},
        ))
        assert bus.published == []
        await svc._on_bar_closed(Event(
            type=EventType.BAR_COMPLETED,
            data={"symbol": "MGC", "timeframe": "5m", "bars": bars},
        ))
        assert len(bus.published) == 1

    @pytest.mark.asyncio
    async def test_filters_by_timeframe(self):
        bus = _FakeBus()
        svc = MarketSynthesizerService(event_bus=bus, timeframes=["5m"])
        await svc.start()
        bars = _flat_bars(20)
        await svc._on_bar_closed(Event(
            type=EventType.BAR_COMPLETED,
            data={"symbol": "MGC", "timeframe": "1m", "bars": bars},
        ))
        assert bus.published == []
        await svc._on_bar_closed(Event(
            type=EventType.BAR_COMPLETED,
            data={"symbol": "MGC", "timeframe": "5m", "bars": bars},
        ))
        assert len(bus.published) == 1

    @pytest.mark.asyncio
    async def test_event_payload_carries_snapshot_dict(self):
        bus = _FakeBus()
        svc = MarketSynthesizerService(event_bus=bus)
        await svc.start()
        await svc._on_bar_closed(Event(
            type=EventType.BAR_COMPLETED,
            data={"symbol": "MGC", "timeframe": "5m", "bars": _flat_bars(20)},
        ))
        assert len(bus.published) == 1
        evt = bus.published[0]
        assert evt.type == EventType.MARKET_CONTEXT_UPDATED
        snap = evt.data["snapshot"]
        assert snap["symbol"] == "MGC"
        assert snap["timeframe"] == "5m"
        assert "bias" in snap and "confidence" in snap

    @pytest.mark.asyncio
    async def test_get_context_returns_latest_snapshot(self):
        bus = _FakeBus()
        svc = MarketSynthesizerService(event_bus=bus)
        await svc.start()
        await svc._on_bar_closed(Event(
            type=EventType.BAR_COMPLETED,
            data={"symbol": "MGC", "timeframe": "5m", "bars": _flat_bars(20)},
        ))
        snap = svc.get_context("MGC", "5m")
        assert snap is not None
        assert snap.bar_count == 20

    @pytest.mark.asyncio
    async def test_get_context_case_insensitive_symbol(self):
        bus = _FakeBus()
        svc = MarketSynthesizerService(event_bus=bus)
        await svc.start()
        await svc._on_bar_closed(Event(
            type=EventType.BAR_COMPLETED,
            data={"symbol": "mgc", "timeframe": "5m", "bars": _flat_bars(20)},
        ))
        assert svc.get_context("MGC", "5m") is not None
        assert svc.get_context("mgc", "5m") is not None

    @pytest.mark.asyncio
    async def test_get_context_without_timeframe_returns_any(self):
        bus = _FakeBus()
        svc = MarketSynthesizerService(event_bus=bus)
        await svc.start()
        await svc._on_bar_closed(Event(
            type=EventType.BAR_COMPLETED,
            data={"symbol": "MGC", "timeframe": "5m", "bars": _flat_bars(20)},
        ))
        snap = svc.get_context("MGC")
        assert snap is not None
        assert snap.timeframe == "5m"

    @pytest.mark.asyncio
    async def test_get_context_returns_none_when_no_snapshot(self):
        bus = _FakeBus()
        svc = MarketSynthesizerService(event_bus=bus)
        assert svc.get_context("MGC", "5m") is None

    @pytest.mark.asyncio
    async def test_confluence_score_default_zero_when_no_snapshot(self):
        bus = _FakeBus()
        svc = MarketSynthesizerService(event_bus=bus)
        assert svc.confluence_score("MGC", side=+1) == 0.0

    @pytest.mark.asyncio
    async def test_window_bar_trim(self):
        bus = _FakeBus()
        svc = MarketSynthesizerService(event_bus=bus, window_bars=10)
        await svc.start()
        # Hand in 50 bars; should be trimmed to 10 before snapshotting.
        await svc._on_bar_closed(Event(
            type=EventType.BAR_COMPLETED,
            data={"symbol": "MGC", "timeframe": "5m", "bars": _flat_bars(50)},
        ))
        snap = svc.get_context("MGC", "5m")
        assert snap is not None
        assert snap.bar_count == 10

    @pytest.mark.asyncio
    async def test_fetch_history_used_when_bars_missing(self):
        captured: Dict[str, Any] = {}

        def fetch(*, symbol: str, timeframe: str, limit: int):
            captured["call"] = (symbol, timeframe, limit)
            return _flat_bars(15)

        bus = _FakeBus()
        svc = MarketSynthesizerService(event_bus=bus, fetch_history=fetch)
        await svc.start()
        await svc._on_bar_closed(Event(
            type=EventType.BAR_COMPLETED,
            data={"symbol": "MGC", "timeframe": "5m"},  # no bars in payload
        ))
        assert captured["call"] == ("MGC", "5m", svc._window_bars)
        snap = svc.get_context("MGC", "5m")
        assert snap is not None
        assert snap.bar_count == 15

    @pytest.mark.asyncio
    async def test_empty_payload_short_circuits(self):
        bus = _FakeBus()
        svc = MarketSynthesizerService(event_bus=bus)
        await svc.start()
        # No symbol → ignored.
        await svc._on_bar_closed(Event(type=EventType.BAR_COMPLETED, data={}))
        assert bus.published == []

    @pytest.mark.asyncio
    async def test_callback_errors_are_isolated(self, monkeypatch):
        bus = _FakeBus()
        svc = MarketSynthesizerService(event_bus=bus)
        await svc.start()

        # Force build_snapshot to raise; service must swallow + log,
        # not propagate, so the bus stays healthy.
        from core import market_synthesizer as ms

        monkeypatch.setattr(
            ms, "build_snapshot", lambda **kw: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        await svc._on_bar_closed(Event(
            type=EventType.BAR_COMPLETED,
            data={"symbol": "MGC", "timeframe": "5m", "bars": _flat_bars(20)},
        ))
        # No published events (build crashed), but the callback returned normally.
        assert bus.published == []

    @pytest.mark.asyncio
    async def test_liquidity_sweep_event_fires_on_fresh_long_side_sweep(self):
        """v2 headline: a fresh sweep_low must emit LIQUIDITY_SWEEP_DETECTED
        alongside the regular MARKET_CONTEXT_UPDATED event."""
        bus = _FakeBus()
        svc = MarketSynthesizerService(event_bus=bus)
        await svc.start()
        await svc._on_bar_closed(Event(
            type=EventType.BAR_COMPLETED,
            data={"symbol": "MGC", "timeframe": "5m", "bars": _sweep_low_bars()},
        ))
        # Exactly two events: the snapshot update + the sweep alert.
        types = [e.type for e in bus.published]
        assert EventType.MARKET_CONTEXT_UPDATED in types
        assert EventType.LIQUIDITY_SWEEP_DETECTED in types

        sweep_evt = next(
            e for e in bus.published if e.type == EventType.LIQUIDITY_SWEEP_DETECTED
        )
        payload = sweep_evt.data
        assert payload["symbol"] == "MGC"
        assert payload["timeframe"] == "5m"
        assert payload["bars_ago"] == 0
        assert payload["sweep_level"] is not None
        assert 0.0 < payload["confidence"] <= 1.0

    @pytest.mark.asyncio
    async def test_no_sweep_event_when_no_fresh_sweep(self):
        """Flat bars → no sweep detected → only the snapshot event fires."""
        bus = _FakeBus()
        svc = MarketSynthesizerService(event_bus=bus)
        await svc.start()
        await svc._on_bar_closed(Event(
            type=EventType.BAR_COMPLETED,
            data={"symbol": "MGC", "timeframe": "5m", "bars": _flat_bars(20)},
        ))
        types = [e.type for e in bus.published]
        assert EventType.MARKET_CONTEXT_UPDATED in types
        assert EventType.LIQUIDITY_SWEEP_DETECTED not in types

    @pytest.mark.asyncio
    async def test_snapshots_built_counter_increments(self):
        bus = _FakeBus()
        svc = MarketSynthesizerService(event_bus=bus)
        await svc.start()
        for _ in range(3):
            await svc._on_bar_closed(Event(
                type=EventType.BAR_COMPLETED,
                data={"symbol": "MGC", "timeframe": "5m", "bars": _flat_bars(20)},
            ))
        assert svc.snapshots_built == 3


class TestBootHelper:
    def test_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv("MARKET_SYNTHESIZER_ENABLED", raising=False)
        bot = type("Bot", (), {"event_bus": _FakeBus()})()
        assert maybe_start_synthesizer(bot) is None

    def test_enabled_via_env(self, monkeypatch):
        monkeypatch.setenv("MARKET_SYNTHESIZER_ENABLED", "1")
        bus = _FakeBus()
        bot = type("Bot", (), {"event_bus": bus, "get_historical_data": None})()
        svc = maybe_start_synthesizer(bot)
        assert svc is not None
        assert get_synthesizer() is svc

    def test_idempotent_when_already_started(self, monkeypatch):
        monkeypatch.setenv("MARKET_SYNTHESIZER_ENABLED", "1")
        bot = type("Bot", (), {"event_bus": _FakeBus(), "get_historical_data": None})()
        a = maybe_start_synthesizer(bot)
        b = maybe_start_synthesizer(bot)
        assert a is b

    def test_no_event_bus_returns_none(self, monkeypatch):
        monkeypatch.setenv("MARKET_SYNTHESIZER_ENABLED", "1")
        bot = type("Bot", (), {})()  # no event_bus attr
        assert maybe_start_synthesizer(bot) is None

    def test_symbols_env_parsed(self, monkeypatch):
        monkeypatch.setenv("MARKET_SYNTHESIZER_ENABLED", "1")
        monkeypatch.setenv("MARKET_SYNTHESIZER_SYMBOLS", "MGC, MNQ , MES")
        bot = type("Bot", (), {"event_bus": _FakeBus(), "get_historical_data": None})()
        svc = maybe_start_synthesizer(bot)
        assert svc is not None
        assert svc._symbols == {"MGC", "MNQ", "MES"}

    def test_invalid_int_env_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("MARKET_SYNTHESIZER_ENABLED", "1")
        monkeypatch.setenv("MARKET_SYNTHESIZER_WINDOW_BARS", "not-an-int")
        bot = type("Bot", (), {"event_bus": _FakeBus(), "get_historical_data": None})()
        svc = maybe_start_synthesizer(bot)
        assert svc is not None
        assert svc._window_bars == 200
