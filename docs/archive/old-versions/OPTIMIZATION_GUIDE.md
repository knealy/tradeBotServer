# 🚀 Optimization Guide - Trading Bot & Server

## Quick Wins (Do These First)

### 1. **Install `psutil` for SDK Performance**
```bash
pip install psutil
```
**Impact**: Reduces SDK resource monitoring overhead by ~2-3 seconds per fetch
**Why**: The SDK uses `psutil` for efficient resource management instead of fallback methods

### 2. **Enable Log Rotation**
Prevents log files from growing indefinitely and impacting disk I/O.

**Current Issue**: Logs append forever, can grow to GBs
**Solution**: Add to `trading_bot.py` and `webhook_server.py`:
```python
from logging.handlers import RotatingFileHandler

# Replace FileHandler with RotatingFileHandler
file_handler = RotatingFileHandler(
    'trading_bot.log', 
    maxBytes=10*1024*1024,  # 10MB
    backupCount=5
)
```

### 3. **Add Request Timeout Configuration**
Prevent hanging requests from blocking operations.

**Current**: No explicit timeouts on API calls
**Solution**: Add to `.env`:
```bash
API_TIMEOUT=30  # Already exists, but verify it's used everywhere
WEBHOOK_TIMEOUT=10  # Already exists
```

### 4. **Optimize Cache Expiration**
Adjust cache TTL based on market volatility.

**Current**: 5 minutes fixed
**Recommendation**: 
- High volatility (during market hours): 1-2 minutes
- Low volatility (off-hours): 15-30 minutes
- Consider making this configurable via `.env`

## Medium Priority Optimizations

### 5. **HTTP Connection Pooling**
Reuse HTTP connections instead of creating new ones for each request.

**Current**: Each API call creates new connection
**Solution**: Use `aiohttp.ClientSession` with connection pooling:
```python
import aiohttp

class TopStepXTradingBot:
    def __init__(self, ...):
        self._http_session = None
    
    async def _get_session(self):
        if self._http_session is None or self._http_session.closed:
            self._http_session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30),
                connector=aiohttp.TCPConnector(limit=10, limit_per_host=5)
            )
        return self._http_session
```

**Impact**: Reduces connection overhead by ~50-100ms per request

### 6. **Async Webhook Server**
Convert webhook server from synchronous `BaseHTTPRequestHandler` to async.

**Current**: Synchronous HTTP server (blocks on each request)
**Solution**: Use `aiohttp` web server:
```python
from aiohttp import web

async def webhook_handler(request):
    data = await request.json()
    # Process asynchronously
    return web.json_response({"status": "ok"})

app = web.Application()
app.router.add_post('/webhook', webhook_handler)
web.run_app(app, port=8080)
```

**Impact**: Can handle 10-100x more concurrent requests

### 7. **Batch API Calls Where Possible**
Group multiple operations into single API calls.

**Current**: Each position/order check = separate API call
**Opportunity**: 
- Batch position checks
- Cache contract lookups
- Batch order status checks

### 8. **Database for Persistent State**
Replace in-memory caches with persistent storage.

**Current**: All state lost on restart
**Solution**: Use SQLite or lightweight DB:
```python
import sqlite3

class StateDB:
    def __init__(self, db_path='.cache/trading_state.db'):
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self._init_tables()
    
    def _init_tables(self):
        self.conn.execute('''
            CREATE TABLE IF NOT EXISTS positions (
                account_id TEXT,
                symbol TEXT,
                position_id TEXT,
                quantity INTEGER,
                side TEXT,
                timestamp TEXT,
                PRIMARY KEY (account_id, symbol)
            )
        ''')
```

**Benefits**:
- Survives restarts
- Faster lookups than API calls
- Can track historical data

## Advanced Optimizations

### 9. **WebSocket Connection Pooling**
Reuse WebSocket connections for market data.

**Current**: New connections for each subscription
**Solution**: Single WebSocket connection with multiplexing

### 10. **Rate Limiting & Throttling**
Prevent API rate limit violations.

**Current**: No rate limiting
**Solution**: Add token bucket or sliding window:
```python
from collections import deque
import time

class RateLimiter:
    def __init__(self, max_calls=60, period=60):
        self.max_calls = max_calls
        self.period = period
        self.calls = deque()
    
    def acquire(self):
        now = time.time()
        # Remove old calls
        while self.calls and self.calls[0] < now - self.period:
            self.calls.popleft()
        
        if len(self.calls) >= self.max_calls:
            sleep_time = self.period - (now - self.calls[0])
            time.sleep(sleep_time)
        
        self.calls.append(now)
```

### 11. **Metrics & Monitoring**
Add Prometheus/StatsD metrics for visibility.

**Metrics to track**:
- API call latency
- Webhook processing time
- Cache hit rate
- Error rates
- Position/order counts

### 12. **Background Task Optimization**
Optimize the auto-fill checker and monitoring tasks.

**Current**: Running every 30 seconds
**Optimizations**:
- Use asyncio event-driven instead of polling
- Batch multiple checks
- Skip if no pending orders

### 13. **Cache Warming Strategy**
Pre-load frequently accessed data.

**Opportunities**:
- Warm contract list at startup
- Pre-cache common symbol/timeframe combinations
- Cache account info

### 14. **Compression for API Responses**
Compress large API responses.

**Solution**: Use gzip compression for responses > 1KB

## Server/Infrastructure Optimizations

### 15. **Use Production ASGI Server**
Replace development server with production-grade server.

**Current**: Basic HTTP server
**Solution**: Use `gunicorn` + `uvicorn`:
```bash
pip install gunicorn uvicorn
gunicorn -w 4 -k uvicorn.workers.UvicornWorker webhook_server:app
```

### 16. **Add Health Check Endpoints**
Already have `/health` - enhance it:
- Check database connectivity
- Check API connectivity
- Check cache status
- Return detailed metrics

### 17. **Implement Graceful Shutdown**
Properly close connections on shutdown.

**Current**: May lose in-flight requests
**Solution**: Add signal handlers:
```python
import signal

def graceful_shutdown(signum, frame):
    logger.info("Shutting down gracefully...")
    # Close HTTP sessions
    # Save state
    # Close WebSocket connections
    sys.exit(0)

signal.signal(signal.SIGTERM, graceful_shutdown)
signal.signal(signal.SIGINT, graceful_shutdown)
```

### 18. **Environment-Based Configuration**
Optimize for different environments.

**Development**:
- Verbose logging
- Longer timeouts
- More debugging

**Production**:
- Minimal logging
- Aggressive caching
- Fast timeouts
- Error monitoring

## Performance Monitoring

### 19. **Add Performance Profiling**
Identify actual bottlenecks.

**Tools**:
```python
# Add to critical paths
import cProfile
import pstats

profiler = cProfile.Profile()
profiler.enable()
# ... your code ...
profiler.disable()
stats = pstats.Stats(profiler)
stats.sort_stats('cumulative')
stats.print_stats(20)  # Top 20 slowest operations
```

### 20. **APM Integration**
Consider APM tools for production:
- New Relic
- Datadog
- Sentry (for errors)

## Recommended Priority Order

1. **Immediate** (5 minutes):
   - ✅ Install `psutil`
   - ✅ Add log rotation
   - ✅ Verify timeouts are set

2. **This Week** (1-2 hours):
   - HTTP connection pooling
   - Cache expiration tuning
   - Database for persistent state

3. **This Month** (4-8 hours):
   - Async webhook server
   - Batch API calls
   - Rate limiting
   - Metrics collection

4. **Future** (when scaling):
   - Full async migration
   - Advanced caching strategies
   - APM integration
   - Load balancing

## Quick Performance Checklist

- [ ] `psutil` installed
- [ ] Log rotation configured
- [ ] HTTP connection pooling
- [ ] Cache TTL optimized
- [ ] Database for state persistence
- [ ] Rate limiting implemented
- [ ] Metrics collection added
- [ ] Health checks enhanced
- [ ] Graceful shutdown implemented
- [ ] Production server configured

## Expected Performance Gains

| Optimization | Latency Improvement | Throughput Improvement |
|-------------|---------------------|----------------------|
| psutil | 2-3s per fetch | N/A |
| HTTP Pooling | 50-100ms per request | 2-3x |
| Async Webhook | N/A | 10-100x |
| Database Cache | 100-500ms per lookup | 5-10x |
| Rate Limiting | Prevents errors | Stability |
| Batch Calls | 50-200ms per batch | 3-5x |

## Notes

- **Measure First**: Profile before optimizing
- **Incremental**: Implement one optimization at a time
- **Test**: Verify each optimization doesn't break functionality
- **Monitor**: Track metrics before/after each change

---

**Next Steps**: Start with Quick Wins, then move to Medium Priority based on your specific bottlenecks.

