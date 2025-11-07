#!/usr/bin/env python3
"""
TopStepX Trading Bot - Real API Implementation
A dynamic trading bot for TopStepX prop firm futures accounts.

This bot provides:
1. Real authentication with TopStepX ProjectX API
2. Live account listing from API
3. Account selection for trading
4. Live market order placement
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
from pathlib import Path
from typing import List, Dict, Optional, Any
from datetime import datetime, timedelta
from threading import Lock
from collections import deque, OrderedDict
import time
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from signalrcore.hub_connection_builder import HubConnectionBuilder
from discord_notifier import DiscordNotifier
from signalrcore.transport.websockets.websocket_transport import WebsocketTransport
from account_tracker import AccountTracker

# Optional ProjectX SDK adapter
try:
    import sdk_adapter  # local adapter around project-x-py
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
file_handler.setLevel(getattr(logging, log_level, logging.INFO))
file_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))

console_handler = logging.StreamHandler(sys.stdout)
console_handler.setLevel(getattr(logging, log_level, logging.INFO))
console_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))

logging.basicConfig(
    level=getattr(logging, log_level, logging.INFO),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[file_handler, console_handler],
    force=True  # Override any existing configuration
)
logger = logging.getLogger(__name__)
logger.info("Logging initialized - file: trading_bot.log, console: stdout")

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
        self.session_token = None
        self.token_expiry = None  # Track JWT token expiration time
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
        self._market_hub_quote_event = os.getenv("PROJECT_X_QUOTE_EVENT", "GatewayQuote")
        self._market_hub_subscribe_method = os.getenv("PROJECT_X_SUBSCRIBE_METHOD", "SubscribeContractQuotes")
        self._market_hub_unsubscribe_method = os.getenv("PROJECT_X_UNSUBSCRIBE_METHOD", "UnsubscribeContractQuotes")
        self._subscribed_symbols = set()
        self._pending_symbols = set()
        
        # WebSocket connection pool: {url: hub_connection}
        # Reuses connections for multiple symbols to reduce overhead
        self._websocket_pool: Dict[str, Any] = {}
        self._websocket_pool_lock = Lock()
        self._websocket_pool_max_size = int(os.getenv('WEBSOCKET_POOL_MAX_SIZE', '5'))  # Max 5 concurrent connections
        
        # Initialize Discord notifier
        self.discord_notifier = DiscordNotifier()
        
        # Initialize real-time account state tracker
        self.account_tracker = AccountTracker()
        logger.debug("Account tracker initialized")
        
        # Order counter for unique custom tags
        self._order_counter = 0
        
        # Initialize rate limiter
        # Default: 60 calls per 60 seconds (1 call/second)
        # Configurable via environment variables
        rate_limit_max = int(os.getenv('API_RATE_LIMIT_MAX', '60'))
        rate_limit_period = int(os.getenv('API_RATE_LIMIT_PERIOD', '60'))
        self._rate_limiter = RateLimiter(max_calls=rate_limit_max, period=rate_limit_period)
        logger.debug(f"Rate limiter initialized: {rate_limit_max} calls per {rate_limit_period} seconds")
        
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
    async def _ensure_market_socket_started(self) -> None:
        """
        Start market data socket only when actually needed (e.g., for quotes/depth).
        Uses connection pooling to reuse WebSocket connections for multiple symbols.
        """
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
            .with_automatic_reconnect({"type": "raw", "keep_alive_interval": 10, "reconnect_interval": 1, "max_attempts": 0})
            .build()
        )

        def on_open():
            logger.info("SignalR Market Hub connected")
            self._market_hub_connected = True
            # Flush any pending subscriptions
            try:
                for sym in list(self._pending_symbols):
                    cid = self._get_contract_id(sym)
                    self._market_hub.send(self._market_hub_subscribe_method, [cid])
                    self._subscribed_symbols.add(sym)
                    self._pending_symbols.discard(sym)
                    logger.info(f"Subscribed (flush) to {sym} via {cid}")
            except Exception as e:
                logger.debug(f"Flush subscribe failed: {e}")

        def on_close():
            logger.warning("SignalR Market Hub disconnected")
            self._market_hub_connected = False

        def on_error(err):
            logger.error(f"SignalR Market Hub error: {err}")

        def on_quote(*args):
            try:
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
                    # Fallback to symbolId in payload
                    sym_id = (data.get("symbol") or data.get("symbolId") or "").upper()
                    if sym_id and "." in sym_id:
                        parts = sym_id.split(".")
                        symbol = parts[-1].upper()
                    elif sym_id:
                        symbol = sym_id
                if not symbol:
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
        event_names = [self._market_hub_quote_event, "GatewayQuote"]
        seen = set()
        for ev in event_names:
            if ev and ev not in seen:
                try:
                    hub.on(ev, on_quote)
                    seen.add(ev)
                except Exception:
                    pass
        
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
        sym = symbol.upper()
        if sym in self._subscribed_symbols:
            return
        if not self._market_hub_connected:
            self._pending_symbols.add(sym)
            logger.debug(f"Queued subscription for {sym} until hub connects")
            return
        try:
            # Per docs, subscribe by contract ID
            cid = self._get_contract_id(sym)
            self._market_hub.send(self._market_hub_subscribe_method, [cid])
            self._subscribed_symbols.add(sym)
            logger.info(f"Subscribed to live quotes for {sym} via {cid}")
        except Exception as e:
            logger.warning(f"Failed to subscribe to quotes for {sym}: {e}")
    
    async def _ensure_depth_subscription(self, symbol: str) -> None:
        """Subscribe to market depth data via SignalR."""
        sym = symbol.upper()
        if not self._market_hub_connected:
            logger.debug(f"Market hub not connected, cannot subscribe to depth for {sym}")
            return
        try:
            # Subscribe to depth data - try different possible method names
            cid = self._get_contract_id(sym)
            # Try only the most likely depth subscription methods to reduce errors
            depth_methods = [
                "SubscribeOrderBook",  # Most common for depth data
                "SubscribeLevel2"      # Alternative depth method
            ]
            
            for method in depth_methods:
                try:
                    self._market_hub.send(method, [cid])
                    logger.info(f"Subscribed to depth data for {sym} via {cid} using {method}")
                except Exception as e:
                    logger.debug(f"Depth method {method} failed: {e}")
                    continue
                
        except Exception as e:
            logger.warning(f"Failed to subscribe to depth for {sym}: {e}")
        
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
                request_kwargs['json'] = data
                if 'Content-Type' not in request_kwargs['headers']:
                    request_kwargs['headers']['Content-Type'] = 'application/json'
            
            logger.debug(f"HTTP {method} request to {endpoint}")
            
            # Make request using session (connection pooling enabled)
            response = self._http_session.request(
                method=method,
                url=url,
                **request_kwargs
            )
            
            # Handle response
            try:
                response.raise_for_status()  # Raise exception for bad status codes
            except requests.exceptions.HTTPError as e:
                if suppress_errors:
                    logger.debug(f"HTTP error {response.status_code}: {e}")
                else:
                    logger.error(f"HTTP error {response.status_code}: {e}")
                return {"error": f"HTTP {response.status_code}: {str(e)}"}
            
            # Parse JSON response
            try:
                # Handle empty response (common for successful operations)
                if not response.text.strip():
                    return {"success": True, "message": "Operation completed successfully"}
                
                response_data = response.json()
                return response_data
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse JSON response: {e}")
                logger.error(f"Raw response: {response.text[:500]}")  # Log first 500 chars
                return {"error": f"Invalid JSON response: {e}"}
                
        except requests.exceptions.Timeout:
            logger.error(f"HTTP request timed out after {api_timeout}s")
            return {"error": "Request timed out"}
        except requests.exceptions.ConnectionError as e:
            logger.error(f"HTTP connection error: {e}")
            return {"error": f"Connection error: {str(e)}"}
        except Exception as e:
            logger.error(f"HTTP request failed: {str(e)}")
            return {"error": str(e)}
    
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
        
        Returns:
            bool: True if token needs refresh
        """
        if not self.session_token or not self.token_expiry:
            return True
        
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        # Refresh if token expires in less than 5 minutes
        buffer = timedelta(minutes=5)
        return now >= (self.token_expiry - buffer)
    
    async def _ensure_valid_token(self) -> bool:
        """
        Ensure we have a valid, non-expired JWT token.
        Automatically refreshes if needed.
        
        Returns:
            bool: True if token is valid/refreshed successfully
        """
        if not self._is_token_expired():
            return True
        
        logger.info("Token expired or missing, refreshing...")
        return await self.authenticate()
    
    async def list_accounts(self) -> List[Dict]:
        """
        List all active accounts for the authenticated user.
        
        Returns:
            List[Dict]: List of account information
        """
        try:
            logger.info("Fetching active accounts from TopStepX API...")
            
            if not self.session_token:
                logger.error("No session token available. Please authenticate first.")
                return []
            
            # Make real API call to get accounts using session token
            headers = {
                "accept": "text/plain",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.session_token}"
            }
            
            # Search for active accounts
            search_data = {
                "onlyActiveAccounts": True
            }
            
            response = self._make_curl_request("POST", "/api/Account/search", data=search_data, headers=headers)
            
            if "error" in response:
                logger.error(f"Failed to fetch accounts: {response['error']}")
                return []
            
            # Parse the response - adjust based on actual API response structure
            if isinstance(response, list):
                accounts = response
            elif isinstance(response, dict) and "accounts" in response:
                accounts = response["accounts"]
            elif isinstance(response, dict) and "data" in response:
                accounts = response["data"]
            elif isinstance(response, dict) and "result" in response:
                accounts = response["result"]
            else:
                logger.warning(f"Unexpected API response format: {response}")
                accounts = []
            
            # Normalize account data structure
            normalized_accounts = []
            for account in accounts:
                # Determine account type from name or other fields
                account_name = account.get("name") or account.get("accountName", "Unknown Account")
                account_type = "unknown"
                
                if "PRAC" in account_name.upper():
                    account_type = "practice"
                elif "150KTC" in account_name.upper():
                    account_type = "eval"
                elif "EXPRESS" in account_name.upper():
                    account_type = "funded"
                elif "EVAL" in account_name.upper():
                    account_type = "evaluation"
                
                normalized_account = {
                    "id": account.get("id") or account.get("accountId"),
                    "name": account_name,
                    "status": account.get("status", "active"),
                    "balance": account.get("balance", 0.0),
                    "currency": account.get("currency", "USD"),
                    "account_type": account_type
                }
                normalized_accounts.append(normalized_account)
            
            logger.info(f"Found {len(normalized_accounts)} active accounts")
            return normalized_accounts
            
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
            target_account = account_id or (self.selected_account['id'] if self.selected_account else None)
            
            if not target_account:
                return {"error": "No account selected"}
            
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
                return {
                    "id": self.selected_account.get('id'),
                    "name": self.selected_account.get('name', 'Unknown'),
                    "balance": self.selected_account.get('balance', 0),
                    "status": self.selected_account.get('status', 'unknown'),
                    "type": self.selected_account.get('type', 'unknown'),
                    "note": "Detailed account info endpoints not available - showing cached basic info"
                }
            return {"error": "Could not fetch account info - no API endpoints available"}
            
        except Exception as e:
            logger.error(f"Failed to fetch account info: {str(e)}")
            return {"error": str(e)}
    
    def _get_contract_id(self, symbol: str) -> str:
        """
        Convert trading symbol to TopStepX contract ID format.
        
        First tries to find the contract ID from the cached contract list.
        Falls back to hardcoded mappings if cache is unavailable.
        
        Args:
            symbol: Trading symbol (e.g., "ES", "NQ", "MNQ", "YM")
            
        Returns:
            str: Contract ID in TopStepX format
        """
        symbol = symbol.upper()
        
        # Try to find in cached contract list first
        with self._contract_cache_lock:
            if self._contract_cache is not None:
                contracts = self._contract_cache['contracts']
                # Look for contract matching the symbol
                for contract in contracts:
                    # Contract might have various field names for symbol
                    contract_symbol = None
                    if isinstance(contract, dict):
                        contract_symbol = (
                            contract.get('symbol') or
                            contract.get('Symbol') or
                            contract.get('ticker') or
                            contract.get('Ticker') or
                            contract.get('name') or
                            contract.get('Name')
                        )
                        contract_id = (
                            contract.get('contractId') or
                            contract.get('ContractId') or
                            contract.get('id') or
                            contract.get('Id') or
                            contract.get('contract_id')
                        )
                        
                        if contract_symbol and contract_symbol.upper() == symbol and contract_id:
                            logger.debug(f"Found contract ID for {symbol} in cache: {contract_id}")
                            return str(contract_id)
        
        # Fallback to hardcoded mappings if cache lookup failed
        contract_mappings = {
            "ES": "CON.F.US.ES.Z25",      # E-mini S&P 500
            "MES": "CON.F.US.MES.Z25",    # Micro E-mini S&P 500
            "NQ": "CON.F.US.NQ.Z25",      # E-mini NASDAQ-100
            "MNQ": "CON.F.US.MNQ.Z25",    # Micro E-mini NASDAQ-100
            "YM": "CON.F.US.YM.Z25",       # E-mini Dow Jones
            "MYM": "CON.F.US.MYM.Z25",    # Micro E-mini Dow Jones
            "RTY": "CON.F.US.RTY.Z25",    # E-mini Russell 2000
            "M2K": "CON.F.US.M2K.Z25",    # Micro E-mini Russell 2000
            "GC": "CON.F.US.GC.Z25",       # Gold
            "MGC": "CON.F.US.MGC.Z25",     # Micro Gold
        }
        
        if symbol in contract_mappings:
            return contract_mappings[symbol]
        
        # If not found, try to construct it
        logger.warning(f"Unknown symbol {symbol}, using generic format")
        return f"CON.F.US.{symbol}.Z25"
    
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
    
    async def check_order_fills(self, account_id: str = None) -> Dict:
        """Check for filled orders and send Discord notifications"""
        try:
            target_account = account_id or (self.selected_account['id'] if self.selected_account else None)

            if not target_account:
                return {"error": "No account selected"}

            # Get order history to check for fills - limit to recent orders only
            orders = await self.get_order_history(target_account, limit=10)  # Reduced from 50 to 10
            filled_orders = []

            for order in orders:
                order_id = str(order.get('id', ''))
                if order_id in self._notified_orders:
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
                    # Get order details
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

                    # Send Discord notification
                    try:
                        account_name = self.selected_account.get('name', 'Unknown') if self.selected_account else 'Unknown'

                        # Only notify for orders we placed (with customTag)
                        custom_tag = order.get('customTag', '')
                        if not custom_tag or not custom_tag.startswith('TradingBot-v1.0'):
                            continue  # Skip orders not placed by our bot
                            
                        notification_data = {
                            'symbol': symbol,
                            'side': side,
                            'quantity': quantity,
                            'fill_price': f"${float(fill_price):.2f}" if fill_price else "Unknown",
                            'order_type': order_type_str,
                            'order_id': order_id,
                            'position_id': position_id
                        }

                        self.discord_notifier.send_order_fill_notification(notification_data, account_name)
                        self._notified_orders.add(order_id)
                        filled_orders.append(order_id)

                    except Exception as notif_err:
                        logger.warning(f"Failed to send order fill notification: {notif_err}")

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
            
            for order in orders:
                order_id = str(order.get('id', ''))
                if order_id in self._notified_orders:
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
                        self._notified_orders.add(order_id)
                        
                        logger.info(f"Sent Discord notification for closing order {order_id}")
                        
                    except Exception as notif_err:
                        logger.warning(f"Failed to send closing order notification: {notif_err}")
                        
        except Exception as e:
            logger.error(f"Failed to check order fills for closes: {str(e)}")
    
    async def _get_tick_size(self, symbol: str) -> float:
        """
        Get the tick size for a trading symbol.
        
        Args:
            symbol: Trading symbol (e.g., "ES", "NQ", "MNQ", "YM")
            
        Returns:
            float: Tick size for the symbol
        """
        symbol = symbol.upper()
        
        # Tick sizes for common futures contracts
        tick_sizes = {
            "ES": 0.25,      # E-mini S&P 500
            "MES": 0.25,     # Micro E-mini S&P 500
            "NQ": 0.25,      # E-mini NASDAQ-100
            "MNQ": 0.25,     # Micro E-mini NASDAQ-100
            "YM": 1.0,       # E-mini Dow Jones
            "MYM": 0.5,      # Micro E-mini Dow Jones (0.5 point ticks)
            "RTY": 0.1,      # E-mini Russell 2000
            "M2K": 0.1,      # Micro E-mini Russell 2000
            "CL": 0.01,      # Crude Oil
            "NG": 0.001,     # Natural Gas
            "GC": 0.1,       # Gold
            "SI": 0.005,     # Silver
            "MGC": 0.1,      # Micro Gold
        }
        
        if symbol in tick_sizes:
            base_ts = tick_sizes[symbol]
            # Hard guard for critical micros
            hard_map = {"MNQ": 0.25, "MES": 0.25, "MYM": 0.5, "MGC": 0.1}
            if symbol in hard_map and base_ts != hard_map[symbol]:
                logger.warning(f"Hard guard: overriding tick size for {symbol} to {hard_map[symbol]} (was {base_ts})")
                return hard_map[symbol]
            return base_ts

        # Try to discover tick size from contract metadata via API
        try:
            if self.session_token:
                contracts = await self.get_available_contracts()  # type: ignore  # called from async contexts
                # Find by symbol occurrence in known fields
                for c in contracts or []:
                    sym = (c.get("symbol") or c.get("name") or "").upper()
                    cid = (c.get("contractId") or c.get("id") or "").upper()
                    if symbol in sym or f".{symbol}." in cid:
                        # check various possible keys for tick size
                        for key in ("tickSize", "minTick", "priceIncrement", "minimumPriceIncrement", "tick"):
                            if c.get(key):
                                try:
                                    ts = float(c.get(key))
                                    if ts > 0:
                                        # Enforce hard guards if symbol is one of our known micros
                                        hard_map = {"MNQ": 0.25, "MES": 0.25, "MYM": 0.5, "MGC": 0.1}
                                        if symbol in hard_map and abs(ts - hard_map[symbol]) > 1e-9:
                                            logger.warning(f"Hard guard: API tick for {symbol}={ts} differs from expected {hard_map[symbol]}; using expected")
                                            return hard_map[symbol]
                                        logger.info(f"Discovered tick size from API for {symbol}: {ts} (key {key})")
                                        return ts
                                except Exception:
                                    pass
        except Exception as e:
            logger.debug(f"Tick size discovery via API failed for {symbol}: {e}")
        
        # Default tick size
        # Before defaulting, apply hard guard if symbol is one of our known ones
        hard_map = {"MNQ": 0.25, "MES": 0.25, "MYM": 0.5, "MGC": 0.1}
        if symbol in hard_map:
            logger.warning(f"Hard guard default: using {hard_map[symbol]} for {symbol}")
            return hard_map[symbol]
        logger.warning(f"Unknown symbol {symbol}, using default tick size: 0.25")
        return 0.25
    
    def _round_to_tick_size(self, price: float, tick_size: float) -> float:
        """Round price to nearest valid tick size."""
        if tick_size <= 0:
            return price
        return round(price / tick_size) * tick_size
    
    def _get_point_value(self, symbol: str) -> float:
        """
        Get the point value (dollar value per point) for a trading symbol.
        
        Args:
            symbol: Trading symbol (e.g., "ES", "NQ", "MNQ", "YM")
            
        Returns:
            float: Point value in dollars per point per contract
        """
        symbol = symbol.upper()
        
        # Point values for common futures contracts (dollars per point per contract)
        point_values = {
            "ES": 50.0,      # E-mini S&P 500: $50 per point
            "MES": 5.0,      # Micro E-mini S&P 500: $5 per point
            "NQ": 20.0,      # E-mini NASDAQ-100: $20 per point
            "MNQ": 2.0,      # Micro E-mini NASDAQ-100: $2 per point
            "YM": 5.0,       # E-mini Dow Jones: $5 per point
            "MYM": 0.5,      # Micro E-mini Dow Jones: $0.50 per point
            "RTY": 50.0,     # E-mini Russell 2000: $50 per point
            "M2K": 5.0,      # Micro E-mini Russell 2000: $5 per point
            "CL": 10.0,      # Crude Oil: $10 per point
            "NG": 10.0,      # Natural Gas: $10 per point
            "GC": 100.0,     # Gold: $100 per point
            "SI": 50.0,      # Silver: $50 per point
            "MGC": 10.0,     # Micro Gold: $10 per point
        }
        
        return point_values.get(symbol, 1.0)  # Default to $1 per point if unknown
    
    def _generate_unique_custom_tag(self, order_type: str = "order") -> str:
        """Generate a unique custom tag for orders."""
        self._order_counter += 1
        timestamp = int(datetime.now().timestamp())
        return f"{BOT_ORDER_TAG_PREFIX}-{order_type}-{self._order_counter}-{timestamp}"

    async def place_market_order(self, symbol: str, side: str, quantity: int, account_id: str = None, 
                                stop_loss_ticks: int = None, take_profit_ticks: int = None, order_type: str = "market", 
                                limit_price: float = None) -> Dict:
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
            contract_id = self._get_contract_id(symbol)
            
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
                "customTag": self._generate_unique_custom_tag("market")
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
            
            if not self.session_token:
                logger.error("No session token available. Please authenticate first.")
                return []
            
            headers = {
                "accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.session_token}"
            }
            
            # Try multiple contract endpoints in order of preference
            # 1. Contract/search (POST) - preferred method
            # 2. Contract/list (GET) - older method
            # 3. Contract/getAll (GET) - alternative
            attempts = 0
            last_error = None
            response = None
            endpoint_methods = [
                ("POST", "/api/Contract/search", {}),
                ("POST", "/api/Contract/search", {"request": {}}),
                ("GET", "/api/Contract/list", None),
                ("GET", "/api/Contract/getAll", None),
                ("GET", "/api/Contract", None),
            ]
            
            for method, endpoint, data in endpoint_methods:
                attempts += 1
                try:
                    if method == "GET":
                        response = self._make_curl_request(method, endpoint, headers=headers, suppress_errors=True)
                    else:
                        response = self._make_curl_request(method, endpoint, data=data, headers=headers, suppress_errors=True)
                    
                    if "error" not in response:
                        logger.debug(f"Contract fetch succeeded: {method} {endpoint}")
                        break
                    
                    # Log error and try next method
                    err_msg = str(response.get("error", ""))
                    last_error = err_msg
                    logger.debug(f"Contract attempt {attempts} failed ({method} {endpoint}): {err_msg}")
                    time.sleep(0.1)
                except Exception as e:
                    logger.debug(f"Contract attempt {attempts} exception ({method} {endpoint}): {e}")
                    last_error = str(e)
                    time.sleep(0.1)
                    continue
            
            if "error" in response or not response:
                logger.warning(f"All contract API endpoints failed, using fallback approach")
                # Return cached data if available, even if expired
                if use_cache:
                    with self._contract_cache_lock:
                        if self._contract_cache is not None:
                            logger.warning(f"API error, returning stale cached contracts ({len(self._contract_cache['contracts'])} contracts)")
                            return self._contract_cache['contracts'].copy()
                
                # Try SDK if available
                use_sdk = os.getenv("USE_PROJECTX_SDK", "0").lower() in ("1", "true", "yes")
                if use_sdk and sdk_adapter is not None and sdk_adapter.is_sdk_available():
                    try:
                        logger.info("Attempting to fetch contracts via SDK...")
                        # Use SDK to get contracts
                        suite = await sdk_adapter.get_or_create_order_suite("MNQ")
                        if hasattr(suite, 'client') and suite.client:
                            # Try to get contracts from client
                            contracts_result = await suite.client.get_contracts()
                            if contracts_result:
                                logger.info(f"✅ Retrieved {len(contracts_result)} contracts via SDK")
                                return contracts_result
                    except Exception as sdk_err:
                        logger.debug(f"SDK contract fetch failed: {sdk_err}")
                
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
            
            # Cache the contracts
            if use_cache:
                with self._contract_cache_lock:
                    self._contract_cache = {
                        'contracts': contracts.copy(),
                        'timestamp': datetime.now(),
                        'ttl_minutes': cache_ttl_minutes
                    }
                    logger.debug(f"Cached {len(contracts)} contracts for {cache_ttl_minutes} minutes")
            
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
        
        Args:
            interactive: If True, ask for confirmation. If False, proceed automatically.
        
        Returns:
            Dict: Flatten response or error
        """
        try:
            if not self.selected_account:
                print("❌ No account selected")
                return {"error": "No account selected"}
            
            if not self.session_token:
                print("❌ No session token available. Please authenticate first.")
                return {"error": "No session token available"}
            
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
            
            # Get all open positions first
            positions = await self.get_open_positions(target_account)
            if not positions:
                logger.info("No open positions found to close")
                print("✅ No open positions found")
                return {"success": True, "message": "No positions to close"}
            
            # Close each position individually
            closed_positions = []
            failed_positions = []
            
            for position in positions:
                position_id = position.get("id")
                position_size = position.get("size", 0)
                position_type = position.get("type", 0)
                
                if not position_id:
                    logger.warning(f"Skipping position without ID: {position}")
                    continue
                
                logger.info(f"Closing position {position_id} with size {position_size}")
                
                # Close the position
                result = await self.close_position(position_id, account_id=target_account)
                
                if "error" in result:
                    logger.error(f"Failed to close position {position_id}: {result['error']}")
                    failed_positions.append({"id": position_id, "error": result["error"]})
                else:
                    logger.info(f"Successfully closed position {position_id}")
                    closed_positions.append(position_id)
                    
                    # Send Discord notification for position close
                    try:
                        account_name = self.selected_account.get('name', 'Unknown') if self.selected_account else 'Unknown'
                        
                        # Get position details for notification
                        position_details = None
                        for pos in positions:
                            if str(pos.get('id', '')) == str(position_id):
                                position_details = pos
                                break
                        
                        if position_details:
                            contract_id = position_details.get('contractId')
                            symbol = self._get_symbol_from_contract_id(contract_id)
                            
                            # Get current market price for exit price
                            exit_price = "Unknown"
                            try:
                                quote = await self.get_market_quote(symbol)
                                if "error" not in quote:
                                    if position_details.get('side', 0) == 0:  # Long position
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
                                'quantity': position_details.get('size', 0),
                                'entry_price': f"${position_details.get('entryPrice', 0):.2f}",
                                'exit_price': exit_price,
                                'pnl': position_details.get('unrealizedPnl', 0),
                                'position_id': position_id
                            }
                            self.discord_notifier.send_position_close_notification(notification_data, account_name)
                    except Exception as notif_err:
                        logger.warning(f"Failed to send Discord position close notification: {notif_err}")
            
            # Cancel all open orders
            logger.info("Canceling all open orders")
            orders = await self.get_open_orders(target_account)
            canceled_orders = []
            failed_orders = []
            
            for order in orders:
                order_id = order.get("id")
                if not order_id:
                    continue
                
                logger.info(f"Canceling order {order_id}")
                result = await self.cancel_order(order_id, account_id=target_account)
                
                if "error" in result:
                    logger.error(f"Failed to cancel order {order_id}: {result['error']}")
                    failed_orders.append({"id": order_id, "error": result["error"]})
                else:
                    logger.info(f"Successfully canceled order {order_id}")
                    canceled_orders.append(order_id)
            
            # Prepare result
            result = {
                "success": True,
                "closed_positions": closed_positions,
                "canceled_orders": canceled_orders,
                "failed_positions": failed_positions,
                "failed_orders": failed_orders,
                "positions_count": len(closed_positions),
                "orders_count": len(canceled_orders)
            }
            
            if closed_positions or canceled_orders:
                logger.info(f"Successfully closed {len(closed_positions)} positions and canceled {len(canceled_orders)} orders")
                print(f"✅ All positions flattened successfully!")
                print(f"   Account: {self.selected_account['name']}")
                print(f"   Closed positions: {len(closed_positions)}")
                print(f"   Canceled orders: {len(canceled_orders)}")
            else:
                logger.info("No positions or orders found to close/cancel")
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
            
            if not self.session_token:
                logger.error("No session token available. Please authenticate first.")
                return []
            
            logger.info(f"Fetching open positions for account {target_account}")
            
            headers = {
                "accept": "text/plain",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.session_token}"
            }
            
            # Use the official TopStepX Gateway API for positions
            # Based on https://gateway.docs.projectx.com/docs/api-reference/positions/search-open-positions
            search_data = {
                "accountId": int(target_account)
            }
            
            logger.info(f"Requesting open positions for account {target_account} using TopStepX Gateway API")
            logger.info(f"Request data: {search_data}")
            
            # Call the official TopStepX Gateway API
            response = self._make_curl_request("POST", "/api/Position/searchOpen", data=search_data, headers=headers)
            
            if "error" in response:
                logger.error(f"TopStepX Gateway API failed: {response['error']}")
                return []
            
            if not response.get("success"):
                logger.error(f"TopStepX Gateway API returned error: {response}")
                return []
            
            positions = response.get("positions", [])
            if not positions:
                logger.info(f"No open positions found for account {target_account}")
                return []
            
            logger.info(f"Successfully found {len(positions)} open positions from TopStepX Gateway API")
            logger.info(f"Positions data: {positions}")
            
            return positions
            
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
            
            if not self.session_token:
                return {"error": "No session token available. Please authenticate first."}
            
            logger.info(f"Fetching position details for position {position_id}")
            
            headers = {
                "accept": "text/plain",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.session_token}"
            }
            
            response = self._make_curl_request("GET", f"/api/Position/{position_id}", headers=headers)
            
            if "error" in response:
                logger.error(f"Failed to fetch position details: {response['error']}")
                return response
            
            return response
            
        except Exception as e:
            logger.error(f"Failed to fetch position details: {str(e)}")
            return {"error": str(e)}
    
    async def close_position(self, position_id: str, quantity: int = None, account_id: str = None) -> Dict:
        """
        Close a specific position or part of it.
        
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
            
            if not self.session_token:
                return {"error": "No session token available. Please authenticate first."}
            
            logger.info(f"Closing position {position_id} on account {target_account}")
            if quantity:
                logger.info(f"Closing {quantity} contracts")
            
            headers = {
                "accept": "text/plain",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.session_token}"
            }
            
            # Get the contract ID for this position
            positions = await self.get_open_positions(target_account)
            contract_id = None
            for pos in positions:
                if str(pos.get('id', '')) == str(position_id):
                    contract_id = pos.get('contractId')
                    break
            
            if not contract_id:
                return {"error": f"Could not find contract ID for position {position_id}"}
            
            close_data = {
                "accountId": int(target_account),
                "contractId": contract_id
            }
            
            if quantity:
                close_data["quantity"] = quantity
            
            response = self._make_curl_request("POST", "/api/Position/closeContract", data=close_data, headers=headers)
            
            if "error" in response:
                logger.error(f"Failed to close position: {response['error']}")
                return response
            
            logger.info(f"Position closed successfully: {response}")
            
            # Send Discord notification for position close
            try:
                # Get position details before closing for notification
                positions = await self.get_open_positions(target_account)
                position_details = None
                for pos in positions:
                    if str(pos.get('id', '')) == str(position_id):
                        position_details = pos
                        break
                
                if position_details:
                    account_name = self.selected_account.get('name', 'Unknown') if self.selected_account else 'Unknown'
                    
                    # Get current market price for exit price
                    symbol = self._get_symbol_from_contract_id(contract_id)
                    exit_price = "Unknown"
                    try:
                        quote = await self.get_market_quote(symbol)
                        if "error" not in quote:
                            if position_details.get('side', 0) == 0:  # Long position
                                exit_price = quote.get("bid") or quote.get("last")
                            else:  # Short position
                                exit_price = quote.get("ask") or quote.get("last")
                            if exit_price:
                                exit_price = f"${float(exit_price):.2f}"
                                logger.info(f"Set exit price to: {exit_price}")
                    except Exception as price_err:
                        logger.warning(f"Could not fetch exit price: {price_err}")
                    
                    notification_data = {
                        'symbol': symbol,
                        'side': 'LONG' if position_details.get('side', 0) == 0 else 'SHORT',
                        'quantity': position_details.get('size', 0),
                        'entry_price': f"${position_details.get('entryPrice', 0):.2f}",
                        'exit_price': exit_price,
                        'pnl': position_details.get('unrealizedPnl', 0),
                        'position_id': position_id
                    }
                    self.discord_notifier.send_position_close_notification(notification_data, account_name)
            except Exception as notif_err:
                logger.warning(f"Failed to send Discord position close notification: {notif_err}")
            
            return response
            
        except Exception as e:
            logger.error(f"Failed to close position: {str(e)}")
            return {"error": str(e)}
    
    # ============================================================================
    # NATIVE TOPSTEPX API METHODS - ORDER MANAGEMENT
    # ============================================================================
    
    async def get_open_orders(self, account_id: str = None) -> List[Dict]:
        """
        Get all open orders for the selected account.
        
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
            
            if not self.session_token:
                logger.error("No session token available. Please authenticate first.")
                return []
            
            logger.info(f"Fetching open orders for account {target_account}")
            
            headers = {
                "accept": "text/plain",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.session_token}"
            }
            
            # Use the official TopStepX Gateway API for orders
            # Try the standard order search endpoint with proper data
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
            
            logger.info(f"Requesting open orders for account {target_account} using TopStepX Gateway API")
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
            
            if not orders:
                logger.info(f"No open orders found for account {target_account}")
                return []
            
            total_orders = len(orders)
            # Filter strictly to OPEN orders (status == 1)
            open_only = [o for o in orders if o.get("status") == 1]
            logger.info(f"Orders returned: {total_orders}; OPEN filtered: {len(open_only)}")
            if total_orders != len(open_only):
                logger.debug(f"Filtered out non-open orders; first 3 removed examples: {[o for o in orders if o.get('status') != 1][:3]}")
            logger.info(f"Open Orders data: {open_only}")
            
            return open_only
            
        except Exception as e:
            logger.error(f"Failed to fetch orders: {str(e)}")
            return []
    
    async def cancel_order(self, order_id: str, account_id: str = None) -> Dict:
        """
        Cancel a specific order.
        
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
            
            if not self.session_token:
                return {"error": "No session token available. Please authenticate first."}
            
            logger.info(f"Canceling order {order_id} on account {target_account}")
            
            headers = {
                "accept": "text/plain",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.session_token}"
            }
            
            cancel_data = {
                "orderId": order_id,
                "accountId": int(target_account)
            }
            
            response = self._make_curl_request("POST", "/api/Order/cancel", data=cancel_data, headers=headers)
            
            if "error" in response:
                logger.error(f"Failed to cancel order: {response['error']}")
                return response
            
            logger.info(f"Order canceled successfully: {response}")
            return response
            
        except Exception as e:
            logger.error(f"Failed to cancel order: {str(e)}")
            return {"error": str(e)}
    
    async def modify_order(self, order_id: str, new_quantity: int = None, new_price: float = None, 
                          account_id: str = None, order_type: int = None) -> Dict:
        """
        Modify an existing order.
        
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
            
            if not self.session_token:
                return {"error": "No session token available. Please authenticate first."}
            
            # Get order info to determine type and check if it's a bracket order
            order_info = None
            if new_quantity is not None or new_price is not None:
                orders = await self.get_open_orders(target_account)
                for order in orders:
                    if str(order.get('id', '')) == str(order_id):
                        order_info = order
                        break
                
                # Check if order is a bracket order (no customTag) and trying to modify size
                if new_quantity is not None and order_info and not order_info.get('customTag'):
                    return {
                        "error": "Cannot modify size of bracket order attached to position. "
                                "Bracket orders (stop loss/take profit) automatically match position size. "
                                "You can only modify the price, or close the position to remove the bracket orders."
                    }
            
            logger.info(f"Modifying order {order_id} on account {target_account}")
            
            headers = {
                "accept": "text/plain",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.session_token}"
            }
            
            modify_data = {
                "orderId": int(order_id),
                "accountId": int(target_account)
            }
            
            # Only include size if provided and not trying to modify bracket order
            if new_quantity is not None:
                modify_data["size"] = new_quantity  # Use 'size' field for TopStepX API
            
            if new_price is not None:
                # Determine price field based on order type
                # Get order type from order_info if we fetched it, otherwise use provided order_type
                actual_order_type = None
                if order_info:
                    actual_order_type = order_info.get('type', order_type)
                elif order_type is not None:
                    actual_order_type = order_type
                else:
                    # Need to fetch order type
                    if order_info is None:
                        orders = await self.get_open_orders(target_account)
                        for order in orders:
                            if str(order.get('id', '')) == str(order_id):
                                order_info = order
                                break
                    if order_info:
                        actual_order_type = order_info.get('type')
                    else:
                        actual_order_type = 1  # Default to limit order
                
                if actual_order_type == 4:  # Stop order
                    modify_data["stopPrice"] = new_price
                else:  # Limit order or other types
                    modify_data["limitPrice"] = new_price
            
            response = self._make_curl_request("POST", "/api/Order/modify", data=modify_data, headers=headers)
            
            if "error" in response:
                logger.error(f"Failed to modify order: {response['error']}")
                return response
            
            # Check if the API response indicates success
            if response.get("success") == False:
                error_code = response.get("errorCode", "Unknown")
                error_message = response.get("errorMessage", "No error message")
                logger.error(f"Order modification failed: Error Code {error_code}, Message: {error_message}")
                
                # Provide helpful error message for common errors
                if error_code == 3 or "attached to position" in error_message.lower():
                    return {
                        "error": "Cannot modify size of bracket order attached to position. "
                                "Bracket orders (stop loss/take profit) automatically match position size. "
                                "You can only modify the price, or close the position to remove the bracket orders."
                    }
                
                return {"error": f"Order modification failed: Error Code {error_code}, Message: {error_message}"}
            
            logger.info(f"Order modified successfully: {response}")
            return response
            
        except Exception as e:
            logger.error(f"Failed to modify order: {str(e)}")
            return {"error": str(e)}
    
    async def modify_stop_loss(self, position_id: str, new_stop_price: float, account_id: str = None) -> Dict:
        """
        Modify the stop loss order attached to a position.
        
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
            
            # Get linked orders for the position
            linked_orders = await self.get_linked_orders(position_id, target_account)
            
            # Find the stop loss order (type 4 = Stop order)
            stop_order = None
            for order in linked_orders:
                if order.get('type') == 4:  # Stop order type
                    stop_order = order
                    break
            
            if not stop_order:
                return {"error": "No stop loss order found for this position"}
            
            stop_order_id = str(stop_order.get('id', ''))
            
            # Modify the stop loss order (only price, not size)
            result = await self.modify_order(
                stop_order_id, 
                new_quantity=None,  # Don't change size
                new_price=new_stop_price,
                account_id=target_account,
                order_type=4  # Stop order
            )
            
            if "error" in result:
                return result
            
            logger.info(f"Stop loss modified successfully for position {position_id}: new price = {new_stop_price}")
            return {
                "success": True,
                "position_id": position_id,
                "stop_order_id": stop_order_id,
                "new_stop_price": new_stop_price,
                "message": f"Stop loss modified to ${new_stop_price}"
            }
            
        except Exception as e:
            logger.error(f"Failed to modify stop loss: {str(e)}")
            return {"error": str(e)}
    
    async def modify_take_profit(self, position_id: str, new_tp_price: float, account_id: str = None) -> Dict:
        """
        Modify the take profit order attached to a position.
        
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
            
            # Get linked orders for the position
            linked_orders = await self.get_linked_orders(position_id, target_account)
            
            # Find the take profit order (type 1 = Limit order, opposite side of position)
            tp_order = None
            for order in linked_orders:
                if order.get('type') == 1:  # Limit order type (take profit)
                    tp_order = order
                    break
            
            if not tp_order:
                return {"error": "No take profit order found for this position"}
            
            tp_order_id = str(tp_order.get('id', ''))
            
            # Modify the take profit order (only price, not size)
            result = await self.modify_order(
                tp_order_id, 
                new_quantity=None,  # Don't change size
                new_price=new_tp_price,
                account_id=target_account,
                order_type=1  # Limit order
            )
            
            if "error" in result:
                return result
            
            logger.info(f"Take profit modified successfully for position {position_id}: new price = {new_tp_price}")
            return {
                "success": True,
                "position_id": position_id,
                "tp_order_id": tp_order_id,
                "new_tp_price": new_tp_price,
                "message": f"Take profit modified to ${new_tp_price}"
            }
            
        except Exception as e:
            logger.error(f"Failed to modify take profit: {str(e)}")
            return {"error": str(e)}
    
    def _get_trading_session_dates(self, date: datetime = None) -> tuple:
        """
        Get the start and end dates for the trading session containing the given date.
        Sessions run from 6pm EST to 4pm EST next day, Sunday through Friday.
        
        Args:
            date: Date to find session for (defaults to now)
            
        Returns:
            tuple: (session_start, session_end) as datetime objects in UTC
        """
        from datetime import timedelta
        from pytz import timezone as tz
        import pytz
        
        if date is None:
            date = datetime.now(pytz.UTC)
        
        # Convert to EST
        est = tz('US/Eastern')
        if date.tzinfo is None:
            date = pytz.UTC.localize(date)
        date_est = date.astimezone(est)
        
        # Get the day of week (0=Monday, 6=Sunday)
        weekday = date_est.weekday()
        
        # If Saturday (5) or Sunday before 6pm, use previous Friday's session
        # If Sunday after 6pm, start new session
        if weekday == 5:  # Saturday - use Friday's session end
            session_start_est = date_est.replace(hour=18, minute=0, second=0, microsecond=0) - timedelta(days=1)
            session_end_est = date_est.replace(hour=16, minute=0, second=0, microsecond=0)
        elif weekday == 6:  # Sunday
            if date_est.hour < 18:  # Before 6pm Sunday - use previous Friday
                session_start_est = date_est.replace(hour=18, minute=0, second=0, microsecond=0) - timedelta(days=2)
                session_end_est = (date_est.replace(hour=18, minute=0, second=0, microsecond=0) - timedelta(days=1)).replace(hour=16, minute=0)
            else:  # After 6pm Sunday - start new session
                session_start_est = date_est.replace(hour=18, minute=0, second=0, microsecond=0)
                session_end_est = (date_est + timedelta(days=1)).replace(hour=16, minute=0, second=0, microsecond=0)
        else:  # Monday-Friday
            # Check if we're before 6pm today or after
            if date_est.hour < 18:  # Before 6pm - use previous day's session
                if weekday == 0:  # Monday before 6pm - use Sunday's session
                    session_start_est = (date_est - timedelta(days=1)).replace(hour=18, minute=0, second=0, microsecond=0)
                    session_end_est = date_est.replace(hour=16, minute=0, second=0, microsecond=0)
                else:
                    session_start_est = (date_est - timedelta(days=1)).replace(hour=18, minute=0, second=0, microsecond=0)
                    session_end_est = date_est.replace(hour=16, minute=0, second=0, microsecond=0)
            else:  # After 6pm - current session
                session_start_est = date_est.replace(hour=18, minute=0, second=0, microsecond=0)
                session_end_est = (date_est + timedelta(days=1)).replace(hour=16, minute=0, second=0, microsecond=0)
        
        # Convert back to UTC
        session_start_utc = session_start_est.astimezone(pytz.UTC)
        session_end_utc = session_end_est.astimezone(pytz.UTC)
        
        return (session_start_utc, session_end_utc)
    
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
                price = (order.get('executionPrice') or 
                        order.get('averagePrice') or 
                        order.get('fillPrice') or 
                        order.get('price') or 
                        order.get('limitPrice') or 
                        order.get('stopPrice') or 0.0)
                timestamp = order.get('executionTimestamp') or order.get('creationTimestamp', '')
                
                # Debug logging for order details
                logger.debug(f"Processing order: side={side}, qty={quantity}, price={price}, timestamp={timestamp}, order_keys={list(order.keys())}")
                
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
                            'exit_order_id': order.get('id')
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
                            'exit_order_id': order.get('id')
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
    
    def _get_point_value(self, symbol: str) -> float:
        """
        Get the dollar value per point move for a symbol.
        
        Args:
            symbol: Symbol/contract code
            
        Returns:
            float: Dollar value per point
        """
        symbol_upper = symbol.upper()
        
        # Micro contracts
        if 'MNQ' in symbol_upper:
            return 2.0  # $2 per point
        elif 'MES' in symbol_upper:
            return 5.0  # $5 per point
        elif 'MYM' in symbol_upper:
            return 0.5  # $0.50 per point
        elif 'M2K' in symbol_upper or 'MRTYZ' in symbol_upper:
            return 0.5  # $0.50 per point
        
        # Full-size contracts
        elif 'NQ' in symbol_upper and 'MNQ' not in symbol_upper:
            return 20.0  # $20 per point
        elif 'ES' in symbol_upper and 'MES' not in symbol_upper:
            return 50.0  # $50 per point
        elif 'YM' in symbol_upper and 'MYM' not in symbol_upper:
            return 5.0  # $5 per point
        elif 'RTY' in symbol_upper or 'M2K' in symbol_upper:
            return 50.0  # $50 per point (full-size Russell)
        
        # Commodities
        elif 'CL' in symbol_upper:
            return 1000.0  # $1000 per point for crude oil
        elif 'GC' in symbol_upper:
            return 100.0  # $100 per point for gold
        elif 'SI' in symbol_upper:
            return 5000.0  # $5000 per point for silver
        elif '6E' in symbol_upper:
            return 125000.0  # $125,000 per point for Euro FX
        
        # Default fallback
        logger.warning(f"Unknown symbol {symbol}, using default point value $1")
        return 1.0
    
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
            
            if not self.session_token:
                logger.error("No session token available. Please authenticate first.")
                return []
            
            logger.info(f"Fetching order history for account {target_account}")
            
            headers = {
                "accept": "text/plain",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.session_token}"
            }
            
            # Use the same approach as get_open_orders but for all orders (not just open)
            from datetime import datetime, timezone, timedelta
            import pytz
            
            if start_timestamp:
                start_time = datetime.fromisoformat(start_timestamp.replace('Z', '+00:00'))
            else:
                now = datetime.now(timezone.utc)
                start_time = now - timedelta(days=7)
            
            if end_timestamp:
                end_time = datetime.fromisoformat(end_timestamp.replace('Z', '+00:00'))
            else:
                end_time = datetime.now(timezone.utc)
            
            search_data = {
                "accountId": int(target_account),
                "startTimestamp": start_time.isoformat(),
                "endTimestamp": end_time.isoformat(),
                "request": {
                    "accountId": int(target_account),
                    "limit": limit
                }
            }
            
            logger.info(f"Requesting order history for account {target_account} using TopStepX Gateway API")
            logger.info(f"Request data: {search_data}")
            
            # Use the same endpoint as get_open_orders but without status filter
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
            
            if not orders:
                logger.info(f"No historical orders found for account {target_account}")
                return []
            
            # Filter to only filled/executed orders for history
            filled_orders = [o for o in orders if o.get("status") in [2, 3, 4]]  # Filled, Executed, Complete
            logger.info(f"Total orders returned: {len(orders)}; Filled orders: {len(filled_orders)}")
            
            # If no filled orders found, try the Fill API endpoint
            if not filled_orders:
                logger.info("No filled orders from Order/search, trying Fill/search endpoint")
                
                fill_search_data = {
                    "accountId": int(target_account),
                    "startTime": start_time.isoformat(),
                    "endTime": end_time.isoformat(),
                    "limit": limit
                }
                
                fill_response = self._make_curl_request("POST", "/api/Fill/search", data=fill_search_data, headers=headers, suppress_errors=True)
                
                if fill_response and not "error" in fill_response and fill_response.get("success"):
                    # Check for fills in response
                    fills = []
                    for field in ["fills", "data", "result", "items", "list"]:
                        if field in fill_response and isinstance(fill_response[field], list):
                            fills = fill_response[field]
                            break
                    
                    if fills:
                        logger.info(f"Found {len(fills)} fills from Fill/search endpoint")
                        # Convert fills to order format for consistency
                        for fill in fills:
                            filled_orders.append({
                                'id': fill.get('id') or fill.get('fillId'),
                                'symbol': fill.get('symbol') or fill.get('contractId'),
                                'side': fill.get('side'),
                                'quantity': fill.get('quantity') or fill.get('qty'),
                                'price': fill.get('price') or fill.get('fillPrice'),
                                'timestamp': fill.get('timestamp') or fill.get('fillTime'),
                                'status': 4,  # Mark as filled
                                'orderId': fill.get('orderId'),
                                'type': 'fill',
                                **fill  # Include all original fill data
                            })
                    else:
                        logger.info("Fill/search also returned no results")
                else:
                    logger.debug("Fill/search endpoint not available or returned error")
            
            # Limit results
            if len(filled_orders) > limit:
                filled_orders = filled_orders[:limit]
            
            logger.info(f"Found {len(filled_orders)} historical filled orders")
            return filled_orders
            
        except Exception as e:
            logger.error(f"Failed to fetch order history: {str(e)}")
            return []
    
    # ============================================================================
    # NATIVE TOPSTEPX API METHODS - BRACKET ORDER SYSTEM
    # ============================================================================
    
    async def create_bracket_order_improved(self, symbol: str, side: str, quantity: int,
                                           entry_stop_price: float, stop_loss_price: float,
                                           take_profit_price: float, account_id: str = None) -> Dict:
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
                account_id=target_account
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
                                        account_id=target_account
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
                                 account_id: str = None) -> Dict:
        """
        Create a native TopStepX bracket order with linked stop loss and take profit.
        Uses the same approach as the working place_market_order method.
        
        Args:
            symbol: Trading symbol (e.g., "ES", "NQ", "MNQ", "YM")
            side: "BUY" or "SELL"
            quantity: Number of contracts
            stop_loss_price: Stop loss price (optional if stop_loss_ticks provided)
            take_profit_price: Take profit price (optional if take_profit_price provided)
            stop_loss_ticks: Stop loss in ticks (optional if stop_loss_price provided)
            take_profit_ticks: Take profit in ticks (optional if take_profit_price provided)
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
            
            # Check if OCO brackets are enabled for the account
            logger.warning("⚠️  IMPORTANT: Bracket orders require 'Auto OCO Brackets' to be enabled in your TopStepX account settings.")
            logger.warning("   If this order fails with 'Brackets cannot be used with Position Brackets', please enable Auto OCO Brackets in your account.")
            
            logger.info(f"Creating bracket order for {side} {quantity} {symbol} on account {target_account}")
            
            # Get proper contract ID
            contract_id = self._get_contract_id(symbol)
            
            # Convert side to numeric value
            side_value = 0 if side.upper() == "BUY" else 1
            
            # Prepare order data using the same format as place_market_order
            order_data = {
                "accountId": int(target_account),
                "contractId": contract_id,
                "type": 2,  # Market order for entry
                "side": side_value,
                "size": quantity,
                "limitPrice": None,
                "stopPrice": None,
                "customTag": self._generate_unique_custom_tag("bracket")
            }
            
            # For bracket orders, we should use the entry price as the reference point
            # rather than trying to get current market price, since we're placing a market order
            # that will execute at the current market price
            tick_size = await self._get_tick_size(symbol)
            logger.info(f"Bracket context: contract={contract_id}, tick_size={tick_size}")
            
            # For bracket orders, we calculate ticks from the entry price (which will be the market price when filled)
            # We don't need to get current market price since we're placing a market order
            
            # Calculate stop loss ticks from entry price
            if stop_loss_price is not None and stop_loss_ticks is None:
                try:
                    # Prefer bid/ask aligned to side for better precision
                    quote = await self.get_market_quote(symbol)
                    if "error" not in quote and (quote.get("bid") or quote.get("ask") or quote.get("last")):
                        if side.upper() == "BUY":
                            entry_price = float(quote.get("ask") or quote.get("last"))
                        else:
                            entry_price = float(quote.get("bid") or quote.get("last"))
                        logger.info(f"Using current market price as entry: ${entry_price}")
                        
                        if side.upper() == "BUY":
                            # For BUY orders, stop loss should be below entry price
                            # TopStepX expects negative ticks for long stop loss
                            price_diff = entry_price - stop_loss_price
                            stop_loss_ticks = int(price_diff / tick_size)
                            # Ensure negative ticks for long stop loss
                            if stop_loss_ticks > 0:
                                stop_loss_ticks = -stop_loss_ticks
                        else:
                            # For SELL orders, stop loss should be above entry price
                            # TopStepX expects positive ticks for short stop loss
                            price_diff = stop_loss_price - entry_price
                            stop_loss_ticks = int(price_diff / tick_size)
                            # Ensure positive ticks for short stop loss
                            if stop_loss_ticks < 0:
                                stop_loss_ticks = -stop_loss_ticks
                        
                        logger.info(f"Stop Loss Calculation: Entry=${entry_price}, Target=${stop_loss_price}, Diff=${price_diff:.2f}, Ticks={stop_loss_ticks} (tick_size={tick_size})")
                        
                        # Validate tick values (TopStepX has limits)
                        if abs(stop_loss_ticks) > 1000:
                            logger.warning(f"Stop loss ticks ({stop_loss_ticks}) exceeds 1000 limit, capping at 1000")
                            stop_loss_ticks = 1000 if stop_loss_ticks > 0 else -1000
                    else:
                        logger.error(f"Could not get market price for {symbol}")
                        return {"error": f"Could not get market price for {symbol}. Market data is required for bracket orders."}
                except Exception as e:
                    logger.error(f"Failed to calculate stop loss ticks: {e}")
                    return {"error": f"Failed to calculate stop loss ticks: {e}"}
            
            # Calculate take profit ticks from entry price
            if take_profit_price is not None and take_profit_ticks is None:
                try:
                    quote = await self.get_market_quote(symbol)
                    if "error" not in quote and (quote.get("bid") or quote.get("ask") or quote.get("last")):
                        if side.upper() == "BUY":
                            entry_price = float(quote.get("ask") or quote.get("last"))
                        else:
                            entry_price = float(quote.get("bid") or quote.get("last"))
                        
                        if side.upper() == "BUY":
                            # For BUY orders, take profit should be above entry price
                            # TopStepX expects positive ticks for long take profit
                            price_diff = take_profit_price - entry_price
                            take_profit_ticks = int(price_diff / tick_size)
                            # Ensure positive ticks for long take profit
                            if take_profit_ticks < 0:
                                take_profit_ticks = -take_profit_ticks
                        else:
                            # For SELL orders, take profit should be below entry price
                            # TopStepX expects negative ticks for short take profit
                            price_diff = entry_price - take_profit_price
                            take_profit_ticks = int(price_diff / tick_size)
                            # Ensure negative ticks for short take profit
                            if take_profit_ticks > 0:
                                take_profit_ticks = -take_profit_ticks
                        
                        logger.info(f"Take Profit Calculation: Entry=${entry_price}, Target=${take_profit_price}, Diff=${price_diff:.2f}, Ticks={take_profit_ticks} (tick_size={tick_size})")
                        
                        # Validate tick values (TopStepX has limits)
                        if abs(take_profit_ticks) > 1000:
                            logger.warning(f"Take profit ticks ({take_profit_ticks}) exceeds 1000 limit, capping at 1000")
                            take_profit_ticks = 1000 if take_profit_ticks > 0 else -1000
                    else:
                        logger.error(f"Could not get market price for {symbol}")
                        return {"error": f"Could not get market price for {symbol}. Market data is required for bracket orders."}
                except Exception as e:
                    logger.error(f"Failed to calculate take profit ticks: {e}")
                    return {"error": f"Failed to calculate take profit ticks: {e}"}
            
            # Add bracket orders using the same format as place_market_order
            if stop_loss_ticks is not None:
                order_data["stopLossBracket"] = {
                    "ticks": stop_loss_ticks,
                    "type": 4,  # Stop loss type
                    "size": quantity,
                    "reduceOnly": True
                }
                logger.info(f"Added stop loss bracket: {stop_loss_ticks} ticks, size: {quantity}, reduceOnly: True")
            
            if take_profit_ticks is not None:
                order_data["takeProfitBracket"] = {
                    "ticks": take_profit_ticks,
                    "type": 1,  # Take profit type
                    "size": quantity,
                    "reduceOnly": True
                }
                logger.info(f"Added take profit bracket: {take_profit_ticks} ticks, size: {quantity}, reduceOnly: True")
            
            headers = {
                "accept": "text/plain",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.session_token}"
            }
            
            # Use the same endpoint as place_market_order
            response = self._make_curl_request("POST", "/api/Order/place", data=order_data, headers=headers)
            
            if "error" in response:
                logger.error(f"Failed to create bracket order: {response['error']}")
                return response
            
            # Check if order was actually successful
            if response.get("success") == False:
                error_code = response.get("errorCode", "Unknown")
                error_message = response.get("errorMessage", "No error message")
                logger.error(f"Bracket order failed: Error Code {error_code}, Message: {error_message}")
                
                # If bracket order fails due to tick limits, try a regular market order
                if "ticks" in error_message.lower() and "1000" in error_message:
                    logger.warning("Bracket order failed due to tick limits, falling back to regular market order")
                    
                    # Place a regular market order without brackets
                    fallback_result = await self.place_market_order(
                        symbol=symbol,
                        side=side,
                        quantity=quantity,
                        account_id=target_account
                    )
                    
                    if "error" not in fallback_result:
                        logger.info("Fallback market order placed successfully")
                        return {
                            "success": True,
                            "order_result": fallback_result,
                            "warning": "Bracket order failed due to tick limits, placed regular market order instead"
                        }
                    else:
                        logger.error(f"Fallback market order also failed: {fallback_result['error']}")
                        return {"error": f"Both bracket order and fallback market order failed. Bracket: {error_message}, Market: {fallback_result['error']}"}
                else:
                    return {"error": f"Bracket order failed: Error Code {error_code}, Message: {error_message}"}
            
            logger.info(f"Bracket order created successfully: {response}")
            
            # Send Discord notification for successful bracket order
            try:
                account_name = self.selected_account.get('name', 'Unknown') if self.selected_account else 'Unknown'
                
                # Get actual execution price from positions after a brief delay
                execution_price = "Market"
                if response.get('executionPrice'):
                    execution_price = f"${response['executionPrice']:.2f}"
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
                
                notification_data = {
                    'symbol': symbol,
                    'side': side,
                    'quantity': quantity,
                    'price': execution_price,
                    'order_type': 'Native Bracket',
                    'order_id': response.get('orderId', 'Unknown'),
                    'status': order_status,
                    'account_id': target_account,
                    'stop_loss': stop_loss_price,
                    'take_profit': take_profit_price
                }
                self.discord_notifier.send_order_notification(notification_data, account_name)
            except Exception as notif_err:
                logger.warning(f"Failed to send Discord notification: {notif_err}")
            
            # Start monitoring for this position if we have a position ID
            if "orderId" in response and response.get("success"):
                # We need to get the position ID from the order response or by checking positions
                # For now, we'll start monitoring after a brief delay to let the position be created
                import asyncio
                await asyncio.sleep(1)  # Brief delay to let position be created
                
                # Get the most recent position for this symbol
                positions = await self.get_open_positions(target_account)
                if positions:
                    # Find the most recent position for this symbol
                    symbol_positions = []
                    for pos in positions:
                        if pos.get('contractId') == contract_id:
                            symbol_positions.append(pos)
                    
                    if symbol_positions:
                        # Get the most recent position
                        latest_position = max(symbol_positions, key=lambda x: x.get('creationTimestamp', ''))
                        position_id = str(latest_position.get('id'))
                        
                        # Start monitoring with trade parameters
                        await self._start_bracket_monitoring(
                            position_id, symbol, target_account,
                            side=side, stop_loss_price=stop_loss_price, take_profit_price=take_profit_price
                        )
                        logger.info(f"Started bracket monitoring for position {position_id}")
            
            return response
            
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
            for pos in positions:
                if pos.get('contractId') == self._get_contract_id(symbol):
                    position_id = pos.get('id')
                    break
            
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
                    account_id=target_account
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
            for order in orders:
                order_contract = order.get('contractId', '')
                if order_contract == self._get_contract_id(symbol):
                    symbol_orders.append(order)
            
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
                              account_id: str = None) -> Dict:
        """
        Place a stop order (entry stop - triggers market order when price is hit).
        Use stop_buy for BUY stop orders or stop_sell for SELL stop orders.
        
        Args:
            symbol: Trading symbol
            side: "BUY" or "SELL"
            quantity: Number of contracts
            stop_price: Stop price (triggers when price reaches this level)
            account_id: Account ID (uses selected account if not provided)
            
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
            contract_id = self._get_contract_id(symbol)
            
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
                "customTag": self._generate_unique_custom_tag("stop_entry")
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
                                                take_profit_price: float, account_id: str = None) -> Dict:
        """
        Place OCO bracket order with stop order as entry.
        
        Works like native_bracket but uses a stop order for entry instead of market order.
        Uses the same /api/Order/place endpoint with stopLossBracket and takeProfitBracket.
        
        Args:
            symbol: Trading symbol (e.g., "MNQ", "ES")
            side: "BUY" or "SELL"
            quantity: Number of contracts
            entry_price: Stop price for entry
            stop_loss_price: Stop loss price
            take_profit_price: Take profit price
            account_id: Account ID (uses selected account if not provided)
            
        Returns:
            Dict: OCO bracket response or error
        """
        try:
            target_account = account_id or (self.selected_account['id'] if self.selected_account else None)
            
            if not target_account:
                return {"error": "No account selected"}
            
            if side.upper() not in ["BUY", "SELL"]:
                return {"error": "Side must be 'BUY' or 'SELL'"}
            
            # Ensure valid token
            await self._ensure_valid_token()
            
            logger.info(f"Placing OCO bracket with stop entry:")
            logger.info(f"  Symbol: {symbol}")
            logger.info(f"  Side: {side}")
            logger.info(f"  Quantity: {quantity}")
            logger.info(f"  Entry (stop): ${entry_price:.2f}")
            logger.info(f"  Stop Loss: ${stop_loss_price:.2f}")
            logger.info(f"  Take Profit: ${take_profit_price:.2f}")
            
            # Get proper contract ID
            contract_id = self._get_contract_id(symbol)
            
            # Convert side to numeric value
            side_value = 0 if side.upper() == "BUY" else 1
            
            # Get tick size for calculations
            tick_size = await self._get_tick_size(symbol)
            logger.info(f"Bracket context: contract={contract_id}, tick_size={tick_size}, entry_price=${entry_price:.2f}")
            
            # Calculate stop loss ticks from entry price
            if side.upper() == "BUY":
                # For BUY orders, stop loss should be below entry price
                # TopStepX expects negative ticks for long stop loss
                price_diff = entry_price - stop_loss_price
                stop_loss_ticks = int(price_diff / tick_size)
                # Ensure negative ticks for long stop loss
                if stop_loss_ticks > 0:
                    stop_loss_ticks = -stop_loss_ticks
            else:
                # For SELL orders, stop loss should be above entry price
                # TopStepX expects positive ticks for short stop loss
                price_diff = stop_loss_price - entry_price
                stop_loss_ticks = int(price_diff / tick_size)
                # Ensure positive ticks for short stop loss
                if stop_loss_ticks < 0:
                    stop_loss_ticks = -stop_loss_ticks
            
            logger.info(f"Stop Loss Calculation: Entry=${entry_price:.2f}, Target=${stop_loss_price:.2f}, Diff=${price_diff:.2f}, Ticks={stop_loss_ticks} (tick_size={tick_size})")
            
            # Calculate take profit ticks from entry price
            if side.upper() == "BUY":
                # For BUY orders, take profit should be above entry price
                # TopStepX expects positive ticks for long take profit
                price_diff = take_profit_price - entry_price
                take_profit_ticks = int(price_diff / tick_size)
                # Ensure positive ticks for long take profit
                if take_profit_ticks < 0:
                    take_profit_ticks = -take_profit_ticks
            else:
                # For SELL orders, take profit should be below entry price
                # TopStepX expects negative ticks for short take profit
                price_diff = entry_price - take_profit_price
                take_profit_ticks = int(price_diff / tick_size)
                # Ensure negative ticks for short take profit
                if take_profit_ticks > 0:
                    take_profit_ticks = -take_profit_ticks
            
            logger.info(f"Take Profit Calculation: Entry=${entry_price:.2f}, Target=${take_profit_price:.2f}, Diff=${price_diff:.2f}, Ticks={take_profit_ticks} (tick_size={tick_size})")
            
            # Validate tick values (TopStepX has limits)
            if abs(stop_loss_ticks) > 1000:
                logger.warning(f"Stop loss ticks ({stop_loss_ticks}) exceeds 1000 limit, capping at 1000")
                stop_loss_ticks = 1000 if stop_loss_ticks > 0 else -1000
            if abs(take_profit_ticks) > 1000:
                logger.warning(f"Take profit ticks ({take_profit_ticks}) exceeds 1000 limit, capping at 1000")
                take_profit_ticks = 1000 if take_profit_ticks > 0 else -1000
            
            # Prepare order data using the same format as create_bracket_order
            # but with stop order type instead of market
            order_data = {
                "accountId": int(target_account),
                "contractId": contract_id,
                "type": 4,  # Stop-market order for entry (instead of 2=market)
                "side": side_value,
                "size": quantity,
                "limitPrice": None,
                "stopPrice": entry_price,  # This makes it a stop order
                "customTag": self._generate_unique_custom_tag("stop_bracket")
            }
            
            # Add bracket orders using the same format as create_bracket_order
            order_data["stopLossBracket"] = {
                "ticks": stop_loss_ticks,
                "type": 4,  # Stop loss type
                "size": quantity,
                "reduceOnly": True
            }
            logger.info(f"Added stop loss bracket: {stop_loss_ticks} ticks, size: {quantity}, reduceOnly: True")
            
            order_data["takeProfitBracket"] = {
                "ticks": take_profit_ticks,
                "type": 1,  # Take profit type
                "size": quantity,
                "reduceOnly": True
            }
            logger.info(f"Added take profit bracket: {take_profit_ticks} ticks, size: {quantity}, reduceOnly: True")
            
            headers = {
                "accept": "text/plain",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.session_token}"
            }
            
            # Use the same endpoint as create_bracket_order
            response = self._make_curl_request("POST", "/api/Order/place", data=order_data, headers=headers)
            
            if "error" in response:
                logger.error(f"Failed to create stop bracket order: {response['error']}")
                # Check if it's a bracket-related error
                error_msg = response.get("error", "").lower()
                if any(keyword in error_msg for keyword in ["bracket", "not enabled", "not supported", "position brackets"]):
                    logger.warning("Brackets might not be enabled, falling back to hybrid approach")
                    print("⚠️  Native brackets not supported in this configuration")
                    print("   Falling back to hybrid approach: stop order + auto-bracket on fill")
                    return await self._stop_bracket_hybrid(
                        symbol=symbol,
                        side=side,
                        quantity=quantity,
                        entry_price=entry_price,
                        stop_loss_price=stop_loss_price,
                        take_profit_price=take_profit_price,
                        account_id=target_account
                    )
                return response
            
            # Check if order was actually successful
            if response.get("success") == False:
                error_code = response.get("errorCode", "Unknown")
                error_message = response.get("errorMessage", "No error message")
                logger.error(f"Stop bracket order failed: Error Code {error_code}, Message: {error_message}")
                
                # Check for bracket-related errors
                if "bracket" in error_message.lower() or "position brackets" in error_message.lower():
                    logger.warning("Falling back to hybrid approach due to bracket error")
                    return await self._stop_bracket_hybrid(
                        symbol=symbol,
                        side=side,
                        quantity=quantity,
                        entry_price=entry_price,
                        stop_loss_price=stop_loss_price,
                        take_profit_price=take_profit_price,
                        account_id=target_account
                    )
                
                return {"error": f"Stop bracket order failed: Error Code {error_code}, Message: {error_message}"}
            
            # Update order activity timestamp
            self._update_order_activity()
            
            logger.info(f"Stop bracket order created successfully: {response}")
            
            # Send Discord notification for successful bracket order
            try:
                account_name = self.selected_account.get('name', 'Unknown') if self.selected_account else 'Unknown'
                
                notification_data = {
                    'symbol': symbol,
                    'side': side,
                    'quantity': quantity,
                    'price': f"${entry_price:.2f} (stop)",
                    'order_type': 'Stop Bracket',
                    'order_id': response.get('orderId', 'Unknown'),
                    'status': 'Placed',
                    'account_name': account_name
                }
                
                await self.discord_notifier.send_order_notification(notification_data)
                logger.info(f"Discord notification sent for stop bracket {side} {quantity} {symbol}")
            except Exception as notify_err:
                logger.warning(f"Failed to send Discord notification: {notify_err}")
            
            return {
                "success": True,
                "orderId": response.get("orderId"),
                "method": "oco_native",
                "symbol": symbol,
                "side": side,
                "quantity": quantity,
                "entry_price": entry_price,
                "stop_loss": stop_loss_price,
                "take_profit": take_profit_price,
                **response
            }
            
        except Exception as e:
            logger.error(f"Failed to place OCO bracket order: {str(e)}")
            # Fall back to hybrid on any exception
            logger.info("Falling back to hybrid approach due to exception")
            return await self._stop_bracket_hybrid(
                symbol=symbol,
                side=side,
                quantity=quantity,
                entry_price=entry_price,
                stop_loss_price=stop_loss_price,
                take_profit_price=take_profit_price,
                account_id=account_id
            )
    
    async def _stop_bracket_hybrid(self, symbol: str, side: str, quantity: int,
                                  entry_price: float, stop_loss_price: float,
                                  take_profit_price: float, account_id: str = None) -> Dict:
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
                account_id=account_id
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
                        contract_id = self._get_contract_id(symbol)
                        
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

            # SDK-only: no fallback to REST
            return {"error": "SDK trailing stop unavailable. Ensure USE_PROJECTX_SDK=1 and SDK is installed."}
            
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
                contract_id = self._get_contract_id(symbol_up)
                quote_resp = self._make_curl_request("GET", f"/api/MarketData/quote/{contract_id}", headers=headers)
                if quote_resp and "error" not in quote_resp:
                    # Normalize keys possibly: bid, ask, last, volume
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
            contract_id = self._get_contract_id(symbol)
            
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
    
    async def get_historical_data(self, symbol: str, timeframe: str = "1m", 
                                 limit: int = 100) -> List[Dict]:
        """
        Get historical price data for a symbol using direct REST API.
        
        Args:
            symbol: Trading symbol (e.g., "MNQ", "MES")
            timeframe: Timeframe (1m, 5m, 15m, 1h, 1d)
            limit: Number of bars to return
            
        Returns:
            List[Dict]: Historical data with keys: timestamp, open, high, low, close, volume
        """
        try:
            logger.info(f"Fetching historical data for {symbol} ({timeframe}, {limit} bars)")
            
            # For small limits (1-5 bars) on short timeframes (1m, 5m), bypass cache or use very short TTL
            # This ensures real-time monitoring gets fresh data
            use_fresh_data = False
            if limit <= 5 and timeframe in ['1m', '5m']:
                use_fresh_data = True
                logger.debug(f"Small limit ({limit}) on short timeframe ({timeframe}) - fetching fresh data")
            
            # Check cache first - use dynamic TTL based on market hours (unless we need fresh data)
            if not use_fresh_data:
                cache_key = self._get_cache_key(symbol, timeframe)
                cached_data = self._load_from_cache(cache_key, max_age_minutes=None)
                if cached_data is not None and len(cached_data) >= limit:
                    logger.info(f"Using cached data ({len(cached_data)} bars available)")
                    return cached_data[-limit:] if len(cached_data) > limit else cached_data
            else:
                cache_key = self._get_cache_key(symbol, timeframe)
            
            # Ensure we have a valid JWT token
            if not await self._ensure_valid_token():
                logger.error("Failed to authenticate - cannot fetch historical data")
                return []
            
            # Map timeframe to API format
            tf_map = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "1d": 1440}
            unit_number = tf_map.get(timeframe, 1)
            
            # Get contract ID for the symbol
            contract_id = self._get_contract_id(symbol)
            
            # Calculate time range for the request
            from datetime import datetime, timedelta, timezone
            end_time = datetime.now(timezone.utc)
            # Request more data than needed to account for market closures/gaps
            window_minutes = int(unit_number * max(limit * 3, limit + 100))
            start_time = end_time - timedelta(minutes=window_minutes)
            
            # Format timestamps for API (ISO 8601)
            start_str = start_time.strftime("%Y-%m-%dT%H:%M:%S")
            end_str = end_time.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"  # Milliseconds + Z
            
            # Prepare API request
            bars_request = {
                "contractId": contract_id,
                "live": False,
                "startTime": start_str,
                "endTime": end_str,
                "unit": 2,  # 2 = Minutes (1=Tick, 2=Minute, 3=Day, 4=Week, etc.)
                "unitNumber": unit_number,
                "limit": limit * 3,  # Request extra to handle gaps
                "includePartialBar": True  # Include current incomplete bar for real-time monitoring
            }
            
            # Set headers with JWT token
            headers = {
                "Authorization": f"Bearer {self.session_token}",
                "Content-Type": "application/json",
                "accept": "text/plain"
            }
            
            # Make API call
            logger.debug(f"Requesting bars: {start_str} to {end_str}")
            response = self._make_curl_request("POST", "/api/History/retrieveBars", 
                                              data=bars_request, headers=headers)
            
            # Check for errors
            if isinstance(response, dict) and "error" in response:
                logger.error(f"API error: {response['error']}")
                return []
            
            # Parse response - API may return dict with 'bars' key or direct array
            bars_data = None
            if isinstance(response, list):
                bars_data = response
                logger.debug(f"API returned list with {len(response)} items")
            elif isinstance(response, dict):
                # Try common response formats
                bars_data = response.get('bars') or response.get('data') or response.get('candles')
                if bars_data is None:
                    logger.error(f"Unexpected dict response format. Keys: {list(response.keys())}")
                    logger.error(f"Full response (first 500 chars): {str(response)[:500]}")
                    return []
                logger.debug(f"API returned dict, extracted '{[k for k,v in response.items() if v == bars_data][0]}' with {len(bars_data)} items")
            else:
                logger.error(f"Unexpected response type: {type(response)}")
                return []
            
            if not bars_data:
                logger.warning("API returned empty bars data")
                return []
            
            # Convert API response to our standard format
            parsed_bars: List[Dict] = []
            for i, bar in enumerate(bars_data):
                # Debug: log first bar to see actual field names and values (only with -v flag)
                if i == 0:
                    logger.debug(f"Sample bar keys: {list(bar.keys())}")
                    logger.debug(f"Sample bar data: {bar}")
                    logger.debug(f"Timestamp field 't' value: {bar.get('t')} (type: {type(bar.get('t'))})")
                
                # API returns single-letter keys: t, o, h, l, c, v
                # Also check for full names as fallback
                timestamp_val = bar.get("t") or bar.get("time") or bar.get("timestamp") or bar.get("Time") or bar.get("Timestamp")
                
                # Parse and convert to local timezone
                timestamp_local = ""
                if timestamp_val:
                    try:
                        # Try parsing as ISO string first
                        if isinstance(timestamp_val, str):
                            dt_utc = datetime.fromisoformat(timestamp_val.replace("Z", "+00:00"))
                        # Try parsing as Unix timestamp (seconds since epoch)
                        elif isinstance(timestamp_val, (int, float)):
                            # Could be seconds or milliseconds
                            if timestamp_val > 10000000000:  # Milliseconds
                                dt_utc = datetime.fromtimestamp(timestamp_val / 1000, tz=timezone.utc)
                            else:  # Seconds
                                dt_utc = datetime.fromtimestamp(timestamp_val, tz=timezone.utc)
                        else:
                            logger.warning(f"Unknown timestamp type: {type(timestamp_val)}, value: {timestamp_val}")
                            timestamp_local = str(timestamp_val)
                            dt_utc = None
                        
                        if dt_utc:
                            local_tz = datetime.now().astimezone().tzinfo
                            dt_local = dt_utc.astimezone(local_tz)
                            timestamp_local = dt_local.isoformat()
                    except Exception as e:
                        logger.debug(f"Failed to parse timestamp '{timestamp_val}': {e}")
                        timestamp_local = str(timestamp_val) if timestamp_val else ""
                else:
                    logger.warning(f"No timestamp found in bar data. Bar keys: {list(bar.keys())}")
                
                # Handle case-insensitive field names
                # API uses single-letter keys (t, o, h, l, c, v), but check full names as fallback
                def get_field(field_names):
                    """Get field value trying multiple name variations."""
                    for name in field_names:
                        val = bar.get(name)
                        if val is not None:
                            return val
                    return 0
                
                parsed_bar = {
                    "timestamp": timestamp_local,
                    "time": timestamp_local,
                    "open": float(get_field(["o", "open", "Open", "O"])),
                    "high": float(get_field(["h", "high", "High", "H"])),
                    "low": float(get_field(["l", "low", "Low", "L"])),
                    "close": float(get_field(["c", "close", "Close", "C"])),
                    "volume": int(get_field(["v", "volume", "Volume", "V", "vol", "Vol"]))
                }
                parsed_bars.append(parsed_bar)
            
            # Sort by timestamp and take the most recent bars
            parsed_bars.sort(key=lambda b: b.get("timestamp", ""))
            result_bars = parsed_bars[-limit:] if len(parsed_bars) > limit else parsed_bars
            
            logger.debug(f"[REST API] Found {len(result_bars)} historical bars")
            
            # Save to cache for future use
            if parsed_bars:
                self._save_to_cache(cache_key, parsed_bars)
            
            return result_bars
            
        except Exception as e:
            logger.error(f"Failed to fetch historical data: {str(e)}")
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
        from datetime import datetime, time as dt_time, timedelta
        
        logger.info("EOD scheduler started - will update balance at midnight UTC")
        
        while True:
            try:
                # Calculate time until next midnight UTC
                now = datetime.utcnow()
                midnight = datetime.combine(now.date() + timedelta(days=1), dt_time.min)
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
            contracts_task = asyncio.create_task(self.get_available_contracts())
            
            # Wait for all parallel tasks to complete
            accounts_result = await accounts_task
            contracts_result = await contracts_task
            
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
                    print(f"  - {contract.get('symbol', 'Unknown')}: {contract.get('name', 'No name')}")
                if len(contracts) > 5:
                    print(f"  ... and {len(contracts) - 5} more")
            
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
            
            # Step 9: Trading interface
            print(f"\n🎯 Ready to trade on account: {selected_account['name']}")
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
        print("  orders - Show open orders")
        print("  close <position_id> [quantity] - Close position")
        print("  cancel <order_id> - Cancel order")
        print("  modify <order_id> <new_quantity> [new_price] - Modify order")
        print("  quote <symbol> - Get market quote")
        print("  depth <symbol> - Get market depth")
        print("  history <symbol> [timeframe] [limit] [raw] [csv] - Get historical data")
        print("    Add 'raw' for fast tab-separated output (e.g., history MNQ 5m 20 raw)")
        print("    Add 'csv' to export data to CSV file (e.g., history MNQ 5m 20 csv)")
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
        print("  help - Show this help message")
        print("  quit - Exit trading interface")
        print("="*50)
        print("💡 Use ↑/↓ arrows to navigate command history, Tab for completion")
        print("="*50)
        print("💡 Use 'auto_fills' command to enable automatic fill checking if needed")
        print("="*50)
        
        while True:
            try:
                command = input("\nEnter command: ").strip()
                
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
                            print(f"  - {contract.get('symbol', 'Unknown')}: {contract.get('name', 'No name')}")
                    else:
                        print("❌ No contracts available")
                elif command_lower == "accounts":
                    accounts = await self.list_accounts()
                    self.display_accounts(accounts)
                
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
                    print("\n" + "="*50)
                    print("TRADING INTERFACE HELP")
                    print("="*50)
                    print("Commands:")
                    print("  trade <symbol> <side> <quantity>")
                    print("    Example: trade MNQ BUY 1")
                    print("    Places a market order")
                    print()
                    print("  limit <symbol> <side> <quantity> <price>")
                    print("    Example: limit MNQ BUY 1 19500.50")
                    print("    Places a limit order at specified price")
                    print()
                    print("  bracket <symbol> <side> <quantity> <stop_ticks> <profit_ticks>")
                    print("    Example: bracket MNQ BUY 1 80 80")
                    print("    Places a bracket order with stop loss and take profit")
                    print()
                    print("  native_bracket <symbol> <side> <quantity> <stop_price> <profit_price>")
                    print("    Example: native_bracket MNQ BUY 1 19400.00 19600.00")
                    print("    Places a native TopStepX bracket order with linked stop/take profit")
                    print()
                    print("  stop_bracket <symbol> <side> <quantity> <entry_price> <stop_price> <profit_price>")
                    print("    Example: stop_bracket MNQ BUY 1 25800.00 25750.00 25900.00")
                    print("    Places a stop entry order with stop loss and take profit prices defined")
                    print()
                    print("  stop_buy <symbol> <quantity> <stop_price>")
                    print("    Example: stop_buy MNQ 1 25900.00")
                    print("    Places a stop buy order (triggers market buy when price reaches stop_price)")
                    print()
                    print("  stop_sell <symbol> <quantity> <stop_price>")
                    print("    Example: stop_sell MNQ 1 25900.00")
                    print("    Places a stop sell order (triggers market sell when price reaches stop_price)")
                    print()
                    print("  stop <symbol> <side> <quantity> <price>")
                    print("    Example: stop MNQ BUY 1 19400.00")
                    print("    Places a stop order (legacy command, use stop_buy/stop_sell)")
                    print()
                    print("  trail <symbol> <side> <quantity> <trail_amount>")
                    print("    Example: trail MNQ BUY 1 25.00")
                    print("    Places a trailing stop order (uses SDK native trailing stop)")
                    print()
                    print("  positions")
                    print("    Shows all open positions")
                    print()
                    print("  orders")
                    print("    Shows all open orders")
                    print()
                    print("  close <position_id> [quantity]")
                    print("    Example: close 12345 1")
                    print("    Closes a position (entire or partial)")
                    print()
                    print("  cancel <order_id>")
                    print("    Example: cancel 12345")
                    print("    Cancels an order")
                    print()
                    print("  modify <order_id> <new_quantity> [new_price]")
                    print("    Example: modify 12345 2 19500.00")
                    print("    Modifies an existing order")
                    print()
                    print("  modify_stop <position_id> <new_stop_price>")
                    print("    Example: modify_stop 425682864 25800.00")
                    print("    Modifies the stop loss order attached to a position")
                    print()
                    print("  modify_tp <position_id> <new_tp_price>")
                    print("    Example: modify_tp 425682864 26100.00")
                    print("    Modifies the take profit order attached to a position")
                    print()
                    print("  quote <symbol>")
                    print("    Example: quote MNQ")
                    print("    Gets real-time market quote")
                    print()
                    print("  depth <symbol>")
                    print("    Example: depth MNQ")
                    print("    Gets market depth (order book)")
                    print()
                    print("  history <symbol> [timeframe] [limit]")
                    print("    Example: history MNQ 1m 50")
                    print("    Gets historical price data")
                    print()
                    print("  monitor")
                    print("    Monitors position changes and automatically adjusts bracket orders")
                    print("    Use this after adding/subtracting contracts to existing positions")
                    print()
                    print("  flatten")
                    print("    Closes all positions and cancels all orders")
                    print("    Requires confirmation by typing 'FLATTEN'")
                    print()
                    print("  contracts")
                    print("    Lists available trading contracts")
                    print()
                    print("  accounts")
                    print("    Lists all your trading accounts")
                    print()
                    print("  help")
                    print("    Shows this help message")
                    print()
                    print("  quit")
                    print("    Exits the trading interface")
                    print("="*50)
                    print("💡 Use ↑/↓ arrows for command history, Tab for completion")
                    print("="*50)
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
                    if len(parts) != 7:
                        print("❌ Usage: stop_bracket <symbol> <side> <quantity> <entry_price> <stop_price> <profit_price>")
                        print("   Example: stop_bracket MNQ BUY 1 25800.00 25750.00 25900.00")
                        print("   Places a stop order at entry_price with bracket SL/TP attached")
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
                    
                    # Confirm the stop bracket order
                    print(f"\n⚠️  CONFIRM STOP BRACKET ORDER:")
                    print(f"   Symbol: {symbol.upper()}")
                    print(f"   Side: {side.upper()}")
                    print(f"   Quantity: {quantity}")
                    print(f"   Entry (Stop) Price: ${entry_price}")
                    print(f"   Stop Loss: ${stop_price}")
                    print(f"   Take Profit: ${profit_price}")
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
                        take_profit_price=profit_price
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
                    elif method == "hybrid_auto_bracket":
                        print(f"✅ Stop entry order placed with auto-bracketing!")
                        print(f"   Order ID: {entry_order_id}")
                        print(f"   Method: Hybrid (auto-bracket on fill)")
                        print(f"   Entry: ${entry_price} (stop order)")
                        print(f"   Stop Loss: ${stop_price}")
                        print(f"   Take Profit: ${profit_price}")
                        print(f"   📝 Brackets will be placed automatically when stop order fills")
                    else:
                        print(f"✅ Stop bracket order placed!")
                        print(f"   Order ID: {entry_order_id}")
                        print(f"   Entry: ${entry_price}")
                        print(f"   Stop Loss: ${stop_price}")
                        print(f"   Take Profit: ${profit_price}")
                
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
                
                elif command_lower.startswith("history "):
                    parts = command.split()
                    if len(parts) < 2:
                        print("❌ Usage: history <symbol> [timeframe] [limit] [raw] [csv]")
                        print("   Example: history MNQ 1m 50")
                        print("   Example: history MNQ 5m 20 raw")
                        print("   Example: history MNQ 5m 20 csv")
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
                    limit = int(clean_parts[1]) if len(clean_parts) > 1 else 20
                    
                    # Measure duration for performance insight
                    import time as _t
                    _t0 = _t.time()
                    # Get historical data (SDK only - no REST fallback)
                    result = await self.get_historical_data(symbol, timeframe, limit)
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
                        # Handle CSV export
                        if csv:
                            csv_filename = self._export_to_csv(result[-limit:], symbol, timeframe)
                            if csv_filename:
                                print(f"✅ Exported {len(result[-limit:])} bars to {csv_filename}")
                                print(f"   Elapsed time: {_elapsed_ms} ms")
                            else:
                                print(f"❌ Failed to export CSV file")
                        
                        if raw:
                            for bar in result[-limit:]:
                                time = bar.get('time', 'N/A')
                                open_price = bar.get('open', 0)
                                high = bar.get('high', 0)
                                low = bar.get('low', 0)
                                close = bar.get('close', 0)
                                volume = bar.get('volume', 0)
                                print(f"{time} {open_price} {high} {low} {close} {volume}")
                            print(f"fetched={len(result[-limit:])} elapsed_ms={_elapsed_ms}")
                        elif not csv:  # Only show formatted output if not CSV-only export
                            _count = len(result[-limit:])
                            print(f"\n📊 Historical Data for {symbol.upper()} ({timeframe}) - fetched={_count} in {_elapsed_ms} ms:")
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
                            for bar in result[-limit:]:  # Show exactly requested bars
                                # Get timestamp - prioritize parsed 'time'/'timestamp' keys
                                time = bar.get('time') or bar.get('timestamp') or ''
                                
                                # Format timestamp nicely (just date and time, no microseconds)
                                if time:
                                    if len(str(time)) > 19:
                                        try:
                                            # Parse ISO format and format nicely
                                            from datetime import datetime as _dt
                                            dt = _dt.fromisoformat(str(time).replace('Z', '+00:00'))
                                            time = dt.strftime('%Y-%m-%d %H:%M:%S')
                                        except Exception as e:
                                            # Keep original if parsing fails
                                            logger.debug(f"Failed to format timestamp {time}: {e}")
                                            time = str(time)[:19] if len(str(time)) > 19 else str(time)
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
                                
                                # Format timestamps
                                if isinstance(entry_time, str) and entry_time != 'N/A':
                                    try:
                                        from datetime import datetime
                                        dt = datetime.fromisoformat(entry_time.replace('Z', '+00:00'))
                                        entry_time = dt.strftime('%Y-%m-%d %H:%M:%S')
                                    except:
                                        pass
                                
                                if isinstance(exit_time, str) and exit_time != 'N/A':
                                    try:
                                        from datetime import datetime
                                        dt = datetime.fromisoformat(exit_time.replace('Z', '+00:00'))
                                        exit_time = dt.strftime('%Y-%m-%d %H:%M:%S')
                                    except:
                                        pass
                                
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
    
    print("TopStepX Trading Bot - Real API Version")
    print("=======================================")
    print()
    print("This bot will help you:")
    print("1. Authenticate with TopStepX API")
    print("2. List your active accounts")
    print("3. Select which account to trade on")
    print("4. Place live market orders")
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
