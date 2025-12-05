# Rust Module Integration Guide

**Date**: December 5, 2025  
**Status**: Ready for Integration

---

## Quick Start

### 1. Build the Rust Module

```bash
cd /Users/knealy/tradeBotServer/rust
cargo build --release

# Copy the built library to Python import path
cp target/release/libtrading_bot_rust.dylib ../trading_bot_rust.so
```

### 2. Test Import in Python

```python
# Test basic import
import trading_bot_rust

# Create executor
executor = trading_bot_rust.OrderExecutor(base_url="https://api.topstepx.com")
print(f"Executor created: {executor.get_base_url()}")
```

### 3. Integration with TopStepXAdapter

Add Rust executor as an optional fast path in `brokers/topstepx_adapter.py`:

```python
class TopStepXAdapter:
    def __init__(self, base_url, auth_manager, ...):
        self.base_url = base_url
        self.auth = auth_manager
        
        # Try to import Rust module for hot paths
        try:
            import trading_bot_rust
            self._rust_executor = trading_bot_rust.OrderExecutor(base_url=base_url)
            self._use_rust = True
            logger.info("✅ Rust order execution module loaded")
        except ImportError:
            self._rust_executor = None
            self._use_rust = False
            logger.info("⚠️ Rust module not available, using Python implementation")
    
    async def place_market_order(
        self,
        symbol: str,
        side: str,
        quantity: int,
        account_id: int,
        stop_loss_ticks: Optional[int] = None,
        take_profit_ticks: Optional[int] = None,
        limit_price: Optional[float] = None,
        order_type: str = "market",
        custom_tag: Optional[str] = None
    ) -> Dict:
        """Place market order - uses Rust if available, falls back to Python"""
        
        if self._use_rust and self._rust_executor:
            # Use Rust hot path
            try:
                # Update token and contract cache
                token = self.auth.get_token()
                self._rust_executor.set_token(token)
                
                contract_id = await self._get_contract_id(symbol)
                self._rust_executor.set_contract_id(symbol, contract_id)
                
                # Call Rust async method
                result = await self._rust_executor.place_market_order(
                    symbol=symbol,
                    side=side,
                    quantity=quantity,
                    account_id=account_id,
                    stop_loss_ticks=stop_loss_ticks,
                    take_profit_ticks=take_profit_ticks,
                    limit_price=limit_price,
                    order_type=order_type,
                    custom_tag=custom_tag
                )
                
                logger.info(f"🚀 Rust order execution: {result.get('order_id')}")
                return result
                
            except Exception as e:
                logger.warning(f"Rust execution failed, falling back to Python: {e}")
                # Fall through to Python implementation
        
        # Python implementation (existing code)
        return await self._place_market_order_python(
            symbol, side, quantity, account_id,
            stop_loss_ticks, take_profit_ticks,
            limit_price, order_type, custom_tag
        )
    
    async def _place_market_order_python(self, ...):
        """Original Python implementation (renamed)"""
        # ... existing implementation ...
```

---

## Performance Monitoring

### Add Timing Instrumentation

```python
import time

async def place_market_order(self, ...):
    start_time = time.perf_counter()
    
    if self._use_rust:
        result = await self._rust_executor.place_market_order(...)
        elapsed_ms = (time.perf_counter() - start_time) * 1000
        logger.info(f"⚡ Rust execution: {elapsed_ms:.2f}ms")
    else:
        result = await self._place_market_order_python(...)
        elapsed_ms = (time.perf_counter() - start_time) * 1000
        logger.info(f"🐍 Python execution: {elapsed_ms:.2f}ms")
    
    return result
```

### Performance Metrics to Track

1. **Order Execution Latency**:
   - p50, p95, p99 latencies
   - Rust vs Python comparison
   - Target: < 5ms (p95)

2. **Throughput**:
   - Orders per second
   - Concurrent order handling
   - Connection pool efficiency

3. **Error Rates**:
   - 500 error retry success rate
   - Fallback to Python frequency
   - Overall success rate

---

## Testing Strategy

### 1. Unit Tests

Test Rust module independently:

```python
import pytest
import trading_bot_rust

@pytest.mark.asyncio
async def test_rust_order_executor():
    executor = trading_bot_rust.OrderExecutor(base_url="https://api.topstepx.com")
    
    # Test token management
    executor.set_token("test_token")
    assert executor.get_token() == "test_token"
    
    # Test contract caching
    executor.set_contract_id("MNQ", 12345)
    assert executor.get_contract_id("MNQ") == 12345
```

### 2. Integration Tests

Test with TopStepXAdapter:

```python
@pytest.mark.asyncio
async def test_rust_integration_with_adapter():
    adapter = TopStepXAdapter(...)
    
    # Verify Rust module loaded
    assert adapter._use_rust is True
    
    # Place order
    result = await adapter.place_market_order(
        symbol="MNQ",
        side="BUY",
        quantity=1,
        account_id=12345678
    )
    
    assert result['success'] is True
    assert 'order_id' in result
```

### 3. Performance Benchmarks

Compare Rust vs Python:

```python
import asyncio
import time

async def benchmark_order_execution():
    adapter = TopStepXAdapter(...)
    
    # Warm up
    for _ in range(10):
        await adapter.place_market_order(...)
    
    # Benchmark Rust
    adapter._use_rust = True
    rust_times = []
    for _ in range(100):
        start = time.perf_counter()
        await adapter.place_market_order(...)
        rust_times.append((time.perf_counter() - start) * 1000)
    
    # Benchmark Python
    adapter._use_rust = False
    python_times = []
    for _ in range(100):
        start = time.perf_counter()
        await adapter.place_market_order(...)
        python_times.append((time.perf_counter() - start) * 1000)
    
    print(f"Rust p95: {sorted(rust_times)[95]:.2f}ms")
    print(f"Python p95: {sorted(python_times)[95]:.2f}ms")
    print(f"Improvement: {sorted(python_times)[95] / sorted(rust_times)[95]:.1f}x")
```

---

## Deployment

### Development Environment

```bash
# Build Rust module
cd rust
cargo build --release
cp target/release/libtrading_bot_rust.dylib ../trading_bot_rust.so

# Test import
python3 -c "import trading_bot_rust; print('✅ Rust module loaded')"
```

### Production Environment (Railway)

Add to `build.sh`:

```bash
#!/bin/bash

# Install Rust
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
source $HOME/.cargo/env

# Build Rust module
cd rust
cargo build --release
cp target/release/libtrading_bot_rust.so ../trading_bot_rust.so
cd ..

# Continue with Python setup
pip install -r requirements.txt
```

Add to `requirements.txt`:
```
# No additional Python dependencies needed for Rust module
```

---

## Troubleshooting

### Issue: Import Error

**Problem**: `ImportError: No module named 'trading_bot_rust'`

**Solution**:
```bash
# Verify library exists
ls -lh trading_bot_rust.so

# Check Python can find it
python3 -c "import sys; print(sys.path)"

# Copy to site-packages if needed
cp trading_bot_rust.so $(python3 -c "import site; print(site.getsitepackages()[0])")
```

### Issue: Symbol Not Found

**Problem**: `Symbol not found: _PyExc_TypeError`

**Solution**:
```bash
# Rebuild with correct Python version
cd rust
cargo clean
cargo build --release
```

### Issue: Async Not Working

**Problem**: `RuntimeError: no running event loop`

**Solution**:
```python
# Ensure running in async context
import asyncio

async def main():
    result = await executor.place_market_order(...)
    print(result)

asyncio.run(main())
```

---

## Monitoring & Observability

### Add Metrics Collection

```python
from prometheus_client import Counter, Histogram

rust_order_latency = Histogram(
    'rust_order_execution_latency_seconds',
    'Rust order execution latency'
)

rust_order_total = Counter(
    'rust_order_execution_total',
    'Total Rust order executions',
    ['status']
)

@rust_order_latency.time()
async def place_market_order(self, ...):
    try:
        result = await self._rust_executor.place_market_order(...)
        rust_order_total.labels(status='success').inc()
        return result
    except Exception as e:
        rust_order_total.labels(status='error').inc()
        raise
```

### Logging

```python
import logging

logger = logging.getLogger(__name__)

# Log Rust usage
logger.info(f"🚀 Using Rust executor: {self._use_rust}")

# Log performance
logger.info(f"⚡ Order execution: {elapsed_ms:.2f}ms (Rust)")
logger.info(f"🐍 Order execution: {elapsed_ms:.2f}ms (Python)")
```

---

## Rollback Plan

If issues arise, disable Rust and use Python:

```python
class TopStepXAdapter:
    def __init__(self, ...):
        # Force Python implementation
        self._use_rust = False  # Set to False to disable Rust
        
        # Or use environment variable
        import os
        self._use_rust = os.getenv('USE_RUST_EXECUTOR', 'true').lower() == 'true'
```

---

## Next Steps

1. ✅ Build Rust module
2. ✅ Test import
3. ⏳ Integrate with TopStepXAdapter
4. ⏳ Run performance benchmarks
5. ⏳ Deploy to staging
6. ⏳ Monitor performance
7. ⏳ Deploy to production

---

**Status**: Ready for integration! 🚀

