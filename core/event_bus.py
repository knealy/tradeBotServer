"""
Event Bus for Trading Bot

Central pub/sub messaging system for event-driven architecture.
Allows components to react to events without tight coupling.
"""

import asyncio
import logging
from typing import Callable, Dict, List, Optional
from collections import defaultdict

from core.events import Event, EventType

logger = logging.getLogger(__name__)


class EventBus:
    """
    Central event bus for pub/sub messaging.
    
    Components can subscribe to event types and receive callbacks
    when those events are published. This decouples event producers
    from consumers.
    
    Features:
    - Asynchronous event processing
    - Multiple subscribers per event type
    - Error isolation (one subscriber error doesn't affect others)
    - Graceful shutdown
    """
    
    def __init__(self):
        self._subscribers: Dict[EventType, List[Callable]] = defaultdict(list)
        self._event_queue: asyncio.Queue = asyncio.Queue()
        self._running: bool = False
        self._processor_task: Optional[asyncio.Task] = None
        self._event_count: Dict[EventType, int] = defaultdict(int)
    
    def subscribe(self, event_type: EventType, callback: Callable):
        """
        Subscribe to an event type.
        
        Args:
            event_type: Type of event to subscribe to
            callback: Async or sync function to call when event occurs
        """
        self._subscribers[event_type].append(callback)
        logger.info(f"📡 Subscribed to {event_type.value} (total subscribers: {len(self._subscribers[event_type])})")
    
    def unsubscribe(self, event_type: EventType, callback: Callable):
        """
        Unsubscribe from an event type.
        
        Args:
            event_type: Type of event to unsubscribe from
            callback: Callback function to remove
        """
        if callback in self._subscribers[event_type]:
            self._subscribers[event_type].remove(callback)
            logger.debug(f"📡 Unsubscribed from {event_type.value}")
    
    async def publish(self, event: Event):
        """
        Publish an event to all subscribers.
        
        Args:
            event: Event to publish
        """
        if not self._running:
            logger.warning(f"⚠️  EventBus not running, event dropped: {event}")
            return
        
        await self._event_queue.put(event)
        self._event_count[event.type] += 1
    
    async def _process_events(self):
        """Background task to process events from the queue."""
        logger.info("📡 Event processor started")
        
        while self._running:
            try:
                # Wait for event with timeout to allow checking _running flag
                event = await asyncio.wait_for(
                    self._event_queue.get(), 
                    timeout=1.0
                )
                
                # Call all subscribers for this event type
                callbacks = self._subscribers.get(event.type, [])
                
                if not callbacks:
                    logger.debug(f"No subscribers for {event.type.value}")
                    continue
                
                # Execute all callbacks (isolate errors)
                for callback in callbacks:
                    try:
                        if asyncio.iscoroutinefunction(callback):
                            await callback(event)
                        else:
                            # Run sync callbacks in executor to avoid blocking
                            await asyncio.get_event_loop().run_in_executor(
                                None, callback, event
                            )
                    except Exception as e:
                        logger.error(f"❌ Error in event callback for {event.type.value}: {e}", exc_info=True)
                
            except asyncio.TimeoutError:
                # No event received, continue loop
                continue
            except Exception as e:
                logger.error(f"❌ Error processing event: {e}", exc_info=True)
        
        logger.info("📡 Event processor stopped")
    
    async def start(self):
        """Start the event bus."""
        if self._running:
            logger.warning("⚠️  EventBus already running")
            return
        
        self._running = True
        self._processor_task = asyncio.create_task(self._process_events())
        logger.info("📡 EventBus started")
    
    async def stop(self):
        """Stop the event bus and process remaining events."""
        if not self._running:
            return
        
        logger.info("📡 Stopping EventBus...")
        self._running = False
        
        # Wait for processor to finish
        if self._processor_task:
            try:
                await asyncio.wait_for(self._processor_task, timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning("⚠️  EventBus processor didn't stop gracefully")
                self._processor_task.cancel()
        
        # Log statistics
        logger.info("📊 EventBus statistics:")
        for event_type, count in self._event_count.items():
            logger.info(f"  {event_type.value}: {count} events")
        
        logger.info("📡 EventBus stopped")
    
    def get_statistics(self) -> Dict[str, any]:
        """
        Get event bus statistics.
        
        Returns:
            Dictionary with statistics
        """
        return {
            "running": self._running,
            "queue_size": self._event_queue.qsize(),
            "subscriber_counts": {
                event_type.value: len(callbacks)
                for event_type, callbacks in self._subscribers.items()
            },
            "event_counts": {
                event_type.value: count
                for event_type, count in self._event_count.items()
            }
        }
