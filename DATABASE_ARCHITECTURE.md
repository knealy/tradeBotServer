# Database Architecture for Trading Bot

**Date:** November 7, 2025  
**Purpose:** Design scalable, fast, and reliable database structure for trading data

---

## 📊 Current State Analysis

### Current Data Storage
- **Account State**: JSON file (`.account_state.json`)
- **Historical Data Cache**: Parquet/Pickle files
- **Orders/Fills**: In-memory only (lost on restart)
- **Trades**: Computed on-the-fly from API calls
- **Logs**: Text files (`trading_bot.log`)

### Problems
1. **No persistent order/fill history** - Can't analyze past trades offline
2. **Slow trade consolidation** - Reprocesses all orders every time
3. **No analytics** - Can't efficiently query performance metrics
4. **Limited scale** - JSON files don't scale beyond small datasets
5. **No concurrent access** - Multiple processes can't safely read/write

---

## 🎯 Requirements

### Functional Requirements
1. **Order Management**
   - Store all orders (market, limit, stop, bracket)
   - Track order lifecycle (pending → filled → closed)
   - Link parent/child orders (OCO, bracket relationships)
   
2. **Fill Tracking**
   - Record every fill with timestamp, price, quantity
   - Link fills to orders
   - Track commissions and fees
   
3. **Trade History**
   - Pre-computed consolidated trades (entry/exit pairs)
   - FIFO matching already done
   - P&L calculated and stored
   
4. **Position Tracking**
   - Real-time open positions
   - Historical position snapshots
   - Entry/exit tracking
   
5. **Account State History**
   - Balance snapshots (EOD and intraday)
   - PnL history (realized, unrealized, net)
   - Compliance tracking (DLL/MLL violations)
   - Drawdown tracking
   
6. **Analytics**
   - Win rate, average win/loss
   - Drawdown curves
   - Performance by symbol, time, strategy
   - Risk metrics over time

### Non-Functional Requirements
1. **Performance**
   - Sub-10ms reads for recent data
   - Sub-100ms writes for orders/fills
   - Support 1000+ orders/day
   
2. **Reliability**
   - ACID transactions for critical data (balance, orders)
   - Atomic updates (no partial writes)
   - Backup/recovery capability
   
3. **Scalability**
   - Handle years of trading data
   - Support multiple accounts simultaneously
   - Efficient historical queries
   
4. **Portability**
   - Easy to migrate to Go/Rust later
   - Standard SQL interface
   - Platform-independent (macOS, Linux, Windows)

---

## 🔍 Database Options Comparison

### Option 1: SQLite ⭐ **RECOMMENDED**

#### Pros
- ✅ **Zero configuration** - Single file, no server setup
- ✅ **ACID compliant** - Full transaction support
- ✅ **Fast** - In-process, no network overhead
- ✅ **Portable** - Works on all platforms
- ✅ **Mature** - Battle-tested, stable
- ✅ **Python support** - Built into Python standard library
- ✅ **Go support** - Excellent Go drivers (mattn/go-sqlite3)
- ✅ **Small footprint** - ~600KB library
- ✅ **Good for analytics** - Supports complex queries, CTEs, window functions

#### Cons
- ⚠️ Limited concurrent writes (one writer at a time)
- ⚠️ Not ideal for high-frequency trading (1000s of writes/sec)
- ⚠️ File-based (less robust than client-server for network access)

#### Best For
- Single bot instance
- Moderate write volume (10-100 orders/minute)
- Local storage
- Easy migration to other languages
- **Our use case**: Perfect fit! ✅

#### Estimated Performance
- Reads: ~0.1ms (with proper indexing)
- Writes: ~1-5ms
- Complex queries: ~10-50ms
- Database size: ~100MB/year of trading data

---

### Option 2: PostgreSQL

#### Pros
- ✅ **Enterprise-grade** - Robust, proven at scale
- ✅ **Advanced features** - Partitioning, materialized views, JSONB
- ✅ **Concurrent writes** - Multiple writers supported
- ✅ **Network access** - Remote connections
- ✅ **Full-text search** - Good for log analysis
- ✅ **Time-series extensions** - TimescaleDB for time-series data

#### Cons
- ❌ **Complex setup** - Requires server installation, configuration
- ❌ **Resource heavy** - ~50MB RAM minimum
- ❌ **Overkill for single-bot** - Too much overhead
- ❌ **Portability** - Harder to move database file

#### Best For
- Multiple bot instances
- High write volume
- Distributed systems
- Team collaboration

#### Not Recommended
- Too complex for our current needs
- Consider if scaling to 10+ bot instances

---

### Option 3: DuckDB

#### Pros
- ✅ **Analytical powerhouse** - Optimized for OLAP queries
- ✅ **Fast aggregations** - 10-100x faster than SQLite for analytics
- ✅ **Columnar storage** - Efficient for large datasets
- ✅ **Zero config** - Like SQLite, embedded
- ✅ **Parquet integration** - Can query Parquet files directly
- ✅ **Python support** - Excellent Python API

#### Cons
- ⚠️ Less mature than SQLite (but growing fast)
- ⚠️ Fewer language bindings (limited Go support)
- ⚠️ Optimized for analytics, not OLTP
- ⚠️ Newer technology (higher risk)

#### Best For
- Heavy analytics workloads
- Large historical datasets
- Data science workflows
- When you need fast aggregations

#### Consider If
- You're doing complex performance analysis
- Historical data > 10GB
- Want to query Parquet caches directly

---

### Option 4: Redis + SQLite Hybrid

#### Pros
- ✅ **Fast writes** - Redis handles high-frequency updates
- ✅ **Real-time data** - Sub-millisecond latency
- ✅ **Persistent** - SQLite for historical data
- ✅ **Best of both** - Speed + durability

#### Cons
- ❌ **Complex architecture** - Two databases to manage
- ❌ **Sync overhead** - Data must be moved from Redis to SQLite
- ❌ **More code** - Dual persistence layer

#### Best For
- High-frequency trading bots
- Real-time dashboards
- When SQLite writes are too slow

#### Not Recommended Yet
- Only if single-database approach is too slow
- Adds unnecessary complexity initially

---

## ✅ Recommendation: SQLite

**Why SQLite?**
1. **Simplicity** - Zero configuration, single file
2. **Performance** - More than sufficient for our needs
3. **Portability** - Easy to backup, move, migrate
4. **Language Support** - Native Python, excellent Go support
5. **ACID** - Full transactional integrity
6. **Proven** - Used by browsers, mobile apps, embedded systems worldwide

**Perfect for:**
- Single trading bot instance
- 10-1000 orders/day
- Local storage
- Future migration to Go/Rust
- Easy backups (just copy the file!)

---

## 📐 Database Schema Design

### Core Tables

#### 1. `accounts`
Stores account information and metadata.

```sql
CREATE TABLE accounts (
    id INTEGER PRIMARY KEY,
    account_id TEXT UNIQUE NOT NULL,
    account_name TEXT NOT NULL,
    account_type TEXT NOT NULL,  -- 'evaluation', 'funded', 'practice'
    starting_balance REAL NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

#### 2. `account_snapshots`
Daily/intraday balance snapshots for tracking.

```sql
CREATE TABLE account_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id TEXT NOT NULL,
    timestamp TIMESTAMP NOT NULL,
    balance REAL NOT NULL,
    realized_pnl REAL DEFAULT 0,
    unrealized_pnl REAL DEFAULT 0,
    commissions REAL DEFAULT 0,
    fees REAL DEFAULT 0,
    highest_eod_balance REAL,
    is_eod BOOLEAN DEFAULT FALSE,  -- End-of-day snapshot
    snapshot_type TEXT DEFAULT 'intraday',  -- 'intraday', 'eod', 'manual'
    FOREIGN KEY (account_id) REFERENCES accounts(account_id),
    UNIQUE(account_id, timestamp)
);

CREATE INDEX idx_snapshots_account_time ON account_snapshots(account_id, timestamp DESC);
```

#### 3. `orders`
All orders (market, limit, stop, bracket).

```sql
CREATE TABLE orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT UNIQUE NOT NULL,  -- TopStepX order ID
    account_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    contract_id TEXT,
    side INTEGER NOT NULL,  -- 0=BUY, 1=SELL
    order_type INTEGER NOT NULL,  -- 0=Market, 1=Limit, 2=Stop, etc.
    quantity INTEGER NOT NULL,
    limit_price REAL,
    stop_price REAL,
    trail_amount REAL,
    status INTEGER NOT NULL,  -- 0=Pending, 1=Working, 2=Filled, 3=Cancelled, 4=Rejected
    filled_quantity INTEGER DEFAULT 0,
    remaining_quantity INTEGER,
    average_fill_price REAL,
    
    -- Bracket/OCO relationships
    parent_order_id TEXT,  -- For child orders in bracket
    stop_loss_order_id TEXT,  -- Link to SL order
    take_profit_order_id TEXT,  -- Link to TP order
    
    -- Timestamps
    created_at TIMESTAMP NOT NULL,
    submitted_at TIMESTAMP,
    filled_at TIMESTAMP,
    cancelled_at TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    -- Metadata
    custom_tag TEXT,
    notes TEXT,
    
    FOREIGN KEY (account_id) REFERENCES accounts(account_id),
    FOREIGN KEY (parent_order_id) REFERENCES orders(order_id)
);

CREATE INDEX idx_orders_account ON orders(account_id);
CREATE INDEX idx_orders_symbol ON orders(symbol);
CREATE INDEX idx_orders_status ON orders(status);
CREATE INDEX idx_orders_created ON orders(created_at DESC);
CREATE INDEX idx_orders_parent ON orders(parent_order_id) WHERE parent_order_id IS NOT NULL;
```

#### 4. `fills`
Individual fill executions (one order can have multiple fills).

```sql
CREATE TABLE fills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fill_id TEXT UNIQUE NOT NULL,  -- TopStepX fill ID
    order_id TEXT NOT NULL,
    account_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side INTEGER NOT NULL,  -- 0=BUY, 1=SELL
    quantity INTEGER NOT NULL,
    price REAL NOT NULL,
    commission REAL DEFAULT 0,
    fee REAL DEFAULT 0,
    execution_timestamp TIMESTAMP NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    FOREIGN KEY (order_id) REFERENCES orders(order_id),
    FOREIGN KEY (account_id) REFERENCES accounts(account_id)
);

CREATE INDEX idx_fills_order ON fills(order_id);
CREATE INDEX idx_fills_account ON fills(account_id);
CREATE INDEX idx_fills_symbol ON fills(symbol);
CREATE INDEX idx_fills_timestamp ON fills(execution_timestamp DESC);
```

#### 5. `trades`
Consolidated completed trades (entry + exit).

```sql
CREATE TABLE trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id TEXT UNIQUE NOT NULL,  -- Generated UUID
    account_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,  -- 'LONG' or 'SHORT'
    quantity INTEGER NOT NULL,
    
    -- Entry
    entry_order_id TEXT NOT NULL,
    entry_price REAL NOT NULL,
    entry_timestamp TIMESTAMP NOT NULL,
    
    -- Exit
    exit_order_id TEXT NOT NULL,
    exit_price REAL NOT NULL,
    exit_timestamp TIMESTAMP NOT NULL,
    
    -- P&L
    gross_pnl REAL NOT NULL,
    commissions REAL DEFAULT 0,
    fees REAL DEFAULT 0,
    net_pnl REAL NOT NULL,
    
    -- Metadata
    duration_seconds INTEGER,  -- Time in trade
    point_value REAL,  -- For P&L verification
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    FOREIGN KEY (account_id) REFERENCES accounts(account_id),
    FOREIGN KEY (entry_order_id) REFERENCES orders(order_id),
    FOREIGN KEY (exit_order_id) REFERENCES orders(order_id)
);

CREATE INDEX idx_trades_account ON trades(account_id);
CREATE INDEX idx_trades_symbol ON trades(symbol);
CREATE INDEX idx_trades_exit_time ON trades(exit_timestamp DESC);
CREATE INDEX idx_trades_pnl ON trades(net_pnl);
```

#### 6. `positions`
Current and historical positions.

```sql
CREATE TABLE positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id TEXT UNIQUE NOT NULL,  -- TopStepX position ID or generated
    account_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,  -- 'LONG' or 'SHORT'
    quantity INTEGER NOT NULL,
    entry_price REAL NOT NULL,
    current_price REAL,
    unrealized_pnl REAL DEFAULT 0,
    
    -- Status
    status TEXT NOT NULL DEFAULT 'open',  -- 'open', 'closed'
    opened_at TIMESTAMP NOT NULL,
    closed_at TIMESTAMP,
    
    -- Linked orders
    entry_order_id TEXT,
    stop_loss_order_id TEXT,
    take_profit_order_id TEXT,
    
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    FOREIGN KEY (account_id) REFERENCES accounts(account_id),
    FOREIGN KEY (entry_order_id) REFERENCES orders(order_id)
);

CREATE INDEX idx_positions_account ON positions(account_id);
CREATE INDEX idx_positions_status ON positions(status);
CREATE INDEX idx_positions_symbol ON positions(symbol);
```

#### 7. `compliance_events`
Track compliance violations and warnings.

```sql
CREATE TABLE compliance_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id TEXT NOT NULL,
    event_type TEXT NOT NULL,  -- 'dll_warning', 'dll_violation', 'mll_warning', 'mll_violation'
    severity TEXT NOT NULL,  -- 'info', 'warning', 'violation'
    
    -- Details
    current_balance REAL,
    daily_pnl REAL,
    trailing_loss REAL,
    dll_limit REAL,
    mll_limit REAL,
    dll_remaining REAL,
    mll_remaining REAL,
    
    message TEXT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    FOREIGN KEY (account_id) REFERENCES accounts(account_id)
);

CREATE INDEX idx_compliance_account ON compliance_events(account_id);
CREATE INDEX idx_compliance_timestamp ON compliance_events(timestamp DESC);
CREATE INDEX idx_compliance_severity ON compliance_events(severity);
```

---

## 🔧 Implementation Plan

### Phase 1: Database Setup (Day 1)
- [ ] Create `database.py` module
- [ ] Implement connection management
- [ ] Create schema initialization
- [ ] Add migration system (for future schema changes)
- [ ] Write unit tests for basic CRUD operations

### Phase 2: Order/Fill Storage (Day 2-3)
- [ ] Hook into `place_market_order`, `place_limit_order`, etc.
- [ ] Store orders immediately after API call
- [ ] Update order status on fills
- [ ] Store fills from API callbacks
- [ ] Add order history queries

### Phase 3: Trade Consolidation (Day 3-4)
- [ ] Migrate `_consolidate_orders_into_trades` to use database
- [ ] Pre-compute trades on fill events
- [ ] Store consolidated trades in `trades` table
- [ ] Add trade analytics queries
- [ ] Optimize `trades` command to use database

### Phase 4: Position Tracking (Day 4-5)
- [ ] Sync positions from API to database
- [ ] Update positions on fills
- [ ] Track unrealized P&L updates
- [ ] Add position history queries

### Phase 5: Account Snapshots (Day 5-6)
- [ ] Integrate with AccountTracker
- [ ] Store balance snapshots (intraday + EOD)
- [ ] Track compliance events
- [ ] Add performance analytics queries

### Phase 6: Analytics & Reporting (Day 6-7)
- [ ] Add pre-computed metrics (win rate, Sharpe ratio, etc.)
- [ ] Create reporting queries
- [ ] Build performance dashboard data endpoints
- [ ] Optimize slow queries with proper indexes

---

## 📝 Code Examples

### database.py (Core Module)

```python
import sqlite3
import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, List, Optional, Any
from datetime import datetime
import json

logger = logging.getLogger(__name__)

class TradingDatabase:
    """
    SQLite database manager for trading bot.
    
    Handles orders, fills, trades, positions, and account snapshots.
    """
    
    def __init__(self, db_path: str = "trading_bot.db"):
        """
        Initialize database connection.
        
        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = Path(db_path)
        self.conn = None
        self._initialize()
    
    def _initialize(self):
        """Initialize database connection and create schema if needed."""
        self.conn = sqlite3.connect(
            self.db_path,
            check_same_thread=False,  # Allow multi-threaded access
            timeout=30.0  # Wait up to 30s for locks
        )
        self.conn.row_factory = sqlite3.Row  # Return rows as dicts
        
        # Enable WAL mode for better concurrency
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        
        # Create schema if doesn't exist
        self._create_schema()
        
        logger.info(f"Database initialized: {self.db_path}")
    
    def _create_schema(self):
        """Create database schema."""
        schema_file = Path(__file__).parent / "schema.sql"
        if schema_file.exists():
            with open(schema_file) as f:
                self.conn.executescript(f.read())
        else:
            # Inline schema creation
            self._create_tables()
        self.conn.commit()
    
    def _create_tables(self):
        """Create all tables (inline if schema.sql not found)."""
        # accounts table
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS accounts (
                id INTEGER PRIMARY KEY,
                account_id TEXT UNIQUE NOT NULL,
                account_name TEXT NOT NULL,
                account_type TEXT NOT NULL,
                starting_balance REAL NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Add other tables here...
        # (Full SQL from schema design above)
    
    @contextmanager
    def transaction(self):
        """Context manager for transactions."""
        try:
            yield self.conn
            self.conn.commit()
        except Exception as e:
            self.conn.rollback()
            logger.error(f"Transaction failed: {e}")
            raise
    
    def insert_order(self, order_data: Dict[str, Any]) -> int:
        """
        Insert a new order.
        
        Args:
            order_data: Order details
            
        Returns:
            int: Order ID
        """
        with self.transaction() as conn:
            cursor = conn.execute("""
                INSERT INTO orders (
                    order_id, account_id, symbol, contract_id,
                    side, order_type, quantity, limit_price, stop_price,
                    status, created_at, custom_tag
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                order_data['order_id'],
                order_data['account_id'],
                order_data['symbol'],
                order_data.get('contract_id'),
                order_data['side'],
                order_data['order_type'],
                order_data['quantity'],
                order_data.get('limit_price'),
                order_data.get('stop_price'),
                order_data.get('status', 0),  # 0 = Pending
                order_data.get('created_at', datetime.utcnow()),
                order_data.get('custom_tag')
            ))
            return cursor.lastrowid
    
    def update_order_status(self, order_id: str, status: int, filled_at: Optional[datetime] = None):
        """Update order status."""
        with self.transaction() as conn:
            conn.execute("""
                UPDATE orders
                SET status = ?, filled_at = ?, updated_at = CURRENT_TIMESTAMP
                WHERE order_id = ?
            """, (status, filled_at, order_id))
    
    def insert_fill(self, fill_data: Dict[str, Any]) -> int:
        """Insert a fill execution."""
        with self.transaction() as conn:
            cursor = conn.execute("""
                INSERT INTO fills (
                    fill_id, order_id, account_id, symbol,
                    side, quantity, price, commission, fee,
                    execution_timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                fill_data['fill_id'],
                fill_data['order_id'],
                fill_data['account_id'],
                fill_data['symbol'],
                fill_data['side'],
                fill_data['quantity'],
                fill_data['price'],
                fill_data.get('commission', 0),
                fill_data.get('fee', 0),
                fill_data['execution_timestamp']
            ))
            return cursor.lastrowid
    
    def get_orders_by_account(self, account_id: str, limit: int = 100) -> List[Dict]:
        """Get recent orders for an account."""
        cursor = self.conn.execute("""
            SELECT * FROM orders
            WHERE account_id = ?
            ORDER BY created_at DESC
            LIMIT ?
        """, (account_id, limit))
        return [dict(row) for row in cursor.fetchall()]
    
    def get_trades_by_account(self, account_id: str, start_date: Optional[datetime] = None,
                              end_date: Optional[datetime] = None) -> List[Dict]:
        """Get consolidated trades for an account."""
        query = """
            SELECT * FROM trades
            WHERE account_id = ?
        """
        params = [account_id]
        
        if start_date:
            query += " AND exit_timestamp >= ?"
            params.append(start_date)
        
        if end_date:
            query += " AND exit_timestamp <= ?"
            params.append(end_date)
        
        query += " ORDER BY exit_timestamp DESC"
        
        cursor = self.conn.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]
    
    def close(self):
        """Close database connection."""
        if self.conn:
            self.conn.close()
            logger.info("Database connection closed")
```

---

## 🚀 Migration Path

### Step 1: Add Database (Non-Breaking)
- Create `database.py` module
- Initialize database on bot startup
- **Don't remove existing code yet**
- Dual-write: Write to both database and existing systems

### Step 2: Validate Data (Parallel Run)
- Run bot with database for 1-2 days
- Compare database vs. in-memory data
- Fix any discrepancies
- Verify performance (latency < 100ms)

### Step 3: Read from Database
- Switch `trades` command to use database
- Switch `account_state` to use database snapshots
- Verify output matches previous version

### Step 4: Remove Legacy Code
- Remove in-memory order tracking
- Remove on-the-fly trade consolidation
- Keep JSON account state as backup (for now)

### Step 5: Advanced Features
- Add analytics queries
- Build performance dashboard
- Implement backup/restore
- Add data export (CSV, JSON)

---

## 📊 Expected Benefits

### Performance
- **Trades command**: 500ms → 10ms (50x faster!)
- **Order history**: Always available (no re-fetch from API)
- **Analytics**: Complex queries in <100ms

### Reliability
- **Persistent data**: Survives restarts
- **Atomic updates**: No data corruption
- **Backup**: Simple file copy

### Features
- **Historical analysis**: Query any past data
- **Performance metrics**: Sharpe ratio, drawdown curves
- **Compliance audit**: Full violation history
- **Reporting**: Daily/weekly/monthly summaries

---

## 🎯 Success Metrics

- [ ] Database initialization < 100ms
- [ ] Order insert < 10ms
- [ ] Fill insert < 10ms
- [ ] Trades query (30 days) < 50ms
- [ ] Database size < 100MB/year
- [ ] Zero data loss (ACID compliance)
- [ ] Easy Go migration (standard SQL)

---

## 🔒 Backup Strategy

### Automated Backups
```python
def backup_database(db_path: str, backup_dir: str = "./backups"):
    """Create timestamped backup of database."""
    from shutil import copy2
    from datetime import datetime
    
    backup_path = Path(backup_dir)
    backup_path.mkdir(exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_file = backup_path / f"trading_bot_{timestamp}.db"
    
    copy2(db_path, backup_file)
    logger.info(f"Database backed up to {backup_file}")
    
    # Clean old backups (keep last 30 days)
    cleanup_old_backups(backup_path, days=30)
```

### Schedule
- **Hourly**: During active trading
- **Daily**: End-of-day (after EOD update)
- **Weekly**: Full backup with compression
- **Monthly**: Archive to external storage

---

## 📚 Next Steps

1. **Review & Approve** this architecture
2. **Create `database.py`** module with schema
3. **Write unit tests** for database operations
4. **Integrate** with existing bot (dual-write)
5. **Test & Validate** for 2-3 days
6. **Switch** to database-first
7. **Remove** legacy code

---

## ❓ Questions to Consider

1. **Backup location**: Local only or cloud (S3, Dropbox)?
2. **Data retention**: Keep all data or purge old data (1 year+)?
3. **Export format**: Need CSV/JSON export for external analysis?
4. **Multi-account**: Separate database per account or shared?

---

**Ready to implement? Let's start with Phase 1!** 🚀

