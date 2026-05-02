# Quick Start Guide

Get up and running with the Trading Bot in 5 minutes.

---

## Prerequisites

- ✅ Python 3.11+
- ✅ TopStepX account (practice, eval, or funded)
- ✅ TopStepX API credentials (email/password)
- ✅ Virtual environment (recommended)

---

## Step 1: Installation

### Clone and Setup

```bash
# Clone the repository
cd /path/to/tradeBotServer

# Create and activate virtual environment
python3 -m venv venv
source venv/bin/activate  # On macOS/Linux
# OR
venv\Scripts\activate  # On Windows

# Install dependencies
pip install -r requirements.txt
```

### Configure Environment

Create `.env` file in the project root:

```bash
# Required
TOPSTEPX_EMAIL=your_email@example.com
TOPSTEPX_PASSWORD=your_password

# Optional (will be auto-generated if not provided)
TOPSTEPX_SESSION_TOKEN=

# Discord notifications (optional)
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
```

---

## Step 2: First Run (CLI Mode)

### Start the Bot

```bash
python trading_bot.py
```

You'll see:

```
TopStepX Trading Bot - Real API Version
========================================

✅ Authentication successful! (195 ms)

ACTIVE ACCOUNTS
================================================================================
# Account Name              ID        Status  Balance      Type
--------------------------------------------------------------------------------
1 PRAC-V2-14334-56363256   12694476  active  $157,688.19  practice
2 150KTC-V2-14334-23803636 15020996  active  $149,885.86  eval
...
```

### Select an Account

```bash
Enter account selection (or press Enter for account 1): 1
```

### Basic Commands

```bash
# Get a market quote
> quote MNQ

# View open positions
> positions

# View open orders
> orders

# Get account info
> account_info

# Exit
> quit
```

---

## Step 3: Place Your First Trade

### Market Order (Instant Execution)

```bash
> trade MNQ BUY 1
```

Confirmation:

```
⚠️ CONFIRM TRADE:
Symbol: MNQ
Side: BUY
Quantity: 1
Account: PRAC-V2-14334-56363256
Confirm? (y/N): y

✅ Order placed successfully!
Order ID: 2152365156
```

### Limit Order (Price-Specific)

```bash
> limit MNQ SELL 1 25750
```

### Bracket Order (Entry + Stop Loss + Take Profit)

```bash
> bracket MNQ BUY 1 50 100
# Buys 1 MNQ with 50-tick stop loss and 100-tick take profit
```

### Close a Position

```bash
> positions  # Note the position ID
> close 503201232
```

### Emergency: Flatten All

```bash
> flatten
# Closes ALL positions and cancels ALL orders
```

---

## Step 4: Open the GUI (Master Control)

### Launch Browser Interface

From the CLI:

```bash
> master
# OR
> gui
```

Or non-interactive:

```bash
python trading_bot.py --account_select=1 --command='master'
```

The browser will open automatically at `http://127.0.0.1:5555/master`

### GUI Features

- **Real-time chart** (TradingView Lightweight Charts)
- **Trading panel** (market, limit, stop, bracket orders)
- **Positions table** (live updates via WebSocket)
- **Orders table** (live updates)
- **Strategy control** (start/stop strategies)
- **Quick actions** (flatten, cancel all)
- **Account status bar** (balance, P&L)

---

## Step 5: Run a Strategy

### List Available Strategies

```bash
> strategies list
```

Output:

```
Available Strategies:
1. simple_candle - Simple Candle Strategy
2. trend_scalp - Trend Scalping Strategy
```

### Start a Strategy

```bash
> strategies start simple_candle --symbols=MNQ --timeframe=15s
```

### Check Strategy Status

```bash
> strategies status
```

### Stop a Strategy

```bash
> strategies stop simple_candle
```

---

## Quick Command Reference

### Market Data
```bash
quote <symbol>           # Real-time quote
depth <symbol>           # Order book
history <symbol> 5m 100  # 100 bars of 5-minute data
contracts                # List available contracts
```

### Trading
```bash
trade <symbol> <side> <qty>                     # Market order
limit <symbol> <side> <qty> <price>             # Limit order
bracket <symbol> <side> <qty> <stop> <tp>       # Bracket order
stop <symbol> <side> <qty> <price>              # Stop order
```

### Position & Order Management
```bash
positions                # Show open positions
orders                   # Show open orders
close <pos_id> [qty]     # Close position (full or partial)
cancel <order_id>        # Cancel order
flatten                  # Close all + cancel all
```

### Account
```bash
account_info             # Detailed account info
account_state            # Real-time balance and P&L
compliance               # Check compliance status
risk                     # Risk metrics
```

### Strategies
```bash
strategies list          # List all strategies
strategies start <name>  # Start a strategy
strategies stop <name>   # Stop a strategy
strategies status        # Show all strategy status
```

### System
```bash
metrics                  # System performance metrics
help                     # Show help
quit                     # Exit
```

---

## Non-Interactive Mode

### Execute Single Command

```bash
python trading_bot.py --account_select=1 --command='quote MNQ'
```

### Flatten All (Webhook/Script)

```bash
python trading_bot.py --account_select=1 --command='flatten' --non_interactive
```

### Start Strategy

```bash
python trading_bot.py --account_select=1 --command='strategies start simple_candle --symbols=MNQ'
```

---

## Troubleshooting

### Authentication Failed
- ✅ Check `.env` file exists and has correct credentials
- ✅ Verify TopStepX account is active
- ✅ Try logging into TopStepX web UI to confirm password

### No Contracts Found
- ✅ Wait 5-10 seconds for initialization
- ✅ Run `contracts` command to refresh
- ✅ Check logs: `tail -f trading_bot.log`

### Order Placement Failed
- ✅ Check if market is open
- ✅ Verify account has sufficient balance
- ✅ Check compliance status: `compliance` command
- ✅ Review logs for error details

### GUI Won't Open
- ✅ Check if port 5555 is available
- ✅ Try manually: `http://127.0.0.1:5555/master`
- ✅ Check firewall settings
- ✅ Review logs for errors

### Strategy Not Starting
- ✅ Check if symbol is valid: `contracts` command
- ✅ Verify timeframe format (e.g., `15s`, `5m`, `1h`)
- ✅ Check strategy status: `strategies status`
- ✅ Review logs for errors

---

## Log Files

Logs are written to `trading_bot.log`:

```bash
# View live logs
tail -f trading_bot.log

# Search for errors
grep ERROR trading_bot.log

# Last 100 lines
tail -100 trading_bot.log
```

---

## Next Steps

- 📖 **Read [Trading Guide](03-TRADING-GUIDE.md)** for advanced order types
- 🖥️ **Explore [GUI Dashboard](04-GUI-DASHBOARD.md)** for browser interface details
- 🎯 **Learn [Strategy System](05-STRATEGIES.md)** to create custom strategies
- 🧪 **Try [Backtesting](06-BACKTESTING.md)** to test strategies on historical data
- 🏗️ **Understand [Architecture](02-ARCHITECTURE.md)** to see how it all works

---

## Safety Tips

1. **Always use practice accounts first** before trading live/eval/funded
2. **Use `flatten` command** if you need to exit all positions immediately
3. **Monitor compliance** regularly: `compliance` command
4. **Set appropriate stop losses** on all trades
5. **Test strategies on backtests** before running live
6. **Keep logs** for debugging and analysis
7. **Never share credentials** or API tokens

---

**Ready to trade?** Open the GUI with `master` or start trading via CLI! 🚀

For more detailed information, see:
- [Trading Guide](03-TRADING-GUIDE.md)
- [GUI Dashboard](04-GUI-DASHBOARD.md)
- [Troubleshooting](10-TROUBLESHOOTING.md)

