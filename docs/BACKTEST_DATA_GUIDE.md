# Backtest Data Guide

**Complete guide to providing data for backtesting**

---

## 🎯 Three Ways to Get Data

### 1. Export from History Command (Recommended) ✅
### 2. Use Export Script (Easy) ✅
### 3. Provide Your Own CSV (Flexible) ✅

---

## Method 1: Export from History Command (Recommended)

**Use the trading_bot `history` command to export real data:**

### Step 1: Export Data

```bash
# Start trading bot
python trading_bot.py --account_select=1

# From CLI, export to CSV:
history MNQ 1m 2024-01-01 2024-12-31 csv
```

**Output:**
```
Saved 125000 bars to historical_data/MNQ_1m_20241217_123456.csv
```

### Step 2: Backtest with Exported Data

```bash
python core/backtest_executor.py \
  --strategy=ma_crossover \
  --symbol=MNQ \
  --csv=historical_data/MNQ_1m_20241217_123456.csv
```

---

## Method 2: Use Export Script (Easy)

**Dedicated script for exporting data:**

### Quick Export (Last 90 Days)

```bash
python scripts/export_history.py --symbol=MNQ --timeframe=1m --days=90
```

**Output:**
```
================================================================================
HISTORICAL DATA EXPORT FOR BACKTESTING
================================================================================
Symbol: MNQ
Timeframe: 1m
Date Range: 2024-09-18 to 2024-12-17 (90 days)

🔐 Authenticating...
✅ Authenticated

📊 Fetching MNQ 1m bars...
✅ Fetched 129600 bars

💾 Exporting to CSV: historical_data/MNQ_1m_20241217_123456.csv
✅ Exported 129600 bars

================================================================================
BACKTEST THIS DATA:
================================================================================

python core/backtest_executor.py \
  --strategy=ma_crossover \
  --symbol=MNQ \
  --csv=historical_data/MNQ_1m_20241217_123456.csv
```

### Date Range Export

```bash
python scripts/export_history.py \
  --symbol=MES \
  --timeframe=5m \
  --start=2024-01-01 \
  --end=2024-12-31
```

### Custom Output

```bash
python scripts/export_history.py \
  --symbol=MNQ \
  --timeframe=1m \
  --days=180 \
  --output=my_data/mnq_6months.csv
```

---

## Method 3: Provide Your Own CSV

**Use any CSV file with OHLCV data:**

### Required CSV Format

```csv
timestamp,open,high,low,close,volume
2024-01-01 09:30:00,25300.00,25310.00,25295.00,25305.00,1000
2024-01-01 09:31:00,25305.00,25315.00,25300.00,25310.00,1200
2024-01-01 09:32:00,25310.00,25320.00,25305.00,25315.00,1100
...
```

**Requirements:**
- `timestamp` column (datetime format)
- `open`, `high`, `low`, `close` columns (prices)
- `volume` column (optional but recommended)
- Chronological order (oldest first)
- No missing bars (gaps will affect results)

### Backtest with Your CSV

```bash
python core/backtest_executor.py \
  --strategy=ma_crossover \
  --symbol=MNQ \
  --csv=my_data/custom_data.csv
```

---

## Complete Workflow Examples

### Example 1: Quick Test (30 days)

```bash
# Step 1: Export data
python scripts/export_history.py --symbol=MNQ --timeframe=1m --days=30

# Step 2: Backtest
python core/backtest_executor.py \
  --strategy=ma_crossover \
  --symbol=MNQ \
  --csv=historical_data/MNQ_1m_*.csv  # Use latest file
```

### Example 2: Full Year Analysis

```bash
# Step 1: Export full year
python scripts/export_history.py \
  --symbol=MNQ \
  --timeframe=5m \
  --start=2024-01-01 \
  --end=2024-12-31 \
  --output=data/mnq_2024.csv

# Step 2: Backtest with Monte Carlo
python core/backtest_executor.py \
  --strategy=ema_trend \
  --symbol=MNQ \
  --csv=data/mnq_2024.csv \
  --monte-carlo=1000
```

### Example 3: Multiple Timeframes

```bash
# Export different timeframes
python scripts/export_history.py --symbol=MNQ --timeframe=1m --days=90
python scripts/export_history.py --symbol=MNQ --timeframe=5m --days=90
python scripts/export_history.py --symbol=MNQ --timeframe=15m --days=90

# Backtest each
python core/backtest_executor.py --strategy=ma_crossover --symbol=MNQ --csv=historical_data/MNQ_1m_*.csv
python core/backtest_executor.py --strategy=ma_crossover --symbol=MNQ --csv=historical_data/MNQ_5m_*.csv
python core/backtest_executor.py --strategy=ma_crossover --symbol=MNQ --csv=historical_data/MNQ_15m_*.csv
```

### Example 4: Multiple Symbols

```bash
# Export multiple symbols
for symbol in MNQ MES MYM MGC; do
  python scripts/export_history.py --symbol=$symbol --timeframe=1m --days=60
done

# Backtest each
for csv in historical_data/*.csv; do
  symbol=$(basename $csv | cut -d'_' -f1)
  python core/backtest_executor.py --strategy=ma_crossover --symbol=$symbol --csv=$csv
done
```

---

## Data Quality Tips

### 1. Check Bar Count

```python
import pandas as pd
df = pd.read_csv('historical_data/MNQ_1m_*.csv')
print(f"Bars: {len(df)}")
print(f"Date Range: {df['timestamp'].min()} to {df['timestamp'].max()}")
```

### 2. Verify No Gaps

```python
df['timestamp'] = pd.to_datetime(df['timestamp'])
df = df.sort_values('timestamp')
time_diffs = df['timestamp'].diff()
gaps = time_diffs[time_diffs > pd.Timedelta(minutes=2)]  # For 1m data
print(f"Gaps found: {len(gaps)}")
```

### 3. Check OHLC Logic

```python
# High should be >= open, close, low
assert (df['high'] >= df['open']).all()
assert (df['high'] >= df['close']).all()
assert (df['high'] >= df['low']).all()

# Low should be <= open, close, high
assert (df['low'] <= df['open']).all()
assert (df['low'] <= df['close']).all()
assert (df['low'] <= df['high']).all()
```

---

## Troubleshooting

### Issue: "CSV missing required columns"

**Problem:** CSV doesn't have expected column names

**Solution:** Ensure columns are named exactly: `timestamp`, `open`, `high`, `low`, `close`, `volume`

```python
# Fix column names
df = pd.read_csv('data.csv')
df.columns = ['timestamp', 'open', 'high', 'low', 'close', 'volume']
df.to_csv('data_fixed.csv', index=False)
```

### Issue: "Cannot parse timestamp"

**Problem:** Timestamp format not recognized

**Solution:** Specify date format explicitly

```python
df = pd.read_csv('data.csv')
df['timestamp'] = pd.to_datetime(df['timestamp'], format='%Y-%m-%d %H:%M:%S')
df.to_csv('data_fixed.csv', index=False)
```

### Issue: "No data returned from API"

**Problem:** Date range too old or symbol incorrect

**Solution:** 
- Check symbol spelling (MNQ not MNQZ24)
- Use recent dates (data usually available for last 6-12 months)
- Try smaller date range first

### Issue: File not found

**Problem:** CSV path incorrect

**Solution:** Use absolute path or verify relative path

```bash
# Use absolute path
python core/backtest_executor.py \
  --strategy=ma_crossover \
  --symbol=MNQ \
  --csv=/Users/knealy/tradeBotServer/historical_data/MNQ_1m_20241217_123456.csv

# Or verify relative path
ls historical_data/MNQ_1m_*.csv
```

---

## Advanced: Custom Data Processing

### Resample to Different Timeframe

```python
import pandas as pd

# Read 1m data
df = pd.read_csv('historical_data/MNQ_1m_20241217.csv')
df['timestamp'] = pd.to_datetime(df['timestamp'])
df.set_index('timestamp', inplace=True)

# Resample to 5m
df_5m = df.resample('5min').agg({
    'open': 'first',
    'high': 'max',
    'low': 'min',
    'close': 'last',
    'volume': 'sum'
})

# Save
df_5m.to_csv('historical_data/MNQ_5m_resampled.csv')
```

### Filter by Market Hours

```python
import pandas as pd

df = pd.read_csv('historical_data/MNQ_1m_20241217.csv')
df['timestamp'] = pd.to_datetime(df['timestamp'])

# Filter to 9:30 AM - 4:00 PM ET only
df['hour'] = df['timestamp'].dt.hour
df['minute'] = df['timestamp'].dt.minute
df_market_hours = df[
    ((df['hour'] == 9) & (df['minute'] >= 30)) |
    ((df['hour'] >= 10) & (df['hour'] < 16))
]

df_market_hours.drop(['hour', 'minute'], axis=1, inplace=True)
df_market_hours.to_csv('historical_data/MNQ_1m_market_hours.csv', index=False)
```

### Merge Multiple Files

```python
import pandas as pd
import glob

# Read all CSV files
all_files = glob.glob('historical_data/MNQ_1m_*.csv')
dfs = []

for file in all_files:
    df = pd.read_csv(file)
    dfs.append(df)

# Concatenate and deduplicate
merged = pd.concat(dfs, ignore_index=True)
merged['timestamp'] = pd.to_datetime(merged['timestamp'])
merged = merged.sort_values('timestamp')
merged = merged.drop_duplicates(subset='timestamp')

# Save
merged.to_csv('historical_data/MNQ_1m_merged.csv', index=False)
```

---

## Comparison: Methods

| Method | Ease of Use | Flexibility | Best For |
|--------|-------------|-------------|----------|
| **History Command** | ⭐⭐⭐ Easy | ⭐⭐ Medium | Quick exports during trading |
| **Export Script** | ⭐⭐⭐⭐ Very Easy | ⭐⭐⭐ Good | Bulk data exports |
| **Custom CSV** | ⭐ Advanced | ⭐⭐⭐⭐⭐ Maximum | Custom data sources |

---

## Quick Reference

### Export 90 Days of Data

```bash
python scripts/export_history.py --symbol=MNQ --timeframe=1m --days=90
```

### Backtest with CSV

```bash
python core/backtest_executor.py \
  --strategy=ma_crossover \
  --symbol=MNQ \
  --csv=historical_data/MNQ_1m_20241217_123456.csv
```

### One-Liner (Export + Backtest)

```bash
python scripts/export_history.py --symbol=MNQ --timeframe=1m --days=90 && \
python core/backtest_executor.py --strategy=ma_crossover --symbol=MNQ --csv=$(ls -t historical_data/MNQ_1m_*.csv | head -1)
```

---

## Best Practices

### 1. Always Export Recent Data

```bash
# Re-export before important backtests
python scripts/export_history.py --symbol=MNQ --timeframe=1m --days=30
```

### 2. Keep Data Organized

```bash
# Create folders by symbol
mkdir -p historical_data/MNQ historical_data/MES

# Export to symbol folders
python scripts/export_history.py --symbol=MNQ --days=90 --output=historical_data/MNQ/90days.csv
```

### 3. Document Your Exports

```bash
# Create a metadata file
echo "MNQ 1m 90 days, exported $(date)" > historical_data/MNQ_1m_90d.txt
```

### 4. Test on Multiple Periods

```bash
# Export and test different periods
python scripts/export_history.py --symbol=MNQ --days=30 --output=historical_data/mnq_30d.csv
python scripts/export_history.py --symbol=MNQ --days=90 --output=historical_data/mnq_90d.csv
python scripts/export_history.py --symbol=MNQ --days=180 --output=historical_data/mnq_180d.csv

# Backtest each
for csv in historical_data/mnq_*.csv; do
  echo "Testing $csv"
  python core/backtest_executor.py --strategy=ma_crossover --symbol=MNQ --csv=$csv
done
```

---

## Summary

**You now have 3 ways to provide data for backtesting:**

1. ✅ **History command** - Quick exports from trading_bot CLI
2. ✅ **Export script** - Dedicated tool for bulk exports
3. ✅ **Custom CSV** - Maximum flexibility for any data source

**All methods produce the same standard CSV format that works with the backtest engine!**

**Start exporting data now:**
```bash
python scripts/export_history.py --symbol=MNQ --timeframe=1m --days=90
```

**Then backtest:**
```bash
python core/backtest_executor.py --strategy=ma_crossover --symbol=MNQ --csv=historical_data/MNQ_*.csv
```

🚀 **Happy backtesting with real data!**
