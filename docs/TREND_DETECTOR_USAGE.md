# Trend Detector Usage Guide

## Overview

The `TrendDetector` module provides a reusable trend quality scoring system that can be used by any strategy to filter out choppy/consolidating markets and only trade in trending conditions.

## Quick Start

### Basic Usage

```python
from core.trend_detector import TrendDetector

# Initialize with conservative settings
detector = TrendDetector(
    ema_fast_period=8,
    ema_slow_period=21,
    atr_period=14,
    min_trend_score=40,  # VERY conservative - only blocks extreme chop
    lookback_bars=10
)

# Calculate trend quality
score = await detector.calculate_trend_quality(
    symbol="MNQ",
    bars=bars,  # List of OHLC bar dictionaries
    fast_ema=fast_ema,  # Optional: pre-calculated
    slow_ema=slow_ema   # Optional: pre-calculated
)

# Check if market is trending
if score.is_trending:
    # Safe to trade
    print(f"✅ Trending market: {score.total_score}/100")
else:
    # Choppy market - skip trade
    print(f"🚫 Choppy market: {score.total_score}/100")
```

## Integration in Strategy

### Example: Simple Candle Strategy

The trend detector is already integrated in `simple_candle_strategy.py`:

```python
# In __init__:
self.use_trend_filter = os.getenv('USE_TREND_FILTER', 'true').lower() == 'true'
if self.use_trend_filter:
    self.trend_detector = TrendDetector(
        ema_fast_period=8,
        ema_slow_period=21,
        atr_period=14,
        min_trend_score=40,  # VERY conservative
        lookback_bars=10
    )

# In analyze() method:
if self.use_trend_filter and self.trend_detector:
    trend_score = await self.trend_detector.calculate_trend_quality(...)
    if not trend_score.is_trending:
        return None  # Block trade
```

## Configuration

### Environment Variables

- `USE_TREND_FILTER=true` - Enable/disable trend filtering (default: true)
- `MIN_TREND_SCORE=40` - Minimum score threshold (default: 40 - very conservative)

### Conservative Settings (Current)

- **min_trend_score**: 40/100 (only blocks extreme chop)
- **EMA separation**: 0.05% minimum (~12 points for MNQ)
- **Momentum**: 0.05% minimum (~12 points)
- **ATR expansion**: 5% minimum
- **Direction consistency**: 50% minimum

These settings are designed to **NOT exclude too many trades** initially. You can gradually increase thresholds as you see results.

## Score Breakdown

The trend quality score (0-100) is calculated from:

1. **EMA Separation** (0-25 points)
   - Measures distance between fast and slow EMA
   - Trending: EMAs stay separated
   - Choppy: EMAs converge

2. **Direction Consistency** (0-20 points)
   - Checks if price moves align with EMA direction
   - Trending: Consistent direction
   - Choppy: Frequent reversals

3. **Momentum** (0-20 points)
   - Measures price movement over lookback period
   - Trending: Sustained movement
   - Choppy: Sideways movement

4. **ATR Expansion** (0-15 points)
   - Compares current volatility to average
   - Trending: Volatility expanding
   - Choppy: Volatility contracting

5. **Candle Consistency** (0-10 points)
   - Counts consecutive same-direction candles
   - Trending: Many candles same direction
   - Choppy: Alternating candles

## Adjusting Thresholds

### Very Conservative (Current)
```python
min_trend_score=40  # Only blocks extreme chop
```
- Blocks ~10-20% of trades
- Good for initial testing

### Moderate
```python
min_trend_score=50  # Blocks moderate chop
```
- Blocks ~30-40% of trades
- Better win rate, fewer trades

### Aggressive
```python
min_trend_score=60  # Only allows strong trends
```
- Blocks ~50-60% of trades
- Higher win rate, much fewer trades

### Very Aggressive
```python
min_trend_score=70  # Only allows very strong trends
```
- Blocks ~70-80% of trades
- Highest win rate, very few trades

## Expected Results

### Before Trend Filter:
- Win Rate: ~10%
- Trade Frequency: High (many in choppy markets)
- Stop Loss Hits: Frequent

### After Trend Filter (min_score=40):
- Win Rate: Target 20-30%+
- Trade Frequency: Reduced by ~20%
- Stop Loss Hits: Less frequent

### After Trend Filter (min_score=60):
- Win Rate: Target 30-40%+
- Trade Frequency: Reduced by ~50%
- Stop Loss Hits: Much less frequent

## Monitoring

The strategy logs trend quality when trades are blocked:

```
🚫 CHOPPY MARKET: Trend score 32/100 
   (EMA sep: 5, Dir: 8, Mom: 0, ATR: 5, Candles: 0) - Skipping trade
```

For trending markets (occasionally logged):
```
🟢 TRENDING: Score 68/100 (LONG) - Trade allowed
```

## Troubleshooting

### Too Many Trades Blocked?
- Lower `min_trend_score` (e.g., 30 instead of 40)
- Check individual score components to see what's failing

### Not Enough Trades Blocked?
- Increase `min_trend_score` (e.g., 50 or 60)
- Adjust individual thresholds in `TrendDetector.__init__()`

### Performance Issues?
- The detector caches EMA calculations
- Consider reducing `lookback_bars` if needed

## Advanced Usage

### Custom Thresholds

You can adjust individual thresholds when initializing:

```python
detector = TrendDetector(
    min_trend_score=50,
    ema_separation_min=0.08,  # Custom threshold
    momentum_min=0.10,        # Custom threshold
    # ... etc
)
```

### Accessing Score Details

```python
score = await detector.calculate_trend_quality(...)

print(f"Total: {score.total_score}/100")
print(f"EMA Separation: {score.ema_separation_score}/25")
print(f"Direction: {score.direction_consistency_score}/20")
print(f"Momentum: {score.momentum_score}/20")
print(f"ATR: {score.atr_expansion_score}/15")
print(f"Candles: {score.candle_consistency_score}/10")
print(f"Trend Direction: {score.trend_direction}")  # "LONG", "SHORT", or None
print(f"Details: {score.details}")  # Full breakdown
```

## Next Steps

1. **Monitor** how many trades are blocked with current settings
2. **Analyze** which score components are failing most often
3. **Adjust** thresholds gradually based on results
4. **Backtest** different threshold values
5. **Optimize** for your specific trading style

## Summary

The trend detector is now integrated and ready to use with **very conservative settings** (min_score=40). This means it will only block the most choppy markets initially, allowing you to:

1. See how it performs
2. Monitor what gets blocked
3. Gradually increase thresholds as needed

Start conservative, then optimize based on results! 🎯

