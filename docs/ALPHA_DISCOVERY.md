# Alpha Discovery Process

How to find, validate, and operationalize new trading edges in this repo.

---

## TL;DR (the 5-step loop)

```
1. Pull data       →  bash scripts/fetch_history_csv.sh --symbol MNQ --timeframe 5m --days 365 \\
                       --chunk-days 45 --output historical_data/MNQ_5m.csv
                     (API caps ~20k bars per request; chunking pages the range. Optional: merge multiple
                     CSVs with python historical_data/csv_merger.py a.csv b.csv -o merged.csv)
2. Scan            →  python scripts/alpha_discovery.py --symbol MNQ --csv historical_data/MNQ_5m.csv \\
                       --output docs/alpha/MNQ.md
                     Add --oos-split 0.3 only when you have enough sessions (~50+): tests split by
                     *session date* while features/simulation still use the full bar file (ATR context preserved).
3. Read report     →  cat docs/alpha/MNQ.md   (or open in editor)
4. Filter          →  edit config/strategies/overnight_range.toml — add the ≥1 significant filter
5. Validate        →  run backtest with filter, compare Sharpe/win-rate before and after
```

Repeat monthly or after ≥ 50 new live trades.

**Overnight range live TOML** (`config/strategies/overnight_range.toml`) can make CSV replay show **very few trades** (one attempt per session + filters + weekday skip). That is a *frequency* choice, not proof the edge is “strong.” Use this scanner to decide which filters to keep before chasing more trades — see [OVERNIGHT_RANGE_RESEARCH.md](OVERNIGHT_RANGE_RESEARCH.md) §0.

---

## What the scanner does

`scripts/alpha_discovery.py` runs a fully self-contained analysis:

1. **Feature extraction** (`core/alpha/features.py`) — computes 18 features per session
   from bar data, all at signal time (9:29 AM ET), no lookahead.
2. **Simulation** (`core/alpha/scanner.py`) — walks 5m bars through a vectorised
   overnight-range breakout model (same SL/TP logic as the live strategy).
3. **Hypothesis tests** (`core/alpha/hypothesis.py`) — Kruskal-Wallis for categorical
   features, Mann-Whitney U (Q1 vs Q4) for continuous; IC computed for every feature.
4. **FDR correction** — Benjamini-Hochberg at α = 0.05 across all features simultaneously
   (prevents false discoveries from testing 18 features at once).
5. **Report** (`core/alpha/report.py`) — ranked signal table, per-bucket stats, ASCII bars,
   TOML action guide.

---

## Feature dictionary

| Feature | Description | Typical range |
|---------|-------------|---------------|
| `range_pts` | Overnight high − low in points | MNQ: 50–400 pts |
| `range_atr_ratio` | range_pts / 14-day ATR | 0.5 – 3.0 |
| `gap_pts` | Today's open − prev close (signed) | −100 – +100 pts |
| `gap_abs_pts` | Absolute gap magnitude | 0 – 200 pts |
| `gap_atr_ratio` | gap_abs_pts / ATR | 0 – 1.5 |
| `atr_pts` | 14-period daily ATR at signal time | MNQ: 50–200 pts |
| `atr_pct_rank` | ATR's percentile vs prior 30 sessions | 0 (low vol) – 1 (high vol) |
| `range_position` | Where open sits in range: 0=at low, 1=at high | 0 – 1 |
| `dow` | Day of week: 0=Mon … 4=Fri | categorical |
| `trend_up` | 1 if open > 20-day EMA, 0 otherwise | 0 or 1 |
| `early_range_pct` | Fraction of range formed in first 3 hours overnight | 0 – 1 |
| `overnight_volume` | Total bar volume during overnight session | symbol-dependent |
| `breakout_side` | LONG (bought range_high) or SHORT (sold range_low) | categorical |
| `prior_session_win` | 1 if previous day's session had positive avg R | 0 or 1 |
| `consecutive_wins` | Rolling count of consecutive positive sessions before this one | 0+ |

---

## Reading the report

### Signal ranking table

```
| # | Feature       | IC     | p-value | Sig | Best condition                    |
|---|---------------|--------|---------|-----|-----------------------------------|
| 1 | range_atr_ratio | +0.241 | 0.0012 ** | ✅ | Q2 [0.80–1.20] → avg +0.41R  |
| 2 | dow           | +0.187 | 0.0104 *  | ✅ | Tue → avg +0.38R                  |
```

- **IC** (Information Coefficient): Pearson correlation of the feature with realized-R.
  IC > 0.10 is meaningful; IC > 0.20 is strong.
- **p-value stars**: `*` p<0.05, `**` p<0.01, `***` p<0.001 (before FDR correction — shown for reference).
- **Sig ✅**: survives FDR correction — act on this.
- **Sig ⚡**: not significant but |IC| > 0.12 — worth monitoring over next 30 sessions.
- **Best condition**: the bucket with highest avg R for this feature.

### Per-feature bucket table

```
| Bucket            | N  | Win% | Avg R  | PF     | Avg P&L | Sharpe |
|-------------------|----|------|--------|--------|---------|--------|
| Q1 [0.40–0.80]    | 23 | 39%  | -0.18  | 0.67x  | -$36    | -0.41  |
| Q2 [0.80–1.20]    | 31 | 61%  | +0.41  | 2.10x  | +$82    | +1.12  |
| Q3 [1.20–1.60]    | 28 | 54%  | +0.22  | 1.44x  | +$44    | +0.67  |
| Q4 [1.60–3.20]    | 18 | 44%  | +0.09  | 1.08x  | +$18    | +0.24  |
```

The ASCII bar after Avg R shows relative magnitude across buckets.

### IS → OOS stability table

When `--oos-split 0.3` is used, the report ends with:

```
  Feature                   IS IC    OOS IC   Stable?
  ---------------------------------------------------------
  range_atr_ratio          +0.241   +0.189   ✅
  dow                      +0.187   +0.091   ✅
  gap_atr_ratio            +0.142   -0.031   ⚠️   ← sign flip = overfit
```

**Never apply a filter whose IC sign flips IS → OOS.**  
A small OOS IC that keeps the same sign is acceptable (shrinkage is expected).

---

## Decision criteria: when to act on a signal

| Criterion | Minimum threshold | Strong threshold |
|-----------|-------------------|-----------------|
| Sample size | N ≥ 100 trades | N ≥ 200 trades |
| Statistical significance | p < 0.05 (FDR-corrected) | p < 0.01 |
| IC magnitude | \|IC\| > 0.10 | \|IC\| > 0.20 |
| IS → OOS IC sign | Same sign | \|OOS IC / IS IC\| > 0.5 |
| Best bucket win rate | > 50% | > 55% |
| Best bucket profit factor | > 1.2 | > 1.5 |

If a signal meets *minimum* on all criteria: add it as a filter in TOML (see below).  
If it meets *strong* on all criteria: it's a primary edge — can be the basis of a new strategy.

---

## Applying findings to the strategy

### Adding a filter (example: skip high-volatility days)

Suppose the scan finds `atr_pct_rank` is significant with Q4 (high vol) having avg R = −0.31:

```toml
# config/strategies/overnight_range.toml
[filters]
volatility        = true      # already exists — ensure it's enabled
atr_max           = 160.0     # set to the Q4 lower bound from the report
```

### Adding a day-of-week filter

If Monday has significantly negative avg R:

```toml
[filters]
skip_weekdays = [0]           # 0 = Monday
```

Then add to `overnight_range_strategy.py` in `_scan_for_breakout`:
```python
skip_days = self._get_config().get("filters", {}).get("skip_weekdays", [])
if datetime.now(ET_TZ).weekday() in skip_days:
    return
```

### Creating a new strategy from a discovered edge

If an edge (e.g., "small-range, low-volatility days have IC=0.38") is too specific
to express as a filter on overnight_range:

1. Create `strategies/small_range_breakout_strategy.py` — subclass `strategy_base.py`
2. Create `config/strategies/small_range_breakout.toml`
3. Register in `strategies/strategy_manager.py`
4. Backtest with `--strategy small_range_breakout`
5. Paper-trade ≥ 20 sessions before going live

---

## Running the scan

### Minimum viable run (local CSV, all defaults)

```bash
bash scripts/fetch_history_csv.sh --symbol MNQ --timeframe 5m --days 365 \
  --output historical_data/MNQ_5m.csv

python scripts/alpha_discovery.py \
  --symbol MNQ \
  --csv historical_data/MNQ_5m.csv \
  --output docs/alpha/MNQ_$(date +%Y%m%d).md
```

### Full IS/OOS split + JSON output

```bash
python scripts/alpha_discovery.py \
  --symbol MNQ \
  --csv historical_data/MNQ_5m.csv \
  --oos-split 0.3 \
  --output docs/alpha/MNQ_IS.md \
  --json-output docs/alpha/MNQ.json
```

### Multi-symbol scan

```bash
python scripts/alpha_discovery.py \
  --symbol MNQ MES MGC \
  --csv historical_data/MNQ_5m.csv historical_data/MES_5m.csv historical_data/MGC_5m.csv \
  --oos-split 0.25
```

### Sensitivity: test different SL/TP multipliers

```bash
for STOP in 0.75 1.0 1.25 1.5; do
  for TP in 1.5 2.0 2.5; do
    python scripts/alpha_discovery.py \
      --symbol MNQ --csv historical_data/MNQ_5m.csv \
      --stop-atr $STOP --tp-atr $TP \
      --json-output docs/alpha/sensitivity/stop${STOP}_tp${TP}.json 2>/dev/null
  done
done
# Then compare overall.sharpe across the JSON files:
python3 -c "
import json, glob, os
rows = []
for f in sorted(glob.glob('docs/alpha/sensitivity/*.json')):
    d = json.load(open(f))
    rows.append((os.path.basename(f), d['overall'].get('sharpe',0), d['overall'].get('win_rate',0), d['overall'].get('profit_factor',0)))
rows.sort(key=lambda r: -r[1])
print(f'{'File':<35} Sharpe  WinRate  PF')
for r in rows: print(f'{r[0]:<35} {r[1]:+.2f}   {r[2]:.1%}    {r[3]:.2f}x')
"
```

---

## Monthly cadence (recommended)

```
Week 1 of month:
  - Pull 365 days of fresh data for MNQ, MES, MGC
  - Run alpha scan with --oos-split 0.25
  - Compare signal ranking to last month's report
  - Note any signals that newly became significant OR dropped out

Week 2:
  - For any new significant signal: paper-trade the filter for 3 weeks
  - For any signal that dropped out: disable the corresponding TOML filter

End of month:
  - Commit updated docs/alpha/*.md to repo
  - Update docs/CHANGELOG.md with findings
```

---

## File locations

```
core/alpha/
  __init__.py          exports
  features.py          SessionFeatureExtractor
  hypothesis.py        HypothesisResult, tests, FDR correction
  scanner.py           AlphaScanner + trade simulation
  report.py            AlphaReport (markdown + JSON)

scripts/
  alpha_discovery.py   CLI entry point

docs/alpha/
  *.md                 dated markdown reports (commit these)
  *.json               machine-readable reports (gitignored — add to .gitignore)
  sensitivity/         SL/TP grid scan outputs
```

Add to `.gitignore`:
```
docs/alpha/*.json
docs/alpha/sensitivity/
```

---

## Extending the feature set

To add a new feature:

1. Add the computation to `SessionFeatureExtractor._session_row()` in `core/alpha/features.py`.
2. Add it to `FEATURE_COLS` at the top of that file.
3. Add it to either `CATEGORICAL_TESTS` or `CONTINUOUS_TESTS` in `core/alpha/scanner.py`.
4. Re-run the scan — it appears automatically in the report.

**Lookahead rule:** every feature must be computable from bar data available at 9:29 AM ET
on the session date. If it requires any information from after 9:30 AM, it is invalid.

---

## Connecting to the Risk Brain (Phase 2)

The 12 features selected for the Risk Brain (`docs/STRATEGY_IMPROVEMENT_PLAN.md`) are a
subset of what this scanner tests. Once a feature is confirmed significant:

1. Add it to `FEATURE_COLS` in `core/risk_brain/features.py`.
2. Annotate it in the feature extractor with the confirmed direction (IC sign).
3. The Tier 1 LogisticRegression model will weight it accordingly at inference time.

The alpha scanner is the **hypothesis generation step**. The Risk Brain is the
**live application step**. Always validate with the scanner before trusting the brain.
