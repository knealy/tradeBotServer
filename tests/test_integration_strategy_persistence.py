"""
Integration tests for strategy persistence and bar aggregator
"""

import pytest
import asyncio
from unittest.mock import Mock, MagicMock, patch, AsyncMock
from datetime import datetime, timezone
import os
import sys

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trading_bot import TopStepXTradingBot
from strategies.strategy_manager import StrategyManager
from core.bar_aggregator import BarAggregator


@pytest.fixture
def mock_trading_bot():
    """Create a mock trading bot with all dependencies"""
    with patch.dict(os.environ, {
        'PROJECT_X_API_KEY': 'test_key',
        'PROJECT_X_USERNAME': 'test_user',
        'API_TIMEOUT': '30'
    }):
        bot = TopStepXTradingBot(api_key='test_key', username='test_user')
        bot.selected_account = {'id': '12345', 'name': 'Test Account'}
        bot.session_token = 'test_token'
        
        # Mock database
        bot.db = MagicMock()
        bot.db.get_strategy_states = Mock(return_value={})
        bot.db.save_strategy_state = Mock(return_value=True)
        
        return bot


class TestStrategyPersistenceIntegration:
    """Integration tests for strategy persistence"""
    
    @pytest.mark.asyncio
    async def test_strategy_auto_start_on_server_startup(self, mock_trading_bot):
        """Test that strategies auto-start from persisted state on server startup"""
        # Set up persisted state
        persisted_states = {
            'overnight_range': {
                'enabled': True,
                'symbols': ['MNQ', 'MES'],
                'settings': {
                    'position_size': 3,
                    'max_positions': 2,
                    'overnight_start_time': '18:00',
                    'atr_period': 14
                }
            }
        }
        mock_trading_bot.db.get_strategy_states = Mock(return_value=persisted_states)
        
        # Mock strategy start
        mock_trading_bot.strategy_manager.start_strategy = AsyncMock(return_value=(True, "Started"))
        
        # Simulate server startup sequence
        await mock_trading_bot.strategy_manager.apply_persisted_states()
        await mock_trading_bot.strategy_manager.auto_start_enabled_strategies()
        
        # Verify strategy was started
        assert mock_trading_bot.strategy_manager.start_strategy.called
    
    @pytest.mark.asyncio
    async def test_strategy_config_update_persists(self, mock_trading_bot):
        """Test that strategy config updates are persisted to database"""
        # Update strategy config
        success, message = await mock_trading_bot.strategy_manager.update_strategy_config(
            'overnight_range',
            symbols=['MNQ'],
            position_size=3,
            strategy_params={
                'overnight_start_time': '19:00',
                'atr_period': 20
            }
        )
        
        assert success is True
        assert mock_trading_bot.db.save_strategy_state.called
        
        # Verify saved state includes strategy params
        call_args = mock_trading_bot.db.save_strategy_state.call_args
        settings = call_args[1]['settings']
        assert settings['overnight_start_time'] == '19:00'
        assert settings['atr_period'] == 20
        assert settings['position_size'] == 3


class TestBarAggregatorIntegration:
    """Integration tests for bar aggregator"""
    
    @pytest.mark.asyncio
    async def test_bar_aggregator_with_signalr_quotes(self, mock_trading_bot):
        """Test bar aggregator receives quotes from SignalR"""
        # Verify bar aggregator is initialized
        assert hasattr(mock_trading_bot, 'bar_aggregator')
        assert mock_trading_bot.bar_aggregator is not None
        
        # Subscribe to timeframe
        mock_trading_bot.bar_aggregator.subscribe_timeframe('MNQ', '5m')
        
        # Simulate SignalR quote update
        mock_trading_bot.bar_aggregator.add_quote('MNQ', 15000.0, volume=100)
        mock_trading_bot.bar_aggregator.add_quote('MNQ', 15050.0, volume=50)
        
        # Get current bar
        bar = mock_trading_bot.bar_aggregator.get_current_bar('MNQ', '5m')
        
        assert bar is not None
        assert bar.symbol == 'MNQ'
        assert bar.open == 15000.0
        assert bar.high == 15050.0
        assert bar.close == 15050.0
        assert bar.volume == 150
    
    @pytest.mark.asyncio
    async def test_bar_aggregator_broadcasts_updates(self, mock_trading_bot):
        """Test bar aggregator broadcasts updates via callback"""
        broadcast_messages = []
        
        def mock_broadcast(message):
            broadcast_messages.append(message)
        
        mock_trading_bot.bar_aggregator.broadcast_callback = mock_broadcast
        mock_trading_bot.bar_aggregator.subscribe_timeframe('MNQ', '5m')
        mock_trading_bot.bar_aggregator.add_quote('MNQ', 15000.0, volume=100)
        
        # Trigger broadcast
        await mock_trading_bot.bar_aggregator._broadcast_updates()
        
        # Wait for async operations
        await asyncio.sleep(0.1)
        
        # Verify broadcast was called
        assert len(broadcast_messages) > 0
        message = broadcast_messages[0]
        assert message['type'] == 'market_update'
        assert message['data']['symbol'] == 'MNQ'


class TestSignalRIntegration:
    """Test SignalR quote handler integration with bar aggregator"""
    
    @pytest.mark.asyncio
    async def test_signalr_quote_feeds_bar_aggregator(self, mock_trading_bot):
        """Test that SignalR quote handler feeds quotes to bar aggregator"""
        # Subscribe to timeframe
        mock_trading_bot.bar_aggregator.subscribe_timeframe('MNQ', '5m')
        
        # Simulate SignalR quote callback (from on_quote handler)
        quote_data = {
            'lastPrice': 15000.0,
            'volume': 100,
            'bestBid': 14999.5,
            'bestAsk': 15000.5
        }
        
        # Manually trigger the quote handler logic
        symbol = 'MNQ'
        last_price = quote_data.get('lastPrice')
        volume = quote_data.get('volume', 0)
        
        if last_price is not None and hasattr(mock_trading_bot, 'bar_aggregator'):
            mock_trading_bot.bar_aggregator.add_quote(
                symbol=symbol,
                price=float(last_price),
                volume=int(volume) if volume else 0,
                timestamp=datetime.now(timezone.utc)
            )
        
        # Verify bar was updated
        bar = mock_trading_bot.bar_aggregator.get_current_bar('MNQ', '5m')
        assert bar is not None
        assert bar.close == 15000.0
        assert bar.volume == 100


if __name__ == '__main__':
    pytest.main([__file__, '-v'])

