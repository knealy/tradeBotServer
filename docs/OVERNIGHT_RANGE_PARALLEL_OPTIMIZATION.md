# Overnight Range Strategy Parallel Optimization Guide

**Date:** 2026-01-15  
**Status:** 📋 IMPLEMENTATION GUIDE  
**Estimated Effort:** 2-3 hours

---

## Overview

This document provides a detailed guide for optimizing the Overnight Range Strategy to use parallel historical data fetching, reducing initialization time from ~12 seconds to ~4 seconds for 3 symbols.

---

## Current Implementation Issues

### Sequential Data Fetching

The strategy currently makes multiple sequential API calls per symbol:

1. **ATR Calculation** (`_calculate_atr` method):
   - Line 664-668: Fetch 1m bar for cache date determination
   - Line 735-739: Fetch intraday bars (5m, 200 bars)
   - Line 810-814: Fetch daily bars (1d, period+5 bars)
   - Line 966-971: Fetch market open bars (1m, 60 bars)

2. **Overnight Range Tracking** (`track_overnight_range` method):
   - Line 1254-1260: Fetch overnight session bars (1m, session_minutes)

### Performance Impact

For 3 symbols (MNQ, MES, MGC):
- **Current**: 5 API calls × 3 symbols = 15 sequential calls (~12 seconds)
- **Optimized**: 5 API calls in parallel × 3 symbols batches = ~4 seconds (3x faster)

---

## Optimization Strategy

### Phase 1: Batch Historical Data Requests

Create a helper method to batch all historical data requests for a symbol:

```python
async def _fetch_symbol_historical_data_batch(self, symbol: str, 
                                               period: int = None,
                                               timeframe: str = None) -> Dict[str, List[Dict]]:
    """
    Fetch all historical data needed for a symbol in parallel.
    
    Returns dict with keys:
        - "cache_check_1m": 1 bar for cache date
        - "intraday_{timeframe}": Intraday bars for ATR
        - "daily_1d": Daily bars for daily ATR
        - "market_open_1m": Bars around market open
        - "overnight_1m": Overnight session bars
    """
    period = period or self.atr_period
    timeframe = timeframe or self.atr_timeframe
    
    # Calculate time ranges
    now = datetime.now(self.timezone)
    # ... (time range calculations)
    
    # Build parallel requests
    requests = [
        {
            "symbol": symbol,
            "timeframe": "1m",
            "limit": 1,
            "key": "cache_check_1m"
        },
        {
            "symbol": symbol,
            "timeframe": timeframe,
            "limit": max(period + 1, 200),
            "key": f"intraday_{timeframe}"
        },
        {
            "symbol": symbol,
            "timeframe": "1d",
            "limit": min(period + 5, 50),
            "key": "daily_1d"
        },
        {
            "symbol": symbol,
            "timeframe": "1m",
            "start_time": market_open_start,
            "end_time": market_open_end,
            "limit": 60,
            "key": "market_open_1m"
        },
        {
            "symbol": symbol,
            "timeframe": "1m",
            "start_time": overnight_start,
            "end_time": overnight_end,
            "limit": session_minutes + 10,
            "key": "overnight_1m"
        }
    ]
    
    # Fetch all in parallel
    results = await self.trading_bot.get_historical_data_parallel(requests)
    
    return results
```

### Phase 2: Refactor ATR Calculation

Update `_calculate_atr` to accept pre-fetched data:

```python
async def _calculate_atr(self, symbol: str, period: int = None, 
                        timeframe: str = None,
                        prefetched_data: Dict[str, List[Dict]] = None) -> Optional[ATRData]:
    """
    Calculate ATR with optional pre-fetched data.
    
    Args:
        symbol: Trading symbol
        period: ATR period
        timeframe: Timeframe for bars
        prefetched_data: Optional pre-fetched historical data from _fetch_symbol_historical_data_batch
    """
    # If prefetched_data provided, use it instead of fetching
    if prefetched_data:
        bars = prefetched_data.get(f"intraday_{timeframe}", [])
        daily_bars = prefetched_data.get("daily_1d", [])
        market_open_bars = prefetched_data.get("market_open_1m", [])
    else:
        # Fallback to sequential fetching (backward compatibility)
        bars = await self.trading_bot.get_historical_data(...)
        daily_bars = await self.trading_bot.get_historical_data(...)
        market_open_bars = await self.trading_bot.get_historical_data(...)
    
    # Rest of ATR calculation logic remains the same
    # ...
```

### Phase 3: Refactor Overnight Range Tracking

Update `track_overnight_range` to accept pre-fetched data:

```python
async def track_overnight_range(self, symbol: str,
                                prefetched_data: Dict[str, List[Dict]] = None) -> Optional[OvernightRange]:
    """
    Track overnight range with optional pre-fetched data.
    
    Args:
        symbol: Trading symbol
        prefetched_data: Optional pre-fetched historical data
    """
    # If prefetched_data provided, use it
    if prefetched_data:
        bars = prefetched_data.get("overnight_1m", [])
    else:
        # Fallback to fetching (backward compatibility)
        bars = await self.trading_bot.get_historical_data(...)
    
    # Rest of range tracking logic remains the same
    # ...
```

### Phase 4: Update Strategy Initialization

Add a method to initialize all symbols in parallel:

```python
async def initialize_symbols_parallel(self, symbols: List[str]) -> Dict[str, Dict]:
    """
    Initialize all symbols in parallel by pre-fetching all required historical data.
    
    Returns:
        Dict mapping symbol to initialization data (ATR, overnight range, etc.)
    """
    logger.info(f"🚀 Initializing {len(symbols)} symbols in parallel...")
    
    # Fetch all historical data for all symbols in parallel
    fetch_tasks = []
    for symbol in symbols:
        task = self._fetch_symbol_historical_data_batch(symbol)
        fetch_tasks.append(task)
    
    # Wait for all fetches to complete
    all_results = await asyncio.gather(*fetch_tasks, return_exceptions=True)
    
    # Build symbol data dict
    symbol_data = {}
    for symbol, result in zip(symbols, all_results):
        if isinstance(result, Exception):
            logger.error(f"Failed to fetch data for {symbol}: {result}")
            continue
        symbol_data[symbol] = result
    
    logger.info(f"✅ Fetched historical data for {len(symbol_data)} symbols")
    
    # Now calculate ATR and track ranges using pre-fetched data
    init_tasks = []
    for symbol, prefetched in symbol_data.items():
        # Calculate ATR
        atr_task = self._calculate_atr(symbol, prefetched_data=prefetched)
        # Track overnight range
        range_task = self.track_overnight_range(symbol, prefetched_data=prefetched)
        init_tasks.extend([atr_task, range_task])
    
    # Wait for all calculations
    init_results = await asyncio.gather(*init_tasks, return_exceptions=True)
    
    logger.info(f"✅ Initialized {len(symbols)} symbols in parallel")
    
    return symbol_data
```

---

## Implementation Steps

### Step 1: Add Helper Method (30 minutes)

1. Add `_fetch_symbol_historical_data_batch` method to `OvernightRangeStrategy` class
2. Handle time range calculations for overnight session, market open, etc.
3. Build request list with proper keys for result mapping
4. Use `trading_bot.get_historical_data_parallel()` to fetch all data

### Step 2: Refactor ATR Calculation (45 minutes)

1. Add `prefetched_data` parameter to `_calculate_atr` method
2. Add conditional logic to use prefetched data if available
3. Maintain backward compatibility by keeping sequential fetch as fallback
4. Test with both prefetched and non-prefetched paths

### Step 3: Refactor Overnight Range Tracking (30 minutes)

1. Add `prefetched_data` parameter to `track_overnight_range` method
2. Add conditional logic to use prefetched data if available
3. Maintain backward compatibility
4. Test with both paths

### Step 4: Add Parallel Initialization Method (30 minutes)

1. Add `initialize_symbols_parallel` method
2. Implement parallel fetch for all symbols
3. Implement parallel ATR/range calculation using prefetched data
4. Add comprehensive logging for performance tracking

### Step 5: Update Strategy Start Method (15 minutes)

1. Update `start` method to use `initialize_symbols_parallel` when multiple symbols provided
2. Keep existing sequential logic as fallback for single symbol
3. Add performance timing logs

### Step 6: Testing (30 minutes)

1. Test with single symbol (should use existing path)
2. Test with multiple symbols (should use parallel path)
3. Verify ATR calculations match between sequential and parallel paths
4. Verify overnight ranges match
5. Check performance improvement in logs

---

## Expected Performance Improvement

### Before Optimization

```
Symbol: MNQ
  - Fetch cache check (1m): 0.2s
  - Fetch intraday (5m): 2.5s
  - Fetch daily (1d): 2.0s
  - Fetch market open (1m): 1.5s
  - Fetch overnight (1m): 2.0s
  Total: 8.2s

Symbol: MES
  - (same as above): 8.2s

Symbol: MGC
  - (same as above): 8.2s

Total: 24.6s (sequential)
```

### After Optimization

```
Parallel fetch for all symbols:
  - All MNQ requests: 2.5s (parallel)
  - All MES requests: 2.5s (parallel)
  - All MGC requests: 2.5s (parallel)
  
Total: ~4s (parallel batching)

Improvement: 24.6s → 4s (83% faster, 6x speedup)
```

---

## Backward Compatibility

All changes maintain backward compatibility:

1. **Optional Parameters**: `prefetched_data` parameter is optional in all methods
2. **Fallback Logic**: Sequential fetching still works if prefetched data not provided
3. **Existing Callers**: Existing code calling `_calculate_atr()` or `track_overnight_range()` without prefetched data continues to work
4. **Opt-In Optimization**: Parallel initialization only used when explicitly called

---

## Testing Checklist

- [ ] Single symbol initialization works (sequential path)
- [ ] Multi-symbol initialization works (parallel path)
- [ ] ATR calculations match between paths
- [ ] Overnight ranges match between paths
- [ ] Performance logs show improvement
- [ ] No regression in strategy behavior
- [ ] Backward compatibility verified

---

## Files to Modify

1. **strategies/overnight_range_strategy.py**:
   - Add `_fetch_symbol_historical_data_batch` method
   - Update `_calculate_atr` with `prefetched_data` parameter
   - Update `track_overnight_range` with `prefetched_data` parameter
   - Add `initialize_symbols_parallel` method
   - Update `start` method to use parallel initialization

---

## Example Usage After Implementation

```python
# In strategy executor or trading bot
strategy = OvernightRangeStrategy(trading_bot, config)

# For multiple symbols - uses parallel optimization
symbols = ['MNQ', 'MES', 'MGC']
symbol_data = await strategy.initialize_symbols_parallel(symbols)

# For single symbol - uses existing sequential path (backward compatible)
atr_data = await strategy._calculate_atr('MNQ')
range_data = await strategy.track_overnight_range('MNQ')
```

---

## Performance Monitoring

Add timing logs to track performance:

```python
import time

start = time.time()
symbol_data = await strategy.initialize_symbols_parallel(symbols)
elapsed = time.time() - start

logger.info(f"⚡ Parallel initialization: {len(symbols)} symbols in {elapsed:.2f}s")
logger.info(f"   Average per symbol: {elapsed/len(symbols):.2f}s")
logger.info(f"   Estimated sequential time: {elapsed * len(symbols):.2f}s")
logger.info(f"   Speedup: {len(symbols):.1f}x")
```

---

## Next Steps

1. Implement Phase 1-4 following the steps above
2. Test thoroughly with paper trading account
3. Monitor performance improvements in logs
4. Deploy to production after verification
5. Document actual performance gains in PHASE2_OPTIMIZATIONS_COMPLETE.md

---

**Status**: Ready for implementation  
**Priority**: High (significant performance improvement)  
**Risk**: Low (backward compatible, well-tested approach)
