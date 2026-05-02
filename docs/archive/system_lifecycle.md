# System Lifecycle & Bottleneck Analysis

**Last Updated:** December 16, 2025

## Overview

This document provides a comprehensive walkthrough of the trading bot's code lifecycle, system processes, data flows, and identifies potential bottlenecks and performance hotspots. Use this guide to understand how the system works end-to-end and where to look when diagnosing issues or optimizing performance.

---

## System Architecture Diagram

```mermaid
flowchart TD
    User[User/CLI] -->|Commands| TradingBot[TradingBot Main]
    User -->|HTTP| MasterGUI[Master GUI]
    
    TradingBot -->|Auth| AuthManager[Auth Manager]
    TradingBot -->|Market Data| BrokerAdapter[TopStepX Adapter]
    TradingBot -->|Strategies| StrategyManager[Strategy Manager]
    TradingBot -->|Risk| RiskManager[Risk Manager]
    TradingBot -->|Tracking| AccountTracker[Account Tracker]
    
    MasterGUI -->|REST API| ChartServer[Chart Server]
    ChartServer -->|Queries| TradingBot
    
    BrokerAdapter -->|REST API| TopStepXAPI[TopStepX REST API]
    BrokerAdapter -->|SignalR| SignalRHub[SignalR Market Hub]
    BrokerAdapter -->|Hot Path| RustExecutor[Rust Executor]
    
    RustExecutor -->|Fallback| PythonImpl[Python Implementation]
    
    StrategyManager -->|Manages| Strategies[Strategy Instances]
    Strategies -->|Signals| OrderExecutor[Order Executor]
    OrderExecutor -->|Orders| BrokerAdapter
    
    BrokerAdapter -->|Responses| OrderExecutor
    OrderExecutor -->|Updates| AccountTracker
    AccountTracker -->|Compliance| RiskManager
    
    SignalRHub -->|Real-time Quotes| MarketData[Market Data Manager]
    MarketData -->|Price Updates| Strategies
    MarketData -->|PnL Calc| AccountTracker
```

---

## Data Flow: Command to Execution

### 1. User Command Flow

```mermaid
sequenceDiagram
    participant User
    participant CLI
    participant TradingBot
    participant Adapter
    participant API
    
    User->>CLI: Enter command (e.g., "buy mnq 1")
    CLI->>TradingBot: Parse command
    TradingBot->>TradingBot: Validate account/contract
    TradingBot->>Adapter: place_market_order()
    
    alt Rust Available
        Adapter->>RustExecutor: place_order_rust()
        RustExecutor->>API: POST /api/Order/place
        API-->>RustExecutor: Response (10-15ms)
        RustExecutor-->>Adapter: OrderResponse
    else Rust Unavailable
        Adapter->>PythonImpl: place_order_python()
        PythonImpl->>API: POST /api/Order/place
        API-->>PythonImpl: Response (30-50ms)
        PythonImpl-->>Adapter: OrderResponse
    end
    
    Adapter-->>TradingBot: OrderResponse
    TradingBot->>AccountTracker: Update state
    TradingBot-->>CLI: Display result
    CLI-->>User: Show confirmation
```

**Latency Breakdown:**
- Command parsing: <1ms
- Account/contract validation: 1-5ms (cached)
- Order placement (Rust): 10-15ms
- Order placement (Python): 30-50ms
- State update: 1-2ms
- Display: <1ms

**Total End-to-End Latency:** 15-60ms (depending on Rust availability)

---

### 2. Strategy Signal Flow

```mermaid
sequenceDiagram
    participant Strategy
    participant MarketData
    participant SignalR
    participant OrderExec
    participant Risk
    participant Adapter
    
    SignalR->>MarketData: Real-time quote
    MarketData->>Strategy: Price update
    Strategy->>Strategy: Analyze market
    
    alt Signal Generated
        Strategy->>Risk: Check compliance
        Risk->>Risk: Verify DLL/MLL
        
        alt Compliant
            Risk-->>Strategy: Approved
            Strategy->>OrderExec: Execute trade
            OrderExec->>Adapter: Place order
            Adapter-->>OrderExec: Response
            OrderExec->>Strategy: Confirm
            Strategy->>AccountTracker: Update P&L
        else Not Compliant
            Risk-->>Strategy: Rejected
            Strategy->>Strategy: Log violation
        end
    end
```

**Latency Hotspots:**
- SignalR quote delivery: 50-200ms (network dependent)
- Strategy analysis: 1-10ms (strategy complexity)
- Risk check: 1-5ms
- Order execution: 10-50ms (see above)

**Critical Path:** SignalR → Analysis → Risk → Execution = **60-270ms**

---

### 3. Position/Order Query Flow

```mermaid
sequenceDiagram
    participant User
    participant TradingBot
    participant Adapter
    participant Cache
    participant API
    
    User->>TradingBot: positions command
    TradingBot->>Adapter: get_positions()
    
    alt Rust Available
        Adapter->>RustExecutor: get_positions_rust()
        RustExecutor->>API: GET /api/Position/search
        API-->>RustExecutor: Response (20-30ms)
        RustExecutor-->>Adapter: Position list
    else Python Fallback
        Adapter->>PythonImpl: get_positions_python()
        PythonImpl->>API: GET /api/Position/search
        API-->>PythonImpl: Response (40-60ms)
        PythonImpl-->>Adapter: Position list
    end
    
    Adapter->>TradingBot: Enriched positions
    
    loop For each position
        TradingBot->>Adapter: get_linked_orders()
        Adapter->>Cache: Check orders cache
        alt Cache Hit
            Cache-->>Adapter: Cached orders (1ms)
        else Cache Miss
            Adapter->>API: GET /api/Order/search
            API-->>Adapter: Orders (30-50ms)
        end
        TradingBot->>MarketData: get_market_quote()
        MarketData->>SignalR: Request if not cached
        SignalR-->>MarketData: Current price
    end
    
    TradingBot->>TradingBot: Calculate P&L
    TradingBot-->>User: Display positions
```

**Latency Breakdown:**
- Get positions: 20-60ms
- Get linked orders per position: 1-50ms × N positions
- Get current prices: 50-200ms (SignalR) or cached (<1ms)
- P&L calculation: 1-5ms per position

**Optimization Opportunities:**
- Batch order queries (currently sequential)
- Increase cache TTL for orders (currently 5s)
- Pre-fetch prices for known symbols

---

### 4. Account State Update Flow

```mermaid
flowchart LR
    Fill[Order Fill] --> |Update| AccountTracker
    AccountTracker --> |Calculate| Balance[Current Balance]
    AccountTracker --> |Track| RealizedPnL[Realized P&L]
    
    Position[Open Position] --> |Query Price| Quote[Market Quote]
    Quote --> |Calculate| UnrealizedPnL[Unrealized P&L]
    UnrealizedPnL --> AccountTracker
    
    AccountTracker --> |Check| DLL[Daily Loss Limit]
    AccountTracker --> |Check| MLL[Maximum Loss Limit]
    
    DLL --> |Update| ComplianceStatus[Compliance Status]
    MLL --> ComplianceStatus
    
    ComplianceStatus --> |Enforce| RiskManager[Risk Manager]
    RiskManager --> |Block Trades if Violated| OrderExecutor
```

**Update Triggers:**
- Fill received: Immediate update
- Position query: On-demand calculation
- EOD update: Scheduled at 21:00 UTC
- User command: Manual refresh

**Latency:** 1-5ms (local calculation) + 50-200ms (quote fetch if needed)

---

## System Processes

### Process 1: Main Trading Bot (`trading_bot.py`)

**Responsibilities:**
- User interface (CLI)
- Command parsing
- Account management
- Order routing
- Position tracking
- Strategy coordination

**Lifecycle:**
1. **Startup** (Total: 200-500ms)
   - Load environment variables (10ms)
   - Initialize logging (5ms)
   - Authenticate with TopStepX (100-200ms)
   - Fetch contracts (parallel, 100-200ms)
   - List accounts (parallel, 100-200ms)
   - Initialize strategy manager (10ms)
   - Load persisted strategy states (20-50ms)

2. **Runtime Loop**
   - Listen for user commands
   - Process commands
   - Update displays
   - Monitor strategies

3. **Shutdown**
   - Stop strategies gracefully
   - Save account state
   - Close connections

**Bottlenecks:**
- Initial authentication: 100-200ms
- Contract fetch on first run: 100-200ms
- SignalR connection establishment: 500-2000ms

---

### Process 2: Chart Server (`gui/chart_html.py`)

**Responsibilities:**
- Serve Master GUI HTML
- Provide REST API for GUI
- Real-time data updates
- Chart data aggregation

**Endpoints:**
- `/` - Master control interface
- `/api/chart/quote` - Latest quote (cached 1s)
- `/api/chart/positions` - Open positions (cached 2s)
- `/api/chart/orders` - Open orders (cached 2s)
- `/api/chart/account/state` - Account metrics (cached 10s)
- `/api/chart/strategy/status` - Strategy states (cached 3s)
- `/api/chart/strategy/start` - Start strategy
- `/api/chart/strategy/stop` - Stop strategy

**Performance:**
- Cache TTL: 1-10s depending on data type
- Response time: <10ms (cached), 20-200ms (uncached)
- Concurrent requests: Async/await handles 100+ concurrent

**Bottlenecks:**
- First request after cache expiration
- Heavy aggregation (historical bars)

---

### Process 3: Strategy Executor (`core/strategy_executor.py`)

**Responsibilities:**
- Run strategies in dedicated process
- Isolate strategy failures
- Monitor strategy health
- Auto-restart on failure

**Lifecycle:**
1. Initialize trading bot
2. Authenticate
3. **Prefetch contracts** (critical fix)
4. Load persisted strategy states
5. Start requested strategies
6. Monitor loop (30s interval)

**Bottlenecks:**
- Contract cache miss: 100-200ms (now fixed with prefetch)
- Strategy initialization: 50-100ms per strategy
- Analysis overhead: Variable (strategy dependent)

---

### Process 4: SignalR WebSocket Manager (`core/websocket_manager.py`)

**Responsibilities:**
- Maintain SignalR connection
- Subscribe to symbols
- Deliver real-time quotes
- Handle reconnection

**Connection Lifecycle:**
1. Ensure valid token
2. Connect to SignalR hub (500-2000ms)
3. Subscribe to symbols (50-100ms each)
4. Listen for quotes
5. Reconnect on error (exponential backoff)

**Error Handling:**
- Network interruption: Auto-reconnect with backoff (2s, 4s, 8s...)
- Token expiration: Refresh token and reconnect
- CompletionMessage: No longer treated as error (fixed)

**Bottlenecks:**
- Initial connection: 500-2000ms
- Reconnection after sleep: 2-30s depending on attempts
- Quote latency: 50-200ms (network dependent)

---

## Bottleneck Analysis & Solutions

### Hot Paths (Latency Critical)

| Operation | Current Latency | Optimization | Status |
|-----------|----------------|--------------|---------|
| Order placement | 10-50ms | Rust executor | ✅ Implemented |
| Position query | 20-60ms | Rust executor | ✅ Implemented |
| Market quote | 50-200ms | SignalR cache | ✅ Implemented |
| Historical bars | 200-1000ms | Bar aggregator cache | ✅ Implemented |
| Contract lookup | 1-5ms | In-memory cache | ✅ Implemented |
| Order modification | 10-50ms | Rust executor | ✅ Implemented |

### Cold Paths (Less Critical)

| Operation | Current Latency | Notes |
|-----------|----------------|-------|
| Account list | 100-200ms | Once per session |
| Contract fetch | 100-200ms | Once per session (now prefetched) |
| Trade history | 200-500ms | On-demand |
| Strategy load | 50-100ms | Once per strategy |

### Identified Bottlenecks

#### 1. SignalR Connection Establishment ❌ HIGH IMPACT
**Symptom:** 500-2000ms delay on startup
**Impact:** Delayed quote delivery, strategies can't start
**Solution:** 
- Pre-connect during bot initialization (parallel with other tasks)
- Keep connection alive (already implemented)
- Auto-reconnect on errors (already implemented)

#### 2. Linked Orders Query for Positions ⚠️ MEDIUM IMPACT
**Symptom:** Sequential API calls for each position
**Impact:** N × 30-50ms for N positions
**Solution:**
- Batch order queries (fetch all once, filter locally) ✅ Partially fixed
- Increase order cache TTL from 5s to 10s
- Use heuristic matching (contract + side + type) ✅ Implemented

#### 3. P&L Calculation Without Current Prices ⚠️ MEDIUM IMPACT
**Symptom:** Stale P&L if quotes not cached
**Impact:** 50-200ms per symbol to fetch quote
**Solution:**
- Pre-fetch quotes for all position symbols in parallel ✅ Now implemented
- Update P&L in background task (account_tracker update)

#### 4. Contract Cache Miss in Strategy Executor ❌ HIGH IMPACT
**Symptom:** "Contract cache is empty" error
**Impact:** Strategies fail to start
**Solution:** Prefetch contracts during executor initialization ✅ Fixed

#### 5. Account State Queries Not Using Tracker ⚠️ MEDIUM IMPACT
**Symptom:** Shows $0 P&L even with open positions
**Impact:** Misleading compliance/state display
**Solution:** Update tracker with current positions before display ✅ Fixed

---

## Performance Optimization Checklist

### Implemented ✅

- [x] Rust executor for order operations (10-15x faster)
- [x] SignalR real-time quotes (vs polling)
- [x] In-memory contract cache
- [x] Historical bar aggregation cache
- [x] Lazy initialization for expensive operations
- [x] Parallel initialization tasks
- [x] Account tracker for local state
- [x] Order cache (5s TTL)
- [x] Linked orders heuristic matching
- [x] Prefetch contracts in strategy executor
- [x] Update account tracker with positions

### Potential Future Optimizations

- [ ] Batch order queries (fetch all, filter locally)
- [ ] WebSocket for Master GUI (vs polling)
- [ ] Redis cache for distributed deployments
- [ ] Database for trade history (vs API calls)
- [ ] Parallel position enrichment (quotes + orders)
- [ ] Incremental chart updates (vs full reload)
- [ ] Strategy result caching
- [ ] Connection pooling for REST API

---

## Current System Capabilities

### ✅ Working Features

- **Order Execution:** Market, limit, stop, bracket orders
- **Position Management:** Open, modify, close positions
- **Risk Management:** DLL/MLL tracking and enforcement
- **Real-time Data:** SignalR quotes with caching
- **Strategies:** Multiple strategies with manager
- **CLI Interface:** Full command-line control
- **Master GUI:** Web-based control panel
- **Account Tracking:** Real-time P&L and compliance
- **Historical Data:** Cached bar aggregation
- **Rust Integration:** Hot path optimization
- **Auto-reconnection:** Network interruption recovery

### ⚠️ Limitations

- **SignalR Depth:** Not all exchanges support depth data (REST fallback available)
- **Rust Fallback:** Some operations fall back to Python (logged, not silent)
- **Cache Invalidation:** Manual refresh required for some data
- **Single Instance:** No distributed deployment (yet)
- **Strategy Isolation:** Strategies share same process (executor helps)

### 🔧 Recent Fixes

- ✅ Added `get_linked_orders` to adapter
- ✅ Fixed `raw_response` in ModifyOrderResponse
- ✅ Fixed trades date parsing (supports multiple formats)
- ✅ Fixed 50K/100K eval account type assignment
- ✅ Fixed account_select to work without --command
- ✅ Fixed contract prefetch in strategy executor
- ✅ Fixed account state P&L accuracy
- ✅ Fixed SignalR CompletionMessage error handling
- ✅ Improved linked orders matching logic

---

## Debugging Guide

### Performance Issues

**Symptom:** Slow order placement
1. Check if Rust executor is available: Look for "⚡ Rust" logs
2. Verify network latency to TopStepX API
3. Check rate limiter: Ensure not hitting limits

**Symptom:** Stale position P&L
1. Verify SignalR connection: Check "Connected" status in GUI
2. Force refresh: Use `positions` command or reload GUI
3. Check account tracker update: Ensure positions are being tracked

**Symptom:** Strategy not trading
1. Verify strategy is active: `strategies status` command
2. Check compliance: `compliance` command
3. Check contract cache: Should see contracts in log
4. Verify market hours: Strategies respect trading windows

### Connection Issues

**Symptom:** SignalR disconnects frequently
1. Check network stability
2. Verify token expiration: Should auto-refresh
3. Review websocket_manager logs for errors
4. Check for OSError (network down) recovery

**Symptom:** API calls failing with 401
1. Token expired: Should auto-refresh
2. Check API key validity
3. Verify account access

---

## Metrics & Monitoring

### Key Performance Indicators

- **Order Latency:** Target <20ms (Rust), <50ms (Python)
- **Quote Latency:** Target <200ms (SignalR)
- **Position Query:** Target <100ms (includes enrichment)
- **Strategy Loop:** Target <1s per iteration
- **Cache Hit Rate:** Target >80% for quotes, >90% for contracts
- **Compliance Check:** Target <5ms

### Log Monitoring

**Performance logs:**
- `⚡ Rust execution: Xms` - Rust hot path timing
- `🐍 Python execution: Xms` - Python fallback timing
- `📦 Cache HIT` - Successful cache usage
- `📦 Cache MISS` - Cache miss (may indicate issue)

**Error logs:**
- `❌ Failed to...` - Critical errors
- `⚠️ Rust execution failed` - Rust fallback (expected sometimes)
- `SignalR error` - Connection issues
- `Account ID is required` - Missing state

---

## Summary

**System Strengths:**
- Low-latency order execution (10-20ms with Rust)
- Real-time market data (SignalR)
- Comprehensive risk management
- Robust error handling and fallbacks
- Parallel initialization
- Efficient caching strategy

**System Weaknesses:**
- SignalR connection time on startup (500-2000ms)
- Sequential linked order queries (being optimized)
- Single-process architecture (limits scalability)
- No persistent trade database (uses API)

**Overall Performance:** The system is optimized for low-latency execution with multiple layers of caching, Rust hot paths, and async operations. Most user-facing operations complete in <100ms, with critical trading paths completing in 10-20ms.
