# 🚀 Performance Testing Guide

**Last Updated**: November 9, 2025  
**Purpose**: Test database caching performance locally or on Railway

---

## 🎯 Quick Answer

### **Should You Test Locally?**

**Short Answer**: **Test on Railway first, then optionally test locally if you want to verify.**

**Why?**
- ✅ Railway is your production environment - test there first
- ✅ Database caching works identically on Railway and locally
- ✅ Local testing requires setup (Docker or PostgreSQL install)
- ✅ Railway testing is zero-setup (database already provisioned)

**When to Test Locally:**
- 🔧 You're debugging a specific issue
- 🔧 You want to develop/test without using Railway resources
- 🔧 You want to inspect the database directly with GUI tools
- 🔧 You're making database schema changes

---

## ⚡ Quick Test (Recommended)

### **Option 1: Test on Railway (Easiest)**

**Zero setup - just run the test script:**

#### **Method A: Get DATABASE_URL from Railway Dashboard (No CLI needed)**

1. **Go to Railway Dashboard**: https://railway.app
2. **Open your project** → Click on your **PostgreSQL service**
3. **Go to "Variables" tab** → Find `DATABASE_URL`
4. **Copy the value** (it looks like: `postgresql://postgres:password@containers-us-west-xx.railway.app:5432/railway`)

```bash
# 3. Run test script locally (connects to Railway DB)
export DATABASE_URL="<paste-your-database-url-here>"
python test_performance.py
```

#### **Method B: Install Railway CLI (Optional)**

**Install Railway CLI (macOS with Homebrew):**
```bash
# Install Railway CLI
brew install railway

# Or with npm (if you have Node.js)
npm i -g @railway/cli

# Authenticate
railway login

# Get database URL
railway variables | grep DATABASE_URL
```

**Then run test:**
```bash
export DATABASE_URL="$(railway variables | grep DATABASE_URL | cut -d'=' -f2-)"
python test_performance.py
```

**What this tests:**
- ✅ Real production database
- ✅ Actual cache performance
- ✅ Network latency (realistic)
- ✅ Railway PostgreSQL performance

**Expected Output:**
```
🚀 Trading Bot Performance Test
======================================================================

✅ Database: PostgreSQL connected

📊 Test Configuration:
   Symbol: MNQ
   Timeframe: 5m
   Bars: 100
   Cache Type: PostgreSQL

TEST 1: Cold Cache (First Fetch)
======================================================================
✅ Success: Retrieved 100 bars
⏱️  Duration: 109.2ms

TEST 2: Warm Cache (Second Fetch)
======================================================================
✅ Success: Retrieved 100 bars
⏱️  Duration: 5.3ms
⚡ Improvement: 95.1% faster
🚀 Speedup: 20.6x

TEST 3: Cache Consistency (10 Fetches)
======================================================================
  Fetch  1:    5.2ms  Fetch  2:    5.1ms
  Fetch  3:    5.3ms  Fetch  4:    5.2ms
  ...
📊 Statistics:
   Average: 5.2ms
   Minimum: 5.0ms
   Maximum: 5.5ms
```

---

### **Option 2: Test Locally with Railway DB**

**Same as Option 1, but you're running the bot locally:**

```bash
# Connect local bot to Railway database
export DATABASE_URL="postgresql://postgres:password@containers-us-west-xx.railway.app:5432/railway"

# Run bot
python trading_bot.py

# Test commands
Enter command: history MNQ 5m 100
Enter command: metrics
```

**Benefits:**
- ✅ Test locally but use production database
- ✅ No local PostgreSQL needed
- ✅ Realistic performance (network latency included)
- ✅ Can inspect Railway database from local tools

---

### **Option 3: Test Locally with Local PostgreSQL**

**Only if you want to test without Railway:**

#### **A. With Docker (Recommended if you have Docker)**

```bash
# Start PostgreSQL
docker run -d \
  --name trading-postgres \
  -e POSTGRES_PASSWORD=postgres \
  -e POSTGRES_USER=postgres \
  -e POSTGRES_DB=trading_bot \
  -p 5432:5432 \
  postgres:15

# Configure bot
export DATABASE_URL=postgresql://postgres:postgres@localhost:5432/trading_bot

# Run test
python test_performance.py
```

#### **B. Without Docker (macOS with Homebrew)**

```bash
# Install PostgreSQL
brew install postgresql@15
brew services start postgresql@15

# Create database
createdb trading_bot

# Configure bot
export DATABASE_URL=postgresql://$(whoami)@localhost:5432/trading_bot

# Run test
python test_performance.py
```

**Note**: You said you don't have Docker, so Option 3B is your local option.

---

### **Option 4: Test Without Database (In-Memory Only)**

**Fastest setup, but no persistence:**

```bash
# Don't set DATABASE_URL
unset DATABASE_URL

# Run test
python test_performance.py
```

**What you'll see:**
```
⚠️  Database: Not available (using in-memory cache only)

TEST 1: Cold Cache (First Fetch)
⏱️  Duration: 109.2ms

TEST 2: Warm Cache (Second Fetch)
⏱️  Duration: 0.8ms  (even faster than DB!)
⚡ Improvement: 99.3% faster
```

**Trade-offs:**
- ✅ Fastest response time (<1ms)
- ❌ Cache lost on restart
- ❌ No persistence testing

---

## 📊 What to Look For

### **Good Performance Indicators:**

1. **Cache Hit Rate**: >80% after warmup
   ```
   Hit Rate: 95.3% ✅ Excellent
   ```

2. **Response Time Improvement**: >90% faster
   ```
   Cold: 109ms → Warm: 5ms (95% improvement) ✅
   ```

3. **Consistency**: Low variance in warm cache times
   ```
   Average: 5.2ms, Min: 5.0ms, Max: 5.5ms ✅ Consistent
   ```

4. **API Call Reduction**: >70% fewer calls
   ```
   Before: 100 API calls
   After: 15 API calls (85% reduction) ✅
   ```

### **Warning Signs:**

1. **Low Hit Rate**: <50%
   - Cache not warming up properly
   - Database connection issues
   - Different timeframes being requested

2. **High Warm Cache Time**: >50ms
   - Database connection problems
   - Network latency (if using Railway DB)
   - Database not indexed properly

3. **Inconsistent Times**: High variance
   - Connection pool issues
   - Database contention
   - Network instability

---

## 🔍 Detailed Testing

### **Test Script Usage**

The `test_performance.py` script tests:

1. **Cold Cache**: First fetch (from API)
2. **Warm Cache**: Second fetch (from cache)
3. **Consistency**: 10 repeated fetches
4. **Metrics**: Cache hit rates and API stats
5. **Database Stats**: Cache coverage (if DB available)

**Customize test:**
```bash
# Test different symbol
TEST_SYMBOL=ES python test_performance.py

# Test different timeframe
TEST_TIMEFRAME=15m python test_performance.py

# Test different bar count
TEST_LIMIT=500 python test_performance.py
```

### **Manual Testing in Bot**

```bash
# Start bot
python trading_bot.py

# Test historical data fetching
Enter command: history MNQ 5m 100

# First time: Should see "Fetching from API..." (~109ms)
# Second time: Should see "✅ DB Cache HIT" (~5ms)

# Check metrics
Enter command: metrics

# Look for:
# - Cache hit rate >80%
# - API calls reduced
# - Fast response times
```

### **Test Persistence Across Restarts**

```bash
# 1. Fetch data
Enter command: history MNQ 5m 100
# (takes ~109ms - fetches from API)

# 2. Quit bot
Enter command: quit

# 3. Restart bot
python trading_bot.py

# 4. Fetch same data again
Enter command: history MNQ 5m 100
# (takes ~5ms - retrieved from database! ✅)
```

---

## 🎯 My Recommendation

### **For You (No Docker Installed):**

**Best Approach: Test on Railway First**

1. **Deploy to Railway** (you already have this)
2. **Get DATABASE_URL from Railway Dashboard:**
   - Go to https://railway.app → Your Project → PostgreSQL Service → Variables tab
   - Copy the `DATABASE_URL` value
3. **Run test script connecting to Railway DB:**
   ```bash
   export DATABASE_URL="<paste-from-dashboard>"
   python test_performance.py
   ```
4. **Check Railway logs** for cache performance
5. **Use `metrics` command** in Railway bot

**Why this is best:**
- ✅ Zero local setup
- ✅ Tests real production environment
- ✅ Same database as your live bot
- ✅ Realistic network conditions
- ✅ Can verify immediately

### **If You Want Local Testing:**

**Option A: Connect to Railway DB from Local**
- Run bot locally but connect to Railway PostgreSQL
- Best of both worlds: local development + production DB

**Option B: Install PostgreSQL Locally (macOS)**
```bash
brew install postgresql@15
brew services start postgresql@15
createdb trading_bot
export DATABASE_URL=postgresql://$(whoami)@localhost:5432/trading_bot
```
- More setup, but fully local
- Good for offline development

**Option C: Skip Local Testing**
- Just use Railway
- Database caching works the same everywhere
- No need to duplicate testing

---

## 📈 Expected Performance

### **With PostgreSQL Cache:**

| Metric | Without Cache | With Cache | Improvement |
|--------|---------------|------------|-------------|
| **First Fetch** | 109ms | 109ms | N/A (API call) |
| **Subsequent Fetches** | 109ms | 5ms | **95% faster** |
| **API Calls/Hour** | ~280 | ~85 | **70% reduction** |
| **Cache Hit Rate** | 0% | 85-95% | **Excellent** |
| **Persistence** | ❌ Lost on restart | ✅ Survives restart | **Critical** |

### **With In-Memory Cache:**

| Metric | Without Cache | With Cache | Improvement |
|--------|---------------|------------|-------------|
| **First Fetch** | 109ms | 109ms | N/A (API call) |
| **Subsequent Fetches** | 109ms | <1ms | **99% faster** |
| **API Calls/Hour** | ~280 | ~85 | **70% reduction** |
| **Cache Hit Rate** | 0% | 85-95% | **Excellent** |
| **Persistence** | ❌ Lost on restart | ❌ Lost on restart | **No improvement** |

**Key Difference**: PostgreSQL = persistence, In-Memory = speed (but no persistence)

---

## ✅ Success Criteria

After testing, you should see:

- [ ] **Cold cache**: ~100-120ms (API call)
- [ ] **Warm cache**: <10ms (database) or <1ms (in-memory)
- [ ] **Cache hit rate**: >80% after 5-10 fetches
- [ ] **API calls reduced**: >70% fewer calls
- [ ] **Persistence**: Data survives bot restart (PostgreSQL only)
- [ ] **Consistency**: Low variance in warm cache times (<2ms)

---

## 🚀 Quick Start Commands

```bash
# Test on Railway (recommended)
export DATABASE_URL="<railway-db-url>"
python test_performance.py

# Test in bot
python trading_bot.py
Enter command: history MNQ 5m 100
Enter command: metrics

# Test persistence
python trading_bot.py
# Fetch data, quit, restart, fetch again
```

---

## 💡 Bottom Line

**For your situation (no Docker):**

1. **Test on Railway** - It's your production environment, test there first
2. **Use test script** - Quick way to verify performance
3. **Check metrics** - Use `metrics` command in bot
4. **Skip local testing** - Unless you need to debug something specific

**The database caching will work the same on Railway and locally.** If it works on Railway, it will work locally (and vice versa). The main benefit of local testing is for development/debugging, not for verifying the caching works.

**Recommendation: Test on Railway, then move on to building your dashboard!** 🚀

