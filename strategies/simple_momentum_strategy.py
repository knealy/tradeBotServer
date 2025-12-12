"""
Simple Momentum Test Strategy

A simple strategy designed to trade actively over a 2-hour window.
Trades on short-term momentum signals using price action and volume.

Strategy Logic:
- Enter LONG when: Price breaks above recent high with volume
- Enter SHORT when: Price breaks below recent low with volume
- Exit: Quick profit target (10 ticks) or stop loss (15 ticks)
- Designed to make multiple trades in a 2-hour window
"""

import os
import logging
import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from strategies.strategy_base import BaseStrategy, StrategyConfig, MarketCondition, StrategyStatus

logger = logging.getLogger(__name__)


class SimpleMomentumStrategy(BaseStrategy):
    """
    Simple momentum strategy for active trading.
    
    Designed to make multiple trades in a short time window.
    """
    
    def __init__(self, trading_bot, config: StrategyConfig = None):
        """Initialize the strategy."""
        # Create config if not provided
        if config is None:
            config = StrategyConfig(
                name="simple_momentum",
                enabled=True,
                symbols=["MNQ"],  # Default to MNQ
                max_positions=2,
                position_size=1,
                risk_per_trade_percent=0.5,
                max_daily_trades=20,  # Allow many trades for testing
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
        self.lookback_bars = 10  # Look at last 10 bars
        self.profit_ticks = 10  # Take profit at 10 ticks
        self.stop_ticks = 15    # Stop loss at 15 ticks
        self.min_volume_ratio = 1.2  # Volume must be 20% above average
        
        # Track recent highs/lows
        self.recent_highs = {}  # {symbol: float}
        self.recent_lows = {}   # {symbol: float}
        self.volume_averages = {}  # {symbol: float}
        
        logger.info(f"✅ Simple Momentum Strategy initialized for {config.symbols}")
    
    async def analyze(self, symbol: str) -> Optional[Dict]:
        """
        Analyze market and generate trading signal.
        
        Returns signal dict or None.
        """
        try:
            # Get recent bars (1-minute bars for quick signals)
            bars = await self.trading_bot.get_historical_data(
                symbol=symbol,
                timeframe="1m",
                limit=self.lookback_bars + 5
            )
            
            if not bars or len(bars) < self.lookback_bars:
                return None
            
            # Calculate recent high/low and average volume
            recent_bars = bars[-self.lookback_bars:]
            highs = [bar.get('high', bar.get('High', 0)) for bar in recent_bars]
            lows = [bar.get('low', bar.get('Low', 999999)) for bar in recent_bars]
            volumes = [bar.get('volume', bar.get('Volume', 0)) for bar in recent_bars]
            
            recent_high = max(highs)
            recent_low = min(lows)
            avg_volume = sum(volumes) / len(volumes) if volumes else 0
            
            # Store for reference
            self.recent_highs[symbol] = recent_high
            self.recent_lows[symbol] = recent_low
            self.volume_averages[symbol] = avg_volume
            
            # Get current price
            quote = await self.trading_bot.get_market_quote(symbol)
            if not quote:
                return None
            
            current_price = quote.get('last', quote.get('Last', 0))
            current_volume = quote.get('volume', quote.get('Volume', 0))
            
            if current_price == 0:
                return None
            
            # Check for LONG signal: price breaks above recent high with volume
            if current_price > recent_high and current_volume > avg_volume * self.min_volume_ratio:
                # Calculate entry, stop, and target
                entry_price = current_price
                stop_loss = entry_price - (self.stop_ticks * 0.25)  # MNQ tick size is 0.25
                take_profit = entry_price + (self.profit_ticks * 0.25)
                
                return {
                    "action": "LONG",
                    "symbol": symbol,
                    "entry_price": entry_price,
                    "stop_loss": stop_loss,
                    "take_profit": take_profit,
                    "confidence": 0.7,
                    "reason": f"Price breakout above {recent_high:.2f} with volume"
                }
            
            # Check for SHORT signal: price breaks below recent low with volume
            if current_price < recent_low and current_volume > avg_volume * self.min_volume_ratio:
                entry_price = current_price
                stop_loss = entry_price + (self.stop_ticks * 0.25)
                take_profit = entry_price - (self.profit_ticks * 0.25)
                
                return {
                    "action": "SHORT",
                    "symbol": symbol,
                    "entry_price": entry_price,
                    "stop_loss": stop_loss,
                    "take_profit": take_profit,
                    "confidence": 0.7,
                    "reason": f"Price breakdown below {recent_low:.2f} with volume"
                }
            
            return None
            
        except Exception as e:
            logger.error(f"Error analyzing {symbol}: {e}")
            return None
    
    async def execute(self, signal: Dict) -> bool:
        """Execute a trading signal."""
        try:
            symbol = signal['symbol']
            action = signal['action']
            entry_price = signal['entry_price']
            stop_loss = signal['stop_loss']
            take_profit = signal['take_profit']
            
            # Check if we should trade
            should_trade, reason = self.should_trade(symbol)
            if not should_trade:
                logger.info(f"⏭️  Skipping {action} on {symbol}: {reason}")
                return False
            
            # Get account ID
            account_id = None
            if isinstance(self.trading_bot.selected_account, dict):
                account_id = self.trading_bot.selected_account.get('id')
            else:
                account_id = self.trading_bot.selected_account
            
            if not account_id:
                logger.error("No account selected")
                return False
            
            # Place bracket order
            side = "BUY" if action == "LONG" else "SELL"
            quantity = self.config.position_size
            
            print(f"📈 Executing {action} on {symbol}: Entry={entry_price:.2f}, SL={stop_loss:.2f}, TP={take_profit:.2f}")
            logger.info(f"📈 Executing {action} on {symbol}: Entry={entry_price:.2f}, SL={stop_loss:.2f}, TP={take_profit:.2f}")
            
            result = await self.trading_bot.create_bracket_order(
                symbol=symbol,
                side=side,
                quantity=quantity,
                stop_loss_price=stop_loss,
                take_profit_price=take_profit,
                account_id=account_id
            )
            
            if result and result.get('success'):
                order_id = result.get('order_id')
                print(f"✅ Order placed: {order_id}")
                logger.info(f"✅ Order placed: {order_id}")
                self.daily_trades += 1
                return True
            else:
                error = result.get('error', 'Unknown error') if result else 'No result'
                print(f"❌ Order failed: {error}")
                logger.error(f"❌ Order failed: {error}")
                return False
                
        except Exception as e:
            logger.error(f"Error executing signal: {e}")
            import traceback
            logger.error(traceback.format_exc())
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
            
            positions = await self.trading_bot.get_positions(account_id=account_id)
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
        logger.info("🧹 Cleaning up Simple Momentum Strategy")
        self.status = StrategyStatus.IDLE
    
    async def run(self):
        """Main strategy loop."""
        self.status = StrategyStatus.ACTIVE
        print(f"🚀 Starting Simple Momentum Strategy for {self.config.symbols}")
        logger.info(f"🚀 Starting Simple Momentum Strategy for {self.config.symbols}")
        
        # Set end time (2 hours from now)
        end_time = datetime.now() + timedelta(hours=2)
        print(f"⏰ Strategy will run until {end_time.strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info(f"⏰ Strategy will run until {end_time.strftime('%Y-%m-%d %H:%M:%S')}")
        
        check_interval = 30  # Check every 30 seconds
        loop_count = 0
        
        while self.status == StrategyStatus.ACTIVE and datetime.now() < end_time:
            try:
                # Check if we should continue
                if datetime.now() >= end_time:
                    print("⏰ Strategy time limit reached")
                    logger.info("⏰ Strategy time limit reached")
                    break
                
                loop_count += 1
                if loop_count % 2 == 0:  # Print status every 2 loops (every minute)
                    remaining = (end_time - datetime.now()).total_seconds() / 60
                    print(f"🔄 Strategy running... ({remaining:.1f} minutes remaining, {len(self.active_positions)} positions)")
                
                # Manage existing positions
                await self.manage_positions()
                
                # Check each symbol for signals
                for symbol in self.config.symbols:
                    # Skip if we're at max positions
                    if len(self.active_positions) >= self.config.max_positions:
                        if loop_count % 2 == 0:
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
        
        print("✅ Simple Momentum Strategy finished")
        logger.info("✅ Simple Momentum Strategy finished")
        self.status = StrategyStatus.IDLE
