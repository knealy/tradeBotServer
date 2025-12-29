# Backtest with Real Data - Quick Start

**Use your own historical data for backtesting!**

---

## 🎯 Quick Start (2 minutes)

### Step 1: Export Real Data

```bash
python scripts/export_history.py --symbol=MNQ --timeframe=1m --days=90
```

**Output:**
```
✅ Exported 129600 bars to historical_data/MNQ_1m_20241217_123456.csv
```

### Step 2: Backtest with Real Data

```bash
python core/backtest_executor.py \
  --strategy=ma_crossover \
  --symbol=MNQ \
  --csv=historical_data/MNQ_1m_20241217_123456.csv
```

**Done!** You're now backtesting with real market data! 🎉

---

## 📊 Three Methods

### Method 1: Export Script (Recommended)

**Easiest way to get real data:**

```bash
# Quick export (last 90 days)
python scripts/export_history.py --symbol=MNQ --timeframe=1m --days=90

# Date range
python scripts/export_history.py --symbol=MES --timeframe=5m --start=2024-01-01 --end=2024-12-31

# Custom output
python scripts/export_history.py --symbol=MNQ --days=180 --output=my_data/mnq.csv
```

Then backtest:
```bash
python core/backtest_executor.py --strategy=ma_crossover --symbol=MNQ --csv=<filename>.csv
```

---

### Method 2: History Command

**Export from trading_bot CLI:**

```bash
# Start bot
python trading_bot.py --account_select=1

# From CLI:
history MNQ 1m 2024-01-01 2024-12-31 csv
```

**Output:** `historical_data/MNQ_1m_20241217_123456.csv`

Then backtest:
```bash
python core/backtest_executor.py --strategy=ma_crossover --symbol=MNQ --csv=historical_data/MNQ_*.csv
```

---

### Method 3: Your Own CSV

**Use any CSV with OHLCV data:**

**Required format:**
```csv
timestamp,open,high,low,close,volume
2024-01-01 09:30:00,25300.00,25310.00,25295.00,25305.00,1000
2024-01-01 09:31:00,25305.00,25315.00,25300.00,25310.00,1200
...
```

Then backtest:
```bash
python core/backtest_executor.py --strategy=ma_crossover --symbol=MNQ --csv=your_data.csv
```

---

## 💡 Examples

### Example 1: Quick Test

```bash
# Export 30 days
python scripts/export_history.py --symbol=MNQ --timeframe=1m --days=30

# Backtest
python core/backtest_executor.py \
  --strategy=ma_crossover \
  --symbol=MNQ \
  --csv=historical_data/MNQ_1m_*.csv
```

### Example 2: With Monte Carlo

```bash
# Export 90 days
python scripts/export_history.py --symbol=MNQ --timeframe=1m --days=90

# Backtest with 1000 simulations
python core/backtest_executor.py \
  --strategy=ma_crossover \
  --symbol=MNQ \
  --csv=historical_data/MNQ_1m_*.csv \
  --monte-carlo=1000
```

### Example 3: Multiple Timeframes

```bash
# Export different timeframes
python scripts/export_history.py --symbol=MNQ --timeframe=1m --days=90
python scripts/export_history.py --symbol=MNQ --timeframe=5m --days=90
python scripts/export_history.py --symbol=MNQ --timeframe=15m --days=90

# Backtest each
for csv in historical_data/MNQ_*.csv; do
  python core/backtest_executor.py --strategy=ma_crossover --symbol=MNQ --csv=$csv
done
```

### Example 4: Full Year Analysis

```bash
# Export full year
python scripts/export_history.py \
  --symbol=MNQ \
  --timeframe=5m \
  --start=2024-01-01 \
  --end=2024-12-31

# Backtest with optimization
python core/backtest_executor.py \
  --strategy=ma_crossover \
  --symbol=MNQ \
  --csv=historical_data/MNQ_5m_*.csv \
  --optimize \
  --param=fast_period=5:30:5 \
  --param=slow_period=30:200:20
```

---

## 🔍 Comparison: Data Sources

| Source | Command | Pros | Cons |
|--------|---------|------|------|
| **Sample Data** | `--sample` | Fast, free | Not real market |
| **API Direct** | `--days=30` | Easy | Limited history |
| **CSV Export** | `--csv=file.csv` | **Real data, any range** | Requires export step |

**CSV Export = Best of both worlds!** ✅
- Real market data
- Any date range
- Reusable
- Fast backtesting

---

## ⚡ One-Liner Workflows

### Export + Backtest (90 days)

```bash
python scripts/export_history.py --symbol=MNQ --days=90 && \
python core/backtest_executor.py --strategy=ma_crossover --symbol=MNQ --csv=$(ls -t historical_data/MNQ_1m_*.csv | head -1)
```

### Export + Backtest + Monte Carlo

```bash
python scripts/export_history.py --symbol=MNQ --days=90 && \
python core/backtest_executor.py --strategy=ma_crossover --symbol=MNQ --csv=$(ls -t historical_data/MNQ_1m_*.csv | head -1) --monte-carlo=1000
```

### Batch Export Multiple Symbols

```bash
for sym in MNQ MES MYM MGC; do
  python scripts/export_history.py --symbol=$sym --timeframe=1m --days=60
done
```

---

## 📋 CSV Format Specification

**Your CSV must have these columns:**

```csv
timestamp,open,high,low,close,volume
```

**Requirements:**
- `timestamp`: Date/time (any standard format)
- `open`: Opening price
- `high`: Highest price
- `low`: Lowest price
- `close`: Closing price
- `volume`: Trading volume (optional)

**Example:**
```csv
timestamp,open,high,low,close,volume
2024-12-01 09:30:00,25300.00,25310.00,25295.00,25305.00,1000
2024-12-01 09:31:00,25305.00,25315.00,25300.00,25310.00,1200
2024-12-01 09:32:00,25310.00,25320.00,25305.00,25315.00,1100
```

---

## 🚀 What You Can Do Now

### Before (Sample Data Only)
```bash
python core/backtest_executor.py --strategy=ma_crossover --symbol=MNQ --days=30 --sample
```
❌ Not real market data

### After (Real Market Data)
```bash
python scripts/export_history.py --symbol=MNQ --days=90
python core/backtest_executor.py --strategy=ma_crossover --symbol=MNQ --csv=historical_data/MNQ_*.csv
```
✅ Real market data!  
✅ Any date range!  
✅ Reusable!  
✅ Accurate backtests!

---

## 🎯 Next Steps

1. **Export data for your favorite symbol:**
   ```bash
   python scripts/export_history.py --symbol=MNQ --timeframe=1m --days=90
   ```

2. **Backtest with real data:**
   ```bash
   python core/backtest_executor.py --strategy=ma_crossover --symbol=MNQ --csv=historical_data/MNQ_*.csv
   ```

3. **Run Monte Carlo:**
   ```bash
   python core/backtest_executor.py --strategy=ma_crossover --symbol=MNQ --csv=historical_data/MNQ_*.csv --monte-carlo=1000
   ```

4. **Optimize parameters:**
   ```bash
   python core/backtest_executor.py --strategy=ma_crossover --symbol=MNQ --csv=historical_data/MNQ_*.csv --optimize
   ```

---

## 📚 Full Documentation

**See `docs/BACKTEST_DATA_GUIDE.md` for:**
- Detailed explanations
- Advanced data processing
- Troubleshooting
- Quality checks
- Custom formats

---

## ✅ Summary

**You now have:**
- ✅ Export script for real data
- ✅ CSV support in backtest engine
- ✅ Multiple export methods
- ✅ Complete documentation

**Start now:**
```bash
python scripts/export_history.py --symbol=MNQ --timeframe=1m --days=90
```

**Your backtests now use REAL market data!** 🎉🔬📈
