# 🚂 Railway Deployment Guide

Complete guide to deploy your trading bot with frontend to Railway.

---

## 📋 Prerequisites

1. **Railway Account** - Sign up at [railway.app](https://railway.app)
2. **Railway CLI** (optional but recommended)
   ```bash
   npm install -g @railway/cli
   railway login
   ```

3. **Environment Variables Ready** - Have your credentials ready:
   - `PROJECT_X_API_KEY` or `TOPSETPX_API_KEY`
   - `PROJECT_X_USERNAME` or `TOPSETPX_USERNAME`
   - `TOPSTEPX_ACCOUNT_ID` (optional - will auto-select if not provided)
   - Database URL (Railway provides this automatically)

---

## 🚀 Deployment Methods

### Method 1: Deploy via Railway CLI (Recommended)

1. **Login to Railway**
   ```bash
   railway login
   ```

2. **Link to Project** (or create new)
   ```bash
   cd /Users/susan/projectXbot
   railway link
   ```
   Or create new project:
   ```bash
   railway init
   ```

3. **Add PostgreSQL Database**
   ```bash
   railway add --database postgres
   ```

4. **Set Environment Variables**
   ```bash
   railway variables set PROJECT_X_API_KEY="your_api_key_here"
   railway variables set PROJECT_X_USERNAME="your_username_here"
   railway variables set TOPSTEPX_ACCOUNT_ID="your_account_id"
   
   # Optional: Set specific port (Railway provides PORT automatically)
   railway variables set WEBHOOK_PORT="8080"
   ```

5. **Deploy**
   ```bash
   railway up
   ```

6. **Generate Domain**
   ```bash
   railway domain
   ```
   This will give you a URL like: `https://projectxbot.up.railway.app`

7. **View Logs**
   ```bash
   railway logs
   ```

### Method 2: Deploy via GitHub (Automatic Deployments)

1. **Push to GitHub**
   ```bash
   git add .
   git commit -m "Deploy to Railway with frontend"
   git push origin main
   ```

2. **Connect to Railway**
   - Go to [railway.app](https://railway.app/new)
   - Click "Deploy from GitHub repo"
   - Select your repository
   - Railway will auto-detect the configuration

3. **Add PostgreSQL Database**
   - In your Railway project dashboard
   - Click "New" → "Database" → "Add PostgreSQL"
   - Railway automatically sets `DATABASE_URL`

4. **Set Environment Variables**
   - Go to your project
   - Click on your service
   - Go to "Variables" tab
   - Add:
     - `PROJECT_X_API_KEY`
     - `PROJECT_X_USERNAME`
     - `TOPSTEPX_ACCOUNT_ID`

5. **Generate Domain**
   - Go to "Settings" tab
   - Click "Generate Domain"
   - You'll get a URL like: `https://tvwebhooks.up.railway.app`

---

## 🌐 Accessing Your Application

After deployment, your app will be available at:

- **Root**: `https://tvwebhooks.up.railway.app/`
- **Dashboard**: `https://tvwebhooks.up.railway.app/dashboard`
- **Positions**: `https://tvwebhooks.up.railway.app/positions`
- **Strategies**: `https://tvwebhooks.up.railway.app/strategies`
- **Settings**: `https://tvwebhooks.up.railway.app/settings`

### API Endpoints
- **Webhook**: `https://tvwebhooks.up.railway.app/webhook`
- **Health Check**: `https://tvwebhooks.up.railway.app/health`
- **API Docs**: `https://tvwebhooks.up.railway.app/api/`

---

## 🔧 Configuration Files

The deployment uses these configuration files:

### `railway.json`
```json
{
  "build": {
    "builder": "NIXPACKS",
    "buildCommand": "bash build.sh"
  },
  "deploy": {
    "startCommand": "python3 servers/start_async_webhook.py",
    "restartPolicyType": "ON_FAILURE",
    "restartPolicyMaxRetries": 10,
    "healthcheckPath": "/health",
    "healthcheckTimeout": 30,
    "healthcheckInterval": 60
  }
}
```

### `build.sh`
Automatically:
1. Installs frontend dependencies
2. Builds React app
3. Places output in `static/dashboard/`

### Frontend Auto-Configuration
The frontend automatically detects it's running on Railway and:
- Uses relative API paths (same domain)
- Connects to WebSocket on same domain with wss://
- No manual configuration needed!

---

## 📊 Database Setup

Railway provides PostgreSQL automatically. The app will:

1. **Auto-detect** Railway's `DATABASE_URL` environment variable
2. **Create tables** automatically on first run
3. **Cache data** for faster performance

### Database Tables Created:
- `historical_bars` - Market data cache
- `account_state` - Account tracking
- `strategy_performance` - Strategy metrics
- `api_metrics` - Performance monitoring
- `trade_history` - Trade records
- `order_history_cache` - Order data cache

---

## 🔐 Environment Variables

### Required Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `PROJECT_X_API_KEY` | TopStepX API Key | `your-api-key` |
| `PROJECT_X_USERNAME` | TopStepX Username | `your-username` |

### Optional Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `TOPSTEPX_ACCOUNT_ID` | Specific account ID | Auto-selects first account |
| `WEBHOOK_HOST` | Server host | `0.0.0.0` |
| `WEBHOOK_PORT` | Server port | Railway provides `PORT` |
| `WEBSOCKET_PORT` | WebSocket port | `8081` (same as main port on Railway) |
| `LOG_LEVEL` | Logging level | `INFO` |

### Railway Provides Automatically

| Variable | Description |
|----------|-------------|
| `PORT` | Port to bind server to |
| `DATABASE_URL` | PostgreSQL connection string |
| `RAILWAY_ENVIRONMENT` | Current environment |

---

## 📝 TradingView Webhook Setup

1. **Get Your Railway URL**
   ```
   https://tvwebhooks.up.railway.app
   ```

2. **Configure TradingView Alert**
   - Webhook URL: `https://tvwebhooks.up.railway.app/webhook`
   - Message format (JSON):
   ```json
   {
     "action": "{{strategy.order.action}}",
     "contracts": "{{strategy.order.contracts}}",
     "ticker": "{{ticker}}",
     "price": "{{close}}",
     "strategy": "{{strategy.order.id}}"
   }
   ```

3. **Test Webhook**
   ```bash
   curl -X POST https://tvwebhooks.up.railway.app/webhook \
     -H "Content-Type: application/json" \
     -d '{
       "action": "BUY",
       "contracts": "1",
       "ticker": "MNQ",
       "price": "25300.00"
     }'
   ```

---

## 🐛 Troubleshooting

### Build Fails

**Issue**: Frontend build fails during deployment

**Solution**: Check Railway build logs
```bash
railway logs --deployment
```

Common causes:
- Missing `node_modules` - Railway will install automatically
- TypeScript errors - Fix locally first
- Memory issues - Upgrade Railway plan if needed

### Database Connection Issues

**Issue**: "connection already closed" errors

**Solution**: The app now auto-handles stale connections (fixed in recent update)

If issues persist:
- Check `DATABASE_URL` is set
- Verify database is running: `railway service`
- Check logs: `railway logs`

### Frontend Not Loading

**Issue**: Dashboard shows 404 or blank page

**Solution**:
1. Check if frontend built: `railway logs | grep "Frontend built"`
2. Verify static files exist: Check deployment artifacts
3. Try rebuilding: `railway up --force`

### Webhook Not Receiving Signals

**Issue**: TradingView alerts not executing trades

**Solution**:
1. Check webhook URL is correct
2. Test with curl (see above)
3. Check Railway logs: `railway logs --tail`
4. Verify environment variables are set

### WebSocket Connection Failed

**Issue**: Dashboard shows "Disconnected" status

**Solution**:
- Railway automatically proxies WebSocket on same port
- Ensure using `wss://` (secure WebSocket)
- Check browser console for errors
- Frontend auto-detects Railway and uses correct protocol

---

## 📈 Monitoring

### View Logs
```bash
# Real-time logs
railway logs --tail

# Last 100 lines
railway logs --tail 100

# Specific deployment
railway logs --deployment
```

### Health Check
```bash
# Check if server is running
curl https://tvwebhooks.up.railway.app/health
```

### Metrics Dashboard
Access at: `https://tvwebhooks.up.railway.app/dashboard`

Shows:
- Account balance
- Open positions
- Recent trades
- Strategy performance
- System metrics

---

## 🔄 Updates & Redeployment

### Deploy New Changes

**Via CLI:**
```bash
git add .
git commit -m "Update trading bot"
railway up
```

**Via GitHub:**
```bash
git push origin main
# Railway auto-deploys
```

### Rollback to Previous Version
```bash
railway rollback
```

### View Deployments
```bash
railway status
```

---

## 💰 Cost Estimates

### Railway Pricing (as of 2025)

**Hobby Plan** (Free):
- $5 free credit/month
- Suitable for testing
- Limited to 512MB RAM

**Developer Plan** ($20/month):
- $20 included usage
- Pay-as-you-go beyond that
- Recommended for production
- Up to 8GB RAM

**Typical Monthly Cost**:
- Small bot (1-2 symbols): ~$10-15/month
- Medium bot (3-5 symbols): ~$20-30/month
- Includes: Compute + PostgreSQL + Network

### Optimize Costs
- Use database caching (already configured)
- Limit historical data fetches
- Use appropriate timeframes
- Monitor usage in Railway dashboard

---

## 🎯 Next Steps After Deployment

1. **Test All Features**
   - Open dashboard
   - Check positions
   - Verify strategies
   - Test webhook

2. **Set Up Monitoring**
   - Add Discord webhook for notifications
   - Monitor Railway logs
   - Set up alerts for errors

3. **Configure Strategies**
   - Enable desired strategies via dashboard
   - Set risk parameters
   - Test with small position sizes first

4. **Security**
   - Keep API keys secure
   - Don't share Railway URL publicly
   - Use Railway's built-in authentication if needed

---

## 📞 Support

### Useful Links
- [Railway Documentation](https://docs.railway.app/)
- [Railway Status](https://status.railway.app/)
- [Railway Discord](https://discord.gg/railway)

### Check Logs
```bash
# Application logs
railway logs

# Database logs
railway logs --service postgres
```

### Common Commands
```bash
# Restart service
railway up --force

# View environment variables
railway variables

# Open Railway dashboard
railway open

# SSH into container
railway shell
```

---

## ✅ Deployment Checklist

- [ ] Railway account created
- [ ] Project initialized
- [ ] PostgreSQL database added
- [ ] Environment variables set
- [ ] Code pushed/deployed
- [ ] Domain generated
- [ ] Frontend accessible at `/dashboard`
- [ ] Health check returns 200 OK
- [ ] TradingView webhook configured
- [ ] Test trade executed successfully
- [ ] WebSocket connection working
- [ ] Logs show no errors

---

## 🎉 Success!

If everything is working:
- ✅ Dashboard loads at `https://tvwebhooks.up.railway.app/dashboard`
- ✅ Positions and orders display correctly
- ✅ WebSocket shows "Connected"
- ✅ Webhook receives TradingView signals
- ✅ Trades execute on TopStepX

Your trading bot is now fully deployed and accessible from anywhere! 🚀

---

**Last Updated**: November 10, 2025
**Version**: 1.0.0 (with frontend integration)

