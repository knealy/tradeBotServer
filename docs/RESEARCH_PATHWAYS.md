# Research pathways — overnight_range tuning + new strategies

Operator map for (1) systematically increasing **`overnight_range`** sample size while controlling risk and (2) exploring **additional** strategies on the same historical CSVs for weekly-style cashflow hypotheses.

Also read: [OVERNIGHT_RANGE_RESEARCH.md](OVERNIGHT_RANGE_RESEARCH.md) §0 (trade frequency), [ALPHA_DISCOVERY.md](ALPHA_DISCOVERY.md), [BACKTESTING.md](BACKTESTING.md), [BACKTEST_RESEARCH.md](BACKTEST_RESEARCH.md), [LOCAL_LLM_RESEARCH.md](LOCAL_LLM_RESEARCH.md) (Ollama-powered review), [perf/sweeps/CANDIDATES.md](perf/sweeps/CANDIDATES.md) (open hypotheses), [perf/researching.md](perf/researching.md) (upstream library / data index).

Tooling added under this plan:

- [scripts/run_overnight_range_sweep.sh](../scripts/run_overnight_range_sweep.sh) — Pathway 1 P0–P5 filter sweep via env overrides (no TOML edits).
- [scripts/run_overnight_range_oos_wf.sh](../scripts/run_overnight_range_oos_wf.sh) — P0 vs P5 through `core.research.runner` (IS/OOS + MC + optional walk-forward).
- [scripts/run_strategy_matrix.sh](../scripts/run_strategy_matrix.sh) — Pathway 2 strategy × symbol matrix.
- [scripts/walkback_history.sh](../scripts/walkback_history.sh) — chunked walk-back exporter for MES / MGC (and any TopStepX root).
- [scripts/llm_review.py](../scripts/llm_review.py) + [scripts/llm_embed_logs.py](../scripts/llm_embed_logs.py) — research-only Ollama helpers.

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
  --csv historical_data/price/MNQ_1m_complete.csv --chunk-days 30 --timeframe 1m --no-print-trades
```

**Last regenerated in this repo:** 2026-04-29 → [`docs/perf/overnight_range_MNQ_1m_chunks.json`](perf/overnight_range_MNQ_1m_chunks.json). Rollup: **`sum_total_trades` = 4**, **`sum_total_pnl` ≈ 238.30** — same totals as one `core/backtest_executor.py` run over the CSV’s full span (`--start`/`--end` omitted = whole file) with current `overnight_range` TOML (chunk runs reset capital each window, but here the completed round-trips line up).

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

Suggested **order** (largest impact on sample size first; the sweep script
uses *cumulative* env overrides so each step adds to the prior one):

| Step | Override(s) | What to log |
|------|-------------|----------------|
| P0 | none (live TOML) | trade count, total PnL, max DD, win rate, Sharpe |
| P1 | `OVERNIGHT_RANGE_FILTERS_SKIP_WEEKDAYS=""` | + count gain from Mon/Fri |
| P2 | + `OVERNIGHT_RANGE_FILTERS_VOLATILITY=false` | drops the ATR band gate |
| P3 | + `OVERNIGHT_RANGE_FILTERS_GAP=false` | drops the open-gap gate |
| P4 | + `OVERNIGHT_RANGE_FILTERS_RANGE_SIZE=false` (≈ "all filters off") | upper-bound on cadence |
| P5 | live filters **on** but widened bands (range 30..600, gap 250, ATR 15..220) | quality-controlled cadence |

One-shot driver — writes JSON + a TSV summary under
[docs/perf/sweeps/](perf/sweeps/):

```bash
CSV=historical_data/price/MNQ_1m_complete.csv \
  bash scripts/run_overnight_range_sweep.sh
```

**OOS / walk-forward (P0 vs P5)** — chronological split + Monte Carlo on OOS trades + optional folds (same env semantics as the sweep):

```bash
bash scripts/run_overnight_range_oos_wf.sh
# full-span (slow): USE_FAST=0 START=2025-12-24 WALK_FORWARD=4 bash scripts/run_overnight_range_oos_wf.sh
```

See [BACKTEST_RESEARCH.md](BACKTEST_RESEARCH.md) § “OOS / walk-forward”.

### Live TOML = P5 + reviewing simulated trades

**Live config:** [`config/strategies/overnight_range.toml`](../config/strategies/overnight_range.toml) `[filters]` is aligned with sweep **P5** (widened bands, `skip_weekdays = []`, range/gap/volatility filters still **on**). Hot-reload applies when the executor runs with `--reload` (see [PLAYBOOK.md](PLAYBOOK.md)).

**Re-simulate and inspect trades** (same logic as live; no env overrides needed):

```bash
# JSON with embedded trade objects (stdout = one JSON line).
# ``2>/dev/null`` = discard stderr (fd 2); use ``2>replay.stderr.log`` to keep logs.
# ``PYTHONUNBUFFERED=1`` avoids a 0-byte JSON file until the process exits when using ``>``.
export PYTHONUNBUFFERED=1
ENABLE_SIGNALR=false .venv/bin/python core/backtest_executor.py --strategy=overnight_range --symbol=MNQ \
  --timeframe=1m --csv=historical_data/price/MNQ_1m_complete.csv \
  --start=2025-12-24 --end=2026-04-30 --replay --format=json --include-trades \
  2>/dev/null > docs/perf/trade_review/overnight_range_p5_live_toml_mnq/backtest.json

# Human-readable summary / trades-only / CSV / Markdown
.venv/bin/python scripts/format_backtest_json.py --summary docs/perf/trade_review/overnight_range_p5_live_toml_mnq/backtest.json
.venv/bin/python scripts/format_backtest_json.py --trades-md docs/perf/trade_review/overnight_range_p5_live_toml_mnq/backtest.json

# Per-trade HTML charts around entry/exit
.venv/bin/python scripts/render_trade_review_charts.py \
  --json docs/perf/trade_review/overnight_range_p5_live_toml_mnq/backtest.json \
  --csv historical_data/price/MNQ_1m_complete.csv \
  --out-dir docs/perf/trade_review/overnight_range_p5_live_toml_mnq/charts
```

**Pre-built bundle (same P5 / live TOML semantics):** [trade_review/overnight_range_p5_live_toml_mnq/README.md](perf/trade_review/overnight_range_p5_live_toml_mnq/README.md) — `SUMMARY.txt`, `trades.md`, `trades.csv`, and **64** `charts/MNQ_*.html` files.

Full operator detail: [OVERNIGHT_RANGE_RESEARCH.md](OVERNIGHT_RANGE_RESEARCH.md) §2 (CSV replay, `format_backtest_json`, `render_trade_review_charts`). Use your Databento merged CSV path instead of `MNQ_1m_complete.csv` when you want 2023–2026 history.

Single ad-hoc step (e.g. only P4):

```bash
OVERNIGHT_RANGE_FILTERS_SKIP_WEEKDAYS="" \
OVERNIGHT_RANGE_FILTERS_VOLATILITY=false \
OVERNIGHT_RANGE_FILTERS_GAP=false \
OVERNIGHT_RANGE_FILTERS_RANGE_SIZE=false \
.venv/bin/python core/backtest_executor.py --strategy=overnight_range \
  --symbol=MNQ --timeframe=1m \
  --csv=historical_data/price/MNQ_1m_complete.csv \
  --start=2025-12-24 --end=2026-04-30 \
  --format=json --include-trades 2>/dev/null \
  > docs/perf/sweeps/overnight_range_MNQ_P4.json
```

Session‑level stats (features, OOS by **session**): [`scripts/alpha_discovery.py`](../scripts/alpha_discovery.py) on **5m** resampled CSV — see [ALPHA_DISCOVERY.md](ALPHA_DISCOVERY.md). **Bar-level conditionals** (next 5m bar vs RSI / VWAP / MA / inside bar; RTH prior-day high/low re-touch): [`scripts/pattern_conditional_scan.py`](../scripts/pattern_conditional_scan.py) → [`docs/alpha/pattern_scan_INDEX.md`](alpha/pattern_scan_INDEX.md). **Deep multi-horizon** scan with IS/OOS sign stability + phase stratification: [`scripts/deep_pattern_scan.py`](../scripts/deep_pattern_scan.py) → [`docs/alpha/deep_scan_INDEX.md`](alpha/deep_scan_INDEX.md) — the first run produced [`body_reversion`](../strategies/body_reversion_strategy.py) (cross-instrument big-body mean reversion).

Knobs file: [`config/strategies/overnight_range.toml`](../config/strategies/overnight_range.toml) (comments under `[filters]`).

---

## Pathway 2 — New strategies for “weekly cashflow” hypotheses

Same CSV contract: **1m (or 5m) OHLCV**, same `--start` / `--end`, then:

| Direction | Strategy config | Backtest entry |
|-----------|-------------------|----------------|
| Intraday / RTH | [`config/strategies/simple_rth.toml`](../config/strategies/simple_rth.toml) | `python core/backtest_executor.py --strategy=simple_rth … --replay` |
| Mean reversion (RSI / ATR) | [`config/strategies/mean_reversion.toml`](../config/strategies/mean_reversion.toml) | `--strategy=mean_reversion` |
| **VWAP Z-score reversion** (new) | [`config/strategies/vwap_zscore_reversion.toml`](../config/strategies/vwap_zscore_reversion.toml) | `--strategy=vwap_zscore_reversion --replay` |
| **Body reversion** (new, big-body 5m fade) | [`config/strategies/body_reversion.toml`](../config/strategies/body_reversion.toml) | `--strategy=body_reversion --timeframe=5m --replay` |
| Trend | [`config/strategies/trend_following.toml`](../config/strategies/trend_following.toml), [`trend_scalping.toml`](../config/strategies/trend_scalping.toml) | matching `--strategy=` |
| Candle pattern + EMA filter (testing-only live) | [`config/strategies/simple_candle.toml`](../config/strategies/simple_candle.toml) | `--strategy=simple_candle` (replay / `ALLOW_TESTING_STRATEGIES=1` for executor) |
| Momentum | [`config/strategies/simple_momentum.toml`](../config/strategies/simple_momentum.toml) | `--strategy=simple_momentum` |
| Research grid + OOS + MC | — | `python -m core.research.runner --help` (CSV flags in [BACKTEST_RESEARCH.md](BACKTEST_RESEARCH.md)) |

Run all of the above for a symbol in one shot:

```bash
SYMBOLS="MNQ" bash scripts/run_strategy_matrix.sh
# After MES / MGC are exported via scripts/walkback_history.sh:
SYMBOLS="MNQ MES MGC" bash scripts/run_strategy_matrix.sh
```

Register new names in [`strategies/strategy_manager.py::BUILTIN_STRATEGY_SPECS`](../strategies/strategy_manager.py) + add `config/strategies/<name>.toml` per [AGENTS.md](../AGENTS.md). Open hypotheses live in [perf/sweeps/CANDIDATES.md](perf/sweeps/CANDIDATES.md).

**Reality check:** Higher trade frequency usually **lowers** edge per trade; validate with **OOS** and **session** splits, not raw in‑sample win rate on 10 trades.

---

## Artifacts folder

Save dated JSON / notes under [`docs/perf/`](.) (see [README.md](README.md)). Regenerate `overnight_range_*_chunks.json` when strategy or replay logic changes so chunk rollups stay comparable.
