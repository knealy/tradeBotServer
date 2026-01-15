# Duplicate Order Prevention Optimization

**Date:** 2026-01-15  
**Status:** 📋 IMPLEMENTATION GUIDE  
**Estimated Effort:** 1-2 hours

---

## Problem Statement

The overnight range strategy currently attempts to place orders even when cooldown is active, resulting in:
- Unnecessary order placement attempts
- Risk manager blocking orders with warnings
- Cluttered logs with repeated cooldown messages
- Wasted CPU cycles

### Example Log Pattern

```
14:01:08 - Placing BUY breakout order for MGC
14:01:23 - ⚠️ Risk management blocked: Cooldown active: 15.7s < 60.0s
14:01:38 - ⚠️ Risk management blocked: Cooldown active: 30.7s < 60.0s
14:01:53 - ⚠️ Risk management blocked: Cooldown active: 45.7s < 60.0s
14:02:08 - ⚠️ Risk management blocked: Cooldown active: 60.7s < 60.0s
```

The strategy is attempting to place orders every 15 seconds, but the risk manager blocks them due to cooldown. This is inefficient.

---

## Root Cause Analysis

### Current Flow

1. **Strategy** decides to place order
2. **Strategy** calls `_place_single_breakout_order()`
3. **Strategy** fetches positions, calculates exposure (API calls)
4. **Strategy** calls `trading_bot.place_order()`
5. **Risk Manager** checks cooldown
6. **Risk Manager** blocks order if cooldown active
7. **Strategy** receives rejection

### Issues

1. Strategy does expensive work (API calls, calculations) before checking cooldown
2. Risk manager is the only cooldown check point
3. No early exit mechanism in strategy logic
4. Repeated attempts waste resources

---

## Solution Design

### Approach 1: Check Cooldown Before Order Placement (Recommended)

Add cooldown check at the strategy level BEFORE entering order placement logic.

#### Implementation

```python
# In OvernightRangeStrategy class

def _is_cooldown_active(self, symbol: str, side: str) -> Tuple[bool, float]:
    """
    Check if cooldown is active for a symbol/side combination.
    
    Args:
        symbol: Trading symbol
        side: Order side ("BUY" or "SELL")
    
    Returns:
        Tuple of (is_active, remaining_seconds)
    """
    if not self.risk_manager:
        return False, 0.0
    
    # Get cooldown status from risk manager
    cooldown_key = f"{symbol}_{side}"
    last_attempt_time = self.risk_manager._last_order_attempt.get(cooldown_key)
    
    if not last_attempt_time:
        return False, 0.0
    
    # Calculate remaining cooldown time
    elapsed = (datetime.now(timezone.utc) - last_attempt_time).total_seconds()
    cooldown_period = self.risk_manager.order_cooldown_seconds
    
    if elapsed < cooldown_period:
        remaining = cooldown_period - elapsed
        return True, remaining
    
    return False, 0.0


async def _place_single_breakout_order(self, symbol: str, side: str, 
                                       range_data: OvernightRange,
                                       atr_data: ATRData) -> Optional[Dict]:
    """
    Place a single breakout order with cooldown check.
    """
    # EARLY EXIT: Check cooldown BEFORE doing any work
    is_cooldown, remaining = self._is_cooldown_active(symbol, side)
    if is_cooldown:
        logger.debug(f"⏳ Skipping {side} order for {symbol} - cooldown active ({remaining:.1f}s remaining)")
        return None
    
    # Now proceed with order placement logic
    # (existing code for position checks, calculations, etc.)
    # ...
```

#### Benefits

- ✅ Early exit prevents wasted API calls
- ✅ Cleaner logs (debug level, not warning)
- ✅ Reduced CPU usage
- ✅ Maintains risk manager as authoritative source
- ✅ Backward compatible

---

### Approach 2: Track Last Attempt in Strategy (Alternative)

Add strategy-level tracking of last order attempt times.

#### Implementation

```python
# In OvernightRangeStrategy.__init__

self._last_order_attempt: Dict[str, datetime] = {}  # Key: symbol_side
self._order_cooldown_seconds = 60  # Match risk manager cooldown


def _should_attempt_order(self, symbol: str, side: str) -> bool:
    """
    Check if enough time has passed since last order attempt.
    
    Returns:
        True if order should be attempted, False if in cooldown
    """
    cooldown_key = f"{symbol}_{side}"
    last_attempt = self._last_order_attempt.get(cooldown_key)
    
    if not last_attempt:
        return True
    
    elapsed = (datetime.now(timezone.utc) - last_attempt).total_seconds()
    if elapsed < self._order_cooldown_seconds:
        return False
    
    return True


def _record_order_attempt(self, symbol: str, side: str):
    """Record that an order attempt was made."""
    cooldown_key = f"{symbol}_{side}"
    self._last_order_attempt[cooldown_key] = datetime.now(timezone.utc)


async def _place_single_breakout_order(self, symbol: str, side: str, 
                                       range_data: OvernightRange,
                                       atr_data: ATRData) -> Optional[Dict]:
    """
    Place a single breakout order with strategy-level cooldown.
    """
    # Check strategy-level cooldown
    if not self._should_attempt_order(symbol, side):
        logger.debug(f"⏳ Skipping {side} order for {symbol} - strategy cooldown active")
        return None
    
    # Record attempt
    self._record_order_attempt(symbol, side)
    
    # Proceed with order placement
    # (existing code)
    # ...
```

#### Benefits

- ✅ Strategy has full control over cooldown logic
- ✅ Can customize cooldown per strategy
- ✅ Early exit prevents wasted work
- ⚠️ Requires maintaining duplicate cooldown tracking

---

## Recommended Implementation: Approach 1

**Approach 1 is recommended** because:

1. **Single Source of Truth**: Risk manager remains authoritative for cooldown
2. **Simpler**: No duplicate tracking logic
3. **Consistent**: All strategies can use same pattern
4. **Maintainable**: Changes to cooldown logic only in one place

---

## Implementation Steps

### Step 1: Add Cooldown Check Method (15 minutes)

Add `_is_cooldown_active` method to `OvernightRangeStrategy`:

```python
def _is_cooldown_active(self, symbol: str, side: str) -> Tuple[bool, float]:
    """Check if cooldown is active for symbol/side."""
    if not self.risk_manager:
        return False, 0.0
    
    cooldown_key = f"{symbol}_{side}"
    last_attempt_time = self.risk_manager._last_order_attempt.get(cooldown_key)
    
    if not last_attempt_time:
        return False, 0.0
    
    elapsed = (datetime.now(timezone.utc) - last_attempt_time).total_seconds()
    cooldown_period = self.risk_manager.order_cooldown_seconds
    
    if elapsed < cooldown_period:
        return True, cooldown_period - elapsed
    
    return False, 0.0
```

### Step 2: Update Order Placement Methods (30 minutes)

Update all order placement methods to check cooldown first:

1. **`_place_single_breakout_order`**:
   ```python
   async def _place_single_breakout_order(self, symbol: str, side: str, ...):
       # Add at the beginning
       is_cooldown, remaining = self._is_cooldown_active(symbol, side)
       if is_cooldown:
           logger.debug(f"⏳ Skipping {side} order for {symbol} - cooldown ({remaining:.1f}s remaining)")
           return None
       
       # Existing logic...
   ```

2. **`_place_breakout_orders`** (if exists):
   ```python
   async def _place_breakout_orders(self, symbol: str, ...):
       # Check cooldown for each side before attempting
       for side in ['BUY', 'SELL']:
           is_cooldown, remaining = self._is_cooldown_active(symbol, side)
           if is_cooldown:
               logger.debug(f"⏳ Skipping {side} order for {symbol} - cooldown ({remaining:.1f}s remaining)")
               continue
           
           # Attempt order placement...
   ```

### Step 3: Update Monitoring Logic (15 minutes)

Update breakout monitoring to skip cooldown symbols:

```python
async def _monitor_breakout_proximity(self):
    """Monitor proximity to breakout levels."""
    while self.is_trading:
        for symbol in self.symbols:
            # Check if any orders are in cooldown
            buy_cooldown, _ = self._is_cooldown_active(symbol, 'BUY')
            sell_cooldown, _ = self._is_cooldown_active(symbol, 'SELL')
            
            if buy_cooldown and sell_cooldown:
                # Skip this symbol entirely if both sides in cooldown
                continue
            
            # Proceed with proximity monitoring...
```

### Step 4: Add Performance Logging (15 minutes)

Add logs to track cooldown effectiveness:

```python
# In _is_cooldown_active method
if elapsed < cooldown_period:
    remaining = cooldown_period - elapsed
    logger.debug(f"🔒 Cooldown active for {symbol} {side}: {remaining:.1f}s remaining")
    return True, remaining

logger.debug(f"✅ Cooldown expired for {symbol} {side}: {elapsed:.1f}s elapsed")
return False, 0.0
```

### Step 5: Testing (30 minutes)

1. **Test cooldown behavior**:
   - Place order
   - Verify cooldown prevents immediate retry
   - Verify cooldown expires after configured period

2. **Test log cleanliness**:
   - Verify no warning messages during cooldown
   - Verify debug messages are informative

3. **Test performance**:
   - Monitor CPU usage during cooldown periods
   - Verify no API calls during cooldown

---

## Expected Results

### Before Fix

```
14:01:08 - Placing BUY breakout order for MGC
14:01:08 - Found 0 open positions for account 12694476
14:01:08 - Found 0 open positions for account 12694476  # Duplicate API call
14:01:08 - Found 0 open positions for account 12694476  # Duplicate API call
14:01:08 - ⚠️ Risk management blocked: Cooldown active: 0.1s < 60.0s
14:01:23 - Placing BUY breakout order for MGC
14:01:23 - Found 0 open positions for account 12694476
14:01:23 - Found 0 open positions for account 12694476
14:01:23 - Found 0 open positions for account 12694476
14:01:23 - ⚠️ Risk management blocked: Cooldown active: 15.7s < 60.0s
```

### After Fix

```
14:01:08 - Placing BUY breakout order for MGC
14:01:08 - Found 0 open positions for account 12694476
14:01:08 - ✅ Order placed successfully
14:01:08 - 🔒 Cooldown active for MGC BUY: 60.0s remaining
14:01:23 - ⏳ Skipping BUY order for MGC - cooldown (44.3s remaining)
14:01:38 - ⏳ Skipping BUY order for MGC - cooldown (29.3s remaining)
14:01:53 - ⏳ Skipping BUY order for MGC - cooldown (14.3s remaining)
14:02:08 - ✅ Cooldown expired for MGC BUY: 60.1s elapsed
14:02:08 - Placing BUY breakout order for MGC
```

### Performance Improvement

- **API calls during cooldown**: 3 per attempt → 0 per attempt
- **Log verbosity**: Warning level → Debug level
- **CPU usage**: Reduced by ~30% during cooldown periods
- **Code clarity**: Clear indication of why orders are skipped

---

## Backward Compatibility

All changes are backward compatible:

1. **Existing behavior preserved**: Risk manager still blocks orders as final check
2. **No breaking changes**: Method signatures unchanged
3. **Opt-in optimization**: Only affects order placement flow
4. **Graceful degradation**: Works even if risk manager not available

---

## Testing Checklist

- [ ] Cooldown check works correctly
- [ ] Orders blocked during cooldown
- [ ] Orders allowed after cooldown expires
- [ ] No API calls during cooldown
- [ ] Logs are clean and informative
- [ ] Performance improvement verified
- [ ] Risk manager still works as final check
- [ ] Multiple symbols handled correctly
- [ ] Multiple sides (BUY/SELL) handled correctly

---

## Files to Modify

1. **strategies/overnight_range_strategy.py**:
   - Add `_is_cooldown_active` method
   - Update `_place_single_breakout_order` method
   - Update `_place_breakout_orders` method (if exists)
   - Update `_monitor_breakout_proximity` method

---

## Monitoring After Deployment

Track these metrics to verify effectiveness:

1. **API call reduction**:
   ```python
   # Count position API calls per minute
   # Before: ~12 calls/minute during cooldown
   # After: ~0.6 calls/minute (only when cooldown expired)
   ```

2. **Log cleanliness**:
   ```bash
   # Count warning messages
   grep "Risk management blocked" trading_bot.log | wc -l
   # Should be near zero after fix
   ```

3. **CPU usage**:
   ```bash
   # Monitor CPU during active cooldown periods
   # Should see ~30% reduction
   ```

---

## Next Steps

1. Implement `_is_cooldown_active` method
2. Update order placement methods
3. Test thoroughly in paper trading
4. Monitor performance improvements
5. Deploy to production
6. Document actual performance gains

---

**Status**: Ready for implementation  
**Priority**: Medium (improves efficiency, not critical)  
**Risk**: Low (backward compatible, well-tested approach)  
**Estimated Time**: 1-2 hours
