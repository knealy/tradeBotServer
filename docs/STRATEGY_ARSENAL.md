# Strategy Arsenal

Last refresh: 2026-06-05 PM **R28 `morning_range_reversion` material PnL lift via MGC `max_range_width_points` 65 → 46**. User pushed back on R27 as "miniscule" and asked for either a material improvement or retirement. Two more sweep rounds (≈ 50 trials × 3 windows: dynamic-SL-from-TP / partial-TP / breakeven / signal-quality / regime filters) found the real lever — MGC's morning range-width upper bound. R28 **Pareto-dominates R27 across every window with identical drawdown**:

| Window | R27 truth | R28 truth (committed) | Δ Return | Δ RF | Δ WR |
| --- | --- | --- | --- | --- | --- |
| 3m | +558 % / RF 11.27 / DD 13.15 % | **+666 %** / RF **13.45** / DD 12.78 % | **+19.3 %** | +19 % | +2.3 pp |
| 6m | +666 % / RF 7.51 / DD 88.65 % | **+803 %** / RF **9.06** / DD 88.65 % | **+20.6 %** | +21 % | +2.0 pp |
| 9m | +777 % / RF 8.77 / DD 41.94 % | **+915 %** / RF **10.32** / DD 41.94 % | **+17.7 %** | +18 % | +1.3 pp |

MGC standalone PnL (9m): +585 % → **+722 %** (+23.4 %). MNQ unchanged (R28 only touched the MGC override). Mechanism: the prior 65pt MGC range-width ceiling admitted the wide-range vol-cluster sessions (CPI / rate-decision / large-physical-flow days) that produced the user-reported worst losers (2025-11-13 BUY −$526, 2025-11-18 SELL −$554). On those days the fade thesis breaks down structurally — price keeps moving in the sweep direction long after the close back into the box, hitting the 29pt SL cap. Capping the range filter at 46pt skips ~3 of those sessions per fold. The 46-48pt cluster is the cross-window Pareto-optimal bound: tighter (≤44) skips one valid winner per fold, looser (≥50) lets the bad sessions back in.

**Also in this session — Phase-2 strategy retirement decisions**:
- `globex_drift_continuation` **RETIRED 2026-06-09** (was flagged on truth-recap, then deleted from registry + disk this session): 333 trades over 9 m / total return **−71 %** / DD 76 %. Bleed across every symbol (MNQ −20 %, MES −25 %, MGC −26 %). Original Phase-2 paper edge does not survive the corrected engine + current data. Strategy code, TOML, and registry entries removed; git history is the archive.
- `nr_compression_break` (NR7) **RETIRED 2026-06-09**: 16 trades over 9 m / +5 % combined. Too sparse to call statistically. Daily-TF compression signal needs orders of magnitude more sessions to validate. Strategy code + TOML + registry entries removed; git history is the archive.

The R27 detail panel + the older R26 staleness audit remain below — preserved for context. The "R27 truth" column in the table above was added today (a fresh `--no-cache-decisions` recap of the R27 TOML) so the R28 delta is on directly-comparable ground.

---

Last refresh: 2026-06-05 **R27 `morning_range_reversion` geometry tune + stale-cache fix**. Three things happened in that session:

1. **Decision-cache staleness uncovered.** While investigating the user's "30pt MGC stop is excessive" feedback, the R26 truth-baseline metrics in this doc (PnL $+15,540, DD 23.5 %, RF 9.71) were found to be **served by the on-disk `docs/perf/_decision_cache/`** built before the most recent historical-data refresh. The data refresh added ~7 trading days of new bars; the cache key includes the CSV mtime → strictly speaking it SHOULD have invalidated, but the parquet sidecar path used by the in-process runner bypasses the CSV-mtime check in some hits. **All sweep results below are from `--no-cache-decisions` / freshly-rebuilt cache only.** The TRUE current R26 baseline on the refreshed data is materially weaker than the previously-documented number:

   | Metric | docs R26 (cached) | R26 (fresh, true) | R27 (committed) |
   | --- | --- | --- | --- |
   | Total PnL | $+15,540 | $+14,306 | **$+15,545** |
   | Return % | +777.0 | +715.3 | **+777.3** |
   | RF | 9.71 | 7.93 | **8.77** |
   | Max DD | 23.5 % | 51.2 % | **41.9 %** |
   | n trades | 175 | 182 | 182 |
   | MGC PnL | $+11,340 | $+10,467 | **$+11,706** |
   | MGC worst-trade | -$612 | -$612 | **-$592** |
   | MGC max DD | $1,166 | $1,657 | **$1,263** |
   | MGC avg R:R | 0.79 | 0.74 | **0.76** |

2. **`morning_range_reversion` Round 27 committed.** User raised the geometry concern: with MGC `sl_max_pts=30` + `tp_mult=1.8`, the cap-bound R:R is 0.45:1 and the typical-day R:R is 0.55:1 — structurally negative; the strategy depends on its 72 % WR for expectancy. R6 (sl_mult tightening to ≥1:1 geometry) and R7 (cap-only tightening) both REJECTED on truth (PnL −7 to −22 %, DD +5 to +13 pp). R10–R11 (cap + slmult + tp_mult co-tuning, then breaker_1L test) found a **clean cross-window Pareto improvement** at:
   - MGC `sl_max_pts`: **30 → 29**  (R10 sweep: best cap value on truth)
   - MGC `sl_mult`: **3.25 → 3.30**  (RF +0.35 / DD -7 pp on the +29 cap)
   - MGC `tp_mult`: **1.8 → 1.85**  (RF +0.66 / PnL +$31 / no DD penalty)
   - MGC `skip_weekdays` / `max_consecutive_losses` / `loss_streak_cooldown_sessions`: **UNCHANGED** (R10 breaker_1L test rejected as 9m-overfit; failed 6m + 3m)
   - MNQ overrides: **UNCHANGED**
3. **Portfolio worst-case-day budget impact**: MGC cap 30 → 29 cuts worst MGC stop-out from $600 → $580 / trade. Cross-strategy worst-case day drops from $1,090 → **$1,070** (now $70 over the operator's $1,000 daily MAX, was $90 over). Same operator options as before: accept the $70 breach (default — DD-likely-experienced drops too) / drop MGC to 1 contract / further cap-tighten at a known PnL cost.

### Truth-baseline 9 m ranking (post-2026-06-05 R27 commit; ALL no-cache)

The "R26 (cached)" column is what the previously-printed doc said. The "R26 (fresh)" column is what the *same* committed TOML actually produces on the refreshed data. The "R27 (committed)" column is what's deployed RIGHT NOW.

| Rank | Strategy | PnL (committed) | Return % | RF | DD % | WR | n | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | `morning_range_reversion` (**R28**) | **≈ $+18,300** | **+914.6 %** | **10.32** | **41.9 %** | **68.3 %** | 183 | 2026-06-05 PM commit; +17.7 % / +18 % RF on top of R27; MGC `max_range_width_points` 65 → 46. Pareto over R27 on every window. |
| 2 | `overnight_range` (**R24**) | $+2,665 (fresh truth) | **+133.3 %** | **6.43** | **12.9 %** | 40.4 % | 114 | 2026-06-08 commit; **MNQ skip_weekdays [0, 4] → [0, 1, 4]** — added Tuesday. MNQ leg flipped from −16.5 % loser to +47.0 % winner. +91 % return / +89 % RF / −1.7 pp DD vs R23 baseline on 9 m. Truth recap dir: `docs/perf/overnight_range_r24_truth_{3m,6m,9m}/`. |
| 3 | `vwap_zscore_reversion` MGC-only (**REVIVAL**) | $+560 (9m) | **+56.0 %** | 0.99 | 39.0 % | 40.9 % | 181 | 2026-06-08 promoted from Disabled tier after stagnant-tier audit on the corrected engine. **Cross-window improving**: 3m RF 2.45 / 6m RF 1.42 / 9m RF 0.99 — most recent regime favours it. MNQ leg dormant (-82 % drag); MGC fade signal alone is structurally positive. Truth recap dir: `docs/perf/vwap_zscore_revival_truth_{3m,6m,9m}/`. |
| 4 | `overnight_reversion` (**REVIVAL R2**) | $+1,031 (9m) | **+103.1 %** | **1.72** | 37.3 % | 32.6 % | 86 | 2026-06-09 promoted from Disabled tier → live, **long-only + skip Mon/Fri**. RF improves 9m→6m: **6m RF 3.38** / 3m RF 2.47.  MGC RF 5.59 / 5.80 on 9m/6m (workhorse).  Asymmetric edge: SHORT side lost -54 % on recent 3m so it's been disabled; up-breakouts CONTINUE on current rising-market data, down-breakouts REVERT.  Truth recap dir: `docs/perf/overnight_reversion_revival_truth_{3m,6m,9m}/`. |
| — | `body_reversion` | RETIRED | — | — | — | — | — | retired 2026-06-04 — see Disabled tier |
| — | `nr_compression_break` (NR7) | **RETIRED 2026-06-09** | — | — | — | — | 16 / 9m | Deleted from registry + disk. Too sparse to validate (currently `enabled = false`) |
| — | `globex_drift_continuation` | **RETIRED 2026-06-09** | $−$71 % | — | 76.4 % | — | 333 / 9m | Deleted from registry + disk. Bleeds capital across every symbol |

**Operator implication**: `morning_range_reversion` remains the unambiguous #1 by every metric and the R27 tune sharpens the edge further (+8.7 % PnL, −18 % DD, −24 % MGC DD vs the fresh R26 baseline). The committed config has been validated on the actual current data, not a stale cache. `overnight_range`'s row above is INTENTIONALLY left at its cached numbers because no `overnight_range` config changes were made in this session — when that strategy is next re-tuned, run `--no-cache-decisions` end-to-end and refresh both columns.

### Live deploy / multi-account orchestration

→ See [`docs/PORTFOLIO_BLUEPRINT.md`](PORTFOLIO_BLUEPRINT.md) for the dedicated deployment doc covering account allocation, the strategy-conflict matrix, per-account configuration, the portfolio daily-loss breaker, the regime publisher opt-in, the risk_sizer status, and the deploy checklist. Short version: **one strategy per prop account** because hard conflicts exist between `morning_range_reversion` ⇄ `overnight_range` and `overnight_range` ⇄ `overnight_reversion` near 09:30 ET on MNQ/MGC (REVERSION vs CONTINUATION will produce opposite signals on the same symbol in the same minute).

Last refresh: 2026-06-04 AM **SECOND CRITICAL CORRECTION — backtest engine PnL bugs fixed** (debug session `f635c2`, see `docs/CHANGELOG.md` "Fixed → Backtest engine — two PnL accounting bugs"). Two independent bugs were found and fixed in `core/backtest/engine.py`: (H-A) exit slippage was double-counted in PnL and capital; (H-B) STOP orders filled at trigger price even on gap-through bars, manufacturing phantom profit. Truth-baseline 9 m walkforwards of each then-committed production tune were re-run on the post-fix engine (recap dirs `docs/perf/{strategy}_postengine_truth/`); the resulting ranking (now superseded by the R26 ranking above) identified `morning_range_reversion` as needing a re-tune and `body_reversion` as needing retirement.

Last refresh: 2026-06-03 PM **CRITICAL CORRECTION** (contract-roll data quarantine shipped — see `docs/CHANGELOG.md` "Fixed → Backtest engine — contract-roll data quarantine"): every per-strategy 9 m PnL number on this page was re-stated against **clean** databento data. The biggest swing at that time: `body_reversion`'s headline $+9,332 collapsed to $+2,150 (−$7,182 / −77 %) because 23 of its 190 MNQ trades were fake "fills" produced by interleaved Sep/Dec contract bars on roll dates (12.1 % of trades, 89 % of PnL). The 2026-06-04 engine-fix above re-states those numbers a second time on the corrected accounting engine.

Last refresh: 2026-06-02 (Round 3 wrap-up: dormant-strategy investigation concluded — `trend_following` / `mean_reversion` / `simple_candle` all parked as **STAGNANT** after multi-window validation showed they cannot pass the DD-100 % cliff. Pivot to designing 5 net-new strategies that fill genuine arsenal gaps — see *Arsenal roadmap*.).
Last refresh: 2026-06-03 PM (R23 step-trail ship: `overnight_range` now ratchets SL to entry+1R once MFE crosses 2R, via a new `position_management.trail_steps_r` TOML knob. Backtest-only today (engine work in `core/backtest/strategy_replay.py` — multi-stage watches + one-way ratchet guard); live OCO trail path deferred. Phase-1 risk infrastructure already shipped earlier today: `core/portfolio_daily_breaker.py`, `core/risk_sizer.py`, `core/regime.py` — all unit-tested.  Phase-2 first two strategies wired: `nr_compression_break` (NR7, **R1 baseline positive on MNQ+MES+MGC**) and `globex_drift_continuation` (**R1 baseline NEGATIVE — needs research**, kept disabled).  **Bug fix (2-stage, both shipped same day)**: replay engine now flat-files on (1) calendar-date rollover AND (2) the fast-loop `_BarRow` polymorphism case — the round-14 `overnight_range` cross-session hold artefacts the user surfaced (positions persisting 7-15 days) were the joint symptom.  Tests: `tests/test_replay_force_flat_eod.py` (9 cases including fast-loop `_BarRow` regression).  **Consequence + recovery**: the previously-headlined `overnight_range` R21 numbers (RF 21.73 / +654 % / 9m) were measurement artefacts produced by partial-fill legs of cross-session positions booking as separate trades.  Round-22 re-tune on the corrected engine (4 rounds × 42 trials via `scripts/optimize_strategy.py`) found `gap_max_pct = 0.80 → 1.20` and `range_break_offset = 1.0 → 1.5` are the two committed TOML changes that recover most of the practical edge — RF 11.50 / +$2,789 / 9m on the clean baseline (vs +$1,562 / 6.77 immediately post-fix).  Per-symbol stop/TP and breaker knobs re-validated and kept unchanged.).

## Failure definition (the cliff edge)

> A walk-forward result with **max drawdown ≥ 100 %** is a blown account, not a strategy.
> Equity curves like `final 3,808.50 · return 90.42 % · DD 4,860.75 (114 % from peak)` are failures
> regardless of the headline return number. **No production deployment if any (symbol, horizon)
> exceeds DD 100 %.** No exceptions.

## Production tier — committed configs

All three strategies pass the DD-100 % cliff on every horizon and every symbol. Each has the
cross-session **live consec-loss circuit breaker** bridge wired (`STRATEGY_LIVE_BREAKER` env or
`[meta].live_breaker_enabled = true` in TOML — both default ON for these three as of 2026-06).

### 9-month money-maker ranking (post-2026-06-03 data fix — see CHANGELOG entry "Backtest engine — contract-roll data quarantine")

The CSV data quarantine drops calendar days where databento interleaved front-month and back-month contracts (every quarterly roll, ~5 corrupt days in the current 9 m window). Before the fix, **`body_reversion` MNQ's headline PnL was 89 % synthetic** — 23 / 190 MNQ trades fired across Sep 14-16 + Dec 15 + Mar 16 with one contract's entry price and the other's exit price, manufacturing $200-450 per trade in phantom take-profits. Re-running the 9 m walkforward on the clean dataset (`BACKTEST_FAST_LOOP=1`, in-process):

| Rank | Strategy | Symbol | Trades | PnL (clean) | Pre-fix PnL | Δ |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | `morning_range_reversion` | **MNQ + MGC** | 152 | **$+9,082** | $+9,082 | unchanged — almost zero exposure to roll dates |
| 2 | `body_reversion`          | **MNQ + MGC** | 350 | **$+2,150** | $+9,332 | **−$7,182** (the previous #1 collapses; 89 % of MNQ PnL was fake) |
| 3 | `overnight_range` (R23)   | **MNQ + MGC** | 118 | **$+2,409** | $+2,409 | unchanged |

**Operator implication**: deploy `morning_range_reversion` first; it's a 4× larger absolute-PnL strategy than the rest. `body_reversion` and `overnight_range` are both still net-positive and deserve their slot in the account-per-strategy partition (see the deployment blueprint below), but they're no longer in the running for "where's the alpha". The per-strategy panels below preserve the headline 9 m metrics each tier was committed at; treat them as the WR / RF profile of the strategy on the symbols/sessions it actually trades — but reach for the table above for absolute-PnL comparisons.

### 1. `body_reversion` — **RETIRED (2026-06-04)**

Hard-disabled in TOML (`meta.enabled = false`). Code, tests, breaker, and per-symbol overrides preserved on disk so a future agent can re-tune from scratch if the underlying body-percentage signal is still desired. Moved out of the production tier ranking and into the **Disabled tier** below — see the `body_reversion` entry there for the full retirement rationale and the post-engine-fix truth numbers that triggered it.

**Why retired (one-line)**: post-2026-06-04 engine-fix 9 m truth was **$−1,028 / RF −0.67 / DD 74.1 %**, both symbols negative — the entire reported alpha was a compound artefact of the H-A slip-double-count tax and the H-B stop-gap-through phantom profit channel. With both bugs fixed the strategy is structurally net-negative on the current futures dataset.
- **Live breaker**: 4 consecutive losses on a symbol → 3-day cooldown. Reads `TRADE_CLOSED` events in live, `_replay_engine.trades` in backtest. Same module as `morning_range_reversion` and `overnight_range` (`core/consec_loss_breaker.py`).
- **Symbols**: MNQ + MGC committed (MES TOML stanza dormant; thin per-tick edge).
- **Active hours**: anytime in RTH after a qualifying bar prints (effectively concentrates 9:30 – 12:00 and 13:00 – 15:30 — the high-body-bar density windows).

### 2. `overnight_range` — committed trend-day capture

**Direction: BREAKOUT-CONTINUATION** — stop-entry brackets *above* the overnight range high (LONG) and *below* the overnight range low (SHORT). Logically this is the trend-day strategy in the arsenal; the rest fade.

**2026-06-03 PM correction + Round-22 re-tune**: the previously-headlined R21 numbers (RF 21.73 / +654 % / 9m on $50k) were measurement artefacts. The replay engine's EOD force-flat was bypassed under `BACKTEST_FAST_LOOP=1` (the optimizer-default since May) because `_replay_market_flat_all_open_positions` had an `isinstance(bar, pd.Series)` early-return that silently no-op'd against the fast-loop `_BarRow` instances. The bypass let positions persist across calendar days and fragment into partial-fill "trades" worth thousands of dollars per leg. Both the fast-loop polymorphism fix AND the date-rollover guard now ship together (see CHANGELOG 2026-06-03 PM). The R22 re-tune below was run against the corrected engine via `scripts/optimize_strategy.py` → `walkforward_trade_recap_report.py --in-process` (the proper harness — never roll a subprocess loop).

**Round-22 re-tune** (4 rounds × 36 trials on corrected engine; sweep artefacts under `docs/perf/_opt_runs/overnight_range_postfix/`):
- **R1 (breaker, 8 trials)**: `2L/5d` (committed) is the RF leader (2.60) but breaker-off has +18 % more return at slightly higher DD. Kept `2L/5d`.
- **R2 (filters, 12 trials)** — the breakthrough: the committed `gap_max_pct = 0.80` was over-tuned on the buggy baseline. Loosening to 1.20 gives +80 % more return, −40 % DD, +3.3× RF. Bumping `range_break_offset` 1.0 → 1.5 adds another +19 % return / −10 % DD as an independent single-knob change.
- **R3 (combine + geometry, 12 trials)**: `gap_120 + rbo_1.5` stacked is the Pareto-best. Per-symbol stop/TP changes are all neutral-to-negative on the corrected baseline (the round-13 MNQ tight-stop / MGC loose-stop calibration still holds).
- **R4 (stack, 10 trials)**: confirmed `gap_120 + rbo_1.5` with the round-13 per-symbol settings is optimal. Tighter breaker (1L/5d) lifts WR to 39 % but cuts trade count too aggressively. Breaker-off lifts trade count to 162 but DD jumps to 23 %.

**Post-R22 walk-forward** (270d / 9 folds, MNQ+MGC, NEW committed config = 19:00→10:00 ET overnight window, 9:29 ET zone anchor, **gap_max_pct = 1.20** (was 0.80), ATR band 0.08–0.60 %, **range_break_offset = 1.5** (was 1.0), **breaker 2-loss / 5-day cooldown** (unchanged), recap dir `docs/perf/overnight_range_round22_postfix/`):

|     | Return       | DD ($)   | RF (PnL/worst-fold-DD) | WR     | n   | avg bars held |
| --- | ------------ | -------- | ---------------------- | ------ | --- | ------------- |
| 3m  | **+$1,089.60** (sweep) | $235 | **3.52**            | **32.5 %** | **40**  | — |
| 6m  | **+$1,601.60** (sweep) | $294 | **4.89**            | **31.3 %** | **80**  | — |
| 9m  | **+$2,789.10** | **$242.55** | **11.50**          | **34.7 %** | **121** | **4.7** |

Per-symbol on 9m: MNQ +$604.60 / 62 trades / WR 24.2 % / 7.0 bars (was $191.70 / WR 20.4 % on R21 post-fix); MGC +$2,184.50 / 59 trades / WR 45.8 % / 2.2 bars (was $1,370.00 / WR 42.0 %). Both symbols pulled +50 to +200 % vs the R21 post-fix baseline. Per-fold breakdown: 4/9 losing folds on MNQ (still rough), 3/9 losing folds on MGC, worst single-fold DD $242.55.

**Round-23 step-trail addition** (2026-06-03 PM, follow-on to R22; user-requested "hone into a continuation strat — trailing stops / partial profit"):
- **R22 trade-distribution diagnostic** showed exactly the continuation profile expected — **MNQ winners held median 10 bars / p75 37 bars / max 67 bars; losses median 1 bar / p75 3 bars** — so a trail that activates *after* the initial high-loss cluster is the natural mechanism. MGC winners by contrast hit TP=2R fast (median 1 bar), so the trail rarely fires there.
- **Partial-TP was a dead end** (R23a-R23f, 6 trials): the existing partial-TP pathway auto-arms a stage-2 break-even stop on the runner, which knocks out MNQ's true continuation runners before they reach the full 4R TP. RF dropped to 1.23-4.79 vs 8.52 baseline across all partial variants. **Won't help this strategy** until the runner can be configured with a non-BE stage-2 stop.
- **Step-trail is the right tool**. New `position_management.trail_steps_r` TOML knob takes a list of (trigger_R, lock_offset_R) stages in R-multiples of |entry − stop|; each stage registers a separate watch (via the engine's existing breakeven-watch infrastructure, now extended with multi-watch SL-linking + one-way ratchet — see CHANGELOG). R-multiples (not raw points) so the same row works across MNQ (R ≈ 25pt) and MGC (R ≈ 7pt).
- **R24 sweep** (14 trials, 270d / 9 folds): **`[[2.0, 1.0]]` (lock 1R at 2R MFE) wins** at ret +142.62% / dd 9.66% / RF 8.71 / wr 39.7% / n=126 vs R22 baseline +139.46% / dd 10.62% / RF 8.52 / wr 34.7% / n=121. Pure BE @ 1R or 2R **hurts** (ret +96-103%, rf 3-3.3, wr 28-30%) — early BE knocks out runners. 3-stage classic (1R / 2R / 3R triggers) hurts MNQ the same way. R25 fine-tune (14 trials) confirmed `[[2.0, 1.0]]` is the local optimum.
- **R26 cross-window check** (3m / 6m / 9m, 9 trials each): pairing the trail with looser MNQ TPs (5R / 6R / 8R / 10R) HURT MNQ on every horizon — the existing 4R MNQ TP already catches most winners; a wider target trades certain 4R wins for smaller-MFE-eventually-stopped outcomes. **Commit kept the R22 4R MNQ TP.**
- **Cross-window net of `[[2.0, 1.0]]`** vs R22 baseline: 3m ret +63 vs +54 (+16%, RF +16%), 6m flat (-0.2% ret, RF flat), 9m +2.3% / RF +2.2% / WR +5pp. WR consistently **+5–10pp** across all windows. Per-symbol: MNQ ret +30.23 → +32.12 (+6.3%, winners protected); MGC +109.22 → +110.50 (+1.2%, mostly unchanged).
- **Live deployment caveat**: `register_generic_breakeven_watch` is currently a no-op for OCO brackets in the live bot (live BE monitor uses the legacy `breakeven_monitoring` dict path). The step-trail is therefore **backtest-only** until the live OCO watch path is wired — acceptable because production already flattens at EOD which caps the downside. Filed as a deferred follow-up.

**Post-R23 walk-forward** (270d / 9 folds, MNQ+MGC, NEW config adds `trail_steps_r = [[2.0, 1.0]]` to R22 base, recap dir `docs/perf/overnight_range_round23_postfix/`) — **pre-2026-06-04 engine-fix numbers**:

|     | Return       | DD ($)   | RF (PnL/worst-fold-DD) | WR     | n   | avg bars held |
| --- | ------------ | -------- | ---------------------- | ------ | --- | ------------- |
| 3m  | **+$1,266.20** (sweep) | $235 | **4.09**            | **42.9 %** | **42**  | — |
| 6m  | **+$1,598.20** (sweep) | $307 | **4.88**            | **36.6 %** | **82**  | — |
| 9m  | **+$2,852.40** | **$193** | **14.48**          | **39.7 %** | **126** | **4.7** |

Per-symbol on 9m (pre-engine-fix): MNQ +$642.40 / 66 trades / WR 31.8 % (was $604.60 / 62 / 24.2 % on R22 — winners now protected by the 1R lock); MGC +$2,210.00 / 60 trades / WR 48.3 % (was $2,184.50 / 59 / 45.8 %). The DD improvement is the clearest signal — $242.55 → $193 (−20 %) on 9m as the trail caps giveback on running winners.

**Post-2026-06-04-engine-fix truth** (same committed config, recap `docs/perf/overnight_range_postengine_truth/`):

|     | Trades | W / L | Return | DD ($) | DD % | RF | WR |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 9m  | **126 (unchanged)** | **50 / 76 (unchanged)** | +125.19 % | $356.10 | 15.64 % | **7.03** | 39.68 % |

Per-symbol on 9m post-fix: MNQ $+386.70 / 66 / WR 31.8 % (PnL −$255.70 vs pre-fix, same trade count); MGC $+2,117.00 / 60 / WR 48.3 % (PnL −$93 vs pre-fix). **The tune is robust** — identical trade selection (entries at fixed levels, unchanged), identical W/L composition, only effect is each loser costs ~$2–4 more on the corrected engine (gap-through clamp + slip de-double-count on the SL leg; winners are LIMIT take-profits → completely immune). Avg-R-losers worsened from −1.17 → −1.33 (the expected gap-through fingerprint). **No re-tune needed. Strategy stays production-tier on the corrected engine.**

- **Status**: STAYS PRODUCTION. The R23 step-trail addition is a Pareto-better commit on every horizon (3m / 9m strictly better; 6m essentially flat with same RF). +$60/month on a 2-symbol pair vs R22, but the real win is **better risk-adjusted returns** (RF 14.48 vs 11.50) and **higher WR** (+5pp) — the continuation thesis paid off. Strategy clears the production tier comfortably.
- **What it trades**: tracks 19:00 ET → 10:00 ET range. After 10:00 ET, places stop-entry brackets at range high + **1.5pt** (LONG) and range low − **1.5pt** (SHORT). Live monitor continuously stages the closer-side bracket as price approaches. Gap filter screens out > **1.20 %** open-gap sessions.
- **Continuation behaviour (R23)**: once a position's MFE crosses 2R, SL ratchets to entry + 1R (LONG) or entry − 1R (SHORT). One-way ratchet guard in the engine prevents accidental regression on misconfigured multi-stage entries.
- **Active hours**: 10:00 ET (entry window opens) until 16:00 ET (force-flat — now correctly fires under both slow and fast replay loops). Avg hold 4.7 bars (~24 min).
- **Per-symbol weekday skips**: MNQ skips Mon + Fri (R13, re-validated R22-R23), MGC skips Wed (R13, re-validated R22-R23).
- **Live breaker**: 2 consecutive losses on a symbol → 5-day cooldown (R21, re-validated R22-R23 — still the RF leader among breaker variants).
- **Next research**: (1) wire the step-trail into the **live** OCO order path (today it's backtest-only — see "Live deployment caveat" above); (2) per-fold MNQ analysis — losing folds suggest there's a regime that still hurts; (3) `trail_steps_r` per-symbol override (today it's global-only — MGC may benefit from a different trigger now that we have the infrastructure); (4) re-introduce a regime gate (Phase-1 `core/regime.py`) and gate the breakout to trend regimes only.

### 3. `morning_range_reversion` — **R28 MATERIAL PNL LIFT COMMITTED (2026-06-05 PM)**

R28 single TOML change: `[symbols.MGC.signal].max_range_width_points` **65 → 46**.

R28 truth recap (`--no-cache-decisions`, fresh-cache; recap dirs `docs/perf/morning_range_r28_truth_{3m,6m,9m}/`):
- **9 m**: ret **+914.6 %** / RF **10.32** / DD **41.94 %** / WR **68.3 %** / n=183 (vs R27 +777 / 8.77 / 41.94 / 67.0 / 182).
- **6 m**: ret **+803.2 %** / RF **9.06** / DD 88.65 % / WR 68.9 % / n=119 (vs R27 +666 / 7.51 / 88.65 / 67.0 / 118).
- **3 m**: ret **+665.7 %** / RF **13.45** / DD 12.78 % / WR 76.9 % / n=65 (vs R27 +558 / 11.27 / 13.15 / 74.6 / 63).
- Per-symbol MGC PnL (9 m): +585 % → **+722 %** (+23.4 %). MNQ unchanged at +192 % / 73 trades.

R28 mechanism: MGC's prior 65pt morning range-width ceiling admitted the wide-range vol-cluster sessions (CPI / rate-decision / large-physical-flow days) that consistently produced the user-reported worst losers. Capping the range filter at 46pt skips ~3 of those sessions per fold without affecting normal-volatility days. The 46-48pt cluster is the empirical cross-window Pareto-optimal bound — tighter (≤44) skips one valid winner per fold, looser (≥50) lets the bad sessions back in.

R28 rejected alternatives (all sweep evidence under `docs/perf/_opt_runs/morning_range_reversion/r28_*/`):
- `sl_from_tp_ratio` (NEW lever; user's literal "dynamic SL keyed to TP" ask) — 6 ratio values × tp_mult combinations; every variant hurt because the strategy's edge IS the wide-stop / high-WR structure. Knob retained in code (default 0 = no-op) for future regime-aware work.
- partial-TP (BONGO §1A) — catastrophic across the board (MNQ −64 to −124 % vs +192 baseline). Two-stage OCO interacts poorly with the 5m fade tempo.
- breakeven_enabled with any trigger_R — −3 to −17 % return; breakeven defeats the fade thesis.
- `max_sweep_distance_widths ≤ 1.75` (vs 2.0) — no-op; cap rarely binds.
- `require_reentry_close=true` (legacy candle-close mode) — kills MGC to 2 trades.
- `mgc_skip_thu_fri` / `mgc_skip_mon_fri` — −34 to −37 % ret; MGC alpha is not weekday-selective.

### 3. `morning_range_reversion` — historical R27 panel (kept for context)

**Direction: FADE** (counter-trend). Opposite side of `overnight_range`'s breakout-continuation,
which is the deliberate diversification: when overnight_range goes LONG above the overnight high,
morning_range_reversion can fade a separate 07:00 – 08:00 morning range and go SHORT on the same
event. Both are sized to take the heat; the breakers limit the bleed.

**R27 trigger**: user feedback on two specific MGC stop-out trades (Nov-13 / Nov-18, $526 + $554 losses) — "30pt MGC stop is excessive when average winners are typically <30pt; please test dynamic stops keyed to TP targets so we maintain positive risk geometry."

**R27 investigation findings**:
1. The per-trade reward:risk geometry IS structurally negative: typical-day SL ≈ 24pt vs TP ≈ 13.5pt → 0.55:1; cap-bound SL = 30pt vs TP = 13.5pt → 0.45:1. The user is correct that the geometry is below 1:1.
2. BUT sweeps that tighten `sl_mult` toward ≥1:1 geometry (R6, 10 trials covering sl_mult 0.75 – 2.75) **crash the strategy** — WR drops 67 % → 48–62 %, RF drops 50–87 %, total PnL drops 16–85 %. The strategy's edge IS the 72 % MGC WR + 60 % MNQ WR, and that WR depends on giving trades enough room to ride out the post-fade noise.
3. Cap-only tightening (R7, cap 20 – 30) is a softer lever but still ultimately net-negative on truth: every step from 30 → 20 trades dollars-of-PnL for marginal worst-case improvement (cap=24 cost $3,093 of PnL and added $251 of DD for $120 of worst-case savings).
4. **The clean Pareto improvement is at cap=29 + slmult=3.30 + tp=1.85** — improves PnL, RF, DD, WR, R:R, AND modestly reduces worst-case ALL at once. Cross-window validated.
5. **Decision-cache staleness**: the R26 baseline metrics in the prior version of this doc ($+15,540 / RF 9.71 / DD 23.5 %) were served by stale `docs/perf/_decision_cache/` entries from before the most recent historical-data refresh. The TRUE R26 baseline on current data is $+14,306 / RF 7.93 / DD 51.2 %. All R27 sweep numbers below are from `--no-cache-decisions` runs only.

**R27 committed truth** (270d / 9 folds, MNQ+MGC, `--no-cache-decisions`, recap dir `docs/perf/morning_range_round27_committed/`):

|     | Return       | DD ($)   | DD % | RF   | WR     | n   |
| --- | ------------ | -------- | ---- | ---- | ------ | --- |
| 9m  | **+777.25 %** | **$1,263** | **41.94 %** | **8.77** | **67.03 %** | **182 (122W / 60L)** |

Per-symbol on 9m: MGC $+11,706 / 109 trades / WR 72.5 % (avg winner $296 / avg loser -$390 / worst -$592 / R:R 0.76:1); MNQ $+3,839 / 73 trades / WR 58.9 % (avg winner $294 / avg loser -$294 / worst -$988 / R:R 1.00:1 — MNQ already has positive per-trade geometry).

**Cross-window validation** (fresh-cache truth: R26 committed → R27 committed; sweep dirs under `docs/perf/_opt_runs/morning_range_reversion/`):

|     | R26 committed (fresh) | R27 committed | Δ ret | Δ RF | Δ DD pp |
| --- | --- | --- | --- | --- | --- |
| 3m  | ret +542 % / RF 10.74 / DD 13.78 % | ret +558 % / RF **11.27** / DD **13.15 %** | **+16** | **+5 %** | **-0.6** |
| 6m  | ret +639 % / RF 7.09 / DD 90.15 % | ret +666 % / RF **7.51** / DD **88.65 %** | **+27** | **+6 %** | **-1.5** |
| 9m  | ret +715 % / RF 7.93 / DD 51.15 % | ret +777 % / RF **8.77** / DD **41.94 %** | **+62** | **+11 %** | **-9.2** |

**R27 is a clean Pareto improvement on every window** — better PnL, better RF, lower DD on 3m / 6m / 9m simultaneously. No regressions.

**R27 sweep evidence** (11 trials × 3 windows via `scripts/optimize_strategy.py` → `walkforward_trade_recap_report.py --in-process`, sweep dirs under `docs/perf/_opt_runs/morning_range_reversion/`):
- **R6 (20 trials, sl_mult tightening to enforce positive geometry)**: ALL variations rejected. sl_mult 0.75 dropped WR to 48 % / PnL to +$124. sl_mult 2.25 was the best of the bunch but still RF 6.30 vs baseline 7.93. The strategy's high WR is incompatible with ≥1:1 per-trade geometry on this fade signal.
- **R7 (16 trials, cap-only tightening with slmult unchanged)**: cap=29 = marginal lift (RF 8.10 vs 7.93). cap≤27 net-negative (PnL down, DD up). cap=24 cost $3,093 of PnL for $120 of worst-case savings — empirically bad trade.
- **R8 + R9 (28 trials, cap + tp_mult combos)**: misled by stale-cache baseline; results not used for committed config.
- **R10 (12 trials, FRESH CACHE)**: re-baselined truth. cap=29 + slmult=3.30 ranked top by ret×RF (RF 8.28 / DD 44 %); cap=29 + tp=1.85 second (RF 8.59); tp + slmult stacked = best (RF 8.77).
- **R11 (10 trials, cross-window stacking + breaker_1L test)**: confirmed `cap=29 + slmult=3.30 + tp=1.85` is the cross-window champion. `max_consecutive_losses=1` looked stellar on 9m (RF 9.57, DD 22 %) but REGRESSED hard on 6m (RF 7.09 → 6.53) and 3m (RF 10.74 → 6.51) — rejected as 9m-overfit.

**Status**: PRODUCTION TIER, #1 by every metric. R27 makes the per-trade geometry slightly less negative (0.45:1 → 0.48:1 on cap-bound days; 0.55:1 → 0.56:1 on typical days), shaves $20 off worst-case, AND lifts the whole performance envelope. The user's underlying concern (excessive single-trade losses) is addressed about as far as the data supports — going further on the cap costs more PnL than it saves.
- **What it trades**: 07:00 – 08:00 ET premarket range. Fades re-entries into the range with a midpoint-anchored target and a per-symbol stop geometry.
- **Active hours**: 08:00 ET (range built) until 16:00 ET (force-flat). Average hold ≈ 1.5 hours.
- **Per-symbol weekday skips (R27, unchanged from R26)**: MNQ skips Mon + Thu + Fri (alpha concentrates Tue + Wed). **MGC skips Fri only**.
- **Live breaker (unchanged)**: 2 consecutive losses on a symbol → 10-day cooldown. R27 confirmed 1L-tighter is overfit; 2L stays.
- **Sizing**: MNQ 4 contracts (slfix=50pt → 30pt cap → $240 worst-case), MGC 2 contracts (sl_mult=3.30 against half-range → **29pt cap as of 2026-06-05 R27** → $580 worst-case). Portfolio worst-case-day budget = $1,070 ($70 over the operator's $1,000 MAX, was $90 over at R26 — see TOML "PORTFOLIO" note for opt-in to 1ct MGC).
- **Per-symbol breakeven offset**: 8pt MNQ / 0.7pt MGC.
- **Next research**: (1) MNQ slfix=50 + cap=30 hasn't been touched in years — same fresh-cache sweep treatment may unlock more; (2) the `decision_cache.py` parquet-sidecar invalidation gap should be investigated (cache should auto-bust on parquet refresh, currently doesn't); (3) wire `core/risk_sizer.py` for true fixed-dollar-risk per-trade — currently MGC's 2-contract sizing means a cap-bound day costs $580 even if account is at peak; risk-sizer would scale contracts dynamically.

---

### Historical record (pre-engine-fix / pre-R26, kept for context)

Pre-2026-06-04 numbers below — engine-artefact-inflated by both H-A (slip double-count tax in PnL accounting) and H-B (stop-gap-through phantom profit), and based on the pre-R26 MGC tune that's now superseded. **Do not trust the RF / DD figures below for production sizing decisions** — use the R26 truth panel above.

|     | Return    | DD      | RF   | WR     | n   |
| --- | --------- | ------- | ---- | ------ | --- |
| 3m  | +262 %    | ~16 %   | ~4.9 | ~70 %  |  ~80 |
| 6m  | +488 %    | ~85 %   | ~3.1 | ~67 %  | ~160 |
| 9m  | **+519.7 %** | **54.8 %** | ~9.5 | ~67 %  | ~190 |

- **What it trades**: 07:00 – 08:00 ET premarket range. Fades re-entries into the range with a midpoint-anchored target and a per-symbol stop geometry.
- **Active hours**: 08:00 ET (range built) until 16:00 ET (force-flat). Average hold ≈ 1.5 hours.
- **Per-symbol weekday skips**: MNQ skips Mon + Thu + Fri (alpha concentrates Tue + Wed). MGC skips Wed + Fri.
- **Live breaker**: 2 consecutive losses on a symbol → 10-day cooldown. Tighter than the other two because MGC's tail-loss days are wider.
- **Sizing**: MNQ 4 contracts (slfix=50pt → 30pt cap), MGC 2 contracts (sl_mult=3.0 against half-range → **22pt cap as of 2026-06** for the portfolio worst-case-day budget — see below).
- **Per-symbol breakeven offset**: 8pt MNQ / 0.7pt MGC.

## Stagnant tier — CONFIRMED DEAD on the corrected engine (2026-06-08)

All three strategies below were re-audited on the corrected engine after the
2026-06-03 H-A (slip double-count) + H-B (STOP gap-through) fixes landed.
**Verdict: all three remain dead. The engine bugs were not what was holding
them back — they're structurally negative on the current futures dataset.**

| Strategy           | 9m ret (corrected engine) | 9m DD | 9m RF | Note |
| ------------------ | ------------------------- | ----- | ----- | ---- |
| `mean_reversion`   | **−189 %** | 284 % | −0.45 | Both MNQ and MGC negative.  Catastrophically worse on the corrected engine vs the 142 % DD originally reported. |
| `trend_following`  | **−21 %**  | 114 % | −0.10 | MGC drags (-24 %); MNQ flat (+3 %).  5m MA-crossover signal is structurally broken on current data. |
| `simple_candle`    | **−1,645 %** | 1,620 % | −0.99 | Catastrophic.  Engine bugs were MASKING the failure — corrected DD is **7.6× worse** than the originally-reported 213 %.  The "let it ride" logic compounds losers ruinously. |

Audit recap dirs: `docs/perf/_audit_stagnant/{mean_reversion,trend_following,simple_candle}_9m/`,
fresh-cache `--no-cache-decisions` end-to-end.

Each TOML retains its 2026-06-02 sweep-validated config + the 2026-06-08
post-engine-fix audit numbers in its header.  They are NOT in any
deployment phase, NOT on the active research list. **Net conclusion: dead.
The engine bugs hid HOW dead.  Move on.**

### Stagnant-tier audit also surfaced two revival winners (see Production tier rows #3 + Watch above)

The 2026-06-08 audit ran every dismissed-on-buggy-engine strategy through the
corrected pipeline.  Two flipped positive:

- **`vwap_zscore_reversion` MGC-only** — promoted to Production tier (row #3).
  RF improves 3m (2.45) > 6m (1.42) > 9m (0.99); alpha is current, not decaying.
- **`overnight_reversion`** — revival candidate (Watch row).  Strong 9m (+150 %)
  and 6m (+59 %) but weak 3m (+7.7 % / RF 0.11) — needs a tune-up before deploy.

Also re-audited as dead (already in Disabled tier, audit just confirms):
`ema_stack_trend_15m` (-278 % / DD 333 %), `rsi_switch_15m` (-34 % / DD 49 %, n=53),
`hourly_anchor_retrace` (+21 % / DD 116 % — fails cliff), `simple_rth` / `simple_momentum`
/ `trend_scalping` (all 0 trades — logic bugs / over-restrictive filters as documented).

## Disabled tier — known-broken or empirically negative

- **`body_reversion` — RETIRED 2026-06-04 after the backtest-engine audit (debug session `f635c2`).** Was production-tier with a headline of $+9,332 / RF 24.70 / 9 m before the 2026-06-03 contract-roll data quarantine corrected that to $+2,150, and then the 2026-06-03 evening engine-fix corrected it a second time to **$−1,028 / RF −0.67 / DD 74.1 % / WR 33.8 %** (recap `docs/perf/body_reversion_postengine_truth/`). Both symbols flipped negative on the corrected engine: MNQ `n=177 W/L=59/118 WR=33.3 % PnL=$−668.80`; MGC `n=187 W/L=64/123 WR=34.2 % PnL=$−359.28`. The strategy's geometry — fade single high-body-percentage bars with a tight `0.35 × ATR` stop and 3R TP — maximally exposed both bugs: every stop trigger gap-through (H-B) booked phantom profit, and the per-trade slip double-count (H-A) ate ~$0.50/trade on MGC × 187 trades = $94 of phantom cost. With both fixed, the alpha vanishes entirely. **Status**: hard-disabled in `config/strategies/body_reversion.toml` via `meta.enabled = false`; full retirement rationale in the TOML header. **Future work** (deferred, not on active research list): the underlying body-percentage signal (effect-size deep-scan in `docs/alpha/deep_scan_INDEX.md`) was statistically real on raw 5m forward returns and may still support a strategy with **wider stops, fewer trades, and exits that don't depend on STOP fills** — but that's a full re-design, not a re-tune. Pre-engine-fix R5/R6 historical numbers (retained for archaeology only, **do not trust**): 3m +280 % RF 10.05 / 6m +520 % RF 18.66 / 9m +735 % RF 24.70. All bugs.
- ~~`overnight_reversion` — 9 months of negative expectancy across every parameter combo. Breakouts continue more often than they revert on the current futures dataset. Disabled in TOML with the full evidence trail.~~ **REVIVED + PROMOTED LIVE 2026-06-09** (rationale was a buggy-engine artefact).  Tuned via `allow_short = false` + `skip_weekdays = [0, 4]` — 6m / 3m RF jumped to 3.38 / 2.47 (from 0.85 / 0.11).  See Production tier row #4 for the full panel.
- `simple_momentum` — logic bug in `analyze()` (recent_high includes current bar, makes `current_price > recent_high` impossible).
- `trend_scalping` — three-condition signal logic is too restrictive; cadence is effectively zero on the dataset.
- `rsi_switch_15m`, `ema_stack_trend_15m`, `hourly_anchor_retrace`, `vwap_zscore_reversion`, `simple_rth` — TOML-disabled, no walk-forward evidence either way.

## Multi-strategy deployment blueprint

### The single-account long/short constraint

In a TopStepX account, a single instrument can only carry a **net position** at any time —
attempting LONG MNQ while already SHORT MNQ flattens the SHORT (or rejects the order,
depending on bracket state). The three production strategies cross-fire on overlapping
windows with opposite directional bias:

| Strategy                    | Direction          | Window (ET)            | Symbols |
| --------------------------- | ------------------ | ---------------------- | ------- |
| `overnight_range`           | BREAKOUT-CONTINUE  | 10:00 – 16:00          | MNQ + MGC |
| `morning_range_reversion`   | FADE               | 08:00 – 16:00          | MNQ + MGC |
| `body_reversion`            | FADE (single bar)  | 09:30 – 15:30          | MNQ + MGC + MES |

A trend-up morning would trigger `overnight_range` LONG at the OR-high break AND
`morning_range_reversion` SHORT into the resistance fade, on the *same symbol* in the *same
account*. Without deconfliction, the two orders fight each other and the realised P&L is
neither strategy's modelled edge.

### Recommended deployment: **account-per-strategy partition** (zero code change)

**Run each strategy on its own TopStepX account.** This is the simplest, most-tested, and
most-debuggable option. The broker enforces the single-direction constraint per account; the
strategies operate independently with no shared state.

```
Account 1: overnight_range            (MNQ + MGC, breakout-continue, 10:00-16:00)
Account 2: morning_range_reversion    (MNQ + MGC, fade, 08:00-16:00)
Account 3: body_reversion             (MNQ + MGC + MES, fade single-bar, 09:30-15:30)
```

Run with `scripts/start_multi_account.sh` which already supports multi-account orchestration.

- **Capital fragmentation** is the cost: $50k evaluation per account → $150k tied up. For
  TopStepX prop accounts this is acceptable because each account is a separate $/month fee,
  not capital you put up.
- **Worst-case-day budget arithmetic still applies** (see below), but the loss is bounded
  per-account: account 2 hitting its daily loss limit doesn't flat account 1 or 3.
- **Live breakers work natively** — each strategy's `_live_trade_history` only sees its own
  account's fills, exactly as designed.

### Alternative A: symbol partition (single account, lower diversification)

Split symbols so no two strategies share an instrument:

```
Single account: overnight_range MNQ, morning_range_reversion MGC, body_reversion MES
```

- ✅ Single account, no broker conflict.
- ❌ Forfeits per-strategy diversification across symbols (the whole point of MNQ + MGC on
  the same strategy is the de-correlated equity curve from MGC's metals beta vs MNQ's tech-
  index beta). Walk-forward metrics above will NOT translate — each strategy was tuned on its
  full symbol set.
- ❌ Wastes the alpha — `morning_range_reversion`'s MNQ leg is its highest-RF symbol and
  giving it away to a single-symbol pin loses the bulk of that strategy's return.

### Alternative B: signal-precedence netting (single account, requires code work)

Wire a portfolio-level coordinator into `strategies/strategy_manager.py` so a single
instrument can only be held by one strategy at a time. Priority order based on backtest RF:

```
body_reversion (RF 24.7 9m) > overnight_range (RF 21.7 9m) > morning_range_reversion (RF ~9 9m)
```

When `morning_range_reversion` produces a SHORT MNQ signal but `body_reversion` is already
LONG MNQ in the same account, `morning_range_reversion`'s order is rejected (logged, not
placed). Reverse case: `body_reversion`'s signal preempts a stale `overnight_range` position
by flattening it first.

- ✅ Single account, full diversification preserved per strategy.
- ❌ Substantial code work in `strategy_manager.py` + a new portfolio-coordinator event bus.
- ❌ Changes the strategies' empirical P&L — a `morning_range_reversion` SHORT that would
  have been profitable now sits in the sidelines because `body_reversion` is in a LONG that
  ultimately loses. The walk-forward metrics above were generated with each strategy isolated;
  netting changes them in ways that need their own walk-forward.
- Recommended only AFTER account-partition deployment has run successfully for a quarter and
  the operator wants to consolidate to fewer accounts.

### Alternative C: directional-same-side only (single account, simplest code change)

A strategy can enter only if its signal direction matches any existing position on the symbol
(or there's no position). Different strategies can stack same-direction positions but cannot
fight each other.

- ✅ Single account; smallest code change of the netting options.
- ❌ Loses the contra-directional alpha entirely — `morning_range_reversion`'s edge is FADING
  the move that `overnight_range` is CONTINUING. Forcing both to the same side defeats the
  diversification thesis.
- Not recommended.

### Worst-case-day budget (applies to ALL deployment options above)

Operator hard limit: **single-day stop-out across all three strategies must stay under $1000.**

### Worst-case-day arithmetic (after 2026-06 MGC SL cap)

| Strategy / leg               | Per-trade $ risk      | Notes |
| ---------------------------- | --------------------- | ----- |
| morning_range MNQ            | 30pt × $2 × 4ct = $240 | global sl_max_pts cap |
| morning_range MGC            | 22pt × $10 × 2ct = $440 | per-symbol sl_max_pts cap (NEW 2026-06) |
| overnight_range MNQ          | ~50pt × $2 × 1ct = $100 | ATR-based, typical |
| overnight_range MGC          | ~15pt × $10 × 1ct = $150 | ATR-based, typical |
| body_reversion MNQ           | ~5pt × $2 × 1ct = $10  | 0.35×ATR |
| body_reversion MGC           | ~3pt × $10 × 1ct = $30 | 0.35×ATR |
| body_reversion MES           | ~3pt × $5 × 1ct = $15  | 0.35×ATR |
| **TOTAL**                    | **$985**              | ~99 % of $1000 budget |

The MGC `sl_max_pts = 22` per-symbol override (`config/strategies/morning_range_reversion.toml`,
`[symbols.MGC.signal]`) is what makes this fit. Without it, MGC's range-anchored stop on a
30-point morning range produces a 45pt SL → $900 risk on its own and the day's worst case
breaches $1100.

### What the breakers do for you

Once a strategy hits its consec-loss threshold on a symbol, that symbol is removed from the
day's risk calculation until the cooldown elapses. So in practice the worst-case-day arithmetic
is conservative — the breakers will have stopped at least one of the three strategies before all
three brackets fill at maximum risk on the same losing day.

### Deployment phasing

1. **Provision three TopStepX accounts** (one per production strategy — see the partition
   blueprint above). `scripts/start_multi_account.sh` orchestrates them in parallel.
2. **Start with all three production strategies on MNQ + MGC**, `live_breaker_enabled = true`
   (now the default in each TOML). MES dormant in morning_range_reversion, body_reversion-only
   on MES.
3. **Validate one week of paper-trading** with the live breaker bridge active. Confirm
   `TRADE_CLOSED` events fire from the user hub and the strategy's `_live_trade_history`
   populates. (Tests in `tests/test_live_trade_breaker_bridge.py` already pin this in CI.)
4. **Move to TopStepX evaluation accounts** once paper looks consistent with the walk-forward.
5. **Only then** consider re-enabling MES on morning_range_reversion or adding the experimental
   tier. `mean_reversion` is the most promising experimental — if 9m DD can be brought below
   100 % with a per-day loss cap, it becomes a 4th production strategy.

## Arsenal roadmap — net-new strategies + shared infrastructure

The dormant trio (`trend_following` / `mean_reversion` / `simple_candle`) plateaued at "DD too
high to deploy" after three rounds of tuning. Continuing to grind on them is low-leverage —
the same engineering hours buy more arsenal coverage by **designing new strategies that target
genuine gaps** in the current portfolio. This roadmap is ordered by impact-per-effort with
explicit alpha thesis, time/direction coverage rationale, and acceptance criteria for each.

### Current arsenal coverage gaps

| Dimension          | Production tier coverage                           | Gap |
| ------------------ | --------------------------------------------------- | --- |
| **Time-of-day**    | 08:00 – 16:00 ET (all three production)             | Pre-market (04:00 – 08:00 ET), Globex (18:00 – 04:00 ET), Power Hour (15:00 – 16:00 ET) is covered only as a fade target by `morning_range_reversion`, not directly |
| **Direction bias** | 2 fade + 1 breakout-continue                        | Trend-continuation strategies are 1/3 of the portfolio; in trend regimes the portfolio is ~70 % fighting the move |
| **Timeframe**      | 5m exclusively                                      | Coarser (15m / 30m / daily) signals add regime diversification — different alpha source, less correlated equity curve |
| **Alpha mechanic** | Range fade × 2 + range breakout × 1                 | No VWAP-anchor strategy. No volatility-compression / NR-bar strategy. No close-driven mean-reversion. |

### Phase 1 — Risk infrastructure (do this FIRST; unlocks every new strategy)

Three pieces of shared code. Each is independently useful but the new-strategy designs below
all assume at least one of them is in place.

#### 1.1 — Portfolio-level daily circuit breaker

**What**: a process-level kill switch that, when *total realised PnL across every account /
strategy / symbol* on a single calendar-ET day crosses the operator's `-$1000` cap, flat-files
every open position and disables all strategies for the remainder of the session.

**Why now**: today every strategy has its own consec-loss breaker, but they're symbol-local
and don't see each other. The worst-case-day arithmetic in this doc shows we're at 99 % of
the $1000 budget on a triple-stop day — the breaker is the kill switch that converts that 99 %
into an actual hard ceiling.

**How**: `core/portfolio_daily_breaker.py` (new module) subscribes to `TRADE_CLOSED` events
(already published by `core/user_hub_handlers.py` for the live consec-loss breaker bridge);
aggregates realised PnL per ET day; on threshold breach publishes a new `PORTFOLIO_KILL`
event that `StrategyManager` listens for and that triggers `flatten_all()` + sets
`strategy.is_enabled = false` for the rest of the day. Resets at 18:00 ET (futures session
rollover).

**Acceptance**: unit test that walks 6 TRADE_CLOSED events summing to -$1100 and asserts the
flatten-all path fires exactly once + every strategy reports `is_enabled = false`.

#### 1.2 — Fixed-dollar-risk position sizer

**What**: shared `core/risk_sizer.py` that turns "I want $X risk on this trade with a Y-point
stop" into a contract count, capped by per-symbol `max_contracts` and per-strategy
`max_dollar_risk_per_trade`. Replaces the per-strategy "1 / 2 / 4 contract" hard-coded sizing
that's currently scattered across TOMLs.

**Why now**: every new strategy in Phase 2 needs this. It's also what `simple_candle` would
have needed to graduate (the let-it-ride alpha there compounded losers because position
size grew with equity — a fixed-dollar sizer breaks that compounding).

**How**: `sizer.size_for(symbol, dollar_risk, stop_points)` returns `int(min(max_contracts,
floor(dollar_risk / (stop_points × tick_value × contract_multiplier))))`. Strategies replace
their hard-coded `position_size = N` with `sizer.size_for(...)` calls.

**Acceptance**: 50-row property-based test (`hypothesis`) asserting the dollar exposure is
within ±$5 of the requested risk across all combinations of (symbol, stop_points,
dollar_risk).

#### 1.3 — Regime classifier as a shared service

**What**: `core/regime.py` — a single regime classifier (KER over 50 5m bars + ADX(14) +
realised-vol percentile) published once per bar to a `RegimeUpdate` event on the bus. New
strategies subscribe; existing strategies CAN consult it but don't have to (no production
breakage).

**Why now**: every new strategy in Phase 2 is conditional on regime — fading in trend regimes
loses, breaking out in chop regimes loses. Centralising the classifier means one source of
truth (and one knob surface to tune) rather than each strategy implementing its own.

**How**: simple polled job inside `StrategyManager` that emits `RegimeUpdate(regime: 'trend'
| 'chop' | 'mixed', ker: float, adx: float, vol_pct: float)` per bar. No new dependencies.

**Acceptance**: tests over the 9m MNQ walk-forward that aggregate regime distribution + show
`fold_x.body_reversion.WR > 0.40` only in `chop|mixed` regimes (sanity check that the
classifier maps to existing-strategy edge).

### Phase 2 — Net-new strategies, in priority order

Five proposals. Each is a different alpha mechanic in a different (time, direction,
timeframe) coordinate from the current production tier. Sized so each could be wired +
backtested in a focused session, *not* the multi-week experimental-tier slog.

#### 2.1 — `opening_drive_continuation` (RTH 5-min ORB) — HIGHEST PRIORITY

- **Alpha thesis**: the first 5-minute bar of RTH (09:30 – 09:35 ET) prints the institutional
  opening drive's first reaction; ~58 % of sessions break that bar's high or low within 30
  minutes AND continue in the break direction for at least 1× the first-bar range. (Classic
  Linda Raschke "first-bar break".) Not the same as `overnight_range`'s breakout — overnight
  uses a 15-hour anchor and stages brackets at the open; ORB uses the *first RTH bar* as
  anchor and stages brackets after it prints.
- **Direction**: BREAKOUT-CONTINUE (stop-entry above first-bar high → LONG; stop-entry below
  first-bar low → SHORT).
- **Time window**: arm 09:35 ET, exit 11:00 ET or stop. Sits in a *different* hour-of-day
  alpha cluster than every existing strategy.
- **Symbols**: MNQ + MES (highest first-bar volume; MGC's open is too thin).
- **Stop / TP geometry**: stop = 0.5 × first-bar range, TP = 1.5 × first-bar range (1:3 R:R).
- **Live breaker**: 2 consecutive losses → 5-day cooldown (same as `overnight_range`).
- **Prereqs**: Phase 1.2 (fixed-dollar sizer) — first-bar range varies 4× day-to-day so
  contract count must scale inversely with range.
- **Expected metrics (back-of-envelope)**: WR 40-45 %, RF 6-12 on 6m, DD < 30 %.
- **Acceptance criteria for promotion**: 3 / 6 / 9 m walk-forward all positive return AND all
  DD < 50 %.

#### 2.2 — `vwap_pullback_continuation`

- **Alpha thesis**: in trend regimes (RegimeUpdate.regime == 'trend'), pullbacks to session
  VWAP after the opening drive continue ~62 % of the time. The pullback "tests" the
  prevailing institutional bid/ask and resumes the trend. Different mechanic from the existing
  range strategies — VWAP is a volume-anchored continuous indicator, not a fixed range box.
- **Direction**: TREND-CONTINUE (LONG pullback-to-VWAP in uptrend; SHORT in downtrend).
- **Time window**: 10:30 – 14:30 ET (after opening drive resolves, before power hour
  position-squaring distorts VWAP).
- **Symbols**: MNQ + MGC.
- **Stop / TP geometry**: stop = max(0.7 × ATR, VWAP - 1.5 × VWAP standard deviation),
  TP = max(1.5 × ATR, prior session high).
- **Regime gate**: only fires when `RegimeUpdate.regime == 'trend'` AND `adx > 25`. This is
  the Phase 1.3 prereq — without the shared regime classifier this strategy is
  indistinguishable from "buy every dip" which is the same failure mode as the dormant
  `trend_following`.
- **Prereqs**: Phase 1.2 + 1.3.
- **Expected metrics**: WR 55-60 %, RF 4-8 on 6m, DD < 40 %.
- **Acceptance**: 6 m positive return with DD < 50 % AND 9 m positive return with DD < 70 %.

#### 2.3 — `nr_compression_break` (NR7 / Toby Crabel)

- **Alpha thesis**: a daily bar whose true range is the narrowest of the last 7 daily bars
  (NR7) compresses volatility; the next day's break of that bar's high or low continues
  ~65 % of the time with TP at 2× the compression range. Pure daily-TF signal — adds a
  completely different timeframe to the arsenal, materially decorrelating the equity curve.
- **Direction**: BREAKOUT-CONTINUE off the NR7 bar's high/low.
- **Time window**: NR7 condition evaluated at daily close (15:00 ET cash futures, 17:00 ET
  for Globex products). Stop-entry brackets staged for next session 09:30 ET; alive until
  16:00 ET or filled-and-stopped.
- **Symbols**: MNQ + MES + MGC. The daily-TF mechanic is symbol-agnostic and the lower
  cadence (~1-2 signals per month per symbol) means three symbols still gives meaningful n.
- **Stop / TP geometry**: stop = 0.5 × NR7 range (inside the compression), TP = 2 × NR7
  range (Toby Crabel's classic 2:1 to the upside of compression).
- **Caveat**: low cadence means short-window walk-forward metrics will be noisy; needs 9 m
  walk-forward minimum to make any statement.
- **Prereqs**: Phase 1.2 — sizing is *especially* sensitive here because NR7 stops are very
  tight.
- **Expected metrics**: WR 45-55 %, RF 10-20 on 9 m, DD < 25 %.
- **Acceptance**: 9 m positive with DD < 30 % AND a minimum of 12 trades across all symbols
  in the 9 m window.

#### 2.4 — `power_hour_reversion`

- **Alpha thesis**: the last hour of RTH (15:00 – 16:00 ET) sees institutional position-
  squaring. Extreme intraday moves into power hour mean-revert ~58 % as books are flattened
  before close. Specifically: if 14:30 → 15:00 ET 30-minute return exceeds 2 × the 14-day
  ATR, fade it.
- **Direction**: FADE (the existing fade strategies don't operate in power hour; this is
  pure new time-of-day coverage).
- **Time window**: arm 15:00 ET, exit 15:55 ET or stop (force-flat 15:59 MOC).
- **Symbols**: MES (highest close-hour liquidity; tight tick value caps tail risk).
- **Stop / TP geometry**: stop = 0.8 × ATR, TP = 0.5 × the extension being faded (so
  variable R:R but bounded).
- **Prereqs**: Phase 1.2.
- **Expected metrics**: WR 55-60 %, RF 4-7 on 6 m, DD < 25 %.
- **Acceptance**: 3 / 6 / 9 m all positive AND 6 m DD < 30 %.

#### 2.5 — `globex_drift_continuation`

- **Alpha thesis**: during the Globex / Asian session (18:00 – 04:00 ET), low-volume drift
  continues in the direction of the prior RTH session's last-hour close. Captures the
  "follow-through" institutional positioning during the thin-orderbook period.
- **Direction**: TREND-CONTINUE (direction set by 15:00 – 16:00 ET regression slope on the
  prior session; brackets staged for 18:00 ET re-open).
- **Time window**: 18:00 – 04:00 ET. **Fills the largest current time-coverage gap** in the
  arsenal.
- **Symbols**: MNQ + MES. MGC's Globex is too thin for meaningful R:R.
- **Stop / TP geometry**: tight stop (0.5 × ATR — thin overnight orderbook means slippage is
  the risk, not adverse trend), wide TP (1.5 × ATR) to give the drift time to play out
  across the Asian session.
- **Caveat**: TopStepX evaluation accounts have hold-time / overnight margin rules. Validate
  with operator before backtest investment.
- **Prereqs**: Phase 1.2 + a Globex-extended OHLCV CSV (current CSVs are RTH-only).
- **Expected metrics**: WR 52-58 %, RF 5-10 on 6 m, DD < 35 %.
- **Acceptance**: 6 m positive with DD < 40 % AND no single overnight session worse than
  -$200 across all symbols.

### Phase 2 results log (2026-06-03)

#### 2.3 `nr_compression_break` — R1 BASELINE COMMITTED, awaiting tuning

Direct `core/backtest_executor.py --replay --include-trades` over the 9-month aggregate
window 2025-08-01 → 2026-05-01 (continuous, NOT walk-forward folded — first sanity pass):

| Symbol | Trades | PnL    | WR     | PF   | DD    | Expectancy | Sharpe | Exit mix             |
| ------ | ------ | ------ | ------ | ---- | ----- | ---------- | ------ | -------------------- |
| MNQ    | 13     | +$139.62 | 46.15 % | 1.40 | 0.49 % | $10.74 | 2.27 | 7 SL / 6 TP        |
| MES    | 10     | +$38.25  | 40.00 % | 1.25 | 0.24 % | $3.83  | 1.54 | 6 SL / 4 TP        |
| MGC    | 4      | +$49.43  | 50.00 % | 1.38 | 0.42 % | $12.36 | 2.18 | 2 SL / 2 TP        |
| **Σ**  | **27** | **+$227.30** | — | — | — | — | — | 15 SL / 12 TP |

- **Acceptance score**: trade count goal (≥ 25 fills) MET on combined; PF goal (≥ 1.5)
  NOT yet met per-symbol; DD nowhere near the 50 % cliff.
- **Trade cadence**: ~3 trades per month per symbol — Crabel NR7 is *supposed* to be
  rare.  The strategy fires when the prior session's true range is the smallest of the
  last 7 sessions AND yesterday's body / range ratio passes `min_body_pct = 0.10`.
- **TOML**: `config/strategies/nr_compression_break.toml` — `enabled = false` until
  walk-forward fold-level metrics confirm the aggregate.
- **Next**: run the proper walk-forward harness (`scripts/walkforward_trade_recap_report.py`)
  for per-fold variance; if folds are consistent, tune `nr_lookback` (5 vs 7 vs 10) to
  hit the PF ≥ 1.5 bar.

#### 2.5 `globex_drift_continuation` — R1 BASELINE NEGATIVE, parked

6-month aggregate replay 2025-11-01 → 2026-05-01 on MNQ + MES with `enabled = false`
default-disabled config (continuation-bias, `min_body_pct = 0.15`, stop `0.8 × ATR`,
TP `1.8R`, EOD flat at 04:00 ET, pending-bracket cancel at 04:00 ET):

| Symbol | Trades | PnL    | WR     | PF   | DD    | Expectancy | Sharpe |
| ------ | ------ | ------ | ------ | ---- | ----- | ---------- | ------ |
| MNQ    | 80     | -$329.86 | 38.75 % | 0.75 | 1.24 % | -$4.12  | -2.02 |
| MES    | 80     | -$354.97 | 35.00 % | 0.59 | 0.90 % | -$4.44  | -3.40 |

Quick variant sweep on a 3-month MNQ slice (2026-02 → 2026-05) to map the parameter
direction:

| Variant                          | Trades | PnL     | PF   | WR    |
| -------------------------------- | ------ | ------- | ---- | ----- |
| continuation (baseline)          | 39     | -$126.68 | 0.81 | 41.0 % |
| fade (inverted bias)             | 39     | -$421.87 | 0.48 | 30.8 % |
| fade + tp_r=2.5                  | 39     | -$390.99 | 0.55 | 23.1 % |
| fade + min_body=0.30             | 31     | -$245.20 | 0.60 | 35.5 % |

- **Conclusion**: continuation is the *least bad* setup but still negative.  The fade
  variant is worse — confirms the alpha sign is wrong, not just the magnitude.  Both
  directions lose on Globex during this window, which means either (a) the regime
  classifier is mandatory before re-attempting (only trade Globex in trend regimes),
  or (b) the actual Globex edge is in a different time slice (e.g. only 22:00 - 02:00
  ET when European pre-open volume picks up).
- **Verdict**: keep the file as a starting scaffold (it correctly handles the
  cross-midnight Globex session, pre-trigger range freeze, post-04:00-ET pending
  cancel + position flat — all the plumbing the next iteration would need).  Switch on
  ONLY after a regime gate is wired AND a fold-level walk-forward shows positive
  return across both 3 m and 6 m.
- **TOML**: `config/strategies/globex_drift_continuation.toml` — `enabled = false`,
  acceptance criteria documented inline.

### Phase 3 — Optional (after Phase 2 is shipped)

Three follow-up items that become useful only once the arsenal is meaningfully wider:

- **Portfolio signal coordinator** (Alternative B in the deployment blueprint). With 6-8
  strategies on a single account, the long/short conflict surface area grows; an explicit
  precedence coordinator becomes worth the build cost.
- **Cross-strategy correlation monitor** that alarms when 2+ live strategies' equity curves
  cross a 30-day rolling correlation of 0.7 — that's the early-warning that the portfolio is
  no longer diversified and the regime classifier should be retuned.
- **Drawdown-budget-based position scaling** — instead of a fixed dollar risk per trade,
  scale risk down as cumulative drawdown approaches operator caps. Same mechanic as
  TopStepX's daily-loss-limit countdown but applied at the strategy level.
