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
import json
from pathlib import Path
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.logging_setup import configure_logging
configure_logging()
logger = logging.getLogger(__name__)

# Now import trading_bot (it will override logging config with force=True, but will use same LOG_FILE)
from trading_bot import TopStepXTradingBot
from infrastructure.database import get_database
from core.events import Event, EventType


class StrategyExecutor:
    """Standalone strategy executor process."""
    
    def __init__(self, trading_bot: TopStepXTradingBot):
        """Initialize strategy executor."""
        self.trading_bot = trading_bot
        # Store reference to executor in trading_bot so strategy_manager can find it
        trading_bot._strategy_executor = self
        self.running_strategies: Dict[str, Any] = {}
        self.is_running = False
        # Generate unique process ID for this executor instance
        import socket
        import time
        self.process_id = f"strategy_executor_{socket.gethostname()}_{os.getpid()}_{int(time.time())}"
        # WebSocket client for broadcasting signals to GUI
        self.ws_client = None
        self.ws_port = None
        self.ws_connected = False
        self._lifecycle_bound = None

    async def _on_strategy_lifecycle_event(self, event: Event) -> None:
        """EventBus subscriber: run health check when strategies start/stop."""
        try:
            await self._check_strategy_status()
        except Exception as exc:
            logger.debug("Strategy lifecycle handler: %s", exc)

    async def _heartbeat_loop(self) -> None:
        """DB process heartbeat on a fixed interval (no strategy polling here)."""
        try:
            while self.is_running:
                await self._update_process_state()
                await self._sync_persisted_disable_flags()
                await asyncio.sleep(30)
        except asyncio.CancelledError:
            raise

    async def _sync_persisted_disable_flags(self) -> None:
        """Stop strategies when Master GUI persisted ``enabled=False`` on this account."""
        if not self.running_strategies:
            return
        db = get_database()
        if not db:
            return
        aid = self._get_account_id()
        if not aid:
            return
        for name in list(self.running_strategies.keys()):
            row = db.get_strategy_state(str(aid), name)
            if row and row.get("enabled") is False:
                logger.info(
                    "strategy_states disabled %s — stopping in executor (account %s)",
                    name,
                    aid,
                )
                await self.stop_strategy(name)
                await self._update_process_state()

    async def start_strategy(self, strategy_name: str, symbols: Optional[List[str]] = None, 
                            account_id: Optional[str] = None, risk_config: Optional[Dict[str, Dict[str, Any]]] = None) -> bool:
        """Start a strategy."""
        try:
            if not hasattr(self.trading_bot, 'strategy_manager'):
                logger.error("Strategy manager not available")
                return False
            
            # Switch account if provided
            if account_id:
                await self.trading_bot.switch_account(account_id)
            
            # Start strategy with process tracking
            success, message = await self.trading_bot.strategy_manager.start_strategy(
                strategy_name,
                symbols=symbols,
                risk_config=risk_config,
                process_id=self.process_id
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
                # Update process metadata with running strategies
                await self._update_process_state()
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
                 account_id: Optional[str] = None, auto_start_persisted: bool = True,
                 risk_config: Optional[Dict[str, Dict[str, Any]]] = None):
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
        
        # Start event bus for real-time event-driven updates
        if hasattr(self.trading_bot, 'event_bus') and self.trading_bot.event_bus:
            try:
                await self.trading_bot.event_bus.start()
                logger.info("📡 Event bus started for strategy executor")
            except Exception as e:
                logger.warning(f"⚠️  Could not start event bus: {e}")
        
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
        
        # Connect to GUI WebSocket server if available
        await self._connect_to_gui_websocket()
        
        # Start requested strategies
        for strategy_name in strategies:
            await self.start_strategy(strategy_name, symbols=symbols, account_id=account_id, risk_config=risk_config)
        
        # Register this process in the database
        await self._register_process()

        bus = getattr(self.trading_bot, "event_bus", None)
        if bus and getattr(bus, "_running", False):
            self._lifecycle_bound = self._on_strategy_lifecycle_event
            bus.subscribe(EventType.STRATEGY_STARTED, self._lifecycle_bound)
            bus.subscribe(EventType.STRATEGY_STOPPED, self._lifecycle_bound)
            logger.debug("Subscribed strategy executor to STRATEGY_STARTED / STRATEGY_STOPPED")

        await self._check_strategy_status()

        logger.info("🔄 Strategy executor running... (Press Ctrl+C to stop)")
        hang = asyncio.get_running_loop().create_future()
        heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        try:
            await hang
        except asyncio.CancelledError:
            logger.info("🛑 Strategy executor shutting down...")
        finally:
            self.is_running = False
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass

            if bus and self._lifecycle_bound:
                try:
                    bus.unsubscribe(EventType.STRATEGY_STARTED, self._lifecycle_bound)
                    bus.unsubscribe(EventType.STRATEGY_STOPPED, self._lifecycle_bound)
                except Exception as exc:
                    logger.debug("Could not unsubscribe lifecycle handler: %s", exc)
                self._lifecycle_bound = None

            for strategy_name in list(self.running_strategies.keys()):
                await self.stop_strategy(strategy_name)

            await self._disconnect_from_gui_websocket()

            if hasattr(self.trading_bot, "event_bus") and self.trading_bot.event_bus:
                try:
                    await self.trading_bot.event_bus.stop()
                    logger.info("📡 Event bus stopped")
                except Exception as e:
                    logger.debug(f"Could not stop event bus: {e}")

            try:
                db = get_database()
                if db:
                    db.save_process_state(
                        process_id=self.process_id,
                        process_type="strategy_executor",
                        status="stopped",
                        account_id=self._get_account_id(),
                    )
            except Exception as e:
                logger.debug(f"Could not update process state on shutdown: {e}")

            logger.info("✅ Strategy executor stopped")
    
    async def _register_process(self):
        """Register this process in the database."""
        try:
            db = get_database()
            if not db:
                return
            
            account_id = self._get_account_id()
            db.save_process_state(
                process_id=self.process_id,
                process_type='strategy_executor',
                status='running',
                account_id=account_id,
                metadata={
                    'strategies': list(self.running_strategies.keys()),
                    'started_at': datetime.now(timezone.utc).isoformat()
                }
            )
            logger.info(f"📝 Registered process: {self.process_id}")
        except Exception as e:
            logger.debug(f"Could not register process: {e}")
    
    def _get_account_id(self) -> Optional[str]:
        """Get current account ID."""
        account = getattr(self.trading_bot, 'selected_account', None)
        if not account:
            return None
        if isinstance(account, dict):
            return str(account.get('id') or account.get('account_id') or account.get('accountId'))
        return str(account)
    
    async def _connect_to_gui_websocket(self):
        """Connect to GUI WebSocket server to broadcast signals."""
        try:
            # Try to read port from file
            port_file = Path('.gui_websocket_port')
            if not port_file.exists():
                logger.debug("📡 GUI WebSocket port file not found - signals will not be broadcast to GUI")
                return
            
            with open(port_file, 'r') as f:
                self.ws_port = int(f.read().strip())
            
            if not self.ws_port:
                logger.debug("📡 No GUI WebSocket port found")
                return
            
            # Connect to WebSocket server
            import aiohttp
            ws_url = f"ws://127.0.0.1:{self.ws_port}/ws"
            logger.info(f"📡 Connecting to GUI WebSocket at {ws_url}")
            
            try:
                # Create a persistent session for WebSocket
                from aiohttp.client_ws import ClientWSTimeout

                self.ws_session = aiohttp.ClientSession()
                self.ws_client = await self.ws_session.ws_connect(
                    ws_url,
                    timeout=ClientWSTimeout(ws_close=10),
                    heartbeat=30,
                )
                self.ws_connected = True
                logger.info("✅ Connected to GUI WebSocket server - signals will be broadcast")
                
                # Start a task to keep connection alive and handle reconnection
                asyncio.create_task(self._websocket_keepalive())
            except Exception as e:
                logger.debug(f"Could not connect to GUI WebSocket: {e}")
                self.ws_client = None
                self.ws_connected = False
                if hasattr(self, 'ws_session'):
                    await self.ws_session.close()
                    self.ws_session = None
        except Exception as e:
            logger.debug(f"Could not connect to GUI WebSocket: {e}")
            self.ws_client = None
            self.ws_connected = False
    
    async def _websocket_keepalive(self):
        """Keep WebSocket connection alive and handle reconnection."""
        while self.is_running:
            try:
                if self.ws_client and not self.ws_client.closed:
                    # Send ping to keep connection alive
                    await self.ws_client.send_json({'type': 'ping'})
                    await asyncio.sleep(30)  # Ping every 30 seconds
                else:
                    # Connection lost, try to reconnect
                    if self.ws_port:
                        logger.info("📡 WebSocket disconnected, attempting reconnect...")
                        await self._connect_to_gui_websocket()
                    await asyncio.sleep(5)
            except Exception as e:
                logger.debug(f"WebSocket keepalive error: {e}")
                self.ws_connected = False
                await asyncio.sleep(5)
    
    async def _disconnect_from_gui_websocket(self):
        """Disconnect from GUI WebSocket server."""
        try:
            if self.ws_client and not self.ws_client.closed:
                await self.ws_client.close()
                logger.info("📡 Disconnected from GUI WebSocket")
            self.ws_client = None
            self.ws_connected = False
            if hasattr(self, 'ws_session') and self.ws_session:
                await self.ws_session.close()
                self.ws_session = None
        except Exception as e:
            logger.debug(f"Error disconnecting from WebSocket: {e}")
    
    async def broadcast_signal_to_gui(self, signal_data: Dict[str, Any]):
        """Broadcast a signal to the GUI via WebSocket."""
        if not self.ws_connected or not self.ws_client or self.ws_client.closed:
            return
        
        try:
            message = {
                'type': 'signal',
                'data': signal_data
            }
            await self.ws_client.send_json(message)
            logger.debug(f"📡 Broadcasted signal to GUI: {signal_data.get('type')} {signal_data.get('symbol')}")
        except Exception as e:
            logger.debug(f"Could not broadcast signal to GUI: {e}")
            self.ws_connected = False
    
    async def _update_process_state(self):
        """Update process state in database."""
        try:
            db = get_database()
            if not db:
                return
            
            account_id = self._get_account_id()
            meta: Dict[str, Any] = {
                'strategies': list(self.running_strategies.keys()),
                'last_heartbeat': datetime.now(timezone.utc).isoformat(),
            }
            # Embed OR high/low on every heartbeat so Master GUI (separate process) can draw
            # range lines without relying on strategy_states.settings throttling / account quirks.
            if 'overnight_range' in self.running_strategies and hasattr(
                self.trading_bot, 'strategy_manager'
            ):
                strat = self.trading_bot.strategy_manager.strategies.get('overnight_range')
                if strat is not None:
                    ar = getattr(strat, 'active_ranges', None) or {}
                    snap: Dict[str, Dict[str, float]] = {}
                    for sym, r in ar.items():
                        try:
                            hi = float(r.high)
                            lo = float(r.low)
                            key = str(sym).upper()
                            snap[key] = {'high': hi, 'low': lo}
                            if '.' in key:
                                short = key.split('.')[-1].strip()
                                if short and short != key:
                                    snap[short] = {'high': hi, 'low': lo}
                        except (TypeError, ValueError, AttributeError):
                            continue
                    if snap:
                        meta['or_ranges'] = snap
            db.save_process_state(
                process_id=self.process_id,
                process_type='strategy_executor',
                status='running',
                account_id=account_id,
                metadata=meta,
            )
            logger.debug(f"💓 Process heartbeat: {len(self.running_strategies)} strategies running")
        except Exception as e:
            logger.debug(f"Could not update process state: {e}")
    
    async def _check_strategy_status(self):
        """Check status of running strategies."""
        try:
            if not hasattr(self.trading_bot, 'strategy_manager'):
                return
            
            # Get status for all registered strategies
            all_strategies = self.trading_bot.strategy_manager.strategies
            for name, strategy in all_strategies.items():
                # Check for both is_running and is_trading attributes (different strategies use different names)
                is_active = False
                if hasattr(strategy, 'is_running'):
                    is_active = strategy.is_running
                elif hasattr(strategy, 'is_trading'):
                    is_active = strategy.is_trading
                
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
    parser.add_argument('--risk-config', type=str, help='Per-instrument risk config as JSON string. Format: {"SYMBOL":{"max_quantity":10,"cooldown":60.0,"max_pending":1}}')
    parser.add_argument('--max-quantity', type=int, help='Default max quantity per instrument (overridden by risk-config)')
    parser.add_argument('--cooldown', type=float, help='Default order cooldown in seconds (overridden by risk-config)')
    parser.add_argument('--max-pending', type=int, help='Default max pending orders per symbol/side (overridden by risk-config)')
    parser.add_argument('--reload', action='store_true', help='Enable cheap hot-reload for config/strategies/*.toml (mtime poll)')
    args = parser.parse_args()

    if args.reload:
        os.environ["STRATEGY_CONFIG_RELOAD"] = "true"
    
    # Get credentials
    api_key = os.getenv('PROJECT_X_API_KEY') or os.getenv('TOPSTEPX_API_KEY') or os.getenv('TOPSETPX_API_KEY')
    username = os.getenv('PROJECT_X_USERNAME') or os.getenv('TOPSTEPX_USERNAME') or os.getenv('TOPSETPX_USERNAME')
    
    if not api_key or not username:
        logger.error("❌ Missing API credentials. Set PROJECT_X_API_KEY and PROJECT_X_USERNAME")
        sys.exit(1)
    
    # Initialize trading bot (narrow lazy registration when running a single strategy)
    logger.info("🤖 Initializing trading bot...")
    strategy_registration_subset = None
    if args.strategy and not args.all:
        strategy_registration_subset = [args.strategy]
    trading_bot = TopStepXTradingBot(
        api_key=api_key,
        username=username,
        strategy_registration_subset=strategy_registration_subset,
    )
    
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
    
    # Parse risk configuration
    risk_config = None
    if args.risk_config:
        try:
            risk_config = json.loads(args.risk_config)
            logger.info(f"📊 Risk config parsed: {risk_config}")
        except json.JSONDecodeError as e:
            logger.error(f"❌ Invalid risk-config JSON: {e}")
            sys.exit(1)
    elif args.max_quantity or args.cooldown or args.max_pending:
        # Build risk config from individual args (applies to all symbols)
        risk_config = {}
        if symbols:
            for symbol in symbols:
                risk_config[symbol] = {
                    'max_quantity': args.max_quantity or 10,
                    'cooldown': args.cooldown or 60.0,
                    'max_pending': args.max_pending or 1
                }
        logger.info(f"📊 Risk config from individual args: {risk_config}")
    
    # Create and run executor
    executor = StrategyExecutor(trading_bot)
    await executor.run(
        strategies=strategies,
        symbols=symbols,
        account_id=account_id,
        auto_start_persisted=auto_start_persisted,
        risk_config=risk_config
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
