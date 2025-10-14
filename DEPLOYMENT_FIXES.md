# 🚀 Deployment Fixes and Next Steps (Production-Ready)

This guide updates the previous deployment instructions to reflect the latest production-ready changes across `trading_bot.py`, `webhook_server.py`, `requirements.txt`, and `railway.json`.

## 🎯 What Changed (Production Enhancements)

- Environment variable compatibility: supports both `PROJECT_X_*` and `TOPSETPX_*`.
- Health endpoints added in `webhook_server.py`: `/health` and `/status`.
- Configurable logging with `LOG_LEVEL` and file logging to `trading_bot.log`.
- Railway health check now targets `/health` with tuned timeout/interval.
- Requirements include `python-dotenv`, `urllib3`, `certifi` for production stability.

---

## 🚨 Before You Start

1) Stop any running instances
```bash
pkill -f "start_webhook.py" || true
pkill -f "webhook_server.py" || true
```

2) Ensure account is flat
- Close open positions and cancel open orders
- Verify in TopStepX account UI

3) Backup current code (optional)
```bash
cp -r /Users/knealy/tradeBotServer /Users/knealy/tradeBotServer_backup_$(date +%Y%m%d)
```

---

## 🔧 Environment Configuration

Use either naming convention. Both work now.

```bash
# Primary (kept for backward-compatibility)
export PROJECT_X_API_KEY=your_api_key
export PROJECT_X_USERNAME=your_username

# Alternative (used in deployment guides)
export TOPSETPX_API_KEY=your_api_key
export TOPSETPX_USERNAME=your_username
export TOPSETPX_PASSWORD=your_password
export TOPSETPX_ACCOUNT_ID=11481693

# Trading controls
export POSITION_SIZE=3
export MAX_POSITION_SIZE=6
export IGNORE_NON_ENTRY_SIGNALS=true
export IGNORE_TP1_SIGNALS=true
export DEBOUNCE_SECONDS=300

# Logging
export LOG_LEVEL=INFO
```

See `PRODUCTION_ENVIRONMENT.md` for the full list and explanations.

Quick validation:
```bash
python3 -c "import trading_bot; print('import-ok')"
```

---

## 🚀 Deployment Options

### Option A: Railway.app (Recommended)

1) Variables (Railway → Variables)
```
POSITION_SIZE=3
MAX_POSITION_SIZE=6
IGNORE_NON_ENTRY_SIGNALS=true
IGNORE_TP1_SIGNALS=true
DEBOUNCE_SECONDS=300
TOPSETPX_USERNAME=your_username
TOPSETPX_PASSWORD=your_password
TOPSETPX_ACCOUNT_ID=11481693
LOG_LEVEL=INFO
```

2) Deploy
```bash
git add .
git commit -m "Production-ready updates: health checks, logging, env compat"
git push origin main
```

3) Health check configuration
- `railway.json` now uses `"healthcheckPath": "/health"`
- `healthcheckTimeout`: 30, `healthcheckInterval`: 60

4) Verify
```bash
# Replace with your Railway URL
curl -s https://your-app.railway.app/health | jq .
curl -s https://your-app.railway.app/status | jq .
```

### Option B: Local Development

```bash
pip install -r requirements.txt
python3 load_env.py
python3 start_webhook.py --position-size 3
```

---

## 🧪 Testing the Fixes (incl. Health)

1) Health endpoints
```bash
curl -s http://localhost:8080/health | jq .
curl -s http://localhost:8080/status | jq .
```

2) Position size + signal filtering
```bash
curl -X POST http://localhost:8080 \
  -H "Content-Type: application/json" \
  -d '{"embeds": [{"title": "🚀 [MNQ1!] open long", "fields": [{"name": "Entry", "value": "25143.00"}]}]}'

# Send a TP1-type signal, should be ignored
curl -X POST http://localhost:8080 \
  -H "Content-Type: application/json" \
  -d '{"embeds": [{"title": "✂️📈 [MNQ1!] trim/close long", "fields": [{"name": "Entry", "value": "25143.00"}]}]}'
```

---

## 📊 Monitoring & Logs

- `trading_bot.log`: main application logs
- `webhook_server.log`: webhook server logs
- Configure via `LOG_LEVEL` (e.g., DEBUG, INFO, WARNING)

Tail logs locally:
```bash
tail -f trading_bot.log
tail -f webhook_server.log
```

Railway logs:
```bash
railway logs --follow | grep -E "ERROR|WARNING|INFO"
```

---

## 🔍 Verification Checklist

Before deployment
- [ ] Bot stopped; account flat
- [ ] Environment variables set (see above)
- [ ] `.env` loaded if used; imports pass

After deployment
- [ ] `/health` returns HTTP 200 and `authenticated: true`
- [ ] `/status` returns HTTP 200 with service info
- [ ] Position limits enforced; no oversized positions
- [ ] Non-entry signals ignored; debounce active

During trading
- [ ] Entry signals create single protected positions
- [ ] Orders have SL/TP (OCO) when enabled in TopStepX

---

## 🛠️ Troubleshooting

Issue: `/health` returns 503 or `authenticated: false`
- Verify credentials (`PROJECT_X_*` or `TOPSETPX_*`)
- Check Railway Variables; restart service

Issue: No logs appear
- Ensure `LOG_LEVEL` is set; check file permissions

Issue: Webhook 400 errors
- Validate JSON payload formatting
- Check `IGNORE_NON_ENTRY_SIGNALS`/`IGNORE_TP1_SIGNALS`

Issue: OCO/Brackets not working
- Enable Auto OCO Brackets in TopStepX account settings

---

## 📁 Files Updated in this Release

- `trading_bot.py`: env var compatibility; logging improvements
- `webhook_server.py`: `/health` and `/status` endpoints
- `requirements.txt`: add `python-dotenv`, `urllib3`, `certifi`
- `railway.json`: health check path/timeouts
- `PRODUCTION_ENVIRONMENT.md`: production variables and guidance

---

## 📈 Expected Outcomes

- Stable deployments with health monitoring
- Clear, configurable logging for diagnostics
- Accurate environment configuration across local and Railway
- Safer trading flow: entry-only processing, debounce, position caps

---

## 🚀 Next Steps

1) Set variables and deploy (Railway recommended)
2) Validate `/health` and `/status`
3) Paper-trade to verify behavior
4) Monitor logs; adjust `LOG_LEVEL` as needed
5) Scale cautiously once stable

For a comprehensive production reference, see `PRODUCTION_ENVIRONMENT.md`.

---

# Previous guide (reference)

## 🎯 **Critical Fixes Applied**

This guide covers deploying the **FIXED** trading bot system that prevents oversized orphaned positions.

---

## 🚨 **Before You Start**

### **1. Stop Current Bot**
```bash
# Stop any running webhook servers
pkill -f "start_webhook.py"
pkill -f "webhook_server.py"
```

### **2. Close Existing Positions**
- **Manually close all open positions** in your TopStep account
- **Cancel all open orders** to start fresh
- **Verify account is flat** before deploying fixes

### **3. Backup Current Code**
```bash
# Create backup of current system
cp -r /Users/susan/projectXbot /Users/susan/projectXbot_backup_$(date +%Y%m%d)
```

---

## 🔧 **Environment Configuration**

### **Create Fixed Environment File**
```bash
# Create .env file with fixed settings
cat > .env << 'EOF'
# Trading Configuration
POSITION_SIZE=3
MAX_POSITION_SIZE=6
CLOSE_ENTIRE_POSITION_AT_TP1=false
TP1_FRACTION=0.75

# Signal Filtering (CRITICAL)
IGNORE_NON_ENTRY_SIGNALS=true
IGNORE_TP1_SIGNALS=true

# Debounce Settings (CRITICAL)
DEBOUNCE_SECONDS=300

# TopStepX Credentials
TOPSETPX_USERNAME=your_username
TOPSETPX_PASSWORD=your_password
TOPSETPX_ACCOUNT_ID=11481693
EOF
```

### **Test Configuration**
```bash
# Test the fixed system
python3 test_fixed_system.py
```

---

## 🚀 **Deployment Options**

### **Option 1: Railway.app (Recommended)**

#### **Step 1: Update Railway Environment Variables**
1. **Go to Railway dashboard**
2. **Select your project**
3. **Go to Variables tab**
4. **Add/Update these variables**:
   ```
   POSITION_SIZE=3
   MAX_POSITION_SIZE=6
   IGNORE_NON_ENTRY_SIGNALS=true
   IGNORE_TP1_SIGNALS=true
   DEBOUNCE_SECONDS=300
   TOPSETPX_USERNAME=your_username
   TOPSETPX_PASSWORD=your_password
   TOPSETPX_ACCOUNT_ID=11481693
   ```

#### **Step 2: Deploy**
```bash
# Push changes to GitHub
git add .
git commit -m "Fixed trading bot - prevents oversized positions"
git push origin main

# Railway will auto-deploy with new settings
```

#### **Step 3: Verify Deployment**
```bash
# Check Railway logs for successful deployment
# Look for: "Webhook server is running"
# Look for: "Position size: 3, Max position size: 6"
```

### **Option 2: Local Development**

#### **Step 1: Install Dependencies**
```bash
pip install -r requirements.txt
```

#### **Step 2: Load Environment**
```bash
# Load environment variables
python3 load_env.py
```

#### **Step 3: Test System**
```bash
# Test the fixed system
python3 test_fixed_system.py
```

#### **Step 4: Start Webhook Server**
```bash
# Start with fixed settings
python3 start_webhook.py --position-size 3
```

---

## 🧪 **Testing the Fixes**

### **Test 1: Position Size Validation**
```bash
# Send multiple rapid open_long signals
curl -X POST https://your-webhook-url \
  -H "Content-Type: application/json" \
  -d '{"embeds": [{"title": "🚀 [MNQ1!] open long", "fields": [{"name": "Entry", "value": "25143.00"}]}]}'

# Expected: Only first signal processes, others ignored
```

### **Test 2: Signal Filtering**
```bash
# Send TP1 signal (should be ignored)
curl -X POST https://your-webhook-url \
  -H "Content-Type: application/json" \
  -d '{"embeds": [{"title": "✂️📈 [MNQ1!] trim/close long", "fields": [{"name": "Entry", "value": "25143.00"}]}]}'

# Expected: Signal ignored (IGNORE_TP1_SIGNALS=true)
```

### **Test 3: Staged Exits**
```bash
# Send open_long signal
# Expected: Single position with TP1 (75%) and TP2 (25%) orders
# Check logs for: "Created single position with staged exits"
```

---

## 📊 **Monitoring the Fixed System**

### **Key Log Messages to Watch For**

#### **✅ Success Messages**
```
✅ "Created single position with staged exits: 3 contracts"
✅ "Position size limit reached for MNQ. Ignoring new entry signal."
✅ "Debounced duplicate open_long for MNQ; received too soon"
✅ "Ignoring non-entry signal: tp1_hit_long (IGNORE_NON_ENTRY_SIGNALS=true)"
```

#### **❌ Warning Messages**
```
⚠️ "Found existing MNQ positions: 6 contracts"
⚠️ "Maximum position size limit reached for MNQ"
⚠️ "Adding 3 contracts would exceed max position size"
```

#### **🚨 Error Messages**
```
❌ "TP1 bracket failed: [error details]"
❌ "TP2 bracket failed: [error details]"
❌ "Position size limit reached for [symbol]"
```

---

## 🔍 **Verification Checklist**

### **Before Deployment**
- [ ] Current bot stopped
- [ ] All positions closed manually
- [ ] Environment variables configured
- [ ] Test script passes all tests

### **After Deployment**
- [ ] Webhook server starts successfully
- [ ] Environment variables loaded correctly
- [ ] Position size limits enforced
- [ ] Signal filtering working
- [ ] Debounce protection active

### **During Trading**
- [ ] Only entry signals processed
- [ ] No oversized positions created
- [ ] All positions have stop/take profit protection
- [ ] Duplicate signals properly debounced

---

## 🛠️ **Troubleshooting**

### **Issue: Bot Still Creating Multiple Positions**
**Solution**: Check that `IGNORE_NON_ENTRY_SIGNALS=true` is set

### **Issue: Positions Not Protected**
**Solution**: Verify `IGNORE_TP1_SIGNALS=true` and OCO brackets are working

### **Issue: Rapid Duplicate Signals**
**Solution**: Increase `DEBOUNCE_SECONDS` to 600 (10 minutes)

### **Issue: Position Size Exceeded**
**Solution**: Check `MAX_POSITION_SIZE` setting and existing positions

---

## 📈 **Expected Results**

After implementing these fixes:

### **✅ What You'll See**
- **Single positions**: One position per signal instead of multiple
- **Proper protection**: All positions have stop loss and take profit
- **Size limits**: Maximum 6 contracts per symbol (configurable)
- **Signal filtering**: Only entry signals processed
- **Debounce protection**: Duplicate signals ignored

### **❌ What You Won't See**
- **Oversized positions**: No more -6 contract issues
- **Orphaned positions**: All positions have protection
- **OCO cancellations**: Proper bracket order management
- **Unwanted trades**: TP1/TP2 signals ignored

---

## 🎯 **Success Metrics**

### **Position Management**
- ✅ Maximum position size: 6 contracts per symbol
- ✅ Single entry per signal
- ✅ All positions protected with stops/takes

### **Signal Processing**
- ✅ Only entry signals processed
- ✅ TP1/TP2 signals ignored
- ✅ Duplicate signals debounced

### **Risk Management**
- ✅ No oversized positions
- ✅ No orphaned positions
- ✅ Proper staged exits (75% TP1, 25% TP2)

---

## 🚀 **Next Steps**

1. **Deploy the fixes** using the guide above
2. **Test thoroughly** with paper trading
3. **Monitor logs** for proper behavior
4. **Gradually increase position size** once stable
5. **Add more symbols** as needed

The fixed system will now create **single positions with proper risk management** instead of the multiple separate positions that caused your issues.
