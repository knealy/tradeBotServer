/**
 * Chart.js visualizations for TopStepX Trading Dashboard
 */

let pnlChart = null;
let winrateChart = null;

function initCharts() {
    initPnLChart();
    initWinRateChart();
}

function initPnLChart() {
    const ctx = document.getElementById('pnl-chart');
    if (!ctx) return;
    
    pnlChart = new Chart(ctx, {
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
                    grid: {
                        color: 'rgba(0, 0, 0, 0.1)'
                    }
                },
                x: {
                    grid: {
                        color: 'rgba(0, 0, 0, 0.1)'
                    }
                }
            },
            elements: {
                point: {
                    radius: 3,
                    hoverRadius: 6
                }
            }
        }
    });
}

function initWinRateChart() {
    const ctx = document.getElementById('winrate-chart');
    if (!ctx) return;
    
    winrateChart = new Chart(ctx, {
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

function updatePnLChart(data) {
    if (!pnlChart) return;
    
    // Generate sample data if no real data available
    const labels = [];
    const values = [];
    
    if (data && data.length > 0) {
        // Use real data
        data.forEach((point, index) => {
            labels.push(new Date(point.timestamp).toLocaleTimeString());
            values.push(point.pnl);
        });
    } else {
        // Generate sample data for demonstration
        const now = new Date();
        for (let i = 23; i >= 0; i--) {
            const time = new Date(now.getTime() - (i * 60 * 60 * 1000));
            labels.push(time.toLocaleTimeString());
            values.push(Math.random() * 200 - 100);
        }
    }
    
    pnlChart.data.labels = labels;
    pnlChart.data.datasets[0].data = values;
    pnlChart.update();
}

function updateWinRateChart(stats) {
    if (!winrateChart) return;
    
    const winningTrades = stats.winning_trades || 0;
    const losingTrades = stats.losing_trades || 0;
    
    winrateChart.data.datasets[0].data = [winningTrades, losingTrades];
    winrateChart.update();
}

function updateCharts(stats) {
    updateWinRateChart(stats);
    
    // For P&L chart, we'd need historical data
    // This would typically come from the trade history API
    updatePnLChart([]);
}

// Export functions for use in dashboard.js
window.initCharts = initCharts;
window.updateCharts = updateCharts;
window.updatePnLChart = updatePnLChart;
window.updateWinRateChart = updateWinRateChart;
