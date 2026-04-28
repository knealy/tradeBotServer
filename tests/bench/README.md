# Performance benches

Benches for the trading bot, run via `pytest-benchmark`.

Phase 2.11 of the cleanup plan adds:

- `test_bar_aggregator_throughput.py`
- `test_event_bus_publish_latency.py`
- `test_db_write_batch.py`
- `test_orjson_vs_json.py`

Targets enforced via `make bench`:

- `place_market_order` RTT — < 200 ms p95
- `get_positions` — < 100 ms p95
- Tick → aggregator → broadcast — < 50 ms p95
- Strategy startup — < 5 s

The two existing rust-vs-python benches at the repo top level move here:
[bench_rust_vs_python_aggregation.py](../bench_rust_vs_python_aggregation.py),
[bench_rust_vs_python_orders.py](../bench_rust_vs_python_orders.py).

Nightly results land at [docs/perf/nightly.md](../../docs/perf/nightly.md).
