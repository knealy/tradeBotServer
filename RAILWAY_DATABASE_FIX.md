# 🔧 Fix Railway DATABASE_URL Issue

## Problem
Your `DATABASE_URL` environment variable is set to `"${{Postgres.DATABASE_URL}}"` which is Railway's variable reference syntax. Railway should resolve this automatically, but if it's not working, we need to fix it.

## Solution

### Option 1: Remove Manual DATABASE_URL (Recommended)
**Railway automatically provides `DATABASE_URL` when you add a PostgreSQL service.** You shouldn't need to set it manually.

1. Go to Railway Dashboard → Your "web" service
2. Click "Variables" tab
3. **Find `DATABASE_URL` in the list**
4. **Delete it** (click the trash icon)
5. Railway will automatically provide `DATABASE_URL` from the Postgres service
6. Redeploy your service

### Option 2: Use Actual Connection String
If you need to set it manually (not recommended):

1. Go to Railway Dashboard → Your "Postgres" service
2. Click "Variables" tab
3. Find `DATABASE_URL` - it should look like:
   ```
   postgresql://postgres:password@containers-us-west-xxx.railway.app:5432/railway
   ```
4. Copy the **actual connection string** (not `${{Postgres.DATABASE_URL}}`)
5. Go to your "web" service → "Variables" tab
6. Set `DATABASE_URL` to the **actual connection string** you copied
7. Redeploy

### Option 3: Use Railway's Service Reference (If Option 1 doesn't work)
If Railway isn't auto-providing the variable:

1. Go to Railway Dashboard → Your "web" service
2. Click "Variables" tab
3. Click "New Variable"
4. Name: `DATABASE_URL`
5. Value: Click the "Reference" button and select `Postgres` → `DATABASE_URL`
6. This should create a proper reference that Railway resolves
7. Redeploy

## Verification

After fixing, check your logs for:
```
✅ Using DATABASE_URL (Railway detected: RAILWAY_ENV=..., PORT=...)
✅ Database connection pool created
```

Instead of:
```
❌ DATABASE_URL appears to be unresolved Railway variable reference
Using individual PostgreSQL params: localhost:5432/trading_bot
```

## Why This Happens

Railway's variable reference syntax `${{Service.Variable}}` is used in Railway's configuration files (like `railway.json` or service settings), but when you manually set an environment variable in the Variables tab, you need to use the **actual value**, not the reference syntax.

Railway automatically provides `DATABASE_URL` to all services when you add a PostgreSQL service, so you typically don't need to set it manually at all.

