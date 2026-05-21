# Walk-forward strategy competition

- **Bar timeframe:** 5m
- **Span:** last **90** calendar days ending **2026-05-14** (per-symbol CSV last date min)
- **Folds:** 5 non-overlapping segments
- **Strategies:** overnight_range, overnight_reversion, morning_range_reversion, body_reversion
- **Symbols:** MNQ, MES, MGC

## Leaderboard (by sum of fold total_pnl)

Trade-weighted **win_rate** = Σ(fold_win_rate × fold_trades) / Σ(trades) (same scale as JSON `win_rate`, typically 0–100).

| rank | strategy | symbol | sum_pnl | total_trades | win_rate_pct | folds_green | mean_fold_sharpe |
|---:|---|---|---:|---:|---:|---:|---:|
| 1 | `overnight_reversion` | **MNQ** | 2163.55 | 47 | 55.32 | 5/5 | 3.472 |
| 2 | `morning_range_reversion` | **MGC** | 2141.00 | 48 | 64.58 | 3/5 | 2.966 |
| 3 | `morning_range_reversion` | **MNQ** | 1439.25 | 55 | 65.45 | 4/5 | 4.794 |
| 4 | `overnight_reversion` | **MGC** | 857.00 | 30 | 43.33 | 3/5 | 1.941 |
| 5 | `body_reversion` | **MES** | 837.72 | 60 | 35.00 | 3/5 | 0.082 |
| 6 | `body_reversion` | **MGC** | 459.32 | 98 | 30.61 | 3/5 | 0.159 |
| 7 | `morning_range_reversion` | **MES** | 442.50 | 56 | 71.43 | 5/5 | 3.026 |
| 8 | `overnight_reversion` | **MES** | 231.25 | 48 | 43.75 | 2/5 | 0.385 |
| 9 | `body_reversion` | **MNQ** | 47.71 | 96 | 29.17 | 2/5 | -0.303 |
| 10 | `overnight_range` | **MES** | -265.63 | 59 | 47.46 | 2/5 | -1.042 |
| 11 | `overnight_range` | **MGC** | -539.50 | 35 | 34.29 | 1/5 | -2.273 |
| 12 | `overnight_range` | **MNQ** | -2120.45 | 57 | 36.84 | 0/5 | -4.090 |

## Per-fold detail

See `summary.tsv` and `runs/*.json`.
