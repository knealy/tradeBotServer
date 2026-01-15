# Statistics Endpoints Removal

**Date**: 2026-01-15  
**Status**: ✅ Complete

## Summary

Removed all Statistics endpoint calls from the codebase. Statistics endpoints on `userapi.topstepx.com` require internal developer permissions and are not available via API key scope.

## Changes Made

### 1. `get_today_stats()` - Updated

**Location**: `trading_bot.py:3898-3961`

**Before**: Tried to call `/Statistics/todaystats` endpoints  
**After**: Uses manual calculation from `/api/Trade/search`

**Implementation**:
- Gets trades for today using `get_trades_from_api()`
- Calculates statistics using `_calculate_trade_statistics()`
- Returns statistics in same format as before

### 2. `get_trade_statistics()` - Updated

**Location**: `trading_bot.py:3963-4039`

**Before**: Tried to call `/Statistics/daystats` endpoints  
**After**: Uses manual calculation from `/api/Trade/search`

**Implementation**:
- Gets trades for date range using `get_trades_from_api()`
- Calculates statistics using `_calculate_trade_statistics()`
- Returns statistics in same format as before

### 3. `get_profit_factor()` - Updated

**Location**: `trading_bot.py:4104-4177`

**Before**: Tried to call `/Statistics/profitFactor` endpoints  
**After**: Uses manual calculation from `/api/Trade/search`

**Implementation**:
- Gets trades for date range using `get_trades_from_api()`
- Calculates total profit and total loss from trades
- Returns `{'totalProfit': ..., 'totalLoss': ...}` format

## Why This Approach

1. **Statistics endpoints require internal permissions** - Not available via API key
2. **Manual calculation is accurate** - Uses `/api/Trade/search` with pre-calculated PnL
3. **Consistent with TopStepX dashboard** - Uses same trade data
4. **Flexible** - Can filter by date, strategy, etc.
5. **Reliable** - Doesn't depend on unavailable endpoints

## Current Implementation

All statistics are now calculated using:
- `/api/Trade/search` - Gets trades with pre-calculated `profitAndLoss`
- `_calculate_trade_statistics()` - Calculates win rate, averages, etc.
- Manual profit/loss calculation for profit factor

## Benefits

1. ✅ **No dependency on unavailable endpoints**
2. ✅ **Accurate calculations** - Uses API-provided PnL
3. ✅ **Same data source** - Trade/search matches TopStepX dashboard
4. ✅ **Flexible filtering** - Can filter by date, strategy, etc.
5. ✅ **Maintainable** - Clear, straightforward code

## Testing

All methods now:
- Use `get_trades_from_api()` to fetch trades
- Use `_calculate_trade_statistics()` for calculations
- Return same format as before (backward compatible)

## Notes

- Statistics endpoints on `userapi.topstepx.com` exist but require internal developer permissions
- Current manual calculation approach is the correct solution
- No functionality lost - all statistics still available
- Code is cleaner without failed API calls
