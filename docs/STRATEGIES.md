# Strategies

## How strategies run

- **Registry:** [strategies/strategy_manager.py](../strategies/strategy_manager.py).
- **Base class:** [strategies/strategy_base.py](../strategies/strategy_base.py).
- **Config:** `config/strategies/<strategy>.toml` + [core/strategy_config.py](../core/strategy_config.py); schema hints in `config/strategies/_schema.toml`.

## Adding a strategy

1. Implement a subclass of `BaseStrategy` under `strategies/`.
2. Add TOML under `config/strategies/`.
3. Register in `strategy_manager.py`.
4. Log the change in [CHANGELOG.md](CHANGELOG.md).

Workflow and when to use replay vs research vs live: [STRATEGY_DEVELOPMENT.md](STRATEGY_DEVELOPMENT.md).

## Pine scripts

Reference only (not imported by Python): [strategies/pine/](../strategies/pine/).

## Legacy guides

- [MODULAR_STRATEGY_GUIDE.md](MODULAR_STRATEGY_GUIDE.md) — modular design notes.
- [BACKTEST_ENGINE_GUIDE.md](BACKTEST_ENGINE_GUIDE.md) touches backtest wiring; see also [BACKTESTING.md](BACKTESTING.md).
