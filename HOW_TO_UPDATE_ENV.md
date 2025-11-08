# How to Update Your .env File

## Quick Guide

You have **2 options** for updating your `.env` file:

---

## Option 1: Add Only New Variables (RECOMMENDED)

This keeps your current `.env` file and just adds the new Option A variables.

### Steps:

1. **Open your `.env` file** in a text editor

2. **Copy all content from** `NEW_ENV_VARIABLES_TO_ADD.txt`

3. **Paste at the end** of your `.env` file

4. **Save** the file

5. **Restart** your trading bot

**Time**: ~2 minutes  
**Risk**: Low (only adds new variables)  
**Result**: All Option A features available

---

## Option 2: Replace Entire File (Clean Start)

This gives you a clean, organized `.env` file with everything properly commented.

### Steps:

1. **Backup** your current `.env` file:
   ```bash
   cp .env .env.backup
   ```

2. **Copy all content from** `YOUR_ENV_FILE_COMPLETE.txt`

3. **Replace** your entire `.env` file with this content

4. **Verify** your credentials are correct (username, API key, JWT token)

5. **Save** the file

6. **Restart** your trading bot

**Time**: ~5 minutes  
**Risk**: Medium (replaces file, but you have backup)  
**Result**: Clean, organized configuration

---

## What Gets Added?

### New Variables Count: ~85

| Category | Variables | Default State |
|----------|-----------|---------------|
| Strategy Manager | 2 | Active |
| Overnight Range - New Options | 7 | Active |
| Market Condition Filters | 13 | **OFF** |
| Mean Reversion Strategy | 28 | **DISABLED** |
| Trend Following Strategy | 28 | **DISABLED** |
| Account Tracker | 3 | Active |
| API Settings | 2 | Active |

---

## Important Notes

### ✅ Safe Additions (No Impact):

These are **already disabled** and won't change your bot's behavior:

```bash
# Market filters - All OFF
OVERNIGHT_FILTER_RANGE_SIZE=false
OVERNIGHT_FILTER_GAP=false
OVERNIGHT_FILTER_VOLATILITY=false
OVERNIGHT_FILTER_DLL_PROXIMITY=false

# Additional strategies - All DISABLED
MEAN_REVERSION_ENABLED=false
TREND_FOLLOWING_ENABLED=false
```

### ⚠️ Variables That Take Effect Immediately:

These will be used right away (but are set to safe defaults):

```bash
# Strategy Manager
MAX_CONCURRENT_STRATEGIES=3  # Allows up to 3 strategies
GLOBAL_MAX_POSITIONS=5  # Max 5 positions total

# Time Windows (sensible defaults)
OVERNIGHT_RANGE_START_TIME=09:30
OVERNIGHT_RANGE_END_TIME=15:45
OVERNIGHT_RANGE_NO_TRADE_START=15:30
OVERNIGHT_RANGE_NO_TRADE_END=16:00
```

### 🔧 Variables You Should Customize:

Update these based on your TopStepX account:

```bash
# Your account limits
DAILY_LOSS_LIMIT=1000.00  # Your DLL
MAXIMUM_LOSS_LIMIT=3000.00  # Your MLL
INITIAL_BALANCE=150000.00  # Your starting balance
```

---

## Step-by-Step: Option 1 (Add New Variables)

### 1. Open Terminal

```bash
cd /Users/susan/projectXbot
nano .env
```

### 2. Scroll to Bottom

Press `Ctrl+End` or `Cmd+Down`

### 3. Add a Section Header

```bash
# =============================================================================
# OPTION A ADDITIONS - 2025-11-08
# =============================================================================
```

### 4. Copy from NEW_ENV_VARIABLES_TO_ADD.txt

Open `NEW_ENV_VARIABLES_TO_ADD.txt`, copy all content

### 5. Paste into .env

Paste at the current cursor position

### 6. Save & Exit

- Press `Ctrl+X`
- Press `Y`
- Press `Enter`

### 7. Verify

```bash
grep "MEAN_REVERSION_ENABLED" .env
# Should show: MEAN_REVERSION_ENABLED=false
```

### 8. Restart Bot

```bash
python3 trading_bot.py
```

---

## Step-by-Step: Option 2 (Replace Entire File)

### 1. Backup Current .env

```bash
cd /Users/susan/projectXbot
cp .env .env.backup
```

### 2. Open .env

```bash
nano .env
```

### 3. Delete All Content

- Press `Ctrl+K` repeatedly to delete all lines
- Or select all (`Cmd+A`) and delete

### 4. Copy from YOUR_ENV_FILE_COMPLETE.txt

Open `YOUR_ENV_FILE_COMPLETE.txt`, copy all content

### 5. Paste into .env

Paste into the now-empty file

### 6. Save & Exit

- Press `Ctrl+X`
- Press `Y`
- Press `Enter`

### 7. Verify

```bash
tail -20 .env
# Should show the last few variables
```

### 8. Restart Bot

```bash
python3 trading_bot.py
```

---

## Verification Checklist

After updating, verify these work:

### ✅ Bot Starts Successfully

```bash
python3 trading_bot.py
```

Look for:
```
🎯 Overnight Range Strategy initialized
Strategy manager initialized
Strategies registered with manager
```

### ✅ Overnight Range Still Works

In the bot, try:
```
strategy_status
```

Should show your overnight range configuration.

### ✅ New Commands Work

In the bot, try:
```
strategies list
```

Should show all 3 strategies:
- Overnight Range (active)
- Mean Reversion (not loaded)
- Trend Following (not loaded)

### ✅ Market Filters Are OFF

Check logs for:
```
Market Condition Filters:
  Range Size: DISABLED (50-500 pts)
  Gap Filter: DISABLED (max 200 pts)
  Volatility Filter: DISABLED (ATR 20-200)
  DLL Proximity: DISABLED (threshold 75%)
```

---

## Troubleshooting

### Issue: Bot won't start after update

**Solution**: Check for syntax errors
```bash
python3 -c "import os; from dotenv import load_dotenv; load_dotenv('.env'); print('✅ .env file is valid')"
```

### Issue: Variables not being read

**Solution**: Restart the bot
```bash
# Kill existing process
pkill -f trading_bot.py

# Start fresh
python3 trading_bot.py
```

### Issue: Still see old behavior

**Solution**: Clear any cached config
```bash
rm -f *.pyc
rm -rf __pycache__
python3 trading_bot.py
```

### Issue: Want to revert changes

**Solution**: Use your backup
```bash
cp .env.backup .env
python3 trading_bot.py
```

---

## Quick Reference

### Files Created for You:

1. **YOUR_ENV_FILE_COMPLETE.txt** - Complete .env file (all variables)
2. **NEW_ENV_VARIABLES_TO_ADD.txt** - Only new variables to add
3. **HOW_TO_UPDATE_ENV.md** - This guide

### Choose Your Method:

| Method | When to Use | Difficulty |
|--------|-------------|------------|
| **Option 1** (Add) | Safe, keep current config | Easy |
| **Option 2** (Replace) | Want clean, organized file | Medium |

### Recommendation:

**Use Option 1** if:
- ✅ Your current .env works
- ✅ You want to add new features gradually
- ✅ You prefer minimal changes

**Use Option 2** if:
- ✅ You want a clean, organized file
- ✅ You're comfortable with file replacements
- ✅ You have a backup

---

## After Adding Variables

### Test the New Features:

#### 1. List All Strategies
```
strategies list
```

#### 2. Enable a Market Filter (Optional)
Edit `.env`:
```bash
OVERNIGHT_FILTER_RANGE_SIZE=true
```
Restart bot, check logs.

#### 3. Test Mean Reversion (Optional)
Edit `.env`:
```bash
MEAN_REVERSION_ENABLED=true
MEAN_REVERSION_SYMBOLS=MES
```
Restart bot, use:
```
strategies start mean_reversion MES
```

---

## Summary

### What You're Adding: ~85 new environment variables

### What Changes: Nothing (by default)

### What You Get:
- ✅ Market condition filters (ready to enable)
- ✅ Mean reversion strategy (ready to activate)
- ✅ Trend following strategy (ready to activate)
- ✅ Modular strategy system (ready to use)

### Time Required:
- Option 1: ~2 minutes
- Option 2: ~5 minutes

### Risk Level:
- Option 1: Low
- Option 2: Medium (but you have backup)

---

**👉 Choose Option 1 (Add New Variables) for the safest, quickest update!**

