#!/usr/bin/env python3
"""
Debug script to examine raw order data from TopStepX API.
"""
import asyncio
import json
import sys
import os
from datetime import datetime, timedelta, timezone

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from trading_bot import TopStepXTradingBot

async def main():
    # Environment is already loaded by trading_bot import
    
    # Initialize bot
    bot = TopStepXTradingBot()
    
    # Authenticate
    print("🔐 Authenticating...")
    await bot.authenticate()
    print("✅ Authenticated\n")
    
    # Get account list
    accounts = await bot.list_accounts()
    if not accounts:
        print("❌ No accounts found")
        return
    
    # Use EXPRESS account (13543880)
    express_account = None
    for acc in accounts:
        if acc.get('id') == '13543880' or 'EXPRESS' in acc.get('name', ''):
            express_account = acc
            break
    
    if not express_account:
        print("❌ EXPRESS account not found")
        print(f"Available accounts: {[a.get('name') for a in accounts]}")
        return
    
    account_id = express_account['id']
    print(f"📊 Using account: {express_account.get('name')} ({account_id})")
    print(f"   Balance: ${express_account.get('balance', 0):,.2f}\n")
    
    # Fetch recent orders (last 7 days)
    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(days=7)
    
    print(f"📥 Fetching orders from {start_time.date()} to {end_time.date()}...")
    orders = await bot.get_order_history(
        account_id=account_id,
        limit=1000,  # Get more orders to find recent Nov 9 trades
        start_timestamp=start_time.isoformat(),
        end_timestamp=end_time.isoformat()
    )
    
    print(f"✅ Retrieved {len(orders)} orders\n")
    
    if not orders:
        print("❌ No orders found")
        return
    
    # Show first 5 orders in detail
    print("=" * 80)
    print("SAMPLE ORDERS (first 5)")
    print("=" * 80)
    
    for i, order in enumerate(orders[:5]):
        print(f"\n--- Order {i+1} ---")
        print(json.dumps(order, indent=2, default=str))
    
    # Analyze order structure
    print("\n" + "=" * 80)
    print("ORDER FIELD ANALYSIS")
    print("=" * 80)
    
    all_fields = set()
    for order in orders:
        all_fields.update(order.keys())
    
    print(f"\nAll fields found across {len(orders)} orders:")
    for field in sorted(all_fields):
        # Count how many orders have this field
        count = sum(1 for o in orders if field in o and o[field] is not None)
        sample_value = next((o[field] for o in orders if field in o and o[field] is not None), None)
        print(f"  {field:30s} - {count:3d}/{len(orders)} orders - Sample: {sample_value}")
    
    # Now consolidate and show results
    print("\n" + "=" * 80)
    print("TRADE CONSOLIDATION TEST")
    print("=" * 80)
    
    consolidated = bot._consolidate_orders_into_trades(orders)
    print(f"\n✅ Consolidated {len(orders)} orders into {len(consolidated)} trades\n")
    
    if consolidated:
        print("First 5 consolidated trades:")
        for i, trade in enumerate(consolidated[:5]):
            print(f"\n--- Trade {i+1} ---")
            print(f"  Symbol: {trade.get('symbol')}")
            print(f"  Side: {trade.get('side')}")
            print(f"  Quantity: {trade.get('quantity')}")
            print(f"  Entry Price: ${trade.get('entry_price', 0):.2f}")
            print(f"  Exit Price: ${trade.get('exit_price', 0):.2f}")
            print(f"  P&L: ${trade.get('pnl', 0):.2f}")
            print(f"  Entry Time: {trade.get('entry_time')}")
            print(f"  Exit Time: {trade.get('exit_time')}")
            print(f"  Entry Order ID: {trade.get('entry_order_id')}")
            print(f"  Exit Order ID: {trade.get('exit_order_id')}")
    
    # Calculate total P&L
    total_pnl = sum(t.get('pnl', 0) for t in consolidated)
    print(f"\n📊 Total P&L from consolidated trades: ${total_pnl:,.2f}")
    
    # Compare with the 5 trades from the CSV
    print("\n" + "=" * 80)
    print("COMPARISON WITH ACTUAL TRADES")
    print("=" * 80)
    print("\nExpected trades from CSV (Nov 9, 2025):")
    expected_trades = [
        {"id": "1598642016", "entry": 25318.50, "exit": 25319.50, "qty": 10, "pnl": 20.00},
        {"id": "1598695546", "entry": 25321.00, "exit": 25337.25, "qty": 5, "pnl": 162.50},
        {"id": "1598693592", "entry": 25321.25, "exit": 25343.00, "qty": 5, "pnl": 217.50},
        {"id": "1598713593", "entry": 25341.75, "exit": 25339.75, "qty": 5, "pnl": -20.00},
        {"id": "1598734725", "entry": 25341.75, "exit": 25370.75, "qty": 5, "pnl": 290.00},
    ]
    
    for exp in expected_trades:
        print(f"\n  Trade ID {exp['id']}:")
        print(f"    Entry: ${exp['entry']:.2f}, Exit: ${exp['exit']:.2f}")
        print(f"    Qty: {exp['qty']}, P&L: ${exp['pnl']:.2f}")
    
    expected_total = sum(t['pnl'] for t in expected_trades)
    print(f"\n  Expected Total P&L: ${expected_total:.2f}")
    print(f"  Calculated Total P&L: ${total_pnl:.2f}")
    print(f"  Difference: ${abs(expected_total - total_pnl):.2f}")

if __name__ == "__main__":
    asyncio.run(main())

