# Changelog

Rolling log of substantive repo changes. New entries on top. Every PR that
changes runtime behavior or conventions adds an entry here AND updates
`docs/HANDOFF.md` "Last verified" header.

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
- Phase 2.1 — async I/O cutover (auth + discord_notifier off `requests`).
- Phase 2.2 — log volume reduction (~70% target).
- Phase 2.7+ — profile baseline, `uvloop`/`orjson`/`__slots__` adoption,
  `BackgroundDBWriter` queue, bench suite with budgets.
- Strategy refactor: migrate `os.getenv` calls inside every strategy to
  `StrategyConfig.get*` (only the loader exists today; strategies still
  read env directly).
- Phase 4 — README rewrite + collapse 96 docs/* → ~12 canonical pages.
