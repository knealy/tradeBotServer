# Testing

## Curated suite (default)

From repo root:

```bash
make test
# or: pytest
```

[pytest.ini](../pytest.ini) sets `pythonpath = .` and a small default `testpaths` (smoke, order executor bus, logging path, etc.).

## Full tree

Many tests are legacy or unmaintained:

```bash
pytest --override-ini="testpaths=tests"
```

## Benchmarks

- [perf/README.md](perf/README.md), [perf/nightly.md](perf/nightly.md), [scripts/run_bench.sh](../scripts/run_bench.sh).

## Handoff verification

```bash
scripts/verify_handoff.sh
```
