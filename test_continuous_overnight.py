#!/usr/bin/env python3
"""
Test continuous overnight range monitoring.
Verifies that the strategy calculates ranges immediately and monitors continuously.
"""

import sys
import os
import asyncio
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from trading_bot import TopStepXTradingBot

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)


async def main():
    """Test continuous overnight range monitoring."""
    
    api_key = os.getenv('PROJECT_X_API_KEY') or os.getenv('TOPSETPX_API_KEY')
    username = os.getenv('PROJECT_X_USERNAME') or os.getenv('TOPSETPX_USERNAME')
    
    if not api_key or not username:
        logger.error("❌ Missing API credentials")
        sys.exit(1)
    
    logger.info("🤖 Initializing trading bot...")
    trading_bot = TopStepXTradingBot(api_key=api_key, username=username)
    
    if not await trading_bot._ensure_valid_token():
        logger.error("❌ Authentication failed")
        return
    
    accounts = await trading_bot.list_accounts()
    if not accounts:
        logger.error("❌ No accounts available")
        return
    
    await trading_bot.switch_account(str(accounts[0]['id']))
    logger.info(f"✅ Selected account: {accounts[0]['name']}")
    
    contracts = await trading_bot.get_available_contracts()
    logger.info(f"✅ Loaded {len(contracts)} contracts")
    
    if not hasattr(trading_bot, 'strategy_manager'):
        logger.error("❌ Strategy manager not available")
        return
    
    strategy = trading_bot.strategy_manager.strategies.get('overnight_range')
    if not strategy:
        logger.error("❌ Overnight range strategy not found")
        return
    
    logger.info("🎯 Starting overnight range strategy in CONTINUOUS mode...")
    logger.info("   This will:")
    logger.info("   1. Calculate overnight ranges immediately")
    logger.info("   2. Calculate breakout levels")
    logger.info("   3. Monitor price continuously")
    logger.info("   4. Place orders when price gets within threshold")
    logger.info("")
    
    # Start strategy with MNQ
    await strategy.start(symbols=['MNQ'])
    
    # Let it run for 30 seconds to see monitoring in action
    logger.info("⏰ Monitoring for 30 seconds...")
    await asyncio.sleep(30)
    
    # Check if breakout levels were calculated
    if strategy.breakout_levels:
        logger.info("✅ Breakout levels are active:")
        for symbol, levels in strategy.breakout_levels.items():
            logger.info(f"   {symbol}:")
            if 'BUY' in levels:
                logger.info(f"     LONG: Entry={levels['BUY'].entry_price:.2f}")
            if 'SELL' in levels:
                logger.info(f"     SHORT: Entry={levels['SELL'].entry_price:.2f}")
    else:
        logger.warning("⚠️  No breakout levels calculated")
    
    # Stop strategy
    await strategy.stop()
    logger.info("✅ Test complete!")


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

