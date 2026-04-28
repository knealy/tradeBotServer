#!/usr/bin/env python3
"""
Position Monitor Script

Continuously monitors and displays open positions, orders, and account state.
Designed to run in a separate terminal window.
"""

import os
import sys
import asyncio
import json
from datetime import datetime
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from trading_bot import TopStepXTradingBot
import logging

from core.logging_setup import configure_logging
configure_logging()
logger = logging.getLogger(__name__)


async def monitor_positions(account_select=None, refresh_interval=5):
    """Monitor positions and display updates."""
    bot = TopStepXTradingBot()
    
    try:
        # Initialize bot
        print("🔐 Authenticating...")
        if not await bot._ensure_valid_token():
            print("❌ Authentication failed")
            return
        
        # Get accounts
        accounts = await bot.list_accounts()
        if not accounts:
            print("❌ No accounts found")
            return
        
        # Select account
        if account_select:
            try:
                if account_select.isdigit():
                    idx = int(account_select) - 1
                    if 0 <= idx < len(accounts):
                        bot.selected_account = accounts[idx]
                    else:
                        print(f"❌ Invalid account index: {account_select}")
                        return
                else:
                    for acc in accounts:
                        if str(acc.get('id')) == account_select or acc.get('name') == account_select:
                            bot.selected_account = acc
                            break
                    if not bot.selected_account:
                        print(f"❌ Account not found: {account_select}")
                        return
            except Exception as e:
                print(f"❌ Error selecting account: {e}")
                return
        else:
            bot.selected_account = accounts[0]
        
        account_name = bot.selected_account.get('name', 'Unknown') if isinstance(bot.selected_account, dict) else 'Unknown'
        account_id = bot.selected_account.get('id') if isinstance(bot.selected_account, dict) else bot.selected_account
        
        # Initialize account tracker for compliance data
        if hasattr(bot, 'account_tracker') and bot.account_tracker:
            try:
                balance = await bot.get_account_balance(account_id=account_id)
                if balance:
                    account_type = bot.selected_account.get('type', 'unknown') if isinstance(bot.selected_account, dict) else 'unknown'
                    bot.account_tracker.initialize(
                        account_id=str(account_id),
                        starting_balance=balance,
                        account_type=account_type
                    )
            except Exception as e:
                logger.debug(f"Could not initialize account tracker: {e}")
        
        print(f"✅ Monitoring account: {account_name}")
        print(f"🔄 Refresh interval: {refresh_interval} seconds")
        print("=" * 80)
        print("Press Ctrl+C to stop monitoring\n")
        
        # Monitor loop
        while True:
            try:
                # Clear screen (works on most terminals)
                os.system('clear' if os.name != 'nt' else 'cls')
                
                print("=" * 80)
                print(f"📊 POSITION MONITOR - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
                print("=" * 80)
                print(f"Account: {account_name} (ID: {account_id})\n")
                
                # Get account info
                account_info = await bot.get_account_info(account_id=account_id)
                balance = await bot.get_account_balance(account_id=account_id)
                
                if account_info or balance:
                    if balance:
                        print(f"💰 Balance: ${balance:,.2f}")
                    if account_info:
                        unrealized_pnl = account_info.get('unrealized_pnl', 0)
                        realized_pnl = account_info.get('realized_pnl', 0)
                        if unrealized_pnl or realized_pnl:
                            print(f"📈 Unrealized P&L: ${unrealized_pnl:,.2f}")
                            print(f"💵 Realized P&L: ${realized_pnl:,.2f}")
                    print()
                
                # Get positions
                positions = await bot.get_open_positions(account_id=account_id)
                if positions:
                    print(f"📦 OPEN POSITIONS ({len(positions)}):")
                    print("-" * 80)
                    print(f"{'Symbol':<8} {'Side':<6} {'Qty':<6} {'Entry':<12} {'Current':<12} {'P&L':<12} {'P&L %':<8}")
                    print("-" * 80)
                    
                    total_pnl = 0
                    for pos in positions:
                        # Handle multiple field name variations (camelCase, snake_case, raw_data)
                        symbol = pos.get('symbol') or pos.get('Symbol') or 'N/A'
                        
                        # Handle side: can be numeric (0=LONG, 1=SHORT), string ("LONG"/"SHORT"), or missing
                        side_raw = pos.get('side')
                        if side_raw == 0 or side_raw == '0' or (isinstance(side_raw, str) and side_raw.upper() == 'LONG'):
                            side = 'LONG'
                        elif side_raw == 1 or side_raw == '1' or (isinstance(side_raw, str) and side_raw.upper() == 'SHORT'):
                            side = 'SHORT'
                        else:
                            side = str(side_raw) if side_raw is not None else 'N/A'
                        
                        # Handle quantity: can be 'quantity', 'size', 'qty'
                        quantity = pos.get('quantity') or pos.get('size') or pos.get('qty') or 0
                        if isinstance(quantity, str):
                            try:
                                quantity = int(float(quantity))
                            except:
                                quantity = 0
                        
                        # Handle entry price: can be 'entry_price', 'entryPrice', 'entry'
                        entry_price = pos.get('entry_price') or pos.get('entryPrice') or pos.get('entry') or 0
                        if isinstance(entry_price, str):
                            try:
                                entry_price = float(entry_price)
                            except:
                                entry_price = 0
                        
                        # Handle current price: can be 'current_price', 'currentPrice', 'current', 'markPrice'
                        current_price = pos.get('current_price') or pos.get('currentPrice') or pos.get('current') or pos.get('markPrice') or entry_price
                        if isinstance(current_price, str):
                            try:
                                current_price = float(current_price)
                            except:
                                current_price = entry_price
                        
                        # Handle P&L: can be 'unrealized_pnl', 'unrealizedPnl', 'unrealizedPnL', 'pnl'
                        pnl = pos.get('unrealized_pnl') or pos.get('unrealizedPnl') or pos.get('unrealizedPnL') or pos.get('pnl') or 0
                        if isinstance(pnl, str):
                            try:
                                pnl = float(pnl)
                            except:
                                pnl = 0
                        
                        # Calculate P&L percentage (MNQ tick size is 0.25, point value is $5)
                        if entry_price and quantity:
                            # For MNQ: 1 tick = 0.25 points, 1 point = $5, so 1 tick = $1.25
                            # P&L in dollars / (entry_price * quantity * point_value_per_tick)
                            # Simplified: assume $5 per point, 0.25 per tick = $1.25 per tick
                            tick_size = 0.25  # MNQ default
                            point_value = 5.0  # MNQ default
                            pnl_pct = (pnl / (entry_price * quantity * point_value / tick_size)) * 100 if entry_price and quantity else 0
                        else:
                            pnl_pct = 0
                        
                        total_pnl += pnl
                        
                        pnl_color = "🟢" if pnl >= 0 else "🔴"
                        print(f"{symbol:<8} {side:<6} {quantity:<6} ${entry_price:<11.2f} ${current_price:<11.2f} {pnl_color} ${pnl:<11.2f} {pnl_pct:>6.2f}%")
                    
                    print("-" * 80)
                    total_color = "🟢" if total_pnl >= 0 else "🔴"
                    print(f"{'TOTAL':<32} {total_color} ${total_pnl:<11.2f}")
                    print()
                else:
                    print("📦 No open positions\n")
                
                # Get open orders
                orders = await bot.get_open_orders(account_id=account_id)
                if orders:
                    print(f"📋 OPEN ORDERS ({len(orders)}):")
                    print("-" * 80)
                    print(f"{'Order ID':<12} {'Symbol':<8} {'Side':<6} {'Qty':<6} {'Price':<12} {'Type':<12} {'Status':<10}")
                    print("-" * 80)
                    
                    for order in orders:
                        order_id = str(order.get('id', 'N/A'))[:12]
                        symbol = order.get('symbol', 'N/A')
                        side = order.get('side', 'N/A')
                        quantity = order.get('quantity', 0)
                        price = order.get('price', order.get('limit_price', 0))
                        order_type = order.get('order_type', 'N/A')
                        status = order.get('status', 'N/A')
                        
                        print(f"{order_id:<12} {symbol:<8} {side:<6} {quantity:<6} ${price:<11.2f} {order_type:<12} {status:<10}")
                    
                    print()
                else:
                    print("📋 No open orders\n")
                
                # Get compliance status
                compliance = None
                if hasattr(bot, 'account_tracker') and bot.account_tracker:
                    compliance = bot.account_tracker.check_compliance()
                if compliance:
                    print("⚠️  COMPLIANCE STATUS:")
                    print("-" * 80)
                    dll = compliance.get('dll', {})
                    mll = compliance.get('mll', {})
                    drawdown = compliance.get('trailing_drawdown', {})
                    
                    dll_status = "✅" if dll.get('compliant', False) else "❌"
                    mll_status = "✅" if mll.get('compliant', False) else "❌"
                    dd_status = "✅" if drawdown.get('compliant', False) else "❌"
                    
                    print(f"DLL: {dll_status} {dll.get('current', 0):.2f} / {dll.get('limit', 0):.2f} ({dll.get('percent', 0):.1f}%)")
                    print(f"MLL: {mll_status} {mll.get('current', 0):.2f} / {mll.get('limit', 0):.2f} ({mll.get('percent', 0):.1f}%)")
                    print(f"Drawdown: {dd_status} {drawdown.get('current', 0):.2f} / {drawdown.get('limit', 0):.2f} ({drawdown.get('percent', 0):.1f}%)")
                    print()
                
                print("=" * 80)
                print(f"Next update in {refresh_interval} seconds... (Ctrl+C to stop)")
                
                await asyncio.sleep(refresh_interval)
                
            except KeyboardInterrupt:
                print("\n\n👋 Stopping position monitor...")
                break
            except Exception as e:
                logger.error(f"Error in monitor loop: {e}")
                import traceback
                logger.error(traceback.format_exc())
                await asyncio.sleep(refresh_interval)
    
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='Monitor trading positions and account state')
    parser.add_argument('--account', type=str, default=None, help='Account index or ID to monitor')
    parser.add_argument('--interval', type=int, default=5, help='Refresh interval in seconds (default: 5)')
    
    args = parser.parse_args()
    
    asyncio.run(monitor_positions(account_select=args.account, refresh_interval=args.interval))
