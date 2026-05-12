# Trading Bot GUI - Chart Window

**Canonical UI docs:** [docs/DASHBOARD.md](../docs/DASHBOARD.md) and [docs/README.md](../docs/README.md). Production dashboard assets live under `static/dashboard/` and are served by `servers/start_async_webhook.py`.

A pop-out / dev-oriented chart path uses [chart_html.py](chart_html.py) (aiohttp) and [master_control.html](master_control.html). The notes below describe chart-style usage; prefer the static dashboard for Railway/production.

**Master chart history:** `GET /api/chart/reload` accepts `source=auto|api|databento` (default `auto` or `CHART_RELOAD_SOURCE` from env). **`databento`** reads the stitched canonical files under `historical_data/price/{MNQ,MES,MGC}_{1m,5m}_databento.csv` (see `scripts/databento_stitch_canonical.py`). The Master panel includes a **Bars** dropdown next to the timeframe selector. For **MNQ / MES / MGC** (including outright contract ids), reload **limits** are raised (up to **5000** bars for **Databento file**); the **first viewport** only shows the **most recent ≤200** bars so the chart stays readable—**pan left** for older loaded bars.

## Features

- 📊 **Real-time Charts**: Display candlestick charts with live data
- 🔄 **Auto-refresh**: Automatically updates chart data every 5 seconds
- ⚙️ **Customizable**: Change symbol, timeframe, and bar count on the fly
- 🎨 **Clean Interface**: Simple, intuitive controls

## Installation

Install required dependencies:

```bash
pip install matplotlib
```

## Usage

### From Trading Bot Terminal

1. Start the trading bot:
   ```bash
   python trading_bot.py
   ```

2. In the trading interface, use the `chart` command:
   ```
   chart [symbol] [timeframe] [limit]
   ```

### Examples

```bash
# Open chart for MNQ 5-minute bars (default)
chart

# Open chart for MES with 15-minute bars, 200 bars
chart MES 15m 200

# Open chart for NQ with 1-hour bars, 50 bars
chart NQ 1h 50
```

### Chart Window Controls

- **Symbol**: Enter trading symbol (e.g., MNQ, MES, NQ)
- **Timeframe**: Select from dropdown (5s, 15s, 30s, 1m, 2m, 5m, 15m, 30m, 1h)
- **Bars**: Number of bars to display (default: 100)
- **Refresh**: Manually refresh chart data
- **Auto-refresh**: Toggle automatic updates (default: enabled)

## Technical Details

- Built with **Tkinter** (Python's built-in GUI library)
- Uses **Matplotlib** for chart rendering
- Runs in a separate thread to avoid blocking the terminal
- Automatically closes when window is closed

## Troubleshooting

### "GUI module not available"
- Install matplotlib: `pip install matplotlib`
- Ensure you're using Python 3.7+

### Chart not updating
- Check that auto-refresh is enabled
- Verify symbol and timeframe are valid
- Check terminal for error messages

### Window doesn't appear
- Ensure you have a GUI environment (X11 on Linux, native on macOS/Windows)
- Check that Tkinter is available: `python3 -c "import tkinter"`

