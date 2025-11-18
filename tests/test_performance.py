#!/usr/bin/env python3
"""
Quick Performance Test Script

Tests database caching performance locally or against Railway database.
Works WITHOUT Docker - can connect to Railway DB or use in-memory cache.

Usage:
    # Test with Railway database (recommended)
    python test_performance.py

    # Test with local database (if you have one)
    DATABASE_URL=postgresql://postgres:postgres@localhost:5432/trading_bot python test_performance.py

    # Test without database (in-memory only)
    unset DATABASE_URL
    python test_performance.py
"""

import os
import sys
import time
import asyncio
from datetime import datetime

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from trading_bot import TopStepXTradingBot
    from infrastructure.database import get_database
    from infrastructure.performance_metrics import get_metrics_tracker
except ImportError as e:
    print(f"❌ Import error: {e}")
    print()
    print("💡 Troubleshooting:")
    print("   1. Make sure you're in the project root directory")
    print("   2. Activate your virtual environment:")
    print("      source .venv/bin/activate")
    print("   3. Install dependencies:")
    print("      pip install -r requirements.txt")
    print()
    sys.exit(1)


async def test_performance():
    """Run performance tests comparing cache vs API."""
    
    print("=" * 70)
    print("🚀 Trading Bot Performance Test")
    print("=" * 70)
    print()
    
    # Check database availability
    db = None
    try:
        db = get_database()
        print("✅ Database: PostgreSQL connected")
        db_type = "PostgreSQL"
    except Exception as e:
        error_msg = str(e)
        print(f"⚠️  Database: Not available (using in-memory cache only)")
        print(f"   Reason: {error_msg}")
        
        # Provide helpful guidance
        if "railway.internal" in error_msg or "could not translate host name" in error_msg:
            print()
            print("💡 Tip: Database connection failed.")
            if not os.getenv('PUBLIC_DATABASE_URL'):
                print("   Add PUBLIC_DATABASE_URL to your .env file:")
                print("   1. Go to Railway Dashboard → PostgreSQL → Variables")
                print("   2. Copy PUBLIC_DATABASE_URL")
                print("   3. Add to .env: PUBLIC_DATABASE_URL=<paste-url>")
                print()
                print("   The script will automatically use PUBLIC_DATABASE_URL when local.")
            else:
                print("   PUBLIC_DATABASE_URL is set but connection still failed.")
                print("   Check that the URL is correct in your .env file.")
            print()
            print("   Or test without database (in-memory cache):")
            print("   Comment out DATABASE_URL and PUBLIC_DATABASE_URL in .env")
        
        db_type = "In-Memory"
    
    print()
    
    # Initialize bot
    print("📡 Initializing bot...")
    api_key = os.getenv('PROJECT_X_API_KEY') or os.getenv('TOPSETPX_API_KEY')
    username = os.getenv('PROJECT_X_USERNAME') or os.getenv('TOPSETPX_USERNAME')
    
    if not api_key or not username:
        print("❌ Error: PROJECT_X_API_KEY and PROJECT_X_USERNAME must be set")
        return
    
    bot = TopStepXTradingBot(api_key=api_key, username=username)
    
    # Authenticate (async)
    print("🔐 Authenticating...")
    auth_result = await bot.authenticate()
    if not auth_result:
        print("❌ Authentication failed")
        return
    
    print("✅ Authenticated")
    print()
    
    # Test symbol and timeframe
    symbol = os.getenv('TEST_SYMBOL', 'MNQ')
    timeframe = os.getenv('TEST_TIMEFRAME', '5m')
    limit = int(os.getenv('TEST_LIMIT', '100'))
    
    print(f"📊 Test Configuration:")
    print(f"   Symbol: {symbol}")
    print(f"   Timeframe: {timeframe}")
    print(f"   Bars: {limit}")
    print(f"   Cache Type: {db_type}")
    print()
    
    # Test 1: Cold cache (first fetch)
    print("=" * 70)
    print("TEST 1: Cold Cache (First Fetch)")
    print("=" * 70)
    
    start_time = time.time()
    try:
        bars = await bot.get_historical_data(symbol, timeframe, limit)
        cold_duration = (time.time() - start_time) * 1000
        
        if bars:
            print(f"✅ Success: Retrieved {len(bars)} bars")
            print(f"⏱️  Duration: {cold_duration:.1f}ms")
            print(f"📈 First bar: {bars[0].get('timestamp', 'N/A')}")
            print(f"📈 Last bar: {bars[-1].get('timestamp', 'N/A')}")
        else:
            print("❌ No data returned")
            return
    except Exception as e:
        print(f"❌ Error: {e}")
        return
    
    print()
    
    # Test 2: Warm cache (second fetch)
    print("=" * 70)
    print("TEST 2: Warm Cache (Second Fetch)")
    print("=" * 70)
    
    start_time = time.time()
    try:
        bars = await bot.get_historical_data(symbol, timeframe, limit)
        warm_duration = (time.time() - start_time) * 1000
        
        if bars:
            print(f"✅ Success: Retrieved {len(bars)} bars")
            print(f"⏱️  Duration: {warm_duration:.1f}ms")
            
            # Calculate improvement
            if cold_duration > 0:
                improvement = ((cold_duration - warm_duration) / cold_duration) * 100
                speedup = cold_duration / warm_duration if warm_duration > 0 else 0
                print(f"⚡ Improvement: {improvement:.1f}% faster")
                print(f"🚀 Speedup: {speedup:.1f}x")
        else:
            print("❌ No data returned")
    except Exception as e:
        print(f"❌ Error: {e}")
    
    print()
    
    # Test 3: Multiple fetches (cache consistency)
    print("=" * 70)
    print("TEST 3: Cache Consistency (10 Fetches)")
    print("=" * 70)
    
    durations = []
    for i in range(10):
        start_time = time.time()
        try:
            bars = await bot.get_historical_data(symbol, timeframe, limit)
            duration = (time.time() - start_time) * 1000
            durations.append(duration)
            print(f"  Fetch {i+1:2d}: {duration:6.1f}ms", end="")
            if i % 2 == 1:
                print()  # New line every 2
        except Exception as e:
            print(f"  Fetch {i+1:2d}: ERROR - {e}")
    
    if durations:
        avg_duration = sum(durations) / len(durations)
        min_duration = min(durations)
        max_duration = max(durations)
        print()
        print(f"📊 Statistics:")
        print(f"   Average: {avg_duration:.1f}ms")
        print(f"   Minimum: {min_duration:.1f}ms")
        print(f"   Maximum: {max_duration:.1f}ms")
    
    print()
    
    # Test 4: Metrics summary
    print("=" * 70)
    print("TEST 4: Performance Metrics Summary")
    print("=" * 70)
    
    metrics_tracker = get_metrics_tracker(db=db)
    metrics = metrics_tracker.get_full_report()
    
    if metrics.get('cache'):
        print("💾 Cache Performance:")
        for cache_key, cache_data in metrics['cache'].items():
            hits = cache_data.get('hits', 0)
            misses = cache_data.get('misses', 0)
            total = cache_data.get('total', hits + misses)
            hit_rate_str = cache_data.get('hit_rate', '0.0%')
            
            print(f"   {cache_key}:")
            print(f"      Hits: {hits}")
            print(f"      Misses: {misses}")
            print(f"      Total: {total}")
            print(f"      Hit Rate: {hit_rate_str}")
    else:
        print("   No cache metrics available")
    
    if metrics.get('api'):
        print()
        print("🌐 API Performance:")
        api_data = metrics['api']
        print(f"   Total Calls: {api_data.get('total_calls', 0)}")
        print(f"   Total Errors: {api_data.get('total_errors', 0)}")
        error_rate = api_data.get('error_rate', 0)
        if isinstance(error_rate, (int, float)):
            print(f"   Error Rate: {error_rate:.1f}%")
        else:
            print(f"   Error Rate: {error_rate}")
        
        if api_data.get('slowest_endpoints'):
            print()
            print("   ⏱️  Slowest Endpoints:")
            for item in api_data['slowest_endpoints'][:5]:  # Top 5
                print(f"      - {item.get('endpoint', 'N/A')}: {item.get('avg_ms', 'N/A')}ms avg")
    
    print()
    
    # Test 5: Database stats (if available)
    if db:
        print("=" * 70)
        print("TEST 5: Database Statistics")
        print("=" * 70)
        
        try:
            coverage = db.get_cache_coverage(symbol, timeframe)
            if coverage['cached']:
                print(f"✅ Cached: Yes")
                print(f"   Bar Count: {coverage['bar_count']}")
                print(f"   Oldest: {coverage['oldest_bar']}")
                print(f"   Newest: {coverage['newest_bar']}")
            else:
                print(f"⚠️  Cached: No")
        except Exception as e:
            print(f"⚠️  Could not get database stats: {e}")
    
    print()
    print("=" * 70)
    print("✅ Performance Test Complete!")
    print("=" * 70)
    print()
    
    # Recommendations
    print("💡 Recommendations:")
    if db:
        # For remote databases (Railway), account for network latency
        # Local DB: <10ms, Remote DB: 50-250ms is normal
        is_remote = 'proxy.rlwy.net' in str(os.getenv('PUBLIC_DATABASE_URL', '')) or 'railway' in str(os.getenv('DATABASE_URL', ''))
        
        if warm_duration < 10:
            print("   ✅ Excellent! Database cache is working perfectly (local DB)")
        elif warm_duration < 50:
            print("   ✅ Good! Database cache is providing significant speedup")
        elif warm_duration < 300 and is_remote:
            # Remote database - network latency is expected
            improvement_pct = ((cold_duration - warm_duration) / cold_duration * 100) if cold_duration > 0 else 0
            if improvement_pct > 50:
                print(f"   ✅ Excellent! Remote database cache working well ({improvement_pct:.1f}% faster)")
                print("   💡 200-250ms is normal for remote databases (network latency)")
                print("   💡 Still much faster than API calls (~600ms)")
            else:
                print(f"   ⚠️  Cache working but improvement is modest ({improvement_pct:.1f}%)")
        else:
            print("   ⚠️  Cache may not be working optimally - check database connection")
    else:
        print("   💡 Consider setting up PostgreSQL for persistent caching")
        print("   💡 In-memory cache works but data is lost on restart")
    
    print()


if __name__ == "__main__":
    asyncio.run(test_performance())

