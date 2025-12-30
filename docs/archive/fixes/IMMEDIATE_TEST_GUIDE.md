# Immediate Test Guide - Run Strategy Right Now (7:51 PM EST)

## Step 1: Update Your `.env` File

Add or update these lines in your `.env` file:

```bash
# Test Configuration - Execute at 7:55 PM TODAY
OVERNIGHT_START_TIME=19:00
OVERNIGHT_END_TIME=19:55
MARKET_OPEN_TIME=19:55
MARKET_OPEN_GRACE_MINUTES=5

# Keep these as-is
OVERNIGHT_RANGE_ENABLED=true
OVERNIGHT_RANGE_SYMBOLS=MNQ
OVERNIGHT_RANGE_POSITION_SIZE=1
STRATEGY_TIMEZONE=US/Eastern
```

**Why these times?**
- `OVERNIGHT_START_TIME=19:00` - Start tracking from 7:00 PM (gives us 55 minutes of historical data)
- `OVERNIGHT_END_TIME=19:55` - Stop tracking at 7:55 PM
- `MARKET_OPEN_TIME=19:55` - Place orders at 7:55 PM (in 4 minutes from now!)

## Step 2: Restart the Strategy

In your running bot CLI, run:

```
> strategies stop overnight_range
> strategies start overnight_range MNQ
```

**OR** if the bot isn't running, restart it:
```bash
python trading_bot.py
# Select account
# Strategy will auto-start with new times
```

## Step 3: Watch the Logs

You should see:

```
📅 Market open scanner started - targeting 19:55 US/Eastern
⏰ Next market open execution scheduled for 2025-11-19 19:55:00 EST (in 0.07 hours)
```

Then at **7:55 PM**, you'll see:

```
🔔 Market open reached—executing scheduled sequence.
🔔 Executing overnight range break strategy for symbols: MNQ
📊 Processing MNQ...
Fetching overnight range for MNQ
  Session: 2025-11-19 19:00 to 2025-11-19 19:55
  Duration: 55 minutes
  Found 55 bars in overnight session
📊 Overnight range for MNQ: High=..., Low=..., Range=...
🚀 Placing range break orders for MNQ...
✅ Long breakout order placed: [order_id]
✅ Short breakout order placed: [order_id]
✅ Successfully placed orders for MNQ
```

## Step 4: Verify Orders

Check your TopStepX account - you should see:
- 1 LONG stop order above the overnight high
- 1 SHORT stop order below the overnight low
- Both with OCO brackets (stop loss + take profit)

## Troubleshooting

### If orders don't place:

1. **Check logs for errors:**
   - "Insufficient overnight bars" → Not enough historical data
   - "Failed to calculate ATR" → ATR calculation issue
   - "Failed to place orders" → API error

2. **Check timezone:**
   - Make sure `STRATEGY_TIMEZONE=US/Eastern`
   - Current time should be in EST/EDT

3. **Check grace period:**
   - If you start the bot AFTER 7:55 PM, it won't execute
   - Set `MARKET_OPEN_GRACE_MINUTES=10` to give more time

### If you miss the window:

Just update the times to a future time:
```bash
MARKET_OPEN_TIME=20:00  # 8:00 PM
OVERNIGHT_END_TIME=20:00
```
Then restart the strategy.

## After Testing

**Reset to production times:**
```bash
OVERNIGHT_START_TIME=18:00
OVERNIGHT_END_TIME=09:30
MARKET_OPEN_TIME=09:30
```

Then restart the strategy again.

