# TopStepX Trading Bot 🤖

A production-ready trading bot for TopStepX prop firm futures accounts with TradingView webhook integration.

## ✨ Features

- **Real TopStepX API Integration** - Live account authentication and trading
- **TradingView Webhook Integration** - Automatic trade execution from TradingView alerts
- **Interactive Trading Interface** - Command-line interface with history and tab completion
- **Market & Limit Orders** - Support for both market and limit order types
- **Bracket Orders** - Automatic stop loss and take profit management
- **Position Management** - Flatten all positions, partial closes, and position tracking
- **Advanced Signal Processing** - Supports all TradingView signal types

## 🚀 Quick Start

### 1. **Installation**
```bash
# Clone the repository
git clone <repository-url>
cd projectXbot

# Install dependencies
pip install -r requirements.txt

# Set up environment
./setup_env.sh
```

### 2. **Configuration**
```bash
# Set your TopStepX credentials
export PROJECT_X_API_KEY="your_api_key_here"
export PROJECT_X_USERNAME="your_username_here"
```

### 3. **Interactive Trading**
```bash
# Start the trading bot
python3 trading_bot.py
```

### 4. **Webhook Server**
```bash
# Start webhook server (conservative mode)
python3 webhook_server.py --position-size 1 --close-entire-at-tp1 true

# Start webhook server (aggressive mode)
python3 webhook_server.py --position-size 3 --close-entire-at-tp1 false
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

### **Log Files**
- `trading_bot.log` - Trading bot activity log
- `webhook_server.log` - Webhook server activity log

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

**⚠️ Disclaimer**: Trading futures involves substantial risk of loss. This software is provided as-is without warranty. Use at your own risk.