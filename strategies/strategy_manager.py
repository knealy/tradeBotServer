"""
Strategy Manager - Dynamic Strategy Loading and Coordination

Manages multiple trading strategies, selects appropriate strategies based on
market conditions, and coordinates their execution.
"""

import os
import logging
import asyncio
import importlib
import tomllib
from pathlib import Path
from typing import Dict, List, Optional, Type, Any, Tuple, Set
from datetime import datetime, timezone
from strategies.strategy_base import BaseStrategy, StrategyConfig, StrategyStatus, MarketCondition
from core.events import Event, EventType

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_STRATEGIES_CONFIG_DIR = _REPO_ROOT / "config" / "strategies"

# Built-in strategies: (import path, class name, short description for UIs without importing).
BUILTIN_STRATEGY_SPECS: Dict[str, Tuple[str, str, str]] = {
    "overnight_range": (
        "strategies.overnight_range_strategy",
        "OvernightRangeStrategy",
        "Overnight range breakout",
    ),
    "mean_reversion": (
        "strategies.mean_reversion_strategy",
        "MeanReversionStrategy",
        "Mean reversion",
    ),
    "trend_following": (
        "strategies.trend_following_strategy",
        "TrendFollowingStrategy",
        "Trend following",
    ),
    "simple_momentum": (
        "strategies.simple_momentum_strategy",
        "SimpleMomentumStrategy",
        "Simple momentum",
    ),
    "simple_candle": (
        "strategies.simple_candle_strategy",
        "SimpleCandleStrategy",
        "Simple candle patterns",
    ),
    "trend_scalping": (
        "strategies.trend_scalping_strategy",
        "TrendScalpingStrategy",
        "Trend scalping",
    ),
    "simple_rth": (
        "strategies.simple_rth_strategy",
        "SimpleRthStrategy",
        "RTH-hours simple momentum",
    ),
    "vwap_zscore_reversion": (
        "strategies.vwap_zscore_reversion_strategy",
        "VwapZscoreReversionStrategy",
        "Intraday VWAP Z-score mean reversion (research-grade)",
    ),
    "body_reversion": (
        "strategies.body_reversion_strategy",
        "BodyReversionStrategy",
        "Big-body 5m bar mean reversion (research-grade)",
    ),
    "morning_range_reversion": (
        "strategies.morning_range_reversion_strategy",
        "MorningRangeReversionStrategy",
        "7am ET 5m range sweep → re-entry fade (research-grade)",
    ),
    "hourly_anchor_retrace": (
        "strategies.hourly_anchor_retrace_strategy",
        "HourlyAnchorRetraceStrategy",
        "7–8am ET anchor hour: first close outside → stop-entry at breached extreme",
    ),
}


def _normalize_strategy_id(name: str) -> str:
    return str(name).strip().lower().replace("-", "_")


def _strategy_ids_enabled_in_toml() -> Set[str]:
    """Strategy ids with config/strategies/<id>.toml and meta.enabled != false."""
    out: Set[str] = set()
    if not _STRATEGIES_CONFIG_DIR.is_dir():
        return out
    for path in _STRATEGIES_CONFIG_DIR.glob("*.toml"):
        if path.name.startswith("_"):
            continue
        try:
            with path.open("rb") as fh:
                data = tomllib.load(fh)
            meta = data.get("meta") or {}
            if meta.get("enabled", True) is False:
                continue
            out.add(path.stem.lower())
        except Exception as exc:
            logger.warning("Skipping strategy TOML %s: %s", path, exc)
    return out


class StrategyManager:
    """
    Manages multiple trading strategies dynamically.
    
    Features:
    - Load strategies from config
    - Auto-select strategies based on market conditions
    - Coordinate multiple strategies running simultaneously
    - Aggregate performance metrics
    - Enforce global risk limits
    """
    
    def __init__(self, trading_bot):
        """
        Initialize strategy manager.
        
        Args:
            trading_bot: Reference to main TradingBot instance
        """
        self.trading_bot = trading_bot
        self.strategies: Dict[str, BaseStrategy] = {}
        self.strategy_classes: Dict[str, Type[BaseStrategy]] = {}
        self.available_strategies: Dict[str, Type[BaseStrategy]] = {}  # Alias for strategy_classes
        # Deferred imports: name -> (module_path, class_name) — imported on first use
        self._lazy_strategy_specs: Dict[str, Tuple[str, str]] = {}
        self.active_strategies: List[str] = []
        
        # Global settings
        # Env-only infra settings (strategy knobs live in config/strategies/*.toml).
        self.max_concurrent_strategies = int(os.environ.get('MAX_CONCURRENT_STRATEGIES', '3'))
        self.auto_select_enabled = os.environ.get('AUTO_SELECT_STRATEGIES', 'false').lower() == 'true'
        self.market_condition_check_interval = int(os.environ.get('MARKET_CONDITION_CHECK_INTERVAL', '300'))  # 5 minutes
        
        # State
        self._tasks: List[asyncio.Task] = []
        self._running = False
        self._state_cache: Dict[str, Dict] = {}
        
        logger.info("✨ Strategy Manager initialized")

    async def _publish_strategy_lifecycle(self, event_type: EventType, strategy_name: str) -> None:
        """Notify EventBus when a strategy starts or stops (for event-driven monitors)."""
        bus = getattr(self.trading_bot, "event_bus", None)
        if not bus or not getattr(bus, "_running", False):
            return
        try:
            await bus.publish(
                Event(
                    type=event_type,
                    data={"strategy": strategy_name, "strategy_name": strategy_name},
                    source="strategy_manager",
                )
            )
        except Exception as exc:
            logger.debug("Strategy lifecycle event not published: %s", exc)

    def _all_registered_names(self) -> List[str]:
        """Strategy keys including lazy-registered (not yet imported)."""
        names: Set[str] = set(self.strategy_classes.keys()) | set(self._lazy_strategy_specs.keys())
        return sorted(names)

    def registered_strategy_names(self) -> List[str]:
        """Public: all strategy ids registered with this manager (lazy or eager)."""
        return self._all_registered_names()

    def catalog_strategy_names(self) -> List[str]:
        """Full built-in catalog plus any extra registered/loaded ids (for UIs, CLI lists)."""
        return sorted(
            set(self.builtin_strategy_ids())
            | set(self._all_registered_names())
            | set(self.strategies.keys())
        )

    @staticmethod
    def builtin_strategy_ids() -> List[str]:
        """All built-in strategy ids (catalog); does not import strategy modules."""
        return sorted(BUILTIN_STRATEGY_SPECS.keys())

    def _is_registered(self, name: str) -> bool:
        n = _normalize_strategy_id(name)
        return n in self.strategy_classes or n in self._lazy_strategy_specs

    def is_builtin_strategy(self, name: str) -> bool:
        return _normalize_strategy_id(name) in BUILTIN_STRATEGY_SPECS

    def _is_known_strategy(self, name: str) -> bool:
        """Built-in id, or explicitly registered (lazy/eager), or ad-hoc test class."""
        n = _normalize_strategy_id(name)
        return n in BUILTIN_STRATEGY_SPECS or n in self.strategy_classes or n in self._lazy_strategy_specs

    def is_strategy_registered(self, name: str) -> bool:
        """True if this id can be started or configured (built-in or registered in this process)."""
        return self._is_known_strategy(name)

    def get_strategy_class(self, name: str) -> Optional[Type[BaseStrategy]]:
        """Resolve and return the strategy class, importing lazily if needed."""
        n = _normalize_strategy_id(name)
        if n in self.strategy_classes:
            return self.strategy_classes[n]
        if n not in BUILTIN_STRATEGY_SPECS and n not in self._lazy_strategy_specs:
            return None
        try:
            return self._ensure_strategy_class(n)
        except Exception:
            logger.exception("Failed to load strategy class for %s", n)
            return None

    def _ensure_strategy_class(self, name: str) -> Type[BaseStrategy]:
        """Return strategy class, importing lazily if needed."""
        n = _normalize_strategy_id(name)
        if n in self.strategy_classes:
            return self.strategy_classes[n]
        spec_pair = self._lazy_strategy_specs.get(n)
        if not spec_pair:
            spec = BUILTIN_STRATEGY_SPECS.get(n)
            if not spec:
                raise KeyError(n)
            spec_pair = (spec[0], spec[1])
            self._lazy_strategy_specs[n] = spec_pair
        module_path, class_name = spec_pair
        mod = importlib.import_module(module_path)
        cls = getattr(mod, class_name)
        self.strategy_classes[n] = cls
        self.available_strategies[n] = cls
        return cls

    def register_builtin_strategies(self, subset: Optional[List[str]] = None) -> None:
        """
        Register lazy imports for this process.

        - If ``subset`` is a non-empty list: only those built-in ids (e.g. executor ``--strategy=``).
        - Else: ids with ``config/strategies/<id>.toml`` and ``meta.enabled`` not false, plus
          optional env ``REGISTER_STRATEGIES=comma,list``.
        - If that yields nothing: register all built-ins (dashboard / dev default).
        """
        if subset:
            for raw in subset:
                n = _normalize_strategy_id(raw)
                if n not in BUILTIN_STRATEGY_SPECS:
                    logger.warning("Unknown strategy id %r ignored (not in built-in catalog)", raw)
                    continue
                spec = BUILTIN_STRATEGY_SPECS[n]
                self.register_strategy_lazy(n, spec[0], spec[1])
            logger.info(
                "Registered %d built-in strategy module(s) for this process (explicit subset)",
                len(self._all_registered_names()),
            )
            return

        selected: Set[str] = set(_strategy_ids_enabled_in_toml())
        extra = os.environ.get("REGISTER_STRATEGIES", "").strip()
        if extra:
            for part in extra.split(","):
                n = _normalize_strategy_id(part)
                if n in BUILTIN_STRATEGY_SPECS:
                    selected.add(n)
        # Keep only ids that map to lazy built-in modules (ignore unknown TOML stems).
        selected = {n for n in selected if n in BUILTIN_STRATEGY_SPECS}
        if not selected:
            selected = set(BUILTIN_STRATEGY_SPECS.keys())
        for n in sorted(selected):
            spec = BUILTIN_STRATEGY_SPECS[n]
            self.register_strategy_lazy(n, spec[0], spec[1])
        logger.info(
            "Registered %d built-in strategy module(s) for this process (TOML/env/default)",
            len(selected),
        )
    
    def register_strategy(self, name: str, strategy_class: Type[BaseStrategy]):
        """
        Register a strategy class.
        
        Args:
            name: Strategy identifier
            strategy_class: Strategy class (not instance)
        """
        n = _normalize_strategy_id(name)
        self.strategy_classes[n] = strategy_class
        self.available_strategies[n] = strategy_class  # Keep alias in sync
        self._lazy_strategy_specs.pop(n, None)
        logger.info(f"📝 Registered strategy: {n}")

    def register_strategy_lazy(self, name: str, module_path: str, class_name: str) -> None:
        """Register a strategy without importing its module until first use."""
        n = _normalize_strategy_id(name)
        if n in self.strategy_classes:
            return
        self._lazy_strategy_specs[n] = (module_path, class_name)
        logger.debug("Registered strategy (lazy): %s", n)

    async def broadcast_signal(self, signal_data: Dict[str, Any]) -> None:
        """
        Broadcast a strategy signal to:
        - GUI via `gui.chart_html.broadcast_update` (WebSocket)
        - Discord via `DiscordNotifier.send_signal_notification`
        - External processes via StrategyExecutor WebSocket client (if running in executor)

        Used by StrategyManager loops AND strategies that run their own loop
        (e.g. `SimpleCandleStrategy.run()`).
        """
        # Discord (best-effort)
        try:
            if hasattr(self.trading_bot, 'discord_notifier') and self.trading_bot.discord_notifier:
                account_name = 'Unknown'
                if getattr(self.trading_bot, 'selected_account', None):
                    if isinstance(self.trading_bot.selected_account, dict):
                        account_name = self.trading_bot.selected_account.get('name', 'Unknown')
                    else:
                        account_name = str(self.trading_bot.selected_account)
                # Discord notifier uses synchronous HTTP; offload to a thread to avoid
                # blocking the event loop in StrategyManager async code.
                asyncio.create_task(
                    self.trading_bot.discord_notifier.send_signal_notification(
                        signal_type=str(signal_data.get('type', 'SIGNAL')),
                        symbol=str(signal_data.get('symbol', 'Unknown')),
                        account_name=account_name,
                        details=signal_data,
                    )
                )
        except Exception as e:
            logger.debug(f"Could not send Discord notification for signal: {e}")

        # GUI via WebSocket (best-effort)
        # Try local GUI first (if running in same process)
        try:
            import gui.chart_html as chart_html_module
            broadcast_func = getattr(chart_html_module, 'broadcast_update', None)
            if broadcast_func:
                await broadcast_func({'type': 'signal', 'data': signal_data})
        except Exception as e:
            # If local GUI not available, try external executor WebSocket
            try:
                # Check if we're running in a StrategyExecutor context
                # The executor will have its own WebSocket client
                if hasattr(self.trading_bot, '_strategy_executor'):
                    executor = self.trading_bot._strategy_executor
                    if hasattr(executor, 'broadcast_signal_to_gui'):
                        await executor.broadcast_signal_to_gui(signal_data)
            except Exception as exec_e:
                logger.debug(f"Could not broadcast signal via executor: {exec_e}")
    
    def load_strategies(self):
        """
        Load all enabled strategies from environment configuration.
        """
        logger.info("🔄 Loading strategies from configuration...")
        
        for name in self._all_registered_names():
            try:
                strategy_class = self._ensure_strategy_class(name)
                # Load config from env
                config = StrategyConfig.from_env(name)
                
                if config.enabled:
                    # Instantiate strategy
                    strategy = strategy_class(self.trading_bot, config)
                    self.strategies[name] = strategy
                    logger.info(f"✅ Loaded strategy: {name} (symbols: {', '.join(config.symbols)})")
                else:
                    logger.info(f"⏸️  Strategy disabled: {name}")
            
            except Exception as e:
                logger.error(f"❌ Failed to load strategy {name}: {e}")
        
        logger.info(f"📊 Total strategies loaded: {len(self.strategies)}/{len(self._all_registered_names())}")
    
    def load_strategies_from_config(self):
        """Alias for load_strategies() for backward compatibility."""
        return self.load_strategies()

    async def auto_start_enabled_strategies(self):
        """
        Auto-start strategies that are enabled via persisted state (per account) or environment variables.
        Called after apply_persisted_states() to ensure enabled strategies start.
        Prioritizes persisted state over environment variables for per-account configuration.
        """
        logger.info("🚀 Auto-starting enabled strategies...")

        db = getattr(self.trading_bot, 'db', None)
        account_id = self._get_account_id()
        
        # Get persisted states for this account
        persisted_states = {}
        if db and account_id:
            raw_persisted = db.get_strategy_states(account_id)
            persisted_states = {_normalize_strategy_id(k): v for k, v in raw_persisted.items()}
            logger.info(f"📋 Loaded {len(persisted_states)} persisted strategy states for account {account_id}")
        else:
            persisted_states = {}

        names = sorted(
            set(persisted_states.keys())
            | set(self._all_registered_names())
            | set(self.strategies.keys())
        )
        for name in names:
            if not self._is_known_strategy(name):
                logger.debug("Skipping auto-start for unknown strategy id: %s", name)
                continue
            try:
                strategy_class = self._ensure_strategy_class(name)
                # Check persisted state first (per-account configuration)
                persisted_state = persisted_states.get(name)
                should_start = False
                symbols = None
                config_source = "persisted"
                
                if persisted_state:
                    # Use persisted state if available
                    enabled_value = persisted_state.get('enabled', False)
                    # Handle both string "true"/"false" and boolean True/False
                    if isinstance(enabled_value, str):
                        should_start = enabled_value.lower() in ('true', '1', 'yes', 'on')
                    else:
                        should_start = bool(enabled_value)
                    symbols = persisted_state.get('symbols') or []
                    logger.info(f"📝 Strategy {name}: using persisted state (enabled={should_start}, symbols={symbols}, raw_value={enabled_value})")
                else:
                    # Fallback to environment variables if no persisted state
                    config = StrategyConfig.from_env(name)
                    should_start = config.enabled
                    symbols = config.symbols
                    config_source = "environment"
                    logger.info(f"🌍 Strategy {name}: using environment config (enabled={should_start}, symbols={symbols})")

                if should_start:
                    if name in self.active_strategies:
                        logger.info(f"⏭️  Strategy {name} already active, skipping auto-start")
                    else:
                        # Strategy should be running but isn't - START IT!
                        logger.info(f"▶️  Auto-starting {name} from {config_source} (symbols: {', '.join(symbols) if symbols else 'default'})")
                        
                        # Ensure strategy instance exists
                        if name not in self.strategies:
                            base_config = StrategyConfig.from_env(name)
                            strategy = strategy_class(self.trading_bot, base_config)
                            self.strategies[name] = strategy
                            
                            # Apply persisted settings if available
                            if persisted_state and persisted_state.get('settings'):
                                self._apply_config_settings(strategy, persisted_state['settings'])
                                # Apply strategy-specific parameters
                                self._apply_strategy_specific_settings(strategy, persisted_state['settings'])
                        else:
                            strategy = self.strategies[name]
                            # Update from persisted state if available
                            if persisted_state and persisted_state.get('settings'):
                                self._apply_config_settings(strategy, persisted_state['settings'])
                                self._apply_strategy_specific_settings(strategy, persisted_state['settings'])

                        # Start the strategy with persisted symbols or config symbols
                        start_symbols = symbols if symbols else (strategy.config.symbols if strategy else [])
                        success, message = await self.start_strategy(name, symbols=start_symbols, persist=True)
                        if success:
                            logger.info(f"✅ Auto-started: {message}")
                        else:
                            logger.error(f"❌ Failed to auto-start {name}: {message}")
                else:
                    logger.info(f"⏸️  Strategy {name} is disabled (should_start=False), skipping auto-start")

            except Exception as e:
                logger.error(f"❌ Error auto-starting strategy {name}: {e}")
                import traceback
                logger.debug(f"Traceback: {traceback.format_exc()}")

        logger.info(f"📊 Active strategies after auto-start: {len(self.active_strategies)}")

    async def update_strategy_config(self, name: str, symbols: List[str] = None,
                                     position_size: int = None, max_positions: int = None,
                                     enabled: bool = None, strategy_params: Dict[str, Any] = None) -> tuple:
        """
        Update strategy configuration.

        Args:
            name: Strategy name
            symbols: List of trading symbols
            position_size: Contracts per trade
            max_positions: Max concurrent positions
            enabled: Whether strategy is enabled
            strategy_params: Strategy-specific parameters (e.g., overnight_start_time, atr_period)

        Returns:
            tuple: (success: bool, message: str)
        """
        n = _normalize_strategy_id(name)
        if not self._is_known_strategy(n):
            return False, f"Unknown strategy: {name}"

        # Get or create strategy instance
        if n not in self.strategies:
            strategy_class = self._ensure_strategy_class(n)
            config = StrategyConfig.from_env(n)
            strategy = strategy_class(self.trading_bot, config)
            self.strategies[n] = strategy

        strategy = self.strategies[n]

        # Update config fields
        if symbols is not None:
            strategy.config.symbols = symbols
        if position_size is not None:
            strategy.config.position_size = position_size
        if max_positions is not None:
            strategy.config.max_positions = max_positions
        if enabled is not None:
            strategy.config.enabled = enabled

        # Update strategy-specific parameters
        if strategy_params:
            self._apply_strategy_specific_settings(strategy, strategy_params)

        # Persist the updated state (including strategy-specific params)
        strategy_specific_settings = {}
        if strategy_params:
            strategy_specific_settings = strategy_params.copy()
        
        self._save_strategy_state(
            n,
            strategy.config.enabled,
            strategy.config.symbols,
            persist=True,
            strategy_specific_settings=strategy_specific_settings
        )

        logger.info(f"📝 Updated config for {n}: symbols={strategy.config.symbols}, "
                   f"position_size={strategy.config.position_size}, max_positions={strategy.config.max_positions}")

        return True, f"Configuration updated for {n}"

    def _get_account_id(self) -> Optional[str]:
        """Resolve the currently selected account id from the trading bot."""
        account = getattr(self.trading_bot, 'selected_account', None)
        if not account:
            return None
        return str(
            account.get('id')
            or account.get('account_id')
            or account.get('accountId')
        )
    
    def _serialize_config(self, config: StrategyConfig, strategy: Optional[BaseStrategy] = None) -> Dict[str, Any]:
        """Serialize strategy config and strategy-specific settings for persistence."""
        serialized = {
            "max_positions": config.max_positions,
            "position_size": config.position_size,
            "risk_per_trade_percent": config.risk_per_trade_percent,
            "max_daily_trades": config.max_daily_trades,
            "preferred_conditions": [c.value for c in config.preferred_conditions],
            "avoid_conditions": [c.value for c in config.avoid_conditions],
            "trading_start_time": config.trading_start_time,
            "trading_end_time": config.trading_end_time,
            "no_trade_start": config.no_trade_start,
            "no_trade_end": config.no_trade_end,
            "respect_dll": config.respect_dll,
            "respect_mll": config.respect_mll,
            "max_dll_usage_percent": config.max_dll_usage_percent,
        }
        
        # Add strategy-specific parameters
        if strategy:
            strategy_name = strategy.__class__.__name__.lower()
            if 'overnightrange' in strategy_name or strategy_name == 'overnight_range':
                # Serialize overnight range specific settings
                if hasattr(strategy, 'overnight_start'):
                    serialized['overnight_start_time'] = strategy.overnight_start
                if hasattr(strategy, 'overnight_end'):
                    serialized['overnight_end_time'] = strategy.overnight_end
                if hasattr(strategy, 'market_open_time'):
                    serialized['market_open_time'] = strategy.market_open_time
                if hasattr(strategy, 'timezone'):
                    serialized['strategy_timezone'] = str(strategy.timezone)
                if hasattr(strategy, 'atr_period'):
                    serialized['atr_period'] = strategy.atr_period
                if hasattr(strategy, 'atr_timeframe'):
                    serialized['atr_timeframe'] = strategy.atr_timeframe
                if hasattr(strategy, 'stop_atr_multiplier'):
                    serialized['stop_atr_multiplier'] = strategy.stop_atr_multiplier
                if hasattr(strategy, 'tp_atr_multiplier'):
                    serialized['tp_atr_multiplier'] = strategy.tp_atr_multiplier
                if hasattr(strategy, 'breakeven_enabled'):
                    serialized['breakeven_enabled'] = strategy.breakeven_enabled
                if hasattr(strategy, 'breakeven_profit_points'):
                    serialized['breakeven_profit_points'] = strategy.breakeven_profit_points
                if hasattr(strategy, 'range_break_offset'):
                    serialized['range_break_offset'] = strategy.range_break_offset
        
        return serialized
    
    def _apply_config_settings(self, strategy: BaseStrategy, settings: Dict[str, Any]) -> None:
        """Apply persisted settings onto a strategy config."""
        if not settings:
            return
        
        config = strategy.config
        if 'max_positions' in settings:
            config.max_positions = int(settings['max_positions'])
        if 'position_size' in settings:
            config.position_size = int(settings['position_size'])
        if 'risk_per_trade_percent' in settings:
            config.risk_per_trade_percent = float(settings['risk_per_trade_percent'])
        if 'max_daily_trades' in settings:
            config.max_daily_trades = int(settings['max_daily_trades'])
        if 'preferred_conditions' in settings:
            config.preferred_conditions = [
                MarketCondition(value) for value in settings['preferred_conditions']
                if value in MarketCondition._value2member_map_
            ]
        if 'avoid_conditions' in settings:
            config.avoid_conditions = [
                MarketCondition(value) for value in settings['avoid_conditions']
                if value in MarketCondition._value2member_map_
            ]
        if 'trading_start_time' in settings:
            config.trading_start_time = settings['trading_start_time']
        if 'trading_end_time' in settings:
            config.trading_end_time = settings['trading_end_time']
        if 'no_trade_start' in settings:
            config.no_trade_start = settings['no_trade_start']
        if 'no_trade_end' in settings:
            config.no_trade_end = settings['no_trade_end']
        if 'respect_dll' in settings:
            config.respect_dll = bool(settings['respect_dll'])
        if 'respect_mll' in settings:
            config.respect_mll = bool(settings['respect_mll'])
        if 'max_dll_usage_percent' in settings:
            config.max_dll_usage_percent = float(settings['max_dll_usage_percent'])
    
    def _apply_strategy_specific_settings(self, strategy: BaseStrategy, settings: Dict[str, Any]) -> None:
        """Apply strategy-specific parameters (e.g., overnight time range, ATR settings).
        
        NOTE: Environment variables take precedence over database settings.
        Only apply database settings if the corresponding env var is not set.
        """
        if not settings:
            return
        
        strategy_name = strategy.__class__.__name__.lower()
        
        # Overnight Range Strategy specific settings
        if 'overnightrange' in strategy_name or strategy_name == 'overnight_range':
            import pytz
            import os
            
            # Only apply database settings if env vars are not set (env vars take precedence)
            # This ensures .env file changes are respected even if database has old values
            if 'overnight_start_time' in settings:
                if os.environ.get('OVERNIGHT_START_TIME'):
                    logger.info(f"🌍 Using OVERNIGHT_START_TIME from .env ({os.environ.get('OVERNIGHT_START_TIME')}) instead of database ({settings['overnight_start_time']})")
                else:
                    strategy.overnight_start = str(settings['overnight_start_time'])
            if 'overnight_end_time' in settings:
                if os.environ.get('OVERNIGHT_END_TIME'):
                    logger.info(f"🌍 Using OVERNIGHT_END_TIME from .env ({os.environ.get('OVERNIGHT_END_TIME')}) instead of database ({settings['overnight_end_time']})")
                else:
                    strategy.overnight_end = str(settings['overnight_end_time'])
            if 'market_open_time' in settings:
                if os.environ.get('MARKET_OPEN_TIME'):
                    logger.info(f"🌍 Using MARKET_OPEN_TIME from .env ({os.environ.get('MARKET_OPEN_TIME')}) instead of database ({settings['market_open_time']})")
                else:
                    strategy.market_open_time = str(settings['market_open_time'])
            if 'strategy_timezone' in settings and not os.environ.get('STRATEGY_TIMEZONE'):
                strategy.timezone = pytz.timezone(str(settings['strategy_timezone']))
            if 'atr_period' in settings and not os.environ.get('ATR_PERIOD'):
                strategy.atr_period = int(settings['atr_period'])
            if 'atr_timeframe' in settings and not os.environ.get('ATR_TIMEFRAME'):
                strategy.atr_timeframe = str(settings['atr_timeframe'])
            if 'stop_atr_multiplier' in settings and not os.environ.get('STOP_ATR_MULTIPLIER'):
                strategy.stop_atr_multiplier = float(settings['stop_atr_multiplier'])
            if 'tp_atr_multiplier' in settings and not os.environ.get('TP_ATR_MULTIPLIER'):
                strategy.tp_atr_multiplier = float(settings['tp_atr_multiplier'])
            if 'breakeven_enabled' in settings and not os.environ.get('BREAKEVEN_ENABLED'):
                strategy.breakeven_enabled = bool(settings['breakeven_enabled'])
            if 'breakeven_profit_points' in settings and not os.environ.get('BREAKEVEN_PROFIT_POINTS'):
                strategy.breakeven_profit_points = float(settings['breakeven_profit_points'])
            if 'range_break_offset' in settings and not os.environ.get('RANGE_BREAK_OFFSET'):
                strategy.range_break_offset = float(settings['range_break_offset'])
            logger.debug(f"Applied overnight_range specific settings to {strategy_name} (env vars take precedence)")
    
    def _save_strategy_state(
        self,
        strategy_name: str,
        enabled: bool,
        symbols: Optional[List[str]] = None,
        persist: bool = True,
        strategy_specific_settings: Optional[Dict[str, Any]] = None,
        process_id: Optional[str] = None,
    ) -> None:
        """Persist state to database if available."""
        strategy = self.strategies.get(strategy_name)
        config_settings = self._serialize_config(strategy.config, strategy) if strategy else {}
        
        # Merge in any additional strategy-specific settings
        if strategy_specific_settings:
            config_settings.update(strategy_specific_settings)
        
        metadata = {
            "manager_saved_at": datetime.now(timezone.utc).isoformat(),
            "process_id": process_id  # Track which process started this strategy
        }
        
        last_started = datetime.now(timezone.utc) if enabled else None
        last_stopped = datetime.now(timezone.utc) if not enabled else None
        
        cached_entry = {
            "enabled": enabled,
            "symbols": symbols or (strategy.config.symbols if strategy else []),
            "settings": config_settings,
            "last_started": last_started.isoformat() if last_started else None,
            "last_stopped": last_stopped.isoformat() if last_stopped else None,
        }
        self._state_cache[strategy_name] = cached_entry
        
        if not persist:
            return
        
        db = getattr(self.trading_bot, 'db', None)
        if not db:
            return
        
        account_id = self._get_account_id()
        if not account_id:
            return
        
        db.save_strategy_state(
            account_id=account_id,
            strategy_name=strategy_name,
            enabled=enabled,
            symbols=symbols or (strategy.config.symbols if strategy else None),
            settings=config_settings,
            metadata=metadata,
            last_started=last_started,
            last_stopped=last_stopped,
        )
    
    def get_strategy(self, name: str) -> Optional[BaseStrategy]:
        """Get strategy by name."""
        return self.strategies.get(_normalize_strategy_id(name))
    
    def get_all_strategies(self) -> List[BaseStrategy]:
        """Get all loaded strategies."""
        return list(self.strategies.values())
    
    def get_active_strategies(self) -> List[BaseStrategy]:
        """Get currently active strategies."""
        return [self.strategies[name] for name in self.active_strategies if name in self.strategies]
    
    async def apply_persisted_states(self, auto_start: bool = True):
        """
        Load persisted state from the database and sync running strategies.

        Args:
            auto_start: When True (default), start/stop strategies to match
                        persisted enabled state. When False, only apply configs
                        without changing running state. Useful for callers that
                        want to start specific strategies exclusively.
        """
        db = getattr(self.trading_bot, 'db', None)
        account_id = self._get_account_id()
        
        if not db or not account_id:
            logger.info("⚠️  Skipping persisted state sync (database or account unavailable)")
            return
        
        logger.info("💾 Loading persisted strategy state...")
        raw_persisted = db.get_strategy_states(account_id)
        persisted_states = {_normalize_strategy_id(k): v for k, v in raw_persisted.items()}
        self._state_cache = dict(persisted_states)

        names = sorted(
            set(persisted_states.keys())
            | set(self._all_registered_names())
            | set(self.strategies.keys())
        )
        for name in names:
            if not self._is_known_strategy(name):
                logger.warning("Skipping persisted state for unknown strategy id: %s", name)
                continue
            strategy_class = self._ensure_strategy_class(name)
            strategy = self.strategies.get(name)
            state = persisted_states.get(name)
            
            if not strategy:
                # Instantiate strategy with base config
                config = StrategyConfig.from_env(name)
                strategy = strategy_class(self.trading_bot, config)
                self.strategies[name] = strategy
            
            # Apply persisted configuration overrides
            if state:
                if state.get('symbols'):
                    strategy.config.symbols = state['symbols']
                if state.get('settings'):
                    self._apply_config_settings(strategy, state['settings'])
                    # Apply strategy-specific settings
                    self._apply_strategy_specific_settings(strategy, state['settings'])
                strategy.config.enabled = bool(state.get('enabled', strategy.config.enabled))
            else:
                # Persist initial state for new strategies
                self._save_strategy_state(name, strategy.config.enabled, strategy.config.symbols, persist=True)
                state = {
                    "enabled": strategy.config.enabled,
                    "symbols": strategy.config.symbols,
                }
                persisted_states[name] = state
            
            # Ensure runtime state matches persisted toggle
            should_be_active = bool(state.get('enabled'))
            is_active = name in self.active_strategies
            
            if auto_start:
                if should_be_active and not is_active:
                    logger.info(f"▶️  Auto-starting strategy from persisted state: {name}")
                    await self.start_strategy(name, symbols=strategy.config.symbols, persist=False)
                elif not should_be_active and is_active:
                    logger.info(f"⏹️  Auto-stopping strategy from persisted state: {name}")
                    await self.stop_strategy(name, persist=False)
            else:
                # When auto_start is False, just log the intended state without changing runtime
                logger.info(f"ℹ️  apply_persisted_states(auto_start=False): skipping auto-start/stop for {name} "
                            f"(enabled={should_be_active}, running={is_active})")
    
    def get_strategy_summaries(self) -> List[Dict[str, Any]]:
        """
        Build a summary list combining runtime and persisted state.
        """
        summaries: List[Dict[str, Any]] = []
        account_id = self._get_account_id()
        db = getattr(self.trading_bot, 'db', None)
        if db and account_id:
            raw_persisted = db.get_strategy_states(account_id)
            persisted = {_normalize_strategy_id(k): v for k, v in raw_persisted.items()}
            self._state_cache = dict(persisted)
        else:
            persisted = {_normalize_strategy_id(k): v for k, v in self._state_cache.items()}

        for name in self.catalog_strategy_names():
            strategy = self.strategies.get(name)
            state = persisted.get(name, {})
            spec = BUILTIN_STRATEGY_SPECS.get(name)
            strategy_class = self.strategy_classes.get(name)

            symbols = list(state.get('symbols') or (strategy.config.symbols if strategy else []))
            if not symbols and spec:
                try:
                    symbols = list(StrategyConfig.from_env(name).symbols)
                except Exception:
                    symbols = []

            enabled = state.get('enabled')
            if enabled is None and strategy:
                enabled = strategy.config.enabled
            elif enabled is None and spec:
                try:
                    enabled = bool(StrategyConfig.from_env(name).enabled)
                except Exception:
                    enabled = False
            enabled = bool(enabled)

            is_running = name in self.active_strategies
            status = 'running' if is_running else ('enabled' if enabled else 'disabled')

            settings = dict(state.get('settings') or {})
            if not settings and strategy:
                settings = {
                    "position_size": strategy.config.position_size,
                    "max_positions": strategy.config.max_positions,
                }
            elif strategy:
                if 'position_size' not in settings:
                    settings['position_size'] = strategy.config.position_size
                if 'max_positions' not in settings:
                    settings['max_positions'] = strategy.config.max_positions

            if spec:
                description = spec[2]
            else:
                description = (getattr(strategy_class, '__doc__', '') or '') if strategy_class else ''

            summaries.append({
                "name": name,
                "description": description,
                "status": status,
                "enabled": enabled,
                "is_running": is_running,
                "symbols": symbols,
                "settings": settings,
                "last_started": state.get('last_started'),
                "last_stopped": state.get('last_stopped'),
            })
        
        return summaries
    
    async def start_strategy(self, name: str, symbols: List[str] = None, persist: bool = True,
                            risk_config: Optional[Dict[str, Dict[str, Any]]] = None,
                            process_id: Optional[str] = None):
        """
        Start a specific strategy.
        
        Args:
            name: Strategy name
            symbols: Optional list of symbols to override config
            persist: Whether to persist state to database
            risk_config: Optional per-instrument risk configuration.
                        Format: {'SYMBOL': {'max_quantity': int, 'cooldown': float, 'max_pending': int}}
            process_id: Optional process ID that started this strategy (for tracking external processes)
        
        Returns:
            tuple: (success: bool, message: str)
        """
        n = _normalize_strategy_id(name)
        if n not in self.strategies:
            if not self._is_known_strategy(n):
                catalog = self.builtin_strategy_ids()
                reg = self._all_registered_names()
                logger.error(
                    "❌ Strategy not found: %s. Built-ins: %s. Pre-registered: %s",
                    name,
                    ", ".join(catalog) if catalog else "none",
                    ", ".join(reg) if reg else "none",
                )
                return False, (
                    f"Strategy not found: {name}. "
                    f"Built-ins: {', '.join(catalog) if catalog else 'none'}; "
                    f"pre-registered: {', '.join(reg) if reg else 'none'}"
                )
            logger.info("Creating strategy instance for %s", n)
            try:
                strategy_class = self._ensure_strategy_class(n)
                config = StrategyConfig.from_env(n)
                if symbols:
                    config.symbols = symbols
                if risk_config:
                    config.risk_config = risk_config
                config.enabled = True
                strategy = strategy_class(self.trading_bot, config)
                self.strategies[n] = strategy
                logger.info("✅ Created strategy instance: %s", n)
            except Exception as e:
                logger.error("❌ Failed to create strategy instance for %s: %s", n, e)
                logger.exception(e)
                return False, f"Failed to create strategy instance: {str(e)}"

        if n in self.active_strategies:
            logger.warning(f"⚠️  Strategy already active: {n}")
            return False, f"Strategy already active: {n}"
        
        # Check concurrent limit
        if len(self.active_strategies) >= self.max_concurrent_strategies:
            logger.error(f"❌ Max concurrent strategies limit reached ({self.max_concurrent_strategies})")
            return False, f"Max concurrent strategies limit reached ({self.max_concurrent_strategies})"
        
        strategy = self.strategies[n]

        # Update timeframe if provided and strategy supports it (e.g., SimpleCandleStrategy)
        timeframe = os.environ.get('SIMPLE_CANDLE_TIMEFRAME')
        if timeframe and hasattr(strategy, 'timeframe'):
            strategy.timeframe = timeframe
            logger.info(f"⏰ Updated timeframe for {n} to: {timeframe}")

        # Override symbols if provided
        if symbols:
            strategy.config.symbols = symbols

        # Set risk config if provided
        if risk_config:
            strategy.config.risk_config = risk_config
            strategy.set_risk_config(risk_config)

        # Track start time for UI/runtime display (strategies don't set this consistently)
        try:
            now = datetime.now(timezone.utc)
            setattr(strategy, "_start_time", now)
            setattr(strategy, "start_time", now)
        except Exception:
            pass

        strategy.status = StrategyStatus.ACTIVE
        self.active_strategies.append(n)
        strategy.config.enabled = True

        # Strategies with their own event loop can implement an async start() or run() hook
        custom_start = getattr(strategy, 'start', None)
        custom_run = getattr(strategy, 'run', None)

        if callable(custom_start) and asyncio.iscoroutinefunction(custom_start):
            await custom_start(symbols or strategy.config.symbols)
            logger.debug("▶️  Invoked custom start() for strategy %s", n)
        elif callable(custom_run) and asyncio.iscoroutinefunction(custom_run):
            task = asyncio.create_task(custom_run())
            self._tasks.append(task)
            logger.debug("▶️  Created task for custom run() method of strategy %s", n)
        else:
            task = asyncio.create_task(self._run_strategy(strategy))
            self._tasks.append(task)
            logger.debug("▶️  Using standard monitoring loop for strategy %s", n)

        logger.info("🚀 Started strategy: %s", n)
        self._save_strategy_state(n, enabled=True, symbols=strategy.config.symbols, persist=persist, process_id=process_id)
        await self._publish_strategy_lifecycle(EventType.STRATEGY_STARTED, n)
        return True, f"Strategy started: {n} on {', '.join(strategy.config.symbols)}"
    
    async def stop_strategy(self, name: str, persist: bool = True):
        """
        Stop a specific strategy.
        
        Args:
            name: Strategy name
        
        Returns:
            tuple: (success: bool, message: str)
        """
        n = _normalize_strategy_id(name)
        if n not in self.active_strategies:
            logger.warning(f"⚠️  Strategy not active: {n}")
            return False, f"Strategy not active: {n}"

        strategy = self.strategies[n]
        strategy.status = StrategyStatus.IDLE
        self.active_strategies.remove(n)
        strategy.config.enabled = False

        # Clear start time for UI
        try:
            setattr(strategy, "_start_time", None)
            setattr(strategy, "start_time", None)
        except Exception:
            pass

        # Cleanup strategy
        await strategy.cleanup()

        logger.info("🛑 Stopped strategy: %s", n)
        self._save_strategy_state(n, enabled=False, symbols=strategy.config.symbols, persist=persist)
        await self._publish_strategy_lifecycle(EventType.STRATEGY_STOPPED, n)
        return True, f"Strategy stopped: {n}"
    
    async def start_all_strategies(self):
        """Start all enabled strategies."""
        logger.info("🚀 Starting all strategies...")
        results = {}
        for name in self.strategies.keys():
            success, message = await self.start_strategy(name)
            results[name] = (success, message)
        return results
    
    async def stop_all_strategies(self):
        """Stop all active strategies."""
        logger.info("🛑 Stopping all strategies...")
        results = {}
        for name in list(self.active_strategies):
            success, message = await self.stop_strategy(name)
            results[name] = (success, message)
        
        # Cancel all tasks
        for task in self._tasks:
            task.cancel()
        self._tasks.clear()
        
        return results
    
    # Backward compatibility aliases
    async def start_all(self):
        """Alias for start_all_strategies()."""
        return await self.start_all_strategies()
    
    async def stop_all(self):
        """Alias for stop_all_strategies()."""
        return await self.stop_all_strategies()
    
    async def _run_strategy(self, strategy: BaseStrategy):
        """
        Run a strategy's main loop.
        
        Args:
            strategy: Strategy instance to run
        """
        logger.info(f"▶️  Running strategy loop: {strategy.config.name}")
        
        try:
            while strategy.status == StrategyStatus.ACTIVE:
                # Optional hot-reload for TOML configs (cheap mtime poll).
                if os.environ.get("STRATEGY_CONFIG_RELOAD", "").strip().lower() in ("1", "true", "yes", "on"):
                    cfg = getattr(strategy, "_cfg", None)
                    if cfg is not None and hasattr(cfg, "maybe_reload"):
                        try:
                            if cfg.maybe_reload():
                                bus = getattr(self.trading_bot, "event_bus", None)
                                if bus and getattr(bus, "_running", False):
                                    try:
                                        await bus.publish(
                                            Event(
                                                type=EventType.STRATEGY_CONFIG_RELOADED,
                                                data={"strategy": strategy.config.name, "strategy_name": strategy.config.name},
                                                source="strategy_manager",
                                            )
                                        )
                                    except Exception as exc:
                                        logger.debug("Could not publish STRATEGY_CONFIG_RELOADED: %s", exc)
                        except Exception as exc:
                            logger.debug("Strategy config reload check failed for %s: %s", strategy.config.name, exc)

                # Process each symbol
                for symbol in strategy.config.symbols:
                    try:
                        # Check if should trade
                        should_trade, reason = strategy.should_trade(symbol)
                        if not should_trade:
                            logger.debug(f"⏸️  {strategy.config.name} skipping {symbol}: {reason}")
                            continue
                        
                        # Analyze market
                        signal = await strategy.analyze(symbol)
                        
                        # Execute if signal present
                        if signal:
                            logger.debug(
                                "📊 %s signal for %s: %s",
                                strategy.config.name,
                                symbol,
                                signal["action"],
                            )
                            
                            # Broadcast signal to GUI if available
                            signal_data = {
                                'type': signal.get('action', 'SIGNAL'),
                                'strategy': strategy.config.name,
                                'symbol': symbol,
                                'message': signal.get('reason', f"{signal.get('action', 'SIGNAL')} signal generated"),
                                'price': signal.get('entry_price') or signal.get('price'),
                                'entry_price': signal.get('entry_price'),
                                'stop_loss': signal.get('stop_loss'),
                                'take_profit': signal.get('take_profit'),
                                'direction': signal.get('action', 'SIGNAL'),
                                'timestamp': datetime.now(timezone.utc).isoformat()
                            }
                            
                            # Send Discord notification for strategy signal
                            try:
                                if hasattr(self.trading_bot, 'discord_notifier') and self.trading_bot.discord_notifier:
                                    account_name = 'Unknown'
                                    if hasattr(self.trading_bot, 'selected_account') and self.trading_bot.selected_account:
                                        if isinstance(self.trading_bot.selected_account, dict):
                                            account_name = self.trading_bot.selected_account.get('name', 'Unknown')
                                        else:
                                            account_name = str(self.trading_bot.selected_account)
                                    
                                    details = {
                                        'entry_price': signal.get('entry_price'),
                                        'stop_loss': signal.get('stop_loss'),
                                        'take_profit': signal.get('take_profit'),
                                        'reason': signal.get('reason', ''),
                                        'strategy': strategy.config.name
                                    }
                                    asyncio.create_task(
                                        self.trading_bot.discord_notifier.send_signal_notification(
                                            signal_type=signal.get('action', 'SIGNAL'),
                                            symbol=symbol,
                                            account_name=account_name,
                                            details=details,
                                        )
                                    )
                                    logger.debug(f"Discord notification queued for {signal.get('action')} signal on {symbol} from {strategy.config.name}")
                            except Exception as e:
                                logger.debug(f"Could not send Discord notification for signal: {e}")
                            
                            # Broadcast signal to GUI if available
                            try:
                                # Try to import and broadcast signal to GUI
                                import gui.chart_html as chart_html_module
                                if hasattr(chart_html_module, 'broadcast_update'):
                                    # Ensure broadcast_update is called correctly
                                    broadcast_func = getattr(chart_html_module, 'broadcast_update', None)
                                    if broadcast_func:
                                        await broadcast_func({'type': 'signal', 'data': signal_data})
                                        logger.debug(f"Broadcasted signal to GUI: {signal_data['type']} {symbol} from {strategy.config.name}")
                                    else:
                                        logger.warning(f"broadcast_update function not found in chart_html_module")
                            except (ImportError, AttributeError, Exception) as e:
                                # GUI not available or broadcast not set up - log the actual error
                                logger.warning(f"Could not broadcast signal to GUI: {e}")
                                import traceback
                                logger.debug(traceback.format_exc())
                            
                            await strategy.execute(signal)
                    
                    except Exception as e:
                        logger.error(f"❌ Error processing {symbol} in {strategy.config.name}: {e}")
                
                # Manage existing positions
                try:
                    await strategy.manage_positions()
                except Exception as e:
                    logger.error(f"❌ Error managing positions in {strategy.config.name}: {e}")
                
                # Wait before next iteration
                await asyncio.sleep(60)  # Check every minute
        
        except asyncio.CancelledError:
            logger.info(f"🛑 Strategy loop cancelled: {strategy.config.name}")
        except Exception as e:
            logger.error(f"❌ Strategy loop error: {strategy.config.name} - {e}")
            strategy.status = StrategyStatus.ERROR
    
    async def auto_select_strategies(self):
        """
        Automatically select and activate strategies based on market conditions.
        
        This runs as a background task when auto_select_enabled=true.
        """
        if not self.auto_select_enabled:
            return
        
        logger.info("🤖 Starting auto-strategy selection...")
        
        while self._running:
            try:
                # Analyze market conditions for each symbol
                market_conditions = {}
                
                for strategy in self.strategies.values():
                    for symbol in strategy.config.symbols:
                        condition = strategy.get_market_condition(symbol)
                        market_conditions[symbol] = condition
                        logger.debug(f"📊 {symbol} condition: {condition.value}")
                
                # Find best strategies for current conditions
                best_strategies = self._select_best_strategies(market_conditions)
                
                # Activate/deactivate strategies based on selection
                for strategy_name in best_strategies:
                    if strategy_name not in self.active_strategies:
                        logger.info(f"🎯 Auto-activating strategy: {strategy_name}")
                        await self.start_strategy(strategy_name)
                
                # Deactivate strategies no longer suitable
                for strategy_name in list(self.active_strategies):
                    if strategy_name not in best_strategies:
                        logger.info(f"⏸️  Auto-deactivating strategy: {strategy_name}")
                        await self.stop_strategy(strategy_name)
                
            except Exception as e:
                logger.error(f"❌ Auto-selection error: {e}")
            
            # Wait before next check
            await asyncio.sleep(self.market_condition_check_interval)
    
    def _select_best_strategies(self, market_conditions: Dict[str, MarketCondition]) -> List[str]:
        """
        Select best strategies for current market conditions.
        
        Args:
            market_conditions: Dict of {symbol: MarketCondition}
        
        Returns:
            List of strategy names to activate
        """
        strategy_scores = {}
        
        for name, strategy in self.strategies.items():
            if not strategy.config.enabled:
                continue
            
            score = 0
            
            # Score based on preferred conditions
            for symbol in strategy.config.symbols:
                condition = market_conditions.get(symbol, MarketCondition.UNKNOWN)
                
                if condition in strategy.config.preferred_conditions:
                    score += 2
                elif condition in strategy.config.avoid_conditions:
                    score -= 3
            
            # Score based on recent performance
            if strategy.metrics.win_rate > 0.6:
                score += 2
            elif strategy.metrics.win_rate < 0.4:
                score -= 1
            
            if strategy.metrics.profit_factor > 1.5:
                score += 1
            
            strategy_scores[name] = score
        
        # Sort by score and select top strategies
        sorted_strategies = sorted(strategy_scores.items(), key=lambda x: x[1], reverse=True)
        best_strategies = [name for name, score in sorted_strategies[:self.max_concurrent_strategies] if score > 0]
        
        return best_strategies
    
    def get_aggregated_metrics(self) -> Dict:
        """Get combined metrics across all strategies."""
        total_trades = sum(s.metrics.total_trades for s in self.strategies.values())
        total_pnl = sum(s.metrics.total_pnl for s in self.strategies.values())
        winning_trades = sum(s.metrics.winning_trades for s in self.strategies.values())
        
        aggregate_win_rate = winning_trades / total_trades if total_trades > 0 else 0.0
        
        return {
            "total_strategies": len(self.strategies),
            "active_strategies": len(self.active_strategies),
            "total_trades": total_trades,
            "total_pnl": total_pnl,
            "aggregate_win_rate": aggregate_win_rate,
            "best_strategy": max(self.strategies.values(), key=lambda s: s.metrics.total_pnl).config.name if self.strategies else None,
            "strategies": {
                name: strategy.get_status() for name, strategy in self.strategies.items()
            }
        }
    
    def get_status(self) -> Dict:
        """Get manager status with detailed strategy information."""
        # Get aggregated metrics
        metrics = self.get_aggregated_metrics()

        # Get process information for strategies
        db = getattr(self.trading_bot, 'db', None)
        account_id = self._get_account_id()
        strategy_process_info = {}
        
        if db and account_id:
            # Get strategy states to check process_id
            states = db.get_strategy_states(account_id)
            for strategy_name, state in states.items():
                metadata = state.get('metadata', {})
                process_id = metadata.get('process_id') if isinstance(metadata, dict) else None
                if process_id:
                    # Check if process is still running
                    process_states = db.get_process_states('strategy_executor')
                    process_running = any(p.get('process_id') == process_id and p.get('status') == 'running' 
                                         for p in process_states)
                    strategy_process_info[strategy_name] = {
                        'process_id': process_id,
                        'process_running': process_running,
                        'started_externally': True
                    }

        # Get individual strategy statuses with monitoring info
        strategy_statuses = {}
        for name, strategy in self.strategies.items():
            status = strategy.get_status()
            # Add whether it's actually in active list (monitoring)
            status['monitoring'] = name in self.active_strategies
            # Add task info if available
            running_tasks = sum(1 for task in self._tasks if not task.done())
            status['has_running_task'] = running_tasks > 0 and name in self.active_strategies
            
            # Add process information if available
            if name in strategy_process_info:
                status.update(strategy_process_info[name])
            else:
                status['started_externally'] = False
                status['process_id'] = None
            
            strategy_statuses[name] = status

        return {
            "total_strategies": len(self.strategies),
            "active_strategies": len(self.active_strategies),
            "running_tasks": sum(1 for task in self._tasks if not task.done()),
            "total_positions": sum(len(s.active_positions) for s in self.strategies.values()),
            "strategies": strategy_statuses,
            "auto_select_enabled": self.auto_select_enabled,
            "max_concurrent": self.max_concurrent_strategies,
            "registered_strategies": self._all_registered_names(),
            "loaded_strategies": list(self.strategies.keys()),
            "active_strategy_names": self.active_strategies
        }

