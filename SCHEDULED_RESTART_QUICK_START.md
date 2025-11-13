# Scheduled Strategy Restart - Quick Start

## ✅ What's Been Set Up

Your trading bot now **automatically restarts the overnight range strategy at 8:00 AM ET every weekday** on Railway.

## How It Works

1. **Automatic**: No configuration needed - it's built into the server
2. **Time**: 8:00 AM ET (Eastern Time) - 1.5 hours before market open
3. **Days**: Monday through Friday only (weekends skipped)
4. **Action**: Stops and restarts the `overnight_range` strategy

## What Happens

### At 8:00 AM ET (Weekdays):
```
⏰ Scheduled strategy restart triggered at 08:00:15 ET
🔄 Restarting strategy: overnight_range
   Stopping overnight_range...
   ✅ Strategy stopped: overnight_range
   Starting overnight_range...
   ✅ Strategy started: overnight_range on MNQ
✅ Strategy restart completed successfully
```

### At 9:30 AM ET:
The strategy executes trades based on overnight range data.

## Verification

### Check Next Restart Time
```bash
# Via API (replace with your Railway URL)
curl https://your-railway-url.railway.app/api/scheduled-tasks

# Response:
{
  "enabled": true,
  "restart_time": "08:00",
  "timezone": "US/Eastern",
  "next_restart": "2025-11-13T08:00:00-05:00",
  "last_restart_date": null
}
```

### Check Logs on Railway
1. Go to Railway dashboard
2. Click on your service
3. View logs
4. Look for:
   - `📅 Scheduled task manager initialized`
   - `📅 Next strategy restart scheduled: ...`
   - `⏰ Scheduled strategy restart triggered` (at 8 AM)

## No Action Required

✅ **Already configured** - Works automatically when deployed to Railway  
✅ **No Railway settings needed** - Runs as background task  
✅ **No external services** - Self-contained in your application  

## Troubleshooting

### If Strategy Doesn't Restart

1. **Check if it's a weekday** - Restarts only happen Mon-Fri
2. **Check server logs** for scheduled task manager messages
3. **Verify timezone** - Should show "US/Eastern" in logs
4. **Check API endpoint**: `GET /api/scheduled-tasks`

### Manual Restart (If Needed)

```bash
# Stop strategy
curl -X POST https://your-railway-url/api/strategies/overnight_range/stop

# Start strategy  
curl -X POST https://your-railway-url/api/strategies/overnight_range/start
```

## Summary

🎯 **Goal**: Ensure strategy is running before 9:30 AM market open  
✅ **Solution**: Automatic restart at 8:00 AM ET every weekday  
🚀 **Status**: Ready to deploy to Railway - no additional setup needed!

The strategy will now automatically restart every weekday morning, ensuring it's fresh and ready for market open! 🎉

