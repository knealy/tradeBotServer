#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TopStepX Trading Bot - Real API Implementation
A dynamic trading bot for TopStepX prop firm futures accounts.

This bot provides:
1. Real authentication with TopStepX ProjectX API
2. Live account listing from API
3. Account selection for trading
4. Live market order placement

Version: 2.0.0 (Modular Strategy System)
"""

import os
import sys
import asyncio
import json
import logging
import readline
import pickle
import hashlib
import csv
# jwt is optional - imported conditionally where needed
try:
    import jwt
except ImportError:
    jwt = None
from pathlib import Path
from typing import List, Dict, Optional, Any
from datetime import datetime, timedelta, timezone
from threading import Lock
from collections import deque, OrderedDict
import time
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
# SignalR is optional - imported conditionally where needed
try:
    from signalrcore.hub_connection_builder import HubConnectionBuilder
    from signalrcore.transport.websockets.websocket_transport import WebsocketTransport
    SIGNALR_AVAILABLE = True
except ImportError:
    HubConnectionBuilder = None
    WebsocketTransport = None
    SIGNALR_AVAILABLE = False

# Import from new organized structure
from core.discord_notifier import DiscordNotifier
from core.account_tracker import AccountTracker
from strategies.overnight_range_strategy import OvernightRangeStrategy
from strategies.mean_reversion_strategy import MeanReversionStrategy
from strategies.trend_following_strategy import TrendFollowingStrategy
from strategies.strategy_manager import StrategyManager
from infrastructure.performance_metrics import get_metrics_tracker
from infrastructure.database import get_database

# Import new modular architecture components
from core.auth import AuthManager
from core.rate_limiter import RateLimiter as RateLimiterModule
from core.market_data import ContractManager
from core.risk_management import RiskManager
from core.position_management import PositionManager
from core.websocket_manager import WebSocketManager
from core.order_execution import OrderExecutor
from brokers.topstepx_adapter import TopStepXAdapter
from events.event_bus import EventBus, get_event_bus

# Optional ProjectX SDK adapter
try:
    from core import sdk_adapter  # local adapter around project-x-py
    logger_temp = logging.getLogger(__name__)
    logger_temp.debug("✅ sdk_adapter imported successfully")
except Exception as import_err:
    # Log the actual import error for debugging
    import_err_str = str(import_err)
    import_err_type = type(import_err).__name__
    print(f"⚠️  WARNING: Failed to import sdk_adapter: {import_err_type}: {import_err_str}")
    logging.getLogger(__name__).warning(f"Failed to import sdk_adapter: {import_err_type}: {import_err_str}")
    import traceback
    logging.getLogger(__name__).debug(f"Import traceback: {traceback.format_exc()}")
    sdk_adapter = None  # type: ignore

# Load environment variables from .env file
import load_env

# Configure logging
log_level = os.getenv('LOG_LEVEL', 'INFO').upper()
# Ensure log file handler is properly configured with rotation
from logging.handlers import RotatingFileHandler
file_handler = RotatingFileHandler(
    'trading_bot.log', 
    mode='a', 
    encoding='utf-8',
    maxBytes=10*1024*1024,  # 10MB per file
    backupCount=5  # Keep 5 backup files (50MB total)
)
# File handler gets all logs (INFO and above by default, DEBUG if verbose)
file_handler.setLevel(getattr(logging, log_level, logging.INFO))
file_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))

# Console handler only shows warnings and errors to reduce terminal verbosity
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setLevel(logging.WARNING)  # Only WARNING, ERROR, CRITICAL in terminal
console_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))

logging.basicConfig(
    level=getattr(logging, log_level, logging.INFO),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[file_handler, console_handler],
    force=True  # Override any existing configuration
)
logger = logging.getLogger(__name__)
# Only log to file, not console (this is INFO level)
logger.info("Logging initialized - file: trading_bot.log (INFO+), console: stdout (WARNING+)")

# SignalR errors are now properly handled with token refresh - no suppression needed
# Keep log levels reasonable to avoid spam but show important errors
logging.getLogger("SignalRCoreClient").setLevel(logging.INFO)  # Show info and above
logging.getLogger("websocket").setLevel(logging.WARNING)  # Only warnings and errors for websocket library

# Bot identifier for order tagging - will be made unique per order
BOT_ORDER_TAG_PREFIX = "TradingBot-v1.0"


class RateLimiter:
    """
    Rate limiter using sliding window algorithm.
    Prevents API rate limit violations by tracking calls within a time window.
    """
    def __init__(self, max_calls: int = 60, period: int = 60):
        """
        Initialize rate limiter.
        
        Args:
            max_calls: Maximum number of calls allowed in the period
            period: Time period in seconds (default: 60 seconds)
        """
        self.max_calls = max_calls
        self.period = period
        self.calls = deque()
        self.lock = Lock()
    
    def acquire(self) -> None:
        """
        Acquire permission to make an API call.
        Blocks if necessary until rate limit allows the call.
        """
        with self.lock:
            now = time.time()
            
            # Remove calls older than the period
            while self.calls and self.calls[0] < now - self.period:
                self.calls.popleft()
            
            # If we're at the limit, wait until the oldest call expires
            if len(self.calls) >= self.max_calls:
                sleep_time = self.period - (now - self.calls[0])
                if sleep_time > 0:
                    logger.debug(f"Rate limit reached, waiting {sleep_time:.2f}s before next API call")
                    time.sleep(sleep_time)
                    # Update now after sleep
                    now = time.time()
                    # Remove any additional expired calls
                    while self.calls and self.calls[0] < now - self.period:
                        self.calls.popleft()
            
            # Record this call
            self.calls.append(now)
    
    def get_remaining_calls(self) -> int:
        """Get number of remaining calls in current period."""
        with self.lock:
            now = time.time()
            # Remove expired calls
            while self.calls and self.calls[0] < now - self.period:
                self.calls.popleft()
            return max(0, self.max_calls - len(self.calls))
    
    def reset(self) -> None:
        """Reset the rate limiter (clear call history)."""
        with self.lock:
            self.calls.clear()


class TopStepXTradingBot:
    """
    A real trading bot for TopStepX prop firm futures accounts.
    Uses actual ProjectX API calls via cURL.
    """
    
    def __init__(self, api_key: str = None, username: str = None, base_url: str = "https://api.topstepx.com"):
        """
        Initialize the trading bot.
        
        Args:
            api_key: TopStepX API key
            username: TopStepX username
            base_url: TopStepX API base URL
        """
        self.api_key = api_key or os.getenv('PROJECT_X_API_KEY') or os.getenv('TOPSETPX_API_KEY')
        self.username = username or os.getenv('PROJECT_X_USERNAME') or os.getenv('TOPSETPX_USERNAME')
        self.base_url = base_url
        
        # Try to load JWT token from environment (useful for Railway deployment)
        env_jwt = os.getenv('JWT_TOKEN')
        if env_jwt:
            self.session_token = env_jwt
            # Parse JWT to extract expiration time
            try:
                import jwt
                decoded = jwt.decode(env_jwt, options={"verify_signature": False})
                exp_timestamp = decoded.get('exp')
                if exp_timestamp:
                    self.token_expiry = datetime.fromtimestamp(exp_timestamp, tz=timezone.utc)
                    logger.info(f"Loaded JWT from environment (expires: {self.token_expiry})")
                else:
                    self.token_expiry = None
                    logger.warning("JWT loaded from environment but has no expiration claim")
            except Exception as parse_err:
                logger.warning(f"Failed to parse JWT from environment: {parse_err}")
                self.token_expiry = None
        else:
            self.session_token = None
            self.token_expiry = None
        
        self.selected_account = None
        # In-memory caches for quick shutdown without searching
        # Structure: { accountId: { symbol: set([orderId, ...]) } }
        self._cached_order_ids = {}
        # Structure: { accountId: { symbol: set([positionId, ...]) } }
        self._cached_position_ids = {}
        # Real-time quote cache: { SYMBOL: { 'bid': float, 'ask': float, 'last': float, 'volume': float, 'ts': iso } }
        self._quote_cache: Dict[str, Dict] = {}
        self._quote_cache_lock: Lock = Lock()
        # Real-time depth cache: { SYMBOL: { 'bids': [], 'asks': [], 'ts': iso } }
        self._depth_cache: Dict[str, Dict] = {}
        self._depth_cache_lock: Lock = Lock()
        # Contract list cache: { 'contracts': List[Dict], 'timestamp': datetime, 'ttl_minutes': int }
        self._contract_cache: Optional[Dict] = None
        self._contract_cache_lock: Lock = Lock()
        self._market_hub = None
        self._market_hub_connected = False
        self._market_hub_url = os.getenv("PROJECT_X_MARKET_HUB_URL", "https://rtc.topstepx.com/hubs/market")
        # Allow overriding hub method names via env to adapt without code change
        # Default to the naming used by the REST quote command (`/api/MarketData/quote`)
        # while still allowing overrides via env vars.
        self._market_hub_quote_event = os.getenv("PROJECT_X_QUOTE_EVENT", "Quote")
        self._market_hub_subscribe_method = os.getenv("PROJECT_X_SUBSCRIBE_METHOD", "SubscribeQuote")
        self._market_hub_unsubscribe_method = os.getenv("PROJECT_X_UNSUBSCRIBE_METHOD", "UnsubscribeQuote")
        self._subscribed_symbols = set()
        self._pending_symbols = set()
        self._raw_quote_log_count = 0
        self._missing_symbol_log_count = 0
        
        # WebSocket connection pool: {url: hub_connection}
        # Reuses connections for multiple symbols to reduce overhead
        self._websocket_pool: Dict[str, Any] = {}
        self._websocket_pool_lock = Lock()
        self._websocket_pool_max_size = int(os.getenv('WEBSOCKET_POOL_MAX_SIZE', '5'))  # Max 5 concurrent connections
        
        # Initialize Discord notifier
        self.discord_notifier = DiscordNotifier()
        
        # Initialize PostgreSQL database (for persistent caching and state)
        try:
            self.db = get_database()
            logger.info("✅ PostgreSQL database initialized")
        except Exception as e:
            logger.warning(f"⚠️  PostgreSQL unavailable (will use memory cache only): {e}")
            self.db = None
        
        # Initialize real-time account state tracker (with database support)
        self.account_tracker = AccountTracker(db=self.db)
        logger.debug("Account tracker initialized with database support")
        
        # Initialize Strategy Manager (modular strategy system)
        self.strategy_manager = StrategyManager(trading_bot=self)
        logger.debug("Strategy manager initialized")
        
        # Initialize bar aggregator for real-time chart updates
        from core.bar_aggregator import BarAggregator
        self.bar_aggregator = BarAggregator(broadcast_callback=None)  # Will be set by webhook server
        logger.debug("Bar aggregator initialized")
        
        # Register all available strategies
        self.strategy_manager.register_strategy("overnight_range", OvernightRangeStrategy)
        self.strategy_manager.register_strategy("mean_reversion", MeanReversionStrategy)
        self.strategy_manager.register_strategy("trend_following", TrendFollowingStrategy)
        logger.debug("Strategies registered with manager")
        
        # Load strategies from environment configuration
        self.strategy_manager.load_strategies_from_config()
        
        # Initialize overnight range breakout strategy (backward compatibility)
        # This is the default active strategy
        self.overnight_strategy = self.strategy_manager.strategies.get("overnight_range")
        if not self.overnight_strategy:
            # Fallback if not loaded from config
            self.overnight_strategy = OvernightRangeStrategy(trading_bot=self)
        logger.debug("Overnight range strategy initialized (default active strategy)")
        
        # Order counter for unique custom tags
        self._order_counter = 0
        
        # Initialize rate limiter
        # Default: 60 calls per 60 seconds (1 call/second)
        # Configurable via environment variables
        rate_limit_max = int(os.getenv('API_RATE_LIMIT_MAX', '60'))
        rate_limit_period = int(os.getenv('API_RATE_LIMIT_PERIOD', '60'))
        self._rate_limiter = RateLimiter(max_calls=rate_limit_max, period=rate_limit_period)
        self.rate_limiter = self._rate_limiter  # Alias for compatibility
        logger.debug(f"Rate limiter initialized: {rate_limit_max} calls per {rate_limit_period} seconds")
        
        # ========================================================================
        # NEW MODULAR ARCHITECTURE - Dependency Injection Setup
        # ========================================================================
        logger.info("🔧 Initializing modular architecture components...")
        
        # Initialize AuthManager (handles authentication and token management)
        self.auth_manager = AuthManager(
            api_key=self.api_key,
            username=self.username,
            base_url=self.base_url
        )
        # Sync session token from AuthManager if available
        if self.auth_manager.session_token:
            self.session_token = self.auth_manager.session_token
            self.token_expiry = self.auth_manager.token_expiry
        logger.debug("✅ AuthManager initialized")
        
        # Initialize ContractManager (handles contract ID resolution)
        self.contract_manager = ContractManager()
        logger.debug("✅ ContractManager initialized")
        
        # Initialize EventBus (for event-driven architecture)
        self.event_bus = get_event_bus()
        logger.debug("✅ EventBus initialized")
        
        # Initialize RiskManager (handles tick sizes, point values, trading sessions)
        self.risk_manager = RiskManager()
        logger.debug("✅ RiskManager initialized")
        
        # Initialize TopStepXAdapter (broker-specific implementation)
        #
        # Rust hot path routing:
        # - By default, TopStepXAdapter auto-enables Rust if the trading_bot_rust
        #   module is installed (RUST_AVAILABLE = True).
        # - To force-enable/disable Rust (e.g., in staging), use env var:
        #     TOPSTEPX_USE_RUST=true|false
        use_rust_env = os.getenv("TOPSTEPX_USE_RUST")
        if use_rust_env is None:
            use_rust_flag = None  # auto-detect
        else:
            use_rust_flag = use_rust_env.strip().lower() in ("1", "true", "yes", "on")

        self.broker_adapter = TopStepXAdapter(
            auth_manager=self.auth_manager,
            contract_manager=self.contract_manager,
            rate_limiter=self._rate_limiter,
            base_url=self.base_url,
            use_rust=use_rust_flag,
        )
        logger.debug("✅ TopStepXAdapter initialized")
        
        # Initialize PositionManager (handles position modifications)
        self.position_manager = PositionManager(broker_adapter=self.broker_adapter)
        logger.debug("✅ PositionManager initialized")
        
        # Initialize WebSocketManager (handles SignalR real-time data)
        self.websocket_manager = WebSocketManager(
            auth_manager=self.auth_manager,
            contract_manager=self.contract_manager
        )
        # Register quote callback to update local cache
        self.websocket_manager.register_quote_callback(self._on_websocket_quote)
        self.websocket_manager.register_depth_callback(self._on_websocket_depth)
        logger.debug("✅ WebSocketManager initialized")
        
        # Initialize OrderExecutor (high-level order orchestration)
        self.order_executor = OrderExecutor(
            broker_adapter=self.broker_adapter,
            event_bus=self.event_bus,
            selected_account=self.selected_account
        )
        logger.debug("✅ OrderExecutor initialized")
        
        logger.info("✅ Modular architecture components initialized")
        
        # Initialize in-memory cache for historical data (ultra-fast access)
        # LRU cache with max size and TTL
        memory_cache_max = int(os.getenv('MEMORY_CACHE_MAX_SIZE', '50'))  # Cache up to 50 symbol/timeframe combos
        self._memory_cache: OrderedDict[str, tuple] = OrderedDict()  # key -> (data, timestamp)
        self._memory_cache_max_size = memory_cache_max
        self._memory_cache_lock = Lock()
        
        # Cache format preference: 'parquet' (faster) or 'pickle' (compatible)
        self._cache_format = os.getenv('CACHE_FORMAT', 'parquet').lower()
        if self._cache_format not in ('parquet', 'pickle'):
            self._cache_format = 'parquet'
            logger.warning(f"Invalid CACHE_FORMAT, defaulting to 'parquet'")
        
        logger.debug(f"Cache initialized: format={self._cache_format}, memory_cache_size={memory_cache_max}")
        
        # Monitoring state - only monitor after market orders are placed
        self._monitoring_active = False
        self._last_order_time = None
        
        # Track filled orders to avoid duplicate notifications
        self._notified_orders = set()
        self._notification_warmup_done: Dict[str, bool] = {}
        self._startup_time = datetime.now(timezone.utc)
        self._notified_positions = set()  # Track position close notifications
        
        # Auto fills settings
        self._auto_fills_enabled = False
        self._last_order_activity = None  # Track last order activity for adaptive fill checking
        self._fill_check_interval = 30  # Default interval (seconds)
        self._fill_check_active_interval = 10  # Active interval when orders exist (seconds)
        
        # Prefetch cache for common symbols/timeframes
        self._prefetch_enabled = os.getenv('PREFETCH_ENABLED', 'true').lower() in ('true', '1', 'yes')
        self._prefetch_symbols = [s.strip().upper() for s in os.getenv('PREFETCH_SYMBOLS', 'MNQ,ES,NQ,MES').split(',')]
        self._prefetch_timeframes = [tf.strip() for tf in os.getenv('PREFETCH_TIMEFRAMES', '1m,5m').split(',')]
        self._prefetch_task = None
        
        # HTTP session with connection pooling for efficient API calls
        self._http_session = self._create_http_session()

    # ---------------------------
    # SignalR Market Hub Support
    # ---------------------------
    def _on_websocket_quote(self, symbol: str, data: Dict):
        """
        Callback for WebSocket quote events.
        
        Updates local quote cache and feeds bar aggregator.
        """
        try:
            with self._quote_cache_lock:
                entry = self._quote_cache.setdefault(symbol, {})
                # GatewayQuote payload fields per docs
                if "bestBid" in data:
                    entry["bid"] = data.get("bestBid")
                if "bestAsk" in data:
                    entry["ask"] = data.get("bestAsk")
                if "lastPrice" in data:
                    entry["last"] = data.get("lastPrice")
                if "volume" in data:
                    entry["volume"] = data.get("volume")
                entry["ts"] = datetime.now(timezone.utc).isoformat()
            
            # Feed quote to bar aggregator for real-time bar updates
            if hasattr(self, 'bar_aggregator') and self.bar_aggregator:
                last_price = data.get("lastPrice")
                volume = data.get("volume", 0)
                if last_price is not None:
                    try:
                        self.bar_aggregator.add_quote(
                            symbol=symbol,
                            price=float(last_price),
                            volume=int(volume) if volume else 0,
                            timestamp=datetime.now(timezone.utc)
                        )
                        # Log first few quotes per symbol to verify flow
                        if not hasattr(self, '_quote_log_count'):
                            self._quote_log_count = {}
                        count = self._quote_log_count.get(symbol, 0)
                        if count < 5:
                            logger.info(f"📈 Quote #{count+1} for {symbol}: ${last_price} (vol: {volume}) → bar aggregator")
                            self._quote_log_count[symbol] = count + 1
                        elif count == 5:
                            logger.info(f"📈 Quote flow confirmed for {symbol} (suppressing further logs)")
                            self._quote_log_count[symbol] = count + 1
                    except Exception as e:
                        logger.debug(f"Error adding quote to bar aggregator for {symbol}: {e}")
        except Exception as e:
            logger.debug(f"Failed processing quote message: {e}")
    
    def _on_websocket_depth(self, symbol: str, data: Dict):
        """
        Callback for WebSocket depth events.
        
        Updates local depth cache.
        """
        try:
            with self._depth_cache_lock:
                entry = self._depth_cache.setdefault(symbol, {})
                # Handle different depth data formats
                if "bids" in data:
                    entry["bids"] = data.get("bids", [])
                if "asks" in data:
                    entry["asks"] = data.get("asks", [])
                if "orderBook" in data:
                    order_book = data.get("orderBook", {})
                    entry["bids"] = order_book.get("bids", [])
                    entry["asks"] = order_book.get("asks", [])
                entry["ts"] = datetime.now(timezone.utc).isoformat()
        except Exception as e:
            logger.debug(f"Failed processing depth message: {e}")
    
    async def _ensure_market_socket_started(self) -> None:
        """
        Start market data socket only when actually needed (e.g., for quotes/depth).
        
        Now uses WebSocketManager for SignalR connections, maintaining backward compatibility.
        """
        # Use WebSocketManager if available
        if hasattr(self, 'websocket_manager'):
            if self.websocket_manager.is_connected():
                self._market_hub_connected = True
                return
            # Start WebSocketManager connection
            success = await self.websocket_manager.start()
            if success:
                self._market_hub_connected = True
                # Sync subscribed symbols
                self._subscribed_symbols = self.websocket_manager.get_subscribed_symbols()
                return
        
        # Fallback to legacy SignalR implementation for backward compatibility
        if self._market_hub_connected:
            return
        
        # Don't auto-start SDK realtime - it will start on-demand when needed
        # This avoids unnecessary overhead and WebSocket errors
        use_sdk = os.getenv("USE_PROJECTX_SDK", "0").lower() in ("1", "true", "yes")
        if use_sdk and sdk_adapter is not None and sdk_adapter.is_sdk_available():
            # SDK realtime will start lazily when subscribe_to_quotes is called
            # For now, just mark as available but don't start yet
            logger.debug("SDK realtime available but not auto-starting (lazy initialization)")
            # We'll still use SignalR fallback for now unless explicitly using SDK realtime
            # This prevents premature WebSocket connections
        # Build headers with bearer token for auth
        headers = {"Authorization": f"Bearer {self.session_token}"} if self.session_token else {}
        # Websocket transport ensures low latency
        # Append token to URL per docs, while also setting access_token_factory
        url_with_token = self._market_hub_url
        if self.session_token and "access_token=" not in url_with_token:
            sep = '&' if '?' in url_with_token else '?'
            url_with_token = f"{url_with_token}{sep}access_token={self.session_token}"

        # Convert to ws/wss when using direct WebSocket transport
        url_ws = url_with_token
        if url_ws.startswith("https://"):
            url_ws = "wss://" + url_ws[len("https://"):]
        elif url_ws.startswith("http://"):
            url_ws = "ws://" + url_ws[len("http://"):]

        hub = (
            HubConnectionBuilder()
            .with_url(
                url_ws,
                options={
                    "headers": headers,
                    "skip_negotiation": True,
                    "access_token_factory": (lambda: self.session_token or ""),
                    "transport": WebsocketTransport
                }
            )
            # Implement exponential backoff: [5s, 10s, 20s, 40s, 60s, 60s, ...]
            # Max 10 attempts to prevent infinite retry spam
            .with_automatic_reconnect({
                "type": "raw", 
                "keep_alive_interval": 15,
                "reconnect_interval": 5,  # Start at 5 seconds
                "max_attempts": 10  # Limit retry attempts
            })
            .build()
        )

        def on_open():
            logger.info("✅ SignalR Market Hub connected")
            self._market_hub_connected = True
            # Flush any pending subscriptions
            try:
                for sym in list(self._pending_symbols):
                    asyncio.create_task(self._ensure_quote_subscription(sym))
                    self._pending_symbols.discard(sym)
            except Exception as e:
                logger.debug(f"Flush subscribe failed: {e}")

        def on_close():
            logger.warning("⚠️  SignalR Market Hub disconnected")
            self._market_hub_connected = False

        def on_error(err):
            try:
                error_text = ""
                result_text = ""
                if hasattr(err, "error"):
                    error_text = getattr(err, "error")
                if hasattr(err, "result"):
                    result_text = getattr(err, "result")
                if isinstance(err, dict):
                    error_text = err.get("error") or err.get("ExceptionMessage") or error_text
                    result_text = err.get("result") or result_text
                if not error_text:
                    error_text = str(err)
                
                # Check for 403 Forbidden (authentication/rate limit issues)
                if "403" in error_text or "Forbidden" in error_text:
                    logger.error(f"❌ SignalR authentication/rate limit error (403 Forbidden) - check session token validity")
                    # Don't spam logs with repeated 403 errors - limit max attempts will handle this
                    return
                
                # Only log non-403 errors with full details
                logger.error(f"SignalR Market Hub error: {error_text} | result={result_text}")
            except Exception:
                logger.error(f"SignalR Market Hub error: {err}")

        def on_quote(*args):
            try:
                if self._raw_quote_log_count < 5:
                    logger.info(f"📶 Raw quote event #{self._raw_quote_log_count + 1}: args={args}")
                    self._raw_quote_log_count += 1
                # Normalize payload across different SignalR client shapes
                cid = ""
                data = {}
                if len(args) >= 2:
                    cid = args[0] or ""
                    data = args[1] or {}
                elif len(args) == 1:
                    maybe = args[0]
                    if isinstance(maybe, dict):
                        data = maybe
                        cid = data.get("contractId") or ""
                    elif isinstance(maybe, (list, tuple)) and len(maybe) >= 2:
                        cid = maybe[0] or ""
                        data = maybe[1] or {}
                    else:
                        data = {}
                symbol = ""
                if isinstance(cid, str) and "." in cid:
                    parts = cid.split(".")
                    symbol = parts[-2].upper() if len(parts) >= 2 else cid
                if not symbol:
                    sym_id = (data.get("symbol") or data.get("symbolId") or "").upper()
                    if sym_id and "." in sym_id:
                        parts = sym_id.split(".")
                        symbol = parts[-1].upper()
                    elif sym_id:
                        symbol = sym_id
                if not symbol:
                    if self._missing_symbol_log_count < 5:
                        logger.warning(f"⚠️  Received quote payload without resolvable symbol. cid={cid}, data_keys={list(data.keys())}")
                        self._missing_symbol_log_count += 1
                    return
                with self._quote_cache_lock:
                    entry = self._quote_cache.setdefault(symbol, {})
                    # GatewayQuote payload fields per docs
                    if "bestBid" in data:
                        entry["bid"] = data.get("bestBid")
                    if "bestAsk" in data:
                        entry["ask"] = data.get("bestAsk")
                    if "lastPrice" in data:
                        entry["last"] = data.get("lastPrice")
                    if "volume" in data:
                        entry["volume"] = data.get("volume")
                    entry["ts"] = datetime.now(datetime.UTC).isoformat()
                
                # Feed quote to bar aggregator for real-time bar updates
                if hasattr(self, 'bar_aggregator') and self.bar_aggregator:
                    last_price = data.get("lastPrice")
                    volume = data.get("volume", 0)
                    if last_price is not None:
                        try:
                            self.bar_aggregator.add_quote(
                                symbol=symbol,
                                price=float(last_price),
                                volume=int(volume) if volume else 0,
                                timestamp=datetime.now(datetime.UTC)
                            )
                            # Log first few quotes per symbol to verify flow
                            if not hasattr(self, '_quote_log_count'):
                                self._quote_log_count = {}
                            count = self._quote_log_count.get(symbol, 0)
                            if count < 5:
                                logger.info(f"📈 Quote #{count+1} for {symbol}: ${last_price} (vol: {volume}) → bar aggregator")
                                self._quote_log_count[symbol] = count + 1
                            elif count == 5:
                                logger.info(f"📈 Quote flow confirmed for {symbol} (suppressing further logs)")
                                self._quote_log_count[symbol] = count + 1
                        except Exception as e:
                            logger.debug(f"Error adding quote to bar aggregator for {symbol}: {e}")
            except Exception as e:
                logger.debug(f"Failed processing quote message: {e}")

        def on_depth(*args):
            try:
                # Handle depth/orderbook data similar to quotes
                cid = ""
                data = {}
                if len(args) >= 2:
                    cid = args[0] or ""
                    data = args[1] or {}
                elif len(args) == 1:
                    maybe = args[0]
                    if isinstance(maybe, dict):
                        data = maybe
                        cid = data.get("contractId") or ""
                    elif isinstance(maybe, (list, tuple)) and len(maybe) >= 2:
                        cid = maybe[0] or ""
                        data = maybe[1] or {}
                    else:
                        data = {}
                
                symbol = ""
                if isinstance(cid, str) and "." in cid:
                    parts = cid.split(".")
                    symbol = parts[-2].upper() if len(parts) >= 2 else cid
                if not symbol:
                    sym_id = (data.get("symbol") or data.get("symbolId") or "").upper()
                    if sym_id and "." in sym_id:
                        parts = sym_id.split(".")
                        symbol = parts[-1].upper()
                    elif sym_id:
                        symbol = sym_id
                if not symbol:
                    return
                
                with self._depth_cache_lock:
                    entry = self._depth_cache.setdefault(symbol, {})
                    # Handle different depth data formats
                    if "bids" in data:
                        entry["bids"] = data.get("bids", [])
                    if "asks" in data:
                        entry["asks"] = data.get("asks", [])
                    if "orderBook" in data:
                        order_book = data.get("orderBook", {})
                        entry["bids"] = order_book.get("bids", [])
                        entry["asks"] = order_book.get("asks", [])
                    entry["ts"] = datetime.now(datetime.UTC).isoformat()
            except Exception as e:
                logger.debug(f"Failed processing depth message: {e}")

        hub.on_open(on_open)
        hub.on_close(on_close)
        hub.on_error(on_error)
        # Register multiple likely quote event names; env var takes precedence
        event_names = [
            self._market_hub_quote_event,
            "GatewayQuote",
            "GatewayQuoteWithConflation",
            "ContractQuote",
            "Quote",
            "RealtimeQuote",
            "ConflatedQuote",
        ]
        seen = set()
        for ev in event_names:
            if ev and ev not in seen:
                try:
                    hub.on(ev, on_quote)
                    seen.add(ev)
                    logger.debug(f"Registered SignalR quote handler for event '{ev}'")
                except Exception as register_err:
                    logger.debug(f"Failed to register quote handler for '{ev}': {register_err}")
        
        # Register depth event handlers
        depth_event_names = ["Depth", "OrderBook", "Level2", "MarketDepth", "GatewayDepth"]
        for ev in depth_event_names:
            try:
                hub.on(ev, on_depth)
            except Exception:
                pass

        # Start the hub (non-blocking)
        hub.start()
        self._market_hub = hub

        # Wait until connection opens before allowing subscriptions
        import time
        start = time.time()
        while not self._market_hub_connected and time.time() - start < 10:
            time.sleep(0.05)

    async def _ensure_quote_subscription(self, symbol: str) -> None:
        """
        Subscribe to real-time quotes using ProjectX Gateway Market Hub API.
        
        Now uses WebSocketManager for subscriptions, maintaining backward compatibility.
        """
        # Use WebSocketManager if available
        if hasattr(self, 'websocket_manager'):
            success = await self.websocket_manager.subscribe_quote(symbol)
            if success:
                self._subscribed_symbols.add(symbol.upper())
            return
        
        # Fallback to legacy SignalR implementation
        sym = symbol.upper()
        if sym in self._subscribed_symbols:
            return
        if not self._market_hub_connected:
            self._pending_symbols.add(sym)
            logger.debug(f"Queued subscription for {sym} until hub connects")
            return
        try:
            # Get contract ID (e.g., CON.F.US.MNQ.Z25)
            try:
                contract_id = self._get_contract_id(sym)
            except ValueError as e:
                logger.warning(f"Cannot subscribe to quotes for {sym}: {e}")
                return
            
            # Per ProjectX docs: invoke SubscribeContractQuotes with contract ID string
            logger.info(f"📡 Subscribing to live quotes for {sym} (contract: {contract_id})")
            self._market_hub.send("SubscribeContractQuotes", [contract_id])
            
            self._subscribed_symbols.add(sym)
            logger.info(f"✅ Subscribed to GatewayQuote events for {sym} via {contract_id}")
            
        except Exception as e:
            logger.error(f"❌ Failed to subscribe to quotes for {sym}: {e}")
            import traceback
            logger.debug(traceback.format_exc())
    
    async def _ensure_depth_subscription(self, symbol: str) -> None:
        """
        Subscribe to market depth data via SignalR.
        
        Now uses WebSocketManager for depth subscriptions, maintaining backward compatibility.
        """
        # Use WebSocketManager if available
        if hasattr(self, 'websocket_manager'):
            await self.websocket_manager.subscribe_depth(symbol)
            return
        
        # Fallback to legacy SignalR implementation
        sym = symbol.upper()
        if not self._market_hub_connected:
            logger.debug(f"Market hub not connected, cannot subscribe to depth for {sym}")
            return
        try:
            # Subscribe to depth data - try different possible method names
            try:
                cid = self._get_contract_id(sym)
            except ValueError as e:
                logger.warning(f"Cannot subscribe to depth for {sym}: {e}")
                return
            
            # Try depth subscription methods (following pattern from SubscribeContractQuotes)
            # Note: Depth data may be auto-subscribed or use different method names
            depth_methods = [
                "SubscribeContractDepth",  # Following SubscribeContractQuotes pattern
                "SubscribeDepth",          # Alternative
                "SubscribeOrderBook",       # Legacy attempt (may not exist)
                "SubscribeLevel2"          # Legacy attempt (may not exist)
            ]
            
            subscribed = False
            for method in depth_methods:
                try:
                    self._market_hub.send(method, [cid])
                    logger.debug(f"Attempted depth subscription for {sym} via {cid} using {method}")
                    # Don't log success immediately - wait to see if it actually works
                    subscribed = True
                    break  # If no exception, assume it worked
                except Exception as e:
                    error_str = str(e)
                    # Only log if it's not a "method does not exist" error (expected for wrong methods)
                    if "does not exist" not in error_str.lower() and "Method" not in error_str:
                        logger.debug(f"Depth method {method} failed: {e}")
                    continue
            
            if subscribed:
                logger.debug(f"Depth subscription attempted for {sym} - will use REST API fallback if SignalR fails")
            else:
                logger.debug(f"All depth subscription methods failed for {sym} - will use REST API fallback")
                
        except Exception as e:
            logger.debug(f"Depth subscription error for {sym}: {e} (will use REST API fallback)")
        
        if not self.api_key or not self.username:
            raise ValueError("API key and username must be provided either as parameters or environment variables")
    
    def _create_http_session(self) -> requests.Session:
        """
        Create a reusable HTTP session with connection pooling.
        This significantly improves performance by reusing TCP connections.
        
        Returns:
            requests.Session: Configured session with connection pooling
        """
        session = requests.Session()
        
        # Configure connection pooling
        # Use HTTPAdapter with connection pool for better performance
        adapter = HTTPAdapter(
            pool_connections=10,  # Number of connection pools to cache
            pool_maxsize=20,  # Maximum number of connections to save in the pool
            max_retries=Retry(
                total=3,
                backoff_factor=0.3,
                status_forcelist=[500, 502, 503, 504],
                allowed_methods=["GET", "POST"]
            )
        )
        
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        
        return session
    
    def _make_curl_request(self, method: str, endpoint: str, data: Dict = None, headers: Dict = None, skip_rate_limit: bool = False, suppress_errors: bool = False) -> Dict:
        """
        Make HTTP request using requests library with connection pooling and rate limiting.
        
        This replaces the previous subprocess curl implementation for better performance.
        Connection pooling reduces latency by reusing TCP connections.
        Rate limiting prevents API rate limit violations.
        
        Args:
            method: HTTP method (GET, POST, etc.)
            endpoint: API endpoint
            data: Request data (for POST requests)
            headers: Request headers
            skip_rate_limit: If True, skip rate limiting (for critical operations)
            suppress_errors: If True, log errors as debug instead of error (for expected failures)
            
        Returns:
            Dict: Response data
        """
        # Start performance tracking
        start_time = time.time()
        status_code = None
        success = False
        error_message = None
        
        # Check if token is expired (synchronous check)
        # Note: Actual refresh must be done by caller if 401/403 is returned
        if endpoint != "/api/Auth/loginKey" and self._is_token_expired():
            logger.warning("⚠️  Token expired or missing - request may fail. Caller should refresh token.")
        
        # Apply rate limiting (unless skipped for critical operations)
        if not skip_rate_limit:
            self._rate_limiter.acquire()
        
        try:
            url = f"{self.base_url}{endpoint}"
            
            # Get timeout from environment or use default
            api_timeout = int(os.getenv('API_TIMEOUT', '30'))
            
            # Prepare request kwargs
            request_kwargs = {
                'timeout': api_timeout,
                'headers': headers or {}
            }
            
            # Add JSON data for POST/PUT requests
            if data and method.upper() in ('POST', 'PUT', 'PATCH'):
                # Remove None values from data (TopStepX API may reject None values)
                cleaned_data = {k: v for k, v in data.items() if v is not None}
                request_kwargs['json'] = cleaned_data
                if 'Content-Type' not in request_kwargs['headers']:
                    request_kwargs['headers']['Content-Type'] = 'application/json'
            
            # Log request details (especially for order placement)
            if endpoint == "/api/Order/place" and data:
                logger.info(f"📤 Sending order to /api/Order/place:")
                logger.info(f"   JSON payload: {json.dumps(data, indent=2)}")
                logger.info(f"   Token: {self.session_token[:20] + '...' if self.session_token else 'MISSING'}")
            
            logger.debug(f"HTTP {method} request to {endpoint}")
            
            # Make request using session (connection pooling enabled)
            response = self._http_session.request(
                method=method,
                url=url,
                **request_kwargs
            )
            
            status_code = response.status_code
            
            # Handle response
            try:
                response.raise_for_status()  # Raise exception for bad status codes
            except requests.exceptions.HTTPError as e:
                error_message = f"HTTP {response.status_code}: {str(e)}"
                if suppress_errors:
                    logger.debug(f"HTTP error {response.status_code}: {e}")
                else:
                    logger.error(f"HTTP error {response.status_code}: {e}")
                    # Log response body for 500 errors to help debug
                    if status_code == 500:
                        try:
                            error_body = response.text[:500]
                            logger.error(f"   500 Error response body: {error_body}")
                        except:
                            pass
                return {"error": error_message}
            
            # Parse JSON response
            try:
                # Handle empty response (common for successful operations)
                if not response.text.strip():
                    success = True
                    return {"success": True, "message": "Operation completed successfully"}
                
                response_data = response.json()
                success = True
                return response_data
            except json.JSONDecodeError as e:
                error_message = f"Invalid JSON response: {e}"
                logger.error(f"Failed to parse JSON response: {e}")
                logger.error(f"Raw response: {response.text[:500]}")  # Log first 500 chars
                return {"error": error_message}
                
        except requests.exceptions.Timeout:
            error_message = "Request timed out"
            logger.error(f"HTTP request timed out after {api_timeout}s")
            return {"error": error_message}
        except requests.exceptions.ConnectionError as e:
            error_message = f"Connection error: {str(e)}"
            logger.error(f"HTTP connection error: {e}")
            return {"error": error_message}
        except Exception as e:
            error_message = str(e)
            logger.error(f"HTTP request failed: {str(e)}")
            return {"error": str(e)}
        finally:
            # Record performance metrics
            duration_ms = (time.time() - start_time) * 1000
            try:
                metrics_tracker = get_metrics_tracker(db=getattr(self, 'db', None))
                metrics_tracker.record_api_call(
                    endpoint=endpoint,
                    method=method,
                    duration_ms=duration_ms,
                    status_code=status_code,
                    success=success,
                    error_message=error_message
                )
            except Exception as metrics_err:
                logger.debug(f"Failed to record metrics: {metrics_err}")
    
    async def authenticate(self) -> bool:
        """
        Authenticate with the TopStepX API using username and API key.
        
        Returns:
            bool: True if authentication successful, False otherwise
        """
        try:
            logger.info("Authenticating with TopStepX API...")
            
            # Prepare login data
            login_data = {
                "userName": self.username,
                "apiKey": self.api_key
            }
            
            # Set headers for login request
            headers = {
                "accept": "text/plain",
                "Content-Type": "application/json"
            }
            
            # Make login request
            response = self._make_curl_request("POST", "/api/Auth/loginKey", data=login_data, headers=headers)
            
            if "error" in response:
                logger.error(f"Authentication failed: {response['error']}")
                return False
            
            # Check if login was successful
            if response.get("success") and response.get("token"):
                self.session_token = response["token"]
                
                # Parse JWT to extract expiration time
                try:
                    import jwt
                    import json
                    # Decode without verification (we trust the server's token)
                    decoded = jwt.decode(self.session_token, options={"verify_signature": False})
                    exp_timestamp = decoded.get("exp")
                    if exp_timestamp:
                        from datetime import datetime, timezone
                        self.token_expiry = datetime.fromtimestamp(exp_timestamp, tz=timezone.utc)
                        logger.info(f"Token expires at: {self.token_expiry}")
                    else:
                        # Default to 30 minutes if no expiry in token
                        from datetime import datetime, timedelta, timezone
                        self.token_expiry = datetime.now(timezone.utc) + timedelta(minutes=30)
                        logger.warning("No expiry in JWT, assuming 30 minute lifetime")
                except ImportError:
                    # If PyJWT not installed, fall back to base64 decoding
                    try:
                        import base64
                        import json
                        # JWT format: header.payload.signature
                        parts = self.session_token.split('.')
                        if len(parts) >= 2:
                            # Decode payload (add padding if needed)
                            payload = parts[1]
                            payload += '=' * (4 - len(payload) % 4)
                            decoded = json.loads(base64.urlsafe_b64decode(payload))
                            exp_timestamp = decoded.get("exp")
                            if exp_timestamp:
                                from datetime import datetime, timezone
                                self.token_expiry = datetime.fromtimestamp(exp_timestamp, tz=timezone.utc)
                                logger.info(f"Token expires at: {self.token_expiry}")
                            else:
                                from datetime import datetime, timedelta, timezone
                                self.token_expiry = datetime.now(timezone.utc) + timedelta(minutes=30)
                        else:
                            from datetime import datetime, timedelta, timezone
                            self.token_expiry = datetime.now(timezone.utc) + timedelta(minutes=30)
                    except Exception as parse_err:
                        # If parsing fails, assume 30 minute lifetime
                        from datetime import datetime, timedelta, timezone
                        self.token_expiry = datetime.now(timezone.utc) + timedelta(minutes=30)
                        logger.warning(f"Failed to parse token expiry: {parse_err}, assuming 30 minute lifetime")
                except Exception as decode_err:
                    # If decoding fails, assume 30 minute lifetime
                    from datetime import datetime, timedelta, timezone
                    self.token_expiry = datetime.now(timezone.utc) + timedelta(minutes=30)
                    logger.warning(f"Failed to decode token: {decode_err}, assuming 30 minute lifetime")
                
                logger.info(f"Successfully authenticated as: {self.username}")
                logger.info(f"Session token obtained: {self.session_token[:20]}...")
                # Best-effort start market hub after auth for real-time quotes
                try:
                    await self._ensure_market_socket_started()
                except Exception as sock_err:
                    logger.warning(f"Failed to start market hub (will fallback to REST): {sock_err}")
                return True
            else:
                error_msg = response.get("errorMessage", "Unknown error")
                logger.error(f"Authentication failed: {error_msg}")
                return False
            
        except Exception as e:
            logger.error(f"Authentication failed: {str(e)}")
            return False
    
    def _is_token_expired(self) -> bool:
        """
        Check if the JWT token is expired or close to expiring.
        Refresh proactively if less than 5 minutes remaining.
        
        Uses AuthManager for token expiration checking.
        
        Returns:
            bool: True if token needs refresh
        """
        # Use AuthManager's token expiration check
        expired = self.auth_manager._is_token_expired()
        
        # Sync state for backward compatibility
        if expired:
            self.session_token = None
            self.token_expiry = None
        
        return expired
    
    async def _ensure_valid_token(self) -> bool:
        """
        Ensure we have a valid, non-expired JWT token.
        Automatically refreshes if needed.
        
        Uses AuthManager for token management.
        
        Returns:
            bool: True if token is valid/refreshed successfully
        """
        # Use AuthManager's ensure_valid_token
        success = await self.auth_manager.ensure_valid_token()
        
        if success:
            # Sync session token and expiry for backward compatibility
            self.session_token = self.auth_manager.session_token
            self.token_expiry = self.auth_manager.token_expiry
        
        return success
    
    async def list_accounts(self) -> List[Dict]:
        """
        List all active accounts for the authenticated user.
        
        Uses AuthManager for account listing, maintaining backward compatibility.
        
        Returns:
            List[Dict]: List of account information
        """
        try:
            # Use AuthManager's list_accounts method
            accounts = await self.auth_manager.list_accounts()
            
            logger.info(f"Found {len(accounts)} active accounts")
            return accounts
            
        except Exception as e:
            logger.error(f"Failed to fetch accounts: {str(e)}")
            return []
    
    def display_accounts(self, accounts: List[Dict]) -> None:
        """
        Display accounts in a formatted table.
        
        Args:
            accounts: List of account dictionaries
        """
        if not accounts:
            print("No accounts found.")
            return
        
        print("\n" + "="*80)
        print("ACTIVE ACCOUNTS")
        print("="*80)
        print(f"{'#':<3} {'Account Name':<30} {'ID':<12} {'Status':<10} {'Balance':<12} {'Type':<12}")
        print("-"*80)
        
        for idx, account in enumerate(accounts, 1):
            balance = f"${account.get('balance', 0):,.2f}"
            print(f"{idx:<3} {account.get('name', 'N/A'):<30} {account.get('id', 'N/A'):<12} "
                  f"{account.get('status', 'N/A'):<10} {balance:<12} {account.get('account_type', 'N/A'):<12}")
        
        print("="*80)
    
    def select_account(self, accounts: List[Dict]) -> Optional[Dict]:
        """
        Allow user to select an account for trading.
        
        Args:
            accounts: List of account dictionaries
            
        Returns:
            Optional[Dict]: Selected account or None if invalid selection
        """
        if not accounts:
            print("No accounts available for selection.")
            return None
        
        while True:
            try:
                print(f"\nSelect an account to trade on (1-{len(accounts)}, or 'q' to quit):")
                choice = input("Enter your choice: ").strip().lower()
                
                if choice == 'q':
                    print("Exiting account selection.")
                    return None
                
                account_index = int(choice) - 1
                
                if 0 <= account_index < len(accounts):
                    selected_account = accounts[account_index]
                    self.selected_account = selected_account
                    
                    # Update OrderExecutor's selected account
                    self.order_executor.set_selected_account(selected_account)
                    
                    print(f"\n✓ Selected Account: {selected_account['name']}")
                    print(f"  Account ID: {selected_account['id']}")
                    print(f"  Balance: ${selected_account.get('balance', 0):,.2f}")
                    print(f"  Status: {selected_account.get('status', 'N/A')}")
                    
                    # Initialize account tracker with this account
                    account_balance = selected_account.get('balance', 0)
                    account_type = selected_account.get('type', 'unknown')
                    self.account_tracker.initialize(
                        account_id=selected_account['id'],
                        starting_balance=account_balance,
                        account_type=account_type
                    )
                    logger.info(f"Account tracker initialized for {selected_account['name']} (${account_balance:,.2f})")
                    
                    return selected_account
                else:
                    print(f"Invalid choice. Please enter a number between 1 and {len(accounts)}.")
                    
            except ValueError:
                print("Invalid input. Please enter a number or 'q' to quit.")
            except KeyboardInterrupt:
                print("\nExiting account selection.")
                return None
    
    async def get_account_balance(self, account_id: str = None) -> Optional[float]:
        """
        Get the current balance for the selected account.
        Since we already have balance info from account listing, we'll use that.
        
        Args:
            account_id: Account ID (uses selected account if not provided)
            
        Returns:
            Optional[float]: Account balance or None if error
        """
        try:
            target_account = account_id or (self.selected_account['id'] if self.selected_account else None)
            
            if not target_account:
                logger.error("No account selected")
                return None
            
            # Use balance from selected account (already fetched during account listing)
            if self.selected_account and str(self.selected_account['id']) == str(target_account):
                balance = self.selected_account.get('balance', 0.0)
                logger.info(f"Using cached balance for account {target_account}: ${balance:,.2f}")
                return float(balance)
            
            # If we need to fetch balance for a different account, refresh account list
            logger.info(f"Refreshing account list to get balance for account {target_account}")
            accounts = await self.list_accounts()
            
            for account in accounts:
                if str(account['id']) == str(target_account):
                    balance = account.get('balance', 0.0)
                    logger.info(f"Found balance for account {target_account}: ${balance:,.2f}")
                    return float(balance)
            
            logger.warning(f"Account {target_account} not found in account list")
            return None
            
        except Exception as e:
            logger.error(f"Failed to get account balance: {str(e)}")
            return None
    
    async def get_account_info(self, account_id: str = None) -> Dict:
        """
        Get detailed account information including positions and orders.
        
        Args:
            account_id: Account ID (uses selected account if not provided)
            
        Returns:
            Dict: Account information or error
        """
        try:
            # If account_id is provided but selected_account is not set, find and set it
            if account_id and not self.selected_account:
                accounts = await self.list_accounts()
                for acc in accounts:
                    if str(acc.get('id')) == str(account_id):
                        self.selected_account = acc
                        break
            
            target_account = account_id or (self.selected_account['id'] if self.selected_account else None)
            
            if not target_account:
                return {"error": "No account selected or provided"}
            
            if not self.session_token:
                return {"error": "No session token available. Please authenticate first."}
            
            logger.info(f"Fetching account info for account {target_account}")
            
            headers = {
                "accept": "text/plain",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.session_token}"
            }
            
            # Try different account info endpoints
            endpoints_to_try = [
                f"/api/Account/{target_account}",
                f"/api/Account/{target_account}/info",
                f"/api/Account/{target_account}/details",
                f"/api/Account/{target_account}/summary"
            ]
            
            for endpoint in endpoints_to_try:
                try:
                    logger.debug(f"Trying account info endpoint: {endpoint}")
                    response = self._make_curl_request("GET", endpoint, headers=headers, suppress_errors=True)
                    
                    if "error" not in response and response:
                        logger.info(f"Found account info from {endpoint}")
                        return response
                    else:
                        logger.debug(f"Endpoint {endpoint} failed: {response.get('error', 'Unknown error')}")
                        continue
                        
                except Exception as e:
                    logger.warning(f"Endpoint {endpoint} failed with exception: {e}")
                    continue
            
            # If no endpoint worked, return minimal info from cached account data
            logger.debug("All account info endpoints failed - using cached account data")
            if self.selected_account:
                # Try to get current balance if available
                current_balance = None
                try:
                    current_balance = await self.get_account_balance(target_account)
                except Exception:
                    pass
                
                account_info = {
                    "id": self.selected_account.get('id'),
                    "name": self.selected_account.get('name', 'Unknown'),
                    "balance": current_balance or self.selected_account.get('balance', 0),
                    "status": self.selected_account.get('status', 'unknown'),
                    "type": self.selected_account.get('type', 'unknown'),
                    "note": "Detailed account info endpoints not available - showing cached basic info",
                    "success": True  # Mark as success even though using cached data
                }
                
                # Add compliance/risk info if we can get it from account state
                try:
                    positions = await self.get_open_positions(target_account)
                    orders = await self.get_open_orders(target_account)
                    account_info["positions_count"] = len(positions) if positions else 0
                    account_info["orders_count"] = len(orders) if orders else 0
                except Exception:
                    pass
                
                return account_info
            return {"error": "Could not fetch account info - no account selected"}
            
        except Exception as e:
            logger.error(f"Failed to fetch account info: {str(e)}")
            return {"error": str(e)}
    
    def _get_contract_id(self, symbol: str) -> str:
        """
        Convert trading symbol to TopStepX contract ID format.
        
        Now uses ContractManager for contract ID resolution, maintaining backward compatibility.
        
        Args:
            symbol: Trading symbol (e.g., "ES", "NQ", "MNQ", "YM")
            
        Returns:
            str: Contract ID in TopStepX format
            
        Raises:
            ValueError: If contract cache is empty or symbol not found
        """
        try:
            # Use ContractManager for contract ID resolution
            contract_id = self.contract_manager.get_contract_id(symbol)
            return contract_id
        except ValueError as e:
            # Re-raise with same error message for backward compatibility
            raise e
    
    def _clear_contract_cache(self) -> None:
        """Clear the contract list cache (useful for testing or forced refresh)."""
        with self._contract_cache_lock:
            self._contract_cache = None
            logger.debug("Contract cache cleared")
    
    def _get_symbol_from_contract_id(self, contract_id: str) -> str:
        """Get symbol from contract ID"""
        # Reverse map contract IDs to symbols
        contract_map = {
            "CON.F.US.ES.Z25": "ES",
            "CON.F.US.NQ.Z25": "NQ",
            "CON.F.US.MNQ.Z25": "MNQ", 
            "CON.F.US.YM.Z25": "YM",
            "CON.F.US.MGC.Z25": "MGC",
            "CON.F.US.MES.Z25": "MES",
            "CON.F.US.MYM.Z25": "MYM"
        }
        
        return contract_map.get(contract_id, contract_id)
    
    def _derive_symbol_id_from_contract(self, contract_id: Optional[str]) -> Optional[str]:
        """
        Convert contract identifiers like CON.F.US.MNQ.Z25 into signal/REST-friendly symbol ids (F.US.MNQ).
        """
        if not contract_id:
            return None
        if contract_id.startswith("CON."):
            parts = contract_id.split(".")
            if len(parts) >= 4:
                return ".".join(parts[1:-1])
        return None
    
    def _symbol_variants_for_subscription(self, symbol: str) -> List[str]:
        """
        Build ordered list of identifier variants (contract id, symbol id, @root, etc.)
        to maximize compatibility with different market data endpoints.
        """
        sym = (symbol or "").upper()
        variants: List[str] = []
        try:
            contract_id = self._get_contract_id(sym)
            if contract_id:
                variants.append(contract_id)
        except ValueError:
            # Contract not found - will try other variants below
            pass
            derived = self._derive_symbol_id_from_contract(contract_id)
            if derived:
                variants.append(derived)
            parts = contract_id.split(".")
            if len(parts) >= 4:
                root = parts[-2]
                month = parts[-1]
                if root and month:
                    variants.append(f"{root}{month}")
                if root:
                    variants.append(f"@{root}")
                    variants.append(root)
        if sym:
            variants.append(sym)
        seen = set()
        ordered: List[str] = []
        for v in variants:
            if v and v not in seen:
                ordered.append(v)
                seen.add(v)
        return ordered
    
    async def check_order_fills(self, account_id: str = None) -> Dict:
        """Check for filled orders and send Discord notifications"""
        try:
            target_account = account_id or (self.selected_account['id'] if self.selected_account else None)

            if not target_account:
                return {"error": "No account selected"}

            # Get order history to check for fills - limit to recent orders only
            orders = await self.get_order_history(target_account, limit=10)  # Reduced from 50 to 10
            filled_orders = []

            account_key = str(target_account)

            # Warm-up notifications after restart so we don't re-announce historical fills
            if not self._notification_warmup_done.get(account_key):
                warmup_count = 0
                for order in orders:
                    order_id = str(order.get('id', ''))
                    status = order.get('status', '')
                    if isinstance(status, int):
                        is_filled = status in [2, 3, 4]
                    else:
                        status_str = str(status).lower()
                        is_filled = status_str in ['filled', 'executed', 'complete']

                    if is_filled:
                        unique_id = f"{account_key}:{order_id}"
                        self._notified_orders.add(unique_id)
                        warmup_count += 1

                if warmup_count:
                    logger.info(f"🔕 Notification warm-up: marked {warmup_count} existing filled orders for account {account_key}")
                self._notification_warmup_done[account_key] = True

            for order in orders:
                order_id = str(order.get('id', ''))
                unique_id = f"{account_key}:{order_id}"
                if unique_id in self._notified_orders:
                    continue  # Already notified

                # Check if order is filled
                status = order.get('status', '')
                # Handle both string and integer status values
                if isinstance(status, int):
                    # Status codes: 1=Open, 2=Filled, 3=Executed, 4=Complete, 5=Cancelled
                    is_filled = status in [2, 3, 4]
                else:
                    # Handle string status
                    status_str = str(status).lower()
                    is_filled = status_str in ['filled', 'executed', 'complete']
                
                if is_filled:
                    # CRITICAL: Only notify for orders we placed (with customTag) - check BEFORE processing
                    custom_tag = order.get('customTag', '')
                    if not custom_tag or not custom_tag.startswith('TradingBot-v1.0'):
                        # Skip orders not placed by our bot, but still mark as notified to avoid re-checking
                        self._notified_orders.add(unique_id)
                        continue
                    
                    # Additional validation: ensure order has a fill price (actually filled, not just status change)
                    fill_price = order.get('fillPrice') or order.get('executionPrice') or order.get('filledPrice')
                    if not fill_price:
                        logger.debug(f"Order {order_id} marked as filled but has no fill price - skipping notification")
                        self._notified_orders.add(unique_id)
                        continue
                    
                    # Get order details
                    symbol = self._get_symbol_from_contract_id(order.get('contractId', ''))
                    side = 'BUY' if order.get('side', 0) == 0 else 'SELL'
                    quantity = order.get('size', 0)
                    order_type = order.get('type', 0)

                    # Map order type to string
                    type_map = {1: 'Limit', 2: 'Market', 4: 'Stop', 5: 'Stop Limit'}
                    order_type_str = type_map.get(order_type, 'Unknown')

                    # Get position ID if available
                    position_id = order.get('positionId', 'Unknown')

                    # Send Discord notification
                    try:
                        account_name = self.selected_account.get('name', 'Unknown') if self.selected_account else 'Unknown'
                            
                        notification_data = {
                            'symbol': symbol,
                            'side': side,
                            'quantity': quantity,
                            'fill_price': f"${float(fill_price):.2f}" if fill_price else "Unknown",
                            'order_type': order_type_str,
                            'order_id': order_id,
                            'position_id': position_id
                        }

                        logger.info(f"📢 Sending Discord notification for filled order: {order_id} ({symbol} {side} x{quantity} @ ${fill_price})")
                        self.discord_notifier.send_order_fill_notification(notification_data, account_name)
                        self._notified_orders.add(unique_id)
                        filled_orders.append(order_id)

                    except Exception as notif_err:
                        logger.warning(f"Failed to send order fill notification: {notif_err}")
                        # Still mark as notified to avoid retrying
                        self._notified_orders.add(unique_id)

            # Also check for position closes (manual closes, TP hits, stop hits)
            await self._check_position_closes(target_account)

            # Check for any order fills that might have closed positions
            await self._check_order_fills_for_closes(target_account)

            return {
                "success": True,
                "checked_orders": len(orders),
                "filled_orders": len(filled_orders),
                "new_fills": filled_orders
            }

        except Exception as e:
            logger.error(f"Failed to check order fills: {str(e)}")
            return {"error": str(e)}
    
    async def _check_position_closes(self, account_id: str) -> None:
        """Check for position closes and send notifications"""
        try:
            # Get current positions
            current_positions = await self.get_open_positions(account_id)
            current_position_ids = {str(pos.get('id', '')) for pos in current_positions}
            
            # Check if we have any previously tracked positions that are now closed
            if hasattr(self, '_tracked_positions'):
                for tracked_id in list(self._tracked_positions):
                    if tracked_id not in current_position_ids:
                        # Position was closed - check if we already notified
                        if tracked_id in self._notified_positions:
                            continue
                        
                        # Position was closed
                        position_data = self._tracked_positions[tracked_id]

                        # Send close notification
                        try:
                            account_name = self.selected_account.get('name', 'Unknown') if self.selected_account else 'Unknown'

                            # Get current market price for exit price
                            symbol = position_data.get('symbol', 'Unknown')
                            exit_price = "Unknown"
                            close_method = "Unknown"
                            
                            try:
                                quote = await self.get_market_quote(symbol)
                                if "error" not in quote:
                                    if position_data.get('side', 0) == 0:  # Long position
                                        exit_price = quote.get("bid") or quote.get("last")
                                    else:  # Short position
                                        exit_price = quote.get("ask") or quote.get("last")
                                    if exit_price:
                                        exit_price = f"${float(exit_price):.2f}"
                            except Exception as price_err:
                                logger.warning(f"Could not fetch exit price: {price_err}")

                            # Try to determine close method by checking recent order history
                            try:
                                recent_orders = await self.get_order_history(account_id, limit=10)
                                for order in recent_orders:
                                    if (order.get('positionId') == tracked_id and 
                                        order.get('status') in [2, 3, 4] and  # Filled/Executed/Complete
                                        order.get('positionDisposition') == 'Closing'):
                                        
                                        order_type = order.get('type', 0)
                                        if order_type == 4:  # Stop order
                                            close_method = "Stop Loss Hit"
                                        elif order_type == 1:  # Limit order
                                            close_method = "Take Profit Hit"
                                        elif order_type == 2:  # Market order
                                            close_method = "Manual Close"
                                        else:
                                            close_method = "Order Close"
                                        break
                            except Exception as method_err:
                                logger.warning(f"Could not determine close method: {method_err}")

                            notification_data = {
                                'symbol': symbol,
                                'side': 'LONG' if position_data.get('side', 0) == 0 else 'SHORT',
                                'quantity': position_data.get('size', 0),
                                'entry_price': f"${position_data.get('entryPrice', 0):.2f}",
                                'exit_price': exit_price,
                                'pnl': position_data.get('unrealizedPnl', 0),
                                'close_method': close_method,
                                'position_id': tracked_id
                            }

                            self.discord_notifier.send_position_close_notification(notification_data, account_name)
                            self._notified_positions.add(tracked_id)

                        except Exception as notif_err:
                            logger.warning(f"Failed to send position close notification: {notif_err}")

                        # Remove from tracked positions
                        del self._tracked_positions[tracked_id]
            
            # Track new positions
            if not hasattr(self, '_tracked_positions'):
                self._tracked_positions = {}
            
            for pos in current_positions:
                pos_id = str(pos.get('id', ''))
                if pos_id not in self._tracked_positions:
                    # New position - track it
                    self._tracked_positions[pos_id] = {
                        'symbol': self._get_symbol_from_contract_id(pos.get('contractId', '')),
                        'side': pos.get('side', 0),
                        'size': pos.get('size', 0),
                        'entryPrice': pos.get('entryPrice', 0),
                        'unrealizedPnl': pos.get('unrealizedPnl', 0)
                    }
                    
        except Exception as e:
            logger.error(f"Failed to check position closes: {str(e)}")
    
    async def _check_order_fills_for_closes(self, account_id: str) -> None:
        """Check for order fills that close positions and send notifications"""
        try:
            # Get order history to check for fills - limit to recent orders only
            orders = await self.get_order_history(account_id, limit=10)
            account_key = str(account_id)
            
            if not self._notification_warmup_done.get(account_key):
                warmup_count = 0
                for order in orders:
                    order_id = str(order.get('id', ''))
                    status = order.get('status', '')
                    if isinstance(status, int):
                        is_filled = status in [2, 3, 4]
                    else:
                        status_str = str(status).lower()
                        is_filled = status_str in ['filled', 'executed', 'complete']

                    if is_filled:
                        unique_id = f"{account_key}:{order_id}"
                        self._notified_orders.add(unique_id)
                        warmup_count += 1

                if warmup_count:
                    logger.info(f"🔕 Notification warm-up (close check): marked {warmup_count} filled orders for account {account_key}")
                self._notification_warmup_done[account_key] = True
            
            for order in orders:
                order_id = str(order.get('id', ''))
                unique_id = f"{account_key}:{order_id}"
                if unique_id in self._notified_orders:
                    continue  # Already notified
                
                # Check if order is filled and closes a position
                status = order.get('status', '')
                position_disposition = order.get('positionDisposition', '')
                
                # Handle both string and integer status values
                if isinstance(status, int):
                    # Status codes: 1=Open, 2=Filled, 3=Executed, 4=Complete, 5=Cancelled
                    is_filled = status in [2, 3, 4]
                else:
                    # Handle string status
                    status_str = str(status).lower()
                    is_filled = status_str in ['filled', 'executed', 'complete']
                
                logger.info(f"Checking order {order_id}: status={status}, disposition={position_disposition}")
                
                if is_filled and position_disposition == 'Closing':
                    # This is a closing order - send notification
                    try:
                        symbol = self._get_symbol_from_contract_id(order.get('contractId', ''))
                        side = 'BUY' if order.get('side', 0) == 0 else 'SELL'
                        quantity = order.get('size', 0)
                        fill_price = order.get('fillPrice') or order.get('executionPrice')
                        order_type = order.get('type', 0)
                        
                        # Map order type to string
                        type_map = {1: 'Limit', 2: 'Market', 4: 'Stop', 5: 'Stop Limit'}
                        order_type_str = type_map.get(order_type, 'Unknown')
                        
                        # Get position ID if available
                        position_id = order.get('positionId', 'Unknown')
                        
                        logger.info(f"Found closing order: {order_id} - {side} {quantity} {symbol} at ${fill_price}")
                        
                        # Send Discord notification for closing order
                        account_name = self.selected_account.get('name', 'Unknown') if self.selected_account else 'Unknown'
                        
                        notification_data = {
                            'symbol': symbol,
                            'side': side,
                            'quantity': quantity,
                            'fill_price': f"${float(fill_price):.2f}" if fill_price else "Unknown",
                            'order_type': f"{order_type_str} (Close)",
                            'order_id': order_id,
                            'position_id': position_id
                        }
                        
                        self.discord_notifier.send_order_fill_notification(notification_data, account_name)
                        self._notified_orders.add(unique_id)
                        
                        logger.info(f"Sent Discord notification for closing order {order_id}")
                        
                    except Exception as notif_err:
                        logger.warning(f"Failed to send closing order notification: {notif_err}")
                        
        except Exception as e:
            logger.error(f"Failed to check order fills for closes: {str(e)}")
    
    async def _get_tick_size(self, symbol: str) -> float:
        """
        Get the tick size for a trading symbol.
        
        Now uses RiskManager for tick size calculations, maintaining backward compatibility.
        
        Args:
            symbol: Trading symbol (e.g., "ES", "NQ", "MNQ", "YM")
            
        Returns:
            float: Tick size for the symbol
        """
        return self.risk_manager.get_tick_size(symbol)
    
    def _round_to_tick_size(self, price: float, tick_size: float) -> float:
        """
        Round price to nearest valid tick size.
        
        Now uses RiskManager for price rounding, maintaining backward compatibility.
        """
        return self.risk_manager.round_to_tick_size(price, tick_size)
    
    def _generate_unique_custom_tag(self, order_type: str = "order", strategy_name: str = None) -> str:
        """
        Generate a unique custom tag for orders.
        
        Args:
            order_type: Type of order (e.g., "market", "stop_bracket", "bracket")
            strategy_name: Optional strategy name to include in tag for tracking
        
        Returns:
            Custom tag string like "TradingBot-v1.0-strategy-overnight_range-order-123-1234567890"
        """
        self._order_counter += 1
        timestamp = int(datetime.now().timestamp())
        
        # Include strategy name in tag if provided
        if strategy_name:
            # Sanitize strategy name (remove spaces, special chars)
            clean_strategy = strategy_name.lower().replace(' ', '_').replace('-', '_')
            return f"{BOT_ORDER_TAG_PREFIX}-strategy-{clean_strategy}-{order_type}-{self._order_counter}-{timestamp}"
        else:
            return f"{BOT_ORDER_TAG_PREFIX}-{order_type}-{self._order_counter}-{timestamp}"

    async def place_market_order(self, symbol: str, side: str, quantity: int, account_id: str = None, 
                                stop_loss_ticks: int = None, take_profit_ticks: int = None, order_type: str = "market", 
                                limit_price: float = None, strategy_name: str = None) -> Dict:
        """
        Place a market or limit order on the selected account.
        
        Args:
            symbol: Trading symbol (e.g., "ES", "NQ", "MNQ", "YM")
            side: "BUY" or "SELL"
            quantity: Number of contracts
            account_id: Account ID (uses selected account if not provided)
            stop_loss_ticks: Optional stop loss in ticks
            take_profit_ticks: Optional take profit in ticks
            order_type: "market" or "limit"
            limit_price: Price for limit orders (required if order_type="limit")
            
        Returns:
            Dict: Order response or error
        """
        try:
            target_account = account_id or (self.selected_account['id'] if self.selected_account else None)
            
            if not target_account:
                return {"error": "No account selected"}
            
            if not self.session_token:
                return {"error": "No session token available. Please authenticate first."}
            
            if side.upper() not in ["BUY", "SELL"]:
                return {"error": "Side must be 'BUY' or 'SELL'"}
            
            if order_type.lower() not in ["market", "limit", "bracket"]:
                return {"error": "Order type must be 'market', 'limit', or 'bracket'"}
            
            if order_type.lower() == "limit" and limit_price is None:
                return {"error": "Limit price is required for limit orders"}
            
            logger.info(f"Placing {side} {order_type} order for {quantity} {symbol} on account {target_account}")
            if order_type.lower() == "limit":
                logger.info(f"Limit price: {limit_price}")
            
            # Convert side to numeric value (TopStepX API uses numbers)
            side_value = 0 if side.upper() == "BUY" else 1
            
            # Get proper contract ID
            try:
                contract_id = self._get_contract_id(symbol)
            except ValueError as e:
                error_msg = f"Cannot place order: {e}. Please fetch contracts first using 'contracts' command."
                logger.error(f"❌ {error_msg}")
                return {"error": error_msg}
            
            # Determine order type (TopStepX API uses numbers)
            if order_type.lower() == "limit":
                order_type_value = 1  # Limit order
            elif order_type.lower() == "bracket":
                order_type_value = 2  # Market order for entry, brackets handled separately
            else:
                order_type_value = 2  # Market order
            
            # Prepare order data for TopStepX API
            order_data = {
                "accountId": int(target_account),  # Ensure it's an integer
                "contractId": contract_id,
                "type": order_type_value,  # 1 = Limit order, 2 = Market order
                "side": side_value,  # 0 = Buy, 1 = Sell
                "size": quantity,
                "limitPrice": limit_price if order_type.lower() == "limit" else None,
                "stopPrice": None,
                "customTag": self._generate_unique_custom_tag("market", strategy_name)
            }
            
            # Add bracket orders if specified
            if stop_loss_ticks is not None or take_profit_ticks is not None:
                if stop_loss_ticks is not None:
                    order_data["stopLossBracket"] = {
                        "ticks": stop_loss_ticks,
                        "type": 4,  # Stop loss type
                        "size": quantity,
                        "reduceOnly": True
                    }
                
                if take_profit_ticks is not None:
                    order_data["takeProfitBracket"] = {
                        "ticks": take_profit_ticks,
                        "type": 1,  # Take profit type
                        "size": quantity,
                        "reduceOnly": True
                    }
            
            # EMERGENCY DEBUG LOGGING
            logger.info("===== ORDER PLACEMENT DEBUG =====")
            logger.info(f"Symbol: {symbol}, Side: {side}, Quantity: {quantity}")
            logger.info(f"Account ID: {target_account}")
            logger.info(f"Contract ID: {contract_id}")
            logger.info(f"Order Data: {json.dumps(order_data, indent=2)}")
            logger.info("=================================")
            
            # Make real API call to place order using session token
            headers = {
                "accept": "text/plain",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.session_token}"
            }
            
            response = self._make_curl_request("POST", "/api/Order/place", data=order_data, headers=headers)
            
            # Log FULL API response
            logger.info("===== API RESPONSE =====")
            logger.info(f"Response Type: {type(response)}")
            logger.info(f"Response Keys: {list(response.keys()) if isinstance(response, dict) else 'Not a dict'}")
            logger.info(f"Full Response: {json.dumps(response, indent=2) if isinstance(response, dict) else str(response)}")
            logger.info("========================")
            
            # Check for explicit errors first
            if "error" in response:
                logger.error(f"API returned error: {response['error']}")
                return response

            # Validate response structure
            if not isinstance(response, dict):
                logger.error(f"API returned non-dict response: {type(response)}")
                return {"error": f"Invalid API response type: {type(response)}"}

            # Check success field explicitly
            success = response.get("success")
            if success is False or success is None or success == "false":
                error_code = response.get("errorCode", "Unknown")
                error_message = response.get("errorMessage", response.get("message", "No error message"))
                logger.error(f"Order failed - success={success}, errorCode={error_code}, message={error_message}")
                logger.error(f"Full response: {json.dumps(response, indent=2)}")
                return {"error": f"Order failed: {error_message} (Code: {error_code})"}

            # Check for order ID - real orders always have IDs
            order_id = response.get("orderId") or response.get("id") or response.get("data", {}).get("orderId")
            if not order_id:
                logger.error(f"API returned success but NO order ID! Full response: {json.dumps(response, indent=2)}")
                return {"error": "Order rejected: No order ID returned", "api_response": response}

            logger.info(f"Order placed successfully with ID: {order_id}")
            logger.info(f"Full response: {json.dumps(response, indent=2)}")

            # Activate monitoring for market orders (not limit orders)
            if order_type.lower() == "market":
                self._monitoring_active = True
                self._last_order_time = datetime.now()
                logger.info("Monitoring activated for market order")
            
            # Update order activity timestamp for adaptive fill checking
            self._update_order_activity()

            # Verify order placement based on bracket mode
            use_native_brackets = os.getenv('USE_NATIVE_BRACKETS', 'false').lower() in ('true', '1', 'yes', 'on')
            
            if use_native_brackets:
                # For OCO brackets, we need to verify orders exist for bracket management
                try:
                    await asyncio.sleep(0.5)  # Brief delay for API consistency
                    
                    # Check open orders for bracket management
                    open_orders = await self.get_open_orders(account_id=target_account)
                    order_found = False
                    
                    if "error" not in open_orders:
                        # Check if our order exists in open orders
                        for order in open_orders:
                            if order.get("customTag") == order_data.get("customTag"):
                                logger.info(f"✅ Order verified: ID {order_id} found in open orders")
                                order_found = True
                                break
                    
                    # If not found in open orders, check if it was filled immediately
                    if not order_found:
                        logger.info(f"Order {order_id} not in open orders, checking if it was filled immediately...")
                        
                        # Check recent order history for our order
                        try:
                            recent_orders = await self.get_order_history(account_id=target_account, limit=10)
                            if "error" not in recent_orders and isinstance(recent_orders, list):
                                for order in recent_orders:
                                    if str(order.get("id")) == str(order_id):
                                        logger.info(f"✅ Order verified: ID {order_id} found in recent fills (immediate fill)")
                                        order_found = True
                                        break
                        except Exception as history_err:
                            logger.warning(f"Could not check order history for verification: {history_err}")
                        
                        # If still not found, this might be a real failure for OCO brackets
                        if not order_found:
                            logger.error(f"⚠️ ORDER VERIFICATION FAILED: Order ID {order_id} not found in open orders or recent fills!")
                            logger.error(f"Expected customTag: {order_data.get('customTag')}")
                            return {"error": "Order verification failed - order not found", "order_id": order_id}
                    else:
                        logger.warning(f"Could not verify order - failed to get open orders: {open_orders.get('error')}")
                except Exception as verify_err:
                    logger.warning(f"Could not verify order placement: {verify_err}")
                    # Don't fail the order, just log the warning
            else:
                # For Position Brackets, TopStepX manages brackets automatically
                # No need for complex verification - trust the API response
                logger.info(f"✅ Position Brackets mode: Trusting API response for order {order_id}")
                logger.info(f"TopStepX will automatically manage stop/take profit orders based on position size")

            # Send Discord notification for successful order
            try:
                account_name = self.selected_account.get('name', 'Unknown') if self.selected_account else 'Unknown'
                
                # Get actual execution price from positions after a brief delay
                execution_price = "Market"
                if response.get('executionPrice'):
                    execution_price = f"${response['executionPrice']:.2f}"
                elif limit_price:
                    execution_price = f"${limit_price:.2f}"
                else:
                    # For market orders, get current market price as entry price
                    try:
                        quote = await self.get_market_quote(symbol)
                        if "error" not in quote:
                            if side.upper() == "BUY":
                                current_price = quote.get("ask") or quote.get("last")
                            else:
                                current_price = quote.get("bid") or quote.get("last")
                            if current_price:
                                execution_price = f"${float(current_price):.2f}"
                                logger.info(f"Set execution price to current market price: {execution_price}")
                    except Exception as price_err:
                        logger.warning(f"Could not fetch current market price: {price_err}")
                
                # Determine order status
                order_status = "Placed"
                if response.get('status'):
                    order_status = response['status']
                
                # Determine if this is a bracket order
                order_type_display = order_type.capitalize()
                if stop_loss_ticks is not None or take_profit_ticks is not None:
                    order_type_display = "Bracket"
                
                notification_data = {
                    'symbol': symbol,
                    'side': side,
                    'quantity': quantity,
                    'price': execution_price,
                    'order_type': order_type_display,
                    'order_id': response.get('orderId', 'Unknown'),
                    'status': order_status,
                    'account_id': target_account
                }
                self.discord_notifier.send_order_notification(notification_data, account_name)
            except Exception as notif_err:
                logger.warning(f"Failed to send Discord notification: {notif_err}")

            # Cache any discovered order/position IDs for fast future cancellations/closures
            try:
                self._cache_ids_from_response(response, target_account, symbol)
            except Exception as cache_err:
                logger.warning(f"Failed to cache IDs from order response: {cache_err}")

            return response
            
        except Exception as e:
            logger.error(f"Failed to place order: {str(e)}")
            return {"error": str(e)}
    
    async def get_available_contracts(self, use_cache: bool = True, cache_ttl_minutes: int = 60) -> List[Dict]:
        """
        Get available trading contracts with caching support.
        
        Contracts are cached for 60 minutes by default since they rarely change.
        This significantly reduces API calls and improves performance.
        
        Args:
            use_cache: If True, use cached contract list if available and fresh
            cache_ttl_minutes: Cache TTL in minutes (default: 60 minutes)
        
        Returns:
            List[Dict]: List of available contracts
        """
        try:
            # Check cache first if enabled
            if use_cache:
                with self._contract_cache_lock:
                    if self._contract_cache is not None:
                        cache_age = datetime.now() - self._contract_cache['timestamp']
                        if cache_age < timedelta(minutes=cache_ttl_minutes):
                            logger.debug(f"Using cached contract list ({len(self._contract_cache['contracts'])} contracts, age: {cache_age.total_seconds()/60:.1f} min)")
                            return self._contract_cache['contracts'].copy()
                        else:
                            logger.debug(f"Contract cache expired (age: {cache_age.total_seconds()/60:.1f} min, max: {cache_ttl_minutes} min)")
            
            logger.info("Fetching available contracts...")
            
            # Use AuthManager for authentication
            if not await self.auth_manager.ensure_valid_token():
                logger.error("No session token available. Please authenticate first.")
                return []
            
            headers = {
                "accept": "application/json",
                "Content-Type": "application/json",
                **self.auth_manager.get_auth_headers()
            }
            
            # Use correct endpoint per API documentation:
            # https://gateway.docs.projectx.com/docs/api-reference/market-data/available-contracts/
            # POST /api/Contract/available with { "live": false }
            
            # Use broker adapter for contract fetching if available
            if hasattr(self, 'broker_adapter'):
                try:
                    contracts = await self.broker_adapter.get_available_contracts(use_cache=use_cache, cache_ttl_minutes=cache_ttl_minutes)
                    # Sync to local cache
                    if contracts:
                        with self._contract_cache_lock:
                            self._contract_cache = {
                                'contracts': contracts.copy(),
                                'timestamp': datetime.now(),
                                'ttl_minutes': cache_ttl_minutes
                            }
                        if hasattr(self, 'contract_manager'):
                            self.contract_manager.set_contract_cache(contracts, cache_ttl_minutes)
                    logger.info(f"Found {len(contracts)} available contracts via adapter")
                    return contracts
                except Exception as adapter_err:
                    logger.warning(f"Adapter contract fetch failed, falling back to direct API: {adapter_err}")
            
            # Fallback to direct API call
            response = self._make_curl_request(
                "POST",
                "/api/Contract/available",
                data={"live": False},  # Use False for simulation/paper trading contracts
                headers=headers
            )
            
            # Check if API returned an error
            if "error" in response or not response:
                logger.warning(f"Contract API returned error or empty response")
                # Return cached data if available
                if use_cache:
                    with self._contract_cache_lock:
                        if self._contract_cache is not None:
                            logger.warning(f"API error, returning stale cached contracts ({len(self._contract_cache['contracts'])} contracts)")
                            return self._contract_cache['contracts'].copy()
                
                # Fallback to hardcoded common contracts
                logger.warning("Using fallback hardcoded contract list")
                fallback_contracts = [
                    {"symbol": "MNQ", "name": "Micro E-mini Nasdaq-100", "contractId": "CON.F.US.MNQ"},
                    {"symbol": "MES", "name": "Micro E-mini S&P 500", "contractId": "CON.F.US.MES"},
                    {"symbol": "MYM", "name": "Micro E-mini Dow", "contractId": "CON.F.US.MYM"},
                    {"symbol": "M2K", "name": "Micro E-mini Russell 2000", "contractId": "CON.F.US.M2K"},
                    {"symbol": "ES", "name": "E-mini S&P 500", "contractId": "CON.F.US.ES"},
                    {"symbol": "NQ", "name": "E-mini Nasdaq-100", "contractId": "CON.F.US.NQ"},
                    {"symbol": "YM", "name": "E-mini Dow", "contractId": "CON.F.US.YM"},
                    {"symbol": "RTY", "name": "E-mini Russell 2000", "contractId": "CON.F.US.RTY"},
                    {"symbol": "CL", "name": "Crude Oil", "contractId": "CON.F.US.CL"},
                    {"symbol": "GC", "name": "Gold", "contractId": "CON.F.US.GC"},
                    {"symbol": "SI", "name": "Silver", "contractId": "CON.F.US.SI"},
                    {"symbol": "6E", "name": "Euro FX", "contractId": "CON.F.US.6E"},
                ]
                return fallback_contracts
            
            # Check API success field (per API docs, response includes success boolean)
            if isinstance(response, dict) and response.get('success') == False:
                error_code = response.get('errorCode', 'Unknown')
                error_msg = response.get('errorMessage', 'No error message')
                logger.error(f"API returned error: Code {error_code}, Message: {error_msg}")
                # Try cached data first
                if use_cache:
                    with self._contract_cache_lock:
                        if self._contract_cache is not None:
                            logger.warning(f"Using stale cached contracts due to API error")
                            return self._contract_cache['contracts'].copy()
                return []
            
            # Parse contracts from response
            # Contract/search endpoint may return different formats
            if isinstance(response, list):
                contracts = response
            elif isinstance(response, dict):
                # Try common response keys
                if "contracts" in response:
                    contracts = response["contracts"]
                elif "data" in response:
                    contracts = response["data"]
                elif "result" in response:
                    contracts = response["result"]
                elif "items" in response:
                    contracts = response["items"]
                elif response.get("success") and "data" in response:
                    contracts = response["data"]
                else:
                    # If response is a dict but doesn't have expected keys, log warning
                    logger.warning(f"Unexpected contracts response format (dict): {list(response.keys())}")
                    contracts = []
            else:
                logger.warning(f"Unexpected contracts response type: {type(response)}")
                contracts = []
            
            # Log sample contract structure for debugging
            if contracts and len(contracts) > 0:
                sample = contracts[0]
                logger.debug(f"Sample contract structure: {list(sample.keys()) if isinstance(sample, dict) else type(sample)}")
                if isinstance(sample, dict):
                    logger.debug(f"Sample contract fields: symbol={sample.get('symbol')}, contractId={sample.get('contractId')}, name={sample.get('name')}")
            
            # Cache the contracts
            if use_cache:
                with self._contract_cache_lock:
                    self._contract_cache = {
                        'contracts': contracts.copy(),
                        'timestamp': datetime.now(),
                        'ttl_minutes': cache_ttl_minutes
                    }
                    logger.info(f"✅ Cached {len(contracts)} contracts for {cache_ttl_minutes} minutes")
                    # Log a few sample symbols for verification
                    sample_symbols = []
                    for contract in contracts[:10]:
                        if isinstance(contract, dict):
                            sym = contract.get('symbol') or contract.get('Symbol') or contract.get('ticker')
                            if not sym and contract.get('contractId'):
                                cid = str(contract.get('contractId'))
                                if '.' in cid:
                                    parts = cid.split('.')
                                    if len(parts) >= 4:
                                        sym = parts[-2]
                            if sym:
                                sample_symbols.append(str(sym).upper())
                    if sample_symbols:
                        logger.debug(f"Sample symbols in cache: {sorted(set(sample_symbols))}")
            
            logger.info(f"Found {len(contracts)} available contracts")
            return contracts
            
        except Exception as e:
            logger.error(f"Failed to fetch contracts: {str(e)}")
            # Return cached data if available, even if expired, on error
            if use_cache:
                with self._contract_cache_lock:
                    if self._contract_cache is not None:
                        logger.warning(f"Exception during fetch, returning stale cached contracts ({len(self._contract_cache['contracts'])} contracts)")
                        return self._contract_cache['contracts'].copy()
            return []
    
    async def flatten_all_positions(self, interactive: bool = True) -> Dict:
        """
        Close all open positions and cancel all open orders on the selected account.
        
        Now uses TopStepXAdapter for flattening, maintaining backward compatibility.
        
        Args:
            interactive: If True, ask for confirmation. If False, proceed automatically.
        
        Returns:
            Dict: Flatten response or error
        """
        try:
            if not self.selected_account:
                print("❌ No account selected")
                return {"error": "No account selected"}
            
            target_account = self.selected_account['id']
            print(f"\n⚠️  FLATTEN ALL POSITIONS")
            print(f"   Account: {self.selected_account['name']}")
            print(f"   This will close ALL positions and cancel ALL orders!")
            
            if interactive:
                confirm = input("   Are you sure? (y/N): ").strip().lower()
                if confirm != 'y':
                    print("❌ Flatten cancelled")
                    return {"error": "Cancelled by user"}
            else:
                print("   Auto-confirming for webhook execution...")
            
            logger.info(f"Flattening all positions on account {target_account}")
            
            # Use TopStepXAdapter for flattening
            result = await self.broker_adapter.flatten_all_positions(account_id=target_account)
            
            # Send Discord notifications for closed positions
            try:
                if result.get("closed_positions"):
                    positions = await self.get_open_positions(target_account)
                    # Note: positions will be empty after flatten, so we can't get details
                    # But we already sent notifications in close_position()
            except Exception:
                pass  # Notifications are best-effort
            
            # Format result for backward compatibility
            if result.get("success"):
                closed_count = len(result.get("closed_positions", []))
                canceled_count = len(result.get("canceled_orders", []))
                
                if closed_count > 0 or canceled_count > 0:
                    print(f"✅ All positions flattened successfully!")
                    print(f"   Account: {self.selected_account['name']}")
                    print(f"   Closed positions: {closed_count}")
                    print(f"   Canceled orders: {canceled_count}")
                else:
                    print("✅ No positions or orders found to close/cancel")
            
            return result
            
        except Exception as e:
            logger.error(f"Failed to flatten positions: {str(e)}")
            print(f"❌ Flatten failed: {str(e)}")
            return {"error": str(e)}
    
    # ============================================================================
    # NATIVE TOPSTEPX API METHODS - POSITION MANAGEMENT
    # ============================================================================
    
    async def get_open_positions(self, account_id: str = None) -> List[Dict]:
        """
        Get all open positions for the selected account.
        
        Now uses TopStepXAdapter for position fetching, maintaining backward compatibility.
        
        Args:
            account_id: Account ID (uses selected account if not provided)
            
        Returns:
            List[Dict]: List of open positions
        """
        try:
            target_account = account_id or (self.selected_account['id'] if self.selected_account else None)
            
            if not target_account:
                logger.error("No account selected")
                return []
            
            # Use TopStepXAdapter for position fetching
            positions = await self.broker_adapter.get_open_positions(account_id=target_account)
            
            # Convert Position objects to dicts for backward compatibility
            result = []
            for pos in positions:
                if hasattr(pos, 'raw_data') and pos.raw_data:
                    result.append(pos.raw_data)
                else:
                    # Convert Position dataclass to dict format
                    try:
                        contract_id = None
                        if pos.symbol:
                            try:
                                contract_id = self.contract_manager.get_contract_id(pos.symbol)
                            except (ValueError, AttributeError):
                                pass
                        
                        result.append({
                            'id': pos.position_id,
                            'position_id': pos.position_id,
                            'symbol': pos.symbol,
                            'contractId': contract_id,
                            'contract_id': contract_id,
                            'side': 0 if pos.side == "LONG" else 1,
                            'size': pos.quantity,
                            'quantity': pos.quantity,
                            'entryPrice': pos.entry_price,
                            'entry_price': pos.entry_price,
                            'currentPrice': pos.current_price,
                            'current_price': pos.current_price,
                            'unrealizedPnl': pos.unrealized_pnl,
                            'unrealized_pnl': pos.unrealized_pnl,
                            'accountId': pos.account_id,
                            'account_id': pos.account_id
                        })
                    except Exception as e:
                        logger.warning(f"Failed to convert position to dict: {e}")
                        continue
            
            logger.info(f"Found {len(result)} open positions for account {target_account}")
            return result
            
        except Exception as e:
            logger.error(f"Failed to fetch positions: {str(e)}")
            return []

    # ============================================================================
    # ID CACHE HELPERS
    # ============================================================================

    def _cache_ids_from_response(self, response: Dict, account_id: str, symbol: str) -> None:
        """Extract and cache order and position IDs from arbitrary API responses."""
        def collect_ids(obj, orders, positions):
            if isinstance(obj, dict):
                # Common keys for IDs
                for key, value in obj.items():
                    lk = key.lower()
                    if lk in ("id", "orderid", "order_id") and isinstance(value, (str, int)):
                        orders.add(str(value))
                    if lk in ("positionid", "position_id") and isinstance(value, (str, int)):
                        positions.add(str(value))
                    # Recurse into nested structures
                    collect_ids(value, orders, positions)
            elif isinstance(obj, list):
                for item in obj:
                    collect_ids(item, orders, positions)

        order_ids, position_ids = set(), set()
        collect_ids(response, order_ids, position_ids)

        if order_ids:
            acct_map = self._cached_order_ids.setdefault(str(account_id), {})
            sym_set = acct_map.setdefault(symbol.upper(), set())
            sym_set.update(str(oid) for oid in order_ids)
            logger.info(f"Cached {len(order_ids)} order IDs for {symbol} on account {account_id}")
        if position_ids:
            acct_map = self._cached_position_ids.setdefault(str(account_id), {})
            sym_set = acct_map.setdefault(symbol.upper(), set())
            sym_set.update(str(pid) for pid in position_ids)
            logger.info(f"Cached {len(position_ids)} position IDs for {symbol} on account {account_id}")

    async def cancel_cached_orders(self, account_id: str = None, symbol: str = None) -> Dict:
        """Cancel cached orders quickly without searching; returns details of attempts."""
        target_account = account_id or (self.selected_account['id'] if self.selected_account else None)
        if not target_account:
            return {"error": "No account selected"}
        acct_map = self._cached_order_ids.get(str(target_account), {})
        symbols = [symbol.upper()] if symbol else list(acct_map.keys())
        canceled, failed = [], []
        for sym in symbols:
            ids = list(acct_map.get(sym, set()))
            for oid in ids:
                result = await self.cancel_order(oid, account_id=target_account)
                if "error" in result:
                    failed.append(oid)
                else:
                    canceled.append(oid)
                    acct_map[sym].discard(oid)
        return {"canceled": canceled, "failed": failed}

    async def close_cached_positions(self, account_id: str = None, symbol: str = None) -> Dict:
        """Close cached positions quickly without searching; returns details of attempts."""
        target_account = account_id or (self.selected_account['id'] if self.selected_account else None)
        if not target_account:
            return {"error": "No account selected"}
        acct_map = self._cached_position_ids.get(str(target_account), {})
        symbols = [symbol.upper()] if symbol else list(acct_map.keys())
        closed, failed = [], []
        for sym in symbols:
            ids = list(acct_map.get(sym, set()))
            for pid in ids:
                result = await self.close_position(pid, account_id=target_account)
                if "error" in result:
                    failed.append(pid)
                else:
                    closed.append(pid)
                    acct_map[sym].discard(pid)
        return {"closed": closed, "failed": failed}
    
    async def get_position_details(self, position_id: str, account_id: str = None) -> Dict:
        """
        Get detailed information about a specific position.
        
        Now uses TopStepXAdapter for position details, maintaining backward compatibility.
        
        Args:
            position_id: Position ID
            account_id: Account ID (uses selected account if not provided)
            
        Returns:
            Dict: Position details or error
        """
        try:
            target_account = account_id or (self.selected_account['id'] if self.selected_account else None)
            
            if not target_account:
                return {"error": "No account selected"}
            
            # Use TopStepXAdapter for position details
            position = await self.broker_adapter.get_position_details(
                position_id=position_id,
                account_id=target_account
            )
            
            if position is None:
                return {"error": f"Position {position_id} not found"}
            
            # Convert Position object to dict for backward compatibility
            if hasattr(position, 'raw_data'):
                return position.raw_data
            else:
                return {
                    'id': position.position_id,
                    'symbol': position.symbol,
                    'side': 0 if position.side == "LONG" else 1,
                    'size': position.quantity,
                    'quantity': position.quantity,
                    'entryPrice': position.entry_price,
                    'entry_price': position.entry_price,
                    'currentPrice': position.current_price,
                    'current_price': position.current_price,
                    'unrealizedPnl': position.unrealized_pnl,
                    'unrealized_pnl': position.unrealized_pnl,
                    'accountId': position.account_id,
                    'account_id': position.account_id
                }
            
        except Exception as e:
            logger.error(f"Failed to fetch position details: {str(e)}")
            return {"error": str(e)}
    
    async def close_position(self, position_id: str, quantity: int = None, account_id: str = None) -> Dict:
        """
        Close a specific position or part of it.
        
        Now uses TopStepXAdapter for position closing, maintaining backward compatibility.
        
        Args:
            position_id: Position ID to close
            quantity: Quantity to close (None for entire position)
            account_id: Account ID (uses selected account if not provided)
            
        Returns:
            Dict: Close response or error
        """
        try:
            target_account = account_id or (self.selected_account['id'] if self.selected_account else None)
            
            if not target_account:
                return {"error": "No account selected"}
            
            logger.info(f"Closing position {position_id} on account {target_account}")
            if quantity:
                logger.info(f"Closing {quantity} contracts (partial close)")
            
            # Get position details before closing for notification
            position_details = await self.get_position_details(position_id, target_account)
            
            # Use TopStepXAdapter for position closing
            result = await self.broker_adapter.close_position(
                position_id=position_id,
                quantity=quantity,
                account_id=target_account
            )
            
            # Convert CloseResponse to dict for backward compatibility
            if hasattr(result, 'success'):
                if result.success:
                    # Send Discord notification
                    try:
                        if position_details and "error" not in position_details:
                            account_name = self.selected_account.get('name', 'Unknown') if self.selected_account else 'Unknown'
                            
                            # Get current market price for exit price
                            symbol = position_details.get('symbol') or self._get_symbol_from_contract_id(position_details.get('contractId', ''))
                            exit_price = "Unknown"
                            try:
                                quote = await self.get_market_quote(symbol)
                                if "error" not in quote and isinstance(quote, dict):
                                    side_value = position_details.get('side', 0)
                                    if side_value == 0:  # Long position
                                        exit_price = quote.get("bid") or quote.get("last")
                                    else:  # Short position
                                        exit_price = quote.get("ask") or quote.get("last")
                                    if exit_price:
                                        exit_price = f"${float(exit_price):.2f}"
                            except Exception as price_err:
                                logger.warning(f"Could not fetch exit price: {price_err}")
                            
                            notification_data = {
                                'symbol': symbol,
                                'side': 'LONG' if position_details.get('side', 0) == 0 else 'SHORT',
                                'quantity': position_details.get('size', 0) or position_details.get('quantity', 0),
                                'entry_price': f"${position_details.get('entryPrice', 0) or position_details.get('entry_price', 0):.2f}",
                                'exit_price': exit_price,
                                'pnl': position_details.get('unrealizedPnl', 0) or position_details.get('unrealized_pnl', 0),
                                'position_id': position_id
                            }
                            self.discord_notifier.send_position_close_notification(notification_data, account_name)
                    except Exception as notif_err:
                        logger.warning(f"Failed to send Discord notification: {notif_err}")
                    
                    return {
                        "success": True,
                        "position_id": result.position_id,
                        "message": result.message or "Position closed successfully"
                    }
                else:
                    return {
                        "error": result.error or "Failed to close position"
                    }
            
            # If already a dict, return as-is
            return result if isinstance(result, dict) else {"error": "Unexpected response type"}
            
        except Exception as e:
            logger.error(f"Failed to close position: {str(e)}")
            return {"error": str(e)}
    
    # ============================================================================
    # NATIVE TOPSTEPX API METHODS - ORDER MANAGEMENT
    # ============================================================================
    
    async def get_open_orders(self, account_id: str = None) -> List[Dict]:
        """
        Get all open orders for the selected account.
        
        Now uses TopStepXAdapter for order fetching, maintaining backward compatibility.
        
        Args:
            account_id: Account ID (uses selected account if not provided)
            
        Returns:
            List[Dict]: List of open orders
        """
        try:
            target_account = account_id or (self.selected_account['id'] if self.selected_account else None)
            
            if not target_account:
                logger.error("No account selected")
                return []
            
            # Use TopStepXAdapter for order fetching
            orders = await self.broker_adapter.get_open_orders(account_id=target_account)
            
            logger.info(f"Found {len(orders)} open orders for account {target_account}")
            return orders
            
        except Exception as e:
            logger.error(f"Failed to fetch orders: {str(e)}")
            return []
    
    async def cancel_order(self, order_id: str, account_id: str = None) -> Dict:
        """
        Cancel a specific order.
        
        Now uses TopStepXAdapter for order cancellation, maintaining backward compatibility.
        
        Args:
            order_id: Order ID to cancel
            account_id: Account ID (uses selected account if not provided)
            
        Returns:
            Dict: Cancel response or error
        """
        try:
            target_account = account_id or (self.selected_account['id'] if self.selected_account else None)
            
            if not target_account:
                return {"error": "No account selected"}
            
            # Use TopStepXAdapter for order cancellation
            result = await self.broker_adapter.cancel_order(
                order_id=order_id,
                account_id=target_account
            )
            
            # Convert CancelResponse to dict for backward compatibility
            if hasattr(result, 'success'):
                if result.success:
                    return {
                        "success": True,
                        "orderId": result.order_id,
                        "message": result.message or "Order canceled successfully"
                    }
                else:
                    return {
                        "error": result.error or "Failed to cancel order"
                    }
            
            # If already a dict, return as-is
            return result if isinstance(result, dict) else {"error": "Unexpected response type"}
            
        except Exception as e:
            logger.error(f"Failed to cancel order: {str(e)}")
            return {"error": str(e)}
    
    async def modify_order(self, order_id: str, new_quantity: int = None, new_price: float = None, 
                          account_id: str = None, order_type: int = None) -> Dict:
        """
        Modify an existing order.
        
        Now uses TopStepXAdapter for order modification, maintaining backward compatibility.
        
        Args:
            order_id: Order ID to modify
            new_quantity: New quantity (None to keep current)
            new_price: New price (None to keep current)
            account_id: Account ID (uses selected account if not provided)
            order_type: Order type (1=Limit, 4=Stop, etc.) to determine price field
            
        Returns:
            Dict: Modify response or error
        """
        try:
            target_account = account_id or (self.selected_account['id'] if self.selected_account else None)
            
            if not target_account:
                return {"error": "No account selected"}
            
            # Use TopStepXAdapter for order modification
            result = await self.broker_adapter.modify_order(
                order_id=order_id,
                quantity=new_quantity,
                price=new_price,
                account_id=target_account,
                order_type=order_type
            )
            
            # Convert ModifyOrderResponse to dict for backward compatibility
            if hasattr(result, 'success'):
                if result.success:
                    return {
                        "success": True,
                        "orderId": result.order_id,
                        "message": result.message or "Order modified successfully"
                    }
                else:
                    return {
                        "error": result.error or "Failed to modify order"
                    }
            
            # If already a dict, return as-is
            return result if isinstance(result, dict) else {"error": "Unexpected response type"}
            
        except Exception as e:
            logger.error(f"Failed to modify order: {str(e)}")
            return {"error": str(e)}
    
    async def modify_stop_loss(self, position_id: str, new_stop_price: float, account_id: str = None) -> Dict:
        """
        Modify the stop loss order attached to a position.
        
        Now uses PositionManager for position modifications, maintaining backward compatibility.
        
        Args:
            position_id: Position ID
            new_stop_price: New stop loss price
            account_id: Account ID (uses selected account if not provided)
            
        Returns:
            Dict: Modify response or error
        """
        try:
            target_account = account_id or (self.selected_account['id'] if self.selected_account else None)
            
            if not target_account:
                return {"error": "No account selected"}
            
            # Delegate to PositionManager
            return await self.position_manager.modify_stop_loss(
                position_id=position_id,
                new_stop_price=new_stop_price,
                account_id=str(target_account)
            )
            
        except Exception as e:
            logger.error(f"Failed to modify stop loss: {str(e)}")
            return {"error": str(e)}
    
    async def modify_take_profit(self, position_id: str, new_tp_price: float, account_id: str = None) -> Dict:
        """
        Modify the take profit order attached to a position.
        
        Now uses PositionManager for position modifications, maintaining backward compatibility.
        
        Args:
            position_id: Position ID
            new_tp_price: New take profit price
            account_id: Account ID (uses selected account if not provided)
            
        Returns:
            Dict: Modify response or error
        """
        try:
            target_account = account_id or (self.selected_account['id'] if self.selected_account else None)
            
            if not target_account:
                return {"error": "No account selected"}
            
            # Delegate to PositionManager
            return await self.position_manager.modify_take_profit(
                position_id=position_id,
                new_tp_price=new_tp_price,
                account_id=str(target_account)
            )
            
        except Exception as e:
            logger.error(f"Failed to modify take profit: {str(e)}")
            return {"error": str(e)}
    
    def _get_trading_session_dates(self, date: datetime = None) -> tuple:
        """
        Get the start and end dates for the trading session containing the given date.
        Sessions run from 6pm EST to 4pm EST next day, Sunday through Friday.
        
        Now uses RiskManager for session date calculations, maintaining backward compatibility.
        
        Args:
            date: Date to find session for (defaults to now)
            
        Returns:
            tuple: (session_start, session_end) as datetime objects in UTC
        """
        result = self.risk_manager.get_trading_session_dates(date)
        # RiskManager returns a dict, convert to tuple for backward compatibility
        if isinstance(result, dict):
            return (result.get('session_start'), result.get('session_end'))
        return result

    def _get_point_value(self, symbol: str) -> float:
        """
        Get point value for a symbol ($ per point movement).
        
        Now uses RiskManager for point value calculations, maintaining backward compatibility.
        """
        return self.risk_manager.get_point_value(symbol)

    def _consolidate_orders_into_trades(self, orders: List[Dict]) -> List[Dict]:
        """
        Consolidate individual filled orders into completed trades using FIFO methodology.
        Matches entry and exit orders to calculate P&L for each trade.
        
        Args:
            orders: List of filled order dictionaries
            
        Returns:
            List[Dict]: List of consolidated trades with entry/exit info and P&L
        """
        if not orders:
            return []
        
        # Group orders by symbol
        by_symbol = {}
        for order in orders:
            contract_id = order.get('contractId', '')
            if contract_id:
                symbol = contract_id.split('.')[-2] if '.' in contract_id else contract_id
            else:
                symbol = order.get('symbol', 'UNKNOWN')
            
            if symbol not in by_symbol:
                by_symbol[symbol] = []
            by_symbol[symbol].append(order)
        
        # Sort orders by execution timestamp for each symbol
        for symbol in by_symbol:
            by_symbol[symbol].sort(key=lambda x: x.get('executionTimestamp') or x.get('creationTimestamp') or '')
        
        # Process each symbol's orders using FIFO to match entries and exits
        consolidated_trades = []
        
        for symbol, symbol_orders in by_symbol.items():
            # Track open positions using a queue (FIFO)
            open_positions = []  # List of (entry_order, remaining_quantity)
            
            for order in symbol_orders:
                side = order.get('side', -1)  # 0=BUY, 1=SELL
                quantity = order.get('size', 0)
                # Try multiple field names for price (API returns different fields)
                # Note: 'filledPrice' is the correct field from TopStepX API for filled orders
                price = (order.get('filledPrice') or  # ← Primary field for filled orders
                        order.get('executionPrice') or 
                        order.get('averagePrice') or 
                        order.get('price') or 
                        order.get('limitPrice') or 
                        order.get('stopPrice') or 0.0)
                timestamp_raw = order.get('executionTimestamp') or order.get('creationTimestamp') or ''
                
                # Convert timestamp to datetime if it's a string, otherwise keep as-is
                from datetime import datetime, timezone
                if isinstance(timestamp_raw, str) and timestamp_raw:
                    try:
                        # Try parsing ISO format timestamp
                        timestamp = datetime.fromisoformat(timestamp_raw.replace('Z', '+00:00'))
                    except (ValueError, AttributeError):
                        # If parsing fails, keep as string but log warning
                        logger.warning(f"Could not parse timestamp '{timestamp_raw}', keeping as string")
                        timestamp = timestamp_raw
                elif isinstance(timestamp_raw, datetime):
                    timestamp = timestamp_raw
                else:
                    # Fallback to current time if no timestamp
                    timestamp = datetime.now(timezone.utc)
                
                # Debug logging for order details (only if verbose logging enabled)
                if logger.level <= logging.DEBUG:
                    logger.debug(f"Processing order: side={side}, qty={quantity}, price={price}, timestamp={timestamp}")
                
                # Log warning if price is still 0 after all attempts
                if price == 0.0 or price is None:
                    logger.warning(f"⚠️  Order {order.get('id')} has no price in any expected field. Available fields: {list(order.keys())}")
                
                if side == 0:  # BUY
                    # First, try to close short positions
                    remaining_qty = quantity
                    
                    while remaining_qty > 0 and open_positions and open_positions[0]['side'] == 'SHORT':
                        position = open_positions[0]
                        closed_qty = min(remaining_qty, position['remaining_qty'])
                        
                        # Calculate P&L for closing short: profit when buy price < sell price
                        entry_price = position['entry_price']
                        exit_price = price
                        point_value = self._get_point_value(symbol)
                        pnl = (entry_price - exit_price) * closed_qty * point_value  # Reversed for short
                        
                        # Extract strategy from entry order's custom tag
                        entry_order = position['entry_order']
                        strategy_name = None
                        custom_tag = entry_order.get('customTag') or entry_order.get('custom_tag')
                        if custom_tag and '-strategy-' in str(custom_tag):
                            try:
                                # Format: TradingBot-v1.0-strategy-{strategy_name}-{order_type}-...
                                parts = str(custom_tag).split('-strategy-')
                                if len(parts) >= 2:
                                    strategy_name = parts[1].split('-')[0]
                            except Exception:
                                pass
                        
                        # Create consolidated trade
                        trade = {
                            'symbol': symbol,
                            'side': 'SHORT',
                            'quantity': closed_qty,
                            'entry_price': entry_price,
                            'exit_price': exit_price,
                            'entry_time': position['entry_time'],
                            'exit_time': timestamp,
                            'pnl': pnl,
                            'entry_order_id': position['entry_order'].get('id'),
                            'exit_order_id': order.get('id'),
                            'strategy': strategy_name  # Add strategy name from custom tag
                        }
                        consolidated_trades.append(trade)
                        logger.debug(f"Created SHORT trade: {closed_qty} @ ${entry_price:.2f} → ${exit_price:.2f}, P&L: ${pnl:.2f}")
                        
                        # Update remaining quantities
                        position['remaining_qty'] -= closed_qty
                        remaining_qty -= closed_qty
                        
                        # Remove position if fully closed
                        if position['remaining_qty'] <= 0:
                            open_positions.pop(0)
                    
                    # If there's still quantity remaining after closing shorts, it's a new long position
                    if remaining_qty > 0:
                        open_positions.append({
                            'entry_order': order,
                            'entry_price': price,
                            'entry_time': timestamp,
                            'remaining_qty': remaining_qty,
                            'side': 'LONG'
                        })
                        logger.debug(f"Opened LONG position: {remaining_qty} @ ${price:.2f}")
                        
                elif side == 1:  # SELL
                    # First, try to close long positions
                    remaining_qty = quantity
                    
                    while remaining_qty > 0 and open_positions and open_positions[0]['side'] == 'LONG':
                        position = open_positions[0]
                        closed_qty = min(remaining_qty, position['remaining_qty'])
                        
                        # Calculate P&L for closing long: profit when sell price > buy price
                        entry_price = position['entry_price']
                        exit_price = price
                        point_value = self._get_point_value(symbol)
                        pnl = (exit_price - entry_price) * closed_qty * point_value
                        
                        # Extract strategy from entry order's custom tag
                        entry_order = position['entry_order']
                        strategy_name = None
                        custom_tag = entry_order.get('customTag') or entry_order.get('custom_tag')
                        if custom_tag and '-strategy-' in str(custom_tag):
                            try:
                                # Format: TradingBot-v1.0-strategy-{strategy_name}-{order_type}-...
                                parts = str(custom_tag).split('-strategy-')
                                if len(parts) >= 2:
                                    strategy_name = parts[1].split('-')[0]
                            except Exception:
                                pass
                        
                        # Create consolidated trade
                        trade = {
                            'symbol': symbol,
                            'side': 'LONG',
                            'quantity': closed_qty,
                            'entry_price': entry_price,
                            'exit_price': exit_price,
                            'entry_time': position['entry_time'],
                            'exit_time': timestamp,
                            'pnl': pnl,
                            'entry_order_id': position['entry_order'].get('id'),
                            'exit_order_id': order.get('id'),
                            'strategy': strategy_name  # Add strategy name from custom tag
                        }
                        consolidated_trades.append(trade)
                        logger.debug(f"Created LONG trade: {closed_qty} @ ${entry_price:.2f} → ${exit_price:.2f}, P&L: ${pnl:.2f}")
                        
                        # Update remaining quantities
                        position['remaining_qty'] -= closed_qty
                        remaining_qty -= closed_qty
                        
                        # Remove position if fully closed
                        if position['remaining_qty'] <= 0:
                            open_positions.pop(0)
                    
                    # If there's still quantity remaining after closing longs, it's a new short position
                    if remaining_qty > 0:
                        open_positions.append({
                            'entry_order': order,
                            'entry_price': price,
                            'entry_time': timestamp,
                            'remaining_qty': remaining_qty,
                            'side': 'SHORT'
                        })
                        logger.debug(f"Opened SHORT position: {remaining_qty} @ ${price:.2f}")
        
        # Sort consolidated trades by exit time
        consolidated_trades.sort(key=lambda x: x.get('exit_time', ''))
        
        # Log summary of consolidation
        logger.info(f"Consolidated {len(orders)} orders into {len(consolidated_trades)} completed trades")
        if consolidated_trades:
            logger.debug(f"Trades summary: {[(t['symbol'], t['side'], t['quantity'], t['pnl']) for t in consolidated_trades]}")
        
        return consolidated_trades
    
    def _calculate_trade_statistics(self, trades: List[Dict]) -> Dict:
        """
        Calculate statistics from a list of trades.
        Groundwork for statistical analysis.
        
        Args:
            trades: List of trade dictionaries
            
        Returns:
            Dict: Statistics including win rate, total P&L, average win/loss, etc.
        """
        if not trades:
            return {
                "total_trades": 0,
                "winning_trades": 0,
                "losing_trades": 0,
                "win_rate": 0.0,
                "total_pnl": 0.0,
                "average_pnl": 0.0,
                "average_win": 0.0,
                "average_loss": 0.0,
                "largest_win": 0.0,
                "largest_loss": 0.0
            }
        
        winning_trades = []
        losing_trades = []
        
        for trade in trades:
            pnl = float(trade.get('pnl', 0) or trade.get('unrealizedPnl', 0) or trade.get('realizedPnl', 0))
            if pnl > 0:
                winning_trades.append(pnl)
            elif pnl < 0:
                losing_trades.append(pnl)
        
        total_pnl = sum(float(t.get('pnl', 0) or t.get('unrealizedPnl', 0) or t.get('realizedPnl', 0)) for t in trades)
        win_rate = (len(winning_trades) / len(trades) * 100) if trades else 0.0
        
        return {
            "total_trades": len(trades),
            "winning_trades": len(winning_trades),
            "losing_trades": len(losing_trades),
            "break_even_trades": len(trades) - len(winning_trades) - len(losing_trades),
            "win_rate": round(win_rate, 2),
            "total_pnl": round(total_pnl, 2),
            "average_pnl": round(total_pnl / len(trades), 2) if trades else 0.0,
            "average_win": round(sum(winning_trades) / len(winning_trades), 2) if winning_trades else 0.0,
            "average_loss": round(sum(losing_trades) / len(losing_trades), 2) if losing_trades else 0.0,
            "largest_win": round(max(winning_trades), 2) if winning_trades else 0.0,
            "largest_loss": round(min(losing_trades), 2) if losing_trades else 0.0
        }
    
    async def get_order_history(self, account_id: str = None, limit: int = 100, 
                               start_timestamp: str = None, end_timestamp: str = None) -> List[Dict]:
        """
        Get order history for the selected account.
        
        Now uses TopStepXAdapter for order history, maintaining backward compatibility.
        
        Args:
            account_id: Account ID (uses selected account if not provided)
            limit: Maximum number of orders to return
            start_timestamp: Start timestamp in ISO format (optional)
            end_timestamp: End timestamp in ISO format (optional)
            
        Returns:
            List[Dict]: List of historical orders
        """
        try:
            target_account = account_id or (self.selected_account['id'] if self.selected_account else None)
            
            if not target_account:
                logger.error("No account selected")
                return []
            
            # Use TopStepXAdapter for order history
            orders = await self.broker_adapter.get_order_history(
                account_id=target_account,
                limit=limit,
                start_timestamp=start_timestamp,
                end_timestamp=end_timestamp
            )
            
            logger.info(f"Found {len(orders)} historical orders for account {target_account}")
            return orders
            
        except Exception as e:
            logger.error(f"Failed to fetch order history: {str(e)}")
            return []
    
    # ============================================================================
    # NATIVE TOPSTEPX API METHODS - BRACKET ORDER SYSTEM
    # ============================================================================
    
    async def create_bracket_order_improved(self, symbol: str, side: str, quantity: int,
                                           entry_stop_price: float, stop_loss_price: float,
                                           take_profit_price: float, account_id: str = None,
                                           strategy_name: Optional[str] = None) -> Dict:
        """
        Create an improved bracket order using stop order for entry, then modifying stop/take profit.
        This approach places a stop order for entry, then after fill, creates/modifies stop loss and take profit.
        
        Args:
            symbol: Trading symbol
            side: "BUY" or "SELL"
            quantity: Number of contracts
            entry_stop_price: Stop price for entry (triggers when price reaches this level)
            stop_loss_price: Stop loss price (after entry)
            take_profit_price: Take profit price (after entry)
            account_id: Account ID (uses selected account if not provided)
            strategy_name: Optional strategy name for custom tagging
            
        Returns:
            Dict: Bracket order response or error
        """
        try:
            target_account = account_id or (self.selected_account['id'] if self.selected_account else None)
            
            if not target_account:
                return {"error": "No account selected"}
            
            if not self.session_token:
                return {"error": "No session token available. Please authenticate first."}
            
            if side.upper() not in ["BUY", "SELL"]:
                return {"error": "Side must be 'BUY' or 'SELL'"}
            
            logger.info(f"Creating improved bracket order: {side} {quantity} {symbol}")
            logger.info(f"Entry Stop: ${entry_stop_price}, Stop Loss: ${stop_loss_price}, Take Profit: ${take_profit_price}")
            
            # Step 1: Place stop order for entry
            entry_result = await self.place_stop_order(
                symbol=symbol,
                side=side,
                quantity=quantity,
                stop_price=entry_stop_price,
                account_id=target_account,
                strategy_name=strategy_name
            )
            
            if "error" in entry_result:
                return {"error": f"Entry stop order failed: {entry_result['error']}"}
            
            entry_order_id = entry_result.get('orderId') or entry_result.get('id') or entry_result.get('order_id')
            logger.info(f"Entry stop order placed: {entry_order_id}")
            
            # Step 2: Monitor for fill, then attach stop loss and take profit
            # We'll use a background task to monitor the order fill
            import asyncio
            
            async def _attach_brackets_after_fill():
                """Monitor entry order and attach brackets after fill"""
                max_wait = 300  # 5 minutes max wait
                check_interval = 2  # Check every 2 seconds
                elapsed = 0
                
                while elapsed < max_wait:
                    await asyncio.sleep(check_interval)
                    elapsed += check_interval
                    
                    # Check if entry order is filled
                    orders = await self.get_open_orders(target_account)
                    entry_filled = True
                    for order in orders:
                        if str(order.get('id')) == str(entry_order_id):
                            entry_filled = False
                            break
                    
                    if entry_filled:
                        logger.info("Entry stop order filled, attaching stop loss and take profit")
                        
                        # Wait a moment for position to be established
                        await asyncio.sleep(1)
                        
                        # Get the position
                        positions = await self.get_open_positions(target_account)
                        contract_id = self._get_contract_id(symbol)
                        position_id = None
                        
                        for pos in positions:
                            if pos.get('contractId') == contract_id:
                                position_id = pos.get('id')
                                break
                        
                        if position_id:
                            # Modify stop loss and take profit using existing methods
                            try:
                                # Use modify_stop_loss and modify_take_profit if they exist
                                # Otherwise, create new orders
                                stop_result = await self.modify_stop_loss(position_id, stop_loss_price)
                                tp_result = await self.modify_take_profit(position_id, take_profit_price)
                                
                                logger.info(f"Brackets attached: Stop Loss={stop_result}, Take Profit={tp_result}")
                                return {"success": True, "position_id": position_id}
                            except Exception as e:
                                logger.error(f"Failed to attach brackets: {e}")
                                # Fallback: create new stop loss and take profit orders
                                try:
                                    # Create stop loss order
                                    stop_side = "SELL" if side.upper() == "BUY" else "BUY"
                                    stop_result = await self.place_stop_order(
                                        symbol=symbol,
                                        side=stop_side,
                                        quantity=quantity,
                                        stop_price=stop_loss_price,
                                        account_id=target_account,
                                        strategy_name=strategy_name
                                    )
                                    
                                    # Create take profit limit order
                                    tp_side = "SELL" if side.upper() == "BUY" else "BUY"
                                    tp_result = await self.place_market_order(
                                        symbol=symbol,
                                        side=tp_side,
                                        quantity=quantity,
                                        order_type="limit",
                                        limit_price=take_profit_price,
                                        account_id=target_account
                                    )
                                    
                                    logger.info(f"Brackets created via orders: Stop={stop_result}, TP={tp_result}")
                                    return {"success": True, "position_id": position_id}
                                except Exception as e2:
                                    logger.error(f"Fallback bracket creation failed: {e2}")
                                    return {"error": f"Failed to attach brackets: {e2}"}
                        else:
                            logger.warning("Position not found after entry fill")
                            return {"error": "Position not found after entry fill"}
                
                return {"error": "Entry order did not fill within timeout"}
            
            # Start monitoring task (fire and forget)
            asyncio.create_task(_attach_brackets_after_fill())
            
            return {
                "success": True,
                "entry_order_id": entry_order_id,
                "message": "Entry stop order placed. Brackets will be attached after fill.",
                "entry_stop_price": entry_stop_price,
                "stop_loss_price": stop_loss_price,
                "take_profit_price": take_profit_price
            }
            
        except Exception as e:
            logger.error(f"Failed to create improved bracket order: {str(e)}")
            return {"error": str(e)}
    
    async def create_bracket_order(self, symbol: str, side: str, quantity: int, 
                                 stop_loss_price: float = None, take_profit_price: float = None,
                                 stop_loss_ticks: int = None, take_profit_ticks: int = None,
                                 account_id: str = None, strategy_name: str = None) -> Dict:
        """
        Create a native TopStepX bracket order with linked stop loss and take profit.
        
        Now uses TopStepXAdapter for bracket order creation, maintaining backward compatibility.
        
        Args:
            symbol: Trading symbol (e.g., "ES", "NQ", "MNQ", "YM")
            side: "BUY" or "SELL"
            quantity: Number of contracts
            stop_loss_price: Stop loss price (optional if stop_loss_ticks provided)
            take_profit_price: Take profit price (optional if take_profit_price provided)
            stop_loss_ticks: Stop loss in ticks (optional if stop_loss_price provided)
            take_profit_ticks: Take profit in ticks (optional if take_profit_price provided)
            account_id: Account ID (uses selected account if not provided)
            strategy_name: Optional strategy name for tracking
            
        Returns:
            Dict: Bracket order response or error
        """
        try:
            target_account = account_id or (self.selected_account['id'] if self.selected_account else None)
            
            if not target_account:
                return {"error": "No account selected"}
            
            if side.upper() not in ["BUY", "SELL"]:
                return {"error": "Side must be 'BUY' or 'SELL'"}
            
            # Use TopStepXAdapter for bracket order creation
            result = await self.broker_adapter.create_bracket_order(
                symbol=symbol,
                side=side,
                quantity=quantity,
                stop_loss_price=stop_loss_price,
                take_profit_price=take_profit_price,
                stop_loss_ticks=stop_loss_ticks,
                take_profit_ticks=take_profit_ticks,
                account_id=target_account,
                strategy_name=strategy_name
            )
            
            # Convert OrderResponse to dict for backward compatibility
            if result.success:
                return {
                    "success": True,
                    "orderId": result.order_id,
                    "message": result.message,
                    **({"raw_response": result.raw_response} if result.raw_response else {})
                }
            else:
                return {"error": result.error}
            
        except Exception as e:
            logger.error(f"Failed to create bracket order: {str(e)}")
            return {"error": str(e)}
    
    async def create_partial_tp_bracket_order(self, symbol: str, side: str, quantity: int,
                                             stop_loss_price: float = None, take_profit_1_price: float = None,
                                             take_profit_2_price: float = None, tp1_quantity: int = None,
                                             account_id: str = None) -> Dict:
        """
        Create a bracket order with partial TP1 and full TP2 exits.
        This places the entry order, then creates separate TP1 and TP2 orders.
        
        Args:
            symbol: Trading symbol
            side: "BUY" or "SELL"
            quantity: Total number of contracts
            stop_loss_price: Stop loss price
            take_profit_1_price: TP1 price (partial exit)
            take_profit_2_price: TP2 price (full exit)
            tp1_quantity: Number of contracts for TP1 (default: 1)
            account_id: Account ID (uses selected account if not provided)
            
        Returns:
            Dict: Bracket order response or error
        """
        try:
            target_account = account_id or (self.selected_account['id'] if self.selected_account else None)
            
            if not target_account:
                return {"error": "No account selected"}
            
            if not self.session_token:
                return {"error": "No session token available. Please authenticate first."}
            
            if side.upper() not in ["BUY", "SELL"]:
                return {"error": "Side must be 'BUY' or 'SELL'"}
            
            if tp1_quantity is None:
                tp1_quantity = 1  # Default to 1 contract for TP1
            
            if tp1_quantity >= quantity:
                return {"error": "TP1 quantity must be less than total quantity"}
            
            logger.info(f"Creating partial TP bracket order for {side} {quantity} {symbol} on account {target_account}")
            logger.info(f"TP1: {tp1_quantity} contracts at {take_profit_1_price}, TP2: {quantity} contracts at {take_profit_2_price}")
            
            # First, place the entry order
            entry_result = await self.place_market_order(
                symbol=symbol,
                side=side,
                quantity=quantity,
                account_id=target_account
            )
            
            if "error" in entry_result:
                return {"error": f"Entry order failed: {entry_result['error']}"}
            
            logger.info(f"Entry order placed successfully: {entry_result}")
            
            # Wait a moment for the position to be established
            import time
            time.sleep(1)
            
            # Get the position ID for the new position
            positions = await self.get_open_positions(target_account)
            position_id = None
            try:
                symbol_contract_id = self._get_contract_id(symbol)
                for pos in positions:
                    if pos.get('contractId') == symbol_contract_id:
                        position_id = pos.get('id')
                        break
            except ValueError as e:
                logger.error(f"❌ Cannot find position: {e}. Please fetch contracts first.")
                return {"error": str(e)}
            
            if not position_id:
                return {"error": "Could not find position after entry order"}
            
            # Create stop loss order
            stop_result = None
            if stop_loss_price:
                # Round stop loss price to valid tick size
                tick_size = await self._get_tick_size(symbol)
                rounded_stop_price = self._round_to_tick_size(stop_loss_price, tick_size)
                logger.info(f"Stop loss price: {stop_loss_price} -> {rounded_stop_price} (tick_size: {tick_size})")
                
                stop_side = "SELL" if side.upper() == "BUY" else "BUY"
                stop_result = await self.place_stop_order(
                    symbol=symbol,
                    side=stop_side,
                    quantity=quantity,
                    stop_price=rounded_stop_price,
                    account_id=target_account,
                    strategy_name=None  # This function doesn't have strategy_name parameter
                )
                if "error" in stop_result:
                    logger.warning(f"Stop loss order failed: {stop_result['error']}")
                else:
                    logger.info(f"Stop loss order placed: {stop_result}")
            
            # FIXED: Create TP1 order (partial exit) using proper limit order
            tp1_result = None
            if take_profit_1_price:
                # Round TP1 price to valid tick size
                tick_size = await self._get_tick_size(symbol)
                rounded_tp1_price = self._round_to_tick_size(take_profit_1_price, tick_size)
                logger.info(f"TP1 price: {take_profit_1_price} -> {rounded_tp1_price} (tick_size: {tick_size})")
                
                tp1_side = "SELL" if side.upper() == "BUY" else "BUY"
                tp1_result = await self.place_market_order(
                    symbol=symbol,
                    side=tp1_side,
                    quantity=tp1_quantity,
                    order_type="limit",
                    limit_price=rounded_tp1_price,
                    account_id=target_account
                )
                if "error" in tp1_result:
                    logger.warning(f"TP1 order failed: {tp1_result['error']}")
                else:
                    logger.info(f"TP1 limit order placed: {tp1_result}")
            
            # FIXED: Create TP2 order (remaining position exit) using proper limit order
            tp2_result = None
            if take_profit_2_price:
                # Round TP2 price to valid tick size
                tick_size = await self._get_tick_size(symbol)
                rounded_tp2_price = self._round_to_tick_size(take_profit_2_price, tick_size)
                logger.info(f"TP2 price: {take_profit_2_price} -> {rounded_tp2_price} (tick_size: {tick_size})")
                
                tp2_side = "SELL" if side.upper() == "BUY" else "BUY"
                tp2_quantity = quantity - tp1_quantity  # Remaining contracts after TP1
                if tp2_quantity > 0:
                    tp2_result = await self.place_market_order(
                        symbol=symbol,
                        side=tp2_side,
                        quantity=tp2_quantity,
                        order_type="limit",
                        limit_price=rounded_tp2_price,
                        account_id=target_account
                    )
                    if "error" in tp2_result:
                        logger.warning(f"TP2 order failed: {tp2_result['error']}")
                    else:
                        logger.info(f"TP2 limit order placed: {tp2_result}")
                else:
                    logger.info("No TP2 order needed (TP1 covers entire position)")
            else:
                logger.info("No TP2 order created (full exit at TP1)")
            
            # Create appropriate message based on TP2 presence
            if take_profit_2_price and tp2_quantity > 0:
                message = f"Staged TP bracket created: {tp1_quantity}@TP1, {tp2_quantity}@TP2"
            else:
                message = f"Full TP1 exit created: {tp1_quantity}@TP1 (no TP2)"
            
            # Start position monitoring for this bracket order
            if position_id:
                await self._start_bracket_monitoring(
                    position_id, symbol, target_account,
                    side=side, stop_loss_price=stop_loss_price, take_profit_price=take_profit_1_price
                )
            
            return {
                "success": True,
                "entry_order": entry_result,
                "stop_order": stop_result,
                "tp1_order": tp1_result,
                "tp2_order": tp2_result,
                "position_id": position_id,
                "message": message
            }
            
        except Exception as e:
            logger.error(f"Failed to create partial TP bracket order: {str(e)}")
            return {"error": str(e)}
    
    async def _start_bracket_monitoring(self, position_id: str, symbol: str, account_id: str, 
                                      side: str = None, stop_loss_price: float = None, take_profit_price: float = None) -> None:
        """
        Start monitoring a position for bracket order management.
        This ensures orders are adjusted when position size changes.
        """
        try:
            # Store monitoring info for this position
            if not hasattr(self, '_bracket_monitoring'):
                self._bracket_monitoring = {}
            
            self._bracket_monitoring[position_id] = {
                'symbol': symbol,
                'account_id': account_id,
                'side': side,  # Original trade direction
                'stop_loss_price': stop_loss_price,  # Original stop loss price
                'take_profit_price': take_profit_price,  # Original take profit price
                'original_quantity': None,  # Will be set when we first check
                'last_check': None,
                'active': True
            }
            
            logger.info(f"Started bracket monitoring for position {position_id} ({symbol}) - Side: {side}, SL: {stop_loss_price}, TP: {take_profit_price}")
            
        except Exception as e:
            logger.error(f"Failed to start bracket monitoring: {str(e)}")
    
    async def _manage_bracket_orders(self, position_id: str) -> Dict:
        """
        Manage bracket orders for a specific position.
        Adjusts orders when position size changes and cancels conflicting orders.
        """
        try:
            if not hasattr(self, '_bracket_monitoring') or position_id not in self._bracket_monitoring:
                return {"error": "Position not being monitored"}
            
            monitoring_info = self._bracket_monitoring[position_id]
            if not monitoring_info['active']:
                return {"message": "Monitoring stopped for this position"}
            
            symbol = monitoring_info['symbol']
            account_id = monitoring_info['account_id']
            
            # Get current position
            positions = await self.get_open_positions(account_id)
            current_position = None
            for pos in positions:
                if str(pos.get('id', '')) == str(position_id):
                    current_position = pos
                    break
            
            if not current_position:
                logger.info(f"Position {position_id} no longer exists, stopping monitoring")
                monitoring_info['active'] = False
                return {"message": "Position closed, monitoring stopped"}
            
            current_quantity = current_position.get('size', 0)
            original_quantity = monitoring_info.get('original_quantity')
            
            # Set original quantity on first check
            if original_quantity is None:
                monitoring_info['original_quantity'] = current_quantity
                original_quantity = current_quantity
                logger.info(f"Set original quantity for position {position_id}: {original_quantity}")
            
            # Check if position size has changed
            if current_quantity == original_quantity:
                return {"message": "Position size unchanged"}
            
            logger.info(f"Position {position_id} size changed: {original_quantity} → {current_quantity}")
            
            # Get all open orders for this symbol (using individual call since we only need orders)
            orders = await self.get_open_orders(account_id)
            symbol_orders = []
            try:
                symbol_contract_id = self._get_contract_id(symbol)
                for order in orders:
                    order_contract = order.get('contractId', '')
                    if order_contract == symbol_contract_id:
                        symbol_orders.append(order)
            except ValueError as e:
                logger.error(f"❌ Cannot filter orders: {e}. Please fetch contracts first.")
                return {"error": str(e)}
            
            # Cancel all existing TP and SL orders for this symbol
            canceled_orders = []
            for order in symbol_orders:
                order_id = order.get('id')
                order_type = order.get('type', 0)
                custom_tag = order.get('customTag', '')
                
                # Cancel stop loss and take profit orders
                if (order_type in [1, 4] or  # Limit or Stop orders
                    'AutoBracket' in custom_tag or
                    '-SL' in custom_tag or '-TP' in custom_tag):
                    
                    cancel_result = await self.cancel_order(order_id, account_id)
                    if "error" not in cancel_result:
                        canceled_orders.append(order_id)
                        logger.info(f"Canceled order {order_id} (type: {order_type}, tag: {custom_tag})")
                    else:
                        logger.warning(f"Failed to cancel order {order_id}: {cancel_result.get('error')}")
            
            # If position is still open, create new orders based on remaining quantity
            if current_quantity > 0:
                # CRITICAL: All positions must have protection - never leave positions unhedged
                logger.warning(f"Position {position_id} has {current_quantity} contracts remaining - creating new protection orders")
                
                # Get the original trade parameters from monitoring info
                original_side = monitoring_info.get('side', 'BUY')
                original_stop_loss = monitoring_info.get('stop_loss_price')
                original_take_profit = monitoring_info.get('take_profit_price')
                
                if original_stop_loss and original_take_profit:
                    # Create new bracket order for remaining position
                    new_side = "SELL" if original_side == "BUY" else "BUY"
                    
                    logger.info(f"Creating new protection for {current_quantity} contracts: {new_side} with SL={original_stop_loss}, TP={original_take_profit}")
                    
                    new_bracket_result = await self.create_bracket_order(
                        symbol=symbol,
                        side=new_side,
                        quantity=current_quantity,
                        stop_loss_price=original_stop_loss,
                        take_profit_price=original_take_profit,
                        account_id=account_id
                    )
                    
                    if "error" in new_bracket_result:
                        logger.error(f"Failed to create new protection orders: {new_bracket_result['error']}")
                        logger.error("⚠️ POSITION IS UNPROTECTED - MANUAL INTERVENTION REQUIRED")
                    else:
                        logger.info(f"Successfully created new protection orders: {new_bracket_result}")
                        # Update monitoring info with new order details
                        monitoring_info['original_quantity'] = current_quantity
                else:
                    logger.error(f"Cannot create protection orders - missing original parameters for position {position_id}")
                    logger.error("⚠️ POSITION IS UNPROTECTED - MANUAL INTERVENTION REQUIRED")
            else:
                logger.info(f"Position {position_id} fully closed - all orders canceled")
                monitoring_info['active'] = False
            
            return {
                "success": True,
                "position_quantity": current_quantity,
                "canceled_orders": canceled_orders,
                "monitoring_active": monitoring_info['active']
            }
            
        except Exception as e:
            logger.error(f"Failed to manage bracket orders: {str(e)}")
            return {"error": str(e)}
    
    async def monitor_all_bracket_positions(self, account_id: str = None) -> Dict:
        """
        Monitor all positions with active bracket orders.
        This should be called periodically to manage order adjustments.
        """
        try:
            target_account = account_id or (self.selected_account['id'] if self.selected_account else None)
            
            if not target_account:
                return {"error": "No account selected"}
            
            # First, check for any unprotected positions
            await self._check_unprotected_positions(target_account)
            
            if not hasattr(self, '_bracket_monitoring'):
                return {"message": "No positions being monitored"}
            
            results = {}
            positions_to_remove = []
            
            for position_id, monitoring_info in self._bracket_monitoring.items():
                if not monitoring_info['active']:
                    positions_to_remove.append(position_id)
                    continue
                
                result = await self._manage_bracket_orders(position_id)
                results[position_id] = result
                
                # If monitoring stopped, mark for removal
                if not monitoring_info['active']:
                    positions_to_remove.append(position_id)
            
            # Clean up stopped monitoring
            for position_id in positions_to_remove:
                del self._bracket_monitoring[position_id]
                logger.info(f"Removed monitoring for position {position_id}")
            
            return {
                "success": True,
                "monitored_positions": len(self._bracket_monitoring),
                "results": results,
                "removed_positions": len(positions_to_remove)
            }
            
        except Exception as e:
            logger.error(f"Failed to monitor bracket positions: {str(e)}")
            return {"error": str(e)}
    
    async def _check_unprotected_positions(self, account_id: str) -> None:
        """
        Check for positions that don't have proper stop/target protection.
        This is a safety mechanism to prevent orphaned positions.
        """
        try:
            # Use batch API call for efficiency (reduces round-trips by 50%)
            batch_result = await self.get_positions_and_orders_batch(account_id)
            positions = batch_result.get("positions", [])
            orders = batch_result.get("orders", [])
            
            if not positions:
                return
            
            for position in positions:
                position_id = str(position.get('id'))
                symbol = position.get('contractId', '')
                size = position.get('size', 0)
                
                if size == 0:
                    continue
                
                # Check if this position has any protective orders
                has_protection = False
                for order in orders:
                    order_contract = order.get('contractId', '')
                    if order_contract == symbol:
                        order_type = order.get('type', 0)
                        custom_tag = order.get('customTag', '')
                        
                        # Check for stop loss or take profit orders
                        if (order_type in [1, 4] or  # Limit or Stop orders
                            'AutoBracket' in custom_tag or
                            '-SL' in custom_tag or '-TP' in custom_tag):
                            has_protection = True
                            break
                
                if not has_protection:
                    logger.error(f"⚠️ UNPROTECTED POSITION DETECTED: {position_id} - {symbol} size {size}")
                    logger.error("This position has no stop loss or take profit orders!")
                    logger.error("Manual intervention required to add protection")
                    
                    # If this position is not being monitored, start monitoring it
                    if not hasattr(self, '_bracket_monitoring') or position_id not in self._bracket_monitoring:
                        logger.warning(f"Starting emergency monitoring for unprotected position {position_id}")
                        # We can't start proper monitoring without original trade parameters
                        # But we can at least track it
                        if not hasattr(self, '_bracket_monitoring'):
                            self._bracket_monitoring = {}
                        
                        self._bracket_monitoring[position_id] = {
                            'symbol': symbol,
                            'account_id': account_id,
                            'side': 'UNKNOWN',  # We don't know the original direction
                            'stop_loss_price': None,  # We don't have original parameters
                            'take_profit_price': None,
                            'original_quantity': size,
                            'last_check': None,
                            'active': True,
                            'emergency': True  # Mark as emergency monitoring
                        }
                        logger.warning(f"Emergency monitoring started for position {position_id}")
            
        except Exception as e:
            logger.error(f"Failed to check unprotected positions: {str(e)}")
    
    async def get_linked_orders(self, position_id: str, account_id: str = None) -> List[Dict]:
        """
        Get all orders linked to a specific position.
        
        Args:
            position_id: Position ID
            account_id: Account ID (uses selected account if not provided)
            
        Returns:
            List[Dict]: List of linked orders
        """
        try:
            target_account = account_id or (self.selected_account['id'] if self.selected_account else None)
            
            if not target_account:
                return {"error": "No account selected"}
            
            if not self.session_token:
                return {"error": "No session token available. Please authenticate first."}
            
            logger.info(f"Fetching linked orders for position {position_id}")
            
            headers = {
                "accept": "text/plain",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.session_token}"
            }
            
            # Use the official TopStepX Gateway API for orders
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc)
            start_time = now.replace(hour=0, minute=0, second=0, microsecond=0)
            
            search_data = {
                "accountId": int(target_account),
                "startTimestamp": start_time.isoformat(),
                "endTimestamp": now.isoformat(),
                "request": {
                    "accountId": int(target_account),
                    "status": "Open"
                }
            }
            
            logger.info(f"Requesting linked orders for position {position_id} using TopStepX Gateway API")
            logger.info(f"Request data: {search_data}")
            
            # Call the official TopStepX Gateway API
            response = self._make_curl_request("POST", "/api/Order/search", data=search_data, headers=headers)
            
            if "error" in response:
                logger.error(f"TopStepX Gateway API failed: {response['error']}")
                return []
            
            if not response.get("success"):
                logger.error(f"TopStepX Gateway API returned error: {response}")
                return []
            
            # Check for different possible order data fields
            orders = []
            for field in ["orders", "data", "result", "items", "list"]:
                if field in response and isinstance(response[field], list):
                    orders = response[field]
                    break
            
            # Filter orders that are linked to this position
            # Since we don't have direct position linking, we'll find orders for the same contract
            # that are likely bracket orders (stop loss and take profit)
            linked_orders = []
            position_contract = None
            
            # Get the contract ID for the position
            positions = await self.get_open_positions(target_account)
            for pos in positions:
                if str(pos.get('id', '')) == str(position_id):
                    position_contract = pos.get('contractId')
                    break
            
            if not position_contract:
                logger.error(f"Could not find contract for position {position_id}")
                return []
            
            for order in orders:
                order_contract = order.get('contractId', '')
                order_status = order.get('status', 0)
                custom_tag = order.get('customTag', '') or ''  # Ensure it's never None
                order_type = order.get('type', 0)
                
                # Only process open orders for the same contract
                if order_contract == position_contract and order_status == 1:  # Status 1 = Open
                    # Check for bracket orders using customTag
                    if custom_tag and "AutoBracket" in custom_tag:
                        if "-SL" in custom_tag or "-TP" in custom_tag:
                            linked_orders.append(order)
                            logger.info(f"Found bracket order: {order.get('id')} tag: {custom_tag}")
                    # Also check by order type (4 = Stop orders, 1 = Limit orders)
                    elif order_type == 4:  # Stop orders
                        linked_orders.append(order)
                        logger.info(f"Found stop order: {order.get('id')} type: {order_type}")
                    elif order_type == 1:  # Limit orders that might be take profit
                        side = order.get('side', -1)
                        if side == 1:  # SELL side limit order is likely take profit
                            linked_orders.append(order)
                            logger.info(f"Found take profit order: {order.get('id')} type: {order_type}")
            
            logger.info(f"Found {len(linked_orders)} linked orders for position {position_id}")
            return linked_orders
            
        except Exception as e:
            logger.error(f"Failed to fetch linked orders: {str(e)}")
            return []
    
    async def adjust_bracket_orders(self, position_id: str, new_quantity: int, 
                                   account_id: str = None) -> Dict:
        """
        Adjust bracket orders when position size changes.
        This method automatically finds and adjusts all linked stop loss and take profit orders.
        
        Args:
            position_id: Position ID
            new_quantity: New total quantity for the position
            account_id: Account ID (uses selected account if not provided)
            
        Returns:
            Dict: Adjustment response or error
        """
        try:
            target_account = account_id or (self.selected_account['id'] if self.selected_account else None)
            
            if not target_account:
                return {"error": "No account selected"}
            
            if not self.session_token:
                return {"error": "No session token available. Please authenticate first."}
            
            logger.info(f"Adjusting bracket orders for position {position_id} to quantity {new_quantity}")
            
            # Get current open orders to find linked ones
            open_orders = await self.get_open_orders(target_account)
            
            if not open_orders:
                logger.warning("No open orders found")
                return {"error": "No open orders found"}
            
            # Find orders that are linked to this position
            # Look for bracket orders using customTag and contract matching
            linked_orders = []
            position_contract = None
            
            # Get the contract ID for the position
            positions = await self.get_open_positions(target_account)
            for pos in positions:
                if str(pos.get('id', '')) == str(position_id):
                    position_contract = pos.get('contractId')
                    break
            
            if not position_contract:
                logger.error(f"Could not find contract for position {position_id}")
                return {"error": f"Could not find contract for position {position_id}"}
            
            for order in open_orders:
                order_contract = order.get('contractId', '')
                custom_tag = order.get('customTag', '')
                order_type = order.get('type', 0)
                order_status = order.get('status', 0)
                order_side = order.get('side', -1)
                
                # Only process open orders for the same contract
                if order_contract == position_contract and order_status == 1:  # Status 1 = Open
                    # Check for bracket orders using customTag
                    if "AutoBracket" in custom_tag:
                        if "-SL" in custom_tag or "-TP" in custom_tag:
                            linked_orders.append(order)
                            logger.info(f"Found bracket order: {order.get('id')} tag: {custom_tag}")
                    # Also check by order type (4 = Stop orders, 1 = Limit orders)
                    elif order_type == 4:  # Stop orders
                        linked_orders.append(order)
                        logger.info(f"Found stop order: {order.get('id')} type: {order_type}")
                    elif order_type == 1:  # Limit orders that might be take profit
                        # Check if this is a take profit order (opposite side from position)
                        # For long positions, take profit should be SELL (side=1)
                        # For short positions, take profit should be BUY (side=0)
                        linked_orders.append(order)
                        logger.info(f"Found limit order (potential TP): {order.get('id')} type: {order_type} side: {order_side}")
                    # Also check for any orders with our custom tag prefix
                    elif "TradingBot-v1.0" in custom_tag:
                        linked_orders.append(order)
                        logger.info(f"Found bot order: {order.get('id')} tag: {custom_tag}")
            
            if not linked_orders:
                logger.warning("No linked orders found for position")
                return {"error": "No linked orders found for position"}
            
            # Adjust each linked order
            adjustment_results = []
            for order in linked_orders:
                order_id = order.get("id")
                current_quantity = order.get("size", 0)
                custom_tag = order.get("customTag", "")
                
                if order_id and current_quantity != new_quantity:
                    try:
                        logger.info(f"Adjusting order {order_id} from {current_quantity} to {new_quantity} (tag: {custom_tag})")
                        # Get order type for proper price field handling
                        order_type = order.get("type", 1)  # Default to limit order
                        # Modify the order with new quantity
                        modify_result = await self.modify_order(order_id, new_quantity=new_quantity, account_id=target_account, order_type=order_type)
                        if "error" not in modify_result:
                            adjustment_results.append({"order_id": order_id, "success": True, "result": modify_result})
                            logger.info(f"Successfully adjusted order {order_id} to quantity {new_quantity}")
                        else:
                            adjustment_results.append({"order_id": order_id, "success": False, "error": modify_result["error"]})
                            logger.error(f"Failed to adjust order {order_id}: {modify_result['error']}")
                    except Exception as e:
                        logger.error(f"Exception adjusting order {order_id}: {e}")
                        adjustment_results.append({"order_id": order_id, "success": False, "error": str(e)})
                else:
                    logger.info(f"Order {order_id} already has correct quantity {current_quantity}, skipping")
            
            successful_adjustments = [r for r in adjustment_results if r.get("success")]
            logger.info(f"Adjusted {len(successful_adjustments)} out of {len(adjustment_results)} linked orders")
            
            return {
                "success": True, 
                "adjusted_orders": len(successful_adjustments),
                "total_orders": len(adjustment_results),
                "results": adjustment_results
            }
            
        except Exception as e:
            logger.error(f"Failed to adjust bracket orders: {str(e)}")
            return {"error": str(e)}
    
    async def monitor_position_changes(self, account_id: str = None) -> Dict:
        """
        Monitor position changes and automatically adjust bracket orders.
        This method should be called periodically to track position size changes.
        Only monitors if a market order was recently placed to avoid interfering with concurrent bracket orders.
        
        Args:
            account_id: Account ID (uses selected account if not provided)
            
        Returns:
            Dict: Monitoring results
        """
        try:
            target_account = account_id or (self.selected_account['id'] if self.selected_account else None)
            
            if not target_account:
                return {"error": "No account selected"}
            
            # Check if monitoring should be active
            if not self._monitoring_active:
                logger.info("Monitoring not active - no recent market orders placed")
                return {"positions": 0, "adjustments": 0, "message": "Monitoring not active - no recent market orders"}
            
            # Check if monitoring should timeout (after 30 seconds)
            if self._last_order_time:
                time_since_order = (datetime.now() - self._last_order_time).total_seconds()
                if time_since_order > 30:  # 30 seconds
                    self._monitoring_active = False
                    logger.info("Monitoring deactivated - timeout after 30 seconds")
                    return {"positions": 0, "adjustments": 0, "message": "Monitoring timeout - no recent market orders"}
            
            logger.info(f"Monitoring position changes for account {target_account}")
            
            # Get current positions
            positions = await self.get_open_positions(target_account)
            
            if not positions:
                logger.info("No open positions found - checking for orphaned orders")
                
                # Get all open orders
                orders = await self.get_open_orders(target_account)
                if orders:
                    logger.info(f"Found {len(orders)} open orders with no positions - these may be orphaned")
                    
                    # Cancel all open orders since there are no positions
                    cancelled_orders = 0
                    for order in orders:
                        order_id = order.get("id")
                        if order_id:
                            try:
                                cancel_result = await self.cancel_order(order_id, target_account)
                                if "error" not in cancel_result:
                                    cancelled_orders += 1
                                    logger.info(f"Cancelled orphaned order {order_id}")
                                else:
                                    logger.warning(f"Failed to cancel order {order_id}: {cancel_result['error']}")
                            except Exception as e:
                                logger.error(f"Exception cancelling order {order_id}: {e}")
                    
                    if cancelled_orders > 0:
                        logger.info(f"Cancelled {cancelled_orders} orphaned orders")
                        return {"positions": 0, "adjustments": 0, "cancelled_orders": cancelled_orders}
                
                return {"positions": 0, "adjustments": 0}
            
            adjustments_made = 0
            
            for position in positions:
                position_id = position.get("id")
                current_quantity = position.get("size", 0)  # Use 'size' field for position quantity
                symbol = position.get("symbol", "Unknown")
                
                logger.info(f"Checking position {position_id}: {current_quantity} {symbol}")
                
                # Check if we have tracked this position before
                # In a real implementation, you'd store the previous quantity
                # For now, we'll just log the current state
                
                # Get linked orders for this position
                linked_orders = await self.get_linked_orders(position_id, target_account)
                
                if linked_orders:
                    logger.info(f"Found {len(linked_orders)} linked orders for position {position_id}")
                    
                    # Check if any linked orders need quantity adjustment
                    # Only adjust if position quantity is greater than 0
                    if current_quantity > 0:
                        for order in linked_orders:
                            order_quantity = order.get("size", 0)  # Use 'size' field for order quantity
                            if order_quantity != current_quantity:
                                logger.info(f"Order {order.get('id')} quantity {order_quantity} != position quantity {current_quantity}")
                                
                                # Adjust the order quantity to match position
                                adjust_result = await self.adjust_bracket_orders(position_id, current_quantity, target_account)
                                if adjust_result.get("success"):
                                    adjustments_made += 1
                                    logger.info(f"Successfully adjusted bracket orders for position {position_id}")
                                else:
                                    logger.error(f"Failed to adjust bracket orders for position {position_id}: {adjust_result.get('error')}")
                                break  # Only adjust once per position
                    else:
                        logger.info(f"Position {position_id} has zero quantity, skipping bracket order adjustments")
            
            return {
                "success": True,
                "positions": len(positions),
                "adjustments": adjustments_made
            }
            
        except Exception as e:
            logger.error(f"Failed to monitor position changes: {str(e)}")
            return {"error": str(e)}
    
    # ============================================================================
    # NATIVE TOPSTEPX API METHODS - ADVANCED ORDER TYPES
    # ============================================================================
    
    async def place_stop_order(self, symbol: str, side: str, quantity: int, stop_price: float,
                              account_id: str = None, strategy_name: Optional[str] = None) -> Dict:
        """
        Place a stop order (entry stop - triggers market order when price is hit).
        Use stop_buy for BUY stop orders or stop_sell for SELL stop orders.
        
        Args:
            symbol: Trading symbol
            side: "BUY" or "SELL"
            quantity: Number of contracts
            stop_price: Stop price (triggers when price reaches this level)
            account_id: Account ID (uses selected account if not provided)
            strategy_name: Optional strategy name for custom tagging
            
        Returns:
            Dict: Stop order response or error
        """
        try:
            target_account = account_id or (self.selected_account['id'] if self.selected_account else None)
            
            if not target_account:
                return {"error": "No account selected"}
            
            if not self.session_token:
                return {"error": "No session token available. Please authenticate first."}
            
            if side.upper() not in ["BUY", "SELL"]:
                return {"error": "Side must be 'BUY' or 'SELL'"}
            
            # Round stop price to valid tick size
            tick_size = await self._get_tick_size(symbol)
            rounded_stop_price = self._round_to_tick_size(stop_price, tick_size)
            logger.info(f"Stop price: {stop_price} -> {rounded_stop_price} (tick_size: {tick_size})")
            logger.info(f"Placing stop {side} order for {quantity} {symbol} at {rounded_stop_price}")
            
            # Get proper contract ID
            try:
                contract_id = self._get_contract_id(symbol)
            except ValueError as e:
                error_msg = f"Cannot create bracket order: {e}. Please fetch contracts first using 'contracts' command."
                logger.error(f"❌ {error_msg}")
                return {"error": error_msg}
            
            # Convert side to numeric value
            side_value = 0 if side.upper() == "BUY" else 1
            
            # Prepare stop order data (type 4 = Stop order)
            stop_data = {
                "accountId": int(target_account),
                "contractId": contract_id,
                "type": 4,  # Stop order type (triggers market order when price is hit)
                "side": side_value,
                "size": quantity,
                "stopPrice": rounded_stop_price,
                "customTag": self._generate_unique_custom_tag("stop_entry", strategy_name)
            }
            
            headers = {
                "accept": "text/plain",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.session_token}"
            }
            
            response = self._make_curl_request("POST", "/api/Order/place", data=stop_data, headers=headers)
            
            if "error" in response:
                logger.error(f"Failed to place stop order: {response['error']}")
                return response
            
            # Update order activity timestamp
            self._update_order_activity()
            
            logger.info(f"Stop {side} order placed successfully: {response}")
            return response
            
        except Exception as e:
            logger.error(f"Failed to place stop order: {str(e)}")
            return {"error": str(e)}
    
    async def place_oco_bracket_with_stop_entry(self, symbol: str, side: str, quantity: int,
                                                entry_price: float, stop_loss_price: float,
                                                take_profit_price: float, account_id: str = None,
                                                enable_breakeven: bool = False, strategy_name: str = None) -> Dict:
        """
        Place OCO bracket order with stop order as entry.
        
        Now uses TopStepXAdapter for bracket order placement, maintaining backward compatibility.
        
        Args:
            symbol: Trading symbol (e.g., "MNQ", "ES")
            side: "BUY" or "SELL"
            quantity: Number of contracts
            entry_price: Stop price for entry
            stop_loss_price: Stop loss price
            take_profit_price: Take profit price
            account_id: Account ID (uses selected account if not provided)
            enable_breakeven: Enable breakeven stop monitoring (default: False)
            strategy_name: Optional strategy name for tracking
            
        Returns:
            Dict: OCO bracket response or error
        """
        try:
            target_account = account_id or (self.selected_account['id'] if self.selected_account else None)
            
            if not target_account:
                return {"error": "No account selected"}
            
            if side.upper() not in ["BUY", "SELL"]:
                return {"error": "Side must be 'BUY' or 'SELL'"}
            
            # Use TopStepXAdapter for bracket order placement
            result = await self.broker_adapter.place_oco_bracket_with_stop_entry(
                symbol=symbol,
                side=side,
                quantity=quantity,
                entry_price=entry_price,
                stop_loss_price=stop_loss_price,
                take_profit_price=take_profit_price,
                account_id=target_account,
                enable_breakeven=enable_breakeven,
                strategy_name=strategy_name
            )
            
            # Convert OrderResponse to dict for backward compatibility
            if result.success:
                return {
                    "success": True,
                    "orderId": result.order_id,
                    "message": result.message,
                    **({"raw_response": result.raw_response} if result.raw_response else {})
                }
            else:
                return {"error": result.error}
            
        except Exception as e:
            logger.error(f"Failed to place OCO bracket with stop entry: {str(e)}")
            return {"error": str(e)}
    
    async def _stop_bracket_hybrid(self, symbol: str, side: str, quantity: int,
                                  entry_price: float, stop_loss_price: float,
                                  take_profit_price: float, account_id: str = None,
                                  enable_breakeven: bool = False, strategy_name: Optional[str] = None) -> Dict:
        """
        Hybrid stop bracket: place stop order for entry, then auto-bracket on fill.
        
        This is a fallback when OCO brackets are not enabled in TopStepX platform.
        
        Args:
            symbol: Trading symbol
            side: "BUY" or "SELL"
            quantity: Number of contracts
            entry_price: Stop price for entry
            stop_loss_price: Stop loss price
            take_profit_price: Take profit price
            account_id: Account ID
            enable_breakeven: Enable breakeven stop monitoring (default: False)
            
        Returns:
            Dict: Order response
        """
        try:
            logger.info("Using hybrid approach: stop order + auto-bracket")
            
            # 1. Place stop order for entry
            stop_result = await self.place_stop_order(
                symbol=symbol,
                side=side,
                quantity=quantity,
                stop_price=entry_price,
                account_id=account_id,
                strategy_name=strategy_name
            )
            
            if "error" in stop_result:
                return {"error": f"Stop order failed: {stop_result['error']}"}
            
            order_id = stop_result.get('orderId')
            logger.info(f"Stop order placed: {order_id}")
            
            # 2. Start background monitor for fill
            async def monitor_and_bracket():
                """Monitor stop order and place brackets when filled."""
                max_wait_time = 3600  # 1 hour max
                check_interval = 1  # Check every second
                elapsed_time = 0
                
                while elapsed_time < max_wait_time:
                    try:
                        # Check order status
                        orders = await self.get_open_orders(account_id=account_id)
                        
                        # Check if our order is filled
                        order_found = False
                        order_filled = False
                        
                        for order in orders:
                            if order.get('id') == order_id or str(order.get('id')) == str(order_id):
                                order_found = True
                                status = order.get('status', -1)
                                
                                # Status 2, 3, 4 = Filled/Executed/Complete
                                if status in [2, 3, 4]:
                                    order_filled = True
                                    logger.info(f"Stop order {order_id} filled! Placing brackets")
                                    break
                                # Status 3, 5, 6 = Cancelled/Rejected
                                elif status in [5, 6]:
                                    logger.warning(f"Stop order {order_id} was cancelled/rejected")
                                    return
                        
                        if order_filled:
                            # Wait a moment for position to register
                            await asyncio.sleep(0.5)
                            
                            # Get position
                            positions = await self.get_open_positions(account_id=account_id)
                            position_id = None
                            
                            for pos in positions:
                                pos_symbol = pos.get('symbol', '').upper()
                                if symbol.upper() in pos_symbol or pos_symbol in symbol.upper():
                                    position_id = pos.get('id')
                                    logger.info(f"Found position {position_id} for {symbol}")
                                    break
                            
                            if position_id:
                                # Place brackets
                                logger.info(f"Placing brackets on position {position_id}")
                                
                                sl_result = await self.modify_stop_loss(position_id, stop_loss_price, account_id)
                                if "error" not in sl_result:
                                    logger.info(f"Stop loss set at ${stop_loss_price:.2f}")
                                else:
                                    logger.error(f"Stop loss failed: {sl_result['error']}")
                                
                                tp_result = await self.modify_take_profit(position_id, take_profit_price, account_id)
                                if "error" not in tp_result:
                                    logger.info(f"Take profit set at ${take_profit_price:.2f}")
                                else:
                                    logger.error(f"Take profit failed: {tp_result['error']}")
                                
                                print(f"\n✅ Brackets placed on position {position_id}")
                                print(f"   Stop Loss: ${stop_loss_price:.2f}")
                                print(f"   Take Profit: ${take_profit_price:.2f}")
                            else:
                                logger.error(f"Position not found for {symbol} after fill")
                            
                            return
                        
                        if not order_found:
                            # Order might have been filled and closed already
                            logger.info(f"Order {order_id} not found in open orders, may have filled and closed")
                            return
                        
                    except Exception as e:
                        logger.error(f"Error in bracket monitor: {e}")
                    
                    await asyncio.sleep(check_interval)
                    elapsed_time += check_interval
                
                logger.warning(f"Bracket monitor timed out after {max_wait_time}s")
            
            # Start monitoring in background
            asyncio.create_task(monitor_and_bracket())
            
            # Setup breakeven monitoring if enabled
            if enable_breakeven and order_id and hasattr(self, 'overnight_strategy'):
                logger.info(f"Setting up breakeven monitoring for hybrid order {order_id}")
                breakeven_points = float(os.getenv('MANUAL_BREAKEVEN_PROFIT_POINTS', '15.0'))
                self.overnight_strategy.breakeven_monitoring[order_id] = {
                    "symbol": symbol,
                    "side": "LONG" if side.upper() == "BUY" else "SHORT",
                    "entry_price": entry_price,
                    "original_stop": stop_loss_price,
                    "breakeven_threshold": breakeven_points,
                    "breakeven_triggered": False,
                    "is_filled": False  # Will be set to True when entry order fills
                }
                logger.info(f"Breakeven monitoring active: {breakeven_points} pts profit threshold")
            
            return {
                "success": True,
                "orderId": order_id,
                "method": "hybrid_auto_bracket",
                "symbol": symbol,
                "side": side,
                "quantity": quantity,
                "entry_price": entry_price,
                "stop_loss": stop_loss_price,
                "take_profit": take_profit_price,
                "breakeven_enabled": enable_breakeven,
                "message": "Stop order placed, will auto-bracket on fill"
            }
            
        except Exception as e:
            logger.error(f"Hybrid bracket failed: {str(e)}")
            return {"error": str(e)}
    
    async def place_trailing_stop_order(self, symbol: str, side: str, quantity: int, 
                                       trail_amount: float, account_id: str = None) -> Dict:
        """
        Place a trailing stop order using the Project-X SDK (native trailing stop support).
        
        The SDK provides native trailing stop functionality that automatically adjusts
        the stop price as the market moves in your favor. This is more efficient than
        manual trailing stops.
        
        Args:
            symbol: Trading symbol (e.g., "MNQ", "ES")
            side: "BUY" or "SELL"
            quantity: Number of contracts
            trail_amount: Trail amount in price units (e.g., 25.00 for $25)
            account_id: Account ID (uses selected account if not provided)
            
        Returns:
            Dict: Trailing stop order response or error
        """
        try:
            target_account = account_id or (self.selected_account['id'] if self.selected_account else None)
            
            if not target_account:
                return {"error": "No account selected"}
            
            if side.upper() not in ["BUY", "SELL"]:
                return {"error": "Side must be 'BUY' or 'SELL'"}
            
            logger.info(f"Placing trailing stop order for {side} {quantity} {symbol} with trail ${trail_amount}")
            
            # Try SDK first if available (native trailing stop support)
            use_sdk = os.getenv("USE_PROJECTX_SDK", "0").lower() in ("1", "true", "yes")
            if use_sdk and sdk_adapter is not None and sdk_adapter.is_sdk_available():
                try:
                    logger.info("✅ Attempting SDK native trailing stop via TradingSuite")
                    
                    # Get or create a cached TradingSuite instance for order placement
                    # This avoids re-authenticating and reconnecting for every order
                    suite = await sdk_adapter.get_or_create_order_suite(symbol, account_id=int(target_account))
                    
                    try:
                        # Get contract ID for the order
                        try:
                            contract_id = self._get_contract_id(symbol)
                        except ValueError as e:
                            error_msg = f"Cannot place trailing stop order: {e}. Please fetch contracts first."
                            logger.error(f"❌ {error_msg}")
                            return {"error": error_msg}
                        
                        # Convert side to numeric value (0=BUY, 1=SELL)
                        side_value = 0 if side.upper() == "BUY" else 1
                        
                        # Determine tick size
                        tick_size = await self._get_tick_size(symbol)
                        
                        # Convert trail amount to ticks
                        trail_ticks = trail_amount / tick_size
                        
                        # Server-side limit defaults to 1000 ticks
                        max_ticks = 1000
                        clamped = False
                        if trail_ticks > max_ticks:
                            clamped = True
                            trail_ticks = max_ticks
                            trail_amount = max_ticks * tick_size
                            logger.warning(f"Trail exceeded max; clamped to {max_ticks} ticks -> ${trail_amount:.2f}")
                        
                        logger.info(f"Trail amount: ${trail_amount} = {trail_ticks:.0f} ticks (tick_size: {tick_size})")
                        
                        # Try to access order manager from suite
                        order_manager = None
                        if hasattr(suite, 'orders') and suite.orders is not None:
                            order_manager = suite.orders
                            logger.debug("Using suite.orders")
                        elif hasattr(suite, 'order_manager') and suite.order_manager is not None:
                            order_manager = suite.order_manager
                            logger.debug("Using suite.order_manager")
                        
                        # If we have an order manager with the method, use it
                        if order_manager and hasattr(order_manager, 'place_trailing_stop_order'):
                            logger.info("✅ Using SDK order manager for trailing stop")
                            order_result = await order_manager.place_trailing_stop_order(
                                contract_id=contract_id,
                                side=side_value,
                                size=quantity,
                                trail_distance=int(trail_ticks),
                                account_id=int(target_account)
                            )
                        # Otherwise, try calling method directly on suite
                        elif hasattr(suite, 'place_trailing_stop_order'):
                            logger.info("✅ Using SDK suite.place_trailing_stop_order()")
                            order_result = await suite.place_trailing_stop_order(
                                contract_id=contract_id,
                                side=side_value,
                                size=quantity,
                                trail_distance=int(trail_ticks),
                                account_id=int(target_account)
                            )
                        # Last resort: use the client API directly
                        elif hasattr(suite, 'client') and suite.client:
                            logger.info("✅ Using SDK client API for trailing stop")
                            # Use the raw client API
                            order_data = {
                                "accountId": int(target_account),
                                "contractId": contract_id,
                                "type": 5,  # Trailing stop order type
                                "side": side_value,
                                "size": quantity,
                                "trailDistance": int(trail_ticks),
                            }
                            order_result = await suite.client.place_order(**order_data)
                        else:
                            logger.warning("SDK TradingSuite doesn't have accessible order methods")
                            logger.debug(f"Suite attributes: {[a for a in dir(suite) if not a.startswith('_')]}")
                            return {"error": "SDK TradingSuite orders unavailable"}
                        
                        # At this point order_result should be set
                        logger.info(f"✅ SDK trailing stop order placed successfully: {order_result}")
                        self._update_order_activity()
                        
                        # Keep suite cached - don't disconnect
                        # It will be reused for subsequent orders
                        
                        # Extract order ID from response
                        order_id = None
                        if hasattr(order_result, 'order_id'):
                            order_id = order_result.order_id
                        elif hasattr(order_result, 'id'):
                            order_id = order_result.id
                        elif isinstance(order_result, dict):
                            order_id = order_result.get('order_id') or order_result.get('id') or order_result.get('orderId')
                        
                        result_payload = {
                            "success": True,
                            "orderId": order_id,
                            "message": "Trailing stop order placed via SDK",
                            "sdk_result": order_result
                        }
                        if clamped:
                            result_payload["clamped"] = True
                            result_payload["trail_price_used"] = trail_amount
                            result_payload["trail_ticks_used"] = int(max_ticks)
                        return result_payload
                    except Exception as suite_err:
                        logger.error(f"SDK TradingSuite order placement failed: {suite_err}")
                        import traceback
                        logger.debug(f"SDK error traceback: {traceback.format_exc()}")
                        # Keep suite cached - may be transient error
                        return {"error": f"SDK trailing stop failed: {suite_err}"}
                
                except Exception as sdk_err:
                    logger.error(f"SDK trailing stop failed: {sdk_err}")
                    import traceback
                    logger.debug(f"SDK error traceback: {traceback.format_exc()}")
                    return {"error": f"SDK trailing stop failed: {sdk_err}"}

            # Fallback to adapter if SDK unavailable
            logger.info("SDK unavailable, using adapter for trailing stop order")
            result = await self.broker_adapter.place_trailing_stop_order(
                symbol=symbol,
                side=side,
                quantity=quantity,
                trail_amount=trail_amount,
                account_id=target_account
            )
            
            # Convert OrderResponse to dict for backward compatibility
            if result.success:
                return {
                    "success": True,
                    "orderId": result.order_id,
                    "message": result.message,
                    **({"raw_response": result.raw_response} if result.raw_response else {})
                }
            else:
                return {"error": result.error}
            
        except Exception as e:
            logger.error(f"Failed to place trailing stop order: {str(e)}")
            return {"error": str(e)}
    
    # ============================================================================
    # NATIVE TOPSTEPX API METHODS - MARKET DATA
    # ============================================================================
    
    async def get_market_quote(self, symbol: str) -> Dict:
        """
        Get near real-time market quote for a symbol.
        Prefer SignalR live stream (bid/ask/last/volume); fallback to REST bars.
        
        Args:
            symbol: Trading symbol
            
        Returns:
            Dict: Market quote or error
        """
        try:
            if not self.session_token:
                return {"error": "No session token available. Please authenticate first."}

            symbol_up = symbol.upper()

            # Try live cache first
            try:
                await self._ensure_market_socket_started()
                await self._ensure_quote_subscription(symbol_up)
                # Briefly wait for first live tick
                import time
                start_wait = time.time()
                while time.time() - start_wait < 0.5:
                    with self._quote_cache_lock:
                        live = self._quote_cache.get(symbol_up)
                    if live and any(live.get(k) is not None for k in ("bid", "ask", "last")):
                        break
                    time.sleep(0.02)
                with self._quote_cache_lock:
                    live = self._quote_cache.get(symbol_up)
                if live and any(live.get(k) is not None for k in ("bid", "ask", "last")):
                    return {
                        "bid": live.get("bid"),
                        "ask": live.get("ask"),
                        "last": live.get("last"),
                        "volume": live.get("volume"),
                        "ts": live.get("ts"),
                        "source": "signalr"
                    }
            except Exception as live_err:
                logger.debug(f"Live quote not available yet for {symbol_up}: {live_err}")

            # Try REST quote endpoint for bid/ask/last/volume
            try:
                headers = {
                    "accept": "text/plain",
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.session_token}"
                }
                quote_paths = [
                    f"/api/MarketData/quote/{identifier}"
                    for identifier in self._symbol_variants_for_subscription(symbol_up)
                ]
                
                quote_resp = None
                for path in quote_paths:
                    resp = self._make_curl_request("GET", path, headers=headers, suppress_errors=True)
                    if resp and "error" not in resp:
                        quote_resp = resp
                        break
                    if not resp:
                        continue
                    error_text = str(resp.get("error", "")).lower()
                    if "404" in error_text or "not found" in error_text:
                        logger.debug(f"Quote endpoint {path} returned 404, trying alternative identifier")
                        continue
                    # Other errors: use resp and break to surface issue
                    quote_resp = resp
                    break
                
                if quote_resp and "error" not in quote_resp:
                    bid = quote_resp.get("bid") or quote_resp.get("bestBid")
                    ask = quote_resp.get("ask") or quote_resp.get("bestAsk")
                    last = quote_resp.get("last") or quote_resp.get("lastPrice") or quote_resp.get("price")
                    volume = quote_resp.get("volume") or quote_resp.get("totalVolume")
                    if any(v is not None for v in (bid, ask, last, volume)):
                        return {
                            "bid": bid,
                            "ask": ask,
                            "last": last,
                            "volume": volume,
                            "source": "rest_quote"
                        }
                elif quote_resp and quote_resp.get("error"):
                    logger.debug(f"REST quote attempts failed: {quote_resp.get('error')}")
            except Exception as e:
                logger.debug(f"REST quote endpoint not available: {e}")

            # Fallback to recent bars for last price
            from datetime import datetime, timezone, timedelta
            logger.info(f"Fetching fallback bars for {symbol_up}")
            contract_id = self._get_contract_id(symbol_up)
            headers = {
                "accept": "text/plain",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.session_token}"
            }
            now = datetime.now(timezone.utc)
            start_time = now - timedelta(seconds=5)
            bars_request = {
                "contractId": contract_id,
                "live": True,
                "startTime": start_time.isoformat(),
                "endTime": now.isoformat(),
                "unit": 1,
                "unitNumber": 1,
                "limit": 5,
                "includePartialBar": True
            }
            response = self._make_curl_request("POST", "/api/History/retrieveBars", data=bars_request, headers=headers)
            if "error" in response or not response.get("success"):
                # Retry with non-live over a wider window
                from datetime import timedelta as _td
                start_time2 = now - _td(seconds=30)
                bars_request2 = dict(bars_request)
                bars_request2.update({"live": False, "startTime": start_time2.isoformat(), "limit": 30})
                response = self._make_curl_request("POST", "/api/History/retrieveBars", data=bars_request2, headers=headers)
                if "error" in response or not response.get("success"):
                    # Last resort: return any cached last if present
                    with self._quote_cache_lock:
                        live2 = self._quote_cache.get(symbol_up)
                    if live2 and live2.get("last") is not None:
                        return {
                            "last": live2.get("last"),
                            "bid": live2.get("bid"),
                            "ask": live2.get("ask"),
                            "volume": live2.get("volume"),
                            "source": "cache"
                        }
                    return {"error": response.get("error") or response.get("errorMessage") or "Bars request failed"}
            bars = response.get("bars", [])
            if not bars:
                # Same retry logic if empty
                from datetime import timedelta as _td
                start_time2 = now - _td(seconds=30)
                bars_request2 = dict(bars_request)
                bars_request2.update({"live": False, "startTime": start_time2.isoformat(), "limit": 30})
                response = self._make_curl_request("POST", "/api/History/retrieveBars", data=bars_request2, headers=headers)
                bars = response.get("bars", []) if response and response.get("success") else []
                if not bars:
                    with self._quote_cache_lock:
                        live2 = self._quote_cache.get(symbol_up)
                    if live2 and live2.get("last") is not None:
                        return {
                            "last": live2.get("last"),
                            "bid": live2.get("bid"),
                            "ask": live2.get("ask"),
                            "volume": live2.get("volume"),
                            "source": "cache"
                        }
                    return {"error": f"No market data available for {symbol_up}"}
            latest_bar = bars[-1]
            current_price = latest_bar.get("c")
            if current_price is None:
                with self._quote_cache_lock:
                    live2 = self._quote_cache.get(symbol_up)
                if live2 and live2.get("last") is not None:
                    return {
                        "last": live2.get("last"),
                        "bid": live2.get("bid"),
                        "ask": live2.get("ask"),
                        "volume": live2.get("volume"),
                        "source": "cache"
                    }
                return {"error": f"No close price found in latest bar for {symbol_up}"}
            return {
                "last": current_price,
                "source": "bars_fallback",
                "bar_data": latest_bar
            }
        except Exception as e:
            logger.error(f"Failed to fetch market quote: {str(e)}")
            return {"error": str(e)}
    
    async def get_market_depth(self, symbol: str) -> Dict:
        """
        Get market depth (order book) for a symbol using SignalR.
        
        Args:
            symbol: Trading symbol
            
        Returns:
            Dict: Market depth or error
        """
        try:
            if not self.session_token:
                return {"error": "No session token available. Please authenticate first."}
            
            logger.info(f"Fetching market depth for {symbol} via SignalR")
            
            symbol_up = symbol.upper()
            
            # Try to get depth data through SignalR
            try:
                await self._ensure_market_socket_started()
                await self._ensure_depth_subscription(symbol_up)
                
                # Wait for depth data to arrive
                import time
                start_wait = time.time()
                depth_data = None
                
                while time.time() - start_wait < 2.0:  # Wait up to 2 seconds
                    with self._depth_cache_lock:
                        depth_data = self._depth_cache.get(symbol_up)
                    if depth_data and (depth_data.get('bids') or depth_data.get('asks')):
                        break
                    time.sleep(0.05)
                
                if depth_data and (depth_data.get('bids') or depth_data.get('asks')):
                    logger.info(f"Got market depth data via SignalR for {symbol_up}")
                    return {
                        "bids": depth_data.get('bids', []),
                        "asks": depth_data.get('asks', []),
                        "source": "signalr"
                    }
                else:
                    logger.warning(f"No depth data received via SignalR for {symbol_up}")
                    
                    # Try to get basic depth from quote data (bid/ask)
                    try:
                        await self._ensure_quote_subscription(symbol_up)
                        import time
                        time.sleep(0.2)  # Wait a bit longer for quote data
                        
                        with self._quote_cache_lock:
                            quote_data = self._quote_cache.get(symbol_up)
                        
                        if quote_data and quote_data.get('bid') and quote_data.get('ask'):
                            # Create basic depth from bid/ask
                            bid_price = quote_data.get('bid')
                            ask_price = quote_data.get('ask')
                            
                            logger.info(f"Got depth from quote data for {symbol_up}: bid={bid_price}, ask={ask_price}")
                            return {
                                "bids": [{"price": bid_price, "size": 1}],
                                "asks": [{"price": ask_price, "size": 1}],
                                "source": "signalr_quote"
                            }
                        else:
                            logger.debug(f"No quote data available for {symbol_up}: {quote_data}")
                    except Exception as e:
                        logger.debug(f"Could not get depth from quote data: {e}")
                    
            except Exception as e:
                logger.warning(f"SignalR depth failed: {e}")
            
            # Fallback to REST API if SignalR fails
            logger.info(f"Falling back to REST API for market depth: {symbol}")
            
            # Get proper contract ID
            try:
                contract_id = self._get_contract_id(symbol)
            except ValueError as e:
                error_msg = f"Cannot get market depth: {e}. Please fetch contracts first using 'contracts' command."
                logger.error(f"❌ {error_msg}")
                return {"error": error_msg}
            
            headers = {
                "accept": "text/plain",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.session_token}"
            }
            
            # Try different possible endpoints for market depth
            endpoints_to_try = [
                f"/api/MarketData/orderbook/{contract_id}",
                f"/api/MarketData/level2/{contract_id}",
                f"/api/MarketData/depth/{contract_id}",
                f"/api/MarketData/orderbook",
                f"/api/MarketData/depth",
                f"/api/MarketData/level2"
            ]
            
            response = None
            for endpoint in endpoints_to_try:
                try:
                    if endpoint in [f"/api/MarketData/depth", f"/api/MarketData/orderbook", f"/api/MarketData/level2"]:
                        # Try with contract_id as parameter using both GET and POST
                        for method in ["GET", "POST"]:
                            try:
                                response = self._make_curl_request(method, endpoint, headers=headers, data={"contractId": contract_id})
                                if response and "error" not in response and response != {"success": True, "message": "Operation completed successfully"}:
                                    logger.info(f"Successfully got response from endpoint: {endpoint} ({method})")
                                    break
                            except Exception as e:
                                logger.debug(f"Endpoint {endpoint} ({method}) failed: {e}")
                                continue
                        if response and "error" not in response:
                            break
                    else:
                        # Try both GET and POST for specific contract endpoints
                        for method in ["GET", "POST"]:
                            try:
                                response = self._make_curl_request(method, endpoint, headers=headers)
                                if response and "error" not in response and response != {"success": True, "message": "Operation completed successfully"}:
                                    logger.info(f"Successfully got response from endpoint: {endpoint} ({method})")
                                    break
                            except Exception as e:
                                logger.debug(f"Endpoint {endpoint} ({method}) failed: {e}")
                                continue
                        if response and "error" not in response:
                            break
                except Exception as e:
                    logger.debug(f"Endpoint {endpoint} failed: {e}")
                    continue
            
            if not response:
                response = self._make_curl_request("GET", f"/api/MarketData/depth/{contract_id}", headers=headers)
            
            # Debug logging to see actual API response
            logger.info(f"Raw market depth API response: {response}")
            
            if "error" in response:
                logger.error(f"Failed to fetch market depth: {response['error']}")
                return response
            
            # Parse market depth response - check for different possible formats
            if isinstance(response, dict):
                if "bids" in response and "asks" in response:
                    # Direct format with bids/asks
                    return response
                elif "data" in response:
                    # Data wrapped in 'data' field
                    return response["data"]
                elif "result" in response:
                    # Data wrapped in 'result' field
                    return response["result"]
                elif "success" in response and response.get("success") == True:
                    # Success response but no depth data available
                    logger.warning(f"Market depth API returned success but no depth data available for {symbol}")
                    return {"bids": [], "asks": []}
                else:
                    logger.warning(f"Unexpected market depth response format: {response}")
                    return {"bids": [], "asks": []}
            else:
                logger.warning(f"Unexpected market depth response type: {type(response)}")
                return {"bids": [], "asks": []}
            
        except Exception as e:
            logger.error(f"Failed to fetch market depth: {str(e)}")
            return {"error": str(e)}
    
    def _get_cache_key(self, symbol: str, timeframe: str) -> str:
        """Generate a cache key for symbol+timeframe."""
        key_str = f"{symbol.upper()}_{timeframe}"
        return hashlib.md5(key_str.encode()).hexdigest()
    
    def _get_cache_path(self, cache_key: str) -> Path:
        """Get the full path to a cache file."""
        cache_dir = Path(".cache")
        cache_dir.mkdir(exist_ok=True)
        extension = '.parquet' if self._cache_format == 'parquet' else '.pkl'
        return cache_dir / f"history_{cache_key}{extension}"
    
    def _is_market_hours(self, dt: Optional[datetime] = None) -> bool:
        """
        Determine if the given datetime (or current time) is during market hours.
        
        Market hours for futures: 8:00 AM - 10:00 PM ET (13:00-03:00 UTC next day)
        This covers the most volatile trading periods.
        
        Args:
            dt: Optional datetime to check (defaults to now in UTC). If timezone-aware, converts to UTC.
            
        Returns:
            True if during market hours, False otherwise
        """
        if dt is None:
            from datetime import timezone
            dt = datetime.now(timezone.utc).replace(tzinfo=None)  # Get UTC time, remove timezone for consistency
        else:
            # Convert to UTC if timezone-aware
            if dt.tzinfo is not None:
                from datetime import timezone
                if dt.tzinfo != timezone.utc:
                    dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
                else:
                    dt = dt.replace(tzinfo=None)
        
        # Get hour in UTC (0-23)
        hour_utc = dt.hour
        
        # Market hours: 8:00 AM - 10:00 PM ET
        # ET is UTC-5 (EST) or UTC-4 (EDT), so:
        # 8:00 AM ET = 13:00 UTC (EST) or 12:00 UTC (EDT)
        # 10:00 PM ET = 03:00 UTC next day (EST) or 02:00 UTC next day (EDT)
        # For simplicity, we'll use 13:00-03:00 UTC range (covers EST)
        # This wraps around midnight, so we check if hour >= 13 or hour < 3
        return hour_utc >= 13 or hour_utc < 3
    
    def _get_last_market_close(self) -> datetime:
        """
        Get the last market close time (when trading actually stopped).
        
        Futures market schedule (US/Eastern):
        - Trades: Sunday 6:00 PM - Friday 5:00 PM
        - Daily break: 5:00 PM - 6:00 PM (maintenance)
        - Weekend break: Friday 5:00 PM - Sunday 6:00 PM
        
        Returns:
            datetime: Last market close in UTC
        """
        from datetime import timezone, timedelta
        import pytz
        
        et_tz = pytz.timezone('US/Eastern')
        now_et = datetime.now(et_tz)
        
        # Market close times in ET
        daily_close_hour = 17  # 5:00 PM ET
        weekend_open_hour = 18  # Sunday 6:00 PM ET
        
        # Get current day of week (0=Monday, 6=Sunday)
        weekday = now_et.weekday()
        current_hour = now_et.hour
        
        # Weekend (Friday after 5pm - Sunday before 6pm)
        if weekday == 6:  # Sunday
            if current_hour < weekend_open_hour:
                # Before Sunday 6pm - use last Friday 5pm
                days_back = 2
                last_close = now_et.replace(hour=daily_close_hour, minute=0, second=0, microsecond=0) - timedelta(days=days_back)
            else:
                # After Sunday 6pm - market is open, use current time
                return datetime.now(timezone.utc)
        elif weekday == 5:  # Saturday
            # Use last Friday 5pm
            days_back = 1
            last_close = now_et.replace(hour=daily_close_hour, minute=0, second=0, microsecond=0) - timedelta(days=days_back)
        elif weekday == 4 and current_hour >= daily_close_hour:  # Friday after 5pm
            # Use today's 5pm
            last_close = now_et.replace(hour=daily_close_hour, minute=0, second=0, microsecond=0)
        else:
            # During the week - check if in daily break (5pm-6pm)
            if current_hour == daily_close_hour or (current_hour < weekend_open_hour and now_et.hour < 18):
                # In daily break - use today's 5pm or yesterday's 5pm
                if current_hour >= daily_close_hour:
                    last_close = now_et.replace(hour=daily_close_hour, minute=0, second=0, microsecond=0)
                else:
                    # Before 6pm after daily close - use yesterday's close
                    last_close = now_et.replace(hour=daily_close_hour, minute=0, second=0, microsecond=0) - timedelta(days=1)
            else:
                # Market is currently open - use current time
                return datetime.now(timezone.utc)
        
        # Convert to UTC
        last_close_utc = last_close.astimezone(timezone.utc)
        logger.debug(f"Last market close: {last_close} ET = {last_close_utc} UTC")
        return last_close_utc
    
    def _get_cache_ttl_minutes(self) -> int:
        """
        Get cache TTL in minutes based on market hours and environment configuration.
        
        Returns:
            Cache TTL in minutes (2-15 minutes based on market hours)
        """
        try:
            if self._is_market_hours():
                # Market hours - use shorter TTL for high volatility
                ttl = int(os.getenv('CACHE_TTL_MARKET_HOURS', '2'))
            else:
                # Off hours - use longer TTL for low volatility
                ttl = int(os.getenv('CACHE_TTL_OFF_HOURS', '15'))
            
            # Validate TTL is reasonable (1-60 minutes)
            if ttl < 1 or ttl > 60:
                logger.warning(f"Cache TTL {ttl} is outside reasonable range (1-60), using default")
                ttl = int(os.getenv('CACHE_TTL_DEFAULT', '5'))
            
            return ttl
        except (ValueError, TypeError) as e:
            logger.warning(f"Failed to parse cache TTL from environment: {e}, using default")
            return int(os.getenv('CACHE_TTL_DEFAULT', '5'))
    
    def _get_from_memory_cache(self, cache_key: str, max_age_minutes: Optional[int] = None) -> Optional[List[Dict]]:
        """
        Get data from in-memory cache (ultra-fast, <1ms).
        
        Args:
            cache_key: Cache key for the data
            max_age_minutes: Maximum age in minutes. If None, uses dynamic TTL.
            
        Returns:
            Cached data if fresh, None otherwise
        """
        if max_age_minutes is None:
            max_age_minutes = self._get_cache_ttl_minutes()
        
        with self._memory_cache_lock:
            if cache_key not in self._memory_cache:
                return None
            
            data, timestamp = self._memory_cache[cache_key]
            age = datetime.now() - timestamp
            
            if age > timedelta(minutes=max_age_minutes):
                # Expired, remove from cache
                del self._memory_cache[cache_key]
                logger.debug(f"Memory cache expired for {cache_key}")
                return None
            
            # Move to end (LRU - most recently used)
            self._memory_cache.move_to_end(cache_key)
            logger.debug(f"Memory cache hit for {cache_key} ({len(data)} bars, age: {age.total_seconds():.1f}s)")
            return data.copy()  # Return copy to prevent mutation
    
    def _save_to_memory_cache(self, cache_key: str, data: List[Dict]) -> None:
        """Save data to in-memory cache (LRU eviction)."""
        with self._memory_cache_lock:
            # Remove oldest if at capacity
            if len(self._memory_cache) >= self._memory_cache_max_size:
                oldest_key = next(iter(self._memory_cache))
                del self._memory_cache[oldest_key]
                logger.debug(f"Memory cache evicted oldest entry: {oldest_key}")
            
            # Add or update entry
            self._memory_cache[cache_key] = (data.copy(), datetime.now())
            logger.debug(f"Saved {len(data)} bars to memory cache for {cache_key}")
    
    def _load_from_parquet(self, cache_path: Path, max_age_minutes: int) -> Optional[List[Dict]]:
        """Load data from Parquet file."""
        try:
            import polars as pl
            
            # Check file age
            file_age = datetime.now() - datetime.fromtimestamp(cache_path.stat().st_mtime)
            if file_age > timedelta(minutes=max_age_minutes):
                logger.debug(f"Parquet cache expired (age: {file_age.total_seconds()/60:.1f} min)")
                return None
            
            # Read Parquet (very fast!)
            df = pl.read_parquet(cache_path)
            
            # Convert to list of dicts
            cached_data = df.to_dicts()
            
            logger.debug(f"Loaded {len(cached_data)} bars from Parquet cache (age: {file_age.total_seconds()/60:.1f} min)")
            return cached_data
        except ImportError:
            logger.warning("polars not available, cannot use Parquet cache")
            return None
        except Exception as e:
            logger.warning(f"Failed to load Parquet cache: {e}")
            return None
    
    def _save_to_parquet(self, cache_path: Path, data: List[Dict]) -> None:
        """Save data to Parquet file."""
        try:
            import polars as pl
            
            # Convert to Polars DataFrame
            df = pl.DataFrame(data)
            
            # Save to Parquet with compression
            df.write_parquet(cache_path, compression='lz4')
            
            logger.debug(f"Cached {len(data)} bars to Parquet: {cache_path}")
        except ImportError:
            logger.warning("polars not available, cannot use Parquet cache")
            raise
        except Exception as e:
            logger.warning(f"Failed to save Parquet cache: {e}")
            raise
    
    def _load_from_pickle(self, cache_path: Path, max_age_minutes: int) -> Optional[List[Dict]]:
        """Load data from pickle file (fallback)."""
        try:
            # Check file age
            file_age = datetime.now() - datetime.fromtimestamp(cache_path.stat().st_mtime)
            if file_age > timedelta(minutes=max_age_minutes):
                logger.debug(f"Pickle cache expired (age: {file_age.total_seconds()/60:.1f} min)")
                return None
            
            # Load from pickle
            with open(cache_path, 'rb') as f:
                cached_data = pickle.load(f)
            
            logger.debug(f"Loaded {len(cached_data)} bars from pickle cache (age: {file_age.total_seconds()/60:.1f} min)")
            return cached_data
        except Exception as e:
            logger.warning(f"Failed to load pickle cache: {e}")
            return None
    
    def _save_to_pickle(self, cache_path: Path, data: List[Dict]) -> None:
        """Save data to pickle file (fallback)."""
        try:
            with open(cache_path, 'wb') as f:
                pickle.dump(data, f)
            logger.debug(f"Cached {len(data)} bars to pickle: {cache_path}")
        except Exception as e:
            logger.warning(f"Failed to save pickle cache: {e}")
            raise
    
    def _load_from_cache(self, cache_key: str, max_age_minutes: Optional[int] = None) -> Optional[List[Dict]]:
        """
        Load historical data from cache (memory -> Parquet/Pickle -> None).
        
        Uses a 3-tier cache strategy:
        1. Memory cache (ultra-fast, <1ms)
        2. Parquet/Pickle file (fast, 15-50ms)
        3. API call (slow, 500-2000ms)
        
        Uses dynamic TTL based on market hours if max_age_minutes is None.
        
        Args:
            cache_key: Cache key for the data
            max_age_minutes: Maximum age of cache in minutes. If None, uses dynamic TTL.
            
        Returns:
            Cached data if fresh, None otherwise
        """
        # Use dynamic TTL if not specified
        if max_age_minutes is None:
            max_age_minutes = self._get_cache_ttl_minutes()
            logger.debug(f"Using dynamic cache TTL: {max_age_minutes} minutes (market hours: {self._is_market_hours()})")
        
        # Tier 1: Check memory cache (ultra-fast)
        memory_data = self._get_from_memory_cache(cache_key, max_age_minutes)
        if memory_data is not None:
            return memory_data
        
        # Tier 2: Check file cache
        cache_path = self._get_cache_path(cache_key)
        if not cache_path.exists():
            return None
        
        try:
            # Load from file (Parquet or Pickle)
            if self._cache_format == 'parquet':
                file_data = self._load_from_parquet(cache_path, max_age_minutes)
                # If Parquet fails and pickle file exists, try pickle
                if file_data is None and cache_path.with_suffix('.pkl').exists():
                    logger.debug(f"Parquet cache not available, trying pickle fallback")
                    file_data = self._load_from_pickle(cache_path.with_suffix('.pkl'), max_age_minutes)
            else:
                file_data = self._load_from_pickle(cache_path, max_age_minutes)
            
            if file_data is not None:
                # Store in memory cache for next time (promote to Tier 1)
                self._save_to_memory_cache(cache_key, file_data)
                return file_data
            
            return None
        except Exception as e:
            logger.warning(f"Failed to load cache for {cache_key}: {e}")
            return None
    
    def _save_to_cache(self, cache_key: str, data: List[Dict]) -> None:
        """
        Save historical data to cache (both memory and file).
        
        Args:
            cache_key: Cache key for the data
            data: List of bar dictionaries to cache
        """
        try:
            # Save to memory cache (Tier 1)
            self._save_to_memory_cache(cache_key, data)
            
            # Save to file cache (Tier 2)
            cache_path = self._get_cache_path(cache_key)
            if self._cache_format == 'parquet':
                try:
                    self._save_to_parquet(cache_path, data)
                except ImportError:
                    # Fallback to pickle if polars not available
                    logger.warning("polars not available, falling back to pickle cache")
                    pickle_path = cache_path.with_suffix('.pkl')
                    self._save_to_pickle(pickle_path, data)
            else:
                self._save_to_pickle(cache_path, data)
            
            logger.debug(f"Cached {len(data)} bars to {self._cache_format} cache for {cache_key}")
        except Exception as e:
            logger.warning(f"Failed to save cache for {cache_key}: {e}")
    
    def _export_to_csv(self, data: List[Dict], symbol: str, timeframe: str) -> Optional[str]:
        """
        Export historical data to CSV file.
        
        Args:
            data: List of bar dictionaries with OHLCV data
            symbol: Trading symbol (e.g., "MNQ")
            timeframe: Timeframe (e.g., "5m")
            
        Returns:
            Filename of created CSV file, or None on error
        """
        if not data:
            logger.warning("No data to export to CSV")
            return None
        
        try:
            # Create filename with timestamp
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{symbol.upper()}_{timeframe}_{timestamp}.csv"
            
            # Write CSV file
            with open(filename, 'w', newline='', encoding='utf-8') as csvfile:
                fieldnames = ['Time', 'Open', 'High', 'Low', 'Close', 'Volume']
                writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                
                writer.writeheader()
                for bar in data:
                    writer.writerow({
                        'Time': bar.get('time', bar.get('timestamp', 'N/A')),
                        'Open': bar.get('open', 0),
                        'High': bar.get('high', 0),
                        'Low': bar.get('low', 0),
                        'Close': bar.get('close', 0),
                        'Volume': bar.get('volume', 0)
                    })
            
            logger.info(f"Exported {len(data)} bars to {filename}")
            return filename
        except Exception as e:
            logger.error(f"Failed to export CSV: {e}")
            return None
    
    def _parse_timeframe(self, timeframe: str):
        """
        Parse timeframe string to API unit format.
        
        Per TopStepX API documentation: https://gateway.docs.projectx.com/docs/api-reference/market-data/retrieve-bars/
        
        API Units (CORRECT mapping):
        - 1 = Second
        - 2 = Minute
        - 3 = Hour
        - 4 = Day
        - 5 = Week
        - 6 = Month
        
        Args:
            timeframe: String like "1s", "5m", "1h", "4h", "1d", "1w", "1M"
            
        Returns:
            Tuple[int, int, Callable]: (unit, unitNumber, time_delta_function)
                - unit: API unit type (1=Second, 2=Minute, 3=Hour, 4=Day, 5=Week, 6=Month)
                - unitNumber: Number of units
                - time_delta_function: Function to calculate timedelta for the given bar count
        """
        import re
        from datetime import timedelta
        
        # Parse timeframe string (e.g., "5m" -> number=5, unit="m")
        match = re.match(r'^(\d+)([smhdwM])$', timeframe)
        if not match:
            logger.error(f"Invalid timeframe format: {timeframe}. Use format like: 1s, 5m, 15m, 1h, 4h, 1d, 1w, 1M")
            return None, None, None
        
        number = int(match.group(1))
        unit_char = match.group(2)
        
        # Map to API units per official documentation
        # API units: 1=Second, 2=Minute, 3=Hour, 4=Day, 5=Week, 6=Month
        if unit_char == 's':
            # Seconds - unit=1
            api_unit = 1
            api_unit_number = number
            time_delta_func = lambda bars: timedelta(seconds=number * bars)
        elif unit_char == 'm':
            # Minutes - unit=2
            api_unit = 2
            api_unit_number = number
            time_delta_func = lambda bars: timedelta(minutes=number * bars)
        elif unit_char == 'h':
            # Hours - unit=3
            api_unit = 3
            api_unit_number = number
            time_delta_func = lambda bars: timedelta(hours=number * bars)
        elif unit_char == 'd':
            # Days - unit=4
            api_unit = 4
            api_unit_number = number
            time_delta_func = lambda bars: timedelta(days=number * bars)
        elif unit_char == 'w':
            # Weeks - unit=5
            api_unit = 5
            api_unit_number = number
            time_delta_func = lambda bars: timedelta(weeks=number * bars)
        elif unit_char == 'M':
            # Months - unit=6 (approximate as 30 days for time calculation)
            api_unit = 6
            api_unit_number = number
            time_delta_func = lambda bars: timedelta(days=30 * number * bars)
        else:
            logger.error(f"Unknown timeframe unit: {unit_char}")
            return None, None, None
        
        logger.debug(f"Parsed timeframe '{timeframe}' -> unit={api_unit}, unitNumber={api_unit_number}")
        return api_unit, api_unit_number, time_delta_func
    
    def _aggregate_bars(self, bars: List[Dict], target_timeframe: str) -> List[Dict]:
        """
        Aggregate 1-minute bars into higher timeframes (5m, 15m, 30m, 1h, etc.).
        
        This ensures accurate data by using reliable 1m data as the source.
        
        Args:
            bars: List of 1-minute bars (must be sorted by timestamp)
            target_timeframe: Target timeframe to aggregate to (e.g., '5m', '15m', '1h')
            
        Returns:
            List[Dict]: Aggregated bars in the target timeframe
        """
        if not bars:
            return []
        
        # Parse target timeframe
        target_seconds = self._parse_timeframe_to_seconds(target_timeframe)
        if target_seconds is None or target_seconds <= 60:
            # Can't aggregate to same or lower timeframe
            return bars
        
        # Group 1m bars into target timeframe periods
        aggregated = []
        current_group = []
        current_group_start = None
        
        for bar in bars:
            # Get timestamp
            ts_str = bar.get('timestamp') or bar.get('time')
            if isinstance(ts_str, str):
                try:
                    ts = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
                except:
                    continue
            elif isinstance(ts_str, datetime):
                ts = ts_str
            else:
                continue
            
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            
            # Calculate which target timeframe bar this belongs to
            bar_start_seconds = int(ts.timestamp()) // target_seconds * target_seconds
            bar_start = datetime.fromtimestamp(bar_start_seconds, tz=timezone.utc)
            
            # Start new group if needed
            if current_group_start is None or bar_start != current_group_start:
                # Finalize previous group
                if current_group:
                    agg_bar = {
                        'timestamp': current_group_start.isoformat(),
                        'time': current_group_start.isoformat(),
                        'open': current_group[0].get('open', 0),
                        'high': max(b.get('high', 0) for b in current_group),
                        'low': min(b.get('low', float('inf')) for b in current_group if b.get('low') is not None),
                        'close': current_group[-1].get('close', 0),
                        'volume': sum(b.get('volume', 0) or 0 for b in current_group),
                    }
                    # Ensure low is valid
                    if agg_bar['low'] == float('inf'):
                        agg_bar['low'] = agg_bar['open']
                    aggregated.append(agg_bar)
                
                # Start new group
                current_group = [bar]
                current_group_start = bar_start
            else:
                # Add to current group
                current_group.append(bar)
        
        # Finalize last group
        if current_group:
            agg_bar = {
                'timestamp': current_group_start.isoformat(),
                'time': current_group_start.isoformat(),
                'open': current_group[0].get('open', 0),
                'high': max(b.get('high', 0) for b in current_group),
                'low': min(b.get('low', float('inf')) for b in current_group if b.get('low') is not None),
                'close': current_group[-1].get('close', 0),
                'volume': sum(b.get('volume', 0) or 0 for b in current_group),
            }
            if agg_bar['low'] == float('inf'):
                agg_bar['low'] = agg_bar['open']
            aggregated.append(agg_bar)
        
        return aggregated
    
    def _parse_timeframe_to_seconds(self, timeframe: str) -> Optional[int]:
        """Parse timeframe string to seconds."""
        timeframe = timeframe.strip().lower()
        
        if timeframe.endswith('s'):
            return int(timeframe[:-1])
        elif timeframe.endswith('m'):
            return int(timeframe[:-1]) * 60
        elif timeframe.endswith('h'):
            return int(timeframe[:-1]) * 3600
        elif timeframe.endswith('d'):
            return int(timeframe[:-1]) * 86400
        elif timeframe.endswith('w'):
            return int(timeframe[:-1]) * 604800
        elif timeframe.endswith('M'):
            # Approximate month as 30 days
            return int(timeframe[:-1]) * 2592000
        else:
            return None
    
    async def get_historical_data(self, symbol: str, timeframe: str = "1m",
                                  limit: int = 100, start_time: datetime = None,
                                  end_time: datetime = None) -> List[Dict]:
        """
        Get historical price data for a symbol.

        This is now a thin wrapper around the canonical implementation in
        `TopStepXAdapter.get_historical_data`, so ALL paths (CLI, dashboard,
        strategies) share one source of truth for historical bar logic.
        """
        try:
            logger.info(f"Fetching historical data for {symbol} ({timeframe}, {limit} bars)")

            # Delegate to adapter (canonical implementation)
            bars = await self.broker_adapter.get_historical_data(
                symbol=symbol,
                timeframe=timeframe,
                limit=limit,
                start_time=start_time,
                end_time=end_time,
            )

            # Convert Bar objects to normalized dicts for backward compatibility.
            # IMPORTANT: We do NOT pass through raw_data here, because the legacy
            # printing layer expects keys: timestamp/time/open/high/low/close/volume.
            result: List[Dict] = []
            for bar in bars:
                result.append(
                    {
                        "timestamp": bar.timestamp.isoformat() if bar.timestamp else None,
                        "time": bar.timestamp.isoformat() if bar.timestamp else None,
                        "open": bar.open,
                        "high": bar.high,
                        "low": bar.low,
                        "close": bar.close,
                        "volume": bar.volume,
                        "symbol": getattr(bar, "symbol", None) or symbol,
                    }
                )

            if result:
                # Debug: log last bar vs current time to help diagnose stale data
                from datetime import datetime, timezone as _tz

                last_ts = result[-1].get("timestamp") or result[-1].get("time")
                logger.info(f"📊 get_historical_data last bar timestamp (ISO) = {last_ts}")
                logger.info(f"📊 get_historical_data now UTC                    = {datetime.now(_tz.utc)}")

                logger.info(f"✅ Retrieved {len(result)} bars from adapter (canonical implementation)")
            else:
                logger.warning("get_historical_data: adapter returned no bars")

            return result

        except Exception as e:
            logger.error(f"Failed to fetch historical data via adapter: {e}")
            import traceback

            logger.debug(f"Error traceback: {traceback.format_exc()}")
            return []
    
    def _start_prefetch_task(self) -> None:
        """Start background task to prefetch common symbols/timeframes."""
        if self._prefetch_task is not None:
            return  # Already started
        
        async def prefetch_worker():
            """Background worker to prefetch historical data."""
            # Wait a bit after startup to let cache initialize
            await asyncio.sleep(5)
            
            while True:
                try:
                    # Only prefetch if cache is initialized
                    if sdk_adapter and sdk_adapter.is_cache_initialized():
                        for symbol in self._prefetch_symbols:
                            for timeframe in self._prefetch_timeframes:
                                try:
                                    # Prefetch with small limit (just to warm cache)
                                    await self.get_historical_data(symbol, timeframe, limit=20)
                                    logger.debug(f"Prefetched {symbol} {timeframe}")
                                except Exception as e:
                                    logger.debug(f"Prefetch failed for {symbol} {timeframe}: {e}")
                    
                    # Wait before next prefetch cycle (5 minutes)
                    await asyncio.sleep(300)
                except Exception as e:
                    logger.warning(f"Prefetch worker error: {e}")
                    await asyncio.sleep(60)
        
        self._prefetch_task = asyncio.create_task(prefetch_worker())
        logger.info(f"Started prefetch task for {len(self._prefetch_symbols)} symbols × {len(self._prefetch_timeframes)} timeframes")
    
    async def get_positions_and_orders_batch(self, account_id: str = None) -> Dict:
        """
        Batch API call to get both positions and orders in a single operation.
        Reduces API round-trips by 50% when both are needed.
        
        Args:
            account_id: Account ID (uses selected account if not provided)
            
        Returns:
            Dict with 'positions' and 'orders' keys
        """
        try:
            target_account = account_id or (self.selected_account['id'] if self.selected_account else None)
            
            if not target_account:
                return {"positions": [], "orders": [], "error": "No account selected"}
            
            if not self.session_token:
                return {"positions": [], "orders": [], "error": "No session token"}
            
            # Run both API calls in parallel
            positions_task = asyncio.create_task(self.get_open_positions(target_account))
            orders_task = asyncio.create_task(self.get_open_orders(target_account))
            
            positions, orders = await asyncio.gather(positions_task, orders_task)
            
            return {
                "positions": positions,
                "orders": orders
            }
        except Exception as e:
            logger.error(f"Batch API call failed: {e}")
            return {"positions": [], "orders": [], "error": str(e)}
    
    def _has_active_orders_or_positions(self, account_id: str = None) -> bool:
        """
        Quick check if there are active orders or positions.
        Uses cached data to avoid API calls.
        
        Args:
            account_id: Account ID
            
        Returns:
            True if there are active orders/positions, False otherwise
        """
        target_account = account_id or (self.selected_account['id'] if self.selected_account else None)
        if not target_account:
            return False
        
        # Check cached order/position IDs
        account_str = str(target_account)
        has_orders = bool(self._cached_order_ids.get(account_str, {}))
        has_positions = bool(self._cached_position_ids.get(account_str, {}))
        
        return has_orders or has_positions
    
    def _update_order_activity(self) -> None:
        """Update timestamp of last order activity."""
        self._last_order_activity = datetime.now()
    
    async def _auto_fill_checker(self) -> None:
        """
        Adaptive background task to automatically check for fills.
        Adjusts check interval based on activity:
        - Active interval (10s): When orders exist
        - Idle interval (30s): When no orders/positions
        """
        self._auto_fills_enabled = True
        
        while self._auto_fills_enabled:
            try:
                # Determine check interval based on activity
                has_activity = self._has_active_orders_or_positions()
                
                # Use shorter interval if orders exist or recent activity
                if has_activity:
                    interval = self._fill_check_active_interval
                elif self._last_order_activity:
                    # Recent activity within last 5 minutes
                    time_since_activity = (datetime.now() - self._last_order_activity).total_seconds()
                    if time_since_activity < 300:  # 5 minutes
                        interval = self._fill_check_active_interval
                    else:
                        interval = self._fill_check_interval
                else:
                    interval = self._fill_check_interval
                
                # Perform fill check
                await self.check_order_fills()
                
                # Wait before next check (adaptive interval)
                await asyncio.sleep(interval)
                
            except Exception as e:
                logger.error(f"Auto fill checker error: {e}")
                await asyncio.sleep(self._fill_check_interval)
    
    async def _eod_scheduler(self) -> None:
        """
        Background task to update account tracker at end of day (midnight UTC).
        Updates highest EOD balance for trailing drawdown calculations.
        """
        import asyncio
        from datetime import datetime, time as dt_time, timedelta, timezone
        
        logger.info("EOD scheduler started - will update balance at midnight UTC")
        
        while True:
            try:
                # Calculate time until next midnight UTC
                now = datetime.now(timezone.utc)
                # Create timezone-aware midnight datetime
                tomorrow = now.date() + timedelta(days=1)
                midnight = datetime.combine(tomorrow, dt_time.min, tzinfo=timezone.utc)
                seconds_until_midnight = (midnight - now).total_seconds()
                
                logger.debug(f"EOD scheduler: Next update in {seconds_until_midnight/3600:.1f} hours")
                
                # Wait until midnight
                await asyncio.sleep(seconds_until_midnight)
                
                # Update EOD balance if account is selected
                if self.selected_account:
                    account_id = self.selected_account['id']
                    balance = await self.get_account_balance(account_id)
                    
                    if balance:
                        self.account_tracker.update_eod_balance(balance)
                        logger.info(f"EOD balance updated: ${balance:,.2f}")
                        print(f"\n💰 End-of-Day balance updated: ${balance:,.2f}")
                    else:
                        logger.warning("Could not fetch balance for EOD update")
                
            except Exception as e:
                logger.error(f"EOD scheduler error: {e}")
                # On error, wait 1 hour before retrying
                await asyncio.sleep(3600)
    
    async def run(self):
        """
        Main bot execution flow with parallel initialization and performance timing.
        
        Uses parallel execution for independent operations to reduce startup time by 30-50%.
        """
        import time as _t
        
        try:
            print("🤖 TopStepX Trading Bot - Real API Version")
            print("="*50)
            
            # Step 1: Authenticate (must be first - required for all other operations)
            _total_start = _t.time()
            _auth_start = _t.time()
            if not await self.authenticate():
                print("❌ Authentication failed. Please check your API key.")
                return
            _auth_ms = int((_t.time() - _auth_start) * 1000)
            print(f"✅ Authentication successful! ({_auth_ms} ms)")
            
            # Step 2: Parallel initialization of independent operations
            # These can all run concurrently after authentication
            print("\n⚡ Initializing in parallel...")
            _parallel_start = _t.time()
            
            # Create tasks for parallel execution
            # Note: Cache initialization is now LAZY (done on first history command)
            accounts_task = asyncio.create_task(self.list_accounts())
            # Ensure contracts are cached (use_cache=True by default)
            contracts_task = asyncio.create_task(self.get_available_contracts(use_cache=True))
            
            # Wait for all parallel tasks to complete
            accounts_result = await accounts_task
            contracts_result = await contracts_task
            
            # Verify contracts were cached
            with self._contract_cache_lock:
                if self._contract_cache is None:
                    logger.warning("⚠️  Contracts fetched but cache is empty - this should not happen")
                else:
                    logger.debug(f"✅ Contract cache verified: {len(self._contract_cache['contracts'])} contracts cached")
            
            _parallel_ms = int((_t.time() - _parallel_start) * 1000)
            
            # Step 3: Display accounts
            accounts = accounts_result
            if not accounts:
                print("❌ No active accounts found.")
                return
            self.display_accounts(accounts)
            print(f"   (Parallel init: {_parallel_ms} ms)")
            
            # Step 4: Select account (interactive, no timer needed)
            selected_account = self.select_account(accounts)
            if not selected_account:
                print("❌ No account selected. Exiting.")
                return
            
            # Step 5: Show account details (requires selected account)
            _balance_start = _t.time()
            balance = await self.get_account_balance()
            _balance_ms = int((_t.time() - _balance_start) * 1000)
            if balance is not None:
                print(f"\n💰 Current Balance: ${balance:,.2f} ({_balance_ms} ms)")
            
            # Step 6: Display contracts (already fetched in parallel)
            contracts = contracts_result
            if contracts:
                print(f"\n📋 Available Contracts: {len(contracts)} found")
                for contract in contracts[:5]:  # Show first 5
                    symbol = contract.get('name', 'Unknown')
                    description = contract.get('description', 'No description')
                    desc_short = description[:50] + "..." if len(description) > 50 else description
                    print(f"  - {symbol:8s} {desc_short}")
                if len(contracts) > 5:
                    print(f"  ... and {len(contracts) - 5} more (use 'contracts' command to see all)")
            
            # Step 7: Cache initialization is now LAZY (initialized on first history command)
            # This saves ~10s at startup and only initializes when actually needed
            use_sdk = os.getenv("USE_PROJECTX_SDK", "0").lower() in ("1", "true", "yes")
            if use_sdk and sdk_adapter is not None and sdk_adapter.is_sdk_available():
                print(f"\n💡 Historical data cache will initialize on first use (lazy loading)")
            
            _total_ms = int((_t.time() - _total_start) * 1000)
            print(f"\n🚀 Total initialization time: {_total_ms} ms")
            
            # Step 8: Start background prefetch (if enabled)
            if self._prefetch_enabled:
                self._start_prefetch_task()
            
            # Step 8b: Start EOD scheduler for account tracking
            asyncio.create_task(self._eod_scheduler())
            logger.info("EOD scheduler background task started")
            
            # Step 9: Auto-start enabled strategies (if strategy manager available)
            if hasattr(self, 'strategy_manager'):
                logger.info("💾 Loading persisted strategy states for CLI session...")
                await self.strategy_manager.apply_persisted_states()
                logger.info("🚀 Auto-starting enabled strategies for CLI session...")
                await self.strategy_manager.auto_start_enabled_strategies()
                logger.info("✅ Strategy initialization complete for CLI session")
            
            # Step 10: Trading interface
            print(f"\n🎯 Ready to trade on account: {selected_account['name']}")
            print("\n" + "="*70)
            print("📋 QUICK REFERENCE - Most Useful Commands")
            print("="*70)
            print("📊 Market Data:")
            print("   quote <symbol>              - Get real-time market quote")
            print("   depth <symbol>              - Get market depth (order book)")
            print("   history <symbol> [tf] [n]   - Get historical bars (e.g., history MNQ 5m 100)")
            print("   contracts                   - List all available trading contracts")
            print()
            print("💰 Account & Risk:")
            print("   account_info                - Detailed account information")
            print("   account_state               - Real-time account state (balance, PnL)")
            print("   compliance                   - Check compliance status (DLL, MLL, drawdown)")
            print("   risk                        - Show current risk metrics and limits")
            print("   trades [start] [end]        - List trades with FIFO consolidation")
            print()
            print("📈 Trading Orders:")
            print("   trade <sym> <side> <qty>    - Place market order (e.g., trade MNQ BUY 1)")
            print("   limit <sym> <side> <qty> <price> - Place limit order")
            print("   bracket <sym> <side> <qty> <stop_ticks> <tp_ticks> - Bracket order")
            print("   stop_bracket <sym> <side> <qty> <entry> <stop> <tp> - Stop entry bracket")
            print("   stop_buy <sym> <qty> <price> - Stop buy order")
            print("   stop_sell <sym> <qty> <price> - Stop sell order")
            print("   trail <sym> <side> <qty> <trail> - Trailing stop order")
            print()
            print("📦 Position Management:")
            print("   positions                   - Show all open positions")
            print("   orders                      - Show all open orders")
            print("   close <pos_id> [qty]        - Close position (entire or partial)")
            print("   cancel <order_id>           - Cancel an order")
            print("   modify <order_id> <qty> [price] - Modify order")
            print("   modify_stop <pos_id> <price> - Modify stop loss")
            print("   modify_tp <pos_id> <price>  - Modify take profit")
            print("   flatten                     - Close all positions and cancel all orders")
            print()
            print("🔄 Monitoring & Automation:")
            print("   monitor                     - Monitor position changes, adjust brackets")
            print("   bracket_monitor             - Monitor bracket positions, manage orders")
            print("   auto_fills                  - Enable automatic fill checking")
            print("   check_fills                 - Manually check for filled orders")
            print()
            print("🎯 Strategy Management:")
            print("   strategies list             - List all available strategies")
            print("   strategies status           - Show all strategies status")
            print("   strategies start <name>    - Start a specific strategy")
            print("   strategies stop <name>     - Stop a specific strategy")
            print()
            print("⚙️  System:")
            print("   accounts                    - List all trading accounts")
            print("   switch_account [id]         - Switch to different account")
            print("   metrics                     - Show performance metrics and system stats")
            print("   help                        - Show detailed help for all commands")
            print("   quit                        - Exit trading interface")
            print("="*70)
            print("💡 Use ↑/↓ arrows for command history, Tab for completion")
            print("💡 Type 'help' for detailed information on any command")
            print("="*70)
            try:
                await self.trading_interface()
            finally:
                # Cleanup: Shutdown SDK cache on exit
                if use_sdk and sdk_adapter is not None and sdk_adapter.is_cache_initialized():
                    logger.info("Shutting down historical client cache...")
                    await sdk_adapter.shutdown_historical_client_cache()
            
        except Exception as e:
            logger.error(f"Bot execution failed: {str(e)}")
            print(f"❌ Bot execution failed: {str(e)}")
        finally:
            # Ensure cache is cleaned up even on error
            if sdk_adapter is not None and sdk_adapter.is_cache_initialized():
                try:
                    await sdk_adapter.shutdown_historical_client_cache()
                except Exception:
                    pass
    
    def _setup_readline(self):
        """
        Set up readline for command history and arrow key navigation.
        """
        # Set up history file
        history_file = os.path.expanduser("~/.topstepx_trading_history")
        
        # Load existing history
        try:
            readline.read_history_file(history_file)
        except FileNotFoundError:
            pass
        
        # Set history length
        readline.set_history_length(100)
        
        # Set up tab completion for commands
        def completer(text, state):
            # Get current line to understand context
            line = readline.get_line_buffer()
            words = line.split()
            
            # If we're completing the first word (command)
            if len(words) == 1:
                commands = [
                    "trade", "limit", "bracket", "native_bracket", "stop", "stop_buy", "stop_sell", "trail",
                    "positions", "orders", "close", "cancel", "modify", "modify_stop", "modify_tp", 
                    "quote", "depth", "history", "monitor", "bracket_monitor", "account_info", "flatten", 
                    "contracts", "accounts", "help", "quit"
                ]
                matches = [cmd for cmd in commands if cmd.startswith(text.lower())]
                if state < len(matches):
                    return matches[state]
            
            # If we're completing after a command, suggest common symbols
            elif len(words) >= 2 and words[0] in ["trade", "limit", "bracket", "native_bracket", "stop", "stop_buy", "stop_sell", "trail", "quote", "depth", "history"]:
                symbols = ["MNQ", "MES", "MYM", "MGC", "ES", "NQ", "YM", "GC"]
                matches = [sym for sym in symbols if sym.lower().startswith(text.lower())]
                if state < len(matches):
                    return matches[state]
            
            # If we're completing after symbol, suggest sides
            elif len(words) >= 3 and words[1].upper() in ["MNQ", "MES", "MYM", "MGC", "ES", "NQ", "YM", "GC"]:
                sides = ["buy", "sell"]
                matches = [side for side in sides if side.startswith(text.lower())]
                if state < len(matches):
                    return matches[state]
            
            return None
        
        readline.set_completer(completer)
        readline.parse_and_bind("tab: complete")
        
        # Save history on exit
        def save_history():
            try:
                readline.write_history_file(history_file)
            except:
                pass
        
        import atexit
        atexit.register(save_history)

    async def _async_input(self, prompt: str = "") -> str:
        """Non-blocking input helper so background tasks remain active."""
        return await asyncio.to_thread(input, prompt)
    
    async def trading_interface(self):
        """
        Interactive trading interface with command history.
        """
        # Set up readline for command history
        self._setup_readline()
        
        print("\n" + "="*50)
        print("TRADING INTERFACE")
        print("="*50)
        print("Commands:")
        print("  trade <symbol> <side> <quantity> - Place market order")
        print("  limit <symbol> <side> <quantity> <price> - Place limit order")
        print("  bracket <symbol> <side> <quantity> <stop_ticks> <profit_ticks> - Place bracket order")
        print("  native_bracket <symbol> <side> <quantity> <stop_price> <profit_price> - Native bracket order")
        print("  stop <symbol> <side> <quantity> <price> - Place stop order")
        print("  trail <symbol> <side> <quantity> <trail_amount> - Place trailing stop")
        print("  positions - Show open positions")
        print("  metrics - Show performance metrics and system stats")
        print("  orders - Show open orders")
        print("  close <position_id> [quantity] - Close position")
        print("  cancel <order_id> - Cancel order")
        print("  modify <order_id> <new_quantity> [new_price] - Modify order")
        print("  quote <symbol> - Get market quote")
        print("  depth <symbol> - Get market depth")
        print("  history <symbol> [timeframe] [limit] [raw] [csv] - Get historical data")
        print("    Add 'raw' for fast tab-separated output (e.g., history MNQ 5m 20 raw)")
        print("    Add 'csv' to export data to CSV file (e.g., history MNQ 5m 20 csv)")
        print("  chart [symbol] [timeframe] [limit] - Open chart window GUI")
        print("    Example: chart MNQ 5m 100")
        print("  monitor - Monitor position changes and adjust bracket orders")
        print("  bracket_monitor - Monitor bracket positions and manage orders")
        print("  activate_monitor - Manually activate monitoring for testing")
        print("  deactivate_monitor - Manually deactivate monitoring")
        print("  check_fills - Check for filled orders and send Discord notifications")
        print("  test_fills - Test fill checking with detailed output")
        print("  clear_notifications - Clear notification cache to re-check all orders")
        print("  auto_fills - Enable automatic fill checking every 30 seconds")
        print("  stop_auto_fills - Disable automatic fill checking")
        print("  account_info - Get detailed account information")
        print("  account_state - Show real-time account state (balance, PnL, positions)")
        print("  compliance - Check account compliance status (DLL, MLL, trailing drawdown)")
        print("  risk - Show current risk metrics and limits")
        print("  drawdown - Show max loss limit and drawdown information (also: max_loss, risk)")
        print("  switch_account [account_id] - Switch to a different account without restarting")
        print("  trades [start_date] [end_date] - List trades between dates (default: current session)")
        print("  flatten - Close all positions and cancel all orders")
        print("  contracts - List available contracts")
        print("  accounts - List accounts again")
        print("  strategy_start [symbols] - Start overnight range breakout strategy (default)")
        print("  strategy_stop - Stop the overnight strategy")
        print("  strategy_status - Show overnight strategy status and configuration")
        print("  strategy_test <symbol> - Test strategy components (ATR, ranges, orders)")
        print("  strategy_execute [symbols] - Manually trigger market open sequence (for testing)")
        print("  ")
        print("  📦 Modular Strategy System:")
        print("  strategies list - List all available strategies")
        print("  strategies status - Show all strategies status")
        print("  strategies start <name> [symbols] - Start a specific strategy")
        print("  strategies stop <name> - Stop a specific strategy")
        print("  strategies start_all - Start all enabled strategies")
        print("  strategies stop_all - Stop all strategies")
        print("  ")
        print("  help - Show this help message")
        print("  quit - Exit trading interface")
        print("="*50)
        print("💡 Use ↑/↓ arrows to navigate command history, Tab for completion")
        print("="*50)
        print("💡 Use 'auto_fills' command to enable automatic fill checking if needed")
        print("="*50)
        
        while True:
            try:
                command = (await self._async_input("\nEnter command: ")).strip()
                
                # Convert to lowercase for processing but keep original for history
                command_lower = command.lower()
                
                if command_lower == "quit" or command_lower == "q":
                    print("👋 Exiting trading interface.")
                    break
                elif command_lower == "flatten":
                    await self.flatten_all_positions()
                elif command_lower == "contracts":
                    contracts = await self.get_available_contracts()
                    if contracts:
                        print(f"\n📋 Available Contracts ({len(contracts)}):")
                        for contract in contracts:
                            # API returns: name (symbol), description (full name), tickSize, tickValue, activeContract
                            symbol = contract.get('name', 'Unknown')
                            description = contract.get('description', 'No description')
                            tick_size = contract.get('tickSize')
                            tick_value = contract.get('tickValue')
                            active = contract.get('activeContract', False)
                            
                            # Format display with symbol and description
                            status = "✓" if active else "○"
                            desc_short = description[:60] + "..." if len(description) > 60 else description
                            print(f"  {status} {symbol:8s} - {desc_short}")
                            
                            # Show tick info if available
                            if tick_size and tick_value:
                                print(f"    {'':8s}   Tick: ${tick_size:.4f} = ${tick_value:.2f}")
                    else:
                        print("❌ No contracts available")
                elif command_lower == "accounts":
                    accounts = await self.list_accounts()
                    self.display_accounts(accounts)
                
                elif command_lower == "contracts":
                    print("📋 Fetching available contracts...")
                    contracts = await self.get_available_contracts(use_cache=False)
                    if contracts:
                        print(f"\n✅ Found {len(contracts)} available contracts:\n")
                        # Group by symbol prefix
                        by_symbol = {}
                        for c in contracts:
                            symbol = c.get('symbol') or c.get('Symbol') or 'Unknown'
                            contract_id = c.get('contractId') or c.get('ContractId') or c.get('id') or 'Unknown'
                            description = c.get('description') or c.get('Description') or ''
                            
                            if symbol not in by_symbol:
                                by_symbol[symbol] = []
                            by_symbol[symbol].append({'id': contract_id, 'desc': description})
                        
                        # Display grouped by symbol
                        for symbol in sorted(by_symbol.keys()):
                            items = by_symbol[symbol]
                            if len(items) == 1:
                                print(f"  {symbol:8} → {items[0]['id']}")
                                if items[0]['desc']:
                                    print(f"           {items[0]['desc']}")
                            else:
                                print(f"  {symbol} ({len(items)} contracts):")
                                for item in items:
                                    print(f"    → {item['id']}")
                                    if item['desc']:
                                        print(f"      {item['desc']}")
                    else:
                        print("❌ No contracts available")
                
                elif command_lower == "metrics":
                    # Display performance metrics
                    try:
                        metrics_tracker = get_metrics_tracker(db=self.db)
                        metrics_tracker.print_report()
                        
                        # Also print JSON summary for programmatic access
                        report = metrics_tracker.get_full_report()
                        print(f"\n📊 Summary: {report['api']['total_calls']} API calls, "
                              f"{report['system']['memory_mb']} MB memory, "
                              f"{report['system']['cpu_percent']}% CPU")
                        
                        # Show cache performance if available
                        if report['cache']:
                            for cache_name, metrics in report['cache'].items():
                                print(f"💾 {cache_name}: {metrics['hit_rate']} hit rate")
                    except Exception as e:
                        print(f"❌ Failed to display metrics: {e}")
                        logger.error(f"Metrics display error: {e}")
                
                elif command_lower == "switch_account" or command_lower.startswith("switch_account "):
                    # Switch to a different account without closing the bot
                    accounts = await self.list_accounts()
                    if not accounts:
                        print("❌ No accounts available")
                        continue
                    
                    # Check if account ID provided as argument
                    parts = command.split()
                    if len(parts) > 1:
                        # Try to find account by ID or name
                        search_term = parts[1].strip()
                        target_account = None
                        
                        # Try by ID first
                        try:
                            account_id = int(search_term)
                            for acc in accounts:
                                if acc.get('id') == account_id:
                                    target_account = acc
                                    break
                        except ValueError:
                            # Try by name
                            for acc in accounts:
                                if search_term.lower() in acc.get('name', '').lower():
                                    target_account = acc
                                    break
                        
                        if target_account:
                            old_account = self.selected_account
                            self.selected_account = target_account
                            # Clear account-specific caches
                            self._cached_order_ids = {}
                            self._cached_position_ids = {}
                            
                            # Reinitialize account tracker for new account
                            balance = await self.get_account_balance()
                            if balance:
                                self.account_tracker.initialize(
                                    account_id=target_account['id'],
                                    starting_balance=balance,
                                    account_type=target_account.get('type', 'unknown')
                                )
                                logger.info(f"Account tracker reinitialized for {target_account.get('name')} (${balance:,.2f})")
                            
                            print(f"✅ Switched from {old_account.get('name', 'N/A')} to {target_account.get('name')}")
                            print(f"   Account ID: {target_account.get('id')}")
                            if balance:
                                print(f"   Balance: ${balance:,.2f}")
                        else:
                            print(f"❌ Account not found: {search_term}")
                            print("   Use 'accounts' to list available accounts")
                    else:
                        # Interactive selection
                        print("\n📋 Available Accounts:")
                        self.display_accounts(accounts)
                        print("\n💡 Enter account number or account ID to switch")
                        choice = input("Switch to account (number/ID or 'c' to cancel): ").strip()
                        
                        if choice.lower() == 'c':
                            print("❌ Account switch cancelled")
                            continue
                        
                        target_account = None
                        # Try by number first
                        try:
                            account_index = int(choice) - 1
                            if 0 <= account_index < len(accounts):
                                target_account = accounts[account_index]
                        except ValueError:
                            # Try by ID
                            try:
                                account_id = int(choice)
                                for acc in accounts:
                                    if acc.get('id') == account_id:
                                        target_account = acc
                                        break
                            except ValueError:
                                pass
                        
                        if target_account:
                            old_account = self.selected_account
                            self.selected_account = target_account
                            # Clear account-specific caches
                            self._cached_order_ids = {}
                            self._cached_position_ids = {}
                            
                            # Reinitialize account tracker for new account
                            balance = await self.get_account_balance()
                            if balance:
                                self.account_tracker.initialize(
                                    account_id=target_account['id'],
                                    starting_balance=balance,
                                    account_type=target_account.get('type', 'unknown')
                                )
                                logger.info(f"Account tracker reinitialized for {target_account.get('name')} (${balance:,.2f})")
                            
                            print(f"\n✅ Switched from {old_account.get('name', 'N/A') if old_account else 'None'} to {target_account.get('name')}")
                            print(f"   Account ID: {target_account.get('id')}")
                            if balance:
                                print(f"   Balance: ${balance:,.2f}")
                        else:
                            print(f"❌ Invalid selection: {choice}")
                elif command_lower == "help":
                    print("\n" + "="*70)
                    print("TRADING INTERFACE - COMPLETE COMMAND REFERENCE")
                    print("="*70)
                    print()
                    print("📊 MARKET DATA COMMANDS:")
                    print("  quote <symbol>")
                    print("    Get real-time market quote (bid, ask, last, volume)")
                    print("    Example: quote MNQ")
                    print()
                    print("  depth <symbol>")
                    print("    Get market depth (order book) with bids and asks")
                    print("    Example: depth MNQ")
                    print()
                    print("  history <symbol> [timeframe] [limit] [raw] [csv]")
                    print("    Get historical price data (bars)")
                    print("    Timeframes: 1s, 5s, 10s, 15s, 30s, 1m, 2m, 3m, 5m, 10m, 15m, 30m, 1h, 2h, 4h, 1d")
                    print("    Examples: history MNQ 5m 100")
                    print("              history MNQ 1m 50 raw    (tab-separated output)")
                    print("              history MNQ 5m 100 csv (export to CSV file)")
                    print()
                    print("  chart [symbol] [timeframe] [limit]")
                    print("    Open interactive chart window GUI")
                    print("    Example: chart MNQ 5m 100")
                    print()
                    print("  contracts")
                    print("    List all available trading contracts with details")
                    print()
                    print()
                    print("💰 ACCOUNT & RISK COMMANDS:")
                    print("  account_info")
                    print("    Get detailed account information (balance, equity, margin, etc.)")
                    print()
                    print("  account_state")
                    print("    Show real-time account state (balance, PnL, positions summary)")
                    print()
                    print("  compliance")
                    print("    Check account compliance status (DLL, MLL, trailing drawdown)")
                    print()
                    print("  risk")
                    print("    Show current risk metrics and limits")
                    print()
                    print("  drawdown (also: max_loss)")
                    print("    Show max loss limit and drawdown information")
                    print()
                    print("  trades [start_date] [end_date]")
                    print("    List trades with FIFO consolidation and statistics")
                    print("    Example: trades 2025-12-01 2025-12-04")
                    print()
                    print("  accounts")
                    print("    List all your trading accounts")
                    print()
                    print("  switch_account [account_id]")
                    print("    Switch to a different account without restarting")
                    print("    Example: switch_account 12694476")
                    print()
                    print("  metrics")
                    print("    Show performance metrics and system stats (API calls, cache, memory, CPU)")
                    print()
                    print()
                    print("📈 TRADING ORDER COMMANDS:")
                    print("  trade <symbol> <side> <quantity>")
                    print("    Place a market order")
                    print("    Example: trade MNQ BUY 1")
                    print()
                    print("  limit <symbol> <side> <quantity> <price>")
                    print("    Place a limit order at specified price")
                    print("    Example: limit MNQ BUY 1 19500.50")
                    print()
                    print("  bracket <symbol> <side> <quantity> <stop_ticks> <profit_ticks>")
                    print("    Place a bracket order with stop loss and take profit (in ticks)")
                    print("    Example: bracket MNQ BUY 1 80 80")
                    print()
                    print("  native_bracket <symbol> <side> <quantity> <stop_price> <profit_price>")
                    print("    Place a native TopStepX bracket order with linked stop/take profit")
                    print("    Example: native_bracket MNQ BUY 1 19400.00 19600.00")
                    print()
                    print("  stop_bracket <symbol> <side> <quantity> <entry_price> <stop_price> <profit_price>")
                    print("    Place a stop entry order with stop loss and take profit prices defined")
                    print("    Example: stop_bracket MNQ BUY 1 25800.00 25750.00 25900.00")
                    print()
                    print("  stop_buy <symbol> <quantity> <stop_price>")
                    print("    Place a stop buy order (triggers market buy when price reaches stop_price)")
                    print("    Example: stop_buy MNQ 1 25900.00")
                    print()
                    print("  stop_sell <symbol> <quantity> <stop_price>")
                    print("    Place a stop sell order (triggers market sell when price reaches stop_price)")
                    print("    Example: stop_sell MNQ 1 25900.00")
                    print()
                    print("  stop <symbol> <side> <quantity> <price>")
                    print("    Place a stop order (legacy command, use stop_buy/stop_sell)")
                    print("    Example: stop MNQ BUY 1 19400.00")
                    print()
                    print("  trail <symbol> <side> <quantity> <trail_amount>")
                    print("    Place a trailing stop order")
                    print("    Example: trail MNQ BUY 1 25.00")
                    print()
                    print()
                    print("📦 POSITION MANAGEMENT COMMANDS:")
                    print("  positions")
                    print("    Show all open positions with P&L, entry price, current price")
                    print()
                    print("  orders")
                    print("    Show all open orders with status and details")
                    print()
                    print("  close <position_id> [quantity]")
                    print("    Close a position (entire or partial)")
                    print("    Example: close 425682864 1")
                    print()
                    print("  cancel <order_id>")
                    print("    Cancel an order")
                    print("    Example: cancel 12345")
                    print()
                    print("  modify <order_id> <new_quantity> [new_price]")
                    print("    Modify an existing order")
                    print("    Example: modify 12345 2 19500.00")
                    print()
                    print("  modify_stop <position_id> <new_stop_price>")
                    print("    Modify the stop loss order attached to a position")
                    print("    Example: modify_stop 425682864 25800.00")
                    print()
                    print("  modify_tp <position_id> <new_tp_price>")
                    print("    Modify the take profit order attached to a position")
                    print("    Example: modify_tp 425682864 26100.00")
                    print()
                    print("  flatten")
                    print("    Close all positions and cancel all orders")
                    print("    Requires confirmation by typing 'FLATTEN'")
                    print()
                    print()
                    print("🔄 MONITORING & AUTOMATION COMMANDS:")
                    print("  monitor")
                    print("    Monitor position changes and automatically adjust bracket orders")
                    print("    Use this after adding/subtracting contracts to existing positions")
                    print()
                    print("  bracket_monitor")
                    print("    Monitor bracket positions and manage orders")
                    print()
                    print("  activate_monitor")
                    print("    Manually activate monitoring for testing")
                    print()
                    print("  deactivate_monitor")
                    print("    Manually deactivate monitoring")
                    print()
                    print("  check_fills")
                    print("    Manually check for filled orders and send Discord notifications")
                    print()
                    print("  test_fills")
                    print("    Test fill checking with detailed output")
                    print()
                    print("  auto_fills")
                    print("    Enable automatic fill checking every 30 seconds")
                    print()
                    print("  stop_auto_fills")
                    print("    Disable automatic fill checking")
                    print()
                    print("  clear_notifications")
                    print("    Clear notification cache to re-check all orders")
                    print()
                    print()
                    print("🎯 STRATEGY MANAGEMENT COMMANDS:")
                    print("  strategies list (or: strategies)")
                    print("    List all available strategies")
                    print()
                    print("  strategies status")
                    print("    Show status of all strategies")
                    print()
                    print("  strategies start <name> [symbols]")
                    print("    Start a specific strategy")
                    print("    Example: strategies start overnight_range MNQ MES")
                    print()
                    print("  strategies stop <name>")
                    print("    Stop a specific strategy")
                    print("    Example: strategies stop overnight_range")
                    print()
                    print("  strategies start_all")
                    print("    Start all enabled strategies")
                    print()
                    print("  strategies stop_all")
                    print("    Stop all strategies")
                    print()
                    print("  strategy_start [symbols]")
                    print("    Start overnight range breakout strategy (legacy command)")
                    print()
                    print("  strategy_stop")
                    print("    Stop the overnight strategy (legacy command)")
                    print()
                    print("  strategy_status")
                    print("    Show overnight strategy status and configuration (legacy command)")
                    print()
                    print("  strategy_test <symbol>")
                    print("    Test strategy components (ATR, ranges, orders) (legacy command)")
                    print()
                    print("  strategy_execute [symbols]")
                    print("    Manually trigger market open sequence for testing (legacy command)")
                    print()
                    print()
                    print("⚙️  SYSTEM COMMANDS:")
                    print("  help")
                    print("    Show this help message")
                    print()
                    print("  quit (or: q)")
                    print("    Exit trading interface")
                    print()
                    print("="*70)
                    print("💡 TIPS:")
                    print("   - Use ↑/↓ arrows to navigate command history")
                    print("   - Use Tab key for command completion")
                    print("   - All commands are case-insensitive")
                    print("   - Symbol names are automatically converted to uppercase")
                    print("   - Use 'raw' flag with history for fast tab-separated output")
                    print("   - Use 'csv' flag with history to export data to CSV file")
                    print("="*70)
                elif command_lower.startswith("trade "):
                    parts = command.split()
                    if len(parts) != 4:
                        print("❌ Usage: trade <symbol> <side> <quantity>")
                        print("   Example: trade MNQ BUY 1")
                        continue
                    
                    symbol, side, quantity = parts[1], parts[2], parts[3]
                    
                    try:
                        quantity = int(quantity)
                    except ValueError:
                        print("❌ Quantity must be a number")
                        continue
                    
                    if side.upper() not in ["BUY", "SELL"]:
                        print("❌ Side must be BUY or SELL")
                        continue
                    
                    # Confirm the trade
                    print(f"\n⚠️  CONFIRM TRADE:")
                    print(f"   Symbol: {symbol.upper()}")
                    print(f"   Side: {side.upper()}")
                    print(f"   Quantity: {quantity}")
                    print(f"   Account: {self.selected_account['name']}")
                    
                    confirm = input("   Confirm? (y/N): ").strip().lower()
                    if confirm != 'y':
                        print("❌ Trade cancelled")
                        continue
                    
                    # Place the order
                    result = await self.place_market_order(symbol, side, quantity)
                    if "error" in result:
                        print(f"❌ Order failed: {result['error']}")
                    else:
                        print(f"✅ Order placed successfully!")
                        print(f"   Order ID: {result.get('orderId', 'Unknown')}")
                        print(f"   Status: {result.get('status', 'Unknown')}")
                
                elif command_lower.startswith("limit "):
                    parts = command.split()
                    if len(parts) != 5:
                        print("❌ Usage: limit <symbol> <side> <quantity> <price>")
                        print("   Example: limit MNQ BUY 1 19500.50")
                        continue
                    
                    symbol, side, quantity, price = parts[1], parts[2], parts[3], parts[4]
                    
                    try:
                        quantity = int(quantity)
                        price = float(price)
                    except ValueError:
                        print("❌ Quantity must be a number and price must be a decimal number")
                        continue
                    
                    if side.upper() not in ["BUY", "SELL"]:
                        print("❌ Side must be BUY or SELL")
                        continue
                    
                    # Confirm the limit order
                    print(f"\n⚠️  CONFIRM LIMIT ORDER:")
                    print(f"   Symbol: {symbol.upper()}")
                    print(f"   Side: {side.upper()}")
                    print(f"   Quantity: {quantity}")
                    print(f"   Price: {price}")
                    print(f"   Account: {self.selected_account['name']}")
                    
                    confirm = input("   Confirm? (y/N): ").strip().lower()
                    if confirm != 'y':
                        print("❌ Order cancelled")
                        continue
                    
                    # Place the limit order
                    result = await self.place_market_order(symbol, side, quantity, order_type="limit", limit_price=price)
                    if "error" in result:
                        print(f"❌ Order failed: {result['error']}")
                    else:
                        print(f"✅ Limit order placed successfully!")
                        print(f"   Order ID: {result.get('orderId', 'Unknown')}")
                        print(f"   Status: {result.get('status', 'Unknown')}")
                
                elif command_lower.startswith("bracket "):
                    parts = command.split()
                    if len(parts) != 6:
                        print("❌ Usage: bracket <symbol> <side> <quantity> <stop_ticks> <profit_ticks>")
                        print("   Example: bracket MNQ BUY 1 80 80")
                        continue
                    
                    symbol, side, quantity, stop_ticks, profit_ticks = parts[1], parts[2], parts[3], parts[4], parts[5]
                    
                    try:
                        quantity = int(quantity)
                        stop_ticks = int(stop_ticks)
                        profit_ticks = int(profit_ticks)
                    except ValueError:
                        print("❌ Quantity, stop_ticks, and profit_ticks must be numbers")
                        continue
                    
                    if side.upper() not in ["BUY", "SELL"]:
                        print("❌ Side must be BUY or SELL")
                        continue
                    
                    # Confirm the bracket trade
                    print(f"\n⚠️  CONFIRM BRACKET TRADE:")
                    print(f"   Symbol: {symbol.upper()}")
                    print(f"   Side: {side.upper()}")
                    print(f"   Quantity: {quantity}")
                    print(f"   Stop Loss: {stop_ticks} ticks")
                    print(f"   Take Profit: {profit_ticks} ticks")
                    print(f"   Account: {self.selected_account['name']}")
                    
                    confirm = input("   Confirm? (y/N): ").strip().lower()
                    if confirm != 'y':
                        print("❌ Trade cancelled")
                        continue
                    
                    # Place the bracket order
                    result = await self.place_market_order(symbol, side, quantity, 
                                                        stop_loss_ticks=stop_ticks, 
                                                        take_profit_ticks=profit_ticks,
                                                        order_type="bracket")
                    if "error" in result:
                        print(f"❌ Order failed: {result['error']}")
                    else:
                        print(f"✅ Bracket order placed successfully!")
                        print(f"   Order ID: {result.get('orderId', 'Unknown')}")
                        print(f"   Status: {result.get('status', 'Unknown')}")
                
                elif command_lower.startswith("native_bracket "):
                    parts = command.split()
                    if len(parts) != 6:
                        print("❌ Usage: native_bracket <symbol> <side> <quantity> <stop_price> <profit_price>")
                        print("   Example: native_bracket MNQ BUY 1 19400.00 19600.00")
                        continue
                    
                    symbol, side, quantity, stop_price, profit_price = parts[1], parts[2], parts[3], parts[4], parts[5]
                    
                    try:
                        quantity = int(quantity)
                        stop_price = float(stop_price)
                        profit_price = float(profit_price)
                    except ValueError:
                        print("❌ Quantity must be a number and prices must be decimal numbers")
                        continue
                    
                    if side.upper() not in ["BUY", "SELL"]:
                        print("❌ Side must be BUY or SELL")
                        continue
                    
                    # Show OCO bracket warning
                    print(f"\n⚠️  IMPORTANT: Bracket orders require 'Auto OCO Brackets' to be enabled in your TopStepX account settings.")
                    print(f"   If this order fails with 'Brackets cannot be used with Position Brackets', please enable Auto OCO Brackets in your account.")
                    
                    # Confirm the native bracket order
                    print(f"\n⚠️  CONFIRM NATIVE BRACKET ORDER:")
                    print(f"   Symbol: {symbol.upper()}")
                    print(f"   Side: {side.upper()}")
                    print(f"   Quantity: {quantity}")
                    print(f"   Stop Loss: ${stop_price}")
                    print(f"   Take Profit: ${profit_price}")
                    print(f"   Account: {self.selected_account['name']}")
                    
                    confirm = input("   Confirm? (y/N): ").strip().lower()
                    if confirm != 'y':
                        print("❌ Order cancelled")
                        continue
                    
                    # Place the native bracket order
                    result = await self.create_bracket_order(symbol, side, quantity, 
                                                          stop_loss_price=stop_price, 
                                                          take_profit_price=profit_price)
                    if "error" in result:
                        print(f"❌ Order failed: {result['error']}")
                    else:
                        print(f"✅ Native bracket order placed successfully!")
                        print(f"   Order ID: {result.get('orderId', 'Unknown')}")
                        print(f"   Status: {result.get('status', 'Unknown')}")
                
                elif command_lower.startswith("stop_bracket "):
                    parts = command.split()
                    
                    # Check for --breakeven flag
                    enable_breakeven = "--breakeven" in parts
                    if enable_breakeven:
                        parts.remove("--breakeven")
                    
                    if len(parts) != 7:
                        print("❌ Usage: stop_bracket <symbol> <side> <quantity> <entry_price> <stop_price> <profit_price> [--breakeven]")
                        print("   Example: stop_bracket MNQ BUY 1 25800.00 25750.00 25900.00")
                        print("   Example: stop_bracket MNQ BUY 1 25800.00 25750.00 25900.00 --breakeven")
                        print("   Places a stop order at entry_price with bracket SL/TP attached")
                        print("   --breakeven: Automatically move stop to breakeven after profit threshold")
                        print(f"   Profit threshold: {os.getenv('MANUAL_BREAKEVEN_PROFIT_POINTS', '15.0')} points")
                        continue
                    
                    symbol, side, quantity, entry_price, stop_price, profit_price = parts[1], parts[2], parts[3], parts[4], parts[5], parts[6]
                    
                    try:
                        quantity = int(quantity)
                        entry_price = float(entry_price)
                        stop_price = float(stop_price)
                        profit_price = float(profit_price)
                    except ValueError:
                        print("❌ Quantity must be a number and prices must be decimal numbers")
                        continue
                    
                    if side.upper() not in ["BUY", "SELL"]:
                        print("❌ Side must be BUY or SELL")
                        continue
                    
                    # Validate bracket prices
                    if side.upper() == "BUY":
                        if stop_price >= entry_price:
                            print("❌ For BUY orders, stop loss must be below entry price")
                            continue
                        if profit_price <= entry_price:
                            print("❌ For BUY orders, take profit must be above entry price")
                            continue
                    else:  # SELL
                        if stop_price <= entry_price:
                            print("❌ For SELL orders, stop loss must be above entry price")
                            continue
                        if profit_price >= entry_price:
                            print("❌ For SELL orders, take profit must be below entry price")
                            continue
                    
                    # Check env variable for default breakeven behavior
                    if not enable_breakeven and os.getenv('MANUAL_BREAKEVEN_ENABLED', 'false').lower() in ('true', '1', 'yes', 'on'):
                        enable_breakeven = True
                    
                    # Confirm the stop bracket order
                    print(f"\n⚠️  CONFIRM STOP BRACKET ORDER:")
                    print(f"   Symbol: {symbol.upper()}")
                    print(f"   Side: {side.upper()}")
                    print(f"   Quantity: {quantity}")
                    print(f"   Entry (Stop) Price: ${entry_price}")
                    print(f"   Stop Loss: ${stop_price}")
                    print(f"   Take Profit: ${profit_price}")
                    print(f"   Breakeven: {'✅ Enabled' if enable_breakeven else '❌ Disabled'}")
                    if enable_breakeven:
                        breakeven_points = float(os.getenv('MANUAL_BREAKEVEN_PROFIT_POINTS', '15.0'))
                        print(f"   Breakeven Threshold: {breakeven_points} points profit")
                    print(f"   Account: {self.selected_account['name']}")
                    print(f"   ⚠️  Entry triggers when price {'rises to' if side.upper() == 'BUY' else 'falls to'} ${entry_price}")
                    
                    confirm = input("   Confirm? (y/N): ").strip().lower()
                    if confirm != 'y':
                        print("❌ Order cancelled")
                        continue
                    
                    # Place the OCO bracket with stop entry
                    print(f"\n🚀 Placing OCO bracket order with stop entry...")
                    result = await self.place_oco_bracket_with_stop_entry(
                        symbol=symbol,
                        side=side,
                        quantity=quantity,
                        entry_price=entry_price,
                        stop_loss_price=stop_price,
                        take_profit_price=profit_price,
                        enable_breakeven=enable_breakeven
                    )
                    
                    if "error" in result:
                        print(f"❌ Stop bracket failed: {result['error']}")
                        continue
                    
                    entry_order_id = result.get('orderId')
                    method = result.get('method', 'unknown')
                    
                    if method == "oco_native":
                        print(f"✅ OCO bracket order placed successfully!")
                        print(f"   Order ID: {entry_order_id}")
                        print(f"   Method: Native OCO (atomic)")
                        print(f"   Entry: ${entry_price} (stop order)")
                        print(f"   Stop Loss: ${stop_price}")
                        print(f"   Take Profit: ${profit_price}")
                        print(f"   📝 All orders linked - one fills, others cancel automatically")
                        if enable_breakeven:
                            breakeven_pts = float(os.getenv('MANUAL_BREAKEVEN_PROFIT_POINTS', '15.0'))
                            print(f"   🔄 Breakeven enabled: Stop will move to entry after +{breakeven_pts} pts profit")
                    elif method == "hybrid_auto_bracket":
                        print(f"✅ Stop entry order placed with auto-bracketing!")
                        print(f"   Order ID: {entry_order_id}")
                        print(f"   Method: Hybrid (auto-bracket on fill)")
                        print(f"   Entry: ${entry_price} (stop order)")
                        print(f"   Stop Loss: ${stop_price}")
                        print(f"   Take Profit: ${profit_price}")
                        print(f"   📝 Brackets will be placed automatically when stop order fills")
                        if enable_breakeven:
                            breakeven_pts = float(os.getenv('MANUAL_BREAKEVEN_PROFIT_POINTS', '15.0'))
                            print(f"   🔄 Breakeven enabled: Stop will move to entry after +{breakeven_pts} pts profit")
                    else:
                        print(f"✅ Stop bracket order placed!")
                        print(f"   Order ID: {entry_order_id}")
                        print(f"   Entry: ${entry_price}")
                        print(f"   Stop Loss: ${stop_price}")
                        print(f"   Take Profit: ${profit_price}")
                        if enable_breakeven:
                            breakeven_pts = float(os.getenv('MANUAL_BREAKEVEN_PROFIT_POINTS', '15.0'))
                            print(f"   🔄 Breakeven enabled: Stop will move to entry after +{breakeven_pts} pts profit")
                
                elif command_lower == "strategy_start" or command_lower.startswith("strategy_start "):
                    # Start overnight range breakout strategy
                    parts = command.split()
                    symbols = parts[1:] if len(parts) > 1 else None
                    
                    print(f"\n🎯 Starting Overnight Range Breakout Strategy...")
                    print(f"   Symbols: {symbols or os.getenv('STRATEGY_SYMBOLS', 'MNQ,MES')}")
                    print(f"   Overnight: {self.overnight_strategy.overnight_start} - {self.overnight_strategy.overnight_end}")
                    print(f"   Market Open: {self.overnight_strategy.market_open_time}")
                    print(f"   ATR Period: {self.overnight_strategy.atr_period} ({self.overnight_strategy.atr_timeframe})")
                    if self.overnight_strategy.breakeven_enabled:
                        print(f"   Breakeven: ENABLED (+{self.overnight_strategy.breakeven_profit_points} pts)")
                    else:
                        print(f"   Breakeven: DISABLED")
                    
                    confirm = input("\n   Start strategy? (y/N): ").strip().lower()
                    if confirm != 'y':
                        print("❌ Strategy start cancelled")
                        continue
                    
                    await self.overnight_strategy.start(symbols)
                    print("✅ Strategy started! It will place orders at market open.")
                
                elif command_lower == "strategy_stop":
                    # Stop overnight range breakout strategy
                    if not self.overnight_strategy.is_trading:
                        print("❌ Strategy is not running")
                        continue
                    
                    confirm = input("\n   Stop strategy? (y/N): ").strip().lower()
                    if confirm != 'y':
                        print("❌ Strategy stop cancelled")
                        continue
                    
                    await self.overnight_strategy.stop()
                    print("✅ Strategy stopped!")
                
                elif command_lower == "strategy_status":
                    # Show strategy status
                    status = self.overnight_strategy.get_status()
                    
                    print(f"\n📊 Overnight Range Strategy Status:")
                    print(f"   Active: {'✅ YES' if status['is_trading'] else '❌ NO'}")
                    print(f"\n   Configuration:")
                    for key, value in status['config'].items():
                        key_display = key.replace('_', ' ').title()
                        print(f"     {key_display}: {value}")
                    
                    if status['active_ranges']:
                        print(f"\n   📈 Tracked Ranges:")
                        for symbol, range_data in status['active_ranges'].items():
                            print(f"     {symbol}: High={range_data['high']:.2f}, Low={range_data['low']:.2f}, Range={range_data['range_size']:.2f}")
                    
                    if status['active_orders']:
                        print(f"\n   📝 Active Orders:")
                        for symbol, order_ids in status['active_orders'].items():
                            print(f"     {symbol}: {len(order_ids)} orders")
                    
                    if status['config']['breakeven_enabled']:
                        if status['breakeven_monitoring']:
                            print(f"\n   🎯 Breakeven Monitoring:")
                            for order_id, monitor_data in status['breakeven_monitoring'].items():
                                filled = monitor_data.get('filled', False)
                                triggered = monitor_data['triggered']
                                
                                if triggered:
                                    status_str = "✓ At Breakeven"
                                elif filled:
                                    status_str = "⏳ Monitoring (position filled)"
                                else:
                                    status_str = "⏸ Waiting for fill"
                                
                                print(f"     {monitor_data['symbol']} {monitor_data['side']}: {status_str}")
                        else:
                            print(f"\n   🎯 Breakeven Monitoring: No active positions")
                    else:
                        print(f"\n   🎯 Breakeven Monitoring: DISABLED")
                
                elif command_lower.startswith("strategy_test "):
                    # Test strategy components (ATR, overnight range, order calculation)
                    parts = command.split()
                    if len(parts) != 2:
                        print("❌ Usage: strategy_test <symbol>")
                        print("   Example: strategy_test MNQ")
                        continue
                    
                    symbol = parts[1].upper()
                    
                    print(f"\n🔬 Testing strategy components for {symbol}...")
                    
                    # Test ATR calculation
                    print(f"\n1️⃣ Calculating ATR...")
                    atr_data = await self.overnight_strategy.calculate_atr(symbol)
                    if atr_data:
                        print(f"   ✅ Current ATR: {atr_data.current_atr:.2f}")
                        print(f"   ✅ Daily ATR: {atr_data.daily_atr:.2f}")
                        print(f"   ✅ ATR Zone High: {atr_data.atr_zone_high:.2f}")
                        print(f"   ✅ ATR Zone Low: {atr_data.atr_zone_low:.2f}")
                    else:
                        print(f"   ❌ ATR calculation failed")
                    
                    # Test overnight range tracking
                    print(f"\n2️⃣ Tracking overnight range...")
                    range_data = await self.overnight_strategy.track_overnight_range(symbol)
                    if range_data:
                        print(f"   ✅ High: {range_data.high:.2f}")
                        print(f"   ✅ Low: {range_data.low:.2f}")
                        print(f"   ✅ Range Size: {range_data.range_size:.2f}")
                        print(f"   ✅ Midpoint: {range_data.midpoint:.2f}")
                        print(f"   ✅ Time: {range_data.start_time} to {range_data.end_time}")
                    else:
                        print(f"   ❌ Range tracking failed")
                    
                    # Test order calculation
                    print(f"\n3️⃣ Calculating breakout orders...")
                    long_order, short_order = await self.overnight_strategy.calculate_range_break_orders(symbol)
                    if long_order and short_order:
                        print(f"   ✅ LONG Order:")
                        print(f"      Entry: {long_order.entry_price:.2f}")
                        print(f"      Stop:  {long_order.stop_loss:.2f}")
                        print(f"      TP:    {long_order.take_profit:.2f}")
                        print(f"   ✅ SHORT Order:")
                        print(f"      Entry: {short_order.entry_price:.2f}")
                        print(f"      Stop:  {short_order.stop_loss:.2f}")
                        print(f"      TP:    {short_order.take_profit:.2f}")
                    else:
                        print(f"   ❌ Order calculation failed")
                    
                    print(f"\n✅ Strategy test complete!")
                
                elif command_lower == "strategy_execute" or command_lower.startswith("strategy_execute "):
                    # Manually trigger market open sequence (for testing)
                    if not self.overnight_strategy.is_trading:
                        print("❌ Strategy is not running. Start it first with 'strategy_start'")
                        continue
                    
                    parts = command.split()
                    symbols = parts[1:] if len(parts) > 1 else None
                    
                    print(f"\n🚀 Manually executing market open sequence...")
                    print(f"   Symbols: {symbols or 'default from config'}")
                    confirm = input("   Execute now? (y/N): ").strip().lower()
                    if confirm != 'y':
                        print("❌ Execution cancelled")
                        continue
                    
                    try:
                        await self.overnight_strategy._execute_market_open_sequence(symbols)
                        print("✅ Market open sequence executed!")
                    except Exception as e:
                        print(f"❌ Error executing sequence: {e}")
                        logger.error(f"Error in manual strategy execution: {e}")
                        import traceback
                        logger.error(f"Traceback: {traceback.format_exc()}")
                
                # Modular Strategy System Commands
                elif command_lower == "strategies list" or command_lower == "strategies":
                    # List all available strategies
                    print(f"\n📦 Available Strategies:")
                    print(f"="*60)
                    
                    for name, strategy_class in self.strategy_manager.available_strategies.items():
                        strategy_instance = self.strategy_manager.strategies.get(name)
                        
                        if strategy_instance:
                            status = strategy_instance.status.value
                            enabled = strategy_instance.config.enabled
                            symbols = ", ".join(strategy_instance.config.symbols)
                            status_emoji = "✅" if status == "active" else "⏸️" if status == "paused" else "⚪"
                        else:
                            status = "not loaded"
                            enabled = False
                            symbols = "N/A"
                            status_emoji = "⚪"
                        
                        print(f"{status_emoji} {name.replace('_', ' ').title()}")
                        print(f"   Status: {status}")
                        print(f"   Enabled in Config: {enabled}")
                        print(f"   Symbols: {symbols}")
                        print()
                    
                    print(f"💡 Use 'strategies start <name>' to start a strategy")
                    print(f"💡 Use 'strategies status' for detailed metrics")
                
                elif command_lower == "strategies status":
                    # Show detailed status of all strategies
                    status = self.strategy_manager.get_status()
                    
                    print(f"\n📊 Strategy Manager Status:")
                    print(f"="*60)
                    print(f"Active Strategies: {status['active_strategies']}/{status['total_strategies']}")
                    print(f"Total Positions: {status['total_positions']}")
                    print()
                    
                    for strategy_name, strategy_status in status['strategies'].items():
                        print(f"📈 {strategy_name.replace('_', ' ').title()}:")
                        print(f"   Status: {strategy_status['status']}")
                        print(f"   Enabled: {strategy_status['enabled']}")
                        print(f"   Symbols: {', '.join(strategy_status['symbols'])}")
                        print(f"   Active Positions: {strategy_status['active_positions']}")
                        print(f"   Daily Trades: {strategy_status['daily_trades']}")
                        
                        metrics = strategy_status.get('metrics', {})
                        if metrics.get('total_trades', 0) > 0:
                            print(f"   Metrics:")
                            print(f"     Total Trades: {metrics['total_trades']}")
                            print(f"     Win Rate: {metrics['win_rate']}")
                            print(f"     Total P&L: {metrics['total_pnl']}")
                            print(f"     Profit Factor: {metrics['profit_factor']}")
                        print()
                
                elif command_lower.startswith("strategies start "):
                    # Start a specific strategy
                    parts = command.split()
                    if len(parts) < 3:
                        print("❌ Usage: strategies start <name> [symbols]")
                        print("   Example: strategies start mean_reversion MNQ,MES")
                        print("   Available strategies:")
                        for name in self.strategy_manager.available_strategies.keys():
                            print(f"     - {name}")
                        continue
                    
                    strategy_name = parts[2]
                    symbols = parts[3].split(',') if len(parts) > 3 else None
                    
                    print(f"\n🚀 Starting {strategy_name.replace('_', ' ').title()} Strategy...")
                    success, message = await self.strategy_manager.start_strategy(strategy_name, symbols)
                    
                    if success:
                        print(f"✅ {message}")
                    else:
                        print(f"❌ {message}")
                
                elif command_lower.startswith("strategies stop "):
                    # Stop a specific strategy
                    parts = command.split()
                    if len(parts) != 3:
                        print("❌ Usage: strategies stop <name>")
                        print("   Example: strategies stop mean_reversion")
                        continue
                    
                    strategy_name = parts[2]
                    
                    print(f"\n🛑 Stopping {strategy_name.replace('_', ' ').title()} Strategy...")
                    success, message = await self.strategy_manager.stop_strategy(strategy_name)
                    
                    if success:
                        print(f"✅ {message}")
                    else:
                        print(f"❌ {message}")
                
                elif command_lower == "strategies start_all":
                    # Start all enabled strategies
                    print(f"\n🚀 Starting all enabled strategies...")
                    results = await self.strategy_manager.start_all_strategies()
                    
                    for strategy_name, (success, message) in results.items():
                        emoji = "✅" if success else "❌"
                        print(f"{emoji} {strategy_name.replace('_', ' ').title()}: {message}")
                
                elif command_lower == "strategies stop_all":
                    # Stop all strategies
                    confirm = input("\n⚠️  Stop all active strategies? (y/N): ").strip().lower()
                    if confirm != 'y':
                        print("❌ Operation cancelled")
                        continue
                    
                    print(f"\n🛑 Stopping all strategies...")
                    results = await self.strategy_manager.stop_all_strategies()
                    
                    for strategy_name, (success, message) in results.items():
                        emoji = "✅" if success else "❌"
                        print(f"{emoji} {strategy_name.replace('_', ' ').title()}: {message}")
                
                elif command_lower.startswith("stop "):
                    parts = command.split()
                    if len(parts) != 5:
                        print("❌ Usage: stop <symbol> <side> <quantity> <price>")
                        print("   Example: stop MNQ BUY 1 19400.00")
                        continue
                    
                    symbol, side, quantity, price = parts[1], parts[2], parts[3], parts[4]
                    
                    try:
                        quantity = int(quantity)
                        price = float(price)
                    except ValueError:
                        print("❌ Quantity must be a number and price must be a decimal number")
                        continue
                    
                    if side.upper() not in ["BUY", "SELL"]:
                        print("❌ Side must be BUY or SELL")
                        continue
                    
                    # Confirm the stop order
                    print(f"\n⚠️  CONFIRM STOP ORDER:")
                    print(f"   Symbol: {symbol.upper()}")
                    print(f"   Side: {side.upper()}")
                    print(f"   Quantity: {quantity}")
                    print(f"   Stop Price: ${price}")
                    print(f"   Account: {self.selected_account['name']}")
                    
                    confirm = input("   Confirm? (y/N): ").strip().lower()
                    if confirm != 'y':
                        print("❌ Order cancelled")
                        continue
                    
                    # Place the stop order
                    result = await self.place_stop_order(symbol, side, quantity, price)
                    if "error" in result:
                        print(f"❌ Order failed: {result['error']}")
                    else:
                        print(f"✅ Stop {side} order placed successfully!")
                        print(f"   Order ID: {result.get('orderId', 'Unknown')}")
                        print(f"   Status: {result.get('status', 'Unknown')}")
                        print(f"   ⚠️  This order will trigger a market order when price reaches ${price}")
                
                elif command_lower.startswith("stop_buy "):
                    parts = command.split()
                    if len(parts) != 4:
                        print("❌ Usage: stop_buy <symbol> <quantity> <stop_price>")
                        print("   Example: stop_buy MNQ 1 25900.00")
                        print("   Places a stop buy order (triggers market buy when price reaches stop_price)")
                        continue
                    
                    symbol, quantity, stop_price = parts[1], parts[2], parts[3]
                    
                    try:
                        quantity = int(quantity)
                        stop_price = float(stop_price)
                    except ValueError:
                        print("❌ Quantity must be a number and stop_price must be a decimal number")
                        continue
                    
                    # Confirm the stop buy order
                    print(f"\n⚠️  CONFIRM STOP BUY ORDER:")
                    print(f"   Symbol: {symbol.upper()}")
                    print(f"   Quantity: {quantity}")
                    print(f"   Stop Price: ${stop_price}")
                    print(f"   Account: {self.selected_account['name']}")
                    print(f"   ⚠️  This will trigger a market BUY when price reaches ${stop_price}")
                    
                    confirm = input("   Confirm? (y/N): ").strip().lower()
                    if confirm != 'y':
                        print("❌ Order cancelled")
                        continue
                    
                    # Place the stop buy order
                    result = await self.place_stop_order(symbol, "BUY", quantity, stop_price)
                    if "error" in result:
                        print(f"❌ Order failed: {result['error']}")
                    else:
                        print(f"✅ Stop buy order placed successfully!")
                        print(f"   Order ID: {result.get('orderId', 'Unknown')}")
                        print(f"   Status: {result.get('status', 'Unknown')}")
                
                elif command_lower.startswith("stop_sell "):
                    parts = command.split()
                    if len(parts) != 4:
                        print("❌ Usage: stop_sell <symbol> <quantity> <stop_price>")
                        print("   Example: stop_sell MNQ 1 25900.00")
                        print("   Places a stop sell order (triggers market sell when price reaches stop_price)")
                        continue
                    
                    symbol, quantity, stop_price = parts[1], parts[2], parts[3]
                    
                    try:
                        quantity = int(quantity)
                        stop_price = float(stop_price)
                    except ValueError:
                        print("❌ Quantity must be a number and stop_price must be a decimal number")
                        continue
                    
                    # Confirm the stop sell order
                    print(f"\n⚠️  CONFIRM STOP SELL ORDER:")
                    print(f"   Symbol: {symbol.upper()}")
                    print(f"   Quantity: {quantity}")
                    print(f"   Stop Price: ${stop_price}")
                    print(f"   Account: {self.selected_account['name']}")
                    print(f"   ⚠️  This will trigger a market SELL when price reaches ${stop_price}")
                    
                    confirm = input("   Confirm? (y/N): ").strip().lower()
                    if confirm != 'y':
                        print("❌ Order cancelled")
                        continue
                    
                    # Place the stop sell order
                    result = await self.place_stop_order(symbol, "SELL", quantity, stop_price)
                    if "error" in result:
                        print(f"❌ Order failed: {result['error']}")
                    else:
                        print(f"✅ Stop sell order placed successfully!")
                        print(f"   Order ID: {result.get('orderId', 'Unknown')}")
                        print(f"   Status: {result.get('status', 'Unknown')}")
                
                elif command_lower.startswith("modify_stop "):
                    parts = command.split()
                    if len(parts) != 3:
                        print("❌ Usage: modify_stop <position_id> <new_stop_price>")
                        print("   Example: modify_stop 425682864 25800.00")
                        print("   Modifies the stop loss order attached to a position")
                        continue
                    
                    position_id, new_stop_price = parts[1], parts[2]
                    
                    try:
                        new_stop_price = float(new_stop_price)
                    except ValueError:
                        print("❌ new_stop_price must be a decimal number")
                        continue
                    
                    # Confirm the stop loss modification
                    print(f"\n⚠️  CONFIRM MODIFY STOP LOSS:")
                    print(f"   Position ID: {position_id}")
                    print(f"   New Stop Price: ${new_stop_price}")
                    print(f"   Account: {self.selected_account['name']}")
                    
                    confirm = input("   Confirm? (y/N): ").strip().lower()
                    if confirm != 'y':
                        print("❌ Modification cancelled")
                        continue
                    
                    # Modify the stop loss
                    result = await self.modify_stop_loss(position_id, new_stop_price)
                    if "error" in result:
                        print(f"❌ Modify failed: {result['error']}")
                    else:
                        print(f"✅ Stop loss modified successfully!")
                        print(f"   Position ID: {position_id}")
                        print(f"   New Stop Price: ${new_stop_price}")
                        print(f"   Stop Order ID: {result.get('stop_order_id', 'Unknown')}")
                
                elif command_lower.startswith("modify_tp "):
                    parts = command.split()
                    if len(parts) != 3:
                        print("❌ Usage: modify_tp <position_id> <new_tp_price>")
                        print("   Example: modify_tp 425682864 26100.00")
                        print("   Modifies the take profit order attached to a position")
                        continue
                    
                    position_id, new_tp_price = parts[1], parts[2]
                    
                    try:
                        new_tp_price = float(new_tp_price)
                    except ValueError:
                        print("❌ new_tp_price must be a decimal number")
                        continue
                    
                    # Confirm the take profit modification
                    print(f"\n⚠️  CONFIRM MODIFY TAKE PROFIT:")
                    print(f"   Position ID: {position_id}")
                    print(f"   New Take Profit Price: ${new_tp_price}")
                    print(f"   Account: {self.selected_account['name']}")
                    
                    confirm = input("   Confirm? (y/N): ").strip().lower()
                    if confirm != 'y':
                        print("❌ Modification cancelled")
                        continue
                    
                    # Modify the take profit
                    result = await self.modify_take_profit(position_id, new_tp_price)
                    if "error" in result:
                        print(f"❌ Modify failed: {result['error']}")
                    else:
                        print(f"✅ Take profit modified successfully!")
                        print(f"   Position ID: {position_id}")
                        print(f"   New Take Profit Price: ${new_tp_price}")
                        print(f"   TP Order ID: {result.get('tp_order_id', 'Unknown')}")
                
                elif command_lower.startswith("trail "):
                    parts = command.split()
                    if len(parts) != 5:
                        print("❌ Usage: trail <symbol> <side> <quantity> <trail_amount>")
                        print("   Example: trail MNQ BUY 1 25.00")
                        print("   Places a trailing stop order (uses SDK native trailing stop)")
                        print("   ⚠️  Requires USE_PROJECTX_SDK=1 in .env file")
                        continue
                    
                    symbol, side, quantity, trail_amount = parts[1], parts[2], parts[3], parts[4]
                    
                    try:
                        quantity = int(quantity)
                        trail_amount = float(trail_amount)
                    except ValueError:
                        print("❌ Quantity must be a number and trail_amount must be a decimal number")
                        continue
                    
                    if side.upper() not in ["BUY", "SELL"]:
                        print("❌ Side must be BUY or SELL")
                        continue
                    
                    # Check if SDK is available
                    use_sdk = os.getenv("USE_PROJECTX_SDK", "0").lower() in ("1", "true", "yes")
                    if not use_sdk or sdk_adapter is None or not sdk_adapter.is_sdk_available():
                        print("❌ Trailing stop requires SDK. Please:")
                        print("   1. Install: pip install 'project-x-py[realtime]'")
                        print("   2. Set USE_PROJECTX_SDK=1 in your .env file")
                        continue
                    
                    # Confirm the trailing stop order
                    print(f"\n⚠️  CONFIRM TRAILING STOP ORDER:")
                    print(f"   Symbol: {symbol.upper()}")
                    print(f"   Side: {side.upper()}")
                    print(f"   Quantity: {quantity}")
                    print(f"   Trail Amount: ${trail_amount}")
                    print(f"   Account: {self.selected_account['name']}")
                    print(f"   ⚠️  This uses SDK native trailing stop (automatically adjusts with price)")
                    
                    confirm = input("   Confirm? (y/N): ").strip().lower()
                    if confirm != 'y':
                        print("❌ Order cancelled")
                        continue
                    
                    # Place the trailing stop order via SDK
                    result = await self.place_trailing_stop_order(symbol, side, quantity, trail_amount)
                    if "error" in result:
                        print(f"❌ Order failed: {result['error']}")
                    else:
                        print(f"✅ Trailing stop order placed successfully!")
                        print(f"   Order ID: {result.get('orderId', 'Unknown')}")
                        print(f"   Method: SDK native trailing stop")
                        print(f"   ⚠️  Stop will automatically adjust as price moves in your favor")
                
                elif command_lower == "positions":
                    positions = await self.get_open_positions()
                    if positions:
                        print(f"\n📊 Open Positions ({len(positions)}):")
                        print(f"{'ID':<12} {'Symbol':<8} {'Side':<6} {'Quantity':<10} {'Price':<12} {'Stop':<12} {'TP':<12} {'P&L':<12}")
                        print("-" * 90)
                        for pos in positions:
                            pos_id = pos.get('id', 'N/A')
                            # Get symbol from contractId or symbol field
                            contract_id = pos.get('contractId', '')
                            if contract_id:
                                # Extract symbol from contract ID (e.g., CON.F.US.MNQ.Z25 -> MNQ)
                                symbol = contract_id.split('.')[-2] if '.' in contract_id else contract_id
                            else:
                                symbol = pos.get('symbol', 'N/A')
                            
                            # Determine side from type (1 = Long, 2 = Short)
                            position_type = pos.get('type', 0)
                            if position_type == 1:
                                side = "LONG"
                            elif position_type == 2:
                                side = "SHORT"
                            else:
                                side = "UNKNOWN"
                            
                            quantity = pos.get('size', 0)
                            price = pos.get('averagePrice', 0.0)
                            
                            # Get stop loss and take profit prices from linked orders
                            stop_price = None
                            tp_price = None
                            try:
                                linked_orders = await self.get_linked_orders(str(pos_id))
                                if linked_orders and isinstance(linked_orders, list):
                                    logger.debug(f"Found {len(linked_orders)} linked orders for position {pos_id}")
                                    for order in linked_orders:
                                        order_type = order.get('type', 0)
                                        order_side = order.get('side', -1)
                                        # Type 4 = Stop orders (stop loss), Type 1 = Limit orders (take profit)
                                        # Also check prices directly: stopPrice for stop loss, limitPrice for take profit
                                        has_stop_price = order.get('stopPrice') is not None
                                        has_limit_price = order.get('limitPrice') is not None
                                        
                                        # For long positions: stop loss is a sell stop, TP is a sell limit
                                        # For short positions: stop loss is a buy stop, TP is a buy limit
                                        if position_type == 1:  # LONG position
                                            if order_side == 1:  # SELL orders
                                                if order_type == 4 or has_stop_price:  # Stop order
                                                    stop_price = order.get('stopPrice') or order.get('limitPrice')
                                                    logger.debug(f"Found stop loss for long: {stop_price}")
                                                elif order_type == 1 and has_limit_price:  # Limit order for TP
                                                    tp_price = order.get('limitPrice')
                                                    logger.debug(f"Found take profit for long: {tp_price}")
                                        elif position_type == 2:  # SHORT position
                                            if order_side == 0:  # BUY orders
                                                if order_type == 4 or has_stop_price:  # Stop order
                                                    stop_price = order.get('stopPrice') or order.get('limitPrice')
                                                    logger.debug(f"Found stop loss for short: {stop_price}")
                                                elif order_type == 1 and has_limit_price:  # Limit order for TP
                                                    tp_price = order.get('limitPrice')
                                                    logger.debug(f"Found take profit for short: {tp_price}")
                                else:
                                    logger.debug(f"No linked orders found for position {pos_id}")
                            except Exception as e:
                                logger.warning(f"Could not fetch linked orders for position {pos_id}: {e}")
                            
                            # Get P&L from API, or calculate it if not provided
                            pnl = pos.get('unrealizedPnl')
                            if pnl is None:
                                # Calculate P&L from current market price
                                # P&L = (price_difference) * quantity * point_value
                                try:
                                    quote = await self.get_market_quote(symbol)
                                    if "error" not in quote and quote.get('last'):
                                        current_price = float(quote['last'])
                                        point_value = self._get_point_value(symbol)
                                        
                                        if position_type == 1:  # LONG
                                            price_diff = current_price - price
                                        else:  # SHORT
                                            price_diff = price - current_price
                                        
                                        # Calculate P&L: price difference * quantity * point value per contract
                                        pnl = price_diff * quantity * point_value
                                    else:
                                        pnl = 0.0
                                except Exception as e:
                                    logger.debug(f"Could not calculate P&L for {symbol}: {e}")
                                    pnl = 0.0
                            else:
                                pnl = float(pnl)
                            
                            # Format prices for display
                            stop_str = f"${stop_price:.2f}" if stop_price else "N/A"
                            tp_str = f"${tp_price:.2f}" if tp_price else "N/A"
                            
                            print(f"{pos_id:<12} {symbol:<8} {side:<6} {quantity:<10} ${price:<11.2f} {stop_str:<12} {tp_str:<12} ${pnl:<11.2f}")
                    else:
                        print("❌ No open positions found")
                
                elif command_lower == "orders":
                    orders = await self.get_open_orders()
                    if orders:
                        print(f"\n📋 Open Orders ({len(orders)}):")
                        print(f"{'ID':<12} {'Symbol':<8} {'Side':<6} {'Type':<8} {'Quantity':<10} {'Price':<12} {'Status':<10}")
                        print("-" * 80)
                        for order in orders:
                            order_id = order.get('id', 'N/A')
                            # Get symbol from contractId or symbol field
                            contract_id = order.get('contractId', '')
                            if contract_id:
                                # Extract symbol from contract ID (e.g., CON.F.US.MNQ.Z25 -> MNQ)
                                symbol = contract_id.split('.')[-2] if '.' in contract_id else contract_id
                            else:
                                symbol = order.get('symbol', 'N/A')
                            
                            # Determine side from side field (0 = BUY, 1 = SELL)
                            side_num = order.get('side', -1)
                            if side_num == 0:
                                side = "BUY"
                            elif side_num == 1:
                                side = "SELL"
                            else:
                                side = "UNKNOWN"
                            
                            # Determine order type from type field
                            type_num = order.get('type', -1)
                            if type_num == 1:
                                order_type = "LIMIT"
                            elif type_num == 2:
                                order_type = "MARKET"
                            elif type_num == 4:
                                order_type = "STOP"
                            else:
                                order_type = f"TYPE{type_num}"
                            
                            # Determine status from status field
                            status_num = order.get('status', -1)
                            if status_num == 1:
                                status = "OPEN"
                            elif status_num == 2:
                                status = "FILLED"
                            elif status_num == 3:
                                status = "PENDING"
                            elif status_num == 5:
                                status = "CANCELLED"
                            else:
                                status = f"STATUS{status_num}"
                            
                            quantity = order.get('size', 0)
                            price = order.get('limitPrice') or order.get('stopPrice') or 0.0
                            custom_tag = order.get('customTag', '')
                            
                            print(f"{order_id:<12} {symbol:<8} {side:<6} {order_type:<8} {quantity:<10} ${price:<11.2f} {status:<10}")
                            if custom_tag:
                                print(f"             Tag: {custom_tag}")
                    else:
                        print("❌ No open orders found")
                
                elif command_lower.startswith("close "):
                    parts = command.split()
                    if len(parts) < 2 or len(parts) > 3:
                        print("❌ Usage: close <position_id> [quantity]")
                        print("   Example: close 12345 1")
                        continue
                    
                    position_id = parts[1]
                    quantity = int(parts[2]) if len(parts) == 3 else None
                    
                    # Confirm the close
                    print(f"\n⚠️  CONFIRM CLOSE POSITION:")
                    print(f"   Position ID: {position_id}")
                    if quantity:
                        print(f"   Quantity: {quantity}")
                    else:
                        print(f"   Quantity: Entire position")
                    print(f"   Account: {self.selected_account['name']}")
                    
                    confirm = input("   Confirm? (y/N): ").strip().lower()
                    if confirm != 'y':
                        print("❌ Close cancelled")
                        continue
                    
                    # Close the position
                    result = await self.close_position(position_id, quantity)
                    if "error" in result:
                        print(f"❌ Close failed: {result['error']}")
                    else:
                        print(f"✅ Position closed successfully!")
                        print(f"   Position ID: {position_id}")
                
                elif command_lower.startswith("cancel "):
                    parts = command.split()
                    if len(parts) != 2:
                        print("❌ Usage: cancel <order_id>")
                        print("   Example: cancel 12345")
                        continue
                    
                    order_id = parts[1]
                    
                    # Confirm the cancel
                    print(f"\n⚠️  CONFIRM CANCEL ORDER:")
                    print(f"   Order ID: {order_id}")
                    print(f"   Account: {self.selected_account['name']}")
                    
                    confirm = input("   Confirm? (y/N): ").strip().lower()
                    if confirm != 'y':
                        print("❌ Cancel cancelled")
                        continue
                    
                    # Cancel the order
                    result = await self.cancel_order(order_id)
                    if "error" in result:
                        print(f"❌ Cancel failed: {result['error']}")
                    else:
                        print(f"✅ Order cancelled successfully!")
                        print(f"   Order ID: {order_id}")
                
                elif command_lower.startswith("modify "):
                    parts = command.split()
                    if len(parts) < 2 or len(parts) > 4:
                        print("❌ Usage: modify <order_id> [new_quantity] [new_price]")
                        print("   Example: modify 12345 2 19500.00  (modify quantity and price)")
                        print("   Example: modify 12345 2           (modify quantity only)")
                        print("   Example: modify 12345 19500.00    (modify price only - for bracket orders)")
                        continue
                    
                    order_id = parts[1]
                    new_quantity = None
                    new_price = None
                    
                    # Parse arguments: could be quantity only, price only, or both
                    if len(parts) == 3:
                        # Could be quantity or price - check if it looks like a price (has decimal)
                        arg = parts[2]
                        if '.' in arg:
                            # Likely a price (decimal number)
                            try:
                                new_price = float(arg)
                            except ValueError:
                                print("❌ Invalid price format")
                                continue
                        else:
                            # Likely a quantity (integer)
                            try:
                                new_quantity = int(arg)
                            except ValueError:
                                print("❌ Invalid quantity format")
                                continue
                    elif len(parts) == 4:
                        # Both quantity and price
                        try:
                            new_quantity = int(parts[2])
                            new_price = float(parts[3])
                        except ValueError:
                            print("❌ Quantity must be an integer and price must be a decimal number")
                            continue
                    
                    # Confirm the modify
                    print(f"\n⚠️  CONFIRM MODIFY ORDER:")
                    print(f"   Order ID: {order_id}")
                    if new_quantity is not None:
                        print(f"   New Quantity: {new_quantity}")
                    if new_price is not None:
                        print(f"   New Price: ${new_price}")
                    if new_quantity is None and new_price is None:
                        print("❌ Must specify either quantity or price (or both)")
                        continue
                    print(f"   Account: {self.selected_account['name']}")
                    
                    confirm = input("   Confirm? (y/N): ").strip().lower()
                    if confirm != 'y':
                        print("❌ Modify cancelled")
                        continue
                    
                    # Get order type first to help with modification
                    orders = await self.get_open_orders()
                    order_type = None
                    for order in orders:
                        if str(order.get('id', '')) == str(order_id):
                            order_type = order.get('type')
                            break
                    
                    # Modify the order
                    result = await self.modify_order(order_id, new_quantity, new_price, order_type=order_type)
                    if "error" in result:
                        print(f"❌ Modify failed: {result['error']}")
                        # Provide helpful context for bracket orders
                        if "bracket order" in result['error'].lower():
                            print(f"\n💡 Tip: Bracket orders (stop loss/take profit) attached to positions")
                            print(f"   cannot have their size changed. You can:")
                            print(f"   - Modify the price only: modify {order_id} <quantity> <new_price>")
                            print(f"   - Close the position to remove bracket orders: close <position_id>")
                    else:
                        print(f"✅ Order modified successfully!")
                        print(f"   Order ID: {order_id}")
                
                elif command_lower.startswith("quote "):
                    parts = command.split()
                    if len(parts) != 2:
                        print("❌ Usage: quote <symbol>")
                        print("   Example: quote MNQ")
                        continue
                    
                    symbol = parts[1]
                    
                    # Get market quote
                    result = await self.get_market_quote(symbol)
                    if "error" in result:
                        print(f"❌ Quote failed: {result['error']}")
                    else:
                        print(f"\n📈 Market Quote for {symbol.upper()}:")
                        bid = result.get('bid', 'N/A')
                        ask = result.get('ask', 'N/A')
                        last = result.get('last', 'N/A')
                        volume = result.get('volume', 'N/A')
                        source = result.get('source', 'N/A')
                        print(f"   Bid: ${bid}")
                        print(f"   Ask: ${ask}")
                        print(f"   Last: ${last}")
                        print(f"   Volume: {volume}")
                        print(f"   Source: {source}")
                
                elif command_lower.startswith("depth "):
                    parts = command.split()
                    if len(parts) != 2:
                        print("❌ Usage: depth <symbol>")
                        print("   Example: depth MNQ")
                        continue
                    
                    symbol = parts[1]
                    
                    # Get market depth
                    result = await self.get_market_depth(symbol)
                    if "error" in result:
                        print(f"❌ Depth failed: {result['error']}")
                    else:
                        print(f"\n📊 Market Depth for {symbol.upper()}:")
                        bids = result.get('bids', [])
                        asks = result.get('asks', [])
                        
                        if not bids and not asks:
                            print("   No market depth data available")
                            print("   This might indicate:")
                            print("   - Market is closed")
                            print("   - Symbol is not actively traded")
                            print("   - Market depth data is not available for this symbol")
                        else:
                            if asks:
                                print("   Asks (Sell):")
                                for ask in asks[:5]:  # Show top 5
                                    price = ask.get('price', 0)
                                    size = ask.get('size', 0)
                                    print(f"     ${price:.2f} x {size}")
                            
                            if bids:
                                print("   Bids (Buy):")
                                for bid in bids[:5]:  # Show top 5
                                    price = bid.get('price', 0)
                                    size = bid.get('size', 0)
                                    print(f"     ${price:.2f} x {size}")
                
                elif command_lower.startswith("chart"):
                    # Open chart window GUI
                    parts = command.split()
                    symbol = parts[1] if len(parts) > 1 else "MNQ"
                    timeframe = parts[2] if len(parts) > 2 else "5m"
                    limit = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 100
                    
                    try:
                        from gui.chart_window import open_chart_window
                        print(f"📊 Opening chart window for {symbol} {timeframe} ({limit} bars)...")
                        print("💡 Close the chart window to return to terminal")
                        window = open_chart_window(self, symbol, timeframe, limit)
                        window.run()
                    except ImportError as e:
                        print(f"❌ GUI module not available: {e}")
                        print("   Install matplotlib: pip install matplotlib")
                    except Exception as e:
                        print(f"❌ Error opening chart window: {e}")
                
                elif command_lower.startswith("history "):
                    parts = command.split()
                    if len(parts) < 2:
                        print("❌ Usage: history <symbol> [timeframe] [limit|start_date end_date] [raw] [csv]")
                        print("   Bar count mode:")
                        print("     history MNQ 1m 50")
                        print("     history MNQ 5m 20 raw")
                        print("   Date range mode (gets ALL bars between dates):")
                        print("     history MNQ 1m 2024-11-01 2024-11-08")
                        print("     history MNQ 5m 2024-11-08T09:30 2024-11-08T16:00")
                        print("     history MNQ 1h 2024-11-01 2024-11-08 csv")
                        continue
                    
                    symbol = parts[1]
                    # Check for raw and csv flags (can be anywhere after symbol)
                    raw_flags = ("raw", "--raw", "-q", "-r")
                    csv_flags = ("csv", "--csv", "-c", "--export")
                    raw = any(p.lower() in raw_flags for p in parts[2:])
                    csv = any(p.lower() in csv_flags for p in parts[2:])
                    # Filter out flags when parsing other args
                    clean_parts = [p for p in parts[2:] if p.lower() not in raw_flags and p.lower() not in csv_flags]
                    timeframe = clean_parts[0] if len(clean_parts) > 0 else "1m"
                    
                    # Parse limit or date range
                    start_dt = None
                    end_dt = None
                    limit = 20  # Default
                    
                    if len(clean_parts) >= 3:
                        # Might be date range mode: history MNQ 1m 2024-11-01 2024-11-08
                        try:
                            from datetime import datetime as dt_parser
                            # Try common date formats
                            date_formats = [
                                "%Y-%m-%d",           # 2024-11-01
                                "%Y-%m-%dT%H:%M",     # 2024-11-01T09:30
                                "%Y-%m-%dT%H:%M:%S",  # 2024-11-01T09:30:00
                                "%Y/%m/%d",           # 2024/11/01
                                "%m/%d/%Y",           # 11/01/2024
                            ]
                            start_dt = None
                            end_dt = None
                            for fmt in date_formats:
                                try:
                                    start_dt = dt_parser.strptime(clean_parts[1], fmt)
                                    end_dt = dt_parser.strptime(clean_parts[2], fmt)
                                    break
                                except:
                                    continue
                            
                            if start_dt and end_dt:
                                print(f"📅 Date range mode: {start_dt} to {end_dt}")
                            else:
                                # Not valid dates, try as bar count
                                limit = int(clean_parts[1])
                        except:
                            # Not a valid date, fall back to bar count
                            try:
                                limit = int(clean_parts[1])
                            except:
                                limit = 20
                    elif len(clean_parts) >= 2:
                        # Bar count mode
                        try:
                            limit = int(clean_parts[1])
                        except:
                            limit = 20
                    
                    # Measure duration for performance insight
                    import time as _t
                    _t0 = _t.time()
                    # Get historical data
                    result = await self.get_historical_data(symbol, timeframe, limit, 
                                                           start_time=start_dt, end_time=end_dt)
                    _elapsed_ms = int((_t.time() - _t0) * 1000)
                    
                    # Check for error response
                    if isinstance(result, dict) and "error" in result:
                        print(f"❌ Historical data fetch failed: {result['error']}")
                        print(f"   Elapsed time: {_elapsed_ms} ms")
                        print("   Check logs for detailed error information")
                    elif not result or (isinstance(result, list) and len(result) == 0):
                        print(f"❌ No historical data available for {symbol}")
                        print(f"   Elapsed time: {_elapsed_ms} ms")
                        print("   This might indicate:")
                        print("   - Symbol is not available for historical data")
                        print("   - Timeframe is not supported")
                        print("   - Market is closed or data is not available")
                        print("   - Try a different symbol or timeframe")
                    else:
                        # In date range mode, show ALL bars. In bar count mode, show last N bars
                        display_bars = result if start_dt is not None else result[-limit:]
                        
                        # Handle CSV export
                        if csv:
                            csv_filename = self._export_to_csv(display_bars, symbol, timeframe)
                            if csv_filename:
                                print(f"✅ Exported {len(display_bars)} bars to {csv_filename}")
                                print(f"   Elapsed time: {_elapsed_ms} ms")
                            else:
                                print(f"❌ Failed to export CSV file")
                        
                        if raw:
                            for bar in display_bars:
                                time = bar.get('time', 'N/A')
                                open_price = bar.get('open', 0)
                                high = bar.get('high', 0)
                                low = bar.get('low', 0)
                                close = bar.get('close', 0)
                                volume = bar.get('volume', 0)
                                print(f"{time} {open_price} {high} {low} {close} {volume}")
                            print(f"fetched={len(display_bars)} elapsed_ms={_elapsed_ms}")
                        elif not csv:  # Only show formatted output if not CSV-only export
                            _count = len(display_bars)
                            mode_str = f"date range" if start_dt else f"last {limit} bars"
                            print(f"\n📊 Historical Data for {symbol.upper()} ({timeframe}) - {mode_str}, fetched={_count} in {_elapsed_ms} ms:")
                            # Only show psutil tip if slow AND psutil not installed
                            if _elapsed_ms > 10000:
                                try:
                                    import psutil
                                    _has_psutil = True
                                except ImportError:
                                    _has_psutil = False
                                if not _has_psutil:
                                    print(f"💡 Tip: Install 'psutil' to improve SDK performance: pip install psutil")
                                elif _elapsed_ms > 15000:
                                    print(f"⚠️  Performance note: Consider caching SDK client connections for faster fetches")
                            # Align headers properly - Time column needs 26 chars for ISO timestamps with timezone
                            print(f"{'Time':<26} {'Open':<12} {'High':<12} {'Low':<12} {'Close':<12} {'Volume':<10}")
                            print("-" * 100)
                            for bar in display_bars:  # Show bars based on mode (all for date range, last N for bar count)
                                # Get timestamp - prioritize parsed 'time'/'timestamp' keys
                                time = bar.get('time') or bar.get('timestamp') or ''
                                
                                # Format timestamp nicely (convert UTC to ET for display)
                                if time:
                                    if len(str(time)) > 19:
                                        try:
                                            # Parse ISO format and convert to ET for display
                                            from datetime import datetime as _dt
                                            import pytz
                                            dt = _dt.fromisoformat(str(time).replace('Z', '+00:00'))
                                            # Ensure timezone-aware (UTC)
                                            if dt.tzinfo is None:
                                                dt = dt.replace(tzinfo=timezone.utc)
                                            # Convert to ET
                                            et_tz = pytz.timezone('America/New_York')
                                            dt_et = dt.astimezone(et_tz)
                                            time = dt_et.strftime('%Y-%m-%d %H:%M:%S ET')
                                        except Exception as e:
                                            # Fallback: show UTC time if conversion fails
                                            try:
                                                dt = _dt.fromisoformat(str(time).replace('Z', '+00:00'))
                                                if dt.tzinfo is None:
                                                    dt = dt.replace(tzinfo=timezone.utc)
                                                time = dt.strftime('%Y-%m-%d %H:%M:%S UTC')
                                            except:
                                                time = str(time)[:19] if len(str(time)) > 19 else str(time)
                                            logger.debug(f"Failed to convert timestamp to ET {time}: {e}")
                                else:
                                    time = "N/A"
                                    logger.debug(f"Empty timestamp in bar display. Bar keys: {list(bar.keys())}")
                                
                                open_price = bar.get('open', 0)
                                high = bar.get('high', 0)
                                low = bar.get('low', 0)
                                close = bar.get('close', 0)
                                volume = bar.get('volume', 0)
                                print(f"{time:<26} ${open_price:<11.2f} ${high:<11.2f} ${low:<11.2f} ${close:<11.2f} {volume:<10}")
                
                elif command_lower == "monitor":
                    # Monitor position changes and adjust bracket orders
                    result = await self.monitor_position_changes()
                    if "error" in result:
                        print(f"❌ Monitor failed: {result['error']}")
                    else:
                        if result.get('message'):
                            print(f"ℹ️  {result['message']}")
                        else:
                            print(f"✅ Position monitoring completed!")
                            print(f"   Positions checked: {result.get('positions', 0)}")
                            print(f"   Adjustments made: {result.get('adjustments', 0)}")
                            if result.get('cancelled_orders', 0) > 0:
                                print(f"   Orphaned orders cancelled: {result.get('cancelled_orders', 0)}")
                
                elif command_lower == "bracket_monitor":
                    # Monitor bracket positions and manage orders
                    result = await self.monitor_all_bracket_positions()
                    if "error" in result:
                        print(f"❌ Bracket monitor failed: {result['error']}")
                    else:
                        print(f"✅ Bracket monitoring completed!")
                        print(f"   Monitored positions: {result.get('monitored_positions', 0)}")
                        print(f"   Removed positions: {result.get('removed_positions', 0)}")
                        if result.get('results'):
                            for pos_id, pos_result in result['results'].items():
                                print(f"   Position {pos_id}: {pos_result.get('message', 'No changes')}")
                
                elif command_lower == "activate_monitor":
                    # Manually activate monitoring for testing
                    self._monitoring_active = True
                    self._last_order_time = datetime.now()
                    print("✅ Monitoring manually activated")
                    print("   Monitoring will be active for 30 seconds")
                
                elif command_lower == "deactivate_monitor":
                    # Manually deactivate monitoring
                    self._monitoring_active = False
                    self._last_order_time = None
                    print("✅ Monitoring manually deactivated")
                
                elif command_lower == "check_fills":
                    # Check for filled orders and send notifications
                    result = await self.check_order_fills()
                    if "error" in result:
                        print(f"❌ Check fills failed: {result['error']}")
                    else:
                        print(f"✅ Order fill check completed!")
                        print(f"   Orders checked: {result.get('checked_orders', 0)}")
                        print(f"   New fills found: {result.get('filled_orders', 0)}")
                        if result.get('new_fills'):
                            print(f"   Fill notifications sent for: {', '.join(result['new_fills'])}")
                
                elif command_lower == "auto_fills":
                    # Enable automatic fill checking every 30 seconds
                    print("✅ Automatic fill checking enabled")
                    print("   Checking for fills every 30 seconds...")
                    print("   Use 'stop_auto_fills' to disable")
                    
                    # Start background task for auto fills
                    import asyncio
                    asyncio.create_task(self._auto_fill_checker())
                
                elif command_lower == "stop_auto_fills":
                    # Disable automatic fill checking
                    self._auto_fills_enabled = False
                    print("✅ Automatic fill checking disabled")
                
                elif command_lower == "clear_notifications":
                    # Clear notification cache to re-check all orders
                    self._notified_orders.clear()
                    self._notification_warmup_done.clear()
                    self._notified_positions.clear()
                    if hasattr(self, '_tracked_positions'):
                        self._tracked_positions.clear()
                    print("✅ Notification cache cleared - will re-check all orders and positions")
                
                elif command_lower == "test_fills":
                    # Test fill checking with detailed output
                    print("🔄 Testing fill checking...")
                    result = await self.check_order_fills()
                    if "error" in result:
                        print(f"❌ Test failed: {result['error']}")
                    else:
                        print(f"✅ Fill check completed!")
                        print(f"   Orders checked: {result.get('checked_orders', 0)}")
                        print(f"   New fills found: {result.get('filled_orders', 0)}")
                        if result.get('new_fills'):
                            print(f"   Fill notifications sent for: {', '.join(result['new_fills'])}")
                        else:
                            print("   No new fills found")
                
                elif command_lower == "account_info":
                    # Get detailed account information
                    result = await self.get_account_info()
                    if "error" in result:
                        print(f"❌ Account info failed: {result['error']}")
                    else:
                        print(f"\n📊 Account Information:")
                        print(f"   Account ID: {result.get('id', 'N/A')}")
                        print(f"   Name: {result.get('name', 'N/A')}")
                        print(f"   Balance: ${result.get('balance', 0):,.2f}")
                        print(f"   Status: {result.get('status', 'unknown')}")
                        print(f"   Type: {result.get('type', 'unknown')}")
                        if 'note' in result:
                            print(f"\n   ℹ️  {result['note']}")
                        # Show any additional fields that were returned
                        extra_fields = {k: v for k, v in result.items() 
                                      if k not in ['id', 'name', 'balance', 'status', 'type', 'note', 'error']}
                        if extra_fields:
                            print(f"\n   Additional Info:")
                            for key, value in extra_fields.items():
                                print(f"   - {key}: {value}")
                
                elif command_lower == "account_state":
                    # Show real-time account state from tracker
                    state = self.account_tracker.get_state()
                    
                    print(f"\n📊 Real-Time Account State:")
                    print(f"   Account ID: {state['account_id']}")
                    print(f"   Starting Balance: ${state['starting_balance']:,.2f}")
                    print(f"   Current Balance: ${state['current_balance']:,.2f}")
                    print(f"   Realized PnL: ${state['realized_pnl']:,.2f}")
                    print(f"   Unrealized PnL: ${state['unrealized_pnl']:,.2f}")
                    print(f"   Total PnL: ${state['total_pnl']:,.2f}")
                    print(f"   Highest EOD Balance: ${state['highest_eod_balance']:,.2f}")
                    print(f"\n   Open Positions: {state['position_count']}")
                    if state['positions']:
                        print(f"   Position Details:")
                        for symbol, pos in state['positions'].items():
                            print(f"      {symbol}: {pos['quantity']} @ ${pos['entry_price']:.2f} (PnL: ${pos['unrealized_pnl']:.2f})")
                    
                    print(f"\n   Last Updated: {state['last_update']}")
                    print(f"   📝 Note: Real-time tracking based on local state + API data")
                
                elif command_lower == "compliance":
                    # Check compliance status
                    compliance = self.account_tracker.check_compliance()
                    state = self.account_tracker.get_state()
                    
                    print(f"\n✅ Compliance Status:")
                    print(f"   Account Type: {state['account_type']}")
                    print(f"   Is Compliant: {'✓ YES' if compliance['is_compliant'] else '❌ NO'}")
                    
                    print(f"\n   Daily Loss Limit (DLL):")
                    if compliance['dll_limit']:
                        print(f"      Limit: ${compliance['dll_limit']:,.2f}")
                        print(f"      Used: ${compliance['dll_used']:,.2f}")
                        print(f"      Remaining: ${compliance['dll_remaining']:,.2f}")
                        print(f"      Status: {'✓ OK' if not compliance['dll_violated'] else '❌ VIOLATED'}")
                    else:
                        print(f"      No DLL limit set")
                    
                    print(f"\n   Maximum Loss Limit (MLL):")
                    if compliance['mll_limit']:
                        print(f"      Limit: ${compliance['mll_limit']:,.2f}")
                        print(f"      Used: ${compliance['mll_used']:,.2f}")
                        print(f"      Remaining: ${compliance['mll_remaining']:,.2f}")
                        print(f"      Status: {'✓ OK' if not compliance['mll_violated'] else '❌ VIOLATED'}")
                    else:
                        print(f"      No MLL limit set")
                    
                    print(f"\n   Trailing Drawdown:")
                    print(f"      Highest EOD Balance: ${state['highest_eod_balance']:,.2f}")
                    print(f"      Current Balance: ${state['current_balance']:,.2f}")
                    print(f"      Trailing Loss: ${compliance['trailing_loss']:,.2f}")
                    
                    if compliance['violations']:
                        print(f"\n   ⚠️  Violations:")
                        for violation in compliance['violations']:
                            print(f"      - {violation}")
                
                elif command_lower == "risk":
                    # Show risk metrics
                    state = self.account_tracker.get_state()
                    compliance = self.account_tracker.check_compliance()
                    
                    print(f"\n⚠️  Risk Metrics:")
                    print(f"   Account: {self.selected_account.get('name', 'N/A')}")
                    print(f"   Current Balance: ${state['current_balance']:,.2f}")
                    print(f"   Total PnL: ${state['total_pnl']:,.2f} ({(state['total_pnl'] / state['starting_balance'] * 100):.2f}%)")
                    
                    print(f"\n   Daily Loss:")
                    if compliance['dll_limit']:
                        dll_pct = (compliance['dll_used'] / compliance['dll_limit'] * 100) if compliance['dll_limit'] else 0
                        print(f"      Used: ${compliance['dll_used']:,.2f} / ${compliance['dll_limit']:,.2f} ({dll_pct:.1f}%)")
                        print(f"      Remaining: ${compliance['dll_remaining']:,.2f}")
                    else:
                        print(f"      No limit set")
                    
                    print(f"\n   Maximum Loss:")
                    if compliance['mll_limit']:
                        mll_pct = (compliance['mll_used'] / compliance['mll_limit'] * 100) if compliance['mll_limit'] else 0
                        print(f"      Used: ${compliance['mll_used']:,.2f} / ${compliance['mll_limit']:,.2f} ({mll_pct:.1f}%)")
                        print(f"      Remaining: ${compliance['mll_remaining']:,.2f}")
                    else:
                        print(f"      No limit set")
                    
                    print(f"\n   Open Positions Risk:")
                    print(f"      Position Count: {state['position_count']}")
                    print(f"      Unrealized PnL: ${state['unrealized_pnl']:,.2f}")
                    
                    if state['position_count'] > 0:
                        total_exposure = sum(abs(pos['quantity'] * pos['entry_price']) for pos in state['positions'].values())
                        print(f"      Total Exposure: ${total_exposure:,.2f}")
                        if state['current_balance'] > 0:
                            leverage = total_exposure / state['current_balance']
                            print(f"      Leverage: {leverage:.2f}x")
                
                elif command_lower in ["drawdown", "max_loss"]:
                    # Show max loss limit and drawdown information
                    account_id = self.selected_account['id'] if self.selected_account else None
                    if not account_id:
                        print("❌ No account selected")
                        continue
                    
                    # Get account info and balance
                    account_info = await self.get_account_info(account_id)
                    balance = await self.get_account_balance(account_id)
                    
                    if balance is None:
                        print("❌ Could not retrieve account balance")
                        continue
                    
                    # Extract max loss limit from account info if available
                    max_loss_limit = None
                    starting_balance = None
                    daily_loss_limit = None
                    
                    # Account info might not be available due to API limitations
                    if isinstance(account_info, dict) and "error" not in account_info:
                        max_loss_limit = account_info.get('maxLossLimit') or account_info.get('maxLoss') or account_info.get('maxDailyLoss')
                        starting_balance = account_info.get('startingBalance') or account_info.get('initialBalance') or account_info.get('accountBalance')
                        daily_loss_limit = account_info.get('dailyLossLimit') or account_info.get('maxDailyLoss')
                    else:
                        # Use reasonable defaults based on account type
                        account_name = self.selected_account.get('name', '')
                        account_type = self.selected_account.get('type', '')
                        
                        # Estimate limits based on account type/name
                        if 'PRAC' in account_name or account_type == 'practice':
                            max_loss_limit = 2500.00  # Common practice account limit
                            daily_loss_limit = 1000.00
                        elif '50K' in account_name:
                            max_loss_limit = 2000.00
                            daily_loss_limit = 1000.00
                        elif '150K' in account_name:
                            max_loss_limit = 3000.00
                            daily_loss_limit = 1500.00
                        elif 'EXPRESS' in account_name:
                            max_loss_limit = 500.00
                            daily_loss_limit = 250.00
                        
                        logger.info(f"Account info API unavailable, using estimated limits for {account_name}")
                    
                    # Calculate drawdown
                    drawdown = None
                    drawdown_percent = None
                    if starting_balance and starting_balance > 0:
                        drawdown = starting_balance - balance
                        drawdown_percent = (drawdown / starting_balance) * 100
                    
                    # Calculate remaining loss capacity
                    remaining_loss = None
                    if max_loss_limit:
                        remaining_loss = max_loss_limit - (drawdown if drawdown else 0)
                    
                    # Display information
                    print(f"\n📉 Risk & Drawdown Information:")
                    print(f"   Account: {self.selected_account.get('name', account_id)}")
                    print(f"   Current Balance: ${balance:,.2f}")
                    
                    if starting_balance:
                        print(f"   Starting Balance: ${starting_balance:,.2f}")
                    
                    if drawdown is not None:
                        print(f"   Drawdown: ${drawdown:,.2f} ({drawdown_percent:.2f}%)")
                        if drawdown > 0:
                            print(f"   ⚠️  Account is down ${drawdown:,.2f}")
                        else:
                            print(f"   ✅ Account is up ${abs(drawdown):,.2f}")
                    
                    if max_loss_limit:
                        print(f"   Max Loss Limit: ${max_loss_limit:,.2f}")
                        if remaining_loss is not None:
                            if remaining_loss > 0:
                                print(f"   Remaining Loss Capacity: ${remaining_loss:,.2f}")
                            else:
                                print(f"   ⚠️  Max loss limit reached!")
                    
                    if daily_loss_limit:
                        print(f"   Daily Loss Limit: ${daily_loss_limit:,.2f}")
                    
                    # Show note if using estimated limits
                    if isinstance(account_info, dict) and "error" in account_info:
                        print(f"\n   ℹ️  Note: Loss limits are estimated based on account type")
                        print(f"   API endpoints for detailed account info are not available")
                    
                    # Show positions P&L if available
                    try:
                        positions = await self.get_open_positions(account_id)
                        if positions:
                            total_unrealized_pnl = sum(float(p.get('unrealizedPnl', 0)) for p in positions)
                            if total_unrealized_pnl != 0:
                                print(f"   Open Positions P&L: ${total_unrealized_pnl:,.2f}")
                    except Exception:
                        pass
                
                elif command_lower == "trades" or command_lower.startswith("trades "):
                    # List trades between optional dates, default to current session
                    parts = command.split()
                    start_date_str = None
                    end_date_str = None
                    
                    if len(parts) > 1:
                        start_date_str = parts[1]
                    if len(parts) > 2:
                        end_date_str = parts[2]
                    
                    account_id = self.selected_account['id'] if self.selected_account else None
                    if not account_id:
                        print("❌ No account selected")
                        continue
                    
                    # If no dates provided, use current trading session
                    if not start_date_str and not end_date_str:
                        from datetime import datetime
                        import pytz
                        session_start, session_end = self._get_trading_session_dates()
                        start_date_str = session_start.isoformat()
                        end_date_str = session_end.isoformat()
                        print(f"\n📊 Trades for Current Trading Session:")
                        print(f"   Session: {session_start.strftime('%Y-%m-%d %H:%M:%S %Z')} to {session_end.strftime('%Y-%m-%d %H:%M:%S %Z')}")
                    else:
                        print(f"\n📊 Trades from {start_date_str} to {end_date_str}")
                    
                    # Get order history (filled orders only)
                    orders = await self.get_order_history(
                        account_id=account_id,
                        limit=1000,
                        start_timestamp=start_date_str,
                        end_timestamp=end_date_str
                    )
                    
                    if orders:
                        # Consolidate individual orders into completed trades
                        consolidated_trades = self._consolidate_orders_into_trades(orders)
                        
                        if consolidated_trades:
                            # Calculate statistics
                            stats = self._calculate_trade_statistics(consolidated_trades)
                            
                            # Display consolidated trades
                            print(f"\n{'Symbol':<8} {'Side':<6} {'Qty':<5} {'Entry':<12} {'Exit':<12} {'P&L':<12} {'Entry Time':<20} {'Exit Time':<20}")
                            print("-" * 110)
                            for trade in consolidated_trades:
                                symbol = trade.get('symbol', 'N/A')
                                side = trade.get('side', 'N/A')
                                quantity = trade.get('quantity', 0)
                                entry_price = trade.get('entry_price', 0.0)
                                exit_price = trade.get('exit_price', 0.0)
                                pnl = trade.get('pnl', 0.0)
                                entry_time = trade.get('entry_time', 'N/A')
                                exit_time = trade.get('exit_time', 'N/A')
                                
                                # Format timestamps - handle both datetime objects and strings
                                if entry_time and entry_time != 'N/A':
                                    try:
                                        from datetime import datetime
                                        if isinstance(entry_time, datetime):
                                            entry_time = entry_time.strftime('%Y-%m-%d %H:%M:%S')
                                        elif isinstance(entry_time, str):
                                            dt = datetime.fromisoformat(entry_time.replace('Z', '+00:00'))
                                            entry_time = dt.strftime('%Y-%m-%d %H:%M:%S')
                                    except Exception as e:
                                        logger.debug(f"Could not format entry_time: {e}")
                                        entry_time = str(entry_time) if entry_time else 'N/A'
                                
                                if exit_time and exit_time != 'N/A':
                                    try:
                                        from datetime import datetime
                                        if isinstance(exit_time, datetime):
                                            exit_time = exit_time.strftime('%Y-%m-%d %H:%M:%S')
                                        elif isinstance(exit_time, str):
                                            dt = datetime.fromisoformat(exit_time.replace('Z', '+00:00'))
                                            exit_time = dt.strftime('%Y-%m-%d %H:%M:%S')
                                    except Exception as e:
                                        logger.debug(f"Could not format exit_time: {e}")
                                        exit_time = str(exit_time) if exit_time else 'N/A'
                                
                                # Color code P&L (green for positive, red for negative)
                                pnl_str = f"${pnl:>11.2f}"
                                
                                print(f"{symbol:<8} {side:<6} {quantity:<5} ${entry_price:<11.2f} ${exit_price:<11.2f} {pnl_str:<12} {entry_time:<20} {exit_time:<20}")
                            
                            # Display statistics
                            print(f"\n📈 Trade Statistics:")
                            print(f"   Total Trades: {stats['total_trades']}")
                            print(f"   Winning: {stats['winning_trades']} | Losing: {stats['losing_trades']} | Break Even: {stats['break_even_trades']}")
                            print(f"   Win Rate: {stats['win_rate']}%")
                            print(f"   Total P&L: ${stats['total_pnl']:,.2f}")
                            print(f"   Average P&L: ${stats['average_pnl']:,.2f}")
                            if stats['average_win'] > 0:
                                print(f"   Average Win: ${stats['average_win']:,.2f}")
                            if stats['average_loss'] < 0:
                                print(f"   Average Loss: ${stats['average_loss']:,.2f}")
                            if stats['largest_win'] > 0:
                                print(f"   Largest Win: ${stats['largest_win']:,.2f}")
                            if stats['largest_loss'] < 0:
                                print(f"   Largest Loss: ${stats['largest_loss']:,.2f}")
                        else:
                            print("\n⚠️  No completed trades found")
                            print(f"   Found {len(orders)} individual filled orders, but no matching entry/exit pairs")
                            print("   Trades are shown only when entry and exit orders can be matched using FIFO.")
                    else:
                        print("❌ No orders found for this period")
                
                else:
                    print("❌ Unknown command. Available commands:")
                    print("   trade, limit, bracket, native_bracket, stop_bracket, stop, trail, positions, orders,")
                    print("   close, cancel, modify, quote, depth, history, monitor, flatten, contracts, accounts,")
                    print("   switch_account, account_info, account_state, compliance, risk, drawdown, trades, help, quit")
                    print("   Use ↑/↓ arrows for command history, Tab for completion")
                    print("   Type 'help' for detailed command information")
                    
            except KeyboardInterrupt:
                print("\n👋 Exiting trading interface.")
                break
            except Exception as e:
                print(f"❌ Error: {str(e)}")
                logger.error(f"Trading interface error: {str(e)}")

def main():
    """
    Main entry point for the trading bot.
    """
    import argparse
    
    # Parse command-line arguments
    parser = argparse.ArgumentParser(description='TopStepX Trading Bot - Real API Version')
    parser.add_argument('-v', '--verbose', action='store_true', 
                       help='Enable verbose/debug logging')
    args = parser.parse_args()
    
    # Reconfigure logging if verbose mode is enabled
    if args.verbose:
        # Set all loggers to DEBUG level
        logging.getLogger().setLevel(logging.DEBUG)
        for handler in logging.getLogger().handlers:
            handler.setLevel(logging.DEBUG)
        logger.info("Verbose logging enabled")
        print("📊 Verbose logging enabled - all logs will appear in terminal")
    else:
        # Ensure console only shows WARNING+ in non-verbose mode
        for handler in logging.getLogger().handlers:
            if isinstance(handler, logging.StreamHandler) and handler.stream == sys.stdout:
                handler.setLevel(logging.WARNING)
    
    print("TopStepX Trading Bot - Real API Version")
    print("=======================================")
    print()
    print("This bot will help you:")
    print("1. Authenticate with TopStepX API")
    print("2. List your active accounts")
    print("3. Select which account to trade on")
    print("4. Place live market orders")
    print()
    print("ℹ️  Detailed logs are being written to: trading_bot.log")
    print("   (Terminal will only show warnings and errors)")
    print()
    
    # Check for environment variables
    api_key = os.getenv('PROJECT_X_API_KEY') or os.getenv('TOPSETPX_API_KEY')
    username = os.getenv('PROJECT_X_USERNAME') or os.getenv('TOPSETPX_USERNAME')
    
    if not api_key or not username:
        print("⚠️  Environment variables not found.")
        print("Please set your credentials:")
        print("  export PROJECT_X_API_KEY='your_api_key_here'")
        print("  export PROJECT_X_USERNAME='your_username_here'")
        print("  OR")
        print("  export TOPSETPX_API_KEY='your_api_key_here'")
        print("  export TOPSETPX_USERNAME='your_username_here'")
        print()
        print("Or provide them manually:")
        
        if not api_key:
            api_key = input("Enter your TopStepX API Key: ").strip()
        if not username:
            username = input("Enter your TopStepX Username: ").strip()
        
        if not api_key or not username:
            print("❌ Both API key and username are required. Exiting.")
            return
    
    # Initialize and run the bot
    bot = TopStepXTradingBot(api_key=api_key, username=username)
    
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        print("\n\n👋 Bot stopped by user.")
    except Exception as e:
        print(f"\n❌ Unexpected error: {str(e)}")

if __name__ == "__main__":
    main()
# Force Railway redeploy
