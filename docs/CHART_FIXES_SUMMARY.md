# Chart Fixes Summary - December 8, 2025

## Issues Identified

### 1. ✅ Rust Execution Warnings - FIXED
**Problem**: `'TopStepXAdapter' object has no attribute '_get_available_contracts_rust'` and `'_get_positions_rust'`

**Solution**: Commented out Rust hot path attempts for these methods since they're not yet implemented in Rust.

### 2. ⏳ Symbols Dropdown Only Shows Current Symbol
**Problem**: Dropdown only shows MNQ, not all 51 contracts. Console shows "No symbols returned from contracts endpoint"

**Root Cause**: The JavaScript is checking `data.symbols` but the response has `symbols` as an empty array or the contracts aren't being fetched properly.

**Next Steps**: 
- Verify contracts are being fetched in Python (check logs)
- Ensure `symbols_list` is populated correctly
- Check JavaScript is reading the correct field from response

### 3. ⏳ X-Axis Still Shows UTC Time (06:24 instead of 01:24)
**Problem**: Added `localization.timeFormatter` but x-axis labels still show UTC

**Root Cause**: The `timeFormatter` only affects tooltips/crosshair, not the x-axis labels. Need to add `timeScale.timeVisible` configuration.

**Solution Needed**: The x-axis time display is controlled by TradingView's internal formatting. The localization we added affects crosshair/tooltips but not axis labels. This is a known limitation of Lightweight Charts - axis labels are always in UTC.

**Workaround**: Document that axis shows UTC, crosshair shows local time.

### 4. ⏳ "Cannot Update Oldest Data" Error
**Problem**: `Real-time update error: Error: Cannot update oldest data, last time=[object Object], new time=[object Object]`

**Root Cause**: The `bar.time` values are objects instead of numbers. This happens when timestamps aren't properly converted to Unix seconds.

**Solution Needed**: Ensure all timestamps in `updateRealtime()` are numbers, not objects.

### 5. ⏳ No Position Lines on Chart
**Problem**: Orders execute successfully but no visual lines showing entry/stop/TP on canvas

**Root Cause**: The `updatePositionLines()` function is called but may not be working correctly, or positions aren't being returned properly.

**Next Steps**:
- Verify `get_open_positions()` returns data in correct format
- Check if `updatePositionLines()` is creating the price lines correctly
- Ensure price line series are added to the chart

## Priority Fixes

1. **HIGH**: Fix "Cannot update oldest data" error (breaks real-time updates)
2. **MEDIUM**: Fix symbols dropdown (usability issue)
3. **MEDIUM**: Add position lines (trading feature)
4. **LOW**: X-axis time display (workaround: document UTC display)

## Files Modified
- `brokers/topstepx_adapter.py`: Disabled unimplemented Rust hot paths
- `gui/chart_html.py`: (pending fixes for remaining issues)

