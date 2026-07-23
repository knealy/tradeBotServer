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
from typing import List, Dict, Optional, Any, Iterable
from datetime import datetime, timedelta, timezone
import threading
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
        # Opt-in income brain: one :class:`~core.income_brain.IncomeBrain` per account id
        self._income_brain_cache: Dict[str, Any] = {}
        self.regime_kpi_gate = None
        self.regime_fast_gate = None
        # Real-time quote cache: { SYMBOL: { 'bid': float, 'ask': float, 'last': float, 'volume': float, 'ts': iso } }
        self._quote_cache: Dict[str, Dict] = {}
        self._quote_cache_lock: Lock = Lock()
        # Real-time depth cache: { SYMBOL: { 'bids': [], 'asks': [], 'ts': iso } }
        self._depth_cache: Dict[str, Dict] = {}
        self._depth_cache_lock: Lock = Lock()
        # Contract list cache: { 'contracts': List[Dict], 'timestamp': datetime, 'ttl_minutes': int }
        self._contract_cache: Optional[Dict] = None
        self._contract_cache_lock: Lock = Lock()
        # Serialize concurrent contract API refreshes (hot path: many callers miss cache together)
        self._contract_refresh_async_lock = asyncio.Lock()
        self._market_hub_connected = False
        self._subscribed_symbols = set()
        self._market_hub_open_event = asyncio.Event()
        self._quote_ready_events: Dict[str, asyncio.Event] = {}
        self._depth_ready_events: Dict[str, asyncio.Event] = {}
        
        # Initialize Discord notifier
        self.discord_notifier = DiscordNotifier()
        # Bracket placement: try native Auto OCO first; sticky hybrid after
        # Position-Brackets reject (see place_oco_bracket_with_stop_entry).
        self._prefer_hybrid_brackets = False
        self._position_brackets_alerted = False
        # Hybrid post-fill SL/TP: pending by entry order id + strong task refs
        # (asyncio only keeps weak refs — bare create_task can be GC'd before fill).
        self._hybrid_pending_brackets: Dict[str, Dict[str, Any]] = {}
        self._hybrid_bracket_tasks: Dict[str, Any] = {}
        self._hybrid_attach_locks: Dict[str, asyncio.Lock] = {}
        # Software OCO for hybrid protective legs (broker does not link them).
        # order_id -> {sibling_id, position_id, account_id, symbol, leg, prices...}
        self._hybrid_oco_legs: Dict[str, Dict[str, Any]] = {}
        self._hybrid_oco_tasks: Dict[str, Any] = {}  # keyed by sorted "sl|tp" pair key
        self._hybrid_oco_locks: Dict[str, asyncio.Lock] = {}
        self._hybrid_orphan_sweeper_task: Optional[Any] = None
        self._hybrid_price_oco_tasks: Dict[str, Any] = {}  # pair_key -> price-watch task
        # order_id -> monotonic ts when cancel was claimed (dedupe cancel spam)
        self._hybrid_cancel_claimed: Dict[str, float] = {}

        # Initialize PostgreSQL database (for persistent caching and state).
        # History stitch / offline scripts set DISABLE_DATABASE=1 to skip the
        # Railway DNS wait entirely (see scripts/refresh_historical.sh).
        if str(os.getenv("DISABLE_DATABASE", "") or "").strip().lower() in (
            "1", "true", "yes", "on",
        ):
            self.db = None
            logger.info("PostgreSQL skipped (DISABLE_DATABASE)")
        else:
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

        # Portfolio-level daily-loss circuit breaker (Phase 1 arsenal infra).
        # Lazy: only constructs when ``PORTFOLIO_DAILY_LOSS_CAP`` env > 0.
        # Wired on first ``start_strategy`` via ``ensure_portfolio_breaker``
        # so chart-only / dashboard processes don't pay the import cost.
        self.portfolio_breaker = None

        # Regime classifier publisher (Phase 1 arsenal infra).  Off by
        # default; opt-in via ``REGIME_PUBLISHER_ENABLED=1``.  Same lazy
        # path: ``ensure_regime_publisher`` runs on first ``start_strategy``
        # so chart-only / dashboard processes pay nothing.  No production
        # strategy currently subscribes — strategies that want to gate on
        # regime can either use the polling-style ``classify(bars)`` API
        # directly in ``analyze()`` (recommended) or subscribe to
        # ``EventType.REGIME_UPDATE`` once the publisher is enabled.
        self.regime_publisher = None

        # PA/SMC synthesis engine (the "brain" — see core/market_synthesizer).
        # Off by default; opt-in via ``MARKET_SYNTHESIZER_ENABLED=1``.  Same
        # lazy boot path as the regime publisher: ``ensure_market_synthesizer``
        # runs on first ``start_strategy`` so chart-only / dashboard processes
        # pay nothing.  When enabled, subscribes to ``BAR_COMPLETED`` and
        # publishes ``MARKET_CONTEXT_UPDATED`` events carrying a typed PA/SMC
        # snapshot.  Strategies that want confluence can either subscribe to
        # the event or call ``core.market_synthesizer.get_synthesizer()`` to
        # query the latest cached snapshot synchronously.
        self.market_synthesizer = None

        # Initialize bar aggregator for real-time chart updates
        from core.bar_aggregator import BarAggregator
        self.bar_aggregator = BarAggregator(broadcast_callback=None)  # Will be set by webhook server
        logger.debug("Bar aggregator initialized")

        # Live bar cache (filled by the bar aggregator's completed-bar callback). Used by
        # ``get_historical_data`` to merge fresh SignalR-derived bars on top of REST results
        # so strategies see new data even when ``/api/History/retrieveBars`` stalls. Keyed
        # by ``symbol → normalized_timeframe → deque[bar_dict]`` (capped per series).
        from collections import deque as _deque
        self._live_bars: Dict[str, Dict[str, _deque]] = {}
        self._live_bars_lock = threading.RLock()
        self._live_bars_maxlen = int(os.getenv("LIVE_BAR_CACHE_MAXLEN", "240") or 240)
        try:
            self.bar_aggregator.register_completed_bar_callback(self._on_live_bar_close)
            logger.debug("Wired live-bar cache callback into bar aggregator")
        except Exception as exc:
            logger.debug("Could not wire live-bar cache callback: %s", exc)
        
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

        # BONGO §1B: R-based breakeven for bracket strategies (body_reversion, morning_range, …).
        # Keyed by entry stop-order id from ``place_oco_bracket_with_stop_entry``.
        self._generic_breakeven_monitoring: Dict[str, Dict[str, Any]] = {}
        self._generic_breakeven_task: Optional[Any] = None

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

        # Data-feed health monitor — single instance, wired into BOTH hubs.
        # The post-2026-06-11 staleness gate strategies consult before placing
        # orders.  Living instance + helper getters in core.data_feed_health.
        from core.data_feed_health import get_monitor as _get_health_monitor
        self.data_feed_monitor = _get_health_monitor()
        self.websocket_manager.register_quote_callback(
            self.data_feed_monitor.record_market_tick_callback
        )
        logger.debug("✅ DataFeedHealthMonitor wired to Market Hub")

        # Initialize UserHubManager (handles SignalR User Hub for account/position/order updates)
        self.user_hub_manager = UserHubManager(
            auth_manager=self.auth_manager
        )
        # Register callbacks for User Hub updates
        from core.user_hub_handlers import UserHubHandlers
        self._user_hub_handlers = UserHubHandlers(self)
        self.user_hub_manager.register_account_callback(self._user_hub_handlers.on_account)
        self.user_hub_manager.register_position_callback(self._user_hub_handlers.on_position)
        self.user_hub_manager.register_order_callback(self._user_hub_handlers.on_order)
        self.user_hub_manager.register_trade_callback(self._user_hub_handlers.on_trade)
        # Wire data-feed monitor into User Hub events (any of these = "alive").
        self.user_hub_manager.register_account_callback(
            self.data_feed_monitor.record_user_account_callback
        )
        self.user_hub_manager.register_position_callback(
            self.data_feed_monitor.record_user_position_callback
        )
        self.user_hub_manager.register_order_callback(
            self.data_feed_monitor.record_user_order_callback
        )
        logger.debug("✅ UserHubManager initialized (+ DataFeedHealthMonitor wired)")

        from core.hub_deferred_queue import HubDeferredWorkQueue

        _hq = int(os.getenv("HUB_DEFERRED_QUEUE_MAX", "128"))
        self.hub_deferred_queue = HubDeferredWorkQueue(maxsize=max(8, _hq))
        
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
        # Near-instant hybrid software OCO: cancel peer on TP/SL price touch
        # (does not wait for User Hub fill or Order/search).
        try:
            self._hybrid_price_oco_on_quote(sym)
        except Exception:
            logger.debug("hybrid price OCO quote hook failed", exc_info=True)

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
    
    # ── Live bar cache (Market Hub → bar aggregator → strategies) ────────────────

    @staticmethod
    def _normalize_timeframe_key(tf: Any) -> str:
        """Normalize timeframe spelling so cache keys match across REST and live paths.

        Accepts '5m', '5M', '5min', '5 minutes' → '5m'; '1h', '60m' → '1h'; defaults to
        lower-case stripped string.
        """
        if not tf:
            return ""
        s = str(tf).strip().lower().replace(" ", "")
        s = s.replace("minutes", "m").replace("minute", "m").replace("min", "m")
        s = s.replace("hours", "h").replace("hour", "h").replace("hr", "h")
        s = s.replace("seconds", "s").replace("second", "s").replace("sec", "s")
        return s

    def _on_live_bar_close(self, bar: Any) -> None:
        """Bar aggregator callback: append the closed bar to the per-symbol/tf live cache.

        Bar is the dataclass from ``core.bar_aggregator``. We store dicts shaped like the
        REST response so :meth:`get_historical_data` can merge them without conversions.
        """
        try:
            from collections import deque as _deque

            sym = str(getattr(bar, "symbol", "") or "").upper()
            tf = self._normalize_timeframe_key(getattr(bar, "timeframe", ""))
            ts = getattr(bar, "timestamp", None)
            if not sym or not tf or ts is None:
                return
            ts_iso = ts.isoformat() if hasattr(ts, "isoformat") else str(ts)
            bar_dict = {
                "timestamp": ts_iso,
                "time": ts_iso,
                "open": float(getattr(bar, "open", 0.0)),
                "high": float(getattr(bar, "high", 0.0)),
                "low": float(getattr(bar, "low", 0.0)),
                "close": float(getattr(bar, "close", 0.0)),
                "volume": int(getattr(bar, "volume", 0) or 0),
                "symbol": sym,
            }
            with self._live_bars_lock:
                by_sym = self._live_bars.setdefault(sym, {})
                series = by_sym.get(tf)
                if series is None:
                    series = _deque(maxlen=self._live_bars_maxlen)
                    by_sym[tf] = series
                # Avoid duplicate timestamps (replace tail if same minute closes twice)
                if series and series[-1].get("timestamp") == ts_iso:
                    series[-1] = bar_dict
                else:
                    series.append(bar_dict)
            # Bar-close is a second liveness signal for the health monitor.
            try:
                from core.data_feed_health import get_monitor
                get_monitor().record_bar_activity(sym)
            except Exception:
                pass
        except Exception as exc:
            logger.debug("Error updating live bar cache: %s", exc)

    def _get_live_bars(self, symbol: str, timeframe: str) -> List[Dict]:
        """Return a copy of the cached live bars for (symbol, timeframe), oldest → newest."""
        sym = str(symbol or "").upper()
        tf = self._normalize_timeframe_key(timeframe)
        if not sym or not tf:
            return []
        with self._live_bars_lock:
            by_sym = self._live_bars.get(sym)
            if not by_sym:
                return []
            series = by_sym.get(tf)
            if not series:
                return []
            return list(series)

    @staticmethod
    def _parse_bar_ts(ts: Any) -> Optional[datetime]:
        """Common parser for bar timestamp fields (ISO string, datetime, or epoch)."""
        if ts is None:
            return None
        if isinstance(ts, datetime):
            return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
        try:
            return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        except Exception:
            return None

    @staticmethod
    def _timeframe_seconds(tf: str) -> Optional[float]:
        """Convert a normalized timeframe like '5m', '1h', '15s' to seconds (or None)."""
        if not tf:
            return None
        s = str(tf).strip().lower()
        if s.endswith("ms"):
            try:
                return float(s[:-2]) / 1000.0
            except ValueError:
                return None
        unit = s[-1]
        try:
            n = float(s[:-1])
        except ValueError:
            return None
        return {
            "s": n,
            "m": n * 60.0,
            "h": n * 3600.0,
            "d": n * 86400.0,
        }.get(unit)

    def _live_cache_can_serve(
        self,
        symbol: str,
        timeframe: str,
        limit: int,
        max_lag_factor: float = 2.0,
    ) -> bool:
        """Return True iff the live cache can fully satisfy a ``get_historical_data`` call.

        Conditions:
          1. The cache holds at least ``limit`` bars for (symbol, timeframe).
          2. The newest cached bar is no older than ``max_lag_factor × tf_seconds``
             (e.g. for a 5m timeframe with factor=2.0, the tail must be < 10 min old).

        Used to short-circuit the REST round-trip on the strategy hot path. The
        2026-05-21 outage proved REST can stall for >70 min; once SignalR has been
        feeding the cache through ``_on_live_bar_close``, every subsequent call
        should return from memory in microseconds.
        """
        live = self._get_live_bars(symbol, timeframe)
        if len(live) < int(max(1, limit)):
            return False
        tf_secs = self._timeframe_seconds(self._normalize_timeframe_key(timeframe))
        if tf_secs is None or tf_secs <= 0:
            return False
        tail_ts = self._parse_bar_ts(live[-1].get("timestamp") or live[-1].get("time"))
        if tail_ts is None:
            return False
        age_s = (datetime.now(timezone.utc) - tail_ts).total_seconds()
        if age_s > (max_lag_factor * tf_secs):
            return False
        return True

    def _serve_from_live_cache(self, symbol: str, timeframe: str, limit: int) -> List[Dict]:
        """Return the trailing ``limit`` bars from the live cache (ascending)."""
        live = self._get_live_bars(symbol, timeframe)
        if not live:
            return []
        if limit and limit > 0 and len(live) > limit:
            return list(live[-limit:])
        return list(live)

    def _merge_live_bars(self, rest_bars: List[Dict], symbol: str, timeframe: str) -> List[Dict]:
        """Append any live cache bars newer than the REST tail. Returns the (possibly extended) list.

        Strategy: parse the last REST bar's timestamp; iterate live cache; append bars whose
        timestamp is strictly greater. If REST is empty, return the live cache as-is.
        """
        live = self._get_live_bars(symbol, timeframe)
        if not live:
            return rest_bars

        if not rest_bars:
            logger.debug(
                "get_historical_data: REST returned empty; serving %d live bars from cache (%s %s)",
                len(live), symbol, timeframe,
            )
            return list(live)

        last_rest_ts = self._parse_bar_ts(rest_bars[-1].get("timestamp") or rest_bars[-1].get("time"))
        if last_rest_ts is None:
            return rest_bars

        extras: List[Dict] = []
        for b in live:
            live_ts = self._parse_bar_ts(b.get("timestamp") or b.get("time"))
            if live_ts is None:
                continue
            if live_ts > last_rest_ts:
                extras.append(b)

        if not extras:
            return rest_bars

        # Demoted from INFO to DEBUG (2026-05-29). At one symbol × one timeframe
        # per ~5s poll, three symbols generate 36+ of these per minute and
        # they're useless in steady state — the bar count merging is identical
        # every cycle. Operators care about the merge-rate, not every merge;
        # keep DEBUG so the data is still grep-able from the file when needed.
        logger.debug(
            "📡 get_historical_data: merged %d live bar(s) onto REST tail for %s %s "
            "(rest_tail=%s, live_tail=%s)",
            len(extras), symbol, timeframe,
            last_rest_ts.isoformat(),
            (self._parse_bar_ts(extras[-1].get("timestamp")) or last_rest_ts).isoformat(),
        )
        return list(rest_bars) + extras

    async def _detect_and_handle_stuck_rest(
        self,
        symbol: str,
        timeframe: str,
        rest_last_ts: Optional[str],
    ) -> bool:
        """Detect when ``/api/History/retrieveBars`` is pinned to a stale cached
        response and force a route reset when so.

        2026-05-27 outage: the broker's edge / CDN cache served the same
        ``last_bar_ts = 2026-05-27T12:05:00+00:00`` payload for **1,077
        consecutive fetches over 60 minutes** while wall-clock time advanced
        and live-cache SignalR continued delivering bars (live_tail reached
        ``T13:00:00`` while REST tail stayed pinned at ``T12:05:00``). The
        moment an unrelated ``/api/Auth/...`` call forced a fresh TCP
        connection, REST advanced to ``T13:05:00`` on the very next fetch.
        Conclusion: keepalive route is being served stale, and the fix is to
        rotate the underlying ``aiohttp`` session.

        Detection rule (per ``(symbol, timeframe)``):
          * Same REST ``last_ts`` returned **N ≥ 3** consecutive calls, AND
          * That timestamp has been pinned for ``> 2 × timeframe_seconds`` of
            wall-clock time (so a quiet weekend session with no new bars
            doesn't fire the alarm — only a stuck route does).

        On trip: closes the broker's HTTP session via
        ``auth.force_session_reset(...)``. The bearer token is preserved; the
        next request creates a fresh TCP+TLS connection that bypasses any
        per-route cache. Tracker resets so a subsequent advance returns to
        normal pool reuse.

        Returns ``True`` iff a reset was actually triggered.
        """
        if not rest_last_ts:
            return False
        if not hasattr(self, "_rest_freshness_track"):
            self._rest_freshness_track: Dict[str, Dict[str, Any]] = {}
        tf_key = self._normalize_timeframe_key(timeframe)
        tf_secs = self._timeframe_seconds(tf_key)
        if tf_secs is None or tf_secs <= 0:
            return False
        key = f"{str(symbol).upper()}|{tf_key}"
        now = time.monotonic()
        rec = self._rest_freshness_track.get(key)

        # ── Cross-symbol coalescing window (2026-05-29 fix) ─────────────────────
        # If the bot just woke up and the broker route is pinned to a stale CDN
        # entry, all three symbols (MNQ/MES/MGC) hit ``_detect_and_handle_stuck_rest``
        # within a few hundred ms of each other and EACH triggers its own
        # ``force_session_reset``. The first rotation alone is enough to recover
        # the route — the 2nd and 3rd are wasted work AND they catch the in-flight
        # ``/api/Position/searchOpen`` calls mid-rotation, surfacing extra
        # ``ServerDisconnectedError`` ERRORs that have nothing to do with the
        # actual problem.
        #
        # Coalesce: if any cold-start reset has fired in the last ``_session_reset_cooldown_s``
        # seconds, skip the actual rotation call but still log + rearm the tracker
        # (so the strategy's stale-data check can still observe "we're recovering"
        # and downgrade its severity).
        # _last_session_reset_at_mono uses ``None`` (no rotation yet) as the sentinel
        # rather than 0.0 — under a monkeypatched ``time.monotonic`` (used in tests)
        # the very first call would have ``now == 0.0 == default`` and the cooldown
        # guard ``> 0.0`` would incorrectly treat the first rotation as "no previous
        # rotation". A None sentinel is unambiguous: ``None == no rotation ever``.
        if not hasattr(self, "_last_session_reset_at_mono"):
            self._last_session_reset_at_mono: Optional[float] = None
        if not hasattr(self, "_session_reset_cooldown_s"):
            # 10s window: enough for a fresh TCP+TLS handshake + 2-3 follow-up
            # requests on the new pool but short enough that a stuck route that
            # comes back stuck doesn't get a "free" 60s of pinned response.
            self._session_reset_cooldown_s: float = 10.0

        async def _trip_reset(reason: str, log_msg: str, *log_args, quiet: bool = False) -> bool:
            """Local helper: log + force the broker session reset + arm tracker.

            Used by both the cold-start fast path and the steady-state slow path so
            both branches log/trip identically (and only the trigger differs).

            The tracker is rearmed with ``cold_tripped=True`` so that subsequent
            polls returning the SAME stale timestamp won't re-trip the cold-start
            fast path — the flag clears only when a new bar timestamp arrives.
            Steady-state path is also throttled because we reset
            ``first_seen_at_mono = now``, restarting the 2× tf dwell counter.
            """
            last_reset = self._last_session_reset_at_mono
            in_cooldown = (
                last_reset is not None
                and (now - float(last_reset)) < float(self._session_reset_cooldown_s)
            )
            elapsed_since_last = 0.0 if last_reset is None else (now - float(last_reset))
            if in_cooldown:
                # Another symbol already rotated the session ${elapsed:.1f}s ago.
                # Skip the actual reset but still mark the tracker and surface a
                # softer DEBUG line so a careful operator can see we suppressed
                # the duplicate.
                logger.debug(
                    "REST stuck detector: skipping duplicate session reset for %s (last reset %.1fs ago, "
                    "inside %.0fs coalesce window)",
                    reason, elapsed_since_last, float(self._session_reset_cooldown_s),
                )
                self._rest_freshness_track[key] = {
                    "last_ts": rest_last_ts,
                    "first_seen_at_mono": now,
                    "consecutive": 1,
                    "cold_tripped": True,
                }
                # We didn't *call* force_session_reset, but downstream code wants
                # to know "the route is being rotated" — and it IS being rotated
                # by whichever sibling tripped first. Return True so the strategy
                # can downgrade its STALE DATA log to WARNING.
                return True

            # ── Claim the cooldown window BEFORE awaiting force_session_reset.
            # 2026-05-29 race fix: when two symbols (e.g. MES + MNQ) tripped within
            # microseconds of each other, both tasks read ``_last_session_reset_at_mono``
            # as ``None`` (or the OLD value), saw ``in_cooldown=False``, and both
            # proceeded into the ``await auth.force_session_reset(...)`` branch —
            # firing two real HTTP session rotations 5ms apart. The 2026-05-29
            # 14:00:21 log captured this exactly:
            #   ``rest-stuck:MES:5m → reset @ 14:00:21.326``
            #   ``rest-stuck:MNQ:5m → reset @ 14:00:21.331``
            # By stamping ``_last_session_reset_at_mono`` *before* the await yields,
            # any task that re-enters ``_trip_reset`` while the first rotation is
            # still in flight will see the cooldown window already claimed and
            # take the coalesce-fast-path return above.
            self._last_session_reset_at_mono = now
            if quiet:
                logger.debug(log_msg, *log_args)
            else:
                logger.warning(log_msg, *log_args)
            try:
                auth = getattr(getattr(self, "broker_adapter", None), "auth", None)
                if auth is not None and hasattr(auth, "force_session_reset"):
                    did_reset = await auth.force_session_reset(reason=reason)
                else:
                    logger.debug("REST stuck detector: broker_adapter.auth.force_session_reset unavailable")
                    did_reset = False
            except Exception as exc:
                logger.error("REST stuck detector: session reset raised %s: %s", type(exc).__name__, exc)
                did_reset = False
            self._rest_freshness_track[key] = {
                "last_ts": rest_last_ts,
                "first_seen_at_mono": now,
                "consecutive": 1,
                "cold_tripped": True,
            }
            return bool(did_reset)

        # ── COLD-START fast path (2026-05-29 fix) ────────────────────────────────
        # The original steady-state rule (≥ 3 same-ts polls AND > 2× timeframe of
        # wall-clock dwell) needs ~10 minutes of polling on a 5m bar before it
        # trips. That works when the route goes stale mid-session, but it's too
        # slow at COLD START: the bot wakes up, the broker's first REST response
        # is already a bar that's hours old (route pinned to a stale CDN entry
        # since the last keepalive close), and the strategy bleeds 10 minutes of
        # ``⛔ STALE DATA`` errors before recovery — long enough to miss the
        # entire fade window. The 2026-05-29 log shows this exact pattern:
        # 08:35:30 bot start → 08:35:32 first stale ERROR (bar 1233s old) →
        # 08:45:33 first stuck-REST trip → recovery only at 08:45.
        #
        # The fix is to look at the BAR'S ABSOLUTE AGE (now − bar_ts in real
        # seconds) on every fetch and trip immediately when it's already past
        # the steady-state threshold. Independent of how long we've been
        # polling, a bar that's already 2× its own timeframe old at first sight
        # means the route is stuck and we should rotate right now.
        bar_age_s: Optional[float] = None
        bar_dt = self._parse_bar_ts(rest_last_ts)
        if bar_dt is not None:
            try:
                bar_age_s = (datetime.now(timezone.utc) - bar_dt).total_seconds()
            except Exception:
                bar_age_s = None
        cold_threshold_s = 2.0 * tf_secs
        if bar_age_s is not None and bar_age_s > cold_threshold_s:
            # Trip only ONCE per pinned timestamp (don't fire every poll while the
            # route is still mid-rotate). Two cases qualify as "fire-worthy":
            #
            #   1. ``rec is None`` — never observed this (symbol, tf) before. Most
            #      common at cold start: bot just woke up, first REST response is
            #      already a 20-min-old bar → rotate now.
            #   2. ``rec.last_ts != rest_last_ts`` — tracker exists but for an
            #      older bar, and the broker has now advanced to a NEW but still
            #      stale bar. Rotate before we accept the new staleness.
            #
            # ``cold_tripped=True`` on the existing record means "we already
            # rotated for THIS exact ts; let the steady-state path take over from
            # here". This prevents the reset-storm we saw in
            # test_coldstart_does_not_double_trip_on_same_stuck_timestamp.
            already_tripped_this_ts = (
                rec is not None
                and rec.get("last_ts") == rest_last_ts
                and bool(rec.get("cold_tripped", False))
            )
            if not already_tripped_this_ts:
                return await _trip_reset(
                    f"rest-coldstart:{symbol}:{tf_key}",
                    "🧊 REST feed cold-start stale for %s %s: last_ts=%s is %.0fs old "
                    "(threshold %.0fs = 2× timeframe). Rotating HTTP session immediately "
                    "to bypass route-pinned stale cache — recovers in seconds instead of "
                    "waiting for the steady-state %.0fs dwell trigger.",
                    symbol, timeframe, rest_last_ts, bar_age_s, cold_threshold_s, cold_threshold_s,
                )

        if rec is None or rec.get("last_ts") != rest_last_ts:
            self._rest_freshness_track[key] = {
                "last_ts": rest_last_ts,
                "first_seen_at_mono": now,
                "consecutive": 1,
            }
            return False
        # Same timestamp as before — advance counters.
        rec["consecutive"] = int(rec.get("consecutive", 0)) + 1
        stuck_for = now - float(rec.get("first_seen_at_mono", now))
        # Stuck threshold: at least 3 repeats AND wall-clock dwell > 2 × timeframe.
        # The ``2 ×`` matches the live-cache freshness threshold so the two layers
        # speak the same language: if the live cache would consider its own tail
        # stale, the REST route is definitely overdue too.
        if rec["consecutive"] < 3 or stuck_for <= 2.0 * tf_secs:
            return False
        # Steady-state trip — rotate quietly; SignalR live cache is the primary bar path.
        return await _trip_reset(
            f"rest-stuck:{symbol}:{tf_key}",
            "REST feed pinned for %s %s: last_ts=%s repeated %d× over %.0fs "
            "(threshold %.0fs = 2× timeframe). Rotating HTTP session so the next "
            "/api/History/retrieveBars call hits a fresh route.",
            symbol, timeframe, rest_last_ts, rec["consecutive"], stuck_for, 2.0 * tf_secs,
            quiet=True,
        )

    def _rest_freshness_key(self, symbol: str, timeframe: str) -> str:
        return f"{str(symbol).upper()}|{self._normalize_timeframe_key(timeframe)}"

    def is_rest_bar_feed_pinned(self, symbol: str, timeframe: str) -> bool:
        """True when REST ``retrieveBars`` has returned the same ``last_ts`` long enough to trip the stuck detector."""
        if not hasattr(self, "_rest_freshness_track"):
            return False
        tf_secs = self._timeframe_seconds(self._normalize_timeframe_key(timeframe))
        if tf_secs is None or tf_secs <= 0:
            return False
        rec = self._rest_freshness_track.get(self._rest_freshness_key(symbol, timeframe))
        if not rec:
            return False
        now = time.monotonic()
        stuck_for = now - float(rec.get("first_seen_at_mono", now))
        consecutive = int(rec.get("consecutive", 0) or 0)
        return consecutive >= 3 and stuck_for > (2.0 * tf_secs)

    def rest_bar_tail_age_seconds(self, symbol: str, timeframe: str) -> Optional[float]:
        """Wall-clock age of the last REST tail timestamp observed for (symbol, timeframe)."""
        if not hasattr(self, "_rest_freshness_track"):
            return None
        rec = self._rest_freshness_track.get(self._rest_freshness_key(symbol, timeframe))
        if not rec:
            return None
        rest_last_ts = rec.get("last_ts")
        bar_dt = self._parse_bar_ts(rest_last_ts)
        if bar_dt is None:
            return None
        try:
            return (datetime.now(timezone.utc) - bar_dt).total_seconds()
        except Exception:
            return None

    @staticmethod
    def _parse_order_event_timestamp(order: Dict) -> Optional[datetime]:
        """Best-effort parse of when an order was filled/updated (UTC)."""
        for key in (
            "updateTimestamp",
            "updatedTimestamp",
            "executionTimestamp",
            "fillTime",
            "filledTime",
            "creationTimestamp",
            "timestamp",
        ):
            raw = order.get(key)
            if not raw:
                continue
            try:
                if isinstance(raw, datetime):
                    dt = raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
                else:
                    dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(timezone.utc)
            except Exception:
                continue
        return None

    def _strategy_from_custom_tag(self, tag: str) -> str:
        from core.discord_notifier import strategy_from_custom_tag
        return strategy_from_custom_tag(tag)

    def _order_fill_notification_allowed(self, order: Dict) -> bool:
        """Gate poll-based fill notifications so headless executors skip stale cross-strategy fills."""
        tag = str(order.get("customTag") or "")
        active = os.getenv("STRATEGY_EXECUTOR_ACTIVE_STRATEGY", "").strip()
        if active and active not in tag:
            return False
        startup = getattr(self, "_startup_time", None)
        if startup is not None:
            ts = self._parse_order_event_timestamp(order)
            if ts is not None and ts < (startup - timedelta(seconds=30)):
                return False
        return True

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

    async def start_market_hub_for_strategies(
        self,
        symbols: Iterable[str],
        timeframes: Optional[Iterable[str]] = None,
    ) -> bool:
        """Open Market Hub + bar aggregator and subscribe quote feeds for strategy symbols.

        Idempotent: safe to call multiple times. Returns True on best-effort success
        (Market Hub connected and at least one quote subscription attempted). Failure
        is logged as a warning, not raised, because REST polling remains the fallback.

        Args:
            symbols: Symbols whose live ticks should drive the bar aggregator.
            timeframes: Optional iterable of timeframes (e.g. ``["5m", "15m"]``) to
                register on the aggregator so strategy lookups find pre-built series.
                Default: rely on the aggregator's built-in default frames.
        """
        try:
            await self._ensure_market_socket_started()
        except Exception as exc:
            logger.warning("Market Hub failed to start (REST-only mode): %s", exc)
            return False

        if not getattr(self, "_market_hub_connected", False):
            logger.warning("Market Hub did not connect — strategies will continue on REST polling only")
            return False

        # Ensure the bar aggregator's start() ran so completed-bar callbacks fire.
        agg = getattr(self, "bar_aggregator", None)
        if agg is not None and not getattr(agg, "_running", False):
            try:
                await agg.start()
            except Exception as exc:
                logger.debug("bar_aggregator.start() raised: %s", exc)

        tfs = [str(tf).strip() for tf in (timeframes or []) if tf and str(tf).strip()]
        attempted = 0
        symbols_norm: List[str] = []
        for sym in symbols:
            if not sym:
                continue
            sym_norm = str(sym).strip().upper()
            symbols_norm.append(sym_norm)
            try:
                await self._ensure_quote_subscription(sym_norm)
                attempted += 1
            except Exception as exc:
                logger.warning("Quote subscription failed for %s: %s", sym_norm, exc)
                continue
            if agg is not None and tfs:
                try:
                    agg.register_timeframes(sym_norm, tfs)
                except Exception as exc:
                    logger.debug("register_timeframes(%s, %s) raised: %s", sym_norm, tfs, exc)

        # ── A: Warm up the live bar cache with one REST fetch per (symbol, timeframe) so
        # the first analyze() call after startup is a cache hit, not a REST round-trip. ──
        warmup_bars = int(os.getenv("LIVE_BAR_CACHE_WARMUP_BARS", "200") or 200)
        if warmup_bars > 0 and symbols_norm and tfs:
            try:
                await self._warmup_live_bar_cache(symbols_norm, tfs, warmup_bars)
            except Exception as exc:
                logger.warning("Live bar cache warmup raised (non-fatal): %s", exc)

        logger.info(
            "📡 Market Hub wired for %d symbol(s); timeframes registered: %s",
            attempted, ", ".join(tfs) or "(aggregator defaults)",
        )

        # Start the SignalR zombie-connection watchdog now that we have
        # symbols subscribed.  Idempotent — safe to call across multiple
        # strategies in the same process.
        if attempted > 0 and not getattr(self, "_data_feed_watchdog", None):
            try:
                from core.data_feed_watchdog import DataFeedWatchdog
                from core.data_feed_health import get_monitor as _get_health_monitor
                from core.working_order_registry import get_registry as _get_wo_registry
                if os.getenv("DATA_FEED_WATCHDOG", "true").lower() not in ("false", "0", "no"):
                    def _watchdog_account_name() -> str:
                        acc = getattr(self, "selected_account", None)
                        if isinstance(acc, dict):
                            return str(acc.get("name") or acc.get("id") or "")
                        return str(acc or "")

                    self._data_feed_watchdog = DataFeedWatchdog(
                        monitor=_get_health_monitor(),
                        market_hub_manager=self.websocket_manager,
                        user_hub_manager=getattr(self, "user_hub_manager", None),
                        broker_adapter=getattr(self, "broker_adapter", None),
                        working_order_registry=_get_wo_registry(),
                        discord_notifier=getattr(self, "discord_notifier", None),
                        account_name_getter=_watchdog_account_name,
                        subscribed_symbols_getter=lambda: list(self._subscribed_symbols),
                    )
                    self._data_feed_watchdog.start()
            except Exception as exc:
                logger.warning("DataFeedWatchdog failed to start (continuing): %s", exc)

        return attempted > 0

    async def _warmup_live_bar_cache(
        self,
        symbols: Iterable[str],
        timeframes: Iterable[str],
        bars: int,
    ) -> None:
        """Prime ``_live_bars`` with one REST fetch per (symbol, timeframe).

        Run concurrently so total wait ≈ slowest single REST call (~300 ms) rather than
        N × 300 ms serial. Failures are logged and swallowed so a single endpoint hiccup
        doesn't prevent the executor from coming up.
        """
        tasks = []
        for sym in symbols:
            for tf in timeframes:
                tasks.append(self._warmup_one_series(sym, tf, bars))
        if not tasks:
            return
        results = await asyncio.gather(*tasks, return_exceptions=True)
        ok = sum(1 for r in results if r is True)
        logger.info(
            "🔥 Live bar cache warmup complete: %d/%d series filled (%d bars target)",
            ok, len(tasks), bars,
        )

    # ── H: Idle TLS keep-alive heartbeat ──────────────────────────────────────────
    # Without traffic for ~60 s the broker (or any intermediary) may close the TCP/TLS
    # session, forcing a 100–250 ms handshake on the next REST call. A periodic cheap
    # POST keeps the connection warm. We use ``/api/Account/search`` because it always
    # returns a small JSON, exercises the bearer token, and matches the live order path
    # exactly (same host, same headers, same JSON serializer).

    async def start_keepalive_heartbeat(self) -> None:
        """Start the idle keep-alive task once. Idempotent. Skips if interval <= 0."""
        if getattr(self, "_keepalive_task", None) is not None:
            return
        try:
            interval = float(os.getenv("BROKER_KEEPALIVE_HEARTBEAT_SEC", "120") or 0.0)
        except ValueError:
            interval = 120.0
        if interval <= 0:
            logger.debug("Broker keep-alive heartbeat disabled (BROKER_KEEPALIVE_HEARTBEAT_SEC=0)")
            return
        self._keepalive_task = asyncio.create_task(self._keepalive_heartbeat_loop(interval))
        logger.info("💓 Broker keep-alive heartbeat started (every %.0fs)", interval)

    async def _keepalive_heartbeat_loop(self, interval: float) -> None:
        """Periodic cheap REST call to keep TCP+TLS session warm."""
        # Tiny jitter so multiple bots in the same process don't synchronize calls.
        import random as _rand

        while True:
            try:
                await asyncio.sleep(interval + _rand.uniform(0.0, 5.0))
            except asyncio.CancelledError:
                logger.info("💓 Broker keep-alive heartbeat stopped")
                raise

            try:
                if hasattr(self, "auth_manager") and getattr(self.auth_manager, "session_token", None):
                    # ``list_accounts`` already retries on 401/403, so it doubles as a
                    # cheap token-validity probe. Result is discarded.
                    accounts = await self.auth_manager.list_accounts()
                    logger.debug(
                        "💓 keep-alive ping ok (%d account(s) returned)",
                        len(accounts) if accounts else 0,
                    )
                else:
                    logger.debug("💓 keep-alive skipped — no session token yet")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.debug("💓 keep-alive ping error (continuing): %s", exc)

    async def stop_keepalive_heartbeat(self) -> None:
        """Cancel the heartbeat task on shutdown (idempotent)."""
        task = getattr(self, "_keepalive_task", None)
        if task is None or task.done():
            self._keepalive_task = None
            return
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
        self._keepalive_task = None

    async def _warmup_one_series(self, symbol: str, timeframe: str, bars: int) -> bool:
        """Fetch ``bars`` historical bars and copy them into ``_live_bars`` for fast reads."""
        try:
            adapter_bars = await self.broker_adapter.get_historical_data(
                symbol=symbol,
                timeframe=timeframe,
                limit=bars,
            )
            if not adapter_bars:
                return False
            sym_key = str(symbol).upper()
            tf_key = self._normalize_timeframe_key(timeframe)
            from collections import deque as _deque

            with self._live_bars_lock:
                by_sym = self._live_bars.setdefault(sym_key, {})
                series = by_sym.get(tf_key)
                if series is None:
                    series = _deque(maxlen=self._live_bars_maxlen)
                    by_sym[tf_key] = series
                else:
                    series.clear()
                for b in adapter_bars:
                    ts = getattr(b, "timestamp", None)
                    if ts is None:
                        continue
                    ts_iso = ts.isoformat() if hasattr(ts, "isoformat") else str(ts)
                    series.append({
                        "timestamp": ts_iso,
                        "time": ts_iso,
                        "open": float(getattr(b, "open", 0.0)),
                        "high": float(getattr(b, "high", 0.0)),
                        "low": float(getattr(b, "low", 0.0)),
                        "close": float(getattr(b, "close", 0.0)),
                        "volume": int(getattr(b, "volume", 0) or 0),
                        "symbol": sym_key,
                    })
            return True
        except Exception as exc:
            logger.debug("Warmup failed for %s %s: %s", symbol, timeframe, exc)
            return False
    
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
                try:
                    await self.hub_deferred_queue.ensure_started()
                except Exception as hq_err:
                    logger.debug("Hub deferred queue start: %s", hq_err)
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

    def _income_brain_account_id(self) -> Optional[str]:
        from core.income_brain import income_brain_account_id_from_bot

        return income_brain_account_id_from_bot(self)

    def income_brain_entry_quantity(self, strategy_name: str, requested: int) -> int:
        """Clamp entry size when ``INCOME_BRAIN`` is enabled; else passthrough.

        Uses ``account_tracker.get_daily_pnl`` so daily halt matches broker session PnL.
        """
        try:
            from core.income_brain import income_brain_entry_quantity_for_bot
        except ImportError:
            try:
                return max(0, int(requested))
            except (TypeError, ValueError):
                return 0
        return income_brain_entry_quantity_for_bot(self, strategy_name, requested)

    def regime_sizing_entry_quantity(
        self, strategy_name: str, symbol: str, requested: int,
    ) -> int:
        """Clamp entry size when ``REGIME_SIZING_ENABLED`` (calendar-era prototype)."""
        try:
            from core.regime_sizing import regime_sizing_entry_quantity_for_bot
        except ImportError:
            try:
                return max(0, int(requested))
            except (TypeError, ValueError):
                return 0
        return regime_sizing_entry_quantity_for_bot(
            self, strategy_name, symbol, requested,
        )

    def regime_kpi_entry_quantity(
        self, strategy_name: str, symbol: str, requested: int,
    ) -> int:
        """Clamp entry size when ``REGIME_KPI_GATE_ENABLED`` (rolling session KPI)."""
        try:
            from core.regime_kpi_gate import regime_kpi_entry_quantity_for_bot
        except ImportError:
            try:
                return max(0, int(requested))
            except (TypeError, ValueError):
                return 0
        return regime_kpi_entry_quantity_for_bot(
            self, strategy_name, symbol, requested,
        )

    def regime_fast_entry_quantity(
        self, strategy_name: str, symbol: str, requested: int,
    ) -> int:
        """Clamp entry size when ``REGIME_FAST_GATE_ENABLED`` (session halt / fast roll)."""
        try:
            from core.regime_fast_gate import regime_fast_entry_quantity_for_bot
        except ImportError:
            try:
                return max(0, int(requested))
            except (TypeError, ValueError):
                return 0
        return regime_fast_entry_quantity_for_bot(
            self, strategy_name, symbol, requested,
        )

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
                    logger.debug(
                        "get_account_balance fallback path failed for %s",
                        target_account,
                        exc_info=True,
                    )
                
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
                    snap = await self.get_positions_and_orders_batch(target_account)
                    positions = snap.get("positions") or []
                    orders = snap.get("orders") or []
                    account_info["positions_count"] = len(positions)
                    account_info["orders_count"] = len(orders)
                except Exception:
                    logger.debug(
                        "get_positions_and_orders_batch in cached account_info failed",
                        exc_info=True,
                    )
                
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

            account_key = str(target_account)
            warmup_limit = 50 if not self._notification_warmup_done.get(account_key) else 10
            orders = await self.get_order_history(target_account, limit=warmup_limit)
            filled_orders = []

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
                    if not self._order_fill_notification_allowed(order):
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
                        strat_slug = self._strategy_from_custom_tag(custom_tag)
                            
                        notification_data = {
                            'symbol': symbol,
                            'side': side,
                            'quantity': quantity,
                            'fill_price': f"${float(fill_price):.2f}" if fill_price else "Unknown",
                            'order_type': order_type_str,
                            'order_id': order_id,
                            'position_id': position_id,
                            'custom_tag': custom_tag,
                            'strategy': strat_slug,
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

        TopStepX rejects / HTTP-500s on ``customTag`` longer than 64 chars
        (empty body). Keep tags short like ``TopStepXAdapter._generate_unique_custom_tag``.
        """
        import uuid

        base_tag = f"TB-{order_type}"
        if strategy_name:
            safe = "".join(
                ch if ch.isalnum() or ch in ("_", "-") else "_"
                for ch in str(strategy_name).lower()
            )
            if len(safe) > 16:
                safe = safe[:16]
            base_tag += f"-{safe}"

        timestamp = datetime.now().strftime("%y%m%d%H%M%S")
        unique_id = str(uuid.uuid4())[:6]
        self._order_counter += 1
        tag = f"{base_tag}-{timestamp}-{unique_id}"
        if len(tag) > 64:
            tag = tag[:64]
        return tag

    async def place_market_order(self, symbol: str, side: str, quantity: int, account_id: str = None, 
                                stop_loss_ticks: int = None, take_profit_ticks: int = None, order_type: str = "market", 
                                limit_price: float = None, strategy_name: str = None, reduce_only: bool = False,
                                custom_tag: Optional[str] = None) -> Dict:
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
                "customTag": (
                    str(custom_tag).strip()[:64]
                    if custom_tag
                    else self._generate_unique_custom_tag("market", strategy_name)
                ),
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
            
            async with self._contract_refresh_async_lock:
                # Another coroutine may have filled the cache while we waited
                if use_cache:
                    with self._contract_cache_lock:
                        if self._contract_cache is not None:
                            cache_age = datetime.now() - self._contract_cache["timestamp"]
                            if cache_age < timedelta(minutes=cache_ttl_minutes):
                                logger.debug(
                                    "Using cached contract list after refresh wait (%d contracts)",
                                    len(self._contract_cache["contracts"]),
                                )
                                return self._contract_cache["contracts"].copy()

                logger.debug("Fetching available contracts from API...")

                # Use AuthManager for authentication
                if not await self.auth_manager.ensure_valid_token():
                    logger.error("No session token available. Please authenticate first.")
                    return []

                headers = {
                    "accept": "application/json",
                    "Content-Type": "application/json",
                    **self.auth_manager.get_auth_headers(),
                }

                # Use broker adapter for contract fetching if available
                if hasattr(self, "broker_adapter"):
                    try:
                        contracts = await self.broker_adapter.get_available_contracts(
                            use_cache=use_cache, cache_ttl_minutes=cache_ttl_minutes
                        )
                        if contracts:
                            with self._contract_cache_lock:
                                self._contract_cache = {
                                    "contracts": contracts.copy(),
                                    "timestamp": datetime.now(),
                                    "ttl_minutes": cache_ttl_minutes,
                                }
                            if hasattr(self, "contract_manager"):
                                self.contract_manager.set_contract_cache(contracts, cache_ttl_minutes)
                        logger.debug("Found %d available contracts via adapter", len(contracts or []))
                        return contracts or []
                    except Exception as adapter_err:
                        logger.warning(
                            "Adapter contract fetch failed, falling back to direct API: %s", adapter_err
                        )

                # Fallback to direct API call
                response = await self._make_http_request(
                    "POST",
                    "/api/Contract/available",
                    data={"live": False},  # Use False for simulation/paper trading contracts
                    headers=headers,
                )

                # Check if API returned an error
                if "error" in response or not response:
                    logger.warning("Contract API returned error or empty response")
                    # Return cached data if available
                    if use_cache:
                        with self._contract_cache_lock:
                            if self._contract_cache is not None:
                                logger.warning(
                                    "API error, returning stale cached contracts (%d contracts)",
                                    len(self._contract_cache["contracts"]),
                                )
                                return self._contract_cache["contracts"].copy()

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
                if isinstance(response, dict) and response.get("success") is False:
                    error_code = response.get("errorCode", "Unknown")
                    error_msg = response.get("errorMessage", "No error message")
                    logger.error("API returned error: Code %s, Message: %s", error_code, error_msg)
                    # Try cached data first
                    if use_cache:
                        with self._contract_cache_lock:
                            if self._contract_cache is not None:
                                logger.warning("Using stale cached contracts due to API error")
                                return self._contract_cache["contracts"].copy()
                    return []

                # Parse contracts from response
                if isinstance(response, list):
                    contracts = response
                elif isinstance(response, dict):
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
                        logger.warning(
                            "Unexpected contracts response format (dict): %s", list(response.keys())
                        )
                        contracts = []
                else:
                    logger.warning("Unexpected contracts response type: %s", type(response))
                    contracts = []

                if contracts and len(contracts) > 0:
                    sample = contracts[0]
                    logger.debug(
                        "Sample contract structure: %s",
                        list(sample.keys()) if isinstance(sample, dict) else type(sample),
                    )
                    if isinstance(sample, dict):
                        logger.debug(
                            "Sample contract fields: symbol=%s, contractId=%s, name=%s",
                            sample.get("symbol"),
                            sample.get("contractId"),
                            sample.get("name"),
                        )

                if use_cache:
                    with self._contract_cache_lock:
                        self._contract_cache = {
                            "contracts": contracts.copy(),
                            "timestamp": datetime.now(),
                            "ttl_minutes": cache_ttl_minutes,
                        }
                        logger.debug(
                            "Cached %d contracts for %d minutes", len(contracts), cache_ttl_minutes
                        )
                        sample_symbols = []
                        for contract in contracts[:10]:
                            if isinstance(contract, dict):
                                sym = contract.get("symbol") or contract.get("Symbol") or contract.get("ticker")
                                if not sym and contract.get("contractId"):
                                    cid = str(contract.get("contractId"))
                                    if "." in cid:
                                        parts = cid.split(".")
                                        if len(parts) >= 4:
                                            sym = parts[-2]
                                if sym:
                                    sample_symbols.append(str(sym).upper())
                        if sample_symbols:
                            logger.debug("Sample symbols in cache: %s", sorted(set(sample_symbols)))

                logger.debug("Found %d available contracts", len(contracts))
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
    
    async def ensure_regime_publisher(self) -> "Optional[Any]":
        """Lazily construct + start the regime classifier publisher.

        Off by default — only constructs when ``REGIME_PUBLISHER_ENABLED``
        env is truthy.  See ``core/regime.py::maybe_start_regime_publisher``
        for the env-var contract.

        Idempotent — multiple callers (StrategyManager.start_strategy,
        master CLI, etc.) can call this safely.  No production strategy
        currently consumes ``REGIME_UPDATE`` events; the publisher is an
        opt-in extension point, not a hard dependency.
        """
        if self.regime_publisher is not None:
            return self.regime_publisher
        try:
            from core.regime import maybe_start_regime_publisher
            svc = maybe_start_regime_publisher(self)
            if svc is None:
                return None
            self.regime_publisher = svc
            await svc.start()
            return svc
        except Exception as exc:
            logger.error("ensure_regime_publisher failed: %s", exc, exc_info=True)
            self.regime_publisher = None
            return None

    async def ensure_market_synthesizer(self) -> "Optional[Any]":
        """Lazily construct + start the PA/SMC synthesis engine ("the brain").

        Off by default — only constructs when ``MARKET_SYNTHESIZER_ENABLED``
        env is truthy.  See ``core/market_synthesizer.py::maybe_start_synthesizer``
        for the full env-var contract (symbols, timeframes, window, lookback).

        Idempotent — multiple callers can call this safely.  When enabled,
        the service subscribes to ``BAR_COMPLETED`` events on the bot's event
        bus and publishes ``MARKET_CONTEXT_UPDATED`` events with the synthesized
        PA/SMC snapshot.  Strategies can query the latest snapshot
        synchronously via ``core.market_synthesizer.get_synthesizer()``.

        No production strategy consumes the brain yet — this just installs the
        publisher so the snapshots are available for opt-in consumers (the
        dashboard, future confluence-aware strategies, the next-generation
        liquidity-sweep work).
        """
        if self.market_synthesizer is not None:
            return self.market_synthesizer
        try:
            from core.market_synthesizer import maybe_start_synthesizer
            svc = maybe_start_synthesizer(self)
            if svc is None:
                return None
            self.market_synthesizer = svc
            await svc.start()
            return svc
        except Exception as exc:
            logger.error("ensure_market_synthesizer failed: %s", exc, exc_info=True)
            self.market_synthesizer = None
            return None

    async def ensure_portfolio_breaker(self) -> "Optional[Any]":
        """Lazily construct + start the portfolio daily-loss breaker.

        Reads ``PORTFOLIO_DAILY_LOSS_CAP`` (USD, positive number).  Zero or
        unset disables the breaker entirely.  Default is ``1000`` (matches
        the worst-case-day arithmetic in ``docs/STRATEGY_ARSENAL.md``).

        Idempotent — multiple callers (StrategyManager.start_strategy,
        master CLI, etc.) can call this safely.
        """
        if self.portfolio_breaker is not None:
            return self.portfolio_breaker
        try:
            cap = float(os.environ.get("PORTFOLIO_DAILY_LOSS_CAP", "1000").strip() or "1000")
        except (TypeError, ValueError):
            cap = 1000.0
        if cap <= 0:
            logger.info("Portfolio breaker disabled (PORTFOLIO_DAILY_LOSS_CAP=%s)", cap)
            return None
        try:
            from core.portfolio_daily_breaker import (
                PortfolioBreakerConfig, PortfolioDailyBreaker,
            )
            self.portfolio_breaker = PortfolioDailyBreaker(
                self, PortfolioBreakerConfig(daily_loss_cap_dollars=cap),
            )
            started = await self.portfolio_breaker.start()
            if not started:
                logger.warning("Portfolio breaker could not subscribe (no event bus yet)")
            return self.portfolio_breaker
        except Exception as exc:
            logger.error("ensure_portfolio_breaker failed: %s", exc, exc_info=True)
            self.portfolio_breaker = None
            return None

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

    def _adapter_positions_to_ui_dicts(self, positions) -> List[Dict]:
        """Convert broker ``Position`` models to UI/API dicts (shared hot path)."""
        result: List[Dict] = []
        for pos in positions:
            try:
                contract_id = None
                if pos.symbol:
                    try:
                        contract_id = self.contract_manager.get_contract_id(pos.symbol)
                    except (ValueError, AttributeError):
                        pass

                pos_dict = {
                    "id": pos.position_id,
                    "position_id": pos.position_id,
                    "symbol": pos.symbol,
                    "contractId": contract_id,
                    "contract_id": contract_id,
                    "side": 0 if pos.side == "LONG" else 1,
                    "size": pos.quantity,
                    "quantity": pos.quantity,
                    "entryPrice": pos.entry_price,
                    "entry_price": pos.entry_price,
                    "currentPrice": pos.current_price,
                    "current_price": pos.current_price,
                    "unrealizedPnl": pos.unrealized_pnl,
                    "unrealized_pnl": pos.unrealized_pnl,
                    "accountId": pos.account_id,
                    "account_id": pos.account_id,
                }

                if hasattr(pos, "raw_data") and pos.raw_data:
                    pos_dict.update(pos.raw_data)
                    pos_dict["symbol"] = pos.symbol

                result.append(pos_dict)
            except Exception as e:
                logger.warning("Failed to convert position to dict: %s", e)
                logger.exception(e)
                continue
        return result
    
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
            
            positions = await self.broker_adapter.get_open_positions(account_id=target_account)
            result = self._adapter_positions_to_ui_dicts(positions)
            logger.debug("Found %d open positions for account %s", len(result), target_account)
            return result
            
        except Exception as e:
            logger.error(f"Failed to fetch positions: {str(e)}")
            return []

    async def get_positions(self, account_id: str = None) -> List[Dict]:
        """Alias for :meth:`get_open_positions` (overnight breakeven + legacy call sites)."""
        return await self.get_open_positions(account_id=account_id)

    @staticmethod
    def _position_symbol_matches(pos_sym: str, want: str) -> bool:
        ps = (pos_sym or "").upper().strip()
        w = (want or "").upper().strip()
        if not w:
            return False
        if ps == w:
            return True
        if ps.endswith("." + w):
            return True
        # e.g. MNQM6, CON.F.US.MNQU5 — root symbol prefix
        return len(ps) > len(w) and ps.startswith(w)

    def _start_generic_breakeven_monitor_if_needed(self) -> None:
        if self._generic_breakeven_task is not None and not self._generic_breakeven_task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.warning("generic breakeven: no running event loop — monitor not started")
            return
        self._generic_breakeven_task = loop.create_task(self._generic_breakeven_monitor_loop())

    def register_generic_breakeven_watch(
        self,
        order_id: str,
        *,
        symbol: str,
        side: str,
        entry_price: float,
        profit_threshold: float,
        strategy_name: str,
        breakeven_offset: float = 0.0,
    ) -> None:
        """Arm broker stop → entry+offset after ``profit_threshold`` price move in favour (BONGO §1B).

        ``breakeven_offset`` (price pts, clamped ≥ 0) shifts the moved stop in the
        trade's favour so a triggered breakeven exit can end slightly green instead
        of flat (covers commission + slippage). Default 0 = snap exactly to entry.
        """
        if not order_id or profit_threshold is None or float(profit_threshold) <= 0:
            return
        try:
            be_offset = max(0.0, float(breakeven_offset or 0.0))
        except (TypeError, ValueError):
            be_offset = 0.0
        su = str(side).upper()
        row = {
            "symbol": str(symbol).upper().strip(),
            "side": "LONG" if su in ("BUY", "LONG") else "SHORT",
            "entry_price": float(entry_price),
            "profit_threshold": float(profit_threshold),
            "breakeven_offset": be_offset,
            "breakeven_triggered": False,
            "position_filled": False,
            "strategy_name": str(strategy_name),
        }
        self._generic_breakeven_monitoring[str(order_id)] = row
        self._start_generic_breakeven_monitor_if_needed()
        logger.info(
            "generic breakeven: armed order=%s sym=%s strat=%s thr=%.4f offset=%.4f (entry=%.4f)",
            order_id,
            row["symbol"],
            strategy_name,
            float(profit_threshold),
            be_offset,
            float(entry_price),
        )

    async def _generic_breakeven_monitor_loop(self) -> None:
        """Poll open positions; when unrealized move ≥ threshold, tighten SL toward entry."""
        logger.info("🔄 Generic breakeven monitor loop started (R-threshold path)")
        while True:
            try:
                await asyncio.sleep(10)
                if not self._generic_breakeven_monitoring:
                    continue

                positions = await self.get_open_positions()
                acct = getattr(self, "selected_account", None)
                account_id = acct.get("id") if isinstance(acct, dict) else None

                for oid, monitor_data in list(self._generic_breakeven_monitoring.items()):
                    if monitor_data.get("breakeven_triggered"):
                        del self._generic_breakeven_monitoring[oid]
                        continue

                    sym_want = monitor_data["symbol"]
                    side = monitor_data["side"]
                    entry_price = float(monitor_data["entry_price"])
                    thr = float(monitor_data["profit_threshold"])
                    be_offset = max(0.0, float(monitor_data.get("breakeven_offset") or 0.0))

                    position = None
                    if positions:
                        for p in positions:
                            ps = p.get("symbol") or p.get("contractName") or ""
                            qty = p.get("quantity") or p.get("size") or 0
                            if abs(float(qty or 0)) <= 0:
                                continue
                            if self._position_symbol_matches(str(ps), sym_want):
                                position = p
                                break

                    if not position:
                        if monitor_data.get("position_filled"):
                            logger.info(
                                "generic breakeven: position gone for %s — removing watch %s",
                                sym_want,
                                oid,
                            )
                            del self._generic_breakeven_monitoring[oid]
                        continue

                    if not monitor_data.get("position_filled"):
                        monitor_data["position_filled"] = True
                        logger.info(
                            "generic breakeven: %s %s opened @ %.4f (watch order=%s strat=%s)",
                            sym_want,
                            side,
                            entry_price,
                            oid,
                            monitor_data.get("strategy_name", ""),
                        )

                    cur = position.get("currentPrice") or position.get("current_price") or position.get("lastPrice")
                    if cur is None:
                        continue
                    try:
                        cur_f = float(cur)
                    except (TypeError, ValueError):
                        continue

                    if side == "LONG":
                        profit_move = cur_f - entry_price
                    else:
                        profit_move = entry_price - cur_f

                    if profit_move < thr:
                        continue

                    monitor_data["breakeven_triggered"] = True
                    # New stop snaps to entry ± offset (offset is in the trade's favour).
                    if side == "LONG":
                        new_stop = entry_price + be_offset
                    else:
                        new_stop = entry_price - be_offset
                    logger.info(
                        "generic breakeven: %s %s move=%.4f ≥ thr=%.4f — moving stop to %.4f (entry=%.4f offset=%.4f)",
                        sym_want,
                        side,
                        profit_move,
                        thr,
                        new_stop,
                        entry_price,
                        be_offset,
                    )
                    position_id = position.get("id") or position.get("positionId") or position.get("position_id")
                    if account_id and position_id:
                        try:
                            res = await self.modify_stop_loss(
                                str(position_id),
                                float(new_stop),
                                account_id=str(account_id),
                            )
                            if isinstance(res, dict) and res.get("error"):
                                logger.warning(
                                    "generic breakeven: modify_stop_loss failed %s: %s",
                                    sym_want,
                                    res.get("error"),
                                )
                        except Exception as exc:
                            logger.warning(
                                "generic breakeven: modify_stop_loss error %s: %s",
                                sym_want,
                                exc,
                                exc_info=True,
                            )
                    else:
                        logger.debug(
                            "generic breakeven: missing account_id or position_id for %s",
                            sym_want,
                        )

            except asyncio.CancelledError:
                logger.info("Generic breakeven monitor loop cancelled")
                raise
            except Exception as e:
                logger.error("generic breakeven monitor error: %s", e, exc_info=True)
                await asyncio.sleep(60)

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

    async def create_bracket_order_improved(
        self,
        symbol: str,
        side: str,
        quantity: int,
        entry_stop_price: float,
        stop_loss_price: float,
        take_profit_price: float,
        account_id: str = None,
        strategy_name: Optional[str] = None,
    ) -> Dict:
        from core import bracket_orders as _bo

        return await _bo.create_bracket_order_improved(
            self,
            symbol,
            side,
            quantity,
            entry_stop_price,
            stop_loss_price,
            take_profit_price,
            account_id,
            strategy_name,
        )

    async def create_bracket_order(
        self,
        symbol: str,
        side: str,
        quantity: int,
        stop_loss_price: float = None,
        take_profit_price: float = None,
        stop_loss_ticks: int = None,
        take_profit_ticks: int = None,
        account_id: str = None,
        strategy_name: str = None,
    ) -> Dict:
        from core import bracket_orders as _bo

        return await _bo.create_bracket_order(
            self,
            symbol,
            side,
            quantity,
            stop_loss_price,
            take_profit_price,
            stop_loss_ticks,
            take_profit_ticks,
            account_id,
            strategy_name,
        )

    async def create_partial_tp_bracket_order(
        self,
        symbol: str,
        side: str,
        quantity: int,
        stop_loss_price: float = None,
        take_profit_1_price: float = None,
        take_profit_2_price: float = None,
        tp1_quantity: int = None,
        account_id: str = None,
    ) -> Dict:
        from core import bracket_orders as _bo

        return await _bo.create_partial_tp_bracket_order(
            self,
            symbol,
            side,
            quantity,
            stop_loss_price,
            take_profit_1_price,
            take_profit_2_price,
            tp1_quantity,
            account_id,
        )

    async def monitor_all_bracket_positions(self, account_id: str = None) -> Dict:
        from core import bracket_orders as _bo

        return await _bo.monitor_all_bracket_positions(self, account_id)

    async def get_linked_orders(self, position_id: str, account_id: str = None) -> List[Dict]:
        from core import bracket_orders as _bo

        return await _bo.get_linked_orders(self, position_id, account_id)

    async def adjust_bracket_orders(
        self, position_id: str, new_quantity: int, account_id: str = None
    ) -> Dict:
        from core import bracket_orders as _bo

        return await _bo.adjust_bracket_orders(self, position_id, new_quantity, account_id)

    async def monitor_position_changes(self, account_id: str = None) -> Dict:
        from core import bracket_orders as _bo

        return await _bo.monitor_position_changes(self, account_id)


    # ============================================================================
    # NATIVE TOPSTEPX API METHODS - ADVANCED ORDER TYPES
    # ============================================================================
    
    async def place_stop_order(self, symbol: str, side: str, quantity: int, stop_price: float,
                              account_id: str = None, strategy_name: Optional[str] = None,
                              custom_tag: Optional[str] = None) -> Dict:
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
            custom_tag: Optional explicit customTag (overrides generated tag; max 64 chars)
            
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
            
            tag = str(custom_tag).strip()[:64] if custom_tag else self._generate_unique_custom_tag(
                "stop_entry", strategy_name
            )
            # Prepare stop order data (type 4 = Stop order)
            stop_data = {
                "accountId": int(target_account),
                "contractId": contract_id,
                "type": 4,  # Stop order type (triggers market order when price is hit)
                "side": side_value,
                "size": quantity,
                "stopPrice": rounded_stop_price,
                "customTag": tag,
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

        Uses TopStepXAdapter for native OCO brackets when the account has Auto OCO
        enabled. Accounts on **Position Brackets** reject those payloads (Code 2).

        Default on that reject: one-shot Discord + log alert telling you to enable
        **Auto OCO Brackets** in ProjectX, and refuse the place (no silent hybrid).
        Opt-in hybrid only via ``TOPSTEPX_BRACKET_MODE=position|hybrid`` (smoke/debug).
        """
        try:
            target_account = account_id or (self.selected_account['id'] if self.selected_account else None)

            if not target_account:
                return {"error": "No account selected"}

            if side.upper() not in ["BUY", "SELL"]:
                return {"error": "Side must be 'BUY' or 'SELL'"}

            # Opt-in hybrid only (env). Default is native Auto OCO.
            if self._should_prefer_hybrid_brackets():
                logger.info(
                    "TOPSTEPX_BRACKET_MODE hybrid/position — placing stop-entry without "
                    "native OCO brackets (%s %s %s @ %.2f)",
                    side, quantity, symbol, entry_price,
                )
                await self._maybe_alert_position_brackets_mode(
                    source="prefer_hybrid_env",
                    detail=(
                        f"TOPSTEPX_BRACKET_MODE forces hybrid "
                        f"({side} {quantity} {symbol} @ {entry_price:.2f}). "
                        f"Enable Auto OCO Brackets in ProjectX for native linked brackets."
                    ),
                    symbol=symbol,
                    side=side,
                    strategy_name=strategy_name,
                )
                return await self._run_hybrid_bracket_fallback(
                    symbol=symbol,
                    side=side,
                    quantity=quantity,
                    entry_price=entry_price,
                    stop_loss_price=stop_loss_price,
                    take_profit_price=take_profit_price,
                    account_id=target_account,
                    enable_breakeven=enable_breakeven,
                    strategy_name=strategy_name,
                    prior_error=None,
                )

            # ── Data-feed health gate (bot-level chokepoint) ──────────────────
            try:
                from core.data_feed_health import (
                    resolve_health_gate_decision,
                    resolve_health_gate_mode,
                )
                gate_mode = resolve_health_gate_mode()
                if gate_mode != "off":
                    monitor = getattr(self, "data_feed_monitor", None)
                    if monitor is not None:
                        decision, reason = resolve_health_gate_decision(monitor, gate_mode)
                        if decision == "refuse":
                            logger.error(
                                "place_oco_bracket_with_stop_entry refused by data-feed "
                                "health gate (%s %s): %s",
                                side, symbol, reason,
                            )
                            return {
                                "error": f"data feed unhealthy: {reason}",
                                "orderId": None,
                                "method": "gated_health",
                            }
                        if decision == "warn":
                            logger.warning(
                                "⚠️  place_oco_bracket_with_stop_entry: data feed degraded "
                                "(%s %s) — placing anyway: %s",
                                side, symbol, reason,
                            )
            except Exception as _hg_exc:
                logger.warning(
                    "bot-level health gate raised %s — proceeding",
                    type(_hg_exc).__name__,
                )

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

            if result.success:
                method = "unknown"
                if isinstance(result.raw_response, dict) and result.raw_response.get("_execution_path"):
                    method = str(result.raw_response.get("_execution_path"))
                elif isinstance(result.message, str):
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

            error_msg = result.error or "Unknown error"
            try:
                self._last_bracket_error = error_msg
            except Exception:
                logger.debug("Could not set _last_bracket_error", exc_info=True)

            logger.error(f"Stop bracket order failed: {error_msg}")
            err_lower = str(error_msg).lower()

            if self._is_position_brackets_mode_error(error_msg):
                # Account is on Position Brackets (Auto OCO off). Notify + refuse.
                # Hybrid is unreliable (orphan legs); do not silently fall back.
                logger.error(
                    "❌ Auto OCO Brackets not enabled on this account (Position Brackets). "
                    "Enable Auto OCO Brackets in ProjectX/TopStepX account settings. "
                    "Order not placed. (Opt-in hybrid only: TOPSTEPX_BRACKET_MODE=hybrid)"
                )
                await self._maybe_alert_position_brackets_mode(
                    source="broker_reject_auto_oco_disabled",
                    detail=str(error_msg),
                    symbol=symbol,
                    side=side,
                    strategy_name=strategy_name,
                )
                return {
                    "success": False,
                    "error": (
                        "Auto OCO Brackets not enabled on this account. "
                        "Enable Auto OCO Brackets in ProjectX (not Position Brackets), "
                        "then retry. Discord alert sent."
                    ),
                    "errorCode": 2,
                    "method": "refused_position_brackets",
                    "broker_error": error_msg,
                    "action_required": "enable_auto_oco_brackets",
                }

            if (
                "http 500" in err_lower
                or "internal server error" in err_lower
                or ("500" in err_lower and "error" in err_lower)
            ):
                logger.warning(
                    "⚠️ OCO bracket failed with server error; attempting hybrid "
                    "stop-entry + post-fill bracket fallback"
                )
                return await self._run_hybrid_bracket_fallback(
                    symbol=symbol,
                    side=side,
                    quantity=quantity,
                    entry_price=entry_price,
                    stop_loss_price=stop_loss_price,
                    take_profit_price=take_profit_price,
                    account_id=target_account,
                    enable_breakeven=enable_breakeven,
                    strategy_name=strategy_name,
                    prior_error=error_msg,
                )

            return {"success": False, "error": error_msg}

        except Exception as e:
            logger.error(f"Failed to place OCO bracket with stop entry: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            return {"success": False, "error": str(e)}

    @staticmethod
    def _is_position_brackets_mode_error(error_msg: object) -> bool:
        """True when TopStepX rejects native OCO brackets (Position Brackets account mode)."""
        err_lower = str(error_msg or "").lower()
        return (
            "brackets cannot be used with position brackets" in err_lower
            or ("you must enable auto oco brackets" in err_lower)
            or ("auto oco brackets" in err_lower and "not enabled" in err_lower)
            or ("error code 2" in err_lower and "auto oco brackets" in err_lower)
            or ("code: 2" in err_lower and "auto oco brackets" in err_lower)
        )

    def _should_prefer_hybrid_brackets(self) -> bool:
        """True only when ``TOPSTEPX_BRACKET_MODE=position|hybrid`` (explicit opt-in).

        Default (unset): native Auto OCO only. On Position Brackets reject we Discord
        alert and refuse — we do not sticky-prefer hybrid after a broker reject.
        """
        mode = str(os.getenv("TOPSTEPX_BRACKET_MODE", "") or "").strip().lower()
        if mode in ("position", "hybrid", "position_brackets", "pos"):
            return True
        if mode in ("auto_oco", "oco", "native"):
            return False
        return False

    async def _maybe_alert_position_brackets_mode(
        self,
        *,
        source: str,
        detail: str = "",
        symbol: str = "",
        side: str = "",
        strategy_name=None,
    ) -> None:
        """One-shot log + Discord: Auto OCO Brackets not enabled — action required."""
        if getattr(self, "_position_brackets_alerted", False):
            return
        self._position_brackets_alerted = True
        acct = getattr(self, "selected_account", None) or {}
        acct_name = acct.get("name") or acct.get("id") or "unknown"
        strat = strategy_name or "n/a"
        lines = [
            f"account={acct_name}",
            f"source={source}",
            f"strategy={strat}",
            f"symbol={symbol or 'n/a'} side={side or 'n/a'}",
            "action=Enable Auto OCO Brackets in ProjectX (disable Position Brackets)",
            "orders=refused until Auto OCO is enabled (unless TOPSTEPX_BRACKET_MODE=hybrid)",
        ]
        if detail:
            lines.append(f"detail={detail[:400]}")
        msg = " | ".join(lines)
        logger.warning("BRACKET_MODE_ALERT %s", msg)
        notifier = getattr(self, "discord_notifier", None)
        if notifier is None or not getattr(notifier, "enabled", False):
            return
        try:
            await notifier.send_bracket_mode_notification(
                account_name=str(acct_name),
                source=source,
                detail=detail or msg,
                symbol=symbol,
                strategy_name=str(strat),
            )
        except Exception:
            logger.debug("Discord bracket-mode alert failed", exc_info=True)

    async def _run_hybrid_bracket_fallback(
        self,
        *,
        symbol: str,
        side: str,
        quantity: int,
        entry_price: float,
        stop_loss_price: float,
        take_profit_price: float,
        account_id: str,
        enable_breakeven: bool,
        strategy_name,
        prior_error,
    ) -> Dict:
        hybrid = await self._stop_bracket_hybrid(
            symbol=symbol,
            side=side,
            quantity=quantity,
            entry_price=entry_price,
            stop_loss_price=stop_loss_price,
            take_profit_price=take_profit_price,
            account_id=account_id,
            enable_breakeven=enable_breakeven,
            strategy_name=strategy_name,
        )
        if isinstance(hybrid, dict) and "error" not in hybrid and (
            hybrid.get("success") or hybrid.get("orderId")
        ):
            if "method" not in hybrid:
                hybrid["method"] = "hybrid_auto_bracket"
            hybrid.setdefault("success", True)
            return hybrid
        hybrid_err = hybrid.get("error") if isinstance(hybrid, dict) else str(hybrid)
        if prior_error:
            return {
                "success": False,
                "error": f"{prior_error} | Hybrid fallback failed: {hybrid_err}",
            }
        return {"success": False, "error": f"Hybrid bracket failed: {hybrid_err}"}

    async def place_oco_bracket_with_stop_entry_partial_tp(
        self,
        symbol: str,
        side: str,
        quantity: int,
        entry_price: float,
        stop_loss_price: float,
        take_profit_full_price: float,
        account_id: str = None,
        *,
        scalp_r_multiple: float = 1.0,
        enable_breakeven: bool = False,
        strategy_name: str = None,
    ) -> Dict:
        """BONGO §1A — stop entry + partial take-profit at ``scalp_r_multiple`` R + runner TP.

        ``StrategyReplayEngine`` intercepts this on the mock bot and simulates a two-stage
        OCO. Live placement uses ``TopStepXAdapter.place_oco_bracket_stop_entry_partial_tp_v1``
        (dual native stop-entry brackets; composite ``orderId``).
        """
        try:
            from core.bracket_orders import build_partial_tp_stop_entry_plan

            if int(quantity) < 2:
                return {"success": False, "error": "partial_tp_requires_quantity_ge_2", "orderId": None}
            target_account = account_id or (self.selected_account["id"] if self.selected_account else None)
            if not target_account:
                return {"error": "No account selected"}
            if side.upper() not in ("BUY", "SELL"):
                return {"error": "Side must be 'BUY' or 'SELL'"}

            plan = build_partial_tp_stop_entry_plan(
                symbol=symbol,
                side=side,
                quantity=int(quantity),
                entry_stop_price=float(entry_price),
                stop_loss_price=float(stop_loss_price),
                take_profit_full_price=float(take_profit_full_price),
                scalp_r_multiple=float(scalp_r_multiple or 1.0),
            )
            result = await self.broker_adapter.place_oco_bracket_stop_entry_partial_tp_v1(
                symbol=symbol,
                side=side,
                quantity=int(quantity),
                entry_price=float(entry_price),
                stop_loss_price=float(stop_loss_price),
                take_profit_full_price=float(take_profit_full_price),
                scalp_r_multiple=float(scalp_r_multiple or 1.0),
                account_id=target_account,
                enable_breakeven=enable_breakeven,
                strategy_name=strategy_name,
            )
            if result.success:
                method = "unknown"
                if isinstance(result.raw_response, dict) and result.raw_response.get("_execution_path"):
                    method = str(result.raw_response.get("_execution_path"))
                return {
                    "success": True,
                    "orderId": result.order_id,
                    "message": result.message,
                    "method": method,
                    **({"raw_response": result.raw_response} if result.raw_response else {}),
                }
            return {
                "success": False,
                "error": result.error or "partial_tp_adapter_failed",
                "orderId": None,
                **(
                    {"raw_response": result.raw_response}
                    if getattr(result, "raw_response", None)
                    else {}
                ),
            }
        except Exception as e:
            logger.error("place_oco_bracket_with_stop_entry_partial_tp failed: %s", e, exc_info=True)
            return {"success": False, "error": str(e), "orderId": None}
    
    async def _stop_bracket_hybrid(self, symbol: str, side: str, quantity: int,
                                  entry_price: float, stop_loss_price: float,
                                  take_profit_price: float, account_id: str = None,
                                  enable_breakeven: bool = False, strategy_name: Optional[str] = None) -> Dict:
        """
        Hybrid stop bracket: place stop order for entry, then attach SL/TP on fill.

        Fallback when the account is on Position Brackets (native OCO
        ``stopLossBracket`` / ``takeProfitBracket`` payloads are rejected).

        Attach is durable: pending registry + strong-referenced monitor task +
        User Hub fill hook (``try_attach_hybrid_brackets_on_fill``).
        """
        try:
            logger.info("Using hybrid approach: stop order + auto-bracket")

            stop_result = await self.place_stop_order(
                symbol=symbol,
                side=side,
                quantity=quantity,
                stop_price=entry_price,
                account_id=account_id,
                strategy_name=strategy_name,
            )

            if "error" in stop_result:
                return {"error": f"Stop order failed: {stop_result['error']}"}

            order_id = (
                stop_result.get("orderId")
                or stop_result.get("order_id")
                or stop_result.get("id")
            )
            if not order_id:
                return {"error": f"Stop order placed but no orderId in response: {stop_result}"}
            order_id = str(order_id)
            logger.info("Hybrid stop entry placed: %s", order_id)

            self.register_hybrid_pending_bracket(
                order_id=order_id,
                symbol=symbol,
                side=side,
                quantity=quantity,
                stop_loss_price=stop_loss_price,
                take_profit_price=take_profit_price,
                account_id=account_id,
                strategy_name=strategy_name,
                entry_price=entry_price,
            )
            self._start_hybrid_bracket_monitor(order_id)

            if enable_breakeven and order_id and hasattr(self, "overnight_strategy"):
                logger.info("Setting up breakeven monitoring for hybrid order %s", order_id)
                breakeven_points = float(os.getenv("MANUAL_BREAKEVEN_PROFIT_POINTS", "15.0"))
                self.overnight_strategy.breakeven_monitoring[order_id] = {
                    "symbol": symbol,
                    "side": "LONG" if side.upper() == "BUY" else "SHORT",
                    "entry_price": entry_price,
                    "original_stop": stop_loss_price,
                    "breakeven_threshold": breakeven_points,
                    "breakeven_triggered": False,
                    "is_filled": False,
                }

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
                "message": "Stop order placed, will auto-bracket on fill",
            }

        except Exception as e:
            logger.error("Hybrid bracket failed: %s", e, exc_info=True)
            return {"error": str(e)}

    def register_hybrid_pending_bracket(
        self,
        *,
        order_id: str,
        symbol: str,
        side: str,
        quantity: int,
        stop_loss_price: float,
        take_profit_price: float,
        account_id: str = None,
        strategy_name: Optional[str] = None,
        entry_price: float = None,
    ) -> None:
        """Remember SL/TP to attach after ``order_id`` fills (Position Brackets path)."""
        oid = str(order_id)
        pending = {
            "order_id": oid,
            "symbol": str(symbol).upper(),
            "side": str(side).upper(),
            "quantity": int(quantity),
            "stop_loss_price": float(stop_loss_price),
            "take_profit_price": float(take_profit_price),
            "account_id": str(account_id) if account_id is not None else None,
            "strategy_name": strategy_name,
            "entry_price": float(entry_price) if entry_price is not None else None,
            "attached": False,
            "attach_error": None,
        }
        if not hasattr(self, "_hybrid_pending_brackets") or self._hybrid_pending_brackets is None:
            self._hybrid_pending_brackets = {}
        self._hybrid_pending_brackets[oid] = pending
        logger.info(
            "Hybrid pending registered order=%s %s %s qty=%s SL=%.2f TP=%.2f",
            oid,
            pending["side"],
            pending["symbol"],
            pending["quantity"],
            pending["stop_loss_price"],
            pending["take_profit_price"],
        )

    def _start_hybrid_bracket_monitor(self, order_id: str) -> None:
        """Strong-referenced poller until attach succeeds, order dies, or timeout."""
        oid = str(order_id)
        if not hasattr(self, "_hybrid_bracket_tasks") or self._hybrid_bracket_tasks is None:
            self._hybrid_bracket_tasks = {}
        existing = self._hybrid_bracket_tasks.get(oid)
        if existing is not None and not existing.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.error("Hybrid monitor: no running event loop for order %s", oid)
            return

        async def _monitor():
            max_wait_time = int(os.getenv("HYBRID_BRACKET_MONITOR_MAX_S", "14400") or 14400)
            check_interval = float(os.getenv("HYBRID_BRACKET_POLL_S", "1.0") or 1.0)
            elapsed = 0.0
            while elapsed < max_wait_time:
                pending = (getattr(self, "_hybrid_pending_brackets", None) or {}).get(oid)
                if not pending:
                    return
                if pending.get("attached"):
                    return
                try:
                    result = await self.try_attach_hybrid_brackets_on_fill(
                        oid, reason="poll"
                    )
                    if result.get("attached") or result.get("done"):
                        return
                    if result.get("abort"):
                        return
                except Exception:
                    logger.error(
                        "Hybrid monitor tick failed for %s", oid, exc_info=True
                    )
                await asyncio.sleep(check_interval)
                elapsed += check_interval
            logger.warning(
                "Hybrid bracket monitor timed out after %ss for order %s — "
                "position may still be naked",
                max_wait_time,
                oid,
            )

        task = loop.create_task(_monitor(), name=f"hybrid_bracket_{oid}")
        self._hybrid_bracket_tasks[oid] = task

        def _cleanup(t, _oid=oid):
            try:
                tasks = getattr(self, "_hybrid_bracket_tasks", None) or {}
                if tasks.get(_oid) is t:
                    tasks.pop(_oid, None)
            except Exception:
                pass

        task.add_done_callback(_cleanup)

    async def try_attach_hybrid_brackets_on_fill(
        self,
        order_id: str,
        *,
        reason: str = "unknown",
        order_status=None,
    ) -> Dict[str, Any]:
        """Attach SL/TP if ``order_id`` is a pending hybrid entry that has filled.

        Safe to call from poller and User Hub. Idempotent.
        """
        oid = str(order_id)
        pending_map = getattr(self, "_hybrid_pending_brackets", None) or {}
        pending = pending_map.get(oid)
        if not pending:
            return {"done": True, "skipped": True}
        if pending.get("attached"):
            return {"done": True, "attached": True}

        locks = getattr(self, "_hybrid_attach_locks", None)
        if locks is None:
            self._hybrid_attach_locks = {}
            locks = self._hybrid_attach_locks
        lock = locks.setdefault(oid, asyncio.Lock())

        async with lock:
            pending = (getattr(self, "_hybrid_pending_brackets", None) or {}).get(oid)
            if not pending or pending.get("attached"):
                return {"done": True, "attached": bool(pending and pending.get("attached"))}

            account_id = pending.get("account_id")
            status = order_status

            def _is_filled(st) -> bool:
                return st in (2, "2", "Filled", "filled", "FILLED")

            def _is_dead(st) -> bool:
                return st in (
                    3, 4, 5, 6,
                    "3", "4", "5", "6",
                    "Cancelled", "Canceled", "Rejected",
                    "cancelled", "canceled", "rejected",
                )

            # Still working?
            still_working = False
            try:
                orders = await self.get_open_orders(account_id=account_id)
                if isinstance(orders, list):
                    for order in orders:
                        if str(order.get("id") or order.get("orderId") or "") == oid:
                            still_working = True
                            status = order.get("status", status)
                            break
            except Exception:
                logger.debug("Hybrid open-orders check failed for %s", oid, exc_info=True)

            if still_working and not _is_filled(status):
                if _is_dead(status):
                    logger.warning(
                        "Hybrid entry %s terminal while still listed status=%s — abort (%s)",
                        oid, status, reason,
                    )
                    pending_map.pop(oid, None)
                    return {"done": True, "abort": True, "status": status}
                return {"done": False, "waiting": True, "status": status}

            # Confirm fill via history if not already known filled
            if not _is_filled(status):
                try:
                    recent = await self.get_order_history(account_id=account_id, limit=40)
                    if isinstance(recent, list):
                        for order in recent:
                            if str(order.get("id") or order.get("orderId") or "") == oid:
                                status = order.get("status", status)
                                break
                except Exception:
                    logger.debug("Hybrid history check failed for %s", oid, exc_info=True)

            if _is_dead(status) and not _is_filled(status):
                logger.warning(
                    "Hybrid entry %s dead status=%s — abort attach (%s)",
                    oid, status, reason,
                )
                pending_map.pop(oid, None)
                return {"done": True, "abort": True, "status": status}

            # Need a position (brief retry — hub can lag REST)
            position_id = None
            for attempt in range(8):
                position_id = await self._find_hybrid_position_id(pending)
                if position_id:
                    break
                # If order still working, not filled yet
                if still_working and attempt == 0:
                    return {"done": False, "waiting": True}
                await asyncio.sleep(0.4 + 0.2 * attempt)

            if not position_id:
                # Order gone + no position + not confirmed filled → keep waiting
                # (cancel race) unless history says filled.
                if not _is_filled(status) and not still_working:
                    logger.info(
                        "Hybrid: order %s not working, no position yet (reason=%s status=%s)",
                        oid, reason, status,
                    )
                    return {"done": False, "waiting": True}
                if _is_filled(status):
                    logger.error(
                        "Hybrid: order %s filled but no open position for %s — cannot attach",
                        oid, pending.get("symbol"),
                    )
                    pending["attach_error"] = "filled_but_no_position"
                    return {"done": True, "abort": True, "error": "filled_but_no_position"}
                return {"done": False, "waiting": True}

            logger.info(
                "Hybrid: attaching protective SL/TP on position %s for entry %s (reason=%s)",
                position_id, oid, reason,
            )
            attach = await self._attach_hybrid_protective_orders(
                pending, position_id=str(position_id)
            )
            pending["attached"] = bool(attach.get("success"))
            pending["attach_result"] = attach
            if attach.get("success"):
                logger.info(
                    "Hybrid brackets attached for %s: SL=%s TP=%s",
                    oid,
                    attach.get("sl_order_id"),
                    attach.get("tp_order_id"),
                )
                pending_map.pop(oid, None)
                return {"done": True, "attached": True, **attach}

            pending["attach_error"] = attach.get("error") or attach
            logger.error("Hybrid attach failed for %s: %s", oid, attach)
            # Keep pending so poller can retry a few times
            return {"done": False, "attached": False, **attach}

    async def _find_hybrid_position_id(self, pending: Dict[str, Any]):
        """Resolve open position id for a pending hybrid entry (contract/symbol)."""
        account_id = pending.get("account_id")
        symbol = str(pending.get("symbol") or "").upper()
        want_side = str(pending.get("side") or "").upper()
        try:
            contract_id = self._get_contract_id(symbol)
        except Exception:
            contract_id = None

        positions = await self.get_open_positions(account_id=account_id)
        if isinstance(positions, dict) and positions.get("error"):
            return None
        for pos in positions or []:
            pos_sym = str(
                pos.get("symbol") or pos.get("contractSymbol") or ""
            ).upper()
            pos_cid = pos.get("contractId") or pos.get("contract_id")
            if contract_id and pos_cid and str(pos_cid) == str(contract_id):
                pass  # match
            elif self._position_symbol_matches(pos_sym, symbol):
                pass
            elif symbol and symbol in str(pos_cid or "").upper():
                pass
            else:
                continue
            # Prefer matching side when multiple (rare)
            pos_side = pos.get("side")
            if want_side in ("BUY", "LONG") and pos_side in (1, "1", "SHORT", "Sell"):
                continue
            if want_side in ("SELL", "SHORT") and pos_side in (0, "0", "LONG", "Buy"):
                continue
            return pos.get("id") or pos.get("position_id") or pos.get("positionId")
        return None

    async def _attach_hybrid_protective_orders(
        self, pending: Dict[str, Any], *, position_id: str
    ) -> Dict[str, Any]:
        """Place tagged protective stop + limit and arm software OCO + orphan sweeper.

        Always places our own tagged legs (``TB-hyb-{gid}-sl`` / ``-tp``) so a
        continuous sweeper can cancel orphans when the position is gone — native
        Auto OCO is unavailable on Position Brackets accounts.
        """
        import uuid

        account_id = pending.get("account_id")
        symbol = pending["symbol"]
        quantity = int(pending["quantity"])
        stop_loss_price = float(pending["stop_loss_price"])
        take_profit_price = float(pending["take_profit_price"])
        strategy_name = pending.get("strategy_name") or "hybrid"
        entry_side = str(pending.get("side") or "").upper()
        exit_side = "SELL" if entry_side in ("BUY", "LONG") else "BUY"
        group_id = str(uuid.uuid4()).replace("-", "")[:8]
        sl_tag = self.hybrid_protective_tag(group_id, "sl")
        tp_tag = self.hybrid_protective_tag(group_id, "tp")

        logger.info(
            "Hybrid: placing tagged protective legs group=%s pos=%s SL=%.2f TP=%.2f tags=%s/%s",
            group_id, position_id, stop_loss_price, take_profit_price, sl_tag, tp_tag,
        )

        sl_result = await self.place_stop_order(
            symbol=symbol,
            side=exit_side,
            quantity=quantity,
            stop_price=stop_loss_price,
            account_id=account_id,
            strategy_name=f"{str(strategy_name)[:10]}-hyb",
            custom_tag=sl_tag,
        )
        tp_result = await self.place_market_order(
            symbol=symbol,
            side=exit_side,
            quantity=quantity,
            account_id=account_id,
            order_type="limit",
            limit_price=take_profit_price,
            strategy_name=f"{str(strategy_name)[:10]}-hyb",
            custom_tag=tp_tag,
        )

        def _oid(res):
            if not isinstance(res, dict):
                return None
            if res.get("error"):
                return None
            return (
                res.get("order_id")
                or res.get("orderId")
                or res.get("stop_order_id")
                or res.get("tp_order_id")
                or res.get("id")
            )

        sl_ok = _oid(sl_result) is not None or (
            isinstance(sl_result, dict) and sl_result.get("success") and "error" not in sl_result
        )
        tp_ok = _oid(tp_result) is not None or (
            isinstance(tp_result, dict) and tp_result.get("success") and "error" not in tp_result
        )
        sl_id = _oid(sl_result)
        tp_id = _oid(tp_result)

        # Prefer broker book ids by tag (place response may use a different id shape).
        await asyncio.sleep(0.15)
        resolved = await self._resolve_hybrid_legs_by_tag(
            account_id=account_id, group_id=group_id, symbol=symbol
        )
        sl_id = resolved.get("sl_order_id") or sl_id
        tp_id = resolved.get("tp_order_id") or tp_id
        if not sl_id or not tp_id:
            await asyncio.sleep(0.45)
            resolved = await self._resolve_hybrid_legs_by_tag(
                account_id=account_id, group_id=group_id, symbol=symbol
            )
            sl_id = sl_id or resolved.get("sl_order_id")
            tp_id = tp_id or resolved.get("tp_order_id")

        if sl_id and tp_id:
            self.register_hybrid_oco_pair(
                sl_order_id=str(sl_id),
                tp_order_id=str(tp_id),
                position_id=str(position_id),
                account_id=str(account_id) if account_id is not None else None,
                symbol=symbol,
                strategy_name=str(strategy_name),
                group_id=group_id,
                stop_loss_price=stop_loss_price,
                take_profit_price=take_profit_price,
                entry_side=entry_side,
            )
            self._ensure_hybrid_orphan_sweeper()
            # Arm live quotes so price-OCO can cancel peer on touch (not Order/search).
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(
                    self._ensure_hybrid_price_feed(str(symbol).upper()),
                    name=f"hyb_quote_{symbol}",
                )
            except Exception:
                logger.debug("hybrid price feed schedule failed", exc_info=True)
            return {
                "success": True,
                "position_id": position_id,
                "sl_order_id": sl_id,
                "tp_order_id": tp_id,
                "group_id": group_id,
                "sl_tag": sl_tag,
                "tp_tag": tp_tag,
                "sl_result": sl_result,
                "tp_result": tp_result,
            }
        return {
            "success": False,
            "error": f"sl_ok={sl_ok} tp_ok={tp_ok} sl_id={sl_id} tp_id={tp_id}",
            "sl_result": sl_result,
            "tp_result": tp_result,
            "position_id": position_id,
            "group_id": group_id,
        }

    @staticmethod
    def hybrid_protective_tag(group_id: str, leg: str) -> str:
        """``TB-hyb-{gid}-sl`` / ``TB-hyb-{gid}-tp`` (≤64 chars)."""
        gid = str(group_id or "").replace("-", "")[:8] or "x"
        leg_s = "sl" if str(leg).lower().startswith("s") else "tp"
        return f"TB-hyb-{gid}-{leg_s}"[:64]

    @staticmethod
    def is_hybrid_protective_tag(tag: object) -> bool:
        t = str(tag or "")
        return t.startswith("TB-hyb-") and (t.endswith("-sl") or t.endswith("-tp"))

    @staticmethod
    def hybrid_group_from_tag(tag: object):
        t = str(tag or "")
        if not t.startswith("TB-hyb-"):
            return None
        parts = t.split("-")
        # TB hyb gid sl
        if len(parts) >= 4 and parts[1] == "hyb":
            return parts[2]
        return None

    async def _resolve_hybrid_legs_by_tag(
        self, *, account_id, group_id: str, symbol: str
    ) -> Dict[str, Any]:
        sl_tag = self.hybrid_protective_tag(group_id, "sl")
        tp_tag = self.hybrid_protective_tag(group_id, "tp")
        out: Dict[str, Any] = {}
        try:
            orders = await self.get_open_orders(account_id=account_id)
        except Exception:
            return out
        for o in orders or []:
            tag = str(o.get("customTag") or o.get("tag") or "")
            oid = o.get("id") or o.get("orderId")
            if not oid:
                continue
            if tag == sl_tag:
                out["sl_order_id"] = str(oid)
            elif tag == tp_tag:
                out["tp_order_id"] = str(oid)
        return out

    def register_hybrid_oco_pair(
        self,
        *,
        sl_order_id: str,
        tp_order_id: str,
        position_id: str,
        account_id: str = None,
        symbol: str = "",
        strategy_name: str = "hybrid",
        group_id: str = None,
        stop_loss_price: float = None,
        take_profit_price: float = None,
        entry_side: str = None,
    ) -> None:
        """Arm software OCO between hybrid protective stop and take-profit legs.

        Native Auto OCO links legs at the broker; Position Brackets hybrid places
        two independent orders — when one fills/cancels we must cancel the other.
        Tags ``TB-hyb-{group}-sl/tp`` let the orphan sweeper cancel by position.
        Price levels arm near-instant quote-touch cancel (no Order/search lag).
        """
        sl = str(sl_order_id or "").strip()
        tp = str(tp_order_id or "").strip()
        if not sl or not tp or sl == tp:
            logger.warning("register_hybrid_oco_pair skipped — need two distinct ids")
            return
        if not hasattr(self, "_hybrid_oco_legs") or self._hybrid_oco_legs is None:
            self._hybrid_oco_legs = {}
        meta_common = {
            "position_id": str(position_id),
            "account_id": str(account_id) if account_id is not None else None,
            "symbol": str(symbol or "").upper(),
            "strategy_name": str(strategy_name or "hybrid"),
            "pair_key": "|".join(sorted((sl, tp))),
            "group_id": str(group_id) if group_id else None,
            "stop_loss_price": float(stop_loss_price) if stop_loss_price is not None else None,
            "take_profit_price": float(take_profit_price) if take_profit_price is not None else None,
            "entry_side": str(entry_side or "").upper() or None,
        }
        self._hybrid_oco_legs[sl] = {**meta_common, "sibling_id": tp, "leg": "sl"}
        self._hybrid_oco_legs[tp] = {**meta_common, "sibling_id": sl, "leg": "tp"}
        logger.info(
            "Hybrid software OCO armed sl=%s tp=%s position=%s %s group=%s "
            "SL=%.4f TP=%.4f side=%s",
            sl, tp, position_id, symbol, group_id,
            meta_common["stop_loss_price"] or 0.0,
            meta_common["take_profit_price"] or 0.0,
            meta_common["entry_side"],
        )
        # Also register in working-order registry so feed-watchdog cancel-all
        # can cancel both legs (mutual siblings).
        try:
            from core.working_order_registry import get_registry

            reg = get_registry()
            acct = str(account_id) if account_id is not None else ""
            sym = str(symbol or "").upper() or "UNK"
            reg.register(
                order_id=sl,
                account_id=acct,
                symbol=sym,
                side="SL",
                strategy_name=f"{strategy_name}-hybsl",
                oco_sibling_ids=[tp],
            )
            reg.register(
                order_id=tp,
                account_id=acct,
                symbol=sym,
                side="TP",
                strategy_name=f"{strategy_name}-hybtp",
                oco_sibling_ids=[sl],
            )
        except Exception:
            logger.debug("working_order_registry hybrid OCO register failed", exc_info=True)
        self._start_hybrid_oco_monitor(sl, tp)
        self._start_hybrid_price_oco_watch(sl, tp)
        self._ensure_hybrid_orphan_sweeper()

    def _hybrid_oco_pair_key(self, a: str, b: str) -> str:
        return "|".join(sorted((str(a), str(b))))

    def _start_hybrid_oco_monitor(self, sl_order_id: str, tp_order_id: str) -> None:
        """Poll backup: if one leg leaves the book, cancel the sibling."""
        pair_key = self._hybrid_oco_pair_key(sl_order_id, tp_order_id)
        if not hasattr(self, "_hybrid_oco_tasks") or self._hybrid_oco_tasks is None:
            self._hybrid_oco_tasks = {}
        existing = self._hybrid_oco_tasks.get(pair_key)
        if existing is not None and not existing.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.error("Hybrid OCO monitor: no running event loop")
            return

        async def _monitor():
            # Sub-second backup when User Hub is down/lagging; primary path is
            # urgent hub cancel via on_hybrid_protective_leg_terminal.
            max_wait = int(os.getenv("HYBRID_OCO_MONITOR_MAX_S", "28800") or 28800)
            interval = float(os.getenv("HYBRID_OCO_POLL_S", "0.25") or 0.25)
            interval = max(0.1, interval)
            elapsed = 0.0
            while elapsed < max_wait:
                legs = getattr(self, "_hybrid_oco_legs", None) or {}
                if str(sl_order_id) not in legs and str(tp_order_id) not in legs:
                    return
                try:
                    # If either leg is gone from open orders, treat as terminal
                    # and cancel sibling (covers hub-disabled / missed fill events).
                    acct = None
                    for oid in (str(sl_order_id), str(tp_order_id)):
                        meta = legs.get(oid)
                        if meta and meta.get("account_id"):
                            acct = meta.get("account_id")
                            break
                    orders = await self.get_open_orders(account_id=acct)
                    open_ids = set()
                    if isinstance(orders, list):
                        for o in orders:
                            oid = o.get("id") or o.get("orderId")
                            if oid is not None:
                                open_ids.add(str(oid))
                    missing = [
                        oid for oid in (str(sl_order_id), str(tp_order_id))
                        if oid in legs and oid not in open_ids
                    ]
                    if len(missing) == 2:
                        # Both gone (filled/cancelled elsewhere) — just clear.
                        for oid in (str(sl_order_id), str(tp_order_id)):
                            legs.pop(oid, None)
                        return
                    if len(missing) == 1:
                        await self.cancel_hybrid_oco_sibling(
                            missing[0], reason="poll_missing_from_open_orders"
                        )
                        return
                except Exception:
                    logger.debug("Hybrid OCO poll tick failed", exc_info=True)
                await asyncio.sleep(interval)
                elapsed += interval
            logger.warning("Hybrid OCO monitor timed out for pair %s", pair_key)

        task = loop.create_task(_monitor(), name=f"hybrid_oco_{pair_key[:24]}")
        self._hybrid_oco_tasks[pair_key] = task

        def _cleanup(t, _key=pair_key):
            try:
                tasks = getattr(self, "_hybrid_oco_tasks", None) or {}
                if tasks.get(_key) is t:
                    tasks.pop(_key, None)
            except Exception:
                pass

        task.add_done_callback(_cleanup)

    async def _ensure_hybrid_price_feed(self, symbol: str) -> None:
        """Best-effort Market Hub quote sub so price-OCO sees ticks immediately."""
        try:
            await self._ensure_market_socket_started()
        except Exception:
            logger.debug("hybrid price feed: market hub start failed", exc_info=True)
        try:
            await self._ensure_quote_subscription(symbol)
            logger.info("Hybrid price-OCO quote feed armed for %s", symbol)
        except Exception:
            logger.warning(
                "Hybrid price-OCO quote subscribe failed for %s", symbol, exc_info=True
            )

    def _start_hybrid_price_oco_watch(self, sl_order_id: str, tp_order_id: str) -> None:
        """Poll quote cache / REST last so TP/SL touch cancels peer without Order/search."""
        pair_key = self._hybrid_oco_pair_key(sl_order_id, tp_order_id)
        if not hasattr(self, "_hybrid_price_oco_tasks") or self._hybrid_price_oco_tasks is None:
            self._hybrid_price_oco_tasks = {}
        existing = self._hybrid_price_oco_tasks.get(pair_key)
        if existing is not None and not existing.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        async def _watch():
            interval = float(os.getenv("HYBRID_PRICE_OCO_POLL_S", "0.05") or 0.05)
            interval = max(0.02, interval)
            max_wait = int(os.getenv("HYBRID_OCO_MONITOR_MAX_S", "28800") or 28800)
            elapsed = 0.0
            while elapsed < max_wait:
                legs = getattr(self, "_hybrid_oco_legs", None) or {}
                if str(sl_order_id) not in legs and str(tp_order_id) not in legs:
                    return
                meta = legs.get(str(tp_order_id)) or legs.get(str(sl_order_id)) or {}
                sym = str(meta.get("symbol") or "").upper()
                if sym:
                    try:
                        self._hybrid_price_oco_on_quote(sym)
                    except Exception:
                        logger.debug("hybrid price OCO poll tick failed", exc_info=True)
                    try:
                        with self._quote_cache_lock:
                            live = dict(self._quote_cache.get(sym) or {})
                        ts = live.get("ts")
                        stale = True
                        if ts:
                            try:
                                tdt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                                stale = (
                                    datetime.now(timezone.utc) - tdt
                                ).total_seconds() > 1.0
                            except Exception:
                                stale = True
                        if stale or not any(
                            live.get(k) is not None for k in ("last", "bid", "ask")
                        ):
                            q = await self.get_market_quote(sym)
                            if isinstance(q, dict) and not q.get("error"):
                                last = q.get("last")
                                bid = q.get("bid")
                                ask = q.get("ask")
                                self._hybrid_price_oco_evaluate(
                                    sym,
                                    last=float(last) if last is not None else None,
                                    bid=float(bid) if bid is not None else None,
                                    ask=float(ask) if ask is not None else None,
                                )
                    except Exception:
                        logger.debug("hybrid price OCO REST refresh failed", exc_info=True)
                await asyncio.sleep(interval)
                elapsed += interval

        task = loop.create_task(_watch(), name=f"hyb_px_{pair_key[:20]}")
        self._hybrid_price_oco_tasks[pair_key] = task

        def _cleanup(t, _key=pair_key):
            try:
                tasks = getattr(self, "_hybrid_price_oco_tasks", None) or {}
                if tasks.get(_key) is t:
                    tasks.pop(_key, None)
            except Exception:
                pass

        task.add_done_callback(_cleanup)

    def _hybrid_price_oco_on_quote(self, symbol: str) -> None:
        """Sync entry from quote cache update — schedule peer cancel on TP/SL touch."""
        sym = str(symbol or "").upper()
        if not sym:
            return
        legs = getattr(self, "_hybrid_oco_legs", None) or {}
        if not any(str(m.get("symbol") or "").upper() == sym for m in legs.values()):
            return
        with self._quote_cache_lock:
            live = dict(self._quote_cache.get(sym) or {})
        last = live.get("last")
        bid = live.get("bid")
        ask = live.get("ask")
        try:
            last_f = float(last) if last is not None else None
        except (TypeError, ValueError):
            last_f = None
        try:
            bid_f = float(bid) if bid is not None else None
        except (TypeError, ValueError):
            bid_f = None
        try:
            ask_f = float(ask) if ask is not None else None
        except (TypeError, ValueError):
            ask_f = None
        self._hybrid_price_oco_evaluate(sym, last=last_f, bid=bid_f, ask=ask_f)

    def _hybrid_price_oco_evaluate(
        self,
        symbol: str,
        *,
        last: float = None,
        bid: float = None,
        ask: float = None,
    ) -> None:
        """If last/bid/ask touches TP or SL, cancel the *other* leg by known id."""
        sym = str(symbol or "").upper()
        legs = getattr(self, "_hybrid_oco_legs", None) or {}
        seen_pairs = set()
        for oid, meta in list(legs.items()):
            if str(meta.get("symbol") or "").upper() != sym:
                continue
            if meta.get("leg") != "tp":
                continue
            pair_key = meta.get("pair_key") or oid
            if pair_key in seen_pairs:
                continue
            seen_pairs.add(pair_key)
            sl_px = meta.get("stop_loss_price")
            tp_px = meta.get("take_profit_price")
            if sl_px is None or tp_px is None:
                continue
            entry_side = str(meta.get("entry_side") or "").upper()
            is_long = entry_side in ("BUY", "LONG", "")
            tp_hit = False
            sl_hit = False
            if is_long:
                px_tp = bid if bid is not None else last
                px_sl = last if last is not None else (ask if ask is not None else bid)
                if px_tp is not None and float(px_tp) >= float(tp_px):
                    tp_hit = True
                if px_sl is not None and float(px_sl) <= float(sl_px):
                    sl_hit = True
            else:
                px_tp = ask if ask is not None else last
                px_sl = last if last is not None else (bid if bid is not None else ask)
                if px_tp is not None and float(px_tp) <= float(tp_px):
                    tp_hit = True
                if px_sl is not None and float(px_sl) >= float(sl_px):
                    sl_hit = True

            sl_id = str(meta.get("sibling_id") or "")
            tp_id = str(oid)
            if tp_hit and not sl_hit:
                self._hybrid_schedule_peer_cancel(
                    peer_order_id=sl_id,
                    triggered_order_id=tp_id,
                    account_id=meta.get("account_id"),
                    reason="price_tp_touch",
                )
            elif sl_hit and not tp_hit:
                self._hybrid_schedule_peer_cancel(
                    peer_order_id=tp_id,
                    triggered_order_id=sl_id,
                    account_id=meta.get("account_id"),
                    reason="price_sl_touch",
                )
            elif tp_hit and sl_hit:
                self._hybrid_schedule_peer_cancel(
                    peer_order_id=sl_id,
                    triggered_order_id=tp_id,
                    account_id=meta.get("account_id"),
                    reason="price_both_touch",
                )
                self._hybrid_schedule_peer_cancel(
                    peer_order_id=tp_id,
                    triggered_order_id=sl_id,
                    account_id=meta.get("account_id"),
                    reason="price_both_touch",
                )

    def _hybrid_claim_cancel(self, order_id: str) -> bool:
        """Return True if this is the first cancel claim for order_id (dedupe spam)."""
        oid = str(order_id or "").strip()
        if not oid:
            return False
        if not hasattr(self, "_hybrid_cancel_claimed") or self._hybrid_cancel_claimed is None:
            self._hybrid_cancel_claimed = {}
        now = time.monotonic()
        for k, ts in list(self._hybrid_cancel_claimed.items()):
            if now - ts > 120.0:
                self._hybrid_cancel_claimed.pop(k, None)
        if oid in self._hybrid_cancel_claimed:
            return False
        self._hybrid_cancel_claimed[oid] = now
        return True

    def _hybrid_schedule_peer_cancel(
        self,
        *,
        peer_order_id: str,
        triggered_order_id: str,
        account_id: str = None,
        reason: str,
    ) -> None:
        """Fire-and-forget cancel of peer by known id (no Order/search)."""
        peer = str(peer_order_id or "").strip()
        if not peer or not self._hybrid_claim_cancel(peer):
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        async def _do():
            logger.info(
                "Hybrid price-OCO: %s → cancelling peer %s (trigger=%s)",
                reason, peer, triggered_order_id,
            )
            try:
                await self.cancel_order(peer, account_id=account_id)
            except Exception as exc:
                logger.warning("Hybrid price-OCO cancel %s failed: %s", peer, exc)
                (getattr(self, "_hybrid_cancel_claimed", None) or {}).pop(peer, None)
                return
            legs = getattr(self, "_hybrid_oco_legs", None) or {}
            for drop in (peer, str(triggered_order_id or "")):
                if drop:
                    legs.pop(drop, None)
                    try:
                        from core.working_order_registry import get_registry
                        get_registry().unregister(drop)
                    except Exception:
                        pass

        loop.create_task(_do(), name=f"hyb_px_cancel_{peer[:16]}")

    async def on_hybrid_protective_leg_terminal(
        self,
        *,
        order_id: str = None,
        custom_tag: str = None,
        account_id: str = None,
        status=None,
        reason: str = "leg_terminal",
    ) -> Dict[str, Any]:
        """Urgent software-OCO: cancel peer when a hybrid SL/TP leg goes terminal.

        Tries id-registry sibling cancel first, then cancels any remaining open
        orders in the same ``TB-hyb-{gid}-*`` group (covers id mismatch).
        """
        results: Dict[str, Any] = {"reason": reason, "status": status}
        oid = str(order_id or "").strip()
        tag = str(custom_tag or "").strip()

        if oid:
            sib = await self.cancel_hybrid_oco_sibling(
                oid, reason=reason, status=status
            )
            results["sibling_cancel"] = sib
            if sib.get("success") and not sib.get("skipped"):
                # Also clear any residual tagged peers (belt).
                meta_gid = None
                # sibling path already cleared legs; use tag if present
                if self.is_hybrid_protective_tag(tag):
                    meta_gid = self.hybrid_group_from_tag(tag)
                if meta_gid:
                    results["group_cancel"] = await self.cancel_hybrid_group_remaining(
                        meta_gid, account_id=account_id, reason=f"{reason}_group"
                    )
                return results

        if self.is_hybrid_protective_tag(tag):
            gid = self.hybrid_group_from_tag(tag)
            if gid:
                results["group_cancel"] = await self.cancel_hybrid_group_remaining(
                    gid, account_id=account_id, reason=f"{reason}_by_tag"
                )
                return results

        # Tag unknown but order_id might still map after a book lookup
        if oid and not results.get("sibling_cancel"):
            results["skipped"] = True
        return results

    async def cancel_hybrid_group_remaining(
        self,
        group_id: str,
        *,
        account_id: str = None,
        reason: str = "group_cancel",
    ) -> Dict[str, Any]:
        """Cancel every open ``TB-hyb-{group_id}-*`` order (software OCO by tag)."""
        gid = str(group_id or "").strip()
        if not gid:
            return {"skipped": True, "reason": "no_group"}
        target_account = account_id or (
            self.selected_account["id"] if self.selected_account else None
        )
        if not target_account:
            return {"skipped": True, "reason": "no_account"}
        cancelled: List[str] = []
        try:
            orders = await self.get_open_orders(account_id=target_account)
        except Exception as exc:
            return {"error": str(exc)}
        prefix = f"TB-hyb-{gid}-"
        for order in orders or []:
            tag = str(order.get("customTag") or order.get("tag") or "")
            if not tag.startswith(prefix):
                continue
            oid = str(order.get("id") or order.get("orderId") or "")
            if not oid:
                continue
            if not self._hybrid_claim_cancel(oid):
                continue
            try:
                await self.cancel_order(oid, account_id=target_account)
                cancelled.append(oid)
                logger.info(
                    "Hybrid group cancel %s tag=%s reason=%s", oid, tag, reason
                )
            except Exception as exc:
                (getattr(self, "_hybrid_cancel_claimed", None) or {}).pop(oid, None)
                logger.warning("Hybrid group cancel %s failed: %s", oid, exc)
            legs = getattr(self, "_hybrid_oco_legs", None) or {}
            meta = legs.pop(oid, None)
            if meta and meta.get("sibling_id"):
                legs.pop(str(meta["sibling_id"]), None)
            try:
                from core.working_order_registry import get_registry
                get_registry().unregister(oid)
            except Exception:
                pass
        return {"success": True, "group_id": gid, "cancelled": cancelled, "reason": reason}

    async def cancel_hybrid_oco_sibling(
        self,
        order_id: str,
        *,
        reason: str = "unknown",
        status=None,
    ) -> Dict[str, Any]:
        """Cancel the other hybrid protective leg when one goes terminal (fill/cancel).

        Cancels *only* the sibling by known id — never the whole tag group (that
        would kill a still-working TP on price-touch races).
        """
        oid = str(order_id or "").strip()
        legs = getattr(self, "_hybrid_oco_legs", None) or {}
        meta = legs.get(oid)
        if not meta:
            return {"skipped": True}

        locks = getattr(self, "_hybrid_oco_locks", None)
        if locks is None:
            self._hybrid_oco_locks = {}
            locks = self._hybrid_oco_locks
        lock = locks.setdefault(meta.get("pair_key") or oid, asyncio.Lock())

        async with lock:
            legs = getattr(self, "_hybrid_oco_legs", None) or {}
            meta = legs.get(oid)
            if not meta:
                return {"skipped": True, "already_cleared": True}

            sibling_id = str(meta.get("sibling_id") or "")
            account_id = meta.get("account_id")
            logger.info(
                "Hybrid software OCO: leg %s terminal (status=%s reason=%s) — cancelling sibling %s",
                oid, status, reason, sibling_id,
            )
            cancel_result = None
            if sibling_id:
                if self._hybrid_claim_cancel(sibling_id):
                    try:
                        cancel_result = await self.cancel_order(
                            sibling_id, account_id=account_id
                        )
                    except Exception as exc:
                        logger.warning(
                            "Hybrid OCO sibling cancel %s failed: %s", sibling_id, exc
                        )
                        cancel_result = {"error": str(exc)}
                        (getattr(self, "_hybrid_cancel_claimed", None) or {}).pop(
                            sibling_id, None
                        )
                else:
                    cancel_result = {"skipped": True, "already_claimed": True}

            # Drop both legs from software OCO + working-order registry
            for drop_id in (oid, sibling_id):
                if drop_id:
                    legs.pop(str(drop_id), None)
                    try:
                        from core.working_order_registry import get_registry
                        get_registry().unregister(str(drop_id))
                    except Exception:
                        pass

            return {
                "success": True,
                "filled_or_terminal": oid,
                "cancelled_sibling": sibling_id,
                "cancel_result": cancel_result,
                "reason": reason,
            }

    async def cancel_hybrid_oco_for_position(
        self, position_id: str, *, reason: str = "position_flat"
    ) -> Dict[str, Any]:
        """Cancel any remaining hybrid protective legs for a flattened position."""
        pid = str(position_id or "")
        if not pid:
            return {"skipped": True}
        legs = getattr(self, "_hybrid_oco_legs", None) or {}
        targets = [
            oid for oid, meta in list(legs.items())
            if str(meta.get("position_id")) == pid
        ]
        if not targets:
            return {"skipped": True, "position_id": pid}
        # Cancelling one will clear the pair via cancel_hybrid_oco_sibling
        results = []
        for oid in targets:
            if oid in (getattr(self, "_hybrid_oco_legs", None) or {}):
                results.append(
                    await self.cancel_hybrid_oco_sibling(oid, reason=reason)
                )
        return {"success": True, "position_id": pid, "results": results}

    def _ensure_hybrid_orphan_sweeper(self) -> None:
        """Start durable loop that cancels ``TB-hyb-*`` orders when position is gone."""
        task = getattr(self, "_hybrid_orphan_sweeper_task", None)
        if task is not None and not task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.warning("hybrid orphan sweeper: no running event loop")
            return
        self._hybrid_orphan_sweeper_task = loop.create_task(
            self._hybrid_orphan_sweeper_loop(),
            name="hybrid_orphan_sweeper",
        )
        logger.info("Hybrid orphan sweeper started (tag TB-hyb-* vs open positions)")

    async def _hybrid_orphan_sweeper_loop(self) -> None:
        # Backup only — primary cancel is urgent User Hub path (~immediate).
        interval = float(os.getenv("HYBRID_ORPHAN_SWEEP_S", "0.5") or 0.5)
        interval = max(0.2, interval)
        while True:
            try:
                await self.sweep_hybrid_orphan_orders()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.debug("hybrid orphan sweeper tick failed", exc_info=True)
            await asyncio.sleep(interval)

    async def sweep_hybrid_orphan_orders(self, account_id: str = None) -> Dict[str, Any]:
        """Cancel hybrid protective orders whose position/contract is flat.

        Primary safety net for Position Brackets hybrid path: tags
        ``TB-hyb-{group}-sl|tp`` group legs; when no open position matches the
        order's contract (or registered position_id), cancel the order.
        Also enforces software OCO when one tagged sibling is already gone.
        """
        target_account = account_id or (
            self.selected_account["id"] if self.selected_account else None
        )
        if not target_account:
            return {"skipped": True, "reason": "no_account"}

        positions = await self.get_open_positions(account_id=target_account)
        if isinstance(positions, dict) and positions.get("error"):
            return {"error": positions.get("error")}

        open_pos_ids = set()
        open_contracts = set()
        open_symbols = set()
        for pos in positions or []:
            size = pos.get("size")
            if size is None:
                size = pos.get("quantity")
            try:
                if size is not None and int(size) == 0:
                    continue
            except (TypeError, ValueError):
                pass
            pid = pos.get("id") or pos.get("position_id") or pos.get("positionId")
            if pid is not None:
                open_pos_ids.add(str(pid))
            cid = pos.get("contractId") or pos.get("contract_id")
            if cid:
                open_contracts.add(str(cid))
            sym = str(pos.get("symbol") or "").upper()
            if sym:
                open_symbols.add(sym)

        orders = await self.get_open_orders(account_id=target_account)
        if not isinstance(orders, list):
            return {"cancelled": [], "checked": 0}

        # Group tagged hybrid orders by group_id
        by_group: Dict[str, List[Dict[str, Any]]] = {}
        cancelled: List[str] = []
        for order in orders:
            tag = order.get("customTag") or order.get("tag") or ""
            if not self.is_hybrid_protective_tag(tag):
                continue
            gid = self.hybrid_group_from_tag(tag) or "unknown"
            by_group.setdefault(gid, []).append(order)

        for gid, group_orders in by_group.items():
            # Resolve whether any open position still covers these legs
            still_needed = False
            for order in group_orders:
                oid = str(order.get("id") or order.get("orderId") or "")
                meta = (getattr(self, "_hybrid_oco_legs", None) or {}).get(oid)
                if meta and str(meta.get("position_id")) in open_pos_ids:
                    still_needed = True
                    break
                cid = str(order.get("contractId") or order.get("contract_id") or "")
                if cid and cid in open_contracts:
                    still_needed = True
                    break
                # Symbol from contract id
                try:
                    sym = ""
                    if cid and hasattr(self, "contract_manager"):
                        sym = (
                            self.contract_manager.get_symbol_from_contract_id(cid) or ""
                        ).upper()
                    if sym and sym in open_symbols:
                        still_needed = True
                        break
                except Exception:
                    pass

            if still_needed:
                # Software OCO by tag: peer gone from book but registry armed →
                # cancel remaining immediately (do not wait for position flat).
                # Safe only when the pair was registered (avoids canceling mid-attach
                # when only SL has been placed so far).
                if len(group_orders) == 1:
                    order = group_orders[0]
                    oid = str(order.get("id") or order.get("orderId") or "")
                    meta = (getattr(self, "_hybrid_oco_legs", None) or {}).get(oid)
                    sib = str((meta or {}).get("sibling_id") or "")
                    if oid and meta and sib:
                        open_ids_now = {
                            str(o.get("id") or o.get("orderId"))
                            for o in orders
                            if o.get("id") or o.get("orderId")
                        }
                        if sib not in open_ids_now:
                            if not self._hybrid_claim_cancel(oid):
                                continue
                            try:
                                await self.cancel_order(oid, account_id=target_account)
                                cancelled.append(oid)
                                logger.info(
                                    "Hybrid orphan sweeper software-OCO cancelled "
                                    "%s (peer %s gone, position still open) tag=%s",
                                    oid, sib, order.get("customTag"),
                                )
                            except Exception as exc:
                                (getattr(self, "_hybrid_cancel_claimed", None) or {}).pop(
                                    oid, None
                                )
                                logger.warning(
                                    "Hybrid orphan sweeper OCO cancel %s failed: %s",
                                    oid, exc,
                                )
                            legs = getattr(self, "_hybrid_oco_legs", None) or {}
                            legs.pop(oid, None)
                            legs.pop(sib, None)
                            try:
                                from core.working_order_registry import get_registry
                                get_registry().unregister(oid)
                                get_registry().unregister(sib)
                            except Exception:
                                pass
                continue

            # No open position for this hybrid group → cancel all remaining legs
            for order in group_orders:
                oid = str(order.get("id") or order.get("orderId") or "")
                if not oid:
                    continue
                if not self._hybrid_claim_cancel(oid):
                    continue
                try:
                    res = await self.cancel_order(oid, account_id=target_account)
                    cancelled.append(oid)
                    logger.info(
                        "Hybrid orphan sweeper cancelled %s tag=%s (no open position) res=%s",
                        oid, order.get("customTag"), res,
                    )
                except Exception as exc:
                    (getattr(self, "_hybrid_cancel_claimed", None) or {}).pop(oid, None)
                    logger.warning(
                        "Hybrid orphan sweeper cancel %s failed: %s", oid, exc
                    )
                # Clear registry entries
                legs = getattr(self, "_hybrid_oco_legs", None) or {}
                sib = None
                if oid in legs:
                    sib = legs[oid].get("sibling_id")
                    legs.pop(oid, None)
                if sib:
                    legs.pop(str(sib), None)
                try:
                    from core.working_order_registry import get_registry
                    get_registry().unregister(oid)
                    if sib:
                        get_registry().unregister(str(sib))
                except Exception:
                    pass

        # Also run id-based sibling cancel for registered pairs where one is missing
        legs = getattr(self, "_hybrid_oco_legs", None) or {}
        open_ids = {
            str(o.get("id") or o.get("orderId"))
            for o in orders
            if o.get("id") or o.get("orderId")
        }
        for oid, meta in list(legs.items()):
            if oid in open_ids:
                continue
            # Registered leg gone from book → cancel sibling
            if meta.get("sibling_id") and str(meta["sibling_id"]) in open_ids:
                await self.cancel_hybrid_oco_sibling(
                    oid, reason="orphan_sweep_peer_missing"
                )

        if cancelled:
            logger.info("Hybrid orphan sweeper cancelled %d order(s)", len(cancelled))
        return {"cancelled": cancelled, "groups": list(by_group.keys())}

    async def attach_brackets_to_open_position(
        self,
        position_id_or_symbol: str,
        *,
        stop_loss_price: float,
        take_profit_price: float,
        account_id: str = None,
        strategy_name: str = "attach_brackets",
    ) -> Dict:
        """Attach protective SL/TP to an existing open position (interactive / recovery).

        Same mechanics as hybrid post-fill attach. ``position_id_or_symbol`` may be
        a numeric position id or a root symbol (e.g. ``MNQ``).
        """
        target_account = account_id or (
            self.selected_account["id"] if self.selected_account else None
        )
        if not target_account:
            return {"error": "No account selected"}

        positions = await self.get_open_positions(account_id=target_account)
        if isinstance(positions, dict) and positions.get("error"):
            return positions
        want = str(position_id_or_symbol or "").strip()
        if not want:
            return {"error": "position_id or symbol required"}

        matched = None
        for pos in positions or []:
            pid = str(pos.get("id") or pos.get("position_id") or "")
            sym = str(pos.get("symbol") or "").upper()
            if want == pid or want.upper() == sym or self._position_symbol_matches(sym, want.upper()):
                matched = pos
                break
            cid = str(pos.get("contractId") or "")
            if want.upper() in cid.upper():
                matched = pos
                break
        if not matched:
            return {"error": f"No open position matching {want!r}"}

        position_id = str(matched.get("id") or matched.get("position_id"))
        symbol = str(matched.get("symbol") or want).upper()
        side_raw = matched.get("side")
        if side_raw in (0, "0", "LONG", "long", "Buy", "BUY"):
            side = "BUY"
        else:
            side = "SELL"
        qty = int(matched.get("quantity") or matched.get("size") or 1)
        pending = {
            "order_id": f"manual-attach-{position_id}",
            "symbol": symbol,
            "side": side,
            "quantity": qty,
            "stop_loss_price": float(stop_loss_price),
            "take_profit_price": float(take_profit_price),
            "account_id": str(target_account),
            "strategy_name": strategy_name,
            "entry_price": matched.get("entryPrice") or matched.get("entry_price"),
            "attached": False,
        }
        logger.info(
            "attach_brackets_to_open_position pos=%s %s %s qty=%s SL=%s TP=%s",
            position_id, side, symbol, qty, stop_loss_price, take_profit_price,
        )
        return await self._attach_hybrid_protective_orders(
            pending, position_id=position_id
        )

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
            # ── A: Live-cache-first read (skip REST when SignalR has fed enough fresh bars) ──
            # Only short-circuits in the steady-state hot path:
            #   - no explicit start_time/end_time (we don't try to satisfy historical ranges)
            #   - kwargs that imply special semantics (continuous_daily, include_partial_daily)
            #     are not present
            #   - the live cache already holds ``limit`` bars and the tail is fresh
            if (
                start_time is None
                and end_time is None
                and not kwargs
                and self._live_cache_can_serve(symbol, timeframe, int(limit))
                and os.getenv("LIVE_BAR_CACHE_FIRST", "true").strip().lower() not in {"0", "false", "no", "off"}
            ):
                served = self._serve_from_live_cache(symbol, timeframe, int(limit))
                if not hasattr(self, "_live_cache_hit_count"):
                    self._live_cache_hit_count: Dict[str, int] = {}
                key = f"{str(symbol).upper()}|{self._normalize_timeframe_key(timeframe)}"
                self._live_cache_hit_count[key] = self._live_cache_hit_count.get(key, 0) + 1
                # Log once per (symbol, tf) at INFO so operators see the cache is doing its job;
                # subsequent hits go to DEBUG to avoid log spam.
                first_hit = self._live_cache_hit_count[key] == 1
                logger.log(
                    logging.INFO if first_hit else logging.DEBUG,
                    "⚡ get_historical_data live-cache hit %s %s (%d bars, tail=%s) — REST skipped",
                    symbol, timeframe, len(served),
                    served[-1].get("timestamp") if served else "?",
                )
                return served

            # DEBUG: fires for every symbol on every strategy poll (3 symbols × 12 polls/min = 36 lines/min).
            # The stuck-REST detector below logs a WARNING when things go wrong; this is breadcrumbs only.
            logger.debug(f"Fetching historical data for {symbol} ({timeframe}, {limit} bars)")

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
                # Demoted from INFO to DEBUG (2026-05-29): these two lines fire
                # for EVERY symbol on EVERY poll (~36 lines/min for the typical
                # 3-symbol setup) and only matter when actively diagnosing stale
                # data. The stuck-REST detector ABOVE produces a WARNING when
                # things actually go wrong; this is purely a debug breadcrumb.
                from datetime import datetime, timezone as _tz

                last_ts = result[-1].get("timestamp") or result[-1].get("time")
                logger.debug(f"📊 get_historical_data last bar timestamp (ISO) = {last_ts}")
                logger.debug(f"📊 get_historical_data now UTC                    = {datetime.now(_tz.utc)}")

                logger.debug(f"✅ Retrieved {len(result)} bars from adapter (canonical implementation)")
            else:
                logger.warning("get_historical_data: adapter returned no bars")

            # ── Stuck-REST detector (2026-05-27 fix) ────────────────────────────────────
            # ONLY for live polling — calls with an explicit ``start_time``/``end_time``
            # or special kwargs are backfills/backtests that legitimately request fixed
            # historical windows and shouldn't trip the detector.
            if (
                result
                and start_time is None
                and end_time is None
                and not kwargs
            ):
                rest_last_ts = result[-1].get("timestamp") or result[-1].get("time")
                try:
                    await self._detect_and_handle_stuck_rest(symbol, timeframe, rest_last_ts)
                except Exception as exc:
                    logger.debug("Stuck-REST detector raised (continuing): %s", exc)

            # Merge fresh bars from the live SignalR cache so strategies don't go blind when
            # the historical REST endpoint stalls (see docs/GOTCHAS.md, 2026-05-21 outage).
            try:
                result = self._merge_live_bars(result, symbol, timeframe)
            except Exception as exc:
                logger.debug("Live-bar merge failed (continuing with REST-only result): %s", exc)

            # REST poll delivered bars — counts as bar-path liveness even
            # when SignalR quotes are paused.
            if result:
                try:
                    from core.data_feed_health import get_monitor
                    get_monitor().record_bar_activity(str(symbol).upper())
                except Exception:
                    pass

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
            
            # Single adapter-level gather (avoids duplicate work vs two bot wrappers)
            pos_objs, orders = await self.broker_adapter.get_positions_and_open_orders_parallel(
                target_account
            )
            positions = self._adapter_positions_to_ui_dicts(pos_objs)

            return {
                "positions": positions,
                "orders": orders,
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
    
    def ensure_discord_event_notifications(self) -> None:
        """Wire User Hub fill / trade-close events to Discord (idempotent).

        Headless ``strategy_executor`` processes do not run the polling
        ``check_order_fills`` loop unless ``auto_fills`` is enabled manually.
        Real-time ``ORDER_FILLED`` / ``TRADE_CLOSED`` bus events cover fills
        and closed-trade P&L for Discord when ``DISCORD_NOTIFY_FILLS`` is on.
        """
        if getattr(self, "_discord_events_wired", False):
            return
        bus = getattr(self, "event_bus", None)
        notifier = getattr(self, "discord_notifier", None)
        if not bus or not notifier or not notifier.enabled:
            return
        from core.discord_notifier import notify_fills_enabled
        from core.events import EventType

        async def _account_name() -> str:
            acc = getattr(self, "selected_account", None)
            if isinstance(acc, dict):
                return str(acc.get("name") or "Unknown")
            if acc:
                return str(acc)
            return "Unknown"

        async def _on_order_filled(event) -> None:
            if not notify_fills_enabled():
                return
            data = getattr(event, "data", None) or {}
            order = data.get("order") or {}
            account_id = str(data.get("account_id") or order.get("accountId") or "")
            order_id = str(order.get("id") or order.get("orderId") or "")
            if not order_id:
                return
            unique_id = f"{account_id}:{order_id}"
            if unique_id in self._notified_orders:
                return
            disposition = str(order.get("positionDisposition") or "").lower()
            if disposition == "closing":
                return

            symbol = order.get("symbol") or self._get_symbol_from_contract_id(
                order.get("contractId", "")
            )
            side = "BUY" if order.get("side", 0) == 0 else "SELL"
            quantity = order.get("size") or order.get("quantity") or 0
            fill_price = (
                order.get("filledPrice")
                or order.get("fillPrice")
                or order.get("executionPrice")
            )
            order_type = order.get("type", 0)
            type_map = {1: "Limit", 2: "Market", 4: "Stop", 5: "Stop Limit"}
            order_type_str = (
                order.get("orderType")
                or type_map.get(order_type, "Unknown")
            )
            tag = order.get("customTag") or order.get("tag") or ""
            if not self._order_fill_notification_allowed(order):
                return
            strat_slug = self._strategy_from_custom_tag(tag)
            notification_data = {
                "symbol": symbol,
                "side": side,
                "quantity": quantity,
                "fill_price": f"${float(fill_price):.2f}" if fill_price else "Unknown",
                "order_type": order_type_str,
                "order_id": order_id,
                "position_id": order.get("positionId", "Unknown"),
                "custom_tag": tag,
                "strategy": strat_slug,
            }
            try:
                await notifier.send_order_fill_notification(
                    notification_data, await _account_name()
                )
                self._notified_orders.add(unique_id)
            except Exception as exc:
                logger.debug("Discord ORDER_FILLED handler failed: %s", exc)

        async def _on_trade_closed(event) -> None:
            if not notify_fills_enabled():
                return
            data = getattr(event, "data", None) or {}
            trade_id = str(data.get("trade_id") or "")
            if not trade_id:
                return
            close_key = f"trade:{trade_id}"
            if not hasattr(self, "_notified_trades"):
                self._notified_trades = set()
            if close_key in self._notified_trades:
                return

            symbol = data.get("symbol", "Unknown")
            side = str(data.get("side") or "Unknown").upper()
            quantity = data.get("quantity") or 0
            entry_price = float(data.get("entry_price") or 0)
            exit_price = float(data.get("exit_price") or 0)
            net_pnl = data.get("net_pnl")
            if net_pnl is None:
                net_pnl = data.get("gross_pnl", 0)
            notification_data = {
                "symbol": symbol,
                "side": side,
                "quantity": quantity,
                "entry_price": entry_price,
                "exit_price": exit_price,
                "pnl": net_pnl,
                "close_method": "Trade closed",
                "exit_reason": "Trade closed",
                "position_id": trade_id,
            }
            try:
                await notifier.send_position_close_notification(
                    notification_data, await _account_name()
                )
                self._notified_trades.add(close_key)
            except Exception as exc:
                logger.debug("Discord TRADE_CLOSED handler failed: %s", exc)

        try:
            bus.subscribe(EventType.ORDER_FILLED, _on_order_filled)
            bus.subscribe(EventType.TRADE_CLOSED, _on_trade_closed)
            self._discord_events_wired = True
            logger.info("Discord fill/trade-close notifications wired to event bus")
        except Exception as exc:
            logger.warning("Could not wire Discord event notifications: %s", exc)

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
    
    async def _discord_status_reporter_loop(self) -> None:
        """Optional periodic account snapshot to Discord (set DISCORD_STATUS_INTERVAL_SECONDS > 0)."""
        from core.discord_status_digest import (
            build_discord_status_lines,
            discord_status_interval_seconds,
        )

        interval = discord_status_interval_seconds()
        if interval <= 0:
            return
        await asyncio.sleep(20)
        while True:
            try:
                notifier = getattr(self, "discord_notifier", None)
                if not notifier or not notifier.enabled:
                    await asyncio.sleep(interval)
                    continue
                title, lines = await build_discord_status_lines(self)
                await notifier.send_status_digest(title, lines)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.debug("Discord status reporter tick failed", exc_info=True)
            await asyncio.sleep(interval)

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
                    self.ensure_discord_event_notifications()
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

            from core.discord_status_digest import discord_status_interval_seconds

            _st_int = discord_status_interval_seconds()
            if _st_int > 0:
                asyncio.create_task(self._discord_status_reporter_loop())
                logger.info("Discord status reporter started (every %ss)", _st_int)
            
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
                    logger.debug(
                        "shutdown_historical_client_cache in finally failed",
                        exc_info=True,
                    )
    
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
        finally:
            # Avoid aiohttp "Unclosed client session" after one-shot CLI commands (e.g. history … --csv).
            try:
                await self.auth_manager.close()
            except Exception:
                logger.debug("auth_manager.close in run_non_interactive finally failed", exc_info=True)
            try:
                await self.discord_notifier.close()
            except Exception:
                logger.debug("discord_notifier.close in run_non_interactive finally failed", exc_info=True)
    
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