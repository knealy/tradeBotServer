#!/usr/bin/env bash
# Single backtest run for one symbol/timeframe (wrapper around core/backtest_executor.py).
#
# Defaults use SYNTHETIC in-memory bars (--sample). No CSV required.
#
# Usage:
#   SYMBOL=MNQ TIMEFRAME=5m DAYS=60 STRATEGY=ma_crossover bash scripts/backtest_symbol.sh
#   SYMBOL=MNQ STRATEGY=rsi_mean_reversion bash scripts/backtest_symbol.sh --format=json
#   MODE=api SYMBOL=MNQ DAYS=30 STRATEGY=ma_crossover bash scripts/backtest_symbol.sh   # real TopStepX history (needs .env auth)
#
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

SYMBOL="${SYMBOL:-MNQ}"
TIMEFRAME="${TIMEFRAME:-5m}"
DAYS="${DAYS:-30}"
STRATEGY="${STRATEGY:-ma_crossover}"
MODE="${MODE:-sample}"   # sample | api | csv
CSV="${CSV:-}"

ARGS=(
  --strategy="$STRATEGY"
  --symbol="$SYMBOL"
  --timeframe="$TIMEFRAME"
)

if [[ "$MODE" == "sample" ]]; then
  ARGS+=(--sample --days="$DAYS")
elif [[ "$MODE" == "api" ]]; then
  ARGS+=(--days="$DAYS")
elif [[ "$MODE" == "csv" ]]; then
  [[ -n "$CSV" && -f "$CSV" ]] || { echo "MODE=csv requires existing CSV=path/to/file.csv"; exit 1; }
  ARGS+=(--csv="$CSV")
else
  echo "MODE must be sample, api, or csv"; exit 1
fi

exec python3 core/backtest_executor.py "${ARGS[@]}" "$@"
