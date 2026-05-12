#!/usr/bin/env bash
# Run morning_range_reversion headless — same pattern as scripts/run_reversion.sh
# and scripts/run_overnight.sh (caffeinate + core/strategy_executor.py).
#
# Bar timeframe comes from config/strategies/morning_range_reversion.toml (default 5m).
# Default symbols: MNQ,MGC,MES. Override with:
#   MORNING_RANGE_SYMBOLS="MNQ" bash scripts/run_morning_reversion.sh 1
#
# Prerequisites:
# - Auto OCO Brackets enabled on the account (bracket / stop-entry path).
# - Set [meta] enabled = true in morning_range_reversion.toml (or enable via DB/GUI)
#   when you are ready to trade; the stock TOML keeps enabled = false for safety.
#
# Usage: bash scripts/run_morning_reversion.sh <account_select_index>
# Example: bash scripts/run_morning_reversion.sh 1

set -euo pipefail

ACCOUNT_NUM="${1:-}"

if [ -z "$ACCOUNT_NUM" ]; then
    echo "Error: account index required (same as run_overnight.sh --account_select)"
    echo "Usage: $0 <account_number>"
    echo "Example: $0 1"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
LOG_DIR="${PROJECT_ROOT}/logs"
if [ -e "$LOG_DIR" ] && [ ! -d "$LOG_DIR" ]; then
    TS="$(date +%Y%m%d_%H%M%S)"
    mv "$LOG_DIR" "${LOG_DIR}.file_backup_${TS}"
fi
mkdir -p "$LOG_DIR"

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
PID=$$
LOG_FILE="${LOG_DIR}/morning_range_reversion_account${ACCOUNT_NUM}_${TIMESTAMP}_${PID}.log"

SYMBOLS="${MORNING_RANGE_SYMBOLS:-MNQ,MGC,MES}"

echo "Starting strategy executor: morning_range_reversion (account select ${ACCOUNT_NUM})"
echo "Symbols: ${SYMBOLS}"
echo "Log file: ${LOG_FILE}"
echo "Project root: ${PROJECT_ROOT}"

export LOG_FILE
cd "$PROJECT_ROOT"

PY="${PROJECT_ROOT}/.venv/bin/python"
if [ ! -x "$PY" ]; then
    echo "Missing ${PY} — create venv or use python3"
    exit 1
fi

# max_pending=2: room for hybrid stop-entry + OCO siblings (same tuning as body_reversion).
exec caffeinate -dimsu "$PY" core/strategy_executor.py \
  --strategy=morning_range_reversion \
  --symbols="${SYMBOLS}" \
  --account_select="${ACCOUNT_NUM}" \
  --risk-config '{"MNQ":{"max_quantity":1,"cooldown":60.0,"max_pending":2},"MES":{"max_quantity":1,"cooldown":60.0,"max_pending":2},"MGC":{"max_quantity":1,"cooldown":60.0,"max_pending":2}}'
