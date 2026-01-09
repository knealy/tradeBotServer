#!/bin/bash
# Temporary fix: Disable Rust hot path to use Python fallback (which has the fix)

echo "🔄 Disabling Rust hot path temporarily..."
echo ""

# Backup .env
cp .env .env.backup.$(date +%Y%m%d_%H%M%S)

# Disable Rust hot path
sed -i.bak 's/^TOPSTEPX_USE_RUST=true/TOPSTEPX_USE_RUST=false/' .env

echo "✅ Rust hot path disabled"
echo "   The Python fallback (which has the fix) will be used instead"
echo ""
echo "📝 Original .env backed up"
echo ""
echo "🔄 Now restart your strategy executors for the change to take effect:"
echo "   pkill -f strategy_executor"
echo "   Then restart each strategy"
echo ""
