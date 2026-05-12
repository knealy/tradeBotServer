# Event-driven architecture

High-level flow matches the sequence in **[HANDOFF.md](HANDOFF.md)** (SignalR → `websocket_manager` → `bar_aggregator` → `event_bus` → strategy → orders → `user_hub_manager`).

## Code anchors

- **`core/event_bus.py`** — in-process pub/sub; strategies and services subscribe here.
- **`core/events.py`** — canonical event types (`BarClosedEvent`, fill/order events, etc.).
- **`core/user_hub_manager.py`** — account hub: order and position updates from SignalR, re-published as events where wired.

Do **not** import from the removed `events/` package; use `core.event_bus` and `core.events` only.

For deeper historical notes, see **[archive/FRAMEWORK_AND_ARCHITECTURE.md](archive/FRAMEWORK_AND_ARCHITECTURE.md)**.
