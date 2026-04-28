# Architecture

High-level layout of the TopStepX bot. For the tick-by-tick narrative, see [HANDOFF.md](HANDOFF.md).

## Runtime picture

- **Strategy process:** `core/strategy_executor.py` loads one strategy + account, constructs `TopStepXTradingBot` (`trading_bot.py`), connects SignalR, runs the event loop.
- **Broker:** `brokers/topstepx_adapter.py` — REST + SignalR session management, order placement, historical data (optional Parquet disk tier).
- **Market path:** `core/websocket_manager.py` → `core/bar_aggregator.py` → `core/event_bus.py` → strategy `evaluate()`.
- **Account path:** `core/user_hub_manager.py` — orders, fills, positions; feeds the same `EventBus` where needed.
- **Persistence:** `infrastructure/database.py` — Postgres pool, optional async batch writers for telemetry tables.

## Further reading

- [FRAMEWORK_AND_ARCHITECTURE.md](FRAMEWORK_AND_ARCHITECTURE.md) — older but detailed framework notes.
- [EVENT_DRIVEN_ARCHITECTURE.md](EVENT_DRIVEN_ARCHITECTURE.md) — event-driven view.
- [MAP.md](MAP.md) — generated file ↔ responsibility mapping.
- [DECISIONS.md](DECISIONS.md) — why key choices were made.
