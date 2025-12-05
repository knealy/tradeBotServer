"""
Event-driven architecture components.

This module provides an event bus for decoupled communication
between components.
"""

from .event_bus import EventBus, get_event_bus
from .events import (
    OrderEvent,
    PositionEvent,
    MarketDataEvent,
    StrategyEvent,
    RiskEvent,
    EventType,
)

__all__ = [
    'EventBus',
    'get_event_bus',
    'OrderEvent',
    'PositionEvent',
    'MarketDataEvent',
    'StrategyEvent',
    'RiskEvent',
    'EventType',
]

