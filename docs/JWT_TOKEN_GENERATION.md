# JWT Token Generation Guide

## Quick Reference

### Generate Token via cURL

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
  "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c",
  "expiresIn": 1440
}
```

### Extract Token

Copy the `token` value from the response and add to your environment:

**Local (.env file):**
```bash
JWT_TOKEN="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
```

**Railway (Environment Variables):**
1. Go to Railway Dashboard → Your Service → Variables
2. Add/Update: `JWT_TOKEN` = `eyJhbGci...`
3. Redeploy

## Python Script Method

Create `generate_jwt.py`:

```python
#!/usr/bin/env python3
import os
import requests
import json
from datetime import datetime

username = os.getenv('PROJECT_X_USERNAME')
api_key = os.getenv('PROJECT_X_API_KEY')

if not username or not api_key:
    print("❌ Error: Set PROJECT_X_USERNAME and PROJECT_X_API_KEY")
    print("\nUsage:")
    print("  export PROJECT_X_USERNAME='your_username'")
    print("  export PROJECT_X_API_KEY='your_api_key'")
    print("  python3 generate_jwt.py")
    exit(1)

print(f"🔐 Authenticating as {username}...")
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
        token = data['token']
        expires_in = data.get('expiresIn', 1440)  # Default 24 hours
        
        print(f"\n✅ Authentication successful!")
        print(f"📅 Token expires in: {expires_in} minutes ({expires_in/60:.1f} hours)")
        print(f"\n🔑 JWT Token:")
        print(f"JWT_TOKEN=\"{token}\"")
        print(f"\n📋 Add to Railway environment variables:")
        print(f"   Variable: JWT_TOKEN")
        print(f"   Value: {token}")
        print(f"\n💡 Note: Token will auto-refresh when expired if PROJECT_X_USERNAME and PROJECT_X_API_KEY are set")
    else:
        print(f"❌ Error: {data}")
else:
    print(f"❌ HTTP Error {response.status_code}")
    print(response.text)
```

**Run it:**
```bash
export PROJECT_X_USERNAME="your_username"
export PROJECT_X_API_KEY="your_api_key"
python3 generate_jwt.py
```

## Using Trading Bot

The bot can generate tokens automatically:

```python
from trading_bot import TopStepXTradingBot
import asyncio

async def get_token():
    bot = TopStepXTradingBot()
    if await bot.authenticate():
        print(f"JWT_TOKEN={bot.session_token}")
        print(f"Expires: {bot.token_expiry}")
    else:
        print("Authentication failed")

asyncio.run(get_token())
```

## Token Expiration

- **Default lifetime**: 24 hours (1440 minutes)
- **Auto-refresh**: Server automatically refreshes expired tokens if credentials are set
- **Proactive refresh**: Token refreshes when less than 5 minutes remaining

## Important Notes

1. **JWT tokens are optional** - Server will authenticate using credentials if token is missing
2. **Auto-refresh requires credentials** - Set `PROJECT_X_USERNAME` and `PROJECT_X_API_KEY` for auto-refresh
3. **Token format** - Should start with `eyJ...` (base64 encoded JWT header)
4. **Railway deployment** - Tokens in environment variables persist across deployments

## Troubleshooting

### "Invalid token format"
- Ensure token starts with `eyJ...`
- Check for extra quotes or whitespace
- Token should be a single line

### "Token expired"
- Normal - server will auto-refresh if credentials are set
- Or generate a new token using methods above

### "Authentication failed"
- Verify `PROJECT_X_USERNAME` and `PROJECT_X_API_KEY` are correct
- Check API key hasn't been revoked
- Ensure account is active

## See Also

- `JWT_AUTO_REFRESH_FIX_2025-12-02.md` - Auto-refresh functionality details
- `SIGNALR_JWT_FIX.md` - JWT token setup and SignalR integration
- `RAILWAY_DEPLOYMENT.md` - Railway deployment guide

