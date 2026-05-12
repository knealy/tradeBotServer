#!/usr/bin/env bash
# Operator sprint: session-level alpha_discovery on canonical 5m Databento CSVs.
# Run while long body_reversion replays finish in other terminals.
#
# Usage:
#   bash scripts/run_alpha_discovery_sprint.sh
#   OOS_SPLIT=0.3 bash scripts/run_alpha_discovery_sprint.sh   # IS/OOS session split (needs depth)
#
# Prerequisites: historical_data/price/{MNQ,MES,MGC}_5m_databento.csv
# (see scripts/databento_stitch_canonical.py if you only have 1m canonical files).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
OOS="${OOS_SPLIT:-0}"
for s in MNQ MES MGC; do
  csv="historical_data/price/${s}_5m_databento.csv"
  if [[ ! -f "$csv" ]]; then
    echo "[skip] $s: missing $csv"
    continue
  fi
  out="docs/alpha/sprint_overnight_range_${s}.md"
  echo "=== alpha_discovery $s -> $out (oos_split=$OOS) ==="
  .venv/bin/python scripts/alpha_discovery.py --symbol "$s" --csv "$csv" \
    --output "$out" --oos-split "$OOS" || {
      echo "[warn] $s: alpha_discovery exited $?"
    }
done
echo "Next (bar-level / body-reversion): scripts/deep_pattern_scan.py, scripts/body_reversion_combo_audit.py — see docs/ALPHA_DISCOVERY.md"
