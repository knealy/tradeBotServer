# 🚀 Deployment Guide for Fixed Trading Bot

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
