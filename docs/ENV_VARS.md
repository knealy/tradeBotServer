# Environment variables

## Source of truth

- **Template:** [.env.example](../.env.example) — list vars actually used by the codebase; copy to `.env` locally.
- **Prune:** [scripts/slim_env.py](../scripts/slim_env.py) rewrites `.env` to an allowlisted infra set (strategy parameters belong in `config/strategies/*.toml`).
- **Loader:** [load_env.py](../load_env.py) (if used by your entrypoint).

## Rules

- **Secrets:** `PROJECT_X_*` / `TOPSTEPX_*` / legacy typo `TOPSETPX_*` for broker auth (see [GOTCHAS.md](GOTCHAS.md)).
- **Strategy parameters:** prefer `config/strategies/<name>.toml` and [core/strategy_config.py](../core/strategy_config.py), not new `os.getenv` in `strategies/`.

## Slim env vs strategy TOML

[scripts/slim_env.py](../scripts/slim_env.py) keeps only infra keys in `.env`. Strategy timing, risk, signal, filters, and similar knobs belong in `config/strategies/<name>.toml`. Optional env overrides still work at runtime (see precedence in [config/strategies/_schema.toml](../config/strategies/_schema.toml)), but they are **dropped** from `.env` when you run `slim_env.py` unless the key is in the allowlist.

- **`STRATEGY_CONFIG_RELOAD`** — allowlisted so you can force hot-reload polling from `.env`; [core/strategy_executor.py](../core/strategy_executor.py) may also set it when `--reload` is used.
- **`HUB_DEFERRED_QUEUE_MAX`** — max size of the bounded User Hub deferred work queue ([core/hub_deferred_queue.py](../core/hub_deferred_queue.py)); default 128.

Regression checks: [tests/test_strategy_toml_audit.py](../tests/test_strategy_toml_audit.py) asserts each shipped strategy TOML parses and includes `[meta].symbols`; overnight range includes `timing`, `signal`, `risk`, and `position_management` sections aligned with the schema comments.

## Deep dive

- [reference/ENV_CONFIGURATION.md](reference/ENV_CONFIGURATION.md) — older consolidated env doc; cross-check names against `.env.example`.
