#!/bin/bash
# Quick deployment script for Railway
# Run this before deploying to ensure everything is ready

set -e

echo "========================================"
echo "🚂 Railway Deployment Preparation"
echo "========================================"
echo

# Check if git is clean
if [[ -n $(git status -s) ]]; then
    echo "📝 You have uncommitted changes:"
    git status -s
    echo
    read -p "Commit changes now? (y/n) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        echo "💬 Enter commit message:"
        read commit_msg
        git add .
        git commit -m "$commit_msg"
        echo "✅ Changes committed"
    fi
fi

# Build frontend locally to test
echo
echo "🏗️  Building frontend locally..."
bash build.sh

# Check if Railway CLI is installed
if ! command -v railway &> /dev/null; then
    echo
    echo "⚠️  Railway CLI not found!"
    echo "Install it with: npm install -g @railway/cli"
    echo "Or deploy via GitHub instead"
    exit 1
fi

# Check if logged in
echo
echo "🔐 Checking Railway authentication..."
if railway whoami &> /dev/null; then
    echo "✅ Logged in to Railway as: $(railway whoami)"
else
    echo "⚠️  Not logged in to Railway"
    echo "Run: railway login"
    exit 1
fi

# Prompt for deployment
echo
echo "========================================"
echo "Ready to deploy!"
echo "========================================"
echo
echo "Your app will be available at:"
echo "  - Dashboard: https://[your-app].up.railway.app/dashboard"
echo "  - API: https://[your-app].up.railway.app/api/"
echo "  - Webhook: https://[your-app].up.railway.app/webhook"
echo
read -p "Deploy now? (y/n) " -n 1 -r
echo

if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo
    echo "🚀 Deploying to Railway..."
    railway up
    
    echo
    echo "✅ Deployment initiated!"
    echo
    echo "📊 View logs:"
    echo "   railway logs --tail"
    echo
    echo "🌐 Generate domain (if not done):"
    echo "   railway domain"
    echo
    echo "🔧 Set environment variables (if needed):"
    echo "   railway variables set PROJECT_X_API_KEY=your_key"
    echo "   railway variables set PROJECT_X_USERNAME=your_username"
    echo
else
    echo
    echo "Deployment cancelled"
    echo
    echo "Deploy manually with:"
    echo "  railway up"
fi

