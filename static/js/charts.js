/**
 * Advanced Charting for TopStepX Trading Dashboard
 * Provides comprehensive charting capabilities for P&L, performance metrics, and market data
 */

class TradingCharts {
    constructor() {
        this.charts = {};
        this.chartData = {
            pnl: [],
            winRate: [],
            trades: [],
            marketData: {}
        };
        this.updateInterval = null;
    }
    
    init() {
        this.initializeCharts();
        this.startDataCollection();
    }
    
    initializeCharts() {
        // P&L Over Time Chart
        this.initPnLChart();
        
        // Win Rate Chart
        this.initWinRateChart();
        
        // Trade Distribution Chart
        this.initTradeDistributionChart();
        
        // Market Data Chart
        this.initMarketDataChart();
    }
    
    initPnLChart() {
        const ctx = document.getElementById('pnl-chart');
        if (!ctx) return;
        
        this.charts.pnl = new Chart(ctx, {
            type: 'line',
            data: {
                labels: [],
                datasets: [{
                    label: 'P&L',
                    data: [],
                    borderColor: 'rgb(75, 192, 192)',
                    backgroundColor: 'rgba(75, 192, 192, 0.2)',
                    tension: 0.1,
                    fill: true
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    title: {
                        display: true,
                        text: 'P&L Over Time'
                    },
                    legend: {
                        display: false
                    }
                },
                scales: {
                    y: {
                        beginAtZero: false,
                        title: {
                            display: true,
                            text: 'P&L ($)'
                        }
                    },
                    x: {
                        title: {
                            display: true,
                            text: 'Time'
                        }
                    }
                },
                interaction: {
                    intersect: false,
                    mode: 'index'
                }
            }
        });
    }
    
    initWinRateChart() {
        const ctx = document.getElementById('winrate-chart');
        if (!ctx) return;
        
        this.charts.winRate = new Chart(ctx, {
            type: 'doughnut',
            data: {
                labels: ['Winning Trades', 'Losing Trades'],
                datasets: [{
                    data: [0, 0],
                    backgroundColor: [
                        'rgba(75, 192, 192, 0.8)',
                        'rgba(255, 99, 132, 0.8)'
                    ],
                    borderColor: [
                        'rgba(75, 192, 192, 1)',
                        'rgba(255, 99, 132, 1)'
                    ],
                    borderWidth: 2
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    title: {
                        display: true,
                        text: 'Win Rate'
                    },
                    legend: {
                        position: 'bottom'
                    }
                }
            }
        });
    }
    
    initTradeDistributionChart() {
        // Create a new chart for trade distribution if needed
        const container = document.getElementById('charts');
        if (!container) return;
        
        // Add trade distribution chart container
        const tradeChartDiv = document.createElement('div');
        tradeChartDiv.className = 'col-md-6';
        tradeChartDiv.innerHTML = `
            <div class="card">
                <div class="card-header">
                    <h5>Trade Distribution</h5>
                </div>
                <div class="card-body">
                    <canvas id="trade-distribution-chart" style="height: 300px;"></canvas>
                </div>
            </div>
        `;
        
        // Insert after the existing charts
        const existingCharts = container.querySelector('.row');
        if (existingCharts) {
            existingCharts.appendChild(tradeChartDiv);
        }
        
        // Initialize the chart
        setTimeout(() => {
            const ctx = document.getElementById('trade-distribution-chart');
            if (ctx) {
                this.charts.tradeDistribution = new Chart(ctx, {
                    type: 'bar',
                    data: {
                        labels: ['Wins', 'Losses'],
                        datasets: [{
                            label: 'Number of Trades',
                            data: [0, 0],
                            backgroundColor: [
                                'rgba(75, 192, 192, 0.8)',
                                'rgba(255, 99, 132, 0.8)'
                            ],
                            borderColor: [
                                'rgba(75, 192, 192, 1)',
                                'rgba(255, 99, 132, 1)'
                            ],
                            borderWidth: 1
                        }]
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        plugins: {
                            title: {
                                display: true,
                                text: 'Trade Distribution'
                            }
                        },
                        scales: {
                            y: {
                                beginAtZero: true,
                                title: {
                                    display: true,
                                    text: 'Number of Trades'
                                }
                            }
                        }
                    }
                });
            }
        }, 100);
    }
    
    initMarketDataChart() {
        // Market data chart for selected symbol
        const container = document.getElementById('market-data');
        if (!container) return;
        
        // Add market chart container
        const marketChartDiv = document.createElement('div');
        marketChartDiv.id = 'market-chart-container';
        marketChartDiv.innerHTML = `
            <div class="mt-3">
                <h6>Price Chart</h6>
                <canvas id="market-chart" style="height: 200px;"></canvas>
            </div>
        `;
        
        container.appendChild(marketChartDiv);
        
        // Initialize market chart
        setTimeout(() => {
            const ctx = document.getElementById('market-chart');
            if (ctx) {
                this.charts.marketData = new Chart(ctx, {
                    type: 'line',
                    data: {
                        labels: [],
                        datasets: [{
                            label: 'Price',
                            data: [],
                            borderColor: 'rgb(54, 162, 235)',
                            backgroundColor: 'rgba(54, 162, 235, 0.1)',
                            tension: 0.1,
                            fill: true
                        }]
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        plugins: {
                            title: {
                                display: false
                            },
                            legend: {
                                display: false
                            }
                        },
                        scales: {
                            y: {
                                title: {
                                    display: true,
                                    text: 'Price ($)'
                                }
                            },
                            x: {
                                title: {
                                    display: true,
                                    text: 'Time'
                                }
                            }
                        }
                    }
                });
            }
        }, 100);
    }
    
    updatePnLChart(data) {
        if (!this.charts.pnl) return;
        
        const now = new Date();
        const timeLabel = now.toLocaleTimeString();
        
        // Add new data point
        this.charts.pnl.data.labels.push(timeLabel);
        this.charts.pnl.data.datasets[0].data.push(data.total_pnl || 0);
        
        // Keep only last 50 data points
        if (this.charts.pnl.data.labels.length > 50) {
            this.charts.pnl.data.labels.shift();
            this.charts.pnl.data.datasets[0].data.shift();
        }
        
        this.charts.pnl.update('none');
    }
    
    updateWinRateChart(data) {
        if (!this.charts.winRate) return;
        
        const winningTrades = data.winning_trades || 0;
        const losingTrades = (data.total_trades || 0) - winningTrades;
        
        this.charts.winRate.data.datasets[0].data = [winningTrades, losingTrades];
        this.charts.winRate.update();
    }
    
    updateTradeDistributionChart(data) {
        if (!this.charts.tradeDistribution) return;
        
        const winningTrades = data.winning_trades || 0;
        const losingTrades = (data.total_trades || 0) - winningTrades;
        
        this.charts.tradeDistribution.data.datasets[0].data = [winningTrades, losingTrades];
        this.charts.tradeDistribution.update();
    }
    
    updateMarketDataChart(symbol, data) {
        if (!this.charts.marketData) return;
        
        const now = new Date();
        const timeLabel = now.toLocaleTimeString();
        const price = data.last || data.bid || 0;
        
        // Add new data point
        this.charts.marketData.data.labels.push(timeLabel);
        this.charts.marketData.data.datasets[0].data.push(price);
        
        // Keep only last 30 data points
        if (this.charts.marketData.data.labels.length > 30) {
            this.charts.marketData.data.labels.shift();
            this.charts.marketData.data.datasets[0].data.shift();
        }
        
        this.charts.marketData.update('none');
    }
    
    startDataCollection() {
        // Start collecting data every 5 seconds
        this.updateInterval = setInterval(() => {
            this.collectChartData();
        }, 5000);
    }
    
    async collectChartData() {
        try {
            // This would be called by the main dashboard to update charts
            // The actual data collection is handled by the dashboard's periodic updates
        } catch (error) {
            console.error('Error collecting chart data:', error);
        }
    }
    
    destroy() {
        if (this.updateInterval) {
            clearInterval(this.updateInterval);
        }
        
        Object.values(this.charts).forEach(chart => {
            if (chart && typeof chart.destroy === 'function') {
                chart.destroy();
            }
        });
    }
}

// Initialize charts when the charts tab is activated
function initCharts() {
    if (!window.tradingCharts) {
        window.tradingCharts = new TradingCharts();
        window.tradingCharts.init();
    }
}

// Export for use in dashboard
window.TradingCharts = TradingCharts;
window.initCharts = initCharts;