# Rust Integration: Quick Start Guide

**5-Minute Setup**: Get Rust-optimized trading bot running

## ✅ Prerequisites Check

```bash
cd /Users/knealy/tradeBotServer
source venv/bin/activate

# Check Rust module
python -c "import trading_bot_rust; print('✅ Rust OK')"
```

## 🚀 Run Locally

```bash
# 1. Activate virtual environment
source venv/bin/activate

# 2. Run trading bot
python trading_bot.py

# 3. Look for this message:
# 🚀 Rust hot path enabled for order execution and queries (optimized)
```

## 🔧 Environment Variables

### Minimal Setup (.env file)

```bash
# Required
PROJECT_X_API_KEY=your_api_key
PROJECT_X_USERNAME=your_username

# Optional: Force Rust (default: auto-detect)
TOPSTEPX_USE_RUST=true
```

### Railway Deployment

Add to Railway Dashboard → Variables:
```bash
PROJECT_X_API_KEY=your_api_key
PROJECT_X_USERNAME=your_username
TOPSTEPX_USE_RUST=true  # Enable Rust
```

## 📊 Test Performance

### In Trading Bot Terminal

```bash
# Test order (look for ⚡ Rust logs)
trade MNQ BUY 1

# Test queries (look for ⚡ Rust logs)
orders
positions

# Test aggregation (look for ⚡ Rust logs)
history MNQ 5m 30
```

### Expected Logs

```
⚡ Rust place_order execution: 87.23ms
⚡ Rust get_open_orders execution: 45.12ms
⚡ Rust aggregation for MNQ 5m
```

## ⚡ Performance: Railway vs Local

| Operation | Local | Railway | Difference |
|-----------|-------|---------|------------|
| Orders | 85-95ms | 95-105ms | +10ms |
| Queries | 40-50ms | 50-60ms | +10ms |
| Aggregation | 0.5-1ms | 0.5-1ms | Same |

**Verdict**: Railway adds ~10ms (negligible). Use Railway for 24/7, local for dev.

## 🎯 Next Steps

1. **Test locally** - Verify Rust is working
2. **Deploy to Railway** - Add `TOPSTEPX_USE_RUST=true`
3. **Monitor performance** - Check logs for `⚡ Rust` messages
4. **See `docs/RUST_NEXT_STEPS.md`** - Further optimizations

## 🐛 Troubleshooting

**Rust not loading?**
```bash
cd rust
maturin develop --release
```

**Check if Rust is enabled:**
- Look for `🚀 Rust hot path enabled` in logs
- If missing, check build errors

