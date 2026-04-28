# Performance benches (`tests/bench/`)

Run via **`pytest-benchmark`**:

```bash
# from repo root
./scripts/run_bench.sh
```

Or: `pytest tests/bench -m bench --override-ini="testpaths="`

## Modules

- `test_orjson_vs_json.py` — `json_fast` vs stdlib `json`
- `test_bar_aggregator_throughput.py` — `BarAggregator.add_quote` burst
- `test_event_bus_publish_latency.py` — `EventBus` publish + drain
- `test_db_write_batch.py` — batch row / JSONB string prep (no live Postgres)
- `test_aggregation.py` — `BarBuilder.add_tick`
- `test_orders.py` — order-shaped payload serialize/parse

## Plan budgets (manual / future CI)

See [docs/perf/nightly.md](../../docs/perf/nightly.md): `place_market_order`, `get_positions`,
tick→aggregator, strategy startup.

## Standalone Rust / live scripts

These stay as **runnable scripts** under `tests/` (not pytest):

- [bench_rust_vs_python_aggregation.py](../bench_rust_vs_python_aggregation.py)
- [bench_rust_vs_python_orders.py](../bench_rust_vs_python_orders.py) (sandbox only; places real orders when enabled)
