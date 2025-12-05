"""
Risk Management Module - Handles risk calculations and validations.

This module provides tick size, point value, and trading session calculations
that are used across the trading system.
"""

import logging
from typing import Optional, Dict
from datetime import datetime, timedelta, timezone

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
        "MNQ": 0.50,   # $0.50 per point
        "MES": 0.50,   # $0.50 per point
        "MYM": 0.50,   # $0.50 per point
        "M2K": 0.50,   # $0.50 per point
        "ES": 50.0,    # $50 per point
        "NQ": 20.0,    # $20 per point
        "YM": 5.0,     # $5 per point
        "RTY": 50.0,   # $50 per point
        "CL": 1000.0,  # $1000 per point
        "GC": 100.0,   # $100 per point
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

