#!/bin/bash
# Quick test script for JWT + SignalR integration

echo "🧪 Testing JWT Token + SignalR Market Hub Integration"
echo "========================================================"
echo ""

# Check if JWT_TOKEN is set in .env
if grep -q "^JWT_TOKEN=" .env 2>/dev/null; then
    echo "✅ JWT_TOKEN found in .env"
    
    # Extract and validate JWT expiration
    JWT=$(grep "^JWT_TOKEN=" .env | cut -d'"' -f2)
    
    # Decode JWT payload (base64 decode the middle section)
    PAYLOAD=$(echo "$JWT" | cut -d'.' -f2)
    # Add padding if needed
    PADDED=$(printf '%s' "$PAYLOAD" | sed 's/-/+/g; s/_/\//g')
    while [ $((${#PADDED} % 4)) -ne 0 ]; do
        PADDED="${PADDED}="
    done
    
    # Decode and extract expiration
    EXP=$(echo "$PADDED" | base64 -d 2>/dev/null | grep -o '"exp":[0-9]*' | cut -d':' -f2)
    
    if [ -n "$EXP" ]; then
        NOW=$(date +%s)
        REMAINING=$((EXP - NOW))
        
        if [ $REMAINING -gt 0 ]; then
            DAYS=$((REMAINING / 86400))
            HOURS=$(( (REMAINING % 86400) / 3600 ))
            echo "✅ JWT is valid (expires in ${DAYS}d ${HOURS}h)"
        else
            echo "❌ JWT is EXPIRED! Please update JWT_TOKEN in .env"
            exit 1
        fi
    else
        echo "⚠️  Could not parse JWT expiration (but token exists)"
    fi
else
    echo "⚠️  JWT_TOKEN not found in .env"
    echo "   Will authenticate using PROJECT_X_USERNAME and PROJECT_X_API_KEY"
fi

echo ""
echo "🚀 Starting bot to test real-time quotes..."
echo "========================================================"
echo ""
echo "Commands to test:"
echo "  1. Type 'quote mnq' to test SignalR real-time quotes"
echo "  2. Look for log messages:"
echo "     - 'Loaded JWT from environment'"
echo "     - 'Subscribed to GatewayQuote events'"
echo "     - '📶 Raw quote event'"
echo "  3. Verify quote has 'source: signalr' (not 'bars')"
echo ""
echo "Starting bot in 3 seconds..."
sleep 3

# Start the bot
venv/bin/python trading_bot.py

