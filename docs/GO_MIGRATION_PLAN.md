# Go Migration Plan for Hot Paths

## Executive Summary

This document outlines the plan to migrate performance-critical paths from Python to Go for maximum speed and lowest latency.

## Why Go Over Rust?

**Go is recommended for this project because:**
- ✅ Faster development time (2-3 months vs 6-12 months for Rust)
- ✅ Excellent concurrency (goroutines) for order execution
- ✅ Built-in HTTP/WebSocket support
- ✅ Easier to learn and maintain
- ✅ Sufficient performance (10-50x faster than Python)
- ✅ Better ecosystem for trading APIs

**Rust would be better if:**
- We needed absolute maximum performance (100x+)
- We were building HFT with microsecond latency requirements
- We had experienced Rust developers

**For our use case, Go provides the best balance of performance and development speed.**

---

## Phase 1: Order Execution Engine (Priority 1 - Highest Impact)

### Current Performance
- **Python**: ~100-150ms per order
- **Bottleneck**: GIL, sequential processing, JSON parsing

### Go Target Performance
- **Go**: ~10-20ms per order
- **Improvement**: 5-10x faster

### Implementation Plan

**File Structure:**
```
go/
├── cmd/
│   └── order-executor/
│       └── main.go          # Order execution service
├── internal/
│   ├── api/
│   │   ├── topstepx.go      # TopStepX API client
│   │   └── auth.go          # Authentication
│   ├── orders/
│   │   ├── executor.go      # Order execution logic
│   │   ├── validator.go     # Order validation
│   │   └── types.go         # Order types
│   └── cache/
│       └── redis.go         # Redis cache client
└── pkg/
    └── strategy/
        └── tags.go          # Strategy tagging utilities
```

**Key Features:**
1. **Concurrent Order Execution**
   ```go
   // Execute multiple orders in parallel
   func (e *OrderExecutor) ExecuteBatch(orders []Order) []Result {
       results := make([]Result, len(orders))
       var wg sync.WaitGroup
       
       for i, order := range orders {
           wg.Add(1)
           go func(idx int, o Order) {
               defer wg.Done()
               results[idx] = e.executeOrder(o)
           }(i, order)
       }
       
       wg.Wait()
       return results
   }
   ```

2. **Fast JSON Processing**
   - Use `encoding/json` with struct tags
   - Pre-allocate buffers for responses
   - Zero-copy where possible

3. **Connection Pooling**
   - Reuse HTTP connections
   - Keep-alive connections
   - Connection pool size: 100

4. **Strategy Tagging**
   ```go
   type Order struct {
       Symbol      string
       Side        string
       Quantity    int
       Price       float64
       StrategyTag string  // e.g., "overnight_range"
   }
   
   func GenerateCustomTag(orderType string, strategyName string) string {
       timestamp := time.Now().Unix()
       counter := atomic.AddUint64(&orderCounter, 1)
       return fmt.Sprintf("TradingBot-v1.0-strategy-%s-%s-%d-%d",
           strategyName, orderType, counter, timestamp)
   }
   ```

**API Endpoints:**
- `POST /api/go/orders/place` - Place single order
- `POST /api/go/orders/batch` - Place multiple orders concurrently
- `GET /api/go/orders/{id}/status` - Get order status

**Communication with Python:**
- **Option A**: gRPC (recommended)
  - Type-safe
  - Fast binary protocol
  - ~1-2ms overhead
  
- **Option B**: REST API
  - Simpler to implement
  - JSON overhead (~5-10ms)
  - Easier debugging

**Recommended: gRPC for internal communication**

---

## Phase 2: Market Data Processing (Priority 2)

### Current Performance
- **Python**: ~5-10ms per bar update
- **Bottleneck**: JSON parsing, list operations

### Go Target Performance
- **Go**: ~0.1-0.5ms per bar update
- **Improvement**: 50-100x faster

### Implementation Plan

**Features:**
1. **WebSocket Handler**
   ```go
   type MarketDataProcessor struct {
       subscribers map[string][]chan BarUpdate
       mu          sync.RWMutex
   }
   
   func (p *MarketDataProcessor) ProcessUpdate(update BarUpdate) {
       p.mu.RLock()
       subs := p.subscribers[update.Symbol]
       p.mu.RUnlock()
       
       // Broadcast to all subscribers concurrently
       for _, ch := range subs {
           select {
           case ch <- update:
           default: // Non-blocking
           }
       }
   }
   ```

2. **Bar Aggregation**
   - Real-time OHLCV aggregation
   - ATR calculation
   - Moving averages

3. **Caching Layer**
   - In-memory cache for recent bars
   - Redis for distributed cache
   - Automatic expiration

**Performance Targets:**
- Process 10,000+ bars/second
- Sub-1ms latency for bar updates
- Support 100+ concurrent WebSocket connections

---

## Phase 3: Risk Management (Priority 3)

### Current Performance
- **Python**: ~1-2ms per check
- **Bottleneck**: Position calculations, DLL checks

### Go Target Performance
- **Go**: ~0.05-0.1ms per check
- **Improvement**: 20x faster

### Implementation Plan

**Features:**
1. **Real-time Position Tracking**
   ```go
   type RiskManager struct {
       positions map[string]Position
       mu        sync.RWMutex
       dll       float64
       mll       float64
   }
   
   func (r *RiskManager) CheckOrder(order Order) error {
       r.mu.RLock()
       defer r.mu.RUnlock()
       
       // Fast concurrent checks
       if r.dailyPnL < -r.dll {
           return ErrDLLViolation
       }
       
       // Position size validation
       // DLL proximity checks
       // MLL checks
       
       return nil
   }
   ```

2. **Concurrent Safety**
   - RWMutex for read-heavy operations
   - Atomic operations for counters
   - Lock-free data structures where possible

---

## Phase 4: Data Feed Handler (Optional - Rust)

If we need even more performance, we can migrate data feed processing to Rust:

**Rust Advantages:**
- Zero-cost abstractions
- No garbage collector
- Maximum performance

**When to Consider:**
- If Go performance isn't sufficient
- If we need sub-millisecond data processing
- If we're processing millions of ticks/second

---

## Migration Strategy

### Step 1: Create Go Service (Week 1-2)
1. Set up Go project structure
2. Implement TopStepX API client
3. Create order execution engine
4. Add strategy tagging support

### Step 2: gRPC Bridge (Week 2-3)
1. Define protobuf schemas
2. Implement gRPC server (Go)
3. Implement gRPC client (Python)
4. Test communication

### Step 3: Gradual Migration (Week 3-4)
1. Route new orders through Go service
2. Keep Python as fallback
3. Monitor performance
4. Gradually migrate all order execution

### Step 4: Optimize (Week 4+)
1. Add connection pooling
2. Implement caching
3. Optimize hot paths
4. Benchmark and tune

---

## Performance Comparison

| Operation | Python (Current) | Go (Target) | Improvement |
|-----------|------------------|-------------|-------------|
| Order Execution | 100-150ms | 10-20ms | **5-10x** |
| Data Parsing | 5-10ms | 0.1-0.5ms | **50-100x** |
| Risk Checks | 1-2ms | 0.05-0.1ms | **20x** |
| Concurrent Orders | 1 at a time | 100+ parallel | **100x** |
| Memory Usage | 250MB | 50MB | **5x** |

---

## Code Example: Go Order Executor

```go
package orders

import (
    "context"
    "sync"
    "time"
)

type OrderExecutor struct {
    apiClient    *topstepx.Client
    cache        *cache.RedisCache
    strategyTags map[string]string
    mu           sync.RWMutex
}

func (e *OrderExecutor) PlaceOrder(ctx context.Context, order Order) (*Result, error) {
    // Validate order
    if err := e.validateOrder(order); err != nil {
        return nil, err
    }
    
    // Generate custom tag with strategy name
    customTag := GenerateCustomTag(order.Type, order.StrategyName)
    
    // Check risk limits (fast concurrent check)
    if err := e.riskManager.CheckOrder(order); err != nil {
        return nil, err
    }
    
    // Execute order (non-blocking, uses connection pool)
    start := time.Now()
    result, err := e.apiClient.PlaceOrder(ctx, order, customTag)
    duration := time.Since(start)
    
    // Log performance
    metrics.RecordOrderExecution(duration, err == nil)
    
    return result, err
}

func (e *OrderExecutor) PlaceBatch(ctx context.Context, orders []Order) []Result {
    results := make([]Result, len(orders))
    semaphore := make(chan struct{}, 10) // Limit to 10 concurrent
    var wg sync.WaitGroup
    
    for i, order := range orders {
        wg.Add(1)
        semaphore <- struct{}{} // Acquire
        
        go func(idx int, o Order) {
            defer wg.Done()
            defer func() { <-semaphore }() // Release
            
            results[idx], _ = e.PlaceOrder(ctx, o)
        }(i, order)
    }
    
    wg.Wait()
    return results
}
```

---

## Integration with Python

### gRPC Service Definition

```protobuf
syntax = "proto3";

package orders;

service OrderService {
    rpc PlaceOrder(OrderRequest) returns (OrderResponse);
    rpc PlaceBatch(BatchOrderRequest) returns (BatchOrderResponse);
    rpc GetOrderStatus(OrderStatusRequest) returns (OrderStatusResponse);
}

message OrderRequest {
    string symbol = 1;
    string side = 2;
    int32 quantity = 3;
    double price = 4;
    string order_type = 5;
    string strategy_name = 6;
    optional double stop_loss = 7;
    optional double take_profit = 8;
}
```

### Python Client

```python
import grpc
from go_orders_pb2 import OrderRequest, OrderResponse
from go_orders_pb2_grpc import OrderServiceStub

class GoOrderExecutor:
    def __init__(self):
        channel = grpc.insecure_channel('localhost:50051')
        self.stub = OrderServiceStub(channel)
    
    async def place_order(self, symbol, side, quantity, strategy_name=None):
        request = OrderRequest(
            symbol=symbol,
            side=side,
            quantity=quantity,
            strategy_name=strategy_name or ""
        )
        response = self.stub.PlaceOrder(request)
        return response
```

---

## Deployment Architecture

```
┌─────────────────────────────────────────────────┐
│              Python Trading Bot                 │
│  (Strategies, Business Logic)                   │
└─────────────────────────────────────────────────┘
                    │
                    │ gRPC
                    ▼
┌─────────────────────────────────────────────────┐
│            Go Order Execution Service           │
│  - Order placement                              │
│  - Risk checks                                  │
│  - Position tracking                            │
└─────────────────────────────────────────────────┘
                    │
                    │ HTTP/WebSocket
                    ▼
            TopStepX API
```

---

## Next Steps

1. **Immediate** (This Week):
   - ✅ Fix strategy tagging (Python)
   - ✅ Optimize overnight_range strategy
   - ✅ Fix stats/verify endpoints

2. **Short-term** (Next 2 Weeks):
   - Set up Go project structure
   - Implement basic TopStepX client
   - Create order execution engine
   - Add strategy tagging

3. **Medium-term** (Next Month):
   - Implement gRPC bridge
   - Migrate order execution to Go
   - Add performance monitoring
   - Benchmark improvements

4. **Long-term** (Next 3 Months):
   - Migrate data processing to Go
   - Migrate risk management to Go
   - Full Go service for hot paths
   - Python only for strategies

---

## Success Metrics

- **Order Execution**: <20ms (from 100-150ms)
- **Data Processing**: <1ms per bar (from 5-10ms)
- **Concurrent Orders**: 100+ parallel (from 1 sequential)
- **Memory Usage**: <100MB (from 250MB)
- **CPU Usage**: <30% (from 60%)

---

**This migration will provide significant performance improvements while maintaining the flexibility of Python for strategy development.**

