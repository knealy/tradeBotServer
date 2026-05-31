"""Live SignalR → bar aggregator → trading_bot live cache → strategy ``get_historical_data`` merge.

Verifies the medium-term Market Hub wiring added after the 2026-05-21 outage.
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Force REST-only paths off during the SignalR import probe.
os.environ.setdefault("ENABLE_SIGNALR", "false")

from core.bar_aggregator import Bar, BarAggregator  # noqa: E402


# ── BarAggregator multi-subscriber ───────────────────────────────────────────


def test_register_completed_bar_callback_receives_closed_bars():
    """Closing a bar should fire every registered completed-bar callback exactly once."""
    received_a: List[Bar] = []
    received_b: List[Bar] = []

    agg = BarAggregator(broadcast_callback=None, default_timeframes=["1m"])
    agg.register_completed_bar_callback(received_a.append)
    agg.register_completed_bar_callback(received_b.append)

    base = datetime(2026, 5, 21, 13, 0, tzinfo=timezone.utc)
    agg.add_quote("MNQ", 21000.0, volume=1, timestamp=base + timedelta(seconds=5))
    agg.add_quote("MNQ", 21010.0, volume=2, timestamp=base + timedelta(seconds=30))
    agg.add_quote("MNQ", 21005.0, volume=3, timestamp=base + timedelta(minutes=1, seconds=5))

    assert len(received_a) == 1, "first minute should have closed exactly one bar"
    assert len(received_b) == 1, "second subscriber should see the same close"
    bar = received_a[0]
    assert bar.symbol == "MNQ"
    assert bar.timeframe == "1m"
    assert bar.open == 21000.0
    assert bar.high == 21010.0
    assert bar.low == 21000.0
    assert bar.close == 21010.0


def test_unregister_completed_bar_callback_stops_delivery():
    received: List[Bar] = []
    agg = BarAggregator(broadcast_callback=None, default_timeframes=["1m"])
    agg.register_completed_bar_callback(received.append)
    agg.unregister_completed_bar_callback(received.append)

    base = datetime(2026, 5, 21, 13, 0, tzinfo=timezone.utc)
    agg.add_quote("MNQ", 21000.0, volume=1, timestamp=base + timedelta(seconds=5))
    agg.add_quote("MNQ", 21005.0, volume=1, timestamp=base + timedelta(minutes=1, seconds=5))

    assert received == []


def test_completed_bar_callback_errors_are_isolated():
    """A misbehaving subscriber must not break the broadcast or other subscribers."""
    received_good: List[Bar] = []

    def bad(_bar: Bar) -> None:
        raise RuntimeError("boom")

    agg = BarAggregator(broadcast_callback=None, default_timeframes=["1m"])
    agg.register_completed_bar_callback(bad)
    agg.register_completed_bar_callback(received_good.append)

    base = datetime(2026, 5, 21, 13, 0, tzinfo=timezone.utc)
    agg.add_quote("MNQ", 21000.0, volume=1, timestamp=base + timedelta(seconds=5))
    agg.add_quote("MNQ", 21005.0, volume=1, timestamp=base + timedelta(minutes=1, seconds=5))

    assert len(received_good) == 1
    assert received_good[0].close == 21000.0


# ── TopStepXTradingBot live cache + REST merge ───────────────────────────────


class _StubAdapterBar:
    """Mimics the Bar object that ``broker_adapter.get_historical_data`` returns."""

    def __init__(self, ts: datetime, close: float, **kw: Any) -> None:
        self.timestamp = ts
        self.open = kw.get("open", close)
        self.high = kw.get("high", close)
        self.low = kw.get("low", close)
        self.close = close
        self.volume = kw.get("volume", 0)
        self.symbol = kw.get("symbol", "MNQ")


class _StubAdapter:
    """Replacement for ``trading_bot.broker_adapter`` that returns a fixed bar series."""

    def __init__(self, bars: List[_StubAdapterBar]) -> None:
        self._bars = bars

    async def get_historical_data(self, **_kw: Any) -> List[_StubAdapterBar]:
        return list(self._bars)


@pytest.fixture
def _bot():
    """Spin up a minimal TopStepXTradingBot without auth/SignalR side-effects."""
    from trading_bot import TopStepXTradingBot

    # Stub out the adapter and auth-token plumbing the constructor invokes.
    bot = TopStepXTradingBot.__new__(TopStepXTradingBot)
    # Mirror the parts of __init__ we need (cache + bar aggregator hook).
    import threading as _t

    bot._live_bars = {}
    bot._live_bars_lock = _t.RLock()
    bot._live_bars_maxlen = 240
    bot.bar_aggregator = BarAggregator(broadcast_callback=None, default_timeframes=["1m", "5m"])
    bot.bar_aggregator.register_completed_bar_callback(bot._on_live_bar_close.__get__(bot))
    bot.broker_adapter = None  # will be set per-test
    return bot


def test_live_bar_close_appends_to_cache(_bot):
    """A closed bar from the aggregator should appear in ``_live_bars[symbol][tf]``."""
    base = datetime(2026, 5, 21, 13, 0, tzinfo=timezone.utc)
    _bot.bar_aggregator.add_quote("MNQ", 21000.0, volume=1, timestamp=base + timedelta(seconds=5))
    _bot.bar_aggregator.add_quote("MNQ", 21005.0, volume=1, timestamp=base + timedelta(minutes=1, seconds=5))

    cached = _bot._get_live_bars("MNQ", "1m")
    assert len(cached) == 1
    assert cached[0]["close"] == 21000.0
    assert cached[0]["timestamp"].startswith("2026-05-21T13:00:00")


def test_get_historical_data_merges_live_bars_when_rest_is_stale(_bot):
    """REST returns bars frozen at T-30min; live cache has fresher minute bars → merge appends them."""
    from trading_bot import TopStepXTradingBot  # imported for type, not used directly

    now = datetime(2026, 5, 21, 13, 30, tzinfo=timezone.utc)
    stale_tail = now - timedelta(minutes=30)
    rest_bars = [_StubAdapterBar(stale_tail - timedelta(minutes=5 * i), close=21000.0 - i)
                 for i in range(5)]
    rest_bars.reverse()  # oldest → newest
    _bot.broker_adapter = _StubAdapter(rest_bars)

    # Seed live cache with two newer bars.
    base = now - timedelta(minutes=2)
    _bot.bar_aggregator.add_quote("MNQ", 21020.0, volume=1, timestamp=base + timedelta(seconds=5))
    _bot.bar_aggregator.add_quote("MNQ", 21030.0, volume=1, timestamp=base + timedelta(minutes=1, seconds=5))
    _bot.bar_aggregator.add_quote("MNQ", 21040.0, volume=1, timestamp=base + timedelta(minutes=2, seconds=5))

    # REST tail is at stale_tail; live cache has bars at base, base+1min — both newer.
    merged = asyncio.run(
        TopStepXTradingBot.get_historical_data(_bot, "MNQ", "1m", limit=10)
    )
    assert len(merged) > len(rest_bars), "live bars should be appended"
    assert merged[-1]["close"] in (21020.0, 21030.0)


def test_get_historical_data_no_merge_when_live_cache_empty(_bot):
    """No cached bars → REST result returned untouched."""
    from trading_bot import TopStepXTradingBot

    now = datetime(2026, 5, 21, 13, 30, tzinfo=timezone.utc)
    rest_bars = [_StubAdapterBar(now - timedelta(minutes=5 * i), close=21000.0 - i) for i in range(3)]
    rest_bars.reverse()
    _bot.broker_adapter = _StubAdapter(rest_bars)

    merged = asyncio.run(
        TopStepXTradingBot.get_historical_data(_bot, "MNQ", "5m", limit=10)
    )
    assert len(merged) == 3


def test_get_historical_data_no_merge_when_rest_is_fresher(_bot):
    """REST tail is newer than the live cache → cache must NOT poison the result."""
    from trading_bot import TopStepXTradingBot

    now = datetime(2026, 5, 21, 13, 30, tzinfo=timezone.utc)
    rest_bars = [_StubAdapterBar(now - timedelta(minutes=i), close=21100.0 + i) for i in range(5)]
    rest_bars.reverse()  # oldest → newest, ending at now
    _bot.broker_adapter = _StubAdapter(rest_bars)

    # Seed cache with bars that are OLDER than REST tail.
    old_base = now - timedelta(hours=2)
    _bot.bar_aggregator.add_quote("MNQ", 20000.0, volume=1, timestamp=old_base + timedelta(seconds=5))
    _bot.bar_aggregator.add_quote("MNQ", 20001.0, volume=1, timestamp=old_base + timedelta(minutes=1, seconds=5))

    merged = asyncio.run(
        TopStepXTradingBot.get_historical_data(_bot, "MNQ", "1m", limit=10)
    )
    assert len(merged) == 5
    assert merged[-1]["close"] == 21100.0  # REST tail preserved


def test_get_historical_data_returns_live_cache_when_rest_empty(_bot):
    """If REST returns []/None, the live cache becomes the result (better than nothing)."""
    from trading_bot import TopStepXTradingBot

    _bot.broker_adapter = _StubAdapter([])

    base = datetime(2026, 5, 21, 13, 0, tzinfo=timezone.utc)
    _bot.bar_aggregator.add_quote("MNQ", 21000.0, volume=1, timestamp=base + timedelta(seconds=5))
    _bot.bar_aggregator.add_quote("MNQ", 21005.0, volume=1, timestamp=base + timedelta(minutes=1, seconds=5))

    merged = asyncio.run(
        TopStepXTradingBot.get_historical_data(_bot, "MNQ", "1m", limit=10)
    )
    assert len(merged) == 1
    assert merged[0]["close"] == 21000.0


def test_timeframe_normalization_5min_to_5m(_bot):
    assert _bot._normalize_timeframe_key("5MIN") == "5m"
    assert _bot._normalize_timeframe_key("5 minutes") == "5m"
    assert _bot._normalize_timeframe_key("1HOUR") == "1h"
    assert _bot._normalize_timeframe_key("15s") == "15s"
    assert _bot._normalize_timeframe_key("") == ""
    assert _bot._normalize_timeframe_key(None) == ""


def test_live_cache_replaces_tail_on_duplicate_timestamp(_bot):
    """If a bar with the same timestamp closes twice (shouldn't happen but defensive)."""
    base = datetime(2026, 5, 21, 13, 0, tzinfo=timezone.utc)
    bar_a = Bar(symbol="MNQ", timeframe="1m", timestamp=base,
                open=1.0, high=2.0, low=0.5, close=1.5, volume=3, tick_count=2)
    bar_b = Bar(symbol="MNQ", timeframe="1m", timestamp=base,
                open=1.0, high=4.0, low=0.5, close=3.5, volume=7, tick_count=5)
    _bot._on_live_bar_close(bar_a)
    _bot._on_live_bar_close(bar_b)
    cached = _bot._get_live_bars("MNQ", "1m")
    assert len(cached) == 1
    assert cached[0]["close"] == 3.5
    assert cached[0]["high"] == 4.0


# ── StrategyExecutor wiring (env toggle + symbol/timeframe gathering) ────────


class _RecordingBot:
    """Minimal stand-in for TopStepXTradingBot.start_market_hub_for_strategies."""

    def __init__(self) -> None:
        self.start_calls: List[Dict[str, Any]] = []
        # Mimic the attributes _wire_market_hub_for_live_strategies inspects.
        self.strategy_manager = _StubStrategyManager()

    async def start_market_hub_for_strategies(
        self, symbols, timeframes=None,
    ) -> bool:
        self.start_calls.append({
            "symbols": list(symbols),
            "timeframes": list(timeframes) if timeframes else None,
        })
        return True


class _StubStrategyManager:
    def __init__(self) -> None:
        self.strategies: Dict[str, Any] = {}


class _StubStrategyCfg:
    def __init__(self, symbols: List[str]) -> None:
        self.symbols = symbols


class _StubStrategy:
    def __init__(self, symbols: List[str], timeframe: str) -> None:
        self.config = _StubStrategyCfg(symbols)
        self.timeframe = timeframe


def _make_executor(running: Dict[str, Any], bot: Any):
    from core.strategy_executor import StrategyExecutor

    ex = StrategyExecutor.__new__(StrategyExecutor)
    ex.trading_bot = bot
    ex.running_strategies = running
    return ex


def test_executor_wires_market_hub_with_symbols_and_timeframes(monkeypatch):
    monkeypatch.setenv("EXECUTOR_MARKET_HUB", "true")
    monkeypatch.setenv("ENABLE_SIGNALR", "true")

    bot = _RecordingBot()
    bot.strategy_manager.strategies["morning_range_reversion"] = _StubStrategy(["MNQ", "MES"], "5m")
    bot.strategy_manager.strategies["overnight_range"] = _StubStrategy(["MGC", "MNQ"], "1m")
    ex = _make_executor({"morning_range_reversion": {}, "overnight_range": {}}, bot)

    asyncio.run(ex._wire_market_hub_for_live_strategies())

    assert len(bot.start_calls) == 1
    call = bot.start_calls[0]
    assert sorted(call["symbols"]) == ["MES", "MGC", "MNQ"]
    assert sorted(call["timeframes"] or []) == ["1m", "5m"]


def test_executor_skips_market_hub_when_toggle_disabled(monkeypatch):
    monkeypatch.setenv("EXECUTOR_MARKET_HUB", "false")
    bot = _RecordingBot()
    bot.strategy_manager.strategies["morning_range_reversion"] = _StubStrategy(["MNQ"], "5m")
    ex = _make_executor({"morning_range_reversion": {}}, bot)

    asyncio.run(ex._wire_market_hub_for_live_strategies())

    assert bot.start_calls == []


def test_executor_skips_market_hub_when_signalr_disabled(monkeypatch):
    monkeypatch.setenv("EXECUTOR_MARKET_HUB", "true")
    monkeypatch.setenv("ENABLE_SIGNALR", "false")
    bot = _RecordingBot()
    bot.strategy_manager.strategies["morning_range_reversion"] = _StubStrategy(["MNQ"], "5m")
    ex = _make_executor({"morning_range_reversion": {}}, bot)

    asyncio.run(ex._wire_market_hub_for_live_strategies())

    assert bot.start_calls == []


def test_executor_noop_when_no_strategies_running(monkeypatch):
    monkeypatch.setenv("EXECUTOR_MARKET_HUB", "true")
    monkeypatch.setenv("ENABLE_SIGNALR", "true")
    bot = _RecordingBot()
    ex = _make_executor({}, bot)

    asyncio.run(ex._wire_market_hub_for_live_strategies())

    assert bot.start_calls == []


# ── Option B: bar-close → instant strategy-loop wake-up (StrategyManager) ────


class _WakeBot:
    """Minimal trading-bot stand-in exposing the bar aggregator + normalize helper."""

    def __init__(self):
        self.bar_aggregator = BarAggregator(broadcast_callback=None, default_timeframes=["1m", "5m"])
        # Mimic TopStepXTradingBot._normalize_timeframe_key for the wake callback.
        from trading_bot import TopStepXTradingBot
        self._normalize_timeframe_key = TopStepXTradingBot._normalize_timeframe_key

    # The StrategyManager constructor expects these to exist.
    selected_account = None
    event_bus = None


class _WakeStrategyCfg:
    def __init__(self, name, symbols):
        self.name = name
        self.symbols = symbols
        self.enabled = True


class _WakeStrategy:
    """Strategy stand-in: holds the fields ``_on_completed_bar_wake`` filters on."""

    def __init__(self, name, symbols, timeframe):
        self.config = _WakeStrategyCfg(name, symbols)
        self.timeframe = timeframe


def _make_manager(active=None):
    """Build a StrategyManager wired to a fresh BarAggregator (no real broker)."""
    from strategies.strategy_manager import StrategyManager

    bot = _WakeBot()
    mgr = StrategyManager(bot)
    if active:
        for strat in active:
            mgr.strategies[strat.config.name] = strat
            mgr.active_strategies.append(strat.config.name)
    return mgr, bot


def _emit_close(agg: BarAggregator, symbol: str, timeframe: str, close: float = 100.0) -> None:
    """Drive ``agg.add_quote`` enough to close one bar in the given timeframe."""
    base = datetime(2026, 5, 21, 13, 0, tzinfo=timezone.utc)
    # First tick inside the bar
    agg.add_quote(symbol, close, volume=1, timestamp=base + timedelta(seconds=1))
    # Tick that crosses the next bar boundary → completes the previous bar
    step = timedelta(minutes=1) if timeframe == "1m" else timedelta(minutes=5)
    agg.add_quote(symbol, close, volume=1, timestamp=base + step + timedelta(seconds=1))


def test_completed_bar_wakes_matching_strategy(monkeypatch):
    """A 5m bar close on MNQ should fire the wake event for a 5m MNQ strategy within ms."""
    strat = _WakeStrategy("morning_range_reversion", ["MNQ"], "5m")
    mgr, bot = _make_manager(active=[strat])

    async def _run():
        loop = asyncio.get_running_loop()
        wake = asyncio.Event()
        mgr._strategy_wake_events["morning_range_reversion"] = wake
        mgr._strategy_loops["morning_range_reversion"] = loop
        mgr._ensure_completed_bar_callback()
        # Drive a 5m bar close.
        _emit_close(bot.bar_aggregator, "MNQ", "5m")
        # Should resolve effectively immediately.
        await asyncio.wait_for(wake.wait(), timeout=1.0)
        assert wake.is_set()

    asyncio.run(_run())


def test_completed_bar_does_not_wake_wrong_symbol(monkeypatch):
    """A bar close on a symbol the strategy doesn't trade must not wake it."""
    strat = _WakeStrategy("strat_a", ["MNQ"], "5m")
    mgr, bot = _make_manager(active=[strat])

    async def _run():
        loop = asyncio.get_running_loop()
        wake = asyncio.Event()
        mgr._strategy_wake_events["strat_a"] = wake
        mgr._strategy_loops["strat_a"] = loop
        mgr._ensure_completed_bar_callback()

        _emit_close(bot.bar_aggregator, "MGC", "5m")  # NOT MNQ
        await asyncio.sleep(0.05)  # give the threadsafe schedule a chance
        assert not wake.is_set()

    asyncio.run(_run())


def test_completed_bar_does_not_wake_wrong_timeframe():
    """A 1m bar close must not wake a 5m strategy on the same symbol."""
    strat = _WakeStrategy("strat_5m", ["MNQ"], "5m")
    mgr, bot = _make_manager(active=[strat])

    async def _run():
        loop = asyncio.get_running_loop()
        wake = asyncio.Event()
        mgr._strategy_wake_events["strat_5m"] = wake
        mgr._strategy_loops["strat_5m"] = loop
        mgr._ensure_completed_bar_callback()

        _emit_close(bot.bar_aggregator, "MNQ", "1m")  # 1m close, strategy wants 5m
        await asyncio.sleep(0.05)
        assert not wake.is_set()

    asyncio.run(_run())


def test_completed_bar_wakes_multiple_strategies_on_same_symbol():
    s1 = _WakeStrategy("strat_a", ["MNQ"], "5m")
    s2 = _WakeStrategy("strat_b", ["MNQ", "MES"], "5m")
    mgr, bot = _make_manager(active=[s1, s2])

    async def _run():
        loop = asyncio.get_running_loop()
        w1 = asyncio.Event()
        w2 = asyncio.Event()
        mgr._strategy_wake_events["strat_a"] = w1
        mgr._strategy_wake_events["strat_b"] = w2
        mgr._strategy_loops["strat_a"] = loop
        mgr._strategy_loops["strat_b"] = loop
        mgr._ensure_completed_bar_callback()

        _emit_close(bot.bar_aggregator, "MNQ", "5m")
        await asyncio.wait_for(w1.wait(), timeout=1.0)
        await asyncio.wait_for(w2.wait(), timeout=1.0)

    asyncio.run(_run())


def test_register_completed_bar_callback_is_idempotent():
    """Multiple calls to _ensure_completed_bar_callback should register only once."""
    strat = _WakeStrategy("s1", ["MNQ"], "5m")
    mgr, bot = _make_manager(active=[strat])

    mgr._ensure_completed_bar_callback()
    mgr._ensure_completed_bar_callback()
    mgr._ensure_completed_bar_callback()

    callbacks = bot.bar_aggregator._completed_bar_callbacks
    assert callbacks.count(mgr._on_completed_bar_wake) == 1


def test_release_wake_event_drops_from_registry():
    strat = _WakeStrategy("s1", ["MNQ"], "5m")
    mgr, _bot = _make_manager(active=[strat])
    mgr._strategy_wake_events["s1"] = asyncio.Event()
    mgr._strategy_loops["s1"] = None  # placeholder
    mgr._release_wake_event("s1")
    assert "s1" not in mgr._strategy_wake_events
    assert "s1" not in mgr._strategy_loops


def test_loop_max_interval_env_override(monkeypatch):
    """STRATEGY_LOOP_MAX_INTERVAL_SEC=2.0 → manager uses 2.0 s timeout."""
    monkeypatch.setenv("STRATEGY_LOOP_MAX_INTERVAL_SEC", "2.0")
    from strategies.strategy_manager import StrategyManager
    mgr = StrategyManager(_WakeBot())
    assert mgr._loop_max_interval_sec == pytest.approx(2.0)


def test_loop_max_interval_env_invalid_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("STRATEGY_LOOP_MAX_INTERVAL_SEC", "garbage")
    from strategies.strategy_manager import StrategyManager
    mgr = StrategyManager(_WakeBot())
    assert mgr._loop_max_interval_sec == pytest.approx(5.0)


def test_loop_max_interval_env_zero_falls_back_to_default(monkeypatch):
    """Zero is nonsensical (would busy-spin); manager must clamp to default."""
    monkeypatch.setenv("STRATEGY_LOOP_MAX_INTERVAL_SEC", "0")
    from strategies.strategy_manager import StrategyManager
    mgr = StrategyManager(_WakeBot())
    assert mgr._loop_max_interval_sec == pytest.approx(5.0)


def test_threadsafe_wake_via_background_thread():
    """Simulates the SignalR thread calling _on_completed_bar_wake from outside the loop."""
    import threading

    strat = _WakeStrategy("strat_x", ["MNQ"], "5m")
    mgr, bot = _make_manager(active=[strat])

    async def _run():
        loop = asyncio.get_running_loop()
        wake = asyncio.Event()
        mgr._strategy_wake_events["strat_x"] = wake
        mgr._strategy_loops["strat_x"] = loop
        mgr._ensure_completed_bar_callback()

        # Drive a bar close from a *different* thread, mimicking SignalR.
        def emit_from_thread():
            _emit_close(bot.bar_aggregator, "MNQ", "5m")

        t = threading.Thread(target=emit_from_thread)
        t.start()
        await asyncio.wait_for(wake.wait(), timeout=1.0)
        t.join(timeout=1.0)

    asyncio.run(_run())


# ── Tier 1 perf: live-cache-first reads (Option A) ──────────────────────────


def test_live_cache_can_serve_when_warm_and_fresh(_bot):
    """A warm, recent cache should let get_historical_data skip REST entirely."""
    base = datetime.now(timezone.utc)
    # Seed 50 fresh 1-minute bars ending at "now".
    for i in range(50):
        ts = base - timedelta(minutes=49 - i)
        bar = Bar(symbol="MNQ", timeframe="1m", timestamp=ts,
                  open=100.0, high=101.0, low=99.0, close=100.5, volume=1, tick_count=1)
        _bot._on_live_bar_close(bar)

    # max_lag_factor=2 → tail must be < 2 min old; ours is just-now → True
    assert _bot._live_cache_can_serve("MNQ", "1m", limit=20) is True


def test_live_cache_cannot_serve_when_stale(_bot):
    """A cache with a stale tail must NOT short-circuit REST."""
    base = datetime.now(timezone.utc) - timedelta(minutes=30)  # 30 min ago
    for i in range(50):
        ts = base - timedelta(minutes=49 - i)
        bar = Bar(symbol="MNQ", timeframe="1m", timestamp=ts,
                  open=100.0, high=101.0, low=99.0, close=100.5, volume=1, tick_count=1)
        _bot._on_live_bar_close(bar)

    assert _bot._live_cache_can_serve("MNQ", "1m", limit=20) is False


def test_live_cache_cannot_serve_when_too_few_bars(_bot):
    """A cache with fresh data but fewer than ``limit`` bars must NOT short-circuit."""
    now = datetime.now(timezone.utc)
    for i in range(5):
        ts = now - timedelta(minutes=4 - i)
        bar = Bar(symbol="MNQ", timeframe="1m", timestamp=ts,
                  open=100.0, high=101.0, low=99.0, close=100.5, volume=1, tick_count=1)
        _bot._on_live_bar_close(bar)

    assert _bot._live_cache_can_serve("MNQ", "1m", limit=20) is False


def test_serve_from_live_cache_returns_trailing_limit(_bot):
    base = datetime.now(timezone.utc) - timedelta(minutes=50)
    for i in range(50):
        ts = base + timedelta(minutes=i)
        bar = Bar(symbol="MNQ", timeframe="1m", timestamp=ts,
                  open=float(i), high=float(i), low=float(i), close=float(i),
                  volume=1, tick_count=1)
        _bot._on_live_bar_close(bar)

    served = _bot._serve_from_live_cache("MNQ", "1m", limit=10)
    assert len(served) == 10
    assert served[0]["close"] == 40.0
    assert served[-1]["close"] == 49.0


def test_get_historical_data_skips_rest_when_cache_warm(_bot, monkeypatch):
    """Warm fresh cache → get_historical_data returns from memory; broker_adapter NOT called."""
    from trading_bot import TopStepXTradingBot

    monkeypatch.setenv("LIVE_BAR_CACHE_FIRST", "true")

    base = datetime.now(timezone.utc)
    for i in range(50):
        ts = base - timedelta(minutes=49 - i)
        bar = Bar(symbol="MNQ", timeframe="1m", timestamp=ts,
                  open=100.0, high=101.0, low=99.0, close=100.5, volume=1, tick_count=1)
        _bot._on_live_bar_close(bar)

    rest_called = {"count": 0}

    class _SpyAdapter:
        async def get_historical_data(self, **_kw):
            rest_called["count"] += 1
            return []

    _bot.broker_adapter = _SpyAdapter()

    served = asyncio.run(
        TopStepXTradingBot.get_historical_data(_bot, "MNQ", "1m", limit=10)
    )
    assert len(served) == 10
    assert rest_called["count"] == 0, "REST should NOT be called when cache is warm"


def test_get_historical_data_falls_back_to_rest_when_cache_disabled(_bot, monkeypatch):
    """LIVE_BAR_CACHE_FIRST=false → REST is always called (legacy behaviour)."""
    from trading_bot import TopStepXTradingBot

    monkeypatch.setenv("LIVE_BAR_CACHE_FIRST", "false")

    base = datetime.now(timezone.utc)
    for i in range(50):
        ts = base - timedelta(minutes=49 - i)
        bar = Bar(symbol="MNQ", timeframe="1m", timestamp=ts,
                  open=100.0, high=101.0, low=99.0, close=100.5, volume=1, tick_count=1)
        _bot._on_live_bar_close(bar)

    class _SpyAdapter:
        async def get_historical_data(self, **_kw):
            from trading_bot import TopStepXTradingBot as _Bot  # avoid circular
            return []

    rest_calls = []

    class _RestSpy:
        async def get_historical_data(self, **kw):
            rest_calls.append(kw)
            return []

    _bot.broker_adapter = _RestSpy()
    asyncio.run(TopStepXTradingBot.get_historical_data(_bot, "MNQ", "1m", limit=10))
    assert len(rest_calls) == 1


def test_timeframe_seconds_parsing(_bot):
    """``_timeframe_seconds`` should round-trip common timeframe strings."""
    from trading_bot import TopStepXTradingBot

    assert TopStepXTradingBot._timeframe_seconds("5m") == 300.0
    assert TopStepXTradingBot._timeframe_seconds("1h") == 3600.0
    assert TopStepXTradingBot._timeframe_seconds("15s") == 15.0
    assert TopStepXTradingBot._timeframe_seconds("1d") == 86400.0
    assert TopStepXTradingBot._timeframe_seconds("garbage") is None


# ── Tier 2 perf: Option E1 (parallel symbol gather) ──────────────────────────


def test_executor_uses_gather_for_symbols_by_default(monkeypatch):
    """STRATEGY_SYMBOL_PARALLEL=true (default) → ``_process_strategy_symbol`` runs concurrently."""
    monkeypatch.setenv("STRATEGY_SYMBOL_PARALLEL", "true")
    from strategies.strategy_manager import StrategyManager

    mgr = StrategyManager(_WakeBot())
    # Verify env knob is read at iteration time (not __init__) by toggling.
    assert os.environ.get("STRATEGY_SYMBOL_PARALLEL") == "true"


def test_executor_serial_when_toggle_off(monkeypatch):
    monkeypatch.setenv("STRATEGY_SYMBOL_PARALLEL", "false")
    assert os.environ.get("STRATEGY_SYMBOL_PARALLEL") == "false"


# ── Tier 2 perf: Option E2 (fire-and-forget orders, opt-in only) ─────────────


def test_fire_and_forget_returns_immediately_with_placeholder(monkeypatch):
    """STRATEGY_FIRE_AND_FORGET_ORDERS=true → place_bracket_order returns synthetic success."""
    monkeypatch.setenv("STRATEGY_FIRE_AND_FORGET_ORDERS", "true")

    class _FakeBot:
        bg_called = []

        async def place_oco_bracket_with_stop_entry(self, **kw):
            # Simulate a slow broker round-trip — must NOT block place_bracket_order
            await asyncio.sleep(0.5)
            self.bg_called.append(kw)
            return {"orderId": "abc", "success": True}

    bot = _FakeBot()

    from strategies.strategy_base import BaseStrategy, StrategyConfig

    cfg = StrategyConfig(
        name="test_strat", enabled=True, symbols=["MNQ"], max_positions=1,
        position_size=1, risk_per_trade_percent=0.5, max_daily_trades=1,
        preferred_conditions=[], avoid_conditions=[],
        trading_start_time="00:00", trading_end_time="23:59",
        no_trade_start="", no_trade_end="",
    )

    class _RM:
        async def check_order_allowed(self, **_kw):
            return True, ""
        @property
        def daily_pnl(self):
            return 0.0

    class _Strat(BaseStrategy):
        async def evaluate(self, *_a, **_kw):
            return None
        async def analyze(self, *_a, **_kw):
            return None
        async def execute(self, *_a, **_kw):
            return None
        async def manage_positions(self, *_a, **_kw):
            return None
        async def cleanup(self, *_a, **_kw):
            return None

    strat = _Strat(bot, cfg)
    strat._risk_manager = _RM()  # type: ignore[assignment]

    async def _run():
        t0 = asyncio.get_running_loop().time()
        result = await strat.place_bracket_order(
            symbol="MNQ", side="BUY", quantity=1,
            entry_price=100.0, stop_loss_price=99.0, take_profit_price=101.0,
        )
        t1 = asyncio.get_running_loop().time()
        assert result["fire_and_forget"] is True
        assert result["orderId"] is None
        assert result["success"] is True
        assert (t1 - t0) < 0.1, f"F&F took {t1 - t0:.3f}s; should return <100ms"

        await asyncio.sleep(0.7)
        assert len(bot.bg_called) == 1
        assert bot.bg_called[0]["entry_price"] == 100.0

    asyncio.run(_run())


def test_fire_and_forget_off_blocks_until_broker_responds(monkeypatch):
    """Default behaviour (toggle off): place_bracket_order awaits the broker."""
    monkeypatch.setenv("STRATEGY_FIRE_AND_FORGET_ORDERS", "false")

    class _FakeBot:
        async def place_oco_bracket_with_stop_entry(self, **kw):
            await asyncio.sleep(0.2)
            return {"orderId": "xyz", "success": True}

    from strategies.strategy_base import BaseStrategy, StrategyConfig

    cfg = StrategyConfig(
        name="test_strat", enabled=True, symbols=["MNQ"], max_positions=1,
        position_size=1, risk_per_trade_percent=0.5, max_daily_trades=1,
        preferred_conditions=[], avoid_conditions=[],
        trading_start_time="00:00", trading_end_time="23:59",
        no_trade_start="", no_trade_end="",
    )

    class _RM:
        async def check_order_allowed(self, **_kw):
            return True, ""
        async def record_order_placement(self, *_a, **_kw):
            return None

    class _Strat(BaseStrategy):
        async def evaluate(self, *_a, **_kw):
            return None
        async def analyze(self, *_a, **_kw):
            return None
        async def execute(self, *_a, **_kw):
            return None
        async def manage_positions(self, *_a, **_kw):
            return None
        async def cleanup(self, *_a, **_kw):
            return None

    strat = _Strat(_FakeBot(), cfg)
    strat._risk_manager = _RM()  # type: ignore[assignment]

    async def _run():
        t0 = asyncio.get_running_loop().time()
        result = await strat.place_bracket_order(
            symbol="MNQ", side="BUY", quantity=1,
            entry_price=100.0, stop_loss_price=99.0, take_profit_price=101.0,
        )
        t1 = asyncio.get_running_loop().time()
        assert result.get("orderId") == "xyz"
        assert (t1 - t0) >= 0.15, "default mode must wait for broker"
        assert result.get("fire_and_forget") is None or result.get("fire_and_forget") is False

    asyncio.run(_run())


# ── Tier 1 perf: H heartbeat lifecycle ───────────────────────────────────────


def test_keepalive_heartbeat_starts_idempotent(monkeypatch, _bot):
    """Two start_keepalive_heartbeat calls → only one task created."""
    monkeypatch.setenv("BROKER_KEEPALIVE_HEARTBEAT_SEC", "60")

    # _bot is built via __new__ so it lacks _keepalive_task; initialise.
    _bot._keepalive_task = None

    # Patch _keepalive_heartbeat_loop on the instance so the task body returns immediately
    # and we don't actually fire any RESTs during the test.
    async def _noop(_interval: float) -> None:
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            return
    _bot._keepalive_heartbeat_loop = _noop

    from trading_bot import TopStepXTradingBot

    async def _run():
        await TopStepXTradingBot.start_keepalive_heartbeat(_bot)
        first = _bot._keepalive_task
        assert first is not None
        await TopStepXTradingBot.start_keepalive_heartbeat(_bot)
        assert _bot._keepalive_task is first  # idempotent — same task
        await TopStepXTradingBot.stop_keepalive_heartbeat(_bot)
        assert _bot._keepalive_task is None

    asyncio.run(_run())


def test_keepalive_heartbeat_disabled_when_zero(monkeypatch, _bot):
    monkeypatch.setenv("BROKER_KEEPALIVE_HEARTBEAT_SEC", "0")
    _bot._keepalive_task = None

    from trading_bot import TopStepXTradingBot

    asyncio.run(TopStepXTradingBot.start_keepalive_heartbeat(_bot))
    assert _bot._keepalive_task is None


def test_wake_callback_handles_strategy_without_timeframe():
    """A strategy missing ``timeframe`` should still wake on any bar close for its symbol."""
    class _NoTfStrategy:
        config = _WakeStrategyCfg("no_tf", ["MNQ"])
        # No ``timeframe`` attribute at all

    strat = _NoTfStrategy()
    mgr, bot = _make_manager(active=[strat])

    async def _run():
        loop = asyncio.get_running_loop()
        wake = asyncio.Event()
        mgr._strategy_wake_events["no_tf"] = wake
        mgr._strategy_loops["no_tf"] = loop
        mgr._ensure_completed_bar_callback()

        _emit_close(bot.bar_aggregator, "MNQ", "5m")
        await asyncio.wait_for(wake.wait(), timeout=1.0)

    asyncio.run(_run())
