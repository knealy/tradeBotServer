# API Endpoint Optimization Analysis

**Date**: 2026-01-15  
**Status**: Analysis Complete

## Overview

This document analyzes manual calculations and operations in the codebase that could be simplified by using existing TopStepX API endpoints instead of manual computation.

**Reference**: [TopStepX Swagger API Documentation](https://api.topstepx.com/swagger/index.html)

---

## Current Manual Calculations vs. API Endpoints

### 1. ✅ **Trade PnL Calculation** - FIXED

**Previous**: Manual FIFO consolidation + PnL calculation  
**Current**: Using `/api/Trade/search` with pre-calculated `profitAndLoss`  
**Status**: ✅ Implemented

---

### 2. ❌ **Account Statistics** - ENDPOINTS EXIST BUT DON'T RETURN DATA

**Location**: `trading_bot.py:3898-4167`

**Current Implementation**:
- `get_today_stats()` - Tries `/Statistics/todaystats` 
- `get_trade_statistics()` - Tries `/Statistics/daystats`
- `get_profit_factor()` - Tries `/Statistics/profitFactor`

**Status**: ⚠️ **Endpoints exist on `userapi.topstepx.com` but return empty arrays**

**Discovery**:
- ✅ Endpoints exist: `/Statistics/daily`, `/Statistics/profitFactor`, `/Statistics/trades`, `/Statistics/monthly`, `/Statistics/lifetimestats`
- ✅ All return HTTP 200 (endpoints are valid)
- ❌ All return empty arrays `[]` even for accounts with active trades
- ❌ Tested with multiple parameter formats, date ranges, and accounts with trades
- ❌ No data returned despite valid requests

**Conclusion**: 
- **Endpoints appear to be incomplete or non-functional** on TopStepX's side
- **Stick with manual calculations** using `/api/Trade/search`
- **Do not use Statistics endpoints** - they don't return useful data

**Recommendation**: 
- **Keep manual calculation in `_calculate_trade_statistics()`** using Trade/search data
- **Remove or deprecate Statistics endpoint calls** - they don't work
- **Use `/api/Trade/search` to calculate statistics from trades** (current approach is correct)

**Code Locations to Update**:
- `trading_bot.py:3898-3961` - `get_today_stats()` - **Remove Statistics API calls, use manual calculation**
- `trading_bot.py:3963-4039` - `get_trade_statistics()` - **Remove Statistics API calls, use manual calculation**
- `trading_bot.py:4104-4177` - `get_profit_factor()` - **Remove Statistics API calls, use manual calculation**

**Testing**: See `docs/STATISTICS_ENDPOINTS_DISCOVERY.md` for testing results

---

### 3. ✅ **Account Balance** - USING API (LIMITED)

**Location**: `core/account_tracker.py`, `trading_bot.py:1872-1985`

**Current Implementation**:
```python
# Manual calculation in AccountTracker for equity/dailyPnL
state.current_balance = (
    state.starting_balance + 
    state.realised_PnL + 
    state.unrealised_PnL - 
    state.commissions - 
    state.fees
)
```

**API Endpoint Available**: `/api/Account/search`

**Status**: ✅ **Already using API for balance**  
**Limitation**: `/api/Account/search` **only returns current balance**, not:
- ❌ Equity
- ❌ Daily PnL
- ❌ Realized PnL
- ❌ Unrealized PnL

**Recommendation**: 
- ✅ **Keep using `/api/Account/search` for balance** (already implemented)
- ⚠️ **Continue manual calculation for equity/dailyPnL** (API doesn't provide)
- Use Trade/search to calculate daily PnL from trades

**Code Locations**:
- `core/account_tracker.py:320-322` - Manual balance calculation (for equity/dailyPnL)
- `trading_bot.py:1872-1912` - `get_account_balance()` - ✅ Uses API
- `trading_bot.py:1913-1985` - `get_account_info()` - ✅ Uses API for balance

**Conclusion**: Current implementation is correct - API only provides balance, not equity/PnL

---

### 4. ✅ **Unrealized PnL Calculation** - MANUAL (REQUIRED)

**Location**: `core/account_tracker.py:276-332`

**Current Implementation**:
```python
# Manual calculation using point values
if side == 'BUY' or side == 'LONG':
    price_diff = current_price - entry_price
else:
    price_diff = entry_price - current_price

tick_value = self._get_tick_value(symbol)  # Hardcoded values
position_pnl = price_diff * tick_value * qty
```

**API Endpoint Status**: ❌ **API does not provide unrealized PnL**
- `/api/Account/search` - Does not include unrealizedPnL
- `/api/Order/searchOpen` (Position endpoints) - Does not include unrealizedPnL per position

**Status**: ✅ **Manual calculation is required and correct**

**Recommendation**:
- ✅ **Keep manual calculation** - API doesn't provide this data
- Ensure point values are accurate (currently hardcoded)
- Consider caching point values or fetching from contract data if available

**Code Locations**:
- `core/account_tracker.py:296-316` - Manual unrealized PnL calculation ✅ Correct
- `trading_bot.py:740` - Summing unrealized PnL from positions ✅ Correct
- `servers/dashboard.py:628-650` - Manual position PnL calculation ✅ Correct

**Conclusion**: Current manual calculation is necessary - API doesn't provide unrealized PnL

---

### 5. ✅ **Historical Bars** - USING API (OPTIMIZATION POSSIBLE)

**Location**: `brokers/topstepx_adapter.py:2150-2751`

**Current Implementation**: ✅ Using `/api/History/retrieveBars`

**Status**: Already optimized, but potential improvement:
- Daily bars are aggregated from 1m bars (to enforce 18:00-18:00 boundaries)
- This is intentional and correct
- **Potential Optimization**: Use `startTime`/`endTime` parameters to fetch exact number of bars needed

**API Parameters Available**:
```json
{
  "contractId": "string",
  "live": true,
  "startTime": "2026-01-15T02:31:50.047Z",
  "endTime": "2026-01-15T02:31:50.047Z",
  "unit": 0,
  "unitNumber": 0,
  "limit": 0,
  "includePartialBar": true
}
```

**Recommendation**:
- **Optional**: Optimize by using `startTime`/`endTime` to fetch exact bars needed for aggregation
- **Current approach is fine** - if optimization doesn't simplify code, keep as-is
- Daily bar aggregation from 1m bars is correct (enforces 18:00-18:00 boundaries)

**Code Locations**:
- `brokers/topstepx_adapter.py:2150-2751` - Historical bar fetching and aggregation

---

### 6. ✅ **Trade Statistics Calculation** - MANUAL (REQUIRED)

**Location**: `trading_bot.py:3790-3840`

**Current Implementation**:
```python
def _calculate_trade_statistics(self, trades: List[Dict]) -> Dict:
    # Manual calculation of:
    # - Win rate
    # - Average win/loss
    # - Total PnL
    # - Largest win/loss
```

**API Endpoint Status**: ❌ **No Statistics endpoints exist**

**Status**: ✅ **Manual calculation is required and correct**
- Uses `net_pnl` from `/api/Trade/search` (pre-calculated PnL)
- Calculates statistics from trade data
- This is the correct approach

**Recommendation**:
- ✅ **Keep manual calculation** - No API alternative exists
- ✅ **Continue using `/api/Trade/search`** for trade data with pre-calculated PnL
- Statistics calculation from Trade/search data is accurate

**Code Locations**:
- `trading_bot.py:3790-3840` - `_calculate_trade_statistics()` ✅ Correct

---

### 7. ⚠️ **Order Search** - USING API BUT COULD OPTIMIZE

**Location**: `brokers/topstepx_adapter.py:1186-1326`

**Current Implementation**: ✅ Using `/api/Order/search`

**Note**: Also tries `/api/Fill/search` as fallback (may not exist)

**Status**: Already using API, but could verify if Fill/search exists

---

### 8. ✅ **Position Unrealized PnL** - MANUAL (REQUIRED)

**Location**: `servers/dashboard.py:628-650`

**Current Implementation**:
```python
# Manual calculation of unrealized PnL per position
if side.upper() in ['LONG', 'BUY', '0']:
    price_diff = current_price - entry_price
    unrealized_pnl = price_diff * quantity * point_value
else:
    price_diff = entry_price - current_price
    unrealized_pnl = price_diff * quantity * point_value
```

**API Endpoint Status**: ❌ **Position endpoints do not provide `unrealizedPnL`**

**Status**: ✅ **Manual calculation is required and correct**

**Recommendation**:
- ✅ **Keep manual calculation** - API doesn't provide this data
- Ensure point values are accurate

**Code Locations**:
- `servers/dashboard.py:628-650` - Manual position PnL calculation ✅ Correct
- `core/account_tracker.py:296-316` - Similar calculation ✅ Correct

---

## Summary of Findings

### ✅ Already Optimized

1. **Trade PnL** - ✅ Using `/api/Trade/search` with pre-calculated `profitAndLoss`
2. **Account Balance** - ✅ Using `/api/Account/search` for balance
3. **Historical Bars** - ✅ Using `/api/History/retrieveBars` correctly
4. **Order Search** - ✅ Using `/api/Order/search`

### ✅ Manual Calculation Required (API Doesn't Provide)

1. **Unrealized PnL** - ✅ Manual calculation required (API doesn't provide)
2. **Position Unrealized PnL** - ✅ Manual calculation required (API doesn't provide)
3. **Equity/Daily PnL** - ✅ Manual calculation required (API only provides balance)
4. **Trade Statistics** - ✅ Manual calculation from Trade/search data (no Statistics API)

### ❌ Endpoints That Don't Work

1. **Statistics Endpoints** - ⚠️ Endpoints exist on `userapi.topstepx.com` but return empty arrays
   - **Endpoints**: `/Statistics/daily`, `/Statistics/profitFactor`, `/Statistics/trades`, `/Statistics/monthly`, `/Statistics/lifetimestats`
   - **Status**: Return HTTP 200 but empty data even for accounts with active trades
   - **Action**: Do not use these endpoints - they appear incomplete/non-functional
   - **Alternative**: Use `/api/Trade/search` to calculate statistics (current approach)
   - **Note**: Other endpoints on `userapi.topstepx.com` may be useful - explore Swagger UI

### 🔄 Optional Optimization

1. **Historical Bars** - Could optimize with `startTime`/`endTime` parameters (optional, current approach is fine)

---

## Recommended Actions

### Phase 1: Remove Non-Existent Statistics Endpoints ✅

1. **Remove Statistics endpoint references**:
   - `trading_bot.py:3898-3961` - `get_today_stats()` - **Remove or deprecate**
   - `trading_bot.py:3963-4039` - `get_trade_statistics()` - **Remove or deprecate**
   - `trading_bot.py:4104-4177` - `get_profit_factor()` - **Remove or deprecate**

2. **Update code to use Trade/search for statistics**:
   - Use `/api/Trade/search` to get trades
   - Calculate statistics using `_calculate_trade_statistics()` with Trade/search data
   - This is already the correct approach

### Phase 2: Verify Current Implementation ✅

1. **Account Balance** - ✅ Already using `/api/Account/search` correctly
2. **Unrealized PnL** - ✅ Manual calculation is required (API doesn't provide)
3. **Trade Statistics** - ✅ Using Trade/search + manual calculation (correct approach)

### Phase 3: Optional Optimizations

1. **Historical Bars** - Consider using `startTime`/`endTime` if it simplifies code (optional)
2. **Documentation** - Update to reflect that Statistics endpoints don't exist

---

## Benefits

1. **Accuracy**: API values are authoritative and match TopStepX dashboard
2. **Simplicity**: Less code to maintain
3. **Consistency**: Single source of truth (API)
4. **Performance**: Potentially faster (no calculation overhead)
5. **Reliability**: Eliminates hardcoded point values and calculation errors

---

## Files to Review/Modify

1. `brokers/topstepx_adapter.py` - Add account search method
2. `trading_bot.py` - Update `get_account_info()` and `get_account_balance()`
3. `core/account_tracker.py` - Use API values instead of manual calculation
4. `servers/dashboard.py` - Use API unrealized PnL if available
5. `trading_bot.py:3898-4177` - Verify Statistics endpoints

---

## Testing Checklist

- [x] Test `/api/Account/search` endpoint - ✅ Only returns balance
- [x] Test Statistics endpoints - ❌ Do not exist (see `docs/TEST_STATISTICS_ENDPOINTS.md`)
- [x] Test Position endpoints - ❌ Do not include unrealizedPnL
- [x] Verify Trade/search endpoint - ✅ Provides profitAndLoss
- [ ] Remove references to Statistics endpoints from code
- [ ] Update documentation to reflect findings
- [ ] (Optional) Optimize historical bars with startTime/endTime

---

## Testing Commands

See `docs/TEST_STATISTICS_ENDPOINTS.md` for curl commands to:
- Get JWT token
- Test Statistics endpoints
- Verify which endpoints exist

---

**Next Steps**: 
1. Remove Statistics endpoint references from code
2. Ensure all statistics use Trade/search + manual calculation
3. (Optional) Optimize historical bars if beneficial
