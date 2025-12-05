"""
Order Interface - Abstract interface for order operations.

This interface abstracts order execution across different brokers,
enabling easy migration and multi-broker support.
"""

from abc import ABC, abstractmethod
from typing import Optional, Dict, Any
from dataclasses import dataclass
from enum import Enum


class OrderSide(Enum):
    """Order side (buy/sell)."""
    BUY = "BUY"
    SELL = "SELL"
    LONG = "LONG"
    SHORT = "SHORT"


class OrderType(Enum):
    """Order type."""
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"
    STOP_LIMIT = "STOP_LIMIT"


@dataclass
class OrderResponse:
    """Response from order placement."""
    success: bool
    order_id: Optional[str] = None
    message: Optional[str] = None
    error: Optional[str] = None
    raw_response: Optional[Dict[str, Any]] = None


@dataclass
class ModifyOrderResponse:
    """Response from order modification."""
    success: bool
    order_id: Optional[str] = None
    message: Optional[str] = None
    error: Optional[str] = None


@dataclass
class CancelResponse:
    """Response from order cancellation."""
    success: bool
    order_id: Optional[str] = None
    message: Optional[str] = None
    error: Optional[str] = None


class OrderInterface(ABC):
    """
    Abstract interface for order operations.
    
    All broker implementations must implement this interface.
    """
    
    @abstractmethod
    async def place_market_order(
        self,
        symbol: str,
        side: str,
        quantity: int,
        account_id: Optional[str] = None,
        **kwargs
    ) -> OrderResponse:
        """
        Place a market order.
        
        Args:
            symbol: Trading symbol (e.g., "MNQ")
            side: Order side ("BUY" or "SELL")
            quantity: Number of contracts
            account_id: Account ID (optional)
            **kwargs: Additional broker-specific parameters
            
        Returns:
            OrderResponse with order details
        """
        pass
    
    @abstractmethod
    async def place_limit_order(
        self,
        symbol: str,
        side: str,
        quantity: int,
        price: float,
        account_id: Optional[str] = None,
        **kwargs
    ) -> OrderResponse:
        """
        Place a limit order.
        
        Args:
            symbol: Trading symbol
            side: Order side ("BUY" or "SELL")
            quantity: Number of contracts
            price: Limit price
            account_id: Account ID (optional)
            **kwargs: Additional broker-specific parameters
            
        Returns:
            OrderResponse with order details
        """
        pass
    
    @abstractmethod
    async def place_stop_order(
        self,
        symbol: str,
        side: str,
        quantity: int,
        stop_price: float,
        account_id: Optional[str] = None,
        **kwargs
    ) -> OrderResponse:
        """
        Place a stop order.
        
        Args:
            symbol: Trading symbol
            side: Order side ("BUY" or "SELL")
            quantity: Number of contracts
            stop_price: Stop price
            account_id: Account ID (optional)
            **kwargs: Additional broker-specific parameters
            
        Returns:
            OrderResponse with order details
        """
        pass
    
    @abstractmethod
    async def modify_order(
        self,
        order_id: str,
        price: Optional[float] = None,
        quantity: Optional[int] = None,
        account_id: Optional[str] = None,
        **kwargs
    ) -> ModifyOrderResponse:
        """
        Modify an existing order.
        
        Args:
            order_id: Order ID to modify
            price: New price (optional)
            quantity: New quantity (optional)
            account_id: Account ID (optional)
            **kwargs: Additional broker-specific parameters
            
        Returns:
            ModifyOrderResponse with modification details
        """
        pass
    
    @abstractmethod
    async def cancel_order(
        self,
        order_id: str,
        account_id: Optional[str] = None,
        **kwargs
    ) -> CancelResponse:
        """
        Cancel an order.
        
        Args:
            order_id: Order ID to cancel
            account_id: Account ID (optional)
            **kwargs: Additional broker-specific parameters
            
        Returns:
            CancelResponse with cancellation details
        """
        pass
    
    @abstractmethod
    async def get_open_orders(
        self,
        account_id: Optional[str] = None,
        **kwargs
    ) -> list:
        """
        Get all open orders.
        
        Args:
            account_id: Account ID (optional)
            **kwargs: Additional broker-specific parameters
            
        Returns:
            List of open orders
        """
        pass
    
    @abstractmethod
    async def get_order_history(
        self,
        account_id: Optional[str] = None,
        limit: int = 100,
        **kwargs
    ) -> list:
        """
        Get order history.
        
        Args:
            account_id: Account ID (optional)
            limit: Maximum number of orders to return
            **kwargs: Additional broker-specific parameters
            
        Returns:
            List of historical orders
        """
        pass

