#!/usr/bin/env bash
# Run morning_range_reversion headless with a scheduled wake-up + live countdown.
#
# The schedule is computed straight from
# ``config/strategies/morning_range_reversion.toml`` (US/Eastern wall clock) so
# editing the TOML also moves this script. Flow:
#
#   1. Read start_time / range_start / range_end_open / flat_before from TOML.
#   2. If we're already inside the trading window (or NO_WAIT=1 / --now), exec
#      ``core/strategy_executor.py`` immediately.
#   3. Otherwise sleep until ``start_time - WAKE_MINUTES_EARLY`` (default 2 min)
#      with a live one-line countdown showing the next session milestone.
#
# Typical "set it tonight, runs in the morning" usage:
#
#   bash scripts/run_morning_reversion.sh 1
#     → prints schedule, waits with countdown until ~06:53 ET, then launches.
#       ``caffeinate -dimsu`` keeps the Mac awake for the whole wait.
#
# Other invocations:
#
#   bash scripts/run_morning_reversion.sh 1 --now       # skip wait, run now
#   NO_WAIT=1 bash scripts/run_morning_reversion.sh 1   # same via env var
#   MORNING_RANGE_SYMBOLS="MNQ" bash scripts/run_morning_reversion.sh 1
#   WAKE_MINUTES_EARLY=5 bash scripts/run_morning_reversion.sh 1
#
# Prerequisites:
# - Auto OCO Brackets enabled on the account (bracket / stop-entry path).
# - ``[meta] enabled = true`` in ``config/strategies/morning_range_reversion.toml``
#   (or enable per-account via DB / Master GUI).
# - Python 3.11+ for ``tomllib`` (the repo's .venv/bin/python is preferred).

set -euo pipefail

ACCOUNT_NUM="${1:-}"

if [ -z "$ACCOUNT_NUM" ]; then
    echo "Error: account index required (same as run_overnight.sh --account_select)"
    echo "Usage: $0 <account_select_index> [--now]"
    echo "Example: $0 1"
    exit 1
fi

# Accept ``--now`` as the second positional in addition to NO_WAIT=1.
if [ "${2:-}" = "--now" ]; then
    NO_WAIT=1
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
TOML_PATH="${PROJECT_ROOT}/config/strategies/morning_range_reversion.toml"
WAKE_EARLY_MIN="${WAKE_MINUTES_EARLY:-2}"

PY="${PROJECT_ROOT}/.venv/bin/python"
if [ ! -x "$PY" ]; then
    PY="python3"
fi

if [ ! -f "$TOML_PATH" ]; then
    echo "Error: missing TOML config at ${TOML_PATH}"
    exit 1
fi

# ---------- compute schedule from TOML (US/Eastern) ---------------------------
# Emits one line: WAKE_TS START_TS RANGE_START_TS RANGE_END_TS FLAT_BEFORE_TS TZ
if ! SCHEDULE=$("$PY" - "$TOML_PATH" "$WAKE_EARLY_MIN" <<'PY'
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    import tomllib
except ImportError:  # pragma: no cover - 3.10 fallback
    import tomli as tomllib  # type: ignore

toml_path = Path(sys.argv[1])
wake_early_min = int(sys.argv[2])

with toml_path.open("rb") as fh:
    cfg = tomllib.load(fh)

signal = cfg.get("signal", {})
tz_name = signal.get("session_timezone", "America/New_York")
tz = ZoneInfo(tz_name)


def hhmm(value, fallback):
    try:
        hh, mm = str(value).split(":")
        return int(hh), int(mm)
    except Exception:
        return fallback


start_hh, start_mm = hhmm(cfg.get("start_time", "06:55"), (6, 55))
rs_hh, rs_mm = hhmm(signal.get("range_start", "07:00"), (7, 0))
re_hh, re_mm = hhmm(signal.get("range_end_open", "08:00"), (8, 0))
fb_hh, fb_mm = hhmm(signal.get("flat_before", "16:00"), (16, 0))

now_et = datetime.now(tz)


def at(hh, mm, day):
    return day.replace(hour=hh, minute=mm, second=0, microsecond=0)


flat_today = at(fb_hh, fb_mm, now_et)
# After today's flat-before clock we target tomorrow's window; otherwise today.
base = now_et if now_et < flat_today else now_et + timedelta(days=1)

start_dt = at(start_hh, start_mm, base)
wake_dt = start_dt - timedelta(minutes=wake_early_min)
range_start = at(rs_hh, rs_mm, base)
range_end = at(re_hh, re_mm, base)
flat_before = at(fb_hh, fb_mm, base)

print(
    int(wake_dt.timestamp()),
    int(start_dt.timestamp()),
    int(range_start.timestamp()),
    int(range_end.timestamp()),
    int(flat_before.timestamp()),
    tz_name,
)
PY
); then
    echo "Error: failed to compute schedule from ${TOML_PATH}"
    exit 1
fi

read -r WAKE_TS START_TS RANGE_START_TS RANGE_END_TS FLAT_BEFORE_TS SCHED_TZ <<<"$SCHEDULE"

# ---------- formatting helpers ------------------------------------------------
fmt_et() {
    "$PY" - "$1" "$SCHED_TZ" <<'PY'
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

ts = int(sys.argv[1])
tz = ZoneInfo(sys.argv[2])
print(datetime.fromtimestamp(ts, tz).strftime("%a %b %d  %H:%M %Z"))
PY
}

fmt_local() {
    "$PY" - "$1" <<'PY'
import sys
from datetime import datetime

ts = int(sys.argv[1])
print(datetime.fromtimestamp(ts).astimezone().strftime("%a %b %d  %H:%M %Z"))
PY
}

human_hms() {
    local diff=$1
    [ "$diff" -lt 0 ] && diff=0
    local h=$((diff / 3600))
    local m=$(((diff % 3600) / 60))
    local s=$((diff % 60))
    printf "%02d:%02d:%02d" "$h" "$m" "$s"
}

# ---------- decide whether to wait -------------------------------------------
NOW_TS=$(date +%s)
SKIP_WAIT=0
if [ "${NO_WAIT:-0}" = "1" ]; then
    SKIP_WAIT=1
fi
# Already inside [start_time, flat_before) ET window? -> launch now (cron-friendly).
if [ "$NOW_TS" -ge "$START_TS" ] && [ "$NOW_TS" -lt "$FLAT_BEFORE_TS" ]; then
    SKIP_WAIT=1
fi

# ---------- header ------------------------------------------------------------
cat <<EOF

+------------------------------------------------------------------+
|   morning_range_reversion  -  headless executor schedule         |
+------------------------------------------------------------------+
  account select  : ${ACCOUNT_NUM}
  symbols         : ${SYMBOLS}
  TOML config     : ${TOML_PATH#${PROJECT_ROOT}/}
  log file        : ${LOG_FILE#${PROJECT_ROOT}/}

  wake-up         : $(fmt_et "$WAKE_TS")   ($(fmt_local "$WAKE_TS"))
  strategy window : $(fmt_et "$START_TS")  ->  $(fmt_et "$FLAT_BEFORE_TS")
  anchor build    : $(fmt_et "$RANGE_START_TS")  ->  $(fmt_et "$RANGE_END_TS")
  fade watch from : $(fmt_et "$RANGE_END_TS")
EOF

# ---------- countdown ---------------------------------------------------------
if [ "$SKIP_WAIT" -eq 1 ]; then
    echo
    echo "  in trading window (or NO_WAIT/--now set) -> launching executor now."
    echo
else
    echo
    echo "  press Ctrl+C any time to cancel.  caffeinate keeps the Mac awake."
    echo
    trap 'printf "\n  countdown interrupted - executor NOT launched.\n"; exit 130' INT

    SPINNER='|/-\'
    TICK=0
    LAST_MINUTE=-1

    while :; do
        NOW_TS=$(date +%s)
        REMAIN=$((WAKE_TS - NOW_TS))
        if [ "$REMAIN" -le 0 ]; then
            break
        fi

        FRAME=${SPINNER:$((TICK % 4)):1}
        TICK=$((TICK + 1))

        # Closest contextual milestone after wake-up.
        if [ "$NOW_TS" -lt "$RANGE_START_TS" ]; then
            CTX="anchor opens in  $(human_hms $((RANGE_START_TS - NOW_TS)))"
        elif [ "$NOW_TS" -lt "$RANGE_END_TS" ]; then
            CTX="anchor closes in $(human_hms $((RANGE_END_TS - NOW_TS)))  (fades arm after)"
        elif [ "$NOW_TS" -lt "$FLAT_BEFORE_TS" ]; then
            CTX="flat-before in   $(human_hms $((FLAT_BEFORE_TS - NOW_TS)))"
        else
            CTX="post-session window"
        fi

        if [ -t 1 ]; then
            # Interactive TTY: redraw a single status line.
            printf "\r\033[2K  %s  T-%s until wake-up   |   %s" \
                "$FRAME" "$(human_hms "$REMAIN")" "$CTX"
        else
            # Non-TTY (nohup / cron / pipe): log a fresh line each minute.
            MIN=$((REMAIN / 60))
            if [ "$MIN" -ne "$LAST_MINUTE" ]; then
                printf "  T-%s until wake-up  |  %s\n" \
                    "$(human_hms "$REMAIN")" "$CTX"
                LAST_MINUTE=$MIN
            fi
        fi
        sleep 1
    done

    if [ -t 1 ]; then
        printf "\r\033[2K"
    fi
    trap - INT
    DRIFT=$(( $(date +%s) - WAKE_TS ))
    echo "  wake-up reached ($(fmt_et "$WAKE_TS"), drift +${DRIFT}s) - launching executor."
    echo
fi

# ---------- launch headless executor ------------------------------------------
export LOG_FILE

# Surface morning_range_reversion lifecycle INFO logs (🌅 new session, 📐 anchor
# range built, 🔁 backfill from history, 🎯 SHORT/LONG, ⏰ deadline reached,
# 🛑 max_fades reached) to the terminal as well as the file. WARNING/ERROR from
# every other module continue to print as before; INFO from anything ELSE stays
# file-only. ``core.logging_setup`` picks this env var up automatically.
export LIFECYCLE_LOGGERS="${LIFECYCLE_LOGGERS:-strategies.morning_range_reversion_strategy}"

cd "$PROJECT_ROOT"

# max_pending=2: room for hybrid stop-entry + OCO siblings (same tuning as body_reversion).
# max_quantity mirrors the committed per-symbol position_size in
# ``config/strategies/morning_range_reversion.toml``: MNQ=4 (Round-24 2× weighting),
# MES/MGC=2 (root ``[risk] position_size = 2``).  Set higher than the TOML qty would
# silently allow accidental over-sizing; setting lower would silently throttle the
# strategy's committed sizing.  Keep these in sync when the TOML is re-tuned.
exec caffeinate -dimsu "$PY" core/strategy_executor.py \
  --strategy=morning_range_reversion \
  --symbols="${SYMBOLS}" \
  --account_select="${ACCOUNT_NUM}" \
  --risk-config '{"MNQ":{"max_quantity":4,"cooldown":60.0,"max_pending":2},"MES":{"max_quantity":2,"cooldown":60.0,"max_pending":2},"MGC":{"max_quantity":2,"cooldown":60.0,"max_pending":2}}'
