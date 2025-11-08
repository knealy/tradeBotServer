"""
Test script for TradingView webhook integration

This script simulates TradingView webhook calls to test the integration.
"""

import json
import requests
import time
import asyncio
from datetime import datetime

# Test webhook payloads - Realistic trading sequence with micro futures
TEST_PAYLOADS = [
    # === TRADING SEQUENCE 1: MNQ Long Position ===
    # 1. Open MNQ long position
    {
        "embeds": [{
            "title": "[MNQ1!] Open long position",
            "description": "**Strategy:** breakout\n**Time:** 2025-10-04 09:30:15\n**PnL:** $ +0.0 points",
            "color": 65280,
            "url": "https://www.tradingview.com/chart/?symbol=MNQ1!&interval=5",
            "fields": [
                {"name": "Entry", "value": "25072.64", "inline": True},
                {"name": "• Stop", "value": "25000.00", "inline": True},
                {"name": "• Target 1", "value": "25100.00", "inline": True},
                {"name": "• Target 2", "value": "25150.00", "inline": True},
                {"name": "Chart Link", "value": "[Open Chart](https://www.tradingview.com/chart/?symbol=MNQ1!&interval=5)", "inline": False}
            ]
        }]
    },
    # 2. MNQ long position hits TP1 (Pine Script format)
    {
        "embeds": [{
            "title": "[MNQ1!] trim/close long",
            "description": "**Strategy:** breakout\n**Time:** 2025-10-04 10:15:30\n**PnL:** $ +27.36 points",
            "color": 65280,
            "url": "https://www.tradingview.com/chart/?symbol=MNQ1!&interval=5",
            "fields": [
                {"name": "🌶️ Entry", "value": "25072.64 👈", "inline": True},
                {"name": "🛑 Stop", "value": "25000.00", "inline": True},
                {"name": "🥇 Target 1", "value": "25100.00 👈", "inline": True},
                {"name": "🥈 Target 2", "value": "25150.00", "inline": True},
                {"name": "Chart Link", "value": "[Open Chart](https://www.tradingview.com/chart/?symbol=MNQ1!&interval=5)", "inline": False}
            ]
        }]
    },
    # 3. MNQ long position hits TP2 (final close)
    {
        "embeds": [{
            "title": "[MNQ1!] TP2 hit for long",
            "description": "**Strategy:** breakout\n**Time:** 2025-10-04 10:45:00\n**PnL:** $ +77.36 points",
            "color": 65280,
            "url": "https://www.tradingview.com/chart/?symbol=MNQ1!&interval=5",
            "fields": [
                {"name": "Entry", "value": "25072.64", "inline": True},
                {"name": "• Stop", "value": "25000.00", "inline": True},
                {"name": "• Target 1", "value": "25100.00", "inline": True},
                {"name": "• Target 2", "value": "25150.00", "inline": True},
                {"name": "Chart Link", "value": "[Open Chart](https://www.tradingview.com/chart/?symbol=MNQ1!&interval=5)", "inline": False}
            ]
        }]
    },
    
    # === TRADING SEQUENCE 2: MES Short Position ===
    # 4. Open MES short position
    {
        "embeds": [{
            "title": "[MES1!] Open short position",
            "description": "**Strategy:** momentum\n**Time:** 2025-10-04 11:20:45\n**PnL:** $ +0.0 points",
            "color": 16711680,
            "url": "https://www.tradingview.com/chart/?symbol=MES1!&interval=5",
            "fields": [
                {"name": "Entry", "value": "6785.00", "inline": True},
                {"name": "• Stop", "value": "6795.00", "inline": True},
                {"name": "• Target 1", "value": "6775.00", "inline": True},
                {"name": "• Target 2", "value": "6765.00", "inline": True},
                {"name": "Chart Link", "value": "[Open Chart](https://www.tradingview.com/chart/?symbol=MES1!&interval=5)", "inline": False}
            ]
        }]
    },
    # 5. MES short position gets stopped out
    {
        "embeds": [{
            "title": "[MES1!] stop out short",
            "description": "**Strategy:** momentum\n**Time:** 2025-10-04 12:05:15\n**PnL:** $ -10.0 points",
            "color": 16711680,
            "url": "https://www.tradingview.com/chart/?symbol=MES1!&interval=5",
            "fields": [
                {"name": "Entry", "value": "6785.00", "inline": True},
                {"name": "• Stop", "value": "6795.00", "inline": True},
                {"name": "• Target 1", "value": "6775.00", "inline": True},
                {"name": "• Target 2", "value": "6765.00", "inline": True},
                {"name": "Chart Link", "value": "[Open Chart](https://www.tradingview.com/chart/?symbol=MES1!&interval=5)", "inline": False}
            ]
        }]
    },
    
    # === TRADING SEQUENCE 3: MYM Long Position ===
    # 6. Open MYM long position
    {
        "embeds": [{
            "title": "[MYM1!] Open long position",
            "description": "**Strategy:** trend\n**Time:** 2025-10-04 13:10:30\n**PnL:** $ +0.0 points",
            "color": 65280,
            "url": "https://www.tradingview.com/chart/?symbol=MYM1!&interval=5",
            "fields": [
                {"name": "Entry", "value": "39850.00", "inline": True},
                {"name": "• Stop", "value": "39800.00", "inline": True},
                {"name": "• Target 1", "value": "39900.00", "inline": True},
                {"name": "• Target 2", "value": "39950.00", "inline": True},
                {"name": "Chart Link", "value": "[Open Chart](https://www.tradingview.com/chart/?symbol=MYM1!&interval=5)", "inline": False}
            ]
        }]
    },
    # 7. MYM long position gets trimmed (partial close)
    {
        "embeds": [{
            "title": "[MYM1!] trim/close long",
            "description": "**Strategy:** trend\n**Time:** 2025-10-04 13:45:20\n**PnL:** $ +50.0 points",
            "color": 16776960,
            "url": "https://www.tradingview.com/chart/?symbol=MYM1!&interval=5",
            "fields": [
                {"name": "Entry", "value": "39850.00", "inline": True},
                {"name": "• Stop", "value": "39800.00", "inline": True},
                {"name": "• Target 1", "value": "39900.00", "inline": True},
                {"name": "• Target 2", "value": "39950.00", "inline": True},
                {"name": "Chart Link", "value": "[Open Chart](https://www.tradingview.com/chart/?symbol=MYM1!&interval=5)", "inline": False}
            ]
        }]
    },
    # 8. MYM long position hits TP2 (final close)
    {
        "embeds": [{
            "title": "[MYM1!] TP2 hit for long",
            "description": "**Strategy:** trend\n**Time:** 2025-10-04 14:20:00\n**PnL:** $ +100.0 points",
            "color": 65280,
            "url": "https://www.tradingview.com/chart/?symbol=MYM1!&interval=5",
            "fields": [
                {"name": "Entry", "value": "39850.00", "inline": True},
                {"name": "• Stop", "value": "39800.00", "inline": True},
                {"name": "• Target 1", "value": "39900.00", "inline": True},
                {"name": "• Target 2", "value": "39950.00", "inline": True},
                {"name": "Chart Link", "value": "[Open Chart](https://www.tradingview.com/chart/?symbol=MYM1!&interval=5)", "inline": False}
            ]
        }]
    }
]

def test_webhook_payload(payload: dict, webhook_url: str = "http://localhost:8080/"):
    """Test a single webhook payload"""
    try:
        print(f"Testing payload: {payload['embeds'][0]['title']}")
        
        response = requests.post(
            webhook_url,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=10
        )
        
        print(f"Status Code: {response.status_code}")
        print(f"Response: {response.text}")
        
        if response.status_code == 200:
            try:
                response_data = response.json()
                if response_data.get("action"):
                    action = response_data.get("action")
                    print(f"✅ Webhook test successful! (Action: {action})")
                elif response_data.get("success"):
                    print("✅ Webhook test successful! (Trade executed)")
                else:
                    print("⚠️  Webhook test completed with warnings")
            except:
                print("✅ Webhook test successful!")
        else:
            print("❌ Webhook test failed!")
        
        return response.status_code == 200
        
    except requests.exceptions.RequestException as e:
        print(f"❌ Request failed: {str(e)}")
        return False

def test_all_payloads(webhook_url: str = "http://localhost:8080/"):
    """Test all webhook payloads"""
    print("🧪 Testing TradingView Webhook Integration")
    print("=" * 50)
    print("Testing realistic trading sequence with micro futures contracts")
    print("Sequence: MNQ long (open→TP1→TP2), MES short (open→stop), MYM long (open→trim→TP2)")
    print("Features: Native bracket orders, position monitoring, real position queries")
    print("=" * 50)
    
    success_count = 0
    total_count = len(TEST_PAYLOADS)
    
    # Test descriptions - Realistic trading sequence with market orders for trim/close
    test_descriptions = [
        "MNQ Open long position (should execute native bracket order with stop/take profit)",
        "MNQ TP1 hit long (should close partial position with market order + run monitor)",
        "MNQ TP2 hit long (should close remaining position at TP2)",
        "MES Open short position (should execute native bracket order with stop/take profit)",
        "MES Stop out short (should flatten all sell positions)",
        "MYM Open long position (should execute native bracket order with stop/take profit)",
        "MYM Trim long (should close partial position with market order + run monitor)",
        "MYM TP2 hit long (should close remaining position at TP2)"
    ]
    
    for i, payload in enumerate(TEST_PAYLOADS, 1):
        print(f"\n📋 Test {i}/{total_count}: {test_descriptions[i-1]}")
        print("-" * 50)
        
        if test_webhook_payload(payload, webhook_url):
            success_count += 1
        
        # Wait between tests
        if i < total_count:
            time.sleep(2)
    
    print("\n" + "=" * 50)
    print(f"📊 Test Results: {success_count}/{total_count} successful")
    
    if success_count == total_count:
        print("🎉 All webhook tests passed!")
    else:
        print("⚠️  Some webhook tests failed!")
    
    return success_count == total_count

def test_webhook_server_connection(webhook_url: str = "http://localhost:8080/"):
    """Test if webhook server is running"""
    try:
        response = requests.get(webhook_url, timeout=5)
        print(f"✅ Webhook server is running (Status: {response.status_code})")
        return True
    except requests.exceptions.RequestException as e:
        print(f"❌ Webhook server is not running: {str(e)}")
        print("💡 Make sure to start the webhook server first:")
        print("   python3 webhook_server.py")
        return False

def main():
    """Main test function"""
    print("🚀 TradingView Webhook Test Suite")
    print("=" * 50)
    
    webhook_url = "http://localhost:8080/"
    
    # Test server connection
    if not test_webhook_server_connection(webhook_url):
        return False
    
    # Test all payloads
    return test_all_payloads(webhook_url)

if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)
