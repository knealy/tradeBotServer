"""
Market Data Interface - Abstract interface for market data operations.

This interface abstracts market data fetching across different brokers.
"""

from abc import ABC, abstractmethod
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Bar:
    """OHLCV bar data."""
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    symbol: str
    timeframe: str
    raw_data: Optional[Dict[str, Any]] = None


@dataclass
class Quote:
    """Market quote (bid/ask/last)."""
    symbol: str
    bid: Optional[float] = None
    ask: Optional[float] = None
    last: Optional[float] = None
    volume: Optional[int] = None
    timestamp: Optional[datetime] = None
    raw_data: Optional[Dict[str, Any]] = None


@dataclass
class DepthLevel:
    """Order book depth level."""
    price: float
    quantity: int
    side: str  # "BID" or "ASK"


@dataclass
class Depth:
    """Market depth (order book)."""
    symbol: str
    bids: List[DepthLevel]
    asks: List[DepthLevel]
    timestamp: Optional[datetime] = None
    raw_data: Optional[Dict[str, Any]] = None


class MarketDataInterface(ABC):
    """
    Abstract interface for market data operations.
    
    All broker implementations must implement this interface.
    """
    
    @abstractmethod
    async def get_historical_data(
        self,
        symbol: str,
        timeframe: str = "1m",
        limit: int = 100,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        **kwargs
    ) -> List[Bar]:
        """
        Get historical bar data.
        
        Args:
            symbol: Trading symbol
            timeframe: Timeframe (e.g., "1m", "5m", "1h")
            limit: Maximum number of bars
            start_time: Start time (optional)
            end_time: End time (optional)
            **kwargs: Additional broker-specific parameters
            
        Returns:
            List of Bar objects
        """
        pass
    
    @abstractmethod
    async def get_market_quote(
        self,
        symbol: str,
        **kwargs
    ) -> Optional[Quote]:
        """
        Get current market quote.
        
        Args:
            symbol: Trading symbol
            **kwargs: Additional broker-specific parameters
            
        Returns:
            Quote object or None if unavailable
        """
        pass
    
    @abstractmethod
    async def get_market_depth(
        self,
        symbol: str,
        **kwargs
    ) -> Optional[Depth]:
        """
        Get market depth (order book).
        
        Args:
            symbol: Trading symbol
            **kwargs: Additional broker-specific parameters
            
        Returns:
            Depth object or None if unavailable
        """
        pass
    
    @abstractmethod
    async def get_available_contracts(
        self,
        use_cache: bool = True,
        **kwargs
    ) -> List[Dict[str, Any]]:
        """
        Get available trading contracts.
        
        Args:
            use_cache: Whether to use cached data
            **kwargs: Additional broker-specific parameters
            
        Returns:
            List of contract dictionaries
        """
        pass

