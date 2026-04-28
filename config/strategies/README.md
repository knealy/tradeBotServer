# Per-strategy configuration

Strategy parameters live here so the root `.env` can stay slim and focused
on secrets + infrastructure.

## Files

- [`_schema.toml`](_schema.toml) — fully documented template. Copy when
  adding a new strategy.
- `<strategy_name>.toml` — one per strategy
  (`overnight_range.toml`, `mean_reversion.toml`, …). Loaded automatically
  by `core.strategy_config.load_strategy_config(name)`.

## Precedence (highest to lowest)

1. **CLI override** — passed as a `dict` to `load_strategy_config(..., cli_overrides=...)`
2. **Environment variable** — e.g. `OVERNIGHT_RANGE_RISK_POSITION_SIZE` or the
   legacy unprefixed name (`STOP_ATR_MULTIPLIER`)
3. **TOML value** in this directory
4. **Strategy class default** (the fallback passed to `cfg.get(..., default=...)`)

## Hot reload

`StrategyConfig.maybe_reload()` re-parses the TOML when its `mtime` changes.
Strategies should call it once per loop iteration. With `--reload` set on
the executor, edits here propagate without restart.

## Per-symbol overrides

```toml
[symbols.MNQ]
position_size = 3
```

Looked up via `cfg.symbol_override("MNQ", "risk.position_size")`. Falls back
to the global key if no override exists.

## Legacy env compatibility

The loader accepts both prefixed (`OVERNIGHT_RANGE_*`) and unprefixed
(`OVERNIGHT_START_TIME`) names so existing `.env` files keep working during
the migration. New code should write to the TOML file directly.
