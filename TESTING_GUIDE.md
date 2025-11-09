# 🧪 Comprehensive Testing Guide

**Last Updated**: November 9, 2025  
**Purpose**: Test all new features locally and on Railway

---

## 📋 Table of Contents

1. [Quick Start Testing](#quick-start-testing)
2. [Local Testing with Docker PostgreSQL](#local-testing-with-docker-postgresql)
3. [Local Testing without Docker](#local-testing-without-docker)
4. [Railway Testing](#railway-testing)
5. [Performance Testing](#performance-testing)
6. [Strategy Testing](#strategy-testing)
7. [Troubleshooting](#troubleshooting)

---

## 🚀 Quick Start Testing

### **Test Everything in 5 Minutes**

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Set environment variables
cp .env.example .env
# Edit .env with your credentials

# 3. Run the bot locally
python trading_bot.py

# 4. Test basic commands
Enter command: accounts     # List accounts
Enter command: balance      # Check balance
Enter command: contracts    # List contracts
Enter command: metrics      # View performance
Enter command: quit
```

---

## 🐳 Local Testing with Docker PostgreSQL

### **Why Docker?**
- ✅ Test database caching locally
- ✅ Same environment as Railway
- ✅ Measure performance improvements
- ✅ Test persistence across restarts

### **Step 1: Install Docker**

**macOS:**
```bash
# Option 1: Docker Desktop (GUI, easier)
# Download from: https://www.docker.com/products/docker-desktop

# Option 2: Homebrew (CLI only)
brew install --cask docker
# Or just docker engine:
brew install docker

# Verify installation
docker --version
```

**Linux:**
```bash
# Ubuntu/Debian
sudo apt update
sudo apt install docker.io docker-compose
sudo systemctl start docker
sudo systemctl enable docker

# Add your user to docker group (no sudo needed)
sudo usermod -aG docker $USER
newgrp docker

# Verify
docker --version
```

**Windows:**
```powershell
# Download Docker Desktop from:
# https://www.docker.com/products/docker-desktop

# Verify in PowerShell
docker --version
```

---

### **Step 2: Start PostgreSQL in Docker**

```bash
# Pull PostgreSQL 15 image
docker pull postgres:15

# Run PostgreSQL container
docker run -d \
  --name trading-bot-postgres \
  -e POSTGRES_PASSWORD=postgres \
  -e POSTGRES_USER=postgres \
  -e POSTGRES_DB=trading_bot \
  -p 5432:5432 \
  -v trading-bot-data:/var/lib/postgresql/data \
  postgres:15

# Verify it's running
docker ps

# Should see:
# CONTAINER ID   IMAGE         STATUS         PORTS
# abc123...      postgres:15   Up 2 seconds   0.0.0.0:5432->5432/tcp
```

**What this does:**
- `-d`: Run in background
- `--name`: Container name for easy management
- `-e POSTGRES_PASSWORD`: Database password
- `-p 5432:5432`: Expose port 5432 (PostgreSQL default)
- `-v trading-bot-data:/var/lib/postgresql/data`: Persistent storage
- `postgres:15`: Use PostgreSQL version 15

---

### **Step 3: Configure Bot to Use Local PostgreSQL**

**Option A: Use .env file**
```bash
# Add to .env
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/trading_bot
```

**Option B: Export environment variable**
```bash
export DATABASE_URL=postgresql://postgres:postgres@localhost:5432/trading_bot
```

**Connection String Format:**
```
postgresql://[user]:[password]@[host]:[port]/[database]

user:     postgres
password: postgres
host:     localhost (or 127.0.0.1)
port:     5432
database: trading_bot
```

---

### **Step 4: Test Database Connection**

```bash
# Start the bot
python trading_bot.py

# Look for these log lines:
# ✅ PostgreSQL connected: postgresql://postgres:***@localhost:5432/trading_bot
# ✅ Database schema initialized (6 tables created)
# ✅ Database cache ready

# Test database caching
Enter command: history MNQ 5m 100

# First time (no cache):
# ⏱️  Fetching from API... (takes ~109ms)
# 💾 Saved 100 bars to database cache

# Second time (with cache):
# ✅ DB Cache HIT: 100 bars for MNQ 5m
# ⚡ Retrieved in ~5ms (95% faster!)
```

---

### **Step 5: Verify Database Performance**

```bash
# Run performance test
Enter command: metrics

# Look for cache stats:
📊 PERFORMANCE METRICS REPORT
================================================================================

💾 CACHE PERFORMANCE:
  historical_MNQ_5m:
    hits: 45
    misses: 3
    hit_rate: 93.8%
    avg_response_time: 5.2ms

🌐 API CALLS:
  Total: 15
  Average: 87ms
  Slowest: POST /api/History/retrieveBars: 109ms

# Compare without cache (before):
# Total API calls: ~100
# Average response: ~109ms

# With cache (after):
# Total API calls: ~15 (85% reduction!)
# Average response: ~10ms (90% faster!)
```

---

### **Step 6: Test Persistence Across Restarts**

```bash
# 1. Fetch some data
Enter command: history MNQ 5m 100
# (takes ~109ms - fetches from API)

# 2. Quit the bot
Enter command: quit

# 3. Restart the bot
python trading_bot.py

# 4. Fetch same data again
Enter command: history MNQ 5m 100
# (takes ~5ms - retrieved from database! ⚡)

# SUCCESS! Data persisted across restarts
```

---

### **Step 7: Inspect Database Directly**

**Using psql (PostgreSQL CLI):**
```bash
# Connect to database
docker exec -it trading-bot-postgres psql -U postgres -d trading_bot

# List all tables
\dt

# Should see:
#  Schema |       Name          | Type  | Owner
# --------+---------------------+-------+----------
#  public | account_state       | table | postgres
#  public | api_metrics         | table | postgres
#  public | cache_metadata      | table | postgres
#  public | historical_bars     | table | postgres
#  public | strategy_performance| table | postgres
#  public | trade_history       | table | postgres

# View cached bars
SELECT symbol, timeframe, COUNT(*) as bar_count,
       MIN(timestamp) as oldest, MAX(timestamp) as newest
FROM historical_bars
GROUP BY symbol, timeframe;

#  symbol | timeframe | bar_count |       oldest        |       newest
# --------+-----------+-----------+---------------------+---------------------
#  MNQ    | 5m        |       100 | 2025-11-08 10:00:00 | 2025-11-08 18:20:00
#  MNQ    | 15s       |      5000 | 2025-11-08 17:00:00 | 2025-11-08 18:20:00

# View API performance
SELECT endpoint, method, 
       COUNT(*) as calls,
       ROUND(AVG(duration_ms), 2) as avg_ms,
       ROUND(MAX(duration_ms), 2) as max_ms,
       COUNT(*) FILTER (WHERE success = true) * 100.0 / COUNT(*) as success_rate
FROM api_metrics
WHERE created_at > NOW() - INTERVAL '1 hour'
GROUP BY endpoint, method
ORDER BY avg_ms DESC;

# Exit psql
\q
```

**Using GUI Tool (DBeaver, pgAdmin, etc.):**
```
Host: localhost
Port: 5432
Database: trading_bot
Username: postgres
Password: postgres
```

---

### **Step 8: Performance Benchmarking**

**Test Script:**
```bash
#!/bin/bash
# benchmark.sh

echo "🚀 Performance Benchmarking: With vs Without Cache"
echo "=================================================="

# Warm up cache
python3 << EOF
from trading_bot import TopStepXTradingBot
import time

bot = TopStepXTradingBot(api_key="$PROJECT_X_API_KEY", username="$PROJECT_X_USERNAME")
bot.authenticate()

# First fetch (no cache) - measure time
start = time.time()
bars = bot.get_historical_data("MNQ", "5m", 100)
cold_time = (time.time() - start) * 1000
print(f"❄️  Cold cache (API): {cold_time:.0f}ms")

# Second fetch (with cache) - measure time
start = time.time()
bars = bot.get_historical_data("MNQ", "5m", 100)
warm_time = (time.time() - start) * 1000
print(f"🔥 Warm cache (DB): {warm_time:.0f}ms")

improvement = ((cold_time - warm_time) / cold_time) * 100
print(f"⚡ Performance improvement: {improvement:.1f}%")
EOF
```

**Expected Output:**
```
🚀 Performance Benchmarking: With vs Without Cache
==================================================
❄️  Cold cache (API): 109ms
🔥 Warm cache (DB): 5ms
⚡ Performance improvement: 95.4%
```

---

### **Step 9: Clean Up Docker (Optional)**

```bash
# Stop PostgreSQL container
docker stop trading-bot-postgres

# Start it again later
docker start trading-bot-postgres

# Remove container (WARNING: deletes data unless using volume)
docker rm -f trading-bot-postgres

# Remove volume (WARNING: deletes ALL data)
docker volume rm trading-bot-data

# Remove image
docker rmi postgres:15
```

---

## 🏠 Local Testing without Docker

### **Use In-Memory Cache Only**

If you don't want to install Docker, the bot still works great with in-memory caching:

```bash
# Don't set DATABASE_URL
# Remove or comment out in .env:
# DATABASE_URL=...

# Start bot
python trading_bot.py

# You'll see:
# ⚠️  PostgreSQL not configured (DATABASE_URL not set)
# ✅ Using in-memory cache only

# Performance:
# - First fetch: ~109ms (API)
# - Subsequent fetches: <1ms (memory)
# - Cache lost on restart (not persistent)
```

**Pros:**
- ✅ No Docker needed
- ✅ Zero setup
- ✅ <1ms response (even faster than DB!)

**Cons:**
- ❌ Cache lost on restart
- ❌ Can't test persistence
- ❌ Limited cache size (memory constraints)

---

## ☁️ Railway Testing

### **Test Deployed Bot on Railway**

**Step 1: Verify Deployment**
```bash
# Check Railway logs
railway logs

# Look for:
✅ PostgreSQL connected: postgresql://postgres:***@containers-us-west-xx.railway.app:5432/railway
✅ Database schema initialized (6 tables created)
✅ Database cache ready
🚀 Starting async webhook server on 0.0.0.0:8080
```

**Step 2: Test Health Endpoint**
```bash
# Get your Railway URL (e.g., https://your-app.railway.app)
curl https://your-app.railway.app/health

# Expected response:
{
  "status": "healthy",
  "authenticated": true,
  "selected_account": "TopStep Express - 3 of 10",
  "account_id": "11481693",
  "server_uptime": "0:15:32",
  "task_queue": {
    "queue_size": 0,
    "active_tasks": 3,
    "success_rate": "98.5%"
  }
}
```

**Step 3: Test Metrics Endpoint**
```bash
curl https://your-app.railway.app/metrics | jq

# View cache performance:
{
  "performance": {
    "cache": {
      "historical_MNQ_5m": {
        "hits": 245,
        "misses": 12,
        "hit_rate": "95.3%"
      }
    },
    "api": {
      "total_calls": 67,
      "avg_duration_ms": 12.3,
      "error_rate": "0.0%"
    }
  }
}
```

**Step 4: Test Database Persistence**
```bash
# Trigger a restart on Railway
railway restart

# Wait ~30 seconds for restart

# Check metrics again
curl https://your-app.railway.app/metrics | jq '.performance.cache'

# Should still show cached data (persisted!)
{
  "historical_MNQ_5m": {
    "hits": 245,  # Counter reset, but data still cached
    "misses": 0    # No API calls needed!
  }
}
```

**Step 5: Connect to Railway PostgreSQL from Local**

```bash
# Get database URL from Railway
railway variables | grep DATABASE_URL

# Or view in Railway dashboard:
# Project > Variables > DATABASE_URL

# Connect from local machine
export DATABASE_URL=postgresql://postgres:password@containers-us-west-xx.railway.app:5432/railway

# Test connection
psql $DATABASE_URL

# Or use in bot
python trading_bot.py
# Bot will connect to Railway database!
```

---

## 📊 Performance Testing

### **Test 1: Cache Hit Rate**

```python
# test_cache_performance.py
from trading_bot import TopStepXTradingBot
import time

bot = TopStepXTradingBot(api_key="...", username="...")
bot.authenticate()

# Fetch data 10 times
for i in range(10):
    start = time.time()
    bars = bot.get_historical_data("MNQ", "5m", 100)
    duration = (time.time() - start) * 1000
    print(f"Fetch {i+1}: {duration:.1f}ms")

# Expected output:
# Fetch 1: 109.2ms  (API call - cold cache)
# Fetch 2: 5.3ms    (DB cache hit)
# Fetch 3: 5.1ms    (DB cache hit)
# ...
# Fetch 10: 5.2ms   (DB cache hit)
```

### **Test 2: API Call Reduction**

```bash
# Before cache (baseline)
# Count API calls over 1 hour:
# - Balance checks: 60 calls (every minute)
# - Fill checks: 120 calls (every 30 seconds)
# - Historical data: 100+ calls
# Total: ~280 API calls/hour

# After cache
# Count API calls over 1 hour:
# - Balance checks: 60 calls (still needed)
# - Fill checks: 120 calls (still needed)
# - Historical data: ~5 calls (95% cache hit!)
# Total: ~185 API calls/hour (34% reduction!)
```

### **Test 3: Memory Usage**

```bash
# Monitor memory usage
watch -n 1 'ps aux | grep python'

# Without cache:
# ~200 MB (base + in-memory cache)

# With PostgreSQL:
# ~250 MB (base + in-memory + DB connection)

# Memory increase: ~50 MB
# Performance improvement: 95% faster
# Trade-off: Worth it! ✅
```

---

## 🎯 Strategy Testing

### **Test Overnight Range Strategy**

```bash
# 1. Enable strategy in .env
OVERNIGHT_ENABLED=true
OVERNIGHT_SYMBOL=MNQ
OVERNIGHT_POSITION_SIZE=1
OVERNIGHT_USE_BREAKEVEN=true

# 2. Start bot
python trading_bot.py

# 3. Monitor strategy
Enter command: strategy overnight_range status

# Expected output:
Strategy: overnight_range
Status: ACTIVE
Symbol: MNQ
Position Size: 1
Overnight High: 25355.00
Overnight Low: 25061.75
Daily ATR: 189.25
Upper Zone: [25443.50, 25472.25]
Lower Zone: [24972.50, 24999.00]
Pending Orders:
  - LONG stop: 25355.00 (SL: 25299.75, TP: 25443.50)
  - SHORT stop: 25061.75 (SL: 25117.00, TP: 24999.00)

# 4. Test fill notification
# When an order fills, check Discord for notification:
# 🟢 LONG POSITION FILLED
# Symbol: MNQ
# Entry: 25355.00
# ...
```

### **Test Breakeven Stop**

```bash
# 1. Manually place a trade
Enter command: bracket MNQ BUY 1 50 100

# 2. Wait for price to move +15 points
# Bot will automatically:
# - Detect profit >= 15 pts
# - Move stop to entry price
# - Log: "✅ Breakeven stop adjusted: 25355.00"
# - Send Discord notification

# 3. Verify stop moved
Enter command: orders
# Should see stop at entry price
```

---

## 🐛 Troubleshooting

### **Database Connection Issues**

**Problem: "Could not connect to PostgreSQL"**
```bash
# Check if Docker container is running
docker ps

# If not running, start it
docker start trading-bot-postgres

# Check logs
docker logs trading-bot-postgres

# Test connection manually
psql postgresql://postgres:postgres@localhost:5432/trading_bot
```

**Problem: "Connection refused"**
```bash
# PostgreSQL might not be ready yet
docker logs trading-bot-postgres | grep "ready"

# Wait for: "database system is ready to accept connections"

# Or restart with health check
docker run -d \
  --name trading-bot-postgres \
  -e POSTGRES_PASSWORD=postgres \
  -e POSTGRES_USER=postgres \
  -e POSTGRES_DB=trading_bot \
  -p 5432:5432 \
  --health-cmd="pg_isready -U postgres" \
  --health-interval=5s \
  --health-timeout=3s \
  --health-retries=3 \
  postgres:15

# Check health
docker ps
# STATUS column should show "healthy"
```

**Problem: "Authentication failed"**
```bash
# Check password
echo $DATABASE_URL

# Should match Docker password:
# postgresql://postgres:postgres@localhost:5432/trading_bot

# Reset password
docker exec -it trading-bot-postgres psql -U postgres
ALTER USER postgres PASSWORD 'newpassword';
\q

# Update .env
DATABASE_URL=postgresql://postgres:newpassword@localhost:5432/trading_bot
```

---

### **Cache Not Working**

**Problem: Still seeing API calls on repeated requests**
```bash
# Check if database is connected
python trading_bot.py

# Look for:
# ✅ PostgreSQL connected
# ✅ Database cache ready

# If not, check DATABASE_URL
echo $DATABASE_URL

# Test cache explicitly
Enter command: history MNQ 5m 100
# Look for: "✅ DB Cache HIT" or "💾 Saved to database cache"

# If still not working, check logs
tail -f trading_bot.log | grep -i cache
```

---

### **Performance Not Improved**

**Problem: Cache hit rate is low**
```bash
# Check cache coverage
Enter command: metrics

# Look for hit rate:
# hit_rate: 15%  ❌ Too low!

# Possible causes:
1. Cache not warmed up yet (wait 5-10 minutes)
2. Requesting different timeframes (each is cached separately)
3. Database connection issues
4. Cache expiration too aggressive

# Solution: Warm up cache
for i in {1..10}; do
  echo "history MNQ 5m 100" | python trading_bot.py
  sleep 1
done

# Check metrics again - should be >80%
```

---

### **Docker Issues**

**Problem: Docker daemon not running**
```bash
# macOS: Start Docker Desktop app
open -a Docker

# Linux: Start service
sudo systemctl start docker

# Verify
docker ps
```

**Problem: Permission denied**
```bash
# Add user to docker group (Linux)
sudo usermod -aG docker $USER
newgrp docker

# Or use sudo (not recommended)
sudo docker ps
```

**Problem: Port 5432 already in use**
```bash
# Check what's using port 5432
lsof -i :5432

# If local PostgreSQL is running, stop it:
sudo systemctl stop postgresql  # Linux
brew services stop postgresql  # macOS

# Or use different port:
docker run -p 5433:5432 postgres:15
# Update DATABASE_URL:
DATABASE_URL=postgresql://postgres:postgres@localhost:5433/trading_bot
```

---

## ✅ Success Checklist

After testing, verify:

- [ ] Docker PostgreSQL running
- [ ] Bot connects to database
- [ ] Cache hit rate >80%
- [ ] API calls reduced by >70%
- [ ] Response time <10ms (cached)
- [ ] Data persists across restarts
- [ ] Metrics tracking works
- [ ] Discord notifications received
- [ ] Strategies execute correctly
- [ ] Railway deployment healthy

---

## 📚 Additional Resources

- **PostgreSQL Guide**: `POSTGRESQL_SETUP.md`
- **Architecture Overview**: `CURRENT_ARCHITECTURE.md`
- **Performance Metrics**: `ASYNC_IMPROVEMENTS.md`
- **Strategy Guide**: `OVERNIGHT_STRATEGY_GUIDE.md`
- **Deployment Guide**: `DEPLOYMENT_GUIDE.md`

---

**You're now ready to test the full system with maximum performance!** 🚀

