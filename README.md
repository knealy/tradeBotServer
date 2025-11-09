# TopStepX Trading Bot 🤖

**Version 2.0.0** - Autonomous Trading System with High-Performance Caching

A production-ready, autonomous futures trading bot for TopStepX prop firm accounts with advanced performance optimization.

## 🌟 **What's New in v2.0**

- ✅ **Fully Autonomous** - No webhooks needed, strategies execute automatically
- ✅ **PostgreSQL Caching** - 95% faster data access, persistent across restarts
- ✅ **Performance Metrics** - Comprehensive tracking of API calls, cache hits, system resources
- ✅ **Priority Task Queue** - Intelligent background task scheduling
- ✅ **Modular Strategies** - Easy to add, enable/disable strategies
- ✅ **Production Ready** - Deployed on Railway with hosted database

## ⚡ Performance Highlights

| Metric | Before | After | Improvement |
|--------|--------|-------|-------------|
| Data fetch time | 109ms | 5ms | **95% faster** ⚡ |
| API calls per day | ~2000 | ~300 | **85% reduction** |
| Cache hit rate | 0% | 85-95% | **Persistent caching** |
| Task success rate | 85% | 98%+ | **More reliable** |

## ✨ Core Features

- **Autonomous Trading** - Strategies execute automatically based on market conditions
- **Real TopStepX API Integration** - Live account authentication and trading
- **PostgreSQL Persistent Caching** - 95% faster data access, survives restarts
- **Priority Task Queue** - Critical tasks (fills, risk checks) execute first
- **Modular Strategy System** - 3 strategies included, easy to add more
- **TopStepX Compliance** - DLL, MLL, and consistency rule tracking
- **Discord Notifications** - Real-time alerts on trade fills and risk events
- **Interactive CLI** - Full control via command-line interface
- **Performance Monitoring** - Comprehensive metrics and analytics

## 🚀 Quick Start

### 1. **Installation**
```bash
# Clone the repository
git clone <repository-url>
cd projectXbot

# Install dependencies
pip install -r requirements.txt

# Install PostgreSQL dependencies
pip install psycopg2-binary>=2.9.0
```

### 2. **Configuration**
```bash
# Create .env file with your credentials
cp .env.example .env

# Edit .env and add:
PROJECT_X_API_KEY=your_api_key
PROJECT_X_USERNAME=your_username
TOPSETPX_ACCOUNT_ID=your_account_id

# Optional: PostgreSQL database (Railway auto-configures)
DATABASE_URL=postgresql://user:pass@host:5432/db

# Discord notifications (optional)
DISCORD_WEBHOOK_URL=your_discord_webhook_url
```

### 3. **Run Locally**
```bash
# Start the trading bot
python trading_bot.py

# The bot will:
# ✅ Authenticate with TopStepX
# ✅ Connect to PostgreSQL (if available)
# ✅ Start autonomous strategies
# ✅ Begin monitoring positions
# ✅ Send Discord notifications on fills
```

### 4. **Deploy to Railway** (Recommended)
```bash
# Install Railway CLI
npm install -g @railway/cli

# Login
railway login

# Deploy
railway up

# The bot will automatically:
# ✅ Use Railway's PostgreSQL
# ✅ Load environment variables
# ✅ Start background tasks
# ✅ Monitor 24/7
```

## 📋 Interactive Commands

Start the bot with `python trading_bot.py` and use these commands:

### **Trading**
- `trade <symbol> <side> <quantity>` - Place market order
- `limit <symbol> <side> <quantity> <price>` - Place limit order
- `bracket <symbol> <side> <qty> <stop> <profit>` - Place bracket order with stop/take profit
- `stop_bracket <symbol> <side> <qty> <entry> <stop> <profit>` - Stop entry with bracket
- `flatten [symbol]` - Close all positions (or specific symbol)

### **Monitoring**
- `positions` - Show open positions with P&L
- `orders` - Show pending orders
- `trades` - Show trade history with FIFO consolidation
- `balance` - Check account balance and DLL remaining

### **Data & Analysis**
- `history <symbol> <timeframe> <limit>` - Get historical bars
  - Examples: `history MNQ 5m 100`, `history ES 15s 500`, `history NQ 4h 50`
- `contracts` - List all available trading contracts
- `metrics` - Show performance metrics (API calls, cache hits, system resources)

### **Strategy Control**
- `strategy <name> status` - Check strategy status
- `strategy <name> start` - Start a strategy
- `strategy <name> stop` - Stop a strategy
- `strategy list` - List all available strategies

### **System**
- `accounts` - List your trading accounts
- `help` - Show all available commands
- `quit` - Exit trading interface

## 🎯 Trading Strategies

### **1. Overnight Range Breakout** (Active ✅)
Tracks overnight price action (6pm - 9:30am CT) and places breakout orders at market open.

**How it works**:
- Monitors overnight high/low during low-volume hours
- At 9:30am, places stop orders above/below range
- Dynamic ATR-based stops and profit targets
- Uses daily ATR zones for smart profit targeting
- Automatically moves stops to breakeven after +15pts profit
- Flattens all positions at 4:00pm CT (EOD)

**Configuration** (.env):
```bash
OVERNIGHT_ENABLED=true
OVERNIGHT_SYMBOL=MNQ
OVERNIGHT_POSITION_SIZE=3
OVERNIGHT_ATR_PERIOD=14
OVERNIGHT_ATR_MULTIPLIER_SL=2.0
OVERNIGHT_ATR_MULTIPLIER_TP=2.5
OVERNIGHT_USE_BREAKEVEN=true
OVERNIGHT_BREAKEVEN_PROFIT_PTS=15
```

**Performance**: Designed for trending breakouts, works best on volatile days.

### **2. Mean Reversion** (Disabled by default)
Trades against extreme price moves using RSI and moving average deviation.

**Best for**: Ranging/choppy markets  
**Enable in .env**: `MEAN_REVERSION_ENABLED=true`

### **3. Trend Following** (Disabled by default)
Uses dual moving average crossovers to ride strong trends.

**Best for**: Trending markets  
**Enable in .env**: `TREND_FOLLOWING_ENABLED=true`

See **[MODULAR_STRATEGY_GUIDE.md](docs/MODULAR_STRATEGY_GUIDE.md)** for complete strategy documentation.

---

## 🎯 Trading Modes (Legacy)

### **Conservative Mode** (`--close-entire-at-tp1 true`)
- Single contract or close entire position at TP1
- Uses TP1 for bracket order take profit
- No additional limit orders

### **Aggressive Mode** (`--close-entire-at-tp1 false`)
- Multi-contract positions with partial closes
- Uses TP2 for bracket order take profit
- Places TP1 limit order for 75% partial close
- Maximizes profit potential

## 📊 Supported Symbols

### **Index Futures**
- **ES** - E-mini S&P 500
- **NQ** - E-mini NASDAQ-100
- **YM** - E-mini Dow Jones
- **RTY** - E-mini Russell 2000

### **Micro Futures**
- **MES** - Micro E-mini S&P 500
- **MNQ** - Micro E-mini NASDAQ-100
- **MYM** - Micro E-mini Dow Jones
- **M2K** - Micro E-mini Russell 2000

### **Other Futures**
- **Energy**: CL, NG, RB, HO
- **Metals**: GC, SI, PL, PA, HG
- **Agriculture**: ZC, ZS, ZW, KC, SB
- **Currencies**: 6E, 6J, 6B, 6A, 6C
- **Bonds**: ZB, ZN, ZF, ZT
- **Volatility**: VX
- **Crypto**: BTC, ETH

## 🧪 Testing

### **Test Webhook Integration**
```bash
# Test webhook server with realistic trading sequence
python3 test_webhook.py
```

### **Test Native Methods**
```bash
# Test native API methods
python3 test_native_methods.py
```

## 📁 Project Structure

```
projectXbot/
├── trading_bot.py              # Main trading bot
├── servers/                    # Server modules
│   ├── webhook_server.py       # TradingView webhook server
│   ├── start_webhook.py        # Webhook server startup script
│   ├── async_webhook_server.py # Async webhook server
│   ├── dashboard.py           # Dashboard API
│   └── websocket_server.py     # WebSocket server
├── core/                       # Core modules
│   ├── account_tracker.py     # Account tracking
│   ├── discord_notifier.py    # Discord notifications
│   └── sdk_adapter.py         # SDK adapter
├── tests/                      # Test suite
│   ├── test_webhook.py        # Webhook testing
│   └── test_native_methods.py # Native API testing
├── load_env.py                # Environment variable loader
├── setup_env.sh              # Environment setup script
├── requirements.txt           # Python dependencies
└── README.md                 # This file
```

## 📚 Documentation

**📖 Start Here**: **[docs/START_HERE.md](docs/START_HERE.md)** - Quick navigation guide for all documentation

### **Getting Started**
- **[docs/TESTING_GUIDE.md](docs/TESTING_GUIDE.md)** - Complete testing guide (local with Docker, Railway)
- **[docs/POSTGRESQL_SETUP.md](docs/POSTGRESQL_SETUP.md)** - Database setup and configuration
- **[docs/ENV_CONFIGURATION.md](docs/ENV_CONFIGURATION.md)** - All environment variables explained

### **Architecture & Design**
- **[docs/CURRENT_ARCHITECTURE.md](docs/CURRENT_ARCHITECTURE.md)** - Complete system architecture overview
- **[docs/COMPREHENSIVE_ROADMAP.md](docs/COMPREHENSIVE_ROADMAP.md)** - Project roadmap, current status, future plans
- **[docs/TECH_STACK_ANALYSIS.md](docs/TECH_STACK_ANALYSIS.md)** - Tech stack comparison and migration plans

### **Recent Updates**
- **[docs/RECENT_CHANGES.md](docs/RECENT_CHANGES.md)** - Last 3 weeks of development (Nov 2025)
- **[docs/problems.md](docs/problems.md)** - Completed tasks and next priorities

### **Performance & Optimization**
- **[docs/DATABASE_ARCHITECTURE.md](docs/DATABASE_ARCHITECTURE.md)** - Database schema and caching strategy

### **Trading Strategies**
- **[docs/OVERNIGHT_STRATEGY_GUIDE.md](docs/OVERNIGHT_STRATEGY_GUIDE.md)** - Overnight range breakout guide
- **[docs/MODULAR_STRATEGY_GUIDE.md](docs/MODULAR_STRATEGY_GUIDE.md)** - Strategy system documentation
- **[docs/STRATEGY_IMPROVEMENTS.md](docs/STRATEGY_IMPROVEMENTS.md)** - Strategy optimization recommendations

### **Deployment**
- **[docs/DEPLOYMENT_GUIDE.md](docs/DEPLOYMENT_GUIDE.md)** - Railway deployment instructions

### **Development**
- **[docs/CLEANUP_PLAN.md](docs/CLEANUP_PLAN.md)** - Code cleanup and organization

---

## 🔧 Configuration Files

### **Environment Variables**
See **[docs/ENV_CONFIGURATION.md](docs/ENV_CONFIGURATION.md)** for complete documentation.

**Core**:
- `PROJECT_X_API_KEY` - Your TopStepX API key
- `PROJECT_X_USERNAME` - Your TopStepX username  
- `TOPSETPX_ACCOUNT_ID` - Account ID to trade on

**Database**:
- `DATABASE_URL` - PostgreSQL connection string (auto-configured on Railway)

**Discord**:
- `DISCORD_WEBHOOK_URL` - Discord webhook for notifications
- `DISCORD_NOTIFICATIONS_ENABLED` - Enable/disable Discord alerts

**Strategies**:
- `OVERNIGHT_ENABLED` - Enable overnight range strategy
- `OVERNIGHT_SYMBOL` - Symbol to trade (e.g., MNQ)
- `OVERNIGHT_POSITION_SIZE` - Position size in contracts
- See **[docs/ENV_CONFIGURATION.md](docs/ENV_CONFIGURATION.md)** for all strategy variables

### **Official SDK Docs**
- Installation: https://project-x-py.readthedocs.io/en/latest/installation.html
- Market Data Guide: https://project-x-py.readthedocs.io/en/latest/user_guide/market_data.html

### **Log Files**
- `trading_bot.log` - Trading bot activity log
- `webhook_server.log` - Webhook server activity log

## ⚡ Performance Notes

### **Current Architecture (Python)**
- Historical data fetches: ~10-15 seconds (SDK initialization overhead)
- Real-time operations: Adequate for most trading strategies
- Webhook latency: <100ms typical
- **Optimization**: Install `psutil` for SDK performance: `pip install psutil`

### **Performance Limitations**
- SDK's TradingSuite initialization adds ~10s overhead per historical fetch
- Python's async I/O is sufficient for most use cases but not sub-millisecond
- Network latency dominates actual trading operations

### **Future Considerations**
For sub-millisecond latency requirements (HFT, ultra-low-latency trading):
- **Go/Rust**: Consider porting critical paths to Go or Rust
- **Architecture**: Frontend (React/Next.js) → Backend (Go gRPC) → Bridge (WebSocket) → Trading Engine
- **Current**: Python is optimal for rapid development and API integration

**Note**: Most retail/prop firm trading strategies don't require sub-ms latency. The current Python implementation handles 99% of use cases efficiently.

## 🚨 Important Notes

### **Risk Management**
- Always test with small position sizes first
- Use paper trading accounts for initial testing
- Monitor logs for any errors or issues
- Set appropriate stop losses for all positions

### **Market Hours**
- Limit orders may be rejected outside trading hours
- Market orders work during regular trading hours
- Check your broker's trading hours for specific contracts

### **Webhook Security**
- Use HTTPS for production webhook endpoints
- Implement authentication for webhook endpoints
- Monitor webhook logs for suspicious activity

## 📞 Support

For issues, questions, or contributions:
1. Check the logs for error messages
2. Verify your API credentials are correct
3. Ensure you're using supported symbols
4. Test with the provided test scripts

## 📄 License

This project is for educational and personal use. Please ensure compliance with your broker's terms of service and local regulations.

---

**⚠️ Disclaimer**: Trading futures involves substantial risk of loss. This software is provided as-is without warranty. Use at your own risk.# Force deployment Tue Oct 14 13:18:55 EDT 2025
