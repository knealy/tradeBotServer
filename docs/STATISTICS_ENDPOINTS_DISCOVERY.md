# Statistics Endpoints Discovery

**Date**: 2026-01-15  
**Discovery**: Statistics endpoints exist on `userapi.topstepx.com` (different base URL!)

## Key Finding

The Statistics endpoints are on a **different API base URL**:
- **Statistics API**: `https://userapi.topstepx.com` 
- **Trading API**: `https://api.topstepx.com`

## Endpoints That Exist (HTTP 200)

All tested endpoints return HTTP 200, confirming they exist:

### 1. `/Statistics/daily`
- **URL**: `https://userapi.topstepx.com/Statistics/daily`
- **Status**: ✅ EXISTS (200 OK)
- **Response**: `[]` (empty array - may need date parameters)
- **Request**: `{"accountId":15991599}`

### 2. `/Statistics/profitFactor`
- **URL**: `https://userapi.topstepx.com/Statistics/profitFactor`
- **Status**: ✅ EXISTS (200 OK)
- **Response**: `{"totalProfit":0,"totalLoss":0}`
- **Request**: `{"accountId":15991599}`

### 3. `/Statistics/trades`
- **URL**: `https://userapi.topstepx.com/Statistics/trades`
- **Status**: ✅ EXISTS (200 OK)
- **Response**: `[]` (empty array - may need date parameters)
- **Request**: `{"accountId":15991599}`

### 4. `/Statistics/monthly`
- **URL**: `https://userapi.topstepx.com/Statistics/monthly`
- **Status**: ✅ EXISTS (200 OK)
- **Response**: `[]` (empty array - may need date parameters)
- **Request**: `{"accountId":15991599}`

## Swagger Documentation

- **Statistics API Swagger**: https://userapi.topstepx.com/swagger/index.html#/Statistics/Statistics_GetTodayStats
- **Trading API Swagger**: https://api.topstepx.com/swagger/index.html#/Position/Position_SearchOpenPositions

## Current Test Results

All endpoints return 200, but some return empty data. This suggests:

1. **They may need additional parameters** (date ranges, trading account ID in different format)
2. **The account may not have data** for the requested period
3. **They may need different field names** (e.g., `tradingAccountId` instead of `accountId`)

## Next Steps to Test

### Try with Date Ranges

```bash
# Daily stats with date range
curl -v -X POST 'https://userapi.topstepx.com/Statistics/daily' \
  -H 'Content-Type: application/json' \
  -H 'accept: text/plain' \
  -H 'Authorization: Bearer YOUR_TOKEN' \
  -d '{
    "accountId": 15991599,
    "startDate": "2026-01-01",
    "endDate": "2026-01-15"
  }'
```

### Try with tradingAccountId

```bash
curl -v -X POST 'https://userapi.topstepx.com/Statistics/daily' \
  -H 'Content-Type: application/json' \
  -H 'accept: text/plain' \
  -H 'Authorization: Bearer YOUR_TOKEN' \
  -d '{
    "tradingAccountId": 15991599
  }'
```

### Check Swagger for Required Parameters

Visit the Swagger UI to see the exact request schema:
- https://userapi.topstepx.com/swagger/index.html#/Statistics

## Summary

✅ **Statistics endpoints DO exist**  
✅ **They're on `userapi.topstepx.com`** (not `api.topstepx.com`)  
✅ **They accept requests and return 200**  
⚠️ **Some return empty data** - may need additional parameters or date ranges

## Updated API Endpoint List

| Endpoint | Base URL | Status |
|----------|----------|--------|
| `/api/Account/search` | `api.topstepx.com` | ✅ Works |
| `/api/Order/search` | `api.topstepx.com` | ✅ Works |
| `/api/Trade/search` | `api.topstepx.com` | ✅ Works |
| `/api/Contract/available` | `api.topstepx.com` | ✅ Works |
| `/api/Position/searchOpen` | `api.topstepx.com` | ✅ Works |
| `/Statistics/daily` | `userapi.topstepx.com` | ✅ Exists (needs params?) |
| `/Statistics/profitFactor` | `userapi.topstepx.com` | ✅ Exists |
| `/Statistics/trades` | `userapi.topstepx.com` | ✅ Exists (needs params?) |
| `/Statistics/monthly` | `userapi.topstepx.com` | ✅ Exists (needs params?) |
