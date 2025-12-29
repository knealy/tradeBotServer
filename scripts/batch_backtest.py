"""
Batch Backtest Runner - Python Version

Runs multiple backtests with different parameters and generates comparison report.

Usage:
    python scripts/batch_backtest.py
    python scripts/batch_backtest.py --quick  # Fast test with sample data
    python scripts/batch_backtest.py --monte-carlo=1000  # Include MC simulations
"""

import sys
import os
import asyncio
import argparse
from pathlib import Path
from datetime import datetime

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.backtest import HistoricalDataLoader, BacktestEngine, PerformanceMetrics, MonteCarloSimulator
from core.backtest_executor import BacktestExecutor


async def run_batch_tests(quick: bool = False, monte_carlo: int = 0):
    """
    Run batch of backtests and generate comparison report.
    
    Args:
        quick: Use sample data for speed
        monte_carlo: Number of MC simulations (0 = skip)
    """
    print("="*80)
    print("BATCH BACKTESTING SUITE")
    print("="*80)
    print(f"Mode: {'QUICK (sample data)' if quick else 'FULL (real data)'}")
    print(f"Monte Carlo: {'YES (' + str(monte_carlo) + ' sims)' if monte_carlo > 0 else 'NO'}")
    print("="*80)
    
    executor = BacktestExecutor()
    
    results = []
    
    # Test configurations
    tests = [
        # MA Crossover tests
        {
            'name': 'MA_10_50_MNQ_1m',
            'strategy': 'ma_crossover',
            'symbol': 'MNQ',
            'timeframe': '1m',
            'params': {'fast_period': 10, 'slow_period': 50}
        },
        {
            'name': 'MA_20_100_MNQ_5m',
            'strategy': 'ma_crossover',
            'symbol': 'MNQ',
            'timeframe': '5m',
            'params': {'fast_period': 20, 'slow_period': 100}
        },
        {
            'name': 'MA_10_50_MES_1m',
            'strategy': 'ma_crossover',
            'symbol': 'MES',
            'timeframe': '1m',
            'params': {'fast_period': 10, 'slow_period': 50}
        },
        
        # RSI tests
        {
            'name': 'RSI_30_70_MNQ_1m',
            'strategy': 'rsi_mean_reversion',
            'symbol': 'MNQ',
            'timeframe': '1m',
            'params': {'rsi_oversold': 30, 'rsi_overbought': 70}
        },
        {
            'name': 'RSI_25_75_MNQ_5m',
            'strategy': 'rsi_mean_reversion',
            'symbol': 'MNQ',
            'timeframe': '5m',
            'params': {'rsi_oversold': 25, 'rsi_overbought': 75}
        },
        
        # EMA Trend tests
        {
            'name': 'EMA_89_233_MNQ_1m',
            'strategy': 'ema_trend',
            'symbol': 'MNQ',
            'timeframe': '1m',
            'params': {'ema_short': 89, 'ema_long': 233}
        },
        {
            'name': 'EMA_50_200_MNQ_5m',
            'strategy': 'ema_trend',
            'symbol': 'MNQ',
            'timeframe': '5m',
            'params': {'ema_short': 50, 'ema_long': 200}
        },
    ]
    
    days = 30 if quick else 90
    
    # Run each test
    for i, test in enumerate(tests, 1):
        print(f"\n[{i}/{len(tests)}] Running: {test['name']}")
        print("-" * 80)
        
        try:
            result = await executor.run_backtest(
                strategy_name=test['strategy'],
                symbol=test['symbol'],
                timeframe=test['timeframe'],
                days=days,
                use_sample_data=quick,
                **test['params']
            )
            
            if result and result['result']:
                bt_result = result['result']
                
                # Store key metrics
                results.append({
                    'name': test['name'],
                    'strategy': test['strategy'],
                    'symbol': test['symbol'],
                    'timeframe': test['timeframe'],
                    'total_return_pct': bt_result.total_return_pct,
                    'sharpe_ratio': bt_result.sharpe_ratio,
                    'sortino_ratio': bt_result.sortino_ratio,
                    'max_drawdown_pct': bt_result.max_drawdown_pct,
                    'win_rate': bt_result.win_rate,
                    'profit_factor': bt_result.profit_factor,
                    'total_trades': bt_result.total_trades,
                    'expectancy': bt_result.expectancy
                })
                
                # Run Monte Carlo if requested
                if monte_carlo > 0 and bt_result.total_trades > 0:
                    print(f"\n🎲 Running {monte_carlo} Monte Carlo simulations...")
                    mc_results = await executor.run_monte_carlo(
                        bt_result,
                        num_simulations=monte_carlo
                    )
                    
                    if mc_results:
                        results[-1]['mc_mean_return'] = mc_results.get('mean_final_capital', 0) - 50000
                        results[-1]['mc_probability_profit'] = mc_results.get('probability_of_profit', 0)
        
        except Exception as e:
            print(f"❌ Error running {test['name']}: {e}")
            results.append({
                'name': test['name'],
                'error': str(e)
            })
    
    # Generate comparison report
    print("\n" + "="*80)
    print("BATCH BACKTEST RESULTS COMPARISON")
    print("="*80)
    
    # Sort by Sharpe ratio
    valid_results = [r for r in results if 'error' not in r]
    sorted_results = sorted(valid_results, key=lambda x: x.get('sharpe_ratio', 0), reverse=True)
    
    if sorted_results:
        print(f"\n{'Rank':<6} {'Name':<25} {'Return':<10} {'Sharpe':<8} {'Win%':<8} {'MaxDD%':<8} {'Trades':<8}")
        print("-" * 85)
        
        for i, r in enumerate(sorted_results, 1):
            print(f"{i:<6} {r['name']:<25} {r['total_return_pct']:>8.2f}% {r['sharpe_ratio']:>7.2f} {r['win_rate']:>7.1f}% {r['max_drawdown_pct']:>7.1f}% {r['total_trades']:>7}")
        
        # Best strategy
        best = sorted_results[0]
        print(f"\n{'='*80}")
        print(f"🏆 BEST STRATEGY: {best['name']}")
        print(f"{'='*80}")
        print(f"Strategy: {best['strategy']}")
        print(f"Symbol: {best['symbol']}")
        print(f"Timeframe: {best['timeframe']}")
        print(f"Return: {best['total_return_pct']:.2f}%")
        print(f"Sharpe: {best['sharpe_ratio']:.2f}")
        print(f"Win Rate: {best['win_rate']:.1f}%")
        print(f"Max DD: {best['max_drawdown_pct']:.1f}%")
        print(f"Trades: {best['total_trades']}")
        
        if 'mc_probability_profit' in best:
            print(f"\nMonte Carlo:")
            print(f"  Probability of Profit: {best['mc_probability_profit']*100:.1f}%")
    
    # Save results to CSV
    csv_file = f"backtest_results/batch_{TIMESTAMP}.csv"
    if valid_results:
        import csv
        with open(csv_file, 'w', newline='') as f:
            if valid_results:
                writer = csv.DictWriter(f, fieldnames=valid_results[0].keys())
                writer.writeheader()
                writer.writerows(valid_results)
        print(f"\n📊 Results saved to: {csv_file}")
    
    print(f"\n✅ Batch backtest complete!")
    print(f"   Total tests: {len(tests)}")
    print(f"   Successful: {len(valid_results)}")
    print(f"   Failed: {len(results) - len(valid_results)}")


async def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description='Run batch backtests')
    parser.add_argument('--quick', action='store_true',
                       help='Use sample data for faster testing')
    parser.add_argument('--monte-carlo', type=int, default=0,
                       help='Number of Monte Carlo simulations per test')
    
    args = parser.parse_args()
    
    await run_batch_tests(quick=args.quick, monte_carlo=args.monte_carlo)


if __name__ == '__main__':
    asyncio.run(main())
