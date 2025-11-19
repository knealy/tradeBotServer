"""
Unit tests for strategy persistence and per-account configuration
"""

import pytest
import asyncio
from unittest.mock import Mock, MagicMock, patch, AsyncMock
import os
import sys
from datetime import datetime, timezone

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from strategies.strategy_manager import StrategyManager
from strategies.strategy_base import StrategyConfig, BaseStrategy


class MockStrategy(BaseStrategy):
    """Mock strategy for testing"""
    
    def __init__(self, trading_bot, config: StrategyConfig = None):
        super().__init__(trading_bot, config)
        self.overnight_start = '18:00'
        self.overnight_end = '09:30'
        self.atr_period = 14
        self.stop_atr_multiplier = 1.25


@pytest.fixture
def mock_trading_bot():
    """Create a mock trading bot"""
    bot = MagicMock()
    bot.selected_account = {'id': '12345', 'name': 'Test Account'}
    bot.db = MagicMock()
    return bot


@pytest.fixture
def strategy_manager(mock_trading_bot):
    """Create a strategy manager instance"""
    manager = StrategyManager(trading_bot=mock_trading_bot)
    manager.register_strategy("test_strategy", MockStrategy)
    return manager


class TestStrategyPersistence:
    """Test strategy persistence per account"""
    
    @pytest.mark.asyncio
    async def test_auto_start_from_persisted_state(self, strategy_manager, mock_trading_bot):
        """Test that strategies auto-start from persisted state per account"""
        # Mock persisted state
        persisted_states = {
            'test_strategy': {
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
        strategy_manager.start_strategy = AsyncMock(return_value=(True, "Started"))
        
        # Call auto_start_enabled_strategies
        await strategy_manager.auto_start_enabled_strategies()
        
        # Verify strategy was started with persisted symbols
        strategy_manager.start_strategy.assert_called_once()
        call_args = strategy_manager.start_strategy.call_args
        assert call_args[0][0] == 'test_strategy'
        assert call_args[1]['symbols'] == ['MNQ', 'MES']
        assert call_args[1]['persist'] is True
    
    @pytest.mark.asyncio
    async def test_auto_start_fallback_to_env(self, strategy_manager, mock_trading_bot):
        """Test fallback to environment variables when no persisted state"""
        # No persisted state
        mock_trading_bot.db.get_strategy_states = Mock(return_value={})
        
        # Mock environment config
        with patch('strategies.strategy_manager.StrategyConfig.from_env') as mock_from_env:
            mock_config = StrategyConfig(
                enabled=True,
                symbols=['ES'],
                position_size=1,
                max_positions=1
            )
            mock_from_env.return_value = mock_config
            
            strategy_manager.start_strategy = AsyncMock(return_value=(True, "Started"))
            
            await strategy_manager.auto_start_enabled_strategies()
            
            # Should still try to start if enabled in env
            mock_from_env.assert_called()
    
    @pytest.mark.asyncio
    async def test_update_strategy_config_with_params(self, strategy_manager, mock_trading_bot):
        """Test updating strategy config with strategy-specific parameters"""
        # Create a strategy instance
        config = StrategyConfig.from_env("test_strategy")
        strategy = MockStrategy(mock_trading_bot, config)
        strategy_manager.strategies['test_strategy'] = strategy
        
        # Mock database save
        mock_trading_bot.db.save_strategy_state = Mock(return_value=True)
        
        # Update with strategy-specific parameters
        strategy_params = {
            'overnight_start_time': '19:00',
            'overnight_end_time': '09:00',
            'atr_period': 20,
            'stop_atr_multiplier': 1.5
        }
        
        success, message = await strategy_manager.update_strategy_config(
            'test_strategy',
            symbols=['MNQ'],
            position_size=3,
            strategy_params=strategy_params
        )
        
        assert success is True
        assert strategy.overnight_start == '19:00'
        assert strategy.overnight_end == '09:00'
        assert strategy.atr_period == 20
        assert strategy.stop_atr_multiplier == 1.5
        assert strategy.config.symbols == ['MNQ']
        assert strategy.config.position_size == 3
        
        # Verify state was saved
        assert mock_trading_bot.db.save_strategy_state.called
    
    def test_apply_strategy_specific_settings(self, strategy_manager):
        """Test applying strategy-specific settings"""
        config = StrategyConfig.from_env("test_strategy")
        strategy = MockStrategy(MagicMock(), config)
        
        settings = {
            'overnight_start_time': '20:00',
            'overnight_end_time': '08:00',
            'atr_period': 21,
            'stop_atr_multiplier': 1.75,
            'tp_atr_multiplier': 2.5,
            'breakeven_enabled': False,
            'breakeven_profit_points': 20.0,
            'range_break_offset': 0.5
        }
        
        strategy_manager._apply_strategy_specific_settings(strategy, settings)
        
        assert strategy.overnight_start == '20:00'
        assert strategy.overnight_end == '08:00'
        assert strategy.atr_period == 21
        assert strategy.stop_atr_multiplier == 1.75
    
    def test_serialize_config_with_strategy_params(self, strategy_manager):
        """Test serializing config includes strategy-specific parameters"""
        config = StrategyConfig.from_env("test_strategy")
        strategy = MockStrategy(MagicMock(), config)
        strategy.overnight_start = '18:00'
        strategy.overnight_end = '09:30'
        strategy.atr_period = 14
        
        serialized = strategy_manager._serialize_config(config, strategy)
        
        assert 'position_size' in serialized
        assert 'max_positions' in serialized
        assert 'overnight_start_time' in serialized
        assert 'overnight_end_time' in serialized
        assert 'atr_period' in serialized
        assert serialized['overnight_start_time'] == '18:00'
        assert serialized['atr_period'] == 14
    
    @pytest.mark.asyncio
    async def test_apply_persisted_states_with_strategy_params(self, strategy_manager, mock_trading_bot):
        """Test applying persisted states includes strategy-specific settings"""
        # Create strategy instance
        config = StrategyConfig.from_env("test_strategy")
        strategy = MockStrategy(mock_trading_bot, config)
        strategy_manager.strategies['test_strategy'] = strategy
        
        # Mock persisted state with strategy-specific params
        persisted_states = {
            'test_strategy': {
                'enabled': True,
                'symbols': ['MNQ'],
                'settings': {
                    'position_size': 3,
                    'overnight_start_time': '19:00',
                    'atr_period': 20
                }
            }
        }
        
        mock_trading_bot.db.get_strategy_states = Mock(return_value=persisted_states)
        
        await strategy_manager.apply_persisted_states()
        
        # Verify strategy-specific settings were applied
        assert strategy.overnight_start == '19:00'
        assert strategy.atr_period == 20
        assert strategy.config.symbols == ['MNQ']
        assert strategy.config.position_size == 3


if __name__ == '__main__':
    pytest.main([__file__, '-v'])

