# Walk-forward strategy competition

- **Bar timeframe:** 5m
- **Span:** last **100** calendar days ending **2026-05-01** (per-symbol CSV last date min)
- **Folds:** 5 non-overlapping segments
- **Strategies:** body_reversion, morning_range_reversion, overnight_range
- **Symbols:** MNQ, MES, MGC

## Leaderboard (by sum of fold total_pnl)

| rank | strategy | symbol | sum_pnl | total_trades | folds_green | mean_fold_sharpe |
|---:|---|---|---:|---:|---:|---:|
| 1 | `overnight_range` | **MGC** | 7301.00 | 80 | 4/5 | 3.904 |
| 2 | `morning_range_reversion` | **MGC** | 4242.00 | 62 | 4/5 | 4.758 |
| 3 | `overnight_range` | **MNQ** | 3044.15 | 89 | 5/5 | 6.165 |
| 4 | `body_reversion` | **MGC** | 2259.61 | 150 | 3/5 | 1.450 |
| 5 | `body_reversion` | **MNQ** | 2113.77 | 137 | 4/5 | 1.548 |
| 6 | `morning_range_reversion` | **MNQ** | 1456.25 | 63 | 4/5 | 4.099 |
| 7 | `overnight_range` | **MES** | 827.75 | 88 | 5/5 | 3.480 |
| 8 | `body_reversion` | **MES** | 679.24 | 75 | 2/5 | -0.725 |
| 9 | `morning_range_reversion` | **MES** | 460.00 | 62 | 3/5 | 2.695 |

## Per-fold detail

See `summary.tsv` and `runs/*.json`.
