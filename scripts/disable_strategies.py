#!/usr/bin/env python3
"""
Script to disable strategies in the database.

Usage:
    python scripts/disable_strategies.py mean_reversion trend_following
    python scripts/disable_strategies.py --all
"""

import sys
import os
import asyncio
import logging
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from infrastructure.database import get_database
from datetime import datetime, timezone

from core.logging_setup import configure_logging
configure_logging()
logger = logging.getLogger(__name__)


async def disable_strategies(strategy_names: list, account_id: str = None):
    """Disable strategies in the database."""
    db = get_database()
    
    if not db:
        logger.error("❌ Database not available")
        return False
    
    # If no account_id provided, try to get from environment or use a default
    if not account_id:
        account_id = os.getenv('TOPSTEPX_ACCOUNT_ID')
        if not account_id:
            logger.error("❌ No account_id provided and TOPSTEPX_ACCOUNT_ID not set")
            logger.info("💡 Usage: python scripts/disable_strategies.py <strategy1> <strategy2> [--account_id=ID]")
            return False
    
    logger.info(f"📋 Disabling strategies for account {account_id}: {', '.join(strategy_names)}")
    
    success_count = 0
    for strategy_name in strategy_names:
        try:
            result = db.save_strategy_state(
                account_id=account_id,
                strategy_name=strategy_name,
                enabled=False,
                last_stopped=datetime.now(timezone.utc)
            )
            if result:
                logger.info(f"✅ Disabled strategy: {strategy_name}")
                success_count += 1
            else:
                logger.error(f"❌ Failed to disable strategy: {strategy_name}")
        except Exception as e:
            logger.error(f"❌ Error disabling {strategy_name}: {e}")
    
    logger.info(f"📊 Disabled {success_count}/{len(strategy_names)} strategies")
    return success_count == len(strategy_names)


async def main():
    """Main entry point."""
    args = sys.argv[1:]
    
    if not args:
        logger.error("❌ No strategies specified")
        logger.info("💡 Usage: python scripts/disable_strategies.py <strategy1> <strategy2> [--account_id=ID]")
        logger.info("💡 Example: python scripts/disable_strategies.py mean_reversion trend_following")
        sys.exit(1)
    
    # Parse arguments
    strategy_names = []
    account_id = None
    
    for arg in args:
        if arg.startswith('--account_id='):
            account_id = arg.split('=', 1)[1]
        elif arg == '--all':
            strategy_names = ['mean_reversion', 'trend_following', 'overnight_range']
        elif not arg.startswith('--'):
            strategy_names.append(arg)
    
    if not strategy_names:
        logger.error("❌ No strategy names provided")
        sys.exit(1)
    
    success = await disable_strategies(strategy_names, account_id)
    sys.exit(0 if success else 1)


if __name__ == '__main__':
    asyncio.run(main())
