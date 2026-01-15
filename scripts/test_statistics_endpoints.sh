#!/bin/bash

# Test Statistics Endpoints with Different Parameters
# Usage: ./test_statistics_endpoints.sh YOUR_TOKEN

set -e

TOKEN="${1:-${TOKEN}}"

if [ -z "$TOKEN" ]; then
    echo "Error: Missing token"
    echo "Usage: $0 <TOKEN>"
    exit 1
fi

BASE_URL="https://userapi.topstepx.com"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo "Testing Statistics Endpoints"
echo "============================"
echo ""

# Test function
test_endpoint() {
    local name=$1
    local endpoint=$2
    local data=$3
    
    echo -e "${YELLOW}Testing: $name${NC}"
    echo "  Endpoint: $endpoint"
    echo "  Data: $data"
    
    response=$(curl -s -w "\nHTTP_CODE:%{http_code}" -X POST "$BASE_URL$endpoint" \
      -H 'Content-Type: application/json' \
      -H 'accept: text/plain' \
      -H "Authorization: Bearer $TOKEN" \
      -d "$data" 2>&1)
    
    http_code=$(echo "$response" | grep "HTTP_CODE" | cut -d: -f2)
    body=$(echo "$response" | sed '/HTTP_CODE/d')
    
    if [ "$http_code" == "200" ]; then
        echo -e "  ${GREEN}✅ Status: 200 OK${NC}"
        if [ -n "$body" ] && [ "$body" != "[]" ] && [ "$body" != "{}" ]; then
            echo -e "  ${GREEN}✅ Has data!${NC}"
            echo "  Response: $body"
        else
            echo -e "  ${YELLOW}⚠️  Empty response${NC}"
            echo "  Response: $body"
        fi
    else
        echo -e "  ${RED}❌ Status: $http_code${NC}"
        echo "  Response: $body"
    fi
    echo ""
}

# Test with account 15991599 (user's account)
ACCOUNT_ID=15991599

echo "=== Testing with Account 15991599 ==="
echo ""

# Test 1: Basic accountId
test_endpoint "Statistics/daily (basic)" \
  "/Statistics/daily" \
  "{\"accountId\":$ACCOUNT_ID}"

# Test 2: With tradingAccountId
test_endpoint "Statistics/daily (tradingAccountId)" \
  "/Statistics/daily" \
  "{\"tradingAccountId\":$ACCOUNT_ID}"

# Test 3: With date range (YYYY-MM-DD)
test_endpoint "Statistics/daily (date range YYYY-MM-DD)" \
  "/Statistics/daily" \
  "{\"accountId\":$ACCOUNT_ID,\"startDate\":\"2026-01-01\",\"endDate\":\"2026-01-15\"}"

# Test 4: With ISO datetime
test_endpoint "Statistics/daily (ISO datetime)" \
  "/Statistics/daily" \
  "{\"accountId\":$ACCOUNT_ID,\"startDate\":\"2026-01-15T00:00:00Z\",\"endDate\":\"2026-01-15T23:59:59Z\"}"

# Test 5: With single date
test_endpoint "Statistics/daily (single date)" \
  "/Statistics/daily" \
  "{\"accountId\":$ACCOUNT_ID,\"date\":\"2026-01-15\"}"

# Test 6: profitFactor
test_endpoint "Statistics/profitFactor" \
  "/Statistics/profitFactor" \
  "{\"accountId\":$ACCOUNT_ID}"

# Test 7: trades
test_endpoint "Statistics/trades" \
  "/Statistics/trades" \
  "{\"accountId\":$ACCOUNT_ID}"

# Test 8: monthly
test_endpoint "Statistics/monthly" \
  "/Statistics/monthly" \
  "{\"accountId\":$ACCOUNT_ID}"

# Test with accounts that have canTrade: true
echo "=== Testing with Accounts That Have canTrade: true ==="
echo ""

# Account 12694476 - PRAC-V2-14334-56363256
test_endpoint "Statistics/daily (account 12694476)" \
  "/Statistics/daily" \
  "{\"accountId\":12694476}"

# Account 16611204 - 150KTC-V2-14334-81127307
test_endpoint "Statistics/daily (account 16611204)" \
  "/Statistics/daily" \
  "{\"accountId\":16611204}"

echo "Testing complete!"
echo ""
echo "If all endpoints return empty arrays:"
echo "  1. The accounts may not have trades in the requested period"
echo "  2. The endpoints may need different parameters (check Swagger)"
echo "  3. The endpoints may require different authentication or permissions"
