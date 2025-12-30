# Changelog

All notable changes to the Trading Bot system are documented here.

---

## [Unreleased]

### Planned Features
- Terminal logs widget in GUI
- Strategy signal feed widget
- Command input widget in GUI
- Enhanced strategy control with parameter inputs
- Phase 2 Rust migration (market data)
- Phase 3 Rust migration (strategy engine)

---

## [2.5.0] - 2025-12-29

### 🎉 Major Improvements

#### Fixed Rust Integration
- **FIXED**: Rust `cancel_order` now working (~5ms faster than Python)
  - Workaround for PyO3 0.20 parameter exposure bug
  - Added `set_account_id()` state-based approach
- **FIXED**: Re-enabled Rust `close_position` hot path (~20ms faster)
  - Removed explicit `if False` disable
  - Now uses Rust for position closing reliably

#### WebSocket Real-Time Updates
- **ADDED**: WebSocket backend endpoint at `/ws`
  - Client connection management
  - Broadcast function for push updates
  - Background loop broadcasting every 2 seconds
- **ADDED**: WebSocket frontend client
  - Auto-reconnect with exponential backoff
  - Graceful fallback to HTTP polling if WebSocket fails
  - Keep-alive ping/pong every 30 seconds
  - **20-40x lower network overhead** vs HTTP polling

#### GUI Enhancements
- **ADDED**: Collapsible widget system
  - Click panel headers to collapse/expand
  - localStorage persistence across sessions
  - Smooth CSS animations
- **ADDED**: Higher chart refresh rates (12x/s, 18x/s, 24x/s)
  - **4x faster** than previous max (6x/s)
- **ADDED**: More chart timeframes (2m, 3m, 10m, 30m, 2h, 4h, 1d)
  - **12 timeframes** total (was 5)
- **ADDED**: Stop order types
  - Stop Market
  - Stop Limit
  - Trailing Stop
  - **5 order types** total (was 2)

#### Symbol Resolution & Contract Cache
- **FIXED**: GUI symbol dropdown showing month codes instead of root symbols
  - Now correctly extracts `MNQ` from `CON.F.US.MNQ.H26`
  - Prioritizes contract ID parsing over free-text name fields
- **FIXED**: Contract cache initialization for GUI
  - Pre-loads contracts before GUI launch
  - Syncs `ContractManager` cache with `trading_bot` cache
  - Default account and symbol now correctly set in dropdowns

#### CLI Enhancements
- **ADDED**: `reduce_only` flag support for limit and stop orders
  - `--reduce-only` or `-r` flags
  - Properly passed through to API
- **ADDED**: `master` and `gui` commands in interactive CLI
  - Opens Master Control dashboard from CLI
  - Pre-loads contracts and account data

### 🐛 Bug Fixes

- Fixed `TypeError` in quote volume handling (handles `None` values)
- Fixed collapsible panels not fully collapsing (removed blank space)
- Fixed `'dict' object has no attribute 'raw_data'` in `get_linked_orders`
- Fixed position close retry logic with exponential backoff
- Fixed GUI command execution (`parse_and_execute` wrapper)
- Fixed `cancel_all` orders endpoint (proper `account_id` usage)
- Fixed account and symbol dropdown population
- Fixed `IndentationError` in `simple_candle_strategy.py`

### 📝 Documentation
- Created comprehensive documentation structure
- Added 10 main guides + API reference
- Consolidated 177+ markdown files into organized structure
- Added CHANGELOG.md (this file)
- Updated `.cursor/context_profile.json` with all recent fixes

### Performance
- **14+ Rust hot paths** now active (was 12)
- **20-40x lower** network overhead (WebSocket vs polling)
- **4x faster** chart updates (24x/s vs 6x/s)
- **Instant** UI updates (WebSocket push vs 3-5s polling delay)

---

## [2.4.0] - 2025-12-24

### Added
- Unified browser dashboard (Master Control)
  - All widgets on one page (no tabs)
  - Responsive CSS Grid layout
  - 12 API endpoints fully functional
- Execution paths documentation (`RUST_PYTHON_EXECUTION_PATHS.md`)
- JWT token management clarification (`JWT_TOKEN_VALIDATION_EXPLAINED.md`)

### Fixed
- Flatten command reliability
  - Now closes positions AND cancels orders correctly
  - Added verification step for position closes
  - Retry logic for API eventual consistency

### Changed
- Removed unnecessary `ensure_valid_token` calls
  - Only checks token expiration (< 1ms overhead)
  - Re-authentication only when token actually expires

---

## [2.3.0] - 2025-12-17

### Added
- Backtesting system
  - Real historical data integration
  - CSV data export
  - Monte Carlo simulation
  - Walk-forward analysis
- Trend scalping strategy
- Strategy monitoring and auto-adjustment

### Fixed
- Strategy executor validation
- Backtest data loading
- Test suite fixes

---

## [2.2.0] - 2025-12-12

### Added
- CLI-first architecture
- Enhanced command-line interface
- Bracket order improvements
- Automated trading setup guide

### Fixed
- Order placement issues
- Bracket order execution
- Strategy trading reliability

---

## [2.1.0] - 2025-12-05

### Added
- Initial Rust integration (Phase 1)
  - Order execution module
  - Query module for data fetching
  - PyO3 bindings
- Rust hot paths for:
  - `get_positions`
  - `get_open_orders`
  - `get_market_quote`
  - And 8+ more operations

### Performance
- 1.05-1.10x speedup for network-bound operations
- Automatic fallback to Python on errors

---

## [2.0.0] - 2025-12-02

### Added
- Real-time charting with TradingView Lightweight Charts
- SignalR WebSocket integration for market data
- Sub-minute timeframe support (30s, 45s, 1m)
- JWT auto-refresh mechanism
- Chart diagnostics and improvements

### Fixed
- SignalR authentication
- Chart data aggregation
- JWT token generation
- Sub-minute timeframe handling

---

## [1.5.0] - 2025-11-15

### Added
- Railway deployment support
- Frontend dashboard (React/TypeScript)
- PostgreSQL database integration
- Account selection and switching
- Trade consolidation (FIFO)

### Fixed
- Railway automation issues
- Frontend-backend integration
- Account selection bugs

---

## [1.0.0] - 2025-11-01

### Initial Release
- TopStepX API integration
- Basic trading operations (market, limit, stop orders)
- Position and order management
- Authentication system
- CLI interface
- Risk management
- Strategy framework
- Discord notifications

---

## Version Numbering

We use [Semantic Versioning](https://semver.org/):
- **Major** (X.0.0): Breaking changes
- **Minor** (0.X.0): New features, backward compatible
- **Patch** (0.0.X): Bug fixes, backward compatible

---

## Recent Session Summaries

For detailed session notes, see:
- `archive/fixes/SESSION_COMPLETE_DEC29.md` - Full Dec 29 session summary
- `archive/fixes/COMPLETE_IMPLEMENTATION_DEC29.md` - Dec 29 GUI enhancements
- `archive/fixes/GUI_CONTRACT_CACHE_FIX_DEC29.md` - Symbol resolution fix
- `archive/fixes/SYMBOL_EXTRACTION_FIX_DEC29.md` - Contract ID parsing fix

---

**Last Updated**: December 29, 2025

