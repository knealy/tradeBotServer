#!/usr/bin/env python3
"""
Start Async Webhook Server

Launches the high-performance async webhook server with priority task queue.

Usage:
    python start_async_webhook.py
    
Environment Variables:
    WEBHOOK_HOST - Server host (default: 0.0.0.0)
    WEBHOOK_PORT - Server port (default: 8080)
    PROJECT_X_API_KEY - TopStepX API key
    PROJECT_X_USERNAME - TopStepX username
    TOPSTEPX_ACCOUNT_ID - Account ID to trade on (optional, auto-selects first if not set)
"""

import asyncio
import os
import sys
import logging
from pathlib import Path

# Add project root to path (parent of servers/)
sys.path.insert(0, str(Path(__file__).parent.parent))

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger(__name__)

def check_requirements():
    """Check if required packages are installed."""
    try:
        import aiohttp
        logger.info("✅ aiohttp installed")
    except ImportError:
        logger.error("❌ aiohttp not installed. Run: pip install aiohttp>=3.9.0")
        sys.exit(1)
    
    try:
        import psycopg2
        logger.info("✅ psycopg2 installed")
    except ImportError:
        logger.warning("⚠️  psycopg2 not installed. Database caching will be disabled.")
        logger.warning("   Run: pip install psycopg2-binary>=2.9.0")


def main():
    """Main entry point."""
    logger.info("=" * 60)
    logger.info("🚀 ASYNC WEBHOOK SERVER STARTUP")
    logger.info("=" * 60)
    
    # Check requirements
    check_requirements()
    
    # Load environment
    try:
        import load_env  # noqa: F401 (side-effect: loads .env automatically)
        logger.info("✅ Environment loaded")
    except Exception as e:
        logger.warning(f"⚠️  Failed to load .env file: {e}")
    
    # Verify credentials
    api_key = os.getenv('PROJECT_X_API_KEY') or os.getenv('TOPSETPX_API_KEY')
    username = os.getenv('PROJECT_X_USERNAME') or os.getenv('TOPSETPX_USERNAME')
    
    if not api_key or not username:
        logger.error("❌ Missing credentials!")
        logger.error("   Set PROJECT_X_API_KEY and PROJECT_X_USERNAME in environment")
        sys.exit(1)
    
    # Show configuration
    host = os.getenv('WEBHOOK_HOST', '0.0.0.0')
    port = int(os.getenv('WEBHOOK_PORT', '8080'))
    account_id = os.getenv('TOPSTEPX_ACCOUNT_ID', 'auto-select')
    
    logger.info(f"📝 Configuration:")
    logger.info(f"   Host: {host}")
    logger.info(f"   Port: {port}")
    logger.info(f"   Username: {username}")
    logger.info(f"   Account ID: {account_id}")
    logger.info("=" * 60)
    
    # Import and run async server
    try:
        from servers.async_webhook_server import main as async_main
        asyncio.run(async_main())
    except KeyboardInterrupt:
        logger.info("\n🛑 Received shutdown signal")
        logger.info("✅ Async webhook server stopped")
    except Exception as e:
        logger.error(f"❌ Server error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()

