# API Endpoint Format Fix

**Date**: 2026-01-15  
**Issue**: 400 Bad Request errors - API requires nested `request` object for some endpoints

## Problem

The TopStepX API has **two different request formats**:

1. **Flat format** (most endpoints): `{"accountId": 123}`
2. **Nested format** (some endpoints): `{"request": {"accountId": 123}}`

## Fixed Curl Commands

### ✅ Contract/available (Nested Format Required)

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

**Error you were getting**:
```json
{
  "errors": {
    "$": ["JSON deserialization... was missing required properties including: 'live'."],
    "request": ["The request field is required."]
  }
}
```

**Fix**: Wrap in `"request": {...}`

---

### ✅ Position/searchOpen (Nested Format Required)

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

**Error you were getting**:
```json
{
  "errors": {
    "$": ["JSON deserialization... was missing required properties including: 'accountId'."],
    "request": ["The request field is required."]
  }
}
```

**Fix**: Use `"request": {"accountId": ...}` instead of just `{"accountId": ...}`

---

### ✅ Account/search (Flat Format - Works)

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

**This one works as-is** - no nested `request` needed

---

### ✅ Order/search (Flat Format - Works)

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

---

### ✅ Trade/search (Flat Format - Works)

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

---

## Quick Reference Table

| Endpoint | Format | Example |
|----------|--------|---------|
| `/api/Account/search` | **Flat** | `{"accountId": 123}` |
| `/api/Order/search` | **Flat** | `{"accountId": 123, "startTimestamp": "..."}` |
| `/api/Trade/search` | **Flat** | `{"accountId": 123, "startTimestamp": "..."}` |
| `/api/Contract/available` | **Nested** | `{"request": {"live": false}}` |
| `/api/Position/searchOpen` | **Nested** | `{"request": {"accountId": 123}}` |

## Testing All Endpoints

Use the test script:
```bash
./scripts/test_api_endpoints.sh YOUR_TOKEN 15991599
```

Or test manually:
```bash
# Test Contract/available
curl -v -X POST 'https://api.topstepx.com/api/Contract/available' \
  -H 'Content-Type: application/json' \
  -H 'accept: text/plain' \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"request": {"live": false}}'

# Test Position/searchOpen
curl -v -X POST 'https://api.topstepx.com/api/Position/searchOpen' \
  -H 'Content-Type: application/json' \
  -H 'accept: text/plain' \
  -H "Authorization: Bearer $TOKEN" \
  -d "{\"request\": {\"accountId\": 15991599}}"
```

## Code May Need Updates

**Note**: Our codebase currently uses flat format for Contract/available and Position/searchOpen:
- `brokers/topstepx_adapter.py:3653` - Uses `{"live": False}`
- `brokers/topstepx_adapter.py:1554` - Uses `{"accountId": int(account_id)}`

**If these are failing in production**, we need to update the code to use nested format.

---

**Next Steps**: 
1. Test with corrected curl commands above
2. If they work, update codebase to use nested format
3. Test Statistics endpoints (they likely don't exist)
