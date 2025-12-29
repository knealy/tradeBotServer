# Browser UI - Master Control Unified Dashboard

**Last Updated**: December 29, 2025  
**Status**: ✅ IMPLEMENTED - All widgets consolidated on ONE page (no tabs)

---

## Overview

The Master Control Page is a **unified dashboard** that displays ALL trading widgets on a SINGLE page. No tabs - everything is visible simultaneously in a responsive grid layout.

---

## File Location

**Template**: `/Users/knealy/tradeBotServer/gui/master_control.html`  
**Server**: Served via `chart_html.py` HTTP server on dynamic port  
**Access**: `http://127.0.0.1:<PORT>/` or `http://127.0.0.1:<PORT>/master`

---

## Dashboard Layout

### Grid Structure (Desktop View)

```
┌─────────────────────────────────────────────────────────────────┐
│                    🎯 MASTER TRADING CONTROL                    │
│                         Connection Status                        │
├─────────────────────────────────────────────────────────────────┤
│  Account │ Balance │ Unrealized P&L │ Realized P&L │ Pos │ Ord │
├──────────────────────────────────┬──────────────────────────────┤
│                                  │                              │
│   📈 REAL-TIME CHART & TRADING  │     📊 OPEN POSITIONS        │
│   ┌──────────────────────────┐  │   ┌──────────────────────┐   │
│   │ [Symbol] [Timeframe] [⟳]│  │   │ Symbol│Side│Qty│P&L  │   │
│   │ [Market] [Qty] [BUY/SELL]│  │   │ MNQ  │LONG│ 2 │+$50 │   │
│   └──────────────────────────┘  │   └──────────────────────┘   │
│   ┌──────────────────────────┐  │                              │
│   │                          │  ├──────────────────────────────┤
│   │   TradingView Chart      │  │                              │
│   │   (Real-time updates)    │  │     📋 OPEN ORDERS           │
│   │                          │  │   ┌──────────────────────┐   │
│   │                          │  │   │ Symbol│Side│Price    │   │
│   └──────────────────────────┘  │   │ MNQ  │BUY │5200.00  │   │
│                                  │   └──────────────────────┘   │
├──────────────────────────────────┼──────────────────────────────┤
│                                  │                              │
│   🎯 STRATEGY CONTROL            │   ⚡ QUICK ACTIONS           │
│   ┌────────────────────────┐    │   ┌──────────────────────┐   │
│   │ Simple Candle [ACTIVE] │    │   │  🛑 Flatten All     │   │
│   │ [Start] [Stop]         │    │   │  ❌ Cancel All Ord  │   │
│   ├────────────────────────┤    │   │  🔄 Refresh All     │   │
│   │ Mean Reversion [IDLE]  │    │   └──────────────────────┘   │
│   │ [Start] [Stop]         │    │   System Status: Online      │
│   └────────────────────────┘    │   Last update: 12:34:56      │
└──────────────────────────────────┴──────────────────────────────┘
```

### Mobile/Tablet View (Responsive)

On smaller screens, the grid stacks vertically:
1. Chart (full width)
2. Positions (full width)
3. Orders (full width)
4. Strategy Control (full width)
5. Quick Actions (full width)

---

## Dashboard Components

### 1. Header Bar
**Purpose**: System status and branding

**Elements**:
- 🎯 Master Trading Control (title)
- Connection status indicator (green = connected)

**Update**: Real-time

---

### 2. Account Status Bar
**Purpose**: At-a-glance account metrics

**Elements**:
- **Account Name**: e.g., "PRAC-V2-14334-56363256"
- **Balance**: Total account balance (e.g., "$157,688.19")
- **Unrealized P&L**: Current open position P&L (color-coded: green=profit, red=loss)
- **Realized P&L**: Closed position P&L (color-coded)
- **Open Positions**: Count of open positions
- **Open Orders**: Count of pending orders

**Update Frequency**: 3 seconds

**API Endpoint**: `GET /api/chart/account/state`

---

### 3. Chart Panel (Large, Left Side)
**Purpose**: Real-time price chart with integrated trading controls

#### Chart Display
- **Library**: TradingView Lightweight Charts 4.1.3
- **Type**: Candlestick
- **Colors**: Green (up), Red (down)
- **Size**: ~600px height, responsive width

#### Chart Controls
```
[Symbol ▼] [Timeframe ▼] [Refresh Rate ▼] [⟳ Refresh]
```
- **Symbol Selector**: Populated from available contracts
- **Timeframe Selector**: 30s, 1m, 5m (default), 15m, 1h
- **Refresh Rate**: 1x, 3x, 6x (default) updates per second
- **Refresh Button**: Reload historical data

#### Trading Panel
```
[Order Type ▼] [Qty] [Price] [☐ Bracket] [SL Ticks] [TP Ticks] [BUY] [SELL]
```
- **Order Type**: Market, Limit, Bracket
- **Quantity**: Number of contracts
- **Price**: Limit price (shows for limit orders)
- **Bracket**: Enable stop loss / take profit
- **SL/TP Ticks**: Stop loss and take profit in ticks
- **BUY/SELL**: Execute order

**Update Frequency**: Variable (1-6x per second)

**API Endpoints**:
- `GET /api/chart/quote?symbol=MNQ` - Real-time quote
- `GET /api/chart/reload?symbol=MNQ&timeframe=5m&limit=300` - Historical data
- `POST /api/chart/order` - Place order

---

### 4. Positions Panel (Right Side, Top)
**Purpose**: Monitor open positions

#### Table Columns
| Symbol | Side | Qty | Entry | P&L |
|--------|------|-----|-------|-----|
| MNQ | LONG | 2 | $5,200.00 | +$50.00 |

**Features**:
- Color-coded P&L (green/red)
- Empty state: "No positions"
- Scrollable (max height: 400px)

**Update Frequency**: 3 seconds

**API Endpoint**: `GET /api/chart/positions`

---

### 5. Orders Panel (Right Side, Middle)
**Purpose**: Monitor pending orders

#### Table Columns
| Symbol | Side | Qty | Price | Type |
|--------|------|-----|-------|------|
| MNQ | BUY | 1 | $5,200.00 | Limit |

**Features**:
- Shows all pending orders
- Empty state: "No orders"
- Scrollable (max height: 400px)

**Update Frequency**: 5 seconds

**API Endpoint**: `GET /api/chart/orders`

---

### 6. Strategy Control Panel (Bottom Left)
**Purpose**: Start/stop trading strategies

#### Strategy Cards
```
┌─────────────────────────────┐
│ Simple Candle      [ACTIVE] │
│ MNQ, MES | 2 positions      │
│ [Start] [Stop]              │
└─────────────────────────────┘
```

**Card Elements**:
- Strategy name
- Status badge (ACTIVE/IDLE)
- Trading symbols and position count
- Start/Stop buttons (disabled when appropriate)

**Features**:
- Grid layout (3 columns on desktop, 1 on mobile)
- All strategies shown (active and idle)
- Real-time status updates

**Available Strategies**:
- overnight_range
- mean_reversion
- trend_following
- simple_momentum
- simple_candle
- trend_scalping

**Update Frequency**: 5 seconds

**API Endpoints**:
- `GET /api/chart/strategy/status` - Get all strategies
- `POST /api/chart/strategy/start` - Start strategy
- `POST /api/chart/strategy/stop` - Stop strategy

---

### 7. Quick Actions Panel (Bottom Right)
**Purpose**: One-click critical actions

#### Action Buttons
```
┌────────────────────────┐
│  🛑 Flatten All        │
│  ❌ Cancel All Orders  │
│  🔄 Refresh All Data   │
└────────────────────────┘
```

**Features**:
- **Flatten All**: Close all positions + cancel all orders
  - Shows confirmation dialog
  - Calls `/api/chart/flatten`
- **Cancel All Orders**: Cancel all pending orders only
  - Shows confirmation dialog
  - Calls `/api/chart/cancel_all`
- **Refresh All**: Reload all data (account, positions, orders, strategies, chart)
  - No confirmation needed

#### System Status
- Online/Offline indicator (green/red)
- Last update timestamp (updates every second)

**API Endpoints**:
- `POST /api/chart/flatten` - Flatten all
- `POST /api/chart/cancel_all` - Cancel all orders

---

## Opening the Master Control Page

### From Interactive CLI

```bash
# Start trading bot
python trading_bot.py --account_select=1

# In the trading interface:
Enter command: master MNQ

# Or use the 'gui' alias:
Enter command: gui MNQ
```

**Optional Arguments**:
```bash
master [SYMBOL] [TIMEFRAME] [LIMIT]

Examples:
  master MNQ              # Default: MNQ, 5m, 300 bars
  master MES 15m          # MES, 15m, 300 bars
  master MNQ 1m 500       # MNQ, 1m, 500 bars
```

### From Non-Interactive CLI

```bash
# Open and keep bot running
python trading_bot.py --account_select=1 --command='master MNQ'

# Bot will open browser and continue running
# Press Ctrl+C to stop
```

### From Python Code

```python
from gui.chart_html import _start_chart_server
import webbrowser

# Start server
port = await _start_chart_server(trading_bot, symbol='MNQ')

# Open in browser
master_url = f"http://127.0.0.1:{port}/master"
webbrowser.open(master_url)
```

---

## Complete API Reference

### Data Endpoints (GET)

| Endpoint | Purpose | Update Freq | Response |
|----------|---------|-------------|----------|
| `/api/chart/quote` | Real-time quote | Variable (1-6x/s) | `{latest_bar: {...}}` |
| `/api/chart/reload` | Historical chart data | On demand | `{bars: [...]}` |
| `/api/chart/positions` | Open positions | 3s | `{positions: [...]}` |
| `/api/chart/orders` | Open orders | 5s | `{orders: [...]}` |
| `/api/chart/contracts` | Available symbols | On load | `{symbols: [...]}` |
| `/api/chart/account/state` | Account status | 3s | `{balance, pnl, ...}` |
| `/api/chart/strategy/status` | All strategies | 5s | `{strategies: {...}}` |

### Action Endpoints (POST)

| Endpoint | Purpose | Request Body | Response |
|----------|---------|--------------|----------|
| `/api/chart/order` | Place order | `{symbol, side, quantity, type, ...}` | `{success, orderId}` |
| `/api/chart/flatten` | Flatten all | `{}` | `{success, closed_positions, canceled_orders}` |
| `/api/chart/cancel_all` | Cancel all orders | `{}` | `{success, canceled, failed}` |
| `/api/chart/strategy/start` | Start strategy | `{strategy, symbols}` | `{success, message}` |
| `/api/chart/strategy/stop` | Stop strategy | `{strategy}` | `{success, message}` |

### Request/Response Examples

#### Place Order
```javascript
// Request
POST /api/chart/order
{
  "symbol": "MNQ",
  "side": "BUY",
  "quantity": 2,
  "type": "market"
}

// Response
{
  "success": true,
  "orderId": "2139561234"
}
```

#### Flatten All
```javascript
// Request
POST /api/chart/flatten
{}

// Response
{
  "success": true,
  "closed_positions": ["501158962"],
  "canceled_orders": ["2139561234", "2139561235"],
  "failed_positions": [],
  "failed_orders": []
}
```

#### Start Strategy
```javascript
// Request
POST /api/chart/strategy/start
{
  "strategy": "simple_candle",
  "symbols": ["MNQ", "MES"]
}

// Response
{
  "success": true,
  "message": "Strategy simple_candle started successfully"
}
```

---

## Customization

### Changing Update Frequencies

Edit `master_control.html`, line ~840-850:

```javascript
// Update intervals (in milliseconds)
updateIntervals.account = setInterval(updateAccountStatus, 3000);    // 3s
updateIntervals.positions = setInterval(updatePositions, 3000);      // 3s
updateIntervals.orders = setInterval(updateOrders, 5000);            // 5s
updateIntervals.strategies = setInterval(updateStrategies, 5000);    // 5s

// Chart refresh rate is user-configurable via dropdown (1x, 3x, 6x per second)
```

### Changing Layout

Edit `master_control.html`, line ~40-70 (CSS Grid):

```css
.dashboard {
    display: grid;
    grid-template-columns: 2fr 1fr;  /* Chart takes 2/3, Positions/Orders 1/3 */
    grid-template-rows: auto auto auto;
    gap: 16px;
    padding: 16px;
}

/* Responsive breakpoint */
@media (max-width: 1400px) {
    .dashboard {
        grid-template-columns: 1fr;  /* Single column on small screens */
    }
}
```

### Adding New Widgets

1. **Add HTML panel** in `master_control.html`:
```html
<div class="panel my-new-panel">
    <h2>🎨 My Widget</h2>
    <div id="my-widget-content">...</div>
</div>
```

2. **Add CSS** for positioning:
```css
.my-new-panel {
    grid-column: 1;
    grid-row: 4;
}
```

3. **Add JavaScript** update function:
```javascript
async function updateMyWidget() {
    const response = await fetch(`${BASE_URL}/api/chart/my_endpoint`);
    const data = await response.json();
    // Update DOM
}

// Add to initialization
updateIntervals.myWidget = setInterval(updateMyWidget, 3000);
```

4. **Add backend endpoint** in `chart_html.py`:
```python
async def handle_my_endpoint(request):
    """Handle my widget data."""
    try:
        # Get data
        data = {'key': 'value'}
        response = web.json_response(data)
        response.headers['Access-Control-Allow-Origin'] = '*'
        return response
    except Exception as e:
        logger.error(f"Error: {e}")
        return web.json_response({'error': str(e)}, status=500)

# Register route
app.router.add_get('/api/chart/my_endpoint', handle_my_endpoint)
app.router.add_options('/api/chart/my_endpoint', handle_options)
```

---

## Troubleshooting

### Issue: Page won't load / shows error

**Solution**:
1. Check if server is running:
   ```bash
   # In trading bot logs, look for:
   Chart server started on port 12345
   ```

2. Check firewall isn't blocking localhost connections

3. Try accessing directly: `http://127.0.0.1:<PORT>/master`

### Issue: Chart not showing

**Possible causes**:
1. JavaScript error (check browser console: F12 → Console)
2. TradingView library failed to load (check Network tab)
3. No data available (check `/api/chart/reload` response)

**Solution**:
- Clear browser cache
- Check `trading_bot.log` for errors
- Ensure symbol has data available

### Issue: Data not updating

**Possible causes**:
1. WebSocket connection lost
2. Server stopped responding
3. API endpoint error

**Solution**:
- Check browser console for fetch errors
- Verify server is still running
- Check `trading_bot.log` for API errors
- Click "🔄 Refresh All Data" button

### Issue: "Position close failed" after flatten

**Known issue**: Rust `close_position` temporarily disabled, using Python fallback

**Workaround**: Python implementation works correctly, but may be slower

**Tracking**: See `.cursor/context_profile.json` for latest status

### Issue: Strategies not showing

**Possible causes**:
1. Strategy manager not initialized
2. No strategies loaded

**Solution**:
```python
# Check if strategies are loaded
if not hasattr(trading_bot, 'strategy_manager'):
    print("Strategy manager not available")
else:
    print(f"Loaded strategies: {trading_bot.strategy_manager.strategies.keys()}")
```

---

## Performance Considerations

### Update Frequencies
- **Chart**: 1-6x per second (user-configurable)
  - Higher = smoother, but more CPU/network
  - Lower = less resource usage
- **Account/Positions**: 3 seconds
  - Balance changes are infrequent
- **Orders**: 5 seconds
  - Orders don't change often
- **Strategies**: 5 seconds
  - Status doesn't change frequently

### Browser Resource Usage
- **Memory**: ~50-100MB (typical)
- **CPU**: 1-5% (idle), 5-15% (active trading)
- **Network**: ~1-5 KB/s (minimal)

### Optimization Tips
1. **Reduce chart refresh rate** if CPU usage is high
2. **Close unused browser tabs** to free memory
3. **Use HTTP/2** (aiohttp default) for multiplexed requests
4. **Enable browser hardware acceleration** for smoother charts

---

## Security Notes

### CORS Configuration
All API endpoints include:
```python
response.headers['Access-Control-Allow-Origin'] = '*'
```

**⚠️ Warning**: Allows any origin to access the API. 

**For production**:
```python
# Restrict to specific origin
response.headers['Access-Control-Allow-Origin'] = 'http://127.0.0.1:3000'
```

### Network Binding
Server binds to `127.0.0.1` (localhost only):
```python
runner = web.AppRunner(app)
await runner.setup()
site = web.TCPSite(runner, '127.0.0.1', port)
```

**Not accessible from**:
- Other computers on network
- Internet

**To allow network access** (use with caution):
```python
site = web.TCPSite(runner, '0.0.0.0', port)  # All interfaces
```

### Authentication
Currently **no authentication** on API endpoints.

**For production**:
1. Add API key header check
2. Implement session tokens
3. Use HTTPS with self-signed cert

---

## Future Enhancements

### Planned Features
- [ ] Drag-and-drop widget positioning
- [ ] Customizable grid layout (save/load)
- [ ] Dark/light theme toggle
- [ ] Multiple chart tabs (multi-symbol monitoring)
- [ ] Trade history widget
- [ ] P&L chart (equity curve)
- [ ] Order book widget (Level 2 data)
- [ ] Alert/notification system
- [ ] Mobile app (React Native)

### Under Consideration
- [ ] WebSocket for push updates (replace polling)
- [ ] Audio alerts for fills/errors
- [ ] Hotkeys for quick actions
- [ ] Strategy performance metrics widget
- [ ] Risk management dashboard
- [ ] Backtesting integration

---

## Related Documentation

- **Execution Paths**: `docs/RUST_PYTHON_EXECUTION_PATHS.md`
- **JWT Token Management**: `docs/JWT_TOKEN_VALIDATION_EXPLAINED.md`
- **User Requests Summary**: `docs/USER_REQUEST_SUMMARY_DEC29.md`
- **Context Profile**: `.cursor/context_profile.json`

---

**Questions? Issues?**  
Check `trading_bot.log` for detailed error messages and API call traces.
