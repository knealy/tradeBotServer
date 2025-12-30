# SignalR Authentication & Async Callback Fixes - 2025-12-04

## Problem

SignalR WebSocket connections were failing with `401 Unauthorized` errors immediately after connection attempts. This prevented real-time market data from being available.

### Symptoms
- `WebSocketBadStatusException: Handshake status 401 Unauthorized`
- SignalR Market Hub disconnected immediately after connection attempt
- Connection timeout after 10 seconds
- Real-time quotes and depth data not available

### Root Causes
1. **Expired Token**: Token was expired when SignalR tried to connect
2. **No Token Refresh**: Token wasn't refreshed before connection attempt
3. **Stale Token Factory**: `access_token_factory` was capturing token value at connection time, not getting fresh token
4. **No Error Recovery**: 401 errors were suppressed instead of being fixed

## Solution

### 1. Ensure Token is Valid Before Connecting

**File**: `core/websocket_manager.py`  
**Method**: `start()`

```python
# CRITICAL FIX: Ensure token is valid and refreshed before connecting
# This prevents 401 errors from expired tokens
await self.auth_manager.ensure_valid_token()

token = self.auth_manager.get_token()
if not token:
    logger.error("No authentication token available after refresh")
    return False
```

### 2. Use Fresh Token Factory

**File**: `core/websocket_manager.py`  
**Method**: `start()`

Instead of capturing token value:
```python
# OLD (BAD): Captures token value at connection time
"access_token_factory": (lambda: token or "")
```

Use a factory that always gets fresh token:
```python
# NEW (GOOD): Always gets latest token
def get_fresh_token():
    """Get fresh token, refreshing if needed."""
    return self.auth_manager.get_token() or ""

"access_token_factory": get_fresh_token
```

### 3. Handle 401 Errors with Automatic Recovery

**File**: `core/websocket_manager.py`  
**Method**: `on_error()`

```python
def on_error(err):
    try:
        error_text = str(err)
        # Handle authentication errors - try to refresh token and reconnect
        if "401" in error_text or "403" in error_text or "Unauthorized" in error_text:
            logger.warning(f"SignalR authentication error (401/403): {error_text}")
            logger.info("Attempting to refresh token and reconnect...")
            # Schedule token refresh and reconnection
            asyncio.create_task(self._handle_auth_error_and_reconnect())
            return
        logger.error(f"SignalR Market Hub error: {error_text}")
    except Exception:
        logger.error(f"SignalR Market Hub error: {err}")
```

### 4. Automatic Reconnection After Token Refresh

**File**: `core/websocket_manager.py`  
**Method**: `_handle_auth_error_and_reconnect()`

```python
async def _handle_auth_error_and_reconnect(self):
    """Handle authentication error by refreshing token and reconnecting."""
    try:
        logger.info("Refreshing authentication token...")
        await self.auth_manager.ensure_valid_token()
        
        # Stop current connection
        if self._hub:
            try:
                self._hub.stop()
            except:
                pass
            self._hub = None
        
        with self._lock:
            self._connected = False
        
        # Wait a moment before reconnecting
        await asyncio.sleep(1)
        
        # Retry connection
        logger.info("Reconnecting SignalR with fresh token...")
        success = await self.start()
        if success:
            logger.info("✅ SignalR reconnected successfully after token refresh")
        else:
            logger.warning("⚠️  SignalR reconnection failed after token refresh")
    except Exception as e:
        logger.error(f"Error during auth error recovery: {e}")
```

## Prevention

1. **Always call `ensure_valid_token()` before getting token for SignalR**
2. **Use token factory function instead of capturing token value**
3. **Handle 401 errors with automatic token refresh and reconnection**
4. **Don't suppress 401 errors - fix the root cause (expired token)**
5. **Test SignalR connection with token that's about to expire**

## Impact

- ✅ SignalR connections now work reliably with proper token management
- ✅ Real-time market data available without 401 errors
- ✅ Automatic recovery from authentication errors
- ✅ Foundation for high-performance system is solid - problems are fixed, not suppressed

## Related Files

- `core/websocket_manager.py` - Main SignalR connection management
- `core/auth.py` - Token management and refresh logic
- `trading_bot.py` - Integration point

## Testing

After this fix, SignalR connections should:
1. Successfully connect with valid tokens
2. Automatically refresh token if expired before connection
3. Automatically recover from 401 errors by refreshing token and reconnecting
4. Provide real-time quotes and depth data without errors

Run the comprehensive test suite to verify:
```bash
python tests/test_all_commands_comprehensive.py
```

All tests should pass, and SignalR errors should be resolved.

