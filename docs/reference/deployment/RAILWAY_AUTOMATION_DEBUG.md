# Railway Automation Debugging Guide

## Problem: Overnight Strategy Not Auto-Starting on Railway

Based on your logs, the strategy automation is **not running** on Railway. This guide helps you diagnose exactly where it's failing.

---

## Step 1: Verify Railway Start Command

**Check:** Railway Dashboard → Your Service → Settings → "Start Command"

**Should be:**
```bash
python3 servers/start_async_webhook.py
```

**If it's anything else** (like `python trading_bot.py` or `python servers/webhook_server.py`), the modular strategy auto-start will **never run**.

**Fix:** Update the start command to match the Procfile:
```
web: python3 servers/start_async_webhook.py
```

---

## Step 2: Check Railway Environment Variables

**Check:** Railway Dashboard → Your Service → Variables

**Required variables for overnight strategy:**

```bash
# API Credentials (required)
PROJECT_X_USERNAME=your_username
PROJECT_X_PASSWORD=your_password
# OR
PROJECT_X_API_KEY=your_api_key

# Strategy Enable Flag (CRITICAL - must match modular system)
OVERNIGHT_RANGE_ENABLED=true

# Strategy Configuration
OVERNIGHT_RANGE_SYMBOLS=MNQ
OVERNIGHT_RANGE_POSITION_SIZE=1
OVERNIGHT_RANGE_MAX_POSITIONS=2

# Optional: Strategy-specific settings
OVERNIGHT_START_TIME=18:00
OVERNIGHT_END_TIME=09:30
MARKET_OPEN_TIME=09:30
STRATEGY_TIMEZONE=US/Eastern
ATR_PERIOD=14
STOP_ATR_MULTIPLIER=1.25
TP_ATR_MULTIPLIER=2.0
BREAKEVEN_ENABLED=true
BREAKEVEN_PROFIT_POINTS=15.0
```

**⚠️ IMPORTANT:** The code uses `OVERNIGHT_RANGE_ENABLED`, NOT `OVERNIGHT_ENABLED`. If you only have `OVERNIGHT_ENABLED=true` in Railway, the strategy will be disabled.

---

## Step 3: Check Railway Logs for Startup Messages

**Check:** Railway Dashboard → Your Service → Logs

**Search for these startup messages** (they should appear when the service starts):

### Expected Startup Sequence:

```
🤖 Initializing trading bot...
🔐 Authenticating...
📋 Listing accounts...
✅ Selected account: [account name]
💾 Loading persisted strategy states on server startup...
🚀 Auto-starting enabled strategies on server startup...
✅ Strategy initialization complete on server startup
```

### Strategy Manager Initialization:

```
📦 Strategy Manager initialized
📝 Registered strategy: overnight_range
🔄 Loading strategies from configuration...
✅ Loaded strategy: overnight_range (symbols: MNQ)
📊 Total strategies loaded: 1/3
```

### Strategy Auto-Start:

```
🚀 Auto-starting enabled strategies...
▶️  Auto-starting overnight_range from environment (symbols: MNQ)
🚀 Started strategy: overnight_range
✅ Auto-started: Strategy started: overnight_range on MNQ
📊 Active strategies after auto-start: 1
```

### Overnight Strategy Start:

```
🚀 Overnight Range Strategy started!
   Symbols: MNQ
   Market Open: 09:30 US/Eastern
📅 Market open scanner started - targeting 09:30 US/Eastern
```

---

## Step 4: Check for Market Open Execution

**Around 9:30 AM ET**, you should see:

```
🔔 Market open reached—executing scheduled sequence.
🔔 Executing overnight range break strategy for symbols: MNQ
📊 Processing MNQ...
Fetching overnight range for MNQ
📊 Overnight range for MNQ: High=..., Low=..., Range=...
🚀 Placing range break orders for MNQ...
✅ Long breakout order placed: [order_id]
✅ Short breakout order placed: [order_id]
✅ Successfully placed orders for MNQ
```

**If you don't see these messages**, the strategy is either:
- Not started (check Step 3)
- Not enabled (check Step 2)
- Failing silently (check error logs)

---

## Step 5: Check Database Strategy State

The strategy state is **persisted per account** in the database. If the DB has `enabled=false` for your account, it will override environment variables.

**To check/override via CLI:**

1. **Activate your venv:**
   ```bash
   source venv/bin/activate
   # OR use venv's Python directly:
   ./venv/bin/python trading_bot.py
   ```

2. **Run strategy status commands:**
   ```
   > strategies list
   > strategies status
   ```

3. **If strategy shows as disabled, force start it:**
   ```
   > strategies start overnight_range MNQ
   ```

   This will:
   - Start the strategy immediately
   - Persist `enabled=true` to the database for your account
   - Override any previous disabled state

---

## Step 6: Common Failure Modes

### Failure Mode 1: Wrong Start Command
**Symptom:** No startup logs at all, only monitoring loops
**Fix:** Update Railway start command to `python3 servers/start_async_webhook.py`

### Failure Mode 2: Missing/Incorrect Env Variable
**Symptom:** See "⏸️  Strategy disabled: overnight_range" in logs
**Fix:** Add `OVERNIGHT_RANGE_ENABLED=true` to Railway variables

### Failure Mode 3: Database Override
**Symptom:** Strategy shows as disabled in `strategies status` even with env var set
**Fix:** Run `strategies start overnight_range MNQ` to override DB state

### Failure Mode 4: Strategy Started But Not Executing
**Symptom:** See "Strategy started" but no market open logs
**Check:**
- Is it actually 9:30 AM ET?
- Check `MARKET_OPEN_TIME` and `STRATEGY_TIMEZONE` env vars
- Look for errors in `track_overnight_range` or `calculate_atr` methods

---

## Step 7: Quick Diagnostic Script

Create a test script to verify Railway environment:

```python
# test_railway_env.py
import os
from load_env import load_env_file

load_env_file()

print("=== Railway Environment Check ===")
print(f"OVERNIGHT_RANGE_ENABLED: {os.getenv('OVERNIGHT_RANGE_ENABLED', 'NOT SET')}")
print(f"OVERNIGHT_RANGE_SYMBOLS: {os.getenv('OVERNIGHT_RANGE_SYMBOLS', 'NOT SET')}")
print(f"PROJECT_X_USERNAME: {os.getenv('PROJECT_X_USERNAME', 'NOT SET')}")
print(f"PROJECT_X_PASSWORD: {'SET' if os.getenv('PROJECT_X_PASSWORD') else 'NOT SET'}")

# Test strategy config loading
from strategies.strategy_base import StrategyConfig
config = StrategyConfig.from_env("overnight_range")
print(f"\nStrategy Config:")
print(f"  Enabled: {config.enabled}")
print(f"  Symbols: {config.symbols}")
print(f"  Position Size: {config.position_size}")
```

Run on Railway:
```bash
python3 test_railway_env.py
```

---

## Step 8: Force Strategy Start (Temporary Fix)

If you need to force-start the strategy immediately without waiting for auto-start:

1. **SSH into Railway** (if available) or use Railway CLI
2. **Run:**
   ```bash
   python3 -c "
   import asyncio
   from trading_bot import TopStepXTradingBot
   from load_env import load_env_file
   load_env_file()
   
   async def test():
       bot = TopStepXTradingBot()
       await bot.authenticate()
       accounts = await bot.list_accounts()
       bot.selected_account = accounts[0]
       await bot.strategy_manager.start_strategy('overnight_range', symbols=['MNQ'])
       print('Strategy started!')
   
   asyncio.run(test())
   "
   ```

---

## Summary Checklist

- [ ] Railway start command is `python3 servers/start_async_webhook.py`
- [ ] Railway has `OVERNIGHT_RANGE_ENABLED=true` (not `OVERNIGHT_ENABLED`)
- [ ] Railway logs show "🤖 Initializing trading bot..." on startup
- [ ] Railway logs show "🚀 Auto-starting enabled strategies..."
- [ ] Railway logs show "✅ Auto-started: Strategy started: overnight_range"
- [ ] Railway logs show "📅 Market open scanner started" after strategy starts
- [ ] Around 9:30 AM ET, logs show "🔔 Market open reached—executing scheduled sequence"
- [ ] Database strategy state for your account has `enabled=true` (check via `strategies status`)

If all checkboxes are checked but still no trades, the issue is likely in:
- Order placement logic (check for API errors)
- Market condition filters blocking trades
- Insufficient historical data for ATR/range calculation

