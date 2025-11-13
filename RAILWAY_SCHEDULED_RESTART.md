# Railway Scheduled Strategy Restart Setup

## Overview
The trading bot now includes a built-in scheduled task manager that automatically restarts the overnight range strategy at **8:00 AM ET every weekday** to ensure it's ready for market open at 9:30 AM ET.

## How It Works

### Automatic Strategy Restart
- **Time**: 8:00 AM ET (Eastern Time)
- **Days**: Monday through Friday (weekdays only)
- **Action**: Stops and restarts the `overnight_range` strategy
- **Purpose**: Ensures strategy is fresh and ready for market open at 9:30 AM ET

### Implementation
The scheduled task manager runs as a background task within the async webhook server:
- Checks time every minute
- Executes restart at 8:00-8:05 AM window (prevents duplicate executions)
- Only runs on weekdays (Monday-Friday)
- Uses ET timezone (automatically handles EST/EDT)

## Configuration

### Environment Variables (Optional)
You can customize the restart time via environment variables (future enhancement):
```bash
# Default is 8:00 AM ET
STRATEGY_RESTART_TIME=08:00
STRATEGY_RESTART_TIMEZONE=US/Eastern
```

### Current Settings
- **Restart Time**: 8:00 AM ET
- **Timezone**: US/Eastern (automatically handles daylight saving)
- **Days**: Weekdays only (Monday-Friday)

## Verification

### Check Next Restart Time
```bash
# Via API
curl http://your-railway-url/api/scheduled-tasks

# Response:
{
  "enabled": true,
  "restart_time": "08:00",
  "timezone": "US/Eastern",
  "next_restart": "2025-11-13T08:00:00-05:00",
  "last_restart_date": null
}
```

### Check Logs
When the server starts, you'll see:
```
📅 Scheduled task manager initialized
📅 Scheduled Task Manager initialized
   Strategy restart: 08:00 ET (weekdays only)
📅 Next strategy restart scheduled: 2025-11-13 08:00:00 EST-05:00
```

When restart executes (at 8 AM ET on weekdays):
```
⏰ Scheduled strategy restart triggered at 08:00:15 ET
🔄 Restarting strategy: overnight_range
   Stopping overnight_range...
   ✅ Strategy stopped: overnight_range
   Starting overnight_range...
   ✅ Strategy started: overnight_range on MNQ
✅ Strategy restart completed successfully
```

## Railway Deployment

### No Additional Configuration Needed
The scheduled task manager is **automatically enabled** when the server starts. No Railway-specific configuration is required.

### How It Works on Railway
1. Railway keeps your service running 24/7
2. The scheduled task manager runs as a background task
3. At 8:00 AM ET on weekdays, it automatically restarts the strategy
4. Strategy is ready for market open at 9:30 AM ET

### Benefits
- ✅ **No external cron services needed**
- ✅ **Self-contained** (runs within your application)
- ✅ **Automatic** (no manual intervention)
- ✅ **Timezone-aware** (handles EST/EDT automatically)
- ✅ **Weekday-only** (skips weekends)

## Troubleshooting

### Strategy Not Restarting
1. **Check if scheduled task manager is running**:
   ```bash
   curl http://your-railway-url/api/scheduled-tasks
   ```

2. **Check server logs** for:
   - "📅 Scheduled task manager initialized"
   - "⏰ Scheduled strategy restart triggered"

3. **Verify timezone**:
   - Server should be using ET timezone
   - Check logs for timezone information

4. **Check if it's a weekday**:
   - Restarts only happen Monday-Friday
   - Weekends are skipped

### Manual Strategy Restart
If you need to manually restart the strategy:
```bash
# Via API
curl -X POST http://your-railway-url/api/strategies/overnight_range/stop
curl -X POST http://your-railway-url/api/strategies/overnight_range/start
```

## Alternative: Railway Cron Jobs (If Needed)

If you prefer Railway's native cron functionality (requires Railway Pro plan):

### Option 1: Railway Cron Service
1. Create a new service in Railway
2. Set it as a "Cron" service type
3. Configure schedule: `0 8 * * 1-5` (8 AM ET, weekdays)
4. Command: `curl -X POST http://your-main-service/api/strategies/overnight_range/restart`

### Option 2: External Cron Service
Use a free service like [cron-job.org](https://cron-job.org):
- Schedule: 8:00 AM ET, Monday-Friday
- URL: `https://your-railway-url/api/strategies/overnight_range/restart`
- Method: POST

**Note**: The built-in scheduled task manager is recommended as it's simpler and doesn't require external services.

## Summary

✅ **Automatic**: Strategy restarts at 8 AM ET every weekday  
✅ **No Configuration**: Works out of the box on Railway  
✅ **Self-Contained**: No external services needed  
✅ **Reliable**: Runs within your application process  

The strategy will now automatically restart every weekday morning, ensuring it's ready for market open at 9:30 AM ET!

