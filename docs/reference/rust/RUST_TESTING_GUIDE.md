# Rust Integration Testing Guide

**Date**: December 5, 2025  
**Purpose**: Test the new Rust-optimized trading bot system

## Quick Start: Local Testing

### 1. Verify Rust Module is Built

```bash
cd /Users/knealy/tradeBotServer
source venv/bin/activate

# Verify Rust module is available
python -c "import trading_bot_rust; print('✅ Rust module loaded'); print('Classes:', [x for x in dir(trading_bot_rust) if not x.startswith('_')])"
```

**Expected output:**
```
✅ Rust module loaded
Classes: ['Bar', 'BarAggregator', 'OrderExecutor', 'QueryExecutor', 'aggregate_bars', 'aggregate_bars_raw', 'parse_timeframe', 'trading_bot_rust']
```

### 2. Test Adapter Initialization

```bash
python -c "
from brokers.topstepx_adapter import TopStepXAdapter
from core.auth import AuthManager
from core.market_data import ContractManager

# Initialize (will auto-detect Rust)
auth = AuthManager()
adapter = TopStepXAdapter(auth_manager=auth)
print('✅ Adapter initialized')
print(f'Rust enabled: {adapter._use_rust}')
"
```

**Expected output:**
```
🚀 Rust hot path enabled for order execution and queries (optimized)
✅ Adapter initialized
Rust enabled: True
```

### 3. Run Trading Bot Locally

```bash
# Make sure you're in the project directory
cd /Users/knealy/tradeBotServer
source venv/bin/activate

# Run the bot
python trading_bot.py
```

The bot will:
- ✅ Auto-detect and use Rust if available
- ✅ Fall back to Python if Rust fails
- ✅ Log performance metrics for each operation

## Environment Variables

### Required (Same as Before)

```bash
# API Credentials
PROJECT_X_API_KEY=your_api_key_here
PROJECT_X_USERNAME=your_username_here

# Optional: Account ID (auto-selects if not provided)
TOPSTEPX_ACCOUNT_ID=your_account_id
```

### New: Rust Control (Optional)

```bash
# Force Rust usage (default: auto-detect)
TOPSTEPX_USE_RUST=true

# Disable Rust (use Python only)
TOPSTEPX_USE_RUST=false

# Auto-detect (default - uses Rust if available)
# TOPSTEPX_USE_RUST not set, or set to empty
```

**Note:** The bot automatically detects Rust availability. You only need `TOPSTEPX_USE_RUST` if you want to explicitly enable/disable it.

## Testing Commands

### Test Order Execution (Rust Hot Path)

```bash
# In trading_bot.py terminal:
trade MNQ BUY 1

# Watch for log message:
# ⚡ Rust place_order execution: X.XXms
```

### Test Query Operations (Rust Hot Path)

```bash
# In trading_bot.py terminal:
orders
positions
history MNQ 5m 30

# Watch for log messages:
# ⚡ Rust get_open_orders execution: X.XXms
# ⚡ Rust get_positions execution: X.XXms
# ⚡ Rust aggregation for MNQ 5m
```

### Test Market Data (Rust Hot Path)

```bash
# In trading_bot.py terminal:
contracts  # Uses Rust if cache disabled

# Watch for log message:
# ⚡ Rust get_available_contracts execution: X.XXms
```

## Performance Verification

### Check Logs for Performance Metrics

All Rust operations log execution time. Look for:

```
⚡ Rust place_order execution: 87.23ms
⚡ Rust get_open_orders execution: 45.12ms
⚡ Rust get_market_quote execution: 38.67ms
```

Compare with Python fallback (if Rust fails):
```
🐍 Python execution: 95.45ms
```

### Expected Performance Improvements

- **Order Execution**: 5-10% faster (85-95ms vs 90-100ms)
- **Query Operations**: 5-10% faster (network-bound)
- **Small Aggregations**: 2-3x faster (0.5-1ms vs 1-3ms for < 100 bars)
- **Market Data**: 5-10% faster (network-bound)

## Railway Deployment

### Environment Variables for Railway

Add these to your Railway project:

```bash
# Required (same as before)
PROJECT_X_API_KEY=your_api_key
PROJECT_X_USERNAME=your_username
TOPSTEPX_ACCOUNT_ID=your_account_id

# Optional: Rust control
TOPSTEPX_USE_RUST=true  # Enable Rust (default: auto-detect)

# Database (Railway provides automatically)
DATABASE_URL=postgresql://...  # Railway auto-provides

# Other existing variables
LOG_LEVEL=INFO
DISCORD_WEBHOOK_URL=...
OVERNIGHT_RANGE_ENABLED=true
# ... etc
```

### Railway Build Configuration

Railway will automatically:
1. ✅ Detect `rust/` directory
2. ✅ Build Rust module during deployment
3. ✅ Install Python dependencies
4. ✅ Link Rust module to Python

**No additional build steps needed!**

### Railway vs Local Performance

#### Railway (Cloud)
- **Network Latency**: +10-20ms (Railway → TopStepX API)
- **CPU**: Shared resources, but adequate
- **Uptime**: 24/7, no local machine needed
- **Best for**: Production, 24/7 monitoring

#### Local (Your Machine)
- **Network Latency**: Lower (direct connection)
- **CPU**: Dedicated resources, faster
- **Uptime**: Requires machine to stay on
- **Best for**: Development, testing, debugging

**Recommendation:**
- **Development/Testing**: Use local for faster iteration
- **Production**: Use Railway for 24/7 operation
- **Performance difference**: ~10-20ms network latency (negligible for trading)

## Troubleshooting

### Rust Module Not Loading

```bash
# Rebuild Rust module
cd rust
maturin develop --release

# Verify
python -c "import trading_bot_rust; print('✅ OK')"
```

### Rust Disabled Unexpectedly

Check logs for:
```
⚠️  Rust module not available. Using Python implementation.
```

**Causes:**
- Rust module not built
- Import error
- Missing dependencies

**Fix:**
```bash
cd rust
maturin develop --release
```

### Performance Not Improving

1. **Check if Rust is actually being used:**
   ```bash
   # Look for these log messages:
   🚀 Rust hot path enabled for order execution and queries (optimized)
   ⚡ Rust place_order execution: X.XXms
   ```

2. **If you see Python logs instead:**
   ```
   🐍 Python execution: X.XXms
   ```
   Rust is falling back to Python (check error logs)

3. **Network-bound operations** (orders, queries) will show smaller improvements (5-10%) because network latency dominates

## Next Steps

See `docs/RUST_NEXT_STEPS.md` for:
- Further speed optimizations
- Backtesting simulation engine
- Strategy development tools
- Expected value analysis

