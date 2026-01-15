# Testing Statistics Endpoints

**Date**: 2026-01-15  
**Purpose**: Verify if Statistics API endpoints exist

## Step 1: Get JWT Token

### Option A: Using loginApp (Application Login)

```bash
# Replace with your actual credentials
curl -X POST 'https://api.topstepx.com/api/Auth/loginApp' \
  -H 'Content-Type: application/json' \
  -H 'accept: text/plain' \
  -d '{
    "username": "YOUR_USERNAME",
    "password": "YOUR_PASSWORD",
    "applicationId": "YOUR_APP_ID"
  }'
```

**Response will include**:
```json
{
  "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "expires": "2026-01-15T10:00:00Z"
}
```

### Option B: Using loginKey (API Key Login)

```bash
curl -X POST 'https://api.topstepx.com/api/Auth/loginKey' \
  -H 'Content-Type: application/json' \
  -H 'accept: text/plain' \
  -d '{
    "apiKey": "YOUR_API_KEY",
    "apiSecret": "YOUR_API_SECRET"
  }'
```

## Step 2: Extract Token

Save the token from the response:

```bash
# Example: Save token to variable
TOKEN="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
```

## Step 3: Test Statistics Endpoints

**IMPORTANT**: Always use `-v` (verbose) flag to see what's happening!

### Test 1: `/api/Statistics/todaystats`

```bash
# Use -v to see full request/response details
curl -v -X POST 'https://api.topstepx.com/api/Statistics/todaystats' \
  -H 'Content-Type: application/json' \
  -H 'accept: text/plain' \
  -H "Authorization: Bearer $TOKEN" \
  -d "{
    \"tradingAccountId\": $ACCOUNT_ID
  }"
```

**What to look for**:
- `HTTP/1.1 200 OK` = Endpoint exists
- `HTTP/1.1 404 Not Found` = Endpoint doesn't exist
- `HTTP/1.1 401 Unauthorized` = Token expired or invalid
- No response = Network issue or timeout

### Test 2: `/Statistics/todaystats` (without /api/ prefix)

```bash
curl -X POST 'https://api.topstepx.com/Statistics/todaystats' \
  -H 'Content-Type: application/json' \
  -H 'accept: text/plain' \
  -H "Authorization: Bearer $TOKEN" \
  -d '{
    "tradingAccountId": YOUR_ACCOUNT_ID
  }'
```

### Test 3: `/api/Statistics/daystats`

```bash
curl -X POST 'https://api.topstepx.com/api/Statistics/daystats' \
  -H 'Content-Type: application/json' \
  -H 'accept: text/plain' \
  -H "Authorization: Bearer $TOKEN" \
  -d '{
    "tradingAccountId": YOUR_ACCOUNT_ID,
    "startTradeDay": "2026-01-15",
    "endTradeDay": "2026-01-15"
  }'
```

### Test 4: `/Statistics/daystats` (without /api/ prefix)

```bash
curl -X POST 'https://api.topstepx.com/Statistics/daystats' \
  -H 'Content-Type: application/json' \
  -H 'accept: text/plain' \
  -H "Authorization: Bearer $TOKEN" \
  -d '{
    "tradingAccountId": YOUR_ACCOUNT_ID,
    "startTradeDay": "2026-01-15",
    "endTradeDay": "2026-01-15"
  }'
```

### Test 5: `/api/Statistics/profitFactor`

```bash
curl -X POST 'https://api.topstepx.com/api/Statistics/profitFactor' \
  -H 'Content-Type: application/json' \
  -H 'accept: text/plain' \
  -H "Authorization: Bearer $TOKEN" \
  -d '{
    "tradingAccountId": YOUR_ACCOUNT_ID,
    "startTradeDay": "2026-01-15",
    "endTradeDay": "2026-01-15"
  }'
```

### Test 6: `/Statistics/profitFactor` (without /api/ prefix)

```bash
curl -X POST 'https://api.topstepx.com/Statistics/profitFactor' \
  -H 'Content-Type: application/json' \
  -H 'accept: text/plain' \
  -H "Authorization: Bearer $TOKEN" \
  -d '{
    "tradingAccountId": YOUR_ACCOUNT_ID,
    "startTradeDay": "2026-01-15",
    "endTradeDay": "2026-01-15"
  }'
```

## Expected Results

### If Endpoint Exists (200 OK):
```json
{
  "success": true,
  "data": { ... }
}
```

### If Endpoint Doesn't Exist (404 Not Found):
```json
{
  "error": "Not Found",
  "statusCode": 404
}
```

### If Authentication Fails (401 Unauthorized):
```json
{
  "error": "Unauthorized",
  "statusCode": 401
}
```

## Quick Test Script

Save this as `test_statistics_endpoints.sh`:

```bash
#!/bin/bash

# Configuration
USERNAME="YOUR_USERNAME"
PASSWORD="YOUR_PASSWORD"
APP_ID="YOUR_APP_ID"
ACCOUNT_ID=YOUR_ACCOUNT_ID

# Step 1: Login and get token
echo "Step 1: Logging in..."
LOGIN_RESPONSE=$(curl -s -X POST 'https://api.topstepx.com/api/Auth/loginApp' \
  -H 'Content-Type: application/json' \
  -H 'accept: text/plain' \
  -d "{
    \"username\": \"$USERNAME\",
    \"password\": \"$PASSWORD\",
    \"applicationId\": \"$APP_ID\"
  }")

TOKEN=$(echo $LOGIN_RESPONSE | jq -r '.token')

if [ "$TOKEN" == "null" ] || [ -z "$TOKEN" ]; then
  echo "❌ Login failed!"
  echo "Response: $LOGIN_RESPONSE"
  exit 1
fi

echo "✅ Login successful! Token: ${TOKEN:0:20}..."

# Step 2: Test endpoints
ENDPOINTS=(
  "/api/Statistics/todaystats"
  "/Statistics/todaystats"
  "/api/Statistics/daystats"
  "/Statistics/daystats"
  "/api/Statistics/profitFactor"
  "/Statistics/profitFactor"
)

for endpoint in "${ENDPOINTS[@]}"; do
  echo ""
  echo "Testing: $endpoint"
  RESPONSE=$(curl -s -w "\nHTTP_CODE:%{http_code}" -X POST "https://api.topstepx.com$endpoint" \
    -H 'Content-Type: application/json' \
    -H 'accept: text/plain' \
    -H "Authorization: Bearer $TOKEN" \
    -d "{
      \"tradingAccountId\": $ACCOUNT_ID
    }")
  
  HTTP_CODE=$(echo "$RESPONSE" | grep "HTTP_CODE" | cut -d: -f2)
  BODY=$(echo "$RESPONSE" | sed '/HTTP_CODE/d')
  
  if [ "$HTTP_CODE" == "200" ]; then
    echo "✅ EXISTS (200 OK)"
    echo "Response: $BODY" | jq '.' 2>/dev/null || echo "$BODY"
  elif [ "$HTTP_CODE" == "404" ]; then
    echo "❌ NOT FOUND (404)"
  elif [ "$HTTP_CODE" == "401" ]; then
    echo "⚠️  UNAUTHORIZED (401) - Check token"
  else
    echo "⚠️  Status: $HTTP_CODE"
    echo "Response: $BODY"
  fi
done

echo ""
echo "✅ Testing complete!"
```

Make it executable:
```bash
chmod +x test_statistics_endpoints.sh
./test_statistics_endpoints.sh
```

## Notes

- Replace `YOUR_USERNAME`, `YOUR_PASSWORD`, `YOUR_APP_ID`, and `YOUR_ACCOUNT_ID` with actual values
- The token expires after a certain time (check the `expires` field in login response)
- If you get 401 errors, the token may have expired - login again
- If all endpoints return 404, they don't exist and should be removed from code
