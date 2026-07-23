#!/usr/bin/env bash
# Alert if no MRR executor log was created today (Mon-Fri).
# Install: cron at 17:00 local on weekdays, or run manually after the session.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

if [ -f "${ROOT}/.env" ]; then
    set -a
    # shellcheck disable=SC1091
    source "${ROOT}/.env"
    set +a
fi

PY="${ROOT}/.venv/bin/python"
TODAY=$(date +%Y%m%d)
DOW=$(date +%u)

if [ "$DOW" -gt 5 ]; then
    exit 0
fi

if ls "${ROOT}/logs/morning_range_reversion_account"*_"${TODAY}"_*.log 1>/dev/null 2>&1; then
    exit 0
fi

"$PY" "${ROOT}/scripts/discord_wrapper_ping.py" missed_session \
    "No MRR executor log for ${TODAY} (weekday) — check launchd, Mac sleep, or wrapper errors in logs/launchd_mrr_account*.err"
