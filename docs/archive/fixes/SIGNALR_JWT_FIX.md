# SignalR Market Hub JWT Integration Fix

## Changes Made

### 1. JWT Token from Environment Variable
**File:** `trading_bot.py`

Added support for loading JWT token directly from `.env` file:
```python
JWT_TOKEN="eyJhbGci..."
```

The bot now:
- Reads `JWT_TOKEN` from environment on startup
- Parses the JWT to extract expiration time
- Uses this token for all API calls if available
- **Automatically refreshes expired tokens** using `_ensure_valid_token()`
- Falls back to authentication via username/API key if not present

**Benefits:**
- No need to authenticate on every restart (useful for Railway)
- Faster startup time
- **Auto-refresh**: Expired tokens are automatically refreshed on startup
- Token recycling mechanism still works when token approaches expiration

### ⚠️ Important: Auto-Refresh Implementation

**Critical Quirk**: The server startup code uses `_ensure_valid_token()` instead of `authenticate()` directly.

**Why?**
- `_ensure_valid_token()` checks if the token is expired first
- If expired, it automatically calls `authenticate()` to get a fresh token
- If valid, it uses the existing token (no API call needed)

**File**: `servers/async_webhook_server.py` (line 2470)
```python
# ✅ CORRECT - Uses auto-refresh
if await trading_bot._ensure_valid_token():
    auth_success = True

# ❌ WRONG - Bypasses expiration check
if await trading_bot.authenticate():
    auth_success = True
```

**What This Means:**
- If `JWT_TOKEN` in environment is expired, the server will automatically get a new token
- No manual intervention needed - just ensure `PROJECT_X_USERNAME` and `PROJECT_X_API_KEY` are set
- Server will start successfully even with expired JWT tokens

### 2. Simplified SignalR Subscription Method
**File:** `trading_bot.py` - `_ensure_quote_subscription()`

**Before:** Tried multiple subscription methods and payload formats (shotgun approach)

**After:** Uses the **exact** ProjectX Gateway API method:
```python
# Per ProjectX docs: https://gateway.docs.projectx.com/docs/realtime/
self._market_hub.send("SubscribeContractQuotes", [contract_id])
```

**Parameters:**
- Method: `SubscribeContractQuotes`
- Payload: `[contractId]` (e.g., `["CON.F.US.MNQ.Z25"]`)
- Event received: `GatewayQuote`

### 3. GatewayQuote Event Handling
**File:** `trading_bot.py` - `on_quote()` handler

Already supports GatewayQuote payload fields:
- `bestBid` → mapped to `bid`
- `bestAsk` → mapped to `ask`
- `lastPrice` → mapped to `last`
- `volume` → mapped to `volume`

Event listeners registered for:
1. `GatewayQuote` (primary, per ProjectX docs)
2. `GatewayQuoteWithConflation` (conflated quotes)
3. Other fallback event names for compatibility

## How to Test

### 1. Verify JWT Token is Loaded
```bash
# Start the bot
venv/bin/python trading_bot.py
```

Look for this log message:
```
INFO - Loaded JWT from environment (expires: 2025-12-07 12:00:00+00:00)
```

### 2. Test Real-time Quotes
```bash
# In the trading interface:
quote mnq
```

**Expected behavior:**
1. SignalR hub connects with JWT
2. Subscription request sent: `SubscribeContractQuotes(["CON.F.US.MNQ.Z25"])`
3. Within 0.5 seconds, you should see:
   ```
   📶 Raw quote event #1: args=(...)
   ```
4. Quote returned with live bid/ask:
   ```json
   {
     "bid": 21234.50,
     "ask": 21234.75,
     "last": 21234.50,
     "volume": 123456,
     "source": "signalr"
   }
   ```

### 3. Check for Errors
If subscription fails, you'll see detailed error logging:
```
❌ Failed to subscribe to quotes for MNQ: <error details>
ERROR - SignalR Market Hub error: <actual server response>
```

The `on_error` handler now logs the exact error from the ProjectX server's `CompletionMessage`.

## SignalR Connection Flow

```
1. Bot starts → Loads JWT_TOKEN from .env
2. User runs 'quote mnq' → _ensure_market_socket_started()
3. Hub connects to wss://rtc.topstepx.com/hubs/market?access_token=<JWT>
4. Connection headers include: Authorization: Bearer <JWT>
5. Hub connected → on_open() fires
6. _ensure_quote_subscription("MNQ") called
7. Get contract ID: CON.F.US.MNQ.Z25
8. Send: SubscribeContractQuotes(["CON.F.US.MNQ.Z25"])
9. Receive: GatewayQuote event with bid/ask/last/volume
10. Cache quote data in _quote_cache
11. Return quote to user
```

## Troubleshooting

### Issue: "SignalR Market Hub error: Unauthorized"
**Solution:** JWT token expired or invalid. Check:
1. Token expiration: `exp` claim in JWT
2. Token format: Should start with `eyJ...`
3. User permissions: Token must have market data entitlements
4. **Auto-refresh**: Ensure `PROJECT_X_USERNAME` and `PROJECT_X_API_KEY` are set for auto-refresh

### Issue: "Authentication failed - cannot start server"
**Solution:** This happens when:
1. JWT token is expired AND
2. `PROJECT_X_USERNAME` or `PROJECT_X_API_KEY` are missing/invalid

**Fix:**
- Ensure `PROJECT_X_USERNAME` and `PROJECT_X_API_KEY` are set in Railway
- Or generate a new JWT token and add it to `JWT_TOKEN` environment variable
- The server will auto-refresh expired tokens if credentials are valid

### Issue: "HTTP error 404: Not Found for url: .../quote/CON.F.US.MNQ.Z25"
**Solution:** This is expected! The REST endpoint fallback tries multiple identifier formats. If SignalR is working, this error is harmless.

### Issue: Quote returns `"source": "bars"` instead of `"source": "signalr"`
**Solution:** SignalR subscription didn't work. Check:
1. JWT token is valid (not expired)
2. Contract ID is correct for the symbol
3. Check logs for "Failed to subscribe" messages
4. Server may reject if market is closed or symbol is invalid

## Environment Variables Reference

Required for SignalR real-time quotes:
```bash
# Option 1: Use JWT directly (faster, recommended for Railway)
# Note: If expired, will auto-refresh using Option 2 credentials
JWT_TOKEN="eyJhbGci..."

# Option 2: Authenticate on startup (required for auto-refresh)
# These are REQUIRED if JWT_TOKEN is expired or missing
PROJECT_X_USERNAME="your_username"
PROJECT_X_API_KEY="your_api_key"
```

### Recommended Setup (Auto-Refresh Enabled)

For Railway deployment, set **both** JWT token and credentials:
```bash
# JWT token (optional, but speeds up startup if valid)
JWT_TOKEN="eyJhbGci..."

# Credentials (REQUIRED for auto-refresh when JWT expires)
PROJECT_X_USERNAME="your_username"
PROJECT_X_API_KEY="your_api_key"
```

**Why both?**
- If `JWT_TOKEN` is valid → Fast startup (no API call)
- If `JWT_TOKEN` is expired → Auto-refreshes using credentials
- If `JWT_TOKEN` is missing → Authenticates using credentials

## Generating a New JWT Token

If you want to generate a fresh JWT token to add to your environment variables:

### Method 1: Using the Trading Bot (Recommended)

The bot can generate a new token automatically. Simply ensure credentials are set:

```bash
# Set credentials in environment
export PROJECT_X_USERNAME="your_username"
export PROJECT_X_API_KEY="your_api_key"

# Start the bot - it will authenticate and get a new token
python3 trading_bot.py
```

Look for this log message:
```
INFO - Token expires at: 2025-12-02 18:00:00+00:00
```

The token is stored in `self.session_token`. To extract it:

```python
# In Python shell or script
from trading_bot import TopStepXTradingBot
import asyncio

async def get_token():
    bot = TopStepXTradingBot()
    if await bot.authenticate():
        print(f"JWT_TOKEN={bot.session_token}")
        print(f"Expires: {bot.token_expiry}")

asyncio.run(get_token())
```

### Method 2: Using cURL (Direct API Call)

```bash
curl -X POST https://api.topstepx.com/api/Auth/loginKey \
  -H "Content-Type: application/json" \
  -H "accept: text/plain" \
  -d '{
    "userName": "your_username",
    "apiKey": "your_api_key"
  }'
```

**Response:**
```json
{
  "success": true,
  "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "expiresIn": 1440
}
```

Copy the `token` value and add it to your environment:
```bash
JWT_TOKEN="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
```

### Method 3: Using Python Script

Create a script `generate_jwt.py`:
```python
#!/usr/bin/env python3
import os
import requests
import json

username = os.getenv('PROJECT_X_USERNAME')
api_key = os.getenv('PROJECT_X_API_KEY')

if not username or not api_key:
    print("Error: Set PROJECT_X_USERNAME and PROJECT_X_API_KEY environment variables")
    exit(1)

response = requests.post(
    'https://api.topstepx.com/api/Auth/loginKey',
    headers={
        'Content-Type': 'application/json',
        'accept': 'text/plain'
    },
    json={
        'userName': username,
        'apiKey': api_key
    }
)

if response.status_code == 200:
    data = response.json()
    if data.get('success') and data.get('token'):
        print(f"JWT_TOKEN={data['token']}")
        print(f"\nAdd this to your .env file or Railway environment variables:")
        print(f"JWT_TOKEN=\"{data['token']}\"")
    else:
        print(f"Error: {data}")
else:
    print(f"Error: HTTP {response.status_code}")
    print(response.text)
```

Run it:
```bash
export PROJECT_X_USERNAME="your_username"
export PROJECT_X_API_KEY="your_api_key"
python3 generate_jwt.py
```

### Adding Token to Railway

1. **Copy the token** from any method above
2. **Go to Railway Dashboard** → Your Service → Variables
3. **Add or Update**:
   - Variable: `JWT_TOKEN`
   - Value: `eyJhbGci...` (the full token)
4. **Redeploy** or the server will auto-refresh on next restart

### Token Expiration

JWT tokens typically expire after **24 hours** (1440 minutes). The bot:
- Checks expiration on startup
- Auto-refreshes if expired (requires `PROJECT_X_USERNAME` and `PROJECT_X_API_KEY`)
- Proactively refreshes if less than 5 minutes remaining

Optional SignalR configuration:
```bash
# Override SignalR hub URL (default: https://rtc.topstepx.com/hubs/market)
PROJECT_X_MARKET_HUB_URL="https://rtc.topstepx.com/hubs/market"

# Override event name (default: Quote, but GatewayQuote is also registered)
PROJECT_X_QUOTE_EVENT="GatewayQuote"
```

## Next Steps

1. **Test locally** with `quote mnq` command
2. **Deploy to Railway** with `JWT_TOKEN` in environment variables
3. **Monitor logs** for successful `GatewayQuote` events
4. **Test strategy automation** to ensure real-time quotes flow to strategies

## References

- [ProjectX Gateway API - Realtime Updates](https://gateway.docs.projectx.com/docs/realtime/)
- SignalR Core Python Client: [github.com/mandrewcito/signalrcore](https://github.com/mandrewcito/signalrcore)

