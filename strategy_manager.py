"""
Strategy Manager - Dynamic Strategy Loading and Coordination

Manages multiple trading strategies, selects appropriate strategies based on
market conditions, and coordinates their execution.
"""

import os
import logging
import asyncio
from typing import Dict, List, Optional, Type
from datetime import datetime
from strategy_base import BaseStrategy, StrategyConfig, StrategyStatus, MarketCondition

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
        self.active_strategies: List[str] = []
        
        # Global settings
        self.max_concurrent_strategies = int(os.getenv('MAX_CONCURRENT_STRATEGIES', '3'))
        self.auto_select_enabled = os.getenv('AUTO_SELECT_STRATEGIES', 'false').lower() == 'true'
        self.market_condition_check_interval = int(os.getenv('MARKET_CONDITION_CHECK_INTERVAL', '300'))  # 5 minutes
        
        # State
        self._tasks: List[asyncio.Task] = []
        self._running = False
        
        logger.info("✨ Strategy Manager initialized")
    
    def register_strategy(self, name: str, strategy_class: Type[BaseStrategy]):
        """
        Register a strategy class.
        
        Args:
            name: Strategy identifier
            strategy_class: Strategy class (not instance)
        """
        self.strategy_classes[name] = strategy_class
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
    
    def get_strategy(self, name: str) -> Optional[BaseStrategy]:
        """Get strategy by name."""
        return self.strategies.get(name)
    
    def get_all_strategies(self) -> List[BaseStrategy]:
        """Get all loaded strategies."""
        return list(self.strategies.values())
    
    def get_active_strategies(self) -> List[BaseStrategy]:
        """Get currently active strategies."""
        return [self.strategies[name] for name in self.active_strategies if name in self.strategies]
    
    async def start_strategy(self, name: str) -> bool:
        """
        Start a specific strategy.
        
        Args:
            name: Strategy name
        
        Returns:
            bool: True if started successfully
        """
        if name not in self.strategies:
            logger.error(f"❌ Strategy not found: {name}")
            return False
        
        if name in self.active_strategies:
            logger.warning(f"⚠️  Strategy already active: {name}")
            return False
        
        # Check concurrent limit
        if len(self.active_strategies) >= self.max_concurrent_strategies:
            logger.error(f"❌ Max concurrent strategies limit reached ({self.max_concurrent_strategies})")
            return False
        
        strategy = self.strategies[name]
        strategy.status = StrategyStatus.ACTIVE
        self.active_strategies.append(name)
        
        # Start strategy monitoring task
        task = asyncio.create_task(self._run_strategy(strategy))
        self._tasks.append(task)
        
        logger.info(f"🚀 Started strategy: {name}")
        return True
    
    async def stop_strategy(self, name: str) -> bool:
        """
        Stop a specific strategy.
        
        Args:
            name: Strategy name
        
        Returns:
            bool: True if stopped successfully
        """
        if name not in self.active_strategies:
            logger.warning(f"⚠️  Strategy not active: {name}")
            return False
        
        strategy = self.strategies[name]
        strategy.status = StrategyStatus.IDLE
        self.active_strategies.remove(name)
        
        # Cleanup strategy
        await strategy.cleanup()
        
        logger.info(f"🛑 Stopped strategy: {name}")
        return True
    
    async def start_all(self):
        """Start all loaded strategies."""
        logger.info("🚀 Starting all strategies...")
        for name in self.strategies.keys():
            await self.start_strategy(name)
    
    async def stop_all(self):
        """Stop all active strategies."""
        logger.info("🛑 Stopping all strategies...")
        for name in list(self.active_strategies):
            await self.stop_strategy(name)
        
        # Cancel all tasks
        for task in self._tasks:
            task.cancel()
        self._tasks.clear()
    
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
        """Get manager status."""
        return {
            "auto_select_enabled": self.auto_select_enabled,
            "max_concurrent": self.max_concurrent_strategies,
            "registered_strategies": list(self.strategy_classes.keys()),
            "loaded_strategies": list(self.strategies.keys()),
            "active_strategies": self.active_strategies,
            "metrics": self.get_aggregated_metrics()
        }

