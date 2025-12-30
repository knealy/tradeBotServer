# Chart Query Key Fix - December 2, 2025

## 🐛 Issue: Blank Chart Canvas Despite API Returning Data

### Problem
The trading chart canvas was blank even though:
- The terminal `history` command worked correctly and returned data
- The API endpoint `/api/history` was returning valid data (verified via curl)
- Network requests were being made successfully

### Root Cause
The React Query setup in `TradingChart.tsx` had a critical flaw:

```typescript
// ❌ BEFORE - BROKEN
const currentTime = new Date().toISOString()
const { data } = useQuery(
  ['tradingChartData', symbol, timeframe, barLimit, currentTime], // ⚠️ currentTime changes every render!
  () => analyticsApi.getHistoricalData({ ... })
)
```

**Why this broke:**
- `currentTime` was recalculated on **every render** (milliseconds change)
- React Query treats each unique query key as a **new query**
- This caused the query key to change constantly, canceling in-flight requests
- The query never completed, leaving `data` as `undefined` or stale

### Fix Applied

```typescript
// ✅ AFTER - FIXED
const { data } = useQuery(
  ['tradingChartData', symbol, timeframe, barLimit], // ✅ Stable query key
  () => {
    const currentTime = new Date().toISOString() // ✅ Only calculated when query runs
    return analyticsApi.getHistoricalData({
      symbol,
      timeframe,
      limit: barLimit,
      end: currentTime, // ✅ Still uses current time for API request
    })
  }
)
```

**Why this works:**
- Query key is now **stable** across renders (only changes when symbol/timeframe/barLimit changes)
- `currentTime` is only calculated **inside the query function**, not in the query key
- React Query can properly cache and complete the request
- Periodic refresh (60s) still works via `refetchInterval`

### Files Modified
- `frontend/src/components/TradingChart.tsx` (lines 131-170)

### Testing
1. ✅ Chart now displays historical bars correctly
2. ✅ Data loads on initial mount
3. ✅ Timeframe/bar limit changes trigger new queries properly
4. ✅ Periodic refresh (60s) continues to work
5. ✅ Real-time WebSocket updates still function

### Key Takeaway
**Never include dynamic values in React Query keys that change on every render.** Use stable identifiers in the key, and pass dynamic values to the query function itself.

