# Walk-forward strategy competition

- **Bar timeframe:** 5m
- **Span:** last **60** calendar days ending **2026-05-14** (per-symbol CSV last date min)
- **Folds:** 4 non-overlapping segments
- **Strategies:** mean_reversion, trend_following, simple_momentum, simple_candle, trend_scalping, simple_rth, vwap_zscore_reversion, hourly_anchor_retrace
- **Symbols:** MNQ

## Leaderboard (by sum of fold total_pnl)

Trade-weighted **win_rate** = Σ(fold_win_rate × fold_trades) / Σ(trades) (same scale as JSON `win_rate`, typically 0–100).

| rank | strategy | symbol | sum_pnl | total_trades | win_rate_pct | folds_green | mean_fold_sharpe |
|---:|---|---|---:|---:|---:|---:|---:|
| 1 | `vwap_zscore_reversion` | **MNQ** | 565.11 | 42 | 45.24 | 3/4 | 1.489 |
| 2 | `hourly_anchor_retrace` | **MNQ** | 561.25 | 39 | 58.97 | 3/4 | 2.574 |
| 3 | `mean_reversion` | **MNQ** | 0.00 | 0 | 0.00 | 0/4 | 0.000 |
| 4 | `trend_following` | **MNQ** | 0.00 | 0 | 0.00 | 0/4 | 0.000 |
| 5 | `simple_momentum` | **MNQ** | 0.00 | 0 | 0.00 | 0/4 | 0.000 |
| 6 | `trend_scalping` | **MNQ** | 0.00 | 0 | 0.00 | 0/4 | 0.000 |
| 7 | `simple_rth` | **MNQ** | 0.00 | 0 | 0.00 | 0/4 | 0.000 |
| 8 | `simple_candle` | **MNQ** | -5262.84 | 524 | 44.27 | 2/4 | -1.522 |

## Per-fold detail

See `summary.tsv` and `runs/*.json`.
