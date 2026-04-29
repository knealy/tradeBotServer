#!/usr/bin/env bash
# Stitch a freshly exported CSV into the long-running MNQ archive (or any OHLCV archive)
# using historical_data/csv_merger.py. Duplicate bar timestamps keep the row from the
# *newer* file (the second argument), so list the archive first and the API pull second.
#
# Defaults assume the running log lives at historical_data/merged.csv (same layout as
# csv_merger.py output: timestamp,open,high,low,close,volume).
#
# Usage:
#   bash scripts/merge_into_merged_csv.sh historical_data/MNQ_1m_recent_20260428_120000.csv
#   ARCHIVE=/path/old.csv OUTPUT=/path/out.csv bash scripts/merge_into_merged_csv.sh recent.csv
#   MERGED_BACKUP=0 bash scripts/merge_into_merged_csv.sh recent.csv   # skip backup
#
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PYTHON="${PYTHON:-python3}"
if [[ -x "$ROOT/.venv/bin/python" ]]; then
  PYTHON="$ROOT/.venv/bin/python"
fi

ARCHIVE="${ARCHIVE:-$ROOT/historical_data/merged.csv}"
OUTPUT="${OUTPUT:-$ROOT/historical_data/merged.csv}"
MERGED_BACKUP="${MERGED_BACKUP:-1}"

RECENT="${1:-}"
if [[ -z "$RECENT" || ! -f "$RECENT" ]]; then
  echo "Usage: bash scripts/merge_into_merged_csv.sh <recent_export.csv>" >&2
  echo "  Fetches nothing — pass the CSV from pull_recent_topstepx_csv.sh (or export_history)." >&2
  echo "  ARCHIVE=$ARCHIVE (set ARCHIVE= to use only the recent file if no archive exists)" >&2
  exit 1
fi

if [[ "$MERGED_BACKUP" != "0" && -f "$OUTPUT" ]]; then
  BAK="${OUTPUT}.bak.$(date +%Y%m%d_%H%M%S)"
  cp -p "$OUTPUT" "$BAK"
  echo "Backed up existing merge -> $BAK"
fi

if [[ -f "$ARCHIVE" ]]; then
  "$PYTHON" historical_data/csv_merger.py "$ARCHIVE" "$RECENT" -o "$OUTPUT"
else
  echo "No archive at $ARCHIVE — writing normalized data from $RECENT only -> $OUTPUT"
  "$PYTHON" historical_data/csv_merger.py "$RECENT" -o "$OUTPUT"
fi
