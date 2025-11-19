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
        assert aggregator.update_interval == 0.2
        assert aggregator._running is False
        assert len(aggregator.bar_builders) == 0
    
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
        """Test starting and stopping aggregator"""
        assert aggregator._running is False
        
        await aggregator.start()
        assert aggregator._running is True
        assert aggregator._update_task is not None
        
        await aggregator.stop()
        assert aggregator._running is False
    
    @pytest.mark.asyncio
    async def test_broadcast_callback(self):
        """Test broadcast callback is called with bar updates"""
        callback_calls = []
        
        def mock_callback(message):
            callback_calls.append(message)
        
        aggregator = BarAggregator(broadcast_callback=mock_callback)
        aggregator.subscribe_timeframe('MNQ', '5m')
        aggregator.add_quote('MNQ', 15000.0, volume=100)
        
        # Trigger broadcast
        await aggregator._broadcast_updates()
        
        # Wait a bit for async operations
        await asyncio.sleep(0.1)
        
        # Callback should have been called
        assert len(callback_calls) > 0
        message = callback_calls[0]
        assert message['type'] == 'market_update'
        assert message['data']['symbol'] == 'MNQ'
        assert message['data']['timeframe'] == '5m'
        assert 'bar' in message['data']


if __name__ == '__main__':
    pytest.main([__file__, '-v'])

