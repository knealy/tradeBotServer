"""
Rate Limiter - Sliding window rate limiting for API calls.

Prevents API rate limit violations by tracking calls within a time window.
"""

import time
import logging
from threading import Lock
from collections import deque

logger = logging.getLogger(__name__)


class RateLimiter:
    """
    Rate limiter using sliding window algorithm.
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
        """Reset the rate limiter (clear call history)."""
        with self.lock:
            self.calls.clear()
            logger.debug("Rate limiter reset")

