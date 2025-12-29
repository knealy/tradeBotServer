# Backtesting Engine Guide

**Complete backtesting framework with Monte Carlo simulations**

---

## Quick Start

### 1. Install Dependencies

```bash
pip install pandas numpy scipy matplotlib seaborn
# or
pip install -r requirements.txt
```

### 2. Basic Backtest Example

```python
from core.backtest import HistoricalDataLoader, BacktestEngine, PerformanceMetrics
from strategies.trend_scalping_strategy import TrendScalpingStrategy

# Load historical data
loader = HistoricalDataLoader(broker_adapter=bot.broker_adapter)
data = await loader.load_from_api(
    symbol='MNQ',
    timeframe='1m',
    start_date=datetime(2024, 1, 1),
    end_date=datetime(2024, 12, 31)
)

# Or use sample data for testing
data = loader.get_sample_data(symbol='MNQ', days=30, timeframe='1m')

# Create strategy function
async def strategy_logic(bar_index, data_slice, engine):
    """Your strategy logic here"""
    # Example: Simple moving average crossover
    if len(data_slice) < 50:
        return None
    
    sma_fast = data_slice['close'].rolling(10).mean().iloc[-1]
    sma_slow = data_slice['close'].rolling(50).mean().iloc[-1]
    
    # Check for cross
    if sma_fast > sma_slow:
        return {'action': 'BUY', 'quantity': 1}
    elif sma_fast < sma_slow:
        return {'action': 'SELL', 'quantity': 1}
    
    return None

# Run backtest
engine = BacktestEngine(initial_capital=50000.0)
result = await engine.run(
    strategy_func=strategy_logic,
    data=data,
    symbol='MNQ',
    strategy_name='My Strategy'
)

# Print results
print(PerformanceMetrics.generate_report(result))
```

### 3. Monte Carlo Simulation

```python
from core.backtest import MonteCarloSimulator

# Run Monte Carlo on backtest results
mc = MonteCarloSimulator(initial_capital=50000.0)
mc_results = mc.run_simulations(
    trades=result.trades,
    num_simulations=1000
)

# Print Monte Carlo report
print(mc.generate_report(mc_results))
```

---

## Features

### Historical Data Loader

**Supported Sources:**
- ✅ TopStepX API (real historical data)
- ✅ CSV files (import your own data)
- ✅ JSON files
- ✅ Sample data generator (for testing)

**Supported Timeframes:**
- 30s, 1m, 2m, 5m, 15m, 30m, 1h, 4h, 1d
- Automatic resampling between timeframes

**Data Validation:**
- OHLC integrity checks
- Missing data detection
- Outlier detection

### Backtesting Engine

**Order Types:**
- Market orders (fills at next bar open + slippage)
- Limit orders (fills if price touches limit)
- Stop orders (fills if price touches stop + slippage)
- Bracket orders (entry + SL + TP)

**Realistic Simulation:**
- Configurable slippage (default: 0.5 ticks)
- Configurable commission (default: $2.50/contract round-trip)
- Proper fill logic (limits fill at limit price, stops with slippage)
- Position tracking (longs, shorts, netting)

**Outputs:**
- Complete trade history
- Equity curve
- Drawdown curve
- Comprehensive metrics

### Performance Metrics

**Return Metrics:**
- Total return ($ and %)
- CAGR (Compound Annual Growth Rate)
- Monthly returns
- Average trade P&L

**Risk Metrics:**
- Sharpe ratio (risk-adjusted return)
- Sortino ratio (downside risk only)
- Max drawdown ($ and %)
- Recovery factor
- Calmar ratio

**Trade Metrics:**
- Win rate
- Profit factor
- Expectancy
- Average win/loss
- Largest win/loss
- Consecutive wins/losses
- Average hold time

**Distribution Metrics:**
- Return skewness
- Return kurtosis
- Percentiles

### Monte Carlo Simulator

**Simulations:**
- Randomizes trade order
- Generates distribution of outcomes
- Calculates confidence intervals
- Estimates risk probabilities

**Outputs:**
- Return distribution (mean, median, percentiles)
- Confidence intervals (95%, 99%)
- Drawdown probabilities
- Probability of profit/loss
- Risk of ruin

**Use Cases:**
- Validate strategy robustness
- Understand worst-case scenarios
- Set position sizing
- Estimate capital requirements

---

## Usage Examples

### Example 1: Test Existing Strategy

```python
# Backtest overnight_range strategy
from strategies.overnight_range_strategy import OvernightRangeStrategy

async def backtest_overnight_range():
    # Load 6 months of data
    loader = HistoricalDataLoader(bot.broker_adapter)
    data = await loader.load_from_api(
        symbol='MNQ',
        timeframe='5m',
        start_date=datetime(2024, 6, 1),
        end_date=datetime(2024, 12, 1)
    )
    
    # Create strategy wrapper
    strategy_instance = OvernightRangeStrategy(bot)
    
    async def strategy_wrapper(bar_index, data_slice, engine):
        # Simulate strategy logic
        # (This requires adapting live strategy to backtest format)
        pass
    
    # Run backtest
    engine = BacktestEngine(initial_capital=50000.0)
    result = await engine.run(
        strategy_func=strategy_wrapper,
        data=data,
        symbol='MNQ',
        strategy_name='Overnight Range'
    )
    
    return result
```

### Example 2: Parameter Optimization

```python
# Test different EMA periods for trend_scalping
async def optimize_ema_parameters():
    results = {}
    
    for short_period in [21, 50, 89]:
        for long_period in [144, 200, 233]:
            if short_period >= long_period:
                continue
            
            # Modify strategy parameters
            os.environ['TREND_SCALP_EMA_SHORT'] = str(short_period)
            os.environ['TREND_SCALP_EMA_LONG'] = str(long_period)
            
            # Run backtest
            result = await run_backtest(...)
            
            results[f"{short_period}/{long_period}"] = {
                'sharpe': result.sharpe_ratio,
                'return': result.total_return_pct,
                'max_dd': result.max_drawdown_pct,
                'win_rate': result.win_rate
            }
    
    # Find best parameters
    best = max(results.items(), key=lambda x: x[1]['sharpe'])
    print(f"Best parameters: {best[0]}")
    print(f"  Sharpe: {best[1]['sharpe']:.2f}")
    print(f"  Return: {best[1]['return']:.2f}%")
```

### Example 3: Walk-Forward Analysis

```python
# Test strategy on rolling windows
async def walk_forward_test():
    train_months = 6
    test_months = 1
    
    results = []
    start_date = datetime(2024, 1, 1)
    
    while start_date < datetime(2024, 12, 1):
        train_end = start_date + timedelta(days=train_months * 30)
        test_end = train_end + timedelta(days=test_months * 30)
        
        # Train on train period (optimize parameters)
        train_data = await loader.load_from_api(
            'MNQ', '1m', start_date, train_end
        )
        # ... optimize parameters ...
        
        # Test on test period (out-of-sample)
        test_data = await loader.load_from_api(
            'MNQ', '1m', train_end, test_end
        )
        test_result = await engine.run(strategy_func, test_data, 'MNQ')
        
        results.append({
            'period': f"{train_end.date()} to {test_end.date()}",
            'return': test_result.total_return_pct,
            'sharpe': test_result.sharpe_ratio
        })
        
        start_date = test_end
    
    # Aggregate results
    avg_return = np.mean([r['return'] for r in results])
    avg_sharpe = np.mean([r['sharpe'] for r in results])
    print(f"Walk-forward results:")
    print(f"  Average return: {avg_return:.2f}%")
    print(f"  Average Sharpe: {avg_sharpe:.2f}")
```

---

## API Reference

### HistoricalDataLoader

```python
loader = HistoricalDataLoader(broker_adapter=bot.broker_adapter)

# Load from API
data = await loader.load_from_api(
    symbol='MNQ',
    timeframe='1m',
    start_date=datetime(2024, 1, 1),
    end_date=datetime(2024, 12, 31),
    limit=10000
)

# Load from CSV
data = loader.load_from_csv(
    filepath='data/mnq_1m.csv',
    symbol='MNQ',
    timestamp_col='timestamp'
)

# Generate sample data
data = loader.get_sample_data(
    symbol='MNQ',
    days=30,
    timeframe='1m'
)

# Resample to different timeframe
data_5m = loader.resample(data, '5m')

# Add indicators
data_with_indicators = loader.add_indicators(
    data,
    indicators=['ema_89', 'ema_233', 'atr_14', 'rsi_14']
)

# Validate data
is_valid = loader.validate_data(data)
```

### BacktestEngine

```python
engine = BacktestEngine(
    initial_capital=50000.0,
    commission_per_contract=2.50,
    slippage_ticks=0.5,
    point_value=2.0  # MNQ
)

# Run backtest
result = await engine.run(
    strategy_func=my_strategy,
    data=historical_data,
    symbol='MNQ',
    strategy_name='My Strategy',
    tick_size=0.25
)

# Access results
print(f"Total return: {result.total_return_pct:.2f}%")
print(f"Sharpe ratio: {result.sharpe_ratio:.2f}")
print(f"Max drawdown: {result.max_drawdown_pct:.2f}%")
print(f"Win rate: {result.win_rate:.1f}%")

# Get trade history
for trade in result.trades:
    print(f"{trade.entry_time}: {trade.side.value} {trade.quantity} @ ${trade.entry_price:.2f} -> ${trade.exit_price:.2f} = ${trade.pnl:.2f}")
```

### PerformanceMetrics

```python
# Calculate individual metrics
sharpe = PerformanceMetrics.calculate_sharpe_ratio(returns)
sortino = PerformanceMetrics.calculate_sortino_ratio(returns)
max_dd, max_dd_pct, peak_idx, trough_idx = PerformanceMetrics.calculate_max_drawdown(equity_curve)
profit_factor = PerformanceMetrics.calculate_profit_factor(trades)
win_rate = PerformanceMetrics.calculate_win_rate(trades)

# Generate full report
report = PerformanceMetrics.generate_report(result)
print(report)

# Analyze trade distribution
dist = PerformanceMetrics.analyze_trade_distribution(trades)
print(f"Mean: ${dist['mean']:.2f}")
print(f"Std: ${dist['std']:.2f}")
print(f"Skewness: {dist['skewness']:.2f}")
```

### MonteCarloSimulator

```python
mc = MonteCarloSimulator(initial_capital=50000.0)

# Run simulations
mc_results = mc.run_simulations(
    trades=result.trades,
    num_simulations=1000,
    seed=42  # For reproducibility
)

# Access results
print(f"Mean return: {mc_results['mean_final_capital'] - 50000:.2f}")
print(f"95% CI: [{mc_results['return_95pct_ci'][0]:.2f}%, {mc_results['return_95pct_ci'][1]:.2f}%]")
print(f"Probability of profit: {mc_results['probability_of_profit']*100:.1f}%")

# Bootstrap returns
bootstrapped = mc.bootstrap_returns(trades=result.trades, num_samples=1000)
print(f"Bootstrap mean: ${np.mean(bootstrapped):.2f}")

# Worst-case analysis
worst_case = mc.analyze_worst_case(trades=result.trades, percentile=5.0)
print(f"Worst 5% average loss: ${worst_case['average_loss']:.2f}")
```

---

## Best Practices

### 1. Data Quality

✅ **DO:**
- Use sufficient data (minimum 6 months)
- Validate data before backtesting
- Check for gaps and missing bars
- Use realistic timeframes

❌ **DON'T:**
- Use incomplete data
- Ignore data quality issues
- Test on too little data (<3 months)

### 2. Overfitting Prevention

✅ **DO:**
- Use out-of-sample testing
- Walk-forward analysis
- Monte Carlo simulations
- Test on multiple symbols/periods

❌ **DON'T:**
- Optimize on all data
- Trust single backtest
- Ignore Monte Carlo warnings
- Over-optimize parameters

### 3. Realistic Assumptions

✅ **DO:**
- Include slippage (0.5-2 ticks)
- Include commission ($2.50-5.00/contract)
- Use realistic fill logic
- Account for market hours

❌ **DON'T:**
- Assume perfect fills
- Ignore costs
- Use look-ahead bias
- Assume 24/7 trading

### 4. Risk Management

✅ **DO:**
- Test with proper position sizing
- Include stop losses
- Check drawdown limits
- Validate risk of ruin

❌ **DON'T:**
- Use unlimited position size
- Ignore max drawdown
- Skip risk metrics
- Over-leverage

---

## Interpreting Results

### Good Strategy Characteristics

✅ **Sharpe Ratio:** >1.5 (excellent: >2.0)  
✅ **Sortino Ratio:** >2.0 (excellent: >3.0)  
✅ **Win Rate:** >50% (excellent: >60%)  
✅ **Profit Factor:** >1.5 (excellent: >2.0)  
✅ **Max Drawdown:** <15% (excellent: <10%)  
✅ **Recovery Factor:** >3.0 (excellent: >5.0)

### Red Flags

❌ **Sharpe < 1.0** - Risk not worth reward  
❌ **Win rate < 40%** - Too many losses  
❌ **Profit factor < 1.0** - Losing strategy  
❌ **Max DD > 25%** - Excessive risk  
❌ **Few trades (<50)** - Insufficient data

### Monte Carlo Interpretation

**Probability of Profit:**
- >80%: Robust strategy
- 60-80%: Reasonable
- <60%: Questionable

**95% Confidence Interval:**
- Tight CI: Consistent results
- Wide CI: High variability

**Drawdown Probability:**
- P(DD >10%) <20%: Good
- P(DD >20%) <5%: Excellent

---

## Troubleshooting

### Issue: "Insufficient data"

**Cause:** Not enough bars for indicators

**Solution:**
- Increase `limit` parameter
- Use longer date range
- Check data availability

### Issue: "No trades generated"

**Cause:** Strategy conditions not met

**Solution:**
- Check strategy logic
- Verify indicator calculations
- Lower entry thresholds (for testing)
- Check data timeframe matches strategy

### Issue: "Unrealistic returns"

**Cause:** Missing slippage/commission or look-ahead bias

**Solution:**
- Verify slippage settings
- Check commission calculation
- Review strategy logic for future data usage
- Test with conservative assumptions

---

## Examples

### Simple MA Crossover

```python
async def ma_crossover(bar_index, data, engine):
    if len(data) < 50:
        return None
    
    fast_ma = data['close'].rolling(10).mean().iloc[-1]
    slow_ma = data['close'].rolling(50).mean().iloc[-1]
    
    # Check previous cross
    if len(data) < 51:
        return None
    
    fast_ma_prev = data['close'].iloc[:-1].rolling(10).mean().iloc[-1]
    slow_ma_prev = data['close'].iloc[:-1].rolling(50).mean().iloc[-1]
    
    # Bullish cross
    if fast_ma_prev <= slow_ma_prev and fast_ma > slow_ma:
        return {'action': 'BUY', 'quantity': 1}
    
    # Bearish cross
    if fast_ma_prev >= slow_ma_prev and fast_ma < slow_ma:
        return {'action': 'SELL', 'quantity': 1}
    
    return None
```

### RSI Mean Reversion

```python
async def rsi_mean_reversion(bar_index, data, engine):
    if len(data) < 14:
        return None
    
    # Calculate RSI
    delta = data['close'].diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = -delta.where(delta < 0, 0).rolling(14).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    current_rsi = rsi.iloc[-1]
    
    # Oversold: Buy
    if current_rsi < 30:
        return {'action': 'BUY', 'quantity': 1}
    
    # Overbought: Sell
    if current_rsi > 70:
        return {'action': 'SELL', 'quantity': 1}
    
    return None
```

---

## Performance Tips

1. **Cache historical data:**
   ```python
   loader._data_cache[cache_key] = data  # Reuse for multiple tests
   ```

2. **Use sample data for development:**
   ```python
   data = loader.get_sample_data(days=30)  # Fast testing
   ```

3. **Parallelize multiple backtests:**
   ```python
   results = await asyncio.gather(*[
       run_backtest(symbol) for symbol in ['MNQ', 'MES', 'MGC']
   ])
   ```

4. **Batch Monte Carlo:**
   ```python
   # Run once with many simulations vs many times with few
   mc.run_simulations(trades, num_simulations=10000)  # Better
   ```

---

## Summary

**Backtesting Engine Capabilities:**
- ✅ Event-driven simulation
- ✅ Realistic order fills
- ✅ Slippage and commission
- ✅ Comprehensive metrics
- ✅ Monte Carlo analysis
- ✅ Multiple data sources
- ✅ Parameter optimization
- ✅ Walk-forward testing

**Use this to:**
1. Validate strategy profitability
2. Optimize parameters
3. Understand risks
4. Set realistic expectations
5. Compare strategies
6. Build confidence

**Start backtesting your strategies today!** 🔬
