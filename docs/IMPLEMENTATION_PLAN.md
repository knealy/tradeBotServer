# Implementation Plan - System Optimizations, Backtesting & New Strategy

**Date:** December 17, 2025  
**Objectives:**
1. Optimize system for maximum speed/efficiency
2. Build backtesting engine with Monte Carlo simulations
3. Create trend scalping strategy

---

## Phase 1: Performance Optimizations (Est: 4-6 hours)

### Priority 1: Batch Order Queries ⚡ HIGH IMPACT
**Current:** Sequential API calls for each position (N × 30-50ms)  
**Optimized:** Single API call + local filtering (50ms total)  
**Speedup:** 5-10x for multiple positions

**Implementation:**
- Modify `get_linked_orders()` to accept optional cached orders
- Add `get_all_orders()` method that fetches once
- Update position enrichment to fetch orders once, reuse for all positions

**Files:**
- `brokers/topstepx_adapter.py`
- `trading_bot.py`

### Priority 2: Parallel Position Enrichment ⚡ HIGH IMPACT
**Current:** Sequential: positions → orders → quotes  
**Optimized:** Parallel: `asyncio.gather(positions, all_orders, all_quotes)`  
**Speedup:** 2-3x

**Implementation:**
- Use `asyncio.gather()` for concurrent API calls
- Fetch all quotes for position symbols in parallel
- Enrich positions from cached data

**Files:**
- `trading_bot.py` - `get_positions()` method

### Priority 3: Connection Pooling 🔧 MEDIUM IMPACT
**Current:** New connection per request  
**Optimized:** Reuse connections with pooling  
**Speedup:** 10-20ms per request

**Implementation:**
- Configure `requests.Session` with connection pooling
- Set `pool_connections` and `pool_maxsize`
- Already partially implemented in `auth.py`

**Files:**
- `core/auth.py`
- `brokers/topstepx_adapter.py`

### Priority 4: Strategy Result Caching 🔧 LOW IMPACT
**Current:** Recalculate indicators every iteration  
**Optimized:** Cache results with TTL  
**Speedup:** 5-10ms per strategy loop

**Implementation:**
- Add `@lru_cache` to expensive calculations
- Cache ATR, EMA, signal results for 1-5s
- Invalidate on new data

**Files:**
- `strategies/*.py`

---

## Phase 2: Backtesting Engine (Est: 8-12 hours)

### Component 1: Historical Data Loader

**Features:**
- Load CSV/JSON historical data
- Support multiple timeframes (30s, 1m, 5m, 15m, 1h, 1d)
- OHLCV format with timestamps
- Symbol mapping
- Date range filtering

**Structure:**
```python
class HistoricalDataLoader:
    def load_data(symbol, timeframe, start_date, end_date) -> pd.DataFrame
    def resample(data, target_timeframe) -> pd.DataFrame
    def validate_data(data) -> bool
```

**Files:**
- `core/backtest/data_loader.py` (NEW)
- `core/backtest/__init__.py` (NEW)

### Component 2: Backtesting Engine Core

**Features:**
- Event-driven architecture
- Order simulation (market, limit, stop)
- Slippage modeling
- Commission calculation
- Position tracking
- P&L calculation
- Trade logging

**Structure:**
```python
class BacktestEngine:
    def run(strategy, data, initial_capital) -> BacktestResult
    def simulate_order(order, current_price, slippage)
    def calculate_metrics(trades) -> dict
```

**Files:**
- `core/backtest/engine.py` (NEW)
- `core/backtest/models.py` (NEW)

### Component 3: Performance Metrics

**Metrics to Calculate:**
- Total return
- Sharpe ratio
- Sortino ratio
- Max drawdown
- Win rate
- Average win/loss
- Profit factor
- Expectancy
- Number of trades
- Average hold time

**Structure:**
```python
class PerformanceMetrics:
    def calculate_sharpe_ratio(returns) -> float
    def calculate_max_drawdown(equity_curve) -> float
    def calculate_profit_factor(trades) -> float
    # ... etc
```

**Files:**
- `core/backtest/metrics.py` (NEW)

### Component 4: Monte Carlo Simulation

**Features:**
- Randomize trade order
- Bootstrap returns
- Generate confidence intervals
- Risk of ruin calculation
- Expected returns distribution

**Structure:**
```python
class MonteCarloSimulator:
    def run_simulations(trades, num_simulations=1000) -> dict
    def randomize_trades(trades) -> list
    def calculate_confidence_intervals(results) -> dict
```

**Files:**
- `core/backtest/monte_carlo.py` (NEW)

### Component 5: CLI Integration

**Commands:**
```bash
backtest <strategy> --symbol=MNQ --timeframe=5m --start=2024-01-01 --end=2024-12-31
backtest_monte_carlo <strategy> --symbol=MNQ --simulations=1000
backtest_optimize <strategy> --symbol=MNQ --param=stop_loss --range=10,100,10
```

**Files:**
- `trading_bot.py` - Add backtest commands
- `core/cli_command_parser.py` - Parse backtest commands

---

## Phase 3: Trend Scalping Strategy (Est: 4-6 hours)

### Strategy Specifications

**Name:** `TrendScalpingStrategy`  
**Timeframe:** Configurable (30s, 1m, 2m, 5m)  
**Indicators:**
- 89 EMA
- 233 EMA
- Market structure (HH/HL for uptrend, LH/LL for downtrend)

**Entry Rules:**

**LONG:**
1. Price above 233 EMA (long-term trend up)
2. 89 EMA crosses above 233 EMA (momentum confirmation)
3. Recent higher high AND higher low (market structure confirms)
4. Current price pullback to 89 EMA (entry opportunity)

**SHORT:**
1. Price below 233 EMA (long-term trend down)
2. 89 EMA crosses below 233 EMA (momentum confirmation)
3. Recent lower low AND lower high (market structure confirms)
4. Current price rally to 89 EMA (entry opportunity)

**Exit Rules:**
- Stop loss: Recent swing low/high (market structure)
- Take profit: 2:1 or 3:1 risk/reward
- Trailing stop: Move to breakeven after +1R
- Time-based: Max hold time (e.g., 30 min for scalping)

**Risk Management:**
- Max 1-2% risk per trade
- Max 3 concurrent positions
- Daily loss limit enforcement

### Implementation Structure

```python
class TrendScalpingStrategy(BaseStrategy):
    def __init__(self, timeframe='1m', ema_short=89, ema_long=233)
    
    def calculate_ema(self, data, period) -> float
    def detect_market_structure(self, data) -> dict
    def check_ema_cross(self, current, previous) -> str
    def find_entry_opportunity(self, price, ema, structure) -> bool
    def calculate_stop_loss(self, structure, side) -> float
    def calculate_take_profit(self, entry, stop, rr_ratio) -> float
    
    async def execute(self, symbol) -> dict
```

**Files:**
- `strategies/trend_scalping_strategy.py` (NEW)
- `strategies/__init__.py` - Register new strategy

### Configuration

**Environment Variables:**
```bash
TREND_SCALP_TIMEFRAME=1m
TREND_SCALP_EMA_SHORT=89
TREND_SCALP_EMA_LONG=233
TREND_SCALP_RR_RATIO=2.0
TREND_SCALP_MAX_HOLD_TIME=1800  # 30 minutes
TREND_SCALP_TRAILING_STOP=true
```

---

## Implementation Order & Timeline

### Week 1: Optimizations (Dec 17-20)
- [x] Day 1: Batch order queries + parallel enrichment
- [ ] Day 2: Connection pooling + strategy caching
- [ ] Day 3: Testing & validation

### Week 2: Backtesting (Dec 21-27)
- [ ] Day 4-5: Data loader + engine core
- [ ] Day 6: Performance metrics
- [ ] Day 7: Monte Carlo simulator
- [ ] Day 8: CLI integration & testing

### Week 3: New Strategy (Dec 28-31)
- [ ] Day 9: EMA indicator implementation
- [ ] Day 10: Market structure detection
- [ ] Day 11: Entry/exit logic
- [ ] Day 12: Testing & optimization

---

## Success Metrics

### Optimizations
- [ ] Position query <50ms (currently 100-300ms)
- [ ] Order enrichment <20ms per position (currently 30-50ms)
- [ ] Strategy loop <500ms (currently 1-2s)

### Backtesting
- [ ] Run 1-year backtest in <10s
- [ ] 1000 Monte Carlo simulations in <30s
- [ ] Generate full report with charts

### New Strategy
- [ ] Sharpe ratio >1.5
- [ ] Win rate >55%
- [ ] Max drawdown <10%
- [ ] Tested on 6+ months historical data

---

## Dependencies

### Python Packages (add to requirements.txt)
```
pandas>=2.0.0  # Data manipulation (likely already installed)
numpy>=1.24.0  # Numerical computing (likely already installed)
scipy>=1.10.0  # Statistical functions (NEW)
matplotlib>=3.7.0  # Plotting (NEW)
seaborn>=0.12.0  # Statistical visualization (NEW)
```

### Data Sources
- TopStepX historical API (if available)
- CSV exports from broker
- Third-party data providers (optional)

---

## Testing Strategy

### Unit Tests
- Each optimization: Before/after latency comparison
- Backtesting: Known results validation
- Strategy: Signal generation accuracy

### Integration Tests
- Full backtest run with sample data
- Monte Carlo with reproducible seed
- Strategy with live (paper) data

### Performance Tests
- Benchmark position queries (1, 5, 10 positions)
- Backtest performance (1 month, 6 months, 1 year)
- Strategy execution speed

---

## Risk Mitigation

### Optimization Risks
- Breaking existing functionality
- **Mitigation:** Comprehensive testing, gradual rollout

### Backtesting Risks
- Overfitting to historical data
- Look-ahead bias
- Survivorship bias
- **Mitigation:** Out-of-sample testing, walk-forward analysis

### Strategy Risks
- False signals in choppy markets
- Whipsaw on EMA crosses
- **Mitigation:** Market condition filters, volatility checks

---

## Next Steps

1. **Start Phase 1** - Optimize batch queries (highest impact)
2. **Validate improvements** - Run performance tests
3. **Move to Phase 2** - Build backtesting foundation
4. **Create Phase 3** - Implement trend strategy
5. **Backtest Phase 3** - Validate with Phase 2 tools

**Let's begin with the optimizations!** 🚀
