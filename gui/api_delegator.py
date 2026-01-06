"""
API Delegator - Thin wrapper that maps GUI endpoints to trading_bot methods.

Enforces the "GUI calls CLI core methods" rule to prevent duplicate business logic.
"""

import logging
from typing import Dict, Any, Optional, Callable
from aiohttp import web

logger = logging.getLogger(__name__)


class APIDelegator:
    """
    Delegates GUI API requests to trading_bot methods with consistent error handling.
    
    This ensures all GUI operations use the same code paths as CLI commands,
    preventing logic drift and bugs from duplicate implementations.
    """
    
    def __init__(self, trading_bot):
        """
        Initialize API delegator.
        
        Args:
            trading_bot: TopStepXTradingBot instance
        """
        self.trading_bot = trading_bot
        
        # Map GUI endpoints to trading_bot methods
        self._endpoint_map = {
            'place_order': self._delegate_place_order,
            'cancel_order': self._delegate_cancel_order,
            'close_position': self._delegate_close_position,
            'get_orders': self._delegate_get_orders,
            'get_positions': self._delegate_get_positions,
            'get_account_info': self._delegate_get_account_info,
        }
    
    async def delegate(self, endpoint: str, request: web.Request) -> Dict[str, Any]:
        """
        Delegate request to appropriate trading_bot method.
        
        Args:
            endpoint: Endpoint name (e.g., 'place_order')
            request: aiohttp request object
            
        Returns:
            Response dictionary
        """
        if endpoint not in self._endpoint_map:
            return {
                'error': f'Unknown endpoint: {endpoint}',
                'available_endpoints': list(self._endpoint_map.keys())
            }
        
        try:
            handler = self._endpoint_map[endpoint]
            return await handler(request)
        except Exception as e:
            logger.error(f"Error delegating {endpoint}: {e}", exc_info=True)
            return {
                'error': f'Delegation failed: {str(e)}',
                'endpoint': endpoint
            }
    
    async def _delegate_place_order(self, request: web.Request) -> Dict[str, Any]:
        """Delegate order placement to trading_bot.place_oco_bracket_with_stop_entry."""
        try:
            data = await request.json()
            
            # Extract parameters
            symbol = data.get('symbol')
            side = data.get('side', 'BUY')
            quantity = int(data.get('quantity', 1))
            entry_price = data.get('entry_price')
            stop_loss_price = data.get('stop_loss_price')
            take_profit_price = data.get('take_profit_price')
            enable_bracket = data.get('enable_bracket', False)
            account_id = data.get('account_id')
            
            if not symbol:
                return {'error': 'Symbol is required'}
            
            if not account_id:
                # Get from selected_account
                if hasattr(self.trading_bot, 'selected_account') and self.trading_bot.selected_account:
                    if isinstance(self.trading_bot.selected_account, dict):
                        account_id = self.trading_bot.selected_account.get('id')
                    else:
                        account_id = str(self.trading_bot.selected_account)
            
            if not account_id:
                return {'error': 'Account ID is required'}
            
            # Use canonical method
            result = await self.trading_bot.place_oco_bracket_with_stop_entry(
                symbol=symbol,
                side=side,
                quantity=quantity,
                entry_price=entry_price,
                stop_loss_price=stop_loss_price,
                take_profit_price=take_profit_price,
                account_id=account_id,
                enable_breakeven=False,
                strategy_name=data.get('strategy_name')
            )
            
            if result.get('error'):
                return {'error': result['error']}
            
            return {
                'success': True,
                'order_id': result.get('orderId') or result.get('order_id'),
                'message': result.get('message', 'Order placed successfully')
            }
        except Exception as e:
            logger.error(f"Error in _delegate_place_order: {e}", exc_info=True)
            return {'error': str(e)}
    
    async def _delegate_cancel_order(self, request: web.Request) -> Dict[str, Any]:
        """Delegate order cancellation to trading_bot.cancel_order."""
        try:
            data = await request.json()
            order_id = data.get('order_id')
            account_id = data.get('account_id')
            
            if not order_id:
                return {'error': 'Order ID is required'}
            
            result = await self.trading_bot.cancel_order(order_id=order_id, account_id=account_id)
            
            if result.get('error'):
                return {'error': result['error']}
            
            return {'success': True, 'message': 'Order canceled successfully'}
        except Exception as e:
            logger.error(f"Error in _delegate_cancel_order: {e}", exc_info=True)
            return {'error': str(e)}
    
    async def _delegate_close_position(self, request: web.Request) -> Dict[str, Any]:
        """Delegate position close to trading_bot.close_position."""
        try:
            data = await request.json()
            position_id = data.get('position_id')
            quantity = data.get('quantity')
            account_id = data.get('account_id')
            
            if not position_id:
                return {'error': 'Position ID is required'}
            
            result = await self.trading_bot.close_position(
                position_id=position_id,
                quantity=quantity,
                account_id=account_id
            )
            
            if result.get('error'):
                return {'error': result['error']}
            
            return {'success': True, 'message': 'Position closed successfully'}
        except Exception as e:
            logger.error(f"Error in _delegate_close_position: {e}", exc_info=True)
            return {'error': str(e)}
    
    async def _delegate_get_orders(self, request: web.Request) -> Dict[str, Any]:
        """Delegate order fetching to trading_bot.get_open_orders."""
        try:
            account_id = request.query.get('account_id')
            if not account_id:
                if hasattr(self.trading_bot, 'selected_account') and self.trading_bot.selected_account:
                    if isinstance(self.trading_bot.selected_account, dict):
                        account_id = self.trading_bot.selected_account.get('id')
                    else:
                        account_id = str(self.trading_bot.selected_account)
            
            orders = await self.trading_bot.get_open_orders(account_id=account_id)
            return {'orders': orders}
        except Exception as e:
            logger.error(f"Error in _delegate_get_orders: {e}", exc_info=True)
            return {'error': str(e), 'orders': []}
    
    async def _delegate_get_positions(self, request: web.Request) -> Dict[str, Any]:
        """Delegate position fetching to trading_bot.get_open_positions."""
        try:
            account_id = request.query.get('account_id')
            if not account_id:
                if hasattr(self.trading_bot, 'selected_account') and self.trading_bot.selected_account:
                    if isinstance(self.trading_bot.selected_account, dict):
                        account_id = self.trading_bot.selected_account.get('id')
                    else:
                        account_id = str(self.trading_bot.selected_account)
            
            positions = await self.trading_bot.get_open_positions(account_id=account_id)
            return {'positions': positions}
        except Exception as e:
            logger.error(f"Error in _delegate_get_positions: {e}", exc_info=True)
            return {'error': str(e), 'positions': []}
    
    async def _delegate_get_account_info(self, request: web.Request) -> Dict[str, Any]:
        """Delegate account info fetching to trading_bot.get_account_info."""
        try:
            account_id = request.query.get('account_id')
            if not account_id:
                if hasattr(self.trading_bot, 'selected_account') and self.trading_bot.selected_account:
                    if isinstance(self.trading_bot.selected_account, dict):
                        account_id = self.trading_bot.selected_account.get('id')
                    else:
                        account_id = str(self.trading_bot.selected_account)
            
            account_info = await self.trading_bot.get_account_info(account_id=account_id)
            return {'account': account_info}
        except Exception as e:
            logger.error(f"Error in _delegate_get_account_info: {e}", exc_info=True)
            return {'error': str(e)}

