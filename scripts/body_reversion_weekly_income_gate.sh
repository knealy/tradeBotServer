#!/usr/bin/env bash
# Replay body_reversion with Gate A/B/C env overrides, --include-trades, then
# scripts/print_weekly_income.py (true ISO-week buckets from exit_time).
#
# Usage:
#   bash scripts/body_reversion_weekly_income_gate.sh
#   GATE=B SYM=MES bash scripts/body_reversion_weekly_income_gate.sh
#   GATE=B START=2025-01-01 END=2025-04-01 bash scripts/body_reversion_weekly_income_gate.sh   # faster probe
#
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

GATE="${GATE:-B}"
SYM="${SYM:-MNQ}"
START="${START:-2024-01-01}"
END="${END:-2026-05-01}"
OUT="${OUT:-/tmp/body_rev_${SYM}_gate${GATE}_trades_${START}_to_${END}.json}"
CSV="${ROOT}/historical_data/price/${SYM}_5m_databento.csv"

if [[ ! -f "$CSV" ]]; then
  echo "Missing $CSV" >&2
  exit 1
fi

case "$GATE" in
  A)
    export BODY_REVERSION_SIGNAL_REQUIRE_HIGH_ATR=true
    export BODY_REVERSION_SIGNAL_REQUIRE_RANGE_EXPAND=false
    ;;
  B)
    export BODY_REVERSION_SIGNAL_REQUIRE_HIGH_ATR=false
    export BODY_REVERSION_SIGNAL_REQUIRE_RANGE_EXPAND=true
    ;;
  C)
    export BODY_REVERSION_SIGNAL_REQUIRE_HIGH_ATR=true
    export BODY_REVERSION_SIGNAL_REQUIRE_RANGE_EXPAND=true
    ;;
  *)
    echo "GATE must be A, B, or C (got $GATE)" >&2
    exit 1
    ;;
esac
export ENABLE_SIGNALR=false
export PYTHONUNBUFFERED=1

echo "# Gate ${GATE}  ${SYM}  ${START} .. ${END}  ->  ${OUT}" >&2
"${ROOT}/.venv/bin/python" "${ROOT}/core/backtest_executor.py" \
  --strategy=body_reversion \
  --symbol="${SYM}" \
  --timeframe=5m \
  --csv="${CSV}" \
  --start="${START}" \
  --end="${END}" \
  --replay \
  --format=json \
  --include-trades \
  >"${OUT}" 2>"${OUT%.json}.log"

echo "# wrote $(wc -c <"${OUT}") bytes" >&2
"${ROOT}/.venv/bin/python" "${ROOT}/scripts/print_weekly_income.py" "${OUT}"
