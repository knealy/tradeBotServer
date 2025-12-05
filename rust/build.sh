#!/bin/bash
set -e

echo "🔧 Building Rust trading bot module..."
echo ""

# Find Python
PYTHON_PATH=$(which python3)
echo "✓ Found Python: $PYTHON_PATH"
PYTHON_VERSION=$($PYTHON_PATH --version)
echo "✓ Python version: $PYTHON_VERSION"

# Set PyO3 environment variables
export PYO3_PYTHON=$PYTHON_PATH

echo ""
echo "🦀 Building release version..."
cargo build --release

if [ $? -eq 0 ]; then
    echo ""
    echo "✅ Build successful!"
    echo ""
    echo "📦 Output:"
    ls -lh target/release/libtrading_bot_rust.dylib
    echo ""
    echo "📋 Next steps:"
    echo "  1. Copy to Python path:"
    echo "     cp target/release/libtrading_bot_rust.dylib ../trading_bot_rust.so"
    echo ""
    echo "  2. Test import:"
    echo "     python3 -c 'import trading_bot_rust; print(\"✅ Module loaded\")'"
else
    echo ""
    echo "❌ Build failed"
    exit 1
fi

