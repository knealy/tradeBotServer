# Strategy Executor Fix

**Date:** December 17, 2025  
**Issue:** `AttributeError: 'TopStepXTradingBot' object has no attribute 'switch_account'`

---

## Problem

When running the strategy executor with account selection:

```bash
python core/strategy_executor.py --strategy=simple_candle --account_select=1 --symbols=mnq
```

Error occurred:
```
AttributeError: 'TopStepXTradingBot' object has no attribute 'switch_account'. Did you mean: 'select_account'?
```

**Root Cause:** The strategy executor was calling `trading_bot.switch_account()` which didn't exist. The existing `select_account()` method was designed for interactive user selection, not programmatic account switching.

---

## Solution

### 1. Added `switch_account()` Method

Created a new async method in `trading_bot.py` that:
- Accepts account ID or index as a string
- Fetches available accounts
- Finds the account by ID or index
- Sets it as `selected_account`
- Initializes the account tracker
- Updates the order executor
- Returns success/failure status

**Method Signature:**
```python
async def switch_account(self, account_identifier: str) -> bool:
    """
    Switch to a different account by ID or index.
    Used by strategy executor and programmatic account switching.
    
    Args:
        account_identifier: Account ID or 1-based index as string
        
    Returns:
        bool: True if account switched successfully, False otherwise
    """
```

**Features:**
- Tries index first (1-based, e.g., "1", "2", "3")
- Falls back to account ID or name matching
- Properly initializes account tracker with balance and type
- Updates order executor
- Logs all actions
- Returns clear success/failure status

### 2. Updated Strategy Executor

Modified `core/strategy_executor.py` to:
- Convert account_id to string before calling `switch_account`
- Check return value for success
- Exit gracefully if account switch fails
- Log appropriate messages

**Changes:**
```python
# Before:
await self.trading_bot.switch_account(account_id)

# After:
success = await self.trading_bot.switch_account(str(account_id))
if not success:
    logger.error(f"❌ Failed to switch to account: {account_id}")
    return
```

---

## Files Modified

1. **`trading_bot.py`**
   - Added `switch_account()` method (55 lines)
   - Placed before `select_account()` for logical organization

2. **`core/strategy_executor.py`**
   - Updated account switching logic (5 lines)
   - Added error handling

---

## Usage

### Strategy Executor with Account Selection

```bash
# By index (1-based)
python core/strategy_executor.py --strategy=simple_candle --account_select=1 --symbols=mnq

# By account ID
python core/strategy_executor.py --strategy=simple_candle --account_select=123456 --symbols=mnq

# Multiple strategies
python core/strategy_executor.py --all --account_select=1 --symbols=mnq,mes
```

### Programmatic Account Switching

```python
# In code
success = await trading_bot.switch_account("1")  # By index
if success:
    print("Account switched!")

# Or by ID
success = await trading_bot.switch_account("123456")
```

---

## Comparison: select_account vs switch_account

| Feature | `select_account()` | `switch_account()` |
|---------|-------------------|-------------------|
| **Mode** | Interactive | Programmatic |
| **Input** | User prompt | Parameter |
| **Async** | No (sync) | Yes (async) |
| **Fetches Accounts** | No (requires list) | Yes (auto-fetches) |
| **Returns** | Account dict or None | bool (success/failure) |
| **Use Case** | CLI interaction | Scripts, executors |

---

## Testing

### Test Strategy Executor

```bash
# 1. Run with account selection
python core/strategy_executor.py --strategy=simple_candle --account_select=1 --symbols=mnq

# Expected output:
# 📁 Loading environment variables from .env file...
# ✅ Environment variables loaded successfully
# 🚀 Strategy Executor starting...
# ✅ Switched to account: 1
# 📋 Fetching available contracts...
# ✅ Loaded X contracts
# 🚀 Starting strategy: simple_candle
# ...
```

### Test Programmatic Switching

```python
import asyncio
from trading_bot import TopStepXTradingBot

async def test():
    bot = TopStepXTradingBot(api_key="...", username="...")
    await bot.authenticate()
    
    # Switch by index
    success = await bot.switch_account("1")
    print(f"Switch by index: {success}")
    
    # Switch by ID
    success = await bot.switch_account("123456")
    print(f"Switch by ID: {success}")

asyncio.run(test())
```

---

## Benefits

### For Strategy Executor
- ✅ Works with `--account_select` flag
- ✅ Clean error handling
- ✅ No interactive prompts
- ✅ Proper account initialization

### For Developers
- ✅ Programmatic account switching
- ✅ Clear API (returns bool)
- ✅ Async/await compatible
- ✅ Auto-fetches accounts

### For System
- ✅ Account tracker properly initialized
- ✅ Order executor updated
- ✅ All components in sync
- ✅ Clean logging

---

## Error Handling

The method handles several edge cases:

1. **No accounts available**
   ```
   ❌ ERROR: No accounts available
   Returns: False
   ```

2. **Account not found**
   ```
   ❌ ERROR: Account not found: 999
   Returns: False
   ```

3. **Authentication failure** (before switch_account called)
   ```
   ❌ ERROR: Authentication failed
   (Strategy executor exits)
   ```

4. **Invalid index**
   ```
   ❌ ERROR: Account not found: 10
   Returns: False
   ```

---

## Integration Points

The `switch_account` method is now used by:

1. **Strategy Executor** - Account selection before strategy start
2. **Future Features** - Programmatic account switching
3. **API Endpoints** - When/if account switching API is added
4. **Testing** - Automated tests with account selection

---

## Next Steps

### Recommended Enhancements (Optional)

1. **Add to CLI Commands**
   ```bash
   # In trading_bot.py interactive mode:
   switch_account 2
   # Switches to account index 2
   ```

2. **Add to Dashboard**
   - GUI dropdown to switch accounts
   - Calls `switch_account()` via API
   - Updates all panels

3. **Add Account Caching**
   - Cache account list for 60 seconds
   - Reduce API calls during rapid switching

4. **Add Validation**
   - Check account status before switching
   - Warn if account is suspended/inactive

---

## Conclusion

The strategy executor now works correctly with account selection. Users can:
- ✅ Run strategies with `--account_select` flag
- ✅ Switch accounts programmatically in code
- ✅ Trust that account tracker is initialized
- ✅ See proper logging of account switches

**Status:** Fixed and tested ✅
