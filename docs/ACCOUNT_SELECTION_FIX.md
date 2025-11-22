# Account Selection & Postgres Migration Fix

## Summary

Fixed critical account selection bug and migrated account state tracking to Postgres.

**Date**: November 22, 2025  
**Status**: ✅ Complete

---

## Problem 1: Wrong Account Selected on Railway

### Root Cause
The async webhook server had a **typo** in the environment variable name:
- **Code looked for**: `TOPSETPX_ACCOUNT_ID` (missing second "S")
- **Railway had set**: `TOPSTEPX_ACCOUNT_ID` (correct spelling)

When the env var lookup failed, the server defaulted to `accounts[0]`, which was an eval account instead of the intended practice account.

### Impact
- Automated trades were placed on account `14449349` (eval) instead of `12694476` (practice)
- User had correctly set `TOPSTEPX_ACCOUNT_ID=12694476` in Railway, but it was never read
- Dashboard showed correct account, but backend ignored it

### Fix
✅ **Corrected all instances** of `TOPSETPX_ACCOUNT_ID` → `TOPSTEPX_ACCOUNT_ID` across:
- `servers/async_webhook_server.py`
- `servers/start_async_webhook.py`
- `servers/dashboard_api_server.py`
- `tests/validate_deployment.py`
- `tests/test_fixed_system.py`
- All documentation files (README, deployment guides)

---

## Problem 2: Account State Not Persisted in Database

### Root Cause
The `AccountTracker` class was still using JSON file persistence (`.account_state.json`) instead of the Postgres `account_state` table. This meant:
- Dashboard settings for default account were never saved
- Account state didn't survive restarts on Railway
- The `account_state` table remained empty

### Impact
- No persistent default account across restarts
- Dashboard couldn't remember user's account selection
- State was lost when Railway redeployed

### Fix
✅ **Migrated `AccountTracker` to use Postgres**:

1. **Updated `core/account_tracker.py`**:
   - Added `db` parameter to `__init__`
   - Modified `_save_state()` to write to database first, JSON as fallback
   - Modified `_load_state()` to read from database first, JSON as fallback
   - Maps `AccountState` fields to database schema

2. **Updated `trading_bot.py`**:
   - Initialize database **before** `AccountTracker`
   - Pass `db` instance to `AccountTracker(db=self.db)`

3. **Updated server startup code**:
   - `servers/async_webhook_server.py`: Enhanced account selection logic
   - `servers/dashboard_api_server.py`: Added same account selection logic
   - Both now check: env var → persisted settings → first account

---

## Account Selection Priority (New Behavior)

All servers now follow this priority order:

1. **Environment Variable** (`TOPSTEPX_ACCOUNT_ID`)
   - Highest priority
   - Set in Railway variables
   - Overrides everything else

2. **Persisted Settings** (from database)
   - Dashboard saves user's selected default account
   - Survives restarts

3. **First Available Account** (fallback)
   - Only used if no env var and no persisted setting
   - Logs a warning

### Example Logs

**With env var set**:
```
✅ Selected account: PRAC-V2-14334-56363256 (source=env)
```

**With persisted setting**:
```
📝 Persisted default account from settings: 12694476
✅ Selected account: PRAC-V2-14334-56363256 (source=settings)
```

**Fallback**:
```
✅ Auto-selected first account: PRAC-V2-14334-56363256
```

---

## Database Schema

The `account_state` table is now actively used:

```sql
CREATE TABLE account_state (
    account_id TEXT PRIMARY KEY,
    account_name TEXT,
    balance NUMERIC(12,2),
    starting_balance NUMERIC(12,2),
    daily_pnl NUMERIC(12,2),
    dll_remaining NUMERIC(12,2),
    mll_remaining NUMERIC(12,2),
    total_trades_today INTEGER DEFAULT 0,
    winning_trades_today INTEGER DEFAULT 0,
    losing_trades_today INTEGER DEFAULT 0,
    metadata JSONB,
    last_updated TIMESTAMP DEFAULT NOW()
);
```

**Stored in `metadata` JSONB**:
- `account_type`
- `highest_EOD_balance`
- `realised_PnL`
- `unrealised_PnL`
- `commissions`
- `fees`
- `daily_loss_limit`
- `maximum_loss_limit`
- `drawdown_threshold`
- `is_compliant`
- `violation_reason`
- `last_update`
- `last_EOD_update`

---

## Files Changed

### Core Components
- ✅ `core/account_tracker.py` - Postgres integration
- ✅ `trading_bot.py` - Initialize tracker with database

### Server Components
- ✅ `servers/async_webhook_server.py` - Fixed typo, enhanced account selection
- ✅ `servers/start_async_webhook.py` - Fixed typo
- ✅ `servers/dashboard_api_server.py` - Enhanced account selection

### Tests
- ✅ `tests/validate_deployment.py` - Fixed typo
- ✅ `tests/test_fixed_system.py` - Fixed typo

### Documentation
- ✅ `README.md`
- ✅ `docs/DEPLOYMENT_GUIDE.md`
- ✅ `docs/RAILWAY_SETUP_COMPLETE.md`
- ✅ `docs/RAILWAY_DEPLOYMENT.md`
- ✅ `docs/DEPLOY_QUICK_START.md`

---

## Testing Checklist

### Local Testing
- [ ] Run `python trading_bot.py` and verify account selection
- [ ] Check that `.account_state.json` is no longer created (if DB available)
- [ ] Verify account state persists across restarts

### Railway Testing
- [x] Verify `TOPSTEPX_ACCOUNT_ID=12694476` is set in Railway variables
- [ ] Deploy and check logs for: `✅ Selected account: PRAC-V2-... (source=env)`
- [ ] Verify trades are placed on correct account
- [ ] Check Postgres `account_state` table has data:
  ```sql
  SELECT account_id, account_name, balance, last_updated 
  FROM account_state;
  ```

### Dashboard Testing
- [ ] Select default account in dashboard
- [ ] Restart server
- [ ] Verify default account is remembered
- [ ] Check `/api/account/info` returns correct account

---

## Deployment Steps

1. **Commit changes**:
   ```bash
   git add .
   git commit -m "Fix account selection typo and migrate to Postgres"
   git push
   ```

2. **Verify Railway env vars**:
   ```bash
   railway variables
   # Confirm TOPSTEPX_ACCOUNT_ID=12694476 (not TOPSETPX)
   ```

3. **Deploy to Railway**:
   - Railway will auto-deploy on push
   - Or manually: `railway up`

4. **Monitor logs**:
   ```bash
   railway logs
   # Look for: "✅ Selected account: PRAC-V2-... (source=env)"
   ```

5. **Verify database**:
   - Connect to Railway Postgres
   - Check `account_state` table is populated

---

## Rollback Plan

If issues occur:

1. **Revert to JSON fallback**:
   - The code still supports JSON file fallback
   - If database fails, it automatically uses `.account_state.json`

2. **Revert account selection**:
   - Set `TOPSTEPX_ACCOUNT_ID` explicitly in Railway
   - This overrides all other logic

3. **Full rollback**:
   ```bash
   git revert HEAD
   git push
   ```

---

## Future Improvements

1. **Add account switching API**:
   - Allow dashboard to switch accounts dynamically
   - Persist selection to database

2. **Add account state history**:
   - Track balance changes over time
   - Create `account_state_history` table

3. **Add account state validation**:
   - Verify account exists before selection
   - Alert if account becomes inactive

4. **Add multi-account support**:
   - Allow strategies to run on multiple accounts
   - Track state separately per account

---

## Related Documentation

- [Database Architecture](DATABASE_ARCHITECTURE.md)
- [Railway Deployment](RAILWAY_DEPLOYMENT.md)
- [Account Tracker API](../core/account_tracker.py)
- [Dashboard Settings](DASHBOARD_SETTINGS.md)

---

## Questions?

If you encounter issues:
1. Check Railway logs: `railway logs`
2. Verify env vars: `railway variables`
3. Check database: Connect to Postgres and query `account_state` table
4. Review this document for troubleshooting steps

