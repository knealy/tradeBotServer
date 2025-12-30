# Simplified Environment Variables Guide

## Current State: Redundant Variables

The overnight range strategy uses a mix of variable prefixes:
- `OVERNIGHT_*` (strategy-specific times)
- `OVERNIGHT_RANGE_*` (base strategy config)
- `MARKET_OPEN_TIME` (shared)

This creates confusion. Here's what's actually needed:

## Minimal Configuration (5 variables)

```bash
# 1. Enable Strategy
OVERNIGHT_RANGE_ENABLED=true

# 2. Symbols
OVERNIGHT_RANGE_SYMBOLS=MNQ

# 3. Time Windows
OVERNIGHT_START_TIME=18:00
OVERNIGHT_END_TIME=09:30
MARKET_OPEN_TIME=09:30
STRATEGY_TIMEZONE=US/Eastern
```

That's it! Everything else has sensible defaults.

## Full Configuration (All Options)

```bash
# ============================================
# STRATEGY ENABLE/DISABLE
# ============================================
OVERNIGHT_RANGE_ENABLED=true

# ============================================
# SYMBOLS & POSITION SIZING
# ============================================
OVERNIGHT_RANGE_SYMBOLS=MNQ,MES
OVERNIGHT_RANGE_POSITION_SIZE=1
OVERNIGHT_RANGE_MAX_POSITIONS=2

# ============================================
# TIME CONFIGURATION
# ============================================
OVERNIGHT_START_TIME=18:00        # When to START tracking overnight range
OVERNIGHT_END_TIME=09:30          # When to STOP tracking overnight range
MARKET_OPEN_TIME=09:30            # When to PLACE ORDERS
STRATEGY_TIMEZONE=US/Eastern      # Timezone for all times above
MARKET_OPEN_GRACE_MINUTES=5       # Grace period after market open (default: 5)

# ============================================
# ATR CONFIGURATION
# ============================================
ATR_PERIOD=14                     # ATR calculation period (bars)
ATR_TIMEFRAME=5m                  # Timeframe for ATR bars
STOP_ATR_MULTIPLIER=1.25          # Stop loss distance (1.25x ATR)
TP_ATR_MULTIPLIER=2.0             # Take profit distance (2.0x ATR)

# ============================================
# BREAKEVEN MANAGEMENT
# ============================================
BREAKEVEN_ENABLED=true            # Enable/disable breakeven
BREAKEVEN_PROFIT_POINTS=15.0      # Profit threshold to trigger breakeven

# ============================================
# ORDER PLACEMENT
# ============================================
RANGE_BREAK_OFFSET=0.25           # Offset from range high/low for entry
STRATEGY_QUANTITY=1                # Default position size (if not using config)

# ============================================
# RISK MANAGEMENT (Base Strategy Config)
# ============================================
OVERNIGHT_RANGE_MAX_DAILY_TRADES=10
OVERNIGHT_RANGE_RISK_PERCENT=0.5
OVERNIGHT_RANGE_RESPECT_DLL=true
OVERNIGHT_RANGE_RESPECT_MLL=true
OVERNIGHT_RANGE_MAX_DLL_USAGE=0.75

# ============================================
# MARKET CONDITION FILTERS (All Optional)
# ============================================
# These are all DISABLED by default
OVERNIGHT_FILTER_RANGE_SIZE=false
OVERNIGHT_RANGE_MIN_POINTS=50.0
OVERNIGHT_RANGE_MAX_POINTS=500.0

OVERNIGHT_FILTER_GAP=false
OVERNIGHT_GAP_MAX_POINTS=200.0

OVERNIGHT_FILTER_VOLATILITY=false
OVERNIGHT_ATR_MIN=20.0
OVERNIGHT_ATR_MAX=200.0

OVERNIGHT_FILTER_DLL_PROXIMITY=false
OVERNIGHT_DLL_THRESHOLD_PERCENT=0.75
```

## Variable Usage Map

| Variable | Used By | Purpose |
|----------|---------|---------|
| `OVERNIGHT_RANGE_ENABLED` | `StrategyConfig.from_env()` | Enable/disable strategy |
| `OVERNIGHT_RANGE_SYMBOLS` | `StrategyConfig.from_env()` | Trading symbols |
| `OVERNIGHT_RANGE_POSITION_SIZE` | `StrategyConfig.from_env()` | Contracts per trade |
| `OVERNIGHT_START_TIME` | `OvernightRangeStrategy.__init__()` | Range tracking start |
| `OVERNIGHT_END_TIME` | `OvernightRangeStrategy.__init__()` | Range tracking end |
| `MARKET_OPEN_TIME` | `OvernightRangeStrategy.__init__()` | When to place orders |
| `STRATEGY_TIMEZONE` | `OvernightRangeStrategy.__init__()` | Timezone for times |
| `ATR_PERIOD` | `OvernightRangeStrategy.__init__()` | ATR calculation period |
| `STOP_ATR_MULTIPLIER` | `OvernightRangeStrategy.__init__()` | Stop loss multiplier |
| `TP_ATR_MULTIPLIER` | `OvernightRangeStrategy.__init__()` | Take profit multiplier |
| `BREAKEVEN_ENABLED` | `OvernightRangeStrategy.__init__()` | Enable breakeven |
| `BREAKEVEN_PROFIT_POINTS` | `OvernightRangeStrategy.__init__()` | Breakeven threshold |

## Future Simplification Ideas

1. **Unify prefixes**: Use `OVERNIGHT_RANGE_*` for everything
2. **Group related vars**: `OVERNIGHT_RANGE_TIMES_START`, `OVERNIGHT_RANGE_TIMES_END`, etc.
3. **Database-driven**: Store all config in DB, use env only for enable/disable
4. **Config file**: Use YAML/JSON config file instead of 20+ env variables

