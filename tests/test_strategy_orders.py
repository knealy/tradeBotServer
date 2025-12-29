#!/usr/bin/env python3
"""
Ultra-simple test: Place market order via same path as CLI to verify automation works.
Tests if the issue is with strategies or with the API itself.
"""

import asyncio
import os
import sys
from dotenv import load_dotenv

# Load environment
load_dotenv()

from trading_bot import TopStepXTradingBot

async def test():
    print("="*70)
    print("STRATEGY ORDER AUTOMATION TEST")
    print("="*70)
    print()
    
    # Init
    api_key = os.getenv('TOPSETPX_API_KEY')
    username = os.getenv('TOPSETPX_USERNAME')
    
    if not api_key or not username:
        print("❌ Missing credentials in .env")
        return
    
    bot = TopStepXTradingBot(api_key=api_key, username=username)
    
    # Auth
    print("🔐 Authenticating...")
    if not await bot._ensure_valid_token():
        print("❌ Auth failed")
        return
    print("✅ Authenticated")
    
    # Get accounts
    print("\n📋 Fetching accounts...")
    accounts = await bot.list_accounts()
    if not accounts:
        print("❌ No accounts found")
        return
    
    # Select account 1
    success = await bot.switch_account("1")
    if not success:
        print("❌ Failed to switch account")
        return
    print(f"✅ Using account: {bot.selected_account['name']}")
    
    # Load contracts
    print("\n📦 Loading contracts...")
    await bot.get_available_contracts()
    print("✅ Contracts loaded")
    
    # Test 1: Plain market order (NO brackets, NO protections)
    print("\n"+"="*70)
    print("TEST 1: Plain Market Order (same as: trade mnq buy 1)")
    print("="*70)
    print("📝 Placing BUY 1 MNQ at market...")
    
    result = await bot.place_market_order(
        symbol="MNQ",
        side="BUY",
        quantity=1,
        order_type="market",
        strategy_name="test"
    )
    
    print(f"\n🔍 Result: {result}")
    
    if result.get('error'):
        print(f"❌ FAILED: {result['error']}")
        print("\n⚠️  If this fails, the issue is NOT with strategy code.")
        print("   It's either TopStepX API or account settings.")
        return
    else:
        print(f"✅ SUCCESS: Market order placed!")
        print(f"   Order ID: {result.get('orderId', 'unknown')}")
    
    # Wait for fill
    print("\n⏳ Waiting 5 seconds for fill...")
    await asyncio.sleep(5)
    
    # Check positions
    positions = await bot.get_open_positions(account_id=bot.selected_account['id'])
    print(f"\n📊 Open positions: {len(positions)}")
    if positions:
        for pos in positions:
            print(f"   - {pos.get('symbol')}: {pos.get('size')} @ ${pos.get('averagePrice'):.2f}")
    
    # Test 2: Market order with brackets
    print("\n"+"="*70)
    print("TEST 2: Market Order with SL/TP Brackets")
    print("="*70)
    print("📝 Placing SELL 1 MNQ with brackets...")
    
    bracket_result = await bot.place_market_order(
        symbol="MNQ",
        side="SELL",
        quantity=1,
        stop_loss_ticks=-15,  # 15 ticks below (for SELL, this is above entry)
        take_profit_ticks=10,  # 10 ticks above (for SELL, this is below entry) 
        order_type="bracket",
        strategy_name="test"
    )
    
    print(f"\n🔍 Result: {bracket_result}")
    
    if bracket_result.get('error'):
        print(f"❌ FAILED: {bracket_result['error']}")
        print("\n⚠️  Bracket orders failing but plain market worked.")
        print("   Likely issue: 'Auto OCO Brackets' not enabled in TopStepX account.")
    else:
        print(f"✅ SUCCESS: Bracket order placed!")
        print(f"   Order ID: {bracket_result.get('orderId', 'unknown')}")
    
    # Cleanup
    print("\n🧹 Cleaning up - flattening all positions...")
    await bot.flatten_all_positions(interactive=False)
    print("✅ All positions closed")
    
    print("\n"+"="*70)
    print("TEST COMPLETE")
    print("="*70)
    print()
    print("RESULTS:")
    print(f"  Plain Market Order: {'✅ WORKS' if not result.get('error') else '❌ FAILS'}")
    print(f"  Bracket Order: {'✅ WORKS' if not bracket_result.get('error') else '❌ FAILS'}")
    print()
    if not result.get('error'):
        print("✅ Strategies CAN place orders - code is working!")
        print("   If brackets fail, enable 'Auto OCO Brackets' in TopStepX.")
    else:
        print("❌ Even plain orders fail - check TopStepX API status")

if __name__ == "__main__":
    try:
        asyncio.run(test())
    except KeyboardInterrupt:
        print("\n👋 Test cancelled")
    except Exception as e:
        print(f"\n❌ Test failed with exception: {e}")
        import traceback
        traceback.print_exc()
