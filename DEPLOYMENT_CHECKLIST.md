# 🚀 Deployment Checklist

## Critical Issues Found in Logs

### 1. Database Connection Failing ❌
**Problem**: Database trying to connect to `localhost:5432` instead of Railway's `DATABASE_URL`

**Fix Applied**:
- Updated `infrastructure/database.py` to detect Railway by checking for `PORT` environment variable (Railway always sets this)
- Now checks: `RAILWAY_ENVIRONMENT`, `RAILWAY`, or `PORT` to detect Railway

**Action Required**:
1. **Verify Railway PostgreSQL is added to your project**
   - Go to Railway dashboard → Your project → Click "+ New" → "Database" → "Add PostgreSQL"
   - Railway will automatically set `DATABASE_URL` environment variable

2. **Check environment variables in Railway**:
   - Go to your Railway service → "Variables" tab
   - Verify `DATABASE_URL` is set (Railway sets this automatically when you add PostgreSQL)
   - If missing, add PostgreSQL database service

### 2. Code Changes Not Deployed ❌
**Problem**: New log messages aren't appearing, suggesting code changes aren't deployed

**Action Required**:
1. **Commit and push all changes**:
   ```bash
   git add .
   git commit -m "Add strategy persistence, real-time chart updates, and verification endpoint"
   git push origin main
   ```

2. **Trigger Railway redeploy**:
   - Railway should auto-deploy on push
   - Or manually trigger: Railway dashboard → Your service → "Deploy" → "Redeploy"

3. **Verify deployment**:
   - Check logs for new messages:
     - `📡 Ensured SignalR quote subscription for MNQ (triggered by chart load)`
     - `📈 Quote received for MNQ...`
     - `📊 Auto-subscribed MNQ to timeframes...`

### 3. Strategy Verification Endpoint ✅
**New Endpoint Added**: `/api/strategies/{name}/verify`

**Usage**:
```bash
curl https://tvwebhooks.up.railway.app/api/strategies/overnight_range/verify
```

**Returns**:
- `will_trade`: true/false
- `reasons`: Array of why it will/won't trade
- `next_execution`: Next market open time
- `hours_until_execution`: Hours until next trade execution

### 4. Frontend Changes Not Visible ❌
**Problem**: Strategy-specific parameters UI not showing

**Action Required**:
1. **Rebuild frontend**:
   ```bash
   cd frontend
   npm run build
   ```

2. **Verify build output**:
   - Check that `frontend/dist` or `static/dashboard` has new files
   - Railway should auto-build on deploy, but verify build script runs

3. **Clear browser cache**:
   - Hard refresh: Cmd+Shift+R (Mac) or Ctrl+Shift+R (Windows)
   - Or open in incognito/private window

## Verification Steps

### After Deployment:

1. **Check Database Connection**:
   ```bash
   # In Railway logs, look for:
   ✅ Database connection pool created
   # NOT:
   ❌ Failed to create database pool: connection to server at "localhost"
   ```

2. **Check Strategy Verification**:
   ```bash
   curl https://tvwebhooks.up.railway.app/api/strategies/overnight_range/verify
   ```
   Should return JSON with `will_trade: true` and `next_execution` time

3. **Check Real-Time Chart Updates**:
   - Open chart for MNQ
   - Check browser console for: `[TradingChart] Chart ready for MNQ...`
   - Check server logs for: `📡 Ensured SignalR quote subscription for MNQ`
   - Check for quote logs: `📈 Quote received for MNQ...`

4. **Check Strategy Persistence**:
   - Edit strategy config in UI
   - Save changes
   - Check logs for: `📝 Updating strategy config...`
   - Restart server
   - Strategy should auto-start with saved settings

## Quick Fixes

### If Database Still Failing:
1. Add PostgreSQL service in Railway dashboard
2. Railway automatically sets `DATABASE_URL`
3. Redeploy service

### If Code Not Deployed:
1. Check Railway build logs for errors
2. Verify `build.sh` runs successfully
3. Check that Python files are in the deployment

### If Frontend Not Updating:
1. Check Railway build logs for frontend build
2. Verify `static/dashboard` directory has new files
3. Hard refresh browser (Cmd+Shift+R)

## Expected Log Messages After Fix

```
✅ Database connection pool created
📊 Bar aggregator started - real-time chart updates enabled
💾 Loading persisted strategy states on server startup...
🚀 Auto-starting enabled strategies on server startup...
📡 Ensured SignalR quote subscription for MNQ (triggered by chart load)
📈 Quote received for MNQ: $15000.0 (vol: 100) → bar aggregator
📊 Auto-subscribed MNQ to timeframes: 1m, 5m, 15m, 1h
```

