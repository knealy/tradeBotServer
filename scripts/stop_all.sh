#!/bin/bash
# Stop all trading bot processes

set -e

echo "🛑 Stopping all trading bot processes..."

# Stop tmux session if it exists
if command -v tmux &> /dev/null; then
    if tmux has-session -t trading 2>/dev/null; then
        echo "📺 Stopping tmux session 'trading'..."
        tmux kill-session -t trading
        echo "✅ Tmux session stopped"
    else
        echo "ℹ️  No tmux session 'trading' found"
    fi
fi

# Kill processes by name (fallback)
echo "🔍 Searching for running bot processes..."
pkill -f "trading_bot.py" && echo "✅ Stopped trading_bot.py" || echo "ℹ️  No trading_bot.py process found"
pkill -f "strategy_executor.py" && echo "✅ Stopped strategy_executor.py" || echo "ℹ️  No strategy_executor.py process found"

echo ""
echo "✅ All processes stopped"
