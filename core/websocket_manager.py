"""
WebSocket/SignalR Manager - Handles real-time market data connections.

This module provides SignalR connection management for real-time quotes and depth data.
"""

import logging
import os
import asyncio
import threading
from typing import Dict, Set, Optional, Any, Callable
from threading import Lock
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# SignalR is optional - imported conditionally
try:
    from signalrcore.hub_connection_builder import HubConnectionBuilder
    from signalrcore.transport.websockets.websocket_transport import WebsocketTransport
    SIGNALR_AVAILABLE = True
except ImportError:
    HubConnectionBuilder = None
    WebsocketTransport = None
    SIGNALR_AVAILABLE = False


class WebSocketManager:
    """
    Manages SignalR WebSocket connections for real-time market data.
    
    Handles connection lifecycle, subscriptions, and event callbacks.
    """
    
    def __init__(
        self,
        auth_manager,
        contract_manager,
        market_hub_url: Optional[str] = None,
        quote_event_name: Optional[str] = None,
        subscribe_method: Optional[str] = None
    ):
        """
        Initialize WebSocket manager.
        
        Args:
            auth_manager: AuthManager instance for token management
            contract_manager: ContractManager instance for contract ID resolution
            market_hub_url: SignalR hub URL (defaults to env var or standard URL)
            quote_event_name: Quote event name (defaults to env var or "Quote")
            subscribe_method: Subscribe method name (defaults to env var or "SubscribeContractQuotes")
        """
        self.auth_manager = auth_manager
        self.contract_manager = contract_manager
        
        self.market_hub_url = market_hub_url or os.getenv(
            "PROJECT_X_MARKET_HUB_URL",
            "https://rtc.topstepx.com/hubs/market"
        )
        self.quote_event_name = quote_event_name or os.getenv(
            "PROJECT_X_QUOTE_EVENT",
            "Quote"
        )
        self.subscribe_method = subscribe_method or os.getenv(
            "PROJECT_X_SUBSCRIBE_METHOD",
            "SubscribeContractQuotes"
        )
        self.unsubscribe_method = os.getenv(
            "PROJECT_X_UNSUBSCRIBE_METHOD",
            "UnsubscribeQuote"
        )
        
        self._hub = None
        self._connected = False
        self._subscribed_symbols: Set[str] = set()
        self._pending_symbols: Set[str] = set()
        self._lock = Lock()
        
        # Store event loop reference for async operations from sync callbacks
        self._event_loop: Optional[asyncio.AbstractEventLoop] = None
        
        # Event callbacks
        self._quote_callbacks: list[Callable] = []
        self._depth_callbacks: list[Callable] = []
        
        logger.debug("WebSocketManager initialized")
    
    async def start(self) -> bool:
        """
        Start the SignalR connection.
        
        Returns:
            True if connection started successfully
        """
        if self._connected:
            return True
        
        if not SIGNALR_AVAILABLE:
            logger.warning("SignalR not available - install signalrcore package")
            return False
        
        try:
            # Store event loop reference for async operations from sync callbacks
            try:
                self._event_loop = asyncio.get_running_loop()
            except RuntimeError:
                # No running loop, will handle in callbacks
                self._event_loop = None
            
            # CRITICAL FIX: Ensure token is valid and refreshed before connecting
            # This prevents 401 errors from expired tokens
            await self.auth_manager.ensure_valid_token()
            
            token = self.auth_manager.get_token()
            if not token:
                logger.error("No authentication token available after refresh")
                return False
            
            # Build URL with token
            url_with_token = self.market_hub_url
            if token and "access_token=" not in url_with_token:
                sep = '&' if '?' in url_with_token else '?'
                url_with_token = f"{url_with_token}{sep}access_token={token}"
            
            # Convert to ws/wss
            url_ws = url_with_token
            if url_ws.startswith("https://"):
                url_ws = "wss://" + url_ws[len("https://"):]
            elif url_ws.startswith("http://"):
                url_ws = "ws://" + url_ws[len("http://"):]
            
            headers = {"Authorization": f"Bearer {token}"} if token else {}
            
            # Create access_token_factory that always gets fresh token
            def get_fresh_token():
                """Get fresh token, refreshing if needed."""
                # Check if we need to refresh (this is sync, so we can't await)
                # The token should already be fresh from ensure_valid_token() above
                return self.auth_manager.get_token() or ""
            
            hub = (
                HubConnectionBuilder()
                .with_url(
                    url_ws,
                    options={
                        "headers": headers,
                        "skip_negotiation": True,  # TopStepX SignalR works without negotiation
                        "access_token_factory": get_fresh_token,
                        "transport": WebsocketTransport
                    }
                )
                .with_automatic_reconnect({
                    "type": "raw",
                    "keep_alive_interval": 15,
                    "reconnect_interval": 5,
                    "max_attempts": 10
                })
                .build()
            )
            
            def on_open():
                logger.info("✅ SignalR Market Hub connected")
                with self._lock:
                    self._connected = True
                # Flush pending subscriptions - safely handle async from sync callback
                # SignalR callbacks are synchronous, so we need to handle event loop carefully
                if self._event_loop and self._event_loop.is_running():
                    # We have a running event loop - schedule the task
                    self._event_loop.create_task(self._flush_pending_subscriptions())
                else:
                    # No running loop - run in a separate thread
                    def run_in_thread():
                        new_loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(new_loop)
                        try:
                            new_loop.run_until_complete(self._flush_pending_subscriptions())
                        except Exception as e:
                            logger.debug(f"Error flushing subscriptions in thread: {e}")
                        finally:
                            new_loop.close()
                    thread = threading.Thread(target=run_in_thread, daemon=True)
                    thread.start()
            
            def on_close():
                logger.warning("⚠️  SignalR Market Hub disconnected")
                with self._lock:
                    self._connected = False
            
            def on_error(err):
                try:
                    error_text = str(err)
                    # Handle authentication errors - try to refresh token and reconnect
                    if "401" in error_text or "403" in error_text or "Unauthorized" in error_text or "Forbidden" in error_text:
                        logger.warning(f"SignalR authentication error (401/403): {error_text}")
                        logger.info("Attempting to refresh token and reconnect...")
                        # Schedule token refresh and reconnection
                        asyncio.create_task(self._handle_auth_error_and_reconnect())
                        return
                    logger.error(f"SignalR Market Hub error: {error_text}")
                except Exception:
                    logger.error(f"SignalR Market Hub error: {err}")
            
            def on_quote(*args):
                """Handle quote events."""
                try:
                    # Normalize payload
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
                    
                    # Extract symbol from contract ID
                    symbol = ""
                    if isinstance(cid, str) and "." in cid:
                        parts = cid.split(".")
                        symbol = parts[-2].upper() if len(parts) >= 2 else cid
                    
                    if not symbol:
                        return
                    
                    # Call registered callbacks
                    for callback in self._quote_callbacks:
                        try:
                            callback(symbol, data)
                        except Exception as e:
                            logger.debug(f"Quote callback error: {e}")
                            
                except Exception as e:
                    logger.debug(f"Failed processing quote message: {e}")
            
            def on_depth(*args):
                """Handle depth events."""
                try:
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
                    
                    symbol = ""
                    if isinstance(cid, str) and "." in cid:
                        parts = cid.split(".")
                        symbol = parts[-2].upper() if len(parts) >= 2 else cid
                    
                    if not symbol:
                        return
                    
                    # Call registered callbacks
                    for callback in self._depth_callbacks:
                        try:
                            callback(symbol, data)
                        except Exception as e:
                            logger.debug(f"Depth callback error: {e}")
                            
                except Exception as e:
                    logger.debug(f"Failed processing depth message: {e}")
            
            hub.on_open(on_open)
            hub.on_close(on_close)
            hub.on_error(on_error)
            
            # Register quote event handlers
            event_names = [
                self.quote_event_name,
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
                        logger.debug(f"Registered quote handler for event '{ev}'")
                    except Exception:
                        pass
            
            # Register depth event handlers
            depth_events = ["Depth", "OrderBook", "Level2", "MarketDepth", "GatewayDepth"]
            for ev in depth_events:
                try:
                    hub.on(ev, on_depth)
                except Exception:
                    pass
            
            # Start connection
            hub.start()
            self._hub = hub
            
            # Wait for connection
            import time
            start = time.time()
            while not self._connected and time.time() - start < 10:
                await asyncio.sleep(0.05)
            
            if self._connected:
                logger.info("✅ SignalR Market Hub connection established")
                return True
            else:
                logger.warning("⚠️  SignalR connection timeout")
                return False
                
        except Exception as e:
            logger.error(f"Failed to start SignalR connection: {e}")
            # If it's an auth error, try refreshing token once more
            if "401" in str(e) or "403" in str(e) or "Unauthorized" in str(e):
                logger.info("Retrying with fresh token...")
                try:
                    await self.auth_manager.ensure_valid_token()
                    # Retry connection (but only once to avoid infinite loop)
                    return await self._retry_connection()
                except Exception as retry_error:
                    logger.error(f"Retry failed: {retry_error}")
            return False
    
    async def _handle_auth_error_and_reconnect(self):
        """Handle authentication error by refreshing token and reconnecting."""
        try:
            logger.info("Refreshing authentication token...")
            await self.auth_manager.ensure_valid_token()
            
            # Stop current connection
            if self._hub:
                try:
                    self._hub.stop()
                except:
                    pass
                self._hub = None
            
            with self._lock:
                self._connected = False
            
            # Wait a moment before reconnecting
            await asyncio.sleep(1)
            
            # Retry connection
            logger.info("Reconnecting SignalR with fresh token...")
            success = await self.start()
            if success:
                logger.info("✅ SignalR reconnected successfully after token refresh")
            else:
                logger.warning("⚠️  SignalR reconnection failed after token refresh")
        except Exception as e:
            logger.error(f"Error during auth error recovery: {e}")
    
    async def _retry_connection(self) -> bool:
        """Retry connection after token refresh (internal method to avoid recursion)."""
        # This is a simplified retry - just return False to let caller handle
        # Full retry logic is in _handle_auth_error_and_reconnect
        return False
    
    async def _flush_pending_subscriptions(self):
        """Flush pending symbol subscriptions."""
        with self._lock:
            pending = list(self._pending_symbols)
            self._pending_symbols.clear()
        
        for sym in pending:
            await self.subscribe_quote(sym)
    
    async def subscribe_quote(self, symbol: str) -> bool:
        """
        Subscribe to real-time quotes for a symbol.
        
        Args:
            symbol: Trading symbol (e.g., "MNQ", "ES")
            
        Returns:
            True if subscription successful
        """
        sym = symbol.upper()
        
        with self._lock:
            if sym in self._subscribed_symbols:
                return True
            
            if not self._connected:
                self._pending_symbols.add(sym)
                logger.debug(f"Queued subscription for {sym} until hub connects")
                return False
        
        try:
            contract_id = self.contract_manager.get_contract_id(sym)
            
            logger.info(f"📡 Subscribing to live quotes for {sym} (contract: {contract_id})")
            self._hub.send(self.subscribe_method, [contract_id])
            
            with self._lock:
                self._subscribed_symbols.add(sym)
            
            logger.info(f"✅ Subscribed to quotes for {sym} via {contract_id}")
            return True
            
        except ValueError as e:
            logger.warning(f"Cannot subscribe to quotes for {sym}: {e}")
            return False
        except Exception as e:
            logger.error(f"Failed to subscribe to quotes for {sym}: {e}")
            return False
    
    async def subscribe_depth(self, symbol: str) -> bool:
        """
        Subscribe to market depth for a symbol.
        
        Args:
            symbol: Trading symbol (e.g., "MNQ", "ES")
            
        Returns:
            True if subscription attempted (may fallback to REST API)
        """
        sym = symbol.upper()
        
        if not self._connected:
            logger.debug(f"Market hub not connected, cannot subscribe to depth for {sym}")
            return False
        
        try:
            contract_id = self.contract_manager.get_contract_id(sym)
            
            # Try different depth subscription methods
            depth_methods = [
                "SubscribeContractDepth",
                "SubscribeDepth",
                "SubscribeOrderBook",
                "SubscribeLevel2"
            ]
            
            for method in depth_methods:
                try:
                    self._hub.send(method, [contract_id])
                    logger.debug(f"Attempted depth subscription for {sym} via {contract_id} using {method}")
                    return True
                except Exception as e:
                    error_str = str(e)
                    if "does not exist" not in error_str.lower() and "Method" not in error_str:
                        logger.debug(f"Depth method {method} failed: {e}")
                    continue
            
            logger.debug(f"All depth subscription methods failed for {sym} - will use REST API fallback")
            return False
            
        except ValueError as e:
            logger.warning(f"Cannot subscribe to depth for {sym}: {e}")
            return False
        except Exception as e:
            logger.debug(f"Depth subscription error for {sym}: {e}")
            return False
    
    def register_quote_callback(self, callback: Callable[[str, Dict], None]):
        """
        Register a callback for quote events.
        
        Args:
            callback: Function(symbol: str, data: Dict) -> None
        """
        self._quote_callbacks.append(callback)
        logger.debug(f"Registered quote callback: {callback.__name__ if hasattr(callback, '__name__') else 'anonymous'}")
    
    def register_depth_callback(self, callback: Callable[[str, Dict], None]):
        """
        Register a callback for depth events.
        
        Args:
            callback: Function(symbol: str, data: Dict) -> None
        """
        self._depth_callbacks.append(callback)
        logger.debug(f"Registered depth callback: {callback.__name__ if hasattr(callback, '__name__') else 'anonymous'}")
    
    def is_connected(self) -> bool:
        """Check if SignalR connection is active."""
        with self._lock:
            return self._connected
    
    def get_subscribed_symbols(self) -> Set[str]:
        """Get set of currently subscribed symbols."""
        with self._lock:
            return self._subscribed_symbols.copy()
    
    async def stop(self):
        """Stop the SignalR connection."""
        if self._hub:
            try:
                self._hub.stop()
            except Exception as e:
                logger.debug(f"Error stopping hub: {e}")
        
        with self._lock:
            self._connected = False
            self._subscribed_symbols.clear()
            self._pending_symbols.clear()
        
        logger.info("SignalR Market Hub stopped")

