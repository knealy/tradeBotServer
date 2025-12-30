# Sub-1-Minute Timeframe Fix - December 2, 2025

## 🐛 Issue: Sub-1-Minute Timeframes Not Showing Recent Data

### Problem
Sub-1-minute timeframes (5s, 15s, 30s) were not displaying data up to the current time. Data would end at around 12:45 Dec 1, even though it was 5:05 Dec 2 - a significant gap of ~16 hours.

### Root Cause
The cache logic was allowing sub-1-minute timeframes to use stale cached data. Unlike timeframes > 1m (which use 1m aggregation and bypass cache), sub-1-minute timeframes were:
1. Using cached data that was old
2. Not forcing fresh API requests to get current data
3. Cache checks were happening before the "fresh data" flag was properly set

### Fix Applied

**File**: `trading_bot.py` (lines 6321-6327)

```python
# Before: Only bypassed cache for small limits (<= 5 bars)
short_timeframes = ['1s', '5s', '10s', '15s', '30s', '1m', '2m', '3m', '5m', '10m', '15m']
if limit <= 5 and timeframe in short_timeframes:
    use_fresh_data = True

# After: ALWAYS bypass cache for sub-1-minute timeframes
sub_minute_timeframes = ['1s', '5s', '10s', '15s', '30s']

# Sub-1-minute timeframes always need fresh data to show current time
if timeframe in sub_minute_timeframes:
    use_fresh_data = True
    logger.info(f"📊 Sub-1-minute timeframe ({timeframe}) - bypassing cache to get fresh data up to current time")
```

### Why This Works
1. **Forces Fresh API Calls**: Sub-1-minute timeframes now always bypass cache, ensuring the API is called with current time
2. **Includes Partial Bars**: The API request includes `includePartialBar: True`, which returns the current incomplete bar
3. **Current Time End**: The `end_time` parameter defaults to `datetime.now(timezone.utc)`, ensuring we get data up to the current moment

### Expected Behavior
- **5s, 15s, 30s timeframes**: Always fetch fresh data from API
- **Data includes**: Current incomplete bar (partial bar)
- **No cache**: Sub-1-minute timeframes never use cached data
- **Up-to-date**: Charts show data up to the current second

## 🕐 Countdown Timer Fix

### Problem
The countdown timer was showing remaining time until next bar, but should start at the full timeframe duration and count down.

### Example
For a **5-minute timeframe**:
- **Before**: Shows `4:23` (remaining time)
- **After**: Shows `5:00` → `4:59` → `4:58` → ... → `0:01` → `0:00` → `5:00` (resets)

### Fix Applied

**File**: `frontend/src/components/TradingChart.tsx` (lines 618-653, 700-730)

**Countdown Calculation**:
```typescript
// Calculate how much time has elapsed in the current bar period
const currentBarStart = Math.floor(now / intervalMs) * intervalMs
const elapsedInCurrentBar = now - currentBarStart

// Calculate remaining time in current bar (countdown from full duration)
const remainingMs = intervalMs - elapsedInCurrentBar
const remainingSeconds = Math.max(0, Math.floor(remainingMs / 1000))

// Format as MM:SS
const minutes = Math.floor(remainingSeconds / 60)
const secs = remainingSeconds % 60
return `${minutes}:${secs.toString().padStart(2, '0')}`
```

### How It Works
1. **Current Bar Start**: Calculates when the current bar period started
2. **Elapsed Time**: Determines how much time has passed in the current bar
3. **Remaining Time**: Subtracts elapsed from full duration
4. **Countdown**: Displays remaining time, starting from full duration (e.g., `5:00` for 5m)

### Display Format
- **5m timeframe**: `5:00` → `4:59` → ... → `0:00`
- **15s timeframe**: `0:15` → `0:14` → ... → `0:00`
- **1h timeframe**: `1:00:00` (if we add hours) or `60:00` → `59:59` → ... → `0:00`

## 📁 Files Modified

1. **`trading_bot.py`**
   - Added sub-1-minute timeframe detection
   - Force cache bypass for sub-1-minute timeframes
   - Ensures fresh API calls with current time

2. **`frontend/src/components/TradingChart.tsx`**
   - Fixed countdown timer calculation
   - Now starts at full timeframe duration
   - Counts down every second

## ✅ Testing

### Sub-1-Minute Timeframes
1. Select `15s` timeframe
2. Verify data shows up to current time
3. Check that latest bar timestamp is within last 15 seconds
4. Refresh chart - should still show current data

### Countdown Timer
1. Select `5m` timeframe
2. Verify timer shows `5:00` at start of new bar
3. Watch it count down: `5:00` → `4:59` → `4:58` → ...
4. Verify it resets to `5:00` when new bar starts

## 🎯 Impact

- **Sub-1-minute charts**: Now show real-time data up to current second
- **Countdown timer**: Provides clear visual feedback of bar progress
- **User experience**: Better real-time monitoring for scalping strategies

