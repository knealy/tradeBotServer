# Backtest research layer

Orchestration lives in [`core/research/runner.py`](../core/research/runner.py) on top of [`core/backtest`](../core/backtest/) and [`BacktestExecutor`](../core/backtest_executor.py). It does **not** replace the backtest engine.

## What it does (MVP)

- **Parameter grid:** CLI `--grid key=min:max:step,...` (comma-separated). Integer strategy params are coerced after evaluation.
- **Mandatory OOS:** Sample bars are split by index: first `(1 - oos_fraction)` in-sample, remainder out-of-sample. Fails if OOS has fewer than `--min-oos-bars`.
- **Monte Carlo gate:** After each combo, MC runs on **OOS** trades only. Promotion defaults: `probability_of_profit >= 0.45` and `mean_max_drawdown <= 35%` (tunable).
- **Persistence:** When the gate passes and `--no-db` is not set, one row is written via [`save_strategy_metrics`](../infrastructure/database.py) with strategy name `{strategy}_research`. Extra fields (`git_sha`, `toml_hash`, `run_tag`, `is_oos`, `grid_id`, `mc_summary`, `walk_forward_folds`, `slippage_sensitivity_is`, `slippage_sensitivity_oos`) are stored in the `metadata` JSONB column (keys stripped from scalar columns).

## v2 additions

- **Walk-forward:** `--walk-forward N` runs `N` contiguous folds over the same sample window (train/test Sharpe per fold; first grid combo’s params).
- **Slippage sensitivity:** `--slippage-sensitivity 0.25,0.5,1.0` runs extra in-sample backtests at those tick values for the **first** grid combination only; results appear as `slippage_sensitivity_is` and `slippage_sensitivity_oos` in the JSON output (plus per-row `oos_slippage_break_even_ticks` where computed).
- **Walk-forward:** `walk_forward_by_combo` holds rolling IS→OOS fold summaries per grid row when `--walk-forward` > 1.
- **Monte Carlo:** `--mc-mode shuffle|bootstrap` — `bootstrap` resamples trade P&Ls with replacement (parametric bootstrap).
- **Screening script:** [`scripts/research_screen.sh`](../scripts/research_screen.sh) — short `--screen-days` stage; set `FULL_DAYS` / `GRID` / `MC` env vars as needed.

## Commands

```bash
# Default: 14 days of 5m sample data, ma_crossover, no grid
python3 -m core.research.runner --no-db

# Grid + loose MC thresholds for exploration
python3 -m core.research.runner \
  --grid "fast_period=8:12:2,slow_period=40:60:20" \
  --days 30 \
  --mc 200 \
  --mc-min-profit-prob 0.4 \
  --mc-max-mean-dd 40 \
  --toml config/strategies/mean_reversion.toml \
  --run-tag my_experiment
```

## Live alignment

Use the same symbol and timeframe conventions as live (`StrategyConfig` + bar types). For full fidelity, prefer CSV or API-loaded bars via extending the runner to call `BacktestExecutor.run_backtest` with `--csv` / broker adapter rather than synthetic sample data.

## Tests

```bash
pytest tests/test_research_runner.py -q
```
