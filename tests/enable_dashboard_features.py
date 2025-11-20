#!/usr/bin/env python3
"""
Script to enable dashboard features and test functionality
"""

import os
import sys
import asyncio
import json
from datetime import datetime

def print_environment_setup():
    """Print the environment variables needed for Railway"""
    print("🔧 DASHBOARD SETUP REQUIRED")
    print("=" * 50)
    print("Add these environment variables to your Railway project:")
    print()
    print("1. DASHBOARD_AUTH_TOKEN=0mdUV5n7O534DPPvB_w0fsIYw1XeM7HSBtm_GlS_w8w")
    print("2. WEBSOCKET_ENABLED=true")
    print("3. WEBSOCKET_PORT=8081")
    print()
    print("📋 Steps to add environment variables in Railway:")
    print("1. Go to your Railway project dashboard")
    print("2. Click on your service")
    print("3. Go to 'Variables' tab")
    print("4. Add each variable above")
    print("5. Click 'Deploy' to restart with new variables")
    print()
    print("🌐 Dashboard URL (after adding variables):")
    print("https://tvwebhooks.up.railway.app/dashboard?token=0mdUV5n7O534DPPvB_w0fsIYw1XeM7HSBtm_GlS_w8w")
    print()

def test_dashboard_endpoints():
    """Test dashboard endpoints"""
    import requests
    
    base_url = "https://tvwebhooks.up.railway.app"
    token = "0mdUV5n7O534DPPvB_w0fsIYw1XeM7HSBtm_GlS_w8w"
    
    print("🧪 TESTING DASHBOARD ENDPOINTS")
    print("=" * 40)
    
    endpoints = [
        "/health",
        "/dashboard",
        f"/api/account?token={token}",
        f"/api/positions?token={token}",
        f"/api/orders?token={token}",
        f"/api/history?token={token}",
        f"/api/stats?token={token}",
        f"/api/logs?token={token}"
    ]
    
    for endpoint in endpoints:
        try:
            url = f"{base_url}{endpoint}"
            response = requests.get(url, timeout=10)
            status = "✅" if response.status_code == 200 else "❌"
            print(f"{status} {endpoint} - {response.status_code}")
            
            if endpoint.startswith("/api/") and response.status_code == 200:
                try:
                    data = response.json()
                    if isinstance(data, list):
                        print(f"   📊 Data: {len(data)} items")
                    elif isinstance(data, dict):
                        print(f"   📊 Data: {len(data)} fields")
                except:
                    print(f"   📊 Data: {len(response.text)} characters")
        except Exception as e:
            print(f"❌ {endpoint} - Error: {e}")
    
    print()

def main():
    """Main function"""
    print("🚀 DASHBOARD FEATURE ENABLER")
    print("=" * 50)
    print()
    
    print_environment_setup()
    
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        test_dashboard_endpoints()
    
    print("✨ Setup complete! Add the environment variables to Railway and redeploy.")
    print("🎯 The dashboard will then have full functionality with real trading data!")

if __name__ == "__main__":
    main()
