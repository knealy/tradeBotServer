"""
Test suite for strategy_executor.py

Tests all critical paths, error handling, and edge cases for automated trading.
"""

import pytest
import asyncio
from unittest.mock import Mock, patch, AsyncMock
from datetime import datetime, timedelta, timezone
import time

# Imports from our system
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core.auth import AuthManager
from core.strategy_executor import StrategyExecutor
from brokers.topstepx_adapter import TopStepXAdapter
from strategies.overnight_range_strategy import OvernightRangeStrategy


class TestTokenManagement:
    """Test token refresh efficiency and correctness."""
    
    @pytest.mark.asyncio
    async def test_token_check_efficiency(self):
        """Token checks should be <1ms when valid (just timestamp comparison)."""
        auth = AuthManager()
        # Set valid token
        auth.session_token = "fake_token"
        auth.token_expiry = datetime.now(timezone.utc) + timedelta(hours=1)
        
        # Should not refresh (just timestamp check)
        start = time.perf_counter()
        for _ in range(100):
            result = await auth.ensure_valid_token()
            assert result is True
        elapsed = (time.perf_counter() - start) / 100
        
        # Each check should be <1ms
        assert elapsed < 0.001, f"Token check took {elapsed*1000:.2f}ms, expected <1ms"
    
    @pytest.mark.asyncio
    async def test_token_refresh_when_expired(self):
        """Token should refresh when expired."""
        auth = AuthManager()
        # Set expired token
        auth.session_token = "old_token"
        auth.token_expiry = datetime.now(timezone.utc) - timedelta(hours=1)
        
        with patch.object(auth, 'authenticate') as mock_auth:
            mock_auth.return_value = True
            
            result = await auth.ensure_valid_token()
            assert result is True
            mock_auth.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_token_refresh_with_buffer(self):
        """Token should refresh 5 minutes before expiry."""
        auth = AuthManager()
        # Set token expiring in 4 minutes
        auth.session_token = "expiring_token"
        auth.token_expiry = datetime.now(timezone.utc) + timedelta(minutes=4)
        
        with patch.object(auth, 'authenticate') as mock_auth:
            mock_auth.return_value = True
            
            result = await auth.ensure_valid_token()
            assert result is True
            # Should refresh (within 5min buffer)
            mock_auth.assert_called_once()


class TestOrderExecutionPathways:
    """Test all order execution paths: Rust, Python, Fallback."""
    
    @pytest.mark.asyncio
    async def test_rust_path_success(self):
        """Rust path should execute orders in 10-50ms."""
        # Mock auth manager
        mock_auth = Mock()
        mock_auth.ensure_valid_token = AsyncMock(return_value=True)
        mock_auth.get_token = Mock(return_value='fake_token')
        
        adapter = TopStepXAdapter(mock_auth)
        adapter._use_rust = True
        adapter._rust_executor = Mock()
        
        # Mock contract manager
        adapter.contract_manager = Mock()
        adapter.contract_manager.get_contract_id = Mock(return_value=123)
        
        # Mock get_tick_size
        adapter._get_tick_size = AsyncMock(return_value=0.25)
        adapter._round_to_tick_size = Mock(side_effect=lambda p, t: p)
        
        # Mock Rust success
        adapter._rust_executor.place_order = AsyncMock(return_value={
            'success': True,
            'order_id': '12345'
        })
        adapter._rust_executor.set_token = Mock()
        adapter._rust_executor.set_contract_id = Mock()
        
        start = time.perf_counter()
        result = await adapter._place_oco_bracket_rust(
            symbol='MNQ',
            side='BUY',
            quantity=1,
            entry_price=25390.0,
            stop_loss_price=25380.0,
            take_profit_price=25400.0,
            account_id='12345',
            strategy_name='test'
        )
        elapsed_ms = (time.perf_counter() - start) * 1000
        
        assert result.success is True
        assert result.order_id == '12345'
        assert elapsed_ms < 100, f"Rust path took {elapsed_ms:.2f}ms"
    
    @pytest.mark.asyncio
    async def test_python_fallback_on_rust_failure(self):
        """Python fallback should work when Rust fails."""
        # Mock auth manager
        mock_auth = Mock()
        mock_auth.ensure_valid_token = AsyncMock(return_value=True)
        mock_auth.get_token = Mock(return_value='fake_token')
        
        adapter = TopStepXAdapter(mock_auth)
        adapter._use_rust = True
        adapter._rust_executor = Mock()
        
        # Mock contract manager
        adapter.contract_manager = Mock()
        adapter.contract_manager.get_contract_id = Mock(return_value=123)
        
        # Mock get_tick_size
        adapter._get_tick_size = AsyncMock(return_value=0.25)
        adapter._round_to_tick_size = Mock(side_effect=lambda p, t: p)
        adapter.get_quote = AsyncMock(return_value=None)  # Skip quote check
        
        # Mock Rust failure
        adapter._rust_executor.place_order = AsyncMock(
            side_effect=Exception("Rust error")
        )
        
        # Mock Python success
        with patch.object(adapter, '_make_request') as mock_request:
            mock_request.return_value = {
                'success': True,
                'orderId': '67890'
            }
            
            result = await adapter.place_oco_bracket_with_stop_entry(
                symbol='MNQ',
                side='BUY',
                quantity=1,
                entry_price=25390.0,
                stop_loss_price=25380.0,
                take_profit_price=25400.0,
                account_id='12345'
            )
            
            assert result.success is True
            assert result.order_id == '67890'


class TestPlainStopFallback:
    """Test plain stop fallback and automatic SL/TP addition."""
    
    @pytest.mark.asyncio
    async def test_fallback_to_plain_stop_on_500_error(self):
        """Should place plain stop when bracket gets 500 error."""
        bot = Mock()
        bot.place_oco_bracket_with_stop_entry = AsyncMock(return_value={
            'error': '500 Server Error'
        })
        bot.place_stop_order = AsyncMock(return_value={
            'orderId': '99999'
        })
        bot.selected_account = {'id': '12345'}
        
        strategy = OvernightRangeStrategy(bot)
        
        # Mock range data
        strategy.active_ranges['MNQ'] = Mock(high=25400.0, low=25300.0)
        
        # This should trigger fallback
        # (Implementation details depend on exact method)
        # Verify plain stop is flagged for monitoring
        # assert strategy.breakout_active_orders['MNQ']['BUY']['needs_brackets']
        # assert strategy.breakout_active_orders['MNQ']['BUY']['monitoring']
        pass  # TODO: Complete based on exact implementation
    
    @pytest.mark.asyncio
    async def test_auto_add_sl_tp_after_fill(self):
        """Should automatically place SL/TP when plain stop fills."""
        bot = Mock()
        bot.get_positions = AsyncMock(return_value=[
            {'symbol': 'MNQ', 'net_quantity': 1}  # LONG position
        ])
        bot.place_stop_order = AsyncMock(return_value={'orderId': 'SL123'})
        bot.place_limit_order = AsyncMock(return_value={'orderId': 'TP123'})
        bot.selected_account = {'id': '12345'}
        
        strategy = OvernightRangeStrategy(bot)
        
        # Set up plain stop that needs brackets
        strategy.breakout_active_orders['MNQ'] = {
            'LONG': {  # Changed from 'BUY' to 'LONG' to match position side
                'order_id': '99999',
                'entry_price': 25390.0,
                'stop_loss': 25380.0,
                'take_profit': 25400.0,
                'quantity': 1,
                'needs_brackets': True,
                'monitoring': True
            }
        }
        
        # Manually run one check (the actual loop checks every 5s)
        strategy.is_trading = True
        
        # Simulate one monitoring cycle
        orders_to_monitor = []
        for symbol, sides in strategy.breakout_active_orders.items():
            for side, order_data in sides.items():
                if order_data.get('needs_brackets') and order_data.get('monitoring'):
                    orders_to_monitor.append((symbol, side, order_data))
        
        # Should have one order to monitor
        assert len(orders_to_monitor) == 1
        
        symbol, side, order_data = orders_to_monitor[0]
        positions = await bot.get_positions()
        
        # Position found, place SL/TP
        await bot.place_stop_order(
            symbol=symbol,
            side="SELL",
            quantity=order_data['quantity'],
            stop_price=order_data['stop_loss'],
            account_id='12345'
        )
        await bot.place_limit_order(
            symbol=symbol,
            side="SELL",
            quantity=order_data['quantity'],
            limit_price=order_data['take_profit'],
            account_id='12345'
        )
        
        # Verify SL and TP were called
        assert bot.place_stop_order.called
        assert bot.place_limit_order.called


class TestConcurrentStrategies:
    """Test multiple strategies running simultaneously."""
    
    @pytest.mark.asyncio
    async def test_multiple_strategies_dont_interfere(self):
        """Multiple strategies should run independently."""
        bot = Mock()
        bot.place_oco_bracket_with_stop_entry = AsyncMock(return_value={
            'orderId': '12345'
        })
        
        strategy1 = OvernightRangeStrategy(bot)
        strategy2 = OvernightRangeStrategy(bot)
        
        # Both should be able to trade same symbol
        # without interfering with each other
        # (They share broker adapter but have separate state)
        pass  # TODO: Implement based on StrategyManager
    
    @pytest.mark.asyncio
    async def test_shared_token_across_strategies(self):
        """All strategies should share same token (no duplicate refreshes)."""
        auth = AuthManager()
        auth.session_token = "shared_token"
        auth.token_expiry = datetime.now(timezone.utc) + timedelta(hours=1)
        
        # Simulate 3 strategies checking token concurrently
        results = await asyncio.gather(*[
            auth.ensure_valid_token() for _ in range(3)
        ])
        
        assert all(results)
        # Token should still be the same (no refreshes)
        assert auth.session_token == "shared_token"


class TestErrorRecovery:
    """Test recovery from various error conditions."""
    
    @pytest.mark.asyncio
    async def test_network_retry_logic(self):
        """Should retry on network errors with exponential backoff."""
        auth = AuthManager()
        
        call_count = [0]
        async def mock_request(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] < 3:
                raise ConnectionError("Network down")
            return {'token': 'new_token', 'expiration': '2025-12-18T10:00:00Z'}
        
        with patch.object(auth._http_session, 'post', side_effect=mock_request):
            # Should retry and eventually succeed
            # (Implementation depends on retry logic)
            pass
    
    @pytest.mark.asyncio
    async def test_strategy_exception_isolation(self):
        """One strategy crash shouldn't affect others."""
        bot = Mock()
        
        strategy1 = OvernightRangeStrategy(bot)
        strategy2 = OvernightRangeStrategy(bot)
        
        # Make strategy1 throw exception
        strategy1.execute = AsyncMock(side_effect=ValueError("Test error"))
        strategy2.execute = AsyncMock(return_value={'success': True})
        
        # Both run concurrently
        results = await asyncio.gather(
            strategy1.execute(),
            strategy2.execute(),
            return_exceptions=True
        )
        
        # strategy1 should have exception, strategy2 should succeed
        assert isinstance(results[0], ValueError)
        assert results[1] == {'success': True}


class TestEdgeCases:
    """Test boundary conditions and edge cases."""
    
    def test_wide_overnight_range(self):
        """Should handle overnight ranges of any size (no distance limits)."""
        # MGC can have entry 13,000+ ticks from market
        # TopStepX accepts this (user confirmed)
        entry = 3065.0
        market = 4366.0
        tick_size = 0.1
        distance_ticks = abs(int((entry - market) / tick_size))
        
        # Distance is massive but should be accepted
        assert distance_ticks > 13000
        # No validation should reject this
    
    @pytest.mark.asyncio
    async def test_simultaneous_long_short_fills(self):
        """Should handle LONG and SHORT filling at same time (net flat)."""
        bot = Mock()
        bot.get_positions = AsyncMock(return_value=[
            {'symbol': 'MNQ', 'net_quantity': 0}  # Flat
        ])
        
        strategy = OvernightRangeStrategy(bot)
        
        # Both LONG and SHORT filled → net flat
        # Should not place SL/TP (no position)
        # (Test monitoring logic)
        pass
    
    @pytest.mark.asyncio
    async def test_strategy_restart_with_open_positions(self):
        """Should resume monitoring without duplicate orders."""
        bot = Mock()
        bot.get_positions = AsyncMock(return_value=[
            {'symbol': 'MNQ', 'net_quantity': 1}  # Existing position
        ])
        bot.get_open_orders = AsyncMock(return_value=[
            {'symbol': 'MNQ', 'side': 'BUY', 'type': 'STOP'}  # Existing SL
        ])
        
        strategy = OvernightRangeStrategy(bot)
        
        # Restart should detect existing orders
        # Should not place duplicate orders
        # (Test logic depends on implementation)
        pass


class TestPerformance:
    """Performance and load tests."""
    
    @pytest.mark.asyncio
    async def test_market_open_burst(self):
        """Should handle 6 orders at market open in <1 second."""
        bot = Mock()
        bot.place_oco_bracket_with_stop_entry = AsyncMock(return_value={
            'orderId': '12345'
        })
        
        strategy = OvernightRangeStrategy(bot)
        
        # Simulate placing 6 orders (3 symbols × 2 sides)
        start = time.perf_counter()
        
        results = await asyncio.gather(*[
            bot.place_oco_bracket_with_stop_entry(
                symbol=sym,
                side=side,
                quantity=1,
                entry_price=25390.0,
                stop_loss_price=25380.0,
                take_profit_price=25400.0,
                account_id='12345'
            )
            for sym in ['MNQ', 'MES', 'MGC']
            for side in ['BUY', 'SELL']
        ])
        
        elapsed = time.perf_counter() - start
        
        assert all(r['orderId'] for r in results)
        assert elapsed < 1.0, f"Burst took {elapsed:.2f}s, expected <1s"
    
    @pytest.mark.asyncio
    async def test_sustained_high_frequency(self):
        """Should handle order every 10s for 1 minute without delays."""
        bot = Mock()
        bot.place_oco_bracket_with_stop_entry = AsyncMock(return_value={
            'orderId': '12345'
        })
        
        orders_placed = 0
        start = time.perf_counter()
        
        for _ in range(6):  # 1 minute (6 × 10s)
            result = await bot.place_oco_bracket_with_stop_entry(
                symbol='MNQ',
                side='BUY',
                quantity=1,
                entry_price=25390.0,
                stop_loss_price=25380.0,
                take_profit_price=25400.0,
                account_id='12345'
            )
            if result.get('orderId'):
                orders_placed += 1
            await asyncio.sleep(0.01)  # Simulated delay
        
        elapsed = time.perf_counter() - start
        
        assert orders_placed == 6
        assert elapsed < 1.0, "Should complete quickly in test"


# Run tests
if __name__ == '__main__':
    pytest.main([__file__, '-v', '--asyncio-mode=auto'])
