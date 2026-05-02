# Comprehensive Backtest Engine Guide

## Overview

The backtest engine provides professional-grade strategy testing capabilities using your actual strategy code. It supports multiple data sources, comprehensive metrics, and HTML reporting.

## Data Sources

### 1. **TopStepX API (Real Historical Data)** ✅
Uses the broker adapter to fetch real historical data:

```bash
python core/backtest_executor.py \
  --strategy=simple_candle \
  --symbol=MNQ \
  --start=2025-12-01 \
  --end=2025-12-07 \
  --replay \
  --slippage-ticks=0.5
```

**Features:**
- Automatically authenticates with TopStepX API
- Fetches real historical bars via `TopStepXAdapter.get_historical_data()`
- Supports all timeframes (30s, 1m, 5m, 15m, 1h, 1d)
- Handles market hours and weekend filtering automatically

### 2. **CSV Files (From History Command)** ✅
Export data using the `history` command, then backtest:

```bash
# Step 1: Export data from trading bot
python trading_bot.py --account_select=1
# Then in CLI:
history MNQ 1m 2025-12-01 2025-12-07 csv

# Step 2: Backtest with exported CSV
python core/backtest_executor.py \
  --strategy=simple_candle \
  --symbol=MNQ \
  --csv=historical_data/MNQ_1m_20250102_123456.csv \
  --replay
```

**CSV Format:**
- Auto-detects column names (case-insensitive)
- Supports: `timestamp,open,high,low,close,volume` or `Time,Open,High,Low,Close,Volume`
- Handles various timestamp formats automatically

### 3. **Sample Data (For Testing)** ✅
Generate synthetic data for quick testing:

```bash
python core/backtest_executor.py \
  --strategy=simple_candle \
  --symbol=MNQ \
  --days=30 \
  --sample \
  --replay
```

## Strategy Replay Mode

**Replay mode** uses your actual strategy classes (e.g., `SimpleCandleStrategy`) instead of function-based backtests. This ensures identical logic between backtest and live trading.

### Supported Strategies

- `simple_candle` - Simple Candle Strategy
- `overnight_range` - Overnight Range Strategy
- `mean_reversion` - Mean Reversion Strategy
- `trend_following` - Trend Following Strategy
- `trend_scalping` - Trend Scalping Strategy
- `simple_momentum` - Simple Momentum Strategy

### How It Works

1. **Instantiates actual strategy class** - Uses the same code as live trading
2. **Intercepts order placement** - `place_bracket_order()` calls are simulated
3. **Bar-by-bar replay** - Feeds historical bars to `strategy.analyze()` method
4. **Simulates execution** - Uses `BacktestEngine` for realistic fills with slippage
5. **Tracks positions** - Maintains position state just like live trading

## Performance Metrics

The engine calculates comprehensive metrics:

### Trade Statistics
- Total trades, winning trades, losing trades
- Win rate, profit factor
- Average win/loss, largest win/loss
- Expectancy (expected value per trade)

### Risk Metrics
- Maximum drawdown (absolute and percentage)
- Sharpe ratio (risk-adjusted returns)
- Sortino ratio (downside risk-adjusted)
- Average bars held per trade

### Execution Metrics
- Total commission paid
- Total slippage cost
- MAE/MFE (Maximum Adverse/Favorable Excursion)

## HTML Reports

Every backtest generates a comprehensive HTML report with:

- **Equity Curve Chart** - Visual representation of account value over time
- **Drawdown Chart** - Shows drawdown periods
- **Trade Distribution** - Win/loss distribution visualization
- **Performance Metrics Dashboard** - All key metrics in cards
- **Trade History Table** - Detailed list of all trades

Reports are saved to `backtest_reports/` directory.

## Advanced Features

### Parameter Optimization

Test multiple parameter combinations:

```bash
python core/backtest_executor.py \
  --strategy=simple_candle \
  --symbol=MNQ \
  --start=2025-12-01 \
  --end=2025-12-07 \
  --optimize \
  --param=atr_multiplier=1.0:3.0:0.5 \
  --param=min_distance=2:10:2
```

### Monte Carlo Simulation

Test robustness by randomizing trade order:

```bash
python core/backtest_executor.py \
  --strategy=simple_candle \
  --symbol=MNQ \
  --start=2025-12-01 \
  --end=2025-12-07 \
  --replay \
  --monte-carlo=1000
```

### Custom Slippage

Adjust slippage assumptions:

```bash
python core/backtest_executor.py \
  --strategy=simple_candle \
  --symbol=MNQ \
  --start=2025-12-01 \
  --end=2025-12-07 \
  --replay \
  --slippage-ticks=1.0  # Higher slippage for larger orders
```

## Integration with Existing Components

### Broker Adapter Integration
- Uses `TopStepXAdapter` for real historical data
- Leverages existing authentication and rate limiting
- Reuses contract ID resolution logic

### Strategy Classes
- Works with all existing strategy classes
- No code changes needed to strategies
- Intercepts order placement transparently

### Trading Bot Integration
- Can use real `TradingBot` instance if available
- Falls back to mock for standalone backtests
- Supports all strategy dependencies (ATR, EMA, etc.)

## Example Workflows

### Quick Strategy Test
```bash
# Test simple_candle on last 7 days
python core/backtest_executor.py \
  --strategy=simple_candle \
  --symbol=MNQ \
  --days=7 \
  --replay
```

### Comprehensive Analysis
```bash
# Full month with optimization
python core/backtest_executor.py \
  --strategy=simple_candle \
  --symbol=MNQ \
  --start=2025-11-01 \
  --end=2025-11-30 \
  --replay \
  --optimize \
  --monte-carlo=500
```

### CSV-Based Backtest
```bash
# Use pre-exported data
python core/backtest_executor.py \
  --strategy=simple_candle \
  --symbol=MNQ \
  --csv=historical_data/MNQ_1m_export.csv \
  --replay \
  --slippage-ticks=0.5
```

## Best Practices

1. **Use real data** - Export from `history` command or use API directly
2. **Test multiple timeframes** - Strategy performance varies by timeframe
3. **Adjust slippage** - Use realistic slippage based on order size
4. **Run Monte Carlo** - Test robustness to trade order
5. **Compare strategies** - Run same period with different strategies
6. **Review HTML reports** - Visual analysis reveals patterns

## Technical Details

### Order Simulation
- **Market orders**: Fill at next bar open + slippage
- **Stop orders**: Trigger when price crosses stop, fill at stop + slippage
- **Limit orders**: Fill when price reaches limit
- **Bracket orders**: Entry fills first, then stop loss and take profit attach

### Position Tracking
- Maintains position state throughout backtest
- Tracks unrealized P&L bar-by-bar
- Calculates MAE/MFE for each position
- Handles partial fills and position scaling

### Commission & Slippage
- Default commission: $2.50 per contract (round-trip)
- Default slippage: 0.5 ticks
- Both configurable via parameters

## Future Enhancements

Planned features (from `addingFeatures.md`):
- Live metrics dashboard integration
- Auto-optimization loop with approval workflow
- Trade quality tagging (market regime, time-based)
- Enhanced replay mode with strategy state persistence
- Multi-strategy portfolio backtesting

