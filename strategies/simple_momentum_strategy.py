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

import logging
import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from strategies.strategy_base import BaseStrategy, StrategyConfig, MarketCondition, StrategyStatus
from core.strategy_config import load_strategy_config

logger = logging.getLogger(__name__)


class SimpleMomentumStrategy(BaseStrategy):
    """
    Simple momentum strategy for active trading.
    
    Designed to make multiple trades in a short time window.
    """

    STRATEGY_ID = "simple_momentum"

    @staticmethod
    def _bar_float(bar: Dict, *keys: str, default: float = 0.0) -> float:
        for k in keys:
            v = bar.get(k)
            if v is not None:
                try:
                    return float(v)
                except (TypeError, ValueError):
                    continue
        return default

    def __init__(self, trading_bot, config: StrategyConfig = None):
        """Initialize the strategy."""
        sid = getattr(type(self), "STRATEGY_ID", "simple_momentum")
        if config is None:
            config = StrategyConfig.from_env(sid)

        super().__init__(trading_bot, config)

        self._cfg = load_strategy_config(sid, env_prefix=f"{sid.upper()}_")

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

    def _has_open_position_for_symbol(self, symbol: str) -> bool:
        for pos in getattr(self, "active_positions", None) or []:
            if str(pos.get("symbol", "")).upper() == symbol.upper():
                return True
        return False

    def _replay_has_pending_bracket_entry(self, symbol: str) -> bool:
        """True when class-strategy replay has a simulated stop bracket entry working."""
        eng = getattr(self.trading_bot, "backtest_engine", None)
        if eng is None:
            return False
        for o in list(getattr(eng, "pending_orders", None) or []):
            if str(getattr(o, "symbol", "")).upper() != symbol.upper():
                continue
            st = getattr(o, "status", None)
            name = getattr(st, "name", None) or (str(st).split(".")[-1] if st is not None else "")
            if name and name.upper() != "PENDING":
                continue
            if (
                getattr(o, "stop_loss_price", None) is not None
                and getattr(o, "take_profit_price", None) is not None
            ):
                return True
        return False

    async def analyze(self, symbol: str) -> Optional[Dict]:
        """
        Analyze market and generate trading signal.
        
        Returns signal dict or None.
        """
        try:
            # Matrix / class replay: without this, every bar can stack new stop brackets
            # while prior entry stops are still pending (mock open-orders are empty).
            if self._has_open_position_for_symbol(symbol):
                return None
            if self._replay_has_pending_bracket_entry(symbol):
                return None
            if len(getattr(self, "active_positions", None) or []) >= int(self.config.max_positions):
                return None

            # Get recent bars (1-minute bars for quick signals)
            bars = await self.trading_bot.get_historical_data(
                symbol=symbol,
                timeframe="1m",
                limit=self.lookback_bars + 5
            )
            
            if not bars or len(bars) < self.lookback_bars:
                return None
            
            # Calculate recent high/low and average volume (support h/l/c CSV keys)
            recent_bars = bars[-self.lookback_bars:]
            highs = [self._bar_float(bar, "high", "h", "High") for bar in recent_bars]
            lows = [self._bar_float(bar, "low", "l", "Low") for bar in recent_bars]
            volumes = [self._bar_float(bar, "volume", "v", "Volume") for bar in recent_bars]
            
            if not highs or max(highs) <= 0:
                return None
            recent_high = max(highs)
            recent_low = min(lows)
            if recent_low <= 0:
                return None
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

            # TESTING MODE: Skip should_trade checks
            logger.info("TESTING MODE: Bypassing should_trade() checks")

            # Get account ID
            account_id = None
            if isinstance(self.trading_bot.selected_account, dict):
                account_id = self.trading_bot.selected_account.get('id')
            else:
                account_id = self.trading_bot.selected_account

            if not account_id:
                logger.error("No account selected")
                return False
            
            # Place order using the EXACT method that works from CLI (stop_bracket command)
            side = "BUY" if action == "LONG" else "SELL"
            quantity = self.config.position_size

            logger.info(
                "Executing %s on %s: Entry=%.2f SL=%.2f TP=%.2f",
                action,
                symbol,
                entry_price,
                stop_loss,
                take_profit,
            )

            # Use the verified working bracket order method from BaseStrategy
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
                logger.error("Stop bracket order failed: %s", error_msg)
                return False

            # Success!
            order_id = result.get('orderId')
            method = result.get('method', 'unknown')
            logger.info("Stop bracket placed: order_id=%s method=%s", order_id, method)
            self.daily_trades += 1
            return True
                
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
    
    def _in_trading_window(self) -> bool:
        """Override: Trade 24/7 for testing."""
        return True  # Always trade, no time restrictions
    
    async def run(self):
        """Main strategy loop."""
        self.status = StrategyStatus.ACTIVE
        
        # Print initialization message
        strategy_details = [
            f"⏰ Timeframe: 1m",
            f"📊 Lookback: {self.lookback_bars} bars",
            f"🎯 Take Profit: {self.profit_ticks} ticks",
            f"🛑 Stop Loss: {self.stop_ticks} ticks",
            f"📈 Min Volume Ratio: {self.min_volume_ratio}x",
            f"📦 Position Size: {self.config.position_size} contract(s) per order"
        ]
        self.print_initialization_message("Simple Momentum Strategy", self.config.symbols, strategy_details)
        
        logger.info(f"🚀 Starting Simple Momentum Strategy for {self.config.symbols}")
        
        # NO time limit for testing
        logger.info(f"⏰ Strategy running with NO time limit (test mode)")
        
        check_interval = 10  # Check every 10 seconds (faster for testing)
        loop_count = 0
        
        while self.status == StrategyStatus.ACTIVE:
            try:
                loop_count += 1
                if loop_count % 6 == 0:  # status every 6 loops (~minute at 10s interval)
                    logger.debug(
                        "Strategy running (%s positions, %s trades today)",
                        len(self.active_positions),
                        self.daily_trades,
                    )
                
                # Manage existing positions
                await self.manage_positions()
                
                # Check each symbol for signals
                for symbol in self.config.symbols:
                    # Skip if we're at max positions
                    if len(self.active_positions) >= self.config.max_positions:
                        if loop_count % 2 == 0:
                            logger.debug(
                                "Max positions reached (%s/%s), skipping %s",
                                len(self.active_positions),
                                self.config.max_positions,
                                symbol,
                            )
                        continue
                    
                    # Analyze for signals
                    signal = await self.analyze(symbol)
                    
                    if signal:
                        logger.info(
                            "Signal detected: %s %s — %s",
                            signal["action"],
                            signal["symbol"],
                            signal.get("reason", ""),
                        )
                        await self.execute(signal)
                
                # Wait before next check
                await asyncio.sleep(check_interval)
                
            except KeyboardInterrupt:
                logger.info("Strategy stopped by user")
                break
            except Exception as e:
                logger.error("Error in strategy loop: %s", e)
                import traceback

                logger.error(traceback.format_exc())
                await asyncio.sleep(check_interval)

        logger.info("Simple Momentum Strategy finished")
        self.status = StrategyStatus.IDLE
