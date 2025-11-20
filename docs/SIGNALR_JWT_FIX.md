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
- Falls back to authentication via username/API key if not present

**Benefits:**
- No need to authenticate on every restart (useful for Railway)
- Faster startup time
- Token recycling mechanism still works when token approaches expiration

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
JWT_TOKEN="eyJhbGci..."

# Option 2: Authenticate on startup (traditional)
PROJECT_X_USERNAME="your_username"
PROJECT_X_API_KEY="your_api_key"
```

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

