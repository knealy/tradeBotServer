# Fix for Persistent 500 Errors on Order Placement

**Date**: December 18, 2024  
**Issue**: Bracket orders with stop entry were consistently failing with 500 errors from TopStepX API

## Root Cause

The code was setting `"reduceOnly": True` on both stop loss and take profit brackets when placing **entry orders**. This is incorrect because:

1. **Entry orders haven't filled yet** - there's no open position when the order is placed
2. **The `reduceOnly` flag** is only valid for orders that close/reduce an existing position
3. **TopStepX API rejects these orders** with a 500 error because you can't "reduce" a position that doesn't exist yet

### Example of the Problem

```python
# ❌ WRONG - Setting reduceOnly on brackets for an entry order
order_data = {
    "accountId": 12694476,
    "contractId": 123,
    "type": 4,  # Stop entry order (not filled yet!)
    "side": 0,
    "size": 1,
    "stopPrice": 6846.80,
    "stopLossBracket": {
        "ticks": -50,
        "type": 4,
        "size": 1,
        "reduceOnly": True  # ❌ ERROR: No position exists yet to reduce!
    },
    "takeProfitBracket": {
        "ticks": 100,
        "type": 1,
        "size": 1,
        "reduceOnly": True  # ❌ ERROR: No position exists yet to reduce!
    }
}
```

## The Fix

Removed `"reduceOnly": True` from bracket orders in **three locations**:

### 1. Stop Bracket Orders (lines 3534-3546)
**File**: `brokers/topstepx_adapter.py`  
**Function**: `place_oco_bracket_with_stop_entry()`

```python
# ✅ FIXED - Removed reduceOnly from brackets
order_data["stopLossBracket"] = {
    "ticks": stop_loss_ticks,
    "type": 4,
    "size": quantity
    # reduceOnly removed - brackets auto-attach after entry fills
}

order_data["takeProfitBracket"] = {
    "ticks": take_profit_ticks,
    "type": 1,
    "size": quantity
    # reduceOnly removed - brackets auto-attach after entry fills
}
```

### 2. Generic Bracket Orders (lines 355-371)
**File**: `brokers/topstepx_adapter.py`  
**Function**: `_place_order_python()`

Similar fix applied to generic order placement function.

### 3. Market Bracket Orders (lines 4145-4162)
**File**: `brokers/topstepx_adapter.py`  
**Function**: Market bracket order placement

Similar fix applied to market order bracket placement.

## Secondary Bug Fixed

**Issue**: `Error in plain stop fill monitoring: 'str' object has no attribute 'get'`

**Cause**: Lines 1042 and 1119 in `overnight_range_strategy.py` were setting `order_id` as a string even when orders failed, causing type mismatches in monitoring code.

**Fix**: Removed these misplaced lines that were storing failed order IDs:
- Line 1042: `self.breakout_active_orders.setdefault(symbol, {})["BUY"] = str(order_id)`
- Line 1119: `self.breakout_active_orders.setdefault(symbol, {})["SELL"] = str(order_id)`

## How TopStepX Bracket Orders Work

**Correct Understanding**:
1. You place a bracket order with an entry (market or stop)
2. The bracket specifications are attached to the entry order
3. When the entry fills and creates a position, **TopStepX automatically**:
   - Creates the stop loss order (linked to the position)
   - Creates the take profit order (linked to the position)  
   - Makes both orders `reduceOnly` automatically (they close when position closes)

**Key Point**: You don't need to specify `reduceOnly` on the bracket orders themselves - TopStepX handles this automatically once the position opens.

## Testing the Fix

### Before Testing
1. Ensure TopStepX account has "Auto OCO Brackets" enabled
2. Ensure you're using a PRAC/PRACTICE account
3. Token should be valid (auto-refresh is working)

### Test Commands
```bash
# Test stop bracket order
python trading_bot.py
> start overnight_range MES
# Wait for market open (9:30 AM EST) or use trigger_market_open command
> trigger_market_open
```

### Expected Results
✅ **Success Indicators**:
- Orders placed without 500 errors
- Log shows: `✅ Long breakout order placed: [order_id]`
- Log shows: `✅ Short breakout order placed: [order_id]`
- No "EOF while parsing" errors from Rust executor
- No "Error in plain stop fill monitoring" errors

❌ **Failure Indicators** (should NOT see these anymore):
- `HTTP 500: 500 Server Error: Internal Server Error`
- `❌ Retry also failed with 500 error`
- `⚠️  Unable to place BUY breakout order for MES: HTTP 500`

### Monitoring the Fix
```bash
# Watch the logs in real-time
tail -f trading_bot.log | grep -E "500|bracket|BUY|SELL|placed"
```

## Why This Wasn't Caught Earlier

1. **Server/token were fine** - The error message suggested server or auth issues, but those were working
2. **OCO brackets were enabled** - Account settings were correct
3. **Prices were valid** - Tick sizes and price calculations were correct
4. **The actual problem**: Invalid use of `reduceOnly` flag on brackets for entry orders that haven't filled yet

## Summary

**Root Cause**: Setting `reduceOnly: True` on brackets for entry orders that haven't filled yet  
**Impact**: 100% failure rate on all bracket orders with stop entry  
**Fix**: Removed `reduceOnly` flag from brackets - TopStepX handles this automatically  
**Files Changed**:
- `brokers/topstepx_adapter.py` (3 locations fixed)
- `strategies/overnight_range_strategy.py` (2 lines removed)

**Testing**: Orders should now place successfully without 500 errors.
