<!-- Last verified commit 4653935a859cc747bca1a8fcdf9a9cf3a8ce67bc -->
<!-- Last verified by: cursor-agent on 2026-05-12 — morning_range_reversion executor script + sieve/live parity; generic breakeven opt-in; drift monitor; income brain wiring; walk-forward doc gate (STRATEGY_DEVELOPMENT); legacy replay noise fixes. Prior HANDOFF note referenced ed3f8c1… (2026-05-05; historical). Next: PRAC shadow for morning_range before meta; drift-compare; partial TP broker; breakeven/slip validation; walk-forward before CANDIDATES; INCOME_BRAIN/DRIFT_MONITOR env gates. -->

## You are inheriting an autonomous TopStepX futures trading bot. It has been actively traded; do not break it.

This bot connects to the TopStepX (ProjectX) platform, receives real-time bar data via SignalR, evaluates strategy logic, and places/manages futures orders. It is run overnight on macOS (kept awake via `caffeinate`) and also deployed to Railway as a webhook+dashboard server. The codebase is a living system — changes to entrypoints, broker wiring, or strategy config must be validated against a live or paper account before merging.

---

## Mental model — lifecycle of a tick

```mermaid
sequenceDiagram
    participant SR as SignalR (TopStepX)
    participant WM as websocket_manager.py
    participant BA as bar_aggregator.py
    participant EB as event_bus.py
    participant ST as strategy.evaluate()
    participant OE as order_execution.py
    participant TA as topstepx_adapter.py
    participant UH as user_hub_manager.py

    SR->>WM: raw quote / bar message
    WM->>BA: aggregate tick into OHLCV bar
    BA->>EB: publish BarEvent
    EB->>ST: dispatch to subscribed strategy
    ST->>OE: signal (BUY/SELL/CLOSE)
    OE->>TA: REST call (place_order / cancel_order)
    TA-->>UH: SignalR fill / order-update event
    UH->>EB: publish FillEvent / OrderEvent
    EB->>ST: update strategy state (position, P&L)
```

Numbered narrative:
1. `core/websocket_manager.py` holds the market SignalR connection; reconnects automatically.
2. `core/bar_aggregator.py` converts raw ticks into closed OHLCV bars at the configured resolution.
3. Each closed bar is published onto `core/event_bus.py` as a `BarClosedEvent` (defined in `core/events.py`).
4. The active strategy's `evaluate()` method is called; it reads `StrategyConfig` for parameters.
5. If a signal is generated, `core/order_execution.py` validates risk limits then calls the adapter.
6. `brokers/topstepx_adapter.py` issues the REST request; TopStepX echoes the result on the user hub.
7. `core/user_hub_manager.py` receives fills/order-updates from the account SignalR hub and re-publishes them.
8. The strategy's state machine advances (flat → long/short → exit) based on fill events.

---

## Process layout

- One `core/strategy_executor.py` process per (strategy × account) pair.
- Each process instantiates one `TopStepXTradingBot` (`trading_bot.py`), which owns:
  - the SignalR connections (market hub + user hub)
  - `brokers/topstepx_adapter.py` (REST + auth token)
  - `infrastructure/database.py` pool (Postgres, `psycopg2.ThreadedConnectionPool`, 12 tables)
  - `core/risk_management.py` (global position limits, drawdown limits)
- Discord alerts are sent via `core/discord_notifier.py` on fills, errors, and session summaries.
- Multiple accounts: `scripts/start_multi_account.sh` spawns one executor process per account, each with its own `--account_id`.
- Railway deployment: `Procfile` → `web: python3 servers/start_async_webhook.py`; this process runs the dashboard API and webhook endpoints, not strategies.
- `core/slave_process_base.py` — base class for subprocess workers that communicate with the parent bot via a shared `process_states` Postgres row and signals.
- `scripts/monitor_positions.py` — standalone script that polls Postgres and prints live position/P&L; safe to run alongside a live process without interfering.
- **Backtest economics on disk:** `scripts/print_weekly_income.py` reads `core/backtest_executor.py --format=json` output and prints **per-week realized PnL** when `--include-trades` was used (otherwise a naive **average $/week** from `total_pnl` ÷ calendar weeks in `period`). Use it to sanity-check sweeps (e.g. Gate **A** MNQ full-window JSON) before trusting headline totals.
- **Gate A/B/C grid (body_reversion, full window):** `bash scripts/resume_body_rev_gate_ab_full.sh` runs **`scripts/run_body_rev_gate_ab_parallel.py`** — up to **`GATE_AB_JOBS`** concurrent subprocesses (default **3**, one per symbol), skipping cells whose output JSON is already **>200 bytes**. Same result files as the old sequential `run_full.sh`, lower wall-clock time on multi-core hosts.
- **Custom parallel backtest matrices:** `scripts/run_backtest_manifest.py` + JSONL under `config/backtest_matrices/` — arbitrary `backtest_executor` replay jobs with per-job `env` (strategy knobs), **`BACKTEST_MANIFEST_JOBS`** for worker count. **`HistoricalDataLoader.load_from_csv`** uses an in-process LRU (**`BACKTEST_CSV_CACHE`**, **`BACKTEST_CSV_CACHE_SIZE`**) so research loops that re-open the same CSV avoid repeated disk parse; replay bar lists are built with **`core.backtest.ohlcv.replay_bars_from_ohlcv_df`** (faster than `iterrows`). See **`docs/BACKTESTING.md`** § Speed.
- Strategy processes do not share memory. Each reads its own `strategy_states` Postgres row on startup to restore state after a restart.
- `core/session_trade_tracker.py` — tracks within-session trade count and P&L to enforce session-level limits independently of the global risk manager.

---

## Code map at a glance

`trading_bot.py` (10 k lines) is the integration hub — `TopStepXTradingBot` wires together auth, broker, websockets, event bus, and strategy management. Until decomposition happens, most cross-cutting concerns live there. The `strategies/` directory contains pure strategy logic (subclasses of `strategies/strategy_base.BaseStrategy`). The `core/` directory contains all shared infrastructure. The `brokers/` directory is the broker-specific adapter. `infrastructure/` holds Postgres persistence. `servers/` holds async HTTP endpoints. `gui/` holds the browser dashboard. See [docs/MAP.md](MAP.md) for the full annotated tree.

---

## Core modules quick-reference

| Module | Lines | Responsibility |
|--------|-------|----------------|
| `trading_bot.py` | ~10 500 | `TopStepXTradingBot` — auth, connection lifecycle, strategy orchestration, CLI |
| `brokers/topstepx_adapter.py` | ~5 500 | All TopStepX REST calls (orders, positions, account); SignalR negotiation |
| `gui/chart_html.py` | ~6 300 | Serves `gui/master_control.html`; generates the browser dashboard payload |
| `core/websocket_manager.py` | — | Market hub: receives quotes and bars from TopStepX SignalR |
| `core/user_hub_manager.py` | — | Account hub: receives order updates, fills, position changes |
| `core/event_bus.py` | — | In-process async pub/sub; `publish(event)` / `subscribe(EventType, handler)` |
| `core/events.py` | — | All event dataclasses (`BarClosedEvent`, `FillEvent`, `OrderEvent`, `PositionEvent`, …) |
| `core/order_execution.py` | — | Order validation, risk checks, broker dispatch; wraps `topstepx_adapter` |
| `core/risk_management.py` | — | Max position size, daily loss limit, drawdown halt |
| `core/bar_aggregator.py` | — | Converts raw tick stream to closed OHLCV bars at configured resolution |
| `core/strategy_config.py` | — | `StrategyConfig`: TOML load, env overlay, hot reload via `maybe_reload()` |
| `core/auth.py` | — | JWT acquisition and automatic refresh before expiry |
| `core/state_cache.py` | — | In-memory position + order state; rebuilt from Postgres + API on startup |
| `core/order_audit.py` | — | Logs all order lifecycle events; source of truth for "fill that didn't happen" |
| `core/discord_notifier.py` | — | Discord webhook/bot client; queued in `notifications` table for retry |
| `core/rate_limiter.py` | — | Token-bucket rate limiter for REST calls; respects `API_RATE_LIMIT_*` env vars |
| `core/performance_timer.py` | — | Context-manager timing util; used on hot paths (tick processing, order round-trip) |
| `infrastructure/database.py` | — | Postgres pool; `DatabaseManager` owns schema creation and all SQL |
| `infrastructure/performance_metrics.py` | — | Writes latency and throughput metrics to `api_metrics` table |
| `core/strategy_executor.py` | — | Process entry point; parses args, wires bot + strategy, runs event loop |
| `servers/start_async_webhook.py` | — | Production ASGI entry point for Railway (webhook + dashboard API) |
| `servers/dashboard.py` | — | FastAPI/aiohttp route handlers for the dashboard REST API |

---

## How configuration flows

Priority (highest to lowest):
```
CLI argument  >  environment variable  >  config/strategies/<name>.toml  >  class default
```

- `.env` (never committed) sets broker credentials, DB URL, Discord webhook, account IDs.
- `config/strategies/<name>.toml` sets strategy-specific tuning params (e.g., ATR multipliers, target ticks).
- `core/strategy_config.py` exposes `StrategyConfig` — use `StrategyConfig.get("param_name")` in strategies.
- Hot reload: call `StrategyConfig.maybe_reload()` at the top of each `evaluate()` call to pick up TOML edits without restarting.
- Do NOT call `os.getenv` inside `strategies/*`. The `.env`-vs-TOML split is deliberate: credentials stay in env, trading params stay in TOML so they are version-controlled and diff-able.

Example: `config/strategies/overnight_range.toml` holds `atr_period`, `risk_per_trade`, `target_ticks`, etc. Overriding at launch: `scripts/run_overnight.sh 3 -- --atr_period=20`.

---

## Adding a new strategy

1. Subclass `strategies.strategy_base.BaseStrategy`. Implement `evaluate(bar)`, `on_fill(event)`, and `reset()`.
2. Read all tunable parameters via `StrategyConfig` — no bare `os.getenv` calls.
3. Create `config/strategies/<name>.toml`. Use `config/strategies/_schema.toml` as the reference for required keys.
4. Register the strategy class in `strategies/strategy_manager.py` (`STRATEGY_REGISTRY` dict).
5. Smoke-test: `python core/strategy_executor.py --strategy=<name> --account_select=N --dry_run`.
6. Add a section to `docs/CHANGELOG.md` under `[Unreleased]`.

---

## Adding a new broker / venue

1. Create `brokers/<venue>_adapter.py`. Implement the interfaces in `core/interfaces/`:
   - `core/interfaces/order_interface.py` — `place_order`, `cancel_order`, `modify_order`
   - `core/interfaces/position_interface.py` — `get_positions`, `close_position`
   - `core/interfaces/market_data_interface.py` — `subscribe_bars`, `get_historical`
2. Wire the adapter into `TopStepXTradingBot.__init__` (or a new bot subclass) in `trading_bot.py`.
3. If the venue uses a different auth scheme, add credential keys to `.env.example` (not `.env`).
4. Add a TOML schema for venue-specific config if needed.

---

## Operating notes

- **macOS overnight runs**: `scripts/run_overnight.sh` wraps the process in `caffeinate -dimsu` to prevent sleep. This is expected and intentional.
- **Log locations**: `logs/` directory at repo root. One rotating file per session (`trading_bot.log`, plus per-strategy files like `overnight_range_account3_<timestamp>.log`). `RotatingFileHandler`: 10 MB × 5 backups, file level = INFO, console level = WARNING.
- **Log drain / flatten**: `logs/` can fill quickly; manually remove old `*.log` files or adjust `ROTATING_BACKUP_COUNT` in `core/logging_setup.py`. If a file named `logs` (not a directory) exists at repo root, `scripts/run_overnight.sh` renames it automatically before creating the directory.
- **Switching accounts**: change `TOPSTEPX_ACCOUNT_ID` in `.env` or pass `--account_select=<N>` (1-based index into the account list returned by the API). `scripts/start_multi_account.sh` iterates all configured account numbers.
- **Rust hotpath**: optional. Set `TOPSTEPX_USE_RUST=1` to enable. Only ~3 of the ~10 execution paths are wired to Rust (`trading_bot.py:344`). Build with `scripts/build.sh` or `scripts/rebuild_rust.sh`. Toggle at runtime with `scripts/toggle_rust.sh`.
- **Auth token**: JWT, refreshed automatically by `core/auth.py`. If you see 401s, the token cycle may be misaligned with the SignalR reconnect — check `trading_bot.log` for `token refresh` lines.

---

## Environment variables reference

All variables are documented in [`.env.example`](../.env.example). Critical ones:

| Variable | Purpose | Notes |
|----------|---------|-------|
| `PROJECT_X_API_KEY` | TopStepX REST auth | Aliased from `TOPSETPX_API_KEY` (typo fallback, `trading_bot.py:180`) |
| `PROJECT_X_USERNAME` | TopStepX login | Aliased from `TOPSETPX_USERNAME` (`trading_bot.py:181`) |
| `PROJECT_X_ACCOUNT_ID` | Default account | Override per-process with `--account_id=` |
| `JWT_TOKEN` | Pre-set JWT (optional) | `core/auth.py` refreshes it automatically; leave blank to let auth handle it |
| `DATABASE_URL` | Postgres connection string | `postgresql://user:pass@host:5432/db`; required for persistence |
| `DISCORD_WEBHOOK_URL` | Discord alert channel | Leave blank to disable Discord alerts |
| `DISCORD_STATUS_INTERVAL_SECONDS` | Periodic status digest to webhook | `0` = off; e.g. `900` for every 15 minutes (`trading_bot` / `strategy_executor`) |
| `TOPSTEPX_USE_RUST` | Enable Rust hotpath | `false` by default; set `1`/`true` only after building `rust/` |
| `DAILY_LOSS_LIMIT` | Global drawdown limit | Enforced in `core/risk_management.py`; overrides TOML |
| `INITIAL_BALANCE` | Account balance for risk sizing | Used in position sizing math |
| `LOG_LEVEL` | Root log level | `INFO` default; set `DEBUG` for verbose output locally only |
| `STRATEGY_TIMEZONE` | Timezone for session windows | Defaults to `US/Eastern`; affects overnight range cutoffs |
| `ENABLE_SIGNALR` | Toggle SignalR connections | Set `false` for offline testing / backtesting only |

Do not add new env-var reads directly in strategy code — expose them through `core/strategy_config.py`.

---

## Database schema overview

`infrastructure/database.py` creates all tables on startup (`CREATE TABLE IF NOT EXISTS`). The 12 tables:

| Table | Purpose |
|-------|---------|
| `historical_bars` | Cached OHLCV bars fetched from the API; keyed by symbol + timeframe + timestamp |
| `account_state` | Current account snapshot (balance, buying power, drawdown) per account ID |
| `strategy_performance` | Cumulative P&L, trade count, win rate per strategy per session |
| `api_metrics` | REST call latency and error rate tracking for the adapter |
| `trade_history` | Every completed round-trip trade (entry, exit, P&L, strategy tag) |
| `order_history_cache` | Raw order records from the exchange, keyed by order ID |
| `cache_metadata` | TTL and format metadata for the historical bar cache |
| `strategy_states` | Serialized strategy state (position, mode, last signal) — survives restarts |
| `process_states` | Executor PID/heartbeat; `metadata` includes `strategies` and, for `overnight_range`, live **`or_ranges`** so the Master chart (separate process) can draw session highs/lows without reading the executor’s in-memory `active_ranges` |
| `strategy_executions` | Per-execution log: which strategy ran, on which account, for how long |
| `dashboard_settings` | Persisted GUI preferences (visible symbols, chart range, etc.) |
| `notifications` | Discord / alert queue for retries and deduplication |

Migrations: there is no migration framework. Schema changes must be backward-compatible `ALTER TABLE … ADD COLUMN IF NOT EXISTS` statements run manually or added to `DatabaseManager.initialize_database()`.

---

## Shared utilities worth knowing

- `core/trend_detector.py` — reusable trend/chop classifier; strategies call it to gate signals. Documented in `docs/TREND_DETECTOR_USAGE.md`.
- `core/market_data.py` — wrapper around the adapter's historical fetch + cache layer; prefer this over calling the adapter directly.
- `core/sdk_adapter.py` — thin shim that routes calls to either the Rust extension or the Python implementation depending on `TOPSTEPX_USE_RUST`.
- `core/account_tracker.py` — lightweight wrapper around `account_state` Postgres table; updated on every fill event.
- `core/cli_command_parser.py` — parses runtime CLI commands sent to a running bot process via stdin or Discord; handles `pause`, `resume`, `close_all`, `status`.
- `infrastructure/task_queue.py` — async task queue for deferred / retry work (e.g., notifications, history writes) so the hot path is not blocked.

---

## Backtest

- Entry point: `python core/backtest_executor.py`
- Uses `core/backtest/` sub-package (distinct from the deleted `core/backtesting_engine.py`).
- **`import core.backtest` is lightweight:** [core/backtest/__init__.py](../core/backtest/__init__.py) uses PEP 562 lazy exports so pandas/numpy are not loaded until you reference `HistoricalDataLoader`, `BacktestEngine`, `PerformanceMetrics`, or `MonteCarloSimulator`. [core/backtest/data_loader.py](../core/backtest/data_loader.py) and [core/backtest/engine.py](../core/backtest/engine.py) import pandas/numpy inside methods, not at module import time.
- Fetches historical bars from `infrastructure/database.py` cache (falls back to API fetch if cache misses).
- Strategies must implement `evaluate(bar)` in a pure-function style to be backtest-compatible.
- Batch backtest across parameter grid: `scripts/batch_backtest.sh` → `scripts/batch_backtest.py`.
- Research orchestration (grid + mandatory OOS + MC gate + optional DB metadata): `python -m core.research.runner` and [docs/BACKTEST_RESEARCH.md](BACKTEST_RESEARCH.md).
- Strategy workflow (decision tree): [docs/STRATEGY_DEVELOPMENT.md](STRATEGY_DEVELOPMENT.md).
- Results are written to Postgres `strategy_performance` table and optionally to CSV via `scripts/export_history.py`.
- Do not run backtests against a live account connection; use `ENABLE_SIGNALR=false` in `.env` or pass `--offline`.

---

## What just got cleaned up

See [docs/CHANGELOG.md](CHANGELOG.md) for the full entry. Summary:

- Deleted `events/` package (conflicted with `core/event_bus`); all imports migrated to `core.event_bus` + `core.events`.
- Deleted `servers/webhook_server.py` + `servers/start_webhook.py` (synchronous legacy; superseded by `servers/start_async_webhook.py`).
- Deleted `core/backtesting_engine.py` (superseded by `core/backtest_executor.py`).
- Deleted `core/strategy_cache.py` (superseded by `core/state_cache.py`).
- Deleted `gui/chart_html_fixed.py` + `gui/chart_window.py` (dead GUI variants).
- Removed `rust/target/` (1 700+ build artifacts) from git tracking; added to `.gitignore`.
- Deleted `.env.bak`, `.env.backup.*`, `.env.clean` (contained plaintext secrets; policy is `.env` only, never committed).
- Introduced `core/logging_setup.py` — single `configure_logging()` call; all entrypoints use it; `logging.basicConfig` removed everywhere else.
- Introduced `config/strategies/<name>.toml` + `core/strategy_config.py` — strategies now read params from TOML, not bare env vars.

---

## What is intentionally left for later

See [docs/ROADMAP.md](ROADMAP.md) (canonical) and the `[Unreleased]` section of [docs/CHANGELOG.md](CHANGELOG.md). Older narrative: [docs/archive/COMPREHENSIVE_ROADMAP.md](archive/COMPREHENSIVE_ROADMAP.md). Key deferreds:

- **`trading_bot.py` decomposition**: the 10 k-line god module must be split into cohesive sub-modules (auth, connection, order management, session management). No timeline set; do not add new top-level logic there.
- **Full Rust hotpath or removal**: only 3 paths wired. Either complete the migration or remove the FFI entirely. Currently opt-in via `TOPSTEPX_USE_RUST=1`.
- **Dashboard WebSocket migration**: `gui/master_control.html` polls; a push-based WebSocket feed from `servers/websocket_server.py` is planned but not wired end-to-end.
- **Complete strategy migration to TOML**: `mean_reversion_strategy.py` and a few others still have hard-coded defaults that should move to `config/strategies/*.toml`.

---

## Where to start when …

- **Adding a strategy**: see [Adding a new strategy](#adding-a-new-strategy) above → `strategies/strategy_base.py` → `config/strategies/_schema.toml`.
- **Debugging a misfire** (order placed at wrong price / wrong side): start at `core/order_execution.py` → check `BarClosedEvent` payload in logs → cross-reference `brokers/topstepx_adapter.py` REST call log lines.
- **Changing risk limits**: `core/risk_management.py` + the relevant `config/strategies/<name>.toml` keys (`max_position_size`, `daily_loss_limit`). Risk limits in `.env` (`RISK_MAX_LOSS`) override TOML.
- **Chasing SignalR disconnects**: `core/websocket_manager.py` logs every reconnect at INFO. Look for `SignalR reconnecting` in `trading_bot.log`; check `brokers/topstepx_adapter.py` for the handshake sequence. Known issue: token expiry and reconnect can race — ensure `core/auth.py` refresh fires before the reconnect attempt.
- **Looking at a fill that didn't happen**: check `user_hub_manager.py` logs for the order ID → look for the REST response in `topstepx_adapter.py` → confirm the order reached `ACCEPTED` state before the market moved. Rejected orders appear in `core/order_audit.py` log lines.
- **Suspecting a stale cache**: `core/state_cache.py` holds in-memory position and order state. Call `state_cache.invalidate()` or restart the executor process; the cache is rebuilt from Postgres and the live API on startup.
- **P&L numbers look wrong**: start at `infrastructure/database.py` `trade_history` table → cross-reference with `strategy_performance` → check `core/session_trade_tracker.py` for within-session math. Contract multipliers are defined per symbol in `trading_bot.py`; if a new symbol is added without a multiplier entry, P&L will be wrong.
- **Deployment not starting on Railway**: check `Procfile` references `servers/start_async_webhook.py`; confirm `DATABASE_URL` and `PROJECT_X_API_KEY` are set in the Railway environment panel. Build logs are at `scripts/deploy_to_railway.sh` output or in the Railway dashboard.
- **Rate-limit errors (429) from TopStepX**: `core/rate_limiter.py` enforces `API_RATE_LIMIT_MAX` requests per `API_RATE_LIMIT_PERIOD` seconds (defaults 60/60). Under heavy backtest or multi-account load you may need to lower `PREFETCH_SYMBOLS` or increase `API_TIMEOUT`.
