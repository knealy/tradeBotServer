# Walk-forward strategy competition

- **Bar timeframe:** 5m
- **Span:** last **120** calendar days ending **2026-05-14** (per-symbol CSV last date min)
- **Folds:** 5 non-overlapping segments
- **Strategies:** rsi_switch_15m
- **Symbols:** MNQ, MES, MGC

## Leaderboard (by sum of fold total_pnl)

Trade-weighted **win_rate** = Σ(fold_win_rate × fold_trades) / Σ(trades) (same scale as JSON `win_rate`, typically 0–100).

| rank | strategy | symbol | sum_pnl | total_trades | win_rate_pct | folds_green | mean_fold_sharpe |
|---:|---|---|---:|---:|---:|---:|---:|
| 1 | `rsi_switch_15m` | **MNQ** | 117.96 | 27 | 48.15 | 3/5 | 16.497 |
| 2 | `rsi_switch_15m` | **MES** | -2.81 | 32 | 43.75 | 2/5 | -2.342 |
| 3 | `rsi_switch_15m` | **MGC** | -895.57 | 26 | 42.31 | 2/5 | -292.658 |

## Per-fold detail

See `summary.tsv` and `runs/*.json`.
