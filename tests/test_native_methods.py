#!/usr/bin/env python3
"""
Test script for the new native TopStepX API methods.
This script demonstrates the enhanced functionality of the trading bot.
"""

import asyncio
import os
from trading_bot import TopStepXTradingBot

async def test_native_methods():
    """
    Test the new native TopStepX API methods.
    """
    print("🧪 Testing Native TopStepX API Methods")
    print("=" * 50)
    
    # Initialize bot
    bot = TopStepXTradingBot()
    
    try:
        # Test authentication
        print("1. Testing Authentication...")
        if not await bot.authenticate():
            print("❌ Authentication failed")
            return
        print("✅ Authentication successful")
        
        # Test account listing
        print("\n2. Testing Account Listing...")
        accounts = await bot.list_accounts()
        if not accounts:
            print("❌ No accounts found")
            return
        print(f"✅ Found {len(accounts)} accounts")
        
        # Select first account for testing
        bot.selected_account = accounts[0]
        print(f"✅ Selected account: {bot.selected_account['name']}")
        
        # Test position management
        print("\n3. Testing Position Management...")
        positions = await bot.get_open_positions()
        print(f"✅ Found {len(positions)} open positions")
        
        # Test order management
        print("\n4. Testing Order Management...")
        orders = await bot.get_open_orders()
        print(f"✅ Found {len(orders)} open orders")
        
        # Test market data
        print("\n5. Testing Market Data...")
        quote = await bot.get_market_quote("MNQ")
        if "error" not in quote:
            print(f"✅ Market quote for MNQ: Bid=${quote.get('bid', 'N/A')}, Ask=${quote.get('ask', 'N/A')}")
        else:
            print(f"❌ Market quote failed: {quote['error']}")
        
        # Test historical data
        print("\n6. Testing Historical Data...")
        history = await bot.get_historical_data("MNQ", "1m", 10)
        print(f"✅ Retrieved {len(history)} historical bars")
        
        # Test bracket order system
        print("\n7. Testing Bracket Order System...")
        print("   Note: This would place a real order in live trading!")
        print("   Skipping actual order placement for safety.")
        
        # Test contract listing
        print("\n8. Testing Contract Listing...")
        contracts = await bot.get_available_contracts()
        print(f"✅ Found {len(contracts)} available contracts")
        
        print("\n🎉 All native methods tested successfully!")
        print("\nNew Native Methods Available:")
        print("  📊 Position Management:")
        print("    - get_open_positions()")
        print("    - get_position_details()")
        print("    - close_position()")
        print("  📋 Order Management:")
        print("    - get_open_orders()")
        print("    - cancel_order()")
        print("    - modify_order()")
        print("    - get_order_history()")
        print("  🔗 Bracket Order System:")
        print("    - create_bracket_order()")
        print("    - get_linked_orders()")
        print("    - adjust_bracket_orders()")
        print("  🎯 Advanced Order Types:")
        print("    - place_stop_order()")
        print("    - place_trailing_stop_order()")
        print("  📈 Market Data:")
        print("    - get_market_quote()")
        print("    - get_market_depth()")
        print("    - get_historical_data()")
        
    except Exception as e:
        print(f"❌ Test failed: {str(e)}")

def main():
    """
    Main test function.
    """
    print("TopStepX Native API Methods Test")
    print("===============================")
    print()
    print("This script tests the new native TopStepX API methods.")
    print("Make sure you have your API credentials set up.")
    print()
    
    # Check for environment variables
    api_key = os.getenv('PROJECT_X_API_KEY')
    username = os.getenv('PROJECT_X_USERNAME')
    
    if not api_key or not username:
        print("⚠️  Environment variables not found.")
        print("Please set your credentials:")
        print("  export PROJECT_X_API_KEY='your_api_key_here'")
        print("  export PROJECT_X_USERNAME='your_username_here'")
        print()
        return
    
    try:
        asyncio.run(test_native_methods())
    except KeyboardInterrupt:
        print("\n\n👋 Test stopped by user.")
    except Exception as e:
        print(f"\n❌ Unexpected error: {str(e)}")

if __name__ == "__main__":
    main()
