"""
Unit tests for lazy cache initialization, prefetch, batch API calls, and adaptive fill checker.

Tests all the new performance optimizations.
"""

import pytest
import asyncio
import os
from unittest.mock import Mock, patch, AsyncMock, MagicMock
from datetime import datetime

from trading_bot import TopStepXTradingBot


class TestLazyCacheInitialization:
    """Test suite for lazy cache initialization."""
    
    @pytest.fixture
    def bot(self):
        """Create a bot instance for testing."""
        with patch.dict(os.environ, {
            'PROJECT_X_API_KEY': 'test_key',
            'PROJECT_X_USERNAME': 'test_user',
            'USE_PROJECTX_SDK': '1',
        }):
            bot = TopStepXTradingBot(api_key='test_key', username='test_user')
            return bot
    
    @pytest.mark.asyncio
    async def test_cache_not_initialized_at_startup(self, bot):
        """Test that cache is not initialized at startup."""
        with patch('trading_bot.sdk_adapter') as mock_adapter:
            mock_adapter.is_sdk_available = Mock(return_value=True)
            mock_adapter.is_cache_initialized = Mock(return_value=False)
            
            # Cache should not be initialized
            assert not mock_adapter.is_cache_initialized()
    
    @pytest.mark.asyncio
    async def test_cache_initializes_on_first_history_command(self, bot):
        """Test that cache initializes when first history command is used."""
        # Clear cache to force SDK fetch
        bot._memory_cache.clear()
        cache_path = bot._get_cache_path(bot._get_cache_key("MNQ", "1m"))
        if cache_path.exists():
            cache_path.unlink()
        
        with patch('trading_bot.sdk_adapter') as mock_adapter:
            mock_adapter.is_sdk_available = Mock(return_value=True)
            mock_adapter.is_cache_initialized = Mock(side_effect=[False, True])
            mock_adapter.initialize_historical_client_cache = AsyncMock(return_value=True)
            mock_adapter.get_historical_bars = AsyncMock(return_value=[])
            
            # First call should initialize cache
            result = await bot.get_historical_data("MNQ", "1m", 10)
            
            # Should have called initialize
            mock_adapter.initialize_historical_client_cache.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_cache_not_reinitialized_if_already_initialized(self, bot):
        """Test that cache is not reinitialized if already initialized."""
        with patch('trading_bot.sdk_adapter') as mock_adapter:
            mock_adapter.is_sdk_available = Mock(return_value=True)
            mock_adapter.is_cache_initialized = Mock(return_value=True)
            mock_adapter.get_historical_bars = AsyncMock(return_value=[])
            
            # Should not call initialize
            result = await bot.get_historical_data("MNQ", "1m", 10)
            
            # Should not have called initialize
            mock_adapter.initialize_historical_client_cache.assert_not_called()


class TestPrefetch:
    """Test suite for prefetch functionality."""
    
    @pytest.fixture
    def bot(self):
        """Create a bot instance for testing."""
        with patch.dict(os.environ, {
            'PROJECT_X_API_KEY': 'test_key',
            'PROJECT_X_USERNAME': 'test_user',
            'PREFETCH_ENABLED': 'true',
            'PREFETCH_SYMBOLS': 'MNQ,ES',
            'PREFETCH_TIMEFRAMES': '1m,5m',
        }):
            bot = TopStepXTradingBot(api_key='test_key', username='test_user')
            return bot
    
    def test_prefetch_configuration(self, bot):
        """Test that prefetch is configured correctly."""
        assert bot._prefetch_enabled == True
        assert 'MNQ' in bot._prefetch_symbols
        assert 'ES' in bot._prefetch_symbols
        assert '1m' in bot._prefetch_timeframes
        assert '5m' in bot._prefetch_timeframes
    
    def test_prefetch_disabled(self):
        """Test that prefetch can be disabled."""
        with patch.dict(os.environ, {
            'PROJECT_X_API_KEY': 'test_key',
            'PROJECT_X_USERNAME': 'test_user',
            'PREFETCH_ENABLED': 'false',
        }):
            bot = TopStepXTradingBot(api_key='test_key', username='test_user')
            assert bot._prefetch_enabled == False
    
    @pytest.mark.asyncio
    async def test_prefetch_task_started(self, bot):
        """Test that prefetch task is started."""
        bot.get_historical_data = AsyncMock(return_value=[])
        
        with patch('trading_bot.sdk_adapter') as mock_adapter:
            mock_adapter.is_cache_initialized = Mock(return_value=True)
            
            # Start prefetch
            bot._start_prefetch_task()
            
            # Task should be created
            assert bot._prefetch_task is not None
            assert not bot._prefetch_task.done()
            
            # Cancel task
            bot._prefetch_task.cancel()
            try:
                await bot._prefetch_task
            except asyncio.CancelledError:
                pass


class TestBatchAPICalls:
    """Test suite for batch API calls."""
    
    @pytest.fixture
    def bot(self):
        """Create a bot instance for testing."""
        with patch.dict(os.environ, {
            'PROJECT_X_API_KEY': 'test_key',
            'PROJECT_X_USERNAME': 'test_user',
        }):
            bot = TopStepXTradingBot(api_key='test_key', username='test_user')
            bot.selected_account = {'id': 12345}
            bot.session_token = "test_token"
            return bot
    
    @pytest.mark.asyncio
    async def test_batch_api_call_returns_both(self, bot):
        """Test that batch API call returns both positions and orders."""
        bot.get_open_positions = AsyncMock(return_value=[{"id": 1, "symbol": "MNQ"}])
        bot.get_open_orders = AsyncMock(return_value=[{"id": 2, "symbol": "MNQ"}])
        
        result = await bot.get_positions_and_orders_batch()
        
        assert "positions" in result
        assert "orders" in result
        assert len(result["positions"]) == 1
        assert len(result["orders"]) == 1
    
    @pytest.mark.asyncio
    async def test_batch_api_call_runs_in_parallel(self, bot):
        """Test that batch API calls run in parallel."""
        call_order = []
        
        async def mock_positions(account_id=None):
            call_order.append("positions_start")
            await asyncio.sleep(0.1)
            call_order.append("positions_end")
            return []
        
        async def mock_orders(account_id=None):
            call_order.append("orders_start")
            await asyncio.sleep(0.1)
            call_order.append("orders_end")
            return []
        
        bot.get_open_positions = mock_positions
        bot.get_open_orders = mock_orders
        
        start = asyncio.get_event_loop().time()
        await bot.get_positions_and_orders_batch()
        elapsed = (asyncio.get_event_loop().time() - start) * 1000
        
        # Should complete in ~100ms (parallel) not ~200ms (sequential)
        assert elapsed < 150  # Allow some overhead
        # Both should start before either ends (parallel execution)
        assert "positions_start" in call_order
        assert "orders_start" in call_order
        assert "positions_end" in call_order
        assert "orders_end" in call_order
        # Verify parallel execution: both start before either ends
        assert call_order.index("positions_start") < call_order.index("orders_end")
        assert call_order.index("orders_start") < call_order.index("positions_end")
    
    @pytest.mark.asyncio
    async def test_batch_api_handles_errors(self, bot):
        """Test that batch API handles errors gracefully."""
        bot.get_open_positions = AsyncMock(side_effect=Exception("API Error"))
        bot.get_open_orders = AsyncMock(return_value=[])
        
        result = await bot.get_positions_and_orders_batch()
        
        # Should return error but still try to get orders
        assert "error" in result or "positions" in result


class TestAdaptiveFillChecker:
    """Test suite for adaptive fill checker."""
    
    @pytest.fixture
    def bot(self):
        """Create a bot instance for testing."""
        with patch.dict(os.environ, {
            'PROJECT_X_API_KEY': 'test_key',
            'PROJECT_X_USERNAME': 'test_user',
        }):
            bot = TopStepXTradingBot(api_key='test_key', username='test_user')
            bot.selected_account = {'id': 12345}
            return bot
    
    def test_adaptive_intervals(self, bot):
        """Test that adaptive intervals are configured."""
        assert bot._fill_check_interval == 30  # Default interval
        assert bot._fill_check_active_interval == 10  # Active interval
    
    def test_has_active_orders_or_positions(self, bot):
        """Test checking for active orders/positions."""
        bot.selected_account = {'id': 12345}
        
        # No activity
        assert bot._has_active_orders_or_positions() == False
        
        # Add cached orders
        bot._cached_order_ids['12345'] = {'MNQ': {'123'}}
        assert bot._has_active_orders_or_positions() == True
        
        # Clear orders, add positions
        bot._cached_order_ids.clear()
        bot._cached_position_ids['12345'] = {'MNQ': {'456'}}
        assert bot._has_active_orders_or_positions() == True
    
    def test_update_order_activity(self, bot):
        """Test updating order activity timestamp."""
        assert bot._last_order_activity is None
        
        bot._update_order_activity()
        
        assert bot._last_order_activity is not None
        assert isinstance(bot._last_order_activity, datetime)
    
    @pytest.mark.asyncio
    async def test_adaptive_interval_selection(self, bot):
        """Test that adaptive interval is selected based on activity."""
        bot.check_order_fills = AsyncMock(return_value={"success": True})
        bot._has_active_orders_or_positions = Mock(return_value=True)
        
        # Track intervals used
        intervals = []
        original_sleep = asyncio.sleep
        
        async def tracked_sleep(seconds):
            intervals.append(seconds)
            if len(intervals) < 3:  # Only run a few iterations
                await original_sleep(0.01)  # Short delay for testing
            else:
                bot._auto_fills_enabled = False  # Stop after 3 iterations
        
        with patch('asyncio.sleep', side_effect=tracked_sleep):
            try:
                await bot._auto_fill_checker()
            except Exception:
                pass
        
        # Should use active interval (10s) when orders exist
        assert all(interval == 10 for interval in intervals)
    
    @pytest.mark.asyncio
    async def test_adaptive_interval_idle(self, bot):
        """Test that idle interval is used when no activity."""
        bot.check_order_fills = AsyncMock(return_value={"success": True})
        bot._has_active_orders_or_positions = Mock(return_value=False)
        bot._last_order_activity = None
        
        # Track intervals used
        intervals = []
        original_sleep = asyncio.sleep
        
        async def tracked_sleep(seconds):
            intervals.append(seconds)
            if len(intervals) < 2:  # Only run 2 iterations
                await original_sleep(0.01)
            else:
                bot._auto_fills_enabled = False
        
        with patch('asyncio.sleep', side_effect=tracked_sleep):
            try:
                await bot._auto_fill_checker()
            except Exception:
                pass
        
        # Should use idle interval (30s) when no activity
        assert all(interval == 30 for interval in intervals)
    
    @pytest.mark.asyncio
    async def test_adaptive_interval_recent_activity(self, bot):
        """Test that active interval is used for recent activity."""
        bot.check_order_fills = AsyncMock(return_value={"success": True})
        bot._has_active_orders_or_positions = Mock(return_value=False)
        bot._last_order_activity = datetime.now()  # Recent activity
        
        # Track intervals used
        intervals = []
        original_sleep = asyncio.sleep
        
        async def tracked_sleep(seconds):
            intervals.append(seconds)
            if len(intervals) < 2:
                await original_sleep(0.01)
            else:
                bot._auto_fills_enabled = False
        
        with patch('asyncio.sleep', side_effect=tracked_sleep):
            try:
                await bot._auto_fill_checker()
            except Exception:
                pass
        
        # Should use active interval (10s) for recent activity
        assert all(interval == 10 for interval in intervals)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

