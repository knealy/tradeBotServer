# Walk-forward strategy competition

- **Bar timeframe:** 5m
- **Span:** last **100** calendar days ending **2026-05-01** (per-symbol CSV last date min)
- **Folds:** 5 non-overlapping segments
- **Strategies:** body_reversion
- **Symbols:** MNQ, MES, MGC
- **Env overrides:** `BODY_REVERSION_SIGNAL_MIN_BARS_BETWEEN_SIGNALS=4, BODY_REVERSION_SIGNAL_TP_R_MULTIPLE=1.5`

## Leaderboard (by sum of fold total_pnl)

| rank | strategy | symbol | sum_pnl | total_trades | folds_green | mean_fold_sharpe |
|---:|---|---|---:|---:|---:|---:|
| 1 | `body_reversion` | **MNQ** | 3444.46 | 158 | 4/5 | 1.440 |
| 2 | `body_reversion` | **MES** | 316.38 | 78 | 2/5 | -0.669 |
| 3 | `body_reversion` | **MGC** | 226.32 | 159 | 2/5 | 0.351 |

## Per-fold detail

See `summary.tsv` and `runs/*.json`.
