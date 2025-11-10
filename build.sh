#!/bin/bash
# Build script for Railway deployment
# This builds the frontend and prepares it for production

set -e

echo "========================================="
echo "🏗️  Building Frontend for Production"
echo "========================================="

# Navigate to frontend directory
cd frontend

# Install dependencies if node_modules doesn't exist
if [ ! -d "node_modules" ]; then
    echo "📦 Installing frontend dependencies..."
    npm ci --prefer-offline --no-audit
else
    echo "✅ Dependencies already installed"
fi

# Build frontend
echo "🔨 Building React frontend..."
npm run build

# Verify build output
cd ..
if [ -d "static/dashboard" ]; then
    echo "✅ Frontend built successfully!"
    echo "📂 Output: static/dashboard/"
    ls -lh static/dashboard/
else
    echo "❌ Frontend build failed - output directory not found"
    exit 1
fi

echo "========================================="
echo "✅ Build complete!"
echo "========================================="

