#!/usr/bin/env bash
# Fetch TopStepX historical bars to CSV (requires PROJECT_X_API_KEY + PROJECT_X_USERNAME in .env).
# Then backtest with: MODE=csv CSV=<this-file> bash scripts/backtest_symbol.sh
#
# Usage:
#   bash scripts/fetch_history_csv.sh --symbol MNQ --timeframe 5m --days 90
#   bash scripts/fetch_history_csv.sh --symbol MES --timeframe 1m --start 2024-06-01 --end 2024-12-01 --output historical_data/mes_1m.csv
#
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
exec python3 scripts/export_history.py "$@"
