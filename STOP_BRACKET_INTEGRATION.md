# Stop Bracket Integration Summary

## Overview
Successfully integrated stop bracket orders into the webhook server for TradingView signals. The webhook server can now place stop orders at the TradingView entry price with automatic SL/TP brackets attached.

## Changes Made

### 1. Fixes to `trading_bot.py`
- **Fixed trade price extraction**: Added `averagePrice`, `fillPrice`, and `price` field lookups for better compatibility with API response formats
- **Added debugging logging**: Logs full order data when prices are $0.00 to help identify root cause
- **Fixed stop_bracket implementation**: Now uses `/api/Order/place` endpoint (same as native_bracket) instead of non-existent `/api/Order/bracketOrder`

### 2. Webhook Server Integration (`webhook_server.py`)
- **Added `USE_STOP_ENTRY` environment variable** (defaults to `true`)
- **Updated `_execute_open_long()` function**: Now places stop bracket orders when `USE_STOP_ENTRY=true`
- **Updated `_execute_open_short()` function**: Now places stop bracket orders when `USE_STOP_ENTRY=true`
- **Uses `place_oco_bracket_with_stop_entry()` method**: Places stop order at entry price with SL/TP brackets

## How It Works

### Order Placement Logic (Priority Order)
1. **If `USE_STOP_ENTRY=true`** (NEW - default):
   - Places stop order at TradingView entry price
   - Automatically attaches stop loss and take profit brackets
   - Uses TP1 as the take profit level
   - Entry executes only when price reaches the stop level

2. **Else if `USE_NATIVE_BRACKETS=true`**:
   - Places immediate market order with native OCO brackets
   - Supports both single exit (TP1) and staged exits (TP1 + TP2)

3. **Else** (Position Brackets mode):
   - Places simple market order
   - TopStepX platform manages brackets automatically

### Stop Bracket Order Details
- **Entry Type**: Stop-market order (type=4)
- **Entry Price**: From TradingView `entry` field
- **Stop Loss**: From TradingView `stop_loss` field
- **Take Profit**: From TradingView `take_profit_1` field
- **Bracket Format**: Uses `stopLossBracket` and `takeProfitBracket` with ticks calculated from entry price
- **Fallback**: Automatically falls back to hybrid approach if native brackets not enabled in platform

## Environment Variables

### New Variable
- **`USE_STOP_ENTRY`**: Controls whether to use stop orders for entry
  - `true` (default): Use stop bracket orders (waits for entry price)
  - `false`: Use market/bracket orders (immediate execution)

### Existing Variables (still work as before)
- **`USE_NATIVE_BRACKETS`**: Controls bracket type
  - `true`: Use native OCO brackets
  - `false`: Use position brackets
- **`IGNORE_NON_ENTRY_SIGNALS`**: Controls TP signal processing
- **`TP1_FRACTION`**: Percentage to close at TP1 (default: 0.75 = 75%)

## Configuration Examples

### Use Stop Entry Orders (Recommended for TradingView)
```bash
USE_STOP_ENTRY=true           # Wait for entry price
USE_NATIVE_BRACKETS=false     # Not used when USE_STOP_ENTRY=true
```

### Use Immediate Market Entry with OCO Brackets
```bash
USE_STOP_ENTRY=false
USE_NATIVE_BRACKETS=true
```

### Use Position Brackets (TopStepX managed)
```bash
USE_STOP_ENTRY=false
USE_NATIVE_BRACKETS=false
```

## Testing Recommendations

### 1. Test Stop Bracket Orders Manually
```bash
# In trading_bot.py CLI
stop_bracket mnq buy 1 25300 25285 25320
```
This should place:
- Stop order at 25300 (entry)
- Stop loss at 25285
- Take profit at 25320

### 2. Test via TradingView Webhook
Send a test signal with:
```json
{
  "action": "open_long",
  "symbol": "MNQ",
  "entry": 25300.00,
  "stop_loss": 25285.00,
  "take_profit_1": 25320.00,
  "take_profit_2": 25350.00
}
```

Expected behavior:
- Webhook receives signal
- Places stop bracket order at entry=25300 with SL=25285, TP=25320
- Order waits for price to reach 25300 before executing
- When filled, SL and TP brackets are automatically active

### 3. Monitor Logs
```bash
tail -f trading_bot.log webhook_server.log
```

Look for:
- `"Using stop entry orders at $[price]"`
- `"Placed stop bracket: entry=$[price], size=[qty], SL=[sl], TP=[tp]"`
- Check for any fallback messages if brackets not enabled

## Trades P&L Issue

### Current Status
- Added logging to identify orders with $0.00 prices
- The next time you run `trades` command, check logs for:
  ```
  ⚠️  Order with zero/null price detected: {order dict}
  ```
- This will show what fields are available in orders that have missing prices

### Expected Resolution
Once we see the raw order data, we can:
1. Identify the correct field names for filled order prices
2. Update price extraction logic accordingly
3. Fix P&L calculations

### Temporary Workaround
If you need accurate P&L now:
- Use the TopStepX web dashboard to view trade history
- The bot's tracking will be fixed once we see the order structure

## Next Steps

1. **Set environment variable**:
   ```bash
   export USE_STOP_ENTRY=true
   ```

2. **Restart webhook server** with new configuration

3. **Test with a TradingView signal** to verify stop bracket orders work

4. **Run trades command** after placing some orders to:
   - Verify prices are now showing correctly
   - Check logs for any orders with $0.00 prices
   - Share the log output if issues persist

5. **Monitor fills** to ensure brackets activate correctly when stop orders execute

## Benefits of Stop Entry Orders

1. **Better Entry Prices**: Wait for price to reach your desired entry level
2. **Reduced Slippage**: No immediate market orders unless price confirms
3. **Risk Management**: Only enter when price action confirms signal
4. **TradingView Alignment**: Entry price from TV signals is used as stop trigger
5. **Automatic Brackets**: SL/TP still attached and managed automatically

## Support

If you encounter issues:
1. Check `trading_bot.log` for error messages
2. Verify environment variables are set correctly
3. Ensure OCO brackets are enabled in TopStepX platform (if applicable)
4. Test manually with `stop_bracket` command first
5. Share logs showing the full order placement flow

---

**Files Modified**:
- `trading_bot.py`: Price extraction fix, debugging logging
- `webhook_server.py`: Stop bracket integration for entry signals

**Commits**:
- `f8d609d`: Fix trades prices and stop_bracket implementation
- `cf467a7`: Add temporary logging for orders with zero prices
- `79b2f46`: Integrate stop_bracket orders into webhook server for TradingView signals

