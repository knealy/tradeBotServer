# Walk-forward strategy competition

- **Bar timeframe:** 5m
- **Span:** last **100** calendar days ending **2026-05-01** (per-symbol CSV last date min)
- **Folds:** 5 non-overlapping segments
- **Strategies:** morning_range_reversion
- **Symbols:** MNQ, MES, MGC
- **Env overrides:** `MORNING_RANGE_REVERSION_SIGNAL_REQUIRE_HIGH_ATR=true, MORNING_RANGE_REVERSION_SIGNAL_TP_MULT=0.7`

## Leaderboard (by sum of fold total_pnl)

| rank | strategy | symbol | sum_pnl | total_trades | folds_green | mean_fold_sharpe |
|---:|---|---|---:|---:|---:|---:|
| 1 | `morning_range_reversion` | **MGC** | 1284.50 | 47 | 4/5 | 7.074 |
| 2 | `morning_range_reversion` | **MNQ** | 1075.50 | 50 | 4/5 | 5.223 |
| 3 | `morning_range_reversion` | **MES** | 591.25 | 52 | 4/5 | 5.487 |

## Per-fold detail

See `summary.tsv` and `runs/*.json`.
