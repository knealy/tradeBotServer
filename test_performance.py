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

# IMPORTANT: Set DATABASE_URL from environment BEFORE importing trading_bot
# This prevents .env file from overriding the exported DATABASE_URL
if 'DATABASE_URL' in os.environ:
    # Store the exported DATABASE_URL before .env loads
    exported_db_url = os.environ['DATABASE_URL']
    # Temporarily remove it so .env can load
    del os.environ['DATABASE_URL']

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

# Restore exported DATABASE_URL after imports (overrides .env)
if 'exported_db_url' in locals():
    os.environ['DATABASE_URL'] = exported_db_url


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
            print("💡 Tip: You're using Railway's internal database URL.")
            print("   To test locally, get the PUBLIC_DATABASE_URL from Railway Dashboard:")
            print("   1. Go to https://railway.app")
            print("   2. Your Project → PostgreSQL Service → Variables")
            print("   3. Copy the PUBLIC_DATABASE_URL (not DATABASE_URL - that's internal-only)")
            print("   4. Run: export DATABASE_URL='<paste-public-url>'")
            print()
            print("   Or test without database (in-memory cache):")
            print("   unset DATABASE_URL")
        
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
        if warm_duration < 10:
            print("   ✅ Excellent! Database cache is working perfectly")
        elif warm_duration < 50:
            print("   ✅ Good! Database cache is providing significant speedup")
        else:
            print("   ⚠️  Cache may not be working optimally - check database connection")
    else:
        print("   💡 Consider setting up PostgreSQL for persistent caching")
        print("   💡 In-memory cache works but data is lost on restart")
    
    print()


if __name__ == "__main__":
    asyncio.run(test_performance())

