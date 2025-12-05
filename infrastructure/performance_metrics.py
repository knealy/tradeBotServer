"""
Performance Metrics & Monitoring Module

Tracks API calls, cache performance, memory usage, and execution times.
Provides real-time visibility into bot performance and bottlenecks.
"""

import time
import logging
try:
    import psutil
except ImportError:
    psutil = None  # Optional dependency
import functools
from typing import Dict, List, Optional, Callable
from dataclasses import dataclass, field
from datetime import datetime
from collections import defaultdict, deque

logger = logging.getLogger(__name__)


@dataclass
class APIMetric:
    """Single API call metric."""
    endpoint: str
    method: str
    duration_ms: float
    status_code: Optional[int]
    success: bool
    timestamp: datetime
    error_message: Optional[str] = None


@dataclass
class CacheMetrics:
    """Cache performance metrics."""
    hits: int = 0
    misses: int = 0
    total_requests: int = 0
    
    @property
    def hit_rate(self) -> float:
        """Calculate cache hit rate percentage."""
        if self.total_requests == 0:
            return 0.0
        return (self.hits / self.total_requests) * 100
    
    def record_hit(self):
        """Record a cache hit."""
        self.hits += 1
        self.total_requests += 1
    
    def record_miss(self):
        """Record a cache miss."""
        self.misses += 1
        self.total_requests += 1


@dataclass
class PerformanceStats:
    """Performance statistics for a metric category."""
    count: int = 0
    total_time_ms: float = 0.0
    min_time_ms: float = float('inf')
    max_time_ms: float = 0.0
    errors: int = 0
    recent_times: deque = field(default_factory=lambda: deque(maxlen=100))
    
    @property
    def avg_time_ms(self) -> float:
        """Calculate average execution time."""
        if self.count == 0:
            return 0.0
        return self.total_time_ms / self.count
    
    @property
    def p95_time_ms(self) -> float:
        """Calculate 95th percentile execution time."""
        if not self.recent_times:
            return 0.0
        sorted_times = sorted(self.recent_times)
        index = int(len(sorted_times) * 0.95)
        return sorted_times[index] if index < len(sorted_times) else sorted_times[-1]
    
    def record(self, duration_ms: float, success: bool = True):
        """Record a new execution time."""
        self.count += 1
        self.total_time_ms += duration_ms
        self.min_time_ms = min(self.min_time_ms, duration_ms)
        self.max_time_ms = max(self.max_time_ms, duration_ms)
        self.recent_times.append(duration_ms)
        if not success:
            self.errors += 1


class MetricsTracker:
    """
    Central metrics tracking system.
    
    Tracks:
    - API call performance (timing, success rate, errors)
    - Cache hit/miss rates
    - Memory usage
    - Strategy execution times
    """
    
    def __init__(self, db=None):
        """
        Initialize metrics tracker.
        
        Args:
            db: Optional database instance for persistent storage
        """
        self.api_metrics: Dict[str, PerformanceStats] = defaultdict(PerformanceStats)
        self.cache_metrics: Dict[str, CacheMetrics] = defaultdict(CacheMetrics)
        self.strategy_metrics: Dict[str, PerformanceStats] = defaultdict(PerformanceStats)
        self.recent_api_calls: deque = deque(maxlen=1000)  # Keep last 1000 API calls
        
        self.start_time = datetime.now()
        self.process = psutil.Process() if psutil else None
        self.db = db  # Optional database for persistent metrics
        
        if not psutil:
            logger.warning("psutil not available, system metrics will be limited")
        
        logger.info("📊 Metrics tracker initialized")
    
    def record_api_call(self, endpoint: str, method: str, duration_ms: float, 
                       status_code: Optional[int] = None, success: bool = True,
                       error_message: Optional[str] = None):
        """
        Record an API call metric.
        
        Args:
            endpoint: API endpoint called
            method: HTTP method (GET, POST, etc.)
            duration_ms: Call duration in milliseconds
            status_code: HTTP status code
            success: Whether the call succeeded
            error_message: Error message if failed
        """
        metric = APIMetric(
            endpoint=endpoint,
            method=method,
            duration_ms=duration_ms,
            status_code=status_code,
            success=success,
            timestamp=datetime.now(),
            error_message=error_message
        )
        
        # Record in recent calls
        self.recent_api_calls.append(metric)
        
        # Update stats for this endpoint
        key = f"{method} {endpoint}"
        self.api_metrics[key].record(duration_ms, success)
        
        # Log slow calls (>1 second)
        if duration_ms > 1000:
            logger.warning(f"🐌 SLOW API CALL: {key} took {duration_ms:.0f}ms")
        
        # Log all API calls at debug level
        status = "✅" if success else "❌"
        logger.debug(f"API_METRIC: {status} {key} - {duration_ms:.0f}ms - {status_code or 'N/A'}")
        
        # Save to database if available (for historical tracking)
        if self.db:
            try:
                self.db.save_api_metric(endpoint, method, duration_ms, status_code, success, error_message)
            except Exception as e:
                logger.debug(f"Failed to save API metric to database: {e}")
    
    def record_cache_hit(self, cache_name: str):
        """Record a cache hit."""
        self.cache_metrics[cache_name].record_hit()
    
    def record_cache_miss(self, cache_name: str):
        """Record a cache miss."""
        self.cache_metrics[cache_name].record_miss()
    
    def record_strategy_execution(self, strategy_name: str, duration_ms: float, success: bool = True):
        """Record strategy execution time."""
        self.strategy_metrics[strategy_name].record(duration_ms, success)
    
    def get_memory_usage_mb(self) -> float:
        """Get current memory usage in MB."""
        if self.process:
            return self.process.memory_info().rss / 1024 / 1024
        return 0.0
    
    def get_cpu_percent(self) -> float:
        """Get current CPU usage percentage."""
        if self.process:
            return self.process.cpu_percent(interval=0.1)
        return 0.0
    
    def get_api_summary(self) -> Dict:
        """Get summary of API call metrics."""
        total_calls = sum(stat.count for stat in self.api_metrics.values())
        total_errors = sum(stat.errors for stat in self.api_metrics.values())
        
        # Find slowest endpoints
        slowest = sorted(
            [(endpoint, stat.avg_time_ms) for endpoint, stat in self.api_metrics.items()],
            key=lambda x: x[1],
            reverse=True
        )[:5]
        
        return {
            "total_calls": total_calls,
            "total_errors": total_errors,
            "error_rate": (total_errors / total_calls * 100) if total_calls > 0 else 0.0,
            "slowest_endpoints": [
                {"endpoint": endpoint, "avg_ms": f"{avg_ms:.1f}"} 
                for endpoint, avg_ms in slowest
            ],
            "endpoints": {
                endpoint: {
                    "count": stat.count,
                    "avg_ms": f"{stat.avg_time_ms:.1f}",
                    "min_ms": f"{stat.min_time_ms:.1f}",
                    "max_ms": f"{stat.max_time_ms:.1f}",
                    "p95_ms": f"{stat.p95_time_ms:.1f}",
                    "errors": stat.errors,
                    "error_rate": f"{(stat.errors / stat.count * 100) if stat.count > 0 else 0:.1f}%"
                }
                for endpoint, stat in self.api_metrics.items()
            }
        }
    
    def get_cache_summary(self) -> Dict:
        """Get summary of cache metrics."""
        return {
            cache_name: {
                "hits": metrics.hits,
                "misses": metrics.misses,
                "total": metrics.total_requests,
                "hit_rate": f"{metrics.hit_rate:.1f}%"
            }
            for cache_name, metrics in self.cache_metrics.items()
        }
    
    def get_strategy_summary(self) -> Dict:
        """Get summary of strategy execution metrics."""
        return {
            strategy: {
                "executions": stat.count,
                "avg_ms": f"{stat.avg_time_ms:.1f}",
                "min_ms": f"{stat.min_time_ms:.1f}",
                "max_ms": f"{stat.max_time_ms:.1f}",
                "errors": stat.errors
            }
            for strategy, stat in self.strategy_metrics.items()
        }
    
    def get_system_metrics(self) -> Dict:
        """Get system resource metrics."""
        uptime = datetime.now() - self.start_time
        return {
            "memory_mb": f"{self.get_memory_usage_mb():.1f}",
            "cpu_percent": f"{self.get_cpu_percent():.1f}",
            "uptime": str(uptime).split('.')[0]  # Remove microseconds
        }
    
    def get_full_report(self) -> Dict:
        """Get complete metrics report."""
        return {
            "system": self.get_system_metrics(),
            "api": self.get_api_summary(),
            "cache": self.get_cache_summary(),
            "strategies": self.get_strategy_summary()
        }
    
    def print_report(self):
        """Print formatted metrics report."""
        report = self.get_full_report()
        
        logger.info("=" * 80)
        logger.info("📊 PERFORMANCE METRICS REPORT")
        logger.info("=" * 80)
        
        # System metrics
        logger.info("\n🖥️  SYSTEM:")
        for key, value in report["system"].items():
            logger.info(f"  {key}: {value}")
        
        # API metrics
        api = report["api"]
        logger.info(f"\n🌐 API CALLS:")
        logger.info(f"  Total: {api['total_calls']} | Errors: {api['total_errors']} | Error Rate: {api['error_rate']:.1f}%")
        
        if api.get("slowest_endpoints"):
            logger.info(f"\n  ⏱️  Slowest Endpoints:")
            for item in api["slowest_endpoints"]:
                logger.info(f"    - {item['endpoint']}: {item['avg_ms']}ms avg")
        
        # Cache metrics
        if report["cache"]:
            logger.info(f"\n💾 CACHE:")
            for cache_name, metrics in report["cache"].items():
                logger.info(f"  {cache_name}: {metrics['hit_rate']} hit rate ({metrics['hits']}/{metrics['total']})")
        
        # Strategy metrics
        if report["strategies"]:
            logger.info(f"\n📈 STRATEGIES:")
            for strategy, metrics in report["strategies"].items():
                logger.info(f"  {strategy}: {metrics['executions']} executions, {metrics['avg_ms']}ms avg")
        
        logger.info("=" * 80)


# Global metrics tracker instance
_metrics_tracker: Optional[MetricsTracker] = None


def get_metrics_tracker(db=None) -> MetricsTracker:
    """
    Get or create global metrics tracker instance.
    
    Args:
        db: Optional database instance for persistent storage
    
    Returns:
        MetricsTracker: Global metrics tracker
    """
    global _metrics_tracker
    if _metrics_tracker is None:
        _metrics_tracker = MetricsTracker(db=db)
    elif db and not _metrics_tracker.db:
        # Update database reference if provided later
        _metrics_tracker.db = db
        logger.info("📊 Metrics tracker database connection updated")
    return _metrics_tracker


def track_api_call(func: Callable) -> Callable:
    """
    Decorator to track API call performance.
    
    Usage:
        @track_api_call
        def _send_request(self, method, endpoint, ...):
            ...
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        # Extract method and endpoint from args
        # Assumes signature: (self, method, endpoint, ...)
        method = args[1] if len(args) > 1 else kwargs.get('method', 'UNKNOWN')
        endpoint = args[2] if len(args) > 2 else kwargs.get('endpoint', 'UNKNOWN')
        
        start_time = time.time()
        success = False
        status_code = None
        error_message = None
        
        try:
            result = func(*args, **kwargs)
            success = True
            
            # Try to extract status code from response
            if isinstance(result, dict) and 'status_code' in result:
                status_code = result['status_code']
            
            return result
        except Exception as e:
            error_message = str(e)
            raise
        finally:
            duration_ms = (time.time() - start_time) * 1000
            tracker = get_metrics_tracker()
            tracker.record_api_call(
                endpoint=endpoint,
                method=method,
                duration_ms=duration_ms,
                status_code=status_code,
                success=success,
                error_message=error_message
            )
    
    return wrapper


def track_execution(category: str):
    """
    Decorator to track execution time for a category.
    
    Usage:
        @track_execution("strategy")
        def analyze(self, symbol):
            ...
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            start_time = time.time()
            success = False
            
            try:
                result = func(*args, **kwargs)
                success = True
                return result
            finally:
                duration_ms = (time.time() - start_time) * 1000
                tracker = get_metrics_tracker()
                
                if category == "strategy":
                    # For strategy methods, extract strategy name
                    strategy_name = args[0].__class__.__name__ if args else "Unknown"
                    tracker.record_strategy_execution(strategy_name, duration_ms, success)
    
        return wrapper
    return decorator

