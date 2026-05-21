#!/usr/bin/env bash
# Backtest a matrix of strategies × symbols on the same calendar window
# using already-exported 1m CSVs. Writes one JSON per (strategy, symbol)
# under docs/perf/sweeps/ + a TSV summary of headline metrics.
#
# Env vars (all optional):
#   START=2025-12-24
#   END=2026-04-30
#   SYMBOLS="MNQ MES MGC"
#   STRATS="overnight_range mean_reversion trend_following trend_scalping simple_momentum simple_rth"
#   CSV_DIR=historical_data/price
#   OUT_DIR=docs/perf/sweeps
#   TIMEFRAME=1m
#   CSV_TEMPLATE='${CSV_DIR}/${SYM}_1m_complete.csv'   # how to find the CSV per symbol
#   EXTRA_OVERRIDES=""                                  # e.g. "OVERNIGHT_RANGE_FILTERS_RANGE_SIZE=false ..."
#
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="${ROOT}/.venv/bin/python"
[[ -x "$PY" ]] || PY=python3

START="${START:-2025-12-24}"
END="${END:-2026-04-30}"
SYMBOLS="${SYMBOLS:-MNQ}"
STRATS="${STRATS:-overnight_range mean_reversion trend_following trend_scalping simple_momentum simple_rth}"
CSV_DIR="${CSV_DIR:-historical_data/price}"
OUT_DIR="${OUT_DIR:-docs/perf/sweeps}"
TIMEFRAME="${TIMEFRAME:-1m}"
TAG="${TAG:-}"
# CSV file pattern: %s placeholders for SYMBOL and CSV_DIR. Default looks like
# historical_data/price/MNQ_1m_complete.csv.
CSV_PATTERN="${CSV_PATTERN:-%CSV_DIR%/%SYM%_1m_complete.csv}"
EXTRA_OVERRIDES="${EXTRA_OVERRIDES:-}"
# Per-strategy hard timeout (seconds). Some replay-only strategies place orders
# every bar and can run indefinitely against months of 1m data; cap them here.
PER_RUN_TIMEOUT="${PER_RUN_TIMEOUT:-600}"

mkdir -p "$OUT_DIR"
SUMMARY="${OUT_DIR}/strategy_matrix${TAG:+_${TAG}}_summary.tsv"
echo -e "strategy\tsymbol\ttrades\ttotal_pnl\twin_rate\tsharpe\tmax_dd\tperiod" > "$SUMMARY"

for SYM in $SYMBOLS; do
  CSV="${CSV_PATTERN//%CSV_DIR%/$CSV_DIR}"
  CSV="${CSV//%SYM%/$SYM}"
  if [[ ! -f "$CSV" ]]; then
    echo "skip $SYM — CSV not found: $CSV" >&2
    continue
  fi
  for STRAT in $STRATS; do
    out="${OUT_DIR}/${STRAT}_${SYM}${TAG:+_${TAG}}.json"
    echo "=== $STRAT × $SYM (timeout ${PER_RUN_TIMEOUT}s) → $out ==="
    # Portable timeout via background-job + perl alarm wrapper. macOS does not
    # ship `timeout` / `gtimeout` by default, and we don't want a blocked
    # backtest to wedge the matrix run.
    # shellcheck disable=SC2086
    if ! eval env $EXTRA_OVERRIDES perl -e \
        '"alarm shift @ARGV; exec @ARGV"' "$PER_RUN_TIMEOUT" \
        "$PY" core/backtest_executor.py \
        --strategy="$STRAT" --symbol="$SYM" --timeframe="$TIMEFRAME" \
        --csv="$CSV" --start="$START" --end="$END" \
        --format=json --include-trades --replay 2>/dev/null > "$out"; then
      echo "  (run failed or timed out, see $out tail)" >&2
    fi
    "$PY" - "$out" "$STRAT" "$SYM" "$SUMMARY" <<'PY' || true
import json, sys, os
out, strat, sym, summary = sys.argv[1:]
try:
    last = open(out).read().strip().splitlines()[-1]
    d = json.loads(last)
except Exception as exc:
    row = [strat, sym, "0", "0.00", "0.00", "0.00", "0.00", f"ERR: {exc}"]
else:
    if not d.get("ok", True):
        row = [strat, sym, "0", "0.00", "0.00", "0.00", "0.00", f"ERR: {d.get('error','?')}"]
    else:
        r = d.get("result") or {}
        row = [strat, sym, str(r.get("total_trades", 0)),
               f"{r.get('total_pnl',0):.2f}", f"{r.get('win_rate',0):.2f}",
               f"{(r.get('sharpe_ratio') or 0):.2f}",
               f"{(r.get('max_drawdown') or 0):.2f}",
               str(r.get("period",""))]
print("  →", "\t".join(row))
with open(summary, "a") as f:
    f.write("\t".join(row) + "\n")
PY
  done
done

echo
echo "=== Matrix summary: $SUMMARY ==="
column -t -s $'\t' "$SUMMARY"
