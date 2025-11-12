

# 🎯 Current Focus



┌─────────────────────────────────────────────────────┐
│               CACHING STRATEGY                      │
├─────────────────────────────────────────────────────┤
│                                                     │
│  REDIS (In-Memory, Fast, Volatile)                 │
│  ├── Real-time quotes          <1ms, TTL=5s       │
│  ├── Recent bars (hot)         <1ms, TTL=60s      │
│  ├── Active positions          <1ms, no TTL       │
│  ├── Rate limit counters       <1ms, TTL=60s      │
│  └── Session data (dashboard)  <1ms, TTL=24h      │
│                                                     │
│  POSTGRESQL (Persistent, Durable)                  │
│  ├── Historical bars           ~5ms, permanent     │
│  ├── Account state             ~5ms, permanent     │
│  ├── Strategy metrics          ~5ms, permanent     │
│  ├── API performance logs      ~5ms, 7-day TTL    │
│  └── Trade history             ~5ms, permanent     │
│                                                     │
│  API (External, Slow)                              │
│  └── TopStepX API              ~109ms              │
│                                                     │
└─────────────────────────────────────────────────────┘



Next Priorities:
Build React/JS dashboard [in progress]
Add real-time WebSocket updates [in progress]
Implement user authentication 
Create admin panel


## ✅ Recently Completed

- Strategy enable/disable buttons now persist to PostgreSQL and reload correctly after redeploys or account switches.
- Dashboard settings (default account, risk controls, WS/API overrides) are stored via `/api/settings` and restored on load.
- Overnight Range strategy no longer backfills trades after the 09:30 ET window – runs only when the toggle is on and within the trading session.
- Parquet caching restored on Railway by bundling `polars` in the runtime image.

## ⏭️ Still Open / Upcoming

- Swap price charts to a more responsive library (TradingView LW charts or Chart.js plugin) with zoom + crosshair UX.
- Wire up authentication + admin panel once dashboard foundations are solid.
- Add Redis layer to persist sessions and speed up hot data once Railway deployment is stable.

Priority 1 (NOW): ✅ PostgreSQL (DONE!)
Priority 2 (NEXT): ⏳ Redis for sessions
Priority 3 (When scaling): ⏳ Redis for distributed cache
Priority 4 (Optional): ⏳ Redis for HFT quotes
priority 5 add a chat bar / side panel

#### J. Go/Rust Migration (Future)
**Problem**: Python GIL limits concurrency
  **Solution**: Migrate hot paths to Go/Rust
  **Impact**: 10-100x performance improvement for I/O-bound operations


## 🧭 Dashboard Functionality Parity

- [x] **Orders**: full ticket (market/limit/stop/bracket), bulk cancel, flatten account
- [x] **Positions**: partial close, TP/SL edits, detailed drawer
- [x] **Risk View**: DLL/MLL gauges, collapsible sidebar widget, violation alerts
- [x] **Notifications Feed**: show order/position events (Discord parity)
- [x] **Automation Tools**: trailing stop, breakeven toggle, overnight breakout test
- [x] **Strategy Insights**: per-strategy stats, logs, test trigger buttons
- [ ] **Data Exports & Charts**: CSV download, upgraded candlestick/TV charts
