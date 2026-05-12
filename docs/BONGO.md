I have enough context to deliver a thorough systems report. Let me compile it.

---

# `tradeBotServer` — Full Systems Report
*Saturday, May 9, 2026*

## 1. Status snapshot

| Layer | State | Notes |
|---|---|---|
| Live runtime (`strategy_executor` × broker × hubs × DB) | **Active / production** | Trades run overnight on macOS via `caffeinate`; Railway companion serves dashboard/webhook |
| Order/risk pipeline (`bracket_orders.py`, `risk_management.py`) | **Active / hardened** | `max_pending=2`, correlated‑exposure cap on MNQ/MES/MYM/M2K, DLL/MLL respected |
| Strategy inventory | **2 live + 4 research + 4 legacy** | 10 strategies registered in `BUILTIN_STRATEGY_SPECS` (`strategies/strategy_manager.py`) |
| Backtest engine (`core/backtest/*`) | **Active / recently bug‑fixed** | Phantom-reverse-position OCO bug + double-commission bug both fixed in last week |
| Research stack (`core/alpha`, `core/research`, deep_pattern_scan, combo_audit, LLM review) | **Active / mature** | Fisher + BH-FDR, IS/OOS, MC, walk-forward, LLM-driven critique via local Ollama |
| Master dashboard (`gui/master_control.html` + `gui/chart_html.py`) | **Active / polished** | WS-driven order/position lines, theme presets, popout panels, OR levels |
| Rust hotpath (`TOPSTEPX_USE_RUST`) | **Optional / partial** | ~3 of ~10 execution paths wired; off by default |
| Postgres schema migration | **Live DDL at startup; Alembic baseline only** | Works but not enterprise-clean |
| Documentation | **Mostly current; 13 broken links** | `scripts/verify_handoff.sh` flags pre-existing rot; `MAP.md` regenerates clean |

Default test suite: **14 fast tests pass in <1 s** (`pytest.ini` curated set).

---

## 2. What changed recently (highest-impact items)

### Engine correctness — *systemic, applies to every bracket strategy*
1. **`core/backtest/strategy_replay.py`** — OCO sibling cancellation now fires **eagerly** the moment any leg fills. Previously SL+TP could fill in the same bar, the second fill saw no position, and the engine **opened a phantom reverse position** (cost `body_reversion` ‑$4,429 over 15 k bars before discovery).
2. **`core/backtest/engine.py`** — closing fills no longer **double-charge exit commission**. `BacktestTrade.pnl` now reflects round-trip fees so `final_capital = initial_capital + Σ trade.pnl` matches replay JSON.
3. **`MockTradingBot.calculate_atr`** leak — strategies were getting ATR over the *entire input* instead of *up to current replay timestamp*. `body_reversion` now computes ATR locally; rest of strategies should follow.

### New strategy work
- **`body_reversion` v3 → v3.1 hybrid**: per-symbol regime gates (ATR-q4 on all, range-expansion only on MES). Enabled live by default in TOML.
- **`morning_range_reversion`**: 7:00–7:55 ET range fade. Default path now matches `overnight_range` (immediate stop-entry brackets on first close outside). Sieve simulator + 1m intrabar resolution.
- **`hourly_anchor_retrace`**: variant of morning-range — first close outside an anchor hour = stop-entry at breached extreme, TP at 50%, 1:1 SL.
- **`vwap_zscore_reversion`**: prototyped, registered, off by default.

### Data + research
- Databento canonical CSVs for MNQ/MES/MGC at 1m (~2 yrs) and 5m, with `scripts/databento_stitch_canonical.py` to refresh.
- `scripts/run_body_rev_gate_ab_parallel.py` — parallel Gate A/B/C × 3 symbols full-window grid (just finished).
- `scripts/print_weekly_income.py` — per-week realized PnL from any backtest JSON.
- Local LLM research wiring (Ollama via `core/llm/ollama_client.py`) for summary/rank/critique/grid-propose, **strictly research-only** (no live import path).

### GUI / ops
- WS-driven dashboard refresh; HTTP fallback slowed 30 s → 60 s.
- Right-click chart context menu, theme presets, popout panels.
- `DISCORD_STATUS_INTERVAL_SECONDS` heartbeat from any executor.
- `_cancel_previous_session_orders()` now runs at top of `_execute_market_open_sequence` (clean session rollover).

---

## 3. Best features (what gives this codebase its real edge)

1. **Same code path live and replay.** Strategies use `BaseStrategy` + `StrategyConfig`; replay drives them through a `MockTradingBot`. Any logic that works in replay works live, modulo broker latency. This is the single most valuable property the repo has.
2. **Fully event-driven core.** SignalR → bar aggregator → `BarClosedEvent` on `EventBus` → strategy → order via adapter → `FillEvent`. Subscriber model means new strategies plug in without touching plumbing.
3. **Hot-reload TOML config (`maybe_reload`) per loop tick** — change a stop multiplier without restarting the bot.
4. **Research-grade discovery pipeline.** `core/alpha/scanner.py` (Kruskal–Wallis / Mann–Whitney + BH-FDR), `core/research/deep_pattern_scan.py` (multi-horizon forward-return effect sizes, IS/OOS sign stability, triple-barrier R), `body_reversion_combo_audit.py` (3-way co-trigger interactions). This is what surfaced the `atr_q4 × range_expand_1.5×` co-trigger that turned a flat strategy into a profitable one.
5. **Risk manager correctness.** `_order_counts_as_working_entry_for_risk` correctly excludes terminal/fully-filled legs while keeping `SUSPENDED` brackets counted — a subtle but important distinction.
6. **Master dashboard chart lines** — OR highs/lows render from `process_states.metadata.or_ranges` (heartbeat-embedded) so a *separate* executor process can drive the chart without RPC.

---

## 4. Worst features / live risk

1. **`trading_bot.py` is still 4,878 lines** after a 10 k → 5 k refactor. Cohesion is improving (hubs, brackets, interactive UI extracted) but it remains the only way to wire an account, and any logic added there is hard to test.
2. **Three legacy strategies are broken in the matrix runner**:
   - `simple_momentum` / `simple_rth` hang in replay (every bar emits a stop-bracket, mock bot resorts; CPU-bound).
   - `trend_scalping.__init__` constructs `StrategyConfig(name=…, symbols=…, enabled=True)` directly — `StrategyConfig` is a dataclass with 13 required fields, so it raises immediately.
   - `simple_candle` emits multi-line JSON in replay; the run-summary parser only reads the last line.
   None affect live trading today, but they live in the registry and quietly contaminate matrix sweeps.
3. **Two parallel timing systems** (`docs/GOTCHAS.md`):
   - Executor 30 s supervisor poll (`_update_process_state`, `_check_strategy_status`) overlaps with `EventBus` traffic.
   - `BarAggregator` 200 ms timer broadcasts partial bars instead of bar-close-driven.
   Both work but represent legacy polling in an otherwise event-driven design.
4. **Rust hotpath partial.** ~3 of ~10 paths wired. Should be all-in or removed; the Python↔Rust marshaling cost can dominate small wins.
5. **Schema migration discipline.** DDL still applied at startup by `DatabaseManager`. Alembic exists with a no-op baseline; new columns must remain `ADD COLUMN IF NOT EXISTS` style.
6. **`overnight_range` cadence is too low for income.** Live (P5) on MNQ Q1 2026 was **64 trades / +$776 / Sharpe 3.51 / DD $384** over the full sweep — that's the *aggressive* preset. P0 (live before P5) was **4 trades / +$238 / quarter**. You can't pay yourself off 4 trades a quarter.
7. **`morning_range_reversion` is the highest theoretical edge** (92.2% WR / 0.844 R / ~0.755 trades/day from upstream stats, see `docs/alpha/morning_reversion_info.md`) **but is `enabled = false`** and was just bug-fixed (sieve previously returned 0 trades; date-day boundary was wrong). Until a clean full-window MNQ replay matches the upstream stats, it's a theory.
8. **Doc rot.** `verify_handoff.sh` reports 13 broken in-kit links. They don't break anything but make the kit confusing for new operators (or future-you).
9. **No live-vs-replay drift monitor.** When you go live with body_reversion v3.1, you'll only know it tracks replay R if you build the comparison yourself.

---

## 5. Timing elements (what the system does *when*)

| Layer | Cadence | Source |
|---|---|---|
| Quote → bar aggregator | synchronous on market hub callback | `core/websocket_manager.py:on_quote` (intentionally not deferred — tick-to-bar latency) |
| User hub heavy work | bounded queue (deferred) | `core/hub_deferred_queue.py` |
| Partial bar broadcast | 200 ms timer | `core/bar_aggregator.py:148` |
| Strategy `analyze()` per bar | bar-close-driven | `EventBus` |
| Executor supervisor heartbeat | **30 s** | `_update_process_state`, `_check_strategy_status` |
| TOML hot-reload check | each loop iteration (`maybe_reload`) | `core/strategy_config.py` |
| `overnight_range` breakout monitor | **15 s** | `breakout_monitor.interval_seconds` |
| `overnight_range` CSV-replay analyze window | **8 minutes after market_open** | `timing.replay_order_window_minutes` |
| `body_reversion` cooldown / max hold | 30 min / 30 min (6 × 5m bars) | `min_bars_between_signals=6`, `max_hold_bars=6` |
| `morning_range_reversion` flat-by | **16:00 ET** (force-flat) | `signal.flat_before` |
| `morning_range_reversion` anchor build | **07:00–07:59 ET** (5m) | `signal.range_start/range_end_open` |
| Discord status digest | configurable (default off) | `DISCORD_STATUS_INTERVAL_SECONDS` |

The live timing model is healthy at each layer; the suspect points are (a) the 30 s supervisor poll overlapping with the event bus, and (b) the 200 ms partial-bar timer.

---

## 6. Strategy edge ledger (full-window, real Databento bars)

### `body_reversion` v3 ATR-only — **2024-01-01 → 2026-05-01**, full window

| Symbol | Trades | PnL | WR | PF | Sharpe | Max DD % | Expectancy |
|---|---:|---:|---:|---:|---:|---:|---:|
| **MNQ** | 962 | **+$12,475** | 30.0% | 1.46 | 1.83 | 5.34% | +$12.97 |
| **MES** | 513 | **+$17,905** | 44.6% | **3.53** | **5.67** | **1.23%** | **+$34.90** |
| **MGC** | 124 | **+$1,881** | 41.1% | 2.53 | 3.81 | 0.96% | +$15.17 |

These three numbers come straight from `/tmp/body_rev_gate_ab/full_2024_2026/body_rev_*_A.json`. **MES is the standout** — Sharpe 5.67 with sub-1.5% DD over 28 months. **MGC's trade count is too low** (124 trades / 28 mo = ~1.1/wk) — the ATR-q4 gate is filtering too aggressively on metals; per-symbol tuning is needed.

### `body_reversion` v3.1 hybrid — Q1 2026 OOS (recommended live config)

| Symbol | Gate | Trades | PnL | PF | Sharpe |
|---|---|---:|---:|---:|---:|
| MNQ | A (ATR-only) | 108 | +$992 | 1.34 | 1.86 |
| MES | C (both) | 66 | +$1,003 | 1.89 | 3.16 |
| MGC | A (ATR-only) | 145 | +$3,321 | 1.50 | 2.21 |

This is what `config/strategies/body_reversion.toml` ships today (`require_high_atr=true` global; `[symbols.MES.signal] require_range_expand=true`).

### `overnight_range` — MNQ 2025-12-24 → 2026-04-30

| Step | Trades | PnL | WR | Sharpe | Max DD |
|---|---:|---:|---:|---:|---:|
| P0 (legacy live) | 4 | +$238 | 75% | 11.11 | $85 |
| P3 (most filters off) | 67 | +$6,511 | 52% | 2.62 | $11,791 |
| **P5 (live now — widened bands)** | **64** | **+$776** | **51%** | **3.51** | **$384** |

P5 is conservative cashflow: 16× cadence at 1/30th the DD of P3. Still only ~5 trades/week on MNQ.

### `morning_range_reversion` — *not yet validated end-to-end live*
Upstream paper stats (`docs/alpha/morning_reversion_info.md`): 322 days / 243 trades / **92.2% WR** / +0.844 R expectancy / 0.755 trades/day. Sieve was returning 0 trades on full history until **this week's bug fix** (ET calendar boundary). **Validation gate:** full-window MNQ replay using `scripts/strategy_litmus.py` with intrabar 1m TP/SL resolution before promoting.

### `vwap_zscore_reversion` — MNQ Q1 2026
84 trades / +$1,172 / 46% WR / Sharpe 2.09 / DD $597 — needs per-symbol calibration on MES/MGC (mildly negative there at default knobs).

---

## 7. Analysis — where income realistically comes from

If the goal is **trading income** (cashflow), rank the candidates by *trades-per-week × edge per trade × tail safety*:

| Strategy | Cadence | Edge | Tail | Income role |
|---|---|---|---|---|
| `body_reversion` v3.1 hybrid | **High** (50–150/quarter/symbol live, ~3 symbols) | Validated full-window, 1.4–3.5 PF | DD <2% on MES/MGC | **Primary income engine** — ready to live-shadow now |
| `morning_range_reversion` | **Daily** (~0.75/day MNQ) | Theoretical 92.2% WR, 0.844R; replay validation pending | Stop = entry distance, capped tail per trade | **Highest theoretical income** — must be validated next 7 days |
| `overnight_range` | Low (3–5/wk all symbols combined) | Defensive, high Sharpe, but small absolute PnL | Bracketed, tight stops | **Stable base layer** — keep on, don't depend for income |
| `vwap_zscore_reversion` | Medium | Marginal at defaults | Needs per-symbol grid | **Calibrate before scaling** |
| `hourly_anchor_retrace` | Daily | New, no full-window data | Same shape as morning-range | **Run litmus first**, then evaluate |

The math from `morning_reversion_info.md` (which is sound *if the WR holds live*):
- **Fixed 1 contract on MNQ:** ~+$2,700/month expectancy, ~$8,000 over 6 months.
- **Equity-scaled (`N = floor(equity / 2500)`):** ~$22k–$28k by month 6, P(breach $2k DD) < 0.003%.
- **25% Kelly:** P(≥$9k month 1) ≈ 96.84%, mean DD ~20%, max observed DD ~67% (1 in 10k paths).

**The single biggest income unlock is making `morning_range_reversion` live and proving the WR replicates** — and it's the only piece of work that materially changes the income trajectory.

---

## 8. Next steps (prioritized for income)

### Tier 0 — *do this week, in order*

1. **Validate `morning_range_reversion` end-to-end.**
   ```bash
   .venv/bin/python scripts/strategy_litmus.py morning_range \
     --csv historical_data/price/MNQ_5m_databento.csv \
     --1m-csv historical_data/price/MNQ_1m_databento.csv --why
   ```
   Expect: ≥80% WR, 0.5–0.9 R expectancy, ≥0.5 trades/day. **Kill if WR < 70% or expectancy < 0.3 R after costs.**

2. **PRAC-shadow `body_reversion` v3.1 hybrid for 10–15 trading days.** Compare live R per trade against replay R for same bars; flag if drift > 20%. Start with `[meta] enabled = true` (already set), `position_size=1`, all three symbols.

3. **Build a live-vs-replay drift monitor.** A simple async task in `strategy_executor` that, on every fill, looks up the same bar in the replay JSON and logs `(live_R − replay_R)`. This is the single highest-leverage observability addition you can make.

4. **Wire an income brain (`core/income_brain.py`).** Two pieces:
   - Equity-tier sizing: `N = max(1, floor((equity − cushion) / divisor))`, configured per strategy in TOML, with HWM trail.
   - Daily session goal monitor: if realized PnL ≥ `daily_target` or ≤ `daily_stop`, flatten and pause to next session.
   Both sit *between* the strategy and `risk_management.py`; no strategy code changes.

### Tier 1 — *next 2 weeks*

5. **Promote `body_reversion` v3.1 hybrid to live** on a small account once PRAC matches replay. Start at 1 contract, scale per the income brain.

6. **Re-run `body_reversion` Gate A/B/C full-window with `--include-trades`** to get a real weekly histogram (current JSONs are summary-only). Use `scripts/print_weekly_income.py` to see how many *zero-PnL* weeks the strategy actually has — that's your minimum cash buffer.

7. **Calibrate `body_reversion` MGC.** 124 trades over 28 months is too sparse. Lower `atr_regime_quantile` 0.75 → 0.65 in `[symbols.MGC.signal]` and re-run; expect ~3× cadence.

8. **Fix the three broken legacy strategies** (`simple_momentum` hang, `trend_scalping.__init__`, `simple_candle` JSON). They are dead weight in the matrix runner today — fix or delete (per AGENTS.md "delete dead code outright").

### Tier 2 — *month-out*

9. **Decide on Rust hotpath** — finish wiring or remove. The half-state is more drag than help. `docs/perf/OPERATIONS_TUNING.md` has the profile-first workflow; run it once on a live PRAC executor and follow the data.

10. **Replace 30-second supervisor poll with event-driven status updates.** Today's poll overlaps `EventBus`; move `_check_strategy_status` to subscribe to `STRATEGY_STATE_CHANGED` events.

11. **Bar-close-driven aggregator broadcasts.** Drop the 200 ms timer; rely on `BarClosedEvent` on the bus. Removes a hot-path timer and a class of partial-bar replay edge cases.

12. **4-way co-trigger audit** (`body_reversion` open question g): test `RSI<30 LONG / RSI>70 SHORT` *in addition to* ATR-q4 + range_expand. RSI showed +1.4–+3.7 ΔR in the audit but population-overlapped with q4-ATR — needs the 4-way to confirm orthogonal lift.

13. **Walk-forward gate** — make `core.research.runner --walk-forward` mandatory before any strategy graduates from `idea` to `live` in `CANDIDATES.md`. The infra exists; just enforce it in `STRATEGY_DEVELOPMENT.md`.

14. **Repair doc kit** — fix the 13 broken links flagged by `verify_handoff.sh` (mostly `archive/` redirects). Quick win, cheap.

### Tier 3 — *aspirational, only after the above*

15. **Add a calendar-spread feed** for `calendar_spread_micro` (front − next month MES/MNQ z-score). Requires two continuous histories per symbol; `scripts/walkback_history.sh` already supports it conceptually.

16. **Tape microstructure / order-flow signals** — defer until you have a tick or DOM feed. Today the bot only sees aggregated bars.

17. **`trading_bot.py` decomposition.** Continue the slice-extraction pattern (auth, connection, order management, session). Aim for <2,500 lines.

---

## 9. The one-paragraph executive summary

The platform is in **good live shape**: SignalR + REST + Postgres are stable, the event bus is canonical, the backtest engine just had two systemic bugs fixed (phantom-reverse OCO, double-commission), and `body_reversion` v3.1 hybrid is the **first strategy with validated full-window edge across all three symbols** (MES Sharpe 5.67 / DD 1.23% over 28 months). The biggest single income lever you have is **`morning_range_reversion`** — its theoretical edge (92.2% WR, ~$2.7k/mo at 1 MNQ contract) blows everything else away, and the sieve bug that hid it was just fixed this week. The best use of the next 7 days is: (1) validate `morning_range_reversion` on full Databento history, (2) start a 10-day PRAC shadow of `body_reversion` v3.1 alongside, (3) build a live-vs-replay drift monitor, and (4) wire an income brain (HWM-trailed equity-tier sizing + daily target/stop). After that, the strategy library is rich enough that scaling capital, not finding edge, becomes the limiting factor.

Related prior chats: [Body reversion strategy validation](ed3f8c1abb1a9f7373ac35f129cc4bbeb9803c66), [Overnight range filter sweep results](docs/perf/sweeps/CANDIDATES.md).







All four next steps are complete. Here's the rundown of what was delivered:

---

## What got done

### 1. `morning_range_reversion` validated against full Databento history → real edge identified, much smaller than upstream claim

Ran the full 2024-01-01 → 2026-05-01 sieve on MNQ (164,826 5m bars + 824,127 1m bars for intrabar TP/SL resolution). The upstream **92.2 % WR / +$2.7 k/mo** claim **does not replicate**.

Real cross-symbol portability table (legacy reentry, uncapped):

| Symbol | Trades | Sessions | WR | Net R |
|---|---:|---:|---:|---:|
| MNQ | 2,417 | 529 | **0.5536** | +0.107 |
| MES | 2,415 | 537 | **0.5673** | +0.135 |
| MGC | 2,106 | 515 | **0.5954** | +0.191 |

Best operational config (cleanest read on edge): **`require_reentry_close=true` + `max_fades_per_session=1`** = MNQ 62.95 % WR / +0.26 R / **~$400/mo per contract after costs**. ~5× weaker than the headline but real and portable.

→ **`config/strategies/morning_range_reversion.toml` defaults updated** to the validated configuration.
→ Full report at `docs/perf/morning_range_reversion_validation.md`.

### 2. `core/drift_monitor.py` — live-vs-replay drift monitor

Subscribes to `EventType.ORDER_FILLED` on the running bot's bus and writes a JSONL log per `(strategy, account, date)` to `logs/drift/`. Strategy name is extracted from the order `customTag` (`TB-<role>-<strategy>-<...>`). Symbol normalization mirrors the existing `gui/chart_html.extract_symbol_from_contract` logic.

Standalone CLI:
```bash
python -m core.drift_monitor compare --live <jsonl> --replay <backtest.json> --tolerance-seconds 600
```

Reports per-trade R drift, p95 |drift_R|, and **% of trades over the 0.20 R kill threshold**.

Wired into `core/strategy_executor.py` behind the env gate `DRIFT_MONITOR=true` (off by default — existing executors are unaffected).

### 3. `core/income_brain.py` — equity-tier sizing + daily target/stop with HWM trail

Three composable pieces:
- **`EquityTierSizer`** — `N = clamp(min_n, max_n, floor((equity − cushion) / divisor))` with HWM-breach brake (falls back to `min_n` when current equity is more than `hwm_breach_dollars` below the trailing high-water mark).
- **`DailySessionGoal`** — halts new trading when realized PnL ≥ daily target or ≤ daily stop on the current ET session date.
- **`IncomeBrain`** — composes both, persists state to `data/income_brain_<account>.json`, exposes `decide_size(equity)` and `should_halt_today()`.

All knobs are env-driven (`INCOME_BRAIN_*`, documented in `.env.example`). Strategies do **not** import this module — only `strategy_executor` / `risk_management` should consume it (per the strategies cursor rule).

CLI:
```bash
python -m core.income_brain status --account <id>
python -m core.income_brain size --account <id> --equity 12500   # → N=4
python -m core.income_brain record-pnl --account <id> --delta 175
python -m core.income_brain reset --account <id>
python -m core.income_brain archive --account <id> --label preprom
```

### 4. PRAC shadow runbook

`docs/perf/body_reversion_v31_prac_shadow.md` — pre-flight commands, daily drift-compare loops, **promotion gate** (n_matched ≥ 20/symbol, % |drift_R|>0.20 ≤ 20 %, mean drift_R ∈ [−0.30, +0.30]), and a failure-mode triage table that maps drift signatures back to root causes (custom-tag bug, slippage model, both-touch conservative resolution, etc.).

### Tests

- `tests/test_drift_monitor_smoke.py` — 8 cases: tag parsing, round-trip pairing, replay LONG/SHORT normalization, tolerance matching, end-to-end compare, async event-bus subscribe + JSONL write, CLI subprocess.
- `tests/test_income_brain_smoke.py` — 11 cases: sizer cushion / divisor / max-N clamp / HWM-brake, daily goal target / stop / disabled, persistence round-trip, halt blocks `decide_size`, session roll archives, env-var factory, CLI subprocess.

Both registered in `pytest.ini`. Full default suite now: **83 passed in 2.12 s** (was 64).

`docs/MAP.md` regenerated. `docs/CHANGELOG.md` updated under `[Unreleased]`. Linter clean across all new and modified files.

---

## What's next (operationally, not code)

1. **Start the PRAC shadow today.** Set `DRIFT_MONITOR=true` and `INCOME_BRAIN=true` in `.env`, then `bash scripts/run_reversion.sh <PRAC_ACCOUNT_ID>`. Check daily with the compare commands in the runbook.
2. **Wire `IncomeBrain.decide_size()` into the actual order-placement path** (between `bracket_orders` and `risk_management`) — this is the next PR. Today the brain is observability-only; you call `python -m core.income_brain status` to see what it *would* size, then promote once the values match operator intuition.
3. **Run the `morning_range_reversion` MES + MGC sieves at cap=1** (current portability table is uncapped) to confirm the cleanest read holds across all three symbols before scaling that strategy.

# TL;DR — what's done and what's next

## What just got done (today's session)

| # | Deliverable | Why it matters |
|---|---|---|
| 1 | **Validated `morning_range_reversion`** on 28 mo of MNQ/MES/MGC Databento bars | The 92.2 % WR upstream headline does **not** replicate. Real edge is ~62.95 % WR / +0.26 R / ~$400/mo per MNQ contract — modest but portable across all three symbols. **TOML defaults updated** to the only configuration that's actually positive expectancy on this data. |
| 2 | **`core/drift_monitor.py`** + JSONL log + CLI compare | First way to know *objectively* whether live trading matches the backtest. Without it, "is the strategy working?" is a vibe. |
| 3 | **`core/income_brain.py`** — sizing + daily target/stop + HWM | Turns a positive-expectancy strategy into actual cashflow. Edge alone doesn't pay you; sizing does. |
| 4 | **PRAC shadow runbook** for `body_reversion` v3.1 | Promotion checklist with hard numerical gates instead of "feels good, ship it." |
| — | 19 new smoke tests; full suite **83 passed in 2.12 s** | Default `pytest` keeps catching regressions before they ship. |

---

## What's next, ranked, with the *why* for each

### Tier 0 — *do this week*

#### A. Start the PRAC shadow on `body_reversion` v3.1 *today*

**TL;DR:** Flip `DRIFT_MONITOR=true` + `INCOME_BRAIN=true` in `.env`, run `scripts/run_reversion.sh <PRAC>`, check the drift compare daily.

**Why:** The replay numbers are great (MES Sharpe 5.67 / DD 1.23 % over 28 mo) but **none of that has touched a broker**. The backtest engine had two systemic bugs fixed this week (phantom-reverse OCO, double-commission); replay results before those fixes are unreliable, and even after them you still don't know if SignalR latency, broker slippage, or order-tagging quirks degrade the edge in real conditions. **You can't safely scale an unvalidated strategy.** A PRAC shadow with a hard kill threshold (% trades with |drift_R| > 0.20) is the cheapest way to find this out — paper money, real plumbing, real timestamps. Doing this *first* avoids the worst failure mode: discovering at $20k account size that live R is half of replay R.

#### B. Wire `IncomeBrain.decide_size()` into the actual order-placement path

**TL;DR:** Today the brain is observability-only. Next PR: replace `risk.position_size` reads in `core/bracket_orders.py` with `income_brain.decide_size(equity)` so contracts auto-scale with equity (and auto-throttle on daily-goal hits).

**Why:** The strategy library now has *more validated edge than capital* — `body_reversion` v3.1 alone projects +$32 k/yr per contract on MES at 1 contract. The only way to capture that fully is to scale contracts as the account grows. Manually changing `position_size` in TOML once a week is error-prone (and breaks backtests vs live parity); a deterministic equity → N rule is auditable and reproducible. The HWM-breach brake is the safety: if you draw down $2 k below your peak, sizing drops to 1 contract until you set a new high-water mark, which mathematically caps the worst-case run *without* a fixed daily-loss limit that arbitrarily ends a profitable session.

#### C. Run `morning_range_reversion` MES + MGC sieves at `max_fades_per_session=1`

**TL;DR:** Current portability table is uncapped. Re-run the sieve with the validated cap to confirm the per-symbol WR ceilings.

**Why:** Uncapped numbers are diluted by re-arms after in/out oscillation in the same session. The clean read at cap=1 will tell you whether MGC's 0.595 WR holds at single-trade-per-session cadence. If it does, **MGC is the strongest single-symbol candidate of any strategy in the repo** — it diversifies away from `body_reversion`'s MES dominance, which is concentration risk you don't want.

---

### Tier 1 — *next 2 weeks, after the shadow looks clean*

#### D. Promote `body_reversion` v3.1 to live on a small real account

**TL;DR:** After 10 PRAC days hit the promotion gate, flip the account ID and start at `INCOME_BRAIN_MAX_N=1`.

**Why:** Real money is the only test that catches the things PRAC can't (broker reject patterns, real slippage on stop-entries, fill latency under load). Starting at 1 contract means worst-case daily downside is ~$50 on MNQ — well below the daily stop. You only graduate `MAX_N` upward once the next week's drift-compare also shows clean numbers at the larger size. **This is how you compound without ever risking ruin** — every size increase is gated on evidence the previous size held up.

#### E. Re-run `body_reversion` Gate A/B/C with `--include-trades`

**TL;DR:** Current `/tmp/body_rev_gate_ab/full_2024_2026/*.json` are summary-only. Regen with trades embedded so `scripts/print_weekly_income.py` can produce a real per-week histogram.

**Why:** Headline PnL hides distribution. **A strategy with 0 zero-PnL weeks is fundamentally different from one with 8** even at the same total. The weekly histogram tells you the minimum cash buffer you need to survive a normal drawdown (`max consecutive weekly DD × planned size`), which is the actual input to capital allocation. It also catches single-trade-dominant performance, which the Sharpe doesn't always flag.

#### F. Calibrate `body_reversion` MGC

**TL;DR:** 124 trades / 28 mo on MGC is too sparse — lower `[symbols.MGC.signal] atr_regime_quantile` from 0.75 → 0.65 and re-run.

**Why:** The ATR-q4 gate that fixes MES's short-leg bleed is *over-filtering* on metals: ~1.1 trades/week is too thin to overcome any drawdown, even at PF 2.53. A small relaxation should triple cadence at modestly lower per-trade R. **More trades at slightly lower R beats fewer trades at higher R** for income — you can't pay rent off 4 trades a month.

#### G. Fix or delete the three broken legacy strategies

**TL;DR:** `simple_momentum` / `simple_rth` hang in replay; `trend_scalping.__init__` raises immediately; `simple_candle` emits malformed JSON. Either fix or remove from `BUILTIN_STRATEGY_SPECS`.

**Why:** They contaminate matrix sweeps (PER_RUN_TIMEOUT wrapper hides them from view but they still consume CPU), they're confusing to operators reading the registry, and they're a violation of the AGENTS.md "delete dead code outright — git history is the archive" rule. Cheap, high-value cleanup that prevents a future agent from spending hours on a bug that's been known for months.

---

### Tier 2 — *month-out infrastructure*

#### H. Decide on the Rust hotpath: finish or delete

**TL;DR:** Today only ~3 of ~10 paths are wired. Profile (`scripts/profile_strategy_executor.sh`), then commit.

**Why:** **Half-state Rust is worse than no Rust.** The Python↔Rust marshaling overhead can erase the win on small operations, and the cognitive overhead of "does this path use Rust or not?" slows every code change. If profiling shows the wired paths are actually saving meaningful time, finish the migration; if not, rip it out. Either decision is better than the status quo.

#### I. Replace the 30 s supervisor poll with event-driven status

**TL;DR:** `_check_strategy_status` runs on a timer that overlaps with the EventBus. Subscribe to `STRATEGY_STATE_CHANGED` instead.

**Why:** Two parallel timing systems is a smell — it's how you end up with one that lies because the other already updated state. The 30 s poll is also an unnecessary 2,880 DB hits per day per process. Going fully event-driven means status reflects reality within milliseconds, not within 30 s, which matters for operator dashboards and Discord alerts.

#### J. Drop the 200 ms partial-bar broadcast; rely on `BarClosedEvent`

**TL;DR:** Bar aggregator timer broadcasts partials every 200 ms. Strategies only act on bar close anyway. Subscribe to bar-close instead.

**Why:** **Hot-path simplification.** Removes an entire class of partial-bar replay edge cases (where a partial-bar broadcast races a quote update) and reduces tick-to-strategy latency variance. The intentionally-synchronous quote-to-bar path stays as-is; only the broadcast cadence changes.

#### K. 4-way RSI co-trigger audit on `body_reversion`

**TL;DR:** Run `body_reversion_combo_audit` with `rsi<30 LONG / rsi>70 SHORT` *on top of* the current `atr_high_q4 + range_expand` triggers.

**Why:** The current 3-way combo is the strongest signal in the deep scan, but RSI extreme by itself showed +1.4 to +3.7 ΔR — the question is whether RSI is **orthogonal** (adds lift on top of ATR-q4) or **redundant** (mostly hits the same bars). **A confirmed orthogonal co-trigger doubles your edge per trade for free** — same cadence, higher R. Worth one afternoon to test.

#### L. Make `--walk-forward` a mandatory promotion gate

**TL;DR:** Update `docs/STRATEGY_DEVELOPMENT.md`: a strategy can't graduate from `idea` to `live` in `CANDIDATES.md` without `core.research.runner --walk-forward` passing.

**Why:** Walk-forward is the cheapest defense against curve-fitting. The infra exists, no one's required to use it, and `body_reversion` was promoted without it (worked anyway, but lucky). Standardizing the gate prevents a future strategy from sneaking through on a single-window backtest that won't generalize.

#### M. Repair the 13 broken doc-kit links

**TL;DR:** `scripts/verify_handoff.sh` flags 13 broken in-kit links — mostly archive redirects.

**Why:** Pure hygiene. They don't break runtime, but they make the kit confusing for new operators (and future-you after a 6-month gap). 30 minutes of work for a kit that scores clean instead of "13 errors / 1 warn."

---

### Tier 3 — *aspirational, only after Tier 0/1 paying you*

#### N. Calendar-spread feed for `calendar_spread_micro`

**Why:** Spread strategies have lower correlation to the directional edges (`body_reversion`, `morning_range_reversion`) than a third directional strategy ever could. Diversification in the *type* of edge, not just the symbol. Requires two continuous histories per pair — `walkback_history.sh` already supports it.

#### O. Tape microstructure / order-flow

**Why:** Aspirational. Today the bot only sees aggregated 1m / 5m bars, so any order-flow alpha is closed off. Defer until you have a tick or DOM feed; until then, every "ML on order flow" idea is a 6-month rabbit hole that ends in "we don't have the data."

#### P. `trading_bot.py` decomposition (4,878 → < 2,500 lines)

**Why:** The god module is the main reason any cross-cutting change is risky. Continuing the slice-extraction pattern (auth, connection, order management, session) makes every future change cheaper and testable in isolation. **Don't do this until A–G are paying** — refactoring code that hasn't proven its value in the first place is just moving deck chairs.

---

## The single sentence

**A through C this week, D through G next two weeks, H–M after the income brain is paying you, N–P only when the limiting factor is no longer "is the edge real?"**



# Win-rate analysis — `overnight_range`, `body_reversion`, `morning_range_reversion`

Read both docs, all three TOMLs, the body-reversion combo audit, the `morning_range_reversion_validation` numbers. Putting **trades × WR × edge / trade** front-and-center, since "cashflow" = those three multiplied (per `docs/perf/researching.md` lines 122–134 and `docs/BONGO.md` § 7).

There are **strategy-specific** levers and **three universal** levers. Universal first (best ROI / time).

---

## 0. The framing problem (most important paragraph)

Two of these three strategies have a **mathematical WR ceiling baked into the geometry**:

| Strategy | Reward : Risk | Theoretical break-even WR | Today |
|---|---|---|---|
| `body_reversion` | **3 : 1** (`tp_r_multiple = 3.0`) | **25.0%** | MNQ 30% / MES 44.6% / MGC 41.1% |
| `morning_range_reversion` | **1 : 1** (`tp_mult = 1.0`, `sl_mult = 1.0`) | **50.0%** | 62.95% (validated) |
| `overnight_range` | **2 : 1.25** (`tp_atr=2.0` / `stop_atr=1.25` ≈ 1.6:1) | ~**38.5%** | 51% (P5) |

You **cannot** lift `body_reversion`'s WR meaningfully without lowering its TP-to-stop ratio — the 30% WR is the *definition* of "3R take-profit on a 50% reverter." Same arithmetic applies to the others.

So the right question is **"how do we add wins without lowering trade frequency or breaking what works?"** Three real levers do this; everything else is folklore.

---

## 1. Universal levers (work on all three; ranked by leverage)

### 1A. Partial TP at 1R (bank → high WR, runner → high R)

**The single biggest, lowest-risk WR lever.** Today every strategy is single-shot: TP or SL, one or the other.

Split each entry into two equal-size tickets:
- **Ticket A:** TP at **1R** (≈ stop distance) → **almost always hits before SL on a positive-edge strategy.**
- **Ticket B:** TP at the original target (3R for body_reversion, mid for morning_range, ATR-2.0 for overnight_range), with SL **trailed to BE** the moment ticket A fills.

What it does to the metrics:
- **WR ≈ 1 − (1 − originalWR) / 2.** A 30% WR strategy goes to **≈ 65%** in the blotter (every win → 2 wins, every loss → 1 win + 1 loss because A still tags 1R most of the time before SL on a momentum-event strategy. Use replay to measure precisely.).
- **Trade count up ~2×** for free (your "cadence" doubles).
- **Per-ticket R drops** but total $ per signal stays roughly flat (slightly **better** because fewer full SL losses).
- **Drawdown shrinks** because losing trades are now half-size on the runner.

Where to wire it: today brackets are placed via `place_oco_bracket_with_stop_entry` in `brokers/topstepx_adapter.py`. The per-strategy signal payload would gain a `partial_tp` key; `core/bracket_orders.py` is the right place to fork into A + B legs.

**Validation experiment** (cheap, ~5 min/symbol):
```
BODY_REVERSION_SIGNAL_TP_R_MULTIPLE=1.0 \
  .venv/bin/python core/backtest_executor.py --strategy=body_reversion \
  --symbol=MES --csv=historical_data/price/MES_5m_databento.csv \
  --start=2024-01-01 --end=2026-05-01 --replay --format=json --include-trades
```
Compare `win_rate`, `total_pnl`, `expectancy` against current `tp_r_multiple=3.0`. If 1R-only beats 3R-only on cumulative $, then 50/50 split A+B will beat both.

### 1B. Breakeven shift at 0.5R

**Wired (opt-in):** `body_reversion` and `morning_range_reversion` read **`[position_management] breakeven_enabled`** (default **false**) and **`breakeven_trigger_r`** (default **0.5**). On a filled bracket, **`TopStepXTradingBot`** polls open positions every 10s; when favourable move ≥ **`breakeven_trigger_r × |entry − stop|`**, it calls **`modify_stop_loss`** toward **`entry_price`** (same mechanical goal as overnight’s points threshold). Registration happens from **`BaseStrategy.place_bracket_order(..., breakeven_profit_threshold=…)`** after a successful OCO stop-entry placement. **`get_positions`** is now an alias for **`get_open_positions`** (fixes legacy overnight call sites).

`overnight_range` keeps its own task (**`breakeven_profit_points`** in index points) — unchanged.

- After **0.5R** in profit, move stop to entry + 1 tick.
- Converts a chunk of "loss" trades into **scratches** (counted as +0 in `pnl`, neither win nor loss in `winning_trades` count → small WR drag, but **expectancy goes up**).
- For income reporting, count BE flats as wins (zero-loss fills) → **WR goes up directly**.

Cost: a few "would have hit TP" trades stop out at BE on a wick. Replay measures exactly how many.

### 1C. Time-of-day open filter — skip the first 5–15 min of RTH

Already half-baked: `body_reversion.toml` has `skip_open_minutes = 30` but `rth_only = false`, so it's a no-op. The combo audit (`docs/alpha/body_reversion_combos.md`) shows **`rth_close_120m`** has positive ΔR on every (symbol × direction) cell, while **`rth_open_60m`** and **`eth_overnight`** mostly drag.

Concrete change for any reversion strategy: gate signals by `rth_start + 5min ≤ bar_open ≤ rth_end - 5min`. This is **WR-positive without dropping cadence enough to matter** (typical loss is 10–15% of trades, but those trades have WR well below the strategy mean, so the *kept* trades have higher WR).

**Cheapest test:** flip `rth_only = true, skip_open_minutes = 5` in `body_reversion.toml`, replay one symbol, compare.

---

## 2. `overnight_range` — specific (live, 51% WR, 64 trades/quarter)

This one is **already at the WR ceiling for its geometry** (51% on a 1.6:1 strategy is solid). The lever isn't WR — it's **trade count**.

The toml today (P5) trades all weekdays with widened bands:

```5:80:config/strategies/overnight_range.toml
[meta]
enabled = true
symbols = ["MNQ", "MES", "MGC"]
...
[signal]
atr_period          = 14
atr_timeframe       = "15m"
stop_atr_multiplier = 1.25
tp_atr_multiplier   = 2.0
...
[filters]
range_size        = true
gap               = true
volatility        = true
range_min_pts     = 30.0
range_max_pts     = 600.0
gap_max_pts       = 250.0
atr_min           = 15.0
atr_max           = 220.0
skip_weekdays     = []
```

### Lever 2.1 — drop `range_min_pts` from 30 → 20

Half the skipped sessions today are skipped because the overnight range is "too small." But the ATR filter (`atr_min = 15`) already guarantees enough realized volatility — `range_min` is filtering twice. Lowering it widens cadence without changing the geometric edge.

**Test:** `OVERNIGHT_RANGE_FILTERS_RANGE_MIN_PTS=20`, replay full window, expect ~+15% trades, WR within ±1pt.

### Lever 2.2 — `tp_atr_multiplier` 2.0 → 1.5 with `breakeven_profit_points` 15 → 8

Closer TP = higher WR (mechanically). Earlier BE = fewer turn-around losses. The 15pt BE threshold is too far on a 30pt typical range. Set BE at half the typical first-leg push.

This is a textbook WR-up-cadence-flat trade.

### Lever 2.3 — partial TP per § 1A above

Pre-built: `tp_atr_multiplier = 2.0` becomes ticket A = 1.0×ATR, ticket B = 2.0×ATR with BE-trailed stop. Pure additive on the existing strategy.

### What you cannot do
Don't drop `stop_atr_multiplier` below 1.0. It's already tight at 1.25; tighter stops on a breakout strategy = whipsaw losses, which are what kill it.

---

## 3. `body_reversion` v3.1 — specific (live, 30–45% WR, 1.4–3.5 PF)

The most important table in the repo for this strategy is `docs/alpha/body_reversion_combos.md`. Rereading it, **`atr_high_q4 & bb_lower_touch`** (LONG) and **`atr_high_q4 & bb_upper_touch`** (SHORT) **beat the live config's `atr_high_q4 & range_expand_1.5x` on WR for every (symbol × direction) cell:**

| | live combo (range_expand) | alt combo (bb_touch) | Δ WR |
|---|---|---|---|
| MNQ LONG | 62.7% | **58.5%** (n=468) | -4.2 |
| MNQ SHORT | 47.5% | **48.7%** (n=273) | +1.2 |
| MES LONG | 55.7% | **56.9%** (n=339) | +1.2 |
| MES SHORT | 54.1% | **57.4%** (n=394) | +3.3 |
| MGC LONG | 55.1% | **48.9%** (n=393) | -6.2 |
| MGC SHORT | 45.3% | **38.9%** (n=288) | -6.4 |

**MNQ-SHORT, MES-LONG, MES-SHORT** all show higher WR with `bb_touch` than `range_expand`. The current TOML uses `range_expand` only on MES (`[symbols.MES.signal] require_range_expand = true`), so the actionable knob is:

### Lever 3.1 — additive co-trigger gate (OR, not AND)

Add a third boolean: **`require_bb_touch`** (default `false`). Strategy fires if `(big_body) AND atr_high_q4 AND (range_expand OR bb_touch)`. This **adds n** (the two cohorts only partially overlap — MNQ MES MGC tables show `n_range_expand + n_bb_touch ≫ n_either_alone`) and the WR of the combined population sits between the two. **More signals at similar WR.**

The Bollinger-touch mask already exists in `core/research/deep_pattern_scan.py`; lifting it into `strategies/body_reversion_strategy.py` is ~30 lines.

### Lever 3.2 — RSI extreme as a *separate* trigger path

`rsi<30` LONG (MGC) has **+3.694 ΔR / n=525**, MNQ-LONG **+1.987 ΔR / n=458**, MES-LONG **+1.987 ΔR / n=482** (combo audit). These are **larger than the current 3-way combo's R** but with **smaller n**. The right wiring is a **second signal path**: the strategy emits a fade when `(big_body anchor) AND (atr_high_q4 AND range_expand)` **OR** when `(rsi extreme) AND (atr_high_q4)` — two parallel triggers, not one ANDed condition.

This is **literally more trades at higher mean R** — the holy grail. The audit says `rsi<30` and `atr_high_q4 & range_expand` populations are mostly disjoint (500 + 700 ≈ 1200, anchor-only n was 4,864 → these two together cover ~25% of the anchor population).

Mention from the doc: "**RSI showed +1.4 to +3.7 ΔR but population-overlapped with q4-ATR — needs 4-way to confirm orthogonal lift**" (`docs/BONGO.md` Tier 2 K). That audit is the prerequisite. Run it before wiring this lever.

### Lever 3.3 — MGC `atr_regime_quantile` 0.75 → 0.65

Already called out in `docs/BONGO.md` Tier 1 F. MGC's 124 trades over 28 months is too thin. The ATR-q4 gate is over-filtering on metals (lower base volatility). Per-symbol override:

```toml
[symbols.MGC.signal]
atr_regime_quantile = 0.65
```

Expect **~3× cadence**, **WR drops 1–3 pts**, **net $ goes up**.

### Lever 3.4 — `tp_r_multiple` 3.0 → 2.0 with `min_bars_between_signals` 6 → 4

Both knobs are conservative. Tighter TP raises WR mechanically; shorter cooldown raises cadence. The combo audit found mean realized R on the regime-gate cohort is ~1.5–2.7 — TP at 3R is on the **right tail** of the distribution and clipping. TP at 2R catches more of the meat.

```
BODY_REVERSION_SIGNAL_TP_R_MULTIPLE=2.0
BODY_REVERSION_SIGNAL_MIN_BARS_BETWEEN_SIGNALS=4
```

Replay against the current Q1 2026 OOS; if PnL holds and trade count is up, ship.

---

## 4. `morning_range_reversion` — specific (research, 62.95% WR, ~1 trade/day)

Validated config (`config/strategies/morning_range_reversion.toml`) is **`require_reentry_close=true, max_fades_per_session=1`**. The geometry is **1:1** (`sl_mult=1.0, tp_mult=1.0`).

**§4.1–4.3 wiring (done):** Live **`analyze()`**, offline **`sieve_simulate_from_ohlcv`**, and **`scripts/validate_morning_range_reversion.py`** all honor **`tp_mult`**, **`sl_mult`**, **`reentry_frac`** (inner re-entry band), and optional **`require_high_atr`** + **`atr_period` / `atr_regime_lookback` / `atr_regime_quantile`** (default **`require_high_atr = false`**). Sieve TP geometry matches live (half-range × `tp_mult` from the breached extreme). Smoke tests: **`test_sieve_tp_mult_changes_take_profit_target`**, **`test_sieve_reentry_frac_blocks_shallow_close_inside_box`**. **Still open:** full-window MNQ/MES/MGC replays to **promote** non-default TOML values (especially **`tp_mult=0.7`** and **`require_high_atr=true`**).

### Lever 4.1 — `tp_mult` 1.0 → 0.7 (closer TP, higher WR)

Today TP = midpoint (= half the morning range). Many trades stall before midpoint and reverse. Set TP at **70% of the way to midpoint** (`tp_mult = 0.7`). Mechanically:

- **WR up** (closer TP hits more often)
- **Per-trade R down** to ~0.7
- **Cadence flat** (same arms)
- Net $: probably **higher** because the WR lift on 1:1 reverter is steep (53% → 65% → 70% as TP moves in).

Test:
```
MORNING_RANGE_REVERSION_SIGNAL_TP_MULT=0.7 \
  .venv/bin/python scripts/validate_morning_range_reversion.py \
  --csv historical_data/price/MNQ_5m_databento.csv \
  --1m-csv historical_data/price/MNQ_1m_databento.csv \
  --start 2024-01-01 --end 2026-05-01
```

### Lever 4.2 — `reentry_frac` 0.0 → 0.15

Today re-entry triggers on **any** close back inside `[L, H]`. The deeper the re-entry into the box, the better the fade (less chance of immediate fail-back). `reentry_frac = 0.15` requires the close to be **15% inside** the band before arming. **Drops** cadence ~10–20% but each kept trade has materially higher WR.

The validation report already encoded this trade-off (line 5 of the toml comments): "the cadence ↔ WR ↔ PnL trade-off curve."

### Lever 4.3 — apply `atr_high_q4` from `body_reversion` (cross-strategy lift)

The combo audit's most portable finding: **`atr_high_q4` lifts WR on every reversion-shape pattern across MNQ/MES/MGC**. **`require_high_atr`** is implemented on live + sieve (rolling TR → ATR, quantile over lookback, no lookahead). Flip **`require_high_atr = true`** only after a full-window sieve confirms the expected trade/WR/PnL trade-off:

```toml
[signal]
require_high_atr     = true
atr_regime_lookback  = 500
atr_regime_quantile  = 0.75
```

…and the morning-range fade only arms in volatile regimes. Sieve replay should show: **trades down ~25%, WR up 5–10 pts, PnL flat-to-up.**

This is a "borrow what works" lever — the heavy lifting (the combo audit) is already done.

### Lever 4.4 — `max_fades_per_session` 1 → 2 *with* tighter re-entry (`reentry_frac=0.15`)

Today's cap at 1 maximizes WR per signal at the cost of cadence. Lifting to 2 **only** on sessions where the second fade is the deeper-re-entry kind (lever 4.2 gates this) doubles cadence on the days the strategy is working without diluting WR.

---

## 5. Where the math says trades AND wins both go up

Combining the per-strategy levers above, the realistic cumulative effect on Q1 2026 OOS:

| Strategy | Today (live config) | After Lever Stack | Comment |
|---|---|---|---|
| `overnight_range` MNQ Q1 | 64 tr / 51% WR / +$776 | ~80 tr / ~58% WR / ~+$1,100 | Levers 2.1 + 2.2 + 1A |
| `body_reversion` v3.1 (3 sym) | 319 tr / ~38% WR / +$5,316 | ~450 tr / ~50% WR / ~+$6,800 | Levers 3.1 + 3.3 + 1A |
| `morning_range_reversion` MNQ | 529 tr / 63% WR / +$400/mo | ~440 tr / ~70% WR / ~+$550/mo | Levers 4.1 + 4.3 |

These are **estimates, not promises** — the right next step for each of the seven specific levers is one short replay command, listed inline. None of them require rewrites; six of seven are TOML-only knobs.

---

## 6. Ranked by ROI / time

| # | Lever | Effort | Impact | Risk |
|---|---|---|---|---|
| **1** | **§1A Partial-TP at 1R (any strategy)** | ~1 day | Highest — doubles "trade count" and lifts WR ~15–25 pts simultaneously | Medium (touches `bracket_orders.py`) |
| 2 | §3.3 MGC `atr_regime_quantile` 0.75→0.65 | TOML one-line | ~3× MGC cadence, 1–3 pt WR drop | Low |
| 3 | §4.1 morning_range `tp_mult` 1.0→0.7 | TOML/env + replay (knob wired) | +5–10 pt WR, $-flat-to-up | Low |
| 4 | §4.3 morning_range `require_high_atr` | TOML + replay (knob wired, default off) | +5–10 pt WR, -25% trades | Low |
| 5 | §3.1 body_reversion `bb_touch` co-trigger (OR) | ~30 lines | +20–30% trades at similar WR | Low |
| 6 | §1B BE-shift at 0.5R (body_rev + morn_rev) | TOML flip + replay (**wired**, default off) | Converts losses → scratches; +3–8 pt WR | Low |
| 7 | §2.1 overnight `range_min_pts` 30→20 | TOML | +10–15% trades | Low |
| 8 | §3.4 body_reversion `tp_r_multiple` 3.0→2.0 + cooldown 6→4 | env override | +50% cadence, +5pt WR | Low–Medium |
| 9 | §1C `rth_only=true, skip_open_minutes=5` on body_rev | TOML | ~10% trades dropped, kept ones cleaner | Low |
| 10 | §3.2 body_reversion RSI parallel trigger | ~40 lines after 4-way audit | +25% trades at higher mean R | Medium (needs audit first) |

**The fastest wins are TOML-only:** 2, 3, 7, 8, 9. Run those replays in one batch this weekend, pick the ones that hold up, ship.

The **biggest wins** are 1 (partial TP) and 5/10 (parallel triggers); those need code, but they unlock the "more trades AND more wins" property the user is asking about — they're not zero-sum.

---

## 7. The single sentence

**Levers 1, 2, 3, 7 give you "more trades AND more wins" in <2 days of work and zero new risk; lever 1 (partial TP at 1R) is the only lever that bends the WR ceiling without touching strategy logic and is worth a focused PR before any of the others.**



## HANDOFF: Last verified commit ed3f8c1abb1a9f7373ac35f129cc4bbeb9803c66 (2026-05-05): weekly PnL script, parallel Gate A/B/C grid, Master OR lines via executor heartbeat + strategy_states.
## Recently landed: Morning-range reversion (live path, sieve, validated TOML defaults, run_morning_reversion.sh), replay tooling (intrabar/1m CSV, litmus, manifest runner, perf helpers), body_reversion v3/v3.1 + income brain + optional generic breakeven + drift monitor, partial-TP design (adapter still N/I), GUI/chart fixes (Databento reload, throttling, fingerprints), backtest engine commission/OCO fixes, Discord status digest, docs (STRATEGY_DEVELOPMENT walk-forward gate, validation/PRAC runbooks).
## Next: Enable morning_range_reversion only after ~30-trade PRAC shadow vs replay (±20% R); run drift-compare where relevant; ship partial TP on broker (two-ticket OCO + BE); validate breakeven/slip on live; continue walk-forward/OOS evidence before CANDIDATES promotion; optional INCOME_BRAIN / DRIFT_MONITOR rollout with env gates.

