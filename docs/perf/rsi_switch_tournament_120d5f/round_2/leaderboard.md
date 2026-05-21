# RSI switch tournament — round 2

- **Span:** 120d ending 2026-05-14, **5** folds
- **Symbols:** MNQ, MES, MGC

## Fitness ranking

| rank | candidate | fitness | sum_pnl | trades | win_rate_pct | folds_green | mean_sharpe | label |
|---:|---|---:|---:|---:|---:|---:|---:|---|
| 1 | `g1_x5_mid_32_68_ema_strict` | 39.52 | 226.25 | 32 | 43.75 | 7/15 | -10.760 | cross(mid_32_68,ema_strict) |
| 2 | `g2_m0_g1_x5_mid_32_68_ema_strict` | 39.52 | 268.68 | 32 | 43.75 | 7/15 | -10.696 | mutate(g1_x5_mid_32_68_ema_strict) |
| 3 | `tight_30_70` | 38.19 | 130.55 | 19 | 42.11 | 5/15 | 7.687 | Deeper extremes 30/70, hook buffer 2 |
| 4 | `g2_m4_g1_x3_ema_strict_baseline_hook` | 29.75 | -246.13 | 90 | 48.89 | 8/15 | 4.645 | mutate(g1_x3_ema_strict_baseline_hook) |
| 5 | `mid_32_68` | 25.53 | 24.63 | 36 | 38.89 | 5/15 | -11.173 | Mid band 32/68 |
| 6 | `g1_x2_mid_32_68_baseline_hook` | 25.53 | 24.63 | 36 | 38.89 | 5/15 | -11.173 | cross(mid_32_68,baseline_hook) |
| 7 | `ema_strict` | 23.52 | -309.58 | 77 | 48.05 | 7/15 | -91.872 | Tighter EMA touch 0.35 ATR |
| 8 | `g1_x3_ema_strict_baseline_hook` | 23.52 | -309.58 | 77 | 48.05 | 7/15 | -91.872 | cross(ema_strict,baseline_hook) |
| 9 | `g2_m2_g1_x3_ema_strict_baseline_hook` | 23.52 | -309.58 | 77 | 48.05 | 7/15 | -91.872 | mutate(g1_x3_ema_strict_baseline_hook) |
| 10 | `g2_m5_ema_strict` | 21.52 | -418.11 | 111 | 48.65 | 5/15 | -0.899 | mutate(ema_strict) |
| 11 | `g2_m3_g1_x5_mid_32_68_ema_strict` | 17.60 | -208.44 | 40 | 45.00 | 5/15 | -6.573 | mutate(g1_x5_mid_32_68_ema_strict) |
| 12 | `g2_m1_g1_x2_mid_32_68_baseline_hook` | 12.53 | -431.65 | 55 | 40.00 | 4/15 | -2.648 | mutate(g1_x2_mid_32_68_baseline_hook) |
