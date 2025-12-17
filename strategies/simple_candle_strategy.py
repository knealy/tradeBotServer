"""
Simple Candle Strategy

A very simple strategy that trades based on consecutive candle patterns:
- LONG: 2 consecutive bullish (green) 1-minute candles
- SHORT: 2 consecutive bearish (red) 1-minute candles

This strategy is designed to be active and make trades quickly.
"""

import os
import logging
import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from strategies.strategy_base import BaseStrategy, StrategyConfig, MarketCondition, StrategyStatus

logger = logging.getLogger(__name__)


class SimpleCandleStrategy(BaseStrategy):
    """
    Simple candle-based strategy for active trading.
    
    Trades on simple 2-candle patterns:
    - 2 green candles in a row = LONG
    - 2 red candles in a row = SHORT
    """
    
    def __init__(self, trading_bot, config: StrategyConfig = None):
        """Initialize the strategy."""
        # Create config if not provided
        if config is None:
            config = StrategyConfig(
                name="simple_candle",
                enabled=True,
                symbols=["MNQ"],  # Default to MNQ
                max_positions=2,
                position_size=1,
                risk_per_trade_percent=0.5,
                max_daily_trades=30,  # Allow many trades for testing
                preferred_conditions=[],
                avoid_conditions=[],
                trading_start_time="09:30",
                trading_end_time="16:00",
                no_trade_start="",
                no_trade_end="",
                respect_dll=True,
                respect_mll=True,
                max_dll_usage_percent=0.70
            )
        
        super().__init__(trading_bot, config)
        
        # Strategy-specific settings
        self.candles_needed = 2  # Need 2 consecutive candles
        self.profit_ticks = 8    # Take profit at 8 ticks
        self.stop_ticks = 12     # Stop loss at 12 ticks
        self.timeframe = "1m"   # Use 1-minute candles
        
        logger.info(f"✅ Simple Candle Strategy initialized for {config.symbols}")
        print(f"✅ Simple Candle Strategy initialized for {config.symbols}")

    def _in_trading_window(self) -> bool:
        """
        Override trading window check to always allow trading.
        This strategy is used for high-frequency testing and should trade anytime.
        """
        return True
    
    async def analyze(self, symbol: str) -> Optional[Dict]:
        """
        Analyze market and generate trading signal based on candle patterns.
        
        Returns signal dict or None.
        """
        try:
            # Get recent 1-minute bars (need at least 2)
            bars = await self.trading_bot.get_historical_data(
                symbol=symbol,
                timeframe=self.timeframe,
                limit=5  # Get last 5 bars to be safe
            )
            
            if not bars or len(bars) < self.candles_needed:
                return None
            
            # Get the last 2 candles
            last_candle = bars[-1]
            prev_candle = bars[-2]
            
            # Extract OHLC data (handle different formats)
            def get_close(bar):
                return bar.get('close', bar.get('Close', bar.get('c', 0)))
            
            def get_open(bar):
                return bar.get('open', bar.get('Open', bar.get('o', 0)))
            
            last_close = get_close(last_candle)
            last_open = get_open(last_candle)
            prev_close = get_close(prev_candle)
            prev_open = get_open(prev_candle)
            
            if last_close == 0 or prev_close == 0:
                return None
            
            # Check for bullish pattern: 2 consecutive green candles (close > open)
            last_bullish = last_close > last_open
            prev_bullish = prev_close > prev_open
            
            if last_bullish and prev_bullish:
                # 2 consecutive bullish candles = LONG signal
                # For stop_bracket LONG: entry (stop buy) should be above current price
                # Add a few ticks above the close to trigger on continuation
                current_price = last_close
                entry_price = current_price + (2 * 0.25)  # 2 ticks above for stop buy
                stop_loss = entry_price - (self.stop_ticks * 0.25)  # Stop loss below entry price
                take_profit = entry_price + (self.profit_ticks * 0.25)  # Take profit above entry price
                
                return {
                    "action": "LONG",
                    "symbol": symbol,
                    "entry_price": entry_price,  # Stop buy price (above current)
                    "stop_loss": stop_loss,
                    "take_profit": take_profit,
                    "confidence": 0.6,
                    "reason": f"2 consecutive bullish candles (prev: {prev_close:.2f}->{prev_open:.2f}, last: {last_open:.2f}->{last_close:.2f})"
                }
            
            # Check for bearish pattern: 2 consecutive red candles (close < open)
            last_bearish = last_close < last_open
            prev_bearish = prev_close < prev_open
            
            if last_bearish and prev_bearish:
                # 2 consecutive bearish candles = SHORT signal
                # For stop_bracket SHORT: entry (stop sell) should be below current price
                current_price = last_close
                entry_price = current_price - (2 * 0.25)  # 2 ticks below for stop sell
                stop_loss = entry_price + (self.stop_ticks * 0.25)  # Stop loss above entry price
                take_profit = entry_price - (self.profit_ticks * 0.25)  # Take profit below entry price
                
                return {
                    "action": "SHORT",
                    "symbol": symbol,
                    "entry_price": entry_price,
                    "stop_loss": stop_loss,
                    "take_profit": take_profit,
                    "confidence": 0.6,
                    "reason": f"2 consecutive bearish candles (prev: {prev_open:.2f}->{prev_close:.2f}, last: {last_open:.2f}->{last_close:.2f})"
                }
            
            return None
            
        except Exception as e:
            logger.error(f"Error analyzing {symbol}: {e}")
            print(f"❌ Error analyzing {symbol}: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return None
    
    def _in_trading_window(self) -> bool:
        """Override: Trade 24/7 for testing."""
        return True  # Always trade, no time restrictions
    
    async def execute(self, signal: Dict) -> bool:
        """Execute a trading signal."""
        try:
            symbol = signal['symbol']
            action = signal['action']
            entry_price = signal['entry_price']
            stop_loss = signal['stop_loss']
            take_profit = signal['take_profit']
            
            # Skip should_trade check for testing - we want to see if orders work at all
            # (normally should_trade checks time, limits, compliance, etc.)
            print(f"⚡ TESTING MODE: Bypassing should_trade() checks")
            logger.info(f"⚡ TESTING MODE: Bypassing should_trade() checks")
            
            # Get account ID
            account_id = None
            if isinstance(self.trading_bot.selected_account, dict):
                account_id = self.trading_bot.selected_account.get('id')
            else:
                account_id = self.trading_bot.selected_account
            
            if not account_id:
                print("❌ No account selected")
                logger.error("No account selected")
                return False
            
            # Place order using the EXACT method that works from CLI (stop_bracket command)
            side = "BUY" if action == "LONG" else "SELL"
            quantity = self.config.position_size

            print(f"📈 Executing {action} on {symbol}: Entry={entry_price:.2f}, SL={stop_loss:.2f}, TP={take_profit:.2f}")
            logger.info(f"📈 Executing {action} on {symbol}: Entry={entry_price:.2f}, SL={stop_loss:.2f}, TP={take_profit:.2f}")

            # Use the verified working bracket order method from BaseStrategy
            print("📝 Placing bracket order (verified working method)...")
            logger.info("Using BaseStrategy.place_bracket_order() - same path as CLI stop_bracket")
            
            result = await self.place_bracket_order(
                symbol=symbol,
                side=side,
                quantity=quantity,
                entry_price=entry_price,
                stop_loss_price=stop_loss,
                take_profit_price=take_profit,
                enable_breakeven=False  # Can enable later if desired
            )

            if result.get("error"):
                error_msg = result.get("error")
                print(f"❌ Stop bracket order failed: {error_msg}")
                logger.error(f"Stop bracket order failed: {error_msg}")
                return False

            # Success!
            order_id = result.get('orderId')
            method = result.get('method', 'unknown')
            print(f"✅ Stop bracket order placed successfully!")
            print(f"   Order ID: {order_id}")
            print(f"   Method: {method}")
            logger.info(f"✅ Stop bracket placed: Order ID {order_id}, Method: {method}")
            self.daily_trades += 1
            return True
                
        except Exception as e:
            print(f"❌ Error executing signal: {e}")
            logger.error(f"Error executing signal: {e}")
            import traceback
            logger.error(traceback.format_exc())
            print(traceback.format_exc())
            return False
    
    async def manage_positions(self):
        """Manage open positions (check for exits, trailing stops, etc.)."""
        # This strategy uses bracket orders, so positions are managed automatically
        # Just track active positions
        try:
            account_id = None
            if isinstance(self.trading_bot.selected_account, dict):
                account_id = self.trading_bot.selected_account.get('id')
            else:
                account_id = self.trading_bot.selected_account
            
            if not account_id:
                return
            
            positions = await self.trading_bot.get_open_positions(account_id=account_id)
            if positions:
                # Update active positions list
                self.active_positions = [
                    pos for pos in positions 
                    if pos.get('symbol') in self.config.symbols
                ]
        except Exception as e:
            logger.debug(f"Error managing positions: {e}")
    
    async def cleanup(self):
        """Clean up strategy resources."""
        print("🧹 Cleaning up Simple Candle Strategy")
        logger.info("🧹 Cleaning up Simple Candle Strategy")
        self.status = StrategyStatus.IDLE
    
    async def start(self, symbols: Optional[List[str]] = None):
        """
        Start the strategy with custom monitoring loop.
        Called by strategy_manager when strategy is started.
        """
        if symbols:
            self.config.symbols = symbols
        asyncio.create_task(self.run())
        logger.info(f"✅ Simple Candle Strategy start() called, running in background")

    async def run(self):
        """Main strategy loop."""
        self.status = StrategyStatus.ACTIVE
        print(f"🚀 Starting Simple Candle Strategy for {self.config.symbols}")
        logger.info(f"🚀 Starting Simple Candle Strategy for {self.config.symbols}")

        # Ensure valid token before starting
        print("🔐 Ensuring valid authentication token...")
        if not await self.trading_bot._ensure_valid_token():
            print("❌ Failed to ensure valid token. Cannot start strategy.")
            logger.error("Failed to ensure valid token. Cannot start strategy.")
            self.status = StrategyStatus.IDLE
            return

        print("✅ Authentication token validated")
        logger.info("✅ Authentication token validated")

        print("⏰ Strategy running with NO time limit (test mode)")
        logger.info("⏰ Strategy running with NO time limit (test mode)")

        check_interval = 10  # Check every 10 seconds (faster for 1m candles)
        loop_count = 0

        while self.status == StrategyStatus.ACTIVE:
            try:
                loop_count += 1
                if loop_count % 6 == 0:  # Print status every 6 loops (every minute)
                    print(f"🔄 Strategy running... ({len(self.active_positions)} positions)")
                
                # Manage existing positions
                await self.manage_positions()
                
                # Check each symbol for signals
                for symbol in self.config.symbols:
                    # Skip if we're at max positions
                    if len(self.active_positions) >= self.config.max_positions:
                        if loop_count % 6 == 0:
                            print(f"⏸️  Max positions reached ({len(self.active_positions)}/{self.config.max_positions}), skipping {symbol}")
                        continue
                    
                    # Analyze for signals
                    signal = await self.analyze(symbol)
                    
                    if signal:
                        print(f"📊 Signal detected: {signal['action']} {signal['symbol']} - {signal['reason']}")
                        logger.info(f"📊 Signal detected: {signal['action']} {signal['symbol']} - {signal['reason']}")
                        await self.execute(signal)
                
                # Wait before next check
                await asyncio.sleep(check_interval)
                
            except KeyboardInterrupt:
                print("\n🛑 Strategy stopped by user")
                logger.info("🛑 Strategy stopped by user")
                break
            except Exception as e:
                print(f"❌ Error in strategy loop: {e}")
                logger.error(f"Error in strategy loop: {e}")
                import traceback
                logger.error(traceback.format_exc())
                print(traceback.format_exc())
                await asyncio.sleep(check_interval)
        
        print("✅ Simple Candle Strategy finished")
        logger.info("✅ Simple Candle Strategy finished")
        self.status = StrategyStatus.IDLE
