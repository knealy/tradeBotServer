# Master GUI Control Panel - Implementation Plan

## Overview

Create a unified browser-based GUI that combines:
1. **Real-time Chart** (already working)
2. **Position Monitor** (currently terminal-based)
3. **Strategy Runner** (currently terminal-based)
4. **Master Control Panel** (new)

All in a single browser window with tabs or panels.

## Current State

✅ **Working:**
- Chart server with real-time updates (`gui/chart_html.py`)
- Chart opens in browser with TradingView Lightweight Charts
- Chart server provides API endpoints for quotes, positions, orders

❌ **Missing:**
- Position monitoring in browser
- Strategy control in browser
- Master control panel
- Unified interface

## Architecture

### Single Browser Window with Multiple Panels

```
┌─────────────────────────────────────────────────────────┐
│  Master Trading Control Panel                           │
├─────────────────────────────────────────────────────────┤
│  [Chart] [Positions] [Strategy] [Control] [Settings]    │
├─────────────────────────────────────────────────────────┤
│                                                          │
│  ┌──────────────────┐  ┌──────────────────┐          │
│  │  Chart Panel     │  │  Position Panel   │          │
│  │  (TradingView)   │  │  (Real-time)      │          │
│  │                  │  │  - Open Positions│          │
│  │                  │  │  - P&L           │          │
│  │                  │  │  - Orders        │          │
│  └──────────────────┘  └──────────────────┘          │
│                                                          │
│  ┌──────────────────┐  ┌──────────────────┐          │
│  │  Strategy Panel  │  │  Control Panel   │          │
│  │  - Status        │  │  - Start/Stop    │          │
│  │  - Signals       │  │  - Config        │          │
│  │  - Trades        │  │  - Logs          │          │
│  └──────────────────┘  └──────────────────┘          │
│                                                          │
└─────────────────────────────────────────────────────────┘
```

## Implementation Steps

### Phase 1: Extend Chart Server (Current Foundation)

The chart server (`gui/chart_html.py`) already has:
- HTTP server running on localhost
- API endpoints for quotes, positions, orders
- Real-time updates

**Add new endpoints:**
- `/api/chart/strategy/status` - Get strategy status
- `/api/chart/strategy/start` - Start strategy
- `/api/chart/strategy/stop` - Stop strategy
- `/api/chart/strategy/logs` - Get strategy logs
- `/api/chart/account/state` - Get account state (balance, P&L, compliance)

### Phase 2: Create Master HTML Template

Create `gui/master_control.html` that includes:
- Tab navigation (Chart, Positions, Strategy, Control)
- Real-time position table (updates via WebSocket or polling)
- Strategy status panel
- Control buttons (start/stop strategies)
- Settings panel

### Phase 3: Integrate Position Monitoring

**Current:** `scripts/monitor_positions.py` runs in terminal
**New:** Position data displayed in browser panel

**Implementation:**
- Use existing `/api/chart/positions` endpoint
- Create position table component in HTML
- Auto-refresh every 2-5 seconds
- Show: Symbol, Side, Qty, Entry, Current, P&L, P&L%

### Phase 4: Integrate Strategy Control

**Current:** Strategy runs in separate terminal
**New:** Strategy controlled from browser

**Implementation:**
- Add strategy status endpoint to chart server
- Create strategy control panel in HTML
- Buttons: Start, Stop, Pause, Resume
- Display: Strategy name, status, signals, trades, logs
- Real-time log streaming

### Phase 5: Master Control Panel

**Features:**
- Account selection dropdown
- Strategy selection and configuration
- Quick actions (flatten, close all, etc.)
- System status (server health, connection status)
- Settings (refresh rates, alerts, etc.)

## Technical Details

### Server Extensions

Extend `gui/chart_html.py` to add:

```python
# Strategy endpoints
async def handle_strategy_status(request):
    """Get current strategy status"""
    status = await trading_bot.strategy_manager.get_all_strategy_status()
    return web.json_response(status)

async def handle_strategy_start(request):
    """Start a strategy"""
    data = await request.json()
    strategy_name = data.get('strategy')
    symbols = data.get('symbols', [])
    success, message = await trading_bot.strategy_manager.start_strategy(strategy_name, symbols)
    return web.json_response({'success': success, 'message': message})

async def handle_strategy_stop(request):
    """Stop a strategy"""
    data = await request.json()
    strategy_name = data.get('strategy')
    success, message = await trading_bot.strategy_manager.stop_strategy(strategy_name)
    return web.json_response({'success': success, 'message': message})
```

### Frontend Components

**Position Panel:**
- HTML table with auto-refresh
- JavaScript fetch to `/api/chart/positions`
- Update every 2-5 seconds
- Color coding for P&L

**Strategy Panel:**
- Strategy status cards
- Start/Stop buttons
- Signal log display
- Trade history

**Control Panel:**
- Account selector
- Quick actions
- System status indicators

## File Structure

```
gui/
  ├── chart_html.py          # Existing chart server (extend this)
  ├── master_control.html    # New master control page
  └── master_control.js      # JavaScript for master control
```

## Next Steps

1. ✅ Fix position monitoring data extraction (DONE)
2. ✅ Switch to stop_bracket orders (DONE)
3. ⏳ Extend chart server with strategy endpoints
4. ⏳ Create master_control.html template
5. ⏳ Add position panel to browser GUI
6. ⏳ Add strategy control panel to browser GUI
7. ⏳ Integrate everything into single page

## Benefits

- **Single Window:** Everything in one browser tab
- **Real-time Updates:** All data updates automatically
- **Easy Control:** Start/stop strategies with buttons
- **Better Monitoring:** See positions, strategy, and chart together
- **Mobile Friendly:** Can access from phone/tablet

## Migration Path

**Current:** 3 separate terminal windows
**Target:** 1 browser window with all features

**Transition:**
- Keep terminal scripts working (backward compatible)
- Add browser GUI as alternative interface
- Eventually make browser GUI the primary interface
- Terminal scripts become fallback/debugging tools
