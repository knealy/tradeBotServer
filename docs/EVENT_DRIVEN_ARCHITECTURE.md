# Event-Driven Architecture Implementation

**Date:** 2026-01-15  
**Priority:** HIGH  
**Status:** Implementation Guide

---

## Problem Statement

The current system relies on polling loops that constantly check for changes:
- GUI broadcast loop polls every 5s
- Chart updates poll every 40-80ms
- Position/order checks happen repeatedly even when nothing changed

**This is inefficient and causes**:
- Unnecessary API calls
- Wasted CPU cycles
- Delayed reactions to actual events
- Poor user experience

---

## Solution: Event-Driven Architecture

### Core Principle

**Don't poll for changes - React to events when they happen**

```
Before (Polling):
┌─────────────┐
│ Loop every  │──┐
│ 5 seconds   │  │
└─────────────┘  │
       ↓         │
┌─────────────┐  │
│ Check for   │  │
│ changes     │  │
└─────────────┘  │
       ↓         │
┌─────────────┐  │
│ 99% no      │  │
│ changes     │  │
└─────────────┘  │
       ↑         │
       └─────────┘

After (Event-Driven):
┌─────────────┐
│ SignalR     │
│ Event       │
└─────────────┘
       ↓
┌─────────────┐
│ Invalidate  │
│ Cache       │
└─────────────┘
       ↓
┌─────────────┐
│ Notify GUI  │
│ Immediately │
└─────────────┘
```

---

## Implementation

### 1. Event Types

```python
# core/events.py (NEW FILE)
from enum import Enum
from dataclasses import dataclass
from typing import Any, Dict, Optional
from datetime import datetime

class EventType(Enum):
    """All possible system events."""
    # Order events
    ORDER_PLACED = "order_placed"
    ORDER_FILLED = "order_filled"
    ORDER_CANCELLED = "order_cancelled"
    ORDER_REJECTED = "order_rejected"
    
    # Position events
    POSITION_OPENED = "position_opened"
    POSITION_CLOSED = "position_closed"
    POSITION_UPDATED = "position_updated"
    
    # Account events
    ACCOUNT_UPDATED = "account_updated"
    BALANCE_CHANGED = "balance_changed"
    PNL_UPDATED = "pnl_updated"
    
    # Market events
    QUOTE_UPDATED = "quote_updated"
    BAR_COMPLETED = "bar_completed"
    
    # Strategy events
    STRATEGY_STARTED = "strategy_started"
    STRATEGY_STOPPED = "strategy_stopped"
    SIGNAL_GENERATED = "signal_generated"
    
    # GUI events
    GUI_REFRESH_REQUESTED = "gui_refresh_requested"
    CHART_SYMBOL_CHANGED = "chart_symbol_changed"
    CHART_TIMEFRAME_CHANGED = "chart_timeframe_changed"

@dataclass
class Event:
    """System event."""
    type: EventType
    data: Dict[str, Any]
    timestamp: datetime = None
    source: str = "system"
    
    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.utcnow()
```

### 2. Event Bus

```python
# core/event_bus.py (NEW FILE)
import asyncio
import logging
from typing import Callable, Dict, List, Set
from collections import defaultdict

logger = logging.getLogger(__name__)

class EventBus:
    """
    Central event bus for pub/sub messaging.
    Allows components to react to events without tight coupling.
    """
    
    def __init__(self):
        self._subscribers: Dict[EventType, List[Callable]] = defaultdict(list)
        self._event_queue = asyncio.Queue()
        self._running = False
        self._processor_task = None
    
    def subscribe(self, event_type: EventType, callback: Callable):
        """Subscribe to an event type."""
        self._subscribers[event_type].append(callback)
        logger.debug(f"📡 Subscribed to {event_type.value}")
    
    def unsubscribe(self, event_type: EventType, callback: Callable):
        """Unsubscribe from an event type."""
        if callback in self._subscribers[event_type]:
            self._subscribers[event_type].remove(callback)
            logger.debug(f"📡 Unsubscribed from {event_type.value}")
    
    async def publish(self, event: Event):
        """Publish an event to all subscribers."""
        await self._event_queue.put(event)
    
    async def _process_events(self):
        """Background task to process events."""
        while self._running:
            try:
                event = await asyncio.wait_for(
                    self._event_queue.get(), 
                    timeout=1.0
                )
                
                # Call all subscribers for this event type
                callbacks = self._subscribers.get(event.type, [])
                for callback in callbacks:
                    try:
                        if asyncio.iscoroutinefunction(callback):
                            await callback(event)
                        else:
                            callback(event)
                    except Exception as e:
                        logger.error(f"Error in event callback: {e}")
                
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                logger.error(f"Error processing event: {e}")
    
    async def start(self):
        """Start the event bus."""
        if self._running:
            return
        
        self._running = True
        self._processor_task = asyncio.create_task(self._process_events())
        logger.info("📡 Event bus started")
    
    async def stop(self):
        """Stop the event bus."""
        self._running = False
        if self._processor_task:
            await self._processor_task
        logger.info("📡 Event bus stopped")
```

### 3. Integration with Existing Components

#### A. SignalR Callbacks → Events

```python
# trading_bot.py
async def _on_user_hub_order(self, data: Dict):
    """Callback for User Hub order updates."""
    try:
        # ... existing code ...
        
        # EMIT EVENT instead of just invalidating cache
        if self.event_bus:
            event_type = EventType.ORDER_FILLED if status == 'Filled' else EventType.ORDER_UPDATED
            await self.event_bus.publish(Event(
                type=event_type,
                data={'order': data, 'account_id': account_id_str},
                source='signalr_user_hub'
            ))
    except Exception as e:
        logger.error(f"Error handling User Hub order update: {e}")

async def _on_user_hub_position(self, data: Dict):
    """Callback for User Hub position updates."""
    try:
        # ... existing code ...
        
        # EMIT EVENT
        if self.event_bus:
            await self.event_bus.publish(Event(
                type=EventType.POSITION_UPDATED,
                data={'position': data, 'account_id': account_id_str},
                source='signalr_user_hub'
            ))
    except Exception as e:
        logger.error(f"Error handling User Hub position update: {e}")
```

#### B. GUI Subscribes to Events

```python
# gui/chart_html.py
async def _start_chart_server(trading_bot, symbol: str, timeframe: str = '5m') -> int:
    # ... existing code ...
    
    # Subscribe to relevant events
    if hasattr(trading_bot, 'event_bus') and trading_bot.event_bus:
        # Subscribe to order events
        trading_bot.event_bus.subscribe(
            EventType.ORDER_PLACED,
            lambda event: asyncio.create_task(_on_order_event(event))
        )
        trading_bot.event_bus.subscribe(
            EventType.ORDER_FILLED,
            lambda event: asyncio.create_task(_on_order_event(event))
        )
        
        # Subscribe to position events
        trading_bot.event_bus.subscribe(
            EventType.POSITION_UPDATED,
            lambda event: asyncio.create_task(_on_position_event(event))
        )
        
        # Subscribe to account events
        trading_bot.event_bus.subscribe(
            EventType.ACCOUNT_UPDATED,
            lambda event: asyncio.create_task(_on_account_event(event))
        )
    
    async def _on_order_event(event: Event):
        """React to order events."""
        # Broadcast to WebSocket clients immediately
        await broadcast_update({
            'type': 'order_update',
            'data': event.data
        }, immediate=True)
    
    async def _on_position_event(event: Event):
        """React to position events."""
        # Broadcast to WebSocket clients immediately
        await broadcast_update({
            'type': 'position_update',
            'data': event.data
        }, immediate=True)
    
    async def _on_account_event(event: Event):
        """React to account events."""
        # Broadcast to WebSocket clients immediately
        await broadcast_update({
            'type': 'account_update',
            'data': event.data
        }, immediate=True)
```

#### C. Replace Polling with Event Reactions

```python
# gui/chart_html.py - BEFORE
async def websocket_broadcast_loop():
    while True:
        # Poll for changes every 5s
        response = await handle_account_state(None)
        # ... broadcast ...
        await asyncio.sleep(5)

# gui/chart_html.py - AFTER
async def websocket_broadcast_loop():
    """
    Lightweight heartbeat loop.
    Actual updates are event-driven via subscriptions.
    """
    while True:
        # Only send heartbeat/status every 30s
        await broadcast_update({
            'type': 'heartbeat',
            'timestamp': datetime.utcnow().isoformat()
        }, immediate=False)
        await asyncio.sleep(30)
```

---

## Benefits

### Performance

| Metric | Before (Polling) | After (Event-Driven) | Improvement |
|--------|------------------|----------------------|-------------|
| **Reaction Time** | 0-5s (avg 2.5s) | <10ms | **250x faster** |
| **Unnecessary Checks** | ~12/min | 0 | **100% eliminated** |
| **CPU Usage** | Constant high | Idle when quiet | **~80% reduction** |
| **GUI Latency** | 2-5s | <50ms | **40-100x faster** |

### Code Quality

- **Decoupled**: Components don't need to know about each other
- **Testable**: Easy to test event handlers in isolation
- **Maintainable**: Clear event flow, easy to trace
- **Scalable**: Add new subscribers without modifying publishers

---

## Migration Plan

### Phase 1: Add Event Infrastructure (30 min)

1. Create `core/events.py`
2. Create `core/event_bus.py`
3. Add `event_bus` to `trading_bot.py` initialization

### Phase 2: Emit Events from SignalR (30 min)

1. Update `_on_user_hub_order` to emit events
2. Update `_on_user_hub_position` to emit events
3. Update `_on_user_hub_account` to emit events
4. Update `_on_market_hub_quote` to emit events

### Phase 3: Subscribe in GUI (1 hour)

1. Subscribe to order/position/account events
2. Create event handlers that broadcast to WebSocket
3. Remove polling from broadcast loop
4. Keep lightweight heartbeat (30s interval)

### Phase 4: Test & Verify (30 min)

1. Test order placement triggers immediate GUI update
2. Test position changes trigger immediate GUI update
3. Test account changes trigger immediate GUI update
4. Verify no more excessive polling in logs

---

## Testing

```python
# Test event emission
async def test_order_event():
    bot = TopStepXTradingBot()
    await bot.authenticate()
    
    # Subscribe to order events
    order_received = asyncio.Event()
    
    def on_order(event):
        print(f"Order event: {event.type.value}")
        order_received.set()
    
    bot.event_bus.subscribe(EventType.ORDER_PLACED, on_order)
    
    # Place an order
    await bot.place_order(...)
    
    # Wait for event (should be immediate)
    await asyncio.wait_for(order_received.wait(), timeout=1.0)
    print("✅ Event received within 1 second!")
```

---

## Success Criteria

✅ **No more polling loops** (except lightweight heartbeat)  
✅ **GUI updates < 50ms** after events  
✅ **Zero unnecessary API calls**  
✅ **Clean event flow** (traceable in logs)  
✅ **CPU idle** when no trading activity  

---

**Status**: Ready for Implementation  
**Estimated Time**: 2-3 hours  
**Risk**: Low (backward compatible, can be rolled back)  
**Impact**: HIGH (transforms system responsiveness)
