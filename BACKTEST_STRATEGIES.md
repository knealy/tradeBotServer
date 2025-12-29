# Backtest Strategies Guide

**Important: Backtest strategies are DIFFERENT from live trading strategies!**

---

## 🎯 Available Backtest Strategies

The backtest engine has **3 built-in simple strategies** for testing:

### 1. ma_crossover (Moving Average Crossover)
```bash
python core/backtest_executor.py \
  --strategy=ma_crossover \
  --symbol=MNQ \
  --csv=historical_data/MNQ_1m_*.csv
```

**Parameters:**
- `--fast-period` (default: 10) - Fast MA period
- `--slow-period` (default: 50) - Slow MA period

**Logic:**
- Buy when fast MA crosses above slow MA
- Sell when fast MA crosses below slow MA

---

### 2. rsi_mean_reversion (RSI Mean Reversion)
```bash
python core/backtest_executor.py \
  --strategy=rsi_mean_reversion \
  --symbol=MNQ \
  --csv=historical_data/MNQ_1m_*.csv
```

**Parameters:**
- `--rsi-oversold` (default: 30) - Oversold level
- `--rsi-overbought` (default: 70) - Overbought level

**Logic:**
- Buy when RSI < oversold level
- Sell when RSI > overbought level

---

### 3. ema_trend (EMA Trend Following)
```bash
python core/backtest_executor.py \
  --strategy=ema_trend \
  --symbol=MNQ \
  --csv=historical_data/MNQ_1m_*.csv
```

**Parameters:**
- `--ema-short` (default: 89) - Short EMA period
- `--ema-long` (default: 233) - Long EMA period

**Logic:**
- Buy when short EMA crosses above long EMA
- Sell when short EMA crosses below long EMA

---

## ❌ Common Mistake

**These do NOT work in the backtest engine:**
```bash
# ❌ WRONG - These are live trading strategies
--strategy=overnight_range
--strategy=trend_scalping  
--strategy=simple_candle
--strategy=mean_reversion
```

**Why?** These are complex live trading strategies in `strategies/` folder.  
The backtest engine only has 3 simple built-in strategies for now.

---

## ✅ Correct Usage

### Test Different Strategies

```bash
# Test MA crossover
python core/backtest_executor.py \
  --strategy=ma_crossover \
  --symbol=MNQ \
  --csv=historical_data/MNQ_1m_*.csv

# Test RSI
python core/backtest_executor.py \
  --strategy=rsi_mean_reversion \
  --symbol=MNQ \
  --csv=historical_data/MNQ_1m_*.csv

# Test EMA trend
python core/backtest_executor.py \
  --strategy=ema_trend \
  --symbol=MNQ \
  --csv=historical_data/MNQ_1m_*.csv
```

### Test Different Parameters

```bash
# MA crossover with different periods
python core/backtest_executor.py \
  --strategy=ma_crossover \
  --symbol=MNQ \
  --csv=historical_data/MNQ_1m_*.csv \
  --fast-period=5 \
  --slow-period=20

# RSI with different levels
python core/backtest_executor.py \
  --strategy=rsi_mean_reversion \
  --symbol=MNQ \
  --csv=historical_data/MNQ_1m_*.csv \
  --rsi-oversold=25 \
  --rsi-overbought=75
```

---

## 📊 Comparison Table

| Backtest Engine | Live Trading |
|----------------|--------------|
| 3 simple strategies | 6 complex strategies |
| `ma_crossover` | `overnight_range` |
| `rsi_mean_reversion` | `mean_reversion` |
| `ema_trend` | `trend_following` |
| - | `simple_momentum` |
| - | `simple_candle` |
| - | `trend_scalping` |

**For backtesting:** Use the 3 backtest strategies  
**For live trading:** Use `strategy_executor.py` with live strategies

---

## 🔍 Why the Confusion?

**Before fix:**
- Invalid strategy names silently defaulted to `ma_crossover`
- All results looked identical because they were all running the same strategy!

**After fix:**
- Clear error message shows available strategies
- No more silent defaults

---

## 🎯 Examples

### What You Saw (All Identical Results)

```bash
# All of these ran ma_crossover (the default)
python core/backtest_executor.py --strategy=ma_crossover --csv=<file>
python core/backtest_executor.py --strategy=trend_scalper --csv=<file>  # Invalid, used default
python core/backtest_executor.py --strategy=overnight_range --csv=<file>  # Invalid, used default
```

**Result:** 66 trades, -0.58%, 24.2% win rate (all the same!)

---

### What You Should Do (Test Each Strategy)

```bash
# Test MA crossover
python core/backtest_executor.py --strategy=ma_crossover --symbol=MNQ --csv=historical_data/MNQ_1m_*.csv

# Test RSI (will have DIFFERENT results)
python core/backtest_executor.py --strategy=rsi_mean_reversion --symbol=MNQ --csv=historical_data/MNQ_1m_*.csv

# Test EMA (will have DIFFERENT results)  
python core/backtest_executor.py --strategy=ema_trend --symbol=MNQ --csv=historical_data/MNQ_1m_*.csv
```

**Result:** Each strategy will produce different trade counts, win rates, returns!

---

## 🚀 Next Steps

1. **Test all 3 strategies:**
   ```bash
   python core/backtest_executor.py --strategy=ma_crossover --symbol=MNQ --csv=historical_data/MNQ_1m_*.csv
   python core/backtest_executor.py --strategy=rsi_mean_reversion --symbol=MNQ --csv=historical_data/MNQ_1m_*.csv
   python core/backtest_executor.py --strategy=ema_trend --symbol=MNQ --csv=historical_data/MNQ_1m_*.csv
   ```

2. **Compare results** - Which has best Sharpe ratio? Win rate?

3. **Optimize best strategy:**
   ```bash
   python core/backtest_executor.py \
     --strategy=<best> \
     --symbol=MNQ \
     --csv=historical_data/MNQ_1m_*.csv \
     --optimize
   ```

4. **Validate with Monte Carlo:**
   ```bash
   python core/backtest_executor.py \
     --strategy=<best> \
     --symbol=MNQ \
     --csv=historical_data/MNQ_1m_*.csv \
     --monte-carlo=1000
   ```

---

## 💡 Pro Tip

Want to test your live trading strategies (overnight_range, trend_scalping, etc.)?

**You'll need to:**
1. Create backtest versions of those strategies
2. Add them to `backtest_executor.py`
3. Follow the pattern of the existing 3 strategies

That's a future enhancement! For now, use the 3 built-in strategies to validate your backtesting workflow.

---

## Summary

✅ **Use these strategies:** `ma_crossover`, `rsi_mean_reversion`, `ema_trend`  
❌ **Don't use:** `overnight_range`, `trend_scalping`, etc. (they're for live trading)  
🎯 **Test each one** to see different results!

**Now try the correct strategies!** 🚀
