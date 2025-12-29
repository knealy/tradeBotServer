#!/bin/bash
# ONE-LINE FIX: Disable Rust hot path and restart

cd /Users/knealy/tradeBotServer
echo "🔧 Applying quick fix..."
pkill -f strategy_executor && echo "✅ Stopped old processes"
sed -i.bak 's/TOPSTEPX_USE_RUST=true/TOPSTEPX_USE_RUST=false/' .env && echo "✅ Disabled Rust hot path"
grep TOPSTEPX_USE_RUST .env
echo ""
echo "✅ Fix applied! Rust hot path disabled."
echo "   Python fallback (which has the fix) will be used."
echo ""
echo "🚀 Now restart your strategies:"
echo "   caffeinate -dimsu python core/strategy_executor.py --account_select=1 --strategy=mean_reversion --symbols=mnq,mes,mgc"
