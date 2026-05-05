#!/usr/bin/env bash
# Pathway 1 — overnight_range filter sweep (env overrides only; live TOML untouched).
# Writes one JSON per step under docs/perf/sweeps/ + a tiny summary table.
#
# Required:
#   CSV=historical_data/price/MNQ_1m_complete.csv
# Optional:
#   SYMBOL=MNQ TF=1m START=2025-12-24 END=2026-04-30 OUT_DIR=docs/perf/sweeps STEPS="P0 P1 P2 P3 P4 P5"
#
# Steps:
#   P0  baseline (live TOML, no overrides)
#   P1  skip_weekdays = []
#   P2  + volatility filter OFF
#   P3  + gap filter OFF
#   P4  + range_size filter OFF (≈ "all filters off")
#   P5  all filters ON, but widened bands (range 30..600, gap 250, atr 15..220)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="${ROOT}/.venv/bin/python"
[[ -x "$PY" ]] || PY=python3

CSV="${CSV:-historical_data/price/MNQ_1m_complete.csv}"
SYMBOL="${SYMBOL:-MNQ}"
TF="${TF:-1m}"
START="${START:-2025-12-24}"
END="${END:-2026-04-30}"
OUT_DIR="${OUT_DIR:-docs/perf/sweeps}"
STEPS="${STEPS:-P0 P1 P2 P3 P4 P5}"

mkdir -p "$OUT_DIR"
SUMMARY="${OUT_DIR}/overnight_range_${SYMBOL}_sweep_summary.tsv"
echo -e "step\tdescription\ttrades\ttotal_pnl\twin_rate\tsharpe\tmax_dd" > "$SUMMARY"

run_step() {
  local step="$1" desc="$2" out="${OUT_DIR}/overnight_range_${SYMBOL}_${step}.json"
  shift 2
  echo "=== ${step}: ${desc} ==="
  env "$@" "$PY" core/backtest_executor.py --strategy=overnight_range \
      --symbol="$SYMBOL" --timeframe="$TF" --csv="$CSV" \
      --start="$START" --end="$END" \
      --format=json --include-trades 2>/dev/null > "$out"
  "$PY" - "$out" "$step" "$desc" "$SUMMARY" <<'PY'
import json, sys
out, step, desc, summary = sys.argv[1:]
last = open(out).read().strip().splitlines()[-1]
d = json.loads(last)
r = d.get("result") or {}
row = [step, desc, str(r.get("total_trades", 0)), f"{r.get('total_pnl',0):.2f}",
       f"{r.get('win_rate',0):.2f}", f"{(r.get('sharpe_ratio') or 0):.2f}",
       f"{(r.get('max_drawdown') or 0):.2f}"]
print("  →", "\t".join(row))
with open(summary, "a") as f:
    f.write("\t".join(row) + "\n")
PY
}

for step in $STEPS; do
  case "$step" in
    P0) run_step P0 "baseline (live TOML)" ;;
    P1) run_step P1 "skip_weekdays=[]" \
          OVERNIGHT_RANGE_FILTERS_SKIP_WEEKDAYS="" ;;
    P2) run_step P2 "skip_weekdays=[] + volatility=false" \
          OVERNIGHT_RANGE_FILTERS_SKIP_WEEKDAYS="" \
          OVERNIGHT_RANGE_FILTERS_VOLATILITY=false ;;
    P3) run_step P3 "skip_weekdays=[] + volatility=false + gap=false" \
          OVERNIGHT_RANGE_FILTERS_SKIP_WEEKDAYS="" \
          OVERNIGHT_RANGE_FILTERS_VOLATILITY=false \
          OVERNIGHT_RANGE_FILTERS_GAP=false ;;
    P4) run_step P4 "all filters off" \
          OVERNIGHT_RANGE_FILTERS_SKIP_WEEKDAYS="" \
          OVERNIGHT_RANGE_FILTERS_VOLATILITY=false \
          OVERNIGHT_RANGE_FILTERS_GAP=false \
          OVERNIGHT_RANGE_FILTERS_RANGE_SIZE=false \
          OVERNIGHT_RANGE_FILTERS_DLL_PROXIMITY=false ;;
    P5) run_step P5 "filters on, widened bands" \
          OVERNIGHT_RANGE_FILTERS_SKIP_WEEKDAYS="" \
          OVERNIGHT_RANGE_FILTERS_RANGE_MIN_PTS=30 \
          OVERNIGHT_RANGE_FILTERS_RANGE_MAX_PTS=600 \
          OVERNIGHT_RANGE_FILTERS_GAP_MAX_PTS=250 \
          OVERNIGHT_RANGE_FILTERS_ATR_MIN=15 \
          OVERNIGHT_RANGE_FILTERS_ATR_MAX=220 ;;
    *) echo "Unknown step: $step" >&2; exit 2 ;;
  esac
done

echo
echo "=== Summary: $SUMMARY ==="
column -t -s $'\t' "$SUMMARY"
