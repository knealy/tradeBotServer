"""
PostgreSQL Database Module for Trading Bot

Provides persistent storage for:
- Historical market data (bars/candles)
- Account state and balance tracking
- Strategy performance metrics
- API performance metrics

Uses connection pooling for efficiency and supports Railway's PostgreSQL.
"""

import os
import logging
import psycopg2
from psycopg2 import pool, sql
from psycopg2.extras import RealDictCursor, execute_values
from typing import List, Dict, Optional, Any
from datetime import datetime, timezone
from contextlib import contextmanager
import json

logger = logging.getLogger(__name__)


class DatabaseManager:
    """
    Manages PostgreSQL database connections and operations.
    
    Features:
    - Connection pooling for efficiency
    - Automatic schema creation/migration
    - Thread-safe operations
    - Railway PostgreSQL compatible
    """
    
    def __init__(self):
        """Initialize database manager with connection pool."""
        self.pool = None
        self._initialize_pool()
        self._initialize_schema()
        logger.info("✅ Database manager initialized")
    
    def _get_connection_params(self) -> Dict[str, str]:
        """
        Get database connection parameters from environment.
        
        Automatically selects the correct database URL:
        - On Railway: Uses DATABASE_URL (internal)
        - Locally: Uses PUBLIC_DATABASE_URL if DATABASE_URL is internal-only
        
        Supports both Railway's DATABASE_URL and individual params.
        
        Returns:
            Dict: Connection parameters
        """
        # Check if running on Railway (multiple ways to detect)
        is_railway = (
            os.getenv('RAILWAY_ENVIRONMENT') is not None or
            os.getenv('RAILWAY') is not None or
            os.getenv('PORT') is not None  # Railway always sets PORT
        )
        
        # Debug logging for Railway detection
        logger.debug(f"Railway detection: RAILWAY_ENV={os.getenv('RAILWAY_ENVIRONMENT')}, RAILWAY={os.getenv('RAILWAY')}, PORT={os.getenv('PORT')}, is_railway={is_railway}")
        
        # Get database URLs
        database_url = os.getenv('DATABASE_URL')
        public_database_url = os.getenv('PUBLIC_DATABASE_URL')
        
        # Debug logging for DATABASE_URL (masked for security)
        if database_url:
            masked_url = database_url[:20] + "..." + database_url[-10:] if len(database_url) > 30 else "***"
            logger.debug(f"DATABASE_URL present: {masked_url} (length: {len(database_url)})")
        else:
            logger.warning("⚠️  DATABASE_URL not found in environment variables")
        
        # Check if DATABASE_URL is a Railway variable reference that wasn't resolved
        if database_url and database_url.startswith('${{') and database_url.endswith('}}'):
            logger.error(f"❌ DATABASE_URL appears to be unresolved Railway variable reference: {database_url}")
            logger.error("   Railway should resolve ${{Postgres.DATABASE_URL}} automatically.")
            logger.error("   FIX: In Railway Variables tab, remove the manual DATABASE_URL variable.")
            logger.error("   Railway automatically provides DATABASE_URL when Postgres service is added.")
            logger.error("   If you must set it manually, use the actual connection string, not the variable reference.")
            # Fall through to use individual params as fallback
        
        # Choose the correct URL
        if database_url and not database_url.startswith('${{'):
            # If on Railway, always use internal DATABASE_URL
            if is_railway:
                logger.info(f"Using DATABASE_URL (Railway detected: RAILWAY_ENV={os.getenv('RAILWAY_ENVIRONMENT')}, PORT={os.getenv('PORT')})")
                return {'dsn': database_url}
            
            # If local, check if DATABASE_URL is internal-only
            # Internal URLs contain "railway.internal" or similar internal hostnames
            is_internal = (
                'railway.internal' in database_url or
                'containers-us-west' in database_url and 'proxy.rlwy.net' not in database_url
            )
            
            if is_internal and public_database_url:
                # Use public URL for local access
                logger.info("Using PUBLIC_DATABASE_URL (local access to Railway)")
                return {'dsn': public_database_url}
            elif is_internal and not public_database_url:
                # Internal URL but no public URL - warn and try anyway
                logger.warning("DATABASE_URL appears to be internal-only, but PUBLIC_DATABASE_URL not set. "
                             "This may fail from local machine.")
                return {'dsn': database_url}
            else:
                # Not internal, use as-is (could be local PostgreSQL or already public)
                logger.info("Using DATABASE_URL from environment")
                return {'dsn': database_url}
        
        # Fallback to individual parameters
        params = {
            'host': os.getenv('POSTGRES_HOST', 'localhost'),
            'port': int(os.getenv('POSTGRES_PORT', '5432')),
            'database': os.getenv('POSTGRES_DB', 'trading_bot'),
            'user': os.getenv('POSTGRES_USER', 'postgres'),
            'password': os.getenv('POSTGRES_PASSWORD', ''),
        }
        
        logger.info(f"Using individual PostgreSQL params: {params['host']}:{params['port']}/{params['database']}")
        return params
    
    def _initialize_pool(self):
        """Create connection pool for efficient database access."""
        try:
            params = self._get_connection_params()
            
            # Create connection pool (min 2, max 10 connections)
            self.pool = psycopg2.pool.ThreadedConnectionPool(
                minconn=2,
                maxconn=10,
                **params
            )
            
            logger.info("✅ Database connection pool created")
        except Exception as e:
            logger.error(f"❌ Failed to create database pool: {e}")
            raise
    
    @contextmanager
    def get_connection(self):
        """
        Context manager for database connections with health check.
        
        Usage:
            with db.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT ...")
        """
        conn = None
        try:
            conn = self.pool.getconn()
            
            # Health check: Test if connection is still alive
            try:
                conn.isolation_level  # Quick check without executing query
            except (psycopg2.InterfaceError, psycopg2.OperationalError):
                # Connection is dead, close it and get a new one
                logger.warning("⚠️ Stale database connection detected, reconnecting...")
                try:
                    conn.close()
                except:
                    pass
                conn = self.pool.getconn()
            
            yield conn
            conn.commit()
        except Exception as e:
            if conn:
                try:
                    conn.rollback()
                except:
                    # Rollback might fail if connection is already closed
                    pass
            logger.error(f"Database error: {e}")
            raise
        finally:
            if conn:
                try:
                    self.pool.putconn(conn)
                except:
                    # Connection might be already closed, that's ok
                    pass
    
    def _initialize_schema(self):
        """Create database schema if it doesn't exist."""
        logger.info("🔨 Initializing database schema...")
        
        schema_sql = """
        -- Historical market data (OHLCV bars)
        CREATE TABLE IF NOT EXISTS historical_bars (
            id SERIAL PRIMARY KEY,
            symbol VARCHAR(20) NOT NULL,
            timeframe VARCHAR(10) NOT NULL,
            timestamp TIMESTAMPTZ NOT NULL,
            open DECIMAL(12, 4),
            high DECIMAL(12, 4),
            low DECIMAL(12, 4),
            close DECIMAL(12, 4),
            volume BIGINT,
            metadata JSONB,  -- Store additional data (bid/ask, etc.)
            created_at TIMESTAMPTZ DEFAULT NOW(),
            UNIQUE(symbol, timeframe, timestamp)
        );
        
        -- Indexes for fast lookups
        CREATE INDEX IF NOT EXISTS idx_bars_lookup 
            ON historical_bars(symbol, timeframe, timestamp DESC);
        CREATE INDEX IF NOT EXISTS idx_bars_symbol_timeframe 
            ON historical_bars(symbol, timeframe);
        CREATE INDEX IF NOT EXISTS idx_bars_created 
            ON historical_bars(created_at);
        
        -- Account state tracking
        CREATE TABLE IF NOT EXISTS account_state (
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
            metadata JSONB,  -- Additional account data
            last_updated TIMESTAMPTZ DEFAULT NOW()
        );
        
        -- Strategy performance metrics
        CREATE TABLE IF NOT EXISTS strategy_performance (
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
            avg_win DECIMAL(12, 2),
            avg_loss DECIMAL(12, 2),
            best_trade DECIMAL(12, 2),
            worst_trade DECIMAL(12, 2),
            metadata JSONB  -- Additional metrics
        );
        
        CREATE INDEX IF NOT EXISTS idx_strategy_name 
            ON strategy_performance(strategy_name, timestamp DESC);
        CREATE INDEX IF NOT EXISTS idx_strategy_symbol 
            ON strategy_performance(symbol, timestamp DESC);
        
        -- API performance metrics
        CREATE TABLE IF NOT EXISTS api_metrics (
            id SERIAL PRIMARY KEY,
            endpoint VARCHAR(200) NOT NULL,
            method VARCHAR(10) NOT NULL,
            duration_ms DECIMAL(10, 2),
            status_code INT,
            success BOOLEAN,
            error_message TEXT,
            timestamp TIMESTAMPTZ DEFAULT NOW()
        );
        
        CREATE INDEX IF NOT EXISTS idx_api_endpoint 
            ON api_metrics(endpoint, timestamp DESC);
        CREATE INDEX IF NOT EXISTS idx_api_timestamp 
            ON api_metrics(timestamp DESC);
        
        -- Trade history (for detailed tracking)
        CREATE TABLE IF NOT EXISTS trade_history (
            id SERIAL PRIMARY KEY,
            account_id VARCHAR(50),
            strategy_name VARCHAR(50),
            symbol VARCHAR(20),
            side VARCHAR(10),  -- BUY or SELL
            quantity INT,
            entry_price DECIMAL(12, 4),
            exit_price DECIMAL(12, 4),
            pnl DECIMAL(12, 2),
            entry_time TIMESTAMPTZ,
            exit_time TIMESTAMPTZ,
            duration_seconds INT,
            metadata JSONB,  -- Order IDs, stop/target prices, etc.
            created_at TIMESTAMPTZ DEFAULT NOW()
        );
        
        -- Order history cache (raw API responses for faster dashboard loads)
        CREATE TABLE IF NOT EXISTS order_history_cache (
            id SERIAL PRIMARY KEY,
            account_id VARCHAR(50) NOT NULL,
            order_data JSONB NOT NULL,
            order_timestamp TIMESTAMPTZ NOT NULL,
            cached_at TIMESTAMPTZ DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS idx_order_cache_account_time 
            ON order_history_cache(account_id, order_timestamp DESC);
        CREATE INDEX IF NOT EXISTS idx_order_cache_cached_at 
            ON order_history_cache(cached_at);
        -- Composite index for fast lookups
        CREATE INDEX IF NOT EXISTS idx_order_cache_lookup
            ON order_history_cache(account_id, order_timestamp DESC, cached_at);
        
        CREATE INDEX IF NOT EXISTS idx_trades_account 
            ON trade_history(account_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_trades_strategy 
            ON trade_history(strategy_name, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_trades_symbol 
            ON trade_history(symbol, created_at DESC);
        
        -- Cache metadata (track what's cached and when)
        CREATE TABLE IF NOT EXISTS cache_metadata (
            cache_key VARCHAR(200) PRIMARY KEY,
            cache_type VARCHAR(50),  -- 'historical_bars', 'account', etc.
            last_updated TIMESTAMPTZ DEFAULT NOW(),
            expires_at TIMESTAMPTZ,
            hit_count INT DEFAULT 0,
            metadata JSONB
        );
        
        CREATE INDEX IF NOT EXISTS idx_cache_type 
            ON cache_metadata(cache_type, last_updated DESC);

        -- Strategy state persistence
        CREATE TABLE IF NOT EXISTS strategy_states (
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
        CREATE INDEX IF NOT EXISTS idx_strategy_states_account
            ON strategy_states(account_id);

        -- Dashboard/UI settings persistence
        CREATE TABLE IF NOT EXISTS dashboard_settings (
            account_id VARCHAR(50) PRIMARY KEY,
            settings JSONB NOT NULL,
            updated_at TIMESTAMPTZ DEFAULT NOW()
        );
        
        -- Notifications table for server-side notification tracking
        CREATE TABLE IF NOT EXISTS notifications (
            id SERIAL PRIMARY KEY,
            account_id VARCHAR(50) NOT NULL,
            notification_type VARCHAR(50) NOT NULL,
            message TEXT NOT NULL,
            level VARCHAR(20) DEFAULT 'info',  -- 'info', 'success', 'warning', 'error'
            meta JSONB,
            created_at TIMESTAMPTZ DEFAULT NOW()
        );
        
        CREATE INDEX IF NOT EXISTS idx_notifications_account 
            ON notifications(account_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_notifications_type 
            ON notifications(notification_type, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_notifications_level 
            ON notifications(level, created_at DESC);
        """
        
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(schema_sql)
            logger.info("✅ Database schema initialized")
        except Exception as e:
            logger.error(f"❌ Failed to initialize schema: {e}")
            raise
    
    # ==================== Historical Data Methods ====================
    
    def cache_historical_bars(self, symbol: str, timeframe: str, bars: List[Dict]) -> int:
        """
        Cache historical bars in database.
        
        Args:
            symbol: Trading symbol
            timeframe: Timeframe (e.g., "1m", "5m", "1h")
            bars: List of OHLCV bar dictionaries
        
        Returns:
            int: Number of bars cached
        """
        if not bars:
            return 0
        
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    # Prepare data for bulk insert
                    values = []
                    for bar in bars:
                        timestamp = bar.get('timestamp') or bar.get('time')
                        if not timestamp:
                            continue
                        
                        # Convert timestamp string to datetime if needed
                        if isinstance(timestamp, str):
                            try:
                                timestamp = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                            except:
                                continue
                        
                        # Extract additional metadata
                        metadata = {
                            k: v for k, v in bar.items() 
                            if k not in ['symbol', 'timeframe', 'timestamp', 'time', 
                                       'open', 'high', 'low', 'close', 'volume']
                        }
                        
                        values.append((
                            symbol,
                            timeframe,
                            timestamp,
                            bar.get('open'),
                            bar.get('high'),
                            bar.get('low'),
                            bar.get('close'),
                            bar.get('volume'),
                            json.dumps(metadata) if metadata else None
                        ))
                    
                    if not values:
                        return 0
                    
                    # Use ON CONFLICT to handle duplicates
                    insert_sql = """
                        INSERT INTO historical_bars 
                        (symbol, timeframe, timestamp, open, high, low, close, volume, metadata)
                        VALUES %s
                        ON CONFLICT (symbol, timeframe, timestamp) 
                        DO UPDATE SET 
                            open = EXCLUDED.open,
                            high = EXCLUDED.high,
                            low = EXCLUDED.low,
                            close = EXCLUDED.close,
                            volume = EXCLUDED.volume,
                            metadata = EXCLUDED.metadata,
                            created_at = NOW()
                    """
                    
                    execute_values(cur, insert_sql, values)
                    
                    logger.info(f"✅ Cached {len(values)} bars for {symbol} {timeframe}")
                    return len(values)
        
        except Exception as e:
            logger.error(f"❌ Failed to cache historical bars: {e}")
            return 0
    
    def get_cached_bars(self, symbol: str, timeframe: str, 
                       start_time: Optional[datetime] = None,
                       end_time: Optional[datetime] = None,
                       limit: int = 1000) -> List[Dict]:
        """
        Retrieve cached historical bars from database.
        
        Args:
            symbol: Trading symbol
            timeframe: Timeframe
            start_time: Optional start time (UTC)
            end_time: Optional end time (UTC)
            limit: Maximum number of bars to return
        
        Returns:
            List[Dict]: Cached bars in standard format
        """
        try:
            with self.get_connection() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    # Build query based on time constraints
                    if start_time and end_time:
                        query = """
                            SELECT symbol, timeframe, timestamp, 
                                   open, high, low, close, volume, metadata
                            FROM historical_bars
                            WHERE symbol = %s AND timeframe = %s
                            AND timestamp >= %s AND timestamp <= %s
                            ORDER BY timestamp ASC
                            LIMIT %s
                        """
                        cur.execute(query, (symbol, timeframe, start_time, end_time, limit))
                    elif start_time:
                        query = """
                            SELECT symbol, timeframe, timestamp, 
                                   open, high, low, close, volume, metadata
                            FROM historical_bars
                            WHERE symbol = %s AND timeframe = %s
                            AND timestamp >= %s
                            ORDER BY timestamp ASC
                            LIMIT %s
                        """
                        cur.execute(query, (symbol, timeframe, start_time, limit))
                    else:
                        # Get most recent N bars
                        query = """
                            SELECT symbol, timeframe, timestamp, 
                                   open, high, low, close, volume, metadata
                            FROM historical_bars
                            WHERE symbol = %s AND timeframe = %s
                            ORDER BY timestamp DESC
                            LIMIT %s
                        """
                        cur.execute(query, (symbol, timeframe, limit))
                    
                    rows = cur.fetchall()
                    
                    # Convert to standard format
                    bars = []
                    for row in rows:
                        bar = {
                            'symbol': row['symbol'],
                            'timeframe': row['timeframe'],
                            'timestamp': row['timestamp'].isoformat(),
                            'time': row['timestamp'].isoformat(),
                            'open': float(row['open']) if row['open'] else None,
                            'high': float(row['high']) if row['high'] else None,
                            'low': float(row['low']) if row['low'] else None,
                            'close': float(row['close']) if row['close'] else None,
                            'volume': int(row['volume']) if row['volume'] else None,
                        }
                        
                        # Add metadata if present
                        if row['metadata']:
                            bar.update(row['metadata'])
                        
                        bars.append(bar)
                    
                    # If we fetched in DESC order (no start_time), reverse to get ASC
                    if not start_time:
                        bars.reverse()
                    
                    logger.debug(f"📥 Retrieved {len(bars)} cached bars for {symbol} {timeframe}")
                    return bars
        
        except Exception as e:
            logger.error(f"❌ Failed to retrieve cached bars: {e}")
            return []
    
    def get_cache_coverage(self, symbol: str, timeframe: str) -> Dict:
        """
        Get information about cache coverage for a symbol/timeframe.
        
        Returns:
            Dict: Coverage info (oldest, newest, count)
        """
        try:
            with self.get_connection() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    query = """
                        SELECT 
                            COUNT(*) as bar_count,
                            MIN(timestamp) as oldest_bar,
                            MAX(timestamp) as newest_bar,
                            MAX(created_at) as last_cached
                        FROM historical_bars
                        WHERE symbol = %s AND timeframe = %s
                    """
                    cur.execute(query, (symbol, timeframe))
                    result = cur.fetchone()
                    
                    if result and result['bar_count'] > 0:
                        return {
                            'cached': True,
                            'bar_count': result['bar_count'],
                            'oldest_bar': result['oldest_bar'].isoformat() if result['oldest_bar'] else None,
                            'newest_bar': result['newest_bar'].isoformat() if result['newest_bar'] else None,
                            'last_cached': result['last_cached'].isoformat() if result['last_cached'] else None
                        }
                    else:
                        return {'cached': False, 'bar_count': 0}
        
        except Exception as e:
            logger.error(f"❌ Failed to get cache coverage: {e}")
            return {'cached': False, 'bar_count': 0}
    
    # ==================== Account State Methods ====================
    
    def save_account_state(self, account_id: str, state: Dict) -> bool:
        """
        Save account state to database.
        
        Args:
            account_id: Account ID
            state: Account state dictionary
        
        Returns:
            bool: Success
        """
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    # Extract standard fields
                    metadata = {k: v for k, v in state.items() 
                              if k not in ['account_id', 'account_name', 'balance', 
                                         'starting_balance', 'daily_pnl', 'dll_remaining',
                                         'mll_remaining', 'total_trades_today', 
                                         'winning_trades_today', 'losing_trades_today']}
                    
                    upsert_sql = """
                        INSERT INTO account_state 
                        (account_id, account_name, balance, starting_balance, daily_pnl, 
                         dll_remaining, mll_remaining, total_trades_today, 
                         winning_trades_today, losing_trades_today, metadata, last_updated)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                        ON CONFLICT (account_id) 
                        DO UPDATE SET
                            account_name = EXCLUDED.account_name,
                            balance = EXCLUDED.balance,
                            starting_balance = EXCLUDED.starting_balance,
                            daily_pnl = EXCLUDED.daily_pnl,
                            dll_remaining = EXCLUDED.dll_remaining,
                            mll_remaining = EXCLUDED.mll_remaining,
                            total_trades_today = EXCLUDED.total_trades_today,
                            winning_trades_today = EXCLUDED.winning_trades_today,
                            losing_trades_today = EXCLUDED.losing_trades_today,
                            metadata = EXCLUDED.metadata,
                            last_updated = NOW()
                    """
                    
                    cur.execute(upsert_sql, (
                        account_id,
                        state.get('account_name'),
                        state.get('balance'),
                        state.get('starting_balance'),
                        state.get('daily_pnl'),
                        state.get('dll_remaining'),
                        state.get('mll_remaining'),
                        state.get('total_trades_today', 0),
                        state.get('winning_trades_today', 0),
                        state.get('losing_trades_today', 0),
                        json.dumps(metadata) if metadata else None
                    ))
                    
                    logger.debug(f"✅ Saved account state for {account_id}")
                    return True
        
        except Exception as e:
            logger.error(f"❌ Failed to save account state: {e}")
            return False
    
    def get_account_state(self, account_id: str) -> Optional[Dict]:
        """
        Retrieve account state from database.
        
        Args:
            account_id: Account ID
        
        Returns:
            Optional[Dict]: Account state or None
        """
        try:
            with self.get_connection() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    query = """
                        SELECT * FROM account_state WHERE account_id = %s
                    """
                    cur.execute(query, (account_id,))
                    row = cur.fetchone()
                    
                    if row:
                        state = dict(row)
                        # Parse metadata if present
                        if state.get('metadata'):
                            state.update(state['metadata'])
                        
                        # Convert decimals to floats
                        for key in ['balance', 'starting_balance', 'daily_pnl', 
                                  'dll_remaining', 'mll_remaining']:
                            if state.get(key) is not None:
                                state[key] = float(state[key])
                        
                        logger.debug(f"📥 Retrieved account state for {account_id}")
                        return state
                    
                    return None
        
        except Exception as e:
            logger.error(f"❌ Failed to retrieve account state: {e}")
            return None
    
    # ==================== Strategy State Methods ====================
    
    def save_strategy_state(
        self,
        account_id: str,
        strategy_name: str,
        enabled: bool,
        symbols: Optional[List[str]] = None,
        settings: Optional[Dict] = None,
        metadata: Optional[Dict] = None,
        last_started: Optional[datetime] = None,
        last_stopped: Optional[datetime] = None,
    ) -> bool:
        """Persist strategy toggle/configuration state for an account."""
        if not account_id:
            logger.warning("⚠️  Cannot save strategy state without account_id")
            return False
        
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    upsert_sql = """
                        INSERT INTO strategy_states
                        (account_id, strategy_name, enabled, symbols, settings, metadata, last_started, last_stopped, updated_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
                        ON CONFLICT (account_id, strategy_name)
                        DO UPDATE SET
                            enabled = EXCLUDED.enabled,
                            symbols = EXCLUDED.symbols,
                            settings = EXCLUDED.settings,
                            metadata = EXCLUDED.metadata,
                            last_started = COALESCE(EXCLUDED.last_started, strategy_states.last_started),
                            last_stopped = COALESCE(EXCLUDED.last_stopped, strategy_states.last_stopped),
                            updated_at = NOW()
                    """
                    
                    cur.execute(
                        upsert_sql,
                        (
                            account_id,
                            strategy_name,
                            enabled,
                            symbols if symbols is not None else None,
                            json.dumps(settings) if settings else None,
                            json.dumps(metadata) if metadata else None,
                            last_started,
                            last_stopped,
                        ),
                    )
                    
                    logger.debug(f"💾 Saved strategy state for {strategy_name} ({account_id}) -> enabled={enabled}")
                    return True
        except Exception as e:
            logger.error(f"❌ Failed to save strategy state ({strategy_name}): {e}")
            return False
    
    def get_strategy_states(self, account_id: str) -> Dict[str, Dict]:
        """Retrieve strategy state map keyed by strategy name."""
        states: Dict[str, Dict] = {}
        
        if not account_id:
            return states
        
        try:
            with self.get_connection() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(
                        """
                        SELECT strategy_name, enabled, symbols, settings, metadata,
                               last_started, last_stopped, created_at, updated_at
                        FROM strategy_states
                        WHERE account_id = %s
                        """,
                        (account_id,),
                    )
                    
                    for row in cur.fetchall():
                        name = row['strategy_name']
                        states[name] = {
                            "enabled": row['enabled'],
                            "symbols": list(row['symbols']) if row.get('symbols') else [],
                            "settings": row['settings'] or {},
                            "metadata": row['metadata'] or {},
                            "last_started": row['last_started'].isoformat() if row.get('last_started') else None,
                            "last_stopped": row['last_stopped'].isoformat() if row.get('last_stopped') else None,
                            "created_at": row['created_at'].isoformat() if row.get('created_at') else None,
                            "updated_at": row['updated_at'].isoformat() if row.get('updated_at') else None,
                        }
            return states
        except Exception as e:
            logger.error(f"❌ Failed to fetch strategy states for account {account_id}: {e}")
            return states
    
    def get_strategy_state(self, account_id: str, strategy_name: str) -> Optional[Dict]:
        """Retrieve a single strategy state."""
        states = self.get_strategy_states(account_id)
        return states.get(strategy_name)
    
    # ==================== Dashboard Settings Methods ====================
    
    def save_dashboard_settings(self, settings: Dict, account_id: Optional[str] = None) -> bool:
        """Persist dashboard/settings preferences."""
        key = account_id or "__global__"
        
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    upsert_sql = """
                        INSERT INTO dashboard_settings (account_id, settings, updated_at)
                        VALUES (%s, %s, NOW())
                        ON CONFLICT (account_id)
                        DO UPDATE SET
                            settings = EXCLUDED.settings,
                            updated_at = NOW()
                    """
                    cur.execute(upsert_sql, (key, json.dumps(settings)))
                    logger.debug(f"💾 Saved dashboard settings for {key}")
                    return True
        except Exception as e:
            logger.error(f"❌ Failed to save dashboard settings ({key}): {e}")
            return False
    
    def get_dashboard_settings(self, account_id: Optional[str] = None) -> Dict:
        """Retrieve dashboard/settings preferences."""
        key = account_id or "__global__"
        
        try:
            with self.get_connection() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(
                        """
                        SELECT settings, updated_at
                        FROM dashboard_settings
                        WHERE account_id = %s
                        """,
                        (key,),
                    )
                    row = cur.fetchone()
                    if row:
                        result = row['settings'] or {}
                        if row.get('updated_at'):
                            result['_updated_at'] = row['updated_at'].isoformat()
                        return result
        except Exception as e:
            logger.error(f"❌ Failed to load dashboard settings ({key}): {e}")
        
        return {}
    
    # ==================== Strategy Performance Methods ====================
    
    def save_strategy_metrics(self, strategy_name: str, metrics: Dict) -> bool:
        """
        Save strategy performance metrics.
        
        Args:
            strategy_name: Strategy name
            metrics: Metrics dictionary
        
        Returns:
            bool: Success
        """
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    # Extract additional metadata
                    metadata = {k: v for k, v in metrics.items() 
                              if k not in ['symbol', 'total_trades', 'winning_trades',
                                         'losing_trades', 'total_pnl', 'win_rate',
                                         'profit_factor', 'max_drawdown', 'sharpe_ratio',
                                         'avg_win', 'avg_loss', 'best_trade', 'worst_trade']}
                    
                    insert_sql = """
                        INSERT INTO strategy_performance
                        (strategy_name, symbol, total_trades, winning_trades, losing_trades,
                         total_pnl, win_rate, profit_factor, max_drawdown, sharpe_ratio,
                         avg_win, avg_loss, best_trade, worst_trade, metadata)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """
                    
                    cur.execute(insert_sql, (
                        strategy_name,
                        metrics.get('symbol'),
                        metrics.get('total_trades', 0),
                        metrics.get('winning_trades', 0),
                        metrics.get('losing_trades', 0),
                        metrics.get('total_pnl', 0),
                        metrics.get('win_rate'),
                        metrics.get('profit_factor'),
                        metrics.get('max_drawdown'),
                        metrics.get('sharpe_ratio'),
                        metrics.get('avg_win'),
                        metrics.get('avg_loss'),
                        metrics.get('best_trade'),
                        metrics.get('worst_trade'),
                        json.dumps(metadata) if metadata else None
                    ))
                    
                    logger.debug(f"✅ Saved metrics for strategy {strategy_name}")
                    return True
        
        except Exception as e:
            logger.error(f"❌ Failed to save strategy metrics: {e}")
            return False
    
    # ==================== API Metrics Methods ====================
    
    def save_api_metric(self, endpoint: str, method: str, duration_ms: float,
                       status_code: Optional[int], success: bool,
                       error_message: Optional[str] = None) -> bool:
        """
        Save API performance metric.
        
        Args:
            endpoint: API endpoint
            method: HTTP method
            duration_ms: Duration in milliseconds
            status_code: HTTP status code
            success: Whether call succeeded
            error_message: Error message if failed
        
        Returns:
            bool: Success
        """
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    insert_sql = """
                        INSERT INTO api_metrics
                        (endpoint, method, duration_ms, status_code, success, error_message)
                        VALUES (%s, %s, %s, %s, %s, %s)
                    """
                    
                    cur.execute(insert_sql, (
                        endpoint, method, duration_ms, status_code, success, error_message
                    ))
                    
                    return True
        
        except psycopg2.InterfaceError as e:
            # Connection interface error - likely already closed
            logger.debug(f"Database connection interface error (non-fatal): {e}")
            return False
        except psycopg2.OperationalError as e:
            # Connection operational error - database might be unavailable
            logger.debug(f"Database operational error (non-fatal): {e}")
            return False
        except Exception as e:
            logger.error(f"❌ Failed to save API metric: {e}")
            return False
    
    # ==================== Order History Cache Methods ====================
    
    def cache_order_history(self, account_id: str, orders: List[Dict]) -> bool:
        """
        Cache order history in database for faster subsequent loads.
        
        Args:
            account_id: Account ID
            orders: List of order dictionaries from API
            
        Returns:
            bool: Success status
        """
        if not orders:
            return True
        
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    # Prepare data for bulk insert
                    values = []
                    for order in orders:
                        # Extract timestamp from order
                        order_ts = None
                        for key in ['updateTimestamp', 'creationTimestamp', 'timestamp']:
                            if key in order:
                                ts_val = order[key]
                                if isinstance(ts_val, str):
                                    try:
                                        order_ts = datetime.fromisoformat(ts_val.replace('Z', '+00:00'))
                                        break
                                    except:
                                        pass
                                elif isinstance(ts_val, datetime):
                                    order_ts = ts_val
                                    break
                        
                        if not order_ts:
                            continue
                        
                        values.append((
                            account_id,
                            json.dumps(order),
                            order_ts
                        ))
                    
                    if not values:
                        return True
                    
                    # Bulk insert with ON CONFLICT DO NOTHING to avoid duplicates
                    execute_values(
                        cur,
                        """
                        INSERT INTO order_history_cache (account_id, order_data, order_timestamp)
                        VALUES %s
                        ON CONFLICT DO NOTHING
                        """,
                        values,
                        page_size=100
                    )
                    
                    logger.info(f"✅ Cached {len(values)} orders for account {account_id}")
                    return True
        
        except Exception as e:
            logger.error(f"❌ Failed to cache order history: {e}")
            return False
    
    def get_cached_order_history(
        self,
        account_id: str,
        start_time: datetime,
        end_time: datetime,
        limit: int = 1000,
        max_age_hours: int = 24
    ) -> Optional[List[Dict]]:
        """
        Retrieve cached order history from database.
        
        Args:
            account_id: Account ID
            start_time: Start timestamp
            end_time: End timestamp
            limit: Maximum orders to return
            max_age_hours: Maximum cache age in hours
            
        Returns:
            List of order dictionaries or None if cache miss/stale
        """
        try:
            with self.get_connection() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute("""
                        SELECT order_data, order_timestamp, cached_at
                        FROM order_history_cache
                        WHERE account_id = %s
                          AND order_timestamp >= %s
                          AND order_timestamp <= %s
                          AND cached_at > NOW() - INTERVAL '%s hours'
                        ORDER BY order_timestamp DESC
                        LIMIT %s
                    """, (account_id, start_time, end_time, max_age_hours, limit))
                    
                    rows = cur.fetchall()
                    
                    if not rows:
                        return None
                    
                    # Parse JSON order data
                    orders = [row['order_data'] for row in rows]
                    
                    logger.info(f"✅ DB Cache HIT: {len(orders)} orders for account {account_id}")
                    return orders
        
        except Exception as e:
            logger.error(f"❌ Failed to get cached order history: {e}")
            return None
    
    # ==================== Utility Methods ====================
    
    def cleanup_old_data(self, days: int = 30):
        """
        Clean up old data to prevent database bloat.
        
        Args:
            days: Keep data newer than this many days
        """
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    # Clean old historical bars (keep last 30 days)
                    cur.execute("""
                        DELETE FROM historical_bars 
                        WHERE created_at < NOW() - INTERVAL '%s days'
                    """, (days,))
                    
                    bars_deleted = cur.rowcount
                    
                    # Clean old API metrics (keep last 7 days)
                    cur.execute("""
                        DELETE FROM api_metrics 
                        WHERE timestamp < NOW() - INTERVAL '7 days'
                    """)
                    
                    metrics_deleted = cur.rowcount
                    
                    logger.info(f"🧹 Cleanup: Deleted {bars_deleted} old bars, {metrics_deleted} old metrics")
        
        except Exception as e:
            logger.error(f"❌ Failed to cleanup old data: {e}")
    
    def get_stats(self) -> Dict:
        """
        Get database statistics.
        
        Returns:
            Dict: Statistics about cached data
        """
        try:
            with self.get_connection() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    stats = {}
                    
                    # Count cached bars
                    cur.execute("SELECT COUNT(*) as count FROM historical_bars")
                    stats['total_bars'] = cur.fetchone()['count']
                    
                    # Count by symbol/timeframe
                    cur.execute("""
                        SELECT symbol, timeframe, COUNT(*) as count
                        FROM historical_bars
                        GROUP BY symbol, timeframe
                        ORDER BY count DESC
                        LIMIT 10
                    """)
                    stats['top_cached'] = cur.fetchall()
                    
                    # Account state count
                    cur.execute("SELECT COUNT(*) as count FROM account_state")
                    stats['accounts'] = cur.fetchone()['count']
                    
                    # Strategy metrics count
                    cur.execute("SELECT COUNT(*) as count FROM strategy_performance")
                    stats['strategy_records'] = cur.fetchone()['count']
                    
                    # API metrics count
                    cur.execute("SELECT COUNT(*) as count FROM api_metrics")
                    stats['api_metrics'] = cur.fetchone()['count']
                    
                    return stats
        
        except Exception as e:
            logger.error(f"❌ Failed to get database stats: {e}")
            return {}
    
    def record_notification(self, account_id: str, notification_type: str, message: str, 
                           level: str = "info", meta: Optional[Dict[str, Any]] = None) -> None:
        """
        Record a notification in the database.
        
        Args:
            account_id: Account ID for the notification
            notification_type: Type of notification (e.g., 'order_failed', 'risk_alert')
            message: Notification message
            level: Notification level ('info', 'success', 'warning', 'error')
            meta: Optional metadata dictionary
        """
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO notifications (account_id, notification_type, message, level, meta)
                        VALUES (%s, %s, %s, %s, %s)
                    """, (
                        str(account_id),
                        notification_type,
                        message,
                        level,
                        json.dumps(meta) if meta else None
                    ))
            logger.debug(f"✅ Notification recorded: {notification_type} for account {account_id}")
        except Exception as e:
            logger.error(f"❌ Failed to record notification: {e}")
            # Don't raise - notification recording failure shouldn't break the app
    
    def close(self):
        """Close all connections in the pool."""
        if self.pool:
            self.pool.closeall()
            logger.info("✅ Database connections closed")


# Global database manager instance
_db_manager: Optional[DatabaseManager] = None


def get_database() -> DatabaseManager:
    """Get or create global database manager instance."""
    global _db_manager
    if _db_manager is None:
        _db_manager = DatabaseManager()
    return _db_manager

