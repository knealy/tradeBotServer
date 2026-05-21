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
| 1 | `ema_stack_trend_15m` | **MGC** | 702.57 | 10 | 40.00 | 2/5 | 1.524 |
| 2 | `ema_stack_trend_15m` | **MNQ** | 405.25 | 10 | 60.00 | 3/5 | 1.470 |
| 3 | `ema_stack_trend_15m` | **MES** | -149.38 | 14 | 35.71 | 2/5 | -5.664 |

## Per-fold detail

See `summary.tsv` and `runs/*.json`.
