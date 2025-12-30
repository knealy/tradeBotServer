# 🔄 RESTART REQUIRED - Your Strategies Are Running OLD Code

## Current Situation

✅ **ALL CODE FIXES ARE APPLIED** - The `reduceOnly` bug has been fixed in all locations  
❌ **YOUR RUNNING PROCESSES HAVE OLD CODE** - They started at 10:13 AM (before fixes)  
🔄 **RESTART NEEDED** - Kill and restart all strategy executor processes

## Running Processes (Need Restart)

Found these processes running OLD code:

```
PID 89295 - trend_following   (started 10:13 AM) - Terminal s001
PID 89280 - mean_reversion    (started 10:13 AM) - Terminal s002 ← Getting 500 errors!
PID 89270 - overnight_range   (started 10:13 AM) - Terminal s000
```

These were started BEFORE the fixes were applied, so they're still using the buggy code with `reduceOnly=True` on brackets.

## Quick Restart (Option 1 - Stop Only)

Run this script to stop all old processes:

```bash
cd /Users/knealy/tradeBotServer
./RESTART_STRATEGIES.sh
```

Then manually restart each strategy in its own terminal.

## Full Restart (Option 2 - Stop & Auto-Start)

Run this to stop AND restart all strategies with the fixed code:

```bash
cd /Users/knealy/tradeBotServer

# First, stop the old processes
pkill -f "strategy_executor"

# Wait a moment
sleep 2

# Then run the restart script
./restart_all_strategies.sh
```

This will start all three strategies as background processes with separate log files.

## Manual Restart (Option 3 - Terminal by Terminal)

### Step 1: Stop Each Strategy

In each terminal where a strategy is running, press `Ctrl+C` to stop it.

Or kill them all at once:
```bash
pkill -f "strategy_executor.*overnight_range"
pkill -f "strategy_executor.*mean_reversion"
pkill -f "strategy_executor.*trend_following"
```

### Step 2: Restart Each Strategy

**Terminal 1 (overnight_range):**
```bash
cd /Users/knealy/tradeBotServer
caffeinate -dimsu python core/strategy_executor.py \
  --account_select=1 \
  --strategy=overnight_range \
  --symbols=mnq,mes,mgc
```

**Terminal 2 (mean_reversion):**
```bash
cd /Users/knealy/tradeBotServer
caffeinate -dimsu python core/strategy_executor.py \
  --account_select=1 \
  --strategy=mean_reversion \
  --symbols=mnq,mes,mgc
```

**Terminal 3 (trend_following):**
```bash
cd /Users/knealy/tradeBotServer
caffeinate -dimsu python core/strategy_executor.py \
  --account_select=1 \
  --strategy=trend_following \
  --symbols=mnq,mes,mgc
```

## Verification After Restart

### 1. Check Processes Are Running with NEW Code

```bash
ps aux | grep strategy_executor | grep -v grep
```

You should see three new processes with LATER start times (not 10:13 AM).

### 2. Monitor for Success (No More 500 Errors)

```bash
# Watch main log
tail -f trading_bot.log | grep -E "placed|500|bracket"

# Or watch strategy-specific logs if using background mode
tail -f strategy_mean_reversion.log | grep -E "placed|500|bracket"
```

### 3. Expected Results

**✅ What You SHOULD See:**
```
✅ Mean reversion order placed: SELL 1 MES (Order ID: 12345)
✅ Long breakout order placed: 67890
✅ Bracket order placed - ID: 54321
```

**❌ What You Should NOT See Anymore:**
```
❌ HTTP 500: 500 Server Error: Internal Server Error
❌ Failed to create bracket order: HTTP 500
❌ Retry also failed with 500 error
```

## What Was Fixed (Code Changes Applied)

All 5 locations where `reduceOnly=True` was incorrectly set on brackets:

1. ✅ `brokers/topstepx_adapter.py` line ~355 - Generic order placement
2. ✅ `brokers/topstepx_adapter.py` line ~3534 - Stop bracket orders
3. ✅ `brokers/topstepx_adapter.py` line ~4148 - Market bracket orders (mean_reversion uses this!)
4. ✅ `rust/src/order_execution/mod.rs` line ~484 - Rust hot-path
5. ✅ `strategies/overnight_range_strategy.py` - Monitoring cleanup (2 locations)

## Why the Restart is Necessary

Python loads all code into memory when a process starts. Changes to `.py` files on disk don't affect running processes. Your strategies started at 10:13 AM with the OLD code and have been running with the bug ever since.

Timeline:
- **10:13 AM** - Your strategies started (with OLD buggy code)
- **10:45 AM** - I applied all the fixes to the code files
- **10:46 AM** - mean_reversion strategy got 500 error (still running OLD code!)
- **11:01 AM** - NOW - strategies still running OLD code, need restart

## Summary

| Item | Status |
|------|--------|
| Code fixes applied | ✅ DONE |
| Rust module fixed | ✅ DONE |
| Documentation | ✅ DONE |
| **Processes restarted** | ❌ **TODO** ← YOU ARE HERE |
| Testing/verification | ⏳ Pending (after restart) |

---

## Next Steps

1. 🛑 **Stop all old strategy executor processes** (use one of the options above)
2. 🚀 **Restart all strategies** with the fixed code
3. 👀 **Watch logs** to confirm NO MORE 500 errors
4. 🎉 **Celebrate** - bracket orders will work correctly!

**Choose your restart method above and execute it now!** 🔄
