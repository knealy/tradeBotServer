#!/bin/bash

# Test Case: Staged Exit Scenario to Prevent Unprompted Second Trades
# This simulates the exact scenario that was causing the -3 short position issue

# Configuration
WEBHOOK_URL="http://localhost:8000"  # Adjust to your webhook server URL
SYMBOL="MNQ"  # Using MNQ for testing (adjust as needed)

echo "=== Testing Staged Exit Scenario ==="
echo "This test simulates the problematic sequence that caused unprompted second trades"
echo ""

# Test 1: First Long Entry Signal (+1 contract)
echo "1. Sending first open_long signal (+1 contract)..."
curl -X POST "$WEBHOOK_URL" \
  -H "Content-Type: application/json" \
  -d '{
    "embeds": [
      {
        "title": "🚀 [MNQ1!] open long",
        "description": "**Strategy:** breakout\n**Time:** 2025-01-08 10:00:00",
        "color": 3066993,
        "url": "https://www.tradingview.com/chart/?symbol=MNQ1!&interval=5",
        "fields": [
          {
            "name": "🌿 Entry",
            "value": "25111.00 👉",
            "inline": true
          },
          {
            "name": "🛑 Stop",
            "value": "25090.00",
            "inline": true
          },
          {
            "name": "🥇 Target 1",
            "value": "25125.00",
            "inline": true
          },
          {
            "name": "🥈 Target 2",
            "value": "25140.00",
            "inline": true
          },
          {
            "name": "Chart Link",
            "value": "[Open Chart](https://www.tradingview.com/chart/?symbol=MNQ1!&interval=5)",
            "inline": false
          }
        ]
      }
    ]
  }'

echo -e "\n\nWaiting 5 seconds for first order to process..."
sleep 5

# Test 2: Second Long Entry Signal (+2 contracts) 
echo "2. Sending second open_long signal (+2 contracts)..."
curl -X POST "$WEBHOOK_URL" \
  -H "Content-Type: application/json" \
  -d '{
    "embeds": [
      {
        "title": "🚀 [MNQ1!] open long",
        "description": "**Strategy:** breakout\n**Time:** 2025-01-08 10:01:00",
        "color": 3066993,
        "url": "https://www.tradingview.com/chart/?symbol=MNQ1!&interval=5",
        "fields": [
          {
            "name": "🌿 Entry",
            "value": "25111.25 👉",
            "inline": true
          },
          {
            "name": "🛑 Stop",
            "value": "25090.25",
            "inline": true
          },
          {
            "name": "🥇 Target 1",
            "value": "25125.25",
            "inline": true
          },
          {
            "name": "🥈 Target 2",
            "value": "25140.25",
            "inline": true
          },
          {
            "name": "Chart Link",
            "value": "[Open Chart](https://www.tradingview.com/chart/?symbol=MNQ1!&interval=5)",
            "inline": false
          }
        ]
      }
    ]
  }'

echo -e "\n\nWaiting 5 seconds for second order to process..."
sleep 5

# Test 3: TP1 Hit Signal (should close 2 contracts, leave 1 with TP2 protection)
echo "3. Sending tp1_hit_long signal (should close 2 contracts)..."
curl -X POST "$WEBHOOK_URL" \
  -H "Content-Type: application/json" \
  -d '{
    "embeds": [
      {
        "title": "✂️📈 [MNQ1!] trim/close long",
        "description": "**Strategy:** breakout\n**Time:** 2025-01-08 10:05:00\n**PnL:** 💰 +14.25 points",
        "color": 16776960,
        "url": "https://www.tradingview.com/chart/?symbol=MNQ1!&interval=5",
        "fields": [
          {
            "name": "🌿 Entry",
            "value": "25111.12",
            "inline": true
          },
          {
            "name": "🛑 Stop",
            "value": "25090.12",
            "inline": true
          },
          {
            "name": "🥇 Target 1",
            "value": "25125.37 👉",
            "inline": true
          },
          {
            "name": "🥈 Target 2",
            "value": "25140.37",
            "inline": true
          },
          {
            "name": "Chart Link",
            "value": "[Open Chart](https://www.tradingview.com/chart/?symbol=MNQ1!&interval=5)",
            "inline": false
          }
        ]
      }
    ]
  }'

echo -e "\n\nWaiting 10 seconds for TP1 processing..."
sleep 10

# Test 4: Check for any unprompted trades (this should NOT happen)
echo "4. Checking for any unprompted second trades..."
echo "   If the fix is working, there should be NO unprompted -3 sell order"
echo "   The remaining +1 position should still have its TP2 protection"

echo -e "\n\n=== Test Summary ==="
echo "Expected behavior with the fix:"
echo "1. +1 @ 25111.00: Entry + SELL 1 with SL+TP2"
echo "2. +2 @ 25111.25: Entry + SELL 2 with SL+TP1" 
echo "3. -2 @ 25125.37: TP1 hit, closes 2 contracts"
echo "4. Remaining +1: Still has SELL 1 with SL+TP2 protection"
echo ""
echo "❌ OLD BEHAVIOR (BUG): Would create unprompted -3 sell at 25133.50"
echo "✅ NEW BEHAVIOR (FIXED): No unprompted trades, proper staged exits"
echo ""
echo "Check your trading logs and position monitor to verify:"
echo "- No orphaned positions"
echo "- No unprompted second trades" 
echo "- Remaining position has proper protection"
