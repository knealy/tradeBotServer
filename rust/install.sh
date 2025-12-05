#!/bin/bash
set -e

echo "🔧 Installing Rust Trading Bot Module"
echo ""

# Check Python 3
if ! command -v python3 &> /dev/null; then
    echo "❌ Error: python3 not found"
    exit 1
fi

PYTHON_VERSION=$(python3 --version)
echo "✓ Found: $PYTHON_VERSION"

# Check if venv exists and activate it
if [ -d "../venv" ]; then
    echo "✓ Activating virtual environment..."
    source ../venv/bin/activate
    echo "✓ Virtual environment activated"
else
    echo "⚠️  No virtual environment found (continuing with system Python 3)"
fi

# Check pip3
if ! command -v pip3 &> /dev/null; then
    echo "❌ Error: pip3 not found. Installing pip..."
    python3 -m ensurepip --upgrade
fi

echo ""
echo "📦 Installing maturin..."
pip3 install maturin

echo ""
echo "🦀 Building Rust module (this may take a few minutes)..."
maturin develop --release

if [ $? -eq 0 ]; then
    echo ""
    echo "✅ Build successful!"
    echo ""
    echo "🧪 Testing module..."
    python3 -c "import trading_bot_rust; print('✅ Module loaded successfully!')"
    
    if [ $? -eq 0 ]; then
        echo ""
        echo "🎉 Installation complete!"
        echo ""
        echo "📋 Next steps:"
        echo "  1. Test the module:"
        echo "     python3 -c \"import trading_bot_rust; e = trading_bot_rust.OrderExecutor('https://api.topstepx.com'); print(e.get_base_url())\""
        echo ""
        echo "  2. Integrate with trading bot (see RUST_PHASE1_QUICKSTART.md)"
    else
        echo ""
        echo "⚠️  Build succeeded but import test failed"
        echo "   Try: python3 -c 'import trading_bot_rust'"
    fi
else
    echo ""
    echo "❌ Build failed"
    echo ""
    echo "📋 Troubleshooting:"
    echo "  - Make sure you're using Python 3: python3 --version"
    echo "  - Check Rust is installed: cargo --version"
    echo "  - See rust/INSTALL.md for detailed help"
    exit 1
fi

