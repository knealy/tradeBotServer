"""
Local TradingView Lightweight Charts - HTML Generator
Creates a standalone HTML file with TradingView Lightweight Charts that can be opened in any browser.
Supports real-time updates and backtesting mode.
"""

import json
import os
import asyncio
from datetime import datetime, timezone
from typing import List, Dict, Optional
from pathlib import Path
from aiohttp import web
import logging

logger = logging.getLogger(__name__)

# Global server instance for real-time charts
_chart_server = None
_chart_server_port = None
_chart_server_trading_bot = None


async def _start_chart_server(trading_bot, symbol: str) -> int:
    """Start a simple HTTP server for real-time chart updates."""
    global _chart_server, _chart_server_port, _chart_server_trading_bot
    
    if _chart_server is not None:
        # Server already running
        _chart_server_trading_bot = trading_bot
        return _chart_server_port
    
    app = web.Application()
    
    # Cache for latest bar to avoid rate limiting
    _latest_bar_cache = {}
    _last_bar_fetch_time = {}
    
    async def handle_quote(request):
        """Handle quote requests for real-time updates."""
        try:
            # Get symbol from query parameter, fallback to server's default symbol
            quote_symbol = request.query.get('symbol') or symbol
            quote = await trading_bot.get_market_quote(quote_symbol)
            if quote and "error" not in quote:
                # Use quote data to build bar instead of fetching historical data
                # This avoids rate limiting from too many historical data requests
                current_price = float(quote.get('last') or quote.get('lastPrice') or quote.get('bid') or 0)
                current_volume = int(quote.get('volume', 0))  # Total daily volume
                current_time = datetime.now(timezone.utc)
                
                # Round timestamp down to minute boundary (for 1m bars)
                # This prevents gaps in the data due to sub-minute timing differences
                timestamp_sec = int(current_time.timestamp() // 60 * 60)
                
                # Get or create latest bar from cache
                cache_key = f"{quote_symbol}_latest"
                volume_cache_key = f"{quote_symbol}_last_volume"
                
                if cache_key not in _latest_bar_cache:
                    # Initialize with current price
                    _latest_bar_cache[cache_key] = {
                        'time': timestamp_sec,
                        'open': current_price,
                        'high': current_price,
                        'low': current_price,
                        'close': current_price,
                        'volume': 0  # Start at 0, will be calculated from volume delta
                    }
                    _last_bar_fetch_time[cache_key] = current_time
                    # Initialize last known volume for delta calculation
                    _latest_bar_cache[volume_cache_key] = current_volume
                else:
                    # Update existing bar
                    cached_bar = _latest_bar_cache[cache_key]
                    last_volume = _latest_bar_cache.get(volume_cache_key, current_volume)
                    
                    # Check if we're in the same minute (for 1m bars) - if not, start new bar
                    if cached_bar['time'] != timestamp_sec:
                        # New bar - reset
                        cached_bar = {
                            'time': timestamp_sec,
                            'open': current_price,
                            'high': current_price,
                            'low': current_price,
                            'close': current_price,
                            'volume': max(0, current_volume - last_volume)  # Volume delta for this bar
                        }
                        # Update last volume reference
                        _latest_bar_cache[volume_cache_key] = current_volume
                    else:
                        # Update current bar
                        cached_bar['high'] = max(cached_bar['high'], current_price)
                        cached_bar['low'] = min(cached_bar['low'], current_price)
                        cached_bar['close'] = current_price
                        # Add volume delta since last update
                        volume_delta = max(0, current_volume - last_volume)
                        if volume_delta > 0:
                            cached_bar['volume'] = cached_bar.get('volume', 0) + volume_delta
                            _latest_bar_cache[volume_cache_key] = current_volume
                    
                    _latest_bar_cache[cache_key] = cached_bar
                
                latest_bar = _latest_bar_cache[cache_key]
                
                # Create response with CORS headers
                response = web.json_response({
                    'quote': quote,
                    'latest_bar': latest_bar,
                    'timestamp': datetime.now().isoformat()
                })
                response.headers['Access-Control-Allow-Origin'] = '*'
                response.headers['Access-Control-Allow-Methods'] = 'GET, OPTIONS'
                response.headers['Access-Control-Allow-Headers'] = '*'
                return response
            else:
                response = web.json_response({'error': 'No quote available'}, status=404)
                response.headers['Access-Control-Allow-Origin'] = '*'
                return response
        except Exception as e:
            logger.error(f"Error fetching quote: {e}")
            import traceback
            logger.error(traceback.format_exc())
            response = web.json_response({'error': str(e)}, status=500)
            response.headers['Access-Control-Allow-Origin'] = '*'
            return response
    
    async def handle_options(request):
        """Handle CORS preflight requests."""
        response = web.Response()
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Methods'] = 'GET, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = '*'
        return response
    
    async def handle_place_order(request):
        """Handle order placement requests."""
        try:
            data = await request.json()
            order_symbol = data.get('symbol', symbol)  # Use order_symbol to avoid scope conflict
            side = data.get('side')  # 'BUY' or 'SELL'
            quantity = int(data.get('quantity', 1))
            order_type = data.get('order_type', 'market')  # 'market', 'limit', 'stop'
            limit_price = data.get('limit_price')
            stop_price = data.get('stop_price')
            stop_loss_price = data.get('stop_loss_price')
            take_profit_price = data.get('take_profit_price')
            enable_bracket = data.get('enable_bracket', False)
            
            # Place order via trading bot
            if order_type == 'market':
                result = await trading_bot.place_market_order(
                    symbol=order_symbol,
                    side=side,
                    quantity=quantity,
                    stop_loss_ticks=None,  # Will use prices if provided
                    take_profit_ticks=None,
                    order_type='market'
                )
            elif order_type == 'limit':
                result = await trading_bot.place_market_order(
                    symbol=order_symbol,
                    side=side,
                    quantity=quantity,
                    order_type='limit',
                    limit_price=limit_price
                )
            elif order_type == 'stop':
                if enable_bracket and stop_loss_price and take_profit_price:
                    result = await trading_bot.place_oco_bracket_with_stop_entry(
                        symbol=order_symbol,
                        side=side,
                        quantity=quantity,
                        entry_price=float(stop_price),
                        stop_loss_price=float(stop_loss_price),
                        take_profit_price=float(take_profit_price)
                    )
                else:
                    result = await trading_bot.place_stop_order(
                        symbol=order_symbol,
                        side=side,
                        quantity=quantity,
                        stop_price=float(stop_price)
                    )
            else:
                result = {'error': f'Unsupported order type: {order_type}'}
            
            response = web.json_response(result)
            response.headers['Access-Control-Allow-Origin'] = '*'
            return response
        except Exception as e:
            logger.error(f"Error placing order: {e}")
            import traceback
            logger.error(traceback.format_exc())
            response = web.json_response({'error': str(e)}, status=500)
            response.headers['Access-Control-Allow-Origin'] = '*'
            return response
    
    async def handle_get_positions(request):
        """Handle position requests."""
        try:
            # Use get_open_positions() - that's the correct method name
            positions = await trading_bot.get_open_positions()
            logger.debug(f"✅ Fetched {len(positions) if positions else 0} positions for chart")
            response = web.json_response({'positions': positions or []})
            response.headers['Access-Control-Allow-Origin'] = '*'
            return response
        except Exception as e:
            logger.error(f"❌ Error fetching positions for chart: {e}")
            import traceback
            logger.error(traceback.format_exc())
            # Return empty positions array instead of 500 error to keep chart functional
            response = web.json_response({'error': str(e), 'positions': []}, status=200)
            response.headers['Access-Control-Allow-Origin'] = '*'
            return response
    
    async def handle_get_contracts(request):
        """Handle contract list requests."""
        try:
            # Force fresh fetch
            contracts = await trading_bot.get_available_contracts(use_cache=False)
            logger.info(f"📋 Fetched {len(contracts)} contracts for chart dropdown")
            
            if not contracts:
                logger.warning("⚠️ No contracts returned - contract cache may be empty. Try running 'contracts' command in CLI first.")
            
            # Group by symbol
            by_symbol = {}
            for c in contracts:
                sym = c.get('symbol') or c.get('Symbol') or 'Unknown'
                contract_id = c.get('contractId') or c.get('ContractId') or c.get('id') or ''
                # Skip 'Unknown' symbols and empty symbols
                if sym and sym != 'Unknown' and sym.strip():
                    if sym not in by_symbol:
                        by_symbol[sym] = []
                    by_symbol[sym].append({'id': contract_id, 'description': c.get('description', '')})
            
            symbols_list = sorted(list(by_symbol.keys()))
            logger.info(f"✅ Grouped into {len(symbols_list)} unique symbols: {symbols_list[:10] if len(symbols_list) > 10 else symbols_list}")
            
            response = web.json_response({'contracts': by_symbol, 'symbols': symbols_list})
            response.headers['Access-Control-Allow-Origin'] = '*'
            return response
        except Exception as e:
            logger.error(f"❌ Error fetching contracts: {e}")
            import traceback
            logger.error(traceback.format_exc())
            response = web.json_response({'error': str(e), 'contracts': {}, 'symbols': []}, status=500)
            response.headers['Access-Control-Allow-Origin'] = '*'
            return response
    
    async def handle_reload_data(request):
        """Handle chart data reload requests."""
        try:
            # Get symbol and timeframe from query params, fallback to outer scope
            reload_symbol = request.query.get('symbol') or symbol
            reload_timeframe = request.query.get('timeframe') or timeframe
            limit = int(request.query.get('limit', 100))
            
            # Fetch fresh historical data
            bars = await trading_bot.get_historical_data(
                symbol=reload_symbol,
                timeframe=reload_timeframe,
                limit=limit
            )
            
            # Convert bars to chart format
            chart_data = []
            for bar in bars:
                try:
                    # Handle timestamp - can be datetime object or string
                    if hasattr(bar, 'timestamp'):
                        if isinstance(bar.timestamp, datetime):
                            ts = int(bar.timestamp.timestamp())
                        elif isinstance(bar.timestamp, str):
                            # Parse ISO string
                            from datetime import datetime as dt
                            dt_obj = dt.fromisoformat(bar.timestamp.replace('Z', '+00:00'))
                            ts = int(dt_obj.timestamp())
                        else:
                            ts = int(bar.timestamp)
                    elif isinstance(bar, dict):
                        ts_val = bar.get('timestamp')
                        if isinstance(ts_val, datetime):
                            ts = int(ts_val.timestamp())
                        elif isinstance(ts_val, str):
                            from datetime import datetime as dt
                            dt_obj = dt.fromisoformat(ts_val.replace('Z', '+00:00'))
                            ts = int(dt_obj.timestamp())
                        else:
                            ts = int(ts_val) if ts_val else 0
                    else:
                        continue
                    
                    chart_data.append({
                        'time': ts,
                        'open': float(bar.open if hasattr(bar, 'open') else bar.get('open', 0)),
                        'high': float(bar.high if hasattr(bar, 'high') else bar.get('high', 0)),
                        'low': float(bar.low if hasattr(bar, 'low') else bar.get('low', 0)),
                        'close': float(bar.close if hasattr(bar, 'close') else bar.get('close', 0)),
                        'volume': float(bar.volume if hasattr(bar, 'volume') else bar.get('volume', 0))
                    })
                except Exception as e:
                    logger.warning(f"Failed to convert bar: {e}")
                    continue
            
            response = web.json_response({'bars': chart_data, 'symbol': reload_symbol, 'timeframe': reload_timeframe})
            response.headers['Access-Control-Allow-Origin'] = '*'
            return response
        except Exception as e:
            logger.error(f"Error reloading chart data: {e}")
            import traceback
            logger.error(traceback.format_exc())
            response = web.json_response({'error': str(e), 'bars': []}, status=500)
            response.headers['Access-Control-Allow-Origin'] = '*'
            return response
    
    app.router.add_get('/api/chart/quote', handle_quote)
    app.router.add_options('/api/chart/quote', handle_options)
    app.router.add_post('/api/chart/order', handle_place_order)
    app.router.add_options('/api/chart/order', handle_options)
    app.router.add_get('/api/chart/positions', handle_get_positions)
    app.router.add_options('/api/chart/positions', handle_options)
    app.router.add_get('/api/chart/contracts', handle_get_contracts)
    app.router.add_options('/api/chart/contracts', handle_options)
    app.router.add_get('/api/chart/reload', handle_reload_data)
    app.router.add_options('/api/chart/reload', handle_options)
    
    # Find available port
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    sock.close()
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', port)
    await site.start()
    
    _chart_server = runner
    _chart_server_port = port
    _chart_server_trading_bot = trading_bot
    
    logger.info(f"📡 Chart server started on http://127.0.0.1:{port}")
    return port


def generate_chart_html(
    symbol: str,
    timeframe: str,
    bars: List[Dict],
    output_path: Optional[str] = None,
    realtime: bool = False,
    backtest: bool = False,
    backtest_speed: float = 1.0,
    server_port: Optional[int] = None
) -> str:
    """
    Generate standalone HTML file with TradingView Lightweight Charts.
    
    Args:
        symbol: Trading symbol (e.g., 'MNQ')
        timeframe: Timeframe (e.g., '5m')
        bars: List of bar dictionaries with keys: timestamp, open, high, low, close, volume
        output_path: Optional path to save HTML file
        realtime: Enable real-time updates
        backtest: Enable backtesting mode
        backtest_speed: Playback speed multiplier
        server_port: Port for real-time server (if realtime=True)
    
    Returns:
        Path to generated HTML file
    """
    # Prepare data for TradingView format
    chart_data = []
    for bar in bars:
        # Convert timestamp to seconds (TradingView expects Unix timestamp in seconds)
        if isinstance(bar.get('timestamp'), str):
            from datetime import datetime as dt
            try:
                ts = dt.fromisoformat(bar['timestamp'].replace('Z', '+00:00'))
                timestamp_sec = int(ts.timestamp())
            except:
                continue
        elif isinstance(bar.get('timestamp'), (int, float)):
            # If in milliseconds, convert to seconds
            timestamp_sec = int(bar['timestamp'] / 1000) if bar['timestamp'] > 1e12 else int(bar['timestamp'])
        else:
            continue
        
        chart_data.append({
            'time': timestamp_sec,
            'open': float(bar.get('open', 0)),
            'high': float(bar.get('high', 0)),
            'low': float(bar.get('low', 0)),
            'close': float(bar.get('close', 0)),
            'volume': int(bar.get('volume', 0))
        })
    
    if not chart_data:
        raise ValueError("No valid bar data provided")
    
    # Sort chart_data by time to ensure chronological order
    chart_data.sort(key=lambda x: x['time'])
    
    # Generate HTML with TradingView Lightweight Charts
    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{symbol} {timeframe} Chart</title>
    <script src="https://unpkg.com/lightweight-charts@4.1.3/dist/lightweight-charts.standalone.production.js"></script>
    <style>
        body {{
            margin: 0;
            padding: 20px;
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
            background: #1a1a1a;
            color: #ffffff;
        }}
        .container {{
            max-width: 1400px;
            margin: 0 auto;
        }}
        .header {{
            margin-bottom: 20px;
        }}
        .header h1 {{
            margin: 0;
            font-size: 24px;
            color: #ffffff;
        }}
        .header .info {{
            margin-top: 5px;
            color: #888;
            font-size: 14px;
        }}
        #chart-container {{
            width: 100%;
            height: 600px;
            background: #1a1a1a;
            border-radius: 8px;
            overflow: hidden;
        }}
        .controls {{
            margin-top: 20px;
            padding: 15px;
            background: #2a2a2a;
            border-radius: 8px;
        }}
        .controls button {{
            background: #2962ff;
            color: white;
            border: none;
            padding: 10px 20px;
            border-radius: 4px;
            cursor: pointer;
            font-size: 14px;
            margin-right: 10px;
        }}
        .controls button:hover {{
            background: #1e53e5;
        }}
        .controls button.active {{
            background: #26a69a;
        }}
        .controls button:disabled {{
            background: #555;
            cursor: not-allowed;
        }}
        .status {{
            margin-top: 10px;
            color: #888;
            font-size: 12px;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>{symbol} {timeframe} Chart</h1>
            <div class="info">
                {len(chart_data)} bars | Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
            </div>
        </div>
        <div id="chart-container"></div>
        {(f'''
        <div class="trading-panel" id="tradingPanel" style="background: #2a2a2a; padding: 10px; margin: 10px 0; border-radius: 8px; border: 1px solid #444;">
            <div style="display: flex; gap: 8px; align-items: center; flex-wrap: wrap;">
                <select id="symbolSelect" onchange="updateSymbol()" style="background: #1a1a1a; color: #d1d5db; border: 1px solid #444; padding: 6px; border-radius: 4px; font-size: 12px;">
                    <option value="{symbol.upper()}" selected>{symbol.upper()}</option>
                </select>
                <select id="timeframeSelect" onchange="updateTimeframe()" style="background: #1a1a1a; color: #d1d5db; border: 1px solid #444; padding: 6px; border-radius: 4px; font-size: 12px;">
                    <option value="30s"{' selected' if timeframe == '30s' else ''}>30s</option>
                    <option value="1m"{' selected' if timeframe == '1m' else ''}>1m</option>
                    <option value="5m"{' selected' if timeframe == '5m' else ''}>5m</option>
                    <option value="15m"{' selected' if timeframe == '15m' else ''}>15m</option>
                    <option value="1h"{' selected' if timeframe == '1h' else ''}>1h</option>
                    <option value="4h"{' selected' if timeframe == '4h' else ''}>4h</option>
                    <option value="1d"{' selected' if timeframe == '1d' else ''}>1d</option>
                </select>
                <select id="orderTypeSelect" onchange="updateOrderType()" style="background: #1a1a1a; color: #d1d5db; border: 1px solid #444; padding: 6px; border-radius: 4px; font-size: 12px; width: 80px;">
                    <option value="market" selected>Market</option>
                    <option value="limit">Limit</option>
                    <option value="stop">Stop</option>
                </select>
                <input type="number" id="quantityInput" value="1" min="1" placeholder="Qty" style="background: #1a1a1a; color: #d1d5db; border: 1px solid #444; padding: 6px; border-radius: 4px; font-size: 12px; width: 60px;">
                <input type="number" id="limitPriceInput" step="0.25" placeholder="Limit" style="background: #1a1a1a; color: #d1d5db; border: 1px solid #444; padding: 6px; border-radius: 4px; font-size: 12px; width: 80px; display: none;">
                <input type="number" id="stopPriceInput" step="0.25" placeholder="Stop" style="background: #1a1a1a; color: #d1d5db; border: 1px solid #444; padding: 6px; border-radius: 4px; font-size: 12px; width: 80px; display: none;">
                <label style="display: flex; align-items: center; gap: 4px; font-size: 11px; color: #888;">
                    <input type="checkbox" id="enableBracketCheck" onchange="updateBracket()" style="margin: 0;">
                    <span>Bracket</span>
                </label>
                <input type="number" id="stopLossPriceInput" step="0.25" placeholder="SL" style="background: #1a1a1a; color: #d1d5db; border: 1px solid #444; padding: 6px; border-radius: 4px; font-size: 12px; width: 70px; display: none;">
                <input type="number" id="takeProfitPriceInput" step="0.25" placeholder="TP" style="background: #1a1a1a; color: #d1d5db; border: 1px solid #444; padding: 6px; border-radius: 4px; font-size: 12px; width: 70px; display: none;">
                <button onclick="placeOrder('BUY')" style="background: #26a69a; color: white; border: none; padding: 6px 12px; border-radius: 4px; cursor: pointer; font-weight: bold; font-size: 12px;">BUY</button>
                <button onclick="placeOrder('SELL')" style="background: #ef5350; color: white; border: none; padding: 6px 12px; border-radius: 4px; cursor: pointer; font-weight: bold; font-size: 12px;">SELL</button>
            </div>
        </div>
        ''') if not backtest else ''}
        <div class="controls">
            <button onclick="refreshChart()">Refresh Data</button>
            <button onclick="exportData()">Export CSV</button>
            {'<button id="realtimeBtn" onclick="toggleRealtime()">Start Real-Time</button>' if not backtest else ''}
            {'<div style="display: inline-block; margin-left: 10px;">Refresh: <select id="refreshRateSelect" onchange="updateRefreshRate()" style="background: #2a2a2a; color: #d1d5db; border: 1px solid #444; padding: 5px;"><option value="1" selected>1x/sec</option><option value="3">3x/sec</option><option value="6">6x/sec</option><option value="12">12x/sec</option></select></div>' if not backtest else ''}
            {'<button id="backtestBtn" onclick="toggleBacktest()">Start Backtest</button>' if backtest else ''}
            {'<button id="pauseBtn" onclick="togglePause()" style="background: #666; display: none;">Pause</button>' if backtest else ''}
            {'<button onclick="loadAllBars()" style="background: #26a69a;">Load All Bars (Test)</button>' if backtest else ''}
            {'<button onclick="exitTestMode()" style="background: #666;">Exit Test Mode</button>' if backtest else ''}
            {'<div style="display: inline-block; margin-left: 10px;">Speed: <select id="speedSelect" onchange="updateBacktestSpeed()" style="background: #2a2a2a; color: #d1d5db; border: 1px solid #444; padding: 5px;"><option value="1">1x</option><option value="2">2x</option><option value="5">5x</option><option value="10">10x</option><option value="25">25x</option><option value="50" selected>50x</option><option value="100">100x</option><option value="150">150x</option><option value="200">200x</option><option value="250">250x</option><option value="300">300x</option><option value="400">400x</option><option value="500">500x</option><option value="750">750x</option><option value="1000">1000x</option></select></div>' if backtest else ''}
            <div class="status" id="status">Chart loaded</div>
        </div>
    </div>

    <script>
        let chart = null;
        let candlestickSeries = null;
        let volumeSeries = null;
        let chartContainer = null; // Global scope for resize handler
        let entryLineSeries = null; // Entry price line
        let stopLossLineSeries = null; // Stop loss line
        let takeProfitLineSeries = null; // Take profit line
        let chartData = {json.dumps(chart_data)};
        let realtimeActive = {'true' if realtime else 'false'};
        let backtestMode = {'true' if backtest else 'false'}; // Mode enabled, not necessarily running
        let backtestActive = false; // Actually running
        let backtestPaused = false; // Paused state
        let backtestIndex = 0;
        let backtestInterval = null;
        let realtimeInterval = null;
        let realtimeRefreshRate = 1; // Updates per second (default: 1x/sec)
        let serverPort = {server_port if server_port else 'null'};
        let backtestSpeed = {backtest_speed}; // Speed multiplier (can be changed via dropdown)
        const baseBacktestIntervalMs = 150000; // Base interval (2.5 minutes for 5m bars)
        const symbol = '{symbol}';
        const timeframe = '{timeframe}';
        let testModeActive = false; // Flag to prevent backtest from interfering with test mode
        
        // Debug: Log initial state
        console.log('Chart initialized:', {{
            chartDataLength: chartData.length,
            backtestMode: backtestMode,
            backtestActive: backtestActive,
            firstBar: chartData[0],
            lastBar: chartData[chartData.length - 1]
        }});
        
        // Calculate interval based on timeframe (in seconds)
        function getTimeframeSeconds(tf) {{
            const match = tf.match(/(\\d+)([mhd])/);
            if (!match) return 60;
            const value = parseInt(match[1]);
            const unit = match[2];
            if (unit === 'm') return value * 60;
            if (unit === 'h') return value * 3600;
            if (unit === 'd') return value * 86400;
            return 60;
        }}
        
        const timeframeSeconds = getTimeframeSeconds(timeframe);
        const backtestIntervalMs = (timeframeSeconds * 1000) / backtestSpeed;
        
        function showError(message) {{
            const statusEl = document.getElementById('status');
            if (statusEl) {{
                statusEl.textContent = 'Error: ' + message;
                statusEl.style.color = '#ef5350';
            }}
            console.error(message);
        }}
        
        function updateStatus(message) {{
            const statusEl = document.getElementById('status');
            if (statusEl) {{
                statusEl.textContent = message;
                statusEl.style.color = '#888';
            }}
        }}
        
        // Initialize chart after library loads
        function initChart() {{
            try {{
                // Prevent double initialization
                if (chart) {{
                    console.log('Chart already initialized, skipping');
                    return;
                }}
                
                // Check if library loaded
                if (typeof LightweightCharts === 'undefined') {{
                    showError('TradingView library failed to load');
                    return;
                }}
                
                if (!chartData || chartData.length === 0) {{
                    showError('No chart data available');
                    return;
                }}
                
                // Get chart container (assign to global variable)
                chartContainer = document.getElementById('chart-container');
                if (!chartContainer) {{
                    showError('Chart container not found');
                    return;
                }}
                
                // Ensure container has dimensions
                const containerWidth = chartContainer.clientWidth || chartContainer.offsetWidth || 1200;
                const containerHeight = 600;
                
                console.log('Chart container dimensions:', containerWidth, 'x', containerHeight);
                
                // Create chart
                chart = LightweightCharts.createChart(chartContainer, {{
                    layout: {{
                        background: {{ color: '#1a1a1a' }},
                        textColor: '#d1d5db',
                    }},
                    grid: {{
                        vertLines: {{ color: '#2a2a2a' }},
                        horzLines: {{ color: '#2a2a2a' }},
                    }},
                    crosshair: {{
                        mode: LightweightCharts.CrosshairMode.Normal,
                    }},
                    rightPriceScale: {{
                        borderColor: '#2a2a2a',
                        autoScale: true,
                    }},
                    timeScale: {{
                        borderColor: '#2a2a2a',
                        timeVisible: true,
                        secondsVisible: true,
                    }},
                    localization: {{
                        timeFormatter: (timestamp) => {{
                            // Convert UTC timestamp to local time for display
                            const date = new Date(timestamp * 1000);
                            return date.toLocaleTimeString('en-US', {{
                                hour: '2-digit',
                                minute: '2-digit',
                                hour12: false
                            }});
                        }},
                    }},
                    timeScale: {{
                        borderColor: '#2a2a2a',
                        timeVisible: true,
                        secondsVisible: false,
                    }},
                    width: containerWidth,
                    height: containerHeight,
                }});
                
                console.log('Chart created successfully');
                
                // Create candlestick series
                if (typeof chart.addCandlestickSeries !== 'function') {{
                    showError('addCandlestickSeries not available');
                    console.error('Available methods:', Object.getOwnPropertyNames(chart));
                    return;
                }}
                
                candlestickSeries = chart.addCandlestickSeries({{
                    upColor: '#26a69a',
                    downColor: '#ef5350',
                    borderVisible: false,
                    wickUpColor: '#26a69a',
                    wickDownColor: '#ef5350',
                    priceScaleId: 'right',
                    visible: true,
                }});
                
                // Create volume series
                volumeSeries = chart.addHistogramSeries({{
                    color: '#26a69a',
                    priceFormat: {{
                        type: 'volume',
                    }},
                    priceScaleId: 'volume',
                    scaleMargins: {{
                        top: 0.8,
                        bottom: 0,
                    }},
                }});
                
                // Set up volume price scale
                chart.priceScale('volume').applyOptions({{
                    scaleMargins: {{
                        top: 0.8,
                        bottom: 0,
                    }},
                }});
                
                // Prepare initial data
                if (!backtestMode) {{
                    // Normal mode: load all data immediately
                    const candlestickData = chartData.map(bar => ({{
                        time: bar.time,
                        open: parseFloat(bar.open),
                        high: parseFloat(bar.high),
                        low: parseFloat(bar.low),
                        close: parseFloat(bar.close),
                    }}));
                    
                    const volumeData = chartData.map(bar => ({{
                        time: bar.time,
                        value: parseInt(bar.volume) || 0,
                        color: parseFloat(bar.close) >= parseFloat(bar.open) ? '#26a69a80' : '#ef535080',
                    }}));
                    
                    // Set data
                    candlestickSeries.setData(candlestickData);
                    volumeSeries.setData(volumeData);
                    
                    // Explicitly set visible range to show all data
                    // Use requestAnimationFrame to ensure chart has processed setData()
                    if (candlestickData.length > 0) {{
                        const firstBar = candlestickData[0];
                        const lastBar = candlestickData[candlestickData.length - 1];
                        
                        requestAnimationFrame(() => {{
                            try {{
                                chart.timeScale().setVisibleRange({{
                                    from: firstBar.time,
                                    to: lastBar.time
                                }});
                                console.log('Normal mode: Set visible range from', firstBar.time, 'to', lastBar.time);
                            }} catch (e) {{
                                console.error('Error setting visible range:', e);
                                chart.timeScale().fitContent();
                            }}
                        }});
                    }} else {{
                        chart.timeScale().fitContent();
                    }}
                    
                    updateStatus('Chart ready - ' + chartData.length + ' bars loaded');
                }} else {{
                    // Backtest mode: initialize with empty arrays, ready for replay
                    candlestickSeries.setData([]);
                    volumeSeries.setData([]);
                    
                    updateStatus('Chart ready - ' + chartData.length + ' bars loaded. Click "Start Backtest" to begin replay.');
                    console.log('Backtest mode: Chart initialized with empty series, ready for replay');
                }}
                
                // Load contracts and initialize UI
                if (serverPort) {{
                    loadContracts();
                    updateOrderType(); // Initialize order type visibility
                    updateBracket(); // Initialize bracket visibility
                }}
                
                // Auto-start real-time if enabled (start immediately)
                if (realtimeActive && serverPort) {{
                    // Small delay to ensure everything is initialized
                    setTimeout(() => {{
                        toggleRealtime();
                    }}, 100);
                }}
            }} catch (error) {{
                showError('Chart error: ' + error.message);
                console.error('Full error:', error);
            }}
        }}
        
        // Real-time update function
        async function updateRealtime() {{
            if (!serverPort) {{
                updateStatus('Real-time server not available');
                return;
            }}
            
            try {{
                // Get current symbol from dropdown
                const currentSymbol = document.getElementById('symbolSelect')?.value || symbol;
                const response = await fetch(`http://127.0.0.1:${{serverPort}}/api/chart/quote?symbol=${{encodeURIComponent(currentSymbol)}}`);
                if (!response.ok) {{
                    throw new Error(`HTTP ${{response.status}}`);
                }}
                const data = await response.json();
                
                if (data.latest_bar) {{
                    const bar = data.latest_bar;
                    const lastBar = chartData[chartData.length - 1];
                    
                    // Check if this is a new bar or update to current bar
                    if (bar.time === lastBar.time) {{
                        // Update current bar
                        lastBar.high = Math.max(lastBar.high, bar.close);
                        lastBar.low = Math.min(lastBar.low, bar.close);
                        lastBar.close = bar.close;
                        lastBar.volume = bar.volume;
                        
                        // Update chart
                        candlestickSeries.update({{
                            time: bar.time,
                            open: lastBar.open,
                            high: lastBar.high,
                            low: lastBar.low,
                            close: lastBar.close,
                        }});
                        
                        volumeSeries.update({{
                            time: bar.time,
                            value: bar.volume,
                            color: bar.close >= lastBar.open ? '#26a69a80' : '#ef535080',
                        }});
                    }} else if (bar.time > lastBar.time) {{
                        // New bar
                        chartData.push(bar);
                        candlestickSeries.update({{
                            time: bar.time,
                            open: bar.open,
                            high: bar.high,
                            low: bar.low,
                            close: bar.close,
                        }});
                        
                        volumeSeries.update({{
                            time: bar.time,
                            value: bar.volume,
                            color: bar.close >= bar.open ? '#26a69a80' : '#ef535080',
                        }});
                    }}
                    
                    updateStatus(`Real-time: ${{data.quote.last || data.quote.bid || 'N/A'}} | ${{new Date().toLocaleTimeString()}}`);
                }}
            }} catch (error) {{
                console.error('Real-time update error:', error);
                updateStatus('Real-time update failed: ' + error.message);
            }}
        }}
        
        // Toggle real-time updates
        function toggleRealtime() {{
            const btn = document.getElementById('realtimeBtn');
            if (!btn) return;
            
            if (realtimeActive) {{
                // Stop
                if (realtimeInterval) {{
                    clearInterval(realtimeInterval);
                    realtimeInterval = null;
                }}
                realtimeActive = false;
                btn.textContent = 'Start Real-Time';
                btn.classList.remove('active');
                updateStatus('Real-time updates stopped');
            }} else {{
                // Start
                if (!serverPort) {{
                    showError('Real-time server not available. Start chart with --realtime flag.');
                    return;
                }}
                realtimeActive = true;
                btn.textContent = 'Stop Real-Time';
                btn.classList.add('active');
                updateRealtime(); // Immediate update
                
                // Calculate interval based on refresh rate (updates per second)
                const intervalMs = 1000 / realtimeRefreshRate;
                realtimeInterval = setInterval(updateRealtime, intervalMs);
                updateStatus(`Real-time updates started at ${{realtimeRefreshRate}}x/sec`);
            }}
        }}
        
        // Backtesting function
        function playBacktestBar() {{
            // Don't play if paused - keep all rendered candles visible
            if (backtestPaused) {{
                return;
            }}
            
            if (backtestIndex >= chartData.length) {{
                // Finished
                toggleBacktest();
                updateStatus('Backtest complete - ' + chartData.length + ' bars replayed');
                return;
            }}
            
            const bar = chartData[backtestIndex];
            
            try {{
                if (!candlestickSeries || !volumeSeries) {{
                    console.error('Chart series not initialized');
                    showError('Chart series not initialized');
                    return;
                }}
                
                // Prepare bar data
                const barData = {{
                    time: bar.time,
                    open: parseFloat(bar.open),
                    high: parseFloat(bar.high),
                    low: parseFloat(bar.low),
                    close: parseFloat(bar.close),
                }};
                
                const volumeData = {{
                    time: bar.time,
                    value: parseInt(bar.volume) || 0,
                    color: parseFloat(bar.close) >= parseFloat(bar.open) ? '#26a69a80' : '#ef535080',
                }};
                
                // Use update() to add/update bars
                candlestickSeries.update(barData);
                volumeSeries.update(volumeData);
                
                // Verify update worked
                const currentData = candlestickSeries.data();
                if (backtestIndex < 5) {{
                    console.log('Backtest bar', backtestIndex + 1, ':', barData);
                    console.log('Total bars in series:', currentData.length);
                }}
                
                // Don't auto-fit content - let user control zoom/pan manually
                // Removed fitContent() calls to allow manual zoom control
                
                const date = new Date(bar.time * 1000);
                updateStatus(`Backtest: ${{backtestIndex + 1}}/${{chartData.length}} | Speed: ${{backtestSpeed}}x | ${{date.toLocaleString()}}`);
            }} catch (error) {{
                console.error('Error playing backtest bar:', error);
                console.error('Bar data:', bar);
                showError('Error playing bar ' + (backtestIndex + 1) + ': ' + error.message);
            }}
            
            backtestIndex++;
        }}
        
        // Toggle backtesting
        function toggleBacktest() {{
            console.log('🔔 toggleBacktest() called');
            console.trace('Call stack:');
            
            // Don't allow backtest to start if we're in test mode
            if (testModeActive && !backtestActive) {{
                console.log('⚠️  Ignoring toggleBacktest() call - test mode is active');
                return;
            }}
            
            const btn = document.getElementById('backtestBtn');
            if (!btn) {{
                console.error('Backtest button not found');
                return;
            }}
            
            if (backtestActive) {{
                // Stop
                if (backtestInterval) {{
                    clearInterval(backtestInterval);
                    backtestInterval = null;
                }}
                backtestActive = false;
                backtestPaused = false; // Reset pause state when stopping
                btn.textContent = 'Start Backtest';
                btn.classList.remove('active');
                
                // Hide pause button
                const pauseBtn = document.getElementById('pauseBtn');
                if (pauseBtn) {{
                    pauseBtn.style.display = 'none';
                }}
                
                updateStatus('Backtest stopped at bar ' + backtestIndex + '/' + chartData.length);
            }} else {{
                // Start
                if (!chart || !candlestickSeries || !volumeSeries) {{
                    showError('Chart not initialized. Please wait...');
                    return;
                }}
                
                if (!chartData || chartData.length === 0) {{
                    showError('No chart data available for backtest');
                    return;
                }}
                
                backtestActive = true;
                backtestPaused = false; // Reset pause state when starting
                backtestIndex = 0;
                btn.textContent = 'Stop Backtest';
                btn.classList.add('active');
                
                // Show pause button
                const pauseBtn = document.getElementById('pauseBtn');
                if (pauseBtn) {{
                    pauseBtn.style.display = 'inline-block';
                    pauseBtn.textContent = 'Pause';
                    pauseBtn.style.background = '#666';
                }}
                
                // Clear existing data and start fresh
                try {{
                    console.log('Starting backtest with', chartData.length, 'bars');
                    console.log('Backtest interval:', backtestIntervalMs, 'ms');
                    console.log('First bar:', chartData[0]);
                    
                    // Reset index
                    backtestIndex = 0;
                    
                    // Clear series
                    candlestickSeries.setData([]);
                    volumeSeries.setData([]);
                    
                    // Small delay to ensure chart is ready, then start
                    setTimeout(() => {{
                        if (chartData.length > 0) {{
                            // Set first bar using setData() to initialize the series
                            const firstBar = chartData[0];
                            const firstBarData = {{
                                time: firstBar.time,
                                open: parseFloat(firstBar.open),
                                high: parseFloat(firstBar.high),
                                low: parseFloat(firstBar.low),
                                close: parseFloat(firstBar.close),
                            }};
                            
                            const firstVolumeData = {{
                                time: firstBar.time,
                                value: parseInt(firstBar.volume) || 0,
                                color: parseFloat(firstBar.close) >= parseFloat(firstBar.open) ? '#26a69a80' : '#ef535080',
                            }};
                            
                            console.log('Setting first bar:', firstBarData);
                            
                            try {{
                            candlestickSeries.setData([firstBarData]);
                            volumeSeries.setData([firstVolumeData]);
                            
                            console.log('First bar set, data count:', candlestickSeries.data().length);
                            
                            // Set visible range to show first ~100 bars (zoomed out for better overview)
                            const timeframeSeconds = getTimeframeSeconds(timeframe);
                            const visibleBars = 100; // Increased from 50 for better zoom out
                            const lastVisibleBar = chartData[Math.min(visibleBars - 1, chartData.length - 1)];
                            chart.timeScale().setVisibleRange({{
                                from: firstBar.time,
                                to: lastVisibleBar.time + (timeframeSeconds * 10) // Add some padding
                            }});
                            console.log('Set visible range to show first', visibleBars, 'bars (zoomed out)');
                                
                                // Verify data was set
                                const verifyData = candlestickSeries.data();
                                console.log('Verified data after setData:', verifyData.length, 'bars');
                                if (verifyData.length > 0) {{
                                    console.log('First bar in series:', verifyData[0]);
                                }}
                                
                                // Update index and status
                                backtestIndex = 1;
                                const date = new Date(firstBar.time * 1000);
                                const intervalMs = baseBacktestIntervalMs / backtestSpeed;
                                updateStatus(`Backtest: 1/${{chartData.length}} | Speed: ${{backtestSpeed}}x | ${{date.toLocaleString()}}`);
                                
                                // Start interval for remaining bars
                                if (chartData.length > 1) {{
                                    backtestInterval = setInterval(() => {{
                                        playBacktestBar();
                                    }}, intervalMs);
                                    console.log('Backtest interval started, next bar in', intervalMs, 'ms (speed:', backtestSpeed + 'x)');
                                }} else {{
                                    // Only one bar, already shown
                                    toggleBacktest();
                                    updateStatus('Backtest complete - 1 bar displayed');
                                }}
                                
                                updateStatus('Backtest started - ' + chartData.length + ' bars to replay');
                            }} catch (setError) {{
                                console.error('Error setting first bar:', setError);
                                showError('Error setting first bar: ' + setError.message);
                                backtestActive = false;
                                btn.textContent = 'Start Backtest';
                                btn.classList.remove('active');
                            }}
                        }} else {{
                            showError('No chart data available');
                        }}
                    }}, 100);
                }} catch (error) {{
                    showError('Error starting backtest: ' + error.message);
                    console.error('Backtest start error:', error);
                    console.error('Error stack:', error.stack);
                    backtestActive = false;
                    btn.textContent = 'Start Backtest';
                    btn.classList.remove('active');
                }}
            }}
        }}
        
        // Wait for DOM and library, ensure container is visible
        function tryInitChart() {{
            const container = document.getElementById('chart-container');
            if (container && (container.clientWidth > 0 || container.offsetWidth > 0)) {{
                initChart();
            }} else {{
                console.log('Container not ready, retrying...');
                setTimeout(tryInitChart, 100);
            }}
        }}
        
        if (document.readyState === 'loading') {{
            document.addEventListener('DOMContentLoaded', function() {{
                setTimeout(tryInitChart, 200);
            }});
        }} else {{
            setTimeout(tryInitChart, 200);
        }}
        
        window.addEventListener('load', function() {{
            if (!chart) {{
                setTimeout(tryInitChart, 200);
            }}
        }});
        
        // Refresh chart
        function refreshChart() {{
            updateStatus('Refresh not available. Re-run chart command to update.');
        }}
        
        // Exit test mode and allow backtest to run
        function exitTestMode() {{
            testModeActive = false;
            console.log('🚪 Test mode deactivated - backtest can now run');
            updateStatus('Test mode exited - you can now start backtest');
        }}
        
        // Update real-time refresh rate from dropdown
        function updateRefreshRate() {{
            const select = document.getElementById('refreshRateSelect');
            if (select) {{
                realtimeRefreshRate = parseInt(select.value);
                console.log('Real-time refresh rate updated to', realtimeRefreshRate, 'updates/sec');
                
                // If real-time is running, restart with new rate
                if (realtimeActive && realtimeInterval) {{
                    clearInterval(realtimeInterval);
                    const intervalMs = 1000 / realtimeRefreshRate;
                    realtimeInterval = setInterval(updateRealtime, intervalMs);
                    updateStatus(`Real-time refresh rate: ${{realtimeRefreshRate}}x/sec`);
                }}
            }}
        }}
        
        // Toggle pause/play for backtest
        function togglePause() {{
            const pauseBtn = document.getElementById('pauseBtn');
            if (!pauseBtn) {{
                console.error('Pause button not found');
                return;
            }}
            
            if (!backtestActive) {{
                console.log('Cannot pause - backtest is not running');
                return;
            }}
            
            if (backtestPaused) {{
                // Resume
                backtestPaused = false;
                pauseBtn.textContent = 'Pause';
                pauseBtn.style.background = '#666';
                updateStatus('Backtest resumed at bar ' + (backtestIndex + 1) + '/' + chartData.length);
                console.log('▶️  Backtest resumed at bar', backtestIndex + 1);
            }} else {{
                // Pause
                backtestPaused = true;
                pauseBtn.textContent = '▶ Play';
                pauseBtn.style.background = '#26a69a';
                updateStatus('Backtest paused at bar ' + backtestIndex + '/' + chartData.length + ' - All candles remain visible');
                console.log('⏸️  Backtest paused at bar', backtestIndex);
            }}
        }}
        
        // Update backtest speed from dropdown
        function updateBacktestSpeed() {{
            const select = document.getElementById('speedSelect');
            if (select) {{
                backtestSpeed = parseInt(select.value);
                console.log('Backtest speed updated to', backtestSpeed + 'x');
                
                // If backtest is running, restart with new speed
                if (backtestActive) {{
                    clearInterval(backtestInterval);
                    const intervalMs = baseBacktestIntervalMs / backtestSpeed;
                    backtestInterval = setInterval(() => {{
                        playBacktestBar();
                    }}, intervalMs);
                    console.log('Backtest interval updated to', intervalMs, 'ms');
                    updateStatus('Backtest speed: ' + backtestSpeed + 'x');
                }}
            }}
        }}
        
        // Test function: Load all bars at once to verify chart works
        function loadAllBars() {{
            if (!chart || !candlestickSeries || !volumeSeries) {{
                showError('Chart not initialized');
                return;
            }}
            
            // Set test mode flag to prevent backtest from interfering
            testModeActive = true;
            console.log('🧪 Test mode activated');
            
            try {{
                console.log('Loading all', chartData.length, 'bars at once (test)...');
                
                // Sort data by time to ensure chronological order
                const sortedChartData = [...chartData].sort((a, b) => a.time - b.time);
                
                const candlestickData = sortedChartData.map(bar => ({{
                    time: Number(bar.time), // Ensure it's a number (TradingView expects Unix timestamp in seconds)
                    open: parseFloat(bar.open),
                    high: parseFloat(bar.high),
                    low: parseFloat(bar.low),
                    close: parseFloat(bar.close),
                }}));
                
                const volumeData = sortedChartData.map(bar => ({{
                    time: Number(bar.time), // Ensure it's a number
                    value: parseInt(bar.volume) || 0,
                    color: parseFloat(bar.close) >= parseFloat(bar.open) ? '#26a69a80' : '#ef535080',
                }}));
                
                // Verify time format - must be numbers, not strings
                const invalidTimes = candlestickData.filter(bar => typeof bar.time !== 'number' || isNaN(bar.time));
                if (invalidTimes.length > 0) {{
                    console.error('Invalid time format found:', invalidTimes.slice(0, 3));
                }}
                
                console.log('First 3 bars sample:', candlestickData.slice(0, 3));
                console.log('Last 3 bars sample:', candlestickData.slice(-3));
                console.log('Time range:', candlestickData[0].time, 'to', candlestickData[candlestickData.length - 1].time);
                console.log('All times are numbers:', candlestickData.every(bar => typeof bar.time === 'number' && !isNaN(bar.time)));
                
                // Log price range for debugging
                const minPrice = Math.min(...candlestickData.map(b => b.low));
                const maxPrice = Math.max(...candlestickData.map(b => b.high));
                console.log('Price range:', minPrice, 'to', maxPrice);
                
                candlestickSeries.setData(candlestickData);
                volumeSeries.setData(volumeData);
                
                console.log('Data set. Series data count:', candlestickSeries.data().length);
                
                // Force price axis to auto-scale
                candlestickSeries.priceScale().applyOptions({{
                    autoScale: true,
                }});
                volumeSeries.priceScale().applyOptions({{
                    autoScale: true,
                }});
                
                // Force resize and fit
                const container = document.getElementById('chart-container');
                if (container) {{
                    const width = container.clientWidth || container.offsetWidth || 1200;
                    chart.applyOptions({{ width: width, height: 600 }});
                    console.log('Chart resized to:', width, 'x 600');
                }}
                
                // Verify data before fitting
                const verifyCount = candlestickSeries.data().length;
                const firstBar = candlestickSeries.data()[0];
                const lastBar = candlestickSeries.data()[verifyCount - 1];
                console.log('All bars loaded. Series has', verifyCount, 'bars');
                console.log('First bar:', firstBar);
                console.log('Last bar:', lastBar);
                
                // Explicitly set visible range to show all data
                // The issue: fitContent() seems to fit to wrong range, so we MUST use setVisibleRange
                if (firstBar && lastBar) {{
                    const firstTime = Number(firstBar.time);
                    const lastTime = Number(lastBar.time);
                    
                    console.log('Attempting to set visible range from', firstTime, 'to', lastTime);
                    
                    // Use requestAnimationFrame to ensure chart has processed setData() first
                    requestAnimationFrame(() => {{
                        try {{
                            chart.timeScale().setVisibleRange({{
                                from: firstTime,
                                to: lastTime
                            }});
                            console.log('✅ setVisibleRange called');
                            
                            // Verify after a frame
                            requestAnimationFrame(() => {{
                                const range = chart.timeScale().getVisibleRange();
                                console.log('Visible range after setVisibleRange:', range);
                                if (range && (range.from > firstTime || range.to < lastTime)) {{
                                    console.error('❌ Range still wrong! Trying scrollToRealTime...');
                                    // Last resort: try scrolling to the first bar
                                    chart.timeScale().scrollToRealTime();
                                    // Then set range again
                                    setTimeout(() => {{
                                        chart.timeScale().setVisibleRange({{ from: firstTime, to: lastTime }});
                                    }}, 100);
                                }} else if (range) {{
                                    console.log('✅ Range is correct!');
                                    
                                    // Force chart to redraw by applying options
                                    chart.applyOptions({{
                                        timeScale: {{
                                            visible: true,
                                        }},
                                    }});
                                    
                                    // Force price scale to autoscale
                                    candlestickSeries.priceScale().applyOptions({{
                                        autoScale: true,
                                    }});
                                    
                                    console.log('Forced chart redraw and autoscale');
                                }}
                            }});
                        }} catch (e) {{
                            console.error('Error in setVisibleRange:', e);
                        }}
                    }});
                }}
                
                updateStatus('All ' + verifyCount + ' bars loaded (test mode)');
                console.log('✅ All bars loaded successfully in test mode');
            }} catch (error) {{
                console.error('Error loading all bars:', error);
                console.error('Error stack:', error.stack);
                showError('Error: ' + error.message);
            }} finally {{
                // Keep test mode active so backtest doesn't interfere
                console.log('🧪 Test mode remains active (bars should be visible)');
            }}
        }}
        
        // Place order from chart
        async function placeOrder(side) {{
            if (!serverPort) {{
                showError('Trading server not available');
                return;
            }}
            
            try {{
                const orderType = document.getElementById('orderTypeSelect').value;
                const quantity = parseInt(document.getElementById('quantityInput').value) || 1;
                const limitPrice = parseFloat(document.getElementById('limitPriceInput').value) || null;
                const stopPrice = parseFloat(document.getElementById('stopPriceInput').value) || null;
                const enableBracket = document.getElementById('enableBracketCheck').checked;
                const stopLossPrice = parseFloat(document.getElementById('stopLossPriceInput').value) || null;
                const takeProfitPrice = parseFloat(document.getElementById('takeProfitPriceInput').value) || null;
                
                // Validate required fields
                if (orderType === 'limit' && !limitPrice) {{
                    showError('Limit price required for limit orders');
                    return;
                }}
                if (orderType === 'stop' && !stopPrice) {{
                    showError('Stop price required for stop orders');
                    return;
                }}
                if (enableBracket && (!stopLossPrice || !takeProfitPrice)) {{
                    showError('Stop loss and take profit prices required for bracket orders');
                    return;
                }}
                
                const symbolSelect = document.getElementById('symbolSelect');
                const orderSymbol = symbolSelect?.value || symbol;
                
                if (!orderSymbol || orderSymbol.trim() === '' || orderSymbol === 'UNKNOWN') {{
                    showError('Please select a symbol first');
                    return;
                }}
                
                updateStatus(`Placing ${{side}} ${{orderType}} order...`);
                
                const response = await fetch(`http://127.0.0.1:${{serverPort}}/api/chart/order`, {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{
                        symbol: orderSymbol,
                        side: side,
                        quantity: quantity,
                        order_type: orderType,
                        limit_price: limitPrice,
                        stop_price: stopPrice,
                        stop_loss_price: stopLossPrice,
                        take_profit_price: takeProfitPrice,
                        enable_bracket: enableBracket
                    }})
                }});
                
                const result = await response.json();
                
                if (result.error) {{
                    showError(`Order failed: ${{result.error}}`);
                }} else {{
                    updateStatus(`✅ Order placed successfully!`);
                    // Update position lines
                    await updatePositionLines();
                }}
            }} catch (error) {{
                showError(`Order error: ${{error.message}}`);
                console.error('Order placement error:', error);
            }}
        }}
        
        // Update order type UI visibility
        function updateOrderType() {{
            const orderType = document.getElementById('orderTypeSelect').value;
            const limitInput = document.getElementById('limitPriceInput');
            const stopInput = document.getElementById('stopPriceInput');
            
            if (orderType === 'limit') {{
                limitInput.style.display = 'inline-block';
                stopInput.style.display = 'none';
            }} else if (orderType === 'stop') {{
                limitInput.style.display = 'none';
                stopInput.style.display = 'inline-block';
            }} else {{
                limitInput.style.display = 'none';
                stopInput.style.display = 'none';
            }}
            updateBracket(); // Also update bracket visibility
        }}
        
        // Update bracket UI visibility
        function updateBracket() {{
            const enableBracket = document.getElementById('enableBracketCheck').checked;
            const slInput = document.getElementById('stopLossPriceInput');
            const tpInput = document.getElementById('takeProfitPriceInput');
            
            if (enableBracket) {{
                slInput.style.display = 'inline-block';
                tpInput.style.display = 'inline-block';
            }} else {{
                slInput.style.display = 'none';
                tpInput.style.display = 'none';
            }}
        }}
        
        // Load available contracts and populate symbol dropdown
        async function loadContracts() {{
            if (!serverPort) {{
                console.log('No server port, skipping contract load');
                return;
            }}
            
            try {{
                console.log('Loading contracts from server...');
                const response = await fetch(`http://127.0.0.1:${{serverPort}}/api/chart/contracts`);
                if (!response.ok) {{
                    console.error('Failed to fetch contracts:', response.status);
                    return;
                }}
                const data = await response.json();
                console.log('Contracts response:', data);
                const symbols = data.symbols || [];
                
                if (symbols.length === 0) {{
                    console.warn('No symbols returned from contracts endpoint');
                    return;
                }}
                
                const select = document.getElementById('symbolSelect');
                if (!select) {{
                    console.error('Symbol select element not found');
                    return;
                }}
                
                const currentValue = select.value || '{symbol}';
                select.innerHTML = '';
                
                symbols.forEach(sym => {{
                    const option = document.createElement('option');
                    option.value = sym;
                    option.textContent = sym;
                    if (sym === currentValue || (currentValue === '{symbol}' && sym === '{symbol}')) {{
                        option.selected = true;
                    }}
                    select.appendChild(option);
                }});
                
                // If no symbol was selected and we have symbols, select the first one or the default
                if (select.selectedIndex === -1 && symbols.length > 0) {{
                    const defaultSymbol = '{symbol}';
                    const foundDefault = symbols.find(s => s === defaultSymbol);
                    if (foundDefault) {{
                        select.value = foundDefault;
                    }} else {{
                        select.selectedIndex = 0;
                    }}
                }}
                
                console.log('Contracts loaded:', symbols.length, 'symbols');
            }} catch (error) {{
                console.error('Error loading contracts:', error);
                updateStatus('Failed to load contracts: ' + error.message);
            }}
        }}
        
        // Reload chart data for new symbol/timeframe
        async function reloadChartData() {{
            if (!serverPort || !chart || !candlestickSeries) return;
            
            const symbolSelect = document.getElementById('symbolSelect');
            const newSymbol = symbolSelect?.value || '{symbol}';
            const newTimeframe = document.getElementById('timeframeSelect').value || '{timeframe}';
            
            if (!newSymbol || newSymbol.trim() === '') {{
                showError('Please select a symbol first');
                return;
            }}
            
            updateStatus(`Loading ${{newSymbol}} ${{newTimeframe}}...`);
            
            try {{
                const response = await fetch(`http://127.0.0.1:${{serverPort}}/api/chart/reload?symbol=${{encodeURIComponent(newSymbol)}}&timeframe=${{encodeURIComponent(newTimeframe)}}&limit=100`);
                const data = await response.json();
                
                if (data.error) {{
                    showError(`Failed to reload: ${{data.error}}`);
                    return;
                }}
                
                if (!data.bars || data.bars.length === 0) {{
                    showError('No data available for this symbol/timeframe');
                    return;
                }}
                
                // Update chart data
                const chartBars = data.bars.map(bar => ({{
                    time: bar.time,
                    open: bar.open,
                    high: bar.high,
                    low: bar.low,
                    close: bar.close
                }}));
                
                candlestickSeries.setData(chartBars);
                
                // Update volume if available
                if (volumeSeries && data.bars[0].volume !== undefined) {{
                    const volumeBars = data.bars.map(bar => ({{
                        time: bar.time,
                        value: bar.volume,
                        color: bar.close >= bar.open ? '#26a69a80' : '#ef535080'
                    }}));
                    volumeSeries.setData(volumeBars);
                }}
                
                // Update chart title
                document.querySelector('.header h1').textContent = `${{newSymbol}} ${{newTimeframe}} Chart`;
                
                // Fit content to show all bars
                chart.timeScale().fitContent();
                
                updateStatus(`✅ Chart updated: ${{data.bars.length}} bars`);
            }} catch (error) {{
                showError(`Error reloading chart: ${{error.message}}`);
                console.error('Reload error:', error);
            }}
        }}
        
        // Update symbol (reload chart data)
        function updateSymbol() {{
            reloadChartData();
        }}
        
        // Update timeframe (reload chart data)
        function updateTimeframe() {{
            reloadChartData();
        }}
        
        // Update position lines on chart
        async function updatePositionLines() {{
            if (!serverPort || !chart) return;
            
            try {{
                const response = await fetch(`http://127.0.0.1:${{serverPort}}/api/chart/positions`);
                const data = await response.json();
                const positions = data.positions || [];
                
                // Find position for current symbol
                const currentSymbol = document.getElementById('symbolSelect').value;
                const position = positions.find(p => p.symbol === currentSymbol || p.contractId?.includes(currentSymbol));
                
                if (!position || !position.quantity || position.quantity === 0) {{
                    // No position - clear lines
                    if (entryLineSeries) entryLineSeries.setData([]);
                    if (stopLossLineSeries) stopLossLineSeries.setData([]);
                    if (takeProfitLineSeries) takeProfitLineSeries.setData([]);
                    return;
                }}
                
                // Get current price range for line placement
                const timeRange = chart.timeScale().getVisibleRange();
                if (!timeRange) return;
                
                const entryPrice = parseFloat(position.entryPrice || position.averagePrice || 0);
                const stopLoss = parseFloat(position.stopLoss || 0);
                const takeProfit = parseFloat(position.takeProfit || 0);
                const quantity = parseFloat(position.quantity || 0);
                
                // Create or get line series
                if (!entryLineSeries) {{
                    entryLineSeries = chart.addLineSeries({{
                        color: quantity > 0 ? '#26a69a' : '#ef5350',
                        lineWidth: 2,
                        lineStyle: LightweightCharts.LineStyle.Solid,
                        title: 'Entry'
                    }});
                }}
                if (!stopLossLineSeries && stopLoss > 0) {{
                    stopLossLineSeries = chart.addLineSeries({{
                        color: '#ef5350',
                        lineWidth: 2,
                        lineStyle: LightweightCharts.LineStyle.Dashed,
                        title: 'Stop Loss'
                    }});
                }}
                if (!takeProfitLineSeries && takeProfit > 0) {{
                    takeProfitLineSeries = chart.addLineSeries({{
                        color: '#26a69a',
                        lineWidth: 2,
                        lineStyle: LightweightCharts.LineStyle.Dashed,
                        title: 'Take Profit'
                    }});
                }}
                
                // Set line data (horizontal lines across visible time range)
                if (entryPrice > 0) {{
                    entryLineSeries.setData([
                        {{ time: timeRange.from, value: entryPrice }},
                        {{ time: timeRange.to, value: entryPrice }}
                    ]);
                }}
                if (stopLoss > 0 && stopLossLineSeries) {{
                    stopLossLineSeries.setData([
                        {{ time: timeRange.from, value: stopLoss }},
                        {{ time: timeRange.to, value: stopLoss }}
                    ]);
                }}
                if (takeProfit > 0 && takeProfitLineSeries) {{
                    takeProfitLineSeries.setData([
                        {{ time: timeRange.from, value: takeProfit }},
                        {{ time: timeRange.to, value: takeProfit }}
                    ]);
                }}
            }} catch (error) {{
                console.error('Error updating position lines:', error);
            }}
        }}
        
        // Export data to CSV
        function exportData() {{
            try {{
                const csv = [
                    ['Time', 'Open', 'High', 'Low', 'Close', 'Volume'].join(','),
                    ...chartData.map(bar => {{
                        const date = new Date(bar.time * 1000);
                        return [
                            date.toISOString(),
                            bar.open,
                            bar.high,
                            bar.low,
                            bar.close,
                            bar.volume
                        ].join(',');
                    }})
                ].join('\\n');
                
                const blob = new Blob([csv], {{ type: 'text/csv' }});
                const url = window.URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = '{symbol}_{timeframe}_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv';
                a.click();
                window.URL.revokeObjectURL(url);
                updateStatus('CSV exported');
            }} catch (error) {{
                showError('Export error: ' + error.message);
            }}
        }}
        
        // Handle window resize
        window.addEventListener('resize', () => {{
            if (chart && chartContainer) {{
                chart.applyOptions({{ width: chartContainer.clientWidth }});
            }} else if (chart) {{
                // Fallback: get container again if not in scope
                const container = document.getElementById('chart-container');
                if (container) {{
                    chart.applyOptions({{ width: container.clientWidth }});
                }}
            }}
        }});
    </script>
</body>
</html>"""
    
    # Determine output path
    if output_path is None:
        charts_dir = Path("charts")
        charts_dir.mkdir(exist_ok=True)
        output_path = str(charts_dir / f"{symbol}_{timeframe}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html")
    
    # Write HTML file
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html_content)
    
    return output_path


async def open_chart_html_async(
    trading_bot, 
    symbol: str = 'MNQ', 
    timeframe: str = '5m', 
    limit: int = 100,
    realtime: bool = False,
    backtest: bool = False,
    backtest_start: Optional[str] = None,
    backtest_end: Optional[str] = None,
    backtest_speed: float = 1.0
):
    """
    Generate and open TradingView Lightweight Charts HTML file (async version).
    
    Args:
        trading_bot: TopStepXTradingBot instance
        symbol: Trading symbol
        timeframe: Timeframe
        limit: Number of bars
        realtime: Enable real-time updates (polls for new quotes)
        backtest: Enable backtesting mode (replays historical bars)
        backtest_start: Start date for backtesting (YYYY-MM-DD or YYYY-MM-DD HH:MM:SS)
        backtest_end: End date for backtesting (YYYY-MM-DD or YYYY-MM-DD HH:MM:SS)
        backtest_speed: Playback speed multiplier (1.0 = real-time, 2.0 = 2x speed, etc.)
    
    Returns:
        Path to generated HTML file
    """
    # For backtesting, fetch all bars in the date range
    if backtest and backtest_start and backtest_end:
        from datetime import datetime
        try:
            start_dt = datetime.fromisoformat(backtest_start.replace('Z', '+00:00'))
            end_dt = datetime.fromisoformat(backtest_end.replace('Z', '+00:00'))
        except:
            try:
                start_dt = datetime.strptime(backtest_start, '%Y-%m-%d')
                end_dt = datetime.strptime(backtest_end, '%Y-%m-%d')
            except:
                raise ValueError(f"Invalid date format. Use YYYY-MM-DD or YYYY-MM-DD HH:MM:SS")
        
        bars = await trading_bot.get_historical_data(
            symbol=symbol,
            timeframe=timeframe,
            start_time=start_dt,
            end_time=end_dt
        )
    else:
        # Fetch historical data (already in async context)
        bars = await trading_bot.get_historical_data(
            symbol=symbol,
            timeframe=timeframe,
            limit=limit
        )
    
    if not bars:
        raise ValueError(f"No data available for {symbol} {timeframe}")
    
    # Convert to dict format if needed
    if bars and hasattr(bars[0], 'timestamp'):
        from datetime import timezone as tz
        bars = [
            {
                # Convert timestamp to Unix seconds directly to avoid timezone issues
                # If timestamp is naive, assume UTC
                'timestamp': int(bar.timestamp.replace(tzinfo=tz.utc).timestamp()) if bar.timestamp.tzinfo is None else int(bar.timestamp.timestamp()),
                'open': bar.open,
                'high': bar.high,
                'low': bar.low,
                'close': bar.close,
                'volume': bar.volume,
            }
            for bar in bars
        ]
    
    if not bars:
        raise ValueError(f"No data available for {symbol} {timeframe}")
    
    # Get trading bot reference for real-time updates (store in a way the HTML can access)
    # We'll create a simple HTTP server endpoint if realtime mode is enabled
    server_port = None
    if realtime:
        # Start a simple HTTP server for real-time updates
        server_port = await _start_chart_server(trading_bot, symbol)
    
    # Generate HTML
    html_path = generate_chart_html(
        symbol, 
        timeframe, 
        bars, 
        realtime=realtime,
        backtest=backtest,
        backtest_speed=backtest_speed,
        server_port=server_port
    )
    
    # Open in browser
    import webbrowser
    import os
    file_url = f"file://{os.path.abspath(html_path)}"
    webbrowser.open(file_url)
    
    return html_path


def open_chart_html(trading_bot, symbol: str = 'MNQ', timeframe: str = '5m', limit: int = 100):
    """
    Synchronous wrapper for open_chart_html_async.
    For use in non-async contexts (creates new event loop).
    
    Args:
        trading_bot: TopStepXTradingBot instance
        symbol: Trading symbol
        timeframe: Timeframe
        limit: Number of bars
    
    Returns:
        Path to generated HTML file
    """
    import asyncio
    
    # Check if we're in an async context
    try:
        loop = asyncio.get_running_loop()
        # If we get here, we're in an async context - use create_task
        # But this is a sync function, so we need to handle it differently
        raise RuntimeError("Use open_chart_html_async() in async contexts")
    except RuntimeError:
        # No running loop - create new one
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(
                open_chart_html_async(trading_bot, symbol, timeframe, limit)
            )
        finally:
            loop.close()
