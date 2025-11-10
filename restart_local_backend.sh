#!/bin/bash
# Restart the local backend to pick up code changes

echo "🛑 Stopping local backend..."
pkill -f "python.*start_async_webhook.py" || echo "No backend process found"

sleep 2

echo "🚀 Starting local backend..."
cd "$(dirname "$0")"
source .venv/bin/activate
python servers/start_async_webhook.py &

echo "✅ Backend restarted!"
echo "💡 Wait 5 seconds for it to initialize, then refresh your dashboard"

