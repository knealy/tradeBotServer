# Environment variables

## Source of truth

- **Template:** [.env.example](../.env.example) — list vars actually used by the codebase; copy to `.env` locally.
- **Loader:** [load_env.py](../load_env.py) (if used by your entrypoint).

## Rules

- **Secrets:** `PROJECT_X_*` / `TOPSTEPX_*` / legacy typo `TOPSETPX_*` for broker auth (see [GOTCHAS.md](GOTCHAS.md)).
- **Strategy parameters:** prefer `config/strategies/<name>.toml` and [core/strategy_config.py](../core/strategy_config.py), not new `os.getenv` in `strategies/`.

## Deep dive

- [reference/ENV_CONFIGURATION.md](reference/ENV_CONFIGURATION.md) — older consolidated env doc; cross-check names against `.env.example`.
