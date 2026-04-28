# Multi-Window Trading Setup

**Ops / lifecycle:** prefer [docs/PLAYBOOK.md](../docs/PLAYBOOK.md) and [docs/HANDOFF.md](../docs/HANDOFF.md) for production start/stop. This file is a dev-oriented multi-window layout.

This guide explains how to use the multi-window trading setup for testing strategies with real-time monitoring.

## Overview

The multi-window setup provides:
1. **Strategy Runner** - Runs your trading strategy
2. **Position Monitor** - Real-time position and order tracking
3. **Real-Time Chart** - Browser-based chart with live updates
4. **Orchestration Script** - Starts everything with one command

## Quick Start

### Start Everything at Once

```bash
./scripts/start_test_strategy.sh [account_index] [symbol] [timeframe] [bars]
```

**Examples:**
```bash
# Default: Account 1, MNQ, 5m, 300 bars
./scripts/start_test_strategy.sh

# Custom account and symbol
./scripts/start_test_strategy.sh 2 MES 1m 500

# Full customization
./scripts/start_test_strategy.sh 1 MNQ 5m 300
```

### What Gets Started

1. **Strategy Runner Window**
   - Runs `SimpleMomentumStrategy` for 2 hours
   - Monitors market and places trades automatically
   - Shows strategy logs and trade execution

2. **Position Monitor Window**
   - Updates every 5 seconds
   - Shows:
     - Account balance and P&L
     - Open positions with entry/current prices
     - Open orders
     - Compliance status (DLL, MLL, Drawdown)

3. **Chart Server Window**
   - Starts chart server in background
   - Opens browser with real-time chart
   - Chart updates automatically with live quotes
   - Can place orders directly from chart

## Manual Setup

If you prefer to start components individually:

### 1. Start Strategy

```bash
python -c "
import asyncio
from trading_bot import TopStepXTradingBot
from strategies.simple_momentum_strategy import SimpleMomentumStrategy

async def run():
    bot = TopStepXTradingBot()
    await bot._ensure_valid_token()
    accounts = await bot.list_accounts()
    bot.selected_account = accounts[0]  # or accounts[1] for second account
    await bot.get_available_contracts()
    
    strategy = SimpleMomentumStrategy(bot)
    strategy.config.symbols = ['MNQ']
    await strategy.run()

asyncio.run(run())
"
```

### 2. Start Position Monitor

```bash
python scripts/monitor_positions.py --account 1 --interval 5
```

### 3. Open Chart

```bash
python trading_bot.py --account_select=1 --command='chart MNQ 5m 300 --realtime'
```

## Simple Momentum Strategy

The test strategy (`SimpleMomentumStrategy`) is designed to:
- Trade actively over a 2-hour window
- Use momentum signals (price breakouts)
- Place bracket orders with 10-tick profit targets
- Stop loss at 15 ticks
- Maximum 2 concurrent positions
- Up to 20 trades per day

### Strategy Logic

- **LONG Signal**: Price breaks above recent high with volume
- **SHORT Signal**: Price breaks below recent low with volume
- **Exit**: Bracket orders handle exits automatically
- **Time Window**: Runs for exactly 2 hours from start

### Configuration

You can modify strategy behavior by editing `strategies/simple_momentum_strategy.py`:
- `lookback_bars`: Number of bars to analyze (default: 10)
- `profit_ticks`: Profit target in ticks (default: 10)
- `stop_ticks`: Stop loss in ticks (default: 15)
- `min_volume_ratio`: Volume threshold (default: 1.2x average)

## Position Monitor

The position monitor (`scripts/monitor_positions.py`) displays:

```
📊 POSITION MONITOR - 2025-12-12 14:30:00
================================================================================
Account: PRAC-V2-14334-56363256 (ID: 12694476)

💰 Balance: $158,196.01
📈 Unrealized P&L: $12.50
💵 Realized P&L: $4.25

📦 OPEN POSITIONS (1):
--------------------------------------------------------------------------------
Symbol   Side    Qty    Entry        Current      P&L          P&L %
--------------------------------------------------------------------------------
MNQ      LONG    1      $25330.75    $25333.25    🟢 $2.50     0.10%

📋 OPEN ORDERS (2):
--------------------------------------------------------------------------------
Order ID     Symbol   Side    Qty    Price        Type         Status
--------------------------------------------------------------------------------
12345678     MNQ      SELL    1      $25340.75    LIMIT        PENDING
12345679     MNQ      SELL    1      $25320.75    STOP         PENDING

⚠️  COMPLIANCE STATUS:
--------------------------------------------------------------------------------
DLL: ✅ 1250.00 / 2000.00 (62.5%)
MLL: ✅ 850.00 / 1500.00 (56.7%)
Drawdown: ✅ 150.00 / 500.00 (30.0%)
```

### Monitor Options

```bash
# Monitor specific account
python scripts/monitor_positions.py --account 2

# Change refresh interval (default: 5 seconds)
python scripts/monitor_positions.py --account 1 --interval 10
```

## Real-Time Chart

The chart opens in your default browser and provides:
- Live candlestick updates
- Position and order visualization
- Trading interface (place orders from chart)
- Symbol switching
- Timeframe selection

### Chart Features

- **Real-time Updates**: Chart updates every second when enabled
- **Position Lines**: Shows entry, stop loss, and take profit levels
- **Order Placement**: Place market, limit, stop, and bracket orders
- **Symbol Dropdown**: Switch between available contracts
- **Timeframe Selection**: Change chart timeframe

## Terminal Multiplexers

The script supports multiple terminal environments:

### macOS
- Uses `osascript` to open Terminal windows
- Each component gets its own window
- Windows are titled for easy identification

### Linux (GNOME)
- Uses `gnome-terminal` to open windows
- Similar to macOS experience

### tmux (All Platforms)
- Creates a tmux session with multiple panes
- View all windows: `tmux attach -t trading_test`
- Switch panes: `Ctrl+B` then arrow keys
- Detach: `Ctrl+B` then `D`

### Fallback
- If no multiplexer is available, processes run in background
- Use `ps` to find PIDs and `kill` to stop

## Stopping Everything

### Individual Windows
- Press `Ctrl+C` in each window to stop that component

### All at Once (tmux)
```bash
tmux kill-session -t trading_test
```

### Manual Cleanup
```bash
# Find processes
ps aux | grep "trading_bot\|monitor_positions\|simple_momentum"

# Kill by PID
kill <PID1> <PID2> <PID3>
```

## Troubleshooting

### Chart Server Connection Refused
- Make sure the chart server window is still running
- Check that the port isn't blocked by firewall
- Try refreshing the browser

### Strategy Not Trading
- Check account balance and compliance limits
- Verify market is open
- Check strategy logs for error messages
- Ensure contracts are loaded (should happen automatically)

### Position Monitor Not Updating
- Verify account selection is correct
- Check authentication (should auto-refresh)
- Look for errors in the monitor window

### Multiple Windows Not Opening
- Install tmux: `brew install tmux` (macOS) or `sudo apt-get install tmux` (Linux)
- Or use the fallback mode (background processes)

## Next Steps

1. **Customize Strategy**: Edit `strategies/simple_momentum_strategy.py` to modify trading logic
2. **Add More Windows**: Extend `start_test_strategy.sh` to add account info, trade history, etc.
3. **Create GUI**: Build a master GUI using Tkinter, PyQt, or web-based dashboard
4. **Add Alerts**: Integrate Discord/Slack notifications for trades
5. **Backtesting**: Use the chart's backtest mode to test strategies on historical data

## Architecture

```
┌─────────────────┐
│  Orchestration  │
│     Script      │
└────────┬────────┘
         │
         ├───► Strategy Runner (SimpleMomentumStrategy)
         │
         ├───► Position Monitor (monitor_positions.py)
         │
         └───► Chart Server (trading_bot.py --command='chart ...')
                  │
                  └───► Browser (Real-time Chart)
```

All components share the same:
- Trading bot instance (separate processes)
- Account selection
- Authentication
- Contract cache
