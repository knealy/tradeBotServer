"""
Strategy Result Caching - Avoid redundant calculations

Provides caching decorators and utilities for expensive strategy calculations.
"""

import time
import logging
from functools import wraps
from typing import Callable, Any, Optional, Dict, Tuple
from collections import OrderedDict

logger = logging.getLogger(__name__)


class TTLCache:
    """
    Time-to-live cache with max size limit.
    
    Automatically expires entries after TTL seconds.
    Implements LRU eviction when max_size is reached.
    """
    
    def __init__(self, max_size: int = 1000, ttl: float = 5.0):
        """
        Initialize TTL cache.
        
        Args:
            max_size: Maximum number of entries
            ttl: Time-to-live in seconds
        """
        self.max_size = max_size
        self.ttl = ttl
        self._cache: OrderedDict = OrderedDict()
        self._timestamps: Dict = {}
    
    def get(self, key: Any) -> Optional[Any]:
        """
        Get value from cache if not expired.
        
        Args:
            key: Cache key
            
        Returns:
            Cached value or None if expired/missing
        """
        if key not in self._cache:
            return None
        
        # Check if expired
        timestamp = self._timestamps.get(key, 0)
        if time.time() - timestamp > self.ttl:
            # Expired - remove it
            del self._cache[key]
            del self._timestamps[key]
            return None
        
        # Move to end (LRU)
        self._cache.move_to_end(key)
        return self._cache[key]
    
    def set(self, key: Any, value: Any):
        """
        Set value in cache.
        
        Args:
            key: Cache key
            value: Value to cache
        """
        # Evict oldest if at max size
        if len(self._cache) >= self.max_size and key not in self._cache:
            oldest_key = next(iter(self._cache))
            del self._cache[oldest_key]
            del self._timestamps[oldest_key]
        
        self._cache[key] = value
        self._timestamps[key] = time.time()
        self._cache.move_to_end(key)
    
    def clear(self):
        """Clear all cache entries."""
        self._cache.clear()
        self._timestamps.clear()
    
    def size(self) -> int:
        """Get current cache size."""
        return len(self._cache)


def cache_result(ttl: float = 5.0, max_size: int = 100):
    """
    Decorator to cache function results with TTL.
    
    Args:
        ttl: Time-to-live in seconds
        max_size: Maximum cache entries
    
    Example:
        @cache_result(ttl=10.0)
        def calculate_ema(self, data, period):
            # Expensive calculation
            return ema_value
    """
    cache = TTLCache(max_size=max_size, ttl=ttl)
    
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            # Create cache key from args and kwargs
            # Skip 'self' argument if present
            cache_args = args[1:] if args and hasattr(args[0], '__class__') else args
            cache_key = (cache_args, tuple(sorted(kwargs.items())))
            
            # Try cache first
            cached = cache.get(cache_key)
            if cached is not None:
                logger.debug(f"📦 Cache HIT: {func.__name__}")
                return cached
            
            # Cache miss - calculate
            logger.debug(f"📦 Cache MISS: {func.__name__}")
            result = func(*args, **kwargs)
            
            # Store in cache
            cache.set(cache_key, result)
            return result
        
        # Add cache control methods
        wrapper.cache_clear = cache.clear
        wrapper.cache_size = cache.size
        
        return wrapper
    
    return decorator


def cache_async_result(ttl: float = 5.0, max_size: int = 100):
    """
    Decorator to cache async function results with TTL.
    
    Args:
        ttl: Time-to-live in seconds
        max_size: Maximum cache entries
    
    Example:
        @cache_async_result(ttl=10.0)
        async def fetch_historical_data(self, symbol, timeframe):
            # Expensive API call
            return data
    """
    cache = TTLCache(max_size=max_size, ttl=ttl)
    
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # Create cache key from args and kwargs
            # Skip 'self' argument if present
            cache_args = args[1:] if args and hasattr(args[0], '__class__') else args
            cache_key = (cache_args, tuple(sorted(kwargs.items())))
            
            # Try cache first
            cached = cache.get(cache_key)
            if cached is not None:
                logger.debug(f"📦 Cache HIT: {func.__name__}")
                return cached
            
            # Cache miss - calculate
            logger.debug(f"📦 Cache MISS: {func.__name__}")
            result = await func(*args, **kwargs)
            
            # Store in cache
            cache.set(cache_key, result)
            return result
        
        # Add cache control methods
        wrapper.cache_clear = cache.clear
        wrapper.cache_size = cache.size
        
        return wrapper
    
    return decorator


class StrategyCache:
    """
    Centralized cache for strategy calculations.
    
    Provides type-specific caches with appropriate TTLs.
    """
    
    def __init__(self):
        """Initialize strategy cache."""
        self.indicator_cache = TTLCache(max_size=500, ttl=10.0)  # Indicators: 10s TTL
        self.market_data_cache = TTLCache(max_size=100, ttl=2.0)  # Market data: 2s TTL
        self.signal_cache = TTLCache(max_size=200, ttl=1.0)  # Signals: 1s TTL
        self.historical_cache = TTLCache(max_size=50, ttl=60.0)  # Historical: 60s TTL
    
    def get_indicator(self, symbol: str, indicator_name: str, period: int) -> Optional[float]:
        """Get cached indicator value."""
        key = (symbol, indicator_name, period)
        return self.indicator_cache.get(key)
    
    def set_indicator(self, symbol: str, indicator_name: str, period: int, value: float):
        """Cache indicator value."""
        key = (symbol, indicator_name, period)
        self.indicator_cache.set(key, value)
    
    def get_market_data(self, symbol: str, data_type: str) -> Optional[Any]:
        """Get cached market data."""
        key = (symbol, data_type)
        return self.market_data_cache.get(key)
    
    def set_market_data(self, symbol: str, data_type: str, value: Any):
        """Cache market data."""
        key = (symbol, data_type)
        self.market_data_cache.set(key, value)
    
    def clear_all(self):
        """Clear all caches."""
        self.indicator_cache.clear()
        self.market_data_cache.clear()
        self.signal_cache.clear()
        self.historical_cache.clear()
    
    def get_stats(self) -> Dict[str, int]:
        """Get cache statistics."""
        return {
            'indicators': self.indicator_cache.size(),
            'market_data': self.market_data_cache.size(),
            'signals': self.signal_cache.size(),
            'historical': self.historical_cache.size()
        }
