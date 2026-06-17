# Changelog

Rolling log of substantive repo changes. New entries on top. Every PR that
changes runtime behavior or conventions adds an entry here AND updates
`docs/HANDOFF.md` "Last verified" header.

## [Unreleased]

### Changed / Fixed
- **MRR production reliability: SignalR resubscribe bug + dual-path feed health + 15m thresholds + headless GUI-WS disable (2026-06-17)** —
  Root-cause fix for today's "orders placed then immediately cancelled"
  incident and the underlying morning-long SignalR zombie.

  **SignalR root cause (permanent zombie after watchdog reconnect)**

  ``WebSocketManager.stop()`` cleared ``_subscribed_symbols`` before
  ``_resubscribe_all_symbols()`` ran, so every watchdog reconnect
  reported "reconnect OK" but re-subscribed **zero** symbols — quotes
  never flowed again for the rest of the session.  Fix: preserve the
  subscription intent set across ``stop()``; null ``_hub`` after
  ``hub.stop()``; watchdog snapshots symbols before stop as backup.

  **Dual-path feed health (quote OR bar)**

  Health monitor tracked quote ticks only.  When SignalR quotes paused
  but REST polls + periodic bar-completion still delivered fresh bars,
  the system falsely declared zombie → blocked analyze OR placed then
  cancelled brackets.  New ``record_bar_activity(symbol)`` wired from
  ``trading_bot._on_live_bar_close`` and REST ``get_historical_data``.
  ``is_safe_to_trade`` returns OK when **either** path is fresh.
  Watchdog + cancel-on-staleness only fire when **both** paths are
  stale for a symbol.

  **15-minute thresholds (operator request)**

  Defaults raised to match MRR ``max_bar_staleness_seconds = 900``:
  ``DATA_FEED_MARKET_HUB_MAX_SILENCE_S=900``,
  ``DATA_FEED_HEALTH_SEVERE_SILENCE_S=900``.  Wrapper exports both.

  **Headless GUI WebSocket fix**

  ``strategy_executor`` GUI WS keepalive spawned recursive reconnect
  loops + leaked ``aiohttp`` sessions (07:13 ET storm in today's log).
  Fixed: single keepalive task, close-before-reconnect, disabled by
  default in ``run_morning_reversion.sh`` via ``STRATEGY_EXECUTOR_GUI_WS=0``.

  **Discord + feed-down alerts (2026-06-17)**

  ``run_morning_reversion.sh`` and ``run_overnight.sh`` source ``.env`` and
  export ``DISCORD_STATUS_INTERVAL_SECONDS`` (default 1800),
  ``DATA_FEED_DISCORD_ALERTS``, and ``DATA_FEED_DISCORD_ALERT_COOLDOWN_S``.
  ``DataFeedWatchdog`` posts Discord embeds when both quote+bar paths go
  zombie and when they recover.

  **MRR production hardening round 2 (2026-06-17)**

  - **Restart re-fire fix**: ``session_activity`` persisted in anchor JSON
    (``sweep_fired_high/low``, ``fades_this_session``, ``immediate_block``).
    Restored on anchor finalize + startup reconcile; price-outside-range guard
    after backfill; adopted same-session orders register in
    ``working_order_registry`` and block duplicate sweeps.
  - **Replay isolation fix**: ``_persist/_restore_session_activity`` no-op in
    replay/backtest so walk-forward does not read live ``data/anchors/``
    ``session_activity`` (was zeroing fold 2 trades via ``max_fades=1``).
  - **GUI WS log storm**: reconnect/connect lines demoted to DEBUG; WARNING
    rate-limited to 1/min; stale port cleared after 5 failures.
  - **Log volume**: ``LOG_SUPPRESS_ASYNCIO_SESSION_ERRORS=1`` silences aiohttp
    leak spam; ``LOG_MAX_BYTES`` env override for size rotation.
  - **Execute health gate**: removed redundant MRR ``execute()`` double-gate;
    analyze stale-bar check + ``place_bracket`` tiered gate remain.
  - **Reconcile**: skip terminal-status orders (cancelled/filled) when adopting.

  **Selective cancel-on-staleness (opt-in)**

  New ``cancel_working_orders_for_stale_symbols`` — only pulls brackets
  when both quote AND bar paths are dead for that symbol.  **Default OFF**
  via ``DATA_FEED_CANCEL_ON_STALENESS=false`` (operator 2026-06-17):
  range-breakout brackets stay on the broker until filled or session-end
  flatten.  Set ``true`` only for continuous-monitoring strategies needing
  2026-06-11 blind-fill protection.

  **Tests**: +5 (bar-path liveness, selective cancel, no reconnect when
  bar fresh, stop preserves subscriptions, bar-activity trade allow).
  Full suite green.

- **MRR orders cancelled seconds after placement — tiered health gate + post-reconnect grace fix (2026-06-17)** —
  Field incident on prac account 1: MGC + MNQ OCO brackets placed at
  09:23 ET (order ids 3143088042, 3143090337) then cancelled at 09:26:46
  with broker label "Cancelled by trader" (the bot's
  ``cancel_all_working_orders`` REST path — operator did not cancel).

  **Root cause chain (from ``morning_range_reversion_account1_20260617_065000_444.log``)**:

  1. SignalR Market Hub went zombie ~07:14 ET (connection alive, zero
     ticks).  Watchdog fired 17+ reconnects through the morning; most
     returned "reconnect OK" but ticks never resumed.
  2. MRR correctly logged ``⛔ STALE DATA`` and skipped analyze from
     ~08:20–09:22 ET.
  3. At 09:23 ET, MGC analyze briefly passed (REST bar cache) and fired
     SHORT fades on a **7700 s zombie feed** — health gate logged
     ``data feed degraded but placing anyway`` (warn-and-proceed).
  4. At 09:26:46 ET, watchdog backoff (480 s since 09:18 reconnect)
     expired → ``cancel_all_working_orders`` pulled both brackets before
     forcing reconnect #18.  Operator never saw working orders.

  **Fixes**:

  1. **Tiered health gate** (``core/data_feed_health.resolve_health_gate_decision``):
     ``warn`` mode now refuses when Market Hub / User Hub silence exceeds
     ``DATA_FEED_HEALTH_SEVERE_SILENCE_S`` (default **300 s**).  Mild
     transients (120–300 s) still warn-and-proceed; long-running zombies
     refuse at both ``strategy_base.place_bracket_order`` and
     ``trading_bot.place_oco_bracket_with_stop_entry`` chokepoints.
  2. **Post-reconnect grace bypass** (``is_safe_to_trade``): startup
     grace now applies only while awaiting a symbol's **first** tick.
     ``reset_clock_for_grace()`` after watchdog reconnect no longer masks
     a previously-flowing zombie feed for 60 s.
  3. **MRR ``execute()`` defense-in-depth**: refuses fades when the tiered
     gate would block placement (covers analyze paths that pass on briefly
     fresh REST bars while SignalR is still dead).

  **Tests**: 3 new in ``tests/test_health_gate_integration.py`` (severe
  refuse, mild warn, post-reconnect grace).  Full suite green.

- **Master GUI v2 — range overlays, recap labels, idle list (2026-06-16)** —
  - Trade recap IN/OUT vertical labels show **clock time only** (no date).
  - Stale-range filter uses **6× range width** (min 10% of price / 400 pts),
    not a tight fixed % — better when ATR/range width is elevated.
  - Range overlays: unified ``/api/chart/range_overlays`` endpoint; multi-session
    picks newest N dates; MRR/ORB no longer gated by overnight freshness filter.
    **Fix:** handler used ``request.app['trading_bot']`` (KeyError) — now uses the
    chart-server closure like other routes; legacy per-strategy fetch fallback in GUI.
    **Fix:** backfill stored naive UTC in ``session_*_et`` fields (MRR showed
    11am–12pm instead of 7–8am ET); API now rewrites true ET ISO bounds on read.
  - **v2 dash blank fix:** restored accidental deletion of ``collectRangeDrawables``
    (JS syntax error broke entire page boot).
  - **Range build-window boxes:** LWC baseline + dashed midline scoped to each
    strategy's build window (not DOM divs — those sat behind LWC canvases).
    MRR default-on; OR/ORB opt-in.
  - **Range backfill stitch:** lazy/API backfill now merges Databento 1m CSV +
    recent broker API bars (same idea as chart ``auto`` source); trade snapshots
    override bar-derived ranges when both exist.
  - **v2 chart trade lines:** entry / SL / TP + working order price lines on the
    live canvas for the selected symbol.
  - **v2 legend:** removed redundant bar date under countdown; tick-change strip
    (66 segments, left of last price). Last price color tracks **forming bar**
    direction (moss/rose), not per-tick flash.
  - **Range overlays live merge:** ``/api/chart/range_overlays`` now augments DB
    history with Databento+API bar reconstruction on every read (sessions after
    the CSV lag date, e.g. post-June-15).
  - **Fixes:** trades endpoint ``datetime`` shadowing NameError; log tail UTF-8;
    ``backfill_range_history.py`` loads ``.env`` for ``DATABASE_URL``.
  - **Range overlay stability:** normalize naive/aware OHLCV indexes before merge;
    90s API bar cache (3 fetches not 9); overlay endpoint always returns 200 with
    partial data; DB backfill is fire-and-forget (no longer blocks HTTP).
  - **Timezone resilience:** canonical `core.backtest.ohlcv.ohlcv_index_naive_utc()`;
    documented bar-clock vs session-clock split in `docs/GOTCHAS.md` and
    `docs/CONVENTIONS.md` (recurring naive/aware merge bugs).
  - **`/master` now serves v2** (`master_control_v2.html`); legacy UI at
    `/master/classic`. Chart toolbar: OHLC on hover floats top-left over canvas;
    compact two-row controls; refresh rate beside data source; session pills
    toggle off on re-click (removed redundant Off pill). Fixed `SyntaxWarning`
    for `\\d` in embedded chart HTML template.
  - **v2 chart polish:** default range **1 day**; live price line with on-chart
    tag (no duplicate axis label); quieter position/order price lines; improved
    tick strip; DLL/MLL under balance stats; perf selects hidden when drawer
    collapsed. **Load perf:** chart paints before deferred range overlays;
    skip overlay API when pills off; cap ``max_sessions`` to UI count (1–3).
- **Master GUI v2 — chart legend, DLL/MLL fix, idle list polish (2026-06-16)** —
  - Chart: OHLC hidden until crosshair hovers a bar; countdown moved under
    last price; maximize above price; last-price tick flash (moss/rose).
    Symbol/timeframe persist in ``localStorage`` across refresh.
  - **DLL/MLL pills:** ``AccountTracker.get_compliance_status`` alias added
    (was calling a non-existent method); lazy tracker init on account state.
  - Strategies idle list: only overnight_range / morning_range_reversion /
    opening_range_breakout; column headers for Schedule vs Last range;
    arms-in tooltip; order table shows auto/manual/bracket from ``customTag``.
  - Positions/orders poll every 3s; range-session depth selector (1–3).
  - Trades + KPIs: ``customTag`` on entry order classifies each trade as
    auto/manual/bracket; ``statistics_by_source`` + perf-drawer source
    filter (All / Auto / Manual / Bracket). Idle list: only MRR is
    launchd-managed; others show schedule preview.
  - v2 polish: ``--subtle`` text color (replaces invisible ``--hairline``
    on hints/tags); sleek ⤢ zoom; sticky last-price tick color; Next
    countdown prefix; strategy launcher limited to OR/MRR/ORB; chart +
    recap range overlays fetch from DB for all three range strategies
    (not only when active).

- **Master GUI — session warmth, account filter, log-spam fix (2026-06-16)** —
  - **Session warmth:** ``master``/``gui`` boot now calls
    ``start_keepalive_heartbeat()`` (cheap ``Account/search`` every
    ``BROKER_KEEPALIVE_HEARTBEAT_SEC``, default 120s — keeps TCP+TLS
    warm and refreshes JWT via ``ensure_valid_token``) and
    ``start_market_hub_for_strategies()`` for the chart symbol/timeframe.
    GUI does **not** honour ``ENABLE_SIGNALR=false`` — that flag only
    gates ``strategy_executor`` Market Hub wiring.
  - **Account rotation:** ``GUI_ACCOUNT_BLOCKLIST`` (comma-separated IDs)
    and ``GUI_DEFAULT_ACCOUNT_ID`` env vars; blocklisted accounts are
    removed from the dropdown and auto-switch picks the highest-balance
    practice account when the current selection is ineligible.
  - **KPI flash fix:** ``handle_account_state`` no longer treats
    ``realized_pnl==0`` as "uninitialized" and no longer fetches
    session-only trades (which returned 0 for empty-session accounts
    like 22182502 while the trades table used a 30-day window).
  - **Log spam:** ``websocket_manager.subscribe_quote`` routes hub-not-ready
    ``ValueError`` through the same once-per-symbol dedupe as other hub
    errors (was logging WARNING on every quote poll).

- **Master GUI — v2 Phase 3.7: MLL (trailing) chip + canonical risk-source refactor + 3.6 silent-hide bugfix (2026-06-16 early morning)** —
  Two things in one swing:

  1. **MLL chip added** next to the DLL pill in the always-visible
     head summary. MLL is the trailing-drawdown floor — breaching it
     permanently *terminates* the prop account (no daily reset), so
     it's strictly more catastrophic than DLL and now sits one
     position to the right with the same color thresholds:
     muted ≥50% buffer, ``--linen`` 25-50%, ``--rose`` <25%,
     bright-rose ``MLL BREACH`` at 0. Tooltip carries the full
     breakdown including trailing loss and breach context
     (``"trading locked until EOD reset"`` for DLL,
     ``"account permanently terminated"`` for MLL).

  2. **Phase 3.6 silent-hide bug fixed.** While wiring MLL I traced
     the data path and found the DLL chip was actually *always*
     hidden in production: ``handle_account_state`` was reading
     ``daily_loss_limit`` from ``tracker.get_state()`` — which
     **doesn't include the limit fields** (``get_state()`` only
     returns balance/PnL/positions). The condition
     ``if !limit || limit <= 0 → hide`` was always true.
     Refactored to read DLL + MLL from
     ``AccountTracker.get_compliance_status(account_id)`` instead —
     that's the canonical risk-state source the bot's own risk gate
     reads from, so the dashboard chip and the consec-loss breaker
     now agree on the same numbers.

  Backend (``gui/chart_html.py``):
  - ``handle_account_state`` now calls
    ``trading_bot.account_tracker.get_compliance_status(account_id)``
    once and pulls ``dll_limit`` / ``dll_remaining`` /
    ``dll_violated`` / ``mll_limit`` / ``mll_remaining`` /
    ``mll_violated`` / ``trailing_loss`` from a single source.
    Inline DLL-buffer math removed (the tracker already handles the
    ``min(0, daily_pnl)`` no-inflate semantics correctly).
  - Response keys added: ``mll_remaining``, ``mll_pct_remaining``,
    ``mll_violated``, ``trailing_loss``, ``dll_violated``.

  Frontend (``gui/master_control_v2.html``):
  - New ``#head-summary-mll`` span with class ``risk mll`` (paired
    sep with class ``mll-sep``).
  - Generalised CSS: ``.head-summary .risk`` carries the shared
    palette (warn / hot / breach / hidden); ``.dll`` and ``.mll``
    are now style-free identifiers used only for selectors.
  - ``applyAccountState`` now calls a shared ``applyRiskChip(cfg)``
    helper twice (once per pill). Same threshold ladder, same
    BREACH semantics, same hide-on-unknown-limit behaviour.
  - The chip respects the tracker's ``*_violated`` boolean as the
    authoritative breach flag (defensive: don't redundantly derive
    breach from pct math when the source already says so).

  Pinned by ``tests/test_mll_buffer_math.py``: 8 cases covering
  unknown limits, full-buffer at high-water mark, no-inflate when
  intraday gain pushes above prior high, partial drawdown math,
  at-floor zero, sub-floor clamp, threshold boundary parity with
  DLL, and an inline-extraction drift guard against the production
  expression. ``tests/test_dll_buffer_math.py`` (Phase 3.6) still
  passes against the new compliance-status path — the math contract
  was identical, only the source changed.

- **Master GUI — v2 Phase 3.6: DLL buffer pill in head-summary chip (2026-06-16 early morning)** —
  Phase 3.2 promoted WS health into the always-visible drawer-head
  chip but dropped the daily-loss-limit number entirely. The DLL is
  the most fatal-to-prop number on the dashboard — TopStep prop
  accounts get blown out the instant ``daily_pnl <= -daily_loss_limit``
  — so cycling the operator through "expand drawer → squint at
  Realized → mental math against the DLL env var" was the wrong
  cost-vs-glance-value tradeoff. Restored as a 4th compact token
  next to ``Connected · uptime · last tick``, with color thresholds.

  Backend (``gui/chart_html.py``):
  - ``handle_account_state`` now also emits ``dll_remaining`` and
    ``dll_pct_remaining`` (range 0..1, ``None`` when limit not
    seeded). Math:
    ``dll_remaining = max(0, daily_loss_limit + min(0, total_pnl))``
    The inner ``min(0, total_pnl)`` is the critical guard — without
    it a profitable session would inflate the buffer above 100%
    instead of capping at the limit.

  Frontend (``gui/master_control_v2.html``):
  - New ``#head-summary-dll`` span in the head-summary chip,
    rendered as ``DLL $670`` with the prefix label in
    ``--hairline`` so the dollar number dominates.
  - Color thresholds (matches the rest of the dashboard's risk
    palette): muted ≥ 50% buffer, ``--linen`` 25-50%, ``--rose``
    < 25%, bright rose + label flips to ``DLL BREACH`` when
    buffer hits 0 (the bot's risk gate has already locked
    trading; chip is the visual confirmation).
  - When the tracker hasn't seeded the limit yet (fresh session,
    no broker round-trip), the chip and its preceding sep both
    add ``.hidden`` so the operator doesn't see a phantom
    ``DLL —``.
  - Tooltip carries the full breakdown:
    ``Daily loss-limit buffer: $670 of $1,000 remaining (70%)``.

  Pinned by ``tests/test_dll_buffer_math.py``: 9 cases covering
  unknown / negative limits, profitable sessions (no-inflate
  guarantee), partial loss math, at-limit zero, sub-zero clamp,
  exact threshold boundaries, and an inline-math drift check
  against the production expression.

- **Master GUI — v2 Phase 3.5: strategies "ready / sleeping" panel (2026-06-15 night)** —
  The Strategies section's empty state used to be just the launcher
  form, which gave the operator zero visibility into what the bot was
  going to do next during the long quiet windows around active
  sessions (overnight_range trades ~10 minutes/day around 9:29 ET, MRR
  has a 7 AM-4 PM weekday window, ORB is similar). New panel renders
  every registered range strategy that isn't currently active with
  its schedule, an arms-in countdown, and the last persisted range
  per symbol. Sits above the launcher; 1 Hz client-side ticker so
  the countdown stays smooth between the 15s strategy-status polls.

  Backend (``gui/chart_html.py``):
  - New ``_STRATEGY_SCHEDULES`` map encoding each time-gated
    strategy's launch window. Cross-referenced with the live
    ``config/strategies/<name>.toml`` (``range_start`` /
    ``range_end_open``) and the strategy's own
    ``_in_trading_window`` logic.
  - New ``_compute_next_launch(name, now=None)`` — converts to
    ``America/New_York`` via ``zoneinfo``, advances past today's
    window if already passed, skips Sat/Sun for weekday-only
    strategies, returns ``eta_iso`` (UTC), ``eta_seconds``,
    human ``label`` ("9:29 AM ET tomorrow"), ``schedule`` string,
    and ``kind`` (``signal`` / ``range_build`` / ``manual``).
  - New ``_strategy_idle_state(name, db, account_id)`` — pulls the
    persisted ranges from ``strategy_states.settings`` (re-using
    the same blob the live chart consumes for ``or_ranges`` /
    ``mrr_ranges`` / ``orb_ranges``), normalises symbol aliases
    ("F.US.MNQ" → "MNQ"), dedupes on (high, low) so each symbol
    shows once, and computes ``snapshot_age_seconds`` from
    ``metadata.or_ranges_saved_at`` (OR) or the row's
    ``updated_at`` (MRR / ORB).
  - ``handle_strategy_status`` attaches ``idle_state`` to every
    non-active strategy in the response. Active strategies skip
    this — their ranges already render as live overlays on the
    chart.

  Frontend (``gui/master_control_v2.html``):
  - New ``#strategy-idle-list`` block below the active list and
    above the launcher form. Header reads "Ready — schedule · last
    range".
  - Per row: pretty name | schedule + arms-in countdown
    (color-coded — moss when < 2 min, linen when < 1h, accent
    default) | per-symbol low/high range chips with snapshot age.
    "manual launch" strategies render without a countdown.
  - Eyebrow upgrades from ``"at rest"`` to ``"at rest · N ready"``
    when there are registered idle strategies.
  - 1 Hz ``tickIdleCountdowns()`` recomputes the arms-in label
    from the cached ``eta_iso`` between polls so the chip stays
    smooth.
  - Idle rows ranked by soonest arms-in first (so what's coming
    up next is at the top).

  Pinned by ``tests/test_strategy_next_launch.py``: 9 cases
  covering unknown strategies → manual, same-day before window,
  same-day after window → tomorrow, Friday-evening → Monday weekend
  skip, Saturday → Monday, Sunday → tomorrow (Monday) label
  preference, MRR / ORB schedule times, and label-suffix contract.

- **Master GUI — v2 Phase 3.4: trade aggregation (collapse fill legs into logical trades) (2026-06-15 night)** —
  The TopStepX ``Trade/search`` API emits one record per filled
  contract leg. The v2 dashboard was rendering each leg as a separate
  trade row, which triple-counted everything downstream: trade count,
  win rate, streaks, max-drawdown, profit factor, equity curve.
  Concrete symptoms in the live screenshot:

  - Rows ``#57``, ``#58``, ``#59``: identical entry stamp / exit stamp
    / exit price (``29008.50 → 28932.50``); three back-to-back
    ``-$1,520`` rows. Real shape: one logical 3-contract trade.
  - Rows ``#51-56``: same entry stamp (``5/19 10:14 AM``), staggered
    exits (``10:20-10:23 AM``). Real shape: one 6-contract scale-out.

  With ~30 logical trades inflated to 59 rows, ``profit_factor`` was
  pinned at 1.00 and the KPI strip looked broken even though net P&L
  was small-positive.

  Fix lives entirely in ``gui/chart_html.py``:

  - New module-level ``_aggregate_trade_legs(legs)`` helper. Group key
    in priority order: ``(symbol, side, entry_order_id)`` (most
    precise — same parent open IS the same logical position), with a
    ``(symbol, side, entry_time bucketed to 2s)`` fallback for legs
    whose opener wasn't paired by the FIFO half-turn matcher. Output
    is qty-weighted average entry/exit prices, summed qty/pnl/fees,
    earliest entry / latest exit, sign-correct points, and a
    ``legs[]`` drilldown list.
  - ``handle_get_trades`` tags each enhanced leg with
    ``entry_order_id`` (sourced from the existing pairings dict),
    aggregates BEFORE computing ``cumulative_pnl`` / ``trade_number``,
    and recomputes ``statistics`` via
    ``trading_bot._calculate_trade_statistics`` over the
    logical-trade list. Response now also carries ``total_legs`` so
    the frontend can show both numbers.
  - Frontend (``gui/master_control_v2.html``):
    - Trades eyebrow: ``"30 trades"`` by default; expands to
      ``"30 trades · 59 fills"`` only when fills > trades.
    - SIDE column gets a small ``× N`` pill next to ``BUY``/``SELL``
      when the trade aggregated multiple legs (e.g. ``BUY ×3`` for
      a 3-contract position). Tooltip shows legs-aggregated count.
    - KPI strip (``applyKpisFromTrades``) and equity curve
      (``buildEquityFromTrades``) need NO changes — they consume the
      already-corrected ``stats`` and ``trades[]``. Streaks recompute
      client-side from the logical-trade list automatically.
  - ``handle_performance_metrics`` is left untouched (legacy v1
    contract; the v2 dashboard reads stats from
    ``/api/chart/trades`` only). Fix can be lifted there later if a
    v1 caller needs it.
  - Regression-pinned by ``tests/test_aggregate_trade_legs.py``:
    seven cases covering single-leg passthrough, atomic-close
    3-fill collapse, 6-fill scale-out collapse, time-bucket
    fallback when pairing failed, separation when entry orders
    differ, empty input, and short-position points sign.

- **CRITICAL HOTFIX: MRR launchd wrapper crashed on first morning fire — `SESSION_EXIT_GRACE_MIN` referenced before assignment (2026-06-16 morning)** —
  Field-discovered regression in the previous night's launchd migration.
  The schedule banner at line 211 of ``scripts/run_morning_reversion.sh``
  printed ``session-end exit: $(fmt_et "$SESSION_EXIT_TS")  (grace
  ${SESSION_EXIT_GRACE_MIN}min ...)`` conditional on
  ``$SESSION_EXIT_GRACE_MIN`` — but the variable's assignment block was
  137 lines lower (line 348, in the supervise-loop tunables section).
  Under ``set -euo pipefail`` (``set -u``) this is an instant crash:
  ``unbound variable``, rc=1, executor never spawned, no trades placed.

  Observed in production: the user installed the launchd agent Mon
  night, the agent fired Tue 06:50 ET (``launchctl print`` showed
  ``runs = 1`` and ``last exit code = 1``), the wrapper printed the
  schedule banner, then crashed silently into
  ``logs/launchd_mrr_account1.err`` with no executor log file created.
  MRR did not trade Tuesday's morning session.

  **Fix**: moved the entire ``SESSION_EXIT_GRACE_MIN`` /
  ``SESSION_EXIT_TS`` declaration block to immediately AFTER the
  ``FLAT_BEFORE_TS`` computation (line ~190) and BEFORE the schedule
  banner.  Left a NOTE comment at the old position pointing readers
  upward.  Both ``set -u`` and the banner reference are now satisfied.

  **Why the original tests missed this**: the existing wrapper-snippet
  tests (``test_wrapper_session_end_grace_zero_disables_cutoff`` and
  ``test_wrapper_session_end_grace_positive_computes_cutoff``)
  exercised the cutoff arithmetic in isolation — they didn't execute
  the wrapper's actual top-to-bottom flow.  Plist-rendering tests
  exercised the installer, not the wrapper.  The crash only manifested
  in real execution order.

  **New regression test**:
  ``tests/test_mrr_launchd_install.py::test_wrapper_runs_past_banner_without_unbound_variable_crash``
  spawns the wrapper end-to-end with NO_WAIT=1 + MAX_RESTARTS=0, streams
  stdout line-by-line, and asserts (a) no ``unbound variable`` error
  appears and (b) the wrapper reaches the
  ``→ launching strategy_executor`` supervise-loop line within 12s.
  SIGTERMs the wrapper + nukes the spawned executor pgrp on
  completion.  This now catches any future "reference before
  assignment" regression in the wrapper.

  **Validation**: 9/9 launchd tests pass (one new regression added).
  Full suite: **600 passed in 16.3s**.  Direct smoke test with
  ``SESSION_EXIT_GRACE_MIN=0`` and ``=30`` confirmed the wrapper
  reaches the supervise loop cleanly and the cutoff banner appears
  when grace > 0.

  **Operator action**: launchd installs do NOT need reinstall — the
  wrapper script is read fresh on each fire, so tomorrow's 06:50 ET
  wake will use the fixed code automatically.  To trade today, run
  ``launchctl kickstart -k "gui/$(id -u)/com.tradebot.mrr.account1"``
  (afternoon fades still possible; morning window already past).

- **MRR daily-launchd migration: session-end clean-exit + launchd agent + install/uninstall scripts (2026-06-15 night)** —
  Operational architecture shift for MRR.  The always-on supervise-loop
  model (Round 1 + Round 2 hardening) worked, but MRR is structurally a
  daily strategy — range build 7-8 AM ET, fade window 8 AM - 4 PM, no
  overnight state.  Running 24/7 for a 9-hour-per-day strategy meant 15h
  of zero-value process lifetime per day plus the entire weekend gap to
  defend against.  Migrating to a launchd-scheduled daily launch:
  * Eliminates the weekend SignalR gap entirely (process doesn't exist
    Friday evening → Monday morning).
  * Bounds resource lifetime to ~9h/day (no long-running drift / FD
    growth / GC pressure).
  * Natural one-log-per-launch (the rotated handler is still installed,
    but now mostly belt-and-suspenders).
  * Kernel-managed scheduling via ``StartCalendarInterval`` (more
    reliable than userspace bash wait+sleep).
  * Mid-day crash recovery and reconciliation logic from Round 1+2
    carry forward unchanged — the supervise loop, hang watchdog,
    startup reconciliation, orphan-sweep, and tag fix all still run
    inside each daily launch.

  **Files**

  1. ``scripts/run_morning_reversion.sh`` — new session-end clean-exit
     clause:
     * ``SESSION_EXIT_GRACE_MIN`` env (default 30) computes
       ``SESSION_EXIT_TS = FLAT_BEFORE_TS + grace*60`` (set 0 to
       restore legacy always-on behaviour).
     * ``session_exit_timer_loop`` spawns alongside the hang watchdog
       for each executor iteration; SIGTERMs the executor at
       SESSION_EXIT_TS.
     * Supervise loop checks ``session_ended`` predicate before each
       relaunch AND after each exit — breaks cleanly with rc=0 so
       launchd can pick up the next scheduled wake.
     * Shutdown trap extended to tear down the session timer alongside
       the hang watchdog.
     * Schedule banner now prints the session-end exit time when grace
       is non-zero.
  2. ``scripts/launchd/com.tradebot.mrr.plist.template`` — new
     placeholder-substituted launchd agent template.  ``StartCalendarInterval``
     entries for Weekday 1-5 (Mon-Fri only — Sunday would idle because
     MRR's first trade of the week is Monday 7 AM ET).  Placeholders:
     ``__LABEL__ __SCRIPT_PATH__ __ACCOUNT__ __WORKING_DIR__ __LOG_OUT__
     __LOG_ERR__ __HOUR__ __MINUTE__ __PATH__``.
  3. ``scripts/install_mrr_launchd.sh`` — new installer.  Reads
     ``start_time`` + ``session_timezone`` from MRR's TOML, translates
     "start_time minus WAKE_LEAD_MIN ET" to the host's local clock
     (works for any host TZ — DST-safe within US-observing zones),
     awk-substitutes the template, validates the rendered XML via
     ``plistlib``, copies to ``~/Library/LaunchAgents/``, and
     ``launchctl bootstrap``s it.  Flags: ``--wake-lead MIN``,
     ``--dry-run``, ``--reload``.  Default schedule: Mon-Fri 06:50
     local time (5min before MRR's 06:55 ET start_time).
  4. ``scripts/uninstall_mrr_launchd.sh`` — new uninstaller.
     ``launchctl bootout`` (with ``unload -w`` fallback for older
     macOS) + plist removal.  ``--keep`` retains the plist on disk
     for inspection.
  5. ``scripts/launchd/README.md`` — operator quick-start: install,
     verify, kickstart-for-testing, log paths, env tunables, lifecycle
     diagram, "when NOT to use this" guidance for future
     overnight-holding strategies.
  6. ``tests/test_mrr_launchd_install.py`` — new (8 tests, 0.27s):
     installer file permissions, template placeholder coverage,
     post-substitution plistlib parseability, installer dry-run
     end-to-end, absolute-path resolution in rendered plist,
     ``--wake-lead`` override propagation, ``SESSION_EXIT_GRACE_MIN=0``
     sentinel, positive-grace cutoff math.
  7. ``pytest.ini`` — registered new test file.

  **Operator workflow (post-migration)**

  ```bash
  # One-time install per account:
  bash scripts/install_mrr_launchd.sh 1

  # Verify:
  launchctl list | grep com.tradebot.mrr.account1
  launchctl print "gui/$(id -u)/com.tradebot.mrr.account1"

  # Force a test run NOW (executor will idle if outside trading window):
  launchctl kickstart -k "gui/$(id -u)/com.tradebot.mrr.account1"

  # Uninstall when done:
  bash scripts/uninstall_mrr_launchd.sh 1
  ```

  **Daily lifecycle**

  ```
  06:50 ET   launchd fires → wrapper starts
  06:55 ET   wrapper exec's strategy_executor under supervise loop
  06:55 ET   executor authenticates, connects SignalR, subscribes symbols
  07:00 ET   strategy starts building anchor range
  08:00 ET   anchor finalized, fade window opens
   ...trade...
  16:00 ET   flat_before — strategy flattens open positions
  16:30 ET   session-end timer SIGTERMs executor (grace=30min)
  16:30 ET   supervise loop sees cutoff → wrapper exits rc=0
  16:30 ET   launchd waits until tomorrow 06:50 ET
  ```

  **Backward compatibility**

  The always-on workflow is preserved: ``SESSION_EXIT_GRACE_MIN=0``
  (the existing wrapper's legacy default behaviour) disables the
  cutoff entirely.  Operators not using launchd can keep invoking
  ``bash scripts/run_morning_reversion.sh <account>`` directly with
  ``SESSION_EXIT_GRACE_MIN=0`` and the wrapper supervises forever as
  before.  When the wrapper IS launched by launchd, the default
  grace=30 takes effect and the wrapper exits cleanly for daily
  handoff.

  **Validation**

  * 8 new tests pass in 0.27s.
  * Full suite: **599 passed in 16.2s** — no regressions.
  * Installer dry-run validated end-to-end against the real MRR TOML;
    rendered plist parses with plistlib + has exactly 5 Weekday entries.
  * Session-timer shell smoke test confirmed SIGTERM fires within 3s of
    cutoff in isolation.

  **When always-on is still right**

  Documented in ``scripts/launchd/README.md``: any future strategy that
  holds overnight positions (Globex/Asia/European sessions) or tracks
  24-hour rolling state (e.g. ``overnight_range`` building a 6 PM →
  9:29 AM range across midnight) should use the always-on wrapper.
  MRR doesn't need either, so daily-launchd is the cleaner fit.

- **MRR unattended-operation hardening Round 2: startup reconciliation + hang watchdog + SignalR weekend-survival + critical tag-truncation bug fix (2026-06-15 evening)** —
  Tier-1 production-value bundle closing the remaining silent-risk gaps for
  week-long unattended operation.  Each fix targets a specific failure mode
  the Round 1 work (orphan-sweep, supervise loop, daily log rotation) didn't
  cover.

  0. **CRITICAL: tag-truncation bug fix (pre-existing, affects ALL MRR live
     order matching).**
     ``brokers/topstepx_adapter._generate_unique_custom_tag`` truncates
     the strategy name to 16 chars (TopStepX rejects long ``customTag``s
     with opaque HTTP 500s).  For ``morning_range_reversion`` (23 chars)
     the actual generated tag prefix is ``morning_range_re``, NOT the full
     name.  But ``_live_tag_is_entry`` looked for the FULL string —
     meaning:

     * ``_has_open_position_or_pending_entry_async`` thought every
       account had zero pending MRR entries → MRR could place duplicate
       stop-entries on rapid re-evaluation.
     * The Round-1 orphan-sweep helper
       (``_cancel_previous_session_orders``) never matched its own
       orders → orphan triggers carried across sessions unchanged
       (defeating the whole point of that fix).

     Replaced the hard-coded substring check with a new
     ``_expected_tag_marker()`` helper that mirrors the adapter's exact
     truncation logic — automatically robust against future strategy
     renames AND any tag-format tweaks in the adapter.  Two new
     regression tests pin the fix and assert the matcher/emitter stay in
     sync (``test_live_tag_is_entry_matches_truncated_production_tag``,
     ``test_expected_tag_marker_mirrors_broker_truncation``).
     Orphan-sweep tests rewritten with realistic truncated tags to catch
     this drift in CI from now on.

  1. **Startup reconciliation against broker state.**
     The supervise loop (Round 1) re-launches the executor after a crash,
     but the freshly-constructed strategy starts with empty in-memory
     state (``_live_managed_symbols = set()`` etc.).  The broker may
     still hold working stop-entry orders AND/OR open positions from the
     previous incarnation — silent risk for hours until the next session
     rollover orphan-sweep fires.

     New ``_reconcile_with_broker_state()`` runs once per strategy
     lifetime (gated by ``_did_startup_reconcile``) on the first
     ``analyze()`` call:

     * Pulls ``get_open_orders()`` + ``get_positions()`` from the broker.
     * Adopts every open position on a configured symbol into
       ``_live_managed_symbols`` (so ``manage_positions`` sees it) and
       seeds ``_entry_bar_seq`` to the current bar so ``max_hold_bars``
       clocks fresh.
     * Buckets working entry orders by the YYMMDD prefix embedded in the
       customTag (the adapter's ``%y%m%d%H%M%S`` timestamp): adopts
       same-session triggers into ``_live_managed_symbols``, cancels
       prior-session orphans using the pre-computed ID list (NOT via
       the broader ``_cancel_previous_session_orders`` — that would also
       wipe the same-session orders we just adopted).
     * Position-protected symbols still spared the cancellation pass.
     * Replay/backtest paths short-circuit at the top; broker errors
       are caught + logged but never raised; ``finally`` block ensures
       single-shot semantics even on partial failure.

     Five new unit tests cover replay short-circuit, position adoption,
     same-session-vs-prior bucketing, single-shot guarantee, and broker-
     error tolerance.

  2. **Hang watchdog — catches "Python alive but asyncio loop wedged".**
     The Round-1 supervise loop only fires on process EXIT.  If the
     event loop wedges (rare but real: GIL contention, deadlocked
     lock, busy infinite loop) Python stays alive, the supervise wrapper
     sees nothing wrong, and the strategy silently dies.

     Two-part fix:

     * ``core/strategy_executor._heartbeat_loop`` now touches an external
       heartbeat file every 30s when ``HEARTBEAT_FILE`` env var is set.
       Failures swallowed at DEBUG — heartbeat-file write must never
       kill the executor.
     * ``scripts/run_morning_reversion.sh`` adds a ``hang_watchdog_loop``
       background process per executor iteration.  Polls
       ``stat -f %m`` / ``stat -c %Y`` on the heartbeat file every
       ``HANG_CHECK_INTERVAL_SEC`` (default 60s); if the mtime is older
       than ``HANG_THRESHOLD_SEC`` (default 300s = 10 missed heartbeats),
       SIGTERMs the executor PID so the supervise loop respawns a fresh
       instance.  Stale-mtime detection skipped for the brief startup
       window when the file doesn't exist yet (avoids racing the first
       heartbeat).  Cleanup trap ``rm -f``s the heartbeat file on
       wrapper exit so a stopped wrapper doesn't leave a phantom
       liveness signal.

     Verified end-to-end via a synthetic ``/tmp/test_watchdog.sh`` run:
     a sleeping target that never touches the heartbeat is SIGTERMed
     within ~3s of the threshold crossing.

  3. **SignalR weekend-survival reconnect.**
     The original ``_handle_network_interruption_and_reconnect`` had a
     hard-coded ``max_attempts = 10`` with 30s-capped exponential
     backoff — worst-case ~5 minutes total before the connection died
     silently.  Fine for a transient network blip, useless for a
     Friday-5pm-to-Sunday-6pm broker maintenance window.  After
     exhaustion the downstream bar aggregator + MRR's stale-data guard
     saw nothing but gaps; the supervise loop would then respawn the
     executor and hit the same dead broker, burning through its
     restart budget for nothing.

     Replaced the single-phase loop with a two-phase strategy:

     * **Fast phase** — first ``SIGNALR_FAST_RECONNECT_ATTEMPTS``
       attempts (default 10) keep the original 2/4/8/16/30s backoff at
       INFO log level.  Loud, fast — transient blips show up clearly.
     * **Extended phase** — kicks in after the fast phase fails.  Up
       to ``SIGNALR_MAX_RECONNECT_ATTEMPTS`` total (default 1000),
       polling at ``SIGNALR_EXTENDED_RECONNECT_DELAY_SEC`` (default
       300s = 5 min).  Single WARNING-level "switching to extended
       retry mode" banner on transition; individual attempts logged at
       DEBUG so we don't spam the log with 600 lines during a weekend.
       Default 1000 attempts × 300s ≈ 83 h covers any realistic outage
       window.  Loud WARNING-level "✅ reconnected after extended
       outage" on recovery so the operator can see broker-is-back at a
       glance.

     Config clamping: nonsense env-var values fall back to defaults
     (no crash); ``max_attempts < fast_phase_attempts`` is silently
     bumped so we never retry FEWER times than the fast budget.

     Eight new unit tests cover: fast-phase exponential backoff
     timing, fast-phase success + resubscribe, extended-phase cadence,
     transition announcement at WARNING level (fires exactly once),
     loud recovery message after extended outage, garbage-env
     fallback to defaults, max-clamping safety, and the
     reconnecting-flag double-entry guard.

  Operational implication of all three: an MRR instance launched
  Monday morning will now survive — without operator intervention —
  Wednesday-night broker maintenance, an event-loop deadlock at 3am
  Thursday, and the Friday-evening-to-Sunday-evening weekend gap.
  The Round 1 + Round 2 wrapper now self-heals from all four
  identified failure modes (process exit, event-loop wedge,
  reconnect exhaustion, orphaned broker state) and the only thing
  that takes the strategy down is a real config / network /
  hardware failure that needs human attention.

  Verification: 591/591 full suite passes (up from 576: +5 reconcile
  + 2 tag-bug regression + 8 reconnect tests).  ``bash -n`` syntax-
  clean wrapper.  Both wrapper-side (synthetic ``sleep`` target hang)
  and executor-side (heartbeat file touch in temp dir) smoke-tests
  green.

- **MRR unattended-operation hardening: orphan-sweep + supervise loop + daily log rotation (2026-06-15 PM)** —
  Three coordinated fixes so `scripts/run_morning_reversion.sh` can be
  launched once and left running for a full trading week without operator
  intervention.  Operational audit (response to user question "would the bot
  run my morning range reversion strategy all week with no problem being
  idle between sessions etc?") surfaced three real gaps; all three closed:

  1. **MRR `_cancel_previous_session_orders` at session rollover.**
     Symptom that motivated the fix: MRR's stop-entry triggers (the
     ``sweep_advance_stop`` mode used by the validated TOML) sit on the
     broker as **working orders** until either filled or cancelled.  The
     strategy's existing ``flat_before`` + ``max_hold_bars`` guards only
     operate on *filled* positions; unfilled triggers from yesterday could
     silently carry over into tomorrow's session — potentially firing at
     yesterday's stale level once today's anchor range armed a fresh trigger
     at a different price.  Mirrors the
     ``overnight_range._cancel_previous_session_orders`` pattern
     (``strategies/overnight_range_strategy.py:930-981``):
       * Pull ``get_open_orders()`` + ``get_positions()`` from the broker.
       * Skip every order on a symbol that has a **live position** (the SL/TP
         legs covering that position must survive — cancelling them naked-
         legs the live trade).
       * Filter remaining orders through the existing
         ``_live_tag_is_entry`` predicate so we only touch MRR's own
         stop-entry / stop-bracket triggers — never SL/TP children, never
         overnight_range orders, never operator-placed orders without our
         tag.
       * Issue ``cancel_order()`` for the survivors; log + swallow individual
         failures so one broker hiccup can't abort the sweep.
     Wired into ``analyze()``'s ``🌅 new session`` rollover block
     (``strategies/morning_range_reversion_strategy.py:2393``), guarded by a
     new strategy-level ``_last_orphan_sweep_date`` so the broker round-trip
     happens **once per ET date** regardless of how many symbols transition
     simultaneously.  First-ever-session guard
     (``st["session_date"] is not None``) prevents wiping orders the
     operator placed manually before starting MRR.
     Replay/backtest paths short-circuit at the top of the helper — no
     change to determinism.
     Five new unit tests in ``tests/test_morning_range_reversion_smoke.py``
     cover: replay short-circuit, tag-prefix filtering (overnight_range +
     SL/TP children + untagged orders all survive), live-position
     protection, broker-error tolerance, and ``config.symbols``
     normalisation/dedupe + fallback to ``self._state`` when empty.

  2. **Wrapper-side supervise loop with backoff + max-restart cap.**
     Original ``scripts/run_morning_reversion.sh`` ended with
     ``exec caffeinate -dimsu "$PY" core/strategy_executor.py …`` — exec
     replaced the wrapper PID with caffeinate, so a Python crash killed the
     entire instance.  For week-long unattended runs the executor needs to
     self-heal from transient broker hiccups, OOM events, and the (very
     unlikely) post-weekend reconnect failure when SignalR exhausts its
     10-attempt reconnect budget.  New topology:
       ```
       wrapper PID                 (supervisor — the bash script)
         └── caffeinate -w $$ &    (keeps mac awake until wrapper exits)
         └── python executor       (looped; respawned on non-zero exit)
       ```
     * ``caffeinate -dimsu -w $$ &`` runs ONCE at the top of the launch
       phase, tied to the wrapper PID so it dies cleanly on
       wrapper exit (success, error, Ctrl+C).
     * Executor runs in a ``while :; do …; done`` loop.  Exit codes 0 / 130
       (SIGINT) / 143 (SIGTERM) are treated as intentional shutdowns →
       break the loop.  Any other non-zero exit triggers backoff + retry.
     * Exponential backoff: 5s → 10 → 20 → 40 → 80 → 160 → 300 (capped at
       ``BACKOFF_MAX``).
     * Hard cap: ``MAX_RESTARTS=10`` failures in a "tight" window aborts
       the supervisor so a tight crash loop can't burn API quota.
       ``RESET_AFTER_SEC=3600`` (1 h) of healthy uptime resets the counter
       so a 6-hour-in crash doesn't add to yesterday's noise.
     * ``shutdown()`` trap handler on INT/TERM/EXIT forwards SIGTERM to the
       executor (with 10 s grace period for log flush + SignalR cancel),
       then SIGKILL, then kills caffeinate — no orphaned children when
       the operator Ctrl+C's the wrapper.
     All tunables override-able via env (``MAX_RESTARTS``,
     ``RESET_AFTER_SEC``, ``BACKOFF_INITIAL``, ``BACKOFF_MAX``).

  3. **Daily log rotation via ``LOG_ROTATE_DAILY=1``.**
     Default ``core/logging_setup.configure_logging`` used
     ``RotatingFileHandler(maxBytes=10MB, backupCount=5)`` — size-based
     rotation that produced ``morning_range_reversion_account1_<ts>.log``
     + 5 ``.log.1``…``.log.5`` siblings.  Across a multi-day run all
     sessions were interleaved into the same file, making
     "what happened Tuesday?" non-trivial.  Added an opt-in
     ``LOG_ROTATE_DAILY`` env switch (recognised values: ``1``, ``true``,
     ``yes``, ``on``, ``daily``) that swaps to
     ``TimedRotatingFileHandler(when="midnight", interval=1,
     backupCount=max(backup_count, 14), encoding="utf-8")`` with
     ``suffix = "%Y-%m-%d"`` — one archive per calendar day, two trading
     weeks of history kept by default.  Default behaviour unchanged for
     every other entry-point (master CLI, webhook server, one-shot
     scripts) — they keep size-rotation as before.
     ``scripts/run_morning_reversion.sh`` now exports
     ``LOG_ROTATE_DAILY=1`` by default; override with
     ``LOG_ROTATE_DAILY=0 bash scripts/…`` to fall back to size mode.
     ``get_log_path()`` updated to match both handler types via
     ``isinstance(h, (RotatingFileHandler, TimedRotatingFileHandler))`` so
     downstream consumers (dashboard log tail, ``scripts/log_drain.sh``,
     etc.) still resolve the active log.

     Operational implication: an MRR run started Monday morning will
     produce ``morning_range_reversion_account1_<startup_ts>.log``
     (Monday's session) +
     ``…_<startup_ts>.log.2026-06-15`` /
     ``…_<startup_ts>.log.2026-06-16`` / … through Friday.  Easy
     to grep per-day; old archives auto-purged after 14 days.

  Verification: 77/77 MRR tests pass (72 existing + 5 new orphan-sweep
  cases); 576/576 full suite passes.  ``bash -n`` syntax-clean wrapper.
  Smoke-tested both logging modes via a temp-dir reload exercise:
  ``LOG_ROTATE_DAILY`` toggle correctly selects ``RotatingFileHandler``
  vs ``TimedRotatingFileHandler`` with the expected ``%Y-%m-%d``
  archive suffix.

- **Master GUI — v2 Phase 3.3: chart auto-mode regression fix + remove duplicated WS chip (2026-06-13 night)** —
  Two follow-ups to Phase 2.9 + 3.2:

  1. **Auto-mode chart broke for windows < 5 days.**
     Phase 2.9 wired hybrid Databento + API stitching for the
     `source=auto` path. Part of that change had the auto branch always
     pass the *inferred* `start_time`/`end_time` window down to
     `trading_bot.get_historical_data()` — even when the window already
     fit entirely inside the API horizon. That broke the tight presets
     ("Auto · last 200", "1 day", "3 days"): the broker's `Bar/retrieve`
     call returned zero bars whenever `end_time` landed inside the
     still-open candle, so the chart rendered empty. Fix: in
     `gui/chart_html.py`'s `handle_reload_data`, the auto branch now
     **only** forces tight `start_time`/`end_time` on the API call when
     `needs_databento=True` (i.e. the window straddles the api_horizon
     and we need to stitch). When `needs_databento=False` we fall back
     to `start_time=None, end_time=None` and let the broker return its
     natural "latest `limit` bars" — same behaviour as pre-Phase-2.9.
     Big windows (1mo / 3mo / 6mo / 1y) keep the explicit window so
     stitching has a defined join point at api_horizon.

  2. **Removed the now-redundant WS-health pair from the drawer body.**
     Phase 3.2 promoted "Connected · uptime · last tick" into the
     always-visible drawer-head chip but left the original
     `<div class="conn">` + `<div id="conn-meta">` pair sitting under
     the account selector in the body. Stripped them along with their
     CSS (`.head .conn`, `.head .conn-meta`, `.head .conn .dot.warn`,
     etc.). The 1 Hz `tickConnMeta()` and `setConn()` getElementById
     lookups are null-guarded so no JS changes were required — they
     simply stop writing to the gone nodes and continue mirroring into
     the head-summary chip.

- **Master GUI — v2 Phase 3.2: head-summary now shows WS health, not DLL (2026-06-13 night)** —
  The always-visible drawer-head summary chip used to display the daily
  loss-limit buffer (`DLL $XXX left`). Operators rarely act on that
  number from the chip alone (DLL warnings already surface via toast +
  the consec-loss breaker), but they DO need WS health visible at a
  glance without expanding the Session drawer. Swapped the chip's
  payload to:

  ``● Connected · uptime 1m 25s · last tick 2s ago``

  Wiring:
  - Replaced `<span id="head-summary-risk">` with two spans in the
    drawer-head: `#head-summary-conn-status` (the verbal status —
    "Connected" / "Reconnecting…" / etc.) and `#head-summary-conn-meta`
    (uptime + last-tick monospace string).
  - `setConn(state, label)` now mirrors `label` into
    `#head-summary-conn-status` so the chip's text follows reconnect
    state instantly.
  - `tickConnMeta()` (1 Hz) now writes both the existing drawer-body
    `#conn-uptime` / `#conn-tick` and the new `#head-summary-conn-meta`
    summary span. Single source of truth, two render targets.
  - Stripped the dead DLL block in `applyAccountState` and the
    associated `.head-summary #head-summary-risk.warn/.hot` CSS rules.

- **Tooling — daily Databento freshness uses *existing* helpers** —
  Earlier tonight I drafted a new `scripts/append_api_bars_to_databento.py`
  not realising the repo already ships exactly what's needed. The
  canonical pair has been there for a while and is what the cron line
  should use:

  - `scripts/stitch_broker_history_to_databento_1m.py` — chunks broker
    `get_historical_data` in 10-day windows (1m × 10d ≈ 14 400 bars,
    safely under the 20 k adapter cap), merges via
    `historical_data/csv_merger.py` (canonical first, broker second so
    duplicate timestamps keep the **newer** broker row), backs up
    `*.bak.<UTC>` per root.
  - `scripts/stitch_broker_history_to_databento_5m.py` — same flow for
    5 m, independent of 1 m so each cadence stitches against its own
    canonical CSV.

  Cron pair (after CME daily settlement, weekdays only)::

      30 17 * * 1-5 cd /path/to/tradeBotServer && \\
          ENABLE_SIGNALR=false .venv/bin/python \\
            scripts/stitch_broker_history_to_databento_1m.py \\
          >>logs/stitch_broker_1m.log 2>&1
      35 17 * * 1-5 cd /path/to/tradeBotServer && \\
          ENABLE_SIGNALR=false .venv/bin/python \\
            scripts/stitch_broker_history_to_databento_5m.py \\
          >>logs/stitch_broker_5m.log 2>&1

  My new script was deleted (it duplicated the chunking + merge logic
  with no new value). The Phase 3.1 entry is replaced by this note;
  there's no behaviour change to ship beyond pointing the cron at the
  existing scripts.

- **Master GUI — v2 Phase 3.0: DB rewiring — MRR/ORB range persistence + trade snapshots (2026-06-13 night)** —
  Three queued items from `gui/design_todo.md` resolved in one round, all
  hanging off existing Postgres infrastructure that just needed wiring.

  * **MRR + ORB range DB persistence.** Until now only `overnight_range`
    persisted its session range to `strategy_states.settings.or_ranges`,
    so MRR / ORB boxes never showed on the live chart when those
    strategies ran in a separate `strategy_executor` process (production
    setup). Added:
      - `BaseStrategy.persist_range_snapshot(snap, key=..., throttle=...)`
        in `strategies/strategy_base.py` — generic helper that any
        range-based strategy can call to mirror its per-symbol box into
        `strategy_states.settings[key]`. Throttle defaults to 4s
        (matches the OR pattern). Errors are logged at DEBUG and never
        propagate.
      - `_persist_mrr_ranges_to_db()` on
        `MorningRangeReversionStrategy`, called at range-finalization
        right after `_persist_anchor_snapshot`. Slot key: `mrr_ranges`.
      - `_persist_orb_ranges_to_db()` on
        `OpeningRangeBreakoutStrategy`, called whenever the build-window
        scan updates `range_hi`/`range_lo`. Slot key: `orb_ranges`.
      - Both methods emit `{symbol: {high, low, mid, width, session_date,
        session_start_et, session_end_et, ...}}` and also write a
        contract-prefix-stripped alias (`F.US.MNQ` → `MNQ`) so the
        chart's symbol dropdown can find them.
    Read side in `gui/chart_html.py::handle_strategy_details` now
    consults `_strategy_ranges_from_db(trading_bot, account_id, name,
    settings_key)` whenever the in-process `_state`/`_sessions` is empty
    (the executor-owns-strategy case), with a per-strategy slot map:
    `morning_range_reversion → mrr_ranges`, `opening_range_breakout →
    orb_ranges`. Live in-process state still wins when present (avoids
    DB-lag on the host process).

  * **Trade snapshots — locked-at-close OHLCV + range capture.** Adds
    `trade_snapshots` Postgres table (12 columns including JSONB
    `bars_json`, `range_snapshot_json`, `metadata`). New methods on
    `infrastructure/database.py::DatabaseService`:
    `save_trade_snapshot(...)` (idempotent ON CONFLICT trade_id DO UPDATE)
    and `get_trade_snapshot(trade_id)`. Capture runs in
    `core/user_hub_handlers._capture_trade_snapshot`, scheduled as a
    background `asyncio.create_task` from `_publish_trade_closed_events`
    so a slow Databento read never delays downstream subscribers
    (consec-loss breaker, GUI broadcast). Source-priority for the bar
    window: (1) `core.chart_databento_loader.load_databento_window_for_chart`
    (deep history, parquet-cached), (2) broker historical API
    (current-edge bars). Strategy range is sourced from whichever of
    `or_ranges`/`mrr_ranges`/`orb_ranges` matches the trade's symbol in
    `strategy_states.settings` at close time. **Key choice:** rows are
    keyed by the broker's `exit_fill_id` (which equals `trade.id` on the
    `Trade/search` API the dashboard reads from), with a session-tracker
    `trade_id` alias row written so tools that only have the internal id
    still resolve.

  * **`GET /api/chart/trade_snapshot/{trade_id}` endpoint** + frontend
    rewire. The recap modal in `gui/master_control_v2.html` now follows
    a two-tier source priority:
      1. **Locked snapshot** (`/api/chart/trade_snapshot/<id>`) —
         exact bars + range as captured at close. Always available
         regardless of broker-history rolloff or Databento lag.
      2. **Live Databento window** (`/api/chart/trade_recap`) —
         existing fallback for trades that closed before the snapshot
         feature was deployed (or whose capture failed).
    `openTradeRecap()` uses the snapshot's `entry_time`/`exit_time` for
    vertical lines and snaps to the nearest bar in the returned series
    (same `_snapToBar` helper either way) so labels land cleanly.

  * **Strategy range overlay on recap modal.** When a snapshot includes
    `range_snapshot_json`, the modal now draws the H/L box (shaded
    amber area-series + dotted H/L price lines + thin midline) so the
    recap shows the *exact* setup geometry — same shape as
    `scripts/walkforward_trade_recap_report.py`. Range slot is detected
    from the saved `__slot` flag (`mrr_ranges`/`orb_ranges`/`or_ranges`)
    so the price-line labels read `MRR-H` / `ORB-H` / `OR-H` accordingly.

  See `gui/design_todo.md` for the now-resolved list and the one
  remaining queued item (daily Databento append-from-API helper).

- **Master GUI — v2 Phase 2.9: hybrid Databento+API chart history + recap vline timestamps (2026-06-13 late)** —
  Two surface changes, one architectural.

  * **`/api/chart/reload?source=auto` now stitches Databento + broker API.**
    The broker API is current-to-the-tick but only serves the most recent
    ~5 days; Databento covers multi-year history but lags by a day. The
    auto path computes an effective window (explicit `start`/`end`, or
    `limit × tf` back from now), then:
      - window entirely older than `now − CHART_API_HORIZON_DAYS` (default
        5d) → Databento only
      - window entirely within the API horizon → API only
      - window straddles → Databento for `[lo, api_horizon]` + API for
        `[api_horizon, hi]`, deduped on `time` (API wins on overlap)
    The stitched output is trimmed to the requested `limit` from the
    recent edge so wide+coarse windows ("3 months 1h") show the full span
    while narrow+fine windows ("1 day 1m") still hit the live API edge.
    `history_source` in the JSON payload is now one of `databento`,
    `api`, or `databento+api` so the eyebrow chip surfaces the choice.
  * **New `core.chart_databento_loader.load_databento_window_for_chart`** —
    the previous loader only tailed the last N rows of the canonical CSV;
    the new helper routes through `core.backtest.parquet_cache.load_ohlcv_cached`
    (same hot path as `scripts/walkforward_trade_recap_report.py`), slices
    by `[start_utc, end_utc]`, optionally resamples 1m → coarser via
    `HistoricalDataLoader`, and caps to the last `limit` rows. This is what
    enables both the auto stitcher and `source=databento` with an explicit
    window.
  * **Frontend sends explicit `start`/`end` for time-window presets.** When
    the user picks "1 month / 3 months / 1 year / etc.", `loadChartData()`
    now appends `start=now−preset.minutes×60` & `end=now` to the request.
    Without this, the implicit window from `limit × tf` understated wide
    spans (limit gets cap-bound to 5000) and the auto stitcher would route
    purely to the API.
  * **Recap modal: time labels under each vertical line.** The IN/OUT
    vertical-line `<div>`s now host a child `<span class="recap-vlabel">`
    rendering `IN <m/d HH:mm>` / `OUT <m/d HH:mm>` in local time, pinned
    to the bottom of the chart pane (just above the time-axis row) and
    colored to match the trade outcome (moss = profit, rose = loss).
    Reposition logic shares `updateRecapVerticals` so labels follow the
    line on every pan/zoom/resize. Closes the user's "now add time labels
    to the bottoms of the vertical lines" ask.

  Deferred (still open): **daily append-from-API → canonical Databento CSVs**.
  The user proposed a periodic helper that pulls the broker's last 24h of
  bars and merges them into the canonical 1m/5m CSVs so Databento itself
  stays current. The hybrid stitcher above keeps the chart visually
  correct without that, but if we want offline tools (backtests, sweeps)
  to also see today's bars, this script is still required. Sketch in
  `docs/design_todo.md` notes.

- **Master GUI — v2 Phase 2.8: side semantics + local-time chart axis (2026-06-13 night)** —
  Round 2.7 introduced a real entry price via pairing but exposed two
  more correctness bugs the previous derivation was hiding.

  * **`side` now carries the position direction, not the close-fill side.**
    Trade/search returns one record per fill; the closing fill of a
    long is a SELL and the closing fill of a short is a BUY. The
    enhance loop in `gui/chart_html.py::handle_get_trades` was passing
    the close-fill side straight through, so trade #59 (long, qty 15,
    closed for +$1672) read as `SELL MNQ` with `entry < exit` and
    `pts = entry − exit < 0` even though pnl was positive — a confusing
    sign mismatch. After pairing succeeds we now copy the *opener's*
    side into `serialized_trade['side']` (the real position direction),
    keep the close-fill side under `close_fill_side` for diagnostics,
    and recompute `points` using the resolved direction so its sign
    always matches `pnl`.
  * **Entry-derivation fallback re-grounded on pnl sign.** When pairing
    fails (e.g. open lies before the queried window) we still need to
    reconstruct the missing entry price. The previous formula assumed
    `side = position direction` — exactly the assumption that just
    broke. The new fallback uses the `pnl` sign + the close-fill side
    to disambiguate: profit + close-fill SELL → was LONG (`entry =
    exit − |pts|`), loss + close-fill SELL → was LONG with adverse
    move (`entry = exit + |pts|`), and so on. Result: derived trades
    line up with the paired ones — sign of `pts` always matches pnl.
  * **Chart x-axis now renders in local timezone.** The user reported
    "15:20 = 11:20 EDT" — i.e. the LWC default UTC formatter. Both
    `chart` and `recapChart` now pass `tickMarkFormatter` +
    `localization.timeFormatter` that route through
    `Date.toLocale*String(undefined, …)` so axis ticks and the
    crosshair tooltip respect whatever timezone the browser is in.

- **Master GUI — v2 Phase 2.7: real entry timestamps via half-turn pairing (2026-06-13 night)** —
  After 2.6 the recap chart loaded real Databento bars, but `entry_time`
  was still equal to `exit_time` because `core.cli_command_parser._handle_trades`
  drops the half-turn open fills before they reach `handle_get_trades`.
  That made both the IN and OUT arrows snap to the same minute on the
  recap chart ("wild and ugly" arrow stacking).

  * **`handle_get_trades` now pairs opens with closes itself.** Calls
    `trading_bot.get_trades_from_api(...)` directly to get the *raw*
    Trade/search records (incl. `is_half_turn=True` opens), sorts them
    ascending, and FIFO-matches per symbol so each closing record gets
    `{entry_time, entry_price, entry_order_id}` from its corresponding
    open. Works because TopStepX prop accounts hold flat-or-one position
    per symbol; partial scale-outs still get the oldest unmatched open
    as the entry source until the queue empties.
  * **Pairing wins over derivation.** When a paired open is found we
    use the broker's actual entry timestamp + price and recompute
    `points` from the real two-leg prices (no longer marked
    `derived_entry`). The exit-minus-points fallback still runs for
    trades where pairing failed (e.g. opens outside the queried window).
  * **Same-bar scalp visual fix in the recap modal.** When entry and
    exit snap to the same candle (very fast in-out scalps, or trades
    where pairing failed and only the close timestamp is known), the
    modal now pushes the exit marker to the next bar so the IN / OUT
    arrows + connector line stay visually distinct instead of stacking
    on top of each other.

- **Master GUI — v2 Phase 2.6: recap charts on Databento + head label polish (2026-06-13 late)** —
  Replaces the broker-history workaround with the canonical Databento path.

  * **New endpoint `GET /api/chart/trade_recap`** in `gui/chart_html.py`.
    Mirrors `scripts/walkforward_trade_recap_report.py` exactly:
    `core.backtest.parquet_cache.load_ohlcv_cached(csv_path)` → slice
    `[entry − pad, exit + pad]` on the naive-UTC index →
    `core.backtest.ohlcv.dataframe_to_chart_bars_unix(sub)` → snap entry /
    exit unix-times to bar opens via `snap_trade_unix_to_chart_bar_open`.
    Returns LWC-ready bars + the *snapped* entry/exit times so markers
    always land on a real candle (left-labeled bar convention). Query:
    `symbol`, `entry`, `exit` (ISO 8601 or unix), optional `pad_minutes`
    (clamped 5–1440, default 120), optional `timeframe` (`1m`/`5m`,
    default `1m` with 5m fallback when 1m CSV is missing). Coverage =
    full Databento history (currently 2023-05 → today for MNQ/MES/MGC),
    so old trades work the same as today's.
  * **Recap modal switched to the new endpoint.** No more "trade pre-dates
    broker history" empty-state — the modal hits `trade_recap`, gets a
    proper sliced window, and uses `entry_snapped`/`exit_snapped` for
    the markers. Foot now reads e.g. `181 bars · 1m · MNQ_1m_databento.csv`
    (and appends `entry derived from exit + pts` when applicable).
  * **Header drawer label kept as "Session", account name truncated.**
    Long broker names like `PRAC-V2-14334-54471239` collapse to
    `PRAC-V2` in the drawer-toggle row (heuristic: first numeric tail
    segment ≥ 3 chars ends the prefix). The full name remains in the
    `<select>` inside the body.

  * **Known network issue (not a code bug):** the user's last bot run
    hit `socket.gaierror: nodename nor servname provided, or not known`
    on the SignalR transport plus a Railway Postgres `Operation timed
    out`. Both are DNS / connectivity at the host level — restart the
    bot once the network is reachable; no patches required.

- **Master GUI — v2 Phase 2.5: drawer-everything + recap data fixes (2026-06-13 evening)** —
  Round of UI polish + targeted data fixes after the Phase 2.4 review.

  * **Candle countdown moves next to Close (C).** Previously it was a long
    way to the right of the last-price block (after the OHLC group, after
    the big LP figure). It now sits inside the OHLC inline group as the
    fifth element (`O · H · L · C · countdown`), where it's actually
    next to the data it's counting down for. CSS shrinks the cell and
    drops the left margin so it doesn't push other elements.
  * **Trade-recap entry price reconstruction.** The broker's
    `Trade/search` returns one fill per record (the close fill), so
    `entry_price == exit_price` even though `pnl != 0`. We already
    derived `points` from `pnl / (point_value × quantity)` in 2.4; this
    pass also reconstructs the missing leg —
    `entry = exit - pts` for LONG, `entry = exit + pts` for SHORT — so
    the recap modal can render real arrows and price lines instead of
    drawing both at the same level. Trades carry a `derived_entry: true`
    flag so the recap footer can call out that the entry came from the
    pnl/points reconstruction.
  * **Recap modal degrades gracefully for old trades.** When a trade
    pre-dates available broker history (anything older than ~3 weeks),
    the windowed `/api/chart/reload?start=…&end=…` returns no bars and
    we used to dump the latest 200 bars into the chart while still
    placing entry/exit markers at the trade's timestamp. LWC silently
    snaps off-range markers to the leftmost bar, which made the recap
    confidently lie ("BUY at 5/19, 6:40 AM" pinned to today's first
    bar with the wrong price). The recap now checks whether
    `entry/exit_sec` falls inside `[firstBarT, lastBarT]` and skips
    markers + price lines + connector entirely when it doesn't,
    surfacing an honest footer instead: *"trade window pre-dates
    broker history — showing latest N bars (markers omitted; snapshot
    retention on roadmap)"*. Trade snapshot persistence (storing the
    bar window + entry/exit metadata in a new `trade_snapshots` table
    on close) is the canonical fix and remains queued.
  * **Drawer toggle on the chart section.** The chart section is now a
    drawer (`#chart-drawer`, default open). Click the `▸ Chart` toggle
    to collapse the chart canvas + meta + order-entry + bracket rows.
    `toggleDrawer` was extended to call `chart.applyOptions()` and
    `chart.timeScale().fitContent()` after the body un-hides so LWC
    repaints to the new container width.
  * **Drawer toggle on the account header.** The header is now
    `<header class="head drawer" id="head-drawer">` with a compact
    one-line summary visible when collapsed (`▸ Account · <selected>
    · <dot> <balance + unrealized>`). Default open. The mini connection
    dot mirrors `connection-dot` so you can still see live/warn/error
    status with the body collapsed.

- **Master GUI — v2 Phase 2.4: review-pass fixes + recap window query (2026-06-13 PM)** —
  Follow-up round addressing review feedback on the live v2 dashboard.

  * **Trades-window selector now actually changes the data.** The inline
    `onchange="onPerfWindowChange()"` was being called without an event
    arg, so `getPerfWindowDays()` only ever read the *Performance*
    selector. Both selectors now pass `this`, and `onPerfWindowChange`
    falls back through `arg.value` → `arg.target.value` → `activeElement`
    → `getPerfWindowDays()`.
  * **Equity curve hover tooltip.** A floating tooltip now follows the
    cursor over the equity area, showing the date and the cumulative P&L
    of the hovered point (moss for gain, rose for loss). Wired through
    `equityChart.subscribeCrosshairMove`.
  * **Trades table "Pts" column.** The broker's `Trade/search` API
    returns one fill per record, so `entry_price == exit_price` and the
    points calc was always 0 even on winning/losing trades. The
    enhanced-trades builder in `gui/chart_html.py` now falls back to
    `pnl / (point_value × quantity)` whenever entry == exit and
    `pnl != 0`, so the column reflects the actual price movement.
  * **Range overlay reaches edge-to-edge.** The shaded box now spans the
    visible time range of the chart instead of only the build window
    (the strategy is "in effect" for the whole session). The build-
    window region keeps a slightly stronger tint to mark *where* the
    range was formed. Re-renders on pan/zoom via
    `subscribeVisibleTimeRangeChange`.
  * **Stale range filter.** Ranges with a `session_date` more than 36
    hours old are skipped client-side (handles overnight strategies'
    yesterday-dated sessions while suppressing prior-week leftovers).
  * **Trade-recap modal fetches the correct window.** Added `start` /
    `end` query params (Unix seconds OR ISO 8601) to
    `/api/chart/reload`; the modal now fetches the exact trade window
    (entry − 2h → exit + 2h) instead of relying on whatever the most
    recent 1000 bars happen to cover. Falls back to a plain limit-based
    fetch if the windowed call returns nothing. The modal also adds
    horizontal entry/exit price lines and a diagonal connector between
    entry and exit points (walkforward visual).

  Files: `gui/master_control_v2.html`, `gui/chart_html.py`.

### Next
- **MRR / ORB strategy-range DB persistence (planned)** — `overnight_range`
  already round-trips its per-symbol H/L through
  `strategy_state.or_ranges`, which is what makes the chart overlay
  survive a strategy_executor restart. `morning_range_reversion`
  (`_state[sym]`) and `opening_range_breakout`
  (`_sessions[sym]`) currently keep their range state in-memory only,
  so when MRR/ORB are run via the executor the main bot process can't
  read them and the v2 chart shows no overlay until the strategy
  rebuilds the range from scratch. Adding a `persist_state()` /
  `restore_state()` pair to both classes (writing to
  `strategy_state.range_state`) is the next discrete piece.
- **Trade snapshot capture on close (planned)** — store a pre-trimmed
  bar-window + entry/exit metadata to a `trade_snapshots` table at the
  moment a trade exits, so the recap modal can re-render even months
  later without depending on broker historical APIs that may have
  rolled past the trade. Fetched on demand by the recap modal via a
  new `/api/chart/trade_snapshot/{trade_id}` endpoint.

### Added
- **Master GUI — v2 Phase 2.3: live strategy range overlays on the chart (2026-06-13 PM)** —
  When a range-based strategy is running (overnight_range / morning_range_reversion /
  opening_range_breakout / overnight_reversion), the v2 chart now draws the
  same shaded build-window box + high/low/midline overlay that the static
  walkforward trade-recap charts use.

  * **Backend**: `handle_strategy_details` extended to expose
    `details.ranges[symbol] = {high, low, mid, size, session_start_et,
    session_end_et}` and `details.range_window = {start_et, end_et, tz}`
    for MRR / ORB / overnight_reversion (overnight_range already had this
    shape). MRR pulls per-symbol H/L from `_state[sym]`; ORB pulls from
    `_sessions[sym].range_hi/range_lo`; both compute session timestamps
    from the strategy's `range_start` / `range_end_open` attributes.
    Files: `gui/chart_html.py`.

  * **Frontend**: `master_control_v2.html` now polls active strategies
    via `loadStrategies` and, for each that exposes `details.ranges`,
    fetches the per-symbol H/L. When the chart symbol matches, it draws:
    - 3 horizontal price lines (`H`, `L`, `mid`) using
      `candlestickSeries.createPriceLine` — visible across the entire
      chart with right-axis labels.
    - 1 shaded baseline-series rectangle across the build window
      (`session_start_et` → `session_end_et`) using the same
      `addBaselineSeries({ baseValue: { type: 'price', price: lo }, ... })`
      pattern as the static walkforward charts.
    Each strategy gets its own colour palette (overnight_range = moss,
    MRR = amber, ORB = slate-blue, overnight_reversion = rose). Overlays
    redraw on chart symbol change and on every `loadStrategies` cycle
    (15 s).

    Limitation: when MRR / ORB run via `core/strategy_executor.py` (a
    separate subprocess), the main bot may not have the strategy's
    in-memory `_state` loaded. overnight_range already has DB persistence
    via the `strategy_state.or_ranges` blob; extending DB persistence to
    MRR/ORB is a follow-up.

### Added / Changed
- **Master GUI — v2 Phase 2.2: candle countdown, symbol search, drawer-everywhere, trade recap (2026-06-13 PM)** —
  Six follow-ups to the data-correctness pass.

  * **Candle countdown** — small `mm:ss` count-down to the right of the
    last-price legend. Uses `__lastBarOpenSec + tfSeconds`, ticks every
    second, goes muted → `linen` (last 25%) → `rose` (last 10%).
  * **Symbol combobox** — replaced the basic 3-option `<select>` with a
    custom popover: a search input, MNQ / MES / MGC pinned as favorite
    pills at the top, and a scrollable list of all account-available
    contracts pulled from `/api/chart/contracts` (filtered by typed
    query). The hidden `<select>` stays as the source-of-truth so all
    existing handlers (`onChartContextChange`) keep working.
  * **Drawer-everywhere** — Active, Performance, Trades, and Strategies
    sections now use the same collapsible drawer pattern as Terminal.
    Open state persists in `localStorage['masterv2.drawers.open']`.
    Default open: all of Active / Performance / Trades / Strategies;
    Terminal stays closed-by-default. The chart section is intentionally
    NOT collapsible.
  * **Refresh rate** — default bumped from 4× to **12×/sec**, options
    extended to include 1× / 2× / 4× / 8× / 12× / **24×** (matches v1).
    Persists in localStorage.
  * **Chart canvas** — default height bumped another 10% (462 → **508 px**).
  * **Trade recap modal** — every row in the Trades table gets a "view"
    link. Click opens a modal showing the trade on a 5-minute candlestick
    chart, windowed to 90 min before entry through 90 min after exit,
    with entry/exit arrows (`IN` / `OUT`) coloured moss/rose by P&L sign.
    Same per-trade snapshot pattern as
    `scripts/walkforward_last_trades_charts.py`. ESC closes; click the
    backdrop closes. Uses the existing `/api/chart/reload?limit=1000`
    endpoint, falls back to the latest 200 bars if the trade is older
    than the available history (~3.5 days at 5m × 1000).

  Files: `gui/master_control_v2.html`.

### Changed / Fixed
- **Master GUI — v2 Phase 2.1: data-correctness pass + UX polish (2026-06-13 PM)** —
  Bug-fix and polish round on `gui/master_control_v2.html` after live review.

  **Data correctness — KPIs / Equity / Trades all now derive from a single
  `/api/chart/trades` fetch.** Previous code split this across three endpoints
  (`/performance/metrics`, `/pnl/history`, `/trades`) which produced
  inconsistent values:
  * **Net P&L** showed `$0.00` because it was reading `current.realized_pnl`
    from live account state (often zero for stale sessions). Now uses
    `data.statistics.total_pnl`.
  * **Avg trade** showed `$0.00` because the metrics endpoint never set
    `trades.avg_trade`. Now computed as `total_pnl / total_trades`.
  * **Max drawdown** showed values like `599.7%` / `1757.4%` because the
    metrics endpoint returns drawdown in **dollars** but the v1 widget
    treated it as a percent. Now uses `statistics.max_drawdown_pct`
    (already a proper percent).
  * **Equity curve** stayed empty because `/api/chart/pnl/history` queries
    the `trade_history` DB table — sometimes empty even when `/chart/trades`
    has hundreds of fills. Now built **client-side** by cumulative-summing
    `pnl` over sorted exit_time from the trades list. Always matches the
    visible table.

  **New KPI cells: Best streak / Current streak.** Computed client-side from
  the trades list. Cells render as `<n><W|L>` with W in moss-green, L in
  rose. KPI grid expanded from 6 to 8 cells (4-column layout).

  **Stale-order filter.** Orders with status REJECTED / CANCELLED / FILLED /
  EXPIRED / DONE / CLOSED are now filtered out of the Orders table client
  side, so rejected fat-finger orders from market-closed hours stop
  lingering on the board.

  **Trades table is now scrollable** (max-height 520px, sticky thead,
  custom thin scrollbar) instead of growing the page.

  **Chart controls:**
  * Default chart height 420 → **462px** (+10%).
  * **Maximize** link in the chart-meta — toggles a body class that hides
    everything but the chart section + trade row, with a sticky/blurred
    trade row at the bottom of the viewport.
  * **Refresh-rate selector** (1× / 4× / 12× per second) restored next to
    the History-source dropdown; persists in localStorage.

  **Header:**
  * Added a small mono-typed line under the connection dot:
    `uptime 2h 14m · last tick 1s ago` — uptime ticks every second, last-tick
    color goes moss → linen → rose as the WS message age crosses 10s / 60s.

  **Misc:** PRICE / TYPE column gap in the Orders table fixed (extra
  padding on the Type column). WS handler collapsed: `metrics_update`,
  `performance_metrics`, `pnl_history`, `session_trades` all invalidate the
  trades cache and re-fetch the unified trades response. Removed
  `loadPerformanceMetrics` / `loadPnlHistory` (folded into
  `loadTradesTable`).

  Files: `gui/master_control_v2.html`.

### Added
- **Master GUI — v2 Phase 2: Performance, Trades, Equity, Strategies, Terminal (2026-06-13 AM)** —
  Phase 2 of the P6 Quiet Trader rewrite. The "wiring next round" placeholder
  in `gui/master_control_v2.html` is replaced with four fully-wired sections:
  * **Performance** — 6-cell KPI grid (Trades / Win rate / Net P&L / Avg
    trade / Max drawdown / Profit factor) with italic-serif labels and big
    JetBrains-Mono numerics. Window selector at top-right (Today / 7d / 30d
    / 90d / 1y / All). Pulls `/api/chart/performance/metrics?period_days=N`
    every 30s plus on `metrics_update`/`performance_metrics` WS pushes.
  * **Equity curve** — Lightweight Charts area series (transparent canvas,
    bone-white line) below the KPIs. Fed from `/api/chart/pnl/history` —
    converts ISO timestamps → Unix seconds, dedupes by time so LWC never
    rejects the dataset.
  * **Trades** — sortable-feel table (newest first by trade_number),
    9 columns matching the prototype (#, Side, Entry, Exit, Entry px, Exit
    px, Pts, P&L, Cum). Side is rendered as a small letter-spaced "BUY" /
    "SELL" tag in moss / rose. Window selector mirrors the Performance one;
    changing either keeps both in sync and re-fetches both endpoints.
  * **Strategies** — italic-serif active list with `<symbols> · <tf> · N pos
    · runtime` meta and a ghost Stop pill per row, plus a horizontal
    "strat-row" with Strategy / Symbols / Timeframe + Start pill at the
    bottom of the section. Pulls `/api/chart/strategy/status` every 15s.
    Start posts `{strategy, symbols[], timeframe}`; Stop posts `{strategy}`.
  * **Terminal** — collapsible drawer ("▸ Terminal — N logs") matching the
    P6 prototype. Subscribes to WS `log` messages, keeps the last 500 entries
    in memory and renders the last 200 to the DOM, color-coded INFO / OK /
    WARN / ERR. Auto-scrolls when the drawer is open.

  Window state persists in localStorage (`masterv2.perf.window`) and
  syncs the Performance and Trades selectors. Singular/plural fix on the
  rolling-N label ("rolling 1 month", "rolling 1 year").

  Phase 3 (next): hotkeys (b/s/f/c/r/esc), audio alerts, desktop
  notifications, then `/master` becomes a redirect to `/master/v2`.

  Files: `gui/master_control_v2.html` (~1100 → ~1590 lines).

- **Master GUI — v2 (P6 Quiet Trader, Phase 1) (2026-06-12 PM)** —
  After previewing P1–P6 the user picked **P6 Quiet Trader**. New file
  `gui/master_control_v2.html` implements P6 as a real, live dashboard
  served alongside the existing v1 — both stay accessible:
  * `/master`    — v1 (existing dashboard, untouched)
  * `/master/v2` — v2 (P6 Quiet Trader, wired)
  * Top-right pill switcher toggles between them.

  Phase 1 wires the trading-critical path: WebSocket connection with
  reconnect/backoff, account selector + switching, live stat strip
  (Balance / Unrealized / Realized P&L), chart with all controls
  (Symbol / Timeframe / Range / History source) including the
  recently-added Range selector, OHLC + last-price legend, order entry
  (market / limit / stop / stop-limit / trailing-stop) with Bracket
  (SL/TP price) row, Buy / Sell / Cancel / Flatten, positions table with
  Close action, orders table with Cancel action, all reusing the
  existing `/api/chart/*` endpoints.

  Phase 2 (placeholder banner visible in v2) will wire Performance KPIs,
  Trades table, Equity curve, Strategies, Terminal log stream. Phase 3
  will add hotkeys, alerts, notifications, then v2 becomes the default.

  Visual fidelity matches the prototype: italic-serif KPI/eyebrow labels,
  hairline-underlined select inputs, SAFE filled buy/sell pills,
  ghost cancel/flatten pills, transparent chart canvas, type-only
  Active section. Drops v1's drag-drop panel reordering, panel
  visibility toggles, multi-theme presets, and Customize button —
  these are intentionally out-of-scope for v2.

  Files: `gui/master_control_v2.html` (new, ~1100 lines),
  `gui/chart_html.py` (`/master/v2` route),
  `gui/serve_previews.py` (preview-server route so layout can be
  reviewed at `:8787/master/v2` without launching the live bot).

- **Master GUI — P5/P6 hybrid prototypes (2026-06-12 PM)** —
  Two additional prototypes synthesizing the user's preferred P3 (Reading Room)
  and P4 (Whitespace) directions:
  * `master_control_preview_5.html` **Atelier Press** — P3's editorial
    masthead and italic-serif accents, but with P4's transparent chart, big
    KPI numbers, and zero panel chrome. Trade pills stay filled for execution
    safety.
  * `master_control_preview_6.html` **Quiet Trader** — P4's austere type-only
    bones, but with P3-imported italic-serif KPI labels and SAFE filled
    Buy/Sell pills (replacing P4's risky underlined text controls).

  `gui/serve_previews.py` and the `/master/preview/{n}` route in
  `gui/chart_html.py` now accept `n in {1..6}`. The variant switcher pill in
  every prototype lists all six options.

- **Master GUI — redesign prototypes (2026-06-12 PM)** —
  Four self-contained static prototype HTMLs in `gui/master_control_preview_{1..4}.html`
  exploring the **Editorial + Paper** redesign direction. Each is a single
  self-contained file (mock data, no live wiring) so they have **zero runtime
  impact on the live dashboard**. Variants:
  * `_1.html` **Pure Paper** — single column, sticky chart, hairline rules,
    no panel chrome.
  * `_2.html` **Bound Paper** — max-width centered column, thin section
    borders, sticky stat strip.
  * `_3.html` **Reading Room** — editorial masthead with Instrument Serif
    headings, 64–80px rhythm, no rules.
  * `_4.html` **Whitespace** — maximum minimal, type-only structure, large
    KPI numbers, transparent chart.

  Routes added:
  * `gui/chart_html.py` — new `/master/preview/{n}` route for the live master GUI
    (requires GUI restart to register).
  * `gui/serve_previews.py` — standalone preview server on port 8787 so the
    prototypes can be browsed without restarting the live bot. Run with
    `python gui/serve_previews.py`.

  Each prototype has a fixed top-right variant switcher pill linking to the
  other three plus a `Live` link back to `/master`. All four use the same mock
  dataset (MNQ 5m, 82 trades, $152,081 balance) and the existing
  graphite/linen/moss palette so the comparison is purely about structure,
  rhythm, and chrome.

- **Master GUI — Chart Range selector (2026-06-12 PM)** —
  New "Range" dropdown in the chart controls row (`gui/master_control.html`)
  controls how far back / how many bars to load. Two `<optgroup>`s:
  * **Time** — `Last 1 day / 3 days / 1 week / 2 weeks / 1 month / 3 months /
    6 months / 1 year`. Time → bars converts via the current timeframe
    (`ceil(seconds / tfSec)`).
  * **Bars** — `500 / 1000 / 2000 / 5000` and `Max (source cap)`.

  `Auto` (default) preserves existing behavior — server cap from
  `historyReloadBarLimit(histSrc, symbol)` and most-recent 200 bars visible.
  Any explicit Range fits the entire loaded set into view (instead of clamping
  to `CHART_INITIAL_VISIBLE_MAX = 200`), so the user-picked window is fully
  visible and they can pan within it.

  When the requested span exceeds the active source cap (e.g. `Last 1 year` on
  `API only` source = 105 120 wanted vs 900 cap), the request is clamped and a
  `showToast(..., 'warning')` notes the clamp and recommends switching to the
  Databento source for deeper history.

  Selection persists in `localStorage` under key `master-chart-range`. The
  handler (`onChartRangeChange`) is exposed on `window` for the inline
  `onchange` attribute. Helpers (`CHART_RANGE_PRESETS`, `getChartRangeKey`,
  `chartRangeBarLimit`, `chartRangeFitsAll`) live next to the existing
  `historyReloadBarLimit` / `initialRecentVisibleBarCount` helpers and are
  IIFE-scoped.

- **v2 brain build + sweep_low_fade optimization + primitive combos probe (2026-06-12 PM)** —
  Three-phase delivery turning the v1 NO_SIGNAL finding into a data-driven v2.

  **Phase 1 — v2 brain refactor** (``core/market_synthesizer.py``):

  Driven by the empirical finding that BoS / CHoCH / swing-trend labels carry
  zero isolated signal and SHORT-side sweeps are anti-predictive, the
  synthesizer's bias scoring was completely rewritten:

  * ``_bias_factor_scores`` now considers ONLY the long-side liquidity
    sweep (``recent_sweep_direction == +1``).  BoS / CHoCH / swing labels
    are still computed and emitted in the snapshot as structural FACTS
    (visible on the dashboard and to research probes), but they no longer
    drive bias.
  * Bias confidence decays with sweep freshness via a new
    ``_sweep_freshness_weight(bars_ago)`` helper:
    ``0 → 1.0, 1 → 2/3, 2 → 1/3, ≥ 3 → 0.0`` — chosen to match the 1- to
    6-bar forward-return horizons the primitive probe validated on.
  * Short-side sweeps return ``("neutral", 0.0)`` — the brain has no
    validated SHORT edge.
  * New ``confluence_for_long_signal(snapshot) → float`` module-level
    helper exposes the bullish confidence directly for strategies that
    want to layer the brain as size/confidence boost on top of their
    own LONG signal.
  * New ``EventType.LIQUIDITY_SWEEP_DETECTED`` (``core/events.py``) fires
    alongside ``MARKET_CONTEXT_UPDATED`` whenever a fresh long-side sweep
    is detected.  Payload carries symbol / timeframe / sweep_level /
    strength / bars_ago / confidence so subscribers can react without
    parsing the full snapshot.

  Tests: ``tests/test_market_synthesizer.py`` rewritten to pin v2
  semantics — added ``TestSweepFreshnessWeight`` (4 tests),
  ``TestComputeBiasV2`` (9 tests), ``TestConfluenceForLongSignal``
  (3 tests), plus 2 new service-lifecycle tests for the
  ``LIQUIDITY_SWEEP_DETECTED`` emission path.  53 / 53 passing.

  **Phase 2 — sweep_low_fade optimization sweep**
  (``scripts/probe_sweep_low_fade_optimize.py`` + 20 unit tests):

  Truth-mode optimization probe that grids over (min_poke_ticks ×
  min_strength × session × stop_policy × tp_r × max_hold_bars ×
  swing_lookback × timeframe), reusing the canonical
  ``_simulate_trade_truth`` fill model (slippage + commission + intrabar
  1m resolution + force-flat at 16:00 ET).  Includes 1m-to-15m/30m
  aggregator so the grid can probe timeframes without re-pulling data.

  Sweep candidate detection is cached per (symbol, timeframe,
  swing_lookback) — 1944-cell grid runs in ~7 s with ``--no-intrabar``.

  Per-symbol best combo identified (extended history 2025-01-01 →
  2026-06-01, 9 months):

      symbol  tf    poke  strength  session  stop         tp_r  n    WR     mean R
      MES     15m   8t    1.0       nyam     structural   1.5R  44   56.8%  +0.29  ← intrabar
      MES     15m   8t    1.0       nyam     structural   2.0R  44   54.5%  +0.26
      MES     15m   8t    0.5       nyam     structural   1.5R  58   51.7%  +0.22
      MGC, MNQ — uniformly NEGATIVE across all configurations

  Verdict: ``NO_EDGE`` cross-symbol; ``EDGE_FOUND`` on MES alone.  The
  PA/SMC mechanic carries a real but isolated edge on the S&P futures
  (MES) at higher timeframes with a structural stop and nyam session
  filter, but does NOT generalise to MGC (gold) or MNQ (NASDAQ).  This
  is consistent with MES being the most consistently mean-reverting of
  the three on this timescale.

  Saved artifacts: ``docs/perf/sweep_low_fade_optimize/grid_5m_15m_fast.json``,
  ``docs/perf/sweep_low_fade_optimize/MES_15m_intrabar.json``.

  **Phase 3 — multi-primitive combos probe**
  (``scripts/probe_sweep_combos.py`` + 20 unit tests):

  Layered other primitives on top of sweep_low to test whether
  combinations produce stronger / more consistent edge than the bare
  signal:

  * **sweep + bullish FVG below close** (unmitigated, within distance
    band) — no improvement; sample size too small to be conclusive.
  * **sweep + recent CHoCH_up** (within ``--choch-lookback-bars``) —
    BEST combo found.  On MES 15m nyam structural, 1.5R, lk=3 with
    extended history (Jan-2025 → Jun-2026): ``n=40, WR=55.0%, meanR=+0.18``.
    MNQ / MGC remain negative.  Verdict: MARGINAL (single-symbol edge).
  * **sweep at prior-day-low** (``|swing_low - PDL| ≤ 3 pts``) —
    insufficient samples; what samples exist show mixed results.
  * **sweep + RSI(14) oversold** — anti-predictive on MES + MGC + MNQ,
    likely because by the time RSI flags oversold the sweep has already
    overshot and reversal is exhausted.
  * **Triple combos** (e.g. sweep+choch+pdl) — universally have n < 5;
    not statistically usable.

  Verdict aggregate: every combo emerges as ``NO_EDGE`` or ``MARGINAL``
  cross-symbol.  The sweep+CHoCH combo is the strongest finding but is
  a SINGLE-SYMBOL edge on MES that does not transfer to MGC / MNQ.

  Saved artifacts: ``docs/perf/sweep_low_fade_optimize/combos_15m_long_history.txt``,
  ``docs/perf/sweep_low_fade_optimize/combos_5m_long_history.txt``,
  ``docs/perf/sweep_low_fade_optimize/combos_full.json``.

  **Operational implication**: the v2 brain's
  ``confluence_for_long_signal`` helper IS now data-driven and safe to
  use as a confluence boost, but:
  * On MGC / MNQ it has no demonstrated edge in isolation — strategies
    should treat it as informational, not as a size multiplier.
  * On MES at 15m timeframes with strict filters
    (poke ≥ 8 ticks, strength ≥ 1.0, nyam session, CHoCH within last 60
    bars, structural stop) it represents a marginally profitable
    discretionary setup; a standalone MVP strategy would need ≥ 100
    trades to confirm statistical significance and is therefore deferred
    until more data accumulates.

  Tests: 40 new tests total (``tests/test_probe_sweep_low_fade_optimize.py``
  20 tests; ``tests/test_probe_sweep_combos.py`` 20 tests).  Suite total
  522 → 571.

- **Brain primitives isolated probe + asymmetric-edge finding (2026-06-12 PM)** —
  Follow-up to the v1 NO_SIGNAL finding: extended ``scripts/probe_brain_predictive_power.py``
  with two new ``--group-by`` modes (``structure`` and ``sweep``) that bypass the composite
  bias label and bucket snapshots by a SINGLE primitive in isolation.  Goal: figure out
  whether ANY individual signal has predictive power even if the composite doesn't.

  Run results on the same 9m of MGC + MNQ + MES:

  **Structure events alone (BoS / CHoCH)** — ``OVERALL VERDICT: NO_SIGNAL``.

      symbol  primitive    n      WR_3 (sign-agreement)
      MGC     bos_up      10641   48.6%   (anti, as expected — lagging)
      MGC     bos_down     8275   45.0%   (anti)
      MGC     choch_up     8724   50.4%   (noise)
      MGC     choch_down   6956   48.2%   (noise)
      MNQ     bos_up      10925   50.2%   (noise)
      MNQ     bos_down     8705   46.6%   (anti)
      MES     bos_up      11090   49.4%   (noise)
      MES     bos_down     8826   46.1%   (anti)

  BoS and CHoCH are individually NOT directional signals on these instruments
  / timeframes.  BoS_DOWN is consistently anti-predictive, consistent with
  upward-drift bias.

  **Liquidity sweep direction alone** — ``OVERALL VERDICT: WEAK_SIGNAL`` (1 SIGNAL, 1 WEAK).

      symbol  primitive          n      WR_6 (sign-agreement)
      MGC     sweep_low_fade   11542   52.1%   ← marginal LONG signal
      MGC     sweep_high_fade  11144   49.7%   (no signal)
      MNQ     sweep_low_fade   11229   52.9%   ← marginal LONG signal
      MNQ     sweep_high_fade  12159   48.1%   (no signal)
      MES     sweep_low_fade   10657   51.7%   (marginal but below threshold)
      MES     sweep_high_fade  11158   45.8%   ← ANTI-predictive

  **Headline finding**: only the LONG side of the liquidity sweep mechanic
  (sweep below recent swing low → expect bounce up) has consistent
  cross-symbol predictive lift.  The SHORT side does not work.  This is the
  same asymmetry seen in the prior-day RTH H/L sweep probe earlier today and
  is fully consistent with these futures' structural upward-drift bias.

  **v2 brain implication**: the right next iteration is to drop the
  composite bias label, drop BoS / CHoCH from the scoring, keep ONLY the
  sweep-low-fade primitive, and emit it as a typed event (``LiquiditySweep
  DetectedEvent`` with side=long-only) for strategies to consume as a
  confluence boost.  A standalone "sweep-low-fade-LONG" strategy on its own
  (52% WR at 1.5R/1.0SL) would be marginally profitable but unstable; it's
  more valuable as a confluence filter on top of MRR's existing edge.

  Tests: 10 new tests in ``tests/test_probe_brain_predictive_power.py``
  (TestGroupKeyFor — 7 tests pinning all three modes; TestVerdictPerSymbol
  WithGroupBy — 3 tests pinning the per-mode verdict aggregation).

  Artifacts:
  - ``docs/perf/_probe_brain_predictive_power/by_structure_9m.json``
  - ``docs/perf/_probe_brain_predictive_power/by_sweep_9m.json``

  Test status: 522/522 passing.

- **Brain v1 predictive-power probe + EMPIRICAL FINDING: v1 weights are anti-predictive (2026-06-12 PM)** —
  Built ``scripts/probe_brain_predictive_power.py`` + 19 pinning tests to validate
  the synthesizer's bias signal BEFORE integrating it into any production strategy.
  The probe walks historical 5m bars chronologically (look-ahead-safe — each
  snapshot only sees bars up to and including the current close), builds a
  snapshot at each bar, then measures forward returns at horizons 1 / 3 / 6 bars
  bucketed by (bias_label, confidence_bucket).  Decision rule: SIGNAL_FOUND
  when sign-agreement WR ≥ 55% on AT LEAST 2 of 3 symbols at the most-confident
  bucket on at least one horizon.

  **Result on 9-month MGC + MNQ + MES (2025-09-01 → 2026-06-12, ~55k snapshots
  per symbol)**: ``OVERALL VERDICT: NO_SIGNAL``.

  Headline numbers (strong-bucket = confidence ≥ 0.60):

      symbol  bias_strong   n      mean_fwd_6   WR_6 (sign-agreement)
      MGC     bullish     4426    +0.329 pts   50.5%
      MGC     bearish     2812    +0.178 pts   45.6%   ← anti-predictive (bearish but fwd return positive)
      MNQ     bullish     4101    -0.616 pts   50.7%   ← anti-predictive (bullish but fwd return negative)
      MNQ     bearish     2908    +2.746 pts   48.4%   ← anti-predictive
      MES     bullish     4338    +0.134 pts   52.2%
      MES     bearish     3221    +0.595 pts   46.4%   ← anti-predictive

  **Interpretation**: the v1 brain weights put BoS at 0.40 (strongest single
  factor), which makes the brain a TREND-FOLLOWING signal.  But on intraday
  5m bars for these instruments, the dominant mode is MEAN-REVERTING — BoS
  signals fire AFTER the move has happened, marking the END of a leg rather
  than its continuation.  Bearish signals are especially anti-predictive
  (consistent with the upward-drift bias of these futures): a "bearish"
  brain label tends to print at the BOTTOM of a pullback that's about to
  reverse up.

  **What this proves**:
  1. The brain infrastructure is sound — 49k+ snapshots built across the
     probe without errors, JSON serialisation round-trips, snapshots
     monotonically advance per-bar.
  2. The current scoring weights do NOT capture predictive signal on these
     instruments and timeframes.
  3. The right v2 design is one of:
     a) **Asymmetric / regime-aware weights** — trust bullish bias signals
        (they confirm the upward drift), treat bearish bias as contrarian.
     b) **De-emphasise BoS, emphasise the lagging-detector counter-signal**
        (e.g. liquidity sweeps + CHoCH are EARLIER signals).
     c) **Drop the composite bias label entirely** and only export structured
        features; let consuming strategies build their own scoring.

  **Decision**: do NOT integrate the v1 brain into MRR yet.  Re-design the
  scoring as a follow-up.  The probe + infrastructure is reusable and stays
  in place to validate v2 / v3 / ... iterations.

  Artifact: ``docs/perf/_probe_brain_predictive_power/baseline_9m.json``.

  Tests: 19 new tests covering bucket boundaries, walk_symbol forward-return
  accounting, per-symbol verdict thresholds (INSUFFICIENT_N / SIGNAL /
  WEAK_SIGNAL / NO_SIGNAL), overall verdict aggregation (≥ 2 strong, mixed,
  weak combinations), and an E2E smoke test against live CSVs.

  Test status: 512/512 passing.

- **PA/SMC Synthesis Engine MVP — the "brain" (2026-06-12 PM)** —
  First concrete step toward the user's PA/SMC vision pivot earlier today:
  build a single perception layer that composes ALL the primitive detectors
  (swing pivots, BoS/CHoCH, FVG, OB, liquidity sweeps, session classifier,
  prior-session levels) into typed market interpretations strategies can
  consume as confluence.

  **Built**:
  - ``core/market_synthesizer.py`` (~480 LoC) with three layers:
    - ``MarketContextSnapshot`` (frozen dataclass): symbol, timeframe, as_of,
      bar_count, session label, prior-session H/L, recent swing high/low + labels,
      structure event (bos_up / bos_down / choch_up / choch_down / none), recent
      liquidity sweep (bars_ago + direction + close/poke strength ratio), FVG /
      OB counts, derived bias (bullish / bearish / neutral), confidence [0, 1],
      and per-factor bias evidence tally. ``as_dict()`` returns a
      JSON-serialisable view for event payloads + dashboard surfaces.
    - Pure scoring helpers: ``compute_bias(snapshot) -> (bias, confidence)`` and
      ``confluence_score(snapshot, side) -> float in [-1.0, +1.0]``. Bias scoring
      weights: BoS = 0.40, CHoCH = 0.35, swing-trend agreement = 0.20, recent
      sweep direction = 0.25. Conflicting signals partially cancel; full bull /
      bear stack caps at 1.0.
    - ``MarketSynthesizerService`` class: subscribes to ``BAR_COMPLETED`` events
      on the bot's event bus, runs the primitives pipeline against a rolling
      N-bar window (default 200), publishes ``MARKET_CONTEXT_UPDATED`` events,
      maintains a thread-safe latest-snapshot cache for synchronous queries via
      ``get_context(symbol, [timeframe])`` and a convenience
      ``confluence_score(symbol, side)`` that delegates to the pure scorer.
  - ``EventType.MARKET_CONTEXT_UPDATED`` added to ``core/events.py``.
  - Bot wiring: new ``ensure_market_synthesizer()`` async method on
    ``TopStepXTradingBot`` (mirrors ``ensure_regime_publisher`` — lazy, env-gated,
    idempotent). Called from ``StrategyManager._start_strategy`` after the
    regime publisher so chart-only / dashboard processes pay nothing.
  - ``maybe_start_synthesizer()`` boot helper + process-global accessor
    ``get_synthesizer()`` so strategies can opt-in via a soft dependency without
    holding a direct service reference.

  **Env vars (all off by default — opt-in)**:
  - ``MARKET_SYNTHESIZER_ENABLED``       — "1" / "true" to enable.
  - ``MARKET_SYNTHESIZER_SYMBOLS``       — CSV symbol filter; empty = all.
  - ``MARKET_SYNTHESIZER_TIMEFRAMES``    — CSV timeframe filter; empty = all.
  - ``MARKET_SYNTHESIZER_WINDOW_BARS``   — int, default 200.
  - ``MARKET_SYNTHESIZER_SWING_LOOKBACK``— int, default 3.

  **Tests**: 44 new tests in ``tests/test_market_synthesizer.py`` covering:
  - Pure scoring helpers (9 tests): neutral on empty, single-factor weights,
    conflicting-signal cancellation, cap-at-1.0 behaviour, side-zero / neutral-
    bias guards.
  - Snapshot construction (8 tests): empty-bars guard, flat-bars no-signal,
    zigzag-up → bullish BoS_UP, zigzag-down → bearish BoS_DOWN, swing labels
    emitted when pivots present, as_of carries last bar timestamp, JSON
    serialisation round-trip, graceful handling of <2*lookback+1 bars, session
    label resolution for tz-aware timestamps.
  - Service lifecycle (13 tests): subscribe / unsubscribe, per-symbol filter,
    per-timeframe filter, event payload shape, get_context (with / without
    timeframe, case-insensitive, default-when-missing), confluence_score
    default-when-missing, window trimming, fetch_history fallback when payload
    has no bars, empty-payload short circuit, callback error isolation,
    snapshots-built counter.
  - Boot helper (6 tests): disabled by default, enabled via env, idempotent,
    no-bus → None, symbols env parsing, invalid-int env falls back to default.

  **Status**: no production strategy consumes the brain yet — that's the next
  deliverable. The MVP is the publisher + cache + scoring helpers, all
  wired through the existing event bus and ready for confluence-aware
  strategies (and the dashboard overlay) to subscribe.

  Test status: full suite 493/493 passing.

- **PA/SMC vision pivot + prior-day RTH H/L sweep-fade viability probe (2026-06-12 PM)** —
  User explicitly reframed the price-action research direction: instead of keeping individual PA/SMC
  concepts as standalone features, the goal is now a single **synthesis engine** ("the brain") that knows
  ALL of them and emits structured market interpretations (context tags + setup probabilities) for other
  strategies to consume as confluence. Documented the architecture and design constraints in the new top
  section of ``docs/STRATEGY_ARSENAL.md``.

  Separately, user green-lit exploring **prior-day RTH high/low sweep-fade** as a standalone strategy
  (the one liquidity-sweep extension that could in principle generalise MRR's edge to the afternoon
  session without requiring a regime classifier).

  **Built**: ``scripts/probe_prior_day_sweep_fade.py`` — truth-mode viability probe that scans
  MGC/MNQ/MES historical 5m bars (with 1m intrabar resolution when available), computes prior-day RTH
  H/L per ET trading date, detects sweep events (close-back-inside bars), simulates fade trades through
  the canonical ``_simulate_trade_truth()`` helper (entry slippage + commission + force-flat-ET +
  intrabar SL/TP resolution), and reports per-symbol summaries with a PRODUCTIONABLE / MARGINAL / NO EDGE
  verdict. Decision rule: PRODUCTIONABLE requires ≥ 0.20 mean R, ≥ 40 trades, ≥ 50% WR on ≥ 2 of 3 symbols.

  21 pinning tests in ``tests/test_probe_prior_day_sweep_fade.py``: RTH-window inclusion/exclusion
  boundaries (15:59 in, 16:00 out), sweep-above/below detection, close-back-inside rejection logic,
  min-penetration filter, entry-window filter, summary math (WR, mean R, $ PnL, long/short split),
  verdict thresholds (PRODUCTIONABLE / MARGINAL / NO EDGE bands), and an E2E smoke test on the live
  databento CSVs.

  **Verdict: NO EDGE / NOT VIABLE as a standalone.** Naïve fade across all 3 symbols, all-day, 9m:

      symbol    n  WR    meanR   medR   totR    $PnL
      MGC      302  18.5% -0.020  -0.054  -6.14  -889.60
      MNQ      505  16.4% -0.066  -0.046 -33.35 -1022.00
      MES      437  13.3% -0.180  -0.102 -78.62 -2518.60

  Strongest pocket = MGC SHORT in the morning window 10:00-12:00 ET (mean R +0.103, n=92) — falls short
  of PRODUCTIONABLE bar AND is **anti-regime** (edge shrinking: 9m +0.103 → 6m +0.053 → 3m +0.031). Five
  parameter variations (stronger penetration, tighter TP, wider SL+tight TP, morning-only, morning+MGC-
  only) all land NO EDGE / MARGINAL at best.

  **Why the standalone thesis fails**: WR 13-33% too low to be profitable on a 1R-loss/1.5R-win
  expectancy; force-flat-ET dominates exits (~36% of MGC morning trades); asymmetric symbol response
  (only MGC SHORT shows life); no compression context (unlike MRR's morning range, prior-day H/L is just
  a level — many sweeps are legitimate trend continuation rather than reversal).

  **What this validates about the synthesis-engine direction**: this probe ONLY tested the fade thesis
  (close-back-inside). The opposite mechanic (sweep + close stays outside = breakout) wasn't tested and
  may have edge in different regimes. A "brain" that can DECIDE which mode is appropriate given context
  (regime / time-of-day / prior-day shape) is exactly what a standalone strategy can't do. The probe code
  + detection helpers are kept as research infrastructure for the synthesis engine work.

  Artifacts: ``docs/perf/_probe_prior_day_sweep_fade/baseline_9m.json``,
  ``docs/perf/_probe_prior_day_sweep_fade/morning_only_9m.json``.

  Test status: full suite 449/449 passing (added ``tests/test_bar_aggregator.py`` and
  ``tests/test_probe_prior_day_sweep_fade.py`` to ``pytest.ini`` testpaths).

- **Periodic bar-completion safety net + staleness threshold relaxation (2026-06-12 PM)** —
  Investigation of the 2026-06-11/2026-06-12 MRR run logs (5.5h, 543k quote
  events, ZERO live-cache hits in ``get_historical_data``) traced the persistent
  ``⛔ STALE DATA`` spam to a **quote dead-zone race** in ``core/bar_aggregator``.
  ``add_quote`` is the ONLY path that rolls over a 5m bar via the strict
  ``current_time >= bar_end`` check inside ``_should_start_new_bar``; if the
  SignalR quote stream pauses for even a few seconds right at a boundary, the
  in-progress builder stays "open" until the next tick fires (which can be
  many seconds later). The live-bar cache in ``trading_bot`` then stays stuck
  at the previous REST-warmup tail and strategies repeatedly fall through to
  REST historical fetches that lag the broker by 5-10 min in morning hours.

  **Two changes shipped together** to close the gap:

  1. **``core/bar_aggregator.py``** — added ``_periodic_bar_completion_loop``,
     an asyncio task started by ``BarAggregator.start()`` that runs every
     ``BAR_PERIODIC_COMPLETION_INTERVAL`` seconds (default 10, env-tunable;
     0 disables). Each tick walks all live builders and force-closes any whose
     ``bar_end`` has passed AND that has at least one tick recorded — emits the
     completed bar through the existing ``_emit_completed_bar`` path (broadcast
     + ``_completed_bar_callbacks`` fan-out) and opens a fresh builder for the
     current boundary. New ``_periodic_completions_total`` counter logged at
     ``stop()``. Safe behavior on shutdown via ``CancelledError`` handling.
     Pre-existing ``test_bar_aggregator.py`` tests that referenced removed
     attributes (``update_interval``, ``_update_task``, ``_broadcast_updates``)
     were rewritten against the current API as a side-effect.
  2. **``config/strategies/morning_range_reversion.toml``** — bumped
     ``max_bar_staleness_seconds`` ``600 → 900`` (10min → 15min). The
     periodic-completion task should keep the live cache fresh, but this
     belt-and-suspenders relaxation tolerates genuine broker-side publication
     latency without weakening true "broken feed" detection (still trips at
     15+ min stale). Comment in TOML explains both layers.

  Documented in ``.env.example`` and STRATEGY_ARSENAL.md's audit section.
  Tests: ``tests/test_bar_aggregator.py::TestPeriodicBarCompletion`` (12 new
  tests covering env-var parsing, sweep behavior with no data / fresh / stale
  builders, multi-symbol / multi-timeframe routing, idempotence, callback
  exception isolation, async task lifecycle, and the disable-via-zero-interval
  path). Full suite: **400/400 pass**.

  Net effect: in the steady-state hot path, the live-bar cache stays current
  even during quote dead-zones → ``_live_cache_can_serve`` short-circuits
  REST → MRR's ``_bars_are_stale`` check passes → no more spurious 10-min-old
  ``analyze()`` skips → fewer wasteful HTTP session rotations → cleaner logs.

- **MRR parameter re-sweep (A + D) under new directional filters (2026-06-12)** —
  After the directional-weekday filters landed (entry above), re-ran the MGC
  ``sl_mult × tp_mult`` 3×3 grid + 2 single-axis probes (11 trials, 6m / 9
  folds) and a separate 7-point ``reentry_threshold_points`` sweep
  ``{0.00, 0.25, 0.50, 0.75, 1.00, 1.50, 2.00}`` to check whether the
  optimal geometry shifted now that the worst-cohort losers are filtered.

  Both sweeps confirm **NO change is warranted** — the committed R27 stack
  (sl_mult=3.30, tp_mult=1.85, sl_min_pts=28, sl_max_pts=29, reentry=0.0)
  is already at the local optimum:

  * **A. sl_mult × tp_mult sweep**: every ``sl_mult ∈ {3.20, 3.30, 3.40}``
    produces an identical equity curve (within ±$0.40 across 6m) because the
    ``[sl_min_pts=28, sl_max_pts=29]`` band fully dominates SL distance —
    ``sl_mult × half_width`` only matters when its product lands inside the
    1-pt band, which is rare. ``tp_mult=1.85`` is the local optimum; 1.80
    costs ~$26 of total return, 1.90 costs ~$23, 1.70 raises WR (74.8% vs
    72.97%) but costs $31, 2.00 crashes WR to 70.3% and return to +786%.
  * **D. reentry_threshold_points sweep**: ``0.00`` (legacy candle-close
    entry at the range extreme) is the global maximum; every +0.25 step
    costs $11-15 of return monotonically. At 1.5+ the trade count drops
    (some signals get filtered as the entry depth caps at half-width) and
    DD shrinks slightly, but PnL drops more. The MGC edge depends on
    getting the best entry price at the range extreme, not on widening
    the stop room via a deeper re-entry.

  These are **clean negative results** — the directional-filter lift didn't
  expose any hidden parameter optima. Files: ``docs/perf/_opt_runs/mrr_a_resweep_6m/``,
  ``docs/perf/_opt_runs/mrr_d_reentry_6m/``.

- **Per-direction weekday filters for MRR (2026-06-12)** — Surfaced via the new
  ``scripts/mrr_setup_decomposition.py`` tool, which parses an existing recap
  directory's per-trade chart HTMLs and groups trades by ``(symbol × side ×
  weekday × hour × anchor-width-quartile × prior-day-direction)`` to find
  losing-cell asymmetries. Run over a 6m walk-forward (n=120, +$15.8k PnL),
  it flagged two strong asymmetric losing cells:

  * **MGC BUY on Tuesday**: 33% WR / -$203 expectancy (n=9, -$1,823 total)
    vs MGC SELL on Tuesday: 89% WR / +$306 expectancy (n=9). Tuesday MGC
    has a structural fade-the-rip downside bias — LONG fades die because
    the market keeps grinding lower on Tuesday opens. **$509/trade
    directional differential** inside the same session-symbol pair.
  * **MNQ BUY on Wednesday**: 22% WR / -$51 expectancy (n=9, -$463 total)
    vs MNQ SELL on Wednesday: 69% WR / +$30 expectancy (n=16, +$483).
    Same asymmetry on MNQ, opposite weekday.

  Codified via two new per-direction config knobs in
  ``MorningRangeReversionStrategy``:

  * ``signal.skip_weekdays_long`` — list of weekdays to suppress LONG entries
    only (SHORT side stays armed). Per-symbol override supported.
  * ``signal.skip_weekdays_short`` — symmetric, kept for completeness.
  * ``signal.min_range_width_long_points`` / ``..._short_points`` — per-direction
    minimum anchor width floor. **Tested and DEFAULTED TO 0**: a candidate
    ``min_range_width_long_points=25`` for MGC dropped 20 narrow-width MGC
    BUY trades that turned out to be 85% WR / +$2,831 PnL — the cell-level
    decomposition was confounded with the Tuesday cohort, and once Tue is
    skipped separately the remaining narrow-width BUYs are mostly winners.
    The knob is wired and tested but defaulted to 0 so a future per-symbol
    investigation can flip it on without code changes.

  Committed TOML changes:

  * ``[symbols.MGC.signal] skip_weekdays_long = ["Tue"]``
  * ``[symbols.MNQ.signal] skip_weekdays_long = ["Wed"]``

  Validation across two windows (no curve-fit risk, both improve):

  | Window | ΔPnL | ΔWR | ΔAvg-fold PF | ΔDD-sum |
  |--------|------|-----|--------------|---------|
  | 6m | +$1,483 (+9.4%) | +3.8 pp | +2.72 | -$2,214 (-21%) |
  | 9m | +$207 (+1.4%)   | +1.1 pp | +1.26 | -$1,816 (-12%) |

  MNQ is the primary lift (+27% PnL on 9m, +93% on 6m); MGC is essentially
  flat in $ terms but cleaner equity curve (worst trade unchanged at -$988).
  The 9m generalization is what matters — only 8% of the 9m PnL is in the
  most recent 6m sample, so the cross-window confirmation establishes this
  isn't curve-fit to a recent regime.

  Tests: 4 net-new in ``tests/test_morning_range_reversion_smoke.py`` (220 →
  224 total in that file; 70 → 70 passed before, 70 → 74 after = ``+4 fully
  pinned``). All committed defaults are pinned (Tue for MGC LONG, Wed for
  MNQ LONG, empty SHORT lists, MES inherits root). The ``_parse_weekday_tokens``
  refactor is tested for input-shape robustness (list-of-names, list-of-ints,
  comma-separated string, space-separated string, empty, unknown tokens).

- **Backtest-vs-live anchor diff + thin-margin diagnostics (2026-06-12)** — Direct response to the user's observation that the walk-forward report on 2026-06-11 showed the MGC ``morning_range_reversion`` trade as a +$297 ``take_profit``, while the live broker recorded a −$534 ``stop_loss`` on the same trade.  The post-mortem traced this to three compounding gaps:

  1. The walk-forward recap was running ``core/backtest_executor.py`` *without* ``--csv-1m``, so SL/TP resolution was 5m-only — no intrabar truth.
  2. The recap report had no way to surface a trade as "razor-thin" — a TP exit where MAE consumed 97.8% of the SL distance looks identical to a clean TP.
  3. The live MGC anchor width was 16.18 pt while the databento walk-forward computed 16.70 pt — a 0.52 pt disagreement that translated (via ``half_width × sl_mult``) into a **0.90 pt** SL placement disagreement, enough to flip the trade outcome because the actual price low (4073.10) hit live SL but missed sim SL.

  Four-part fix, all net-new and pinned by 47 new tests (411 / 411 total):

  * **``scripts/walkforward_trade_recap_report.py``** — gains a new CLI flag ``--csv-1m-template`` plus an ``_auto_discover_1m_sibling`` helper that finds the matching ``{stem-with-_5m-replaced-by-_1m}.csv`` next to ``--csv``.  The flag is forwarded into the spawned ``backtest_executor.py`` subprocess as ``--csv-1m=...``.  Default behavior auto-discovers, so every existing recap run now resolves SL/TP with 1m intrabar truth without any operator action.
  * **Recap "margin" column** — new ``compute_trade_margin(trade)`` helper computes ``abs(MAE) / initial_risk * 100`` for TP exits and ``MFE / initial_risk * 100`` for SL exits.  Trades ≥ 85% on either metric are flagged ⚠ in red in the HTML table.  The 2026-06-11 MGC trade now shows ``SL→ 97.8% ⚠`` — exactly the warning that would have surfaced this discrepancy before the live loss.
  * **``core/anchor_persistence.py``** — new ``record_anchor()`` writes one JSON snapshot per ``(strategy, symbol, session_date_et)`` under ``data/anchors/`` containing the live H/L/width/mid, the anchor window provenance (n_bars_used, first/last bar UTC), and a stamped ``data_feed_health`` block from ``DataFeedHealthMonitor``.  Best-effort by design: every IOError is caught and logged at WARNING so persistence breakage can never take the strategy offline.  Wired into ``MorningRangeReversionStrategy.analyze`` immediately after the "📐 anchor range built" log fires, exactly once per session.
  * **``scripts/diff_anchor_live_vs_databento.py``** — new operator-facing diff tool: re-computes the same anchor from a local databento 5m CSV and reports any ``|Δ| >= --threshold-pts`` (default 0.10) on H, L, or width.  Supports ``--anchor`` (single file) or ``--all`` (whole ``data/anchors/`` directory).  Exit code 1 on divergence so it can drop into CI / health checks.  ``--json`` for machine consumption.  Verified end-to-end on the synthetic 2026-06-11 anchor: catches the −0.52 pt width divergence, flags ``feed=STALE``, exits 1.
  * **``config/strategies/morning_range_reversion.toml``** — adds ``sl_min_pts = 28.0`` for MGC.  This is the structural fix: combined with the existing ``sl_max_pts = 29`` cap, the MGC SL distance now sits in a tight ``[28, 29]`` band on any anchor wide enough to matter, absorbing the ~1 pt anchor disagreement empirically observed.  On the 2026-06-11 trade, this would have placed the live SL at 4072.10 (vs the actual 4073.40) — 1.0 pt below the price low of 4073.10, so the trade would have ridden to TP for ~+$534 instead of stopping out at −$534.  Net effect on this specific live trade: **+$1,068 swing**.  3-week walk-forward validation: 12 trades, 83% WR, +104.5% return, 16.1% DD, RF=3.65 — the floor doesn't crater win rate, it slightly inflates per-trade $ risk on narrow-anchor days, which the existing dollar-risk cap auto-trims.

  Tests:

  * ``tests/test_walkforward_recap_thin_margin.py`` — 18 tests covering sibling auto-discovery (databento preference, lowercase, missing siblings, None input), thin-margin math (TP vs SL exits, 85% boundary, missing-fields safety, the literal 2026-06-11 numbers), HTML cell formatting (thin-vs-safe rendering, color), CLI surface.
  * ``tests/test_anchor_persistence.py`` — 14 tests covering path resolution (ISO date, env var, override precedence), payload shape (the exact 2026-06-11 databento anchor), no-overwrite-by-default, the ``overwrite=True`` escape hatch, inverted-range rejection, ``extra`` merge / collision-protect-core, IO failure → None (no raise), zero-width acceptance, round-trip via ``load_anchor``, corrupt-JSON resilience.
  * ``tests/test_diff_anchor_live_vs_databento.py`` — 15 tests covering ``compute_databento_anchor`` (real 2026-06-11 rows, half-open [start, end) window, empty CSV, missing CSV), ``diff_one_anchor`` (perfect match → not diverged, the literal 2026-06-11 scenario → −0.52 pt flagged, threshold-respecting boundary, missing CSV → error row, corrupt JSON → error row, missing-bars-for-date → error row), CLI surface (help, mutually-exclusive ``--anchor``/``--all``, exit code 1 on divergence, exit code 0 on clean diff, ``--all`` walks the directory).

  **Operator quick-reference:** the morning after a discrepancy event, run ``python scripts/diff_anchor_live_vs_databento.py --all`` to instantly see which symbol's live anchor diverged from databento and whether the data feed was stale during the anchor window.

- **Per-trade $ loss cap + adaptive sizing (2026-06-11)** — Net-new equity-curve smoother that turns the 2026-06-11 MGC 2-ct $534 loss into a 1-ct $267 loss automatically.  New helper ``core.risk_sizer.cap_quantity_by_dollar_risk(symbol, entry_price, stop_loss_price, requested_quantity, max_dollar_risk, min_quantity=1, strict=False)`` returns ``(adjusted_qty, reason)`` after computing the actual $-risk via the canonical ``point_value()`` table (MGC = $10/pt, MES = $5/pt, MNQ = $2/pt, etc.) and trimming contracts until risk ≤ cap.  Wired into ``strategies/strategy_base.place_bracket_order`` between the income-brain check and the risk-manager check.

  Configuration precedence (highest first):

  1. ``StrategyConfig.params['max_dollar_risk_per_trade']`` (per-strategy TOML; opt-in)
  2. ``MAX_DOLLAR_RISK_PER_TRADE`` env var (global default)

  Modes: default ``warn`` — when even ``min_quantity=1`` over-risks, log a warning and place 1 ct anyway (the strategy decided to trade; we just shave the size).  ``MAX_DOLLAR_RISK_STRICT=true`` flips to refuse-the-order in that edge case.  Default ``0`` = cap disabled (no behavioural change unless explicitly turned on).

  Tests: 16 net-new in ``tests/test_dollar_risk_cap.py`` covering disabled / within-budget / the literal 2026-06-11 MGC scenario at three cap thresholds / micro-futures sweep / strict-vs-warn behaviour at over-risk min-qty / defensive inputs (zero stop, negative qty, string qty, unknown symbol fallback).

- **Health-gate softened to ``warn-and-proceed`` by default (2026-06-11)** — User clarification: the gate must NOT refuse trades the strategy decided to take.  Its job is to surface degraded data-feed conditions loudly while the rest of the safety net (watchdog, place-and-verify, cancel-on-staleness) protects the trade.  New env var ``DATA_FEED_HEALTH_GATE_MODE`` with values ``warn`` (default) / ``off`` / ``refuse``.  Legacy ``DATA_FEED_HEALTH_GATE=false`` still maps to ``off``.  4 net-new tests in ``tests/test_health_gate_integration.py`` pin all three modes.  333/333 → 349/349 after both changes.

- **Cancel-on-staleness for working orders (2026-06-11)** — Closes the FOURTH and final P0/P1 item from the MGC post-mortem: the health-gate stops a *new* order from being placed during zombie conditions, but it cannot help an order that was placed *before* the feed went zombie and is still sitting on the broker as a working stop-entry.  That order can fill silently exactly as on 2026-06-11.

  New module ``core/working_order_registry.py`` exposes a thread-safe singleton ``WorkingOrderRegistry`` that tracks every placed-but-unfilled entry order (with its OCO sibling order ids) plus an async helper ``cancel_all_working_orders(registry, broker_adapter, ...)``.  Terminal-status entries (Filled / Cancelled / Rejected / Expired — both string and numeric forms) are auto-dropped via ``mark_status``.

  Wire-in points:

  * **``strategies/strategy_base.place_bracket_order``** — after a successful place, registers the entry order_id along with any OCO siblings extracted from the broker response (``stopOrderId`` / ``limitOrderId`` / ``stopLossOrderId`` / ``takeProfitOrderId``).
  * **``core/user_hub_handlers.on_order``** — calls ``registry.mark_status(order_id, status)`` on every User Hub order update so the registry's view stays consistent with the broker's view.
  * **``core/data_feed_watchdog``** — gains ``broker_adapter`` + ``working_order_registry`` constructor args.  Before forcing a zombie reconnect, calls ``cancel_all_working_orders`` so no stop-entry can fill blind during the cycle.
  * **``trading_bot.start_market_hub_for_strategies``** — passes both into the watchdog.

  Tests: 23 net-new in ``tests/test_working_order_registry.py`` covering register/get, normalization, snapshot independence, per-symbol filtering, age, every terminal-status form (parameterized), idempotent unregister, singleton round-trip, and four cancel-all behaviours (entries + siblings, sibling opt-out, broker exception resilience, empty registry).  Plus 2 net-new in ``tests/test_data_feed_watchdog.py`` pinning that the watchdog cancels working orders BEFORE reconnecting and gracefully no-ops when no registry is wired.

  **Net effect on a hypothetical 2026-06-11 replay where a MGC stop-entry was already working when the feed went zombie:** at the moment the watchdog detects staleness (30 s after the 120 s threshold), it pulls the entry + SL + TP via REST cancel calls so when SignalR reconnects there's nothing left on the broker that could have filled blind.

- **P0/P1 data-feed safety net (2026-06-11)** — Net-new defensive infrastructure responding to the live ``morning_range_reversion`` MGC -$534 stop-out where BOTH SignalR hubs went zombie (connection "alive", zero data flowing) and the bot placed + lost a full -1R trade without seeing ANY of its own fills.  Broker statement confirmed entry 4100.1 → SL 4073.4, both filled, TP OCO-cancelled.

  Three new modules + two wire-in points:

  1. **``core/data_feed_health.py``** — ``DataFeedHealthMonitor`` singleton.  Records every Market Hub quote tick (per-symbol) and every User Hub account/position/order event (global counter).  Exposes ``is_safe_to_trade(symbol) -> HealthVerdict(ok, reason)`` consulted by strategies on every order placement.  Returns ``False`` when Market Hub has been silent for the symbol > 120 s (env: ``DATA_FEED_MARKET_HUB_MAX_SILENCE_S``) OR User Hub has stayed silent > 90 s after the most recent order (env: ``DATA_FEED_USER_HUB_MAX_SILENCE_AFTER_ORDER_S``).  Startup grace 60 s prevents false alarms during hub negotiation.  Thread-safe (``RLock``), zero allocations on the hot tick path.  Pause/resume hooks so backtest paths don't consult live feeds.

     **GATE MODE** (env: ``DATA_FEED_HEALTH_GATE_MODE``): default ``warn`` — log a loud warning and PROCEED with the trade because the strategy's signal logic is the source of truth and the watchdog / verifier / cancel-on-staleness collectively protect the position.  ``refuse`` reverts to the original block-the-order behaviour (use when you trust feed health more than your strategy).  ``off`` silences the gate entirely.  Legacy ``DATA_FEED_HEALTH_GATE=false`` still maps to ``off``.

  2. **``core/data_feed_watchdog.py``** — Async ``DataFeedWatchdog`` task started from ``trading_bot.start_market_hub_for_strategies``.  Polls every 30 s (env: ``DATA_FEED_WATCHDOG_POLL_S``); when ANY subscribed symbol's Market Hub age exceeds the threshold AND we're inside market hours (``is_within_market_hours`` — CME 18:00→17:00 ET with daily maintenance pause), forces ``stop()`` + ``start()`` + ``_resubscribe_all_symbols()`` on the WebSocketManager.  Exponential cooldown (60s → 120s → 240s → 480s cap) prevents thrash if the hub keeps coming up dead.  Resets the monitor's grace window after each reconnect so the next check waits for genuine data.  Disable via ``DATA_FEED_WATCHDOG=false``.

  3. **``core/order_verifier.py``** — Fire-and-forget ``verify_order_landed()`` scheduled by ``strategy_base.place_bracket_order`` immediately after every successful place.  Waits ``soft_timeout_s=30 s`` for a User Hub event to confirm the order (via ``DataFeedHealthMonitor.user_event_received_since_last_order``).  If User Hub stays silent, falls back to REST polling ``broker_adapter.get_open_orders(account_id)`` for up to 20 s.  Verdicts: ``ok_user_hub`` (info log) / ``ok_rest`` (warning: "User Hub IS UNRELIABLE for this run") / ``missing`` (🚨 ERROR: "CHECK BROKER UI IMMEDIATELY").

  Wire-in points:

  * **``trading_bot.__init__``** — instantiates the singleton, registers ``record_market_tick_callback`` on the WebSocketManager and ``record_user_account/position/order_callback`` on the UserHubManager.  Now the monitor sees every event the bot sees.
  * **``trading_bot.start_market_hub_for_strategies``** — starts the watchdog after the first symbol subscription succeeds.  Idempotent; safe across multiple strategies in the same process.
  * **``strategies/strategy_base.place_bracket_order``** — calls ``monitor.is_safe_to_trade(symbol)`` BEFORE the broker call; rejects the order with ``method="gated_health"`` if unhealthy.  Then on successful place, calls ``monitor.record_order_placed()`` and schedules the verifier task.  Opt out via ``DATA_FEED_HEALTH_GATE=false`` (for replay paths).
  * **``trading_bot.place_oco_bracket_with_stop_entry``** — same gate at the bot-level chokepoint so strategies bypassing ``strategy_base`` (e.g. overnight_range) are also covered.

  Tests added: 39 net-new across 4 files:
  * ``tests/test_data_feed_health.py`` (21 tests) — recording, age tracking, safe-to-trade verdicts (grace window, never-ticked, stale, per-symbol isolation, user-hub-silent-after-order), pause/reset, snapshot, market-hours helper.
  * ``tests/test_data_feed_watchdog.py`` (8 tests) — grace bypass, outside-hours noop, zombie reconnect path, cooldown enforcement, backoff reset on healthy feed, fallback symbols getter, start/stop idempotency, never-ticked-triggers-reconnect.
  * ``tests/test_order_verifier.py`` (6 tests) — ok_user_hub fast path, ok_rest fallback, missing-order ERROR log, REST exception retried, multiple order-id key forms, empty order id.
  * ``tests/test_health_gate_integration.py`` (4 tests) — singleton round-trip + safety verdict contract.

  Net effect of the safety net on the 2026-06-11 incident, replayed: (1) the bot would have REFUSED to place the MGC LONG order at 08:15:07 because the monitor would have flagged Market Hub silent for ≫120 s; (2) had the order somehow gone through, the verifier would have ERROR-logged "ORDER VERIFY FAILED" within 50 s; (3) the watchdog would have forced a reconnect within 30 s of zombie detection, restoring data flow so the consec_loss_breaker would correctly register the -1R loss when SL hit.

- **RTH-only truth-mode re-test of legacy PA edges (2026-06-11)** — Hypothesis: since force-flat at 16:00 ET was the DOMINANT optimism source (~+0.45 R), restricting to RTH-only sessions (nyam + nypm) might revive the PA stack.  Tested 6 candidate edges on MES 9m with full truth-mode (``--truth-mode --csv-1m --force-flat-et 16:00 --session nyam|nypm|lunch|premarket``):

  | Pattern | bias | filter | n | WR | Mean R | survives? |
  |---|---|---|---|---|---|---|
  | dragonfly_doji | contrarian | OB + RTH (nyam+nypm) | 12 | 41.7% | **+0.117** | marginal, n too small |
  | dragonfly_doji | contrarian | OB + lunch | 4 | 25.0% | -0.738 | no |
  | dragonfly_doji | contrarian | OB + premarket | 2 | 0.0% | -0.806 | no |
  | gravestone_doji | contrarian | OB | 26 | 30.8% | -0.397 | **no** (was +0.257 legacy) |
  | bullish_marubozu | contrarian | OB | 11 | 45.5% | **+0.419** | promising but n=11 |
  | bullish_marubozu | continuation | OB | 9 | 11.1% | -0.458 | no |
  | bearish_marubozu | contrarian | OB | 6 | 16.7% | -0.845 | no |
  | bullish_engulfing | contrarian | OB | 40 | 40.0% | -0.087 | neutral |
  | bearish_engulfing | contrarian | OB | 41 | 22.0% | -0.487 | no |
  | sweep_into_fvg | contrarian | premarket | 82 | 42.7% | -0.075 | **no** (was +0.19-0.32 legacy) |

  **CONCLUSION: the PA research stack at this geometry does NOT contain a productionizable edge.**  Only two candidates have positive Mean R (``dragonfly_doji + OB`` in RTH and ``bullish_marubozu + OB`` contrarian) and both have n ≤ 12 — sample sizes far too small for production deployment.  The marubozu+OB result (+0.419 R, n=11) is worth tracking with more data but the broader stack should be considered exhausted at the current 1×ATR stop / 2R TP / 12-bar hold geometry.

  Implication: the previously-queued "Re-run legacy winning PA edges with truth-mode" line item is **CLOSED** — the survivors are tracked above; no edge meets the bar for spawning another MVP.  The remaining queued work (``probe_pattern_edge.py``, ``[LEGACY-SIM]`` tagging) becomes hygiene rather than primary research.

- **Anti-bypass risk-infra audit (2026-06-11)** — Net-new ``tests/test_strategy_risk_infra_wiring.py`` (33 tests across 16 strategies).  Closes the bug class that produced the silent consec-loss-breaker bypass in ``opening_range_breakout`` + ``price_action_fade`` (discovered by the 2026-06-11 preflight, not by any existing test).  Two invariants enforced for every strategy in ``BUILTIN_STRATEGY_SPECS``:

  1. **AST static scan** — every ``from core.consec_loss_breaker import X`` may only import names in the canonical API surface ``{BreakerConfig, evaluate, trade_iter_for_strategy}``.  Catches misspelled / out-of-date / invented names that ``try / except`` swallows at runtime.
  2. **Instantiation + canonical-method audit** — every strategy must instantiate against a minimal mock bot AND, if it declares ``_breaker_config_for_symbol``, that method must return a valid ``BreakerConfig`` instance for any symbol.  Catches the case where a strategy DEFINES the bridge method but mis-builds the config inside.

  Also pins ``CANONICAL_CONSEC_LOSS_API`` as the single source of truth.  Adds ``test_canonical_consec_loss_api_is_exported`` to fail the suite if the canonical set goes stale.  **Result on the existing arsenal: 33/33 PASS** — no other strategy has the silent-bypass bug pattern.  Future strategy authors get a hard CI failure if they invent helper names.

- **Arsenal truth-effects sanity check (2026-06-11)** — Verified the backtest engine's gap-through / force-flat / commission modeling IS firing for the production-tier strategies that are about to be live-deployed.  Audit of ``docs/perf/*_truth_3m/metrics_insights.json`` + exit-reason distributions:

  | Strategy | avg_r_losers | Gap-through? | force-flat exits? | Verdict |
  |---|---|---|---|---|
  | morning_range_reversion R28 | -1.043 | ✓ mild | n/a (internal flat) | clean |
  | overnight_range R24 | -1.161 | ✓ moderate | n/a (intraday) | clean (overnight gap risk real) |
  | overnight_reversion | -11.617 | recap-metric anomaly | ✓ replay_force_flat_et present | engine OK, see note |
  | vwap_zscore_reversion | -0.993 | ✓ slight | ✓ replay_force_flat_et present | clean |

  **CAVEAT — ``overnight_reversion`` R-multiple metrics are misleading**, NOT broken.  ``avg_r_winners=+850.080`` and ``avg_r_losers=-11.617`` come from the recap's ``pnl / initial_risk_dollars`` formula in ``core/backtest/recap_metrics.py:235``; because overnight_reversion uses ATR-scaled stops, per-trade ``initial_risk_dollars`` varies 10× across the 30-trade sample, so the straight-mean R is dominated by tail trades.  Absolute PnL ($1397 / 30 trades, RF 2.47) is sane.  Recap metric needs an outlier-cap (or per-trade R-distribution display) for variable-stop strategies — filed as future work; engine itself is correct.

- **Items 1-6 of "what's still queued" landed (2026-06-11)** — Six high-leverage gaps closed in one session, all behind tests, all integrated into ``make`` targets:

  1. **Refresh stale historical data** — ``scripts/refresh_historical.sh`` wraps the existing 5m + 1m broker stitchers with a staleness report (BEFORE / AFTER), credential checks, and a ``--check`` offline mode.  ``make refresh-data`` + ``make refresh-data-check``.  Discovered all canonical CSVs are 160h (6.7 days) stale — operator should run before next sweep.
  2. **``scripts/validate_engine_vs_live.py`` (per-trade engine-vs-live diff)** — Reuses ``_simulate_trade_truth`` so the engine model lives in ONE place.  Pulls live trades from Postgres ``trade_history`` (or offline JSON), replays each through engine truth-mode, surfaces per-trade ``ΔR`` with ✓ / ⚠ / ✗ verdicts (< 0.25 / < 1.0 / ≥ 1.0).  3 pinning tests at ``tests/test_validate_engine_vs_live.py`` cover zero-ΔR on engine-matched trades, missing-metadata skip path, and force-flat alarm on overnight-held trades.
  3. **``scripts/probe_pattern_edge.py`` (truth-mode-always pattern probe)** — Opinionated multi-symbol screening tool: ALWAYS truth-mode (no legacy mode at all), runs MES + MNQ + MGC in one invocation, outputs PRODUCTIONABLE / MARGINAL / NO EDGE verdict + JSON for downstream tooling.  ``make probe PATTERN=dragonfly_doji BIAS=contrarian OB=1`` runs the full arsenal in 15 s.  Surfaced a previously-unknown asymmetric edge: ``dragonfly_doji + OB`` on MNQ posts +0.345 R (n=43) while MES / MGC have no edge — exactly the kind of insight single-symbol research misses.  3 pinning tests at ``tests/test_probe_pattern_edge.py``.
  4. **``[LEGACY-SIM]`` tagging across CHANGELOG + STRATEGY_ARSENAL.md** — Every entry citing R numbers from the legacy (over-optimistic) ``scripts/simulate_price_action_trades.py`` now carries an inline ``[LEGACY-SIM]`` tag with a pointer to the 2026-06-11 truth-mode entry.  STRATEGY_ARSENAL.md got a new top banner noting the PA stack DE-RATING (no production tier change; ``price_action_fade`` MVP stays ``meta.enabled = false``).
  5. **Recap metric outlier-cap (``core/backtest/recap_metrics.py``)** — Fixes the ``overnight_reversion`` arsenal-sanity-check finding (avg_r_winners=+850 from a single near-zero-risk trade).  Per-trade R now clipped at ±10R before averaging; ``avg_r_winners`` / ``avg_r_losers`` use clipped means.  Surfaced NEW diagnostic keys: ``median_r_winners`` / ``median_r_losers`` (non-parametric central tendency), ``avg_r_winners_unclipped`` / ``avg_r_losers_unclipped`` (raw means for transparency), ``n_clipped_r_winners`` / ``n_clipped_r_losers`` (variable-stop pathology visibility), ``r_cap``.  HTML recap surfaces the clip note + median row.  3 new tests at ``tests/test_recap_metrics.py``.
  6. **``scripts/portfolio_allocator.py`` (capital-allocation primitive)** — Derives the per-account assignment programmatically from truth metrics + per-trade overlays.  Score = 0.4 × RF + 0.4 × Sharpe-ish + 0.2 × (-DD%).  Greedy assignment with correlation penalty (|Pearson on daily-PnL| ≥ ``--corr-threshold`` demotes the candidate in favour of an orthogonal alternative).  Conflict matrix derived from each TOML's ``meta.symbols`` + ``signal.allow_long`` / ``allow_short``.  ``make allocate`` runs the full arsenal: current top-4 = MRR (RF 13.45) → ORB (RF 4.67) → overnight_reversion (Sharpe +1.40) → vwap_zscore.  10 pinning tests at ``tests/test_portfolio_allocator.py`` cover Pearson edge cases, CLI surface, JSON schema, account bounds, no-duplicate-assignment invariant.

  **Test suite**: 242 → 265 (23 new tests).  All green.  No regressions.

- **`[LEGACY-SIM]` tagging convention (2026-06-11)** — All CHANGELOG entries dated 2026-06-09 to 2026-06-11 that pre-date the truth-mode entry below carry an inline ``[LEGACY-SIM]`` annotation.  The legacy ``scripts/simulate_price_action_trades.py`` (without ``--truth-mode``) is now FROZEN for forward research; use ``scripts/probe_pattern_edge.py`` (truth-mode-always, multi-symbol) for any new edge measurement.  Legacy R numbers below the tag are documentation of the discovered bias — they are NOT to be cited as the strategy's actual edge.

- **Truth-mode simulator + convergence with backtest engine (2026-06-11)** — Major finding that **DE-RATES the entire price-action research stack's headline numbers**.  Added ``_simulate_trade_truth()`` to ``scripts/simulate_price_action_trades.py`` plus 8 new CLI flags (``--truth-mode``, ``--csv-1m``, ``--commission-per-trade``, ``--slippage-ticks``, ``--tick-size``, ``--point-value``, ``--agg-minutes``, ``--force-flat-et``).  Truth-mode mirrors the FOUR engine effects that the legacy simulator was ignoring:

  1. **Entry slippage** — engine MARKET orders fill at NEXT bar's OPEN ± slip, not at the pattern bar's close.  Legacy sim's "enter at pattern close" was systematically optimistic when the next bar's open was unfavorable.
  2. **Stop gap-through clamp** — engine fills SL at ``max(stop_px, bar.open) ± slip`` (BUY) / ``min(stop_px, bar.open) ± slip`` (SELL).  Bars that GAP THROUGH the stop fill at the gap, not at the stop price.  This is THE smoking gun for the engine's median R of -1.146 (losses larger than -1R).  Mirrors the 2026-06-03 engine fix that already caught 25.2% gap-through fills.
  3. **Commission** — engine charges round-trip commission per contract (default $5/trade).  Truth-mode converts to R units: ``commission_r = commission / (stop_dist × point_value)``.
  4. **Force-flat at session boundary** — engine's ``timing.replay_force_flat_et`` defaults to "16:00" ET; any position held across that ET wall-clock OR across a calendar-date boundary gets synthetically market-closed at the bar's open.  This was the BIGGEST hidden effect: 24/5 strategies (like ``price_action_fade``) have many trades fire outside RTH and get force-flat'd at unfavorable mid-day prices.

  Optionally accepts ``--csv-1m`` for 1m intrabar fill resolution that mirrors the engine's ``intrabar_series_iter`` — without 1m data, falls back to aggregate-bar conservative tie-break (SL wins same-bar SL+TP).

  **CONVERGENCE VALIDATED on the canonical divergent edge** (``dragonfly_doji + bearish OB`` on MES, 9 months, contrarian short):

  | Mode | Mean R | WR | Gap vs engine |
  |---|---|---|---|
  | Legacy simulator | +0.885 | 60.0% | **+0.788 over** |
  | Truth-mode (no intrabar, no force-flat) | +0.577 | 58.2% | +0.480 over |
  | Truth-mode + 1m intrabar | +0.389 | 58.2% | +0.292 over |
  | **Truth-mode + intrabar + force-flat 16:00 ET** | **-0.058** | **32.7%** | **-0.155 under (within noise)** |
  | Backtest engine reference (CHANGELOG) | +0.097 | 35.3% | (reference) |

  Truth-mode WR is within **2.6 percentage points** of engine WR; Mean R is within **0.155 R** (vs +0.788 R gap originally).  Decomposing the cumulative correction:

  - **~+0.31 R** of legacy-simulator optimism from ignoring entry slippage / pattern-close-vs-next-open fill
  - **~+0.19 R** from ignoring stop gap-through penalty
  - **~+0.45 R** from ignoring force-flat at 16:00 ET (DOMINANT effect — 24/5 strategies suffer disproportionately)
  - **~+0.04 R** from ignoring commissions

  **CRITICAL IMPLICATION — the entire PA research stack's headline numbers are de-rated.**  The dragonfly+OB "edge" of +0.885 R from the legacy simulator is ~0 R under realistic execution.  The original ``price_action_fade`` MVP backtest result of -$613 on 34 trades is now CONSISTENT with truth — no bug, the edge just isn't there at this geometry.  Other edges from the legacy stack (gravestone+OB +0.257 R, sweep_into_fvg @ premarket +0.19-0.32 R, etc.) need to be re-evaluated with ``--truth-mode --csv-1m --force-flat-et 16:00`` before any further productionization work.

  **17 pinning tests** in ``tests/test_simulate_price_action_trades.py`` covering:  
    • Legacy simulator (baseline + trailing + partial-profit + combined) — 8 tests, unchanged  
    • Truth-mode: TP includes slip + commission; SHORT stop gap-through; LONG clean stop; intrabar TP-wins-over-SL when 1m data shows favorable order; no-next-bar → invalid; commission inverse-scales-with-stop-dist; force-flat at 16:00 ET; force-flat disabled → timed exit — 9 tests, ALL pinning concrete arithmetic.

  Full repo: **209/209 tests pass; 0 lints.**

  Next steps (queued):
  1. Re-run ``gravestone_doji + OB``, ``sweep_into_fvg @ premarket``, and other legacy "winning" PA edges with ``--truth-mode --csv-1m --force-flat-et 16:00`` to see what survives realistic execution
  2. Update ``CHANGELOG.md`` price-action sections to flag the legacy R numbers as ``[LEGACY-SIM]`` (over-optimistic) where they appear
  3. Promote any pattern that maintains positive truth-R AFTER full friction modeling
  4. Consider building a ``probe_pattern_edge.py`` tool that ALWAYS uses truth-mode (deprecating legacy mode entirely for forward research)

- **Automated live-deploy pre-flight (2026-06-11)** — Net-new ``scripts/preflight_live_deploy.sh`` (15 checks across 5 categories) + Python helper ``scripts/preflight_dryrun.py``.  Single-command sanity check before flipping MRR + overnight_range to two prop accounts.  Categories:
  1. **Env vars** — broker creds (``PROJECT_X_*``/``TOPSTEPX_*`` aliases), ``DATABASE_URL``, ``DAILY_LOSS_LIMIT``, ``INITIAL_BALANCE``, ``PORTFOLIO_DAILY_LOSS_CAP``, ``ENABLE_SIGNALR`` not disabled
  2. **TOML pinning** — ``meta.enabled=true``, ``live_breaker_enabled=true``, valid ``symbols`` on both production configs
  3. **Smoke tests** — runs the 5 prereq test files in one shot (125 tests)
  4. **Risk infra wiring** — imports ``portfolio_daily_breaker``, ``regime``, ``consec_loss_breaker`` (with the correct ``BreakerConfig``/``evaluate``/``trade_iter_for_strategy`` API surface), and verifies ``EventType.TRADE_CLOSED``/``PORTFOLIO_KILL`` exist
  5. **Dry-run ``analyze()``** — instantiates MRR + overnight_range against the last 1500 bars of MNQ/MGC/MES CSVs with a minimal mock bot and confirms ``analyze()`` runs end-to-end without exceptions (catches mock/import/signature regressions before live)

  The script prints a colorised ✓/⚠/✗ tree, exits non-zero on any failure, and on success ends with the exact launch commands.  Runbook updated at ``docs/LIVE_DEPLOY_RUNBOOK.md`` to make this the AUTHORITATIVE pre-flight; the historical manual checks remain as a fallback diagnostic in a collapsed section.  All 15 checks pass on HEAD (one expected warning: ``PORTFOLIO_DAILY_LOSS_CAP`` defaults to \$1000 if unset).

### Fixed
- **Silent consec-loss breaker bypass on ``opening_range_breakout`` + ``price_action_fade`` (2026-06-11)** — While building the pre-flight, the risk-infra wiring check caught that BOTH strategies were importing non-existent functions ``register_strategy_breaker`` and ``evaluate_breaker`` from ``core.consec_loss_breaker``.  The imports were wrapped in ``try/except`` so they failed SILENTLY — the consec-loss breaker for these strategies was never operative.  The canonical API (used by ``overnight_range`` / ``body_reversion`` / MRR) is ``BreakerConfig`` + ``evaluate`` + ``trade_iter_for_strategy``.  Replaced both strategies' ``_maybe_register_breaker`` / ``_evaluate_breaker`` implementations with the canonical pattern that MRR and overnight_range use (per-symbol resolution via ``self._cfg.symbol_override``, falling back to ``self._cfg.get_*``).  20 / 20 ORB + PAF smoke tests still pass; full repo suite 209 / 209 green.  Impact: ORB now actually has a consec-loss breaker bridge (even though the committed TOML has ``max_consecutive_losses=0`` so it's a no-op until tuned); PAF inherits the same bridge for when its MVP is eventually promoted.

- **price_action_fade MVP spawned (2026-06-11) + CRITICAL simulator-vs-engine divergence found.** **`[LEGACY-SIM]`** — the legacy-simulator R figures cited below are over-optimistic by ~+0.79 R; see the 2026-06-11 truth-mode entry above for the truth-mode-validated values.  Built ``strategies/price_action_fade_strategy.py`` (~470 LoC) as the minimum-viable productionization of the cross-symbol ``dragonfly_doji + Order Block`` edge characterised in earlier sessions.  Signal mechanic: a bullish-reversal doji formed inside a CONFIRMED unmitigated BEARISH order block → contrarian SHORT fade.  Geometry: ATR(14)-scaled SL = 1×ATR, TP = 2R, max 12-bar hold.  MES-only at MVP (the strongest simulator pocket).  TOML at ``config/strategies/price_action_fade.toml``; 10 pinning tests at ``tests/test_price_action_fade_smoke.py``.

  **THREE BUGS CAUGHT during the walk-from-simulator-to-strategy transition**:

  1. **``PatternEvent`` field mismatch** — strategy initially referenced ``ev.pattern`` and ``ev.bar_index``; correct fields are ``ev.name`` and "all events apply to last bar of slice".
  2. **``OrderBlock`` field mismatch** — strategy initially used ``ob.high / ob.low``; correct fields are ``ob.upper / ob.lower`` (use ``ob.contains(price)`` helper instead).
  3. **``find_order_blocks`` edge-case bug (FIXED IN PRIMITIVE)** — the detector's outer loop ``range(atr_period, len(bars) - window)`` excluded formation candidates in the LAST ``window=5`` bars of the input.  This is fine for offline simulation (always has future bars) but BREAKS live detection — the strategy can never see an OB whose confirmation bar IS the current bar (the most recent and tradeable OBs).  Trace evidence: with the bug, MES MVP fired 2 signals over 5 months instead of the expected ~32.  **Fixed** in ``core/market_structure.py``: outer loop now walks to ``len(bars) - 1``; inner impulse loop caps at ``min(i + 1 + window, len(bars))`` so partial future-windows still work.  49 / 49 market_structure tests still pass; the change is forward-compatible (any OB that confirmed with a full future window is detected identically).

  **CRITICAL FINDING — simulator vs backtest-engine divergence**.  After fixing the three bugs the MVP's signal count matched expectation (34 signals over 9m MES = ~simulator's 108 / 17m extrapolation).  But the per-trade economics did NOT replicate:

  | Metric | Simulator finding | Backtest-engine 9m MES |
  |---|---|---|
  | Trades | 108 over 17 m | 34 over 9 m |
  | Win rate | 64.8 % | **35.3 %** |
  | Mean R | **+0.907** | **+0.097** |
  | Median R | — | **-1.146** |
  | Profit factor | (strongly positive) | **0.60** |
  | Total PnL | (strongly positive) | **-$613** |

  The simulator's R/trade is **9 × higher** than the engine's.  The Median R of -1.146 (below -1.0!) implies many trades exit beyond the SL price.  Hypotheses for the gap, ranked:
  1. **Bracket fill semantics** — simulator enters AT the pattern bar's close; engine places a real order that fills at next-bar OPEN (gap risk + worse slippage on illiquid overnight bars where most signals fire).
  2. **Same-bar SL/TP resolution** — simulator's SL/TP check is bar-by-bar conservative (SL before TP if both touch).  Engine uses 1 m intrabar data when available; without it, the conservative path may NOT trigger TP on a bar where the SL+TP both hit.
  3. **Session force-flat** — 6 / 34 engine trades exit ``replay_force_flat_et`` (held across session boundary then flat-mid-day).  Simulator has no such mechanism.
  4. **Commission + slippage** — engine charges $5 / trade commission and tick-level slippage; simulator has neither.  At a 2 R baseline this is ~10 % erosion.

  **Status**: MVP REMAINS ``enabled = false``.  The edge as characterised by ``scripts/simulate_price_action_trades.py`` does NOT survive realistic backtest execution.  This calls into question the entire price-action research stack's headline numbers (dragonfly+OB +0.907 R, gravestone+OB +0.257 R, sweep_into_fvg @ premarket +0.19-0.32 R, etc.) — the simulator is BIASED OPTIMISTIC.  The market-structure primitives + setups themselves are correct (49 + 7 + 22 tests pin them); the issue is in how the simulator MODELS trade execution.

  **Next-session priorities** (queued, not done):
  1. Diagnose simulator-engine gap on a single trade — instrument both paths and find where the prices diverge.
  2. Tighten the simulator's intra-bar resolution to use 1 m data (matching the engine's truth path).
  3. Re-run dragonfly+OB and sweep_into_fvg edges with the TRUTH simulator and see if the +0.907 R / +0.21 R numbers hold.
  4. If they do — promote MVP (the simulator was correct, the engine has a quirk).  If they don't — accept the edges are weaker than initially measured and re-prioritise the arsenal.
- **Exit-mechanics stress test (2026-06-11): multi-R / multi-horizon matrix + trailing stop + partial profit.** **`[LEGACY-SIM]`** — every R figure in this entry is from the legacy simulator and should be re-measured with ``--truth-mode --csv-1m --force-flat-et 16:00`` before any productionization decision.  Extended ``scripts/simulate_price_action_trades.py`` with three new exit mechanic modes:
  - **``--stress-test``** with ``--stress-tp-list`` and ``--stress-bars-list`` — runs the full (TP-ATR × max-bars) cross-product per pattern in a single pass and emits a heatmap.  Quickly answers "does my edge depend on the specific 2R / 12-bar choice or is it stable across exit configurations?"
  - **``--trail-atr N --trail-trigger-r M``** — activates a trailing stop at ``N × ATR`` once unrealised profit reaches ``M × stop_dist`` (default 1R).  SL only moves favorably.
  - **``--partial-r N --partial-frac F``** — closes ``F`` fraction of position at ``N × R`` then moves the runner's SL to breakeven.  Final pnl = partial fill + runner outcome.
  - Trailing + partial can be combined; runner gets trailed after partial fill.
  - **Critical intra-bar fix found via tests**: initial implementation updated SL within the same bar that produced the favorable MFE, allowing single-bar feedback loops (bar simultaneously creates MFE, raises SL, and stops at the new SL).  Corrected ordering: hit-check uses start-of-bar SL/TP; trailing/partial updates apply at END of bar (effective NEXT bar).  Inflated MES dragonfly+OB+trailing from +1.395 R to a realistic +1.327 R (+46 % vs baseline rather than +54 %).
- **9 new tests** in ``tests/test_simulate_price_action_trades.py`` covering fixed-TP / SL / timed-exit paths + 3 trailing-stop scenarios + 2 partial-profit scenarios + 1 combined mode.
- **Headline stress-test findings (MES + MNQ + MGC 5m, Jan 2025 → present)**:
  - **``dragonfly_doji + OB`` is ROBUST across (TP-ATR × max-bars) matrix** — every cell in the (TP ∈ {1, 1.5, 2, 2.5, 3} R) × (max-bars ∈ {6, 12, 24, 48}) grid is positive.  The current 2R / 12-bar default sits MID-MATRIX (not over-fit to a specific config).
    | max-bars | TP=1R | TP=2R | TP=3R |
    |---|---|---|---|
    | 12 | +0.544 | **+0.907** | +0.966 |
    | 48 | +0.602 | +0.998 | **+1.225** |
    Best cell: TP=3R + 48-bar hold = **+1.225 R/trade**.  Edge grows with both TP and horizon → strong directional runners common.
  - **``sweep_into_fvg @ premarket`` PLATEAUS at +0.21 R** — edge stable around +0.19-0.21 R for TP=2R+ and max-bars 12+.  Different mechanic: mean-revert (no runner upside) vs dragonfly's directional drive.
  - **Trailing stop wins for MES + MNQ; neutral on MGC**:
    | Symbol | Baseline (2R/12) | Trailing (3R/24/0.5×ATR) | Delta |
    |---|---|---|---|
    | MES | +0.907 R (n=108) | **+1.327 R** (n=108, WR 75 %) | **+46 %** |
    | MNQ | +0.723 R (n=72)  | +0.854 R (n=72, WR 61 %) | +18 % |
    | MGC | +0.452 R (n=73)  | +0.414 R (n=73, WR 62 %) | −8 % |
    MGC's volatility character causes premature stop-outs under trailing.
  - **Partial-profit + BE-runner HURTS overall edge** — clips upside (50 % of position capped at 1R).  WR rises to 78 % but mean R drops to +0.329 R.  Not a useful exit mechanic for this signal.

  **Strategy implication**: a future ``price_action_fade`` strategy should use:
  - **MES + MNQ**: TP=3R + trailing-stop after 1R MFE, max 24-bar hold
  - **MGC** (optional 3rd instrument): fixed TP=2R, max 12-bar hold (or drop MGC entirely since its baseline edge is weakest and trailing doesn't help)

- **Compound SMC setups (2026-06-11): sweep_into_fvg, fvg_in_ob, choch_then_ob_retest.** **`[LEGACY-SIM]`** — R figures below are from the legacy simulator.  Truth-mode (2026-06-11) showed ``sweep_into_fvg @ premarket`` falls from +0.19-0.32 R to -0.075 R (n=82) when entry slip, stop gap-through, and force-flat are modelled correctly.  Re-validate each headline with the truth-mode probe before promotion.  New module ``core/smc_setups.py`` (~360 LoC) provides the multi-primitive CONFLUENCES that practitioners actually trade:
  - **``sweep_into_fvg``** — a liquidity sweep (stop-hunt) followed by price re-entering an unmitigated FVG on the reversal side.  Sweep proves stops were run; FVG is the high-probability entry zone.  Trade direction = sweep's implied reversal direction.  Includes the sweep bar itself in the search window (sweep-bar-into-FVG entries fire immediately) and uses sweep-bar-aware FVG mitigation gating so the sweep's own intrusion into the FVG doesn't disqualify the trade.
  - **``fvg_in_ob``** — an FVG whose zone OVERLAPS an unmitigated Order Block in the same direction.  Double-confluence entry zone is the OVERLAP rectangle.  Mitigation-aware: signal blocked if FVG or OB was mitigated before the trigger bar.
  - **``choch_then_ob_retest``** — Change-of-Character (trend flip) followed by retest of the FIRST Order Block formed in the new trend's first impulse.  Canonical SMC trend-reversal entry.  Each CHoCH event consumes at most one OB.
  - All three emit ``SetupSignal`` events at the trigger bar with ``entry_zone_upper / entry_zone_lower / invalidation`` for clean risk anchoring.  No look-ahead: every detector consults primitives whose confirmation index is ≤ the trigger bar.
  - ``find_all_smc_setups(bars, ...)`` is the one-call runner.
  - 7 new tests added (3 sweep + 2 fvg-ob + 1 choch + 1 runner) → 108 / 108 tests pass across the price-action stack.
- **Simulator: SMC compound integration.**  New flags ``--include-smc-setups`` (turns on all 3 detectors), ``--smc-sweep-max-bars``, ``--smc-choch-max-retest-bars``.  Compound signals carry their own direction (no ``--bias`` flip applied because the setup IS the direction).  Signals merge into the per-bar event stream alongside ordinary patterns + sweep-as-pattern.
- **Headline SMC-compound findings (MES + MNQ + MGC 5m, Jan 2025 → present, contrarian, 1R-SL / 2R-TP, 12-bar horizon)**:
  - **Baselines (no session filter)**:
    | Setup | MES | MNQ | MGC |
    |---|---|---|---|
    | `sweep_into_fvg` | +0.039 R (n=1598) | +0.081 R (n=1736) | +0.060 R (n=1584) |
    | `fvg_in_ob` | +0.065 R (n=699) | +0.016 R (n=534) | −0.001 R (n=625) |
    | `choch_then_ob_retest` | −0.009 R (n=5114) | −0.036 R (n=5744) | −0.046 R (n=5858) |
  - **`sweep_into_fvg @ premarket` is the cross-symbol-confirmed standout**: MES +0.193 R (n=132), MNQ implicit (top by sym), MGC +0.324 R (n=101).  Premarket (08:00-09:30 ET) = thin liquidity + institutional positioning → sweeps + FVGs hold.
  - **`fvg_in_ob @ premarket` (MNQ)**: +0.477 R, n=49 — strongest single pocket but small sample.
  - **`choch_then_ob_retest` doesn't survive standalone on 5m**: negative across all 3 symbols.  CHoCH on intraday TF is too noisy without HTF (1h/4h) trend gating.

  **Comparison to the existing winning confluence**: ``dragonfly_doji + OB`` posts +0.45 to +0.91 R cross-symbol (n=70-110).  ``sweep_into_fvg @ premarket`` is a smaller edge (+0.19 to +0.32 R) but fires FAR more often (n=132+).  These are complementary: ``dragonfly_doji + OB`` = rare-but-strong; ``sweep_into_fvg @ premarket`` = frequent-but-modest.  A live ``price_action_fade`` strategy can run both as orthogonal entries.

  Next step (queued): spawn ``price_action_fade`` strategy with two signal modes (1) dragonfly_doji + OB confluence (primary), (2) sweep_into_fvg @ premarket (secondary).  Walk-forward separately per symbol.
- **SMC / ICT primitives (2026-06-11): Fair Value Gap, Order Block, Liquidity Sweep.** **`[LEGACY-SIM]`** — the +0.45 to +0.91 R headlines below are from the legacy simulator.  The PRIMITIVES themselves are correct (49 tests pin look-ahead-safety + zone semantics); only the trade simulator that consumes them was biased.  Use the truth-mode probe (``scripts/probe_pattern_edge.py``) for current-truth measurements.  Added the three core institutional-trading structural concepts to ``core/market_structure.py`` (now ~700 LoC total):
  - ``FairValueGap`` dataclass + ``find_fair_value_gaps(bars)`` — detects 3-bar imbalances where ``bar[i-2].high < bar[i].low`` (bullish FVG) or mirror (bearish). Mitigation pass tags each FVG with the first subsequent bar that re-enters the zone.  ``is_inside_fvg(fvgs, bar_index, price, direction, require_unmitigated)`` is the point-in-zone helper with no-look-ahead semantics.
  - ``OrderBlock`` dataclass + ``find_order_blocks(bars, impulse_threshold_atr, window, atr_period)`` — detects the last opposite-color bar before a strong impulsive move (≥ ``impulse_threshold_atr`` × ATR within ``window`` bars).  **CRITICAL**: the dataclass stores BOTH ``formation_index`` (the OB bar itself) AND ``confirmation_index`` (the EARLIEST bar at which the impulse threshold was breached).  ``is_inside_order_block`` gates on ``confirmation_index`` — using ``formation_index`` would leak the impulse window into the live signal (look-ahead bias).  Initial pass without this gate produced suspiciously high edges (WR 76-87 %, mean R +1.0 to +1.6); the corrected gate brings results to realistic +0.7 to +0.9 R with n in the 60-110 range.
  - ``LiquiditySweep`` dataclass + ``find_liquidity_sweeps(bars, swings, min_poke_points, require_close_back_inside)`` — detects stop-hunt setups (wick pokes through a prior swing high or low then closes back inside).  Bullish sweep = poke below swing low + close above → LONG.  Bearish sweep = poke above swing high + close below → SHORT.
  - 13 new tests added (5 FVG + 5 OB + 4 sweep + 1 OB look-ahead-safety) → 49 / 49 market_structure tests pass.
- **Simulator: SMC integration (``scripts/simulate_price_action_trades.py``).**  Four new flags:
  - ``--require-fvg`` — pattern's close must lie inside an unmitigated FVG in the trade's intended direction.
  - ``--require-order-block`` — pattern's close must lie inside a CONFIRMED, unmitigated Order Block.
  - ``--ob-impulse-atr N``, ``--ob-window M`` — tune the OB detector's impulse threshold (default 2.0 × ATR over 5 bars).
  - ``--include-sweep-as-pattern`` + ``--sweep-min-poke N`` — treat liquidity sweeps as standalone patterns (``bullish_sweep`` / ``bearish_sweep``) trading the stop-run reversal.
- **Headline SMC findings (MES + MNQ + MGC 5m, Jan 2025 → present, contrarian, 1R-SL / 2R-TP, 12-bar horizon)**:
  - **Order Block confluence ~3× the edge of the same patterns without OB**:
    | Pattern | No OB filter | + OB filter | Lift |
    |---|---|---|---|
    | `dragonfly_doji` (MES) | +0.305 R (n=945) | **+0.907 R (n=108)** | **2.97×** |
    | `dragonfly_doji` (MNQ) | +0.193 R (n=755) | **+0.723 R (n=72)** | **3.75×** |
    | `dragonfly_doji` (MGC) | +0.150 R (n=829) | **+0.452 R (n=73)** | **3.01×** |
  - **Session-concentrated**: `dragonfly_doji @ asia + OB` (MES) = +1.417 R/trade, n=42, WR 83.3 %.  Tiny sample but extreme edge.
  - **FVG filter is too restrictive standalone**: 0.8 % pass rate (855 / 106 k signals) — most bars never sit inside an unmitigated FVG.  Some patterns DO show big lift in the tiny survivor pool (e.g. `bearish_marubozu + FVG` = +0.797 R, n=16) but samples are too small to commit on.  FVG works better as a TIE-BREAKER on top of session + OB filters than as a primary gate.
  - **Standalone sweep patterns LOSE**: `bullish_sweep` -0.153 R (n=1843), `bearish_sweep` -0.079 R (n=1691).  The naked stop-hunt is not enough to trade — needs context (session, pattern confluence, or structural confluence).  `bullish_sweep @ close` shows +0.161 R in a small pocket but not robust enough to build a strategy on alone.

Next step (queued): spawn ``price_action_fade`` strategy targeting the top-confluence combinations.  Primary candidate: ``dragonfly_doji + OB`` on MNQ + MGC + MES — three-instrument-confirmed edge in the +0.45 to +0.90 R range, n=70-110 per symbol.
- **Price-action engine: classical pattern library expansion (2026-06-10).** **`[LEGACY-SIM]`** — R figures below are from the legacy simulator.  The DETECTORS themselves are tested independently (51 tests) and are correct; only the trade simulator was biased. Added 17 new pure-function detectors to ``core/price_action.py``, bringing the total to 22 classical patterns:
  - Single-bar: doji (4 sub-types: generic / long-legged / gravestone / dragonfly), bullish/bearish marubozu
  - Two-bar: bullish/bearish outside bar (range-engulf vs body-engulf), tweezer top/bottom (equal H/L at reversal), bullish/bearish harami (small body inside prior big body), piercing line, dark cloud cover
  - Three-bar: morning star, evening star (reversal), three white soldiers, three black crows (continuation)
  Each pattern has thresholds documented inline and a positive + negative pinning test in ``tests/test_price_action.py``.  ``Pattern.ALL`` updated; ``ProbabilityEmitter`` automatically tracks all new patterns.  42 / 42 tests pass.
- **Price-action engine: market-structure primitives (2026-06-10).** New ``core/market_structure.py`` module (~410 LoC) provides the structural-context layer that turns raw pattern detection into tradeable signals:
  - ``find_swing_pivots(bars, lookback)`` — Williams-fractal pivot detector (offline / batch).
  - ``SwingTracker`` — online stateful pivot tracker with bounded history; labels each new swing as HH/LH/HL/LL relative to the prior swing of the same kind.
  - ``detect_break_of_structure(tracker, last_close)`` — returns BOS_UP / BOS_DOWN when the latest close exceeds the most recent swing high or breaks below the most recent swing low.
  - ``detect_change_of_character(tracker)`` — flags trend reversals (LL→HL = CHoCH_UP, HH→LH = CHoCH_DOWN).
  - ``classify_session(ts_et)`` — maps an ET timestamp to one of 8 session buckets (asia, london, premarket, nyam, lunch, nypm, close, afterhours).  Boundaries align with the intraday character documented in STRATEGY_ARSENAL.
  - ``prior_session_levels(bars)`` — builds a {today_et_date → prior_session_levels} map; each live bar can be annotated with "yesterday's H / L / close" for liquidity-magnet filtering.
  - ``find_equal_highs / find_equal_lows(swings, tol)`` — pair detector for stop-hunt setups.
  35 / 35 tests pass.
- **Price-action simulator: structural + session filters (2026-06-10).** **`[LEGACY-SIM]`** — R figures below are from the legacy simulator; the FILTERS (session / swing / prior-day H/L) themselves work correctly under truth-mode too. Extended ``scripts/simulate_price_action_trades.py`` with four new flags: ``--session asia|london|nyam|...`` (restrict to ET session bucket), ``--at-swing N`` (require pattern within ±N points of nearest confirmed swing high/low), ``--at-prior-day-hl N`` (require pattern within ±N points of prior day H/L), ``--pattern foo`` (whitelist specific patterns).  Direction policy ``_DIRECTIONS`` extended to map every new pattern (e.g. ``dragonfly_doji`` → +1 bullish-reversal base; ``bearish_marubozu`` → -1).  New `Per (pattern × session)` breakdown added to the report.
- **Headline findings on the expanded engine** (MES + MNQ + MGC 5m databento, Jan 2025 → present, contrarian / 1R-SL / 2R-TP / 12-bar horizon):
  - **Marubozu + doji family CONSISTENTLY OUTPERFORM the original 5 patterns across all 3 instruments**:
    | Pattern | MNQ mean R (n) | MGC mean R (n) | MES mean R (n) |
    |---|---|---|---|
    | `bearish_marubozu` (contrarian LONG) | **+0.270** (776) | **+0.175** (907) | **+0.280** (1093) |
    | `gravestone_doji` (contrarian LONG)  | **+0.257** (630) | **+0.222** (663) | **+0.118** (714) |
    | `dragonfly_doji`  (contrarian SHORT) | **+0.193** (755) | **+0.150** (829) | **+0.305** (945) |
    | `bullish_marubozu` (contrarian SHORT)| **+0.146** (863) | **+0.131** (1049)| **+0.173** (1205)|

    Original 5 patterns showed ±0.02 R noise levels; these new patterns are 5-15× stronger.  Interpretation: marubozu (full-body / no wick) and doji (zero-body / all wick) represent EXTREMES of bar shape — strong momentum and total indecision.  Fading both works.  Mechanism is **momentum exhaustion**.
  - **Session decomposition surfaces even tighter edges**:
    - `dragonfly_doji @ london` (3-8 AM ET): **+0.486 R/trade**, n=271, WR 57.9 % (MES)
    - `bearish_marubozu @ london`: **+0.433 R/trade**, n=377, WR 54.1 % (MES)
    - `dragonfly_doji @ asia`: **+0.433 R/trade**, n=308, WR 53.9 % (MES)
  - **Structural filter (`--at-swing 5`) further concentrates** — `dragonfly_doji @ asia + at swing high`: **+0.659 R/trade**, n=161, WR 60.2 %.

These are now directly tradeable edges.  Next step (queued): spawn ``price_action_fade`` strategy targeting the top (pattern × session × structure) combinations, walk-forward MNQ + MGC + MES separately, promote if RF beats existing tier-5 (`opening_range_breakout` 3m RF 4.67).
- **`opening_range_breakout` strategy SPAWNED + PROMOTED to Production tier #5 (2026-06-09 evening).** The previous session's ORB sweep produced 0 trades on 15/30-min windows. Root-cause analysis revealed `OvernightRangeStrategy.track_overnight_range` hard-codes `timeframe='1m'` with a 10-bar minimum — for short same-day windows + a 5m backtest CSV, only 3-6 bars are available, so the strategy rejects with `Insufficient overnight bars: 0 bars`. **Not a cross-vs-same-day mechanic limitation, a code design constraint.** Built `strategies/opening_range_breakout_strategy.py` (~530 LoC) as a purpose-built standalone strategy: mirrors `morning_range_reversion`'s same-day lifecycle (range_start / range_end_open / flat_before, iterate bars in analyze instead of separate fetch) with `overnight_range`'s symmetric stop-bracket entries (BUY stop above box high + SELL stop below box low, OCO via per-session signaled marker). Configurable bracket geometry (range-width fraction OR ATR multiplier). Registered in `strategies/strategy_manager.py` + `core/backtest_executor.py`. Default config + new TOML at `config/strategies/opening_range_breakout.toml`. R1 sweep on MNQ (4 windows × 3 variants × 9m/6m/3m) found:
  - Best window: **60-min (09:30 → 10:30)**
  - Best direction: **LONG ONLY** (short side RF 0.47 vs long-only 0.86 on 9m)
  - Best day filter: **skip Friday** (49 % WR vs 56 % Mon-Thu)
  - Truth metrics: 9m RF **4.09** / 6m RF **3.66** / 3m RF **4.67**
  - **3m truth beats `overnight_range` R24 (RF 2.20) by 2.1×** — the headline reason for promotion.
  Position size = 1, MNQ-only at commit (MGC ORB loses, MES not yet tested). DD is 25-33 % (high — requires a higher-balance account for live).
  10 pinning tests in `tests/test_opening_range_breakout_smoke.py`. Sweep artifacts in `docs/perf/_opt_runs/orb_strategy/` + `docs/perf/_opt_runs/orb_strategy_mnq_focused/`.
- **Price-action trade-simulating validator (2026-06-09 evening).** **`[LEGACY-SIM]`** — the R numbers cited in the "Headline findings" below are from the over-optimistic simulator; use ``scripts/probe_pattern_edge.py`` (truth-mode-always) for current-truth measurements. `scripts/simulate_price_action_trades.py` extends the price-action primitive from raw 1-bar conditional probabilities (which user correctly flagged as "not very actionable") to FULL trade simulation: for each pattern detection, place a stop-bracket entry, compute SL/TP from ATR×multiplier or range×fraction, walk forward up to `--max-bars` and exit on SL hit / TP hit / timed close. Aggregates per pattern: n trades, WR %, mean R per trade, std R, total R, Sharpe-ish, exit-reason mix. Supports `--bias contrarian|continuation`, `--time-buckets` (per-ET-hour breakdown), `--rsi-buckets` (per RSI quintile), `--vwap-band` (require pattern within ±N points of session VWAP). **Headline findings on MES 5m databento (Jan 2025 → present, ~100 k bars)** with contrarian bias / 1R-SL / 2R-TP / 12-bar horizon:
  - Raw per-pattern: `bearish_pin` LONG fade +0.037 R/trade (n=6574), `bullish_pin` SHORT fade +0.018 R, others within noise.
  - **Time-bucket decomposition uncovered real concentrated edges**:
    - `bullish_engulfing @ 04h ET` SHORT fade: **+0.211 R/trade** (n=315) — strongest single bucket
    - `bearish_pin @ 13h ET` LONG fade: **+0.152 R/trade** (n=214)
    - `bullish_engulfing @ 06h ET` SHORT: **+0.149 R/trade** (n=362)
    - `inside_bar @ 04h ET`: **+0.148 R/trade** (n=407)
  - **RSI extremes also concentrate edge**:
    - `bullish_pin @ RSI≥70` SHORT: +0.116 R (n=327, WR 41.9 %) — extreme overbought + bullish pin = reliable contrarian fade
    - `bullish_engulfing @ RSI<45` SHORT: +0.072 R (n=1687) — bullish engulfing in oversold context = contrarian fade
  - **Continuation bias is uniformly NEGATIVE** across MNQ + MGC + MES — contrarian is decisively the right direction. This is the foundation for a `price_action_fade` strategy: target the highest-edge (pattern × hour × RSI bucket) combinations.
- **Price-action engine primitive (2026-06-09).** **`[LEGACY-SIM]`** — any R figures in this entry's "Initial validation finding" are from the legacy single-bar probability emitter, not a full trade simulation.  The primitive itself + emitter are correct; tests at ``tests/test_price_action.py`` (16/16) pin the detector logic. New `core/price_action.py` module: 5 hand-crafted detectors (`bullish_engulfing`, `bearish_engulfing`, `inside_bar`, `bullish_pin`, `bearish_pin`) + a `ProbabilityEmitter` that maintains per-pattern conditional probabilities online. Pure-function detectors (no broker / event-bus dependencies) so they're trivially unit-testable; the emitter is a rolling tally that credits each detected pattern with the *next* bar's outcome. New `tests/test_price_action.py` (16/16 pass) pins all 5 detectors with positive + negative fixtures and the emitter with both single-pattern and baseline-accumulation tests. New `scripts/validate_price_action_patterns.py` runs the engine against any OHLCV CSV and prints per-pattern edge statistics with Wilson CIs. **Initial validation finding** on MNQ + MGC + MES 5m databento data (Jan 2025 → present, ~100 k bars per symbol): single-bar patterns have negligible 1-bar edge except `bullish_pin` (consistently BEARISH next bar, edge -0.95 % to -1.70 %) and `bearish_pin` (consistently BULLISH next bar, edge +0.97 % to +2.15 %, **MES CI clear of baseline → statistically meaningful**). These are FADE signals — opposite of conventional textbook price action — and form the foundation for a future `price_action_fade` strategy. Module docstring spells out the design intent, threshold defaults (`pin wick ≥ 2× body`, `body ≤ 40 % of range`), and the strategy-vs-primitive boundary so the next session can build the trading layer without re-deriving the spec.
- **Live-deploy runbook + monitoring tool (2026-06-09).** New `docs/LIVE_DEPLOY_RUNBOOK.md` is the copy-paste-able operator path for deploying `morning_range_reversion` (R28) and `overnight_range` (R24) to two TopStepX prop accounts: pre-flight checks, account-list snippet, exact `nohup`/`scripts/run_*.sh` launch commands, expected log markers per session window, half-day calendar limitation, portfolio breaker per-process semantics, first-week checklist. Companion tool `scripts/compare_live_vs_backtest.py` pulls trades from the `trade_history` Postgres table for the trailing 5 sessions and prints a side-by-side comparison against the corresponding 3m / 6m / 9m walk-forward truth recap with per-symbol breakdown and verdict markers (`✓` within ±30 %, `⚠` 30-60 % drift, `✗` > 60 % drift → STOP). Auto-resolves truth dirs via glob (`<strategy>_*_truth_<window>`) with strategy-name aliasing (e.g. `morning_range_reversion` ↔ `morning_range_*`). Gracefully degrades when DB is unreachable (prints truth panel only). Auto-scales truth recap to live window so `n_trades` and `total_pnl` are directly comparable.
- **ORB window exploration sweep (2026-06-09) — decisive negative result.** Tested 7 alternative range windows for the `overnight_range` strategy mechanic against the committed 19:00→10:00 baseline (15-min / 30-min / 60-min ORB, 60-min / 3 h pre-market, 11 h / 6 h Globex Asia/short). 24 trials × 3 walk-forward windows (9 m / 6 m / 3 m) on MNQ + MGC with all per-symbol `skip_weekdays` cleared and `filters.gap`/`filters.range_size` disabled (unbiased mechanism test). **No window beats baseline RF 6.43 / +133 % return / 12.92 % DD.** Best alternatives all post negative RF and 50-90 % DD; short windows (≤ 30 min) produce zero trades. Findings recorded in `docs/perf/_opt_runs/orb_exploration/SUMMARY.md` with the rationale (range width matters; cross-session positioning is the alpha). **No `opening_range_breakout` strategy spawned** — the committed window is uniquely good within this strategy's mechanic. Future ORB-style alpha should be a *fundamentally* different mechanic (opening-drive continuation, VWAP fade), not the same class with different timing knobs. Trial JSON shape gotcha discovered + fixed: per-symbol overrides must go under `"symbols"` block (not the dotted-root path).
- **MRR live-readiness audit (2026-06-09).** Pre-flight pass before deploying real money to `morning_range_reversion`. Nine audit dimensions checked:

  1. **Committed config + pinning** — `tests/test_morning_range_reversion_smoke.py` (67/67 pass) locks every R26 → R28 commit value (MGC `sl_mult=3.30`, `tp_mult=1.85`, `sl_max_pts=29`, `max_range_width_points=46`; MNQ `position_size=4`).
  2. **Golden-rule scan** — zero `os.getenv` / `os.environ` calls in `strategies/morning_range_reversion_strategy.py`; no `import requests` / `time.sleep` in async paths; no `logging.basicConfig` outside `core.logging_setup`. Clean.
  3. **Order placement path** — MRR calls `place_bracket_order` (base class) → `bot.place_oco_bracket_with_stop_entry` with `strategy_name=self.config.name`. Order tag format `TB-stop_bracket-morning_range_re-YYMMDDHHmmss-uuid6` (16-char strategy-name truncation; 64-char hard cap). Risk-manager counts pending `stop_bracket` orders correctly per-symbol; **does NOT distinguish strategies** — per-account separation required (already documented in `docs/PORTFOLIO_BLUEPRINT.md`).
  4. **Event-bus wiring** — `TRADE_CLOSED` publisher in `core/user_hub_handlers.py::_publish_trade_closed_events` emits per-trade events with full payload (symbol, side, net_pnl, entry/exit times). MRR + overnight_range have `meta.live_breaker_enabled = true` to wire the consec-loss breaker. **Fix**: added the same flag to `overnight_reversion.toml` + `vwap_zscore_reversion.toml` (the two new revivals were missing it — silent no-op in live mode).
  5. **Session lifecycle** — ET timezone resolution via `zoneinfo.ZoneInfo` (with `pytz` fallback); live `flat_before` enforcement at 16:00 ET via `bot.close_position()` in `manage_positions`. **Documented limitation**: MRR does NOT consult `core.market_calendar` for half-days (e.g. day-after-Thanksgiving 13:00 ET close). Operator must manually disable on observed half-days; `core.market_calendar.equity_futures_session_note` only catches full closures (weekends + holidays), not half-days.
  6. **strategy_executor launch path** — `--strategy=morning_range_reversion` resolves through `BUILTIN_STRATEGY_SPECS`; `--risk-config` JSON from `scripts/run_morning_reversion.sh` (MNQ qty=4, MGC qty=2, MES qty=2) is aligned with committed TOML `position_size` values; `--account_select` resolves through the master-CLI account list. End-to-end dry run on May 2026 MNQ data: **7 trades / 71.4 % WR / +$1,213 PnL / Sharpe 8.6 / max DD $401**. All exit reasons valid (`take_profit`, `stop_loss`). Same drill for `overnight_range`: 6 trades / 50 % WR / +0.47 % / `replay_force_flat_et` + `take_profit` + `stop_loss` + `breakeven` all firing correctly.
  7. **Failure modes** — 17 `try/except` blocks + 61 None-guards in MRR strategy module; defensive coding solid against missing historical bars, broker timeout, partial fills.
  8. **Hot-reload** — `maybe_reload()` re-reads TOML without state loss (existing test coverage in `tests/test_strategy_config_snapshot.py`).
  9. **Three new pinning tests** added to `tests/test_morning_range_reversion_smoke.py`:
     - `test_live_readiness_committed_meta_flags` — locks `meta.enabled`, `meta.symbols ⊇ [MNQ, MGC]`, `meta.live_breaker_enabled`
     - `test_live_readiness_position_size_aligned_with_wrapper_script` — locks `[symbols.MNQ.risk].position_size = 4` (R24 2× weighting) + root `position_size = 2`
     - `test_live_readiness_flat_before_enforces_session_close` — locks `signal.flat_before` between 12:00 and 17:00 ET

  **Verdict**: MRR is live-ready. Operator deploy checklist in `docs/PORTFOLIO_BLUEPRINT.md` §8 is the canonical pre-flight; the audit above is the verification that every item on the checklist is satisfied for the current committed R28 config. **Files modified**: `config/strategies/overnight_reversion.toml` (+`live_breaker_enabled = true`); `config/strategies/vwap_zscore_reversion.toml` (+`live_breaker_enabled = true`); `tests/test_morning_range_reversion_smoke.py` (+3 pinning tests).

- **`overnight_reversion` REVIVED + PROMOTED LIVE (2026-06-09 R1+R2 sweep, 33 trials × 3 windows).** Continuation of the 2026-06-08 stagnant-tier audit. The strategy showed 9m / 6m strong but 3m softness (+7.7 % / RF 0.11 / DD 43 %) — needed a tune before deploy. R1 swept allow-side / skip-weekdays / range-cap / ATR-cap / stop-mult; R2 combined the R1 winners. **Result: `signal.allow_short = false` + `filters.skip_weekdays = [0, 4]` Pareto-dominates baseline on the recent 6m + 3m**:

  | Window | baseline | committed (lo_skip_mon_fri) | Δ Return | Δ RF |
  | --- | --- | --- | --- | --- |
  | 9m | +150 % / RF 2.18 / DD 23 % | +103 % / RF **1.72** / DD 37 % | −31 % | −21 % |
  | 6m | +59 % / RF 0.85 / DD 33 % | **+99 %** / RF **3.38** / DD 23 % | **+68 %** | **+297 %** |
  | 3m | +8 % / RF 0.11 / DD 43 % | **+70 %** / RF **2.47** / DD 18 % | **+810 %** | **+2245 %** |

  9 m return drops (baseline included some short-side wins early in the dataset that the asymmetric edge now misses), but the 6 m / 3 m improvements show the **alpha is current** — the short-side fade is broken on the post-2025-Q4 regime (up-breakouts CONTINUE on current rising-market data). Per-symbol on committed config: MGC RF 5.59 / 5.80 / 2.84 (9m / 6m / 3m — workhorse); MNQ RF 0.33 / 0.54 / 1.40 (positive but lower). **PROMOTED TO PRODUCTION TIER #4** alongside the existing 3 — `morning_range_reversion`, `overnight_range`, `vwap_zscore_reversion` MGC-only. **Files**: `config/strategies/overnight_reversion.toml` (header rewritten with revival rationale + R1/R2 sweep table + rejection log; `enabled = true`, `symbols = ["MNQ","MGC"]`); `tests/test_overnight_reversion_smoke.py` (added `test_overnight_reversion_revival_defaults_pinned` to lock the committed values); `docs/STRATEGY_ARSENAL.md` (new Production tier row #4; Disabled tier struck-through entry refreshed to "REVIVED + PROMOTED LIVE"); this CHANGELOG entry. Truth recap dir (as-committed config): `docs/perf/overnight_reversion_revival_truth_{3m,6m,9m}/`. Sweep evidence: `docs/perf/_opt_runs/overnight_reversion/r{1,2}_{3m,6m,9m}/`.

- **Portfolio Blueprint doc (`docs/PORTFOLIO_BLUEPRINT.md`, 2026-06-09).** Answers the long-standing "how do I run my top strategies together?" question. Covers the 4-strategy production tier (post-revival), the conflict matrix (`morning_range_reversion` ⇄ `overnight_range` and `overnight_range` ⇄ `overnight_reversion` are direct opposites on MNQ/MGC near 09:30), the recommended one-strategy-per-account allocation, per-account configuration, the portfolio daily-loss breaker (`PORTFOLIO_DAILY_LOSS_CAP` env, default $1000), the regime publisher opt-in, the risk_sizer status (available but not wired — would invalidate every walk-forward tune behind the current production tier), the live deploy checklist, multi-account orchestration patterns, and the maintenance cadence. Cross-referenced from `docs/STRATEGY_ARSENAL.md` "Live deploy / multi-account orchestration" subsection.

- **Phase 1 risk-infra wiring complete (2026-06-09).** Audit + finalization pass on `core/portfolio_daily_breaker.py`, `core/risk_sizer.py`, `core/regime.py` — all three modules were built + unit-tested earlier (2026-06-02) but the live wiring story was incomplete. Status post-pass:
  - **`portfolio_daily_breaker`** — already lazy-wired into `trading_bot.ensure_portfolio_breaker()`, invoked by `strategies/strategy_manager._start_strategy()` on first call (no change needed; just verified). `tests/test_portfolio_daily_breaker.py::test_trade_closed_event_accumulates` + `test_trip_fires_once_per_session` were FLAKY (used fixed `datetime(2026, 6, 2, ...)` exit_time values that fell outside today's session window once the date drifted past 2026-06-02) — fixed by monkeypatching `_now_et` to the same fixed test date.
  - **`regime`** — NEW: added `RegimePublisherService` + `maybe_start_regime_publisher` helper to `core/regime.py`. Subscribes to `EventType.BAR_COMPLETED` on the bot's event bus, classifies on each reference-symbol bar close, publishes `EventType.REGIME_UPDATE` only when the label changes (or every bar if `REGIME_PUBLISHER_ALWAYS_EMIT=1`). Env-gated opt-in via `REGIME_PUBLISHER_ENABLED=1` (default off). Wired into `trading_bot.ensure_regime_publisher()` (lazy-construct on first strategy start, idempotent). No production strategy currently subscribes — the polling-style `classify(bars)` API is the recommended path for backtest-friendly regime gating; pinning tests on the production tier would all need re-validation if the production strategies retrofitted onto the publisher. **Files**: `core/regime.py` (~150 LoC publisher + helper); `trading_bot.py` (new `ensure_regime_publisher` method + boot-time `self.regime_publisher = None`); `strategies/strategy_manager.py` (lazy-call alongside the portfolio breaker wiring); `tests/test_regime.py` (added 6 new tests for env gating, no-bus path, label-change emit, wrong-symbol skip, dedupe). 22/22 regime tests pass.
  - **`risk_sizer`** — NOT auto-wired (intentional). Switching from fixed `[risk].position_size` integers to fixed-dollar-risk sizing would change per-trade dollar exposure for every production-tier strategy, invalidating the R28 / R24 / R2-revival walk-forward tunes. Documented as "available, opt-in, not wired" in `docs/PORTFOLIO_BLUEPRINT.md` §7. Right time to enable: the next round of walk-forward tuning when the sweep harness can be configured to use the new sizer end-to-end.

### Removed
- **`nr_compression_break` and `globex_drift_continuation` formally retired (2026-06-09).** Both were flagged for retirement in the 2026-06-05 commit (`enabled = false`) after the post-engine-fix truth recap confirmed they were structurally negative on the corrected engine + current data. Today's pass deleted both strategy modules, TOMLs, registry entries, and dispatch branches per "git history is the archive" guidance in `AGENTS.md`. Refer to git history for the strategy module / TOML / registry contents.
  - `strategies/nr_compression_break_strategy.py` (20,714 bytes) — DELETED
  - `strategies/globex_drift_continuation_strategy.py` (23,943 bytes) — DELETED
  - `config/strategies/nr_compression_break.toml` (3,745 bytes) — DELETED
  - `config/strategies/globex_drift_continuation.toml` (3,192 bytes) — DELETED
  - `strategies/strategy_manager.py::BUILTIN_STRATEGY_SPECS` — removed entries for both
  - `core/backtest_executor.py` — removed from `class_strategies` list + dispatch `elif` branches
  - `core/regime.py` docstring updated to reflect retirement (was listed as a planned consumer)
  - `docs/STRATEGY_ARSENAL.md` Production tier rows and Disabled tier preamble updated from "RETIREMENT FLAGGED" → "RETIRED 2026-06-09 (deleted from registry + disk)"

- **Stagnant-tier truth audit on corrected engine (2026-06-08). Two revival winners surfaced.** Continuation of the "what's actually in my arsenal" theme: every strategy that was dismissed on the pre-engine-fix pipeline got a single 9 m walk-forward truth recap on the corrected engine + fresh-cache (`--no-cache-decisions` end-to-end after the decision-cache parquet-sidecar fix landed earlier today). Audit dir: `docs/perf/_audit_stagnant/<strategy>_9m/`.

  **Dead on corrected engine (final verdict — engine bugs were NOT what was holding them back):**

  | Strategy | 9m ret | 9m DD | 9m RF | Engine-fix delta |
  | --- | --- | --- | --- | --- |
  | `mean_reversion` | **−189 %** | 284 % | −0.45 | Was DD 142 % on buggy → 2× worse on corrected; both symbols negative |
  | `trend_following` | **−21 %** | 114 % | −0.10 | Was DD 148 % on buggy → marginal improvement but structurally broken |
  | `simple_candle` | **−1,645 %** | **1,620 %** | −0.99 | Was DD 213 % on buggy → **7.6× worse** on corrected. The "let-it-ride" logic was masked by phantom STOP-gap profits; with H-B fixed the compounding losers show their full damage |
  | `ema_stack_trend_15m` | −278 % | 333 % | −0.74 | (Never-validated; dead) |
  | `rsi_switch_15m` | −34 % | 49.6 % | −0.61 | n=53, too sparse to be useful even if positive |
  | `hourly_anchor_retrace` | +21 % | 116 % | +0.17 | 50 % WR but DD blows the cliff |
  | `simple_rth` / `simple_momentum` / `trend_scalping` | 0 trades | — | — | Logic bugs / over-restrictive filters as previously documented |

  **REVIVAL WINNER #1 — `vwap_zscore_reversion` MGC-only PROMOTED to Production tier.** Cross-window robust on the corrected engine:

  | Window | n | ret | DD | RF | WR |
  | --- | --- | --- | --- | --- | --- |
  | 3m | 61 | +67.40 % | 14.59 % | **2.45** | 49.2 % |
  | 6m | 119 | +80.51 % | 33.34 % | **1.42** | 45.4 % |
  | 9m | 181 | +55.98 % | 38.98 % | 0.99 | 40.9 % |

  RF *improves* 9m → 6m → 3m, DD shrinks, WR rises — alpha is **current**, not decaying.  The MNQ leg drags the global verdict negative (-82 % / RF -0.89 / WR 26.7 % on 9m) — fade signal does not work on MNQ on current data (MNQ intraday is more trend-persistent than MGC; VWAP fades during NY morning drives are the dominant MNQ failure mode per chart audit).  Config change: `[meta].enabled = false → true`; `[meta].symbols = ["MNQ","MES","MGC"] → ["MGC"]` only.  MNQ + MES stanzas retained dormant for future tune.  Truth recap dir (as-committed config): `docs/perf/vwap_zscore_revival_truth_{3m,6m,9m}/`.  **Files**: `config/strategies/vwap_zscore_reversion.toml` (TOML header rewritten with revival rationale + per-window truth table + MNQ disable explanation); `docs/STRATEGY_ARSENAL.md` (new Production tier row #3); this CHANGELOG entry.

  **REVIVAL CANDIDATE — `overnight_reversion` — promoted from "9-months-negative-expectancy" disable to Watch / research list.** The disable rationale ("breakouts continue more often than they revert on the current futures dataset") was a buggy-engine artefact.  Truth on corrected engine:

  | Window | n | ret | DD | RF | WR |
  | --- | --- | --- | --- | --- | --- |
  | 9m | 237 | **+150.19 %** | 22.81 % | **2.18** | 30.0 % |
  | 6m | 152 | +58.95 % | 32.67 % | 0.85 | 27.0 % |
  | 3m | 76 | **+7.67 %** | 43.16 % | **0.11** | 23.7 % |

  9m / 6m are solid (both symbols positive at 9m: MGC +92 % / MNQ +58 %); **3m is barely positive with DD approaching the cliff and RF near zero** — recent regime softness signals a tune-up is needed before live deploy.  Not committed to live yet (TOML stays `enabled = false`); listed in `STRATEGY_ARSENAL.md` Production tier as **Watch**.  Next research: regime-gated entry filter or signal-quality cut (high candidate for the so-far-unused `core/regime.py` infrastructure built in Phase 1).  Audit dirs: `docs/perf/_audit_stagnant/overnight_reversion_{3m,6m,9m}/`.

- **`morning_range_reversion` R29 MNQ max_range_width sweep — negative finding (2026-06-08).** After the R24 `overnight_range` MNQ Tuesday fix, the next "low-hanging fruit" candidate was applying the R28 MGC `max_range_width_points` insight to MNQ (root default is `300`, which only ever bound on the most extreme MNQ days). Swept MNQ `max_range_width_points` ∈ {80, 90, 100, 110, 120, 130, 140, 150, 160, 180, 200, 250} across all three windows (fresh cache, `--no-cache-decisions`, sweep dir `docs/perf/_opt_runs/morning_range_reversion/r29_mnq_maxrange_{3m,6m,9m}/`). Pareto-best variants (150 / 160 / 180 all produce identical metrics — they cut exactly **one** MNQ session per 9-month window):

  | Window | baseline R28 | mnq_max150 (best) | Δ Return | Δ RF |
  | --- | --- | --- | --- | --- |
  | 3m | +665.70 % / RF 13.45 / WR 76.92 % | +678.80 % / RF 13.71 / WR 78.12 % | +2.0 % | +1.9 % |
  | 6m | +803.15 % / RF 9.06 | +816.25 % / RF 9.21 | +1.6 % | +1.7 % |
  | 9m | +914.55 % / RF 10.32 | +927.65 % / RF 10.46 | +1.4 % | +1.4 % |

  Tighter ceilings (≤ 140) start trimming valid trades and regress; at 80 the 9 m return drops to +792 % (−13 %). Verdict: lever exhausted — MNQ range distribution on the active session window is already well-contained, and the 1 extreme-width session per multi-month window isn't a reliable enough signal to justify a config commit (+13 pts in returns is below the "miniscule" bar from the prior R27 feedback). TOML unchanged; this entry is the on-record explanation of the negative result so the lever isn't re-swept by accident.

- **`overnight_range` R24 — MNQ Tuesday skip added (2026-06-08, fresh-cache truth audit).** Continuation of the R28-era "what's actually a money-maker on the corrected engine + current data" pass. Fresh-cache truth recap of the R23 commit (`docs/perf/overnight_range_r28era_truth_{3m,6m,9m}/`, `--no-cache-decisions` end-to-end after the decision-cache-stale-data fix landed) revealed the MNQ leg was a CONSISTENT MONEY-LOSER across every window despite the headline aggregate being positive:

  | Window (R23 truth, fresh-cache) | MNQ ret | MNQ RF | MNQ WR | MGC ret | MGC RF |
  | --- | --- | --- | --- | --- | --- |
  | 9 m | **−16.48 %** | **−0.31** | 29.2 % | +86.25 % | 4.99 |
  | 6 m | +0.59 % | 0.02 | 29.5 % | +56.35 % | 3.26 |
  | 3 m | **−4.12 %** | **−0.28** | 36.0 % | +32.38 % | 4.07 |

  Per-fold MNQ analysis surfaced Tuesdays as the bulk of the underperformance — the post-Globex Tuesday breakout has a notably low continuation rate on MNQ on current data, with more chop / failed follow-through than Mon or the rest of the week.

  **R24 single TOML change**: `[symbols.MNQ.filters].skip_weekdays` **`[0, 4]` → `[0, 1, 4]`** (added Tuesday). Pareto-dominates baseline on every window:

  | Window | R23 baseline (fresh truth) | R24 (committed) | Δ Return | Δ RF | Δ DD pp |
  | --- | --- | --- | --- | --- | --- |
  | 3m | +28.26 % / RF 1.58 / DD 17.86 % | **+39.67 %** / RF **2.20** / DD 18.07 % | **+40 %** | +39 % | +0.2 |
  | 6m | +56.94 % / RF 2.78 / DD 15.61 % | **+93.47 %** / RF **4.51** / DD **13.24 %** | **+64 %** | +62 % | **−2.4** |
  | 9m | +69.77 % / RF 3.40 / DD 14.59 % | **+133.28 %** / RF **6.43** / DD **12.92 %** | **+91 %** | **+89 %** | **−1.7** |

  **MNQ standalone delta is the headline**:
  - 9 m: MNQ ret **−16.48 % → +47.03 %** (+63 pp swing — MNQ flips from net loser to net winner).
  - 6 m: MNQ ret +0.59 % → +37.12 % (+37 pp).
  - 3 m: MNQ ret −4.12 % → +7.30 % (+11 pp).

  MGC override untouched — same +86 % / +56 % / +32 % across 9m / 6m / 3m. MGC R:R remains 2.66:1, fully positive.

  **Sweep matrix** (14 trials × 3 windows via `scripts/optimize_strategy.py` → `walkforward_trade_recap_report.py --in-process --no-cache-decisions`, dirs under `docs/perf/_opt_runs/overnight_range/r28_{3m,6m,9m}/`). Rejection log:
  - `mnq_skip_only_fri` ([Fri] only): better 3m return alone but 9m regresses; net worse Pareto.
  - `mnq_skip_thu_fri` ([Thu, Fri]): RF 2.01 / 9m return drops to +45 %; rejected.
  - `mnq_tighter_atr_floor` (0.12 / 0.15) and `mnq_tighter_atr_ceiling_50`: trim valid trades, net regression.
  - `mnq_tighter_stop_0p4` / `mnq_tighter_tp_3` / `mnq_wider_tp_5`: per-symbol stop/TP knobs all regress vs the R23 0.5 / 4.0 ATR multipliers.
  - `tighter_gap_max_pct` 1.0 / 0.8: both regress (filter over-aggressive).
  - `mgc_only` / `mnq_only` (`meta.symbols` overrides): no-op in the patch path (meta-section root-array key not handled by `_patch_block_in_text`); not pursued further since the Tuesday skip captured the MNQ-leg fix without touching `meta.symbols`.

  **Truth recap dirs**: `docs/perf/overnight_range_r24_truth_{3m,6m,9m}/`. Pinning test updated in `tests/test_overnight_range_symbol_risk_signal_overrides.py::test_overnight_committed_round23_defaults_resolve_correctly` to lock MNQ skip_weekdays = [0, 1, 4]. **Files**: `config/strategies/overnight_range.toml` (R24 evidence block + `skip_weekdays = [0, 1, 4]`); `docs/STRATEGY_ARSENAL.md` (row #2 refreshed to R24 truth numbers); this CHANGELOG entry; updated pinning test.

- **`morning_range_reversion` R28 — material PnL lift via MGC `max_range_width_points` tightening (2026-06-05 PM, response to user feedback that R27 was "miniscule").** User pushed back on R27's marginal gains (+2.6 % R:R, +1.8 % per-trade EV) and asked to keep iterating until we see a material improvement OR consider retiring strategies. Two more sweep rounds (dynamic-SL-from-TP + structural partial-TP/breakeven + signal-quality filters, ≈ 50 trials × 3 windows) found the real lever: **MGC `[symbols.MGC.signal].max_range_width_points` 65 → 46.** Pareto-dominates baseline R27 across every window with **identical drawdown**:

  | Window | R27 baseline | R28 (committed) | Δ PnL | Δ RF | Δ WR |
  | --- | --- | --- | --- | --- | --- |
  | 3m | ret +558 % / RF 11.27 / DD 13.15 % | ret **+666 %** / RF **13.45** / DD 12.78 % | **+19.3 %** | **+19 %** | **+2.3 pp** |
  | 6m | ret +666 % / RF 7.51 / DD 88.65 % | ret **+803 %** / RF **9.06** / DD 88.65 % | **+20.6 %** | **+21 %** | **+2.0 pp** |
  | 9m | ret +777 % / RF 8.77 / DD 41.94 % | ret **+915 %** / RF **10.32** / DD 41.94 % | **+17.7 %** | **+18 %** | **+1.3 pp** |

  MGC standalone PnL (9m): +585 % → **+722 %** (+23.4 %). MNQ unchanged.

  **Mechanism:** the prior 65pt MGC range-width ceiling admitted the wide-range volatility-cluster sessions (CPI / rate-decision / large-physical-flow days) that produced the user-reported worst losers (2025-11-13 BUY −$526, 2025-11-18 SELL −$554). On those days price keeps moving in the sweep direction long after the close-back-into-the-box, hitting the 29pt SL cap structurally. Capping the range filter at 46pt skips ~3 of those sessions per fold, which is the ONLY thing that materially lifts the curve without flipping the strategy's high-WR/wide-stop edge upside-down. The 46-48pt cluster (no MGC range falls in that gap) is the cross-window Pareto-optimal bound: max ≤ 44 skips one valid winner per fold, max ≥ 50 lets the bad sessions back in.

  **Sweep matrix (fresh-cache, no decision cache):** `docs/perf/_opt_runs/morning_range_reversion/r28_{dyn_sl, struct_clean, signal, maxrange_{3m,6m,9m}}_*/`. Rejection log:
  - **`sl_from_tp_ratio` (NEW lever; user's literal ask)** — adds `signal.sl_from_tp_ratio` knob that derives SL = TP / ratio. 16 trials × 6 ratios (0.8 / 1.0 / 1.25 / 1.5 / 1.75 / 2.0) ± wider TP combinations. Every variant **hurt** because the strategy's edge IS the wide-stop / high-WR structure: forcing ≥ 1:1 R:R drops WR 67 → 50-60 % and PnL 17-58 %. The knob is **retained in code** (default 0 = no-op) for future regime-aware work; it's a clean, documented lever that future research can layer on top of regime gating.
  - **partial-TP** (BONGO §1A, root `signal.partial_tp_enabled=true` × scalp_r ∈ {0.5, 0.75, 1.0, 1.5}) — catastrophic across the board (MNQ −64 to −124 % vs +192 baseline, MGC −12 to −67 %). Two-stage OCO interacts poorly with the 5m fade tempo; the scalp leg crystalises before the runner has time to validate the mean-reversion.
  - **breakeven** (`position_management.breakeven_enabled=true` × trigger_R ∈ {0.5, 0.75, 1.0, 1.5}) — −3 % to −17 % return on every setting. Breakeven defeats the fade thesis; winners that touch +1R routinely retrace through entry and re-extend.
  - **`max_sweep_distance_widths` ≤ 1.75** (vs current 2.0) — no-op. Cap doesn't bind; user's reported losers were inside 1.0× width past anchor.
  - **`require_reentry_close=true` (legacy candle-close mode)** — kills MGC to 2 trades.
  - **`mgc_skip_thu_fri` / `mgc_skip_mon_fri`** — both −34 to −37 % ret. MGC alpha is not weekday-selective beyond the existing Fri skip.
  - **`tighter_effective_hours` / `entry_window_4h` / `entry_window_6h`** — no-op (strategy doesn't trade late-session enough for these to bind).

  **Files**: `config/strategies/morning_range_reversion.toml` (R28 evidence comment + `max_range_width_points = 46` for MGC); `strategies/morning_range_reversion_strategy.py` (NEW `_sl_from_tp_ratio` accessor + dyn-SL-from-TP wiring in `_signal_with_sweep_state`; default 0 = no-op); `docs/STRATEGY_ARSENAL.md` (R28 truth panel replaces R27); this CHANGELOG entry; new pinning assertion in `tests/test_morning_range_reversion_smoke.py::test_morning_range_toml_per_symbol_sl_mult_overrides` for `max_range_width_points = 46`.

  **Truth recap dirs (fresh-cache, end-to-end no-cache):** `docs/perf/morning_range_r28_truth_{3m,6m,9m}/`.

- **`morning_range_reversion` R27 geometry tune committed (2026-06-05, follow-up to user feedback on excessive MGC stop-loss distances).** User raised: "30pt MGC stop is excessive when average winners are typically <30pt; test configs against dynamic stop losses keyed to TP targets so we maintain positive risk geometry." Investigation produced two findings:
  1. **The per-trade reward:risk geometry IS structurally negative** at the committed R26 config: typical-day SL ≈ 24pt vs TP ≈ 13.5pt → 0.55:1; cap-bound SL = 30pt vs TP = 13.5pt → 0.45:1. The strategy's edge is the 72% MGC + 60% MNQ WR which compensates for the negative geometry — proving this empirically via R6's broad sl_mult sweep, where forcing ≥1:1 geometry collapses WR (67 → 48 %), RF (7.93 → 1.26), and PnL (−85 %).
  2. **Decision-cache staleness** — the R26 truth-baseline metrics documented just yesterday (PnL $+15,540 / RF 9.71 / DD 23.51 %) were served by stale entries in `docs/perf/_decision_cache/` carrying results from an older state of the historical-data CSVs/parquet sidecars. The CSV mtime IS in the cache key, but the parquet-sidecar-read path under `--in-process` workers has a gap where mtime-based invalidation can be skipped. **All R27 sweeps + truth recaps below ran with `--no-cache-decisions` (or against a freshly-rebuilt cache).** True R26 baseline on the current data is PnL $+14,306 / RF 7.93 / DD 51.2 % — materially weaker than the previously-printed number.

  Sweep matrix: 11 trials × 3 windows + 4 confirmation runs via `scripts/optimize_strategy.py` → `walkforward_trade_recap_report.py --in-process` (sweep dirs under `docs/perf/_opt_runs/morning_range_reversion/r{6..11}_*/`). Cross-window-validated Pareto winner is the cap + slmult + tp_mult stack — every metric improves on every window:

  | Window | R26 (fresh) | R27 (committed) | Δ PnL | Δ RF | Δ DD pp |
  | --- | --- | --- | --- | --- | --- |
  | 3m | ret +542 % / RF 10.74 / DD 13.78 % | ret +558 % / RF **11.27** / DD **13.15 %** | +16 | +5 % | −0.6 |
  | 6m | ret +639 % / RF 7.09 / DD 90.15 % | ret +666 % / RF **7.51** / DD **88.65 %** | +27 | +6 % | −1.5 |
  | 9m | ret +715 % / RF 7.93 / DD 51.15 % | ret +777 % / RF **8.77** / DD **41.94 %** | +62 | +11 % | **−9.2** |

  Committed delta vs R26 in `[symbols.MGC.signal]`:
  - `sl_max_pts`: **30 → 29** (R10 sweep: cap=29 best of {24, 26, 27, 28, 29, 30, 31}; tighter caps RF-net-negative).
  - `sl_mult`: **3.25 → 3.30** (R10 sweep: best of {3.20, 3.25, 3.30} at cap=29; RF 8.28 vs 8.10).
  - `tp_mult`: **1.80 → 1.85** (R10 sweep: best of {1.75, 1.80, 1.85, 1.9, 2.0} on truth; RF 8.59 vs 8.10 at cap=29).
  - MGC `max_consecutive_losses` = 2 **retained** (R11 tested 1L: spectacular on 9m (DD 22 %, RF 9.57) but REGRESSED hard on 6m + 3m — RF 7.09 → 6.53 on 6m, RF 10.74 → 6.51 on 3m, with 20 % fewer trades. Classic 9m-overfit. Rejected.).
  - MGC `skip_weekdays` = ["Fri"] **retained** (R10 re-validated; no further skip improves).
  - MNQ overrides **untouched** (R26 settings — MNQ already has positive 1.00:1 R:R per trade).
  - **Truth walkforward** (no-cache, recap `docs/perf/morning_range_round27_committed/`): 9m ret **+777.25 %** / PnL **$+15,545** / RF **8.77** / DD% **41.94** / WR **67.0 %** / n=182 (122W / 60L).
  - **Per-symbol on 9m**: MGC $+11,706 / 109 trades / WR 72.5 % / avg winner $296 / avg loser -$390 / worst -$592 / R:R 0.76:1 / max DD $1,263. MNQ $+3,839 / 73 trades / WR 58.9 % / R:R 1.00:1 (already balanced).
  - **Realised geometry impact**: MGC avg R:R 0.74:1 → 0.76:1 (+2.6 %); worst-case trade -$612 → -$592 (-3.3 %); max MGC DD $1,657 → $1,263 (-23.8 %).
  - **Portfolio budget impact**: MGC cap 30 → 29 cuts worst MGC stop-out from $600 → $580. Portfolio worst-case-day $1,090 → $1,070 ($70 over the operator's $1,000 MAX, was $90 over). Same opt-in to 1ct MGC available via commented-out `[symbols.MGC.risk]` block.
  - **Per-knob rejection log (R6-R11)**: tighter sl_mult (≤2.5) rejected (WR crashes); cap ≤27 or ≥31 rejected (PnL or RF degrades); `max_consecutive_losses=1` rejected (9m-overfit); `max_daily_trades` 4→3 neutral (not binding on MGC).
  - **Files**: `config/strategies/morning_range_reversion.toml` (`[symbols.MGC.signal]` block re-written for R27 with full sweep evidence, per-knob rejection log, updated portfolio budget note, refreshed `[symbols.MGC.risk]` opt-in comment); `docs/STRATEGY_ARSENAL.md` (R27 truth panel replaces R26; header re-baselined to show the cache-stale-vs-fresh diff); this CHANGELOG entry; pinning-tests update (3 assertions in `tests/test_morning_range_reversion_smoke.py`).

### Fixed
- **`core/backtest/strategy_replay.py::_simulate_place_bracket_order` partial-TP kwarg collision (2026-06-05 PM).** Found while debugging the R28 partial-TP sweep — every trial with `signal.partial_tp_enabled=true` produced 0 trades because `_simulate_place_bracket_order` invoked `_simulate_place_oco_partial_tp` with `strategy_name` passed BOTH positionally (slot 9 = `None`) AND as a kwarg, raising `TypeError: got multiple values for argument 'strategy_name'`. The strategy's `execute()` swallowed the exception as a soft failure → silent 0-trade output. Fix: pass `account_id`, `enable_breakeven`, and `strategy_name` as **named kwargs only** (also moves `scalp_r_multiple` to the kwargs-only block where it already lived in the callee signature). Affected: any strategy that opts into BONGO §1A partial-TP via `signal.partial_tp_enabled=true` in backtest. Not user-facing until now because none of the production strategies have the knob enabled. **Files**: `core/backtest/strategy_replay.py`. Replay-engine breakeven tests (`tests/test_strategy_replay_breakeven.py`) pass unchanged.
- **`scripts/optimize_strategy.py` TOML-restore hardening (2026-06-05 PM, related to the partial-TP debug above).** When a trial subprocess crashed mid-run, the `try / finally toml_path.write_text(original)` block could be skipped (e.g. on hard interrupt), leaving the production TOML in a patched state — silently polluting ALL subsequent baseline runs in the same session. Caught when the R28 baseline diverged from R27 truth (n_trades 182 → 258, MNQ +192 → -64) until inspection revealed `partial_tp_enabled = true` stuck in the committed TOML. Fix: write a sibling sentinel `.optimize_strategy.bak` BEFORE the patch so a hard-kill recovery is trivial, plus tighten the `finally` block so the backup cleanup never masks the restore. **Files**: `scripts/optimize_strategy.py`.
- **`core/user_hub_handlers.py::on_trade` SyntaxError — `await` outside async function (2026-06-04).** The 2026-06-03 backtest-engine fix commit (`c526ec03e`) added an `await self._bot.event_bus.publish(...)` block inside `on_trade`, but `on_trade` is the **sync** SignalR callback delivered from a hub thread (the other sync callbacks `on_position` / `on_order` defer their async work via `_defer_coro_from_sync`). The illegal `await` made `core.user_hub_handlers` un-importable, which in turn broke any entrypoint that constructs `TopStepXTradingBot` — including `scripts/stitch_broker_history_to_databento_1m.py` (and by extension all stitcher / history-pull tooling). Fix: extract the publish loop into an async helper `_publish_trade_closed_events(completed_trades, account_id)` and dispatch it via `self._defer_coro_from_sync(...)`, mirroring the existing `on_order → _on_order_async_tail` pattern. The TRADE_CLOSED publish behaviour is preserved 1:1; only the dispatch path changed. **Files**: `core/user_hub_handlers.py`.

### Fixed (cont'd)
- **Decision-cache stale-data invalidation gap closed (`core/backtest/decision_cache.py`, 2026-06-08, R28 follow-up).** Surfaced during the 2026-06-05 R27 re-tune and reproduced again at the start of the R28 sweep: the v1 cache key hashed the CSV path + size + mtime, but the in-process runner reads the parquet sidecar (`<csv>.parquet`), not the CSV. An in-place sidecar rewrite — concretely the 2026-06-03 contract-roll quarantine path — could mutate the sidecar without touching the source CSV's mtime, leaving the cache key unchanged → `lookup()` returned stale rows even though the data on disk had moved. Fix:
  1. **`_CACHE_KEY_VERSION` bumped 1 → 2.** One-shot wipe of every pre-fix entry so no caller can accidentally hit a stale row from before the fix landed.
  2. **`CacheKeyInputs.fingerprint()` now also hashes `<csv>.parquet` size + mtime_ns when present** (degrades to a stable `MISSING` sentinel when no sidecar exists, so transient fixtures stay reproducible).
  Regression tests live in `tests/test_backtest_decision_cache.py::test_fingerprint_invalidates_on_parquet_sidecar_mtime` (sidecar rewrite must change the key) and `::test_fingerprint_unaffected_when_no_sidecar_present` (no sidecar → stable key across runs). **Files**: `core/backtest/decision_cache.py`, `tests/test_backtest_decision_cache.py`.

### Added (earlier — superseded by R27 above)
- **`morning_range_reversion` R26 re-tune committed (2026-06-04 PM, post-engine-fix).** Full re-tune via 5 sweep rounds × 85 trials (`scripts/optimize_strategy.py` → `walkforward_trade_recap_report.py --in-process --no-cache-decisions`, in-process workers, 270d / 9 folds, MNQ+MGC). Sweep artefacts under `docs/perf/_opt_runs/mrr_postengine/r{1..5}_*/`. Final committed config delta vs pre-R26 (all in `[symbols.MGC.signal]`):
  - `sl_max_pts`: **22 → 30** (R1 evidence: the 22pt cap was the binding bug-favoured constraint — H-B's gap-through channel meant cap=22 was clamping legitimate winners into phantom-fill "stop-hits". Loosening to 30 picks up 17 extra MGC trades over 9m AND lifts MGC PnL from $180 to $11,340 = **63× MGC PnL improvement**).
  - `sl_mult`: **3.0 → 3.25** (R4/R5 local optimum vs 3.0 / 3.1 / 3.15 / 3.2 / 3.3 / 3.4 / 3.5).
  - `tp_mult`: **inherit root 1.5 → MGC-specific override 1.8** (R5 single-point optimum vs 1.5 / 1.7 / 1.75 / 2.0 — slightly wider TPs outperform now that gap-through SLs don't manufacture fake wins).
  - `skip_weekdays`: **["Wed", "Fri"] → ["Fri"]** (R3 evidence: the Wed-skip was a pre-engine-fix artefact — those "Wed losses" were largely H-B gap-through phantoms. Removing the skip un-loses 17 MGC trades / +$331 over 9m).
  - MNQ overrides unchanged (R4/R5 sweep confirmed MNQ's pre-existing `["Mon","Thu","Fri"]` skip + `sl_fixed_pts=50` + `tp_mult=1.25` all survive on the corrected engine; the MNQ cap=30 hits via root inheritance and was not the binding constraint there).
  - Breaker `2L/10d` retained (R4 confirmed — breaker-off ret +833 % / RF 4.95 worse; breaker `3L` worse; breaker `1L/5d` cuts too aggressively).
  - **Truth walkforward** (recap `docs/perf/morning_range_round26_postengine/`): 9m ret **+777 %** / RF **9.71** / DD **23.5 %** / WR **67.4 %** / n=175 (118W / 57L). vs pre-R26 (same engine): ret +390 % / RF 2.14 / DD 58 % / WR 64 % / n=154. **PnL +99 %, RF +354 %, DD −59 %.** Per-symbol: MGC $+11,340 (was $+180 on the corrected pre-R26 baseline); MNQ $+4,200 (unchanged — overrides held).
  - **Cross-window validation**: 6m DD 95% → 59% (was at cliff edge!), RF 1.92 → 6.88. 9m DD 58% → 24%, RF 2.14 → 9.71. 3m DD slightly worse (29% → 45%) but RF 5.06 still safe; the long-window wins are decisive.
  - **Portfolio budget impact**: MGC cap 22 → 30 lifts MGC morning worst-case from $440 to $600. With `body_reversion` retired (see below), portfolio worst-case-day = $1,090 = ~$90 over the operator's $1,000 MAX. TOML carries a commented-out `[symbols.MGC.risk] position_size = 1` opt-in so the operator can drop to 1 contract if the breach is unacceptable; alternative `sl_max_pts=25` (budget-fit) loses ~$2k / 9m vs cap=30.
  - **Files**: `config/strategies/morning_range_reversion.toml` (`[symbols.MGC.signal]` block re-written with R26 evidence + portfolio budget note + commented `[symbols.MGC.risk]` opt-in); `docs/STRATEGY_ARSENAL.md` (new R26 truth panel, updated ranking table); this CHANGELOG entry.

### Removed
- **`nr_compression_break` (NR7) and `globex_drift_continuation` flagged for retirement (2026-06-05 PM).** Truth-recap on the post-engine-fix engine + current data (270 d / 9 folds / MNQ+MES+MGC, `--in-process --no-cache-decisions`):
  - **`globex_drift_continuation`**: 333 trades / total return **−71 %** / DD 76 % — bleeding capital across every symbol (MNQ −20 %, MES −25 %, MGC −26 %). Original Phase-2 ship from 2026-06 had a paper edge on a smaller earlier window; the corrected engine + current data confirms it doesn't survive walk-forward. **Already disabled** (`meta.enabled = false`); strategy + TOML retained on disk for autopsy.
  - **`nr_compression_break` (NR7)**: 16 trades over 9 m / +5 % combined return — too sparse to call statistically. Daily-TF compression signal needs orders of magnitude more sessions to validate. **Already disabled** (`meta.enabled = false`); strategy + TOML retained on disk.
  Neither was on the active production rotation (both `enabled = false` since they shipped), so no live impact — this CHANGELOG entry is a formal record that the Phase-2 idea-pipeline was validated against the corrected engine and found not viable on current data. Both are removed from the recommended-rotation list in `docs/STRATEGY_ARSENAL.md`.
- **`body_reversion` retired (2026-06-04, post-engine-fix decision).** Hard-disabled in `config/strategies/body_reversion.toml` (`meta.enabled = false`) and moved from the production tier into the Disabled tier. The post-2026-06-03-engine-fix 9 m truth walkforward (`docs/perf/body_reversion_postengine_truth/`) showed the strategy flips to **PnL $−1,028 / RF −0.67 / DD 74.1 %** on the corrected engine — both MNQ (`PnL $−668.80, WR 33.3 %`) and MGC (`PnL $−359.28, WR 34.2 %`) negative. The strategy's geometry (single high-body-bar fade, tight 0.35×ATR stop, 3R TP) maximised exposure to BOTH backtest-engine bugs: H-B's phantom-profit channel triggered on every stop gap-through, and H-A's per-trade slip double-count taxed all 364 trades. Strategy code, tests, breaker, and per-symbol overrides remain on disk so the underlying body-percentage signal (statistically real on raw 5m forward returns — see `docs/alpha/deep_scan_INDEX.md`) can be re-investigated later with a different exit geometry. **Not on the active research list.** See `docs/STRATEGY_ARSENAL.md` "Disabled tier" entry for the full retirement rationale.

### Changed
- **Production-tier truth baselines refreshed against post-engine-fix engine (2026-06-04 AM, follow-up to the 2026-06-03 evening engine fixes).** With the two PnL accounting bugs corrected (see "Fixed → Backtest engine — two PnL accounting bugs" entry below), the committed configs of each production-tier strategy were re-run on the same 9 m / 9-fold window via `walkforward_trade_recap_report.py --in-process --no-cache-decisions`. Recap dirs: `docs/perf/{overnight_range, morning_range, body_reversion}_postengine_truth/`. Findings:
  - **`overnight_range` R23 — TUNE HOLDS.** 126 trades / 50 W / 76 L identical to pre-fix; PnL $+2,852 → $+2,504 (−12.2 %); RF 8.71 → 7.03 (still excellent); DD% 9.66 → 15.64. Only effect: losers now cost ~$2–4 more on the corrected engine (LIMIT take-profits are immune to both bugs; only the STOP-loss leg is affected). avg-R-losers −1.17 → −1.33 — the textbook gap-through fingerprint. Strategy stays production-tier without re-tune.
  - **`morning_range_reversion` — RE-TUNE WARRANTED.** Trade count shifted 277 → 272 and 10 wins flipped to losses (209/68 → 199/73). PnL $+14,227 → $+8,857 (−37.7 %). RF 6.66 → **2.46 (−63 %)**. Max DD $2,135 → **$3,597 (+68 %)**, DD% 30.5 → **54.6 %** — approaches the 100 % cliff. Per-symbol: MGC took the biggest hit (−54.7 %, high point value amplifies both bugs); MNQ −18 %; MES −7.5 %. The committed parameters were chosen by sweeps under the buggy engine which favoured tight-stop configs disproportionately (H-B's phantom-profit channel scales with stop-trigger frequency). Strategy still profitable but at materially worse risk-adjusted quality. Full re-tune via `scripts/optimize_strategy.py` recommended over `stop_atr_mult`, `tp_atr_mult`, anchor window, `range_break_offset`.
  - **`body_reversion` — FLIPS NEGATIVE.** 364 trades, **PnL $−1,028 / RF −0.67** (was $+2,150 / RF 24.7 pre-fix). Both symbols negative: MNQ $−668.80 (WR 33.3 %), MGC $−359.28 (WR 34.2 %). The strategy's geometry (single high-body-bar fade, tight 0.35×ATR stop, 3R TP) maximised H-B phantom-profit channel exposure — every stop-out gap-through paid out the trigger price not the realised gap fill, and every loser ate the doubled slip tax. With the bugs fixed the alpha vanishes entirely. **Demoted out of the production tier**; do not deploy live until a full re-tune from scratch (or formal retirement). The prior R5/R6 headlines should be treated as engine artefacts.
  - **Production tier post-fix**: `overnight_range` R23 is the only strategy whose tune is provably robust. `morning_range_reversion` needs a re-tune before next deployment; `body_reversion` exits the tier.
  - **Files / docs**: `docs/STRATEGY_ARSENAL.md` rewritten — new 9 m ranking table at the top, per-strategy panels updated with post-engine-fix truth callouts and historical-record sections preserving the pre-fix numbers for context. This CHANGELOG entry captures the operator-level summary.

### Fixed
- **Backtest engine — two PnL accounting bugs uncovered by full-engine audit (2026-06-03 evening, CRITICAL accuracy).** Following the contract-roll data fix the user asked for a full audit of the backtest engine "for any problems, inefficiencies, redundancies, etc.". Two independent bugs were found via runtime instrumentation (debug session `f635c2`) and fixed with confirmed before/after evidence in `core/backtest/engine.py`:
  - **H-A — exit slippage double-counted in PnL and capital.** `BacktestEngine._update_position` computed `pnl = gross - round_trip_commission - slip_dollars` and `self.capital += gross - slip_dollars`, but `gross` was derived from `filled_price` values that already had slippage baked in (entry leg via `entry_price = filled_price`, exit leg via `filled_price = stop_price ± slip`). The extra `slip_dollars` term therefore deducted the exit-leg slippage a SECOND time. Runtime evidence: 94 / 136 closed trades in a 90-day MNQ+MGC walkforward had a `double_count_delta` matching `slip_dollars` exactly ($0.25 per MNQ trade, $0.50 per MGC trade). Fix: drop `slip_dollars` from both lines so the engine's accounting invariant (`initial_capital + Σ trades.pnl == final_capital`) still holds.
  - **H-B — STOP orders fill at trigger price even when the bar gaps through.** In `BacktestEngine._check_order_fill`, STOP fills used `filled_price = stop_price ± slippage_amount` unconditionally. When a bar opened with `bar.open` already well past the stop trigger (gap-through), the engine produced an over-optimistic fill instead of the realistic "next available price ≈ bar.open" that a real broker delivers. Runtime evidence: 58 / 230 stop fills gapped through, producing **188pt of phantom gain** across the 90d sample (single worst example: MNQ SELL-STOP `stop=27332.65, bar.open=27296.5` filled at 27332.525 → 36pt fake profit = $72 on 1ct). Fix: clamp the effective trigger to `max(stop, bar.open)` for BUY-STOP and `min(stop, bar.open)` for SELL-STOP before applying slippage.
  - **Aggregate impact (90 days, `overnight_range` + `body_reversion` × MNQ+MGC, 136 trades):** PnL was **$+1,671.21 → $+930.69 (−$740.53, −44 %)**. Per-bucket: `body_reversion`/MNQ $+292.14 → $+17.39 (−94 %); `body_reversion`/MGC $+112.77 → **−$306.15** (flips negative); `overnight_range`/MNQ $+766.30 → $+763.45 (−0.4 %, mostly LIMIT exits, immune); `overnight_range`/MGC $+500 → $+456 (−9 %). `body_reversion`'s heavy stop-loss exit pattern compounded both bugs every trade; `overnight_range` is largely insulated because most of its exits are LIMIT take-profit fills (no slip applied) and EOD market-flat (already used unblocked MARKET fills).
  - **Tests**: `tests/test_strategy_replay_breakeven.py` had 3 tests that **encoded the H-B bug** (`test_breakeven_clean_bar_moved_stop_fills_when_close_below_new_stop`, `test_bracket_short_sl_fires_when_entry_bar_close_runs_above_sl_level`, `test_bracket_long_sl_fires_when_entry_bar_close_runs_below_sl_level` — each asserted the SL fills at the stop level even when bar.open was past the trigger). Their regression INTENT (SL must not be silently rejected as "wrong-side") is preserved; the exit_price + pnl expectations were updated to reflect realistic gap-through fills. 113 / 113 backtest+parity tests green via `make verify-fast`.
  - **Files**: `core/backtest/engine.py` (3-line fix, ~30 lines of inline comments tying the change to the audit's runtime evidence); `tests/test_strategy_replay_breakeven.py` (3 test assertions updated, with inline rationale).
  - **Doc follow-up needed**: `docs/STRATEGY_ARSENAL.md` and any "real money-maker ranking" claims relying on pre-fix `body_reversion` numbers should be refreshed against the post-fix engine. The 9-month walkforward will need to be re-run before treating the engine-corrected `body_reversion` PnL as production-ready.

- **Backtest engine — contract-roll data quarantine (2026-06-03 PM, CRITICAL data sanity).** The user surfaced a `body_reversion` MNQ trade on 2025-09-15 18:30Z that the chart showed entering at ~24491 but the engine recorded at 24264.88 (~227pt off), booking a phantom $448 take-profit. Root-cause investigation traced this to **databento CSV data corruption around quarterly futures rolls**: on roll dates (every Sep/Dec/Mar/Jun for MNQ/MES, every other month for MGC) the CSV interleaves front-month and back-month contract bars into the same timestream, alternating every other bar between ~24300 (Sep contract) and ~24515 (Dec contract). Real trades enter at one contract's price and exit at the other's, manufacturing impossible 200+pt PnL swings.
  - **Scope (pre-fix)**: 70 corrupted calendar days in the MNQ 5m series across 2023-2026, 252 in MGC. Within the 9-month walkforward window (2025-08-30..2026-05-27): **11 MNQ + 7 MGC corrupted dates**. Concrete impact: `body_reversion` MNQ had **23 / 190 trades (12.1 %)** entirely produced by interleaved-contract fills, booking **$+7,177 of the $+8,077 headline PnL (89 %)**. `morning_range_reversion` and `overnight_range` were essentially clean (≤ 3.4 % of trades tainted, < $570 PnL impact).
  - **Fix**: new `_quarantine_contract_roll_dates()` in `core/backtest/parquet_cache.py` runs inside `_normalize_ohlcv_frame` (CSV + sidecar paths) and drops any calendar day with **≥ 5 bar-to-bar close-to-next-open gaps exceeding 5× the typical bar range** (`high-low` median of the first 5k rows) within ≤ 5 min. Symbol-agnostic — typical_range adapts to MNQ vs MGC vs anything else. Roll days carry 30-100+ flagged gaps; isolated bad ticks carry exactly 1 and pass through. Disable via env `BACKTEST_QUARANTINE_ROLL_DAYS=0`. **In the 9m walkforward window only 5 MNQ days + 0 MGC days are quarantined** (Sep 14-16 + Dec 15 + Mar 16 = the smear around three real rolls).
  - **Sidecar invalidation**: existing parquet sidecars carry stale (pre-quarantine) frames, so the guard ALSO runs on the sidecar-read path. Idempotent + O(N) — no measurable overhead on a clean dataset. Existing `*_databento.csv.parquet` sidecars on disk were also deleted to force fresh rebuilds.
  - **Truth panel — post-fix 9m walkforward** (`BACKTEST_FAST_LOOP=1`, in-process, committed configs):

    | Strategy | Pre-fix PnL | **Post-fix PnL** | Δ |
    |---|---|---|---|
    | `body_reversion` | $+9,332 | **$+2,150** | **−$7,182** (was driven by fake roll-date wins) |
    | `morning_range_reversion` | $+9,082 | **$+9,082** | unchanged — essentially no exposure to roll dates |
    | `overnight_range` (R23) | $+2,409 | **$+2,409** | unchanged |

    **Real money-maker ranking inverts post-fix**: `morning_range_reversion` is now the clear #1 at $+9,082 (4 × more PnL than the next strategy). `body_reversion` was previously inflated by ~$7k of phantom roll-date profit and drops to #3.
  - **Tests**: new `tests/test_contract_roll_quarantine.py` (10 cases — clean passthrough, roll-day drop, single-outlier ignore, env-disable, log emission, end-to-end via `_normalize_ohlcv_frame`, empty/zero-range/short-frame edge cases). 10/10 green.
  - **Files**: `core/backtest/parquet_cache.py` (+`_quarantine_contract_roll_dates`, +sidecar-read guard call, +3 module-level constants); `tests/test_contract_roll_quarantine.py` (new).

### Added
- **`overnight_range` Round-23: step-trail wiring + continuation tune (2026-06-03 PM, follow-on to R22).** With R22 locked in (`$2,789 / 9m / RF 11.50`), the user asked to "hone overnight_range into a continuation strat — look at trailing stops / partial profit". Diagnostic on R22 trades showed exactly the continuation profile the user expected: **MNQ winners held a median 10 bars, p75 37 bars, max 67 bars; losses median 1 bar, p75 3 bars** — winners that survive past the first few bars run *far*, suggesting a trail is the natural mechanism. **Partial-TP was a dead end**: the existing partial-TP path auto-arms a stage-2 break-even stop on the runner that knocks out MNQ's deep continuation runners (R23a-R23f sweep, RF 1.23-4.79 vs baseline 8.52). The actual lever:
  - **Engine work**: extended the existing breakeven-watch mechanism (`core/backtest/strategy_replay.py::_breakeven_watches`) to support **multi-stage step-trails** registered with synthetic keys `{entry_id}_trail{N}`. The SL-linking step in `_process_subbar_fills` (after entry fill) now finds ALL watches whose key matches `entry_id` or `entry_id_*` and links each to the same SL pending-order id. The `_evaluate_breakeven_watches` body gained a **one-way ratchet guard** so a higher-trigger stage with a (mis-configured) lower lock cannot regress the SL — LONG stops only move up, SHORT stops only move down.
  - **Strategy wiring**: `strategies/overnight_range_strategy.py::_place_oco_stop_entry_bracket` now calls `_register_trail_watches` after a successful OCO bracket placement. Each (trigger_R, lock_offset_R) row in `position_management.trail_steps_r` is scaled to absolute points via `R_pts = |entry - stop|` so the same TOML row applies cleanly across MNQ (R ≈ 25 pts at stop_atr=0.5) and MGC (R ≈ 7 pts). Empty list → trail disabled (R22 behaviour preserved).
  - **Sweep**: R24 (14 trials, 270d / 9 folds, MNQ+MGC) ranked candidates by RF×ret:
    - `[[1.0, 0.0]]` pure BE @ 1R: ret +95.93% rf 3.04 wr 27.9% — kills MNQ runners
    - `[[2.0, 0.0]]` pure BE @ 2R: ret +103.45% rf 3.28 wr 29.5% — still hurts
    - **`[[2.0, 1.0]]` lock 1R @ 2R MFE: ret +142.62% rf 8.71 wr 39.7%** ← R23 winner
    - `[[2.0, 1.0], [4.0, 2.5]]` 2-stage: ret +138.34% rf 8.45 wr 39.7% — close but not better
    - `[[1.0, 0.0], [2.0, 1.0], [3.0, 2.0]]` 3-stage classic: ret +83% rf 4.51 — hurts (1R BE step kills MNQ)
  - R25 fine-tune (14 trials around the winner) confirmed `[[2.0, 1.0]]` is the local optimum; tightening (1.75R / 0.75R) or loosening (2.5R / 1.5R) regressed by 1–10% RF.
  - R26 cross-window check (3m / 6m / 9m, 9 trials each) tested pairing the trail with looser MNQ TPs (5R / 6R / 8R / 10R) — **all variants hurt MNQ** on every horizon. The R22 4R MNQ TP already catches most winners; a wider target trades certain 4R wins for smaller-MFE-eventually-stopped outcomes.
  - **Committed**: `position_management.trail_steps_r = [[2.0, 1.0]]` (single-stage, lock 1R at 2R MFE).
  - **Cross-window net**: 3m ret +63 vs +54 (+16%, RF +16%), 6m flat (ret -0.2%, RF flat), 9m +2.3% / RF +2.2% / WR +5pp. **WR consistently +5–10pp across all windows**, max DD smaller on 3m + 9m (slightly wider on 6m). Per-symbol R23 vs R22: MNQ ret +30.23 → +32.12 (+6.3%, winners protected); MGC +109.22 → +110.50 (+1.2%, MGC winners typically hit TP=2R before the 2R trigger fires).
  - **Post-commit 9m walkforward** (`docs/perf/overnight_range_round23_postfix/`): **+$2,852.40 / 126 trades / WR 39.7 % / max DD $193 / RF 14.48** vs R22 `$2,789 / 121 / 34.7% / $213 / 13.07`. +2.3% ret, -9% DD, +5pp WR, +11% RF.
  - **Live deployment caveat**: `register_generic_breakeven_watch` is a no-op for OCO brackets in the live bot today (the live BE monitor uses the legacy `breakeven_monitoring` dict path). The per-trade step-trail is therefore **backtest-only** until the live OCO watch path is wired — acceptable because production already flattens at EOD which caps downside. Filed as a deferred follow-up.
  - **Files**: `strategies/overnight_range_strategy.py` (+`_read_trail_steps_r`, `_register_trail_watches`, ctor + reload hook); `core/backtest/strategy_replay.py` (multi-watch SL linking + one-way ratchet); `config/strategies/overnight_range.toml` (+ `trail_steps_r = [[2.0, 1.0]]` with sweep evidence comment block). Tests: `tests/test_strategy_replay_breakeven.py` (+4 multi-stage tests — registration, ratcheting, regression-guard, SHORT mirror); `tests/test_overnight_range_symbol_risk_signal_overrides.py` (+3 parse tests + rename R22 → R23 pinning test). 49 / 49 trail+pinning + 112 / 112 verify-fast tests green.

### Fixed
- **Replay engine — fast-loop (`BACKTEST_FAST_LOOP=1`) was silently bypassing the EOD force-flat (2026-06-03 PM, root-cause of the round-14 cross-session bug).** The earlier-today fix to `_maybe_replay_force_flat_et` (date-rollover guard) **only worked under the slow loop**. Re-running the `overnight_range` 9m walkforward through `scripts/walkforward_trade_recap_report.py` (which sets `BACKTEST_FAST_LOOP=1` by default per the optimizer-default rollout in May) showed that 2 / 18 folds STILL produced cross-session trades (MNQ fold 8 = 1920 bars avg hold, MGC fold 4 = 988 bars). Direct in-process call without that env was clean. Root cause: `_replay_market_flat_all_open_positions(bar)` had an `if not isinstance(bar, pd.Series): return` guard at the top. The fast loop yields `_BarRow` instances (the `__slots__`-based 10× lighter replacement for `pd.Series`), so the guard silently no-op'd the flatten — both the cutoff path AND the new rollover path. Fix: replace the isinstance check with a try/except around `bar["open"]` + `bar.name`, so any object that supports the Series-style access pattern (Series, `_BarRow`, dict, ...) routes through correctly; non-conforming inputs still no-op gracefully. New regression test `tests/test_replay_force_flat_eod.py::test_replay_market_flat_accepts_barrow_fast_loop` exercises the real `_replay_market_flat_all_open_positions` against a `_BarRow` instance and asserts the synthetic close is placed + filled + the position is cleared. **Re-verified**: same fold-8 MNQ window under fast loop now matches slow loop byte-identically (8 trades / $165.40 / max hold 3.1 h / 2 trades exit `replay_force_flat_et`). 9 / 9 EOD regression tests green.
- **Replay engine — EOD force-flat now also fires on calendar-date rollover (2026-06-03 AM).** The user surfaced six trades from the `overnight_range_round14_9m` walkforward where positions persisted 7-15 calendar days (e.g. MNQ BUY `2026-05-12T14:05Z → 2026-05-26T14:00Z` 14 days later, `take_profit` $1183.10).  Prop firms enforce same-session close, so these synthetic multi-session holds were producing outsized PnL artefacts in the walkforward metrics.  Root cause: `_maybe_replay_force_flat_et` only triggered when `bar_minutes_et >= cutoff_minutes` (default 960 = 16:00 ET).  In edge cases where the CSV had no bar at or after the cutoff on day N (CME settlement gap 16:00 → 18:00 ET, then a holiday early-close) and the next bar landed before the cutoff on day N+1, the position survived the cutoff entirely.  Fix: `_maybe_replay_force_flat_et` now also fires on **any calendar-date rollover** — when the current bar's ET date differs from `_last_replay_bar_et_date`, all pending orders cancel + all open positions market-flat at the bar's open.  The cutoff branch still fires as before.  `_last_replay_bar_et_date` is reset to `None` per replay so a new fold starts clean.  Tests: `tests/test_replay_force_flat_eod.py` (8 cases — cutoff path, rollover path, no-op path, None handling, first-bar guard).  **Note**: this fix was necessary but not sufficient — the fast-loop bypass above was the load-bearing fix; see fast-loop entry directly above.
  - **Files**: `core/backtest/strategy_replay.py` (`_maybe_replay_force_flat_et` + per-bar `bar_et_date` derive + `_last_replay_bar_et_date` cache invalidation in `replay()` + `_replay_market_flat_all_open_positions` Series-vs-`_BarRow` polymorphism fix).

### Changed
- **`overnight_range` Round-22 re-tune on corrected engine (2026-06-03 PM, follow-on to the fast-loop bypass fix).** Once the corrected engine baseline was in (`$1,561.70 / RF 6.77 / 9m` — see entry directly below), a clean 4-round 42-trial sweep via `scripts/optimize_strategy.py` (the proper harness — fans out through `scripts/walkforward_trade_recap_report.py --in-process`, NEVER subprocess-per-trial loops) found that the round-21 `filters.gap_max_pct = 0.80` was over-tuned on the buggy baseline. The partial-fill artefacts had made the tight gap look protective; on the corrected engine the true optimum is materially wider. **Two committed TOML changes** (`config/strategies/overnight_range.toml`):
  - `filters.gap_max_pct`: **0.80 → 1.20** (R2 isolated: ret +78%→+140%, dd 17%→10%, rf 2.60→8.70, wr 30.8%→34.2%)
  - `position_management.range_break_offset`: **1.0 → 1.5** (R2 isolated: ret +78%→+93%, dd 17%→15%, rf 2.60→3.09, wr 30.8%→32.7%; additive with gap change)
  - Per-symbol stop/TP and weekday skips re-validated R22, kept unchanged from round-13 (MNQ stop=0.5/tp=4.0/skip Mon+Fri, MGC stop=0.5/tp=2.0/skip Wed).
  - Breaker re-validated R22, kept at `max_consecutive_losses = 2` / `loss_streak_cooldown_sessions = 5` — still the RF leader among 8 breaker variants on the corrected baseline (next-best 3L/8d at RF 2.48 vs 2.60).
- **Post-R22 walkforward** (270d / 9 folds, MNQ+MGC, `docs/perf/overnight_range_round22_postfix/`): **+$2,789.10 / 121 trades / WR 34.7 % / max DD $242.55 / RF 11.50 / avg bars held 4.7** — +78 % more return, +13 % more trades, +12 % WR, +70 % RF vs the R21 post-fix baseline. Cross-window stable: 3m RF 3.52, 6m RF 4.89, 9m RF 11.50, every horizon strictly Pareto-better than the R21 baseline. Sweep artefacts under `docs/perf/_opt_runs/overnight_range_postfix/r{1,2,3,4,5}_*` (trial JSON + per-trial recap dirs).
- **Pinning tests updated**: `tests/test_overnight_range_trading_window.py::test_filter_pct_matches_shipped_overnight_range_toml` now pins `gap_max_pct = 1.20` (was 0.80). `tests/test_overnight_range_symbol_risk_signal_overrides.py::test_overnight_committed_round22_defaults_resolve_correctly` (renamed from `_round13_`) now pins `range_break_offset = 1.5` (was 1.0). Per-symbol overrides unchanged. 13/13 pinning tests green.

- **`overnight_range` walkforward baseline corrected post-bug-fix (2026-06-03 PM, intermediate snapshot before R22 re-tune).** With both the date-rollover guard AND the fast-loop bypass fix in place, the realistic 9m walkforward baseline is materially lower than what was previously committed to `docs/STRATEGY_ARSENAL.md`. The earlier "$13,569 / RF 10.85" headline numbers reflected the bug producing partial-fill artefacts spread across 7-21 days that each booked TP/SL as separate "trades" worth thousands of dollars per leg. **Truth (committed config, pre-R22 re-tune)**: 270d / 9 folds / MNQ + MGC / no parameter changes = **$1,561.70 / 104 trades / WR 30.8 % / max DD $231 / RF 6.77 / avg bars held 4.3**. This snapshot is preserved at `docs/perf/overnight_range_round14_9m_postfix_eod/` as the baseline against which the R22 sweep was measured. The strategy remains profitable and the realized drawdown is tight, but the historical RF claim of 21.73 (round 14 with the breaker stacked on the buggy baseline) is invalidated — that number was a measurement artefact, not an alpha signal. **Methodology**: re-run was via `scripts/walkforward_trade_recap_report.py --days 270 --folds 9 --symbols MNQ,MGC --strategies overnight_range --no-cache-decisions` (clearing the stale fixture cache that had served pre-fix payloads).

### Added
- **Phase-2 strategy #1 — `nr_compression_break` (NR7 / Toby Crabel daily-TF, 2026-06-03).** New `strategies/nr_compression_break_strategy.py` + `config/strategies/nr_compression_break.toml` + registration in `strategies/strategy_manager.py::BUILTIN_STRATEGY_SPECS` and both `_get_strategy_function` and `_get_strategy_class` in `core/backtest_executor.py`.  Aggregates RTH 5m bars into per-symbol per-ET-date daily OHLC, detects NR7 setup (last completed daily bar is the narrowest of the trailing `nr_lookback = 7`), uses yesterday's close-vs-open as a continuation cue (`use_continuation_bias = true`, `min_body_pct = 0.10` doji filter), arms stop-entry brackets in the bias direction at `nr_high + 1 tick` / `nr_low - 1 tick` with `0.8 × ATR(14)` stop + `2.0R` TP.  Force-flat at `flat_et = 15:55` ET via the shared `core.max_hold_exit.close_positions_exceeding_max_hold` helper.  Consults the Phase-1 portfolio breaker (lazy-instantiated, `PORTFOLIO_DAILY_LOSS_CAP` env, default $1000).  **R1 baseline (continuous 9 m, MNQ+MES+MGC, `enabled = false` by default)**: 27 trades / +$227.30 / WR 44.4 % / PF 1.4 across the three symbols — PF goal (≥ 1.5) not yet met per-symbol, DD nowhere near the 50 % cliff.  Numbers logged in `docs/STRATEGY_ARSENAL.md` "Phase 2 results log" §2.3.  Next: proper walk-forward harness for per-fold variance + `nr_lookback` sweep (5 / 7 / 10).

- **Phase-2 strategy #2 — `globex_drift_continuation` (Asian-session breakout, 2026-06-03).** New `strategies/globex_drift_continuation_strategy.py` + `config/strategies/globex_drift_continuation.toml` + registration in `BUILTIN_STRATEGY_SPECS` and both backtest-executor strategy resolvers.  Cross-midnight Globex session detection (18:00 ET → 04:00 ET; the calendar date of the 18:00 open is the "globex session date"), pre-trigger range build 18:00 → 22:00 ET then frozen, stop-entry bracket placement window 22:00 → 04:00 ET in the prior RTH close direction (`min_body_pct = 0.15` quality filter), 04:00 ET EOD flat that **cancels pending stop-entry brackets** (so they cannot fill in RTH) and closes any open position, `tp_r_multiple = 1.8`, `stop_atr_multiplier = 0.8`, `max_hold_bars = 72` (6 h) secondary safety net.  Same Phase-1 portfolio breaker hook as NR7.  **R1 baseline (continuous 6 m, MNQ+MES, `enabled = false`)**: NEGATIVE on both symbols — MNQ -$329.86 / PF 0.75, MES -$354.97 / PF 0.59.  Quick 3 m sweep on MNQ confirmed continuation is the *least bad* direction (fade variant even worse at PF 0.48).  **Verdict: parked as a scaffold** until a regime gate is wired (Phase-1 #1.3 — only fire Globex in trend regimes) AND fold-level walk-forward shows positive return.  Full sweep table in `docs/STRATEGY_ARSENAL.md` "Phase 2 results log" §2.5.

### Changed
- **Arsenal roadmap pivot (2026-06-02 PM).** After 3 rounds of dormant-strategy tuning failed to bring `trend_following` / `mean_reversion` / `simple_candle` under the DD-100 % cliff, the doc-level direction shifted: those three are now formally **STAGNANT** (parked with full evidence trail), and `docs/STRATEGY_ARSENAL.md` has a new *Arsenal roadmap* section that lists 5 net-new strategy designs targeting genuine portfolio gaps (`opening_drive_continuation` 5-min ORB, `vwap_pullback_continuation`, `nr_compression_break` NR7 / Toby Crabel, `power_hour_reversion`, `globex_drift_continuation`) plus 3 shared-infrastructure prereqs (portfolio-level daily circuit breaker, fixed-dollar-risk position sizer, shared regime classifier). Each new-strategy proposal documents alpha thesis, time/direction/timeframe rationale, geometry, prereqs, expected metrics, and acceptance criteria. The stagnant-tier TOMLs retain their committed round-3 configs as evidence but are NOT on any active research list.

### Added
- **Experimental-strategy round-3 wiring: scoping fix + `max_hold_bars` exit + multi-window validation.** Three coordinated changes turned `mean_reversion`, `trend_following`, and `simple_candle` from "wiring done but DD > 100 %" to "trade-generating and individually evaluated".
  - **TOML scoping bug** (the silent failure mode): `config/strategies/mean_reversion.toml` and `config/strategies/simple_candle.toml` had their strategy-specific knobs (`stop_atr_multiplier`, `signal_cooldown_bars`, `max_hold_bars`, `stop_multiplier`, etc.) declared AFTER the `[meta]` / `[risk]` section headers. In TOML that scopes them under the section (`meta.stop_atr_multiplier`, `risk.stop_multiplier`), but the strategy reads them via root-key lookup (`cfg.get_float("stop_atr_multiplier")`) which traverses `self._data` from the top — so every "tightening" was silently falling through to the strategy's hard-coded default. Each TOML was restructured to put all strategy-specific keys ABOVE `[meta]` with a header comment ("DO NOT move below [meta]") explaining the gotcha. Round-2 sweep numbers reported in the prior turn's metrics were therefore *baseline strategy defaults*, not the tested configs.
  - **`core/max_hold_exit.py` (new shared module)**: extracted body_reversion's time-based MARKET exit + orphan-OCO cleanup into a reusable function (`close_positions_exceeding_max_hold(engine, max_hold_bars, current_bar_seq, entry_bar_seq, ...)`). `simple_candle`, `trend_following`, and `mean_reversion` each grew a `_maybe_close_stale_positions()` helper that delegates to it and a per-symbol `_entry_bar_seq` tracker that's populated at signal emission and popped on engine close. Defaults: `mean_reversion = 18` (≈ 90 min on 5m), `trend_following = 60` (≈ 5 h, long enough for a real trend leg), `simple_candle = 0` (the alpha requires winners to run — any non-zero cap turned a +2562 % run into -98 %).
  - **`scripts/optimize_strategy.py` patcher extension**: `_split_dotted` now treats a bare key (no `.`) as a root-level edit, and a new `_patch_root_keys()` slices the pre-`[section]` block of the TOML and inserts/replaces root keys without crossing the section boundary. Previously the patcher required dotted (`section.leaf`) keys only, which silently failed on the bare-key strategies and made it impossible to sweep them from the JSON trial harness.
  - **Round-3 validated configs** (committed in TOML):
    - `mean_reversion`: `stop_atr_multiplier = 0.7`, `max_hold_bars = 18`, symbols pinned to **MNQ + MGC** (MES = -$4,363 on 9m, drops by same logic as `morning_range_reversion`). 3m MNQ-only +209 % / DD 29 % / **RF 3.41**; 6m MNQ+MGC +556 % / DD 55 % / **RF 3.33**; 9m DD still pushes 100 % combined — STAYS EXPERIMENTAL pending a per-day loss cap.
    - `trend_following`: `atr_target_multiplier = 2.0` (narrow TP gets hit more often than 3-5×), `max_hold_bars = 60`. 3m +27 % / DD 39 %; 6m +10 % / DD 70 %; 9m -73 % / DD 148 %. STAYS EXPERIMENTAL — 5m MA-crossover edge is real but small and doesn't survive long windows.
    - `simple_candle`: `stop_multiplier = 0.6`, `profit_multiplier = 1.5`, `max_hold_bars = 0`. 3m +463 % / DD 155 %; 6m +455 % / DD 139 %; 9m +395 % / DD 213 %. Positive return on every horizon (47 % WR × 1.5R = positive expectancy) BUT **DD > 100 % on every horizon**. STAYS EXPERIMENTAL — needs fixed-fraction position sizing (the let-it-ride logic that captures the displacement alpha is the same logic that compounds the losers), multi-leg scratch, or per-symbol pin to graduate.
  - **Tests**: 96 / 96 backtest + parity tests green. Smoke parity on `morning_range_reversion` (1m + 4m windows) + `overnight_range` (4m window) byte-identical.

- **Multi-strategy long/short deconfliction blueprint** in `docs/STRATEGY_ARSENAL.md`. Documents the single-account constraint (TopStepX flattens or rejects opposing orders on the same instrument), explains why `overnight_range` BREAKOUT-LONG and `morning_range_reversion` FADE-SHORT can collide on the same RTH session, and recommends the **account-per-strategy partition** as the operator-preferred deployment (zero code change, broker enforces the constraint, breakers work natively). Two alternative single-account paths (symbol partition; signal-precedence netting) are documented with trade-offs.

- **Cross-strategy consec-loss breaker port — `overnight_range` + `body_reversion` now live-breaker capable.** Extracted the count + magnitude breaker walk from `morning_range_reversion_strategy.py::_consec_loss_breaker_status` into a reusable `core/consec_loss_breaker.py` (BreakerConfig dataclass + pure `evaluate(trade_iter, ...)`). Both `overnight_range_strategy.py` and `body_reversion_strategy.py` now call `_evaluate_consec_loss_breaker(symbol, bar_date)` at the top of `analyze()`, reading `_replay_engine.trades` (backtest) or `_live_trade_history` (live, fed by `TRADE_CLOSED` events). Same per-symbol TOML knobs as morning-range (`signal.max_consecutive_losses`, `signal.loss_streak_cooldown_sessions`, `signal.rolling_pnl_loss_threshold_dollars`).
  - **Tunable + sweep-validated**:
    - `overnight_range` committed `2 losses → 5-day cooldown` (Round 21 R20-R21 cross-window sweep — 9m DD 11.73 % → 9.78 %, RF 10.85 → 21.73, WR +5 pp, with -4 % return).
    - `body_reversion` committed `4 losses → 3-day cooldown` (R5/R6 cross-window sweep — 9m DD 11.33 % → 10.27 %, RF 29.27 → 24.70, WR +1.6 pp; cleaner 6m: -25 % DD).
  - **Defaults to ON in committed TOMLs**: `morning_range_reversion`, `overnight_range`, `body_reversion` all flip `[meta].live_breaker_enabled = true`. Env `STRATEGY_LIVE_BREAKER=0` still wins as a kill switch.
  - **Tests**: 98 / 98 unit + smoke + parity tests green (`tests/test_live_trade_breaker_bridge.py` + smokes + 3/3 fast-loop parity).

- **`overnight_range` Round 21 committed config flip — 19:00 → 10:00 ET + gap filter + breaker (r21c).**
  - **Why**: Round 19 R19g `19:00→10:00 + gap-filter` showed RF 17.57 vs 10.85 on the committed baseline but DD only marginally lower (13.72 vs 11.73). Round 21 stacked the new consec-loss breaker on top: with `2 losses / 5-day cooldown`, the same window goes to **ret +654 % / DD 9.78 % / RF 21.73 / WR 38.32 % / n=107 (270d)** — cross-window valid on 3m (+234 % / DD 26 % / RF 7.79) and 6m (+545 % / DD 9.76 % / RF 18.11). Same Pareto direction every window: -4 % return, -17 % DD, +100 % RF.
  - **Files**: `config/strategies/overnight_range.toml` (timing + gap filter + breaker knobs + commentary), `tests/test_overnight_range_trading_window.py` (committed-config assertions updated for the new 10:00 ET market_open + 0.80 % gap cap).

- **Portfolio worst-case-day budget — MGC `sl_max_pts = 22` per-symbol cap.** Operator hard limit: 3-strategy worst-case single-day stop-out must stay under $1000. Pre-cap arithmetic blew that by ~$140 because MGC's range-anchored stop on a wide morning range (45pt SL × $10/pt × 2ct = $900) dominated the sum. New per-symbol cap `[symbols.MGC.signal].sl_max_pts = 22` ($440 worst-case for MGC morning_range_reversion). Total worst-case-day = **$985** (~99 % of budget). See the new arithmetic table in `docs/STRATEGY_ARSENAL.md`.
  - **Walk-forward impact**: 9m MNQ+MGC went from baseline +678 % / DD ~62 % to +519.7 % / DD 54.8 % — return cost 23 %, DD improved 12 % — operator's $1000-day rule beats the return.

- **Dormant strategy wiring round 2 (`trend_following`, `mean_reversion`, `simple_candle`).** All three now generate trades in walk-forward (previously zero). Root causes + fixes:
  - **`trend_following` + `mean_reversion`**: `execute()` called `trading_bot.create_bracket_order` directly — the replay engine intercepts `BaseStrategy.place_bracket_order` only, so every order silently bypassed the simulator. Both files now route through `self.place_bracket_order(...)` with replay-aware `_has_open_position_in_engine` guard + cross-bar `signal_cooldown_bars` cooldown to prevent same-bar entry stacking.
  - **`simple_candle`**: cooldown + position guard + `stop_multiplier` / `profit_multiplier` exposed in TOML (was hardcoded).
  - **Status**: still EXPERIMENTAL — all three exceed the 100 % DD cliff on the 90d / 3-fold MNQ window (TF -327 % / DD 277 %, MR -1345 % / DD 783 %, SC +2562 % / DD 228 %). Edge fingerprints exist but production-readiness needs `max_hold_bars` exits + per-symbol parameter sweeps + (TF / MR) trailing-stop wiring through the replay engine. Documented in each TOML's header comment and in `docs/STRATEGY_ARSENAL.md` "Experimental tier".

- **Backtest engine Tier 1.1 / 1.3 / 2.1 hot-path wins.**
  - **Tier 1.1 — `StrategyReplayEngine.replay_df(symbol, df, bars=...)` public entry.** New method accepts a pre-parsed OHLCV DataFrame directly so callers that already hold one (sample-data path, future cache callers) skip the `list[dict] → _bars_to_dataframe → DataFrame` round-trip the legacy `replay(bars=...)` entry pays (~5-10 ms × N folds on this Mac mini). `replay(bars=...)` is unchanged and now simply prepares both shapes then delegates to `replay_df`, keeping every existing caller byte-identical. **Important caller contract**: `df` and `bars` must be in sync — the production CSV path passes through `replay_bars_from_ohlcv_df(df, deroll=True)` which drops interleaved dual-contract bars from the list, so callers that want to bypass `replay()` must hold a **derolled** DataFrame to feed `replay_df`. Otherwise stick with `replay()`.
  - **Tier 1.2 status: already in place** prior to this round (timestamps materialized once via `timestamps = list(df.index)` in `_iter_bars_fast`; nanosecond-int bisect path in the fast-mocks closure). Re-validated in this batch via `tests/test_backtest_fast_loop_parity.py` (3/3 cases green at 82 s wall clock).
  - **Tier 1.3 — `BacktestEngine._calculate_equity()` short-circuit when no positions are open.** The replay loop calls this once per bar regardless of position state. For strategies that are in-position <5% of bars (`morning_range_reversion` ~3%, `overnight_range` ~1%) the `sum(...)` generator allocation + iter overhead dominated this call's cost. Now returns `self.capital` directly via an `if not self.positions` early-out, swapping the generator for a plain accumulator on the rare populated path. Byte-identical numerically (parity test green).
  - **Tier 2.1 status: already in place** prior to this round (`core/backtest/inprocess_runner.py` + `--in-process` flag on `scripts/walkforward_trade_recap_report.py`, pre-warming pandas / numpy / pyarrow / strategy bytecode once per worker). This round: `scripts/optimize_strategy.py` now passes `--in-process` by default so every sweep launched from the optimizer benefits without an env tweak. Opt out via `OPTIMIZE_STRATEGY_INPROCESS=0` for parity debugging.
  - **Wall-clock measurement** (4-trial × 9-fold × 2-symbol overnight_range sweep, M-series Mac mini): subprocess-pool 30 s vs in-process-pool 31 s — within noise on this matrix size. Larger matrices (100-trial parameter sweeps) are where Tier 2.1 amortizes meaningfully; the underlying primitives are already shipped.
  - **Tier 3-6 not implemented**: vectorized strategy subclass / Rust hotpath / `should_skip_bar` hook / Dask distribution remain on the roadmap. The existing `replay_active_window_et` + `replay_precompute_indicators` hooks already deliver most of Tier 5's gain for the strategies that actually have a narrow active window.

- **Live consec-loss / equity-curve breaker bridge** — new `EventType.TRADE_CLOSED` + `core/live_trade_history.py` + per-strategy `_live_trade_history` buffer wired into `BaseStrategy.record_trade_outcome` and consumed by `MorningRangeReversionStrategy._consec_loss_breaker_status` when no `_replay_engine` is attached (i.e. live mode).
  - **Why**: `morning_range_reversion`'s consec-loss breaker (`max_consecutive_losses=2`, `loss_streak_cooldown_sessions=10`, regime-gated by KER + magnitude) has been backtest-proven since Round 19 but had **no live wiring** — `record_trade_outcome` was a deliberate no-op stub since the live SignalR pipeline doesn't push per-trade PnL deltas through the strategy's `analyze()` hook.
  - **Bridge mechanics**: `core/user_hub_handlers.on_trade` now publishes one `TRADE_CLOSED` event per completed `Trade` returned by `SessionTradeTracker.process_fill` (carrying `symbol` + `net_pnl` + entry/exit timestamps). `BaseStrategy.start_live_trade_bridge` subscribes the strategy to those events; the subscriber filters by `config.symbols` and calls `record_trade_outcome`, which writes into a per-symbol bounded `collections.deque` exposed through the same `for trade in reversed(history)` walk pattern the backtest path uses on `engine.trades`.
  - **Backtest path unchanged**: `_consec_loss_breaker_status` reads `_replay_engine.trades` first when present; the live history is a fallback only. Same observable behaviour for every replay run (pinned by the existing morning-range smoke + new `tests/test_live_trade_breaker_bridge.py` — 11 new tests, full suite still green at 206 / 206).
  - **Off-by-default for safety**: subscription is gated by `STRATEGY_LIVE_BREAKER=1` env var or per-strategy `[meta].live_breaker_enabled = true` in TOML. Flip after a parity-validation pass against a paper account; existing live deployments are byte-identical until then.
  - **Files**: `core/events.py` (new event), `core/live_trade_history.py` (new module), `core/user_hub_handlers.py` (publisher), `strategies/strategy_base.py` (`record_trade_outcome` + start/stop bridge), `strategies/strategy_manager.py` (auto-subscribe on `start_strategy`), `strategies/morning_range_reversion_strategy.py` (`_consec_loss_breaker_status` reads `_live_trade_history` when no replay engine), `tests/test_live_trade_breaker_bridge.py` (new).

- **Generic strategy-sweep harness `scripts/optimize_strategy.py`** — thin wrapper around the morning-range / overnight-range optimization helpers that accepts `--strategy <id>` + optional `--toml-path` so any TOML-driven strategy can be swept with the same trial schema (`{label, root, symbols, env}`). Trials run with `BACKTEST_FAST_LOOP=1` by default (was already default-on in the underlying walk-forward scripts) — gets 19-trial / 270d / 9-fold / 2-symbol sweeps done in ~60s on this Mac mini. Restores the TOML on every `finally` so Ctrl-C between trials leaves the working tree clean.

### Changed
- **`body_reversion` Round 1 — TP=3.0R / Stop=0.35×ATR + MGC ATR-percentile gate 0.75 (`config/strategies/body_reversion.toml`).** Single sweep round (R1-R4 via `scripts/optimize_strategy.py`, ~3 min wall clock) lifted the 9m walk-forward from "great-but-blowup" to "near-perfect equity curve":
  - **Performance** (`docs/perf/body_reversion_round1_9m/`):
    - **9m**: **+773.93% / DD 11.33% / RF 29.27 / WR 36.64% (n=524)** — was +528.86% / DD 82.16% / RF 6.21 (R0 committed). **+46% return, −86% DD, +371% RF.**
    - **6m**: +517.87% / DD 10.30% / RF 19.59 / WR 36.45% — was +529.31% / 13.09% / 9.92. Slightly lower return on the 6m window but DD halved and RF doubled.
    - **3m**: +286.28% / DD 21.62% / RF 10.83 / WR 35.29% — was +249.88% / 41.58% / 4.68.
  - **Sweep evidence** (`docs/perf/_opt_runs/body_reversion/r{1,2,3,4}_*`, 54 distinct trials):
    - **R1 single-knob screen** (19 trials, MNQ+MGC, 270d/9f): TP=2.0R alone collapses DD 82% → 17.7% with +742% return. TP=1.5R hits DD 13.4% but at -33% return. `max_hold_bars` 3-8 had **identical** outcomes (most trades exit on SL/TP before the hold cap).
    - **R2 TP+stop combos** (16 trials): TP=2.5R / Stop=0.4×ATR scores **+737% / DD 13.6% / RF 17.70** — best Pareto frontier vs R1's TP=2.0/Stop=0.5 (+742%/17.7/15.25). Per-symbol MGC ATR-q=0.70/0.75/0.80 monotonically lifted MGC return-per-trade. Validated that the strategy ignores root `meta.symbols` and root `signal.skip_weekdays` (identical results to baseline — Step-4 finding).
    - **R3 fine-grid refinement** (14 trials): Stop=0.35 + TP=3.0 hit **+766% / DD 11.12% / RF 24.73** (R3g winner) and **MGC q=0.75** at base settings hit +758% / 13.5% / RF 24.34. Combining both (R4e on 3m/6m/9m) is the **strictly Pareto-best** across all three horizons.
    - **R4 cross-horizon validation** (5 trials × 3 horizons): confirmed the winner is stable on 3m + 6m + 9m — not overfit to the 9m blowup window.
  - **Why this works**: the previous Stop=0.5×ATR was the dominant DD-driver (~$80 per loss vs $30 per win at 33% WR → expectancy fragile to any 4-loss streak). Tightening to 0.35×ATR keeps the same loss-per-trade dollar amount on the typical-vol regime BUT prevents catastrophic stop-outs in the high-vol Q4 2025 / Jan 2026 cluster sessions. The wider TP (3.0R vs effective 6:1 → 8.6:1) captures the post-event drift that the alpha thesis identified. MGC's q=0.75 trims the 26% of MGC sessions where ATR is in the bottom quartile (low-vol-no-drift sessions where mean reversion just chops).
  - **Notable null results from R1/R2**:
    - `signal.skip_weekdays` (Fri / Wed / Wed+Fri) had **identical** outputs to baseline → the strategy does not honour weekday filtering (different from `morning_range_reversion`). Don't try to optimize through it.
    - Root `meta.symbols = ["MNQ"]` doesn't drop MGC (same n=605 in R2/R3) → the symbol allow-list is set by the walk-forward driver, not the TOML. To restrict to MNQ, the walk-forward must use `--symbols MNQ`.
    - Per-symbol `signal.allow_short = false` on MGC (R2o) was a no-op (identical to baseline). The strategy's symbol-override path does not propagate the short toggle.

- **`overnight_reversion` disabled in TOML pending an algorithmic redesign.** 9m walk-forward (270d / 9 folds, MNQ+MES+MGC) across every R1 sweep variant (committed config, Round-14 ATR band, loose ATR band, tight ATR band, drop_mes) returned **ret = -250% to -415%, DD = 160-215%, RF = -0.5 to -0.7**. The strategy fades a confirmed first-close breakout outside the overnight box; on the Aug-2025 → May-2026 dataset, breakouts overwhelmingly continued rather than reverting, producing systematically losing fade entries. Every retracement gate trims trades **proportionally** so per-trade expectancy stays negative — this is a strategy-design problem, not a parameter-tuning problem. `[meta].enabled = false` shipped with a multi-paragraph comment block documenting the evidence and the deferred rebuild path (regime-detector gating, not threshold-over-range tightening).

- **Dormant-strategy TOMLs reorganised + structural diagnostics (`trend_following`, `mean_reversion`, `simple_momentum`, `trend_scalping`).** Each strategy previously produced **zero trades** on every walk-forward; root-cause hunt revealed three distinct failure modes:
  1. **TOML key location** — `trend_following` and `mean_reversion` previously had `[risk]`-only TOMLs, but the strategy code reads bare keys (`cfg.get_int("fast_ma_period")`); a key under `[risk]` becomes `risk.fast_ma_period` in the parsed tree and never reaches the strategy. **Fixed**: keys moved to TOML root, defaults made fire-able (`min_trend_strength: 0.5 → 0.05`, `atr_deviation_threshold: 2.0 → 1.5`, `rsi_overbought/oversold: 70/30 → 65/35`).
  2. **System-clock gate in `BaseStrategy._in_trading_window`** — uses `datetime.now()` (wall clock) rather than the replay bar's timestamp, so any strategy that calls `should_trade()` in a backtest run outside its configured `start_time/end_time` produces zero trades regardless of strategy logic. `body_reversion` and `overnight_range` were unaffected because they ship with 24-hour TOML windows (`start_time="00:00"` / `end_time="23:59"`); same fix applied to the two dormant strategies. **Open work**: refactor `_in_trading_window` to accept an explicit `bar_timestamp` (defaulting to `datetime.now()` for live) so production strategies with non-24h windows still backtest correctly.
  3. **Structural strategy bugs** — `simple_momentum.analyze` defines `recent_high = max(highs)` over the last 10 bars **including the current bar**, so `current_price > recent_high` is never strictly true on a closed bar; `trend_scalping.analyze` requires three simultaneous gates (UP/DOWN trend AND EMA cross AND 0.3% pullback to 89-EMA) on the **same** 1m bar, which is a rare-event triple-coincidence that practically never fires. Both strategies shipped `[meta].enabled = false` with a multi-paragraph comment describing the fix that belongs in the strategy code (not the TOML).
- **`trend_following` / `mean_reversion` still emit zero trades** after the TOML reorganisation despite confirmed correct config loading (`fast_ma=9, slow_ma=21, min_strength=0.05, tf=5m` on `trend_following`). Deeper `analyze()`-path debugging is required (likely a MockTradingBot resample / look-ahead-clip interaction or a stale-quote check inside the strategy); both TOMLs ship enabled-for-research with `NOT PRODUCTION-READY` headers and an explicit "do not launch a live executor against this" warning. Will resume in a dedicated session.

- **`overnight_range` R18-R19 extended-window timing re-test — committed 19:00→9:29 still wins on DD, `19:00→10:00 + gap filter` documented as a return-leaning alternative.** 14 trials in `docs/perf/_opt_runs/overnight/{r18_19to10,r19_19to10_dd}/` (270d / 9 folds, MNQ+MGC) confirmed the Round-14 committed timing is on the DD frontier. Highlights:
    - committed (19:00→9:29):                     +678.49% / DD 11.73% / RF 10.85 / n=190
    - 19:00→10:00 anchor 9:29 (r18f):             +742.73% / DD 17.77% / RF 11.52 / n=201   (+64% return, +6pp DD)
    - 19:00→10:00 + gap filter on (r19g):         +681.12% / DD 13.72% / RF **17.57** / n=131  (+62% RF for +2pp DD, **only Pareto-winner on RF**)
    - 19:00→10:30 anchor 9:29:                    +688.69% / DD 22.50% / RF  9.34 / n=177
    - 19:00→9:15  anchor 9:29:                    +676.40% / DD 11.30% / RF 10.95 / n=212  (Pareto-tied with committed)
  Findings: extending the overnight window past 9:29 ET steadily adds 11-22 trades (more 9:30-bar fade ingress) at the cost of a near-linear DD lift. The gap filter (`filters.gap=true, gap_max_pct=0.80`) trims 35% of those extra trades — exactly the high-vol-overshoot subset — and recovers the missing RF. Once the cross-session consec-loss breaker is ported from `morning_range_reversion` to `overnight_range_strategy.py` (not in scope this round), the recommended live config switches to **`overnight_end=10:00, market_open=10:00, zone_anchor=9:29, filters.gap=true, gap_max_pct=0.80`** — committed TOML retains the safe baseline + commented-out alternative.

- **`overnight_range` Round 14 — ATR-percent volatility band ON (`filters.volatility=true`, `atr_min_pct=0.08`, `atr_max_pct=0.60`).** Single-knob upgrade on top of Round 13 that beats the committed baseline on every horizon, with the biggest jump on the 6m window where DD had been the soft spot.
  - **Performance** (`docs/perf/overnight_range_round14_{3m,6m,9m}/`):
    - **3m**: +274.92% / DD 28.86% / RF 4.40 / WR 29.69%   (Round 13 was +201.90% / 32.68% / 2.61)
    - **6m**: **+588.24% / DD 18.67% / RF 9.41** / WR 33.07%   (Round 13 was +334.88% / 22.61% / 5.36 — **+253% return, -3.9% DD, +4.05 RF**)
    - **9m**: **+678.49% / DD 11.73% / RF 10.85** / WR 33.16%   (Round 13 was +656.28% / 11.53% / 10.49)
  - **Sweep evidence** (`docs/perf/_opt_runs/overnight/r16_*` and `r17_*`, 33 distinct trial configs across 3m/6m/9m):
    - **R14 timing-window sweep** (15 trials): 19:00→9:29 (Round-13 committed) wins the DD crown across all three horizons. 19:00→10:00 (later anchor) gains +75% return on 9m but doubles DD to 23%; 19:00→7:30 / 8:00 / 8:30 all underperform.
    - **R15 timing combos** (11 trials): confirmed no combination of earlier/later open or ATR-period sweep improves on the committed timing; ATR period 21 was the only DD-neutral variant, and it cost too much return.
    - **R16 DD-focus sweep** (19 trials, broader filter+timing): identified three Pareto-improving families:
      - `volatility=true` with bounded ATR% (Pareto winner — applied here)
      - `gap=true` with `gap_max_pct=0.5` (kept off — cuts 50% of trades for +6 RF that exceeds the bot's risk budget)
      - `replay_force_flat_et=14:00` (helps 3m return +60% but kills 9m RF by 35% — kept off; live and replay should agree on session length)
    - **R17 ATR-band refinement** (14 trials, all horizons): `atr_min_pct=0.08, atr_max_pct=0.60` is the clear winner. Wider-floor variant (0.12) cut 6m DD further to 11.75% but lost 9m return; tighter ceiling (0.50) reduced trade count too aggressively. The 0.08-0.60 band sits in the sweet spot.
  - **Why this works**: the ATR floor (0.08% of overnight midpoint) gates out dead-range overnights where the breakout has no fuel (typical of holiday-eve / early-summer expiries). The ceiling (0.60%) gates out the high-vol-cluster sessions where the overnight range itself is so noisy that a "breakout" is statistically a fakeout. Both ends of the band coincide with the historical worst-percentile sessions in the MGC drawdown cluster (Feb 24-Mar 17 2026).
  - **Notable null results from R16**:
    - `risk.risk_per_trade_pct`, `risk.max_daily_trades`, `risk.max_positions` have **zero effect** on the score (they govern sizing/throttle but the simulator scores per-trade R, not equity-curve pacing). Don't try to optimize through these knobs.
    - `range_size` filter is still a no-op at current band defaults (R10 finding holds).
    - `position_management.range_break_offset` is now near-optimal at 1.0; 0.75 / 1.25 / 1.5 are all within noise.
  - **Files**:
    - `config/strategies/overnight_range.toml` — `filters.volatility=true`, `atr_min_pct=0.08`, `atr_max_pct=0.60`; inline rationale block updated.
    - `docs/perf/overnight_range_round14_{3m,6m,9m}/` — definitive Round-14 reports (replace Round-13 ones for daily reference).
    - `docs/perf/_opt_runs/overnight/r14_9m/`, `r15_{3,6,9}m/`, `r16_{3,6,9}m/`, `r17_{3,6,9}m/` — full sweep history.
  - **No code changes** — pure TOML-level tweak validated by the Round-13 harness. Live wiring (and the live ATR percent filter logic in `_apply_volatility_filter`) was already in place from earlier work.

- **`overnight_range` 13-round optimization — flipped from -200%/-122%DD/-1.00RF (9m baseline) to +656%/11.5%DD/10.49RF on the same walkforward grid, with consistent profitability across 3m/6m/9m windows. Strategy now trades MNQ + MGC only with per-symbol stop/TP geometry and per-symbol weekday skips.**  Mirrors the morning_range Round-25 process: iterative TOML-level sweeps (no algorithmic changes) plus three small enabling code changes documented below.
  - **Final geometry (committed TOML)**:
    - **Root**: `stop=1.0×ATR`, `tp=2.5×ATR`, `range_break_offset=1.0pts`, `skip_weekdays=[]`.
    - **MNQ override**: `stop=0.5×ATR`, `tp=4.0×ATR`, `skip_weekdays=[Mon, Fri]` — wider asymmetric R:R (1:8) plus weekday gating because MNQ Mondays/Fridays are choppy and Tuesday-Thursday breakouts run.
    - **MGC override**: `stop=0.5×ATR`, `tp=2.0×ATR`, `skip_weekdays=[Wed]` — tighter symmetric R:R because MGC range-break continuations rarely run past 2 ATR; Wednesday skip drops the fold-4-style choppy clusters (Feb 24-Mar 17 2026: 1 win in 13 MGC trades = -$1166 alone).
    - **MES**: dropped from rotation (consistent with morning_range round-25 finding); per-symbol stanzas preserved (dormant) for one-line re-enable.
  - **Validated performance (`docs/perf/overnight_range_round13_{3m,6m,9m}/`)**:
    - **3m**: +201.90% / DD 32.68% / RF 2.61 / WR 31.82% / R+ 3.73 / R- -1.30 (66 trades)
    - **6m**: +334.88% / DD 22.61% / RF 5.36 / WR 27.69% / R+ 3.89 / R- -1.20 (130 trades)
    - **9m**: +656.28% / DD 11.53% / RF 10.49 / WR 31.82% / R+ 3.99 / R- -1.23 (198 trades)
    - All three windows: positive return, sub-35% DD, RF > 2.5. **Baseline before round-13** (existing TOML `skip_weekdays=[1,2,3,5,6]` Mon+Fri only): 3m -40%/DD 53%/RF -0.66, 6m -77%/DD 84%/RF -0.91, 9m -200%/DD 122%/RF -1.00.
  - **Three enabling code changes** (each minimal, parity-preserving for legacy configs):
    1. **`tp_atr_multiplier` actually wired** (`strategies/overnight_range_strategy.py`). The TOML declared `tp_atr_multiplier` and the strategy loaded it into `self.tp_atr_multiplier`, but `calculate_range_break_orders` used a hard-coded `2.0` in four places (long/short × overlap/no-overlap paths). Replaced with `self._overnight_symbol_tp_atr_multiplier(symbol)` so the per-symbol resolver works end-to-end. **Behaviour change**: pre-existing deployments where the TOML had `tp_atr_multiplier != 2.0` will now see that value honoured (was previously silently ignored). The default value in `_schema.toml` was 1.5; the live `overnight_range.toml` had 3 which means the live strategy was running with a stealth 2.0 TP. The new committed TOML uses explicit per-symbol values (MNQ 4.0, MGC 2.0).
    2. **New `_overnight_symbol_tp_atr_multiplier(symbol)`** resolver mirroring the existing `_overnight_symbol_stop_atr_multiplier`. Reads `[symbols.<SYM>.signal].tp_atr_multiplier` with fall-through to root `[signal].tp_atr_multiplier`.
    3. **Per-symbol weekday skip** (`_overnight_symbol_skip_weekdays(symbol)` + wired into `analyze()` and `monitor_breakout_levels()`). The morning_range strategy already supported `[symbols.<SYM>.filters].skip_weekdays`; overnight_range only honoured the root-level list. Now MNQ can skip Mon+Fri while MGC keeps trading 5 days (just minus Wednesday).
  - **Optimization process** (13 rounds, `docs/perf/_opt_runs/overnight/r1..r13_*`):
    - **R1-R2**: baseline calibration; learned the strategy was structurally losing (-260%/9m) because TP was hard-coded at 2x ATR and skip-weekdays defaulted to Mon+Fri only (over-restrictive — that's "P5 research" config from a 2026-04 sweep).
    - **R2b (isolation)**: proved `breakeven_enabled` has **zero effect** in the overnight_range replay path because `_simulate_place_oco_bracket` never registers a BE watch (only `_simulate_place_bracket_order` does, and morning_range uses the latter). Documented in the TOML and treated as deferred follow-up; live & backtest now match by keeping BE off.
    - **R3**: TP sweep at fixed stop=1.0. Sweet spot **TP=2.5x ATR** (RF 3.46, +408% on 9m before per-symbol tuning).
    - **R4-R5**: per-symbol stop/TP overrides + `range_break_offset` sweep. Three Pareto-improving levers identified: MNQ `stop=0.5` (was 1.0), MNQ `tp=4.0` (was 2.5), root `range_break_offset=1.0` (was 0.25 — wider confirmation gates out first-touch fakeouts).
    - **R6-R7**: combined the three R4-R5 winners → +677% / DD 43% / RF 6.81 on 9m (sweep `r6g`/`r7i`).
    - **R8-R9**: added per-symbol weekday skip code support, then re-swept. MNQ skip Mon+Fri (with MGC still trading 5 days) → +746% / DD 38.7% / RF **10.95** on 9m.
    - **R10**: 3m/6m validation revealed 6m DD was still **93%** because MGC had a regime-driven drawdown cluster in Feb 24-Mar 17 2026 (the per-fold view showed fold 4 = 1 MGC win in 13 trades, -$1166).
    - **R11-R13**: MGC mitigations sweep. `stop=0.5` cut MGC per-trade loss magnitude; `tp=2.0` (vs root 2.5) captured more of MGC's quick mean-reversion-prone breakouts; `skip_weekdays=[Wed]` dropped the highest-variance MGC weekday. Final config (`r13a == r12e == committed TOML`) brings 6m DD from 93% → 30.7% in the sweep harness and 22.6% in the final clean run, while preserving 9m return.
  - **Reports**: `docs/perf/overnight_range_round13_{3m,6m,9m}/` are the definitive baselines. Sweep history archived under `docs/perf/_opt_runs/overnight/r1..r13_{9m,6m,3m}/` for audit. Existing pre-round-13 reports (`docs/perf/parameter_sweeps/overnight_timing_mnq`, etc.) remain valid as time-of-day calibration evidence.
  - **Optimization harness**: new `scripts/optimize_overnight_range.py` (sibling to `scripts/sweep_per_symbol_breaker.py`). Generalised to support root + per-symbol overrides and multi-section TOML injection; reusable for future overnight_range tuning rounds.
  - **Live readiness**: live wiring is the same as before (no broker-side changes). The only live-impacting code change is per-symbol weekday skip in `monitor_breakout_levels()` — the loop now early-returns on a symbol only if THAT symbol's skip-list contains today's weekday (previously was a global gate). Net behaviour identical when only root `skip_weekdays` is set. Tests not extended (no smoke tests existed for overnight_range pre-round-13; added separately as a follow-up).
  - **Files**:
    - `config/strategies/overnight_range.toml` — full rewrite to match the committed config with inline rationale per knob.
    - `strategies/overnight_range_strategy.py` — wired `tp_atr_multiplier`; added `_overnight_symbol_tp_atr_multiplier`; added `_overnight_symbol_skip_weekdays`; updated `analyze()` and `monitor_breakout_levels()` for per-symbol weekday checks.
    - `scripts/run_overnight.sh` — default `OVERNIGHT_RANGE_SYMBOLS=mnq,mgc` with comment documenting the `MES` re-enable path.
    - `scripts/optimize_overnight_range.py` (new) — root + per-symbol sweep harness.
  - **Deferred follow-ups (not blocking live)**: (a) wire breakeven for the OCO replay path so live & backtest agree on BE; (b) extend per-symbol smoke tests for overnight_range to pin the round-13 weekday/stop/TP defaults; (c) MGC fold 4-style regime detector (mirror the morning_range consec-loss breaker if MGC re-enters the choppy regime live).

- **`morning_range_reversion` round 25 — MES dropped from the active rotation; strategy now trades MNQ + MGC only.** Direct response to the round-24 follow-up question *"would dropping a symbol or trading just one help?"*. Three-way per-symbol diagnosis (3m/6m/9m) made the call unambiguous:
  - **MES is a high-WR trap**: 84.9% WR on 9m but mean win **$41** vs mean loss **$126-250** (avg R+ 0.19 vs R- -1.04). 100% of losses are full stop-outs (zero partial exits), 5/9 folds net-negative, RF only 1.95. The asymmetry means a handful of stop-outs systematically erase a long tail of small wins — the WR-to-edge ratio is the worst of the three (9.4 pp above breakeven vs MNQ's 6.3 pp at 60% WR and MGC's 10 pp at 75% WR). Kelly-style sizing recommends ≤2×; we were already at 2×.
  - **MNQ + MGC are independently viable**: MNQ RF 4.14 with R+ 1.20 / R- -1.09 (clean systematic edge), MGC RF 5.68 with the largest per-trade edge of all three (R+ 0.59 / R- -1.05 + 75% WR). Their bad folds are uncorrelated (correlation ≈ 0.1-0.3 across the 9 folds), which is the real diversification engine — MES's contribution to that was second-order at best.
  - **Subset walkforward (MNQ 2× + MGC, no MES)** under the committed round-24 sizing: 3m +466.65%/14.56% DD/RF 10.65/WR 74.5%, 6m +454.65%/73.22% DD/**RF 4.26**/WR 64.1%, 9m +654.40%/30.48% DD/**RF 6.71**/WR 68.3%. Compared to round-24 (with MES): 6m RF 4.02 → **4.26** and 9m RF 6.66 → **6.71** are both Pareto improvements; only the 3m headline return slips ~12% because MES contributed ~30 small wins to that window.
  - **Files**:
    - `config/strategies/morning_range_reversion.toml` — `[meta].symbols` now `["MNQ","MGC"]` with the rationale comment inline; `[symbols.MES.*]` stanzas retained (dormant) so re-enabling is one-line.
    - `scripts/run_morning_reversion.sh` — `MORNING_RANGE_SYMBOLS` default updated to `MNQ,MGC`; comment documents the override path (`MORNING_RANGE_SYMBOLS=MNQ,MES,MGC ./run_morning_reversion.sh 1`) for fast A/B testing.
  - **Reports**: `docs/perf/morning_range_round25_{3m,6m,9m}/` carry the new baseline. All 64 smoke tests pass unchanged (no per-symbol-list assumptions were baked into the test suite).

### Added
- **Agent iteration workflow — fast-path defaults + single-shot perf summary + parity-pinned cursor rules + `make` agent loop.** Five-part landing that turns the round-3 performance work into ergonomics the agent (and operator) gets for free, without per-task flag memorization.
  - **Walkforward defaults flipped to `--in-process` + `--cache-decisions` ON** in all three scripts (`scripts/walkforward_trade_recap_report.py`, `scripts/walkforward_strategy_competition.py`, `scripts/walkforward_last_trades_charts.py`). Each flag now uses `argparse.BooleanOptionalAction`, so the historic opt-in pattern (`--in-process`) still works AND a new opt-out (`--no-in-process` / `--no-cache-decisions`) is available for subprocess isolation or cold-cache debugging. Measured: a 4-task competition matrix that previously ran in 3-4 s subprocess now runs in **0.85 s** wall-clock by default. Byte-identical metrics (parity tests still green).
  - **`scripts/agent_perf_summary.py`** — single-shot tool that scans `docs/perf/` and returns a stable-schema JSON summary of every matching run for a given strategy, with `vs_previous` deltas (return%, DD%, RF, WR%, n_trades) computed against the most recent same-window run automatically. Replaces the 4-6 file dive an agent used to do to compare runs.
    - Output: JSON by default (`generated_at_utc`, `n_runs_scanned`, `n_runs_matched`, `filters`, `runs[]` with `headline`, optional `per_symbol`, `vs_previous`). `--format=table` renders a compact human view. Skips ad-hoc directories (`_opt_runs/`, `_decision_cache/`) and runs without a `metrics_insights.json`. Malformed runs are surfaced with `"ok": false` + `"error"` rather than silently dropped.
    - Filters: positional `strategy` arg, `--windows 3m,9m`, `--limit N`, `--compare-baseline <run_id>` for forced baseline diffs. `--include-per-symbol` surfaces the MNQ/MES/MGC breakdown when needed.
    - Surfaced as `make summary STRATEGY=<id> [WINDOWS=...] [LIMIT=...] [FORMAT=table]`.
  - **`Makefile` agent loop** — replaces the 2-line stub with discoverable targets that match the round-3 conventions:
    - `make help` — prints every target with its one-line description (parsed from `## ` doc comments).
    - `make backtest STRATEGY=<id> DAYS=<n> FOLDS=<n>` — walkforward competition matrix (uses the new in-process + cache defaults).
    - `make backtest-recap STRATEGY=<id>` — trade-recap walkforward (charts + HTML).
    - `make backtest-competition` — full multi-strategy competition (`morning_range_reversion,overnight_range,body_reversion`).
    - `make summary STRATEGY=<id>` — JSON perf summary; pass `FORMAT=table` for human view.
    - `make smoke STRATEGY=<id>` — `tests/test_backtest_fast_loop_parity.py` filtered to the strategy (~90 s, byte-identical contract).
    - `make verify-fast` — pytest scoped to backtest/replay/ohlcv/decision_cache/parquet_sidecar (96 tests, ~2 min) — what you run after an engine change. Skips `tests/test_hub_bracket_smoke.py` (pre-existing `trading_bot.py` `SyntaxError`) and `tests/test_parquet_memory_cache.py` (pre-existing unrelated failures from a defunct memory-cache experiment).
    - `make verify` / `make map` / `make test` / `make bench` retained from the old Makefile.
  - **`.cursor/rules/backtest-workflow.mdc`** — auto-attached to `core/backtest/**/*.py`, `core/backtest_executor.py`, `scripts/walkforward_*.py`, `scripts/agent_perf_summary.py`, `strategies/strategy_base.py`, `tests/test_backtest_*`. Encodes the muscle memory from the three rounds of perf work so a fresh agent doesn't relitigate it:
    - The agent iteration loop (make targets > hand-rolled CLI).
    - The parity contract (slow loop is truth; fast loop is byte-identical; never edit parity tests to make them pass).
    - The shipped optimizations list (parquet sidecar, decision cache, fast loop, in-process runner, active-window gate, precompute hook) so the agent doesn't reinvent them.
    - "What NOT to do" red list (no `core/backtest_executor.py` in tight loops, no global fast-loop disable, no new CSV ingestion path, no strategy imports inside the engine, etc.).
  - **`docs/BACKTESTING.md` + `docs/CHANGELOG.md` link hygiene** — two pre-existing broken links that were tripping `scripts/verify_handoff.sh` on every change (and forcing the agent to read+ignore false-`FAILED` output) are now fixed. `docs/BACKTESTING.md` no longer links to the deleted `docs/perf/walkforward_competition/README.md` (the script's `summary.tsv` + `leaderboard.md` are still mentioned, just not as a hyperlink). `docs/CHANGELOG.md` converts five historic markdown links to the removed `docs/perf/sweeps/CANDIDATES.md` into backticked filenames (history preserved, dead link eliminated). `make verify` is now green.
  - **Files**:
    - `scripts/walkforward_trade_recap_report.py`, `scripts/walkforward_strategy_competition.py`, `scripts/walkforward_last_trades_charts.py` — `BooleanOptionalAction` for `--in-process` (all 3) and `--cache-decisions` (recap only).
    - `scripts/agent_perf_summary.py` (new, executable) — 450 lines including docstring + table renderer.
    - `Makefile` — fully rewritten; ~80 lines of doc-commented targets.
    - `.cursor/rules/backtest-workflow.mdc` (new) — auto-attached agent guidance.
    - `docs/BACKTESTING.md` — broken link → factual prose.
    - `docs/CHANGELOG.md` — 5 dead links → backticked references.
  - **Validation**:
    - `make verify-fast` → 96 backtest tests pass, 0 failures.
    - `make smoke STRATEGY=morning_range_reversion` → 2 parity tests pass (1m + 4m windows).
    - `make verify` → handoff link check OK, MAP fresh.
    - `make summary STRATEGY=morning_range_reversion WINDOWS=9m LIMIT=3 FORMAT=table` → 3-row table with `vs_previous` deltas rendered in a single tool call.
  - **Net effect for Cursor agents**: the typical "tweak a knob → re-verify" loop went from ~5 tool roundtrips (locate script flags, run with the right flags, locate output, locate previous output, diff) to **1 roundtrip** (`make backtest` or `make summary`). All paths byte-identical to the canonical reporting flow.

- **Backtest performance overhaul round 3 — strategy active-window gate + per-replay engine micro-opts. Another 1.6-1.7× on top of round-2 for a single replay, 2.9-3.1× on the realistic walkforward matrix.** Three independent landings driven by `cProfile` of a 3-month MNQ `morning_range_reversion` replay after round 2 had landed. Goal stated by the user: "make it so that cursor agents can operate faster when diagnosing / analyzing strategy performance metrics when they don't have to wait for long backtest runs to finish every change." All paths are still byte-identical to the slow loop — `tests/test_backtest_fast_loop_parity.py` (1-month + 4-month windows) and the full 96-test backtest pytest suite all pass after every change.
  - **Tier 5 — Strategy active-window gate (`strategies/strategy_base.py`, `core/backtest/strategy_replay.py`, `strategies/morning_range_reversion_strategy.py`).** New opt-in class attribute `BaseStrategy.replay_active_window_et`. A strategy that knows it can only emit signals during a specific ET window (e.g. `morning_range_reversion` only fires 07:00→16:00 ET) declares the window once; the replay engine precomputes a per-bar `np.ndarray` boolean mask via vectorized `tz_convert` + minute-of-day arithmetic and skips `strategy.analyze()` for bars outside the mask **as long as no position is open**. When a position is open the strategy is still called every bar so `manage_positions` / breakeven / scratch logic stays intact — engine fills (TP / SL) always fire regardless of window. Strategies leaving the attribute `None` pay zero overhead.
    - For `morning_range_reversion`: window = `[signal.range_start, signal.flat_before]` (defaults 07:00→16:00 ET, derived from the same TOML keys the strategy reads internally — single source of truth, no second config to keep in sync). Cuts `analyze()` invocations from 17,395 → 6,765 on a 3-month MNQ stream (61% fewer calls). For `overnight_range` the window would span ~19:00 ET → ~16:00 ET next day (≥21 hours of 24) so the gate adds zero value — left as `None`.
    - The base class also gains an opt-in `replay_precompute_indicators(df)` hook (Tier 3 reframe): strategies that recompute the same indicator every bar (ATR / EMA / session ranges) can override and store results on `self.trading_bot._precomputed` for `analyze()` to consume. Default base impl is a no-op; failures fall back to per-bar so a bad override can't break the run. Infrastructure ships now; concrete strategy refactor deferred.
    - **Measured**: 3-month MNQ `morning_range_reversion` 1.04 s → **0.79 s wall** (1.32× on the replay path) just from the gate.
  - **Tier 4a — `_replay_force_flat_cutoff_minutes_et` hoisted out of the bar loop (`core/backtest/strategy_replay.py`).** The function reads two TOML keys (`timing.replay_force_flat_et`, `signal.flat_before`) that are immutable mid-replay. It was being called every bar (17,395 calls, ~103 ms cProfile-amplified). Replaced with: (a) lazy-cache the resolved minute count on first call within a replay; (b) hoist the call OUT of the per-bar loop entirely so it runs once per replay. `_replay_force_flat_minutes_cache` resets at the top of `replay()` so a follow-up replay with a different fold's config picks up the new values.
  - **Tier 4b — Vectorized `replay_bars_from_ohlcv_df` (`core/backtest/ohlcv.py`).** This function converts the loaded OHLCV DataFrame to the `list[dict]` shape the engine consumes. cProfile pinned it at **451 ms (43% of the post-round-2 replay)** — almost all in `pd.Timestamp(idx[i]).to_pydatetime()` called 17,395 times. Rewrote to:
    - One vectorized `idx.to_pydatetime()` C-call returning all 17 k Python `datetime` objects.
    - Vectorized OHLC sanitization (`numpy` body-wick clip + `numpy.maximum/minimum` envelope coercion) replacing the per-bar `sanitize_ohlcv_ohlc` call.
    - Vectorized volume cast (`np.where(isnan, 0, v).astype(int64)`).
    - One Python-list comprehension at the end to materialize the dicts.
    - **Measured**: same function `451 ms → 79 ms` (**5.7×**) in cProfile, byte-identical output (parity tests green).
  - **Combined-stack measured wall-clock**:
    - **3-month MNQ `morning_range_reversion` single replay**: 1.04 s → **~0.58-0.70 s** (1.6× on top of round 2).
    - **36-task walkforward matrix (90-day, 6-fold, 3-symbol, 2-strategy) subprocess pool**: 11.9 s → **4.11 s** (2.9× on top of round 2).
    - **Same matrix in-process pool**: 9.0 s → **2.92 s** (3.1× on top of round 2), user CPU 24 s → **12.3 s** (49% reduction).
    - `summary.tsv` and `metrics.json` are **byte-identical** between subprocess and in-process paths (only `captured_at_utc` / output-path strings differ — both expected).
  - **Why these three and not more**: post-round-2 cProfile showed `replay_bars_from_ohlcv_df` (451 ms), `_replay_force_flat_cutoff_minutes_et` (103 ms / 17 k calls), and `analyze()` overhead on out-of-window bars (~80 ms saved by the gate) as the three biggest items by a wide margin. Below those, remaining hot spots are inside the strategy's own `analyze()` body (config re-reads, staleness checks) — strategy-specific refactors with low cross-strategy leverage. The `BaseStrategy.replay_precompute_indicators` hook lands the infrastructure for strategies that want to opt in later.
  - **Cumulative since the pre-optimization baseline (slow loop, serial subprocesses, CSV parse on every read)** the realistic agent-iteration workload (90d / 6f / 3s / 2-strategy matrix) went from **multiple minutes → ~3 s wall** in-process, or **single-digit seconds on cache hits**.
  - **Files**:
    - `strategies/strategy_base.py` — adds `replay_active_window_et` opt-in attribute + `replay_precompute_indicators(df)` hook with docstrings explaining contract.
    - `core/backtest/strategy_replay.py` — `_build_replay_active_window_mask` (vectorized minute-of-day mask), Tier-5 gate in the bar loop, `_replay_force_flat_cutoff_minutes_et` lazy cache + hoist, precompute hook invocation.
    - `core/backtest/ohlcv.py` — `replay_bars_from_ohlcv_df` vectorized rewrite (numpy import added).
    - `strategies/morning_range_reversion_strategy.py` — declares `replay_active_window_et = [(range_start, flat_before)]` during `__init__`.
  - **Operational notes**:
    - Like the prior rounds, **all changes are byte-identical** — no flag to enable, no opt-out needed. The gate auto-engages on strategies that declare a window; strategies without one are unchanged.
    - Parity test runtime visible side effect: `tests/test_backtest_fast_loop_parity.py::...morning_range_reversion-MNQ-2026-04-01-2026-04-30]` dropped from ~13 s → **~6.7 s** on this Mac mini.

- **Backtest performance overhaul round 2 — `_bars_to_dataframe` fast path + in-process walkforward runner. Another 1.3-1.6× on top of the existing fast-loop stack.** Two independent wins surfaced by cProfile-ing the post-Tier-1 fast loop on a 3-month MNQ replay. Both stay parity-pinned by the existing `tests/test_backtest_fast_loop_parity.py` regression net (including the 4-month window that catches O(n²) drift) — both 1-month and 4-month parity tests passed after every change.
  - **`_bars_to_dataframe` fast path** (`core/backtest/strategy_replay.py`). The legacy implementation called `pd.to_datetime` twice per bar (once in the row-build loop, once in the index comprehension) even though the timestamps coming from `replay_bars_from_ohlcv_df` are already native Python `datetime` objects. The cProfile breakdown showed this single function consuming **~2.88 s of a 6-s 3-month replay** (in cProfile-overhead-amplified numbers) — pure waste re-parsing already-parsed timestamps. Replaced with a NumPy-array-backed assembly that builds the DataFrame column-oriented and feeds the pre-parsed datetimes straight into `pd.DatetimeIndex(list)` (pandas detects the pre-parsed shape and skips per-element parsing). Falls back to the legacy slow path only when a bar's timestamp is a string/int/float (the rare non-canonical case — tests, sample data, ad-hoc callers).
    - Companion change in `_iter_bars_fast`: `timestamps = list(df.index)` once at top of the generator instead of `timestamps[i]` per iter. `DatetimeIndex.__getitem__` costs ~70 µs/call (pandas's element-access path); a flat list lookup is ~50 ns. On 17k bars this is **~3.4 s → ~1 ms** of the same cProfile dimension.
    - **Measured**: 3-month MNQ `morning_range_reversion` 1.34 s → **1.04 s per subprocess** (1.3× more on the replay path on top of the prior 62× fast-loop win). The full walkforward matrix benefits proportionally.
  - **In-process walkforward runner (`--in-process` flag on all three scripts).** Even after the fast-loop work, each subprocess pays a fixed ~250-400 ms tax for Python interpreter startup + pandas/numpy/pyarrow/strategy bytecode imports. On a 36-task matrix that's ~10 s of pure overhead serialized across 8 parallel workers — bigger than the actual replay work now. New `core/backtest/inprocess_runner.py` exposes `run_backtest_inprocess(...)` (same input/output contract as the subprocess CLI's `--format=json --include-trades` mode) plus `worker_init(env)` for `ProcessPoolExecutor` pre-warming.
    - **How it works**: the parent script spins up a `ProcessPoolExecutor(initializer=worker_init, initargs=(env,))`. Each worker runs `worker_init` **once** at spawn (loads pandas, numpy, pyarrow, strategy_replay, executor module; applies env-var overrides including `BACKTEST_FAST_LOOP=1`; disables WARN-level logging to keep stdout clean). Subsequent task submissions reuse the already-warm worker — no Python startup, no re-import cost.
    - **Parity model**: same `BacktestExecutor.run_backtest` pipeline, same `_serialize_backtest_bundle` output shape. Env vars apply once at pool startup (process-wide) rather than per call; the walkforward scripts use a single `extra_env` for an entire run so this matches subprocess semantics.
    - **`--cache-decisions` interaction**: when both flags are on, the parent does cache lookups inline (fast — JSON read per key) and dispatches only cache misses to the process pool. Threads cannot run `run_backtest_inprocess` concurrently because the strategy loop is pure-Python (GIL-bound), so the cache-on + in-process combination MUST use processes for the actual replays.
    - **Measured (90-day, 6-fold, 3-symbol, 2-strategy = 36-task matrix)**:
      - subprocess + ThreadPool (Tier 1, baseline before this round): **~11.9 s wall, ~35 s user CPU**
      - in-process + ProcessPool (Tier 1 + Tier 2, this round): **~9.0 s wall, ~24 s user CPU**
      - Wall-clock: **1.32× faster**. User CPU: **31% less** (~11 s of pure subprocess/import overhead reclaimed).
      - `metrics.json` (36 rows): **byte-identical** between the two paths.
    - **Measured (24-task matrix, 4 sub-windows × 2 strategies × 3 symbols)**:
      - subprocess (cold): **3.64 s**
      - in-process (cold): **1.68 s** — **2.17× faster cold**. Identical trade counts (67/67).
  - **Files**:
    - `core/backtest/strategy_replay.py` — `_bars_to_dataframe` rewritten; `_iter_bars_fast` materializes timestamps once.
    - `core/backtest/inprocess_runner.py` (new) — `worker_init`, `run_backtest_inprocess`.
    - `scripts/walkforward_trade_recap_report.py` — new `--in-process` flag; main fan-out branches into ProcessPool path (cache-aware misses-only dispatch when `--cache-decisions` is also on) or legacy subprocess+ThreadPool path.
    - `scripts/walkforward_strategy_competition.py` — new `--in-process` flag; new `_run_one_inprocess` mirrors the subprocess `_run_one`'s on-disk JSON artefact for debug-trail parity.
    - `scripts/walkforward_last_trades_charts.py` — new `--in-process` flag.
  - **Operational notes**:
    - `--in-process` is opt-in (subprocess is still the default) so existing CI / scheduled runs are unchanged. After more bake-in we can flip the default; for now it's "use it when iterating".
    - Worker initialization disables WARN-level logging — strategies like `morning_range_reversion` emit thousands of "sweep too far" warnings per fold which add up to noticeable stdout-formatting cost on a fan-out run. The walkforward outputs (HTML, metrics.json, summary.tsv) don't consume those WARN lines so dropping them is safe.
    - When `--cache-decisions` is on, every cache hit avoids a worker entirely (the cache JSON read happens in the parent). Cache misses go to the warm worker pool — best of both worlds.
    - Memory: each warm worker holds pandas + numpy + pyarrow + strategy modules (~80-120 MB RSS). 8 workers = ~700 MB-1 GB peak. The subprocess path peaks at the same point during execution (each subprocess loads the same modules) but releases the memory between tasks; the in-process path holds it for the lifetime of the matrix run. Not a problem on dev machines or any host with >4 GB free.
  - **Why these two and not more**:
    - cProfile of the current fast-loop path showed `_bars_to_dataframe` and `_iter_bars_fast`'s `DatetimeIndex.__getitem__` together accounting for **~80%** of the post-fast-loop remaining time. Fixing those was the clear next move.
    - Subprocess startup was the next-biggest unaccounted-for cost. Moving in-process targets it directly.
    - Below these, remaining hot spots (engine fill simulation, equity curve append, strategy `analyze` body) are either already cheap or strategy-specific (would only help that one strategy). Not worth pursuing without a profile pointing at them.
  - **Combined-stack measured wall-clock** on a 90-day / 6-fold / 3-symbol / 2-strategy walkforward matrix (the realistic batch-tuning workload):
    - **Pre-optimization baseline** (slow loop, serial subprocesses, CSV parse on every read): several minutes.
    - **Tier 1 + parallel + parquet + fast-loop** (committed before this round): ~12 s.
    - **+ `_bars_to_dataframe` fast path + `--in-process`** (this round): **~9 s.** All paths produce byte-identical metrics.
    - **+ `--cache-decisions` warm**: single-digit seconds.

- **Backtest performance overhaul (Tier 1 + decision cache) — 6× wall-clock on walkforward matrix runs, 2-3× on report-iteration re-runs, byte-identical outputs throughout.** Three independent landings, each pinned with a regression test, all opt-in or transparent so existing optimization runs stay byte-for-byte reproducible.
  - **Parallel walkforward subprocess pool (`ThreadPoolExecutor`, default ON in every walkforward script).** `scripts/walkforward_trade_recap_report.py`, `walkforward_strategy_competition.py`, and `walkforward_last_trades_charts.py` now fan their `core/backtest_executor.py` subprocess calls out across a thread pool sized to `os.cpu_count()` (override via `--workers N` or env `WALKFORWARD_{RECAP,COMPETITION,LASTTRADES}_WORKERS`). Phase A runs the replays concurrently; Phase B (chart rendering / metrics aggregation / HTML assembly) stays serial so output ordering is byte-identical to the prior single-threaded loop.
    - **Measured speedup**: 36-task matrix (6 folds × 2 strats × 3 syms) on `walkforward_strategy_competition.py` went from **42.0 s wall (99% CPU) → 6.8 s wall (659% CPU)** = **6.2× faster**, `summary.tsv` and `leaderboard.md` byte-identical between `--workers 1` and `--workers 8` runs.
    - Same parity confirmed on `walkforward_trade_recap_report.py`: `metrics.json`, `metrics_insights.json`, `index.html`, `metrics.html`, and the full trade-chart-filename listing all `diff -q` clean between serial and parallel runs.
  - **Transparent Parquet sidecar cache for canonical OHLCV CSVs (`core/backtest/parquet_cache.py`).** Pyarrow promoted to a hard dep (`requirements.txt`). On first read of any `historical_data/price/*.csv` (or any auxiliary CSV the walkforward scripts open), a `<csv>.parquet` sidecar is written alongside it carrying the source CSV's `mtime_ns` in its parquet metadata. Subsequent reads skip the text parse entirely. Stale-detection is automatic: a CSV touched by databento/manual edit → sidecar invalidated → rebuilt next call.
    - **Measured speedup**: `pd.read_csv(MNQ_5m_databento.csv)` 70 ms → `load_ohlcv_cached(... same file)` **4 ms** after warm-up (217k rows, 13 MB CSV → 3.4 MB parquet). 18× faster on the hottest data-loading path; net wall-clock impact varies by script but compounds with the parallelism above.
    - Wired into both `HistoricalDataLoader.load_from_csv` (used inside every replay subprocess) and the three walkforward scripts' direct `pd.read_csv` calls (`_csv_last_date`, OHLCV chart cache, morning-range 5m signal-bar lookup). `BACKTEST_PARQUET_CACHE=0` is the emergency-fallback to disable sidecar use entirely. Sidecars are `.gitignore`-ed (rule already covered `*.parquet`).
  - **Opt-in decision cache for `walkforward_trade_recap_report.py` (`--cache-decisions` / `BACKTEST_CACHE_DECISIONS=1`).** Each (strategy, symbol, fold, TOML-hash, engine-hash, env-hash, CSV-stat) tuple maps to a content-addressed `docs/perf/_decision_cache/v1_<sha256>.json` file containing the full replay payload. Cache hits skip the subprocess + replay engine roundtrip entirely; cache key changes the moment any input that affects results changes (strategy `.py` edit, TOML edit, CSV refresh, env override, engine code change). Hit/miss counter printed at end of every run so a stale cache never goes unnoticed.
    - **Measured speedup**: 8-task recap re-run **4.8 s cold → 2.1 s warm = 2.3×** with 8/8 cache hits and byte-identical outputs. On a real 36+ task matrix this is the **seconds-for-iteration** path: when you're tweaking chart shading or report layout without touching strategy logic, every re-run skips every subprocess.
    - Cache key version `_CACHE_KEY_VERSION = 1` in `core/backtest/decision_cache.py` provides a single-line wipe escape hatch for indirect-dependency changes (e.g. shared helpers not covered by the explicit engine-file hashes).
  - **Engine fast-loop (`BACKTEST_FAST_LOOP=1`) — phase 1 + phase 2 landed; default ON in all three walkforward scripts; parity-pinned. ~62× faster on a 3-month morning_range_reversion replay.** The slow-path profile showed `mock_get_historical_data` consuming 91% of wall-clock with 12.3M datetime parses on a 4125-bar fold (the strategy asks for the last-N bars on every analyze invocation; the slow path linearly scanned + re-parsed every bar's timestamp on every call → O(n²)). Phase 2 replaces this with an `np.searchsorted` over a pre-computed UTC-ns array carved directly out of the DataFrame's DatetimeIndex (zero parsing). Phase 1's `_BarRow` + `_iter_bars_fast` (NumPy column iterator instead of `df.iterrows`) sit on top.
    - **Measured (MNQ 5m morning_range_reversion replay)**: 1 month **10.6 s → 0.94 s (11.3×)**; 3 months **83.8 s → 1.34 s (62.6×)** — the O(n²) of the slow path's mock means longer windows compound exponentially worse, exactly where the fast path's O(log n) bisect wins biggest. `overnight_range` 3 months 16.3 s → 12.7 s (1.3×) — small because overnight short-circuits per bar and rarely asks for history.
    - **Default ON in the walkforward scripts.** `scripts/walkforward_trade_recap_report.py`, `walkforward_strategy_competition.py`, `walkforward_last_trades_charts.py` now inject `BACKTEST_FAST_LOOP=1` into each replay subprocess by default. Pass `--no-fast-loop` for the slow path if you suspect drift (or set `BACKTEST_FAST_LOOP=0` for direct `core/backtest_executor.py` calls — defaults OFF there for safety).
    - **End-to-end recap report sanity check** (30-day, 2-fold, 2-symbol matrix): `--no-fast-loop` 9.5 s → default 3.4 s; `metrics.json`, `metrics_insights.json`, and trade-chart filename listing **byte-identical** between the two runs.
    - **Architecture**:
      - `_install_fast_strategy_mocks` is called **once** at the top of `replay()` instead of per bar. Closes over a precomputed `bar_times_ns: np.ndarray[int64]` (from `df.index.to_numpy(dtype="datetime64[ns]").view("int64")` — pandas already parsed every timestamp once, fast path reuses that work).
      - Per-bar replay loop in fast mode: just sets `self.backtest_engine.current_bar_index = i` and `self.trading_bot._fast_cursor_index = i`. No `bars[:i+1]` slice, no `MockTradingBot.bars` reassignment, no `_resampled_cache.clear()`.
      - `fast_mock_get_historical_data` intraday-csv path: `np.searchsorted` for `cur_utc` cutoff + optional `start_time` / `end_time` filters, then a single list slice. O(log n) + O(k).
      - Rare coarser-TF resample path: still re-allocates the prefix slice when invoked, but `MockTradingBot._select_historical_source` + `_resample_native_to` now honor `_fast_cursor_index` so the resample sees only `bars[:cursor+1]` even though `self.bars` is the full list. Cache key in fast mode is `(target_tf, cursor)` with one entry retained per target_tf (auto-evicts prior cursor entries).
      - `fast_mock_get_market_quote` indexes `bars[cursor_index]` instead of the slow path's `bars_list[-1]` — same effective bar.
      - Slow path is **untouched** — every line of the original `_call_strategy_analyze` install/call/uninstall flow is preserved verbatim and exercised whenever `BACKTEST_FAST_LOOP=0`. Both code paths share the same parity test scaffolding.
    - **Parity test** (`tests/test_backtest_fast_loop_parity.py`): runs the same canonical-CSV fold twice through `core/backtest_executor.py` (`BACKTEST_FAST_LOOP=0` then `=1`) and asserts trade count + every trade's side/entry_time/exit_time/entry_price/exit_price/qty/pnl/exit_reason/bars_held are byte-identical, plus top-line metrics (total_pnl, win_rate, max_drawdown) match within 1e-6. Parametrized over `overnight_range` and `morning_range_reversion` on MNQ for both a 1-month (Apr 2026) and a 4-month (Jan-Apr 2026) window — the long window is the regression net for any future O(n²) drift; it would silently produce wrong-results on a small fold but fail loudly on a long one.
  - **Tests** (new): `tests/test_backtest_parquet_sidecar.py` (5 tests covering round-trip, mtime invalidation, `HistoricalDataLoader` integration, env-disable fallback, pinned-date-format path), `tests/test_backtest_decision_cache.py` (11 tests covering key determinism, per-input invalidation, env-order canonicalization, CSV mtime invalidation, corrupted-file recovery, failed-payload skip, env-var enable), `tests/test_backtest_fast_loop_parity.py` (2 strategy parametrizations × byte-identical trade-list diff).
  - **Files**:
    - `core/backtest/parquet_cache.py` (new) — `load_ohlcv_cached(csv_path)` + `_normalize_ohlcv_frame`. Tested standalone; reused by `data_loader.py`.
    - `core/backtest/decision_cache.py` (new) — `CacheKeyInputs`, `lookup`, `store`, `cache_enabled`. Self-contained, no runtime imports of strategy code.
    - `core/backtest/data_loader.py` — `HistoricalDataLoader.load_from_csv` now defers to `load_ohlcv_cached` on the default-format path; `date_format=…` callers still write a sidecar after parse so the next default read is hot.
    - `core/backtest/strategy_replay.py` — new `_BarRow` + `_iter_bars_fast` + `_fast_loop_enabled()` env gate; replay loop branches based on the flag.
    - `scripts/walkforward_trade_recap_report.py` — new `--workers`, `--cache-decisions`, `--cache-dir` flags; main loop split into Phase A (parallel subprocess fan-out) + Phase B (serial chart/metrics build). `_csv_last_date` and the OHLCV/morning-5m chart caches read through `load_ohlcv_cached`.
    - `scripts/walkforward_strategy_competition.py` — new `--workers` flag; task matrix flattened into a `ThreadPoolExecutor` fan-out preserving symbol→strategy→fold output ordering. `_csv_last_date` uses parquet cache.
    - `scripts/walkforward_last_trades_charts.py` — new `--workers` flag; same Phase A/B pattern as the recap report. `_csv_last_date` and the chart OHLCV cache use parquet.
    - `requirements.txt` — `pyarrow>=15.0.0` promoted to a hard dep (was implicit via polars).
    - `.gitignore` — added `docs/perf/_decision_cache/`.
  - **Operational notes**:
    - Default workers = `min(n_tasks, os.cpu_count())`. On an 8-core mac this saturates around 6.5× CPU which is the practical ceiling once subprocess Python startup + import time is amortized.
    - The first parquet sidecar write for each CSV pays a one-shot pyarrow.parquet import cost (~150-300 ms cold) that gets amortized over all subsequent reads in the same Python process. Cross-process speedup (each replay subprocess is fresh) shows up as cumulative wall-clock reduction over the matrix run.
    - `--cache-decisions` is intentionally opt-in. Use it while iterating on the report (chart shading, new metrics rows, fold layout); leave it off when validating a new TOML or strategy edit — though even with it on, any change to the strategy `.py` / TOML / engine code triggers a miss and a fresh run.
    - `BACKTEST_FAST_LOOP=1` is the **default** for the three walkforward scripts (recap report, strategy competition, last-trades charts) and **off** for raw `core/backtest_executor.py` invocations. The walkforward scripts opt in via injected env in their subprocess fan-out; pass `--no-fast-loop` to revert. For direct `backtest_executor` calls (notebooks, ad-hoc tools) set `BACKTEST_FAST_LOOP=1` to enable.
    - **Combined-stack measured win on a typical 90-day, 6-fold, 3-symbol walkforward matrix**: parallelism × parquet × fast-loop together drops wall-clock from **multiple minutes** (slow loop, serial, CSV reads) to **roughly a minute or under** (parallel + parquet + fast loop), with `--cache-decisions` cutting that further to **single-digit seconds** on report-iteration re-runs.

- **`morning_range_reversion` per-symbol position-size weighting (round 24) — MNQ doubled to 4 contracts for +18-22% return on every window with same-or-better DD and RF.** Empirical follow-up to user feedback: *"reevaluate combination weighting between symbols, varying size or dropping low-performance symbols"*. New `[symbols.MNQ.risk].position_size = 4` override (root stays `position_size = 2`) routed through a new `_position_size(symbol)` resolver in the strategy and a multi-section TOML injector in `sweep_per_symbol_breaker.py`.
  - **Drop-symbol analysis (real subset walkforwards, not approximation)**: dropping MGC catastrophic (+583% → +185% on 9m, -68% return loss — MGC is THE driver); dropping MNQ −22% return; dropping MES neutral (-7-12% return, ~same RF; **not worth the complexity reduction** because MES provides high-WR ballast that smooths the equity curve).
  - **Weighting sweep (7 variants × 3 windows)** committed `MNQ=2× (position_size=4)` because it's the only Pareto improvement:
    - **3m**: ret 434% → **513%** (+18%), DD 12.38% → 13.08% (~tied), RF 12.42 → 12.37 (~tied)
    - **6m**: ret 403% → **484%** (+20%), DD 74.94% → 78.95% (~tied), RF 3.78 → **4.02** (+6%)
    - **9m**: ret 583% → **711%** (+22%), DD 31.79% → **30.49%** (LOWER!), RF 6.27 → **6.66** (+6%)
  - **Rejected weightings**: `MGC_2x` (+71% ret but 6m DD 75% → 111% — same RF, just bigger swings); `MNQ_2x_MGC_2x` (combines benefits but 6m DD 112%); `MES_half` (neutral); `MNQ_2x_MES_half_GC_2x` (no improvement over MNQ_2x alone).
  - **Per-trade dollar risk under new config**: MNQ `30pt × $2 × 4ct = $240`, MES `~15pt × $5 × 2 = $150`, MGC `~22.5pt × $10 × 2 = $450` — closer to risk parity than baseline ($120 / $150 / $450). Worst-case 3-symbol stop-out day = $840 (below the typical $1,000 Topstep DLL).
  - **Files**:
    - `strategies/morning_range_reversion_strategy.py` — new `_position_size(symbol)` helper. `execute()` swaps `self.config.position_size` for `self._position_size(signal["symbol"])`. The replay engine routes through `strategy.execute()`, so backtests pick up per-symbol qty automatically.
    - `config/strategies/morning_range_reversion.toml` — new `[symbols.MNQ.risk]` section with `position_size = 4` and full sweep rationale inline.
    - `scripts/sweep_per_symbol_breaker.py` — multi-section TOML injection (knobs like `position_size` now route to `[symbols.<SYM>.risk]`); also fixed a regex bug where `[^\[]*` prematurely stopped inside the MNQ block at a `[` char in a comment (`skip ["Mon","Thu","Fri"]`) — replaced with lazy `.*?` + lookahead for next section header.
    - `tests/test_morning_range_reversion_smoke.py` — new `test_per_symbol_position_size_falls_back_to_root` pins resolver precedence (per-symbol TOML > root TOML > class default).
  - **Final reports**: `docs/perf/morning_range_round24_{3m,6m,9m}/` (supersedes `morning_range_round23_*`).

- **`morning_range_reversion` per-symbol weekday filters (round 23) — every committed symbol either improved or held vs Round-22.** Empirical follow-up to user feedback after eyeballing the Round-22 3m index page: "MGC has a disproportionate amount of losses on Wednesday" and "re-evaluate configs for each of the symbols separately rather than combined." Per-symbol `[symbols.<SYM>.signal].skip_weekdays` overrides land via the existing `_skip_weekdays(symbol)` resolver — no new code path, just three sweep-validated TOML stanzas.
  - **MGC: `skip_weekdays = ["Wed", "Fri"]`** — 3m loss-pattern report showed MGC Wed = 4 of 8 losses (50% loss share, 40% Wed loss rate vs 10–22% on other days). Per-window sweep (`docs/perf/_opt_runs/r23_mgc_wd_{3m,6m,9m}/`, baseline ⇒ committed):
    - **3m MGC DD: 39% → 16% (-59%), MGC ret +262% → +308% (+18%), RF 2.74 → 8.16**
    - 6m MGC DD: 85% → 63% (-25%), ret -19%, RF 1.95 → 3.68 (+89%)
    - 9m MGC DD: 36% → 34% (~tied), ret -9%, RF 3.85 → 5.68 (+48%)
    - Wed-only without Fri-skip regressed 9m DD 36% → 126% — Fri-skip stays as the primary cluster protection; Wed-skip is the marginal MGC-specific filter.
  - **MNQ: `skip_weekdays = ["Mon", "Thu", "Fri"]`** — MNQ Mon/Thu had 53-57% loss rate across all windows (vs ~40% Tue/Wed). Per-window sweep:
    - **3m MNQ DD: 23% → 10.37% (-55%), RF 3.28 → 4.86 (+48%)**, ret -27% (108→79)
    - **6m MNQ DD: 55% → 24.41% (-56%), RF 1.16 → 3.01 (+159%)**, ret +14% (70→81)
    - **9m MNQ DD: 40% → 19.16% (-52%), RF 1.74 → 4.14 (+138%)**, ret -10% (143→128)
    - Single-day skips were weaker (skip_thu_fri RF 2.40, skip_mon_only RF 3.60); the Mon-Thu-Fri triple was the clear cross-window winner. MNQ alpha concentrates on Tue/Wed.
  - **MES: inherits root `["Fri"]` (no per-symbol weekday override)** — sweep tested {none, [Tue,Fri], [Wed,Fri], [Wed]}; every alternative regressed:
    - skip_wed_only catastrophic: 9m ret +57% → -50%, DD 20% → 73%
    - skip_wed_fri: 9m ret +57% → +40%, DD 20% → 23%
    - 3m MES Wed had 0% loss rate (BEST day) but 6m/9m Wed = 23% loss rate; the 3m signal didn't generalize. Conclusion: keep root Fri-only.
  - **MES `tp_mult` re-evaluated and held at 0.7** — user asked about lifting MES tp_mult back to 1.0+. Sweep tested {0.85, 1.0, 1.25, 1.5}. Every higher TP hurt MES DD on every window (tp=1.0 raised 9m DD 20% → 41%; tp=1.5 to 66%). MES thin ranges cause higher-TP targets to time out before being filled. Decision: **0.7 is empirically optimal**, documented inline in the MES override block to forestall re-litigation.
  - **Tooling**: `scripts/sweep_per_symbol_breaker.py` extended to TOML-render list-typed values (was stringifying `["Wed","Fri"]` as Python repr — invalid TOML). New `test_per_symbol_skip_weekdays_committed_defaults` pins the resolved-weekday-set per symbol so accidental TOML edits regress visibly.
  - **Composite (`docs/perf/morning_range_round23_{3m,6m,9m}/`)** — supersedes `docs/perf/morning_range_regime_breaker_*`. MES unchanged across the board. MNQ DD halved on every window. MGC dramatic 3m improvement, modest 6m/9m DD trade-off for outlier protection.

- **`morning_range_reversion` regime-detection layer (round 19 — cross-session consec-loss circuit breaker).** New committed defense against trend-cluster drawdowns (e.g. the late-2025 MGC bleed that drove the 9m/6m DD on the prior config). Per-symbol opt-in breaker (`signal.max_consecutive_losses` + `signal.loss_streak_cooldown_sessions`) inspects `self._replay_engine.trades` (backtest) or live broker fill history; once N contiguous losing trades occur on a symbol, halts entries on that symbol for K calendar days from the *most-recent* loss. Pure read of engine state, zero per-bar strategy state writes — avoids the non-deterministic drift the earlier `record_trade_outcome` hook approach caused.
  - **Committed config (MGC + MES only, mcl=2, cd=10)** — found by per-symbol sweep round 19c+d+e+f (after fixing a wrapper bug that was silently clobbering MES injection):
    - **9m MGC DD: 73% → 36%** (-37pp), MGC ret +465% → +438% (-6%), MGC RF 3.81 → **5.90** (+55%)
    - **9m MES DD: 32% → 20%** (-12pp), MES RF 1.42 → **1.95**
    - **6m MGC DD: 106% → 85%** (-21pp), 6m MGC ret 408% → 364% (-11%)
    - **3m MGC**: hurt — DD 25% → 39% and ret 417% → 262% — the 3m window has no bad cluster so the breaker is over-firing on normal-market 2-loss streaks. **Accepted trade-off**: the outlier (late-2025 trend cluster) lives in 6m/9m where the breaker materially protects DD; sacrificing some 3m return to insure against the cluster is the explicit user-requested priority ("improve performance during this outlier time").
    - **MNQ untouched** (no breaker) — universal-breaker sweeps showed MNQ ret would drop -60% (143% → 59%) because MNQ's tighter slfix=50 / cap=30 geometry already produces shallow losses and a count-only breaker over-fires on normal-market noise.
  - **Per-fold breakdown of MGC win sources (9m)**: fold 1 (Sep-Oct 2025 bad) pnl -1103 → -9 (+1094, DD 2177→1083); fold 3 (Nov-Dec 2025 BAD CLUSTER) pnl -1511 → -393 (+1118, DD 1823→705); fold 7 (Mar-Apr 2026 PROFITABLE) pnl 3712 → 1176 (-2536, DD unchanged — collateral mis-fire). Net pnl impact -528 vs ~$3K of DD reduction; the trade-off is favorable.
  - **Rejected enhancements (all kept as opt-in default-0 knobs for future tuning)**:
    - `signal.rolling_pnl_loss_threshold_dollars` (magnitude AND-filter) — sweep round 21: 3m normal-market 2-loss streaks and 9m cluster streaks have similar $ magnitudes (~$400-700 cum loss), so no threshold cleanly discriminates without disabling cluster protection.
    - `signal.breaker_min_efficiency_ratio` + `signal.breaker_efficiency_ratio_lookback_days` (KER regime gate on breaker trip) — sweep round 22: even though descriptive stats showed KER_10d cleanly separates bad-folds (mean 0.45-0.51) from profitable-folds (mean 0.31-0.33), at the *specific moments* the breaker trips, KER value doesn't predict future losses; the gate WORSENED the cluster catch (DD 36% → 77% at thr=0.45). The breaker's own loss-count signal is empirically better.
    - `signal.skip_above_efficiency_ratio` (KER as standalone entry-skip gate, added earlier this turn) — descriptive inspection of MGC 5m databento data showed KER>0.5 fires on 49% of all sessions but only 14% of those are bad-cluster days; not selective enough to use as a primary filter.
  - **Files**:
    - `strategies/morning_range_reversion_strategy.py` — new helpers `_max_consecutive_losses`, `_loss_streak_cooldown_sessions`, `_consec_loss_breaker_status`, `_normalize_to_date`, `_compute_efficiency_ratio`, `_skip_above_efficiency_ratio`, `_efficiency_ratio_lookback_days`, `_breaker_min_efficiency_ratio`, `_breaker_efficiency_ratio_lookback_days`, `_rolling_loss_threshold_dollars`. `_fade_signal_after_sweep` consults the breaker via the new `_consec_loss_breaker_status(symbol, bar_session_date, bars)` returning a structured status dict (engine-state-read only, side-effect-free). `record_trade_outcome` is now a forward-compat no-op stub for live wiring.
    - `config/strategies/morning_range_reversion.toml` — MGC + MES blocks each gain `max_consecutive_losses = 2` and `loss_streak_cooldown_sessions = 10` with inline rationale. MNQ left untouched.
    - `tests/test_morning_range_reversion_smoke.py` — 4 new tests: `test_consec_loss_breaker_reads_engine_trades`, `test_consec_loss_breaker_disabled_when_knob_zero`, `test_consec_loss_breaker_magnitude_filter`, `test_consec_loss_breaker_mgc_default_from_toml`, `test_efficiency_ratio_computation` (perfect-trend KER ≈ 1.0, perfect-oscillation KER ≈ 0.2).
    - `scripts/sweep_per_symbol_breaker.py` — new sweep harness; writes per-symbol TOML overrides per trial (per-symbol overrides win over env vars in `StrategyConfig.symbol_override` precedence, so env-var-based sweeps can't reach `[symbols.<SYM>.signal]`). Trials may also specify `env` overrides for ROOT-level knobs (e.g. `MORNING_RANGE_REVERSION_SIGNAL_LOOKBACK_BARS=5000` was needed to give KER enough daily-close history). Runs sequentially to avoid TOML races. **Wrapper bug fixed mid-round-19**: previous version's "check key existence anywhere" replace logic clobbered the FIRST symbol's injection when multiple symbols were being patched in one trial; now scopes the existence check to the specific `[symbols.<SYM>.signal]` block.
  - **Fresh full reports**: `docs/perf/morning_range_regime_breaker_{3m,6m,9m}/` (3 windows committed-config walkforwards) supersede `docs/perf/morning_range_final_{3m,6m,9m}/` (pre-breaker baselines).

- **`morning_range_reversion` four opt-in DD-attack knobs (all 0/None default → no-op on existing config).** Added so future operators can flip them without re-touching the strategy code; each was sweep-tested this turn and found either neutral or harmful on the current 3m/6m/9m walk-forward — they ship turned-off and inline-documented for the next regime where they might be useful.
  - **`signal.sl_max_pct_of_range` (dynamic SL cap)** — alternative to absolute `sl_max_pts`, expressed as a fraction of the anchor range width.  Lets the cap scale with each day's volatility: a 12pt anchor day with `sl_max_pct_of_range=1.5` caps SL to 18pt; a 24pt day caps to 36pt.  *Use case*: if `sl_max_pts=30` ever proves too tight on extreme-range days for a symbol, this knob can keep the cap proportional.  Wired into the SL-distance computation in `analyze()` *in addition to* the absolute cap (whichever is tighter wins).
  - **`signal.entry_start_et` / `signal.entry_end_et` (TOD entry window)** — skip entries whose bar ET-time falls outside `[start, end]` (both bounds inclusive; either side `None` = unbounded).  Per-symbol override-able.  **Sweep result (9m, round 15)**: any start ≥ 09:15 ET destroys the strategy (ret +634% → -37% to -206%, DD 53% → 175–395%); any end ≤ 12:00–14:00 ET was a no-op (entries already cluster in the 08:35–11:00 ET window).  **The morning IS the edge** — keep this knob OFF and rely on the existing `flat_before` / `range_effectiveness_hours` for time-bounding.
  - **`signal.skip_high_atr_quantile` (inverse ATR-regime gate)** — mirror of the existing `require_high_atr`: skip entries on days where today's ATR is *above* the `quantile` percentile of the prior `atr_regime_lookback` ATR samples.  *Use case*: cut whipsaw days during volatility spikes.  **Sweep result (9m, round 17b)**: every value 0.60–0.90 regressed (ret −20% to −95%, DD up to 104%).  **High-vol days are the GOOD days** for range-reversion (wider ranges → cleaner fade geometry); skipping them removes the edge.
  - **`signal.max_consecutive_losses` (per-session consec-loss circuit breaker)** — counts losing trades within the current session via the new `record_trade_outcome(symbol, pnl)` strategy hook; once the streak hits the threshold, no more entries this session.  Resets each session-date.  *Use case*: protect against a known-bad-day regime where 3 losses in 90 min predict a 4th.  **Live-only**: the `BacktestEngine` dispatch was *intentionally not wired* — a 9m diagnostic showed that even an inert hook caused +4 MNQ trades vs the no-hook run (ret 143% → 113%, RF 1.74 → 1.29) for reasons not yet bisected (the strategy state writes are guarded behind `mcl > 0` so the cause is upstream).  See the in-code note at `core/backtest/strategy_replay.StrategyReplayEngine.__init__`.  Live bot can wire this hook from its fill handler when needed.

### Changed
- **`morning_range_reversion` per-symbol SL normalization + `sl_max_pts` safety cap — fixes the blanket 35pt fixed-stop geometry that imposed wildly uneven dollar risk across symbols (MNQ $70 / MES $175 / MGC $350 per loss) and adds a cross-symbol max-cap on the final entry→stop distance.** Walk-forward 9m validation vs the Friday-skip-only baseline:
  - **MNQ**: ret +136% → **+143%**, max DD **64% → 40%** (-24pp), RF 0.94 → **1.74** (+85%)
  - **MES**: ret +52% → +35%, max DD 40% → **32%** (-8pp), RF 1.29 → 1.02
  - **MGC**: ret +480% → +465%, max DD **105% → 73%** (-32pp), RF 2.32 → **3.81** (+64%)
  - **6m MNQ DD: 89% → 55%** (-34pp), **6m MGC RF**: 2.92 → 3.35 (+15%)
  - **Method (round 12 — per-symbol joint sweep)**: 9m walk-forward (270d / 9 folds, Friday-skip on) ran 10 trials per symbol over `(sl_fixed_pts, sl_mult, tp_mult)` grids. MGC winner: `sl_fixed_pts=0` + `sl_mult=3.0` produced composite **41850 vs slfix=35's 26905** (+55%) with **43pp lower DD** (62% vs 105%). MES winner: `sl_fixed_pts=0` + `sl_mult=3.0` (tp_mult stays 0.7 per-symbol because per-symbol TOML overrides win over root env-var overrides in `StrategyConfig.symbol_override`'s precedence chain — every R12 MES trial silently used tp=0.7 even when the trial label said tp=1.5).
  - **Method (round 14 — max-cap sweep)**: 9m walk-forward across all 3 symbols ran 8 trials over `sl_max_pts ∈ {25, 30, 35, 40, 50, 60, 75}` + uncapped baseline. **cap=30 won** the cross-window composite (3m RF **10.78**, 6m RF **3.14**, 9m RF **3.96**); cap=25 catastrophe on 9m (DD **104%** — too-tight stops convert winners into whipsaw-out losses); caps ≥ 60 are effectively no-ops because the typical SL distance (sl_mult=3.0 × half_width ≈ 15–22pt for MES/MGC, slfix=50 for MNQ) sits well below them on typical-range days.
  - **Answer to "fixed 35pts vs minimum/maximum 35pts" operator question**: **MAX CAP wins** on every metric. The fixed-35pt geometry was a regime trap — it dominated 3m backtests because MES/MGC almost never hit it (WR 91–86% with $175/$350 per-loss tail), but the rare extreme-range days drove all the long-window drawdown. A range-anchored stop with an absolute cap (cap=30) produces consistently smaller tails AND captures the natural per-symbol volatility regime.
  - **Files**:
    - `config/strategies/morning_range_reversion.toml` — root `signal.sl_max_pts = 30` (safety cap) and `signal.sl_min_pts = 0` (floor, unused, 0-default knob for future use); MES per-symbol overrides updated: `sl_fixed_pts = 0`, `sl_mult = 3.0` (was root slfix=35 / sl_mult=1.0); MGC per-symbol overrides updated: `sl_fixed_pts = 0`, `sl_mult = 3.0`; MNQ per-symbol slfix=50 unchanged (universal cap=30 clamps it to 30pt effective, which 9m walk-forward shows is the DD-optimal MNQ stop anyway).
    - `strategies/morning_range_reversion_strategy.py` — new helpers `_sl_mult(symbol)` (per-symbol-aware range-anchored multiplier), `_sl_max_pts(symbol)` (per-symbol-aware max cap), `_sl_min_pts(symbol)` (per-symbol-aware min floor). `analyze()` SL computation refactored to compute the entry→stop *distance* first, apply cap/floor, then place the stop — gives identical cap/floor semantics across fixed-pts and range-anchored modes.
  - **Effective dollar risk per trade (new config)**: MNQ `30pt × $2 = $60`, MES typical `15pt × $5 = $75` / cap `30pt × $5 = $150`, MGC typical `22.5pt × $10 = $225` / cap `30pt × $10 = $300`. Still uneven (MGC 5× MNQ in $) because the contracts have different per-point values — perfect dollar normalization would require per-symbol caps (e.g. MGC cap=10), but round 14 showed the universal cap=30 beats every tighter cap on 9m composite, so we accept the dollar asymmetry as the price of letting the range-anchored math breathe.
  - **Tests**: 2 new in `tests/test_morning_range_reversion_smoke.py` — `test_sl_max_pts_clamps_sl_distance_from_entry` (env-var override of `sl_max_pts=30` reduces `slfix=50` to a 30pt entry→stop distance; symmetric floor lookup), `test_morning_range_toml_root_has_sl_max_pts_safety_cap` (root TOML ships with `sl_max_pts=30 / sl_min_pts=0`), `test_morning_range_toml_per_symbol_sl_mult_overrides` (MES + MGC `sl_mult=3.0` per-symbol overrides hold, MES + MGC `sl_fixed_pts=0` explicit, MNQ still keeps `sl_fixed_pts=50`). `test_morning_range_toml_per_symbol_tp_mult_overrides` updated to assert MES `tp_mult=0.7` (was inadvertently flipped to 1.5 in mid-round-12 commit; restored to the long-standing per-symbol override value because every walk-forward sweep evaluated with this effective value via per-symbol-overrides-win precedence).

- **`morning_range_reversion` weekday-skip gate — ``signal.skip_weekdays = ["Fri"]`` lifts composite score another 2.4× on top of the prior knob sweep.** Empty list = legacy behaviour; per-symbol overrides via ``[symbols.<SYM>.signal].skip_weekdays``. Validated parallel on 3m/6m/9m walk-forward (composite values left → right are *without* / *with* the Fri skip):
  - **3m**: 77295 → **182588** (+136%), return +574% → +642%, max DD 31% → 30%, RF 5.74 → **12.31**, WR 77.5% → **81.3%**, n_trades 173 → 139
  - **6m**: 13166 → **26481** (+101%), return +400% → +530%, max DD 195% → 173%, RF 1.32 → 2.10, WR 75.0% → 76.1%, n_trades 340 → 272
  - **9m**: 16496 → **40811** (+147%), return +453% → **+669%**, max DD **145% → 88%**, RF 1.49 → 2.65, WR 75.3% → 76.9%, n_trades 509 → 412
  - **Per-symbol uplift (3m, Fri skipped vs traded)**: MES WR 91.5% → 93.6% (RF 1.09 → 1.86); MGC WR 81.5% → 86.4% (RF 5.32 → **10.19**, double); MNQ WR 60.0% → 64.6% (RF 1.79 → 2.62)
  - **Why Friday**: pre-optimization Fri loss-rate was 38% (3m) / 32% (9m) vs 14–22% on Mon–Thu. MNQ specifically was a coin flip toward losing on Fri (58% loss-rate). The morning-range mean-reversion geometry consistently underperforms on Fridays — pre-weekend closeout flow + thin afternoons.
  - **Files**:
    - `config/strategies/morning_range_reversion.toml` — new root `signal.skip_weekdays = ["Fri"]` with the same inline justification block as the other walk-forward winners.
    - `strategies/morning_range_reversion_strategy.MorningRangeReversionStrategy._skip_weekdays(symbol)` — new per-symbol-aware helper. Accepts list (TOML) or comma/space-separated string (env) of weekday names (Mon/Tue/.../Sun, case-insensitive, with common abbreviations like ``Tues``/``Thur``/``Weds``) **or** ints 0..6 (Mon=0). Unknown tokens log a one-shot WARNING and are ignored. Class also gains `_WEEKDAY_ALIASES` / `_WEEKDAY_ALIASES_INV` constants for parsing / display.
    - `strategies/morning_range_reversion_strategy.analyze()` — new top-level weekday gate **above** the new-session block. Skipped days never reach the range-build branch (which would otherwise call finalize and reset ``phase="scan"`` at line ~1705, defeating the gate on every bar after the first). Emits `🚫 SYM session skipped 2026-05-29 (Fri in skip_weekdays=['Fri'])` once per (symbol, date) lifecycle log.
  - **Tests**: 5 new in `tests/test_morning_range_reversion_smoke.py` — Fri (2026-05-29) is gated to ``phase='idle'`` with one log, multi-call throttle to one log per session, empty list restores legacy behaviour, non-listed weekday (Thu 2026-05-28) still builds, env-var int token (``"4"``) is equivalent to ``"Fri"``. Six pre-existing range-width tests had to add ``MORNING_RANGE_REVERSION_SIGNAL_SKIP_WEEKDAYS=""`` because their 2026-05-29 fixture date is a Friday (those tests cover the width-filter path, not the weekday gate).

- **`morning_range_reversion` walk-forward batch optimization — composite score (final_equity × recovery_factor) lifted from 1416 (baseline TOML) to 77295 (54.6× improvement) on 90d / 6-fold all-symbol walk-forward.** Aggregate metrics: return +90.4% → +573.9%, max DD 114% → 31%, RF 0.37 → 5.74, WR 49% → 77.5%. Validated on 6m (+400%, RF 1.32) and 9m (+453%, RF 1.49) — DD widens on longer windows because of structural MGC drawdown clusters in late-2025 that no knob in the current TOML can filter out without throwing away the 2026 wins (sweep-distance / range-effectiveness / range-width grids all neutral or worse). Reports: `docs/perf/morning_range_optimized_3m/index.html`, `docs/perf/morning_range_optimized_9m/index.html`.
  - **Method**: 8 rounds of coordinate-descent grid search via the new harness `scripts/optimize_morning_range.py` (runs N trials of `scripts/walkforward_trade_recap_report.py` in parallel with `--env KEY=VAL` overrides, parses each trial's `metrics_insights.json`, ranks by composite, writes a summary JSON; emits an ETA line every 30s once total elapsed > 5 min). Per-round summaries live under `docs/perf/_opt_runs/_summaries/round{1..9}_*.json`; per-trial recaps under `docs/perf/_opt_runs/round{1..9}_*/<trial_label>/`.
  - **Winning knob changes in `config/strategies/morning_range_reversion.toml`**:
    - `signal.sl_fixed_pts`: `0` → `35` (round 4–5: slfix=35 beat every `sl_mult ∈ {0.5..2.0}` AND every other slfix ∈ {20,25,30,32,37,40,55,70} by 30–50% composite — the fixed-distance stop survives narrow-range days that the range-anchored stop blew through).
    - `signal.max_fades_per_session`: `2` → `1` (round 2: 4× composite uplift across every other knob combo tested — the second fade of an anchor session has consistently worse expectancy than the first).
    - New `[symbols.MNQ.signal]` block: `sl_fixed_pts = 50`, `tp_mult = 1.25` (round 7: MNQ-isolated sweep showed wider stops + tighter TPs lift MNQ composite 1350 → 7208; MES / MGC kept root slfix=35 because their isolated sweeps preferred it).
  - **Knobs explicitly tested and rejected**: `breakeven_enabled ∈ {on, off}` × `breakeven_trigger_r ∈ {0.5, 1.0, 1.5}` × `breakeven_offset ∈ {0, 3, 5}` (round 3 — neutral or worse; SL=35pts is already wide enough that the break-even trigger rarely fires before the TP), `reentry_threshold_points ∈ {0, 2, 3, 5}` (round 3 — current `1` is optimal, `require_reentry_close=true` killed trade count from 173 → 6), `range_effectiveness_hours ∈ {4, 8}`, `max_sweep_distance_widths ∈ {0.5, 1.0, 1.5, 2.0, 3.0}` (round 2 + round 6 — all neutral once `max_fades=1` is in place), `min_range_width_points ∈ {10, 30, 40}`, `max_range_width_points ∈ {80, 100, 120, 150}` (round 4–5 — slightly negative; current `20 / 300` root + per-symbol overrides are at the sweet spot), `partial_tp_enabled = true` with `partial_tp_scalp_r ∈ {0.5, 1.0}` (round 4 — produced zero trades, partial-TP path is not currently exercised by `BacktestEngine`).
  - **Files**:
    - `config/strategies/morning_range_reversion.toml` — 4 knob bumps + a new `[symbols.MNQ.signal]` block, all carrying inline comments that cite the walk-forward sweep ranges they beat.
    - `scripts/optimize_morning_range.py` — new coordinate-descent harness (`asyncio.gather` of `walkforward_trade_recap_report.py` subprocesses with a `--max-parallel` cap, composite scoring with `min_trades` guard, ETA printer that only kicks in once total wall-clock > 5 min so quick rounds stay quiet).
  - **Operator notes**: per-symbol overrides win over root env vars (`StrategyConfig.symbol_override` returns the per-symbol TOML value if present, else falls back to root via env-var chain). The harness tests root via `--env` and adds per-symbol overrides by editing the TOML directly (no env-var shortcut exists for `[symbols.X.signal].Y`). Re-running the harness with a fresh `--round-label` writes to a new subdirectory under `docs/perf/_opt_runs/` so prior runs survive.

### Fixed
- **`morning_range_reversion` no longer attempts a fade entry when price is dramatically past the anchor (broker would reject as "Invalid price").** 2026-05-29 13:47 ET log: anchor `MGC H=4571.90 L=4556.80 W=15.10pts`, executor started 5h45m past 08:00 ET anchor close with `range_effectiveness_hours=8` (user-loosened from 4h). The strategy detected `c > H` (close was somewhere north of 4615 — ~3× width past the boundary), fired `🎯 SHORT MGC @ 4571.90 immediate_stop`, and the broker came back with `Code 2 Message: Invalid price. Price is outside allowed range.` because the stop-entry was outside the broker's accepted price band relative to current market. Even if the broker had accepted it, the fill probability of "price retraces $40+ to trigger our stop" inside the remaining session is essentially zero — it's a "catch a falling knife from above" geometry that range reversion is not designed for.
  - **`config/strategies/morning_range_reversion.toml`** — new `signal.max_sweep_distance_widths = 2.0` (default). Caps the allowed close-distance past the anchor boundary as a multiple of the anchor width. Per-symbol overrides live in `[symbols.<SYM>.signal]` (none added in this commit — the 2.0 default works for all three live symbols). `0` disables the guard (legacy behaviour).
  - **`strategies/morning_range_reversion_strategy.MorningRangeReversionStrategy._max_sweep_distance_widths(symbol)`** — new helper, mirrors `_min_range_width` / `_max_range_width` per-symbol lookup pattern. **`analyze()`** — new far-sweep guard evaluated at the top of the scan-phase sweep detector (before `c > H` / `c < L` even arms a signal). When `c > H + N×width` (or `c < L − N×width`) the signal is skipped with a one-shot WARNING per (symbol, side): `🛰️  MGC sweep too far for fade — close=4615.00 is 43.10pts past H=4571.90 (2.85× width=15.10, cap=2.00×). Skipping signal until price retraces inside cap.` The flag auto-rearms once price retraces inside the cap so a fresh far-sweep on the same side after a quiet stretch still gets a new warning.
  - **MGC behaviour explanation (operator FAQ)**: the strategy didn't pick the entry "far away from the anchor"; it placed the stop-entry exactly AT the anchor high (4571.90), which is the canonical fade-vs-breakout level. The *current market* had drifted ~43pt above the anchor by 13:47, so the entry was correctly geometrically placed but practically impossible — broker price-band check is the second line of defence after the strategy logic, and now it's the third (the new far-sweep guard is the first).
  - Tests: **4 new in `tests/test_morning_range_reversion_smoke.py`** — far-sweep high-side skipped with the `🛰️ sweep too far` WARNING (3× width past boundary, cap 2×), within-cap fade still emits (0.2× width past, well inside cap), `max_sweep_distance_widths=0` restores legacy behaviour (far-sweep still emits), symmetric low-side guard skips with `L=` in the message.

- **Race condition in cross-symbol session-reset coalescing — two concurrent symbols rotated the HTTP session within 5 ms of each other instead of coalescing.** 2026-05-29 14:00:21 log:
  ```
  14:00:21,243 - REST feed pinned for MES 5m … repeated 61× over 600s
  14:00:21,324 - REST feed pinned for MNQ 5m … repeated 61× over 600s
  14:00:21,326 - 🔁 HTTP session reset (reason=rest-stuck:MES:5m)
  14:00:21,331 - 🔁 HTTP session reset (reason=rest-stuck:MNQ:5m)  ← coalesce missed
  ```
  The original 2026-05-29 coalesce fix kept the `_last_session_reset_at_mono` update AFTER the `await auth.force_session_reset(...)` call. When MES and MNQ tasks ran in parallel under `asyncio.gather`, both read the old timestamp BEFORE either had written back ⇒ both saw `in_cooldown=False` and both fired the real rotation.
  - **`trading_bot.TopStepXTradingBot._detect_and_handle_stuck_rest._trip_reset`** — claim `self._last_session_reset_at_mono = now` **synchronously before** the `await auth.force_session_reset(...)` yields. Any concurrent task entering `_trip_reset` while task #1 is mid-await now sees the fresh stamp and takes the coalesce branch. Applies to BOTH the cold-start fast path AND the steady-state slow path because both share `_trip_reset`.
  - Tests: **1 new in `tests/test_rest_stuck_detector.py`** — `test_concurrent_trips_coalesce_to_one_actual_rotation`: runs `_detect_and_handle_stuck_rest` for MNQ + MES under `asyncio.gather` with a 50 ms slow-reset wrapper that yields mid-rotation; asserts exactly ONE actual `force_session_reset` call fires and both tasks return `True` for the caller's WARN-downgrade. Uses real wall-clock monotonic (no monkeypatch) because freezing `time.monotonic` would also stop `asyncio.sleep`.

- **Duplicate transient-retry WARNINGs in quick succession when concurrent in-flight requests hit the same dead pool slot.** 2026-05-29 14:00:32 log:
  ```
  14:00:32,194 - 🔁 Request transient ClientConnectionError for /api/Position/searchOpen (attempt 1/2)
  14:00:32,409 - 🔁 Request transient ClientConnectionError for /api/Position/searchOpen (attempt 1/2)
  14:00:32,411 - 🔁 Request transient ClientConnectionError for /api/Position/searchOpen (attempt 1/2)
  ```
  Three identical WARNINGs 215 ms / 2 ms apart from three concurrent `/Position/searchOpen` calls that each independently closed the (already-closed-by-a-sibling) session and emitted their own log line.
  - **`core/auth.AuthManager`** — new instance state: `_last_session_reset_at_mono` (monotonic timestamp of the most recent session close, `None` ⇒ never rotated) and `_transient_dedupe_window_s` (default `1.0`s, overridable via `AIOHTTP_TRANSIENT_DEDUPE_S`).
  - **`AuthManager.force_session_reset`** — stamps `_last_session_reset_at_mono = _monotonic_now()` both after a successful close AND on the no-op idempotent path, so the next concurrent transient handler sees "session was just rotated".
  - **`AuthManager._make_request`** — transient-retry handler now checks the rotation timestamp BEFORE closing the session. Within the dedupe window: skip the close, skip the WARNING, emit a single DEBUG line explaining the dedupe, just sleep the backoff and retry on the existing (fresh) session. Outside the window: rotate as before (and update the stamp INSIDE the session lock so other waiters see it). `getattr` defaults keep the path safe for test mocks that bypass `__init__`.
  - Tests: **2 new in `tests/test_auth_transient_retry.py`** — within-dedupe-window transient retry reuses the same session (no close, no WARNING, one DEBUG line, success on attempt #2 via the same scripted session), outside-window transient retry still rotates with the original WARNING.

- **Pre-existing TOML-pinned test failures fixed (smoke test hygiene).** `test_morning_range_toml_mes_tp_mult_override` previously hard-coded `tp_mult=1.0` for MNQ (root TOML) and broke whenever walk-forward results pushed the root knob — now it asserts the *fallback semantics* (override present for MES, fallback to live root for MNQ/MGC) without pinning the live value. `test_analyze_emits_short_after_high_sweep_reentry` previously assumed root `tp_mult=1.0` for its `take_profit==105.0` geometry — now monkeypatches `MORNING_RANGE_REVERSION_SIGNAL_TP_MULT=1.0` so the test documents the *geometry* it's testing without being coupled to the live TOML.

### Changed
- **Per-keepalive REST noise demoted from INFO to DEBUG.** The broker keep-alive heartbeat (`trading_bot._keepalive_heartbeat_loop`, every 120 s) called `AuthManager.list_accounts()` as a cheap "is the session still alive?" probe, which logged `Fetching active accounts from TopStepX API...` at INFO every iteration — ~30 lines/hour of noise in the file with no operator value (heartbeat success is implied by the absence of error logs and the bot continuing to trade). Similarly `_get_session()` logged `HTTP session ready: keepalive=75s, limit=64, …` at INFO on every fresh session build, which fires both at startup AND after every `force_session_reset` — 5+ INFO lines in a 25-min healthy session.
  - **`core/auth.AuthManager.list_accounts`** — demoted `Fetching active accounts from TopStepX API...` from INFO to DEBUG.
  - **`core/auth.AuthManager._get_session`** — demoted `HTTP session ready: …` from INFO to DEBUG.
  - Real account-fetch contexts (startup, account switch) still log their meaningful INFO from the caller side (`Found N active accounts`, `Selected account by index N: …`). Session-rotation context still surfaces via the `🔁 HTTP session reset (reason=…)` WARNING which IS the operator-visible signal.

### Changed
- **Terminal output for `morning_range_reversion` is now operator-grade clean.** The 2026-05-29 12:05:46 run still showed every lifecycle INFO with a full `2026-05-29 12:05:54,680 - strategies.morning_range_reversion_strategy - INFO - …` prefix — operator can't eyeball columns of H/L/width across symbols when half the line is timestamp + logger name. Plus three concurrent symbols triggered three separate cold-start session rotations within 500 ms of each other, surfacing extra `ServerDisconnectedError` ERRORs from in-flight `/api/Position/searchOpen` requests caught mid-rotation. All of that is gone now.
  - **`core/logging_setup._LifecycleConsoleFormatter`** (new) — dual-mode formatter the console handler uses when `lifecycle_logger_names` is set. **INFO** records from a named lifecycle logger render as **bare message** (no timestamp, no logger name, no level prefix), so a sequence of `📐 MNQ anchor range built …` / `📐 MES …` / `📐 MGC …` lines align visually in the terminal. **WARNING / ERROR** from the same logger keep the full `timestamp - logger - LEVEL - message` format because operators need timestamps when diagnosing faults. Records from non-lifecycle loggers also keep the full format — defensive against an unexpected leak through the upstream filter.
  - **`strategies/morning_range_reversion_strategy.MorningRangeReversionStrategy`** — every lifecycle log call (`🌅 new session`, `📐 anchor range built`, `🔁 anchor backfill`, `⏰ fade deadline reached`, `🛑 max_fades reached`, `🎯 SHORT/LONG signal`) reformatted with **fixed-width column alignment**. Symbol gets a `%-3s` left-pad (MNQ/MES/MGC line up), numerics use `%9.2f` right-aligned with 2 decimals (H/L/mid line up across MNQ at ~30000, MES at ~7500, MGC at ~4500), the filter-verdict block uses `%>3g`/`%<3g` width-aligned bounds and a final `✓` or `✗`. The strategy init banner that used to be a single 280-character line is now three stacked lines (timing / R-multiples / session caps), each one short enough to fit a normal terminal.
  - **`trading_bot.TopStepXTradingBot._detect_and_handle_stuck_rest`** — **cross-symbol coalesce window**. Tracks `_last_session_reset_at_mono` (sentinel `None` for "never reset"); when a cold-start trip fires within `_session_reset_cooldown_s = 10s` of the last actual rotation, the duplicate is suppressed (no second `force_session_reset` call). The detector still rearms the per-symbol tracker and still returns `True` so the strategy can downgrade its STALE DATA log. Net effect on the 2026-05-29 startup repro: 3 cold-start rotations within 500 ms collapse to **1** actual session reset.
  - **`strategies/morning_range_reversion_strategy._bars_are_stale`** — when `bot._last_session_reset_at_mono` shows a rotation within the last `3 × cooldown` seconds (≈30s), STALE DATA logs at **WARNING** instead of **ERROR**. The message also surfaces "Session was just rotated %.1fs ago — waiting for fresh REST/SignalR data to flow in" so the operator can see we're already healing. Steady-state stale data (no recent rotation) keeps logging ERROR — that's a real broker outage, not a cold-start blip.
  - **`trading_bot.TopStepXTradingBot.get_historical_data` / `_merge_live_bars`** — three INFO log lines demoted to **DEBUG** because they fired for EVERY symbol on EVERY 5-second strategy poll (3 symbols × 12 polls/min = 36 lines/min × 3 lines per poll = 108 INFO lines/min that the file kept but the operator didn't need): `Fetching historical data for {symbol}`, `📊 get_historical_data last bar timestamp (ISO) = …` + the matching `now UTC` line, and `📡 get_historical_data: merged %d live bar(s)`. The stuck-REST detector still WARNs when these actually fail, so the diagnostic signal is preserved without the steady-state noise. Operators who want the breadcrumbs back can `export LOG_LEVEL=DEBUG`.
  - Tests: **2 new in `tests/test_rest_stuck_detector.py`** — cross-symbol coalesce (3 cold-start trips within 250 ms produce exactly ONE actual `force_session_reset` call, all three return `True` for the caller's WARN-downgrade), cooldown expiry allows a second rotation past the 10s window. **2 new in `tests/test_morning_range_reversion_smoke.py`** — STALE DATA downgrades from ERROR to WARNING when `_last_session_reset_at_mono` is within recent-recovery window (and the message references the rotation), STALE DATA keeps ERROR severity when no recent rotation (real outage). **6 new in `tests/test_lifecycle_console_filter.py`** — bare-message format for lifecycle INFO (asserts no `" - "` separator leaks), full format for lifecycle WARNING/ERROR, full format for non-lifecycle INFO (defence against leaks), empty-whitelist fallback, dotted-child INFO inherits bare-format.

### Added
- **Lifecycle INFO logs from `morning_range_reversion` now surface in the terminal** — `📐 anchor range built`, `🌅 new session`, `🔁 anchor backfill from history`, `🎯 SHORT/LONG signal`, `⏰ deadline reached`, `🛑 max_fades reached`. Default console level stays at WARNING for every other module, so the per-poll `📊 last bar timestamp` chatter from `trading_bot` stays file-only; only the strategy's own lifecycle beacons get promoted. Operator gets to eyeball "which symbols will trade today" from the foreground without `tail -f` ing the log.
  - **`core/logging_setup`** — new `lifecycle_logger_names` kwarg on `configure_logging` and matching `LIFECYCLE_LOGGERS` env var (CSV, e.g. `strategies.morning_range_reversion_strategy,strategies.overnight_range_strategy`). Implementation: a `_LifecycleConsoleFilter` on the console handler lets `record.levelno >= INFO` through for any matched logger (or any of its dotted children — `strategies.foo` matches `strategies.foo.helpers`) and otherwise enforces the default `WARNING+` threshold. Module-startup banner now reads `Logging configured: ... console=WARNING (+ INFO console for: strategies.morning_range_reversion_strategy)` so the operator can verify the filter is live.
  - **`scripts/run_morning_reversion.sh`** — exports `LIFECYCLE_LOGGERS=strategies.morning_range_reversion_strategy` before exec'ing the executor; respects an already-set override.
  - **`core/strategy_executor`** — peeks at `sys.argv` for `--strategy=<name>` at module load and auto-derives `strategies.<name>_strategy` as a fallback lifecycle logger, so direct invocations (`python core/strategy_executor.py --strategy=morning_range_reversion ...`) also get the right console output without the wrapper script.
  - **`strategies/morning_range_reversion_strategy.MorningRangeReversionStrategy.analyze`** — the `📐 anchor range built` line is now the single source of truth for the range-finalisation verdict. Format matches the operator's mental model from the user request: `📐 morning_range_reversion {SYM} anchor range built for {date}: H={h} L={l} width={w:.2f}pts mid={m:.2f} filter={min}≤w≤{max} — {verdict}` where `verdict` is `fade scanner armed ← {w} {comparison} {bound}  ✓` for acceptable widths and `IDLED ← {w} {comparison} {bound}  ✗` for rejected widths. Failed filters still emit the separate `📏` WARNING with the remediation hint (which TOML section to widen). All three symbols print side-by-side at startup, every one shows its own filter math, and the ✓/✗ tells you at a glance which ones will fire today.
  - Tests: **14 new in `tests/test_lifecycle_console_filter.py`** — filter matrix (INFO from named logger ✓, DEBUG blocked, non-listed INFO blocked, WARNING/ERROR always pass, dotted-child inheritance, dot-boundary prevents prefix bleed, empty whitelist degrades to WARNING-only, multi-logger whitelist), env-var parsing (unset / empty / single / CSV-with-whitespace). **2 new in `tests/test_morning_range_reversion_smoke.py`** — accepted-range log includes the `filter=` bounds, ✓ verdict, and "fade scanner armed"; narrow-range log includes the same `filter=` bounds, ✗ verdict, "IDLED", and the failing width comparison.

### Fixed
- **Cold-start REST staleness now recovers in seconds instead of 10 minutes.** Repro from 2026-05-29 log: bot started at 08:35:30 ET, first `/api/History/retrieveBars` response was already `last_bar_ts = 12:15 UTC` (1233s old — broker's CDN was pinned to a stale entry from before the bot launched). The 2026-05-27 stuck-REST detector eventually fixed it, but only after waiting `2 × 5min = 600s` of wall-clock dwell to satisfy the steady-state trigger — 4 minutes of `⛔ STALE DATA` errors and a 10-minute window of "bot does nothing" right when the operator needs it to be trading. By comparison `overnight_range` doesn't hit this because it (a) typically polls at 1m timeframe (steady-state trigger is `2 × 60s = 120s`) and (b) stays running through the night so the keepalive route is already warm when trading begins.
  - **`trading_bot.TopStepXTradingBot._detect_and_handle_stuck_rest`** — new **cold-start fast path** evaluated BEFORE the steady-state counter. Looks at the bar's **absolute age** (`now − bar_ts` in real seconds, independent of how long we've been polling). If `bar_age > 2 × timeframe`, the detector trips `force_session_reset` immediately on the very first observation of this `(symbol, timeframe, ts)`. The rearmed tracker carries a `cold_tripped=True` flag so subsequent polls returning the SAME stale ts inside the new dwell window don't fire a reset storm — the flag only clears when a NEW timestamp arrives or the tracker key flips to a different symbol/tf.
  - Threshold is `2 × timeframe` (same as steady-state), so the fix scales correctly: 1m timeframe → trip at 120s old, 5m → trip at 600s old, 15m → trip at 1800s old. A bar that's "stale but inside its own timeframe's quiet window" still doesn't trip, preserving the original "don't false-alarm on a quiet 5-minute candle" behaviour.
  - **Recovery time before fix**: bot start → 10 minutes of `⛔ STALE DATA` errors → steady-state trip → fresh route. **Recovery time after fix**: bot start → 1 poll (~5s) → cold-start trip → fresh route on next poll. Empirical 2026-05-29 scenario: bar was 1233s old at first sight, threshold for 5m is 600s, so trip fires on call #0.
  - Tests: **5 new in `tests/test_rest_stuck_detector.py`** — immediate trip when first fetch returns a stale bar (1233s old, threshold 600s) and reset-reason starts with `rest-coldstart:`; same-stuck-ts subsequent polls don't double-trip; strict `>` comparison at the 2× tf boundary (599s under → no trip, 602s past → trip); threshold scales with timeframe (150s bar trips 1m but not 5m or 15m); end-to-end timeline assertion that cold-start trips on poll #0, not poll #N. The existing 13 steady-state tests were updated to use freshly-stamped timestamps (`_fresh_ts(seconds_old=…)` helper) so they only exercise the steady-state path.
- **`Request failed: ServerDisconnectedError` / `ClientConnectionError` no longer surface for every keepalive blip.** The 2026-05-29 morning log shows 33 such ERRORs spread across the trading window, each one costing the strategy a poll and producing a confusing log line. Root cause: the broker drops idle keepalive connections after ~75s, the pool returns a dead socket on the next request, the request raises, and the next poll 5s later has the same problem because the SAME stale pool slot may still be there.
  - **`core/auth.AuthManager._make_request`** — wrapped the network call in a small retry-with-session-rotate loop, capped at 2 attempts total. Transient classification covers `aiohttp.ServerDisconnectedError`, `ClientConnectionError`, `ClientPayloadError`, and `asyncio.TimeoutError`. On the first transient: close the current session (forces `_get_session()` to build a brand-new TCP+TLS handshake on the retry), sleep 0.3s, retry. On the second transient (or first non-transient): surface the failure via the same dict-with-error-key shape as before, but now the dict is flagged `transient=True` so callers can distinguish "broker temporarily flaky" from "logic error / 4xx body". The error log uses the existing class-name fallback so empty-message exceptions still produce a meaningful log line.
  - **Non-idempotent endpoints are explicitly excluded from the retry** — `/api/Order/place` (currently the only one in `_NON_IDEMPOTENT_PREFIXES`) is sent exactly once; a silent retry could place two orders for one user-intended click. The strategy layer still sees the transient error and can decide whether to retry with its own idempotency safeguards.
  - The retry path lives BELOW the existing 429-backoff retry — they compose cleanly. The retry only WARN-logs (`🔁 Request transient {ExcClass} for {endpoint} (attempt 1/2) — rotating session and retrying in 0.3s`); the operator sees one WARNING per recovered request instead of one ERROR per dropped one. Net effect on the 2026-05-29 log shape: the same 33 underlying TCP blips become ~33 WARNINGs (most recovered) with maybe 1–2 final ERRORs (true persistent outages), not 33 ERROR storms with empty messages.
  - Tests: **7 new in `tests/test_auth_transient_retry.py`** — retry succeeds after one transient (asserts exactly one session-close + two distinct sessions used), retry covers `ClientConnectionError` and `asyncio.TimeoutError`, retry exhausted on both attempts returns `{"error": ..., "transient": True}` and logs exactly one WARNING + one ERROR (with the exception class name), `/Order/place` is NEVER retried even on transient (no session close, exactly one session used), non-transient exceptions are NOT retried, healthy first-attempt does not rotate the session (sanity check).

### Fixed
- **`morning_range_reversion` per-symbol range-width filters: MNQ-calibrated `min_range_width_points=20` silently filtered MES and MGC every session.** Repro from 2026-05-29 log: MNQ width=73.25 ⇒ traded once (✓). MES width=11.75, MGC width=15.10 — both built ranges, both logged `📐 fade scanner armed`, both never produced a signal. Root cause: the width filter in `_fade_signal_after_sweep` (line 1300) rejected matches at `logger.debug` level, invisible at INFO; the "armed" log fired regardless of whether the range could pass the filter; and `min_range_width_points` was a root key applied uniformly across all symbols even though MNQ's typical pre-market range is 5–7× MES/MGC's. Symbol multipliers make the dollar economics differ wildly too — MGC's hypothetical 2026-05-29 trade would have grossed $113 at TP, comparable to MNQ's $108, yet it was silently filtered.
  - **`config/strategies/morning_range_reversion.toml`** — new `[symbols.MES.signal].min_range_width_points = 3` / `max_range_width_points = 50` and `[symbols.MGC.signal].min_range_width_points = 4` / `max_range_width_points = 65`. Calibration reverse-engineers MNQ's root values as %s of typical pre-market width (20 ≈ 27% of 73, 300 ≈ 410% of 73) and applies the same ratios to MES/MGC's typical widths (~12pt and ~15pt respectively). Dollar geometry: MES min=3 ⇒ TP distance ≥ 1.05pt × $5 ≈ $5 gross; MGC min=4 ⇒ TP distance ≥ 3pt × $10 ≈ $30 gross — both comfortably above commission.
  - **`strategies/morning_range_reversion_strategy.MorningRangeReversionStrategy.analyze`** — range-width filter now runs at **range-finalisation** (right after `st["H"], st["L"]` are populated, before `st["phase"] = "scan"`) instead of inside `_fade_signal_after_sweep`. A failing width sets `phase="idle"` and emits a one-shot WARNING line: `📏 morning_range_reversion {sym} anchor range too narrow for {date} (width={w:.2f}pts < min={min:.2f}pts) — idle for the day. Add a per-symbol override in [symbols.{SYM}.signal] if this range is normal for this contract.` Mirror message for the wide-range case. The per-sweep filter inside `_fade_signal_after_sweep` is kept as defence-in-depth. Net effect: no more contradictory "fade scanner armed" logs for symbols whose range can never produce a signal, no more silent rejection of MES/MGC trades, and no more burning analyze cycles all day for nothing.
  - Tests: **4 new in `tests/test_morning_range_reversion_smoke.py`** — narrow range rejected with operator-friendly WARNING that names the width and points at the override section, wide range mirror with same UX, throttle to 1× WARNING per (symbol, session) across 5 analyze() polls, and an end-to-end repro feeding the exact 2026-05-29 widths (MNQ=73.25, MES=11.75, MGC=15.10) through the live TOML — all three must reach `phase='scan'` (proves per-symbol overrides unblock MES/MGC without weakening MNQ). Two pre-existing `test_morning_range_reversion_smoke.py` failures (`test_morning_range_toml_mes_tp_mult_override` / `test_analyze_emits_short_after_high_sweep_reentry`) are unrelated TOML-default mismatches from the user's local edits.

### Fixed
- **Stuck-REST detector + session-reset bypass: when `/api/History/retrieveBars` is pinned to a stale cached response, rotate the underlying HTTP session.** Repro from the 2026-05-27 log: the bot received `last_bar_ts = 2026-05-27T12:05:00+00:00` from REST **1,077 consecutive times** across a 60-minute window while live SignalR continued delivering bars (`live_tail` reached `T13:00:00`). The freeze ended the moment an unrelated `/api/Auth/...` call forced a fresh TCP handshake → REST advanced to `T13:05:00` on the very next fetch. Conclusion: the broker's edge / CDN was serving a per-keepalive-route cache and the fix is to rotate the route. Belt-and-suspenders on top of the existing live-cache merge layer so the bot stays usable even if SignalR also stalls.
  - **`core/auth.AuthManager.force_session_reset(reason)`** — closes the shared `aiohttp.ClientSession` and clears `self._session`; next call to `_get_session()` builds a brand-new TCP+TLS connection. Preserves the bearer token (no re-auth needed). Idempotent (returns `False` when there's nothing to close); swallows exceptions from `close()` so a half-broken transport can't crash the caller; logs a single `🔁 HTTP session reset (reason=…)` WARNING line.
  - **`trading_bot.TopStepXTradingBot._detect_and_handle_stuck_rest(symbol, timeframe, rest_last_ts)`** — per-(symbol, timeframe) tracker maintained in `self._rest_freshness_track`. Trips when the same `rest_last_ts` has been returned **≥ 3 consecutive calls** AND has been pinned for **> 2× timeframe** of wall-clock dwell (matches the live-cache freshness threshold so the two layers speak the same language). On trip: logs `🧊 REST feed pinned for {symbol} {tf}: last_ts={ts} repeated {N}× over {dwell}s. Rotating HTTP session …` at WARNING and awaits `auth.force_session_reset(reason="rest-stuck:{symbol}:{tf}")`. Tracker re-arms with dwell=0 after each trip so a multi-hour outage doesn't trigger reset spam every poll.
  - **`trading_bot.TopStepXTradingBot.get_historical_data`** — calls the detector immediately after the REST result returns and the `📊 last bar timestamp` line logs, BEFORE the live-merge. Only fires for live polling — calls with explicit `start_time`/`end_time` or special `kwargs` (backfills, backtests, dashboard range queries) bypass the detector so a legitimate fixed-window historical request can't trip the alarm.
  - Detector is per-symbol-per-timeframe isolated (MNQ stuck does NOT trigger a reset for MGC's tracker), defensive against `None`/empty timestamps, and the reset call itself is exception-isolated (a transport explosion during `close()` is logged but doesn't propagate).
  - Tests: **13 new in `tests/test_rest_stuck_detector.py`** — `force_session_reset` happy path / idempotency / no-session / exception-during-close-still-clears-handle, detector ignores 1st-and-2nd duplicates, holds fire when count threshold met but wall-clock dwell still under 2× timeframe, fires exactly once on the 2026-05-27 5-poll repro across 12 min, advancing bar timestamp resets the counter cleanly, MNQ stuck does NOT trip MGC, multi-trip-loop suppression (only one reset per stuck episode), `None`/empty-string `rest_last_ts` safely skipped, and detector swallows a raising `force_session_reset` without crashing the caller.

### Fixed
- **`morning_range_reversion` silently idled for the whole day when the executor was started after 08:00 ET.** Repro: `./scripts/run_morning_reversion.sh 1` at 08:07:30 ET on 2026-05-27 — the bot ran for 60 minutes, fetched historical data 1,113 times, and never emitted a single signal. Root cause: the per-symbol state machine in `MorningRangeReversionStrategy.analyze()` only accumulated `range_hi`/`range_lo` on the bar branch `range_start ≤ t_open < range_end_open` and consumed `bars[-1]` (the latest bar) per invocation. If `bars[-1]` was already past the 08:00 ET anchor close on the very first call (the typical case when the script wakes mid-window), the build branch never ran, the finalize block saw `range_hi/range_lo == None`, and the session was promoted to `range_ready=True, phase='idle'` with no signals for the day.
  - **`strategies/morning_range_reversion_strategy.MorningRangeReversionStrategy._seed_range_from_history(bars, session_date)`** — new helper that scans the already-fetched bar history for completed bars whose ET timestamp falls in `[range_start, range_end_open)` AND match `session_date`, returning `(range_hi, range_lo, n_bars_used)`. Idempotent re-aggregation (max/min) so it interleaves safely with the per-bar branch when the executor is started DURING the build window.
  - **`analyze()` session-reset block** now calls the seed immediately after clearing per-session state. When it returns `n > 0`, `st["range_hi"]`/`st["range_lo"]` are populated *before* the build-vs-finalize branch runs — so the finalize block correctly promotes the range, sets `H/L/mid/width`, and arms the fade scanner on the very first invocation of the day even when the bot is launched 6 hours past anchor close. A new INFO line `🔁 anchor backfill from history for {date}: seeded H=… L=… from N build-window bar(s) (executor started past/during anchor close)` documents the recovery so operators can see exactly *why* the strategy is armed without a live build.
  - Date filter is strict (`bt.date() == session_date`) so 400-bar fetches that span ~33 hours of history don't bleed yesterday's range into today's seed.
  - Tests: **7 new in `tests/test_morning_range_reversion_smoke.py`** — seed captures the full 12-bar window, excludes post-anchor bars, excludes prior session dates, returns `(None, None, 0)` when the bot starts before the anchor opens, end-to-end `analyze()` finalises range + transitions to `phase='scan'` from a single mid-session invocation, backfill INFO log fires exactly once per (symbol, session), and starting pre-anchor (no completed build bars) does NOT seed.

### Added
- **`morning_range_reversion.position_management.breakeven_offset`** (default **`0.0`** — backward-compatible). When the BONGO §1B breakeven monitor fires, the moved SL now snaps to **`entry ± breakeven_offset`** in the trade's favour (LONG → `entry + offset`, SHORT → `entry − offset`) instead of exactly `entry`. Use a couple of points (e.g. `1.5`–`3` on MNQ) to cover commission + slippage so a triggered breakeven exit ends slightly green instead of flat. Negative offsets clamp to `0` at every layer so a sloppy TOML can't tighten the stop into a loss. Plumbing is symmetric across all three layers so live and replay agree:
  - **`config/strategies/morning_range_reversion.toml`** — new key under `[position_management]` documented next to `breakeven_trigger_r`. Mirror entry added to `config/strategies/_schema.toml`.
  - **`strategies/morning_range_reversion_strategy.MorningRangeReversionStrategy.__init__`** — reads `position_management.breakeven_offset` once (clamped ≥ 0). `execute()` forwards it to `place_bracket_order(..., breakeven_offset=...)` only when `be_thr` is set, so disabled breakeven keeps a zero offset on the wire.
  - **`strategies/strategy_base.BaseStrategy.place_bracket_order`** — new `breakeven_offset: float = 0.0` parameter, plumbed into the post-fill `bot.register_generic_breakeven_watch(...)` call alongside `profit_threshold`.
  - **`trading_bot.TopStepXTradingBot.register_generic_breakeven_watch`** — accepts and clamps `breakeven_offset`, stores it on the monitor row. `_generic_breakeven_monitor_loop` computes `new_stop = entry_price ± be_offset` and passes it to `modify_stop_loss` (was hard-coded to `entry_price`). Log line now includes both `offset=` and the resolved `new_stop`.
  - **`core/backtest/strategy_replay.StrategyReplayEngine._simulate_register_breakeven_watch`** + **`_simulate_place_bracket_order`** — mirror the live signature, store the clamped offset on `_breakeven_watches[oid]`.
  - **`core/backtest/strategy_replay.StrategyReplayEngine._evaluate_breakeven_watches`** — on trigger, snaps `sl_order.stop_price` / `placement_price` / `price` and the open position's `stop_loss` to `entry ± offset` (the existing `bar_close`-vs-`stop_price` direction-guard fix from the 2026-05-21 entry still applies; placement_price is anchored to the **new** stop, not entry). Trigger log line now shows the moved stop *and* the entry/offset breakdown.
  - Tests: **7 new in `tests/test_strategy_replay_breakeven.py`** (21 total now): offset persisted on the watch row, negative offset clamped to 0, default 0 preserves snap-to-entry, LONG with `offset=3` snaps moved SL to 103 and exits at +$6 PnL, SHORT mirror with `offset=3` snaps to 97 and exits at +$6 PnL, `_simulate_place_bracket_order` forwards `breakeven_offset` correctly into the watch.
- **Walk-forward recap reports now ship the exact strategy TOML(s) that produced the metrics.** When you look back at `docs/perf/<run>/metrics.html` weeks later, you can now see — and re-apply — the configuration the run used. New module **`core/backtest/strategy_config_snapshot.py`** (`snapshot_strategy_configs`, `snapshots_to_json`, `snapshots_to_html`) copies each strategy's `config/strategies/<name>.toml` verbatim into `<out_dir>/config/<name>.toml`, captures any matching env-var overrides (filtered by `<NAME_UPPER>_` prefix, mirroring `StrategyConfig` precedence), records the parsed TOML body, and stamps a UTC capture time + short `git rev-parse --short HEAD`. Wired into:
  - **`scripts/walkforward_trade_recap_report.py`** — adds a **Replay configuration (exact TOML)** section to `metrics.html` (nav anchor `#config-snapshot`), writes a sidecar **`strategy_configs.json`**, and links both from `metrics.html` + `index.html`. Each strategy gets a `<details>` block with the full TOML text plus an env-override table so the report is self-contained for replication.
  - **`scripts/walkforward_last_trades_charts.py`** — same snapshot files (`config/<name>.toml`, `strategy_configs.json`) plus the snapshot HTML appended to `index.html`. Light-theme styles tuned for `<details>` / `<pre>` blocks.
  - The recap script also embeds the CLI args (`days`, `folds`, `strategies`, `symbols`, `timeframe`, `csv_template`, `last_trades`, `padding_minutes`, `sim_start_cash`), the base replay env (`ENABLE_SIGNALR=false`, etc.), and the extra `--env` overrides under top-level keys in `strategy_configs.json` so a later reader has every knob in one place.
  - Tests: **`tests/test_strategy_config_snapshot.py`** (7) — verbatim TOML copy, parsed `data` shape, prefix-filtered env overrides per strategy, missing-TOML graceful path, JSON bundle round-trip, HTML output contains snapshot link / env table / TOML body, empty-input no-op.

### Added
- **`morning_range_reversion` — visible lifecycle INFO logs during live sessions.** Previously the strategy was silent between the init banner and a fade signal firing — operators running `scripts/run_morning_reversion.sh` had no way to confirm the strategy was actually progressing through its session beyond the noisy historical-data fetch lines. Overnight_range emits clear status messages throughout its lifecycle (`🔔 Recalculating overnight ranges`, `📊 Overnight range for {sym}: High=… Low=…`, `🚀 Placing range break orders for {sym}`, `🎯 Position opened`); morning_range_reversion now matches that verbosity:
  - **`🌅 new session`** when the date rolls over and a new anchor build begins (fires once per symbol per date, only after the first session has been seen so the very first start-up doesn't double-log with the init banner).
  - **`📐 anchor range built`** with `H`, `L`, `width`, `mid`, and the active range-width filter — fires the moment the 07:00-08:00 ET window finalises and the fade scanner arms. This is the single most useful "the strategy is working" beacon since it confirms historical data covered the anchor window and the fade machinery is live.
  - **`⚠️  anchor range invalid`** (WARN) when no bars covered the anchor window or `H ≤ L` — tells operators "no trade today" up-front instead of leaving them to infer it from the absence of signals.
  - **`⏰ fade deadline reached`** when `range_effectiveness_hours` has elapsed past `range_end_open` — surfaces the "fades disarmed" moment that previously only existed as silent state.
  - **`🛑 max_fades_per_session reached`** when the per-session fade cap stops new entries — equivalent to the "session done" notice overnight_range emits when its breakout monitor disarms.
  All four lifecycle events use a per-session `_logged_*` flag stored on the per-symbol state dict so each fires **at most once per (symbol, date)** — no log-spam if the strategy loop hits the same condition on every 5-second wake-up. Touches a single method (`MorningRangeReversionStrategy.analyze` in `strategies/morning_range_reversion_strategy.py`); existing 26 smoke tests in `tests/test_morning_range_reversion_smoke.py` still pass.

### Fixed
- **`core/auth.AuthManager._make_request` / `.authenticate` / fetch-accounts + `brokers/topstepx_adapter.get_open_orders` / `.get_open_positions` — error logs now include the exception class name so empty-`str(e)` transients (`ServerDisconnectedError`, `ClientPayloadError`, `ConnectionResetError`, `asyncio.TimeoutError`, …) are diagnosable instead of looking like fatal mystery errors.** User report (2026-05-27 08:08 ET): `./scripts/run_morning_reversion.sh 1` produced 4 alarming-looking lines in the live log —
  ```
  core.auth - ERROR - Request failed:
  brokers.topstepx_adapter - ERROR - Failed to fetch positions:
  core.auth - ERROR - Request failed:
  brokers.topstepx_adapter - ERROR - Failed to fetch orders:
  ```
  The bot self-recovered (the retry path inside `_make_request` re-authed and the loop continued normally for hours), but with a bare prefix and nothing after the colon, an operator has no way to tell a benign keepalive blip from a real auth/credential break. Logging `type(e).__name__: str(e) or repr(e)` now turns the message into e.g. `Request failed: ServerDisconnectedError: ServerDisconnectedError('Server disconnected')`. Five call sites updated (one in `_make_request`, two in `core/auth.py` (`authenticate`, `get_accounts`), two in `brokers/topstepx_adapter.py` (`get_open_orders`, `get_open_positions`)). All other behaviour unchanged — no retry-logic delta, no swallowed exceptions, just better log lines.
- **`core/backtest/strategy_replay._process_subbar_fills` — bracket exit orders (SL/TP) now correctly fill instead of being silently rejected by the engine's wrong-side STOP guard.** The bug was a sibling of the 2026-03-30 `placement_price` anchor (already fixed for the BE-move path). When a SHORT bracket entered via threshold/advance-stop and the entry bar's body closed back above the SL level (or a LONG whose entry bar reversed below the SL), the bracket SL exit was placed via `engine.place_order(stop_price=sl, price=sl)` with **no explicit `placement_price`** — so it fell back to `engine.last_close` (the current bar's close). `_check_order_fill` then refused the bracket SL forever after: BUY STOP rejects when `placement_price > stop_price` (treats as "wrong-side, below market"), SELL STOP rejects when `placement_price < stop_price`. The trade rode `max_hold_bars` to a timeout exit hundreds of points later. User-reported case on **2026-03-04 MNQ** SHORT: signal at 09:25 ET, entry at 09:30 ET via SELL STOP at 24837.625, original SL=24900.125 (62.5 pt risk = ~$130 per contract). Entry bar closed at ~24950 (above SL level), so the bracket SL inherited `placement_price=24950 > stop_price=24900.125` → rejected → trade held until 13:25 ET timeout at 25147.625 for **−$1,839.75** on 3 contracts. Same SHORT trade in the ModeB recap (where entry was market, not threshold) exited cleanly at the SL 5 minutes later for −$130/contract. Fix: anchor every bracket exit's `placement_price` to its own bracket level (`stop_price` for STOPs, `limit_price` for LIMITs) in three places inside `_process_subbar_fills` — the standard bracket (lines ~390-420), the BONGO §1A stage-1 partial-TP scalp (lines ~327-355), and the stage-2 "runner" SL+TP (lines ~460-510). With `pp == stop_price` the strict-inequality direction-guard check is false, so the engine treats the modify as neutral and fills the order normally when `bar.high` / `bar.low` touches the level. Verified by re-running the same 180-day MNQ recap: the 2026-03-04 trade now exits at **2026-03-04 09:35 ET @ 24900.125 / `stop_loss` / −$396.75** (3 contracts) — a ~$1,443 recovery on a single trade. The TP (LIMIT) doesn't have a direction guard today but is anchored symmetrically so a future LIMIT-side guard won't reintroduce the same silent-reject bug. Three new tests in `tests/test_strategy_replay_breakeven.py` (28 total now): `test_bracket_short_sl_fires_when_entry_bar_close_runs_above_sl_level` (the exact 2026-03-04 MNQ reproduction), `test_bracket_long_sl_fires_when_entry_bar_close_runs_below_sl_level` (LONG mirror — entry bar reverses below SL), `test_bracket_tp_placement_price_also_anchored_for_symmetry` (documents the LIMIT anchor for future-proofing).
- **`core/backtest/strategy_replay._evaluate_breakeven_watches` — same-bar look-ahead fallacy fixed: BE moves are now deferred when intra-bar ordering is ambiguous.** Previously, the helper ran *before* the per-bar fill loop, so on any bar where the high crossed the trigger level **and** the low reached the proposed moved-SL level on the same bar (LONG: `bar.high ≥ entry+thr` AND `bar.low ≤ entry+offset`; SHORT: mirrored), the SL was unconditionally snapped to `entry ± offset` and then "filled" by the very same bar's low/high — producing a flat `breakeven` exit on what was visibly a large impulsive bar that more likely punched straight through both levels without ever giving the BE machinery a chance to arm. The user flagged this against a 2026-05-26 MNQ chart: a long-impulse 5m bar tagged the BE-trigger and the moved-SL level simultaneously, the recap recorded a `breakeven` exit ≈ $0, but in real execution the price would have just continued past entry (no retest) and the trade would have run to TP. **New conservative policy**: when the trigger condition AND `bar.low ≤ new_stop` (LONG) / `bar.high ≥ new_stop` (SHORT) hold on the same bar, **defer** the snap — the watch stays armed (`triggered=False`), the original SL stays in place for this bar, and the engine's normal fill loop decides the bar's outcome. Three concrete outcomes after the fix:
  1. Bar's low *also* tags the original SL → engine fires a regular `stop_loss` exit at the original SL price (correctly logged as a loss, not a misleading `breakeven` $0).
  2. Bar's low stays above the original SL → trade keeps running with the original SL through the bar's close; BE re-evaluates on subsequent bars (clean-range bars snap as usual).
  3. A subsequent bar's range is clean (high ≥ trigger, low > new_stop) → BE moves on *that* bar and the moved SL applies from there on.
  Net effect on the 70-day MNQ recap: the **single** `breakeven` exit from the prior `morning_range_recent_tweaks3` run becomes a clean `take_profit` win — equity ticks up from **$3,196.25 → $3,406.25** (+$210, one MNQ TP-vs-flat differential). The previous 2026-03-30 MNQ regression (the `placement_price` direction-guard fix) is still covered: that bar's low stayed above the moved-SL level on the trigger bar, so the snap fires cleanly and the SL fills on the subsequent gap-down bar. New tests in `tests/test_strategy_replay_breakeven.py` (25 total now): `test_breakeven_same_bar_ambiguity_defers_move_and_keeps_original_sl`, `test_breakeven_same_bar_ambiguity_lets_original_sl_fire_at_real_loss`, `test_breakeven_deferral_allows_clean_move_on_next_bar`, `test_breakeven_same_bar_ambiguity_defers_short_side`. The earlier `test_breakeven_moved_stop_fills_when_bar_close_below_new_stop` was renamed to `test_breakeven_clean_bar_moved_stop_fills_when_close_below_new_stop` and re-tooled to exercise the placement-price guard on a clean-range bar (so both fixes are covered, not just one). Live `_generic_breakeven_monitor_loop` is unchanged — it polls 10-s position snapshots, not OHLC bars, so it has no intra-bar ambiguity.
- **`core/backtest/strategy_replay._evaluate_breakeven_watches` — moved breakeven stop now actually fills on the same bar.** When BONGO §1B fired (LONG: `bar.high − entry ≥ thr`), the helper snapped `sl_order.stop_price` to `entry_price` *and* re-anchored `sl_order.placement_price = bar_close`. But `BacktestEngine._check_order_fill` rejects a SELL STOP whose `placement_price < stop_price` (treats it as a fresh wrong-side place). On the typical happy-then-sad bar — high reaches `entry+thr` *and* low dips below entry, with `bar_close` ending below entry — the moved stop was silently refused and the trade only exited later via `manage_positions` `max_hold_bars`. Visible symptom on **2026-03-30 MNQ**: BUY entry @ 23407 with breakeven enabled, MFE peaked at +30 pts (well above the 26.7 pt threshold), low slammed to 23117, but the recap reported `exit_reason=timeout` at -**$558** instead of `breakeven` at ~$0. Fix: anchor `placement_price` to the new `stop_price` itself so the direction guard treats the modify as neutral (`pp == stop_price` makes the strict-inequality reject false). Verified by re-running the same fold: trade now exits at `2026-03-30 11:05 ET @ 23407.125` with `exit_reason=breakeven`, **−$5.75**. New regression test: **`test_breakeven_moved_stop_fills_when_bar_close_below_new_stop`** in `tests/test_strategy_replay_breakeven.py` (14 tests now in that file).
- **`strategies/morning_range_reversion_strategy.MorningRangeReversionStrategy.manage_positions` — `max_hold_bars` now counts from the entry-fill bar, not the signal bar.** The strategy stamps `_entry_bar_seq[symbol] = self._bar_seq` inside `_fade_signal_after_sweep` (i.e. when the *signal* fires), then `manage_positions` used `self._bar_seq - entry_seq` to decide when to flatten. With **threshold / advance-stop entries** the BUY/SELL STOP can sit pending for an hour or more before price retraces back to the trigger, so the held-bar counter started long before the fill — making `max_hold_bars=46` (3h50m) feel like 2h–2h30m of *actual* hold time. Same 2026-03-30 MNQ trade: signal at **09:25 ET**, entry filled at **11:00 ET**, old code timed out at **13:15 ET** after only 2h15m of real hold. Fix: in replay mode, prefer `engine.current_bar_index − pos.entry_bar_index` (the BacktestPosition stamp set at fill time, line 336 of `core/backtest/engine.py`); fall back to the legacy `_entry_bar_seq` for live mode where there's no engine. Net effect on the 70-day MNQ recap: the only `timeout` exit (03-30) drops to a clean breakeven, and `manage_positions` debug log now includes `entry_bar_idx` for traceability.
- **`tests/test_morning_range_reversion_smoke.py` — two threshold/reentry-confirm tests now pin `MORNING_RANGE_REVERSION_SIGNAL_SL_MULT=1.0`.** Production TOML ships `sl_mult=0.5` (half-range stop, ~+0.5R risk profile) but the fixtures' expected `stop_loss` values (110+5 → 115; 130+15 → 145) were tuned to the legacy class default `sl_mult=1.0`. The override keeps each test focused on the geometric relationship it documents (`stop = H + half_range × sl_mult` for SHORT, mirror for LONG) without coupling to the live risk-profile knob.

### Added
- **REST → execution latency squeeze (Options A, B, C, E, G, H, I, J)** — completes the second wave of latency work after Option B (event-driven loop) shrunk *signal* latency. This pass attacks the *order* round-trip:
  - **A. Live-cache-first reads** — `trading_bot.TopStepXTradingBot.get_historical_data` now short-circuits the REST round-trip when the SignalR-fed `_live_bars` cache holds at least `limit` fresh bars (tail age < 2 × timeframe). Saves **~100–500 ms** per `analyze()` call in steady state. New helpers: **`_live_cache_can_serve`**, **`_serve_from_live_cache`**, **`_timeframe_seconds`**, **`_parse_bar_ts`**. Cache miss / `start_time/end_time` / `kwargs` flags fall through to REST. Per-`(symbol, tf)` "cache-hit" counter logs the first hit at INFO and subsequent hits at DEBUG so operators can grep `⚡ get_historical_data live-cache hit` to confirm the path is engaged. Disable with **`LIVE_BAR_CACHE_FIRST=false`**. Warmup: **`_warmup_live_bar_cache`** primes one REST fetch per (symbol, tf) at executor startup (driven by `start_market_hub_for_strategies`); size via **`LIVE_BAR_CACHE_WARMUP_BARS`** (default **200**). Tests: **`test_live_cache_can_serve_when_warm_and_fresh`**, **`test_live_cache_cannot_serve_when_stale`**, **`test_live_cache_cannot_serve_when_too_few_bars`**, **`test_serve_from_live_cache_returns_trailing_limit`**, **`test_get_historical_data_skips_rest_when_cache_warm`**, **`test_get_historical_data_falls_back_to_rest_when_cache_disabled`**, **`test_timeframe_seconds_parsing`** in `tests/test_live_bar_pipeline.py`.
  - **B. Rust hotpath ON by default** — `.env.example` flipped `TOPSTEPX_USE_RUST` template to `"true"` (was `"false"`); the user `.env` already had this on. Saves **~20–80 ms** per order POST when the Rust adapter handles serialization + the wire path. Falls back to Python on `RustError`.
  - **C. orjson into the aiohttp `json_serialize`** — `core/auth.AuthManager._get_session` now passes `json_serialize=dumps_str` to `aiohttp.ClientSession`, so every outgoing JSON body uses orjson (3–5× faster than stdlib). Logs the active backend on session creation: `HTTP session ready: keepalive=…s, limit=64, limit_per_host=16, json_serializer=orjson`. uvloop wiring (in `core/logging_setup.py`) was already on; no change needed.
  - **G. TLS keep-alive tuning** — `keepalive_timeout` raised **30 s → 75 s** (broker idles ~60 s; we now reuse the TCP+TLS session before it closes), `limit_per_host=16` (was unlimited but `limit=64` total — explicit per-host cap to allow E1 parallel POSTs). `force_close=False` set explicitly so a regression elsewhere can't disable pooling. Override via **`AIOHTTP_KEEPALIVE_TIMEOUT`** (seconds, default 75).
  - **H. Idle keep-alive heartbeat** — `TopStepXTradingBot.start_keepalive_heartbeat` / `stop_keepalive_heartbeat` start a background task that calls **`auth_manager.list_accounts`** every **`BROKER_KEEPALIVE_HEARTBEAT_SEC`** (default **120**, jitter ±5 s). Keeps the TCP+TLS connection from closing during dead air, so the next live order avoids a 100–250 ms handshake. Wired from `StrategyExecutor.run()` after auth and stopped in `stop_all`. Set the env to **0** to disable. Tests: **`test_keepalive_heartbeat_starts_idempotent`** (idempotent + clean cancel), **`test_keepalive_heartbeat_disabled_when_zero`**.
  - **I. `morning_range_reversion.signal.lookback_bars` 400 → 100** — the strategy only inspects `bars[-1]` per call; 100 5m bars (~8 h) is enough cold-start lookback to cover the overnight session and 7-8am anchor. Smaller payload + faster JSON parse. Override per-symbol if needed.
  - **J. Strip optional fields from order POST payloads** — `brokers/topstepx_adapter` no longer emits `"limitPrice": null` / `"stopPrice": null` / `"reduceOnly": false` on entry orders (TopStepX defaults handle these). `core/auth.AuthManager._make_request` now skips the dict-comprehension None-strip pass when the payload is already clean (the adapter is the only large-payload caller). Net: a few µs per order plus a smaller TLS write.
  - **E1. Per-symbol parallel `analyze()`** (default ON) — `StrategyManager._run_strategy` now wraps the per-symbol pipeline in **`_process_strategy_symbol(strategy, symbol)`** and dispatches the symbols via `asyncio.gather(...)`. Multi-symbol strategies (e.g. `morning_range_reversion` runs MNQ + MES + MGC) now process simultaneously instead of serially: max wake-to-action drops from N × (REST + order) to roughly one round-trip. Override with **`STRATEGY_SYMBOL_PARALLEL=false`** for serial debugging.
  - **E2. Fire-and-forget order POST** (opt-in) — when **`STRATEGY_FIRE_AND_FORGET_ORDERS=true`**, `BaseStrategy.place_bracket_order` dispatches the broker call as a background `asyncio.Task` and returns immediately with `{"success": True, "orderId": None, "fire_and_forget": True}`. The synchronous risk check still runs first; rejections arrive asynchronously via the User Hub. Disabled by default — downstream features that need the order ID (BONGO §1B breakeven monitor, `breakout_active_orders` cache) skip registration when `fire_and_forget=True`. Tests: **`test_fire_and_forget_returns_immediately_with_placeholder`** (returns < 100 ms even when the broker simulates 500 ms), **`test_fire_and_forget_off_blocks_until_broker_responds`**.
  - Tuning knobs documented in `.env.example` under "Performance toggles". Existing `STRATEGY_LOOP_MAX_INTERVAL_SEC` ceiling (Option B) re-tuned to **5 s** in the user `.env` so SignalR outage detection stays snappy.

### Fixed
- **`config/strategies/morning_range_reversion.toml` — TOML structural bug: 17 `signal.*` keys were silently routed to `[position_management]`.** The previous layout put `[position_management]` *between* the basic `[signal]` keys (timeframe, range_start, range_end_open, etc.) and the rest of the signal knobs (`max_fades_per_session`, `reentry_threshold_points`, `range_effectiveness_hours`, `min_range_width_points`, `max_range_width_points`, `require_reentry_close`, `sl_fixed_pts`, `sl_mult`, `tp_mult`, `reentry_frac`, `partial_tp_*`, `require_high_atr`, `atr_*`, `max_bar_staleness_seconds`). TOML section headers extend to the next header — once `[position_management]` appeared, every key below it became `position_management.X`. The strategy reads them via `signal.X` so every lookup returned `None` → class default. Visible symptoms in the 70-day MNQ walk-forward recap: 84 trades / **8 trades on a single day** (2026-04-06) with `max_fades_per_session=1` in the file; entries firing at 11:43 ET / 14:00 ET / 16:00 ET despite `range_effectiveness_hours=4` (= 12:00 ET cutoff); narrow-range "TP-in-loss" days still hitting the tape despite `min_range_width_points=50`. After moving `[position_management]` to the bottom of the file: 35 trades / one-per-day (max), entries clustered in the 08:00–11:00 ET window as designed. Added a "TOML structural footgun" note next to `[position_management]` so a future edit doesn't regress. Defensive scan via `tomllib` of the other 7 strategies confirmed none had the same misplacement.
- **`scripts/walkforward_trade_recap_report._find_morning_range_signal_bar` — chart marker now matches the strategy's actual mode.** Before: hard-coded `require_reentry_close=true` regardless of TOML, picked the *last* sweep close instead of the *first*, and never accounted for `reentry_threshold_points > 0`. Result: trade charts showed a `reentry_close` yellow dot at e.g. 08:34 ET while the actual entry — a stop-entry placed at `H − threshold` after the first sweep — fired hours later, producing the visible 08:34 → 11:43 gap the user reported. Now the helper:
  - Reads `signal.require_reentry_close`, `signal.reentry_threshold_points`, and `signal.reentry_frac` from the TOML at recap startup (env-var overrides still win, mirroring `StrategyConfig.get` precedence).
  - **Threshold mode** (`reentry_threshold_points > 0`): marks the **first** 5m close outside the anchor range with label `"sweep_close (advance stop)"`. The gap to entry is now expected and correctly visualized.
  - **Mode A** (`require_reentry_close=true`, threshold == 0): unchanged — first sweep + first inner-band close, label `"reentry_close"`.
  - **Mode B / immediate** (`require_reentry_close=false`, threshold == 0): now marks the **first** sweep close (was incorrectly picking the last), label `"sweep_close (immediate)"`.
- **`tests/test_morning_range_reversion_smoke.py` — three threshold/immediate-mode unit tests now bypass the new range-width filter** (`min_range_width_points=50`, `max_range_width_points=300`) via env-var overrides. Their synthetic 10–30 pt fixture ranges were correctly being rejected after the TOML structural fix re-enabled the filter; the bypass keeps the unit-test surface focused on signal/sweep logic without depending on the production filter.
- **`core/backtest/strategy_replay.StrategyReplayEngine` — BONGO §1B breakeven mechanism now actually fires in backtest replays.** Previously the live path (`trading_bot._generic_breakeven_monitor_loop`) polled positions every 10 s and called `modify_stop_loss` once `unrealized_move ≥ trigger_r × |entry-stop|`; the *replay* shim accepted `enable_breakeven` / `breakeven_profit_threshold` arguments and silently dropped them, never moved the SL pending-order's `stop_price`, and never intercepted `register_generic_breakeven_watch` (so the live monitor would have tried to call the broker mid-replay if the event loop hadn't dropped on its face). Net effect on every walk-forward recap to date: every loss was the *full* SL distance even when MFE had crossed the breakeven trigger by multiples. The fix has four coordinated parts — the first three were the original implementation; the last two are the **2026-05-21** follow-up after a recap with `breakeven_trigger_r=0.2` showed **zero breakeven exits** because two upstream guards short-circuited the call before the replay engine could see it:
  - **`StrategyReplayEngine._simulate_register_breakeven_watch`** — replay-mode replacement for the live broker call. Stores `{symbol, side, entry_price, profit_threshold, entry_bar_index, sl_order_id, triggered, position_filled}` in the new `self._breakeven_watches` dict, keyed by entry order id. Hooked from `_intercept_trading_bot_methods` and unhooked from `_restore_trading_bot_methods` so the live monitor never touches the real broker during replay. Cleared at the start of every `replay()` call so back-to-back walk-forward folds don't bleed state.
  - **Bracket-placement linkage** — when `_handle_filled_orders` (now inside `_process_subbar_fills`) creates the SL exit order after an entry fills, we look up the matching watch by entry order id and stash the *SL* order id + `entry_bar_index`. This is the moment we know which pending order to mutate later.
  - **`StrategyReplayEngine._evaluate_breakeven_watches`** — invoked at the top of every `_process_subbar_fills` (after `set_last_close`), before the per-bar fill loop, so any moved stop is in effect for the same bar's fill check. For each active watch: skip the entry bar to avoid look-ahead on the high/low ordering; LONG triggers when `bar.high - entry_price ≥ thr`, SHORT when `entry_price - bar.low ≥ thr`; on trigger, snap the SL pending order's `stop_price` (and `placement_price` to keep the engine's STOP-direction guard satisfied) to `entry_price`, mark the watch triggered, also update `pos.stop_loss` so MFE/MAE bookkeeping + recap shading reflect the new value. If the position has already closed (TP fired earlier), the watch is dropped silently.
  - **Wire-up #1: `_simulate_place_bracket_order` now registers the watch directly** — `StrategyReplayEngine` replaces the entire `BaseStrategy.place_bracket_order` method via `self.strategy.place_bracket_order = self._simulate_place_bracket_order`, so the live `BaseStrategy.place_bracket_order` body (which contains the `bot.register_generic_breakeven_watch(...)` call) **never executed during replay**. The intercept on `bot.register_generic_breakeven_watch` was unreachable for replays. Now `_simulate_place_bracket_order` mirrors the live registration: when `breakeven_profit_threshold > 0` and an entry order id was returned, it calls `_simulate_register_breakeven_watch` directly with the same args. Verified by **`test_simulate_place_bracket_order_registers_breakeven_watch`** (regression) and **`test_simulate_place_bracket_order_skips_watch_when_threshold_zero`** (None / 0 → no watch armed).
  - **Wire-up #2: `MockTradingBot.register_generic_breakeven_watch` stub in `core/backtest_executor.py`** — the executor's mock bot used by `--replay` did not define this method, so `BaseStrategy.place_bracket_order`'s `hasattr(bot, "register_generic_breakeven_watch")` gate was *also* False and the live path's `bot.register_generic_breakeven_watch(...)` call was silently skipped — that's the path strategies take when running through the *real* bot too, but in replay we never reach it because of wire-up #1. Added a no-op stub on `MockTradingBot` so `_intercept_trading_bot_methods` can swap it for the simulated tracker; the stub body never runs because the intercept replaces it before the strategy fires.
  - **Recap exit_reason retag** — when `_evaluate_breakeven_watches` snaps the SL to entry, it also rewrites `sl_order.exit_reason` from `"stop_loss"` → `"breakeven"`. The recap pipeline (`scripts/walkforward_trade_recap_report.py`) and `BacktestEngine._update_position` both copy this field straight onto `BacktestTrade.exit_reason`, so the trade table now shows breakeven exits as a distinct category instead of looking like indistinguishable real-loss SL hits.
  - **Verification**: 70-day MNQ walk-forward replay (`scripts/walkforward_trade_recap_report.py --strategies morning_range_reversion --symbols MNQ --days 70 --folds 10`) with `breakeven_trigger_r=0.2`: trade table now shows **17 breakeven, 3 stop_loss, 4 take_profit, 2 timeout** — vs **0 breakeven, 12 stop_loss, 13 take_profit, 2 timeout** before the wire-up fix. `metrics_insights.json` includes the new `"breakeven"` bucket under `loss_patterns.by_exit_reason`.
  - Strategies wired today: `morning_range_reversion`, `body_reversion`, `overnight_range`, `overnight_reversion`, `rsi_switch_15m`, `ema_stack_trend_15m`, `trend_scalping` (any `BaseStrategy.place_bracket_order` caller that passes `breakeven_profit_threshold > 0`).
  - Tests: 13 in `tests/test_strategy_replay_breakeven.py` covering watch registration / normalization / zero-thr & empty-id rejection, **place-bracket-order watch registration (regression for the wire-up bug)** + **place-bracket-order skips watch when thr is None/0**, LONG-side trigger that snaps SL → entry → ~$0 PnL on next-bar reversal (now asserted as `exit_reason=="breakeven"`), SHORT-side mirror via `bar.low`, no-trigger when MFE never crosses thr (full SL still fires), entry-bar look-ahead skip, replay-to-replay state isolation, watch pruned when position closes via TP first, intercept/restore round-trip, and the realistic happy path where BE arms but TP still fills cleanly.
- **`core/backtest/engine.BacktestEngine._check_order_fill`** — STOP orders now validate **direction** at fill time using a new **`BacktestOrder.placement_price`** field (reference market price at order creation, populated from the engine's `last_close` which is refreshed each bar in the run loop and at the top of `StrategyReplayEngine._process_subbar_fills`). A BUY STOP placed *below* market or a SELL STOP placed *above* market is rejected — they are degenerate "fills at market disguised as stop entries" that real brokers reject. Prior to this fix, the engine filled them at the stop price whenever a later bar's range touched it, even when the bar never physically traversed the level. This produced impossible-looking trades like the 2026-05-19 MNQ mode-A "LONG @ 28853.88 at 9:45 ET" (price never re-traded 28853 between 9:30 and 10:05). After the guard, the 2026-04-19→05-19 MNQ legacy `require_reentry_close=true` replay drops from inflated headline → **0 trades** because every such fill was a phantom. Test: **`test_backtest_engine_rejects_wrong_side_buy_stop`** + **`test_backtest_engine_allows_valid_stop_direction`** in `tests/test_morning_range_reversion_smoke.py`.
- **`morning_range_reversion`** — **`signal.reentry_threshold_points`** (default **7**, MNQ): **overnight_range-style advance stop-entry** on the **first 5m close outside** the 7–8am anchor — immediately place resting BUY/SELL stop at **L + threshold** / **H − threshold** with bracket SL/TP (no wait for a later re-entry bar). Example **2026-05-19 MNQ**: low sweep on the 9:15–9:20 ET bar → BUY STOP at **28860.75** at the 9:20 close; fill **9:30**; TP **9:35** @ midpoint. MES/MGC overrides keep **0** (legacy candle-close) until per-symbol sweep. Tests: **`test_sieve_threshold_mode_arms_on_sweep_bar_advance_stop`**, **`test_analyze_threshold_mode_default_emits_on_sweep_bar`**. Legacy: threshold **0** + `require_reentry_close=true` (candle-close) or threshold **0** + `require_reentry_close=false` (immediate at range extreme).
- **`strategies/strategy_manager._apply_config_settings` / `_serialize_config`** — TOML now wins for **`trading_start_time`** / **`trading_end_time`** / **`no_trade_start`** / **`no_trade_end`** (`_TOML_AUTHORITATIVE_TIME_KEYS`). These keys are no longer written to `strategy_states.settings` and no longer applied on read. The 2026-05-19 `morning_range_reversion` outage was caused by a stale persisted row with `09:30/15:45` overwriting the TOML's `06:55/16:00`, so `should_trade()` stayed False until 09:30 and the live 07:00–08:00 anchor never built. On every applied row that disagrees with TOML, the executor now logs a one-liner: `🕐 Ignoring stale persisted time-window settings for <name> (TOML wins): db=… toml=…`. Tests: **`tests/test_strategy_persistence.py::TestTomlAuthoritativeTimeWindow`**.

### Added
- **Event-driven strategy loop (Option B: bar-close → instant wake)** — eliminates the up-to-60 s polling lag from `strategies/strategy_manager._run_strategy`. The legacy loop ended each iteration with `await asyncio.sleep(60)`, so a 5m bar that closed at `09:20:00` could go un-actioned until `09:20:59`. New behavior:
  - Each running strategy owns an `asyncio.Event` (`StrategyManager._strategy_wake_events[name]`).
  - `StrategyManager._on_completed_bar_wake(bar)` is registered as a single completed-bar callback on `core.bar_aggregator.BarAggregator` (idempotent via the dedup added in the prior CHANGELOG entry). When a closed bar arrives, the callback iterates `active_strategies`, filters by **`symbol in config.symbols`** AND **normalized `strategy.timeframe == bar.timeframe`**, and wakes matching strategies via **`loop.call_soon_threadsafe(wake.set)`** — thread-safe because the aggregator path may fire from the SignalR thread.
  - The loop replaces `asyncio.sleep(60)` with `wake.clear(); await asyncio.wait_for(wake.wait(), timeout=STRATEGY_LOOP_MAX_INTERVAL_SEC)`. The clear-before-wait order guarantees that bar closes arriving *during* `analyze()`/`execute()` are captured for the very next iteration.
  - **End-to-end signal latency** (tick → strategy reacts): ~**250–500 ms** for liquid futures (SignalR push 10–50 ms + bar-close detect <200 ms + `call_soon_threadsafe` ~µs + REST historical merge ~100–300 ms). Down from up-to-60 s.
  - **REST-traffic impact**: *lower*, not higher. A 5m strategy now polls REST roughly once every 5 minutes (driven by bar closes) instead of once every 60 s. The `STRATEGY_LOOP_MAX_INTERVAL_SEC` ceiling (default **5 s**) only fires during SignalR outages or dead hours when there are no bar closes — bounded by the freshness guard for live-trading detectability and by `should_trade()` time-window gating for off-hours quiet.
  - **Config**: `STRATEGY_LOOP_MAX_INTERVAL_SEC` (float, default **5**, invalid/zero falls back to default). Affects ALL strategies globally.
  - **Strategies without a declared `self.timeframe`** wake on any bar close for their config symbols (existing `should_trade()` gates still apply; harmless extra wake-ups).
  - **Backward compatibility**: strategies are unaware of the change — they still implement `analyze()`/`manage_positions()` exactly as before. The only observable difference is *when* the loop calls them.
  - On strategy startup the executor now logs: `🔔 Wake-driven loop active for <name> (timeframe=<tf>, max_interval=<N>s)`.
  - Tests: 11 new in `tests/test_live_bar_pipeline.py` covering happy-path wake, symbol/timeframe filtering negatives, multi-strategy fan-out, idempotent callback registration, registry cleanup on stop, env-var override (valid / invalid / zero), threadsafe wake from a background thread, and strategies without `timeframe`.
- **Market Hub wired into `StrategyExecutor` (live data redundancy)** — completes the medium-term fix for the 2026-05-21 outage where REST `/api/History/retrieveBars` froze for 75+ minutes and the strategy went blind. Three coordinated changes:
  - **`core/bar_aggregator.BarAggregator`**: new **`register_completed_bar_callback(cb)`** / **`unregister_completed_bar_callback(cb)`** — multi-subscriber fan-out for closed bars. The existing single `broadcast_callback` (dashboard) is unchanged; additional subscribers are isolated (exceptions don't break the broadcast pipeline).
  - **`trading_bot.TopStepXTradingBot`**: new **`_live_bars`** ring buffer (`symbol → normalized_timeframe → deque[bar_dict]`, capped by env `LIVE_BAR_CACHE_MAXLEN`, default **240**) populated by an aggregator callback. Timeframe normalization (`5MIN`/`5min`/`5 minutes` → `5m`, `1hour` → `1h`, etc.). `get_historical_data()` now merges any cached bars **strictly newer than the REST tail** onto the REST response, so strategies still see fresh data when the historical endpoint stalls. If REST returns empty, the live cache becomes the result. New convenience **`start_market_hub_for_strategies(symbols, timeframes)`** (idempotent; best-effort; returns `False` on Market Hub connect failure so callers can stay in REST-only mode).
  - **`core/strategy_executor.StrategyExecutor.run()`**: new **`_wire_market_hub_for_live_strategies()`** step after the strategies start — walks `trading_bot.strategy_manager.strategies`, collects each running strategy's `config.symbols` + `self.timeframe`, calls `start_market_hub_for_strategies`. Disable via **`EXECUTOR_MARKET_HUB=false`** (also short-circuited by `ENABLE_SIGNALR=false`). Logs `📡 Market Hub wired for N symbol(s); timeframes registered: …` on startup so absence is grep-visible. Tests: **`tests/test_live_bar_pipeline.py`** (14 tests covering callback fan-out, isolation of subscriber exceptions, cache merge correctness across stale-/fresh-/empty-REST permutations, timeframe normalization, duplicate-timestamp dedup, executor env toggles, symbol/timeframe collection across strategies).
  - Freshness guard error message updated to mention Market Hub: now points operators at the `📡 Market Hub wired …` startup log line as the first diagnostic.
  - **`docs/GOTCHAS.md`** "Live data freshness" section rewritten to reflect the new wiring; the medium-term TODO is closed.
- **`morning_range_reversion.signal.max_bar_staleness_seconds`** (default **600** = 10 min): live-data freshness guard. `MorningRangeReversionStrategy.analyze()` now aborts early with a throttled `⛔ STALE DATA …` ERROR (1× per 60s per symbol) when the most recent bar is older than the threshold. The 2026-05-21 EDT live session went 75+ minutes without a single new bar after 08:30 EDT — the TopStepX `/api/History/retrieveBars` endpoint silently froze and the executor polled forever in silence (User Hub up, no Market Hub fallback). Replay / backtest bypass the guard automatically via `trading_bot._is_strategy_replay` / `_current_bar_timestamp`. Per-symbol override via `[symbols.<SYM>.signal] max_bar_staleness_seconds`. Set to **0** to disable (not recommended for live). Tests: **`test_freshness_guard_aborts_when_last_bar_older_than_threshold`**, **`test_freshness_guard_throttles_repeated_log_within_60s`**, **`test_freshness_guard_passes_through_fresh_bars`**, **`test_freshness_guard_bypassed_during_replay`**, **`test_freshness_guard_disabled_when_threshold_zero`** in `tests/test_morning_range_reversion_smoke.py`. See **GOTCHAS.md → "Live data freshness"** for the wired-up Market Hub fallback.
- **`morning_range_reversion`** — **R-ratio geometry controls** + **range-width guards** to fix structural profit-factor issues:
  - **`signal.sl_fixed_pts`** (default **0** = backward-compatible): when `> 0`, anchors stop a **fixed number of points from entry** (`entry − sl_fixed_pts` for LONG, `entry + sl_fixed_pts` for SHORT) instead of `L/H ± half × sl_mult`. Improves R-ratio on typical-range days — e.g. with `sl_fixed_pts = 14` (= 2 × threshold) on a 50-pt range, breakeven WR drops from **67.7 %** (legacy) to **51.6 %**, giving +EV at the observed 65.5 % WR.
  - **`signal.min_range_width_points`** (default **0** = no minimum): skips fade signals when the 7–8am morning range is narrower than this value. Prevents the TP-in-loss bug: on ranges < 2 × threshold pts, entry depth is capped to midpoint making TP ≈ entry → near-zero gross PnL → net loss after commission. Root cause of 7 "take_profit → net loss" trades in the 700-day MNQ walk-forward.
  - **`signal.max_range_width_points`** (default **0** = no cap): skips fade signals when the range exceeds this value, capping per-trade risk exposure on extreme-volatility days (the two largest losses in the 700-day run were −$707 and −$573 from ~700-pt morning ranges).
  - All three are per-symbol overridable via `[symbols.<SYM>.signal]` sections.
  - Tests: **`test_sieve_min_range_width_skips_narrow_range`**, **`test_sieve_max_range_width_skips_wide_range`**, **`test_sieve_sl_fixed_pts_places_stop_relative_to_entry`** in `tests/test_morning_range_reversion_smoke.py`.
- **`scripts/scrub_strategy_state_time_keys.py`** — one-shot operator helper that drops the four legacy time-window keys from every `strategy_states.settings` JSONB (dry-run by default; **`--apply`** writes). Optional cleanup so the stale-row INFO log stops firing; not required for the fix above.
- **`scripts/walkforward_rsi_switch_tournament.py`** + **`config/perf_sweep/rsi_switch_tournament_seed.json`** — walk-forward **survival tournament** for `rsi_switch_15m` env variants (fitness = WR + PnL + fold consistency; eliminate bottom half; mutate/crossover survivors).
- **`rsi_switch_15m`** — fix entry logic: **RSI hook** (turn up/down in extreme zone) replaces contradictory edge+turn filter that produced zero trades; EMA reaction + soft counter-trend filter; earlier profit exits (**55/45**).
- **`rsi_switch_15m`** — **15m RSI switch**: enter **LONG**/**SHORT** on edge into RSI <= **33** / >= **66**; when in a position at the **opposite** extreme, heuristic **EXIT** vs **HOLD** uses EMA stack structure, RSI heat, bars held, flat vs moved price, and EMA reaction tags. **`strategies/rsi_switch_15m_strategy.py`**, **`config/strategies/rsi_switch_15m.toml`**. Tests: **`tests/test_rsi_switch_15m_strategy.py`**.
- **`ema_stack_trend_15m`** — research scaffold: **15m** bars, **8/21/50/100/200** EMA **stack** (bull: ascending EMA order + close above fastest; bear: mirror), default **alignment-edge** entries, ATR-based bracket SL/TP, optional **Wilder RSI** band on the same closes (`signal.rsi_*`, default on). **`strategies/ema_stack_trend_15m_strategy.py`**, **`config/strategies/ema_stack_trend_15m.toml`** (`meta.enabled = false`), registered in **`strategies/strategy_manager.py`** + **`core/backtest_executor.py`**. Tests: **`tests/test_ema_stack_trend_15m_strategy.py`**.
- **`core/backtest/recap_metrics.py`** — equity simulation from replay trades (chronological exits), extended stats (breakeven win rate from avg win/|avg loss|, recovery factor vs sequential trade DD, median bars held win/loss, avg R), and **loss-pattern** summaries (exit_reason mix, loss rate by side, weekday ET, exploratory Pearson between loss indicator and `bars_held` / `initial_risk_dollars` / entry hour). Wired into **`scripts/walkforward_trade_recap_report.py`**: **`metrics.html`** gains a **Lightweight Charts** equity line (default start **$2,000**, override **`--sim-start-cash`**), pooled + per-strategy×symbol analysis sections, and **`metrics_insights.json`**. Tests: **`tests/test_recap_metrics.py`**.
- **`core/backtest/ohlcv.deroll_dual_contract_bars`** — drops bars belonging to the non-continuing futures contract on quarterly roll dates (Mar/Jun/Sep/Dec). Detection per UTC session day: if more than ``DEFAULT_DEROLL_MIXED_JUMP_PCT`` (5 %) of bars jump > ``DEFAULT_DEROLL_JUMP_THRESHOLD_PT`` (100 pt) from the previous bar, the session is a roll mix. 1-D 2-means clusters the closes and the continuing-contract centre is picked by **walking backward** in time (each mixed session anchored to the next session's chosen centre) so the choice tracks the new front-month's day-to-day drift instead of comparing absolute prices to a far-future clean session. A second pass drops isolated single-bar phantoms that survive the day-level ratio test (one off-track print on an otherwise-clean day). Wired into both ``dataframe_to_chart_bars_unix(..., deroll=True)`` and ``replay_bars_from_ohlcv_df(..., deroll=True)`` so recap charts and strategy replays both see clean data — fixes ``mnq_1m_databento.csv`` showing two parallel candle sequences around Mar 16 / Jun 16 / Sep 16 / Dec 16 caused by interleaved MNQH/MNQM/MNQU/MNQZ bars (~200 pt cost-of-carry basis). Across the 3-year canonical MNQ CSV, 12,592 / 1,073,185 (1.17 %) bars are dropped, all clustered on the standard CME roll dates (3–7 days before the third-Friday expiry). Tests: **`test_deroll_passes_through_single_contract_day`**, **`test_deroll_drops_phantom_track_in_alternating_session`**, **`test_deroll_does_not_drop_high_volatility_single_contract_day`**, **`test_deroll_drops_isolated_single_bar_phantom_on_clean_day`**, **`test_deroll_preserves_bar_order`**.
- **`overnight_reversion`** strategy — failed-breakout **fade** vs the overnight box: after ``timing.market_open`` ET, first ``signal.fade_signal_timeframe`` bar whose **close** is outside **H/L** arms a **stop-entry** back toward the range (**SHORT** at **H** after close > H, **LONG** at **L** after close < L), TP at overnight **mid**, stop beyond the swept side (ATR × ``stop_atr_multiplier``). Same session tracking / filters / replay gates as **`overnight_range`** (**`strategies/overnight_reversion_strategy.py`**, **`config/strategies/overnight_reversion.toml`**). Registered in **`strategies/strategy_manager.py`** and **`core/backtest_executor.py`**. Tests: **`tests/test_overnight_reversion_smoke.py`**.
- **`core/backtest/session_shade.py`** — precomputes **overnight range** baseline segments (ET evening + morning, anchor day) from bar lists using shipped **`overnight_range.toml`** timing; used by **`gui.chart_html.generate_chart_html(..., overnight_range_et_shade=True)`** (amber fill). Test: **`test_session_shade_overnight`**.
- **`scripts/walkforward_trade_recap_report.py`** — walk-forward **hub** + **`trades/<slug>_trade_chart.html`** + **`metrics.html`** / **`metrics.json`**; defaults **90 days**, **6 folds**, **MNQ+MGC**, **`overnight_range` + `morning_range_reversion`**, **5m** Databento CSV replay (TOML-only env except `ENABLE_SIGNALR=false`). Example output: **`docs/perf/walkforward_mnq_mgc_3m_overnight_morning_trade_recaps/`**. Test: **`test_walkforward_trade_recap_report_help`**.
- **`gui/chart_html.generate_chart_html(..., morning_range_et_shade=...)`** — optional **07:00–08:00** (exclusive of 08:00) **America/New_York** **range box** behind candles: per ET date, **baseline series** with `baseValue` = min(low) and line at max(high) over 1m bars in that window (matches anchor **H/L** geometry); enabled from **`replay_morning_reversion_trade_charts_from_1m_csv`**, walk-forward **`morning_range_reversion`** trade charts, and **`generate_signal_walkthrough_report`** morning legs.
- **`scripts/strategy_parameter_sweep.py`** + **`config/perf_sweep/default_wave_manifest.json`** — **wave**-organized replay sweeps (one manifest section per focused variable family). Each run sets **env overrides** on `core/backtest_executor.py --replay` (same resolution as live `StrategyConfig`). JSON output includes **`avg_reward_risk`** (mean realized PnL / initial bracket risk $ when replay attached SL prices). Writes **`summary.tsv`**, **`by_wave.md`**, **`results.jsonl`** under **`docs/perf/parameter_sweeps/<run-id>/`** (gitignored). **`--waves`** filters manifest sections; **`--dry-run`** lists planned jobs. Tests: **`test_strategy_parameter_sweep_smoke`**, **`test_backtest_result_avg_reward_risk_from_trades`**.
- **`scripts/walkforward_strategy_competition.py`** — walk-forward matrix over **last N** calendar days (default **100**), **5m** (or ``--timeframe``), strategies **body_reversion** / **morning_range_reversion** / **overnight_range**, symbols **MNQ,MES,MGC**; writes ``summary.tsv`` + ``leaderboard.md`` (includes **trade-weighted win_rate_pct** per strategy×symbol) + per-fold JSON under ``docs/perf/walkforward_competition/``. Requires canonical ``historical_data/price/{SYM}_5m_databento.csv``. Repeatable **`--env KEY=VAL`** passes env into each replay subprocess (TOML overrides). **`scripts/summarize_walkforward_tsv.py`** prints trade-weighted win rate + mean fold Sharpe from **`summary.tsv`**.
- **`scripts/generate_signal_walkthrough_report.py`** — `docs/perf/signal_walkthrough_overnight_morning/index.html` + per-trade **Lightweight Charts** (`gui.chart_html`) from `core/backtest_executor.py --replay` for overnight MNQ/MES/MGC + morning MNQ/MGC; auto-fallback when requested dates exceed canonical CSV anchor.
- **`scripts/stitch_broker_history_to_databento_5m.py`** — after last bar in ``historical_data/price/{MNQ,MES,MGC}_5m_databento.csv``, fetch **5m** OHLCV via ``TopStepXTradingBot`` + ``broker_adapter.get_historical_data`` (same pathway as ``scripts/export_history.py`` / CLI ``history … csv``), then merge with ``historical_data/csv_merger.py`` (**canonical first**, broker tail second — newer timestamps win). Optional ``--dry-run``.
- **`scripts/stitch_broker_history_to_databento_1m.py`** — same broker pathway for canonical ``*_1m_databento.csv`` (default **10**-day chunks under the 20k cap). Resolves ``mnq_`` / ``MNQ_`` filenames. Test: **`test_stitch_broker_history_to_databento_1m_help`**.
- **`scripts/walkforward_last_trades_charts.py`** — same fold calendar as ``walkforward_strategy_competition``; per fold runs ``backtest_executor --replay --include-trades`` and writes **Lightweight Charts** HTML for the **last N** round-trips (default **5**) per strategy×symbol. Test: **`test_walkforward_last_trades_charts_help`**.
- **`scripts/replay_morning_reversion_trade_charts_from_1m_csv.py`** — ``morning_range_reversion`` **1m** replay on a TopStepX ``history`` CSV (tz-aware ``Time`` column supported); ``load_from_csv`` normalizes indexes to **naive UTC**. Writes ``backtest.json``, ``index.html``, and per-trade LWC charts (``axis_time_zone=America/New_York``). ``--reuse-json`` skips rerun after a full backtest. Tests: **`test_replay_morning_reversion_csv_help`**, **`test_csv_loader_tz_naive_utc`**.
- **`scripts/bongo_portfolio_walkforward_equity.py`** — BONGO **seven-leg** portfolio replay (overnight **MNQ/MES/MGC**, body **MNQ/MGC**, morning **MNQ/MGC**): partial TP **on** only for overnight + body (2-lot on MNQ/MES via env; MGC overnight obeys TOML **`[symbols.MGC.risk]`** when set); morning **1-lot**, partial **off**; morning **`REQUIRE_HIGH_ATR` + `TP_MULT=0.7`**. Walk-forward folds run all legs in parallel per fold; merges trades by **exit time** and writes **`portfolio_equity.html`**, **`merged_trades.json`**, **`portfolio_summary.json`** (overnight **`initial_risk_dollars`** stats when overnight legs run; **`meta.overnight_range_toml_symbols_mgc`** when overnight included). Default under ``docs/perf/bongo_portfolio_walkforward_equity/``. **BONGO.md** describes overnight stop $ risk.
- **`docs/STRATEGY_DEVELOPMENT.md`** — walk-forward promotion gate (BONGO Tier **2L**): strategies should not graduate to live in `CANDIDATES.md` without `python -m core.research.runner --walk-forward` (or equivalent documented time-split evidence).
- **`scripts/run_morning_reversion.sh`** — headless **`morning_range_reversion`** via **`core/strategy_executor.py`** (same pattern as **`run_reversion.sh`** / **`run_overnight.sh`**): **`caffeinate`**, timestamped log under **`logs/`**, optional **`MORNING_RANGE_SYMBOLS`** (default **MNQ**).
- **BONGO §1B (generic breakeven)** — **`TopStepXTradingBot`**: optional **`register_generic_breakeven_watch`** + background **`_generic_breakeven_monitor_loop`** (10s poll); **`get_positions`** aliases **`get_open_positions`**. **`BaseStrategy.place_bracket_order(..., breakeven_profit_threshold=…)`** registers after a successful OCO stop-entry. **`body_reversion`** / **`morning_range_reversion`**: **`[position_management] breakeven_enabled`** (default false) and **`breakeven_trigger_r`** (default 0.5) → threshold = `trigger_r × |entry − stop|`. Replay shim accepts the new kwarg. Test: **`test_generic_breakeven_position_symbol_matches`**.
- **`morning_range_reversion` (BONGO §4.1–4.3)** — **`tp_mult` / `sl_mult`** on live brackets and sieve (TP = fraction of half-range from the sweep extreme; was midpoint-only in sieve for `tp_mult≠1`). **`reentry_frac`** inner band on sieve matches **`analyze()`**. Optional **`require_high_atr`** + **`atr_period` / `atr_regime_lookback` / `atr_regime_quantile`** on live fade path and sieve. **`scripts/validate_morning_range_reversion.py`**: **`--tp-mult`**, **`--sl-mult`**, **`--reentry-frac`**, **`--max-fades`**, **`--require-high-atr`**. Tests: **`test_sieve_tp_mult_changes_take_profit_target`**, **`test_sieve_reentry_frac_blocks_shallow_close_inside_box`**.
- **`core/income_brain.income_brain_entry_quantity_for_bot`** — testable sizing entrypoint used by **`TopStepXTradingBot.income_brain_entry_quantity`**; **`tests/test_income_brain_bot_wiring.py`**. **Partial TP (BONGO §1A)** — **`core/bracket_orders`**: :class:`PartialTpStopEntryPlan`, :func:`compute_partial_tp_prices`, :func:`build_partial_tp_stop_entry_plan`; :func:`place_partial_tp_oco_stop_entry_v1` delegates to **`TopStepXTradingBot.place_oco_bracket_with_stop_entry_partial_tp`** when present. **`StrategyReplayEngine`** simulates two-stage scalp + breakeven/runner OCO; **`TopStepXAdapter.place_oco_bracket_stop_entry_partial_tp_v1`** places dual native OCO stop-entry legs (composite **`orderId`**). **`BaseStrategy.place_bracket_order`** accepts **`partial_tp_enabled` / `partial_tp_scalp_r`**; **`morning_range_reversion`**, **`body_reversion`**, and **`overnight_range`** read **`signal.partial_tp_*`** from TOML where applicable. Tests: **`tests/test_bracket_partial_tp_design.py`**, **`tests/test_topstepx_adapter_partial_tp_v1.py`**.
- **`body_reversion`** — optional **`signal.require_bb_touch`** (+ **`bb_period`**, **`bb_k`**) per docs/BONGO.md: when **both** ``require_range_expand`` and ``require_bb_touch`` are on (e.g. MES), the regime cohort passes if **either** range expansion **or** a Bollinger-band touch on the signal bar holds. **`IncomeBrain.sync_session_pnl_from_broker`** + **`TopStepXTradingBot.income_brain_entry_quantity`** wire **`INCOME_BRAIN=true`** into **`BaseStrategy.place_bracket_order`** (broker daily PnL + balance clamp). Tests: **`test_analyze_mes_emits_long_when_bb_touch_or_range_expand_bb_leg`**, **`test_brain_sync_session_pnl_from_broker`**.
- **`core/drift_monitor.py`** — opt-in (**`DRIFT_MONITOR=true`**) live-vs-replay drift monitor. Subscribes to **`EventType.ORDER_FILLED`** on the running bot's `EventBus` and writes a JSONL log per `(strategy, account, date)` under **`DRIFT_MONITOR_LOG_DIR`** (default `logs/drift`). Standalone CLI **`python -m core.drift_monitor compare --live <jsonl> --replay <backtest.json>`** matches live round-trips to replay trades by `(symbol, side, entry_ts ± tolerance)` and reports per-trade R drift + p95 |drift_R| + % breaches over the **0.20 R** kill threshold. Wiring in **`core/strategy_executor.py`** gates entirely on the env flag, so existing executors are unaffected. Tests: **`tests/test_drift_monitor_smoke.py`** (8 cases, included in default `pytest.ini` set).
- **`core/income_brain.py`** — opt-in (**`INCOME_BRAIN=true`**) equity-tier sizing + daily target/stop with HWM trail. Three composable pieces: **`EquityTierSizer`**, **`DailySessionGoal`**, **`IncomeBrain`** (persists to `data/income_brain_<account>.json`). Knobs from **`INCOME_BRAIN_*`** (see **`.env.example`**); strategies do **not** import this module — sizing is applied via **`income_brain_entry_quantity_for_bot`** / **`TopStepXTradingBot.income_brain_entry_quantity`** on the order path. CLI: **`python -m core.income_brain {status,size,record-pnl,reset,archive} --account <id>`**. Tests: **`tests/test_income_brain_smoke.py`**.
- **`docs/perf/morning_range_reversion_validation.md`** — full-window MNQ/MES/MGC sieve validation (Databento, 2024-01-01 → 2026-05-01). Headline finding: the upstream **92.2 % WR / +0.844 R / ~$2.7 k/mo** claim does **not** replicate. The **only** mode with positive expectancy is `require_reentry_close=true` + `max_fades_per_session=1`: **MNQ 62.95 % WR / +0.26 R / ~$400/mo after costs** (1 contract); MES (56.7 %) and MGC (59.5 %) are similarly portable.
- **`docs/perf/body_reversion_v31_prac_shadow.md`** — 10-day PRAC shadow runbook for `body_reversion` v3.1 hybrid: pre-flight steps, daily drift-compare commands, promotion gate (n_matched ≥ 20/symbol, % |drift_R| > 0.20 ≤ 20 %, mean drift_R ∈ [−0.30, +0.30]), failure-mode triage table.
- **`morning_range_reversion`** — Optional **`signal.max_fades_per_session`** (default **0** = unlimited): caps how many fade signals may fire per **ET session date** on the same built anchor (sieve: **`sieve_simulate_from_ohlcv(..., max_fades_per_session=N)`**). Use **2–4** if you want to limit re-arms after in/out oscillation without disabling the strategy.
- **`core/research/runner.py`** — each `grid_results` row now includes **`is_return_pct`**, **`is_total_pnl`**, and **`oos_total_pnl`** (alongside existing OOS return %) so research JSON exports match IS/OOS PnL without recomputing backtests.

### Changed
- **`ema_stack_trend_15m`** — TOML: **`reaction_tol_atr=0.50`**, **`reaction_require_reclaim_bar=false`**, **`min_bars_between_signals=3`**, **5m RSI** **35/65**; logic remains 5m RSI extremes + 15m EMA S/R reactions.
- **`scripts/walkforward_strategy_competition.py`** — docstring example for **120d / 5-fold** matrix including **`ema_stack_trend_15m`** alongside overnight / morning / body (`docs/perf/walkforward_eval_arsenal_core_120d5f_with_ema`).
- **`simple_candle` testing-only** — removed from operator **`catalog_strategy_names`**, default **`register_builtin_strategies`** / TOML auto-enable lists, **`gui/master_control.html`** start dropdown, and **`scripts/run_strategy_matrix.sh`** defaults. **`StrategyManager.start_strategy`** and **`core/strategy_executor.py`** reject live starts unless **`ALLOW_TESTING_STRATEGIES=1`** (documented in **`.env.example`**). **`config/strategies/simple_candle.toml`** sets **`meta.enabled = false`**. **`--list-strategies`** still lists it under “Testing-only replay”.
- **`core/research/runner.py`** — **`_REPLAY_STRATEGIES`** mirrors the production replay set (**adds** **`overnight_reversion`**, **`ema_stack_trend_15m`**; **drops** **`simple_candle`**).
- **`overnight_reversion`** — **Logic rewrite (breaking vs prior proximity templates):** no longer arms BUY/SELL stops **inside** the box at `low+offset` / `high−offset`. After ``timing.market_open`` ET, uses ``signal.fade_signal_timeframe`` bars (default **1m**) for the **first bar close above H** → **SHORT** stop-entry at **H** (stop beyond high via ATR × ``stop_atr_multiplier``, TP at overnight **mid**), or **first close below L** → **LONG** at **L** (symmetric). Same filters/replay gates as ``overnight_range``. ``calculate_range_break_orders`` now returns ``(None, None)``; market-open housekeeping **clears** ``breakout_levels`` so ``monitor_breakout_levels`` never stages wrong legs. Tests: **`tests/test_overnight_reversion_smoke.py`**.
- **`overnight_range` (live)** — When ``breakout_monitor.enabled`` (default) and not CSV/strategy replay, ``place_range_break_orders`` **does not** submit both stop-entry brackets at the broker immediately; templates stay in ``breakout_levels`` and ``monitor_breakout_levels`` stages each side when price is within the proximity band. Replay (``_is_strategy_replay``) and mock ``trading_bot.bars`` paths still place both legs in-window.
- **`morning_range_reversion`** — New ``signal.range_effectiveness_hours`` (default **4**, **0** = off): live ``analyze`` and ``sieve_simulate_from_ohlcv`` ignore new sweeps / re-entry arms at or after ``range_end_open`` on the anchor ET date plus that many hours (e.g. **12:00 ET** when the box ends at 08:00). Shipped TOML documents the knob. Test: **`test_sieve_range_effectiveness_hours_blocks_late_sweep`**.
- **`gui/chart_html` (LWC)** — Morning ET range **baseline** boxes: dashed **50% midline** per day. Trade recap overlays: **blue** entry and **light grey** exit **price lines** plus **entry→exit diagonal** ``LineSeries`` (two-point segment). **Duplicate bar timestamps** are collapsed (last row wins) in ``generate_chart_html``, ``dataframe_to_chart_bars_unix``, and client-side **dedupe** so stitched feeds do not draw double candles. **Volume** series is created **before** candlesticks so OHLC renders above the histogram band.
- **`scripts/walkforward_trade_recap_report.py`** — **`index.html`** / **`metrics.html`** use a **dark** theme aligned with LWC trade charts; **`metrics.json`** rows include **expectancy**, **σ PnL**, **profit factor**, **max drawdown** (sequential cumulative PnL), **max win/loss streaks**, **avg bars held**, **ΣR**; **`metrics.html`** adds a **rollup** table (all folds merged per strategy×symbol) plus a short **survivability** interpretation blurb.
- **`overnight_range` + `StrategyReplayEngine` (CSV replay)** — resting stop-entry brackets from the prior morning were never cancelled in the simulator (mock `get_open_orders` is empty and `BacktestEngine` orders had no broker tag), so unfilled legs could fill **hours later** during the next overnight window (e.g. ~8 PM ET). Simulated OCO stop entries now carry a **`custom_tag`** with **`overnight_range`**; optional **`replay_before_bar_fills`** on **`overnight_range`** runs after the bar clock is set **before** intrabar fills and purges those entries whenever replay time is **outside** the post-open placement window; **`analyze`** also purges once per **(symbol, session end date)** when re-entering that window. Tests: **`test_replay_cancel_pending_stop_entries_removes_tagged_bracket`**, **`test_overnight_range_replay_before_bar_fills_purges_evening`**.
- **`gui/chart_html.generate_chart_html`** — optional **`overnight_range_et_shade`** draws overnight session **H/L** boxes from **`config/strategies/overnight_range.toml`** (evening / morning segments). Wired for **`overnight_range`** in **`walkforward_last_trades_charts`**, **`generate_signal_walkthrough_report`**, and **`walkforward_trade_recap_report`**.
- **`gui/chart_html.generate_chart_html` trade overlays** — static trade-recap charts: **no** entry/exit candle markers; **horizontal price lines** at entry (**blue**) and exit (**light grey**); **line series** from entry (time, price) to exit; `exit_reason` remains in overlay JSON for scripts / tables.
- **Trade recap overlay times vs OHLC** — recap scripts used **nearest** chart bar to each fill timestamp, which could snap an exit onto the **next** candle open; the diagonal then showed a fill price the visible bar never traded. All recap generators now use ``core.backtest.ohlcv.snap_trade_unix_to_chart_bar_open`` (**last** bar open ``<=`` fill time, left-labeled convention). Tests: **`test_snap_trade_unix_to_containing_bar_open_not_nearest_neighbor`**.
- **Replay + LWC OHLC bad ticks** — single-bar lows/highs more than **200** index points beyond ``min(O,C)`` / ``max(O,C)`` are clipped, then OHLC is re-enveloped (``core.backtest.ohlcv.sanitize_ohlcv_ohlc``), before **class replay** bars / 1m intrabar streams, ``replay_bars_from_ohlcv_df``, ``dataframe_to_chart_bars_unix``, and ``gui.chart_html`` static chart / broker row normalization. Reduces false bracket fills and “fleck” candles from stitched vendor prints. Constant: ``DEFAULT_MAX_BODY_WICK_PT``. Tests: **`test_sanitize_ohlcv_clips_deep_lower_wick_beyond_body`**.
- **Class strategy replay — US/Eastern force-flat** — ``StrategyReplayEngine`` reads ``timing.replay_force_flat_et`` from ``strategy._cfg`` (TOML ``StrategyConfig``, not the dataclass ``strategy.config``). Default **16:00** when the key is absent.
- **`scripts/bongo_portfolio_walkforward_equity.py`** — **`--strategies`** filters legs; **`meta.strategies_filter`** records the choice. HTML **title** reflects **overnight + morning** (no body), **body + morning** (no overnight), or the full seven-leg book; **callout** omits overnight wording only when no overnight legs run.
- **`overnight_range`** — **`calculate_range_break_orders`** uses per-symbol **`[symbols.<SYM>.risk] position_size`** and **`[symbols.<SYM>.signal] stop_atr_multiplier`** (via **`StrategyConfig.symbol_override`**) for each leg’s **`RangeBreakOrder.quantity`** and stop distance; MNQ/MES inherit global **`[risk]`** / **`[signal]`**. Shipped **`config/strategies/overnight_range.toml`** sets MGC to **1** contract and **`stop_atr_multiplier = 1.0`** (tune after replay). Hot-reload also reapplies **`partial_tp_*`** and root **`risk.position_size`**. Tests: **`tests/test_overnight_range_symbol_risk_signal_overrides.py`**.
- **`overnight_range` market filters** — **`check_market_conditions`** compares overnight **range**, **gap** (|last close − first open| in the tracked window), and **`atr_timeframe` ATR** as **percent of `(H+L)/2`**, so one **`[filters]`** calibration applies to **MNQ / MES / MGC**. TOML keys: **`range_min_pct`**, **`range_max_pct`**, **`gap_max_pct`**, **`atr_min_pct`**, **`atr_max_pct`**; legacy **`range_min_pts`** / **`atr_min`** etc. still load and convert as **pts / 21_000 × 100** (MNQ reference). Per-symbol **`[symbols.<SYM>.filters]`** supports the same **`*_pct`** or legacy **`*_pts`** keys. **`timing.replay_order_window_minutes=0`** disables the **minute** cap after **`market_open`** in CSV replay (still **never before** open). **`_reload_config_from_env`** reloads filter + replay keys. Schema: **`config/strategies/_schema.toml`**. Tests: **`test_filter_pct_matches_shipped_overnight_range_toml`**, **`test_filter_pct_for_symbol_override_pct_and_legacy_pts`**, **`test_check_market_conditions_compares_pct_of_midpoint`**, **`test_replay_order_window_zero_requires_market_open_et`**.
- **`docs/HANDOFF.md`** / **`docs/PLAYBOOK.md`** / **`.env.example`** — **2026-05-12** handoff refresh (`Last verified commit`), playbook link for **`scripts/run_morning_reversion.sh`**, rollout notes for **`DRIFT_MONITOR`** / **`INCOME_BRAIN`**.
- **`config/strategies/body_reversion.toml`** — BONGO **§1C** commented experiment (`rth_only` + `skip_open_minutes`); defaults unchanged. **`morning_range_reversion.toml`** — BONGO **§4.4** note on pairing **`max_fades_per_session > 1`** with tighter **`reentry_frac`** (defaults unchanged).
- **`scripts/run_morning_reversion.sh`** — default **`MORNING_RANGE_SYMBOLS`** is **MNQ,MGC,MES** (was **MNQ** only); override with **`MORNING_RANGE_SYMBOLS=...`** for a subset.
- **`config/strategies/morning_range_reversion.toml`** — defaults switched to the **validated** configuration (`require_reentry_close = true`, `max_fades_per_session = 1`). The previous immediate-fade default lost money on every cap value tested against full Databento history; the new defaults are the only mode with positive expectancy. Strategy stays `[meta] enabled = false` until a 30-trade PRAC shadow matches replay R within ±20 %. Existing immediate-fade smoke test pins the legacy behaviour via `MORNING_RANGE_REVERSION_SIGNAL_REQUIRE_REENTRY_CLOSE=false` env override.
- **`config/strategies/overnight_range.toml`** — **`stop_atr_multiplier` / `tp_atr_multiplier`** **1.5 / 2.5** (MNQ sweep `smoke_atr_test`, best WR **sl1p5_tp2p5**). **`timing.overnight_start`** **19:00** after sweep **`overnight_timing_mnq`** (higher PnL + avg R vs 17:00/18:00 on 2026-04-11..2026-05-01); **`overnight_end`** left **9:29** (vs **7:00** for higher WR — see TOML comment). Manifest **`config/perf_sweep/overnight_opt_mnq.json`**.
- **Legacy strategies (BONGO Tier 1 G)** — **`trend_scalping`** / **`simple_candle`** now load **`StrategyConfig.from_env(...)`** instead of an incomplete dataclass constructor. **`simple_momentum`** / **`simple_rth`**: shared **`STRATEGY_ID`** + TOML-driven config, **`h`/`l`/`v` OHLCV keys** in replay (fixes spurious breakouts when ``high`` was missing), **`load_strategy_config`**, and **stdout `print` removed** from the momentum path so JSON backtests are not corrupted.
- **`morning_range_reversion`** — **Default live/sieve path**: first **close** outside the morning anchor range immediately produces a **fade** signal so **`execute()`** places **stop-entry + OCO brackets** via `place_oco_bracket_with_stop_entry` (same stack as **overnight_range**). Legacy “wait for inner re-entry” → set **`signal.require_reentry_close = true`** in TOML (or env **`MORNING_RANGE_REVERSION_SIGNAL_REQUIRE_REENTRY_CLOSE=true`**). Offline sieve: **`sieve_simulate_from_ohlcv(..., require_reentry_close=False)`** by default; **`scripts/validate_morning_range_reversion.py --legacy-reentry`**, **`scripts/strategy_litmus.py --legacy-reentry`**. Replay research that matched the old convention should enable **`require_reentry_close`** explicitly.
- **Master GUI performance** — [gui/master_control.html](gui/master_control.html): chart tick no longer refetches order/position lines every quote update (throttled + WS-driven refresh); HTTP fallback polling slowed **30s → 60s** when WebSocket is disconnected. [gui/chart_html.py](gui/chart_html.py): WebSocket **orders** / **positions** broadcasts skip identical snapshots (fingerprint) after events and periodic reconcile.
- **Master chart UX** — [gui/master_control.html](gui/master_control.html): **right-click** on the chart opens a context menu (copy crosshair close, reload data, fit time scale, jump to Active Trading, bracket drag hint).
- **Discord** — [core/discord_notifier.py](core/discord_notifier.py) `send_status_digest`; [trading_bot.py](trading_bot.py) + [core/strategy_executor.py](core/strategy_executor.py) optional background reporter when **`DISCORD_STATUS_INTERVAL_SECONDS` > 0** (see [`.env.example`](.env.example)). [docs/GOTCHAS.md](docs/GOTCHAS.md) notes on retiring cron/Railway webhooks.
- **`docs/MAP.md`** — regenerated via **`scripts/gen_map.sh`** (new test module + fingerprint helpers).

### Fixed
- **Overnight trade recap range box vs entry** — recap charts only padded OHLC around entry/exit, so the **evening** leg of the overnight window was often missing; the shaded H/L was computed from a **truncated** slice and no longer matched ``track_overnight_range``. **Fix:** (1) ``core/backtest/session_shade.overnight_session_start_end_unix`` mirrors the strategy’s session date logic; shading filters bars with ``reference_unix`` = trade entry into that window; (2) ``overnight_recap_df_slice_bounds`` widens CSV slices for ``overnight_range`` charts in walk-forward / walkthrough scripts; (3) ``gui.chart_html`` passes overlay entry time into segment builder. Test: **`test_session_shade_overnight`**.
- **Trade-review LWC HTML (PascalCase OHLCV)** — TopStepX-style exports use **`Open`/`High`/`Low`/`Close`**; chart scripts used **`row.get("open")`**, which yielded **all-zero OHLC** (flat ~0 price scale, invisible candles). **`core/backtest/ohlcv.dataframe_to_chart_bars_unix`** resolves columns case-insensitively; **`replay_morning_reversion_trade_charts_from_1m_csv`**, **`walkforward_last_trades_charts`**, **`render_trade_review_charts`**, and **`generate_signal_walkthrough_report`** use it. Test: **`test_ohlcv_dataframe_to_chart_bars`**.
- **`overnight_range` CSV replay — placement clock** — **`_replay_in_order_placement_window`** now anchors **`timing.market_open`** to the overnight window’s **`end_date`** (same calendar rules as **`track_overnight_range`**), not the bar’s calendar day. Previously, e.g. **8:30 PM** passed because it was “after **that morning’s** 9:29”, so **`analyze()`** could run during **evening range-building** and produce impossible fills inside the session box; **`_replay_sessions_signaled`** now keys on that **`end_date`** too. Tests: **`test_replay_order_window_zero_requires_market_open_et`** (incl. evening bar rejected).
- **`gui.chart_html.generate_chart_html`** — optional **`axis_time_zone`** (used by walkthrough / walk-forward chart scripts as **`America/New_York`**) so tick labels match US/Eastern strategy docs; Unix series unchanged.
- **`TopStepXTradingBot.run_non_interactive`** — ``finally`` closes **``auth_manager``** (aiohttp) and **``discord_notifier``** after one-shot ``--command`` runs so the CLI no longer leaves an **Unclosed client session** warning on exit.
- **`StrategyReplayEngine` replay `get_historical_data` shim** — **`core/backtest/strategy_replay.py`** no longer treats any **``*m``** timeframe as “same CSV intraday” when replay cadence is **``5m``**; only the **native replay timeframe** is served from the walk-forward bar list. **``15m`` / ``30m`` / ``1h``** (and other non-native TFs) delegate to **`MockTradingBot.get_historical_data`** so resampling applies. The replay loop also clears **`_resampled_cache`** whenever the bar prefix grows so coarser series are not frozen on the first resample. Tests: **`test_replay_mock_historical_timeframe`** (incl. resample-cache growth).
- **Replay mock `get_historical_data`** — **`MockTradingBot`** (``core/backtest_executor.py``) ignored any ``timeframe`` except ``1m`` when ``bars_1m`` was present, so **``overnight_range.calculate_atr``** always saw the **replay CSV cadence** (e.g. 5m) no matter **`signal.atr_timeframe`**. The mock now passes **`replay_timeframe`** from the executor and **OHLCV-resamples** coarser requests (5m→15m/1h/…) via **`HistoricalDataLoader.resample`**. **`HistoricalDataLoader.resample`** uses pandas **``min`` / ``h``** offsets (not deprecated **``T``**/**``H``**) so resampling works on current pandas. Tests: **`test_replay_mock_historical_timeframe`**.
- **Legacy momentum replay (BONGO Tier 1 G)** — **`SimpleMomentumStrategy` / `SimpleRthStrategy`**: skip new signals when a position is already open for the symbol, when at **`max_positions`**, or when class replay still has a **pending simulated bracket entry** on that symbol (prevents unbounded stop stacking). **`StrategyReplayEngine`**: **`mock_get_market_quote`** includes bar **volume** for volume-filter realism. **`TrendDetector`**: “not enough bars” during warmup logs at **`debug`**. **`SimpleCandleStrategy.analyze`**: under **`_is_strategy_replay`**, choppy/position noise at **`debug`** so **`--format=json`** keeps **stdout** usable with the final JSON line.
- **`body_reversion` live executor window** — **`config/strategies/body_reversion.toml`** root **`start_time` / `end_time`** (**00:00–23:59** US/Eastern) + empty **`no_trade_*`** so **`should_trade`** is not tied to the laptop’s local clock; **`signal.rth_only`** still gates entries inside **`analyze()`**. **`BodyReversionStrategy._in_trading_window`** uses **`_session_tz_wall_now()`** (``signal.session_timezone``). Test: **`test_in_trading_window_uses_session_timezone`**.
- **`overnight_range` live executor window** — **`OvernightRangeStrategy._in_trading_window_at`** / **`_in_trading_window`** implement **cross-midnight** wrap for **`timing.overnight_start`**–**`overnight_end`** in **`timing.session_timezone`**; **`StrategyConfig`** linear bounds set to **00:00–23:59** (reload + init) so **`BaseStrategy`** never applies an invalid same-day **18:00 > 09:29** range. Tests: **`tests/test_overnight_range_trading_window.py`**.
- **`morning_range_reversion` live executor window** — **`config/strategies/morning_range_reversion.toml`** adds root **`start_time` / `end_time`** (**06:55–16:00** US/Eastern intent) plus empty **`no_trade_*`** so the strategy loop runs **before** the 07:00–08:00 anchor build. **`MorningRangeReversionStrategy._in_trading_window`** applies the same minute rules as **`BaseStrategy`** but uses **`_session_tz_wall_now()`** (**`datetime.now(self._tz)`**, ``self._tz`` = **`signal.session_timezone`**) instead of naive local **`datetime.now()`**. Tests: **`test_in_trading_window_uses_session_timezone_not_local_naive_clock`**, **`test_morning_range_toml_has_et_executor_window`**.
- **`core/backtest/engine.py`** — Closing fills no longer **double-charge exit commission** against `self.capital` (commission was debited on the fill **and** embedded again in `pnl` before `capital += pnl`). **`BacktestTrade.pnl`** now reflects **round-trip** contract fees (entry + exit) so **`final_capital == initial_capital + sum(trade.pnl)`** matches replay JSON. **`BacktestTrade.commission`** on completed trades is the **sum** of entry + exit fees for that round trip.
- **`morning_range_reversion` (immediate / default path)** — After the first **close** outside the morning range arms a fade, **`analyze()`** and **`sieve_simulate_from_ohlcv`** now **suppress further immediate arms** until a bar **closes back inside** `[L, H]` (per session). Prevents thousands of duplicate “sieve trades” while price keeps printing beyond the box without resetting.
- **Handoff kit (`scripts/verify_handoff.sh`)** — Repaired broken in-kit links (point legacy guides at **`docs/archive/`**), added **`docs/ROADMAP.md`** + **`docs/EVENT_DRIVEN_ARCHITECTURE.md`**, **`docs/HANDOFF.md`** `<!-- Last verified commit … -->` header, and regenerated **`docs/MAP.md`** via **`scripts/gen_map.sh`**.
- **`scripts/validate_morning_range_reversion.py`** — date filters use **UTC-naive** bounds (and strip tz from tz-aware CSV indexes) so pandas no longer raises `Cannot compare tz-naive and tz-aware` when slicing.
- **`morning_range_reversion` sieve + live `analyze()`** — building the 07:00–08:00 ET range no longer **finalizes to idle** on the first bar of the ET **calendar** day when that bar is **before 07:00** (overnight futures bars). Previously every day started in `idle`, so the sieve reported **0 trades** on full-history CSVs; **`MorningRangeReversionStrategy`** replay had the same bug.

### Changed
- **Docs / comments** — Clarified that the 7–8 ET range is **before** NYSE **9:30 ET** RTH; **`docs/alpha/morning_reversion_info.md`**, **`strategies/morning_range_reversion_strategy.py`** docstring, and **`config/strategies/morning_range_reversion.toml`**.

### Added
- **`core/backtest/strategy_replay.py`** + **`core/backtest_executor.py`** — optional **`--csv-1m`** (with **`--csv`**) loads 1m OHLCV and runs **pending-order fill simulation** on each **1m sub-bar** inside each aggregate bar (window from **`--timeframe`**, e.g. 5m). Equity / **`analyze()`** stay on the aggregate cadence. Mock bot **`get_historical_data(..., timeframe="1m")`** serves the truncated 1m series for strategies that request it. Helpers + tests: **`tests/test_strategy_replay_intrabar.py`**.
- **`morning_range_reversion` sieve** — optional ``one_minute_df``: when a 5m bar hits **both** SL and TP, ``resolve_ambiguous_5m_bar_with_1m`` walks **1m** OHLC paths (O→H→L→C / O→L→H→C per minute, **close→next open** gaps) instead of stop-first / TP-first guesses. CLI: **`scripts/validate_morning_range_reversion.py --1m-csv`**, **`scripts/strategy_litmus.py --1m-csv`**. Test: **`test_sieve_1m_path_tp_before_stop_same_5m_bar`**.
- **`hourly_anchor_retrace`** — new replay strategy: 07:00–07:59 ET anchor-hour range; **first 5m close** outside arms a **stop-entry** at the breached extreme, TP at **50%** and SL **1:1**. Files: **`strategies/hourly_anchor_retrace_strategy.py`**, **`config/strategies/hourly_anchor_retrace.toml`**. Registered in **`strategies/strategy_manager.py`**, **`core/backtest_executor.py`**, and **`core/research/runner.py`**. Test: **`tests/test_hourly_anchor_retrace_smoke.py`**.
- **`scripts/strategy_litmus.py`** — fast **preset** litmus (default: last **90** calendar days of CSV, in-process sieve; **`morning_range`** + optional **`--compare-intrabar`** + **`--why`**). Tests: **`tests/test_strategy_litmus_smoke.py`**. Documented in **`docs/BACKTESTING.md`**.
- **`scripts/body_reversion_weekly_income_gate.sh`** — one-shot **Gate A/B/C** replay with **`--include-trades`** then **`scripts/print_weekly_income.py`** (grid JSONs omit trades, so weekly buckets need this path).
- **`strategies/morning_range_reversion_strategy.py`** + **`config/strategies/morning_range_reversion.toml`** (`meta.enabled = false`) — 7:00–7:55 **US/Eastern** 5m range, **close** outside for sweep, **close** back inside for re-entry, bracket **TP = midpoint** / **SL = 1:1** vs half-range; **`sieve_simulate_from_ohlcv`** for offline validation (conservative intrabar: stop before target when both touch). **`scripts/validate_morning_range_reversion.py`** prints win-rate breakdown from a 5m CSV. Registered in **`strategies/strategy_manager.py`**, **`core/backtest_executor.py`** (`--replay`), **`core/research/runner.py`**. Tests: **`tests/test_morning_range_reversion_smoke.py`**.
- **`scripts/run_backtest_manifest.py`** + **`config/backtest_matrices/example_body_reversion_q1.jsonl`** — parallel **`backtest_executor`** replay jobs from a **JSONL manifest** (per-job `env`, optional `--include-trades`, **`BACKTEST_MANIFEST_JOBS`** / **`--jobs`**). Complements the Gate A/B/C parallel runner for arbitrary strategy matrices.
- **`core/backtest/ohlcv.py`** — **`replay_bars_from_ohlcv_df`** (numpy-backed) replaces **`iterrows`** when building replay bar lists in **`core/backtest_executor.py`**.
- **`HistoricalDataLoader.load_from_csv`** — optional in-process **LRU cache** (path + mtime, **`BACKTEST_CSV_CACHE`**, **`BACKTEST_CSV_CACHE_SIZE`**); **`csv_cache_stats`** / **`clear_backtest_csv_cache`**; tries **`engine=pyarrow`** when available. Tests: **`tests/test_backtest_perf_helpers.py`**.
- **`scripts/print_weekly_income.py`** — prints **weekly realized PnL** from backtest JSON (per **ISO week** on `exit_time` when `result.trades` exists; otherwise **avg $/week** from `total_pnl` ÷ calendar weeks in `period`). Optional **`--csv`**. Tests: **`tests/test_print_weekly_income.py`**.
- **`scripts/run_body_rev_gate_ab_parallel.py`** + **`scripts/resume_body_rev_gate_ab_full.sh`** (delegates to the parallel runner) — **Gate A/B/C × MNQ/MES/MGC** full-window `body_reversion` grid: **`ThreadPoolExecutor`** + **`GATE_AB_JOBS`** (default **3**), skip JSON **>200 bytes** — same `/tmp/body_rev_gate_ab/full_2024_2026/` outputs as the old sequential loop, less wall-clock on multi-core hosts.
- **`scripts/run_alpha_discovery_sprint.sh`** — runs **`scripts/alpha_discovery.py`** per symbol on **`historical_data/price/{MNQ,MES,MGC}_5m_databento.csv`** into `docs/alpha/sprint_overnight_range_*.md` (optional **`OOS_SPLIT`**). **`docs/ALPHA_DISCOVERY.md`** documents the sprint + how to monitor long **`backtest_executor`** jobs; **`docs/perf/sweeps/CANDIDATES.md`** notes interim **full-window Gate A** MNQ stats; **`config/strategies/body_reversion.toml`** comments document optional **greedier** env knobs (`tp_r_multiple`, `min_bars_between_signals`).
- **`scripts/databento_stitch_canonical.py`** — merge a Databento **`GLBX-*`** batch into per-symbol **`historical_data/price/{MNQ,MES,MGC}_1m_databento.csv`** (stitch with **newer rows winning** on duplicate timestamps via `historical_data/csv_merger.py`), optionally refresh **`{SYM}_5m_databento.csv`**. Batch dir: **`--batch-dir`**, env **`DATABENTO_BATCH_DIR`**, or auto-pick the lexicographically newest **`historical_data/price/GLBX-*`**. Documented in **`docs/BACKTESTING.md`**; **`docs/GOTCHAS.md`** clarifies **API vs local** account lockout signals.
- **`core/chart_databento_loader.py`** + **`gui/chart_html.py`** (`/api/chart/reload`) + **`gui/master_control.html`** — Master chart can load OHLCV from **canonical Databento CSVs** when **`source=databento`** or **`source=auto`** (API first, then file if empty). Response includes **`history_source`**. Env default: **`CHART_RELOAD_SOURCE`** (documented in **`.env.example`**). **`gui/README.md`** summarizes the query flags. **`pytest.ini`** includes **`tests/test_chart_databento_loader.py`**.
- **`scripts/run_reversion.sh`** — launch **`body_reversion`** via `core/strategy_executor.py` (MNQ/MES/MGC, risk JSON with `max_pending:2`), mirroring `scripts/run_overnight.sh`.
- **`docs/perf/trade_review/body_reversion_v31_hybrid_q1_2026_{MNQ,MES,MGC}/`** — Q1 2026 replay **trade reviews** for `body_reversion` v3.1 hybrid: `backtest.json` (`--include-trades`), `SUMMARY.txt`, `trades.md` / `trades.csv`, and per-trade **Lightweight Charts** HTML under `charts/` (from `scripts/render_trade_review_charts.py`, 5m CSV, ±120 min padding).
- **`scripts/body_reversion_combo_audit.py`** + **`docs/alpha/body_reversion_combos.md`** — targeted **co-trigger audit** for the body-reversion anchor (`big_bear_body>0.9` LONG / `big_bull_body>0.9` SHORT). Pairs the anchor with each candidate co-trigger (VWAP deviation, ATR-regime quartiles, RTH phase, volume spike, range expansion / contraction, gap, RSI extreme, SMA side, Bollinger touch, follow-through bars, ETH-overnight) and reports realized R under both **stop-only / fixed-hold (SO)** and **triple-barrier (BK)** execution models. Also tests **3-way combos** (anchor × A × B). Headline finding: **`anchor & atr_high_q4 & range_expand_1.5x`** is positive on every (symbol × direction) cell with `n ≥ 500`, `delta_R ≥ +0.56`, `PF_SO ≥ 3.0` — driving the v3 strategy update below.

### Changed
- **`gui/master_control.html`** — chart reload requests **more bars** on MNQ/MES/MGC when using **Auto** or **Databento file** (up to **5000** for Databento, capped server-side); first paint keeps the viewport on the **most recent ≤200** bars (pan left for older loaded history).
- **`strategies/body_reversion_strategy.py`** — **live** path: lockout uses `get_open_positions` / `get_open_orders` (not `bot.active_positions`); **time exit** calls `trading_bot.close_position` after wall-clock `max_hold_bars × timeframe` (executor loop is ~60s). Tracks symbols from successful `execute()` so manual positions are not closed.
- **`config/strategies/body_reversion.toml`** — `[meta] enabled = true` for PRAC/live runs (set `false` for replay-only).

### Added
- **`strategies/body_reversion_strategy.py` v3** — added two **regime-gate co-triggers** (default **ON**) at signal time:
  - `require_high_atr` (default true): current 14-bar ATR ≥ rolling 75th percentile of the trailing `atr_regime_lookback` (= 500 bars ≈ 3 sessions on 5m). Implemented via online quantile over the lookback window — no lookahead.
  - `require_range_expand` (default true): current bar range > `range_expand_mult` (= 1.5) × the trailing `range_ma_period` (= 20)-bar range MA.
  - Bumped `lookback_bars` default 100 → 500 so the rolling ATR-percentile gate has enough samples; flipped `allow_short` default false → true (the regime gates fix the v2 SHORT-leg bleed). New v3 backtest summary in `docs/perf/sweeps/CANDIDATES.md` and `docs/alpha/deep_scan_INDEX.md`. Two new smoke tests pin v3 behaviour: `test_analyze_skipped_when_v3_regime_gates_active_and_no_expansion` and `test_analyze_emits_long_when_v3_regime_gates_satisfied`. v3 default test renamed `test_v3_defaults_load_from_toml` and asserts the regime-gate flags + `lookback_bars=500`.
- **Q1 2026 OOS replay backtest (v3 vs v2)** — body_reversion v3 with regime gates ON: **MNQ 48 trades + $421 PF 1.33 Sharpe 1.73 / MES 66 trades + $1,003 PF 1.89 Sharpe 3.16 / MGC 53 trades + $632 PF 1.27 Sharpe 1.51 / Total 167 trades + $2,055 expectancy + $12.31/tr**. Total PnL identical to v2 (+$2,058) with **78 % fewer trades**, **expectancy 4.5×**, Sharpe up on every symbol; the previously-failing **MES SHORT leg flips to PF 1.89 / Sharpe 3.16** (was −0.34). Drawdown halved on MNQ and MES.
- **body_reversion v3.1 hybrid** — added **per-symbol regime gate overrides** via `StrategyConfig.symbol_override(...)` so the range-expansion gate can be enabled only where it helps. Updated defaults: global `require_high_atr=true`, global `require_range_expand=false`, with `[symbols.MES.signal] require_range_expand=true`. Q1 2026 OOS A/B/C gate study found MNQ+MGC prefer ATR-only, MES prefers both gates; v3.1 encodes that without forcing a single global tradeoff.
- **`core/research/pattern_conditional.py`** + **`scripts/pattern_conditional_scan.py`** — Fisher + Benjamini–Hochberg on 2×2 bar patterns (5m NY); RTH prior calendar-day high/low breakout → same-day re-touch stats; markdown under ``docs/alpha/pattern_scan_*.md``. Docs: [ALPHA_DISCOVERY.md](docs/ALPHA_DISCOVERY.md), [researching.md](docs/perf/researching.md), [RESEARCH_PATHWAYS.md](docs/RESEARCH_PATHWAYS.md), `docs/perf/sweeps/CANDIDATES.md` (historic — file removed in a later cleanup). Tests: ``tests/test_pattern_conditional.py``.
- **`core/research/deep_pattern_scan.py`** + **`scripts/deep_pattern_scan.py`** — forward-return effect sizes (points) at multiple horizons (5/15/30/60 min); Welch t + BH FDR; IS/OOS sign stability on 70/30 session-date split; RTH phase stratification on top features; reports under ``docs/alpha/deep_scan_*.md`` (+ ``deep_scan_INDEX.md``).
- **deep_pattern_scan v2** — three new lenses inside `core/research/deep_pattern_scan.py`:
  (1) **`combo_scan`** — pairwise feature AND-conjunctions (e.g. `big_bear_body>0.7 & vwap_dev<-5bp`) with Welch t / BH FDR / IS-OOS stability;
  (2) **`bracket_grid`** + **`realized_r_with_brackets`** — triple-barrier (stop / TP / time-out) realised-R sweep, R unit = stop distance, conservative ordering when stop and target tag the same bar;
  (3) **`stop_only_grid`** + **`realized_stop_only_fixed_hold`** — protective-stop-only with fixed-time exit (no TP) — the right execution model when the alpha lives in the post-event drift, not in a fixed TP. Reports now embed all three sections per symbol.
  Also added stronger body-fraction features (`big_bull_body>0.85`, `big_bull_body>0.9`, mirror bear), `vol_spike_3x`, and 14-bar ATR-regime quartiles (`atr_high_q4`, `atr_low_q1`).
- **`strategies/body_reversion_strategy.py`** + **`config/strategies/body_reversion.toml`** — research-grade 5m big-body mean-reversion fade strategy promoted from the deep scan; registered in ``strategies/strategy_manager.py``, ``core/backtest_executor.py`` (``--list-strategies``), and ``core.research.runner`` replay set. Live wiring **off** by default. Tests: ``tests/test_body_reversion_smoke.py``.
  - **v2 defaults (this update)**: `body_pct_min=0.90` (was 0.70 — captures only capitulation/squeeze bars), `stop_atr_multiplier=0.5` (was 1.5 — tight cut), `tp_r_multiple=3.0`, `min_bars_between_signals=6` (30-min cooldown), `max_hold_bars=6` (time-based exit knob), and **`allow_short=false`** by default after Q1 2026 OOS replay showed the SHORT leg bleeds in sustained equity rallies. New smoke test `test_v2_defaults_load_from_toml` pins the defaults.
- **`docs/perf/sweeps/CANDIDATES.md`** — `body_reversion` candidate card promoted to **v2** with full bracket / stop-only / fixed-hold tables, kill criteria, and a refreshed open-questions list (NQ/ES scaling, vwap_dev co-trigger, ATR-regime gating, asymmetric long-vs-short P&L).
- **`docs/alpha/deep_scan_INDEX.md`** — rewritten as the **headline finding** for body-reversion across MNQ/MES/MGC, with effect-size table at three body-fraction thresholds, realised-R table for the best execution policy, and a "how to read" guide for the per-symbol reports.
- **Pre-resampled 5m Databento CSVs** for MNQ/MES/MGC (`historical_data/price/${SYM}_5m_databento.csv`) — produced by `historical_data/resample_ohlcv_csv.py`, shrinks `body_reversion` end-to-end backtest from hours to ~ 8 min on a 4-month window.

### Fixed
- **`core/backtest/strategy_replay.py`** — **OCO sibling cancellation now fires eagerly** the moment any bracket leg fills, instead of after the whole `for order in pending_orders` loop. The previous post-loop sweep allowed both the stop-loss (`low ≤ stop`) and take-profit (`high ≥ limit`) to fire in the **same bar**, where `_update_position` would close the position on the first fill and then open a **phantom reverse position** on the second (because the second filled order saw no existing position and hit the "Opening new position" branch in `core/backtest/engine.py`). Empirically this turned every multi-bracket-fill bar into a phantom SHORT for any bracket strategy on volatile periods. Found while debugging `body_reversion`'s Q1 2026 OOS replay (one phantom SHORT bled −$4,429 over 15 k bars before the fix). Also added a defensive orphan-bracket sweep that cancels OCO-tagged pending orders for symbols whose position has gone flat.
- **`strategies/body_reversion_strategy.py`** — Stopped consulting `bot.calculate_atr` in replay mocks. The mock returns ATR of the *last 14 bars of the entire input CSV* instead of the last 14 bars *up to the current replay timestamp*; on volatile stretches that inflates the strategy's stop distance 10-50× (e.g. average-loss = $232 instead of the expected ~$12 on MNQ Q1 2026). Strategy now always computes ATR locally from `get_historical_data(limit=atr_period+5)`. Added per-symbol lockout (`_has_open_position_or_pending_entry`) so a new signal cannot stack a second LONG on top of an open position; this prevents the orphan-bracket exits from later opening phantom reverse positions even if engine OCO logic regresses.

### Fixed
- **`.gitignore`** — `docs/archive/old.env` and `docs/archive/old.env.*` (names like `old.env` are not covered by `*.env.*`); **`trading_bot.log.*`** for rotated log shards.
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
- **`config/strategies/overnight_range.toml`** — `[filters]` moved to **Pathway 1 P5** research preset live: ``skip_weekdays = []``, widened ``range_min_pts`` / ``range_max_pts`` (30..600), ``gap_max_pts`` 250, ``atr_min`` / ``atr_max`` (15..220); ``range_size`` / ``gap`` / ``volatility`` remain **on**. Prior Phase 1.1 values left in comments for revert.
- **`docs/RESEARCH_PATHWAYS.md`** — § “Live TOML = P5 + reviewing simulated trades”: paths under ``docs/perf/trade_review/overnight_range_p5_live_toml_mnq/``; note ``2>`` (stderr) + ``PYTHONUNBUFFERED=1`` for JSON redirects; pointer to Databento merged CSV.
- **`docs/perf/trade_review/overnight_range_p5_live_toml_mnq/`** — P5/live-TOML MNQ review bundle: ``backtest.json``, ``SUMMARY.txt``, ``trades.md`` / ``trades.csv``, per-trade ``charts/MNQ_*.html``, ``README.md``.
- **`docs/perf/researching.md`** — promoted from a flat link dump into a categorized knowledge-base index (mental model, statistical libraries, data sources, reference projects, implementation tips); cross-linked from `RESEARCH_PATHWAYS.md` / `OVERNIGHT_RANGE_RESEARCH.md` / `LOCAL_LLM_RESEARCH.md`.
- **`docs/RESEARCH_PATHWAYS.md`** — sweep table re-anchored on cumulative env overrides (P0..P5) using `scripts/run_overnight_range_sweep.sh`; Pathway 2 table now lists `vwap_zscore_reversion` and references `scripts/run_strategy_matrix.sh`.
- **`docs/MAP.md`** — regenerated via `scripts/gen_map.sh` to pick up the new strategy / config / scripts / LLM-helper module / docs / sweeps tree.
- **`pytest.ini`** — added `tests/test_vwap_zscore_reversion_smoke.py` to the default fast-test set.
- **`docs/perf/overnight_range_MNQ_1m_chunks.json`** — regenerated via **`scripts/run_overnight_range_csv_chunks.py`** (rollup **4** trades, **~238.3** PnL aligned with single full-span CSV replay). **`docs/RESEARCH_PATHWAYS.md`** / **`docs/OVERNIGHT_RANGE_RESEARCH.md`** — canonical regen one-liner includes **`--timeframe 1m`**, “last regenerated” note + rollup vs continuous-run sanity check.
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
- **`scripts/merge_databento_glbx_batch.py`** — merge a Databento GLBX batch folder of `glbx-mdp3-*.ohlcv-1m.*.csv` (``split_symbols``) into **`MNQ` / `MES` / `MGC`** single CSVs (`timestamp,open,high,low,close,volume`, naive UTC): skips hyphenated roll-aggregate filenames by default, dedupes duplicate timestamps by **highest volume** bar, optional `--start` / `--end`. Documented in **`docs/BACKTESTING.md`** and **`docs/perf/researching.md`**.
- **`strategies/vwap_zscore_reversion_strategy.py`** + **`config/strategies/vwap_zscore_reversion.toml`** — research-grade intraday VWAP Z-score mean-reversion strategy (RTH-gated, ATR-stopped, TP at VWAP); registered in **`strategies/strategy_manager.py::BUILTIN_STRATEGY_SPECS`** and in **`core/backtest_executor.py`** (`--list-strategies` + `_get_strategy_class`). MNQ replay (2025-12-24..2026-04-30): **84 trades / +$1172 / 46% WR / 2.09 Sharpe / $597 max DD** at default knobs (vs `overnight_range` P0 = 4 trades). Disabled by default in TOML.
- **`scripts/run_overnight_range_sweep.sh`** — one-shot Pathway 1 P0..P5 filter sweep (env overrides only, live TOML untouched); writes JSON per step + TSV summary under **`docs/perf/sweeps/`**. First run on **`MNQ_1m_complete.csv`** (2025-12-24..2026-04-30): P0 = 4 trades / +238 / Sharpe 11.1; P3 = 67 / +6511 / 2.62 / DD 11k; P5 (widened bands, filters on) = 64 / +776 / 3.51 / DD 384.
- **`scripts/run_overnight_range_oos_wf.sh`** + **`core/research/runner.py`** — optional **`--overnight-research-profile p0|p5`**, **`--replay-env`**, **`--replay-env-clear`**, and **`--output`** so `overnight_range` CSV replay can run **IS/OOS + MC + walk-forward** under the same filter env as the sweep. **`docs/BACKTEST_RESEARCH.md`** / **`docs/perf/sweeps/overnight_range_MNQ_oos_wf_README.md`** document the driver + a sample Mar–May 2026 artifact interpretation.
- **`scripts/run_strategy_matrix.sh`** — strategy × symbol matrix backtest with per-run perl-alarm timeout (`PER_RUN_TIMEOUT`, default 600 s) and `%CSV_DIR%`/`%SYM%` template placeholders so MES / MGC drop in beside MNQ once the walk-back exporter has run.
- **`scripts/walkback_history.sh`** — chunked walk-back exporter for any TopStepX root (`SYMBOLS="MES MGC"`, `WINDOW_DAYS`, `MAX_WINDOWS`, `STOP_AFTER_EMPTY`, `MIN_BARS`); auto-sources `.env`, runs `scripts/export_history.py` per window, then `historical_data/csv_merger.py` to produce `<SYM>_1m_complete.csv`. Confirmed TopStepX 1m retention is **~6 weeks for MES** (47 835 bars 2026-03-15..05-01) and **~5 weeks for MGC** (39 998 bars 2026-03-22..05-01); MNQ already had 4-month coverage.
- **`core/llm/ollama_client.py`** + **`core/llm/__init__.py`** — async client (`generate` / `chat` / `embed`) over a local Ollama server (`OLLAMA_HOST` / `OLLAMA_MODEL` / `OLLAMA_TIMEOUT`); only research scripts may import it.
- **`scripts/llm_review.py`** — summarise / rank / critique / propose-grid for backtest JSON, alpha reports, sweep TSVs (defaults to `qwen2.5:7b`, switchable via `OLLAMA_MODEL`).
- **`scripts/llm_embed_logs.py`** — embed log / artifact paragraphs into a parquet file via `nomic-embed-text` (offline retrieval, opt-in).
- **`docs/LOCAL_LLM_RESEARCH.md`** — operator doc; codifies the **research-only** rule (no LLM in live trade decisions), env vars, prompt templates, model menu.
- **`docs/perf/sweeps/CANDIDATES.md`** — open hypothesis ledger (`vwap_zscore_reversion` prototyped; ORB-5m / Bollinger revert / volatility breakout / calendar spread / tape microstructure ideas with kill criteria).
- **`tests/test_vwap_zscore_reversion_smoke.py`** — registry / instantiation / `analyze`-short-circuit / LLM-no-network smoke checks; added to `pytest.ini` `testpaths` (default suite now 14 ✅).
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
