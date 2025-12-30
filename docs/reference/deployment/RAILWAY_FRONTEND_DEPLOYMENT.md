# Railway Frontend Deployment Guide

## Overview

This guide explains how to deploy the React + TypeScript frontend dashboard to Railway alongside the existing Python backend.

## Current Architecture

### Local Development
- **Frontend**: React + Vite dev server on `http://localhost:3000`
- **Backend API**: Python async webhook server on `http://localhost:8080`
- **WebSocket**: Python WebSocket server on `ws://localhost:8081`
- **Database**: PostgreSQL on Railway (accessed via `PUBLIC_DATABASE_URL`)

### Railway Production (Current)
- **Backend API**: Python async webhook server
- **WebSocket**: Python WebSocket server
- **Database**: PostgreSQL service

### Railway Production (Target)
- **Frontend**: Static React build served by backend or separate service
- **Backend API**: Python async webhook server
- **WebSocket**: Python WebSocket server
- **Database**: PostgreSQL service

## Deployment Options

### Option A: Serve Frontend from Python Backend (Recommended)

**Pros:**
- Single service deployment
- No CORS issues
- Simpler configuration
- Lower cost (one service instead of two)

**Cons:**
- Backend restarts affect frontend
- Mixed concerns (API + static files)

**Implementation:**

1. **Build the frontend for production:**
   ```bash
   cd /Users/susan/projectXbot/frontend
   npm run build
   ```

   This creates a production build in `frontend/dist/` (or `static/dashboard/` if configured).

2. **Update `async_webhook_server.py` to serve static files:**
   ```python
   from aiohttp import web
   import os
   
   # In _setup_routes():
   # Serve React app
   static_dir = os.path.join(os.path.dirname(__file__), '..', 'frontend', 'dist')
   if os.path.exists(static_dir):
       self.app.router.add_static('/dashboard', static_dir)
       # Serve index.html for all non-API routes (SPA routing)
       async def serve_react_app(request):
           return web.FileResponse(os.path.join(static_dir, 'index.html'))
       self.app.router.add_get('/', serve_react_app)
   ```

3. **Update `Procfile` or Railway start command:**
   ```
   web: python servers/start_async_webhook.py
   ```

4. **Update frontend environment variables:**
   Create `frontend/.env.production`:
   ```env
   VITE_API_URL=/api
   VITE_WS_URL=wss://your-app.railway.app/ws
   ```

5. **Update `vite.config.ts`:**
   ```typescript
   export default defineConfig({
     // ...
     build: {
       outDir: 'dist',
       emptyOutDir: true,
     },
   })
   ```

6. **Add build step to Railway:**
   In Railway dashboard:
   - Go to your service → Settings → Build
   - Add build command: `cd frontend && npm install && npm run build`

### Option B: Separate Frontend Service

**Pros:**
- Independent scaling
- Frontend updates don't restart backend
- Cleaner separation of concerns

**Cons:**
- Two services to manage
- CORS configuration required
- Higher cost

**Implementation:**

1. **Create a new Railway service:**
   - Click "New" → "Empty Service"
   - Name it "trading-dashboard-frontend"

2. **Connect to your GitHub repo:**
   - Settings → Connect to GitHub
   - Select your repository

3. **Configure build settings:**
   - Root Directory: `frontend`
   - Build Command: `npm install && npm run build`
   - Start Command: `npx serve -s dist -l $PORT`

4. **Add environment variables:**
   ```env
   VITE_API_URL=https://your-backend.railway.app/api
   VITE_WS_URL=wss://your-backend.railway.app/ws
   ```

5. **Update backend CORS settings:**
   In `async_webhook_server.py`:
   ```python
   cors = cors_setup(self.app, defaults={
       "https://your-frontend.railway.app": ResourceOptions(
           allow_credentials=True,
           expose_headers="*",
           allow_headers="*",
           allow_methods="*"
       )
   })
   ```

6. **Deploy:**
   - Push to GitHub
   - Railway will automatically build and deploy

## Environment Variables

### Frontend Variables

Create `frontend/.env.production`:
```env
# API Endpoint
VITE_API_URL=https://your-app.railway.app/api

# WebSocket Endpoint
VITE_WS_URL=wss://your-app.railway.app/ws

# Optional: Enable production mode
NODE_ENV=production
```

### Backend Variables (Already Configured)

Ensure these are set in Railway dashboard:
```env
PROJECT_X_API_KEY=your_api_key
PROJECT_X_USERNAME=your_username
DATABASE_URL=postgresql://... (internal)
PUBLIC_DATABASE_URL=postgresql://... (external)
DISCORD_WEBHOOK_URL=your_webhook_url
```

## Build Process

### Local Build Test

Before deploying to Railway, test the production build locally:

```bash
# 1. Build the frontend
cd /Users/susan/projectXbot/frontend
npm run build

# 2. Preview the build
npm run preview

# 3. Test with backend
cd /Users/susan/projectXbot
source .venv/bin/activate
python servers/start_async_webhook.py

# 4. Open http://localhost:8080 (if serving from backend)
```

### Railway Build

Railway will automatically:
1. Clone your repository
2. Install dependencies (`npm install`)
3. Run build command (`npm run build`)
4. Start the service

## Frontend Code Updates for Production

### 1. Update API Client (`frontend/src/services/api.ts`)

```typescript
const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8080/api'

const apiClient = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  },
})
```

### 2. Update WebSocket Client (`frontend/src/services/websocket.ts`)

```typescript
const WS_URL = import.meta.env.VITE_WS_URL || 'ws://localhost:8081'

class WebSocketService {
  private socket: WebSocket | null = null
  private url: string = WS_URL
  // ...
}
```

### 3. Update Router Base (`frontend/src/App.tsx`)

```typescript
<BrowserRouter basename={import.meta.env.BASE_URL}>
  {/* ... */}
</BrowserRouter>
```

## Deployment Steps (Option A - Recommended)

### Step 1: Prepare Frontend for Production

```bash
cd /Users/susan/projectXbot/frontend

# Install dependencies
npm install

# Build for production
npm run build

# Verify build output
ls -la dist/
```

### Step 2: Update Backend to Serve Frontend

Add static file serving to `servers/async_webhook_server.py`:

```python
def _setup_routes(self):
    """Setup all API routes and static file serving."""
    # ... existing API routes ...
    
    # Serve React frontend
    static_dir = os.path.join(os.path.dirname(__file__), '..', 'frontend', 'dist')
    if os.path.exists(static_dir):
        logger.info(f"📁 Serving React frontend from {static_dir}")
        self.app.router.add_static('/assets', os.path.join(static_dir, 'assets'))
        
        async def serve_index(request):
            return web.FileResponse(os.path.join(static_dir, 'index.html'))
        
        # Serve index.html for all non-API routes (SPA routing)
        self.app.router.add_get('/', serve_index)
        self.app.router.add_get('/{path:.*}', serve_index)
    else:
        logger.warning(f"⚠️ Frontend build not found at {static_dir}")
```

### Step 3: Update Railway Configuration

Create or update `railway.json` in project root:

```json
{
  "$schema": "https://railway.app/railway.schema.json",
  "build": {
    "builder": "NIXPACKS",
    "buildCommand": "cd frontend && npm install && npm run build && cd .. && pip install -r requirements.txt"
  },
  "deploy": {
    "startCommand": "python servers/start_async_webhook.py",
    "restartPolicyType": "ON_FAILURE",
    "restartPolicyMaxRetries": 10
  }
}
```

### Step 4: Commit and Push

```bash
cd /Users/susan/projectXbot

# Add all changes
git add -A

# Commit
git commit -m "Add production frontend build and Railway deployment config"

# Push to trigger Railway deployment
git push
```

### Step 5: Verify Deployment

1. **Check Railway logs:**
   - Go to Railway dashboard
   - Select your service
   - View deployment logs
   - Look for: `📁 Serving React frontend from ...`

2. **Test the frontend:**
   - Open `https://your-app.railway.app`
   - Verify dashboard loads
   - Check browser console for errors
   - Test account switching
   - Verify WebSocket connection

3. **Test API endpoints:**
   - `https://your-app.railway.app/api/accounts`
   - `https://your-app.railway.app/api/strategies`
   - `https://your-app.railway.app/health`

## Troubleshooting

### Frontend Not Loading

**Issue**: 404 errors or blank page

**Solutions**:
1. Check if `frontend/dist` exists in Railway build
2. Verify static file serving is configured correctly
3. Check Railway logs for build errors
4. Ensure `npm run build` completes successfully

### API Calls Failing

**Issue**: CORS errors or 404 on API calls

**Solutions**:
1. Verify `VITE_API_URL` is set correctly
2. Check CORS configuration in backend
3. Ensure API routes are registered before static file serving
4. Test API endpoints directly: `curl https://your-app.railway.app/api/health`

### WebSocket Not Connecting

**Issue**: WebSocket connection fails or disconnects immediately

**Solutions**:
1. Verify `VITE_WS_URL` uses `wss://` (not `ws://`)
2. Check if WebSocket server is running in Railway logs
3. Ensure Railway supports WebSocket connections (it does)
4. Test WebSocket manually: `wscat -c wss://your-app.railway.app/ws`

### Build Fails on Railway

**Issue**: `npm install` or `npm run build` fails

**Solutions**:
1. Check `package.json` for correct dependencies
2. Verify Node.js version compatibility
3. Test build locally first
4. Check Railway build logs for specific errors
5. Ensure `frontend/package-lock.json` is committed

## Performance Optimization

### 1. Enable Gzip Compression

In `async_webhook_server.py`:
```python
from aiohttp import web
import aiohttp_compress

self.app = web.Application(middlewares=[aiohttp_compress.compress_middleware])
```

### 2. Set Cache Headers

```python
async def serve_index(request):
    response = web.FileResponse(os.path.join(static_dir, 'index.html'))
    response.headers['Cache-Control'] = 'no-cache'
    return response

# For static assets
self.app.router.add_static(
    '/assets',
    os.path.join(static_dir, 'assets'),
    append_version=True  # Add version hash to URLs
)
```

### 3. Optimize Frontend Build

In `vite.config.ts`:
```typescript
export default defineConfig({
  build: {
    minify: 'terser',
    terserOptions: {
      compress: {
        drop_console: true,  // Remove console.log in production
      },
    },
    rollupOptions: {
      output: {
        manualChunks: {
          vendor: ['react', 'react-dom', 'react-router-dom'],
          charts: ['recharts', 'lightweight-charts'],
        },
      },
    },
  },
})
```

## Monitoring

### Frontend Metrics

Add analytics to track:
- Page load time
- API response times
- WebSocket connection stability
- Error rates

### Backend Metrics

Already tracked via `performance_metrics.py`:
- API call latency
- Cache hit rates
- Database query times
- System resource usage

## Rollback Plan

If deployment fails:

1. **Revert to previous deployment:**
   ```bash
   git revert HEAD
   git push
   ```

2. **Or rollback in Railway dashboard:**
   - Go to Deployments
   - Find last working deployment
   - Click "Redeploy"

## Next Steps

1. ✅ Remove old static dashboard files
2. ✅ Implement Strategies and Settings pages
3. ⏳ Test production build locally
4. ⏳ Deploy to Railway
5. ⏳ Verify all functionality works in production
6. ⏳ Monitor logs and metrics
7. ⏳ Update documentation with production URLs

## Production URLs (After Deployment)

- **Dashboard**: `https://your-app.railway.app/`
- **API**: `https://your-app.railway.app/api/`
- **WebSocket**: `wss://your-app.railway.app/ws`
- **Health Check**: `https://your-app.railway.app/health`

## Support

If you encounter issues:
1. Check Railway logs
2. Review browser console
3. Test API endpoints directly
4. Verify environment variables
5. Check CORS configuration
6. Review WebSocket connection logs

