"""
Event definitions for event-driven architecture.

Events are used for decoupled communication between components.
"""

from dataclasses import dataclass, field
from typing import Optional, Dict, Any
from datetime import datetime
from enum import Enum


class EventType(Enum):
    """Event types."""
    # Order events
    ORDER_PLACED = "order.placed"
    ORDER_FILLED = "order.filled"
    ORDER_MODIFIED = "order.modified"
    ORDER_CANCELLED = "order.cancelled"
    ORDER_ERROR = "order.error"
    
    # Position events
    POSITION_OPENED = "position.opened"
    POSITION_CLOSED = "position.closed"
    POSITION_MODIFIED = "position.modified"
    
    # Market data events
    QUOTE_UPDATED = "market.quote_updated"
    DEPTH_UPDATED = "market.depth_updated"
    BAR_UPDATED = "market.bar_updated"
    
    # Strategy events
    STRATEGY_SIGNAL = "strategy.signal"
    STRATEGY_EXECUTED = "strategy.executed"
    STRATEGY_ERROR = "strategy.error"
    
    # Risk events
    DLL_WARNING = "risk.dll_warning"
    DLL_VIOLATION = "risk.dll_violation"
    MLL_WARNING = "risk.mll_warning"
    MLL_VIOLATION = "risk.mll_violation"
    RISK_ALERT = "risk.alert"


@dataclass
class BaseEvent:
    """Base event class."""
    event_type: EventType
    timestamp: datetime = field(default_factory=lambda: datetime.now(datetime.UTC))
    source: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class OrderEvent(BaseEvent):
    """Order-related event."""
    order_id: Optional[str] = None
    symbol: Optional[str] = None
    side: Optional[str] = None
    quantity: Optional[int] = None
    price: Optional[float] = None
    account_id: Optional[str] = None
    error: Optional[str] = None


@dataclass
class PositionEvent(BaseEvent):
    """Position-related event."""
    position_id: Optional[str] = None
    symbol: Optional[str] = None
    side: Optional[str] = None
    quantity: Optional[int] = None
    entry_price: Optional[float] = None
    current_price: Optional[float] = None
    pnl: Optional[float] = None
    account_id: Optional[str] = None


@dataclass
class MarketDataEvent(BaseEvent):
    """Market data-related event."""
    symbol: str = ""
    data_type: str = ""  # "quote", "depth", "bar"
    data: Optional[Dict[str, Any]] = None
    
    def __post_init__(self):
        """Initialize data dict if None."""
        if self.data is None:
            self.data = {}


@dataclass
class StrategyEvent(BaseEvent):
    """Strategy-related event."""
    strategy_name: str = ""
    signal: Optional[Dict[str, Any]] = None
    action: Optional[str] = None
    symbol: Optional[str] = None
    error: Optional[str] = None


@dataclass
class RiskEvent(BaseEvent):
    """Risk management event."""
    risk_type: str = ""  # "DLL", "MLL", "POSITION_SIZE", etc.
    level: str = ""  # "WARNING", "VIOLATION", "ALERT"
    account_id: Optional[str] = None
    current_value: Optional[float] = None
    limit_value: Optional[float] = None
    message: Optional[str] = None

