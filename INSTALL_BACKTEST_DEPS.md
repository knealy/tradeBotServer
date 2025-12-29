# Install Backtesting Dependencies

## Quick Install (30 seconds)

```bash
pip install pandas numpy scipy matplotlib seaborn
```

## Or Install Everything

```bash
pip install -r requirements.txt
```

## Then Test

```bash
python core/backtest_executor.py \
  --strategy=ma_crossover \
  --symbol=MNQ \
  --csv=historical_data/MNQ_1m_20251218_000826.csv
```

---

## What Was Fixed Today

✅ **CSV auto-detection** - Handles any column case (Time/time, Open/open, etc.)  
✅ **Strategy parameter filtering** - No more "unexpected keyword argument" errors  
✅ **Export script** - Recreated at `scripts/export_history.py`  
✅ **Reduced logging** - Position checks now at DEBUG level

**Just install dependencies and you're ready!** 🚀
