# Deep pattern scan outputs

Forward-return effect sizes (in **points**) at multiple horizons (1 / 3 / 6 / 12
five-minute bars), Welch t-test + Benjamini–Hochberg FDR, IS/OOS sign stability
on a session-date split (70 / 30 default), phase-stratified follow-up on top
features, **pairwise feature combinations** (AND-conjunctions), **triple-barrier
realised R sweep**, and **stop-only / fixed-time-exit realised R sweep**.

Regenerate (~30 s end-to-end on pre-resampled 5m CSVs):

```bash
.venv/bin/python scripts/deep_pattern_scan.py --symbols MNQ MES MGC \
  --start 2024-01-01
```

See [`../ALPHA_DISCOVERY.md`](../ALPHA_DISCOVERY.md) § "Conditional short-horizon scan"
for a higher-level walkthrough.

- [`deep_scan_MNQ.md`](deep_scan_MNQ.md)
- [`deep_scan_MES.md`](deep_scan_MES.md)
- [`deep_scan_MGC.md`](deep_scan_MGC.md)

---

## Headline finding — body-reversion (cross-instrument, IS/OOS stable, FDR-significant)

A 5-minute bar whose **body fraction** (`|close − open| / (high − low)`) exceeds
**0.90** mean-reverts sharply over the next **15 – 30 minutes**. The effect is
present on MNQ, MES, MGC; sign-stable on the 70/30 session-date split;
significant after Benjamini–Hochberg FDR. The 0.70 threshold (initial v1
prototype) caught too many "normal" trending bars; raising the threshold to
0.90 isolates **capitulation / squeeze** bars whose mean reversion is sharp.

**Effect-size summary (forward-return diff in points, h=1 = 5m forward):**

| body_pct_min | direction       | MNQ diff_pts | MES diff_pts | MGC diff_pts |
|--------------|-----------------|--------------|--------------|--------------|
| `> 0.70`     | bear → LONG     | +5.28        | +1.48        | +1.16        |
| `> 0.85`     | bear → LONG     | +14.7        | +4.5         | +2.4         |
| `> 0.90`     | **bear → LONG** | **+26.6**    | **+6.9**     | +2.6         |
| `> 0.90`     | **bull → SHORT**| **−20.1**    | **−5.4**     | **−3.0**     |

(Sample 2024-01-01 → 2026-05-01, 5 m bars, ~ 165 k bars per symbol.)

**Realised R after stop-only / fixed-time exit (stop = 0.5 × ATR, hold = 6 bars / 30 min):**

| symbol | direction       | mean_R | PF   | win % | n     |
|--------|-----------------|--------|------|-------|-------|
| MNQ    | bear → LONG     | +0.449 | 1.74 | 35.7% | 2,589 |
| MES    | bear → LONG     | +0.370 | 1.57 | 31.5% | 3,618 |
| MGC    | bear → LONG     | +0.164 | 1.25 | 30.8% | 3,963 |
| MNQ    | bull → SHORT    | +0.230 | 1.37 | 32.2% | 2,904 |
| MES    | bull → SHORT    | (~0.20)| ~1.30| ~33%  | ~3,500|

**Best execution policy** (by realised R across all symbols and directions):
**stop = 0.5 × ATR + no TP + close at the close of the 3rd – 6th bar after entry.**
Bracket-style exits with a fixed TP underperform — the alpha lives in the
post-event drift, not in a small TP that fires before mean reversion completes.

### v3 follow-up — regime-gate co-triggers

The deep-scan v2 noted that anchor-only `big_bear_body>0.9` (LONG) /
`big_bull_body>0.9` (SHORT) had unconditional realised R of +0.45 / +0.23 on
MNQ — significant but only marginally tradeable on micros after costs. A
follow-up combo audit ([`body_reversion_combos.md`](body_reversion_combos.md))
paired the anchor with **every** semantically distinct candidate co-trigger
(VWAP, ATR regime, RTH phase, gap, RSI, SMA-side, range expansion, BB touch,
follow-through bars, …) and ran the same realised-R sweep on each joint mask.

Two co-triggers — **`atr_high_q4`** (current 14-bar ATR ≥ rolling 75th
percentile) and **`range_expand_1.5x`** (current bar range > 1.5 × 20-bar
range MA) — produced **delta_R > 0 on every (symbol × direction) cell**.
The 3-way combo `anchor & atr_high_q4 & range_expand_1.5x` is the
cross-instrument winner:

| symbol | direction      | n   | mean_R_SO | PF_SO | delta_R |
|--------|----------------|-----|-----------|-------|---------|
| MNQ    | bear → LONG    | 557 | **+1.977**| 6.15  | +1.47   |
| MES    | bear → LONG    | 699 | **+2.732**| 10.6  | +2.38   |
| MGC    | bear → LONG    | 526 | **+1.958**| 6.52  | +1.53   |
| MNQ    | bull → SHORT   | 501 | +0.772    | 2.99  | +0.56   |
| MES    | bull → SHORT   | 604 | **+1.656**| 6.65  | +1.53   |
| MGC    | bull → SHORT   | 512 | **+2.346**| 7.75  | +1.89   |

n drops ~ 80 % vs anchor-only, mean_R quadruples, PF roughly triples.
Critically the previously-failing **MES SHORT leg** flips to a strong winner
(PF 6.65 vs ~ 1.2 anchor-only). Other co-triggers help (`vwap_dev`,
`bb_touch`, `rsi<30/>70`) but with smaller effect or n drops below 500.

### v3 strategy translation

Translated into the [`body_reversion`](../../strategies/body_reversion_strategy.py)
strategy with v3 defaults: `body_pct_min=0.90, stop_atr_multiplier=0.5,
max_hold_bars=6, min_bars_between_signals=6, allow_long=true, allow_short=true,
require_high_atr=true, require_range_expand=true`. End-to-end replay
backtest on **Q1 2026** (= the OOS slice; full engine path with $2.50
commission + 0.5-tick slippage):

| Symbol | Trades | PnL          | WR    | PF       | Sharpe   | Max DD | Expectancy        |
|--------|--------|--------------|-------|----------|----------|--------|-------------------|
| MNQ    | 48     | **+ $421**   | 27.1% | **1.33** | **1.73** | 0.97 % | **+ $8.76/tr**    |
| MES    | 66     | **+ $1,003** | 31.8% | **1.89** | **3.16** | 0.68 % | **+ $15.20/tr**   |
| MGC    | 53     | **+ $632**   | 32.1% | **1.27** | **1.51** | 2.61 % | **+ $11.92/tr**   |
| Total  | **167**| **+ $2,055** | 30.5% |          |          |        | **+ $12.31/tr**   |

vs v2 (anchor-only, allow_short=false): identical total PnL ($2,055 vs $2,058)
**with 78 % fewer trades**, **expectancy 4.5×**, **Sharpe up on every symbol**
(MES − 0.34 → +**3.16**!), and **MES turned from a − $268 bleed into a
+ $1,003 winner**. v3 is the first version where I'd consider live shadowing.

See [`docs/perf/sweeps/CANDIDATES.md`](../perf/sweeps/CANDIDATES.md) for the
full candidate card, the eight chained fixes that got us from v1 (− $14k) to
v2 (+ $2k), and the **open v3 questions** (RSI extreme on top of regime gates,
session-phase × ATR-regime stratification, marginal contribution of
range_expand vs atr_high_q4 alone).

---

## How to read each per-symbol report

1. **Top forward-return effects (sorted by |diff_pts|)** — the unconditional
   "if I held a contract for N bars after this feature fires, what was the
   mean P&L in points?"  IS_diff/OOS_diff measure stability across the
   session-date split; sign_stable=✅ means the feature points the same way
   in both halves. Mean forward returns sit between hi and lo bands but they
   are *unbounded* outcomes — see the realised-R sections to understand what
   a real bracket would actually capture.
2. **Tradeable candidates** — filtered on `|diff_pts| ≥ 0.5`, sign-stable,
   FDR-significant. This is the "long list" before triple-barrier.
3. **Top features stratified by session phase** — same features, but
   restricted to RTH-open / RTH-mid / RTH-close-120m / ETH-overnight to
   reveal time-of-day concentration.
4. **Pairwise feature combinations (AND-conjunctions)** — top stable
   single features ANDed together; same statistics. A combo "halves the
   trade count and doubles the effect" is the green flag.
5. **Realised R under stop / TP brackets (triple-barrier)** — a (4×5) sweep
   over `(stop_ATR, tp_ATR)` for the top single + combo features.
   Conservative ordering: when a single bar tags both stop and target,
   assume stop fires first. **Tradeable bracket criterion: PF > 1.2 +
   mean_R > 0 + n ≥ 200.**
6. **Realised R: stop-only with fixed-time exit (no TP)** — a (4×4) sweep
   over `(stop_ATR, hold_bars)`. This is the right execution model when the
   alpha lives in the post-event drift, not in a fixed TP. The best
   discovered policy across all three instruments is stop=0.5×ATR /
   hold=3-6 bars (15-30 min).

## Caveats

- Realised R is in units of the stop distance. Multiply by `stop_dist × $/pt`
  to get $ per signal. A realised mean_R of 0.20 with a 0.5×ATR stop on MNQ
  ≈ 0.20 × ~6 pts × $2/pt ≈ **+$2.4 per signal gross**. On NQ (full-size,
  $20/pt) the same edge is **+$24 per signal gross** — so micros may not
  cover commissions/slippage even when minis do.
- The triple-barrier walks **5 m bar high/low** sequences; intra-bar
  fills are simulated by the bar's high/low only. Real fills may differ
  by 1-2 ticks in fast tape.
- Sample window: ~ 2.5 years of 5 m bars. Larger windows would tighten the
  IS/OOS comparison; the current OOS slice is ~ 9 months.
