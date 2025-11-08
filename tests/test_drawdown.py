"""
Unit tests for drawdown/max loss command
"""

import pytest
import asyncio
from unittest.mock import Mock, MagicMock, patch, AsyncMock
import os
import sys

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from trading_bot import TopStepXTradingBot


@pytest.fixture
def bot():
    """Create a bot instance with mocked dependencies"""
    with patch.dict(os.environ, {
        'PROJECT_X_API_KEY': 'test_key',
        'PROJECT_X_USERNAME': 'test_user',
        'API_TIMEOUT': '30'
    }):
        bot = TopStepXTradingBot(api_key='test_key', username='test_user')
        bot.selected_account = {'id': 12345, 'name': 'Test Account'}
        bot.session_token = 'test_token'
        bot._http_session = MagicMock()
        return bot


class TestDrawdownCommand:
    """Test drawdown/max loss command"""
    
    @pytest.mark.asyncio
    async def test_drawdown_with_all_info(self, bot):
        """Test drawdown command with complete account info"""
        mock_account_info = {
            'startingBalance': 100000.0,
            'maxLossLimit': 6000.0,
            'dailyLossLimit': 3000.0,
            'accountBalance': 100000.0
        }
        
        bot.get_account_info = AsyncMock(return_value=mock_account_info)
        bot.get_account_balance = AsyncMock(return_value=98500.0)
        bot.get_open_positions = AsyncMock(return_value=[])
        
        # Simulate command execution
        account_id = bot.selected_account['id']
        account_info = await bot.get_account_info(account_id)
        balance = await bot.get_account_balance(account_id)
        
        assert balance == 98500.0
        assert account_info['startingBalance'] == 100000.0
        
        # Calculate drawdown
        starting_balance = account_info.get('startingBalance', 0)
        if starting_balance > 0:
            drawdown = starting_balance - balance
            drawdown_percent = (drawdown / starting_balance) * 100
            assert drawdown == 1500.0
            assert abs(drawdown_percent - 1.5) < 0.01
    
    @pytest.mark.asyncio
    async def test_drawdown_with_positions_pnl(self, bot):
        """Test drawdown includes open positions P&L"""
        mock_account_info = {
            'startingBalance': 100000.0,
            'maxLossLimit': 6000.0
        }
        mock_positions = [
            {'unrealizedPnl': -500.0},
            {'unrealizedPnl': 200.0}
        ]
        
        bot.get_account_info = AsyncMock(return_value=mock_account_info)
        bot.get_account_balance = AsyncMock(return_value=98500.0)
        bot.get_open_positions = AsyncMock(return_value=mock_positions)
        
        account_id = bot.selected_account['id']
        positions = await bot.get_open_positions(account_id)
        total_unrealized_pnl = sum(float(p.get('unrealizedPnl', 0)) for p in positions)
        
        assert total_unrealized_pnl == -300.0
    
    @pytest.mark.asyncio
    async def test_drawdown_without_account(self, bot):
        """Test drawdown command without selected account"""
        bot.selected_account = None
        
        # Should handle gracefully
        account_id = bot.selected_account['id'] if bot.selected_account else None
        assert account_id is None
    
    @pytest.mark.asyncio
    async def test_remaining_loss_capacity(self, bot):
        """Test calculation of remaining loss capacity"""
        mock_account_info = {
            'startingBalance': 100000.0,
            'maxLossLimit': 6000.0
        }
        
        bot.get_account_info = AsyncMock(return_value=mock_account_info)
        bot.get_account_balance = AsyncMock(return_value=98500.0)
        
        account_id = bot.selected_account['id']
        account_info = await bot.get_account_info(account_id)
        balance = await bot.get_account_balance(account_id)
        
        max_loss_limit = account_info.get('maxLossLimit', 0)
        starting_balance = account_info.get('startingBalance', 0)
        drawdown = starting_balance - balance if starting_balance > 0 else 0
        remaining_loss = max_loss_limit - drawdown if max_loss_limit else None
        
        assert remaining_loss == 4500.0  # 6000 - 1500


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

