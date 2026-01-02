#!/usr/bin/env python3
"""
Manual test script to force overnight range strategy execution.
Useful for testing outside of market open hours.
"""

import sys
import os
import asyncio
import logging
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

from trading_bot import TopStepXTradingBot

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)


async def main():
    """Manually execute overnight range strategy."""
    
    # Get credentials
    api_key = os.getenv('PROJECT_X_API_KEY') or os.getenv('TOPSETPX_API_KEY')
    username = os.getenv('PROJECT_X_USERNAME') or os.getenv('TOPSETPX_USERNAME')
    
    if not api_key or not username:
        logger.error("❌ Missing API credentials")
        sys.exit(1)
    
    # Initialize trading bot
    logger.info("🤖 Initializing trading bot...")
    trading_bot = TopStepXTradingBot(api_key=api_key, username=username)
    
    # Authenticate
    if not await trading_bot._ensure_valid_token():
        logger.error("❌ Authentication failed")
        return
    
    # Select account (index 1 = PRAC account)
    accounts = await trading_bot.list_accounts()
    if not accounts or len(accounts) < 1:
        logger.error("❌ No accounts available")
        return
    
    selected = accounts[0]  # First account (index 0 in list = --account_select=1)
    await trading_bot.switch_account(str(selected['id']))
    logger.info(f"✅ Selected account: {selected['name']}")
    
    # Load contracts
    contracts = await trading_bot.get_available_contracts()
    logger.info(f"✅ Loaded {len(contracts)} contracts")
    
    # Get overnight range strategy
    if not hasattr(trading_bot, 'strategy_manager'):
        logger.error("❌ Strategy manager not available")
        return
    
    strategy = trading_bot.strategy_manager.strategies.get('overnight_range')
    if not strategy:
        logger.error("❌ Overnight range strategy not found")
        logger.info(f"Available strategies: {list(trading_bot.strategy_manager.strategies.keys())}")
        return
    
    logger.info("🎯 Found overnight range strategy")
    
    # Manually execute market open sequence
    symbols = ['MNQ', 'MGC', 'MES']
    logger.info(f"🔔 Manually executing market open sequence for: {', '.join(symbols)}")
    
    try:
        await strategy._execute_market_open_sequence(symbols=symbols)
        logger.info("✅ Market open sequence completed!")
    except Exception as e:
        logger.error(f"❌ Error executing market open sequence: {e}")
        import traceback
        traceback.print_exc()


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("👋 Stopped by user")
    except Exception as e:
        logger.error(f"❌ Fatal error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

