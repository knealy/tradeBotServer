# 🚀 Production Environment Variables

## **New Features**

### **Enhanced Order Management**
- **Order Tagging**: All bot-executed orders are tagged with "TradingBot-v1.0" for easy identification
- **Discord Notifications**: Real-time notifications for order executions and signal processing
- **Smart TP1 Handling**: Conditional TP1 signal processing based on bracket order type

### **Bracket Order Types**
- **USE_NATIVE_BRACKETS=false** (default): Uses separate limit/stop orders, ignores TP1 signals
- **USE_NATIVE_BRACKETS=true**: Uses TopStepX Auto OCO Brackets, processes TP1 signals

## **Required Environment Variables**

### **TopStepX API Credentials**
```bash
# Primary naming convention (recommended)
TOPSTEPX_API_KEY=your_api_key_here
TOPSTEPX_USERNAME=your_username_here
TOPSTEPX_ACCOUNT_ID=12694476

# Legacy naming convention (for backward compatibility)
PROJECT_X_API_KEY=your_api_key_here
PROJECT_X_USERNAME=your_username_here
PROJECT_X_ACCOUNT_ID=12694476
```

### **Trading Configuration**
```bash
# Position management
POSITION_SIZE=3
MAX_POSITION_SIZE=6

# Signal filtering
IGNORE_NON_ENTRY_SIGNALS=true
DEBOUNCE_SECONDS=300

# Bracket order configuration
USE_NATIVE_BRACKETS=false  # Set to true if using TopStepX Auto OCO Brackets
CLOSE_ENTIRE_POSITION_AT_TP1=false
TP1_FRACTION=0.75

# Risk management
ENABLE_BRACKET_ORDERS=true

# Discord notifications
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/your_webhook_url_here
STOP_LOSS_TICKS=10
TAKE_PROFIT_TICKS=20
```

### **Production Settings**
```bash
# Logging
LOG_LEVEL=INFO
ENABLE_FILE_LOGGING=true

# Health checks
HEALTH_CHECK_ENABLED=true
HEALTH_CHECK_INTERVAL=60

# API settings
API_TIMEOUT=30
MAX_RETRIES=3
```

### **Optional Settings**
```bash
# Market data
ENABLE_SIGNALR=true
MARKET_DATA_CACHE_SIZE=1000

# Webhook settings
WEBHOOK_TIMEOUT=10
MAX_WEBHOOK_SIZE=1024

# Development
DEBUG_MODE=false
ENABLE_TESTING=false
```

---

## **Railway.app Deployment**

### **Step 1: Set Environment Variables**
1. Go to Railway dashboard
2. Select your project
3. Go to Variables tab
4. Add all required variables

### **Step 2: Deploy**
```bash
git add .
git commit -m "Production ready trading bot"
git push origin main
```

### **Step 3: Monitor**
- Check `/health` endpoint for status
- Monitor logs for errors
- Verify authentication is working

---

## **Health Check Endpoints**

### **Basic Health Check**
```bash
curl https://your-app.railway.app/health
```

**Response:**
```json
{
  "status": "healthy",
  "authenticated": true,
  "selected_account": "PRAC-V2-14334-56363256",
  "timestamp": "2025-10-13T02:00:00Z",
  "uptime": "running"
}
```

### **Service Status**
```bash
curl https://your-app.railway.app/status
```

**Response:**
```json
{
  "service": "TopStepX Trading Bot",
  "version": "1.0.0",
  "status": "running",
  "timestamp": "2025-10-13T02:00:00Z"
}
```

---

## **Production Monitoring**

### **Log Files**
- `trading_bot.log` - Main application logs
- `webhook_server.log` - Webhook server logs

### **Key Metrics to Monitor**
- Authentication status
- Position sizes
- Signal processing
- API response times
- Error rates

### **Alert Conditions**
- Authentication failures
- Position size exceeded
- API timeouts
- Webhook processing errors

---

## **Security Considerations**

### **Environment Variables**
- Never commit credentials to git
- Use Railway's secure variable storage
- Rotate API keys regularly

### **Network Security**
- Use HTTPS for webhooks
- Validate webhook signatures (if available)
- Rate limit webhook endpoints

### **Access Control**
- Limit webhook access to TradingView IPs
- Monitor for unauthorized access
- Log all webhook requests

---

## **Troubleshooting**

### **Common Issues**

#### **Authentication Failures**
```bash
# Check credentials
echo $PROJECT_X_API_KEY
echo $PROJECT_X_USERNAME

# Test authentication
python3 -c "from trading_bot import TopStepXTradingBot; bot = TopStepXTradingBot(); print(bot.authenticate())"
```

#### **Position Size Issues**
```bash
# Check position limits
echo $MAX_POSITION_SIZE
echo $POSITION_SIZE

# Verify account selection
curl https://your-app.railway.app/health
```

#### **Webhook Processing Errors**
```bash
# Check logs
tail -f trading_bot.log
tail -f webhook_server.log

# Test webhook endpoint
curl -X POST https://your-app.railway.app \
  -H "Content-Type: application/json" \
  -d '{"test": "webhook"}'
```

---

## **Performance Optimization**

### **Resource Limits**
- Memory: 512MB (Railway default)
- CPU: 1 vCPU (Railway default)
- Storage: 1GB (Railway default)

### **Optimization Tips**
- Use connection pooling for API calls
- Cache market data when possible
- Implement request queuing for high volume
- Monitor memory usage

---

## **Backup and Recovery**

### **Configuration Backup**
```bash
# Export environment variables
railway variables --export > .env.backup

# Save configuration
cp railway.json railway.json.backup
```

### **Recovery Procedures**
1. Restore environment variables
2. Redeploy from git
3. Verify health checks
4. Test webhook functionality

---

## **Scaling Considerations**

### **Horizontal Scaling**
- Multiple webhook servers
- Load balancer configuration
- Shared state management

### **Vertical Scaling**
- Increase memory allocation
- Upgrade CPU resources
- Optimize database connections

---

## **Maintenance**

### **Regular Tasks**
- Monitor log files
- Check health endpoints
- Verify position management
- Update dependencies

### **Scheduled Maintenance**
- Weekly: Review logs and metrics
- Monthly: Update dependencies
- Quarterly: Security audit

---

## **Support and Monitoring**

### **Health Check URLs**
- `/health` - Authentication and account status
- `/status` - Service information
- `/` - Basic connectivity

### **Log Monitoring**
```bash
# Real-time log monitoring
railway logs --follow

# Filter for errors
railway logs --follow | grep ERROR
```

### **Performance Monitoring**
- Response times
- Error rates
- Memory usage
- CPU utilization

---

## **Production Checklist**

- [ ] All environment variables set
- [ ] Health checks responding
- [ ] Authentication working
- [ ] Position limits configured
- [ ] Signal filtering enabled
- [ ] Logging configured
- [ ] Monitoring active
- [ ] Backup procedures in place
- [ ] Security measures implemented
- [ ] Documentation updated

**Your production trading bot is now ready for deployment!** 🚀
