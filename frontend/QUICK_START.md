# 🚀 Quick Start Guide

## ✅ Installation Complete!

- **Node.js**: v25.1.0 ✅
- **npm**: v11.6.2 ✅
- **Dependencies**: Installed ✅

---

## 🎯 Next Steps

### **1. Create Environment File**

```bash
# In frontend/ directory
echo "VITE_API_URL=http://localhost:8080" > .env
echo "VITE_WS_URL=http://localhost:8080" >> .env
```

### **2. Start Development Server**

```bash
npm run dev
```

Dashboard will be available at: **http://localhost:3000**

### **3. Make Sure Python Backend is Running**

The dashboard needs your Python backend running on port 8080:

```bash
# In project root
python trading_bot.py
# Or
python servers/start_async_webhook.py
```

---

## 📝 Available Commands

```bash
# Development
npm run dev          # Start dev server (port 3000)

# Build
npm run build        # Build for production
npm run preview      # Preview production build

# Code Quality
npm run lint         # Run ESLint
npm run type-check   # TypeScript type checking
```

---

## 🎨 What You'll See

When you run `npm run dev`, you'll get:

- **Dashboard UI** at http://localhost:3000
- **Hot reload** - changes update instantly
- **TypeScript** - type checking in real-time
- **Tailwind CSS** - modern dark theme

---

## 🔌 Backend Connection

The dashboard connects to:
- **REST API**: `http://localhost:8080/api/*`
- **WebSocket**: `ws://localhost:8080/ws`

Make sure your Python backend is running and accessible!

---

## 🐛 Troubleshooting

### **Port 3000 Already in Use**

Edit `vite.config.ts`:
```typescript
server: {
  port: 3001,  // Change port
}
```

### **Backend Connection Failed**

1. Check backend is running: `curl http://localhost:8080/health`
2. Check `.env` file has correct URLs
3. Check browser console for errors

### **TypeScript Errors**

```bash
npm run type-check
```

---

## ✅ Ready to Go!

Everything is set up. Just run:

```bash
npm run dev
```

And open http://localhost:3000 in your browser! 🎉

