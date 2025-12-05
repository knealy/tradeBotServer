"""
Position Interface - Abstract interface for position operations.

This interface abstracts position management across different brokers.
"""

from abc import ABC, abstractmethod
from typing import Optional, List, Dict, Any
from dataclasses import dataclass


@dataclass
class Position:
    """Position data structure."""
    position_id: str
    symbol: str
    side: str  # "LONG" or "SHORT"
    quantity: int
    entry_price: float
    current_price: Optional[float] = None
    unrealized_pnl: Optional[float] = None
    account_id: Optional[str] = None
    raw_data: Optional[Dict[str, Any]] = None


@dataclass
class CloseResponse:
    """Response from position close operation."""
    success: bool
    position_id: Optional[str] = None
    message: Optional[str] = None
    error: Optional[str] = None
    raw_response: Optional[Dict[str, Any]] = None


class PositionInterface(ABC):
    """
    Abstract interface for position operations.
    
    All broker implementations must implement this interface.
    """
    
    @abstractmethod
    async def get_positions(
        self,
        account_id: Optional[str] = None,
        **kwargs
    ) -> List[Position]:
        """
        Get all open positions.
        
        Args:
            account_id: Account ID (optional)
            **kwargs: Additional broker-specific parameters
            
        Returns:
            List of Position objects
        """
        pass
    
    @abstractmethod
    async def get_position_details(
        self,
        position_id: str,
        account_id: Optional[str] = None,
        **kwargs
    ) -> Optional[Position]:
        """
        Get details for a specific position.
        
        Args:
            position_id: Position ID
            account_id: Account ID (optional)
            **kwargs: Additional broker-specific parameters
            
        Returns:
            Position object or None if not found
        """
        pass
    
    @abstractmethod
    async def close_position(
        self,
        position_id: str,
        quantity: Optional[int] = None,
        account_id: Optional[str] = None,
        **kwargs
    ) -> CloseResponse:
        """
        Close a position (fully or partially).
        
        Args:
            position_id: Position ID to close
            quantity: Quantity to close (None = close all)
            account_id: Account ID (optional)
            **kwargs: Additional broker-specific parameters
            
        Returns:
            CloseResponse with close operation details
        """
        pass
    
    @abstractmethod
    async def flatten_all_positions(
        self,
        account_id: Optional[str] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Close all open positions (flatten account).
        
        Args:
            account_id: Account ID (optional)
            **kwargs: Additional broker-specific parameters
            
        Returns:
            Dictionary with flatten operation results
        """
        pass

