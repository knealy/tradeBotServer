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
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from strategies.strategy_base import BaseStrategy, StrategyConfig, MarketCondition, StrategyStatus
from core.trend_detector import TrendDetector

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
                trading_start_time="16:30",
                trading_end_time="23:00",
                no_trade_start="",
                no_trade_end="",
                respect_dll=True,
                respect_mll=True,
                max_dll_usage_percent=0.70
            )
        
        super().__init__(trading_bot, config)
        
        # Strategy-specific settings
        self.candles_needed = 2  # Need 2 consecutive candles
        self.profit_multiplier = 2.0    # Take profit at 2 * ATR
        self.stop_multiplier = 1.5      # Stop loss at 1.5 * ATR
        self.atr_period = 14            # ATR period (14 bars)
        
        # EMA settings for cross detection
        self.ema_fast_period = 8   # Fast EMA period
        self.ema_slow_period = 21  # Slow EMA period
        
        # Track previous EMA values per symbol to detect crosses
        self.prev_emas: Dict[str, Dict[str, float]] = {}  # {symbol: {"fast": float, "slow": float}}
        
        # Trend detection (optional filter - VERY conservative to start)
        self.use_trend_filter = os.getenv('USE_TREND_FILTER', 'true').lower() == 'true'
        if self.use_trend_filter:
            # VERY conservative: min_score=40 (out of 100)
            # This will only block the most choppy markets
            self.trend_detector = TrendDetector(
                ema_fast_period=self.ema_fast_period,
                ema_slow_period=self.ema_slow_period,
                atr_period=self.atr_period,
                min_trend_score=30,  # VERY conservative - only block extreme chop
                lookback_bars=10
            )
            logger.info(f"✅ Trend filter ENABLED (min_score=40 - very conservative)")
        else:
            self.trend_detector = None
            logger.info(f"⚠️  Trend filter DISABLED")
        
        # Cache for position data (to reduce API calls)
        self._position_cache: Optional[List[Dict]] = None
        self._position_cache_time: float = 0
        self._position_cache_ttl: float = 5.0  # Cache TTL in seconds
        
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
    
    async def check_ema_distance(self, symbol: str, threshold: float = 20.0) -> bool:
        """
        Check if EMA distance (separation) exceeds threshold.
        
        When EMAs are too far apart, it may indicate extreme trend extension
        or potential reversal, so we flatten positions as a risk management measure.
        
        Args:
            symbol: Trading symbol
            threshold: Distance threshold in points (default: 20.0)
        
        Returns:
            True if distance >= threshold, False otherwise
        """
        try:
            # Calculate current EMAs
            fast_ema = await self.calculate_ema(symbol, self.ema_fast_period)
            slow_ema = await self.calculate_ema(symbol, self.ema_slow_period)
            
            if fast_ema is None or slow_ema is None:
                return False
            
            # Calculate absolute distance
            distance = abs(fast_ema - slow_ema)
            
            if distance >= threshold:
                direction = "LONG" if fast_ema > slow_ema else "SHORT"
                logger.warning(
                    f"🛑 EMA DISTANCE EXCEEDED for {symbol}: "
                    f"Distance={distance:.2f} points (threshold={threshold}), "
                    f"Fast EMA={fast_ema:.2f}, Slow EMA={slow_ema:.2f}, Direction={direction}"
                )
                print(
                    f"🛑 EMA DISTANCE EXCEEDED: {distance:.2f} points >= {threshold} "
                    f"({direction} direction) for {symbol}"
                )
                return True
            
            return False
            
        except Exception as e:
            logger.error(f"Error checking EMA distance for {symbol}: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False
    
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
            # Determine current position direction for this symbol (if any)
            # Only place orders in the direction of the current position unless flat.
            # Use the CLI command path to check current positions (more reliable)
            pos_side: Optional[str] = None  # "LONG" | "SHORT" | None
            try:
                # Get account ID
                account_id = None
                if isinstance(self.trading_bot.selected_account, dict):
                    account_id = self.trading_bot.selected_account.get('id')
                else:
                    account_id = self.trading_bot.selected_account
                
                logger.info(f"🔍 Position check for {symbol}: account_id={account_id}")
                
                if account_id:
                    # Use cached positions if available and fresh (reduces API calls)
                    import time
                    current_time = time.time()
                    if (self._position_cache is not None and 
                        (current_time - self._position_cache_time) < self._position_cache_ttl):
                        positions = self._position_cache
                        logger.debug(f"Using cached positions ({current_time - self._position_cache_time:.1f}s old)")
                    else:
                        # Fetch current positions directly from trading bot (same as CLI)
                        positions = await self.trading_bot.get_open_positions(account_id=account_id)
                        self._position_cache = positions
                        self._position_cache_time = current_time
                        logger.debug(f"Fetched fresh positions from API")
                    
                    logger.info(f"🔍 Retrieved {len(positions) if positions else 0} total positions")
                    
                    if positions:
                        for i, pos in enumerate(positions):
                            if not isinstance(pos, dict):
                                logger.warning(f"Position {i} is not a dict: {type(pos)}")
                                continue
                            
                            # Log FULL position data to debug symbol and side issues
                            logger.warning(f"🔍 RAW Position {i} (FULL DATA): {pos}")
                            print(f"🔍 RAW Position {i}: {pos}")
                            
                            pos_symbol = pos.get('symbol') or pos.get('Symbol') or pos.get('ticker') or ''
                            logger.info(f"🔍 Position {i}: symbol='{pos_symbol}' (checking against '{symbol}')")
                            
                            # Check if this position matches our symbol
                            if pos_symbol.upper() != symbol.upper():
                                logger.debug(f"Position symbol '{pos_symbol}' doesn't match '{symbol}', skipping")
                                continue
                            
                            # Get position side - check multiple sources
                            side_val = pos.get('side')
                            qty = pos.get('quantity', pos.get('size', 0))
                            pos_type = pos.get('type')  # Type field might indicate direction
                            
                            # Also check if Position object already converted side to string
                            side_str = pos.get('side') if isinstance(pos.get('side'), str) else None
                            
                            logger.info(f"🔍 Matched position for {symbol}: side_val={side_val} (type={type(side_val)}), qty={qty}, pos_type={pos_type}, side_str={side_str}")
                            
                            # Priority 1: Check type field (TopStepX may use type: 1=LONG, 2=SHORT)
                            if pos_type is not None:
                                if pos_type == 1:
                                    pos_side = "LONG"
                                    logger.info(f"Position side determined from type field: {pos_side} (type={pos_type})")
                                elif pos_type == 2:
                                    pos_side = "SHORT"
                                    logger.info(f"Position side determined from type field: {pos_side} (type={pos_type})")
                            
                            # Priority 2: Check if side is already a string (most reliable)
                            if pos_side is None and side_str:
                                s = side_str.upper()
                                if s in ("LONG", "BUY"):
                                    pos_side = "LONG"
                                elif s in ("SHORT", "SELL"):
                                    pos_side = "SHORT"
                            
                            # Priority 3: Determine side from side_val
                            # NOTE: TopStepX convention is 0=LONG, 1=SHORT, but if type field exists and conflicts,
                            # we trust type field more. If type is missing, use side_val.
                            side_val_side = None
                            if isinstance(side_val, str):
                                s = side_val.upper()
                                if s in ("LONG", "BUY"):
                                    side_val_side = "LONG"
                                elif s in ("SHORT", "SELL"):
                                    side_val_side = "SHORT"
                            elif isinstance(side_val, int):
                                # Standard TopStepX: 0=LONG, 1=SHORT
                                if side_val == 0:
                                    side_val_side = "LONG"
                                elif side_val == 1:
                                    side_val_side = "SHORT"
                                else:
                                    # Unknown integer - try to infer
                                    if side_val > 0:
                                        side_val_side = "LONG"
                                    else:
                                        side_val_side = "SHORT"
                            
                            # If type field exists and conflicts with side_val, trust type
                            if pos_side and side_val_side and pos_side != side_val_side:
                                logger.warning(f"⚠️  CONFLICT: type={pos_type} says {pos_side}, but side_val={side_val} says {side_val_side}. Using type field ({pos_side}).")
                                print(f"⚠️  CONFLICT: type={pos_type} ({pos_side}) vs side_val={side_val} ({side_val_side}). Using {pos_side}.")
                            elif pos_side is None and side_val_side:
                                pos_side = side_val_side
                            
                            # Priority 4: Check quantity sign (negative = SHORT, positive = LONG)
                            # BUT: TopStepX uses side_val as primary, quantity is always positive
                            # So: side_val=1 with qty=17 means SHORT 17, not LONG 17
                            qty_side = None
                            if qty != 0:
                                if qty < 0:
                                    qty_side = "SHORT"
                                else:
                                    # Positive quantity: use side_val to determine direction
                                    # If side_val says SHORT, it's SHORT even with positive qty
                                    if side_val_side:
                                        qty_side = side_val_side
                                    else:
                                        qty_side = "LONG"  # Default to LONG for positive qty
                                logger.info(f"Quantity-based side: {qty_side} (qty={qty}, side_val={side_val})")
                            
                            # Final determination: pos_side should already be set from type or side_val
                            # If still None, use quantity as last resort
                            if pos_side is None:
                                if qty_side:
                                    pos_side = qty_side
                                    logger.info(f"Position side determined from quantity as fallback: {pos_side}")
                                else:
                                    logger.error(f"❌ Could not determine position side: side_val={side_val}, qty={qty}, type={pos_type}")
                            
                            # First match is enough
                            if pos_side:
                                logger.warning(f"✅ Found {pos_side} position for {symbol}: qty={qty}, side_val={side_val}")
                                print(f"✅ POSITION DETECTED: {pos_side} {qty} {symbol} (side_val={side_val})")
                                break
                            else:
                                logger.error(f"❌ Could not determine position side for {symbol}: side_val={side_val}, qty={qty}")
                                print(f"❌ FAILED TO PARSE POSITION: symbol={symbol}, side_val={side_val}, qty={qty}")
                    else:
                        logger.info(f"🔍 No positions returned for account {account_id}")
                else:
                    logger.warning(f"🔍 No account_id available for position check")
                    
                if pos_side:
                    logger.warning(f"📊 DETECTED {pos_side} POSITION for {symbol}")
                else:
                    logger.info(f"📊 No position detected for {symbol} - FLAT")
                    
            except Exception as e:
                logger.error(f"❌ Error checking current positions for {symbol}: {e}")
                import traceback
                logger.error(traceback.format_exc())
                pos_side = None

            # Get recent bars using configured timeframe (need at least 2)
            # Need more bars for trend detection
            bars_needed = max(50, self.ema_slow_period * 2)  # Enough for trend analysis
            bars = await self.trading_bot.get_historical_data(
                symbol=symbol,
                timeframe=self.timeframe,
                limit=bars_needed
            )
            
            # New logic requires 3 bars (two consecutive "distance" conditions)
            if not bars or len(bars) < 3:
                return None
            
            # Check trend quality if filter is enabled
            if self.use_trend_filter and self.trend_detector:
                try:
                    # Calculate EMAs for trend detection
                    fast_ema = await self.calculate_ema(symbol, self.ema_fast_period)
                    slow_ema = await self.calculate_ema(symbol, self.ema_slow_period)
                    
                    if fast_ema and slow_ema:
                        trend_score = await self.trend_detector.calculate_trend_quality(
                            symbol=symbol,
                            bars=bars,
                            fast_ema=fast_ema,
                            slow_ema=slow_ema
                        )
                        
                        # Log trend quality (only if below threshold to reduce noise)
                        if not trend_score.is_trending:
                            logger.warning(
                                f"🚫 CHOPPY MARKET: Trend score {trend_score.total_score}/100 "
                                f"(EMA sep: {trend_score.ema_separation_score}, "
                                f"Dir: {trend_score.direction_consistency_score}, "
                                f"Mom: {trend_score.momentum_score}, "
                                f"ATR: {trend_score.atr_expansion_score}, "
                                f"Candles: {trend_score.candle_consistency_score}) - "
                                f"Skipping trade"
                            )
                            print(
                                f"🚫 CHOPPY: Score {trend_score.total_score}/100 "
                                f"(min: {self.trend_detector.min_trend_score}) - Trade BLOCKED"
                            )
                            return None  # Block trade in choppy market
                        else:
                            # Log occasionally for trending markets (every 10th check)
                            import random
                            if random.randint(1, 10) == 1:  # 10% chance to log
                                logger.info(
                                    f"🟢 TRENDING: Score {trend_score.total_score}/100 "
                                    f"({trend_score.trend_direction or 'NEUTRAL'}) - Trade allowed"
                                )
                    else:
                        logger.debug(f"Could not calculate EMAs for trend detection, allowing trade")
                except Exception as e:
                    logger.warning(f"Error in trend detection, allowing trade: {e}")
                    # On error, allow trade (fail open)
            
            # Get the last 3 candles (two consecutive distance checks)
            c0 = bars[-3]  # older
            c1 = bars[-2]
            c2 = bars[-1]  # latest
            
            # Extract OHLC data (handle different formats)
            def get_close(bar):
                return bar.get('close', bar.get('Close', bar.get('c', 0)))

            def get_open(bar):
                return bar.get('open', bar.get('Open', bar.get('o', 0)))

            def get_high(bar):
                return bar.get('high', bar.get('High', bar.get('h', 0)))

            def get_low(bar):
                return bar.get('low', bar.get('Low', bar.get('l', 0)))

            c0_high = get_high(c0)
            c0_low = get_low(c0)
            c1_close = get_close(c1)
            c1_high = get_high(c1)
            c1_low = get_low(c1)
            c2_close = get_close(c2)
            c2_high = get_high(c2)
            c2_low = get_low(c2)

            if c0_high == 0 or c0_low == 0 or c1_close == 0 or c2_close == 0:
                return None
            
            # New "distance candle" logic:
            # - LONG signal requires 2 consecutive positive distance closes:
            #   c1.close > c0.high AND c2.close > c1.high
            # - SHORT signal requires 2 consecutive negative distance closes:
            #   c1.close < c0.low  AND c2.close < c1.low
            long_distance_ok = (c1_close > c0_high) and (c2_close > c1_high)
            short_distance_ok = (c1_close < c0_low) and (c2_close < c1_low)

            # Position-direction gating:
            # - If flat: allow either direction
            # - If long: allow only LONG
            # - If short: allow only SHORT
            allow_long = (pos_side is None) or (pos_side == "LONG")
            allow_short = (pos_side is None) or (pos_side == "SHORT")
            
            # Log when signals are blocked by position gating
            if long_distance_ok and not allow_long:
                msg = f"🚫 LONG signal BLOCKED for {symbol} - Current position is {pos_side}, cannot add opposing orders"
                logger.warning(msg)
                print(msg)
            if short_distance_ok and not allow_short:
                msg = f"🚫 SHORT signal BLOCKED for {symbol} - Current position is {pos_side}, cannot add opposing orders"
                logger.warning(msg)
                print(msg)

            if long_distance_ok and allow_long:
                # 2 consecutive bullish candles = LONG signal
                logger.info(f"LONG signal condition met for {symbol} - Current position: {pos_side or 'FLAT'}, Allow long: {allow_long}")
                
                # Calculate ATR for stop/profit levels
                atr = await self.calculate_atr(symbol, self.atr_period)
                if atr is None or atr <= 0:
                    logger.warning(f"Could not calculate ATR for {symbol}, skipping signal")
                    return None
                
                # Get current market quote to ensure entry price is above current market
                # For stop_bracket LONG: entry (stop buy) must be above current market price
                quote = await self.trading_bot.get_market_quote(symbol)
                if quote and "error" not in quote:
                    current_bid = quote.get('bid', c2_close)
                    current_ask = quote.get('ask', c2_close)
                    current_last = quote.get('last', current_ask)
                    
                    # For LONG stop order, entry must be above current market
                    # Use the highest of bid/ask/last as base
                    current_price = max(current_bid, current_ask, current_last) if all([current_bid, current_ask]) else c2_close
                    
                    # Entry should be at least 4 ticks above current market (0.25 tick size for MNQ)
                    entry_price = current_price + (4 * 0.25)  # 1 point above market
                    
                    logger.info(f"LONG entry calc: bid={current_bid}, ask={current_ask}, last={current_last}, entry={entry_price:.2f}")
                else:
                    # Fallback: use candle close + buffer
                    current_price = c2_close
                    entry_price = current_price + (4 * 0.25)  # 1 point above for stop buy
                    logger.warning(f"Could not get market quote for {symbol}, using candle close: {entry_price:.2f}")
                
                stop_loss = entry_price - (self.stop_multiplier * atr)  # Stop loss below entry price (1.5 * ATR)
                take_profit = entry_price + (self.profit_multiplier * atr)  # Take profit above entry price (3 * ATR)
                
                return {
                    "action": "LONG",
                    "symbol": symbol,
                    "entry_price": entry_price,  # Stop buy price (above current)
                    "stop_loss": stop_loss,
                    "take_profit": take_profit,
                    "confidence": 0.6,
                    "reason": (
                        f"2 consecutive +distance closes: c1.close({c1_close:.2f})>c0.high({c0_high:.2f}) "
                        f"and c2.close({c2_close:.2f})>c1.high({c1_high:.2f}), ATR: {atr:.2f} "
                        f"[POSITION: {pos_side or 'FLAT'}, ALLOWED: LONG only]"
                    )
                }
            
            if short_distance_ok and allow_short:
                # 2 consecutive bearish candles = SHORT signal
                logger.info(f"SHORT signal condition met for {symbol} - Current position: {pos_side or 'FLAT'}, Allow short: {allow_short}")
                
                # Calculate ATR for stop/profit levels
                atr = await self.calculate_atr(symbol, self.atr_period)
                if atr is None or atr <= 0:
                    logger.warning(f"Could not calculate ATR for {symbol}, skipping signal")
                    return None
                
                # Get current market quote to ensure entry price is below current market
                # For stop_bracket SHORT: entry (stop sell) must be below current market price
                quote = await self.trading_bot.get_market_quote(symbol)
                if quote and "error" not in quote:
                    current_bid = quote.get('bid', c2_close)
                    current_ask = quote.get('ask', c2_close)
                    current_last = quote.get('last', current_bid)
                    
                    # For SHORT stop order, entry must be below current market
                    # Use the lowest of bid/ask/last as base
                    current_price = min(current_bid, current_ask, current_last) if all([current_bid, current_ask]) else c2_close
                    
                    # Entry should be at least 4 ticks below current market (0.25 tick size for MNQ)
                    entry_price = current_price - (4 * 0.25)  # 1 point below market
                    
                    logger.info(f"SHORT entry calc: bid={current_bid}, ask={current_ask}, last={current_last}, entry={entry_price:.2f}")
                else:
                    # Fallback: use candle close - buffer
                    current_price = c2_close
                    entry_price = current_price - (4 * 0.25)  # 1 point below for stop sell
                    logger.warning(f"Could not get market quote for {symbol}, using candle close: {entry_price:.2f}")
                
                stop_loss = entry_price + (self.stop_multiplier * atr)  # Stop loss above entry price (1.5 * ATR)
                take_profit = entry_price - (self.profit_multiplier * atr)  # Take profit below entry price (3 * ATR)
                
                return {
                    "action": "SHORT",
                    "symbol": symbol,
                    "entry_price": entry_price,
                    "stop_loss": stop_loss,
                    "take_profit": take_profit,
                    "confidence": 0.6,
                    "reason": (
                        f"2 consecutive -distance closes: c1.close({c1_close:.2f})<c0.low({c0_low:.2f}) "
                        f"and c2.close({c2_close:.2f})<c1.low({c1_low:.2f}), ATR: {atr:.2f} "
                        f"[POSITION: {pos_side or 'FLAT'}, ALLOWED: SHORT only]"
                    )
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
        # Just track active positions and refresh cache
        try:
            account_id = None
            if isinstance(self.trading_bot.selected_account, dict):
                account_id = self.trading_bot.selected_account.get('id')
            else:
                account_id = self.trading_bot.selected_account
            
            if not account_id:
                return
            
            # Fetch and cache positions
            import time
            positions = await self.trading_bot.get_open_positions(account_id=account_id)
            self._position_cache = positions
            self._position_cache_time = time.time()
            
            if positions:
                # Update active positions list
                self.active_positions = [
                    pos for pos in positions 
                    if pos.get('symbol') in self.config.symbols
                ]
                logger.debug(f"Manage positions: {len(self.active_positions)} active for strategy symbols")
            else:
                self.active_positions = []
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
        
        # Print initialization message
        strategy_details = [
            f"⏰ Timeframe: {self.timeframe}",
            f"📈 ATR Period: {self.atr_period} bars",
            f"🎯 Take Profit: {self.profit_multiplier}x ATR",
            f"🛑 Stop Loss: {self.stop_multiplier}x ATR",
            f"📊 EMA Cross Detection: {self.ema_fast_period}EMA / {self.ema_slow_period}EMA",
            f"🔄 Trend Filter: {'ENABLED' if self.use_trend_filter else 'DISABLED'}",
            f"📦 Position Size: {self.config.position_size} contract(s) per order"
        ]
        self.print_initialization_message("Simple Candle Strategy", self.config.symbols, strategy_details)
        
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
        last_position_count = 0  # Track position count changes

        while self.status == StrategyStatus.ACTIVE:
            try:
                loop_count += 1
                current_position_count = len(self.active_positions)
                
                # Only log/print on state change (position count changed) or every 30 loops (~5 minutes)
                if current_position_count != last_position_count or loop_count % 30 == 0:
                    logger.debug(f"🔄 Strategy running... ({current_position_count} positions)")
                    # Only print to terminal on position changes, not every loop
                    if current_position_count != last_position_count:
                        print(f"🔄 Strategy running... ({current_position_count} positions)")
                    last_position_count = current_position_count
                
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
                
                # Check for EMA distance >= 20 points and flatten if detected
                for symbol in self.config.symbols:
                    distance_exceeded = await self.check_ema_distance(symbol, threshold=20.0)
                    if distance_exceeded:
                        logger.warning(f"🛑 EMA DISTANCE >= 20 points for {symbol} - FLATTENING ALL POSITIONS AND ORDERS")
                        print(f"🛑 EMA DISTANCE >= 20 points for {symbol} - FLATTENING ALL POSITIONS AND ORDERS")
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
                        # Broadcast to GUI + Discord via StrategyManager helper (this strategy runs its own loop)
                        try:
                            if hasattr(self.trading_bot, 'strategy_manager') and self.trading_bot.strategy_manager:
                                signal_data = {
                                    'type': signal.get('action', 'SIGNAL'),
                                    'strategy': self.config.name,
                                    'symbol': signal.get('symbol', symbol),
                                    'message': signal.get('reason', f"{signal.get('action', 'SIGNAL')} signal generated"),
                                    'entry_price': signal.get('entry_price'),
                                    'stop_loss': signal.get('stop_loss'),
                                    'take_profit': signal.get('take_profit'),
                                    'direction': signal.get('action', 'SIGNAL'),
                                    'timestamp': datetime.now(timezone.utc).isoformat(),
                                }
                                await self.trading_bot.strategy_manager.broadcast_signal(signal_data)
                        except Exception as e:
                            logger.debug(f"Could not broadcast strategy signal: {e}")
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
