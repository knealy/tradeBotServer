#!/usr/bin/env bash
# Example CSV research sweeps on MNQ 1m OHLCV (see docs/BACKTEST_RESEARCH.md).
# Usage: CSV=historical_data/price/MNQ_1m_complete.csv bash scripts/mnq_1m_research_sweep.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PYTHON="${ROOT}/.venv/bin/python"
[[ -x "$PYTHON" ]] || PYTHON="python3"
CSV="${CSV:-historical_data/price/MNQ_1m_complete.csv}"

echo "=== MA crossover — March 2026 (1m) ==="
"$PYTHON" -m core.research.runner --strategy ma_crossover --symbol MNQ --timeframe 1m \
  --csv "$CSV" --csv-start 2026-03-01 --csv-end 2026-03-31 \
  --oos-fraction 0.2 --min-oos-bars 200 \
  --grid "fast_period=8:14:2,slow_period=30:50:10" --max-grid 12 \
  --mc 80 --mc-min-profit-prob 0.4 --mc-max-mean-dd 45 --no-db

echo "=== MA crossover — April 2026 (1m) ==="
"$PYTHON" -m core.research.runner --strategy ma_crossover --symbol MNQ --timeframe 1m \
  --csv "$CSV" --csv-start 2026-04-01 --csv-end 2026-04-30 \
  --oos-fraction 0.2 --min-oos-bars 200 \
  --grid "fast_period=8:14:2,slow_period=30:50:10" --max-grid 12 \
  --mc 80 --mc-min-profit-prob 0.4 --mc-max-mean-dd 45 --no-db

echo "Done. For overnight_range, prefer resampled 5m + alpha_discovery or dedicated replay hardening."
