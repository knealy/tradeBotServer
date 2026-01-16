#!/bin/bash
# save as: scripts/run_strategy_instance.sh

ACCOUNT_NUM=$1

if [ -z "$ACCOUNT_NUM" ]; then
    echo "❌ Error: Account number required"
    echo "Usage: $0 <account_number>"
    echo "Example: $0 3"
    exit 1
fi

# Ensure logs directory exists
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
PROJECT_ROOT="$( cd "${SCRIPT_DIR}/.." && pwd )"
LOG_DIR="${PROJECT_ROOT}/logs"
mkdir -p "$LOG_DIR"

# Generate unique log file name with account number and timestamp (with microseconds)
# This ensures each instance gets its own log file, even if started within the same second
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
# Add process ID to ensure uniqueness even if started at exact same time
PID=$$
LOG_FILE="${LOG_DIR}/overnight_range_account${ACCOUNT_NUM}_${TIMESTAMP}_${PID}.log"

echo "📝 Starting strategy executor for account ${ACCOUNT_NUM}"
echo "📝 Log file: ${LOG_FILE}"
echo "📝 Project root: ${PROJECT_ROOT}"

export LOG_FILE
cd "$PROJECT_ROOT"
caffeinate -dimsu python3 core/strategy_executor.py \
  --symbols=mnq,mes,mgc \
  --timeframe=5m \
  --strategy=overnight_range \
  --account_select=${ACCOUNT_NUM} \
  --max-pending=3