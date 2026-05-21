# RSI switch tournament — round 1

- **Span:** 120d ending 2026-05-14, **5** folds
- **Symbols:** MNQ, MES, MGC

## Fitness ranking

| rank | candidate | fitness | sum_pnl | trades | win_rate_pct | folds_green | mean_sharpe | label |
|---:|---|---:|---:|---:|---:|---:|---:|---|
| 1 | `g1_x5_mid_32_68_ema_strict` | 39.52 | 226.25 | 32 | 43.75 | 7/15 | -10.760 | cross(mid_32_68,ema_strict) |
| 2 | `tight_30_70` | 38.19 | 130.55 | 19 | 42.11 | 5/15 | 7.687 | Deeper extremes 30/70, hook buffer 2 |
| 3 | `mid_32_68` | 25.53 | 24.63 | 36 | 38.89 | 5/15 | -11.173 | Mid band 32/68 |
| 4 | `g1_x2_mid_32_68_baseline_hook` | 25.53 | 24.63 | 36 | 38.89 | 5/15 | -11.173 | cross(mid_32_68,baseline_hook) |
| 5 | `ema_strict` | 23.52 | -309.58 | 77 | 48.05 | 7/15 | -91.872 | Tighter EMA touch 0.35 ATR |
| 6 | `g1_x3_ema_strict_baseline_hook` | 23.52 | -309.58 | 77 | 48.05 | 7/15 | -91.872 | cross(ema_strict,baseline_hook) |
| 7 | `g1_m4_loss_tight` | 20.59 | -442.77 | 85 | 42.35 | 8/15 | -10.445 | mutate(loss_tight) |
| 8 | `loss_tight` | 18.76 | -670.06 | 85 | 42.35 | 8/15 | -10.506 | Tighter loss cut 0.55 ATR |
| 9 | `baseline_hook` | 18.32 | -780.42 | 85 | 44.71 | 7/15 | -92.834 | Current TOML defaults (5m hook / 35-65) |
| 10 | `tp_150` | 18.32 | -666.18 | 85 | 44.71 | 7/15 | -92.723 | Wider TP 1.5R |
| 11 | `g1_m1_baseline_hook` | 16.79 | -791.17 | 86 | 44.19 | 6/15 | -93.121 | mutate(baseline_hook) |
| 12 | `g1_m0_tight_30_70` | 13.17 | -228.86 | 24 | 37.50 | 6/15 | -8.768 | mutate(tight_30_70) |
