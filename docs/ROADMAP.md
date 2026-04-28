# Roadmap

Honest, narrow roadmap. Items in priority order. Each item has a phase tag
linking back to [`.cursor/plans/tradebot_infra_cleanup_<id>.plan.md`](../.cursor/plans/).

<!-- Last refreshed: 2026-04-28. Update on every substantive PR. -->

## Currently underway

- **Phase 1 cleanup** — secrets purge, Docker fix, dead-module deletion,
  script-path fixes, logging consolidation, env restructure. See
  [`docs/CHANGELOG.md`](CHANGELOG.md) for the in-progress slice.

## Next up (Phase 2 — performance & noise)

1. **Async I/O cutover** — replace sync `requests` + `time.sleep` in
   `core/auth.py` and `core/discord_notifier.py` with `aiohttp` + `asyncio.sleep`.
   Remove `requests` from `requirements.txt` once verified.
2. **Log volume cut (~70%)** — drop INFO-level `json.dumps(payload, indent=2)`
   on every order in `trading_bot.py:2623-2673` and `brokers/topstepx_adapter.py:412`;
   gate bar-aggregator chatter behind `BAR_AGG_DEBUG`; strip emoji prefixes
   from hot-path loggers.
3. **Replace polling with events** — strategy_executor 30s loop subscribes
   to `EventBus` instead of polling; `bar_aggregator` broadcasts on bar-close
   instead of every 200ms; SignalR connect spinwait → `asyncio.Event`.
4. **Profile-driven baseline** — capture `py-spy` and `scalene` snapshots
   under live market hours; commit before/after SVGs to `docs/perf/`.
5. **uvloop + orjson + __slots__ + lru_cache** — adopt in hot paths
   (event bus, broker adapter, bar aggregator). Tune one shared
   `aiohttp.ClientSession` per process.
6. **Background DB writer queue** — wrap every `record_*`/`save_*` in
   `infrastructure/database.py` behind an `asyncio.Queue`; flush every 1s
   or 500 rows via `execute_values`. Hot path never waits on the DB.
7. **Polars Parquet hot tier** — finish the cutover at
   `trading_bot.py:6409-6562`; Postgres becomes long-term archive only.
8. **Bench suite + budgets** — `tests/bench/` with `pytest-benchmark`;
   enforce: order RTT <200ms p95, position fetch <100ms p95, tick cycle
   <50ms p95, startup <5s. Auto-publish to `docs/perf/nightly.md`.

## Phase 3 — broken / known-issue cleanup

- Migrate the `os.getenv(...)` reads inside `strategies/*.py` to
  `StrategyConfig` (loader is in place; strategies still read env directly).
- Wire actual stop modification at
  [strategies/trend_following_strategy.py:434](../strategies/trend_following_strategy.py)
  and [strategies/overnight_range_strategy.py:3107](../strategies/overnight_range_strategy.py).
- Replace silent `except: pass` clusters in `trading_bot.py`,
  `gui/chart_html.py`, `core/websocket_manager.py`, `infrastructure/database.py`.
- Remove "NOV 3 DEBUG" / "NOV 5 DEBUG" lines at
  [servers/dashboard.py:1693](../servers/dashboard.py),
  [servers/dashboard.py:1700](../servers/dashboard.py).
- Move `strategies/MOR.pine` and `strategies/mom_current.pine` into
  `strategies/pine/` (or a sibling repo).
- Decide whether `servers/scheduled_tasks.py` is wired up; delete if not.

## Phase 4 — docs consolidation + README rewrite

- Collapse `docs/` (~96 markdown files) to the canonical set:
  README + ARCHITECTURE + DEPLOYMENT + DATABASE + ENV_VARS + STRATEGIES +
  BACKTESTING + DASHBOARD + RUST + CHANGELOG + ROADMAP + TESTING.
- Replace marketing-style README with operator-focused 150-line version
  (current status table, run-locally, process layout mermaid, strategies,
  operating notes, prospectus, disclaimer).

## Big-picture decisions deferred

- **`trading_bot.py` decomposition** — 10.5K lines in one class. Split
  quote/depth caches into `core/state_cache`, websocket pool into
  `core/websocket_manager`, market-hub bootstrap into
  `core/user_hub_manager`. Target: <5K lines.
- **Rust hotpath**: only 3 of ~10 paths active; speedup is 1.05–1.10×
  for network-bound ops. Decision (per ADR-003): freeze until benchmark
  gate (≥1.5× over Python) is met. Toggle via `scripts/toggle_rust.sh`.
- **Dashboard websocket migration**: master_control.html currently uses
  HTTP polling; migrate to the existing `/ws` route.

## Operating constraints to respect

- Live trading bot. Don't break order paths or strategy signals.
- Don't ship anything that increases CPU / memory baseline without a
  paired benchmark showing the win.
- Every conventions change → update `.cursor/rules/*.mdc` AND
  `docs/CONVENTIONS.md` AND `docs/HANDOFF.md` "Last verified" header.
