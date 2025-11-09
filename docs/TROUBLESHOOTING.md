# 🐛 Troubleshooting Guide

**Last Updated**: November 9, 2025

---

## Common Issues & Solutions

### **1. Task Queue Errors: "coroutine object is not callable"**

**Error:**
```
❌ Task error: periodic_fill_check - 'coroutine' object is not callable
```

**Cause:** Periodic tasks were being passed as coroutine objects instead of callables.

**Solution:** ✅ Fixed in latest commit
- Wrapped periodic tasks in callable functions
- Tasks now execute correctly in background

**Status:** Fixed

---

### **2. Excessive API Calls / Slow Dashboard**

**Symptoms:**
- Dashboard is slow to load
- Multiple API calls for account balances
- Slow API warnings (5+ seconds)

**Cause:** 
- Frontend polling too frequently
- Individual balance API calls for each account
- No caching between requests

**Solution:** ✅ Fixed
- Reduced frontend polling intervals:
  - Accounts: 30s → 60s
  - Account info: 5s → 15s
  - Metrics: 10s → 30s
  - Positions: 5s → 10s
- Optimized `get_accounts()` to use balances from `list_accounts()`
- Added React Query `staleTime` for better caching

**Result:** ~70% reduction in API calls

---

### **3. CORS Errors in Browser**

**Error:**
```
Access to fetch at 'http://localhost:8080/api/accounts' from origin 'http://localhost:3000' 
has been blocked by CORS policy
```

**Solution:**
1. Verify `aiohttp-cors` is installed:
   ```bash
   pip install "aiohttp-cors>=0.7.0"
   ```

2. Check CORS is enabled in `async_webhook_server.py`:
   ```python
   cors = cors_setup(self.app, defaults={
       "*": ResourceOptions(...)
   })
   ```

3. Restart backend server

---

### **4. WebSocket Connection Failed**

**Error:**
```
WebSocket connection failed
```

**Solution:**
1. Check WebSocket server is running (port 8081)
2. Verify `frontend/.env` has:
   ```bash
   VITE_WS_URL=ws://localhost:8081
   ```
3. Check browser console for specific errors

**Note:** WebSocket is optional - dashboard works without it (uses polling)

---

### **5. API Endpoints Return 404**

**Error:**
```
GET /api/accounts 404 Not Found
```

**Solution:**
1. Verify routes are registered in `async_webhook_server.py`
2. Check server logs for route registration
3. Test endpoint directly:
   ```bash
   curl http://localhost:8080/api/accounts
   ```

---

### **6. Slow API Responses**

**Symptoms:**
- API calls taking 1-5 seconds
- Dashboard feels sluggish

**Causes & Solutions:**

1. **TopStepX API Latency** (External - Unavoidable)
   - Average: ~100-200ms
   - Sometimes: 1-5 seconds
   - **Solution:** Use caching (already implemented)

2. **Excessive Polling** (Fixed)
   - Frontend polling too frequently
   - **Solution:** Reduced polling intervals

3. **Individual Balance Calls** (Fixed)
   - Fetching balance for each account separately
   - **Solution:** Use balances from `list_accounts()`

4. **No Caching** (Fixed)
   - React Query now uses `staleTime`
   - Backend uses PostgreSQL cache

---

### **7. Background Tasks Not Running**

**Symptoms:**
- Task queue errors in logs
- Periodic tasks failing

**Solution:** ✅ Fixed
- Wrapped coroutines in callable functions
- Tasks now execute correctly

---

### **8. Frontend Not Updating**

**Symptoms:**
- Dashboard shows stale data
- No real-time updates

**Solutions:**
1. **Check polling intervals** - Data refreshes automatically
2. **Check WebSocket** - Optional, but provides real-time updates
3. **Check browser console** - Look for API errors
4. **Verify backend is running** - Check server logs

---

## Performance Optimization Tips

### **Backend:**
1. ✅ Use PostgreSQL cache (already implemented)
2. ✅ Reduce API calls (already optimized)
3. ✅ Use task queue for background operations

### **Frontend:**
1. ✅ Reduced polling frequency
2. ✅ Added React Query caching
3. ⏳ Consider WebSocket for real-time updates (optional)

---

## Debugging Steps

### **1. Check Backend Health**
```bash
curl http://localhost:8080/health
```

### **2. Test API Endpoints**
```bash
# Test accounts endpoint
curl http://localhost:8080/api/accounts

# Test positions endpoint
curl http://localhost:8080/api/positions

# Test metrics endpoint
curl http://localhost:8080/api/metrics
```

### **3. Check Server Logs**
```bash
# Look for errors
tail -f trading_bot.log | grep ERROR

# Check API call times
tail -f trading_bot.log | grep "SLOW API CALL"
```

### **4. Check Frontend Console**
- Open browser DevTools (F12)
- Check Console tab for errors
- Check Network tab for failed requests

---

## Quick Fixes

### **Restart Everything**
```bash
# Stop backend (Ctrl+C)
# Stop frontend (Ctrl+C)

# Restart backend
python servers/start_async_webhook.py

# Restart frontend (in new terminal)
cd frontend
npm run dev
```

### **Clear Browser Cache**
- Hard refresh: `Cmd+Shift+R` (Mac) or `Ctrl+Shift+R` (Windows)
- Or clear browser cache in DevTools

### **Check Environment Variables**
```bash
# Backend
cat .env | grep -E "API_KEY|USERNAME|DATABASE"

# Frontend
cat frontend/.env
```

---

## Still Having Issues?

1. **Check logs** - Both backend and frontend
2. **Verify versions** - Python, Node.js, dependencies
3. **Test endpoints** - Use curl to isolate issues
4. **Check network** - Firewall, proxy settings
5. **Review recent changes** - Git log for recent commits

---

**Most issues are resolved with the latest fixes! 🎉**

