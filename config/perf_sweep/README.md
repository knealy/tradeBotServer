# Perf sweep manifests (`config/perf_sweep/`)

JSON manifests drive **`scripts/strategy_parameter_sweep.py`**.

- **defaults**: `start`, `end`, `timeframe`, `csv_template` (placeholders `{root}`, `{sym}`, `{sym_lower}`, `{SYM}`).
- **waves[]**: each has `id`, `description`, `strategy`, `symbols`, `runs[]`.
- **runs[]**: `label` + `env` map. Keys are full env names, e.g. `OVERNIGHT_RANGE_SIGNAL_STOP_ATR_MULTIPLIER` (see `core/strategy_config.StrategyConfig.get`).

Run one wave (dry plan):

```bash
.venv/bin/python scripts/strategy_parameter_sweep.py \
  --manifest config/perf_sweep/default_wave_manifest.json \
  --waves overnight_atr_bracket --dry-run
```

Full sweep (writes under `docs/perf/parameter_sweeps/<utc>/`, gitignored):

```bash
ENABLE_SIGNALR=false .venv/bin/python scripts/strategy_parameter_sweep.py \
  --manifest config/perf_sweep/default_wave_manifest.json
```

## Overnight-only follow-up (MNQ, ATR 1.5 / 2.5 pinned)

After adopting **`stop_atr_multiplier=1.5`** and **`tp_atr_multiplier=2.5`** in **`config/strategies/overnight_range.toml`**, use **`overnight_opt_mnq.json`** to sweep **session end**, **session start**, **market_open/zone_anchor**, and **`signal.atr_timeframe`** on the same date window:

```bash
ENABLE_SIGNALR=false .venv/bin/python scripts/strategy_parameter_sweep.py \
  --manifest config/perf_sweep/overnight_opt_mnq.json \
  --run-id overnight_timing_mnq
```

Single wave (e.g. ATR timeframe only):

```bash
ENABLE_SIGNALR=false .venv/bin/python scripts/strategy_parameter_sweep.py \
  --manifest config/perf_sweep/overnight_opt_mnq.json \
  --waves overnight_atr_timeframe_mnq \
  --run-id overnight_atr_tf_mnq
```
