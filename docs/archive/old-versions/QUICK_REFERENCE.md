# Quick Reference - Trading Bot

**Everything you need at a glance**

---

## Start Trading

### Interactive CLI
```bash
python trading_bot.py
```

### Automated (Production)
```bash
caffeinate -dimsu python core/strategy_executor.py \
  --account_select=1 \
  --strategy=overnight_range \
  --symbols=mnq,mes
```

---

## Essential Commands

### Positions & Orders
```bash
positions         # View open positions (FAST: <100ms)
orders            # View open orders
account_state     # View balance/P&L
flatten           # Close all positions
```

### Order Placement (with reduce-only!)
```bash
# Stop loss (use -r for protection orders!)
stop mnq sell 1 25020 -r

# Take profit (use -r for protection orders!)
limit mnq sell 1 25060 -r

# Market order
trade mnq buy 1

# Bracket order
stop_bracket mnq buy 1 25400 25390 25410
```

### Strategy Management
```bash
strategies status    # View all strategies
strategies list      # List available
strategies start <name>
strategies stop <name>
```

---

## Available Strategies

| Strategy | Best For | Timeframe | Trades/Day |
|----------|----------|-----------|------------|
| overnight_range | Range breakouts | Daily | 1-2 |
| mean_reversion | Oversold/bought | 5m-15m | 3-8 |
| trend_following | Trends | 5m-1h | 2-5 |
| simple_momentum | Quick moves | 1m-5m | 10-30 |
| simple_candle | Patterns | 1m | 5-15 |
| **trend_scalping** | **Trends+Structure** | **30s-5m** | **5-15** |

---

## New: Trend Scalping Strategy

**Setup:**
```bash
# In .env
TREND_SCALP_TIMEFRAME=1m
TREND_SCALP_EMA_SHORT=89
TREND_SCALP_EMA_LONG=233
TREND_SCALP_RR_RATIO=2.0
STRATEGY_SYMBOLS=MNQ
```

**Start:**
```bash
python core/strategy_executor.py --strategy=trend_scalping --account_select=1 --symbols=mnq
```

**Logic:**
- LONG: Price > 233 EMA, 89 crosses above 233, HH+HL structure, pullback to 89 EMA
- SHORT: Price < 233 EMA, 89 crosses below 233, LH+LL structure, rally to 89 EMA

---

## Backtesting (NEW!)

### Quick Backtest
```python
from core.backtest import *

# Load data
loader = HistoricalDataLoader()
data = loader.get_sample_data('MNQ', days=30)

# Run backtest
engine = BacktestEngine()
result = await engine.run(strategy_func, data, 'MNQ')

# View results
print(PerformanceMetrics.generate_report(result))
```

### Monte Carlo
```python
mc = MonteCarloSimulator()
mc_results = mc.run_simulations(result.trades, 1000)
print(mc.generate_report(mc_results))
```

---

## Performance Stats

### System Speed (After Optimizations)
- Position query (1 pos): 60ms
- Position query (5 pos): 75ms (**6x faster!**)
- Position query (10 pos): 100ms (**10x faster!**)
- Order placement: 10-50ms
- Strategy loop: 400-600ms

### Backtesting Speed
- 1 month (1m bars): ~2-5 seconds
- 1 year (1m bars): ~20-60 seconds
- Monte Carlo (1000 sims): ~10-30 seconds

---

## Key Features

### ✅ Production Features
- Rust-optimized order execution (10-50ms)
- Real-time SignalR quotes
- Auto SL/TP after plain stops (5-10s)
- Reduce-only orders (no orphans!)
- Token management (0.05ms checks)
- Parallel position enrichment
- Batch order queries

### ✅ Backtesting Features
- Event-driven simulation
- Realistic fills (slippage + commission)
- 15+ performance metrics
- Monte Carlo simulations
- Parameter optimization
- Walk-forward testing

### ✅ Strategy Features
- 6 strategies available
- Configurable parameters
- Auto protection (SL/TP)
- Risk management
- Position tracking

---

## Troubleshooting

### Orders getting 500 errors?
→ API overload (market open), fallback to plain stops activates

### Orphaned orders?
→ Use `--reduce-only` or `-r` flag for SL/TP orders

### Slow position display?
→ Should be fast now (<150ms), check logs for "Parallel fetch"

### Strategy not trading?
→ Check `strategies status`, verify market hours, check compliance

---

## Installation

```bash
# Install dependencies
pip install -r requirements.txt

# Verify installation
python -c "import pandas, numpy, scipy; print('✅ Dependencies OK')"

# Run tests
pytest tests/test_strategy_executor.py -v
```

---

## Documentation

**Get Started:**
- `QUICK_START.md` - Basic usage
- `README.md` - Project overview

**Guides:**
- `docs/BACKTESTING_GUIDE.md` - Backtesting
- `docs/TREND_SCALPING_STRATEGY.md` - New strategy
- `CLI_REDUCE_ONLY_GUIDE.md` - Reduce-only orders

**Technical:**
- `docs/ARCHITECTURE_PERFORMANCE_ANALYSIS.md` - Architecture
- `docs/STRATEGY_EXECUTOR_VALIDATION.md` - Validation
- `docs/system_lifecycle.md` - System lifecycle

**Latest:**
- `COMPREHENSIVE_UPDATE_SUMMARY.md` - All updates (Dec 17)

---

**System ready for advanced trading!** 🚀
