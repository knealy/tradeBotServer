# Chart Diagnostics - December 2, 2025

## 🔍 Issues Found in Logs

### 1. ❌ Bar Aggregator Not Starting
**Expected log message:**
```
✅ Bar aggregator started - real-time chart updates enabled
📊 Bar aggregator started - tracking 9 timeframes: 5s, 15s, 30s, 1m, 2m, 5m, 15m, 30m, 1h
```

**Actual logs:** No such messages found.

**Impact:** Real-time bar updates won't work without the bar aggregator running.

**Fix Applied:**
- Enhanced logging in `bar_aggregator.start()` to show which timeframes are being tracked
- Added warning if aggregator is already running

---

### 2. ❌ SignalR Not Receiving Quotes
**Expected log messages:**
```
📈 Quote #1 for MNQ: $25139.25 (vol: 246796) → bar aggregator
📈 Quote #2 for MNQ: $25140.00 (vol: 246800) → bar aggregator
```

**Actual logs:** No quote messages found.

**Impact:** No quotes = no bar updates = no real-time chart updates.

**Possible causes:**
- SignalR connection not established
- SignalR authentication failing
- Market closed (no quotes available)

**Fix Applied:**
- Enhanced quote logging to show first 5 quotes per symbol
- Added confirmation message after 5 quotes to reduce log spam

---

### 3. ✅ Historical Data End Time Fix
**Problem:** Historical data API was using `_get_last_market_close()` as default end_time, which returns the last market close time (not current time).

**Why only 2M and 30M showed recent data:**
- These timeframes might have been cached with more recent data
- Or the API might return different data ranges for different timeframes
- Or those timeframes were requested with explicit end_time

**Fix Applied:**
- Changed default `end_time` to use **current time** (`datetime.now(timezone.utc)`) when not provided
- This ensures all timeframes get data up to the current moment
- Applied in both `async_webhook_server.py` and `dashboard.py`

**Files Modified:**
- `servers/async_webhook_server.py` - `handle_get_historical_data()`
- `servers/dashboard.py` - `get_historical_data()`

---

## 📊 What to Check Next

### Step 1: Verify Bar Aggregator Starts
After restarting the server, look for:
```
✅ Bar aggregator started - real-time chart updates enabled
📊 Bar aggregator started - tracking 9 timeframes: 5s, 15s, 30s, 1m, 2m, 5m, 15m, 30m, 1h
```

**If missing:**
- Check if `trading_bot.bar_aggregator` exists
- Check if `async_webhook_server.run()` is being called
- Check for errors during server startup

### Step 2: Verify SignalR Connection
Look for SignalR connection messages:
```
✅ SignalR Market Hub connected
📡 Ensured SignalR quote subscription for MNQ (triggered by chart load)
```

**If missing:**
- Check SignalR authentication (JWT token)
- Check if market is open (no quotes during market closure)
- Check SignalR connection status in `trading_bot.py`

### Step 3: Verify Quote Flow
Once SignalR is connected, look for:
```
📈 Quote #1 for MNQ: $25139.25 (vol: 246796) → bar aggregator
📈 Quote #2 for MNQ: $25140.00 (vol: 246800) → bar aggregator
...
📈 Quote flow confirmed for MNQ (suppressing further logs)
```

**If missing:**
- SignalR not receiving quote events
- Market might be closed
- Symbol not subscribed in SignalR

### Step 4: Verify Bar Updates Broadcasting
Once quotes are flowing, look for:
```
📡 Broadcasted 5m bar update for MNQ: O:25100.0 H:25150.0 L:25090.0 C:25139.25 (tick_count=45)
📡 Broadcasted 2m bar update for MNQ: O:25100.0 H:25150.0 L:25090.0 C:25139.25 (tick_count=45)
```

**If missing:**
- Bar aggregator not running
- No quotes being received
- Broadcast callback not set

---

## 🔧 Fixes Applied

### 1. Historical Data End Time
**Before:**
```python
if end_time is None:
    end_time = self._get_last_market_close()  # Returns last market close, not current time
```

**After:**
```python
if not end_time:
    from datetime import datetime, timezone
    end_time = datetime.now(timezone.utc)  # Use current time for real-time charts
```

### 2. Enhanced Logging
- Bar aggregator startup now shows which timeframes are tracked
- Quote logging shows first 5 quotes, then confirms flow
- Timeframe registration logged when charts load

### 3. Better Error Handling
- Added try/catch around timeframe registration
- Added logging for SignalR subscription attempts
- Added debug logs for end_time selection

---

## 🧪 Testing Checklist

After restarting the server:

- [ ] **Bar aggregator starts** - Look for "Bar aggregator started" message
- [ ] **SignalR connects** - Look for "SignalR Market Hub connected" message
- [ ] **Quotes flow** - Look for "Quote #1 for [SYMBOL]" messages
- [ ] **Bar updates broadcast** - Look for "Broadcasted [TIMEFRAME] bar update" messages
- [ ] **All timeframes show recent data** - Check chart for 5s, 15s, 30s, 1m, 2m, 5m, 15m, 30m, 1h
- [ ] **Historical data up to current time** - Verify last bar timestamp is recent

---

## 📝 Next Steps

1. **Restart the server** to apply fixes
2. **Check logs** for the messages listed above
3. **Verify chart** shows recent data for all timeframes
4. **Monitor WebSocket** in browser DevTools for `market_update` messages
5. **Report back** with:
   - Which log messages appear
   - Which timeframes show recent data
   - Any errors or warnings

---

**Created:** December 2, 2025  
**Status:** Fixes applied, awaiting verification  
**Priority:** High - Real-time chart updates depend on these fixes

