#!/usr/bin/env bash
# install_mrr_launchd.sh - Install + load the daily MRR launch agent.
#
# 2026-06-15 daily-launchd migration: the original "leave run_morning_reversion.sh
# running all week" workflow has been superseded by a daily launchd schedule —
# launchd kicks off the wrapper once per weekday morning, the wrapper trades
# the session and exits at flat_before+grace, launchd takes over for tomorrow.
# This script does the install dance once per account:
#
#   1. Read start_time + session_timezone from
#      ``config/strategies/morning_range_reversion.toml``.
#   2. Compute the equivalent LOCAL-CLOCK time for "WAKE_LEAD_MIN minutes
#      before start_time ET" (host TZ != ET is fine — we translate).
#   3. Substitute the values into the plist template.
#   4. Copy to ~/Library/LaunchAgents/ and ``launchctl load`` it.
#   5. ``launchctl list`` to confirm.
#
# Usage:
#   bash scripts/install_mrr_launchd.sh 1                # install for account 1
#   bash scripts/install_mrr_launchd.sh 1 --wake-lead 10 # custom wake lead (min)
#   bash scripts/install_mrr_launchd.sh 1 --dry-run     # generate plist, don't load
#   bash scripts/install_mrr_launchd.sh 1 --reload      # reload after edit
#
# To remove: bash scripts/uninstall_mrr_launchd.sh <account_num>

set -euo pipefail

# ── Arg parsing ───────────────────────────────────────────────────────────────
ACCOUNT_NUM="${1:-}"
shift || true

WAKE_LEAD_MIN=5    # launchd fires this many minutes before start_time ET
DRY_RUN=0
RELOAD=0
while [ "$#" -gt 0 ]; do
    case "$1" in
        --wake-lead) WAKE_LEAD_MIN="$2"; shift 2 ;;
        --wake-lead=*) WAKE_LEAD_MIN="${1#*=}"; shift ;;
        --dry-run) DRY_RUN=1; shift ;;
        --reload) RELOAD=1; shift ;;
        --help|-h)
            cat <<EOF
Usage: $0 <account_num> [--wake-lead MIN] [--dry-run] [--reload]

  <account_num>     Account select index passed to run_morning_reversion.sh
  --wake-lead MIN   Launch this many minutes before start_time ET (default 5)
  --dry-run         Generate the plist + print the install steps, but don't load
  --reload          Unload + reload an existing agent (e.g. after editing TOML)
EOF
            exit 0
            ;;
        *) echo "Unknown arg: $1"; exit 1 ;;
    esac
done

if [ -z "$ACCOUNT_NUM" ]; then
    echo "Error: account number required."
    echo "Usage: $0 <account_num> [--wake-lead MIN] [--dry-run] [--reload]"
    exit 1
fi

# ── Path resolution ───────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
TOML_PATH="${PROJECT_ROOT}/config/strategies/morning_range_reversion.toml"
TEMPLATE="${PROJECT_ROOT}/scripts/launchd/com.tradebot.mrr.plist.template"
WRAPPER="${PROJECT_ROOT}/scripts/run_morning_reversion.sh"

for f in "$TOML_PATH" "$TEMPLATE" "$WRAPPER"; do
    if [ ! -f "$f" ]; then
        echo "Error: missing required file: $f"
        exit 1
    fi
done

PY="${PROJECT_ROOT}/.venv/bin/python"
if [ ! -x "$PY" ]; then
    PY="python3"
fi

LABEL="com.tradebot.mrr.account${ACCOUNT_NUM}"
PLIST_NAME="${LABEL}.plist"
TARGET_PATH="${HOME}/Library/LaunchAgents/${PLIST_NAME}"

# ── Compute local-clock wake time from TOML + host TZ ────────────────────────
SCHEDULE=$("$PY" - "$TOML_PATH" "$WAKE_LEAD_MIN" <<'PY'
"""Read MRR's start_time + session_timezone from TOML, then translate
"start_time minus WAKE_LEAD_MIN" into the host's local clock so launchd
(which schedules in local time) fires at the right moment regardless of
host TZ vs ET.

Outputs one line:
  LOCAL_HOUR LOCAL_MINUTE ET_HOUR ET_MINUTE TZ_NAME LOCAL_TZ_LABEL
"""
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    import tomllib
except ImportError:
    import tomli as tomllib

toml_path = Path(sys.argv[1])
wake_lead_min = int(sys.argv[2])

with toml_path.open("rb") as fh:
    cfg = tomllib.load(fh)

signal = cfg.get("signal", {})
tz_name = signal.get("session_timezone", "America/New_York")
session_tz = ZoneInfo(tz_name)

start_raw = str(cfg.get("start_time", "06:55"))
hh, mm = (int(x) for x in start_raw.split(":")[:2])

# Build a representative ET datetime "today at start_time" and subtract the
# wake lead.  Date doesn't actually matter for launchd's clock-only schedule
# but we need *some* concrete dt to anchor the ET->local conversion.
now_et = datetime.now(session_tz)
target_et = now_et.replace(hour=hh, minute=mm, second=0, microsecond=0)
from datetime import timedelta
target_et = target_et - timedelta(minutes=wake_lead_min)

local_tz = ZoneInfo(time.tzname[0] if time.tzname else "UTC")
# tzlocal would be cleaner but it's not a hard dep — use system local instead.
target_local = target_et.astimezone()  # uses system local tz

print(
    target_local.hour,
    target_local.minute,
    target_et.hour,
    target_et.minute,
    tz_name,
    target_local.tzname() or "local",
)
PY
)

read -r LOCAL_HOUR LOCAL_MINUTE ET_HOUR ET_MINUTE SESSION_TZ LOCAL_TZ_LABEL <<<"$SCHEDULE"

# ── Build PATH env for the agent ─────────────────────────────────────────────
# launchd agents start with a minimal PATH; we want homebrew (Apple Silicon +
# Intel paths), the venv, the user's bin, and system defaults so caffeinate,
# python, and the wrapper's PY all resolve.
AGENT_PATH="${PROJECT_ROOT}/.venv/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

# ── Build log paths ───────────────────────────────────────────────────────────
LOG_DIR="${PROJECT_ROOT}/logs"
mkdir -p "$LOG_DIR"
LOG_OUT="${LOG_DIR}/launchd_mrr_account${ACCOUNT_NUM}.out"
LOG_ERR="${LOG_DIR}/launchd_mrr_account${ACCOUNT_NUM}.err"

# ── Render the plist ─────────────────────────────────────────────────────────
RENDERED=$(mktemp -t mrr_launchd_plist.XXXXXX)
trap 'rm -f "$RENDERED"' EXIT

# Use awk -v for safe substitution; sed would need careful escaping of /'s in
# the absolute paths.
awk -v LABEL="$LABEL" \
    -v SCRIPT_PATH="$WRAPPER" \
    -v ACCOUNT="$ACCOUNT_NUM" \
    -v WORKING_DIR="$PROJECT_ROOT" \
    -v LOG_OUT="$LOG_OUT" \
    -v LOG_ERR="$LOG_ERR" \
    -v HOUR="$LOCAL_HOUR" \
    -v MINUTE="$LOCAL_MINUTE" \
    -v APATH="$AGENT_PATH" '
    {
        gsub(/__LABEL__/, LABEL)
        gsub(/__SCRIPT_PATH__/, SCRIPT_PATH)
        gsub(/__ACCOUNT__/, ACCOUNT)
        gsub(/__WORKING_DIR__/, WORKING_DIR)
        gsub(/__LOG_OUT__/, LOG_OUT)
        gsub(/__LOG_ERR__/, LOG_ERR)
        gsub(/__HOUR__/, HOUR)
        gsub(/__MINUTE__/, MINUTE)
        gsub(/__PATH__/, APATH)
        print
    }
' "$TEMPLATE" > "$RENDERED"

# ── Validate the rendered plist via plistlib ─────────────────────────────────
"$PY" - "$RENDERED" <<'PY'
import plistlib, sys
from pathlib import Path
path = Path(sys.argv[1])
try:
    with path.open("rb") as f:
        data = plistlib.load(f)
except Exception as exc:
    print(f"❌ plist validation failed: {exc}", file=sys.stderr)
    sys.exit(1)
# Sanity-check required keys.
for key in ("Label", "ProgramArguments", "StartCalendarInterval"):
    if key not in data:
        print(f"❌ rendered plist missing required key {key!r}", file=sys.stderr)
        sys.exit(1)
intervals = data["StartCalendarInterval"]
if not (isinstance(intervals, list) and len(intervals) == 5):
    print(f"❌ expected 5 StartCalendarInterval entries (Mon-Fri), got {len(intervals)}", file=sys.stderr)
    sys.exit(1)
print("✓ plist validation OK")
PY

# ── Print a summary banner ────────────────────────────────────────────────────
cat <<EOF

+------------------------------------------------------------------+
|   morning_range_reversion daily launchd agent - install summary   |
+------------------------------------------------------------------+
  label                : ${LABEL}
  account              : ${ACCOUNT_NUM}
  wrapper              : ${WRAPPER#${PROJECT_ROOT}/}
  plist target         : ${TARGET_PATH/#${HOME}/~}
  schedule (local time): Mon-Fri  ${LOCAL_HOUR}:$(printf '%02d' "$LOCAL_MINUTE")  ${LOCAL_TZ_LABEL}
  schedule (session TZ): ${ET_HOUR}:$(printf '%02d' "$ET_MINUTE")  ${SESSION_TZ}
  wake lead            : ${WAKE_LEAD_MIN}min before start_time
  log stdout           : ${LOG_OUT#${PROJECT_ROOT}/}
  log stderr           : ${LOG_ERR#${PROJECT_ROOT}/}

EOF

# ── Dry-run short-circuit ────────────────────────────────────────────────────
if [ "$DRY_RUN" -eq 1 ]; then
    echo "  --dry-run: skipping install + load.  Rendered plist below:"
    echo "  --------------------------------------------------------------"
    cat "$RENDERED"
    echo "  --------------------------------------------------------------"
    exit 0
fi

# ── Install + load ───────────────────────────────────────────────────────────
mkdir -p "$(dirname "$TARGET_PATH")"

# Unload any existing agent with the same label first; bootout is the modern
# launchctl unload equivalent.  ``|| true`` since the agent may not be loaded.
if [ -f "$TARGET_PATH" ] || [ "$RELOAD" -eq 1 ]; then
    echo "  → unloading existing agent (if loaded)..."
    launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null || \
        launchctl unload -w "$TARGET_PATH" 2>/dev/null || true
fi

cp "$RENDERED" "$TARGET_PATH"
echo "  ✓ installed plist to ${TARGET_PATH}"

# Modern bootstrap path; falls back to legacy load if bootstrap unavailable.
if launchctl bootstrap "gui/$(id -u)" "$TARGET_PATH" 2>/dev/null; then
    echo "  ✓ loaded via launchctl bootstrap"
else
    launchctl load -w "$TARGET_PATH"
    echo "  ✓ loaded via launchctl load"
fi

# ── Verify ───────────────────────────────────────────────────────────────────
echo
echo "  Verifying with launchctl list..."
if launchctl list | grep -q "$LABEL"; then
    echo "  ✓ ${LABEL} is loaded.  Next wake-up: ${LOCAL_HOUR}:$(printf '%02d' "$LOCAL_MINUTE") local (Mon-Fri)."
else
    echo "  ⚠️  ${LABEL} not found in launchctl list — check Console.app for errors."
fi

cat <<EOF

  Useful commands:
    launchctl list | grep ${LABEL}                   # check load status
    launchctl print "gui/\$(id -u)/${LABEL}"           # detailed inspection
    launchctl kickstart -k "gui/\$(id -u)/${LABEL}"    # force-run NOW (testing)
    bash scripts/uninstall_mrr_launchd.sh ${ACCOUNT_NUM}  # remove

  Tail logs:
    tail -f ${LOG_OUT}
    tail -f ${LOG_ERR}
    tail -f ${LOG_DIR}/morning_range_reversion_account${ACCOUNT_NUM}_*.log

EOF
