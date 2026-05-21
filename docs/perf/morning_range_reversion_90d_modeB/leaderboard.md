# Walk-forward strategy competition

- **Bar timeframe:** 5m
- **Span:** last **90** calendar days ending **2026-05-19** (per-symbol CSV last date min)
- **Folds:** 6 non-overlapping segments
- **Strategies:** morning_range_reversion
- **Symbols:** MNQ, MES, MGC
- **Env overrides:** `MORNING_RANGE_REVERSION_SIGNAL_REQUIRE_REENTRY_CLOSE=false`

## Leaderboard (by sum of fold total_pnl)

Trade-weighted **win_rate** = Σ(fold_win_rate × fold_trades) / Σ(trades) (same scale as JSON `win_rate`, typically 0–100).

| rank | strategy | symbol | sum_pnl | total_trades | win_rate_pct | folds_green | mean_fold_sharpe |
|---:|---|---|---:|---:|---:|---:|---:|
| 1 | `morning_range_reversion` | **MGC** | 800.50 | 55 | 52.73 | 3/6 | -1.152 |
| 2 | `morning_range_reversion` | **MNQ** | 395.00 | 60 | 53.33 | 5/6 | 1.008 |
| 3 | `morning_range_reversion` | **MES** | -197.50 | 60 | 56.67 | 2/6 | -0.986 |

## Per-fold detail

See `summary.tsv` and `runs/*.json`.
