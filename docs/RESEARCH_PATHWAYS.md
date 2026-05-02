# Research pathways — overnight_range tuning + new strategies

Operator map for (1) systematically increasing **`overnight_range`** sample size while controlling risk and (2) exploring **additional** strategies on the same historical CSVs for weekly-style cashflow hypotheses.

Also read: [OVERNIGHT_RANGE_RESEARCH.md](OVERNIGHT_RANGE_RESEARCH.md) §0 (trade frequency), [ALPHA_DISCOVERY.md](ALPHA_DISCOVERY.md), [BACKTESTING.md](BACKTESTING.md), [BACKTEST_RESEARCH.md](BACKTEST_RESEARCH.md).

---

## A. Confirming full date coverage (no hidden “training window” cap)

### Single CSV replay (`core/backtest_executor.py`)

1. **Load** — `HistoricalDataLoader.load_from_csv` reads the whole file into a DataFrame.
2. **Filter** — If you pass `--start` / `--end`, bars are kept with  
   `index >= start` and `index < end + 1 day` (inclusive calendar end day).  
   There is **no** extra row cap on the CSV replay path (unlike API mode’s `limit=20000` per request).
3. **Replay** — `StrategyReplayEngine` walks **every** bar in that filtered list in order.

So for `--csv=… --start=2025-12-23 --end=2026-04-23`, you **are** simulating over the full slice of minutes present in the file for that range. Low trade count comes from **strategy + TOML** (session model, filters, `skip_weekdays`), not from the loader dropping tail rows.

### Mock `get_historical_data` (`limit`)

When the strategy calls `get_historical_data(..., limit=N)`, the mock may return only the last **N** bars **up to the current replay bar** (no look-ahead). That limits **context** for indicators, not the replay calendar length.

### Chunk batch file (`docs/perf/overnight_range_MNQ_1m_chunks.json`)

[`scripts/run_overnight_range_csv_chunks.py`](../scripts/run_overnight_range_csv_chunks.py) walks **`csv_first_date` → `csv_last_date`** from the CSV’s actual min/max timestamps and runs **non‑overlapping** calendar chunks (e.g. 30 days). Each chunk is a **separate** subprocess with **fresh** initial capital — `rollup.sum_total_trades` is the **sum** of per‑chunk trade counts, **not** the same as one continuous run (pending orders / equity do not carry across chunk boundaries).

**Important:** If that JSON was produced **before** the overnight range stale‑cache + opposite‑entry‑cancel fixes, its trade list and PnL are **not** comparable to current `response.json`. Regenerate after code changes:

```bash
.venv/bin/python scripts/run_overnight_range_csv_chunks.py \
  --csv historical_data/price/MNQ_1m_complete.csv --chunk-days 30 --no-print-trades
```

**Ground truth for “one capital path over the whole range”** — one executor invocation with your desired `--start` / `--end` (same as [response.json](../response.json) style).

---

## B. More symbols, same calendar window

Exports use [`scripts/fetch_history_csv.sh`](../scripts/fetch_history_csv.sh) → `scripts/export_history.py` (API key required). Example — **same** `--start` / `--end` / `--chunk-days` for each symbol:

```bash
export START=2025-12-23 END=2026-04-23 CHUNK=30 TF=1m
for sym in MNQ MES MGC; do
  bash scripts/fetch_history_csv.sh --symbol "$sym" --timeframe "$TF" \
    --start "$START" --end "$END" --chunk-days "$CHUNK" \
    --output "historical_data/price/${sym}_${TF}_${START}_${END}.csv"
done
```

Wrapper (repo root, same defaults): [`scripts/export_history_parallel_range.sh`](../scripts/export_history_parallel_range.sh).

Then replay (example MES):

```bash
.venv/bin/python core/backtest_executor.py --strategy=overnight_range --symbol=MES \
  --timeframe=1m --csv=historical_data/price/MES_1m_2025-12-23_2026-04-23.csv \
  --start="$START" --end="$END" --format=json --include-trades > docs/perf/response_MES.json
```

`overnight_range` TOML lists `MNQ`, `MES`, `MGC`; point size / filters may need per‑symbol overrides under `[symbols.MES]` etc. after you have data.

---

## Pathway 1 — `overnight_range`: systematic filter / cadence research

**Goal:** More completed trades **without** fooling yourself — change one axis at a time, record results under `docs/perf/` (dated JSON + one‑line note).

Suggested **order** (largest impact on sample size first):

| Step | Change | What to log |
|------|--------|----------------|
| P1 | `skip_weekdays = []` | Trade count, total PnL, max DD, win rate |
| P2 | Set **`volatility = false`** only | Same |
| P3 | Set **`gap = false`** only (restore vol if needed) | Same |
| P4 | Set **`range_size = false`** only | Same |
| P5 | Widen `range_min_pts` / `range_max_pts`, `gap_max_pts`, `atr_min` / `atr_max` with filters still on | Same |

After each change:

```bash
.venv/bin/python core/backtest_executor.py --strategy=overnight_range --symbol=MNQ --timeframe=1m \
  --csv=historical_data/price/MNQ_1m_complete.csv --start=2025-12-23 --end=2026-04-23 \
  --format=json --include-trades 2>/dev/null | tee docs/perf/overnight_range_stepP1.json
```

Session‑level stats (features, OOS by **session**): [`scripts/alpha_discovery.py`](../scripts/alpha_discovery.py) on **5m** resampled CSV — see [ALPHA_DISCOVERY.md](ALPHA_DISCOVERY.md).

Knobs file: [`config/strategies/overnight_range.toml`](../config/strategies/overnight_range.toml) (comments under `[filters]`).

---

## Pathway 2 — New strategies for “weekly cashflow” hypotheses

Same CSV contract: **1m (or 5m) OHLCV**, same `--start` / `--end`, then:

| Direction | Strategy config | Backtest entry |
|-----------|-------------------|----------------|
| Intraday / RTH | [`config/strategies/simple_rth.toml`](../config/strategies/simple_rth.toml) | `python core/backtest_executor.py --strategy=simple_rth …` |
| Mean reversion | `mean_reversion.toml` | `--strategy=mean_reversion` |
| Trend | `trend_following.toml`, `trend_scalping.toml` | matching `--strategy=` |
| Research grid + OOS + MC | — | `python -m core.research.runner --help` (CSV flags in [BACKTEST_RESEARCH.md](BACKTEST_RESEARCH.md)) |

Register new names in [`strategies/strategy_manager.py`](../strategies/strategy_manager.py) + add `config/strategies/<name>.toml` per [AGENTS.md](../AGENTS.md).

**Reality check:** Higher trade frequency usually **lowers** edge per trade; validate with **OOS** and **session** splits, not raw in‑sample win rate on 10 trades.

---

## Artifacts folder

Save dated JSON / notes under [`docs/perf/`](.) (see [README.md](README.md)). Regenerate `overnight_range_*_chunks.json` when strategy or replay logic changes so chunk rollups stay comparable.
