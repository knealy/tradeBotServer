# 🎨 Dashboard Setup Guide

**Last Updated**: November 9, 2025  
**Purpose**: Set up and run the React + TypeScript trading dashboard

---

## 🚀 Quick Start

### **1. Install Dependencies**

```bash
cd frontend
npm install
```

### **2. Configure Environment**

Create `.env` file in `frontend/` directory:

```bash
# API Configuration
VITE_API_URL=http://localhost:8080
VITE_WS_URL=http://localhost:8080
```

### **3. Start Development Server**

```bash
npm run dev
```

Dashboard will be available at: **http://localhost:3000**

---

## 📋 Prerequisites

- **Node.js 18+** (check with `node --version`)
- **npm/yarn/pnpm** package manager
- **Python backend running** on port 8080

---

## 🏗️ Project Structure

```
frontend/
├── src/
│   ├── components/          # React components
│   │   ├── Dashboard.tsx    # Main dashboard view
│   │   ├── AccountCard.tsx  # Account display
│   │   ├── MetricsCard.tsx # Performance metrics
│   │   ├── PositionsOverview.tsx
│   │   └── PerformanceChart.tsx
│   ├── services/           # API & WebSocket
│   │   ├── api.ts          # REST API client
│   │   └── websocket.ts    # WebSocket service
│   ├── types/              # TypeScript types
│   │   └── index.ts
│   ├── App.tsx             # Root component
│   └── main.tsx            # Entry point
├── public/                 # Static assets
├── package.json
├── vite.config.ts          # Vite configuration
└── tailwind.config.js      # Tailwind CSS config
```

---

## 🔌 Backend Integration

### **API Endpoints**

The dashboard expects these endpoints from your Python backend:

```
GET  /api/accounts          # List all accounts
GET  /api/account/info       # Current account info
POST /api/account/switch     # Switch account
GET  /api/positions          # Get open positions
GET  /api/orders             # Get open orders
GET  /api/strategies         # List strategies
GET  /api/strategies/status  # Strategy status
POST /api/strategies/:name/start
POST /api/strategies/:name/stop
GET  /api/metrics            # Performance metrics
GET  /health                 # Health check
```

### **WebSocket Events**

```
Connection: ws://localhost:8080/ws

Events:
- account_update    # Account balance/status changes
- position_update   # Position changes
- order_update      # Order status changes
- metrics_update    # Performance metrics updates
```

---

## 🎨 Features

### **Current Components:**

1. **Dashboard**
   - Account overview
   - Real-time connection status
   - Performance metrics
   - Position tracking

2. **AccountCard**
   - Account balance
   - P&L display
   - Account status
   - Account selection

3. **MetricsCard**
   - System metrics (CPU, memory, uptime)
   - API call statistics
   - Cache performance
   - Error rates

4. **PositionsOverview**
   - Open positions list
   - Real-time P&L
   - Entry/current prices
   - Position details

5. **PerformanceChart**
   - P&L over time
   - Balance tracking
   - Interactive charts

---

## 🛠️ Development

### **Available Scripts**

```bash
# Start development server
npm run dev

# Build for production
npm run build

# Preview production build
npm run preview

# Type checking
npm run type-check

# Linting
npm run lint
```

### **Hot Reload**

Vite provides instant hot module replacement (HMR). Changes to components will update immediately in the browser.

---

## 📦 Tech Stack

| Technology | Purpose |
|------------|---------|
| **React 18** | UI framework |
| **TypeScript** | Type safety |
| **Vite** | Build tool & dev server |
| **Tailwind CSS** | Styling |
| **Recharts** | Chart library |
| **React Query** | Data fetching & caching |
| **Socket.io Client** | WebSocket communication |
| **Zustand** | State management (ready for use) |

---

## 🔧 Configuration

### **Vite Config**

Located in `vite.config.ts`:

- **Port**: 3000 (development)
- **Proxy**: `/api` → `http://localhost:8080`
- **Proxy**: `/ws` → `ws://localhost:8080`
- **Build output**: `../static/dashboard/`

### **Tailwind Config**

Custom colors and theme in `tailwind.config.js`:
- Primary color scheme
- Dark mode by default
- Custom spacing/typography

---

## 🚀 Production Build

### **Build Dashboard**

```bash
cd frontend
npm run build
```

Output goes to: `../static/dashboard/`

### **Serve with Python Backend**

The Python backend can serve the built dashboard from `static/dashboard/`.

Update your webhook server to serve static files:

```python
# In webhook_server.py or dashboard.py
from flask import send_from_directory

@app.route('/dashboard')
def dashboard():
    return send_from_directory('../static/dashboard', 'index.html')
```

---

## 🐛 Troubleshooting

### **Port Already in Use**

If port 3000 is taken:

```bash
# Edit vite.config.ts
server: {
  port: 3001,  # Change port
}
```

### **Backend Connection Issues**

1. **Check backend is running:**
   ```bash
   curl http://localhost:8080/health
   ```

2. **Check CORS settings** in Python backend
3. **Verify API endpoints** match expected paths
4. **Check WebSocket URL** in `.env`

### **TypeScript Errors**

```bash
# Run type checker
npm run type-check

# Fix common issues:
# - Missing types: npm install @types/package-name
# - Import errors: Check path aliases in tsconfig.json
```

### **Build Errors**

```bash
# Clear cache and rebuild
rm -rf node_modules dist
npm install
npm run build
```

---

## 📚 Next Steps

### **Immediate:**

1. ✅ Install dependencies
2. ✅ Start dev server
3. ✅ Connect to backend
4. ✅ Test basic functionality

### **Future Enhancements:**

- [ ] Add strategy control components
- [ ] Add order management UI
- [ ] Add risk monitoring dashboard
- [ ] Add historical data charts
- [ ] Add settings/configuration page
- [ ] Add authentication/login
- [ ] Add notifications/alerts
- [ ] Add export functionality

---

## 🔗 Related Documentation

- **Tech Stack Analysis**: `docs/TECH_STACK_ANALYSIS.md`
- **Project Structure**: `docs/PROJECT_STRUCTURE.md`
- **Backend API**: `servers/dashboard.py`
- **WebSocket Server**: `servers/websocket_server.py`

---

## ✅ Checklist

- [ ] Node.js 18+ installed
- [ ] Dependencies installed (`npm install`)
- [ ] `.env` file created with API URLs
- [ ] Python backend running on port 8080
- [ ] Development server started (`npm run dev`)
- [ ] Dashboard accessible at http://localhost:3000
- [ ] WebSocket connection established
- [ ] API calls working (check browser console)

---

**Ready to build an amazing trading dashboard!** 🚀

