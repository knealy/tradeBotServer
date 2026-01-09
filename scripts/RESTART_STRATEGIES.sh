#!/bin/bash
# Script to restart all strategy executor processes with fixed code

echo "🔄 Stopping old strategy executor processes..."
echo ""

# Kill the processes
pkill -f "strategy_executor.*overnight_range" && echo "✅ Stopped overnight_range" || echo "⚠️  overnight_range not running"
pkill -f "strategy_executor.*mean_reversion" && echo "✅ Stopped mean_reversion" || echo "⚠️  mean_reversion not running"
pkill -f "strategy_executor.*trend_following" && echo "✅ Stopped trend_following" || echo "⚠️  trend_following not running"

# Give processes time to exit
sleep 2

echo ""
echo "✅ All old processes stopped"
echo ""
echo "🚀 To restart with FIXED code, run these commands in separate terminals:"
echo ""
echo "Terminal 1 (overnight_range):"
echo "  caffeinate -dimsu python core/strategy_executor.py --account_select=1 --strategy=overnight_range --symbols=mnq,mes,mgc"
echo ""
echo "Terminal 2 (mean_reversion):"
echo "  caffeinate -dimsu python core/strategy_executor.py --account_select=1 --strategy=mean_reversion --symbols=mnq,mes,mgc"
echo ""
echo "Terminal 3 (trend_following):"
echo "  caffeinate -dimsu python core/strategy_executor.py --account_select=1 --strategy=trend_following --symbols=mnq,mes,mgc"
echo ""
echo "📋 Or copy/paste the restart_all_strategies.sh script below:"
echo ""
