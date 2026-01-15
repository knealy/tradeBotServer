# Statistics Endpoints Summary & Action Plan

**Date**: 2026-01-15  
**Status**: Endpoints exist but return empty data - needs investigation

## ✅ Discovery

1. **Statistics endpoints DO exist** on `userapi.topstepx.com`
2. **All return HTTP 200** (endpoints are valid and accessible)
3. **All return empty arrays** `[]` or minimal data like `{"totalProfit":0,"totalLoss":0}`

## 📋 Endpoints Found

| Endpoint | Status | Response | Notes |
|----------|--------|----------|-------|
| `/Statistics/daily` | ✅ 200 | `[]` | Empty - may need date params |
| `/Statistics/profitFactor` | ✅ 200 | `{"totalProfit":0,"totalLoss":0}` | Works but no data |
| `/Statistics/trades` | ✅ 200 | `[]` | Empty - may need date params |
| `/Statistics/monthly` | ✅ 200 | `[]` | Empty - may need date params |

## 🔍 Why Empty Data?

Possible reasons:
1. **Account has no trades** in the requested period
2. **Wrong parameters** - date format, field names, etc.
3. **Wrong account ID** - might need trading account ID vs user account ID
4. **Date range required** - endpoints might require explicit dates
5. **Different base path** - might need `/api/Statistics/...` instead

## 🧪 Next Steps to Test

### 1. Run Comprehensive Test Script

```bash
./scripts/test_statistics_endpoints.sh YOUR_TOKEN
```

This will test:
- Different parameter formats
- Different accounts (including ones with `canTrade: true`)
- Different date formats

### 2. Check Swagger Documentation

Visit: https://userapi.topstepx.com/swagger/index.html#/Statistics

Look for:
- **Request body schema** - exact field names and types
- **Required vs optional** parameters
- **Date format** requirements (ISO 8601, YYYY-MM-DD, Unix timestamp?)
- **Example requests** in Swagger UI

### 3. Try Accounts with Trades

From your Account/search, try accounts that have `canTrade: true`:
- `12694476` - PRAC-V2-14334-56363256
- `16611204` - 150KTC-V2-14334-81127307

These might have more data.

### 4. Try Different Date Formats

```bash
# ISO 8601 with time
{"accountId": 15991599, "startDate": "2026-01-15T00:00:00Z", "endDate": "2026-01-15T23:59:59Z"}

# Date only
{"accountId": 15991599, "date": "2026-01-15"}

# Unix timestamp
{"accountId": 15991599, "startTimestamp": 1705276800, "endTimestamp": 1705363199}
```

## 💡 Potential Code Simplification

If Statistics endpoints work, we could replace:

### Current Manual Calculations

1. **`trading_bot.py:3790-3870`** - `_calculate_trade_statistics()`
   - Calculates: win rate, avg win/loss, total PnL, largest win/loss
   - **Could replace with**: `/Statistics/trades` if it returns this data

2. **`trading_bot.py:3898-3961`** - `get_today_stats()`
   - Tries to use Statistics API but falls back to manual
   - **Could replace with**: `/Statistics/daily` if it works

3. **`trading_bot.py:3963-4039`** - `get_trade_statistics()`
   - Tries to use Statistics API but falls back to manual
   - **Could replace with**: `/Statistics/daily` or `/Statistics/trades` if they work

4. **`trading_bot.py:4104-4177`** - `get_profit_factor()`
   - Tries to use Statistics API but falls back to manual
   - **Could replace with**: `/Statistics/profitFactor` (already returns data structure!)

5. **`servers/dashboard.py:1085-1151`** - `get_strategy_stats()`
   - Calculates strategy-specific statistics
   - **Could replace with**: Statistics endpoints if they support strategy filtering

### Benefits if Endpoints Work

1. **Less code** - Remove manual calculation logic
2. **Better accuracy** - API values match TopStepX dashboard
3. **Better performance** - Pre-calculated vs iterating trades
4. **Consistency** - Same values across all clients
5. **Maintainability** - Less code to maintain

## 📝 Current Status

### What Works
- ✅ Endpoints exist and are accessible
- ✅ Authentication works
- ✅ `/Statistics/profitFactor` returns data structure (just no data)

### What Doesn't Work
- ❌ Endpoints return empty arrays
- ❌ Unknown if it's parameter issue or no data issue

### What to Do
1. **Continue testing** with different parameters
2. **Check Swagger** for exact schema
3. **Try accounts with trades** (12694476, 16611204)
4. **If endpoints work**: Update code to use them
5. **If endpoints don't work**: Keep manual calculations (current approach is fine)

## 🎯 Recommendation

**Priority**: Medium

1. **Short term**: Keep using manual calculations (they work fine)
2. **Investigate**: Test Statistics endpoints with different parameters
3. **If successful**: Gradually migrate to Statistics endpoints
4. **If not successful**: Document that endpoints exist but don't return useful data

The current manual calculation approach using `/api/Trade/search` is:
- ✅ Accurate (uses API-provided PnL)
- ✅ Reliable (doesn't depend on Statistics endpoints)
- ✅ Flexible (can filter by date, strategy, etc.)

So even if Statistics endpoints don't work, we're in a good place!
