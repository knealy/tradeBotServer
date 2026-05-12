<!-- Footguns and nonobvious behavior. Add to this list whenever you trip on something. -->

# Gotchas

Dense reference for behavior that is easy to misread or break. Prefer the cited files over this list when in doubt.

### Ops / deploy hygiene

- **Legacy cron → webhook**: If an old machine still runs `curl` to a retired Railway URL on a schedule, remove the line from `crontab -e` (or launchd plist) so you are not hammering a dead endpoint. Railway: cancel the project in the Railway dashboard when decommissioning; env vars there are not auto-deleted from your shell profile.
- **Discord heartbeat**: Optional `DISCORD_STATUS_INTERVAL_SECONDS` + `DISCORD_WEBHOOK_URL` sends a short status digest from the interactive bot or `strategy_executor` (see [core/discord_notifier.py](../core/discord_notifier.py)).

### Environment variables

- `PROJECT_X_API_KEY` and `PROJECT_X_USERNAME` each fall back to the transposed spellings `TOPSETPX_API_KEY` and `TOPSETPX_USERNAME`. Keep both spellings working or migrate env docs and Railway vars explicitly—dropping aliases breaks setups that still export the typo form.

- Citation: [trading_bot.py](../trading_bot.py#L180-L181).

- The strategy executor reads the same pair before constructing `TopStepXTradingBot`, so CLI runs and the big module disagree only if you patch one site.

- Citation: [core/strategy_executor.py](../core/strategy_executor.py#L408-L410).

- [`.gitignore`](../.gitignore) excludes populated env files; [`.env.example`](../.env.example) is the lone committed template.

- Do not `git add .env` or backups—history retains secrets.

- When `JWT_TOKEN` is present, `__init__` sets `session_token` from it and parses `exp` without verifying the signature. Startup may appear healthy while API calls 401 after expiry.

- Citation: [trading_bot.py](../trading_bot.py#L184-L204).

### File-system surprises

- The log directory path must be a directory. If something created a **file** named `logs` or `trading_bot.log`’s parent as a file, logging setup renames the conflicting path to `<name>.file_backup_<UTC ts>` then creates the directory tree.

- Citation: [core/logging_setup.py](../core/logging_setup.py#L54-L58).

- [scripts/run_overnight.sh](../scripts/run_overnight.sh#L16-L21) performs the same `mv` guard for `${PROJECT_ROOT}/logs` before `mkdir -p`.

- Historical note: some branches used to gitignore `tests/`. The current [`.gitignore`](../.gitignore) does not ignore `tests/`; if tests exist locally but never appear in PRs, compare against `main` and remove stale ignore rules in your tree.

- `rust/target/` must stay out of the index; `.gitignore` documents it as a former multi-thousand-file mistake.

- Citation: [`.gitignore`](../.gitignore#L121-L122).

### TopStepX / Project X — account “lockout” vs API

- **`POST /api/Account/search`** (see `AuthManager.list_accounts` in [`core/auth.py`](../core/auth.py)) returns **active** accounts with a normalized **`status`** field copied from the API payload (default `"active"` if missing). You can treat **non-active / disabled** style statuses as “do not trade,” but **prop daily loss limit (DLL) and max loss limit (MLL) thresholds are not exposed** as first-class fields on the account objects this code path normalizes.
- **Order and position reality** still wins: repeated **order rejections**, missing permissions, or **no fills when risk expects working orders** are practical lockout signals the bot already surfaces through logs and risk layers.
- **Local enforcement** remains authoritative for DLL/MLL-style rules: [`core/risk_management.py`](../core/risk_management.py) (`StrategyRiskManager`, env-driven caps) and [`core/account_tracker.py`](../core/account_tracker.py) track PnL/compliance-style state; do not assume the REST API will pre-empt every firm-side lock before you try to trade.

### Async / concurrency

- Never block the loop: no `requests.post`, no `time.sleep`, no long CPU work in `async def`. Phase-style work uses `aiohttp` + `asyncio.sleep` (or `asyncio.to_thread` for unavoidable sync).

- The executor starts the event bus, then runs a **30-second** supervisor loop (`_update_process_state`, `_check_strategy_status`) forever. That polling overlaps with `EventBus` traffic until someone rewires status to events.

- Citations: [core/strategy_executor.py](../core/strategy_executor.py#L135-L141), [core/strategy_executor.py](../core/strategy_executor.py#L178-L189).

- `BarAggregator` ticks every **200 ms** (`update_interval = 0.2`), calling `_broadcast_updates` on the timer. Expect this to become bar-close-driven after cleanup; until then, partial bars broadcast on that cadence.

- Citations: [core/bar_aggregator.py](../core/bar_aggregator.py#L108), [core/bar_aggregator.py](../core/bar_aggregator.py#L148-L207).

### Event bus

- Canonical imports: `from core.event_bus import EventBus` and `from core.events import Event, EventType`. A deleted top-level `events/` package still appears in old forks; merge conflicts often reintroduce bad imports.

- Citations: [core/event_bus.py](../core/event_bus.py#L13-L14), [core/events.py](../core/events.py).

- Handlers must use `event.type` and `event.data`. There is no `.event_type`; that shape died with the old package.

- Citation: [core/events.py](../core/events.py#L51-L65).

- If `EventBus.publish` runs before `start()`, the bus logs a warning and **drops** the event.

- Citation: [core/event_bus.py](../core/event_bus.py#L70-L72).

### Strategy config

- Parameters belong in `StrategyConfig` (`get`, `get_int`, `symbol_override`, …), not raw `os.getenv` inside `strategies/*`.

- Project rule: [AGENTS.md](../AGENTS.md).

- Precedence is documented on the module: CLI, then env (prefixed), then TOML, then default.

- Citation: [core/strategy_config.py](../core/strategy_config.py#L7-L12).

- `maybe_reload()` is silent if nobody calls it; edit TOML all day without effect unless the strategy loop invokes reload.

- Citation: [core/strategy_config.py](../core/strategy_config.py#L125-L143).

- Table sections `[symbols.<SYM>]` win for that symbol via `symbol_override` before falling back to global keys.

- Citation: [core/strategy_config.py](../core/strategy_config.py#L192-L203).

### SignalR / WebSocket

- On `TopStepXTradingBot`, quote-related SignalR method names come from `PROJECT_X_QUOTE_EVENT`, `PROJECT_X_SUBSCRIBE_METHOD`, `PROJECT_X_UNSUBSCRIBE_METHOD`. Wire different hub versions without editing Python beyond env.

- Citation: [trading_bot.py](../trading_bot.py#L224-L229).

- `WebSocketManager.subscribe_quote` returns immediately when the symbol is already in `_subscribed_symbols`, so duplicate strategies do not duplicate hub traffic. Missing ticks: verify connection, pending queue, and `_subscribed_symbols` state—not “subscribe again.”

- Citation: [core/websocket_manager.py](../core/websocket_manager.py#L590-L597).

- Reconnect paths call `_handle_network_interruption_and_reconnect`: exponential backoff, **max 30 s** per wait, **10** attempts.

- Citation: [core/websocket_manager.py](../core/websocket_manager.py#L454-L511).

### Rust hotpath

- `TOPSTEPX_USE_RUST` forces Rust on/off inside `TopStepXAdapter` construction (`true`/`1`/`on` enable; other non-empty disables auto). `None` leaves auto-detect.

- Citation: [trading_bot.py](../trading_bot.py#L338-L357).

- Broker paths that actually branch to Rust helpers include `get_positions`, `get_open_orders`, `get_market_quote` (see `_get_*_rust` in the adapter).

- Citation: [brokers/topstepx_adapter.py](../brokers/topstepx_adapter.py).

- [scripts/toggle_rust.sh](../scripts/toggle_rust.sh) edits `.env` with macOS-safe `sed -i ''`. Expect ~1.05–1.10× on network-heavy calls—not 10×.

### Logging

- Every process should call `configure_logging()` once from [core/logging_setup.py](../core/logging_setup.py). A second `logging.basicConfig` in random modules stacks handlers or resets levels.

- Citation: [core/logging_setup.py](../core/logging_setup.py#L28-L79).

- Default **console** level is **WARNING**; **INFO** lands on the rotating file. Read `trading_bot.log` (or `LOG_FILE`) when diagnosing strategy chatter.

- Citation: [core/logging_setup.py](../core/logging_setup.py#L32-L33), [core/logging_setup.py](../core/logging_setup.py#L72-L74).

- Emoji-heavy log prefixes were trimmed from hot paths; keep decorative output in scripts or explicit CLI banners, not in inner loops.

### macOS specifics

- Overnight runner wraps Python with `caffeinate -dimsu` so lids and idle timers do not pause fills.

- Citation: [scripts/run_overnight.sh](../scripts/run_overnight.sh#L36).

- Portable shell must use BSD `sed -i ''`. Linux-only `sed -i` breaks developer laptops.

- Citation: [scripts/toggle_rust.sh](../scripts/toggle_rust.sh#L17-L25).

### Trading semantics

- `BREAKEVEN_ENABLED=true` arms breakeven logic; `BREAKEVEN_PROFIT_POINTS` sets the profit threshold (points) before moving protectively. Overnight range still wires envs today; TOML equivalents are documented for convergence.

- Citations: [strategies/overnight_range_strategy.py](../strategies/overnight_range_strategy.py#L148-L149), [config/strategies/_schema.toml](../config/strategies/_schema.toml#L46-L47).

- `MARKET_OPEN_TIME` and `ZONE_ANCHOR_TIME` are **naive** `HH:MM` strings; combine with `STRATEGY_TIMEZONE` (default `US/Eastern`) when localizing.

- Citation: [strategies/overnight_range_strategy.py](../strategies/overnight_range_strategy.py#L127-L133).
