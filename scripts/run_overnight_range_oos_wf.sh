#!/usr/bin/env bash
# OOS + walk-forward for ``overnight_range`` P0 (live TOML, no env overrides)
# vs P5 (widened bands; same env as ``scripts/run_overnight_range_sweep.sh`` P5).
#
# Uses ``python -m core.research.runner`` (mandatory chronological IS/OOS split,
# Monte Carlo on OOS trades, optional walk-forward folds).
#
# Env (all optional):
#   CSV=historical_data/price/MNQ_1m_complete.csv
#   SYMBOL=MNQ
#   START=2025-12-24          # use with END for full-span (slow on 1m)
#   END=2026-04-30
#   USE_FAST=1              # when 1 (default), narrows to FAST_START..END for quicker runs
#   FAST_START=2026-02-01
#   OOS_FRACTION=0.2
#   MIN_OOS_BARS=2000
#   WALK_FORWARD=4          # set 0 to skip folds (IS+OOS only)
#   MC=100
#   OUT_DIR=docs/perf/sweeps
#
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="${ROOT}/.venv/bin/python"
[[ -x "$PY" ]] || PY=python3

CSV="${CSV:-historical_data/price/MNQ_1m_complete.csv}"
SYMBOL="${SYMBOL:-MNQ}"
END="${END:-2026-04-30}"
USE_FAST="${USE_FAST:-1}"
FAST_START="${FAST_START:-2026-02-01}"
START="${START:-2025-12-24}"
OOS_FRACTION="${OOS_FRACTION:-0.2}"
MIN_OOS_BARS="${MIN_OOS_BARS:-2000}"
WALK_FORWARD="${WALK_FORWARD:-4}"
MC="${MC:-100}"
OUT_DIR="${OUT_DIR:-docs/perf/sweeps}"

if [[ "$USE_FAST" == "1" ]]; then
  EFF_START="$FAST_START"
  echo "USE_FAST=1 → csv window ${EFF_START}..${END} (set USE_FAST=0 START=... for full span)"
else
  EFF_START="$START"
  echo "Full csv window ${EFF_START}..${END}"
fi

mkdir -p "$OUT_DIR"
TAG="${EFF_START}_${END}"

run_profile() {
  local profile="$1" out_json="$2"
  echo "=== overnight_range research profile=${profile} → ${out_json} ==="
  "$PY" -m core.research.runner \
    --strategy overnight_range \
    --symbol "$SYMBOL" \
    --timeframe 1m \
    --csv "$CSV" \
    --csv-start "$EFF_START" \
    --csv-end "$END" \
    --oos-fraction "$OOS_FRACTION" \
    --min-oos-bars "$MIN_OOS_BARS" \
    --walk-forward "$WALK_FORWARD" \
    --mc "$MC" \
    --no-db \
    --overnight-research-profile "$profile" \
    --run-tag "overnight_mnq_oos_wf_${profile}_${TAG}" \
    --output "$out_json"
}

P0_JSON="${OUT_DIR}/overnight_range_${SYMBOL}_oos_wf_P0_${TAG}.json"
P5_JSON="${OUT_DIR}/overnight_range_${SYMBOL}_oos_wf_P5_${TAG}.json"
run_profile p0 "$P0_JSON"
run_profile p5 "$P5_JSON"

echo
echo "=== Summary (IS/OOS + MC gate) ==="
export P0_JSON P5_JSON
"$PY" - <<'PY'
import json, os

def show(label: str, path: str) -> None:
    d = json.load(open(path))
    for r in d.get("grid_results", []):
        print(
            f"{label:>3}  IS: trades={r['is_trades']:>4}  sharpe={r['is_sharpe']:>8}  |  "
            f"OOS: trades={r['oos_trades']:>4}  sharpe={r['oos_sharpe']:>8}  "
            f"ret%={r['oos_return_pct']:>8}  mc_pass={r['mc_pass']}  ({r['mc_reason']})"
        )
    wf = (d.get("walk_forward_by_combo") or [])
    if wf and wf[0].get("folds"):
        parts = [
            f"f{row['fold']}: test_sharpe={row['test_sharpe']:.3f} trades={row['test_trades']}"
            for row in wf[0]["folds"]
        ]
        print(f"      walk-forward ({len(wf[0]['folds'])} folds): " + "  ".join(parts))


show("P0", os.environ["P0_JSON"])
show("P5", os.environ["P5_JSON"])
PY

echo "Done."
