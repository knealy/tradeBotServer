#!/usr/bin/env bash
# Thorough pass for one symbol/timeframe: strategy list reminder, single backtest, then research grid (if applicable).
#
# Data source is controlled by MODE (default: sample = synthetic bars, no files).
# Research step runs only for ma_crossover / rsi_mean_reversion / ema_trend (function strategies).
#
# Usage:
#   SYMBOL=MNQ TIMEFRAME=5m DAYS=45 bash scripts/backtest_thorough_symbol.sh
#   MODE=api SYMBOL=MNQ DAYS=14 STRATEGY=ma_crossover bash scripts/backtest_thorough_symbol.sh
#   MODE=csv CSV=historical_data/MNQ_5m.csv STRATEGY=ma_crossover bash scripts/backtest_thorough_symbol.sh
#   RESEARCH=0 bash scripts/backtest_thorough_symbol.sh    # skip grid + OOS + MC
#
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

SYMBOL="${SYMBOL:-MNQ}"
TIMEFRAME="${TIMEFRAME:-5m}"
DAYS="${DAYS:-30}"
STRATEGY="${STRATEGY:-ma_crossover}"
MODE="${MODE:-sample}"
CSV="${CSV:-}"
RESEARCH="${RESEARCH:-1}"
RESEARCH_GRID="${RESEARCH_GRID:-fast_period=10:14:2,slow_period=40:80:20}"
MC="${MC:-150}"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " Thorough backtest  symbol=$SYMBOL  timeframe=$TIMEFRAME  days=$DAYS  strategy=$STRATEGY  mode=$MODE"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

echo ""
echo "== [1/3] Strategy names (see BACKTESTING.md / STRATEGY_DEVELOPMENT.md) =="
python3 core/backtest_executor.py --list-strategies

echo ""
echo "== [2/3] Single run (backtest_executor) =="
MODE="$MODE" SYMBOL="$SYMBOL" TIMEFRAME="$TIMEFRAME" DAYS="$DAYS" STRATEGY="$STRATEGY" CSV="$CSV" \
  bash "$ROOT/scripts/backtest_symbol.sh" "$@"

if [[ "$RESEARCH" != "1" ]]; then
  echo "RESEARCH=0 — skipping research runner."
  exit 0
fi

case "$STRATEGY" in
  ma_crossover|rsi_mean_reversion|ema_trend) ;;
  *)
    echo ""
    echo "== [3/3] Skipped: research runner supports function strategies only (got $STRATEGY) =="
    exit 0
    ;;
esac

echo ""
echo "== [3/3] Research runner (in-sample + OOS + MC gate, --no-db) =="
python3 -m core.research.runner \
  --strategy="$STRATEGY" \
  --symbol="$SYMBOL" \
  --timeframe="$TIMEFRAME" \
  --days="$DAYS" \
  --grid="$RESEARCH_GRID" \
  --mc="$MC" \
  --no-db \
  "$@"
