"""
Backtest Report Generator - Creates HTML reports with charts and metrics.
"""

import json
from datetime import datetime
from typing import Dict, Any, List, Optional
from pathlib import Path
import logging
import json

from .models import BacktestResult

logger = logging.getLogger(__name__)


class BacktestReportGenerator:
    """Generate HTML reports from backtest results."""
    
    def __init__(self, output_dir: str = "backtest_reports"):
        """
        Initialize report generator.
        
        Args:
            output_dir: Directory to save reports
        """
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(exist_ok=True)
    
    def generate_html_report(self, result: BacktestResult, output_file: Optional[str] = None) -> str:
        """
        Generate HTML report with equity curve chart and metrics.
        
        Args:
            result: BacktestResult object
            output_file: Optional output file path
            
        Returns:
            Path to generated HTML file
        """
        if output_file is None:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            output_file = self.output_dir / f"backtest_{result.strategy_name}_{result.symbol}_{timestamp}.html"
        else:
            output_file = Path(output_file)
        
        # Prepare data for charts
        equity_data = [(int(ts.timestamp() * 1000), eq) for ts, eq in result.equity_curve]
        drawdown_data = [(int(ts.timestamp() * 1000), dd) for ts, dd in result.drawdown_curve]
        
        # Trade distribution data
        trade_pnls = [t.pnl for t in result.trades]
        win_pnls = [p for p in trade_pnls if p > 0]
        loss_pnls = [p for p in trade_pnls if p < 0]
        
        # Monthly P&L
        monthly_pnl = self._calculate_monthly_pnl(result.trades)
        
        html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Backtest Report: {result.strategy_name} - {result.symbol}</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js@3.9.1/dist/chart.min.js"></script>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
            margin: 0;
            padding: 20px;
            background: #f5f5f5;
        }}
        .container {{
            max-width: 1400px;
            margin: 0 auto;
            background: white;
            padding: 30px;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }}
        h1 {{
            color: #333;
            border-bottom: 3px solid #4CAF50;
            padding-bottom: 10px;
        }}
        .metrics-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 15px;
            margin: 20px 0;
        }}
        .metric-card {{
            background: #f9f9f9;
            padding: 15px;
            border-radius: 6px;
            border-left: 4px solid #4CAF50;
        }}
        .metric-label {{
            font-size: 12px;
            color: #666;
            text-transform: uppercase;
            margin-bottom: 5px;
        }}
        .metric-value {{
            font-size: 24px;
            font-weight: bold;
            color: #333;
        }}
        .metric-value.positive {{
            color: #4CAF50;
        }}
        .metric-value.negative {{
            color: #f44336;
        }}
        .chart-container {{
            margin: 30px 0;
            height: 400px;
            position: relative;
        }}
        .trades-table {{
            width: 100%;
            border-collapse: collapse;
            margin: 20px 0;
        }}
        .trades-table th {{
            background: #4CAF50;
            color: white;
            padding: 12px;
            text-align: left;
        }}
        .trades-table td {{
            padding: 10px;
            border-bottom: 1px solid #ddd;
        }}
        .trades-table tr:hover {{
            background: #f5f5f5;
        }}
        .pnl-positive {{
            color: #4CAF50;
            font-weight: bold;
        }}
        .pnl-negative {{
            color: #f44336;
            font-weight: bold;
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>📊 Backtest Report: {result.strategy_name}</h1>
        <p><strong>Symbol:</strong> {result.symbol} | <strong>Period:</strong> {result.start_date.strftime('%Y-%m-%d')} to {result.end_date.strftime('%Y-%m-%d')}</p>
        
        <div class="metrics-grid">
            <div class="metric-card">
                <div class="metric-label">Total Return</div>
                <div class="metric-value {'positive' if result.total_return_pct >= 0 else 'negative'}">
                    {result.total_return_pct:+.2f}%
                </div>
            </div>
            <div class="metric-card">
                <div class="metric-label">Total P&L</div>
                <div class="metric-value {'positive' if result.total_pnl >= 0 else 'negative'}">
                    ${result.total_pnl:,.2f}
                </div>
            </div>
            <div class="metric-card">
                <div class="metric-label">Win Rate</div>
                <div class="metric-value">
                    {result.win_rate:.1f}%
                </div>
            </div>
            <div class="metric-card">
                <div class="metric-label">Total Trades</div>
                <div class="metric-value">
                    {result.total_trades}
                </div>
            </div>
            <div class="metric-card">
                <div class="metric-label">Profit Factor</div>
                <div class="metric-value">
                    {result.profit_factor:.2f}
                </div>
            </div>
            <div class="metric-card">
                <div class="metric-label">Sharpe Ratio</div>
                <div class="metric-value">
                    {result.sharpe_ratio:.2f}
                </div>
            </div>
            <div class="metric-card">
                <div class="metric-label">Max Drawdown</div>
                <div class="metric-value negative">
                    {result.max_drawdown_pct:.2f}%
                </div>
            </div>
            <div class="metric-card">
                <div class="metric-label">Expectancy</div>
                <div class="metric-value {'positive' if result.expectancy >= 0 else 'negative'}">
                    ${result.expectancy:.2f}
                </div>
            </div>
        </div>
        
        <div class="chart-container">
            <h2>Equity Curve</h2>
            <canvas id="equityChart"></canvas>
        </div>
        
        <div class="chart-container">
            <h2>Drawdown</h2>
            <canvas id="drawdownChart"></canvas>
        </div>
        
        <div class="chart-container">
            <h2>Trade Distribution</h2>
            <canvas id="tradeChart"></canvas>
        </div>
        
        <h2>Trade History</h2>
        <table class="trades-table">
            <thead>
                <tr>
                    <th>Trade ID</th>
                    <th>Symbol</th>
                    <th>Side</th>
                    <th>Entry</th>
                    <th>Exit</th>
                    <th>Quantity</th>
                    <th>P&L</th>
                    <th>Duration</th>
                </tr>
            </thead>
            <tbody>
                {self._generate_trade_rows(result.trades)}
            </tbody>
        </table>
    </div>
    
    <script>
        // Equity Curve Chart
        const equityCtx = document.getElementById('equityChart').getContext('2d');
        new Chart(equityCtx, {{
            type: 'line',
            data: {{
                datasets: [{{
                    label: 'Equity',
                    data: {json.dumps(equity_data)},
                    borderColor: '#4CAF50',
                    backgroundColor: 'rgba(76, 175, 80, 0.1)',
                    fill: true
                }}]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                scales: {{
                    x: {{
                        type: 'time',
                        time: {{
                            unit: 'day'
                        }}
                    }},
                    y: {{
                        beginAtZero: false,
                        ticks: {{
                            callback: function(value) {{
                                return '$' + value.toLocaleString();
                            }}
                        }}
                    }}
                }}
            }}
        }});
        
        // Drawdown Chart
        const drawdownCtx = document.getElementById('drawdownChart').getContext('2d');
        new Chart(drawdownCtx, {{
            type: 'line',
            data: {{
                datasets: [{{
                    label: 'Drawdown %',
                    data: {json.dumps(drawdown_data)},
                    borderColor: '#f44336',
                    backgroundColor: 'rgba(244, 67, 54, 0.1)',
                    fill: true
                }}]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                scales: {{
                    x: {{
                        type: 'time',
                        time: {{
                            unit: 'day'
                        }}
                    }},
                    y: {{
                        beginAtZero: true,
                        ticks: {{
                            callback: function(value) {{
                                return value.toFixed(2) + '%';
                            }}
                        }}
                    }}
                }}
            }}
        }});
        
        // Trade Distribution Chart
        const tradeCtx = document.getElementById('tradeChart').getContext('2d');
        new Chart(tradeCtx, {{
            type: 'bar',
            data: {{
                labels: ['Wins', 'Losses'],
                datasets: [{{
                    label: 'P&L Distribution',
                    data: [{sum(win_pnls) if win_pnls else 0}, {sum(loss_pnls) if loss_pnls else 0}],
                    backgroundColor: ['#4CAF50', '#f44336']
                }}]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                scales: {{
                    y: {{
                        beginAtZero: true,
                        ticks: {{
                            callback: function(value) {{
                                return '$' + value.toLocaleString();
                            }}
                        }}
                    }}
                }}
            }}
        }});
    </script>
</body>
</html>"""
        
        output_file.write_text(html_content)
        logger.info(f"✅ Generated backtest report: {output_file}")
        return str(output_file)
    
    def _calculate_monthly_pnl(self, trades: List) -> Dict[str, float]:
        """Calculate monthly P&L breakdown."""
        monthly = {}
        for trade in trades:
            month_key = trade.entry_time.strftime('%Y-%m')
            monthly[month_key] = monthly.get(month_key, 0.0) + trade.pnl
        return monthly
    
    def _generate_trade_rows(self, trades: List) -> str:
        """Generate HTML table rows for trades."""
        rows = []
        for trade in trades[-100:]:  # Show last 100 trades
            pnl_class = 'pnl-positive' if trade.pnl >= 0 else 'pnl-negative'
            duration = (trade.exit_time - trade.entry_time).total_seconds() / 60  # minutes
            rows.append(f"""
                <tr>
                    <td>{trade.trade_id}</td>
                    <td>{trade.symbol}</td>
                    <td>{trade.side.value}</td>
                    <td>${trade.entry_price:.2f}</td>
                    <td>${trade.exit_price:.2f}</td>
                    <td>{trade.quantity}</td>
                    <td class="{pnl_class}">${trade.pnl:,.2f}</td>
                    <td>{duration:.1f}m</td>
                </tr>
            """)
        return ''.join(rows)

