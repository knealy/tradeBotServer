# Regime Detection — White Paper

**Status:** Research memo (2026-07-07)  
**Audience:** Strategy design, risk sizing, live ops  
**Data:** 450-day walk-forward (`docs/perf/regime_longspan_450d/`), validation JSON (`regime_gate_validation.json`)

---

## Executive summary

We have **three different “regime” concepts** in this codebase. They answer different questions and must not be conflated:

| Layer | What it measures | Time horizon | Can it predict Jan-2026 MGC shift? |
|-------|------------------|--------------|-------------------------------------|
| **A. Calendar era gate** (`core/regime_sizing.py`) | Retrospective PnL buckets from walk-forward | Months | **No** — boundaries are fit from history |
| **B. Bar classifier** (`core/regime.py` KER+ADX) | Trend vs chop vs mixed on 5m OHLCV | ~4 hours lookback | **No** — label mix unchanged ±30d around Jan-14-2026 |
| **C. Structural market regime** (gold vol, rates, liquidity) | Macro + microstructure | Weeks–quarters | **Partially** — requires external features not yet wired |

**Bottom line:** The committed KER+ADX publisher is useful for **context and Discord**, not yet for **automatic era detection**. Calendar-era gates are useful for **replay sanity checks and OR pre-Oct blocking**, not for knowing when the *next* regime starts. Endurance-first trading should lean on **breaker + sizing + exit geometry**, with regime overlays as **throttles**, not primary alpha.

---

## 1. What signifies a regime change?

### 1.1 What we observed in our data (empirical)

From `regime_performance.html` and monthly buckets:

- **Portfolio PnL inflection:** Apr–Jul 2025 negative → Sep 2025 positive → Feb–May 2026 dominant.
- **MRR · MGC:** Pre-2026-01-14 −$1,730 (85 trades) → post +$13,242 (60 trades).
- **OR:** Pre-Oct-2025 −$370 (64 trades) → post-Oct +$2,291 (106 trades).
- **High avg-bars + negative PnL:** Almost exclusively **MGC MRR** folds in Apr–Oct 2025 (timeout/chop drag).

These are **outcome-based** regime labels — you only know them after enough trades or months of data.

### 1.2 What `core/regime.py` actually detects

At each 5m bar close (default: MNQ reference, 50-bar KER lookback ≈ **250 minutes**):

- **KER** (Kaufman Efficiency Ratio): net move / total path length  
- **ADX** (14, Wilder): trend strength  
- Labels:
  - `trend`: KER ≥ 0.55 **and** ADX ≥ 25  
  - `chop`: KER ≤ 0.30 **and** ADX ≤ 18  
  - `mixed`: everything else (~65–70% of samples)

**Validation result (`regime_gate_validation.json`):**

Around **MGC Jan-14-2026** (±30 ET days, hourly 5m samples):

| Period | mixed | chop | trend |
|--------|-------|------|-------|
| Before | 62.1% | 37.9% | ~0% |
| After  | 60.1% | 39.7% | 0.2% |

Around **OR Oct-01-2025**:

| Period | mixed | chop |
|--------|-------|------|
| Before | 67.5% | 32.1% |
| After  | 65.4% | 34.6% |

**The classifier did not materially shift at either known PnL boundary.** Flip rate on 5m bars is **~33–35% per hour** — far too noisy for “new era” detection.

### 1.3 What likely changed for MGC in early 2026 (structural hypothesis)

Without a dedicated macro feature pipeline, we infer from market structure literature + your TradeView observation:

1. **Volatility level & persistence** — Gold realized vol and session range width expanded; MRR fade geometry (wide stop, modest TP, high WR) benefits when ranges mean-revert inside the session but **fail** when post-sweep drift continues (2025 wide-range losers).
2. **Overnight → RTH handoff** — MGC overnight drivers (Asia/Europe flow, USD, rates) may have aligned better with **morning fade** in 2026 vs 2025 trend-through-box days.
3. **Liquidity / tick behavior** — Micro contract depth and stop-run patterns affect whether boundary stop-entries fill and revert.
4. **Parameter overfit** — 90-day tuning window overweighted recent winners; Jan-2026+ params match current microstructure but are not timeless.

**None of these are captured by KER+ADX alone.** They need **vol percentile, range-width distribution, maybe DXY/rates proxies, event calendar flags**.

---

## 2. Accuracy: what can we actually detect, and when?

### 2.1 Calendar-era gate (Layer A)

Replay validation on 422 trades (`scripts/regime_gate_validation.py`):

| Gate | Blocked | Blocked PnL | Kept PnL | Notes |
|------|---------|-------------|----------|-------|
| Calendar (OR pre-2025-10-01 block) | 64 | **−$370** | +$18,878 | Removes OR dead era; 20 winners blocked (opportunity cost) |
| Full (+ trend label block) | 65 | −$409 | +$18,917 | +1 OR trade vs calendar |
| MRR calendar | 0 blocked | — | +$16,587 | All trades post-boundary in this sample; **0.5× MGC pre-Jan-14 not tested as block** |

**Accuracy type:** *post-hoc fit* — excellent for explaining history, **zero forward predictive power** unless you add a rule for *when to move the boundary*.

**Honest use:** Offline filters, OR live “we know pre-Oct 2025 was bad in replay”, **not** “detect next regime”.

### 2.2 KER+ADX classifier (Layer B)

Entry-time label vs PnL (same 450d trades):

**MRR**

| Label | n | PnL | WR | Avg/trade |
|-------|---|-----|-----|-----------|
| chop | 82 | +$8,295 | 73% | +$101 |
| mixed | 170 | +$8,292 | 61% | +$49 |

**OR**

| Label | n | PnL | WR |
|-------|---|-----|-----|
| chop | 46 | +$303 | 41% |
| mixed | 123 | +$1,657 | 37% |
| trend | 1 | −$39 | 0% |

**Conclusions:**

- MRR makes money in **both chop and mixed** — blocking either would destroy edge.
- OR is weak in both labels; trend sample is **n=1** — unusable.
- Classifier **does not separate** good vs bad MRR months (Jan-2026 shift invisible).

**Detectable timeframe:** Intraday **micro-regime** (next 1–4 hours), not multi-month **macro-regime**.

**Expected forward accuracy for “new era” alerts:** Low (<55% useful) without additional features. Suitable for **±10–20% size tilt**, not hard gates.

### 2.3 What accuracy is realistic with extensions?

| Feature set | Horizon | Realistic use |
|-------------|---------|---------------|
| KER+ADX only | 1–4h | Context, Discord, mild size tilt |
| + vol percentile (already in snapshot) | 1–5d | Skip dead-vol OR days; throttle MRR on vol-collapse |
| + rolling strategy KPI (30d expectancy, avg bars) | 1–4 weeks | **Endurance gate** — reduce size when live KPI < replay floor |
| + macro calendar (FOMC/CPI/NFP) | Event | Flat or half size ±24h |
| + external (DXY, US2Y, GVZ) | Weeks | Research phase; lag 1–5 days |
| + ML classifier on labeled months | Months | Overfit risk — require walk-forward *out-of-sample* year |

Target for **production regime gate:** optimize **max drawdown reduction per unit of missed PnL**, not prediction accuracy.

---

## 3. Timeframe & scope recommendations

| Decision | Recommended scope | Rationale |
|----------|-------------------|-----------|
| Hard **block** entries | Calendar + event days only | Binary gates need high precision |
| **Half size** | 30d rolling KPI vs replay band | Endurance; catches slow regime drift |
| **Full size** | KPI > floor AND vol in band AND not in breaker cooldown | Default live posture |
| **Regime publisher label** | 5m MNQ reference | Stable liquidity; not symbol-specific for gold |
| **MGC-specific** | Add MGC 5m parallel publisher OR range-width percentile | Gold differs from MNQ microstructure |
| **OR-specific** | Keep gap/ATR filters; add calendar gate for research | Geometry sweep failed; era gate works |

---

## 4. Market mechanics, news, cycles — integration roadmap

### Phase 1 (now — no new data feeds)

1. **Rolling KPI gate** — 20-session rolling expectancy, avg bars, WR; if below 25th percentile of 450d replay → `0.5×` size.
2. **Vol overlay** — use `RegimeSnapshot.vol_pct`; below 0.20 skip OR; above 0.85 half size MGC MRR.
3. **Event calendar** — extend `core/market_calendar.py` with FOMC/CPI dates → Discord + optional flat.

### Phase 2 (manual / CSV)

4. **Macro weekly flag** — operator sets `REGIME_MACRO=risk_on|risk_off|neutral` in `.env` or TOML weekly.
5. **COT / positioning** — weekly gold net spec; research only.

### Phase 3 (automated external)

6. **Rates + USD** — daily change US2Y, DXY correlation to MGC session range.
7. **News NLP** — high noise; only for **halt**, not direction.

**Principle:** News and cycles **reduce size or halt** — they rarely improve entry timing for 5m fade systems.

---

## 5. Endurance-first design (TODO philosophy)

> Focus on configs that keep the account far above blowup; size up only after multi-span survival.

### 5.1 Priority stack (highest leverage first)

1. **Daily / consecutive-loss breakers** (already in TOML) — non-negotiable.
2. **Per-trade $ cap** (`MAX_DOLLAR_RISK_PER_TRADE`) — bounds worst single hit.
3. **Rolling KPI throttle** — catches regime drift before full blowup.
4. **Calendar era 0.5×** — MGC MRR pre-validated bad eras in replay.
5. **Entry filters** (range width, weekday, sweep distance) — precision over recall.
6. **Regime KER+ADX tilt** — last; smallest marginal DD reduction.

### 5.2 Entry vs exit independence

Your note: *exit almost more important than entry for consistent profit.*

| Strategy | Entry logic | Exit / risk logic | Decouple opportunity |
|----------|-------------|-------------------|----------------------|
| MRR | Sweep + boundary stop-entry | SL/TP from range geometry; `max_hold_bars`; flat_before | **Time stop** tightening when avg bars > fold P75; partial TP research rejected but **session PnL halt** viable |
| OR | Overnight range break | ATR bracket + trail `[[2.0,1.0]]` | Widen trail only after 1R; **separate** “cancel unfilled by 11:00 ET” rule |

**Recommendation:** Add `exit_policy` TOML section (time stops, session halt, trail stages) independent of `signal` entry blocks — replay with entry frozen, exit swept.

---

## 6. What types favor reversion (current conditions)

From 450d **entry-time** labels and monthly PnL:

**Favorable for MRR (MNQ + MGC):**

- `mixed` and `chop` both profitable — reversion works when **session range is defined** and **post-sweep drift is bounded** (your `max_range_width_points`, `max_sweep_distance_widths` guards).
- **2026 Feb–May** — dominant PnL months; avg winner size up, MGC WR ~72%+.

**Unfavorable:**

- Wide anchor days that trend through box (filtered by max_range_width on MGC).
- **Long hold losers** — high `bars_held` folds in 2025 MGC.
- OR in **pre-Oct-2025** calendar era regardless of chop/mixed label.

**Live 2026-07 posture:** MRR is in a validated favorable window; treat as **regime-advantage period** with **KPI monitoring**, not permanent edge.

---

## 7. Implementation & tests

| Artifact | Purpose |
|----------|---------|
| `core/regime.py` | KER+ADX classify + optional publisher |
| `core/regime_sizing.py` | Calendar-era multipliers (opt-in env) |
| `scripts/regime_gate_validation.py` | Replay gate PnL impact |
| `scripts/regime_performance_report.py` | Human HTML report |
| `tests/test_regime_sizing.py` | Unit tests for multipliers |
| `tests/test_regime_gate_validation.py` | Validation harness smoke test |

**Re-run after any gate change:**

```bash
.venv/bin/python scripts/regime_gate_validation.py \
  --walkforward-dir docs/perf/regime_longspan_450d

.venv/bin/python scripts/regime_performance_report.py \
  --walkforward-dir docs/perf/regime_longspan_450d
```

**Before enabling live `REGIME_SIZING_ENABLED=1`:**

- [ ] Add rolling 20-session KPI throttle (Phase 1 — not yet coded).
- [ ] Accept OR calendar block is replay-fit; live dates are post-era anyway.
- [ ] Do **not** block MRR on chop/mixed — data rejects this.
- [ ] Wire `#regime` Discord on label change for **human** review, not auto-flat.

---

## 8. Honest limits (what we cannot do yet)

1. **Predict** Jan-2026-style shifts in advance with bar-only indicators.  
2. **Distinguish** temporary chop from multi-month gold regime change within the first week.  
3. **Replace** walk-forward with live classifier for OR thin-margin problem — geometry sweep already failed.  
4. ** Guarantee** prop survival — only reduce probability via breakers + caps + KPI throttles.

---

## 9. Recommended next engineering steps

1. ~~**`core/regime_kpi_gate.py`** — rolling live expectancy vs replay percentiles → size multiplier.~~ **Done 2026-07-07**
2. **MGC parallel regime stream** — classify MGC 5m separately from MNQ.  
3. **Exit-policy sweep** — hold bars, session halt, trail decoupled from entry (endurance metric = max DD).  
4. **Regime change detector v2** — change-point on **strategy KPI** (weekly), not KER alone.  
5. **Out-of-sample year** — hold out 2025 entirely when tuning any gate.

---

## References

- `docs/perf/regime_longspan_450d/regime_performance.html`
- `docs/perf/regime_longspan_450d/regime_gate_validation.json`
- `core/regime.py` module docstring
- `docs/STRATEGY_ARSENAL.md` — committed strategy params
