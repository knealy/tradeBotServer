# Event-Driven Architecture - Quick Start Guide

**For Developers**: How to use the new event system

---

## 🚀 Quick Start

### 1. Publishing Events

```python
from core.events import Event, EventType

# In your code (e.g., after placing an order)
if hasattr(self, 'event_bus') and self.event_bus:
    await self.event_bus.publish(Event(
        type=EventType.ORDER_PLACED,
        data={'order_id': order_id, 'symbol': symbol, 'price': price},
        source='my_component'
    ))
```

### 2. Subscribing to Events

```python
from core.events import EventType

# Define your handler
async def on_order_filled(event):
    print(f"Order filled: {event.data}")
    # Do something with the event

# Subscribe
trading_bot.event_bus.subscribe(EventType.ORDER_FILLED, on_order_filled)
```

### 3. Unsubscribing

```python
# Unsubscribe when no longer needed
trading_bot.event_bus.unsubscribe(EventType.ORDER_FILLED, on_order_filled)
```

---

## 📋 Available Event Types

### Order Events
- `ORDER_PLACED` - New order submitted
- `ORDER_FILLED` - Order completely filled
- `ORDER_CANCELLED` - Order cancelled
- `ORDER_REJECTED` - Order rejected by broker
- `ORDER_UPDATED` - Order status changed

### Position Events
- `POSITION_OPENED` - New position opened
- `POSITION_CLOSED` - Position closed
- `POSITION_UPDATED` - Position size/price changed

### Account Events
- `ACCOUNT_UPDATED` - Account balance/status changed
- `BALANCE_CHANGED` - Account balance changed
- `PNL_UPDATED` - P&L recalculated

### Market Events
- `QUOTE_UPDATED` - New quote received
- `BAR_COMPLETED` - New bar aggregated

### Strategy Events
- `STRATEGY_STARTED` - Strategy activated
- `STRATEGY_STOPPED` - Strategy deactivated
- `SIGNAL_GENERATED` - Trading signal generated

### GUI Events
- `GUI_REFRESH_REQUESTED` - Manual refresh requested
- `CHART_SYMBOL_CHANGED` - Chart symbol changed
- `CHART_TIMEFRAME_CHANGED` - Chart timeframe changed

### System Events
- `SHUTDOWN_REQUESTED` - System shutdown initiated

---

## 💡 Best Practices

### 1. Always Check Event Bus Exists

```python
if hasattr(self, 'event_bus') and self.event_bus:
    await self.event_bus.publish(event)
```

### 2. Use Async Handlers When Possible

```python
# Good - async handler
async def on_event(event):
    await do_async_work()

# Also OK - sync handler (runs in executor)
def on_event(event):
    do_sync_work()
```

### 3. Handle Errors in Subscribers

```python
async def on_event(event):
    try:
        await process_event(event)
    except Exception as e:
        logger.error(f"Error processing event: {e}")
        # Event bus will continue with other subscribers
```

### 4. Include Relevant Data in Events

```python
# Good - includes all relevant data
Event(
    type=EventType.ORDER_FILLED,
    data={
        'order_id': order_id,
        'symbol': symbol,
        'price': fill_price,
        'quantity': quantity,
        'account_id': account_id
    },
    source='order_executor'
)

# Bad - missing important data
Event(
    type=EventType.ORDER_FILLED,
    data={'order_id': order_id},
    source='unknown'
)
```

### 5. Use Descriptive Source Names

```python
# Good
source='signalr_user_hub'
source='overnight_range_strategy'
source='gui_chart_server'

# Bad
source='system'
source='bot'
source='unknown'
```

---

## 🔍 Debugging

### Check Event Bus Status

```python
stats = trading_bot.event_bus.get_statistics()
print(f"Running: {stats['running']}")
print(f"Queue Size: {stats['queue_size']}")
print(f"Subscribers: {stats['subscriber_counts']}")
print(f"Event Counts: {stats['event_counts']}")
```

### Enable Event Logging

```python
import logging
logging.getLogger('core.event_bus').setLevel(logging.DEBUG)
```

### Test Event Flow

```python
# 1. Subscribe to event
test_received = False

async def test_handler(event):
    global test_received
    test_received = True
    print(f"Received: {event}")

trading_bot.event_bus.subscribe(EventType.ORDER_PLACED, test_handler)

# 2. Publish event
await trading_bot.event_bus.publish(Event(
    type=EventType.ORDER_PLACED,
    data={'test': True},
    source='test'
))

# 3. Wait a bit
await asyncio.sleep(0.1)

# 4. Check
assert test_received, "Event not received!"
```

---

## ⚡ Performance Tips

### 1. Use Immediate Flag for Critical Events

```python
# Critical events (order fills, position changes)
await broadcast_update(data, immediate=True)

# Non-critical events (account updates, metrics)
await broadcast_update(data, immediate=False)
```

### 2. Batch Non-Critical Updates

```python
# Instead of emitting 100 events
for item in items:
    await event_bus.publish(Event(...))  # Slow

# Emit one event with all data
await event_bus.publish(Event(
    type=EventType.BATCH_UPDATE,
    data={'items': items}
))  # Fast
```

### 3. Unsubscribe When Done

```python
# In cleanup/shutdown
trading_bot.event_bus.unsubscribe(EventType.ORDER_FILLED, my_handler)
```

---

## 📊 Monitoring

### Event Counts

```python
stats = trading_bot.event_bus.get_statistics()
for event_type, count in stats['event_counts'].items():
    print(f"{event_type}: {count} events")
```

### Subscriber Counts

```python
stats = trading_bot.event_bus.get_statistics()
for event_type, count in stats['subscriber_counts'].items():
    print(f"{event_type}: {count} subscribers")
```

### Queue Size

```python
stats = trading_bot.event_bus.get_statistics()
if stats['queue_size'] > 100:
    logger.warning(f"Event queue backing up: {stats['queue_size']} events")
```

---

## 🎯 Common Patterns

### Pattern 1: React to Order Fills

```python
async def on_order_filled(event):
    order_data = event.data
    logger.info(f"Order filled: {order_data['order_id']}")
    
    # Update GUI
    await update_gui_orders()
    
    # Check if we need to place hedge
    if should_hedge(order_data):
        await place_hedge_order(order_data)

trading_bot.event_bus.subscribe(EventType.ORDER_FILLED, on_order_filled)
```

### Pattern 2: Monitor Position Changes

```python
async def on_position_updated(event):
    position = event.data['position']
    account_id = event.data['account_id']
    
    # Update P&L
    await recalculate_pnl(account_id)
    
    # Check risk limits
    if exceeds_risk_limit(position):
        await send_alert(f"Risk limit exceeded: {position}")

trading_bot.event_bus.subscribe(EventType.POSITION_UPDATED, on_position_updated)
```

### Pattern 3: Aggregate Multiple Events

```python
class EventAggregator:
    def __init__(self):
        self.events = []
    
    async def on_event(self, event):
        self.events.append(event)
        
        # Process in batches of 10
        if len(self.events) >= 10:
            await self.process_batch()
    
    async def process_batch(self):
        # Process all events at once
        await bulk_update(self.events)
        self.events = []

aggregator = EventAggregator()
trading_bot.event_bus.subscribe(EventType.QUOTE_UPDATED, aggregator.on_event)
```

---

## 🚨 Troubleshooting

### Events Not Being Received

1. Check event bus is running:
   ```python
   assert trading_bot.event_bus._running
   ```

2. Check subscription:
   ```python
   stats = trading_bot.event_bus.get_statistics()
   assert 'order_filled' in stats['subscriber_counts']
   ```

3. Check event is being published:
   ```python
   # Add logging in publisher
   logger.info(f"Publishing event: {event}")
   ```

### Event Queue Backing Up

1. Check for slow subscribers:
   ```python
   # Add timing to your handler
   start = time.time()
   await process_event(event)
   elapsed = time.time() - start
   if elapsed > 1.0:
       logger.warning(f"Slow handler: {elapsed}s")
   ```

2. Use async handlers:
   ```python
   # Bad - blocks event loop
   def slow_handler(event):
       time.sleep(5)  # Blocks!
   
   # Good - doesn't block
   async def fast_handler(event):
       await asyncio.sleep(5)  # Doesn't block
   ```

---

## 📚 Examples

See these files for real-world examples:
- `trading_bot.py` - Event emission from SignalR callbacks
- `gui/chart_html.py` - Event subscription (ready to implement)
- `docs/EVENT_DRIVEN_ARCHITECTURE.md` - Full implementation guide

---

**Quick Reference Complete!** 🎉

For more details, see:
- `docs/EVENT_DRIVEN_ARCHITECTURE.md` - Full guide
- `docs/EVENT_DRIVEN_IMPLEMENTATION_COMPLETE.md` - Status
- `core/events.py` - Event types
- `core/event_bus.py` - EventBus implementation
