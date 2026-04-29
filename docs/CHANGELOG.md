# Changelog

Rolling log of substantive repo changes. New entries on top. Every PR that
changes runtime behavior or conventions adds an entry here AND updates
`docs/HANDOFF.md` "Last verified" header.

## [Unreleased]

### Added
- **Backtest import hygiene** — [core/backtest/metrics.py](../core/backtest/metrics.py),
  [monte_carlo.py](../core/backtest/monte_carlo.py), and
  [strategy_replay.py](../core/backtest/strategy_replay.py) defer `numpy`/`pandas` (and
  `PerformanceMetrics` in Monte Carlo) until methods run; `from __future__ import annotations`
  on those modules.
- **Tests** — [tests/test_backtest_import_chain.py](../tests/test_backtest_import_chain.py)
  (subprocess import smoke + Monte Carlo metrics lazy load),
  [tests/test_hub_bracket_smoke.py](../tests/test_hub_bracket_smoke.py) (`UserHubHandlers`,
  `create_bracket_order_improved`), [tests/test_strategy_toml_audit.py](../tests/test_strategy_toml_audit.py)
  (TOML parse / schema parity).
- **`STRATEGY_CONFIG_RELOAD`** — added to [scripts/slim_env.py](../scripts/slim_env.py) allowlist;
  [docs/ENV_VARS.md](ENV_VARS.md) documents slim-env vs TOML.

### Fixed
- **`TopStepXAdapter` HTTP passthrough** — [brokers/topstepx_adapter.py](../brokers/topstepx_adapter.py)
  `_make_request` is now `async` and `await`s [AuthManager._make_request](../core/auth.py) (aiohttp),
  with all call sites awaited; restores contracts cache, positions, orders, and history. Removed
  `from datetime import …, time` which shadowed the `time` module and broke `time.time()` /
  `time.perf_counter()` in the same file.
- **Interactive `master` / `gui`** — [core/trading_interactive_ui.py](../core/trading_interactive_ui.py)
  passes `bot` into `CLICommandParser` instead of undefined `self`.

### Changed
- **SignalR User Hub hygiene** — [core/hub_deferred_queue.py](../core/hub_deferred_queue.py) bounded worker
  queue; [core/user_hub_handlers.py](../core/user_hub_handlers.py) defers heavy account/order/position tails,
  fixes `getattr`/tracker checks, awaits `get_open_positions` / `get_market_quote` / `broadcast_update` correctly.
  [trading_bot.py](../trading_bot.py) constructs the queue after hub registration and starts it after auth.
  [README.md](../README.md) + [scripts/profile_strategy_executor.sh](../scripts/profile_strategy_executor.sh) for py-spy;
  `HUB_DEFERRED_QUEUE_MAX` in [.env.example](../.env.example) and [scripts/slim_env.py](../scripts/slim_env.py).
- **Faster account snapshot I/O** — [brokers/topstepx_adapter.py](../brokers/topstepx_adapter.py)
  adds `get_positions_and_open_orders_parallel` (overlapping REST waits). [trading_bot.py](../trading_bot.py)
  `get_positions_and_orders_batch` uses it; `_adapter_positions_to_ui_dicts` deduplicates position serialization.
  [core/state_cache.py](../core/state_cache.py) refreshes orders + positions under a shared `snapshot_{account_id}`
  lock via the batch helper so a miss on either side fills both caches in one parallel pair.
- **Hot-path logging** — demoted repetitive adapter `INFO` lines for empty orders/positions and order fetch to
  `DEBUG`; [trading_bot.py](../trading_bot.py) open-positions count log to `DEBUG`.
- **Docker / uvloop** — [Dockerfile](../Dockerfile) sets `ENV USE_UVLOOP=1` for Linux images (still overridable).
- **Operations perf doc** — [docs/perf/OPERATIONS_TUNING.md](perf/OPERATIONS_TUNING.md) (profile-first, Rust vs I/O).
- **`core.backtest` lazy imports** — [core/backtest/__init__.py](../core/backtest/__init__.py) uses `__getattr__`
  so `import core.backtest` does not load pandas/numpy; [core/backtest/data_loader.py](../core/backtest/data_loader.py)
  and [core/backtest/engine.py](../core/backtest/engine.py) import those libraries inside methods only.
- **`trading_bot.py` &lt;5K target (met)** — User Hub callbacks moved to
  [core/user_hub_handlers.py](../core/user_hub_handlers.py); bracket / monitor flows to
  [core/bracket_orders.py](../core/bracket_orders.py) with thin async wrappers on the bot (~4.8k lines).
  [scripts/slim_env.py](../scripts/slim_env.py) rewrites `.env` to an allowlisted infra set (strategy vars
  belong in TOML). [core/backtest_executor.py](../core/backtest_executor.py) imports **pandas** only inside
  `run_backtest`. Refreshed [README.md](../README.md); replaced stale [docs/START-HERE.md](START-HERE.md) /
  [docs/DOCUMENTATION_CONSOLIDATION_SUMMARY.md](DOCUMENTATION_CONSOLIDATION_SUMMARY.md) (no `docs/archive/`).
- **`trading_bot.py` slimming / startup** — Removed unused multi-tier historical cache + bar reaggregation
  block (~700 lines; canonical history lives in [brokers/topstepx_adapter.py](../brokers/topstepx_adapter.py)).
  Quote/depth subscription is [WebSocketManager](../core/websocket_manager.py)-only (no legacy `_market_hub`
  fallback). FIFO trade consolidation and stats moved to [core/trade_consolidation.py](../core/trade_consolidation.py).
  [StrategyManager](../strategies/strategy_manager.py) is built on first `strategy_manager` access so
  `import trading_bot` no longer imports strategy modules. [strategies/trend_scalping_strategy.py](../strategies/trend_scalping_strategy.py)
  loads NumPy/Pandas only inside `calculate_ema`. [load_env.py](../load_env.py) drops defaults for removed
  cache/WebSocket-pool env keys. [.env.example](../.env.example) trimmed to secrets + common infra (see
  [docs/ENV_VARS.md](ENV_VARS.md)).
- **Plan closure (remaining todos)** — [core/websocket_manager.py](../core/websocket_manager.py)
  waits for SignalR `on_open` via `asyncio.Event` (+ thread-safe `set`/`clear`) instead of a 50ms
  spin loop; fallback poll only if no running loop. [core/account_tracker.py](../core/account_tracker.py)
  fills `position_count` / `positions` in `get_state()` from the last `update_unrealised_pnl` snapshot.
  [strategies/trend_following_strategy.py](../strategies/trend_following_strategy.py) and
  [strategies/overnight_range_strategy.py](../strategies/overnight_range_strategy.py) call
  `modify_stop_loss` when trailing / breakeven logic tightens stops (needs broker position id).
  Removed stale root-level post-mortem markdown under `docs/` (FIXES/FINAL/MGC/ATR/BROWSER/API_FORMAT
  clusters); fixed links in [PHASE3_CHANGES_SUMMARY.md](PHASE3_CHANGES_SUMMARY.md) and
  [docs/perf/README.md](perf/README.md). [docs/perf/BASELINE.md](perf/BASELINE.md)
  documents operator-run py-spy / importtime capture.
- **Plan / tooling sync** — [Makefile](../Makefile) adds `make test`, `make verify`, `make map`,
  `make bench`; [`.pre-commit-config.yaml`](../.pre-commit-config.yaml) runs
  [scripts/verify_handoff.sh](../scripts/verify_handoff.sh) (optional: `pip install pre-commit &&
  pre-commit install`). [gui/README.md](../gui/README.md), [tests/README.md](../tests/README.md),
  [scripts/README_MULTI_WINDOW.md](../scripts/README_MULTI_WINDOW.md) point at canonical docs under
  `docs/`. [`.cursor/plans/tradebot_infra_cleanup_1490780a.plan.md`](../.cursor/plans/tradebot_infra_cleanup_1490780a.plan.md)
  todo statuses updated to match the repo (remaining **pending**: `phase1-env-restructure`,
  `phase2-polling`, `phase2-trading-bot-split`, `phase2-perf-baseline`, `phase2-startup-cost`,
  `phase3-broken-fixes`, `phase4-docs-delete`).
- **Phase 4.2 docs hub** — [README.md](README.md) is the canonical index (replaces broken
  `01-QUICK-START`–style links). New hub pages: [ARCHITECTURE.md](ARCHITECTURE.md), [DEPLOYMENT.md](DEPLOYMENT.md),
  [DATABASE.md](DATABASE.md), [ENV_VARS.md](ENV_VARS.md), [STRATEGIES.md](STRATEGIES.md),
  [BACKTESTING.md](BACKTESTING.md), [DASHBOARD.md](DASHBOARD.md), [RUST.md](RUST.md), [TESTING.md](TESTING.md).
  [scripts/verify_handoff.sh](../scripts/verify_handoff.sh) validates links in these files;
  [HANDOFF_INDEX.md](HANDOFF_INDEX.md) + [HANDOFF.md](HANDOFF.md) prefer [ROADMAP.md](ROADMAP.md) over the legacy
  comprehensive roadmap. Root [README.md](../README.md) links the doc index. Bulk deletion of stale `docs/*.md`
  post-mortems remains a follow-up (`phase4-docs-delete`).
- [README.md](../README.md) — Phase 4.1 operator-focused overview: links to AGENTS/HANDOFF/MAP, honest
  status table, Docker pre-built SPA note, pytest commands, removed legacy marketing claims.
- [gui/chart_html.py](../gui/chart_html.py) — chart/dashboard helpers log failures at DEBUG with
  `exc_info` instead of silent `except` / bare `except` (including task-name scan, runtime parse,
  risk metrics, drawdown account_state, WS JSON parse, broadcast queue edge case); external-strategy
  status avoids `NameError` when `started_at` is missing (`ext_started_at` guard).
- [core/websocket_manager.py](../core/websocket_manager.py) — SignalR `hub.on` registration failures and
  `on_error` outer failures log at DEBUG/ERROR with `exc_info` instead of silent `pass` / ambiguous
  handler branch (Phase 3 `silent-except-pass-cluster`).
- [tests/test_logging_setup_log_path.py](../tests/test_logging_setup_log_path.py) — regression: when the
  log file’s parent path exists as a **file**, [core/logging_setup.py](../core/logging_setup.py) renames
  it (`*.file_backup_*`) before creating the directory (Phase 3 `logs-startup-file-vs-dir`).
- [infrastructure/database.py](../infrastructure/database.py) — **`notifications` async batch writer**
  (`execute_values`), on by default (`DB_ASYNC_NOTIFICATIONS=1`); env `DB_NOTIFICATIONS_*`.
- **Credentials env** — prefer `TOPSTEPX_API_KEY` / `TOPSTEPX_USERNAME` before legacy typo `TOPSETPX_*`
  in [trading_bot.py](../trading_bot.py), [core/auth.py](../core/auth.py),
  [core/strategy_executor.py](../core/strategy_executor.py), [servers/async_webhook_server.py](../servers/async_webhook_server.py),
  [servers/start_async_webhook.py](../servers/start_async_webhook.py), [servers/dashboard_api_server.py](../servers/dashboard_api_server.py),
  [scripts/export_history.py](../scripts/export_history.py). [.env.example](../.env.example) updated.
- [servers/dashboard.py](../servers/dashboard.py) — removed ad-hoc Nov 3/5 performance debug logging.
- Reference Pine scripts moved to [strategies/pine/](../strategies/pine/) (`MOR.pine`, `mom_current.pine`).
- [tests/test_order_executor_event_bus.py](../tests/test_order_executor_event_bus.py) — regression test that
  `OrderExecutor` publishes `ORDER_PLACED` on `core.event_bus` (Phase 3).
- [servers/async_webhook_server.py](../servers/async_webhook_server.py) — **`aiohttp_cors` is required**
  (Phase 3 `async-webhook-cors-fallback`): import fails fast if missing; removed optional middleware
  fallback. Matches [requirements.txt](../requirements.txt) `aiohttp-cors`.
- [infrastructure/database.py](../infrastructure/database.py) — **`strategy_executions` async batch
  writer** (`execute_values`), on by default (`DB_ASYNC_STRATEGY_EXEC=1`); tune via `DB_STRATEGY_EXEC_*`.
  `log_strategy_execution` enqueues when enabled; `close()` / process exit drain with `api_metrics` batcher.
- [brokers/topstepx_adapter.py](../brokers/topstepx_adapter.py) — **Parquet disk cache** for
  `get_historical_data` (Phase 2.10): after the 5s in-memory tier, load/save `.parquet` under
  `HISTORICAL_PARQUET_DIR` (default `.cache/historical_parquet`) with TTL
  `HISTORICAL_PARQUET_TTL_MINUTES` (default 60). I/O runs in `asyncio.to_thread`. Disable with
  `HISTORICAL_PARQUET_CACHE=0`. [.env.example](../.env.example) documents env vars.
- [pytest.ini](../pytest.ini) — `pythonpath = .` so `pytest` finds the `core` / `strategies` packages
  from the repo root (fixes `ModuleNotFoundError: No module named 'core'`).
- [infrastructure/database.py](../infrastructure/database.py) — optional **async batch writer** for
  `api_metrics` (daemon thread + `execute_values`), enabled by default (`DB_ASYNC_API_METRICS=1`);
  tune batch size / flush interval via `DB_API_METRICS_*` (Phase 2.10). `close()` and process exit
  drain the queue.
- Phase 2.11 — `pytest-benchmark` in [requirements.txt](../requirements.txt);
  [tests/bench/](tests/bench/) microbenches (event bus, bar aggregator, JSON, DB row prep);
  [scripts/run_bench.sh](../scripts/run_bench.sh); [docs/perf/nightly.md](docs/perf/nightly.md)
  placeholder for baseline tables. Default [pytest.ini](../pytest.ini) `testpaths` runs a small curated
  set under `tests/` (smoke, order executor bus, logging path, …); full legacy tree:
  `pytest --override-ini="testpaths=tests"`.
- [infrastructure/database.py](../infrastructure/database.py) — `cleanup_old_data` prunes
  `notifications` and `strategy_executions` plus `api_metrics` / `historical_bars` using a single
  retention window (`DB_TELEMETRY_RETENTION_DAYS`, default 30). [servers/scheduled_tasks.py](../servers/scheduled_tasks.py)
  runs this nightly (~03:30 ET) when the dashboard webhook process is up.
- [core/rate_limiter.py](../core/rate_limiter.py) — `acquire_async()` defers blocking wait to a thread;
  [trading_bot.py](../trading_bot.py) uses it from `_make_http_request` so rate-limit sleeps do not stall the event loop.
- [trading_bot.py](../trading_bot.py) — legacy `requests.Session` REST helper removed;
  `await _make_http_request(...)` uses [core/auth.py](../core/auth.py) shared `aiohttp`
  session (Phase 2.1). [requirements.txt](../requirements.txt) drops direct `requests` dep.
- [core/auth.py](../core/auth.py) — shared `aiohttp` + `json_fast`; no auto `Authorization`
  on `/api/Auth/*`; optional `quiet_client_errors` for probe-style calls.
- [strategies/strategy_manager.py](../strategies/strategy_manager.py) — per-tick signal line
  at DEBUG (was INFO).
- [core/json_fast.py](../core/json_fast.py), [trading_bot.py](../trading_bot.py),
  [brokers/topstepx_adapter.py](../brokers/topstepx_adapter.py) — REST/debug JSON
  uses `orjson` via `dumps_str` / `json_fast_loads` where payloads are large or frequent.
- [core/websocket_manager.py](../core/websocket_manager.py) — publishes `QUOTE_UPDATED`
  on the in-process `EventBus` (thread-safe via `run_coroutine_threadsafe`) for explicit fan-out;
  hub stop / transport checks log failures at DEBUG (no bare `except`).
- [core/user_hub_manager.py](../core/user_hub_manager.py), [trading_bot.py](../trading_bot.py) —
  removed `time.sleep(...)` usage from code reachable on the async loop.
- [core/discord_notifier.py](../core/discord_notifier.py) — successful webhook sends log at DEBUG
  (avoids INFO spam).
- [core/events.py](../core/events.py), [core/bar_aggregator.py](../core/bar_aggregator.py),
  [core/interfaces/*.py](../core/interfaces/) — high-churn dataclasses use `slots=True`.

## 2026-04-28 — Phase 2.7 uvloop + perf docs

### Changed
- [requirements.txt](../requirements.txt) — `uvloop` (POSIX only via `sys_platform` marker) so
  production installs match [docs/DECISIONS.md](DECISIONS.md) ADR-007.
- [core/logging_setup.py](../core/logging_setup.py) — `USE_UVLOOP` / `DISABLE_UVLOOP` env toggles;
  INFO log when `uvloop.install()` succeeds; DEBUG when skipped.
- [docs/perf/README.md](../docs/perf/README.md) — operator notes for py-spy, Scalene, and import-time.
- [.env.example](../.env.example) — optional uvloop disable vars under Logging.

## 2026-04-28 — Phase 2.3 polling → events

### Changed
- [core/strategy_executor.py](../core/strategy_executor.py) — DB heartbeat on a
  dedicated 30s task; main coroutine idles on a never-completing future until cancel.
  Strategy health checks run on `EventBus` `STRATEGY_STARTED` / `STRATEGY_STOPPED`
  instead of a combined 30s poll loop.
- [strategies/strategy_manager.py](../strategies/strategy_manager.py) — publishes
  those lifecycle events after successful start/stop.
- [trading_bot.py](../trading_bot.py) — SignalR hub open wait uses
  `asyncio.Event`; quote/depth cache waits use per-symbol `asyncio.Event` not 50ms
  spin loops.
- [core/bar_aggregator.py](../core/bar_aggregator.py) — removed 200ms broadcast
  poll; partial updates are quote-driven (throttled via `BAR_PARTIAL_MIN_INTERVAL`,
  default 0.15s); completed bars broadcast on roll.

## 2026-04-28 — Phase 2.2 log volume + Phase 2.8 lazy strategy imports

### Changed
- [load_env.py](../load_env.py) — replaced `print` with `logging`; Railway
  env hints at DEBUG only.
- [servers/async_webhook_server.py](../servers/async_webhook_server.py) —
  full webhook JSON at DEBUG; INFO logs payload keys only (no multi-KB dumps).
- [strategies/simple_candle_strategy.py](../strategies/simple_candle_strategy.py)
  — removed duplicate `print` calls; demoted noisy position-parse logs to DEBUG.
- [strategies/strategy_manager.py](../strategies/strategy_manager.py) —
  `register_strategy_lazy` + `get_strategy_class` / `registered_strategy_names` /
  `is_strategy_registered`; `broadcast_signal` uses `asyncio.create_task` for async
  Discord (fixes incorrect `to_thread` on coroutine).
- [trading_bot.py](../trading_bot.py) — strategy modules no longer imported at
  bot import time; six built-ins registered lazy.
- Call sites updated: [servers/dashboard_api_server.py](../servers/dashboard_api_server.py),
  [gui/chart_html.py](../gui/chart_html.py), [core/cli_command_parser.py](../core/cli_command_parser.py),
  [servers/async_webhook_server.py](../servers/async_webhook_server.py).
- Narrow lazy registration: [strategies/strategy_manager.py](../strategies/strategy_manager.py)
  `register_builtin_strategies` (TOML `meta.enabled` + `REGISTER_STRATEGIES`, optional explicit subset);
  `catalog_strategy_names` for full UI catalog without importing every module; normalized strategy ids in
  start/stop/summaries/persistence. [trading_bot.py](../trading_bot.py) `strategy_registration_subset`;
  [core/strategy_executor.py](../core/strategy_executor.py) passes single-`--strategy` subset.

## 2026-04-28 — Infrastructure cleanup, env restructure, agent handoff kit

Orchestrated multi-phase cleanup. Phases 1.1–1.7 + Phase 5 (handoff docs).

### Removed
- `events/` package (incompatible second event-bus stack). Migrated
  [core/order_execution.py](../core/order_execution.py) to use
  `core.event_bus` + `core.events` exclusively.
- `servers/webhook_server.py` (sync legacy 3.1K-line http.server stack).
- `servers/start_webhook.py` (only consumer of the above).
- `core/backtesting_engine.py` (orphan; superseded by `core/backtest/engine.py`).
- `core/strategy_cache.py` (orphan; overlaps `core/state_cache.py`).
- `gui/chart_html_fixed.py`, `gui/chart_window.py` (orphans).
- `scripts/RESTART_STRATEGIES.sh` (just printed commands; redundant).
- Local secret-bearing env backups: `.env.bak`, `.env.backup.20260209_202831`,
  `.env.clean`. Rotate any keys that lived only in `.env.clean`.
- ~1,748 `rust/target/` build artifacts untracked from git.

### Added
- [core/logging_setup.py](../core/logging_setup.py) — single
  `configure_logging()` for the whole process. RotatingFileHandler
  10MB × 5, file=INFO, console=WARNING. Best-effort `uvloop.install()`.
- [core/strategy_config.py](../core/strategy_config.py) — TOML-backed
  strategy configuration loader. Precedence: CLI > env > TOML > default.
  Hot-reload via `maybe_reload()`.
- [config/strategies/](../config/strategies/) — per-strategy parameter files:
  - [`_schema.toml`](../config/strategies/_schema.toml) (template)
  - [`overnight_range.toml`](../config/strategies/overnight_range.toml)
  - [`README.md`](../config/strategies/README.md) — workflow notes
- [scripts/gen_map.sh](../scripts/gen_map.sh) — regenerates
  [docs/MAP.md](MAP.md) from module docstrings. Idempotent and safe to commit.
- [scripts/verify_handoff.sh](../scripts/verify_handoff.sh) — drift checker
  for the handoff kit (dangling links, missing files, stale SHA, MAP.md
  staleness). Pre-commit hook target.
- [scripts/toggle_rust.sh](../scripts/toggle_rust.sh) (renamed from
  `DISABLE_RUST_HOTPATH.sh`) — `on|off|status` switch for `TOPSTEPX_USE_RUST`.

### Changed
- [Dockerfile](../Dockerfile) — dropped the missing-`frontend/` Node stage;
  Docker now serves the pre-built SPA at `static/dashboard/`.
- [scripts/build.sh](../scripts/build.sh) — no-op stub since SPA is committed.
- [scripts/restart_all_strategies.sh](../scripts/restart_all_strategies.sh)
  and [scripts/rebuild_rust.sh](../scripts/rebuild_rust.sh) — replaced
  hardcoded `/Users/knealy/...` with portable `cd "$(dirname "${BASH_SOURCE[0]}")/.."`.
- [scripts/start_all.sh](../scripts/start_all.sh) and
  [scripts/stop_all.sh](../scripts/stop_all.sh) — removed dead
  `core/order_monitor.py` references.
- [.env.example](../.env.example) — slimmed from ~160 lines to ~95;
  strategy parameters moved to `config/strategies/<name>.toml`.
- [.gitignore](../.gitignore) — replaced wildcard `.env*` with explicit
  list (so `.env.example` is tracked); un-ignored `tests/`; added
  `rust/target/`.
- 7+ entry points migrated from inline `logging.basicConfig` to the shared
  `core.logging_setup.configure_logging()`.

### Documentation
- New canonical agent kit:
  - [`AGENTS.md`](../AGENTS.md) (root)
  - `.cursor/rules/*.mdc` × 6 (python-style, strategies, event-bus,
    config-precedence, secrets, async-io)
  - [`docs/HANDOFF.md`](HANDOFF.md), [`docs/HANDOFF_INDEX.md`](HANDOFF_INDEX.md)
  - [`docs/MAP.md`](MAP.md) — auto-generated
  - [`docs/PLAYBOOK.md`](PLAYBOOK.md), [`docs/DECISIONS.md`](DECISIONS.md)
    (ADR-001 through ADR-008)
  - [`docs/GOTCHAS.md`](GOTCHAS.md), [`docs/CONVENTIONS.md`](CONVENTIONS.md)
- This file (`docs/CHANGELOG.md`).

### Open follow-ups (deferred to later phases)
See [`docs/ROADMAP.md`](ROADMAP.md). Highlights:
- Decompose `trading_bot.py` (10.5K lines) into existing `core/` modules.
- Phase 2.1 — async I/O cutover (auth + discord_notifier off `requests`) — completed.
- Phase 2.2 — log volume reduction (~70% target) — partial (webhook, load_env,
  simple_candle, bar_agg/websocket/strategy_manager + prior trading_bot quote logs).
- Phase 2.8 — lazy strategy imports — built-in registry uses lazy import; further
  work: only import strategies enabled in TOML / `--strategy=`.
- Phase 2.7+ — profile baseline, `uvloop`/`orjson`/`__slots__` adoption,
  `BackgroundDBWriter` queue, bench suite with budgets.
- Strategy config: most strategies use `StrategyConfig` / TOML; spot-check any
  remaining direct env reads when touching a strategy file.
- Phase 4 — README rewrite + collapse 96 docs/* → ~12 canonical pages.
