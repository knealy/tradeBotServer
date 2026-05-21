# Walk-forward strategy competition

- **Bar timeframe:** 5m
- **Span:** last **120** calendar days ending **2026-05-14** (per-symbol CSV last date min)
- **Folds:** 5 non-overlapping segments
- **Strategies:** ema_stack_trend_15m
- **Symbols:** MNQ, MES, MGC

## Leaderboard (by sum of fold total_pnl)

Trade-weighted **win_rate** = Σ(fold_win_rate × fold_trades) / Σ(trades) (same scale as JSON `win_rate`, typically 0–100).

| rank | strategy | symbol | sum_pnl | total_trades | win_rate_pct | folds_green | mean_fold_sharpe |
|---:|---|---|---:|---:|---:|---:|---:|
| 1 | `ema_stack_trend_15m` | **MGC** | 388.43 | 101 | 39.60 | 3/5 | -0.548 |
| 2 | `ema_stack_trend_15m` | **MES** | -329.82 | 100 | 35.00 | 1/5 | -1.093 |
| 3 | `ema_stack_trend_15m` | **MNQ** | -1420.75 | 99 | 34.34 | 2/5 | -3.429 |

## Per-fold detail

See `summary.tsv` and `runs/*.json`.
