# 🧠 Data Flow & API Spec: Performance Analytics & Market Data

**Status**: Draft for implementation  
**Last Updated**: November 9, 2025  

This document covers the data contracts and flow needed to power:
1. The **Performance History chart** on the dashboard.  
2. The **Trades log** (with filters + pagination).  
3. The **TradingView-style OHLCV chart** (historical price data).  

It is the blueprint for both backend endpoints and frontend React Query hooks.

---

## 1. Performance History (`/api/performance/history`)

### Purpose
Provide cumulative and per-period P&L for the selected account so the dashboard chart can show performance over the full account history with granularity (daily, weekly, intraday).

### Data Sources
- `trading_bot.get_order_history()` → detailed fills with PnL.  
- PostgreSQL `strategy_performance` / `trade_history` tables (if available).  
- `account_tracker` snapshots for balance/equity timeseries (fallback).

### Request
```http
GET /api/performance/history?account_id=<id>&interval=daily&start=2025-01-01&end=2025-11-09
```

| Query Param | Type | Default | Notes |
| --- | --- | --- | --- |
| `account_id` | string | current selected account | optional; if omitted, use bot.selected_account |
| `interval` | enum (`tick`, `trade`, `hour`, `day`, `week`, `month`) | `day` | Controls aggregation granularity |
| `start` | ISO datetime | `account inception` | Lower bound |
| `end` | ISO datetime | `now` | Upper bound |

### Response
```json
{
  "account_id": "12694476",
  "currency": "USD",
  "interval": "day",
  "points": [
    {
      "timestamp": "2025-10-01T00:00:00Z",
      "period_pnl": 125.50,
      "cumulative_pnl": 125.50,
      "balance": 50125.50,
      "trade_count": 4,
      "winning_trades": 3,
      "losing_trades": 1,
      "max_drawdown": -45.00
    },
    ...
  ],
  "summary": {
    "start_balance": 50000.00,
    "end_balance": 50842.13,
    "total_pnl": 842.13,
    "win_rate": 57.1,
    "avg_win": 88.12,
    "avg_loss": -65.44,
    "max_drawdown": -240.50
  }
}
```

### Backend Implementation Notes
- Create a helper `DashboardAPI.get_performance_history(interval, start, end)`:
  - Fetch trades via `get_order_history` (with pagination if needed).  
  - Ensure PnL sign is correct (long vs short).  
  - Aggregate by interval using pandas or manual binning.  
  - Use database cache when available; fallback to API and persist.  
  - Compute cumulative sums + drawdown.
- Cache results per account + interval in PostgreSQL (`strategy_performance` table) for quick retrieval.
- Expose endpoint in `async_webhook_server.py` and broadcast updates via WebSocket if new trades insert.

### Frontend Consumption
- `usePerformanceHistory(interval)` returns `{points, summary}`.  
- Chart:  
  - X-axis = `timestamp`  
  - Primary Y = `cumulative_pnl`  
  - Tooltip also shows `period_pnl`, `trade_count`, `max_drawdown`  
- Provide interval pickers (Day / Week / Month / Trade).

---

## 2. Trade History (`/api/trades`)

### Purpose
Drive the Trades table with date filters, pagination, CSV export, and tie into performance calculations.

### Request
```http
GET /api/trades?account_id=<id>&start=2025-11-01&end=2025-11-09&type=filled&limit=50&cursor=abc123
```

| Param | Type | Default | Notes |
| --- | --- | --- | --- |
| `account_id` | string | current account | optional |
| `start` / `end` | ISO datetime | `last 7 days` | Filter window |
| `type` | enum (`all`,`filled`,`cancelled`,`pending`) | `filled` | Filter by status |
| `symbol` | string | all | Filter by instrument |
| `limit` | int (1-500) | 50 | Page size |
| `cursor` | string | null | Pagination cursor (encoded timestamp+id) |

### Response
```json
{
  "account_id": "12694476",
  "start": "2025-11-01T00:00:00Z",
  "end": "2025-11-09T23:59:59Z",
  "items": [
    {
      "id": "trade-001",
      "order_id": "order-xyz",
      "symbol": "MNQ",
      "side": "BUY",
      "quantity": 2,
      "price": 15234.25,
      "pnl": 87.50,
      "fees": 4.50,
      "status": "FILLED",
      "strategy": "overnight_range",
      "timestamp": "2025-11-07T14:32:55Z"
    }
  ],
  "next_cursor": "eyJ0IjoiMjAyNS0xMS0wN1QxNDozMjo1NVoiLCJpZCI6InRyYWRlLTAwMSJ9",
  "summary": {
    "total": 87,
    "filled": 65,
    "cancelled": 12,
    "avg_fill_time_ms": 1240,
    "gross_pnl": 912.44,
    "net_pnl": 887.94,
    "fees": 24.50
  }
}
```

### Backend Implementation Notes
- Extend existing `/api/trades` handler to accept filters + pagination.
- Use `trading_bot.get_order_history()` with `limit`, `start_timestamp`, `end_timestamp`.
- For pagination, encode the last trade’s timestamp+id as cursor.  
- Include additional fields (fees, strategy, fill time) when available.
- Persist trade history to PostgreSQL (already part of `Database.save_trade_history`). Use as primary source when populated; otherwise fetch from API and backfill.

### Frontend Consumption
- Build data grid with virtualized rows, filters, CSV export.  
- Use cursor-based pagination (React Query’s `fetchNextPage`).  
- Show summary metrics at top of table.

---

## 3. Historical Price Data (`/api/history`)

### Purpose
Provide granular OHLCV data for TradingView/lightweight charts and analytics overlays.

### Request
```http
GET /api/history?symbol=MNQ&timeframe=5m&limit=500&end=2025-11-08T21:00:00Z&live=false
```

| Param | Type | Default | Notes |
| --- | --- | --- | --- |
| `symbol` | string | required | Instrument symbol |
| `timeframe` | enum (`1s`,`5s`,`1m`,`5m`,`15m`,`1h`,`1d`) | `5m` | Maps to TopStepX `unit`/`unitNumber` |
| `limit` | int (<=1500) | 300 | Number of bars |
| `end` | ISO datetime | last close | End timestamp |
| `live` | bool | false | Pull live session bars (enables partial bar) |
| `adjust` | bool | true | Whether to adjust to exchange hours |

### Response
```json
{
  "symbol": "MNQ",
  "timeframe": "5m",
  "count": 300,
  "bars": [
    {
      "timestamp": "2025-11-07T21:30:00Z",
      "open": 15234.25,
      "high": 15236.50,
      "low": 15232.75,
      "close": 15235.75,
      "volume": 487
    }
  ],
  "source": "postgres_cache",      // or "topstepx_api" / "memory_cache"
  "cache_status": {
    "hit": true,
    "tier": "postgres",
    "latency_ms": 12.4
  }
}
```

### Backend Implementation Notes
- Wrap `trading_bot.get_historical_data()` in API handler:
  - Use existing 3-tier cache (memory → PostgreSQL → TopStepX API).  
  - Store bars in `historical_bars` table with `symbol`, `timeframe`, `start_time`, `end_time`, `bars_json`.  
  - For `live=true`, ensure SignalR connection is alive; fallback to API.
- Rate limit the endpoint (TopStepX limit ~60/min) using existing `RateLimiter`.
- Add input validation: `limit` <= 1500, timeframe supported.
- Extend WebSocket to broadcast updates when new bar cached (optional later).

### Frontend Consumption
- Create `useHistoricalBars(symbol, timeframe, end)` hook.  
- Feed data into `lightweight-charts` or TradingView widget component.  
- Support date range selector (1D/1W/1M/3M/All).  
- Allow overlays:  
  - Strategy entries/exits (from trade history).  
  - DLL/MLL thresholds (from risk API when ready).

---

## 4. Supporting Concepts

### Authentication & Session
- All endpoints require existing dashboard session (reuse the token top-level).  
- Ensure the async webhook server shares the same session middleware used by `/api/account/switch`.

### Error Handling
- Return structured errors:
```json
{
  "error": {
    "code": "CACHE_MISS",
    "message": "Historical data unavailable for MNQ 5m; try a smaller range.",
    "retryable": true
  }
}
```
- Log all errors via `logger.error` and track counts in `MetricsTracker`.

### WebSocket Enhancements
- When performance/trade data changes, broadcast incremental updates:
```json
{ "type": "performance_update", "data": { ... }, "timestamp": ... }
```
- Add connection health message on route navigation to avoid reconnect loops.

---

## Implementation Order

1. **Backend**  
   - Add new handlers in `DashboardAPI` (`get_performance_history`, `get_trades_paginated`, `get_historical_bars`).  
   - Wire routes in `async_webhook_server.py`.  
   - Update `Database` helper for caching & pagination.  
   - Extend `MetricsTracker` to log endpoint latencies.

2. **Frontend**  
   - Create React Query hooks for each endpoint.  
   - Build charts/tables (Performance, Trades, Historical price).  
   - Overlay PnL & trades on price chart.  

3. **Testing**  
   - Add unit tests for aggregation logic.  
   - Add integration tests hitting API endpoints (happy path + edge cases).  
   - Update `docs/FRONTEND_BACKEND_INTEGRATION.md` and `FEATURE_MATRIX.md` status once delivered.

---

This spec should be kept in sync as we implement. Any changes to data shape or parameters must be reflected here and in the frontend hooks.

---

