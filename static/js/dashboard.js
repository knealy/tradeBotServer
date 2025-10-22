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
        this.marketDataInterval = null;
        this.charts = {};
        this.accounts = [];
        this.selectedAccount = null;
        this.demoModeEnabled = false;
        this.isLoading = false;
        
        this.init();
    }
    
    init() {
        this.setupEventListeners();
        // Skip WebSocket for Railway deployment - use HTTP polling only
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
        
        // Demo mode button
        const demoBtn = document.getElementById('demo-mode-btn');
        if (demoBtn) {
            demoBtn.addEventListener('click', () => {
                this.toggleDemoMode();
            });
        }
        
        // Tab switching
        document.querySelectorAll('[data-bs-toggle="tab"]').forEach(tab => {
            tab.addEventListener('shown.bs.tab', (e) => {
                const target = e.target.getAttribute('data-bs-target');
                console.log('Tab switched to:', target);
                
                if (target === '#positions') {
                    this.loadPositions();
                } else if (target === '#orders') {
                    this.loadOrders();
                } else if (target === '#history') {
                    this.loadTradeHistory();
                } else if (target === '#logs') {
                    this.loadSystemLogs();
                } else if (target === '#charts') {
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
        
        // Trading form
        document.getElementById('order-form').addEventListener('submit', (e) => {
            e.preventDefault();
            this.placeOrder();
        });
        
        // Order type change handler
        document.getElementById('order-type').addEventListener('change', (e) => {
            this.togglePriceFields(e.target.value);
        });
        
        // Symbol change handler for market data
        document.getElementById('order-symbol').addEventListener('change', (e) => {
            if (e.target.value) {
                this.loadMarketData(e.target.value);
                // Start auto-refresh for market data
                this.startMarketDataRefresh(e.target.value);
            } else {
                this.stopMarketDataRefresh();
            }
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
        
        // Try WebSocket on same port as dashboard (Railway compatible)
        const wsUrl = `${protocol}//${window.location.host}/ws/dashboard?token=${encodeURIComponent(token)}`;
        
        try {
            console.log('Attempting WebSocket connection to:', wsUrl);
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
                // Try fallback to port 8081
                this.tryFallbackWebSocket();
            };
        } catch (error) {
            console.error('Failed to connect WebSocket:', error);
            // Try fallback to port 8081
            this.tryFallbackWebSocket();
        }
    }
    
    tryFallbackWebSocket() {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const token = this.getAuthToken();
        
        if (!token) {
            this.connectSSE();
            return;
        }
        
        // Try WebSocket on port 8081 as fallback
        const wsUrl = `${protocol}//${window.location.hostname}:8081/ws/dashboard?token=${encodeURIComponent(token)}`;
        
        try {
            console.log('Trying fallback WebSocket connection to port 8081...');
            this.ws = new WebSocket(wsUrl);
            
            this.ws.onopen = () => {
                console.log('🚀 Fallback WebSocket connected to port 8081');
                this.updateConnectionStatus(true);
                this.isConnected = true;
                this.showAlert('Connected to real-time trading dashboard (fallback)', 'success');
                
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
                console.log('Fallback WebSocket disconnected:', event.code, event.reason);
                this.updateConnectionStatus(false);
                this.isConnected = false;
                
                // Attempt reconnection with exponential backoff
                if (event.code !== 1000) { // Not a normal closure
                    this.attemptReconnection();
                }
            };
            
            this.ws.onerror = (error) => {
                console.error('Fallback WebSocket error:', error);
                this.showAlert('WebSocket connection failed, using HTTP polling mode', 'warning');
                this.updateConnectionStatus(true);
                this.isConnected = true;
                // Don't try SSE, just use HTTP polling
                this.startPeriodicUpdates();
            };
        } catch (error) {
            console.error('Failed to connect fallback WebSocket:', error);
            this.connectSSE();
        }
    }
    
    connectSSE() {
        try {
            const token = this.getAuthToken();
            const sseUrl = `/api/stream?token=${encodeURIComponent(token)}`;
            this.eventSource = new EventSource(sseUrl);

            this.eventSource.onopen = () => {
                console.log('📡 SSE connected');
                this.updateConnectionStatus(true);
                this.isConnected = true;
                this.showAlert('Connected to real-time stream (SSE)', 'success');
            };

            this.eventSource.onerror = (e) => {
                console.warn('SSE error, closing stream and using HTTP polling', e);
                this.eventSource.close();
                this.updateConnectionStatus(true);
                this.isConnected = true;
            };

            const handler = (event) => {
                try {
                    const data = JSON.parse(event.data);
                    this.handleWebSocketMessage({ type: event.type, data });
                } catch (e) {
                    console.warn('Failed to parse SSE message', e);
                }
            };

            this.eventSource.addEventListener('account_update', handler);
            this.eventSource.addEventListener('position_update', handler);
            this.eventSource.addEventListener('order_update', handler);
            this.eventSource.addEventListener('trade_fill', handler);
            this.eventSource.addEventListener('stats_update', handler);
            this.eventSource.addEventListener('log_message', handler);
            this.eventSource.addEventListener('health_update', handler);
        } catch (e) {
            console.error('Failed to connect SSE:', e);
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
        // Prevent multiple simultaneous loads
        if (this.isLoading) {
            console.log('Already loading data, skipping...');
            return;
        }
        
        this.isLoading = true;
        
        try {
            // Check if we have a token first
            const token = this.getAuthToken();
            if (!token) {
                this.updateConnectionStatus(false);
                return;
            }
            
            // Load accounts first, then other data
            await this.loadAccounts();
            
            // Load data sequentially to avoid race conditions
            await this.loadAccountInfo();
            await this.loadPositions();
            await this.loadOrders();
            await this.loadTradeHistory();
            await this.loadPerformanceStats();
            await this.loadSystemLogs();
            
            this.updateConnectionStatus(true);
        } catch (error) {
            console.error('Failed to load initial data:', error);
            this.showAlert('Failed to load dashboard data', 'danger');
            this.updateConnectionStatus(false);
        } finally {
            this.isLoading = false;
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
        try {
            console.log('Loading positions...');
            const data = await this.apiCall('/positions');
            console.log('Positions data:', data);
            if (data) {
                this.updatePositionsTable(data);
                document.getElementById('positions-count').textContent = data.length;
                
                // Show demo data if no real data and demo mode is enabled
                if (data.length === 0 && this.demoModeEnabled) {
                    const demoPositions = [
                        {
                            id: 'demo-1',
                            symbol: 'NQ',
                            side: 'long',
                            quantity: 2,
                            entry_price: 18500.50,
                            current_price: 18525.75,
                            unrealized_pnl: 50.50,
                            stop_loss: 18450.00,
                            take_profit: 18600.00
                        }
                    ];
                    this.updatePositionsTable(demoPositions);
                    document.getElementById('positions-count').textContent = demoPositions.length;
                }
            }
        } catch (error) {
            console.error('Error loading positions:', error);
        }
    }
    
    // Demo mode - show sample data when no real data is available
    showDemoData() {
        // Show demo positions
        const demoPositions = [
            {
                id: 'demo-1',
                symbol: 'NQ',
                side: 'long',
                quantity: 2,
                entry_price: 18500.50,
                current_price: 18525.75,
                unrealized_pnl: 50.50,
                stop_loss: 18450.00,
                take_profit: 18600.00
            },
            {
                id: 'demo-2', 
                symbol: 'ES',
                side: 'short',
                quantity: 1,
                entry_price: 4550.25,
                current_price: 4545.00,
                unrealized_pnl: 26.25,
                stop_loss: 4560.00,
                take_profit: 4530.00
            }
        ];
        
        // Show demo history
        const demoHistory = [
            {
                id: 'demo-trade-1',
                symbol: 'MNQ',
                side: 'buy',
                quantity: 1,
                price: 18500.00,
                pnl: 25.00,
                timestamp: new Date(Date.now() - 3600000).toISOString(),
                status: 'filled'
            },
            {
                id: 'demo-trade-2',
                symbol: 'MES', 
                side: 'sell',
                quantity: 2,
                price: 4550.00,
                pnl: -15.00,
                timestamp: new Date(Date.now() - 7200000).toISOString(),
                status: 'filled'
            }
        ];
        
        // Update tables with demo data
        this.updatePositionsTable(demoPositions);
        this.updateHistoryTable(demoHistory);
        
        // Update counts
        document.getElementById('positions-count').textContent = demoPositions.length;
        
        // Show demo mode indicator
        this.showAlert('Demo Mode: Showing sample data. Real data will appear when you have active positions and trade history.', 'info');
    }
    
    toggleDemoMode() {
        this.demoModeEnabled = !this.demoModeEnabled;
        const demoBtn = document.getElementById('demo-mode-btn');
        
        if (this.demoModeEnabled) {
            this.showDemoData();
            demoBtn.innerHTML = '<i class="bi bi-stop-circle"></i> Exit Demo';
            demoBtn.className = 'btn btn-warning';
        } else {
            this.loadInitialData();
            demoBtn.innerHTML = '<i class="bi bi-play-circle"></i> Demo Mode';
            demoBtn.className = 'btn btn-info';
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
        try {
            console.log('Loading trade history...');
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
            console.log('History data:', data);
            if (data) {
                this.updateHistoryTable(data);
                
                // Show demo data if no real data and demo mode is enabled
                if (data.length === 0 && this.demoModeEnabled) {
                    const demoHistory = [
                        {
                            id: 'demo-trade-1',
                            symbol: 'MNQ',
                            side: 'buy',
                            quantity: 1,
                            price: 18500.00,
                            pnl: 25.00,
                            timestamp: new Date(Date.now() - 3600000).toISOString(),
                            status: 'filled'
                        }
                    ];
                    this.updateHistoryTable(demoHistory);
                }
            }
        } catch (error) {
            console.error('Error loading trade history:', error);
        }
    }
    
    async loadPerformanceStats() {
        const data = await this.apiCall('/stats');
        if (data && !data.error) {
            this.updatePerformanceStats(data);
        }
    }
    
    async loadSystemLogs() {
        try {
            console.log('Loading system logs...');
            const level = document.getElementById('log-level').value;
            let endpoint = '/logs';
            if (level) {
                endpoint += `?level=${level}`;
            }
            
            const data = await this.apiCall(endpoint);
            console.log('Logs data:', data);
            if (data) {
                this.updateLogsDisplay(data);
            }
        } catch (error) {
            console.error('Error loading system logs:', error);
        }
    }
    
    updatePositionsTable(positions) {
        console.log('Updating positions table with:', positions);
        const tbody = document.getElementById('positions-tbody');
        if (!tbody) {
            console.error('Positions tbody element not found');
            return;
        }
        tbody.innerHTML = '';
        
        if (!positions || positions.length === 0) {
            tbody.innerHTML = '<tr><td colspan="9" class="text-center text-muted"><i class="bi bi-info-circle me-2"></i>No open positions - Start trading to see positions here</td></tr>';
            return;
        }
        
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
        console.log('Updating history table with:', history);
        const tbody = document.getElementById('history-tbody');
        if (!tbody) {
            console.error('History tbody element not found');
            return;
        }
        tbody.innerHTML = '';
        
        if (!history || history.length === 0) {
            tbody.innerHTML = '<tr><td colspan="7" class="text-center text-muted"><i class="bi bi-info-circle me-2"></i>No trade history found - Complete some trades to see history here</td></tr>';
            return;
        }
        
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
        if (window.tradingCharts) {
            window.tradingCharts.updatePnLChart(stats);
            window.tradingCharts.updateWinRateChart(stats);
            window.tradingCharts.updateTradeDistributionChart(stats);
        }
        
        // Initialize charts with sample data if no real data
        if (!stats.total_pnl && window.tradingCharts) {
            const sampleStats = {
                total_pnl: 0,
                total_trades: 0,
                winning_trades: 0,
                win_rate: 0
            };
            window.tradingCharts.updatePnLChart(sampleStats);
            window.tradingCharts.updateWinRateChart(sampleStats);
            window.tradingCharts.updateTradeDistributionChart(sampleStats);
        }
    }
    
    updateLogsDisplay(logs) {
        console.log('Updating logs display with:', logs);
        const content = document.getElementById('logs-content');
        if (!content) {
            console.error('Logs content element not found');
            return;
        }
        
        if (!logs || logs.length === 0) {
            content.textContent = 'No logs available';
            return;
        }
        
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
        // Update data every 30 seconds for HTTP polling mode to reduce race conditions
        this.updateInterval = setInterval(() => {
            this.loadInitialData();
        }, 30000);
    }
    
    initCharts() {
        // Initialize charts when charts tab is activated
        if (typeof initCharts === 'function') {
            initCharts();
        }
    }
    
    async loadAccounts() {
        try {
            const data = await this.apiCall('/accounts');
            if (data && !data.error) {
                this.accounts = data;
                this.updateAccountDropdown();
                
                // Auto-select first account if none selected
                if (!this.selectedAccount && this.accounts.length > 0) {
                    this.switchAccount(this.accounts[0].id);
                }
            } else {
                this.showAlert('Failed to load accounts', 'warning');
            }
        } catch (error) {
            console.error('Failed to load accounts:', error);
            this.showAlert('Failed to load accounts', 'danger');
        }
    }
    
    updateAccountDropdown() {
        const dropdown = document.getElementById('account-dropdown');
        const currentAccountSpan = document.getElementById('current-account');
        
        // Clear existing options (except refresh button and divider)
        const refreshItem = dropdown.querySelector('a[onclick*="loadAccounts"]');
        const divider = dropdown.querySelector('hr');
        dropdown.innerHTML = '';
        
        // Add refresh button back
        if (refreshItem) {
            dropdown.appendChild(refreshItem);
        }
        if (divider) {
            dropdown.appendChild(divider);
        }
        
        // Add account options
        this.accounts.forEach(account => {
            const li = document.createElement('li');
            const a = document.createElement('a');
            a.className = 'dropdown-item';
            a.href = '#';
            a.innerHTML = `
                <div class="d-flex justify-content-between">
                    <div>
                        <strong>${account.name || account.id}</strong>
                        <br>
                        <small class="text-muted">${account.account_type || 'Trading Account'}</small>
                    </div>
                    <div class="text-end">
                        <small>$${account.balance?.toFixed(2) || '0.00'}</small>
                        <br>
                        <span class="badge bg-${account.status === 'active' ? 'success' : 'secondary'}">${account.status}</span>
                    </div>
                </div>
            `;
            a.onclick = (e) => {
                e.preventDefault();
                this.switchAccount(account.id);
            };
            li.appendChild(a);
            dropdown.appendChild(li);
        });
        
        // Update current account display
        if (this.selectedAccount) {
            const account = this.accounts.find(acc => acc.id === this.selectedAccount);
            if (account) {
                currentAccountSpan.textContent = account.name || account.id;
            }
        }
    }
    
    async switchAccount(accountId) {
        try {
            this.selectedAccount = accountId;
            this.updateAccountDropdown();
            
            // Switch account on backend
            const result = await this.apiCall(`/accounts/${accountId}/switch`, 'POST');
            if (result && !result.error) {
                this.showAlert(`Switched to account: ${this.accounts.find(acc => acc.id === accountId)?.name || accountId}`, 'success');
                
                // Reload all data for the new account
                await Promise.all([
                    this.loadAccountInfo(),
                    this.loadPositions(),
                    this.loadOrders(),
                    this.loadTradeHistory(),
                    this.loadPerformanceStats()
                ]);
            } else {
                this.showAlert(`Failed to switch account: ${result?.error || 'Unknown error'}`, 'danger');
            }
        } catch (error) {
            console.error('Failed to switch account:', error);
            this.showAlert(`Error switching account: ${error.message}`, 'danger');
        }
    }
    
    togglePriceFields(orderType) {
        const priceFields = document.getElementById('price-fields');
        const priceInput = document.getElementById('order-price');
        const stopPriceInput = document.getElementById('order-stop-price');
        
        if (orderType === 'limit' || orderType === 'stop_limit') {
            priceFields.style.display = 'block';
            priceInput.required = true;
        } else if (orderType === 'stop') {
            priceFields.style.display = 'block';
            priceInput.required = false;
            stopPriceInput.required = true;
        } else {
            priceFields.style.display = 'none';
            priceInput.required = false;
            stopPriceInput.required = false;
        }
    }
    
    async placeOrder() {
        const symbol = document.getElementById('order-symbol').value;
        const side = document.getElementById('order-side').value;
        const quantity = parseInt(document.getElementById('order-quantity').value);
        const orderType = document.getElementById('order-type').value;
        const price = parseFloat(document.getElementById('order-price').value) || null;
        const stopPrice = parseFloat(document.getElementById('order-stop-price').value) || null;
        const stopLossTicks = parseInt(document.getElementById('stop-loss-ticks').value) || null;
        const takeProfitTicks = parseInt(document.getElementById('take-profit-ticks').value) || null;
        
        if (!symbol || !side || !quantity) {
            this.showAlert('Please fill in all required fields', 'warning');
            return;
        }
        
        // Validate order type specific requirements
        if (orderType === 'limit' && !price) {
            this.showAlert('Price is required for limit orders', 'warning');
            return;
        }
        
        if (orderType === 'stop' && !stopPrice) {
            this.showAlert('Stop price is required for stop orders', 'warning');
            return;
        }
        
        if (orderType === 'stop_limit' && (!price || !stopPrice)) {
            this.showAlert('Both price and stop price are required for stop-limit orders', 'warning');
            return;
        }
        
        try {
            const orderData = {
                symbol,
                side,
                quantity,
                order_type: orderType,
                price,
                stop_price: stopPrice,
                stop_loss_ticks: stopLossTicks,
                take_profit_ticks: takeProfitTicks
            };
            
            console.log('Placing order:', orderData);
            
            const result = await this.apiCall('/orders/place', 'POST', orderData);
            if (result && !result.error) {
                this.showAlert(`Order placed successfully: ${result.order_id || 'Order ID not returned'}`, 'success');
                
                // Clear the form
                document.getElementById('order-form').reset();
                document.getElementById('price-fields').style.display = 'none';
                
                // Refresh data
                this.loadOrders();
                this.loadPositions();
            } else {
                this.showAlert(`Failed to place order: ${result?.error || 'Unknown error'}`, 'danger');
            }
        } catch (error) {
            console.error('Error placing order:', error);
            this.showAlert(`Error placing order: ${error.message}`, 'danger');
        }
    }
    
    async placeQuickOrder(side, quantity) {
        const symbol = document.getElementById('order-symbol').value;
        if (!symbol) {
            this.showAlert('Please select a symbol first', 'warning');
            return;
        }
        
        try {
            const orderData = {
                symbol,
                side,
                quantity,
                order_type: 'market'
            };
            
            const result = await this.apiCall('/orders/place', 'POST', orderData);
            if (result && !result.error) {
                this.showAlert(`Quick ${side} order placed for ${quantity} ${symbol}`, 'success');
                this.loadOrders();
                this.loadPositions();
            } else {
                this.showAlert(`Failed to place quick order: ${result?.error || 'Unknown error'}`, 'danger');
            }
        } catch (error) {
            console.error('Error placing quick order:', error);
            this.showAlert(`Error placing quick order: ${error.message}`, 'danger');
        }
    }
    
    async loadMarketData(symbol) {
        try {
            const data = await this.apiCall(`/market/${symbol}`);
            if (data && !data.error) {
                this.updateMarketDataDisplay(data);
            } else {
                document.getElementById('market-data').innerHTML = 
                    `<p class="text-muted">No market data available for ${symbol}</p>`;
            }
        } catch (error) {
            console.error('Error loading market data:', error);
            document.getElementById('market-data').innerHTML = 
                `<p class="text-danger">Error loading market data for ${symbol}</p>`;
        }
    }
    
    updateMarketDataDisplay(data) {
        const marketDataDiv = document.getElementById('market-data');
        
        // Calculate spread
        const spread = data.bid && data.ask ? (data.ask - data.bid).toFixed(2) : 'N/A';
        
        marketDataDiv.innerHTML = `
            <div class="row mb-2">
                <div class="col-6">
                    <h6 class="mb-1">Bid</h6>
                    <span class="badge bg-success fs-6">$${data.bid?.toFixed(2) || 'N/A'}</span>
                </div>
                <div class="col-6">
                    <h6 class="mb-1">Ask</h6>
                    <span class="badge bg-danger fs-6">$${data.ask?.toFixed(2) || 'N/A'}</span>
                </div>
            </div>
            <div class="row mb-2">
                <div class="col-6">
                    <h6 class="mb-1">Last</h6>
                    <span class="badge bg-primary fs-6">$${data.last?.toFixed(2) || 'N/A'}</span>
                </div>
                <div class="col-6">
                    <h6 class="mb-1">Spread</h6>
                    <span class="badge bg-info fs-6">$${spread}</span>
                </div>
            </div>
            <div class="row mb-2">
                <div class="col-6">
                    <h6 class="mb-1">High</h6>
                    <span class="text-success fw-bold">$${data.high?.toFixed(2) || 'N/A'}</span>
                </div>
                <div class="col-6">
                    <h6 class="mb-1">Low</h6>
                    <span class="text-danger fw-bold">$${data.low?.toFixed(2) || 'N/A'}</span>
                </div>
            </div>
            <div class="row">
                <div class="col-6">
                    <h6 class="mb-1">Volume</h6>
                    <span class="text-muted">${data.volume?.toLocaleString() || 'N/A'}</span>
                </div>
                <div class="col-6">
                    <h6 class="mb-1">Change</h6>
                    <span class="text-${data.change >= 0 ? 'success' : 'danger'}">${data.change >= 0 ? '+' : ''}${data.change?.toFixed(2) || 'N/A'}</span>
                </div>
            </div>
        `;
        
        // Update market data chart if it exists
        if (window.tradingCharts) {
            const symbol = document.getElementById('order-symbol').value;
            window.tradingCharts.updateMarketDataChart(symbol, data);
        }
    }
    
    startMarketDataRefresh(symbol) {
        this.stopMarketDataRefresh(); // Clear any existing interval
        
        // Refresh market data every 5 seconds
        this.marketDataInterval = setInterval(() => {
            this.loadMarketData(symbol);
        }, 5000);
    }
    
    stopMarketDataRefresh() {
        if (this.marketDataInterval) {
            clearInterval(this.marketDataInterval);
            this.marketDataInterval = null;
        }
    }
}

// Initialize dashboard when page loads
document.addEventListener('DOMContentLoaded', () => {
    window.dashboard = new TradingDashboard();
});
