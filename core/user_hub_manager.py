"""
User Hub Manager - Handles SignalR User Hub connections for real-time account/position/order updates.

This module provides SignalR connection management for real-time user data (accounts, positions, orders, trades).
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


class UserHubManager:
    """
    Manages SignalR User Hub WebSocket connections for real-time user data.
    
    Handles connection lifecycle, subscriptions, and event callbacks for:
    - Account updates (balance, PnL, etc.)
    - Position updates
    - Order updates
    - Trade updates
    """
    
    def __init__(
        self,
        auth_manager,
        user_hub_url: Optional[str] = None
    ):
        """
        Initialize User Hub manager.
        
        Args:
            auth_manager: AuthManager instance for token management
            user_hub_url: SignalR User Hub URL (defaults to env var or standard URL)
        """
        self.auth_manager = auth_manager
        
        self.user_hub_url = user_hub_url or os.getenv(
            "PROJECT_X_USER_HUB_URL",
            "https://rtc.topstepx.com/hubs/user"
        )
        
        self._hub = None
        self._connected = False
        self._lock = Lock()
        self._reconnecting = False
        self._subscribed_account_id = None
        
        # Store event loop reference for async operations from sync callbacks
        self._event_loop: Optional[asyncio.AbstractEventLoop] = None
        
        # Event callbacks
        self._account_callbacks: list[Callable] = []
        self._position_callbacks: list[Callable] = []
        self._order_callbacks: list[Callable] = []
        self._trade_callbacks: list[Callable] = []
        
        logger.debug("UserHubManager initialized")
    
    async def start(self, account_id: Optional[int] = None) -> bool:
        """
        Start the SignalR User Hub connection.
        
        Args:
            account_id: Account ID to subscribe to (uses selected account if not provided)
        
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
                except:
                    pass
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
            
            # Ensure token is valid before connecting
            await self.auth_manager.ensure_valid_token()
            
            token = self.auth_manager.get_token()
            if not token:
                logger.error("No authentication token available after refresh")
                return False
            
            # Build URL with token
            url_with_token = self.user_hub_url
            if token and "access_token=" not in url_with_token:
                sep = '&' if '?' in url_with_token else '?'
                url_with_token = f"{url_with_token}{sep}access_token={token}"
            
            # Convert to ws/wss
            url_ws = url_with_token
            if url_ws.startswith("https://"):
                url_ws = "wss://" + url_ws[len("https://"):]
            elif url_ws.startswith("http://"):
                url_ws = "ws://" + url_ws[len("http://"):]
            
            # Build headers
            headers = {"Authorization": f"Bearer {token}"} if token else {}
            
            # Create fresh token factory
            def get_fresh_token():
                return self.auth_manager.get_token() or ""
            
            hub = (
                HubConnectionBuilder()
                .with_url(
                    url_ws,
                    options={
                        "headers": headers,
                        "skip_negotiation": True,
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
                logger.info("✅ SignalR User Hub connected")
                self._connected = True
                # Subscribe to updates - use send() method instead of invoke()
                try:
                    # Wait a moment for connection to stabilize (don't block the event loop thread)
                    try:
                        if self._event_loop and self._event_loop.is_running():
                            self._event_loop.call_soon_threadsafe(lambda: None)
                    except Exception:
                        pass
                    
                    # Subscribe to updates - try multiple methods based on SignalR library version
                    subscription_success = False
                    
                    # Method 1: Try send() with method name and arguments
                    if hasattr(hub, 'send'):
                        try:
                            hub.send('SubscribeAccounts', [])
                            if account_id:
                                hub.send('SubscribeOrders', [account_id])
                                hub.send('SubscribePositions', [account_id])
                                hub.send('SubscribeTrades', [account_id])
                            subscription_success = True
                            logger.debug("Used send() method for subscriptions")
                        except Exception as e:
                            logger.debug(f"send() method failed: {e}")
                    
                    # Method 2: Try invoke() with method name
                    if not subscription_success and hasattr(hub, 'invoke'):
                        try:
                            hub.invoke('SubscribeAccounts')
                            if account_id:
                                hub.invoke('SubscribeOrders', account_id)
                                hub.invoke('SubscribePositions', account_id)
                                hub.invoke('SubscribeTrades', account_id)
                            subscription_success = True
                            logger.debug("Used invoke() method for subscriptions")
                        except Exception as e:
                            logger.debug(f"invoke() method failed: {e}")
                    
                    # Method 3: Try direct method calls
                    if not subscription_success:
                        try:
                            if hasattr(hub, 'SubscribeAccounts'):
                                hub.SubscribeAccounts()
                                if account_id:
                                    if hasattr(hub, 'SubscribeOrders'):
                                        hub.SubscribeOrders(account_id)
                                    if hasattr(hub, 'SubscribePositions'):
                                        hub.SubscribePositions(account_id)
                                    if hasattr(hub, 'SubscribeTrades'):
                                        hub.SubscribeTrades(account_id)
                                subscription_success = True
                                logger.debug("Used direct method calls for subscriptions")
                        except Exception as e:
                            logger.debug(f"Direct method calls failed: {e}")
                    
                    if not subscription_success:
                        logger.warning("⚠️  Could not subscribe to User Hub - no working subscription method found")
                    
                    self._subscribed_account_id = account_id
                    logger.info("✅ Subscribed to User Hub updates")
                except Exception as e:
                    logger.error(f"Failed to subscribe to User Hub: {e}")
                    import traceback
                    logger.debug(traceback.format_exc())
            
            def on_close():
                logger.warning("⚠️  SignalR User Hub disconnected")
                self._connected = False
            
            def on_error(err):
                error_text = str(err)
                if '401' in error_text or '403' in error_text or 'Unauthorized' in error_text:
                    logger.warning("⚠️  User Hub authentication error - will reconnect with fresh token")
                    # SignalR error callback can run off the main event loop thread.
                    # Schedule reconnection safely on the captured loop when possible.
                    try:
                        if self._event_loop and self._event_loop.is_running():
                            asyncio.run_coroutine_threadsafe(self._handle_auth_error_and_reconnect(account_id), self._event_loop)
                        else:
                            threading.Thread(target=lambda: asyncio.run(self._handle_auth_error_and_reconnect(account_id)), daemon=True).start()
                    except Exception as e:
                        logger.error(f"Failed to schedule auth-error reconnect: {e}")
                else:
                    logger.error(f"User Hub error: {err}")
            
            # Register event handlers
            hub.on_open(on_open)
            hub.on_close(on_close)
            hub.on_error(on_error)
            
            # Register User Hub event handlers
            hub.on('GatewayUserAccount', self._handle_account_update)
            hub.on('GatewayUserPosition', self._handle_position_update)
            hub.on('GatewayUserOrder', self._handle_order_update)
            hub.on('GatewayUserTrade', self._handle_trade_update)
            
            # Handle reconnection (if method exists). signalrcore may invoke with no args during
            # transport ping/reconnect — accept *args so we never raise TypeError.
            try:
                if hasattr(hub, 'on_reconnect'):
                    hub.on_reconnect(lambda *args, **kwargs: self._on_reconnected(account_id))
                elif hasattr(hub, 'onreconnected'):
                    hub.onreconnected(lambda *args, **kwargs: self._on_reconnected(account_id))
            except Exception as e:
                logger.debug(f"Could not register reconnection handler: {e}")
            
            # Start connection
            hub.start()
            self._hub = hub
            
            # Wait for connection
            import time
            start = time.time()
            while not self._connected and time.time() - start < 10:
                await asyncio.sleep(0.05)
            
            if self._connected:
                logger.info("✅ SignalR User Hub connection established")
                return True
            else:
                logger.warning("⚠️  SignalR User Hub connection timeout")
                return False
                
        except Exception as e:
            logger.error(f"Failed to start User Hub connection: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False
    
    def _handle_account_update(self, data):
        """Handle GatewayUserAccount event."""
        try:
            logger.debug(f"Received account update: {data}")
            # SignalR library often passes event args as a list.
            # Normalize payload so downstream callbacks receive dicts.
            payloads = []
            if isinstance(data, list):
                # either [dict] or [dict, dict, ...]
                payloads = [d for d in data if d is not None]
            else:
                payloads = [data]

            # Call all registered callbacks
            for callback in self._account_callbacks:
                try:
                    for payload in payloads:
                        if asyncio.iscoroutinefunction(callback):
                            if self._event_loop and self._event_loop.is_running():
                                # Run coroutine on the captured loop (SignalR callbacks run on a non-async thread)
                                asyncio.run_coroutine_threadsafe(callback(payload), self._event_loop)
                            else:
                                # Fallback: run in thread with its own loop
                                threading.Thread(target=lambda p=payload: asyncio.run(callback(p)), daemon=True).start()
                        else:
                            callback(payload)
                except Exception as e:
                    logger.error(f"Error in account callback: {e}")
        except Exception as e:
            logger.error(f"Error handling account update: {e}")
    
    def _handle_position_update(self, data):
        """Handle GatewayUserPosition event."""
        try:
            logger.debug(f"Received position update: {data}")
            payloads = []
            if isinstance(data, list):
                payloads = [d for d in data if d is not None]
            else:
                payloads = [data]
            for callback in self._position_callbacks:
                try:
                    for payload in payloads:
                        if asyncio.iscoroutinefunction(callback):
                            if self._event_loop and self._event_loop.is_running():
                                asyncio.run_coroutine_threadsafe(callback(payload), self._event_loop)
                            else:
                                threading.Thread(target=lambda p=payload: asyncio.run(callback(p)), daemon=True).start()
                        else:
                            callback(payload)
                except Exception as e:
                    logger.error(f"Error in position callback: {e}")
        except Exception as e:
            logger.error(f"Error handling position update: {e}")
    
    def _handle_order_update(self, data):
        """Handle GatewayUserOrder event."""
        try:
            logger.debug(f"Received order update: {data}")
            payloads = []
            if isinstance(data, list):
                payloads = [d for d in data if d is not None]
            else:
                payloads = [data]
            for callback in self._order_callbacks:
                try:
                    for payload in payloads:
                        if asyncio.iscoroutinefunction(callback):
                            if self._event_loop and self._event_loop.is_running():
                                asyncio.run_coroutine_threadsafe(callback(payload), self._event_loop)
                            else:
                                threading.Thread(target=lambda p=payload: asyncio.run(callback(p)), daemon=True).start()
                        else:
                            callback(payload)
                except Exception as e:
                    logger.error(f"Error in order callback: {e}")
        except Exception as e:
            logger.error(f"Error handling order update: {e}")
    
    def _handle_trade_update(self, data):
        """Handle GatewayUserTrade event."""
        try:
            logger.debug(f"Received trade update: {data}")
            payloads = []
            if isinstance(data, list):
                payloads = [d for d in data if d is not None]
            else:
                payloads = [data]
            for callback in self._trade_callbacks:
                try:
                    for payload in payloads:
                        if asyncio.iscoroutinefunction(callback):
                            if self._event_loop and self._event_loop.is_running():
                                asyncio.run_coroutine_threadsafe(callback(payload), self._event_loop)
                            else:
                                threading.Thread(target=lambda p=payload: asyncio.run(callback(p)), daemon=True).start()
                        else:
                            callback(payload)
                except Exception as e:
                    logger.error(f"Error in trade callback: {e}")
        except Exception as e:
            logger.error(f"Error handling trade update: {e}")
    
    def _on_reconnected(self, account_id: Optional[int]):
        """Handle reconnection - resubscribe to updates."""
        try:
            if self._hub and self._connected:
                # Wait a moment for connection to stabilize (avoid blocking)
                try:
                    if self._event_loop and self._event_loop.is_running():
                        self._event_loop.call_soon_threadsafe(lambda: None)
                except Exception:
                    pass
                
                # Use the same subscription method as on_open
                subscription_success = False
                
                # Method 1: Try send() with method name and arguments
                if hasattr(self._hub, 'send'):
                    try:
                        self._hub.send('SubscribeAccounts', [])
                        if account_id:
                            self._hub.send('SubscribeOrders', [account_id])
                            self._hub.send('SubscribePositions', [account_id])
                            self._hub.send('SubscribeTrades', [account_id])
                        subscription_success = True
                        logger.debug("Used send() method for reconnection subscriptions")
                    except Exception as e:
                        logger.debug(f"send() method failed on reconnect: {e}")
                
                # Method 2: Try invoke() with method name
                if not subscription_success and hasattr(self._hub, 'invoke'):
                    try:
                        self._hub.invoke('SubscribeAccounts')
                        if account_id:
                            self._hub.invoke('SubscribeOrders', account_id)
                            self._hub.invoke('SubscribePositions', account_id)
                            self._hub.invoke('SubscribeTrades', account_id)
                        subscription_success = True
                        logger.debug("Used invoke() method for reconnection subscriptions")
                    except Exception as e:
                        logger.debug(f"invoke() method failed on reconnect: {e}")
                
                # Method 3: Try direct method calls
                if not subscription_success:
                    try:
                        if hasattr(self._hub, 'SubscribeAccounts'):
                            self._hub.SubscribeAccounts()
                            if account_id:
                                if hasattr(self._hub, 'SubscribeOrders'):
                                    self._hub.SubscribeOrders(account_id)
                                if hasattr(self._hub, 'SubscribePositions'):
                                    self._hub.SubscribePositions(account_id)
                                if hasattr(self._hub, 'SubscribeTrades'):
                                    self._hub.SubscribeTrades(account_id)
                            subscription_success = True
                            logger.debug("Used direct method calls for reconnection subscriptions")
                    except Exception as e:
                        logger.debug(f"Direct method calls failed on reconnect: {e}")
                
                if subscription_success:
                    logger.info("✅ Resubscribed to User Hub updates after reconnection")
                else:
                    logger.warning("⚠️  Could not resubscribe to User Hub after reconnection")
        except Exception as e:
            logger.error(f"Error resubscribing to User Hub: {e}")
            import traceback
            logger.debug(traceback.format_exc())
    
    async def _handle_auth_error_and_reconnect(self, account_id: Optional[int]):
        """Handle authentication error by refreshing token and reconnecting."""
        if self._reconnecting:
            return
        self._reconnecting = True
        try:
            logger.info("Refreshing token and reconnecting User Hub...")
            await self.auth_manager.ensure_valid_token()
            if self._hub:
                self._hub.stop()
            await asyncio.sleep(1)
            await self.start(account_id)
        except Exception as e:
            logger.error(f"Error reconnecting User Hub: {e}")
        finally:
            self._reconnecting = False
    
    def register_account_callback(self, callback: Callable):
        """Register a callback for account updates."""
        self._account_callbacks.append(callback)
    
    def register_position_callback(self, callback: Callable):
        """Register a callback for position updates."""
        self._position_callbacks.append(callback)
    
    def register_order_callback(self, callback: Callable):
        """Register a callback for order updates."""
        self._order_callbacks.append(callback)
    
    def register_trade_callback(self, callback: Callable):
        """Register a callback for trade updates."""
        self._trade_callbacks.append(callback)
    
    def is_connected(self) -> bool:
        """Check if User Hub is connected."""
        return self._connected
    
    async def stop(self):
        """Stop the User Hub connection."""
        if self._hub:
            try:
                self._hub.stop()
            except:
                pass
            self._hub = None
        self._connected = False
        logger.info("User Hub connection stopped")
    
    async def subscribe_account(self, account_id: int):
        """Subscribe to updates for a specific account."""
        if not self._connected or not self._hub:
            logger.warning("User Hub not connected, cannot subscribe")
            return False
        
        try:
            # Try different methods to call hub functions
            if hasattr(self._hub, 'send'):
                self._hub.send('SubscribeOrders', [account_id])
                self._hub.send('SubscribePositions', [account_id])
                self._hub.send('SubscribeTrades', [account_id])
            elif hasattr(self._hub, 'invoke'):
                self._hub.invoke('SubscribeOrders', account_id)
                self._hub.invoke('SubscribePositions', account_id)
                self._hub.invoke('SubscribeTrades', account_id)
            else:
                logger.warning(f"Hub does not have send() or invoke() methods. Available methods: {[m for m in dir(self._hub) if not m.startswith('_')]}")
                return False
            
            self._subscribed_account_id = account_id
            logger.info(f"✅ Subscribed to account {account_id} updates")
            return True
        except Exception as e:
            logger.error(f"Error subscribing to account {account_id}: {e}")
            import traceback
            logger.debug(traceback.format_exc())
            return False

