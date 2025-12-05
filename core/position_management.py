"""
Position Management Module - Handles position-related operations.

This module provides position management methods that work with broker adapters
to modify stop loss, take profit, and get linked orders.
"""

import logging
from typing import Dict, List, Optional
from core.interfaces import PositionInterface

logger = logging.getLogger(__name__)


class PositionManager:
    """
    Manages position-related operations.
    
    Works with broker adapters to provide position management functionality
    like modifying stop loss, take profit, and getting linked orders.
    """
    
    def __init__(self, broker_adapter: PositionInterface):
        """
        Initialize position manager.
        
        Args:
            broker_adapter: Broker adapter implementing PositionInterface
        """
        self.broker_adapter = broker_adapter
        logger.debug("PositionManager initialized")
    
    async def modify_stop_loss(
        self,
        position_id: str,
        new_stop_price: float,
        account_id: str
    ) -> Dict:
        """
        Modify the stop loss order attached to a position.
        
        Args:
            position_id: Position ID
            new_stop_price: New stop loss price
            account_id: Account ID
            
        Returns:
            Dict: Modify response or error
        """
        try:
            # Get linked orders for the position
            linked_orders = await self.broker_adapter.get_linked_orders(position_id, account_id)
            
            if isinstance(linked_orders, dict) and "error" in linked_orders:
                return linked_orders
            
            # Find the stop loss order (type 4 = Stop order)
            stop_order = None
            for order in linked_orders:
                if order.get('type') == 4:  # Stop order type
                    stop_order = order
                    break
            
            if not stop_order:
                return {"error": "No stop loss order found for this position"}
            
            stop_order_id = str(stop_order.get('id', ''))
            
            # Modify the stop loss order (only price, not size)
            result = await self.broker_adapter.modify_order(
                order_id=stop_order_id,
                new_quantity=None,  # Don't change size
                new_price=new_stop_price,
                account_id=account_id,
                order_type=4  # Stop order
            )
            
            if hasattr(result, 'success') and not result.success:
                return {"error": result.error or "Failed to modify stop loss"}
            elif isinstance(result, dict) and "error" in result:
                return result
            
            logger.info(f"Stop loss modified successfully for position {position_id}: new price = {new_stop_price}")
            return {
                "success": True,
                "position_id": position_id,
                "stop_order_id": stop_order_id,
                "new_stop_price": new_stop_price,
                "message": f"Stop loss modified to ${new_stop_price}"
            }
            
        except Exception as e:
            logger.error(f"Failed to modify stop loss: {str(e)}")
            return {"error": str(e)}
    
    async def modify_take_profit(
        self,
        position_id: str,
        new_tp_price: float,
        account_id: str
    ) -> Dict:
        """
        Modify the take profit order attached to a position.
        
        Args:
            position_id: Position ID
            new_tp_price: New take profit price
            account_id: Account ID
            
        Returns:
            Dict: Modify response or error
        """
        try:
            # Get linked orders for the position
            linked_orders = await self.broker_adapter.get_linked_orders(position_id, account_id)
            
            if isinstance(linked_orders, dict) and "error" in linked_orders:
                return linked_orders
            
            # Find the take profit order (type 1 = Limit order, opposite side of position)
            tp_order = None
            for order in linked_orders:
                if order.get('type') == 1:  # Limit order type (take profit)
                    tp_order = order
                    break
            
            if not tp_order:
                return {"error": "No take profit order found for this position"}
            
            tp_order_id = str(tp_order.get('id', ''))
            
            # Modify the take profit order (only price, not size)
            result = await self.broker_adapter.modify_order(
                order_id=tp_order_id,
                new_quantity=None,  # Don't change size
                new_price=new_tp_price,
                account_id=account_id,
                order_type=1  # Limit order
            )
            
            if hasattr(result, 'success') and not result.success:
                return {"error": result.error or "Failed to modify take profit"}
            elif isinstance(result, dict) and "error" in result:
                return result
            
            logger.info(f"Take profit modified successfully for position {position_id}: new price = {new_tp_price}")
            return {
                "success": True,
                "position_id": position_id,
                "tp_order_id": tp_order_id,
                "new_tp_price": new_tp_price,
                "message": f"Take profit modified to ${new_tp_price}"
            }
            
        except Exception as e:
            logger.error(f"Failed to modify take profit: {str(e)}")
            return {"error": str(e)}

