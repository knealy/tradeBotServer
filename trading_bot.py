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
import time
# Import from new organized structure
from core.discord_notifier import DiscordNotifier
from core.account_tracker import AccountTracker
from core.session_trade_tracker import SessionTradeTracker
from infrastructure.performance_metrics import get_metrics_tracker
from infrastructure.database import get_database

# Import new modular architecture components
from core.auth import AuthManager
from core.rate_limiter import RateLimiter
from core.market_data import ContractManager
from core.risk_management import RiskManager
from core.position_management import PositionManager
from core.websocket_manager import WebSocketManager
from core.user_hub_manager import UserHubManager
from core.order_execution import OrderExecutor
from core.json_fast import dumps_str, loads as json_fast_loads
from brokers.topstepx_adapter import TopStepXAdapter
# EventBus is imported where needed (core.event_bus)

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

from core.logging_setup import configure_logging
configure_logging()
logger = logging.getLogger(__name__)

# Bot identifier for order tagging - will be made unique per order
BOT_ORDER_TAG_PREFIX = "TradingBot-v1.0"


class TopStepXTradingBot:
    """
    A real trading bot for TopStepX prop firm futures accounts.
    Uses actual ProjectX API calls via cURL.
    """
    
    def __init__(
        self,
        api_key: str = None,
        username: str = None,
        base_url: str = "https://api.topstepx.com",
        strategy_registration_subset: Optional[List[str]] = None,
    ):
        """
        Initialize the trading bot.
        
        Args:
            api_key: TopStepX API key
            username: TopStepX username
            base_url: TopStepX API base URL
            strategy_registration_subset: When set (e.g. strategy executor ``--strategy=``), only those
                built-in ids are pre-registered; other built-ins still load on demand. When ``None``,
                registration follows ``config/strategies/*.toml`` + ``REGISTER_STRATEGIES`` or all built-ins.
        """
        self.api_key = api_key or os.getenv('PROJECT_X_API_KEY') or os.getenv('TOPSTEPX_API_KEY') or os.getenv('TOPSETPX_API_KEY')
        self.username = username or os.getenv('PROJECT_X_USERNAME') or os.getenv('TOPSTEPX_USERNAME') or os.getenv('TOPSETPX_USERNAME')
        self.base_url = base_url
        self._strategy_registration_subset = strategy_registration_subset
        self._strategy_manager = None
        
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
        self._market_hub_connected = False
        self._subscribed_symbols = set()
        self._market_hub_open_event = asyncio.Event()
        self._quote_ready_events: Dict[str, asyncio.Event] = {}
        self._depth_ready_events: Dict[str, asyncio.Event] = {}
        
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
        # Use lazy loading - accounts will be initialized when selected
        self.account_tracker = AccountTracker(db=self.db, load_all_states=False)
        logger.debug("Account tracker initialized with lazy loading")
        
        # Initialize session-aware trade tracker (real-time FIFO matching)
        self.session_trade_tracker = SessionTradeTracker(db=self.db)
        logger.debug("Session trade tracker initialized")
        
        # Centralized risk manager for all strategies (lazy initialization)
        self._strategy_risk_manager = None
        
        # Initialize bar aggregator for real-time chart updates
        from core.bar_aggregator import BarAggregator
        self.bar_aggregator = BarAggregator(broadcast_callback=None)  # Will be set by webhook server
        logger.debug("Bar aggregator initialized")
        
        # Initialize centralized state cache (reduces API calls by ~95%)
        from core.state_cache import StateCache
        self.state_cache = StateCache(trading_bot=self)
        logger.debug("State cache initialized")
        
        # Initialize Event Bus for event-driven architecture
        from core.event_bus import EventBus
        self.event_bus = EventBus()
        logger.info("📡 Event bus initialized (will start with main loop)")
        
        # NOTE: Strategies are no longer auto-loaded during init for performance
        # They will be instantiated on-demand when explicitly started
        
        # Remove backward compatibility code - overnight_strategy will be created on-demand
        self.overnight_strategy = None  # Lazy-loaded when needed
        
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
        
        # EventBus is initialized in __init__ (line ~311) - no need to reinitialize
        # (Already initialized with core.event_bus.EventBus)
        
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
        # Store reference to trading_bot in adapter for Discord notifications
        self.broker_adapter._trading_bot = self
        logger.debug("✅ TopStepXAdapter initialized")
        
        # Initialize PositionManager (handles position modifications)
        self.position_manager = PositionManager(broker_adapter=self.broker_adapter)
        logger.debug("✅ PositionManager initialized")
        
        # Initialize WebSocketManager (handles SignalR real-time data)
        self.websocket_manager = WebSocketManager(
            auth_manager=self.auth_manager,
            contract_manager=self.contract_manager,
            event_bus=self.event_bus,
        )
        # Register quote callback to update local cache
        self.websocket_manager.register_quote_callback(self._on_websocket_quote)
        self.websocket_manager.register_depth_callback(self._on_websocket_depth)
        logger.debug("✅ WebSocketManager initialized")
        
        # Initialize UserHubManager (handles SignalR User Hub for account/position/order updates)
        self.user_hub_manager = UserHubManager(
            auth_manager=self.auth_manager
        )
        # Register callbacks for User Hub updates
        self.user_hub_manager.register_account_callback(self._on_user_hub_account)
        self.user_hub_manager.register_position_callback(self._on_user_hub_position)
        self.user_hub_manager.register_order_callback(self._on_user_hub_order)
        self.user_hub_manager.register_trade_callback(self._on_user_hub_trade)
        logger.debug("✅ UserHubManager initialized")
        
        # Initialize OrderExecutor (high-level order orchestration)
        self.order_executor = OrderExecutor(
            broker_adapter=self.broker_adapter,
            event_bus=self.event_bus,
            selected_account=self.selected_account
        )
        logger.debug("✅ OrderExecutor initialized")
        
        logger.info("✅ Modular architecture components initialized")
        
        
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

    @property
    def strategy_manager(self):
        """Lazily construct StrategyManager so importing trading_bot does not import strategy modules."""
        if self._strategy_manager is None:
            from strategies.strategy_manager import StrategyManager

            sm = StrategyManager(trading_bot=self)
            sm.register_builtin_strategies(subset=self._strategy_registration_subset)
            self._strategy_manager = sm
        return self._strategy_manager

    # ---------------------------
    # SignalR Market Hub Support
    # ---------------------------
    def _notify_quote_cache_update(self, symbol: str) -> None:
        sym = symbol.upper()
        ev = self._quote_ready_events.get(sym)
        if ev and not ev.is_set():
            ev.set()

    def _notify_depth_cache_update(self, symbol: str) -> None:
        sym = symbol.upper()
        ev = self._depth_ready_events.get(sym)
        if ev and not ev.is_set():
            ev.set()

    async def _wait_for_quote_cache(self, symbol_up: str, timeout: float) -> None:
        with self._quote_cache_lock:
            live = self._quote_cache.get(symbol_up)
            if live and any(live.get(k) is not None for k in ("bid", "ask", "last")):
                return
        waiter = asyncio.Event()
        self._quote_ready_events[symbol_up] = waiter
        try:
            await asyncio.wait_for(waiter.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass
        finally:
            if self._quote_ready_events.get(symbol_up) is waiter:
                del self._quote_ready_events[symbol_up]

    async def _wait_for_depth_cache(self, symbol_up: str, timeout: float) -> None:
        with self._depth_cache_lock:
            dd = self._depth_cache.get(symbol_up)
            if dd and (dd.get("bids") or dd.get("asks")):
                return
        waiter = asyncio.Event()
        self._depth_ready_events[symbol_up] = waiter
        try:
            await asyncio.wait_for(waiter.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass
        finally:
            if self._depth_ready_events.get(symbol_up) is waiter:
                del self._depth_ready_events[symbol_up]

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

            self._notify_quote_cache_update(symbol)

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
                            logger.debug(f"Quote #{count+1} for {symbol}: ${last_price} (vol: {volume}) → bar aggregator")
                            self._quote_log_count[symbol] = count + 1
                        elif count == 5:
                            logger.debug(f"Quote flow confirmed for {symbol} (suppressing further logs)")
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

            self._notify_depth_cache_update(symbol)
        except Exception as e:
            logger.debug(f"Failed processing depth message: {e}")
    
    async def _on_user_hub_account(self, data: Dict):
        """Callback for User Hub account updates."""
        try:
            # Calculate PnL from account tracker if available
            unrealized_pnl = 0.0
            realized_pnl = 0.0
            account_id_str = str(data.get('id', ''))
            
            if hasattr(self, 'account_tracker') and self.account_tracker:
                try:
                    # Get account state which contains PnL
                    account_state = self.account_tracker.get_state(account_id=account_id_str)
                    if account_state:
                        # Account state returns realized_pnl and unrealized_pnl (snake_case)
                        realized_pnl = float(account_state.get('realized_pnl', 0))
                        unrealized_pnl = float(account_state.get('unrealized_pnl', 0))
                        logger.info(f"📊 Account state PnL - Realized: ${realized_pnl:.2f}, Unrealized: ${unrealized_pnl:.2f}")
                    
                    # If account state doesn't have PnL, calculate from positions
                    if unrealized_pnl == 0.0:
                        try:
                            # Use async method if available
                            import asyncio
                            try:
                                loop = asyncio.get_event_loop()
                                if loop.is_running():
                                    # Create task for async call
                                    positions = asyncio.create_task(self.get_open_positions(account_id=account_id_str))
                                else:
                                    positions = loop.run_until_complete(self.get_open_positions(account_id=account_id_str))
                            except RuntimeError:
                                # No event loop, try sync call
                                positions = []
                            
                            if positions and isinstance(positions, list):
                                for pos in positions:
                                    upnl = pos.get('unrealizedPnL') or pos.get('unrealized_pnl') or pos.get('unrealizedPnl') or 0
                                    if upnl:
                                        unrealized_pnl += float(upnl)
                        except Exception as pos_err:
                            logger.debug(f"Could not fetch positions for PnL: {pos_err}")
                    
                    # Also trigger account tracker update to ensure PnL is current
                    try:
                        if hasattr(self, 'account_tracker') and self.account_tracker:
                            # Update unrealized PnL from current positions
                            positions = []
                            try:
                                import asyncio
                                try:
                                    loop = asyncio.get_event_loop()
                                    if loop.is_running():
                                        positions_task = asyncio.create_task(self.get_open_positions(account_id=account_id_str))
                                        # Wait a bit for result
                                        try:
                                            # Yield to the event loop instead of blocking.
                                            await asyncio.sleep(0.1)
                                        except Exception:
                                            pass
                                        if positions_task.done():
                                            positions = positions_task.result()
                                    else:
                                        positions = loop.run_until_complete(self.get_open_positions(account_id=account_id_str))
                                except RuntimeError:
                                    pass
                            except:
                                pass
                            
                            if positions:
                                # Get current prices for positions
                                current_prices = {}
                                for pos in positions:
                                    symbol = pos.get('symbol', '')
                                    if symbol:
                                        try:
                                            quote = self.get_market_quote(symbol)
                                            if quote and 'error' not in quote:
                                                current_prices[symbol] = float(quote.get('last') or quote.get('bid') or quote.get('ask') or 0)
                                        except:
                                            pass
                                
                                if current_prices:
                                    self.account_tracker.update_unrealised_pnl(account_id_str, positions, current_prices)
                                    # Re-fetch account state after update
                                    account_state = self.account_tracker.get_state(account_id=account_id_str)
                                    if account_state:
                                        realized_pnl = float(account_state.get('realized_pnl', 0))
                                        unrealized_pnl = float(account_state.get('unrealized_pnl', 0))
                    except Exception as update_err:
                        logger.debug(f"Could not update account tracker PnL: {update_err}")
                except Exception as e:
                    logger.debug(f"Error calculating PnL for account update: {e}")
                    import traceback
                    logger.debug(traceback.format_exc())
            
            # EVENT-DRIVEN: Emit account update event
            if hasattr(self, 'event_bus') and self.event_bus:
                from core.events import Event, EventType
                try:
                    await self.event_bus.publish(Event(
                        type=EventType.ACCOUNT_UPDATED,
                        data={'account': data, 'account_id': account_id_str, 'unrealized_pnl': unrealized_pnl, 'realized_pnl': realized_pnl},
                        source='signalr_user_hub'
                    ))
                except Exception as e:
                    logger.debug(f"Could not emit account event: {e}")
            
            # Broadcast to GUI if available
            try:
                try:
                    from gui.chart_html import broadcast_update
                except ImportError:
                    broadcast_update = None
                
                if not broadcast_update:
                    return  # GUI not available
                    
                account_update = {
                    'account_id': data.get('id'),
                    'account_name': data.get('name'),
                    'balance': data.get('balance', 0),
                    'unrealized_pnl': unrealized_pnl,
                    'realized_pnl': realized_pnl,
                    'canTrade': data.get('canTrade', True),
                    'isVisible': data.get('isVisible', True),
                    'simulated': data.get('simulated', False)
                }
                # Handle async broadcast_update properly
                import asyncio
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        asyncio.create_task(broadcast_update({
                            'type': 'account',
                            'data': account_update
                        }, immediate=False))
                    else:
                        loop.run_until_complete(broadcast_update({
                            'type': 'account',
                            'data': account_update
                        }, immediate=False))
                except RuntimeError:
                    # No event loop, create new one
                    asyncio.run(broadcast_update({
                        'type': 'account',
                        'data': account_update
                    }, immediate=False))
                logger.debug(f"📡 Broadcasted User Hub account update: {account_update.get('account_name')} - Balance: ${account_update.get('balance'):.2f}, Unrealized: ${unrealized_pnl:.2f}, Realized: ${realized_pnl:.2f}")
            except Exception as e:
                logger.debug(f"Could not broadcast account update to GUI: {e}")
        except Exception as e:
            logger.error(f"Error handling User Hub account update: {e}")
    
    def _on_user_hub_position(self, data: Dict):
        """Callback for User Hub position updates."""
        try:
            # OPTIMIZATION: Invalidate positions cache on SignalR event
            account_id_str = str(data.get('accountId', ''))
            if account_id_str and hasattr(self, 'state_cache') and self.state_cache:
                self.state_cache.invalidate_positions(account_id_str)
                logger.debug(f"🔄 Invalidated positions cache for account {account_id_str}")
            
            # EVENT-DRIVEN: Emit position update event
            if hasattr(self, 'event_bus') and self.event_bus:
                from core.events import Event, EventType
                import asyncio
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        asyncio.create_task(self.event_bus.publish(Event(
                            type=EventType.POSITION_UPDATED,
                            data={'position': data, 'account_id': account_id_str},
                            source='signalr_user_hub'
                        )))
                except Exception as e:
                    logger.debug(f"Could not emit position event: {e}")
            
            # Update account tracker with position change to refresh PnL
            if account_id_str and hasattr(self, 'account_tracker') and self.account_tracker:
                try:
                    # Trigger account update to recalculate PnL
                    # This ensures PnL is refreshed after position changes
                    self._on_user_hub_account({'id': account_id_str})
                except Exception as update_err:
                    logger.debug(f"Could not trigger account update after position change: {update_err}")
            
            # Broadcast to GUI if available
            try:
                try:
                    from gui.chart_html import broadcast_update
                except ImportError:
                    broadcast_update = None
                
                if not broadcast_update:
                    return  # GUI not available
                    
                # Convert position data to GUI format
                contract_id = data.get('contractId', '')
                symbol = ''
                if contract_id and hasattr(self, 'contract_manager'):
                    symbol = self.contract_manager.get_symbol_from_contract_id(contract_id) or ''
                
                position_data = {
                    'id': data.get('id'),
                    'accountId': data.get('accountId'),
                    'contractId': contract_id,
                    'symbol': symbol,
                    'side': 'LONG' if data.get('type') == 1 else 'SHORT',
                    'quantity': data.get('size', 0),
                    'entryPrice': data.get('averagePrice', 0),
                    'creationTimestamp': data.get('creationTimestamp')
                }
                # Position updates are critical - broadcast immediately (create task since this is sync method)
                import asyncio
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        asyncio.create_task(broadcast_update({
                            'type': 'position_opened' if data.get('size', 0) > 0 else 'position_closed',
                            'data': {'positions': [position_data]}
                        }, immediate=True))
                    else:
                        loop.run_until_complete(broadcast_update({
                            'type': 'position_opened' if data.get('size', 0) > 0 else 'position_closed',
                            'data': {'positions': [position_data]}
                        }, immediate=True))
                except RuntimeError:
                    # No event loop, create new one
                    asyncio.run(broadcast_update({
                        'type': 'position_opened' if data.get('size', 0) > 0 else 'position_closed',
                        'data': {'positions': [position_data]}
                    }, immediate=True))
                
                # Also trigger account update to refresh PnL
                account_id = str(data.get('accountId', ''))
                if account_id and hasattr(self, 'account_tracker') and self.account_tracker:
                    try:
                        # Calculate updated PnL
                        positions = self.get_open_positions(account_id=account_id)
                        unrealized_pnl = sum(float(p.get('unrealizedPnL') or p.get('unrealized_pnl') or 0) for p in positions)
                        daily_pnl = self.account_tracker.get_daily_pnl(account_id=account_id) or 0.0
                        realized_pnl = float(daily_pnl)
                        
                        # Get account balance
                        balance = 0.0
                        if hasattr(self, 'selected_account') and self.selected_account:
                            if isinstance(self.selected_account, dict):
                                balance = float(self.selected_account.get('balance', 0.0))
                        
                        # Broadcast account update with updated PnL (queue for batching, not critical)
                        try:
                            loop = asyncio.get_event_loop()
                            if loop.is_running():
                                asyncio.create_task(broadcast_update({
                                    'type': 'account',
                                    'data': {
                                        'account_id': account_id,
                                        'account_name': self.selected_account.get('name', '') if hasattr(self, 'selected_account') and self.selected_account else '',
                                        'balance': balance,
                                        'unrealized_pnl': unrealized_pnl,
                                        'realized_pnl': realized_pnl
                                    }
                                }, immediate=False))
                            else:
                                loop.run_until_complete(broadcast_update({
                                    'type': 'account',
                                    'data': {
                                        'account_id': account_id,
                                        'account_name': self.selected_account.get('name', '') if hasattr(self, 'selected_account') and self.selected_account else '',
                                        'balance': balance,
                                        'unrealized_pnl': unrealized_pnl,
                                        'realized_pnl': realized_pnl
                                    }
                                }, immediate=False))
                        except RuntimeError:
                            pass  # No event loop available
                        logger.debug(f"📡 Updated account PnL after position change: Unrealized: ${unrealized_pnl:.2f}, Realized: ${realized_pnl:.2f}")
                    except Exception as e:
                        logger.debug(f"Error updating account PnL after position change: {e}")
            except Exception as e:
                logger.debug(f"Could not broadcast position update to GUI: {e}")
        except Exception as e:
            logger.error(f"Error handling User Hub position update: {e}")
    
    def _on_user_hub_order(self, data: Dict):
        """Callback for User Hub order updates."""
        try:
            # SignalR can deliver payload as [dict] or [dict, ...]
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict):
                        self._on_user_hub_order(item)
                return
            
            # OPTIMIZATION: Invalidate orders cache on SignalR event
            account_id_str = str(data.get('accountId', ''))
            if account_id_str and hasattr(self, 'state_cache') and self.state_cache:
                self.state_cache.invalidate_orders(account_id_str)
                logger.debug(f"🔄 Invalidated orders cache for account {account_id_str}")
            
            # EVENT-DRIVEN: Emit order update event
            if hasattr(self, 'event_bus') and self.event_bus:
                from core.events import Event, EventType
                import asyncio
                try:
                    # Determine event type based on order status
                    status = data.get('status', '')
                    if status == 'Filled':
                        event_type = EventType.ORDER_FILLED
                    elif status == 'Cancelled':
                        event_type = EventType.ORDER_CANCELLED
                    elif status == 'Rejected':
                        event_type = EventType.ORDER_REJECTED
                    else:
                        event_type = EventType.ORDER_UPDATED
                    
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        asyncio.create_task(self.event_bus.publish(Event(
                            type=event_type,
                            data={'order': data, 'account_id': account_id_str},
                            source='signalr_user_hub'
                        )))
                except Exception as e:
                    logger.debug(f"Could not emit order event: {e}")

            # Broadcast to GUI if available
            try:
                try:
                    from gui.chart_html import broadcast_update
                except ImportError:
                    broadcast_update = None
                
                if not broadcast_update:
                    return  # GUI not available
                    
                order_data = {
                    'id': data.get('id'),
                    'accountId': data.get('accountId'),
                    'contractId': data.get('contractId'),
                    'symbolId': data.get('symbolId'),
                    'status': data.get('status'),
                    'type': data.get('type'),
                    'side': 'BUY' if data.get('side') == 0 else 'SELL',
                    'size': data.get('size', 0),
                    'limitPrice': data.get('limitPrice'),
                    'stopPrice': data.get('stopPrice'),
                    'fillVolume': data.get('fillVolume', 0),
                    'filledPrice': data.get('filledPrice'),
                    'customTag': data.get('customTag')
                }
                # Determine if this is a critical event (filled, canceled) that needs immediate broadcast
                status = data.get('status')
                is_critical = status in [2, 3, 4]  # Filled, Canceled, Rejected
                
                event_type = 'order_filled' if status == 2 else 'order_canceled' if status == 3 else 'order_rejected' if status == 4 else 'order_updated'
                
                # Call with immediate flag for critical events (create task since this is sync method)
                import asyncio
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        asyncio.create_task(broadcast_update({
                            'type': event_type,
                            'data': {'orders': [order_data]}
                        }, immediate=is_critical))
                    else:
                        loop.run_until_complete(broadcast_update({
                            'type': event_type,
                            'data': {'orders': [order_data]}
                        }, immediate=is_critical))
                except RuntimeError:
                    # No event loop, create new one
                    asyncio.run(broadcast_update({
                        'type': event_type,
                        'data': {'orders': [order_data]}
                    }, immediate=is_critical))
            except Exception as e:
                logger.debug(f"Could not broadcast order update to GUI: {e}")
        except Exception as e:
            logger.error(f"Error handling User Hub order update: {e}")
    
    def _on_user_hub_trade(self, data: Dict):
        """Callback for User Hub trade updates."""
        try:
            # SignalR can deliver payload as [dict] or [dict, ...]
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict):
                        self._on_user_hub_trade(item)
                return

            account_id = str(data.get('accountId', ''))
            if not account_id:
                return
            
            # Process fill through session trade tracker (FIFO matching)
            if hasattr(self, 'session_trade_tracker') and self.session_trade_tracker:
                try:
                    # Extract fill data from trade update
                    fill_id = str(data.get('fillId') or data.get('id') or f"fill_{int(datetime.now(timezone.utc).timestamp() * 1000)}")
                    order_id = str(data.get('orderId') or data.get('order_id', ''))
                    symbol = str(data.get('symbol') or data.get('contractSymbol', ''))
                    side = int(data.get('side', 0))  # 0=BUY, 1=SELL
                    quantity = int(data.get('quantity') or data.get('qty', 0))
                    price = float(data.get('price') or data.get('fillPrice', 0))
                    commission = float(data.get('commission', 0))
                    fee = float(data.get('fee', 0))
                    
                    # Parse timestamp if available
                    timestamp = None
                    if 'timestamp' in data:
                        try:
                            if isinstance(data['timestamp'], str):
                                timestamp = datetime.fromisoformat(data['timestamp'].replace('Z', '+00:00'))
                            else:
                                timestamp = datetime.fromtimestamp(data['timestamp'], tz=timezone.utc)
                        except:
                            pass
                    
                    if fill_id and order_id and symbol and quantity > 0 and price > 0:
                        # Process fill through session tracker
                        completed_trades = self.session_trade_tracker.process_fill(
                            fill_id=fill_id,
                            order_id=order_id,
                            account_id=account_id,
                            symbol=symbol,
                            side=side,
                            quantity=quantity,
                            price=price,
                            commission=commission,
                            fee=fee,
                            timestamp=timestamp
                        )
                        
                        # If trades were completed, update AccountTracker with realized PnL
                        if completed_trades:
                            total_realized_pnl = sum(t.net_pnl for t in completed_trades)
                            total_commission = sum(t.commission for t in completed_trades)
                            total_fee = sum(t.fee for t in completed_trades)
                            
                            fill_data = {
                                'pnl': total_realized_pnl,
                                'commission': total_commission,
                                'fee': total_fee
                            }
                            
                            if hasattr(self, 'account_tracker') and self.account_tracker:
                                self.account_tracker.update_from_fill(account_id, fill_data)
                            
                            logger.info(f"✅ Processed {len(completed_trades)} completed trade(s), Realized PnL: ${total_realized_pnl:.2f}")
                            
                            # Broadcast trade updates to GUI
                            try:
                                try:
                                    from gui.chart_html import broadcast_update
                                except ImportError:
                                    broadcast_update = None
                                
                                if broadcast_update:
                                    session_pnl = self.session_trade_tracker.get_session_pnl(account_id)
                                    broadcast_update({
                                        'type': 'session_trades',
                                        'data': {
                                            'completed_trades': [t.to_dict() for t in completed_trades],
                                            'session_pnl': session_pnl,
                                            'realized_pnl': total_realized_pnl
                                        }
                                    })
                            except Exception as e:
                                logger.debug(f"Could not broadcast trade update to GUI: {e}")
                except Exception as e:
                    logger.error(f"Error processing fill in session tracker: {e}", exc_info=True)
            
            # Legacy: Update AccountTracker with trade PnL (fallback if session tracker not available)
            if data.get('profitAndLoss') and hasattr(self, 'account_tracker') and self.account_tracker:
                try:
                    fill_data = {
                        'pnl': float(data.get('profitAndLoss', 0)),
                        'commission': float(data.get('commission', 0)),
                        'fee': float(data.get('fee', 0))
                    }
                    self.account_tracker.update_from_fill(account_id, fill_data)
                    logger.debug(f"✅ Updated AccountTracker with trade PnL: ${fill_data['pnl']:.2f}")
                except Exception as e:
                    logger.debug(f"Could not update AccountTracker with trade: {e}")

            # Update account PnL from trade
            if data.get('profitAndLoss'):
                try:
                    try:
                        from gui.chart_html import broadcast_update
                    except ImportError:
                        broadcast_update = None
                    
                    if not broadcast_update:
                        return  # GUI not available
                        
                    broadcast_update({
                        'type': 'account',
                        'data': {
                            'realized_pnl': data.get('profitAndLoss', 0)
                        }
                    })
                except Exception as e:
                    logger.debug(f"Could not broadcast trade update to GUI: {e}")
        except Exception as e:
            logger.error(f"Error handling User Hub trade update: {e}", exc_info=True)
    
    async def _ensure_market_socket_started(self) -> None:
        """
        Start market data socket only when actually needed (e.g., for quotes/depth).
        
        Now uses WebSocketManager for SignalR connections, maintaining backward compatibility.
        """
        # Use WebSocketManager if available
        if hasattr(self, 'websocket_manager'):
            if self.websocket_manager.is_connected():
                self._market_hub_connected = True
                self._market_hub_open_event.set()
                return
            self._market_hub_open_event.clear()
            # Start WebSocketManager connection
            success = await self.websocket_manager.start()
            if success:
                self._market_hub_connected = True
                self._market_hub_open_event.set()
                # Sync subscribed symbols
                self._subscribed_symbols = self.websocket_manager.get_subscribed_symbols()
                return

        logger.error("WebSocketManager is required for market data but was not initialized")

    async def _ensure_quote_subscription(self, symbol: str) -> None:
        """Subscribe to real-time quotes via WebSocketManager (Gateway Market Hub)."""
        wm = getattr(self, "websocket_manager", None)
        if wm is None:
            logger.error("WebSocketManager is required for quote subscription but is missing")
            return
        success = await wm.subscribe_quote(symbol)
        if success:
            self._subscribed_symbols.add(symbol.upper())

    async def _ensure_depth_subscription(self, symbol: str) -> None:
        """Subscribe to market depth via WebSocketManager."""
        wm = getattr(self, "websocket_manager", None)
        if wm is None:
            logger.error("WebSocketManager is required for depth subscription but is missing")
            return
        await wm.subscribe_depth(symbol)
    
    async def _make_http_request(
        self,
        method: str,
        endpoint: str,
        data: Dict = None,
        headers: Dict = None,
        skip_rate_limit: bool = False,
        suppress_errors: bool = False,
    ) -> Dict:
        """
        REST call via AuthManager's shared aiohttp session (non-blocking for the event loop).

        Preserves trading_bot rate limiting and performance metrics behavior.
        """
        start_time = time.time()
        status_code = None
        success = False
        error_message = None
        api_timeout = int(os.getenv("API_TIMEOUT", "30"))

        if endpoint != "/api/Auth/loginKey" and self._is_token_expired():
            logger.warning(
                "⚠️  Token expired or missing - request may fail. Caller should refresh token."
            )

        if not skip_rate_limit:
            await self._rate_limiter.acquire_async()

        self.auth_manager.session_token = self.session_token
        self.auth_manager.token_expiry = self.token_expiry

        try:
            if endpoint == "/api/Order/place" and data:
                logger.debug("Sending order to /api/Order/place")
                logger.debug(f"   JSON payload: {dumps_str(data)}")
                logger.debug(
                    f"   Token: {self.session_token[:20] + '...' if self.session_token else 'MISSING'}"
                )

            logger.debug("HTTP %s request to %s", method, endpoint)

            response = await self.auth_manager._make_request(
                method,
                endpoint,
                data,
                headers,
                timeout=api_timeout,
                quiet_client_errors=suppress_errors,
            )
            if isinstance(response, dict):
                status_code = response.get("status_code")
                success = "error" not in response
                if "error" in response:
                    error_message = str(response.get("error"))
            return response
        except Exception as e:
            error_message = str(e)
            logger.error("HTTP request failed: %s", e)
            return {"error": error_message}
        finally:
            duration_ms = (time.time() - start_time) * 1000
            try:
                metrics_tracker = get_metrics_tracker(db=getattr(self, "db", None))
                metrics_tracker.record_api_call(
                    endpoint=endpoint,
                    method=method,
                    duration_ms=duration_ms,
                    status_code=status_code,
                    success=success,
                    error_message=error_message,
                )
            except Exception as metrics_err:
                logger.debug("Failed to record metrics: %s", metrics_err)
    
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
            response = await self._make_http_request("POST", "/api/Auth/loginKey", data=login_data, headers=headers)
            
            if "error" in response:
                logger.error(f"Authentication failed: {response['error']}")
                return False
            
            # Check if login was successful
            if response.get("success") and response.get("token"):
                self.session_token = response["token"]
                self.auth_manager.session_token = self.session_token

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
                self.auth_manager.token_expiry = self.token_expiry
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
            self.auth_manager.session_token = None
            self.auth_manager.token_expiry = None

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
    
    async def switch_account(self, account_identifier: str) -> bool:
        """
        Switch to a different account by ID or index.
        Used by strategy executor and programmatic account switching.

        Args:
            account_identifier: Account ID or 1-based index as string

        Returns:
            bool: True if account switched successfully, False otherwise
        """
        try:
            # Fetch accounts
            accounts = await self.list_accounts()
            if not accounts:
                logger.error("No accounts available")
                return False
            
            selected_account = None
            
            # Try as index first (1-based)
            if account_identifier.isdigit():
                idx = int(account_identifier) - 1
                if 0 <= idx < len(accounts):
                    selected_account = accounts[idx]
                    logger.info(f"Selected account by index {account_identifier}: {selected_account['name']}")
            
            # Try as account ID if not found by index
            if not selected_account:
                for acc in accounts:
                    if str(acc.get('id')) == account_identifier or acc.get('name') == account_identifier:
                        selected_account = acc
                        logger.info(f"Selected account by ID/name: {selected_account['name']}")
                        break
            
            if not selected_account:
                logger.error(f"Account not found: {account_identifier}")
                return False
            
            # Set as selected account
            self.selected_account = selected_account
            
            # Update OrderExecutor's selected account
            if hasattr(self, 'order_executor'):
                self.order_executor.set_selected_account(selected_account)
            
            # Initialize account tracker
            account_balance = selected_account.get('balance', 0)
            account_type = selected_account.get('type', 'unknown')
            self.account_tracker.initialize(
                account_id=selected_account['id'],
                starting_balance=account_balance,
                account_type=account_type
            )
            logger.info(f"Account tracker initialized for {selected_account['name']} (${account_balance:,.2f})")
            
            # Start/update User Hub subscription for new account
            if hasattr(self, 'user_hub_manager'):
                try:
                    account_id = selected_account.get('id')
                    if self.user_hub_manager.is_connected():
                        # Already connected, just subscribe to new account
                        await self.user_hub_manager.subscribe_account(account_id)
                    else:
                        # Not connected, start connection
                        success = await self.user_hub_manager.start(account_id=account_id)
                        if success:
                            logger.info("✅ User Hub connected for real-time updates")
                            # Register event-driven cache invalidation callbacks
                            self._setup_event_driven_cache_invalidation(str(account_id))
                        else:
                            logger.warning("⚠️  User Hub connection failed, will use polling")
                except Exception as e:
                    logger.warning(f"⚠️  Failed to start/update User Hub: {e}")
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to switch account: {e}")
            return False
    
    def _setup_event_driven_cache_invalidation(self, account_id: str):
        """
        Setup event-driven cache invalidation from SignalR events.
        This eliminates the need for constant API polling by invalidating
        caches only when actual changes occur.
        
        Args:
            account_id: Account ID to monitor
        """
        if not hasattr(self, 'user_hub_manager') or not hasattr(self, 'state_cache'):
            return
        
        # Create cache invalidation callbacks
        def on_order_update(data):
            """Invalidate orders cache when order event received."""
            try:
                self.state_cache.invalidate_orders(account_id)
                logger.debug(f"🔄 Orders cache invalidated (SignalR event)")
            except Exception as e:
                logger.error(f"Error invalidating orders cache: {e}")
        
        def on_position_update(data):
            """Invalidate positions cache when position event received."""
            try:
                self.state_cache.invalidate_positions(account_id)
                logger.debug(f"🔄 Positions cache invalidated (SignalR event)")
            except Exception as e:
                logger.error(f"Error invalidating positions cache: {e}")
        
        # Register callbacks with User Hub Manager
        self.user_hub_manager.register_order_callback(on_order_update)
        self.user_hub_manager.register_position_callback(on_position_update)
        
        logger.info(f"✅ Event-driven cache invalidation enabled for account {account_id}")

    def select_account(self, accounts: List[Dict]) -> Optional[Dict]:
        """
        Allow user to select an account for trading (interactive mode).

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
                    response = await self._make_http_request("GET", endpoint, headers=headers, suppress_errors=True)
                    
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
                        asyncio.create_task(
                            self.discord_notifier.send_order_fill_notification(notification_data, account_name)
                        )
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

                            asyncio.create_task(
                                self.discord_notifier.send_position_close_notification(notification_data, account_name)
                            )
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
                        
                        asyncio.create_task(
                            self.discord_notifier.send_order_fill_notification(notification_data, account_name)
                        )
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
                                limit_price: float = None, strategy_name: str = None, reduce_only: bool = False) -> Dict:
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
            reduce_only: Accepted for CLI compatibility (some call sites pass this flag).
            
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
            
            # Order placement debug (kept at DEBUG to avoid log spam).
            logger.debug("===== ORDER PLACEMENT DEBUG =====")
            logger.debug(f"Symbol: {symbol}, Side: {side}, Quantity: {quantity}")
            logger.debug(f"Account ID: {target_account}")
            logger.debug(f"Contract ID: {contract_id}")
            logger.debug(f"Order Data: {dumps_str(order_data)}")
            logger.debug("=================================")
            
            # Make real API call to place order using session token
            headers = {
                "accept": "text/plain",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.session_token}"
            }
            
            response = await self._make_http_request("POST", "/api/Order/place", data=order_data, headers=headers)
            
            # Full API response is useful but extremely noisy; keep at DEBUG.
            logger.debug("===== API RESPONSE =====")
            logger.debug(f"Response Type: {type(response)}")
            logger.debug(f"Response Keys: {list(response.keys()) if isinstance(response, dict) else 'Not a dict'}")
            logger.debug(f"Full Response: {dumps_str(response) if isinstance(response, dict) else str(response)}")
            logger.debug("========================")
            
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
                logger.debug(f"Full response: {dumps_str(response)}")
                return {"error": f"Order failed: {error_message} (Code: {error_code})"}

            # Check for order ID - real orders always have IDs
            order_id = response.get("orderId") or response.get("id") or response.get("data", {}).get("orderId")
            if not order_id:
                logger.error("API returned success but NO order ID (see DEBUG for full response).")
                logger.debug(f"Full response: {dumps_str(response)}")
                return {"error": "Order rejected: No order ID returned", "api_response": response}

            logger.info(f"Order placed successfully with ID: {order_id}")
            logger.debug(f"Full response: {dumps_str(response)}")

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
                asyncio.create_task(self.discord_notifier.send_order_notification(notification_data, account_name))
            except Exception as notif_err:
                logger.warning(f"Failed to send Discord notification: {notif_err}")

            # Cache any discovered order/position IDs for fast future cancellations/closures
            try:
                self._cache_ids_from_response(response, target_account, symbol)
            except Exception as cache_err:
                logger.warning(f"Failed to cache IDs from order response: {cache_err}")

            # CRITICAL: Invalidate cache immediately so fast refresh loop gets fresh data
            if hasattr(self, 'state_cache') and self.state_cache and target_account:
                try:
                    self.state_cache.invalidate_orders(str(target_account))
                    # Order fill may create/close position, so invalidate positions too
                    self.state_cache.invalidate_positions(str(target_account))
                    logger.debug(f"🔄 Invalidated orders/positions cache after order placement")
                except Exception as inval_err:
                    logger.debug(f"Error invalidating cache after order: {inval_err}")

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
            response = await self._make_http_request(
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
                # ALWAYS use Position object fields, not raw_data
                # raw_data may be missing symbol field (extracted from contract ID)
                try:
                    contract_id = None
                    if pos.symbol:
                        try:
                            contract_id = self.contract_manager.get_contract_id(pos.symbol)
                        except (ValueError, AttributeError):
                            pass
                    
                    # Build dict from Position object fields (symbol is properly extracted)
                    pos_dict = {
                        'id': pos.position_id,
                        'position_id': pos.position_id,
                        'symbol': pos.symbol,  # This is extracted from contract ID if needed
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
                    }
                    
                    # Merge with raw_data for any additional fields
                    if hasattr(pos, 'raw_data') and pos.raw_data:
                        pos_dict.update(pos.raw_data)
                        # Ensure symbol from Position object takes precedence
                        pos_dict['symbol'] = pos.symbol
                    
                    result.append(pos_dict)
                except Exception as e:
                    logger.warning(f"Failed to convert position to dict: {e}")
                    logger.exception(e)
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
                    # CRITICAL: Invalidate cache immediately so fast refresh loop gets fresh data
                    if hasattr(self, 'state_cache') and self.state_cache and target_account:
                        try:
                            self.state_cache.invalidate_positions(str(target_account))
                            self.state_cache.invalidate_orders(str(target_account))  # Closing may cancel related orders
                            logger.debug(f"🔄 Invalidated positions/orders cache after position close")
                        except Exception as inval_err:
                            logger.debug(f"Error invalidating cache after position close: {inval_err}")
                    
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
                            asyncio.create_task(
                                self.discord_notifier.send_position_close_notification(notification_data, account_name)
                            )
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
            
            logger.debug(f"Found {len(orders)} open orders for account {target_account}")  # Reduced to DEBUG
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
                    # CRITICAL: Invalidate cache immediately so fast refresh loop gets fresh data
                    if hasattr(self, 'state_cache') and self.state_cache and target_account:
                        try:
                            self.state_cache.invalidate_orders(str(target_account))
                            logger.debug(f"🔄 Invalidated orders cache after order cancel")
                        except Exception as inval_err:
                            logger.debug(f"Error invalidating cache after order cancel: {inval_err}")
                    
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
    
    def _calculate_commission(self, quantity: int) -> float:
        """
        Calculate total commission for a trade (round trip).
        
        Each contract has $0.74 commission to open and $0.74 to close,
        so total commission per round trip is $1.48 per contract.
        
        Args:
            quantity: Number of contracts
            
        Returns:
            Total commission in dollars
        """
        COMMISSION_PER_CONTRACT_OPEN = 0.74
        COMMISSION_PER_CONTRACT_CLOSE = 0.74
        return (COMMISSION_PER_CONTRACT_OPEN + COMMISSION_PER_CONTRACT_CLOSE) * quantity

    def _consolidate_orders_into_trades(self, orders: List[Dict]) -> List[Dict]:
        from core.trade_consolidation import consolidate_orders_into_trades

        return consolidate_orders_into_trades(
            orders,
            self._get_point_value,
            self._calculate_commission,
            log=logger,
        )

    def _calculate_trade_statistics(self, trades: List[Dict]) -> Dict:
        from core.trade_consolidation import calculate_trade_statistics

        return calculate_trade_statistics(trades)
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
    
    async def get_today_stats(self, account_id: str = None) -> Dict:
        """
        Get today's trading statistics using manual calculation from Trade/search.
        
        Statistics endpoints on userapi.topstepx.com require internal developer permissions
        and are not available via API key. This method calculates statistics from trades.
        
        Args:
            account_id: Account ID (uses selected account if not provided)
            
        Returns:
            Dict: Today's statistics including totalPnL, totalTrades, winningTrades, etc.
        """
        try:
            from datetime import datetime, timezone, timedelta
            
            target_account = account_id or (self.selected_account['id'] if self.selected_account else None)
            if not target_account:
                logger.error("No account selected for today stats")
                return {}
            
            # Get today's date range (UTC)
            now = datetime.now(timezone.utc)
            start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
            end_of_day = now.replace(hour=23, minute=59, second=59, microsecond=999999)
            
            # Get trades for today using Trade/search
            trades = await self.get_trades_from_api(
                account_id=target_account,
                start_date=start_of_day.isoformat(),
                end_date=end_of_day.isoformat()
            )
            
            if not trades:
                logger.debug(f"No trades found for today for account {target_account}")
                return {
                    "totalPnL": 0.0,
                    "totalTrades": 0,
                    "winningTrades": 0,
                    "losingTrades": 0,
                    "winRate": 0.0,
                    "totalFees": 0.0
                }
            
            # Calculate statistics from trades
            stats = self._calculate_trade_statistics(trades)
            
            # Calculate total fees
            total_fees = sum(float(trade.get('fees', 0) or 0) for trade in trades)
            
            # Return in format similar to what Statistics API would return
            return {
                "totalPnL": stats.get("total_pnl", 0.0),
                "totalTrades": stats.get("total_trades", 0),
                "winningTrades": stats.get("winning_trades", 0),
                "losingTrades": stats.get("losing_trades", 0),
                "winRate": stats.get("win_rate", 0.0),
                "totalFees": round(total_fees, 2),
                "averageWin": stats.get("average_win", 0.0),
                "averageLoss": stats.get("average_loss", 0.0),
                "largestWin": stats.get("largest_win", 0.0),
                "largestLoss": stats.get("largest_loss", 0.0)
            }
        except Exception as e:
            logger.error(f"Error calculating today stats: {e}")
            import traceback
            logger.debug(traceback.format_exc())
            return {}
    
    async def get_trade_statistics(self, account_id: str = None, 
                                   start_date: str = None, end_date: str = None) -> Dict:
        """
        Get trade statistics for a date range using manual calculation from Trade/search.
        
        Statistics endpoints on userapi.topstepx.com require internal developer permissions
        and are not available via API key. This method calculates statistics from trades.
        
        Args:
            account_id: Account ID (uses selected account if not provided)
            start_date: Start date in ISO format (defaults to today)
            end_date: End date in ISO format (defaults to today)
            
        Returns:
            Dict: Statistics including totalPnL, totalTrades, winningTrades, losingTrades, winRate, etc.
        """
        try:
            from datetime import datetime, timezone
            target_account = account_id or (self.selected_account['id'] if self.selected_account else None)
            if not target_account:
                logger.error("No account selected for trade statistics")
                return {}
            
            # Default to today if not provided
            if not start_date:
                now = datetime.now(timezone.utc)
                start_date = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
            if not end_date:
                now = datetime.now(timezone.utc)
                end_date = now.replace(hour=23, minute=59, second=59, microsecond=999999).isoformat()
            
            # Get trades for the date range using Trade/search
            trades = await self.get_trades_from_api(
                account_id=target_account,
                start_date=start_date,
                end_date=end_date
            )
            
            if not trades:
                logger.debug(f"No trades found for date range {start_date} to {end_date} for account {target_account}")
                return {
                    'totalPnL': 0.0,
                    'totalTrades': 0,
                    'winningTrades': 0,
                    'losingTrades': 0,
                    'winRate': 0.0,
                    'totalFees': 0.0
                }
            
            # Calculate statistics from trades
            stats = self._calculate_trade_statistics(trades)
            
            # Calculate total fees
            total_fees = sum(float(trade.get('fees', 0) or 0) for trade in trades)
            
            # Return in format similar to what Statistics API would return
            return {
                'totalPnL': stats.get("total_pnl", 0.0),
                'totalTrades': stats.get("total_trades", 0),
                'winningTrades': stats.get("winning_trades", 0),
                'losingTrades': stats.get("losing_trades", 0),
                'winRate': stats.get("win_rate", 0.0),
                'totalFees': round(total_fees, 2),
                'averageWin': stats.get("average_win", 0.0),
                'averageLoss': stats.get("average_loss", 0.0),
                'largestWin': stats.get("largest_win", 0.0),
                'largestLoss': stats.get("largest_loss", 0.0)
            }
        except Exception as e:
            logger.error(f"Error calculating trade statistics: {e}")
            import traceback
            logger.debug(traceback.format_exc())
            return {}
    
    async def get_trades_from_api(self, account_id: str = None,
                                  start_date: str = None, end_date: str = None) -> List[Dict]:
        """
        Get individual trades from TopStepX Trade/search API endpoint.
        
        This provides authoritative trade data with pre-calculated profitAndLoss
        directly from TopStepX, which is more accurate than manual consolidation.
        
        Args:
            account_id: Account ID (uses selected account if not provided)
            start_date: Start date in ISO format (defaults to 7 days ago)
            end_date: End date in ISO format (defaults to now)
            
        Returns:
            List[Dict]: List of trades with profitAndLoss, fees, etc.
        """
        try:
            from datetime import datetime, timezone, timedelta
            target_account = account_id or (self.selected_account['id'] if self.selected_account else None)
            if not target_account:
                logger.error("No account selected for trades")
                return []
            
            # Use broker adapter's get_trades method which uses Trade/search endpoint
            if hasattr(self, 'broker_adapter') and self.broker_adapter:
                trades = await self.broker_adapter.get_trades(
                    account_id=target_account,
                    start_timestamp=start_date,
                    end_timestamp=end_date
                )
                logger.info(f"✅ Retrieved {len(trades)} trades from Trade/search API")
                return trades
            else:
                logger.warning("Broker adapter not available, cannot fetch trades")
                return []
        except Exception as e:
            logger.error(f"Error fetching trades from Trade/search API: {e}")
            import traceback
            logger.debug(traceback.format_exc())
            return []
    
    async def get_profit_factor(self, account_id: str = None,
                               start_date: str = None, end_date: str = None) -> Dict:
        """
        Get profit factor using manual calculation from Trade/search.
        
        Statistics endpoints on userapi.topstepx.com require internal developer permissions
        and are not available via API key. This method calculates profit factor from trades.
        
        Args:
            account_id: Account ID (uses selected account if not provided)
            start_date: Start date in ISO format (defaults to today)
            end_date: End date in ISO format (defaults to today)
            
        Returns:
            Dict: {totalProfit, totalLoss} for calculating profit factor
        """
        try:
            from datetime import datetime, timezone
            target_account = account_id or (self.selected_account['id'] if self.selected_account else None)
            if not target_account:
                logger.error("No account selected for profit factor")
                return {'totalProfit': 0, 'totalLoss': 0}
            
            # Default to today if not provided
            if not start_date:
                now = datetime.now(timezone.utc)
                start_date = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
            if not end_date:
                now = datetime.now(timezone.utc)
                end_date = now.replace(hour=23, minute=59, second=59, microsecond=999999).isoformat()
            
            # Get trades for the date range using Trade/search
            trades = await self.get_trades_from_api(
                account_id=target_account,
                start_date=start_date,
                end_date=end_date
            )
            
            if not trades:
                logger.debug(f"No trades found for date range {start_date} to {end_date} for account {target_account}")
                return {'totalProfit': 0, 'totalLoss': 0}
            
            # Calculate total profit and loss from trades
            total_profit = 0.0
            total_loss = 0.0
            
            for trade in trades:
                # Prefer net_pnl (after fees) if available
                net_pnl = trade.get('net_pnl')
                if net_pnl is not None:
                    pnl = float(net_pnl)
                else:
                    # Calculate net_pnl from gross pnl and fees if not provided
                    gross_pnl = float(trade.get('pnl', 0) or trade.get('profitAndLoss', 0) or 0)
                    fees = float(trade.get('fees', 0) or 0)
                    pnl = gross_pnl - fees
                
                if pnl > 0:
                    total_profit += pnl
                elif pnl < 0:
                    total_loss += abs(pnl)  # Store as positive value for loss
            
            return {
                'totalProfit': round(total_profit, 2),
                'totalLoss': round(total_loss, 2)
            }
        except Exception as e:
            logger.error(f"Error calculating profit factor: {e}")
            import traceback
            logger.debug(traceback.format_exc())
            return {'totalProfit': 0, 'totalLoss': 0}
    
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
        
        **CENTRALIZED RISK MANAGEMENT**: When called from strategies (strategy_name provided),
        orders are automatically checked for position limits, cooldowns, and time restrictions.
        
        Args:
            symbol: Trading symbol (e.g., "ES", "NQ", "MNQ", "YM")
            side: "BUY" or "SELL"
            quantity: Number of contracts
            stop_loss_price: Stop loss price (optional if stop_loss_ticks provided)
            take_profit_price: Take profit price (optional if take_profit_price provided)
            stop_loss_ticks: Stop loss in ticks (optional if stop_loss_price provided)
            take_profit_ticks: Take profit in ticks (optional if take_profit_price provided)
            account_id: Account ID (uses selected account if not provided)
            strategy_name: Optional strategy name for tracking (triggers risk checks if provided)
            
        Returns:
            Dict: Bracket order response or error
        """
        try:
            target_account = account_id or (self.selected_account['id'] if self.selected_account else None)
            
            if not target_account:
                return {"error": "No account selected"}
            
            if side.upper() not in ["BUY", "SELL"]:
                return {"error": "Side must be 'BUY' or 'SELL'"}
            
            # CENTRALIZED RISK MANAGEMENT: Check if order is allowed (when called from strategies)
            if strategy_name:
                if self._strategy_risk_manager is None:
                    from core.risk_management import StrategyRiskManager
                    self._strategy_risk_manager = StrategyRiskManager(self)
                
                allowed, reason = await self._strategy_risk_manager.check_order_allowed(
                    symbol=symbol,
                    side=side,
                    quantity=quantity
                )
                
                if not allowed:
                    error_msg = f"Risk management blocked order: {reason}"
                    logger.warning(f"⚠️  Strategy {strategy_name}: {error_msg}")
                    return {
                        "success": False,
                        "error": error_msg,
                        "orderId": None
                    }
            
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
                # Record successful order placement for cooldown tracking (when from strategy)
                if strategy_name and self._strategy_risk_manager:
                    await self._strategy_risk_manager.record_order_placement(symbol, side)
                
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
            await asyncio.sleep(1)
            
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
            response = await self._make_http_request("POST", "/api/Order/search", data=search_data, headers=headers)
            
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
                        # Only consider it a take profit if it's actually linked via customTag
                        # OR if it's in the correct direction relative to position entry price
                        custom_tag = order.get('customTag', '') or ''
                        if "AutoBracket" in custom_tag and ("-TP" in custom_tag or "TP" in custom_tag):
                            linked_orders.append(order)
                            logger.info(f"Found take profit order (via bracket tag): {order.get('id')} type: {order_type}")
                        else:
                            # Only link if we can verify it's actually a TP by checking position entry price
                            # This requires position data, so we'll be conservative and only link if explicitly tagged
                            # For now, skip linking standalone limit orders unless they're bracket-linked
                            pass
            
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
            
            response = await self._make_http_request("POST", "/api/Order/place", data=stop_data, headers=headers)
            
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
            
            # Use TopStepXAdapter for bracket order placement (OCO brackets).
            # NOTE: Some accounts are configured for "Position Brackets" (not Auto OCO).
            # In that mode TopStepX can return HTTP 500 for OCO bracket placement.
            # When that happens, fall back to the hybrid stop-entry + post-fill bracket logic.
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
                method = "unknown"
                if isinstance(result.raw_response, dict) and result.raw_response.get("_execution_path"):
                    method = str(result.raw_response.get("_execution_path"))
                elif isinstance(result.message, str):
                    # Best-effort: derive from message if present
                    msg_lower = result.message.lower()
                    if "rust" in msg_lower:
                        method = "rust"
                    elif "python" in msg_lower:
                        method = "python"

                return {
                    "success": True,
                    "orderId": result.order_id,
                    "message": result.message,
                    "method": method,
                    **({"raw_response": result.raw_response} if result.raw_response else {})
                }
            else:
                error_msg = result.error or "Unknown error"

                # Persist last bracket-related error for GUI feedback
                try:
                    self._last_bracket_error = error_msg
                except Exception:
                    pass

                logger.error(f"Stop bracket order failed: {error_msg}")

                # Check if error is specifically about Auto OCO Brackets not being enabled
                # In this case, we should NOT attempt fallback - just return the error gracefully
                err_lower = str(error_msg).lower()
                is_auto_oco_error = (
                    "brackets cannot be used with position brackets" in err_lower
                    or ("you must enable auto oco brackets" in err_lower and "code: 2" in err_lower)
                    or ("error code 2" in err_lower and "auto oco brackets" in err_lower)
                )
                
                if is_auto_oco_error:
                    logger.error("❌ Auto OCO Brackets is not enabled in account settings. Cannot place bracket orders.")
                    logger.error("   Please enable 'Auto OCO Brackets' in your TopStepX account settings to use bracket orders.")
                    logger.error("   Skipping order placement - no fallback will be attempted.")
                    return {
                        "success": False,
                        "error": "Auto OCO Brackets is not enabled in account settings. Please enable 'Auto OCO Brackets' in your TopStepX account settings to use bracket orders.",
                        "error_code": 2,
                        "requires_account_setting": "Auto OCO Brackets"
                    }

                # Hybrid fallback ONLY for HTTP 500 errors (server issues), NOT for account setting errors
                if (
                    "http 500" in err_lower
                    or "internal server error" in err_lower
                    or ("500" in err_lower and "error" in err_lower)
                ):
                    logger.warning("⚠️ OCO bracket failed with server error; attempting hybrid stop-entry + post-fill bracket fallback")
                    hybrid = await self._stop_bracket_hybrid(
                        symbol=symbol,
                        side=side,
                        quantity=quantity,
                        entry_price=entry_price,
                        stop_loss_price=stop_loss_price,
                        take_profit_price=take_profit_price,
                        account_id=target_account,
                        enable_breakeven=enable_breakeven,
                        strategy_name=strategy_name,
                    )
                    # If hybrid worked, return it
                    if isinstance(hybrid, dict) and "error" not in hybrid:
                        if "method" not in hybrid:
                            hybrid["method"] = "hybrid_auto_bracket"
                        return {"success": True, **hybrid}
                    # Otherwise surface hybrid error (more actionable)
                    if isinstance(hybrid, dict) and hybrid.get("error"):
                        return {"success": False, "error": f"{error_msg} | Hybrid fallback failed: {hybrid.get('error')}"}

                return {"success": False, "error": error_msg}
            
        except Exception as e:
            logger.error(f"Failed to place OCO bracket with stop entry: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            return {"success": False, "error": str(e)}
    
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

            # Try live cache first - this is fed by SignalR quotes
            try:
                await self._ensure_market_socket_started()
                await self._ensure_quote_subscription(symbol_up)
                await self._wait_for_quote_cache(symbol_up, timeout=1.0)
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
                    resp = await self._make_http_request("GET", path, headers=headers, suppress_errors=True)
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

            # Fallback to recent bars for last price (only if SignalR is truly unavailable)
            from datetime import datetime, timezone, timedelta
            logger.warning(f"⚠️  SignalR and REST quote unavailable, falling back to bars API for {symbol_up}")
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
                "unit": 2,  # AggregateBarUnit: 2 = Minute
                "unitNumber": 1,
                "limit": 5
            }
            response = await self._make_http_request("POST", "/api/History/retrieveBars", data=bars_request, headers=headers)
            if "error" in response or not response.get("success"):
                # Retry with non-live over a wider window
                from datetime import timedelta as _td
                start_time2 = now - _td(seconds=30)
                bars_request2 = dict(bars_request)
                bars_request2.update({"live": False, "startTime": start_time2.isoformat()})
                response = await self._make_http_request("POST", "/api/History/retrieveBars", data=bars_request2, headers=headers)
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
                bars_request2.update({"live": False, "startTime": start_time2.isoformat()})
                response = await self._make_http_request("POST", "/api/History/retrieveBars", data=bars_request2, headers=headers)
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

                await self._wait_for_depth_cache(symbol_up, timeout=2.0)
                with self._depth_cache_lock:
                    depth_data = self._depth_cache.get(symbol_up)

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
                        await self._wait_for_quote_cache(symbol_up, timeout=0.35)

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
                                response = await self._make_http_request(method, endpoint, headers=headers, data={"contractId": contract_id})
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
                                response = await self._make_http_request(method, endpoint, headers=headers)
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
                response = await self._make_http_request("GET", f"/api/MarketData/depth/{contract_id}", headers=headers)
            
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
                    # For daily bars, TradingView displays the trading day date (ET) rather than the raw
                    # UTC timestamp (often 23:00Z due to 18:00 ET session boundaries). To make CSV parity
                    # checks deterministic, export the **ET date** as YYYY-MM-DD.
                    time_value = bar.get('time', bar.get('timestamp', 'N/A'))
                    if str(timeframe).lower().endswith('d') and time_value not in (None, '', 'N/A'):
                        try:
                            from datetime import datetime as _dt, timezone as _tz
                            from zoneinfo import ZoneInfo
                            dt = _dt.fromisoformat(str(time_value).replace('Z', '+00:00'))
                            if dt.tzinfo is None:
                                dt = dt.replace(tzinfo=_tz.utc)
                            et = ZoneInfo("America/New_York")
                            time_value = dt.astimezone(et).date().isoformat()
                        except Exception:
                            # If parsing fails, keep raw value
                            pass
                    writer.writerow({
                        'Time': time_value,
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
                                  limit: int = 100, start_time: datetime = None,
                                  end_time: datetime = None, **kwargs) -> List[Dict]:
        """
        Get historical price data for a symbol.

        This is now a thin wrapper around the canonical implementation in
        `TopStepXAdapter.get_historical_data`, so ALL paths (CLI, dashboard,
        strategies) share one source of truth for historical bar logic.
        
        Args:
            symbol: Trading symbol
            timeframe: Bar timeframe (e.g., "1m", "5m", "1h", "1d")
            limit: Maximum number of bars
            start_time: Optional start time
            end_time: Optional end time
            **kwargs: Additional arguments (e.g., continuous_daily, include_partial_daily)
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
                **kwargs
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
    
    async def get_historical_data_parallel(self, requests: List[Dict]) -> Dict[str, List[Dict]]:
        """
        Fetch historical data for multiple symbols/timeframes in parallel.
        
        This optimization reduces startup time by fetching historical data concurrently
        instead of sequentially, particularly useful for multi-symbol strategies.
        
        Args:
            requests: List of dicts with keys: symbol, timeframe, limit, start_time, end_time
                     Example: [
                         {"symbol": "MNQ", "timeframe": "1m", "limit": 100},
                         {"symbol": "MES", "timeframe": "5m", "start_time": dt1, "end_time": dt2}
                     ]
        
        Returns:
            Dict mapping request index or symbol to bars:
                {
                    "MNQ_1m": [...bars...],
                    "MES_5m": [...bars...]
                }
        
        Example:
            requests = [
                {"symbol": "MNQ", "timeframe": "1m", "limit": 500},
                {"symbol": "MES", "timeframe": "1m", "limit": 500},
                {"symbol": "MGC", "timeframe": "1m", "limit": 500}
            ]
            results = await bot.get_historical_data_parallel(requests)
            mnq_bars = results["MNQ_1m"]
        """
        import time
        start_time_fetch = time.time()
        
        async def fetch_one(request_dict: Dict, request_idx: int):
            """Fetch one historical data request."""
            symbol = request_dict.get("symbol")
            timeframe = request_dict.get("timeframe", "1m")
            limit = request_dict.get("limit", 100)
            start_time = request_dict.get("start_time")
            end_time = request_dict.get("end_time")
            
            # Create unique key for this request
            key = f"{symbol}_{timeframe}"
            if start_time:
                key += f"_{start_time.strftime('%Y%m%d')}" if hasattr(start_time, 'strftime') else f"_{start_time}"
            
            try:
                bars = await self.get_historical_data(
                    symbol=symbol,
                    timeframe=timeframe,
                    limit=limit,
                    start_time=start_time,
                    end_time=end_time
                )
                return (key, bars)
            except Exception as e:
                logger.error(f"Failed to fetch {symbol} {timeframe}: {e}")
                return (key, [])
        
        # Fetch all requests in parallel
        tasks = [fetch_one(req, idx) for idx, req in enumerate(requests)]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Build result dict
        result_dict = {}
        for result in results:
            if isinstance(result, Exception):
                logger.error(f"Parallel fetch error: {result}")
                continue
            key, bars = result
            result_dict[key] = bars
        
        elapsed = time.time() - start_time_fetch
        logger.info(f"✅ Parallel historical data fetch: {len(requests)} requests in {elapsed:.2f}s ({elapsed/len(requests) if requests else 0:.2f}s avg)")
        
        return result_dict
    
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
    
    async def run(self, account_select: Optional[str] = None):
        """
        Main bot execution flow with parallel initialization and performance timing.
        
        Args:
            account_select: Optional account index/ID to auto-select (e.g., "1", "2", or account ID)
        
        Uses parallel execution for independent operations to reduce startup time by 30-50%.
        """
        import time as _t
        
        try:
            print("🤖 TopStepX Trading Bot - Real API Version")
            print("="*50)
            
            # Step 1: Ensure valid token (checks expiration and refreshes if needed)
            # This uses JWT token refresh mechanism during startup (before account selection)
            _total_start = _t.time()
            _auth_start = _t.time()
            if not await self._ensure_valid_token():
                print("❌ Authentication failed. Please check your API key.")
                return
            _auth_ms = int((_t.time() - _auth_start) * 1000)
            print(f"✅ Authentication successful! ({_auth_ms} ms)")
            
            # Start event bus for event-driven architecture (optional - only needed if GUI is running)
            if hasattr(self, 'event_bus') and self.event_bus and hasattr(self.event_bus, 'start'):
                try:
                    await self.event_bus.start()
                    logger.info("📡 Event bus started")
                except Exception as e:
                    logger.warning(f"⚠️  Could not start event bus (not critical if GUI not running): {e}")
            
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
            
            # Step 4: Select account (auto-select if provided, otherwise interactive)
            if account_select:
                # Auto-select account by index or ID
                selected_account = None
                try:
                    # Try as index first (1-based)
                    account_index = int(account_select)
                    if 1 <= account_index <= len(accounts):
                        selected_account = accounts[account_index - 1]
                        print(f"✅ Selected account by index {account_index}: {selected_account['name']}")
                except ValueError:
                    # Try as account ID
                    for account in accounts:
                        if str(account.get('id')) == str(account_select):
                            selected_account = account
                            print(f"✅ Selected account by ID: {selected_account['name']}")
                            break
                
                if not selected_account:
                    print(f"❌ Could not find account matching '{account_select}'")
                    selected_account = self.select_account(accounts)
                else:
                    self.selected_account = selected_account
                    # Initialize account tracker for auto-selected account
                    account_balance = selected_account.get('balance', 0)
                    account_type = selected_account.get('type', 'unknown')
                    self.account_tracker.initialize(
                        account_id=selected_account['id'],
                        starting_balance=account_balance,
                        account_type=account_type
                    )
                    logger.info(f"Account tracker initialized for {selected_account['name']} (${account_balance:,.2f})")
            else:
                # Interactive selection
                selected_account = self.select_account(accounts)
            
            if not selected_account:
                print("❌ No account selected. Exiting.")
                return
            
            # Start User Hub for real-time account/position/order updates
            if hasattr(self, 'user_hub_manager'):
                try:
                    account_id = selected_account.get('id')
                    success = await self.user_hub_manager.start(account_id=account_id)
                    if success:
                        logger.info("✅ User Hub connected for real-time updates")
                        # Register event-driven cache invalidation callbacks
                        self._setup_event_driven_cache_invalidation(str(account_id))
                    else:
                        logger.warning("⚠️  User Hub connection failed, will use polling")
                except Exception as e:
                    logger.warning(f"⚠️  Failed to start User Hub: {e}")
            
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
            
            # Step 9: Auto-start enabled strategies (optional; default disabled for interactive CLI)
            auto_start_flag = os.getenv("AUTO_START_STRATEGIES", "0").lower() in ("1", "true", "yes")
            if auto_start_flag and hasattr(self, 'strategy_manager'):
                logger.info("💾 Loading persisted strategy states for CLI session (auto-start enabled)...")
                await self.strategy_manager.apply_persisted_states()
                logger.info("🚀 Auto-starting enabled strategies for CLI session...")
                await self.strategy_manager.auto_start_enabled_strategies()
                logger.info("✅ Strategy initialization complete for CLI session")
            else:
                logger.info("🚫 Auto-start of strategies is disabled for interactive CLI (set AUTO_START_STRATEGIES=1 to enable)")
            
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
    
    async def run_non_interactive(
        self,
        account_select: Optional[str] = None,
        command: Optional[str] = None,
        disable_strategy: Optional[str] = None
    ):
        """
        Run bot in non-interactive mode for CLI/script usage.
        
        Args:
            account_select: Account selection (index like "1" or account ID)
            command: Command to execute (e.g., "stop_bracket mnq buy 1 25000 24980 25020")
            disable_strategy: Comma-separated strategy names to disable
        """
        import time as _t
        from core.cli_command_parser import CLICommandParser
        
        try:
            print("🤖 TopStepX Trading Bot - Non-Interactive Mode")
            print("="*50)
            
            # Step 1: Ensure valid token (with refresh)
            _auth_start = _t.time()
            if not await self._ensure_valid_token():
                print("❌ Authentication failed. Please check your API key.")
                return
            _auth_ms = int((_t.time() - _auth_start) * 1000)
            print(f"✅ Authentication successful! ({_auth_ms} ms)")
            
            # Step 2: Get accounts
            accounts = await self.list_accounts()
            if not accounts:
                print("❌ No active accounts found.")
                return
            
            # Step 3: Select account
            selected_account = None
            if account_select:
                try:
                    # Try as index first
                    if account_select.isdigit():
                        idx = int(account_select) - 1  # Convert to 0-based
                        if 0 <= idx < len(accounts):
                            selected_account = accounts[idx]
                            print(f"✅ Selected account by index {account_select}: {selected_account['name']}")
                        else:
                            print(f"❌ Invalid account index: {account_select} (available: 1-{len(accounts)})")
                            return
                    else:
                        # Try as account ID
                        for acc in accounts:
                            if str(acc.get('id')) == account_select or acc.get('name') == account_select:
                                selected_account = acc
                                print(f"✅ Selected account by ID/name: {selected_account['name']}")
                                break
                        if not selected_account:
                            print(f"❌ Account not found: {account_select}")
                            return
                except Exception as e:
                    print(f"❌ Error selecting account: {e}")
                    return
            else:
                # Auto-select first account
                selected_account = accounts[0]
                print(f"✅ Auto-selected account: {selected_account['name']}")
            
            self.selected_account = selected_account
            
            # Initialize account tracker
            account_balance = selected_account.get('balance', 0)
            account_type = selected_account.get('type', 'unknown')
            self.account_tracker.initialize(
                account_id=selected_account['id'],
                starting_balance=account_balance,
                account_type=account_type
            )
            logger.info(f"Account tracker initialized for {selected_account['name']} (${account_balance:,.2f})")

            # Step 3.5: Fetch contracts in parallel (needed for trading commands)
            print("📋 Fetching available contracts...")
            try:
                await self.get_available_contracts(use_cache=True)
                print("✅ Contracts loaded")
            except Exception as e:
                logger.warning(f"⚠️  Failed to fetch contracts: {e}")
                print(f"⚠️  Warning: Contract fetch failed (some commands may not work): {e}")
            
            # Step 4: Disable strategies if requested
            if disable_strategy:
                strategy_names = [s.strip() for s in disable_strategy.split(',')]
                account_id = selected_account.get('id') if isinstance(selected_account, dict) else selected_account
                
                if hasattr(self, 'db') and self.db:
                    from datetime import datetime, timezone
                    print(f"📋 Disabling strategies: {', '.join(strategy_names)}")
                    for name in strategy_names:
                        result = self.db.save_strategy_state(
                            account_id=str(account_id),
                            strategy_name=name,
                            enabled=False,
                            last_stopped=datetime.now(timezone.utc)
                        )
                        if result:
                            print(f"✅ Disabled strategy: {name}")
                        else:
                            print(f"❌ Failed to disable strategy: {name}")
                else:
                    print("⚠️  Database not available - cannot disable strategies")
            
            # Step 5: Execute command if provided
            if command:
                print(f"\n📤 Executing command: {command}")
                parser = CLICommandParser(self)
                result = await parser.execute_command(command)
                
                if result.get('success'):
                    print("✅ Command executed successfully")
                    if result.get('result'):
                        # Pretty print result
                        import json
                        result_data = result['result']
                        print(json.dumps(result_data, indent=2, default=str))
                        
                        # Check if we need to keep running (e.g., for realtime charts)
                        # Only check keep_running if result_data is a dict
                        if isinstance(result_data, dict) and result_data.get('keep_running'):
                            print(f"\n{result_data.get('message', 'Bot will keep running...')}")
                            print("Press Ctrl+C to stop the bot and close the chart server.\n")
                            
                            # Keep the bot running until interrupted
                            try:
                                import signal
                                import asyncio
                                
                                # Set up signal handler for graceful shutdown
                                def signal_handler(sig, frame):
                                    print("\n\n🛑 Shutting down...")
                                    raise KeyboardInterrupt
                                
                                signal.signal(signal.SIGINT, signal_handler)
                                signal.signal(signal.SIGTERM, signal_handler)
                                
                                # Keep event loop running
                                while True:
                                    await asyncio.sleep(1)
                            except KeyboardInterrupt:
                                print("\n👋 Bot stopped by user")
                                # Stop event bus
                                if hasattr(self, 'event_bus') and self.event_bus:
                                    try:
                                        await self.event_bus.stop()
                                        logger.info("📡 Event bus stopped")
                                    except Exception as e:
                                        logger.error(f"Error stopping event bus: {e}")
                                # Cleanup chart server if needed
                                from gui.chart_html import _chart_server
                                if _chart_server:
                                    try:
                                        await _chart_server.cleanup()
                                        print("✅ Chart server stopped")
                                    except Exception as e:
                                        logger.debug(f"Chart server cleanup: {e}")
                else:
                    print(f"❌ Command failed: {result.get('error', 'Unknown error')}")
                    if result.get('available_commands'):
                        print(f"Available commands: {', '.join(result['available_commands'])}")
            else:
                print("\n✅ Bot initialized successfully (no command specified)")
                print("💡 Use --command='COMMAND' to execute a command")
            
        except Exception as e:
            logger.error(f"Non-interactive execution failed: {str(e)}")
            print(f"❌ Execution failed: {str(e)}")
            import traceback
            traceback.print_exc()
    
    def _setup_readline(self):
        from core.trading_interactive_ui import setup_readline_for_bot
        setup_readline_for_bot()

    async def _async_input(self, prompt: str = "") -> str:
        from core.trading_interactive_ui import async_cli_input
        return await async_cli_input(prompt)

    async def trading_interface(self):
        from core.trading_interactive_ui import run_trading_interface
        await run_trading_interface(self)


def main():
    """
    Main entry point for the trading bot.
    """
    import argparse
    
    # Parse command-line arguments
    parser = argparse.ArgumentParser(description='TopStepX Trading Bot - Real API Version')
    parser.add_argument('-v', '--verbose', action='store_true', 
                       help='Enable verbose/debug logging')
    parser.add_argument('--account_select', type=str, default=None,
                       help='Select account by index (1, 2, 3...) or ID. Example: --account_select=1')
    parser.add_argument('--command', type=str, default=None,
                       help='Execute command non-interactively. Example: --command="stop_bracket mnq buy 1 25000 24980 25020"')
    parser.add_argument('--non_interactive', action='store_true',
                       help='Run in non-interactive mode (for scripts)')
    parser.add_argument('--disable_strategy', type=str, default=None,
                       help='Disable strategies (comma-separated). Example: --disable_strategy=mean_reversion,trend_following')
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
    api_key = os.getenv('PROJECT_X_API_KEY') or os.getenv('TOPSTEPX_API_KEY') or os.getenv('TOPSETPX_API_KEY')
    username = os.getenv('PROJECT_X_USERNAME') or os.getenv('TOPSTEPX_USERNAME') or os.getenv('TOPSETPX_USERNAME')
    
    if not api_key or not username:
        print("⚠️  Environment variables not found.")
        print("Please set your credentials:")
        print("  export PROJECT_X_API_KEY='your_api_key_here'")
        print("  export PROJECT_X_USERNAME='your_username_here'")
        print("  OR")
        print("  export TOPSTEPX_API_KEY='your_api_key_here'")
        print("  export TOPSTEPX_USERNAME='your_username_here'")
        print("  (legacy typo still accepted: TOPSETPX_API_KEY / TOPSETPX_USERNAME)")
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
        # Handle non-interactive mode with CLI commands
        if args.command or args.non_interactive or args.disable_strategy:
            # Non-interactive mode (command execution or disable strategy)
            asyncio.run(bot.run_non_interactive(
                account_select=args.account_select,
                command=args.command,
                disable_strategy=args.disable_strategy
            ))
        else:
            # Interactive mode (account_select allowed without command)
            asyncio.run(bot.run(account_select=args.account_select))
    except KeyboardInterrupt:
        print("\n\n👋 Bot stopped by user.")
    except Exception as e:
        print(f"\n❌ Unexpected error: {str(e)}")
        logger.exception("Unexpected error in main")

if __name__ == "__main__":
    main()
# Force Railway redeploy
