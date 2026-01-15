# TopStepX API Request Format Guide

**Date**: 2026-01-15  
**Purpose**: Document correct request formats for TopStepX API endpoints

## Important: Request Format Varies by Endpoint

Some endpoints require a **nested `request` object**, while others accept **flat parameters**.

## Endpoints Requiring Nested `request` Object

### 1. `/api/Contract/available`

**❌ Wrong**:
```json
{
  "live": false
}
```

**✅ Correct**:
```json
{
  "request": {
    "live": false
  }
}
```

**Curl Example**:
```bash
curl -X POST 'https://api.topstepx.com/api/Contract/available' \
  -H 'Content-Type: application/json' \
  -H 'accept: text/plain' \
  -H "Authorization: Bearer $TOKEN" \
  -d '{
    "request": {
      "live": false
    }
  }'
```

### 2. `/api/Position/searchOpen`

**❌ Wrong**:
```json
{
  "accountId": 15991599
}
```

**✅ Correct**:
```json
{
  "request": {
    "accountId": 15991599
  }
}
```

**Curl Example**:
```bash
curl -X POST 'https://api.topstepx.com/api/Position/searchOpen' \
  -H 'Content-Type: application/json' \
  -H 'accept: text/plain' \
  -H "Authorization: Bearer $TOKEN" \
  -d '{
    "request": {
      "accountId": 15991599
    }
  }'
```

## Endpoints Using Flat Parameters

### 1. `/api/Account/search`

**✅ Correct**:
```json
{
  "accountId": 15991599
}
```

**Curl Example**:
```bash
curl -X POST 'https://api.topstepx.com/api/Account/search' \
  -H 'Content-Type: application/json' \
  -H 'accept: text/plain' \
  -H "Authorization: Bearer $TOKEN" \
  -d '{
    "accountId": 15991599
  }'
```

### 2. `/api/Order/search`

**✅ Correct**:
```json
{
  "accountId": 15991599,
  "startTimestamp": "2026-01-15T00:00:00Z",
  "endTimestamp": "2026-01-15T23:59:59Z"
}
```

**Curl Example**:
```bash
curl -X POST 'https://api.topstepx.com/api/Order/search' \
  -H 'Content-Type: application/json' \
  -H 'accept: text/plain' \
  -H "Authorization: Bearer $TOKEN" \
  -d '{
    "accountId": 15991599,
    "startTimestamp": "2026-01-15T00:00:00Z",
    "endTimestamp": "2026-01-15T23:59:59Z"
  }'
```

### 3. `/api/Trade/search`

**✅ Correct**:
```json
{
  "accountId": 15991599,
  "startTimestamp": "2026-01-15T00:00:00Z",
  "endTimestamp": "2026-01-15T23:59:59Z"
}
```

**Curl Example**:
```bash
curl -X POST 'https://api.topstepx.com/api/Trade/search' \
  -H 'Content-Type: application/json' \
  -H 'accept: text/plain' \
  -H "Authorization: Bearer $TOKEN" \
  -d '{
    "accountId": 15991599,
    "startTimestamp": "2026-01-15T00:00:00Z",
    "endTimestamp": "2026-01-15T23:59:59Z"
  }'
```

## Error Messages Explained

### Error: "missing required properties including: 'live'"

**Cause**: Using flat format instead of nested `request` object  
**Fix**: Wrap parameters in `"request": {...}`

### Error: "The request field is required"

**Cause**: Missing `request` wrapper  
**Fix**: Add `"request": {...}` wrapper

### Error: "missing required properties including: 'accountId'"

**Cause**: Using flat format when nested `request` is required  
**Fix**: Use `{"request": {"accountId": ...}}`

## Quick Reference

| Endpoint | Format | Example |
|----------|--------|---------|
| `/api/Account/search` | Flat | `{"accountId": 123}` |
| `/api/Order/search` | Flat | `{"accountId": 123, "startTimestamp": "..."}` |
| `/api/Trade/search` | Flat | `{"accountId": 123, "startTimestamp": "..."}` |
| `/api/Contract/available` | **Nested** | `{"request": {"live": false}}` |
| `/api/Position/searchOpen` | **Nested** | `{"request": {"accountId": 123}}` |

## Testing Tips

1. **Always use `-v` flag** to see full request/response:
   ```bash
   curl -v -X POST ...
   ```

2. **Check HTTP status code**:
   - `200` = Success
   - `400` = Bad Request (check format)
   - `401` = Unauthorized (token expired)
   - `404` = Not Found (endpoint doesn't exist)

3. **Read error messages carefully**:
   - They tell you exactly what's missing
   - Look for "missing required properties" or "The request field is required"

## Common Patterns

### Pattern 1: Flat Parameters (Most Endpoints)
```json
{
  "accountId": 123,
  "startTimestamp": "2026-01-15T00:00:00Z"
}
```

### Pattern 2: Nested Request (Some Endpoints)
```json
{
  "request": {
    "accountId": 123,
    "live": false
  }
}
```

---

**Note**: When in doubt, check the error message - it will tell you what format is expected!
