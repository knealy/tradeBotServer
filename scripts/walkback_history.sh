#!/usr/bin/env bash
# Walk back TopStepX 1m history per symbol in fixed windows until the API
# returns empty/near-empty responses N times in a row. Writes per-window CSVs
# into historical_data/price/walkback/<SYMBOL>/ which can later be merged via
# historical_data/csv_merger.py into a single complete CSV per symbol.
#
# Required: PROJECT_X_API_KEY + PROJECT_X_USERNAME in .env
#
# Env vars:
#   SYMBOLS="MES MGC"          space-separated futures roots
#   TIMEFRAME=1m               bar size
#   WINDOW_DAYS=30             days per pull (also used as --chunk-days)
#   END_DATE=                  YYYY-MM-DD; defaults to today (UTC)
#   MAX_WINDOWS=120            cap on walk-back length (~10 years at 30-day windows)
#   STOP_AFTER_EMPTY=2         stop after this many consecutive empty pulls
#   MIN_BARS=200               treat below this many bars as "near-empty"
#   OUT_ROOT=historical_data/price/walkback
#
# Note: each window produces a CSV named <SYMBOL>_<TF>_<from>_<to>.csv inside
#       walkback/<SYMBOL>/. After the run, merge with:
#   python historical_data/csv_merger.py walkback/MES/*.csv -o MES_1m_complete.csv
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
# Auto-source .env so PROJECT_X_API_KEY / PROJECT_X_USERNAME are available
# without forcing the operator to remember `set -a; source .env`.
if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  . .env
  set +a
fi
PY="${ROOT}/.venv/bin/python"
[[ -x "$PY" ]] || PY=python3

SYMBOLS="${SYMBOLS:-MES MGC}"
TIMEFRAME="${TIMEFRAME:-1m}"
WINDOW_DAYS="${WINDOW_DAYS:-30}"
END_DATE="${END_DATE:-$(date -u +%Y-%m-%d)}"
MAX_WINDOWS="${MAX_WINDOWS:-120}"
STOP_AFTER_EMPTY="${STOP_AFTER_EMPTY:-2}"
MIN_BARS="${MIN_BARS:-200}"
OUT_ROOT="${OUT_ROOT:-historical_data/price/walkback}"

date_minus_days() {
  local base="$1" delta="$2"
  if date -u -d "$base -$delta days" +%Y-%m-%d >/dev/null 2>&1; then
    date -u -d "$base -$delta days" +%Y-%m-%d
  else
    date -j -u -v-"$delta"d -f %Y-%m-%d "$base" +%Y-%m-%d 2>/dev/null
  fi
}

count_csv_rows() {
  local f="$1"
  if [[ ! -f "$f" ]]; then echo 0; return; fi
  local n; n=$(wc -l < "$f" | tr -d ' ')
  echo $(( n > 0 ? n - 1 : 0 ))
}

for SYM in $SYMBOLS; do
  out_dir="${OUT_ROOT}/${SYM}"
  mkdir -p "$out_dir"
  log="${out_dir}/_walkback.log"
  : > "$log"
  echo "=== $SYM walk-back (window=${WINDOW_DAYS}d, end=${END_DATE}) ==="
  cur_end="$END_DATE"
  empties=0
  for i in $(seq 1 "$MAX_WINDOWS"); do
    cur_start=$(date_minus_days "$cur_end" "$WINDOW_DAYS")
    out_csv="${out_dir}/${SYM}_${TIMEFRAME}_${cur_start}_${cur_end}.csv"
    if [[ -s "$out_csv" ]]; then
      rows=$(count_csv_rows "$out_csv")
      echo "  [$i] ${cur_start}..${cur_end} (cached) ${rows} bars" | tee -a "$log"
    else
      echo "  [$i] ${cur_start}..${cur_end} fetching…" | tee -a "$log"
      if ! "$PY" scripts/export_history.py \
            --symbol="$SYM" --timeframe="$TIMEFRAME" \
            --start="$cur_start" --end="$cur_end" \
            --chunk-days="$WINDOW_DAYS" \
            --output="$out_csv" >>"$log" 2>&1; then
        echo "    (export failed; see $log)" >&2
      fi
      rows=$(count_csv_rows "$out_csv")
      echo "    → ${rows} bars" | tee -a "$log"
    fi
    if (( rows < MIN_BARS )); then
      empties=$((empties+1))
      if (( empties >= STOP_AFTER_EMPTY )); then
        echo "  $SYM: ${empties} consecutive near-empty pulls — stopping walk-back" | tee -a "$log"
        break
      fi
    else
      empties=0
    fi
    cur_end="$cur_start"
  done

  merged="historical_data/price/${SYM}_1m_complete.csv"
  echo "  Merging $SYM → $merged"
  csvs=( "$out_dir"/*.csv )
  if (( ${#csvs[@]} == 0 )); then
    echo "  (no CSVs to merge for $SYM)" >&2
    continue
  fi
  "$PY" historical_data/csv_merger.py "${csvs[@]}" -o "$merged" || \
    echo "  merge failed for $SYM" >&2
  rows=$(count_csv_rows "$merged")
  echo "  $SYM merged: ${rows} bars → $merged" | tee -a "$log"
done

echo "=== Walk-back complete ==="
