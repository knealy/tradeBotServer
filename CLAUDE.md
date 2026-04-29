# CLAUDE.md — Agent Bootstrap for tradeBotServer

Claude Code auto-loads this file at session start. It gives you full project context without re-reading every doc.

## What this repo is

Autonomous futures-trading bot for **TopStepX (ProjectX)** prop accounts. Python 3.11+, PostgreSQL, REST + SignalR. Actively traded — treat as production.

## Mandatory read order (read these before touching code)

1. `AGENTS.md` — golden rules, entrypoints, what not to do
2. `docs/HANDOFF.md` — lifecycle, mental model, process layout
3. `docs/MAP.md` — annotated directory tree, every module explained
4. `docs/PLAYBOOK.md` — start/stop, deploy, account switch, log drain
5. `docs/GOTCHAS.md` — footguns and known env-var quirks

## Architecture (one-page summary)

```
scripts/run_overnight.sh <account>
  └── core/strategy_executor.py --strategy=overnight_range --account_id=...
        └── TopStepXTradingBot  (trading_bot.py)
              ├── brokers/topstepx_adapter.py   REST + SignalR (5.5k lines)
              ├── core/websocket_manager.py      market hub (quotes/bars)
              ├── core/user_hub_manager.py       order/fill/position events
              ├── core/event_bus.py              in-process pub/sub
              └── strategies/overnight_range_strategy.py  (primary live strategy)
```

**Event flow:** SignalR tick → `websocket_manager` → `bar_aggregator` → `BarClosedEvent` → `strategy.evaluate()` → bracket order → `topstepx_adapter` → `user_hub_manager` → `FillEvent`

## Key files

| File | Purpose |
|------|---------|
| `trading_bot.py` | God module (~10k lines); `TopStepXTradingBot` class |
| `brokers/topstepx_adapter.py` | All TopStepX API calls |
| `strategies/overnight_range_strategy.py` | Active breakout strategy (MNQ/MES/MGC) |
| `core/risk_management.py` | `StrategyRiskManager` — per-symbol pending/cooldown/qty limits |
| `core/strategy_config.py` | TOML config with hot-reload; use this, never `os.getenv` in strategies |
| `config/strategies/overnight_range.toml` | Live tunable params (no deploy needed) |
| `infrastructure/database.py` | Postgres pool, 12 tables |
| `core/event_bus.py` + `core/events.py` | Canonical event system |
| `servers/start_async_webhook.py` | Railway dashboard entry point |

## Golden rules (enforce always)

- **No `os.getenv` in `strategies/*`** — use `StrategyConfig` from `core/strategy_config.py`
- **No `import` from `events/`** — deleted; use `core.event_bus` and `core.events`
- **No `import requests` or `time.sleep` inside `async def`**
- **All logging via `core.logging_setup.configure_logging()`** — never `logging.basicConfig`
- **No `.env*` commits** — only `.env.example` is tracked
- **Delete dead code outright** — no archive folders; git history is the archive
- **Hot paths**: no `INFO`-level `json.dumps`, no per-tick allocations

## Overnight range strategy — key details

- Tracks 6:00 PM – 9:29 AM ET range for MNQ / MES / MGC
- Scans at 9:29 AM for breakout; places stop bracket orders at market open
- **Breakout monitor** runs every 15s as a separate asyncio task — continues after open
- Order tag format: `TB-stop_bracket-overnight_range-YYMMDDHH`
- Risk manager (`max_pending=2`) counts only `stop_bracket`-tagged entry orders
- `breakout_active_orders[symbol][side]` caches placed order IDs to suppress duplicate attempts
- **Session rollover**: `_cancel_previous_session_orders()` runs at top of `_execute_market_open_sequence` to cancel stale prior-session orders on symbols without open positions

## Config system

- Secrets/infra: `.env` (see `.env.example`)
- Per-strategy knobs: `config/strategies/<name>.toml` — hot-reloaded via `maybe_reload()`
- Credential aliases: `PROJECT_X_*` → `TOPSTEPX_*` → legacy `TOPSETPX_*`

## After every code change

- [ ] Update `docs/CHANGELOG.md` under `[Unreleased]`
- [ ] Run `pytest` (or `.venv/bin/python -m pytest`)
- [ ] Run `scripts/verify_handoff.sh` to check doc links + MAP drift
- [ ] If directory layout changed: `scripts/gen_map.sh` to refresh `docs/MAP.md`
- [ ] New strategy → add `config/strategies/<name>.toml`, register in `strategies/strategy_manager.py`

## Running the bot

```bash
# Single account
bash scripts/run_overnight.sh <account_number>

# All accounts
bash scripts/start_all.sh

# Interactive CLI
python trading_bot.py

# Dashboard (Railway / local)
python servers/start_async_webhook.py
```

## Tests

```bash
.venv/bin/python -m pytest          # full suite
python3 -c "import ast; ast.parse(open('strategies/overnight_range_strategy.py').read())"  # syntax check
```

## Persistent memory

Project memory is saved under `~/.claude/projects/-Users-risu-tradeBotServer/memory/`. `project_tradebot.md` contains architecture notes, known bugs, and deferred work accumulated across sessions. Read it when resuming after a gap.
