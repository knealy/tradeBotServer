"""
Test Backtesting Engine

Simple test to validate backtesting functionality.
"""

import sys
import os
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

import asyncio
import numpy as np
import pytest
from core.backtest import HistoricalDataLoader, BacktestEngine, PerformanceMetrics, MonteCarloSimulator


@pytest.mark.asyncio
async def test_sample_backtest():
    """Test backtest with sample data."""
    # Generate sample data
    loader = HistoricalDataLoader()
    data = loader.get_sample_data(symbol='MNQ', days=30, timeframe='1m')
    
    assert len(data) > 0, "Should generate sample data"
    print(f"✅ Generated {len(data)} bars of sample data")
    
    # Simple moving average strategy
    async def ma_crossover(bar_index, data_slice, engine):
        if len(data_slice) < 50:
            return None
        
        fast_ma = data_slice['close'].rolling(10).mean().iloc[-1]
        slow_ma = data_slice['close'].rolling(50).mean().iloc[-1]
        
        # Get previous values for cross detection
        if len(data_slice) < 51:
            return None
        
        fast_prev = data_slice['close'].iloc[:-1].rolling(10).mean().iloc[-1]
        slow_prev = data_slice['close'].iloc[:-1].rolling(50).mean().iloc[-1]
        
        # Bullish cross
        if fast_prev <= slow_prev and fast_ma > slow_ma:
            return {'action': 'BUY', 'quantity': 1}
        
        # Bearish cross
        if fast_prev >= slow_prev and fast_ma < slow_ma:
            return {'action': 'SELL', 'quantity': 1}
        
        return None
    
    # Run backtest
    engine = BacktestEngine(initial_capital=50000.0)
    result = await engine.run(
        strategy_func=ma_crossover,
        data=data,
        symbol='MNQ',
        strategy_name='MA Crossover Test'
    )
    
    # Validate results
    assert result is not None
    assert result.total_trades >= 0
    assert result.initial_capital == 50000.0
    
    # Print results
    print("\n" + PerformanceMetrics.generate_report(result))
    
    return result


@pytest.mark.asyncio
async def test_monte_carlo():
    """Test Monte Carlo simulations."""
    # First run backtest
    result = await test_sample_backtest()
    
    if len(result.trades) == 0:
        print("⚠️  No trades generated, skipping Monte Carlo")
        return
    
    # Run Monte Carlo
    print("\n🎲 Running Monte Carlo simulations...")
    mc = MonteCarloSimulator(initial_capital=50000.0)
    mc_results = mc.run_simulations(result.trades, num_simulations=100)
    
    # Validate Monte Carlo results
    assert mc_results is not None
    assert 'num_simulations' in mc_results
    assert mc_results['num_simulations'] == 100
    
    print(f"\nMonte Carlo Results:")
    print(f"  Mean return: {np.mean([r['total_return'] for r in mc_results['simulation_results']]):.2f}%")
    print(f"  95% CI: [{mc_results['return_95pct_ci'][0]:.2f}%, {mc_results['return_95pct_ci'][1]:.2f}%]")
    print(f"  Probability of profit: {mc_results['probability_of_profit']*100:.1f}%")
    
    print("\n✅ Backtest and Monte Carlo complete!")


def test_data_validation():
    """Test data validation."""
    loader = HistoricalDataLoader()
    data = loader.get_sample_data(symbol='MNQ', days=10, timeframe='1m')
    
    # Validate data
    is_valid = loader.validate_data(data)
    assert is_valid, "Sample data should be valid"


def test_data_resampling():
    """Test data resampling."""
    loader = HistoricalDataLoader()
    data_1m = loader.get_sample_data(symbol='MNQ', days=1, timeframe='1m')
    
    # Resample to 5m
    data_5m = loader.resample(data_1m, '5m')
    
    assert len(data_5m) < len(data_1m), "Resampled data should have fewer bars"
    assert len(data_5m) > 0, "Should have some resampled bars"


# Standalone script for manual testing
async def main():
    """Run backtest manually."""
    print("=" * 80)
    print("RUNNING MANUAL BACKTEST")
    print("=" * 80)
    
    result = await test_sample_backtest()
    
    if len(result.trades) > 0:
        await test_monte_carlo()
    else:
        print("\n⚠️  No trades generated - strategy may need adjustment")
        print("    Try different MA periods or longer data period")


if __name__ == '__main__':
    # Can run directly: python tests/test_backtest.py
    asyncio.run(main())
