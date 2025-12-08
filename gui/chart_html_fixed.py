"""
Chart HTML Generator - Fixed version with proper TradingView API
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
        .error {{
            color: #ef5350;
            padding: 20px;
            background: #2a1a1a;
            border-radius: 8px;
            margin-top: 20px;
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
            <div class="status" id="status">Loading chart...</div>
        </div>
        <div id="error-message" class="error" style="display: none;"></div>
    </div>

    <script>
        let chart = null;
        let candlestickSeries = null;
        let volumeSeries = null;
        const chartData = {json.dumps(chart_data)};
        
        function showError(message) {{
            const errorEl = document.getElementById('error-message');
            if (errorEl) {{
                errorEl.textContent = message;
                errorEl.style.display = 'block';
            }}
            const statusEl = document.getElementById('status');
            if (statusEl) {{
                statusEl.textContent = 'Error: ' + message;
            }}
            console.error(message);
        }}
        
        function updateStatus(message) {{
            const statusEl = document.getElementById('status');
            if (statusEl) {{
                statusEl.textContent = message;
            }}
        }}
        
        // Wait for library to load
        function initChart() {{
            try {{
                // Check if library loaded
                if (typeof LightweightCharts === 'undefined') {{
                    showError('TradingView Lightweight Charts library failed to load. Please check your internet connection.');
                    return;
                }}
                
                if (!chartData || chartData.length === 0) {{
                    showError('No chart data available');
                    return;
                }}
                
                // Get chart container
                const chartContainer = document.getElementById('chart-container');
                if (!chartContainer) {{
                    showError('Chart container element not found');
                    return;
                }}
                
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
                    }},
                    timeScale: {{
                        borderColor: '#2a2a2a',
                        timeVisible: true,
                        secondsVisible: false,
                    }},
                    width: chartContainer.clientWidth,
                    height: 600,
                }});
                
                // Create candlestick series
                if (typeof chart.addCandlestickSeries !== 'function') {{
                    showError('addCandlestickSeries method not available. Library version may be incompatible.');
                    console.error('Available chart methods:', Object.getOwnPropertyNames(chart));
                    return;
                }}
                
                candlestickSeries = chart.addCandlestickSeries({{
                    upColor: '#26a69a',
                    downColor: '#ef5350',
                    borderVisible: false,
                    wickUpColor: '#26a69a',
                    wickDownColor: '#ef5350',
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
                
                // Prepare data - ensure time is in seconds
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
                
                // Fit content
                chart.timeScale().fitContent();
                
                updateStatus('Chart ready - ' + chartData.length + ' bars loaded');
            }} catch (error) {{
                showError('Chart initialization error: ' + error.message);
                console.error('Full error:', error);
            }}
        }}
        
        // Wait for DOM and library to be ready
        if (document.readyState === 'loading') {{
            document.addEventListener('DOMContentLoaded', function() {{
                // Wait a bit for the script to load
                setTimeout(initChart, 100);
            }});
        }} else {{
            // DOM already loaded, wait for script
            setTimeout(initChart, 100);
        }}
        
        // Also try when window loads
        window.addEventListener('load', function() {{
            if (!chart) {{
                setTimeout(initChart, 100);
            }}
        }});
        
        // Refresh chart (placeholder)
        function refreshChart() {{
            updateStatus('Refresh not available in standalone mode. Re-run chart command to update.');
        }}
        
        // Export data to CSV
        function exportData() {{
            try {{
                const csv = [
                    ['Time', 'Open', 'High', 'Low', 'Close', 'Volume'].join(','),
                    ...chartData.map(bar => {{
                        const date = new Date(bar.time * 1000); // Convert seconds to milliseconds
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

