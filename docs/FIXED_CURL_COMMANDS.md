# Fixed Curl Commands for TopStepX API

**Date**: 2026-01-15  
**Issue**: 400 Bad Request errors due to incorrect request format

## The Problem

You were getting 400 errors because some endpoints require a **nested `request` object**, not flat parameters.

## Fixed Commands

### 1. Contract/available (Requires Nested Request)

**❌ Your Original (Wrong)**:
```bash
curl -X POST 'https://api.topstepx.com/api/Contract/available' \
  -H 'Content-Type: application/json' \
  -H 'accept: text/plain' \
  -H "Authorization: Bearer $TOKEN" \
  -d '{
    "tradingAccountId": 15991599
  }'
```

**✅ Correct Format**:
```bash
TOKEN="YOUR_TOKEN"

curl -v -X POST 'https://api.topstepx.com/api/Contract/available' \
  -H 'Content-Type: application/json' \
  -H 'accept: text/plain' \
  -H "Authorization: Bearer $TOKEN" \
  -d '{
    "request": {
      "live": false
    }
  }'
```

**Expected Response**: HTTP 200 with list of contracts

---

### 2. Position/searchOpen (Requires Nested Request)

**❌ Your Original (Wrong)**:
```bash
curl -X POST 'https://api.topstepx.com/api/Position/searchOpen' \
  -H 'Content-Type: application/json' \
  -H 'accept: text/plain' \
  -H "Authorization: Bearer $TOKEN" \
  -d '{
    "tradingAccountId": 15991599
  }'
```

**✅ Correct Format**:
```bash
TOKEN="YOUR_TOKEN"
ACCOUNT_ID=15991599

curl -v -X POST 'https://api.topstepx.com/api/Position/searchOpen' \
  -H 'Content-Type: application/json' \
  -H 'accept: text/plain' \
  -H "Authorization: Bearer $TOKEN" \
  -d "{
    \"request\": {
      \"accountId\": $ACCOUNT_ID
    }
  }"
```

**Expected Response**: HTTP 200 with positions array

---

### 3. Account/search (Flat Format - Works as-is)

**✅ Correct Format**:
```bash
TOKEN="YOUR_TOKEN"
ACCOUNT_ID=15991599

curl -v -X POST 'https://api.topstepx.com/api/Account/search' \
  -H 'Content-Type: application/json' \
  -H 'accept: text/plain' \
  -H "Authorization: Bearer $TOKEN" \
  -d "{
    \"accountId\": $ACCOUNT_ID
  }"
```

**Expected Response**: HTTP 200 with account data

---

### 4. Order/search (Flat Format)

**✅ Correct Format**:
```bash
TOKEN="YOUR_TOKEN"
ACCOUNT_ID=15991599

curl -v -X POST 'https://api.topstepx.com/api/Order/search' \
  -H 'Content-Type: application/json' \
  -H 'accept: text/plain' \
  -H "Authorization: Bearer $TOKEN" \
  -d "{
    \"accountId\": $ACCOUNT_ID,
    \"startTimestamp\": \"2026-01-15T00:00:00Z\",
    \"endTimestamp\": \"2026-01-15T23:59:59Z\"
  }"
```

**Expected Response**: HTTP 200 with orders array

---

### 5. Trade/search (Flat Format)

**✅ Correct Format**:
```bash
TOKEN="YOUR_TOKEN"
ACCOUNT_ID=15991599

curl -v -X POST 'https://api.topstepx.com/api/Trade/search' \
  -H 'Content-Type: application/json' \
  -H 'accept: text/plain' \
  -H "Authorization: Bearer $TOKEN" \
  -d "{
    \"accountId\": $ACCOUNT_ID,
    \"startTimestamp\": \"2026-01-15T00:00:00Z\",
    \"endTimestamp\": \"2026-01-15T23:59:59Z\"
  }"
```

**Expected Response**: HTTP 200 with trades array

---

## Key Differences

### Endpoints Requiring Nested `request`:
- `/api/Contract/available` → `{"request": {"live": false}}`
- `/api/Position/searchOpen` → `{"request": {"accountId": 123}}`

### Endpoints Using Flat Format:
- `/api/Account/search` → `{"accountId": 123}`
- `/api/Order/search` → `{"accountId": 123, "startTimestamp": "..."}`
- `/api/Trade/search` → `{"accountId": 123, "startTimestamp": "..."}`

## Error Message Guide

If you see:
```
"missing required properties including: 'live'"
"The request field is required"
```

**Fix**: Wrap your parameters in `"request": {...}`

## Quick Test Script

```bash
#!/bin/bash
TOKEN="YOUR_TOKEN"
ACCOUNT_ID=15991599

echo "Testing Contract/available..."
curl -v -X POST 'https://api.topstepx.com/api/Contract/available' \
  -H 'Content-Type: application/json' \
  -H 'accept: text/plain' \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"request": {"live": false}}'

echo ""
echo "Testing Position/searchOpen..."
curl -v -X POST 'https://api.topstepx.com/api/Position/searchOpen' \
  -H 'Content-Type: application/json' \
  -H 'accept: text/plain' \
  -H "Authorization: Bearer $TOKEN" \
  -d "{\"request\": {\"accountId\": $ACCOUNT_ID}}"

echo ""
echo "Testing Account/search..."
curl -v -X POST 'https://api.topstepx.com/api/Account/search' \
  -H 'Content-Type: application/json' \
  -H 'accept: text/plain' \
  -H "Authorization: Bearer $TOKEN" \
  -d "{\"accountId\": $ACCOUNT_ID}"
```

---

**Note**: Always use `-v` flag to see HTTP status codes and full responses!
