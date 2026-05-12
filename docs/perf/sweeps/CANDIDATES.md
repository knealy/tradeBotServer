# Strategy candidate ledger

A working list of new strategy hypotheses that look worth testing on the
existing CSV archive (MNQ 1m and, once §2 of the plan completes, MES + MGC
1m). Each card has the same shape so we can promote → demote → archive
without losing context. For **bar-level** conditional stats (Fisher + FDR on
5m features vs next bar), see [docs/alpha/pattern_scan_INDEX.md](../../alpha/pattern_scan_INDEX.md).

Conventions:

- **Hypothesis** — one sentence, falsifiable.
- **Indicator** — what is computed, on which bars.
- **Cadence** — expected trades / week / symbol.
- **Validation** — a *cheap* script we already have (or trivially can call) that says yes/no on edge.
- **Kill criteria** — when we stop spending time on it.
- **Status** — `idea` → `prototyped` → `live-shadowed` → `paper` → `live` (or `archived`).

A strategy graduates from `idea` once a `core.research.runner` or
`alpha_discovery` run on at least one symbol shows non-trivial OOS edge
(probability_of_profit ≥ 0.55 over MC, Sharpe > 1 OOS, IC ≥ 0.10 on its
core feature, no single-trade dominance).

---

## 0. `body_reversion` — *prototyped (v3.1 hybrid)* ★★★

- **Hypothesis** — A 5 m bar with body (`|close − open|`) consuming **> 90 %** of the bar range is a capitulation/squeeze candle that mean-reverts sharply over the next **15 – 30 minutes** (cross-instrument: MNQ / MES / MGC). v1 used `> 70 %` and was net-flat after costs; v2 raised the threshold to isolate the tradeable subset; **v3 added regime co-triggers**; **v3.1 hybrid makes those co-triggers per-symbol** so each instrument gets the best cadence-vs-quality tradeoff.
- **Indicator** — Per 5 m bar:
  1. `body = |close − open| / (high − low) ≥ body_pct_min` (default **0.90**).
  2. *(v3.1 default ON)* `atr_high_q4`: current 14-bar ATR ≥ rolling 75th percentile of the trailing 500-bar ATR distribution (≈ 3 sessions on 5m).
  3. *(v3.1 hybrid: ON for MES only)* `range_expand_1.5x`: current bar range > 1.5 × the trailing 20-bar range MA.
  4. Lockout: skip if a position **or** any pending entry-stop already exists for the symbol.
- **Cadence** — *Low–medium*, but **symbol-dependent** in v3.1:
  - MNQ + MGC: ATR-only gate keeps higher cadence.
  - MES: ATR + range-expansion keeps cadence lower but prevents the short-leg bleed.
  `min_bars_between_signals = 6` (30 min cooldown) still applies.
- **Entry / exit** — Stop-bracket entry one tick beyond bar close on the *opposite* side (`bull → SHORT`, `bear → LONG`). **SL = 0.5 × ATR(14)**. TP = `3 × stop distance` (effectively a runaway-move guard). Practical exit is the **time unwind**: close after `max_hold_bars = 6` 5 m bars (= 30 min) if neither stop nor target fires.
- **Module** — [`strategies/body_reversion_strategy.py`](../../../strategies/body_reversion_strategy.py) + [`config/strategies/body_reversion.toml`](../../../config/strategies/body_reversion.toml). PRAC/live: [`scripts/run_reversion.sh`](../../../scripts/run_reversion.sh); set `[meta] enabled = false` if you want replay-only / no executor auto-select.
- **Trade review (Q1 2026, v3.1 hybrid)** — per-symbol folders with `backtest.json` (`--include-trades`), `trades.md` / `trades.csv`, and **Lightweight Charts** HTML per trade: [MNQ](../trade_review/body_reversion_v31_hybrid_q1_2026_MNQ/README.md) · [MES](../trade_review/body_reversion_v31_hybrid_q1_2026_MES/README.md) · [MGC](../trade_review/body_reversion_v31_hybrid_q1_2026_MGC/README.md).
- **Discovery reports** — [`docs/alpha/deep_scan_INDEX.md`](../../alpha/deep_scan_INDEX.md) for the anchor effect; [`docs/alpha/body_reversion_combos.md`](../../alpha/body_reversion_combos.md) for the v3 co-trigger audit. At `body > 0.9`, h=1 forward-return diff is **+ 26.6 / + 6.9 / + 2.6 pts** (MNQ / MES / MGC) for bear→LONG and **− 20.1 / − 5.4 / − 3.0 pts** for bull→SHORT — all IS/OOS sign-stable on the 70/30 session-date split, all FDR-significant. Realised R under stop-only / fixed-time exit (stop=0.5×ATR, hold=6 bars):
  - **anchor only (v2):** MNQ +0.45 PF 1.74, MES +0.37 PF 1.57, MGC +0.16 PF 1.25 (all bear→LONG).
  - **anchor & atr_high_q4 & range_expand_1.5x (v3):** MNQ +**1.98** PF **6.15**, MES +**2.73** PF **10.6**, MGC +**1.96** PF **6.52** (bear→LONG); MNQ +0.77 PF 2.99, MES +**1.66** PF **6.65**, MGC +**2.35** PF **7.75** (bull→SHORT). Same n ≥ 500 each cell — population-bounded but real.
- **End-to-end replay backtest (Q1 2026 = OOS slice, full engine path with $2.50/contract commission + 0.5-tick slippage):**

  **Gate A/B/C study (Q1 2026 OOS)** — quantify the marginal contribution of each regime gate.
  - **A = ATR-only** (`require_high_atr=true`, `require_range_expand=false`)
  - **B = Range-only** (`require_high_atr=false`, `require_range_expand=true`)
  - **C = Both** (`require_high_atr=true`, `require_range_expand=true`)

  | Symbol | Gate | Trades | PnL      | PF   | Sharpe | Max DD % | Expectancy |
  |--------|------|--------|----------|------|--------|----------|------------|
  | MNQ    | A    | 108    | + $992   | 1.34 | 1.86   | 1.14     | + $9.18    |
  | MNQ    | B    | 141    | + $377   | 1.14 | 0.79   | 1.66     | + $2.67    |
  | MNQ    | C    | 48     | + $421   | 1.33 | 1.73   | 0.97     | + $8.76    |
  | MES    | A    | 164    | + $392   | 1.12 | 0.65   | 1.82     | + $2.39    |
  | MES    | B    | 226    | + $1,360 | 1.51 | 1.95   | 2.33     | + $6.02    |
  | MES    | C    | 66     | + $1,003 | 1.89 | 3.16   | 0.68     | + $15.20   |
  | MGC    | A    | 145    | + $3,321 | 1.50 | 2.21   | 2.61     | + $22.90   |
  | MGC    | B    | 182    | + $1,481 | 1.27 | 1.47   | 2.27     | + $8.14    |
  | MGC    | C    | 53     | + $632   | 1.27 | 1.51   | 2.61     | + $11.92   |

  **Interpretation:** MNQ + MGC prefer **ATR-only** (A) — range-expansion gate filters out too much good signal. MES prefers **Both** (C) — the range-expansion gate is critical to isolate event bars and prevent the short-leg bleed.

  **v3.1 hybrid defaults (recommended):**
  - Global: `require_high_atr=true`, `require_range_expand=false`
  - Override: `[symbols.MES.signal] require_range_expand=true`

  This matches A on MNQ/MGC and C on MES.

  **v2 baseline (anchor only, allow_short=false):**

  | Symbol    | Trades | PnL          | WR     | PF       | Sharpe   | Max DD   | Expectancy        |
  |-----------|--------|--------------|--------|----------|----------|----------|-------------------|
  | **MNQ**   | 48     | **+ $421**   | 27.1 % | **1.33** | **1.73** | 0.97 %   | **+ $8.76/tr**    |
  | **MES**   | 66     | **+ $1,003** | 31.8 % | **1.89** | **3.16** | 0.68 %   | **+ $15.20/tr**   |
  | **MGC**   | 53     | **+ $632**   | 32.1 % | **1.27** | **1.51** | 2.61 %   | **+ $11.92/tr**   |
  | **Total** | **167**| **+ $2,055** | 30.5 % |          |          |          | **+ $12.31/tr**   |

  **Full-window gate A (MNQ, 2024-01-01 → 2026-05-01, interim):** one completed replay with **ATR-only** gates shows **962 trades**, **+$12,475** PnL, **PF 1.46**, **Sharpe 1.83** (local `/tmp/body_rev_gate_ab/full_2024_2026/body_rev_MNQ_A.json`). **Weekly read:** `scripts/print_weekly_income.py` on that JSON (summary mode) prints **`avg_pnl_per_week_usd`**; for a true week-by-week histogram, re-run the cell once with **`--include-trades`** and point the script at the larger JSON. **Resume the full 9-cell grid:** **`bash scripts/resume_body_rev_gate_ab_full.sh`** — parallel (**`GATE_AB_JOBS`** default **3**), skips JSON **>200 bytes**. Rough **~30–60 min per cell** CPU time ⇒ sequential wall-clock **~5–9 h** for nine from scratch; parallel cuts wall-clock toward **~2–3 h** when three symbols saturate cores (MES/MGC cells still heavy).

  **Actionable decision:** use **v3.1 hybrid** (ATR-only on MNQ/MGC, Both on MES). This is the first configuration that:
  - keeps **MNQ/MGC cadence** without sacrificing Sharpe
  - fixes MES by applying the stricter event filter only where it is needed.
- **Validation** —
  1. Pre-resample to 5 m once (10× faster replay):
     ```bash
     for s in MNQ MES MGC; do
       .venv/bin/python historical_data/resample_ohlcv_csv.py \
         -i historical_data/price/${s}_1m_databento_GLBX-20260504-UDPDE7PWXR.csv \
         --to 5m -o historical_data/price/${s}_5m_databento.csv
     done
     ```
  2. Combo audit (regenerate the regime-gate evidence):
     ```bash
     .venv/bin/python scripts/body_reversion_combo_audit.py --symbols MNQ MES MGC
     ```
  3. Replay end-to-end:
     ```bash
     for s in MNQ MES MGC; do
       ENABLE_SIGNALR=false PYTHONUNBUFFERED=1 \
         .venv/bin/python core/backtest_executor.py --strategy=body_reversion \
           --symbol=$s --timeframe=5m \
           --csv=historical_data/price/${s}_5m_databento.csv \
           --start=2026-01-01 --end=2026-05-01 --replay \
           --format=json --include-trades \
           > /tmp/body_rev_v3_${s}.json
     done
     ```
  4. Grid search: `python -m core.research.runner --strategy=body_reversion --csv=…` with `body_pct_min=0.85..0.95:0.025`, `atr_regime_quantile=0.5..0.9:0.1`, `range_expand_mult=1.0..2.0:0.25` and **per-symbol** `require_range_expand` for MES only.
- **Kill criteria** — OOS Sharpe < 0.5 across **all three** symbols at v3 defaults, OR median realised R after costs (commission + 1 tick on entry+exit) ≤ 0, OR live shadow over 2 weeks fails to mirror replay (> 20 % R deviation), OR n < 200 trades / 6 mo at the gating defaults (signal too rare to pay).
- **Open questions / lessons learned** —
  (a) *(v2 finding, retained — engine bugs that affect every bracket-style backtest)*:
      (1) `MockTradingBot.calculate_atr` returned the ATR of the *last 14 bars
      of the entire input* instead of the last 14 bars *up to the current
      replay timestamp* — inflated stops 10-50× during volatile stretches.
      Strategy now bypasses `bot.calculate_atr` and computes ATR locally.
      (2) When SL **and** TP both touched the same bar (low ≤ stop AND
      high ≥ limit), `_check_order_fill`'s loop fired SL first → position
      closed → then TP fired → no position → engine opened a phantom
      reverse position via the "Opening new position" branch in
      `_update_position`. OCO sibling cancellation now runs **eagerly**
      (the moment one bracket leg fills) rather than after the whole
      `for order in pending` loop in `core/backtest/strategy_replay.py`.
      (3) Strategy lockout: `analyze` returns early if a position or pending
      entry-stop already exists for the symbol (prevents stacked entries
      whose orphaned exits would otherwise fire later as phantom positions).
  (b) Does the edge **survive on the full-size NQ / ES** where the per-point dollar value is 10× and the fixed cost ratio collapses? Now even more interesting because v3 has fewer trades — needs more thoughtful sizing.
  (c) **(Open)** What's the marginal contribution of `range_expand` vs `atr_high_q4` alone? Quick A/B with `require_range_expand=false` would say. Hypothesis from the audit: `atr_high_q4` alone keeps ~ 3× the trade count at ~ 70 % of the per-trade edge. Worth confirming if the higher cadence beats the higher quality on a Sharpe basis.
  (d) **(Open)** Does **session-phase × ATR-regime** stratification reveal that the q4 gate is dominated by `eth_overnight` bars? The deep scan said `eth_overnight` strengthens the anchor; the combo audit said `atr_high_q4` does too, but the two might be the same set. Needs a 3-way stratification.
  (e) **(Resolved)** Asymmetric P&L (LONG vs SHORT) — v2 disabled SHORT to avoid the rally bleed; **v3 re-enables SHORT and it becomes the strongest cell on MES** (PF 1.89, Sharpe 3.16), confirming that the regime gates were the missing piece.
  (f) **(Resolved)** MES profitability — v2 was net-flat ($-268, PF 0.94); v3 nets **+ $1,003 with PF 1.89**, the regime gates fixed it.
  (g) **(Open)** RSI-extreme co-trigger (`rsi<30` LONG, `rsi>70` SHORT) showed a **massive** delta_R in the audit (+1.4 to +3.7 cross-symbol) but at population sizes (n ≈ 400–700 per cell) that overlap heavily with the q4-ATR set. Worth a 4-way audit to see if RSI adds anything **on top of** q4-ATR + range_expand.

## 1. `vwap_zscore_reversion` — *prototyped*

- **Hypothesis** — Sharp intraday deviations of close from session VWAP
  (|Z| ≥ 2 σ over a 30-bar residual window) revert toward VWAP within the
  same RTH session.
- **Indicator** — Session-anchored VWAP from typical-price × volume; rolling
  stdev of `close - VWAP` over `window_bars`. Z = residual / σ.
- **Cadence** — ~3–8 trades / week / symbol (RTH 09:35–15:30).
- **Entry / exit** — Stop-bracket entry one tick beyond close on the
  reversion side. TP = VWAP. SL = `stop_atr_multiplier × ATR`.
- **Module** — [strategies/vwap_zscore_reversion_strategy.py](../../../strategies/vwap_zscore_reversion_strategy.py)
  + [config/strategies/vwap_zscore_reversion.toml](../../../config/strategies/vwap_zscore_reversion.toml).
- **Validation** — `core/backtest_executor.py --strategy=vwap_zscore_reversion --csv=… --replay`
  for each symbol; once stable, `python -m core.research.runner --strategy=vwap_zscore_reversion --csv=…`
  with a small grid (`z_entry=1.5..2.5:0.25, stop_atr_multiplier=1.0..2.0:0.25`).
- **Kill criteria** — OOS Sharpe < 0.5 across **all three** symbols at
  default knobs, or median trade R < 0 once costs (commission + 1 tick
  slippage) are applied.

## 2. `orb_5m` — *idea*

- **Hypothesis** — In micro index futures the high/low of the first 5m
  RTH bar acts as a magnet: a clean break in either direction during the
  next 30m has positive expectancy with stop = opposite side.
- **Indicator** — First 5m bar (09:30:00–09:34:59 ET); breakout window 09:35–10:05.
- **Cadence** — ~3 trades / week / symbol (most days fail filters).
- **Validation** — `scripts/alpha_discovery.py --csv MNQ_5m.csv --output docs/alpha/MNQ_orb.md`;
  feature `range_position` + `dow` should drive R; if IC < 0.10, archive.
- **Kill criteria** — IC < 0.10 OR session WR < 45% OOS.
- **Promote** — only after alpha_discovery confirms — then scaffold
  `strategies/orb_5m_strategy.py` mirroring `simple_momentum` shape.

## 3. `bollinger_revert` — *idea*

- **Hypothesis** — 20-bar Bollinger Band touches at ±2σ on 5m bars during
  RTH revert to the basis (mid band) within 6 bars about 60% of the time.
- **Indicator** — `bb_upper / bb_lower / bb_basis` from a 20-bar SMA + 2σ.
- **Cadence** — ~6–10 trades / week / symbol; turbulent in earnings weeks.
- **Validation** — `core/backtest_executor.py --strategy=mean_reversion …`
  *with widened* `atr_threshold` (already wired) is a close enough first
  pass; if results look promising, scaffold a Bollinger-specific strategy.
- **Kill criteria** — Sharpe < 0.7 on MNQ + MES, or DD/PnL > 1.5.

## 4. `volatility_breakout` — *idea*

- **Hypothesis** — When 14-bar ATR percentile > 0.8 (top quintile of recent
  vol) and price closes outside an N-day Donchian channel, the breakout
  continues for 1–2 bars.
- **Indicator** — Donchian(20), ATR(14) percentile rank vs prior 30 sessions.
- **Cadence** — Rare (~1–2 / week / symbol) — high quality preferred over count.
- **Validation** — `alpha_discovery` features `atr_pct_rank` + `range_atr_ratio`
  already exist; if their IC > 0.20 on R, scaffold the strategy.
- **Kill criteria** — Below median IC vs other features.

## 5. `calendar_spread_micro` — *idea (data-permitting)*

- **Hypothesis** — Front-vs-next-month MES (or MNQ) spread mean-reverts
  around the value-date / contract-roll cycle.
- **Indicator** — Continuous price of front month minus continuous price of
  next month, normalised by ATR of the front.
- **Status** — needs **two** continuous histories per symbol pair; not yet
  available. Revisit after `walkback_history.sh` is run with
  `SYMBOLS="MES.next MES"` style flags or with TopStepX explicit contract IDs.
- **Validation** — Pure z-score backtest on the spread (no live order
  routing yet — TopStepX bracket model is single-instrument).

## 6. `tape_microstructure` — *deferred*

- Order-flow / liquidity-imbalance signals are out of reach until we have
  a tick or order-book data source. Tracked here so we don't lose context.

---

## How to promote a candidate

1. Run a `core/backtest_executor.py … --replay --include-trades` pass on
   each symbol; record metrics in the candidate's section above.
2. If the metrics survive (kill-criteria not triggered), run
   `core.research.runner` with a small grid on the dominant knob.
3. If OOS still survives, run `scripts/alpha_discovery.py` on the relevant
   feature dictionary to confirm the underlying driver.
4. Update `docs/CHANGELOG.md` and add a real strategy module +
   `config/strategies/<name>.toml` per
   [AGENTS.md](../../AGENTS.md) "When you change code".
