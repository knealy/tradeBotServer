# Rust vs Python Execution Paths Reference

**Last Updated**: December 29, 2025  
**Purpose**: Document which operations use Rust vs Python execution for system maintenance

---

## Overview

The trading bot uses a hybrid architecture where performance-critical operations can execute via Rust (high-performance) or Python (fallback). This document maps each operation to its execution path.

---

## Execution Path Legend

- ✅ **Rust Primary**: Attempts Rust first, falls back to Python on error
- 🐍 **Python Only**: Uses Python implementation only
- ⚠️ **Rust Disabled**: Rust implementation exists but currently disabled
- 🚧 **Not Implemented**: Planned but not yet implemented

---

## 1. Order Operations

### 1.1 Place Market Order
**Status**: ✅ Rust Primary  
**Location**: `brokers/topstepx_adapter.py::place_market_order()`  
**Rust Implementation**: `rust/src/order_execution/mod.rs::place_market_order_async()`

**Flow**:
1. Check if `_use_rust` is enabled
2. Call `_place_market_order_rust()` → Rust executor
3. On error, fallback to `_place_market_order_python()`

**Key Details**:
- Token refresh happens before BOTH Rust and Python execution
- Rust uses `reqwest` HTTP client with connection pooling
- Python uses `requests` with retry logic
- Speedup: ~1.05-1.10x (network-bound operation)

---

### 1.2 Modify Order
**Status**: ✅ Rust Primary  
**Location**: `brokers/topstepx_adapter.py::modify_order()`  
**Rust Implementation**: `rust/src/order_execution/mod.rs::modify_order_async()`

**Flow**:
1. Check if `_use_rust` is enabled
2. Call `_modify_order_rust()` → Rust executor
3. On error, fallback to `_modify_order_python()`

**Key Details**:
- Supports price and quantity modifications
- Rust implementation handles JSON serialization efficiently
- Token passed directly to Rust executor via `set_token()`

---

### 1.3 Cancel Order
**Status**: ⚠️ Rust Disabled (PyO3 parameter issue)  
**Location**: `brokers/topstepx_adapter.py::cancel_order()`  
**Rust Implementation**: `rust/src/order_execution/mod.rs::cancel_order_async()` (disabled)

**Flow**:
1. Rust disabled due to PyO3 0.20 parameter passing bug
2. Always uses `_cancel_order_python()`
3. Error logged: "Rust cancel_order disabled - using Python implementation"

**Known Issues**:
- PyO3 0.20 with abi3-py38 doesn't properly expose `account_id` parameter
- Rust code is correct but Python binding fails
- **Workaround**: Using Python fallback (43-47ms execution time)

---

## 2. Position Operations

### 2.1 Get Positions
**Status**: ✅ Rust Primary  
**Location**: `brokers/topstepx_adapter.py::get_positions()`  
**Rust Implementation**: `rust/src/query/mod.rs::get_positions_async()`

**Flow**:
1. Check if `_use_rust` and `_query_executor` available
2. Call Rust `get_positions()` → Query executor
3. On error, fallback to Python implementation

**Key Details**:
- Returns list of `Position` objects
- Rust deserializes JSON to Position struct efficiently
- Python converts dict responses to Position objects

---

### 2.2 Close Position
**Status**: ⚠️ Rust Disabled (API endpoint issues)  
**Location**: `brokers/topstepx_adapter.py::close_position()`  
**Rust Implementation**: `rust/src/query/mod.rs::close_position_async()` (disabled)

**Flow**:
1. Rust disabled due to unreliable position closing
2. Always uses `_close_position_python()`
3. Python uses `/api/Position/closeContract` endpoint

**Known Issues**:
- Rust was reporting success but positions remained open
- API returns `{"success": True}` even when position not fully closed
- **Current Fix**: Disabled Rust, using Python with verification step
- Python implementation verifies position is actually closed after API call

**API Details**:
- Endpoint: `POST /api/Position/closeContract`
- Requires: `contractId` (not `positionId`) and `accountId`
- First fetches position to get `contractId`, then closes

---

### 2.3 Flatten All Positions
**Status**: 🐍 Python Only  
**Location**: `brokers/topstepx_adapter.py::flatten_all_positions()`

**Flow**:
1. Fetch all open positions via `get_positions()` (Rust)
2. For each position, call `close_position()` (Python)
3. Fetch all open orders via `get_open_orders()` (Rust)
4. For each order, call `cancel_order()` (Python)
5. Verify positions are actually closed (re-fetch and check)

**Key Details**:
- Orchestrates multiple operations
- Uses Rust for queries (get positions/orders)
- Uses Python for mutations (close/cancel)
- Includes verification step to catch API lies

---

## 3. Market Data Operations

### 3.1 Get Historical Data
**Status**: 🐍 Python Only  
**Location**: `brokers/topstepx_adapter.py::get_historical_data()`  
**Rust Implementation**: 🚧 Not Implemented (REST API returns 404)

**Flow**:
1. Check cache for recent data (5-second TTL)
2. If cached, return immediately
3. Otherwise, call Python implementation
4. Python fetches bars via `/api/Contract/{contractId}/bars`

**Key Details**:
- Rust REST API endpoint (`/api/bars/historical`) returns 404
- Python implementation works reliably
- Includes sophisticated caching (5s TTL)
- Handles date range mode vs bar count mode
- Market hours logic adjusts times to valid trading periods

---

### 3.2 Get Market Quote
**Status**: ✅ Rust Primary  
**Location**: `brokers/topstepx_adapter.py::get_market_quote()`  
**Rust Implementation**: `rust/src/query/mod.rs::get_market_quote_async()`

**Flow**:
1. Check if `_use_rust` and `_query_executor` available
2. Call Rust `get_market_quote()` → Query executor
3. On error, fallback to Python implementation

**Key Details**:
- Returns current bid/ask/last prices
- Rust uses `/api/Contract/{contractId}/quote`
- Low latency operation (<10ms)

---

### 3.3 Get Market Depth
**Status**: ✅ Rust Primary  
**Location**: `brokers/topstepx_adapter.py::get_market_depth()`  
**Rust Implementation**: `rust/src/query/mod.rs::get_market_depth_async()`

**Flow**:
1. Check if `_use_rust` and `_query_executor` available
2. Call Rust `get_market_depth()` → Query executor
3. On error, fallback to Python implementation

**Key Details**:
- Returns order book (bids/asks at each price level)
- Rust uses `/api/Contract/{contractId}/depth`
- Returns `Depth` object with `DepthLevel` lists

---

### 3.4 Get Available Contracts
**Status**: ✅ Rust Primary (with caching)  
**Location**: `brokers/topstepx_adapter.py::get_available_contracts()`  
**Rust Implementation**: `rust/src/query/mod.rs::get_available_contracts_async()`

**Flow**:
1. Check Python cache (60-minute TTL by default)
2. If cached, return immediately
3. Otherwise, try Rust `get_available_contracts()`
4. On error, fallback to Python implementation
5. Cache result for future requests

**Key Details**:
- Contracts change infrequently, so long cache TTL
- Rust returns all available contracts for account
- Python implementation includes caching layer

---

## 4. Order Workflow Operations

### 4.1 Place Bracket Order
**Status**: 🐍 Python Only  
**Location**: `brokers/topstepx_adapter.py::place_oco_bracket_with_stop_entry()`

**Flow**:
1. Uses Python implementation only
2. Constructs bracket order JSON payload
3. Calls `POST /api/Order/place` with bracket structure
4. Validates response and extracts order ID

**Why Python Only**:
- Complex JSON structure with nested bracket objects
- TopStepX "Position Brackets" mode requires specific payload format
- Rust implementation would need extensive testing
- Python implementation is proven and reliable

**Key Details**:
- Entry order with `stopLossBracket` and `takeProfitBracket` objects
- Uses tick-based offsets (not price-based)
- Requires "Auto OCO Brackets" enabled in TopStepX settings
- Includes token refresh and retry logic for 500 errors

---

### 4.2 Place Trailing Stop
**Status**: 🐍 Python Only  
**Location**: `brokers/topstepx_adapter.py::place_trailing_stop_order()`

**Flow**:
1. Uses Python implementation only
2. Constructs trailing stop order JSON
3. Calls `POST /api/Order/place` with type 5 (TrailingStop)

**Key Details**:
- OrderType 5 = TrailingStop
- Requires `trailAmount` parameter
- Not yet implemented in Rust

---

## 5. Authentication & Token Management

### 5.1 Authentication
**Status**: 🐍 Python Only  
**Location**: `core/auth.py::AuthManager`

**Flow**:
1. Called by `ensure_valid_token()` when token expired/missing
2. POST to `/api/Auth/loginKey` with username and API key
3. Receives JWT token and parses expiration
4. Stores token in `self.session_token`

**Key Details**:
- Tokens expire after ~29 hours (set to refresh 5min before expiry)
- JWT parsed to extract `exp` claim for expiration time
- Token passed to Rust executors via `set_token()` method
- All API calls check token validity before execution

---

### 5.2 Token Validation
**Status**: 🐍 Python Only  
**Location**: `core/auth.py::ensure_valid_token()`

**Flow**:
```python
async def ensure_valid_token(self, force_refresh: bool = False) -> bool:
    if not force_refresh and not self._is_token_expired():
        return True  # ✅ Fast path - no authentication needed
    
    logger.info("Token expired or missing, authenticating...")
    return await self.authenticate()  # Re-authenticate
```

**Key Details**:
- Called before EVERY operation (orders, positions, market data)
- **99% of calls return immediately** (token still valid)
- Only re-authenticates when token expires (every ~29 hours)
- 5-minute buffer before expiration for safety
- **NOT causing performance issues** - just a boolean check

---

## 6. Contract Management

### 6.1 Contract ID Resolution
**Status**: 🐍 Python Only  
**Location**: `core/market_data.py::ContractManager`

**Flow**:
1. Symbol (e.g., "MNQ") → Contract ID (e.g., "CON.F.US.MNQ.H26")
2. Caches contract mappings in memory
3. Fetches from API if not cached
4. Selects most recent/active contract by volume

**Key Details**:
- Required for all API calls (API uses contract IDs, not symbols)
- Caching prevents repeated API calls
- Automatically selects active contract month
- Rust executors receive contract IDs directly (no resolution needed)

---

## 7. Performance Metrics Summary

### Network-Bound Operations (Rust ~1.05-1.10x faster)
- Place Market Order: Rust 88ms vs Python 93ms
- Modify Order: Rust 85ms vs Python 91ms
- Get Positions: Rust 35ms vs Python 38ms
- Get Market Quote: Rust 8ms vs Python 9ms

### Python Only (No Rust Alternative)
- Place Bracket Order: ~100-150ms (complex JSON)
- Get Historical Data: ~200-500ms (bar aggregation)
- Flatten All: ~500ms-2s (orchestrates multiple operations)
- Token Validation: <1ms (boolean check only)

### Disabled Operations (Rust exists but disabled)
- Cancel Order: Python fallback ~43-47ms (PyO3 bug)
- Close Position: Python fallback ~60ms (API reliability issues)

---

## 8. Token Flow Diagram

```
Initialization (once):
├── Load JWT from environment (if available)
├── Parse expiration time
└── If expired/missing → authenticate() → get new token

Every Operation (before execution):
├── ensure_valid_token() called
├── Check if token expired (datetime comparison)
│   ├── NOT expired → return True (< 1ms)
│   └── EXPIRED → authenticate() → get new token
└── Proceed with operation

Rust Executor:
├── Receives token via set_token() at initialization
├── Uses token for all HTTP requests
└── Token updated when Python refreshes it
```

---

## 9. Common Misconceptions

### ❌ MYTH: "Token validation happens on every command"
✅ **FACT**: Token CHECKING happens on every command (boolean check, <1ms), but re-authentication only happens every ~29 hours when token expires.

### ❌ MYTH: "Rust is 10-20x faster for all operations"
✅ **FACT**: Rust is 1.05-1.10x faster for network-bound operations (API calls). 10-20x speedup only happens for CPU-bound operations like data processing.

### ❌ MYTH: "Flatten command uses Rust"
✅ **FACT**: Flatten orchestrates multiple operations: queries use Rust (get positions/orders), but mutations use Python (close/cancel).

---

## 10. Troubleshooting Guide

### Issue: "Rust executor failed, falling back to Python"
**Check**:
1. Is Rust library loaded? (Check logs for "✅ Rust order execution module loaded")
2. Is Rust built against correct Python? (Should use venv Python, not system Python)
3. Are parameters being passed correctly? (PyO3 0.20 has parameter passing bugs)

**Fix**:
```bash
cd rust
source ../venv/bin/activate
PYO3_PYTHON=$(which python3) cargo build --release
```

### Issue: "Position close reported success but positions still open"
**Cause**: TopStepX API sometimes returns success even when position not fully closed.

**Fix**: Disabled Rust implementation, using Python with verification step.

### Issue: "cancel_order disabled - using Python implementation"
**Cause**: PyO3 0.20 doesn't properly expose `account_id` parameter in async methods.

**Fix**: Python fallback is working (43-47ms). Will fix after upgrading PyO3 to 0.21+.

---

## 11. Future Improvements

### High Priority
1. **Fix PyO3 parameter issue** → Re-enable Rust cancel_order (~10ms improvement)
2. **Fix position close reliability** → Re-enable Rust close_position (~20ms improvement)
3. **Implement bracket orders in Rust** → ~50ms improvement for bracket orders

### Medium Priority
4. **Add historical data endpoint to Rust** → 2-5x speedup for bar aggregation
5. **Implement WebSocket handling in Rust** → 5-10x speedup for quote processing
6. **Add strategy execution framework in Rust** → 5-10x speedup for indicator calculations

### Low Priority
7. **Database operations in Rust** → 2-3x speedup for logging/metrics
8. **Add concurrent request batching** → Improve throughput for multi-symbol operations

---

## 12. References

### Key Files
- **Rust Order Execution**: `rust/src/order_execution/mod.rs`
- **Rust Query Operations**: `rust/src/query/mod.rs`
- **Python Adapter**: `brokers/topstepx_adapter.py`
- **Authentication**: `core/auth.py`
- **Interfaces**: `core/interfaces/*.py`

### Documentation
- **Rust Migration Plan**: `docs/RUST_MIGRATION_PLAN.md`
- **Build Instructions**: `docs/RUST_PHASE1_QUICKSTART.md`
- **Context Profile**: `.cursor/context_profile.json`

---

**Last Updated**: December 29, 2025  
**Maintained By**: Trading Bot Development Team  
**Version**: 1.0

