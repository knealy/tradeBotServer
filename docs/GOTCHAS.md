<!-- Footguns and nonobvious behavior. Add to this list whenever you trip on something. -->

# Gotchas

Dense reference for behavior that is easy to misread or break. Prefer the cited files over this list when in doubt.

## Timestamps & time zones (recurring bugs)

We have hit the same timezone mistakes repeatedly (merge failures, range overlays stopping at CSV lag dates, `Cannot compare tz-naive and tz-aware`, mislabeled `*_et` fields). **Follow this split and normalize at boundaries.**

### Two clocks — do not mix them

| Kind | Representation | Examples |
|------|----------------|----------|
| **Bar OHLCV index** | `pandas.DatetimeIndex`, **naive UTC** | Databento CSV, `parquet_cache`, replay `_BarRow.name`, API bars after normalization |
| **Session wall clock** | **tz-aware ISO in US/Eastern** | `session_start_et`, `session_end_et`, `attach_range_window_et()` output |
| **Chart / LWC `time`** | Unix seconds (UTC instant) | `gui/chart_html.py`, trade recap snap |

Naive UTC bar indexes are **not** “UTC mislabeled as local”. They are UTC instants with `tzinfo=None` — the convention inherited from Databento + parquet cache.

### Mandatory before any OHLCV merge or slice

```python
from core.backtest.ohlcv import ohlcv_index_naive_utc

left = ohlcv_index_naive_utc(csv_df)
right = ohlcv_index_naive_utc(api_df)
merged = pd.concat([left, right]).sort_index()
merged = merged[~merged.index.duplicated(keep="last")]
```

Call sites today: `core/range_history_backfill.merge_ohlcv_dataframes`, `chart_bars_to_ohlcv_df`, `strategies/morning_range_reversion_strategy._ensure_ohlcv_index_naive_utc` (same semantics — prefer consolidating on `ohlcv_index_naive_utc` in new code).

### Session bounds → bar slice

When slicing naive-UTC bars for an ET session window, convert **aware ET → UTC → strip tzinfo**:

```python
t0 = pd.Timestamp(start_et.astimezone(timezone.utc).replace(tzinfo=None))
t1 = pd.Timestamp(end_et.astimezone(timezone.utc).replace(tzinfo=None))
window = df.loc[t0:t1]
```

See `core/range_history_backfill._session_window_utc_slice`. Never store naive UTC strings in fields named `*_et`.

### Footguns (checklist)

1. **Merging CSV + API / live bars** without `ohlcv_index_naive_utc` on **both** frames → `TypeError` or silent empty slices.
2. **`session_*_et` as naive UTC** → overlays and range pills draw on the wrong calendar day.
3. **Inner `from datetime import datetime`** inside a handler that already uses the module-level `datetime` → `NameError` on later lines (e.g. `gui/chart_html.py` trades endpoint).
4. **Assuming `load_ohlcv_cached` output is tz-aware** — it is naive UTC; broker frames often are not.
5. **Comparing `datetime.now()` (naive local)** to aware ET bounds — use `zoneinfo` / `core.time_utils` patterns from strategies.

### Regression test

`tests/test_range_history_backfill.py::test_merge_ohlcv_dataframes_api_wins_on_overlap` — tz-aware API frame merged with naive CSV; API wins on overlap.

---

## Historical OHLCV data

- **Quarterly futures rolls interleave two contracts** in the canonical Databento CSVs under `historical_data/price/*_1m_databento.csv` and `*_5m_databento.csv`. Around the 10th–17th of every Mar/Jun/Sep/Dec (≈3–7 trading days before the third-Friday expiry), individual minutes alternate between front-month and back-month bars, with a price spread of ~150–300 pt on MNQ (cost-of-carry basis). Rendered as-is, you get **two parallel candle sequences** at the same x-positions, and intrabar replay fills can trip on prices the contract you would actually trade never touched. `core/backtest/ohlcv.deroll_dual_contract_bars` removes the non-continuing-contract bars and is wired into both `dataframe_to_chart_bars_unix` and `replay_bars_from_ohlcv_df` by default (pass `deroll=False` to opt out). Symptoms when disabled: trade exit prices that don't line up with any visible OHLC bar, "double vision" candles on Mar/Jun/Sep/Dec recap charts, and `cluster_spread` > 200 pt in `_two_means_1d` on rolling days.

- Citation: [core/backtest/ohlcv.py](../core/backtest/ohlcv.py).

### Backtest fill semantics

- **STOP orders are direction-validated against placement_price.** `BacktestEngine._check_order_fill` only fires a BUY STOP when `placement_price <= stop_price` (price has to rise to it) and a SELL STOP when `placement_price >= stop_price` (price has to fall to it). `placement_price` is captured from `BacktestEngine.last_close` at order-creation time and updated in the run loop / at the top of `StrategyReplayEngine._process_subbar_fills` so it matches the bar the strategy just saw. The old behavior — fire a BUY STOP the moment any later bar's high crossed the stop, even when the bar's low never came near it — produced impossible fills like the 2026-05-19 MNQ "LONG @ 28853.88 at 9:45 ET" (the bar's low was 28868.75; price didn't re-trade 28853 until 10:05). If you're writing a strategy that emits an entry at a level the current bar has already crossed past, treat it as a **LIMIT** (waits for retrace) or a **MARKET** (fills at next bar open) — a same-direction stop is the bug magnet.

- Citation: [core/backtest/engine.py](../core/backtest/engine.py), [core/backtest/strategy_replay.py](../core/backtest/strategy_replay.py).

- **`morning_range_reversion.signal.reentry_threshold_points`** (> 0): on the **first 5m close outside** the 7–8am anchor, immediately place a resting stop-entry ``threshold`` pts back inside the box (low sweep → BUY STOP at L + threshold; high sweep → SELL STOP at H − threshold), same intent as **overnight_range** advance brackets. The order rests until price trades through the level (e.g. May 19 2026: sweep close 9:20 ET → stop at 28860.75 → fill 9:30 open rip). TOML root default **1 pt**; MNQ often higher via override; **MGC uses 1.0** (2026-07-15 — was 0.0 exact-H, which TopStepX often rejected as Invalid price). With threshold **0**, legacy `require_reentry_close` / immediate-at-extreme paths apply; replay fills are honest thanks to the stop-direction guard above.

- **Chart `↑ swept` ≠ working broker order.** GUI reads `sweep_fired_high` from strategy state. Before 2026-07-15 an Invalid-price reject also set that flag, so the orange line appeared with 0 fills / 0 orders (2026-07-09 MGC @ 4120.40). Invalid-price rejects now only set `immediate_block_until_inside` and re-arm after a close back inside `[L,H]`.

- **Account on Position Brackets rejects native OCO payloads.** Broker error: `Brackets cannot be used with Position Brackets. You must enable Auto OCO Brackets.` (Code 2). **Default:** one-shot Discord + `BRACKET_MODE_ALERT` (enable Auto OCO in ProjectX) and **refuse** the order — no silent hybrid fallback (hybrid left orphans in live smoke). Opt-in hybrid for debug only: `TOPSTEPX_BRACKET_MODE=position|hybrid`. Prefer enabling Auto OCO Brackets on the account.

- **`./scripts/refresh_historical.sh` must not open Railway Postgres.** It forces `DISABLE_DATABASE=1`, short `API_TIMEOUT`, and unsets `DATABASE_URL`. Stitch scripts use `core/broker_history_session.py` (no full bot). If auth fails they exit 1 — they must not print `✅ ok` and keep going.

- **`morning_range_reversion` — R-ratio structural problem.** With entry at `L + threshold` and the default stop at `L - half × sl_mult` (stop anchored to the **range extreme**, not to entry), the risk is `half + threshold` while the reward is only `half - threshold`. On a 50-pt range (half=25) with threshold=7: risk=32 pts, reward=18 pts → breakeven WR = 32/(32+18) = 64 %; including $5 commission the breakeven rises to ~69 %. The 700-day MNQ replay had a 65.5 % actual WR and only 0.91 PF as a result. **Fix: `signal.sl_fixed_pts`** — when set to e.g. `14` (= 2 × threshold), stop moves to `entry − 14` (LONG) and breakeven WR drops to ~52 %, giving +EV at 65.5 % WR. Walk-forward before deploying live; a tighter stop will increase the stop-loss-hit rate.

- **`morning_range_reversion` — TP-in-loss bug on narrow range days.** When the morning range is narrower than `2 × threshold` (e.g. < 14 pts for MNQ with threshold=7), `depth` is capped to `half − ε ≈ midpoint`, making `entry ≈ TP`. A TP fill earns near-zero gross PnL; after the $5 round-trip commission it records a **net loss**. This was responsible for 7 "exit_reason=take_profit → pnl < 0" rows in the 700-day MNQ walk-forward. **Fix: `signal.min_range_width_points`** — skip fade signals on days where the morning range is narrower than this threshold. Minimum useful value = `2 × threshold + small_buffer` (e.g. 20 for MNQ with threshold=7).

### Live data freshness — REST polling is the only bar source

- **Live executor now wires Market Hub + bar aggregator + REST merge (2026-05-21 fix).** As of CHANGELOG entry "Market Hub wired into StrategyExecutor", `core/strategy_executor.py` calls `TopStepXTradingBot.start_market_hub_for_strategies(symbols, timeframes)` immediately after the strategies start. That opens the SignalR Market Hub, subscribes quotes for every symbol declared in any running strategy's `config.symbols`, registers each strategy's `timeframe` on `core/bar_aggregator.BarAggregator`, and pipes completed bars into a per-(symbol, timeframe) ring buffer on the bot (`_live_bars`, capped by `LIVE_BAR_CACHE_MAXLEN`, default 240). `trading_bot.get_historical_data()` then merges any cached bars **newer than the REST tail** onto the REST response, so when `POST /api/History/retrieveBars` stalls — as it did on 2026-05-21 EDT (last bar frozen at `12:30Z` / 08:30 EDT for 75+ minutes for MNQ/MES/MGC) — strategies still see fresh data. Disable with `EXECUTOR_MARKET_HUB=false` (e.g. backtests, dev without SignalR). `ENABLE_SIGNALR=false` also short-circuits the wiring. The freshness guard (`signal.max_bar_staleness_seconds`, default 600s) remains the last-line scream when *both* REST and the live feed go dark. Tests: `tests/test_live_bar_pipeline.py` (14 tests covering callback fan-out, cache merge, env toggles) + `tests/test_morning_range_reversion_smoke.py::test_freshness_guard_*`. Verify the live feed off-process with:

```bash
ENABLE_SIGNALR=false .venv/bin/python -c "
import asyncio
from trading_bot import TopStepXTradingBot
async def main():
    bot = TopStepXTradingBot()
    await bot.initialize()
    bars = await bot.get_historical_data('MNQ', '5m', limit=5)
    for b in bars: print(b['timestamp'], 'C', b['close'])
asyncio.run(main())"
```
If the last timestamp is more than ~5 minutes behind wall clock during market hours **and the live cache is also empty** (check executor log for `📡 Market Hub wired for N symbol(s)` on startup), the broker is the problem on both paths — restarting the executor is the last resort. Tests: `test_freshness_guard_*` in `tests/test_morning_range_reversion_smoke.py`.

### Strategy loop is event-driven, not polling — `analyze()` fires per bar close

The `StrategyManager._run_strategy` loop **does not poll on a fixed 60 s timer anymore** (since the wake-driven refactor above). Each strategy blocks on an `asyncio.Event` that is set by the bar aggregator the instant a bar closes for its `(symbol, timeframe)` pair, with a fallback timeout of `STRATEGY_LOOP_MAX_INTERVAL_SEC` (default **5 s**). Implications:

- **`analyze()` runs ~250–500 ms after a real-world bar close**, not up to 60 s later. The previous behaviour is gone — do not assume the loop sleeps 60 s between iterations.
- **REST traffic is lower, not higher.** A 5m strategy polls historical bars roughly every 5 minutes (driven by closes), not every 60 s. The 5 s ceiling only fires during SignalR outages or dead hours.
- **A misbehaving `analyze()` blocks its own loop.** If a strategy's `analyze()` takes 10 s, the wake event will pile up but only the first wake matters (`asyncio.Event.set()` is idempotent). The next iteration handles it. No bar closes are lost.
- **The clear-before-wait ordering** in `_run_strategy` is intentional: bar closes that arrive *during* `analyze()`/`execute()` are captured for the next iteration. Don't move the `wake_event.clear()` to after the wait — that would silently drop concurrent wakes.
- **Strategies without `self.timeframe`** wake on every bar close for their symbols. Harmless (`should_trade()` still gates), but noisier. Declare `self.timeframe` explicitly.
- **Tuning**: set `STRATEGY_LOOP_MAX_INTERVAL_SEC=1` for snappier outage detection (you'll see more REST calls during outages but the freshness guard still fires before damage). Set higher (e.g. `30`) if you want to deliberately tick housekeeping less often. Setting to `0` falls back to default 5 s (would otherwise busy-spin).

- Citation: [config/strategies/morning_range_reversion.toml](../config/strategies/morning_range_reversion.toml), [strategies/morning_range_reversion_strategy.py](../strategies/morning_range_reversion_strategy.py).

### Ops / deploy hygiene

- **Legacy cron → webhook**: If an old machine still runs `curl` to a retired Railway URL on a schedule, remove the line from `crontab -e` (or launchd plist) so you are not hammering a dead endpoint. Railway: cancel the project in the Railway dashboard when decommissioning; env vars there are not auto-deleted from your shell profile.
- **Discord heartbeat**: Optional `DISCORD_STATUS_INTERVAL_SECONDS` (default **3600** in run scripts) + `DISCORD_WEBHOOK_URL` sends a status digest with account stats and an ET **daily strategy briefing** (`DISCORD_STATUS_INCLUDE_DAILY=1`, default on). See [core/discord_status_digest.py](../core/discord_status_digest.py).
- **Discord trade/signal tiers**: `DISCORD_NOTIFY_SIGNALS` (default **on** when unset), `DISCORD_NOTIFY_ORDERS` (default off), `DISCORD_NOTIFY_FILLS`, `DISCORD_NOTIFY_FEED`. Heartbeat ignores these flags. Quoted values like `"1"` are accepted.

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
