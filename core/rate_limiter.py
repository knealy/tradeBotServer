"""
Rate Limiter - Sliding window rate limiting for API calls with exponential backoff.

Prevents API rate limit violations by tracking calls within a time window.
Handles 429 errors with exponential backoff and jitter.
"""

import time
import random
import logging
import asyncio
from threading import Lock
from collections import deque
from typing import Optional, Dict

logger = logging.getLogger(__name__)


class RateLimiter:
    """
    Rate limiter using sliding window algorithm with exponential backoff for 429 errors.
    Prevents API rate limit violations by tracking calls within a time window.
    """
    
    def __init__(self, max_calls: int = 60, period: int = 60):
        """
        Initialize rate limiter.
        
        Args:
            max_calls: Maximum number of calls allowed in the period
            period: Time period in seconds (default: 60 seconds)
        """
        self.max_calls = max_calls
        self.period = period
        self.calls = deque()
        self.lock = Lock()
        
        # Exponential backoff tracking for 429 errors
        self._429_retry_count: Dict[str, int] = {}  # endpoint -> retry_count
        self._429_last_error_time: Dict[str, float] = {}  # endpoint -> timestamp
        self._base_backoff_delay = 1.0  # Base delay in seconds
        self._max_backoff_delay = 60.0  # Maximum delay in seconds
        self._jitter_range = 1.0  # Random jitter range in seconds
        
        logger.debug(f"Rate limiter initialized: {max_calls} calls per {period} seconds")
    
    def acquire(self) -> None:
        """
        Acquire permission to make an API call.
        Blocks if necessary until rate limit allows the call.
        """
        with self.lock:
            now = time.time()
            
            # Remove calls older than the period
            while self.calls and self.calls[0] < now - self.period:
                self.calls.popleft()
            
            # If we're at the limit, wait until the oldest call expires
            if len(self.calls) >= self.max_calls:
                sleep_time = self.period - (now - self.calls[0])
                if sleep_time > 0:
                    logger.debug(f"Rate limit reached, waiting {sleep_time:.2f}s before next API call")
                    time.sleep(sleep_time)
                    # Update now after sleep
                    now = time.time()
                    # Remove any additional expired calls
                    while self.calls and self.calls[0] < now - self.period:
                        self.calls.popleft()
            
            # Record this call
            self.calls.append(now)
    
    def get_backoff_delay(self, endpoint: str = "default") -> float:
        """
        Calculate exponential backoff delay with jitter for 429 errors.
        
        Args:
            endpoint: Endpoint identifier for tracking retries per endpoint
            
        Returns:
            Delay in seconds before retrying
        """
        with self.lock:
            retry_count = self._429_retry_count.get(endpoint, 0)
            
            # Exponential backoff: base_delay * (2 ** retry_count)
            backoff_delay = self._base_backoff_delay * (2 ** retry_count)
            
            # Cap at maximum delay
            backoff_delay = min(backoff_delay, self._max_backoff_delay)
            
            # Add jitter to prevent thundering herd
            jitter = random.uniform(0, self._jitter_range)
            total_delay = backoff_delay + jitter
            
            logger.debug(f"429 backoff for {endpoint}: retry_count={retry_count}, delay={total_delay:.2f}s")
            
            return total_delay
    
    def record_429_error(self, endpoint: str = "default") -> None:
        """
        Record a 429 error and increment retry count for exponential backoff.
        
        Args:
            endpoint: Endpoint identifier
        """
        with self.lock:
            self._429_retry_count[endpoint] = self._429_retry_count.get(endpoint, 0) + 1
            self._429_last_error_time[endpoint] = time.time()
            logger.warning(f"429 error recorded for {endpoint}, retry_count={self._429_retry_count[endpoint]}")
    
    def reset_429_backoff(self, endpoint: str = "default") -> None:
        """
        Reset 429 backoff counter after successful request.
        
        Args:
            endpoint: Endpoint identifier
        """
        with self.lock:
            if endpoint in self._429_retry_count:
                old_count = self._429_retry_count.pop(endpoint, 0)
                if old_count > 0:
                    logger.debug(f"429 backoff reset for {endpoint} (was at retry_count={old_count})")
            if endpoint in self._429_last_error_time:
                self._429_last_error_time.pop(endpoint)
    
    async def wait_for_429_backoff(self, endpoint: str = "default") -> None:
        """
        Wait for exponential backoff delay after 429 error (async version).
        
        Args:
            endpoint: Endpoint identifier
        """
        delay = self.get_backoff_delay(endpoint)
        logger.info(f"⏳ Waiting {delay:.2f}s before retrying {endpoint} after 429 error")
        await asyncio.sleep(delay)
    
    def get_remaining_calls(self) -> int:
        """
        Get number of remaining calls in current period.
        
        Returns:
            Number of remaining calls
        """
        with self.lock:
            now = time.time()
            # Remove expired calls
            while self.calls and self.calls[0] < now - self.period:
                self.calls.popleft()
            return max(0, self.max_calls - len(self.calls))
    
    def reset(self) -> None:
        """Reset the rate limiter (clear call history and 429 backoff)."""
        with self.lock:
            self.calls.clear()
            self._429_retry_count.clear()
            self._429_last_error_time.clear()
            logger.debug("Rate limiter reset")

