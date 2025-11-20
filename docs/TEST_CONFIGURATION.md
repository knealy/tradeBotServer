# Test Configuration for Overnight Range Strategy

## Quick Test Setup (7:51 PM EST)

To test the automation RIGHT NOW, update your `.env` file with these values:

```bash
# Overnight Range Tracking Window
OVERNIGHT_START_TIME=19:00    # Start tracking range from 7:00 PM (gives us 51 minutes of data)
OVERNIGHT_END_TIME=19:55      # End tracking at 7:55 PM
MARKET_OPEN_TIME=19:55        # Place orders at 7:55 PM

# Grace period (how long after market open to still execute)
MARKET_OPEN_GRACE_MINUTES=5   # Execute if within 5 minutes of market open

# Strategy Enable
OVERNIGHT_RANGE_ENABLED=true
OVERNIGHT_RANGE_SYMBOLS=MNQ   # Use just one symbol for testing

# Position Size (keep small for testing)
OVERNIGHT_RANGE_POSITION_SIZE=1
```

## How It Works

1. **Range Tracking**: Strategy fetches historical bars from `OVERNIGHT_START_TIME` to `OVERNIGHT_END_TIME` to calculate high/low
2. **Market Open**: At `MARKET_OPEN_TIME`, the strategy:
   - Calculates ATR
   - Calculates overnight range
   - Places LONG order above range high
   - Places SHORT order below range low
3. **Grace Period**: If the bot starts AFTER market open, it will still execute if within `MARKET_OPEN_GRACE_MINUTES`

## Testing Steps

1. **Update `.env`** with the test times above
2. **Restart the bot** (or restart the strategy):
   ```
   > strategies stop overnight_range
   > strategies start overnight_range MNQ
   ```
3. **Watch the logs** - you should see:
   ```
   📅 Market open scanner started - targeting 19:55 US/Eastern
   ⏰ Next market open execution scheduled for 2025-11-19 19:55:00 EST (in 0.07 hours)
   ```
4. **At 19:55**, you should see:
   ```
   🔔 Market open reached—executing scheduled sequence.
   🔔 Executing overnight range break strategy for symbols: MNQ
   📊 Processing MNQ...
   Fetching overnight range for MNQ
   📊 Overnight range for MNQ: High=..., Low=..., Range=...
   🚀 Placing range break orders for MNQ...
   ✅ Long breakout order placed: [order_id]
   ✅ Short breakout order placed: [order_id]
   ```

## Required vs Optional Environment Variables

### ✅ REQUIRED (Minimum to run):

```bash
# Enable Strategy
OVERNIGHT_RANGE_ENABLED=true

# Symbols to Trade
OVERNIGHT_RANGE_SYMBOLS=MNQ

# Time Configuration
OVERNIGHT_START_TIME=18:00      # When to start tracking range
OVERNIGHT_END_TIME=09:30        # When to stop tracking range
MARKET_OPEN_TIME=09:30          # When to place orders
STRATEGY_TIMEZONE=US/Eastern    # Timezone for all times
```

### ⚙️ RECOMMENDED (Good defaults):

```bash
# Position Sizing
OVERNIGHT_RANGE_POSITION_SIZE=1
OVERNIGHT_RANGE_MAX_POSITIONS=2

# ATR Configuration
ATR_PERIOD=14
ATR_TIMEFRAME=5m
STOP_ATR_MULTIPLIER=1.25
TP_ATR_MULTIPLIER=2.0

# Breakeven
BREAKEVEN_ENABLED=true
BREAKEVEN_PROFIT_POINTS=15.0
```

### 🔧 OPTIONAL (Advanced):

```bash
# Risk Management
OVERNIGHT_RANGE_MAX_DAILY_TRADES=10
OVERNIGHT_RANGE_RISK_PERCENT=0.5

# Order Placement
RANGE_BREAK_OFFSET=0.25
STRATEGY_QUANTITY=1

# Market Condition Filters (all default to OFF)
OVERNIGHT_FILTER_RANGE_SIZE=false
OVERNIGHT_FILTER_GAP=false
OVERNIGHT_FILTER_VOLATILITY=false
OVERNIGHT_FILTER_DLL_PROXIMITY=false

# Grace Period
MARKET_OPEN_GRACE_MINUTES=5
```

## Variable Naming Confusion

**Note:** There's some redundancy in variable names:

- `OVERNIGHT_START_TIME` / `OVERNIGHT_END_TIME` - Used by `OvernightRangeStrategy` directly
- `OVERNIGHT_RANGE_*` - Used by `StrategyConfig.from_env("OVERNIGHT_RANGE")` for base strategy config
- `MARKET_OPEN_TIME` - Used by `OvernightRangeStrategy` for when to place orders

**The strategy uses:**
- `OVERNIGHT_START_TIME` (not `OVERNIGHT_RANGE_START_TIME`)
- `OVERNIGHT_END_TIME` (not `OVERNIGHT_RANGE_END_TIME`)
- `MARKET_OPEN_TIME` (not `OVERNIGHT_RANGE_MARKET_OPEN_TIME`)

**The base config uses:**
- `OVERNIGHT_RANGE_ENABLED`
- `OVERNIGHT_RANGE_SYMBOLS`
- `OVERNIGHT_RANGE_POSITION_SIZE`
- etc.

This is a bit confusing and could be simplified in the future!

