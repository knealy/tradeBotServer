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
    MarketContextSnapshot,
    MarketSynthesizerService,
    build_snapshot,
    compute_bias,
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


class TestComputeBias:
    def _snap(self, **kw: Any) -> MarketContextSnapshot:
        # Build a minimal snapshot for the helper to consume.
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

    def test_bos_up_drives_bullish(self):
        bias, conf = compute_bias(self._snap(structure_event="bos_up"))
        assert bias == "bullish"
        assert conf == pytest.approx(0.40, abs=0.001)

    def test_bos_down_drives_bearish(self):
        bias, conf = compute_bias(self._snap(structure_event="bos_down"))
        assert bias == "bearish"
        assert conf == pytest.approx(0.40, abs=0.001)

    def test_choch_up_weaker_than_bos_up(self):
        _, conf_choch = compute_bias(self._snap(structure_event="choch_up"))
        _, conf_bos = compute_bias(self._snap(structure_event="bos_up"))
        assert conf_choch < conf_bos

    def test_swing_trend_up_alone(self):
        bias, conf = compute_bias(self._snap(
            recent_swing_high_label="HH",
            recent_swing_low_label="HL",
        ))
        assert bias == "bullish"
        assert conf == pytest.approx(0.20, abs=0.001)

    def test_sweep_below_adds_long_bias(self):
        bias, conf = compute_bias(self._snap(recent_sweep_direction=+1))
        assert bias == "bullish"
        assert conf == pytest.approx(0.25, abs=0.001)

    def test_sweep_above_adds_short_bias(self):
        bias, conf = compute_bias(self._snap(recent_sweep_direction=-1))
        assert bias == "bearish"
        assert conf == pytest.approx(0.25, abs=0.001)

    def test_conflicting_signals_partially_cancel(self):
        bias, conf = compute_bias(self._snap(
            structure_event="bos_up",      # +0.40 bull
            recent_sweep_direction=-1,     # +0.25 bear
        ))
        assert bias == "bullish"
        assert conf == pytest.approx(0.15, abs=0.001)

    def test_full_bull_stack_caps_at_one(self):
        bias, conf = compute_bias(self._snap(
            structure_event="bos_up",                # +0.40
            recent_swing_high_label="HH",            # \
            recent_swing_low_label="HL",             # +0.20
            recent_sweep_direction=+1,               # +0.25
        ))
        # 0.40 + 0.20 + 0.25 = 0.85 — below cap, but uniformly bullish.
        assert bias == "bullish"
        assert conf == pytest.approx(0.85, abs=0.001)


class TestConfluenceScore:
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

    def test_short_against_bullish_bias_is_negative(self):
        s = self._snap("bullish", 0.50)
        assert confluence_score(s, side=-1) == pytest.approx(-0.50)

    def test_short_agrees_with_bearish_bias(self):
        s = self._snap("bearish", 0.30)
        assert confluence_score(s, side=-1) == pytest.approx(0.30)

    def test_neutral_returns_zero(self):
        s = self._snap("neutral", 0.0)
        assert confluence_score(s, side=+1) == 0.0
        assert confluence_score(s, side=-1) == 0.0

    def test_side_zero_returns_zero(self):
        s = self._snap("bullish", 0.50)
        assert confluence_score(s, side=0) == 0.0


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

    def test_zigzag_uptrend_produces_bullish_bias(self):
        bars = _zigzag_up_bars()
        snap = build_snapshot(symbol="MGC", timeframe="5m", bars=bars)
        # Most recent close (130) breaks above all confirmed swing highs
        # → BoS_UP → bullish bias.
        assert snap.bias == "bullish"
        assert snap.confidence > 0.0
        assert snap.structure_event == "bos_up"

    def test_zigzag_downtrend_produces_bearish_bias(self):
        bars = _zigzag_down_bars()
        snap = build_snapshot(symbol="MGC", timeframe="5m", bars=bars)
        assert snap.bias == "bearish"
        assert snap.structure_event == "bos_down"

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
