#!/bin/bash
# Restart all strategies in background with fixed code
# This will start them as background processes with proper logging

cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "🚀 Starting all strategies with FIXED code..."
echo ""

# Start overnight_range
echo "Starting overnight_range..."
nohup caffeinate -dimsu python core/strategy_executor.py \
  --account_select=1 \
  --strategy=overnight_range \
  --symbols=mnq,mes,mgc \
  >> strategy_overnight_range.log 2>&1 &
OVERNIGHT_PID=$!
echo "  ✅ overnight_range started (PID: $OVERNIGHT_PID)"

# Start mean_reversion
echo "Starting mean_reversion..."
nohup caffeinate -dimsu python core/strategy_executor.py \
  --account_select=1 \
  --strategy=mean_reversion \
  --symbols=mnq,mes,mgc \
  >> strategy_mean_reversion.log 2>&1 &
MEAN_REV_PID=$!
echo "  ✅ mean_reversion started (PID: $MEAN_REV_PID)"

# Start trend_following
echo "Starting trend_following..."
nohup caffeinate -dimsu python core/strategy_executor.py \
  --account_select=1 \
  --strategy=trend_following \
  --symbols=mnq,mes,mgc \
  >> strategy_trend_following.log 2>&1 &
TREND_PID=$!
echo "  ✅ trend_following started (PID: $TREND_PID)"

echo ""
echo "✅ All strategies started with FIXED code!"
echo ""
echo "Monitor logs:"
echo "  tail -f strategy_overnight_range.log"
echo "  tail -f strategy_mean_reversion.log"
echo "  tail -f strategy_trend_following.log"
echo ""
echo "Check for 500 errors (should be GONE):"
echo "  grep '500' strategy_*.log"
