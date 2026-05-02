# Breakeven and Trailing Stops Implementation Guide

**Last Updated**: January 16, 2026

---

## Overview

This document describes the implementation status and configuration flags for breakeven stops and trailing stops in the trading bot.

---

## 1. Breakeven Stops

### Status: ✅ **FULLY IMPLEMENTED**

Breakeven stops are implemented in the `OvernightRangeStrategy` and automatically move stop loss orders to entry price (breakeven) when positions become profitable.

### Configuration

**Environment Variable**: `BREAKEVEN_ENABLED`
- **Default**: `true`
- **Values**: `true`, `1`, `yes`, `on` (case-insensitive) to enable; any other value disables
- **Location**: Set in environment or `.env` file

**Profit Threshold**: `BREAKEVEN_PROFIT_POINTS`
- **Default**: `15.0` points
- **Description**: Minimum profit (in points) required before moving stop to breakeven
- **Example**: If set to `15.0`, stop moves to breakeven when unrealized P&L reaches +15 points

### How It Works

1. **Automatic Monitoring**: When a position is opened (stop entry order filled), breakeven monitoring starts automatically
2. **Profit Check**: Continuously monitors unrealized P&L for the position
3. **Breakeven Trigger**: When P&L >= `BREAKEVEN_PROFIT_POINTS`, the stop loss order is modified to entry price
4. **Auto-Stop**: Monitoring stops after breakeven is set (no need to continue monitoring)

### Implementation Details

- **Strategy**: `strategies/overnight_range_strategy.py`
- **Method**: `monitor_breakeven_stops()` - background task that runs continuously
- **Order Method**: Uses `modify_order()` to update stop loss price to entry price
- **Position Tracking**: Monitors all open positions and checks P&L every few seconds

### Usage Example

```bash
# Enable breakeven (default)
export BREAKEVEN_ENABLED=true
export BREAKEVEN_PROFIT_POINTS=15.0

# Disable breakeven
export BREAKEVEN_ENABLED=false
```

### Log Messages

When breakeven is triggered:
```
🔄 Moving stop to breakeven for {symbol}: {entry_price} (profit: +{profit_points} pts)
✅ Stop loss updated to breakeven: {entry_price}
```

---

## 2. Trailing Stops

### Status: ⚠️ **PARTIALLY IMPLEMENTED**

Trailing stops have infrastructure in place but are not fully integrated into strategies.

### Infrastructure

**Method**: `place_trailing_stop_order()` in `brokers/topstepx_adapter.py`
- **Status**: ✅ Implemented and working
- **Order Type**: Type 5 (TrailingStop) via TopStepX API
- **Parameters**:
  - `symbol`: Trading symbol
  - `side`: "BUY" or "SELL"
  - `quantity`: Number of contracts
  - `trail_amount`: Trailing distance in price units (e.g., $5.00)
  - `account_id`: Account ID

### Strategy Integration

**Status**: 🚧 **NOT FULLY INTEGRATED**

Some strategies have trailing stop logic but it's incomplete:

1. **Trend Following Strategy** (`strategies/trend_following_strategy.py`):
   - Has trailing stop calculation logic
   - Uses ATR-based trailing stops
   - **Issue**: Marked as `TODO` - doesn't actually modify orders via API
   - **Config**: `ATR_TRAILING_MULTIPLIER` (default: 2.0)

2. **Trend Scalping Strategy** (`strategies/trend_scalping_strategy.py`):
   - Has `trailing_stop_enabled` flag
   - **Config**: `TREND_SCALP_TRAILING_STOP` (default: 'true')
   - **Status**: Logic exists but not fully implemented

### Configuration Flags

**Trend Following Strategy**:
- `ATR_TRAILING_MULTIPLIER`: Multiplier for ATR-based trailing (default: 2.0)

**Trend Scalping Strategy**:
- `TREND_SCALP_TRAILING_STOP`: Enable/disable trailing stops (default: 'true')
- `TREND_SCALP_BREAKEVEN_R`: Breakeven trigger in R multiples (default: 1.0)

### How to Use Trailing Stops (Manual)

You can place trailing stop orders manually via CLI or API:

```python
# Via adapter
result = await adapter.place_trailing_stop_order(
    symbol="MNQ",
    side="BUY",
    quantity=1,
    trail_amount=5.0,  # Trail by $5.00
    account_id="12345678"
)
```

### TODO: Full Integration

To fully integrate trailing stops into strategies:

1. **Monitor Positions**: Add background task to monitor open positions
2. **Calculate Trail**: Update trailing stop price based on current price movement
3. **Modify Orders**: Use `modify_order()` to update trailing stop orders
4. **Strategy-Specific**: Each strategy can implement its own trailing logic (ATR-based, percentage-based, etc.)

---

## 3. Comparison

| Feature | Breakeven Stops | Trailing Stops |
|--------|----------------|----------------|
| **Status** | ✅ Fully Implemented | ⚠️ Partially Implemented |
| **Strategy Support** | Overnight Range Strategy | Infrastructure only |
| **Configuration** | `BREAKEVEN_ENABLED` | Strategy-specific flags |
| **Trigger** | Fixed profit threshold | Dynamic price movement |
| **Order Type** | Modified stop loss | Type 5 (TrailingStop) |
| **Auto-Start** | ✅ Yes (on position open) | ❌ No (manual only) |

---

## 4. Recommendations

### For Breakeven Stops
- ✅ **Ready to use** - Fully functional in Overnight Range Strategy
- Consider adding to other strategies if needed

### For Trailing Stops
- 🚧 **Needs work** - Infrastructure exists but needs strategy integration
- Priority: Complete implementation in Trend Following Strategy
- Consider: Generic trailing stop manager that all strategies can use

---

## 5. Related Files

- `strategies/overnight_range_strategy.py` - Breakeven implementation
- `strategies/trend_following_strategy.py` - Trailing stop logic (incomplete)
- `strategies/trend_scalping_strategy.py` - Trailing stop flags
- `brokers/topstepx_adapter.py` - `place_trailing_stop_order()` method
- `strategies/strategy_base.py` - `place_bracket_order()` with `enable_breakeven` parameter

---

## 6. Future Enhancements

1. **Generic Trailing Stop Manager**: Create a shared component for all strategies
2. **Multiple Trailing Methods**: ATR-based, percentage-based, swing-based
3. **Trailing Stop Activation**: Only activate after certain profit threshold
4. **Breakeven for All Strategies**: Add breakeven support to other strategies beyond Overnight Range
