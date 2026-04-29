"""
Order Execution Manager - High-level order execution logic.

This module provides order execution orchestration that uses
the broker adapter for actual API calls.
"""

import logging
from typing import Optional, Dict, Any
from datetime import datetime

from core.interfaces import OrderInterface, OrderResponse
from core.event_bus import EventBus
from core.events import Event, EventType

logger = logging.getLogger(__name__)


class OrderExecutor:
    """
    High-level order execution manager.
    
    Orchestrates order placement using broker adapter and publishes events.
    """
    
    def __init__(
        self,
        broker_adapter: OrderInterface,
        event_bus: Optional[EventBus] = None,
        selected_account: Optional[Dict] = None
    ):
        """
        Initialize order executor.
        
        Args:
            broker_adapter: Broker adapter implementing OrderInterface
            event_bus: Optional event bus for publishing events
            selected_account: Currently selected account
        """
        self.broker = broker_adapter
        self.events = event_bus
        self.selected_account = selected_account
        self._order_counter = 0
        logger.debug("OrderExecutor initialized")
    
    def set_selected_account(self, account: Optional[Dict]) -> None:
        """Set the selected account."""
        self.selected_account = account
    
    def _generate_unique_custom_tag(self, order_type: str = "order", strategy_name: Optional[str] = None) -> str:
        """
        Generate a unique custom tag for orders.
        
        Args:
            order_type: Type of order
            strategy_name: Optional strategy name
            
        Returns:
            Custom tag string
        """
        self._order_counter += 1
        timestamp = int(datetime.now().timestamp())
        bot_tag_prefix = "TradingBot-v1.0"
        
        if strategy_name:
            clean_strategy = strategy_name.lower().replace(' ', '_').replace('-', '_')
            return f"{bot_tag_prefix}-strategy-{clean_strategy}-{order_type}-{self._order_counter}-{timestamp}"
        else:
            return f"{bot_tag_prefix}-{order_type}-{self._order_counter}-{timestamp}"
    
    async def place_market_order(
        self,
        symbol: str,
        side: str,
        quantity: int,
        account_id: Optional[str] = None,
        stop_loss_ticks: Optional[int] = None,
        take_profit_ticks: Optional[int] = None,
        limit_price: Optional[float] = None,
        strategy_name: Optional[str] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Place a market order (or limit if limit_price provided).
        
        Args:
            symbol: Trading symbol
            side: "BUY" or "SELL"
            quantity: Number of contracts
            account_id: Account ID (uses selected account if not provided)
            stop_loss_ticks: Optional stop loss in ticks
            take_profit_ticks: Optional take profit in ticks
            limit_price: Optional limit price (makes it a limit order)
            strategy_name: Optional strategy name for tagging
            **kwargs: Additional parameters
            
        Returns:
            Order response dictionary
        """
        # Determine account
        target_account = account_id or (self.selected_account.get('id') if self.selected_account else None)
        
        if not target_account:
            return {"error": "No account selected"}
        
        # Determine order type
        order_type = "limit" if limit_price else "market"
        
        # Place order via broker adapter
        try:
            response = await self.broker.place_market_order(
                symbol=symbol,
                side=side,
                quantity=quantity,
                account_id=target_account,
                stop_loss_ticks=stop_loss_ticks,
                take_profit_ticks=take_profit_ticks,
                limit_price=limit_price,
                order_type=order_type,
                strategy_name=strategy_name,
                custom_tag=self._generate_unique_custom_tag(order_type, strategy_name),
                **kwargs
            )
            
            # Publish event if event bus available
            if self.events and isinstance(response, OrderResponse) and response.success:
                event = Event(
                    type=EventType.ORDER_PLACED,
                    source="OrderExecutor",
                    data={
                        "order_id": response.order_id,
                        "symbol": symbol,
                        "side": side,
                        "quantity": quantity,
                        "price": limit_price,
                        "account_id": target_account,
                    },
                )
                await self.events.publish(event)
            
            # Convert OrderResponse to dict for backward compatibility
            if isinstance(response, OrderResponse):
                result = {
                    "success": response.success,
                    "orderId": response.order_id,
                    "message": response.message,
                }
                if response.error:
                    result["error"] = response.error
                if response.raw_response:
                    result.update(response.raw_response)
                return result
            
            return response
            
        except Exception as e:
            logger.error(f"Failed to place order: {e}")
            return {"error": str(e)}

    async def close_position_partial(
        self,
        position_id: str,
        quantity: int,
        account_id: Optional[str] = None,
        strategy_name: Optional[str] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Close part of an open position (scale-out). ``quantity`` is contracts to close.

        Delegates to the broker ``close_position`` with an explicit quantity; behavior
        depends on adapter support.
        """
        target_account = account_id or (
            self.selected_account.get("id") if self.selected_account else None
        )
        if not target_account:
            return {"success": False, "error": "No account selected"}
        try:
            q = int(quantity)
        except (TypeError, ValueError):
            return {"success": False, "error": "quantity must be a positive integer"}
        if q <= 0:
            return {"success": False, "error": "quantity must be a positive integer"}
        if strategy_name:
            kwargs.setdefault("strategy_name", strategy_name)
        try:
            resp = await self.broker.close_position(
                position_id,
                quantity=q,
                account_id=str(target_account),
                **kwargs,
            )
            if hasattr(resp, "success"):
                out: Dict[str, Any] = {
                    "success": bool(resp.success),
                    "position_id": getattr(resp, "position_id", None) or position_id,
                }
                if getattr(resp, "message", None):
                    out["message"] = resp.message
                if getattr(resp, "error", None):
                    out["error"] = resp.error
                if getattr(resp, "raw_response", None):
                    out["raw_response"] = resp.raw_response
                return out
            return {"success": False, "error": "unexpected_close_response", "raw": resp}
        except Exception as e:
            logger.error("Failed partial close for %s: %s", position_id, e)
            return {"success": False, "error": str(e)}

