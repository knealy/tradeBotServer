"""
Strategy Manager - Dynamic Strategy Loading and Coordination

Manages multiple trading strategies, selects appropriate strategies based on
market conditions, and coordinates their execution.
"""

import os
import logging
import asyncio
from typing import Dict, List, Optional, Type, Any
from datetime import datetime, timezone
from strategies.strategy_base import BaseStrategy, StrategyConfig, StrategyStatus, MarketCondition

logger = logging.getLogger(__name__)


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
        self.active_strategies: List[str] = []
        
        # Global settings
        self.max_concurrent_strategies = int(os.getenv('MAX_CONCURRENT_STRATEGIES', '3'))
        self.auto_select_enabled = os.getenv('AUTO_SELECT_STRATEGIES', 'false').lower() == 'true'
        self.market_condition_check_interval = int(os.getenv('MARKET_CONDITION_CHECK_INTERVAL', '300'))  # 5 minutes
        
        # State
        self._tasks: List[asyncio.Task] = []
        self._running = False
        self._state_cache: Dict[str, Dict] = {}
        
        logger.info("✨ Strategy Manager initialized")
    
    def register_strategy(self, name: str, strategy_class: Type[BaseStrategy]):
        """
        Register a strategy class.
        
        Args:
            name: Strategy identifier
            strategy_class: Strategy class (not instance)
        """
        self.strategy_classes[name] = strategy_class
        self.available_strategies[name] = strategy_class  # Keep alias in sync
        logger.info(f"📝 Registered strategy: {name}")
    
    def load_strategies(self):
        """
        Load all enabled strategies from environment configuration.
        """
        logger.info("🔄 Loading strategies from configuration...")
        
        for name, strategy_class in self.strategy_classes.items():
            try:
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
        
        logger.info(f"📊 Total strategies loaded: {len(self.strategies)}/{len(self.strategy_classes)}")
    
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
            persisted_states = db.get_strategy_states(account_id)
            logger.info(f"📋 Loaded {len(persisted_states)} persisted strategy states for account {account_id}")

        for name, strategy_class in self.strategy_classes.items():
            try:
                # Check persisted state first (per-account configuration)
                persisted_state = persisted_states.get(name)
                should_start = False
                symbols = None
                config_source = "persisted"
                
                if persisted_state:
                    # Use persisted state if available
                    should_start = bool(persisted_state.get('enabled', False))
                    symbols = persisted_state.get('symbols') or []
                    logger.debug(f"📝 Strategy {name}: using persisted state (enabled={should_start}, symbols={symbols})")
                else:
                    # Fallback to environment variables if no persisted state
                    config = StrategyConfig.from_env(name)
                    should_start = config.enabled
                    symbols = config.symbols
                    config_source = "environment"
                    logger.debug(f"🌍 Strategy {name}: using environment config (enabled={should_start}, symbols={symbols})")

                if should_start and name not in self.active_strategies:
                    # Strategy should be running but isn't
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
        if name not in self.available_strategies:
            return False, f"Unknown strategy: {name}"

        # Get or create strategy instance
        if name not in self.strategies:
            strategy_class = self.available_strategies[name]
            config = StrategyConfig.from_env(name)
            strategy = strategy_class(self.trading_bot, config)
            self.strategies[name] = strategy

        strategy = self.strategies[name]

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
            name,
            strategy.config.enabled,
            strategy.config.symbols,
            persist=True,
            strategy_specific_settings=strategy_specific_settings
        )

        logger.info(f"📝 Updated config for {name}: symbols={strategy.config.symbols}, "
                   f"position_size={strategy.config.position_size}, max_positions={strategy.config.max_positions}")

        return True, f"Configuration updated for {name}"

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
        """Apply strategy-specific parameters (e.g., overnight time range, ATR settings)."""
        if not settings:
            return
        
        strategy_name = strategy.__class__.__name__.lower()
        
        # Overnight Range Strategy specific settings
        if 'overnightrange' in strategy_name or strategy_name == 'overnight_range':
            import pytz
            if 'overnight_start_time' in settings:
                strategy.overnight_start = str(settings['overnight_start_time'])
            if 'overnight_end_time' in settings:
                strategy.overnight_end = str(settings['overnight_end_time'])
            if 'market_open_time' in settings:
                strategy.market_open_time = str(settings['market_open_time'])
            if 'strategy_timezone' in settings:
                strategy.timezone = pytz.timezone(str(settings['strategy_timezone']))
            if 'atr_period' in settings:
                strategy.atr_period = int(settings['atr_period'])
            if 'atr_timeframe' in settings:
                strategy.atr_timeframe = str(settings['atr_timeframe'])
            if 'stop_atr_multiplier' in settings:
                strategy.stop_atr_multiplier = float(settings['stop_atr_multiplier'])
            if 'tp_atr_multiplier' in settings:
                strategy.tp_atr_multiplier = float(settings['tp_atr_multiplier'])
            if 'breakeven_enabled' in settings:
                strategy.breakeven_enabled = bool(settings['breakeven_enabled'])
            if 'breakeven_profit_points' in settings:
                strategy.breakeven_profit_points = float(settings['breakeven_profit_points'])
            if 'range_break_offset' in settings:
                strategy.range_break_offset = float(settings['range_break_offset'])
            logger.debug(f"Applied overnight_range specific settings to {strategy_name}")
    
    def _save_strategy_state(
        self,
        strategy_name: str,
        enabled: bool,
        symbols: Optional[List[str]] = None,
        persist: bool = True,
        strategy_specific_settings: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Persist state to database if available."""
        strategy = self.strategies.get(strategy_name)
        config_settings = self._serialize_config(strategy.config, strategy) if strategy else {}
        
        # Merge in any additional strategy-specific settings
        if strategy_specific_settings:
            config_settings.update(strategy_specific_settings)
        
        metadata = {
            "manager_saved_at": datetime.now(timezone.utc).isoformat()
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
        return self.strategies.get(name)
    
    def get_all_strategies(self) -> List[BaseStrategy]:
        """Get all loaded strategies."""
        return list(self.strategies.values())
    
    def get_active_strategies(self) -> List[BaseStrategy]:
        """Get currently active strategies."""
        return [self.strategies[name] for name in self.active_strategies if name in self.strategies]
    
    async def apply_persisted_states(self):
        """
        Load persisted state from the database and sync running strategies.
        """
        db = getattr(self.trading_bot, 'db', None)
        account_id = self._get_account_id()
        
        if not db or not account_id:
            logger.info("⚠️  Skipping persisted state sync (database or account unavailable)")
            return
        
        logger.info("💾 Loading persisted strategy state...")
        persisted_states = db.get_strategy_states(account_id)
        self._state_cache = dict(persisted_states)
        
        for name, strategy_class in self.available_strategies.items():
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
            
            if should_be_active and not is_active:
                logger.info(f"▶️  Auto-starting strategy from persisted state: {name}")
                await self.start_strategy(name, symbols=strategy.config.symbols, persist=False)
            elif not should_be_active and is_active:
                logger.info(f"⏹️  Auto-stopping strategy from persisted state: {name}")
                await self.stop_strategy(name, persist=False)
    
    def get_strategy_summaries(self) -> List[Dict[str, Any]]:
        """
        Build a summary list combining runtime and persisted state.
        """
        summaries: List[Dict[str, Any]] = []
        account_id = self._get_account_id()
        persisted = self._state_cache
        
        # Refresh cache from DB if available
        db = getattr(self.trading_bot, 'db', None)
        if db and account_id:
            persisted = db.get_strategy_states(account_id)
            self._state_cache = dict(persisted)
        
        for name, strategy_class in self.available_strategies.items():
            strategy = self.strategies.get(name)
            state = persisted.get(name, {})
            
            symbols = state.get('symbols') or (strategy.config.symbols if strategy else [])
            enabled = state.get('enabled')
            if enabled is None and strategy:
                enabled = strategy.config.enabled
            enabled = bool(enabled)
            
            is_running = name in self.active_strategies
            status = 'running' if is_running else ('enabled' if enabled else 'disabled')

            # Get settings from persisted state or strategy config
            settings = state.get('settings') or {}
            if not settings and strategy:
                # Use strategy config values if no persisted settings
                settings = {
                    "position_size": strategy.config.position_size,
                    "max_positions": strategy.config.max_positions,
                }
            elif strategy:
                # Ensure position_size and max_positions are always present
                if 'position_size' not in settings:
                    settings['position_size'] = strategy.config.position_size
                if 'max_positions' not in settings:
                    settings['max_positions'] = strategy.config.max_positions

            summaries.append({
                "name": name,
                "description": getattr(strategy_class, '__doc__', '') or '',
                "status": status,
                "enabled": enabled,
                "is_running": is_running,
                "symbols": symbols,
                "settings": settings,
                "last_started": state.get('last_started'),
                "last_stopped": state.get('last_stopped'),
            })
        
        return summaries
    
    async def start_strategy(self, name: str, symbols: List[str] = None, persist: bool = True):
        """
        Start a specific strategy.
        
        Args:
            name: Strategy name
            symbols: Optional list of symbols to override config
        
        Returns:
            tuple: (success: bool, message: str)
        """
        # Check if strategy exists in instances dict
        if name not in self.strategies:
            # Try to create instance from registered class
            if name in self.available_strategies:
                logger.info(f"Creating strategy instance for {name}")
                try:
                    strategy_class = self.available_strategies[name]
                    # Use from_env to create proper config with all required fields
                    from strategies.strategy_base import StrategyConfig
                    config = StrategyConfig.from_env(name)
                    # Override symbols if provided
                    if symbols:
                        config.symbols = symbols
                    # Ensure enabled
                    config.enabled = True
                    strategy = strategy_class(self.trading_bot, config)
                    self.strategies[name] = strategy
                    logger.info(f"✅ Created strategy instance: {name}")
                except Exception as e:
                    logger.error(f"❌ Failed to create strategy instance for {name}: {e}")
                    logger.exception(e)
                    return False, f"Failed to create strategy instance: {str(e)}"
            else:
                available = list(self.available_strategies.keys()) + list(self.strategies.keys())
                logger.error(f"❌ Strategy not found: {name}. Available: {available}")
                return False, f"Strategy not found: {name}. Available: {', '.join(available) if available else 'none'}"
        
        if name in self.active_strategies:
            logger.warning(f"⚠️  Strategy already active: {name}")
            return False, f"Strategy already active: {name}"
        
        # Check concurrent limit
        if len(self.active_strategies) >= self.max_concurrent_strategies:
            logger.error(f"❌ Max concurrent strategies limit reached ({self.max_concurrent_strategies})")
            return False, f"Max concurrent strategies limit reached ({self.max_concurrent_strategies})"
        
        strategy = self.strategies[name]
        
        # Override symbols if provided
        if symbols:
            strategy.config.symbols = symbols
        
        strategy.status = StrategyStatus.ACTIVE
        self.active_strategies.append(name)
        strategy.config.enabled = True
        
        # Strategies with their own event loop can implement an async start() hook
        custom_start = getattr(strategy, 'start', None)
        if callable(custom_start) and asyncio.iscoroutinefunction(custom_start):
            await custom_start(symbols or strategy.config.symbols)
            logger.debug(f"▶️  Invoked custom start() for strategy {name}")
        else:
            task = asyncio.create_task(self._run_strategy(strategy))
            self._tasks.append(task)
        
        logger.info(f"🚀 Started strategy: {name}")
        self._save_strategy_state(name, enabled=True, symbols=strategy.config.symbols, persist=persist)
        return True, f"Strategy started: {name} on {', '.join(strategy.config.symbols)}"
    
    async def stop_strategy(self, name: str, persist: bool = True):
        """
        Stop a specific strategy.
        
        Args:
            name: Strategy name
        
        Returns:
            tuple: (success: bool, message: str)
        """
        if name not in self.active_strategies:
            logger.warning(f"⚠️  Strategy not active: {name}")
            return False, f"Strategy not active: {name}"
        
        strategy = self.strategies[name]
        strategy.status = StrategyStatus.IDLE
        self.active_strategies.remove(name)
        strategy.config.enabled = False
        
        # Cleanup strategy
        await strategy.cleanup()
        
        logger.info(f"🛑 Stopped strategy: {name}")
        self._save_strategy_state(name, enabled=False, symbols=strategy.config.symbols, persist=persist)
        return True, f"Strategy stopped: {name}"
    
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
                            logger.info(f"📊 {strategy.config.name} signal for {symbol}: {signal['action']}")
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
        
        # Get individual strategy statuses
        strategy_statuses = {}
        for name, strategy in self.strategies.items():
            strategy_statuses[name] = strategy.get_status()
        
        return {
            "total_strategies": len(self.strategies),
            "active_strategies": len(self.active_strategies),
            "total_positions": sum(len(s.active_positions) for s in self.strategies.values()),
            "strategies": strategy_statuses,
            "auto_select_enabled": self.auto_select_enabled,
            "max_concurrent": self.max_concurrent_strategies,
            "registered_strategies": list(self.strategy_classes.keys()),
            "loaded_strategies": list(self.strategies.keys()),
            "active_strategy_names": self.active_strategies
        }

