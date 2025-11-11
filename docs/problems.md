

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
- candlestick charts look are lacking finesse and could use some updating its a bit odd 
  - the price axis is on the left instead of right side and seems 'compressed' 
  - the time axis and candles do not continue to current time and stop at end of new york session (around 5pm EST today)
  - the candles themselves do not look clean/professional and the default white/black is too stark to see easily (the black mainly its too dark)
  - users should be able to drag and zoom responsively across the chart 
    rather than use some compression bar below the chart x axis. 
  - there should be a crosshairs cursor effect while hovering to show price / time easier
  - more modular: users should be able to pick their own candle colors, pick between two specific date/times, etc
- strategies option selector not connected to routes yet
- open positions not connected to routes 
- automated trades execution not fully working 

