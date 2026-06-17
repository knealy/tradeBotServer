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

# Source .env so DISCORD_WEBHOOK_URL / PROJECT_X_* reach the headless executor.
if [ -f "${PROJECT_ROOT}/.env" ]; then
    set -a
    # shellcheck disable=SC1091
    source "${PROJECT_ROOT}/.env"
    set +a
fi

LOG_DIR="${PROJECT_ROOT}/logs"
if [ -e "$LOG_DIR" ] && [ ! -d "$LOG_DIR" ]; then
    TS="$(date +%Y%m%d_%H%M%S)"
    mv "$LOG_DIR" "${LOG_DIR}.file_backup_${TS}"
fi
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

# 2026-05-31 Round-13: MES dropped from active rotation (see TOML
# ``[meta].symbols`` rationale).  Override at the command line to A/B test:
#   OVERNIGHT_RANGE_SYMBOLS=mnq,mes,mgc ./scripts/run_overnight.sh 1
SYMBOLS="${OVERNIGHT_RANGE_SYMBOLS:-mnq,mgc}"

export DISCORD_STATUS_INTERVAL_SECONDS="${DISCORD_STATUS_INTERVAL_SECONDS:-1800}"
export DATA_FEED_DISCORD_ALERTS="${DATA_FEED_DISCORD_ALERTS:-true}"
export DATA_FEED_DISCORD_ALERT_COOLDOWN_S="${DATA_FEED_DISCORD_ALERT_COOLDOWN_S:-900}"
export LOG_SUPPRESS_ASYNCIO_SESSION_ERRORS="${LOG_SUPPRESS_ASYNCIO_SESSION_ERRORS:-1}"
export DATA_FEED_HEALTH_GATE_MODE="${DATA_FEED_HEALTH_GATE_MODE:-warn}"

caffeinate -dimsu python3 core/strategy_executor.py \
  --symbols="${SYMBOLS}" \
  --timeframe=2m \
  --strategy=overnight_range \
  --account_select=${ACCOUNT_NUM} \
  --risk-config '{"MNQ":{"max_quantity":3,"cooldown":60.0,"max_pending":2},"MES":{"max_quantity":3,"cooldown":60.0,"max_pending":2},"MGC":{"max_quantity":2,"cooldown":60.0,"max_pending":1}}'