# Bracket Order Implementation Guide

**For Strategy Developers**  
**Last Updated:** December 17, 2025

---

## ✅ The Working Method

After extensive testing, we've confirmed that **`place_oco_bracket_with_stop_entry()`** is the **only reliable method** for placing bracket orders from strategies.

### Why This Method Works

1. **Verified through CLI testing** - The `stop_bracket` command uses this exact method
2. **Handles TopStepX API quirks** - Built-in retries, error handling, fallbacks
3. **Supports multiple order types** - OCO native, hybrid, fallback modes
4. **Automatic breakeven management** - Optional feature for risk-free trades

### Other Methods (Don't Use These)

❌ `place_market_order()` with `stop_loss_ticks`/`take_profit_ticks`
- **Problem:** Fails with 500 errors from TopStepX API
- **Status:** Unreliable for bracket orders

❌ `create_bracket_order()` 
- **Problem:** May not handle all TopStepX account configurations
- **Status:** Deprecated in favor of verified method

❌ Manual order chaining (stop + limit + limit)
- **Problem:** Complex, error-prone, race conditions
- **Status:** Unnecessary with working method

---

## 🎯 Standard Implementation Pattern

All strategies should use the **`BaseStrategy.place_bracket_order()`** helper method:

### Example Implementation

```python
from strategies.strategy_base import BaseStrategy

class MyStrategy(BaseStrategy):
    async def execute(self, signal: Dict) -> bool:
        """Execute trading signal."""
        symbol = signal['symbol']
        action = signal['action']  # "LONG" or "SHORT"
        entry_price = signal['entry_price']
        stop_loss = signal['stop_loss']
        take_profit = signal['take_profit']
        
        # Determine side
        side = "BUY" if action == "LONG" else "SELL"
        quantity = self.config.position_size
        
        # Place bracket order using verified method
        result = await self.place_bracket_order(
            symbol=symbol,
            side=side,
            quantity=quantity,
            entry_price=entry_price,
            stop_loss_price=stop_loss,
            take_profit_price=take_profit,
            enable_breakeven=False  # Set to True to enable auto-breakeven
        )
        
        # Check result
        if result.get("error"):
            logger.error(f"Order failed: {result['error']}")
            return False
        
        # Success!
        order_id = result.get('orderId')
        logger.info(f"✅ Bracket order placed: {order_id}")
        self.daily_trades += 1
        return True
```

---

## 📋 Method Signature

```python
async def place_bracket_order(
    self, 
    symbol: str,              # Trading symbol (e.g., "MNQ", "ES")
    side: str,                # "BUY" or "SELL"
    quantity: int,            # Number of contracts (1, 2, 3, ...)
    entry_price: float,       # Entry/stop price (25390.0)
    stop_loss_price: float,   # Stop loss price (25380.0)
    take_profit_price: float, # Take profit price (25400.0)
    enable_breakeven: bool = False  # Auto-breakeven (default: False)
) -> Dict
```

### Returns

```python
{
    "success": True,          # or False if error
    "orderId": 2099415779,    # TopStepX order ID
    "method": "oco_native",   # or "hybrid", "fallback"
    "error": None             # or error message if failed
}
```

---

## 🔍 Understanding Bracket Order Types

The method automatically selects the best order type based on account settings:

### 1. OCO Native (Preferred)

**When:** "Auto OCO Brackets" enabled in TopStepX account  
**How:** Single API call places stop entry + SL + TP together  
**Benefit:** Fastest, most reliable, atomic

```
API Call → Stop Entry Order → (on fill) → Auto SL + TP
```

### 2. Hybrid (Fallback)

**When:** OCO Brackets NOT enabled  
**How:** Places stop order, monitors for fill, then adds SL/TP  
**Benefit:** Works without special account settings

```
1. Place Stop Entry
2. Monitor for fill (background task)
3. On fill → Place SL + TP orders
```

### 3. Plain Stop (Emergency Fallback)

**When:** All else fails  
**How:** Just places stop entry, no protection  
**Benefit:** At least gets position entry

```
Place Stop Entry → Manual management required
```

---

## 🎨 Price Validation

The method includes automatic price validation:

### For BUY Orders

```python
entry_price = 25390.0
stop_loss = 25380.0   # Must be < entry_price ✓
take_profit = 25400.0  # Must be > entry_price ✓
```

### For SELL Orders

```python
entry_price = 25390.0
stop_loss = 25400.0   # Must be > entry_price ✓
take_profit = 25380.0  # Must be < entry_price ✓
```

If prices are invalid, the order will be rejected before API call.

---

## 🛡️ Breakeven Feature

Enable automatic breakeven stop adjustment after profit threshold:

```python
result = await self.place_bracket_order(
    symbol="MNQ",
    side="BUY",
    quantity=1,
    entry_price=25390.0,
    stop_loss_price=25380.0,
    take_profit_price=25400.0,
    enable_breakeven=True  # 🎯 Enable auto-breakeven
)
```

**How it works:**
1. Order placed with initial stop loss
2. Background monitor checks position P&L
3. When profit exceeds threshold (default: 15 points):
   - Automatically moves stop to breakeven (entry price)
   - Locks in risk-free trade
4. Take profit remains at target

**Configuration:**
```bash
# In .env
MANUAL_BREAKEVEN_PROFIT_POINTS=15.0  # Points profit before breakeven
```

---

## 📊 Testing Your Implementation

### 1. CLI Test (Confirm Method Works)

```bash
python trading_bot.py --account_select=1 --command='stop_bracket mnq buy 1 25390 25380 25400'
```

**Expected:**
```
✅ Command executed successfully
{
  "success": true,
  "orderId": 2099415779,
  "message": "Order placed successfully"
}
```

### 2. Strategy Test

```bash
# Start bot
python trading_bot.py

# Start your strategy
strategies start my_strategy

# Watch logs
tail -f trading_bot.log | grep -E "Bracket order|orderId|✅|❌"
```

**Expected:**
```
📝 my_strategy: Placing bracket order via verified path
   BUY 1 MNQ @ 25390.00, SL=25380.00, TP=25400.00
✅ my_strategy: Bracket order placed - ID: 2099415779, Method: oco_native
```

### 3. Verify in TopStepX Platform

1. Log into TopStepX web platform
2. Go to Orders → Open Orders
3. Should see:
   - Stop entry order (pending)
   - Linked SL order (pending, triggers on fill)
   - Linked TP order (pending, triggers on fill)

---

## 🚨 Common Issues & Solutions

### Issue: 500 Errors on Order Placement

**Symptoms:**
```
❌ Bracket order failed: HTTP 500: Internal Server Error
```

**Causes:**
1. TopStepX API temporarily down
2. "Auto OCO Brackets" not enabled
3. Practice account limitation
4. Invalid prices (too far from market)

**Solutions:**
1. Check TopStepX platform status
2. Enable "Auto OCO Brackets" in account settings
3. Try during market hours
4. Verify prices are within reasonable range (±100 points)

### Issue: Order Places But No SL/TP

**Symptoms:**
- Order fills
- No stop loss or take profit attached

**Causes:**
- Method fell back to "plain stop" mode
- Account doesn't support brackets

**Solutions:**
1. Check `method` in result: Should be "oco_native" or "hybrid"
2. If "plain_stop": Enable Auto OCO Brackets in TopStepX
3. Consider manually adding SL/TP after fill:
   ```python
   if result.get('method') == 'plain_stop':
       # Wait for fill
       await asyncio.sleep(2)
       # Add stop manually
       await self.trading_bot.modify_stop_loss(position_id, stop_price)
       await self.trading_bot.modify_take_profit(position_id, tp_price)
   ```

### Issue: Breakeven Not Triggering

**Symptoms:**
- Order placed with breakeven enabled
- Position in profit
- Stop not moving to breakeven

**Causes:**
- Profit threshold not reached yet
- Monitor task not running

**Check:**
```bash
# Look for breakeven monitor logs
tail -f trading_bot.log | grep breakeven
```

**Expected:**
```
🔄 Breakeven monitoring ACTIVE (+15.0 pts threshold)
✅ Moving stop to breakeven for MNQ (profit: +18.5 pts)
```

---

## 📚 Real-World Examples

### Example 1: Simple Candle Strategy

```python
class SimpleCandleStrategy(BaseStrategy):
    async def execute(self, signal: Dict) -> bool:
        symbol = signal['symbol']
        action = signal['action']
        entry_price = signal['entry_price']
        stop_loss = signal['stop_loss']
        take_profit = signal['take_profit']
        
        side = "BUY" if action == "LONG" else "SELL"
        
        # Use verified bracket order method
        result = await self.place_bracket_order(
            symbol=symbol,
            side=side,
            quantity=self.config.position_size,
            entry_price=entry_price,
            stop_loss_price=stop_loss,
            take_profit_price=take_profit
        )
        
        if result.get("error"):
            logger.error(f"Trade failed: {result['error']}")
            return False
        
        self.daily_trades += 1
        return True
```

### Example 2: Momentum Strategy with Breakeven

```python
class MomentumStrategy(BaseStrategy):
    async def execute(self, signal: Dict) -> bool:
        symbol = signal['symbol']
        action = signal['action']
        
        # Calculate prices based on ATR
        entry = signal['entry_price']
        atr = signal['atr']
        
        if action == "LONG":
            stop_loss = entry - (2 * atr)
            take_profit = entry + (3 * atr)
        else:
            stop_loss = entry + (2 * atr)
            take_profit = entry - (3 * atr)
        
        side = "BUY" if action == "LONG" else "SELL"
        
        # Place with breakeven enabled (risk-free after threshold)
        result = await self.place_bracket_order(
            symbol=symbol,
            side=side,
            quantity=self.config.position_size,
            entry_price=entry,
            stop_loss_price=stop_loss,
            take_profit_price=take_profit,
            enable_breakeven=True  # 🎯 Auto-breakeven
        )
        
        if not result.get("error"):
            logger.info(f"✅ Momentum trade placed with auto-breakeven")
            self.daily_trades += 1
            return True
        
        return False
```

### Example 3: Overnight Range Strategy

```python
class OvernightRangeStrategy(BaseStrategy):
    async def execute_breakout(self, symbol: str, direction: str, 
                              range_high: float, range_low: float) -> bool:
        """Execute breakout trade on market open."""
        
        # Calculate entry and targets
        if direction == "LONG":
            entry_price = range_high + 5.0  # 5 points above high
            stop_loss = range_low
            take_profit = entry_price + (2 * (range_high - range_low))
            side = "BUY"
        else:
            entry_price = range_low - 5.0  # 5 points below low
            stop_loss = range_high
            take_profit = entry_price - (2 * (range_high - range_low))
            side = "SELL"
        
        logger.info(f"📈 {direction} breakout on {symbol}")
        logger.info(f"   Range: {range_low:.2f} - {range_high:.2f}")
        
        # Place bracket order
        result = await self.place_bracket_order(
            symbol=symbol,
            side=side,
            quantity=self.config.position_size,
            entry_price=entry_price,
            stop_loss_price=stop_loss,
            take_profit_price=take_profit,
            enable_breakeven=True  # Move to breakeven after profit
        )
        
        if not result.get("error"):
            logger.info(f"✅ Breakout order placed: {result.get('orderId')}")
            return True
        
        logger.error(f"❌ Breakout order failed: {result.get('error')}")
        return False
```

---

## 🔧 Advanced: Customizing Bracket Behavior

### Custom Breakeven Threshold Per Strategy

```python
# Override in strategy class
class CustomStrategy(BaseStrategy):
    def __init__(self, trading_bot, config):
        super().__init__(trading_bot, config)
        self.breakeven_threshold = 20.0  # Custom threshold
    
    async def execute(self, signal: Dict) -> bool:
        # ... calculate prices ...
        
        # Use custom breakeven threshold
        result = await self.place_bracket_order(
            symbol=symbol,
            side=side,
            quantity=quantity,
            entry_price=entry,
            stop_loss_price=stop_loss,
            take_profit_price=take_profit,
            enable_breakeven=True
        )
        
        # Override default breakeven threshold
        # (requires modifying the monitor task to check strategy-specific thresholds)
        # This is advanced - usually just use env var
        
        return not result.get("error")
```

### Trailing Stop After Breakeven

```python
async def execute_with_trailing(self, signal: Dict) -> bool:
    # Place initial bracket
    result = await self.place_bracket_order(...)
    
    if not result.get("error"):
        order_id = result.get('orderId')
        
        # Start custom trailing logic after breakeven
        asyncio.create_task(
            self._monitor_trailing_stop(order_id, signal['symbol'])
        )
        
        return True
    return False

async def _monitor_trailing_stop(self, order_id: str, symbol: str):
    """Custom trailing stop after position reaches breakeven."""
    # Wait for fill
    await asyncio.sleep(5)
    
    # Monitor and trail
    highest_price = None
    trailing_amount = 10.0  # 10 points trailing
    
    while True:
        # Get position
        positions = await self.trading_bot.get_open_positions()
        position = next((p for p in positions if p['symbol'] == symbol), None)
        
        if not position:
            break  # Position closed
        
        current_price = position['currentPrice']
        
        if highest_price is None or current_price > highest_price:
            highest_price = current_price
            new_stop = highest_price - trailing_amount
            
            # Update stop loss
            await self.trading_bot.modify_stop_loss(
                position['id'], 
                new_stop
            )
            logger.info(f"📈 Trailing stop updated to {new_stop:.2f}")
        
        await asyncio.sleep(10)  # Check every 10 seconds
```

---

## ✅ Best Practices

### DO ✓

1. **Always use `BaseStrategy.place_bracket_order()`**
   - Verified working method
   - Consistent across all strategies
   - Easy to maintain

2. **Validate prices before calling**
   ```python
   if action == "LONG" and stop_loss >= entry_price:
       logger.error("Invalid stop loss for LONG")
       return False
   ```

3. **Check result for errors**
   ```python
   if result.get("error"):
       logger.error(f"Order failed: {result['error']}")
       return False
   ```

4. **Log order details**
   ```python
   order_id = result.get('orderId')
   logger.info(f"✅ Order placed: {order_id}")
   ```

5. **Track daily trades**
   ```python
   self.daily_trades += 1
   ```

### DON'T ✗

1. ❌ **Don't use `place_market_order()` for brackets**
   - Unreliable with TopStepX API
   - Use `place_bracket_order()` instead

2. ❌ **Don't manually chain orders**
   - Race conditions
   - Complex error handling
   - Use atomic bracket method

3. ❌ **Don't ignore errors**
   ```python
   # BAD:
   await self.place_bracket_order(...)
   return True  # Always returns True!
   
   # GOOD:
   result = await self.place_bracket_order(...)
   return not result.get("error")
   ```

4. ❌ **Don't place orders without position size limits**
   ```python
   # BAD:
   quantity = 100  # Hard-coded, dangerous!
   
   # GOOD:
   quantity = self.config.position_size  # From config
   quantity = min(quantity, self.config.max_positions)
   ```

---

## 📝 Summary

**For Strategy Developers:**

1. **Use `BaseStrategy.place_bracket_order()`** - It's the only reliable method
2. **This method uses the same path as the working CLI command** (`stop_bracket`)
3. **Supports OCO native, hybrid, and fallback modes** automatically
4. **Includes automatic breakeven** (optional, enable with `enable_breakeven=True`)
5. **All other methods are deprecated** for bracket orders

**The pattern is proven and tested. Just copy the example implementations above!**

---

## 🔗 Related Documentation

- `docs/AUTOMATED_TRADING_SETUP.md` - Production deployment guide
- `docs/system_lifecycle.md` - System architecture overview
- `docs/STRATEGY_TRADING_FIX.md` - Recent strategy fixes
- `strategies/strategy_base.py` - BaseStrategy source code
- `trading_bot.py` - place_oco_bracket_with_stop_entry implementation

---

**Questions?** Check the logs:
```bash
tail -f trading_bot.log | grep -E "Bracket|place_bracket_order|oco_native"
```
