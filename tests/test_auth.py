#!/usr/bin/env python3
"""
Test Authentication Script for TopStepX Trading Bot

This script provides a simple way to test authentication and account listing
without running the full interactive trading bot.
"""

import asyncio
import os
import sys
from trading_bot import TopStepXTradingBot

async def main():
    """Test authentication and account listing."""
    print("🔐 Testing TopStepX Authentication")
    print("=" * 50)
    
    # Load environment variables
    try:
        import load_env
        print("✅ Environment variables loaded")
    except ImportError:
        print("⚠️  load_env module not found, using system environment variables")
    
    # Check environment variables
    api_key = os.getenv('TOPSTEPX_API_KEY') or os.getenv('PROJECT_X_API_KEY')
    username = os.getenv('TOPSTEPX_USERNAME') or os.getenv('PROJECT_X_USERNAME')
    account_id = os.getenv('TOPSTEPX_ACCOUNT_ID') or os.getenv('PROJECT_X_ACCOUNT_ID')
    
    print(f"API Key: {'✅ Set' if api_key else '❌ Missing'}")
    print(f"Username: {'✅ Set' if username else '❌ Missing'}")
    print(f"Account ID: {'✅ Set' if account_id else '❌ Missing'}")
    
    if not api_key or not username:
        print("\n❌ Missing required credentials. Please set:")
        print("   TOPSTEPX_API_KEY and TOPSTEPX_USERNAME")
        print("   (or PROJECT_X_API_KEY and PROJECT_X_USERNAME for legacy)")
        return False
    
    # Initialize bot
    bot = TopStepXTradingBot()
    
    # Test authentication
    print("\n🔑 Authenticating...")
    try:
        success = await bot.authenticate()
        if success:
            print("✅ Authentication successful!")
        else:
            print("❌ Authentication failed")
            return False
    except Exception as e:
        print(f"❌ Authentication error: {e}")
        return False
    
    # List accounts
    print("\n📋 Fetching accounts...")
    try:
        accounts = await bot.list_accounts()
        if accounts:
            print(f"✅ Found {len(accounts)} accounts:")
            for i, acc in enumerate(accounts, 1):
                # Convert both to strings for comparison
                acc_id_str = str(acc.get('id', ''))
                account_id_str = str(account_id) if account_id else ''
                status = "🎯 SELECTED" if acc_id_str == account_id_str else ""
                print(f"  {i}. {acc['name']} (ID: {acc['id']}) {status}")
            
            # Check if target account is found
            if account_id:
                target_found = any(str(acc.get('id', '')) == str(account_id) for acc in accounts)
                if target_found:
                    print(f"\n✅ Target account {account_id} found in account list")
                else:
                    print(f"\n⚠️  Target account {account_id} not found in account list")
        else:
            print("❌ No accounts found")
            return False
            
    except Exception as e:
        print(f"❌ Error fetching accounts: {e}")
        return False
    
    # Test account balance
    if account_id:
        print(f"\n💰 Checking balance for account {account_id}...")
        try:
            balance = await bot.get_account_balance(account_id)
            if balance is not None:
                print(f"✅ Account balance: ${balance:,.2f}")
            else:
                print("⚠️  Could not retrieve account balance")
        except Exception as e:
            print(f"❌ Error checking balance: {e}")
    
    print("\n🎉 Authentication test completed successfully!")
    return True

if __name__ == "__main__":
    try:
        result = asyncio.run(main())
        sys.exit(0 if result else 1)
    except KeyboardInterrupt:
        print("\n\n⏹️  Test interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        sys.exit(1)
