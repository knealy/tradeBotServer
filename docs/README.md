# Trading Bot Documentation

**Last Updated**: December 29, 2025  
**System Status**: ✅ **PRODUCTION-READY**

---

## 📚 Documentation Index

This is your complete guide to the TopStepX Trading Bot system.

### 🚀 Getting Started (Read These First)

1. **[Quick Start Guide](01-QUICK-START.md)** - Get trading in 5 minutes
2. **[System Architecture](02-ARCHITECTURE.md)** - How the system works
3. **[Trading Guide](03-TRADING-GUIDE.md)** - Place orders, manage positions
4. **[GUI Dashboard](04-GUI-DASHBOARD.md)** - Master Control browser interface

### 📖 Advanced Topics

5. **[Strategy System](05-STRATEGIES.md)** - Create and run strategies
6. **[Backtesting Guide](06-BACKTESTING.md)** - Test strategies on historical data
7. **[Rust Integration](07-RUST-INTEGRATION.md)** - Performance optimization
8. **[Deployment Guide](08-DEPLOYMENT.md)** - Deploy to production

### 🔧 Reference

9. **[API Reference](09-API-REFERENCE.md)** - All endpoints and methods
10. **[Troubleshooting](10-TROUBLESHOOTING.md)** - Common issues and solutions
11. **[TODO List](TODO.md)** - Current tasks and future plans
12. **[Changelog](CHANGELOG.md)** - Recent changes and improvements

---

## 🎯 Quick Navigation By Task

### I want to...

**Start trading right now**  
→ [Quick Start Guide](01-QUICK-START.md) → [Trading Guide](03-TRADING-GUIDE.md)

**Use the browser GUI**  
→ [GUI Dashboard](04-GUI-DASHBOARD.md)

**Create a new strategy**  
→ [Strategy System](05-STRATEGIES.md) → [Backtesting](06-BACKTESTING.md)

**Understand the system architecture**  
→ [Architecture](02-ARCHITECTURE.md) → [Rust Integration](07-RUST-INTEGRATION.md)

**Deploy to production**  
→ [Deployment Guide](08-DEPLOYMENT.md)

**Fix an error**  
→ [Troubleshooting](10-TROUBLESHOOTING.md) → Check logs

**See API documentation**  
→ [API Reference](09-API-REFERENCE.md)

---

## 📊 System Capabilities

### ✅ What's Available Now

**Trading Operations:**
- ✅ Market orders (instant execution)
- ✅ Limit orders (price-specific)
- ✅ Stop orders (stop market, stop limit)
- ✅ Bracket orders (entry + SL + TP)
- ✅ Trailing stop orders
- ✅ Position management (close, modify)
- ✅ Order management (cancel, modify)

**Interfaces:**
- ✅ CLI (command-line interface)
- ✅ Browser GUI (Master Control dashboard)
- ✅ Python API (programmatic access)

**Data & Analysis:**
- ✅ Real-time market quotes
- ✅ Historical data (all timeframes)
- ✅ Technical indicators (EMA, ATR, RSI, etc.)
- ✅ Market depth (order book)
- ✅ Account state & compliance

**Strategy System:**
- ✅ Multiple strategies simultaneously
- ✅ Custom timeframes and symbols
- ✅ ATR-based stop loss and take profit
- ✅ Strategy monitoring and control
- ✅ Backtesting engine

**Performance:**
- ✅ 14+ Rust hot paths (5-20ms faster)
- ✅ WebSocket real-time updates
- ✅ Automatic fallbacks to Python
- ✅ Production-ready reliability

---

## 🏗️ System Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                     Trading Bot System                       │
├─────────────────────────────────────────────────────────────┤
│                                                               │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐  │
│  │   CLI        │    │  Browser GUI │    │  Python API  │  │
│  │   Interface  │    │  (Master     │    │  (Direct)    │  │
│  │              │    │   Control)   │    │              │  │
│  └──────┬───────┘    └──────┬───────┘    └──────┬───────┘  │
│         │                   │                   │           │
│         └───────────────────┼───────────────────┘           │
│                             │                               │
│                    ┌────────▼────────┐                      │
│                    │  Trading Bot     │                      │
│                    │  Core Engine     │                      │
│                    │  (trading_bot.py)│                      │
│                    └────────┬────────┘                      │
│                             │                               │
│         ┌───────────────────┼───────────────────┐           │
│         │                   │                   │           │
│  ┌──────▼───────┐  ┌────────▼────────┐  ┌──────▼──────┐   │
│  │   Strategy   │  │  TopStepX       │  │   Rust      │   │
│  │   Manager    │  │  Adapter        │  │   Executor  │   │
│  │              │  │  (Broker API)   │  │   (Fast)    │   │
│  └──────────────┘  └────────┬────────┘  └─────────────┘   │
│                              │                              │
│                     ┌────────▼────────┐                     │
│                     │   TopStepX API  │                     │
│                     │   (REST + WS)   │                     │
│                     └─────────────────┘                     │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

### Key Components

- **Trading Bot Core**: Main orchestrator (`trading_bot.py`)
- **TopStepX Adapter**: Broker API integration (`brokers/topstepx_adapter.py`)
- **Rust Executor**: Performance-critical operations (`rust/`)
- **Strategy Manager**: Strategy lifecycle and execution (`strategies/`)
- **GUI Server**: Browser interface (`gui/chart_html.py`, `gui/master_control.html`)

---

## 📁 Repository Structure

```
tradeBotServer/
├── trading_bot.py          # Main bot entry point
├── auth.py                 # Authentication management
├── core/                   # Core functionality
│   ├── cli_command_parser.py
│   ├── strategy_executor.py
│   ├── market_data.py
│   ├── risk_management.py
│   └── ...
├── brokers/                # Broker integrations
│   └── topstepx_adapter.py
├── strategies/             # Trading strategies
│   ├── simple_candle_strategy.py
│   ├── strategy_base.py
│   └── ...
├── gui/                    # Browser interface
│   ├── chart_html.py       # Backend server
│   └── master_control.html # Frontend UI
├── rust/                   # Rust performance modules
│   ├── src/
│   │   ├── order_execution/
│   │   └── query/
│   └── Cargo.toml
├── docs/                   # Documentation (YOU ARE HERE)
└── tests/                  # Test suite
```

---

## 🔧 Quick Commands Reference

### Start Trading (CLI)
```bash
python trading_bot.py
```

### Start Trading (Non-Interactive)
```bash
python trading_bot.py --account_select=1
```

### Open Master Control GUI
```bash
python trading_bot.py --account_select=1 --command='master'
# Or from interactive CLI:
> master
```

### Start a Strategy
```bash
# From CLI
> strategies start simple_candle --symbols=MNQ --timeframe=15s

# Or non-interactive
python trading_bot.py --account_select=1 --command='strategies start simple_candle --symbols=MNQ'
```

### Check Positions
```bash
> positions
```

### Flatten All (Emergency)
```bash
> flatten
```

---

## 📈 Performance Metrics

### Current Performance (as of Dec 29, 2025)

| Operation | Rust (ms) | Python (ms) | Speedup |
|-----------|-----------|-------------|---------|
| Place Order | ~90 | ~95 | 1.05x |
| Cancel Order | ~40 | ~45 | 1.12x |
| Close Position | ~35 | ~55 | 1.57x |
| Get Positions | ~85 | ~90 | 1.06x |
| Get Orders | ~80 | ~85 | 1.06x |
| Market Quote | ~75 | ~80 | 1.07x |

**Active Rust Hot Paths**: 14+  
**Network Overhead**: 20-40x lower (WebSocket vs HTTP polling)  
**Chart Refresh**: Up to 24x/s  
**Update Latency**: Instant (WebSocket push)

---

## 🆘 Need Help?

1. **Check [Troubleshooting Guide](10-TROUBLESHOOTING.md)** first
2. **Review logs**: `trading_bot.log`
3. **Check system status**: `> metrics` command
4. **Test connection**: `> quote MNQ` command

---

## 📝 Recent Updates (December 29, 2025)

- ✅ Fixed Rust `cancel_order` (PyO3 workaround)
- ✅ Re-enabled Rust `close_position` hot path
- ✅ Added WebSocket real-time updates
- ✅ Added collapsible panel system
- ✅ Added higher chart refresh rates (24x/s)
- ✅ Added more timeframes (12 total)
- ✅ Added stop order types (5 total)
- ✅ Fixed GUI symbol extraction (root symbols)
- ✅ Fixed contract cache initialization

See [CHANGELOG.md](CHANGELOG.md) for full history.

---

## 🎯 Next Steps

**New Users**: Start with [Quick Start Guide](01-QUICK-START.md)  
**Traders**: Read [Trading Guide](03-TRADING-GUIDE.md) and [GUI Dashboard](04-GUI-DASHBOARD.md)  
**Developers**: Check [Architecture](02-ARCHITECTURE.md) and [API Reference](09-API-REFERENCE.md)  
**Strategy Creators**: See [Strategy System](05-STRATEGIES.md) and [Backtesting](06-BACKTESTING.md)

---

**Ready to trade? Let's go!** 🚀

