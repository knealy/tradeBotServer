# 🚀 Deployment Guide - Free Cloud Hosting

This guide shows you how to deploy your TopStepX Trading Bot webhook server to free cloud services.

## 🎯 **Recommended: Railway (Easiest)**

### **Step 1: Prepare Your Code**
Your project is already set up with the necessary files:
- ✅ `Procfile` - Tells Railway how to run your app
- ✅ `requirements.txt` - Python dependencies
- ✅ `webhook_server.py` - Your webhook server

### **Step 2: Deploy to Railway**

1. **Go to [Railway.app](https://railway.app)**
2. **Sign up** with GitHub
3. **Click "New Project"**
4. **Select "Deploy from GitHub repo"**
5. **Choose your projectXbot repository**
6. **Railway will automatically deploy!**

### **Step 3: Get Your Webhook URL**

After deployment, Railway will give you a URL like:
```
https://your-app-name.railway.app
```

**Use this URL in your TradingView alerts!**

### **Step 4: Set Environment Variables**

In Railway dashboard:
1. Go to your project
2. Click "Variables" tab
3. Add these environment variables:
   ```
   PROJECT_X_API_KEY=your_api_key_here
   PROJECT_X_USERNAME=your_username_here
   ```

---

## 🌐 **Alternative: Render**

### **Step 1: Deploy to Render**

1. **Go to [Render.com](https://render.com)**
2. **Sign up** with GitHub
3. **Click "New +" → "Web Service"**
4. **Connect your GitHub repository**
5. **Configure:**
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `python3 webhook_server.py --host 0.0.0.0 --port $PORT`
6. **Click "Create Web Service"**

### **Step 2: Set Environment Variables**

In Render dashboard:
1. Go to your service
2. Click "Environment" tab
3. Add:
   ```
   PROJECT_X_API_KEY=your_api_key_here
   PROJECT_X_USERNAME=your_username_here
   ```

### **Step 3: Get Your Webhook URL**

Render will give you a URL like:
```
https://your-app-name.onrender.com
```

---

## 🔧 **Environment Variables**

Both services need these environment variables:

```bash
PROJECT_X_API_KEY=your_topstepx_api_key
PROJECT_X_USERNAME=your_topstepx_username
```

**⚠️ Important**: Never commit your API keys to GitHub! Use the cloud service's environment variable settings.

---

## 🧪 **Testing Your Deployment**

### **Test with curl:**
```bash
curl -X POST https://your-app-name.railway.app/ \
  -H "Content-Type: application/json" \
  -d '{"test": "webhook"}'
```

### **Test with your test script:**
```bash
python3 test_webhook.py --url https://your-app-name.railway.app/
```

---

## 🚨 **Security Notes**

1. **Environment Variables**: Always use the cloud service's environment variable settings
2. **HTTPS**: Both Railway and Render provide automatic HTTPS
3. **Monitoring**: Check the logs in your cloud service dashboard
4. **Backup**: Your code is in GitHub, so it's automatically backed up

---

## 💰 **Cost Comparison**

| Service | Free Tier | Best For |
|---------|-----------|----------|
| **Railway** | $5 credit/month | Python apps, easy setup |
| **Render** | 750 hours/month | Webhooks, APIs |
| **Heroku** | $7/month | Full-featured platform |
| **Fly.io** | 3 shared VMs | Global deployment |

---

## 🎉 **You're Ready!**

Once deployed, you'll have a permanent webhook URL like:
```
https://your-app-name.railway.app/
```

Use this URL in your TradingView alerts instead of the ngrok URL!

**No more port restrictions, no more changing URLs!** 🚀
