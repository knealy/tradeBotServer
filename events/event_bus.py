"""
Event Bus - Central event distribution system.

Implements publish-subscribe pattern for decoupled component communication.
"""

import asyncio
import logging
from typing import Callable, Dict, List, Any, Optional
from collections import defaultdict
from .events import BaseEvent, EventType

logger = logging.getLogger(__name__)


class EventBus:
    """
    Central event bus for event-driven architecture.
    
    Components can subscribe to events and publish events
    without direct dependencies.
    """
    
    def __init__(self):
        """Initialize the event bus."""
        self._subscribers: Dict[EventType, List[Callable]] = defaultdict(list)
        self._wildcard_subscribers: List[Callable] = []
        self._lock = asyncio.Lock()
        self._event_history: List[BaseEvent] = []
        self._max_history = 1000  # Keep last 1000 events
        
    async def subscribe(
        self,
        event_type: EventType,
        callback: Callable[[BaseEvent], Any],
        wildcard: bool = False
    ) -> None:
        """
        Subscribe to events.
        
        Args:
            event_type: Event type to subscribe to
            callback: Async callback function(event: BaseEvent) -> Any
            wildcard: If True, subscribe to all events
        """
        async with self._lock:
            if wildcard:
                if callback not in self._wildcard_subscribers:
                    self._wildcard_subscribers.append(callback)
                    logger.debug(f"Subscribed to all events: {callback.__name__}")
            else:
                if callback not in self._subscribers[event_type]:
                    self._subscribers[event_type].append(callback)
                    logger.debug(f"Subscribed to {event_type.value}: {callback.__name__}")
    
    async def unsubscribe(
        self,
        event_type: EventType,
        callback: Callable[[BaseEvent], Any]
    ) -> None:
        """
        Unsubscribe from events.
        
        Args:
            event_type: Event type to unsubscribe from
            callback: Callback function to remove
        """
        async with self._lock:
            if callback in self._subscribers[event_type]:
                self._subscribers[event_type].remove(callback)
                logger.debug(f"Unsubscribed from {event_type.value}: {callback.__name__}")
            if callback in self._wildcard_subscribers:
                self._wildcard_subscribers.remove(callback)
                logger.debug(f"Unsubscribed from all events: {callback.__name__}")
    
    async def publish(self, event: BaseEvent) -> None:
        """
        Publish an event to all subscribers.
        
        Args:
            event: Event to publish
        """
        # Add to history
        async with self._lock:
            self._event_history.append(event)
            if len(self._event_history) > self._max_history:
                self._event_history.pop(0)
        
        # Get subscribers (copy to avoid lock contention)
        subscribers = []
        wildcard_subscribers = []
        async with self._lock:
            subscribers = self._subscribers[event.event_type].copy()
            wildcard_subscribers = self._wildcard_subscribers.copy()
        
        # Notify type-specific subscribers
        for callback in subscribers:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(event)
                else:
                    callback(event)
            except Exception as e:
                logger.error(f"Error in event subscriber {callback.__name__}: {e}", exc_info=True)
        
        # Notify wildcard subscribers
        for callback in wildcard_subscribers:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(event)
                else:
                    callback(event)
            except Exception as e:
                logger.error(f"Error in wildcard subscriber {callback.__name__}: {e}", exc_info=True)
    
    async def get_event_history(
        self,
        event_type: Optional[EventType] = None,
        limit: int = 100
    ) -> List[BaseEvent]:
        """
        Get recent event history.
        
        Args:
            event_type: Filter by event type (optional)
            limit: Maximum number of events to return
            
        Returns:
            List of recent events
        """
        async with self._lock:
            events = self._event_history.copy()
        
        if event_type:
            events = [e for e in events if e.event_type == event_type]
        
        return events[-limit:]
    
    async def clear_history(self) -> None:
        """Clear event history."""
        async with self._lock:
            self._event_history.clear()


# Global event bus instance
_event_bus: Optional[EventBus] = None


def get_event_bus() -> EventBus:
    """
    Get the global event bus instance (singleton).
    
    Returns:
        EventBus instance
    """
    global _event_bus
    if _event_bus is None:
        _event_bus = EventBus()
    return _event_bus

