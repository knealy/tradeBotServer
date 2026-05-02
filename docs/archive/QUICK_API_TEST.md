# Quick API Testing Reference

## The Problem

You were using **Swagger UI page URLs** (with `#` fragments) instead of **actual API endpoints**:

❌ **Wrong**:
```bash
curl 'https://api.topstepx.com/swagger/index.html#/Order/Order_Get'
```

✅ **Correct**:
```bash
curl 'https://api.topstepx.com/api/Order/search'
```

## Quick Test Commands

### 1. Test with Verbose Mode (See What's Happening)

```bash
TOKEN="YOUR_TOKEN"
ACCOUNT_ID=15991599

# Test Order/search (definitely exists)
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

The `-v` flag shows:
- Request headers
- Response headers  
- HTTP status code
- Response body

### 2. Use the Testing Script

```bash
# Make it executable (already done)
chmod +x scripts/test_api_endpoints.sh

# Run it
./scripts/test_api_endpoints.sh YOUR_TOKEN 15991599
```

### 3. Test Known Working Endpoints First

```bash
TOKEN="YOUR_TOKEN"
ACCOUNT_ID=15991599

# Account/search - definitely exists
curl -i -X POST 'https://api.topstepx.com/api/Account/search' \
  -H 'Content-Type: application/json' \
  -H 'accept: text/plain' \
  -H "Authorization: Bearer $TOKEN" \
  -d "{\"accountId\": $ACCOUNT_ID}"

# Trade/search - definitely exists  
curl -i -X POST 'https://api.topstepx.com/api/Trade/search' \
  -H 'Content-Type: application/json' \
  -H 'accept: text/plain' \
  -H "Authorization: Bearer $TOKEN" \
  -d "{
    \"accountId\": $ACCOUNT_ID,
    \"startTimestamp\": \"2026-01-15T00:00:00Z\",
    \"endTimestamp\": \"2026-01-15T23:59:59Z\"
  }"

# Contract/available - requires nested "request" object
curl -i -X POST 'https://api.topstepx.com/api/Contract/available' \
  -H 'Content-Type: application/json' \
  -H 'accept: text/plain' \
  -H "Authorization: Bearer $TOKEN" \
  -d '{
    "request": {
      "live": false
    }
  }'

# Position/searchOpen - requires nested "request" object
curl -i -X POST 'https://api.topstepx.com/api/Position/searchOpen' \
  -H 'Content-Type: application/json' \
  -H 'accept: text/plain' \
  -H "Authorization: Bearer $TOKEN" \
  -d "{
    \"request\": {
      \"accountId\": $ACCOUNT_ID
    }
  }"
```

### 4. Test Statistics Endpoints (May Not Exist)

```bash
# Try with /api/ prefix
curl -i -X POST 'https://api.topstepx.com/api/Statistics/todaystats' \
  -H 'Content-Type: application/json' \
  -H 'accept: text/plain' \
  -H "Authorization: Bearer $TOKEN" \
  -d "{\"tradingAccountId\": $ACCOUNT_ID}"

# Try without /api/ prefix
curl -i -X POST 'https://api.topstepx.com/Statistics/todaystats' \
  -H 'Content-Type: application/json' \
  -H 'accept: text/plain' \
  -H "Authorization: Bearer $TOKEN" \
  -d "{\"tradingAccountId\": $ACCOUNT_ID}"
```

## What to Look For

- **HTTP/1.1 200 OK** = Endpoint exists and worked
- **HTTP/1.1 404 Not Found** = Endpoint doesn't exist
- **HTTP/1.1 401 Unauthorized** = Token expired or invalid
- **No response** = Network issue, timeout, or server down

## Common Issues

1. **No response at all**:
   - Check network: `ping api.topstepx.com`
   - Check token expiration
   - Use `-v` flag to see what's happening

2. **401 Unauthorized**:
   - Token expired - decode JWT to check `exp` field
   - Re-authenticate to get new token

3. **404 Not Found**:
   - Endpoint doesn't exist
   - Check URL spelling
   - Try with/without `/api/` prefix

## Decode JWT Token to Check Expiration

```bash
# Extract and decode the payload (middle part)
TOKEN="YOUR_TOKEN"
echo "$TOKEN" | cut -d. -f2 | base64 -d 2>/dev/null | python3 -m json.tool

# Look for "exp" field - it's a Unix timestamp
# Compare with: date +%s
```

## Correct Endpoint URLs

Based on Swagger docs, these are the **actual API endpoints**:

- ✅ `POST https://api.topstepx.com/api/Account/search`
- ✅ `POST https://api.topstepx.com/api/Order/search`
- ✅ `POST https://api.topstepx.com/api/Trade/search`
- ✅ `POST https://api.topstepx.com/api/Contract/search`
- ✅ `POST https://api.topstepx.com/api/History/retrieveBars`
- ❓ `POST https://api.topstepx.com/api/Statistics/todaystats` (may not exist)
- ❓ `POST https://api.topstepx.com/api/Statistics/daystats` (may not exist)

**NOT** these (these are Swagger UI page URLs):
- ❌ `https://api.topstepx.com/swagger/index.html#/Order/Order_Get`
- ❌ `https://api.topstepx.com/swagger/index.html#/Statistics/Statistics_GetDaily`

## Quick Test: Verify Your Token Works

```bash
TOKEN="YOUR_TOKEN"

# This should return 200 if token is valid
curl -i -X POST 'https://api.topstepx.com/api/Account/search' \
  -H 'Content-Type: application/json' \
  -H 'accept: text/plain' \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"accountId": 15991599}'
```

If this returns 401, your token is expired - get a new one!
