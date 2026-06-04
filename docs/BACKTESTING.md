# Backtesting

## Where bars come from

| Mode | What you get |
|------|----------------|
| **`--sample`** | **Synthetic** OHLCV: `get_sample_data()` builds a random walk in RAM — no CSV, not real prices. Good for wiring and quick iteration. |
| **No `--sample`, no `--csv`** | **TopStepX REST**: executor authenticates and loads history through the adapter (same family of calls as live). Needs API credentials. |
| **`--csv path`** | **File**: usually produced by `python scripts/export_history.py` or `bash scripts/fetch_history_csv.sh` (wrapper). |

For **ad-hoc TopStepX 1m exports** (e.g. ``MNQ_1m_20260514_132946.csv`` at repo root), replay ``morning_range_reversion`` and build per-trade LWC pages in one step:

```bash
ENABLE_SIGNALR=false .venv/bin/python scripts/replay_morning_reversion_trade_charts_from_1m_csv.py \\
  --csv MNQ_1m_20260514_132946.csv \\
  --out-dir docs/perf/morning_reversion_mnq_export_review
```

Use ``--full-span`` for the file’s entire calendar range (slow on ~20k bars). After a long run, regenerate charts only with ``--reuse-json``.

### TopStepX broker tail → canonical `*_5m_databento.csv`

To **extend** the repo’s Databento-resampled 5m files with the **latest** bars from the **same** TopStepX REST history used by the CLI (`history <sym> 5m … csv` / `export_history.py`), run:

```bash
ENABLE_SIGNALR=false .venv/bin/python scripts/stitch_broker_history_to_databento_5m.py --dry-run
.venv/bin/python scripts/stitch_broker_history_to_databento_5m.py
```

Requires `PROJECT_X_API_KEY` / `PROJECT_X_USERNAME`. Merges via `historical_data/csv_merger.py` with **canonical first, broker tail second** (duplicate timestamps keep the **newer** row).

### TopStepX broker tail → canonical `*_1m_databento.csv`

Same adapter pathway as ``history <sym> 1m … csv``, chunked to stay under the ~20k bar cap:

```bash
ENABLE_SIGNALR=false .venv/bin/python scripts/stitch_broker_history_to_databento_1m.py --symbols MGC --dry-run
.venv/bin/python scripts/stitch_broker_history_to_databento_1m.py
```

For **manual** merges from a broker export file, pass inputs to `csv_merger.py` in the same order (**canonical first, newest export last**) so overlapping minutes keep the fresh pull.

**Databento batches:** use `scripts/databento_stitch_canonical.py` for large GLBX drops; use the stitch scripts above for regular TopStepX tails.

### Databento GLBX batch (`split_symbols` → many CSVs)

If you downloaded **GLBX.MDP3** `ohlcv-1m` with **separate files per instrument**, you get one CSV per outright or per roll-range (filename suffix like `MNQM5` vs `MNQM5-MNQU5`). For a **single continuous outright series** per root (`MNQ`, `MES`, `MGC`):

```bash
python scripts/merge_databento_glbx_batch.py historical_data/price/GLBX-<job_id>
```

This writes `historical_data/price/{MNQ,MES,MGC}_1m_databento_<job_id>.csv` (hyphenated roll-aggregate files are **skipped** by default because their price scale is not the outright index). Optional calendar clip: `--start YYYY-MM-DD --end YYYY-MM-DD`. To **append** with a TopStepX export on overlapping dates, list files **archive first, newest last** and run [`historical_data/csv_merger.py`](../historical_data/csv_merger.py) (later file wins on duplicate timestamps).

### Canonical Databento paths (MNQ / MES / MGC) — merge + stitch

For backtests and scripts that expect **stable paths** (no job-id suffix), keep these files up to date whenever you drop a new `GLBX-*` batch under `historical_data/price/`:

| File | Role |
|------|------|
| `historical_data/price/{MNQ,MES,MGC}_1m_databento.csv` | **Canonical** 1m outright series (naive UTC). |
| `historical_data/price/{MNQ,MES,MGC}_5m_databento.csv` | Optional 5m resample from the canonical 1m (see [`historical_data/resample_ohlcv_csv.py`](../historical_data/resample_ohlcv_csv.py)). |

**One-shot stitch** (merges the batch, then **stitches** into the canonical 1m so **newer timestamps win** on overlap; by default also refreshes the 5m files):

```bash
# Newest GLBX-* under historical_data/price/ (or set DATABENTO_BATCH_DIR, or pass --batch-dir)
.venv/bin/python scripts/databento_stitch_canonical.py
```

```bash
# Explicit batch; skip 5m if you only need 1m (faster)
.venv/bin/python scripts/databento_stitch_canonical.py --batch-dir historical_data/price/GLBX-<job_id> --skip-5m
```

This script **does not download** from Databento; fetch via their portal/CLI/job, unzip into `historical_data/price/`, then run the stitcher. **Cron (weekly example):** after each fetch, `cd` to the repo and run the same command; optional `DATABENTO_BATCH_DIR=/abs/path/to/GLBX-…` if multiple batch folders exist and the lexicographic “newest” name is not the one you want.

**Master GUI chart:** the aiohttp route **`GET /api/chart/reload`** can read the same canonical files (`source=auto|databento`, optional env **`CHART_RELOAD_SOURCE`**) so the TradingView-style chart in **`gui/master_control.html`** can show long local history for MNQ/MES/MGC without relying on broker bar depth. See [`gui/README.md`](../gui/README.md).

Shell helpers: [scripts/backtest_symbol.sh](../scripts/backtest_symbol.sh), [scripts/backtest_thorough_symbol.sh](../scripts/backtest_thorough_symbol.sh), [scripts/fetch_history_csv.sh](../scripts/fetch_history_csv.sh).

## Speed — parallel matrices, CSV cache, bar conversion

- **Parallel job matrix:** [`scripts/run_backtest_manifest.py`](../scripts/run_backtest_manifest.py) runs many `core/backtest_executor.py` replay lines from a **JSONL manifest** (example: [`config/backtest_matrices/example_body_reversion_q1.jsonl`](../config/backtest_matrices/example_body_reversion_q1.jsonl)). Set **`BACKTEST_MANIFEST_JOBS`** or **`--jobs`** for concurrency. Pair with [`scripts/print_weekly_income.py`](../scripts/print_weekly_income.py) when manifests use **`"include_trades": true`**.
- **Gate A/B/C grid (`body_reversion`):** [`scripts/resume_body_rev_gate_ab_full.sh`](../scripts/resume_body_rev_gate_ab_full.sh) → [`scripts/run_body_rev_gate_ab_parallel.py`](../scripts/run_body_rev_gate_ab_parallel.py) (default **3** workers).
- **In-process CSV LRU:** `HistoricalDataLoader.load_from_csv` caches normalized frames by **(path, mtime)** — speeds **`python -m core.research.runner`** and any loop re-reading the same file. Env: **`BACKTEST_CSV_CACHE`**, **`BACKTEST_CSV_CACHE_SIZE`**. Introspection: `csv_cache_stats()` / `clear_backtest_csv_cache()` in [`core/backtest/data_loader.py`](../core/backtest/data_loader.py).
- **Replay prep:** [`core/backtest/ohlcv.py`](../core/backtest/ohlcv.py) **`replay_bars_from_ohlcv_df`** builds bar dicts with a numpy scan (used from **`core/backtest_executor.py`** instead of `iterrows`).

## Timestamps (CSV vs Eastern sessions)

Exported CSV timestamps are usually **naive wall times that match UTC** (no `+00:00` in the cell). In replay and in strategies such as **`overnight_range`**, a naive bar time is treated as **UTC**, then converted with **`US/Eastern`** (see `timing.session_timezone` in `config/strategies/overnight_range.toml`) for session boundaries (e.g. 6:00 PM overnight start, 9:30 AM cash open, 9:29 scan). **DST is handled by that zone**, not by a fixed −5 offset.

So backtests and replay are **consistent with live** as long as each bar’s clock instant really is UTC (normal for TopStepX exports and for files normalized by `historical_data/csv_merger.py`). If a file were mislabeled (e.g. Eastern values saved without a zone and read as UTC), session windows would shift by several hours.

### `overnight_range` replay — trade count vs filters

Replay places at most **one** breakout attempt per symbol per **session** (on/after `timing.market_open`), then **`[filters]`** (with **`[symbols.<SYM>.filters]`** overrides for MES/MGC point scales) and **`skip_weekdays`** remove sessions before orders exist. **`timing.replay_order_window_minutes`** caps how many minutes after open CSV replay considers bars (**`0` = no minute cap after open, still never before open**). A handful of trades over multi‑month CSV windows can still be normal when filters are tight — see [OVERNIGHT_RANGE_RESEARCH.md](OVERNIGHT_RANGE_RESEARCH.md) §0 and `config/strategies/overnight_range.toml`.

## Entrypoints

| Tool | When to use |
|------|-------------|
| **`python core/backtest_executor.py`** | Single run: sample, CSV, or API-loaded bars; optional `--optimize`, `--monte-carlo`. |
| **`python -m core.research.runner`** | Grid search + **mandatory OOS** split + **Monte Carlo gate**; optional Postgres row; walk-forward / slippage table flags. See [BACKTEST_RESEARCH.md](BACKTEST_RESEARCH.md). |
| **`scripts/run_backtest_manifest.py`** | Parallel **JSONL** matrix of `backtest_executor` replay jobs (`config/backtest_matrices/*.jsonl`). |
| **`scripts/walkforward_strategy_competition.py`** | Same **N-day** calendar folds (default **100** / **5** folds) across **body_reversion**, **morning_range_reversion**, **overnight_range** on **MNQ/MES/MGC** **5m** CSVs → writes `summary.tsv` + `leaderboard.md` under the run's `--out-dir`. Defaults to `--in-process` + warm-import `ProcessPoolExecutor`; opt out with `--no-in-process`. Run `python scripts/walkforward_strategy_competition.py --help` for the full flag set. |
| **`scripts/strategy_parameter_sweep.py`** | **Wave** sweeps: one JSON manifest (`config/perf_sweep/default_wave_manifest.json`) groups runs that each change a focused knob via **env overrides** (e.g. `OVERNIGHT_RANGE_SIGNAL_STOP_ATR_MULTIPLIER`). Writes `summary.tsv` + `by_wave.md` + `results.jsonl` under `docs/perf/parameter_sweeps/<run-id>/` with **win_rate** and **avg_reward_risk** (mean PnL / initial bracket risk $). Use `--waves id1,id2` to run a subset. |
| **`scripts/pattern_conditional_scan.py`** | Research-only: 1m CSV → 5m NY bars; Fisher + FDR on short-horizon conditionals + RTH prior-day level re-touch stats → [`docs/alpha/pattern_scan_INDEX.md`](alpha/pattern_scan_INDEX.md). |
| **`scripts/strategy_litmus.py`** | Fast **preset** checks on a **short tail** of CSV (default `--last-days 90`); e.g. `morning_range` runs the same sieve as `validate_morning_range_reversion.py` without loading the full history. Optional **`--1m-csv`** aligns with **`scripts/validate_morning_range_reversion.py --1m-csv`** for **1m-resolved** TP vs SL inside a 5m bar. `--why` explains common gaps vs vendor headlines. |
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

- [archive/BACKTEST_ENGINE_GUIDE.md](archive/BACKTEST_ENGINE_GUIDE.md) — supplementary; verify paths against current `core/`.
