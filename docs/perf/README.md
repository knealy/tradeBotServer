# Performance baselines (`docs/perf/`)

Strategy research (overnight_range filter sweeps, chunk vs single-run semantics, multi-symbol CSV export): [RESEARCH_PATHWAYS.md](../RESEARCH_PATHWAYS.md). Regenerate `overnight_range_*_chunks.json` after replay/strategy fixes so rollups stay comparable to current code.

Use this folder for **before/after** profiling artifacts (SVG/HTML) when tuning the asyncio hot path, startup, or DB.
Short operator checklist: [BASELINE.md](BASELINE.md). Live trading / Rust vs I/O: [OPERATIONS_TUNING.md](OPERATIONS_TUNING.md).

## uvloop

On Linux and macOS, `uvloop` is installed from `requirements.txt` and enabled in `core/logging_setup.configure_logging()` unless disabled:

- `USE_UVLOOP=0` or `false` — skip `uvloop.install()`
- `DISABLE_UVLOOP=1` — same

Windows builds skip the dependency (PEP 508 marker); the stdlib asyncio loop is used.

## Capturing a CPU profile (py-spy)

Install [py-spy](https://github.com/benfred/py-spy) on the host (not inside venv required).

```bash
# Find PID of strategy executor or webhook process, then record ~60s
py-spy record -o docs/perf/profile-strategy_executor.svg --pid <PID> --duration 60
```

Commit SVGs here when comparing phases (e.g. before/after Phase 2.7). Name files with date and scenario.

## Memory / mixed profile (Scalene)

```bash
pip install scalene
scalene --html --outfile docs/perf/scalene-report.html -m core.strategy_executor --strategy=mean_reversion --account_id=YOUR_ID
```

Run during representative load (RTH) for comparable numbers.

## Import time

```bash
python3 -X importtime -c "import trading_bot" 2> docs/perf/importtime-trading_bot.log
```

Useful for regressions after lazy-import or dependency changes.

## Microbenchmarks (`tests/bench/`)

Install dev deps from `requirements.txt` (includes `pytest-benchmark`), then:

```bash
./scripts/run_bench.sh
```

Default `pytest` only runs `tests/test_smoke_imports.py` (see `pytest.ini`). Benches
are opt-in via the script or `pytest tests/bench -m bench --override-ini="testpaths="`.

Checked-in baseline placeholder: [nightly.md](nightly.md).
