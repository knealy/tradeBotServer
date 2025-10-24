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
import subprocess
import readline
from typing import List, Dict, Optional
from datetime import datetime
from threading import Lock
from signalrcore.hub_connection_builder import HubConnectionBuilder
from discord_notifier import DiscordNotifier
from signalrcore.transport.websockets.websocket_transport import WebsocketTransport

# Load environment variables from .env file
import load_env

# Configure logging
log_level = os.getenv('LOG_LEVEL', 'INFO').upper()
logging.basicConfig(
    level=getattr(logging, log_level, logging.INFO),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('trading_bot.log', mode='a'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# Bot identifier for order tagging - will be made unique per order
BOT_ORDER_TAG_PREFIX = "TradingBot-v1.0"

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
        self._market_hub = None
        self._market_hub_connected = False
        self._market_hub_url = os.getenv("PROJECT_X_MARKET_HUB_URL", "https://rtc.topstepx.com/hubs/market")
        # Allow overriding hub method names via env to adapt without code change
        self._market_hub_quote_event = os.getenv("PROJECT_X_QUOTE_EVENT", "GatewayQuote")
        self._market_hub_subscribe_method = os.getenv("PROJECT_X_SUBSCRIBE_METHOD", "SubscribeContractQuotes")
        self._market_hub_unsubscribe_method = os.getenv("PROJECT_X_UNSUBSCRIBE_METHOD", "UnsubscribeContractQuotes")
        self._subscribed_symbols = set()
        self._pending_symbols = set()
        
        # Initialize Discord notifier
        self.discord_notifier = DiscordNotifier()
        
        # Order counter for unique custom tags
        self._order_counter = 0
        
        # Monitoring state - only monitor after market orders are placed
        self._monitoring_active = False
        self._last_order_time = None
        
        # Track filled orders to avoid duplicate notifications
        self._notified_orders = set()
        self._notified_positions = set()  # Track position close notifications
        
        # Auto fills settings
        self._auto_fills_enabled = False

    # ---------------------------
    # SignalR Market Hub Support
    # ---------------------------
    async def _ensure_market_socket_started(self) -> None:
        if self._market_hub_connected:
            return
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
    
    def _make_curl_request(self, method: str, endpoint: str, data: Dict = None, headers: Dict = None) -> Dict:
        """
        Make HTTP request using cURL.
        
        Args:
            method: HTTP method (GET, POST, etc.)
            endpoint: API endpoint
            data: Request data (for POST requests)
            headers: Request headers
            
        Returns:
            Dict: Response data
        """
        try:
            url = f"{self.base_url}{endpoint}"
            
            # Build cURL command with sane timeouts to avoid hanging
            curl_cmd = [
                "curl", "-s", "-X", method,
                "--connect-timeout", "5",
                "--max-time", "10"
            ]
            
            # Add headers
            if headers:
                for key, value in headers.items():
                    curl_cmd.extend(["-H", f"{key}: {value}"])
            
            # Add data for POST requests
            if data and method.upper() == "POST":
                curl_cmd.extend(["-H", "Content-Type: application/json"])
                curl_cmd.extend(["-d", json.dumps(data)])
            
            # Add URL
            curl_cmd.append(url)
            
            logger.debug(f"Executing cURL command: {' '.join(curl_cmd)}")
            
            # Execute cURL command
            result = subprocess.run(curl_cmd, capture_output=True, text=True, timeout=30)
            
            if result.returncode != 0:
                logger.error(f"cURL command failed: {result.stderr}")
                return {"error": f"cURL failed: {result.stderr}"}
            
            # Parse JSON response
            try:
                # Handle empty response (common for successful operations)
                if not result.stdout.strip():
                    return {"success": True, "message": "Operation completed successfully"}
                
                response_data = json.loads(result.stdout)
                return response_data
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse JSON response: {e}")
                logger.error(f"Raw response: {result.stdout}")
                return {"error": f"Invalid JSON response: {e}"}
                
        except subprocess.TimeoutExpired:
            logger.error("cURL request timed out")
            return {"error": "Request timed out"}
        except Exception as e:
            logger.error(f"cURL request failed: {str(e)}")
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
                    logger.info(f"Trying account info endpoint: {endpoint}")
                    response = self._make_curl_request("GET", endpoint, headers=headers)
                    
                    if "error" not in response and response:
                        logger.info(f"Found account info from {endpoint}: {response}")
                        return response
                    else:
                        logger.warning(f"Endpoint {endpoint} failed: {response}")
                        continue
                        
                except Exception as e:
                    logger.warning(f"Endpoint {endpoint} failed with exception: {e}")
                    continue
            
            return {"error": "Could not fetch account info from any endpoint"}
            
        except Exception as e:
            logger.error(f"Failed to fetch account info: {str(e)}")
            return {"error": str(e)}
    
    def _get_contract_id(self, symbol: str) -> str:
        """
        Convert trading symbol to TopStepX contract ID format.
        
        Args:
            symbol: Trading symbol (e.g., "ES", "NQ", "MNQ", "YM")
            
        Returns:
            str: Contract ID in TopStepX format
        """
        symbol = symbol.upper()
        
        # Common contract mappings for TopStepX
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
    
    async def _auto_fill_checker(self) -> None:
        """Background task to automatically check for fills"""
        self._auto_fills_enabled = True
        
        while self._auto_fills_enabled:
            try:
                await self.check_order_fills()
                await asyncio.sleep(30)  # Check every 30 seconds
            except Exception as e:
                logger.error(f"Auto fill checker error: {e}")
                await asyncio.sleep(30)
    
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
                "trailPrice": None,
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
            logger.info(f"===== ORDER PLACEMENT DEBUG =====")
            logger.info(f"Symbol: {symbol}, Side: {side}, Quantity: {quantity}")
            logger.info(f"Account ID: {target_account}")
            logger.info(f"Contract ID: {contract_id}")
            logger.info(f"Order Data: {json.dumps(order_data, indent=2)}")
            logger.info(f"=================================")
            
            # Make real API call to place order using session token
            headers = {
                "accept": "text/plain",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.session_token}"
            }
            
            response = self._make_curl_request("POST", "/api/Order/place", data=order_data, headers=headers)
            
            # Log FULL API response
            logger.info(f"===== API RESPONSE =====")
            logger.info(f"Response Type: {type(response)}")
            logger.info(f"Response Keys: {list(response.keys()) if isinstance(response, dict) else 'Not a dict'}")
            logger.info(f"Full Response: {json.dumps(response, indent=2) if isinstance(response, dict) else str(response)}")
            logger.info(f"========================")
            
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
    
    async def get_available_contracts(self) -> List[Dict]:
        """
        Get available trading contracts.
        
        Returns:
            List[Dict]: List of available contracts
        """
        try:
            logger.info("Fetching available contracts...")
            
            if not self.session_token:
                logger.error("No session token available. Please authenticate first.")
                return []
            
            headers = {
                "accept": "text/plain",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.session_token}"
            }
            
            response = self._make_curl_request("GET", "/api/Contract/list", headers=headers)
            
            if "error" in response:
                logger.error(f"Failed to fetch contracts: {response['error']}")
                return []
            
            # Parse contracts from response
            if isinstance(response, list):
                contracts = response
            elif isinstance(response, dict) and "contracts" in response:
                contracts = response["contracts"]
            elif isinstance(response, dict) and "data" in response:
                contracts = response["data"]
            elif isinstance(response, dict) and "result" in response:
                contracts = response["result"]
            else:
                logger.warning(f"Unexpected contracts response format: {response}")
                contracts = []
            
            logger.info(f"Found {len(contracts)} available contracts")
            return contracts
            
        except Exception as e:
            logger.error(f"Failed to fetch contracts: {str(e)}")
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
                confirm = input("   Are you sure? Type 'FLATTEN' to confirm: ").strip()
                if confirm != 'FLATTEN':
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
            
            logger.info(f"Modifying order {order_id} on account {target_account}")
            
            headers = {
                "accept": "text/plain",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.session_token}"
            }
            
            modify_data = {
                "orderId": order_id,
                "accountId": int(target_account)
            }
            
            if new_quantity is not None:
                modify_data["size"] = new_quantity  # Use 'size' field for TopStepX API
            if new_price is not None:
                # Determine price field based on order type
                if order_type == 4:  # Stop order
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
                return {"error": f"Order modification failed: Error Code {error_code}, Message: {error_message}"}
            
            logger.info(f"Order modified successfully: {response}")
            return response
            
        except Exception as e:
            logger.error(f"Failed to modify order: {str(e)}")
            return {"error": str(e)}
    
    async def get_order_history(self, account_id: str = None, limit: int = 100) -> List[Dict]:
        """
        Get order history for the selected account.
        
        Args:
            account_id: Account ID (uses selected account if not provided)
            limit: Maximum number of orders to return
            
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
            now = datetime.now(timezone.utc)
            # Get orders from the last 7 days to ensure we capture recent fills
            start_time = now - timedelta(days=7)
            
            search_data = {
                "accountId": int(target_account),
                "startTimestamp": start_time.isoformat(),
                "endTimestamp": now.isoformat(),
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
                "trailPrice": None,
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
            
            # Get all open orders for this symbol
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
            positions = await self.get_open_positions(account_id)
            if not positions:
                return
            
            orders = await self.get_open_orders(account_id)
            
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
                custom_tag = order.get('customTag', '')
                order_type = order.get('type', 0)
                
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
        Place a stop order.
        
        Args:
            symbol: Trading symbol
            side: "BUY" or "SELL"
            quantity: Number of contracts
            stop_price: Stop price
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
            logger.info(f"Placing stop order for {side} {quantity} {symbol} at {rounded_stop_price}")
            
            # Get proper contract ID
            contract_id = self._get_contract_id(symbol)
            
            # Convert side to numeric value
            side_value = 0 if side.upper() == "BUY" else 1
            
            # Prepare stop order data
            stop_data = {
                "accountId": int(target_account),
                "contractId": contract_id,
                "type": 4,  # Stop order type
                "side": side_value,
                "size": quantity,
                "stopPrice": rounded_stop_price,
                "customTag": self._generate_unique_custom_tag("stop")
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
            
            logger.info(f"Stop order placed successfully: {response}")
            return response
            
        except Exception as e:
            logger.error(f"Failed to place stop order: {str(e)}")
            return {"error": str(e)}
    
    async def place_trailing_stop_order(self, symbol: str, side: str, quantity: int, 
                                       trail_amount: float, account_id: str = None) -> Dict:
        """
        Place a trailing stop order.
        
        Args:
            symbol: Trading symbol
            side: "BUY" or "SELL"
            quantity: Number of contracts
            trail_amount: Trail amount in price units
            account_id: Account ID (uses selected account if not provided)
            
        Returns:
            Dict: Trailing stop order response or error
        """
        try:
            target_account = account_id or (self.selected_account['id'] if self.selected_account else None)
            
            if not target_account:
                return {"error": "No account selected"}
            
            if not self.session_token:
                return {"error": "No session token available. Please authenticate first."}
            
            if side.upper() not in ["BUY", "SELL"]:
                return {"error": "Side must be 'BUY' or 'SELL'"}
            
            logger.info(f"Placing trailing stop order for {side} {quantity} {symbol} with trail {trail_amount}")
            
            # Get proper contract ID
            contract_id = self._get_contract_id(symbol)
            
            # Convert side to numeric value
            side_value = 0 if side.upper() == "BUY" else 1
            
            # Prepare trailing stop order data
            trail_data = {
                "accountId": int(target_account),
                "contractId": contract_id,
                "type": 5,  # Trailing stop order type
                "side": side_value,
                "size": quantity,
                "trailAmount": trail_amount
            }
            
            headers = {
                "accept": "text/plain",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.session_token}"
            }
            
            response = self._make_curl_request("POST", "/api/Order/place", data=trail_data, headers=headers)
            
            if "error" in response:
                logger.error(f"Failed to place trailing stop order: {response['error']}")
                return response
            
            logger.info(f"Trailing stop order placed successfully: {response}")
            return response
            
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
    
    async def get_historical_data(self, symbol: str, timeframe: str = "1m", 
                                 limit: int = 100) -> List[Dict]:
        """
        Get historical price data for a symbol using REST API (historical data is not real-time).
        
        Args:
            symbol: Trading symbol
            timeframe: Timeframe (1m, 5m, 15m, 1h, 1d)
            limit: Number of bars to return
            
        Returns:
            List[Dict]: Historical data or error
        """
        try:
            if not self.session_token:
                return {"error": "No session token available. Please authenticate first."}
            
            logger.info(f"Fetching historical data for {symbol} ({timeframe})")
            
            # Get proper contract ID
            contract_id = self._get_contract_id(symbol)
            
            headers = {
                "accept": "text/plain",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.session_token}"
            }
            
            # Try the History API endpoint which is more likely to work
            # Based on the error message, we need: live, startTime, endTime, unit, unitNumber, request
            from datetime import datetime, timedelta
            
            # Calculate time range
            end_time = datetime.now()
            start_time = end_time - timedelta(hours=24)  # Get last 24 hours of data
            
            # Convert timeframe to unit and unitNumber
            # Based on the error, the API expects specific enum values
            timeframe_map = {
                "1m": {"unit": 0, "unitNumber": 1},  # 0 = Minute
                "5m": {"unit": 0, "unitNumber": 5},   # 0 = Minute
                "15m": {"unit": 0, "unitNumber": 15}, # 0 = Minute
                "1h": {"unit": 1, "unitNumber": 1},   # 1 = Hour
                "1d": {"unit": 2, "unitNumber": 1}    # 2 = Day
            }
            
            timeframe_info = timeframe_map.get(timeframe, {"unit": 0, "unitNumber": 1})
            
            history_data = {
                "live": False,
                "startTime": start_time.isoformat() + "Z",
                "endTime": end_time.isoformat() + "Z",
                "unit": timeframe_info["unit"],
                "unitNumber": timeframe_info["unitNumber"],
                "contractId": contract_id,
                "limit": limit
            }
            
            # Try different possible endpoints for historical data
            endpoints_to_try = [
                "/api/History/retrieveBars",
                "/api/History/bars",
                "/api/History/historical",
                f"/api/MarketData/history/{contract_id}",
                f"/api/MarketData/bars/{contract_id}",
                f"/api/MarketData/ohlc/{contract_id}"
            ]
            
            response = None
            for endpoint in endpoints_to_try:
                try:
                    if endpoint.startswith("/api/History/"):
                        # History endpoints typically use POST
                        response = self._make_curl_request("POST", endpoint, headers=headers, data=history_data)
                    else:
                        # MarketData endpoints might use GET with params
                        params = {"timeframe": timeframe, "limit": limit}
                        response = self._make_curl_request("GET", endpoint, headers=headers, data=params)
                    
                    if response and "error" not in response and response != {"success": True, "message": "Operation completed successfully"}:
                        logger.info(f"Successfully got response from endpoint: {endpoint}")
                        break
                except Exception as e:
                    logger.debug(f"Endpoint {endpoint} failed: {e}")
                    continue
            
            if not response:
                # Fallback to the original endpoint
                response = self._make_curl_request("POST", "/api/History/retrieveBars", headers=headers, data=history_data)
            
            # Debug logging to see actual API response
            logger.info(f"Raw historical data API response: {response}")
            
            if "error" in response:
                logger.error(f"Failed to fetch historical data: {response['error']}")
                return []
            
            # Parse historical data from response
            if isinstance(response, list):
                data = response
            elif isinstance(response, dict) and "data" in response:
                data = response["data"]
            elif isinstance(response, dict) and "result" in response:
                data = response["result"]
            elif isinstance(response, dict) and "bars" in response:
                bars = response["bars"]
                if bars is None:
                    # API returned error
                    error_code = response.get("errorCode", "Unknown")
                    error_message = response.get("errorMessage", "No error message")
                    logger.error(f"Historical data API error: Code {error_code}, Message: {error_message}")
                    data = []
                else:
                    data = bars
            elif isinstance(response, dict) and "success" in response:
                # Check if this is a successful response with no data
                if response.get("success") == True and "data" not in response and "bars" not in response:
                    logger.warning(f"Historical data API returned success but no data. This might indicate the symbol is not available for historical data or the timeframe is not supported.")
                    data = []
                else:
                    logger.warning(f"Unexpected historical data response format: {response}")
                    data = []
            else:
                logger.warning(f"Unexpected historical data response format: {response}")
                data = []
            
            logger.info(f"Found {len(data)} historical bars")
            return data
            
        except Exception as e:
            logger.error(f"Failed to fetch historical data: {str(e)}")
            return []
    
    async def run(self):
        """
        Main bot execution flow.
        """
        try:
            print("🤖 TopStepX Trading Bot - Real API Version")
            print("="*50)
            
            # Step 1: Authenticate
            if not await self.authenticate():
                print("❌ Authentication failed. Please check your API key.")
                return
            
            print("✅ Authentication successful!")
            
            # Step 2: List accounts
            accounts = await self.list_accounts()
            if not accounts:
                print("❌ No active accounts found.")
                return
            
            # Step 3: Display accounts
            self.display_accounts(accounts)
            
            # Step 4: Select account
            selected_account = self.select_account(accounts)
            if not selected_account:
                print("❌ No account selected. Exiting.")
                return
            
            # Step 5: Show account details
            balance = await self.get_account_balance()
            if balance is not None:
                print(f"\n💰 Current Balance: ${balance:,.2f}")
            
            # Step 6: Get available contracts
            contracts = await self.get_available_contracts()
            if contracts:
                print(f"\n📋 Available Contracts: {len(contracts)} found")
                for contract in contracts[:5]:  # Show first 5
                    print(f"  - {contract.get('symbol', 'Unknown')}: {contract.get('name', 'No name')}")
                if len(contracts) > 5:
                    print(f"  ... and {len(contracts) - 5} more")
            
            # Step 7: Trading interface
            print(f"\n🎯 Ready to trade on account: {selected_account['name']}")
            await self.trading_interface()
            
        except Exception as e:
            logger.error(f"Bot execution failed: {str(e)}")
            print(f"❌ Bot execution failed: {str(e)}")
    
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
                    "trade", "limit", "bracket", "native_bracket", "stop", "trail",
                    "positions", "orders", "close", "cancel", "modify", "quote", "depth", 
                    "history", "monitor", "bracket_monitor", "account_info", "flatten", 
                    "contracts", "accounts", "help", "quit"
                ]
                matches = [cmd for cmd in commands if cmd.startswith(text.lower())]
                if state < len(matches):
                    return matches[state]
            
            # If we're completing after a command, suggest common symbols
            elif len(words) >= 2 and words[0] in ["trade", "limit", "bracket", "native_bracket", "stop", "trail", "quote", "depth", "history"]:
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
        print("  history <symbol> [timeframe] [limit] - Get historical data")
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
        print("  flatten - Close all positions and cancel all orders")
        print("  contracts - List available contracts")
        print("  accounts - List accounts again")
        print("  help - Show this help message")
        print("  quit - Exit trading interface")
        print("="*50)
        print("💡 Use ↑/↓ arrows to navigate command history, Tab for completion")
        print("="*50)
        
        # Start automatic fill checking in background
        import asyncio
        asyncio.create_task(self._auto_fill_checker())
        print("🔄 Automatic fill checking started")
        
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
                    print("  stop <symbol> <side> <quantity> <price>")
                    print("    Example: stop MNQ BUY 1 19400.00")
                    print("    Places a stop order")
                    print()
                    print("  trail <symbol> <side> <quantity> <trail_amount>")
                    print("    Example: trail MNQ BUY 1 50.00")
                    print("    Places a trailing stop order")
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
                        print(f"✅ Stop order placed successfully!")
                        print(f"   Order ID: {result.get('orderId', 'Unknown')}")
                        print(f"   Status: {result.get('status', 'Unknown')}")
                
                elif command_lower.startswith("trail "):
                    parts = command.split()
                    if len(parts) != 5:
                        print("❌ Usage: trail <symbol> <side> <quantity> <trail_amount>")
                        print("   Example: trail MNQ BUY 1 50.00")
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
                    
                    # Confirm the trailing stop order
                    print(f"\n⚠️  CONFIRM TRAILING STOP ORDER:")
                    print(f"   Symbol: {symbol.upper()}")
                    print(f"   Side: {side.upper()}")
                    print(f"   Quantity: {quantity}")
                    print(f"   Trail Amount: ${trail_amount}")
                    print(f"   Account: {self.selected_account['name']}")
                    
                    confirm = input("   Confirm? (y/N): ").strip().lower()
                    if confirm != 'y':
                        print("❌ Order cancelled")
                        continue
                    
                    # Place the trailing stop order
                    result = await self.place_trailing_stop_order(symbol, side, quantity, trail_amount)
                    if "error" in result:
                        print(f"❌ Order failed: {result['error']}")
                    else:
                        print(f"✅ Trailing stop order placed successfully!")
                        print(f"   Order ID: {result.get('orderId', 'Unknown')}")
                        print(f"   Status: {result.get('status', 'Unknown')}")
                
                elif command_lower == "positions":
                    positions = await self.get_open_positions()
                    if positions:
                        print(f"\n📊 Open Positions ({len(positions)}):")
                        print(f"{'ID':<12} {'Symbol':<8} {'Side':<6} {'Quantity':<10} {'Price':<12} {'P&L':<12}")
                        print("-" * 70)
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
                            pnl = pos.get('unrealizedPnl', 0.0)
                            print(f"{pos_id:<12} {symbol:<8} {side:<6} {quantity:<10} ${price:<11.2f} ${pnl:<11.2f}")
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
                    if len(parts) < 3 or len(parts) > 4:
                        print("❌ Usage: modify <order_id> <new_quantity> [new_price]")
                        print("   Example: modify 12345 2 19500.00")
                        continue
                    
                    order_id = parts[1]
                    new_quantity = int(parts[2])
                    new_price = float(parts[3]) if len(parts) == 4 else None
                    
                    # Confirm the modify
                    print(f"\n⚠️  CONFIRM MODIFY ORDER:")
                    print(f"   Order ID: {order_id}")
                    print(f"   New Quantity: {new_quantity}")
                    if new_price:
                        print(f"   New Price: ${new_price}")
                    print(f"   Account: {self.selected_account['name']}")
                    
                    confirm = input("   Confirm? (y/N): ").strip().lower()
                    if confirm != 'y':
                        print("❌ Modify cancelled")
                        continue
                    
                    # Modify the order
                    result = await self.modify_order(order_id, new_quantity, new_price)
                    if "error" in result:
                        print(f"❌ Modify failed: {result['error']}")
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
                    if len(parts) < 2 or len(parts) > 4:
                        print("❌ Usage: history <symbol> [timeframe] [limit]")
                        print("   Example: history MNQ 1m 50")
                        continue
                    
                    symbol = parts[1]
                    timeframe = parts[2] if len(parts) > 2 else "1m"
                    limit = int(parts[3]) if len(parts) > 3 else 20
                    
                    # Get historical data
                    result = await self.get_historical_data(symbol, timeframe, limit)
                    if not result:
                        print(f"❌ No historical data available for {symbol}")
                        print("   This might indicate:")
                        print("   - Symbol is not available for historical data")
                        print("   - Timeframe is not supported")
                        print("   - Market is closed or data is not available")
                        print("   - Try a different symbol or timeframe")
                    else:
                        print(f"\n📊 Historical Data for {symbol.upper()} ({timeframe}):")
                        print(f"{'Time':<20} {'Open':<10} {'High':<10} {'Low':<10} {'Close':<10} {'Volume':<10}")
                        print("-" * 80)
                        for bar in result[-10:]:  # Show last 10 bars
                            time = bar.get('time', 'N/A')
                            open_price = bar.get('open', 0)
                            high = bar.get('high', 0)
                            low = bar.get('low', 0)
                            close = bar.get('close', 0)
                            volume = bar.get('volume', 0)
                            print(f"{time:<20} ${open_price:<9.2f} ${high:<9.2f} ${low:<9.2f} ${close:<9.2f} {volume:<10}")
                
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
                        print(f"✅ Account info retrieved successfully!")
                        print(f"   Response: {result}")
                
                else:
                    print("❌ Unknown command. Available commands:")
                    print("   trade, limit, bracket, native_bracket, stop, trail, positions, orders,")
                    print("   close, cancel, modify, quote, depth, history, monitor, flatten, contracts, accounts, help, quit")
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
