# Rust Integration: Deployment Guide

**Date**: December 5, 2025  
**Purpose**: Deploy Rust-optimized trading bot to Railway and test locally

## 🧪 Local Testing

### Quick Test

```bash
cd /Users/knealy/tradeBotServer
source venv/bin/activate

# 1. Verify Rust module
python -c "import trading_bot_rust; print('✅ Rust loaded')"

# 2. Run trading bot
python trading_bot.py
```

**What to Look For:**
```
🚀 Rust hot path enabled for order execution and queries (optimized)
```

### Test Commands in Trading Bot

```bash
# Test order execution (Rust hot path)
trade MNQ BUY 1
# Look for: ⚡ Rust place_order execution: X.XXms

# Test queries (Rust hot path)
orders
# Look for: ⚡ Rust get_open_orders execution: X.XXms

positions
# Look for: ⚡ Rust get_positions execution: X.XXms

# Test aggregation (Rust hot path)
history MNQ 5m 30
# Look for: ⚡ Rust aggregation for MNQ 5m
```

## 🔧 Environment Variables

### Required (Same as Before)

```bash
# API Credentials
PROJECT_X_API_KEY=your_api_key
PROJECT_X_USERNAME=your_username

# Optional
TOPSTEPX_ACCOUNT_ID=your_account_id
```

### New: Rust Control (Optional)

```bash
# Enable Rust explicitly (default: auto-detect)
TOPSTEPX_USE_RUST=true

# Disable Rust (use Python only)
TOPSTEPX_USE_RUST=false

# Auto-detect (default - no variable needed)
# Rust will be used if available
```

**Note:** Rust is **auto-enabled** if the module is available. You only need `TOPSTEPX_USE_RUST` to explicitly control it.

## 🚂 Railway Deployment

### Step 1: Add Environment Variables

In Railway Dashboard → Your Service → Variables, add:

```bash
# Existing variables (keep these)
PROJECT_X_API_KEY=your_api_key
PROJECT_X_USERNAME=your_username
TOPSTEPX_ACCOUNT_ID=your_account_id
DATABASE_URL=postgresql://...  # Railway provides automatically

# New: Rust control (optional)
TOPSTEPX_USE_RUST=true  # Enable Rust (recommended)

# Other existing variables
LOG_LEVEL=INFO
DISCORD_WEBHOOK_URL=...
OVERNIGHT_RANGE_ENABLED=true
# ... etc
```

### Step 2: Railway Build Process

Railway automatically:
1. ✅ Detects `rust/` directory
2. ✅ Installs Rust toolchain
3. ✅ Builds Rust module with `maturin develop --release`
4. ✅ Installs Python dependencies
5. ✅ Links Rust module to Python

**No additional configuration needed!**

### Step 3: Verify Deployment

Check Railway logs for:
```
🚀 Rust hot path enabled for order execution and queries (optimized)
```

If you see:
```
⚠️  Rust module not available. Using Python implementation.
```

**Troubleshooting:**
1. Check build logs for Rust compilation errors
2. Verify `rust/` directory is in repository
3. Check that `maturin` is in `requirements.txt` (if needed)

## ⚡ Performance: Railway vs Local

### Network Latency Comparison

| Operation | Local | Railway | Difference |
|-----------|-------|---------|------------|
| Order Execution | 85-95ms | 95-105ms | +10ms (Railway → API) |
| Query Operations | 40-50ms | 50-60ms | +10ms (Railway → API) |
| Aggregation | 0.5-1ms | 0.5-1ms | Same (CPU-bound) |

### When to Use Each

**Local (Your Machine):**
- ✅ Development and testing
- ✅ Faster iteration
- ✅ Direct debugging
- ❌ Requires machine to stay on
- ❌ No 24/7 uptime

**Railway (Cloud):**
- ✅ 24/7 operation
- ✅ Automatic deployments
- ✅ No local machine needed
- ✅ Production-ready
- ⚠️ +10ms network latency (negligible)

**Recommendation:**
- **Development**: Use local for faster testing
- **Production**: Use Railway for 24/7 operation
- **Performance Impact**: ~10ms difference is negligible for trading

## 📊 Performance Monitoring

### Check Logs for Performance

All Rust operations log execution time:

```bash
# Railway logs
railway logs

# Look for:
⚡ Rust place_order execution: 87.23ms
⚡ Rust get_open_orders execution: 45.12ms
⚡ Rust get_market_quote execution: 38.67ms
```

### Expected Performance

- **Order Execution**: 85-95ms (local), 95-105ms (Railway)
- **Query Operations**: 40-50ms (local), 50-60ms (Railway)
- **Small Aggregations**: 0.5-1ms (same everywhere, CPU-bound)

### Performance Comparison

Compare Rust vs Python fallback:
```
⚡ Rust place_order execution: 87.23ms  # Rust
🐍 Python execution: 95.45ms            # Python fallback
```

## 🔍 Troubleshooting

### Rust Not Loading on Railway

**Check:**
1. Railway build logs for Rust compilation
2. Verify `rust/` directory is committed to git
3. Check that `Cargo.toml` exists in `rust/` directory

**Fix:**
```bash
# Ensure rust directory is in repository
git add rust/
git commit -m "Add Rust module"
git push
```

### Rust Disabled Unexpectedly

**Check logs for:**
```
⚠️  Failed to initialize Rust executor: [error]
```

**Common causes:**
- Rust module not built
- Missing dependencies
- Import error

**Fix:**
- Check Railway build logs
- Verify Rust toolchain is available
- Ensure `maturin` can build the module

### Performance Not Improving

1. **Verify Rust is being used:**
   - Look for `🚀 Rust hot path enabled` in logs
   - Check for `⚡ Rust` performance logs

2. **Network-bound operations** show smaller improvements (5-10%) because network latency dominates

3. **CPU-bound operations** (aggregation) show larger improvements (2-3x)

## ✅ Verification Checklist

### Local Testing
- [ ] Rust module imports successfully
- [ ] Trading bot starts with Rust enabled
- [ ] Orders execute via Rust hot path
- [ ] Queries execute via Rust hot path
- [ ] Performance logs show Rust execution times

### Railway Deployment
- [ ] Environment variables set (including `TOPSTEPX_USE_RUST=true`)
- [ ] Railway build completes successfully
- [ ] Logs show Rust enabled
- [ ] Orders/queries working correctly
- [ ] Performance logs show Rust execution times

## 🎯 Next Steps

See `docs/RUST_NEXT_STEPS.md` for:
- Further speed optimizations
- Backtesting simulation engine
- Strategy development tools
- Expected value analysis

