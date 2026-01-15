"""
Performance Timing Utilities

Provides lightweight performance instrumentation for measuring latency
and identifying bottlenecks in critical trading operations.
"""

import time
import logging
from typing import Optional, Dict, List
from dataclasses import dataclass, field
from contextlib import contextmanager
from collections import defaultdict
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class TimingStats:
    """Statistics for a named operation."""
    name: str
    count: int = 0
    total_time: float = 0.0
    min_time: float = float('inf')
    max_time: float = 0.0
    last_time: float = 0.0
    
    @property
    def avg_time(self) -> float:
        """Average time per operation."""
        return self.total_time / self.count if self.count > 0 else 0.0
    
    def record(self, elapsed: float):
        """Record a timing measurement."""
        self.count += 1
        self.total_time += elapsed
        self.min_time = min(self.min_time, elapsed)
        self.max_time = max(self.max_time, elapsed)
        self.last_time = elapsed
    
    def __str__(self) -> str:
        """String representation."""
        if self.count == 0:
            return f"{self.name}: no data"
        return (
            f"{self.name}: "
            f"avg={self.avg_time*1000:.1f}ms, "
            f"min={self.min_time*1000:.1f}ms, "
            f"max={self.max_time*1000:.1f}ms, "
            f"count={self.count}"
        )


class PerformanceTimer:
    """
    Lightweight performance timer for measuring operation latency.
    
    Usage:
        timer = PerformanceTimer()
        
        with timer.measure("api_call"):
            await some_api_call()
        
        # Or manual timing
        timer.start("complex_operation")
        # ... do work ...
        timer.end("complex_operation")
        
        # Get statistics
        timer.log_stats()
    """
    
    def __init__(self, name: str = "PerformanceTimer", log_threshold_ms: float = 100.0):
        """
        Initialize performance timer.
        
        Args:
            name: Identifier for this timer instance
            log_threshold_ms: Log individual operations that exceed this threshold (ms)
        """
        self.name = name
        self.log_threshold_ms = log_threshold_ms
        self._stats: Dict[str, TimingStats] = {}
        self._active_timers: Dict[str, float] = {}
        self._enabled = True
    
    def enable(self):
        """Enable timing measurements."""
        self._enabled = True
    
    def disable(self):
        """Disable timing measurements (for production if needed)."""
        self._enabled = False
    
    def start(self, operation: str):
        """Start timing an operation."""
        if not self._enabled:
            return
        self._active_timers[operation] = time.perf_counter()
    
    def end(self, operation: str, log_result: bool = True) -> Optional[float]:
        """
        End timing an operation and record the result.
        
        Args:
            operation: Name of the operation
            log_result: Whether to log slow operations
        
        Returns:
            Elapsed time in seconds, or None if timer wasn't started
        """
        if not self._enabled:
            return None
        
        start_time = self._active_timers.pop(operation, None)
        if start_time is None:
            # Silently ignore if timer wasn't started (common with cached operations)
            logger.debug(f"Timer '{operation}' was not started (likely cached operation)")
            return None
        
        elapsed = time.perf_counter() - start_time
        
        # Record stats
        if operation not in self._stats:
            self._stats[operation] = TimingStats(name=operation)
        self._stats[operation].record(elapsed)
        
        # Log if exceeds threshold
        if log_result and elapsed * 1000 > self.log_threshold_ms:
            logger.debug(f"⏱️  {operation}: {elapsed*1000:.1f}ms")
        
        return elapsed
    
    @contextmanager
    def measure(self, operation: str, log_result: bool = True):
        """
        Context manager for timing an operation.
        
        Usage:
            with timer.measure("my_operation"):
                do_work()
        """
        if not self._enabled:
            yield None
            return
        
        self.start(operation)
        try:
            yield
        finally:
            self.end(operation, log_result=log_result)
    
    def get_stats(self, operation: str) -> Optional[TimingStats]:
        """Get statistics for a specific operation."""
        return self._stats.get(operation)
    
    def get_all_stats(self) -> Dict[str, TimingStats]:
        """Get all collected statistics."""
        return dict(self._stats)
    
    def log_stats(self, top_n: int = 10):
        """
        Log performance statistics for all operations.
        
        Args:
            top_n: Number of slowest operations to highlight
        """
        if not self._stats:
            logger.info(f"📊 {self.name}: No timing data collected")
            return
        
        logger.info(f"📊 {self.name} Performance Statistics:")
        
        # Sort by average time (slowest first)
        sorted_stats = sorted(
            self._stats.values(),
            key=lambda s: s.avg_time,
            reverse=True
        )
        
        for i, stats in enumerate(sorted_stats[:top_n]):
            prefix = f"  🔴 #{i+1}" if i < 3 else f"     #{i+1}"
            logger.info(f"{prefix} {stats}")
        
        if len(sorted_stats) > top_n:
            logger.info(f"  ... and {len(sorted_stats) - top_n} more operations")
    
    def reset(self):
        """Reset all collected statistics."""
        self._stats.clear()
        self._active_timers.clear()
    
    def summary(self) -> str:
        """Get a one-line summary of timing data."""
        if not self._stats:
            return f"{self.name}: no data"
        
        total_ops = sum(s.count for s in self._stats.values())
        total_time = sum(s.total_time for s in self._stats.values())
        
        return (
            f"{self.name}: {len(self._stats)} operations, "
            f"{total_ops} calls, {total_time*1000:.1f}ms total"
        )


# Global timer instance for convenience
_global_timer = PerformanceTimer(name="Global", log_threshold_ms=50.0)


def get_global_timer() -> PerformanceTimer:
    """Get the global performance timer instance."""
    return _global_timer


@contextmanager
def time_operation(operation: str, log_result: bool = True):
    """
    Convenience function for timing operations with the global timer.
    
    Usage:
        with time_operation("fetch_orders"):
            orders = await fetch_orders()
    """
    with _global_timer.measure(operation, log_result=log_result):
        yield


def log_performance_stats(top_n: int = 10):
    """Log global performance statistics."""
    _global_timer.log_stats(top_n=top_n)
