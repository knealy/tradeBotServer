"""
Unit tests for trailing stop implementation using SDK
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
        'USE_PROJECTX_SDK': '1',
        'API_TIMEOUT': '30'
    }):
        bot = TopStepXTradingBot(api_key='test_key', username='test_user')
        bot.selected_account = {'id': 12345, 'name': 'Test Account'}
        bot.session_token = 'test_token'
        bot._http_session = MagicMock()
        return bot


@pytest.fixture
def mock_sdk_adapter():
    """Create a mock SDK adapter"""
    mock_adapter = MagicMock()
    mock_adapter.is_sdk_available.return_value = True
    mock_adapter.create_suite = AsyncMock()
    return mock_adapter


class TestTrailingStop:
    """Test trailing stop functionality"""
    
    @pytest.mark.asyncio
    async def test_trailing_stop_requires_sdk(self, bot):
        """Test that trailing stop requires SDK"""
        with patch.dict(os.environ, {'USE_PROJECTX_SDK': '0'}):
            result = await bot.place_trailing_stop_order("MNQ", "BUY", 1, 25.0)
            assert "error" in result
            assert "SDK" in result["error"]
    
    @pytest.mark.asyncio
    async def test_trailing_stop_sdk_unavailable(self, bot):
        """Test trailing stop when SDK is not available"""
        with patch('trading_bot.sdk_adapter', None):
            result = await bot.place_trailing_stop_order("MNQ", "BUY", 1, 25.0)
            assert "error" in result
            assert "SDK" in result["error"]
    
    @pytest.mark.asyncio
    async def test_trailing_stop_no_account(self, bot):
        """Test trailing stop without selected account"""
        bot.selected_account = None
        result = await bot.place_trailing_stop_order("MNQ", "BUY", 1, 25.0)
        assert "error" in result
        assert "account" in result["error"].lower()
    
    @pytest.mark.asyncio
    async def test_trailing_stop_invalid_side(self, bot):
        """Test trailing stop with invalid side"""
        result = await bot.place_trailing_stop_order("MNQ", "INVALID", 1, 25.0)
        assert "error" in result
        assert "side" in result["error"].lower()
    
    @pytest.mark.asyncio
    async def test_trailing_stop_sdk_success(self, bot, mock_sdk_adapter):
        """Test successful trailing stop via SDK"""
        # Mock suite with orders property
        mock_suite = MagicMock()
        mock_order_manager = MagicMock()
        
        # Create a mock response object with order_id attribute
        mock_response = MagicMock()
        mock_response.order_id = 12345
        mock_response.id = 12345
        
        mock_order_manager.place_trailing_stop_order = AsyncMock(return_value=mock_response)
        mock_suite.orders = mock_order_manager
        mock_suite.instrument_id = "CON.F.US.MNQ.Z25"
        mock_suite.disconnect = AsyncMock()
        mock_sdk_adapter.create_suite = AsyncMock(return_value=mock_suite)
        
        with patch('trading_bot.sdk_adapter', mock_sdk_adapter):
            result = await bot.place_trailing_stop_order("MNQ", "BUY", 1, 25.0)
            
            assert "success" in result or "error" not in result
            assert result.get("orderId") == 12345
            assert "SDK" in result.get("message", "")
            
            # Verify SDK methods were called
            mock_sdk_adapter.create_suite.assert_called_once()
            mock_order_manager.place_trailing_stop_order.assert_called_once()
            mock_suite.disconnect.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_trailing_stop_sdk_order_manager_missing(self, bot, mock_sdk_adapter):
        """Test trailing stop when orders property is missing"""
        mock_suite = MagicMock()
        mock_suite.orders = None
        mock_suite.disconnect = AsyncMock()
        mock_sdk_adapter.create_suite = AsyncMock(return_value=mock_suite)
        
        with patch('trading_bot.sdk_adapter', mock_sdk_adapter):
            result = await bot.place_trailing_stop_order("MNQ", "BUY", 1, 25.0)
            
            # Should fall back to error message
            assert "error" in result or "SDK" in str(result)
            mock_suite.disconnect.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_trailing_stop_sdk_no_place_order_method(self, bot, mock_sdk_adapter):
        """Test trailing stop when orders doesn't have place_trailing_stop_order method"""
        mock_suite = MagicMock()
        mock_order_manager = MagicMock()
        del mock_order_manager.place_trailing_stop_order  # Remove the method
        mock_suite.orders = mock_order_manager
        mock_suite.disconnect = AsyncMock()
        mock_sdk_adapter.create_suite = AsyncMock(return_value=mock_suite)
        
        with patch('trading_bot.sdk_adapter', mock_sdk_adapter):
            result = await bot.place_trailing_stop_order("MNQ", "BUY", 1, 25.0)
            
            # Should handle gracefully
            assert "error" in result or "SDK" in str(result)
            mock_suite.disconnect.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_trailing_stop_sdk_exception_handling(self, bot, mock_sdk_adapter):
        """Test trailing stop when SDK raises exception"""
        mock_sdk_adapter.create_suite = AsyncMock(side_effect=Exception("SDK connection failed"))
        
        with patch('trading_bot.sdk_adapter', mock_sdk_adapter):
            result = await bot.place_trailing_stop_order("MNQ", "BUY", 1, 25.0)
            
            # Should handle exception gracefully
            assert "error" in result or "SDK" in str(result)
    
    @pytest.mark.asyncio
    async def test_trailing_stop_side_conversion(self, bot, mock_sdk_adapter):
        """Test that side is correctly converted for SDK"""
        mock_suite = MagicMock()
        mock_order_manager = MagicMock()
        mock_response = MagicMock()
        mock_response.order_id = 12345
        mock_order_manager.place_trailing_stop_order = AsyncMock(return_value=mock_response)
        mock_suite.orders = mock_order_manager
        mock_suite.instrument_id = "CON.F.US.MNQ.Z25"
        mock_suite.disconnect = AsyncMock()
        mock_sdk_adapter.create_suite = AsyncMock(return_value=mock_suite)
        
        with patch('trading_bot.sdk_adapter', mock_sdk_adapter):
            # Test BUY side (should be 0)
            await bot.place_trailing_stop_order("MNQ", "BUY", 1, 25.0)
            call_args = mock_order_manager.place_trailing_stop_order.call_args
            assert call_args[1]["side"] == 0
            
            # Test SELL side (should be 1)
            await bot.place_trailing_stop_order("MNQ", "SELL", 1, 25.0)
            call_args = mock_order_manager.place_trailing_stop_order.call_args
            assert call_args[1]["side"] == 1
    
    @pytest.mark.asyncio
    async def test_trailing_stop_parameters_passed(self, bot, mock_sdk_adapter):
        """Test that all parameters are correctly passed to SDK"""
        mock_suite = MagicMock()
        mock_order_manager = MagicMock()
        mock_response = MagicMock()
        mock_response.order_id = 12345
        mock_order_manager.place_trailing_stop_order = AsyncMock(return_value=mock_response)
        mock_suite.orders = mock_order_manager
        mock_suite.instrument_id = "CON.F.US.MNQ.Z25"
        mock_suite.disconnect = AsyncMock()
        mock_sdk_adapter.create_suite = AsyncMock(return_value=mock_suite)
        
        with patch('trading_bot.sdk_adapter', mock_sdk_adapter):
            await bot.place_trailing_stop_order("MNQ", "BUY", 2, 30.0, account_id=99999)
            
            # Verify parameters
            call_args = mock_order_manager.place_trailing_stop_order.call_args
            assert call_args[1]["contract_id"] == "CON.F.US.MNQ.Z25"
            assert call_args[1]["side"] == 0
            assert call_args[1]["size"] == 2
            assert call_args[1]["trail_price"] == 30.0
            assert call_args[1]["account_id"] == 99999


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

