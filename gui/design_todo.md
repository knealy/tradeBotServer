Design TODO — Master Control v2

Resolved 2026-06-13 (v2 Phase 3.0 — DB rewiring round)
- ✅ MRR / ORB strategy-state DB persistence
  - Generic ``BaseStrategy.persist_range_snapshot`` helper (mirrors the OR
    pattern). Hooked at MRR range-finalization and ORB range-built.
  - Chart server reads ``strategy_states.settings.{mrr_ranges,orb_ranges}``
    via ``_strategy_ranges_from_db`` so the live chart now shows the box
    even when MRR/ORB run in a separate ``strategy_executor`` process.
- ✅ Trade snapshot capture on close
  - New ``trade_snapshots`` Postgres table + ``save_trade_snapshot`` /
    ``get_trade_snapshot`` methods.
  - Background capture in ``user_hub_handlers._capture_trade_snapshot``
    (runs after TRADE_CLOSED publish; never blocks the consec-loss breaker).
  - New ``GET /api/chart/trade_snapshot/{trade_id}`` endpoint. Keyed off
    the broker's ``exit_fill_id`` (matches ``trade.id`` on the dashboard
    side); a session-tracker-id alias row is also written for tools that
    have only the internal id.
- ✅ Strategy range overlay inside the recap modal
  - ``_capture_trade_snapshot`` reads the live MRR/ORB/OR range from
    ``strategy_states.settings`` for the trade's symbol and embeds it in
    the snapshot row's ``range_snapshot_json`` column.
  - The recap modal renders the range as a shaded amber box + H/L/M
    price lines (mirrors ``applyChartRanges`` on the live chart).

Daily Databento freshness — already shipped (don't duplicate)
- ✅ Use the existing pair, **not** a new wrapper:
    scripts/stitch_broker_history_to_databento_1m.py
    scripts/stitch_broker_history_to_databento_5m.py
  Both chunk get_historical_data into ≤10-day windows (under the 20k bar
  adapter cap), then csv_merger.py with canonical-first / broker-second
  so newer timestamps win on duplicates.
- Cron suggestion (5:30 / 5:35 PM ET weekdays — after CME settlement,
  see CHANGELOG Phase 3.2 for the exact lines).
- The live chart's hybrid stitcher (Phase 2.9) handles in-flight render
  without these scripts; the cron is purely so OFFLINE tools (walk-forward,
  optimizer trials, walkforward_trade_recap_report) see today's data.

Resolved 2026-06-13 (v2 Phase 3.2 — head-summary chip rewire)
- ✅ Drawer-head summary now shows WS health (Connected · uptime ·
  last tick) instead of the DLL buffer chip. tickConnMeta + setConn
  both mirror into the chip so it updates at 1 Hz with the rest.

Resolved 2026-06-16 (v2 Phase 3.7 — MLL chip + risk-source refactor)
- ✅ MLL (trailing-drawdown) chip added next to DLL with same warn/hot/
  breach palette. MLL is the more catastrophic of the two prop
  numbers (permanent account termination on breach vs DLL's daily
  lock); chip tooltip distinguishes them ("trading locked until EOD"
  vs "account permanently terminated"). Generalised CSS to
  .head-summary .risk so DLL and MLL share a single style block.
  Frontend uses a shared applyRiskChip(cfg) helper for both pills.
- ✅ Fixed Phase 3.6 silent-hide bug: handle_account_state was
  reading daily_loss_limit from tracker.get_state(), which never
  includes limit fields — so the DLL chip was always hidden in
  production. Refactored to read DLL+MLL from
  AccountTracker.get_compliance_status() — single source of truth,
  same numbers the bot's risk gate reads. Response now also carries
  mll_violated / dll_violated booleans so the chip respects the
  tracker's authoritative breach state.
- Pinned by tests/test_mll_buffer_math.py (8 cases). tests/
  test_dll_buffer_math.py still green against the new path.

Resolved 2026-06-16 (v2 Phase 3.6 — DLL pill in head chip)
- ✅ Restore the daily-loss-limit number to the always-visible head
  chip (Phase 3.2 dropped it). 4th token next to "Connected · uptime
  · last tick" rendered as "DLL $670" with hairline label + color
  thresholds: muted ≥50%, linen 25-50%, rose <25%, bright-rose
  "DLL BREACH" at 0. Backend computes dll_remaining +
  dll_pct_remaining inline in handle_account_state with the
  no-inflate guard ``min(0, total_pnl)`` so profitable sessions don't
  spike above 100%. Auto-hides chip + leading sep when tracker hasn't
  seeded the limit. Pinned by tests/test_dll_buffer_math.py (9
  cases).

Resolved 2026-06-15 (v2 Phase 3.5 — strategies ready/sleeping panel)
- ✅ Strategies-at-rest empty state rewrite. New idle-list block above
  the launcher renders every registered, non-active strategy with its
  schedule + arms-in countdown + last persisted range (from
  strategy_states.settings.{or,mrr,orb}_ranges). _compute_next_launch
  resolves zoneinfo America/New_York, advances past today's window,
  and skips Sat/Sun for weekday-only strategies. 1 Hz ticker keeps
  the countdown smooth between polls. Eyebrow upgrades to "at rest ·
  N ready". Pinned by tests/test_strategy_next_launch.py (9 cases
  covering unknown→manual, weekend skips, MRR/ORB schedule times,
  and the today/tomorrow/weekday label suffix contract).

Resolved 2026-06-15 (v2 Phase 3.4 — trade aggregation correctness)
- ✅ Collapse Trade/search fill legs into logical trades. The broker
  emits one record per filled contract leg, so a 3-contract atomic
  close arrived as 3 rows and a 6-contract scale-out as 6 — inflating
  trade count, win rate, streaks, max-DD, profit factor, and equity
  curve. New _aggregate_trade_legs helper in gui/chart_html.py groups
  by (symbol, side, entry_order_id) with an entry-time-bucket
  fallback, recomputes statistics via
  trading_bot._calculate_trade_statistics over the aggregated set,
  and exposes total_legs so the frontend can show "30 trades · 59
  fills" plus a "BUY ×3" pill on each multi-leg row. KPI strip,
  equity curve, and streaks self-correct via the same response.
  Pinned by tests/test_aggregate_trade_legs.py.

Resolved 2026-06-13 (v2 Phase 3.3 — auto-mode regression + dedupe)
- ✅ Auto-mode chart fixed for windows < 5 days. Phase 2.9 was forcing
  a tight start_time/end_time on the broker's get_historical_data even
  when the window fit entirely inside the API horizon, which returned
  zero bars (end_time = now lands inside an open candle). Auto branch
  now only passes tight start/end when needs_databento=True (i.e. we
  actually need to stitch); otherwise it reverts to None/None and the
  broker returns its natural "latest limit bars" exactly like pre-2.9.
- ✅ Removed the duplicated WS chip pair (`.conn` + `#conn-meta`) from
  the drawer body — they were redundant after Phase 3.2 moved that
  payload into the always-visible head-summary. JS lookups are
  null-guarded so no behavioural change beyond layout.

Still queued
- (none)
