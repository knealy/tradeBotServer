# Walk-forward strategy competition

- **Bar timeframe:** 5m
- **Span:** last **100** calendar days ending **2026-05-01** (per-symbol CSV last date min)
- **Folds:** 5 non-overlapping segments
- **Strategies:** overnight_range
- **Symbols:** MNQ, MES, MGC
- **Env overrides:** `OVERNIGHT_RANGE_SIGNAL_STOP_ATR_MULTIPLIER=1.0, OVERNIGHT_RANGE_SIGNAL_TP_ATR_MULTIPLIER=2.0`

## Leaderboard (by sum of fold total_pnl)

| rank | strategy | symbol | sum_pnl | total_trades | folds_green | mean_fold_sharpe |
|---:|---|---|---:|---:|---:|---:|
| 1 | `overnight_range` | **MGC** | 5322.50 | 81 | 3/5 | 2.276 |
| 2 | `overnight_range` | **MNQ** | 4338.95 | 89 | 4/5 | 4.830 |
| 3 | `overnight_range` | **MES** | 1332.88 | 89 | 5/5 | 3.067 |

## Per-fold detail

See `summary.tsv` and `runs/*.json`.
