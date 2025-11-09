# TopStepX Trading Dashboard

Modern React + TypeScript dashboard for the TopStepX Trading Bot.

## 🚀 Quick Start

### Prerequisites

- Node.js 18+ and npm/yarn/pnpm
- Python backend running on `http://localhost:8080`

### Installation

```bash
# Install dependencies
npm install

# Start development server
npm run dev

# Build for production
npm run build
```

The dashboard will be available at `http://localhost:3000`

## 📁 Project Structure

```
frontend/
├── src/
│   ├── components/     # React components
│   ├── hooks/          # Custom React hooks
│   ├── services/       # API and WebSocket services
│   ├── types/          # TypeScript type definitions
│   └── utils/          # Utility functions
├── public/             # Static assets
└── package.json
```

## 🔌 Backend Integration

The dashboard connects to the Python backend via:
- **REST API**: `http://localhost:8080/api/*`
- **WebSocket**: `ws://localhost:8080/ws`

Configure in `.env`:
```
VITE_API_URL=http://localhost:8080
VITE_WS_URL=http://localhost:8080
```

## 🎨 Features

- ✅ Real-time account monitoring
- ✅ Position tracking
- ✅ Order management
- ✅ Strategy controls
- ✅ Performance metrics
- ✅ WebSocket updates

## 📦 Tech Stack

- **React 18** - UI framework
- **TypeScript** - Type safety
- **Vite** - Build tool
- **Tailwind CSS** - Styling
- **Recharts** - Charts
- **React Query** - Data fetching
- **Socket.io** - WebSocket client
- **Zustand** - State management

## 🚀 Deployment

Build the dashboard:
```bash
npm run build
```

Output goes to `../static/dashboard/` and can be served by the Python backend.

