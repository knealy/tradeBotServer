# Reduce-Only Orders - Critical Feature

**Date:** December 17, 2025  
**Issue:** User identified orphaned orders when positions close unexpectedly

---

## Problem Statement

**User's Concern:**
> "can we actually link the manual stop/tp orders to the trade position so that when the trade closes the orders are not left orphaned still open?"

**Scenario:**
1. Plain stop order fills (position opens)
2. System auto-adds SL + TP orders (separate orders)
3. Position closes by other means (e.g., opposite signal fills, manual close, etc.)
4. **Problem:** SL + TP orders remain open (orphaned)
5. **Risk:** Orphaned orders can fill later, creating unwanted position

---

## Solution: Reduce-Only Flag

**Implementation:**

```python
# When placing SL/TP after plain stop fill
await self.trading_bot.place_stop_order(
    symbol='MNQ',
    side='SELL',  # Opposite of position
    quantity=1,
    stop_price=25380.0,
    reduce_only=True  # ← CRITICAL: Auto-cancels when position closes
)

await self.trading_bot.place_limit_order(
    symbol='MNQ',
    side='SELL',  # Opposite of position
    quantity=1,
    limit_price=25400.0,
    reduce_only=True  # ← CRITICAL: Auto-cancels when position closes
)
```

**What `reduce_only=True` Does:**

1. **Links order to position:** Order is associated with existing position
2. **Auto-cancels:** When position qty = 0, order auto-cancels
3. **Prevents orphans:** No orphaned orders left open
4. **Position-only:** Order can only reduce position, not open new one

---

## How It Works

### Without Reduce-Only (BAD)

```
09:30:00 - LONG position opens (1 contract)
09:30:05 - Place SL order (SELL 1 @ 25380)
09:30:05 - Place TP order (SELL 1 @ 25400)
09:32:00 - Opposite signal triggers, SELL 1 @ market
           Position: 0 (flat)
           SL order: Still active ❌
           TP order: Still active ❌
09:33:00 - Price drops to 25380
09:33:01 - SL order fills! NEW SHORT position created ❌
           (This was supposed to be protection, not new entry!)
```

**Problem:** Orphaned protection orders create unintended positions!

### With Reduce-Only (GOOD)

```
09:30:00 - LONG position opens (1 contract)
09:30:05 - Place SL order (SELL 1 @ 25380, reduce_only=True)
09:30:05 - Place TP order (SELL 1 @ 25400, reduce_only=True)
09:32:00 - Opposite signal triggers, SELL 1 @ market
           Position: 0 (flat)
           SL order: AUTO-CANCELLED ✅
           TP order: AUTO-CANCELLED ✅
09:33:00 - Price drops to 25380
09:33:01 - No order exists, no fill ✅
           (System prevented orphaned order from triggering)
```

**Solution:** Reduce-only orders auto-cancel, no orphans!

---

## Implementation Details

### Files Modified

#### 1. `brokers/topstepx_adapter.py`

**Added `reduce_only` parameter:**

```python
# Extract kwargs
reduce_only = kwargs.get('reduce_only', False)

# Add to order data
order_data = {
    "accountId": int(account_id),
    "contractId": contract_id,
    "type": order_type_value,
    "side": side_value,
    "size": quantity,
    "reduceOnly": reduce_only  # TopStepX API field
}
```

**Updated stop order implementation:**

```python
async def _place_stop_order_python(..., **kwargs):
    return await self._place_market_order_python(
        symbol, side, quantity, account_id,
        stop_price=stop_price,
        order_type="stop",  # Proper stop order
        **kwargs  # Passes reduce_only through
    )
```

#### 2. `strategies/overnight_range_strategy.py`

**Updated auto SL/TP placement:**

```python
# Place stop loss with reduce_only
sl_result = await self.trading_bot.place_stop_order(
    symbol=symbol,
    side="SELL" if side == "LONG" else "BUY",
    quantity=order_data['quantity'],
    stop_price=order_data['stop_loss'],
    account_id=account_id,
    reduce_only=True  # ← CRITICAL FIX
)

# Place take profit with reduce_only
tp_result = await self.trading_bot.place_limit_order(
    symbol=symbol,
    side="SELL" if side == "LONG" else "BUY",
    quantity=order_data['quantity'],
    limit_price=order_data['take_profit'],
    account_id=account_id,
    reduce_only=True  # ← CRITICAL FIX
)
```

---

## TopStepX API Details

**API Field:** `reduceOnly`  
**Type:** Boolean  
**Default:** `false`  
**Effect:** When `true`:
- Order can only reduce existing position
- Order auto-cancels when position closes
- Order will not open new position

**Example API Payload:**

```json
{
  "accountId": 12345,
  "contractId": 123,
  "type": 4,
  "side": 1,
  "size": 1,
  "stopPrice": 25380.0,
  "reduceOnly": true
}
```

---

## Use Cases

### When to Use `reduce_only=True`

✅ **Stop loss orders** (always for protection)  
✅ **Take profit orders** (always for exits)  
✅ **Trailing stops** (position protection)  
✅ **Partial exit orders** (scaling out)  
✅ **Any order meant to close a position**

### When NOT to Use `reduce_only`

❌ **Entry orders** (opening new positions)  
❌ **Reversal orders** (close + open opposite)  
❌ **Scale-in orders** (adding to position)

---

## Benefits

1. **No Orphaned Orders**
   - Orders auto-cancel when position closes
   - No manual cleanup needed
   - System is self-managing

2. **Risk Management**
   - Can't accidentally create new positions from protection orders
   - SL/TP stay linked to their position
   - Position closes = protection clears

3. **Cleaner Order Book**
   - No lingering orders after positions close
   - Easier to monitor
   - Less clutter

4. **Peace of Mind**
   - Set and forget
   - Orders automatically clean up
   - No need to manually cancel after close

---

## Testing

### Test 1: Reduce-Only Functionality

```python
# 1. Open position
await bot.place_market_order('MNQ', 'BUY', 1)

# 2. Place reduce-only SL
sl_result = await bot.place_stop_order(
    'MNQ', 'SELL', 1, 25380.0, 
    reduce_only=True
)
assert sl_result['orderId']

# 3. Close position manually
await bot.place_market_order('MNQ', 'SELL', 1)

# 4. Check SL order status
orders = await bot.get_open_orders()
# SL should be cancelled automatically
assert not any(o['orderId'] == sl_result['orderId'] for o in orders)
```

### Test 2: Plain Stop Fill → Auto SL/TP

```python
# 1. Place plain stop (fallback scenario)
entry_result = await bot.place_stop_order('MNQ', 'BUY', 1, 25390.0)

# 2. Simulate fill (position opens)
# ... wait for fill ...

# 3. Auto-monitor should add SL/TP
# (Background task runs every 5 seconds)
await asyncio.sleep(10)

# 4. Check orders
orders = await bot.get_open_orders()
sl_order = next((o for o in orders if o['type'] == 'STOP'), None)
tp_order = next((o for o in orders if o['type'] == 'LIMIT'), None)

# Both should exist and be reduce-only
assert sl_order and sl_order.get('reduceOnly') is True
assert tp_order and tp_order.get('reduceOnly') is True
```

---

## Common Scenarios

### Scenario 1: Normal Trade Flow

```
1. Bracket order succeeds
   → Entry + SL + TP all linked (native brackets)
   → All auto-cancel together ✅

2. Bracket fails (500 error)
   → Plain stop order placed
   → Position opens
   → Auto-monitor adds SL + TP (reduce_only=True)
   → SL/TP linked to position ✅
```

### Scenario 2: Manual Close

```
1. Position open (LONG 1 MNQ)
2. SL @ 25380 (reduce_only=True)
3. TP @ 25400 (reduce_only=True)
4. User manually closes: `flatten`
5. Position: 0
6. SL: AUTO-CANCELLED ✅
7. TP: AUTO-CANCELLED ✅
```

### Scenario 3: Opposite Signal

```
1. Position: LONG 1 MNQ
2. SL @ 25380 (reduce_only=True)
3. TP @ 25400 (reduce_only=True)
4. Strategy triggers SHORT signal
5. SHORT order fills → Position: 0 (or -1)
6. SL: AUTO-CANCELLED ✅
7. TP: AUTO-CANCELLED ✅
```

### Scenario 4: Partial Fill

```
1. Position: LONG 2 MNQ
2. SL @ 25380, qty=2 (reduce_only=True)
3. TP @ 25400, qty=2 (reduce_only=True)
4. TP partially fills: SELL 1 @ 25400
5. Position: LONG 1
6. SL: Remains active (qty=2, but reduce_only)
   → Will only close 1 more (max reduce to 0)
7. TP: qty reduced to 1 automatically
```

---

## Troubleshooting

### Issue: "Order rejected - insufficient position"

**Cause:** Reduce-only order qty > position qty

**Example:**
```
Position: LONG 1
Reduce-only order: SELL 2 ❌
```

**Solution:** Ensure order qty ≤ position qty

### Issue: "Reduce-only order didn't cancel"

**Possible causes:**
1. Order not marked reduce-only (check API response)
2. Position still exists (check position qty)
3. API delay (wait a few seconds)

**Debug:**
```python
# Check order
orders = await bot.get_open_orders()
order = next((o for o in orders if o['orderId'] == order_id), None)
print(f"Reduce-only: {order.get('reduceOnly')}")

# Check position
positions = await bot.get_positions()
pos = next((p for p in positions if p['symbol'] == 'MNQ'), None)
print(f"Position qty: {pos.get('net_quantity') if pos else 0}")
```

---

## Best Practices

1. **Always use reduce_only for protection orders**
   ```python
   # Good ✅
   await place_stop_order(..., reduce_only=True)
   await place_limit_order(..., reduce_only=True)
   
   # Bad ❌ (can create orphans)
   await place_stop_order(...)
   await place_limit_order(...)
   ```

2. **Log reduce-only status**
   ```python
   logger.info(f"✅ SL placed (reduce-only): {order_id}")
   # Makes debugging easier
   ```

3. **Verify in tests**
   ```python
   assert order_data.get('reduceOnly') is True
   ```

4. **Document in code**
   ```python
   # CRITICAL: reduce_only=True prevents orphaned orders
   await place_stop_order(..., reduce_only=True)
   ```

---

## Summary

**Problem:** Orphaned SL/TP orders after position closes  
**Solution:** `reduce_only=True` flag  
**Result:** Orders auto-cancel when position closes  
**Status:** ✅ Implemented in all plain stop fallback paths  

**This is a critical feature for risk management!**

Without reduce-only:
- ❌ Orphaned orders can fill later
- ❌ Create unwanted positions
- ❌ Risk is unmanaged

With reduce-only:
- ✅ Orders linked to positions
- ✅ Auto-cancel when position closes
- ✅ No manual cleanup needed
- ✅ Risk properly managed

**Always use `reduce_only=True` for exit/protection orders!**
