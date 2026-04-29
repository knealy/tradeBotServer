#!/usr/bin/env bash
# Pull recent historical OHLCV bars from TopStepX (REST) into a CSV under historical_data/.
# Requires PROJECT_X_API_KEY and PROJECT_X_USERNAME (see .env.example).
#
# This wraps scripts/export_history.py. Date-range requests use the broker cap (~20k bars);
# for 1m data that is roughly up to ~14 days of RTH-heavy bars — use a shorter window or
# 5m if you need a longer calendar span without chunking.
#
# Usage:
#   bash scripts/pull_recent_topstepx_csv.sh
#   bash scripts/pull_recent_topstepx_csv.sh --symbol MNQ --timeframe 1m --days 7
#   bash scripts/pull_recent_topstepx_csv.sh --symbol MNQ --timeframe 5m --start 2026-01-01 --end 2026-04-01
#
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PYTHON="${PYTHON:-python3}"
if [[ -x "$ROOT/.venv/bin/python" ]]; then
  PYTHON="$ROOT/.venv/bin/python"
fi

SYMBOL="${SYMBOL:-MNQ}"
TIMEFRAME="${TIMEFRAME:-1m}"
DAYS="${DAYS:-14}"
START="${START:-}"
END="${END:-}"

# Parse overrides: pass-through anything after first -- to export_history
EXTRA=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --symbol)
      SYMBOL="$2"; shift 2 ;;
    --timeframe)
      TIMEFRAME="$2"; shift 2 ;;
    --days)
      DAYS="$2"; shift 2 ;;
    --start)
      START="$2"; shift 2 ;;
    --end)
      END="$2"; shift 2 ;;
    --)
      shift; EXTRA+=("$@"); break ;;
    *)
      echo "Unknown option: $1" >&2
      echo "Use: SYMBOL= MNQ TIMEFRAME=1m DAYS=14 bash $0  or  bash $0 --symbol MNQ --days 7" >&2
      exit 1 ;;
  esac
done

STAMP="$(date +%Y%m%d_%H%M%S)"
OUT="historical_data/${SYMBOL}_${TIMEFRAME}_recent_${STAMP}.csv"

ARGS=(--symbol="$SYMBOL" --timeframe="$TIMEFRAME" --output="$OUT")
if [[ -n "$START" && -n "$END" ]]; then
  ARGS+=(--start="$START" --end="$END")
elif [[ -n "$START" || -n "$END" ]]; then
  echo "Set both START and END (YYYY-MM-DD), or neither to use --days." >&2
  exit 1
else
  ARGS+=(--days="$DAYS")
fi

exec "$PYTHON" scripts/export_history.py "${ARGS[@]}" "${EXTRA[@]}"
