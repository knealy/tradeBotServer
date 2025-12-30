# Environment Configuration Guide

This document describes all available environment variables for configuring the TopStepX Trading Bot and its strategies.

## API Credentials

### Required for Authentication

```bash
PROJECT_X_USERNAME=your_username_here
PROJECT_X_API_KEY=your_api_key_here
```

**Note:** The bot also accepts these alternative variable names:
- `TOPSETPX_USERNAME` (alternative to `PROJECT_X_USERNAME`)
- `TOPSETPX_API_KEY` (alternative to `PROJECT_X_API_KEY`)

### Optional: JWT Token (Auto-Refresh Enabled)

```bash
JWT_TOKEN=eyJhbGci...  # Optional: Pre-authenticated JWT token
```

**Benefits:**
- Faster startup (no API call if token is valid)
- Auto-refreshes when expired (requires `PROJECT_X_USERNAME` and `PROJECT_X_API_KEY`)
- Useful for Railway deployment

**How it works:**
1. If `JWT_TOKEN` is set and valid → Server starts immediately
2. If `JWT_TOKEN` is expired → Server auto-refreshes using credentials
3. If `JWT_TOKEN` is missing → Server authenticates using credentials

**See also:**
- `JWT_TOKEN_GENERATION.md` - How to generate a new JWT token
- `JWT_AUTO_REFRESH_FIX_2025-12-02.md` - Auto-refresh functionality details

## Logging & Monitoring

```bash
LOG_LEVEL=INFO  # DEBUG, INFO, WARNING, ERROR, CRITICAL
DISCORD_WEBHOOK_URL=  # Optional: Discord webhook for notifications
```

## API Rate Limiting

```bash
API_RATE_LIMIT_MAX=60  # Max API calls per period
API_RATE_LIMIT_PERIOD=60  # Period in seconds
```

## Strategy Manager Configuration

Global settings for the modular strategy system:

```bash
MAX_CONCURRENT_STRATEGIES=3  # Max number of strategies running simultaneously
GLOBAL_MAX_POSITIONS=5  # Max total positions across all strategies
```

## Overnight Range Breakout Strategy (Default Active)

This is the **default active strategy** that runs automatically.

### Strategy Control

```bash
OVERNIGHT_RANGE_ENABLED=true  # Enable/disable strategy at startup
OVERNIGHT_RANGE_SYMBOLS=MNQ,MES  # Comma-separated list of symbols
OVERNIGHT_RANGE_MAX_POSITIONS=2
OVERNIGHT_RANGE_POSITION_SIZE=1
OVERNIGHT_RANGE_RISK_PERCENT=0.5  # Risk per trade as % of account
OVERNIGHT_RANGE_MAX_DAILY_TRADES=10
```

### Market Condition Preferences

```bash
OVERNIGHT_RANGE_PREFERRED_CONDITIONS=breakout,ranging
OVERNIGHT_RANGE_AVOID_CONDITIONS=high_volatility
OVERNIGHT_RANGE_RESPECT_DLL=true  # Respect Daily Loss Limit
OVERNIGHT_RANGE_RESPECT_MLL=true  # Respect Maximum Loss Limit
OVERNIGHT_RANGE_MAX_DLL_USAGE=0.75  # Use max 75% of DLL
```

### Time Windows

```bash
OVERNIGHT_RANGE_START_TIME=09:30  # Trading start time
OVERNIGHT_RANGE_END_TIME=15:45  # Trading end time
OVERNIGHT_RANGE_NO_TRADE_START=15:30  # No new trades window start
OVERNIGHT_RANGE_NO_TRADE_END=16:00  # No new trades window end
```

### Overnight Session Configuration

```bash
OVERNIGHT_START_TIME=18:00  # 6pm EST - overnight session start
OVERNIGHT_END_TIME=09:30  # 9:30am EST - overnight session end
MARKET_OPEN_TIME=09:30  # 9:30am EST - market open time
STRATEGY_TIMEZONE=US/Eastern
```

### ATR Configuration

```bash
ATR_PERIOD=14  # ATR calculation period (bars)
ATR_TIMEFRAME=5m  # Timeframe for ATR calculation
STOP_ATR_MULTIPLIER=1.25  # Stop loss: 1.25x ATR from entry
TP_ATR_MULTIPLIER=2.0  # Take profit: 2.0x ATR from entry
```

### Order Placement

```bash
RANGE_BREAK_OFFSET=0.25  # Offset from range extremes for entry
STRATEGY_QUANTITY=1  # Default position size
```

### Breakeven Management

```bash
BREAKEVEN_ENABLED=true  # Enable auto-breakeven
BREAKEVEN_PROFIT_POINTS=15.0  # Move stop to BE after +15 pts profit
```

### Manual Breakeven (for stop_bracket command)

```bash
MANUAL_BREAKEVEN_ENABLED=false  # Enable breakeven for manual orders
MANUAL_BREAKEVEN_PROFIT_POINTS=15.0  # Profit threshold for manual orders
```

### Market Condition Filters (ALL DEFAULT TO OFF)

These filters are **optional** and **disabled by default**. Enable them to add additional safety checks:

```bash
# Range Size Filter
OVERNIGHT_FILTER_RANGE_SIZE=false  # Filter by range size
OVERNIGHT_RANGE_MIN_POINTS=50.0  # Minimum range size in points
OVERNIGHT_RANGE_MAX_POINTS=500.0  # Maximum range size in points

# Gap Filter
OVERNIGHT_FILTER_GAP=false  # Filter large gaps
OVERNIGHT_GAP_MAX_POINTS=200.0  # Maximum gap size in points

# Volatility Filter
OVERNIGHT_FILTER_VOLATILITY=false  # Filter by ATR volatility
OVERNIGHT_ATR_MIN=20.0  # Minimum ATR for entry
OVERNIGHT_ATR_MAX=200.0  # Maximum ATR for entry

# DLL Proximity Filter
OVERNIGHT_FILTER_DLL_PROXIMITY=false  # Pause when close to DLL
OVERNIGHT_DLL_THRESHOLD_PERCENT=0.75  # Stop trading at 75% DLL usage
```

## Mean Reversion Strategy

This strategy trades mean reversion in ranging markets.

### Strategy Control

```bash
MEAN_REVERSION_ENABLED=false  # Enable/disable strategy at startup
MEAN_REVERSION_SYMBOLS=MNQ,MES
MEAN_REVERSION_MAX_POSITIONS=2
MEAN_REVERSION_POSITION_SIZE=1
MEAN_REVERSION_RISK_PERCENT=0.5
MEAN_REVERSION_MAX_DAILY_TRADES=10
```

### Market Condition Preferences

```bash
MEAN_REVERSION_PREFERRED_CONDITIONS=ranging,reversal
MEAN_REVERSION_AVOID_CONDITIONS=trending_up,trending_down,breakout
MEAN_REVERSION_RESPECT_DLL=true
MEAN_REVERSION_RESPECT_MLL=true
MEAN_REVERSION_MAX_DLL_USAGE=0.75
```

### Time Windows

```bash
MEAN_REVERSION_START_TIME=09:30
MEAN_REVERSION_END_TIME=15:45
MEAN_REVERSION_NO_TRADE_START=15:30
MEAN_REVERSION_NO_TRADE_END=16:00
```

### RSI Configuration

```bash
MEAN_REV_RSI_PERIOD=14  # RSI calculation period
MEAN_REV_RSI_OVERBOUGHT=70  # Overbought level
MEAN_REV_RSI_OVERSOLD=30  # Oversold level
```

### Moving Average Configuration

```bash
MEAN_REV_MA_PERIOD=20  # MA period
MEAN_REV_MA_TYPE=SMA  # SMA or EMA
```

### ATR Configuration

```bash
MEAN_REV_ATR_PERIOD=14
MEAN_REV_ATR_DEVIATION=2.0  # Entry when price is 2x ATR from MA
MEAN_REV_STOP_ATR=1.5  # Stop loss: 1.5x ATR
MEAN_REV_TARGET_MA_RETURN=true  # Target return to MA for exit
```

### Timeframe

```bash
MEAN_REV_TIMEFRAME=5m  # Analysis timeframe
```

## Trend Following Strategy

This strategy follows trends using moving average crossovers.

### Strategy Control

```bash
TREND_FOLLOWING_ENABLED=false  # Enable/disable strategy at startup
TREND_FOLLOWING_SYMBOLS=MNQ,MES
TREND_FOLLOWING_MAX_POSITIONS=2
TREND_FOLLOWING_POSITION_SIZE=1
TREND_FOLLOWING_RISK_PERCENT=0.5
TREND_FOLLOWING_MAX_DAILY_TRADES=10
```

### Market Condition Preferences

```bash
TREND_FOLLOWING_PREFERRED_CONDITIONS=trending_up,trending_down,breakout
TREND_FOLLOWING_AVOID_CONDITIONS=ranging,reversal
TREND_FOLLOWING_RESPECT_DLL=true
TREND_FOLLOWING_RESPECT_MLL=true
TREND_FOLLOWING_MAX_DLL_USAGE=0.75
```

### Time Windows

```bash
TREND_FOLLOWING_START_TIME=09:30
TREND_FOLLOWING_END_TIME=15:45
TREND_FOLLOWING_NO_TRADE_START=15:30
TREND_FOLLOWING_NO_TRADE_END=16:00
```

### Moving Average Crossover Configuration

```bash
TREND_FAST_MA_PERIOD=10  # Fast MA period
TREND_SLOW_MA_PERIOD=30  # Slow MA period
TREND_MA_TYPE=EMA  # SMA or EMA
TREND_MIN_STRENGTH=0.5  # Minimum trend strength (0.0-1.0)
```

### ATR Configuration

```bash
TREND_ATR_PERIOD=14
TREND_ATR_STOP=2.0  # Initial stop loss: 2x ATR
TREND_ATR_TRAILING=3.0  # Trailing stop: 3x ATR
```

### Pyramiding

```bash
TREND_PYRAMID_ENABLED=false  # Enable adding to winning positions
TREND_PYRAMID_MAX_ADDS=2  # Maximum number of pyramid adds
```

### Timeframe

```bash
TREND_TIMEFRAME=15m  # Analysis timeframe (longer for trend following)
```

## Account Tracker

TopStepX compliance tracking:

```bash
DAILY_LOSS_LIMIT=1000.00  # Daily loss limit (prop firm specific)
MAXIMUM_LOSS_LIMIT=3000.00  # Maximum loss limit (prop firm specific)
INITIAL_BALANCE=150000.00  # Starting account balance
```

## Quick Start Configuration

### Minimal Configuration (Overnight Range Only)

The **overnight range strategy is the default active strategy**. To run with default settings, you only need:

```bash
PROJECTX_USERNAME=your_username
PROJECTX_PASSWORD=your_password
OVERNIGHT_RANGE_ENABLED=true
OVERNIGHT_RANGE_SYMBOLS=MNQ
```

### Running Multiple Strategies

To run multiple strategies simultaneously:

```bash
# Enable strategies
OVERNIGHT_RANGE_ENABLED=true
MEAN_REVERSION_ENABLED=true
TREND_FOLLOWING_ENABLED=true

# Set different symbols for each
OVERNIGHT_RANGE_SYMBOLS=MNQ
MEAN_REVERSION_SYMBOLS=MES
TREND_FOLLOWING_SYMBOLS=YM

# Limit concurrent strategies
MAX_CONCURRENT_STRATEGIES=3
```

### Conservative Configuration (With All Filters)

For maximum safety, enable all market condition filters:

```bash
# Overnight range with all filters
OVERNIGHT_RANGE_ENABLED=true
OVERNIGHT_FILTER_RANGE_SIZE=true
OVERNIGHT_FILTER_GAP=true
OVERNIGHT_FILTER_VOLATILITY=true
OVERNIGHT_FILTER_DLL_PROXIMITY=true
OVERNIGHT_DLL_THRESHOLD_PERCENT=0.50  # Stop at 50% DLL
```

## Commands

### Overnight Range Strategy (Default)

```bash
strategy_start [symbols]  # Start overnight range (default strategy)
strategy_stop            # Stop overnight range
strategy_status          # Show overnight range status
```

### Modular Strategy System

```bash
strategies list                      # List all strategies
strategies status                    # Show all strategies status
strategies start <name> [symbols]   # Start specific strategy
strategies stop <name>               # Stop specific strategy
strategies start_all                 # Start all enabled strategies
strategies stop_all                  # Stop all strategies
```

### Examples

```bash
# Start mean reversion on ES and NQ
strategies start mean_reversion MES,MNQ

# Start trend following with default symbols from .env
strategies start trend_following

# Stop mean reversion
strategies stop mean_reversion

# View all strategies status
strategies status
```

## Tips

1. **Start with one strategy**: The overnight range strategy is recommended as the default.
2. **Test in simulation**: Always test new strategies in a simulation account first.
3. **Enable filters gradually**: Start with no filters, then add them one by one to find optimal settings.
4. **Monitor DLL**: Keep `RESPECT_DLL=true` to prevent account violations.
5. **Use different symbols**: Assign different symbols to each strategy to avoid conflicts.
6. **Set realistic position sizes**: Start with `POSITION_SIZE=1` and increase gradually.

## Priority Settings

For your current implementation (Option A), these settings ensure the overnight range remains the default:

```bash
# Default active strategy
OVERNIGHT_RANGE_ENABLED=true
OVERNIGHT_RANGE_SYMBOLS=MNQ,MES

# Other strategies disabled by default
MEAN_REVERSION_ENABLED=false
TREND_FOLLOWING_ENABLED=false

# Market filters disabled by default
OVERNIGHT_FILTER_RANGE_SIZE=false
OVERNIGHT_FILTER_GAP=false
OVERNIGHT_FILTER_VOLATILITY=false
OVERNIGHT_FILTER_DLL_PROXIMITY=false
```

This configuration:
- ✅ Overnight range strategy runs by default
- ✅ Market condition filters are implemented but OFF
- ✅ Other strategies are available but not active
- ✅ System is modular and ready to add new strategies via `.env` changes

