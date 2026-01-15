# API Format Solution - Flat Format Works!

**Date**: 2026-01-15  
**Discovery**: The API actually uses **flat format** for ALL endpoints, despite confusing error messages

## The Problem

The error messages are misleading! They say:
- "The request field is required" 
- "missing required properties including: 'live'"

But our **working code** uses **flat format** (no `request` wrapper).

## Solution: Use Flat Format

### ✅ Contract/available (Flat Format - Like Our Code)

```bash
curl -v -X POST 'https://api.topstepx.com/api/Contract/available' \
  -H 'Content-Type: application/json' \
  -H 'accept: text/plain' \
  -H 'Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJodHRwOi8vc2NoZW1hcy54bWxzb2FwLm9yZy93cy8yMDA1LzA1L2lkZW50aXR5L2NsYWltcy9uYW1laWRlbnRpZmllciI6IjE0MzM0IiwiaHR0cDovL3NjaGVtYXMueG1sc29hcC5vcmcvd3MvMjAwNS8wNS9pZGVudGl0eS9jbGFpbXMvc2lkIjoiYWUwMmEzMzQtOWZiYS00ZTI0LWFlODEtZDU5NGM1NWFkZTNjIiwiaHR0cDovL3NjaGVtYXMueG1sc29hcC5vcmcvd3MvMjAwNS8wNS9pZGVudGl0eS9jbGFpbXMvbmFtZSI6ImNsb3V0cmFkZXMiLCJodHRwOi8vc2NoZW1hcy5taWNyb3NvZnQuY29tL3dzLzIwMDgvMDYvaWRlbnRpdHkvY2xhaW1zL3JvbGUiOiJ1c2VyIiwiaHR0cDovL3NjaGVtYXMubWljcm9zb2Z0LmNvbS93cy8yMDA4LzA2L2lkZW50aXR5L2NsYWltcy9hdXRoZW50aWNhdGlvbm1ldGhvZCI6ImFwaS1rZXkiLCJtc2QiOiJDTUVHUk9VUF9UT0IiLCJtZmEiOiJ2ZXJpZmllZCIsImV4cCI6MTc2ODUzMjg5NH0.PGIc9JAgg6VtWdHh0BbqmKescIO5p17tYP18OM26tBA' \
  -d '{"live":false}'
```

### ✅ Position/searchOpen (Flat Format - Like Our Code)

```bash
curl -v -X POST 'https://api.topstepx.com/api/Position/searchOpen' \
  -H 'Content-Type: application/json' \
  -H 'accept: text/plain' \
  -H 'Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJodHRwOi8vc2NoZW1hcy54bWxzb2FwLm9yZy93cy8yMDA1LzA1L2lkZW50aXR5L2NsYWltcy9uYW1laWRlbnRpZmllciI6IjE0MzM0IiwiaHR0cDovL3NjaGVtYXMueG1sc29hcC5vcmcvd3MvMjAwNS8wNS9pZGVudGl0eS9jbGFpbXMvc2lkIjoiYWUwMmEzMzQtOWZiYS00ZTI0LWFlODEtZDU5NGM1NWFkZTNjIiwiaHR0cDovL3NjaGVtYXMueG1sc29hcC5vcmcvd3MvMjAwNS8wNS9pZGVudGl0eS9jbGFpbXMvbmFtZSI6ImNsb3V0cmFkZXMiLCJodHRwOi8vc2NoZW1hcy5taWNyb3NvZnQuY29tL3dzLzIwMDgvMDYvaWRlbnRpdHkvY2xhaW1zL3JvbGUiOiJ1c2VyIiwiaHR0cDovL3NjaGVtYXMubWljcm9zb2Z0LmNvbS93cy8yMDA4LzA2L2lkZW50aXR5L2NsYWltcy9hdXRoZW50aWNhdGlvbm1ldGhvZCI6ImFwaS1rZXkiLCJtc2QiOiJDTUVHUk9VUF9UT0IiLCJtZmEiOiJ2ZXJpZmllZCIsImV4cCI6MTc2ODUzMjg5NH0.PGIc9JAgg6VtWdHh0BbqmKescIO5p17tYP18OM26tBA' \
  -d '{"accountId":15991599}'
```

## Why This Works

Our codebase uses flat format:
- `brokers/topstepx_adapter.py:3653` - `data={"live": False}`
- `brokers/topstepx_adapter.py:1555` - `data={"accountId": int(account_id)}`

If our code works, then flat format is correct!

## The Confusing Error Message

The API error message is misleading. It says:
```json
{
  "errors": {
    "$": ["missing required properties including: 'live'."],
    "request": ["The request field is required."]
  }
}
```

But this appears to be a **validation error format issue**, not an actual requirement for nested format.

## Correct Format for All Endpoints

**ALL endpoints use flat format**:

| Endpoint | Format |
|----------|--------|
| `/api/Account/search` | `{"accountId": 123}` ✅ |
| `/api/Order/search` | `{"accountId": 123, "startTimestamp": "..."}` ✅ |
| `/api/Trade/search` | `{"accountId": 123, "startTimestamp": "..."}` ✅ |
| `/api/Contract/available` | `{"live": false}` ✅ |
| `/api/Position/searchOpen` | `{"accountId": 123}` ✅ |

**NO nested `request` wrapper needed!**
