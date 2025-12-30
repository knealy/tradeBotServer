# Chart Trading Interface & Backtesting Engine Implementation Plan

## Overview
This document outlines the implementation plan for three major features:
1. Real-time chart refresh rate control ✅ (COMPLETE)
2. Trading interface on charts (IN PROGRESS)
3. Full backtesting simulation engine (PLANNED)

---

## 1. Real-Time Refresh Rate Control ✅

**Status:** COMPLETE

**Implementation:**
- Added dropdown with options: 1x/sec, 3x/sec, 6x/sec, 12x/sec
- Dynamic interval calculation: `1000 / refreshRate`
- Updates interval in real-time when changed

---

## 2. Trading Interface on Charts

### 2.1 UI Components Needed

**Trading Panel:**
- Order Type Dropdown: Market, Limit, Stop
- Side Buttons: BUY (green), SELL (red)
- Quantity Input: Number input field
- Bracket Options:
  - Enable Bracket checkbox
  - Stop Loss Price input
  - Take Profit Price input
- Execute Button: "Place Order"

**Price Lines on Chart:**
- Entry Price Line (green for long, red for short)
- Stop Loss Line (red)
- Take Profit Line (green)
- Position Size indicator

### 2.2 Backend Endpoints

**Already Added:**
- `POST /api/chart/order` - Place orders
- `GET /api/chart/positions` - Get current positions

**Implementation Steps:**
1. Add trading panel HTML to chart template
2. Add JavaScript functions for order placement
3. Add price line rendering using TradingView Lightweight Charts
4. Add position tracking and line updates
5. Handle order responses and update UI

### 2.3 Price Lines Implementation

Use TradingView Lightweight Charts `LineSeries`:
- Create separate line series for entry, stop loss, take profit
- Update lines when positions change
- Color coding: Green (long/TP), Red (short/SL)

---

## 3. Backtesting Simulation Engine

### 3.1 Architecture

**New Module:** `core/backtesting_engine.py`

**Components:**
1. **Strategy Interface** - Define strategy signals
2. **Order Simulator** - Simulate order execution with slippage
3. **Position Tracker** - Track positions, P&L, drawdown
4. **Performance Metrics** - Calculate stats (Sharpe, win rate, etc.)
5. **Report Generator** - Generate backtest reports

### 3.2 Integration Points

**Existing Components:**
- ✅ Historical data fetching (`get_historical_data`)
- ✅ CSV export functionality
- ✅ Backtest chart walkthrough mode
- ✅ Market hours logic
- ✅ Contract management

**New Components Needed:**
- Strategy execution engine
- Order fill simulation (slippage, partial fills)
- Position management
- P&L calculation
- Performance metrics
- Report generation

### 3.3 Implementation Plan

**Phase 1: Core Engine**
- Create `BacktestingEngine` class
- Implement order simulation
- Implement position tracking
- Basic P&L calculation

**Phase 2: Strategy Integration**
- Strategy signal interface
- Entry/exit logic
- Risk management rules

**Phase 3: Performance Metrics**
- Win rate, profit factor
- Sharpe ratio, Sortino ratio
- Max drawdown, recovery factor
- Trade statistics

**Phase 4: Reporting**
- HTML report generation
- Trade log export
- Chart visualization of trades
- Performance charts

### 3.4 Example Usage

```python
from core.backtesting_engine import BacktestingEngine
from strategies.my_strategy import MyStrategy

# Initialize engine
engine = BacktestingEngine(
    symbol="MNQ",
    start_date="2025-01-01",
    end_date="2025-12-01",
    initial_capital=50000
)

# Load strategy
strategy = MyStrategy()

# Run backtest
results = engine.run_backtest(strategy)

# Generate report
engine.generate_report("backtest_report.html")
```

---

## Implementation Priority

1. ✅ **Refresh Rate Control** - COMPLETE
2. **Trading Interface** - IN PROGRESS
   - UI components
   - Order placement
   - Price lines
3. **Backtesting Engine** - NEXT
   - Core engine
   - Strategy integration
   - Performance metrics
   - Reporting

---

## Next Steps

1. Complete trading interface UI
2. Implement price line rendering
3. Create backtesting engine module
4. Integrate with existing chart system
5. Add comprehensive testing

Test the trading interface — verify order placement and price line rendering
Integrate backtesting engine — connect to chart backtest mode
Add strategy interface — allow strategies to generate signals
Generate reports — create HTML reports with performance charts
The foundation is in place. The trading interface is ready for testing, and the backtesting engine can be integrated with your existing chart system. Should I continue with integration and testing, or focus on a specific area?