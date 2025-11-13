"""
Redis Cache Manager for Trading Bot

Provides fast in-memory caching for:
- Real-time quotes (<1ms, TTL=5s)
- Recent bars (hot data) (<1ms, TTL=60s)
- Active positions (<1ms, no TTL)
- Rate limit counters (<1ms, TTL=60s)
- Session data (dashboard) (<1ms, TTL=24h)

Falls back gracefully if Redis is unavailable.
"""

import os
import json
import logging
import time
from typing import Optional, Dict, List, Any, Union
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

try:
    import redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False
    logger.warning("⚠️  Redis not installed - cache will use in-memory fallback only")


class RedisCacheManager:
    """
    Redis cache manager with graceful fallback.
    
    Features:
    - Automatic connection pooling
    - TTL support for automatic expiration
    - JSON serialization for complex objects
    - Graceful degradation if Redis unavailable
    """
    
    def __init__(self):
        """Initialize Redis cache manager."""
        self.client: Optional[redis.Redis] = None
        self._fallback_cache: Dict[str, tuple] = {}  # (value, expiry_time)
        self._enabled = False
        
        if not REDIS_AVAILABLE:
            logger.warning("⚠️  Redis library not available - using in-memory fallback")
            return
        
        try:
            # Get Redis connection URL from environment
            redis_url = os.getenv('REDIS_URL') or os.getenv('REDISCLOUD_URL')
            
            if redis_url:
                # Parse Redis URL (supports redis:// and rediss://)
                self.client = redis.from_url(
                    redis_url,
                    decode_responses=True,  # Automatically decode bytes to strings
                    socket_connect_timeout=2,
                    socket_timeout=2,
                    retry_on_timeout=True,
                    health_check_interval=30,
                )
                
                # Test connection
                self.client.ping()
                self._enabled = True
                logger.info("✅ Redis cache connected successfully")
            else:
                logger.info("ℹ️  REDIS_URL not set - Redis cache disabled (using fallback)")
        except Exception as e:
            logger.warning(f"⚠️  Failed to connect to Redis: {e} - using in-memory fallback")
            self.client = None
    
    def is_enabled(self) -> bool:
        """Check if Redis is enabled and connected."""
        return self._enabled and self.client is not None
    
    def _serialize(self, value: Any) -> str:
        """Serialize value to JSON string."""
        if isinstance(value, str):
            return value
        return json.dumps(value, default=str)
    
    def _deserialize(self, value: str) -> Any:
        """Deserialize JSON string to Python object."""
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return value
    
    def get(self, key: str, default: Any = None) -> Any:
        """
        Get value from cache.
        
        Args:
            key: Cache key
            default: Default value if key not found
            
        Returns:
            Cached value or default
        """
        if self.is_enabled():
            try:
                value = self.client.get(key)
                if value is None:
                    return default
                return self._deserialize(value)
            except Exception as e:
                logger.debug(f"Redis get error for {key}: {e}")
                # Fall through to fallback
        
        # Fallback to in-memory cache
        if key in self._fallback_cache:
            value, expiry = self._fallback_cache[key]
            if expiry is None or time.time() < expiry:
                return self._deserialize(value) if isinstance(value, str) else value
            else:
                # Expired
                del self._fallback_cache[key]
        
        return default
    
    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> bool:
        """
        Set value in cache with optional TTL.
        
        Args:
            key: Cache key
            value: Value to cache
            ttl: Time to live in seconds (None = no expiration)
            
        Returns:
            True if successful, False otherwise
        """
        serialized = self._serialize(value)
        
        if self.is_enabled():
            try:
                if ttl:
                    self.client.setex(key, ttl, serialized)
                else:
                    self.client.set(key, serialized)
                return True
            except Exception as e:
                logger.debug(f"Redis set error for {key}: {e}")
                # Fall through to fallback
        
        # Fallback to in-memory cache
        expiry = None if ttl is None else time.time() + ttl
        self._fallback_cache[key] = (serialized, expiry)
        
        # Clean up expired entries periodically (simple cleanup)
        if len(self._fallback_cache) > 1000:
            now = time.time()
            self._fallback_cache = {
                k: v for k, v in self._fallback_cache.items()
                if v[1] is None or v[1] > now
            }
        
        return True
    
    def delete(self, key: str) -> bool:
        """Delete key from cache."""
        if self.is_enabled():
            try:
                self.client.delete(key)
                return True
            except Exception as e:
                logger.debug(f"Redis delete error for {key}: {e}")
        
        # Fallback
        if key in self._fallback_cache:
            del self._fallback_cache[key]
        return True
    
    def exists(self, key: str) -> bool:
        """Check if key exists in cache."""
        if self.is_enabled():
            try:
                return bool(self.client.exists(key))
            except Exception as e:
                logger.debug(f"Redis exists error for {key}: {e}")
        
        # Fallback
        if key in self._fallback_cache:
            value, expiry = self._fallback_cache[key]
            if expiry is None or time.time() < expiry:
                return True
            else:
                del self._fallback_cache[key]
        return False
    
    def increment(self, key: str, amount: int = 1, ttl: Optional[int] = None) -> int:
        """
        Increment counter in cache.
        
        Args:
            key: Cache key
            amount: Amount to increment by
            ttl: Optional TTL for the key
            
        Returns:
            New counter value
        """
        if self.is_enabled():
            try:
                new_value = self.client.incrby(key, amount)
                if ttl and not self.client.ttl(key):
                    self.client.expire(key, ttl)
                return new_value
            except Exception as e:
                logger.debug(f"Redis increment error for {key}: {e}")
        
        # Fallback
        current = self.get(key, 0)
        new_value = int(current) + amount
        self.set(key, new_value, ttl)
        return new_value
    
    def get_many(self, keys: List[str]) -> Dict[str, Any]:
        """Get multiple keys at once."""
        result = {}
        
        if self.is_enabled():
            try:
                values = self.client.mget(keys)
                for key, value in zip(keys, values):
                    if value is not None:
                        result[key] = self._deserialize(value)
            except Exception as e:
                logger.debug(f"Redis mget error: {e}")
        
        # Fallback
        for key in keys:
            value = self.get(key)
            if value is not None:
                result[key] = value
        
        return result
    
    def set_many(self, mapping: Dict[str, Any], ttl: Optional[int] = None) -> bool:
        """Set multiple keys at once."""
        if self.is_enabled():
            try:
                pipeline = self.client.pipeline()
                for key, value in mapping.items():
                    serialized = self._serialize(value)
                    if ttl:
                        pipeline.setex(key, ttl, serialized)
                    else:
                        pipeline.set(key, serialized)
                pipeline.execute()
                return True
            except Exception as e:
                logger.debug(f"Redis mset error: {e}")
        
        # Fallback
        for key, value in mapping.items():
            self.set(key, value, ttl)
        return True
    
    def clear_pattern(self, pattern: str) -> int:
        """
        Delete all keys matching pattern.
        
        Args:
            pattern: Redis pattern (e.g., 'positions:*')
            
        Returns:
            Number of keys deleted
        """
        if self.is_enabled():
            try:
                keys = list(self.client.scan_iter(match=pattern))
                if keys:
                    return self.client.delete(*keys)
                return 0
            except Exception as e:
                logger.debug(f"Redis clear_pattern error for {pattern}: {e}")
        
        # Fallback
        count = 0
        keys_to_delete = [k for k in self._fallback_cache.keys() if self._match_pattern(k, pattern)]
        for key in keys_to_delete:
            del self._fallback_cache[key]
            count += 1
        return count
    
    def _match_pattern(self, key: str, pattern: str) -> bool:
        """Simple pattern matching for fallback cache."""
        import fnmatch
        return fnmatch.fnmatch(key, pattern)
    
    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        stats = {
            'enabled': self.is_enabled(),
            'fallback_mode': not self.is_enabled(),
            'fallback_size': len(self._fallback_cache),
        }
        
        if self.is_enabled():
            try:
                info = self.client.info('stats')
                stats.update({
                    'keyspace_hits': info.get('keyspace_hits', 0),
                    'keyspace_misses': info.get('keyspace_misses', 0),
                    'total_keys': self.client.dbsize(),
                })
            except Exception as e:
                logger.debug(f"Redis stats error: {e}")
        
        return stats


# Global Redis cache instance
_redis_cache: Optional[RedisCacheManager] = None


def get_redis_cache() -> RedisCacheManager:
    """Get or create global Redis cache instance."""
    global _redis_cache
    if _redis_cache is None:
        _redis_cache = RedisCacheManager()
    return _redis_cache

