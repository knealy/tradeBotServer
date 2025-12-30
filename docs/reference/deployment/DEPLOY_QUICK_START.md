# 🚀 Quick Start - Deploy to Railway

## ⚡ Fastest Way (3 Steps)

```bash
# 1. Run deployment script
./deploy_to_railway.sh

# 2. Set your credentials (if first time)
railway variables set PROJECT_X_API_KEY="your_key_here"
railway variables set PROJECT_X_USERNAME="your_username_here"

# 3. Get your URL
railway domain
```

**Done!** Your dashboard is live at: `https://[your-app].up.railway.app/dashboard`

---

## 📋 What Happens During Deployment

1. ✅ Frontend builds automatically (React → static files)
2. ✅ Backend starts on Railway's assigned port
3. ✅ PostgreSQL database connects automatically
4. ✅ Health checks ensure everything's running
5. ✅ Domain generates (or uses existing)

---

## 🔑 Required Environment Variables

In Railway dashboard or via CLI:

```bash
railway variables set PROJECT_X_API_KEY="your_topstepx_api_key"
railway variables set PROJECT_X_USERNAME="your_topstepx_username"
```

Optional:
```bash
railway variables set TOPSTEPX_ACCOUNT_ID="your_account_id"
```

---

## 🌐 Your Live URLs

After deployment:

- **Dashboard**: `https://tvwebhooks.up.railway.app/dashboard`
- **Positions**: `https://tvwebhooks.up.railway.app/positions`  
- **Strategies**: `https://tvwebhooks.up.railway.app/strategies`
- **Webhook**: `https://tvwebhooks.up.railway.app/webhook` ← Use in TradingView

---

## 📊 Check Status

```bash
# View logs
railway logs --tail

# Check health
curl https://tvwebhooks.up.railway.app/health

# Open Railway dashboard
railway open
```

---

## 🔄 Update & Redeploy

```bash
# Make changes, then:
git add .
git commit -m "Your changes"
railway up
```

Or if connected to GitHub:
```bash
git push origin main  # Auto-deploys!
```

---

## 📞 Need Help?

- **Full Guide**: `RAILWAY_DEPLOYMENT.md`
- **Setup Details**: `RAILWAY_SETUP_COMPLETE.md`
- **Bug Fixes**: `FIXES_APPLIED.md`
- **Railway Docs**: https://docs.railway.app

---

## ✅ Checklist

- [ ] Railway CLI installed: `npm install -g @railway/cli`
- [ ] Logged in: `railway login`
- [ ] Project linked: `railway link`
- [ ] PostgreSQL added: `railway add --database postgres`
- [ ] Environment variables set
- [ ] Deployed: `railway up`
- [ ] Domain generated: `railway domain`
- [ ] TradingView webhook updated
- [ ] Dashboard accessible
- [ ] Test trade executed

**All done? You're live! 🎉**

