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
try:
    import pytz
except ImportError:
    pytz = None  # Optional dependency

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
        
        # ATR cache: {symbol: {timeframe: (atr_data, timestamp)}}
        # Cache ATR calculations for 5 minutes to avoid redundant API calls
        self._atr_cache: Dict[str, Dict[str, Tuple[ATRData, datetime]]] = {}
        self._atr_cache_ttl = timedelta(minutes=5)  # Cache ATR for 5 minutes
        
        # Load strategy-specific configuration from environment
        self.overnight_start = os.getenv('OVERNIGHT_START_TIME', '18:00')  # 6pm EST
        self.overnight_end = os.getenv('OVERNIGHT_END_TIME', '09:30')  # 9:30am EST
        self.market_open_time = os.getenv('MARKET_OPEN_TIME', '09:30')  # 9:30am EST
        if pytz:
            self.timezone = pytz.timezone(os.getenv('STRATEGY_TIMEZONE', 'US/Eastern'))
        else:
            # Fallback to UTC if pytz not available
            self.timezone = timezone.utc
            logger.warning("pytz not available, using UTC timezone. Install pytz for timezone support.")
        
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
        self.max_quantity_per_instrument = int(os.getenv('MAX_QUANTITY_PER_INSTRUMENT', '10'))  # Max contracts per symbol
        self.breakout_monitor_enabled = os.getenv('BREAKOUT_MONITOR_ENABLED', 'true').lower() in ('true', '1', 'yes', 'on')
        self.breakout_proximity_percent = float(os.getenv('BREAKOUT_PROXIMITY_PERCENT', '10.0'))
        self.breakout_min_proximity_points = float(os.getenv('BREAKOUT_MIN_PROXIMITY_POINTS', '5.0'))
        self.breakout_monitor_interval = float(os.getenv('BREAKOUT_MONITOR_INTERVAL_SECONDS', '15'))
        self.breakout_order_tolerance_points = float(os.getenv('BREAKOUT_ORDER_TOLERANCE_POINTS', '1.0'))
        self.breakout_levels: Dict[str, Dict[str, RangeBreakOrder]] = {}
        self.breakout_active_orders: Dict[str, Dict[str, str]] = {}
        self._breakout_monitor_task: Optional[asyncio.Task] = None
        
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
        self._plain_stop_monitor_task: Optional[asyncio.Task] = None
        
        logger.info(f"🎯 Overnight Range Strategy initialized")
        logger.info(f"   Overnight: {self.overnight_start} - {self.overnight_end} {self.timezone}")
        logger.info(f"   Market Open: {self.market_open_time} {self.timezone}")
        logger.info(f"   ATR Period: {self.atr_period} bars ({self.atr_timeframe})")
        logger.info(f"   Stop: {self.stop_atr_multiplier}x ATR, TP: {self.tp_atr_multiplier}x ATR")
        logger.info(f"   Max Quantity per Instrument: {self.max_quantity_per_instrument} contracts")
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
        """
        Execute the range break strategy for the configured symbols.
        
        In CONTINUOUS mode: Only recalculates ranges and breakout levels.
        The monitor_breakout_levels() task handles actual order placement when price approaches.
        """
        trade_symbols = self._get_trade_symbols(symbols)

        if not trade_symbols:
            logger.warning("⚠️  No symbols configured for overnight range strategy - skipping execution")
            return

        logger.info(f"🔔 Recalculating overnight ranges and breakout levels for: {', '.join(trade_symbols)}")

        for symbol in trade_symbols:
            logger.info(f"📊 Processing {symbol}...")

            try:
                # Track overnight range
                range_data = await self.track_overnight_range(symbol)
                if not range_data:
                    logger.warning(f"⚠️  Could not track overnight range for {symbol}")
                    continue
                
                # Calculate breakout orders
                long_order, short_order = await self.calculate_range_break_orders(symbol)
                if long_order and short_order:
                    # Update breakout levels for monitoring
                    self.breakout_levels[symbol] = {
                        "BUY": long_order,
                        "SELL": short_order,
                    }
                    self.breakout_active_orders.setdefault(symbol, {})
                    logger.info(f"✅ Updated breakout levels for {symbol}")
                    logger.info(f"   LONG: Entry={long_order.entry_price:.2f}, SL={long_order.stop_loss:.2f}, TP={long_order.take_profit:.2f}")
                    logger.info(f"   SHORT: Entry={short_order.entry_price:.2f}, SL={short_order.stop_loss:.2f}, TP={short_order.take_profit:.2f}")
                else:
                    logger.error(f"❌ Failed to calculate orders for {symbol}")

            except Exception as exc:
                logger.error(f"❌ Error processing {symbol}: {exc}")

            await asyncio.sleep(1)  # Small delay between symbols
        
        logger.info("✅ Range recalculation complete. Monitor will place orders when price approaches levels.")
    
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
    
    async def get_current_position_quantity(self, symbol: str) -> int:
        """
        Get the total current position quantity for a symbol (absolute value).
        
        Args:
            symbol: Trading symbol (e.g., "MES", "MNQ")
        
        Returns:
            Total absolute position quantity (0 if no position)
        """
        try:
            positions = await self.trading_bot.get_open_positions()
            if not positions:
                return 0
            
            total_quantity = 0
            symbol_upper = symbol.upper()
            
            for pos in positions:
                # Handle Position objects (with attributes) or dicts
                if hasattr(pos, 'symbol'):
                    pos_symbol = (getattr(pos, 'symbol', '') or '').upper()
                    net_qty = getattr(pos, 'net_quantity', 0) or getattr(pos, 'quantity', 0) or 0
                elif isinstance(pos, dict):
                    pos_symbol = pos.get('symbol', '').upper()
                    net_qty = pos.get('net_quantity', 0) or pos.get('quantity', 0) or 0
                else:
                    continue
                
                if pos_symbol == symbol_upper:
                    total_quantity += abs(net_qty)
            
            return total_quantity
        except Exception as e:
            logger.warning(f"⚠️  Error getting position quantity for {symbol}: {e}")
            return 0
    
    async def get_pending_entry_order_quantity(self, symbol: str, open_orders: Optional[List[Dict]] = None) -> int:
        """
        Get the total quantity of pending entry orders for a symbol.
        
        This counts only entry orders (stop orders that will open positions), 
        NOT bracket SL/TP orders which are reduce-only.
        
        Args:
            symbol: Trading symbol (e.g., "MES", "MNQ")
            open_orders: Optional list of open orders (if None, fetches from API)
        
        Returns:
            Total quantity of pending entry orders
        """
        try:
            symbol_upper = symbol.upper()
            
            if open_orders is None:
                orders = await self.trading_bot.get_open_orders()
                orders_list = orders if isinstance(orders, list) else []
            else:
                orders_list = open_orders
            
            total_quantity = 0
            
            for order in orders_list:
                # Get symbol from order
                order_symbol = order.get('symbol', '')
                if not order_symbol:
                    # Try to get from contractId
                    contract_id = order.get('contractId')
                    if contract_id and hasattr(self.trading_bot, '_get_symbol_from_contract_id'):
                        order_symbol = self.trading_bot._get_symbol_from_contract_id(contract_id)
                
                if not order_symbol or order_symbol.upper() != symbol_upper:
                    continue
                
                # Only count entry orders (stop orders that will open positions)
                # Exclude reduce-only orders (SL/TP brackets)
                is_reduce_only = order.get('reduceOnly') or order.get('reduce_only', False)
                
                # Check customTag for bracket identification
                custom_tag = order.get('customTag') or order.get('custom_tag') or ''
                is_bracket_sl_tp = '-SL' in str(custom_tag) or '-TP' in str(custom_tag) or 'AutoBracket' in str(custom_tag)
                
                # Entry orders are stop orders (type 4) that are:
                # 1. NOT reduce-only
                # 2. NOT bracket SL/TP orders (identified by customTag)
                order_type = order.get('type') or order.get('raw_type')
                is_entry_order = (
                    order_type in (4, "4", "Stop", "stop") and
                    not is_reduce_only and
                    not is_bracket_sl_tp
                )
                
                if is_entry_order:
                    qty = order.get('quantity') or order.get('size') or 0
                    if qty:
                        total_quantity += abs(int(qty))
            
            return total_quantity
        except Exception as e:
            logger.warning(f"⚠️  Error getting pending entry order quantity for {symbol}: {e}")
            return 0
    
    async def get_total_exposure(self, symbol: str, open_orders: Optional[List[Dict]] = None) -> int:
        """
        Get total exposure (positions + pending entry orders) for a symbol.
        
        Args:
            symbol: Trading symbol
            open_orders: Optional list of open orders (if None, fetches from API)
        
        Returns:
            Total exposure: positions + pending entry orders
        """
        position_qty = await self.get_current_position_quantity(symbol)
        pending_qty = await self.get_pending_entry_order_quantity(symbol, open_orders)
        return position_qty + pending_qty
    
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
        
        Uses caching to avoid redundant API calls (5-minute TTL).
        
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
            cache_key = f"{symbol}_{timeframe}_{period}"
            
            # Check cache first
            now = datetime.now(self.timezone)
            if symbol in self._atr_cache and cache_key in self._atr_cache[symbol]:
                cached_data, cached_time = self._atr_cache[symbol][cache_key]
                if now - cached_time < self._atr_cache_ttl:
                    logger.debug(f"Using cached ATR for {symbol} ({timeframe})")
                    return cached_data
                else:
                    # Cache expired, remove it
                    del self._atr_cache[symbol][cache_key]
            
            def _bar_time_utc(bar: Dict) -> Optional[datetime]:
                """Parse a bar timestamp into a timezone-aware UTC datetime."""
                ts = bar.get('timestamp') or bar.get('time') or bar.get('t')
                if not ts:
                    return None
                if isinstance(ts, datetime):
                    dt = ts
                elif hasattr(ts, 'to_pydatetime'):
                    # pandas Timestamp-like
                    try:
                        dt = ts.to_pydatetime()
                    except Exception:
                        dt = datetime.fromtimestamp(ts.timestamp(), tz=timezone.utc)
                elif isinstance(ts, str):
                    dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
                elif isinstance(ts, (int, float)):
                    # epoch seconds or ms
                    if ts > 1e12:
                        dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
                    else:
                        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
                else:
                    return None
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(timezone.utc)

            # Fetch a deeper history window for Wilder smoothing to better match TradingView/PineScript.
            # With only period+1 bars, Wilder ATR degenerates into just an SMA and will differ from ta.atr().
            atr_history_bars = int(os.getenv("ATR_HISTORY_BARS", "200"))
            intraday_limit = max(period + 1, atr_history_bars)

            bars = await self.trading_bot.get_historical_data(
                symbol=symbol,
                timeframe=timeframe,
                limit=intraday_limit
            )
            
            if not bars or len(bars) < period + 1:
                logger.error(f"Insufficient bars for ATR calculation: {len(bars) if bars else 0} bars")
                return None

            # IMPORTANT: broker historical data is often returned newest-first.
            # ATR requires chronological order (oldest->newest).
            bars_sorted = sorted(
                bars,
                key=lambda b: _bar_time_utc(b) or datetime.min.replace(tzinfo=timezone.utc)
            )
            
            # Calculate True Range for each bar (chronological)
            true_ranges = []
            for i in range(1, len(bars_sorted)):
                current_bar = bars_sorted[i]
                prev_bar = bars_sorted[i - 1]
                
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
            
            # Calculate ATR using Wilder's Smoothing (matches PineScript ta.atr())
            # PineScript ta.atr() uses Wilder's smoothing, not simple moving average
            # Formula: First ATR = SMA of first period TR values
            #          Subsequent ATR = ((Previous ATR * (period - 1)) + Current TR) / period
            if len(true_ranges) < period:
                logger.error(f"Insufficient true ranges for ATR: {len(true_ranges)} < {period}")
                return None
            
            # First ATR = Simple Average of first period TR values
            current_atr = sum(true_ranges[:period]) / period
            
            # Apply Wilder's smoothing for remaining TR values
            for tr in true_ranges[period:]:
                current_atr = ((current_atr * (period - 1)) + tr) / period
            
            # ALWAYS calculate daily ATR from daily bars using Wilder's smoothing (required for accurate zone calculations)
            daily_atr = current_atr  # Default fallback
            try:
                daily_bars = await self.trading_bot.get_historical_data(
                    symbol=symbol,
                    timeframe='1d',
                    limit=max(period + 1, atr_history_bars)
                )
                
                if daily_bars and len(daily_bars) >= period + 1:
                    daily_bars_sorted = sorted(
                        daily_bars,
                        key=lambda b: _bar_time_utc(b) or datetime.min.replace(tzinfo=timezone.utc)
                    )
                    daily_true_ranges = []
                    for i in range(1, len(daily_bars_sorted)):
                        current_bar = daily_bars_sorted[i]
                        prev_bar = daily_bars_sorted[i - 1]
                        
                        high = current_bar.get('high', current_bar.get('h', 0))
                        low = current_bar.get('low', current_bar.get('l', 0))
                        prev_close = prev_bar.get('close', prev_bar.get('c', 0))
                        
                        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
                        daily_true_ranges.append(tr)
                    
                    if daily_true_ranges and len(daily_true_ranges) >= period:
                        # Calculate daily ATR using Wilder's smoothing (matches PineScript)
                        daily_atr = sum(daily_true_ranges[:period]) / period  # First ATR = SMA
                        for tr in daily_true_ranges[period:]:
                            daily_atr = ((daily_atr * (period - 1)) + tr) / period  # Wilder's smoothing
                        logger.info(f"Daily ATR (Wilder's) calculated from {len(daily_bars_sorted)} daily bars: {daily_atr:.2f}")
                    else:
                        logger.warning(f"Could not calculate daily ATR from daily bars, using current ATR approximation")
                else:
                    logger.warning(f"Insufficient daily bars for ATR: {len(daily_bars) if daily_bars else 0}, using current ATR approximation")
            except Exception as e:
                logger.warning(f"Error fetching daily bars for ATR calculation: {e}, using current ATR approximation")
            
            # Get current price for ATR zones
            current_price = bars_sorted[-1].get('close', bars_sorted[-1].get('c', 0))
            
            # Calculate ATR zones
            atr_zone_high = current_price + daily_atr
            atr_zone_low = current_price - daily_atr
            
            # Get market open price (exact 9:30am 1-minute candle open) - CRITICAL for accurate zone calculations
            # Use 1-minute bars to get the exact market open time, not a fallback
            market_open_price = 0.0
            now = datetime.now(self.timezone)
            open_hour, open_min = map(int, self.market_open_time.split(':'))
            market_open_today = now.replace(hour=open_hour, minute=open_min, second=0, microsecond=0)
            
            try:
                # Convert market open time to UTC for API request
                if pytz:
                    market_open_utc = market_open_today.astimezone(pytz.UTC)
                else:
                    market_open_utc = market_open_today.astimezone(timezone.utc)
                
                # Fetch 1-minute bars around market open time (get a window to ensure we capture it)
                # Get bars from 30 minutes before market open to 30 minutes after
                start_time_utc = (market_open_today - timedelta(minutes=30)).astimezone(pytz.UTC if pytz else timezone.utc)
                end_time_utc = (market_open_today + timedelta(minutes=30)).astimezone(pytz.UTC if pytz else timezone.utc)
                
                open_bars = await self.trading_bot.get_historical_data(
                    symbol=symbol,
                    timeframe='1m',
                    start_time=start_time_utc,
                    end_time=end_time_utc,
                    limit=60  # 60 minutes should be enough
                )
                
                if open_bars and len(open_bars) > 0:
                    market_open_price = None

                    open_bars_sorted = sorted(
                        open_bars,
                        key=lambda b: _bar_time_utc(b) or datetime.min.replace(tzinfo=timezone.utc)
                    )

                    def _bar_time_local_min(bar: Dict) -> Optional[datetime]:
                        dt_utc = _bar_time_utc(bar)
                        if not dt_utc:
                            return None
                        return dt_utc.astimezone(self.timezone).replace(second=0, microsecond=0)

                    # Find the exact 1-minute bar at market open time (e.g., 09:30:00)
                    for bar in open_bars_sorted:
                        bt = _bar_time_local_min(bar)
                        if bt and bt == market_open_today:
                            market_open_price = bar.get('open', bar.get('o', 0))
                            logger.info(
                                f"Market open price (1m) for {symbol}: "
                                f"{bt.strftime('%Y-%m-%d %H:%M:%S')} @ {market_open_price:.2f}"
                            )
                            break
                    
                    # If exact match not found, try to find the closest bar within 1 minute
                    if market_open_price is None:
                        def _dist_seconds(target: datetime, bar: Dict) -> float:
                            bt = _bar_time_local_min(bar)
                            if not bt:
                                return float("inf")
                            return abs((bt - target).total_seconds())

                        best_bar = min(open_bars_sorted, key=lambda b: _dist_seconds(market_open_today, b))
                        bt = _bar_time_local_min(best_bar)
                        dist_seconds = _dist_seconds(market_open_today, best_bar)
                        
                        # Only use if within 1 minute (60 seconds) of market open
                        if dist_seconds <= 60:
                            market_open_price = best_bar.get('open', best_bar.get('o', 0))
                            logger.info(
                                f"Market open price (1m, closest within 1min) for {symbol}: "
                                f"{bt.strftime('%Y-%m-%d %H:%M:%S') if bt else 'unknown'} @ {market_open_price:.2f} "
                                f"(offset: {dist_seconds:.0f}s)"
                            )
                        else:
                            logger.warning(
                                f"Could not find 1m bar near market open for {symbol} "
                                f"(closest was {dist_seconds:.0f}s away), using current price"
                            )
                            market_open_price = current_price
                    
                    # Final fallback: use current price if we couldn't find market open bar
                    if market_open_price is None or market_open_price == 0:
                        logger.warning(f"Could not find market open 1m bar for {symbol}, using current price as fallback")
                        market_open_price = current_price
                else:
                    logger.warning(f"No 1m bars returned for market open lookup for {symbol}, using current price")
                    market_open_price = current_price
            except Exception as e:
                logger.warning(f"Error fetching 1m market open price for {symbol}: {e}, using current price as fallback")
                market_open_price = current_price
            
            # Calculate daily ATR zones.
            #
            # NOTE: Your observed "correct" zones line up with using 0.618 as the nearer bound
            # and ~0.786 as the farther bound (vs the older 0.5/0.618 pairing).
            # Keep `day_dist = daily_atr * 0.5` (dailyATR/2), but use fib multipliers 0.618 / 0.786.
            day_dist = daily_atr * 0.5
            zone_near_mult = float(os.getenv("ATR_ZONE_NEAR_MULT", "0.618"))
            zone_far_mult = float(os.getenv("ATR_ZONE_FAR_MULT", "0.786"))
            if zone_far_mult < zone_near_mult:
                # Safety: enforce ordering
                zone_near_mult, zone_far_mult = zone_far_mult, zone_near_mult
            
            # Upper zone: open + (dailyATR/2) * near .. open + (dailyATR/2) * far
            day_bull_price = market_open_price + day_dist * zone_near_mult  # Lower bound of upper zone
            day_bull_price1 = market_open_price + day_dist * zone_far_mult  # Upper bound of upper zone
            
            # Lower zone: open - (dailyATR/2) * far .. open - (dailyATR/2) * near
            day_bear_price = market_open_price - day_dist * zone_near_mult  # Upper bound (closer to open)
            day_bear_price1 = market_open_price - day_dist * zone_far_mult  # Lower bound (farther from open)
            
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
            
            logger.info(f"ATR calculated for {symbol}: Current={current_atr:.2f}, Daily={daily_atr:.2f}")
            logger.info(f"  Market Open (1m): {market_open_price:.2f}")
            logger.info(f"  day_dist = daily_atr * 0.5 = {daily_atr:.2f} * 0.5 = {day_dist:.2f}")
            logger.info(f"  Upper ATR Zone: [{day_bull_price:.2f}, {day_bull_price1:.2f}]")
            logger.info(f"  Lower ATR Zone: [{day_bear_price1:.2f}, {day_bear_price:.2f}]")
            
            # Cache the result
            if symbol not in self._atr_cache:
                self._atr_cache[symbol] = {}
            self._atr_cache[symbol][cache_key] = (atr_data, now)
            
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
            symbol = symbol.upper()
            
            # In backtest mode, use the date from available bars instead of current system date
            # This ensures we analyze overnight sessions that exist in the backtest data
            now = None
            try:
                # Try to get recent bars to determine the "current" date in backtest context
                recent_bars = await self.trading_bot.get_historical_data(
                    symbol=symbol,
                    timeframe='1m',
                    limit=1  # Just need the latest bar to get the date
                )
                if recent_bars and len(recent_bars) > 0:
                    # Get timestamp from the latest bar
                    latest_bar = recent_bars[-1]
                    bar_ts = latest_bar.get('timestamp') or latest_bar.get('time') or latest_bar.get('t')
                    if bar_ts:
                        # Parse timestamp
                        if isinstance(bar_ts, datetime):
                            bar_dt = bar_ts
                        elif hasattr(bar_ts, 'to_pydatetime'):
                            bar_dt = bar_ts.to_pydatetime()
                        elif isinstance(bar_ts, str):
                            bar_dt = datetime.fromisoformat(bar_ts.replace('Z', '+00:00'))
                        elif isinstance(bar_ts, (int, float)):
                            if bar_ts > 1e12:
                                bar_dt = datetime.fromtimestamp(bar_ts / 1000, tz=timezone.utc)
                            else:
                                bar_dt = datetime.fromtimestamp(bar_ts, tz=timezone.utc)
                        else:
                            bar_dt = None
                        
                        if bar_dt:
                            # Convert to strategy timezone
                            if bar_dt.tzinfo is None:
                                bar_dt = bar_dt.replace(tzinfo=timezone.utc)
                            if pytz:
                                now = bar_dt.astimezone(self.timezone)
                            else:
                                now = bar_dt.astimezone(self.timezone)
                            logger.debug(f"Using backtest date from bars: {now} (bar timestamp: {bar_ts})")
            except Exception as e:
                logger.debug(f"Could not determine date from bars: {e}, using current system date")
            
            # Fall back to current system date if we couldn't get it from bars (live trading)
            if now is None:
                now = datetime.now(self.timezone)
                logger.debug(f"Using current system date: {now}")
            
            # Parse configured session times
            start_hour, start_min = map(int, self.overnight_start.split(':'))
            end_hour, end_min = map(int, self.overnight_end.split(':'))
            start_clock = time(start_hour, start_min)
            end_clock = time(end_hour, end_min)
            
            # Determine the most recent completed session
            # For sessions that cross midnight (e.g., 18:00 -> 09:30):
            #   - Session starts yesterday 6pm, ends today 9:30am
            # For sessions within same day (e.g., 09:30 -> 18:00):
            #   - Session starts today 9:30am, ends today 6pm
            if end_clock <= start_clock:
                # Session crosses midnight (e.g., 18:00 -> 09:30)
                if now.time() >= end_clock:
                    # We're past the end time today, so the session that just ended was:
                    # Start: yesterday at start_clock, End: today at end_clock
                    end_date = now.date()
                    start_date = end_date - timedelta(days=1)
                else:
                    # We're before the end time today, so the session that just ended was:
                    # Start: day before yesterday at start_clock, End: yesterday at end_clock
                    end_date = (now - timedelta(days=1)).date()
                    start_date = end_date - timedelta(days=1)
            else:
                # Session contained within same calendar day (e.g., 09:30 -> 18:00)
                if now.time() >= end_clock:
                    # We're past the end time today, so the session that just ended was:
                    # Start: today at start_clock, End: today at end_clock
                    end_date = now.date()
                    start_date = now.date()  # Same day
                else:
                    # We're before the end time today, so the session that just ended was:
                    # Start: yesterday at start_clock, End: yesterday at end_clock
                    end_date = (now - timedelta(days=1)).date()
                    start_date = (now - timedelta(days=1)).date()  # Same day
            
            end_time = self.timezone.localize(datetime.combine(end_date, end_clock))
            start_time = self.timezone.localize(datetime.combine(start_date, start_clock))
            
            # Safety: if start_time is equal to or after end_time, something went wrong
            if start_time >= end_time:
                logger.error(f"⚠️  Invalid session time range: start={start_time} >= end={end_time}. This should not happen!")
                # For same-day sessions, ensure start is before end on the same day
                if end_clock > start_clock:
                    # Same day session - both should be on end_date
                    start_time = self.timezone.localize(datetime.combine(end_date, start_clock))
                else:
                    # Crosses midnight - start should be day before end
                    start_time = self.timezone.localize(datetime.combine(end_date - timedelta(days=1), start_clock))
            
            # Calculate how many 1-minute bars we need
            session_duration = end_time - start_time
            session_minutes = int(session_duration.total_seconds() / 60)
            
            logger.info(f"Fetching overnight range for {symbol}")
            logger.info(f"  Session: {start_time.strftime('%Y-%m-%d %H:%M')} to {end_time.strftime('%Y-%m-%d %H:%M')}")
            logger.info(f"  Duration: {session_minutes} minutes")
            
            # Convert to UTC for API request
            if pytz:
                start_time_utc = start_time.astimezone(pytz.UTC)
                end_time_utc = end_time.astimezone(pytz.UTC)
            else:
                start_time_utc = start_time.astimezone(timezone.utc)
                end_time_utc = end_time.astimezone(timezone.utc)
            
            # Fetch bars for the overnight session using explicit date range to avoid cache mismatch
            bars = await self.trading_bot.get_historical_data(
                symbol=symbol,
                timeframe='1m',
                start_time=start_time_utc,
                end_time=end_time_utc,
                limit=session_minutes + 10  # still provide limit for DB helpers
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
                
                # Parse timestamp - handle multiple formats including pandas Timestamp
                # Check for datetime/Timestamp objects first to avoid type comparison errors
                if isinstance(ts, datetime):
                    # Already a datetime object
                    bar_time = ts
                elif hasattr(ts, 'to_pydatetime'):
                    # pandas Timestamp - convert to datetime
                    try:
                        bar_time = ts.to_pydatetime()
                    except AttributeError:
                        # Might be a different Timestamp-like object
                        bar_time = datetime.fromtimestamp(ts.timestamp(), tz=timezone.utc)
                elif isinstance(ts, str):
                    bar_time = datetime.fromisoformat(ts.replace('Z', '+00:00'))
                elif isinstance(ts, (int, float)):
                    # Numeric timestamp - only do numeric comparison for numeric types
                    if ts > 1e12:
                        bar_time = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
                    else:
                        bar_time = datetime.fromtimestamp(ts, tz=timezone.utc)
                else:
                    # Unknown type, skip this bar
                    logger.warning(f"Unknown timestamp type: {type(ts)}, value: {ts}")
                    continue
                
                # Ensure timezone-aware and convert to strategy timezone
                if bar_time.tzinfo is None:
                    bar_time = bar_time.replace(tzinfo=timezone.utc)
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
            symbol = symbol.upper()
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
            # Use MIDPOINT of ATR zone (halfway between closest and farthest points)
            upper_zone_midpoint = (atr_data.day_bull_price + atr_data.day_bull_price1) / 2.0
            
            if upper_zone_inside:
                # ATR zone is inside range - use ATR * 2
                logger.info(f"  Upper ATR zone inside overnight range - using ATR*2 for TP")
                long_tp_raw = long_entry_raw + (atr_data.current_atr * 2.0)
            elif atr_data.day_bull_price > range_data.high:
                # ATR zone is ABOVE range - TARGET THE MIDPOINT of the zone
                logger.info(f"  Upper ATR zone above overnight range - targeting zone midpoint at {upper_zone_midpoint:.2f} (zone: [{atr_data.day_bull_price:.2f}, {atr_data.day_bull_price1:.2f}])")
                long_tp_raw = upper_zone_midpoint
            else:
                # ATR zone is BELOW range - use ATR * 2 from entry
                logger.info(f"  Upper ATR zone below overnight range - using ATR*2 for TP")
                long_tp_raw = long_entry_raw + (atr_data.current_atr * 2.0)
            
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
            # Use MIDPOINT of ATR zone (halfway between closest and farthest points)
            lower_zone_midpoint = (atr_data.day_bear_price + atr_data.day_bear_price1) / 2.0
            
            if lower_zone_inside:
                # ATR zone is inside range - use ATR * 2
                logger.info(f"  Lower ATR zone inside overnight range - using ATR*2 for TP")
                short_tp_raw = short_entry_raw - (atr_data.current_atr * 2.0)
            elif atr_data.day_bear_price1 < range_data.low:
                # ATR zone is BELOW range - TARGET THE MIDPOINT of the zone
                # For SHORT orders, TP must be BELOW entry, so we use the midpoint of the lower zone
                logger.info(f"  Lower ATR zone below overnight range - targeting zone midpoint at {lower_zone_midpoint:.2f} (zone: [{atr_data.day_bear_price1:.2f}, {atr_data.day_bear_price:.2f}])")
                short_tp_raw = lower_zone_midpoint
            else:
                # ATR zone is ABOVE range - use ATR * 2 from entry
                logger.info(f"  Lower ATR zone above overnight range - using ATR*2 for TP")
                short_tp_raw = short_entry_raw - (atr_data.current_atr * 2.0)
            
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
            symbol = symbol.upper()
            logger.info(f"🚀 Placing range break orders for {symbol}...")
            
            # Check total exposure (positions + pending orders) BEFORE placing orders - CRITICAL
            open_orders = await self.trading_bot.get_open_orders()
            orders_list = open_orders if isinstance(open_orders, list) else []
            total_exposure = await self.get_total_exposure(symbol, orders_list)
            
            if total_exposure >= self.max_quantity_per_instrument:
                position_qty = await self.get_current_position_quantity(symbol)
                pending_qty = await self.get_pending_entry_order_quantity(symbol, orders_list)
                logger.warning(f"⚠️  Max quantity reached for {symbol}: {total_exposure}/{self.max_quantity_per_instrument} contracts (pos={position_qty} + pending={pending_qty}) - skipping order placement")
                return {"success": False, "error": f"Max quantity reached: {total_exposure}/{self.max_quantity_per_instrument}"}
            
            # Calculate orders
            long_order, short_order = await self.calculate_range_break_orders(symbol)
            if not long_order or not short_order:
                return {"success": False, "error": "Failed to calculate orders"}

            # Remember breakout templates for proactive monitoring
            self.breakout_levels[symbol] = {
                "BUY": long_order,
                "SELL": short_order,
            }
            self.breakout_active_orders.setdefault(symbol, {})
            
            # Extract account ID (handle both dict and string formats)
            # CRITICAL: Validate account selection to ensure PRAC account is used
            account_id = None
            account_name = "Unknown"
            
            if self.trading_bot.selected_account:
                if isinstance(self.trading_bot.selected_account, dict):
                    account_id = self.trading_bot.selected_account.get('id')
                    account_name = self.trading_bot.selected_account.get('name', 'Unknown')
                else:
                    account_id = self.trading_bot.selected_account
                    account_name = str(account_id)
            
            # Validate account selection - ensure PRAC account is used (skip in backtest mode)
            if account_id:
                account_id_str = str(account_id)
                account_name_upper = account_name.upper()
                
                # Skip validation in backtest mode
                is_backtest = (
                    'BACKTEST' in account_name_upper or 
                    account_id_str == 'backtest_account' or
                    hasattr(self.trading_bot, 'bars')  # Mock trading bot has bars attribute
                )
                
                if is_backtest:
                    logger.debug(f"Backtest mode detected - skipping account validation")
                    # Use the backtest account as-is
                else:
                    # Check if account name contains PRAC or PRACTICE
                    is_prac_account = 'PRAC' in account_name_upper or 'PRACTICE' in account_name_upper
                    
                    if not is_prac_account:
                        logger.error(f"⚠️  WARNING: Strategy attempting to use non-PRAC account: {account_name} (ID: {account_id_str})")
                        logger.error(f"⚠️  This should be a PRAC/PRACTICE account. Checking for PRAC account...")
                        
                        # Try to find PRAC account
                        try:
                            accounts = await self.trading_bot.list_accounts()
                            prac_account = None
                            for acc in accounts:
                                acc_name = acc.get('name', '').upper()
                                if 'PRAC' in acc_name or 'PRACTICE' in acc_name:
                                    prac_account = acc
                                    break
                            
                            if prac_account:
                                account_id = prac_account.get('id')
                                account_name = prac_account.get('name', 'Unknown')
                                logger.warning(f"✅ Found PRAC account: {account_name} (ID: {account_id}), switching to it")
                                self.trading_bot.selected_account = prac_account
                            else:
                                logger.error(f"❌ No PRAC account found! Available accounts: {[acc.get('name') for acc in accounts]}")
                                return {"success": False, "error": f"No PRAC account found. Current account: {account_name}"}
                        except Exception as acc_err:
                            logger.error(f"❌ Failed to validate account: {acc_err}")
                            return {"success": False, "error": f"Account validation failed: {acc_err}"}
                    else:
                        logger.info(f"✅ Using PRAC account: {account_name} (ID: {account_id})")
            else:
                logger.error("❌ No account selected for strategy execution")
                return {"success": False, "error": "No account selected"}
            
            results = {"symbol": symbol, "orders": []}
            
            # Place long breakout order with strategy name in custom tag
            long_result = await self.trading_bot.place_oco_bracket_with_stop_entry(
                symbol=symbol,
                side="BUY",
                quantity=long_order.quantity,
                entry_price=long_order.entry_price,
                stop_loss_price=long_order.stop_loss,
                take_profit_price=long_order.take_profit,
                account_id=account_id,
                strategy_name=self.config.name  # Add strategy name for tracking
            )

            # Check for successful order placement (response has orderId or success=True)
            if long_result and long_result.get('orderId'):
                order_id = long_result.get('orderId')
                results["orders"].append({"side": "LONG", "order_id": order_id, "result": long_result})
                logger.info(f"✅ Long breakout order placed: {order_id}")
            # Fallback: If bracket order fails due to API issues, try plain stop order
            elif long_result and "500" in str(long_result.get('error', '')):
                logger.warning(f"⚠️  Bracket order got 500 error, trying plain stop order as fallback...")
                plain_result = await self.trading_bot.place_stop_order(
                    symbol=symbol,
                    side="BUY",
                    quantity=long_order.quantity,
                    stop_price=long_order.entry_price,
                    account_id=account_id
                )
                if plain_result and plain_result.get('orderId'):
                    order_id = plain_result.get('orderId')
                    results["orders"].append({"side": "LONG", "order_id": order_id, "result": plain_result})
                    logger.warning(f"✅ Plain stop order placed (no brackets): {order_id}")
                    logger.warning(f"🔄 Will monitor for fill and add SL/TP automatically")
                    # Store for automatic bracket addition after fill
                    if symbol not in self.breakout_active_orders:
                        self.breakout_active_orders[symbol] = {}
                    self.breakout_active_orders[symbol]["BUY"] = {
                        "order_id": order_id,
                        "entry_price": long_order.entry_price,
                        "stop_loss": long_order.stop_loss,
                        "take_profit": long_order.take_profit,
                        "quantity": long_order.quantity,
                        "needs_brackets": True,  # Flag for post-fill bracket addition
                        "monitoring": True  # Start monitoring for fill
                    }
                else:
                    error_msg = plain_result.get('error', 'Unknown error') if plain_result else 'No response'
                    logger.error(f"❌ Plain stop order also failed: {error_msg}")
                    results.setdefault("errors", [])
                    results["errors"].append({"side": "LONG", "error": f"Both bracket and plain orders failed: {error_msg}"})
                    if symbol in self.breakout_active_orders:
                        self.breakout_active_orders[symbol].pop("BUY", None)
                # NOTE: This line is misplaced - it sets order_id to string even when plain order failed
                # Removed: self.breakout_active_orders.setdefault(symbol, {})["BUY"] = str(order_id)
                
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
            elif long_result and (long_result.get('error') or long_result.get('errorMessage')):
                error_msg = long_result.get('error') or long_result.get('errorMessage', 'Unknown error')
                logger.warning(f"⚠️  Long order failed: {error_msg}")
                results["errors"] = results.get("errors", [])
                results["errors"].append({"side": "LONG", "error": error_msg})
                if symbol in self.breakout_active_orders:
                    self.breakout_active_orders[symbol].pop("BUY", None)
            
            # Place short breakout order with strategy name in custom tag
            short_result = await self.trading_bot.place_oco_bracket_with_stop_entry(
                symbol=symbol,
                side="SELL",
                quantity=short_order.quantity,
                entry_price=short_order.entry_price,
                stop_loss_price=short_order.stop_loss,
                take_profit_price=short_order.take_profit,
                account_id=account_id,
                strategy_name=self.config.name  # Add strategy name for tracking
            )

            # Check for successful order placement (response has orderId)
            if short_result and short_result.get('orderId'):
                order_id = short_result.get('orderId')
                results["orders"].append({"side": "SHORT", "order_id": order_id, "result": short_result})
                logger.info(f"✅ Short breakout order placed: {order_id}")
            # Fallback: If bracket order fails due to API issues, try plain stop order
            elif short_result and "500" in str(short_result.get('error', '')):
                logger.warning(f"⚠️  Bracket order got 500 error, trying plain stop order as fallback...")
                plain_result = await self.trading_bot.place_stop_order(
                    symbol=symbol,
                    side="SELL",
                    quantity=short_order.quantity,
                    stop_price=short_order.entry_price,
                    account_id=account_id
                )
                if plain_result and plain_result.get('orderId'):
                    order_id = plain_result.get('orderId')
                    results["orders"].append({"side": "SHORT", "order_id": order_id, "result": plain_result})
                    logger.warning(f"✅ Plain stop order placed (no brackets): {order_id}")
                    logger.warning(f"🔄 Will monitor for fill and add SL/TP automatically")
                    # Store for automatic bracket addition after fill
                    if symbol not in self.breakout_active_orders:
                        self.breakout_active_orders[symbol] = {}
                    self.breakout_active_orders[symbol]["SELL"] = {
                        "order_id": order_id,
                        "entry_price": short_order.entry_price,
                        "stop_loss": short_order.stop_loss,
                        "take_profit": short_order.take_profit,
                        "quantity": short_order.quantity,
                        "needs_brackets": True,  # Flag for post-fill bracket addition
                        "monitoring": True  # Start monitoring for fill
                    }
                else:
                    error_msg = plain_result.get('error', 'Unknown error') if plain_result else 'No response'
                    logger.error(f"❌ Plain stop order also failed: {error_msg}")
                    results.setdefault("errors", [])
                    results["errors"].append({"side": "SHORT", "error": f"Both bracket and plain orders failed: {error_msg}"})
                    if symbol in self.breakout_active_orders:
                        self.breakout_active_orders[symbol].pop("SELL", None)
                # NOTE: This line is misplaced - it sets order_id to string even when plain order failed
                # Removed: self.breakout_active_orders.setdefault(symbol, {})["SELL"] = str(order_id)
                
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
            elif short_result and (short_result.get('error') or short_result.get('errorMessage')):
                error_msg = short_result.get('error') or short_result.get('errorMessage', 'Unknown error')
                logger.warning(f"⚠️  Short order failed: {error_msg}")
                results["errors"] = results.get("errors", [])
                results["errors"].append({"side": "SHORT", "error": error_msg})
                if symbol in self.breakout_active_orders:
                    self.breakout_active_orders[symbol].pop("SELL", None)
            
            results["success"] = len(results["orders"]) > 0
            return results
            
        except Exception as e:
            logger.error(f"Error placing range break orders for {symbol}: {e}")
            return {"success": False, "error": str(e)}

    def _extract_quote_price(self, quote: Dict) -> Optional[float]:
        """Return best available price from quote payload."""
        if not quote or quote.get("error"):
            return None
        for key in ("last", "bid", "ask", "price"):
            value = quote.get(key)
            if value is None:
                continue
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
        return None

    def _group_orders_by_symbol(self, orders: Optional[List[Dict]]) -> Dict[str, List[Dict]]:
        """Group open orders by symbol for quick lookup."""
        grouped: Dict[str, List[Dict]] = {}
        if not orders or isinstance(orders, dict) and orders.get("error"):
            return grouped
        for order in orders:
            symbol = order.get("symbol")
            if not symbol:
                contract_id = order.get("contractId")
                if contract_id:
                    symbol = self.trading_bot._get_symbol_from_contract_id(contract_id)
            if not symbol:
                continue
            grouped.setdefault(str(symbol).upper(), []).append(order)
        return grouped

    def _order_matches_breakout(self, order: Dict, side: str, target_price: float, tolerance: float) -> bool:
        """Check if an existing order already covers a breakout level."""
        try:
            # Get order type - handle both string and numeric types
            order_type = order.get("type") or order.get("raw_type")
            if order_type not in (4, "4", "Stop", "stop"):
                return False
            
            # Check if it's a reduce-only order (SL/TP brackets) - skip those
            is_reduce_only = order.get("reduceOnly") or order.get("reduce_only", False)
            custom_tag = order.get('customTag') or order.get('custom_tag') or ''
            is_bracket_sl_tp = '-SL' in str(custom_tag) or '-TP' in str(custom_tag) or 'AutoBracket' in str(custom_tag)
            
            if is_reduce_only or is_bracket_sl_tp:
                return False  # Don't match SL/TP brackets, only entry orders
            
            # Check side
            raw_side = order.get("side", -1)
            order_side = "BUY" if raw_side in (0, "0", "buy", "BUY") else "SELL"
            if order_side != side:
                return False
            
            # Get order price - check multiple possible fields
            order_price = (order.get("stopPrice") or order.get("price") or 
                          order.get("limitPrice") or order.get("stop_price"))
            
            # For bracket orders, check the entry price in bracket structure
            if order_price is None:
                bracket = order.get("bracket") or {}
                order_price = bracket.get("entryPrice") or bracket.get("stopPrice")
            
            if order_price is None:
                return False
            
            order_price = float(order_price)
            return abs(order_price - target_price) <= tolerance
        except Exception:
            return False

    async def _place_single_breakout_order(self, order_template: RangeBreakOrder) -> Optional[str]:
        """Place a single breakout stop order (used by proactive monitor)."""
        symbol = order_template.symbol.upper()
        
        # Check total exposure (positions + pending orders) - CRITICAL
        open_orders = await self.trading_bot.get_open_orders()
        orders_list = open_orders if isinstance(open_orders, list) else []
        total_exposure = await self.get_total_exposure(symbol, orders_list)
        
        if total_exposure >= self.max_quantity_per_instrument:
            position_qty = await self.get_current_position_quantity(symbol)
            pending_qty = await self.get_pending_entry_order_quantity(symbol, orders_list)
            logger.warning(f"⚠️  Max quantity reached for {symbol}: {total_exposure}/{self.max_quantity_per_instrument} contracts (pos={position_qty} + pending={pending_qty}) - skipping order")
            return None
        
        # Get tick size and round prices to valid tick increments BEFORE validation
        tick_size = await self.get_tick_size(symbol)
        entry_price = self.round_to_tick(order_template.entry_price, tick_size)
        stop_loss_price = self.round_to_tick(order_template.stop_loss, tick_size)
        take_profit_price = self.round_to_tick(order_template.take_profit, tick_size)
        
        # Get exposure breakdown for logging
        position_qty = await self.get_current_position_quantity(symbol)
        pending_qty = await self.get_pending_entry_order_quantity(symbol, orders_list)
        logger.info(f"📌 Placing {order_template.side} breakout order for {symbol} at {entry_price:.2f} (tick_size={tick_size}, exposure={total_exposure}/{self.max_quantity_per_instrument} [pos={position_qty} + pending={pending_qty}])")
        
        # Validate entry price against current market price
        try:
            quote = await self.trading_bot.get_market_quote(symbol)
            current_price = self._extract_quote_price(quote)
            
            if current_price is not None:
                # For BUY (LONG) stop orders: entry must be above current price
                # For SELL (SHORT) stop orders: entry must be below current price
                if order_template.side == "BUY" and entry_price < current_price:
                    logger.warning(f"⚠️  LONG entry {entry_price:.2f} is below current price {current_price:.2f}, skipping stale order")
                    return None
                elif order_template.side == "SELL" and entry_price > current_price:
                    logger.warning(f"⚠️  SHORT entry {entry_price:.2f} is above current price {current_price:.2f}, skipping stale order")
                    return None
                
                logger.debug(f"✓ Entry price {entry_price:.2f} is valid vs current {current_price:.2f}")
        except Exception as e:
            logger.warning(f"⚠️  Could not validate entry price against market: {e}")
            # Continue anyway - let the API reject if invalid
        
        account_id = None
        if self.trading_bot.selected_account:
            if isinstance(self.trading_bot.selected_account, dict):
                account_id = self.trading_bot.selected_account.get('id')
            else:
                account_id = self.trading_bot.selected_account

        result = await self.trading_bot.place_oco_bracket_with_stop_entry(
            symbol=symbol,
            side=order_template.side,
            quantity=order_template.quantity,
            entry_price=entry_price,
            stop_loss_price=stop_loss_price,
            take_profit_price=take_profit_price,
            account_id=account_id,
            strategy_name=self.config.name  # Add strategy name for tracking
        )

        if not result or not result.get("orderId"):
            logger.warning(f"⚠️  Unable to place {order_template.side} breakout order for {symbol}: {result.get('error') if result else 'unknown error'}")
            return None

        order_id = str(result["orderId"])
        self.active_orders.setdefault(symbol, []).append(order_id)
        self.breakout_active_orders.setdefault(symbol, {})[order_template.side] = order_id

        if self.breakeven_enabled:
            self.breakeven_monitoring[order_id] = {
                "symbol": symbol,
                "side": "LONG" if order_template.side == "BUY" else "SHORT",
                "entry_price": order_template.entry_price,
                "original_stop": order_template.stop_loss,
                "breakeven_triggered": False,
                "position_filled": False
            }

        logger.info(f"✅ {order_template.side} breakout order submitted: {order_id}")
        return order_id

    async def _ensure_breakout_order(self, symbol: str, side: str, order_template: RangeBreakOrder,
                                     existing_orders: List[Dict], tick_size: float) -> None:
        """Ensure there is an active breakout stop order near the target level."""
        symbol = symbol.upper()
        
        # Check total exposure (positions + pending orders) - CRITICAL
        total_exposure = await self.get_total_exposure(symbol, existing_orders)
        if total_exposure >= self.max_quantity_per_instrument:
            position_qty = await self.get_current_position_quantity(symbol)
            pending_qty = await self.get_pending_entry_order_quantity(symbol, existing_orders)
            logger.debug(f"⚠️  Max quantity reached for {symbol}: {total_exposure}/{self.max_quantity_per_instrument} contracts (pos={position_qty} + pending={pending_qty}) - skipping order")
            return
        
        # Check for exact duplicate order at same price (tight tolerance)
        tolerance = max(self.breakout_order_tolerance_points, tick_size)
        for order in existing_orders:
            if self._order_matches_breakout(order, side, order_template.entry_price, tolerance):
                order_id = str(order.get("id"))
                self.breakout_active_orders.setdefault(symbol, {})[side] = order_id
                logger.debug(f"🔁 Existing {side} breakout order already working for {symbol} at {order_template.entry_price:.2f} (ID {order_id}) - skipping duplicate")
                return
        
        # Count ALL pending entry orders for this symbol/side (not just matching price)
        # This prevents excessive orders even if prices are slightly different
        symbol_side_entry_orders = []
        for order in existing_orders:
            order_symbol = order.get('symbol', '').upper()
            if not order_symbol:
                contract_id = order.get('contractId')
                if contract_id and hasattr(self.trading_bot, '_get_symbol_from_contract_id'):
                    order_symbol = self.trading_bot._get_symbol_from_contract_id(contract_id)
            
            if order_symbol != symbol:
                continue
            
            raw_side = order.get("side", -1)
            order_side = "BUY" if raw_side in (0, "0", "buy", "BUY") else "SELL"
            if order_side == side:
                # Check if it's an entry order (not reduce-only, not bracket SL/TP)
                is_reduce_only = order.get('reduceOnly') or order.get('reduce_only', False)
                custom_tag = order.get('customTag') or order.get('custom_tag') or ''
                is_bracket_sl_tp = '-SL' in str(custom_tag) or '-TP' in str(custom_tag)
                order_type = order.get('type') or order.get('raw_type')
                
                # Entry orders: stop orders (type 4) that are not reduce-only and not bracket SL/TP
                is_entry_order = (
                    order_type in (4, "4", "Stop", "stop") and 
                    not is_reduce_only and 
                    not is_bracket_sl_tp
                )
                
                if is_entry_order:
                    symbol_side_entry_orders.append(order)
        
        # Limit to max 2 pending entry orders per symbol/side to prevent spam
        if len(symbol_side_entry_orders) >= 2:
            logger.debug(f"⚠️  Too many pending {side} entry orders for {symbol} ({len(symbol_side_entry_orders)}) - skipping")
            return

        # Remove stale cached order id if it exists but no matching order
        if symbol in self.breakout_active_orders:
            self.breakout_active_orders[symbol].pop(side, None)

        await self._place_single_breakout_order(order_template)

    async def monitor_breakout_levels(self):
        """Continuously monitor price and keep breakout stop orders staged near range extremes."""
        if not self.breakout_monitor_enabled:
            logger.info("⚙️  Breakout monitoring disabled via BREAKOUT_MONITOR_ENABLED")
            return

        logger.info(
            f"⚙️  Breakout monitoring ENABLED (threshold={self.breakout_proximity_percent}% "
            f"min={self.breakout_min_proximity_points} pts, interval={self.breakout_monitor_interval}s)"
        )

        # Cache open orders to reduce API calls (refresh every 30 seconds)
        orders_cache = {"orders": [], "timestamp": None, "ttl": 30}
        
        while self.is_trading:
            try:
                if not self.breakout_levels:
                    await asyncio.sleep(self.breakout_monitor_interval)
                    continue
                
                # Check if market has opened (prevent orders before 09:30 ET)
                now = datetime.now(self.timezone)
                open_hour, open_min = map(int, self.market_open_time.split(':'))
                market_open_today = now.replace(hour=open_hour, minute=open_min, second=0, microsecond=0)
                
                if now < market_open_today:
                    # Before market open - wait until market opens
                    time_until_open = (market_open_today - now).total_seconds()
                    logger.debug(f"⏰ Waiting for market open ({time_until_open/60:.1f}m away) - skipping order placement")
                    await asyncio.sleep(min(self.breakout_monitor_interval, 60))  # Check at least once per minute
                    continue

                # Use cached orders if available, otherwise fetch fresh
                cache_age = (datetime.now() - orders_cache["timestamp"]).total_seconds() if orders_cache["timestamp"] else float('inf')
                if cache_age >= orders_cache["ttl"]:
                    open_orders = await self.trading_bot.get_open_orders()
                    orders_list = open_orders if isinstance(open_orders, list) else []
                    orders_cache["orders"] = orders_list
                    orders_cache["timestamp"] = datetime.now()
                else:
                    orders_list = orders_cache["orders"]
                
                orders_by_symbol = self._group_orders_by_symbol(orders_list)

                for symbol, templates in self.breakout_levels.items():
                    range_data = self.active_ranges.get(symbol)
                    if not range_data:
                        continue

                    quote = await self.trading_bot.get_market_quote(symbol)
                    current_price = self._extract_quote_price(quote)
                    if current_price is None:
                        continue

                    threshold_points = max(
                        range_data.range_size * (self.breakout_proximity_percent / 100.0),
                        self.breakout_min_proximity_points
                    )
                    tick_size = await self.get_tick_size(symbol)
                    symbol_orders = orders_by_symbol.get(symbol, [])

                    long_template = templates.get("BUY")
                    if long_template:
                        distance = long_template.entry_price - current_price
                        if 0 <= distance <= threshold_points:
                            await self._ensure_breakout_order(symbol, "BUY", long_template, symbol_orders, tick_size)

                    short_template = templates.get("SELL")
                    if short_template:
                        distance = current_price - short_template.entry_price
                        if 0 <= distance <= threshold_points:
                            await self._ensure_breakout_order(symbol, "SELL", short_template, symbol_orders, tick_size)

                await asyncio.sleep(self.breakout_monitor_interval)

            except asyncio.CancelledError:
                logger.info("Breakout monitoring task cancelled")
                break
            except Exception as exc:
                logger.error(f"Error in breakout monitoring: {exc}")
                await asyncio.sleep(max(self.breakout_monitor_interval, 5))
    
    async def monitor_plain_stop_fills(self):
        """
        Background task to monitor plain stop orders and add SL/TP when they fill.
        
        This is critical for the 500 error fallback path:
        - When bracket orders fail with 500, we place plain stops
        - We MUST add SL/TP automatically after fill (can't rely on manual intervention)
        - Monitors every 5 seconds until position opens, then places protection
        """
        logger.info("🛡️  Plain stop fill monitoring ACTIVE (auto-adds SL/TP after fill)")
        
        while self.is_trading:
            try:
                await asyncio.sleep(5)  # Check every 5 seconds
                
                # Check if we have any orders flagged for monitoring
                orders_to_monitor = []
                for symbol, sides in self.breakout_active_orders.items():
                    for side, order_data in sides.items():
                        # Safety check: ensure order_data is a dict, not a string (order ID)
                        if not isinstance(order_data, dict):
                            logger.debug(f"⚠️  Skipping non-dict order_data for {symbol} {side}: {type(order_data)} - {order_data}")
                            continue
                        if order_data.get('needs_brackets') and order_data.get('monitoring'):
                            orders_to_monitor.append((symbol, side, order_data))
                
                if not orders_to_monitor:
                    continue  # Nothing to monitor
                
                # Get current positions
                positions = await self.trading_bot.get_positions()
                if not positions:
                    continue
                
                # Ensure positions is a list
                if not isinstance(positions, list):
                    logger.warning(f"⚠️  Positions is not a list: {type(positions)} - {positions}")
                    continue
                
                # Check each monitored order
                for symbol, side, order_data in orders_to_monitor:
                    # Find matching position
                    position_found = False
                    for pos in positions:
                        # Safety check: ensure pos is a dict or has dict-like attributes, not a string
                        if isinstance(pos, str):
                            logger.warning(f"⚠️  Skipping string position: {pos}")
                            continue
                        
                        # Handle Position objects (with attributes) or dicts
                        if hasattr(pos, 'symbol'):
                            # Position object with attributes
                            pos_symbol = (getattr(pos, 'symbol', '') or '').upper()
                            net_qty = getattr(pos, 'net_quantity', 0) or getattr(pos, 'quantity', 0) or 0
                            pos_side = "LONG" if net_qty > 0 else "SHORT" if net_qty < 0 else None
                        elif isinstance(pos, dict):
                            # Dict with keys
                            pos_symbol = pos.get('symbol', '').upper()
                            net_qty = pos.get('net_quantity', 0) or pos.get('quantity', 0) or 0
                            pos_side = "LONG" if net_qty > 0 else "SHORT" if net_qty < 0 else None
                        else:
                            logger.warning(f"⚠️  Skipping unknown position type: {type(pos)} - {pos}")
                            continue
                        
                        if pos_symbol == symbol.upper() and pos_side == side:
                            position_found = True
                            logger.info(f"🎯 Position opened for {symbol} {side} (from plain stop), adding SL/TP now...")
                            
                            # Place stop loss (CRITICAL: use reduce_only=True to link to position)
                            try:
                                sl_result = await self.trading_bot.place_stop_order(
                                    symbol=symbol,
                                    side="SELL" if side == "LONG" else "BUY",
                                    quantity=order_data['quantity'],
                                    stop_price=order_data['stop_loss'],
                                    account_id=self.trading_bot.selected_account.get('id') if isinstance(self.trading_bot.selected_account, dict) else self.trading_bot.selected_account,
                                    reduce_only=True  # Auto-cancels when position closes
                                )
                                if sl_result and sl_result.get('orderId'):
                                    logger.info(f"✅ Stop loss placed (reduce-only): {sl_result['orderId']} @ ${order_data['stop_loss']}")
                                else:
                                    logger.error(f"❌ Failed to place stop loss: {sl_result}")
                            except Exception as sl_err:
                                logger.error(f"❌ Error placing stop loss: {sl_err}")
                            
                            # Place take profit (CRITICAL: use reduce_only=True to link to position)
                            try:
                                tp_result = await self.trading_bot.place_limit_order(
                                    symbol=symbol,
                                    side="SELL" if side == "LONG" else "BUY",
                                    quantity=order_data['quantity'],
                                    limit_price=order_data['take_profit'],
                                    account_id=self.trading_bot.selected_account.get('id') if isinstance(self.trading_bot.selected_account, dict) else self.trading_bot.selected_account,
                                    reduce_only=True  # Auto-cancels when position closes
                                )
                                if tp_result and tp_result.get('orderId'):
                                    logger.info(f"✅ Take profit placed (reduce-only): {tp_result['orderId']} @ ${order_data['take_profit']}")
                                else:
                                    logger.error(f"❌ Failed to place take profit: {tp_result}")
                            except Exception as tp_err:
                                logger.error(f"❌ Error placing take profit: {tp_err}")
                            
                            # Stop monitoring this order
                            order_data['monitoring'] = False
                            order_data['needs_brackets'] = False
                            logger.info(f"🛡️  SL/TP added for {symbol} {side} (reduce-only orders auto-cancel)")
                            break
                    
                    # If position not found but order is old (>5 minutes), stop monitoring
                    if not position_found:
                        # Could add timeout logic here if needed
                        pass
            
            except asyncio.CancelledError:
                logger.info("Plain stop fill monitoring cancelled")
                break
            except Exception as exc:
                logger.error(f"Error in plain stop fill monitoring: {exc}")
                await asyncio.sleep(5)
    
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
        Checks frequently (every 10 seconds) when close to market open time to catch it precisely.
        """
        try:
            logger.info(f"📅 Market open scanner started - targeting {self.market_open_time} {self.timezone}")
            
            while self.is_trading:
                try:
                    now = datetime.now(self.timezone)
                    open_hour, open_min = map(int, self.market_open_time.split(':'))
                    market_open_today = now.replace(hour=open_hour, minute=open_min, second=0, microsecond=0)
                    trading_end_hour, trading_end_min = map(int, self.config.trading_end_time.split(':'))
                    trading_end_today = now.replace(hour=trading_end_hour, minute=trading_end_min, second=0, microsecond=0)
                    
                    ran_today = self._last_market_open_run == market_open_today.date()
                    
                    # Check if we're at or past market open time
                    if now >= market_open_today:
                        if not ran_today:
                            grace_minutes = float(os.getenv('MARKET_OPEN_GRACE_MINUTES', '5'))
                            grace_deadline = market_open_today + timedelta(minutes=grace_minutes)
                            if now <= grace_deadline:
                                time_since_open = (now - market_open_today).total_seconds()
                                logger.info(f"🔔 Market open reached (at {now.strftime('%H:%M:%S')}, {time_since_open:.0f}s after {self.market_open_time})—executing scheduled sequence.")
                                await self._execute_market_open_sequence()
                                self._last_market_open_run = market_open_today.date()
                            else:
                                time_since_open = (now - market_open_today).total_seconds() / 60
                                logger.info(f"⏭️  Market open already passed ({time_since_open:.1f} minutes ago); skipping catch-up and waiting for next session.")
                                self._last_market_open_run = market_open_today.date()
                        
                        # After execution, schedule for next day
                        next_open = market_open_today + timedelta(days=1)
                        sleep_seconds = max((next_open - now).total_seconds(), 60.0)  # Check at least once per minute
                    else:
                        # Before market open - calculate time until market open
                        time_until_open = (market_open_today - now).total_seconds()
                        
                        # If we're within 2 minutes of market open, check every 10 seconds for precision
                        if time_until_open <= 120:  # 2 minutes
                            sleep_seconds = 10  # Check every 10 seconds when close
                            logger.debug(f"⏰ Close to market open ({time_until_open:.0f}s away) - checking every 10s")
                        elif time_until_open <= 600:  # 10 minutes
                            sleep_seconds = 30  # Check every 30 seconds when within 10 minutes
                            logger.debug(f"⏰ Approaching market open ({time_until_open/60:.1f}m away) - checking every 30s")
                        else:
                            # Far from market open - check every minute
                            sleep_seconds = 60
                        
                        next_open = market_open_today
                    
                    if sleep_seconds > 60:
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
                    import traceback
                    logger.error(f"Traceback: {traceback.format_exc()}")
                    await asyncio.sleep(300)  # Wait 5 minutes on error
        except Exception as e:
            logger.error(f"Fatal error in market open scanner: {e}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
    
    def _reload_config_from_env(self):
        """Reload configuration from environment variables (allows runtime updates)."""
        # Reload .env file to pick up any changes
        try:
            from dotenv import load_dotenv
            load_dotenv(override=True)  # override=True forces reload of existing vars
        except ImportError:
            # dotenv not available, use existing env vars
            pass
        
        def _get_env_value(names, default):
            """Return first non-empty environment variable value from provided names."""
            if isinstance(names, str):
                names_iter = [names]
            else:
                names_iter = names
            for name in names_iter:
                value = os.getenv(name)
                if value and str(value).strip():
                    return str(value).strip()
            return default
        
        self.overnight_start = _get_env_value(
            ['OVERNIGHT_START_TIME', 'OVERNIGHT_RANGE_START_TIME', 'OVERNIGHT_SESSION_START'],
            '18:00'
        )
        self.overnight_end = _get_env_value(
            ['OVERNIGHT_END_TIME', 'OVERNIGHT_RANGE_END_TIME', 'OVERNIGHT_SESSION_END'],
            '09:30'
        )
        self.market_open_time = _get_env_value(
            ['MARKET_OPEN_TIME', 'OVERNIGHT_MARKET_OPEN_TIME', 'OVERNIGHT_RANGE_MARKET_OPEN_TIME'],
            '09:30'
        )
        if pytz:
            self.timezone = pytz.timezone(os.getenv('STRATEGY_TIMEZONE', 'US/Eastern'))
        else:
            # Fallback to UTC if pytz not available
            self.timezone = timezone.utc
            logger.warning("pytz not available, using UTC timezone. Install pytz for timezone support.")
        self.atr_period = int(os.getenv('ATR_PERIOD', '14'))
        self.atr_timeframe = os.getenv('ATR_TIMEFRAME', '5m')
        self.stop_atr_multiplier = float(os.getenv('STOP_ATR_MULTIPLIER', '1.25'))
        self.tp_atr_multiplier = float(os.getenv('TP_ATR_MULTIPLIER', '2.0'))
        self.breakeven_enabled = os.getenv('BREAKEVEN_ENABLED', 'true').lower() in ('true', '1', 'yes', 'on')
        self.breakeven_profit_points = float(os.getenv('BREAKEVEN_PROFIT_POINTS', '15.0'))
        self.range_break_offset = float(os.getenv('RANGE_BREAK_OFFSET', '0.25'))
        monitor_enabled_default = 'true' if self.breakout_monitor_enabled else 'false'
        self.breakout_monitor_enabled = os.getenv('BREAKOUT_MONITOR_ENABLED', monitor_enabled_default).lower() in ('true', '1', 'yes', 'on')
        self.breakout_proximity_percent = float(os.getenv('BREAKOUT_PROXIMITY_PERCENT', str(self.breakout_proximity_percent)))
        self.breakout_min_proximity_points = float(os.getenv('BREAKOUT_MIN_PROXIMITY_POINTS', str(self.breakout_min_proximity_points)))
        self.breakout_monitor_interval = float(os.getenv('BREAKOUT_MONITOR_INTERVAL_SECONDS', str(self.breakout_monitor_interval)))
        self.breakout_order_tolerance_points = float(os.getenv('BREAKOUT_ORDER_TOLERANCE_POINTS', str(self.breakout_order_tolerance_points)))
        
        # Keep StrategyConfig in sync so persistence/UI reflect these values
        if hasattr(self, 'config'):
            self.config.trading_start_time = self.overnight_start
            self.config.trading_end_time = self.overnight_end
        logger.info(f"🔄 Reloaded config from environment: Overnight={self.overnight_start}-{self.overnight_end}, Market Open={self.market_open_time}")
    
    async def start(self, symbols: List[str] = None):
        """
        Start the overnight range strategy.
        
        Args:
            symbols: List of symbols to trade (default: from env STRATEGY_SYMBOLS)
        """
        if self.is_trading:
            logger.warning("Strategy is already running")
            return
        
        # Reload config from env vars to pick up any changes
        self._reload_config_from_env()
        
        if symbols:
            self.config.symbols = symbols
        self._last_market_open_run = None
        self.is_trading = True
        
        # Get symbols to trade
        trade_symbols = self._get_trade_symbols(symbols)
        
        # 🔥 NEW: Immediately track overnight ranges and calculate breakout levels
        # This allows the strategy to work at ANY time of day, not just at market open
        logger.info("📊 Calculating overnight ranges and breakout levels...")
        for symbol in trade_symbols:
            try:
                # Track overnight range
                range_data = await self.track_overnight_range(symbol)
                if not range_data:
                    logger.warning(f"⚠️  Could not track overnight range for {symbol}")
                    continue
                
                # Calculate breakout orders
                long_order, short_order = await self.calculate_range_break_orders(symbol)
                if long_order and short_order:
                    # Store breakout levels for monitoring
                    self.breakout_levels[symbol] = {
                        "BUY": long_order,
                        "SELL": short_order,
                    }
                    self.breakout_active_orders.setdefault(symbol, {})
                    logger.info(f"✅ Breakout levels calculated for {symbol}: LONG@{long_order.entry_price:.2f}, SHORT@{short_order.entry_price:.2f}")
                else:
                    logger.warning(f"⚠️  Could not calculate breakout orders for {symbol}")
            except Exception as e:
                logger.error(f"❌ Error setting up {symbol}: {e}")
        
        # Start background tasks
        self._tracking_task = asyncio.create_task(self.market_open_scanner())
        self._breakeven_task = asyncio.create_task(self.monitor_breakeven_stops())
        self._plain_stop_monitor_task = asyncio.create_task(self.monitor_plain_stop_fills())
        if self.breakout_monitor_enabled:
            self._breakout_monitor_task = asyncio.create_task(self.monitor_breakout_levels())

        # Print comprehensive initial configuration to terminal
        print("\n" + "="*80)
        print("🎯 OVERNIGHT RANGE STRATEGY - INITIAL CONFIGURATION")
        print("="*80)
        print(f"📊 Symbols: {', '.join(trade_symbols)}")
        print(f"⏰ Session Time: {self.overnight_start} - {self.overnight_end} {self.timezone}")
        print(f"🚪 Market Open: {self.market_open_time} {self.timezone}")
        print(f"📈 ATR Period: {self.atr_period} bars ({self.atr_timeframe})")
        print(f"🛑 Stop Loss: {self.stop_atr_multiplier}x ATR")
        print(f"🎯 Take Profit: {self.tp_atr_multiplier}x ATR")
        print(f"📦 Position Size: {self.default_quantity} contract(s) per order")
        print(f"🔢 Max Quantity per Instrument: {self.max_quantity_per_instrument} contracts")
        print(f"💰 Breakeven: {'ENABLED' if self.breakeven_enabled else 'DISABLED'} (+{self.breakeven_profit_points} pts)")
        print(f"📏 Range Break Offset: {self.range_break_offset} points")
        print(f"⚙️  Mode: CONTINUOUS (monitors price and places orders when within threshold)")
        print(f"📊 Breakout Threshold: {self.breakout_proximity_percent}% (min {self.breakout_min_proximity_points} pts)")
        print(f"⏱️  Monitor Interval: {self.breakout_monitor_interval}s")
        
        # Print calculated breakout levels for each symbol
        print("\n📊 CALCULATED BREAKOUT LEVELS:")
        print("-"*80)
        for symbol in trade_symbols:
            if symbol in self.breakout_levels:
                long_order = self.breakout_levels[symbol].get("BUY")
                short_order = self.breakout_levels[symbol].get("SELL")
                if long_order and short_order:
                    range_data = long_order.range_data
                    atr_data = long_order.atr_data
                    # Calculate zone midpoints for display
                    upper_zone_midpoint = (atr_data.day_bull_price + atr_data.day_bull_price1) / 2.0
                    lower_zone_midpoint = (atr_data.day_bear_price + atr_data.day_bear_price1) / 2.0
                    
                    print(f"\n{symbol}:")
                    print(f"  📊 Range: High={range_data.high:.2f}, Low={range_data.low:.2f}, Size={range_data.range_size:.2f} pts")
                    print(f"  📈 ATR: Current={atr_data.current_atr:.2f}, Daily={atr_data.daily_atr:.2f}")
                    print(f"  🎯 Market Open Price: {atr_data.market_open_price:.2f}")
                    print(f"  📍 Daily ATR Zones:")
                    print(f"     🟢 Upper Zone: [{atr_data.day_bull_price:.2f}, {atr_data.day_bull_price1:.2f}] (midpoint: {upper_zone_midpoint:.2f})")
                    print(f"     🔴 Lower Zone: [{atr_data.day_bear_price1:.2f}, {atr_data.day_bear_price:.2f}] (midpoint: {lower_zone_midpoint:.2f})")
                    print(f"  🟢 LONG:  Entry={long_order.entry_price:.2f}, SL={long_order.stop_loss:.2f}, TP={long_order.take_profit:.2f}")
                    print(f"           Risk={long_order.entry_price - long_order.stop_loss:.2f} pts, Reward={long_order.take_profit - long_order.entry_price:.2f} pts")
                    print(f"  🔴 SHORT: Entry={short_order.entry_price:.2f}, SL={short_order.stop_loss:.2f}, TP={short_order.take_profit:.2f}")
                    print(f"           Risk={short_order.stop_loss - short_order.entry_price:.2f} pts, Reward={short_order.entry_price - short_order.take_profit:.2f} pts")
                else:
                    print(f"\n{symbol}: ⚠️  Breakout levels not calculated")
            else:
                print(f"\n{symbol}: ⚠️  No breakout levels available")
        
        print("\n" + "="*80 + "\n")
        
        logger.info("🚀 Overnight Range Strategy started!")
        logger.info(f"   Symbols: {', '.join(trade_symbols)}")
        logger.info(f"   Overnight: {self.overnight_start} - {self.overnight_end} {self.timezone}")
        logger.info(f"   Mode: CONTINUOUS (monitors price and places orders when within threshold)")
        logger.info(f"   Breakout Threshold: {self.breakout_proximity_percent}% (min {self.breakout_min_proximity_points} pts)")
        logger.info(f"   Monitor Interval: {self.breakout_monitor_interval}s")
    
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
        if self._plain_stop_monitor_task:
            self._plain_stop_monitor_task.cancel()
            self._plain_stop_monitor_task = None
        if self._breakout_monitor_task:
            self._breakout_monitor_task.cancel()
            self._breakout_monitor_task = None

        self.breakout_levels.clear()
        self.breakout_active_orders.clear()

        logger.info("🛑 Overnight Range Strategy stopped!")
    
    def get_status(self) -> Dict:
        """Get current strategy status."""
        # Get base status from parent class
        base_status = super().get_status()
        
        # Add overnight-specific status
        base_status.update({
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
        })
        return base_status

