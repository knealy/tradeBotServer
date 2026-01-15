# UserAPI Endpoints Exploration

**Date**: 2026-01-15  
**Base URL**: `https://userapi.topstepx.com`  
**Swagger**: https://userapi.topstepx.com/swagger/index.html#/

## Overview

The `userapi.topstepx.com` API contains additional endpoints beyond the main trading API (`api.topstepx.com`). While Statistics endpoints exist but don't return data, there may be other useful endpoints available.

## Statistics Endpoints Status

### ❌ Endpoints Exist But Don't Work

All Statistics endpoints return HTTP 200 but empty arrays:

| Endpoint | Status | Response | Notes |
|----------|--------|----------|-------|
| `/Statistics/daily` | ✅ 200 | `[]` | Returns empty even with active trades |
| `/Statistics/profitFactor` | ✅ 200 | `{"totalProfit":0,"totalLoss":0}` | Returns structure but no data |
| `/Statistics/trades` | ✅ 200 | `[]` | Returns empty even with active trades |
| `/Statistics/monthly` | ✅ 200 | `[]` | Returns empty even with active trades |
| `/Statistics/lifetimestats` | ✅ 200 | `[]` | Returns empty even with active trades |

**Conclusion**: These endpoints appear to be incomplete or non-functional on TopStepX's side. **Do not use them.**

## Other UserAPI Endpoints to Explore

The Swagger documentation at https://userapi.topstepx.com/swagger/index.html#/ shows additional endpoints that might be useful:

### Potential Endpoints to Investigate

1. **Account Management**
   - User profile endpoints
   - Account settings
   - Preferences

2. **Reporting/Analytics**
   - Performance reports
   - Historical analytics
   - Export functionality

3. **Notifications/Alerts**
   - Alert management
   - Notification settings

4. **User Preferences**
   - Dashboard settings
   - Display preferences
   - Custom configurations

## How to Explore

### 1. Browse Swagger UI

Visit: https://userapi.topstepx.com/swagger/index.html#/

Look for:
- Endpoint groups (Account, User, Reports, etc.)
- Request/response schemas
- Authentication requirements
- Example requests

### 2. Test Endpoints

Use the same authentication token and test endpoints:

```bash
TOKEN="YOUR_TOKEN"

# Test an endpoint
curl -v -X POST 'https://userapi.topstepx.com/SomeEndpoint' \
  -H 'Content-Type: application/json' \
  -H 'accept: text/plain' \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"accountId": 12694476}'
```

### 3. Document Findings

When you find useful endpoints:
1. Document the endpoint URL and parameters
2. Test with your account
3. Document the response format
4. Add to codebase if useful

## Current Status

### What We Know
- ✅ `userapi.topstepx.com` exists and is accessible
- ✅ Statistics endpoints exist but don't return data
- ✅ Other endpoint groups exist in Swagger

### What We Don't Know
- ❓ What other endpoints are available
- ❓ Which endpoints are functional
- ❓ What data they return
- ❓ How they could improve the system

## Recommendations

### Short Term
1. **Stick with manual calculations** - Current approach works well
2. **Don't use Statistics endpoints** - They don't work
3. **Continue using `/api/Trade/search`** - Reliable and accurate

### Long Term
1. **Explore UserAPI endpoints** - May find useful features
2. **Document findings** - Keep track of what works
3. **Integrate useful endpoints** - Add new features if endpoints are functional

## Potential Use Cases

If other UserAPI endpoints work, they could provide:

1. **Better Account Management**
   - User preferences
   - Account settings
   - Profile information

2. **Enhanced Reporting**
   - Pre-built reports
   - Analytics dashboards
   - Export capabilities

3. **Notification Features**
   - Alert management
   - Email/SMS notifications
   - Custom alerts

4. **Performance Tracking**
   - Historical performance
   - Benchmark comparisons
   - Goal tracking

## Testing Checklist

When exploring new endpoints:

- [ ] Check Swagger for endpoint definition
- [ ] Test with your authentication token
- [ ] Verify request format (flat vs nested)
- [ ] Test with different accounts
- [ ] Document request/response format
- [ ] Test error cases
- [ ] Document if endpoint is useful

## Notes

- **Statistics endpoints are broken** - Don't waste time on them
- **Other endpoints may work** - Worth exploring
- **Current manual approach is fine** - No rush to replace it
- **Swagger is your friend** - Use it to discover endpoints
