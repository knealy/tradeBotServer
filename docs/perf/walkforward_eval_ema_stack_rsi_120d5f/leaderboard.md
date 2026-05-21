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
| 1 | `ema_stack_trend_15m` | **MGC** | -971.43 | 107 | 34.58 | 2/5 | -0.907 |
| 2 | `ema_stack_trend_15m` | **MES** | -1176.43 | 102 | 36.27 | 2/5 | -1.382 |
| 3 | `ema_stack_trend_15m` | **MNQ** | -1222.68 | 105 | 39.05 | 3/5 | -0.792 |

## Per-fold detail

See `summary.tsv` and `runs/*.json`.
