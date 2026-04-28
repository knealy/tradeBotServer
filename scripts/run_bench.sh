#!/usr/bin/env bash
# Run performance microbenchmarks (Phase 2.11). Optional: capture to docs/perf/nightly.md.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
if [[ -x "$ROOT/.venv/bin/python" ]]; then
  PYTHON="$ROOT/.venv/bin/python"
else
  PYTHON="${PYTHON:-python3}"
fi
exec "$PYTHON" -m pytest tests/bench \
  -m bench \
  --override-ini="testpaths=tests/bench" \
  --benchmark-columns=min,max,mean,median,stddev \
  --benchmark-min-rounds="${BENCH_MIN_ROUNDS:-10}" \
  --benchmark-warmup="${BENCH_WARMUP:-on}" \
  "$@"
