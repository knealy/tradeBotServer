# Walk-forward strategy competition

- **Bar timeframe:** 5m
- **Span:** last **100** calendar days ending **2026-05-01** (per-symbol CSV last date min)
- **Folds:** 5 non-overlapping segments
- **Strategies:** body_reversion, morning_range_reversion, overnight_range
- **Symbols:** MNQ, MES, MGC
- **Env overrides:** `BODY_REVERSION_RISK_POSITION_SIZE=2, BODY_REVERSION_SIGNAL_PARTIAL_TP_ENABLED=true, MORNING_RANGE_REVERSION_RISK_POSITION_SIZE=2, MORNING_RANGE_REVERSION_SIGNAL_PARTIAL_TP_ENABLED=true, MORNING_RANGE_REVERSION_SIGNAL_REQUIRE_HIGH_ATR=true, MORNING_RANGE_REVERSION_SIGNAL_TP_MULT=0.7, OVERNIGHT_RANGE_RISK_POSITION_SIZE=2, OVERNIGHT_RANGE_SIGNAL_PARTIAL_TP_ENABLED=true`

## Leaderboard (by sum of fold total_pnl)

| rank | strategy | symbol | sum_pnl | total_trades | folds_green | mean_fold_sharpe |
|---:|---|---|---:|---:|---:|---:|
| 1 | `overnight_range` | **MNQ** | 7038.05 | 137 | 4/5 | 4.055 |
| 2 | `overnight_range` | **MGC** | 3848.00 | 123 | 3/5 | 0.530 |
| 3 | `body_reversion` | **MNQ** | 2692.43 | 221 | 4/5 | 1.123 |
| 4 | `overnight_range` | **MES** | 2361.62 | 135 | 3/5 | 2.843 |
| 5 | `morning_range_reversion` | **MGC** | 2133.00 | 77 | 4/5 | 4.008 |
| 6 | `morning_range_reversion` | **MNQ** | 2046.00 | 85 | 4/5 | 4.521 |
| 7 | `body_reversion` | **MES** | 345.18 | 115 | 2/5 | -0.610 |
| 8 | `body_reversion` | **MGC** | -0.35 | 233 | 4/5 | 0.581 |
| 9 | `morning_range_reversion` | **MES** | -72.50 | 85 | 2/5 | 1.051 |

## Per-fold detail

See `summary.tsv` and `runs/*.json`.
