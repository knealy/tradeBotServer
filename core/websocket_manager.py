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

from core.events import Event, EventType

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
        subscribe_method: Optional[str] = None,
        event_bus: Optional[Any] = None,
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

        # Optional in-process fan-out (strategies, GUI, etc.)
        self.event_bus = event_bus
        
        self._hub = None
        self._connected = False
        self._subscribed_symbols: Set[str] = set()
        self._pending_symbols: Set[str] = set()
        self._lock = Lock()
        self._reconnecting = False  # Flag to prevent multiple simultaneous reconnection attempts
        
        # Track symbols that have already logged "hub not running" warnings to suppress spam
        self._hub_not_running_warned: Set[str] = set()
        
        # Store event loop reference for async operations from sync callbacks
        self._event_loop: Optional[asyncio.AbstractEventLoop] = None
        # Set from SignalR on_open (thread-safe via call_soon_threadsafe); replaces 50ms spin-wait.
        self._connect_ready = asyncio.Event()
        
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
        # Prevent multiple simultaneous connection attempts
        with self._lock:
            if self._connected:
                return True
            # If hub exists but not connected, stop it first
            if self._hub:
                try:
                    self._hub.stop()
                except Exception:
                    logger.debug("SignalR hub.stop() failed before start()", exc_info=True)
                self._hub = None
        
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
                    # Clear warning set so we can log warnings again if hub disconnects
                    self._hub_not_running_warned.clear()
                loop = self._event_loop
                if loop and loop.is_running():
                    loop.call_soon_threadsafe(self._connect_ready.set)
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
                with self._lock:
                    # Only log and reconnect if we were actually connected
                    if self._connected:
                        logger.warning("⚠️  SignalR Market Hub disconnected")
                        self._connected = False
                        loop = self._event_loop
                        if loop and loop.is_running():
                            loop.call_soon_threadsafe(self._connect_ready.clear)
                        # Schedule reconnection attempt for network interruptions
                        # Use create_task if loop is running, otherwise schedule in thread
                        if self._event_loop and self._event_loop.is_running():
                            self._event_loop.create_task(self._handle_network_interruption_and_reconnect())
                        else:
                            def run_in_thread():
                                new_loop = asyncio.new_event_loop()
                                asyncio.set_event_loop(new_loop)
                                try:
                                    new_loop.run_until_complete(self._handle_network_interruption_and_reconnect())
                                except Exception as e:
                                    logger.debug(f"Error reconnecting in thread: {e}")
                                finally:
                                    new_loop.close()
                            thread = threading.Thread(target=run_in_thread, daemon=True)
                            thread.start()
                    else:
                        # Already disconnected, just a cleanup call - don't log or reconnect
                        logger.debug("SignalR close callback called (already disconnected)")
            
            def on_error(err):
                try:
                    error_text = str(err)
                    error_type = type(err).__name__
                    
                    # Handle CompletionMessage (often just informational, not always an error)
                    if error_type == "CompletionMessage" or "CompletionMessage" in error_text:
                        # Check if it's actually an error or just a completion
                        if hasattr(err, 'error') and err.error:
                            logger.warning(f"SignalR completion with error: {err.error}")
                        else:
                            # Not an actual error, just a completion message - log at debug level
                            logger.debug(f"SignalR hub method completed: {error_text}")
                        return
                    
                    # Handle network interruptions (sleep mode, network down, etc.)
                    if (
                        isinstance(err, OSError) or
                        "Network is down" in error_text or
                        "Errno 50" in error_text or
                        "Connection closed" in error_text or
                        "Connection reset" in error_text or
                        "Broken pipe" in error_text or
                        error_type == "OSError"
                    ):
                        logger.warning(f"SignalR network interruption detected: {error_text}")
                        logger.info("Will attempt to reconnect when network is restored...")
                        # Schedule reconnection with exponential backoff
                        if self._event_loop and self._event_loop.is_running():
                            self._event_loop.create_task(self._handle_network_interruption_and_reconnect())
                        else:
                            def run_in_thread():
                                new_loop = asyncio.new_event_loop()
                                asyncio.set_event_loop(new_loop)
                                try:
                                    new_loop.run_until_complete(self._handle_network_interruption_and_reconnect())
                                except Exception as e:
                                    logger.debug(f"Error reconnecting in thread: {e}")
                                finally:
                                    new_loop.close()
                            thread = threading.Thread(target=run_in_thread, daemon=True)
                            thread.start()
                        return
                    
                    # Handle authentication errors - try to refresh token and reconnect
                    if "401" in error_text or "403" in error_text or "Unauthorized" in error_text or "Forbidden" in error_text:
                        logger.warning(f"SignalR authentication error (401/403): {error_text}")
                        logger.info("Attempting to refresh token and reconnect...")
                        # Schedule token refresh and reconnection
                        if self._event_loop and self._event_loop.is_running():
                            asyncio.create_task(self._handle_auth_error_and_reconnect())
                        else:
                            def run_in_thread():
                                new_loop = asyncio.new_event_loop()
                                asyncio.set_event_loop(new_loop)
                                try:
                                    new_loop.run_until_complete(self._handle_auth_error_and_reconnect())
                                except Exception as e:
                                    logger.debug(f"Error reconnecting in thread: {e}")
                                finally:
                                    new_loop.close()
                            thread = threading.Thread(target=run_in_thread, daemon=True)
                            thread.start()
                        return
                    
                    # Handle server-side errors (502, 503, 504) - these require waiting before retry
                    if "502" in error_text or "503" in error_text or "504" in error_text or "Gateway" in error_text or "Bad Gateway" in error_text or "Service Unavailable" in error_text:
                        logger.warning(f"SignalR server error (5xx): {error_text}")
                        logger.info("Server appears overloaded or unavailable. Will retry with longer delay...")
                        # Use network interruption handler but it will use exponential backoff
                        if self._event_loop and self._event_loop.is_running():
                            self._event_loop.create_task(self._handle_network_interruption_and_reconnect())
                        else:
                            def run_in_thread():
                                new_loop = asyncio.new_event_loop()
                                asyncio.set_event_loop(new_loop)
                                try:
                                    new_loop.run_until_complete(self._handle_network_interruption_and_reconnect())
                                except Exception as e:
                                    logger.debug(f"Error reconnecting in thread: {e}")
                                finally:
                                    new_loop.close()
                            thread = threading.Thread(target=run_in_thread, daemon=True)
                            thread.start()
                        return
                    
                    logger.error(f"SignalR Market Hub error: {error_text}")
                except Exception as handler_exc:
                    logger.error(
                        "SignalR on_error handler failed: %s (original err=%s)",
                        handler_exc,
                        err,
                        exc_info=True,
                    )
            
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

                    # EventBus fan-out (safe from SignalR threads)
                    bus = getattr(self, "event_bus", None)
                    loop = getattr(self, "_event_loop", None)
                    if bus is not None and getattr(bus, "_running", False) and loop is not None and loop.is_running():
                        try:
                            asyncio.run_coroutine_threadsafe(
                                bus.publish(
                                    Event(
                                        type=EventType.QUOTE_UPDATED,
                                        data={"symbol": symbol, "quote": data},
                                        timestamp=datetime.now(timezone.utc),
                                        source="websocket_manager",
                                    )
                                ),
                                loop,
                            )
                        except Exception:
                            logger.debug("Failed publishing QUOTE_UPDATED to EventBus", exc_info=True)
                    
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
                    except Exception as reg_exc:
                        logger.debug(
                            "hub.on skipped for quote event %r: %s", ev, reg_exc, exc_info=True
                        )
            
            # Register depth event handlers
            depth_events = ["Depth", "OrderBook", "Level2", "MarketDepth", "GatewayDepth"]
            for ev in depth_events:
                try:
                    hub.on(ev, on_depth)
                except Exception as reg_exc:
                    logger.debug(
                        "hub.on skipped for depth event %r: %s", ev, reg_exc, exc_info=True
                    )
            
            # Start connection
            if self._event_loop:
                self._connect_ready.clear()
            hub.start()
            self._hub = hub
            
            # Wait for connection to be established (on_open sets _connect_ready)
            if self._event_loop:
                try:
                    await asyncio.wait_for(self._connect_ready.wait(), timeout=10.0)
                except asyncio.TimeoutError:
                    logger.warning("⚠️  SignalR connection timeout")
                    return False
            else:
                import time
                deadline = time.time() + 10.0
                while not self._connected and time.time() < deadline:
                    await asyncio.sleep(0.05)
            
            if not self._connected:
                logger.warning("⚠️  SignalR connection timeout")
                return False
            
            # CRITICAL: Wait for hub to be fully ready to accept subscriptions
            # The on_open callback fires but hub needs additional time to reach "running" state
            logger.debug("Waiting for SignalR hub to reach running state...")
            await asyncio.sleep(0.5)  # Give hub time to fully initialize
            
            logger.info("✅ SignalR Market Hub connection established and ready")
            return True
                
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
    
    async def _handle_network_interruption_and_reconnect(self):
        """Handle network interruptions (sleep mode, network down) with exponential backoff."""
        # Prevent multiple simultaneous reconnection attempts
        with self._lock:
            if self._reconnecting:
                logger.debug("Reconnection already in progress, skipping duplicate attempt")
                return
            if self._connected:
                return
            self._reconnecting = True
        
        try:
            
            # Exponential backoff: 2s, 4s, 8s, 16s, 30s (max)
            max_attempts = 10
            base_delay = 2
            
            for attempt in range(max_attempts):
                delay = min(base_delay * (2 ** attempt), 30)  # Cap at 30 seconds
                
                logger.info(f"Attempting to reconnect SignalR (attempt {attempt + 1}/{max_attempts}) after {delay}s delay...")
                await asyncio.sleep(delay)
                
                # Check if network is available by trying to refresh token
                try:
                    await self.auth_manager.ensure_valid_token()
                except Exception as e:
                    logger.debug(f"Network not ready yet (attempt {attempt + 1}): {e}")
                    continue
                
                # Stop old connection if it exists
                if self._hub:
                    try:
                        self._hub.stop()
                    except Exception:
                        logger.debug("SignalR hub.stop() failed during network recovery", exc_info=True)
                    self._hub = None
                
                with self._lock:
                    self._connected = False
                
                # Try to reconnect
                try:
                    success = await self.start()
                    if success:
                        logger.info(f"✅ SignalR reconnected successfully after network interruption (attempt {attempt + 1})")
                        with self._lock:
                            self._reconnecting = False
                        # Re-subscribe to all symbols
                        await self._resubscribe_all_symbols()
                        return
                    else:
                        logger.debug(f"Reconnection attempt {attempt + 1} failed, will retry...")
                except Exception as e:
                    logger.debug(f"Reconnection attempt {attempt + 1} error: {e}")
                    continue
            
            logger.warning(f"⚠️  SignalR reconnection failed after {max_attempts} attempts")
            with self._lock:
                self._reconnecting = False
        except Exception as e:
            logger.error(f"Error during network interruption recovery: {e}")
            with self._lock:
                self._reconnecting = False
    
    async def _resubscribe_all_symbols(self):
        """Re-subscribe to all previously subscribed symbols after reconnection."""
        with self._lock:
            symbols_to_resubscribe = list(self._subscribed_symbols)
            # Clear and re-add to pending to ensure they get subscribed
            self._subscribed_symbols.clear()
            self._pending_symbols.update(symbols_to_resubscribe)
        
        # Flush pending subscriptions
        await self._flush_pending_subscriptions()
        logger.info(f"Re-subscribed to {len(symbols_to_resubscribe)} symbols after reconnection")
    
    async def _handle_auth_error_and_reconnect(self):
        """Handle authentication error by refreshing token and reconnecting."""
        try:
            logger.info("Refreshing authentication token...")
            await self.auth_manager.ensure_valid_token()
            
            # Stop current connection
            if self._hub:
                try:
                    self._hub.stop()
                except Exception:
                    logger.debug("SignalR hub.stop() failed during auth recovery", exc_info=True)
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
                # Re-subscribe to all symbols
                await self._resubscribe_all_symbols()
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
            
            # Check if hub is actually running before sending
            if not self._hub:
                # Use debug level to avoid spam - hub will be initialized when connection starts
                logger.debug(f"Cannot subscribe to quotes for {sym}: Hub not initialized")
                with self._lock:
                    self._pending_symbols.add(sym)
                return False
            
            # Check hub state - SignalR hub must be in running state
            try:
                # SignalR hub has a state property that indicates if it's running
                if hasattr(self._hub, 'transport') and self._hub.transport:
                    if hasattr(self._hub.transport, '_ws') and not self._hub.transport._ws:
                        # Use debug level to avoid spam - transport will connect when hub starts
                        logger.debug(f"Cannot subscribe to quotes for {sym}: Hub transport not connected")
                        with self._lock:
                            self._pending_symbols.add(sym)
                        return False
            except Exception:
                logger.debug("Could not inspect SignalR hub transport state; attempting subscribe", exc_info=True)
            
            logger.debug(f"Subscribing to live quotes for {sym} (contract: {contract_id})")
            
            # Try subscription with retries (hub may take a moment to be fully ready)
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    self._hub.send(self.subscribe_method, [contract_id])
                    
                    with self._lock:
                        self._subscribed_symbols.add(sym)
                    
                    logger.debug(f"Subscribed to quotes for {sym} via {contract_id}")
                    return True
                except Exception as retry_error:
                    if "not running" in str(retry_error).lower() or "cand send" in str(retry_error).lower():
                        if attempt < max_retries - 1:
                            # Hub not ready yet, wait and retry
                            logger.debug(f"Hub not ready for {sym}, retrying in {0.2 * (attempt + 1)}s...")
                            await asyncio.sleep(0.2 * (attempt + 1))
                            continue
                    # Other error or final retry - raise it
                    raise retry_error
            
        except ValueError as e:
            logger.warning(f"Cannot subscribe to quotes for {sym}: {e}")
            return False
        except Exception as e:
            error_msg = str(e)
            # Handle "Hub is not running" errors gracefully
            # Note: SignalR library has typo "cand" instead of "can't" in error message
            if "not running" in error_msg.lower() or "cand send" in error_msg.lower() or "can't send" in error_msg.lower():
                # Only log warning once per symbol to avoid spam
                with self._lock:
                    if sym not in self._hub_not_running_warned:
                        logger.warning(f"Cannot subscribe to quotes for {sym}: Hub is not running (will retry when connected)")
                        self._hub_not_running_warned.add(sym)
                    else:
                        # Subsequent attempts use debug level to avoid spam
                        logger.debug(f"Cannot subscribe to quotes for {sym}: Hub is not running (will retry when connected)")
                # Queue for retry when hub is ready
                with self._lock:
                    self._pending_symbols.add(sym)
                return False
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
        try:
            self._connect_ready.clear()
        except Exception:
            logger.debug("connect_ready clear after stop", exc_info=True)
        
        logger.info("SignalR Market Hub stopped")

