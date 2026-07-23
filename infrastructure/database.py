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
import atexit
import json
import logging
import queue
import threading
import time
try:
    import psycopg2
    from psycopg2 import pool, sql
    from psycopg2.extras import RealDictCursor, execute_values
except ImportError:
    psycopg2 = None
    pool = None
    sql = None
    RealDictCursor = None
    execute_values = None
from typing import List, Dict, Optional, Any, Tuple
from datetime import datetime, timezone
from contextlib import contextmanager

from core.json_fast import dumps_str

logger = logging.getLogger(__name__)


def _db_pool_bounds() -> tuple:
    """Bounded ThreadedConnectionPool sizes (env overridable)."""
    mn = max(1, int(os.getenv("DB_POOL_MINCONN", "1")))
    mx = max(mn, int(os.getenv("DB_POOL_MAXCONN", "10")))
    mx = min(mx, 32)
    return mn, mx


def _async_api_metrics_enabled() -> bool:
    return os.getenv("DB_ASYNC_API_METRICS", "1").strip().lower() not in (
        "0", "false", "no", "off",
    )


ApiMetricRow = Tuple[str, str, float, Optional[int], bool, Optional[str]]


class _ApiMetricsBatcher:
    """
    Background thread: queue api_metrics rows, flush with execute_values on size or interval.
    Hot-path callers use DatabaseManager.save_api_metric without waiting on the DB.
    """

    _SENTINEL = object()

    def __init__(self, db: "DatabaseManager") -> None:
        self._db = db
        qmax = max(1000, int(os.getenv("DB_API_METRICS_QUEUE_MAX", "10000")))
        self._q: "queue.Queue[object]" = queue.Queue(maxsize=qmax)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="db-api-metrics-batcher"
        )
        self._thread.start()

    def stop(self, join_timeout: float = 5.0) -> None:
        t = self._thread
        if t is None:
            return
        self._stop.set()
        try:
            self._q.put_nowait(self._SENTINEL)
        except queue.Full:
            logger.debug("api_metrics queue full; sentinel skipped on stop")
        if t.is_alive():
            t.join(timeout=join_timeout)
        self._thread = None

    def enqueue(self, row: ApiMetricRow) -> None:
        if self._stop.is_set():
            return
        try:
            self._q.put_nowait(row)
        except queue.Full:
            logger.warning("api_metrics queue full; dropping one sample")

    def _loop(self) -> None:
        if not execute_values:
            return
        batch_max = max(1, int(os.getenv("DB_API_METRICS_BATCH", "500")))
        flush_s = max(0.05, float(os.getenv("DB_API_METRICS_FLUSH_S", "1.0")))
        batch: List[ApiMetricRow] = []
        last_flush = time.monotonic()
        while True:
            timeout = 0.1
            if batch and (time.monotonic() - last_flush) >= flush_s:
                timeout = 0.0
            try:
                item = self._q.get(timeout=timeout)
            except queue.Empty:
                item = None
            if item is self._SENTINEL:
                if batch:
                    self._flush(batch)
                    batch.clear()
                if self._stop.is_set():
                    break
                last_flush = time.monotonic()
                continue
            if isinstance(item, tuple):
                batch.append(item)
            now = time.monotonic()
            if batch and (
                len(batch) >= batch_max or (now - last_flush) >= flush_s
            ):
                self._flush(batch)
                batch.clear()
                last_flush = now
        if batch:
            self._flush(batch)

    def _flush(self, rows: List[ApiMetricRow]) -> None:
        if not rows or not self._db.pool or not execute_values:
            return
        try:
            with self._db.get_connection() as conn:
                with conn.cursor() as cur:
                    execute_values(
                        cur,
                        """
                        INSERT INTO api_metrics
                        (endpoint, method, duration_ms, status_code, success, error_message)
                        VALUES %s
                        """,
                        rows,
                        page_size=min(500, len(rows)),
                    )
        except Exception as exc:
            logger.debug("api_metrics batch flush failed: %s", exc, exc_info=True)


def _async_strategy_exec_enabled() -> bool:
    return os.getenv("DB_ASYNC_STRATEGY_EXEC", "1").strip().lower() not in (
        "0", "false", "no", "off",
    )


# strategy_name, account_id, action, symbol, side, quantity, price, order_id, result, metadata
StrategyExecRow = Tuple[
    str,
    Optional[str],
    str,
    Optional[str],
    Optional[str],
    Optional[int],
    Optional[float],
    Optional[str],
    Optional[str],
    Optional[str],
]


class _StrategyExecutionsBatcher:
    """Background batch INSERT for strategy_executions (Phase 2.10)."""

    _SENTINEL = object()

    def __init__(self, db: "DatabaseManager") -> None:
        self._db = db
        qmax = max(500, int(os.getenv("DB_STRATEGY_EXEC_QUEUE_MAX", "5000")))
        self._q: "queue.Queue[object]" = queue.Queue(maxsize=qmax)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="db-strategy-exec-batcher"
        )
        self._thread.start()

    def stop(self, join_timeout: float = 5.0) -> None:
        t = self._thread
        if t is None:
            return
        self._stop.set()
        try:
            self._q.put_nowait(self._SENTINEL)
        except queue.Full:
            logger.debug("strategy_executions queue full; sentinel skipped on stop")
        if t.is_alive():
            t.join(timeout=join_timeout)
        self._thread = None

    def enqueue(self, row: StrategyExecRow) -> None:
        if self._stop.is_set():
            return
        try:
            self._q.put_nowait(row)
        except queue.Full:
            logger.warning("strategy_executions queue full; dropping one row")

    def _loop(self) -> None:
        if not execute_values:
            return
        batch_max = max(1, int(os.getenv("DB_STRATEGY_EXEC_BATCH", "200")))
        flush_s = max(0.05, float(os.getenv("DB_STRATEGY_EXEC_FLUSH_S", "1.0")))
        batch: List[StrategyExecRow] = []
        last_flush = time.monotonic()
        while True:
            timeout = 0.1
            if batch and (time.monotonic() - last_flush) >= flush_s:
                timeout = 0.0
            try:
                item = self._q.get(timeout=timeout)
            except queue.Empty:
                item = None
            if item is self._SENTINEL:
                if batch:
                    self._flush(batch)
                    batch.clear()
                if self._stop.is_set():
                    break
                last_flush = time.monotonic()
                continue
            if isinstance(item, tuple):
                batch.append(item)
            now = time.monotonic()
            if batch and (
                len(batch) >= batch_max or (now - last_flush) >= flush_s
            ):
                self._flush(batch)
                batch.clear()
                last_flush = now
        if batch:
            self._flush(batch)

    def _flush(self, rows: List[StrategyExecRow]) -> None:
        if not rows or not self._db.pool or not execute_values:
            return
        try:
            with self._db.get_connection() as conn:
                with conn.cursor() as cur:
                    execute_values(
                        cur,
                        """
                        INSERT INTO strategy_executions
                        (strategy_name, account_id, action, symbol, side, quantity,
                         price, order_id, result, metadata)
                        VALUES %s
                        """,
                        rows,
                        page_size=min(200, len(rows)),
                    )
        except Exception as exc:
            logger.debug(
                "strategy_executions batch flush failed: %s", exc, exc_info=True
            )


def _async_notifications_enabled() -> bool:
    return os.getenv("DB_ASYNC_NOTIFICATIONS", "1").strip().lower() not in (
        "0", "false", "no", "off",
    )


# account_id, notification_type, message, level, meta (JSON string)
NotificationRow = Tuple[str, str, str, str, Optional[str]]


class _NotificationsBatcher:
    """Background batch INSERT for notifications (Phase 2.10)."""

    _SENTINEL = object()

    def __init__(self, db: "DatabaseManager") -> None:
        self._db = db
        qmax = max(200, int(os.getenv("DB_NOTIFICATIONS_QUEUE_MAX", "3000")))
        self._q: "queue.Queue[object]" = queue.Queue(maxsize=qmax)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="db-notifications-batcher"
        )
        self._thread.start()

    def stop(self, join_timeout: float = 5.0) -> None:
        t = self._thread
        if t is None:
            return
        self._stop.set()
        try:
            self._q.put_nowait(self._SENTINEL)
        except queue.Full:
            logger.debug("notifications queue full; sentinel skipped on stop")
        if t.is_alive():
            t.join(timeout=join_timeout)
        self._thread = None

    def enqueue(self, row: NotificationRow) -> None:
        if self._stop.is_set():
            return
        try:
            self._q.put_nowait(row)
        except queue.Full:
            logger.warning("notifications queue full; dropping one row")

    def _loop(self) -> None:
        if not execute_values:
            return
        batch_max = max(1, int(os.getenv("DB_NOTIFICATIONS_BATCH", "100")))
        flush_s = max(0.05, float(os.getenv("DB_NOTIFICATIONS_FLUSH_S", "1.0")))
        batch: List[NotificationRow] = []
        last_flush = time.monotonic()
        while True:
            timeout = 0.1
            if batch and (time.monotonic() - last_flush) >= flush_s:
                timeout = 0.0
            try:
                item = self._q.get(timeout=timeout)
            except queue.Empty:
                item = None
            if item is self._SENTINEL:
                if batch:
                    self._flush(batch)
                    batch.clear()
                if self._stop.is_set():
                    break
                last_flush = time.monotonic()
                continue
            if isinstance(item, tuple):
                batch.append(item)
            now = time.monotonic()
            if batch and (
                len(batch) >= batch_max or (now - last_flush) >= flush_s
            ):
                self._flush(batch)
                batch.clear()
                last_flush = now
        if batch:
            self._flush(batch)

    def _flush(self, rows: List[NotificationRow]) -> None:
        if not rows or not self._db.pool or not execute_values:
            return
        try:
            with self._db.get_connection() as conn:
                with conn.cursor() as cur:
                    execute_values(
                        cur,
                        """
                        INSERT INTO notifications
                        (account_id, notification_type, message, level, meta)
                        VALUES %s
                        """,
                        rows,
                        page_size=min(100, len(rows)),
                    )
        except Exception as exc:
            logger.debug(
                "notifications batch flush failed: %s", exc, exc_info=True
            )


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
        self._api_metrics_batcher: Optional[_ApiMetricsBatcher] = None
        self._strategy_exec_batcher: Optional[_StrategyExecutionsBatcher] = None
        self._notifications_batcher: Optional[_NotificationsBatcher] = None
        self.pool = None
        # Offline stitch / export scripts set DISABLE_DATABASE=1 so they never
        # block on unreachable Railway DNS (refresh_historical hang, 2026-07-15).
        if str(os.getenv("DISABLE_DATABASE", "") or "").strip().lower() in (
            "1", "true", "yes", "on",
        ):
            logger.info("Database disabled (DISABLE_DATABASE) — memory-only mode")
            return
        if not psycopg2:
            logger.warning("⚠️  psycopg2 not available - database features will be disabled")
            return
        self._initialize_pool()
        if self.pool and execute_values and _async_api_metrics_enabled():
            self._api_metrics_batcher = _ApiMetricsBatcher(self)
            self._api_metrics_batcher.start()
            logger.info("📊 api_metrics async batch writer started")
        if self.pool and execute_values and _async_strategy_exec_enabled():
            self._strategy_exec_batcher = _StrategyExecutionsBatcher(self)
            self._strategy_exec_batcher.start()
            logger.info("📊 strategy_executions async batch writer started")
        if self.pool and execute_values and _async_notifications_enabled():
            self._notifications_batcher = _NotificationsBatcher(self)
            self._notifications_batcher.start()
            logger.info("📊 notifications async batch writer started")
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
        """Create connection pool for efficient database access.
        
        Tries connections in order:
        1. Railway DATABASE_URL (if available)
        2. Local PostgreSQL (postgresql://postgres:postgres@localhost:5432/trading_bot)
        3. Falls back to memory-only mode if both fail
        """
        if not psycopg2:
            logger.warning("⚠️  psycopg2 not available - database features will be disabled")
            return
        
        # Try Railway database first
        try:
            params = self._get_connection_params()
            
            _mn, _mx = _db_pool_bounds()
            try:
                connect_timeout = int(os.getenv("PG_CONNECT_TIMEOUT", "3") or 3)
            except ValueError:
                connect_timeout = 3
            pool_kwargs = dict(params)
            pool_kwargs.setdefault("connect_timeout", connect_timeout)
            self.pool = psycopg2.pool.ThreadedConnectionPool(
                minconn=_mn,
                maxconn=_mx,
                **pool_kwargs
            )
            
            # Test the connection (don't close it - just check it's alive)
            test_conn = self.pool.getconn()
            try:
                # Quick health check without closing
                test_conn.isolation_level  # Access property to verify connection is alive
            except (psycopg2.InterfaceError, psycopg2.OperationalError):
                # Connection is dead, close it and don't return to pool
                try:
                    test_conn.close()
                except Exception as close_exc:
                    logger.debug("Closing dead test connection: %s", close_exc, exc_info=True)
                raise Exception("Connection test failed - connection is dead")
            # Return connection to pool (don't close it)
            self.pool.putconn(test_conn)
            
            logger.info("✅ Database connection pool created")
            return
        except Exception as e:
            logger.warning(f"⚠️  Failed to connect to primary database: {e}")
            logger.info("🔄 Attempting fallback to local PostgreSQL...")
        
        # Fallback to local PostgreSQL
        try:
            local_params = {
                'host': 'localhost',
                'port': 5432,
                'database': 'trading_bot',
                'user': 'postgres',
                'password': 'postgres'
            }
            
            # Override with environment variables if set
            if os.getenv('POSTGRES_HOST'):
                local_params['host'] = os.getenv('POSTGRES_HOST')
            if os.getenv('POSTGRES_PORT'):
                local_params['port'] = int(os.getenv('POSTGRES_PORT'))
            if os.getenv('POSTGRES_DB'):
                local_params['database'] = os.getenv('POSTGRES_DB')
            if os.getenv('POSTGRES_USER'):
                local_params['user'] = os.getenv('POSTGRES_USER')
            if os.getenv('POSTGRES_PASSWORD'):
                local_params['password'] = os.getenv('POSTGRES_PASSWORD')
            
            _mn, _mx = _db_pool_bounds()
            try:
                connect_timeout = int(os.getenv("PG_CONNECT_TIMEOUT", "3") or 3)
            except ValueError:
                connect_timeout = 3
            local_params.setdefault("connect_timeout", connect_timeout)
            self.pool = psycopg2.pool.ThreadedConnectionPool(
                minconn=_mn,
                maxconn=_mx,
                **local_params
            )
            
            # Test the connection (don't close it - just check it's alive)
            test_conn = self.pool.getconn()
            try:
                # Quick health check without closing
                test_conn.isolation_level  # Access property to verify connection is alive
            except (psycopg2.InterfaceError, psycopg2.OperationalError):
                # Connection is dead, close it and don't return to pool
                try:
                    test_conn.close()
                except Exception as close_exc:
                    logger.debug("Closing dead test connection: %s", close_exc, exc_info=True)
                raise Exception("Connection test failed - connection is dead")
            # Return connection to pool (don't close it)
            self.pool.putconn(test_conn)
            
            logger.info(f"✅ Connected to local PostgreSQL: {local_params['host']}:{local_params['port']}/{local_params['database']}")
            return
        except Exception as e:
            logger.error(f"❌ Failed to connect to local PostgreSQL: {e}")
            logger.warning("⚠️  Database features will be disabled. Running in memory-only mode.")
            logger.info("💡 To enable database:")
            logger.info("   1. Run: ./scripts/setup_local_db.sh")
            logger.info("   2. Or set DATABASE_URL environment variable")
            self.pool = None
            # Don't raise - allow bot to run without database
    
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
                except Exception as close_exc:
                    logger.debug("close stale conn: %s", close_exc, exc_info=True)
                conn = self.pool.getconn()
            
            yield conn
            conn.commit()
        except Exception as e:
            if conn:
                try:
                    conn.rollback()
                except Exception as rb_exc:
                    logger.debug("rollback failed (conn may be closed): %s", rb_exc, exc_info=True)
            logger.error(f"Database error: {e}")
            raise
        finally:
            if conn and self.pool:
                try:
                    self.pool.putconn(conn)
                except Exception as put_exc:
                    logger.debug("putconn failed: %s", put_exc, exc_info=True)
    
    def _initialize_schema(self):
        """Create database schema if it doesn't exist."""
        if not self.pool:
            return
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
        CREATE INDEX IF NOT EXISTS idx_strategy_perf_name_symbol_time
            ON strategy_performance(strategy_name, symbol, timestamp DESC);
        
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
        CREATE INDEX IF NOT EXISTS idx_trade_history_account_exit_time
            ON trade_history(account_id, exit_time DESC)
            WHERE exit_time IS NOT NULL;
        
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

        -- Trade snapshots — captured at trade close so the dashboard recap modal
        -- can render an *exact* historical chart for any past trade without
        -- having to re-pull bars from the broker (whose history window is
        -- short) or stitch from Databento (which lags by a day). One row per
        -- trade; ``bars_json`` holds the OHLCV snippet for the recap window
        -- (see ``trading_bot.capture_trade_snapshot`` for the capture path).
        -- ``range_snapshot_json`` carries the strategy's session range
        -- (high/low/mid + window times) so the recap can render the same
        -- shaded box as the live chart.
        CREATE TABLE IF NOT EXISTS trade_snapshots (
            trade_id VARCHAR(100) PRIMARY KEY,
            account_id VARCHAR(50),
            strategy_name VARCHAR(50),
            symbol VARCHAR(20),
            side VARCHAR(10),                 -- position direction: BUY/LONG or SELL/SHORT
            quantity INT,
            entry_time TIMESTAMPTZ,
            exit_time TIMESTAMPTZ,
            entry_price DECIMAL(12, 4),
            exit_price DECIMAL(12, 4),
            pnl DECIMAL(12, 2),
            timeframe VARCHAR(10),            -- e.g. '1m', '5m'
            bars_json JSONB,                  -- LWC-shaped OHLCV [{time, open, high, low, close, volume}, ...]
            range_snapshot_json JSONB,        -- {high, low, mid, session_start_et, session_end_et} or null
            metadata JSONB,                   -- order ids, snap-source, padding window, etc.
            created_at TIMESTAMPTZ DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS idx_trade_snapshots_account_exit
            ON trade_snapshots(account_id, exit_time DESC);
        CREATE INDEX IF NOT EXISTS idx_trade_snapshots_strategy
            ON trade_snapshots(strategy_name, exit_time DESC);
        CREATE INDEX IF NOT EXISTS idx_trade_snapshots_symbol
            ON trade_snapshots(symbol, exit_time DESC);

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
        
        -- Process states (for slave process tracking)
        CREATE TABLE IF NOT EXISTS process_states (
            process_id VARCHAR(100) PRIMARY KEY,
            process_type VARCHAR(50) NOT NULL,  -- 'strategy_executor', 'order_monitor', etc.
            status VARCHAR(20) NOT NULL,  -- 'running', 'stopped', 'error'
            account_id VARCHAR(50),
            metadata JSONB,  -- Process-specific data
            started_at TIMESTAMPTZ,
            last_heartbeat TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW()
        );
        
        CREATE INDEX IF NOT EXISTS idx_process_states_type_status
            ON process_states(process_type, status);
        CREATE INDEX IF NOT EXISTS idx_process_states_account
            ON process_states(account_id);
        CREATE INDEX IF NOT EXISTS idx_process_states_heartbeat
            ON process_states(last_heartbeat);
        
        -- Strategy executions (log all strategy actions)
        CREATE TABLE IF NOT EXISTS strategy_executions (
            id SERIAL PRIMARY KEY,
            strategy_name VARCHAR(50) NOT NULL,
            account_id VARCHAR(50),
            action VARCHAR(50) NOT NULL,  -- 'signal', 'order_placed', 'order_filled', etc.
            symbol VARCHAR(20),
            side VARCHAR(10),  -- BUY, SELL
            quantity INT,
            price DECIMAL(12, 4),
            order_id VARCHAR(100),
            result JSONB,  -- Full result/response
            timestamp TIMESTAMPTZ DEFAULT NOW(),
            metadata JSONB  -- Additional context
        );
        
        CREATE INDEX IF NOT EXISTS idx_strategy_executions_strategy
            ON strategy_executions(strategy_name, timestamp DESC);
        CREATE INDEX IF NOT EXISTS idx_strategy_executions_account
            ON strategy_executions(account_id, timestamp DESC);
        CREATE INDEX IF NOT EXISTS idx_strategy_executions_action
            ON strategy_executions(action, timestamp DESC);
        CREATE INDEX IF NOT EXISTS idx_strategy_executions_order_id
            ON strategy_executions(order_id);

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
                            except (ValueError, TypeError) as ts_exc:
                                logger.debug("Skip bar with bad timestamp %r: %s", timestamp, ts_exc)
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
                            dumps_str(metadata) if metadata else None
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
        # Ensure account_id is string (database uses VARCHAR)
        account_id = str(account_id)
        
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
                        dumps_str(metadata) if metadata else None
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
        # Ensure account_id is string (database uses VARCHAR)
        account_id = str(account_id)
        
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
        # Ensure account_id is string (database uses VARCHAR)
        account_id = str(account_id)
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
                            dumps_str(settings) if settings else None,
                            dumps_str(metadata) if metadata else None,
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
            # Ensure account_id is string (database uses VARCHAR)
            account_id_str = str(account_id)
            
            with self.get_connection() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(
                        """
                        SELECT strategy_name, enabled, symbols, settings, metadata,
                               last_started, last_stopped, created_at, updated_at
                        FROM strategy_states
                        WHERE account_id = %s
                        """,
                        (account_id_str,),
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

    # ==================== Trade Snapshot Methods ====================

    def save_trade_snapshot(
        self,
        trade_id: str,
        *,
        account_id: Optional[str] = None,
        strategy_name: Optional[str] = None,
        symbol: Optional[str] = None,
        side: Optional[str] = None,
        quantity: Optional[int] = None,
        entry_time: Optional[datetime] = None,
        exit_time: Optional[datetime] = None,
        entry_price: Optional[float] = None,
        exit_price: Optional[float] = None,
        pnl: Optional[float] = None,
        timeframe: Optional[str] = None,
        bars: Optional[List[Dict[str, Any]]] = None,
        range_snapshot: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Persist a trade-recap snapshot. Idempotent on ``trade_id`` — re-saving
        overwrites the row (useful when the snapshot is captured a second time
        with a wider bar window after the trade settles).
        """
        if not self.pool:
            return False
        if not trade_id:
            logger.warning("save_trade_snapshot: missing trade_id")
            return False
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO trade_snapshots
                            (trade_id, account_id, strategy_name, symbol, side,
                             quantity, entry_time, exit_time, entry_price,
                             exit_price, pnl, timeframe, bars_json,
                             range_snapshot_json, metadata)
                        VALUES (%s, %s, %s, %s, %s,
                                %s, %s, %s, %s,
                                %s, %s, %s, %s,
                                %s, %s)
                        ON CONFLICT (trade_id) DO UPDATE SET
                            account_id = EXCLUDED.account_id,
                            strategy_name = EXCLUDED.strategy_name,
                            symbol = EXCLUDED.symbol,
                            side = EXCLUDED.side,
                            quantity = EXCLUDED.quantity,
                            entry_time = EXCLUDED.entry_time,
                            exit_time = EXCLUDED.exit_time,
                            entry_price = EXCLUDED.entry_price,
                            exit_price = EXCLUDED.exit_price,
                            pnl = EXCLUDED.pnl,
                            timeframe = EXCLUDED.timeframe,
                            bars_json = EXCLUDED.bars_json,
                            range_snapshot_json = EXCLUDED.range_snapshot_json,
                            metadata = EXCLUDED.metadata
                        """,
                        (
                            str(trade_id),
                            str(account_id) if account_id is not None else None,
                            strategy_name,
                            symbol,
                            side,
                            int(quantity) if quantity is not None else None,
                            entry_time,
                            exit_time,
                            float(entry_price) if entry_price is not None else None,
                            float(exit_price) if exit_price is not None else None,
                            float(pnl) if pnl is not None else None,
                            timeframe,
                            dumps_str(bars) if bars else None,
                            dumps_str(range_snapshot) if range_snapshot else None,
                            dumps_str(metadata) if metadata else None,
                        ),
                    )
            logger.debug("📸 Saved trade snapshot trade_id=%s (%d bars)",
                         trade_id, len(bars or []))
            return True
        except Exception as e:
            logger.error("❌ Failed to save trade snapshot %s: %s", trade_id, e)
            return False

    def get_trade_snapshot(self, trade_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve a saved trade snapshot. Returns ``None`` when missing."""
        if not self.pool or not trade_id:
            return None
        try:
            with self.get_connection() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(
                        """
                        SELECT trade_id, account_id, strategy_name, symbol, side,
                               quantity, entry_time, exit_time, entry_price,
                               exit_price, pnl, timeframe, bars_json,
                               range_snapshot_json, metadata, created_at
                        FROM trade_snapshots
                        WHERE trade_id = %s
                        """,
                        (str(trade_id),),
                    )
                    row = cur.fetchone()
                    if not row:
                        return None
                    out = dict(row)
                    for k in ("entry_time", "exit_time", "created_at"):
                        v = out.get(k)
                        if v is not None and hasattr(v, "isoformat"):
                            out[k] = v.isoformat()
                    for k in ("entry_price", "exit_price", "pnl"):
                        v = out.get(k)
                        if v is not None:
                            try:
                                out[k] = float(v)
                            except (TypeError, ValueError):
                                pass
                    return out
        except Exception as e:
            logger.error("❌ Failed to load trade snapshot %s: %s", trade_id, e)
            return None

    def list_trade_range_snapshots(
        self,
        account_id: str,
        *,
        strategy_name: Optional[str] = None,
        limit: int = 500,
    ) -> List[Dict[str, Any]]:
        """Trade snapshots that captured a strategy range (for history backfill)."""
        if not self.pool or not account_id:
            return []
        try:
            with self.get_connection() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    if strategy_name:
                        cur.execute(
                            """
                            SELECT strategy_name, symbol, exit_time, range_snapshot_json
                            FROM trade_snapshots
                            WHERE account_id = %s
                              AND strategy_name = %s
                              AND range_snapshot_json IS NOT NULL
                            ORDER BY exit_time DESC NULLS LAST
                            LIMIT %s
                            """,
                            (str(account_id), str(strategy_name), int(limit)),
                        )
                    else:
                        cur.execute(
                            """
                            SELECT strategy_name, symbol, exit_time, range_snapshot_json
                            FROM trade_snapshots
                            WHERE account_id = %s
                              AND range_snapshot_json IS NOT NULL
                            ORDER BY exit_time DESC NULLS LAST
                            LIMIT %s
                            """,
                            (str(account_id), int(limit)),
                        )
                    rows = cur.fetchall() or []
            out: List[Dict[str, Any]] = []
            for row in rows:
                rec = dict(row)
                raw = rec.get("range_snapshot_json")
                if isinstance(raw, str):
                    try:
                        rec["range_snapshot_json"] = json.loads(raw)
                    except (json.JSONDecodeError, TypeError, ValueError):
                        rec["range_snapshot_json"] = None
                if rec.get("range_snapshot_json"):
                    out.append(rec)
            return out
        except Exception as e:
            logger.error("❌ Failed to list trade range snapshots: %s", e)
            return []

    # ==================== Process State Methods ====================
    
    def save_process_state(
        self,
        process_id: str,
        process_type: str,
        status: str,
        account_id: Optional[str] = None,
        metadata: Optional[Dict] = None
    ) -> bool:
        """Save or update process state."""
        if not self.pool:
            return False
        
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    upsert_sql = """
                        INSERT INTO process_states
                        (process_id, process_type, status, account_id, metadata, started_at, last_heartbeat, updated_at)
                        VALUES (%s, %s, %s, %s, %s, NOW(), NOW(), NOW())
                        ON CONFLICT (process_id)
                        DO UPDATE SET
                            status = EXCLUDED.status,
                            account_id = EXCLUDED.account_id,
                            metadata = EXCLUDED.metadata,
                            last_heartbeat = NOW(),
                            updated_at = NOW()
                    """
                    
                    cur.execute(
                        upsert_sql,
                        (
                            process_id,
                            process_type,
                            status,
                            account_id,
                            dumps_str(metadata) if metadata else None
                        )
                    )
                    return True
        except Exception as e:
            logger.error(f"❌ Failed to save process state: {e}")
            return False
    
    def get_process_states(self, process_type: Optional[str] = None) -> List[Dict]:
        """Get all process states, optionally filtered by type."""
        if not self.pool:
            return []
        
        try:
            with self.get_connection() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    if process_type:
                        cur.execute(
                            "SELECT * FROM process_states WHERE process_type = %s ORDER BY last_heartbeat DESC",
                            (process_type,)
                        )
                    else:
                        cur.execute("SELECT * FROM process_states ORDER BY last_heartbeat DESC")
                    
                    return [dict(row) for row in cur.fetchall()]
        except Exception as e:
            logger.error(f"❌ Failed to get process states: {e}")
            return []
    
    def log_strategy_execution(
        self,
        strategy_name: str,
        action: str,
        account_id: Optional[str] = None,
        symbol: Optional[str] = None,
        side: Optional[str] = None,
        quantity: Optional[int] = None,
        price: Optional[float] = None,
        order_id: Optional[str] = None,
        result: Optional[Dict] = None,
        metadata: Optional[Dict] = None
    ) -> bool:
        """Log a strategy execution event."""
        if not self.pool:
            return False

        rj = dumps_str(result) if result else None
        mj = dumps_str(metadata) if metadata else None
        if self._strategy_exec_batcher is not None:
            self._strategy_exec_batcher.enqueue(
                (
                    strategy_name,
                    account_id,
                    action,
                    symbol,
                    side,
                    quantity,
                    price,
                    order_id,
                    rj,
                    mj,
                )
            )
            return True

        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    insert_sql = """
                        INSERT INTO strategy_executions
                        (strategy_name, account_id, action, symbol, side, quantity, price, order_id, result, metadata, timestamp)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                    """
                    
                    cur.execute(
                        insert_sql,
                        (
                            strategy_name,
                            account_id,
                            action,
                            symbol,
                            side,
                            quantity,
                            price,
                            order_id,
                            rj,
                            mj,
                        )
                    )
                    return True
        except Exception as e:
            logger.error(f"❌ Failed to log strategy execution: {e}")
            return False
    
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
                    cur.execute(upsert_sql, (key, dumps_str(settings)))
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
                        dumps_str(metadata) if metadata else None
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
        if self._api_metrics_batcher is not None:
            self._api_metrics_batcher.enqueue(
                (endpoint, method, duration_ms, status_code, success, error_message)
            )
            return True
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
        # Ensure account_id is string (database uses VARCHAR)
        account_id = str(account_id)
        
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
                                    except (ValueError, TypeError) as ts_exc:
                                        logger.debug("Bad order timestamp %r: %s", ts_val, ts_exc)
                                elif isinstance(ts_val, datetime):
                                    order_ts = ts_val
                                    break
                        
                        if not order_ts:
                            continue
                        
                        values.append((
                            account_id,
                            dumps_str(order),
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
    
    def cleanup_old_data(self, days: Optional[int] = None):
        """
        Clean up old data to prevent database bloat.

        Drops rows older than ``days`` from historical bars, API metrics,
        notifications, and strategy execution logs. Retention defaults to
        ``DB_TELEMETRY_RETENTION_DAYS`` (30).

        Args:
            days: Keep data newer than this many days (all listed tables).
        """
        if days is None:
            days = max(1, int(os.getenv("DB_TELEMETRY_RETENTION_DAYS", "30")))
        if not self.pool:
            logger.debug("cleanup_old_data skipped: no connection pool")
            return
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        DELETE FROM historical_bars
                        WHERE created_at < NOW() - (%s * INTERVAL '1 day')
                        """,
                        (days,),
                    )
                    bars_deleted = cur.rowcount

                    cur.execute(
                        """
                        DELETE FROM api_metrics
                        WHERE timestamp < NOW() - (%s * INTERVAL '1 day')
                        """,
                        (days,),
                    )
                    metrics_deleted = cur.rowcount

                    cur.execute(
                        """
                        DELETE FROM notifications
                        WHERE created_at < NOW() - (%s * INTERVAL '1 day')
                        """,
                        (days,),
                    )
                    notifications_deleted = cur.rowcount

                    cur.execute(
                        """
                        DELETE FROM strategy_executions
                        WHERE timestamp < NOW() - (%s * INTERVAL '1 day')
                        """,
                        (days,),
                    )
                    executions_deleted = cur.rowcount

                    logger.info(
                        "🧹 Cleanup: deleted bars=%s api_metrics=%s notifications=%s strategy_executions=%s (older than %s days)",
                        bars_deleted,
                        metrics_deleted,
                        notifications_deleted,
                        executions_deleted,
                        days,
                    )

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
        mj = dumps_str(meta) if meta else None
        aid = str(account_id)
        if self._notifications_batcher is not None:
            self._notifications_batcher.enqueue(
                (aid, notification_type, message, level, mj)
            )
            logger.debug(
                "Notification queued: %s for account %s", notification_type, aid
            )
            return
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO notifications (account_id, notification_type, message, level, meta)
                        VALUES (%s, %s, %s, %s, %s)
                    """, (aid, notification_type, message, level, mj))
            logger.debug(f"✅ Notification recorded: {notification_type} for account {aid}")
        except Exception as e:
            logger.error(f"❌ Failed to record notification: {e}")
            # Don't raise - notification recording failure shouldn't break the app
    
    def close(self):
        """Close all connections in the pool."""
        if self._api_metrics_batcher is not None:
            self._api_metrics_batcher.stop()
            self._api_metrics_batcher = None
        if self._strategy_exec_batcher is not None:
            self._strategy_exec_batcher.stop()
            self._strategy_exec_batcher = None
        if self._notifications_batcher is not None:
            self._notifications_batcher.stop()
            self._notifications_batcher = None
        if self.pool:
            self.pool.closeall()
            logger.info("✅ Database connections closed")


# Global database manager instance
_db_manager: Optional[DatabaseManager] = None


def _atexit_flush_db_batchers() -> None:
    dm = _db_manager
    if dm is None:
        return
    try:
        if dm._api_metrics_batcher is not None:
            dm._api_metrics_batcher.stop()
    except Exception:
        logger.debug("api_metrics batcher stop at exit failed", exc_info=True)
    try:
        if dm._strategy_exec_batcher is not None:
            dm._strategy_exec_batcher.stop()
    except Exception:
        logger.debug("strategy_executions batcher stop at exit failed", exc_info=True)
    try:
        if dm._notifications_batcher is not None:
            dm._notifications_batcher.stop()
    except Exception:
        logger.debug("notifications batcher stop at exit failed", exc_info=True)


atexit.register(_atexit_flush_db_batchers)


def get_database() -> DatabaseManager:
    """Get or create global database manager instance."""
    global _db_manager
    if _db_manager is None:
        _db_manager = DatabaseManager()
    return _db_manager

