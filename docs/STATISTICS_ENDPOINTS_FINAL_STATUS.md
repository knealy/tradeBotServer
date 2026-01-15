# Statistics Endpoints - Final Status

**Date**: 2026-01-15  
**Conclusion**: Endpoints exist but are non-functional

## Summary

After extensive testing, the Statistics endpoints on `userapi.topstepx.com`:
- ✅ **Exist and are accessible** (HTTP 200)
- ✅ **Accept requests** (no 404 errors)
- ❌ **Return empty data** even for accounts with active trades
- ❌ **Don't work** despite existing

## Tested Endpoints

| Endpoint | Status | Response | Tested With |
|----------|--------|----------|-------------|
| `/Statistics/daily` | ✅ 200 | `[]` | Multiple accounts, date formats, parameters |
| `/Statistics/profitFactor` | ✅ 200 | `{"totalProfit":0,"totalLoss":0}` | Multiple accounts |
| `/Statistics/trades` | ✅ 200 | `[]` | Multiple accounts |
| `/Statistics/monthly` | ✅ 200 | `[]` | Multiple accounts |
| `/Statistics/lifetimestats` | ✅ 200 | `[]` | Multiple accounts |

## What We Tried

1. ✅ Different accounts (including ones with active trades)
2. ✅ Different parameter formats (`accountId`, `tradingAccountId`)
3. ✅ Date ranges (YYYY-MM-DD, ISO 8601, single dates)
4. ✅ Different base paths (`/Statistics/...`, `/api/Statistics/...`)
5. ✅ Accounts that were just traded on

**Result**: All return empty arrays

## Conclusion

**These endpoints are broken or incomplete on TopStepX's side.**

Possible reasons:
- Endpoints are deprecated but still accessible
- Endpoints require different authentication/permissions
- Endpoints are incomplete/buggy
- Endpoints need parameters we haven't discovered

## Recommendation

### ✅ Do This
- **Continue using manual calculations** with `/api/Trade/search`
- **Remove Statistics endpoint calls** from code
- **Use `_calculate_trade_statistics()`** for all statistics

### ❌ Don't Do This
- Don't try to use Statistics endpoints
- Don't waste time debugging them
- Don't build features that depend on them

## Current Approach (Correct)

Using `/api/Trade/search` + manual calculation:
- ✅ **Works reliably**
- ✅ **Returns accurate data**
- ✅ **Flexible** (can filter by date, strategy, etc.)
- ✅ **Matches TopStepX dashboard** (uses same trade data)

## Other UserAPI Endpoints

While Statistics endpoints don't work, there may be other useful endpoints on `userapi.topstepx.com`:

- **Explore Swagger**: https://userapi.topstepx.com/swagger/index.html#/
- **Test other endpoint groups** (Account, User, Reports, etc.)
- **Document findings** if useful endpoints are found

See `docs/USERAPI_ENDPOINTS_EXPLORATION.md` for more details.

## Code Changes Needed

### Remove Statistics Endpoint Calls

1. **`trading_bot.py:3898-3961`** - `get_today_stats()`
   - Remove Statistics API calls
   - Use manual calculation from Trade/search

2. **`trading_bot.py:3963-4039`** - `get_trade_statistics()`
   - Remove Statistics API calls
   - Use manual calculation from Trade/search

3. **`trading_bot.py:4104-4177`** - `get_profit_factor()`
   - Remove Statistics API calls
   - Use manual calculation from Trade/search

### Keep Manual Calculations

- ✅ `trading_bot.py:3790-3870` - `_calculate_trade_statistics()` - **Keep this**
- ✅ Use `/api/Trade/search` to get trades
- ✅ Calculate statistics from trade data

## Final Verdict

**Statistics endpoints: Exist but don't work. Use manual calculations instead.**

The current approach is correct and reliable. No need to change it.
