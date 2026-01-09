#!/bin/bash
# Rebuild Rust module with the bracket order fix

set -e  # Exit on error

cd /Users/knealy/tradeBotServer

echo "🔧 Rebuilding Rust module with bracket order fix..."
echo ""

# Step 1: Stop running strategies
echo "1️⃣  Stopping running strategies..."
pkill -f strategy_executor && echo "   ✅ Strategies stopped" || echo "   ℹ️  No strategies running"
sleep 2
echo ""

# Step 2: Install maturin if needed
echo "2️⃣  Checking for maturin..."
if python3 -c "import maturin" 2>/dev/null; then
    echo "   ✅ maturin already installed"
else
    echo "   📦 Installing maturin..."
    pip3 install maturin
    echo "   ✅ maturin installed"
fi
echo ""

# Step 3: Rebuild the Rust module
echo "3️⃣  Building Rust module (this will take 2-3 minutes)..."
echo "   ⏳ Compiling..."
python3 -m maturin develop --release

if [ $? -eq 0 ]; then
    echo "   ✅ Rust module rebuilt successfully!"
else
    echo "   ❌ Build failed - check errors above"
    exit 1
fi
echo ""

# Step 4: Verify the fix is in place
echo "4️⃣  Verifying fix in Rust source..."
if grep -q "reduceOnly removed - brackets auto-attach" rust/src/order_execution/mod.rs; then
    echo "   ✅ Fix confirmed in Rust source code"
else
    echo "   ⚠️  Warning: Fix comment not found in source"
fi
echo ""

echo "✅ Rust module rebuild complete!"
echo ""
echo "🚀 Now restart your strategies:"
echo ""
echo "Terminal 1 (overnight_range):"
echo "  caffeinate -dimsu python core/strategy_executor.py --account_select=1 --strategy=overnight_range --symbols=mnq,mes,mgc"
echo ""
echo "Terminal 2 (mean_reversion):"
echo "  caffeinate -dimsu python core/strategy_executor.py --account_select=1 --strategy=mean_reversion --symbols=mnq,mes,mgc"
echo ""
echo "Terminal 3 (trend_following):"
echo "  caffeinate -dimsu python core/strategy_executor.py --account_select=1 --strategy=trend_following --symbols=mnq,mes,mgc"
echo ""
echo "📋 Watch for successful orders (NO MORE 500 errors!):"
echo "  tail -f trading_bot.log | grep -E 'placed|500|bracket'"
