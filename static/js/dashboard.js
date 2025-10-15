/**
 * TopStepX Trading Dashboard JavaScript
 * Handles WebSocket connections, API calls, and UI updates
 */

class TradingDashboard {
    constructor() {
        this.ws = null;
        this.authToken = null;
        this.isConnected = false;
        this.updateInterval = null;
        this.charts = {};
        
        this.init();
    }
    
    init() {
        this.setupEventListeners();
        this.connectWebSocket();
        this.loadInitialData();
        this.startPeriodicUpdates();
    }
    
    setupEventListeners() {
        // Trading controls
        document.getElementById('flatten-all-btn').addEventListener('click', () => {
            this.flattenAllPositions();
        });
        
        document.getElementById('cancel-all-orders-btn').addEventListener('click', () => {
            this.cancelAllOrders();
        });
        
        document.getElementById('refresh-data-btn').addEventListener('click', () => {
            this.loadInitialData();
        });
        
        // Tab switching
        document.querySelectorAll('[data-bs-toggle="tab"]').forEach(tab => {
            tab.addEventListener('shown.bs.tab', (e) => {
                const target = e.target.getAttribute('data-bs-target');
                if (target === '#charts') {
                    this.initCharts();
                }
            });
        });
        
        // Date filters
        document.getElementById('start-date').addEventListener('change', () => {
            this.loadTradeHistory();
        });
        
        document.getElementById('end-date').addEventListener('change', () => {
            this.loadTradeHistory();
        });
        
        // Log level filter
        document.getElementById('log-level').addEventListener('change', () => {
            this.loadSystemLogs();
        });
        
        // Clear logs
        document.getElementById('clear-logs-btn').addEventListener('click', () => {
            this.clearLogs();
        });
    }
    
    connectWebSocket() {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const token = this.getAuthToken();
        
        if (!token) {
            console.log('No auth token available for WebSocket');
            this.updateConnectionStatus(false);
            return;
        }
        
        // Try WebSocket on port 8081 first, then fallback to same port
        const wsUrl = `${protocol}//${window.location.hostname}:8081/ws/dashboard?token=${encodeURIComponent(token)}`;
        
        try {
            this.ws = new WebSocket(wsUrl);
            
            this.ws.onopen = () => {
                console.log('🚀 WebSocket connected to professional trading dashboard');
                this.updateConnectionStatus(true);
                this.isConnected = true;
                this.showAlert('Connected to real-time trading dashboard', 'success');
                
                // Send subscription message
                this.ws.send(JSON.stringify({
                    action: 'subscribe',
                    payload: { types: ['all'] }
                }));
            };
            
            this.ws.onmessage = (event) => {
                try {
                    const data = JSON.parse(event.data);
                    this.handleWebSocketMessage(data);
                } catch (e) {
                    console.error('Failed to parse WebSocket message:', e);
                }
            };
            
            this.ws.onclose = (event) => {
                console.log('WebSocket disconnected:', event.code, event.reason);
                this.updateConnectionStatus(false);
                this.isConnected = false;
                
                // Attempt reconnection with exponential backoff
                if (event.code !== 1000) { // Not a normal closure
                    this.attemptReconnection();
                }
            };
            
            this.ws.onerror = (error) => {
                console.error('WebSocket error:', error);
                this.showAlert('WebSocket connection failed, falling back to HTTP polling', 'warning');
                this.updateConnectionStatus(true);
                this.isConnected = true;
            };
        } catch (error) {
            console.error('Failed to connect WebSocket:', error);
            this.showAlert('WebSocket not available, using HTTP polling mode', 'info');
            this.updateConnectionStatus(true);
            this.isConnected = true;
        }
    }
    
    attemptReconnection() {
        if (this.reconnectAttempts >= 5) {
            console.log('Max reconnection attempts reached, falling back to HTTP polling');
            this.showAlert('Real-time connection lost, using HTTP polling', 'warning');
            this.updateConnectionStatus(true);
            this.isConnected = true;
            return;
        }
        
        this.reconnectAttempts = (this.reconnectAttempts || 0) + 1;
        const delay = Math.min(1000 * Math.pow(2, this.reconnectAttempts), 30000);
        
        console.log(`Attempting to reconnect in ${delay}ms (attempt ${this.reconnectAttempts}/5)`);
        
        setTimeout(() => {
            if (!this.isConnected) {
                this.connectWebSocket();
            }
        }, delay);
    }
    
    handleWebSocketMessage(data) {
        switch (data.type) {
            case 'connected':
                console.log('✅ WebSocket connection confirmed');
                this.showAlert(`Connected to TopStepX Trading Dashboard at ${new Date().toLocaleTimeString()}`, 'success');
                break;
            case 'account_update':
                this.updateAccountInfo(data.data);
                console.log('💰 Account balance updated:', data.data.balance);
                break;
            case 'position_update':
                this.updatePositionsTable(data.data);
                console.log('📊 Positions updated:', data.data.length, 'positions');
                break;
            case 'order_update':
                this.updateOrdersTable(data.data);
                console.log('📋 Orders updated:', data.data.length, 'orders');
                break;
            case 'trade_fill':
                this.handleTradeFill(data.data);
                console.log('🎯 Trade filled:', data.data);
                break;
            case 'stats_update':
                this.updatePerformanceStats(data.data);
                console.log('📈 Performance stats updated');
                break;
            case 'log_message':
                this.handleLogMessage(data.data);
                break;
            case 'health_update':
                this.handleHealthUpdate(data.data);
                break;
            case 'subscription_confirmed':
                console.log('✅ Subscribed to real-time updates:', data.subscribed_to);
                break;
            case 'pong':
                // Respond to ping
                break;
            case 'auth_error':
                this.showAlert('WebSocket authentication failed', 'danger');
                break;
            case 'error':
                this.showAlert(`WebSocket error: ${data.message}`, 'warning');
                break;
            default:
                console.log('Unknown WebSocket message type:', data.type);
        }
        
        this.updateLastUpdateTime();
    }
    
    updateConnectionStatus(connected) {
        const statusElement = document.getElementById('connection-status');
        const icon = statusElement.querySelector('i');
        
        if (connected) {
            statusElement.innerHTML = '<i class="bi bi-circle-fill text-success"></i> Connected';
        } else {
            statusElement.innerHTML = '<i class="bi bi-circle-fill text-danger"></i> Disconnected';
        }
    }
    
    updateLastUpdateTime() {
        const now = new Date();
        document.getElementById('last-update').textContent = 
            `Last update: ${now.toLocaleTimeString()}`;
    }
    
    async loadInitialData() {
        try {
            // Check if we have a token first
            const token = this.getAuthToken();
            if (!token) {
                this.updateConnectionStatus(false);
                return;
            }
            
            await Promise.all([
                this.loadAccountInfo(),
                this.loadPositions(),
                this.loadOrders(),
                this.loadTradeHistory(),
                this.loadPerformanceStats(),
                this.loadSystemLogs()
            ]);
            
            this.updateConnectionStatus(true);
        } catch (error) {
            console.error('Failed to load initial data:', error);
            this.showAlert('Failed to load dashboard data', 'danger');
            this.updateConnectionStatus(false);
        }
    }
    
    async apiCall(endpoint, method = 'GET', data = null) {
        const url = `/api${endpoint}`;
        const options = {
            method,
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${this.getAuthToken()}`
            }
        };
        
        if (data) {
            options.body = JSON.stringify(data);
        }
        
        try {
            const response = await fetch(url, options);
            
            if (!response.ok) {
                if (response.status === 401) {
                    this.showAlert('Authentication required. Please set DASHBOARD_AUTH_TOKEN environment variable.', 'warning');
                    return null;
                }
                throw new Error(`HTTP ${response.status}: ${response.statusText}`);
            }
            
            return await response.json();
        } catch (error) {
            console.error(`API call failed: ${endpoint}`, error);
            throw error;
        }
    }
    
    getAuthToken() {
        // Get token from URL parameter or prompt user
        const urlParams = new URLSearchParams(window.location.search);
        let token = urlParams.get('token');
        
        if (!token) {
            token = prompt('Enter dashboard authentication token:');
            if (token) {
                // Update URL with token
                const newUrl = new URL(window.location);
                newUrl.searchParams.set('token', token);
                window.history.replaceState({}, '', newUrl);
            }
        }
        
        // If still no token, show error
        if (!token) {
            this.showAlert('Authentication token required. Please set DASHBOARD_AUTH_TOKEN environment variable.', 'warning');
            return null;
        }
        
        return token;
    }
    
    async loadAccountInfo() {
        const data = await this.apiCall('/account');
        if (data && !data.error) {
            document.getElementById('account-balance').textContent = 
                `$${data.balance?.toFixed(2) || '0.00'}`;
        }
    }
    
    async loadPositions() {
        const data = await this.apiCall('/positions');
        if (data) {
            this.updatePositionsTable(data);
            document.getElementById('positions-count').textContent = data.length;
        }
    }
    
    async loadOrders() {
        const data = await this.apiCall('/orders');
        if (data) {
            this.updateOrdersTable(data);
            document.getElementById('orders-count').textContent = data.length;
        }
    }
    
    async loadTradeHistory() {
        const startDate = document.getElementById('start-date').value;
        const endDate = document.getElementById('end-date').value;
        
        let endpoint = '/history';
        if (startDate || endDate) {
            const params = new URLSearchParams();
            if (startDate) params.append('start', startDate);
            if (endDate) params.append('end', endDate);
            endpoint += `?${params.toString()}`;
        }
        
        const data = await this.apiCall(endpoint);
        if (data) {
            this.updateHistoryTable(data);
        }
    }
    
    async loadPerformanceStats() {
        const data = await this.apiCall('/stats');
        if (data && !data.error) {
            this.updatePerformanceStats(data);
        }
    }
    
    async loadSystemLogs() {
        const level = document.getElementById('log-level').value;
        let endpoint = '/logs';
        if (level) {
            endpoint += `?level=${level}`;
        }
        
        const data = await this.apiCall(endpoint);
        if (data) {
            this.updateLogsDisplay(data);
        }
    }
    
    updatePositionsTable(positions) {
        const tbody = document.getElementById('positions-tbody');
        tbody.innerHTML = '';
        
        positions.forEach(position => {
            const row = document.createElement('tr');
            const pnl = position.unrealized_pnl || 0;
            const pnlClass = pnl > 0 ? 'positive' : pnl < 0 ? 'negative' : 'neutral';
            
            row.innerHTML = `
                <td>${position.symbol || 'N/A'}</td>
                <td><span class="badge bg-${position.side === 'long' ? 'success' : 'danger'}">${position.side || 'N/A'}</span></td>
                <td>${position.quantity || 'N/A'}</td>
                <td>$${position.entry_price?.toFixed(2) || 'N/A'}</td>
                <td>$${position.current_price?.toFixed(2) || 'N/A'}</td>
                <td class="${pnlClass}">$${pnl.toFixed(2)}</td>
                <td>$${position.stop_loss?.toFixed(2) || 'N/A'}</td>
                <td>$${position.take_profit?.toFixed(2) || 'N/A'}</td>
                <td>
                    <button class="btn btn-danger btn-sm" onclick="dashboard.closePosition('${position.id}')">
                        <i class="bi bi-x-circle"></i> Close
                    </button>
                </td>
            `;
            tbody.appendChild(row);
        });
    }
    
    updateOrdersTable(orders) {
        const tbody = document.getElementById('orders-tbody');
        tbody.innerHTML = '';
        
        orders.forEach(order => {
            const row = document.createElement('tr');
            
            row.innerHTML = `
                <td>${order.id || 'N/A'}</td>
                <td>${order.symbol || 'N/A'}</td>
                <td><span class="badge bg-${order.side === 'buy' ? 'success' : 'danger'}">${order.side || 'N/A'}</span></td>
                <td><span class="badge bg-info">${order.type || 'N/A'}</span></td>
                <td>${order.quantity || 'N/A'}</td>
                <td>$${order.price?.toFixed(2) || 'N/A'}</td>
                <td><span class="badge bg-warning">${order.status || 'N/A'}</span></td>
                <td>${order.timestamp ? new Date(order.timestamp).toLocaleString() : 'N/A'}</td>
                <td>
                    <button class="btn btn-warning btn-sm" onclick="dashboard.cancelOrder('${order.id}')">
                        <i class="bi bi-x-circle"></i> Cancel
                    </button>
                </td>
            `;
            tbody.appendChild(row);
        });
    }
    
    updateHistoryTable(history) {
        const tbody = document.getElementById('history-tbody');
        tbody.innerHTML = '';
        
        history.forEach(trade => {
            const row = document.createElement('tr');
            const pnl = trade.pnl || 0;
            const pnlClass = pnl > 0 ? 'positive' : pnl < 0 ? 'negative' : 'neutral';
            
            row.innerHTML = `
                <td>${trade.timestamp ? new Date(trade.timestamp).toLocaleString() : 'N/A'}</td>
                <td>${trade.symbol || 'N/A'}</td>
                <td><span class="badge bg-${trade.side === 'buy' ? 'success' : 'danger'}">${trade.side || 'N/A'}</span></td>
                <td>${trade.quantity || 'N/A'}</td>
                <td>$${trade.price?.toFixed(2) || 'N/A'}</td>
                <td class="${pnlClass}">$${pnl.toFixed(2)}</td>
                <td><span class="badge bg-${trade.status === 'filled' ? 'success' : 'warning'}">${trade.status || 'N/A'}</span></td>
            `;
            tbody.appendChild(row);
        });
    }
    
    updatePerformanceStats(stats) {
        // Update daily P&L
        document.getElementById('daily-pnl').textContent = 
            `$${stats.total_pnl?.toFixed(2) || '0.00'}`;
        
        // Update charts if they exist
        if (this.charts.winrate) {
            this.updateWinRateChart(stats);
        }
    }
    
    updateLogsDisplay(logs) {
        const content = document.getElementById('logs-content');
        const logText = logs.map(log => 
            `[${log.timestamp}] ${log.level}: ${log.message}`
        ).join('\n');
        
        content.textContent = logText;
        content.scrollTop = content.scrollHeight;
    }
    
    async closePosition(positionId) {
        if (!confirm('Are you sure you want to close this position?')) {
            return;
        }
        
        try {
            const result = await this.apiCall(`/position/${positionId}`, 'DELETE');
            if (result && !result.error) {
                this.showAlert('Position closed successfully', 'success');
                this.loadPositions();
            } else {
                this.showAlert(`Failed to close position: ${result?.error || 'Unknown error'}`, 'danger');
            }
        } catch (error) {
            this.showAlert(`Error closing position: ${error.message}`, 'danger');
        }
    }
    
    async cancelOrder(orderId) {
        if (!confirm('Are you sure you want to cancel this order?')) {
            return;
        }
        
        try {
            const result = await this.apiCall(`/order/${orderId}`, 'DELETE');
            if (result && !result.error) {
                this.showAlert('Order canceled successfully', 'success');
                this.loadOrders();
            } else {
                this.showAlert(`Failed to cancel order: ${result?.error || 'Unknown error'}`, 'danger');
            }
        } catch (error) {
            this.showAlert(`Error canceling order: ${error.message}`, 'danger');
        }
    }
    
    async flattenAllPositions() {
        if (!confirm('Are you sure you want to flatten ALL positions? This action cannot be undone.')) {
            return;
        }
        
        try {
            const result = await this.apiCall('/flatten', 'POST');
            if (result && !result.error) {
                this.showAlert('All positions flattened successfully', 'success');
                this.loadPositions();
            } else {
                this.showAlert(`Failed to flatten positions: ${result?.error || 'Unknown error'}`, 'danger');
            }
        } catch (error) {
            this.showAlert(`Error flattening positions: ${error.message}`, 'danger');
        }
    }
    
    async cancelAllOrders() {
        if (!confirm('Are you sure you want to cancel ALL orders?')) {
            return;
        }
        
        try {
            const result = await this.apiCall('/orders/all', 'DELETE');
            if (result && !result.error) {
                this.showAlert(`Canceled ${result.canceled} orders successfully`, 'success');
                this.loadOrders();
            } else {
                this.showAlert(`Failed to cancel orders: ${result?.error || 'Unknown error'}`, 'danger');
            }
        } catch (error) {
            this.showAlert(`Error canceling orders: ${error.message}`, 'danger');
        }
    }
    
    clearLogs() {
        document.getElementById('logs-content').textContent = 'Logs cleared';
    }
    
    handleTradeFill(tradeData) {
        // Show notification for trade fills
        this.showAlert(`Trade filled: ${tradeData.symbol} ${tradeData.side} ${tradeData.quantity} @ $${tradeData.price}`, 'success');
        
        // Refresh positions and orders
        this.loadPositions();
        this.loadOrders();
    }
    
    handleLogMessage(logData) {
        // Add log message to logs display
        const logsContent = document.getElementById('logs-content');
        const timestamp = new Date(logData.timestamp).toLocaleTimeString();
        const newLog = `[${timestamp}] ${logData.level}: ${logData.message}\n`;
        logsContent.textContent += newLog;
        logsContent.scrollTop = logsContent.scrollHeight;
    }
    
    handleHealthUpdate(healthData) {
        // Update connection status based on health
        if (healthData.status === 'healthy') {
            this.updateConnectionStatus(true);
        } else {
            this.updateConnectionStatus(false);
        }
    }
    
    showAlert(message, type) {
        // Create and show Bootstrap alert
        const alertDiv = document.createElement('div');
        alertDiv.className = `alert alert-${type} alert-dismissible fade show`;
        alertDiv.innerHTML = `
            ${message}
            <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
        `;
        
        // Insert at top of main content
        const container = document.querySelector('.container-fluid');
        container.insertBefore(alertDiv, container.firstChild);
        
        // Auto-dismiss after 5 seconds
        setTimeout(() => {
            if (alertDiv.parentNode) {
                alertDiv.remove();
            }
        }, 5000);
    }
    
    startPeriodicUpdates() {
        // Update data every 30 seconds
        this.updateInterval = setInterval(() => {
            if (this.isConnected) {
                this.loadInitialData();
            }
        }, 30000);
    }
    
    initCharts() {
        // Initialize charts when charts tab is activated
        if (typeof initCharts === 'function') {
            initCharts();
        }
    }
}

// Initialize dashboard when page loads
document.addEventListener('DOMContentLoaded', () => {
    window.dashboard = new TradingDashboard();
});
