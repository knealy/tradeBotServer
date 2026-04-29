#!/usr/bin/env bash
# Stage-1 short screening run; promote survivors to a longer window manually or via second invocation.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
SCREEN_DAYS="${SCREEN_DAYS:-7}"
FULL_DAYS="${FULL_DAYS:-30}"
python3 -m core.research.runner \
  --strategy "${STRATEGY:-ma_crossover}" \
  --symbol "${SYMBOL:-MNQ}" \
  --timeframe "${TIMEFRAME:-5m}" \
  --screen-days "$SCREEN_DAYS" \
  --full-days "$FULL_DAYS" \
  --days "$FULL_DAYS" \
  --grid "${GRID:-}" \
  --mc "${MC:-100}" \
  --run-tag "${RUN_TAG:-screen}" \
  "$@"
