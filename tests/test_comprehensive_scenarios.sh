#!/bin/bash

# Comprehensive Test Cases for Staged Exit Logic
# Tests various scenarios to ensure no unprompted trades occur

# Configuration
WEBHOOK_URL="https://tvwebhooks.up.railway.app"  # Adjust to your webhook server URL
SYMBOL="MNQ"

echo "=== Comprehensive Staged Exit Test Suite ==="
echo "Testing various scenarios to prevent unprompted second trades"
echo ""

# Function to send webhook
send_webhook() {
    local title="$1"
    local description="$2"
    local entry="$3"
    local stop="$4"
    local tp1="$5"
    local tp2="$6"
    local color="$7"
    
    curl -X POST "$WEBHOOK_URL" \
      -H "Content-Type: application/json" \
      -d "{
        \"embeds\": [
          {
            \"title\": \"$title\",
            \"description\": \"$description\",
            \"color\": $color,
            \"url\": \"https://www.tradingview.com/chart/?symbol=MNQ1!&interval=5\",
            \"fields\": [
              {
                \"name\": \"🌿 Entry\",
                \"value\": \"$entry\",
                \"inline\": true
              },
              {
                \"name\": \"🛑 Stop\",
                \"value\": \"$stop\",
                \"inline\": true
              },
              {
                \"name\": \"🥇 Target 1\",
                \"value\": \"$tp1\",
                \"inline\": true
              },
              {
                \"name\": \"🥈 Target 2\",
                \"value\": \"$tp2\",
                \"inline\": true
              },
              {
                \"name\": \"Chart Link\",
                \"value\": \"[Open Chart](https://www.tradingview.com/chart/?symbol=MNQ1!&interval=5)\",
                \"inline\": false
              }
            ]
          }
        ]
      }"
}

# Test Case 1: Original Problem Scenario
echo "=== TEST CASE 1: Original Problem Scenario ==="
echo "Simulating the exact sequence that caused the -3 short position issue"
echo ""

echo "1. First long entry (+1 contract)..."
send_webhook "🚀 [MNQ1!] open long" "**Strategy:** breakout\n**Time:** 2025-01-08 10:00:00" "25111.00 👉" "25090.00" "25125.00" "25140.00" 3066993
sleep 3

echo "2. Second long entry (+2 contracts)..."
send_webhook "🚀 [MNQ1!] open long" "**Strategy:** breakout\n**Time:** 2025-01-08 10:01:00" "25111.25 👉" "25090.25" "25125.25" "25140.25" 3066993
sleep 3

echo "3. TP1 hit (should close 2 contracts, leave 1 with TP2)..."
send_webhook "✂️📈 [MNQ1!] trim/close long" "**Strategy:** breakout\n**Time:** 2025-01-08 10:05:00\n**PnL:** 💰 +14.25 points" "25111.12" "25090.12" "25125.37 👉" "25140.37" 16776960
sleep 5

echo "✅ Expected: No unprompted -3 sell should occur"
echo "✅ Expected: Remaining +1 position should have TP2 protection"
echo ""

# Test Case 2: Full TP1 Exit Scenario
echo "=== TEST CASE 2: Full TP1 Exit Scenario ==="
echo "Testing when close_entire_at_tp1=true"
echo ""

echo "1. Long entry with full TP1 exit..."
send_webhook "🚀 [MNQ1!] open long" "**Strategy:** breakout\n**Time:** 2025-01-08 10:10:00" "25120.00 👉" "25099.00" "25135.00" "25150.00" 3066993
sleep 3

echo "2. TP1 hit (should close entire position)..."
send_webhook "✂️📈 [MNQ1!] trim/close long" "**Strategy:** breakout\n**Time:** 2025-01-08 10:12:00\n**PnL:** 💰 +15.00 points" "25120.00" "25099.00" "25135.00 👉" "25150.00" 16776960
sleep 5

echo "✅ Expected: Entire position closed, no remaining contracts"
echo ""

# Test Case 3: Short Position Scenario
echo "=== TEST CASE 3: Short Position Scenario ==="
echo "Testing staged exits for short positions"
echo ""

echo "1. Short entry..."
send_webhook "🔻 [MNQ1!] open short" "**Strategy:** breakdown\n**Time:** 2025-01-08 10:15:00" "25130.00 👇" "25151.00" "25105.00" "25090.00" 15158332
sleep 3

echo "2. TP1 hit for short (should close 2 contracts, leave 1 with TP2)..."
send_webhook "✂️📉 [MNQ1!] trim/close short" "**Strategy:** breakdown\n**Time:** 2025-01-08 10:17:00\n**PnL:** 💰 +25.00 points" "25130.00" "25151.00" "25105.00 👇" "25090.00" 16776960
sleep 5

echo "✅ Expected: No unprompted +3 buy should occur"
echo "✅ Expected: Remaining -1 position should have TP2 protection"
echo ""

# Test Case 4: Multiple Rapid Signals (Debounce Test)
echo "=== TEST CASE 4: Debounce Test ==="
echo "Testing rapid duplicate signals should be debounced"
echo ""

echo "1. First signal..."
send_webhook "🚀 [MNQ1!] open long" "**Strategy:** breakout\n**Time:** 2025-01-08 10:20:00" "25140.00 👉" "25119.00" "25155.00" "25170.00" 3066993
sleep 1

echo "2. Duplicate signal (should be debounced)..."
send_webhook "🚀 [MNQ1!] open long" "**Strategy:** breakout\n**Time:** 2025-01-08 10:20:01" "25140.00 👉" "25119.00" "25155.00" "25170.00" 3066993
sleep 1

echo "3. Another duplicate (should be debounced)..."
send_webhook "🚀 [MNQ1!] open long" "**Strategy:** breakout\n**Time:** 2025-01-08 10:20:02" "25140.00 👉" "25119.00" "25155.00" "25170.00" 3066993
sleep 3

echo "✅ Expected: Only first signal should execute, duplicates should be debounced"
echo ""

# Test Case 5: Stop Loss Scenario
echo "=== TEST CASE 5: Stop Loss Scenario ==="
echo "Testing stop loss execution"
echo ""

echo "1. Long entry..."
send_webhook "🚀 [MNQ1!] open long" "**Strategy:** breakout\n**Time:** 2025-01-08 10:25:00" "25150.00 👉" "25129.00" "25175.00" "25190.00" 3066993
sleep 3

echo "2. Stop loss hit (should close entire position)..."
send_webhook "🛑 [MNQ1!] stop hit long" "**Strategy:** breakout\n**Time:** 2025-01-08 10:27:00\n**PnL:** 💰 -21.00 points" "25150.00" "25129.00 👇" "25175.00" "25190.00" 15158332
sleep 5

echo "✅ Expected: Entire position closed at stop loss"
echo ""

echo "=== Test Suite Complete ==="
echo ""
echo "🔍 VERIFICATION CHECKLIST:"
echo "1. Check trading logs for proper order creation"
echo "2. Verify no unprompted second trades occur"
echo "3. Confirm remaining positions have protection"
echo "4. Verify debounce logic works for duplicate signals"
echo "5. Check that stop losses work correctly"
echo ""
echo "📊 EXPECTED LOG PATTERNS:"
echo "- 'Staged exit setup: Entry X, TP1 exit Y, TP2 exit Z'"
echo "- 'Entry order placed: {...}'"
echo "- 'TP1 exit bracket created: SELL Y @ TP1'"
echo "- 'TP2 exit bracket created: SELL Z @ TP2'"
echo "- 'Debounced duplicate open_long for SYMBOL'"
echo ""
echo "❌ RED FLAGS TO WATCH FOR:"
echo "- Multiple entry orders for same signal"
echo "- Unprompted second trades after TP1 hits"
echo "- Positions without stop/target protection"
echo "- Failed bracket order creation"
