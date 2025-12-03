"""
Performance Timing Infrastructure

Provides timing decorators and utilities for measuring function execution time,
API call latency, and identifying performance bottlenecks.
"""

import time
import functools
import logging
from typing import Callable, Any, Dict, Optional
from contextlib import contextmanager
from dataclasses import dataclass, field
from collections import defaultdict
import statistics

logger = logging.getLogger(__name__)

# Global timing registry
_timing_registry: Dict[str, list] = defaultdict(list)
_threshold_warnings: Dict[str, float] = {}


@dataclass
class TimingResult:
    """Result of a timed operation"""
    function_name: str
    duration_ms: float
    timestamp: float
    success: bool
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


def set_timing_threshold(function_name: str, threshold_ms: float):
    """Set a threshold for a function - warnings will be logged if exceeded"""
    _threshold_warnings[function_name] = threshold_ms


def get_timing_stats(function_name: str) -> Dict[str, float]:
    """Get timing statistics for a function"""
    timings = _timing_registry.get(function_name, [])
    if not timings:
        return {}
    
    durations = [t.duration_ms for t in timings]
    return {
        'count': len(durations),
        'min_ms': min(durations),
        'max_ms': max(durations),
        'mean_ms': statistics.mean(durations),
        'median_ms': statistics.median(durations),
        'p95_ms': statistics.quantiles(durations, n=20)[18] if len(durations) > 20 else max(durations),
        'p99_ms': statistics.quantiles(durations, n=100)[98] if len(durations) > 100 else max(durations),
    }


def clear_timing_stats(function_name: Optional[str] = None):
    """Clear timing statistics for a function or all functions"""
    if function_name:
        _timing_registry.pop(function_name, None)
    else:
        _timing_registry.clear()


@contextmanager
def time_operation(operation_name: str, metadata: Optional[Dict[str, Any]] = None):
    """
    Context manager for timing operations.
    
    Usage:
        with time_operation('place_order', {'symbol': 'MNQ', 'quantity': 1}):
            # operation code
            pass
    """
    start_time = time.perf_counter()
    success = True
    error = None
    
    try:
        yield
    except Exception as e:
        success = False
        error = str(e)
        raise
    finally:
        duration_ms = (time.perf_counter() - start_time) * 1000
        result = TimingResult(
            function_name=operation_name,
            duration_ms=duration_ms,
            timestamp=time.time(),
            success=success,
            error=error,
            metadata=metadata or {}
        )
        
        _timing_registry[operation_name].append(result)
        
        # Keep only last 1000 timings per function
        if len(_timing_registry[operation_name]) > 1000:
            _timing_registry[operation_name] = _timing_registry[operation_name][-1000:]
        
        # Check threshold
        threshold = _threshold_warnings.get(operation_name)
        if threshold and duration_ms > threshold:
            logger.warning(
                f"⏱️ SLOW OPERATION: {operation_name} took {duration_ms:.2f}ms "
                f"(threshold: {threshold:.2f}ms)"
            )
        
        # Log slow operations
        if duration_ms > 100:  # Log anything over 100ms
            logger.info(f"⏱️ {operation_name} took {duration_ms:.2f}ms")


def time_function(threshold_ms: Optional[float] = None):
    """
    Decorator for timing function execution.
    
    Usage:
        @time_function(threshold_ms=50.0)
        async def place_order(...):
            ...
    """
    def decorator(func: Callable) -> Callable:
        func_name = f"{func.__module__}.{func.__qualname__}"
        
        if threshold_ms:
            set_timing_threshold(func_name, threshold_ms)
        
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            with time_operation(func_name, {'args': str(args)[:100], 'kwargs': str(kwargs)[:100]}):
                return await func(*args, **kwargs)
        
        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            with time_operation(func_name, {'args': str(args)[:100], 'kwargs': str(kwargs)[:100]}):
                return func(*args, **kwargs)
        
        if hasattr(func, '__code__') and 'async' in str(type(func)):
            return async_wrapper
        else:
            return sync_wrapper
    
    return decorator


def time_api_call(api_name: str, threshold_ms: Optional[float] = None):
    """
    Decorator specifically for API calls.
    
    Usage:
        @time_api_call('TopStepX.place_order', threshold_ms=50.0)
        async def place_order(...):
            ...
    """
    def decorator(func: Callable) -> Callable:
        if threshold_ms:
            set_timing_threshold(api_name, threshold_ms)
        
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            metadata = {
                'api_name': api_name,
                'endpoint': getattr(func, '__name__', 'unknown'),
            }
            with time_operation(api_name, metadata):
                return await func(*args, **kwargs)
        
        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            metadata = {
                'api_name': api_name,
                'endpoint': getattr(func, '__name__', 'unknown'),
            }
            with time_operation(api_name, metadata):
                return func(*args, **kwargs)
        
        if hasattr(func, '__code__') and 'async' in str(type(func)):
            return async_wrapper
        else:
            return sync_wrapper
    
    return decorator


def get_all_timing_stats() -> Dict[str, Dict[str, float]]:
    """Get timing statistics for all timed functions"""
    return {
        func_name: get_timing_stats(func_name)
        for func_name in _timing_registry.keys()
    }


def log_timing_summary():
    """Log a summary of all timing statistics"""
    stats = get_all_timing_stats()
    if not stats:
        logger.info("No timing data available")
        return
    
    logger.info("=" * 80)
    logger.info("PERFORMANCE TIMING SUMMARY")
    logger.info("=" * 80)
    
    for func_name, func_stats in sorted(stats.items(), key=lambda x: x[1].get('mean_ms', 0), reverse=True):
        logger.info(
            f"{func_name:50s} | "
            f"Count: {func_stats.get('count', 0):5d} | "
            f"Mean: {func_stats.get('mean_ms', 0):8.2f}ms | "
            f"P95: {func_stats.get('p95_ms', 0):8.2f}ms | "
            f"Max: {func_stats.get('max_ms', 0):8.2f}ms"
        )
    
    logger.info("=" * 80)

