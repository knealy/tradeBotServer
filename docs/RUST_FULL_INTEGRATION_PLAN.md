# Rust Full Integration Plan

**Date**: December 5, 2025  
**Goal**: Wire ALL adapter methods to Rust hot paths for maximum performance

## Current Status

### ✅ Already Wired to Rust
- `place_market_order` ✅
- `modify_order` ✅  
- `cancel_order` ✅
- `get_historical_data` (aggregation) ✅

### ⏳ Needs Rust Implementation

#### Order Methods
- `place_limit_order` - Not implemented (stub)
- `place_stop_order` - Not implemented (stub)
- `place_oco_bracket_with_stop_entry` - Python only
- `place_trailing_stop_order` - Python only

#### Order Query Methods
- `get_open_orders` - Python only
- `get_order_history` - Python only

#### Position Methods
- `get_positions` - Python only
- `get_open_positions` - Python only (alias)
- `get_position_details` - Python only
- `close_position` - Python only
- `flatten_all_positions` - Python only

#### Market Data Query Methods
- `get_market_quote` - Python only
- `get_market_depth` - Python only
- `get_available_contracts` - Python only

## Implementation Strategy

### Phase 1: Order Methods (High Priority)
All order methods use the same `/api/Order/place` endpoint with different `type` values:
- Market: `type: 2`
- Limit: `type: 1`
- Stop: `type: 4`
- Stop-Limit: `type: 3`

**Rust Implementation**:
- Extend `place_market_order_internal` to support all order types
- Add `order_type` parameter (already exists!)
- Add `stop_price` parameter for stop orders
- Reuse existing retry logic and error handling

### Phase 2: Query Methods (Medium Priority)
These are read-only operations that benefit from:
- Better connection pooling
- Faster JSON parsing
- Lower memory overhead

**Rust Implementation**:
- Create `QueryExecutor` struct (similar to `OrderExecutor`)
- Implement `/api/Order/search` endpoint
- Implement `/api/Position/searchOpen` endpoint
- Implement `/api/MarketData/quote` endpoint
- Implement `/api/MarketData/depth` endpoint
- Implement `/api/Contract/search` endpoint

### Phase 3: Position Management (Medium Priority)
- `close_position` - Uses `/api/Position/close`
- `flatten_all_positions` - Batch close operation

## Performance Expectations

### Network-Bound Operations
- **Current**: ~90-100ms (API round-trip)
- **Rust**: ~85-95ms (1.05-1.10x speedup)
- **Benefit**: Better connection pooling, lower overhead

### CPU-Bound Operations (Aggregation)
- **Current**: ~1-3ms for 1000 bars
- **Rust (optimized)**: ~0.1-0.3ms (10x speedup with parallel processing)
- **Benefit**: SIMD, parallel processing, optimized loops

## Next Steps

1. ✅ Fix compilation issues (remove packed_simd_2 dependency)
2. ⏳ Extend Rust OrderExecutor to support all order types
3. ⏳ Create Rust QueryExecutor for read operations
4. ⏳ Wire all methods in TopStepXAdapter
5. ⏳ Performance benchmarks
6. ⏳ Integration tests

