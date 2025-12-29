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
        self.profit_multiplier = 3.0    # Take profit at 3 * ATR
        self.stop_multiplier = 1.5      # Stop loss at 1.5 * ATR
        self.atr_period = 14            # ATR period (14 bars)
        
        # EMA settings for cross detection
        self.ema_fast_period = 8   # Fast EMA period
        self.ema_slow_period = 21  # Slow EMA period
        
        # Track previous EMA values per symbol to detect crosses
        self.prev_emas: Dict[str, Dict[str, float]] = {}  # {symbol: {"fast": float, "slow": float}}
        
        # Get timeframe from environment variable or use default
        self.timeframe = os.getenv('SIMPLE_CANDLE_TIMEFRAME', "30s")
        
        logger.debug(f"✅ Simple Candle Strategy initialized for {config.symbols}")
        logger.info(f"⏰ Timeframe: {self.timeframe}, ATR period: {self.atr_period}, Profit: {self.profit_multiplier}x ATR, Stop: {self.stop_multiplier}x ATR")
        logger.info(f"📊 EMA Cross Detection: {self.ema_fast_period}EMA / {self.ema_slow_period}EMA - Will flatten on cross")

    def _in_trading_window(self) -> bool:
        """
        Override trading window check to always allow trading.
        This strategy is used for high-frequency testing and should trade anytime.
        """
        return True
    
    async def calculate_ema(self, symbol: str, period: int) -> Optional[float]:
        """
        Calculate EMA (Exponential Moving Average).
        
        Args:
            symbol: Trading symbol
            period: EMA period
        
        Returns:
            EMA value or None if error
        """
        try:
            # Fetch historical data (need more data for EMA calculation)
            bars = await self.trading_bot.get_historical_data(
                symbol=symbol,
                timeframe=self.timeframe,
                limit=period * 2  # EMA needs more data for accuracy
            )
            
            if not bars or len(bars) < period:
                return None
            
            # Extract closing prices
            closes = []
            for bar in bars:
                close = bar.get('close', bar.get('c', bar.get('Close', 0)))
                if close > 0:
                    closes.append(close)
            
            if len(closes) < period:
                return None
            
            # Calculate EMA
            multiplier = 2 / (period + 1)
            # Start with SMA of first period values
            ema = sum(closes[:period]) / period
            
            # Calculate EMA for remaining values
            for close in closes[period:]:
                ema = (close * multiplier) + (ema * (1 - multiplier))
            
            return ema
            
        except Exception as e:
            logger.error(f"Error calculating EMA({period}) for {symbol}: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return None
    
    async def check_ema_cross(self, symbol: str) -> Optional[str]:
        """
        Check for EMA cross and return cross type if detected.
        
        Args:
            symbol: Trading symbol
        
        Returns:
            "bullish" if fast EMA crosses above slow EMA (golden cross)
            "bearish" if fast EMA crosses below slow EMA (death cross)
            None if no cross detected
        """
        try:
            # Calculate current EMAs
            fast_ema = await self.calculate_ema(symbol, self.ema_fast_period)
            slow_ema = await self.calculate_ema(symbol, self.ema_slow_period)
            
            if fast_ema is None or slow_ema is None:
                return None
            
            # Get previous EMAs for this symbol
            prev = self.prev_emas.get(symbol, {})
            prev_fast = prev.get("fast")
            prev_slow = prev.get("slow")
            
            # Update stored values
            self.prev_emas[symbol] = {
                "fast": fast_ema,
                "slow": slow_ema
            }
            
            # Check for cross (need previous values to detect)
            if prev_fast is None or prev_slow is None:
                return None  # First time, no cross yet
            
            # Detect bullish cross: fast EMA crosses above slow EMA
            if prev_fast <= prev_slow and fast_ema > slow_ema:
                logger.info(f"🟢 BULLISH EMA CROSS detected for {symbol}: {self.ema_fast_period}EMA ({fast_ema:.2f}) crossed above {self.ema_slow_period}EMA ({slow_ema:.2f})")
                return "bullish"
            
            # Detect bearish cross: fast EMA crosses below slow EMA
            if prev_fast >= prev_slow and fast_ema < slow_ema:
                logger.info(f"🔴 BEARISH EMA CROSS detected for {symbol}: {self.ema_fast_period}EMA ({fast_ema:.2f}) crossed below {self.ema_slow_period}EMA ({slow_ema:.2f})")
                return "bearish"
            
            return None
            
        except Exception as e:
            logger.error(f"Error checking EMA cross for {symbol}: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return None
    
    async def flatten_all(self, symbol: str = None):
        """
        Flatten all positions and cancel all orders.
        
        Uses the same method as the 'flatten' command in trading_bot.py.
        When EMA cross is detected, we flatten ALL positions and orders (not just for specific symbol).
        
        Args:
            symbol: Symbol that triggered the flatten (for logging purposes)
        """
        try:
            if not self.trading_bot.selected_account:
                logger.error("No account selected, cannot flatten")
                return
            
            logger.warning(f"🛑 FLATTENING ALL POSITIONS AND ORDERS (triggered by EMA cross on {symbol if symbol else 'unknown'})")
            print(f"🛑 FLATTENING ALL POSITIONS AND ORDERS (triggered by EMA cross on {symbol if symbol else 'unknown'})")
            
            # Use the same flatten method as the trading_bot flatten command
            # This uses broker_adapter.flatten_all_positions which properly closes all positions and cancels all orders
            result = await self.trading_bot.flatten_all_positions(interactive=False)
            
            if "error" in result:
                logger.error(f"❌ Flatten failed: {result['error']}")
                print(f"❌ Flatten failed: {result['error']}")
            else:
                closed_count = result.get("positions_count", 0) or len(result.get("closed_positions", []))
                canceled_count = result.get("orders_count", 0) or len(result.get("canceled_orders", []))
                logger.info(f"✅ Flatten completed: {closed_count} positions closed, {canceled_count} orders canceled")
                print(f"✅ Flatten completed: {closed_count} positions closed, {canceled_count} orders canceled")
                
                if result.get("failed_positions"):
                    logger.warning(f"⚠️  Failed to close {len(result['failed_positions'])} positions")
                if result.get("failed_orders"):
                    logger.warning(f"⚠️  Failed to cancel {len(result['failed_orders'])} orders")
            
        except Exception as e:
            logger.error(f"Error in flatten_all: {e}")
            import traceback
            logger.error(traceback.format_exc())
            print(f"❌ Error flattening: {e}")
    
    async def calculate_atr(self, symbol: str, period: int = None) -> Optional[float]:
        """
        Calculate ATR (Average True Range).
        
        Args:
            symbol: Trading symbol
            period: ATR period (default: 14)
        
        Returns:
            ATR value or None if error
        """
        try:
            period = period or self.atr_period
            
            # Fetch historical data (need period + 1 for previous close)
            bars = await self.trading_bot.get_historical_data(
                symbol=symbol,
                timeframe=self.timeframe,
                limit=period + 1
            )
            
            if not bars or len(bars) < period + 1:
                return None
            
            # Calculate True Range for each bar
            true_ranges = []
            for i in range(1, len(bars)):
                high = bars[i].get('high', bars[i].get('h', bars[i].get('High', 0)))
                low = bars[i].get('low', bars[i].get('l', bars[i].get('Low', 0)))
                prev_close = bars[i-1].get('close', bars[i-1].get('c', bars[i-1].get('Close', 0)))
                
                if high == 0 or low == 0 or prev_close == 0:
                    continue
                
                tr = max(
                    high - low,
                    abs(high - prev_close),
                    abs(low - prev_close)
                )
                true_ranges.append(tr)
            
            if len(true_ranges) < period:
                return None
            
            # Calculate ATR as average of True Ranges
            atr = sum(true_ranges[-period:]) / period
            return atr
            
        except Exception as e:
            logger.error(f"Error calculating ATR for {symbol}: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return None
    
    async def analyze(self, symbol: str) -> Optional[Dict]:
        """
        Analyze market and generate trading signal based on candle patterns.
        
        Returns signal dict or None.
        """
        try:
            # Get recent bars using configured timeframe (need at least 2)
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
                # Calculate ATR for stop/profit levels
                atr = await self.calculate_atr(symbol, self.atr_period)
                if atr is None or atr <= 0:
                    logger.warning(f"Could not calculate ATR for {symbol}, skipping signal")
                    return None
                
                # Get current market quote to ensure entry price is above current bid
                # For stop_bracket LONG: entry (stop buy) must be above current best bid
                quote = await self.trading_bot.get_market_quote(symbol)
                if quote and "error" not in quote:
                    current_bid = quote.get('bid')
                    current_ask = quote.get('ask')
                    current_price = quote.get('last') or current_bid or current_ask or last_close
                    
                    # For LONG stop order, entry must be above current bid
                    # Use ask price as base, or bid + buffer if ask unavailable
                    if current_ask:
                        base_price = current_ask
                    elif current_bid:
                        base_price = current_bid + (2 * 0.25)  # 2 ticks above bid
                    else:
                        base_price = last_close + (2 * 0.25)  # Fallback to candle close + buffer
                    
                    # Entry price should be above current ask to ensure it's valid
                    entry_price = base_price + (2 * 0.25)  # 2 ticks above ask/bid for stop buy
                else:
                    # Fallback: use candle close + buffer
                    current_price = last_close
                    entry_price = current_price + (2 * 0.25)  # 2 ticks above for stop buy
                    logger.warning(f"Could not get market quote for {symbol}, using candle close")
                
                stop_loss = entry_price - (self.stop_multiplier * atr)  # Stop loss below entry price (1.5 * ATR)
                take_profit = entry_price + (self.profit_multiplier * atr)  # Take profit above entry price (3 * ATR)
                
                return {
                    "action": "LONG",
                    "symbol": symbol,
                    "entry_price": entry_price,  # Stop buy price (above current)
                    "stop_loss": stop_loss,
                    "take_profit": take_profit,
                    "confidence": 0.6,
                    "reason": f"2 consecutive bullish candles (prev: {prev_close:.2f}->{prev_open:.2f}, last: {last_open:.2f}->{last_close:.2f}), ATR: {atr:.2f}"
                }
            
            # Check for bearish pattern: 2 consecutive red candles (close < open)
            last_bearish = last_close < last_open
            prev_bearish = prev_close < prev_open
            
            if last_bearish and prev_bearish:
                # 2 consecutive bearish candles = SHORT signal
                # Calculate ATR for stop/profit levels
                atr = await self.calculate_atr(symbol, self.atr_period)
                if atr is None or atr <= 0:
                    logger.warning(f"Could not calculate ATR for {symbol}, skipping signal")
                    return None
                
                # Get current market quote to ensure entry price is below current ask
                # For stop_bracket SHORT: entry (stop sell) must be below current best ask
                quote = await self.trading_bot.get_market_quote(symbol)
                if quote and "error" not in quote:
                    current_bid = quote.get('bid')
                    current_ask = quote.get('ask')
                    current_price = quote.get('last') or current_ask or current_bid or last_close
                    
                    # For SHORT stop order, entry must be below current ask
                    # Use bid price as base, or ask - buffer if bid unavailable
                    if current_bid:
                        base_price = current_bid
                    elif current_ask:
                        base_price = current_ask - (2 * 0.25)  # 2 ticks below ask
                    else:
                        base_price = last_close - (2 * 0.25)  # Fallback to candle close - buffer
                    
                    # Entry price should be below current bid to ensure it's valid
                    entry_price = base_price - (2 * 0.25)  # 2 ticks below bid/ask for stop sell
                else:
                    # Fallback: use candle close - buffer
                    current_price = last_close
                    entry_price = current_price - (2 * 0.25)  # 2 ticks below for stop sell
                    logger.warning(f"Could not get market quote for {symbol}, using candle close")
                
                stop_loss = entry_price + (self.stop_multiplier * atr)  # Stop loss above entry price (1.5 * ATR)
                take_profit = entry_price - (self.profit_multiplier * atr)  # Take profit below entry price (3 * ATR)
                
                return {
                    "action": "SHORT",
                    "symbol": symbol,
                    "entry_price": entry_price,
                    "stop_loss": stop_loss,
                    "take_profit": take_profit,
                    "confidence": 0.6,
                    "reason": f"2 consecutive bearish candles (prev: {prev_open:.2f}->{prev_close:.2f}, last: {last_open:.2f}->{last_close:.2f}), ATR: {atr:.2f}"
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
        logger.info(f"⏰ Using timeframe: {self.timeframe}, ATR period: {self.atr_period}")

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
                
                # Check for EMA crosses and flatten if detected
                for symbol in self.config.symbols:
                    cross_type = await self.check_ema_cross(symbol)
                    if cross_type:
                        logger.warning(f"🛑 EMA CROSS DETECTED ({cross_type}) for {symbol} - FLATTENING ALL POSITIONS AND ORDERS")
                        print(f"🛑 EMA CROSS DETECTED ({cross_type}) for {symbol} - FLATTENING ALL POSITIONS AND ORDERS")
                        await self.flatten_all(symbol)
                        # Continue to next symbol after flattening
                        continue
                
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
