# Walk-forward strategy competition

- **Bar timeframe:** 5m
- **Span:** last **100** calendar days ending **2026-05-01** (per-symbol CSV last date min)
- **Folds:** 5 non-overlapping segments
- **Strategies:** body_reversion
- **Symbols:** MNQ, MES, MGC
- **Env overrides:** `BODY_REVERSION_SIGNAL_MIN_BARS_BETWEEN_SIGNALS=5, BODY_REVERSION_SIGNAL_TP_R_MULTIPLE=2.0`

## Leaderboard (by sum of fold total_pnl)

| rank | strategy | symbol | sum_pnl | total_trades | folds_green | mean_fold_sharpe |
|---:|---|---|---:|---:|---:|---:|
| 1 | `body_reversion` | **MNQ** | 4569.86 | 155 | 4/5 | 2.532 |
| 2 | `body_reversion` | **MGC** | 1289.39 | 153 | 3/5 | 1.351 |
| 3 | `body_reversion` | **MES** | 534.33 | 76 | 2/5 | -0.113 |

## Per-fold detail

See `summary.tsv` and `runs/*.json`.
