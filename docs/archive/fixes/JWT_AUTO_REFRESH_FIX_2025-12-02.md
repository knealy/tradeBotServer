# JWT Auto-Refresh Fix - December 2, 2025

## 🐛 Issue: Server Failing to Start with Expired JWT Token

### Problem
Server was failing to start on Railway with error:
```
❌ Authentication failed - cannot start server
```

Even though the code had auto-refresh functionality (`_ensure_valid_token()`), the startup code was calling `authenticate()` directly, bypassing the expiration check.

### Root Cause
**File**: `servers/async_webhook_server.py` (line 2469)

The startup code was calling:
```python
if await trading_bot.authenticate():  # ❌ Bypasses expiration check
```

Instead of:
```python
if await trading_bot._ensure_valid_token():  # ✅ Checks expiration first
```

**Why This Matters:**
- `authenticate()` always makes an API call, even if token is valid
- `_ensure_valid_token()` checks expiration first:
  - If token is valid → Returns immediately (no API call)
  - If token is expired → Calls `authenticate()` to refresh
  - If token is missing → Calls `authenticate()` to get new token

### Fix Applied

**File**: `servers/async_webhook_server.py` (line 2469)

```python
# Before
if await trading_bot.authenticate():

# After  
if await trading_bot._ensure_valid_token():
```

### How Auto-Refresh Works

1. **On Startup**:
   - Bot loads `JWT_TOKEN` from environment (if present)
   - Parses JWT to extract expiration time
   - Calls `_ensure_valid_token()`

2. **Token Validation** (`_is_token_expired()`):
   - Checks if token exists
   - Checks if token expires in less than 5 minutes
   - Returns `True` if token needs refresh

3. **Auto-Refresh** (`_ensure_valid_token()`):
   - If token is valid → Returns `True` immediately
   - If token is expired/missing → Calls `authenticate()`
   - `authenticate()` uses `PROJECT_X_USERNAME` and `PROJECT_X_API_KEY` to get new token

### Expected Behavior

**Scenario 1: Valid JWT Token**
```
Loaded JWT from environment (expires: 2025-12-03 12:00:00+00:00)
Token valid - expires in 1439.7 minutes
✅ Server starts successfully (no API call needed)
```

**Scenario 2: Expired JWT Token (with credentials)**
```
Loaded JWT from environment (expires: 2025-11-07 18:54:01+00:00)
Token expired or missing, refreshing...
Authenticating with TopStepX API...
✅ Successfully authenticated as: username
Token expires at: 2025-12-02 18:00:00+00:00
✅ Server starts successfully (auto-refreshed)
```

**Scenario 3: No JWT Token (with credentials)**
```
Token expired or missing, refreshing...
Authenticating with TopStepX API...
✅ Successfully authenticated as: username
Token expires at: 2025-12-02 18:00:00+00:00
✅ Server starts successfully (authenticated fresh)
```

**Scenario 4: Expired JWT Token (no credentials)**
```
Loaded JWT from environment (expires: 2025-11-07 18:54:01+00:00)
Token expired or missing, refreshing...
Authenticating with TopStepX API...
❌ HTTP error 403: Forbidden
❌ Authentication failed - cannot start server
```

## 📋 Required Environment Variables

### For Auto-Refresh to Work

**Required:**
```bash
PROJECT_X_USERNAME="your_username"
PROJECT_X_API_KEY="your_api_key"
```

**Optional (but recommended):**
```bash
JWT_TOKEN="eyJhbGci..."  # Speeds up startup if valid
```

### Railway Setup

1. **Go to Railway Dashboard** → Your Service → Variables
2. **Add/Update these variables:**
   ```
   PROJECT_X_USERNAME=your_username
   PROJECT_X_API_KEY=your_api_key
   JWT_TOKEN=eyJhbGci...  # Optional, but recommended
   ```
3. **Redeploy** - Server will auto-refresh expired tokens

## 🔑 Generating a New JWT Token

See `SIGNALR_JWT_FIX.md` for detailed instructions on generating new JWT tokens.

**Quick Method:**
```bash
curl -X POST https://api.topstepx.com/api/Auth/loginKey \
  -H "Content-Type: application/json" \
  -H "accept: text/plain" \
  -d '{
    "userName": "your_username",
    "apiKey": "your_api_key"
  }'
```

Copy the `token` from the response and add to Railway as `JWT_TOKEN`.

## ✅ Verification

After deploying, check logs for:
```
✅ Successfully authenticated as: username
Token expires at: 2025-12-02 18:00:00+00:00
✅ Server starts successfully
```

If you see:
```
❌ Authentication failed - cannot start server
```

Check:
1. `PROJECT_X_USERNAME` is set correctly
2. `PROJECT_X_API_KEY` is set correctly
3. Credentials are valid (not expired/revoked)

## 📝 Key Takeaways

1. **Always use `_ensure_valid_token()`** instead of `authenticate()` directly
2. **Set both JWT token and credentials** for best results:
   - JWT token → Fast startup if valid
   - Credentials → Auto-refresh if JWT expires
3. **Auto-refresh is automatic** - no manual intervention needed
4. **Server will start** even with expired JWT tokens (if credentials are valid)

## 🔗 Related Documentation

- `SIGNALR_JWT_FIX.md` - JWT token setup and SignalR integration
- `RAILWAY_DEPLOYMENT.md` - Railway deployment guide
- `ENV_CONFIGURATION.md` - Environment variable reference

