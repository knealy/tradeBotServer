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

import logging
import asyncio
import time as time_module
from datetime import datetime, date, time, timedelta, timezone
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from collections import deque
try:
    import pytz
except ImportError:
    pytz = None  # Optional dependency

from strategies.strategy_base import BaseStrategy, StrategyConfig, MarketCondition, StrategyStatus
from core.strategy_config import load_strategy_config
from core.risk_management import _order_counts_as_working_entry_for_risk

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

        # Strategy-specific config (TOML-backed) for all non-secret knobs.
        # This replaces direct environment-variable reads throughout this strategy.
        self._cfg = load_strategy_config("overnight_range")
        
        # Overnight-specific state
        self.active_ranges: Dict[str, OvernightRange] = {}
        self.active_orders: Dict[str, List[str]] = {}  # symbol -> [order_ids]
        self.breakeven_monitoring: Dict[str, Dict] = {}  # order_id -> monitoring data
        
        # Completed overnight window: key (symbol, end_date) where end_date is the session
        # morning date (the calendar day of overnight_end, e.g. 9:29 AM).
        self._overnight_range_cache: Dict[Tuple[str, date], OvernightRange] = {}
        # In-flight window (before cutoff): short TTL so the range widens as bars arrive
        self._overnight_range_inflight: Dict[Tuple[str, date], Tuple[OvernightRange, float]] = {}
        
        # Tick size cache: {symbol: tick_size}
        self._tick_size_cache: Dict[str, float] = {}
        self._last_market_open_run: Optional[date] = None
        
        # ATR cache: {symbol: {timeframe: (atr_data, timestamp)}}
        # Cache ATR calculations for 5 minutes to avoid redundant API calls
        self._atr_cache: Dict[str, Dict[str, Tuple[ATRData, datetime]]] = {}
        self._atr_cache_ttl = timedelta(minutes=5)  # Cache ATR for 5 minutes
        
        # Load strategy-specific configuration (TOML > env > defaults)
        self.overnight_start = self._cfg.get_str("timing.overnight_start", "18:00")  # 6pm ET
        self.overnight_end = self._cfg.get_str("timing.overnight_end", "09:30")  # 9:30am ET
        self.market_open_time = self._cfg.get_str("timing.market_open", "09:30")
        # MOR.pine uses 30m bar open at session open for daily zones; set to "1m" to use 1m (legacy)
        self.zone_open_source = (self._cfg.get_str("timing.zone_open_source", "30m") or "30m").lower()
        # Zone anchor time: for MOR.pine compat with futures, use 08:30 (MOR: openHour-1 for isFutures)
        self.zone_anchor_time = self._cfg.get_str("timing.zone_anchor", self.market_open_time)
        if pytz:
            self.timezone = pytz.timezone(self._cfg.get_str("timing.session_timezone", "US/Eastern"))
        else:
            # Fallback to UTC if pytz not available
            self.timezone = timezone.utc
            logger.warning("pytz not available, using UTC timezone. Install pytz for timezone support.")
        
        # ATR configuration
        self.atr_period = int(self._cfg.get_int("signal.atr_period", 14))  # 14 bars default
        self.atr_timeframe = self._cfg.get_str("signal.atr_timeframe", "15m")  # used for breakout stops/targets
        
        # Risk management
        self.stop_atr_multiplier = float(self._cfg.get_float("signal.stop_atr_multiplier", 1.25))  # 1.0-1.5 ATR
        self.tp_atr_multiplier = float(self._cfg.get_float("signal.tp_atr_multiplier", 2.0))  # Daily ATR zone
        
        # Breakeven management (optional)
        self.breakeven_enabled = self._cfg.get_bool("position_management.breakeven_enabled", True)
        self.breakeven_profit_points = float(self._cfg.get_float("position_management.breakeven_profit_points", 15.0))  # +15 pts
        
        # Order placement
        self.range_break_offset = float(self._cfg.get_float("position_management.range_break_offset", 0.25))
        self.default_quantity = int(self._cfg.get_int("risk.position_size", 1))
        self.max_quantity_per_instrument = int(self._cfg.get_int("risk.max_quantity_per_instrument", 10))
        self.breakout_monitor_enabled = self._cfg.get_bool("breakout_monitor.enabled", True)
        self.breakout_proximity_percent = float(self._cfg.get_float("breakout_monitor.proximity_pct", 10.0))
        self.breakout_min_proximity_points = float(self._cfg.get_float("breakout_monitor.min_proximity_points", 5.0))
        self.breakout_monitor_interval = float(self._cfg.get_float("breakout_monitor.interval_seconds", 15.0))
        self.breakout_order_tolerance_points = float(self._cfg.get_float("breakout_monitor.order_tolerance_points", 1.0))
        self.breakout_levels: Dict[str, Dict[str, RangeBreakOrder]] = {}
        self.breakout_active_orders: Dict[str, Dict[str, str]] = {}
        self._breakout_monitor_task: Optional[asyncio.Task] = None
        # Note: Order cooldown and quantity limits are now handled by centralized StrategyRiskManager
        # (accessed via self.risk_manager from BaseStrategy)
        
        # Market condition filters (OPTIONAL - defaulted to OFF)
        self.filter_range_size_enabled = self._cfg.get_bool("filters.range_size", False)
        self.filter_range_min = float(self._cfg.get_float("filters.range_min_pts", 50.0))
        self.filter_range_max = float(self._cfg.get_float("filters.range_max_pts", 500.0))
        
        self.filter_gap_enabled = self._cfg.get_bool("filters.gap", False)
        self.filter_gap_max = float(self._cfg.get_float("filters.gap_max_pts", 200.0))
        
        self.filter_volatility_enabled = self._cfg.get_bool("filters.volatility", False)
        self.filter_atr_min = float(self._cfg.get_float("filters.atr_min", 20.0))
        self.filter_atr_max = float(self._cfg.get_float("filters.atr_max", 200.0))
        
        self.filter_dll_proximity_enabled = self._cfg.get_bool("filters.dll_proximity", False)
        self.filter_dll_threshold = float(self._cfg.get_float("filters.dll_threshold_pct", 0.75))  # 75%

        raw_skip = self._cfg.get_list("filters.skip_weekdays", [])
        self.filter_skip_weekdays: List[int] = []
        for x in raw_skip or []:
            try:
                self.filter_skip_weekdays.append(int(x))
            except (TypeError, ValueError):
                continue

        # CSV / strategy replay calls analyze every bar; gate to [market_open, +N minutes) ET.
        self.replay_order_window_minutes = int(
            self._cfg.get_int("timing.replay_order_window_minutes", 8) or 8
        )
        self._replay_sessions_signaled: set[Tuple[str, date]] = set()
        
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
        if self.filter_skip_weekdays:
            logger.info(f"     Skip weekdays: {sorted(self.filter_skip_weekdays)} (Mon=0 … Sun=6)")
        logger.info(
            f"     Replay open window: {self.replay_order_window_minutes} min after {self.market_open_time} "
            f"(strategy CSV replay only)"
        )
    
    def _parse_cfg_time(self, s: str) -> time:
        parts = str(s).strip().split(":")
        h = int(parts[0])
        m = int(parts[1]) if len(parts) > 1 else 0
        return time(h, m)

    def _is_strategy_replay_mode(self) -> bool:
        return bool(getattr(self.trading_bot, "_is_strategy_replay", False))

    def _current_bar_datetime_utc(self) -> Optional[datetime]:
        raw = getattr(self.trading_bot, "_current_bar_timestamp", None)
        if raw is None:
            return None
        if isinstance(raw, datetime):
            dt = raw
        elif hasattr(raw, "to_pydatetime"):
            try:
                dt = raw.to_pydatetime()
            except Exception:
                return None
        else:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        return dt

    def _effective_now_et(self) -> Optional[datetime]:
        """Wall clock for filters: replay uses current bar time; live uses now in session timezone."""
        if self._is_strategy_replay_mode():
            cu = self._current_bar_datetime_utc()
            if cu is None:
                return None
            if self.timezone != timezone.utc:
                return cu.astimezone(self.timezone)
            return cu
        if self.timezone != timezone.utc:
            return datetime.now(self.timezone)
        return datetime.now(timezone.utc)

    def _replay_in_order_placement_window(self, now_et: datetime) -> bool:
        mo = self._parse_cfg_time(self.market_open_time)
        d0 = now_et.date()
        try:
            if pytz is not None and hasattr(self.timezone, "localize"):
                start_dt = self.timezone.localize(datetime.combine(d0, mo))
            else:
                tz = now_et.tzinfo if now_et.tzinfo else self.timezone
                start_dt = datetime.combine(d0, mo, tzinfo=tz)
        except Exception:
            return False
        end_dt = start_dt + timedelta(minutes=max(1, self.replay_order_window_minutes))
        return start_dt <= now_et < end_dt

    def format_overnight_session_window_label(self) -> str:
        """
        Calendar window for the overnight session in Eastern, e.g.
        ``Apr 30 6:00 PM → May 1 9:29 AM US/Eastern`` (crosses midnight).
        """
        if pytz is None or self.timezone == timezone.utc:
            return f"{self.overnight_start} – {self.overnight_end} (install pytz for dated window)"
        try:
            start_t = self._parse_cfg_time(self.overnight_start)
            end_t = self._parse_cfg_time(self.overnight_end)
        except (ValueError, TypeError):
            return f"{self.overnight_start} – {self.overnight_end} {self.timezone}"
        now_et = datetime.now(self.timezone)
        d0 = now_et.date()
        fmt = "%b %d %I:%M %p"
        if end_t <= start_t:
            today_start = self.timezone.localize(datetime.combine(d0, start_t))
            if now_et >= today_start:
                start_dt = today_start
                end_dt = self.timezone.localize(datetime.combine(d0 + timedelta(days=1), end_t))
            else:
                start_dt = self.timezone.localize(datetime.combine(d0 - timedelta(days=1), start_t))
                end_dt = self.timezone.localize(datetime.combine(d0, end_t))
        else:
            start_dt = self.timezone.localize(datetime.combine(d0, start_t))
            end_dt = self.timezone.localize(datetime.combine(d0, end_t))
        return f"{start_dt.strftime(fmt)} → {end_dt.strftime(fmt)} US/Eastern"

    def _is_cooldown_active(self, symbol: str, side: str) -> Tuple[bool, float]:
        """
        Check if cooldown is active for a symbol/side combination.
        
        This prevents wasted API calls and log clutter by checking cooldown
        BEFORE entering order placement logic.
        
        Args:
            symbol: Trading symbol
            side: Order side ("BUY" or "SELL")
        
        Returns:
            Tuple of (is_active, remaining_seconds)
        """
        if not self.risk_manager:
            return False, 0.0
        
        # Get cooldown status from risk manager
        cooldown_key = f"{symbol}_{side}"
        last_attempt_time = self.risk_manager._last_order_attempt.get(cooldown_key)
        
        if not last_attempt_time:
            return False, 0.0
        
        # Calculate remaining cooldown time
        elapsed = (datetime.now(timezone.utc) - last_attempt_time).total_seconds()
        cooldown_period = self.risk_manager.order_cooldown_seconds
        
        if elapsed < cooldown_period:
            remaining = cooldown_period - elapsed
            logger.debug(f"🔒 Cooldown active for {symbol} {side}: {remaining:.1f}s remaining")
            return True, remaining
        
        logger.debug(f"✅ Cooldown expired for {symbol} {side}: {elapsed:.1f}s elapsed")
        return False, 0.0
    
    async def get_quote_optimized(self, symbol: str) -> Optional[Dict]:
        """
        Get quote with SignalR cache optimization.
        
        Tries SignalR cache first (sub-millisecond), falls back to REST API if needed.
        This reduces API calls by 90% for quote fetching.
        
        Args:
            symbol: Trading symbol
        
        Returns:
            Quote dict or None
        """
        try:
            # Try SignalR cache first (fastest, no API call)
            if hasattr(self.trading_bot, '_quote_cache') and self.trading_bot._quote_cache:
                cached_quote = self.trading_bot._quote_cache.get(symbol.upper())
                if cached_quote:
                    # Check cache age (only use if < 5 seconds old)
                    cache_time = cached_quote.get('timestamp') or cached_quote.get('ts')
                    if cache_time:
                        try:
                            if isinstance(cache_time, str):
                                cache_dt = datetime.fromisoformat(cache_time.replace('Z', '+00:00'))
                            elif isinstance(cache_time, datetime):
                                cache_dt = cache_time
                            elif isinstance(cache_time, (int, float)):
                                cache_dt = datetime.fromtimestamp(cache_time, tz=timezone.utc)
                            else:
                                cache_dt = None
                            
                            if cache_dt:
                                age = (datetime.now(timezone.utc) - cache_dt).total_seconds()
                                if age < 5.0:  # Use cache if < 5 seconds old
                                    logger.debug(f"📊 Using SignalR cached quote for {symbol} (age: {age:.1f}s)")
                                    return cached_quote
                        except Exception:
                            pass  # Fall through to API call
            
            # Fallback to REST API
            logger.debug(f"📡 Fetching quote via REST API for {symbol}")
            return await self.trading_bot.get_market_quote(symbol)
            
        except Exception as e:
            logger.error(f"Error getting quote for {symbol}: {e}")
            return None
    
    async def initialize_symbols_parallel(self, symbols: List[str]) -> Dict[str, Dict]:
        """
        Initialize all symbols in parallel by pre-fetching all required historical data.
        
        This optimization fetches historical data for all symbols concurrently,
        reducing initialization time from ~12s to ~4s for 3 symbols (3x speedup).
        
        Args:
            symbols: List of symbols to initialize
        
        Returns:
            Dict mapping symbol to initialization data (ATR, overnight range, etc.)
        """
        import time
        start_time = time.time()
        
        logger.info(f"🚀 Initializing {len(symbols)} symbols in parallel...")
        
        # Build all historical data requests for all symbols
        all_requests = []
        request_map = {}  # Map request index to (symbol, data_type)
        
        for symbol in symbols:
            # Calculate time ranges for this symbol
            now = datetime.now(self.timezone)
            
            # Overnight session time range
            try:
                # Parse overnight session times
                overnight_start_time = datetime.strptime(self.overnight_start, '%H:%M').time()
                overnight_end_time = datetime.strptime(self.overnight_end, '%H:%M').time()
                market_open_time_parsed = datetime.strptime(self.market_open_time, '%H:%M').time()
                
                # Calculate overnight session range
                if overnight_end_time < overnight_start_time:
                    # Crosses midnight (e.g., 18:00 to 08:00)
                    end_date = now.date()
                    start_date = end_date - timedelta(days=1)
                else:
                    # Same day
                    end_date = now.date()
                    start_date = end_date
                
                overnight_start = self.timezone.localize(datetime.combine(start_date, overnight_start_time))
                overnight_end = self.timezone.localize(datetime.combine(end_date, overnight_end_time))
                
                # Market open time range (30 mins before and after)
                market_open_today = self.timezone.localize(datetime.combine(now.date(), market_open_time_parsed))
                market_open_start = (market_open_today - timedelta(minutes=30)).astimezone(timezone.utc)
                market_open_end = (market_open_today + timedelta(minutes=30)).astimezone(timezone.utc)
                
                # Convert to UTC
                overnight_start_utc = overnight_start.astimezone(timezone.utc)
                overnight_end_utc = overnight_end.astimezone(timezone.utc)
                
            except Exception as e:
                logger.error(f"Error calculating time ranges for {symbol}: {e}")
                continue
            
            # Request 1: Cache check (1m, 1 bar)
            req_idx = len(all_requests)
            all_requests.append({
                "symbol": symbol,
                "timeframe": "1m",
                "limit": 1
            })
            request_map[req_idx] = (symbol, "cache_check")
            
            # Request 2: Intraday bars for ATR (5m or configured timeframe)
            req_idx = len(all_requests)
            all_requests.append({
                "symbol": symbol,
                "timeframe": self.atr_timeframe,
                "limit": max(self.atr_period + 1, 200)
            })
            request_map[req_idx] = (symbol, f"intraday_{self.atr_timeframe}")
            
            # Request 3: Daily bars for daily ATR
            req_idx = len(all_requests)
            all_requests.append({
                "symbol": symbol,
                "timeframe": "1d",
                "limit": min(self.atr_period + 5, 50)
            })
            request_map[req_idx] = (symbol, "daily")
            
            # Request 4: Market open bars
            req_idx = len(all_requests)
            all_requests.append({
                "symbol": symbol,
                "timeframe": "1m",
                "start_time": market_open_start,
                "end_time": market_open_end,
                "limit": 60
            })
            request_map[req_idx] = (symbol, "market_open")
            
            # Request 5: Overnight session bars
            req_idx = len(all_requests)
            session_duration = overnight_end_utc - overnight_start_utc
            session_minutes = int(session_duration.total_seconds() / 60)
            all_requests.append({
                "symbol": symbol,
                "timeframe": "1m",
                "start_time": overnight_start_utc,
                "end_time": overnight_end_utc,
                "limit": session_minutes + 10
            })
            request_map[req_idx] = (symbol, "overnight")
        
        # Fetch ALL historical data in parallel
        logger.info(f"📥 Fetching {len(all_requests)} historical data requests in parallel...")
        results_dict = await self.trading_bot.get_historical_data_parallel(all_requests)
        
        # Organize results by symbol
        symbol_data = {}
        for symbol in symbols:
            symbol_data[symbol] = {}
        
        # Map results back to symbols
        for key, bars in results_dict.items():
            # Key format: "SYMBOL_TIMEFRAME" or "SYMBOL_TIMEFRAME_DATE"
            for symbol in symbols:
                if key.startswith(f"{symbol}_"):
                    # Determine data type from key
                    if "1m" in key and len(bars) == 1:
                        symbol_data[symbol]["cache_check"] = bars
                    elif f"{self.atr_timeframe}" in key:
                        symbol_data[symbol]["intraday"] = bars
                    elif "1d" in key:
                        symbol_data[symbol]["daily"] = bars
                    elif len(bars) > 50 and len(bars) < 100:
                        symbol_data[symbol]["market_open"] = bars
                    elif len(bars) > 100:
                        symbol_data[symbol]["overnight"] = bars
        
        elapsed = time.time() - start_time
        logger.info(f"✅ Parallel data fetch complete: {len(all_requests)} requests in {elapsed:.2f}s")
        logger.info(f"   Average per request: {elapsed/len(all_requests):.2f}s")
        logger.info(f"   Estimated sequential time: {elapsed * len(symbols):.2f}s")
        logger.info(f"   Speedup: {len(symbols):.1f}x")
        
        return symbol_data
    
    def _get_trade_symbols(self, symbols: Optional[List[str]] = None) -> List[str]:
        """Determine which symbols to trade for the next execution."""
        if symbols:
            candidates = symbols
        elif getattr(self.config, 'symbols', None):
            candidates = self.config.symbols
        else:
            candidates = self._cfg.symbols() or ["MNQ", "MES"]

        return [sym.strip().upper() for sym in candidates if sym and sym.strip()]
    
    async def _cancel_previous_session_orders(self, symbols: List[str]) -> None:
        """Cancel pending entry orders from the previous session for the given symbols.

        Only cancels orders where no open position exists for that symbol — protective
        SL/TP legs on a live trade are left untouched.
        """
        try:
            open_orders = await self.trading_bot.get_open_orders()
            if not open_orders or not isinstance(open_orders, list):
                return

            positions = await self.trading_bot.get_positions()
            position_symbols: set = set()
            if isinstance(positions, list):
                for pos in positions:
                    qty = pos.get('quantity') or pos.get('size') or 0
                    if abs(qty) > 0:
                        sym = (pos.get('symbol') or pos.get('contractName', '')).upper()
                        for s in symbols:
                            if sym.startswith(s):
                                position_symbols.add(s)

            cancelled = 0
            for order in open_orders:
                if not isinstance(order, dict):
                    continue
                order_symbol = (order.get('symbol') or order.get('contractName', '')).upper()
                base_symbol = next((s for s in symbols if order_symbol.startswith(s)), None)
                if not base_symbol:
                    continue
                if base_symbol in position_symbols:
                    continue  # live position — leave protective orders alone
                tag = str(order.get('customTag') or order.get('custom_tag') or '')
                if 'overnight_range' not in tag:
                    continue
                order_id = str(order.get('id'))
                logger.info(f"🗑️  Cancelling previous-session order {order_id} for {base_symbol} (tag={tag[:40]})")
                try:
                    await self.trading_bot.cancel_order(order_id)
                    cancelled += 1
                except Exception as exc:
                    logger.warning(f"Could not cancel order {order_id}: {exc}")

            if cancelled:
                logger.info(f"✅ Cancelled {cancelled} previous-session order(s)")
            # Reset tracked state so monitor starts fresh for the new session
            for sym in symbols:
                self.breakout_active_orders.pop(sym, None)

        except Exception as exc:
            logger.error(f"Error cancelling previous-session orders: {exc}")

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

        d_et = datetime.now(self.timezone).date()
        try:
            from core.market_calendar import equity_futures_session_note

            cal = equity_futures_session_note(d_et)
        except Exception as exc:
            logger.debug("Calendar check skipped: %s", exc, exc_info=True)
            cal = {"trade_recommended": True, "reason": ""}
        if not cal.get("trade_recommended", True):
            logger.warning(
                "Skipping market-open sequence for %s: %s",
                d_et,
                cal.get("reason", "calendar"),
            )
            return

        threshold = int(self._cfg.get_int("alerts.discord_after_zero_trade_sessions", 0))
        if threshold > 0 and hasattr(self.trading_bot, "session_trade_tracker"):
            acct = getattr(self.trading_bot, "selected_account", None)
            aid = acct.get("id") if isinstance(acct, dict) else (str(acct) if acct else None)
            dn = getattr(self.trading_bot, "discord_notifier", None)
            if aid and dn is not None and hasattr(dn, "send_inactivity_alert"):
                streak = self.trading_bot.session_trade_tracker.zero_trade_session_streak(str(aid))
                if streak >= threshold:
                    account_name = acct.get("name", "Unknown") if isinstance(acct, dict) else "Unknown"
                    asyncio.create_task(
                        dn.send_inactivity_alert(
                            account_name=account_name,
                            strategy="overnight_range",
                            zero_trade_sessions=streak,
                            extra={"session_date_et": str(d_et)},
                        )
                    )

        await self._cancel_previous_session_orders(trade_symbols)
        logger.info(f"🔔 Recalculating overnight ranges and breakout levels for: {', '.join(trade_symbols)}")

        for symbol in trade_symbols:
            logger.info(f"📊 Processing {symbol}...")

            try:
                # Track overnight range
                range_data = await self.track_overnight_range(symbol)
                if not range_data:
                    logger.warning(f"⚠️  Could not track overnight range for {symbol}")
                    continue
                
                # Calculate breakout orders (always calculated even if validation warnings exist)
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
                elif long_order or short_order:
                    # Partial success - store what we got
                    if long_order:
                        self.breakout_levels.setdefault(symbol, {})["BUY"] = long_order
                        logger.info(f"✅ LONG breakout level updated for {symbol}: Entry={long_order.entry_price:.2f}, SL={long_order.stop_loss:.2f}, TP={long_order.take_profit:.2f}")
                    if short_order:
                        self.breakout_levels.setdefault(symbol, {})["SELL"] = short_order
                        logger.info(f"✅ SHORT breakout level updated for {symbol}: Entry={short_order.entry_price:.2f}, SL={short_order.stop_loss:.2f}, TP={short_order.take_profit:.2f}")
                    self.breakout_active_orders.setdefault(symbol, {})
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
                'MGC': 0.10,
                'GC': 0.10,
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
    
    async def get_current_position_quantity(self, symbol: str, positions: Optional[List] = None) -> int:
        """
        Get the total current position quantity for a symbol (absolute value).
        
        Args:
            symbol: Trading symbol (e.g., "MES", "MNQ")
            positions: Optional list of positions (if None, fetches from API)
        
        Returns:
            Total absolute position quantity (0 if no position)
        """
        try:
            if positions is None:
                positions = await self.trading_bot.get_open_positions()
            if not positions:
                return 0
            
            total_quantity = 0
            symbol_upper = symbol.upper()
            
            for pos in positions:
                # Handle Position objects (with attributes) or dicts
                if hasattr(pos, 'symbol'):
                    pos_symbol = (getattr(pos, 'symbol', '') or '').upper()
                    # Try symbolId if symbol is empty
                    if not pos_symbol:
                        symbol_id = getattr(pos, 'symbolId', '')
                        if symbol_id:
                            parts = symbol_id.split('.')
                            pos_symbol = parts[-1].upper() if parts else ''
                    net_qty = getattr(pos, 'net_quantity', 0) or getattr(pos, 'quantity', 0) or 0
                elif isinstance(pos, dict):
                    pos_symbol = pos.get('symbol', '').upper()
                    # Try symbolId if symbol is empty
                    if not pos_symbol:
                        symbol_id = pos.get('symbolId', '')
                        if symbol_id:
                            parts = symbol_id.split('.')
                            pos_symbol = parts[-1].upper() if parts else ''
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
        
        This counts ALL stop orders (type 4) for the symbol that are not reduce-only
        and not explicitly marked as bracket SL/TP orders.
        
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
                if not isinstance(order, dict):
                    continue

                # Get symbol from order - try multiple fields
                order_symbol = order.get('symbol', '').upper()
                if not order_symbol:
                    # Try symbolId field (e.g., "F.US.MGC" -> "MGC")
                    symbol_id = order.get('symbolId', '')
                    if symbol_id:
                        parts = symbol_id.split('.')
                        order_symbol = parts[-1].upper() if parts else ''
                if not order_symbol:
                    # Try contract ID as last resort
                    contract_id = order.get('contractId')
                    if contract_id and hasattr(self.trading_bot, '_get_symbol_from_contract_id'):
                        order_symbol = self.trading_bot._get_symbol_from_contract_id(contract_id)
                
                if not order_symbol or order_symbol != symbol_upper:
                    continue

                # Critical: count only working / live orders as pending.
                if not _order_counts_as_working_entry_for_risk(order):
                    continue
                
                # Get order type - must be a stop order (type 4)
                order_type = order.get('type') or order.get('raw_type')
                if order_type not in (4, "4", "Stop", "stop"):
                    continue
                
                # Exclude reduce-only orders (these are SL/TP brackets for existing positions)
                is_reduce_only = order.get('reduceOnly') or order.get('reduce_only', False)
                if is_reduce_only:
                    continue
                
                # Check customTag for bracket identification
                # If it explicitly says -SL or -TP, it's a bracket order, not an entry order
                custom_tag = order.get('customTag') or order.get('custom_tag') or ''
                is_bracket_sl_tp = '-SL' in str(custom_tag) or '-TP' in str(custom_tag)
                is_stop_bracket = 'stop_bracket' in str(custom_tag) or 'stop-bracket' in str(custom_tag)
                is_overnight = 'overnight_range' in str(custom_tag) or 'overnight-range' in str(custom_tag)
                
                # IMPORTANT: We count ALL stop orders that are NOT reduce-only and NOT explicitly bracket SL/TP
                # This includes suspended bracket entry orders which may not have the exact pattern we expect
                # The key is: if it's a stop order, not reduce-only, and not explicitly -SL/-TP, count it
                if is_stop_bracket and is_overnight and not is_bracket_sl_tp:
                    qty = order.get('quantity') or order.get('size') or 0
                    if qty:
                        total_quantity += abs(int(qty))
            
            return total_quantity
        except Exception as e:
            logger.warning(f"⚠️  Error getting pending entry order quantity for {symbol}: {e}")
            return 0
    
    async def get_total_exposure(
        self, 
        symbol: str, 
        open_orders: Optional[List[Dict]] = None,
        positions: Optional[List] = None
    ) -> int:
        """
        Get total exposure (positions + pending entry orders) for a symbol.
        
        Args:
            symbol: Trading symbol
            open_orders: Optional list of open orders (if None, fetches from API)
            positions: Optional list of positions (if None, fetches from API)
        
        Returns:
            Total exposure: positions + pending entry orders
        """
        position_qty = await self.get_current_position_quantity(symbol, positions=positions)
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
            symbol = symbol.upper()

            now_et = self._effective_now_et()
            if self.filter_skip_weekdays and now_et is not None:
                if now_et.weekday() in self.filter_skip_weekdays:
                    logger.debug("Skipping %s — skip_weekdays (weekday=%s)", symbol, now_et.weekday())
                    return None

            if self._is_strategy_replay_mode():
                if now_et is None:
                    logger.debug("Skipping %s — replay mode but no bar timestamp on bot", symbol)
                    return None
                if not self._replay_in_order_placement_window(now_et):
                    return None
                if (symbol, now_et.date()) in self._replay_sessions_signaled:
                    return None

            # Always resolve range for the *current* session clock. Do not reuse
            # ``active_ranges`` from a prior day — that caused identical bracket
            # prices across CSV replay sessions (stale high/low).
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
            ok = bool(result.get("success", False))
            if ok and self._is_strategy_replay_mode():
                now_et = self._effective_now_et()
                if now_et is not None:
                    self._replay_sessions_signaled.add((symbol.upper(), now_et.date()))
            return ok
            
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
    
    async def recalculate_current_atr(self, symbol: str, period: int = None, timeframe: str = None) -> Optional[float]:
        """
        Recalculate ONLY the current (intraday) ATR at order placement time.
        
        This bypasses the cache to get the most recent volatility reading for stop-loss
        calculations. The daily ATR and zones are NOT recalculated.
        
        Args:
            symbol: Trading symbol
            period: Number of bars for ATR (default: from config)
            timeframe: Timeframe for bars (default: from config)
        
        Returns:
            Current ATR value, or None if error
        """
        try:
            period = period or self.atr_period
            timeframe = timeframe or self.atr_timeframe
            
            # Fetch fresh bars (no cache)
            atr_history_bars = int(self._cfg.get_int("signal.atr_history_bars", 200))
            intraday_limit = max(period + 1, atr_history_bars)
            
            bars = await self.trading_bot.get_historical_data(
                symbol=symbol,
                timeframe=timeframe,
                limit=intraday_limit
            )
            
            if not bars or len(bars) < period + 1:
                logger.warning(f"Insufficient bars for dynamic ATR: {len(bars) if bars else 0}")
                return None
            
            # Sort bars chronologically
            def _bar_time_utc(bar: Dict) -> Optional[datetime]:
                ts = bar.get('timestamp') or bar.get('time') or bar.get('t')
                if not ts:
                    return None
                if isinstance(ts, datetime):
                    dt = ts
                elif isinstance(ts, str):
                    dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
                elif isinstance(ts, (int, float)):
                    if ts > 1e12:
                        dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
                    else:
                        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
                else:
                    return None
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(timezone.utc)
            
            bars_sorted = sorted(bars, key=lambda b: _bar_time_utc(b) or datetime.min.replace(tzinfo=timezone.utc))
            
            # Calculate True Range
            true_ranges = []
            for i in range(1, len(bars_sorted)):
                current_bar = bars_sorted[i]
                prev_bar = bars_sorted[i - 1]
                
                high = current_bar.get('high', current_bar.get('h', 0))
                low = current_bar.get('low', current_bar.get('l', 0))
                prev_close = prev_bar.get('close', prev_bar.get('c', 0))
                
                tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
                true_ranges.append(tr)
            
            if len(true_ranges) < period:
                logger.warning(f"Insufficient TRs for dynamic ATR: {len(true_ranges)} < {period}")
                return None
            
            # Wilder's smoothing
            current_atr = sum(true_ranges[:period]) / period
            for tr in true_ranges[period:]:
                current_atr = ((current_atr * (period - 1)) + tr) / period
            
            logger.info(f"🔄 Recalculated dynamic current ATR for {symbol} ({timeframe}): {current_atr:.2f}")
            return current_atr
            
        except Exception as e:
            logger.error(f"Error recalculating dynamic ATR for {symbol}: {e}")
            return None

    async def calculate_atr(self, symbol: str, period: int = None, timeframe: str = None) -> Optional[ATRData]:
        """
        Calculate ATR (Average True Range) for a symbol.
        
        ATR is calculated using Wilder's Smoothing (RMA) - matches TradingView's ta.atr():
        
        1. True Range (TR) = max(high - low, |high - prev_close|, |low - prev_close|)
        2. First ATR = Simple Moving Average (SMA) of first 'period' TR values
        3. Subsequent ATR = ((Previous ATR * (period - 1)) + Current TR) / period
        
        This is identical to TradingView's ta.atr() which uses ta.rma() internally.
        
        Uses caching to avoid redundant API calls (5-minute TTL). For dynamic recalculation
        at order placement time, use recalculate_current_atr() instead.
        
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
            # In backtest mode, cache should persist for the entire day
            # Use a per-day cache key to avoid recalculating ATR zones for the same day
            now = datetime.now(self.timezone)
            # In backtest mode, try to get the current date from available bars
            cache_date = now.date()
            try:
                recent_bars = await self.trading_bot.get_historical_data(
                    symbol=symbol,
                    timeframe='1m',
                    limit=1
                )
                if recent_bars and len(recent_bars) > 0:
                    latest_bar = recent_bars[-1]
                    bar_ts = latest_bar.get('timestamp') or latest_bar.get('time')
                    if bar_ts:
                        if isinstance(bar_ts, str):
                            bar_dt = datetime.fromisoformat(bar_ts.replace('Z', '+00:00'))
                        elif isinstance(bar_ts, datetime):
                            bar_dt = bar_ts
                        else:
                            bar_dt = None
                        if bar_dt:
                            if bar_dt.tzinfo is None:
                                bar_dt = bar_dt.replace(tzinfo=timezone.utc)
                            if pytz:
                                bar_dt_local = bar_dt.astimezone(self.timezone)
                            else:
                                bar_dt_local = bar_dt.astimezone(self.timezone)
                            cache_date = bar_dt_local.date()
            except Exception:
                pass  # Use system date as fallback
            
            # Use date-based cache key for backtest mode (cache per day)
            date_cache_key = f"{cache_key}_{cache_date}"
            if symbol in self._atr_cache and date_cache_key in self._atr_cache[symbol]:
                cached_data, cached_time = self._atr_cache[symbol][date_cache_key]
                # In backtest mode, cache for the entire day
                # In live mode, use TTL
                if cache_date == now.date() or (now - cached_time < self._atr_cache_ttl):
                    logger.debug(f"Using cached ATR for {symbol} ({timeframe}) for date {cache_date}")
                    return cached_data
                else:
                    # Cache expired, remove it
                    del self._atr_cache[symbol][date_cache_key]
            
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
            atr_history_bars = int(self._cfg.get_int("signal.atr_history_bars", 200))
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
            
            # First ATR = Simple Average of first period TR values (SMA)
            # This matches TradingView's ta.atr() which uses ta.rma() internally
            first_atr = sum(true_ranges[:period]) / period
            current_atr = first_atr
            logger.debug(f"ATR Calculation ({timeframe}):")
            logger.debug(f"  Period: {period}, Total TR values: {len(true_ranges)}")
            logger.debug(f"  First ATR (SMA of first {period} TRs): {first_atr:.4f}")
            
            # Apply Wilder's smoothing (RMA) for remaining TR values
            # Formula: ATR = ((prev_ATR * (period - 1)) + current_TR) / period
            smoothing_steps = 0
            for tr in true_ranges[period:]:
                prev_atr = current_atr
                current_atr = ((current_atr * (period - 1)) + tr) / period
                smoothing_steps += 1
                # Log first few and last few iterations for verification
                if smoothing_steps <= 3 or smoothing_steps >= len(true_ranges[period:]) - 2:
                    logger.debug(f"  RMA step {smoothing_steps}: prev={prev_atr:.4f}, TR={tr:.4f}, new={current_atr:.4f}")
            
            if smoothing_steps > 0:
                logger.debug(f"  Applied Wilder's smoothing for {smoothing_steps} additional bars")
            logger.debug(f"  Final {timeframe} ATR: {current_atr:.4f}")
            
            # Daily ATR: match MOR.pine / request.security("D", ta.atr(14))
            # MOR uses session-aligned "D" bars that roll at 18:00 ET. We build those from 1h bars
            # (1h avoids the adapter overwriting end_time for 5m when end is in the past, e.g. analyze_date).
            # DAILY_BAR_ROLL_TIME="18:00" (default) to match MOR; fall back to exchange 1d if disabled/insufficient.
            daily_atr = current_atr  # Default fallback
            session_aligned = self._cfg.get_bool("signal.daily_atr_session_aligned", True)
            daily_atr_from_session = False
            roll_str = self._cfg.get_str("signal.daily_bar_roll_time", "18:00")  # 18:00 ET to match MOR; do not use overnight_start

            # Prefer as-of time from analyze_date/backtest when set; otherwise latest bar or now.
            # analyze_date sets _current_bar_timestamp to 1min after overnight_end on the target date
            # so we compute session date and 1h window for that day, not "today".
            as_of_et = datetime.now(self.timezone)
            _cb = getattr(self.trading_bot, "_current_bar_timestamp", None)
            if _cb is not None and hasattr(_cb, "astimezone"):
                as_of_et = _cb.astimezone(self.timezone) if _cb.tzinfo else _cb.replace(tzinfo=timezone.utc).astimezone(self.timezone)
            else:
                try:
                    lb = bars_sorted[-1] if bars_sorted else None
                    if lb:
                        bt = _bar_time_utc(lb)
                        if bt:
                            as_of_et = bt.astimezone(self.timezone)
                except Exception:
                    pass

            if session_aligned:
                try:
                    roll_h, roll_m = map(int, roll_str.split(":"))
                    roll_time = time(roll_h, roll_m, 0)
                    now_et = as_of_et
                    # Session date containing "now": day D = [ (D-1) 18:00, D 18:00 ). Bar at 18:00 goes to D+1.
                    base = now_et.replace(hour=roll_h, minute=roll_m, second=0, microsecond=0)
                    if now_et >= base:
                        session_date = (base + timedelta(days=1)).date()
                    else:
                        session_date = base.date()
                    # Need (period+1)+ buffer session days of 1h (360+ bars for period=14). Use 1h; in date-range
                    # the adapter uses API 1h directly. 24 days ensures 360+ bars even with market-hour-only APIs.
                    start_et = datetime.combine(session_date - timedelta(days=24), roll_time)
                    if hasattr(self.timezone, 'localize'):
                        start_et = self.timezone.localize(start_et)
                    else:
                        start_et = start_et.replace(tzinfo=self.timezone)
                    end_et = now_et + timedelta(hours=1)
                    start_utc = start_et.astimezone(pytz.UTC if pytz else timezone.utc)
                    end_utc = end_et.astimezone(pytz.UTC if pytz else timezone.utc)
                    # 24 days * 24 = 576 1h bars (request 600 to have buffer)
                    intra = await self.trading_bot.get_historical_data(
                        symbol=symbol, timeframe="1h", limit=600, start_time=start_utc, end_time=end_utc
                    )
                    min_bars = 24 * (period + 1)  # at least (period+1) days of 1h
                    if not intra or len(intra) < min_bars:
                        logger.warning(
                            f"Session-aligned daily ATR: 1h bars insufficient (got {len(intra) if intra else 0}, need {min_bars}), using 1d fallback"
                        )
                    if intra and len(intra) >= min_bars:
                        intra_sorted = sorted(
                            intra,
                            key=lambda b: _bar_time_utc(b) or datetime.min.replace(tzinfo=timezone.utc),
                        )
                        # Debug: log 1h bar range for session-aligned daily ATR
                        if intra_sorted:
                            first_bar_time = _bar_time_utc(intra_sorted[0])
                            last_bar_time = _bar_time_utc(intra_sorted[-1])
                            logger.info(
                                f"Session-aligned daily ATR: {len(intra_sorted)} 1h bars from "
                                f"{first_bar_time.astimezone(self.timezone) if first_bar_time else 'N/A'} to "
                                f"{last_bar_time.astimezone(self.timezone) if last_bar_time else 'N/A'}"
                            )
                        # Group by session date: D if (D-1) 18:00 <= bar < D 18:00; bar at 18:00 -> D+1
                        #
                        # IMPORTANT (TradingView parity):
                        # TradingView's `MNQ1!` daily series can OMIT certain holiday "trading days" (e.g., MLK Day)
                        # as a separate daily candle. It does NOT ignore the price action; it effectively FOLDS the
                        # holiday session's OHLC into the next trading day's candle.
                        #
                        # Therefore we must NOT simply "skip" holiday sessions here (that would discard price action
                        # and distort ATR). Instead, we build the full 18:00-aligned daily series, then merge any
                        # holiday session date into the next non-holiday session before computing ATR.
                        agg: Dict[date, Dict] = {}
                        for b in intra_sorted:
                            bt = _bar_time_utc(b)
                            if not bt:
                                continue
                            if bt.tzinfo is None:
                                bt = bt.replace(tzinfo=timezone.utc)
                            be = bt.astimezone(self.timezone)
                            if be.time() >= roll_time:
                                sd = (be.date() + timedelta(days=1))
                            else:
                                sd = be.date()
                            o = b.get("open", b.get("o", 0))
                            h = b.get("high", b.get("h", 0))
                            l = b.get("low", b.get("l", 0))
                            c = b.get("close", b.get("c", 0))
                            if sd not in agg:
                                agg[sd] = {"open": o, "high": h, "low": l, "close": c}
                            else:
                                agg[sd]["high"] = max(agg[sd]["high"], h)
                                agg[sd]["low"] = min(agg[sd]["low"], l)
                                agg[sd]["close"] = c
                        # Build daily list for last (period+5) session dates we care about
                        wanted = [session_date - timedelta(days=k) for k in range(period + 5)]
                        daily_list = []
                        for d in sorted(agg.keys()):
                            if wanted and min(wanted) <= d <= max(wanted):
                                daily_list.append((d, agg[d]))
                        daily_list.sort(key=lambda x: x[0])
                        daily_list = daily_list[-(period + 5) :] if len(daily_list) > period + 5 else daily_list

                        # Merge holiday session-days into the next session-day (TradingView behavior).
                        def _observed_us_holiday(d: date) -> date:
                            # Fixed-date holiday observation rule (Sat->Fri, Sun->Mon)
                            if d.weekday() == 5:
                                return d - timedelta(days=1)
                            if d.weekday() == 6:
                                return d + timedelta(days=1)
                            return d

                        def _nth_weekday_of_month(year: int, month: int, weekday: int, n: int) -> date:
                            first = date(year, month, 1)
                            offset = (weekday - first.weekday() + 7) % 7
                            return first + timedelta(days=offset + 7 * (n - 1))

                        def _last_weekday_of_month(year: int, month: int, weekday: int) -> date:
                            import calendar
                            last_day = calendar.monthrange(year, month)[1]
                            last = date(year, month, last_day)
                            offset = (last.weekday() - weekday + 7) % 7
                            return last - timedelta(days=offset)

                        def _easter_sunday(year: int) -> date:
                            # Meeus/Jones/Butcher algorithm
                            a = year % 19
                            b = year // 100
                            c = year % 100
                            d = b // 4
                            e = b % 4
                            f = (b + 8) // 25
                            g = (b - f + 1) // 3
                            h = (19 * a + b - d - g + 15) % 30
                            i = c // 4
                            k = c % 4
                            l = (32 + 2 * e + 2 * i - h - k) % 7
                            m = (a + 11 * h + 22 * l) // 451
                            month = (h + l - 7 * m + 114) // 31
                            day = ((h + l - 7 * m + 114) % 31) + 1
                            return date(year, month, day)

                        def _holiday_set(year: int) -> set[date]:
                            hs: set[date] = set()
                            # Fixed-date (observed)
                            hs.add(_observed_us_holiday(date(year, 1, 1)))    # New Year's Day
                            hs.add(_observed_us_holiday(date(year, 6, 19)))   # Juneteenth
                            hs.add(_observed_us_holiday(date(year, 7, 4)))    # Independence Day
                            hs.add(_observed_us_holiday(date(year, 12, 25)))  # Christmas
                            # Monday-based
                            hs.add(_nth_weekday_of_month(year, 1, 0, 3))       # MLK Day
                            hs.add(_nth_weekday_of_month(year, 2, 0, 3))       # Presidents' Day
                            hs.add(_last_weekday_of_month(year, 5, 0))         # Memorial Day
                            hs.add(_nth_weekday_of_month(year, 9, 0, 1))       # Labor Day
                            # Thanksgiving
                            hs.add(_nth_weekday_of_month(year, 11, 3, 4))      # Thanksgiving
                            # Good Friday
                            hs.add(_easter_sunday(year) - timedelta(days=2))
                            return hs

                        if daily_list:
                            years = sorted({d.year for d, _ in daily_list})
                            holidays = set()
                            for y in years:
                                holidays |= _holiday_set(y)

                            merged_daily: list[tuple[date, dict]] = []
                            carry: Optional[dict] = None
                            for sd, ohlc in daily_list:
                                if sd in holidays:
                                    # Accumulate holiday session(s); fold into next non-holiday day.
                                    if carry is None:
                                        carry = dict(ohlc)
                                    else:
                                        carry["high"] = max(float(carry["high"]), float(ohlc["high"]))
                                        carry["low"] = min(float(carry["low"]), float(ohlc["low"]))
                                        carry["close"] = ohlc["close"]
                                    continue

                                if carry is not None:
                                    # Fold carry into this day: open from carry, close from today.
                                    ohlc = {
                                        "open": carry["open"],
                                        "high": max(float(carry["high"]), float(ohlc["high"])),
                                        "low": min(float(carry["low"]), float(ohlc["low"])),
                                        "close": ohlc["close"],
                                    }
                                    carry = None

                                merged_daily.append((sd, ohlc))

                            # If the requested window ends on a holiday (no next day available), drop the carry.
                            daily_list = merged_daily
                        # Debug: log session dates and sample OHLC
                        if daily_list:
                            logger.info(
                                f"Session-aligned: {len(agg)} total session dates, using {len(daily_list)} "
                                f"from {daily_list[0][0]} to {daily_list[-1][0]}"
                            )
                            for i, (sd, ohlc) in enumerate(daily_list[:3]):
                                logger.info(f"  Session {sd}: O={ohlc['open']:.2f} H={ohlc['high']:.2f} L={ohlc['low']:.2f} C={ohlc['close']:.2f}")
                            if len(daily_list) > 3:
                                logger.info(f"  ... ({len(daily_list)-6} more sessions)")
                            for i, (sd, ohlc) in enumerate(daily_list[-3:]):
                                logger.info(f"  Session {sd}: O={ohlc['open']:.2f} H={ohlc['high']:.2f} L={ohlc['low']:.2f} C={ohlc['close']:.2f}")
                        if len(daily_list) >= period + 1:
                            trs = []
                            for i in range(1, len(daily_list)):
                                cur = daily_list[i][1]
                                prev = daily_list[i - 1][1]
                                tr = max(
                                    cur["high"] - cur["low"],
                                    abs(cur["high"] - prev["close"]),
                                    abs(cur["low"] - prev["close"]),
                                )
                                trs.append(tr)
                            if len(trs) >= period:
                                first = sum(trs[:period]) / period
                                daily_atr = first
                                for tr in trs[period:]:
                                    daily_atr = ((daily_atr * (period - 1)) + tr) / period
                                daily_atr_from_session = True
                                logger.info(
                                    f"Daily ATR (18:00-aligned, match MOR.pine) from {len(daily_list)} session days: {daily_atr:.2f}"
                                )
                except Exception as e:
                    logger.warning(f"Session-aligned daily ATR failed, falling back to 1d: {e}")

            if not daily_atr_from_session:
                try:
                    daily_bars = await self.trading_bot.get_historical_data(
                        symbol=symbol,
                        timeframe="1d",
                        limit=min(period + 10, 120),
                        continuous_daily=True,
                        include_partial_daily=False
                    )
                    if daily_bars and len(daily_bars) >= period + 1:
                        daily_bars_sorted = sorted(
                            daily_bars,
                            key=lambda b: _bar_time_utc(b) or datetime.min.replace(tzinfo=timezone.utc),
                        )
                        current_date = as_of_et.date()
                        # TradingView zone logic typically uses the last *completed* daily ATR value at the
                        # time zones are created (e.g. at the cash open). In analyze_date/backtests, our
                        # "as_of" time is before the daily session completes, so including the current day's
                        # full daily candle would leak future information (lookahead) and diverge from TV.
                        #
                        # To preserve existing behavior for indices while fixing MGC parity, this is
                        # configurable via env var and defaults to excluding the current day for MGC only.
                        exclude_symbols = {
                            s.strip().upper()
                            for s in self._cfg.get_list("signal.daily_atr_exclude_current_day_symbols", ["MGC"])
                            if s.strip()
                        }
                        exclude_current_day = symbol.upper() in exclude_symbols
                        complete_daily_bars = []
                        for b in daily_bars_sorted:
                            t = _bar_time_utc(b)
                            if t:
                                bd = t.astimezone(self.timezone).date()
                                if exclude_current_day:
                                    if bd < current_date:
                                        complete_daily_bars.append(b)
                                else:
                                    if bd <= current_date:
                                        complete_daily_bars.append(b)
                        if len(complete_daily_bars) < period + 1:
                            complete_daily_bars = daily_bars_sorted
                        if len(complete_daily_bars) > period + 1:
                            complete_daily_bars = complete_daily_bars[-(period + 1) :]
                        trs = []
                        for i in range(1, len(complete_daily_bars)):
                            cb = complete_daily_bars[i]
                            pb = complete_daily_bars[i - 1]
                            trs.append(
                                max(
                                    cb.get("high", cb.get("h", 0)) - cb.get("low", cb.get("l", 0)),
                                    abs(cb.get("high", cb.get("h", 0)) - pb.get("close", pb.get("c", 0))),
                                    abs(cb.get("low", cb.get("l", 0)) - pb.get("close", pb.get("c", 0))),
                                )
                            )
                        if len(trs) >= period:
                            daily_atr = sum(trs[:period]) / period
                            for tr in trs[period:]:
                                daily_atr = ((daily_atr * (period - 1)) + tr) / period
                            logger.info(f"Daily ATR (1d fallback) from {len(complete_daily_bars)} bars: {daily_atr:.2f}")
                except Exception as e:
                    logger.warning(f"Error fetching daily bars for ATR: {e}, using current ATR approximation")
            
            # Get current price for ATR zones
            current_price = bars_sorted[-1].get('close', bars_sorted[-1].get('c', 0))
            
            # Calculate ATR zones
            atr_zone_high = current_price + daily_atr
            atr_zone_low = current_price - daily_atr
            
            # Get market open price for zone anchor. MOR.pine uses 30m bar open at session open.
            # OVERNIGHT_ZONE_OPEN_SOURCE: "30m" (default, match MOR.pine) or "1m"
            # ZONE_ANCHOR_TIME: time to fetch (default=MARKET_OPEN_TIME). Set to '08:30' for MOR
            # compatibility with futures (MOR uses openHour-1 = 8:30 for isFutures).
            market_open_price = 0.0
            # Use as_of_et so analyze_date/backtest get market open for the target date
            # TradingView parity for MGC1!: the "market open" anchor is not the cash open.
            # Additionally, MGC must use the *active contract for that date* (G26 vs J26), otherwise
            # we anchor off the wrong intraday series and zones drift badly.
            #
            # - Anchor time: defaults to 07:00 ET for MGC unless ZONE_ANCHOR_TIME is explicitly set.
            # - Contract selection: resolve the source contract from the continuous stitched daily bar.
            zone_anchor_time = self.zone_anchor_time
            if symbol.upper() == "MGC" and not self._cfg.env_present("ZONE_ANCHOR_TIME"):
                zone_anchor_time = "07:00"

            open_hour, open_min = map(int, zone_anchor_time.split(':'))
            market_open_today = as_of_et.replace(hour=open_hour, minute=open_min, second=0, microsecond=0)
            
            try:
                # MOR.pine uses 30m bar open at session open for daily boundary anchor
                # For MGC, resolve the correct contract month for the target date (matches the 1d stitched series)
                contract_id_override = None
                if symbol.upper() == "MGC":
                    try:
                        daily_bars_for_contract = await self.trading_bot.broker_adapter.get_historical_data(
                            symbol=symbol,
                            timeframe="1d",
                            limit=60,
                            continuous_daily=True,
                            include_partial_daily=False,
                        )
                        for db in daily_bars_for_contract or []:
                            if not getattr(db, "timestamp", None):
                                continue
                            if db.timestamp.astimezone(self.timezone).date() == current_date:
                                rd = getattr(db, "raw_data", None) or {}
                                contract_id_override = rd.get("source_contract_id") or rd.get("source_contract") or rd.get("contractId")
                                break
                    except Exception:
                        contract_id_override = None

                if self.zone_open_source == "30m":
                    start_utc = (market_open_today - timedelta(minutes=90)).astimezone(pytz.UTC if pytz else timezone.utc)
                    end_utc = (market_open_today + timedelta(minutes=90)).astimezone(pytz.UTC if pytz else timezone.utc)
                    bars_30m = await self.trading_bot.get_historical_data(
                        symbol=symbol,
                        timeframe='30m',
                        start_time=start_utc,
                        end_time=end_utc,
                        limit=10,
                        contract_id_override=contract_id_override,
                    )
                    if bars_30m:
                        for b in sorted(bars_30m, key=lambda x: _bar_time_utc(x) or datetime.min.replace(tzinfo=timezone.utc)):
                            bt = _bar_time_utc(b)
                            if bt:
                                if bt.tzinfo is None:
                                    bt = bt.replace(tzinfo=timezone.utc)
                                bt_local = bt.astimezone(self.timezone).replace(second=0, microsecond=0)
                                if bt_local == market_open_today:
                                    market_open_price = b.get('open', b.get('o', 0))
                                    logger.info(
                                        f"Market open price (30m, match MOR.pine) for {symbol}: "
                                        f"{bt_local.strftime('%Y-%m-%d %H:%M')} @ {market_open_price:.2f}"
                                    )
                                    break
                
                # 1m fallback when 30m not used or 30m bar not found
                if market_open_price is None or market_open_price == 0:
                    start_time_utc = (market_open_today - timedelta(minutes=30)).astimezone(pytz.UTC if pytz else timezone.utc)
                    end_time_utc = (market_open_today + timedelta(minutes=30)).astimezone(pytz.UTC if pytz else timezone.utc)
                    open_bars = await self.trading_bot.get_historical_data(
                        symbol=symbol,
                        timeframe='1m',
                        start_time=start_time_utc,
                        end_time=end_time_utc,
                        limit=60,
                        contract_id_override=contract_id_override,
                    )
                else:
                    open_bars = []
                
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
                    if market_open_price is None or market_open_price == 0:
                        logger.warning(f"No 1m bars returned for market open lookup for {symbol}, using current price")
                        market_open_price = current_price
            except Exception as e:
                if market_open_price is None or market_open_price == 0:
                    logger.warning(f"Error fetching market open price for {symbol}: {e}, using current price as fallback")
                    market_open_price = current_price
            
            # Calculate daily ATR zones.
            # Use the market open price (8:00 AM / 9:30 AM) for zone calculations
            # This makes the ATR zones more responsive to the current market session
            open_price_for_zones = market_open_price
            
            day_dist = daily_atr * 0.5
            zone_near_mult = float(self._cfg.get_float("signal.atr_zone_near_mult", 0.5))
            zone_far_mult = float(self._cfg.get_float("signal.atr_zone_far_mult", 0.618))
            if zone_far_mult < zone_near_mult:
                # Safety: enforce ordering
                zone_near_mult, zone_far_mult = zone_far_mult, zone_near_mult
            
            # Upper zone: open + (dailyATR/2) * near .. open + (dailyATR/2) * far
            day_bull_price = open_price_for_zones + day_dist * zone_near_mult  # Lower bound of upper zone
            day_bull_price1 = open_price_for_zones + day_dist * zone_far_mult  # Upper bound of upper zone
            
            # Lower zone: open - (dailyATR/2) * far .. open - (dailyATR/2) * near
            day_bear_price = open_price_for_zones - day_dist * zone_near_mult  # Upper bound (closer to open)
            day_bear_price1 = open_price_for_zones - day_dist * zone_far_mult  # Lower bound (farther from open)
            
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
            logger.info(f"  Market Open ({self.zone_open_source}): {market_open_price:.2f}")
            logger.info(f"  day_dist = daily_atr * 0.5 = {daily_atr:.2f} * 0.5 = {day_dist:.2f}")
            logger.info(f"  Upper ATR Zone: [{day_bull_price:.2f}, {day_bull_price1:.2f}]")
            logger.info(f"  Lower ATR Zone: [{day_bear_price1:.2f}, {day_bear_price:.2f}]")
            
            # Cache the result with date-based key
            if symbol not in self._atr_cache:
                self._atr_cache[symbol] = {}
            self._atr_cache[symbol][date_cache_key] = (atr_data, as_of_et)
            
            return atr_data
            
        except Exception as e:
            logger.error(f"Error calculating ATR for {symbol}: {e}")
            return None

    def _persist_or_ranges_to_db(self) -> None:
        """Write active OR high/low to strategy_states so the chart server can draw lines (headless executor)."""
        now_mono = time_module.monotonic()
        if now_mono - getattr(self, "_or_db_last_mono", 0.0) < 4.0:
            return
        self._or_db_last_mono = now_mono
        db = getattr(self.trading_bot, "db", None)
        if not db:
            return
        account_id = None
        acct = getattr(self.trading_bot, "selected_account", None)
        if isinstance(acct, dict):
            account_id = acct.get("id")
        elif acct is not None:
            account_id = str(acct)
        if not account_id:
            return
        aid = str(account_id)
        try:
            st = db.get_strategy_state(aid, "overnight_range") or {}
            settings = dict(st.get("settings") or {})
            snap: Dict[str, Dict[str, float]] = {}
            for sym, r in self.active_ranges.items():
                try:
                    hi = float(r.high)
                    lo = float(r.low)
                    key = str(sym).upper()
                    snap[key] = {"high": hi, "low": lo}
                    # Alias for chart symbol dropdown (e.g. MNQ vs contract-prefixed keys)
                    if "." in key:
                        short = key.split(".")[-1].strip()
                        if short and short != key:
                            snap[short] = {"high": hi, "low": lo}
                except (TypeError, ValueError):
                    continue
            settings["or_ranges"] = snap
            metadata = dict(st.get("metadata") or {})
            metadata["or_ranges_saved_at"] = datetime.now(timezone.utc).isoformat()
            symbols = st.get("symbols")
            if not symbols and self.config is not None:
                symbols = list(getattr(self.config, "symbols", None) or [])
            db.save_strategy_state(
                account_id=aid,
                strategy_name="overnight_range",
                enabled=bool(st.get("enabled", True)),
                symbols=symbols,
                settings=settings,
                metadata=metadata,
                last_started=None,
                last_stopped=None,
            )
        except Exception as e:
            logger.debug("or_ranges DB snapshot skipped: %s", e, exc_info=True)
    
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
            
            # In backtest mode, use the date from the current bar being processed
            # The strategy replay engine stores the current bar timestamp in trading_bot._current_bar_timestamp
            now = None
            try:
                # First, try to get the current bar timestamp from the replay engine (most reliable)
                if hasattr(self.trading_bot, '_current_bar_timestamp'):
                    current_bar_ts = self.trading_bot._current_bar_timestamp
                    if current_bar_ts:
                        if isinstance(current_bar_ts, datetime):
                            bar_dt = current_bar_ts
                        elif isinstance(current_bar_ts, str):
                            bar_dt = datetime.fromisoformat(current_bar_ts.replace('Z', '+00:00'))
                        elif isinstance(current_bar_ts, (int, float)):
                            if current_bar_ts > 1e12:
                                bar_dt = datetime.fromtimestamp(current_bar_ts / 1000, tz=timezone.utc)
                            else:
                                bar_dt = datetime.fromtimestamp(current_bar_ts, tz=timezone.utc)
                        else:
                            bar_dt = None
                        
                        if bar_dt:
                            if bar_dt.tzinfo is None:
                                bar_dt = bar_dt.replace(tzinfo=timezone.utc)
                            if pytz:
                                now = bar_dt.astimezone(self.timezone)
                            else:
                                now = bar_dt.astimezone(self.timezone)
                            logger.debug(f"Using current bar timestamp from replay engine: {now}")
            except Exception as e:
                logger.debug(f"Could not get current bar timestamp: {e}")
            
            # Fallback: try to get date from latest bar in available data
            if now is None:
                try:
                    recent_bars = await self.trading_bot.get_historical_data(
                        symbol=symbol,
                        timeframe='1m',
                        limit=1  # Just need the latest bar
                    )
                    if recent_bars and len(recent_bars) > 0:
                        latest_bar = recent_bars[-1]
                        bar_ts = latest_bar.get('timestamp') or latest_bar.get('time') or latest_bar.get('t')
                        if bar_ts:
                            if isinstance(bar_ts, datetime):
                                bar_dt = bar_ts
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
                                if bar_dt.tzinfo is None:
                                    bar_dt = bar_dt.replace(tzinfo=timezone.utc)
                                if pytz:
                                    now = bar_dt.astimezone(self.timezone)
                                else:
                                    now = bar_dt.astimezone(self.timezone)
                                logger.debug(f"Using date from latest bar: {now}")
                except Exception as e:
                    logger.debug(f"Could not determine date from bars: {e}")
            
            # Final fallback: use current system date (live trading)
            if now is None:
                now = datetime.now(self.timezone)
                logger.debug(f"Using current system date: {now}")
            
            # Parse configured session times
            start_hour, start_min = map(int, self.overnight_start.split(':'))
            end_hour, end_min = map(int, self.overnight_end.split(':'))
            start_clock = time(start_hour, start_min)
            end_clock = time(end_hour, end_min)
            
            # Determine which overnight window to use (all times in session_timezone).
            #
            # Cross-midnight (e.g. 18:00 -> 09:29): window is [prev_day start_clock, end_day end_clock].
            # - From end_clock until before start_clock the same calendar day: that window is still
            #   in progress (it ends today at end_clock).
            # - From start_clock until midnight: a NEW window started today at start_clock and ends
            #   tomorrow at end_clock.
            # - From end_clock until start_clock: the window that ended today at end_clock is the
            #   one to use for the morning session (same dates as the pre-end_clock branch).
            if end_clock <= start_clock:
                t = now.time()
                if t >= start_clock:
                    # Evening: overnight in progress since today start_clock; ends tomorrow end_clock
                    start_date = now.date()
                    end_date = start_date + timedelta(days=1)
                elif t >= end_clock:
                    # Morning/afternoon after cutoff: last completed window ended today at end_clock
                    end_date = now.date()
                    start_date = end_date - timedelta(days=1)
                else:
                    # After midnight, before end_clock: window started yesterday, ends today
                    end_date = now.date()
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
            
            start_time = self.timezone.localize(datetime.combine(start_date, start_clock))
            end_time = self.timezone.localize(datetime.combine(end_date, end_clock))
            # Include the full end minute so 1m bars stamped at e.g. 09:29:xx are not dropped
            end_time = end_time + timedelta(minutes=1) - timedelta(microseconds=1)
            
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
            
            cache_key = (symbol, end_date)
            # After morning cutoff on end_date, the window is fixed and safe to cache long-term.
            cache_allowed = (now.date() > end_date) or (
                now.date() == end_date and now.time() >= end_clock
            )
            if cache_allowed:
                if cache_key in self._overnight_range_cache:
                    logger.debug(
                        "Using cached overnight range for %s session ending %s",
                        symbol,
                        end_date,
                    )
                    rd = self._overnight_range_cache[cache_key]
                    self.active_ranges[symbol] = rd
                    return rd
            else:
                inflight = self._overnight_range_inflight.get(cache_key)
                if inflight is not None:
                    cached_range, ts_mono = inflight
                    if time_module.monotonic() - ts_mono < 45.0:
                        logger.debug(
                            "Using inflight overnight range for %s session ending %s (45s TTL)",
                            symbol,
                            end_date,
                        )
                        self.active_ranges[symbol] = cached_range
                        return cached_range
            
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
            
            if cache_allowed:
                self._overnight_range_cache[cache_key] = range_data
                self._overnight_range_inflight.pop(cache_key, None)
            else:
                self._overnight_range_inflight[cache_key] = (range_data, time_module.monotonic())
            
            logger.info(f"📊 Overnight range for {symbol}: High={high:.2f}, Low={low:.2f}, Range={range_data.range_size:.2f}")
            self.active_ranges[symbol] = range_data
            self._persist_or_ranges_to_db()
            return range_data
            
        except Exception as e:
            logger.error(f"Error tracking overnight range for {symbol}: {e}")
            return None
    
    async def get_previous_day_atr_zones(self, symbol: str, atr_data: ATRData) -> Optional[Dict]:
        """
        Calculate previous day's ATR zones for better profit targeting.
        
        When current zones overlap with overnight range, we can look at previous
        day's zones to find farther profit targets.
        
        Returns:
            Dict with 'upper' and 'lower' zone bounds, or None if can't calculate
        """
        try:
            # Get yesterday's date in strategy timezone
            now_et = datetime.now(self.timezone)
            _cb = getattr(self.trading_bot, "_current_bar_timestamp", None)
            if _cb is not None and hasattr(_cb, "astimezone"):
                now_et = _cb.astimezone(self.timezone) if _cb.tzinfo else _cb.replace(tzinfo=timezone.utc).astimezone(self.timezone)
            
            # Find previous trading day (skip weekends)
            yesterday = (now_et - timedelta(days=1)).date()
            # Skip backward over weekends to find last trading day
            while yesterday.weekday() >= 5:  # 5=Saturday, 6=Sunday
                yesterday = yesterday - timedelta(days=1)
            
            # Get historical 30m/1m bar at market open from yesterday for zone anchor.
            # For MGC parity, anchor at 07:00 ET (unless explicitly overridden) and use the
            # correct contract month for that date (continuous daily source contract).
            zone_anchor_time = self.zone_anchor_time
            if symbol.upper() == "MGC" and not self._cfg.env_present("ZONE_ANCHOR_TIME"):
                zone_anchor_time = "07:00"

            open_hour, open_min = map(int, zone_anchor_time.split(':'))
            yesterday_open = datetime.combine(yesterday, time(open_hour, open_min, 0))
            if hasattr(self.timezone, 'localize'):
                yesterday_open = self.timezone.localize(yesterday_open)
            else:
                yesterday_open = yesterday_open.replace(tzinfo=self.timezone)
            
            # Fetch bars around yesterday's market open
            start_utc = (yesterday_open - timedelta(minutes=90)).astimezone(timezone.utc)
            end_utc = (yesterday_open + timedelta(minutes=90)).astimezone(timezone.utc)
            
            prev_open_price = None
            contract_id_override = None
            if symbol.upper() == "MGC":
                try:
                    daily_bars_for_contract = await self.trading_bot.broker_adapter.get_historical_data(
                        symbol=symbol,
                        timeframe="1d",
                        limit=80,
                        continuous_daily=True,
                        include_partial_daily=False,
                    )
                    for db in daily_bars_for_contract or []:
                        if not getattr(db, "timestamp", None):
                            continue
                        if db.timestamp.astimezone(self.timezone).date() == yesterday:
                            rd = getattr(db, "raw_data", None) or {}
                            contract_id_override = rd.get("source_contract_id") or rd.get("source_contract") or rd.get("contractId")
                            break
                except Exception:
                    contract_id_override = None

            if self.zone_open_source == "30m":
                bars_30m = await self.trading_bot.get_historical_data(
                    symbol=symbol,
                    timeframe='30m',
                    start_time=start_utc,
                    end_time=end_utc,
                    limit=10,
                    contract_id_override=contract_id_override,
                )
                if bars_30m:
                    for b in sorted(bars_30m, key=lambda x: b.get('timestamp') or b.get('time') or datetime.min):
                        ts = b.get('timestamp') or b.get('time')
                        if isinstance(ts, str):
                            bar_time = datetime.fromisoformat(ts.replace('Z', '+00:00'))
                        elif isinstance(ts, datetime):
                            bar_time = ts
                        else:
                            continue
                        if bar_time.tzinfo is None:
                            bar_time = bar_time.replace(tzinfo=timezone.utc)
                        bar_local = bar_time.astimezone(self.timezone).replace(second=0, microsecond=0)
                        if bar_local == yesterday_open:
                            prev_open_price = b.get('open', b.get('o', 0))
                            break
            
            # Fallback to 1m if 30m not found or configured
            if prev_open_price is None or prev_open_price == 0:
                bars_1m = await self.trading_bot.get_historical_data(
                    symbol=symbol,
                    timeframe='1m',
                    start_time=start_utc,
                    end_time=end_utc,
                    limit=60,
                    contract_id_override=contract_id_override,
                )
                if bars_1m:
                    for b in sorted(bars_1m, key=lambda x: b.get('timestamp') or b.get('time') or datetime.min):
                        ts = b.get('timestamp') or b.get('time')
                        if isinstance(ts, str):
                            bar_time = datetime.fromisoformat(ts.replace('Z', '+00:00'))
                        elif isinstance(ts, datetime):
                            bar_time = ts
                        else:
                            continue
                        if bar_time.tzinfo is None:
                            bar_time = bar_time.replace(tzinfo=timezone.utc)
                        bar_local = bar_time.astimezone(self.timezone).replace(second=0, microsecond=0)
                        if bar_local == yesterday_open:
                            prev_open_price = b.get('open', b.get('o', 0))
                            break
            
            if not prev_open_price or prev_open_price == 0:
                logger.debug(f"Could not find previous day's market open price for {symbol}")
                return None
            
            # Calculate zones using same formula as current day
            day_dist = atr_data.daily_atr * 0.5
            zone_near_mult = float(self._cfg.get_float("signal.atr_zone_near_mult", 0.5))
            zone_far_mult = float(self._cfg.get_float("signal.atr_zone_far_mult", 0.618))
            
            prev_upper_lower = prev_open_price + day_dist * zone_near_mult
            prev_upper_upper = prev_open_price + day_dist * zone_far_mult
            prev_lower_upper = prev_open_price - day_dist * zone_near_mult
            prev_lower_lower = prev_open_price - day_dist * zone_far_mult
            
            logger.debug(f"Previous day ({yesterday}) zones: Upper=[{prev_upper_lower:.2f}, {prev_upper_upper:.2f}], Lower=[{prev_lower_lower:.2f}, {prev_lower_upper:.2f}]")
            
            return {
                'upper': {'lower': prev_upper_lower, 'upper': prev_upper_upper, 'midpoint': (prev_upper_lower + prev_upper_upper) / 2},
                'lower': {'lower': prev_lower_lower, 'upper': prev_lower_upper, 'midpoint': (prev_lower_lower + prev_lower_upper) / 2}
            }
            
        except Exception as e:
            logger.debug(f"Error calculating previous day ATR zones: {e}")
            return None

    async def calculate_range_break_orders(self, symbol: str) -> Tuple[Optional[RangeBreakOrder], Optional[RangeBreakOrder]]:
        """
        Calculate range breakout orders (long and short) based on overnight range and ATR.
        
        All prices are rounded to valid tick sizes to prevent order rejections.
        
        For profit targets when zones overlap:
        - Checks previous day's ATR zones
        - Uses the farther target between 2*current_atr and previous day's zone
        
        Returns:
            Tuple of (long_order, short_order) or (None, None) if error
        """
        try:
            symbol = symbol.upper()
            range_data = await self.track_overnight_range(symbol)
            if not range_data:
                return None, None
            
            # Calculate ATR - use cached for daily/zones, but recalc current if placing orders
            # Check if we're being called from place_range_break_orders (use dynamic ATR)
            use_dynamic_atr = self._cfg.get_bool("position_management.use_dynamic_atr_for_orders", True)
            
            atr_data = await self.calculate_atr(symbol)
            if not atr_data:
                logger.error(f"Failed to calculate ATR for {symbol}")
                return None, None
            
            # Recalculate current ATR dynamically if enabled (for more accurate stops at order time)
            if use_dynamic_atr:
                dynamic_current_atr = await self.recalculate_current_atr(symbol)
                if dynamic_current_atr:
                    # Replace current_atr with fresh calculation, keep daily/zones from cache
                    atr_data = ATRData(
                        current_atr=dynamic_current_atr,
                        daily_atr=atr_data.daily_atr,
                        atr_zone_high=atr_data.atr_zone_high,
                        atr_zone_low=atr_data.atr_zone_low,
                        period=atr_data.period,
                        market_open_price=atr_data.market_open_price,
                        day_bull_price=atr_data.day_bull_price,
                        day_bull_price1=atr_data.day_bull_price1,
                        day_bear_price=atr_data.day_bear_price,
                        day_bear_price1=atr_data.day_bear_price1
                    )
            
            # Get tick size for proper price rounding
            tick_size = await self.get_tick_size(symbol)
            logger.info(f"Using tick size {tick_size} for {symbol}")
            
            # Check if daily ATR zones overlap with overnight range
            # If they overlap (any part inside), use 2*current_atr as TP instead
            # Zone overlaps if: zone_low <= range_high AND zone_high >= range_low
            upper_zone_overlaps = (atr_data.day_bull_price <= range_data.high and 
                                   atr_data.day_bull_price1 >= range_data.low)
            lower_zone_overlaps = (atr_data.day_bear_price1 <= range_data.high and 
                                   atr_data.day_bear_price >= range_data.low)
            
            # Only log ATR zones check if we're calculating for the first time today (avoid spam)
            # Check if we've already logged this today
            now = datetime.now(self.timezone)
            log_key = f"{symbol}_atr_zones_logged_{now.date()}"
            if not hasattr(self, '_atr_zones_logged') or log_key not in getattr(self, '_atr_zones_logged', set()):
                if not hasattr(self, '_atr_zones_logged'):
                    self._atr_zones_logged = set()
                self._atr_zones_logged.add(log_key)
                logger.info(f"Daily ATR zones check:")
                logger.info(f"  Overnight Range: [{range_data.low:.2f}, {range_data.high:.2f}]")
                logger.info(f"  Upper Zone: [{atr_data.day_bull_price:.2f}, {atr_data.day_bull_price1:.2f}] - Overlaps: {upper_zone_overlaps}")
                logger.info(f"  Lower Zone: [{atr_data.day_bear_price1:.2f}, {atr_data.day_bear_price:.2f}] - Overlaps: {lower_zone_overlaps}")
            else:
                logger.debug(f"Daily ATR zones (cached): Range=[{range_data.low:.2f}, {range_data.high:.2f}], Upper=[{atr_data.day_bull_price:.2f}, {atr_data.day_bull_price1:.2f}], Lower=[{atr_data.day_bear_price1:.2f}, {atr_data.day_bear_price:.2f}]")
            
            # Calculate long breakout order (above overnight high)
            long_entry_raw = range_data.high + self.range_break_offset
            long_stop_raw = long_entry_raw - (atr_data.current_atr * self.stop_atr_multiplier)
            
            # Determine TP target based on whether ATR zone overlaps with range
            # Use MIDPOINT of ATR zone (halfway between closest and farthest points)
            upper_zone_midpoint = (atr_data.day_bull_price + atr_data.day_bull_price1) / 2.0
            
            if upper_zone_overlaps:
                # ATR zone overlaps with range - use larger of 2*current_atr or previous day's upper zone
                default_tp = long_entry_raw + (atr_data.current_atr * 2.0)
                long_tp_raw = default_tp
                
                # Try to find better target using previous day's zones
                prev_zones = await self.get_previous_day_atr_zones(symbol, atr_data)
                if prev_zones and prev_zones['upper']:
                    prev_upper_midpoint = prev_zones['upper']['midpoint']
                    # Use previous day's zone if it's above current overnight range and farther than 2*ATR
                    if prev_upper_midpoint > range_data.high and prev_upper_midpoint > default_tp:
                        long_tp_raw = prev_upper_midpoint
                        logger.debug(f"  Using previous day's upper zone midpoint {long_tp_raw:.2f} (better than 2*current_atr {default_tp:.2f})")
                    else:
                        logger.debug(f"  Using 2*current_atr for TP (prev zone not better: {prev_upper_midpoint:.2f})")
                else:
                    logger.debug(f"  Upper ATR zone overlaps overnight range - using 2*current_atr for TP")
            elif atr_data.day_bull_price > range_data.high:
                # ATR zone is completely ABOVE range - TARGET THE MIDPOINT of the zone
                logger.debug(f"  Upper ATR zone above overnight range - targeting zone midpoint at {upper_zone_midpoint:.2f} (zone: [{atr_data.day_bull_price:.2f}, {atr_data.day_bull_price1:.2f}])")
                long_tp_raw = upper_zone_midpoint
            else:
                # ATR zone is completely BELOW range - use ATR * 2 from entry
                logger.debug(f"  Upper ATR zone below overnight range - using 2*current_atr for TP")
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
            
            # Determine TP target based on whether ATR zone overlaps with range
            # Use MIDPOINT of ATR zone (halfway between closest and farthest points)
            lower_zone_midpoint = (atr_data.day_bear_price + atr_data.day_bear_price1) / 2.0
            
            if lower_zone_overlaps:
                # ATR zone overlaps with range - use larger of 2*current_atr or previous day's lower zone
                default_tp = short_entry_raw - (atr_data.current_atr * 2.0)
                short_tp_raw = default_tp
                
                # Try to find better target using previous day's zones
                prev_zones = await self.get_previous_day_atr_zones(symbol, atr_data)
                if prev_zones and prev_zones['lower']:
                    prev_lower_midpoint = prev_zones['lower']['midpoint']
                    # Use previous day's zone if it's below current overnight range and farther than 2*ATR
                    if prev_lower_midpoint < range_data.low and prev_lower_midpoint < default_tp:
                        short_tp_raw = prev_lower_midpoint
                        logger.debug(f"  Using previous day's lower zone midpoint {short_tp_raw:.2f} (better than 2*current_atr {default_tp:.2f})")
                    else:
                        logger.debug(f"  Using 2*current_atr for TP (prev zone not better: {prev_lower_midpoint:.2f})")
                else:
                    logger.debug(f"  Lower ATR zone overlaps overnight range - using 2*current_atr for TP")
            elif atr_data.day_bear_price1 < range_data.low:
                # ATR zone is completely BELOW range - TARGET THE MIDPOINT of the zone
                # For SHORT orders, TP must be BELOW entry, so we use the midpoint of the lower zone
                logger.debug(f"  Lower ATR zone below overnight range - targeting zone midpoint at {lower_zone_midpoint:.2f} (zone: [{atr_data.day_bear_price1:.2f}, {atr_data.day_bear_price:.2f}])")
                short_tp_raw = lower_zone_midpoint
            else:
                # ATR zone is completely ABOVE range - use ATR * 2 from entry
                logger.debug(f"  Lower ATR zone above overnight range - using 2*current_atr for TP")
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
            # Log warnings but still return orders so they can be displayed
            validation_passed = True
            if long_stop >= long_entry:
                logger.warning(f"⚠️  Invalid LONG order: Stop ({long_stop}) must be below entry ({long_entry})")
                validation_passed = False
            if long_tp <= long_entry:
                logger.warning(f"⚠️  Invalid LONG order: TP ({long_tp}) must be above entry ({long_entry})")
                validation_passed = False
            if short_stop <= short_entry:
                logger.warning(f"⚠️  Invalid SHORT order: Stop ({short_stop}) must be above entry ({short_entry})")
                validation_passed = False
            if short_tp >= short_entry:
                logger.warning(f"⚠️  Invalid SHORT order: TP ({short_tp}) must be below entry ({short_entry})")
                validation_passed = False
            
            if not validation_passed:
                logger.warning(f"⚠️  Orders calculated but have validation warnings - will still be displayed")
            
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
            
            # Calculate orders first to get quantities
            long_order, short_order = await self.calculate_range_break_orders(symbol)
            if not long_order or not short_order:
                return {"success": False, "error": "Failed to calculate orders"}
            
            # Check total exposure (positions + pending orders + new orders) - CRITICAL
            open_orders = await self.trading_bot.get_open_orders()
            orders_list = open_orders if isinstance(open_orders, list) else []
            total_exposure = await self.get_total_exposure(symbol, orders_list)
            
            # Check if placing both orders would exceed max quantity
            # We place both long and short orders, so check total of both
            new_order_quantity = long_order.quantity + short_order.quantity
            if total_exposure + new_order_quantity > self.max_quantity_per_instrument:
                position_qty = await self.get_current_position_quantity(symbol)
                pending_qty = await self.get_pending_entry_order_quantity(symbol, orders_list)
                logger.warning(f"⚠️  Max quantity would be exceeded for {symbol}: {total_exposure} + {new_order_quantity} = {total_exposure + new_order_quantity} > {self.max_quantity_per_instrument} contracts (pos={position_qty} + pending={pending_qty}) - skipping order placement")
                return {"success": False, "error": f"Max quantity would be exceeded: {total_exposure + new_order_quantity} > {self.max_quantity_per_instrument}"}

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
                
                # Broadcast signal for LONG order placement
                try:
                    if hasattr(self.trading_bot, 'strategy_manager') and self.trading_bot.strategy_manager:
                        signal_data = {
                            'type': 'LONG',
                            'strategy': self.config.name,
                            'symbol': symbol,
                            'message': f'LONG breakout order placed @ {long_order.entry_price:.2f}',
                            'entry_price': long_order.entry_price,
                            'stop_loss': long_order.stop_loss,
                            'take_profit': long_order.take_profit,
                            'direction': 'LONG',
                            'timestamp': datetime.now(timezone.utc).isoformat(),
                        }
                        await self.trading_bot.strategy_manager.broadcast_signal(signal_data)
                except Exception as e:
                    logger.debug(f"Could not broadcast LONG signal: {e}")
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
                # Check if this is the Auto OCO Brackets error - handle it specially
                error_lower = str(error_msg).lower()
                if "auto oco brackets" in error_lower and ("not enabled" in error_lower or "must enable" in error_lower):
                    logger.error(f"❌ LONG order skipped: Auto OCO Brackets is not enabled in account settings")
                    logger.error(f"   Please enable 'Auto OCO Brackets' in your TopStepX account settings to use bracket orders.")
                else:
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
                
                # Broadcast signal for SHORT order placement
                try:
                    if hasattr(self.trading_bot, 'strategy_manager') and self.trading_bot.strategy_manager:
                        signal_data = {
                            'type': 'SHORT',
                            'strategy': self.config.name,
                            'symbol': symbol,
                            'message': f'SHORT breakout order placed @ {short_order.entry_price:.2f}',
                            'entry_price': short_order.entry_price,
                            'stop_loss': short_order.stop_loss,
                            'take_profit': short_order.take_profit,
                            'direction': 'SHORT',
                            'timestamp': datetime.now(timezone.utc).isoformat(),
                        }
                        await self.trading_bot.strategy_manager.broadcast_signal(signal_data)
                except Exception as e:
                    logger.debug(f"Could not broadcast SHORT signal: {e}")
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
                # Check if this is the Auto OCO Brackets error - handle it specially
                error_lower = str(error_msg).lower()
                if "auto oco brackets" in error_lower and ("not enabled" in error_lower or "must enable" in error_lower):
                    logger.error(f"❌ SHORT order skipped: Auto OCO Brackets is not enabled in account settings")
                    logger.error(f"   Please enable 'Auto OCO Brackets' in your TopStepX account settings to use bracket orders.")
                else:
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
            symbol = order.get("symbol", '').upper()
            if not symbol:
                # Try symbolId field (e.g., "F.US.MGC" -> "MGC")
                symbol_id = order.get('symbolId', '')
                if symbol_id:
                    parts = symbol_id.split('.')
                    symbol = parts[-1].upper() if parts else ''
            if not symbol:
                # Try contract ID as last resort
                contract_id = order.get("contractId")
                if contract_id:
                    extracted = self.trading_bot._get_symbol_from_contract_id(contract_id)
                    if extracted:
                        symbol = extracted.upper()
            if not symbol:
                continue
            grouped.setdefault(symbol, []).append(order)
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
        """Place a single breakout stop order (used by proactive monitor). OPTIMIZED: Fetch data once, reuse."""
        symbol = order_template.symbol.upper()
        side = order_template.side
        
        # EARLY EXIT: Check cooldown BEFORE doing any work
        is_cooldown, remaining = self._is_cooldown_active(symbol, side)
        if is_cooldown:
            logger.debug(f"⏳ Skipping {side} order for {symbol} - cooldown active ({remaining:.1f}s remaining)")
            return None
        
        # OPTIMIZATION: Single parallel snapshot for orders + positions, reuse throughout
        snap = await self.trading_bot.get_positions_and_orders_batch()
        orders_list = snap.get("orders") or []
        if not isinstance(orders_list, list):
            orders_list = []
        positions_list = snap.get("positions") or []
        if not isinstance(positions_list, list):
            positions_list = []
        
        # Calculate exposure using cached data (NO additional API calls)
        position_qty = await self.get_current_position_quantity(symbol, positions=positions_list)
        pending_qty = await self.get_pending_entry_order_quantity(symbol, orders_list)
        total_exposure = position_qty + pending_qty
        
        # Check if placing this order would exceed max quantity
        if total_exposure + order_template.quantity > self.max_quantity_per_instrument:
            logger.warning(f"⚠️  Max quantity would be exceeded for {symbol}: {total_exposure} + {order_template.quantity} = {total_exposure + order_template.quantity} > {self.max_quantity_per_instrument} contracts (pos={position_qty} + pending={pending_qty}) - skipping order")
            return None
        
        # Get tick size and round prices to valid tick increments BEFORE validation
        tick_size = await self.get_tick_size(symbol)
        entry_price = self.round_to_tick(order_template.entry_price, tick_size)
        stop_loss_price = self.round_to_tick(order_template.stop_loss, tick_size)
        take_profit_price = self.round_to_tick(order_template.take_profit, tick_size)
        
        # Log exposure breakdown (using already-fetched data, NO additional API calls)
        logger.info(f"📌 Placing {order_template.side} breakout order for {symbol} at {entry_price:.2f} (tick_size={tick_size}, exposure={total_exposure}/{self.max_quantity_per_instrument} [pos={position_qty} + pending={pending_qty}])")
        
        # Validate entry price against current market price
        try:
            quote = await self.get_quote_optimized(symbol)
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
            error_msg = result.get('error') if result else 'unknown error'
            # Check if this is the Auto OCO Brackets error - handle it specially
            error_lower = str(error_msg).lower()
            if "auto oco brackets" in error_lower and ("not enabled" in error_lower or "must enable" in error_lower):
                logger.error(f"❌ {order_template.side} breakout order skipped for {symbol}: Auto OCO Brackets is not enabled in account settings")
                logger.error(f"   Please enable 'Auto OCO Brackets' in your TopStepX account settings to use bracket orders.")
            else:
                logger.warning(f"⚠️  Unable to place {order_template.side} breakout order for {symbol}: {error_msg}")
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

        # Fast path: if we already tracked a live order for this symbol+side, confirm it's
        # still on the exchange and skip placement entirely (avoids risk-check spam).
        tracked_id = self.breakout_active_orders.get(symbol, {}).get(side)
        if tracked_id:
            existing_ids = {str(o.get("id")) for o in existing_orders if isinstance(o, dict)}
            if tracked_id in existing_ids:
                logger.debug(f"Already have live {side} order {tracked_id} for {symbol} - skipping")
                return
            # Tracked order is gone (filled or cancelled) — clear it and fall through
            self.breakout_active_orders[symbol].pop(side, None)

        # Use centralized risk manager for all risk checks
        allowed, reason = await self.risk_manager.check_order_allowed(
            symbol=symbol,
            side=side,
            quantity=order_template.quantity,
            existing_orders=existing_orders
        )
        
        if not allowed:
            logger.info(f"⚠️  Risk management blocked {side} breakout order for {symbol}: {reason}")
            return
        
        # Check for exact duplicate order at same price (strategy-specific check)
        # This is in addition to the centralized risk manager checks
        tolerance = max(self.breakout_order_tolerance_points, tick_size)
        for order in existing_orders:
            if self._order_matches_breakout(order, side, order_template.entry_price, tolerance):
                order_id = str(order.get("id"))
                self.breakout_active_orders.setdefault(symbol, {})[side] = order_id
                logger.debug(f"🔁 Existing {side} breakout order already working for {symbol} at {order_template.entry_price:.2f} (ID {order_id}) - skipping duplicate")
                return

        # Remove stale cached order id if it exists but no matching order
        if symbol in self.breakout_active_orders:
            self.breakout_active_orders[symbol].pop(side, None)

        # Place the order (risk manager will record placement for cooldown)
        order_id = await self._place_single_breakout_order(order_template)
        if order_id:
            # Record order placement in centralized risk manager
            await self.risk_manager.record_order_placement(symbol, side)
            
            # Broadcast signal for order placement attempt (even if it fails, show that strategy is active)
            try:
                if hasattr(self.trading_bot, 'strategy_manager') and self.trading_bot.strategy_manager:
                    signal_data = {
                        'type': 'LONG' if side == 'BUY' else 'SHORT',
                        'strategy': self.config.name,
                        'symbol': symbol,
                        'message': f'{side} breakout order placed @ {order_template.entry_price:.2f}',
                        'entry_price': order_template.entry_price,
                        'stop_loss': order_template.stop_loss,
                        'take_profit': order_template.take_profit,
                        'direction': 'LONG' if side == 'BUY' else 'SHORT',
                        'timestamp': datetime.now(timezone.utc).isoformat(),
                    }
                    await self.trading_bot.strategy_manager.broadcast_signal(signal_data)
            except Exception as e:
                logger.debug(f"Could not broadcast {side} signal: {e}")

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

                if self.filter_skip_weekdays and now.weekday() in self.filter_skip_weekdays:
                    await asyncio.sleep(self.breakout_monitor_interval)
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
                    # OPTIMIZATION: Skip symbols where both sides are in cooldown
                    buy_cooldown, _ = self._is_cooldown_active(symbol, 'BUY')
                    sell_cooldown, _ = self._is_cooldown_active(symbol, 'SELL')
                    
                    if buy_cooldown and sell_cooldown:
                        logger.debug(f"⏭️  Skipping {symbol} - both sides in cooldown")
                        continue
                    
                    range_data = self.active_ranges.get(symbol)
                    if not range_data:
                        continue

                    quote = await self.get_quote_optimized(symbol)
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
                        # String entries are orders that already have brackets and don't need monitoring
                        if not isinstance(order_data, dict):
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
                        
                        # Move stop toward breakeven (entry price) via position-linked stop
                        monitor_data['breakeven_triggered'] = True
                        acct = getattr(self.trading_bot, "selected_account", None)
                        account_id = acct.get("id") if isinstance(acct, dict) else None
                        position_id = (
                            position.get("id")
                            or position.get("positionId")
                            or position.get("position_id")
                        )
                        if account_id and position_id:
                            try:
                                res = await self.trading_bot.modify_stop_loss(
                                    str(position_id),
                                    float(entry_price),
                                    account_id=str(account_id),
                                )
                                if isinstance(res, dict) and res.get("error"):
                                    logger.warning(
                                        "Breakeven stop modify failed for %s: %s",
                                        symbol,
                                        res.get("error"),
                                    )
                                else:
                                    logger.info(
                                        "Breakeven: broker stop adjusted toward entry for %s",
                                        symbol,
                                    )
                            except Exception as exc:
                                logger.warning(
                                    "Breakeven modify_stop_loss error for %s: %s",
                                    symbol,
                                    exc,
                                    exc_info=True,
                                )
                        else:
                            logger.debug(
                                "Breakeven for %s: missing account_id or position_id; broker stop unchanged",
                                symbol,
                            )
                        
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
                            grace_minutes = float(self._cfg.get_float("timing.market_open_grace_minutes", 5.0))
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
        """Reload configuration (TOML hot-reload + best-effort .env reload)."""
        # Reload .env to pick up legacy env overrides (best-effort).
        try:
            from dotenv import load_dotenv
            load_dotenv(override=True)
        except Exception:
            pass

        # Hot-reload TOML if it changed.
        self._cfg.maybe_reload()

        # Re-apply config values (same mapping as __init__)
        self.overnight_start = self._cfg.get_str("timing.overnight_start", self.overnight_start)
        self.overnight_end = self._cfg.get_str("timing.overnight_end", self.overnight_end)
        self.market_open_time = self._cfg.get_str("timing.market_open", self.market_open_time)
        self.zone_open_source = (self._cfg.get_str("timing.zone_open_source", self.zone_open_source) or "30m").lower()
        self.zone_anchor_time = self._cfg.get_str("timing.zone_anchor", self.zone_anchor_time)

        if pytz:
            self.timezone = pytz.timezone(self._cfg.get_str("timing.session_timezone", "US/Eastern"))
        else:
            self.timezone = timezone.utc

        self.atr_period = int(self._cfg.get_int("signal.atr_period", self.atr_period))
        self.atr_timeframe = self._cfg.get_str("signal.atr_timeframe", self.atr_timeframe)
        self.stop_atr_multiplier = float(self._cfg.get_float("signal.stop_atr_multiplier", self.stop_atr_multiplier))
        self.tp_atr_multiplier = float(self._cfg.get_float("signal.tp_atr_multiplier", self.tp_atr_multiplier))

        self.breakeven_enabled = self._cfg.get_bool("position_management.breakeven_enabled", self.breakeven_enabled)
        self.breakeven_profit_points = float(
            self._cfg.get_float("position_management.breakeven_profit_points", self.breakeven_profit_points)
        )
        self.range_break_offset = float(self._cfg.get_float("position_management.range_break_offset", self.range_break_offset))

        self.breakout_monitor_enabled = self._cfg.get_bool("breakout_monitor.enabled", self.breakout_monitor_enabled)
        self.breakout_proximity_percent = float(self._cfg.get_float("breakout_monitor.proximity_pct", self.breakout_proximity_percent))
        self.breakout_min_proximity_points = float(
            self._cfg.get_float("breakout_monitor.min_proximity_points", self.breakout_min_proximity_points)
        )
        self.breakout_monitor_interval = float(
            self._cfg.get_float("breakout_monitor.interval_seconds", self.breakout_monitor_interval)
        )
        self.breakout_order_tolerance_points = float(
            self._cfg.get_float("breakout_monitor.order_tolerance_points", self.breakout_order_tolerance_points)
        )

        # Keep StrategyConfig in sync so persistence/UI reflect these values
        if hasattr(self, 'config'):
            self.config.trading_start_time = self.overnight_start
            self.config.trading_end_time = self.overnight_end

        logger.info(
            "Reloaded config: Overnight=%s-%s MarketOpen=%s ZoneAnchor=%s",
            self.overnight_start,
            self.overnight_end,
            self.market_open_time,
            self.zone_anchor_time,
        )
    
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
        self.status = StrategyStatus.ACTIVE  # Ensure status is set to ACTIVE
        
        # Get symbols to trade
        trade_symbols = self._get_trade_symbols(symbols)
        
        # Check if we need to wait for market open before calculating breakout levels
        # Breakout levels depend on market open price, so we should wait if started before market open
        now = datetime.now(self.timezone)
        try:
            market_open_hour, market_open_min = map(int, self.market_open_time.split(':'))
            market_open_today = now.replace(hour=market_open_hour, minute=market_open_min, second=0, microsecond=0)
            
            if now < market_open_today:
                # Before market open - wait until market open time
                time_until_open = (market_open_today - now).total_seconds()
                wait_minutes = int(time_until_open / 60)
                wait_seconds = int(time_until_open % 60)
                logger.warning(f"⏰ Bot started before market open ({self.market_open_time}). Waiting {wait_minutes}m {wait_seconds}s until market open to calculate breakout levels...")
                logger.warning(f"   Breakout levels need market open price to be accurate. Will calculate at {market_open_today.strftime('%H:%M:%S')}")
                
                # Wait until market open (check every 10 seconds to allow for cancellation)
                while now < market_open_today and self.is_trading:
                    await asyncio.sleep(min(10, (market_open_today - now).total_seconds()))
                    now = datetime.now(self.timezone)
                    if now < market_open_today:
                        remaining = (market_open_today - now).total_seconds()
                        logger.debug(f"   Still waiting... {int(remaining/60)}m {int(remaining%60)}s remaining")
                
                if not self.is_trading:
                    logger.info("Strategy stopped while waiting for market open")
                    return
                
                logger.info(f"✅ Market open time reached. Calculating breakout levels now...")
        except Exception as e:
            logger.warning(f"⚠️  Could not parse market open time '{self.market_open_time}', calculating immediately: {e}")
        
        # 🔥 NEW: Track overnight ranges and calculate breakout levels
        # This allows the strategy to work at ANY time of day, not just at market open
        logger.info("📊 Calculating overnight ranges and breakout levels...")
        for symbol in trade_symbols:
            try:
                # Track overnight range
                range_data = await self.track_overnight_range(symbol)
                if not range_data:
                    logger.warning(f"⚠️  Could not track overnight range for {symbol}")
                    continue
                
                # Calculate breakout orders (always calculated even if validation warnings exist)
                long_order, short_order = await self.calculate_range_break_orders(symbol)
                if long_order and short_order:
                    # Store breakout levels for monitoring
                    self.breakout_levels[symbol] = {
                        "BUY": long_order,
                        "SELL": short_order,
                    }
                    self.breakout_active_orders.setdefault(symbol, {})
                    logger.info(f"✅ Breakout levels calculated for {symbol}: LONG@{long_order.entry_price:.2f}, SHORT@{short_order.entry_price:.2f}")
                elif long_order or short_order:
                    # Partial success - store what we got
                    if long_order:
                        self.breakout_levels.setdefault(symbol, {})["BUY"] = long_order
                        logger.info(f"✅ LONG breakout level calculated for {symbol}: @{long_order.entry_price:.2f}")
                    if short_order:
                        self.breakout_levels.setdefault(symbol, {})["SELL"] = short_order
                        logger.info(f"✅ SHORT breakout level calculated for {symbol}: @{short_order.entry_price:.2f}")
                    self.breakout_active_orders.setdefault(symbol, {})
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
        strategy_details = [
            f"⏰ Session Time: {self.format_overnight_session_window_label()}",
            f"🚪 Market Open: {self.market_open_time} {self.timezone}",
            f"📈 ATR Period: {self.atr_period} bars ({self.atr_timeframe})",
            f"🛑 Stop Loss: {self.stop_atr_multiplier}x ATR",
            f"🎯 Take Profit: {self.tp_atr_multiplier}x ATR",
            f"📦 Position Size: {self.default_quantity} contract(s) per order",
            f"💰 Breakeven: {'ENABLED' if self.breakeven_enabled else 'DISABLED'} (+{self.breakeven_profit_points} pts)",
            f"📏 Range Break Offset: {self.range_break_offset} points",
            f"⚙️  Mode: CONTINUOUS (monitors price and places orders when within threshold)",
            f"📊 Breakout Threshold: {self.breakout_proximity_percent}% (min {self.breakout_min_proximity_points} pts)",
            f"⏱️  Monitor Interval: {self.breakout_monitor_interval}s"
        ]
        self.print_initialization_message("Overnight Range Strategy", trade_symbols, strategy_details)
        
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

