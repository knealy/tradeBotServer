#!/usr/bin/env bash
# Export the same calendar window for multiple symbols (TopStepX API).
# Requires .env with PROJECT_X_API_KEY + PROJECT_X_USERNAME.
#
# Usage:
#   START=2025-12-23 END=2026-04-23 bash scripts/export_history_parallel_range.sh
#   START=2025-12-23 END=2026-04-23 SYMBOLS="MNQ MES" TF=5m CHUNK=45 bash scripts/export_history_parallel_range.sh
#
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

START="${START:?set START=YYYY-MM-DD}"
END="${END:?set END=YYYY-MM-DD}"
TF="${TF:-1m}"
CHUNK="${CHUNK:-30}"
SYMBOLS="${SYMBOLS:-MNQ MES MGC}"
OUT_DIR="${OUT_DIR:-historical_data/price}"

mkdir -p "$OUT_DIR"
PY="${ROOT}/.venv/bin/python"
[[ -x "$PY" ]] || PY=python3

for sym in $SYMBOLS; do
  out="${OUT_DIR}/${sym}_${TF}_${START}_${END}.csv"
  echo "=== $sym -> $out ==="
  "$PY" "${ROOT}/scripts/export_history.py" \
    --symbol="$sym" \
    --timeframe="$TF" \
    --start="$START" \
    --end="$END" \
    --chunk-days="$CHUNK" \
    --output="$out"
done

echo "Done. Use each CSV with: --csv=$OUT_DIR/<file> --start=$START --end=$END"
