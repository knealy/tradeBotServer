# Statistics Endpoints Investigation

**Date**: 2026-01-15  
**Status**: Endpoints exist but return empty data

## Discovery

✅ **All Statistics endpoints exist** on `userapi.topstepx.com`  
✅ **All return HTTP 200** (endpoints are valid)  
⚠️ **All return empty arrays** `[]` or minimal data

## Tested Endpoints

### 1. `/Statistics/daily`
- **Status**: ✅ 200 OK
- **Response**: `[]`
- **Tried**: 
  - `{"accountId": 15991599}`
  - `{"accountId": 15991599, "startDate": "2026-01-01", "endDate": "2026-01-15"}`
  - `{"tradingAccountId": 15991599}`

### 2. `/Statistics/profitFactor`
- **Status**: ✅ 200 OK
- **Response**: `{"totalProfit":0,"totalLoss":0}`
- **Tried**: `{"accountId": 15991599}`

### 3. `/Statistics/trades`
- **Status**: ✅ 200 OK
- **Response**: `[]`
- **Tried**: `{"accountId": 15991599}`

### 4. `/Statistics/monthly`
- **Status**: ✅ 200 OK
- **Response**: `[]`
- **Tried**: `{"accountId": 15991599}`

## Possible Reasons for Empty Data

1. **Account has no trades in requested period**
   - Account 15991599 might not have trades in the date range
   - Try with an account that has recent trades

2. **Wrong parameter format**
   - Date format might need to be different (ISO 8601, Unix timestamp, etc.)
   - Field names might be different (check Swagger)

3. **Account ID format**
   - Might need string instead of number
   - Might need different ID (trading account ID vs user account ID)

4. **Date range required**
   - Some endpoints might require date ranges even if not documented
   - Try with recent dates (today, this week)

5. **Different base path**
   - Might need `/api/Statistics/...` instead of `/Statistics/...`

## Next Steps to Test

### 1. Try with Account That Has Trades

From your Account/search response, try with an account that has `canTrade: true`:
- Account ID: `12694476` (PRAC-V2-14334-56363256) - `canTrade: true`
- Account ID: `16611204` (150KTC-V2-14334-81127307) - `canTrade: true`

```bash
curl -v -X POST 'https://userapi.topstepx.com/Statistics/daily' \
  -H 'Content-Type: application/json' \
  -H 'accept: text/plain' \
  -H 'Authorization: Bearer YOUR_TOKEN' \
  -d '{"accountId":12694476}'
```

### 2. Try with Today's Date

```bash
curl -v -X POST 'https://userapi.topstepx.com/Statistics/daily' \
  -H 'Content-Type: application/json' \
  -H 'accept: text/plain' \
  -H 'Authorization: Bearer YOUR_TOKEN' \
  -d '{
    "accountId": 15991599,
    "date": "2026-01-15"
  }'
```

### 3. Try with ISO 8601 DateTime

```bash
curl -v -X POST 'https://userapi.topstepx.com/Statistics/daily' \
  -H 'Content-Type: application/json' \
  -H 'accept: text/plain' \
  -H 'Authorization: Bearer YOUR_TOKEN' \
  -d '{
    "accountId": 15991599,
    "startDate": "2026-01-15T00:00:00Z",
    "endDate": "2026-01-15T23:59:59Z"
  }'
```

### 4. Try with /api/ prefix

```bash
curl -v -X POST 'https://userapi.topstepx.com/api/Statistics/daily' \
  -H 'Content-Type: application/json' \
  -H 'accept: text/plain' \
  -H 'Authorization: Bearer YOUR_TOKEN' \
  -d '{"accountId":15991599}'
```

### 5. Check Swagger for Exact Schema

Visit: https://userapi.topstepx.com/swagger/index.html#/Statistics

Look for:
- Request body schema
- Required vs optional parameters
- Parameter types (string, number, date format)

## Current Manual Calculations

If we can get these endpoints working, we could replace:

### 1. Daily Statistics
**Current**: `trading_bot.py:3898-3961` - `get_today_stats()`
- Calculates: total trades, wins, losses, PnL for today
- **Could replace with**: `/Statistics/daily` if it returns this data

### 2. Trade Statistics
**Current**: `trading_bot.py:3963-4039` - `get_trade_statistics()`
- Calculates: win rate, average win/loss, total PnL
- **Could replace with**: `/Statistics/trades` if it returns this data

### 3. Profit Factor
**Current**: `trading_bot.py:4104-4177` - `get_profit_factor()`
- Calculates: total profit / total loss
- **Could replace with**: `/Statistics/profitFactor` (already returns `{"totalProfit":0,"totalLoss":0}`)

### 4. Strategy Statistics
**Current**: `servers/dashboard.py:1085-1151` - `get_strategy_stats()`
- Calculates: win rate, profit factor, avg win/loss per strategy
- **Could replace with**: Statistics endpoints if they support strategy filtering

## Potential Code Simplification

If Statistics endpoints work, we could:

1. **Replace manual calculations** with API calls
2. **Reduce code complexity** - less calculation logic
3. **Improve accuracy** - API values are authoritative
4. **Better performance** - Pre-calculated statistics vs iterating trades
5. **Consistency** - Same values as TopStepX dashboard

## Recommendation

1. **Continue testing** with different parameters and accounts
2. **Check Swagger docs** for exact request schema
3. **Try accounts with trades** (12694476, 16611204)
4. **If endpoints work**: Update code to use them
5. **If endpoints don't work**: Keep manual calculations (current approach is fine)
