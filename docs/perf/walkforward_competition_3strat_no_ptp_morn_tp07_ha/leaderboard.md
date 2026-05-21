# Walk-forward strategy competition

- **Bar timeframe:** 5m
- **Span:** last **100** calendar days ending **2026-05-01** (per-symbol CSV last date min)
- **Folds:** 5 non-overlapping segments
- **Strategies:** body_reversion, morning_range_reversion, overnight_range
- **Symbols:** MNQ, MES, MGC
- **Env overrides:** `MORNING_RANGE_REVERSION_SIGNAL_REQUIRE_HIGH_ATR=true, MORNING_RANGE_REVERSION_SIGNAL_TP_MULT=0.7`

## Leaderboard (by sum of fold total_pnl)

Trade-weighted **win_rate** = Σ(fold_win_rate × fold_trades) / Σ(trades) (same scale as JSON `win_rate`, typically 0–100).

| rank | strategy | symbol | sum_pnl | total_trades | win_rate_pct | folds_green | mean_fold_sharpe |
|---:|---|---|---:|---:|---:|---:|---:|
| 1 | `overnight_range` | **MGC** | 4278.50 | 81 | 53.09 | 3/5 | 1.709 |
| 2 | `overnight_range` | **MNQ** | 3852.25 | 89 | 52.81 | 4/5 | 3.783 |
| 3 | `body_reversion` | **MGC** | 2259.61 | 150 | 31.33 | 3/5 | 1.450 |
| 4 | `body_reversion` | **MNQ** | 2113.77 | 137 | 31.39 | 4/5 | 1.548 |
| 5 | `overnight_range` | **MES** | 1290.87 | 89 | 50.56 | 4/5 | 2.955 |
| 6 | `morning_range_reversion` | **MGC** | 1284.50 | 47 | 72.34 | 4/5 | 7.074 |
| 7 | `morning_range_reversion` | **MNQ** | 1075.50 | 50 | 72.00 | 4/5 | 5.223 |
| 8 | `body_reversion` | **MES** | 679.24 | 75 | 29.33 | 2/5 | -0.725 |
| 9 | `morning_range_reversion` | **MES** | 591.25 | 52 | 73.08 | 4/5 | 5.487 |

## Per-fold detail

See `summary.tsv` and `runs/*.json`.
