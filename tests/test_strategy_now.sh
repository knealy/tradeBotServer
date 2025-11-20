#!/bin/bash
# Quick test script to run overnight strategy RIGHT NOW
# Usage: ./test_strategy_now.sh

echo "🧪 Setting up test configuration for immediate execution..."
echo ""

# Set test times (7:00 PM to 7:55 PM, execute at 7:55 PM)
export OVERNIGHT_START_TIME="19:00"
export OVERNIGHT_END_TIME="19:55"
export MARKET_OPEN_TIME="19:55"
export MARKET_OPEN_GRACE_MINUTES="5"
export OVERNIGHT_RANGE_SYMBOLS="MNQ"
export OVERNIGHT_RANGE_POSITION_SIZE="1"

echo "✅ Test configuration:"
echo "   Overnight Range: $OVERNIGHT_START_TIME - $OVERNIGHT_END_TIME"
echo "   Market Open: $MARKET_OPEN_TIME"
echo "   Symbols: $OVERNIGHT_RANGE_SYMBOLS"
echo "   Position Size: $OVERNIGHT_RANGE_POSITION_SIZE"
echo ""
echo "📝 Note: You'll need to restart the strategy in the bot CLI:"
echo "   > strategies stop overnight_range"
echo "   > strategies start overnight_range MNQ"
echo ""
echo "Or restart the entire bot to pick up these env vars."

