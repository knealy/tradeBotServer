# RSI switch survival tournament — final

- **Champion:** `g1_x5_mid_32_68_ema_strict` — cross(mid_32_68,ema_strict)
- **Fitness:** 39.52
- **Metrics:** PnL 226.25, 32 trades, WR 43.75%, green folds 7/15

## Champion env overrides

```
RSI_SWITCH_15M_SIGNAL_REACTION_TOL_ATR=0.35
RSI_SWITCH_15M_SIGNAL_RSI_DEEP_EXTRA=2
RSI_SWITCH_15M_SIGNAL_RSI_HOOK_BUFFER=3
RSI_SWITCH_15M_SIGNAL_RSI_LONG_MAX=32
RSI_SWITCH_15M_SIGNAL_RSI_SHORT_MIN=68
```

## Lineage by round

| round | champion_id | fitness | win_rate_pct | sum_pnl | trades |
|---:|---|---:|---:|---:|---:|
| 0 | `tight_30_70` | 38.19 | 42.11 | 130.55 | 19 |
| 1 | `g1_x5_mid_32_68_ema_strict` | 39.52 | 43.75 | 226.25 | 32 |
| 2 | `g1_x5_mid_32_68_ema_strict` | 39.52 | 43.75 | 226.25 | 32 |
| 3 | `g1_x5_mid_32_68_ema_strict` | 39.52 | 43.75 | 226.25 | 32 |

Apply winning keys to `config/strategies/rsi_switch_15m.toml` after review.
