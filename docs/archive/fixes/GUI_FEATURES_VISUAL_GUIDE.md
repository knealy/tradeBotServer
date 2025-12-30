# 🎨 GUI Features Visual Guide

## Master Control Dashboard - New Features Overview

This guide shows the visual layout and functionality of all newly implemented features.

---

## 🎨 1. Technicolor Widget Borders

### Visual Effect
Each panel now has a vibrant, glowing border in a repeating color pattern:

```
┌─────────────────────────────────────┐
│  📊 Chart Panel                     │  ← PURPLE border + glow
└─────────────────────────────────────┘

┌─────────────────────────────────────┐
│  📍 Positions Panel                 │  ← RED border + glow
└─────────────────────────────────────┘

┌─────────────────────────────────────┐
│  📋 Orders Panel                    │  ← BLUE border + glow
└─────────────────────────────────────┘

┌─────────────────────────────────────┐
│  🎯 Strategy Control Panel          │  ← ORANGE border + glow
└─────────────────────────────────────┘

┌─────────────────────────────────────┐
│  ⚡ Quick Actions Panel             │  ← YELLOW border + glow
└─────────────────────────────────────┘

┌─────────────────────────────────────┐
│  📡 Strategy Signals Panel          │  ← PURPLE border + glow (repeat)
└─────────────────────────────────────┘

┌─────────────────────────────────────┐
│  ⌨️ Command Input Panel             │  ← RED border + glow (repeat)
└─────────────────────────────────────┘

┌─────────────────────────────────────┐
│  📜 Terminal Logs Panel             │  ← BLUE border + glow (repeat)
└─────────────────────────────────────┘
```

### Color Palette
- **Purple**: `#9333ea` (RGB: 147, 51, 234)
- **Red**: `#dc2626` (RGB: 220, 38, 38)
- **Blue**: `#2563eb` (RGB: 37, 99, 235)
- **Orange**: `#ea580c` (RGB: 234, 88, 12)
- **Yellow**: `#eab308` (RGB: 234, 179, 8)

---

## 👤 2. Account Selection Dropdown

### Location
Top status bar, replacing static account name

### Visual Layout
```
┌──────────────────────────────────────────────────────────────┐
│  Account: [▼ 150KTC-V2-14334-23803636 ($149,885.86)      ] │
│           | Balance: $149,885.86 | Unrealized: $0.00       │
└──────────────────────────────────────────────────────────────┘
```

### Dropdown Options
```
┌──────────────────────────────────────────┐
│ ▼ PRAC-V2-14334-56363256 ($157,688.19) │ ← Practice Account
│   150KTC-V2-14334-23803636 ($149,885.86)│ ← Eval Account 1
│   150KTC-V2-14334-92573394 ($145,342.60)│ ← Eval Account 2
│   50KTC-V2-14334-22535377 ($47,885.74)  │ ← Eval Account 3
│   ...                                    │
└──────────────────────────────────────────┘
```

### Functionality
- Click dropdown to see all accounts
- Shows account name and current balance
- Click to switch accounts
- All widgets refresh automatically

---

## 📊 3. Symbol/Contract Selection

### Location
Chart panel, top-left controls

### Visual Layout
```
┌─────────────────────────────────────────────────────────┐
│  📊 Chart Panel                                         │
│  ┌──────────┬──────────┬──────────┬─────────┐          │
│  │[▼ MNQ  ]│[▼ 5m   ]│[▼ 6x   ]│ 🔄      │          │
│  └──────────┴──────────┴──────────┴─────────┘          │
│                                                          │
│  [Chart Display Area]                                   │
└─────────────────────────────────────────────────────────┘
```

### Dropdown Options
```
┌──────────┐
│ ▼ MNQ   │ ← Micro Nasdaq
│   MES   │ ← Micro S&P 500
│   MYM   │ ← Micro Dow
│   M2K   │ ← Micro Russell
│   NQ    │ ← E-mini Nasdaq
│   ES    │ ← E-mini S&P 500
│   YM    │ ← E-mini Dow
│   RTY   │ ← E-mini Russell
│   GC    │ ← Gold Futures
│   CL    │ ← Crude Oil
│   ...   │ ← 50+ symbols
└──────────┘
```

### Functionality
- Loads all available contracts on startup
- Select any symbol to switch chart
- Chart reloads automatically with new data
- Updates quote stream to new symbol

---

## 📡 4. Strategy Signal Feed Widget

### Visual Layout
```
┌─────────────────────────────────────────────────────────┐
│  📡 Strategy Signals                                [−] │
├─────────────────────────────────────────────────────────┤
│  ┌───────────────────────────────────────────────────┐ │
│  │  BUY                              8:45:23 PM      │ │
│  │  overnight_range - MNQ                            │ │
│  │  Range breakout detected                          │ │
│  │  Price: $25712.50                                 │ │
│  └───────────────────────────────────────────────────┘ │
│  ┌───────────────────────────────────────────────────┐ │
│  │  SELL                             8:42:15 PM      │ │
│  │  simple_candle - MES                              │ │
│  │  EMA cross detected                               │ │
│  │  Price: $5892.25                                  │ │
│  └───────────────────────────────────────────────────┘ │
│  ┌───────────────────────────────────────────────────┐ │
│  │  ALERT                            8:40:01 PM      │ │
│  │  trend_scalping - MNQ                             │ │
│  │  High volatility detected                         │ │
│  └───────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────┘
```

### Signal Types & Colors
- **BUY** signals: Green (`#4caf50`)
- **SELL** signals: Red (`#f44336`)
- **ALERT** signals: Orange (`#ff9800`)

### Information Displayed
1. Signal type (BUY/SELL/ALERT)
2. Timestamp
3. Strategy name
4. Symbol
5. Message/reason
6. Price (if available)

### Features
- Real-time updates via WebSocket
- Auto-scrolls to newest signals
- Keeps last 20 signals
- Collapsible panel

---

## ⌨️ 5. Command Input Widget

### Visual Layout
```
┌─────────────────────────────────────────────────────────┐
│  ⌨️ Command Input                                   [−] │
├─────────────────────────────────────────────────────────┤
│  ┌────────────────────────────────────┬──────────────┐ │
│  │ Enter command...                   │  Execute     │ │
│  └────────────────────────────────────┴──────────────┘ │
│  ┌───────────────────────────────────────────────────┐ │
│  │  > flatten                                        │ │
│  │  {                                                │ │
│  │    "success": true,                               │ │
│  │    "closed_positions": 2,                         │ │
│  │    "canceled_orders": 5                           │ │
│  │  }                                                │ │
│  │                                                   │ │
│  │  > contracts                                      │ │
│  │  Found 51 available contracts                     │ │
│  │  MNQ, MES, MYM, M2K, NQ, ES, YM, RTY...          │ │
│  │                                                   │ │
│  │  > positions                                      │ │
│  │  [                                                │ │
│  │    {                                              │ │
│  │      "symbol": "MNQ",                             │ │
│  │      "quantity": 2,                               │ │
│  │      "unrealizedPnl": 125.50                      │ │
│  │    }                                              │ │
│  │  ]                                                │ │
│  └───────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────┘
```

### Supported Commands
All CLI commands are supported, including:
- `flatten` - Close all positions
- `contracts` - List contracts
- `positions` - Show positions
- `orders` - Show orders
- `strategies status` - Strategy status
- `strategies start <name>` - Start strategy
- `strategies stop <name>` - Stop strategy
- `account` - Show account info
- And many more...

### Features
- Type command and press Enter or click Execute
- Output displays in monospace font
- Success messages in white
- Error messages in red
- JSON output formatted with indentation
- Auto-scrolls to newest output
- Keeps last 50 command/output pairs

---

## 🎯 6. Enhanced Strategy Control Widget

### Visual Layout
```
┌─────────────────────────────────────────────────────────┐
│  🎯 Strategy Control                                [−] │
├─────────────────────────────────────────────────────────┤
│  ┌─ Start New Strategy ─────────────────────────────┐  │
│  │  Strategy: [▼ simple_candle  ]                   │  │
│  │  Symbol(s): [MNQ            ]                    │  │
│  │  Timeframe: [▼ 5m           ]                    │  │
│  │  [▶ Start]                                       │  │
│  └──────────────────────────────────────────────────┘  │
│                                                          │
│  ┌─ Active Strategies ──────────────────────────────┐  │
│  │  ┌────────────────────┐  ┌────────────────────┐ │  │
│  │  │ overnight_range    │  │ simple_candle      │ │  │
│  │  │ ● Running          │  │ ● Running          │ │  │
│  │  │ MNQ, MES           │  │ MNQ                │ │  │
│  │  │ 5m timeframe       │  │ 15s timeframe      │ │  │
│  │  │ [⏸ Stop]           │  │ [⏸ Stop]           │ │  │
│  │  └────────────────────┘  └────────────────────┘ │  │
│  └──────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────┘
```

### Start Strategy Form
**Strategy Dropdown**:
```
┌──────────────────┐
│ ▼ simple_candle │
│   overnight_range│
│   trend_scalping │
│   mean_reversion │
│   trend_following│
│   simple_momentum│
└──────────────────┘
```

**Timeframe Dropdown**:
```
┌──────┐
│ ▼ 5s │
│   15s│
│   30s│
│   1m │
│   2m │
│   5m │ ← Default
│   15m│
│   30m│
│   1h │
└──────┘
```

**Symbol Input**:
- Single symbol: `MNQ`
- Multiple symbols: `MNQ,MES,MYM`
- Comma-separated list

### Strategy Cards
Each active strategy shows:
- Strategy name
- Status indicator (● Running / ○ Stopped)
- Symbols being traded
- Timeframe
- Stop button

### Features
- Start strategies with custom parameters
- Stop running strategies with confirmation
- Real-time status updates
- Grid layout adapts to screen size
- Color-coded status indicators

---

## 🎨 Complete Dashboard Layout

```
┌──────────────────────────────────────────────────────────────────┐
│  TopStepX Trading Bot - Master Control                          │
│  Account: [▼ Select Account] | Balance: $XXX | P&L: $XXX       │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌─────────────────────────────────────┐  ┌─────────────────┐  │
│  │  📊 Chart Panel (PURPLE)            │  │  📍 Positions   │  │
│  │  [Symbol][Time][Rate] 🔄            │  │  (RED)          │  │
│  │                                     │  │                 │  │
│  │  [TradingView Chart Display]        │  │  [Position List]│  │
│  │                                     │  │                 │  │
│  │  [Trading Controls]                 │  └─────────────────┘  │
│  │  Side: [Buy/Sell]                   │                       │
│  │  Type: [Market/Limit]               │  ┌─────────────────┐  │
│  │  Qty: [1] Price: [25712]            │  │  📋 Orders      │  │
│  │  [Place Order]                      │  │  (BLUE)         │  │
│  └─────────────────────────────────────┘  │                 │  │
│                                            │  [Order List]   │  │
│  ┌─────────────────────────────────────┐  └─────────────────┘  │
│  │  🎯 Strategy Control (ORANGE)       │                       │
│  │  [Start Form: Strategy/Symbols/TF]  │  ┌─────────────────┐  │
│  │  [Active Strategy Cards]            │  │  ⚡ Actions     │  │
│  └─────────────────────────────────────┘  │  (YELLOW)       │  │
│                                            │  [Flatten]      │  │
│  ┌─────────────────────────────────────┐  │  [Cancel All]   │  │
│  │  📡 Strategy Signals (PURPLE)       │  │  [Refresh]      │  │
│  │  [Signal Feed Display]              │  └─────────────────┘  │
│  └─────────────────────────────────────┘                       │
│                                                                  │
│  ┌─────────────────────────────────────┐                       │
│  │  ⌨️ Command Input (RED)             │                       │
│  │  [Input Field] [Execute]            │                       │
│  │  [Command Output Display]           │                       │
│  └─────────────────────────────────────┘                       │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  📜 Terminal Logs (BLUE) - Full Width                    │  │
│  │  [Filter: All] [Clear] [✓ Auto-scroll]                  │  │
│  │  [Log Display Area]                                      │  │
│  └──────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────┘
```

---

## 🚀 Quick Start Guide

### 1. Launch the GUI
```bash
python trading_bot.py
# Then run:
master
# Or:
gui
```

### 2. Switch Account
1. Click account dropdown in status bar
2. Select desired account
3. Wait for widgets to refresh

### 3. Change Symbol
1. Click symbol dropdown in chart panel
2. Select new symbol
3. Chart reloads automatically

### 4. Start a Strategy
1. Go to Strategy Control panel
2. Select strategy type
3. Enter symbols (e.g., `MNQ,MES`)
4. Choose timeframe
5. Click Start

### 5. Execute Commands
1. Go to Command Input panel
2. Type command (e.g., `positions`)
3. Press Enter or click Execute
4. View output below

### 6. Monitor Signals
1. Strategy Signals panel shows real-time signals
2. Green = BUY, Red = SELL, Orange = ALERT
3. Auto-scrolls to newest signals

---

## 🎨 Color Reference

### Panel Border Colors
| Panel | Color | Hex | RGB |
|-------|-------|-----|-----|
| Chart | Purple | `#9333ea` | 147, 51, 234 |
| Positions | Red | `#dc2626` | 220, 38, 38 |
| Orders | Blue | `#2563eb` | 37, 99, 235 |
| Strategy | Orange | `#ea580c` | 234, 88, 12 |
| Actions | Yellow | `#eab308` | 234, 179, 8 |
| Signals | Purple | `#9333ea` | 147, 51, 234 |
| Command | Red | `#dc2626` | 220, 38, 38 |
| Logs | Blue | `#2563eb` | 37, 99, 235 |

### Signal Colors
| Type | Color | Hex | RGB |
|------|-------|-----|-----|
| BUY | Green | `#4caf50` | 76, 175, 80 |
| SELL | Red | `#f44336` | 244, 67, 54 |
| ALERT | Orange | `#ff9800` | 255, 152, 0 |

### Status Colors
| Status | Color | Hex | RGB |
|--------|-------|-----|-----|
| Positive P&L | Green | `#4caf50` | 76, 175, 80 |
| Negative P&L | Red | `#f44336` | 244, 67, 54 |
| Running | Green | `#4caf50` | 76, 175, 80 |
| Stopped | Gray | `#888` | 136, 136, 136 |
| Connected | Green | `#4caf50` | 76, 175, 80 |
| Disconnected | Orange | `#ff9800` | 255, 152, 0 |

---

## 📱 Responsive Design

The dashboard adapts to different screen sizes:

### Desktop (1920x1080+)
- 3-column grid layout
- All panels visible
- Full chart width

### Laptop (1366x768)
- 2-column grid layout
- Panels stack vertically
- Responsive chart height

### Tablet (1024x768)
- Single column layout
- Collapsible panels recommended
- Touch-friendly controls

---

## ✨ Pro Tips

1. **Collapse Unused Panels**: Click panel headers to collapse/expand
2. **Keyboard Shortcuts**: Press Enter in command input to execute
3. **Multi-Symbol Trading**: Enter comma-separated symbols in strategy form
4. **Real-Time Updates**: WebSocket provides instant updates (no polling delay)
5. **Command History**: Scroll through command output to see previous results
6. **Signal Filtering**: Future feature - for now, all signals shown
7. **Account Switching**: All data refreshes automatically after switch
8. **Symbol Auto-Complete**: Future feature - for now, type exact symbol

---

## 🎉 Conclusion

The Master Control GUI now provides a complete, professional trading interface with:
- ✅ Beautiful visual design
- ✅ Multi-account support
- ✅ Real-time signals
- ✅ Full command access
- ✅ Advanced strategy control
- ✅ Responsive layout
- ✅ WebSocket real-time updates

**Ready to trade with style!** 🚀💰

