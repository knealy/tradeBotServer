# Optimization Quick Reference Guide

**Quick guide for using the newly optimized trading bot features**

---

## 🚀 Quick Start

### Using Parallel Historical Data Fetching

```python
from trading_bot import TopStepXTradingBot
import asyncio

async def main():
    bot = TopStepXTradingBot()
    await bot.authenticate()
    
    # Fetch multiple symbols in parallel (3x faster)
    requests = [
        {'symbol': 'MNQ', 'timeframe': '1m', 'limit': 100},
        {'symbol': 'MES', 'timeframe': '5m', 'limit': 200},
        {'symbol': 'MGC', 'timeframe': '1d', 'limit': 50}
    ]
    
    results = await bot.get_historical_data_parallel(requests)
    # Returns: {'MNQ_1m': [...], 'MES_5m': [...], 'MGC_1d': [...]}

asyncio.run(main())
```

---

## 📊 Using Overnight Range Parallel Initialization

```python
from strategies.overnight_range_strategy import OvernightRangeStrategy

async def main():
    bot = TopStepXTradingBot()
    await bot.authenticate()
    await bot.switch_account('YOUR_ACCOUNT_ID')
    
    strategy = OvernightRangeStrategy(bot)
    
    # Initialize multiple symbols in parallel (3x faster)
    symbols = ['MNQ', 'MES', 'MGC']
    symbol_data = await strategy.initialize_symbols_parallel(symbols)
    
    # symbol_data contains all pre-fetched historical data
    # Use it to initialize ATR, overnight ranges, etc.

asyncio.run(main())
```

---

## 💾 Using State Cache

```python
# Automatic caching - just use normal methods
orders = await bot.state_cache.get_orders(account_id)  # Cached
positions = await bot.state_cache.get_positions(account_id)  # Cached

# Force refresh if needed
orders = await bot.state_cache.get_orders(account_id, force_refresh=True)

# Check cache metrics
bot.state_cache.log_metrics()
```

---

## 📈 Using SignalR Quote Cache

```python
# In strategies, use the optimized quote method
from strategies.overnight_range_strategy import OvernightRangeStrategy

strategy = OvernightRangeStrategy(bot)

# This checks SignalR cache first (sub-millisecond), falls back to API
quote = await strategy.get_quote_optimized('MNQ')

# Old way (still works, but slower):
# quote = await bot.get_market_quote('MNQ')
```

---

## 🔒 Cooldown Prevention

```python
# In strategies, check cooldown before order placement
from strategies.overnight_range_strategy import OvernightRangeStrategy

strategy = OvernightRangeStrategy(bot)

# Check if cooldown is active
is_cooldown, remaining = strategy._is_cooldown_active('MNQ', 'BUY')
if is_cooldown:
    print(f"Cooldown active: {remaining:.1f}s remaining")
else:
    # Place order
    await strategy._place_single_breakout_order(order_template)
```

---

## 🔄 SignalR Event Handling

```python
# SignalR events automatically invalidate cache
# No manual intervention needed!

# When order update arrives via SignalR:
# 1. trading_bot._on_user_hub_order() is called
# 2. state_cache.invalidate_orders() is called
# 3. Next get_orders() call fetches fresh data
# 4. Subsequent calls use cached data

# Same for positions:
# 1. trading_bot._on_user_hub_position() is called
# 2. state_cache.invalidate_positions() is called
# 3. Cache automatically refreshed
```

---

## 📊 Monitoring Performance

```python
# Check cache performance
bot.state_cache.log_metrics()

# Output:
# 📊 Cache Metrics:
#    Orders: 98.2% hit rate (562 hits, 10 misses)
#    Positions: 97.5% hit rate (478 hits, 12 misses)
#    Daily ATR: 99.8% hit rate (1203 hits, 2 misses)

# Check SignalR connections
print(f"User Hub: {bot.user_hub_manager.is_connected()}")
print(f"Market Hub: {bot._market_hub_connected}")

# Check quote cache
if hasattr(bot, '_quote_cache'):
    print(f"Quote cache size: {len(bot._quote_cache)}")
```

---

## 🧪 Testing Optimizations

```bash
# Test parallel data fetching
python -c "
import asyncio
from trading_bot import TopStepXTradingBot

async def test():
    bot = TopStepXTradingBot()
    await bot.authenticate()
    
    requests = [
        {'symbol': 'MNQ', 'timeframe': '1m', 'limit': 100},
        {'symbol': 'MES', 'timeframe': '1m', 'limit': 100}
    ]
    
    import time
    start = time.time()
    results = await bot.get_historical_data_parallel(requests)
    elapsed = time.time() - start
    
    print(f'Parallel fetch: {len(results)} datasets in {elapsed:.2f}s')

asyncio.run(test())
"

# Test cache performance
python -c "
import asyncio
from trading_bot import TopStepXTradingBot

async def test():
    bot = TopStepXTradingBot()
    await bot.authenticate()
    await bot.switch_account('YOUR_ACCOUNT_ID')
    
    # First call (cache miss)
    import time
    start = time.time()
    orders1 = await bot.state_cache.get_orders(bot.selected_account['id'])
    elapsed1 = time.time() - start
    
    # Second call (cache hit)
    start = time.time()
    orders2 = await bot.state_cache.get_orders(bot.selected_account['id'])
    elapsed2 = time.time() - start
    
    print(f'First call (miss): {elapsed1*1000:.1f}ms')
    print(f'Second call (hit): {elapsed2*1000:.1f}ms')
    print(f'Speedup: {elapsed1/elapsed2:.0f}x')

asyncio.run(test())
"
```

---

## 🎯 Best Practices

### 1. Always Use Parallel Fetching for Multiple Symbols

**❌ Bad (Sequential)**:
```python
for symbol in ['MNQ', 'MES', 'MGC']:
    bars = await bot.get_historical_data(symbol, '1m', limit=100)
    # Takes 3x longer
```

**✅ Good (Parallel)**:
```python
requests = [
    {'symbol': s, 'timeframe': '1m', 'limit': 100}
    for s in ['MNQ', 'MES', 'MGC']
]
results = await bot.get_historical_data_parallel(requests)
# 3x faster
```

### 2. Use Optimized Quote Fetching in Strategies

**❌ Bad (Direct API)**:
```python
quote = await self.trading_bot.get_market_quote(symbol)
# Always hits API (50-100ms)
```

**✅ Good (Cache-Optimized)**:
```python
quote = await self.get_quote_optimized(symbol)
# Uses SignalR cache if available (<1ms)
```

### 3. Check Cooldown Before Order Placement

**❌ Bad (No Check)**:
```python
# Place order directly
await self._place_single_breakout_order(order_template)
# Wastes API calls if in cooldown
```

**✅ Good (Early Exit)**:
```python
is_cooldown, remaining = self._is_cooldown_active(symbol, side)
if is_cooldown:
    logger.debug(f"Skipping order - cooldown active ({remaining:.1f}s)")
    return None
await self._place_single_breakout_order(order_template)
# No wasted API calls
```

### 4. Trust the Cache

**❌ Bad (Force Refresh)**:
```python
# Always forcing refresh defeats the purpose
orders = await bot.state_cache.get_orders(account_id, force_refresh=True)
```

**✅ Good (Use Cache)**:
```python
# Let cache handle it - SignalR events keep it fresh
orders = await bot.state_cache.get_orders(account_id)
```

### 5. Monitor Cache Performance

```python
# Periodically check cache metrics
if hasattr(bot, 'state_cache'):
    bot.state_cache.log_metrics()
    
# Expected hit rates:
# - Orders: >95%
# - Positions: >95%
# - Daily ATR: >99%
# - Quotes: >90%
```

---

## 🔧 Troubleshooting

### Cache Hit Rate Too Low

```python
# Check if SignalR is connected
if bot.user_hub_manager:
    print(f"User Hub connected: {bot.user_hub_manager.is_connected()}")
    
# If not connected, cache won't be invalidated properly
# Restart User Hub:
await bot.user_hub_manager.start(account_id)
```

### Parallel Fetching Not Working

```python
# Verify requests format
requests = [
    {'symbol': 'MNQ', 'timeframe': '1m', 'limit': 100},  # ✅ Correct
    # {'MNQ', '1m', 100},  # ❌ Wrong (not a dict)
]

# Check results
results = await bot.get_historical_data_parallel(requests)
if not results:
    print("No results - check authentication and symbols")
```

### Quote Cache Not Working

```python
# Check if quote cache exists
if not hasattr(bot, '_quote_cache'):
    print("Quote cache not initialized")
    
# Check if Market Hub is connected
if not bot._market_hub_connected:
    print("Market Hub not connected - quotes won't be cached")
    # Reconnect:
    await bot.websocket_manager.connect()
```

---

## 📚 Related Documentation

- [CHANGELOG.md](./CHANGELOG.md) — substantive runtime changes
- [perf/README.md](./perf/README.md) — profiling and baselines
- [StateCache Documentation](../core/state_cache.py) - Cache implementation
- [SignalR_ENDPOINT_USAGE.md](./SignalR_ENDPOINT_USAGE.md) - SignalR guide

---

**Last Updated**: 2026-01-15  
**Version**: 3.0  
**Status**: Production Ready
