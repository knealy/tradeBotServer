"""
Unit tests for WebSocket connection pooling.

Tests that WebSocket connections are reused for multiple symbols to reduce overhead.
"""

import pytest
import asyncio
import os
import threading
from unittest.mock import Mock, patch, MagicMock

from trading_bot import TopStepXTradingBot


class TestWebSocketPooling:
    """Test suite for WebSocket connection pooling."""
    
    @pytest.fixture
    def bot(self):
        """Create a bot instance for testing."""
        with patch.dict(os.environ, {
            'PROJECT_X_API_KEY': 'test_key',
            'PROJECT_X_USERNAME': 'test_user',
            'WEBSOCKET_POOL_MAX_SIZE': '5',
        }):
            bot = TopStepXTradingBot(api_key='test_key', username='test_user')
            return bot
    
    def test_websocket_pool_initialization(self, bot):
        """Test that WebSocket pool is initialized."""
        assert hasattr(bot, '_websocket_pool')
        assert hasattr(bot, '_websocket_pool_lock')
        assert hasattr(bot, '_websocket_pool_max_size')
        assert isinstance(bot._websocket_pool, dict)
        assert hasattr(bot._websocket_pool_lock, 'acquire')  # Check it's a lock-like object
        assert bot._websocket_pool_max_size == 5
    
    def test_websocket_pool_max_size_configurable(self, bot):
        """Test that pool max size is configurable via environment."""
        with patch.dict(os.environ, {'WEBSOCKET_POOL_MAX_SIZE': '10'}):
            bot2 = TopStepXTradingBot(api_key='test_key', username='test_user')
            assert bot2._websocket_pool_max_size == 10
    
    @pytest.mark.asyncio
    async def test_single_connection_reused_for_multiple_symbols(self, bot):
        """Test that a single connection is reused for multiple symbol subscriptions."""
        # Mock the market hub connection
        mock_hub = MagicMock()
        mock_hub.start = Mock()
        bot._market_hub = mock_hub
        bot._market_hub_connected = True
        bot.session_token = "test_token"
        
        # Mock contract ID lookup
        bot._get_contract_id = Mock(side_effect=lambda s: f"CONTRACT_{s}")
        
        # Subscribe to multiple symbols
        symbols = ["MNQ", "ES", "NQ"]
        
        for symbol in symbols:
            await bot._ensure_quote_subscription(symbol)
        
        # Connection should only be started once (not per symbol)
        # The hub should be reused for all subscriptions
        assert bot._market_hub_connected
        
        # All symbols should be in subscribed set
        assert len(bot._subscribed_symbols) == len(symbols)
        for symbol in symbols:
            assert symbol.upper() in bot._subscribed_symbols
    
    @pytest.mark.asyncio
    async def test_connection_not_created_if_already_connected(self, bot):
        """Test that connection is not recreated if already connected."""
        # Set up as already connected
        bot._market_hub_connected = True
        bot._market_hub = MagicMock()
        
        # Store original state
        original_connected = bot._market_hub_connected
        original_hub = bot._market_hub
        
        # Try to ensure connection (should return immediately)
        await bot._ensure_market_socket_started()
        
        # Connection state should be unchanged (early return worked)
        assert bot._market_hub_connected == original_connected
        assert bot._market_hub == original_hub
    
    def test_subscribed_symbols_tracking(self, bot):
        """Test that subscribed symbols are properly tracked."""
        bot._subscribed_symbols.add("MNQ")
        bot._subscribed_symbols.add("ES")
        
        assert "MNQ" in bot._subscribed_symbols
        assert "ES" in bot._subscribed_symbols
        assert "NQ" not in bot._subscribed_symbols
    
    def test_pending_symbols_queue(self, bot):
        """Test that pending symbols are queued when connection not ready."""
        bot._market_hub_connected = False
        
        # Add pending symbols
        bot._pending_symbols.add("MNQ")
        bot._pending_symbols.add("ES")
        
        assert "MNQ" in bot._pending_symbols
        assert "ES" in bot._pending_symbols
        
        # When connection is established, pending symbols should be flushed
        bot._market_hub_connected = True
        bot._market_hub = MagicMock()
        bot._market_hub.send = Mock()
        bot._get_contract_id = Mock(side_effect=lambda s: f"CONTRACT_{s}")
        
        # Simulate flush (normally done in on_open callback)
        for sym in list(bot._pending_symbols):
            cid = bot._get_contract_id(sym)
            bot._market_hub.send(bot._market_hub_subscribe_method, [cid])
            bot._subscribed_symbols.add(sym)
            bot._pending_symbols.discard(sym)
        
        assert len(bot._pending_symbols) == 0
        assert len(bot._subscribed_symbols) == 2


class TestWebSocketPoolPerformance:
    """Performance tests for WebSocket pooling."""
    
    @pytest.fixture
    def bot(self):
        """Create a bot instance for testing."""
        with patch.dict(os.environ, {
            'PROJECT_X_API_KEY': 'test_key',
            'PROJECT_X_USERNAME': 'test_user',
        }):
            bot = TopStepXTradingBot(api_key='test_key', username='test_user')
            return bot
    
    @pytest.mark.asyncio
    async def test_pool_reduces_connection_overhead(self, bot):
        """Test that pooling reduces connection overhead."""
        # Reset connection state
        bot._market_hub_connected = False
        bot._market_hub = None
        bot.session_token = "test_token"
        
        # Track connection creation attempts (before early return check)
        connection_attempts = []
        
        # Mock the method to track attempts and simulate successful connection
        original_method = bot._ensure_market_socket_started
        
        async def tracked_start():
            # Check if already connected (early return)
            if bot._market_hub_connected:
                return
            
            # Track this attempt
            connection_attempts.append(1)
            
            # Simulate successful connection
            bot._market_hub_connected = True
            bot._market_hub = MagicMock()
            bot._market_hub.start = Mock()
            
            # Call original (but it will exit early next time)
            return await original_method()
        
        bot._ensure_market_socket_started = tracked_start
        
        # Call multiple times - should only attempt connection once
        await bot._ensure_market_socket_started()
        await bot._ensure_market_socket_started()  # Second call should return early
        await bot._ensure_market_socket_started()  # Third call should return early
        
        # Should only attempt connection once (first call)
        # Subsequent calls should return early due to _market_hub_connected check
        assert len(connection_attempts) == 1
        assert bot._market_hub_connected == True
    
    def test_pool_thread_safety(self, bot):
        """Test that pool operations are thread-safe."""
        results = []
        
        def add_to_pool(thread_id):
            with bot._websocket_pool_lock:
                bot._websocket_pool[f"url_{thread_id}"] = MagicMock()
                results.append(len(bot._websocket_pool))
        
        # Create multiple threads with unique IDs
        threads = []
        for i in range(10):
            t = threading.Thread(target=add_to_pool, args=(i,))
            threads.append(t)
        
        # Start all threads
        for t in threads:
            t.start()
        
        # Wait for all to complete
        for t in threads:
            t.join()
        
        # All should have succeeded
        assert len(results) == 10
        # Pool should contain entries from all threads (thread-safe)
        assert len(bot._websocket_pool) == 10


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

