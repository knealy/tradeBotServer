# Trading Bot Framework & Architecture Documentation

**Last Updated:** January 2025  
**Version:** 2.0.0

## Table of Contents

1. [System Overview](#system-overview)
2. [Architecture & Framework](#architecture--framework)
3. [Core Components](#core-components)
4. [Execution Methods](#execution-methods)
5. [Database Implementation](#database-implementation)
6. [Data Lifecycle & Flow](#data-lifecycle--flow)
7. [Use Cases](#use-cases)
8. [Software Development Lifecycle (SDLC)](#software-development-lifecycle-sdlc)
9. [Typical Results & Performance](#typical-results--performance)

---

## System Overview

The TopStepX Trading Bot is a comprehensive automated trading system designed for prop firm futures trading. It provides:

- **Real-time market data** via SignalR WebSocket connections
- **Automated strategy execution** with multiple trading strategies
- **Risk management** with daily/maximum loss limit tracking
- **Order execution** via TopStepX ProjectX API
- **Backtesting capabilities** for strategy validation
- **Web-based GUI** for monitoring and control
- **PostgreSQL database** for persistent state and historical data

### Key Features

- ✅ Multi-strategy support with modular architecture
- ✅ Real-time account tracking and compliance monitoring
- ✅ SignalR WebSocket for live market data
- ✅ PostgreSQL database for persistence
- ✅ Rust hot-path optimization for low-latency execution
- ✅ Comprehensive backtesting engine
- ✅ Web-based master control interface
- ✅ CLI interface for manual trading
- ✅ Strategy executor for automated trading

---

## Architecture & Framework

### High-Level Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    User Interfaces                            │
├─────────────────────┬───────────────────┬─────────────────────┤
│   CLI Interface     │   Master GUI      │   Strategy Executor │
│  (trading_bot.py)    │  (Web Browser)    │  (Standalone)      │
└──────────┬──────────┴──────────┬────────┴──────────┬─────────┘
           │                      │                    │
           └──────────────────────┼────────────────────┘
                                  │
                    ┌─────────────▼─────────────┐
                    │   TopStepXTradingBot      │
                    │   (Core Orchestrator)     │
                    └─────────────┬─────────────┘
                                  │
        ┌─────────────────────────┼─────────────────────────┐
        │                         │                         │
┌───────▼────────┐    ┌──────────▼──────────┐   ┌─────────▼─────────┐
│  Strategy      │    │   Market Data        │   │  Order Execution  │
│  Manager       │    │   Manager            │   │  & Risk Mgmt     │
└───────┬────────┘    └──────────┬───────────┘   └─────────┬────────┘
        │                        │                          │
        │                        │                          │
┌───────▼────────┐    ┌──────────▼──────────┐   ┌─────────▼─────────┐
│  Strategies    │    │  WebSocket Manager  │   │  TopStepX Adapter │
│  - Overnight   │    │  (SignalR)         │   │  (Rust/Python)    │
│  - Mean Rev    │    │  - Market Hub       │   │                   │
│  - Trend       │    │  - User Hub        │   │                   │
│  - Simple      │    │                     │   │                   │
└────────────────┘    └─────────────────────┘   └─────────┬─────────┘
                                                           │
                                              ┌────────────▼────────────┐
                                              │   TopStepX ProjectX API │
                                              │   (REST + SignalR)      │
                                              └─────────────────────────┘
```

### Framework Structure

The system follows a **modular, layered architecture**:

1. **Presentation Layer**: CLI, Web GUI, Strategy Executor
2. **Business Logic Layer**: Trading Bot, Strategy Manager, Risk Manager
3. **Data Access Layer**: TopStepX Adapter, Database Manager
4. **Infrastructure Layer**: WebSocket Manager, Account Tracker, Performance Metrics

### Directory Structure

```
tradeBotServer/
├── core/                    # Core business logic
│   ├── auth.py              # Authentication & token management
│   ├── websocket_manager.py # SignalR WebSocket connections
│   ├── user_hub_manager.py  # User data WebSocket hub
│   ├── strategy_executor.py # Standalone strategy runner
│   ├── backtest_executor.py # Backtesting engine
│   ├── cli_command_parser.py # CLI command parsing
│   ├── account_tracker.py   # Real-time account state tracking
│   ├── session_trade_tracker.py # Trade tracking
│   ├── market_data.py       # Market data management
│   ├── risk_management.py   # Risk & compliance
│   ├── position_management.py # Position tracking
│   ├── order_execution.py   # Order execution logic
│   └── bar_aggregator.py    # Real-time bar aggregation
│
├── strategies/              # Trading strategies
│   ├── strategy_base.py     # Base strategy class
│   ├── strategy_manager.py  # Strategy coordination
│   ├── overnight_range_strategy.py
│   ├── mean_reversion_strategy.py
│   ├── trend_following_strategy.py
│   ├── simple_candle_strategy.py
│   └── ...
│
├── infrastructure/          # Infrastructure components
│   ├── database.py          # PostgreSQL database manager
│   ├── performance_metrics.py # Performance tracking
│   ├── task_queue.py        # Task queue system
│   └── performance_timing.py # Timing utilities
│
├── gui/                     # Web interface
│   ├── master_control.html  # Master control GUI
│   ├── chart_html.py        # Chart server
│   └── api_delegator.py     # API delegation
│
├── brokers/                 # Broker adapters
│   └── topstepx_adapter.py  # TopStepX API adapter
│
├── servers/                 # Server components
│   ├── async_webhook_server.py # Webhook server
│   └── dashboard_api_server.py # Dashboard API
│
└── trading_bot.py           # Main entry point
```

---

## Core Components

### 1. TopStepXTradingBot (`trading_bot.py`)

**Purpose**: Main orchestrator that coordinates all system components.

**Key Responsibilities**:
- Authentication and token management
- Account selection and management
- Order placement and execution
- Position tracking
- Strategy coordination
- Market data aggregation
- CLI command processing

**Initialization Flow**:
1. Load environment variables
2. Initialize logging
3. Initialize database connection
4. Initialize authentication manager
5. Initialize account tracker
6. Initialize strategy manager
7. Initialize WebSocket managers
8. Initialize broker adapter
9. Authenticate with TopStepX API
10. Fetch contracts and accounts

### 2. Strategy Manager (`strategies/strategy_manager.py`)

**Purpose**: Manages multiple trading strategies, coordinates execution, and enforces global risk limits.

**Features**:
- Dynamic strategy loading
- Strategy lifecycle management (start/stop)
- Strategy state persistence (database)
- Performance metrics aggregation
- Market condition detection
- Auto-selection of strategies based on market conditions

**Registered Strategies**:
- `overnight_range`: Overnight range breakout strategy
- `mean_reversion`: Mean reversion trading
- `trend_following`: Trend following with moving averages
- `simple_candle`: Simple candle pattern strategy
- `trend_scalping`: Scalping strategy
- `simple_momentum`: Momentum-based strategy

### 3. WebSocket Manager (`core/websocket_manager.py`)

**Purpose**: Manages SignalR WebSocket connections for real-time market data.

**Features**:
- Market Hub connection (quotes, depth)
- User Hub connection (account, positions, orders)
- Automatic reconnection with exponential backoff
- Token refresh on authentication errors
- Symbol subscription management
- Event callback registration

**Connection Lifecycle**:
1. Ensure valid authentication token
2. Build WebSocket URL with token
3. Create SignalR hub connection
4. Register event handlers
5. Start connection
6. Subscribe to symbols
7. Handle reconnection on errors

### 4. Account Tracker (`core/account_tracker.py`)

**Purpose**: Real-time tracking of account state, P&L, and compliance.

**Features**:
- Real-time balance tracking
- Realized/unrealized P&L calculation
- Daily loss limit (DLL) tracking
- Maximum loss limit (MLL) tracking
- Compliance status monitoring
- Trade statistics (wins/losses)
- Database persistence

**State Updates**:
- On order fills
- On position changes
- On quote updates (for unrealized P&L)
- Scheduled EOD updates

### 5. Database Manager (`infrastructure/database.py`)

**Purpose**: PostgreSQL database operations for persistent storage.

**Features**:
- Connection pooling (2-10 connections)
- Historical bar caching
- Account state persistence
- Strategy state persistence
- Trade history storage
- Order history cache
- Performance metrics storage
- Strategy execution logging

**Schema Tables**:
- `historical_bars`: OHLCV market data
- `account_state`: Account balances and metrics
- `strategy_states`: Strategy enable/disable state
- `strategy_performance`: Strategy metrics
- `trade_history`: Completed trades
- `order_history_cache`: Cached order data
- `strategy_executions`: Strategy action logs
- `process_states`: Process tracking
- `notifications`: System notifications

### 6. TopStepX Adapter (`brokers/topstepx_adapter.py`)

**Purpose**: Abstraction layer for TopStepX ProjectX API.

**Features**:
- REST API calls (orders, positions, accounts)
- SignalR integration
- Rust hot-path optimization (10-15x faster)
- Python fallback for Rust failures
- Rate limiting
- Error handling and retries
- Response caching

**Execution Paths**:
1. **Rust Path** (preferred): 10-15ms latency
2. **Python Path** (fallback): 30-50ms latency

---

## Execution Methods

### 1. Interactive CLI Mode

**Entry Point**: `python trading_bot.py`

**Usage**:
```bash
python trading_bot.py
# Interactive prompt appears
> accounts
> switch_account 1
> market MNQ BUY 1
> positions
> strategies start overnight_range
```

**Features**:
- Real-time command execution
- Interactive account selection
- Manual order placement
- Strategy control
- Position monitoring
- Trade history viewing

### 2. Strategy Executor Mode

**Entry Point**: `python core/strategy_executor.py`

**Usage**:
```bash
# Run single strategy
python core/strategy_executor.py --strategy=overnight_range --symbols=MNQ --account_id=12694476

# Run all enabled strategies
python core/strategy_executor.py --all --account_id=12694476

# With timeframe
python core/strategy_executor.py --strategy=simple_candle --symbols=MNQ --timeframe=5m
```

**Features**:
- Standalone process for strategies
- Automatic strategy lifecycle management
- Process state tracking in database
- Health monitoring
- Auto-restart on failure

**Lifecycle**:
1. Initialize trading bot
2. Authenticate
3. Prefetch contracts (critical for strategies)
4. Load persisted strategy states
5. Start requested strategies
6. Monitor loop (30s interval)
7. Update process state in database
8. Graceful shutdown on Ctrl+C

### 3. Backtesting Mode

**Entry Point**: `python core/backtest_executor.py`

**Usage**:
```bash
# Backtest with real API data
python core/backtest_executor.py --strategy=overnight_range --symbol=MNQ --start=2024-01-01 --end=2024-12-31

# Backtest with sample data
python core/backtest_executor.py --strategy=mean_reversion --symbol=MNQ --days=30 --sample

# Backtest with CSV export
python core/backtest_executor.py --strategy=simple_candle --symbol=MNQ --csv=history_export.csv

# Monte Carlo simulation
python core/backtest_executor.py --strategy=overnight_range --symbol=MNQ --days=90 --monte-carlo=1000
```

**Features**:
- Historical data replay
- Strategy replay mode (uses actual strategy code)
- Performance metrics calculation
- Monte Carlo simulation
- Parameter optimization
- CSV export/import

### 4. Web GUI Mode

**Entry Point**: `python servers/async_webhook_server.py` or `python gui/chart_html.py`

**Usage**:
```bash
# Start webhook server (includes GUI)
python servers/async_webhook_server.py

# Or start chart server directly
python gui/chart_html.py
```

**Features**:
- Web-based master control interface
- Real-time chart updates
- Strategy control via web UI
- Order placement via web UI
- Position monitoring
- Account state dashboard

**Endpoints**:
- `/` - Master control interface
- `/api/chart/quote` - Latest quotes
- `/api/chart/positions` - Open positions
- `/api/chart/orders` - Open orders
- `/api/chart/account/state` - Account metrics
- `/api/chart/strategy/status` - Strategy states

### 5. CLI Command Mode (Non-Interactive)

**Usage**:
```bash
# Execute single command
python trading_bot.py --command="market MNQ BUY 1"

# Execute with account selection
python trading_bot.py --account_select=1 --command="positions"
```

**Features**:
- Single command execution
- Scriptable automation
- Integration with external tools

---

## Database Implementation

### Database Status: ✅ **FULLY IMPLEMENTED**

The system uses **PostgreSQL** for persistent storage with the following capabilities:

### Connection Management

- **Connection Pooling**: Thread-safe connection pool (2-10 connections)
- **Auto-reconnection**: Automatic reconnection on connection failures
- **Health Checks**: Connection health verification before use
- **Railway Support**: Automatic detection and connection to Railway PostgreSQL
- **Local Fallback**: Falls back to local PostgreSQL if Railway unavailable

### Schema Tables

#### 1. `historical_bars`
Stores OHLCV market data for caching and historical analysis.

```sql
CREATE TABLE historical_bars (
    id SERIAL PRIMARY KEY,
    symbol VARCHAR(20) NOT NULL,
    timeframe VARCHAR(10) NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    open DECIMAL(12, 4),
    high DECIMAL(12, 4),
    low DECIMAL(12, 4),
    close DECIMAL(12, 4),
    volume BIGINT,
    metadata JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(symbol, timeframe, timestamp)
);
```

**Usage**: Caches historical bars to reduce API calls. Used by strategies and backtesting.

#### 2. `account_state`
Tracks account balances, P&L, and compliance metrics.

```sql
CREATE TABLE account_state (
    account_id VARCHAR(50) PRIMARY KEY,
    account_name VARCHAR(100),
    balance DECIMAL(12, 2),
    starting_balance DECIMAL(12, 2),
    daily_pnl DECIMAL(12, 2),
    dll_remaining DECIMAL(12, 2),
    mll_remaining DECIMAL(12, 2),
    total_trades_today INT DEFAULT 0,
    winning_trades_today INT DEFAULT 0,
    losing_trades_today INT DEFAULT 0,
    metadata JSONB,
    last_updated TIMESTAMPTZ DEFAULT NOW()
);
```

**Usage**: Persists account state across restarts. Used by account tracker.

#### 3. `strategy_states`
Persists strategy enable/disable state and configuration.

```sql
CREATE TABLE strategy_states (
    account_id VARCHAR(50) NOT NULL,
    strategy_name VARCHAR(50) NOT NULL,
    enabled BOOLEAN DEFAULT FALSE,
    symbols TEXT[] DEFAULT ARRAY[]::TEXT[],
    settings JSONB,
    metadata JSONB,
    last_started TIMESTAMPTZ,
    last_stopped TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (account_id, strategy_name)
);
```

**Usage**: Remembers which strategies are enabled for each account. Auto-loaded on startup.

#### 4. `strategy_performance`
Stores strategy performance metrics over time.

```sql
CREATE TABLE strategy_performance (
    id SERIAL PRIMARY KEY,
    strategy_name VARCHAR(50) NOT NULL,
    symbol VARCHAR(20),
    timestamp TIMESTAMPTZ DEFAULT NOW(),
    total_trades INT DEFAULT 0,
    winning_trades INT DEFAULT 0,
    losing_trades INT DEFAULT 0,
    total_pnl DECIMAL(12, 2) DEFAULT 0,
    win_rate DECIMAL(5, 2),
    profit_factor DECIMAL(8, 2),
    max_drawdown DECIMAL(12, 2),
    sharpe_ratio DECIMAL(8, 2),
    ...
);
```

**Usage**: Historical performance tracking for strategy analysis.

#### 5. `trade_history`
Stores completed trades for analysis.

```sql
CREATE TABLE trade_history (
    id SERIAL PRIMARY KEY,
    account_id VARCHAR(50),
    strategy_name VARCHAR(50),
    symbol VARCHAR(20),
    side VARCHAR(10),
    quantity INT,
    entry_price DECIMAL(12, 4),
    exit_price DECIMAL(12, 4),
    pnl DECIMAL(12, 2),
    entry_time TIMESTAMPTZ,
    exit_time TIMESTAMPTZ,
    duration_seconds INT,
    metadata JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

**Usage**: Trade history for performance analysis and reporting.

#### 6. `order_history_cache`
Caches order data for faster dashboard loads.

```sql
CREATE TABLE order_history_cache (
    id SERIAL PRIMARY KEY,
    account_id VARCHAR(50) NOT NULL,
    order_data JSONB NOT NULL,
    order_timestamp TIMESTAMPTZ NOT NULL,
    cached_at TIMESTAMPTZ DEFAULT NOW()
);
```

**Usage**: Reduces API calls for order history queries.

#### 7. `strategy_executions`
Logs all strategy actions for debugging and analysis.

```sql
CREATE TABLE strategy_executions (
    id SERIAL PRIMARY KEY,
    strategy_name VARCHAR(50) NOT NULL,
    account_id VARCHAR(50),
    action VARCHAR(50) NOT NULL,
    symbol VARCHAR(20),
    side VARCHAR(10),
    quantity INT,
    price DECIMAL(12, 4),
    order_id VARCHAR(100),
    result JSONB,
    timestamp TIMESTAMPTZ DEFAULT NOW(),
    metadata JSONB
);
```

**Usage**: Audit trail of all strategy actions.

#### 8. `process_states`
Tracks running processes (strategy executor, etc.).

```sql
CREATE TABLE process_states (
    process_id VARCHAR(100) PRIMARY KEY,
    process_type VARCHAR(50) NOT NULL,
    status VARCHAR(20) NOT NULL,
    account_id VARCHAR(50),
    metadata JSONB,
    started_at TIMESTAMPTZ,
    last_heartbeat TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
```

**Usage**: Process health monitoring and coordination.

### Database Operations

**Key Methods**:
- `cache_historical_bars()`: Cache historical market data
- `get_cached_bars()`: Retrieve cached bars
- `save_account_state()`: Persist account state
- `get_account_state()`: Load account state
- `save_strategy_state()`: Persist strategy configuration
- `get_strategy_states()`: Load strategy states
- `log_strategy_execution()`: Log strategy actions
- `cache_order_history()`: Cache order data

### Database Fallback

If PostgreSQL is unavailable, the system:
- Falls back to in-memory caching
- Uses JSON file persistence (account state)
- Logs warnings but continues operation
- Gracefully degrades functionality

---

## Data Lifecycle & Flow

### Complete Data Flow Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                    DATA SOURCES                                   │
├──────────────────────┬──────────────────┬───────────────────────┤
│  TopStepX REST API   │  SignalR WebSocket│  User Input (CLI/GUI) │
└──────────┬───────────┴──────────┬───────┴───────────┬───────────┘
           │                      │                    │
           └──────────────────────┼────────────────────┘
                                  │
                    ┌─────────────▼─────────────┐
                    │   TopStepX Adapter        │
                    │   (Rust/Python)           │
                    └─────────────┬─────────────┘
                                  │
        ┌─────────────────────────┼─────────────────────────┐
        │                         │                         │
┌───────▼────────┐    ┌──────────▼──────────┐   ┌─────────▼─────────┐
│  Market Data   │    │   Order Execution   │   │  Account Tracker   │
│  Manager       │    │   Manager           │   │                    │
└───────┬────────┘    └──────────┬──────────┘   └─────────┬────────┘
        │                        │                          │
        │                        │                          │
┌───────▼────────┐    ┌──────────▼──────────┐   ┌─────────▼─────────┐
│  Quote Cache   │    │  Order Response     │   │  State Update     │
│  (In-Memory)   │    │  Processing         │   │  (Real-time)      │
└───────┬────────┘    └──────────┬──────────┘   └─────────┬────────┘
        │                        │                          │
        └────────────────────────┼──────────────────────────┘
                                 │
                    ┌────────────▼─────────────┐
                    │   Strategy Manager        │
                    │   (Signal Processing)     │
                    └─────────────┬─────────────┘
                                  │
                    ┌─────────────▼─────────────┐
                    │   PostgreSQL Database      │
                    │   (Persistence Layer)      │
                    └───────────────────────────┘
```

### Detailed Data Flow

#### 1. Market Data Flow

```
SignalR Market Hub
    ↓
WebSocket Manager (core/websocket_manager.py)
    ↓
Quote Callback Registration
    ↓
Quote Cache (In-Memory: _quote_cache)
    ↓
Strategy Analysis
    ↓
Bar Aggregator (Real-time bar creation)
    ↓
Database Cache (historical_bars table)
```

**Latency**: 50-200ms (network dependent)

#### 2. Order Execution Flow

```
User/Strategy Command
    ↓
CLI Parser / Strategy Manager
    ↓
Risk Manager (Compliance Check)
    ↓
Order Executor (core/order_execution.py)
    ↓
TopStepX Adapter (Rust/Python)
    ↓
TopStepX REST API
    ↓
Order Response
    ↓
Account Tracker (Update P&L)
    ↓
Database (trade_history, strategy_executions)
    ↓
User Hub WebSocket (Real-time update)
```

**Latency**: 10-50ms (Rust: 10-15ms, Python: 30-50ms)

#### 3. Account State Flow

```
Order Fill / Position Change
    ↓
Account Tracker (core/account_tracker.py)
    ↓
Calculate Realized P&L (from fills)
    ↓
Query Current Quotes (for unrealized P&L)
    ↓
Calculate Unrealized P&L
    ↓
Check Compliance (DLL/MLL)
    ↓
Update Account State
    ↓
Database (account_state table)
    ↓
User Hub WebSocket (Broadcast update)
```

**Update Frequency**: Real-time on fills, on-demand on queries

#### 4. Strategy Execution Flow

```
Strategy Manager (strategies/strategy_manager.py)
    ↓
Load Strategy State (from database)
    ↓
Start Strategy Loop
    ↓
Fetch Historical Data (check database cache first)
    ↓
Get Current Market Quote (from cache or SignalR)
    ↓
Strategy Analysis (strategy-specific logic)
    ↓
Generate Signal (if conditions met)
    ↓
Risk Check (compliance verification)
    ↓
Execute Order (via Order Executor)
    ↓
Log Execution (strategy_executions table)
    ↓
Update Strategy Metrics (strategy_performance table)
```

**Execution Frequency**: Strategy-dependent (typically 1-60 seconds)

#### 5. Historical Data Flow

```
Strategy/Backtest Request
    ↓
Check Database Cache (historical_bars table)
    ↓
If Cache Hit: Return cached data
    ↓
If Cache Miss: Fetch from TopStepX API
    ↓
Store in Database Cache
    ↓
Return to Strategy/Backtest
```

**Cache Strategy**: 
- Cache TTL: Indefinite (until manually cleared)
- Cache Key: `symbol + timeframe + timestamp`
- Cache Size: Unlimited (PostgreSQL handles)

#### 6. Strategy State Persistence Flow

```
Strategy Start/Stop Command
    ↓
Strategy Manager
    ↓
Update Strategy State (enabled/disabled)
    ↓
Save to Database (strategy_states table)
    ↓
On Next Startup: Load from Database
    ↓
Auto-start Enabled Strategies
```

**Persistence**: Immediate on state change

### Data Caching Strategy

**3-Tier Caching System**:

1. **In-Memory Cache** (Fastest, ~1ms)
   - Quote cache: 1-5 second TTL
   - Contract cache: Session lifetime
   - Order cache: 5 second TTL

2. **Database Cache** (Fast, ~5-20ms)
   - Historical bars: Indefinite
   - Order history: 24 hour TTL
   - Account state: Real-time updates

3. **API Fetch** (Slowest, ~50-200ms)
   - TopStepX REST API
   - SignalR WebSocket
   - Used only on cache miss

### Data Synchronization

**Real-time Updates**:
- SignalR WebSocket for market quotes
- User Hub WebSocket for account/position/order updates
- Account tracker updates on fills

**Periodic Updates**:
- Strategy execution loops (1-60s intervals)
- Account state refresh (on-demand)
- Process heartbeat (30s intervals)

**On-Demand Updates**:
- User commands (positions, orders, etc.)
- GUI refresh requests
- Strategy status checks

---

## Use Cases

### Use Case 1: Automated Strategy Trading

**Scenario**: Run automated strategies 24/7 with minimal intervention.

**Execution Method**: Strategy Executor

**Steps**:
1. Start strategy executor: `python core/strategy_executor.py --all --account_id=12694476`
2. Executor loads persisted strategy states from database
3. Auto-starts enabled strategies
4. Strategies run continuously, analyzing market and placing trades
5. Account tracker monitors compliance
6. Process state tracked in database

**Data Flow**:
- Market data → Strategy analysis → Order execution → Account tracking → Database logging

### Use Case 2: Manual Trading via CLI

**Scenario**: Manual trading with real-time market data and position monitoring.

**Execution Method**: Interactive CLI

**Steps**:
1. Start bot: `python trading_bot.py`
2. Select account: `switch_account 1`
3. Monitor positions: `positions`
4. Place orders: `market MNQ BUY 1`
5. Monitor in real-time

**Data Flow**:
- User command → CLI parser → Order execution → Account update → Display

### Use Case 3: Strategy Backtesting

**Scenario**: Validate strategy performance on historical data.

**Execution Method**: Backtest Executor

**Steps**:
1. Run backtest: `python core/backtest_executor.py --strategy=overnight_range --symbol=MNQ --start=2024-01-01 --end=2024-12-31`
2. Backtest engine loads historical data (database cache or API)
3. Replays strategy on historical bars
4. Calculates performance metrics
5. Generates report

**Data Flow**:
- Historical data (database/API) → Strategy replay → Performance calculation → Report

### Use Case 4: Web-Based Monitoring

**Scenario**: Monitor trading activity via web browser.

**Execution Method**: Web GUI Server

**Steps**:
1. Start server: `python servers/async_webhook_server.py`
2. Open browser: `http://localhost:8080`
3. View real-time charts, positions, orders
4. Control strategies via web UI
5. Place orders via web UI

**Data Flow**:
- WebSocket updates → Frontend → User interaction → API calls → Bot execution

### Use Case 5: Multi-Strategy Portfolio

**Scenario**: Run multiple strategies simultaneously on different symbols.

**Execution Method**: Strategy Manager + Strategy Executor

**Steps**:
1. Enable multiple strategies via database or CLI
2. Strategy manager coordinates execution
3. Each strategy trades independently
4. Global risk limits enforced
5. Performance tracked per strategy

**Data Flow**:
- Multiple strategies → Strategy manager → Parallel execution → Aggregated metrics

---

## Software Development Lifecycle (SDLC)

### Development Workflow

#### 1. **Planning & Design**
- Strategy requirements analysis
- Architecture design
- Database schema design
- API integration planning

#### 2. **Development**
- **Core Components**: Business logic, adapters, managers
- **Strategies**: Strategy implementation following `BaseStrategy` interface
- **Infrastructure**: Database, performance metrics, task queues
- **UI**: CLI, Web GUI

#### 3. **Testing**
- **Unit Tests**: Individual component testing
- **Integration Tests**: Component interaction testing
- **Backtesting**: Strategy validation on historical data
- **Paper Trading**: Live testing with practice accounts

#### 4. **Deployment**
- **Local Development**: Direct Python execution
- **Production**: Railway deployment with PostgreSQL
- **Monitoring**: Log files, performance metrics, database state

#### 5. **Maintenance**
- **Bug Fixes**: Issue tracking and resolution
- **Performance Optimization**: Latency improvements, caching
- **Feature Additions**: New strategies, UI enhancements
- **Database Maintenance**: Cleanup, optimization

### Code Organization

**Modular Architecture**:
- Separation of concerns (core, strategies, infrastructure)
- Dependency injection (trading_bot passed to strategies)
- Interface-based design (BaseStrategy abstract class)
- Configuration-driven (environment variables, database state)

**Version Control**:
- Git repository
- Feature branches
- Commit messages with context
- Documentation updates

### Testing Strategy

**Backtesting**:
- Historical data replay
- Strategy replay mode (uses actual strategy code)
- Performance metrics validation
- Monte Carlo simulation

**Live Testing**:
- Practice account testing
- Real-time monitoring
- Error logging
- Performance tracking

### Deployment Process

1. **Local Testing**: Test on local machine
2. **Database Setup**: Ensure PostgreSQL available (Railway or local)
3. **Environment Configuration**: Set API keys, database URLs
4. **Deployment**: Deploy to Railway or run locally
5. **Monitoring**: Monitor logs, database state, performance metrics

---

## Typical Results & Performance

### Execution Performance

**Order Placement**:
- Rust path: **10-15ms** (preferred)
- Python path: **30-50ms** (fallback)
- Success rate: >99%

**Market Data**:
- SignalR quote latency: **50-200ms** (network dependent)
- Quote cache hit rate: **>80%**
- Cache lookup: **<1ms**

**Position Queries**:
- With cache: **<10ms**
- Without cache: **20-60ms**
- Enrichment (quotes + orders): **50-200ms**

**Strategy Execution**:
- Analysis time: **1-10ms** (strategy dependent)
- Total loop time: **100-1000ms** (includes data fetching)
- Signal generation: Variable (market condition dependent)

### Database Performance

**Query Latency**:
- Cache hit: **5-20ms**
- Cache miss (API fetch): **50-200ms**
- Write operations: **10-50ms**

**Storage**:
- Historical bars: Unlimited (PostgreSQL handles)
- Account state: Minimal (one row per account)
- Trade history: Grows with trading activity
- Strategy executions: Grows with strategy activity

### System Reliability

**Uptime**:
- Auto-reconnection on network failures
- Token auto-refresh on expiration
- Graceful degradation on database unavailability
- Process health monitoring

**Error Handling**:
- Comprehensive error logging
- Fallback mechanisms (Rust → Python, Database → Memory)
- Retry logic with exponential backoff
- User-friendly error messages

### Typical Trading Results

**Strategy Performance** (varies by market conditions):
- Win rate: 40-60% (strategy dependent)
- Profit factor: 1.2-2.0 (strategy dependent)
- Average trade duration: Minutes to hours
- Daily trade frequency: 1-20 trades (strategy dependent)

**Risk Management**:
- DLL/MLL compliance: Enforced automatically
- Position sizing: Strategy-controlled
- Stop loss: Strategy-controlled
- Take profit: Strategy-controlled

---

## Summary

The TopStepX Trading Bot is a **comprehensive, production-ready automated trading system** with:

✅ **Full Database Implementation**: PostgreSQL with comprehensive schema  
✅ **Multiple Execution Methods**: CLI, Strategy Executor, Backtesting, Web GUI  
✅ **Real-time Data Flow**: SignalR WebSocket for live market data  
✅ **Modular Architecture**: Easy to extend with new strategies  
✅ **Performance Optimized**: Rust hot-paths, caching, parallel execution  
✅ **Production Ready**: Error handling, logging, monitoring, persistence  

The system is designed for **reliability, performance, and extensibility**, making it suitable for both manual trading and fully automated strategy execution.
