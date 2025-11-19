#!/usr/bin/env python3
"""
Simple test runner for backend changes
Tests strategy persistence and bar aggregator functionality
"""

import sys
import os
from unittest.mock import Mock, MagicMock, patch, AsyncMock
from datetime import datetime, timezone
import asyncio

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def test_bar_builder():
    """Test BarBuilder functionality"""
    print("Testing BarBuilder...")
    from core.bar_aggregator import BarBuilder
    
    now = datetime.now(timezone.utc)
    builder = BarBuilder('MNQ', '5m', now)
    
    # Add ticks
    builder.add_tick(15000.0, volume=100)
    builder.add_tick(15050.0, volume=50)
    builder.add_tick(14980.0, volume=75)
    
    bar = builder.to_bar()
    
    assert bar.symbol == 'MNQ'
    assert bar.open == 15000.0
    assert bar.high == 15050.0
    assert bar.low == 14980.0
    assert bar.close == 14980.0
    assert bar.volume == 225
    
    print("✅ BarBuilder tests passed")


def test_bar_aggregator():
    """Test BarAggregator functionality"""
    print("Testing BarAggregator...")
    from core.bar_aggregator import BarAggregator
    
    aggregator = BarAggregator(broadcast_callback=None)
    
    # Subscribe to timeframe
    aggregator.subscribe_timeframe('MNQ', '5m')
    assert 'MNQ' in aggregator.bar_builders
    assert '5m' in aggregator.bar_builders['MNQ']
    
    # Add quotes
    aggregator.add_quote('MNQ', 15000.0, volume=100)
    aggregator.add_quote('MNQ', 15050.0, volume=50)
    
    # Get current bar
    bar = aggregator.get_current_bar('MNQ', '5m')
    assert bar is not None
    assert bar.close == 15050.0
    assert bar.volume == 150
    
    print("✅ BarAggregator tests passed")


async def test_strategy_persistence():
    """Test strategy persistence functionality"""
    print("Testing Strategy Persistence...")
    from strategies.strategy_manager import StrategyManager
    from strategies.strategy_base import StrategyConfig
    
    # Create mock trading bot
    mock_bot = MagicMock()
    mock_bot.selected_account = {'id': '12345', 'name': 'Test Account'}
    mock_bot.db = MagicMock()
    
    # Mock persisted state
    persisted_states = {
        'overnight_range': {
            'enabled': True,
            'symbols': ['MNQ', 'MES'],
            'settings': {
                'position_size': 3,
                'overnight_start_time': '18:00',
                'atr_period': 14
            }
        }
    }
    mock_bot.db.get_strategy_states = Mock(return_value=persisted_states)
    
    # Create strategy manager
    manager = StrategyManager(trading_bot=mock_bot)
    
    # Mock strategy class
    class MockStrategy:
        def __init__(self, bot, config):
            self.trading_bot = bot
            self.config = config
            self.overnight_start = '18:00'
            self.atr_period = 14
    
    manager.register_strategy('overnight_range', MockStrategy)
    manager.start_strategy = AsyncMock(return_value=(True, "Started"))
    
    # Test auto-start from persisted state
    await manager.auto_start_enabled_strategies()
    
    # Verify strategy was started
    assert manager.start_strategy.called
    
    print("✅ Strategy Persistence tests passed")


async def test_strategy_specific_settings():
    """Test strategy-specific settings application"""
    print("Testing Strategy-Specific Settings...")
    from strategies.strategy_manager import StrategyManager
    from strategies.strategy_base import StrategyConfig
    
    mock_bot = MagicMock()
    mock_bot.selected_account = {'id': '12345'}
    mock_bot.db = MagicMock()
    mock_bot.db.save_strategy_state = Mock(return_value=True)
    
    manager = StrategyManager(trading_bot=mock_bot)
    
    # Create mock strategy with specific attributes
    class MockOvernightStrategy:
        def __init__(self, bot, config):
            self.trading_bot = bot
            self.config = config
            self.overnight_start = '18:00'
            self.overnight_end = '09:30'
            self.atr_period = 14
            self.stop_atr_multiplier = 1.25
    
    manager.register_strategy('overnight_range', MockOvernightStrategy)
    
    # Create strategy instance
    config = StrategyConfig.from_env('overnight_range')
    strategy = MockOvernightStrategy(mock_bot, config)
    manager.strategies['overnight_range'] = strategy
    
    # Test applying strategy-specific settings
    # Note: The method checks for 'overnightrange' in class name
    # So we need to ensure the class name matches
    strategy.__class__.__name__ = 'OvernightRangeStrategy'
    
    # Mock pytz if not available
    import sys
    if 'pytz' not in sys.modules:
        sys.modules['pytz'] = MagicMock()
        sys.modules['pytz'].timezone = Mock(return_value='US/Eastern')
    
    settings = {
        'overnight_start_time': '19:00',
        'atr_period': 20,
        'stop_atr_multiplier': 1.5
    }
    
    manager._apply_strategy_specific_settings(strategy, settings)
    
    assert strategy.overnight_start == '19:00'
    assert strategy.atr_period == 20
    assert strategy.stop_atr_multiplier == 1.5
    
    # Test serialization
    serialized = manager._serialize_config(config, strategy)
    assert 'overnight_start_time' in serialized
    assert serialized['overnight_start_time'] == '19:00'
    
    print("✅ Strategy-Specific Settings tests passed")


async def test_integration():
    """Test integration between components"""
    print("Testing Integration...")
    from core.bar_aggregator import BarAggregator
    
    # Test bar aggregator directly (avoiding full bot initialization which requires dependencies)
    aggregator = BarAggregator(broadcast_callback=None)
    
    # Test bar aggregator functionality
    aggregator.subscribe_timeframe('MNQ', '5m')
    aggregator.add_quote('MNQ', 15000.0, volume=100)
    
    bar = aggregator.get_current_bar('MNQ', '5m')
    assert bar is not None
    assert bar.close == 15000.0
    
    # Test that aggregator can be started/stopped
    await aggregator.start()
    assert aggregator._running is True
    await aggregator.stop()
    assert aggregator._running is False
    
    print("✅ Integration tests passed")


def main():
    """Run all tests"""
    print("=" * 60)
    print("Running Backend Tests")
    print("=" * 60)
    
    tests_passed = 0
    tests_failed = 0
    
    # Run synchronous tests
    try:
        test_bar_builder()
        tests_passed += 1
    except Exception as e:
        print(f"❌ BarBuilder test failed: {e}")
        tests_failed += 1
        import traceback
        traceback.print_exc()
    
    try:
        test_bar_aggregator()
        tests_passed += 1
    except Exception as e:
        print(f"❌ BarAggregator test failed: {e}")
        tests_failed += 1
        import traceback
        traceback.print_exc()
    
    # Run async tests
    async_tests = [
        test_strategy_persistence,
        test_strategy_specific_settings,
        test_integration
    ]
    
    for test_func in async_tests:
        try:
            asyncio.run(test_func())
            tests_passed += 1
        except Exception as e:
            print(f"❌ {test_func.__name__} failed: {e}")
            tests_failed += 1
            import traceback
            traceback.print_exc()
    
    print("=" * 60)
    print(f"Tests Passed: {tests_passed}")
    print(f"Tests Failed: {tests_failed}")
    print("=" * 60)
    
    return 0 if tests_failed == 0 else 1


if __name__ == '__main__':
    sys.exit(main())

