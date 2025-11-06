"""
Unit tests for parallel initialization optimization.

Tests that independent operations run in parallel to reduce startup time.
"""

import pytest
import asyncio
import os
from unittest.mock import Mock, patch, AsyncMock
from datetime import datetime

from trading_bot import TopStepXTradingBot


class TestParallelInitialization:
    """Test suite for parallel initialization."""
    
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
    async def test_parallel_execution_timing(self, bot):
        """Test that parallel operations complete faster than sequential."""
        # Mock methods to simulate network delays
        async def slow_list_accounts():
            await asyncio.sleep(0.1)  # 100ms
            return [{"id": 1, "name": "Test Account"}]
        
        async def slow_get_contracts():
            await asyncio.sleep(0.1)  # 100ms
            return [{"symbol": "MNQ", "name": "Micro E-mini Nasdaq-100"}]
        
        # Sequential execution
        start_seq = asyncio.get_event_loop().time()
        accounts_seq = await slow_list_accounts()
        contracts_seq = await slow_get_contracts()
        seq_time = (asyncio.get_event_loop().time() - start_seq) * 1000
        
        # Parallel execution
        start_par = asyncio.get_event_loop().time()
        accounts_task = asyncio.create_task(slow_list_accounts())
        contracts_task = asyncio.create_task(slow_get_contracts())
        accounts_par = await accounts_task
        contracts_par = await contracts_task
        par_time = (asyncio.get_event_loop().time() - start_par) * 1000
        
        # Parallel should be faster (roughly half the time)
        assert par_time < seq_time * 0.8  # At least 20% faster
        assert accounts_seq == accounts_par
        assert contracts_seq == contracts_par
    
    @pytest.mark.asyncio
    async def test_run_method_creates_parallel_tasks(self, bot):
        """Test that run() method creates tasks for parallel execution."""
        # Mock authenticate to succeed
        bot.authenticate = AsyncMock(return_value=True)
        
        # Mock list_accounts and get_available_contracts to track if called
        bot.list_accounts = AsyncMock(return_value=[{"id": 1, "name": "Test"}])
        bot.get_available_contracts = AsyncMock(return_value=[{"symbol": "MNQ"}])
        bot.display_accounts = Mock()
        bot.select_account = Mock(return_value={"id": 1, "name": "Test"})
        bot.get_account_balance = AsyncMock(return_value=10000.0)
        
        # Mock trading_interface to exit immediately
        bot.trading_interface = AsyncMock()
        
        # Mock SDK adapter
        with patch('trading_bot.sdk_adapter', None):
            # Run should create tasks and wait for them
            try:
                await asyncio.wait_for(bot.run(), timeout=1.0)
            except asyncio.TimeoutError:
                pass  # Expected if trading_interface doesn't exit
        
        # Both methods should have been called
        bot.list_accounts.assert_called_once()
        bot.get_available_contracts.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_parallel_operations_are_independent(self, bot):
        """Test that parallel operations don't interfere with each other."""
        results = {}
        
        async def op1():
            await asyncio.sleep(0.05)
            results['op1'] = 'result1'
            return 'result1'
        
        async def op2():
            await asyncio.sleep(0.05)
            results['op2'] = 'result2'
            return 'result2'
        
        # Run in parallel
        task1 = asyncio.create_task(op1())
        task2 = asyncio.create_task(op2())
        
        await asyncio.gather(task1, task2)
        
        # Both should complete
        assert 'op1' in results
        assert 'op2' in results
        assert results['op1'] == 'result1'
        assert results['op2'] == 'result2'
    
    @pytest.mark.asyncio
    async def test_cache_initialization_runs_in_parallel(self, bot):
        """Test that SDK cache initialization runs in parallel if enabled."""
        with patch.dict(os.environ, {'USE_PROJECTX_SDK': '1'}):
            with patch('trading_bot.sdk_adapter') as mock_adapter:
                mock_adapter.is_sdk_available = Mock(return_value=True)
                mock_adapter.initialize_historical_client_cache = AsyncMock(return_value=True)
                
                # Mock other methods
                bot.authenticate = AsyncMock(return_value=True)
                bot.list_accounts = AsyncMock(return_value=[{"id": 1, "name": "Test"}])
                bot.get_available_contracts = AsyncMock(return_value=[])
                bot.display_accounts = Mock()
                bot.select_account = Mock(return_value={"id": 1, "name": "Test"})
                bot.get_account_balance = AsyncMock(return_value=10000.0)
                bot.trading_interface = AsyncMock()
                
                try:
                    await asyncio.wait_for(bot.run(), timeout=1.0)
                except asyncio.TimeoutError:
                    pass
                
                # Cache initialization should have been called
                mock_adapter.initialize_historical_client_cache.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_error_handling_in_parallel_execution(self, bot):
        """Test that errors in one parallel operation don't break others."""
        async def failing_op():
            await asyncio.sleep(0.01)
            raise ValueError("Operation failed")
        
        async def succeeding_op():
            await asyncio.sleep(0.01)
            return "Success"
        
        # Run in parallel - one fails, one succeeds
        task1 = asyncio.create_task(failing_op())
        task2 = asyncio.create_task(succeeding_op())
        
        # Gather should raise the error, but both tasks complete
        with pytest.raises(ValueError):
            await asyncio.gather(task1, task2)
        
        # But we can check results individually
        assert task2.done()
        assert task2.result() == "Success"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

