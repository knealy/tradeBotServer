# Chart Fixes - December 2, 2025

## 🐛 Issues Fixed

### 1. ✅ Real-Time Bar Updates Not Working

**Problem:**
- Only 2m timeframe showed recent data (20-30 minutes lag)
- Other timeframes were way off current time
- Bars seemed to be generated once on initial load and never updated

**Root Causes:**
1. Bar aggregator only supported limited timeframes (`1m`, `5m`, `15m`, `1h`)
2. Frontend was too strict about timeframe matching in WebSocket updates
3. Bar aggregator only initialized default timeframes, not all requested ones

**Fixes Applied:**

#### A. Backend - Bar Aggregator (`core/bar_aggregator.py`)
- ✅ **Expanded default timeframes** to include all chart timeframes:
  ```python
  # Before: ['1m', '5m', '15m', '1h']
  # After: ['5s', '15s', '30s', '1m', '2m', '5m', '15m', '30m', '1h']
  ```
- ✅ **Enhanced symbol initialization** to always register all default timeframes:
  ```python
  # Ensures all timeframes are built when quotes arrive
  for tf in self.default_timeframes:
      self.symbol_timeframes[symbol_key].add(normalized)
      if normalized not in self.bar_builders[symbol_key]:
          # Initialize builder
  ```

#### B. Frontend - TradingChart (`frontend/src/components/TradingChart.tsx`)
- ✅ **Relaxed timeframe matching** in WebSocket updates:
  ```typescript
  // Before: Strict match required
  if (updateTimeframe !== currentTimeframe) return
  
  // After: Accept updates without timeframe or matching timeframe
  if (updateTimeframe && updateTimeframe !== currentTimeframe) {
    return  // Only skip if timeframe is specified and doesn't match
  }
  ```
- ✅ **Added timeframe subscription** request:
  ```typescript
  wsService.emit('subscribe', {
    types: ['market_update'],
    symbol: symbol.toUpperCase(),
    timeframe: timeframe.toLowerCase(),
  })
  ```

**Expected Result:**
- All timeframes (5s, 15s, 30s, 2m, 5m, 15m, 30m, 1h) should receive real-time updates
- Bars should update every 200ms (5 times per second)
- Current forming candle should update in real-time
- New candles should be created when timeframe period ends

---

### 2. ✅ OHLC Display Shows Latest Bar Instead of Crosshair Bar

**Problem:**
- OHLC values always showed the most recent bar
- Should show values for the bar under the crosshair cursor

**Fix Applied:**
- ✅ **Added crosshair subscription** to track hovered bar:
  ```typescript
  const [hoveredBar, setHoveredBar] = useState<HistoricalBar | null>(null)
  
  chart.subscribeCrosshairMove((param) => {
    const candlestickData = param.seriesData.get(candlestickSeries)
    if (candlestickData) {
      // Find matching bar or use candlestick data
      setHoveredBar(matchingBar || candlestickData)
    } else {
      setHoveredBar(null)
    }
  })
  ```
- ✅ **Updated OHLC display** to use hovered bar:
  ```typescript
  // Use hovered bar if crosshair is over a bar, otherwise use latest bar
  const displayBar = hoveredBar || data.bars[data.bars.length - 1]
  ```

**Expected Result:**
- OHLC values update as you move crosshair over different bars
- Shows latest bar when crosshair is not over any bar
- Real-time updates continue to work

---

### 3. ✅ Moving Average Values Showing on Price Scale

**Problem:**
- MA values were displayed on the right price scale
- Cluttered the price scale with unnecessary information

**Fix Applied:**
- ✅ **Set `lastValueVisible: false`** for all MA series:
  ```typescript
  const maLine = newChart.addSeries(LineSeries, {
    color: config.color,
    lineWidth: 1,
    title: config.label,
    priceLineVisible: false,
    lastValueVisible: false, // Hide MA values from price scale
  })
  ```

**Expected Result:**
- MA lines still visible on chart
- MA values no longer clutter price scale
- Cleaner, more professional appearance

---

### 4. ✅ Dates Not Showing on Time Axis

**Problem:**
- Time axis only showed time (HH:MM)
- No date information for longer timeframes

**Fix Applied:**
- ✅ **Enhanced time formatter** to show dates for longer timeframes:
  ```typescript
  const showDate = ['1h', '4h', '1d'].includes(timeframe) || 
                  (timeframe.includes('m') && parseInt(timeframe) >= 30)
  
  if (showDate) {
    // Show: "Dec 2, 14:30"
    formatterOptions.month = 'short'
    formatterOptions.day = 'numeric'
    formatterOptions.hour = '2-digit'
    formatterOptions.minute = '2-digit'
  } else {
    // Show: "14:30"
    formatterOptions.hour = '2-digit'
    formatterOptions.minute = '2-digit'
  }
  ```

**Expected Result:**
- Short timeframes (5s-15m): Show time only (e.g., "14:30")
- Longer timeframes (30m+): Show date + time (e.g., "Dec 2, 14:30")
- Better context for historical data

---

## 📊 Summary of Changes

### Files Modified

1. **`frontend/src/components/TradingChart.tsx`**
   - Added seconds timeframes (5s, 15s, 30s) and missing minutes (2m, 30m)
   - Added crosshair subscription for OHLC display
   - Set `lastValueVisible: false` for MA series
   - Enhanced time formatter to show dates
   - Relaxed WebSocket update timeframe matching
   - Added timeframe subscription request

2. **`core/bar_aggregator.py`**
   - Expanded default timeframes to include all chart timeframes
   - Enhanced symbol initialization to register all default timeframes
   - Ensures bars are built for all timeframes when quotes arrive

---

## 🧪 Testing Checklist

### Real-Time Updates
- [ ] All timeframes (5s, 15s, 30s, 2m, 5m, 15m, 30m, 1h) show recent data
- [ ] Current forming candle updates every 200ms
- [ ] Bars are up-to-date (within seconds of current time)
- [ ] New candles are created when timeframe period ends

### OHLC Display
- [ ] OHLC values change when hovering over different bars
- [ ] Shows latest bar when crosshair is not over any bar
- [ ] Values match the bar under crosshair

### Moving Averages
- [ ] MA lines visible on chart
- [ ] MA values NOT shown on price scale
- [ ] MA toggle works (show/hide)

### Time Axis
- [ ] Short timeframes show time only (HH:MM)
- [ ] Longer timeframes (30m+) show date + time (Dec 2, HH:MM)
- [ ] Time zone is correct (ET/EST)

---

## 🔍 Debugging Tips

### If Real-Time Updates Still Not Working:

1. **Check Backend Logs:**
   ```bash
   # Look for bar aggregator messages
   grep "Broadcasted.*bar update" logs
   grep "Subscribed.*to timeframes" logs
   ```

2. **Check WebSocket Connection:**
   - Open browser DevTools → Network → WS
   - Verify WebSocket is connected
   - Check for `market_update` messages

3. **Check Frontend Console:**
   ```javascript
   // Should see these logs:
   [TradingChart] Chart ready for MNQ 5m - listening for real-time bar updates
   [TradingChart] Updating chart data { barCount: 300, symbol: 'MNQ', timeframe: '5m' }
   ```

4. **Verify Bar Aggregator is Running:**
   ```bash
   # Check if bar aggregator started
   grep "Bar aggregator started" logs
   ```

5. **Check SignalR Quotes:**
   ```bash
   # Verify quotes are flowing
   grep "Quote received for" logs
   ```

---

## 📝 Notes

- Bar aggregator broadcasts updates every **200ms** (5 times per second)
- Updates only sent for bars updated within last **2 seconds** (prevents stale data)
- All timeframes are automatically initialized when first quote arrives for a symbol
- Frontend subscribes to specific timeframe but accepts updates for all timeframes

---

**Created:** December 2, 2025  
**Status:** All fixes applied ✅  
**Testing:** Pending user verification

