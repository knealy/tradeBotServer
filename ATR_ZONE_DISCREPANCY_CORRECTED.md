# Daily ATR Zone Discrepancy - CORRECTED Analysis
## Date: 2026-01-29
## Test Date: 2026-01-23 (MNQ)

## Calculation Verification

### Given MOR.pine Zones:
- Upper: 25700.75 - 25722.5
- Lower: 25490.75 - 25513.5

### Working Backward to Find Required Values:

**From upper zone lower bound (open + day_dist × 0.5):**
```
25700.75 = 25607.0 + day_dist × 0.5
day_dist = 187.50
```

**From upper zone upper bound (open + day_dist × 0.618):**
```
25722.5 = 25607.0 + day_dist × 0.618
day_dist = 186.89
```

**Average day_dist: 187.20**
**Required daily_atr = day_dist × 2 = 374.39**

## Root Cause Identified

✅ **market_open_price = 25607.0 is CORRECT** (9:30 AM bar open)

❌ **daily_atr = 351.82 is TOO LOW**
- Should be: ~374.39
- Actual: 351.82
- Difference: **22.57 points (6.4% lower)**

This 6.4% lower ATR causes **both zones to be ~6 points off**:
- Upper zone ~6 points too low
- Lower zone ~6 points too high (less downside room)

## Why is Python Daily ATR Lower?

### Python Strategy Calculation:
- Uses **session-aligned daily bars** (18:00 ET to 18:00 ET)
- Built from 1h bars aggregated into daily sessions
- Period: 15 days (2026-01-05 to 2026-01-23)
- Result: **351.82**

### MOR.pine Calculation:
- Uses `request.security(syminfo.tickerid, "D", ta.atr(14))`
- TradingView's **native daily bars** (exchange-defined)
- Same 14-period ATR with Wilder's smoothing
- Result: **~374.39** (calculated from zones)

### Possible Causes of 22.57 Point Difference:

1. **Different Daily Bar Definition:**
   - TradingView "D" may use **midnight-to-midnight UTC** or **exchange hours** (9:30-16:00 ET)
   - Python uses **18:00-to-18:00 ET** (overnight session aligned)
   - Different bar times = different OHLC = different True Range

2. **Data Source Differences:**
   - TradingView: Native exchange data feed
   - TopStepX: API data that may have slight differences

3. **Bar Count or Date Range:**
   - Both should use 14 periods, but may include/exclude different days
   - Contract rollover handling could differ

4. **Incomplete Bar Handling:**
   - Python strategy may be including/excluding partial bars differently

## The "daily_bar.open" JSON Issue

**SEPARATE ISSUE - Terminology confusion:**
- `daily_bar.open` in JSON shows: 25607.0
- But actual daily bar open (18:00 on Jan 22): **25609.75**

This is because:
- **Zone anchor price** (market open @ 9:30): 25607.0 ← used for zones ✓
- **Daily bar open** (session start @ 18:00): 25609.75 ← different concept

The JSON output incorrectly labels the zone anchor as "daily_bar.open" when it should be "zone_anchor_price" or "market_open_price".

## Solutions

### Solution 1: Match TradingView Daily Bar Definition
```python
# Instead of session-aligned 18:00-18:00 bars, try using:
# - Midnight-to-midnight UTC bars
# - Or 9:30-16:00 ET bars (regular trading hours)
```

### Solution 2: Validate Against TradingView Data Export
1. Export TradingView daily bars for MNQ (Jan 5-23)
2. Compare OHLC values with TopStepX data
3. Calculate ATR(14) manually to verify which data source is correct

### Solution 3: Use TradingView Daily ATR as Override
```python
# Add environment variable:
DAILY_ATR_OVERRIDE=374.39  # From TradingView for specific date

# Or fetch from external source that matches TV
```

### Solution 4: Fix JSON Output Terminology
```python
# In core/cli_command_parser.py line 1160-1162:
"market_open": {
    "price": round(atr_data.market_open_price, 2),
},
"daily_bar": {
    "open": round(range_data.open, 2),  # Use overnight_range.open (18:00)
},
"zone_anchor": {
    "price": round(atr_data.market_open_price, 2),  # What's used for zones
    "time": "09:30 ET"
}
```

## Testing Needed

### Test 1: Export and Compare Daily Bars
```bash
# 1. Export from TradingView: 
#    Add to chart: Daily ATR(14) indicator
#    Export daily bars CSV for Jan 5-23

# 2. Compare with Python session-aligned bars
python trading_bot.py --command="history MNQ 1d 20 --start-date=2026-01-05 --end-date=2026-01-23"
```

### Test 2: Try Different Daily Bar Definitions
```python
# Test with regular trading hours (9:30-16:00) instead of overnight-aligned
# May need to modify session-aligned daily ATR calculation
```

### Test 3: Manual ATR Calculation
Calculate ATR(14) manually from TradingView's exported data to verify the ~374 value.

## Summary

**CORRECT:**
- ✅ market_open_price = 25607.0 (9:30 bar)
- ✅ Zone calculation formula (day_dist × 0.5, day_dist × 0.618)
- ✅ Zone anchor at market open (not 8:30, not 18:00)

**ISSUE:**
- ❌ Daily ATR calculation: 351.82 vs ~374.39 required
- ❌ This 6.4% difference causes ~6 point zone offset
- ❌ Likely due to different daily bar definitions (session-aligned vs exchange hours)

**COSMETIC:**
- ⚠️ JSON shows "daily_bar.open = 25607.0" but should show 25609.75 (actual 18:00 open)
- This is just labeling - zones are calculated correctly using 25607.0

## Next Steps

1. Export TradingView daily data and compare with TopStepX
2. Identify exact daily bar definition TradingView uses
3. Modify Python to match that definition OR document the acceptable variance
4. Fix JSON output terminology to clarify zone_anchor vs daily_bar.open
