# Walk-forward strategy competition

- **Bar timeframe:** 5m
- **Span:** last **120** calendar days ending **2026-05-14** (per-symbol CSV last date min)
- **Folds:** 5 non-overlapping segments
- **Strategies:** overnight_range, overnight_reversion, morning_range_reversion, body_reversion
- **Symbols:** MNQ, MES, MGC

## Leaderboard (by sum of fold total_pnl)

Trade-weighted **win_rate** = Σ(fold_win_rate × fold_trades) / Σ(trades) (same scale as JSON `win_rate`, typically 0–100).

| rank | strategy | symbol | sum_pnl | total_trades | win_rate_pct | folds_green | mean_fold_sharpe |
|---:|---|---|---:|---:|---:|---:|---:|
| 1 | `morning_range_reversion` | **MGC** | 3533.50 | 69 | 62.32 | 5/5 | 4.640 |
| 2 | `body_reversion` | **MGC** | 2294.11 | 138 | 30.43 | 4/5 | 0.728 |
| 3 | `morning_range_reversion` | **MNQ** | 1951.25 | 75 | 64.00 | 4/5 | 5.055 |
| 4 | `overnight_reversion` | **MNQ** | 1655.35 | 59 | 50.85 | 3/5 | 0.862 |
| 5 | `morning_range_reversion` | **MES** | 624.38 | 75 | 72.00 | 5/5 | 3.604 |
| 6 | `overnight_reversion` | **MGC** | 585.50 | 37 | 40.54 | 2/5 | -0.455 |
| 7 | `body_reversion` | **MES** | 483.66 | 85 | 28.24 | 3/5 | -1.140 |
| 8 | `overnight_range` | **MES** | 227.87 | 79 | 50.63 | 2/5 | 0.750 |
| 9 | `overnight_reversion` | **MES** | -80.37 | 65 | 41.54 | 2/5 | -0.693 |
| 10 | `body_reversion` | **MNQ** | -114.38 | 137 | 28.47 | 2/5 | -0.439 |
| 11 | `overnight_range` | **MGC** | -1265.50 | 45 | 31.11 | 1/5 | -8.349 |
| 12 | `overnight_range` | **MNQ** | -1623.15 | 73 | 42.47 | 1/5 | -2.228 |

## Per-fold detail

See `summary.tsv` and `runs/*.json`.
