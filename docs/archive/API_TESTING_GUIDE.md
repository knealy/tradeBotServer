# API Endpoint Testing Guide

**Date**: 2026-01-15  
**Purpose**: Properly test TopStepX API endpoints with curl

## Common Issues

1. **Using Swagger UI URLs instead of API endpoints**
   - ❌ Wrong: `https://api.topstepx.com/swagger/index.html#/Order/Order_Get`
   - ✅ Correct: `https://api.topstepx.com/api/Order/search`

2. **Not seeing response details**
   - Use `-v` (verbose) or `-i` (include headers) flags
   - Check HTTP status codes

3. **Token expiration**
   - JWT tokens expire - check the `exp` claim
   - Re-authenticate if needed

## Step 1: Test with Verbose Mode

Always use `-v` or `-i` to see what's happening:

```bash
curl -v -X POST 'https://api.topstepx.com/api/Order/search' \
  -H 'Content-Type: application/json' \
  -H 'accept: text/plain' \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -d '{
    "accountId": 15991599,
    "startTimestamp": "2026-01-15T00:00:00Z",
    "endTimestamp": "2026-01-15T23:59:59Z"
  }'
```

The `-v` flag shows:
- Request headers sent
- Response headers received
- HTTP status code
- Response body

## Step 2: Test Known Working Endpoints First

### Test 1: Order/search (Definitely Exists)

```bash
TOKEN="YOUR_TOKEN_HERE"
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

**Expected Response**:
- HTTP 200 OK
- JSON with `orders` array or `success: true`

### Test 2: Account/search (Definitely Exists)

```bash
curl -v -X POST 'https://api.topstepx.com/api/Account/search' \
  -H 'Content-Type: application/json' \
  -H 'accept: text/plain' \
  -H "Authorization: Bearer $TOKEN" \
  -d "{
    \"accountId\": $ACCOUNT_ID
  }"
```

### Test 3: Trade/search (Definitely Exists)

```bash
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

### Test 4: Contract/search (Definitely Exists)

```bash
curl -v -X POST 'https://api.topstepx.com/api/Contract/search' \
  -H 'Content-Type: application/json' \
  -H 'accept: text/plain' \
  -H "Authorization: Bearer $TOKEN" \
  -d '{}'
```

## Step 3: Test Statistics Endpoints (May Not Exist)

### Test Statistics/todaystats

```bash
# Try with /api/ prefix
curl -v -X POST 'https://api.topstepx.com/api/Statistics/todaystats' \
  -H 'Content-Type: application/json' \
  -H 'accept: text/plain' \
  -H "Authorization: Bearer $TOKEN" \
  -d "{
    \"tradingAccountId\": $ACCOUNT_ID
  }"

# Try without /api/ prefix
curl -v -X POST 'https://api.topstepx.com/Statistics/todaystats' \
  -H 'Content-Type: application/json' \
  -H 'accept: text/plain' \
  -H "Authorization: Bearer $TOKEN" \
  -d "{
    \"tradingAccountId\": $ACCOUNT_ID
  }"
```

**Expected if doesn't exist**: HTTP 404 Not Found

## Step 4: Check Token Expiration

Your token has an expiration time. Decode it to check:

```bash
# Using jq (if installed)
echo "YOUR_TOKEN" | cut -d. -f2 | base64 -d 2>/dev/null | jq .

# Or use online JWT decoder: https://jwt.io
```

Look for `"exp"` field - it's a Unix timestamp.

## Step 5: Better Testing Script

Save this as `test_api_endpoints.sh`:

```bash
#!/bin/bash

# Configuration
TOKEN="YOUR_TOKEN_HERE"
ACCOUNT_ID=15991599

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo "Testing TopStepX API Endpoints"
echo "================================"
echo ""

# Function to test endpoint
test_endpoint() {
    local name=$1
    local url=$2
    local data=$3
    
    echo -n "Testing $name... "
    
    response=$(curl -s -w "\nHTTP_CODE:%{http_code}" -X POST "$url" \
      -H 'Content-Type: application/json' \
      -H 'accept: text/plain' \
      -H "Authorization: Bearer $TOKEN" \
      -d "$data" 2>&1)
    
    http_code=$(echo "$response" | grep "HTTP_CODE" | cut -d: -f2)
    body=$(echo "$response" | sed '/HTTP_CODE/d')
    
    if [ "$http_code" == "200" ]; then
        echo -e "${GREEN}✅ EXISTS (200 OK)${NC}"
        echo "Response preview: $(echo "$body" | head -c 200)..."
    elif [ "$http_code" == "404" ]; then
        echo -e "${RED}❌ NOT FOUND (404)${NC}"
    elif [ "$http_code" == "401" ]; then
        echo -e "${YELLOW}⚠️  UNAUTHORIZED (401) - Token may be expired${NC}"
    elif [ "$http_code" == "400" ]; then
        echo -e "${YELLOW}⚠️  BAD REQUEST (400) - Check request format${NC}"
        echo "Response: $body"
    elif [ -z "$http_code" ]; then
        echo -e "${RED}❌ NO RESPONSE${NC}"
        echo "Full response: $response"
    else
        echo -e "${YELLOW}⚠️  Status: $http_code${NC}"
        echo "Response: $body"
    fi
    echo ""
}

# Test known working endpoints first
echo "=== Known Working Endpoints ==="
test_endpoint "Order/search" \
  "https://api.topstepx.com/api/Order/search" \
  "{\"accountId\": $ACCOUNT_ID, \"startTimestamp\": \"2026-01-15T00:00:00Z\", \"endTimestamp\": \"2026-01-15T23:59:59Z\"}"

test_endpoint "Account/search" \
  "https://api.topstepx.com/api/Account/search" \
  "{\"accountId\": $ACCOUNT_ID}"

test_endpoint "Trade/search" \
  "https://api.topstepx.com/api/Trade/search" \
  "{\"accountId\": $ACCOUNT_ID, \"startTimestamp\": \"2026-01-15T00:00:00Z\", \"endTimestamp\": \"2026-01-15T23:59:59Z\"}"

test_endpoint "Contract/search" \
  "https://api.topstepx.com/api/Contract/search" \
  "{}"

# Test Statistics endpoints (may not exist)
echo "=== Statistics Endpoints (May Not Exist) ==="
test_endpoint "Statistics/todaystats (with /api/)" \
  "https://api.topstepx.com/api/Statistics/todaystats" \
  "{\"tradingAccountId\": $ACCOUNT_ID}"

test_endpoint "Statistics/todaystats (without /api/)" \
  "https://api.topstepx.com/Statistics/todaystats" \
  "{\"tradingAccountId\": $ACCOUNT_ID}"

test_endpoint "Statistics/daystats (with /api/)" \
  "https://api.topstepx.com/api/Statistics/daystats" \
  "{\"tradingAccountId\": $ACCOUNT_ID, \"startTradeDay\": \"2026-01-15\", \"endTradeDay\": \"2026-01-15\"}"

test_endpoint "Statistics/profitFactor (with /api/)" \
  "https://api.topstepx.com/api/Statistics/profitFactor" \
  "{\"tradingAccountId\": $ACCOUNT_ID, \"startTradeDay\": \"2026-01-15\", \"endTradeDay\": \"2026-01-15\"}"

echo "Testing complete!"
```

Make it executable:
```bash
chmod +x test_api_endpoints.sh
./test_api_endpoints.sh
```

## Step 6: Check Response Format

If you get no response, check:

1. **Network connectivity**:
   ```bash
   curl -v https://api.topstepx.com/api/Account/search
   ```

2. **Token validity** - Decode JWT:
   ```bash
   # Extract payload (middle part of JWT)
   echo "YOUR_TOKEN" | cut -d. -f2 | base64 -d 2>/dev/null
   ```

3. **Request format** - Some endpoints need specific field names:
   ```bash
   # Order/search uses "accountId" (camelCase)
   # Not "tradingAccountId" or "account_id"
   ```

## Step 7: Use Python for Better Testing

Create `test_endpoints.py`:

```python
#!/usr/bin/env python3
import requests
import json
from datetime import datetime, timezone

TOKEN = "YOUR_TOKEN_HERE"
ACCOUNT_ID = 15991599
BASE_URL = "https://api.topstepx.com"

headers = {
    "Content-Type": "application/json",
    "accept": "text/plain",
    "Authorization": f"Bearer {TOKEN}"
}

def test_endpoint(name, endpoint, data):
    """Test an API endpoint and show results."""
    url = f"{BASE_URL}{endpoint}"
    print(f"\n{'='*60}")
    print(f"Testing: {name}")
    print(f"URL: {url}")
    print(f"Data: {json.dumps(data, indent=2)}")
    print(f"{'='*60}")
    
    try:
        response = requests.post(url, headers=headers, json=data, timeout=10)
        print(f"Status Code: {response.status_code}")
        print(f"Headers: {dict(response.headers)}")
        
        if response.status_code == 200:
            print("✅ SUCCESS")
            try:
                result = response.json()
                print(f"Response: {json.dumps(result, indent=2)[:500]}...")
            except:
                print(f"Response (text): {response.text[:500]}...")
        elif response.status_code == 404:
            print("❌ NOT FOUND (404)")
        elif response.status_code == 401:
            print("⚠️  UNAUTHORIZED (401) - Token may be expired")
        else:
            print(f"⚠️  Status: {response.status_code}")
            print(f"Response: {response.text[:500]}")
    except requests.exceptions.RequestException as e:
        print(f"❌ ERROR: {e}")

# Test known working endpoints
print("\n=== Known Working Endpoints ===")
test_endpoint(
    "Order/search",
    "/api/Order/search",
    {
        "accountId": ACCOUNT_ID,
        "startTimestamp": "2026-01-15T00:00:00Z",
        "endTimestamp": "2026-01-15T23:59:59Z"
    }
)

test_endpoint(
    "Account/search",
    "/api/Account/search",
    {"accountId": ACCOUNT_ID}
)

test_endpoint(
    "Trade/search",
    "/api/Trade/search",
    {
        "accountId": ACCOUNT_ID,
        "startTimestamp": "2026-01-15T00:00:00Z",
        "endTimestamp": "2026-01-15T23:59:59Z"
    }
)

# Test Statistics endpoints
print("\n=== Statistics Endpoints (May Not Exist) ===")
test_endpoint(
    "Statistics/todaystats",
    "/api/Statistics/todaystats",
    {"tradingAccountId": ACCOUNT_ID}
)

test_endpoint(
    "Statistics/daystats",
    "/api/Statistics/daystats",
    {
        "tradingAccountId": ACCOUNT_ID,
        "startTradeDay": "2026-01-15",
        "endTradeDay": "2026-01-15"
    }
)
```

Run it:
```bash
python3 test_endpoints.py
```

## Common Mistakes to Avoid

1. ❌ **Using Swagger UI page URLs**:
   - Wrong: `https://api.topstepx.com/swagger/index.html#/Order/Order_Get`
   - Right: `https://api.topstepx.com/api/Order/search`

2. ❌ **Missing Content-Type header**:
   - Always include: `-H 'Content-Type: application/json'`

3. ❌ **Wrong field names**:
   - Use camelCase: `accountId`, not `account_id` or `tradingAccountId` (for most endpoints)

4. ❌ **Not checking HTTP status**:
   - Use `-v` or `-i` to see status codes
   - 200 = success, 404 = not found, 401 = unauthorized

5. ❌ **Expired token**:
   - Check token expiration in JWT payload
   - Re-authenticate if expired

## Quick Reference: Endpoints That Definitely Exist

Based on Swagger documentation:

- ✅ `POST /api/Account/search`
- ✅ `POST /api/Order/search`
- ✅ `POST /api/Order/searchOpen`
- ✅ `POST /api/Trade/search`
- ✅ `POST /api/Contract/search`
- ✅ `POST /api/Contract/available`
- ✅ `POST /api/History/retrieveBars`
- ✅ `POST /api/Auth/loginApp`
- ✅ `POST /api/Auth/loginKey`
- ✅ `POST /api/Auth/validate`

## Endpoints That May Not Exist

- ❓ `POST /api/Statistics/todaystats`
- ❓ `POST /api/Statistics/daystats`
- ❓ `POST /api/Statistics/profitFactor`
- ❓ `POST /api/Fill/search`

---

**Next Steps**: Use the testing script to verify which endpoints exist, then update code accordingly.
