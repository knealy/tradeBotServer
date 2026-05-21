# Walk-forward strategy competition

- **Bar timeframe:** 5m
- **Span:** last **90** calendar days ending **2026-05-19** (per-symbol CSV last date min)
- **Folds:** 6 non-overlapping segments
- **Strategies:** morning_range_reversion
- **Symbols:** MNQ, MES, MGC

## Leaderboard (by sum of fold total_pnl)

Trade-weighted **win_rate** = Σ(fold_win_rate × fold_trades) / Σ(trades) (same scale as JSON `win_rate`, typically 0–100).

| rank | strategy | symbol | sum_pnl | total_trades | win_rate_pct | folds_green | mean_fold_sharpe |
|---:|---|---|---:|---:|---:|---:|---:|
| 1 | `morning_range_reversion` | **MGC** | 2301.50 | 49 | 65.31 | 5/6 | 2.918 |
| 2 | `morning_range_reversion` | **MNQ** | 1485.00 | 56 | 64.29 | 5/6 | 4.663 |
| 3 | `morning_range_reversion` | **MES** | 347.50 | 56 | 67.86 | 4/6 | 2.289 |

## Per-fold detail

See `summary.tsv` and `runs/*.json`.
