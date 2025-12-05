"""
Core interfaces for broker abstraction (Translation Layer).

These interfaces abstract away broker-specific implementations,
allowing easy migration to other brokers/platforms.
"""

from .order_interface import (
    OrderInterface,
    OrderResponse,
    ModifyOrderResponse,
    CancelResponse,
    OrderSide,
    OrderType
)
from .position_interface import PositionInterface, Position, CloseResponse
from .market_data_interface import MarketDataInterface, Bar, Quote, Depth, DepthLevel

__all__ = [
    'OrderInterface',
    'OrderResponse',
    'ModifyOrderResponse',
    'CancelResponse',
    'OrderSide',
    'OrderType',
    'PositionInterface',
    'Position',
    'CloseResponse',
    'MarketDataInterface',
    'Bar',
    'Quote',
    'Depth',
    'DepthLevel',
]

