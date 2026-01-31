# MOR.pine Compatibility Guide

This guide explains how to configure `overnight_range_strategy.py` to match TradingView's MOR.pine indicator for futures trading.

## Key Differences: MOR.pine vs Default Config

### 1. Zone Anchor Time (Market Open)

**MOR.pine behavior for futures:**
```pine
if isFutures
    openHour := openHour - 1  // 9:30 becomes 8:30
```

MOR uses the **8:30 AM bar** (not 9:30 AM) as the anchor point for calculating daily ATR zones on futures instruments.

**Configuration:**
```bash
# In .env
ZONE_ANCHOR_TIME=08:30           # Use 8:30 for MOR compatibility (futures)
MARKET_OPEN_TIME=09:30           # Keep 9:30 for RTH open timing/scanner
OVERNIGHT_ZONE_OPEN_SOURCE=30m   # Use 30m bar (matches MOR)
```

### 2. Daily ATR Calculation

**MOR.pine:**
```pine
daily_atr = request.security(syminfo.tickerid, "D", ta.atr(14))
```

Uses TradingView's "D" timeframe for futures, which:
- Rolls at 18:00 ET (17:00 CT for CME)
- Includes the current incomplete bar
- Uses TradingView's data feed

**Our implementation:**
- ✅ Uses 18:00 ET session roll (matches MOR)
- ✅ Includes current incomplete day (matches MOR)
- ✅ Uses Wilder's smoothing/ta.atr() formula (matches MOR)
- ⚠️  Uses TopStepX API data (may differ from TradingView)

**Expected differences:**
- Daily ATR may vary by 5-10% due to data source differences
- Example: Our API gives 351.82, TradingView shows 374.98 for MNQ 2026-01-23

### 3. Zone Calculation Formula

**Both use identical formulas:**
```
day_dist = daily_atr * 0.5
day_bull_price = open + day_dist * 0.5    // Upper zone lower bound
day_bull_price1 = open + day_dist * 0.618  // Upper zone upper bound
day_bear_price = open - day_dist * 0.5    // Lower zone upper bound  
day_bear_price1 = open - day_dist * 0.618  // Lower zone lower bound
```

## Complete MOR-Compatible Configuration

Add to `.env`:
```bash
# Session times (18:00 ET daily roll)
DAILY_BAR_ROLL_TIME=18:00
DAILY_ATR_SESSION_ALIGNED=true

# Overnight range tracking
OVERNIGHT_START_TIME=18:00
OVERNIGHT_END_TIME=09:30

# Market timing
MARKET_OPEN_TIME=09:30          # RTH open for scanner/timing
ZONE_ANCHOR_TIME=08:30          # Use 8:30 bar for zones (MOR futures compat)

# Zone configuration
OVERNIGHT_ZONE_OPEN_SOURCE=30m  # Use 30m bar (matches MOR)
ATR_ZONE_NEAR_MULT=0.5          # Inner zone multiplier
ATR_ZONE_FAR_MULT=0.618         # Outer zone multiplier (golden ratio)
```

## Testing Your Configuration

```bash
python trading_bot.py --command="analyze_date MNQ 2026-01-23 --timeframe=5m"
```

**Expected results (with ZONE_ANCHOR_TIME=08:30):**
- Zones should align closely with MOR.pine display
- Daily ATR may still differ slightly (data source)
- Market open price should match MOR's 8:30 bar

## Data Source Differences

The remaining ATR discrepancy (e.g. 351 vs 374) comes from:

1. **Different data feeds**: TradingView vs TopStepX API
2. **Session coverage**: TradingView may include more complete overnight data
3. **Bar alignment**: Subtle differences in 1h bar timestamps

**Recommendation**: For live trading, verify zones visually against TradingView. If critical precision is needed, you can manually override the daily ATR in your strategy config.

## Verification Checklist

- [ ] Set `ZONE_ANCHOR_TIME=08:30` in `.env`
- [ ] Set `OVERNIGHT_ZONE_OPEN_SOURCE=30m` in `.env`
- [ ] Set `DAILY_ATR_SESSION_ALIGNED=true` in `.env`
- [ ] Run `analyze_date` and compare zones with TradingView
- [ ] Check that market_open price uses 8:30 bar (see logs)
- [ ] Verify overnight range times (18:00-09:30)

## Notes

- The `MARKET_OPEN_TIME` setting (9:30) is still used for the market open scanner and timing checks
- Only the **zone calculation** uses `ZONE_ANCHOR_TIME` (8:30 for MOR compat)
- This separation allows proper timing while matching MOR's zone levels
