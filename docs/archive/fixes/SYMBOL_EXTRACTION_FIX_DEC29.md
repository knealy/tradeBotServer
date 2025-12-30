# Symbol Extraction Fix - December 29, 2025

## Problem

**GUI dropdowns were showing month codes (e.g., `6AH6`) instead of root symbols (e.g., `MNQ`)**, causing:
- "Symbol '6AH6' not found in contract cache" errors
- Chart/quote requests failing
- Contract selection not working

### Root Cause

The symbol extraction logic in `gui/chart_html.py` was prioritizing the API's `name` field (which contains month-specific codes like `6AH6`) over parsing the structured `contractId`.

**Example API Response:**
```json
{
  "contractId": "CON.F.US.MNQ.H26",
  "name": "6AH6",
  "description": "E-mini Micro NASDAQ March 2026"
}
```

**Old Logic (WRONG):**
```python
sym = (
    c.get('symbol')
    or c.get('name')  # ❌ This returns '6AH6' - month code!
    or extract_root_symbol(contract_id)
    or 'Unknown'
)
```

**Result:** Dropdowns showed `6AH6`, but the ContractManager expected `MNQ`.

---

## Solution

### 1. Prioritize Contract ID Parsing

Updated `gui/chart_html.py` to **ALWAYS** try parsing the `contractId` FIRST:

```python
# ALWAYS try to extract root symbol from contract ID FIRST (most reliable)
sym = extract_root_symbol(contract_id)  # CON.F.US.MNQ.H26 → MNQ

# Only if that fails, fall back to name/symbol fields
if not sym:
    sym = (
        c.get('symbol')
        or c.get('Symbol')
        or c.get('name')  # ✅ Now only used as last resort
        or 'Unknown'
    )
```

### 2. Consistent Symbol Extraction Across All Modules

Updated `core/market_data.py` to use the same parsing logic:

**TopStepX Contract ID Format:**
```
CON.F.US.<ROOT_SYMBOL>.<EXPIRATION>
CON.F.US.MNQ.H26
         ^^^
       parts[3]
```

**Extraction Logic:**
```python
if '.' in str(contract_id):
    parts = str(contract_id).split('.')
    # Prefer parts[3] for CON.F.US.SYM.EXP format
    if len(parts) >= 5:
        contract_symbol = parts[3]  # ✅ Root symbol
    elif len(parts) >= 4:
        contract_symbol = parts[-2]  # Fallback for other formats
```

---

## Impact

### Before Fix
- ❌ GUI showed: `6AH6, 6BH6, 6CH6, CLG6, HGH6`
- ❌ User clicks `6AH6`
- ❌ ContractManager: "Symbol '6AH6' not found in cache"
- ❌ No chart/quote data loads

### After Fix
- ✅ GUI shows: `MNQ, 6B, 6C, CL, HG` (root symbols)
- ✅ User clicks `MNQ`
- ✅ ContractManager resolves: `MNQ` → `CON.F.US.MNQ.H26`
- ✅ Chart/quote data loads successfully

---

## Lessons Learned

1. **Always prioritize structured data (contract IDs) over free-text fields (name)**
   - Contract IDs have a predictable format
   - Free-text fields often contain abbreviations or codes

2. **Maintain consistent parsing logic across all modules**
   - GUI, ContractManager, and MarketData must all extract symbols the same way
   - Prevents mismatches between what the user sees and what the system expects

3. **Root symbols are what users recognize**
   - `MNQ` is recognizable
   - `6AH6` is a month code (meaningless without context)

4. **TopStepX contract format:**
   - `CON.F.US.<ROOT_SYMBOL>.<EXPIRATION>`
   - `parts[3]` is the root symbol (e.g., `MNQ`)
   - `parts[4]` is the expiration (e.g., `H26` = March 2026)

---

## Files Changed

- `gui/chart_html.py` (handle_get_contracts)
- `core/market_data.py` (get_contract_id)
- `.cursor/context_profile.json` (added to recent_fixes)

---

## Testing

**Before:**
```bash
python trading_bot.py --account_select=1 --command='master'
# GUI opens → dropdown shows "6AH6"
# Select "6AH6" → ERROR: Symbol '6AH6' not found in contract cache
```

**After:**
```bash
python trading_bot.py --account_select=1 --command='master'
# GUI opens → dropdown shows "MNQ"
# Select "MNQ" → ✅ Chart loads successfully
```

---

## Status

✅ **FIXED** - December 29, 2025

