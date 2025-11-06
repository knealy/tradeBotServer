# Real-Time Account Tracking Implementation Status

**Date:** November 6, 2025  
**Status:** 🚧 In Progress

---

## ✅ Completed

### 1. **Real-Time Account State Tracker** (`account_tracker.py`)
**Purpose:** Track ALL account metrics locally without relying on missing API endpoints

**Features Implemented:**
- ✅ Tracks current balance, highest EOD balance, starting balance
- ✅ Computes realised PnL from filled orders
- ✅ Computes unrealised PnL from open positions + live quotes
- ✅ Tracks commissions and fees separately
- ✅ Calculates net PnL: `(realised + unrealised) - (commissions + fees)`
- ✅ Auto-detects daily/maximum loss limits based on account type
- ✅ Computes trailing drawdown threshold: `highest_EOD_balance - MLL`
- ✅ Real-time compliance checking (daily loss limit + maximum loss limit)
- ✅ EOD update logic to track new balance highs and adjust MLL threshold
- ✅ State persistence to disk (`.account_state.json`)
- ✅ Win/loss statistics tracking

**Based On:**
- TopstepX rules from `topstep_info_profile.md`
- Account limits from `topstep_dev_profile.json`
- Real-world trading requirements

**Key Methods:**
```python
# Initialize account tracking
tracker.initialize_account(account_id, name, type, starting_balance)

# Update from filled order
tracker.update_from_fill(account_id, fill_data)

# Update unrealised PnL from positions + quotes
tracker.update_unrealised_pnl(account_id, positions, current_prices)

# End-of-day update (21:00 UTC)
tracker.update_EOD(account_id)

# Get current state
state = tracker.get_state(account_id)
print(f"Balance: ${state.current_balance:,.2f}")
print(f"Net PnL: ${state.net_PnL:.2f}")
print(f"Compliant: {state.is_compliant}")
```

### 2. **Debugging Infrastructure**
- ✅ Added comprehensive logging for timestamp parsing
- ✅ Added warnings when timestamp fields are missing
- ✅ Multiple field name variations tried (time/timestamp/Time/Timestamp)

---

## 🚧 In Progress / Issues Found

### Issue #1: Timestamps Still Not Showing
**Problem:** History command output shows empty timestamp column
```
Time                       Open         High         Low          Close        Volume
----------------------------------------------------------------------------------------------------
                           $25309.50    $25313.25    $25299.75    $25303.75    706
```

**Root Cause:** Unknown - need to see debug logs
**Debug Added:** 
- Logs when timestamp is empty: `logger.warning(f"No timestamp found in bar data. Bar keys: {list(bar.keys())}")`
- Logs parsing failures with actual value

**Next Step:** Run bot with debug logging to see what the API is actually returning

---

### Issue #2: History Data is 7 Minutes Old
**Problem:** When requesting 3 bars of 1m data, it returns bars from 7 minutes ago instead of current

**Possible Causes:**
1. Cache still being used despite bypass logic
2. API time window calculation is wrong
3. API endpoint returning wrong data

**Implemented Fix:**
```python
# Bypass cache for real-time requests
if limit <= 5 and timeframe in ['1m', '5m']:
    use_fresh_data = True  # Skip cache entirely
```

**Next Step:** Test to verify fresh data is being fetched

---

### Issue #3: Trade History Not Found
**Problem:** `trades` command returns "No orders found" even when trades were made

**Current Endpoint:** `/api/Order/searchHistory` (or similar)
**Request:**
```json
{
  "accountId": 13619274,
  "startTimestamp": "2025-11-05T23:00:00+00:00",
  "endTimestamp": "2025-11-06T21:00:00+00:00",
  "request": {
    "accountId": 13619274,
    "limit": 1000
  }
}
```

**Possible Issues:**
1. Wrong endpoint path
2. Wrong parameter format
3. Time range not including today's trades
4. Need to use different API method (fills vs orders)

**Research Needed:**
- Check Python SDK docs at https://project-x-py.readthedocs.io/en/latest/
- Find correct endpoint for retrieving filled orders/trades
- May need `/api/Fill/search` instead of `/api/Order/searchHistory`

---

## 🎯 Next Steps

### Priority 1: Fix Data Quality Issues
1. **Test with Debug Logging**
   ```bash
   python trading_bot.py
   # Run: history mnq 1m 3
   # Check log output for timestamp warnings
   ```

2. **Verify API Response Structure**
   - Add temporary logging to see raw API response
   - Identify correct field names for timestamp
   - Fix parsing logic based on actual data

3. **Fix Trade History Endpoint**
   - Research correct API endpoint in SDK docs
   - Try alternative endpoints: `/api/Fill/search`, `/api/Trade/history`
   - Adjust request parameters based on working examples

### Priority 2: Integrate Account Tracker
1. **Add Tracker to Trading Bot**
   ```python
   # In trading_bot.py __init__:
   from account_tracker import AccountTracker
   self.account_tracker = AccountTracker()
   ```

2. **Initialize on Account Selection**
   ```python
   # When user selects account:
   self.account_tracker.initialize_account(
       account_id=account['id'],
       account_name=account['name'],
       account_type=account['type'],
       starting_balance=account['balance']
   )
   ```

3. **Update on Order Fills**
   ```python
   # In check_order_fills:
   for fill in new_fills:
       self.account_tracker.update_from_fill(account_id, fill_data)
   ```

4. **Update Unrealised PnL Periodically**
   ```python
   # In background worker or on quote updates:
   positions = await self.get_open_positions(account_id)
   current_prices = {pos['symbol']: self.get_current_price(pos['symbol'])}
   self.account_tracker.update_unrealised_pnl(account_id, positions, current_prices)
   ```

5. **Add EOD Scheduler**
   ```python
   # Schedule at 21:00 UTC (CME close):
   if current_time.hour == 21 and current_time.minute == 0:
       self.account_tracker.update_EOD(account_id)
   ```

### Priority 3: Add Real-Time Commands
1. **`account_state` command** - Show full real-time state
2. **`compliance` command** - Check compliance status
3. **`risk` command** - Show remaining loss capacity
4. **Auto-flatten on limit approach** - Safety feature

---

## 📊 Expected Results After Integration

### Real-Time Dashboard
```
📊 Account State - 150KTC-V2-14334-61291336
   Account ID: 13619274
   Type: Evaluation
   
   💰 Balance Information:
   Starting Balance: $150,000.00
   Current Balance: $151,875.50
   Highest EOD Balance: $152,250.75
   
   📈 PnL Breakdown:
   Realised PnL: $340.25
   Unrealised PnL: $127.50
   Commissions: $12.00
   Fees: $4.50
   Net PnL: $451.25
   
   ⚠️  Risk Limits:
   Daily Loss Limit: $3,000.00
   Remaining: $3,451.25 ✅
   
   Maximum Loss Limit: $4,500.00
   Drawdown Threshold: $147,750.75
   Current vs Threshold: $4,124.75 above ✅
   
   📊 Statistics:
   Total Trades: 15
   Winning Trades: 9 (60.0%)
   Losing Trades: 6 (40.0%)
   
   ✅ Status: COMPLIANT
```

---

## 🔧 Testing Checklist

- [ ] Timestamps display correctly in history command
- [ ] History command returns current/fresh bars (not cached old data)
- [ ] Trade history command finds and displays today's trades
- [ ] Account tracker initializes correctly
- [ ] Real-time PnL updates from position changes
- [ ] Compliance checking triggers correctly
- [ ] EOD update creates new balance highs
- [ ] State persists across bot restarts
- [ ] All commands show real data (no fallbacks)

---

## 📚 Resources

- **SDK Docs:** https://project-x-py.readthedocs.io/en/latest/
- **TopstepX Rules:** `topstep_info_profile.md`
- **Account Config:** `topstep_dev_profile.json`
- **Tracker Implementation:** `account_tracker.py`

---

**Next Session:** Run bot with debug logging, identify actual API response format, fix remaining issues, integrate account tracker.

