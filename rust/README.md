# Trading Bot Rust Module

High-performance Rust implementation of trading bot hot paths with Python bindings via PyO3.

## Status

**Phase 1: Order Execution** - ✅ **COMPLETE**

### Completed Features

1. **Order Execution Module** (`src/order_execution/mod.rs`)
   - ✅ `OrderExecutor` struct with HTTP client and connection pooling
   - ✅ Token management (`set_token`, `get_token`)
   - ✅ Contract ID caching (`set_contract_id`, `get_contract_id`)
   - ✅ `place_market_order` - Full async implementation with:
     - Input validation
     - Order type handling (market/limit)
     - Bracket orders (stop loss/take profit)
     - Custom tags
     - Error handling
     - API response parsing
   - ✅ `modify_order` - Full implementation
   - ✅ `cancel_order` - Full implementation
   - ✅ **Retry logic with exponential backoff** for 500 errors (750ms, 1500ms, 3000ms)

2. **Python Bindings**
   - ✅ PyO3 async support with `pyo3-asyncio`
   - ✅ Proper lifetime management (`&'a PyAny` return types)
   - ✅ Python-callable async methods

3. **Infrastructure**
   - ✅ HTTP client with `reqwest` and connection pooling
   - ✅ Async runtime with `tokio`
   - ✅ Error handling with `thiserror` and `anyhow`
   - ✅ Logging with `tracing`
   - ✅ Serialization with `serde`

## Building

```bash
# Development build
cargo check

# Release build (optimized)
cargo build --release

# The Python module will be at: target/release/libtrading_bot_rust.dylib (macOS)
```

## Usage from Python

```python
import trading_bot_rust

# Initialize executor
executor = trading_bot_rust.OrderExecutor(base_url="https://api.topstepx.com")

# Set authentication token
executor.set_token("your_jwt_token_here")

# Cache contract IDs
executor.set_contract_id("MNQ", 12345)

# Place order (async)
result = await executor.place_market_order(
    symbol="MNQ",
    side="BUY",
    quantity=1,
    account_id=12345678,
    stop_loss_ticks=50,
    take_profit_ticks=100,
    limit_price=None,
    order_type="market",
    custom_tag="my_strategy"
)

# Modify order
result = await executor.modify_order(
    order_id="order_123",
    price=25650.0,
    quantity=2
)

# Cancel order
result = await executor.cancel_order(order_id="order_123")
```

## Performance Targets

Based on `docs/RUST_MIGRATION_PLAN.md`:

- **Order Execution**: < 5ms (p95) - Target 10-20x faster than Python
- **Order Modification**: < 3ms (p95)
- **Order Cancellation**: < 3ms (p95)

## Architecture

### Order Execution Flow

1. **Python calls async method** → PyO3 bindings
2. **Rust async function** → `pyo3_asyncio::tokio::future_into_py`
3. **Internal async executor** → HTTP request with `reqwest`
4. **Retry logic** → Exponential backoff on 500 errors
5. **Response parsing** → JSON to `OrderResponse`
6. **Python dict conversion** → Return to Python

### Key Design Decisions

1. **Lifetime-bound return types** (`&'a PyAny`):
   - Resolved PyO3 async compilation issues
   - Proper GIL management
   - Zero-copy where possible

2. **Retry logic with exponential backoff**:
   - Handles 500 errors automatically
   - 3 retries max (750ms, 1500ms, 3000ms delays)
   - Prevents token expiration issues

3. **Connection pooling**:
   - `Arc<Client>` for shared HTTP client
   - Persistent connections
   - Reduced latency

4. **Token and contract caching**:
   - `Arc<RwLock<>>` for thread-safe access
   - Minimal lock contention
   - Fast lookups

## Testing

```bash
# Unit tests (requires Python runtime)
cargo test --lib

# Integration tests
cargo test --test order_execution_test

# Benchmarks
cargo bench
```

**Note**: Tests and benchmarks require Python symbols to link. For production use, build the `cdylib` target.

## Next Steps

1. **Integration with Python trading bot**:
   - Import Rust module in `brokers/topstepx_adapter.py`
   - Use for hot path order execution
   - Benchmark against Python implementation

2. **Phase 2: Market Data Aggregation**:
   - SIMD-optimized bar aggregation
   - Timeframe conversion
   - Performance benchmarks

3. **Phase 3: WebSocket Processing**:
   - SignalR client in Rust
   - Real-time market data
   - Low-latency message processing

## Dependencies

- `pyo3` 0.20 - Python bindings
- `pyo3-asyncio` 0.20 - Async support
- `tokio` 1.35 - Async runtime
- `reqwest` 0.11 - HTTP client
- `serde` / `serde_json` - Serialization
- `thiserror` / `anyhow` - Error handling
- `tracing` - Logging
- `chrono` - Time handling

## Compilation Notes

### Resolved Issues

1. **PyO3 async return types**:
   - Issue: `pyo3_asyncio::tokio::future_into_py` expects `PyResult<&'a PyAny>`
   - Solution: Use lifetime-bound return types instead of `Py<PyAny>`

2. **Python symbol linking**:
   - Tests/benchmarks require Python runtime
   - Production builds use `cdylib` only

### Build Configuration

```toml
[profile.release]
opt-level = 3          # Maximum optimization
lto = true             # Link-time optimization
codegen-units = 1      # Single codegen unit for better optimization
```

## License

Same as parent project.
