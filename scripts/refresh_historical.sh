#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Refresh canonical historical CSVs (1m + 5m databento format) for MES/MNQ/MGC.
#
# WHAT THIS DOES
#   1. Reports CURRENT staleness of every ``*_databento.csv`` under
#      ``historical_data/price/`` (last bar timestamp vs UTC now).
#   2. Calls ``scripts/stitch_broker_history_to_databento_5m.py`` to append
#      fresh 5m bars from TopStepX REST onto each canonical 5m file.
#   3. Calls ``scripts/stitch_broker_history_to_databento_1m.py`` to do the
#      same for 1m bars.
#   4. Reports NEW staleness after refresh.
#
# WHY THIS EXISTS
#   The full Truth-mode simulator (2026-06-11) leans on 1m + 5m CSVs being
#   FRESH — stale CSVs mean sweep results are scored against a market regime
#   from days ago.  Easy to forget; this is the one-button refresh.
#
# USAGE
#   bash scripts/refresh_historical.sh             # full refresh
#   bash scripts/refresh_historical.sh --check     # report-only (no API calls)
#   bash scripts/refresh_historical.sh --1m-only   # skip the slower 1m stitch
#   bash scripts/refresh_historical.sh --5m-only   # only 5m (fastest sanity)
#   make refresh-data                              # idiomatic Makefile entry
#
# REQUIREMENTS
#   ``PROJECT_X_API_KEY`` + ``PROJECT_X_USERNAME`` in ``.env`` (or env).
#   ``ENABLE_SIGNALR=false`` is forced so SignalR doesn't start a live session.
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PY="${ROOT}/.venv/bin/python"
[[ ! -x "$PY" ]] && PY="python3"

CHECK_ONLY=0
SKIP_5M=0
SKIP_1M=0
SYMBOLS="MNQ,MES,MGC"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --check) CHECK_ONLY=1; shift ;;
        --5m-only) SKIP_1M=1; shift ;;
        --1m-only) SKIP_5M=1; shift ;;
        --symbols) SYMBOLS="$2"; shift 2 ;;
        -h|--help)
            sed -n '2,/^$/p' "$0" | sed 's/^# \{0,1\}//'
            exit 0 ;;
        *) echo "unknown arg: $1" >&2; exit 2 ;;
    esac
done

# Source .env (best-effort) so the stitchers see credentials.
if [[ -f "$ROOT/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    . "$ROOT/.env"
    set +a
fi

# ANSI colours (disable when not a TTY).
if [[ -t 1 ]]; then
    GREEN="\033[32m"; YELLOW="\033[33m"; RED="\033[31m"; DIM="\033[2m"; RESET="\033[0m"
else
    GREEN=""; YELLOW=""; RED=""; DIM=""; RESET=""
fi

report_staleness() {
    local label="$1"
    echo ""
    echo "  ────── ${label} ──────"
    "$PY" - <<'PY'
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent if "__file__" in dir() else Path.cwd()
PRICE = Path(os.environ["ROOT"]) / "historical_data" / "price"
syms = os.environ["SYMBOLS"].split(",")
now_utc = datetime.now(timezone.utc)
rows = []
for sym in syms:
    for tf in ("1m", "5m"):
        p = PRICE / f"{sym}_{tf}_databento.csv"
        if not p.is_file():
            rows.append((sym, tf, "MISSING", None))
            continue
        try:
            with p.open("rb") as fh:
                fh.seek(-4096, 2) if p.stat().st_size > 4096 else fh.seek(0)
                tail = fh.read().decode("utf-8", errors="ignore").strip().splitlines()
            last_line = tail[-1] if tail else ""
            ts_str = last_line.split(",", 1)[0]
            ts = datetime.fromisoformat(ts_str).replace(tzinfo=timezone.utc)
            age_h = (now_utc - ts).total_seconds() / 3600.0
            rows.append((sym, tf, ts.strftime("%Y-%m-%d %H:%M"), age_h))
        except Exception as exc:
            rows.append((sym, tf, f"ERR:{type(exc).__name__}", None))

def colour(age):
    if age is None:
        return "\033[31m"   # red for missing/err
    if age < 18:
        return "\033[32m"   # green: fresh (< 18h)
    if age < 72:
        return "\033[33m"   # yellow: 18-72h stale
    return "\033[31m"       # red: > 72h stale

for sym, tf, ts_str, age_h in rows:
    if age_h is None:
        print(f"    {sym}_{tf:<2}  {colour(age_h)}{ts_str:<25}\033[0m  ")
    else:
        print(f"    {sym}_{tf:<2}  {colour(age_h)}{ts_str}   ({age_h:5.1f}h stale)\033[0m")
PY
}

export ROOT
export SYMBOLS

echo "🔍 Historical-data staleness report"
report_staleness "BEFORE"

if [[ $CHECK_ONLY -eq 1 ]]; then
    echo ""
    echo "  ${DIM}--check passed; skipping refresh.${RESET}"
    exit 0
fi

# Credential sanity check (the stitchers exit 2 anyway, but front-load the error).
if [[ -z "${PROJECT_X_API_KEY:-}" && -z "${TOPSTEPX_API_KEY:-}" ]]; then
    echo ""
    echo -e "  ${RED}❌ PROJECT_X_API_KEY missing — cannot refresh.${RESET}"
    echo -e "  ${DIM}Set credentials in .env then re-run, or use --check for offline staleness report.${RESET}"
    exit 2
fi

export ENABLE_SIGNALR=false

if [[ $SKIP_5M -eq 0 ]]; then
    echo ""
    echo "📥 Pulling fresh 5m bars …"
    "$PY" scripts/stitch_broker_history_to_databento_5m.py --symbols "$SYMBOLS" --chunk-days 30
fi

if [[ $SKIP_1M -eq 0 ]]; then
    echo ""
    echo "📥 Pulling fresh 1m bars …"
    "$PY" scripts/stitch_broker_history_to_databento_1m.py --symbols "$SYMBOLS" --chunk-days 10
fi

echo ""
echo "🔍 Historical-data staleness report"
report_staleness "AFTER"

echo ""
echo -e "  ${GREEN}✅ refresh complete${RESET}"
