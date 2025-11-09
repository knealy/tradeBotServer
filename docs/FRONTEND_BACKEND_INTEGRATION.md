# 🔌 Frontend-Backend Integration Guide

**Last Updated**: November 9, 2025  
**Status**: ✅ Complete

---

## 📋 Overview

The React frontend is now fully integrated with the Python backend through a comprehensive REST API. All dashboard features are connected to real trading bot data.

---

## 🚀 Quick Start

### **1. Install Backend Dependencies**

```bash
# Install new CORS dependency
pip install aiohttp-cors>=0.7.0
```

### **2. Start Backend Server**

```bash
# Start the async webhook server (includes dashboard API)
python servers/start_async_webhook.py
```

The server will run on **http://localhost:8080**

### **3. Start Frontend**

```bash
cd frontend
npm run dev
```

The dashboard will be at **http://localhost:3000**

---

## 🔌 API Endpoints

### **Account Management**

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/accounts` | Get all available accounts |
| `GET` | `/api/account/info` | Get current account info |
| `POST` | `/api/account/switch` | Switch to different account |

**Example:**
```typescript
// Get accounts
const accounts = await accountApi.getAccounts()

// Switch account
await accountApi.switchAccount('PRAC-V2-14334-56363256')
```

### **Positions**

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/positions` | Get open positions |
| `POST` | `/api/positions/{id}/close` | Close a position |
| `POST` | `/api/positions/flatten` | Flatten all positions |

**Example:**
```typescript
// Get positions
const positions = await positionApi.getPositions()

// Close position
await positionApi.closePosition('position-123')

// Flatten all
await positionApi.flattenAll()
```

### **Orders**

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/orders` | Get open orders |
| `POST` | `/api/orders/{id}/cancel` | Cancel an order |
| `POST` | `/api/orders/cancel-all` | Cancel all orders |
| `POST` | `/api/orders/place` | Place new order |

**Example:**
```typescript
// Get orders
const orders = await orderApi.getOrders()

// Place order
await orderApi.placeOrder({
  symbol: 'MNQ',
  side: 'BUY',
  quantity: 1,
  type: 'MARKET'
})
```

### **Strategies**

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/strategies` | Get all strategies |
| `GET` | `/api/strategies/status` | Get strategy status |
| `POST` | `/api/strategies/{name}/start` | Start a strategy |
| `POST` | `/api/strategies/{name}/stop` | Stop a strategy |

**Example:**
```typescript
// Get strategies
const strategies = await strategyApi.getStrategies()

// Start strategy
await strategyApi.startStrategy('overnight_range', ['MNQ', 'MES'])

// Stop strategy
await strategyApi.stopStrategy('overnight_range')
```

### **Metrics & Performance**

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/metrics` | Get performance metrics |
| `GET` | `/api/trades` | Get trade history |
| `GET` | `/api/performance` | Get performance stats |

**Example:**
```typescript
// Get metrics
const metrics = await metricsApi.getMetrics()

// Get trades
const trades = await tradeApi.getTrades('2025-11-01', '2025-11-09')

// Get performance
const performance = await tradeApi.getPerformance()
```

---

## 🔄 Real-Time Updates (WebSocket)

The frontend connects to the WebSocket server on port **8081** for real-time updates.

**WebSocket URL**: `ws://localhost:8081`

**Message Types:**
- `position_update` - Position changes
- `order_update` - Order status changes
- `account_update` - Account balance/P&L updates
- `trade_fill` - Trade fill notifications
- `strategy_update` - Strategy status changes

**Example:**
```typescript
// Connect WebSocket
wsService.connect()

// Listen for updates
wsService.on('position_update', (data) => {
  console.log('Position updated:', data)
})

wsService.on('trade_fill', (data) => {
  console.log('Trade filled:', data)
})
```

---

## 🎨 Frontend Components

### **Dashboard.tsx**
- Main dashboard view
- Fetches account info, positions, orders, metrics
- Auto-refreshes every 5-10 seconds
- WebSocket integration for real-time updates

### **AccountCard.tsx**
- Displays account balance, P&L, DLL status
- Shows account name and ID
- Real-time balance updates

### **PositionsOverview.tsx**
- Lists all open positions
- Shows entry price, current price, P&L
- Close position buttons
- Auto-refreshes every 5 seconds

### **MetricsCard.tsx**
- Performance metrics display
- API call statistics
- Cache hit rates
- System resource usage

### **PerformanceChart.tsx**
- P&L chart over time
- Trade history visualization
- Win rate display

---

## 🔧 Configuration

### **Backend (.env)**

```bash
# API credentials
PROJECT_X_API_KEY=your_key
PROJECT_X_USERNAME=your_username

# Server settings
WEBHOOK_HOST=0.0.0.0
WEBHOOK_PORT=8080

# Database (optional)
DATABASE_URL=postgresql://...
PUBLIC_DATABASE_URL=postgresql://...
```

### **Frontend (frontend/.env)**

```bash
VITE_API_URL=http://localhost:8080
VITE_WS_URL=ws://localhost:8081
```

---

## 🐛 Troubleshooting

### **CORS Errors**

If you see CORS errors in the browser console:

1. **Check CORS is enabled** in `async_webhook_server.py`:
   ```python
   cors = cors_setup(self.app, defaults={
       "*": ResourceOptions(...)
   })
   ```

2. **Verify backend is running** on port 8080:
   ```bash
   curl http://localhost:8080/health
   ```

3. **Check frontend .env** has correct API URL

### **WebSocket Connection Failed**

1. **Check WebSocket server is running**:
   ```bash
   # WebSocket runs on port 8081
   # Check if websocket_server.py is running
   ```

2. **Verify WebSocket URL** in `frontend/.env`:
   ```bash
   VITE_WS_URL=ws://localhost:8081
   ```

3. **Check browser console** for connection errors

### **API Endpoints Not Found (404)**

1. **Verify routes are registered** in `async_webhook_server.py`
2. **Check server logs** for route registration
3. **Test endpoint directly**:
   ```bash
   curl http://localhost:8080/api/accounts
   ```

### **Data Not Updating**

1. **Check auto-refresh intervals** in React components
2. **Verify WebSocket connection** is active
3. **Check browser network tab** for API calls
4. **Review server logs** for errors

---

## ✅ Integration Checklist

- [x] CORS support added to backend
- [x] All REST API endpoints implemented
- [x] Frontend API client created
- [x] WebSocket service implemented
- [x] Dashboard components connected
- [x] Real-time updates working
- [x] Error handling added
- [x] TypeScript types defined

---

## 🚀 Next Steps

1. **Add Authentication** (JWT tokens)
2. **Add Error Boundaries** in React
3. **Add Loading States** for better UX
4. **Add Toast Notifications** for actions
5. **Add Chart Library** for better visualization
6. **Add Strategy Configuration UI**
7. **Add Risk Management Dashboard**

---

## 📚 Related Documentation

- [Dashboard Setup Guide](./DASHBOARD_SETUP.md)
- [API Reference](./API_REFERENCE.md) (coming soon)
- [WebSocket Protocol](./WEBSOCKET_PROTOCOL.md) (coming soon)

---

**The frontend and backend are now fully integrated! 🎉**

