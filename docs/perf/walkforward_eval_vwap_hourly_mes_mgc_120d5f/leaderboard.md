# Walk-forward strategy competition

- **Bar timeframe:** 5m
- **Span:** last **120** calendar days ending **2026-05-14** (per-symbol CSV last date min)
- **Folds:** 5 non-overlapping segments
- **Strategies:** vwap_zscore_reversion, hourly_anchor_retrace
- **Symbols:** MES, MGC

## Leaderboard (by sum of fold total_pnl)

Trade-weighted **win_rate** = Σ(fold_win_rate × fold_trades) / Σ(trades) (same scale as JSON `win_rate`, typically 0–100).

| rank | strategy | symbol | sum_pnl | total_trades | win_rate_pct | folds_green | mean_fold_sharpe |
|---:|---|---|---:|---:|---:|---:|---:|
| 1 | `vwap_zscore_reversion` | **MGC** | 1537.73 | 83 | 44.58 | 3/5 | 1.697 |
| 2 | `hourly_anchor_retrace` | **MGC** | 1145.00 | 78 | 48.72 | 3/5 | 0.668 |
| 3 | `vwap_zscore_reversion` | **MES** | 274.70 | 82 | 45.12 | 3/5 | 0.803 |
| 4 | `hourly_anchor_retrace` | **MES** | -278.75 | 80 | 50.00 | 3/5 | -1.118 |

## Per-fold detail

See `summary.tsv` and `runs/*.json`.
