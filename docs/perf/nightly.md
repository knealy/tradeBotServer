# Nightly performance baselines (Phase 2.11)

This file is a **placeholder** for checked-in benchmark snapshots. Automation can
overwrite the table below when a scheduled job runs `scripts/run_bench.sh` and
commits the output.

## How to capture

From the repo root (venv active, dependencies installed):

```bash
./scripts/run_bench.sh --benchmark-json=docs/perf/last-bench.json
```

Summarize key rows from the JSON or from the terminal table into the section below.

## Target budgets (plan)

These are **not** enforced in CI yet; compare manually or wire a gate later.

| Area | Budget (p95) |
|------|----------------|
| `place_market_order` RTT | < 200 ms |
| `get_positions` | < 100 ms |
| Tick → aggregator → broadcast | < 50 ms |
| Strategy startup → first eval | < 5 s |

## Last run

| Date (UTC) | Commit | Notes |
|------------|--------|--------|
| — | — | Run `./scripts/run_bench.sh` and fill in. |

## Microbenchmarks in `tests/bench/`

- `test_orjson_vs_json.py` — serialization hot path
- `test_bar_aggregator_throughput.py` — `BarAggregator.add_quote` burst
- `test_event_bus_publish_latency.py` — publish + subscriber drain
- `test_db_write_batch.py` — row batch / JSON prep (no DB)
- `test_aggregation.py` — `BarBuilder.add_tick`
- `test_orders.py` — order-shaped payload roundtrip

Standalone Rust harnesses (not pytest): `tests/bench_rust_vs_python_aggregation.py`,
`tests/bench_rust_vs_python_orders.py` (live orders — sandbox only).
