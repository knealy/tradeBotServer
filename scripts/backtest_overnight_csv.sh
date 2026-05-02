#!/usr/bin/env bash
# CSV strategy replay for overnight_range (class-based engine).
#
# Requires OHLCV CSV (e.g. export from scripts/export_history.py or merged archive).
# Optional START/END filter (YYYY-MM-DD) matches backtest_executor calendar slice on the index.
#
# Usage:
#   bash scripts/backtest_overnight_csv.sh
#   CSV=historical_data/price/MNQ_1m_complete.csv START=2026-01-01 END=2026-01-31 bash scripts/backtest_overnight_csv.sh
#   SYMBOL=MNQ TIMEFRAME=5m CSV=path/to/MNQ_5m.csv bash scripts/backtest_overnight_csv.sh
#
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PYTHON="${ROOT}/.venv/bin/python"
[[ -x "$PYTHON" ]] || PYTHON=python3

CSV="${CSV:-${ROOT}/historical_data/price/MNQ_1m_complete.csv}"
SYMBOL="${SYMBOL:-MNQ}"
TIMEFRAME="${TIMEFRAME:-1m}"
START="${START:-}"
END="${END:-}"

[[ -f "$CSV" ]] || { echo "Missing CSV: $CSV"; exit 1; }

ARGS=(
  "${ROOT}/core/backtest_executor.py"
  --strategy=overnight_range
  --symbol="$SYMBOL"
  --timeframe="$TIMEFRAME"
  --csv="$CSV"
  --capital="${CAPITAL:-50000}"
)
[[ -n "$START" ]] && ARGS+=(--start="$START")
[[ -n "$END" ]] && ARGS+=(--end="$END")

exec "$PYTHON" "${ARGS[@]}" "$@"
