# Modular Strategy System Guide

## Overview

The bot now supports a **modular strategy architecture** that allows you to:
- ✅ Run multiple strategies simultaneously
- ✅ Auto-select strategies based on market conditions
- ✅ Add new strategies without modifying core code
- ✅ Configure each strategy independently via `.env`
- ✅ Enforce TopStepX compliance across all strategies

## Architecture

```
┌─────────────────────────────────────────────────────┐
│           Trading Bot (Main)                        │
│  ┌──────────────────────────────────────────────┐   │
│  │       Strategy Manager                       │   │
│  │  ┌────────────┐  ┌────────────┐              │   │
│  │  │ Strategy 1 │  │ Strategy 2 │  ...         │   │
│  │  │  (Active)  │  │  (Paused)  │              │   │
│  │  └────────────┘  └────────────┘              │   │
│  │                                              │   │
│  │  - Auto-selection based on market conditions │   │
│  │  - Global risk management                    │   │
│  │  - Performance tracking                      │   │
│  └──────────────────────────────────────────────┘   │
│                                                     │
│  ┌──────────────────────────────────────────────┐   │
│  │       Account Tracker (TopStepX)             │   │
│  │  - DLL/MLL compliance                        │   │
│  │  - Position sizing                           │   │
│  │  - P&L tracking                              │   │
│  └──────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────┘
```

## Key Components

### 1. BaseStrategy (strategy_base.py)
Abstract base class that all strategies inherit from. Provides:
- TopStepX compliance checks (DLL/MLL)
- Time-based filters
- Position sizing calculations
- Performance metrics tracking
- Common utility methods

### 2. StrategyManager (strategy_manager.py)
Coordinates multiple strategies:
- Loads strategies from configuration
- Starts/stops strategies dynamically
- Auto-selects best strategies for market conditions
- Aggregates performance metrics
- Enforces global limits (max concurrent strategies)

### 3. Individual Strategies
Each strategy inherits from BaseStrategy and implements:
- `analyze()`: Generate trading signals
- `execute()`: Execute trades
- `manage_positions()`: Manage open positions
- `cleanup()`: Clean up resources

## Available Strategies

### 1. Overnight Range Breakout (overnight_range)
**Best for:** Trending breakout markets  
**Preferred conditions:** BREAKOUT, RANGING
**Avoid conditions:** HIGH_VOLATILITY

Tracks overnight ranges and places breakout orders at market open.

**Configuration:**
```bash
# Enable/disable
OVERNIGHT_RANGE_ENABLED=true

# Symbols
OVERNIGHT_RANGE_SYMBOLS=MNQ,MES

# Risk management
OVERNIGHT_RANGE_MAX_POSITIONS=2
OVERNIGHT_RANGE_POSITION_SIZE=1
OVERNIGHT_RANGE_RISK_PERCENT=0.5
OVERNIGHT_RANGE_MAX_DAILY_TRADES=4

# Market conditions
OVERNIGHT_RANGE_PREFERRED_CONDITIONS=breakout,ranging
OVERNIGHT_RANGE_AVOID_CONDITIONS=high_volatility

# Time windows
OVERNIGHT_RANGE_START_TIME=09:30
OVERNIGHT_RANGE_END_TIME=15:45
OVERNIGHT_RANGE_NO_TRADE_START=15:30
OVERNIGHT_RANGE_NO_TRADE_END=16:00

# TopStepX compliance
OVERNIGHT_RANGE_RESPECT_DLL=true
OVERNIGHT_RANGE_RESPECT_MLL=true
OVERNIGHT_RANGE_MAX_DLL_USAGE=0.75
```

### 2. Mean Reversion (mean_reversion) - Coming Soon
**Best for:** Ranging, oversold/overbought markets  
**Preferred conditions:** RANGING, REVERSAL  
**Avoid conditions:** TRENDING_UP, TRENDING_DOWN

Trades reversals at key support/resistance levels.

### 3. Trend Following (trend_following) - Coming Soon
**Best for:** Strong trending markets  
**Preferred conditions:** TRENDING_UP, TRENDING_DOWN  
**Avoid conditions:** RANGING, HIGH_VOLATILITY

Follows trends using moving averages and momentum.

## Commands

### Strategy Manager Commands

#### List All Strategies
```bash
> strategies list
```
Shows all registered strategies and their status.

#### Start Strategy
```bash
> strategy start overnight_range
> strategy start mean_reversion
```

#### Stop Strategy
```bash
> strategy stop overnight_range
```

#### Start All Strategies
```bash
> strategies start_all
```

#### Stop All Strategies
```bash
> strategies stop_all
```

#### Strategy Status
```bash
> strategy status overnight_range
```
Shows detailed status for a specific strategy:
- Current status (active/idle/paused/error)
- Configuration
- Active positions
- Performance metrics
- Daily trades count

#### Manager Status
```bash
> strategies status
```
Shows overall manager status:
- Auto-selection enabled/disabled
- Active strategies
- Aggregated metrics across all strategies

## Configuration

### Global Settings

```bash
# Maximum strategies running simultaneously
MAX_CONCURRENT_STRATEGIES=3

# Auto-select strategies based on market conditions
AUTO_SELECT_STRATEGIES=false

# How often to check market conditions for auto-selection (seconds)
MARKET_CONDITION_CHECK_INTERVAL=300
```

### Per-Strategy Settings

Each strategy has its own configuration using this pattern:
```bash
{STRATEGY_NAME}_{SETTING}=value
```

**Common settings for all strategies:**

```bash
# Enable/disable
{STRATEGY}_ENABLED=true/false

# Symbols to trade
{STRATEGY}_SYMBOLS=MNQ,MES,ES

# Position limits
{STRATEGY}_MAX_POSITIONS=2
{STRATEGY}_POSITION_SIZE=1

# Risk management
{STRATEGY}_RISK_PERCENT=0.5              # % of account per trade
{STRATEGY}_MAX_DAILY_TRADES=10           # Max trades per day

# Market condition filters
{STRATEGY}_PREFERRED_CONDITIONS=breakout,trending
{STRATEGY}_AVOID_CONDITIONS=high_volatility

# Time windows
{STRATEGY}_START_TIME=09:30
{STRATEGY}_END_TIME=15:45
{STRATEGY}_NO_TRADE_START=15:30
{STRATEGY}_NO_TRADE_END=16:00

# TopStepX compliance
{STRATEGY}_RESPECT_DLL=true
{STRATEGY}_RESPECT_MLL=true
{STRATEGY}_MAX_DLL_USAGE=0.75            # Use max 75% of DLL
```

## Creating a New Strategy

### Step 1: Create Strategy File

```python
# strategies/my_new_strategy.py
from strategy_base import BaseStrategy, MarketCondition
from typing import Dict, Optional

class MyNewStrategy(BaseStrategy):
    """
    Description of what your strategy does.
    """
    
    async def analyze(self, symbol: str) -> Optional[Dict]:
        """
        Analyze market and generate signals.
        """
        # Your analysis logic here
        # Example: Check for specific pattern
        
        # If signal found, return signal dict
        return {
            "action": "LONG",  # or "SHORT" or "CLOSE"
            "symbol": symbol,
            "entry_price": 25300.0,
            "stop_loss": 25250.0,
            "take_profit": 25400.0,
            "confidence": 0.85,
            "reason": "Pattern detected"
        }
        
        # If no signal, return None
        return None
    
    async def execute(self, signal: Dict) -> bool:
        """
        Execute the trading signal.
        """
        # Calculate position size
        pos_size = self.calculate_position_size(
            signal['symbol'],
            signal['entry_price'],
            signal['stop_loss']
        )
        
        # Place order using trading_bot
        result = await self.trading_bot.place_oco_bracket_with_stop_entry(
            symbol=signal['symbol'],
            side=signal['action'],
            quantity=pos_size,
            entry_price=signal['entry_price'],
            stop_loss_price=signal['stop_loss'],
            take_profit_price=signal['take_profit']
        )
        
        if result.get('success'):
            # Track position
            self.active_positions[signal['symbol']] = {
                'entry': signal['entry_price'],
                'stop': signal['stop_loss'],
                'tp': signal['take_profit'],
                'side': signal['action']
            }
            return True
        
        return False
    
    async def manage_positions(self):
        """
        Manage open positions.
        """
        # Check for breakeven, trailing stops, etc.
        for symbol, position in list(self.active_positions.items()):
            # Your position management logic
            pass
    
    async def cleanup(self):
        """
        Clean up resources when strategy stops.
        """
        self.active_positions.clear()
        logger.info(f"Cleaned up {self.config.name}")
    
    def get_market_condition(self, symbol: str) -> MarketCondition:
        """
        Determine market condition for symbol.
        """
        # Your condition detection logic
        # Example: Based on ATR, price action, etc.
        return MarketCondition.TRENDING_UP
```

### Step 2: Register Strategy

```python
# In trading_bot.py or strategy_manager initialization

from strategies.my_new_strategy import MyNewStrategy

# Register the strategy
self.strategy_manager.register_strategy('my_new', MyNewStrategy)
```

### Step 3: Configure in .env

```bash
# Enable the strategy
MY_NEW_ENABLED=true
MY_NEW_SYMBOLS=MNQ,MES
MY_NEW_POSITION_SIZE=1
MY_NEW_PREFERRED_CONDITIONS=trending_up,trending_down
MY_NEW_AVOID_CONDITIONS=ranging
# ... other settings
```

### Step 4: Start Strategy

```bash
> strategy start my_new
```

## Auto-Strategy Selection

When enabled, the system automatically:
1. Analyzes market conditions every 5 minutes (configurable)
2. Scores each strategy based on:
   - Current market condition match
   - Recent performance (win rate, profit factor)
3. Activates top-scoring strategies (up to max concurrent limit)
4. Deactivates strategies no longer suitable

**Enable auto-selection:**
```bash
AUTO_SELECT_STRATEGIES=true
MAX_CONCURRENT_STRATEGIES=3
MARKET_CONDITION_CHECK_INTERVAL=300
```

**How it works:**
```
09:30: Market opens - Condition: BREAKOUT
       → Auto-activates: overnight_range (score: +4)
       
10:00: Market condition: TRENDING_UP
       → Auto-activates: trend_following (score: +5)
       → Deactivates: overnight_range (score: -1)
       
14:00: Market condition: RANGING
       → Auto-activates: mean_reversion (score: +4)
       → Deactivates: trend_following (score: -2)
```

## TopStepX Compliance Features

All strategies automatically:
- ✅ Check DLL before entering trades
- ✅ Check MLL proximity
- ✅ Calculate dynamic position sizes based on account risk
- ✅ Track daily trades
- ✅ Respect time windows
- ✅ Monitor consistency rule (best day < 50% total)

**DLL Protection:**
```python
# If daily loss is -$2,200 and DLL is $3,000:
# Strategy will only risk $200 (75% of $800 remaining)
# This prevents exceeding DLL
```

**MLL Protection:**
```python
# If current balance is close to MLL threshold:
# Strategy pauses trading to prevent account failure
```

## Performance Metrics

Each strategy tracks:
- Total trades
- Win rate
- Total P&L
- Best/worst trade
- Average win/loss
- Profit factor
- Sharpe ratio
- Max drawdown
- DLL/MLL violations
- Consistency ratio

**View metrics:**
```bash
> strategy status overnight_range

📊 Strategy Status: overnight_range
   Status: ✅ Active
   Symbols: MNQ, MES
   Active Positions: 2
   Daily Trades: 4/10
   
   Performance:
     Total Trades: 45
     Win Rate: 62.2%
     Total P&L: $2,456.50
     Profit Factor: 1.82
     Best Trade: $245.00
     Worst Trade: -$98.50
     
   TopStepX Compliance:
     DLL Violations: 0
     MLL Violations: 0
     Consistency Ratio: 42.3%
```

## Best Practices

### 1. Start with One Strategy
Begin with the overnight_range strategy to understand the system.

### 2. Test Thoroughly
Use paper trading accounts first.

### 3. Configure Conservatively
Start with:
- `POSITION_SIZE=1`
- `MAX_DLL_USAGE=0.50` (50%)
- `MAX_DAILY_TRADES=5`

### 4. Monitor Performance
Check `strategy status` regularly.

### 5. Gradual Expansion
Add strategies one at a time, validate performance.

### 6. Use Auto-Selection Carefully
Enable only after all strategies are proven individually.

## Troubleshooting

### Strategy Won't Start
- Check: Is strategy enabled in .env?
- Check: Are symbols valid?
- Check: Is account selected?
- Check: Max concurrent limit reached?

### No Trades Being Placed
- Check: Market condition filters
- Check: Time windows
- Check: DLL/MLL compliance
- Check: Daily trade limit
- Run: `strategy status <name>` for details

### Strategy Stopped Unexpectedly
- Check: `trading_bot.log` for errors
- Check: DLL was exceeded
- Check: Account balance issues

## Example: Full Configuration

```bash
# .env file

# === Global Settings ===
MAX_CONCURRENT_STRATEGIES=2
AUTO_SELECT_STRATEGIES=false

# === Overnight Range Strategy ===
OVERNIGHT_RANGE_ENABLED=true
OVERNIGHT_RANGE_SYMBOLS=MNQ
OVERNIGHT_RANGE_MAX_POSITIONS=1
OVERNIGHT_RANGE_POSITION_SIZE=1
OVERNIGHT_RANGE_RISK_PERCENT=0.5
OVERNIGHT_RANGE_MAX_DAILY_TRADES=4
OVERNIGHT_RANGE_PREFERRED_CONDITIONS=breakout,ranging
OVERNIGHT_RANGE_AVOID_CONDITIONS=high_volatility
OVERNIGHT_RANGE_START_TIME=09:30
OVERNIGHT_RANGE_END_TIME=15:45
OVERNIGHT_RANGE_NO_TRADE_START=15:30
OVERNIGHT_RANGE_NO_TRADE_END=16:00
OVERNIGHT_RANGE_RESPECT_DLL=true
OVERNIGHT_RANGE_MAX_DLL_USAGE=0.75

# === Mean Reversion Strategy (when ready) ===
MEAN_REVERSION_ENABLED=false
MEAN_REVERSION_SYMBOLS=MES
MEAN_REVERSION_MAX_POSITIONS=1
MEAN_REVERSION_POSITION_SIZE=2
MEAN_REVERSION_PREFERRED_CONDITIONS=ranging,reversal
# ... other settings
```

## Summary

The modular strategy system provides:
- ✅ **Flexibility**: Add strategies without touching core code
- ✅ **Safety**: TopStepX compliance built-in
- ✅ **Intelligence**: Auto-select based on market conditions
- ✅ **Control**: Configure each strategy independently
- ✅ **Visibility**: Comprehensive metrics and status
- ✅ **Scalability**: Run multiple strategies simultaneously

**Next Steps:**
1. Configure strategies in `.env`
2. Test with `strategy start <name>`
3. Monitor with `strategy status <name>`
4. Optimize based on performance metrics

---

*For questions or issues, check `trading_bot.log` or run `strategies status` for diagnostics.*

