# Pre-Deployment Checklist

## Before deploying to Railway, verify locally:

### 1. Webhook Server Still Works
```bash
# Start webhook server
python3 start_webhook.py

# Test webhook endpoint (in another terminal)
curl -X POST http://localhost:8080/webhook \
  -H "Content-Type: application/json" \
  -d '{"action":"test","symbol":"MNQ","price":16000}'

# Should return 200 OK
```

### 2. Trading Bot Functions Correctly
```bash
# Run test script
python3 test_webhook.py

# Check logs for errors
tail -f webhook_server.log
```

### 3. Environment Variables Load Correctly
```bash
# Verify env vars are read (not overridden by hardcoded values)
python3 -c "from load_env import *; import os; print('POSITION_SIZE:', os.getenv('POSITION_SIZE', '1'))"
```

### 4. New FastAPI Backend Doesn't Interfere
```bash
# Start both servers simultaneously
python3 start_webhook.py &  # Port 8080
cd backend && uvicorn main:app --port 8001 &  # Port 8001

# Test both are running
curl http://localhost:8080/health
curl http://localhost:8001/api/health

# Kill test servers
pkill -f "start_webhook.py"
pkill -f "uvicorn main:app"
```

## Post-Deployment Monitoring (First Week)

- [ ] Webhook response times < 5 seconds
- [ ] All TradingView signals received (check logs)
- [ ] Orders placing successfully (verify in TopStepX)
- [ ] No "ORDER VERIFICATION FAILED" errors
- [ ] Discord notifications accurate
- [ ] FastAPI backend responding (if deployed)
- [ ] Database connections stable (if deployed)

## Set up alerts:
- Railway deployment errors → Email notification
- Webhook server down → Discord alert
- Order placement failures → Immediate notification
