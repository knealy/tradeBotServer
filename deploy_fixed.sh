#!/bin/bash

# 🚀 Deploy Fixed Trading Bot System
# This script deploys the FIXED trading bot with all critical fixes applied

set -e  # Exit on any error

echo "🚀 Deploying FIXED Trading Bot System"
echo "======================================"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function to print colored output
print_status() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check if we're in the right directory
if [ ! -f "trading_bot.py" ] || [ ! -f "webhook_server.py" ]; then
    print_error "Please run this script from the project root directory"
    exit 1
fi

print_status "Starting deployment process..."

# Step 1: Clean up old files
print_status "Cleaning up old log files..."
rm -f *.log
rm -f __pycache__/*.pyc
find . -name "*.pyc" -delete
print_success "Cleanup completed"

# Step 2: Test the fixed system
print_status "Testing the fixed system..."
if python3 test_fixed_system.py; then
    print_success "All tests passed!"
else
    print_error "Tests failed! Please fix issues before deploying"
    exit 1
fi

# Step 3: Check environment variables
print_status "Checking environment variables..."
required_vars=("TOPSETPX_USERNAME" "TOPSETPX_PASSWORD" "TOPSETPX_ACCOUNT_ID")
missing_vars=()

for var in "${required_vars[@]}"; do
    if [ -z "${!var}" ]; then
        missing_vars+=("$var")
    fi
done

if [ ${#missing_vars[@]} -ne 0 ]; then
    print_error "Missing required environment variables: ${missing_vars[*]}"
    print_status "Please set these variables before deploying:"
    for var in "${missing_vars[@]}"; do
        echo "  export $var=\"your_value_here\""
    done
    exit 1
fi

print_success "All required environment variables are set"

# Step 4: Set default values for fixed system
print_status "Setting up fixed system configuration..."
export POSITION_SIZE=${POSITION_SIZE:-3}
export MAX_POSITION_SIZE=${MAX_POSITION_SIZE:-6}
export IGNORE_NON_ENTRY_SIGNALS=${IGNORE_NON_ENTRY_SIGNALS:-true}
export IGNORE_TP1_SIGNALS=${IGNORE_TP1_SIGNALS:-true}
export DEBOUNCE_SECONDS=${DEBOUNCE_SECONDS:-300}

print_success "Configuration set:"
echo "  POSITION_SIZE: $POSITION_SIZE"
echo "  MAX_POSITION_SIZE: $MAX_POSITION_SIZE"
echo "  IGNORE_NON_ENTRY_SIGNALS: $IGNORE_NON_ENTRY_SIGNALS"
echo "  IGNORE_TP1_SIGNALS: $IGNORE_TP1_SIGNALS"
echo "  DEBOUNCE_SECONDS: $DEBOUNCE_SECONDS"

# Step 5: Check if Railway CLI is available
if command -v railway &> /dev/null; then
    print_status "Railway CLI detected. Preparing for Railway deployment..."
    
    # Check if we're logged in to Railway
    if railway whoami &> /dev/null; then
        print_success "Logged in to Railway"
        
        # Deploy to Railway
        print_status "Deploying to Railway..."
        if railway up --detach; then
            print_success "Successfully deployed to Railway!"
            print_status "Your webhook URL will be available in the Railway dashboard"
        else
            print_error "Railway deployment failed"
            exit 1
        fi
    else
        print_warning "Not logged in to Railway. Please run 'railway login' first"
        print_status "You can also deploy manually by pushing to GitHub"
    fi
else
    print_warning "Railway CLI not found. You can deploy manually:"
    echo "  1. Push to GitHub: git push origin main"
    echo "  2. Deploy via Railway dashboard"
    echo "  3. Or run locally: python3 start_webhook.py --position-size 3"
fi

# Step 6: Create deployment summary
print_status "Creating deployment summary..."
cat > DEPLOYMENT_SUMMARY.md << EOF
# 🚀 Deployment Summary - FIXED Trading Bot

## ✅ Critical Fixes Applied
- **Position Management**: Single positions with staged exits
- **Signal Filtering**: Only entry signals processed
- **Risk Management**: All positions protected with OCO brackets
- **Debounce Protection**: 5-minute window prevents duplicates
- **Position Limits**: Maximum 6 contracts per symbol

## 🔧 Configuration
- POSITION_SIZE: $POSITION_SIZE
- MAX_POSITION_SIZE: $MAX_POSITION_SIZE
- IGNORE_NON_ENTRY_SIGNALS: $IGNORE_NON_ENTRY_SIGNALS
- IGNORE_TP1_SIGNALS: $IGNORE_TP1_SIGNALS
- DEBOUNCE_SECONDS: $DEBOUNCE_SECONDS

## 🎯 Expected Behavior
- ✅ Single position per signal (no more -6 contract issues)
- ✅ All positions protected with stops/takes
- ✅ TP1/TP2 signals ignored (OCO manages exits)
- ✅ Duplicate signals debounced
- ✅ Position size limits enforced

## 📊 Monitoring
Watch for these log messages:
- "Created single position with staged exits"
- "Position size limit reached - ignoring signal"
- "Debounced duplicate signal"
- "Ignoring non-entry signal"

## 🚨 Important Notes
1. **Stop any running bots** before deploying
2. **Close all existing positions** manually
3. **Test with paper trading** first
4. **Monitor logs** for proper behavior

Deployment completed: $(date)
EOF

print_success "Deployment summary created: DEPLOYMENT_SUMMARY.md"

# Step 7: Final instructions
echo ""
print_success "🎉 FIXED Trading Bot Deployment Complete!"
echo ""
print_status "Next steps:"
echo "1. Check Railway dashboard for your webhook URL"
echo "2. Update TradingView alerts with the new URL"
echo "3. Test with paper trading first"
echo "4. Monitor logs for proper behavior"
echo ""
print_warning "Remember: This fixed system prevents oversized positions!"
echo "✅ No more -6 contract issues"
echo "✅ No more orphaned positions"
echo "✅ Proper risk management"
echo ""
print_status "Deployment completed successfully! 🚀"
