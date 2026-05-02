# Changelog

Rolling log of substantive repo changes. New entries on top. Every PR that
changes runtime behavior or conventions adds an entry here AND updates
`docs/HANDOFF.md` "Last verified" header.

## [Unreleased]

### Fixed
- **`strategies/overnight_range_strategy.py`** / **`core/backtest/strategy_replay.py`** — CSV replay: (1) **`analyze()`** reused **`active_ranges`** without calling **`track_overnight_range()`**, so the **first** session’s overnight high/low was reused for later days (identical **`entry_price`** on every trade and bogus 1‑bar exits). **`analyze()`** / **`calculate_range_break_orders()`** now always **`await track_overnight_range()`**; cache/inflight returns from **`track_overnight_range`** also refresh **`active_ranges`**. (2) When one simulated bracket **entry** fills, cancel the **opposite** pending entry stop for the same symbol (long+short staging). (3) Earlier: **`_is_strategy_replay`** gates **`analyze()`** to the open window, **`skip_weekdays`**, one successful place per session; **`monitor_breakout_levels`** weekday skip for live.
- **`core/backtest_executor.py`** — **`MockTradingBot`** sets **`_is_strategy_replay = True`** for replay runs.
- **`core/risk_management.py`** — Pending-entry counts use **`_order_counts_as_working_entry_for_risk`**: exclude **filled / cancelled / rejected / expired** (numeric **2/3/4** and matching strings), **fully filled** legs, and numeric statuses **not** **0/1**. **`SUSPENDED`** is **included** as working for **`stop_bracket`** entries (broker often reports active brackets suspended until the stop leg is elected). Still fixes false “N pending” from **terminal** or **fully filled** rows mis-tagged as entries.
- **`core/user_hub_manager.py`** — SignalR **`on_reconnect`** / **`onreconnected`** callbacks use **`lambda *args, **kwargs`** so transport ping/reconnect does not raise **`TypeError`** (`connection_id`).
- **`core/strategy_executor.py`** — **`ws_connect`** uses **`ClientWSTimeout(ws_close=…)`** instead of deprecated **`ClientTimeout`** for the handshake.
- **`core/auth.py`** / **`brokers/topstepx_adapter.py`** — **404** on `GET /api/Position/{id}` (closed/stale position) logged at **debug** instead of error spam.
- **`core/strategy_executor.py`** — Each **`_update_process_state`** heartbeat writes **`metadata.or_ranges`** (from live `active_ranges`) into **`process_states`** so the Master GUI chart can draw OR highs/lows without relying on **`strategy_states`** throttling alone.
- **`gui/chart_html.py`** — **`_overnight_or_ranges_with_executor_fallback`** prefers **`process_states.metadata.or_ranges`** for running executors, then **`strategy_states`** (TTL widened to **120s** for DB fallback). Chart **WebSocket** `pong` send tolerates **closing transport** (`ClientConnectionResetError`). **`POST /api/chart/strategy/stop`**: when the strategy is **not** in the chart process’s `active_strategies`, persist **`enabled=False`** in `strategy_states` (optional `account_id` from the GUI) so a **`strategy_executor`** can stop via **`_sync_persisted_disable_flags`** on heartbeat. External **`overnight_range` details**: **`or_ranges`** fallback from any **fresh** `strategy_executor` row running `overnight_range` when the selected account has no snapshot (mixed GUI vs executor account). **In-process** `overnight_range` details (idle instance while executor runs live) **merge DB `or_ranges`** into **`ranges`** so OR price lines render on Master. **`GET /api/chart/orders`** treats **`SUSPENDED`** as terminal (exclude from working-order payload).
- **`gui/master_control.html`** — Chart toolbar **popout** buttons: removed **Chart**, order **Activity → Performance → Strategies → Terminal → Tradeslog**. **`orderIsTerminalStatus`**: **`SUSPENDED`** so ghost suspended rows do not drive counts, tables, or chart order lines. **`updateOvernightRangePriceLines`**: treat **`strategies.overnight_range.active`** as on (same as top-level **`active`** list).
- **`core/strategy_executor.py`** — Heartbeat polls **`strategy_states.enabled`** and stops matching strategies; shutdown **`await`s `_disconnect_from_gui_websocket`** to avoid **unclosed aiohttp ClientSession**.
- **`gui/master_control.html`** — Default **panel visibility** = **chart only** (new visits / cleared `master-panel-visibility`). Toolbar **Chart** / **Strategies** / **Terminal** quick actions (Strategies & Terminal use the same **popout** pattern as Activity / Performance / Tradeslog). **Command** input/output use a **black** background. Bar countdown **`right: 86px`**. **Stop** sends **`account_id`** from the account dropdown when persisting external stops.
- **`gui/master_control.html`** — OR high/low: resolve `ranges` keys when DB uses contract-style symbols vs chart dropdown (`pickOvernightRangeForSymbol`); bust browser cache on details fetch. **Activity** / **Performance** / **Tradeslog** open compact **popout** panels (Escape / backdrop / × to close) with live positions, working orders, metrics, and recent trades. WebSocket handlers refresh orders on **`order_updated`** / fills / **`position_closed`**. **`core/state_cache.py`** — `get_orders` normalizes `account_id` to string so invalidation matches cache keys.
- **`gui/chart_html.py`** — `GET /api/chart/orders` drops **terminal** orders after normalization (filled/cancelled and full fillVolume) so ghost lines/rows do not return from the HTTP handler. **`handle_strategy_status`** — external `strategy_executor` **start/runtime** merged into the strategy card even when the same strategy is loaded locally; fixed external-only branch `continue`/variable bugs. **`handle_strategy_details`** (external `overnight_range`) parses `settings` JSON strings and fills **start_time** / **runtime** from `last_started`.
- **`strategies/overnight_range_strategy.py`** — `or_ranges` snapshot also stores a **short symbol alias** (e.g. `MNQ`) when keys are contract-style.
- **`core/event_bus.py`** — `publish()` now auto-starts the bus when it was never started (e.g. chart server / partial boot with live SignalR). Stops “EventBus not running, event dropped” and restores `state_cache` invalidation driven by hub events.
- **`gui/chart_html.py`** — TopStepX order **status** normalization: numeric **2 = FILLED**, **3 = CANCELLED**, **4 = REJECTED** (was incorrectly mapped to `SUSPENDED`, so filled stops stayed on the chart). **`GET /api/chart/strategy/details/overnight_range`** returns **200** with DB-backed `ranges` / `or_ranges` when no in-process strategy (headless `strategy_executor`); other unknown names return 200 with empty payload instead of 404.
- **`strategies/overnight_range_strategy.py`** — After computing `active_ranges`, throttled snapshot of OR high/low is written into `strategy_states.settings.or_ranges` so the dashboard chart can draw OR lines for the same account without loading the strategy in the chart process.
- **`gui/master_control.html`** — Dashboard script runs inside an IIFE, so `onclick="placeOrder(...)"` and similar attributes could not see handlers (`ReferenceError`). All inline handlers are now assigned onto `window`. Malformed account-bar markup (toast and P&L blocks nested incorrectly) is corrected so the account strip and layout behave. Removed eager `Notification.requestPermission()` on load (Safari requires a user gesture). Real-time chart polling now passes `timeframe` to `/api/chart/quote` so live bar buckets match the selected TF (was always 1m). **Chart:** grid lines hidden; time-axis tick labels use **time-only** on intraday ticks and **date** on day/month ticks; crosshair time label shows **date only**; OHLCV legend shows crosshair bar **date**; **in-page fullscreen** for the chart (`Escape` exits). **Header:** panel visibility checkboxes (persisted), tab **uptime** + last-**WS** activity. **Orders:** terminal-status rows filtered client-side; order price lines clear `orderLineDataMap` orphans so cancelled orders drop off the chart. **Removed** pop-out buttons and `/popout` wiring. **Overnight range** high/low render as solid purple price lines when `overnight_range` is active (in-process or DB snapshot).
- **`gui/chart_html.py`** — Quote endpoint accepts `timeframe` / `tf` and aligns synthetic `latest_bar` to that bar period (fixes standalone chart real-time updates on 5m/1h/etc.). Standalone chart time axis / crosshair date formatting aligned with master dashboard; OR high/low lines use solid strokes. **`GET /api/chart/orders`** supports `no_cache=1` to bypass the short HTTP response cache; **order cache + `state_cache.invalidate_orders`** run after place / cancel / cancel-all / flatten so the chart UI does not show stale working orders.
- **`strategies/overnight_range_strategy.py`** — Cross-midnight `overnight_start` / `overnight_end` (e.g. 18:00→09:29 ET) used the wrong calendar days when local time was **before** the morning cutoff (e.g. 03:25 ET), shifting the fetched window back two days and corrupting range highs/lows. Logic now distinguishes (1) after evening start → today→tomorrow window, (2) after morning end → yesterday→today completed window, (3) midnight→morning end → in-progress window ending today. Session end includes the full end minute for 1m bar alignment. Range cache keys use the session’s morning `end_date`, with a 45s in-flight TTL until the window is finished.
- **`brokers/topstepx_adapter.py`** — Python stop-bracket path no longer uses bare `print()` (so messages pick up logging timestamps); one INFO line documents SL/TP tick sizes, absolute tick counts, and that SELL take-profit ticks are negative when TP is below entry (TopStepX convention). Success and failure logs include **`wall_start_utc`** and **`round_trip_ms`** for the Python bracket path.

### Changed
- **`docs/OVERNIGHT_RANGE_RESEARCH.md`** — §0 documents **`overnight_range`** session cap, **`skip_weekdays`**, and filter impact on trade frequency vs prop cashflow goals; **`config/strategies/overnight_range.toml`** — `[filters]` comments for research presets; **`docs/BACKTESTING.md`** / **`docs/ALPHA_DISCOVERY.md`** — cross-links on low replay trade counts vs filter TOML.
- **`core/backtest/models.py`** — **`BacktestResult.to_dict(include_trades=…)`** and **`BacktestTrade.to_json_dict()`** for machine-readable round-trips.
- **`scripts/run_overnight_range_csv_chunks.py`** — walks a CSV’s calendar in **1–N day** inclusive chunks (default **30**), writes **`docs/perf/overnight_range_MNQ_1m_chunks.json`** with per-chunk metrics, **`trades`**, and top-level **`all_trades`**; defaults to **`--include-trades`**, prints per-chunk and combined trade tables (**`--omit-trades`** / **`--no-print-trades`** optional).
- **`config/strategies/overnight_range.toml`** — Phase **1.1** filters enabled with tighter range/gap/ATR bands; **`filters.skip_weekdays`** **[0, 4]**; **`timing.replay_order_window_minutes`** for CSV replay cadence (see **`docs/STRATEGY_IMPROVEMENT_PLAN.md`**).
- **`config/strategies/_schema.toml`** — documents **`filters.skip_weekdays`** and **`timing.replay_order_window_minutes`**.
- **`gui/master_control.html`** — **`--chart-canvas-bg`** (**Chart canvas** in Theme): **`#chart-container`** + LWC **layout.background** (default **`#000000`**); pickers / session / presets keep it in sync. **Theme** presets: earth grid, khaki·almond, apricot·rose, modern neutral, neutral boho, neutral summer, **`lavender-house`** (screenshot UI + black canvas). Single **`.top-command-bar`**; crosshair/legend **date + time to seconds**; last bar **close · countdown** + **`lastValueVisible: false`** on candles.
- **`config/page_theme.template.json`** / **`config/chart_theme.template.json`** / **`scripts/init_*_theme_template.py`** — **`--chart-canvas-bg`** in page template; chart template default pane **`#000000`**.
- **Master page theme** — [`config/page_theme.template.json`](../config/page_theme.template.json), optional gitignored **`config/page_theme.json`**, **`GET /api/chart/theme/page`**, and [`docs/PAGE_THEME_OPTIONS.md`](PAGE_THEME_OPTIONS.md); [`scripts/init_page_theme_template.py`](../scripts/init_page_theme_template.py) regenerates the template; Master loads merged variables on startup (`applyPageThemeFromServer`).
- **`strategies/overnight_range_strategy.py`** — Startup **Session Time** line shows a **dated** US/Eastern overnight window (e.g. ``Apr 30 6:00 PM → May 1 9:29 AM US/Eastern``) instead of bare clock spans.
- **`gui/master_control.html`** — Chart legend shows **Now:** (local wall clock) on crosshair move next to the bar date.
- **Docs / tooling** — [`docs/CHART_LIGHTWEIGHT_OPTIONS.md`](CHART_LIGHTWEIGHT_OPTIONS.md) (LWC option map), [`config/chart_theme.template.json`](config/chart_theme.template.json), [`scripts/init_chart_theme_template.py`](scripts/init_chart_theme_template.py) to regenerate the template.
- **`gui/chart_html.py`** — When `overnight_range` is in the strategy status `active` list, the chart fetches `/api/chart/strategy/details/overnight_range` and draws OR high/low plus long/short entry, SL, and TP as price lines (alongside orders/positions). Strategy details JSON includes `session_start_et` / `session_end_et` on each symbol range for debugging.
- **`core/backtest_executor.py`** — CSV loads honor `--start` / `--end` (inclusive calendar filter on the CSV index); mock bot adds `get_open_orders`; `_run_strategy_replay` passes `replay_timeframe` into `StrategyReplayEngine.replay`.
- **`core/research/runner.py`** — optional `--csv`, `--csv-start`, `--csv-end` to run grids + OOS + MC on exported OHLCV instead of synthetic sample data; warns on large replay bar counts.
- **`core/backtest/strategy_replay.py`** — `get_historical_data` mock accepts `**kwargs` and forwards them when delegating; serves minute bars from the replay list when the strategy requests another minute timeframe (e.g. 5m vs 1m CSV); fixes replay kwargs errors with `overnight_range`.
- **`docs/BACKTESTING.md`** — short “Timestamps (CSV vs Eastern sessions)” note: naive CSV times are interpreted as UTC in replay/strategy code; `overnight_range` uses `US/Eastern` for session logic.
- **`docs/BACKTEST_RESEARCH.md`** — CSV research mode + link to `scripts/mnq_1m_research_sweep.sh`; caveat on `overnight_range` replay vs `alpha_discovery` on 5m.
- **`historical_data/csv_merger.py`** — timestamps parsed as UTC (`format="mixed", utc=True`) then stripped to naive UTC so merging CSVs with mixed tz-aware/naive columns no longer fails on `sort_values`.
- **`historical_data/csv_merger.py`** — warns when input files’ median bar spacing differs sharply (likely mixed 1m/5m); public `normalize_ohlcv_columns()` for reuse by the resampler.
- **`scripts/fetch_history_csv.sh`** — prefers repo `.venv/bin/python` when present and executable, falls back to `python3` (avoids missing `aiohttp` / venv deps when exporting history).
- **`scripts/export_history.py`** — optional `--chunk-days N` walks the requested date range in N-day windows (each call still obeys the ~20k bar API cap), dedupes by timestamp, writes one CSV. Warns on single-request responses that hit the cap.
- **`scripts/alpha_discovery.py`** — `--oos-split` no longer slices *bars* (which broke daily ATR / session features). One full `AlphaScanner.run()` on the CSV; IS/OOS hypothesis tests use the first / last fraction of **sessions** via `AlphaScanner.hypothesis_battery()` on merged subsets (`core/alpha/scanner.py`).

### Added
- **`docs/RESEARCH_PATHWAYS.md`** — operator map: CSV replay covers full `--start`/`--end` slice (no hidden bar cap); chunk JSON rollup ≠ single continuous run; multi-symbol same-window export; Pathway 1 (`overnight_range` filter order) + Pathway 2 (other strategies on same CSVs). **`scripts/export_history_parallel_range.sh`** — loops `export_history.py` for **`SYMBOLS`** (default MNQ MES MGC) with shared **`START`/`END`/`TF`/`CHUNK`**.
- **`gui/chart_html.py`** — **`generate_chart_html(..., trade_overlays=[...])`** embeds LWC **markers** + **price lines** for static trade review pages; **`applyTradeOverlays()`** also runs after **“Load All Bars”** in backtest test mode.
- **`scripts/render_trade_review_charts.py`** — slices **`--csv`** OHLCV around each trade’s entry/exit (padding), writes per-trade **`.html`** via **`generate_chart_html`**, optional **matplotlib** **`.png`**; reads **`result.trades`**, **`trades`**, or **`all_trades`**. See **`docs/OVERNIGHT_RANGE_RESEARCH.md`** §2.2 and **`docs/perf/trade_review/README.md`**.
- **`scripts/format_backtest_json.py`** — pretty-print or export **`--format=json`** backtest blobs (**`--summary`**, **`--trades-only`**, **`--trades-csv`**, **`--trades-md`**); stdin / file / `-`. Documented in **`docs/OVERNIGHT_RANGE_RESEARCH.md`** §2.1.
- **`core/backtest_executor.py`** — **`--include-trades`** with **`--format=json`** embeds each completed **`BacktestTrade`** (entry/exit ISO UTC timestamps, prices, PnL, **`exit_reason`**, etc.).
- **`docs/OVERNIGHT_RANGE_RESEARCH.md`** — operator workflow: data → CSV replay → **`alpha_discovery`** → research runner; links the improvement plan.
- **`scripts/backtest_overnight_csv.sh`** — convenience wrapper for **`overnight_range`** CSV replay (**`START`/`END`/`CSV`/`TIMEFRAME`** env vars).
- **`scripts/mnq_1m_research_sweep.sh`** — example driver for `python -m core.research.runner` on `MNQ_1m_complete.csv` (MA grid + MC per calendar month).
- **`historical_data/resample_ohlcv_csv.py`** — OHLCV CSV resampler (e.g. 1m→5m: open=first, high=max, low=min, close=last, volume=sum) so stitched archives use one timeframe before `csv_merger`.
- **Alpha discovery system** — [`core/alpha/`](../core/alpha/) module: `SessionFeatureExtractor` (18 per-session features computed at signal time, no lookahead), `AlphaScanner` (overnight-range trade simulation + hypothesis tests), `AlphaReport` (markdown + JSON output); statistical framework: Kruskal-Wallis / Mann-Whitney U + Benjamini-Hochberg FDR correction across all features simultaneously. CLI entry point: [`scripts/alpha_discovery.py`](../scripts/alpha_discovery.py) — supports CSV/API data, IS/OOS split, multi-symbol, SL/TP sensitivity grid. Process documentation: [`docs/ALPHA_DISCOVERY.md`](ALPHA_DISCOVERY.md).
- **CLAUDE.md** — agent bootstrap file at repo root; auto-loaded by Claude Code every session with full architecture summary, golden rules, and post-change checklist.
- **Session rollover fix** — `_cancel_previous_session_orders()` in [`overnight_range_strategy`](../strategies/overnight_range_strategy.py) cancels stale prior-session orders at start of `_execute_market_open_sequence`.
- **Breakout order fast-path** — `_ensure_breakout_order` checks `breakout_active_orders` cache before calling risk manager, eliminating 280+ blocked-check log spam per session.
- **Risk check log level** — demoted per-order examination lines from INFO to DEBUG in [`core/risk_management.py`](../core/risk_management.py).


- **Research / risk / ops** — [`core/backtest/monte_carlo.py`](../core/backtest/monte_carlo.py) `simulation_mode=bootstrap`;
  correlated same-direction cap across MNQ/MES/MYM/M2K in [`StrategyRiskManager`](../core/risk_management.py) (`CORRELATED_EXPOSURE_GUARD`, `CORRELATED_MAX_SAME_DIRECTION_CONTRACTS`);
  [`core/market_calendar.py`](../core/market_calendar.py) NYSE full-closure guard for [`overnight_range_strategy`](../strategies/overnight_range_strategy.py);
  Discord inactivity streak via [`alerts.discord_after_zero_trade_sessions`](../config/strategies/overnight_range.toml) + [`SessionTradeTracker`](../core/session_trade_tracker.py) / [`DiscordNotifier.send_inactivity_alert`](../core/discord_notifier.py);
  [`OrderExecutor.close_position_partial`](../core/order_execution.py); `[trend_filter]` in [`config/strategies/simple_candle.toml`](../config/strategies/simple_candle.toml).
- **Alembic baseline** — [alembic.ini](../alembic.ini), [migrations/env.py](../migrations/env.py),
  [migrations/versions/001_baseline_noop.py](../migrations/versions/001_baseline_noop.py); `sqlalchemy` + `alembic` in
  [requirements.txt](../requirements.txt). Runtime DDL still from `DatabaseManager`; use revisions for additive changes.
- **Remote emergency CLI** — `POST /api/remote_command` on [servers/async_webhook_server.py](../servers/async_webhook_server.py)
  when `REMOTE_COMMAND_SECRET` is set (header `X-Remote-Command-Secret`, JSON `{"command":"…"}`); documented in
  [README.md](../README.md), [.env.example](../.env.example), [scripts/slim_env.py](../scripts/slim_env.py).
- **`simple_rth` strategy** — [strategies/simple_rth_strategy.py](../strategies/simple_rth_strategy.py),
  [config/strategies/simple_rth.toml](../config/strategies/simple_rth.toml), registered in
  [strategies/strategy_manager.py](../strategies/strategy_manager.py) (disabled by default; RTH-hours momentum via same engine as simple_momentum).
- **Backtest shell helpers** — [scripts/backtest_symbol.sh](../scripts/backtest_symbol.sh),
  [scripts/backtest_thorough_symbol.sh](../scripts/backtest_thorough_symbol.sh),
  [scripts/fetch_history_csv.sh](../scripts/fetch_history_csv.sh); [historical_data/.gitkeep](../historical_data/.gitkeep);
  [README.md](../README.md) explains synthetic vs API vs CSV data sources.
- **Strategy development decision tree** — [docs/STRATEGY_DEVELOPMENT.md](STRATEGY_DEVELOPMENT.md) (mermaid flow +
  links to backtest / research / perf docs).
- **Backtest executor CLI polish** — [core/backtest_executor.py](../core/backtest_executor.py): `--list-strategies`,
  `--format=json` (machine-readable summary + optional MC, no decorative stdout), epilog examples; expanded
  [docs/BACKTESTING.md](BACKTESTING.md).
- **Research runner (grid + OOS + MC)** — [`core/research/runner.py`](../core/research/runner.py),
  [`docs/BACKTEST_RESEARCH.md`](BACKTEST_RESEARCH.md), [`scripts/research_screen.sh`](../scripts/research_screen.sh),
  [`tests/test_research_runner.py`](../tests/test_research_runner.py). Optional walk-forward folds and slippage
  sensitivity table; promoted runs persist extra fields into `strategy_performance.metadata` via
  `save_strategy_metrics`.
- **Contract refresh single-flight** — [`asyncio.Lock`](../trading_bot.py) on
  `TopStepXTradingBot.get_available_contracts` and [`TopStepXAdapter.get_available_contracts`](../brokers/topstepx_adapter.py)
  with double-check after wait to collapse concurrent cache misses.
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
- **Silent exception sites** — [trading_bot.py](../trading_bot.py) and [gui/chart_html.py](../gui/chart_html.py) log
  suppressed failures at DEBUG with stack traces; removed duplicate `CancelledError` handlers in the chart WebSocket
  broadcast loop.
- **Historical fetch Parquet cache key** — [brokers/topstepx_adapter.py](../brokers/topstepx_adapter.py)
  imports `dumps_bytes` from [core/json_fast.py](../core/json_fast.py) for `_historical_parquet_path`; the missing
  name raised `NameError` on every `get_historical_data` call when Parquet cache was enabled, so strategies saw
  zero bars (e.g. overnight range could not compute levels at market open).
- **`TopStepXAdapter` HTTP passthrough** — [brokers/topstepx_adapter.py](../brokers/topstepx_adapter.py)
  `_make_request` is now `async` and `await`s [AuthManager._make_request](../core/auth.py) (aiohttp),
  with all call sites awaited; restores contracts cache, positions, orders, and history. Removed
  `from datetime import …, time` which shadowed the `time` module and broke `time.time()` /
  `time.perf_counter()` in the same file.
- **Interactive `master` / `gui`** — [core/trading_interactive_ui.py](../core/trading_interactive_ui.py)
  passes `bot` into `CLICommandParser` instead of undefined `self`.

### Changed
- **Dashboard WebSocket parity** — [gui/master_control.html](../gui/master_control.html) handles Railway/async webhook
  message types (`account_update`, `position_update`, `order_update`, `risk_update`, `metrics_update`) in addition to
  the local chart server types.
- **Live stop-adjust visibility** — broker `modify_stop_loss` failures for trailing stops and overnight breakeven log at
  **WARNING** instead of DEBUG ([strategies/trend_following_strategy.py](../strategies/trend_following_strategy.py),
  [strategies/overnight_range_strategy.py](../strategies/overnight_range_strategy.py)).
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
- **More batch position+order snapshots** — [servers/websocket_server.py](../servers/websocket_server.py) welcome +
  periodic broadcast, [servers/async_webhook_server.py](../servers/async_webhook_server.py) test handler,
  [strategies/overnight_range_strategy.py](../strategies/overnight_range_strategy.py) breakout pre-check, and
  [core/bracket_orders.py](../core/bracket_orders.py) `adjust_bracket_orders` use
  `get_positions_and_orders_batch`; [trading_bot.py](../trading_bot.py) cached account-info path uses the same.
- **Hot-path logging** — demoted repetitive adapter `INFO` lines for empty orders/positions and order fetch to
  `DEBUG`; [trading_bot.py](../trading_bot.py) open-positions count log to `DEBUG`. Order/trade history fetch,
  Rust quote/depth timing, and contract list refresh logs in [brokers/topstepx_adapter.py](../brokers/topstepx_adapter.py)
  moved to `DEBUG` where appropriate; Parquet cache digest uses [core/json_fast.py](../core/json_fast.py) `dumps_bytes`.
- **Sample data pandas freq** — [core/backtest/data_loader.py](../core/backtest/data_loader.py) uses `Timedelta`
  instead of deprecated minute offset `"T"` for `pd.date_range` (pandas 2.2+).
- **Function-strategy sample/CSV data shape** — [core/backtest_executor.py](../core/backtest_executor.py) keeps a
  **DataFrame** (with indicators) for `ma_crossover` / `rsi_mean_reversion` / `ema_trend`; list-of-dicts conversion
  applies only to replay/class strategies so `BacktestEngine.run` is not given a Python list by mistake.
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
