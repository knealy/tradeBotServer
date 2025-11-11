

# 🎯 Current Focus



#### J. Go/Rust Migration (Future)
**Problem**: Python GIL limits concurrency
  **Solution**: Migrate hot paths to Go/Rust
  **Impact**: 10-100x performance improvement for I/O-bound operations

go over options for fastest / most effecient frontend - bridge - backend structures 
- choose a final tech stack 
- make a plan to port / convert 
- (probably to Go + React + JS at the core with python for certain features) 
- is Go the best choice?


Next Priorities:
Build React/JS dashboard [in progress]
Add real-time WebSocket updates [in progress]
Implement user authentication 
Create admin panel


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


Priority 1 (NOW): ✅ PostgreSQL (DONE!)
Priority 2 (When building dashboard): ⏳ Redis for sessions
Priority 3 (When scaling): ⏳ Redis for distributed cache
Priority 4 (Optional): ⏳ Redis for HFT quotes


Current Problems:
- the open positions dont give enough info realtime (unrealized pnl, entry price, etc )
start/stop strategies buttons dont work 
- getting erroneous discord notifications not connected to actual filled trade orders 
- consider moving charts to tradingview light weight charts or chart.js + plugin
- strategies option selector not connected to routes yet


