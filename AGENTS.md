# AGENTS.md — TopStepX Trading Bot

## What this is

An autonomous futures-trading bot targeting the TopStepX (ProjectX) platform via its SignalR-based REST+WebSocket API. It runs one or more strategy processes (`core/strategy_executor.py`) against live or paper-trading accounts, aggregates real-time bar data, evaluates strategy logic, and submits/manages orders through `brokers/topstepx_adapter.py`. A lightweight webhook/dashboard server (`servers/start_async_webhook.py`) is the Railway-deployed companion. The codebase is actively traded; treat it as production.

## Read order

1. [docs/HANDOFF.md](docs/HANDOFF.md) — mental model, lifecycle, process layout, operating notes
2. [docs/README.md](docs/README.md) — canonical topic index (architecture, deploy, DB, env, strategies, …)
3. [docs/MAP.md](docs/MAP.md) — annotated directory tree, every non-trivial module explained
4. [docs/PLAYBOOK.md](docs/PLAYBOOK.md) — runbooks: start/stop, deploy, account switch, log drain
5. [docs/GOTCHAS.md](docs/GOTCHAS.md) — footguns, subtle bugs, known env-var quirks
6. [docs/DECISIONS.md](docs/DECISIONS.md) — why things are the way they are (ADRs)
7. [docs/CONVENTIONS.md](docs/CONVENTIONS.md) — code style, naming, async patterns, test approach

## Production entrypoint

```
scripts/run_overnight.sh <account_num>
  └── python core/strategy_executor.py --strategy=... --account_id=...
        └── TopStepXTradingBot  (trading_bot.py)
              ├── brokers/topstepx_adapter.py   (REST + SignalR)
              ├── core/websocket_manager.py      (market hub)
              ├── core/user_hub_manager.py       (orders/fills/positions)
              └── core/event_bus.py              (internal event dispatch)
```

- `scripts/start_all.sh` / `scripts/stop_all.sh` / `scripts/restart_all_strategies.sh` — multi-strategy lifecycle.
- `scripts/start_multi_account.sh` / `scripts/stop_multi_account.sh` — parallel account management.
- Dashboard / Railway path: `Procfile` → `web: python3 servers/start_async_webhook.py`.
- `railway.json` and `Dockerfile` define the cloud deployment; `scripts/deploy_to_railway.sh` is the deploy helper.

## Project shape

- [`trading_bot.py`](trading_bot.py) — 10 k-line god module; `TopStepXTradingBot` class lives here. Decomposition is on the roadmap.
- [`strategies/`](strategies/) — `overnight_range_strategy.py`, `mean_reversion_strategy.py`, `trend_following_strategy.py`, `simple_candle_strategy.py`, `simple_momentum_strategy.py`, `trend_scalping_strategy.py`; base class at `strategies/strategy_base.py`; registry at `strategies/strategy_manager.py`.
- [`brokers/topstepx_adapter.py`](brokers/topstepx_adapter.py) — ~5.5 k lines; all TopStepX REST calls and SignalR connection management.
- [`infrastructure/database.py`](infrastructure/database.py) — Postgres via `psycopg2.ThreadedConnectionPool`; 12 tables; all persistence.
- [`core/websocket_manager.py`](core/websocket_manager.py) — market data SignalR hub (quotes, bars).
- [`core/user_hub_manager.py`](core/user_hub_manager.py) — account hub (order updates, fills, position changes).
- [`core/event_bus.py`](core/event_bus.py) + [`core/events.py`](core/events.py) — canonical in-process event system. Do not import from the deleted `events/` package.
- [`core/strategy_config.py`](core/strategy_config.py) — TOML-based config with hot reload; all strategies must use this, not `os.getenv`.
- [`config/strategies/`](config/strategies/) — per-strategy TOML files (`overnight_range.toml`, `_schema.toml` for validation).
- [`servers/start_async_webhook.py`](servers/start_async_webhook.py) — production webhook/dashboard entry point.
- [`gui/master_control.html`](gui/master_control.html) via [`gui/chart_html.py`](gui/chart_html.py) — browser-based trading dashboard (`master` / `gui` CLI). Overnight-range chart lines use in-process strategy data or Postgres `or_ranges` when `overnight_range` runs in `strategy_executor`; orders list filters terminal statuses server-side.

## Golden rules

- Never `os.getenv` inside `strategies/*` — use `StrategyConfig` from `core/strategy_config.py`.
- Never `import` from `events/` — that package is deleted; use `core.event_bus` and `core.events`.
- Never `import requests` or call `time.sleep` inside an `async def`.
- All logging goes through `core.logging_setup.configure_logging()` — never call `logging.basicConfig` elsewhere.
- Never commit `.env*` files — only `.env.example` is tracked.
- Delete dead code outright; do not create an archive folder. Git history is the archive.
- Hot-paths: no `INFO`-level `json.dumps`, no per-tick heap allocations beyond the bare minimum.

## When you change code

- [ ] Update [`docs/CHANGELOG.md`](docs/CHANGELOG.md) under `[Unreleased]`.
- [ ] If conventions or entrypoints change, update [`docs/HANDOFF.md`](docs/HANDOFF.md) and this file.
- [ ] Run `make verify` or `scripts/verify_handoff.sh` (kit links + MAP freshness).
- [ ] Run `make test` or `pytest` (curated default suite).
- [ ] If directory layout changes, regenerate [`docs/MAP.md`](docs/MAP.md) via `scripts/gen_map.sh`.
- [ ] Never commit `.env`, `.env.bak`, `.env.backup.*`, `.env.clean`, or any file with plaintext secrets.
- [ ] New strategy → add `config/strategies/<name>.toml` and register in `strategies/strategy_manager.py`.
- [ ] New async code → verify it does not block the event loop (no `requests`, no `time.sleep`).

## Backtest

- **Single-shot, JSON-mode** (one strategy / one symbol / one window): `python core/backtest_executor.py --strategy=... --symbol=... --csv=... --start=... --end=... --replay --format=json --include-trades`
- **Walk-forward (multi-fold, multi-symbol) — ALWAYS use the harness, never roll a subprocess loop**: `scripts/walkforward_trade_recap_report.py` is the canonical optimized entry. It uses `core/backtest/inprocess_runner.py` (pre-warmed ProcessPoolExecutor with shared pandas/numpy/strategy imports) by default — ~10× faster than spawning `core/backtest_executor.py` per fold and avoids the ad-hoc subprocess fan-out anti-pattern.
- **Parameter sweeps**: `scripts/optimize_strategy.py` (TOML-driven trial harness) → calls the walk-forward harness with `--in-process` for each trial. `scripts/strategy_parameter_sweep.py` for matrix grids. Do NOT write per-session ad-hoc sweep scripts that subprocess `core/backtest_executor.py` in a loop — that path's per-task startup overhead (~250-400 ms × pandas + numpy + pyarrow + strategy imports) dominates the actual replay cost on small windows.
- Batch grid (legacy): `scripts/batch_backtest.sh` → `scripts/batch_backtest.py`
- Results land in Postgres `strategy_performance`; export via `scripts/export_history.py`
- Run offline: set `ENABLE_SIGNALR=false` — never run backtests against a live SignalR connection.
- **Decision cache**: the walk-forward harness writes to `docs/perf/_decision_cache/`. Key includes `core/backtest/strategy_replay.py` + `core/backtest/engine.py` content hashes — engine drift invalidates. If you change anything **outside** those two files that still affects replay behaviour (e.g. a strategy file's signal logic, a shared helper), pass `--no-cache-decisions` for the validating run.
- **`BACKTEST_FAST_LOOP=1` is the default in walk-forward harnesses.** It swaps `df.iterrows()` for a `_BarRow` (slots-based, ~10× lighter) iterator. Any new replay-engine code that touches the per-bar `bar` object MUST work for both `pd.Series` AND `_BarRow` — do NOT add `isinstance(bar, pd.Series)` guards, use duck-typing (`bar["open"]`, `bar.name`). The 2026-06-03 round-14 cross-session bug was caused by exactly this guard silently no-op'ing the EOD flat under fast loop. Regression test: `tests/test_replay_force_flat_eod.py::test_replay_market_flat_accepts_barrow_fast_loop`.

## Key env vars

Documented in [`.env.example`](.env.example). Most critical:
- `PROJECT_X_API_KEY` / `PROJECT_X_USERNAME` — broker auth (also aliased from `TOPSETPX_*` typo variants, `trading_bot.py:180-181`).
- `DATABASE_URL` — Postgres; required for persistence.
- `TOPSTEPX_USE_RUST` — opt-in Rust hotpath (default `false`).
- `DAILY_LOSS_LIMIT` / `INITIAL_BALANCE` — global risk limits enforced by `core/risk_management.py`.
- `ENABLE_SIGNALR=false` — disables live connections for offline/backtest use.

## Cursor rules

Agent-level coding constraints are in [`.cursor/rules/`](.cursor/rules/) (`*.mdc` files). Read them before making structural changes. If that directory is empty or absent, the rules in this file's **Golden rules** section are authoritative.
