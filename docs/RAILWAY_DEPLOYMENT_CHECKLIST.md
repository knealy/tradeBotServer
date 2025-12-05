# Railway Deployment Checklist - Account Fix

## Pre-Deployment Verification

### 1. Local Testing
Run the verification script to ensure everything works locally:

```bash
python verify_account_fix.py
```

Expected output:
```
✅ PASS: Env Vars
✅ PASS: Database
✅ PASS: Account Tracker
✅ PASS: Account Selection
🎉 All checks passed! System is ready for deployment.
```

### 2. Review Changes
Verify all files were updated correctly:

```bash
# Check for any remaining typos
grep -r "TOPSETPX_ACCOUNT_ID" --exclude-dir=.git --exclude-dir=venv

# Should return: (no matches)
```

---

## Railway Deployment Steps

### Step 1: Verify Environment Variables

```bash
railway variables
```

**Required variables** (verify these are set):
- ✅ `TOPSTEPX_ACCOUNT_ID=12694476` (practice account)
- ✅ `DATABASE_URL=postgresql://...` (should already exist)
- ✅ `TOPSTEPX_API_KEY=...`
- ✅ `TOPSTEPX_USERNAME=cloutrades`

**Remove if present** (old typo):
- ❌ `TOPSETPX_ACCOUNT_ID` (delete this if it exists)

To remove the old typo:
```bash
railway variables delete TOPSETPX_ACCOUNT_ID
```

### Step 2: Commit and Push Changes

```bash
# Stage all changes
git add .

# Commit with descriptive message
git commit -m "Fix account selection typo and migrate to Postgres

- Fixed TOPSETPX_ACCOUNT_ID → TOPSTEPX_ACCOUNT_ID typo
- Migrated AccountTracker to use Postgres instead of JSON
- Updated all server startup code for consistent account selection
- Added verification script and documentation"

# Push to Railway
git push origin main
```

### Step 3: Monitor Deployment

Watch the Railway logs during deployment:

```bash
railway logs --follow
```

**Look for these key log lines**:

1. **Database initialization**:
   ```
   ✅ PostgreSQL database initialized
   ```

2. **Account tracker initialization**:
   ```
   Account tracker initialized with database support
   ```

3. **Account selection** (MOST IMPORTANT):
   ```
   ✅ Selected account: PRAC-V2-14334-56363256 (source=env)
   ```
   
   **NOT**:
   ```
   ✅ Auto-selected account: 150KTC-V2-... (wrong!)
   ```

4. **Strategy initialization**:
   ```
   🚀 Auto-starting enabled strategies on server startup...
   ```

### Step 4: Verify Database

Connect to Railway Postgres and verify account state is being saved:

```bash
# Connect to Railway Postgres
railway connect postgres

# In psql:
SELECT account_id, account_name, balance, last_updated 
FROM account_state 
ORDER BY last_updated DESC;
```

Expected output:
```
 account_id |       account_name        |  balance   |       last_updated        
------------+---------------------------+------------+---------------------------
 12694476   | PRAC-V2-14334-56363256    | 158000.32  | 2025-11-22 20:30:15.123
```

### Step 5: Test Trading

1. **Wait for market open** (or use `strategy_execute` for testing)

2. **Monitor logs** for order placement:
   ```bash
   railway logs --follow | grep -i "order\|account"
   ```

3. **Verify orders are placed on correct account**:
   Look for log lines like:
   ```
   📊 Placing order on account 12694476 (PRAC-V2-14334-56363256)
   ```

4. **Check TopStepX platform**:
   - Log into TopStepX
   - Navigate to practice account `PRAC-V2-14334-56363256`
   - Verify orders appear there (NOT on eval accounts)

---

## Rollback Plan

If something goes wrong:

### Option 1: Quick Fix (Environment Variable)
If wrong account is still being selected:

```bash
# Double-check the env var is set correctly
railway variables get TOPSTEPX_ACCOUNT_ID

# If it's wrong, fix it:
railway variables set TOPSTEPX_ACCOUNT_ID="12694476"

# Restart the service
railway restart
```

### Option 2: Full Rollback
If the deployment is completely broken:

```bash
# Revert the commit
git revert HEAD

# Push the revert
git push origin main

# Railway will auto-deploy the reverted code
```

---

## Success Criteria

✅ **Deployment is successful if**:

1. Railway logs show: `✅ Selected account: PRAC-V2-... (source=env)`
2. Postgres `account_state` table has data for account `12694476`
3. Test orders are placed on practice account, not eval accounts
4. Dashboard shows correct account as selected
5. Account state persists across restarts

❌ **Deployment failed if**:

1. Logs show: `✅ Auto-selected account: 150KTC-V2-...` (wrong account)
2. Orders are placed on eval accounts
3. Database errors in logs
4. Account state table is empty

---

## Post-Deployment Verification

### 1. Check Account Selection
```bash
railway logs | grep "Selected account"
```

Should show:
```
✅ Selected account: PRAC-V2-14334-56363256 (source=env)
```

### 2. Check Database
```bash
railway run python -c "
from infrastructure.database import get_database
db = get_database()
state = db.get_account_state('12694476')
print('Account state:', state)
"
```

### 3. Test Strategy Execution
```bash
# Trigger a test execution (if market is closed)
railway run python -c "
import asyncio
from trading_bot import TopStepXTradingBot

async def test():
    bot = TopStepXTradingBot()
    await bot.authenticate()
    accounts = await bot.list_accounts()
    print('Available accounts:', [a['name'] for a in accounts])
    print('Selected account:', bot.selected_account)

asyncio.run(test())
"
```

---

## Troubleshooting

### Issue: Wrong account still selected

**Check**:
```bash
railway variables | grep ACCOUNT_ID
```

**Fix**:
```bash
railway variables set TOPSTEPX_ACCOUNT_ID="12694476"
railway restart
```

### Issue: Database connection errors

**Check**:
```bash
railway variables get DATABASE_URL
```

**Fix**:
Ensure DATABASE_URL is set and Railway Postgres is provisioned.

### Issue: Account state not saving

**Check logs**:
```bash
railway logs | grep "account state"
```

**Fix**:
Verify `account_state` table exists in Postgres:
```sql
CREATE TABLE IF NOT EXISTS account_state (
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

---

## Contact

If you encounter issues not covered here:
1. Check Railway logs: `railway logs`
2. Review [ACCOUNT_SELECTION_FIX.md](docs/ACCOUNT_SELECTION_FIX.md)
3. Run local verification: `python verify_account_fix.py`

---

**Last Updated**: November 22, 2025  
**Status**: Ready for deployment

