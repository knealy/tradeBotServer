"""
Unit tests for real-time bar aggregator
"""

import pytest
import asyncio
from unittest.mock import Mock, MagicMock, patch
from datetime import datetime, timezone, timedelta
import os
import sys

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.bar_aggregator import BarAggregator, Bar, BarBuilder


class TestBarBuilder:
    """Test BarBuilder class"""
    
    def test_bar_builder_initialization(self):
        """Test bar builder initialization"""
        now = datetime.now(timezone.utc)
        builder = BarBuilder('MNQ', '5m', now)
        
        assert builder.symbol == 'MNQ'
        assert builder.timeframe == '5m'
        assert builder.bar_start == now
        assert builder.open is None
        assert builder.high is None
        assert builder.low is None
        assert builder.close is None
        assert builder.volume == 0
        assert builder.tick_count == 0
    
    def test_add_tick(self):
        """Test adding ticks to bar builder"""
        now = datetime.now(timezone.utc)
        builder = BarBuilder('MNQ', '5m', now)
        
        # Add first tick
        builder.add_tick(15000.0, volume=100)
        assert builder.open == 15000.0
        assert builder.high == 15000.0
        assert builder.low == 15000.0
        assert builder.close == 15000.0
        assert builder.volume == 100
        assert builder.tick_count == 1
        
        # Add higher tick
        builder.add_tick(15050.0, volume=50)
        assert builder.open == 15000.0
        assert builder.high == 15050.0
        assert builder.low == 15000.0
        assert builder.close == 15050.0
        assert builder.volume == 150
        assert builder.tick_count == 2
        
        # Add lower tick
        builder.add_tick(14980.0, volume=75)
        assert builder.high == 15050.0
        assert builder.low == 14980.0
        assert builder.close == 14980.0
        assert builder.volume == 225
        assert builder.tick_count == 3
    
    def test_to_bar(self):
        """Test converting builder to bar"""
        now = datetime.now(timezone.utc)
        builder = BarBuilder('MNQ', '5m', now)
        
        builder.add_tick(15000.0, volume=100)
        builder.add_tick(15050.0, volume=50)
        builder.add_tick(14980.0, volume=75)
        
        bar = builder.to_bar()
        
        assert isinstance(bar, Bar)
        assert bar.symbol == 'MNQ'
        assert bar.timeframe == '5m'
        assert bar.open == 15000.0
        assert bar.high == 15050.0
        assert bar.low == 14980.0
        assert bar.close == 14980.0
        assert bar.volume == 225
        assert bar.tick_count == 3
    
    def test_to_bar_no_data(self):
        """Test converting empty builder raises error"""
        now = datetime.now(timezone.utc)
        builder = BarBuilder('MNQ', '5m', now)
        
        with pytest.raises(ValueError, match="Bar has no data"):
            builder.to_bar()


class TestBarAggregator:
    """Test BarAggregator class"""
    
    @pytest.fixture
    def aggregator(self):
        """Create a bar aggregator instance"""
        return BarAggregator(broadcast_callback=None)
    
    def test_aggregator_initialization(self, aggregator):
        """Test aggregator initialization"""
        assert aggregator.broadcast_callback is None
        assert aggregator._running is False
        assert len(aggregator.bar_builders) == 0
        # Periodic-completion safety net wiring (2026-06-12)
        assert aggregator._periodic_completion_task is None
        assert aggregator._periodic_completions_total == 0
    
    def test_subscribe_timeframe(self, aggregator):
        """Test subscribing to a timeframe"""
        aggregator.subscribe_timeframe('MNQ', '5m')
        
        assert 'MNQ' in aggregator.bar_builders
        assert '5m' in aggregator.bar_builders['MNQ']
        builder = aggregator.bar_builders['MNQ']['5m']
        assert builder.symbol == 'MNQ'
        assert builder.timeframe == '5m'
    
    def test_unsubscribe_timeframe(self, aggregator):
        """Test unsubscribing from a timeframe"""
        aggregator.subscribe_timeframe('MNQ', '5m')
        aggregator.subscribe_timeframe('MNQ', '1m')
        
        assert '5m' in aggregator.bar_builders['MNQ']
        assert '1m' in aggregator.bar_builders['MNQ']
        
        aggregator.unsubscribe_timeframe('MNQ', '5m')
        
        assert '5m' not in aggregator.bar_builders['MNQ']
        assert '1m' in aggregator.bar_builders['MNQ']
        
        aggregator.unsubscribe_timeframe('MNQ', '1m')
        
        assert 'MNQ' not in aggregator.bar_builders
    
    def test_add_quote(self, aggregator):
        """Test adding quotes to aggregator"""
        aggregator.subscribe_timeframe('MNQ', '5m')
        
        # Add quotes
        aggregator.add_quote('MNQ', 15000.0, volume=100)
        aggregator.add_quote('MNQ', 15050.0, volume=50)
        aggregator.add_quote('MNQ', 14980.0, volume=75)
        
        builder = aggregator.bar_builders['MNQ']['5m']
        assert builder.open == 15000.0
        assert builder.high == 15050.0
        assert builder.low == 14980.0
        assert builder.close == 14980.0
        assert builder.volume == 225
        assert builder.tick_count == 3
    
    def test_add_quote_multiple_timeframes(self, aggregator):
        """Test adding quotes updates multiple timeframes"""
        aggregator.subscribe_timeframe('MNQ', '1m')
        aggregator.subscribe_timeframe('MNQ', '5m')
        
        aggregator.add_quote('MNQ', 15000.0, volume=100)
        
        builder_1m = aggregator.bar_builders['MNQ']['1m']
        builder_5m = aggregator.bar_builders['MNQ']['5m']
        
        assert builder_1m.close == 15000.0
        assert builder_5m.close == 15000.0
    
    def test_get_current_bar(self, aggregator):
        """Test getting current forming bar"""
        aggregator.subscribe_timeframe('MNQ', '5m')
        aggregator.add_quote('MNQ', 15000.0, volume=100)
        
        bar = aggregator.get_current_bar('MNQ', '5m')
        
        assert bar is not None
        assert bar.symbol == 'MNQ'
        assert bar.timeframe == '5m'
        assert bar.close == 15000.0
    
    def test_get_current_bar_not_subscribed(self, aggregator):
        """Test getting current bar when not subscribed"""
        bar = aggregator.get_current_bar('MNQ', '5m')
        assert bar is None
    
    def test_get_bar_start_time_5m(self, aggregator):
        """Test bar start time calculation for 5m timeframe"""
        timestamp = datetime(2025, 11, 19, 10, 17, 30, tzinfo=timezone.utc)
        bar_start = aggregator._get_bar_start_time(timestamp, '5m')
        
        # Should round down to 10:15:00
        assert bar_start.minute == 15
        assert bar_start.second == 0
    
    def test_get_bar_start_time_1m(self, aggregator):
        """Test bar start time calculation for 1m timeframe"""
        timestamp = datetime(2025, 11, 19, 10, 17, 30, tzinfo=timezone.utc)
        bar_start = aggregator._get_bar_start_time(timestamp, '1m')
        
        # Should round down to 10:17:00
        assert bar_start.minute == 17
        assert bar_start.second == 0
    
    def test_should_start_new_bar(self, aggregator):
        """Test detecting when to start a new bar"""
        now = datetime.now(timezone.utc)
        bar_start = aggregator._get_bar_start_time(now, '5m')
        builder = BarBuilder('MNQ', '5m', bar_start)
        
        # Current time is within the same bar
        should_start = aggregator._should_start_new_bar(builder, '5m', now)
        assert should_start is False
        
        # Time 6 minutes later should start new bar
        future_time = now + timedelta(minutes=6)
        should_start = aggregator._should_start_new_bar(builder, '5m', future_time)
        assert should_start is True
    
    @pytest.mark.asyncio
    async def test_start_stop(self, aggregator):
        """Test starting and stopping aggregator (event-driven, no _update_task)."""
        assert aggregator._running is False

        await aggregator.start()
        assert aggregator._running is True
        # 2026-06-12 update: the periodic-completion safety net is the only
        # background task the aggregator owns now.
        assert aggregator._periodic_completion_task is not None
        assert not aggregator._periodic_completion_task.done()

        await aggregator.stop()
        assert aggregator._running is False
        assert aggregator._periodic_completion_task is None
    
    @pytest.mark.asyncio
    async def test_broadcast_callback(self):
        """Test broadcast callback is called with partial-bar updates.

        2026-06-12 update: rewrote against the current event-driven API.
        ``_broadcast_updates`` no longer exists — broadcasts now fire from
        ``add_quote`` via ``_enqueue_broadcast``/``_emit_partial_bar`` on the
        aggregator's running event loop.
        """
        callback_calls = []

        def mock_callback(message):
            callback_calls.append(message)

        aggregator = BarAggregator(broadcast_callback=mock_callback)
        # Disable the periodic safety net so it doesn't add noise to this test.
        aggregator._periodic_completion_interval = 0
        await aggregator.start()
        try:
            aggregator.subscribe_timeframe('MNQ', '5m')
            aggregator.add_quote('MNQ', 15000.0, volume=100)
            # Yield so the loop.call_soon_threadsafe-scheduled broadcast can run.
            await asyncio.sleep(0.05)
        finally:
            await aggregator.stop()

        assert len(callback_calls) > 0
        message = callback_calls[0]
        assert message['type'] == 'market_update'
        assert message['data']['symbol'] == 'MNQ'
        assert message['data']['timeframe'] == '5m'
        assert 'bar' in message['data']


class TestPeriodicBarCompletion:
    """Tests for the 2026-06-12 periodic-bar-completion safety net.

    This safety net force-closes builders whose ``bar_end`` has elapsed even
    when no fresh tick has arrived — closes the SignalR quote dead-zone race.
    """

    def _make_aggregator(self, interval: float = 10.0) -> BarAggregator:
        """Build an aggregator with an explicit completion interval (no env)."""
        agg = BarAggregator()
        agg._periodic_completion_interval = interval
        return agg

    @staticmethod
    def _seed_stale_builder(
        agg: BarAggregator,
        symbol: str,
        timeframe: str,
        price: float = 100.0,
        minutes_ago: int = 30,
    ) -> BarBuilder:
        """Directly install a builder whose bar_start is well in the past.

        We can't get this state via ``add_quote(timestamp=past)`` because
        ``subscribe_timeframe`` initialises a builder rooted at NOW and
        ``add_quote`` only rolls over the bar_start via the
        ``_should_start_new_bar`` boundary check — a past timestamp won't
        advance bar_start backwards. So we seed directly.
        """
        sym = symbol.upper()
        past = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
        bar_start = agg._get_bar_start_time(past, timeframe)
        builder = BarBuilder(sym, timeframe, bar_start)
        builder.add_tick(price, 1, past)
        agg.symbol_timeframes[sym].add(timeframe)
        agg.bar_builders[sym][timeframe] = builder
        return builder

    def test_init_reads_env_var(self, monkeypatch):
        monkeypatch.setenv("BAR_PERIODIC_COMPLETION_INTERVAL", "3.5")
        agg = BarAggregator()
        assert agg._periodic_completion_interval == pytest.approx(3.5)

    def test_init_handles_bad_env_var(self, monkeypatch):
        monkeypatch.setenv("BAR_PERIODIC_COMPLETION_INTERVAL", "not-a-number")
        agg = BarAggregator()
        assert agg._periodic_completion_interval == 10.0

    def test_init_defaults_to_10s(self, monkeypatch):
        monkeypatch.delenv("BAR_PERIODIC_COMPLETION_INTERVAL", raising=False)
        agg = BarAggregator()
        assert agg._periodic_completion_interval == 10.0

    def test_sweep_skips_builders_with_no_data(self):
        agg = self._make_aggregator()
        agg.subscribe_timeframe("MNQ", "5m")
        # Builder exists but ``open`` is still None
        agg._sweep_force_complete()
        assert agg._periodic_completions_total == 0

    def test_sweep_skips_builders_still_in_window(self):
        agg = self._make_aggregator()
        agg.subscribe_timeframe("MNQ", "5m")
        # add_quote uses ``datetime.now(timezone.utc)`` internally so the
        # builder's window is fresh — sweep should NOT close it.
        agg.add_quote("MNQ", 15000.0, volume=10)
        agg._sweep_force_complete()
        assert agg._periodic_completions_total == 0

    def test_sweep_force_closes_stale_builder(self):
        """Builder whose bar_end is in the past gets force-closed."""
        agg = self._make_aggregator()
        completed_bars = []
        agg.register_completed_bar_callback(lambda b: completed_bars.append(b))
        builder_before = self._seed_stale_builder(agg, "MNQ", "5m", price=15000.0)

        agg._sweep_force_complete()

        assert agg._periodic_completions_total == 1
        assert len(completed_bars) == 1
        bar = completed_bars[0]
        assert bar.symbol == "MNQ"
        assert bar.timeframe == "5m"
        assert bar.close == 15000.0
        # A fresh empty builder should now sit on the current boundary.
        builder_after = agg.bar_builders["MNQ"]["5m"]
        assert builder_after.open is None
        assert builder_after.bar_start > builder_before.bar_start

    def test_sweep_handles_multiple_symbols_and_timeframes(self):
        agg = self._make_aggregator()
        seen = []
        agg.register_completed_bar_callback(lambda b: seen.append((b.symbol, b.timeframe)))
        self._seed_stale_builder(agg, "MNQ", "5m", price=15000.0)
        self._seed_stale_builder(agg, "MGC", "5m", price=2050.0)
        # MGC 1m subscribed but with no tick (builder.open is None) — should be skipped.
        agg.subscribe_timeframe("MGC", "1m")

        agg._sweep_force_complete()

        assert agg._periodic_completions_total == 2
        assert ("MNQ", "5m") in seen
        assert ("MGC", "5m") in seen
        assert ("MGC", "1m") not in seen

    def test_sweep_is_idempotent_across_calls(self):
        agg = self._make_aggregator()
        self._seed_stale_builder(agg, "MNQ", "5m", price=15000.0)
        agg._sweep_force_complete()
        # Second sweep finds the fresh empty builder — should NOT re-close.
        agg._sweep_force_complete()
        assert agg._periodic_completions_total == 1

    def test_sweep_isolates_callback_exceptions(self):
        """A misbehaving subscriber must not break the sweep."""
        agg = self._make_aggregator()
        ok_seen = []

        def bad_cb(_bar):
            raise RuntimeError("subscriber crashed")

        agg.register_completed_bar_callback(bad_cb)
        agg.register_completed_bar_callback(lambda b: ok_seen.append(b))
        self._seed_stale_builder(agg, "MNQ", "5m", price=15000.0)

        agg._sweep_force_complete()
        # Bad subscriber raised, but the sweep itself + the other subscriber
        # still completed the bar.
        assert len(ok_seen) == 1
        assert agg._periodic_completions_total == 1

    @pytest.mark.asyncio
    async def test_periodic_task_starts_and_stops(self):
        """``start()`` should spawn the task; ``stop()`` should cancel it."""
        agg = self._make_aggregator(interval=0.05)
        await agg.start()
        try:
            assert agg._periodic_completion_task is not None
            assert not agg._periodic_completion_task.done()
        finally:
            await agg.stop()
        assert agg._periodic_completion_task is None

    @pytest.mark.asyncio
    async def test_periodic_task_force_closes_stale_builder(self):
        """End-to-end: run the periodic task and verify it closes a stale builder."""
        agg = self._make_aggregator(interval=0.05)
        seen: list[Bar] = []
        agg.register_completed_bar_callback(lambda b: seen.append(b))

        await agg.start()
        try:
            self._seed_stale_builder(agg, "MNQ", "5m", price=15000.0)
            # Wait long enough for at least one periodic sweep.
            await asyncio.sleep(0.20)
        finally:
            await agg.stop()

        assert len(seen) >= 1
        assert agg._periodic_completions_total >= 1
        assert seen[0].symbol == "MNQ"
        assert seen[0].timeframe == "5m"

    @pytest.mark.asyncio
    async def test_periodic_task_disabled_when_interval_zero(self):
        """``BAR_PERIODIC_COMPLETION_INTERVAL=0`` disables the safety net."""
        agg = self._make_aggregator(interval=0.0)
        await agg.start()
        try:
            assert agg._periodic_completion_task is None
        finally:
            await agg.stop()


if __name__ == '__main__':
    pytest.main([__file__, '-v'])

