# Trading Bot Monitoring Guide

## Quick Health Checks

### 1. SignalR Connection Status

**Good Signs:**
```
✅ SignalR Market Hub connected
✅ SignalR Market Hub connection established and ready
✅ Subscribed to quotes for MNQ via CON.F.US.MNQ.H26
📈 Quote #1 for MNQ: $25843.75 (vol: 1426255) → bar aggregator
```

**Bad Signs:**
```
❌ Cannot subscribe to quotes for MNQ: Hub is not running
⚠️  SignalR and REST quote unavailable, falling back to bars API
🐌 SLOW API CALL: POST /api/History/retrieveBars took 2375ms
```

### 2. EventBus Status

**Good Signs:**
```
📡 Event bus started for strategy executor
📡 Subscribed to User Hub updates
✅ SignalR User Hub connection established
```

**Bad Signs:**
```
⚠️  EventBus not running, event dropped
❌ Failed to start event bus
```

### 3. Quote Flow

**Good Signs:**
```
📈 Quote #1 for MNQ: $25843.75 (vol: 1426255) → bar aggregator
📈 Quote flow confirmed for MNQ (suppressing further logs)
```

**Bad Signs:**
```
Fetching fallback bars for MNQ  # Should be rare/never
Cannot subscribe to quotes for MNQ
```

### 4. Performance Metrics

**Good Signs:**
```
⚡ Rust OCO bracket execution: 149.01ms
✅ Retrieved 300 bars from adapter (canonical implementation)
```

**Bad Signs:**
```
🐌 SLOW API CALL: POST /api/History/retrieveBars took 2375ms
⚠️  Rust execution failed, falling back to Python
```

## Log Monitoring Commands

### Real-time Monitoring
```bash
# Watch for errors
tail -f trading_bot.log | grep -E "(ERROR|WARNING|❌|⚠️)"

# Watch SignalR status
tail -f trading_bot.log | grep -E "(SignalR|Hub|Subscribe)"

# Watch quote flow
tail -f trading_bot.log | grep -E "(Quote|📈)"

# Watch EventBus
tail -f trading_bot.log | grep -E "(EventBus|📡)"

# Watch performance
tail -f trading_bot.log | grep -E "(SLOW|⚡|Rust)"
```

### Health Check Script
```bash
#!/bin/bash
# Check last 100 lines for issues

echo "=== SignalR Status ==="
tail -100 trading_bot.log | grep "SignalR.*connected" | tail -1

echo -e "\n=== EventBus Status ==="
tail -100 trading_bot.log | grep "Event bus started" | tail -1

echo -e "\n=== Quote Flow ==="
tail -100 trading_bot.log | grep "Quote flow confirmed" | tail -1

echo -e "\n=== Recent Errors ==="
tail -100 trading_bot.log | grep -E "(ERROR|❌)" | tail -5

echo -e "\n=== Recent Warnings ==="
tail -100 trading_bot.log | grep -E "(WARNING|⚠️)" | tail -5

echo -e "\n=== Slow API Calls ==="
tail -100 trading_bot.log | grep "SLOW API CALL" | tail -3
```

## Key Metrics to Track

### Connection Health
- **SignalR Hub:** Should connect within 1-2 seconds
- **Subscriptions:** Should succeed on first or second attempt
- **Quote Flow:** Should start within 5 seconds of subscription

### Performance
- **API Calls:** Should be minimal (<1 per minute during normal operation)
- **Response Times:** Should be <200ms for most operations
- **Rust Execution:** Should be 100-200ms for order placement

### Event Processing
- **EventBus:** Should process events without dropping
- **Account Updates:** Should arrive within 1 second of changes
- **Position Updates:** Should be real-time via SignalR

## Troubleshooting

### Issue: "Hub is not running"

**Diagnosis:**
```bash
grep "Hub is not running" trading_bot.log | tail -10
```

**Solutions:**
1. Check if hub connection succeeded
2. Verify 0.5s delay is in place
3. Check retry logic (should attempt 3 times)
4. Restart strategy executor

### Issue: HTTP Fallback Triggered

**Diagnosis:**
```bash
grep "Fetching fallback bars" trading_bot.log | wc -l
```

**Solutions:**
1. Check SignalR connection status
2. Verify subscriptions succeeded
3. Check quote cache population
4. Restart WebSocket manager

### Issue: EventBus Not Running

**Diagnosis:**
```bash
grep "EventBus not running" trading_bot.log | tail -10
```

**Solutions:**
1. Verify strategy executor started EventBus
2. Check for startup errors
3. Restart strategy executor

### Issue: Slow API Calls

**Diagnosis:**
```bash
grep "SLOW API CALL" trading_bot.log | tail -10
```

**Solutions:**
1. Check network connectivity
2. Verify API rate limits not exceeded
3. Ensure SignalR is handling quotes (not HTTP fallback)
4. Check for concurrent request stacking

## Dashboard Indicators

### Green (Healthy)
- ✅ SignalR connected
- ✅ Quotes flowing via WebSocket
- ✅ EventBus processing events
- ✅ API calls <1/min
- ✅ Response times <200ms

### Yellow (Warning)
- ⚠️  Occasional HTTP fallback
- ⚠️  Response times 200-500ms
- ⚠️  API calls 1-5/min
- ⚠️  Subscription retries needed

### Red (Critical)
- ❌ SignalR disconnected
- ❌ Constant HTTP fallback
- ❌ EventBus not running
- ❌ Response times >500ms
- ❌ API calls >5/min

## Automated Alerts

### Critical Alerts (Immediate Action)
1. SignalR connection failed
2. EventBus stopped
3. API response times >1000ms
4. Error rate >10/min

### Warning Alerts (Monitor)
1. HTTP fallback triggered >3 times/hour
2. Subscription retries >5/min
3. API response times >500ms
4. Warning rate >5/min

## Performance Baselines

### Normal Operation
- **API Calls:** 0-1 per minute
- **Quote Latency:** <10ms
- **Order Execution:** 100-200ms (Rust)
- **CPU Usage:** <10%
- **Memory:** <500MB
- **Network:** WebSocket only (~100 bytes/quote)

### Under Load (Multiple Strategies)
- **API Calls:** 1-3 per minute
- **Quote Latency:** <50ms
- **Order Execution:** 150-300ms
- **CPU Usage:** <25%
- **Memory:** <1GB
- **Network:** WebSocket + occasional HTTP

## Contact & Escalation

### Self-Service
1. Check this monitoring guide
2. Review recent logs
3. Restart affected components
4. Check documentation in `docs/`

### Escalation Path
1. Review `docs/FIXES_SUMMARY_2026_01_15.md`
2. Check `docs/SIGNALR_EVENT_DRIVEN_FIXES_2026_01_15.md`
3. Review `docs/EVENT_DRIVEN_ARCHITECTURE.md`
4. Consult `.cursor/context_profile.json`

---

**Last Updated:** January 15, 2026  
**Version:** 1.0
