#!/usr/bin/env python3
"""
Test script to check what the API actually returns
"""

import requests
import json

def test_api_endpoints():
    base_url = "https://tvwebhooks.up.railway.app"
    token = "0mdUV5n7O534DPPvB_w0fsIYw1XeM7HSBtm_GlS_w8w"
    
    print("🧪 TESTING API DATA STRUCTURE")
    print("=" * 50)
    
    endpoints = [
        "/api/account",
        "/api/positions", 
        "/api/orders",
        "/api/history",
        "/api/stats",
        "/api/logs"
    ]
    
    for endpoint in endpoints:
        print(f"\n📊 Testing {endpoint}:")
        try:
            url = f"{base_url}{endpoint}?token={token}"
            response = requests.get(url, timeout=10)
            
            if response.status_code == 200:
                try:
                    data = response.json()
                    print(f"✅ Status: {response.status_code}")
                    print(f"📋 Data type: {type(data)}")
                    
                    if isinstance(data, list):
                        print(f"📊 Array length: {len(data)}")
                        if data:
                            print(f"🔍 First item keys: {list(data[0].keys()) if isinstance(data[0], dict) else 'Not a dict'}")
                            print(f"📄 First item: {json.dumps(data[0], indent=2)[:200]}...")
                    elif isinstance(data, dict):
                        print(f"🔍 Keys: {list(data.keys())}")
                        print(f"📄 Data: {json.dumps(data, indent=2)[:300]}...")
                    else:
                        print(f"📄 Raw data: {str(data)[:200]}...")
                        
                except json.JSONDecodeError:
                    print(f"❌ Invalid JSON: {response.text[:200]}...")
            else:
                print(f"❌ Status: {response.status_code}")
                print(f"📄 Response: {response.text[:200]}...")
                
        except Exception as e:
            print(f"❌ Error: {e}")

if __name__ == "__main__":
    test_api_endpoints()
