# Backtest Quick Start Guide

**Get started with backtesting in 5 minutes**

---

## Installation

```bash
# Install dependencies (if not already done)
pip install -r requirements.txt
```

**Required packages:**
- pandas, numpy, scipy (data analysis)
- matplotlib, seaborn (visualization)

---

## 1. Simple Test (30 seconds)

Run a quick test with sample data:

```bash
python core/backtest_executor.py --strategy=ma_crossover --symbol=MNQ --days=30 --sample
```

**Expected output:**
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
   Total Trades: 75
   Win Rate: 54.7%
   Total P&L: $2,450.00
   Return: 4.90%
   Max Drawdown: 8.2%
   Sharpe Ratio: 1.35
```

---

## 2. Test All Strategies (2 minutes)

```bash
# MA Crossover
python core/backtest_executor.py --strategy=ma_crossover --symbol=MNQ --days=30 --sample

# RSI Mean Reversion
python core/backtest_executor.py --strategy=rsi_mean_reversion --symbol=MNQ --days=30 --sample

# EMA Trend
python core/backtest_executor.py --strategy=ema_trend --symbol=MNQ --days=30 --sample
```

---

## 3. Run Monte Carlo (1 minute)

Add risk analysis with Monte Carlo:

```bash
python core/backtest_executor.py \
  --strategy=ma_crossover \
  --symbol=MNQ \
  --days=30 \
  --sample \
  --monte-carlo=1000
```

**Additional output:**
```
🎲 Running 1000 Monte Carlo simulations...

MONTE CARLO SIMULATION RESULTS
  Mean Return: 4.5%
  95% CI: [-2.1%, 11.2%]
  Probability of Profit: 72.3%
  Mean Max Drawdown: 9.1%
```

---

## 4. Optimize Parameters (5 minutes)

Find best parameters:

```bash
python core/backtest_executor.py \
  --strategy=ma_crossover \
  --symbol=MNQ \
  --days=30 \
  --sample \
  --optimize \
  --param=fast_period=5:30:5 \
  --param=slow_period=30:100:10
```

**Output:**
```
PARAMETER OPTIMIZATION: MA_CROSSOVER
Testing 35 parameter combinations...

[1/35] Testing: {'fast_period': 5, 'slow_period': 30}
[2/35] Testing: {'fast_period': 5, 'slow_period': 40}
   🎯 NEW BEST: sharpe_ratio=1.45
...
[35/35] Testing: {'fast_period': 30, 'slow_period': 100}

OPTIMIZATION COMPLETE
Best sharpe_ratio: 1.82
Best parameters: {'fast_period': 10, 'slow_period': 50}
```

---

## 5. Batch Test All (5 minutes)

Test everything at once:

```bash
# Shell script (fastest)
./scripts/batch_backtest.sh

# Python script (more control)
python scripts/batch_backtest.py --quick
```

**Output:**
- Tests 3 strategies on 5 symbols
- Tests 4 timeframes
- Tests parameter variations
- Generates comparison report
- Saves to `backtest_results/` folder

---

## Commands from trading_bot CLI

```bash
# Start trading bot
python trading_bot.py

# From CLI prompt:
backtest              # Show help
backtest ma_crossover MNQ  # Show command to run
```

**Note:** Full backtesting runs via `backtest_executor.py` for now.  
**Coming soon:** Integrated `backtest` command with direct execution.

---

## Understanding Results

### Key Metrics

| Metric | Good | Excellent | What it means |
|--------|------|-----------|---------------|
| **Total Return** | >10% | >20% | Raw profit |
| **Sharpe Ratio** | >1.5 | >2.0 | Risk-adjusted return |
| **Win Rate** | >50% | >60% | % profitable trades |
| **Profit Factor** | >1.5 | >2.0 | Wins / losses ratio |
| **Max Drawdown** | <15% | <10% | Worst decline |

### Traffic Light System

🟢 **GREEN (Trade it!):**
- Sharpe >1.5
- Win rate >55%
- Max DD <12%
- Profit factor >1.8
- Monte Carlo probability of profit >70%

🟡 **YELLOW (Needs work):**
- Sharpe 1.0-1.5
- Win rate 45-55%
- Max DD 12-20%
- Optimize parameters first

🔴 **RED (Don't trade):**
- Sharpe <1.0
- Win rate <45%
- Max DD >20%
- Negative expectancy

---

## Next Steps

### This Week

1. **Run batch tests:**
   ```bash
   python scripts/batch_backtest.py --quick --monte-carlo=1000
   ```

2. **Review results:**
   ```bash
   cat backtest_results/batch_*.csv | column -t -s,
   ```

3. **Pick best strategy** (highest Sharpe ratio)

4. **Optimize it:**
   ```bash
   python core/backtest_executor.py --strategy=<best> --symbol=MNQ --days=90 --optimize
   ```

5. **Validate with Monte Carlo:**
   ```bash
   python core/backtest_executor.py --strategy=<best> --symbol=MNQ --days=90 --monte-carlo=1000
   ```

### Next Week

6. **Test with real data:**
   ```bash
   python core/backtest_executor.py --strategy=<best> --symbol=MNQ --start=2024-01-01 --end=2024-12-31
   ```

7. **Walk-forward test** (train/test split)

8. **Paper trade for 1 week**

9. **Go live** if results match backtest

---

## Pro Tips

### 1. Always Use Monte Carlo

```bash
# Add --monte-carlo=1000 to EVERY important backtest
```

**Why?** Shows range of outcomes, not just one result.

### 2. Test on Multiple Symbols

```bash
for sym in MNQ MES MGC; do
  python core/backtest_executor.py --strategy=ma_crossover --symbol=$sym --days=90
done
```

**Why?** Strategy might work on one symbol but not others.

### 3. Optimize Conservatively

```bash
# Use wide ranges, large steps
--param=fast_period=5:50:5
--param=slow_period=50:200:10
```

**Why?** Prevents overfitting.

### 4. Out-of-Sample Test

```bash
# Train on first 60 days
python core/backtest_executor.py --strategy=ma_crossover --symbol=MNQ --days=60 --optimize

# Test on next 30 days (out-of-sample)
python core/backtest_executor.py --strategy=ma_crossover --symbol=MNQ --days=30 --fast-period=<optimized>
```

**Why?** Validates strategy isn't overfit.

### 5. Compare to Benchmark

```bash
# Buy and hold
python core/backtest_executor.py --strategy=ma_crossover --symbol=MNQ --days=90 --fast-period=999 --slow-period=1000
```

**Why?** Strategy should beat simple buy-and-hold.

---

## Troubleshooting

### Issue: "No data returned"

**Solution:** 
- Try `--sample` flag
- Check date range is valid
- Verify symbol name

### Issue: "No trades generated"

**Solution:**
- Strategy conditions too strict
- Adjust parameters
- Try different timeframe

### Issue: "Unrealistic results"

**Solution:**
- Check slippage settings
- Verify commission calculation
- Look for look-ahead bias

---

## Summary

**You now have:**
- ✅ CLI backtesting tool
- ✅ Parameter optimization
- ✅ Monte Carlo simulations
- ✅ Batch testing scripts
- ✅ Comprehensive metrics

**Start testing:**
```bash
python core/backtest_executor.py --strategy=ma_crossover --symbol=MNQ --days=30 --sample --monte-carlo=1000
```

**This validates your strategies before risking real capital!** 🔬

---

**Related Docs:**
- `docs/BACKTEST_CLI_GUIDE.md` - Full CLI reference
- `docs/BACKTESTING_GUIDE.md` - Comprehensive guide
- `COMPREHENSIVE_UPDATE_SUMMARY.md` - All updates

**Happy backtesting!** 🚀
