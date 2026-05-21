# RSI switch tournament — round 3

- **Span:** 120d ending 2026-05-14, **5** folds
- **Symbols:** MNQ, MES, MGC

## Fitness ranking

| rank | candidate | fitness | sum_pnl | trades | win_rate_pct | folds_green | mean_sharpe | label |
|---:|---|---:|---:|---:|---:|---:|---:|---|
| 1 | `g1_x5_mid_32_68_ema_strict` | 39.52 | 226.25 | 32 | 43.75 | 7/15 | -10.760 | cross(mid_32_68,ema_strict) |
| 2 | `g2_m0_g1_x5_mid_32_68_ema_strict` | 39.52 | 268.68 | 32 | 43.75 | 7/15 | -10.696 | mutate(g1_x5_mid_32_68_ema_strict) |
| 3 | `g3_m3_g2_m0_g1_x5_mid_32_68_ema_strict` | 39.52 | 268.68 | 32 | 43.75 | 7/15 | -10.696 | mutate(g2_m0_g1_x5_mid_32_68_ema_strict) |
| 4 | `g3_x5_g1_x5_mid_32_68_ema_strict_mid_32_68` | 39.52 | 226.25 | 32 | 43.75 | 7/15 | -10.760 | cross(g1_x5_mid_32_68_ema_strict,mid_32_68) |
| 5 | `tight_30_70` | 38.19 | 130.55 | 19 | 42.11 | 5/15 | 7.687 | Deeper extremes 30/70, hook buffer 2 |
| 6 | `g3_x1_g1_x5_mid_32_68_ema_strict_tight_30_70` | 37.70 | 190.59 | 20 | 45.00 | 6/15 | -6.923 | cross(g1_x5_mid_32_68_ema_strict,tight_30_70) |
| 7 | `g3_x2_tight_30_70_g2_m0_g1_x5_mid_32_68_ema_strict` | 34.74 | 154.52 | 25 | 40.00 | 5/15 | -7.391 | cross(tight_30_70,g2_m0_g1_x5_mid_32_68_ema_strict) |
| 8 | `g2_m4_g1_x3_ema_strict_baseline_hook` | 29.75 | -246.13 | 90 | 48.89 | 8/15 | 4.645 | mutate(g1_x3_ema_strict_baseline_hook) |
| 9 | `mid_32_68` | 25.53 | 24.63 | 36 | 38.89 | 5/15 | -11.173 | Mid band 32/68 |
| 10 | `g1_x2_mid_32_68_baseline_hook` | 25.53 | 24.63 | 36 | 38.89 | 5/15 | -11.173 | cross(mid_32_68,baseline_hook) |
| 11 | `g3_m4_g2_m4_g1_x3_ema_strict_baseline_hook` | 24.29 | -519.52 | 90 | 48.89 | 8/15 | 4.412 | mutate(g2_m4_g1_x3_ema_strict_baseline_hook) |
| 12 | `g3_m0_mid_32_68` | 22.50 | -1.62 | 34 | 38.24 | 4/15 | -7.914 | mutate(mid_32_68) |
