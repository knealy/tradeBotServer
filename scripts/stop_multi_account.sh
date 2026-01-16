#!/bin/bash
# Stop all multi-account trading bot instances

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

SESSION_NAME="multi_account_trading"

# Try tmux first
if command -v tmux &> /dev/null; then
    if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
        echo "🛑 Stopping tmux session '$SESSION_NAME'..."
        tmux kill-session -t "$SESSION_NAME"
        echo "✅ Session stopped"
    else
        echo "ℹ️  No tmux session found"
    fi
fi

# Also kill any processes by PID files
if [ -d "logs" ]; then
    for PID_FILE in logs/trading_bot_*.pid; do
        if [ -f "$PID_FILE" ]; then
            PID=$(cat "$PID_FILE")
            if ps -p "$PID" > /dev/null 2>&1; then
                echo "🛑 Stopping process $PID from $PID_FILE..."
                kill "$PID" 2>/dev/null || true
                rm "$PID_FILE"
            fi
        fi
    done
fi

# Kill any remaining python trading_bot.py processes (be careful with this)
echo "🔍 Checking for remaining trading_bot.py processes..."
REMAINING=$(ps aux | grep "[p]ython.*trading_bot.py" | wc -l | tr -d ' ')
if [ "$REMAINING" -gt 0 ]; then
    echo "⚠️  Found $REMAINING remaining trading_bot.py process(es)"
    echo "💡 To kill manually: pkill -f 'python.*trading_bot.py'"
else
    echo "✅ All processes stopped"
fi
