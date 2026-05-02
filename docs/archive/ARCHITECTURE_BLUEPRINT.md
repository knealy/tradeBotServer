# Trading Bot Architecture Blueprint

## Current System Overview

### High-Level Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Frontend (React + TypeScript)            │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐    │
│  │   Dashboard  │  │  Positions   │  │  Strategies  │    │
│  └──────────────┘  └──────────────┘  └──────────────┘    │
│         │                  │                   │            │
│         └──────────────────┼───────────────────┘            │
│                            │                                 │
│                    ┌───────▼────────┐                        │
│                    │  React Query  │                        │
│                    │  (Data Layer) │                        │
│                    └───────┬────────┘                        │
│                            │                                 │
│                    ┌───────▼────────┐                        │
│                    │  WebSocket    │                        │
│                    │  (Real-time)  │                        │
│                    └───────┬────────┘                        │
└────────────────────────────┼─────────────────────────────────┘
                              │
                    ┌─────────▼─────────┐
                    │   API Gateway     │
                    │ (async_webhook_   │
                    │    server.py)     │
                    └─────────┬─────────┘
                              │
        ┌─────────────────────┼─────────────────────┐
        │                     │                     │
┌───────▼────────┐   ┌────────▼────────┐  ┌────────▼────────┐
│ Trading Bot   │   │  Dashboard API  │  │  WebSocket      │
│ (trading_bot. │   │  (dashboard.py) │  │  Server         │
│     py)       │   │                 │  │                 │
└───────┬───────┘   └────────┬────────┘  └────────┬────────┘
        │                    │                    │
        └────────────────────┼────────────────────┘
                             │
                    ┌────────▼────────┐
                    │  PostgreSQL DB  │
                    │  (Caching &     │
                    │   Persistence)  │
                    └─────────────────┘
                             │
                    ┌────────▼────────┐
                    │  TopStepX API   │
                    │  (External)    │
                    └─────────────────┘
```

## Component Breakdown

### 1. Frontend Layer

#### Components
- **Layout.tsx**: Main layout wrapper with navigation
- **Dashboard.tsx**: Overview page with charts and metrics
- **PositionsPage.tsx**: Positions, orders, and order ticket
- **StrategiesPage.tsx**: Strategy management
- **TradingChart.tsx**: Interactive price chart with order/position visualization
- **OrderTicket.tsx**: Order placement interface
- **NotificationsFeed.tsx**: Real-time notifications

#### State Management
- **React Query**: Server state management, caching, and synchronization
- **Context API**: 
  - `AccountContext`: Selected account state
  - `WebSocketContext`: WebSocket connection status
- **Local State**: Component-specific UI state (useState)

#### Data Flow
1. Components request data via React Query hooks
2. React Query checks cache, fetches if stale
3. WebSocket updates invalidate queries or update cache directly
4. Components re-render with new data

### 2. API Gateway Layer (`servers/async_webhook_server.py`)

#### Responsibilities
- HTTP request routing
- WebSocket connection management
- Request/response transformation
- Authentication/authorization
- Rate limiting (future)

#### Key Endpoints
- `/api/positions` - Get open positions
- `/api/orders` - Get/modify/cancel orders
- `/api/orders/place` - Place new orders
- `/api/strategies/*` - Strategy management
- `/api/metrics` - Performance metrics
- `/api/trades` - Trade history

### 3. Business Logic Layer

#### Trading Bot (`trading_bot.py`)
- **Core Responsibilities**:
  - TopStepX API integration
  - Order execution
  - Position management
  - Risk management
  - Order tagging (strategy tracking)

#### Dashboard API (`servers/dashboard.py`)
- **Core Responsibilities**:
  - Data aggregation
  - Performance calculations
  - Trade consolidation
  - Strategy statistics

### 4. Strategy Layer (`strategies/`)

#### Strategy Base (`strategy_base.py`)
- Abstract base class for all strategies
- Common lifecycle management
- Configuration management

#### Implemented Strategies
- `overnight_range_strategy.py`: Overnight breakout trading
- `mean_reversion_strategy.py`: Mean reversion trading
- `trend_following_strategy.py`: Trend following trading

### 5. Infrastructure Layer

#### Database (`infrastructure/database.py`)
- PostgreSQL connection pooling
- Historical data caching
- Trade/order persistence
- Performance metrics storage

#### Task Queue (`infrastructure/task_queue.py`)
- Priority-based task execution
- Async task processing
- Resource management

#### Performance Metrics (`infrastructure/performance_metrics.py`)
- API call timing
- Slow query detection
- Performance monitoring

#### WebSocket Server (`servers/websocket_server.py`)
- Real-time data broadcasting
- Client connection management
- Event distribution

## Data Flow Patterns

### Order Placement Flow
```
User Input → OrderTicket Component
    ↓
React Query Mutation → API Gateway
    ↓
Trading Bot → TopStepX API
    ↓
Response → WebSocket Broadcast
    ↓
Frontend Updates (via WebSocket or Query Invalidation)
```

### Real-Time Updates Flow
```
TopStepX API → Trading Bot
    ↓
WebSocket Server → Frontend WebSocket
    ↓
useMarketSocket Hook → Query Invalidation/Update
    ↓
Component Re-render
```

### Historical Data Flow
```
Chart Request → API Gateway
    ↓
Trading Bot → Check DB Cache
    ↓
If Cache Hit: Return Cached Data
If Cache Miss: Fetch from TopStepX → Cache in DB → Return
```

## Current Performance Bottlenecks

### Identified Issues
1. **API Call Spam**: Multiple components fetching same data simultaneously
2. **Excessive Polling**: refetchInterval active even when WebSocket connected
3. **No Query Deduplication**: Same query executed multiple times
4. **Inefficient Cache Usage**: Cache not properly utilized
5. **Synchronous Operations**: Some blocking operations in async context

### Hot Paths (Critical for Latency)
1. **Order Execution**: `place_market_order` → TopStepX API
2. **Order Modification**: `modify_order` → TopStepX API
3. **Position Updates**: WebSocket → Frontend
4. **Chart Data Loading**: Historical data fetching
5. **Strategy Execution**: Strategy logic → Order placement

## Translation Layer Design

### Purpose
Abstract away TopStepX-specific implementation to allow easy migration to other brokers/platforms.

### Proposed Structure
```
┌─────────────────────────────────────┐
│      Strategy Layer                 │
│  (Platform Agnostic)                │
└──────────────┬──────────────────────┘
               │
┌──────────────▼──────────────────────┐
│   Translation Layer                 │
│   (Broker Abstraction)              │
│   - Order Interface                 │
│   - Position Interface              │
│   - Market Data Interface           │
└──────────────┬──────────────────────┘
               │
    ┌──────────┼──────────┐
    │          │          │
┌───▼───┐ ┌───▼───┐ ┌───▼───┐
│TopStep│ │IBKR   │ │Other  │
│X      │ │       │ │Broker │
└───────┘ └───────┘ └───────┘
```

### Interface Definitions

#### Order Interface
```python
class OrderInterface(ABC):
    @abstractmethod
    async def place_market_order(self, symbol: str, side: str, quantity: int) -> OrderResponse
    
    @abstractmethod
    async def place_limit_order(self, symbol: str, side: str, quantity: int, price: float) -> OrderResponse
    
    @abstractmethod
    async def modify_order(self, order_id: str, price: float = None, quantity: int = None) -> OrderResponse
    
    @abstractmethod
    async def cancel_order(self, order_id: str) -> CancelResponse
```

#### Position Interface
```python
class PositionInterface(ABC):
    @abstractmethod
    async def get_positions(self, account_id: str) -> List[Position]
    
    @abstractmethod
    async def close_position(self, position_id: str, quantity: int = None) -> CloseResponse
```

#### Market Data Interface
```python
class MarketDataInterface(ABC):
    @abstractmethod
    async def get_historical_data(self, symbol: str, timeframe: str, limit: int) -> List[Bar]
    
    @abstractmethod
    async def get_market_quote(self, symbol: str) -> Quote
```

## Modular Design Principles

### 1. Separation of Concerns
- **UI Layer**: React components, presentation only
- **Business Logic**: Trading bot, strategies, risk management
- **Data Layer**: Database, caching, persistence
- **Integration Layer**: External API adapters

### 2. Dependency Injection
- Strategies receive trading bot instance
- Trading bot receives API client
- Easy to mock for testing

### 3. Event-Driven Architecture
- WebSocket events trigger updates
- Strategy events trigger order placement
- Order events trigger notifications

### 4. Configuration Management
- Environment variables for settings
- Strategy configs in database
- Runtime configuration updates

## Migration Strategy

### Phase 1: Translation Layer Implementation
1. Create abstract interfaces
2. Refactor TopStepX implementation to use interfaces
3. Add adapter pattern for TopStepX
4. Test with existing functionality

### Phase 2: Modularization
1. Extract strategy execution to separate service
2. Extract order management to separate service
3. Extract market data to separate service
4. Implement event bus for inter-service communication

### Phase 3: Performance Optimization
1. Identify hot paths
2. Migrate hot paths to Rust
3. Optimize database queries
4. Implement connection pooling

### Phase 4: Multi-Broker Support
1. Implement IBKR adapter
2. Implement other broker adapters
3. Add broker selection UI
4. Test multi-broker scenarios

## Current Status (December 4, 2025)

### **Phase 2 Complete → Preparing for Phase 4 (Rust Migration)**

**Recent Achievements**:
- ✅ Dynamic contract management (no hardcoded values)
- ✅ Comprehensive test suite (100% success rate)
- ✅ All critical bugs fixed (datetime, timezone, SignalR)
- ✅ Performance baselines established
- ✅ Database notifications system
- ✅ Logging optimized

**Performance Metrics**:
- Average command execution: 485.12ms
- Fastest: 0.34ms (cached balance)
- Cache hit rate: 95%
- Test success rate: 100%

## Next Steps

1. **Week 1-2**: Code refactoring + Rust project setup
2. **Week 3-4**: Order execution migration (Priority 1)
3. **Week 4-5**: WebSocket migration (Priority 2)
4. **Week 5-6**: Market data aggregation migration (Priority 3)
5. **Week 7-8**: Strategy engine migration (Priority 4)
6. **Week 9-10**: Database operations migration (Priority 5)

**See**: 
- [MIGRATION_READINESS.md](MIGRATION_READINESS.md) - Detailed checklist
- [RUST_MIGRATION_PLAN.md](RUST_MIGRATION_PLAN.md) - Migration strategy
- [PROJECT_STATUS_2025-12-04.md](PROJECT_STATUS_2025-12-04.md) - Current status

