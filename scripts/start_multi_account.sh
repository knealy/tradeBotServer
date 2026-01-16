#!/bin/bash
# Start multiple trading bot instances, one per account
# Usage: ./scripts/start_multi_account.sh [account_id1] [account_id2] ...
# If no accounts provided, uses environment variable MULTI_ACCOUNT_IDS

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

# Get account IDs from args or env var
if [ $# -gt 0 ]; then
    ACCOUNT_IDS=("$@")
else
    # Read from environment variable (comma-separated)
    if [ -z "$MULTI_ACCOUNT_IDS" ]; then
        echo "❌ Error: No account IDs provided"
        echo "Usage: $0 [account_id1] [account_id2] ..."
        echo "   OR set MULTI_ACCOUNT_IDS environment variable (comma-separated)"
        exit 1
    fi
    IFS=',' read -ra ACCOUNT_IDS <<< "$MULTI_ACCOUNT_IDS"
fi

echo "🚀 Starting ${#ACCOUNT_IDS[@]} trading bot instance(s)..."

# Create logs directory if it doesn't exist
mkdir -p logs

# Check if tmux is available
if command -v tmux &> /dev/null; then
    echo "📺 Using tmux for multi-instance management"
    
    SESSION_NAME="multi_account_trading"
    
    # Kill existing session if it exists
    if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
        echo "⚠️  Session '$SESSION_NAME' already exists. Killing it..."
        tmux kill-session -t "$SESSION_NAME"
    fi
    
    # Create new tmux session
    tmux new-session -d -s "$SESSION_NAME" -n "monitor"
    
    # Create a window for each account
    for i in "${!ACCOUNT_IDS[@]}"; do
        ACCOUNT_ID="${ACCOUNT_IDS[$i]}"
        WINDOW_NAME="account_${ACCOUNT_ID}"
        
        # Create window with account-specific environment
        tmux new-window -t "$SESSION_NAME" -n "$WINDOW_NAME" \
            "cd '$PROJECT_ROOT' && \
             export TOPSTEPX_ACCOUNT_ID='$ACCOUNT_ID' && \
             export LOG_FILE='logs/trading_bot_${ACCOUNT_ID}.log' && \
             python trading_bot.py 2>&1 | tee 'logs/trading_bot_${ACCOUNT_ID}.log'"
        
        echo "✅ Started bot for account $ACCOUNT_ID (window: $WINDOW_NAME)"
        sleep 1  # Small delay between starts
    done
    
    # Switch to first account window
    tmux select-window -t "$SESSION_NAME:account_${ACCOUNT_IDS[0]}"
    
    echo ""
    echo "📋 Tmux session '$SESSION_NAME' created with ${#ACCOUNT_IDS[@]} windows"
    echo "💡 Commands:"
    echo "   tmux attach -t $SESSION_NAME    # Attach to session"
    echo "   tmux list-windows -t $SESSION_NAME  # List all windows"
    echo "   tmux kill-session -t $SESSION_NAME   # Stop all instances"
    echo ""
    echo "🔍 To view logs: tail -f logs/trading_bot_*.log"
    
    # Optionally attach
    if [ "${AUTO_ATTACH:-false}" = "true" ]; then
        tmux attach -t "$SESSION_NAME"
    else
        echo "💡 Run 'tmux attach -t $SESSION_NAME' to view the session"
    fi
    
else
    echo "⚠️  tmux not available. Starting processes in background..."
    echo "💡 Install tmux for better management: brew install tmux (macOS) or apt-get install tmux (Linux)"
    
    PIDS=()
    for ACCOUNT_ID in "${ACCOUNT_IDS[@]}"; do
        (
            export TOPSTEPX_ACCOUNT_ID="$ACCOUNT_ID"
            export LOG_FILE="logs/trading_bot_${ACCOUNT_ID}.log"
            python trading_bot.py > "logs/trading_bot_${ACCOUNT_ID}.log" 2>&1 &
            echo $! > "logs/trading_bot_${ACCOUNT_ID}.pid"
        )
        PID=$(cat "logs/trading_bot_${ACCOUNT_ID}.pid")
        PIDS+=($PID)
        echo "✅ Started bot for account $ACCOUNT_ID (PID: $PID)"
        sleep 2  # Delay between starts
    done
    
    echo ""
    echo "📋 Process PIDs:"
    for i in "${!ACCOUNT_IDS[@]}"; do
        echo "   Account ${ACCOUNT_IDS[$i]}: ${PIDS[$i]}"
    done
    echo ""
    echo "💡 To stop all processes: ./scripts/stop_multi_account.sh"
    echo "💡 Or manually: kill ${PIDS[*]}"
    echo "🔍 To view logs: tail -f logs/trading_bot_*.log"
fi
