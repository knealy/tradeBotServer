"""
Mean Reversion Strategy Module

This module implements a trading strategy that:
1. Identifies overbought/oversold conditions using RSI
2. Trades mean reversion when price deviates from moving average
3. Best for ranging/choppy markets
4. Exits when price returns to mean

Strategy Logic:
- Calculate RSI (Relative Strength Index) and Moving Average
- Enter LONG when: RSI < 30 AND price < MA - (ATR * threshold)
- Enter SHORT when: RSI > 70 AND price > MA + (ATR * threshold)
- Exit when: Price returns to MA or hits stop loss
- Stop loss: 1.5x ATR from entry
"""

import os
import logging
import asyncio
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

from strategies.strategy_base import BaseStrategy, StrategyConfig, MarketCondition, StrategyStatus

logger = logging.getLogger(__name__)


@dataclass
class MeanReversionSignal:
    """Mean reversion signal data."""
    symbol: str
    action: str  # "LONG", "SHORT", "CLOSE"
    entry_price: float
    stop_loss: float
    take_profit: float
    rsi: float
    ma: float
    atr: float
    confidence: float
    reason: str


class MeanReversionStrategy(BaseStrategy):
    """
    Mean reversion strategy for ranging markets.
    
    Features:
    - RSI-based overbought/oversold detection
    - Moving average deviation tracking
    - ATR-based dynamic stops
    - Best for ranging/choppy markets
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
            config = StrategyConfig.from_env("MEAN_REVERSION")
        
        # Initialize base strategy
        super().__init__(trading_bot, config)
        
        # Mean reversion specific configuration
        self.rsi_period = int(os.getenv('MEAN_REV_RSI_PERIOD', '14'))
        self.rsi_overbought = float(os.getenv('MEAN_REV_RSI_OVERBOUGHT', '70'))
        self.rsi_oversold = float(os.getenv('MEAN_REV_RSI_OVERSOLD', '30'))
        
        self.ma_period = int(os.getenv('MEAN_REV_MA_PERIOD', '20'))
        self.ma_type = os.getenv('MEAN_REV_MA_TYPE', 'SMA')  # SMA or EMA
        
        self.atr_period = int(os.getenv('MEAN_REV_ATR_PERIOD', '14'))
        self.atr_deviation_threshold = float(os.getenv('MEAN_REV_ATR_DEVIATION', '2.0'))  # 2x ATR from MA
        
        self.stop_atr_multiplier = float(os.getenv('MEAN_REV_STOP_ATR', '1.5'))
        self.target_ma_return = os.getenv('MEAN_REV_TARGET_MA_RETURN', 'true').lower() == 'true'
        
        self.timeframe = os.getenv('MEAN_REV_TIMEFRAME', '5m')
        
        # State tracking
        self.is_trading = False
        self._monitoring_task: Optional[asyncio.Task] = None
        
        logger.info(f"🔄 Mean Reversion Strategy initialized")
        logger.info(f"   RSI: {self.rsi_period} period, OS<{self.rsi_oversold}, OB>{self.rsi_overbought}")
        logger.info(f"   MA: {self.ma_period} period {self.ma_type}")
        logger.info(f"   Entry: Price > {self.atr_deviation_threshold}x ATR from MA")
        logger.info(f"   Stop: {self.stop_atr_multiplier}x ATR")
        logger.info(f"   Target: {'Return to MA' if self.target_ma_return else 'Fixed TP'}")
    
    async def calculate_rsi(self, symbol: str, period: int = None) -> Optional[float]:
        """
        Calculate RSI (Relative Strength Index).
        
        RSI = 100 - (100 / (1 + RS))
        where RS = Average Gain / Average Loss
        
        Args:
            symbol: Trading symbol
            period: RSI period (default: from config)
        
        Returns:
            RSI value (0-100) or None if error
        """
        try:
            period = period or self.rsi_period
            
            # Fetch historical data (need period + 1 bars for changes)
            bars = await self.trading_bot.get_historical_data(
                symbol=symbol,
                timeframe=self.timeframe,
                limit=period + 1
            )
            
            if not bars or len(bars) < period + 1:
                return None
            
            # Calculate price changes
            changes = []
            for i in range(1, len(bars)):
                prev_close = bars[i-1].get('close', bars[i-1].get('c', 0))
                curr_close = bars[i].get('close', bars[i].get('c', 0))
                changes.append(curr_close - prev_close)
            
            # Separate gains and losses
            gains = [max(c, 0) for c in changes[-period:]]
            losses = [abs(min(c, 0)) for c in changes[-period:]]
            
            # Calculate average gain and loss
            avg_gain = sum(gains) / period
            avg_loss = sum(losses) / period
            
            # Calculate RSI
            if avg_loss == 0:
                return 100.0  # No losses = max RSI
            
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))
            
            return rsi
            
        except Exception as e:
            logger.error(f"Error calculating RSI for {symbol}: {e}")
            return None
    
    async def calculate_moving_average(self, symbol: str, period: int = None, ma_type: str = None) -> Optional[float]:
        """
        Calculate Moving Average (SMA or EMA).
        
        Args:
            symbol: Trading symbol
            period: MA period (default: from config)
            ma_type: 'SMA' or 'EMA' (default: from config)
        
        Returns:
            MA value or None if error
        """
        try:
            period = period or self.ma_period
            ma_type = ma_type or self.ma_type
            
            # Fetch historical data
            bars = await self.trading_bot.get_historical_data(
                symbol=symbol,
                timeframe=self.timeframe,
                limit=period if ma_type == 'SMA' else period * 2  # EMA needs more data
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
                ema = closes[0]  # Start with first close
                for close in closes[1:]:
                    ema = (close * multiplier) + (ema * (1 - multiplier))
                ma = ema
            
            return ma
            
        except Exception as e:
            logger.error(f"Error calculating MA for {symbol}: {e}")
            return None
    
    async def calculate_atr(self, symbol: str, period: int = None) -> Optional[float]:
        """
        Calculate ATR (Average True Range).
        
        Args:
            symbol: Trading symbol
            period: ATR period (default: from config)
        
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
                high = bars[i].get('high', bars[i].get('h', 0))
                low = bars[i].get('low', bars[i].get('l', 0))
                prev_close = bars[i-1].get('close', bars[i-1].get('c', 0))
                
                tr = max(
                    high - low,
                    abs(high - prev_close),
                    abs(low - prev_close)
                )
                true_ranges.append(tr)
            
            # Calculate ATR as average of True Ranges
            atr = sum(true_ranges[-period:]) / period
            return atr
            
        except Exception as e:
            logger.error(f"Error calculating ATR for {symbol}: {e}")
            return None
    
    async def analyze(self, symbol: str) -> Optional[Dict]:
        """
        Analyze market for mean reversion opportunities.
        
        Entry conditions:
        - LONG: RSI < oversold AND price < MA - (ATR * threshold)
        - SHORT: RSI > overbought AND price > MA + (ATR * threshold)
        
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
            rsi = await self.calculate_rsi(symbol)
            ma = await self.calculate_moving_average(symbol)
            atr = await self.calculate_atr(symbol)
            
            if rsi is None or ma is None or atr is None:
                return None
            
            logger.debug(f"{symbol}: Price={current_price:.2f}, RSI={rsi:.1f}, MA={ma:.2f}, ATR={atr:.2f}")
            
            # Check for mean reversion opportunities
            price_deviation = abs(current_price - ma)
            atr_threshold = atr * self.atr_deviation_threshold
            
            signal = None
            
            # LONG signal: Oversold + price below MA
            if rsi < self.rsi_oversold and current_price < (ma - atr_threshold):
                entry_price = current_price
                stop_loss = entry_price - (atr * self.stop_atr_multiplier)
                take_profit = ma if self.target_ma_return else entry_price + (atr * 2.0)
                
                confidence = min(1.0, (self.rsi_oversold - rsi) / self.rsi_oversold + 0.5)
                
                signal = {
                    "action": "LONG",
                    "symbol": symbol,
                    "entry_price": entry_price,
                    "stop_loss": stop_loss,
                    "take_profit": take_profit,
                    "confidence": confidence,
                    "reason": f"Oversold (RSI={rsi:.1f}) + below MA by {price_deviation:.2f} ({price_deviation/atr:.1f}x ATR)"
                }
            
            # SHORT signal: Overbought + price above MA
            elif rsi > self.rsi_overbought and current_price > (ma + atr_threshold):
                entry_price = current_price
                stop_loss = entry_price + (atr * self.stop_atr_multiplier)
                take_profit = ma if self.target_ma_return else entry_price - (atr * 2.0)
                
                confidence = min(1.0, (rsi - self.rsi_overbought) / (100 - self.rsi_overbought) + 0.5)
                
                signal = {
                    "action": "SHORT",
                    "symbol": symbol,
                    "entry_price": entry_price,
                    "stop_loss": stop_loss,
                    "take_profit": take_profit,
                    "confidence": confidence,
                    "reason": f"Overbought (RSI={rsi:.1f}) + above MA by {price_deviation:.2f} ({price_deviation/atr:.1f}x ATR)"
                }
            
            if signal:
                logger.info(f"📊 Mean Reversion Signal: {signal['action']} {symbol} @ {signal['entry_price']:.2f}")
                logger.info(f"   {signal['reason']}")
            
            return signal
            
        except Exception as e:
            logger.error(f"Error analyzing {symbol}: {e}")
            return None
    
    async def execute(self, signal: Dict) -> bool:
        """
        Execute mean reversion trade.
        
        Places a market order with stop loss and take profit.
        """
        try:
            symbol = signal["symbol"]
            action = signal["action"]
            entry_price = signal["entry_price"]
            stop_loss = signal["stop_loss"]
            take_profit = signal["take_profit"]
            
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
            
            if result and 'order' in result:
                order_id = result['order'].get('orderId')
                logger.info(f"✅ Mean reversion order placed: {side} {position_size} {symbol} (Order ID: {order_id})")
                
                # Track position
                self.active_positions[symbol] = {
                    "order_id": order_id,
                    "side": action,
                    "entry_price": entry_price,
                    "stop_loss": stop_loss,
                    "take_profit": take_profit,
                    "quantity": position_size,
                    "timestamp": datetime.now()
                }
                
                return True
            else:
                logger.error(f"❌ Failed to place mean reversion order for {symbol}")
                return False
            
        except Exception as e:
            logger.error(f"Error executing signal: {e}")
            return False
    
    async def manage_positions(self):
        """
        Manage open mean reversion positions.
        
        Can implement trailing stops, MA return exits, etc.
        """
        try:
            if not self.active_positions:
                return
            
            for symbol, position_data in list(self.active_positions.items()):
                # Get current positions from broker
                positions = await self.trading_bot.get_positions()
                
                # Check if position still exists
                broker_position = next((p for p in positions if p.get('symbol') == symbol), None) if positions else None
                
                if not broker_position:
                    # Position closed
                    logger.info(f"Position {symbol} closed")
                    del self.active_positions[symbol]
                    continue
                
                # Could add trailing stop logic here
                # Or check if price returned to MA for early exit
                
        except Exception as e:
            logger.error(f"Error managing positions: {e}")
    
    async def cleanup(self):
        """
        Clean up strategy resources.
        """
        await self.stop()
    
    async def monitoring_loop(self):
        """
        Background task to continuously monitor for signals.
        """
        logger.info(f"🔄 Mean reversion monitoring started")
        
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
                            self.log_trade({"symbol": symbol, "pnl": 0.0})  # Initial log
                
                # Manage existing positions
                await self.manage_positions()
                
                # Sleep before next iteration
                await asyncio.sleep(60)  # Check every 60 seconds
                
            except Exception as e:
                logger.error(f"Error in monitoring loop: {e}")
                await asyncio.sleep(60)
    
    async def start(self, symbols: List[str] = None):
        """
        Start the mean reversion strategy.
        
        Args:
            symbols: List of symbols to trade (default: from config)
        """
        if self.is_trading:
            logger.warning("Mean reversion strategy is already running")
            return
        
        if symbols:
            self.config.symbols = symbols
        
        self.is_trading = True
        self.status = StrategyStatus.ACTIVE
        
        # Start monitoring task
        self._monitoring_task = asyncio.create_task(self.monitoring_loop())
        
        logger.info(f"🚀 Mean Reversion Strategy started!")
        logger.info(f"   Symbols: {self.config.symbols}")
        logger.info(f"   Timeframe: {self.timeframe}")
    
    async def stop(self):
        """Stop the mean reversion strategy."""
        if not self.is_trading:
            logger.warning("Mean reversion strategy is not running")
            return
        
        self.is_trading = False
        self.status = StrategyStatus.IDLE
        
        # Cancel monitoring task
        if self._monitoring_task:
            self._monitoring_task.cancel()
        
        logger.info("🛑 Mean Reversion Strategy stopped!")
    
    def get_status(self) -> Dict:
        """Get mean reversion strategy status."""
        base_status = super().get_status()
        
        base_status.update({
            "timeframe": self.timeframe,
            "rsi_period": self.rsi_period,
            "ma_period": self.ma_period,
            "atr_deviation_threshold": self.atr_deviation_threshold,
            "active_positions": {
                symbol: {
                    "side": data["side"],
                    "entry": data["entry_price"],
                    "stop": data["stop_loss"],
                    "target": data["take_profit"]
                } for symbol, data in self.active_positions.items()
            }
        })
        
        return base_status

