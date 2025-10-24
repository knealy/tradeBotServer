# Emergency Rollback Instructions

## If new changes break trading bot on Railway:

### Option A: Quick Railway Rollback (Fastest - 2 minutes)

1. Go to Railway dashboard → Your project
2. Click on "Deployments" tab
3. Find the last working deployment (before dashboard changes)
4. Click three dots → "Redeploy"
5. Verify webhook endpoint works: `curl https://your-domain.railway.app/health`

### Option B: Git Branch Rollback (Medium - 5 minutes)

```bash
# Switch to backup branch
git checkout backup-before-dashboard-YYYYMMDD

# Force push to main (triggers Railway redeploy)
git push origin backup-before-dashboard-YYYYMMDD:main --force

# Wait for Railway to redeploy (1-2 minutes)
# Verify: curl https://your-domain.railway.app/health
```

### Option C: Full Local Restore (Slowest - 10 minutes)

```bash
# Navigate to parent directory
cd /Users/knealy/

# Backup current state (just in case)
mv tradeBotServer tradeBotServer-broken

# Extract backup
tar -xzf ~/tradeBotServer-backups/tradeBotServer-backup-YYYYMMDD-HHMMSS.tar.gz

# Restore to original location
mv tradeBotServer tradeBotServer-restored

# Push to Git and trigger Railway deploy
cd tradeBotServer-restored
git push origin main --force
```

### Option D: Railway File-Specific Rollback (Surgical - 3 minutes)

```bash
# If only specific files are problematic, restore just those files:
git checkout backup-before-dashboard-YYYYMMDD -- webhook_server.py trading_bot.py
git commit -m "Rollback: Restore core trading files"
git push origin main

# Or restore Railway deployment config only:
git checkout backup-before-dashboard-YYYYMMDD -- Procfile railway.json
git commit -m "Rollback: Restore deployment config"
git push origin main
```

## Pre-Deployment Checklist

Before deploying to Railway, verify locally:

1. **Webhook Server Still Works**
   ```bash
   # Start webhook server
   python3 start_webhook.py
   
   # Test webhook endpoint (in another terminal)
   curl -X POST http://localhost:8080/webhook \
     -H "Content-Type: application/json" \
     -d '{"action":"test","symbol":"MNQ","price":16000}'
   
   # Should return 200 OK
   ```

2. **Trading Bot Functions Correctly**
   ```bash
   # Run test script
   python3 test_webhook.py
   
   # Check logs for errors
   tail -f webhook_server.log
   ```

3. **Environment Variables Load Correctly**
   ```bash
   # Verify env vars are read (not overridden by hardcoded values)
   python3 -c "from load_env import *; import os; print('POSITION_SIZE:', os.getenv('POSITION_SIZE', '1'))"
   ```

4. **New FastAPI Backend Doesn't Interfere**
   ```bash
   # Start both servers simultaneously
   python3 start_webhook.py &  # Port 8080
   cd backend && uvicorn main:app --port 8001 &  # Port 8001
   
   # Test both are running
   curl http://localhost:8080/health
   curl http://localhost:8001/api/health
   
   # Kill test servers
   pkill -f "start_webhook.py"
   pkill -f "uvicorn main:app"
   ```

## Monitoring Checklist (First Week After Deployment)

- [ ] Webhook response times < 5 seconds
- [ ] All TradingView signals received (check logs)
- [ ] Orders placing successfully (verify in TopStepX)
- [ ] No "ORDER VERIFICATION FAILED" errors
- [ ] Discord notifications accurate
- [ ] FastAPI backend responding (if deployed)
- [ ] Database connections stable (if deployed)

## Gradual Deployment Strategy

**Deploy in stages to minimize risk:**

**Stage 1: Backend Only (No Frontend)**
- Deploy FastAPI backend on separate Railway service
- Test all endpoints work
- Keep webhook server untouched
- Rollback if issues

**Stage 2: Add Database**
- Add PostgreSQL + TimescaleDB to Railway
- Connect backend to database
- Verify webhook server unaffected
- Rollback if issues

**Stage 3: Add Frontend**
- Deploy React frontend
- Connect to backend API
- Webhook server still independent
- Rollback if issues

**Stage 4: Integration**
- Connect all systems together
- Monitor trading bot performance for 24 hours
- Keep backup deployment ready
