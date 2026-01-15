# Statistics Endpoints Testing Guide

**Date**: 2026-01-15  
**Base URL**: `https://userapi.topstepx.com` (NOT `api.topstepx.com`)

## Verified Working Endpoints

All these return HTTP 200:

### 1. Statistics/daily

```bash
curl -v -X POST 'https://userapi.topstepx.com/Statistics/daily' \
  -H 'Content-Type: application/json' \
  -H 'accept: text/plain' \
  -H 'Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJodHRwOi8vc2NoZW1hcy54bWxzb2FwLm9yZy93cy8yMDA1LzA1L2lkZW50aXR5L2NsYWltcy9uYW1laWRlbnRpZmllciI6IjE0MzM0IiwiaHR0cDovL3NjaGVtYXMueG1sc29hcC5vcmcvd3MvMjAwNS8wNS9pZGVudGl0eS9jbGFpbXMvc2lkIjoiYWUwMmEzMzQtOWZiYS00ZTI0LWFlODEtZDU5NGM1NWFkZTNjIiwiaHR0cDovL3NjaGVtYXMueG1sc29hcC5vcmcvd3MvMjAwNS8wNS9pZGVudGl0eS9jbGFpbXMvbmFtZSI6ImNsb3V0cmFkZXMiLCJodHRwOi8vc2NoZW1hcy5taWNyb3NvZnQuY29tL3dzLzIwMDgvMDYvaWRlbnRpdHkvY2xhaW1zL3JvbGUiOiJ1c2VyIiwiaHR0cDovL3NjaGVtYXMubWljcm9zb2Z0LmNvbS93cy8yMDA4LzA2L2lkZW50aXR5L2NsYWltcy9hdXRoZW50aWNhdGlvbm1ldGhvZCI6ImFwaS1rZXkiLCJtc2QiOiJDTUVHUk9VUF9UT0IiLCJtZmEiOiJ2ZXJpZmllZCIsImV4cCI6MTc2ODUzMjg5NH0.PGIc9JAgg6VtWdHh0BbqmKescIO5p17tYP18OM26tBA' \
  -d '{"accountId":15991599}'
```

**Response**: `[]` (empty - may need date parameters)

### 2. Statistics/profitFactor

```bash
curl -v -X POST 'https://userapi.topstepx.com/Statistics/profitFactor' \
  -H 'Content-Type: application/json' \
  -H 'accept: text/plain' \
  -H 'Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJodHRwOi8vc2NoZW1hcy54bWxzb2FwLm9yZy93cy8yMDA1LzA1L2lkZW50aXR5L2NsYWltcy9uYW1laWRlbnRpZmllciI6IjE0MzM0IiwiaHR0cDovL3NjaGVtYXMueG1sc29hcC5vcmcvd3MvMjAwNS8wNS9pZGVudGl0eS9jbGFpbXMvc2lkIjoiYWUwMmEzMzQtOWZiYS00ZTI0LWFlODEtZDU5NGM1NWFkZTNjIiwiaHR0cDovL3NjaGVtYXMueG1sc29hcC5vcmcvd3MvMjAwNS8wNS9pZGVudGl0eS9jbGFpbXMvbmFtZSI6ImNsb3V0cmFkZXMiLCJodHRwOi8vc2NoZW1hcy5taWNyb3NvZnQuY29tL3dzLzIwMDgvMDYvaWRlbnRpdHkvY2xhaW1zL3JvbGUiOiJ1c2VyIiwiaHR0cDovL3NjaGVtYXMubWljcm9zb2Z0LmNvbS93cy8yMDA4LzA2L2lkZW50aXR5L2NsYWltcy9hdXRoZW50aWNhdGlvbm1ldGhvZCI6ImFwaS1rZXkiLCJtc2QiOiJDTUVHUk9VUF9UT0IiLCJtZmEiOiJ2ZXJpZmllZCIsImV4cCI6MTc2ODUzMjg5NH0.PGIc9JAgg6VtWdHh0BbqmKescIO5p17tYP18OM26tBA' \
  -d '{"accountId":15991599}'
```

**Response**: `{"totalProfit":0,"totalLoss":0}`

### 3. Statistics/trades

```bash
curl -v -X POST 'https://userapi.topstepx.com/Statistics/trades' \
  -H 'Content-Type: application/json' \
  -H 'accept: text/plain' \
  -H 'Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJodHRwOi8vc2NoZW1hcy54bWxzb2FwLm9yZy93cy8yMDA1LzA1L2lkZW50aXR5L2NsYWltcy9uYW1laWRlbnRpZmllciI6IjE0MzM0IiwiaHR0cDovL3NjaGVtYXMueG1sc29hcC5vcmcvd3MvMjAwNS8wNS9pZGVudGl0eS9jbGFpbXMvc2lkIjoiYWUwMmEzMzQtOWZiYS00ZTI0LWFlODEtZDU5NGM1NWFkZTNjIiwiaHR0cDovL3NjaGVtYXMueG1sc29hcC5vcmcvd3MvMjAwNS8wNS9pZGVudGl0eS9jbGFpbXMvbmFtZSI6ImNsb3V0cmFkZXMiLCJodHRwOi8vc2NoZW1hcy5taWNyb3NvZnQuY29tL3dzLzIwMDgvMDYvaWRlbnRpdHkvY2xhaW1zL3JvbGUiOiJ1c2VyIiwiaHR0cDovL3NjaGVtYXMubWljcm9zb2Z0LmNvbS93cy8yMDA4LzA2L2lkZW50aXR5L2NsYWltcy9hdXRoZW50aWNhdGlvbm1ldGhvZCI6ImFwaS1rZXkiLCJtc2QiOiJDTUVHUk9VUF9UT0IiLCJtZmEiOiJ2ZXJpZmllZCIsImV4cCI6MTc2ODUzMjg5NH0.PGIc9JAgg6VtWdHh0BbqmKescIO5p17tYP18OM26tBA' \
  -d '{"accountId":15991599}'
```

**Response**: `[]` (empty - may need date parameters)

### 4. Statistics/monthly

```bash
curl -v -X POST 'https://userapi.topstepx.com/Statistics/monthly' \
  -H 'Content-Type: application/json' \
  -H 'accept: text/plain' \
  -H 'Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJodHRwOi8vc2NoZW1hcy54bWxzb2FwLm9yZy93cy8yMDA1LzA1L2lkZW50aXR5L2NsYWltcy9uYW1laWRlbnRpZmllciI6IjE0MzM0IiwiaHR0cDovL3NjaGVtYXMueG1sc29hcC5vcmcvd3MvMjAwNS8wNS9pZGVudGl0eS9jbGFpbXMvc2lkIjoiYWUwMmEzMzQtOWZiYS00ZTI0LWFlODEtZDU5NGM1NWFkZTNjIiwiaHR0cDovL3NjaGVtYXMueG1sc29hcC5vcmcvd3MvMjAwNS8wNS9pZGVudGl0eS9jbGFpbXMvbmFtZSI6ImNsb3V0cmFkZXMiLCJodHRwOi8vc2NoZW1hcy5taWNyb3NvZnQuY29tL3dzLzIwMDgvMDYvaWRlbnRpdHkvY2xhaW1zL3JvbGUiOiJ1c2VyIiwiaHR0cDovL3NjaGVtYXMubWljcm9zb2Z0LmNvbS93cy8yMDA4LzA2L2lkZW50aXR5L2NsYWltcy9hdXRoZW50aWNhdGlvbm1ldGhvZCI6ImFwaS1rZXkiLCJtc2QiOiJDTUVHUk9VUF9UT0IiLCJtZmEiOiJ2ZXJpZmllZCIsImV4cCI6MTc2ODUzMjg5NH0.PGIc9JAgg6VtWdHh0BbqmKescIO5p17tYP18OM26tBA' \
  -d '{"accountId":15991599}'
```

**Response**: `[]` (empty - may need date parameters)

## Testing with Additional Parameters

The empty responses suggest these endpoints may need:
- Date ranges (`startDate`, `endDate`)
- Different field names (`tradingAccountId` instead of `accountId`)
- Time periods or filters

### Try with Date Range

```bash
# Test with date range
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

## Swagger Documentation

Check the Swagger UI for exact parameter requirements:
- **Statistics API**: https://userapi.topstepx.com/swagger/index.html#/Statistics

## Summary

✅ **All Statistics endpoints exist and return 200**  
✅ **Base URL is `userapi.topstepx.com`** (different from trading API)  
⚠️ **Some return empty data** - check Swagger for required parameters
