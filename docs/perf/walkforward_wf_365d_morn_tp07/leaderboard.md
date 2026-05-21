# Walk-forward strategy competition

- **Bar timeframe:** 5m
- **Span:** last **365** calendar days ending **2026-05-01** (per-symbol CSV last date min)
- **Folds:** 5 non-overlapping segments
- **Strategies:** morning_range_reversion
- **Symbols:** MNQ, MES, MGC
- **Env overrides:** `MORNING_RANGE_REVERSION_SIGNAL_TP_MULT=0.7`

## Leaderboard (by sum of fold total_pnl)

| rank | strategy | symbol | sum_pnl | total_trades | folds_green | mean_fold_sharpe |
|---:|---|---|---:|---:|---:|---:|
| 1 | `morning_range_reversion` | **MGC** | 3121.00 | 214 | 4/5 | 1.956 |
| 2 | `morning_range_reversion` | **MNQ** | 2456.75 | 233 | 5/5 | 2.593 |
| 3 | `morning_range_reversion` | **MES** | 1058.12 | 229 | 4/5 | 1.894 |

## Per-fold detail

See `summary.tsv` and `runs/*.json`.
