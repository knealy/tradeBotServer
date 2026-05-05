#!/usr/bin/env bash
# Run body_reversion (v3.1 hybrid TOML) like run_overnight.sh — TopStepX PRAC/live.
#
# Prerequisites:
# - Auto OCO Brackets enabled on the account (same as other bracket strategies).
# - 5m bars: strategy reads timeframe from config/strategies/body_reversion.toml.
#
# Usage: bash scripts/run_reversion.sh <account_select_index>
# Example: bash scripts/run_reversion.sh 1

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
LOG_FILE="${LOG_DIR}/body_reversion_account${ACCOUNT_NUM}_${TIMESTAMP}_${PID}.log"

echo "Starting strategy executor: body_reversion (account select ${ACCOUNT_NUM})"
echo "Log file: ${LOG_FILE}"
echo "Project root: ${PROJECT_ROOT}"

export LOG_FILE
cd "$PROJECT_ROOT"

PY="${PROJECT_ROOT}/.venv/bin/python"
if [ ! -x "$PY" ]; then
    echo "Missing ${PY} — create venv or use python3"
    exit 1
fi

# max_pending=2: room for hybrid stop-entry path + bracket siblings (see overnight_range tuning).
exec caffeinate -dimsu "$PY" core/strategy_executor.py \
  --strategy=body_reversion \
  --symbols=MNQ,MES,MGC \
  --account_select="${ACCOUNT_NUM}" \
  --risk-config '{"MNQ":{"max_quantity":1,"cooldown":60.0,"max_pending":2},"MES":{"max_quantity":1,"cooldown":60.0,"max_pending":2},"MGC":{"max_quantity":1,"cooldown":60.0,"max_pending":2}}'
