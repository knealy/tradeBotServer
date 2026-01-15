#!/bin/bash

# TopStepX API Endpoint Testing Script
# Usage: ./test_api_endpoints.sh YOUR_TOKEN YOUR_ACCOUNT_ID

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Get token and account ID from arguments or environment
TOKEN="${1:-${TOKEN}}"
ACCOUNT_ID="${2:-${ACCOUNT_ID}}"

if [ -z "$TOKEN" ] || [ -z "$ACCOUNT_ID" ]; then
    echo -e "${RED}Error: Missing token or account ID${NC}"
    echo "Usage: $0 <TOKEN> <ACCOUNT_ID>"
    echo "Or set: export TOKEN=... ACCOUNT_ID=..."
    exit 1
fi

BASE_URL="https://api.topstepx.com"

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}TopStepX API Endpoint Testing${NC}"
echo -e "${BLUE}========================================${NC}"
echo "Token: ${TOKEN:0:20}..."
echo "Account ID: $ACCOUNT_ID"
echo ""

# Function to test endpoint
test_endpoint() {
    local name=$1
    local endpoint=$2
    local data=$3
    local expected_status=${4:-200}
    
    echo -e "${YELLOW}Testing: $name${NC}"
    echo "  Endpoint: $endpoint"
    
    # Make request and capture both stdout and stderr
    response=$(curl -s -w "\nHTTP_CODE:%{http_code}\nTIME:%{time_total}" \
      -X POST "$BASE_URL$endpoint" \
      -H 'Content-Type: application/json' \
      -H 'accept: text/plain' \
      -H "Authorization: Bearer $TOKEN" \
      -d "$data" 2>&1)
    
    # Extract HTTP code and time
    http_code=$(echo "$response" | grep "HTTP_CODE" | cut -d: -f2)
    time_taken=$(echo "$response" | grep "TIME:" | cut -d: -f2)
    body=$(echo "$response" | sed '/HTTP_CODE/d' | sed '/TIME:/d')
    
    # Check result
    if [ -z "$http_code" ]; then
        echo -e "  ${RED}❌ NO RESPONSE${NC}"
        echo "  Full output: $response"
    elif [ "$http_code" == "200" ]; then
        echo -e "  ${GREEN}✅ EXISTS (200 OK)${NC} - Time: ${time_taken}s"
        if [ -n "$body" ]; then
            echo "  Response preview: $(echo "$body" | head -c 200)..."
        fi
    elif [ "$http_code" == "404" ]; then
        echo -e "  ${RED}❌ NOT FOUND (404)${NC}"
    elif [ "$http_code" == "401" ]; then
        echo -e "  ${YELLOW}⚠️  UNAUTHORIZED (401)${NC} - Token may be expired"
        echo "  Response: $body"
    elif [ "$http_code" == "400" ]; then
        echo -e "  ${YELLOW}⚠️  BAD REQUEST (400)${NC}"
        echo "  Response: $body"
    else
        echo -e "  ${YELLOW}⚠️  Status: $http_code${NC}"
        echo "  Response: $body"
    fi
    echo ""
}

# Test known working endpoints first
echo -e "${GREEN}=== Known Working Endpoints ===${NC}"
echo ""

test_endpoint "Order/search" \
  "/api/Order/search" \
  "{\"accountId\": $ACCOUNT_ID, \"startTimestamp\": \"2026-01-15T00:00:00Z\", \"endTimestamp\": \"2026-01-15T23:59:59Z\"}"

test_endpoint "Account/search" \
  "/api/Account/search" \
  "{\"accountId\": $ACCOUNT_ID}"

test_endpoint "Trade/search" \
  "/api/Trade/search" \
  "{\"accountId\": $ACCOUNT_ID, \"startTimestamp\": \"2026-01-15T00:00:00Z\", \"endTimestamp\": \"2026-01-15T23:59:59Z\"}"

test_endpoint "Contract/search" \
  "/api/Contract/search" \
  "{}"

# Contract/available requires nested "request" object
test_endpoint "Contract/available" \
  "/api/Contract/available" \
  "{\"request\": {\"live\": false}}"

# Position/searchOpen requires nested "request" object  
test_endpoint "Position/searchOpen" \
  "/api/Position/searchOpen" \
  "{\"request\": {\"accountId\": $ACCOUNT_ID}}"

# Test Statistics endpoints (may not exist)
echo -e "${YELLOW}=== Statistics Endpoints (May Not Exist) ===${NC}"
echo ""

test_endpoint "Statistics/todaystats (with /api/)" \
  "/api/Statistics/todaystats" \
  "{\"tradingAccountId\": $ACCOUNT_ID}"

test_endpoint "Statistics/todaystats (without /api/)" \
  "/Statistics/todaystats" \
  "{\"tradingAccountId\": $ACCOUNT_ID}"

test_endpoint "Statistics/daystats (with /api/)" \
  "/api/Statistics/daystats" \
  "{\"tradingAccountId\": $ACCOUNT_ID, \"startTradeDay\": \"2026-01-15\", \"endTradeDay\": \"2026-01-15\"}"

test_endpoint "Statistics/daystats (without /api/)" \
  "/Statistics/daystats" \
  "{\"tradingAccountId\": $ACCOUNT_ID, \"startTradeDay\": \"2026-01-15\", \"endTradeDay\": \"2026-01-15\"}"

test_endpoint "Statistics/profitFactor (with /api/)" \
  "/api/Statistics/profitFactor" \
  "{\"tradingAccountId\": $ACCOUNT_ID, \"startTradeDay\": \"2026-01-15\", \"endTradeDay\": \"2026-01-15\"}"

test_endpoint "Statistics/profitFactor (without /api/)" \
  "/Statistics/profitFactor" \
  "{\"tradingAccountId\": $ACCOUNT_ID, \"startTradeDay\": \"2026-01-15\", \"endTradeDay\": \"2026-01-15\"}"

echo -e "${BLUE}========================================${NC}"
echo -e "${GREEN}Testing complete!${NC}"
echo ""
echo "If known endpoints (Order/search, Account/search) return 401:"
echo "  - Your token may be expired"
echo "  - Re-authenticate to get a new token"
echo ""
echo "If Statistics endpoints return 404:"
echo "  - They don't exist - remove from code"
echo "  - Use Trade/search + manual calculation instead"
