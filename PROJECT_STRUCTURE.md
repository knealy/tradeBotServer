# 📁 Project Structure

**Last Updated**: November 9, 2025  
**Purpose**: Clean, modular organization for frontend-bridge-database-backend architecture

---

## 🎯 Overview

The project is organized into logical folders preparing for the next architecture phase:
- **Frontend** (future React dashboard)
- **Bridge/API** (servers folder)
- **Database** (infrastructure folder)
- **Backend** (core + strategies)

---

## 📂 Folder Structure

```
projectXbot/
│
├── 📄 trading_bot.py              # Main trading bot (entry point)
├── 📄 auth.py                     # Authentication module
├── 📄 load_env.py                 # Environment loader
├── 📄 README.md                   # Main documentation
├── 📄 requirements.txt            # Python dependencies
├── 📄 Procfile                    # Railway deployment config
├── 📄 railway.json                # Railway configuration
├── 📄 runtime.txt                 # Python version
├── 📄 setup_env.sh                # Environment setup script
│
├── 📁 strategies/                 # Trading Strategies
│   ├── __init__.py
│   ├── strategy_base.py           # Abstract base class
│   ├── strategy_manager.py        # Strategy coordinator
│   ├── overnight_range_strategy.py # Overnight range breakout
│   ├── mean_reversion_strategy.py  # Mean reversion trading
│   ├── trend_following_strategy.py # Trend following
│   └── mom_current.pine           # PineScript reference
│
├── 📁 core/                       # Core Bot Components
│   ├── __init__.py
│   ├── account_tracker.py         # Account state management
│   ├── discord_notifier.py        # Discord notifications
│   └── sdk_adapter.py             # ProjectX SDK adapter
│
├── 📁 infrastructure/             # Infrastructure & Utilities
│   ├── __init__.py
│   ├── database.py                # PostgreSQL integration
│   ├── performance_metrics.py     # Performance tracking
│   └── task_queue.py              # Priority task queue
│
├── 📁 servers/                    # API & Server Layer
│   ├── __init__.py
│   ├── webhook_server.py          # Synchronous webhook server
│   ├── async_webhook_server.py    # Async webhook server
│   ├── websocket_server.py        # WebSocket server
│   ├── dashboard.py               # Dashboard server
│   ├── start_webhook.py           # Webhook startup script
│   └── start_async_webhook.py     # Async webhook startup
│
├── 📁 profiles/                   # Reference & Configuration
│   ├── topstep_dev_profile.json   # Developer profile data
│   └── topstep_info_profile.md    # TopStepX information
│
├── 📁 docs/                       # Documentation
│   ├── START_HERE.md              # Navigation guide
│   ├── CURRENT_ARCHITECTURE.md    # System architecture
│   ├── COMPREHENSIVE_ROADMAP.md   # Project roadmap
│   ├── TESTING_GUIDE.md           # Testing procedures
│   ├── RECENT_CHANGES.md          # Change log
│   ├── OVERNIGHT_STRATEGY_GUIDE.md
│   ├── MODULAR_STRATEGY_GUIDE.md
│   ├── ENV_CONFIGURATION.md
│   ├── POSTGRESQL_SETUP.md
│   ├── TECH_STACK_ANALYSIS.md
│   └── ... (14 more docs)
│
├── 📁 tests/                      # Test Suite
│   ├── README.md
│   ├── test_auth.py
│   ├── test_api_data.py
│   ├── test_positions_stop_tp.py
│   ├── test_fixed_system.py
│   └── ... (20+ test files)
│
├── 📁 static/                     # Frontend Assets (future)
│   ├── dashboard.html
│   ├── css/
│   │   └── dashboard.css
│   └── js/
│       ├── dashboard.js
│       └── charts.js
│
└── 📁 .venv/                      # Virtual environment (local)
```

---

## 🔄 Module Dependencies

### **Import Structure**

```
trading_bot.py (main entry point)
├── core.account_tracker
├── core.discord_notifier
├── core.sdk_adapter (optional)
├── strategies.strategy_manager
│   ├── strategies.strategy_base
│   ├── strategies.overnight_range_strategy
│   ├── strategies.mean_reversion_strategy
│   └── strategies.trend_following_strategy
├── infrastructure.database
├── infrastructure.performance_metrics
└── infrastructure.task_queue

servers/async_webhook_server.py
├── trading_bot
├── infrastructure.task_queue
└── infrastructure.performance_metrics

servers/webhook_server.py
├── trading_bot
└── core.discord_notifier
```

---

## 📊 Component Breakdown

### **1. strategies/** (6 files)
**Purpose**: Trading logic and strategy coordination

| File | Purpose | Lines |
|------|---------|-------|
| `strategy_base.py` | Abstract base class, config | 400 |
| `strategy_manager.py` | Multi-strategy coordinator | 500 |
| `overnight_range_strategy.py` | Primary strategy | 1100 |
| `mean_reversion_strategy.py` | RSI-based strategy | 500 |
| `trend_following_strategy.py` | MA crossover strategy | 500 |
| `mom_current.pine` | PineScript reference | 1500 |

**Total**: ~4,500 lines

---

### **2. core/** (3 files)
**Purpose**: Core bot functionality

| File | Purpose | Lines |
|------|---------|-------|
| `account_tracker.py` | DLL/MLL tracking | 200 |
| `discord_notifier.py` | Trade notifications | 200 |
| `sdk_adapter.py` | ProjectX SDK wrapper | 150 |

**Total**: ~550 lines

---

### **3. infrastructure/** (3 files)
**Purpose**: Database, metrics, task management

| File | Purpose | Lines |
|------|---------|-------|
| `database.py` | PostgreSQL integration | 600 |
| `performance_metrics.py` | Performance tracking | 400 |
| `task_queue.py` | Priority task queue | 450 |

**Total**: ~1,450 lines

---

### **4. servers/** (6 files)
**Purpose**: API endpoints and server layer

| File | Purpose | Lines |
|------|---------|-------|
| `webhook_server.py` | Sync webhook server | 3100 |
| `async_webhook_server.py` | Async webhook server | 550 |
| `websocket_server.py` | WebSocket server | 350 |
| `dashboard.py` | Dashboard server | 320 |
| `start_webhook.py` | Webhook startup | 100 |
| `start_async_webhook.py` | Async startup | 100 |

**Total**: ~4,520 lines

---

### **5. docs/** (19 files)
**Purpose**: Complete documentation suite

- Architecture guides
- Testing procedures
- Strategy documentation
- Configuration references
- Roadmap and planning

**Total**: ~6,900 lines

---

### **6. tests/** (20+ files)
**Purpose**: Test coverage

- Unit tests
- Integration tests
- API tests
- Strategy tests

**Total**: ~2,000 lines

---

## 🎯 Design Principles

### **1. Separation of Concerns**
```
Strategies:       Business logic (what to trade)
Core:             Bot mechanics (how to trade)
Infrastructure:   Support systems (performance, data)
Servers:          API layer (external interface)
```

### **2. Dependency Flow**
```
Main Bot (trading_bot.py)
    ↓
Strategies (what to do)
    ↓
Core (how to do it)
    ↓
Infrastructure (support)
```

### **3. Future-Ready**
```
Current:
- Python monolith with organized folders

Future Phase 3 (Dashboard):
frontend/ (React)
    ↓
servers/ (API bridge)
    ↓
core/ + strategies/ (trading logic)
    ↓
infrastructure/ (database, metrics)

Future Phase 4 (Go Migration):
frontend/ (React)
    ↓
api_gateway/ (Go)
    ↓
trading_core/ (Go/Rust)
strategies/ (Python - pluggable)
    ↓
infrastructure/ (PostgreSQL, Redis)
```

---

## 🚀 Next Architecture Phase

### **Phase 3: Dashboard (1-2 months)**

**Additions**:
```
frontend/
├── src/
│   ├── components/
│   │   ├── Dashboard.tsx
│   │   ├── PositionCard.tsx
│   │   ├── StrategyControl.tsx
│   │   └── PerformanceChart.tsx
│   ├── hooks/
│   │   ├── useWebSocket.ts
│   │   └── useAPIClient.ts
│   └── App.tsx
├── public/
└── package.json

api/  (new REST API layer)
├── routes/
│   ├── positions.py
│   ├── strategies.py
│   └── metrics.py
└── middleware/
    ├── auth.py
    └── rate_limit.py
```

**Bridge Layer**:
- servers/ → Becomes API gateway
- WebSocket for real-time updates
- REST API for dashboard
- JWT authentication

---

### **Phase 4: Go Migration (2-3 months)**

**Core Components to Go**:
```
trading_core/ (Go)
├── cmd/
│   ├── trader/
│   └── api-gateway/
├── internal/
│   ├── orders/
│   ├── positions/
│   └── risk/
└── pkg/
    ├── topstepx/
    └── database/

strategies/ (Keep Python)
├── strategy_interface.py  ← Python-Go bridge
└── ... (existing strategies)
```

---

## 📈 Benefits of Current Organization

### **Development**
✅ Clear separation of concerns  
✅ Easy to navigate  
✅ Logical grouping  
✅ Future-proof structure  

### **Testing**
✅ Isolated components  
✅ Easy to mock  
✅ Clear dependencies  
✅ Testable modules  

### **Deployment**
✅ Railway-ready  
✅ Docker-friendly  
✅ Microservice-ready  
✅ Scalable architecture  

### **Collaboration**
✅ Self-documenting structure  
✅ Clear ownership boundaries  
✅ Easy onboarding  
✅ Modular contributions  

---

## 🔧 Import Examples

### **From Main Bot**
```python
# OLD (before organization)
from strategy_manager import StrategyManager
from discord_notifier import DiscordNotifier

# NEW (organized)
from strategies.strategy_manager import StrategyManager
from core.discord_notifier import DiscordNotifier
```

### **From Strategies**
```python
# OLD
from strategy_base import BaseStrategy

# NEW
from strategies.strategy_base import BaseStrategy
```

### **From Servers**
```python
# OLD
from task_queue import get_task_queue

# NEW
from infrastructure.task_queue import get_task_queue
```

---

## 📝 File Count Summary

```
Root:               8 files   (main entry points)
strategies/:        6 files   (~4,500 lines)
core/:              3 files   (~550 lines)
infrastructure/:    3 files   (~1,450 lines)
servers/:           6 files   (~4,520 lines)
profiles/:          2 files   (reference data)
docs/:             19 files   (~6,900 lines)
tests/:            20+ files  (~2,000 lines)
static/:            4 files   (dashboard assets)

Total Python Code:  ~14,000 lines
Total Docs:         ~6,900 lines
Total Project:      ~21,000 lines
```

---

## 🎯 Alignment with Future Goals

This structure aligns perfectly with:

1. **Frontend-Backend Separation**
   - Clear API layer (servers/)
   - Business logic isolated (strategies/ + core/)
   - Infrastructure separate (infrastructure/)

2. **Microservices Ready**
   - Each folder can become a service
   - Clear interfaces between layers
   - Easy to containerize

3. **Go/Rust Migration**
   - Strategy logic stays in Python (pluggable)
   - Core can be rewritten in Go
   - Infrastructure layer shared

4. **Team Collaboration**
   - Frontend devs → frontend/ + servers/
   - Strategy devs → strategies/
   - Platform devs → core/ + infrastructure/
   - DevOps → deployment configs

---

**This organization sets the foundation for scalable, maintainable growth!** 🚀

