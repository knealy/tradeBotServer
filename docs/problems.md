

# 🎯 Current Focus

- consider moving charts to tradingview light weight charts or chart.js + plugin
  - use the version that is the fastest with most responsive UI

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

index-BvDXJSbH.js:11 
 POST https://tvwebhooks.up.railway.app/api/strategies/overnight_range/start 400 (Bad Request)
(anonymous)	@	index-BvDXJSbH.js:11
xhr	@	index-BvDXJSbH.js:11
Gt	@	index-BvDXJSbH.js:13
Promise.then		
_request	@	index-BvDXJSbH.js:14
request	@	index-BvDXJSbH.js:13
(anonymous)	@	index-BvDXJSbH.js:14
(anonymous)	@	index-BvDXJSbH.js:9
startStrategy	@	index-BvDXJSbH.js:14
(anonymous)	@	index-BvDXJSbH.js:89
fn	@	index-BvDXJSbH.js:9
m	@	index-BvDXJSbH.js:9
ln	@	index-BvDXJSbH.js:9
e.executeMutation	@	index-BvDXJSbH.js:9
(anonymous)	@	index-BvDXJSbH.js:9
Promise.then		
e.execute	@	index-BvDXJSbH.js:9
s.mutate	@	index-BvDXJSbH.js:9
(anonymous)	@	index-BvDXJSbH.js:9
u	@	index-BvDXJSbH.js:89
onClick	@	index-BvDXJSbH.js:89
ff	@	react-vendor-kWENTzfD.js:29
pf	@	react-vendor-kWENTzfD.js:29
hf	@	react-vendor-kWENTzfD.js:29
Co	@	react-vendor-kWENTzfD.js:29
va	@	react-vendor-kWENTzfD.js:29
(anonymous)	@	react-vendor-kWENTzfD.js:29
ji	@	react-vendor-kWENTzfD.js:32
Vs	@	react-vendor-kWENTzfD.js:29
Hl	@	react-vendor-kWENTzfD.js:29
fi	@	react-vendor-kWENTzfD.js:29
Tf	@	react-vendor-kWENTzfD.js:29

index-BvDXJSbH.js:9 
R {message: 'Request failed with status code 400', name: 'AxiosError', code: 'ERR_BAD_REQUEST', config: {…}, request: XMLHttpRequest, …}
(anonymous)	@	index-BvDXJSbH.js:9
Promise.catch		
e.execute	@	index-BvDXJSbH.js:9
s.mutate	@	index-BvDXJSbH.js:9
(anonymous)	@	index-BvDXJSbH.js:9
u	@	index-BvDXJSbH.js:89
onClick	@	index-BvDXJSbH.js:89
ff	@	react-vendor-kWENTzfD.js:29
pf	@	react-vendor-kWENTzfD.js:29
hf	@	react-vendor-kWENTzfD.js:29
Co	@	react-vendor-kWENTzfD.js:29
va	@	react-vendor-kWENTzfD.js:29
(anonymous)	@	react-vendor-kWENTzfD.js:29
ji	@	react-vendor-kWENTzfD.js:32
Vs	@	react-vendor-kWENTzfD.js:29
Hl	@	react-vendor-kWENTzfD.js:29
fi	@	react-vendor-kWENTzfD.js:29
Tf	@	react-vendor-kWENTzfD.js:29
index-BvDXJSbH.js:89 Error starting strategy: 
R {message: 'Request failed with status code 400', name: 'AxiosError', code: 'ERR_BAD_REQUEST', config: {…}, request: XMLHttpRequest, …}
onError	@	index-BvDXJSbH.js:89
(anonymous)	@	index-BvDXJSbH.js:9
Promise.then		
(anonymous)	@	index-BvDXJSbH.js:9
Promise.catch		
e.execute	@	index-BvDXJSbH.js:9
s.mutate	@	index-BvDXJSbH.js:9
(anonymous)	@	index-BvDXJSbH.js:9
u	@	index-BvDXJSbH.js:89
onClick	@	index-BvDXJSbH.js:89
ff	@	react-vendor-kWENTzfD.js:29
pf	@	react-vendor-kWENTzfD.js:29
hf	@	react-vendor-kWENTzfD.js:29
Co	@	react-vendor-kWENTzfD.js:29
va	@	react-vendor-kWENTzfD.js:29
(anonymous)	@	react-vendor-kWENTzfD.js:29
ji	@	react-vendor-kWENTzfD.js:32
Vs	@	react-vendor-kWENTzfD.js:29
Hl	@	react-vendor-kWENTzfD.js:29
fi	@	react-vendor-kWENTzfD.js:29
Tf	@	react-vendor-kWENTzfD.js:29



- still no enable/disable buttons on strategies available
- still no way to modify the open orders
- no way to modify or close open positions 


