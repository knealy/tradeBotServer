# 🔧 Fixed Environment Variables Configuration

## 🎯 **Critical Fixes Applied**

This document outlines the environment variables for the **FIXED** trading bot system that prevents oversized orphaned positions.

---

## 🚨 **Critical Settings (Must Configure)**

### **Position Management**
```bash
# Position size per trade (default: 3)
POSITION_SIZE=3

# Maximum position size allowed (default: 2x POSITION_SIZE)
MAX_POSITION_SIZE=6

# Close entire position at TP1 (default: false for staged exits)
CLOSE_ENTIRE_POSITION_AT_TP1=false

# TP1 fraction when using staged exits (default: 0.75 = 75%)
TP1_FRACTION=0.75
```

### **Signal Filtering (CRITICAL)**
```bash
# Ignore non-entry signals to prevent unwanted trades (default: true)
IGNORE_NON_ENTRY_SIGNALS=true

# Ignore TP1 signals when OCO manages exits (default: true)
IGNORE_TP1_SIGNALS=true
```

### **Debounce Settings (CRITICAL)**
```bash
# Debounce window in seconds (default: 300 = 5 minutes)
DEBOUNCE_SECONDS=300
```

---

## 🔧 **Complete Configuration Example**

### **For Railway.app Deployment**
```bash
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
```

### **For Local Development**
```bash
# Copy to .env file
POSITION_SIZE=3
MAX_POSITION_SIZE=6
CLOSE_ENTIRE_POSITION_AT_TP1=false
TP1_FRACTION=0.75
IGNORE_NON_ENTRY_SIGNALS=true
IGNORE_TP1_SIGNALS=true
DEBOUNCE_SECONDS=300
TOPSETPX_USERNAME=your_username
TOPSETPX_PASSWORD=your_password
TOPSETPX_ACCOUNT_ID=11481693
```

---

## 🛠️ **What These Fixes Do**

### **1. Position Size Validation**
- **MAX_POSITION_SIZE**: Prevents oversized positions by limiting total contracts per symbol
- **Enhanced validation**: Checks existing positions before creating new ones
- **Prevents**: The -6 contract issue you experienced

### **2. Signal Filtering**
- **IGNORE_NON_ENTRY_SIGNALS**: Only processes entry signals and critical exits
- **IGNORE_TP1_SIGNALS**: Lets OCO brackets manage exits automatically
- **Prevents**: Unwanted TP1/TP2 signals from creating additional trades

### **3. Enhanced Debounce**
- **DEBOUNCE_SECONDS**: 5-minute window prevents rapid duplicate signals
- **Position-based validation**: Checks existing positions before new entries
- **Prevents**: Multiple rapid entries that create oversized positions

### **4. Single Position Management**
- **Uses `create_partial_tp_bracket_order`**: Creates ONE position with staged exits
- **Proper TP1/TP2 logic**: 75% at TP1, 25% at TP2
- **Prevents**: Multiple separate positions that were causing issues

---

## 🎯 **Expected Behavior After Fixes**

### **Entry Signal Processing**
1. **Single Entry**: Creates ONE position with proper bracket orders
2. **Position Validation**: Checks existing positions before new entries
3. **Debounce Protection**: Ignores duplicate signals within 5 minutes
4. **Size Limits**: Prevents positions exceeding MAX_POSITION_SIZE

### **Exit Management**
1. **OCO Brackets**: Stop loss and take profit orders managed automatically
2. **Staged Exits**: TP1 closes 75% of position, TP2 closes remaining 25%
3. **No Manual Exits**: TP1/TP2 signals are ignored (OCO manages exits)
4. **Protection**: All positions have stop loss and take profit protection

### **Signal Filtering**
- ✅ **Processes**: `open_long`, `open_short`, `stop_out_long`, `stop_out_short`
- ❌ **Ignores**: `tp1_hit_long`, `tp1_hit_short`, `trim_long`, `trim_short`
- 🛡️ **Protects**: Against unwanted signals that caused oversized positions

---

## 🚀 **Deployment Instructions**

### **Railway.app**
1. **Set Environment Variables** in Railway dashboard:
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

2. **Deploy**: Railway will automatically deploy with new settings

### **Local Development**
1. **Create .env file**:
   ```bash
   cp ENVIRONMENT_VARIABLES_FIXED.md .env
   # Edit .env with your credentials
   ```

2. **Run webhook server**:
   ```bash
   python3 start_webhook.py --position-size 3
   ```

---

## 🔍 **Testing the Fixes**

### **Test 1: Position Size Validation**
```bash
# Send multiple rapid open_long signals
# Expected: Only first signal processes, others ignored due to debounce
```

### **Test 2: Signal Filtering**
```bash
# Send tp1_hit_long signal
# Expected: Signal ignored (IGNORE_TP1_SIGNALS=true)
```

### **Test 3: Staged Exits**
```bash
# Send open_long signal
# Expected: Single position with TP1 (75%) and TP2 (25%) orders
```

---

## ⚠️ **Important Notes**

1. **Stop Current Bot**: Stop the current bot before deploying fixes
2. **Close Positions**: Manually close any existing oversized positions
3. **Test Thoroughly**: Use paper trading to test the fixes
4. **Monitor Logs**: Watch for "ignored" and "debounced" messages
5. **Position Limits**: MAX_POSITION_SIZE prevents the -6 contract issue

---

## 🎯 **Expected Results**

After implementing these fixes:
- ✅ **No more oversized positions** (max 6 contracts per symbol)
- ✅ **No more orphaned positions** (all positions have protection)
- ✅ **No more OCO cancellations** (proper bracket order management)
- ✅ **Proper staged exits** (75% at TP1, 25% at TP2)
- ✅ **Signal filtering** (only entry signals processed)

The bot will now create **single positions with proper risk management** instead of multiple separate positions that caused the issues you experienced.
