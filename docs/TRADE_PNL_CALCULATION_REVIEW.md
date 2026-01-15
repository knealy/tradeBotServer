
# Trade PnL Calculation Review

**Date**: 2026-01-14  
**Status**: Review Complete, Implementation In Progress

## Executive Summary

Currently, we are **NOT** using the `/api/Trade/search` endpoint. Instead, we:
1. Fetch orders via `/api/Order/search`
2. Manually consolidate orders into trades using FIFO matching
3. Calculate PnL manually using point values and commission

**Recommendation**: Switch to `/api/Trade/search` endpoint which provides:
- Pre-calculated `profitAndLoss` field
- Fees already included
- Simpler, more accurate implementation
- Handles half-turn trades (null profitAndLoss for open positions)

---

## Current Implementation

### 1. Trade Data Fetching

**Primary Method**: `get_order_history()` → `TopStepXAdapter.get_order_history()`

**API Endpoint Used**: `POST /api/Order/search`

**Location**: `brokers/topstepx_adapter.py:1186-1285`

**Flow**:
```
Frontend/CLI → DashboardAPI.get_trade_history_paginated() 
  → trading_bot.get_order_history() 
  → broker_adapter.get_order_history() 
  → POST /api/Order/search
```

### 2. PnL Calculation Methods

#### A. **Order Consolidation** (`_consolidate_orders_into_trades`)
- **Location**: `trading_bot.py:3490-3788`
- **Method**: FIFO matching of entry/exit orders
- **PnL Calculation**: Manual calculation using:
  - Point values (MNQ=$2, MES=$5, etc.)
  - Commission ($0.74 per contract per side = $1.48 round trip)
  - Price difference × quantity × point_value - commission

#### B. **Session Trade Tracker** (`SessionTradeTracker`)
- **Location**: `core/session_trade_tracker.py`
- **Method**: Real-time FIFO matching from SignalR fills
- **PnL Calculation**: Same manual calculation as above
- **Purpose**: Real-time session PnL tracking

#### C. **Account Tracker** (`AccountTracker`)
- **Location**: `core/account_tracker.py`
- **Method**: Updates from fill data
- **PnL Calculation**: Uses provided PnL from fills or calculates manually
- **Purpose**: Account state tracking with realized/unrealized PnL

#### D. **Dashboard API** (`DashboardAPI._extract_trade_pnl`)
- **Location**: `servers/dashboard.py:102-137`
- **Method**: Extracts PnL from trade data or recalculates
- **Priority**: 
  1. Recalculate for consolidated trades (ensures correct point value)
  2. Check for direct PnL fields

### 3. Where Trades Are Used

1. **Frontend Dashboard**: `/api/trades` endpoint
2. **CLI Command**: `trades` command
3. **Chart UI**: Trade history display
4. **Performance Metrics**: Trade statistics calculation
5. **Session Tracking**: Real-time PnL updates

---

## Issues with Current Approach

### 1. **Complexity**
- Manual FIFO matching logic (~300 lines)
- Multiple places calculating PnL differently
- Risk of calculation errors

### 2. **Accuracy Concerns**
- Point values hardcoded (may not match TopStepX exactly)
- Commission calculation assumptions
- Potential rounding differences

### 3. **Maintenance Burden**
- Changes to PnL calculation require updates in multiple places
- Difficult to ensure consistency across all methods

### 4. **Performance**
- Extra processing to consolidate orders
- Multiple API calls (Order/search + potential Fill/search fallback)

---

## Proposed Solution: Use `/api/Trade/search`

### API Endpoint Details

**URL**: `POST https://api.topstepx.com/api/Trade/search`

**Request Body**:
```json
{
  "accountId": 203,
  "startTimestamp": "2025-01-20T15:47:39.882Z",
  "endTimestamp": "2025-01-30T15:47:39.882Z"
}
```

**Response**:
```json
{
  "trades": [
    {
      "id": 8604,
      "accountId": 203,
      "contractId": "CON.F.US.EP.H25",
      "creationTimestamp": "2025-01-21T16:13:52.523293+00:00",
      "price": 6065.250000000,
      "profitAndLoss": 50.000000000,  // ← Pre-calculated PnL
      "fees": 1.4000,
      "side": 1,
      "size": 1,
      "voided": false,
      "orderId": 14328
    },
    {
      "id": 8603,
      "accountId": 203,
      "contractId": "CON.F.US.EP.H25",
      "creationTimestamp": "2025-01-21T16:13:04.142302+00:00",
      "price": 6064.250000000,
      "profitAndLoss": null,  // ← null = half-turn trade (open position)
      "fees": 1.4000,
      "side": 0,
      "size": 1,
      "voided": false,
      "orderId": 14326
    }
  ],
  "success": true,
  "errorCode": 0,
  "errorMessage": null
}
```

### Benefits

1. **Accuracy**: PnL calculated by TopStepX (authoritative source)
2. **Simplicity**: No manual consolidation needed
3. **Performance**: Single API call, less processing
4. **Reliability**: Handles edge cases (partial fills, reversals, etc.)
5. **Maintainability**: Less code to maintain

### Implementation Plan

1. ✅ Add `get_trades_from_api()` method using `/api/Trade/search`
2. ✅ Update `get_trade_history_paginated()` to use Trade/search
3. ✅ Update CLI `trades` command to use Trade/search
4. ✅ Keep consolidation as fallback for backward compatibility
5. ✅ Update frontend to handle new trade format

---

## Migration Strategy

### Phase 1: Add Trade/search Support (Current)
- Implement `get_trades_from_api()` using `/api/Trade/search`
- Add feature flag to switch between methods
- Test with real account data

### Phase 2: Update All Consumers
- Update `DashboardAPI.get_trade_history_paginated()`
- Update CLI `_handle_trades()`
- Update chart UI handlers
- Update performance metrics

### Phase 3: Remove Consolidation Logic (Optional)
- Keep as fallback for edge cases
- Or remove if Trade/search covers all cases

---

## Testing Checklist

- [ ] Verify Trade/search returns correct data format
- [ ] Test with different date ranges
- [ ] Test with multiple accounts
- [ ] Verify PnL matches TopStepX dashboard
- [ ] Test half-turn trades (null profitAndLoss)
- [ ] Test voided trades
- [ ] Compare results with current consolidation method
- [ ] Performance comparison (API calls, processing time)

---

## Files to Modify

1. `brokers/topstepx_adapter.py` - Add `get_trades()` method
2. `trading_bot.py` - Add `get_trades_from_api()` (already exists, needs update)
3. `servers/dashboard.py` - Update `get_trade_history_paginated()`
4. `core/cli_command_parser.py` - Update `_handle_trades()`
5. `gui/chart_html.py` - Update trade handlers

---

## Notes

- The existing `get_trades_from_api()` method in `trading_bot.py:4042` tries `/Statistics/trades` endpoint, which may not be the same as `/api/Trade/search`
- We should verify which endpoint is correct and update accordingly
- Keep backward compatibility during migration

---

**Next Steps**: ✅ Implemented Trade/search endpoint integration

## Update: Fee Calculation Fix

**Issue Found**: The API's `fees` field appears to be per-side (open OR close), not round-trip commission.

**Fix Applied**: 
- Double the `fees` value from API to get full round-trip commission
- Use `net_pnl` (after fees) in statistics calculations instead of gross `pnl`
- Updated `_calculate_trade_statistics()` to prefer `net_pnl` over `pnl`

**Example**:
- API shows: `profitAndLoss: $210, fees: $3.1`
- Actual commission: $6.20 (round-trip for 5 contracts)
- Calculation: `net_pnl = $210 - ($3.1 * 2) = $203.80` ✓
