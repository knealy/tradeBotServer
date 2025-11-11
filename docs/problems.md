

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
Build React/JS dashboard
Add real-time WebSocket updates
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




'Recent Trades' data seems right but the calculations that translate to the Performance chart data are way off 

for example all of these traids and individual P&L seem correct but 
gross PnL is very high comparitively at +19,020.50

Recent Trades
Gross PnL: +$19,020.50
Time	Symbol	Side	Qty	Price	Net P&L
Nov 09, 2025, 08:01 PM	MNQ	LONG	5	$25,341.75	+$290.00
Nov 09, 2025, 08:00 PM	MNQ	LONG	5	$25,341.75	$20.00
Nov 09, 2025, 07:58 PM	MNQ	LONG	5	$25,321.00	+$162.50
Nov 09, 2025, 07:57 PM	MNQ	LONG	5	$25,321.25	+$217.50
Nov 09, 2025, 07:48 PM	MNQ	LONG	10	$25,318.50	+$20.00
Nov 06, 2025, 07:41 PM	MNQ	LONG	1	$25,321.75	+$46.00
Nov 06, 2025, 07:41 PM	MNQ	LONG	1	$25,321.50	+$46.00
Nov 06, 2025, 07:41 PM	MNQ	LONG	1	$25,325.50	+$38.00
Nov 06, 2025, 07:41 PM	MNQ	LONG	1	$25,325.75	+$38.00
Nov 06, 2025, 07:41 PM	MNQ	LONG	1	$25,325.50	+$37.50
Nov 06, 2025, 07:41 PM	MNQ	LONG	1	$25,325.50	+$40.00
Nov 06, 2025, 07:21 PM	MNQ	SHORT	1	$25,319.25	+$8.00
Nov 06, 2025, 07:21 PM	MNQ	SHORT	1	$25,319.25	+$8.00
Nov 06, 2025, 07:21 PM	MNQ	SHORT	1	$25,319.00	+$7.50
Nov 06, 2025, 07:21 PM	MNQ	SHORT	1	$25,319.00	+$7.50
Nov 06, 2025, 07:21 PM	MNQ	SHORT	1	$25,319.00	+$7.50
Nov 06, 2025, 07:09 PM	MNQ	SHORT	1	$25,322.50	$22.50
Nov 06, 2025, 07:09 PM	MNQ	SHORT	1	$25,322.50	$21.00
Nov 06, 2025, 07:09 PM	MNQ	SHORT	1	$25,322.50	$20.50
Nov 06, 2025, 07:09 PM	MNQ	LONG	1	$25,332.25	$19.50


the day by day balances are off too: 
these are the actual day to day pnLs below 

Su	Mo	Tu	We	Th	Fr	Sa
26
27
28
29
30
$5,320.80
38 trades

31
-$1,818.88
56 trades

1
Week 1

$3,501.92
94 trades

2
3
$489.12
18 trades

4
$616.10
4 trades

5
$1,821.32
45 trades

6
-$501.44
29 trades

7
$213.54
49 trades

8
Week 2

$2,638.64
145 trades

9
10
$647.80
5 trades

11
12
13
14
15
Week 3

$647.80