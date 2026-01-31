# Dynamic ATR & Enhanced Profit Targets

## Overview

These features make the strategy more adaptive to current market conditions:

1. **Dynamic ATR for Stops**: Recalculates ATR at order placement time for more responsive stop-losses
2. **Previous Day Zone Targeting**: Uses previous day's ATR zones for better profit targets when current zones overlap

---

## 1. Dynamic ATR for Order Placement

### Problem Solved
Previously, ATR was calculated once (at market open) and cached. If volatility changed during the session, stop-losses might be too wide or too tight.

### Solution
When `USE_DYNAMIC_ATR_FOR_ORDERS=true`, the strategy:
- Keeps cached daily ATR and zones (stable reference points)
- Recalculates **current (intraday) ATR** at order placement time
- Uses fresh ATR for stop-loss calculations

### Configuration

```bash
# In .env
USE_DYNAMIC_ATR_FOR_ORDERS=true  # Recalc current ATR at order time (default: true)
ATR_PERIOD=14                     # Number of bars for ATR calculation
ATR_TIMEFRAME=5m                  # Timeframe for current ATR
ATR_HISTORY_BARS=200              # Bars to fetch for Wilder's smoothing
STOP_ATR_MULTIPLIER=1.25          # Stop distance = current_atr * multiplier
```

### How It Works

**Market Open (cached calculation):**
```python
atr_data = await strategy.calculate_atr("MNQ")
# Returns: ATRData with current_atr=17.35, daily_atr=351.82, zones
```

**Order Placement (dynamic recalculation):**
```python
# Recalculates ONLY current_atr using latest bars
dynamic_atr = await strategy.recalculate_current_atr("MNQ")
# Returns: 18.42 (volatility increased since market open)

# Stop distance adapts: 18.42 * 1.25 = 23.03 pts
```

### Benefits

- **Tighter stops** when volatility decreases → better risk management
- **Wider stops** when volatility increases → avoid premature stops
- **Responsive** to intraday volatility changes
- **Stable zones** (daily ATR still cached)

### Logging

```
2026-01-26 09:45:12 - INFO - 🔄 Recalculated dynamic current ATR for MNQ (5m): 18.42
2026-01-26 09:45:12 - INFO - Using dynamic ATR 18.42 (vs cached 17.35) for stop-loss
```

---

## 2. Previous Day Zone Targeting

### Problem Solved
When current ATR zones overlap with the overnight range, the default behavior uses `2 * current_atr` for profit targets. This can be conservative and may miss better targets.

### Solution
The strategy now:
1. Calculates previous day's ATR zones
2. Compares them with the default `2*ATR` target
3. Uses the **farther** (more profitable) target

### How It Works

**Scenario: Upper zone overlaps with overnight range**

Current logic:
```
Overnight Range: [25540, 25732]
Current Upper Zone: [25661, 25682] (overlaps)
Default TP: Entry + 2*ATR = 25732 + 34 = 25766
```

Enhanced logic:
```
Previous Day Upper Zone: [25745, 25768]
Previous Zone Midpoint: 25756

Comparison:
- Default: 25766
- Previous: 25756
→ Use 25766 (default is better)

OR if previous was [25790, 25815]:
Previous Zone Midpoint: 25802
→ Use 25802 (previous day zone is farther)
```

### Algorithm

```python
if current_zone_overlaps_range:
    default_tp = entry + (2 * current_atr)
    
    prev_zones = get_previous_day_atr_zones()
    if prev_zones:
        prev_midpoint = prev_zones['upper']['midpoint']
        
        # Use previous zone if:
        # 1. It's beyond the current range
        # 2. It's farther than default target
        if prev_midpoint > range.high and prev_midpoint > default_tp:
            tp = prev_midpoint
        else:
            tp = default_tp
```

### Configuration

This feature is **always enabled** when zones overlap. No configuration needed.

### Logging

```
2026-01-26 09:45:15 - DEBUG - Previous day (2026-01-22) zones: Upper=[25745.21, 25768.96], Lower=[25464.54, 25485.29]
2026-01-26 09:45:15 - DEBUG - Using previous day's upper zone midpoint 25802.50 (better than 2*ATR 25766.00)
```

### Benefits

- **Better risk/reward** when previous day's zones are farther
- **Automatic optimization** based on recent price action
- **Market structure awareness** (respects previous day's key levels)
- **Conservative fallback** (always uses at least 2*ATR)

---

## 3. Zone Anchor Time for MOR Compatibility

### Related Configuration

For futures compatibility with MOR.pine (separate feature):

```bash
ZONE_ANCHOR_TIME=08:30           # Use 8:30 bar for zones (MOR futures)
MARKET_OPEN_TIME=09:30           # RTH open for timing
OVERNIGHT_ZONE_OPEN_SOURCE=30m   # Use 30m bar (matches MOR)
```

This affects the **anchor point** for ATR zones but works with dynamic ATR and previous day targeting.

---

## Testing

### Test Dynamic ATR

```bash
# Monitor order placement logs
python trading_bot.py --command="strategy_start MNQ overnight_range"

# Look for:
# "🔄 Recalculated dynamic current ATR for MNQ (5m): X.XX"
# "Using dynamic ATR X.XX (vs cached Y.YY) for stop-loss"
```

### Test Previous Day Targeting

```bash
# Run analyze_date for a day when zones overlap
python trading_bot.py --command="analyze_date MNQ 2026-01-23 --timeframe=5m"

# Look for:
# "Using previous day's upper zone midpoint X.XX (better than 2*ATR Y.YY)"
# OR
# "Using 2*current_atr for TP (prev zone not better: X.XX)"
```

### Disable Dynamic ATR (for testing)

```bash
# In .env
USE_DYNAMIC_ATR_FOR_ORDERS=false  # Use cached ATR for orders
```

---

## Performance Considerations

### API Calls

**Dynamic ATR:**
- Adds 1 API call per order placement (fetches ~200 bars of 5m data)
- Cached daily ATR still uses session-aligned 1h fetch (no extra calls)
- Total: ~1-2 extra calls per symbol when placing orders

**Previous Day Zones:**
- Adds 1-2 API calls per order calculation (30m + 1m bars around previous market open)
- Only called when zones overlap (not every trade)
- Minimal impact

### Timing

- Dynamic ATR calculation: ~50-100ms
- Previous zone calculation: ~100-200ms
- Total added latency: ~150-300ms per order calculation

This is acceptable for breakout orders (not millisecond-sensitive).

---

## Troubleshooting

### Dynamic ATR not updating

```bash
# Check logs for:
"🔄 Recalculated dynamic current ATR..."

# If missing, verify:
echo $USE_DYNAMIC_ATR_FOR_ORDERS  # Should be "true"

# Or check .env:
grep USE_DYNAMIC_ATR_FOR_ORDERS .env
```

### Previous day zones not found

```bash
# Check logs:
"Could not find previous day's market open price for MNQ"

# Common causes:
# 1. Market was closed previous day (weekend/holiday)
# 2. Insufficient historical data
# 3. ZONE_ANCHOR_TIME mismatch

# Fallback: Strategy uses 2*ATR (safe default)
```

### Unexpected profit targets

```bash
# Enable debug logging:
export LOG_LEVEL=DEBUG
python trading_bot.py --command="analyze_date MNQ 2026-01-23 --timeframe=5m"

# Check:
# - Previous day zone levels
# - Comparison logic
# - Final TP selection
```

---

## Summary

| Feature | Purpose | Impact | Enabled By |
|---------|---------|--------|------------|
| **Dynamic ATR** | Adaptive stops | Better risk management | `USE_DYNAMIC_ATR_FOR_ORDERS=true` |
| **Previous Day Zones** | Better profits | Improved R:R when zones overlap | Always (automatic) |
| **Zone Anchor Time** | MOR compatibility | Matches TradingView levels | `ZONE_ANCHOR_TIME=08:30` |

All features work together to create a more adaptive and profitable trading strategy! 🚀
