# 🚂 Railway CLI Setup Guide

**Last Updated**: November 9, 2025  
**Purpose**: Install and use Railway CLI for easier database management

---

## 🎯 Quick Answer

**You don't need Railway CLI!** You can get your `DATABASE_URL` from the Railway web dashboard.

**But if you want CLI access**, here's how to install it.

---

## 📋 Option 1: Use Railway Dashboard (No CLI Needed)

**Easiest method - no installation required:**

1. **Go to Railway Dashboard**: https://railway.app
2. **Open your project**
3. **Click on your PostgreSQL service** (or database service)
4. **Go to "Variables" tab**
5. **Find `DATABASE_URL`** and copy the value

**That's it!** Use this URL in your test script or `.env` file.

---

## 🛠️ Option 2: Install Railway CLI

### **macOS Installation**

#### **Method A: Homebrew (Recommended)**

```bash
# Install Railway CLI
brew install railway

# Verify installation
railway --version

# Login to Railway
railway login
```

**This will:**
- Open your browser for authentication
- Link your Railway account to CLI
- Allow you to run Railway commands locally

#### **Method B: npm (If you have Node.js)**

```bash
# Install Railway CLI via npm
npm i -g @railway/cli

# Verify installation
railway --version

# Login
railway login
```

---

### **Linux Installation**

#### **Method A: npm**

```bash
# Install Node.js first (if not installed)
# Ubuntu/Debian:
sudo apt update
sudo apt install nodejs npm

# Install Railway CLI
npm i -g @railway/cli

# Login
railway login
```

#### **Method B: Shell Script**

```bash
# Install via Railway's install script
bash <(curl -fsSL cli.new)

# Login
railway login
```

---

### **Windows Installation**

#### **Method A: npm**

```powershell
# Install Node.js first (if not installed)
# Download from: https://nodejs.org/

# Install Railway CLI
npm i -g @railway/cli

# Login
railway login
```

#### **Method B: Scoop**

```powershell
# Install Scoop first (if not installed)
# https://scoop.sh/

# Install Railway CLI
scoop install railway

# Login
railway login
```

---

## 🔧 Common Railway CLI Commands

### **Project Management**

```bash
# List all projects
railway projects

# Link to a project (in project directory)
railway link

# View project status
railway status
```

### **Environment Variables**

```bash
# List all variables
railway variables

# Get specific variable
railway variables | grep DATABASE_URL

# Set a variable
railway variables set MY_VAR=value

# Get DATABASE_URL (most common use case)
railway variables | grep DATABASE_URL | cut -d'=' -f2-
```

### **Database Access**

```bash
# Connect to PostgreSQL shell
railway connect postgres

# Or get connection string
railway variables | grep DATABASE_URL
```

### **Logs**

```bash
# View live logs
railway logs

# View logs for specific service
railway logs --service <service-name>

# Follow logs (like tail -f)
railway logs --follow
```

### **Deployment**

```bash
# Deploy current directory
railway up

# Deploy with specific environment
railway up --environment production

# View deployment status
railway status
```

---

## 🎯 Quick Use Cases

### **Get DATABASE_URL for Testing**

```bash
# Method 1: Using CLI
export DATABASE_URL="$(railway variables | grep DATABASE_URL | cut -d'=' -f2-)"
python test_performance.py

# Method 2: Manual (from dashboard)
# Copy from Railway Dashboard → PostgreSQL → Variables → DATABASE_URL
export DATABASE_URL="postgresql://postgres:password@host:port/db"
python test_performance.py
```

### **Check Bot Logs**

```bash
# View live logs
railway logs

# Filter for specific text
railway logs | grep "cache"
railway logs | grep "error"
```

### **Restart Service**

```bash
# Restart your bot service
railway restart

# Or via dashboard: Project → Service → Restart
```

---

## 🔐 Authentication

**First time setup:**

```bash
railway login
```

**This will:**
1. Open your browser
2. Ask you to authorize Railway CLI
3. Link your account

**If you need to switch accounts:**

```bash
railway logout
railway login
```

---

## ❓ Troubleshooting

### **"command not found: railway"**

**Solution**: Install Railway CLI (see installation methods above)

**Or**: Use Railway Dashboard instead (no CLI needed)

### **"Not authenticated"**

**Solution**: Run `railway login`

### **"Project not linked"**

**Solution**: 
```bash
# In your project directory
railway link

# Select your project from the list
```

### **"Permission denied"**

**Solution**: Make sure you're logged in and have access to the project

```bash
railway login
railway projects  # Verify you can see your project
```

---

## 💡 Pro Tips

1. **Use Dashboard for One-Time Tasks**
   - Getting DATABASE_URL (copy/paste is easier)
   - Viewing logs (better UI)
   - Managing services

2. **Use CLI for Automation**
   - Scripts that need DATABASE_URL
   - CI/CD pipelines
   - Automated deployments

3. **Best of Both Worlds**
   - Get DATABASE_URL from dashboard once
   - Save it in `.env` file
   - Use CLI for logs and deployments

---

## 📚 Additional Resources

- **Railway CLI Docs**: https://docs.railway.com/guides/cli
- **Railway Dashboard**: https://railway.app
- **Railway Support**: https://railway.app/support

---

## ✅ Quick Checklist

- [ ] Decide: CLI or Dashboard? (Dashboard is easier for most tasks)
- [ ] If CLI: Install via `brew install railway` (macOS) or `npm i -g @railway/cli`
- [ ] Authenticate: `railway login`
- [ ] Get DATABASE_URL: Dashboard → PostgreSQL → Variables → DATABASE_URL
- [ ] Test connection: `export DATABASE_URL="..." && python test_performance.py`

---

**Remember: You don't need Railway CLI for most tasks! The dashboard works great.** 🎯

