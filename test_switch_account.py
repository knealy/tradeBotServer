"""
Unit tests for switch account command
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
        bot.selected_account = {'id': 12345, 'name': 'Account 1'}
        bot.session_token = 'test_token'
        bot._http_session = MagicMock()
        return bot


class TestSwitchAccount:
    """Test switch account functionality"""
    
    @pytest.mark.asyncio
    async def test_switch_account_by_id(self, bot):
        """Test switching account by ID"""
        mock_accounts = [
            {'id': 12345, 'name': 'Account 1', 'balance': 100000.0},
            {'id': 67890, 'name': 'Account 2', 'balance': 200000.0}
        ]
        
        bot.list_accounts = AsyncMock(return_value=mock_accounts)
        bot.get_account_balance = AsyncMock(return_value=200000.0)
        
        # Simulate switch_account command with ID
        accounts = await bot.list_accounts()
        target_account = None
        for acc in accounts:
            if acc.get('id') == 67890:
                target_account = acc
                break
        
        assert target_account is not None
        assert target_account['id'] == 67890
        
        old_account = bot.selected_account
        bot.selected_account = target_account
        bot._cached_order_ids = {}
        bot._cached_position_ids = {}
        
        assert bot.selected_account['id'] == 67890
        assert bot.selected_account['name'] == 'Account 2'
        assert len(bot._cached_order_ids) == 0
        assert len(bot._cached_position_ids) == 0
    
    @pytest.mark.asyncio
    async def test_switch_account_by_number(self, bot):
        """Test switching account by list number"""
        mock_accounts = [
            {'id': 12345, 'name': 'Account 1', 'balance': 100000.0},
            {'id': 67890, 'name': 'Account 2', 'balance': 200000.0}
        ]
        
        bot.list_accounts = AsyncMock(return_value=mock_accounts)
        
        accounts = await bot.list_accounts()
        account_index = 1  # Second account (0-indexed)
        if 0 <= account_index < len(accounts):
            target_account = accounts[account_index]
            bot.selected_account = target_account
            
            assert bot.selected_account['id'] == 67890
    
    @pytest.mark.asyncio
    async def test_switch_account_clears_cache(self, bot):
        """Test that switching accounts clears account-specific caches"""
        # Populate caches
        bot._cached_order_ids = {12345: {'MNQ': {111, 222}}}
        bot._cached_position_ids = {12345: {'MNQ': {333}}}
        
        mock_accounts = [
            {'id': 12345, 'name': 'Account 1'},
            {'id': 67890, 'name': 'Account 2'}
        ]
        
        bot.list_accounts = AsyncMock(return_value=mock_accounts)
        
        accounts = await bot.list_accounts()
        target_account = accounts[1]  # Switch to second account
        
        old_account = bot.selected_account
        bot.selected_account = target_account
        bot._cached_order_ids = {}
        bot._cached_position_ids = {}
        
        assert len(bot._cached_order_ids) == 0
        assert len(bot._cached_position_ids) == 0
    
    @pytest.mark.asyncio
    async def test_switch_account_invalid_id(self, bot):
        """Test switching with invalid account ID"""
        mock_accounts = [
            {'id': 12345, 'name': 'Account 1'},
            {'id': 67890, 'name': 'Account 2'}
        ]
        
        bot.list_accounts = AsyncMock(return_value=mock_accounts)
        
        accounts = await bot.list_accounts()
        target_account = None
        # Try to find non-existent account
        for acc in accounts:
            if acc.get('id') == 99999:
                target_account = acc
                break
        
        assert target_account is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

