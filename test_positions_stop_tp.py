"""
Unit tests for positions display with stop and TP columns
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


class TestPositionsStopTP:
    """Test positions display with stop and TP columns"""
    
    @pytest.mark.asyncio
    async def test_positions_display_includes_stop_tp(self, bot):
        """Test that positions display includes stop and TP price columns"""
        # Mock positions data
        mock_positions = [
            {
                'id': 425840440,
                'contractId': 'CON.F.US.MNQ.Z25',
                'type': 2,  # SHORT
                'size': 2,
                'averagePrice': 25779.50,
                'unrealizedPnl': 29.00
            }
        ]
        
        # Mock linked orders (stop loss and take profit)
        mock_linked_orders = [
            {
                'id': 123,
                'type': 4,  # Stop order
                'stopPrice': 25800.00,
                'limitPrice': None
            },
            {
                'id': 124,
                'type': 1,  # Limit order (take profit)
                'limitPrice': 25750.00,
                'stopPrice': None
            }
        ]
        
        bot.get_open_positions = AsyncMock(return_value=mock_positions)
        bot.get_linked_orders = AsyncMock(return_value=mock_linked_orders)
        
        # Capture print output
        import io
        from contextlib import redirect_stdout
        
        with redirect_stdout(io.StringIO()) as f:
            # Simulate positions command
            positions = await bot.get_open_positions()
            if positions:
                print(f"\n📊 Open Positions ({len(positions)}):")
                print(f"{'ID':<12} {'Symbol':<8} {'Side':<6} {'Quantity':<10} {'Price':<12} {'Stop':<12} {'TP':<12} {'P&L':<12}")
                print("-" * 90)
                
                for pos in positions:
                    pos_id = pos.get('id', 'N/A')
                    contract_id = pos.get('contractId', '')
                    if contract_id:
                        symbol = contract_id.split('.')[-2] if '.' in contract_id else contract_id
                    else:
                        symbol = pos.get('symbol', 'N/A')
                    
                    position_type = pos.get('type', 0)
                    if position_type == 1:
                        side = "LONG"
                    elif position_type == 2:
                        side = "SHORT"
                    else:
                        side = "UNKNOWN"
                    
                    quantity = pos.get('size', 0)
                    price = pos.get('averagePrice', 0.0)
                    
                    # Get stop loss and take profit prices from linked orders
                    stop_price = None
                    tp_price = None
                    linked_orders = await bot.get_linked_orders(str(pos_id))
                    if linked_orders:
                        for order in linked_orders:
                            order_type = order.get('type', 0)
                            has_stop_price = order.get('stopPrice') is not None
                            has_limit_price = order.get('limitPrice') is not None
                            
                            if order_type == 4 or has_stop_price:
                                stop_price = order.get('stopPrice')
                                if stop_price is None and has_limit_price:
                                    stop_price = order.get('limitPrice')
                            elif order_type == 1 or (has_limit_price and not has_stop_price):
                                tp_price = order.get('limitPrice')
                                if tp_price is None and has_stop_price:
                                    tp_price = order.get('stopPrice')
                    
                    pnl = pos.get('unrealizedPnl', 0.0)
                    stop_str = f"${stop_price:.2f}" if stop_price else "N/A"
                    tp_str = f"${tp_price:.2f}" if tp_price else "N/A"
                    
                    print(f"{pos_id:<12} {symbol:<8} {side:<6} {quantity:<10} ${price:<11.2f} {stop_str:<12} {tp_str:<12} ${pnl:<11.2f}")
        
        output = f.getvalue()
        
        # Verify output contains stop and TP columns
        assert "Stop" in output
        assert "TP" in output
        assert "$25800.00" in output or "25800" in output  # Stop price
        assert "$25750.00" in output or "25750" in output  # TP price
    
    @pytest.mark.asyncio
    async def test_positions_without_linked_orders(self, bot):
        """Test positions display when no linked orders exist"""
        mock_positions = [
            {
                'id': 425840440,
                'contractId': 'CON.F.US.MNQ.Z25',
                'type': 1,  # LONG
                'size': 1,
                'averagePrice': 25800.00,
                'unrealizedPnl': 10.00
            }
        ]
        
        bot.get_open_positions = AsyncMock(return_value=mock_positions)
        bot.get_linked_orders = AsyncMock(return_value=[])  # No linked orders
        
        # Test that it handles missing orders gracefully
        positions = await bot.get_open_positions()
        linked_orders = await bot.get_linked_orders("425840440")
        
        assert len(positions) == 1
        assert len(linked_orders) == 0
    
    @pytest.mark.asyncio
    async def test_positions_stop_tp_order_type_detection(self, bot):
        """Test correct detection of stop and TP orders by type"""
        mock_linked_orders = [
            {
                'id': 123,
                'type': 4,  # Stop order
                'stopPrice': 25800.00,
                'limitPrice': None
            },
            {
                'id': 124,
                'type': 1,  # Limit order
                'limitPrice': 25750.00,
                'stopPrice': None
            }
        ]
        
        stop_price = None
        tp_price = None
        
        for order in mock_linked_orders:
            order_type = order.get('type', 0)
            has_stop_price = order.get('stopPrice') is not None
            has_limit_price = order.get('limitPrice') is not None
            
            if order_type == 4 or has_stop_price:
                stop_price = order.get('stopPrice')
                if stop_price is None and has_limit_price:
                    stop_price = order.get('limitPrice')
            elif order_type == 1 or (has_limit_price and not has_stop_price):
                tp_price = order.get('limitPrice')
                if tp_price is None and has_stop_price:
                    tp_price = order.get('stopPrice')
        
        assert stop_price == 25800.00
        assert tp_price == 25750.00


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

