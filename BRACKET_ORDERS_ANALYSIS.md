# Bracket Orders Analysis & Implementation Plan

**Date:** November 7, 2025  
**Issue:** Current bracket implementation needs improvement for entry control

---

## 🎯 User Request

> "Want a `stop_bracket` command that uses stop order as entry instead of market order, takes an entry price, then modifies the stop/take profit to desired prices."

---

## 📊 Current Implementation Analysis

### Existing Bracket Commands

#### 1. `bracket` Command (Market Entry)
```python
bracket <symbol> <side> <quantity> <stop_ticks> <profit_ticks>
```
- **Entry**: Market order (immediate fill)
- **Protection**: Auto-creates stop-loss and take-profit
- **Pros**: Simple, fast entry
- **Cons**: No price control, may get bad fill

#### 2. `native_bracket` Command (Limit Entry)
```python
native_bracket <symbol> <side> <quantity> <stop_price> <profit_price>
```
- **Entry**: Limit order (waits for price)
- **Protection**: Native TopStepX brackets
- **Pros**: Better entry control
- **Cons**: May not fill, limited to TopStepX native brackets

### Current Limitation

**The Problem**: TopStepX native brackets are **position-based**, not OCO (One-Cancels-Other).

**What this means**:
- Brackets are attached to an existing position
- Can't create brackets *before* entry
- Can't use stop order as entry with pre-defined brackets

**Why OCO matters**:
- OCO allows: "If Entry fills, activate Stop & Profit orders"
- Without OCO: Must manually place brackets after entry fills
- Stop bracket requires: "If Stop Entry fills, place Stop Loss & Take Profit"

---

## 🔍 API Capabilities Investigation

### TopStepX Bracket Types

#### Position-Based Brackets (Current)
```json
{
  "positionId": "existing-position-id",
  "stopLossPrice": 25200,
  "takeProfitPrice": 25350
}
```
- Requires existing position first
- Attached to position, not orders
- Modified via `/api/Position/modifyStopLoss` and `/api/Position/modifyTakeProfit`

#### OCO Brackets (Desired - Unknown if Available)
```json
{
  "entryOrder": {
    "type": "stop",
    "price": 25300
  },
  "stopLoss": {
    "type": "stop",
    "price": 25250
  },
  "takeProfit": {
    "type": "limit",
    "price": 25350
  }
}
```
- All 3 orders submitted together
- If entry fills, activate SL/TP
- If entry cancelled, cancel SL/TP
- **Status**: Need to check if TopStepX supports this

---

## 💡 Proposed Solutions

### Option 1: Simulated Stop Bracket (Immediate Implementation)

**How it works**:
1. Monitor price until entry level reached
2. Place market/limit order for entry
3. Wait for fill confirmation
4. Immediately place stop-loss and take-profit

**Pros**:
- ✅ Works with current API
- ✅ Can implement today
- ✅ Gives entry price control

**Cons**:
- ⚠️ Not atomic (risk if bot crashes between steps)
- ⚠️ Small delay between entry and protection
- ⚠️ Requires active monitoring (can't "set and forget")

**Implementation**:
```python
async def stop_bracket(self, symbol: str, side: str, quantity: int, 
                       entry_price: float, stop_loss_price: float, 
                       take_profit_price: float):
    """
    Simulated stop bracket order.
    
    Monitors price, enters at entry_price, then places brackets.
    """
    # 1. Monitor price until entry level reached
    logger.info(f"Monitoring {symbol} for entry at ${entry_price:.2f}")
    
    while True:
        current_price = await self.get_current_price(symbol)
        
        if side.upper() == 'BUY':
            # For BUY: Enter when price drops to or below entry
            if current_price <= entry_price:
                break
        else:  # SELL
            # For SELL: Enter when price rises to or above entry
            if current_price >= entry_price:
                break
        
        await asyncio.sleep(0.1)  # Check every 100ms
    
    # 2. Place entry order (market for guaranteed fill)
    logger.info(f"Entry price reached! Placing {side} market order")
    entry_result = await self.place_market_order(
        symbol=symbol,
        side=side,
        quantity=quantity
    )
    
    if "error" in entry_result:
        return {"error": f"Entry failed: {entry_result['error']}"}
    
    order_id = entry_result.get('orderId')
    
    # 3. Wait for fill
    for _ in range(50):  # Wait up to 5 seconds
        fill_status = await self.check_order_status(order_id)
        if fill_status.get('status') in [2, 3, 4]:  # Filled
            break
        await asyncio.sleep(0.1)
    
    # 4. Get position ID
    positions = await self.get_open_positions()
    position_id = None
    for pos in positions:
        if pos.get('symbol') == symbol:
            position_id = pos.get('id')
            break
    
    if not position_id:
        logger.error("Position not found after fill!")
        return {"error": "Position not found"}
    
    # 5. Place brackets
    logger.info(f"Placing brackets: SL=${stop_loss_price:.2f}, TP=${take_profit_price:.2f}")
    
    # Modify stop loss
    sl_result = await self.modify_stop_loss(position_id, stop_loss_price)
    if "error" in sl_result:
        logger.error(f"Stop loss placement failed: {sl_result['error']}")
    
    # Modify take profit
    tp_result = await self.modify_take_profit(position_id, take_profit_price)
    if "error" in tp_result:
        logger.error(f"Take profit placement failed: {tp_result['error']}")
    
    return {
        "success": True,
        "order_id": order_id,
        "position_id": position_id,
        "entry_price": entry_price,
        "stop_loss": stop_loss_price,
        "take_profit": take_profit_price
    }
```

**Usage**:
```bash
stop_bracket MNQ BUY 2 25300 25250 25350
# Monitor price → Enter at 25300 → Set SL at 25250, TP at 25350
```

---

### Option 2: OCO Bracket (If API Supports)

**How it works**:
1. Check if TopStepX supports OCO orders
2. Submit all 3 orders together
3. API manages the relationship automatically

**Pros**:
- ✅ Atomic operation
- ✅ No monitoring required
- ✅ "Set and forget"
- ✅ Most robust

**Cons**:
- ❌ Unknown if TopStepX supports OCO
- ❌ May need API update from TopStepX

**Investigation Needed**:
```bash
# Try submitting OCO bracket via API
curl -X POST https://api.topstepx.com/api/Order/bracket \
  -H "Authorization: Bearer $TOKEN" \
  -d '{
    "accountId": 12694476,
    "symbol": "MNQ",
    "entryType": "stop",
    "entryPrice": 25300,
    "quantity": 2,
    "side": 0,
    "stopLossPrice": 25250,
    "takeProfitPrice": 25350
  }'
```

---

### Option 3: Hybrid Approach

**How it works**:
1. Place stop order for entry (regular stop order)
2. Monitor order status
3. When filled, immediately place brackets

**Pros**:
- ✅ Uses native stop order (guaranteed by exchange)
- ✅ Reduces monitoring to just order status
- ✅ More reliable than price monitoring

**Cons**:
- ⚠️ Still not atomic
- ⚠️ Requires bot to be running when stop triggers

**Implementation**:
```python
async def stop_bracket_hybrid(self, symbol: str, side: str, quantity: int, 
                              entry_price: float, stop_loss_price: float, 
                              take_profit_price: float):
    """
    Hybrid stop bracket using native stop order + bracket automation.
    """
    # 1. Place stop order for entry
    stop_order = await self.place_stop_order(
        symbol=symbol,
        side=side,
        quantity=quantity,
        stop_price=entry_price
    )
    
    if "error" in stop_order:
        return {"error": f"Stop order failed: {stop_order['error']}"}
    
    order_id = stop_order.get('orderId')
    logger.info(f"Stop order placed: {order_id} at ${entry_price:.2f}")
    
    # 2. Monitor order status (non-blocking background task)
    async def monitor_and_bracket():
        while True:
            status = await self.check_order_status(order_id)
            
            if status.get('status') in [2, 3, 4]:  # Filled
                logger.info(f"Stop order filled! Placing brackets")
                
                # Get position
                positions = await self.get_open_positions()
                position_id = None
                for pos in positions:
                    if pos.get('symbol') == symbol:
                        position_id = pos.get('id')
                        break
                
                if position_id:
                    await self.modify_stop_loss(position_id, stop_loss_price)
                    await self.modify_take_profit(position_id, take_profit_price)
                    logger.info(f"Brackets placed on position {position_id}")
                
                break
            elif status.get('status') in [3, 5, 6]:  # Cancelled/Rejected
                logger.warning(f"Stop order {order_id} was cancelled/rejected")
                break
            
            await asyncio.sleep(1)  # Check every second
    
    # Start monitoring in background
    asyncio.create_task(monitor_and_bracket())
    
    return {
        "success": True,
        "order_id": order_id,
        "entry_price": entry_price,
        "stop_loss": stop_loss_price,
        "take_profit": take_profit_price,
        "message": "Stop order placed, will auto-bracket on fill"
    }
```

---

## 🎯 Recommendation

### Immediate Solution: Option 3 (Hybrid Approach)

**Why?**
1. **Best balance** of reliability and simplicity
2. **Uses native stop order** - guaranteed by exchange
3. **Automated brackets** - no manual intervention
4. **Implementable today** - no API changes needed

**Implementation Steps**:
1. Add `stop_bracket` command to trading interface
2. Use existing `place_stop_order` method
3. Add background monitoring task
4. Place brackets when stop fills

### Long-term Solution: Check for OCO Support

**Action Items**:
1. **Test API**: Try submitting OCO bracket via undocumented endpoints
2. **Contact TopStepX**: Ask if OCO brackets are supported or planned
3. **Documentation**: Check if newer API docs mention OCO
4. **If available**: Refactor to use native OCO for atomic operation

---

## 📝 Implementation

### Add to trading_bot.py

```python
async def place_stop_order(self, symbol: str, side: str, quantity: int, 
                           stop_price: float, account_id: str = None) -> Dict:
    """
    Place a stop order.
    
    Args:
        symbol: Trading symbol
        side: 'BUY' or 'SELL'
        quantity: Order quantity
        stop_price: Stop trigger price
        account_id: Account ID (uses selected if not provided)
        
    Returns:
        Dict with order details or error
    """
    try:
        target_account = account_id or (self.selected_account['id'] if self.selected_account else None)
        
        if not target_account:
            return {"error": "No account selected"}
        
        # Ensure valid token
        await self._ensure_valid_token()
        
        # Normalize symbol to contract ID
        contract_id = await self._resolve_contract_id(symbol)
        if not contract_id:
            return {"error": f"Could not resolve contract ID for {symbol}"}
        
        headers = {
            "accept": "text/plain",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.session_token}"
        }
        
        order_data = {
            "accountId": int(target_account),
            "contractId": contract_id,
            "side": 0 if side.upper() == 'BUY' else 1,
            "orderType": 2,  # Stop order
            "size": quantity,
            "stopPrice": stop_price,
            "timeInForce": 0  # Day order
        }
        
        logger.info(f"Placing stop order: {side} {quantity} {symbol} @ ${stop_price:.2f}")
        
        response = self._make_curl_request("POST", "/api/Order/place", data=order_data, headers=headers)
        
        if "error" in response:
            return {"error": response["error"]}
        
        if not response.get("success"):
            return {"error": f"Stop order failed: {response}"}
        
        order_id = response.get("orderId")
        logger.info(f"Stop order placed successfully: {order_id}")
        
        return {
            "success": True,
            "orderId": order_id,
            "symbol": symbol,
            "side": side,
            "quantity": quantity,
            "stop_price": stop_price
        }
        
    except Exception as e:
        logger.error(f"Stop order placement failed: {e}")
        return {"error": str(e)}
```

### Add stop_bracket Command

```python
# In trading_interface method, add:

elif command_lower.startswith("stop_bracket "):
    # stop_bracket <symbol> <side> <quantity> <entry_price> <stop_loss_price> <take_profit_price>
    parts = command.split()
    if len(parts) < 7:
        print("Usage: stop_bracket <symbol> <side> <quantity> <entry_price> <stop_loss> <take_profit>")
        print("Example: stop_bracket MNQ BUY 2 25300 25250 25350")
        continue
    
    symbol = parts[1].upper()
    side = parts[2].upper()
    quantity = int(parts[3])
    entry_price = float(parts[4])
    stop_loss_price = float(parts[5])
    take_profit_price = float(parts[6])
    
    print(f"\n🎯 Placing Stop Bracket Order:")
    print(f"   Symbol: {symbol}")
    print(f"   Side: {side}")
    print(f"   Quantity: {quantity}")
    print(f"   Entry: ${entry_price:.2f} (stop order)")
    print(f"   Stop Loss: ${stop_loss_price:.2f}")
    print(f"   Take Profit: ${take_profit_price:.2f}")
    
    result = await self.stop_bracket_hybrid(
        symbol=symbol,
        side=side,
        quantity=quantity,
        entry_price=entry_price,
        stop_loss_price=stop_loss_price,
        take_profit_price=take_profit_price
    )
    
    if "error" in result:
        print(f"❌ Stop bracket failed: {result['error']}")
    else:
        print(f"✅ Stop bracket order placed!")
        print(f"   Order ID: {result['order_id']}")
        print(f"   📝 Will auto-bracket when stop order fills")
```

---

## ✅ Testing Plan

1. **Unit Test**: Test `place_stop_order` with mock data
2. **Integration Test**: Place real stop order (small quantity)
3. **Bracket Test**: Verify brackets place correctly after fill
4. **Edge Cases**:
   - Stop order cancelled before fill
   - Bot restart while monitoring
   - Multiple stop brackets simultaneously
5. **Performance**: Verify <1s latency from fill to brackets

---

## 🚀 Next Steps

1. **Implement** `place_stop_order` method
2. **Implement** `stop_bracket_hybrid` method
3. **Add** `stop_bracket` command to interface
4. **Test** with paper trading account
5. **Document** usage in help menu
6. **Investigate** OCO support for future upgrade

---

## 📝 Known Limitations

1. **Not Atomic**: Small window between entry and protection
2. **Requires Bot Running**: Bot must be active when stop triggers
3. **Network Risk**: If connection lost between steps
4. **Single Account**: Brackets tied to position, not order

**Mitigation**:
- Keep bot running 24/7
- Use VPS for reliability
- Implement reconnection logic
- Store bracket intentions in database for recovery

---

## 🎉 Summary

**Immediate Solution**: Hybrid stop bracket (Option 3)
- ✅ Uses native stop order for entry
- ✅ Automated bracket placement on fill
- ✅ Implementable today with existing API
- ⚠️ Not atomic, but best available option

**Long-term Goal**: True OCO brackets if/when supported

**Status**: Ready to implement! 🚀

