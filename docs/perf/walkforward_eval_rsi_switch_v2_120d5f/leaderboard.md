# Walk-forward strategy competition

- **Bar timeframe:** 15m
- **Span:** last **120** calendar days ending **2026-05-14** (per-symbol CSV last date min)
- **Folds:** 5 non-overlapping segments
- **Strategies:** rsi_switch_15m
- **Symbols:** MNQ, MES, MGC

## Leaderboard (by sum of fold total_pnl)

Trade-weighted **win_rate** = Σ(fold_win_rate × fold_trades) / Σ(trades) (same scale as JSON `win_rate`, typically 0–100).

| rank | strategy | symbol | sum_pnl | total_trades | win_rate_pct | folds_green | mean_fold_sharpe |
|---:|---|---|---:|---:|---:|---:|---:|
| 1 | `rsi_switch_15m` | **MNQ** | 0.00 | 0 | 0.00 | 0/5 | 0.000 |
| 2 | `rsi_switch_15m` | **MES** | 0.00 | 0 | 0.00 | 0/5 | 0.000 |
| 3 | `rsi_switch_15m` | **MGC** | 0.00 | 0 | 0.00 | 0/5 | 0.000 |

## Per-fold detail

See `summary.tsv` and `runs/*.json`.
