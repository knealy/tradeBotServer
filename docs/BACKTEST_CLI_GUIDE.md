# Backtest CLI Guide

**Run backtests from command line with extensive parameters**

---

## Quick Start

### Basic Backtest (Sample Data)

```bash
# Test MA crossover on MNQ with 30 days of sample data
python core/backtest_executor.py --strategy=ma_crossover --symbol=MNQ --days=30 --sample
```

### Backtest with Real Data (CSV)

```bash
# Step 1: Export real data
python scripts/export_history.py --symbol=MNQ --timeframe=1m --days=90

# Step 2: Backtest with real data
python core/backtest_executor.py --strategy=ma_crossover --symbol=MNQ --csv=historical_data/MNQ_1m_*.csv

# Test RSI strategy with specific parameters
python core/backtest_executor.py --strategy=rsi_mean_reversion --symbol=MES --days=60 --rsi-oversold=25 --rsi-overbought=75

# Test EMA trend following
python core/backtest_executor.py --strategy=ema_trend --symbol=MNQ --days=90 --ema-short=50 --ema-long=200
```

### Backtest with Monte Carlo

```bash
# Run backtest and 1000 Monte Carlo simulations
python core/backtest_executor.py --strategy=ma_crossover --symbol=MNQ --days=30 --monte-carlo=1000
```

### Parameter Optimization

```bash
# Optimize MA periods
python core/backtest_executor.py --strategy=ma_crossover --symbol=MNQ --days=30 --optimize --param=fast_period=5:30:5 --param=slow_period=30:200:20

# Optimize RSI levels
python core/backtest_executor.py --strategy=rsi_mean_reversion --symbol=MNQ --days=60 --optimize --param=rsi_oversold=20:40:5 --param=rsi_overbought=60:80:5
```

---

## Command Reference

### Required Arguments

| Argument | Description | Example |
|----------|-------------|---------|
| `--strategy` | Strategy to test | `ma_crossover`, `rsi_mean_reversion`, `ema_trend` |
| `--symbol` | Trading symbol | `MNQ`, `MES`, `MYM`, `M2K`, `MGC` |

### Data Arguments

| Argument | Description | Example |
|----------|-------------|---------|
| `--timeframe` | Bar interval | `30s`, `1m`, `2m`, `5m`, `15m`, `1h` |
| `--days` | Days to backtest | `30`, `60`, `90`, `180`, `365` |
| `--start` | Start date | `2024-01-01` |
| `--end` | End date | `2024-12-31` |
| `--sample` | Use generated data | (flag, no value) |
| `--csv` | CSV file path | `historical_data/MNQ_1m_20241217.csv` |

### Backtest Parameters

| Argument | Description | Default |
|----------|-------------|---------|
| `--capital` | Initial capital | `50000` |
| `--monte-carlo` | MC simulations | (optional) |

### Strategy Parameters

**MA Crossover:**
| Argument | Description | Default |
|----------|-------------|---------|
| `--fast-period` | Fast MA period | `10` |
| `--slow-period` | Slow MA period | `50` |

**RSI Mean Reversion:**
| Argument | Description | Default |
|----------|-------------|---------|
| `--rsi-oversold` | Oversold level | `30` |
| `--rsi-overbought` | Overbought level | `70` |

**EMA Trend:**
| Argument | Description | Default |
|----------|-------------|---------|
| `--ema-short` | Short EMA period | `89` |
| `--ema-long` | Long EMA period | `233` |

### Optimization Arguments

| Argument | Description | Example |
|----------|-------------|---------|
| `--optimize` | Enable optimization | (flag) |
| `--param` | Parameter range | `fast_period=5:30:5` |

**Parameter format:** `name=min:max:step`

---

## Example Commands

### 1. Quick Test with Sample Data

```bash
python core/backtest_executor.py \
  --strategy=ma_crossover \
  --symbol=MNQ \
  --days=30 \
  --sample
```

**Output:**
```
================================================================================
BACKTESTING: MA_CROSSOVER
================================================================================
Symbol: MNQ
Timeframe: 1m
Initial Capital: $50,000.00

📊 Generating sample data...
✅ Generated 43200 bars of sample data

🔬 Running backtest...
✅ Backtest complete:
   Total Trades: ~50-100
   Win Rate: ~50-60%
   Total P&L: $XXX
   Return: XX%
```

### 2. Real Data from CSV (Recommended)

```bash
# Step 1: Export real data
python scripts/export_history.py --symbol=MNQ --timeframe=1m --days=90

# Step 2: Backtest with real data
python core/backtest_executor.py \
  --strategy=ma_crossover \
  --symbol=MNQ \
  --csv=historical_data/MNQ_1m_20241217_123456.csv
```

See `docs/BACKTEST_DATA_GUIDE.md` for complete CSV data guide.

### 3. Real Data from API

```bash
python core/backtest_executor.py \
  --strategy=ema_trend \
  --symbol=MNQ \
  --start=2024-01-01 \
  --end=2024-12-31 \
  --timeframe=5m \
  --ema-short=89 \
  --ema-long=233
```

### 3. Parameter Optimization

```bash
python core/backtest_executor.py \
  --strategy=ma_crossover \
  --symbol=MNQ \
  --days=60 \
  --optimize \
  --param=fast_period=5:30:5 \
  --param=slow_period=30:200:20
```

**Output:**
```
================================================================================
PARAMETER OPTIMIZATION: MA_CROSSOVER
================================================================================
Symbol: MNQ
Optimizing: sharpe_ratio
Parameters: {'fast_period': [5, 10, 15, 20, 25, 30], 
             'slow_period': [30, 50, 70, 90, ...]}

🔬 Testing 60 parameter combinations...

[1/60] Testing: {'fast_period': 5, 'slow_period': 30}
   🎯 NEW BEST: sharpe_ratio=1.45

[2/60] Testing: {'fast_period': 5, 'slow_period': 50}
   ...

================================================================================
OPTIMIZATION COMPLETE
================================================================================
Best sharpe_ratio: 1.82
Best parameters: {'fast_period': 10, 'slow_period': 50}
```

### 4. Monte Carlo Analysis

```bash
python core/backtest_executor.py \
  --strategy=rsi_mean_reversion \
  --symbol=MES \
  --days=90 \
  --monte-carlo=1000
```

**Output includes:**
```
🎲 Running 1000 Monte Carlo simulations...

================================================================================
MONTE CARLO SIMULATION RESULTS
================================================================================

Simulations: 1,000
Trades per simulation: 45

RETURN DISTRIBUTION
  Mean Return: 12.5%
  Median Return: 11.8%
  
  Percentiles:
    5th:  -2.3%
    25th:  8.1%
    50th: 11.8%
    75th: 16.2%
    95th: 25.4%
  
  95% CI: [5.2%, 19.8%]

RISK METRICS
  Mean Max Drawdown: 8.5%
  Probability of Profit: 87.3%
  Probability of >10% DD: 12.5%
```

---

## Available Strategies

### 1. MA Crossover (`ma_crossover`)

**Logic:**
- Buy when fast MA crosses above slow MA
- Sell when fast MA crosses below slow MA

**Parameters:**
- `--fast-period`: Fast MA period (default: 10)
- `--slow-period`: Slow MA period (default: 50)

**Good for:** Trending markets, smooth price action

### 2. RSI Mean Reversion (`rsi_mean_reversion`)

**Logic:**
- Buy when RSI < oversold level
- Sell when RSI > overbought level

**Parameters:**
- `--rsi-oversold`: Oversold threshold (default: 30)
- `--rsi-overbought`: Overbought threshold (default: 70)

**Good for:** Range-bound markets, choppy conditions

### 3. EMA Trend (`ema_trend`)

**Logic:**
- Buy when short EMA crosses above long EMA
- Sell when short EMA crosses below long EMA

**Parameters:**
- `--ema-short`: Short EMA period (default: 89)
- `--ema-long`: Long EMA period (default: 233)

**Good for:** Strong trends, longer timeframes

---

## Batch Testing Script

Create a script to test multiple configurations:

**`run_batch_backtests.sh`:**
```bash
#!/bin/bash

# Test different symbols
for symbol in MNQ MES MGC; do
  echo "Testing $symbol..."
  python core/backtest_executor.py \
    --strategy=ma_crossover \
    --symbol=$symbol \
    --days=90 \
    --monte-carlo=100
done

# Test different timeframes
for tf in 1m 5m 15m; do
  echo "Testing $tf timeframe..."
  python core/backtest_executor.py \
    --strategy=ema_trend \
    --symbol=MNQ \
    --timeframe=$tf \
    --days=60
done

# Test different periods
for fast in 5 10 20; do
  for slow in 30 50 100; do
    echo "Testing MA($fast/$slow)..."
    python core/backtest_executor.py \
      --strategy=ma_crossover \
      --symbol=MNQ \
      --fast-period=$fast \
      --slow-period=$slow \
      --days=30 \
      --sample
  done
done
```

**Run it:**
```bash
chmod +x run_batch_backtests.sh
./run_batch_backtests.sh > batch_results.txt
```

---

## Integration with trading_bot CLI (Coming Soon)

**Planned commands:**
```bash
# From trading_bot CLI
backtest ma_crossover MNQ --days=30
backtest_optimize rsi_mean_reversion MNQ --param=rsi_oversold=20:40:5
backtest_monte_carlo ema_trend MNQ --sims=1000
chart --backtest ma_crossover MNQ  # Bar-by-bar playback
```

---

## Tips & Best Practices

### 1. Start with Sample Data

```bash
# Quick test
python core/backtest_executor.py --strategy=ma_crossover --symbol=MNQ --days=30 --sample
```

Sample data is:
- Fast (generates instantly)
- Good for testing logic
- Reproducible
- Free (no API calls)

### 2. Use Longer Periods for Validation

```bash
# Proper validation (6+ months)
python core/backtest_executor.py --strategy=ema_trend --symbol=MNQ --days=180
```

### 3. Always Run Monte Carlo

```bash
# Add --monte-carlo=1000 to understand variability
python core/backtest_executor.py --strategy=ma_crossover --symbol=MNQ --days=90 --monte-carlo=1000
```

Monte Carlo shows:
- Range of possible outcomes
- Probability of profit/loss
- Risk of ruin
- Confidence intervals

### 4. Optimize Before Trading

```bash
# Find best parameters
python core/backtest_executor.py \
  --strategy=ma_crossover \
  --symbol=MNQ \
  --days=180 \
  --optimize \
  --param=fast_period=5:30:5 \
  --param=slow_period=30:200:20
```

### 5. Test on Multiple Symbols

```bash
# Test robustness across symbols
for sym in MNQ MES MYM MGC; do
  python core/backtest_executor.py --strategy=ma_crossover --symbol=$sym --days=90
done
```

### 6. Test Multiple Timeframes

```bash
# Find best timeframe
for tf in 1m 2m 5m 15m 30m 1h; do
  python core/backtest_executor.py --strategy=ema_trend --symbol=MNQ --timeframe=$tf --days=60
done
```

---

## Performance Metrics Explained

### Sharpe Ratio
- Risk-adjusted return
- **Target:** >1.5 (good), >2.0 (excellent)
- Higher = better return per unit of risk

### Sortino Ratio  
- Like Sharpe but only penalizes downside
- **Target:** >2.0 (good), >3.0 (excellent)

### Profit Factor
- Gross profit / gross loss
- **Target:** >1.5 (good), >2.0 (excellent)

### Max Drawdown
- Largest peak-to-trough decline
- **Target:** <15% (good), <10% (excellent)

### Win Rate
- Percentage of profitable trades
- **Target:** >50% (good), >60% (excellent)

### Expectancy
- Average profit per trade
- **Target:** Positive and >$50/trade

---

## Next Steps

1. **Test all strategies:**
   ```bash
   python core/backtest_executor.py --strategy=ma_crossover --symbol=MNQ --days=90 --monte-carlo=1000
   python core/backtest_executor.py --strategy=rsi_mean_reversion --symbol=MNQ --days=90 --monte-carlo=1000
   python core/backtest_executor.py --strategy=ema_trend --symbol=MNQ --days=90 --monte-carlo=1000
   ```

2. **Optimize best strategy:**
   ```bash
   python core/backtest_executor.py --strategy=<best> --symbol=MNQ --days=180 --optimize
   ```

3. **Validate on real data:**
   ```bash
   python core/backtest_executor.py --strategy=<best> --symbol=MNQ --start=2024-01-01 --end=2024-12-31
   ```

4. **Paper trade** to verify results match backtest

5. **Go live** once validated

---

**Start backtesting now!** 🔬
