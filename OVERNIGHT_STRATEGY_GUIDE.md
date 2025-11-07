# Overnight Range Breakout Strategy Guide

## Overview

A fully automated trading strategy that:
1. **Tracks overnight price ranges** (6pm - 9:30am EST by default)
2. **Calculates ATR** for dynamic risk management
3. **Places stop bracket orders** at market open for range breakouts
4. **Monitors for breakeven** to protect winning trades

## Strategy Logic

### Phase 1: Overnight Tracking (6pm - 9:30am EST)
- Tracks highest and lowest prices during overnight session
- Session times fully configurable via environment variables
- Uses 1-minute bars for precise range identification

### Phase 2: ATR Calculation (At Market Open)
- **Current Price ATR**: Calculated from intraday bars (default: 14 periods of 5m bars)
- **Daily ATR**: Calculated from daily bars (default: 14 periods)
- **ATR Zones**: Price ± Daily ATR for target zones

### Phase 3: Order Placement (9:30am EST)
At market open, the strategy automatically:

**LONG Breakout Order:**
- Entry: Overnight High + Offset (default: $0.25)
- Stop Loss: Entry - (Current ATR × 1.25)
- Take Profit: Entry + (Daily ATR × 2.0)
- Type: Stop-market order with OCO brackets

**SHORT Breakout Order:**
- Entry: Overnight Low - Offset (default: $0.25)
- Stop Loss: Entry + (Current ATR × 1.25)
- Take Profit: Entry - (Daily ATR × 2.0)
- Type: Stop-market order with OCO brackets

### Phase 4: Breakeven Management (Optional, Auto-Start/Stop)
- **Optional**: Can be disabled via `BREAKEVEN_ENABLED` env variable
- **Auto-starts** when position opens (stop entry order fills)
- **Auto-stops** when:
  - Position reaches +15 pts profit (moves stop to BE, stops monitoring)
  - Position closes (SL/TP hit, cleans up monitoring)
- Zero overhead when disabled or no positions open

## Commands

### Start Strategy
```
strategy_start [symbols]
```
- Start the overnight range strategy
- Symbols optional (default: from STRATEGY_SYMBOLS env var)
- Example: `strategy_start MNQ MES ES`

### Stop Strategy
```
strategy_stop
```
- Stop the strategy and cancel background tasks
- Does NOT close open positions or orders

### Check Status
```
strategy_status
```
Shows:
- Strategy active status
- Configuration (times, ATR settings, etc.)
- Tracked overnight ranges
- Active orders
- Breakeven monitoring status

### Test Components
```
strategy_test <symbol>
```
Test strategy without placing real orders:
1. ATR calculation
2. Overnight range tracking
3. Breakout order calculation

Example: `strategy_test MNQ`

## Configuration

All settings are configurable via environment variables in `.env`:

### Session Times
```bash
OVERNIGHT_START_TIME=18:00        # 6pm EST - overnight session start
OVERNIGHT_END_TIME=09:30          # 9:30am EST - overnight session end
MARKET_OPEN_TIME=09:30            # 9:30am EST - when to place orders
STRATEGY_TIMEZONE=US/Eastern      # Timezone for all times
```

### ATR Settings
```bash
ATR_PERIOD=14                     # Number of bars for ATR calculation
ATR_TIMEFRAME=5m                  # Timeframe for ATR bars
STOP_ATR_MULTIPLIER=1.25          # Stop distance (1.0-1.5 recommended)
TP_ATR_MULTIPLIER=2.0             # Take profit distance (ATR zones)
```

### Risk Management
```bash
BREAKEVEN_ENABLED=true            # Enable/disable breakeven feature (optional)
BREAKEVEN_PROFIT_POINTS=15.0      # Profit threshold for breakeven stop
RANGE_BREAK_OFFSET=0.25           # Offset from range high/low ($)
STRATEGY_QUANTITY=1               # Position size (contracts)
```

### Trading Symbols
```bash
STRATEGY_SYMBOLS=MNQ,MES          # Comma-separated list of symbols
```

## Example Usage

### 1. Test the Strategy First
```
> strategy_test MNQ

🔬 Testing strategy components for MNQ...

1️⃣ Calculating ATR...
   ✅ Current ATR: 45.25
   ✅ Daily ATR: 68.50
   ✅ ATR Zone High: 21450.75
   ✅ ATR Zone Low: 21313.75

2️⃣ Tracking overnight range...
   ✅ High: 21425.00
   ✅ Low: 21325.00
   ✅ Range Size: 100.00
   ✅ Midpoint: 21375.00

3️⃣ Calculating breakout orders...
   ✅ LONG Order:
      Entry: 21425.25
      Stop:  21368.59
      TP:    21562.25
   ✅ SHORT Order:
      Entry: 21324.75
      Stop:  21381.41
      TP:    21187.75
```

### 2. Start the Strategy
```
> strategy_start

🎯 Starting Overnight Range Breakout Strategy...
   Symbols: MNQ,MES
   Overnight: 18:00 - 09:30
   Market Open: 09:30
   ATR Period: 14 (5m)
   Breakeven: +15.0 pts

   Start strategy? (y/N): y

✅ Strategy started! It will place orders at market open.
```

### 3. Monitor Status
```
> strategy_status

📊 Overnight Range Strategy Status:
   Active: ✅ YES

   Configuration:
     Overnight Session: 18:00 - 09:30
     Market Open: 09:30
     Timezone: US/Eastern
     ATR Period: 14
     Stop Multiplier: 1.25
     TP Multiplier: 2.0
     Breakeven Points: 15.0

   📈 Tracked Ranges:
     MNQ: High=21425.00, Low=21325.00, Range=100.00
     MES: High=5725.50, Low=5695.25, Range=30.25

   📝 Active Orders:
     MNQ: 2 orders
     MES: 2 orders

   🎯 Breakeven Monitoring:
     MNQ LONG: ⏳ Monitoring
     MNQ SHORT: ⏳ Monitoring
```

## How It Works: Timeline

### Previous Day 6:00pm EST
- Strategy begins tracking overnight price action
- Records highest and lowest prices
- Continues tracking through the night

### Current Day 9:30am EST (Market Open)
1. **Calculate overnight range:**
   - MNQ High: 21425.00
   - MNQ Low: 21325.00
   - Range: 100.00 points

2. **Calculate ATR:**
   - Current ATR: 45.25 points
   - Daily ATR: 68.50 points

3. **Place LONG breakout order:**
   - Entry: 21425.25 (high + $0.25)
   - Stop: 21368.59 (entry - 56.66)
   - TP: 21562.25 (entry + 137.00)

4. **Place SHORT breakout order:**
   - Entry: 21324.75 (low - $0.25)
   - Stop: 21381.41 (entry + 56.66)
   - TP: 21187.75 (entry - 137.00)

### During Trading Day
- **Breakeven monitoring** runs every 10 seconds
- If LONG fills and reaches 21440.25 (+15 pts):
  - Move stop to 21425.25 (breakeven)
  - Risk-free trade!

## Risk Management Features

### 1. Dynamic Stop Sizing
- Stops are ATR-based (1.25x by default)
- Adapts to market volatility
- Tighter stops in low volatility
- Wider stops in high volatility

### 2. Breakeven Protection (Optional)
- **Optional feature** - can be disabled via env variable
- **Auto-starts** when position opens (no manual intervention)
- **Auto-stops** after triggering or position closes
- Automatically locks in profit after +15 pts
- Moves stop to entry price (risk-free trade)
- Prevents profitable trades from becoming losers
- Zero overhead when disabled or no positions exist

### 3. Range-Based Entry
- Only trades real overnight range breaks
- Not fake-outs within the range
- Entry offset ensures breakout confirmation

### 4. OCO Brackets
- Stop loss and take profit linked
- One fills, the other cancels automatically
- No risk of double positions

## Advanced Configuration

### Custom Session Times
For different markets or strategies:
```bash
# European session example
OVERNIGHT_START_TIME=14:00        # 2pm EST
OVERNIGHT_END_TIME=08:00          # 8am EST  
MARKET_OPEN_TIME=08:00            # 8am EST
```

### Aggressive Settings
Tighter stops, closer to breakeven:
```bash
STOP_ATR_MULTIPLIER=1.0           # 1.0x ATR stop
BREAKEVEN_PROFIT_POINTS=10.0      # Breakeven at +10 pts
RANGE_BREAK_OFFSET=0.10           # Tighter entry offset
```

### Conservative Settings
Wider stops, more room:
```bash
STOP_ATR_MULTIPLIER=1.5           # 1.5x ATR stop
BREAKEVEN_PROFIT_POINTS=20.0      # Breakeven at +20 pts
RANGE_BREAK_OFFSET=0.50           # More confirmation needed
```

## Breakeven Auto-Start/Stop Behavior

The breakeven monitoring feature is **intelligent and autonomous**:

### When It Auto-Starts
```
09:30am: Place stop bracket orders
         → Orders waiting for entry
         
10:15am: Stop entry order fills (position opened)
         → 🎯 AUTO-START monitoring
         → Log: "Position MNQ LONG opened at 21425.25 - AUTO-STARTED breakeven monitoring"
```

### When It Auto-Stops (Scenario 1: Profit Target)
```
10:15am: Position opened, monitoring active
10:45am: Position reaches +16 pts profit
         → Move stop to breakeven (21425.25)
         → ✅ AUTO-STOP monitoring
         → Log: "Breakeven triggered for MNQ - AUTO-STOPPING monitoring"
         → Position removed from monitoring (cleanup)
```

### When It Auto-Stops (Scenario 2: Position Closed)
```
10:15am: Position opened, monitoring active
10:30am: Take profit hit (position closed)
         → Detect position no longer exists
         → ✅ AUTO-STOP monitoring
         → Log: "Position MNQ closed - auto-stopping breakeven monitoring"
         → Position removed from monitoring (cleanup)
```

### Monitoring States
```
⏸ Waiting for fill           # Order placed, not filled yet
                             # Zero API calls, no overhead

⏳ Monitoring (position filled) # Position open, checking P&L
                               # Active checks every 10 seconds

✓ At Breakeven                # Stop moved to BE, will be removed
                             # Cleanup on next iteration

(removed)                     # Position closed or BE complete
                             # No longer in monitoring dict
```

### Disabling Breakeven
To completely disable the feature:
```bash
# In .env file
BREAKEVEN_ENABLED=false
```

When disabled:
- No monitoring setup at all
- Zero overhead
- Orders still placed with SL/TP
- Just no automatic BE adjustment

## Troubleshooting

### Strategy Won't Start
- Check: Is an account selected? Run `accounts` first
- Check: Is authentication valid? Token might be expired
- Check: Environment variables set correctly in `.env`?

### Orders Not Placing
- Check: Is OCO brackets enabled in TopStepX account?
- Check: Sufficient account balance for margin?
- Check: Symbol valid and tradeable?
- Run `strategy_test <symbol>` to debug

### ATR Calculation Fails
- Check: Historical data available for symbol?
- Check: Correct contract ID format (e.g., `MNQ`, not `MNQZ5`)?
- Run `history <symbol> 5m 15` to verify data access

### Breakeven Not Triggering
- Check: Position actually in profit by +15 pts?
- Check: Strategy still running? Run `strategy_status`
- Note: Breakeven monitoring runs every 10 seconds

## Performance Tips

### 1. Multiple Symbols
Strategy can trade multiple symbols simultaneously:
```
strategy_start MNQ MES ES NQ
```

### 2. Optimal Timeframes
- ATR calculation: 5m bars work well (balance speed/accuracy)
- Range tracking: 1m bars ensure precise high/low

### 3. Symbol Selection
Best for:
- High liquidity futures (MNQ, MES, ES, NQ)
- Instruments with clear overnight ranges
- Markets with consistent 9:30am EST open activity

## Integration with Existing Bot

The strategy is fully integrated but **independent**:
- Does NOT interfere with webhook orders
- Does NOT conflict with manual trades
- Runs autonomously in background
- Can be started/stopped anytime

You can:
- Run strategy + webhook server simultaneously
- Place manual trades while strategy active
- Use other bot commands while strategy runs

## Safety Notes

⚠️ **Important Considerations:**

1. **Test First**: Always use `strategy_test` before live trading
2. **Paper Trade**: Test with paper account before real money
3. **Position Sizing**: Start with STRATEGY_QUANTITY=1
4. **Monitor Initially**: Watch first few days to ensure proper operation
5. **OCO Brackets**: Ensure enabled in TopStepX account settings

## Logging

All strategy activity logged to `trading_bot.log`:
```
2025-11-07 09:30:00 - INFO - 🔔 Market open! Executing overnight range break strategy...
2025-11-07 09:30:01 - INFO - 📊 Processing MNQ...
2025-11-07 09:30:02 - INFO - 📊 Overnight range for MNQ: High=21425.00, Low=21325.00, Range=100.00
2025-11-07 09:30:03 - INFO - 🎯 Range break orders for MNQ:
2025-11-07 09:30:03 - INFO -    LONG: Entry=21425.25, SL=21368.59, TP=21562.25
2025-11-07 09:30:03 - INFO -    SHORT: Entry=21324.75, SL=21381.41, TP=21187.75
2025-11-07 09:30:05 - INFO - ✅ Long breakout order placed: 1234567
2025-11-07 09:30:06 - INFO - ✅ Short breakout order placed: 1234568
2025-11-07 09:35:15 - INFO - 🎯 Position MNQ reached +15.50 pts profit - moving stop to breakeven!
```

## Future Enhancements

Potential additions (user can request):
- Multiple timeframe ATR confirmation
- Volume profile integration
- News event filtering
- Dynamic position sizing based on range size
- Trailing stop beyond breakeven
- Partial profit taking at milestones

## Summary

The overnight range breakout strategy is a **complete, automated trading system** that:
- ✅ Handles all aspects of the trade lifecycle
- ✅ Adapts to market volatility via ATR
- ✅ Protects profits with breakeven stops
- ✅ Requires zero manual intervention once started
- ✅ Fully configurable via environment variables

**Key Advantage**: Removes need for webhook/TradingView signals - the bot makes all trading decisions autonomously based on proven range breakout logic.

---

*For questions or issues, check `trading_bot.log` or run `strategy_test` for detailed diagnostics.*

