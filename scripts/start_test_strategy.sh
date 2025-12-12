#!/bin/bash
# Start Multi-Window Test Strategy Setup
# 
# This script orchestrates a complete trading setup with:
# 1. Strategy runner window
# 2. Position monitor window  
# 3. Real-time chart in browser
# 4. Account info display
#
# Usage: ./scripts/start_test_strategy.sh [account_index] [symbol]

set -e

# Configuration
ACCOUNT_INDEX=${1:-1}
SYMBOL=${2:-MNQ}
TIMEFRAME=${3:-5m}
CHART_BARS=${4:-300}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$PROJECT_DIR"

echo "🚀 Starting Multi-Window Test Strategy Setup"
echo "=============================================="
echo "Account: $ACCOUNT_INDEX"
echo "Symbol: $SYMBOL"
echo "Timeframe: $TIMEFRAME"
echo "Chart Bars: $CHART_BARS"
echo ""

# Check if we're on macOS (for osascript) or Linux
if [[ "$OSTYPE" == "darwin"* ]]; then
    USE_OSASCRIPT=true
elif command -v gnome-terminal &> /dev/null; then
    USE_GNOME_TERMINAL=true
elif command -v xterm &> /dev/null; then
    USE_XTERM=true
elif command -v tmux &> /dev/null; then
    USE_TMUX=true
else
    echo "⚠️  No suitable terminal multiplexer found"
    echo "💡 Install tmux for better multi-window management:"
    echo "   macOS: brew install tmux"
    echo "   Linux: sudo apt-get install tmux"
    USE_TMUX=false
fi

# Function to start strategy in a new window
start_strategy() {
    local account=$1
    local symbol=$2
    
    if [ "$USE_OSASCRIPT" = true ]; then
        # macOS - use osascript to open new iTerm2 windows
        osascript <<EOF
tell application "iTerm2"
    activate
    set newWindow to (create window with default profile)
    tell current session of newWindow
        write text "cd '$PROJECT_DIR' && venv/bin/python3 -c \"
import asyncio
import sys
sys.path.insert(0, '.')
from trading_bot import TopStepXTradingBot
from strategies.simple_candle_strategy import SimpleCandleStrategy

async def run_strategy():
    bot = TopStepXTradingBot()
    if not await bot._ensure_valid_token():
        print('❌ Authentication failed')
        return
    
    accounts = await bot.list_accounts()
    if not accounts:
        print('❌ No accounts found')
        return
    
    # Select account
    try:
        idx = int('$account') - 1
        if 0 <= idx < len(accounts):
            bot.selected_account = accounts[idx]
            account_name = bot.selected_account.get('name', 'Unknown')
            print(f'✅ Selected account: {account_name}')
        else:
            print(f'❌ Invalid account index: $account')
            return
    except Exception as e:
        import traceback
        print(f'❌ Error selecting account: {e}')
        print(traceback.format_exc())
        return
    
    # Fetch contracts
    await bot.get_available_contracts(use_cache=True)
    
    # Create and run strategy
    strategy = SimpleCandleStrategy(bot)
    strategy.config.symbols = ['$symbol']
    await strategy.run()

asyncio.run(run_strategy())
\""
    end tell
end tell
EOF
    elif [ "$USE_GNOME_TERMINAL" = true ]; then
        gnome-terminal --title="Strategy Runner - $symbol" -- bash -c "cd '$PROJECT_DIR' && venv/bin/python3 -c \"
import asyncio
import sys
sys.path.insert(0, '.')
from trading_bot import TopStepXTradingBot
from strategies.simple_candle_strategy import SimpleCandleStrategy

async def run_strategy():
    bot = TopStepXTradingBot()
    if not await bot._ensure_valid_token():
        print('❌ Authentication failed')
        return
    
    accounts = await bot.list_accounts()
    if not accounts:
        print('❌ No accounts found')
        return
    
    try:
        idx = int('$account') - 1
        if 0 <= idx < len(accounts):
            bot.selected_account = accounts[idx]
            account_name = bot.selected_account.get('name', 'Unknown')
            print(f'✅ Selected account: {account_name}')
        else:
            print(f'❌ Invalid account index: $account')
            return
    except Exception as e:
        import traceback
        print(f'❌ Error selecting account: {e}')
        print(traceback.format_exc())
        return
    
    await bot.get_available_contracts(use_cache=True)
    strategy = SimpleCandleStrategy(bot)
    strategy.config.symbols = ['$symbol']
    await strategy.run()

asyncio.run(run_strategy())
\""
    elif [ "$USE_TMUX" = true ]; then
        # Use tmux
        if ! tmux has-session -t trading_test 2>/dev/null; then
            tmux new-session -d -s trading_test -n strategy
        fi
        tmux send-keys -t trading_test:strategy "cd '$PROJECT_DIR' && venv/bin/python3 -c \"
import asyncio
import sys
sys.path.insert(0, '.')
from trading_bot import TopStepXTradingBot
from strategies.simple_candle_strategy import SimpleCandleStrategy

async def run_strategy():
    bot = TopStepXTradingBot()
    if not await bot._ensure_valid_token():
        print('❌ Authentication failed')
        return
    
    accounts = await bot.list_accounts()
    if not accounts:
        print('❌ No accounts found')
        return
    
    try:
        idx = int('$account') - 1
        if 0 <= idx < len(accounts):
            bot.selected_account = accounts[idx]
            account_name = bot.selected_account.get('name', 'Unknown')
            print(f'✅ Selected account: {account_name}')
        else:
            print(f'❌ Invalid account index: $account')
            return
    except Exception as e:
        import traceback
        print(f'❌ Error selecting account: {e}')
        print(traceback.format_exc())
        return
    
    await bot.get_available_contracts(use_cache=True)
    strategy = SimpleCandleStrategy(bot)
    strategy.config.symbols = ['$symbol']
    await strategy.run()

asyncio.run(run_strategy())
\"" C-m
    else
        echo "⚠️  Cannot open new window. Run strategy manually:"
        echo "   python3 -c \"...\""
    fi
}

# Function to start position monitor
start_monitor() {
    local account=$1
    
    if [ "$USE_OSASCRIPT" = true ]; then
        osascript <<EOF
tell application "iTerm2"
    activate
    set newWindow to (create window with default profile)
    tell current session of newWindow
        write text "cd '$PROJECT_DIR' && venv/bin/python3 scripts/monitor_positions.py --account $account --interval 5"
    end tell
end tell
EOF
    elif [ "$USE_GNOME_TERMINAL" = true ]; then
        gnome-terminal --title="Position Monitor" -- bash -c "cd '$PROJECT_DIR' && venv/bin/python3 scripts/monitor_positions.py --account $account --interval 5; exec bash"
    elif [ "$USE_TMUX" = true ]; then
        if ! tmux has-session -t trading_test 2>/dev/null; then
            tmux new-session -d -s trading_test
        fi
        tmux new-window -t trading_test -n monitor "cd '$PROJECT_DIR' && venv/bin/python3 scripts/monitor_positions.py --account $account --interval 5"
    fi
}

# Function to open chart
open_chart() {
    local account=$1
    local symbol=$2
    local timeframe=$3
    local bars=$4
    
    echo "📊 Opening real-time chart..."
    
    # Start chart in background and keep bot running
    if [ "$USE_OSASCRIPT" = true ]; then
        osascript <<EOF
tell application "iTerm2"
    activate
    set newWindow to (create window with default profile)
    tell current session of newWindow
        write text "cd '$PROJECT_DIR' && venv/bin/python3 trading_bot.py --account_select=$account --command='chart $symbol $timeframe $bars --realtime'"
    end tell
end tell
EOF
    elif [ "$USE_GNOME_TERMINAL" = true ]; then
        gnome-terminal --title="Chart Server" -- bash -c "cd '$PROJECT_DIR' && venv/bin/python3 trading_bot.py --account_select=$account --command='chart $symbol $timeframe $bars --realtime'; exec bash"
    elif [ "$USE_TMUX" = true ]; then
        if ! tmux has-session -t trading_test 2>/dev/null; then
            tmux new-session -d -s trading_test
        fi
        tmux new-window -t trading_test -n chart "cd '$PROJECT_DIR' && venv/bin/python3 trading_bot.py --account_select=$account --command='chart $symbol $timeframe $bars --realtime'"
    else
        # Fallback: run in background
        cd "$PROJECT_DIR"
        venv/bin/python3 trading_bot.py --account_select=$account --command="chart $symbol $timeframe $bars --realtime" &
        echo "📊 Chart server started in background (PID: $!)"
    fi
    
    # Wait a moment for chart to open
    sleep 3
}

# Main execution
echo "📋 Step 1: Starting strategy runner..."
start_strategy "$ACCOUNT_INDEX" "$SYMBOL"
sleep 2

echo "📋 Step 2: Starting position monitor..."
start_monitor "$ACCOUNT_INDEX"
sleep 2

echo "📋 Step 3: Opening real-time chart..."
open_chart "$ACCOUNT_INDEX" "$SYMBOL" "$TIMEFRAME" "$CHART_BARS"

echo ""
echo "✅ All windows started!"
echo ""
echo "📺 Windows opened:"
echo "   1. Strategy Runner - Running Simple Momentum Strategy"
echo "   2. Position Monitor - Real-time position/order tracking"
echo "   3. Chart Server - Real-time chart in browser"
echo ""
if [ "$USE_TMUX" = true ]; then
    echo "💡 To view all windows, run: tmux attach -t trading_test"
    echo "💡 To switch windows: Ctrl+B then window number (0, 1, 2)"
    echo "💡 To detach: Ctrl+B then D"
else
    echo "💡 Each window is running independently"
    echo "💡 Close windows individually or use Ctrl+C to stop processes"
fi
echo ""
echo "⏰ Strategy will run for 2 hours"
echo "🛑 Press Ctrl+C in strategy window to stop early"
