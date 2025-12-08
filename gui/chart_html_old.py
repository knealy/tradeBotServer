"""
Local TradingView Lightweight Charts - HTML Generator
Creates a standalone HTML file with TradingView Lightweight Charts that can be opened in any browser.
No server required - works completely offline after initial load.
"""

import json
import os
from datetime import datetime
from typing import List, Dict, Optional
from pathlib import Path


def generate_chart_html(
    symbol: str,
    timeframe: str,
    bars: List[Dict],
    output_path: Optional[str] = None
) -> str:
    """
    Generate standalone HTML file with TradingView Lightweight Charts.
    
    Args:
        symbol: Trading symbol (e.g., 'MNQ')
        timeframe: Timeframe (e.g., '5m')
        bars: List of bar dictionaries with keys: timestamp, open, high, low, close, volume
        output_path: Optional path to save HTML file (default: ./charts/{symbol}_{timeframe}.html)
    
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
        <div class="controls">
            <button onclick="refreshChart()">Refresh Data</button>
            <button onclick="exportData()">Export CSV</button>
            <div class="status" id="status">Chart loaded</div>
        </div>
    </div>

    <script>
        // Chart data
        const chartData = {json.dumps(chart_data)};
        
        // Create chart
        const chartContainer = document.getElementById('chart-container');
        const chart = LightweightCharts.createChart(chartContainer, {{
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
            }},
            timeScale: {{
                borderColor: '#2a2a2a',
                timeVisible: true,
                secondsVisible: false,
            }},
        }});
        
        // Create candlestick series
        const candlestickSeries = chart.addCandlestickSeries({{
            upColor: '#26a69a',
            downColor: '#ef5350',
            borderVisible: false,
            wickUpColor: '#26a69a',
            wickDownColor: '#ef5350',
        }});
        
        // Create volume series
        const volumeSeries = chart.addHistogramSeries({{
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
        
        // Prepare data
        const candlestickData = chartData.map(bar => ({{
            time: bar.time,
            open: bar.open,
            high: bar.high,
            low: bar.low,
            close: bar.close,
        }}));
        
        const volumeData = chartData.map(bar => ({{
            time: bar.time,
            value: bar.volume,
            color: bar.close >= bar.open ? '#26a69a80' : '#ef535080',
        }}));
        
        // Set data
        candlestickSeries.setData(candlestickData);
        volumeSeries.setData(volumeData);
        
        // Fit content
        chart.timeScale().fitContent();
        
        // Update status
        function updateStatus(message) {{
            document.getElementById('status').textContent = message;
        }}
        
        // Refresh chart (placeholder - would need to fetch new data)
        function refreshChart() {{
            updateStatus('Refresh not available in standalone mode. Re-run chart command to update.');
        }}
        
        // Export data to CSV
        function exportData() {{
            const csv = [
                ['Time', 'Open', 'High', 'Low', 'Close', 'Volume'].join(','),
                ...chartData.map(bar => {{
                    const date = new Date(bar.time);
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
        }}
        
        // Handle window resize
        window.addEventListener('resize', () => {{
            chart.applyOptions({{ width: chartContainer.clientWidth }});
        }});
        
        updateStatus('Chart ready');
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


async def open_chart_html_async(trading_bot, symbol: str = 'MNQ', timeframe: str = '5m', limit: int = 100):
    """
    Generate and open TradingView Lightweight Charts HTML file (async version).
    
    Args:
        trading_bot: TopStepXTradingBot instance
        symbol: Trading symbol
        timeframe: Timeframe
        limit: Number of bars
    
    Returns:
        Path to generated HTML file
    """
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
        bars = [
            {
                'timestamp': bar.timestamp.isoformat() if hasattr(bar.timestamp, 'isoformat') else str(bar.timestamp),
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
    
    # Convert to dict format if needed
    if bars and hasattr(bars[0], 'timestamp'):
        bars = [
            {
                'timestamp': bar.timestamp.isoformat() if hasattr(bar.timestamp, 'isoformat') else str(bar.timestamp),
                'open': bar.open,
                'high': bar.high,
                'low': bar.low,
                'close': bar.close,
                'volume': bar.volume,
            }
            for bar in bars
        ]
    
    # Generate HTML
    html_path = generate_chart_html(symbol, timeframe, bars)
    
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

