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

# ── Discord + secrets from .env (2026-06-17) ─────────────────────────────────
# launchd agents inherit a minimal environment; sourcing .env here mirrors
# interactive runs and ensures DISCORD_WEBHOOK_URL / PROJECT_X_* reach the
# executor (``import load_env`` in trading_bot.py is a second belt).
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

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
PID=$$
LOG_FILE="${LOG_DIR}/morning_range_reversion_account${ACCOUNT_NUM}_${TIMESTAMP}_${PID}.log"

# 2026-05-31 Round-25: MES dropped from active rotation (no-MES variant
# Pareto-improved 6m/9m RF in the round-24 weighting sweep).  Override with
# ``MORNING_RANGE_SYMBOLS=MNQ,MES,MGC`` to re-enable MES without editing.
SYMBOLS="${MORNING_RANGE_SYMBOLS:-MNQ,MGC}"
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

# ── Session-end clean-exit clause (2026-06-15 daily-launchd migration) ──────
# When the wrapper is invoked by launchd (one execution per trading day, see
# scripts/launchd/) we want it to exit cleanly some grace period after
# ``flat_before`` ET rather than supervise-loop forever.  ``flat_before`` is
# already the strategy's force-flatten time (default 16:00 ET); we wait
# ``SESSION_EXIT_GRACE_MIN`` extra minutes for in-flight cancels / log
# flushes to complete, then SIGTERM the executor and break the supervise
# loop with exit code 0.  launchd picks up the next scheduled wake tomorrow.
#
# Set ``SESSION_EXIT_GRACE_MIN=0`` to disable (i.e. keep the legacy always-on
# behaviour — supervise forever).  Negative values are clamped to 0.
#
# Defined HERE (not in the supervise-loop tunables block lower down) so the
# schedule banner immediately below can show the cutoff time AND because
# ``set -u`` would crash on the banner's reference otherwise.  Regression
# bug fixed 2026-06-16: SESSION_EXIT_GRACE_MIN was previously declared after
# the banner, so launchd-driven runs crashed with "unbound variable" at
# line 211 and never reached the executor.
SESSION_EXIT_GRACE_MIN="${SESSION_EXIT_GRACE_MIN:-30}"
if [ "$SESSION_EXIT_GRACE_MIN" -lt 0 ] 2>/dev/null; then
    SESSION_EXIT_GRACE_MIN=0
fi
if [ "$SESSION_EXIT_GRACE_MIN" -gt 0 ] 2>/dev/null; then
    SESSION_EXIT_TS=$((FLAT_BEFORE_TS + SESSION_EXIT_GRACE_MIN * 60))
else
    # 0 = legacy always-on (no cutoff).  Use a far-future sentinel so the
    # ``session_ended`` predicate never returns true.
    SESSION_EXIT_TS=$(( $(date +%s) + 365 * 24 * 3600 ))
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

# Print session-end cutoff banner (only when daily-handoff mode is active).
# When SESSION_EXIT_GRACE_MIN=0 the cutoff is a year out; suppress that.
if [ "$SESSION_EXIT_GRACE_MIN" -gt 0 ] 2>/dev/null; then
    cat <<EOF
  session-end exit: $(fmt_et "$SESSION_EXIT_TS")  (grace ${SESSION_EXIT_GRACE_MIN}min after flat_before)
EOF
fi

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

# ── Daily log rotation (2026-06-15 fix) ──────────────────────────────────────
# When the wrapper is left running across multiple sessions, the default size-
# based ``RotatingFileHandler`` will accumulate one big file with 5 rotated
# siblings and you lose the ability to grep "what happened Tuesday".  Setting
# ``LOG_ROTATE_DAILY=1`` flips ``core/logging_setup`` to use a
# ``TimedRotatingFileHandler`` instead, rotating at local midnight and naming
# rolled files ``<base>.YYYY-MM-DD`` — one archive per trading day.
export LOG_ROTATE_DAILY="${LOG_ROTATE_DAILY:-1}"

# ── Hang watchdog heartbeat file (2026-06-15 fix) ────────────────────────────
# ``core/strategy_executor._heartbeat_loop`` touches this file every 30 s.
# The watchdog loop launched below checks the file's mtime every minute and
# SIGTERMs the executor if it goes stale — the supervise loop then respawns
# a fresh executor.  Catches the "Python alive but asyncio loop wedged"
# failure mode that the bare exit-code check can't see.
HEARTBEAT_FILE="${HEARTBEAT_FILE:-${LOG_FILE}.heartbeat}"
export HEARTBEAT_FILE

# ── Correlated-cluster same-direction cap (2026-06-16 fix) ───────────────────
# ``core.risk_management._check_correlated_same_direction_limit`` blocks the
# total same-direction exposure across the {MNQ, MES, MYM, M2K} micro-equity
# cluster when it would exceed ``CORRELATED_MAX_SAME_DIRECTION_CONTRACTS``
# (default 2).  MRR's MNQ sizing is 4 contracts (Round-24 2× weighting in
# ``config/strategies/morning_range_reversion.toml``), so the default-2 cap
# blocks every MRR fade — observed on 2026-06-16 when the wrapper finally
# launched the executor after the SESSION_EXIT_GRACE_MIN hotfix.
#
# Setting cap=4 here lets MRR size to its committed 4 MNQ contracts while
# still capping the CLUSTER total — so if another strategy also runs on this
# account and tries to BUY MES while MRR is already at +4 MNQ, the second
# order is still blocked because the cluster total would exceed 4.  This
# preserves the cross-correlation guard's original intent without throttling
# MRR's per-symbol sizing.
#
# To restore the default cap, set CORRELATED_MAX_SAME_DIRECTION_CONTRACTS=2
# before invoking the wrapper.  To disable the guard entirely, set
# CORRELATED_EXPOSURE_GUARD=false.
export CORRELATED_MAX_SAME_DIRECTION_CONTRACTS="${CORRELATED_MAX_SAME_DIRECTION_CONTRACTS:-4}"

# ── Headless executor tuning (2026-06-17) ────────────────────────────────────
# Disable GUI WebSocket fan-out in launchd/headless mode — the reconnect
# storm at 07:13 ET on 2026-06-17 starved the asyncio loop and coincided
# with SignalR going zombie.  Master GUI can still tail log files.
export STRATEGY_EXECUTOR_GUI_WS="${STRATEGY_EXECUTOR_GUI_WS:-0}"

# 15-minute dual-path liveness (quote OR bar must be fresh).  Matches
# ``signal.max_bar_staleness_seconds = 900`` in the MRR TOML.
export DATA_FEED_MARKET_HUB_MAX_SILENCE_S="${DATA_FEED_MARKET_HUB_MAX_SILENCE_S:-900}"
export DATA_FEED_HEALTH_SEVERE_SILENCE_S="${DATA_FEED_HEALTH_SEVERE_SILENCE_S:-900}"

# Brackets stay on the broker until filled or session-end flatten — do NOT
# cancel working stop-entries when local SignalR hiccups (2026-06-17).
# Set DATA_FEED_CANCEL_ON_STALENESS=true only for strategies needing the
# 2026-06-11 blind-fill protection (continuous-monitoring styles).
export DATA_FEED_CANCEL_ON_STALENESS="${DATA_FEED_CANCEL_ON_STALENESS:-false}"

# Discord: fills/signals/closes via trading_bot.discord_notifier when
# DISCORD_WEBHOOK_URL is set.  Periodic status + feed-down watchdog alerts:
export DISCORD_STATUS_INTERVAL_SECONDS="${DISCORD_STATUS_INTERVAL_SECONDS:-1800}"
export DATA_FEED_DISCORD_ALERTS="${DATA_FEED_DISCORD_ALERTS:-true}"
export DATA_FEED_DISCORD_ALERT_COOLDOWN_S="${DATA_FEED_DISCORD_ALERT_COOLDOWN_S:-900}"

# Logging: daily rotation (one file per day) + cap asyncio aiohttp leak spam.
export LOG_SUPPRESS_ASYNCIO_SESSION_ERRORS="${LOG_SUPPRESS_ASYNCIO_SESSION_ERRORS:-1}"
export LOG_MAX_BYTES="${LOG_MAX_BYTES:-52428800}"

# Health gate: warn on mild transients, refuse only at severe (15m) silence.
# MRR analyze() already skips stale bars; placement gate lives in place_bracket.
export DATA_FEED_HEALTH_GATE_MODE="${DATA_FEED_HEALTH_GATE_MODE:-warn}"

cd "$PROJECT_ROOT"

# ── Supervise loop (2026-06-15 fix) ──────────────────────────────────────────
# Originally this script did ``exec caffeinate -dimsu …``, replacing the
# wrapper PID with caffeinate so a Python crash killed the whole instance.
# For unattended week-long runs we need crash recovery: the executor restarts
# automatically on non-clean exits, with exponential backoff and a max-restart
# cap so a tight crash loop can't burn through API quota.
#
# Topology:
#   wrapper PID                 (this script — supervisor)
#     └── caffeinate -w $$ &    (keeps mac awake until wrapper exits)
#     └── python executor       (looped; respawned on non-zero exit)
#
# Tunables (override via env when calling):
#   MAX_RESTARTS    default 10  — hard cap before the wrapper bails out
#   RESET_AFTER_SEC default 3600 — uptime past this resets the restart counter
#   BACKOFF_INITIAL default 5   — seconds for the first restart wait
#   BACKOFF_MAX     default 300 — exponential backoff cap (5min)

MAX_RESTARTS="${MAX_RESTARTS:-10}"
RESET_AFTER_SEC="${RESET_AFTER_SEC:-3600}"
BACKOFF_INITIAL="${BACKOFF_INITIAL:-5}"
BACKOFF_MAX="${BACKOFF_MAX:-300}"
# Hang watchdog tunables (2026-06-15).  ``HANG_THRESHOLD_SEC`` is how long the
# heartbeat file can go un-touched before we declare a hang; the executor
# touches it every 30 s, so 300 s = 10 missed heartbeats is conservative but
# leaves room for transient GC pauses / slow disk I/O.
HANG_THRESHOLD_SEC="${HANG_THRESHOLD_SEC:-300}"
HANG_CHECK_INTERVAL_SEC="${HANG_CHECK_INTERVAL_SEC:-60}"

# NOTE: SESSION_EXIT_GRACE_MIN / SESSION_EXIT_TS are defined earlier in the
# script (right after FLAT_BEFORE_TS is computed) so the schedule banner can
# print the cutoff time.  Don't redefine them here.

# Background caffeinate, tied to wrapper PID via ``-w $$`` so it dies cleanly
# when the wrapper itself exits (success, error, or Ctrl+C all cleaned up).
caffeinate -dimsu -w $$ &
CAFFEINATE_PID=$!

# Ctrl+C and SIGTERM should kill the running executor + caffeinate, not just
# the wrapper.  ``trap`` captures the signal and forwards it down the tree.
# ── Hang watchdog helper ─────────────────────────────────────────────────────
# Returns the heartbeat file's mtime in epoch seconds, or 0 if missing.  Uses
# GNU/BSD-stat compatible variants so this works on Linux + macOS.
heartbeat_mtime() {
    if [ ! -f "$HEARTBEAT_FILE" ]; then
        echo 0
        return
    fi
    # macOS / BSD: stat -f %m ;  GNU/Linux: stat -c %Y.  Try BSD first since
    # we're targeting darwin per the wrapper's primary deployment.
    stat -f %m "$HEARTBEAT_FILE" 2>/dev/null \
        || stat -c %Y "$HEARTBEAT_FILE" 2>/dev/null \
        || echo 0
}

# Predicate: are we past the session-end cutoff?  Used by the supervise
# loop to break cleanly after ``flat_before + grace`` so launchd can take
# over for tomorrow's session.  Always returns false when
# SESSION_EXIT_GRACE_MIN=0 (sentinel is a year in the future).
session_ended() {
    local now
    now=$(date +%s)
    [ "$now" -ge "$SESSION_EXIT_TS" ]
}

# Background loop: SIGTERMs the executor at SESSION_EXIT_TS.  Spawned
# alongside the hang watchdog for each executor iteration so the target PID
# is always the current incarnation.  No-op when SESSION_EXIT_GRACE_MIN=0
# (sentinel far in the future) — falls through to the natural target-exit
# path without ever firing.
session_exit_timer_loop() {
    local target_pid="$1"
    local exit_at="$2"
    while kill -0 "$target_pid" 2>/dev/null; do
        local now
        now=$(date +%s)
        if [ "$now" -ge "$exit_at" ]; then
            local msg="⏰ session-end cutoff reached (grace=${SESSION_EXIT_GRACE_MIN}min) - SIGTERM pid ${target_pid} for clean daily handoff"
            echo "  $msg"
            printf "%s\n" "$msg" >> "$LOG_FILE" 2>/dev/null || true
            kill -TERM "$target_pid" 2>/dev/null || true
            return
        fi
        sleep 30
    done
}

# Background loop: polls the heartbeat file every $HANG_CHECK_INTERVAL_SEC.
# When the file is older than $HANG_THRESHOLD_SEC, sends SIGTERM to the
# executor PID stored in $WATCHDOG_TARGET_PID (set fresh before each launch)
# and exits.  A new watchdog is spawned for each executor iteration so the
# target PID is always correct.
hang_watchdog_loop() {
    local target_pid="$1"
    local log_file_for_warn="$2"
    # Brief startup grace — first heartbeat may take a few seconds after
    # process launch.
    sleep "$HANG_CHECK_INTERVAL_SEC"
    while kill -0 "$target_pid" 2>/dev/null; do
        local mtime
        mtime=$(heartbeat_mtime)
        local now
        now=$(date +%s)
        local age=$((now - mtime))
        # mtime==0 means the file hasn't been created yet — could be a slow
        # startup OR a hang during init.  Only consider it stale if the
        # executor has been alive longer than the threshold.
        if [ "$mtime" -gt 0 ] && [ "$age" -gt "$HANG_THRESHOLD_SEC" ]; then
            local msg="⚠️  hang watchdog: heartbeat ${age}s stale (>${HANG_THRESHOLD_SEC}s) - SIGTERM pid ${target_pid}"
            echo "  $msg"
            # Also append to the log so the operator can correlate at restart.
            printf "%s\n" "$msg" >> "$log_file_for_warn" 2>/dev/null || true
            kill -TERM "$target_pid" 2>/dev/null || true
            return
        fi
        sleep "$HANG_CHECK_INTERVAL_SEC"
    done
}

shutdown() {
    local sig="${1:-EXIT}"
    # Only narrate on signal-driven shutdown; the EXIT trap also runs on a
    # clean ``break`` out of the supervise loop and we don't want to spam the
    # operator with "wrapper received EXIT" every time the executor exits 0.
    if [ "$sig" != "EXIT" ]; then
        echo
        echo "  wrapper received ${sig} - shutting down executor + caffeinate"
    fi
    if [ -n "${WATCHDOG_PID:-}" ] && kill -0 "$WATCHDOG_PID" 2>/dev/null; then
        kill -TERM "$WATCHDOG_PID" 2>/dev/null || true
    fi
    if [ -n "${SESSION_TIMER_PID:-}" ] && kill -0 "$SESSION_TIMER_PID" 2>/dev/null; then
        kill -TERM "$SESSION_TIMER_PID" 2>/dev/null || true
    fi
    if [ -n "${EXEC_PID:-}" ] && kill -0 "$EXEC_PID" 2>/dev/null; then
        kill -TERM "$EXEC_PID" 2>/dev/null || true
        # Give the executor 10s to flush its log + cancel the SignalR conn.
        for _ in 1 2 3 4 5 6 7 8 9 10; do
            kill -0 "$EXEC_PID" 2>/dev/null || break
            sleep 1
        done
        kill -KILL "$EXEC_PID" 2>/dev/null || true
    fi
    if [ -n "${CAFFEINATE_PID:-}" ] && kill -0 "$CAFFEINATE_PID" 2>/dev/null; then
        kill -TERM "$CAFFEINATE_PID" 2>/dev/null || true
    fi
    # Clean up stale heartbeat file so a stopped wrapper doesn't leave a
    # phantom liveness signal lying around.
    if [ -n "${HEARTBEAT_FILE:-}" ] && [ -f "$HEARTBEAT_FILE" ]; then
        rm -f "$HEARTBEAT_FILE" 2>/dev/null || true
    fi
    if [ "$sig" = "INT" ] || [ "$sig" = "TERM" ]; then
        exit 130
    fi
}
trap 'shutdown INT' INT
trap 'shutdown TERM' TERM
trap 'shutdown EXIT' EXIT

restart_count=0
backoff="$BACKOFF_INITIAL"

while :; do
    # Don't re-launch the executor if we've already crossed the session-end
    # cutoff while waiting in backoff.  This catches the edge case where a
    # late-day crash + restart-cap delay pushes us past the daily exit.
    if session_ended; then
        echo "  ⏰ session-end cutoff already crossed before launch — wrapper exiting cleanly"
        break
    fi

    start_ts=$(date +%s)
    echo "  → launching strategy_executor (attempt $((restart_count + 1)) / $((MAX_RESTARTS + 1)))"

    # Clear any stale heartbeat from a previous iteration so the watchdog's
    # ``mtime==0`` startup grace kicks in fresh.  ``rm -f`` is no-op if absent.
    rm -f "$HEARTBEAT_FILE" 2>/dev/null || true

    # max_pending=2: room for hybrid stop-entry + OCO siblings (same tuning as body_reversion).
    # max_quantity mirrors the committed per-symbol position_size in
    # ``config/strategies/morning_range_reversion.toml``: MNQ=4 (Round-24 2× weighting),
    # MES/MGC=2 (root ``[risk] position_size = 2``).  Set higher than the TOML qty would
    # silently allow accidental over-sizing; setting lower would silently throttle the
    # strategy's committed sizing.  Keep these in sync when the TOML is re-tuned.
    "$PY" core/strategy_executor.py \
        --strategy=morning_range_reversion \
        --symbols="${SYMBOLS}" \
        --account_select="${ACCOUNT_NUM}" \
        --risk-config '{"MNQ":{"max_quantity":4,"cooldown":60.0,"max_pending":2},"MES":{"max_quantity":2,"cooldown":60.0,"max_pending":2},"MGC":{"max_quantity":2,"cooldown":60.0,"max_pending":2}}' &
    EXEC_PID=$!

    # Spawn a fresh hang watchdog targeting THIS executor instance.
    hang_watchdog_loop "$EXEC_PID" "$LOG_FILE" &
    WATCHDOG_PID=$!

    # Spawn the session-end timer (no-op when SESSION_EXIT_GRACE_MIN=0 / sentinel).
    # SIGTERMs the executor at SESSION_EXIT_TS so the supervise loop can exit
    # cleanly for the daily launchd handoff.
    session_exit_timer_loop "$EXEC_PID" "$SESSION_EXIT_TS" &
    SESSION_TIMER_PID=$!

    # ``wait`` returns the child's exit status; wrap in ``if`` so set -e doesn't
    # trip on non-zero exits (we capture and act on them below).
    if wait "$EXEC_PID"; then
        EXIT_CODE=0
    else
        EXIT_CODE=$?
    fi
    EXEC_PID=""

    # Tear down both supervisors before evaluating the exit code so we don't
    # have stray background loops checking against now-defunct PIDs.
    if [ -n "${WATCHDOG_PID:-}" ] && kill -0 "$WATCHDOG_PID" 2>/dev/null; then
        kill -TERM "$WATCHDOG_PID" 2>/dev/null || true
        wait "$WATCHDOG_PID" 2>/dev/null || true
    fi
    WATCHDOG_PID=""
    if [ -n "${SESSION_TIMER_PID:-}" ] && kill -0 "$SESSION_TIMER_PID" 2>/dev/null; then
        kill -TERM "$SESSION_TIMER_PID" 2>/dev/null || true
        wait "$SESSION_TIMER_PID" 2>/dev/null || true
    fi
    SESSION_TIMER_PID=""

    end_ts=$(date +%s)
    uptime=$((end_ts - start_ts))

    # ── Daily-handoff exit: if we're past the cutoff, break cleanly.
    # Covers both the "session timer killed the executor" case (EXIT_CODE=143)
    # and the "executor exited naturally after we crossed the cutoff" case.
    if session_ended; then
        echo "  ⏰ session-end cutoff reached (code=${EXIT_CODE}, uptime=${uptime}s) - wrapper exiting cleanly for daily handoff."
        break
    fi

    # Clean exit (0) or operator-initiated stop (130 = SIGINT, 143 = SIGTERM):
    # treat as intentional shutdown and break the supervise loop.
    if [ "$EXIT_CODE" -eq 0 ] || [ "$EXIT_CODE" -eq 130 ] || [ "$EXIT_CODE" -eq 143 ]; then
        echo "  executor exited cleanly (code=${EXIT_CODE}, uptime=${uptime}s) - supervisor done."
        # Clear EXIT trap (we're exiting voluntarily, caffeinate will be reaped
        # by the EXIT handler regardless).
        break
    fi

    # Long uptime → assume the crash is transient (e.g. broker hiccup hours in).
    # Reset the restart counter so we don't trip the cap on legitimate long runs.
    if [ "$uptime" -ge "$RESET_AFTER_SEC" ]; then
        echo "  executor ran ${uptime}s before crash - resetting restart counter."
        restart_count=0
        backoff="$BACKOFF_INITIAL"
    fi

    restart_count=$((restart_count + 1))
    if [ "$restart_count" -gt "$MAX_RESTARTS" ]; then
        echo
        echo "  ❌ executor crashed ${restart_count} times in quick succession (last exit=${EXIT_CODE})."
        echo "     Hitting MAX_RESTARTS=${MAX_RESTARTS} - bailing out so an operator can investigate."
        echo "     See logs in: ${LOG_DIR#${PROJECT_ROOT}/}/"
        exit 2
    fi

    echo "  ⚠️  executor crashed (code=${EXIT_CODE}, uptime=${uptime}s) - restarting in ${backoff}s [${restart_count}/${MAX_RESTARTS}]"
    sleep "$backoff"
    # Exponential backoff with cap (5s → 10 → 20 → 40 → 80 → 160 → 300 → 300 …)
    backoff=$((backoff * 2))
    if [ "$backoff" -gt "$BACKOFF_MAX" ]; then
        backoff="$BACKOFF_MAX"
    fi
done
