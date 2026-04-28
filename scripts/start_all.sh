#!/bin/bash
# Start all trading bot processes in separate terminal windows/tmux panes

set -e

echo "🚀 Starting all trading bot processes..."

# Check if tmux is available
if command -v tmux &> /dev/null; then
    echo "📺 Using tmux for multi-window management"
    
    # Create new tmux session or attach to existing
    if tmux has-session -t trading 2>/dev/null; then
        echo "⚠️  Trading session already exists. Attaching..."
        tmux attach -t trading
    else
        # Create new session with trading_bot.py
        tmux new-session -d -s trading -n main "python trading_bot.py"
        
        # Split window for strategy executor
        tmux split-window -h -t trading:main "python core/strategy_executor.py --all"
        
        # Attach to session
        tmux attach -t trading
    fi
else
    echo "⚠️  tmux not available. Starting processes in background..."
    echo "💡 Install tmux for better multi-window management: brew install tmux"
    
    # Start processes in background
    python trading_bot.py &
    TRADING_BOT_PID=$!
    echo "✅ Trading bot started (PID: $TRADING_BOT_PID)"
    
    python core/strategy_executor.py --all &
    EXECUTOR_PID=$!
    echo "✅ Strategy executor started (PID: $EXECUTOR_PID)"
    
    echo ""
    echo "📋 Process PIDs:"
    echo "   Trading Bot: $TRADING_BOT_PID"
    echo "   Strategy Executor: $EXECUTOR_PID"
    echo ""
    echo "💡 To stop all processes, run: ./scripts/stop_all.sh"
    echo "💡 Or manually: kill $TRADING_BOT_PID $EXECUTOR_PID"
fi
