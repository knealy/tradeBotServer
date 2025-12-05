# Rust Migration Plan for Trading Bot

## Executive Summary

This document outlines the migration strategy for moving hot paths and critical performance bottlenecks from Python to Rust to achieve sub-millisecond latency and maximum throughput.

## Performance Targets

**Baseline Metrics Established** (December 4, 2025):
- **Order Execution Latency**: < 5ms target (currently ~100-150ms via API)
- **Order Modification Latency**: < 3ms target (currently ~30-50ms)
- **WebSocket Message Processing**: < 1ms target (currently ~5-10ms)
- **Historical Data Aggregation**: 10x faster target (currently ~937ms for 10 bars, ~500ms for 3000 bars)
- **Strategy Execution**: < 10ms target (currently ~50-100ms)
- **Account Balance**: 0.34ms (cached) ✅ - Already optimal
- **Quote Fetching**: 42.80ms (SignalR) - Can improve with Rust
- **Contract Fetching**: 146.33ms - Can improve with Rust

**Test Results** (test_all_commands.py):
- All commands: 100% success rate ✅
- Average time: 485.12ms
- Fastest: 0.34ms (account_balance - cached)
- Slowest: 2250.09ms (depth - includes SignalR fallback)

## Phase 1: Infrastructure & Tooling (Week 1-2)

### 1.1 Setup Rust Project Structure
```
rust/
├── Cargo.toml
├── src/
│   ├── lib.rs
│   ├── order_execution/
│   ├── websocket/
│   ├── market_data/
│   ├── strategy_engine/
│   └── database/
├── tests/
└── benches/
```

### 1.2 Python-Rust Interop
- Use `pyo3` for Python bindings
- Create FFI interface for seamless integration
- Maintain backward compatibility during migration

### 1.3 Performance Benchmarking
- Establish baseline metrics for all hot paths
- Create automated performance tests
- Set up continuous performance monitoring

## Phase 2: Hot Path Migration (Week 3-6)

### Priority 1: Order Execution (Week 3-4)

**Current Implementation**: `trading_bot.py::place_market_order`
**Target**: `rust/src/order_execution/mod.rs`

**Key Functions to Migrate**:
```rust
// rust/src/order_execution/mod.rs
pub struct OrderExecutor {
    api_client: TopStepXClient,
    cache: OrderCache,
}

impl OrderExecutor {
    pub async fn place_market_order(
        &self,
        symbol: &str,
        side: OrderSide,
        quantity: u32,
        account_id: u64,
    ) -> Result<OrderResponse, OrderError> {
        // Optimized order placement with:
        // - Connection pooling
        // - Request batching
        // - Zero-copy serialization
        // - Async I/O
    }
    
    pub async fn modify_order(
        &self,
        order_id: u64,
        price: Option<f64>,
        quantity: Option<u32>,
    ) -> Result<ModifyResponse, OrderError> {
        // Fast order modification
    }
    
    pub async fn cancel_order(
        &self,
        order_id: u64,
    ) -> Result<CancelResponse, OrderError> {
        // Fast order cancellation
    }
}
```

**Expected Performance Gain**: 
- **Network-bound operations** (order execution): 1.05-1.10x (network latency dominates ~90-100ms)
- **CPU-bound operations** (bar aggregation, calculations): 10-20x (Phase 2/3)
- **High-frequency scenarios**: Better connection pooling and lower overhead compound over many requests

### Priority 2: WebSocket Processing (Week 4-5)

**Current Implementation**: `servers/websocket_server.py`
**Target**: `rust/src/websocket/mod.rs`

**Key Functions to Migrate**:
```rust
// rust/src/websocket/mod.rs
use tokio_tungstenite::{connect_async, tungstenite::Message};
use futures_util::{SinkExt, StreamExt};

pub struct WebSocketHandler {
    clients: Arc<RwLock<HashMap<String, ClientConnection>>>,
    message_queue: mpsc::UnboundedSender<MarketUpdate>,
}

impl WebSocketHandler {
    pub async fn handle_message(&self, msg: Message) -> Result<(), WSError> {
        // Zero-copy message parsing
        // Lock-free client broadcasting
        // Batch message processing
    }
    
    pub async fn broadcast_market_update(&self, update: MarketUpdate) {
        // Efficient multi-client broadcasting
        // Non-blocking I/O
    }
}
```

**Expected Performance Gain**: 5-10x faster message processing

### Priority 3: Market Data Aggregation (Week 5-6)

**Current Implementation**: `trading_bot.py::get_historical_data` (aggregation)
**Target**: `rust/src/market_data/aggregation.rs`

**Key Functions to Migrate**:
```rust
// rust/src/market_data/aggregation.rs
pub fn aggregate_bars(
    source_bars: &[Bar],
    target_timeframe: Timeframe,
) -> Vec<Bar> {
    // SIMD-optimized aggregation
    // Parallel processing
    // Zero-allocation where possible
}

pub struct BarAggregator {
    cache: LruCache<String, Vec<Bar>>,
}

impl BarAggregator {
    pub fn aggregate_1m_to_5m(&self, bars: &[Bar]) -> Vec<Bar> {
        // Optimized 1m -> 5m aggregation
        // Uses SIMD for price calculations
    }
}
```

**Expected Performance Gain**: 10x faster aggregation (50ms vs 500ms for 3000 bars)

## Phase 3: Strategy Engine (Week 7-8)

### Strategy Execution Engine

**Current Implementation**: Python strategy classes
**Target**: `rust/src/strategy_engine/mod.rs`

**Architecture**:
```rust
// rust/src/strategy_engine/mod.rs
pub trait Strategy: Send + Sync {
    fn name(&self) -> &str;
    fn should_execute(&self, market_data: &MarketData) -> bool;
    fn generate_signals(&self, market_data: &MarketData) -> Vec<Signal>;
    fn risk_check(&self, signal: &Signal) -> bool;
}

pub struct StrategyEngine {
    strategies: Vec<Box<dyn Strategy>>,
    executor: Arc<OrderExecutor>,
}

impl StrategyEngine {
    pub async fn execute_strategies(&self, market_data: MarketData) {
        // Parallel strategy execution
        // Lock-free signal generation
        // Fast risk checks
    }
}
```

**Migration Strategy**:
1. Port `overnight_range_strategy.py` first (most critical)
2. Create Rust trait for strategy interface
3. Maintain Python compatibility via FFI
4. Gradually migrate other strategies

## Phase 4: Database Operations (Week 9-10)

### High-Performance Database Layer

**Current Implementation**: `infrastructure/database.py`
**Target**: `rust/src/database/mod.rs`

**Key Optimizations**:
```rust
// rust/src/database/mod.rs
use sqlx::PgPool;
use tokio::sync::RwLock;

pub struct Database {
    pool: PgPool,
    query_cache: Arc<RwLock<LruCache<String, CachedResult>>>,
}

impl Database {
    pub async fn cache_historical_bars(
        &self,
        symbol: &str,
        timeframe: &str,
        bars: &[Bar],
    ) -> Result<(), DbError> {
        // Batch inserts
        // Prepared statements
        // Connection pooling
    }
    
    pub async fn get_cached_bars(
        &self,
        symbol: &str,
        timeframe: &str,
        start: DateTime<Utc>,
        end: DateTime<Utc>,
    ) -> Result<Vec<Bar>, DbError> {
        // Efficient query with indexes
        // Result caching
    }
}
```

## Phase 5: Integration & Testing (Week 11-12)

### 5.1 Python Bindings
```python
# python/rust_bindings.py
import rust_trading_bot

class FastOrderExecutor:
    def __init__(self):
        self._executor = rust_trading_bot.OrderExecutor()
    
    async def place_market_order(self, symbol, side, quantity, account_id):
        return await self._executor.place_market_order(
            symbol, side, quantity, account_id
        )
```

### 5.2 Gradual Rollout
1. Run Rust and Python implementations in parallel
2. Compare results for correctness
3. Monitor performance metrics
4. Gradually shift traffic to Rust implementation
5. Keep Python as fallback

### 5.3 Performance Monitoring
- Add timing instrumentation to all Rust functions
- Compare before/after metrics
- Identify remaining bottlenecks
- Iterate on optimizations

## Technical Implementation Details

### Memory Management
- Use `Arc` for shared ownership
- `RwLock` for concurrent reads
- Zero-copy where possible
- Custom allocators for hot paths

### Async I/O
- Use `tokio` for async runtime
- Connection pooling for HTTP/WebSocket
- Batch operations where possible
- Non-blocking I/O throughout

### Error Handling
- Use `Result<T, E>` for all fallible operations
- Custom error types for each domain
- Error propagation without panics
- Comprehensive error logging

### Testing Strategy
- Unit tests for all functions
- Integration tests with mock TopStepX API
- Performance benchmarks
- Fuzz testing for edge cases

## Migration Checklist

### Pre-Migration
- [x] Establish performance baselines ✅ (December 4, 2025)
- [x] Create comprehensive test suite ✅ (test_all_commands.py)
- [x] Remove hardcoded dependencies ✅ (dynamic contracts)
- [x] Fix critical bugs ✅ (datetime, timezone, SignalR)
- [x] Set up Rust development environment ✅ (Cargo.toml, dependencies)
- [x] Create project structure ✅ (rust/ directory with all modules)
- [ ] Set up CI/CD for Rust
- [x] Code refactoring (split trading_bot.py) ✅ (Complete modular architecture)

### Phase 1: Order Execution ✅ **COMPLETE** (December 5, 2025)
- [x] Implement TopStepX API client in Rust ✅ (reqwest with connection pooling)
- [x] Port `place_market_order` ✅ (100% complete with bracket orders)
- [x] Port `modify_order` ✅ (100% complete)
- [x] Port `cancel_order` ✅ (100% complete)
- [x] Create Python bindings ✅ (Async support with pyo3-asyncio)
- [x] Fix PyO3 async return type conversion ✅ (RESOLVED - lifetime-bound return types)
- [x] Add retry logic for 500 errors ✅ (Exponential backoff: 750ms, 1500ms, 3000ms)
- [x] Integration tests ✅ (tests/order_execution_test.rs)
- [x] Performance benchmarks ✅ (benches/order_execution_bench.rs)
- [x] Python adapter smoke test ✅ (`tests/test_rust_integration.py` – confirms `RUST_AVAILABLE=True` and wiring into `TopStepXAdapter`, including real-auth smoke test)
- [x] Benchmark harness ✅ (`tests/bench_rust_vs_python_orders.py` – compares `place_market_order` for Rust vs Python against a practice/sandbox account)
- [x] Live performance testing ✅ (Tested against practice account: 1.05-1.10x speedup for network-bound operations)
- [x] Integration complete ✅ (TopStepXAdapter hot path routing, environment variable control, staging toggle)
- [ ] Deploy to staging ⏳ (Next step - ready for deployment)

### Phase 2: WebSocket
- [ ] Port WebSocket client
- [ ] Port message parsing
- [ ] Port broadcasting logic
- [ ] Create Python bindings
- [ ] Integration tests
- [ ] Performance benchmarks
- [ ] Deploy to staging

### Phase 3: Market Data
- [ ] Port aggregation logic
- [ ] Optimize with SIMD
- [ ] Port caching logic
- [ ] Create Python bindings
- [ ] Integration tests
- [ ] Performance benchmarks
- [ ] Deploy to staging

### Phase 4: Strategy Engine
- [ ] Create strategy trait
- [ ] Port overnight_range_strategy
- [ ] Port other strategies
- [ ] Create Python bindings
- [ ] Integration tests
- [ ] Performance benchmarks
- [ ] Deploy to staging

### Phase 5: Database
- [ ] Port database operations
- [ ] Optimize queries
- [ ] Implement connection pooling
- [ ] Create Python bindings
- [ ] Integration tests
- [ ] Performance benchmarks
- [ ] Deploy to staging

## Risk Mitigation

### Risks
1. **Integration Issues**: Rust-Python interop complexity
2. **Correctness**: Different behavior between implementations
3. **Performance**: Rust might not meet targets initially
4. **Maintenance**: Two codebases to maintain

### Mitigation Strategies
1. Comprehensive testing at each phase
2. Parallel execution with comparison
3. Gradual rollout with monitoring
4. Clear documentation and code organization

## Success Metrics

- [ ] Order execution latency < 5ms (p95)
- [ ] Order modification latency < 3ms (p95)
- [ ] WebSocket message processing < 1ms (p95)
- [ ] Historical data aggregation 10x faster
- [ ] Strategy execution < 10ms per check
- [ ] Zero correctness regressions
- [ ] 99.9% uptime during migration

## Timeline Summary

- **Week 1-2**: Infrastructure setup
- **Week 3-4**: Order execution migration
- **Week 4-5**: WebSocket migration
- **Week 5-6**: Market data aggregation
- **Week 7-8**: Strategy engine
- **Week 9-10**: Database operations
- **Week 11-12**: Integration & testing

**Total Estimated Time**: 12 weeks

## Next Steps

1. Review and approve migration plan
2. Set up Rust development environment
3. Create initial project structure
4. Begin Phase 1: Order execution migration

