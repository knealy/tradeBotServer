# Environment Variables Configuration

The webhook server now reads all configuration from environment variables, making it easy to configure through Railway or any other deployment platform.

## Required Environment Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `PROJECT_X_API_KEY` | Your TopStepX API key | `0Gb7bgF05DjaLqRTkZgpP1zmmPE20Vr5gCZay9TpCR0=` |
| `PROJECT_X_USERNAME` | Your TopStepX username | `cloutrades` |

## Optional Environment Variables

| Variable | Description | Default | Example |
|----------|-------------|---------|---------|
| `PROJECT_X_ACCOUNT_ID` | Specific account ID to trade on | Auto-select first account | `11481693` |
| `POSITION_SIZE` | Number of contracts per position | `1` | `2` |
| `CLOSE_ENTIRE_POSITION_AT_TP1` | Close entire position at TP1 instead of partial close | `false` | `true` |
| `WEBHOOK_HOST` | Host to bind the webhook server to | `0.0.0.0` | `0.0.0.0` |
| `PORT` | Port to bind the webhook server to | `8080` | `8080` |
| `TP1_FRACTION` | Fraction of position to assign to TP1 when staged exits are used (0-1) | `0.75` | `0.6` |
| `IGNORE_TP1_SIGNALS` | If `true`, TP1 webhook signals are ignored (OCO-managed) | `true` | `false` |

## Boolean Values

For `CLOSE_ENTIRE_POSITION_AT_TP1`, the following values are considered `true`:
- `true`
- `1` 
- `yes`
- `on`

Any other value (including `false`, `0`, `no`, `off`) is considered `false`.

## Railway Configuration Example

In your Railway project, set these environment variables:

```
PROJECT_X_API_KEY=your_api_key_here
PROJECT_X_USERNAME=your_username_here
PROJECT_X_ACCOUNT_ID=11481693
POSITION_SIZE=2
CLOSE_ENTIRE_POSITION_AT_TP1=true
TP1_FRACTION=0.75
IGNORE_TP1_SIGNALS=true
```

## Configuration Changes

To change any configuration:

1. **Update environment variables** in Railway dashboard
2. **Redeploy** the application:
   ```bash
   git commit --allow-empty -m "Redeploy for config changes"
   git push origin main
   ```

The server will restart with the new configuration and log all settings on startup.

## Startup Logs

When the server starts, it will log the configuration:

```
=== WEBHOOK SERVER CONFIGURATION ===
API Key: ***CR0=
Username: cloutrades
Account ID: 11481693
Position Size: 2 contracts
Close Entire at TP1: True
Host: 0.0.0.0
Port: 8080
=====================================
```

This makes it easy to verify that your configuration is correct.
