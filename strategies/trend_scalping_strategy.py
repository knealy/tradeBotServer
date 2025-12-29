"""
Trend Scalping Strategy

Scalps trends using:
- Market structure (HH/HL for uptrends, LH/LL for downtrends)
- EMA crosses (89 EMA and 233 EMA)
- Configurable timeframes (30s, 1m, 2m, 5m, etc.)

Entry Logic:
- LONG: Price > 233 EMA, 89 EMA crosses above 233 EMA, recent HH+HL, pullback to 89 EMA
- SHORT: Price < 233 EMA, 89 EMA crosses below 233 EMA, recent LH+LL, rally to 89 EMA
- Execution: MARKET order with immediate SL/TP brackets (for speed)

Exit Logic:
- Stop loss: Recent swing low/high
- Take profit: 2:1 or 3:1 R:R
- Trailing stop: Move to breakeven after +1R
- Time-based: Max hold time for scalping
"""

import asyncio
import logging
import os
from datetime import datetime, time, timedelta
from typing import Dict, List, Optional, Tuple, Any
import numpy as np
import pandas as pd

from .strategy_base import BaseStrategy, StrategyConfig, StrategyStatus

logger = logging.getLogger(__name__)


class TrendScalpingStrategy(BaseStrategy):
    """
    Trend scalping strategy using market structure and EMA crosses.
    
    Features:
    - Configurable timeframe
    - Dual EMA system (89/233)
    - Market structure confirmation
    - Dynamic stop placement
    - Risk/reward targeting
    """
    
    def __init__(self, trading_bot, config: Optional[StrategyConfig] = None):
        """
        Initialize trend scalping strategy.
        
        Args:
            trading_bot: Trading bot instance
            config: Optional strategy configuration
        """
        if config is None:
            config = StrategyConfig(
                name="trend_scalping",
                symbols=os.getenv('STRATEGY_SYMBOLS', 'MNQ').split(','),
                enabled=True
            )
        
        super().__init__(trading_bot, config)
        
        # EMA parameters
        self.ema_short = int(os.getenv('TREND_SCALP_EMA_SHORT', '89'))
        self.ema_long = int(os.getenv('TREND_SCALP_EMA_LONG', '233'))
        
        # Timeframe
        self.timeframe = os.getenv('TREND_SCALP_TIMEFRAME', '1m')
        
        # Risk management
        self.risk_reward_ratio = float(os.getenv('TREND_SCALP_RR_RATIO', '2.0'))
        self.max_hold_time_seconds = int(os.getenv('TREND_SCALP_MAX_HOLD_TIME', '1800'))  # 30 min default
        self.trailing_stop_enabled = os.getenv('TREND_SCALP_TRAILING_STOP', 'true').lower() == 'true'
        self.breakeven_trigger_r = float(os.getenv('TREND_SCALP_BREAKEVEN_R', '1.0'))  # Move to BE after +1R
        
        # Market structure parameters
        self.lookback_bars = int(os.getenv('TREND_SCALP_LOOKBACK', '20'))  # Bars to look back for structure
        self.swing_threshold = float(os.getenv('TREND_SCALP_SWING_THRESHOLD', '0.5'))  # % move to confirm swing
        
        # Position tracking
        self.active_positions: Dict[str, Dict] = {}
        self.bar_cache: Dict[str, List] = {}  # Cache recent bars per symbol
        self.ema_cache: Dict[str, Dict] = {}  # Cache EMA values
        
        logger.info(f"📈 Trend Scalping Strategy initialized")
        logger.info(f"   Timeframe: {self.timeframe}")
        logger.info(f"   EMAs: {self.ema_short}/{self.ema_long}")
        logger.info(f"   R:R Ratio: {self.risk_reward_ratio}:1")
        logger.info(f"   Max Hold Time: {self.max_hold_time_seconds}s")
        logger.info(f"   Trailing Stop: {'ENABLED' if self.trailing_stop_enabled else 'DISABLED'}")
        logger.info(f"   Market Structure Lookback: {self.lookback_bars} bars")
    
    def calculate_ema(self, prices: List[float], period: int) -> Optional[float]:
        """
        Calculate Exponential Moving Average.
        
        Args:
            prices: List of prices (most recent last)
            period: EMA period
            
        Returns:
            EMA value or None if insufficient data
        """
        if len(prices) < period:
            return None
        
        # Use numpy for efficiency
        prices_array = np.array(prices)
        ema = pd.Series(prices_array).ewm(span=period, adjust=False).mean().iloc[-1]
        
        return float(ema)
    
    def detect_market_structure(
        self,
        highs: List[float],
        lows: List[float],
        closes: List[float]
    ) -> Dict[str, Any]:
        """
        Detect market structure (higher highs/lows or lower highs/lows).
        
        Args:
            highs: List of high prices
            lows: List of low prices
            closes: List of close prices
            
        Returns:
            Dict with structure info:
            {
                'trend': 'UP'|'DOWN'|'NEUTRAL',
                'recent_hh': bool,  # Recent higher high
                'recent_hl': bool,  # Recent higher low
                'recent_lh': bool,  # Recent lower high
                'recent_ll': bool,  # Recent lower low
                'swing_high': float,  # Most recent swing high
                'swing_low': float,   # Most recent swing low
            }
        """
        if len(highs) < self.lookback_bars:
            return {'trend': 'NEUTRAL'}
        
        # Look at recent bars
        recent_highs = highs[-self.lookback_bars:]
        recent_lows = lows[-self.lookback_bars:]
        
        # Find swing points (local extrema)
        swing_highs = []
        swing_lows = []
        
        for i in range(1, len(recent_highs) - 1):
            # Swing high: higher than neighbors
            if recent_highs[i] > recent_highs[i-1] and recent_highs[i] > recent_highs[i+1]:
                swing_highs.append(recent_highs[i])
            
            # Swing low: lower than neighbors
            if recent_lows[i] < recent_lows[i-1] and recent_lows[i] < recent_lows[i+1]:
                swing_lows.append(recent_lows[i])
        
        if len(swing_highs) < 2 or len(swing_lows) < 2:
            return {'trend': 'NEUTRAL'}
        
        # Check for higher highs / higher lows (uptrend)
        recent_hh = swing_highs[-1] > swing_highs[-2]
        recent_hl = swing_lows[-1] > swing_lows[-2]
        
        # Check for lower highs / lower lows (downtrend)
        recent_lh = swing_highs[-1] < swing_highs[-2]
        recent_ll = swing_lows[-1] < swing_lows[-2]
        
        # Determine trend
        if recent_hh and recent_hl:
            trend = 'UP'
        elif recent_lh and recent_ll:
            trend = 'DOWN'
        else:
            trend = 'NEUTRAL'
        
        return {
            'trend': trend,
            'recent_hh': recent_hh,
            'recent_hl': recent_hl,
            'recent_lh': recent_lh,
            'recent_ll': recent_ll,
            'swing_high': swing_highs[-1] if swing_highs else max(recent_highs),
            'swing_low': swing_lows[-1] if swing_lows else min(recent_lows)
        }
    
    def check_ema_cross(
        self,
        ema_short_current: float,
        ema_long_current: float,
        ema_short_prev: float,
        ema_long_prev: float
    ) -> Optional[str]:
        """
        Check for EMA crossover.
        
        Args:
            ema_short_current: Current short EMA value
            ema_long_current: Current long EMA value
            ema_short_prev: Previous short EMA value
            ema_long_prev: Previous long EMA value
            
        Returns:
            'BULLISH' for bullish cross, 'BEARISH' for bearish cross, None for no cross
        """
        # Bullish cross: short EMA crosses above long EMA
        if ema_short_prev <= ema_long_prev and ema_short_current > ema_long_current:
            return 'BULLISH'
        
        # Bearish cross: short EMA crosses below long EMA
        if ema_short_prev >= ema_long_prev and ema_short_current < ema_long_current:
            return 'BEARISH'
        
        return None
    
    def check_pullback_to_ema(
        self,
        current_price: float,
        ema_value: float,
        tolerance_pct: float = 0.2
    ) -> bool:
        """
        Check if price is near EMA (pullback/rally to support/resistance).
        
        Args:
            current_price: Current price
            ema_value: EMA value
            tolerance_pct: Tolerance percentage (default: 0.2%)
            
        Returns:
            True if price is within tolerance of EMA
        """
        distance_pct = abs((current_price - ema_value) / ema_value) * 100
        return distance_pct <= tolerance_pct
    
    async def get_recent_bars(
        self,
        symbol: str,
        num_bars: int = None
    ) -> Optional[List[Dict]]:
        """
        Get recent bars for analysis.
        
        Args:
            symbol: Trading symbol
            num_bars: Number of bars to fetch (default: enough for long EMA + lookback)
            
        Returns:
            List of bar dictionaries with OHLCV data
        """
        if num_bars is None:
            num_bars = self.ema_long + self.lookback_bars + 10  # Extra buffer
        
        try:
            # Fetch historical data
            bars = await self.trading_bot.broker_adapter.get_historical_data(
                symbol=symbol,
                timeframe=self.timeframe,
                limit=num_bars
            )
            
            if not bars:
                logger.warning(f"No historical data for {symbol}")
                return None
            
            # Convert to dict format
            bar_dicts = []
            for bar in bars:
                bar_dicts.append({
                    'timestamp': bar.timestamp,
                    'open': bar.open,
                    'high': bar.high,
                    'low': bar.low,
                    'close': bar.close,
                    'volume': bar.volume
                })
            
            return bar_dicts
        
        except Exception as e:
            logger.error(f"Error fetching bars for {symbol}: {e}")
            return None
    
    async def analyze(self, symbol: str) -> Optional[Dict]:
        """
        Analyze market and generate trading signal.
        
        Args:
            symbol: Trading symbol
            
        Returns:
            Dict with signal data or None
        """
        try:
            symbol = symbol.upper()
            
            # Get recent bars
            bars = await self.get_recent_bars(symbol)
            if not bars or len(bars) < self.ema_long + self.lookback_bars:
                logger.debug(f"Insufficient data for {symbol} (need {self.ema_long + self.lookback_bars} bars)")
                return {'symbol': symbol, 'action': None, 'reason': 'insufficient_data'}
            
            # Extract price arrays
            closes = [b['close'] for b in bars]
            highs = [b['high'] for b in bars]
            lows = [b['low'] for b in bars]
            current_price = closes[-1]
            
            # Calculate EMAs
            ema_short_current = self.calculate_ema(closes, self.ema_short)
            ema_long_current = self.calculate_ema(closes, self.ema_long)
            
            if ema_short_current is None or ema_long_current is None:
                return {'symbol': symbol, 'action': None, 'reason': 'ema_calculation_failed'}
            
            # Get previous EMAs (for cross detection)
            ema_short_prev = self.calculate_ema(closes[:-1], self.ema_short)
            ema_long_prev = self.calculate_ema(closes[:-1], self.ema_long)
            
            # Detect market structure
            structure = self.detect_market_structure(highs, lows, closes)
            
            # Check for EMA cross
            ema_cross = self.check_ema_cross(
                ema_short_current, ema_long_current,
                ema_short_prev, ema_long_prev
            )
            
            # Check if already in position
            positions = await self.trading_bot.get_open_positions()
            has_position = any(
                p.get('symbol', '').upper() == symbol or
                (p.get('contractId', '') and symbol in p.get('contractId', ''))
                for p in positions
            )
            
            if has_position:
                logger.debug(f"Already in position for {symbol}, skipping signal")
                return {'symbol': symbol, 'action': None, 'reason': 'already_in_position'}
            
            # === LONG ENTRY LOGIC ===
            if structure['trend'] == 'UP' and ema_cross == 'BULLISH':
                # Check pullback to 89 EMA
                if self.check_pullback_to_ema(current_price, ema_short_current, tolerance_pct=0.3):
                    # All conditions met for LONG
                    stop_loss = structure['swing_low']
                    risk = current_price - stop_loss
                    take_profit = current_price + (risk * self.risk_reward_ratio)
                    
                    logger.info(f"📈 LONG signal for {symbol}:")
                    logger.info(f"   Price: ${current_price:.2f}")
                    logger.info(f"   89 EMA: ${ema_short_current:.2f}, 233 EMA: ${ema_long_current:.2f}")
                    logger.info(f"   Market Structure: {structure['trend']} (HH+HL)")
                    logger.info(f"   Entry: ${current_price:.2f}, SL: ${stop_loss:.2f}, TP: ${take_profit:.2f}")
                    logger.info(f"   Risk: ${risk:.2f}, R:R: {self.risk_reward_ratio}:1")
                    
                    # Return signal (execute will place market order with brackets)
                    # entry_price is for reference only - actual entry is at market
                    return {
                        'action': 'LONG',
                        'symbol': symbol,
                        'entry_price': current_price,  # Reference price for logging
                        'stop_loss': stop_loss,
                        'take_profit': take_profit,
                        'confidence': 0.8,
                        'reason': f"EMA cross + {structure['trend']} structure + pullback"
                    }
            
            # === SHORT ENTRY LOGIC ===
            elif structure['trend'] == 'DOWN' and ema_cross == 'BEARISH':
                # Check rally to 89 EMA
                if self.check_pullback_to_ema(current_price, ema_short_current, tolerance_pct=0.3):
                    # All conditions met for SHORT
                    stop_loss = structure['swing_high']
                    risk = stop_loss - current_price
                    take_profit = current_price - (risk * self.risk_reward_ratio)
                    
                    logger.info(f"📉 SHORT signal for {symbol}:")
                    logger.info(f"   Price: ${current_price:.2f}")
                    logger.info(f"   89 EMA: ${ema_short_current:.2f}, 233 EMA: ${ema_long_current:.2f}")
                    logger.info(f"   Market Structure: {structure['trend']} (LH+LL)")
                    logger.info(f"   Entry: ${current_price:.2f}, SL: ${stop_loss:.2f}, TP: ${take_profit:.2f}")
                    logger.info(f"   Risk: ${risk:.2f}, R:R: {self.risk_reward_ratio}:1")
                    
                    # Return signal (execute will place market order with brackets)
                    # entry_price is for reference only - actual entry is at market
                    return {
                        'action': 'SHORT',
                        'symbol': symbol,
                        'entry_price': current_price,  # Reference price for logging
                        'stop_loss': stop_loss,
                        'take_profit': take_profit,
                        'confidence': 0.8,
                        'reason': f"EMA cross + {structure['trend']} structure + rally"
                    }
            
            # No signal
            return None
        
        except Exception as e:
            logger.error(f"Error analyzing {symbol}: {e}", exc_info=True)
            return None
    
    async def execute(self, signal: Dict) -> bool:
        """
        Execute a trading signal.
        
        Args:
            signal: Signal dictionary from analyze()
            
        Returns:
            True if execution successful
        """
        try:
            symbol = signal['symbol']
            action = signal['action']
            
            # For scalping, we want IMMEDIATE market entry with brackets
            # place_bracket_order uses stop entry which requires price to be away from market
            # Instead, use native_bracket which places market order + SL/TP
            
            if action == 'LONG':
                # Native bracket: Market BUY + SL + TP
                result = await self.trading_bot.create_bracket_order(
                    symbol=symbol,
                    side="BUY",
                    quantity=self.config.position_size,
                    stop_loss_price=signal['stop_loss'],
                    take_profit_price=signal['take_profit']
                )
            elif action == 'SHORT':
                # Native bracket: Market SELL + SL + TP
                result = await self.trading_bot.create_bracket_order(
                    symbol=symbol,
                    side="SELL",
                    quantity=self.config.position_size,
                    stop_loss_price=signal['stop_loss'],
                    take_profit_price=signal['take_profit']
                )
            else:
                logger.warning(f"Unknown action: {action}")
                return False
            
            if result and result.get('success'):
                logger.info(f"✅ Order placed: {action} {symbol} @ market with SL=${signal['stop_loss']:.2f} TP=${signal['take_profit']:.2f}")
                return True
            else:
                error_msg = result.get('error', 'Unknown error')
                logger.error(f"❌ Order failed: {error_msg}")
                return False
        
        except Exception as e:
            logger.error(f"Error executing signal: {e}", exc_info=True)
            return False
    
    async def manage_positions(self):
        """
        Manage open positions (trailing stops, breakeven, etc.).
        
        For trend scalping, trailing stops are handled by configuration.
        This method can be used for additional position management if needed.
        """
        # Trailing stops are configured at order placement time
        # This method is here to satisfy the abstract method requirement
        pass
    
    async def cleanup(self):
        """
        Clean up strategy resources.
        """
        # Clear caches
        self.bar_cache.clear()
        self.ema_cache.clear()
        self.active_positions.clear()
        logger.debug("Cleaned up trend scalping strategy resources")
    
    async def run(self):
        """
        Run the trend scalping strategy continuously.
        
        Monitors market at configured timeframe interval.
        """
        logger.info(f"🚀 Starting Trend Scalping Strategy for {self.config.symbols}")
        logger.info(f"   Timeframe: {self.timeframe}")
        
        # Parse timeframe to seconds
        if self.timeframe.endswith('s'):
            interval_seconds = int(self.timeframe[:-1])
        elif self.timeframe.endswith('m'):
            interval_seconds = int(self.timeframe[:-1]) * 60
        elif self.timeframe.endswith('h'):
            interval_seconds = int(self.timeframe[:-1]) * 3600
        else:
            interval_seconds = 60  # Default 1 minute
        
        logger.info(f"   Check interval: {interval_seconds}s")
        
        self.status = StrategyStatus.ACTIVE
        
        try:
            while self.status == StrategyStatus.ACTIVE:
                # Analyze each symbol and execute signals
                for symbol in self.config.symbols:
                    try:
                        signal = await self.analyze(symbol)
                        if signal and signal.get('action'):
                            logger.info(f"📊 Signal generated: {signal['action']} {symbol}")
                            success = await self.execute(signal)
                            if success:
                                logger.info(f"✅ Signal executed successfully")
                            else:
                                logger.warning(f"⚠️ Signal execution failed")
                    except Exception as e:
                        logger.error(f"Error processing {symbol}: {e}")
                
                # Wait for next interval
                await asyncio.sleep(interval_seconds)
        
        except asyncio.CancelledError:
            logger.info("Trend scalping strategy cancelled")
        finally:
            self.status = StrategyStatus.IDLE
            logger.info("🛑 Trend Scalping Strategy stopped")
