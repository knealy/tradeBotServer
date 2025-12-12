---
name: Trading Bot Optimization and Architecture
overview: Optimize trading bot startup, fix order placement bugs, disable strategies, make strategies non-blocking, and create master/slave GUI architecture with updated context profile.
todos:
  - id: fix-order-placement
    content: Fix order placement bug in mean_reversion_strategy.py - change 'order' in result check to check for 'orderId' or 'success' field
    status: pending
  - id: disable-strategies
    content: Disable mean_reversion and trend_following strategies in persistent database state
    status: pending
  - id: optimize-startup
    content: Remove duplicate auth calls, optimize strategy initialization, reduce startup logging
    status: pending
  - id: create-strategy-executor
    content: Create core/strategy_executor.py as separate process/service for non-blocking strategy execution
    status: pending
  - id: add-strategy-controller
    content: Create core/strategy_controller.py API for controlling executor from CLI
    status: pending
  - id: master-gui-enhancement
    content: Extend frontend with master control panel and slave process management UI
    status: pending
  - id: database-process-states
    content: Add process_states and strategy_executions tables to database for slave tracking
    status: pending
  - id: slave-process-framework
    content: Create core/slave_process_base.py base class for slave processes
    status: pending
  - id: update-context-profile
    content: Update .cursor/.ai_context_profile.json with architecture priorities and code standards
    status: pending
---

# Trading Bot Optimization and CLI-First Architecture Plan

## 1. Startup Optimization & Authentication

### Issues Identified:

- **Duplicate authentication** (lines 15122-15126): Auth happens twice - once in `__main__` and once in `core.auth`
- **JWT token refresh not used during startup**: Should refresh token before account selection
- **Sequential initialization**: Some operations can be parallelized further
- **Redundant strategy initialization**: Strategies initialized multiple times during startup
- **Heavy logging**: Excessive debug logging during startup slows initialization

### Fixes:

- **CRITICAL**: Implement JWT token refresh mechanism during startup (before account selection)
  - Check token expiration on startup
  - Auto-refresh if expired or near expiration
  - Ensure valid token before any API calls
- Remove duplicate auth call in `core.auth.ensure_valid_token()` when token already loaded from env
- Optimize strategy manager to avoid re-initializing already-loaded strategies
- Reduce startup logging verbosity (move some INFO to DEBUG)
- Cache contract lookups more aggressively
- Lazy-load non-critical components

**Files to modify:**

- `core/auth.py` - Add startup token refresh, remove redundant auth check
- `trading_bot.py` - Call token refresh before account selection
- `strategies/strategy_manager.py` - Optimize initialization

---

## 2. Local Database Access & Strategy State Management

### Current Problem:

- Database only accessible on Railway server
- Need local PostgreSQL for development and testing
- Strategy states need to be manageable via CLI

### Solution: Local PostgreSQL Setup

- Use Railway's postgres-ssl Docker image for local development
- Create `docker-compose.yml` for easy local database setup
- Add database connection fallback: Railway → Local → Memory cache
- Environment variable `DATABASE_URL` should support both remote and local

### Strategy State Management:

- `mean_reversion` and `trend_following` are enabled in database (line 15182-15185)
- Need to set `enabled=False` in `strategy_states` table
- Add CLI command: `python trading_bot.py --disable_strategy=mean_reversion,trend_following`

**Files to create:**

- `docker-compose.yml` - Local PostgreSQL setup using Railway's postgres-ssl image
- `scripts/setup_local_db.sh` - Database initialization script

**Files to modify:**

- `infrastructure/database.py` - Add connection fallback logic
- `strategies/strategy_manager.py` - Add CLI disable method
- `.env.example` - Document local database setup

---

## 3. Fix Order Placement Logic - CRITICAL

### Critical Bug Found:

**File:** `strategies/mean_reversion_strategy.py` line 365

**Problem:**

```python
if result and 'order' in result:  # WRONG - 'order' key doesn't exist
    order_id = result['order'].get('orderId')
```

**Actual return structure from `create_bracket_order()`:**

```python
{
    "success": True,
    "orderId": result.order_id,  # orderId is at top level
    "message": result.message,
    ...
}
```

**Root Cause Analysis:**

- Orders are being signaled but not actually placed
- API response structure mismatch causes silent failures
- Need to verify orders actually appear in TopStepX after placement

**Fix:**

```python
if result and result.get('success') and result.get('orderId'):
    order_id = result.get('orderId')
    # VERIFY order was actually placed by checking TopStepX
    verification = await self.trading_bot.get_order_status(order_id)
    if verification:
        logger.info(f"✅ Order {order_id} verified in TopStepX")
    else:
        logger.error(f"❌ Order {order_id} not found in TopStepX - placement may have failed")
```

**Also check:** `strategies/trend_following_strategy.py` for similar issue

**Files to modify:**

- `strategies/mean_reversion_strategy.py` - Fix result checking (line 365) + add verification
- `strategies/trend_following_strategy.py` - Check and fix if needed
- `brokers/topstepx_adapter.py` - Add order verification method
- Add comprehensive error logging to show full API response when orders fail
- Add post-placement verification to ensure orders actually exist in TopStepX

---

## 4. CLI-First Architecture with Passthrough Parameters

### Core Philosophy:

- **Resource conservative**: Terminal-based, minimal overhead
- **Low latency**: Direct CLI commands, no HTTP overhead
- **Multi-window terminal**: Each bot runs in separate terminal window
- **Bash orchestration**: Scripts coordinate multiple bots
- **Passthrough parameters**: All commands accessible via CLI args

### Architecture:

```
bash script (orchestrator)
    ├─→ trading_bot.py --account_select=1 --command='stop_bracket mnq buy 1 25000 24980 25020'
    ├─→ strategy_executor.py --strategy=mean_reversion --symbols=MNQ,MES
    └─→ order_monitor.py --account_id=12694476
```

### Implementation:

**1. Passthrough Parameter System:**

- All CLI commands accessible via `--command='command_name arg1 arg2'`
- Account selection via `--account_select=N` (N = index or ID)
- Direct command execution: `python trading_bot.py --command='place_order MNQ BUY 1'`
- Non-interactive mode: `--non_interactive` flag for script usage

**2. Strategy Executor as Separate Process:**

- `strategy_executor.py` runs as standalone script
- Accepts CLI parameters: `--strategy=name --symbols=SYM1,SYM2 --account_id=ID`
- Communicates via:
  - **Primary**: Direct CLI commands from trading_bot.py
  - **Secondary**: Database state table (for status/control)
  - **Tertiary**: File-based IPC (fastest, lowest latency)

**3. Command Interface:**

```bash
# Direct command execution
python trading_bot.py --account_select=1 --command='stop_bracket mnq buy 1 25000 24980 25020'

# Strategy control
python trading_bot.py --command='strategy_start mean_reversion --symbols=MNQ,MES'
python trading_bot.py --command='strategy_stop mean_reversion'

# Strategy executor direct
python strategy_executor.py --strategy=mean_reversion --symbols=MNQ,MES --account_id=12694476

# Bash orchestration example
./run_strategies.sh  # Starts all bots in background
```

**New Files:**

- `core/strategy_executor.py` - Standalone strategy executor (CLI-based)
- `core/cli_command_parser.py` - Parse passthrough commands
- `scripts/run_strategies.sh` - Bash orchestrator example
- `scripts/stop_strategies.sh` - Stop all bots
- `scripts/monitor_bots.sh` - Monitor bot status

**Files to modify:**

- `trading_bot.py` - Add `argparse` for passthrough parameters, non-interactive mode
- `strategies/strategy_base.py` - Ensure strategies work in non-interactive mode
- All command handlers - Make them callable from CLI args

---

## 5. Terminal-Based Multi-Window System

### Architecture Philosophy:

- **No React frontend** - Focus on terminal-based interfaces
- **Multi-window terminals** - Each component in separate terminal
- **Bash orchestration** - Scripts coordinate everything
- **Resource conservative** - Minimal overhead, maximum performance
- **Low latency** - Direct CLI communication, no HTTP overhead

### Architecture:

```
Terminal Window 1: trading_bot.py (Master CLI)
    ├─→ Commands: place_order, get_positions, etc.
    └─→ Controls: strategy_executor via CLI commands

Terminal Window 2: strategy_executor.py (Strategy Bot)
    ├─→ Runs: mean_reversion, trend_following, overnight_range
    └─→ Reports: Status to database, logs to file

Terminal Window 3: order_monitor.py (Order Monitor)
    ├─→ Monitors: Open orders, fills, positions
    └─→ Alerts: Notifications on order events

Terminal Window 4: metrics_dashboard.py (Metrics Display)
    ├─→ Shows: P&L, win rate, strategy performance
    └─→ Updates: Real-time via database polling
```

### Implementation:

**1. Terminal-Based Interfaces:**

- Use `rich` or `blessed` library for terminal UI
- Color-coded output for different log levels
- Real-time updates via database polling (lightweight)
- Keyboard shortcuts for common commands

**2. Bash Orchestration Scripts:**

```bash
# scripts/start_all.sh
#!/bin/bash
# Start all bots in separate terminal windows/tmux panes

tmux new-session -d -s trading 'python trading_bot.py'
tmux split-window -h 'python strategy_executor.py'
tmux split-window -v 'python order_monitor.py'
tmux split-window -v 'python metrics_dashboard.py'
tmux attach -s trading
```

**3. Database State Management:**

- `process_states` table - Track bot status
- `strategy_executions` table - Log all strategy actions
- `order_events` table - Track order lifecycle
- Lightweight polling (1-5 second intervals)

**4. Communication:**

- **Primary**: CLI commands via subprocess/file IPC
- **Secondary**: Database state tables
- **Tertiary**: Signal files for immediate notifications

**New Files:**

- `core/strategy_executor.py` - Standalone strategy executor
- `core/order_monitor.py` - Order monitoring bot
- `core/metrics_dashboard.py` - Terminal-based metrics display
- `scripts/start_all.sh` - Start all bots
- `scripts/stop_all.sh` - Stop all bots
- `scripts/monitor_status.sh` - Status monitoring
- `core/terminal_ui.py` - Terminal UI utilities (using `rich`)

**Files to modify:**

- `infrastructure/database.py` - Add process state tables
- `trading_bot.py` - Add CLI command passthrough
- All strategy files - Ensure non-interactive compatibility

---

## 6. Context Profile Update

### Update `.cursor/.ai_context_profile.json` and `.cursor/context_profile.json`:

**Core Principles:**

1. **CLI-First Architecture:**

   - All functionality accessible via CLI
   - Passthrough parameters for script automation
   - Terminal-based interfaces preferred over web GUIs
   - Resource conservative, low latency

2. **Multi-Process Design:**

   - Each bot runs as separate process
   - Bash scripts orchestrate multiple bots
   - Database as shared state
   - File-based IPC for low-latency communication

3. **Code Quality Standards:**

   - All new files must include type hints
   - Async/await patterns for I/O operations
   - Comprehensive error handling with logging
   - Database transactions for state changes
   - Non-interactive mode support for all commands

4. **File Organization:**

   - `core/` - Core business logic
   - `strategies/` - Strategy implementations
   - `scripts/` - Bash orchestration scripts
   - `infrastructure/` - Database, caching, etc.
   - `servers/` - Optional API servers (not primary interface)

5. **Priority Focus:**

   - **Primary**: Low-latency automated trading
   - **Secondary**: Minimal intervention required
   - **Tertiary**: UI/UX improvements
   - **Avoid**: Heavy frameworks, unnecessary abstractions

6. **Testing Requirements:**

   - Unit tests for order placement logic
   - Integration tests for strategy execution
   - CLI command testing
   - Order verification (must appear in TopStepX)

**Files to modify:**

- `.cursor/.ai_context_profile.json` - Update with CLI-first principles
- `.cursor/context_profile.json` - Add architecture priorities

---

## Implementation Order

1. **Immediate (Critical Bugs):**

   - Fix order placement logic + verification (#3)
   - JWT token refresh during startup (#1)
   - Disable strategies in database (#2)

2. **Short-term (Infrastructure):**

   - Local database setup (#2)
   - CLI passthrough parameters (#4)
   - Strategy executor as standalone script (#4)

3. **Medium-term (Architecture):**

   - Terminal-based multi-window system (#5)
   - Bash orchestration scripts (#5)
   - Context profile update (#6)

4. **Long-term (Optimization):**

   - Performance tuning
   - Additional terminal UI enhancements
   - Advanced bash orchestration

---

## Testing Strategy

1. **Order Placement (CRITICAL):**

   - Test with real API (paper trading account)
   - **VERIFY orders actually appear in TopStepX** (not just API success)
   - Check error handling for failed orders
   - Test order verification logic

2. **Strategy Execution:**

   - Test strategy executor as standalone script
   - Verify orders are actually placed (not just signaled)
   - Test CLI command passthrough
   - Verify non-blocking behavior

3. **CLI & Orchestration:**

   - Test all commands via passthrough parameters
   - Test bash orchestration scripts
   - Verify multi-process communication
   - Test terminal UI components

4. **Database:**

   - Test local PostgreSQL setup
   - Verify connection fallback (Railway → Local → Memory)
   - Test strategy state persistence