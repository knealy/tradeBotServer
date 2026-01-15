"""
Risk Management Module - Handles risk calculations and validations.

This module provides tick size, point value, trading session calculations,
and centralized strategy risk controls (position limits, order cooldowns, etc.)
that are applied to all strategies automatically.
"""

import os
import logging
from typing import Optional, Dict, List, Tuple, Any
from datetime import datetime, timedelta, timezone
from collections import defaultdict

logger = logging.getLogger(__name__)


class RiskManager:
    """
    Manages risk-related calculations and validations.
    
    Handles tick sizes, point values, price rounding, and trading session dates.
    """
    
    # Tick size mapping for common futures symbols
    TICK_SIZES = {
        "MNQ": 0.25,   # Micro E-mini Nasdaq-100
        "MES": 0.25,   # Micro E-mini S&P 500
        "MYM": 0.5,    # Micro E-mini Dow (0.5 point ticks)
        "M2K": 0.10,   # Micro E-mini Russell 2000
        "ES": 0.25,    # E-mini S&P 500
        "NQ": 0.25,    # E-mini Nasdaq-100
        "YM": 1.0,     # E-mini Dow
        "RTY": 0.10,   # E-mini Russell 2000
        "CL": 0.01,    # Crude Oil
        "GC": 0.10,    # Gold
        "SI": 0.005,   # Silver
        "NG": 0.001,   # Natural Gas
        "ZB": 0.03125, # 30-Year Treasury Bond
        "ZN": 0.015625, # 10-Year Treasury Note
        "ZC": 0.25,    # Corn
        "ZS": 0.25,    # Soybeans
        "ZW": 0.25,    # Wheat
        "MGC": 0.10,   # Micro Gold
    }
    
    # Point value mapping (dollar value per point move)
    POINT_VALUES = {
        "MNQ": 2.0,    # $2 per point (Micro E-mini Nasdaq-100)
        "MES": 5.0,    # $5 per point (Micro E-mini S&P 500)
        "MYM": 0.5,    # $0.50 per point (Micro E-mini Dow)
        "M2K": 5.0,    # $5 per point (Micro E-mini Russell 2000)
        "ES": 50.0,    # $50 per point
        "NQ": 20.0,    # $20 per point
        "YM": 5.0,     # $5 per point
        "RTY": 50.0,   # $50 per point
        "CL": 1000.0,  # $1000 per point
        "GC": 100.0,   # $100 per point
        "MGC": 10.0,   # $10 per point (Micro Gold)
        "SI": 50.0,    # $50 per point
        "NG": 10000.0, # $10,000 per point
        "ZB": 1000.0,  # $1000 per point
        "ZN": 1000.0,  # $1000 per point
        "ZC": 50.0,    # $50 per point
        "ZS": 50.0,    # $50 per point
        "ZW": 50.0,    # $50 per point
    }
    
    def __init__(self):
        """Initialize risk manager."""
        logger.debug("RiskManager initialized")
    
    def get_tick_size(self, symbol: str) -> float:
        """
        Get the tick size for a trading symbol.
        
        Args:
            symbol: Trading symbol (e.g., "MNQ", "ES")
            
        Returns:
            Tick size as float (e.g., 0.25 for MNQ)
            
        Raises:
            ValueError: If symbol not found in tick size mapping
        """
        symbol = symbol.upper()
        
        # Remove any contract month suffixes (e.g., "MNQZ25" -> "MNQ")
        base_symbol = symbol
        for suffix in ["Z25", "H26", "M26", "U26", "Z26", "H27", "M27", "U27", "Z27"]:
            if suffix in symbol:
                base_symbol = symbol.replace(suffix, "")
                break
        
        tick_size = self.TICK_SIZES.get(base_symbol)
        
        if tick_size is None:
            # Default to 0.25 for unknown symbols (common for micro futures)
            logger.warning(f"⚠️  Unknown symbol '{symbol}', defaulting tick size to 0.25")
            return 0.25
        
        return tick_size
    
    def round_to_tick_size(self, price: float, tick_size: float) -> float:
        """
        Round price to nearest valid tick size.
        
        Args:
            price: Price to round
            tick_size: Tick size to round to
            
        Returns:
            Rounded price
        """
        if tick_size <= 0:
            return price
        
        return round(price / tick_size) * tick_size
    
    def get_point_value(self, symbol: str) -> float:
        """
        Get the point value (dollar value per point) for a trading symbol.
        
        Args:
            symbol: Trading symbol (e.g., "MNQ", "ES")
            
        Returns:
            Point value as float (e.g., 0.50 for MNQ, 50.0 for ES)
            
        Raises:
            ValueError: If symbol not found in point value mapping
        """
        symbol = symbol.upper()
        
        # Remove any contract month suffixes
        base_symbol = symbol
        for suffix in ["Z25", "H26", "M26", "U26", "Z26", "H27", "M27", "U27", "Z27"]:
            if suffix in symbol:
                base_symbol = symbol.replace(suffix, "")
                break
        
        point_value = self.POINT_VALUES.get(base_symbol)
        
        if point_value is None:
            # Try to infer from symbol name
            if 'MNQ' in symbol or 'NQ' in symbol:
                return 2.0  # Micro NQ: $2 per point
            elif 'MES' in symbol or 'ES' in symbol:
                return 5.0  # Micro ES: $5 per point
            elif 'MYM' in symbol or 'YM' in symbol:
                return 0.5  # Micro YM: $0.50 per point
            elif 'M2K' in symbol or 'RTY' in symbol:
                return 5.0  # Micro Russell: $5 per point
            # Default to 0.50 for unknown symbols (common for micro futures)
            logger.warning(f"⚠️  Unknown symbol '{symbol}', defaulting point value to 0.50")
            return 0.50
        
        return point_value
    
    def get_trading_session_dates(self, date: Optional[datetime] = None) -> Dict[str, datetime]:
        """
        Get the start and end dates for the trading session containing the given date.
        
        Sessions run from 6pm EST to 4pm EST next day, Sunday through Friday.
        
        Args:
            date: Date to get session for (defaults to now in UTC)
            
        Returns:
            Dictionary with 'session_start' and 'session_end' as datetime objects in UTC
        """
        if date is None:
            date = datetime.now(timezone.utc)
        
        # Convert to EST (UTC-5) or EDT (UTC-4) - simplified to UTC-5
        # In production, you'd use pytz for proper timezone handling
        est_offset = timedelta(hours=5)
        est_time = date - est_offset
        
        # Get the date in EST
        est_date = est_time.date()
        est_hour = est_time.hour
        
        # Session starts at 6pm EST (18:00) and ends at 4pm EST (16:00) next day
        # If current time is before 6pm, session started yesterday at 6pm
        # If current time is after 4pm, session ends today at 4pm
        # Otherwise, session started today at 6pm and ends tomorrow at 4pm
        
        if est_hour < 18:  # Before 6pm EST
            # Session started yesterday at 6pm EST
            session_start_est = datetime.combine(
                est_date - timedelta(days=1),
                datetime.min.time().replace(hour=18)
            )
            session_end_est = datetime.combine(
                est_date,
                datetime.min.time().replace(hour=16)
            )
        elif est_hour >= 16:  # After 4pm EST
            # Session ends today at 4pm EST, next session starts today at 6pm EST
            session_start_est = datetime.combine(
                est_date,
                datetime.min.time().replace(hour=18)
            )
            session_end_est = datetime.combine(
                est_date + timedelta(days=1),
                datetime.min.time().replace(hour=16)
            )
        else:  # Between 6pm and 4pm (overnight session)
            # Session started today at 6pm EST, ends tomorrow at 4pm EST
            session_start_est = datetime.combine(
                est_date,
                datetime.min.time().replace(hour=18)
            )
            session_end_est = datetime.combine(
                est_date + timedelta(days=1),
                datetime.min.time().replace(hour=16)
            )
        
        # Convert back to UTC
        session_start_utc = session_start_est + est_offset
        session_end_utc = session_end_est + est_offset
        
        # Make timezone-aware
        session_start_utc = session_start_utc.replace(tzinfo=timezone.utc)
        session_end_utc = session_end_utc.replace(tzinfo=timezone.utc)
        
        return {
            "session_start": session_start_utc,
            "session_end": session_end_utc
        }


class StrategyRiskManager:
    """
    Centralized risk management for all strategies.
    
    Applies position quantity limits, order cooldowns, and time restrictions
    to prevent excessive order placement and ensure compliance with account limits.
    
    This is automatically applied to all strategies via BaseStrategy.place_bracket_order()
    
    Supports per-instrument configuration via risk_config parameter:
    {
        'MNQ': {'max_quantity': 10, 'cooldown': 60.0, 'max_pending': 1},
        'MES': {'max_quantity': 5, 'cooldown': 30.0, 'max_pending': 2},
        ...
    }
    """
    
    def __init__(self, trading_bot, risk_config: Optional[Dict[str, Dict[str, Any]]] = None):
        """
        Initialize strategy risk manager.
        
        Args:
            trading_bot: Reference to trading bot for accessing positions/orders
            risk_config: Optional per-instrument risk configuration dict.
                        Format: {'SYMBOL': {'max_quantity': int, 'cooldown': float, 'max_pending': int}}
                        If not provided, uses environment variables or defaults.
        """
        self.trading_bot = trading_bot
        
        # Get reference to state cache if available
        self._state_cache = getattr(trading_bot, 'state_cache', None)
        
        # Default configuration from environment variables
        default_max_qty = int(os.getenv('MAX_QUANTITY_PER_INSTRUMENT', '10'))
        default_cooldown = float(os.getenv('STRATEGY_ORDER_COOLDOWN_SECONDS', '60.0'))
        default_max_pending = int(os.getenv('MAX_PENDING_ORDERS_PER_SYMBOL_SIDE', '1'))
        
        # Per-instrument configuration (symbol -> config dict)
        self._risk_config: Dict[str, Dict[str, Any]] = {}
        
        # Track recent order attempts (not just placements) to avoid redundant checks
        self._recent_attempts: Dict[str, Dict[str, datetime]] = defaultdict(dict)
        self._attempt_remember_duration = 15.0  # Remember attempts for 15 seconds
        
        if risk_config:
            # Validate and store per-instrument config
            for symbol, config in risk_config.items():
                symbol_upper = symbol.upper()
                self._risk_config[symbol_upper] = {
                    'max_quantity': config.get('max_quantity', default_max_qty),
                    'cooldown': config.get('cooldown', default_cooldown),
                    'max_pending': config.get('max_pending', default_max_pending)
                }
        
        # Store defaults for symbols not in config
        self._default_max_qty = default_max_qty
        self._default_cooldown = default_cooldown
        self._default_max_pending = default_max_pending
        
        # Track last order placement time per symbol/side
        self._last_order_placement: Dict[str, Dict[str, datetime]] = defaultdict(dict)
        
        # Track last order attempt time (flat dict format for strategy compatibility)
        # Format: {f"{symbol}_{side}": datetime}
        self._last_order_attempt: Dict[str, datetime] = {}
        
        # Log initialization
        if self._risk_config:
            config_str = ', '.join([f"{sym}: qty={cfg['max_quantity']}, cooldown={cfg['cooldown']}s, pending={cfg['max_pending']}" 
                                   for sym, cfg in self._risk_config.items()])
            logger.info(f"🛡️  StrategyRiskManager initialized with per-instrument config: {config_str}")
        else:
            logger.info(f"🛡️  StrategyRiskManager initialized: max_qty={default_max_qty}, "
                       f"cooldown={default_cooldown}s, max_pending={default_max_pending}")
    
    def _get_config_for_symbol(self, symbol: str) -> Dict[str, Any]:
        """Get risk configuration for a specific symbol."""
        symbol_upper = symbol.upper()
        if symbol_upper in self._risk_config:
            return self._risk_config[symbol_upper]
        return {
            'max_quantity': self._default_max_qty,
            'cooldown': self._default_cooldown,
            'max_pending': self._default_max_pending
        }
    
    @property
    def order_cooldown_seconds(self) -> float:
        """Get default order cooldown in seconds (for backward compatibility)."""
        return self._default_cooldown
    
    async def check_order_allowed(
        self, 
        symbol: str, 
        side: str, 
        quantity: int,
        existing_orders: Optional[List[Dict]] = None
    ) -> Tuple[bool, str]:
        """
        Check if an order is allowed based on risk management rules.
        
        Args:
            symbol: Trading symbol (e.g., "MNQ")
            side: "BUY" or "SELL"
            quantity: Order quantity
            existing_orders: Optional list of existing orders (if None, fetches from API)
        
        Returns:
            Tuple of (allowed: bool, reason: str)
        """
        symbol = symbol.upper()
        
        # Get per-instrument config
        config = self._get_config_for_symbol(symbol)
        max_quantity = config['max_quantity']
        cooldown_seconds = config['cooldown']
        max_pending = config['max_pending']
        
        # 1. Check if we recently attempted this same order (avoid redundant checks)
        now = datetime.now(timezone.utc)
        cooldown_key = f"{symbol}_{side}"
        
        if symbol in self._recent_attempts and side in self._recent_attempts[symbol]:
            last_attempt = self._recent_attempts[symbol][side]
            time_since_attempt = (now - last_attempt).total_seconds()
            if time_since_attempt < self._attempt_remember_duration:
                # We recently tried this order and it was blocked
                # No need to log again, just return False silently
                return False, f"Recently attempted: {time_since_attempt:.1f}s < {self._attempt_remember_duration}s"
        
        # 2. Check cooldown period (actual placements)
        if symbol in self._last_order_placement and side in self._last_order_placement[symbol]:
            last_placement = self._last_order_placement[symbol][side]
            time_since_last = (now - last_placement).total_seconds()
            if time_since_last < cooldown_seconds:
                # Record this attempt to avoid checking again soon
                self._recent_attempts[symbol][side] = now
                self._last_order_attempt[cooldown_key] = now
                return False, f"Cooldown active: {time_since_last:.1f}s < {cooldown_seconds}s"
        
        # 3. Fetch orders if not provided
        # CRITICAL: Always fetch fresh orders directly from API (bypass cache) for risk checks
        # to ensure we see the most recent orders and prevent exceeding limits
        if existing_orders is None:
            try:
                # Always fetch directly from API to get the latest orders (bypass cache for risk checks)
                existing_orders = await self.trading_bot.get_open_orders()
                    
                if not isinstance(existing_orders, list):
                    existing_orders = []
                
                logger.info(f"🔍 RISK CHECK: Fetched {len(existing_orders)} orders from API for {symbol} {side}")
                # Log first order sample for debugging
                if existing_orders:
                    sample = existing_orders[0]
                    logger.info(f"🔍 RISK CHECK Sample order: symbolId={sample.get('symbolId')}, contractId={sample.get('contractId')}, symbol={sample.get('symbol')}, side={sample.get('side')}, type={sample.get('type')}")
            except Exception as e:
                logger.warning(f"⚠️  Could not fetch orders for risk check: {e}")
                existing_orders = []
        
        # 4. Count existing ENTRY orders for this symbol (ALL sides)
        # Use customTag to identify entry orders: contains "stop_bracket" but NOT "-SL" or "-TP"
        entry_orders_count = 0
        entry_orders_qty = 0
        logger.info(f"🔍 RISK CHECK: Examining {len(existing_orders)} orders to count entry orders for {symbol}")
        for order in existing_orders:
            if not isinstance(order, dict):
                continue
            
            # Extract symbol from order - try multiple fields
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
            
            if not order_symbol or order_symbol != symbol:
                continue
            
            # Check if this is an entry order by looking at customTag
            custom_tag = order.get('customTag') or order.get('custom_tag') or ''
            is_stop_bracket = 'stop_bracket' in str(custom_tag) or 'stop-bracket' in str(custom_tag)
            is_bracket_sl_tp = '-SL' in str(custom_tag) or '-TP' in str(custom_tag)
            
            raw_side = order.get("side", -1)
            order_side = "BUY" if raw_side in (0, "0", "buy", "BUY") else "SELL"
            
            logger.info(f"🔍 RISK CHECK: Order ID={order.get('id')}, symbol='{order_symbol}', side={order_side}, "
                       f"tag='{custom_tag[:40] if custom_tag else 'none'}', is_entry={is_stop_bracket and not is_bracket_sl_tp}")
            
            # Only count ENTRY orders (has stop_bracket tag but NOT -SL/-TP)
            if is_stop_bracket and not is_bracket_sl_tp:
                entry_orders_count += 1
                qty = order.get('quantity') or order.get('size') or 0
                if qty:
                    entry_orders_qty += abs(int(qty))
                logger.info(f"✅ RISK CHECK: COUNTED entry order for {symbol}: ID={order.get('id')}, side={order_side}, "
                           f"qty={qty}, total_count={entry_orders_count}, total_qty={entry_orders_qty}")
        
        # 5. Check pending order limit (max number of pending entry orders)
        logger.info(f"🔍 RISK CHECK: PENDING CHECK: Found {entry_orders_count} entry order(s) for {symbol} (limit: {max_pending})")
        if entry_orders_count >= max_pending:
            # Record this attempt
            self._recent_attempts[symbol][side] = now
            self._last_order_attempt[cooldown_key] = now
            logger.warning(f"🛡️  Risk check BLOCKED: {entry_orders_count} pending entry order(s) for {symbol} >= limit {max_pending}")
            return False, f"Already have {entry_orders_count} pending entry order(s) for {symbol} (limit: {max_pending})"
        
        # 6. Check total exposure (position + pending entry orders + new order)
        # CRITICAL: Must enforce max_quantity limit to prevent excessive position sizes
        try:
            position_qty = await self._get_current_position_quantity(symbol)
            # Use the quantity we just counted above (entry_orders_qty)
            pending_qty = entry_orders_qty
            
            logger.info(f"🔍 RISK CHECK: EXPOSURE for {symbol}: position={position_qty}, pending_entry={pending_qty}, "
                       f"new_order={quantity}, total_if_placed={position_qty + pending_qty + quantity}, limit={max_quantity}")
            
            # Check 1: pending entry orders >= max_quantity
            if pending_qty >= max_quantity:
                self._recent_attempts[symbol][side] = now
                self._last_order_attempt[cooldown_key] = now
                logger.warning(f"🛡️  Risk check BLOCKED: Pending entry orders {pending_qty} >= max_quantity {max_quantity}")
                return False, f"Pending entry orders {pending_qty} >= max_quantity {max_quantity}"
            
            # Check 2: position >= max_quantity
            if position_qty >= max_quantity:
                self._recent_attempts[symbol][side] = now
                self._last_order_attempt[cooldown_key] = now
                logger.warning(f"🛡️  Risk check BLOCKED: Position {position_qty} >= max_quantity {max_quantity}")
                return False, f"Position {position_qty} >= max_quantity {max_quantity}"
            
            # Check 3: position + pending >= max_quantity
            if position_qty + pending_qty >= max_quantity:
                self._recent_attempts[symbol][side] = now
                self._last_order_attempt[cooldown_key] = now
                logger.warning(f"🛡️  Risk check BLOCKED: Total exposure {position_qty + pending_qty} "
                             f"(pos={position_qty} + pending={pending_qty}) >= max_quantity {max_quantity}")
                return False, f"Total exposure {position_qty + pending_qty} >= max_quantity {max_quantity}"
            
            # Check 4: position + pending + new order > max_quantity
            if position_qty + pending_qty + quantity > max_quantity:
                self._recent_attempts[symbol][side] = now
                self._last_order_attempt[cooldown_key] = now
                logger.warning(f"🛡️  Risk check BLOCKED: Would exceed max_quantity: "
                             f"{position_qty + pending_qty + quantity} = pos({position_qty}) + pending({pending_qty}) + new({quantity}) > {max_quantity}")
                return False, (f"Would exceed max_quantity: {position_qty + pending_qty + quantity} > {max_quantity} "
                              f"(pos={position_qty} + pending={pending_qty} + new={quantity})")
        except Exception as e:
            logger.error(f"❌ CRITICAL ERROR checking exposure for {symbol}: {e}")
            import traceback
            logger.error(traceback.format_exc())
            # FAIL CLOSED: Block order if we can't verify exposure (safer than allowing)
            return False, f"Cannot verify exposure limits: {e}"
        
        return True, "Order allowed"
    
    async def record_order_placement(self, symbol: str, side: str) -> None:
        """
        Record that an order was placed (for cooldown tracking).
        
        Args:
            symbol: Trading symbol
            side: "BUY" or "SELL"
        """
        symbol = symbol.upper()
        now = datetime.now(timezone.utc)
        self._last_order_placement[symbol][side] = now
        # Also update flat dict format for strategy compatibility
        cooldown_key = f"{symbol}_{side}"
        self._last_order_attempt[cooldown_key] = now
    
    async def _get_total_exposure(self, symbol: str, open_orders: Optional[List[Dict]] = None) -> int:
        """Get total exposure (positions + pending entry orders) for a symbol."""
        position_qty = await self._get_current_position_quantity(symbol)
        pending_qty = await self._get_pending_entry_order_quantity(symbol, open_orders)
        return position_qty + pending_qty
    
    async def _get_current_position_quantity(self, symbol: str) -> int:
        """
        Get current position quantity for a symbol.
        
        CRITICAL: Always fetch fresh positions directly from API (bypass cache) for risk checks
        to ensure we see the most recent positions.
        """
        try:
            # Always fetch directly from API to get the latest positions (bypass cache for risk checks)
            positions = await self.trading_bot.get_open_positions()
                
            if not isinstance(positions, list):
                return 0
            
            total_qty = 0
            symbol_upper = symbol.upper()
            
            for pos in positions:
                if not isinstance(pos, dict):
                    continue
                
                # Extract symbol from position - try multiple fields
                pos_symbol = pos.get('symbol', '').upper()
                if not pos_symbol:
                    # Try symbolId field (e.g., "F.US.MGC" -> "MGC")
                    symbol_id = pos.get('symbolId', '')
                    if symbol_id:
                        parts = symbol_id.split('.')
                        pos_symbol = parts[-1].upper() if parts else ''
                if not pos_symbol:
                    # Try contract ID as last resort
                    contract_id = pos.get('contractId')
                    if contract_id and hasattr(self.trading_bot, '_get_symbol_from_contract_id'):
                        pos_symbol = self.trading_bot._get_symbol_from_contract_id(contract_id)
                
                if pos_symbol == symbol_upper:
                    qty = pos.get('quantity') or pos.get('size') or 0
                    if qty:
                        total_qty += abs(int(qty))
            
            return total_qty
        except Exception as e:
            logger.warning(f"⚠️  Could not get position quantity for {symbol}: {e}")
            return 0
    
    async def _get_pending_entry_order_quantity(self, symbol: str, open_orders: Optional[List[Dict]] = None) -> int:
        """
        Get total quantity of pending entry orders for a symbol.
        
        Counts ALL stop orders (type 4) that are:
        - Not reduce-only
        - Not explicitly bracket SL/TP orders (identified by customTag containing '-SL' or '-TP')
        
        CRITICAL: This counts the quantity of contracts in pending entry orders,
        which is used to enforce max_quantity limits.
        """
        try:
            symbol_upper = symbol.upper()
            
            if open_orders is None:
                # Always fetch fresh orders directly from API (bypass cache for risk checks)
                orders = await self.trading_bot.get_open_orders()
                orders_list = orders if isinstance(orders, list) else []
            else:
                orders_list = open_orders
            
            total_quantity = 0
            
            for order in orders_list:
                if not isinstance(order, dict):
                    continue
                
                # Extract symbol from order - try multiple fields
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
                
                order_type = order.get('type') or order.get('raw_type')
                if order_type not in (4, "4", "Stop", "stop"):
                    continue
                
                is_reduce_only = order.get('reduceOnly') or order.get('reduce_only', False)
                if is_reduce_only:
                    continue
                
                custom_tag = order.get('customTag') or order.get('custom_tag') or ''
                is_bracket_sl_tp = '-SL' in str(custom_tag) or '-TP' in str(custom_tag)
                
                # Count entry stop orders (not bracket SL/TP)
                if not is_bracket_sl_tp:
                    qty = order.get('quantity') or order.get('size') or 0
                    if qty:
                        total_quantity += abs(int(qty))
            
            return total_quantity
        except Exception as e:
            logger.warning(f"⚠️  Error getting pending entry order quantity for {symbol}: {e}")
            return 0

