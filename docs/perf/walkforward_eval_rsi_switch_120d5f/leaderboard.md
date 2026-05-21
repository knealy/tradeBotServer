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
| 1 | `rsi_switch_15m` | **MGC** | 26.72 | 486 | 37.86 | 1/5 | 0.042 |
| 2 | `rsi_switch_15m` | **MES** | -4712.99 | 533 | 33.40 | 0/5 | -2.307 |
| 3 | `rsi_switch_15m` | **MNQ** | -5421.20 | 567 | 32.80 | 0/5 | -1.280 |

## Per-fold detail

See `summary.tsv` and `runs/*.json`.
