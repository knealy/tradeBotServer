# 1-Minute Aggregation Strategy

## Overview

The trading bot now uses a **1-minute aggregation strategy** for all timeframes greater than 1 minute. This ensures accurate, up-to-date chart data by using reliable 1-minute data as the source of truth.

## How It Works

### For Timeframes > 1m (5m, 15m, 30m, 1h, etc.)

1. **Fetch 1m Data**: Instead of requesting the target timeframe directly from the API, the system fetches 1-minute bars
2. **Aggregate**: Groups consecutive 1-minute bars into the target timeframe
3. **Calculate OHLC**: 
   - **Open**: First 1m bar's open price
   - **High**: Maximum high across all 1m bars
   - **Low**: Minimum low across all 1m bars
   - **Close**: Last 1m bar's close price
   - **Volume**: Sum of all 1m bar volumes

### For Timeframes = 1m

- Uses API directly (no aggregation needed)

### For Timeframes < 1m (30s, 15s, 5s)

- Uses API directly (sub-minute data requires API)

## Implementation Details

### Core Logic (`trading_bot.py`)

The `get_historical_data()` method automatically:
1. Detects if timeframe > 1m
2. Sets `use_aggregation = True` and `source_timeframe = "1m"`
3. Bypasses stale cache for target timeframe
4. Fetches fresh 1m data (or uses cached 1m if fresh)
5. Aggregates 1m bars to target timeframe
6. Returns aggregated bars

### Cache Strategy

- **Target Timeframe Cache**: Bypassed when using aggregation (to avoid stale data)
- **1m Source Cache**: Checked first, used if fresh (within 1 hour)
- **Fresh Data**: Always fetched if cache is stale or missing

### Bar Aggregator Integration

When requesting >1m timeframes:
- Registers both the target timeframe AND 1m timeframe
- Ensures real-time 1m bars are built for aggregation
- Frontend receives aggregated bars via WebSocket updates

## API Endpoints

All historical data endpoints automatically use aggregation:

- `GET /api/history` (via `async_webhook_server.py`)
- `GET /api/history` (via `dashboard_api_server.py`)
- `DashboardAPI.get_historical_data()` (via `dashboard.py`)

All endpoints call `trading_bot.get_historical_data()`, which handles aggregation automatically.

## Frontend Integration

No frontend changes required! The frontend continues to:
- Request data via `analyticsApi.getHistoricalData()`
- Receive aggregated bars in the same format
- Display charts with accurate, up-to-date data

## Benefits

1. **Accuracy**: Uses reliable 1m data as source
2. **Freshness**: Always fetches up-to-date data (bypasses stale cache)
3. **Consistency**: All timeframes derived from same source
4. **Performance**: 1m data cached and reused for multiple timeframes
5. **Reliability**: Avoids API issues with higher timeframes

## Example

Requesting `history mnq 5m 20`:

1. System detects 5m > 1m → uses aggregation
2. Bypasses stale 5m cache
3. Fetches 200 1m bars (5m * 20 bars * 2 buffer)
4. Aggregates every 5 consecutive 1m bars into 5m bars
5. Returns 20 accurate 5m bars ending at current time

## Log Messages

When using aggregation, you'll see:
```
📊 Using 1m aggregation strategy: will fetch 1m data and aggregate to 5m
📊 Aggregating 200 1m bars into 5m bars...
✅ Aggregated to 20 5m bars
```

## Date: December 2, 2025

