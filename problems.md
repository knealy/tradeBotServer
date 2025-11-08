
remove all unneccesary/irrelevant/redundant files/code to cleanup and de clutter the project 

### Advanced Optimizations

#### F. Database for Persistent State
**Problem**: Cache is lost on restart
**Solution**: Use SQLite or PostgreSQL for persistent caching
**Impact**: Eliminates cold cache on restart

#### G. Async Webhook Server
**Problem**: Webhook server blocks on I/O
**Solution**: Convert to async/await pattern
**Impact**: Handles 10x more concurrent requests

#### H. Metrics & Monitoring
**Problem**: No visibility into performance bottlenecks
**Solution**: Add Prometheus metrics or similar
**Impact**: Identify slow operations in production

#### I. Background Task Optimization
**Problem**: Multiple background tasks compete for resources
**Solution**: Use priority queues and task scheduling
**Impact**: Better resource utilization


go over options for fastest / most effecient frontend - bridge - backend structures and tech stack 
- make a plan to port / convert (probably to Go + React + JS) 
- is Go the best choice?

#### J. Go/Rust Migration (Future)
**Problem**: Python GIL limits concurrency
  **Solution**: Migrate hot paths to Go/Rust
  **Impact**: 10-100x performance improvement for I/O-bound operations
