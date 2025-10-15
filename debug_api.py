#!/usr/bin/env python3
"""
Debug script to test API endpoints directly
"""

import requests
import json

def test_api_directly():
    """Test API endpoints directly"""
    base_url = "https://tvwebhooks.up.railway.app"
    
    print("🧪 TESTING API ENDPOINTS DIRECTLY")
    print("=" * 50)
    
    # Test each endpoint individually
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
            url = f"{base_url}{endpoint}"
            response = requests.get(url, timeout=10)
            
            print(f"Status: {response.status_code}")
            print(f"Headers: {dict(response.headers)}")
            
            if response.status_code == 200:
                try:
                    data = response.json()
                    print(f"✅ Success: {json.dumps(data, indent=2)[:200]}...")
                except json.JSONDecodeError:
                    print(f"❌ Invalid JSON: {response.text[:200]}...")
            else:
                print(f"❌ Error: {response.text[:200]}...")
                
        except Exception as e:
            print(f"❌ Exception: {e}")

if __name__ == "__main__":
    test_api_directly()
