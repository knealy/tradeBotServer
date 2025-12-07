# Railway Deployment with Rust Integration

**Quick Reference**: Deploy Rust-optimized trading bot to Railway

## 🚀 Quick Deploy

### 1. Environment Variables

Add to Railway Dashboard → Variables:

```bash
# Required
PROJECT_X_API_KEY=your_api_key
PROJECT_X_USERNAME=your_username
TOPSTEPX_ACCOUNT_ID=your_account_id

# Enable Rust (recommended)
TOPSTEPX_USE_RUST=true

# Database (Railway provides automatically)
DATABASE_URL=postgresql://...  # Auto-provided

# Other existing variables
LOG_LEVEL=INFO
OVERNIGHT_RANGE_ENABLED=true
# ... etc
```

### 2. Deploy

```bash
# Via Railway CLI
railway up

# Or push to GitHub (auto-deploys)
git push origin main
```

### 3. Verify

Check Railway logs:
```bash
railway logs
```

Look for:
```
🚀 Rust hot path enabled for order execution and queries (optimized)
```

## 📊 Performance Expectations

### Railway vs Local

| Metric | Local | Railway | Notes |
|--------|-------|---------|-------|
| Order Execution | 85-95ms | 95-105ms | +10ms network latency |
| Query Operations | 40-50ms | 50-60ms | +10ms network latency |
| Aggregation | 0.5-1ms | 0.5-1ms | Same (CPU-bound) |

**Conclusion**: Railway adds ~10ms network latency (negligible for trading). Use Railway for 24/7 production, local for development.

## 🔧 Build Process

Railway automatically:
1. Detects `rust/` directory
2. Installs Rust toolchain
3. Builds with `maturin develop --release`
4. Links to Python

**No manual steps needed!**

## ✅ Verification

After deployment, check logs for:
- `🚀 Rust hot path enabled` - Rust is working
- `⚡ Rust [operation] execution: X.XXms` - Performance metrics
- `⚠️ Rust module not available` - Rust failed (check build logs)

## 🐛 Troubleshooting

**Rust not loading?**
- Check Railway build logs for compilation errors
- Verify `rust/` directory is in git repository
- Ensure `Cargo.toml` exists

**Performance not improving?**
- Verify Rust is enabled (check logs)
- Network-bound operations show 5-10% improvement
- CPU-bound operations show 2-3x improvement

