#!/bin/bash

# TopStepX Trading Bot - Environment Setup Script
# This script helps you set up the environment variables for the trading bot

echo "🤖 TopStepX Trading Bot - Environment Setup"
echo "============================================="
echo

# Check if Python is installed
if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 is not installed. Please install Python 3.8 or higher."
    exit 1
fi

echo "✅ Python 3 found: $(python3 --version)"
echo

# Check if pip is installed
if ! command -v pip3 &> /dev/null; then
    echo "❌ pip3 is not installed. Please install pip3."
    exit 1
fi

echo "✅ pip3 found"
echo

# Install requirements
echo "📦 Installing Python dependencies..."

# Try different pip commands based on Python version
PYTHON_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "Detected Python version: $PYTHON_VERSION"

# Check if we're dealing with a pre-release version
if [[ "$PYTHON_VERSION" == *"a"* ]] || [[ "$PYTHON_VERSION" == *"b"* ]] || [[ "$PYTHON_VERSION" == *"rc"* ]]; then
    echo "⚠️  Detected pre-release Python version. Trying alternative installation methods..."
    
    # Try using python -m pip instead
    echo "Trying: python3 -m pip install -r requirements.txt"
    python3 -m pip install -r requirements.txt
    
    if [ $? -eq 0 ]; then
        echo "✅ Dependencies installed successfully using python3 -m pip"
    else
        echo "❌ python3 -m pip failed. Trying pip install..."
        pip install -r requirements.txt
        
        if [ $? -eq 0 ]; then
            echo "✅ Dependencies installed successfully using pip"
        else
            echo "❌ All pip methods failed. Please try manual installation:"
            echo "   python3 -m pip install --upgrade pip"
            echo "   python3 -m pip install requests aiohttp python-dateutil"
            echo ""
            echo "Or consider using a stable Python version (3.8-3.11) instead of the alpha version."
            exit 1
        fi
    fi
else
    # Normal installation for stable Python versions
    pip3 install -r requirements.txt
    
    if [ $? -eq 0 ]; then
        echo "✅ Dependencies installed successfully"
    else
        echo "❌ Failed to install dependencies"
        echo "Trying alternative method: python3 -m pip install -r requirements.txt"
        python3 -m pip install -r requirements.txt
        
        if [ $? -eq 0 ]; then
            echo "✅ Dependencies installed successfully using python3 -m pip"
        else
            echo "❌ All installation methods failed"
            exit 1
        fi
    fi
fi

echo

# Set up environment variables
echo "🔐 Setting up environment variables..."
echo "Please enter your TopStepX ProjectX API credentials:"
echo

read -p "Enter your ProjectX API Key: " api_key
read -p "Enter your ProjectX Username: " username

if [ -z "$api_key" ] || [ -z "$username" ]; then
    echo "❌ API Key and Username are required"
    exit 1
fi

# Create .env file
cat > .env << EOF
# TopStepX Trading Bot Environment Variables
# Generated on $(date)

PROJECT_X_API_KEY=$api_key
PROJECT_X_USERNAME=$username

# Optional: Set log level (DEBUG, INFO, WARNING, ERROR)
LOG_LEVEL=INFO
EOF

echo "✅ Environment variables saved to .env file"
echo

# Source the environment variables
source .env

echo "🔧 Environment setup complete!"
echo
echo "To run the trading bot:"
echo "  python3 trading_bot.py"
echo
echo "To activate environment variables in future sessions:"
echo "  source .env"
echo
echo "⚠️  Keep your .env file secure and never commit it to version control!"
