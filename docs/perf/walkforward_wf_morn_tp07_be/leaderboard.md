# Walk-forward strategy competition

- **Bar timeframe:** 5m
- **Span:** last **100** calendar days ending **2026-05-01** (per-symbol CSV last date min)
- **Folds:** 5 non-overlapping segments
- **Strategies:** morning_range_reversion
- **Symbols:** MNQ, MES, MGC
- **Env overrides:** `MORNING_RANGE_REVERSION_POSITION_MANAGEMENT_BREAKEVEN_ENABLED=true, MORNING_RANGE_REVERSION_SIGNAL_TP_MULT=0.7`

## Leaderboard (by sum of fold total_pnl)

| rank | strategy | symbol | sum_pnl | total_trades | folds_green | mean_fold_sharpe |
|---:|---|---|---:|---:|---:|---:|
| 1 | `morning_range_reversion` | **MGC** | 2792.00 | 62 | 4/5 | 4.570 |
| 2 | `morning_range_reversion` | **MNQ** | 1139.75 | 63 | 5/5 | 3.875 |
| 3 | `morning_range_reversion` | **MES** | 630.00 | 62 | 5/5 | 4.061 |

## Per-fold detail

See `summary.tsv` and `runs/*.json`.
