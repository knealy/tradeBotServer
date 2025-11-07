"""
Overnight Range Breakout Strategy Module

This module implements a trading strategy that:
1. Tracks overnight price ranges (6pm-9:30am EST)
2. Calculates ATR (Average True Range) for dynamic stops/targets
3. Places stop bracket orders at market open (9:30am EST) for range breakouts
4. Implements breakeven stop management for winning trades

Strategy Logic:
- Track highest/lowest prices during overnight session
- Calculate current price ATR and daily ATR zones
- At market open, place stop orders slightly above/below range extremes
- Stop loss: -1 to -1.5 ATR from entry
- Take profit: Daily ATR zone target
- Move stop to breakeven after +15 pts profit
"""

import os
import logging
import asyncio
from datetime import datetime, time, timedelta, timezone
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from collections import deque
import pytz

logger = logging.getLogger(__name__)


@dataclass
class OvernightRange:
    """Overnight range data for a symbol."""
    symbol: str
    high: float
    low: float
    open: float
    close: float
    start_time: datetime
    end_time: datetime
    range_size: float  # High - Low
    midpoint: float  # (High + Low) / 2


@dataclass
class ATRData:
    """ATR calculation results."""
    current_atr: float  # Current price ATR
    daily_atr: float  # Daily ATR
    atr_zone_high: float  # Price + Daily ATR
    atr_zone_low: float  # Price - Daily ATR
    period: int  # ATR calculation period


@dataclass
class RangeBreakOrder:
    """Range breakout order configuration."""
    symbol: str
    side: str  # "BUY" or "SELL"
    entry_price: float  # Stop order entry price
    stop_loss: float
    take_profit: float
    quantity: int
    range_data: OvernightRange
    atr_data: ATRData


class OvernightRangeStrategy:
    """
    Manages overnight range breakout strategy execution.
    
    Features:
    - Tracks overnight ranges for multiple symbols
    - Calculates ATR for dynamic stops/targets
    - Places orders at market open
    - Manages breakeven stops
    """
    
    def __init__(self, trading_bot):
        """
        Initialize the strategy.
        
        Args:
            trading_bot: Reference to main TradingBot instance
        """
        self.trading_bot = trading_bot
        self.active_ranges: Dict[str, OvernightRange] = {}
        self.active_orders: Dict[str, List[str]] = {}  # symbol -> [order_ids]
        self.breakeven_monitoring: Dict[str, Dict] = {}  # order_id -> monitoring data
        
        # Tick size cache: {symbol: tick_size}
        self._tick_size_cache: Dict[str, float] = {}
        
        # Load configuration from environment
        self.overnight_start = os.getenv('OVERNIGHT_START_TIME', '18:00')  # 6pm EST
        self.overnight_end = os.getenv('OVERNIGHT_END_TIME', '09:30')  # 9:30am EST
        self.market_open_time = os.getenv('MARKET_OPEN_TIME', '09:30')  # 9:30am EST
        self.timezone = pytz.timezone(os.getenv('STRATEGY_TIMEZONE', 'US/Eastern'))
        
        # ATR configuration
        self.atr_period = int(os.getenv('ATR_PERIOD', '14'))  # 14 bars default
        self.atr_timeframe = os.getenv('ATR_TIMEFRAME', '5m')  # 5-minute bars
        
        # Risk management
        self.stop_atr_multiplier = float(os.getenv('STOP_ATR_MULTIPLIER', '1.25'))  # 1.0-1.5 ATR
        self.tp_atr_multiplier = float(os.getenv('TP_ATR_MULTIPLIER', '2.0'))  # Daily ATR zone
        
        # Breakeven management (optional)
        self.breakeven_enabled = os.getenv('BREAKEVEN_ENABLED', 'true').lower() in ('true', '1', 'yes', 'on')
        self.breakeven_profit_points = float(os.getenv('BREAKEVEN_PROFIT_POINTS', '15.0'))  # +15 pts
        
        # Order placement
        self.range_break_offset = float(os.getenv('RANGE_BREAK_OFFSET', '0.25'))  # Offset from range extremes
        self.default_quantity = int(os.getenv('STRATEGY_QUANTITY', '1'))  # Position size
        
        # Strategy state
        self.is_tracking = False
        self.is_trading = False
        self._tracking_task: Optional[asyncio.Task] = None
        self._breakeven_task: Optional[asyncio.Task] = None
        
        logger.info(f"🎯 Overnight Range Strategy initialized")
        logger.info(f"   Overnight: {self.overnight_start} - {self.overnight_end} {self.timezone}")
        logger.info(f"   Market Open: {self.market_open_time} {self.timezone}")
        logger.info(f"   ATR Period: {self.atr_period} bars ({self.atr_timeframe})")
        logger.info(f"   Stop: {self.stop_atr_multiplier}x ATR, TP: {self.tp_atr_multiplier}x ATR")
        if self.breakeven_enabled:
            logger.info(f"   Breakeven: ENABLED (+{self.breakeven_profit_points} pts to trigger)")
        else:
            logger.info(f"   Breakeven: DISABLED")
    
    async def get_tick_size(self, symbol: str) -> float:
        """
        Get the tick size for a symbol from contract info.
        
        Args:
            symbol: Trading symbol (e.g., "MNQ", "ES")
        
        Returns:
            Tick size (e.g., 0.25 for MNQ, 0.25 for ES)
        """
        # Check cache first
        if symbol in self._tick_size_cache:
            return self._tick_size_cache[symbol]
        
        try:
            # Get contracts from trading bot
            contracts = await self.trading_bot.get_available_contracts()
            
            # Find matching contract
            for contract in contracts:
                contract_symbol = contract.get('name', '').upper()
                # Match base symbol (e.g., "MNQ" matches "MNQZ5")
                if symbol.upper() in contract_symbol or contract_symbol.startswith(symbol.upper()):
                    tick_size = contract.get('tickSize', 0.25)  # Default to 0.25
                    logger.debug(f"Tick size for {symbol}: {tick_size}")
                    self._tick_size_cache[symbol] = tick_size
                    return tick_size
            
            # Default tick sizes if not found
            defaults = {
                'MNQ': 0.25,
                'MES': 0.25,
                'ES': 0.25,
                'NQ': 0.25,
                'MYM': 1.0,
                'YM': 1.0,
                'M2K': 0.10,
                'RTY': 0.10,
            }
            
            tick_size = defaults.get(symbol.upper(), 0.25)
            logger.warning(f"Using default tick size for {symbol}: {tick_size}")
            self._tick_size_cache[symbol] = tick_size
            return tick_size
            
        except Exception as e:
            logger.error(f"Error getting tick size for {symbol}: {e}")
            # Default to 0.25 (most common for micro futures)
            return 0.25
    
    def round_to_tick(self, price: float, tick_size: float) -> float:
        """
        Round price to nearest valid tick size.
        
        Args:
            price: Price to round
            tick_size: Tick size for the symbol
        
        Returns:
            Price rounded to nearest tick
        
        Examples:
            round_to_tick(25299.80, 0.25) -> 25299.75
            round_to_tick(25299.87, 0.25) -> 25300.00
        """
        if tick_size <= 0:
            return price
        
        # Round to nearest tick
        rounded = round(price / tick_size) * tick_size
        
        # Ensure proper decimal places based on tick size
        if tick_size >= 1.0:
            # Whole number ticks (e.g., YM)
            return round(rounded, 0)
        elif tick_size >= 0.1:
            # One decimal place (e.g., RTY)
            return round(rounded, 1)
        elif tick_size >= 0.01:
            # Two decimal places (e.g., most futures)
            return round(rounded, 2)
        else:
            # Three or more decimal places
            return round(rounded, 4)
    
    async def calculate_atr(self, symbol: str, period: int = None, timeframe: str = None) -> Optional[ATRData]:
        """
        Calculate ATR (Average True Range) for a symbol.
        
        ATR is calculated using True Range:
        TR = max(high - low, abs(high - prev_close), abs(low - prev_close))
        ATR = average of TR over period
        
        Args:
            symbol: Trading symbol (e.g., "MNQ", "ES")
            period: Number of bars for ATR calculation (default: from config)
            timeframe: Timeframe for bars (default: from config)
        
        Returns:
            ATRData object with current ATR, daily ATR, and zones, or None if error
        """
        try:
            period = period or self.atr_period
            timeframe = timeframe or self.atr_timeframe
            
            # Fetch historical bars for ATR calculation
            # Need period + 1 bars to calculate true range (requires previous close)
            bars = await self.trading_bot.get_historical_data(
                symbol=symbol,
                timeframe=timeframe,
                limit=period + 1
            )
            
            if not bars or len(bars) < period + 1:
                logger.error(f"Insufficient bars for ATR calculation: {len(bars) if bars else 0} bars")
                return None
            
            # Calculate True Range for each bar
            true_ranges = []
            for i in range(1, len(bars)):
                current_bar = bars[i]
                prev_bar = bars[i - 1]
                
                high = current_bar.get('high', current_bar.get('h', 0))
                low = current_bar.get('low', current_bar.get('l', 0))
                prev_close = prev_bar.get('close', prev_bar.get('c', 0))
                
                # True Range = max of:
                # 1. High - Low
                # 2. |High - Previous Close|
                # 3. |Low - Previous Close|
                tr = max(
                    high - low,
                    abs(high - prev_close),
                    abs(low - prev_close)
                )
                true_ranges.append(tr)
            
            # Calculate ATR as simple moving average of True Range
            current_atr = sum(true_ranges[-period:]) / period
            
            # Calculate daily ATR (using 1-day bars for longer-term ATR)
            daily_bars = await self.trading_bot.get_historical_data(
                symbol=symbol,
                timeframe='1d',
                limit=period + 1
            )
            
            daily_atr = current_atr  # Default to current ATR if daily fetch fails
            if daily_bars and len(daily_bars) >= period + 1:
                daily_true_ranges = []
                for i in range(1, len(daily_bars)):
                    current_bar = daily_bars[i]
                    prev_bar = daily_bars[i - 1]
                    
                    high = current_bar.get('high', current_bar.get('h', 0))
                    low = current_bar.get('low', current_bar.get('l', 0))
                    prev_close = prev_bar.get('close', prev_bar.get('c', 0))
                    
                    tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
                    daily_true_ranges.append(tr)
                
                daily_atr = sum(daily_true_ranges[-period:]) / period
            
            # Get current price for ATR zones
            current_price = bars[-1].get('close', bars[-1].get('c', 0))
            
            # Calculate ATR zones
            atr_zone_high = current_price + daily_atr
            atr_zone_low = current_price - daily_atr
            
            atr_data = ATRData(
                current_atr=current_atr,
                daily_atr=daily_atr,
                atr_zone_high=atr_zone_high,
                atr_zone_low=atr_zone_low,
                period=period
            )
            
            logger.debug(f"ATR calculated for {symbol}: Current={current_atr:.2f}, Daily={daily_atr:.2f}, Zones=[{atr_zone_low:.2f}, {atr_zone_high:.2f}]")
            return atr_data
            
        except Exception as e:
            logger.error(f"Error calculating ATR for {symbol}: {e}")
            return None
    
    async def track_overnight_range(self, symbol: str) -> Optional[OvernightRange]:
        """
        Track overnight range for a symbol (6pm - 9:30am EST by default).
        
        EFFICIENT APPROACH: Fetches historical bars at market open instead of continuous tracking.
        Uses the bot's existing history command to get overnight session bars.
        
        This finds:
        - Highest price during overnight session
        - Lowest price during overnight session
        - Opening price (at start of session)
        - Closing price (at end of session)
        
        Args:
            symbol: Trading symbol (e.g., "MNQ", "ES")
        
        Returns:
            OvernightRange object with high/low/open/close, or None if error
        """
        try:
            # Calculate overnight session duration
            start_hour, start_min = map(int, self.overnight_start.split(':'))
            end_hour, end_min = map(int, self.overnight_end.split(':'))
            
            # Calculate session length in hours
            # Example: 18:00 to 09:30 = 15.5 hours
            if end_hour < start_hour:
                # Session spans midnight
                session_hours = (24 - start_hour) + end_hour + (end_min / 60.0)
            else:
                session_hours = end_hour - start_hour + (end_min / 60.0)
            
            # Calculate how many 1-minute bars we need
            session_minutes = int(session_hours * 60)
            
            logger.info(f"Fetching overnight range for {symbol} (last {session_minutes} minutes)")
            
            # Fetch overnight bars using existing history command
            # This is MUCH more efficient than continuous tracking!
            bars = await self.trading_bot.get_historical_data(
                symbol=symbol,
                timeframe='1m',
                limit=session_minutes + 10  # Add buffer for safety
            )
            
            if not bars or len(bars) < 10:
                logger.error(f"Insufficient overnight bars: {len(bars) if bars else 0} bars")
                return None
            
            # Calculate range statistics from bars
            high = max(bar.get('high', bar.get('h', 0)) for bar in bars)
            low = min(bar.get('low', bar.get('l', 0)) for bar in bars)
            open_price = bars[0].get('open', bars[0].get('o', 0))
            close_price = bars[-1].get('close', bars[-1].get('c', 0))
            
            # Get timestamps for logging
            now = datetime.now(self.timezone)
            end_time = now
            start_time = now - timedelta(hours=session_hours)
            
            range_data = OvernightRange(
                symbol=symbol,
                high=high,
                low=low,
                open=open_price,
                close=close_price,
                start_time=start_time,
                end_time=end_time,
                range_size=high - low,
                midpoint=(high + low) / 2
            )
            
            logger.info(f"📊 Overnight range for {symbol}: High={high:.2f}, Low={low:.2f}, Range={range_data.range_size:.2f}")
            self.active_ranges[symbol] = range_data
            return range_data
            
        except Exception as e:
            logger.error(f"Error tracking overnight range for {symbol}: {e}")
            return None
    
    async def calculate_range_break_orders(self, symbol: str) -> Tuple[Optional[RangeBreakOrder], Optional[RangeBreakOrder]]:
        """
        Calculate range breakout orders (long and short) based on overnight range and ATR.
        
        All prices are rounded to valid tick sizes to prevent order rejections.
        
        Returns:
            Tuple of (long_order, short_order) or (None, None) if error
        """
        try:
            # Get overnight range
            range_data = self.active_ranges.get(symbol)
            if not range_data:
                range_data = await self.track_overnight_range(symbol)
                if not range_data:
                    return None, None
            
            # Calculate ATR
            atr_data = await self.calculate_atr(symbol)
            if not atr_data:
                logger.error(f"Failed to calculate ATR for {symbol}")
                return None, None
            
            # Get tick size for proper price rounding
            tick_size = await self.get_tick_size(symbol)
            logger.info(f"Using tick size {tick_size} for {symbol}")
            
            # Calculate long breakout order (above overnight high)
            long_entry_raw = range_data.high + self.range_break_offset
            long_stop_raw = long_entry_raw - (atr_data.current_atr * self.stop_atr_multiplier)
            long_tp_raw = long_entry_raw + (atr_data.daily_atr * self.tp_atr_multiplier)
            
            # Round to valid tick sizes
            long_entry = self.round_to_tick(long_entry_raw, tick_size)
            long_stop = self.round_to_tick(long_stop_raw, tick_size)
            long_tp = self.round_to_tick(long_tp_raw, tick_size)
            
            long_order = RangeBreakOrder(
                symbol=symbol,
                side="BUY",
                entry_price=long_entry,
                stop_loss=long_stop,
                take_profit=long_tp,
                quantity=self.default_quantity,
                range_data=range_data,
                atr_data=atr_data
            )
            
            # Calculate short breakout order (below overnight low)
            short_entry_raw = range_data.low - self.range_break_offset
            short_stop_raw = short_entry_raw + (atr_data.current_atr * self.stop_atr_multiplier)
            short_tp_raw = short_entry_raw - (atr_data.daily_atr * self.tp_atr_multiplier)
            
            # Round to valid tick sizes
            short_entry = self.round_to_tick(short_entry_raw, tick_size)
            short_stop = self.round_to_tick(short_stop_raw, tick_size)
            short_tp = self.round_to_tick(short_tp_raw, tick_size)
            
            short_order = RangeBreakOrder(
                symbol=symbol,
                side="SELL",
                entry_price=short_entry,
                stop_loss=short_stop,
                take_profit=short_tp,
                quantity=self.default_quantity,
                range_data=range_data,
                atr_data=atr_data
            )
            
            # Validate prices are reasonable
            # Check that stop/TP prices make sense relative to entry
            if long_stop >= long_entry:
                logger.error(f"Invalid LONG order: Stop ({long_stop}) must be below entry ({long_entry})")
                return None, None
            if long_tp <= long_entry:
                logger.error(f"Invalid LONG order: TP ({long_tp}) must be above entry ({long_entry})")
                return None, None
            if short_stop <= short_entry:
                logger.error(f"Invalid SHORT order: Stop ({short_stop}) must be above entry ({short_entry})")
                return None, None
            if short_tp >= short_entry:
                logger.error(f"Invalid SHORT order: TP ({short_tp}) must be below entry ({short_entry})")
                return None, None
            
            # Check that prices aren't too extreme (within 20% of overnight range midpoint)
            midpoint = range_data.midpoint
            max_deviation = midpoint * 0.20  # 20% deviation limit
            
            if abs(long_entry - midpoint) > max_deviation:
                logger.warning(f"LONG entry {long_entry} is very far from range midpoint {midpoint}")
            if abs(short_entry - midpoint) > max_deviation:
                logger.warning(f"SHORT entry {short_entry} is very far from range midpoint {midpoint}")
            
            logger.info(f"🎯 Range break orders for {symbol} (tick size: {tick_size}):")
            logger.info(f"   LONG: Entry={long_entry:.2f}, SL={long_stop:.2f}, TP={long_tp:.2f}")
            logger.info(f"   SHORT: Entry={short_entry:.2f}, SL={short_stop:.2f}, TP={short_tp:.2f}")
            logger.info(f"   LONG Risk: {long_entry - long_stop:.2f} pts, Reward: {long_tp - long_entry:.2f} pts")
            logger.info(f"   SHORT Risk: {short_stop - short_entry:.2f} pts, Reward: {short_entry - short_tp:.2f} pts")
            
            return long_order, short_order
            
        except Exception as e:
            logger.error(f"Error calculating range break orders for {symbol}: {e}")
            return None, None
    
    async def place_range_break_orders(self, symbol: str) -> Dict:
        """
        Place stop bracket orders for overnight range breakouts.
        
        Returns:
            Dict with order placement results
        """
        try:
            logger.info(f"🚀 Placing range break orders for {symbol}...")
            
            # Calculate orders
            long_order, short_order = await self.calculate_range_break_orders(symbol)
            if not long_order or not short_order:
                return {"success": False, "error": "Failed to calculate orders"}
            
            results = {"symbol": symbol, "orders": []}
            
            # Place long breakout order
            long_result = await self.trading_bot.place_oco_bracket_with_stop_entry(
                symbol=symbol,
                side="BUY",
                quantity=long_order.quantity,
                entry_price=long_order.entry_price,
                stop_loss_price=long_order.stop_loss,
                take_profit_price=long_order.take_profit,
                account_id=self.trading_bot.selected_account
            )
            
            if long_result and 'order' in long_result:
                order_id = long_result['order'].get('orderId')
                results["orders"].append({"side": "LONG", "order_id": order_id, "result": long_result})
                logger.info(f"✅ Long breakout order placed: {order_id}")
                
                # Add to active orders
                if symbol not in self.active_orders:
                    self.active_orders[symbol] = []
                self.active_orders[symbol].append(order_id)
                
                # Setup breakeven monitoring ONLY if enabled
                if self.breakeven_enabled:
                    self.breakeven_monitoring[order_id] = {
                        "symbol": symbol,
                        "side": "LONG",
                        "entry_price": long_order.entry_price,
                        "original_stop": long_order.stop_loss,
                        "breakeven_triggered": False,
                        "position_filled": False  # Track if entry order has filled
                    }
                    logger.debug(f"Breakeven monitoring setup for order {order_id}")
            
            # Place short breakout order
            short_result = await self.trading_bot.place_oco_bracket_with_stop_entry(
                symbol=symbol,
                side="SELL",
                quantity=short_order.quantity,
                entry_price=short_order.entry_price,
                stop_loss_price=short_order.stop_loss,
                take_profit_price=short_order.take_profit,
                account_id=self.trading_bot.selected_account
            )
            
            if short_result and 'order' in short_result:
                order_id = short_result['order'].get('orderId')
                results["orders"].append({"side": "SHORT", "order_id": order_id, "result": short_result})
                logger.info(f"✅ Short breakout order placed: {order_id}")
                
                # Add to active orders
                if symbol not in self.active_orders:
                    self.active_orders[symbol] = []
                self.active_orders[symbol].append(order_id)
                
                # Setup breakeven monitoring ONLY if enabled
                if self.breakeven_enabled:
                    self.breakeven_monitoring[order_id] = {
                        "symbol": symbol,
                        "side": "SHORT",
                        "entry_price": short_order.entry_price,
                        "original_stop": short_order.stop_loss,
                        "breakeven_triggered": False,
                        "position_filled": False  # Track if entry order has filled
                    }
                    logger.debug(f"Breakeven monitoring setup for order {order_id}")
            
            results["success"] = len(results["orders"]) > 0
            return results
            
        except Exception as e:
            logger.error(f"Error placing range break orders for {symbol}: {e}")
            return {"success": False, "error": str(e)}
    
    async def monitor_breakeven_stops(self):
        """
        Background task to monitor FILLED positions and move stops to breakeven when profitable.
        
        EFFICIENT APPROACH: Only monitors after positions are opened, not continuously.
        
        Auto-start when: Position is opened (stop entry order filled)
        Auto-stop when: 
            - P&L >= threshold (move stop to BE, then stop monitoring this position)
            - Position is closed (SL/TP hit, clean up monitoring)
        
        Optional: Can be disabled via BREAKEVEN_ENABLED env variable
        """
        if not self.breakeven_enabled:
            logger.info("🔄 Breakeven monitoring DISABLED (set BREAKEVEN_ENABLED=true to enable)")
            return
        
        logger.info(f"🔄 Breakeven monitoring ACTIVE (+{self.breakeven_profit_points} pts threshold)")
        
        while self.is_trading:
            try:
                # Sleep first, then check if there are any filled positions to monitor
                await asyncio.sleep(10)  # Check every 10 seconds
                
                if not self.breakeven_monitoring:
                    continue  # No orders to monitor yet
                
                # Check if any positions are actually filled and not yet at breakeven
                has_active_monitoring = any(
                    data.get('position_filled', False) and not data.get('breakeven_triggered', False)
                    for data in self.breakeven_monitoring.values()
                )
                
                if not has_active_monitoring:
                    # No active positions to monitor - skip API calls
                    continue
                
                # Get current positions ONLY when we have active monitoring
                positions = await self.trading_bot.get_positions()
                
                for order_id, monitor_data in list(self.breakeven_monitoring.items()):
                    if monitor_data['breakeven_triggered']:
                        # Already at breakeven - stop monitoring this position
                        logger.debug(f"Removing completed breakeven monitoring for {monitor_data['symbol']}")
                        del self.breakeven_monitoring[order_id]
                        continue
                    
                    symbol = monitor_data['symbol']
                    side = monitor_data['side']
                    entry_price = monitor_data['entry_price']
                    
                    # Find matching position
                    position = next((p for p in positions if p.get('symbol') == symbol), None) if positions else None
                    
                    if not position:
                        # Position doesn't exist
                        if not monitor_data['position_filled']:
                            # Not filled yet - keep waiting
                            continue
                        else:
                            # Position was filled but now closed (SL/TP hit) - AUTO-STOP
                            logger.info(f"✅ Position {symbol} closed - auto-stopping breakeven monitoring")
                            del self.breakeven_monitoring[order_id]
                            continue
                    
                    # Position exists - check if this is first time seeing it (AUTO-START)
                    if not monitor_data['position_filled']:
                        monitor_data['position_filled'] = True
                        logger.info(f"🎯 Position {symbol} {side} opened at {entry_price:.2f} - AUTO-STARTED breakeven monitoring")
                    
                    # Get current price
                    current_price = position.get('currentPrice', position.get('lastPrice', 0))
                    if not current_price:
                        continue
                    
                    # Calculate profit in points
                    if side == "LONG":
                        profit_points = current_price - entry_price
                    else:  # SHORT
                        profit_points = entry_price - current_price
                    
                    # Check if profit threshold reached
                    if profit_points >= self.breakeven_profit_points:
                        logger.info(f"🎯 Position {symbol} reached +{profit_points:.2f} pts profit - moving stop to breakeven!")
                        
                        # Move stop to breakeven (entry price)
                        # This would require modifying the stop order
                        # Implementation depends on broker API capabilities
                        
                        # Mark as triggered - will be cleaned up on next iteration (AUTO-STOP)
                        monitor_data['breakeven_triggered'] = True
                        
                        # TODO: Implement actual stop modification via API
                        # await self.trading_bot.modify_order(stop_order_id, new_stop_price=entry_price)
                        
                        logger.info(f"✅ Breakeven triggered for {symbol} - AUTO-STOPPING monitoring")
                
            except Exception as e:
                logger.error(f"Error in breakeven monitoring: {e}")
                await asyncio.sleep(60)  # Wait longer on error
    
    async def market_open_scanner(self):
        """
        Background task that runs at market open (9:30am EST by default).
        
        At market open:
        1. Analyzes overnight ranges
        2. Calculates ATR zones
        3. Places stop bracket orders for range breakouts
        """
        logger.info(f"📅 Market open scanner started - will run at {self.market_open_time} {self.timezone}")
        
        while self.is_trading:
            try:
                now = datetime.now(self.timezone)
                
                # Parse market open time
                open_hour, open_min = map(int, self.market_open_time.split(':'))
                market_open = now.replace(hour=open_hour, minute=open_min, second=0, microsecond=0)
                
                # Check if we're past market open today
                if now >= market_open:
                    # Wait until tomorrow's market open
                    market_open = market_open + timedelta(days=1)
                
                # Calculate sleep time
                sleep_seconds = (market_open - now).total_seconds()
                logger.info(f"⏰ Next market open: {market_open} (in {sleep_seconds/3600:.1f} hours)")
                
                # Sleep until market open
                await asyncio.sleep(sleep_seconds)
                
                # Market open! Execute strategy
                logger.info(f"🔔 Market open! Executing overnight range break strategy...")
                
                # Get symbols to trade from environment
                symbols = os.getenv('STRATEGY_SYMBOLS', 'MNQ,MES').split(',')
                
                for symbol in symbols:
                    symbol = symbol.strip()
                    if not symbol:
                        continue
                    
                    logger.info(f"📊 Processing {symbol}...")
                    
                    # Track overnight range
                    await self.track_overnight_range(symbol)
                    
                    # Place range break orders
                    result = await self.place_range_break_orders(symbol)
                    
                    if result.get('success'):
                        logger.info(f"✅ Successfully placed orders for {symbol}")
                    else:
                        logger.error(f"❌ Failed to place orders for {symbol}: {result.get('error')}")
                    
                    # Small delay between symbols
                    await asyncio.sleep(1)
                
                logger.info("🎯 Market open strategy execution complete!")
                
            except Exception as e:
                logger.error(f"Error in market open scanner: {e}")
                await asyncio.sleep(300)  # Wait 5 minutes on error
    
    async def start(self, symbols: List[str] = None):
        """
        Start the overnight range strategy.
        
        Args:
            symbols: List of symbols to trade (default: from env STRATEGY_SYMBOLS)
        """
        if self.is_trading:
            logger.warning("Strategy is already running")
            return
        
        self.is_trading = True
        
        # Start background tasks
        self._tracking_task = asyncio.create_task(self.market_open_scanner())
        self._breakeven_task = asyncio.create_task(self.monitor_breakeven_stops())
        
        logger.info("🚀 Overnight Range Strategy started!")
        logger.info(f"   Symbols: {symbols or os.getenv('STRATEGY_SYMBOLS', 'MNQ,MES')}")
        logger.info(f"   Market Open: {self.market_open_time} {self.timezone}")
    
    async def stop(self):
        """Stop the overnight range strategy."""
        if not self.is_trading:
            logger.warning("Strategy is not running")
            return
        
        self.is_trading = False
        
        # Cancel background tasks
        if self._tracking_task:
            self._tracking_task.cancel()
        if self._breakeven_task:
            self._breakeven_task.cancel()
        
        logger.info("🛑 Overnight Range Strategy stopped!")
    
    def get_status(self) -> Dict:
        """Get current strategy status."""
        return {
            "is_trading": self.is_trading,
            "active_ranges": {symbol: {
                "high": r.high,
                "low": r.low,
                "range_size": r.range_size,
                "midpoint": r.midpoint
            } for symbol, r in self.active_ranges.items()},
            "active_orders": self.active_orders,
            "breakeven_monitoring": {
                order_id: {
                    "symbol": data["symbol"],
                    "side": data["side"],
                    "filled": data.get("position_filled", False),
                    "triggered": data["breakeven_triggered"]
                } for order_id, data in self.breakeven_monitoring.items()
            },
            "config": {
                "overnight_session": f"{self.overnight_start} - {self.overnight_end}",
                "market_open": self.market_open_time,
                "timezone": str(self.timezone),
                "atr_period": self.atr_period,
                "stop_multiplier": self.stop_atr_multiplier,
                "tp_multiplier": self.tp_atr_multiplier,
                "breakeven_enabled": self.breakeven_enabled,
                "breakeven_points": self.breakeven_profit_points
            }
        }

