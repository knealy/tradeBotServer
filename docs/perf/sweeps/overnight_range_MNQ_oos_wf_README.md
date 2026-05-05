# `overnight_range` — OOS / walk-forward (P0 vs P5)

This folder can hold JSON from **`scripts/run_overnight_range_oos_wf.sh`**, which runs
[`core/research/runner.py`](../../../core/research/runner.py) twice:

| Profile | Meaning |
|---------|---------|
| **p0** | Clears all `OVERNIGHT_RANGE_FILTERS_*` process env overrides → **live TOML** filter values. |
| **p5** | Same widened-band env block as [`run_overnight_range_sweep.sh`](../../../scripts/run_overnight_range_sweep.sh) step P5 (`skip_weekdays=""`, widened range / gap / ATR bands). |

Each run applies a **chronological** split (`--oos-fraction`, default 0.2): first \((1 - \alpha)\) of bars = in-sample, tail = out-of-sample. Monte Carlo (**`--mc`**) shuffles **OOS** trade order only (no edge if `oos_trades == 0` → `mc_pass=False` with reason `no_monte_carlo`).

## Example artifact (Mar 22 – May 1 2026, MNQ 1m, `WALK_FORWARD=0`, `MC=30`)

Command:

```bash
USE_FAST=0 START=2026-03-22 END=2026-05-01 WALK_FORWARD=0 MC=30 \
  bash scripts/run_overnight_range_oos_wf.sh
```

Headline rows (see JSON for full `walk_forward_by_combo` when `WALK_FORWARD>1`):

- **P0** — In this short window with strict live filters, **IS and OOS both had 0 trades** (MC gate skipped). That is *not* a bug; it means the strategy never cleared filters inside either slice.
- **P5** — **IS:** 14 trades, Sharpe ≈ 5.58. **OOS:** 5 trades, Sharpe ≈ −0.38, return ≈ −0.01%, MC `profit_prob` 0 → **fails** the default promotion gate (`mc_min_profit_prob` 0.45).

**Takeaway:** P5 still needs **OOS validation** on a longer window (or full Dec–Apr span) and/or walk-forward folds before any live promotion — the in-sample Sharpe on a thin slice is not sufficient.

## Re-run (full span, slow)

```bash
USE_FAST=0 START=2025-12-24 END=2026-04-30 WALK_FORWARD=4 MC=100 \
  bash scripts/run_overnight_range_oos_wf.sh
```

Expect **tens of minutes** on a full MNQ 1m CSV with walk-forward enabled.
