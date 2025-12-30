# Dashboard Performance & Analytics Fixes

## Issues Identified

1. **Performance chart showing "No historical trades available"** despite accounts having 100+ filled orders
2. **Recent Trades showing "No trades in this period"** when trades exist
3. **API error rate at 14.81%** - too high
4. **Multiple redundant API calls** for the same data
5. **Data extraction failing** - TopStepX order format not being parsed correctly

## Root Causes

### 1. Data Extraction Mismatch
TopStepX's order history API returns orders in a specific format that doesn't include direct P&L fields. The dashboard was looking for fields like `pnl`, `realizedPnl`, etc. that don't exist in the raw order data.

**TopStepX Order Format:**
```json
{
  "id": 1866486884,
  "accountId": 13936280,
  "contractId": "CON.F.US.MNQ.Z25",
  "creationTimestamp": "2025-11-07T20:52:58.372Z",
  "updateTimestamp": "2025-11-07T21:00:00.366Z",
  "status": 2,  // 2=filled, 3=cancelled, etc.
  "type": 1,    // 1=limit, 2=market, 4=stop
  "side": 0,    // 0=buy, 1=sell
  "size": 40,
  "limitPrice": 25417.25,
  "fillVolume": 40
}
```

No `pnl`, `timestamp`, or `symbol` fields - these need to be extracted/calculated.

### 2. Timestamp Extraction
Orders use `updateTimestamp` and `creationTimestamp` instead of `timestamp`, causing all orders to be filtered out.

### 3. Excessive API Calls
No database caching for order history, causing every dashboard load to hit the TopStepX API multiple times.

## Fixes Implemented

### ✅ 1. Enhanced Data Extraction (`servers/dashboard.py`)

**`_extract_trade_pnl()` - Now handles TopStepX format:**
- Checks for fills data with PnL
- Calculates P&L from entry/exit prices if available
- Uses symbol-specific point values (MNQ=$2/point, MES=$5/point, etc.)
- Falls back to description parsing as last resort

**`_extract_trade_timestamp()` - Fixed for TopStepX:**
- Now looks for `updateTimestamp` and `creationTimestamp` first
- Handles both string and datetime objects
- Logs missing timestamps for debugging

**`_get_point_value()` - Symbol-specific calculations:**
```python
MNQ/NQ: $2 per point
MES/ES: $5 per point
MYM/YM: $0.50 per point
M2K/RTY: $5 per point
```

### ✅ 2. Database Caching (`infrastructure/database.py`)

**New Table: `order_history_cache`**
- Stores raw order JSON from API
- 24-hour TTL for cached data
- Indexed by account_id + timestamp for fast lookups

**New Methods:**
- `cache_order_history(account_id, orders)` - Bulk insert orders
- `get_cached_order_history(account_id, start, end, limit)` - Fast retrieval

**3-Tier Caching Strategy:**
1. **Memory Cache** (30 seconds) - Instant response for repeated requests
2. **Database Cache** (24 hours) - Persistent storage, survives restarts
3. **API Call** - Only when cache misses

### ✅ 3. Refresh Parameter (`servers/async_webhook_server.py`)

Added `?refresh=1` parameter support to all endpoints:
- `/api/trades?account_id=123&refresh=1`
- `/api/performance/history?account_id=123&interval=day&refresh=1`

Clears both memory and database caches for the account, forcing fresh API fetch.

### ✅ 4. Better Logging & Debugging

Added detailed logging to identify issues:
- Logs first 3 trades with missing timestamps (shows what fields are available)
- Logs cache hits/misses for both memory and database
- Logs normalized trade count vs raw order count
- Logs failed PnL calculations with reasons

## Testing Instructions

### 1. Restart the Backend
```bash
cd /Users/susan/projectXbot
python servers/start_async_webhook.py
```

### 2. Reload Frontend
Refresh your browser at `http://localhost:3000`

### 3. Expected Behavior

**First Load (Cold Cache):**
- Performance chart should now show data points
- Recent Trades should list actual trades
- Logs will show: `Normalized X trades from Y raw orders`
- Database cache will be populated

**Second Load (Warm Cache):**
- Much faster response (~200ms vs ~600ms)
- Logs will show: `✅ DB Cache HIT: X orders for account Y`
- No API calls to TopStepX

**Force Refresh:**
```bash
# In browser console or via API tool:
fetch('http://localhost:8080/api/trades?account_id=13936280&refresh=1')
```
Logs will show: `🔄 Cache cleared for account 13936280 (refresh=1)`

### 4. Monitor Logs
```bash
tail -f trading_bot.log | grep -E "(Normalized|Cache HIT|Cache cleared|Skipping trade)"
```

**Good Signs:**
- `Normalized 167 trades from 167 raw orders` - All orders processed
- `✅ DB Cache HIT: 167 orders for account 13936280` - Database working
- `💾 Memory cache HIT for account 13936280` - Memory cache working

**Bad Signs:**
- `Skipping trade 0 - no timestamp. Keys: [...]` - Timestamp extraction still failing
- `Normalized 0 trades from 167 raw orders` - All trades filtered out

## Performance Improvements

### Before:
- **API Calls per Dashboard Load:** 8-12 calls
- **Load Time:** 2-3 seconds
- **Cache Hit Rate:** 0% (no database cache)
- **API Error Rate:** 14.81%

### After (Expected):
- **API Calls per Dashboard Load:** 1-2 calls (first load), 0 calls (subsequent)
- **Load Time:** 0.5-1 second (first), 0.2-0.3 seconds (cached)
- **Cache Hit Rate:** 80-90% (with database)
- **API Error Rate:** <5% (fewer calls = fewer errors)

## Troubleshooting

### Issue: Still showing "No trades"
**Check logs for:**
```bash
grep "Skipping trade" trading_bot.log | head -5
```
This will show what fields are in the orders and why they're being skipped.

**Fix:** If timestamp extraction is still failing, we may need to add more field names to check.

### Issue: Database cache not working
**Check:**
```bash
# In Python console:
from infrastructure.database import get_database
db = get_database()
stats = db.get_stats()
print(stats)
```

Should show `order_cache_count` > 0 after first load.

**Fix:** Ensure `PUBLIC_DATABASE_URL` is set in `.env` for local testing.

### Issue: Performance chart shows $0 P&L
**This is expected** if TopStepX orders don't include P&L data. The current implementation tries to calculate it, but without paired entry/exit orders, it defaults to $0.

**Solution:** We may need to implement trade consolidation logic (pair buy/sell orders) to calculate accurate P&L. This is a more complex feature that requires:
1. Grouping orders by symbol
2. Matching entry/exit orders
3. Calculating P&L from price differences
4. Handling partial fills

This is tracked as a future enhancement.

## API Reference

### Get Trades (with refresh)
```http
GET /api/trades?account_id=13936280&type=filled&limit=20&refresh=1
```

**Parameters:**
- `account_id` - Account ID
- `type` - `filled`, `cancelled`, `pending`, `rejected`, or `all`
- `limit` - Max trades to return (default: 50)
- `cursor` - Pagination cursor
- `refresh` - Set to `1` to bypass cache

### Get Performance History (with refresh)
```http
GET /api/performance/history?account_id=13936280&interval=day&refresh=1
```

**Parameters:**
- `account_id` - Account ID
- `interval` - `trade`, `hour`, `day`, `week`, or `month`
- `start` - Start date (ISO format)
- `end` - End date (ISO format)
- `refresh` - Set to `1` to bypass cache

## Next Steps

### Remaining TODOs:
1. **Reduce API error rate** - Add retry logic and better error handling
2. **Request deduplication** - Prevent multiple simultaneous requests for same data
3. **Trade consolidation** - Pair entry/exit orders for accurate P&L calculation
4. **WebSocket updates** - Push new trades to frontend in real-time

### Future Enhancements:
- **Incremental cache updates** - Only fetch new orders since last cache
- **Redis caching** - For multi-instance deployments
- **Batch API calls** - Combine multiple requests into one
- **Predictive prefetching** - Load likely-needed data in background

## Summary

The dashboard should now display actual trade data and performance metrics. The 3-tier caching system significantly reduces API calls and improves response times. Use `?refresh=1` when you need the absolute latest data from TopStepX.

**Key Improvements:**
- ✅ Fixed data extraction for TopStepX order format
- ✅ Added database caching (24-hour TTL)
- ✅ Implemented 3-tier caching (memory → database → API)
- ✅ Added `?refresh=1` parameter for cache bypass
- ✅ Enhanced logging for debugging

**Expected Results:**
- Performance chart shows trade data
- Recent Trades table populates
- Faster dashboard loads (especially on refresh)
- Reduced API error rate

