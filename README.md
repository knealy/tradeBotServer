# TopStepX Trading Bot 🤖 (FIXED VERSION)

A production-ready trading bot for TopStepX prop firm futures accounts with TradingView webhook integration.

## 🚨 **CRITICAL FIXES APPLIED**

This version includes comprehensive fixes to prevent:
- ❌ **Oversized orphaned positions** (no more -6 contract issues)
- ❌ **Multiple separate positions** (now creates single positions)
- ❌ **Unprotected positions** (all positions have stop/take profit)
- ❌ **OCO order cancellations** (proper bracket management)

## ✨ Features

- **Real TopStepX API Integration** - Live account authentication and trading
- **TradingView Webhook Integration** - Automatic trade execution from TradingView alerts
- **FIXED Position Management** - Single positions with proper staged exits
- **FIXED Signal Filtering** - Only processes entry signals, ignores TP1/TP2
- **FIXED Risk Management** - All positions protected with OCO brackets
- **Enhanced Debounce** - 5-minute window prevents duplicate signals
- **Position Size Limits** - Configurable maximum position sizes

## 🚀 Quick Start

### 1. **Installation**
```bash
# Clone the repository
git clone <repository-url>
cd projectXbot

# Install dependencies
pip install -r requirements.txt

# (Optional) Enable ProjectX SDK for data
export USE_PROJECTX_SDK=1

# Set up environment
./setup_env.sh
```

### 2. **Configuration (FIXED)**
```bash
# Set your TopStepX credentials
export TOPSETPX_USERNAME="your_username_here"
export TOPSETPX_PASSWORD="your_password_here"
export TOPSETPX_ACCOUNT_ID="11481693"

# FIXED: Critical settings to prevent oversized positions
export POSITION_SIZE=3
export MAX_POSITION_SIZE=6
export IGNORE_NON_ENTRY_SIGNALS=true
export IGNORE_TP1_SIGNALS=true
export DEBOUNCE_SECONDS=300
```

### 3. **Test the Fixed System**
```bash
# Test all fixes before deployment
python3 test_fixed_system.py
```

### 4. **Webhook Server (FIXED)**
```bash
# Start webhook server with fixed settings
python3 start_webhook.py --position-size 3

# The system now:
# ✅ Creates single positions with staged exits
# ✅ Ignores TP1/TP2 signals (OCO manages exits)
# ✅ Prevents oversized positions (max 6 contracts)
# ✅ Debounces duplicate signals (5-minute window)
```

## 📋 Trading Commands

### **Interactive Trading Interface**
- `trade <symbol> <side> <quantity>` - Place market order
- `limit <symbol> <side> <quantity> <price>` - Place limit order
- `bracket <symbol> <side> <quantity> <stop_ticks> <profit_ticks>` - Place bracket order
- `native_bracket <symbol> <side> <quantity> <stop_price> <profit_price>` - Native bracket order
- `flatten` - Close all positions and cancel all orders
- `positions` - Show open positions
- `orders` - Show open orders
- `monitor` - Monitor position changes and adjust bracket orders
- `contracts` - List available trading contracts
- `accounts` - List your trading accounts
- `help` - Show help information
- `quit` - Exit trading interface

### **Webhook Server Options**
```bash
python3 webhook_server.py [options]

Options:
  --account-id ACCOUNT_ID     Account ID to trade on (optional)
  --position-size SIZE        Number of contracts per position (default: 1)
  --close-entire-at-tp1       Close entire position at TP1 instead of TP2
  --host HOST                 Host to bind to (default: 0.0.0.0)
  --port PORT                 Port to bind to (default: 8080)
```

## 🎯 Trading Modes

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
├── webhook_server.py           # TradingView webhook server
├── test_webhook.py            # Webhook testing suite
├── test_native_methods.py      # Native API testing
├── load_env.py                # Environment variable loader
├── setup_env.sh              # Environment setup script
├── requirements.txt           # Python dependencies
├── README.md                 # This file
└── mom_current.pine           # TradingView Pine Script
```

## 🔧 Configuration Files

### **Environment Variables**
- `PROJECT_X_API_KEY` - Your TopStepX API key
- `PROJECT_X_USERNAME` - Your TopStepX username
- `USE_PROJECTX_SDK` - Set to `1` to use official SDK for market data

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
