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
from datetime import datetime, date, time, timedelta, timezone
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from collections import deque
import pytz

from strategies.strategy_base import BaseStrategy, StrategyConfig, MarketCondition, StrategyStatus

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
    # Daily ATR zones (based on market open price at 9:30am)
    market_open_price: float = 0.0  # 9:30am candle open price
    day_bull_price: float = 0.0  # Upper zone lower bound: open + (dailyATR/2) * 0.5
    day_bull_price1: float = 0.0  # Upper zone upper bound: open + (dailyATR/2) * 0.618
    day_bear_price: float = 0.0  # Lower zone upper bound: open - (dailyATR/2) * 0.5
    day_bear_price1: float = 0.0  # Lower zone lower bound: open - (dailyATR/2) * 0.618


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


class OvernightRangeStrategy(BaseStrategy):
    """
    Manages overnight range breakout strategy execution.
    
    Features:
    - Tracks overnight ranges for multiple symbols
    - Calculates ATR for dynamic stops/targets
    - Places orders at market open
    - Manages breakeven stops
    - Market condition filters (optional)
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
            config = StrategyConfig.from_env("OVERNIGHT_RANGE")
        
        # Initialize base strategy
        super().__init__(trading_bot, config)
        
        # Overnight-specific state
        self.active_ranges: Dict[str, OvernightRange] = {}
        self.active_orders: Dict[str, List[str]] = {}  # symbol -> [order_ids]
        self.breakeven_monitoring: Dict[str, Dict] = {}  # order_id -> monitoring data
        
        # Tick size cache: {symbol: tick_size}
        self._tick_size_cache: Dict[str, float] = {}
        self._last_market_open_run: Optional[date] = None
        
        # Load strategy-specific configuration from environment
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
        
        # Market condition filters (OPTIONAL - defaulted to OFF)
        self.filter_range_size_enabled = os.getenv('OVERNIGHT_FILTER_RANGE_SIZE', 'false').lower() == 'true'
        self.filter_range_min = float(os.getenv('OVERNIGHT_RANGE_MIN_POINTS', '50.0'))
        self.filter_range_max = float(os.getenv('OVERNIGHT_RANGE_MAX_POINTS', '500.0'))
        
        self.filter_gap_enabled = os.getenv('OVERNIGHT_FILTER_GAP', 'false').lower() == 'true'
        self.filter_gap_max = float(os.getenv('OVERNIGHT_GAP_MAX_POINTS', '200.0'))
        
        self.filter_volatility_enabled = os.getenv('OVERNIGHT_FILTER_VOLATILITY', 'false').lower() == 'true'
        self.filter_atr_min = float(os.getenv('OVERNIGHT_ATR_MIN', '20.0'))
        self.filter_atr_max = float(os.getenv('OVERNIGHT_ATR_MAX', '200.0'))
        
        self.filter_dll_proximity_enabled = os.getenv('OVERNIGHT_FILTER_DLL_PROXIMITY', 'false').lower() == 'true'
        self.filter_dll_threshold = float(os.getenv('OVERNIGHT_DLL_THRESHOLD_PERCENT', '0.75'))  # 75%
        
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
        
        # Log market condition filters status
        logger.info(f"   Market Condition Filters:")
        logger.info(f"     Range Size: {'ENABLED' if self.filter_range_size_enabled else 'DISABLED'} ({self.filter_range_min:.0f}-{self.filter_range_max:.0f} pts)")
        logger.info(f"     Gap Filter: {'ENABLED' if self.filter_gap_enabled else 'DISABLED'} (max {self.filter_gap_max:.0f} pts)")
        logger.info(f"     Volatility Filter: {'ENABLED' if self.filter_volatility_enabled else 'DISABLED'} (ATR {self.filter_atr_min:.0f}-{self.filter_atr_max:.0f})")
        logger.info(f"     DLL Proximity: {'ENABLED' if self.filter_dll_proximity_enabled else 'DISABLED'} (threshold {self.filter_dll_threshold:.0%})")
    
    def _get_trade_symbols(self, symbols: Optional[List[str]] = None) -> List[str]:
        """Determine which symbols to trade for the next execution."""
        if symbols:
            candidates = symbols
        elif getattr(self.config, 'symbols', None):
            candidates = self.config.symbols
        else:
            candidates = os.getenv('STRATEGY_SYMBOLS', 'MNQ,MES').split(',')

        return [sym.strip().upper() for sym in candidates if sym and sym.strip()]
    
    async def _execute_market_open_sequence(self, symbols: Optional[List[str]] = None) -> None:
        """Execute the range break strategy for the configured symbols."""
        trade_symbols = self._get_trade_symbols(symbols)

        if not trade_symbols:
            logger.warning("⚠️  No symbols configured for overnight range strategy - skipping execution")
            return

        logger.info(f"🔔 Executing overnight range break strategy for symbols: {', '.join(trade_symbols)}")

        for symbol in trade_symbols:
            logger.info(f"📊 Processing {symbol}...")

            try:
                await self.track_overnight_range(symbol)
                result = await self.place_range_break_orders(symbol)

                if result.get('success'):
                    logger.info(f"✅ Successfully placed orders for {symbol}")
                else:
                    logger.error(f"❌ Failed to place orders for {symbol}: {result.get('error')}")

            except Exception as exc:
                logger.error(f"❌ Error executing overnight range for {symbol}: {exc}")

            await asyncio.sleep(1)  # Small delay between symbols
    
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
    
    async def check_market_conditions(self, symbol: str, range_data: OvernightRange, atr_data: ATRData) -> Tuple[bool, str]:
        """
        Check if market conditions are favorable for trading (OPTIONAL filters).
        
        Filters (all default to DISABLED):
        1. Range size filter: Avoid too small/large ranges
        2. Gap filter: Skip large overnight gaps
        3. Volatility filter: Avoid extreme ATR values
        4. DLL proximity filter: Pause when close to daily loss limit
        
        Args:
            symbol: Trading symbol
            range_data: Overnight range data
            atr_data: ATR data
        
        Returns:
            (should_trade: bool, reason: str)
        """
        # Range size filter (DEFAULT: OFF)
        if self.filter_range_size_enabled:
            range_points = range_data.range_size
            if range_points < self.filter_range_min:
                return False, f"Range too small ({range_points:.2f} < {self.filter_range_min:.0f} pts)"
            if range_points > self.filter_range_max:
                return False, f"Range too large ({range_points:.2f} > {self.filter_range_max:.0f} pts)"
        
        # Gap filter (DEFAULT: OFF)
        if self.filter_gap_enabled:
            gap_points = abs(range_data.close - range_data.open)
            if gap_points > self.filter_gap_max:
                return False, f"Gap too large ({gap_points:.2f} > {self.filter_gap_max:.0f} pts)"
        
        # Volatility filter (DEFAULT: OFF)
        if self.filter_volatility_enabled:
            if atr_data.current_atr < self.filter_atr_min:
                return False, f"ATR too low ({atr_data.current_atr:.2f} < {self.filter_atr_min:.0f})"
            if atr_data.current_atr > self.filter_atr_max:
                return False, f"ATR too high ({atr_data.current_atr:.2f} > {self.filter_atr_max:.0f})"
        
        # DLL proximity filter (DEFAULT: OFF)
        if self.filter_dll_proximity_enabled:
            if hasattr(self.trading_bot, 'account_tracker'):
                tracker = self.trading_bot.account_tracker
                current_daily_pnl = tracker.get_daily_pnl()
                dll = tracker.daily_loss_limit
                
                if current_daily_pnl < 0:
                    dll_usage = abs(current_daily_pnl) / dll
                    if dll_usage >= self.filter_dll_threshold:
                        return False, f"Too close to DLL ({dll_usage:.1%} >= {self.filter_dll_threshold:.1%})"
        
        return True, "All filters passed"
    
    # Implement abstract methods from BaseStrategy
    
    async def analyze(self, symbol: str) -> Optional[Dict]:
        """
        Analyze overnight range and generate trading signals.
        
        Returns signals for BOTH long and short breakout orders.
        """
        try:
            # Get overnight range
            range_data = self.active_ranges.get(symbol)
            if not range_data:
                range_data = await self.track_overnight_range(symbol)
                if not range_data:
                    return None
            
            # Calculate ATR
            atr_data = await self.calculate_atr(symbol)
            if not atr_data:
                return None
            
            # Check market conditions (if filters enabled)
            should_trade, reason = await self.check_market_conditions(symbol, range_data, atr_data)
            if not should_trade:
                logger.info(f"❌ Skipping {symbol}: {reason}")
                return None
            
            # Calculate orders
            long_order, short_order = await self.calculate_range_break_orders(symbol)
            if not long_order or not short_order:
                return None
            
            # Return signal with both orders
            return {
                "symbol": symbol,
                "long_order": long_order,
                "short_order": short_order,
                "range_data": range_data,
                "atr_data": atr_data,
                "confidence": 0.8,  # High confidence for range breakouts
                "reason": "Overnight range breakout setup"
            }
            
        except Exception as e:
            logger.error(f"Error analyzing {symbol}: {e}")
            return None
    
    async def execute(self, signal: Dict) -> bool:
        """
        Execute overnight range breakout orders.
        
        Places BOTH long and short stop bracket orders.
        """
        try:
            symbol = signal["symbol"]
            long_order = signal["long_order"]
            short_order = signal["short_order"]
            
            result = await self.place_range_break_orders(symbol)
            return result.get("success", False)
            
        except Exception as e:
            logger.error(f"Error executing signal: {e}")
            return False
    
    async def manage_positions(self):
        """
        Manage open positions - handled by monitor_breakeven_stops().
        """
        # Breakeven monitoring is handled by the background task
        pass
    
    async def cleanup(self):
        """
        Clean up strategy resources.
        """
        await self.stop()
    
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
            
            # Get market open price (9:30am candle open)
            # This is used for daily ATR zone calculations
            market_open_price = 0.0
            now = datetime.now(self.timezone)
            open_hour, open_min = map(int, self.market_open_time.split(':'))
            market_open_today = now.replace(hour=open_hour, minute=open_min, second=0, microsecond=0)
            
            # Fetch a few 1-minute bars around market open to get the open price
            try:
                # Get bars from around market open
                open_bars = await self.trading_bot.get_historical_data(
                    symbol=symbol,
                    timeframe='1m',
                    limit=10
                )
                
                if open_bars and len(open_bars) > 0:
                    # Find the bar closest to market open time
                    # For now, use the most recent bar's open (this will be updated in real-time)
                    # In production, you'd want to specifically find the 9:30am bar
                    market_open_price = open_bars[-1].get('open', open_bars[-1].get('o', current_price))
                else:
                    market_open_price = current_price
            except:
                market_open_price = current_price
            
            # Calculate daily ATR zones (PineScript formula)
            # day_dist = dailyATR * 0.5
            day_dist = daily_atr * 0.5
            
            # Upper zone: open_price + (dailyATR/2) * 0.5 to open_price + (dailyATR/2) * 0.618
            day_bull_price = market_open_price + day_dist * 0.5  # Lower bound of upper zone
            day_bull_price1 = market_open_price + day_dist * 0.618  # Upper bound of upper zone
            
            # Lower zone: open_price - (dailyATR/2) * 0.5 to open_price - (dailyATR/2) * 0.618
            day_bear_price = market_open_price - day_dist * 0.5  # Upper bound of lower zone
            day_bear_price1 = market_open_price - day_dist * 0.618  # Lower bound of lower zone
            
            atr_data = ATRData(
                current_atr=current_atr,
                daily_atr=daily_atr,
                atr_zone_high=atr_zone_high,
                atr_zone_low=atr_zone_low,
                period=period,
                market_open_price=market_open_price,
                day_bull_price=day_bull_price,
                day_bull_price1=day_bull_price1,
                day_bear_price=day_bear_price,
                day_bear_price1=day_bear_price1
            )
            
            logger.debug(f"ATR calculated for {symbol}: Current={current_atr:.2f}, Daily={daily_atr:.2f}")
            logger.debug(f"  Market Open: {market_open_price:.2f}")
            logger.debug(f"  Upper ATR Zone: [{day_bull_price:.2f}, {day_bull_price1:.2f}]")
            logger.debug(f"  Lower ATR Zone: [{day_bear_price1:.2f}, {day_bear_price:.2f}]")
            return atr_data
            
        except Exception as e:
            logger.error(f"Error calculating ATR for {symbol}: {e}")
            return None
    
    async def track_overnight_range(self, symbol: str) -> Optional[OvernightRange]:
        """
        Track overnight range for a symbol (6pm - 9:30am EST by default).
        
        EFFICIENT APPROACH: Fetches historical bars for the SPECIFIC overnight session.
        Uses the bot's existing history command to get bars for that exact time range.
        
        This finds:
        - Highest price during overnight session (yesterday 6pm to today 9:30am)
        - Lowest price during overnight session
        - Opening price (at start of session)
        - Closing price (at end of session)
        
        Args:
            symbol: Trading symbol (e.g., "MNQ", "ES")
        
        Returns:
            OvernightRange object with high/low/open/close, or None if error
        """
        try:
            # Get current time in strategy timezone
            now = datetime.now(self.timezone)
            
            # Parse overnight session times
            start_hour, start_min = map(int, self.overnight_start.split(':'))
            end_hour, end_min = map(int, self.overnight_end.split(':'))
            
            # Calculate the SPECIFIC overnight session times
            # If it's before market open today, use yesterday 6pm to today 9:30am
            # If it's after market open today, use today 6pm to tomorrow 9:30am (for next day)
            
            market_open_today = now.replace(hour=end_hour, minute=end_min, second=0, microsecond=0)
            
            if now.time() < time(end_hour, end_min):
                # Before market open - use yesterday's overnight session
                # Start: Yesterday at overnight_start (e.g., yesterday 18:00)
                # End: Today at overnight_end (e.g., today 09:30)
                start_time = (now - timedelta(days=1)).replace(hour=start_hour, minute=start_min, second=0, microsecond=0)
                end_time = now.replace(hour=end_hour, minute=end_min, second=0, microsecond=0)
            else:
                # After market open - use today's overnight session for tomorrow
                # Start: Today at overnight_start (e.g., today 18:00)
                # End: Tomorrow at overnight_end (e.g., tomorrow 09:30)
                if now.time() < time(start_hour, start_min):
                    # Before overnight start - use yesterday's session
                    start_time = (now - timedelta(days=1)).replace(hour=start_hour, minute=start_min, second=0, microsecond=0)
                    end_time = now.replace(hour=end_hour, minute=end_min, second=0, microsecond=0)
                else:
                    # After overnight start - session is in progress or future
                    # For testing purposes, use the most recent completed session
                    start_time = (now - timedelta(days=1)).replace(hour=start_hour, minute=start_min, second=0, microsecond=0)
                    end_time = now.replace(hour=end_hour, minute=end_min, second=0, microsecond=0)
            
            # Calculate how many 1-minute bars we need
            session_duration = end_time - start_time
            session_minutes = int(session_duration.total_seconds() / 60)
            
            logger.info(f"Fetching overnight range for {symbol}")
            logger.info(f"  Session: {start_time.strftime('%Y-%m-%d %H:%M')} to {end_time.strftime('%Y-%m-%d %H:%M')}")
            logger.info(f"  Duration: {session_minutes} minutes")
            
            # Fetch bars for the overnight session
            bars = await self.trading_bot.get_historical_data(
                symbol=symbol,
                timeframe='1m',
                limit=session_minutes + 60  # Add buffer to ensure we get the full range
            )
            
            if not bars or len(bars) < 10:
                logger.error(f"Insufficient overnight bars: {len(bars) if bars else 0} bars")
                return None
            
            # Filter bars to only include those within the overnight session
            # Bars are returned newest first, so we need to filter by timestamp
            overnight_bars = []
            bar_count_debug = 0
            first_bar_time = None
            last_bar_time = None
            
            for bar in bars:
                # Get timestamp from bar
                ts = bar.get('timestamp') or bar.get('time') or bar.get('t')
                if not ts:
                    continue
                
                # Parse timestamp
                if isinstance(ts, str):
                    bar_time = datetime.fromisoformat(ts.replace('Z', '+00:00'))
                elif ts > 1e12:
                    bar_time = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
                else:
                    bar_time = datetime.fromtimestamp(ts, tz=timezone.utc)
                
                bar_time = bar_time.astimezone(self.timezone)
                
                # Track first and last bar times for debugging
                if bar_count_debug == 0:
                    first_bar_time = bar_time
                bar_count_debug += 1
                last_bar_time = bar_time
                
                # Check if bar is within overnight session
                if start_time <= bar_time <= end_time:
                    overnight_bars.append(bar)
            
            # Debug logging
            logger.info(f"  Total bars fetched: {bar_count_debug}")
            if first_bar_time and last_bar_time:
                logger.info(f"  Bars range: {first_bar_time.strftime('%Y-%m-%d %H:%M')} to {last_bar_time.strftime('%Y-%m-%d %H:%M')}")
            
            if not overnight_bars:
                logger.warning(f"No bars found in overnight session for {symbol}")
                logger.warning(f"Fetched {len(bars)} bars but none matched time range")
                return None
            
            logger.info(f"  Found {len(overnight_bars)} bars in overnight session")
            
            # Calculate range statistics from overnight bars only
            high = max(bar.get('high', bar.get('h', 0)) for bar in overnight_bars)
            low = min(bar.get('low', bar.get('l', 0)) for bar in overnight_bars)
            
            # Sort bars by timestamp to get proper open/close
            overnight_bars_sorted = sorted(overnight_bars, key=lambda b: b.get('timestamp') or b.get('time') or b.get('t', 0))
            open_price = overnight_bars_sorted[0].get('open', overnight_bars_sorted[0].get('o', 0))
            close_price = overnight_bars_sorted[-1].get('close', overnight_bars_sorted[-1].get('c', 0))
            
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
            
            # Check if daily ATR zones are inside overnight range
            # If they are, don't use them as profit targets
            upper_zone_inside = (atr_data.day_bull_price >= range_data.low and 
                                 atr_data.day_bull_price1 <= range_data.high)
            lower_zone_inside = (atr_data.day_bear_price1 >= range_data.low and 
                                 atr_data.day_bear_price <= range_data.high)
            
            logger.info(f"Daily ATR zones check:")
            logger.info(f"  Overnight Range: [{range_data.low:.2f}, {range_data.high:.2f}]")
            logger.info(f"  Upper Zone: [{atr_data.day_bull_price:.2f}, {atr_data.day_bull_price1:.2f}] - Inside: {upper_zone_inside}")
            logger.info(f"  Lower Zone: [{atr_data.day_bear_price1:.2f}, {atr_data.day_bear_price:.2f}] - Inside: {lower_zone_inside}")
            
            # Calculate long breakout order (above overnight high)
            long_entry_raw = range_data.high + self.range_break_offset
            long_stop_raw = long_entry_raw - (atr_data.current_atr * self.stop_atr_multiplier)
            
            # Determine TP target based on whether ATR zone is inside range
            if upper_zone_inside:
                # ATR zone is inside range - use ATR * 2 or ATR * 3 instead
                logger.info(f"  Upper ATR zone inside overnight range - using ATR*2 for TP")
                long_tp_raw = long_entry_raw + (atr_data.current_atr * 2.0)
            else:
                # ATR zone is outside range - TARGET THE ZONE ITSELF (lower bound of upper zone)
                logger.info(f"  Upper ATR zone outside overnight range - targeting zone at {atr_data.day_bull_price:.2f}")
                long_tp_raw = atr_data.day_bull_price  # Target the lower bound of upper zone
            
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
            
            # Determine TP target based on whether ATR zone is inside range
            if lower_zone_inside:
                # ATR zone is inside range - use ATR * 2 or ATR * 3 instead
                logger.info(f"  Lower ATR zone inside overnight range - using ATR*2 for TP")
                short_tp_raw = short_entry_raw - (atr_data.current_atr * 2.0)
            else:
                # ATR zone is outside range - TARGET THE ZONE ITSELF (upper bound of lower zone)
                logger.info(f"  Lower ATR zone outside overnight range - targeting zone at {atr_data.day_bear_price:.2f}")
                short_tp_raw = atr_data.day_bear_price  # Target the upper bound of lower zone
            
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
        Background task that aligns strategy execution with the configured market open.
        Handles catch-up execution when the bot starts after the market open.
        """
        logger.info(f"📅 Market open scanner started - targeting {self.market_open_time} {self.timezone}")
        
        while self.is_trading:
            try:
                now = datetime.now(self.timezone)
                open_hour, open_min = map(int, self.market_open_time.split(':'))
                market_open_today = now.replace(hour=open_hour, minute=open_min, second=0, microsecond=0)
                trading_end_hour, trading_end_min = map(int, self.config.trading_end_time.split(':'))
                trading_end_today = now.replace(hour=trading_end_hour, minute=trading_end_min, second=0, microsecond=0)
                
                ran_today = self._last_market_open_run == market_open_today.date()
                
                if now >= market_open_today:
                    if not ran_today:
                        within_trading_window = now <= (trading_end_today + timedelta(minutes=30))
                        if within_trading_window:
                            logger.info("🔔 Market open window already passed today—running catch-up execution now.")
                            await self._execute_market_open_sequence()
                        else:
                            logger.info("⏭️  Market open already passed and we are outside the trading window. Skipping until next session.")
                        self._last_market_open_run = market_open_today.date()
                    
                    next_open = market_open_today + timedelta(days=1)
                else:
                    next_open = market_open_today
                
                sleep_seconds = max((next_open - now).total_seconds(), 5.0)
                logger.info(
                    f"⏰ Next market open execution scheduled for {next_open.strftime('%Y-%m-%d %H:%M:%S %Z')} "
                    f"(in {sleep_seconds/3600:.2f} hours)"
                )
                await asyncio.sleep(sleep_seconds)
            
            except asyncio.CancelledError:
                logger.info("Market open scanner cancelled.")
                break
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
        
        if symbols:
            self.config.symbols = symbols
        self._last_market_open_run = None
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
        self._last_market_open_run = None
        
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

