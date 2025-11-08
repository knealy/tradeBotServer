#!/bin/bash

# Simple Curl Test for the Original Problem Scenario
# This tests the exact sequence that caused the unprompted -3 sell

WEBHOOK_URL="https://tvwebhooks.up.railway.app"  # Change to your webhook URL

echo "Testing the original problem scenario that caused unprompted second trades..."
echo ""

# Test 1: First Long Entry (+1)
echo "1. First long entry signal..."
curl -X POST "$WEBHOOK_URL" \
  -H "Content-Type: application/json" \
  -d '{
    "embeds": [{
      "title": "🚀 [MNQ1!] open long",
      "description": "**Strategy:** breakout\n**Time:** 2025-01-08 10:00:00",
      "color": 3066993,
      "fields": [
        {"name": "🌿 Entry", "value": "25111.00 👉", "inline": true},
        {"name": "🛑 Stop", "value": "25090.00", "inline": true},
        {"name": "🥇 Target 1", "value": "25125.00", "inline": true},
        {"name": "🥈 Target 2", "value": "25140.00", "inline": true}
      ]
    }]
  }'

echo -e "\nWaiting 3 seconds..."
sleep 3

# Test 2: Second Long Entry (+2) 
echo "2. Second long entry signal..."
curl -X POST "$WEBHOOK_URL" \
  -H "Content-Type: application/json" \
  -d '{
    "embeds": [{
      "title": "🚀 [MNQ1!] open long", 
      "description": "**Strategy:** breakout\n**Time:** 2025-01-08 10:01:00",
      "color": 3066993,
      "fields": [
        {"name": "🌿 Entry", "value": "25111.25 👉", "inline": true},
        {"name": "🛑 Stop", "value": "25090.25", "inline": true},
        {"name": "🥇 Target 1", "value": "25125.25", "inline": true},
        {"name": "🥈 Target 2", "value": "25140.25", "inline": true}
      ]
    }]
  }'

echo -e "\nWaiting 3 seconds..."
sleep 3

# Test 3: TP1 Hit (should close 2, leave 1 with TP2)
echo "3. TP1 hit signal..."
curl -X POST "$WEBHOOK_URL" \
  -H "Content-Type: application/json" \
  -d '{
    "embeds": [{
      "title": "✂️📈 [MNQ1!] trim/close long",
      "description": "**Strategy:** breakout\n**Time:** 2025-01-08 10:05:00\n**PnL:** 💰 +14.25 points",
      "color": 16776960,
      "fields": [
        {"name": "🌿 Entry", "value": "25111.12", "inline": true},
        {"name": "🛑 Stop", "value": "25090.12", "inline": true},
        {"name": "🥇 Target 1", "value": "25125.37 👉", "inline": true},
        {"name": "🥈 Target 2", "value": "25140.37", "inline": true}
      ]
    }]
  }'

echo -e "\n\n=== EXPECTED BEHAVIOR (FIXED) ==="
echo "✅ Entry: BUY 3 contracts"
echo "✅ TP1 Exit: SELL 2 contracts with SL+TP1"  
echo "✅ TP2 Exit: SELL 1 contract with SL+TP2"
echo "✅ TP1 Hit: Closes 2 contracts, leaves 1 with TP2 protection"
echo "❌ NO unprompted second -3 sell should occur"
echo ""
echo "Check your logs for:"
echo "- 'Staged exit setup: Entry 3, TP1 exit 2, TP2 exit 1'"
echo "- 'Entry order placed: {...}'"
echo "- 'TP1 exit bracket created: SELL 2 @ 25125.25'"
echo "- 'TP2 exit bracket created: SELL 1 @ 25140.25'"
