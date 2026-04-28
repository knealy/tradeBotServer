# Backtesting

## Entrypoint

- **`python core/backtest_executor.py`** — primary offline runner.
- Set **`ENABLE_SIGNALR=false`** so the process does not open live SignalR (see `.env.example`).

## Batch / export

- Scripts such as [scripts/batch_backtest.sh](../scripts/batch_backtest.sh) / [scripts/batch_backtest.py](../scripts/batch_backtest.py) (if present in your tree).
- Results persistence is described in [HANDOFF.md](HANDOFF.md) / [DATABASE.md](DATABASE.md).

## Older docs

- [BACKTEST_ENGINE_GUIDE.md](BACKTEST_ENGINE_GUIDE.md) — supplementary; verify paths against current `core/`.
