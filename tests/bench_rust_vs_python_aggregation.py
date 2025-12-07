"""
Performance benchmark: Rust vs Python bar aggregation.

Compares the performance of Rust and Python implementations
for aggregating 1-minute bars into higher timeframes (5m, 15m, 1h).

This benchmark focuses on CPU-bound operations where Rust should show
significant performance improvements (target: 10-20x speedup).
"""

import asyncio
import os
import sys
import time
from typing import List, Dict, Any

# Ensure project root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

# Try to import Rust module
try:
    import trading_bot_rust
    RUST_AVAILABLE = True
except ImportError:
    RUST_AVAILABLE = False
    trading_bot_rust = None
    print("⚠️  Rust module not available. Install with: cd rust && maturin develop --release")


def create_test_bars(count: int, base_timestamp: int = 1609459200) -> List[Dict[str, Any]]:
    """Create test 1-minute bars for aggregation."""
    bars = []
    for i in range(count):
        bars.append({
            'timestamp': base_timestamp + i * 60,
            'time': base_timestamp + i * 60,
            'open': 100.0 + i * 0.1,
            'high': 101.0 + i * 0.1,
            'low': 99.0 + i * 0.1,
            'close': 100.5 + i * 0.1,
            'volume': 100 + i,
        })
    return bars


def aggregate_bars_python(bars: List[Dict], target_timeframe: str) -> List[Dict]:
    """Python implementation of bar aggregation."""
    if not bars:
        return []
    
    # Parse timeframe to seconds
    def parse_timeframe_to_seconds(tf: str) -> int:
        tf = tf.strip().lower()
        if tf.endswith('s'):
            return int(tf[:-1])
        elif tf.endswith('m'):
            return int(tf[:-1]) * 60
        elif tf.endswith('h'):
            return int(tf[:-1]) * 3600
        elif tf.endswith('d'):
            return int(tf[:-1]) * 86400
        elif tf.endswith('w'):
            return int(tf[:-1]) * 604800
        return 60
    
    target_seconds = parse_timeframe_to_seconds(target_timeframe)
    if target_seconds <= 60:
        return bars
    
    # Group bars
    aggregated = []
    current_group = []
    current_group_start = None
    
    for bar in bars:
        ts = bar.get('timestamp') or bar.get('time')
        bar_start_seconds = (ts // target_seconds) * target_seconds
        
        if current_group_start is None or bar_start_seconds != current_group_start:
            # Finalize previous group
            if current_group:
                agg_bar = {
                    'timestamp': current_group_start,
                    'time': current_group_start,
                    'open': current_group[0].get('open', 0),
                    'high': max(b.get('high', 0) for b in current_group),
                    'low': min(b.get('low', float('inf')) for b in current_group if b.get('low') is not None),
                    'close': current_group[-1].get('close', 0),
                    'volume': sum(b.get('volume', 0) or 0 for b in current_group),
                }
                if agg_bar['low'] == float('inf'):
                    agg_bar['low'] = agg_bar['open']
                aggregated.append(agg_bar)
            
            # Start new group
            current_group = [bar]
            current_group_start = bar_start_seconds
        else:
            current_group.append(bar)
    
    # Finalize last group
    if current_group:
        agg_bar = {
            'timestamp': current_group_start,
            'time': current_group_start,
            'open': current_group[0].get('open', 0),
            'high': max(b.get('high', 0) for b in current_group),
            'low': min(b.get('low', float('inf')) for b in current_group if b.get('low') is not None),
            'close': current_group[-1].get('close', 0),
            'volume': sum(b.get('volume', 0) or 0 for b in current_group),
        }
        if agg_bar['low'] == float('inf'):
            agg_bar['low'] = agg_bar['open']
        aggregated.append(agg_bar)
    
    return aggregated


def aggregate_bars_rust(bars: List[Dict], target_timeframe: str, symbol: str = "MNQ", use_raw: bool = True) -> List[Dict]:
    """Rust implementation of bar aggregation."""
    if not RUST_AVAILABLE:
        raise RuntimeError("Rust module not available")
    
    if use_raw:
        # Optimized path: use raw data to minimize conversions
        timestamps = [bar.get('timestamp') or bar.get('time') for bar in bars]
        opens = [bar.get('open', 0.0) for bar in bars]
        highs = [bar.get('high', 0.0) for bar in bars]
        lows = [bar.get('low', 0.0) for bar in bars]
        closes = [bar.get('close', 0.0) for bar in bars]
        volumes = [bar.get('volume', 0) for bar in bars]
        
        # Aggregate using optimized Rust function
        aggregated_rust = trading_bot_rust.aggregate_bars_raw(
            timestamps, opens, highs, lows, closes, volumes,
            target_timeframe, symbol
        )
        
        # Convert back to Python dicts
        result = []
        for bar in aggregated_rust:
            result.append({
                'timestamp': bar.timestamp,
                'time': bar.timestamp,
                'open': bar.open,
                'high': bar.high,
                'low': bar.low,
                'close': bar.close,
                'volume': bar.volume,
            })
        
        return result
    else:
        # Original path: convert to Bar objects (slower)
        rust_bars = []
        for bar in bars:
            rust_bar = trading_bot_rust.Bar(
                timestamp=bar.get('timestamp') or bar.get('time'),
                open=bar.get('open', 0.0),
                high=bar.get('high', 0.0),
                low=bar.get('low', 0.0),
                close=bar.get('close', 0.0),
                volume=bar.get('volume', 0),
                symbol=symbol,
                timeframe="1m"
            )
            rust_bars.append(rust_bar)
        
        # Aggregate using Rust
        aggregated_rust = trading_bot_rust.aggregate_bars(rust_bars, target_timeframe, symbol)
        
        # Convert back to Python dicts
        result = []
        for bar in aggregated_rust:
            result.append({
                'timestamp': bar.timestamp,
                'time': bar.timestamp,
                'open': bar.open,
                'high': bar.high,
                'low': bar.low,
                'close': bar.close,
                'volume': bar.volume,
            })
        
        return result


def benchmark_aggregation(
    bar_count: int,
    target_timeframe: str,
    iterations: int = 100
) -> Dict[str, float]:
    """Benchmark both implementations."""
    print(f"\n📊 Benchmarking aggregation: {bar_count} bars → {target_timeframe}")
    print(f"   Iterations: {iterations}")
    
    # Create test data
    test_bars = create_test_bars(bar_count)
    
    # Python benchmark
    print(f"\n🐍 Python aggregation:")
    start = time.perf_counter()
    for _ in range(iterations):
        result_python = aggregate_bars_python(test_bars, target_timeframe)
    python_time = (time.perf_counter() - start) / iterations * 1000  # ms
    
    print(f"   avg latency: {python_time:.3f} ms")
    print(f"   result count: {len(result_python)} bars")
    
    # Rust benchmark
    if not RUST_AVAILABLE:
        print(f"\n⚠️  Rust not available, skipping Rust benchmark")
        return {
            'python_ms': python_time,
            'rust_ms': None,
            'speedup': None,
            'bar_count': bar_count,
            'target_timeframe': target_timeframe,
        }
    
    print(f"\n🚀 Rust aggregation:")
    start = time.perf_counter()
    for _ in range(iterations):
        result_rust = aggregate_bars_rust(test_bars, target_timeframe)
    rust_time = (time.perf_counter() - start) / iterations * 1000  # ms
    
    print(f"   avg latency: {rust_time:.3f} ms")
    print(f"   result count: {len(result_rust)} bars")
    
    # Verify results match
    if len(result_python) != len(result_rust):
        print(f"   ⚠️  WARNING: Result count mismatch! Python: {len(result_python)}, Rust: {len(result_rust)}")
    else:
        print(f"   ✅ Result counts match: {len(result_python)} bars")
    
    # Calculate speedup
    speedup = python_time / rust_time if rust_time > 0 else None
    
    if speedup:
        print(f"\n📈 Speedup: {speedup:.2f}x")
        if speedup >= 10:
            print(f"   🎉 Excellent! Target achieved (10x+)")
        elif speedup >= 5:
            print(f"   ✅ Good performance improvement")
        else:
            print(f"   ⚠️  Lower than expected (target: 10-20x)")
    
    return {
        'python_ms': python_time,
        'rust_ms': rust_time,
        'speedup': speedup,
        'bar_count': bar_count,
        'target_timeframe': target_timeframe,
        'result_count': len(result_python),
    }


def main():
    """Run comprehensive benchmarks."""
    print("=" * 70)
    print("🚀 Rust vs Python Bar Aggregation Benchmark")
    print("=" * 70)
    
    if not RUST_AVAILABLE:
        print("\n⚠️  Rust module not available!")
        print("   To build: cd rust && maturin develop --release")
        return
    
    results = []
    
    # Test different bar counts and timeframes
    test_cases = [
        (100, "5m"),
        (500, "5m"),
        (1000, "5m"),
        (3000, "5m"),
        (1000, "15m"),
        (3000, "15m"),
        (3000, "1h"),
    ]
    
    for bar_count, timeframe in test_cases:
        result = benchmark_aggregation(bar_count, timeframe, iterations=100)
        results.append(result)
    
    # Summary
    print("\n" + "=" * 70)
    print("📊 Summary")
    print("=" * 70)
    print(f"{'Bars':<8} {'Timeframe':<10} {'Python (ms)':<12} {'Rust (ms)':<12} {'Speedup':<10}")
    print("-" * 70)
    
    for r in results:
        rust_str = f"{r['rust_ms']:.3f}" if r['rust_ms'] else "N/A"
        speedup_str = f"{r['speedup']:.2f}x" if r['speedup'] else "N/A"
        print(f"{r['bar_count']:<8} {r['target_timeframe']:<10} {r['python_ms']:<12.3f} {rust_str:<12} {speedup_str:<10}")
    
    # Overall analysis
    if results and all(r['speedup'] for r in results if r['speedup']):
        avg_speedup = sum(r['speedup'] for r in results if r['speedup']) / len([r for r in results if r['speedup']])
        print(f"\n💡 Average speedup: {avg_speedup:.2f}x")
        
        if avg_speedup >= 10:
            print("   🎉 Excellent! Phase 2 target achieved!")
        elif avg_speedup >= 5:
            print("   ✅ Good performance improvement")
        else:
            print("   ⚠️  Consider further optimizations (SIMD, parallel processing)")


if __name__ == "__main__":
    main()

