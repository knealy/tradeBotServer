#!/usr/bin/env python3
"""
Strategy Executor - Standalone process for running automated strategies.

This runs as a separate process and can be controlled via CLI commands or database state.

Usage:
    python core/strategy_executor.py --strategy=mean_reversion --symbols=MNQ,MES --account_id=12694476
    python core/strategy_executor.py --all --account_id=12694476
"""

import sys
import os
import asyncio
import logging
import argparse
from pathlib import Path
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from trading_bot import TopStepXTradingBot
from infrastructure.database import get_database

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('strategy_executor.log'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


class StrategyExecutor:
    """Standalone strategy executor process."""
    
    def __init__(self, trading_bot: TopStepXTradingBot):
        """Initialize strategy executor."""
        self.trading_bot = trading_bot
        self.running_strategies: Dict[str, Any] = {}
        self.is_running = False
    
    async def start_strategy(self, strategy_name: str, symbols: Optional[List[str]] = None, account_id: Optional[str] = None) -> bool:
        """Start a strategy."""
        try:
            if not hasattr(self.trading_bot, 'strategy_manager'):
                logger.error("Strategy manager not available")
                return False
            
            # Switch account if provided
            if account_id:
                await self.trading_bot.switch_account(account_id)
            
            # Start strategy
            success, message = await self.trading_bot.strategy_manager.start_strategy(
                strategy_name,
                symbols=symbols
            )
            
            # Treat "already active" as success so executor can continue
            if not success and message and "already active" in str(message).lower():
                logger.warning(f"⚠️  Strategy already active: {strategy_name} (continuing)")
                self.running_strategies[strategy_name] = {
                    "started_at": datetime.now(timezone.utc),
                    "symbols": symbols or []
                }
                return True

            if success:
                logger.info(f"✅ Started strategy: {strategy_name}")
                self.running_strategies[strategy_name] = {
                    "started_at": datetime.now(timezone.utc),
                    "symbols": symbols or []
                }
                return True
            else:
                logger.error(f"❌ Failed to start strategy {strategy_name}: {message}")
                return False
        except Exception as e:
            logger.error(f"Error starting strategy {strategy_name}: {e}")
            return False
    
    async def stop_strategy(self, strategy_name: str) -> bool:
        """Stop a strategy."""
        try:
            if not hasattr(self.trading_bot, 'strategy_manager'):
                logger.error("Strategy manager not available")
                return False
            
            success, message = await self.trading_bot.strategy_manager.stop_strategy(strategy_name)
            
            if success:
                logger.info(f"✅ Stopped strategy: {strategy_name}")
                if strategy_name in self.running_strategies:
                    del self.running_strategies[strategy_name]
                return True
            else:
                logger.error(f"❌ Failed to stop strategy {strategy_name}: {message}")
                return False
        except Exception as e:
            logger.error(f"Error stopping strategy {strategy_name}: {e}")
            return False
    
    async def run(self, strategies: List[str], symbols: Optional[List[str]] = None,
                 account_id: Optional[str] = None, auto_start_persisted: bool = True):
        """Run strategy executor."""
        self.is_running = True
        
        logger.info("🚀 Strategy Executor starting...")
        logger.info(f"📈 Requested strategies: {', '.join(strategies)}")
        if symbols:
            logger.info(f"📊 Target symbols: {', '.join(symbols)}")
        
        # Ensure valid token
        if not await self.trading_bot._ensure_valid_token():
            logger.error("❌ Authentication failed")
            return
        
        # Switch account if provided
        if account_id:
            success = await self.trading_bot.switch_account(str(account_id))
            if not success:
                logger.error(f"❌ Failed to switch to account: {account_id}")
                return
            logger.info(f"✅ Switched to account: {account_id}")
        
        # Prefetch contracts to ensure cache is populated
        logger.info("📋 Fetching available contracts...")
        try:
            contracts = await self.trading_bot.get_available_contracts()
            if contracts:
                logger.info(f"✅ Loaded {len(contracts)} contracts")
            else:
                logger.warning("⚠️  No contracts loaded, strategies may fail")
        except Exception as e:
            logger.error(f"❌ Failed to load contracts: {e}")
            logger.warning("⚠️  Continuing without contracts, strategies may fail")
        
        # Load persisted strategy states
        if hasattr(self.trading_bot, 'strategy_manager'):
            logger.info("💾 Loading persisted strategy states...")
            await self.trading_bot.strategy_manager.apply_persisted_states(auto_start=auto_start_persisted)
        
        # Start requested strategies
        for strategy_name in strategies:
            await self.start_strategy(strategy_name, symbols=symbols, account_id=account_id)
        
        # Monitor and keep running
        logger.info("🔄 Strategy executor running... (Press Ctrl+C to stop)")
        try:
            while self.is_running:
                # Update process state in database
                await self._update_process_state()
                
                # Check for strategy status
                await self._check_strategy_status()
                
                # Sleep for a bit
                await asyncio.sleep(30)  # Check every 30 seconds
        except KeyboardInterrupt:
            logger.info("🛑 Stopping strategy executor...")
        finally:
            # Stop all strategies
            for strategy_name in list(self.running_strategies.keys()):
                await self.stop_strategy(strategy_name)
            
            self.is_running = False
            logger.info("✅ Strategy executor stopped")
    
    async def _update_process_state(self):
        """Update process state in database."""
        try:
            db = get_database()
            if not db:
                return
            
            # This will be implemented when we add process_states table
            # For now, just log status
            logger.debug(f"Process state: {len(self.running_strategies)} strategies running")
        except Exception as e:
            logger.debug(f"Could not update process state: {e}")
    
    async def _check_strategy_status(self):
        """Check status of running strategies."""
        try:
            if not hasattr(self.trading_bot, 'strategy_manager'):
                return
            
            status = await self.trading_bot.strategy_manager.get_all_strategy_status()
            for strategy_status in status:
                name = strategy_status.get('name')
                is_active = strategy_status.get('status') == 'active'
                
                if name in self.running_strategies and not is_active:
                    logger.warning(f"⚠️  Strategy {name} is not active but should be running")
                elif name not in self.running_strategies and is_active:
                    logger.info(f"ℹ️  Strategy {name} is active (started externally)")
        except Exception as e:
            logger.debug(f"Could not check strategy status: {e}")


async def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description='Strategy Executor - Run automated trading strategies')
    parser.add_argument('--strategy', type=str, help='Strategy name to run (e.g., mean_reversion)')
    parser.add_argument('--all', action='store_true', help='Run all enabled strategies')
    parser.add_argument('--symbols', type=str, help='Comma-separated symbols (e.g., MNQ,MES)')
    parser.add_argument('--account_id', type=str, help='Account ID to trade on')
    parser.add_argument('--account_select', type=str, help='Account selection by index (1, 2, 3...)')
    parser.add_argument('--timeframe', type=str, help='Timeframe for strategy (e.g., 30s, 1m, 5m). Used by simple_candle strategy.')
    args = parser.parse_args()
    
    # Get credentials
    api_key = os.getenv('PROJECT_X_API_KEY') or os.getenv('TOPSETPX_API_KEY')
    username = os.getenv('PROJECT_X_USERNAME') or os.getenv('TOPSETPX_USERNAME')
    
    if not api_key or not username:
        logger.error("❌ Missing API credentials. Set PROJECT_X_API_KEY and PROJECT_X_USERNAME")
        sys.exit(1)
    
    # Initialize trading bot
    logger.info("🤖 Initializing trading bot...")
    trading_bot = TopStepXTradingBot(api_key=api_key, username=username)
    
    # Determine strategies to run
    strategies = []
    if args.all:
        strategies = ['mean_reversion', 'trend_following', 'overnight_range']
        logger.info("📋 Running all strategies")
    elif args.strategy:
        strategies = [args.strategy]
        logger.info(f"📋 Running strategy: {args.strategy}")
    else:
        logger.error("❌ Must specify --strategy=NAME or --all")
        sys.exit(1)
    
    # Parse symbols
    symbols = None
    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(',')]
        logger.info(f"📊 Symbols: {', '.join(symbols)}")
    
    # Set timeframe environment variable if provided (for simple_candle strategy)
    if args.timeframe:
        os.environ['SIMPLE_CANDLE_TIMEFRAME'] = args.timeframe
        logger.info(f"⏰ Timeframe set to: {args.timeframe}")
    
    # Determine account
    account_id = args.account_id
    if args.account_select and not account_id:
        # Will be handled in executor.run() after listing accounts
        account_id = args.account_select
    
    # Decide whether to auto-start persisted strategies
    # If user explicitly passes a single strategy, do NOT auto-start others.
    # If --all is passed, keep auto-start behavior for completeness.
    auto_start_persisted = True if args.all else False
    
    # Create and run executor
    executor = StrategyExecutor(trading_bot)
    await executor.run(
        strategies=strategies,
        symbols=symbols,
        account_id=account_id,
        auto_start_persisted=auto_start_persisted
    )


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("👋 Strategy executor stopped by user")
    except Exception as e:
        logger.error(f"❌ Fatal error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
