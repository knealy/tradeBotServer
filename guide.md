# 🚀 TradingView Webhook Deployment Guide

## 🎯 **Goal**: Permanent Webhook URL for TradingView Alerts

Deploy your webhook server to get a **permanent HTTPS URL** that never changes, eliminating daily restarts and manual intervention.

---

## 🏆 **Top 3 Solutions (Ranked by Ease + Cost)**

### **🥇 Option 1: Railway.app (RECOMMENDED)**
**Cost**: FREE for 30 days, then $5/month  
**Setup Time**: 5 minutes  
**Reliability**: 99.9% uptime  
**Effort**: Minimal  

#### **Why Railway?**
- ✅ **FREE for 30 days** (then $5/month)
- ✅ **Permanent URL** (never changes)
- ✅ **Zero maintenance** required
- ✅ **Auto-deploys** from GitHub
- ✅ **HTTPS by default**
- ✅ **No server management**
- ✅ **Standalone deployment** (no terminal needed)

#### **Setup Steps:**
1. **Push to GitHub** (if not already done)
2. **Go to [railway.app](https://railway.app)**
3. **Connect GitHub** → Select your repo
4. **Deploy** → One-click deployment
5. **Get URL** → `https://your-app.railway.app`

#### **Railway Configuration:**
```bash
# Add to your project root: railway.json
{
  "build": {
    "builder": "NIXPACKS"
  },
  "deploy": {
    "startCommand": "python3 start_webhook.py --position-size 3",
    "healthcheckPath": "/",
    "healthcheckTimeout": 100,
    "restartPolicyType": "ON_FAILURE"
  }
}
```

---

### **🥈 Option 2: Render.com (FREE)**
**Cost**: FREE tier available  
**Setup Time**: 10 minutes  
**Reliability**: 99.5% uptime  
**Effort**: Low  

#### **Why Render?**
- ✅ **FREE tier** (sleeps after 15min inactivity)
- ✅ **Permanent URL**
- ✅ **Easy GitHub integration**
- ✅ **HTTPS included**

#### **Setup Steps:**
1. **Go to [render.com](https://render.com)**
2. **New Web Service** → Connect GitHub
3. **Configure**:
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `python3 start_webhook.py --position-size 3`
4. **Deploy** → Get permanent URL

---

### **🥉 Option 3: Ngrok Static Domain ($8/month)**
**Cost**: $8/month  
**Setup Time**: 2 minutes  
**Reliability**: 99.9% uptime  
**Effort**: Minimal  

#### **Why Ngrok?**
- ✅ **No code changes** required
- ✅ **Static domain** (never changes)
- ✅ **Immediate setup**
- ✅ **Proven reliability**

#### **Setup Steps:**
1. **Upgrade to Ngrok Basic** ($8/month)
2. **Get static domain**: `https://your-tradingview.ngrok.io`
3. **Update TradingView** with permanent URL
4. **Done!** No more daily restarts

---

## 🔧 **Advanced Options (For Power Users)**

### **Option 4: Cloudflare Tunnel (FREE)**
**Cost**: FREE  
**Setup Time**: 15 minutes  
**Reliability**: 99.99% uptime  
**Effort**: Medium  

#### **Setup Steps:**
```bash
# 1. Install cloudflared
brew install cloudflared

# 2. Login to Cloudflare
cloudflared tunnel login

# 3. Create tunnel
cloudflared tunnel create tradingview-webhook

# 4. Configure DNS
cloudflared tunnel route dns tradingview-webhook your-domain.com

# 5. Start tunnel (permanent)
cloudflared tunnel run tradingview-webhook
```

### **Option 5: Heroku (Paid)**
**Cost**: $7/month (Basic Plan)  
**Setup Time**: 10 minutes  
**Reliability**: 99.9% uptime  
**Effort**: Low  

#### **Setup Steps:**
1. **Install Heroku CLI**
2. **Create Heroku app**
3. **Deploy from GitHub**
4. **Get permanent URL**

---

## 🎯 **My Recommendation: Railway.app**

### **Why Railway Wins:**
- 🆓 **FREE** (no monthly costs)
- ⚡ **5-minute setup**
- 🔒 **HTTPS by default**
- 🚀 **Auto-scaling**
- 🛠️ **Zero maintenance**
- 📱 **Mobile-friendly dashboard**

### **Railway Deployment Process:**

#### **Step 1: Prepare Your Code**
```bash
# Ensure your project is ready
cd /Users/susan/projectXbot
git add .
git commit -m "Ready for Railway deployment"
git push origin main
```

#### **Step 2: Deploy to Railway**
1. **Visit [railway.app](https://railway.app)**
2. **Sign up** with GitHub
3. **New Project** → Deploy from GitHub
4. **Select your repo** → Deploy
5. **Get your URL**: `https://your-app.railway.app`

#### **Step 3: Configure Environment Variables**
In Railway dashboard, add these environment variables:
```
TOPSETPX_USERNAME=your_username
TOPSETPX_PASSWORD=your_password
TOPSETPX_ACCOUNT_ID=11481693
```

#### **Step 4: Update TradingView**
- **Webhook URL**: `https://your-app.railway.app`
- **JSON Format**: Same as before (no changes needed)

---

## 🔄 **Discord Integration Bonus**

Since you already have Discord alerts working, here are **hybrid approaches**:

### **Option A: Discord Bot + Webhook**
```python
# Create a Discord bot that forwards alerts to your webhook
# Discord → Your Bot → TopStepX API
```

### **Option B: Discord Webhook + Server**
```python
# Use Discord webhooks to trigger your trading bot
# Discord → Your Server → TopStepX API
```

---

## 📊 **Comparison Table**

| Solution | Cost | Setup Time | Reliability | Effort | URL Type |
|----------|------|------------|-------------|--------|----------|
| **Railway.app** | FREE | 5 min | 99.9% | Minimal | Permanent |
| **Render.com** | FREE | 10 min | 99.5% | Low | Permanent |
| **Ngrok Static** | $8/mo | 2 min | 99.9% | Minimal | Permanent |
| **Cloudflare** | FREE | 15 min | 99.99% | Medium | Permanent |
| **Heroku** | $7/mo | 10 min | 99.9% | Low | Permanent |

---

## 🚀 **Quick Start (Railway)**

### **1. Deploy Now:**
```bash
# Push to GitHub (if not already done)
git add .
git commit -m "Ready for Railway deployment"
git push origin main

# Go to railway.app and deploy
```

### **2. Get Your Permanent URL:**
```
https://your-app.railway.app
```

### **3. Update TradingView:**
- **Webhook URL**: `https://your-app.railway.app`
- **JSON**: Same format as before

### **4. Test:**
```bash
curl -X POST https://your-app.railway.app \
  -H "Content-Type: application/json" \
  -d '{"embeds": [{"title": "Open Long [MNQ1!]", "fields": [{"name": "Entry", "value": "25143.00"}]}]}'
```

---

## 🎯 **Final Recommendation**

**Go with Railway.app** - it's FREE, takes 5 minutes, and gives you a permanent URL that never changes. No more daily restarts, no more manual intervention, just set it and forget it!

Your TradingView alerts will work 24/7 without any maintenance. 🚀

---

## 🔧 **Future Modifications & Management**

### **🎯 Yes, It's Completely Standalone!**

Once deployed to Railway, your webhook server runs **independently** - no terminal, no local server, no daily restarts needed. It's a **cloud service** that runs 24/7.

### **📝 How to Make Changes in the Future**

#### **1. Change Position Size**
```bash
# In Railway dashboard, update environment variable:
POSITION_SIZE=5  # Change from 3 to 5 contracts
```

#### **2. Switch Trading Accounts**
```bash
# In Railway dashboard, update environment variable:
TOPSETPX_ACCOUNT_ID=11481694  # Change to different account
```

#### **3. Add New Trading Symbols**
```python
# Edit trading_bot.py - add new symbols to _get_contract_id()
def _get_contract_id(self, symbol):
    symbol_map = {
        'MNQ': 'CON.F.US.MNQ.Z25',
        'ES': 'CON.F.US.ES.Z25',      # Add new symbol
        'NQ': 'CON.F.US.NQ.Z25',      # Add new symbol
        # Add more symbols here
    }
```

#### **4. Modify Trading Logic**
```python
# Edit webhook_server.py - customize signal processing
def _execute_open_long(self, trade_info):
    # Add your custom logic here
    # Example: Different position sizes for different symbols
    if trade_info['symbol'] == 'ES':
        position_size = 2  # Smaller size for ES
    else:
        position_size = 3  # Default size
```

#### **5. Add New Signal Types**
```python
# Edit webhook_server.py - add new signal types
def _parse_signal_type(self, title):
    if 'scalp_long' in title.lower():
        return 'scalp_long'
    elif 'swing_short' in title.lower():
        return 'swing_short'
    # Add more signal types
```

### **🚀 Deployment Process for Changes**

#### **Method 1: Railway Dashboard (Easiest)**
1. **Go to Railway dashboard**
2. **Click on your project**
3. **Go to Variables tab**
4. **Update environment variables**
5. **Redeploy** (automatic)

#### **Method 2: GitHub Integration (Recommended)**
1. **Edit code locally**
2. **Commit changes**: `git commit -m "Updated position size"`
3. **Push to GitHub**: `git push origin main`
4. **Railway auto-deploys** (within 2-3 minutes)

### **📊 Environment Variables You Can Modify**

| Variable | Purpose | Example |
|----------|---------|---------|
| `POSITION_SIZE` | Contract size per trade | `3` |
| `TOPSETPX_ACCOUNT_ID` | Trading account ID | `11481693` |
| `CLOSE_ENTIRE_POSITION_AT_TP1` | TP1 behavior | `False` |
| `TP1_FRACTION` | Fraction allocated to TP1 when staged exits are used (0-1) | `0.75` |
| `IGNORE_TP1_SIGNALS` | If `true`, TP1 webhook signals are ignored (OCO-managed exits) | `true` |
| `TOPSETPX_USERNAME` | TopStepX login | `your_username` |
| `TOPSETPX_PASSWORD` | TopStepX password | `your_password` |

### **🔧 Advanced Customizations**

#### **Add Risk Management**
```python
# In webhook_server.py
def _calculate_position_size(self, symbol, account_balance):
    # Dynamic position sizing based on account balance
    if account_balance > 50000:
        return 5
    elif account_balance > 25000:
        return 3
    else:
        return 1
```

#### **Add Time-Based Trading**
```python
# In webhook_server.py
def _is_trading_hours(self):
    import datetime
    now = datetime.datetime.now()
    # Only trade during market hours
    return 9 <= now.hour <= 16
```

#### **Add Multiple Account Support**
```python
# In webhook_server.py
def _get_account_for_symbol(self, symbol):
    # Route different symbols to different accounts
    if symbol == 'MNQ':
        return '11481693'  # Account 1
    elif symbol == 'ES':
        return '11481694'  # Account 2
```

### **📱 Monitoring & Logs**

#### **Railway Dashboard**
- **Real-time logs**: See all trading activity
- **Performance metrics**: CPU, memory usage
- **Deployment history**: Track all changes

#### **TradingView Integration**
- **Webhook URL**: Never changes (permanent)
- **JSON format**: Same as before
- **Alert testing**: Use Railway URL for tests

### **🔄 Update Process**

#### **For Code Changes:**
1. **Edit files locally**
2. **Test locally** (optional)
3. **Commit & push** to GitHub
4. **Railway auto-deploys** (2-3 minutes)
5. **Monitor logs** in Railway dashboard

#### **For Configuration Changes:**
1. **Go to Railway dashboard**
2. **Update environment variables**
3. **Redeploy** (instant)
4. **Test with TradingView**

### **💡 Pro Tips**

#### **Testing Changes:**
```bash
# Test locally before deploying
python3 start_webhook.py --position-size 5

# Test with curl
curl -X POST https://your-app.railway.app \
  -H "Content-Type: application/json" \
  -d '{"embeds": [{"title": "Open Long [MNQ1!]", "fields": [{"name": "Entry", "value": "25143.00"}]}]}'
```

#### **Backup Strategy:**
- **GitHub**: All code changes tracked
- **Railway**: Automatic backups
- **Environment**: Variables saved in Railway

#### **Rollback Plan:**
- **Railway**: One-click rollback to previous version
- **GitHub**: Revert commits if needed

---

## 📞 **Need Help?**

If you run into any issues:
1. **Railway Docs**: [docs.railway.app](https://docs.railway.app)
2. **Render Docs**: [render.com/docs](https://render.com/docs)
3. **Ngrok Docs**: [ngrok.com/docs](https://ngrok.com/docs)

**Happy Trading!** 🎯📈
