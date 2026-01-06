# Position Side Detection Fix - January 4, 2026

## Problem
Strategy was detecting LONG position when user had SHORT position:
```
✅ Found LONG position for MNQ: qty=17, side_val=0
📊 DETECTED LONG POSITION for MNQ
```

But user reports they are in a SHORT position.

## Root Cause Analysis

TopStepX position representation:
- `side = 0` = LONG position
- `side = 1` = SHORT position
- `quantity` = Always positive (17 means 17 contracts)

So a SHORT position should have:
- `side = 1`
- `quantity = 17` (positive)

But logs show:
- `side_val = 0` (LONG)
- `qty = 17` (positive)

This suggests either:
1. The position is actually LONG (not SHORT as user thinks)
2. TopStepX API is returning incorrect `side` value
3. There's a bug in how we're reading the position data

## Fix Applied

### Enhanced Position Detection Logic

**Priority Order:**
1. **String side field** (if already converted to "LONG"/"SHORT")
2. **side_val integer** (0=LONG, 1=SHORT) - Primary indicator for TopStepX
3. **Quantity sign** (negative=SHORT, positive=LONG) - Fallback

**Key Changes:**
- Added conflict detection when side_val and quantity disagree
- Added comprehensive logging of RAW position data
- Added console output for RAW position inspection
- Improved logic to handle edge cases

### New Logging

The strategy now logs:
```
🔍 RAW Position 0 (FULL DATA): {'id': '508148427', 'side': 0, 'quantity': 17, 'symbol': 'MNQ', ...}
🔍 Matched position for MNQ: side_val=0 (type=<class 'int'>), qty=17, side_str=None
⚠️  CONFLICT: side_val=0 (LONG) vs qty=17 (LONG). Using LONG.
```

This will help diagnose if:
- The position data is correct but user expectation is wrong
- The position data is incorrect from broker
- There's a parsing issue

## Testing Instructions

### Step 1: Check RAW Position Data

When strategy runs, look for:
```
🔍 RAW Position 0 (FULL DATA): {...}
```

**What to check:**
- What is the `side` field value? (0 or 1)
- What is the `quantity` field value? (positive or negative)
- Are there any other fields that indicate direction?

### Step 2: Verify Position Direction

Run the `positions` command in CLI:
```
positions
```

Compare what CLI shows vs what strategy detects.

**Expected:**
- If CLI shows "LONG 17" and strategy detects LONG → ✅ Correct
- If CLI shows "SHORT 17" but strategy detects LONG → ❌ Bug (needs more investigation)

### Step 3: Check for Conflicts

Look for conflict warnings:
```
⚠️  CONFLICT: side_val=0 (LONG) vs qty=17 (SHORT). Using LONG.
```

If you see conflicts, the RAW position data will help determine which is correct.

## Possible Scenarios

### Scenario A: Position is Actually LONG
**Evidence:**
- CLI shows: `MNQ LONG 17`
- Strategy detects: `LONG`
- RAW data shows: `side=0, qty=17`

**Conclusion:** ✅ Strategy is correct, user expectation is wrong

### Scenario B: Position is SHORT but API Returns Wrong Side
**Evidence:**
- CLI shows: `MNQ SHORT 17`
- Strategy detects: `LONG`
- RAW data shows: `side=0, qty=17` (but should be `side=1`)

**Conclusion:** ❌ TopStepX API bug - need to work around it

**Workaround Options:**
1. Check if there's another field that indicates SHORT
2. Check if quantity can be negative for SHORT
3. Check if we need to query position differently

### Scenario C: Position Parsing Issue
**Evidence:**
- CLI shows: `MNQ SHORT 17`
- Strategy detects: `LONG`
- RAW data shows: `side=1, qty=17` (correct!)

**Conclusion:** ❌ Our parsing logic is wrong

**Fix:** Update parsing to correctly read `side=1` as SHORT

## Next Steps

1. **Run strategy and capture RAW position logs**
   - Look for `🔍 RAW Position 0 (FULL DATA):` messages
   - Copy the full position dictionary

2. **Compare with CLI positions command**
   - Run `positions` command
   - Note what it shows vs what strategy detects

3. **Report findings:**
   - If RAW data shows `side=1` but strategy detects LONG → parsing bug (we'll fix)
   - If RAW data shows `side=0` but CLI shows SHORT → API bug (need workaround)
   - If RAW data shows `side=0` and CLI shows LONG → strategy correct, user mistaken

## Code Changes

### File: `strategies/simple_candle_strategy.py`

**Lines 330-395:** Enhanced position detection with:
- RAW position logging at WARNING level (always visible)
- Console output for RAW position inspection
- Conflict detection between side_val and quantity
- Improved priority logic for side determination

## Expected Behavior After Fix

### If Position is Actually LONG:
```
🔍 RAW Position 0 (FULL DATA): {'side': 0, 'quantity': 17, ...}
✅ Found LONG position for MNQ: qty=17, side_val=0
📊 DETECTED LONG POSITION for MNQ
🚫 SHORT signal BLOCKED for MNQ - Current position is LONG
```

### If Position is Actually SHORT:
```
🔍 RAW Position 0 (FULL DATA): {'side': 1, 'quantity': 17, ...}
✅ Found SHORT position for MNQ: qty=17, side_val=1
📊 DETECTED SHORT POSITION for MNQ
🚫 LONG signal BLOCKED for MNQ - Current position is SHORT
```

### If There's a Conflict:
```
🔍 RAW Position 0 (FULL DATA): {'side': 0, 'quantity': -17, ...}
⚠️  CONFLICT: side_val=0 (LONG) vs qty=-17 (SHORT). Using SHORT.
✅ Found SHORT position for MNQ: qty=-17, side_val=0
```

## Summary

The fix adds comprehensive logging to diagnose the exact issue. Once we see the RAW position data, we can determine:

1. **If the position is actually LONG** → Strategy is correct, no fix needed
2. **If TopStepX API is wrong** → Need to add workaround (check other fields)
3. **If our parsing is wrong** → Will fix parsing logic

The enhanced logging will make it clear which scenario we're dealing with.

