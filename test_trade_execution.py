#!/usr/bin/env python3
"""
Test script to verify trade execution on PRACTICE account.

This script calls the /api/test/overnight-breakout endpoint to simulate
overnight breakout trades and verify the bot can place orders successfully.

Usage:
    python test_trade_execution.py
    python test_trade_execution.py --symbol MES --quantity 2
    python test_trade_execution.py --url http://localhost:8080
"""

import argparse
import json
import sys
import requests
from typing import Dict, Any


def test_trade_execution(
    base_url: str = "https://tvwebhooks.up.railway.app",
    symbol: str = "MNQ",
    quantity: int = 1,
    account_name: str = "PRACTICE"
) -> Dict[str, Any]:
    """
    Test trade execution by calling the overnight breakout test endpoint.
    
    Args:
        base_url: Base URL of the Railway server
        symbol: Trading symbol (default: MNQ)
        quantity: Number of contracts (default: 1)
        account_name: Account name to search for (default: PRACTICE)
    
    Returns:
        Dict with test results
    """
    url = f"{base_url}/api/test/overnight-breakout"
    
    payload = {
        "symbol": symbol,
        "quantity": quantity,
        "account_name": account_name
    }
    
    print(f"🧪 Testing trade execution on {base_url}")
    print(f"   Symbol: {symbol}")
    print(f"   Quantity: {quantity}")
    print(f"   Account: {account_name}")
    print(f"   URL: {url}")
    print()
    
    try:
        print("📡 Sending request...")
        response = requests.post(
            url,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=60
        )
        
        print(f"📥 Response status: {response.status_code}")
        
        if response.status_code == 200:
            result = response.json()
            print("\n✅ SUCCESS! Trades placed successfully")
            print(f"\n📊 Results:")
            print(f"   Account: {result.get('account', {}).get('name', 'N/A')} (ID: {result.get('account', {}).get('id', 'N/A')})")
            print(f"   Symbol: {result.get('symbol', 'N/A')}")
            print(f"   Current Price: ${result.get('current_price', 0):.2f}")
            print(f"   Tick Size: {result.get('tick_size', 0)}")
            
            print(f"\n📈 Trades Placed:")
            for i, trade in enumerate(result.get('trades', []), 1):
                side = trade.get('side', 'N/A')
                entry = trade.get('entry_price', 0)
                sl = trade.get('stop_loss', 0)
                tp = trade.get('take_profit', 0)
                trade_result = trade.get('result', {})
                
                print(f"\n   Trade {i} ({side}):")
                print(f"      Entry: ${entry:.2f}")
                print(f"      Stop Loss: ${sl:.2f}")
                print(f"      Take Profit: ${tp:.2f}")
                
                if "error" in trade_result:
                    print(f"      ❌ Error: {trade_result['error']}")
                elif "orderId" in trade_result or "id" in trade_result:
                    order_id = trade_result.get('orderId') or trade_result.get('id', 'N/A')
                    print(f"      ✅ Order ID: {order_id}")
                else:
                    print(f"      ⚠️  Response: {json.dumps(trade_result, indent=8)}")
            
            verification = result.get('verification', {})
            print(f"\n🔍 Verification:")
            print(f"   Open Positions: {verification.get('positions_count', 0)}")
            print(f"   Open Orders: {verification.get('orders_count', 0)}")
            
            if verification.get('orders_count', 0) > 0:
                print(f"\n   Recent Orders:")
                for order in verification.get('orders', [])[:3]:
                    order_id = order.get('id', 'N/A')
                    side = order.get('side', 'N/A')
                    status = order.get('status', 'N/A')
                    print(f"      - {order_id}: {side} ({status})")
            
            return result
            
        else:
            error_data = response.json() if response.content else {"error": "No response body"}
            print(f"\n❌ ERROR: {response.status_code}")
            print(f"   {json.dumps(error_data, indent=2)}")
            return {"error": error_data, "status_code": response.status_code}
            
    except requests.exceptions.Timeout:
        print("\n❌ ERROR: Request timed out (60s)")
        return {"error": "Request timeout"}
    except requests.exceptions.ConnectionError as e:
        print(f"\n❌ ERROR: Could not connect to {base_url}")
        print(f"   {str(e)}")
        return {"error": f"Connection error: {str(e)}"}
    except Exception as e:
        print(f"\n❌ ERROR: {type(e).__name__}: {str(e)}")
        import traceback
        traceback.print_exc()
        return {"error": str(e)}


def main():
    parser = argparse.ArgumentParser(
        description="Test trade execution on PRACTICE account",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Test with defaults (MNQ, 1 contract, PRACTICE account)
  python test_trade_execution.py
  
  # Test with custom symbol and quantity
  python test_trade_execution.py --symbol MES --quantity 2
  
  # Test against local server
  python test_trade_execution.py --url http://localhost:8080
  
  # Test with different account
  python test_trade_execution.py --account-name EXPRESS
        """
    )
    
    parser.add_argument(
        '--url',
        default='https://tvwebhooks.up.railway.app',
        help='Base URL of the server (default: https://tvwebhooks.up.railway.app)'
    )
    parser.add_argument(
        '--symbol',
        default='MNQ',
        help='Trading symbol (default: MNQ)'
    )
    parser.add_argument(
        '--quantity',
        type=int,
        default=1,
        help='Number of contracts (default: 1)'
    )
    parser.add_argument(
        '--account-name',
        default='PRACTICE',
        help='Account name to search for (default: PRACTICE)'
    )
    
    args = parser.parse_args()
    
    result = test_trade_execution(
        base_url=args.url,
        symbol=args.symbol.upper(),
        quantity=args.quantity,
        account_name=args.account_name.upper()
    )
    
    # Exit with error code if test failed
    if "error" in result:
        sys.exit(1)
    else:
        # Check if any trades had errors
        trades = result.get('trades', [])
        if trades:
            for trade in trades:
                trade_result = trade.get('result', {})
                if "error" in trade_result:
                    print(f"\n⚠️  Warning: Some trades had errors")
                    sys.exit(1)
        sys.exit(0)


if __name__ == '__main__':
    main()

