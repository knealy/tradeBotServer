# Strategy Arsenal

Last refresh: 2026-06-03 PM **CRITICAL CORRECTION** (contract-roll data quarantine shipped — see `docs/CHANGELOG.md` "Fixed → Backtest engine — contract-roll data quarantine"): every per-strategy 9 m PnL number on this page is now re-stated against **clean** databento data. The biggest swing: `body_reversion`'s headline $+9,332 collapsed to $+2,150 (−$7,182 / −77 %) because 23 of its 190 MNQ trades were fake "fills" produced by interleaved Sep/Dec contract bars on roll dates (12.1 % of trades, 89 % of PnL). **`morning_range_reversion` is the real arsenal #1** at $+9,082 / 9 m (4× the next-best strategy) — its mean-revert geometry never had meaningful exposure to roll dates. `overnight_range` (R23) sits at $+2,409 unchanged.

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

### 1. `body_reversion` — best risk-adjusted

Round 5/6 walk-forward (270d / 9 folds, MNQ+MGC, committed config = stop_atr=0.35, tp_R=3.0,
ATR-percentile gate, per-symbol overrides, **breaker 4-loss / 3-day cooldown**):

|     | Return     | DD       | RF    | WR     | n   |
| --- | ---------- | -------- | ----- | ------ | --- |
| 3m  | +280.13 %  | 22.79 %  | 10.05 | 38.2 % | 76  |
| 6m  | +519.93 %  |  7.70 %  | 18.66 | 38.9 % | 226 |
| 9m  | **+734.75 %** | **10.27 %** | **24.70** | **38.3 %** | **371** |

- **What it trades**: a single 5m bar with `body_pct ≥ 0.90` + ATR-percentile regime ≥ 0.65 (root) or 0.75 (MGC) → fade entry next bar. Stop = `0.35 × ATR`, TP = `3.0R` (8.6:1).
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

**Post-R23 walk-forward** (270d / 9 folds, MNQ+MGC, NEW config adds `trail_steps_r = [[2.0, 1.0]]` to R22 base, recap dir `docs/perf/overnight_range_round23_postfix/`):

|     | Return       | DD ($)   | RF (PnL/worst-fold-DD) | WR     | n   | avg bars held |
| --- | ------------ | -------- | ---------------------- | ------ | --- | ------------- |
| 3m  | **+$1,266.20** (sweep) | $235 | **4.09**            | **42.9 %** | **42**  | — |
| 6m  | **+$1,598.20** (sweep) | $307 | **4.88**            | **36.6 %** | **82**  | — |
| 9m  | **+$2,852.40** | **$193** | **14.48**          | **39.7 %** | **126** | **4.7** |

Per-symbol on 9m: MNQ +$642.40 / 66 trades / WR 31.8 % (was $604.60 / 62 / 24.2 % on R22 — winners now protected by the 1R lock); MGC +$2,210.00 / 60 trades / WR 48.3 % (was $2,184.50 / 59 / 45.8 %). The DD improvement is the clearest signal — $242.55 → $193 (−20 %) on 9m as the trail caps giveback on running winners.

- **Status**: STAYS PRODUCTION. The R23 step-trail addition is a Pareto-better commit on every horizon (3m / 9m strictly better; 6m essentially flat with same RF). +$60/month on a 2-symbol pair vs R22, but the real win is **better risk-adjusted returns** (RF 14.48 vs 11.50) and **higher WR** (+5pp) — the continuation thesis paid off. Strategy clears the production tier comfortably.
- **What it trades**: tracks 19:00 ET → 10:00 ET range. After 10:00 ET, places stop-entry brackets at range high + **1.5pt** (LONG) and range low − **1.5pt** (SHORT). Live monitor continuously stages the closer-side bracket as price approaches. Gap filter screens out > **1.20 %** open-gap sessions.
- **Continuation behaviour (R23)**: once a position's MFE crosses 2R, SL ratchets to entry + 1R (LONG) or entry − 1R (SHORT). One-way ratchet guard in the engine prevents accidental regression on misconfigured multi-stage entries.
- **Active hours**: 10:00 ET (entry window opens) until 16:00 ET (force-flat — now correctly fires under both slow and fast replay loops). Avg hold 4.7 bars (~24 min).
- **Per-symbol weekday skips**: MNQ skips Mon + Fri (R13, re-validated R22-R23), MGC skips Wed (R13, re-validated R22-R23).
- **Live breaker**: 2 consecutive losses on a symbol → 5-day cooldown (R21, re-validated R22-R23 — still the RF leader among breaker variants).
- **Next research**: (1) wire the step-trail into the **live** OCO order path (today it's backtest-only — see "Live deployment caveat" above); (2) per-fold MNQ analysis — losing folds suggest there's a regime that still hurts; (3) `trail_steps_r` per-symbol override (today it's global-only — MGC may benefit from a different trigger now that we have the infrastructure); (4) re-introduce a regime gate (Phase-1 `core/regime.py`) and gate the breakout to trend regimes only.

### 3. `morning_range_reversion` — committed mean-revert fader

**Direction: FADE** (counter-trend). Opposite side of `overnight_range`'s breakout-continuation,
which is the deliberate diversification: when overnight_range goes LONG above the overnight high,
morning_range_reversion can fade a separate 07:00 – 08:00 morning range and go SHORT on the same
event. Both are sized to take the heat; the breakers limit the bleed.

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

## Stagnant tier — wired but not viable, parked

All three strategies below now generate real trades in walk-forward (the previous "zero
trades" failure was a combination of `execute()` not routing through `place_bracket_order`,
missing `max_hold_bars` exit, and a **TOML-scoping bug** that put root-level knobs under
`[meta]` / `[risk]` headers so `cfg.get_float("stop_atr_multiplier")` was silently falling
through to the strategy default). The 2026-06-02 fix sequence is recorded in each TOML's
header.

But **none of them pass the DD-100 % cliff on a full 9-month walk-forward**, even after the
scoping fix and round-3 parameter sweeps. They are not next-session iteration targets — the
remaining work (fixed-fraction sizing, multi-leg exits, per-day loss caps) is shared
infrastructure that would benefit the production tier too, so it's being prioritised under
*Arsenal roadmap → Phase 1 — Risk infrastructure* below rather than as per-strategy tuning.

| Strategy        | 3m DD | 6m DD | 9m DD | Verdict |
| --------------- | ----- | ----- | ----- | ------- |
| `mean_reversion`   | 120 % | **55 %** | 142 % | RF 3.3 on 6m looks great, but 3m / 9m blow the cliff — structurally non-robust. |
| `trend_following`  |  39 % |  70 % | 148 % | Positive on 3m only; the MA-crossover edge does not survive long windows on 5m TF. |
| `simple_candle`    | 155 % | 139 % | 213 % | Alpha is real (47 % WR × 1.5R) but the let-it-ride logic that captures it also compounds losers — DD > 100 % on EVERY horizon. |

Each TOML retains its 2026-06-02 sweep-validated config + commentary so a future agent
picking these back up has the full evidence trail. They are NOT in any deployment phase,
NOT in any account-partition slot, and NOT on the active research list. **Net conclusion:
the three dormant-strategy slots in the arsenal are a dead end for the current architecture.
Move on.**

## Disabled tier — known-broken or empirically negative

- `overnight_reversion` — 9 months of negative expectancy across every parameter combo. Breakouts continue more often than they revert on the current futures dataset. Disabled in TOML with the full evidence trail.
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
