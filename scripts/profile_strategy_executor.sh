#!/usr/bin/env bash
# Record a py-spy flame graph for a running strategy_executor (or any Python PID).
# Usage: bash scripts/profile_strategy_executor.sh <PID> [duration_seconds]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PID="${1:?Usage: $0 <PID> [seconds]}"
DUR="${2:-60}"
OUT="$ROOT/docs/perf/profile-strategy_executor-${PID}-$(date -u +%Y%m%dT%H%M%SZ).svg"
mkdir -p "$ROOT/docs/perf"
if ! command -v py-spy >/dev/null 2>&1; then
  echo "py-spy not found. Install: pip install py-spy  (or brew install py-spy)" >&2
  exit 1
fi
exec py-spy record -o "$OUT" --pid "$PID" --duration "$DUR"
