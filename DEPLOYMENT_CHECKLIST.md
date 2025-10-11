# ✅ Deployment Checklist - FIXED Trading Bot

## 🚨 **Pre-Deployment Checklist**

### **1. Stop Current System**
- [ ] Stop any running webhook servers
- [ ] Close all open positions manually in TopStep account
- [ ] Cancel all open orders
- [ ] Verify account is flat

### **2. Environment Setup**
- [ ] Set `TOPSETPX_USERNAME`
- [ ] Set `TOPSETPX_PASSWORD`
- [ ] Set `TOPSETPX_ACCOUNT_ID`
- [ ] Set `POSITION_SIZE=3`
- [ ] Set `MAX_POSITION_SIZE=6`
- [ ] Set `IGNORE_NON_ENTRY_SIGNALS=true`
- [ ] Set `IGNORE_TP1_SIGNALS=true`
- [ ] Set `DEBOUNCE_SECONDS=300`

### **3. Code Validation**
- [ ] Run `python3 test_fixed_system.py` - All tests pass
- [ ] Run `python3 validate_deployment.py` - All validations pass
- [ ] Check for no linting errors
- [ ] Verify all files are committed to git

---

## 🚀 **Deployment Steps**

### **Option 1: Railway.app Deployment**

#### **Step 1: Prepare Repository**
```bash
# Clean up and commit
git add .
git commit -m "Fixed trading bot - prevents oversized positions"
git push origin main
```

#### **Step 2: Configure Railway**
1. Go to Railway dashboard
2. Select your project
3. Go to Variables tab
4. Add all environment variables
5. Deploy automatically

#### **Step 3: Verify Deployment**
- [ ] Check Railway logs for successful startup
- [ ] Look for "Webhook server is running"
- [ ] Verify environment variables loaded
- [ ] Get webhook URL from Railway dashboard

### **Option 2: Local Deployment**

#### **Step 1: Setup Environment**
```bash
# Create .env file with all variables
cp ENVIRONMENT_VARIABLES_FIXED.md .env
# Edit .env with your credentials
```

#### **Step 2: Test System**
```bash
# Test the fixed system
python3 test_fixed_system.py
python3 validate_deployment.py
```

#### **Step 3: Start Webhook Server**
```bash
# Start with fixed settings
python3 start_webhook.py --position-size 3
```

---

## 🧪 **Post-Deployment Testing**

### **Test 1: Signal Processing**
```bash
# Test entry signal
curl -X POST https://your-webhook-url \
  -H "Content-Type: application/json" \
  -d '{"embeds": [{"title": "🚀 [MNQ1!] open long", "fields": [{"name": "Entry", "value": "25143.00"}]}]}'

# Expected: Single position created with staged exits
```

### **Test 2: Signal Filtering**
```bash
# Test TP1 signal (should be ignored)
curl -X POST https://your-webhook-url \
  -H "Content-Type: application/json" \
  -d '{"embeds": [{"title": "✂️📈 [MNQ1!] trim/close long", "fields": [{"name": "Entry", "value": "25143.00"}]}]}'

# Expected: Signal ignored
```

### **Test 3: Debounce Protection**
```bash
# Send multiple rapid signals
# Expected: Only first signal processes, others debounced
```

---

## 📊 **Monitoring Checklist**

### **Log Messages to Watch For**
- [ ] "Created single position with staged exits"
- [ ] "Position size limit reached - ignoring signal"
- [ ] "Debounced duplicate signal"
- [ ] "Ignoring non-entry signal"

### **Position Management**
- [ ] Maximum 6 contracts per symbol
- [ ] All positions have stop loss and take profit
- [ ] No orphaned positions
- [ ] Proper staged exits (75% TP1, 25% TP2)

### **Signal Processing**
- [ ] Only entry signals processed
- [ ] TP1/TP2 signals ignored
- [ ] Duplicate signals debounced
- [ ] No unwanted trades

---

## 🚨 **Critical Success Factors**

### **✅ What Should Happen**
- Single position per signal (no more -6 contract issues)
- All positions protected with OCO brackets
- TP1/TP2 signals ignored (OCO manages exits)
- Duplicate signals debounced (5-minute window)
- Position size limits enforced (max 6 contracts)

### **❌ What Should NOT Happen**
- Multiple separate positions
- Oversized positions (-6 contracts)
- Orphaned positions without protection
- OCO order cancellations
- Unwanted TP1/TP2 trades

---

## 🛠️ **Troubleshooting**

### **Issue: Still Creating Multiple Positions**
- Check `IGNORE_NON_ENTRY_SIGNALS=true`
- Verify signal filtering is working
- Check logs for "ignored" messages

### **Issue: Positions Not Protected**
- Check `IGNORE_TP1_SIGNALS=true`
- Verify OCO brackets are working
- Check for bracket order errors

### **Issue: Rapid Duplicate Signals**
- Increase `DEBOUNCE_SECONDS` to 600
- Check existing positions before new entries
- Verify debounce logic is working

### **Issue: Position Size Exceeded**
- Check `MAX_POSITION_SIZE` setting
- Verify existing positions
- Check position validation logic

---

## 📈 **Success Metrics**

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

## 🎯 **Final Verification**

After deployment, verify:
1. **Webhook server starts successfully**
2. **Environment variables loaded correctly**
3. **Position size limits enforced**
4. **Signal filtering working**
5. **Debounce protection active**
6. **All positions have protection**
7. **No oversized positions created**

The fixed system will now create **single positions with proper risk management** instead of the multiple separate positions that caused your issues.

---

## 📞 **Support**

If you encounter issues:
1. Check logs for error messages
2. Verify environment variables are set correctly
3. Test with paper trading first
4. Monitor position sizes in your account
5. Review the troubleshooting section above

**Remember**: This fixed system prevents oversized positions and ensures proper risk management!
