# Backtesting

## Where bars come from

| Mode | What you get |
|------|----------------|
| **`--sample`** | **Synthetic** OHLCV: `get_sample_data()` builds a random walk in RAM — no CSV, not real prices. Good for wiring and quick iteration. |
| **No `--sample`, no `--csv`** | **TopStepX REST**: executor authenticates and loads history through the adapter (same family of calls as live). Needs API credentials. |
| **`--csv path`** | **File**: usually produced by `python scripts/export_history.py` or `bash scripts/fetch_history_csv.sh` (wrapper). |

Shell helpers: [scripts/backtest_symbol.sh](../scripts/backtest_symbol.sh), [scripts/backtest_thorough_symbol.sh](../scripts/backtest_thorough_symbol.sh), [scripts/fetch_history_csv.sh](../scripts/fetch_history_csv.sh).

## Timestamps (CSV vs Eastern sessions)

Exported CSV timestamps are usually **naive wall times that match UTC** (no `+00:00` in the cell). In replay and in strategies such as **`overnight_range`**, a naive bar time is treated as **UTC**, then converted with **`US/Eastern`** (see `timing.session_timezone` in `config/strategies/overnight_range.toml`) for session boundaries (e.g. 6:00 PM overnight start, 9:30 AM cash open, 9:29 scan). **DST is handled by that zone**, not by a fixed −5 offset.

So backtests and replay are **consistent with live** as long as each bar’s clock instant really is UTC (normal for TopStepX exports and for files normalized by `historical_data/csv_merger.py`). If a file were mislabeled (e.g. Eastern values saved without a zone and read as UTC), session windows would shift by several hours.

### `overnight_range` replay — expect a small trade count

Replay places at most **one** breakout attempt per symbol per **session** (near `timing.market_open`), then **`[filters]`** and **`skip_weekdays`** remove most sessions before orders exist. A handful of trades over multi‑month CSV windows is often **by design**, not a broken backtest. To interpret frequency and relax knobs, see [OVERNIGHT_RANGE_RESEARCH.md](OVERNIGHT_RANGE_RESEARCH.md) §0 and `config/strategies/overnight_range.toml` comments under `[filters]`.

## Entrypoints

| Tool | When to use |
|------|-------------|
| **`python core/backtest_executor.py`** | Single run: sample, CSV, or API-loaded bars; optional `--optimize`, `--monte-carlo`. |
| **`python -m core.research.runner`** | Grid search + **mandatory OOS** split + **Monte Carlo gate**; optional Postgres row; walk-forward / slippage table flags. See [BACKTEST_RESEARCH.md](BACKTEST_RESEARCH.md). |
| **`scripts/batch_backtest.py`** | Scripted batch comparisons (legacy suite style). |

Set **`ENABLE_SIGNALR=false`** so the process does not open live SignalR when you only need REST or offline data (see `.env.example`).

## Using the backtest executor

```bash
# Names you can pass to --strategy
python core/backtest_executor.py --list-strategies

# Fast check on synthetic data (function strategy = DataFrame path)
python core/backtest_executor.py --strategy=ma_crossover --symbol=MNQ --sample --days=14

# Machine-readable output (one JSON object on stdout; no banner text)
python core/backtest_executor.py --strategy=ma_crossover --symbol=MNQ --sample --days=14 --format=json

# Optional MC on the same run (included inside JSON when --format=json)
python core/backtest_executor.py --strategy=ma_crossover --symbol=MNQ --sample --days=14 --format=json --monte-carlo=200

# Class strategy = replay path (needs enough bars; often pair with --sample + dates)
python core/backtest_executor.py --strategy=overnight_range --symbol=MNQ --replay --sample \
  --start=2025-12-01 --end=2025-12-14 --days=14
```

### Flags worth remembering

- **`--format=json`** — Suppresses decorative prints; prints `{"ok": true, "cache_key": "...", "result": {...}, "monte_carlo": {...}}` or `{"ok": false, "error": "..."}`.
- **`--replay`** — Required for **class-based** strategies in the list from `--list-strategies`.
- **`--csv PATH`** — Exported history; function strategies keep a DataFrame; replay strategies use bar dicts.

## Using the research runner (grid + OOS + MC)

```bash
# Defaults: 14d, 5m sample, ma_crossover, no DB writes
python -m core.research.runner --no-db

# Custom grid + thresholds + config hash in metadata
python -m core.research.runner \
  --grid "fast_period=10:14:2,slow_period=40:80:20" \
  --days 30 \
  --mc 300 \
  --toml config/strategies/mean_reversion.toml \
  --run-tag experiment_apr28

# Screening stage (short window) — see scripts/research_screen.sh
SCREEN_DAYS=7 FULL_DAYS=60 bash scripts/research_screen.sh --no-db
```

## Using live perf features (transparent to you)

These do not change CLI usage; they reduce REST load and log noise in production:

- **`get_positions_and_orders_batch`** — Used by websocket welcome/heartbeat, webhook test handlers, bracket adjustment, and parts of `trading_bot` / strategies where both orders and positions are needed.
- **Contract refresh single-flight** — `get_available_contracts` on bot + adapter serializes concurrent refreshes after cache miss.

Details: [perf/OPERATIONS_TUNING.md](perf/OPERATIONS_TUNING.md), [PLAYBOOK.md](PLAYBOOK.md) (uvloop smoke).

## Strategy development workflow

See **[STRATEGY_DEVELOPMENT.md](STRATEGY_DEVELOPMENT.md)** for the full decision tree (sample vs API, replay vs function, when to use research runner, ship checklist).

## Batch / export / persistence

- Batch grid (older style): [scripts/batch_backtest.sh](../scripts/batch_backtest.sh) / [scripts/batch_backtest.py](../scripts/batch_backtest.py).
- Results persistence: [HANDOFF.md](HANDOFF.md) / [DATABASE.md](DATABASE.md); research promoted runs use `strategy_performance.metadata` JSONB.

## Older docs

- [BACKTEST_ENGINE_GUIDE.md](BACKTEST_ENGINE_GUIDE.md) — supplementary; verify paths against current `core/`.
