#!/bin/bash
#
# Batch Backtest Runner
# Tests multiple strategies, symbols, and configurations
#
# Usage: ./scripts/batch_backtest.sh

echo "========================================="
echo "BATCH BACKTESTING"
echo "========================================="
echo ""

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Create results directory
mkdir -p backtest_results
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
RESULTS_FILE="backtest_results/batch_${TIMESTAMP}.txt"

echo "Results will be saved to: $RESULTS_FILE"
echo ""

# Test 1: MA Crossover on all symbols
echo "${BLUE}=== Test 1: MA Crossover - All Symbols ===${NC}"
for symbol in MNQ MES MYM M2K MGC; do
    echo "${GREEN}Testing $symbol...${NC}"
    python core/backtest_executor.py \
        --strategy=ma_crossover \
        --symbol=$symbol \
        --days=30 \
        --sample \
        2>&1 | tee -a $RESULTS_FILE
    echo ""
done

# Test 2: Different timeframes on MNQ
echo "${BLUE}=== Test 2: Timeframe Comparison ===${NC}"
for timeframe in 1m 2m 5m 15m; do
    echo "${GREEN}Testing $timeframe...${NC}"
    python core/backtest_executor.py \
        --strategy=ma_crossover \
        --symbol=MNQ \
        --timeframe=$timeframe \
        --days=30 \
        --sample \
        2>&1 | tee -a $RESULTS_FILE
    echo ""
done

# Test 3: RSI strategy with different levels
echo "${BLUE}=== Test 3: RSI Levels ===${NC}"
for oversold in 20 25 30 35; do
    overbought=$((100 - oversold))
    echo "${GREEN}Testing RSI $oversold/$overbought...${NC}"
    python core/backtest_executor.py \
        --strategy=rsi_mean_reversion \
        --symbol=MNQ \
        --days=30 \
        --sample \
        --rsi-oversold=$oversold \
        --rsi-overbought=$overbought \
        2>&1 | tee -a $RESULTS_FILE
    echo ""
done

# Test 4: EMA Trend with different periods
echo "${BLUE}=== Test 4: EMA Periods ===${NC}"
for short in 50 89; do
    for long in 200 233; do
        echo "${GREEN}Testing EMA $short/$long...${NC}"
        python core/backtest_executor.py \
            --strategy=ema_trend \
            --symbol=MNQ \
            --days=30 \
            --sample \
            --ema-short=$short \
            --ema-long=$long \
            2>&1 | tee -a $RESULTS_FILE
        echo ""
    done
done

# Test 5: Monte Carlo on best strategy
echo "${BLUE}=== Test 5: Monte Carlo Analysis ===${NC}"
echo "${GREEN}Running Monte Carlo simulations...${NC}"
python core/backtest_executor.py \
    --strategy=ma_crossover \
    --symbol=MNQ \
    --days=90 \
    --sample \
    --monte-carlo=1000 \
    2>&1 | tee -a $RESULTS_FILE

echo ""
echo "========================================="
echo "BATCH BACKTEST COMPLETE"
echo "========================================="
echo "Results saved to: $RESULTS_FILE"
echo ""
echo "To analyze results:"
echo "  cat $RESULTS_FILE | grep 'Sharpe Ratio'"
echo "  cat $RESULTS_FILE | grep 'Win Rate'"
echo "  cat $RESULTS_FILE | grep 'Total Return'"
