# Architectural Decision Records — TradeBotServer

<!-- Last updated: 2026-04-28. Add new entries at the bottom; never renumber or edit Accepted records. -->

One entry per significant architectural decision. Add new entries at the bottom.
Format: ADR-NNN sequential. Do not renumber or retroactively change Accepted entries.

Status values:
- **Accepted** — implemented, in production.
- **Proposed** — under discussion, not yet implemented.
- **Superseded** — replaced by a later ADR; see the superseding entry.

To record a new decision, copy the template below and append it:

```
## ADR-NNN — Title
**Status**: Proposed
**Date**: YYYY-MM-DD
**Context**: ...
**Decision**: ...
**Consequences**:
- ...
```

---

## ADR-001 — Canonical event stack: core/event_bus + core/events

**Status**: Accepted
**Date**: 2026-04-28

**Context**: The codebase previously had an `events/` top-level package that duplicated event type definitions and bus logic already present in `core/event_bus.py` and `core/events.py`. Dual implementations caused import confusion and made it impossible to trace which bus an event had been published on without checking both paths.

**Decision**: `core/event_bus.py` and `core/events.py` are the single canonical event stack. The `events/` package has been deleted. All imports must point to `core.event_bus` and `core.events`.

**Consequences**:
- Any feature branch that imports from `events.*` will break at merge time; fix by updating import paths.
- Adding a new event type requires only one file change (`core/events.py`).
- The event bus is now testable without importing the GUI or server layers.
- `git log -- events/` is the archive if deleted definitions are needed.

---

## ADR-002 — Per-strategy TOML files replace embedded strategy params in .env

**Status**: Accepted
**Date**: 2026-04-28

**Context**: Strategy tuning parameters (timing windows, ATR periods, position sizing, per-symbol overrides) were stored as `STRATEGY_<NAME>_<PARAM>` keys in `.env`. This made the `.env` file unwieldy (200+ keys), conflated secrets with tuning, and required a full restart to change any parameter even when hot-reload was conceptually possible.

**Decision**: Strategy parameters live in `config/strategies/<name>.toml`. The `.env` file holds only secrets and infrastructure config. Parameter precedence (highest to lowest): CLI override → environment variable → TOML → strategy class default. The loader is `core/strategy_config.py`; hot-reload is available via `StrategyConfig.maybe_reload()`.

**Consequences**:
- TOML edits during a live run are picked up on the next `maybe_reload()` call without a restart.
- Operators can diff strategy configs in git independently of secrets.
- A missing TOML file falls back to class defaults silently; set required keys explicitly to avoid silent misconfiguration.
- `.env.example` is the authoritative list of secrets; strategy TOMLs document their own defaults inline.

---

## ADR-003 — Rust hotpath frozen until benchmark gate

**Status**: Accepted
**Date**: 2026-04-28

**Context**: Rust extensions were introduced to accelerate three compute paths. The performance gain over pure Python has not been formally verified under production load. Maintaining Rust binaries increases CI complexity, build time, and on-call cognitive load.

**Decision**: The Rust hotpath is frozen at the three currently active paths. No new Rust paths will be added until a benchmark demonstrates ≥ 1.5× sustained throughput improvement over the Python equivalent under realistic market data volume. The toggle is `TOPSTEPX_USE_RUST` in `.env` (managed by `scripts/toggle_rust.sh`); default production value is determined by the last benchmark result.

**Consequences**:
- Operators can instantly fall back to Python with `bash scripts/toggle_rust.sh off` + executor restart; no rebuild required.
- Rust build artifacts remain in `rust/`; the CI pipeline must keep `cargo build --release` passing.
- The benchmark gate prevents incremental Rust creep that would make Python fallback unreliable.

---

## ADR-004 — Async webhook stack is canonical; sync webhook deleted

**Status**: Accepted
**Date**: 2026-04-28

**Context**: Two webhook server implementations existed: a synchronous `webhook_server.py` (Flask or similar) and an async `servers/async_webhook_server.py` (aiohttp). The sync server blocked the event loop on every incoming webhook, causing latency spikes during burst activity.

**Decision**: `servers/async_webhook_server.py` launched via `servers/start_async_webhook.py` is the sole production webhook entrypoint. The synchronous `webhook_server.py` has been deleted. The dashboard (`gui/master_control.html`) communicates exclusively through the async server.

**Consequences**:
- Webhook ingestion no longer blocks the trading event loop.
- The async server must be started separately from the strategy executors; `python servers/start_async_webhook.py` is the correct entrypoint.
- Any external service sending webhooks must target the async server's port (default 8080, configurable via `WEBHOOK_PORT`).
- `git log -- webhook_server.py` recovers the deleted sync implementation if needed.

---

## ADR-005 — Dead code deleted outright; git history is the archive

**Status**: Accepted
**Date**: 2026-04-28

**Context**: The repository accumulated commented-out code blocks, unreachable branches, duplicate helper functions, and stub files for features that were never completed. Leaving dead code in place increases cognitive load during incident response and makes grep results noisy.

**Decision**: Dead code is removed at the point of discovery with no stub or deprecation comment. The commit message at the deletion commit documents what was removed and why. `git log -p -- <deleted_file>` is sufficient to recover any deleted artifact.

**Consequences**:
- Reviewers must not re-add commented-out code as "just in case" hedges; open an issue instead.
- LLM agents working in this repo should not assume that a missing file indicates an incomplete feature; check `git log --diff-filter=D` first.
- This policy applies to test files, migration scripts, and one-off analysis notebooks as well.

---

## ADR-006 — All logging routes through core/logging_setup.configure_logging()

**Status**: Accepted
**Date**: 2026-04-28

**Context**: Multiple entry points (strategy executor, trading bot, webhook server, backtest CLI) each configured their own `logging.basicConfig` calls, resulting in duplicate handlers, inconsistent formats, and missing log rotation. Incidents were hard to triage because related events were scattered across multiple unrotated files.

**Decision**: Every entry point calls `core.logging_setup.configure_logging()` exactly once before any other logging call. Configuration: `RotatingFileHandler` at 10 MB per file, 5 backup files (50 MB total cap), file handler at `INFO` (overridable via `LOG_LEVEL` env var), console handler at `WARNING`. `LOG_FILE` env var sets the destination. `SignalRCoreClient`, `websocket`, `aiohttp.access`, `urllib3`, and `asyncio` loggers are muted to `WARNING` to suppress noise.

**Consequences**:
- `LOG_LEVEL=DEBUG` in `.env` + executor restart gives full DEBUG output to the rotating file without terminal flood.
- All log files rotate automatically; no cron job or logrotate config is required.
- Adding a new entry point requires only a single `configure_logging()` call at the top.
- Subsequent calls to `configure_logging()` are no-ops unless `LOGGING_FORCE_RECONFIGURE=1` is set.

---

## ADR-007 — uvloop + orjson adopted in hot paths; sync requests removal in Phase 2.1

**Status**: Accepted
**Date**: 2026-04-28

**Context**: The trading bot's hot path — market data ingestion, signal evaluation, order dispatch — runs on asyncio. The default CPython event loop and the standard `json` module introduce measurable latency on high-frequency tick processing. Synchronous HTTP clients (notably `requests`) block the event loop when used from async code.

**Decision**: `uvloop` is installed as the asyncio event loop policy in `core/logging_setup.configure_logging()` (best-effort; silently skipped if not installed). `orjson` replaces `json` in serialization-critical paths. Runtime async HTTP paths are migrated to `aiohttp` and must not block the loop.

**Consequences**:
- `uvloop` and `orjson` must be present in `requirements.txt` and the Dockerfile.
- Any new code in the async hot path must not call `requests` directly; use `aiohttp` or the existing async adapter methods.
- If `uvloop` fails to import at startup (e.g., on Windows CI), the bot falls back to the default loop without error.

---

## ADR-008 — DB writes go through BackgroundDBWriter; trading code never blocks on Postgres

**Status**: Accepted
**Date**: 2026-04-28

**Context**: Early versions of the bot wrote trade records, performance metrics, and API metrics synchronously inside order-execution callbacks. Under load, a slow Postgres write (network jitter, table lock) would delay the next market data event by hundreds of milliseconds, potentially missing entry signals.

**Decision**: All non-critical database writes (trade history, strategy performance, API metrics, cache metadata, process state heartbeats) are enqueued to a `BackgroundDBWriter` queue and flushed asynchronously. Trading-critical reads (account state, active strategy flags) may remain synchronous but must use the connection pool from `infrastructure/database.py`. No trading callback may execute a blocking Postgres write inline.

**Consequences**:
- A write failure in `BackgroundDBWriter` logs an error but does not halt trading; operators must monitor the error rate.
- `BackgroundDBWriter` queue depth should be monitored; a growing queue under normal load indicates a Postgres connectivity issue.
- Reads from `account_state` and `strategy_states` during startup may return stale data if a previous executor crashed mid-write; add a consistency check on startup if this becomes an issue.
- Schema migrations must be backward-compatible with in-flight queued writes; never drop a column without a two-phase migration.
