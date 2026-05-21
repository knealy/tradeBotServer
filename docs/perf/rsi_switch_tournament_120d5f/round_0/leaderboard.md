# RSI switch tournament — round 0

- **Span:** 120d ending 2026-05-14, **5** folds
- **Symbols:** MNQ, MES, MGC

## Fitness ranking

| rank | candidate | fitness | sum_pnl | trades | win_rate_pct | folds_green | mean_sharpe | label |
|---:|---|---:|---:|---:|---:|---:|---:|---|
| 1 | `tight_30_70` | 38.19 | 130.55 | 19 | 42.11 | 5/15 | 7.687 | Deeper extremes 30/70, hook buffer 2 |
| 2 | `mid_32_68` | 25.53 | 24.63 | 36 | 38.89 | 5/15 | -11.173 | Mid band 32/68 |
| 3 | `ema_strict` | 23.52 | -309.58 | 77 | 48.05 | 7/15 | -91.872 | Tighter EMA touch 0.35 ATR |
| 4 | `loss_tight` | 18.76 | -670.06 | 85 | 42.35 | 8/15 | -10.506 | Tighter loss cut 0.55 ATR |
| 5 | `baseline_hook` | 18.32 | -780.42 | 85 | 44.71 | 7/15 | -92.834 | Current TOML defaults (5m hook / 35-65) |
| 6 | `tp_150` | 18.32 | -666.18 | 85 | 44.71 | 7/15 | -92.723 | Wider TP 1.5R |
| 7 | `ema_loose` | 18.32 | -780.42 | 85 | 44.71 | 7/15 | -92.834 | Looser EMA touch 0.55 ATR |
| 8 | `slow_spacing` | 18.32 | -780.42 | 85 | 44.71 | 7/15 | -92.834 | Wider entry spacing (6 bars) |
| 9 | `no_counter_block` | 18.32 | -780.42 | 85 | 44.71 | 7/15 | -92.834 | Allow counter-trend entries |
| 10 | `tight_28_72` | 17.71 | 30.12 | 12 | 50.00 | 4/15 | -0.505 | Sweep winner hint 28/72 |
| 11 | `no_confirm` | 16.79 | -791.17 | 86 | 44.19 | 6/15 | -93.121 | No confirm candle |
| 12 | `tp_100_scalp` | 16.79 | -1626.25 | 86 | 44.19 | 6/15 | -95.873 | Tight TP 1.0R + earlier profit RSI |
