"""
Trend Following Strategy Module

This module implements a trading strategy that:
1. Identifies trending markets using moving average crossovers
2. Enters trades in the direction of the trend
3. Uses ATR-based trailing stops
4. Best for strong trending markets

Strategy Logic:
- Use fast and slow moving averages to identify trend
- Enter LONG when: fast MA > slow MA AND price > fast MA
- Enter SHORT when: fast MA < slow MA AND price < fast MA
- Use ATR trailing stop to ride the trend
- Exit when: MA crossover reverses or stop hit
"""

import logging
import asyncio
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

from strategies.strategy_base import BaseStrategy, StrategyConfig, MarketCondition, StrategyStatus
from core.strategy_config import load_strategy_config

logger = logging.getLogger(__name__)


@dataclass
class TrendSignal:
    """Trend following signal data."""
    symbol: str
    action: str  # "LONG", "SHORT", "CLOSE"
    entry_price: float
    stop_loss: float
    take_profit: float
    fast_ma: float
    slow_ma: float
    atr: float
    trend_strength: float
    confidence: float
    reason: str


class TrendFollowingStrategy(BaseStrategy):
    """
    Trend following strategy using moving average crossovers.
    
    Features:
    - Dual MA crossover for trend identification
    - ATR-based trailing stops
    - Pyramid entries on strong trends (optional)
    - Best for trending markets
    """
    
    def __init__(self, trading_bot, config: StrategyConfig = None):
        """
        Initialize the strategy.
        
        Args:
            trading_bot: Reference to main TradingBot instance
            config: Strategy configuration (if None, loads from environment)
        """
        # Load config from environment if not provided
        if config is None:
            config = StrategyConfig.from_env("TREND_FOLLOWING")
        
        # Initialize base strategy
        super().__init__(trading_bot, config)

        # Strategy-specific config loader (TOML > env > defaults).
        # Legacy env prefix for this strategy is TREND_*.
        self._cfg = load_strategy_config("trend_following", env_prefix="TREND_")
        
        # Trend following specific configuration
        self.fast_ma_period = int(self._cfg.get_int("fast_ma_period", 10))
        self.slow_ma_period = int(self._cfg.get_int("slow_ma_period", 30))
        self.ma_type = self._cfg.get_str("ma_type", "EMA")  # SMA or EMA
        
        self.atr_period = int(self._cfg.get_int("atr_period", 14))
        self.atr_stop_multiplier = float(self._cfg.get_float("atr_stop_multiplier", 2.0))
        self.atr_trailing_multiplier = float(self._cfg.get_float("atr_trailing_multiplier", 3.0))
        
        self.min_trend_strength = float(self._cfg.get_float("min_trend_strength", 0.5))  # MA separation
        self.pyramid_enabled = self._cfg.get_bool("pyramid_enabled", False)
        self.pyramid_max_adds = int(self._cfg.get_int("pyramid_max_adds", 2))
        
        self.timeframe = self._cfg.get_str("timeframe", "15m")
        
        # State tracking
        self.is_trading = False
        self._monitoring_task: Optional[asyncio.Task] = None
        self.trailing_stops: Dict[str, float] = {}  # symbol -> trailing stop price
        
        logger.info(f"📈 Trend Following Strategy initialized")
        logger.info(f"   MA Crossover: {self.fast_ma_period}/{self.slow_ma_period} {self.ma_type}")
        logger.info(f"   ATR: {self.atr_period} period, Stop={self.atr_stop_multiplier}x, Trail={self.atr_trailing_multiplier}x")
        logger.info(f"   Min Trend Strength: {self.min_trend_strength}")
        logger.info(f"   Pyramiding: {'ENABLED' if self.pyramid_enabled else 'DISABLED'} (max {self.pyramid_max_adds} adds)")
    
    async def calculate_moving_average(self, symbol: str, period: int, ma_type: str = None) -> Optional[float]:
        """
        Calculate Moving Average (SMA or EMA).
        
        Args:
            symbol: Trading symbol
            period: MA period
            ma_type: 'SMA' or 'EMA' (default: from config)
        
        Returns:
            MA value or None if error
        """
        try:
            ma_type = ma_type or self.ma_type
            
            # Fetch historical data
            bars = await self.trading_bot.get_historical_data(
                symbol=symbol,
                timeframe=self.timeframe,
                limit=period if ma_type == 'SMA' else period * 2
            )
            
            if not bars or len(bars) < period:
                return None
            
            # Extract closing prices
            closes = [bar.get('close', bar.get('c', 0)) for bar in bars[-period:]]
            
            if ma_type == 'SMA':
                # Simple Moving Average
                ma = sum(closes) / period
            else:
                # Exponential Moving Average
                multiplier = 2 / (period + 1)
                ema = closes[0]
                for close in closes[1:]:
                    ema = (close * multiplier) + (ema * (1 - multiplier))
                ma = ema
            
            return ma
            
        except Exception as e:
            logger.error(f"Error calculating MA for {symbol}: {e}")
            return None
    
    async def calculate_atr(self, symbol: str, period: int = None) -> Optional[float]:
        """Calculate ATR (Average True Range)."""
        try:
            period = period or self.atr_period
            
            bars = await self.trading_bot.get_historical_data(
                symbol=symbol,
                timeframe=self.timeframe,
                limit=period + 1
            )
            
            if not bars or len(bars) < period + 1:
                return None
            
            # Calculate True Range
            true_ranges = []
            for i in range(1, len(bars)):
                high = bars[i].get('high', bars[i].get('h', 0))
                low = bars[i].get('low', bars[i].get('l', 0))
                prev_close = bars[i-1].get('close', bars[i-1].get('c', 0))
                
                tr = max(
                    high - low,
                    abs(high - prev_close),
                    abs(low - prev_close)
                )
                true_ranges.append(tr)
            
            atr = sum(true_ranges[-period:]) / period
            return atr
            
        except Exception as e:
            logger.error(f"Error calculating ATR for {symbol}: {e}")
            return None
    
    def calculate_trend_strength(self, fast_ma: float, slow_ma: float, current_price: float) -> float:
        """
        Calculate trend strength based on MA separation.
        
        Returns:
            Trend strength (0.0-1.0)
        """
        if slow_ma == 0:
            return 0.0
        
        # MA separation as percentage of slow MA
        ma_separation = abs(fast_ma - slow_ma) / slow_ma
        
        # Normalize to 0-1 (assuming 5% separation = full strength)
        trend_strength = min(1.0, ma_separation / 0.05)
        
        return trend_strength
    
    async def analyze(self, symbol: str) -> Optional[Dict]:
        """
        Analyze market for trend following opportunities.
        
        Entry conditions:
        - LONG: fast MA > slow MA AND price > fast MA AND trend strength > min
        - SHORT: fast MA < slow MA AND price < fast MA AND trend strength > min
        
        Returns:
            Signal dictionary or None
        """
        try:
            # Check if we should trade this symbol
            should_trade, reason = self.should_trade(symbol)
            if not should_trade:
                return None
            
            # Get current price
            bars = await self.trading_bot.get_historical_data(
                symbol=symbol,
                timeframe=self.timeframe,
                limit=1
            )
            
            if not bars:
                return None
            
            current_price = bars[0].get('close', bars[0].get('c', 0))
            
            # Calculate indicators
            fast_ma = await self.calculate_moving_average(symbol, self.fast_ma_period)
            slow_ma = await self.calculate_moving_average(symbol, self.slow_ma_period)
            atr = await self.calculate_atr(symbol)
            
            if fast_ma is None or slow_ma is None or atr is None:
                return None
            
            # Calculate trend strength
            trend_strength = self.calculate_trend_strength(fast_ma, slow_ma, current_price)
            
            logger.debug(f"{symbol}: Price={current_price:.2f}, FastMA={fast_ma:.2f}, SlowMA={slow_ma:.2f}, ATR={atr:.2f}, Strength={trend_strength:.2f}")
            
            # Check for trend following opportunities
            signal = None
            
            # LONG signal: Uptrend (fast MA > slow MA) + price above fast MA
            if fast_ma > slow_ma and current_price > fast_ma and trend_strength >= self.min_trend_strength:
                entry_price = current_price
                stop_loss = entry_price - (atr * self.atr_stop_multiplier)
                take_profit = entry_price + (atr * 5.0)  # Wide target for trend
                
                confidence = min(1.0, trend_strength + 0.3)
                
                signal = {
                    "action": "LONG",
                    "symbol": symbol,
                    "entry_price": entry_price,
                    "stop_loss": stop_loss,
                    "take_profit": take_profit,
                    "confidence": confidence,
                    "reason": f"Uptrend (FastMA={fast_ma:.2f} > SlowMA={slow_ma:.2f}), Strength={trend_strength:.2f}",
                    "fast_ma": fast_ma,
                    "slow_ma": slow_ma,
                    "atr": atr,
                    "trend_strength": trend_strength
                }
            
            # SHORT signal: Downtrend (fast MA < slow MA) + price below fast MA
            elif fast_ma < slow_ma and current_price < fast_ma and trend_strength >= self.min_trend_strength:
                entry_price = current_price
                stop_loss = entry_price + (atr * self.atr_stop_multiplier)
                take_profit = entry_price - (atr * 5.0)  # Wide target for trend
                
                confidence = min(1.0, trend_strength + 0.3)
                
                signal = {
                    "action": "SHORT",
                    "symbol": symbol,
                    "entry_price": entry_price,
                    "stop_loss": stop_loss,
                    "take_profit": take_profit,
                    "confidence": confidence,
                    "reason": f"Downtrend (FastMA={fast_ma:.2f} < SlowMA={slow_ma:.2f}), Strength={trend_strength:.2f}",
                    "fast_ma": fast_ma,
                    "slow_ma": slow_ma,
                    "atr": atr,
                    "trend_strength": trend_strength
                }
            
            if signal:
                logger.info(f"📈 Trend Following Signal: {signal['action']} {symbol} @ {signal['entry_price']:.2f}")
                logger.info(f"   {signal['reason']}")
            
            return signal
            
        except Exception as e:
            logger.error(f"Error analyzing {symbol}: {e}")
            return None
    
    async def execute(self, signal: Dict) -> bool:
        """
        Execute trend following trade.
        
        Places a market order with stop loss and take profit.
        """
        try:
            symbol = signal["symbol"]
            action = signal["action"]
            entry_price = signal["entry_price"]
            stop_loss = signal["stop_loss"]
            take_profit = signal["take_profit"]
            atr = signal.get("atr", 0)
            
            # Calculate position size
            position_size = self.calculate_position_size(symbol, entry_price, stop_loss)
            
            # Place bracket order
            side = "BUY" if action == "LONG" else "SELL"
            
            result = await self.trading_bot.create_bracket_order(
                symbol=symbol,
                side=side,
                quantity=position_size,
                stop_loss_price=stop_loss,
                take_profit_price=take_profit,
                account_id=self.trading_bot.selected_account if isinstance(self.trading_bot.selected_account, str) else self.trading_bot.selected_account.get('id') if isinstance(self.trading_bot.selected_account, dict) else None,
                strategy_name=self.config.name  # Add strategy name for tracking
            )

            # Fix: Check for 'success' and 'orderId' at top level (not 'order' key)
            if result and result.get('success') and result.get('orderId'):
                order_id = result.get('orderId')
                logger.info(f"✅ Trend following order placed: {side} {position_size} {symbol} (Order ID: {order_id})")
                
                # Verify order was actually placed in TopStepX
                try:
                    account_id = self.trading_bot.selected_account if isinstance(self.trading_bot.selected_account, str) else self.trading_bot.selected_account.get('id') if isinstance(self.trading_bot.selected_account, dict) else None
                    if account_id:
                        # Check open orders first
                        open_orders = await self.trading_bot.get_open_orders(account_id=account_id)
                        order_found = False
                        
                        if isinstance(open_orders, list):
                            for order in open_orders:
                                if str(order.get('id')) == str(order_id) or str(order.get('orderId')) == str(order_id):
                                    logger.info(f"✅ Order {order_id} verified in TopStepX open orders")
                                    order_found = True
                                    break
                        
                        # If not in open orders, check if it was filled immediately
                        if not order_found:
                            logger.debug(f"Order {order_id} not in open orders, checking if filled immediately...")
                            recent_orders = await self.trading_bot.get_order_history(account_id=account_id, limit=10)
                            if isinstance(recent_orders, list):
                                for order in recent_orders:
                                    if str(order.get('id')) == str(order_id) or str(order.get('orderId')) == str(order_id):
                                        logger.info(f"✅ Order {order_id} verified in TopStepX (immediate fill)")
                                        order_found = True
                                        break
                        
                        if not order_found:
                            logger.warning(f"⚠️  Order {order_id} not found in TopStepX - may have failed silently")
                    else:
                        logger.warning("⚠️  Cannot verify order - no account ID available")
                except Exception as verify_err:
                    logger.warning(f"⚠️  Could not verify order placement: {verify_err}")
                    # Don't fail the order, just log the warning
                
                # Track position
                self.active_positions[symbol] = {
                    "order_id": order_id,
                    "side": action,
                    "entry_price": entry_price,
                    "stop_loss": stop_loss,
                    "take_profit": take_profit,
                    "quantity": position_size,
                    "timestamp": datetime.now(),
                    "adds": 0  # Track pyramid adds
                }
                
                # Initialize trailing stop
                self.trailing_stops[symbol] = stop_loss
                
                return True
            else:
                error_msg = result.get('error', 'Unknown error') if isinstance(result, dict) else str(result)
                logger.error(f"❌ Failed to place trend following order for {symbol}: {error_msg}")
                if isinstance(result, dict) and result.get('raw_response'):
                    logger.debug(f"Full API response: {result.get('raw_response')}")
                return False
            
        except Exception as e:
            logger.error(f"Error executing signal: {e}")
            return False

    async def _sync_trailing_stop_to_broker(
        self, symbol: str, broker_position: dict, new_stop: float
    ) -> None:
        """Push tightened trailing stop to the broker when we have a position id."""
        position_id = (
            broker_position.get("id")
            or broker_position.get("positionId")
            or broker_position.get("position_id")
        )
        acct = getattr(self.trading_bot, "selected_account", None)
        account_id = acct.get("id") if isinstance(acct, dict) else None
        if not (account_id and position_id):
            logger.debug(
                "Trailing stop for %s updated locally only (missing account_id or position_id)",
                symbol,
            )
            return
        try:
            res = await self.trading_bot.modify_stop_loss(
                str(position_id), float(new_stop), account_id=str(account_id)
            )
            if isinstance(res, dict) and res.get("error"):
                logger.debug(
                    "Broker trailing stop not updated for %s: %s",
                    symbol,
                    res.get("error"),
                )
        except Exception as exc:
            logger.debug("modify_stop_loss failed for %s: %s", symbol, exc, exc_info=True)
    
    async def manage_positions(self):
        """
        Manage open trend following positions.
        
        Updates trailing stops and checks for trend reversals.
        """
        try:
            if not self.active_positions:
                return
            
            for symbol, position_data in list(self.active_positions.items()):
                # Get current positions from broker
                positions = await self.trading_bot.get_positions()
                broker_position = next((p for p in positions if p.get('symbol') == symbol), None) if positions else None
                
                if not broker_position:
                    # Position closed
                    logger.info(f"Position {symbol} closed")
                    if symbol in self.trailing_stops:
                        del self.trailing_stops[symbol]
                    del self.active_positions[symbol]
                    continue
                
                # Get current price and ATR
                current_price = broker_position.get('currentPrice', broker_position.get('lastPrice', 0))
                if not current_price:
                    continue
                
                atr = await self.calculate_atr(symbol)
                if not atr:
                    continue
                
                # Update trailing stop
                side = position_data["side"]
                entry_price = position_data["entry_price"]
                current_stop = self.trailing_stops.get(symbol, position_data["stop_loss"])
                
                if side == "LONG":
                    # For LONG: Trail stop up as price increases
                    new_stop = current_price - (atr * self.atr_trailing_multiplier)
                    if new_stop > current_stop:
                        self.trailing_stops[symbol] = new_stop
                        logger.info(f"🔼 Trailing stop updated for {symbol}: {current_stop:.2f} -> {new_stop:.2f}")
                        await self._sync_trailing_stop_to_broker(symbol, broker_position, new_stop)
                        
                elif side == "SHORT":
                    # For SHORT: Trail stop down as price decreases
                    new_stop = current_price + (atr * self.atr_trailing_multiplier)
                    if new_stop < current_stop:
                        self.trailing_stops[symbol] = new_stop
                        logger.info(f"🔽 Trailing stop updated for {symbol}: {current_stop:.2f} -> {new_stop:.2f}")
                        await self._sync_trailing_stop_to_broker(symbol, broker_position, new_stop)
                
                # Check for MA crossover reversal (exit signal)
                fast_ma = await self.calculate_moving_average(symbol, self.fast_ma_period)
                slow_ma = await self.calculate_moving_average(symbol, self.slow_ma_period)
                
                if fast_ma and slow_ma:
                    # If trend reversed, consider closing position
                    if side == "LONG" and fast_ma < slow_ma:
                        logger.info(f"⚠️ Trend reversal detected for LONG {symbol} - consider exit")
                    elif side == "SHORT" and fast_ma > slow_ma:
                        logger.info(f"⚠️ Trend reversal detected for SHORT {symbol} - consider exit")
                
        except Exception as e:
            logger.error(f"Error managing positions: {e}")
    
    async def cleanup(self):
        """Clean up strategy resources."""
        await self.stop()
    
    async def monitoring_loop(self):
        """Background task to continuously monitor for signals."""
        logger.info(f"📈 Trend following monitoring started")
        
        while self.is_trading:
            try:
                # Analyze each symbol
                for symbol in self.config.symbols:
                    symbol = symbol.strip()
                    if not symbol:
                        continue
                    
                    # Skip if already have position in this symbol
                    if symbol in self.active_positions:
                        continue
                    
                    # Analyze for signal
                    signal = await self.analyze(symbol)
                    
                    if signal:
                        # Execute signal
                        success = await self.execute(signal)
                        if success:
                            self.log_trade({"symbol": symbol, "pnl": 0.0})
                
                # Manage existing positions (trailing stops, etc.)
                await self.manage_positions()
                
                # Sleep before next iteration
                await asyncio.sleep(120)  # Check every 2 minutes (longer for trend following)
                
            except Exception as e:
                logger.error(f"Error in monitoring loop: {e}")
                await asyncio.sleep(120)
    
    async def start(self, symbols: List[str] = None):
        """Start the trend following strategy."""
        if self.is_trading:
            logger.warning("Trend following strategy is already running")
            return
        
        if symbols:
            self.config.symbols = symbols
        
        # Print initialization message
        strategy_details = [
            f"⏰ Timeframe: {self.timeframe}",
            f"📈 MA Crossover: {self.fast_ma_period}/{self.slow_ma_period} {self.ma_type}",
            f"📊 ATR: {self.atr_period} period, Stop={self.atr_stop_multiplier}x, Trail={self.atr_trailing_multiplier}x",
            f"🎯 Min Trend Strength: {self.min_trend_strength}",
            f"📦 Pyramiding: {'ENABLED' if self.pyramid_enabled else 'DISABLED'} (max {self.pyramid_max_adds} adds)",
            f"📦 Position Size: {self.config.position_size} contract(s) per order"
        ]
        self.print_initialization_message("Trend Following Strategy", self.config.symbols, strategy_details)
        
        self.is_trading = True
        self.status = StrategyStatus.ACTIVE
        
        # Start monitoring task
        self._monitoring_task = asyncio.create_task(self.monitoring_loop())
        
        logger.info(f"🚀 Trend Following Strategy started!")
        logger.info(f"   Symbols: {self.config.symbols}")
        logger.info(f"   Timeframe: {self.timeframe}")
    
    async def stop(self):
        """Stop the trend following strategy."""
        if not self.is_trading:
            logger.warning("Trend following strategy is not running")
            return
        
        self.is_trading = False
        self.status = StrategyStatus.IDLE
        
        # Cancel monitoring task
        if self._monitoring_task:
            self._monitoring_task.cancel()
        
        logger.info("🛑 Trend Following Strategy stopped!")
    
    def get_status(self) -> Dict:
        """Get trend following strategy status."""
        base_status = super().get_status()
        
        base_status.update({
            "timeframe": self.timeframe,
            "fast_ma_period": self.fast_ma_period,
            "slow_ma_period": self.slow_ma_period,
            "atr_trailing_multiplier": self.atr_trailing_multiplier,
            "active_positions": {
                symbol: {
                    "side": data["side"],
                    "entry": data["entry_price"],
                    "trailing_stop": self.trailing_stops.get(symbol, data["stop_loss"]),
                    "target": data["take_profit"]
                } for symbol, data in self.active_positions.items()
            }
        })
        
        return base_status

