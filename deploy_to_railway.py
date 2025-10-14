#!/usr/bin/env python3
"""
Deploy Trading Bot to Railway

This script updates the Railway deployment with the latest code changes.
"""

import subprocess
import sys
import os
from pathlib import Path

def run_command(command, description):
    """Run a command and handle errors"""
    print(f"🔄 {description}...")
    try:
        result = subprocess.run(command, shell=True, check=True, capture_output=True, text=True)
        print(f"✅ {description} completed")
        if result.stdout:
            print(result.stdout)
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ {description} failed: {e}")
        if e.stderr:
            print(f"Error: {e.stderr}")
        return False

def main():
    """Main deployment function"""
    print("🚀 Starting Railway Deployment")
    print("=" * 50)
    
    # Check if we're in the right directory
    if not Path("trading_bot.py").exists():
        print("❌ Error: trading_bot.py not found. Please run from the project root.")
        sys.exit(1)
    
    # Check if Railway CLI is installed
    if not run_command("railway --version", "Checking Railway CLI"):
        print("❌ Railway CLI not found. Please install it first:")
        print("   npm install -g @railway/cli")
        sys.exit(1)
    
    # Check if we're logged in to Railway
    if not run_command("railway whoami", "Checking Railway authentication"):
        print("❌ Not logged in to Railway. Please run:")
        print("   railway login")
        sys.exit(1)
    
    # Add all files to git
    if not run_command("git add .", "Adding files to git"):
        sys.exit(1)
    
    # Commit changes
    commit_message = "Enhanced trading bot with real entry prices, exit notifications, and auto fill checking"
    if not run_command(f'git commit -m "{commit_message}"', "Committing changes"):
        print("⚠️  No changes to commit or commit failed")
    
    # Push to Railway
    if not run_command("railway up", "Deploying to Railway"):
        sys.exit(1)
    
    print("\n🎉 Deployment completed successfully!")
    print("=" * 50)
    print("📋 Deployment Summary:")
    print("✅ Real entry prices in Discord notifications")
    print("✅ Exit fill prices for position closes")
    print("✅ Bracket order type fixes")
    print("✅ Automatic fill checking")
    print("✅ Enhanced Discord notifications")
    print("✅ Webhook server consistency")
    
    print("\n🔧 New Commands Available:")
    print("  check_fills - Check for filled orders")
    print("  auto_fills - Enable automatic fill checking")
    print("  stop_auto_fills - Disable automatic fill checking")
    
    print("\n📱 Discord Notifications Now Include:")
    print("  • Real entry prices (not 'Market')")
    print("  • Exit prices for position closes")
    print("  • Order fill notifications for TP/Stop hits")
    print("  • Position close notifications")
    print("  • Correct order types (Bracket vs Market)")

if __name__ == "__main__":
    main()
