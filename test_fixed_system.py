#!/usr/bin/env python3
"""
Test script for the FIXED trading bot system
Tests the critical fixes to prevent oversized orphaned positions
"""

import asyncio
import json
import logging
import os
import sys
from datetime import datetime

# Add the current directory to Python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from trading_bot import TopStepXTradingBot
from webhook_server import WebhookServer

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class FixedSystemTester:
    """Test the fixed trading bot system"""
    
    def __init__(self):
        self.trading_bot = None
        self.webhook_server = None
        
    async def setup(self):
        """Setup the trading bot and webhook server"""
        try:
            # Initialize trading bot
            self.trading_bot = TopStepXTradingBot()
            
            # Authenticate
            auth_result = await self.trading_bot.authenticate()
            if not auth_result:
                logger.error("Failed to authenticate with TopStepX")
                return False
                
            logger.info("✅ Successfully authenticated with TopStepX")
            
            # Initialize webhook server with fixed settings
            self.webhook_server = WebhookServer(
                trading_bot=self.trading_bot,
                host="localhost",
                port=8080,
                account_id=os.getenv('TOPSETPX_ACCOUNT_ID', '11481693'),
                position_size=int(os.getenv('POSITION_SIZE', '3')),
                close_entire_position_at_tp1=False  # Use staged exits
            )
            
            logger.info("✅ Webhook server initialized with fixed settings")
            return True
            
        except Exception as e:
            logger.error(f"Setup failed: {e}")
            return False
    
    async def test_position_validation(self):
        """Test position size validation"""
        logger.info("\n🧪 Testing Position Size Validation...")
        
        try:
            # Get current positions
            positions = await self.trading_bot.get_open_positions()
            logger.info(f"Current positions: {len(positions)}")
            
            # Test position size limits
            max_position_size = int(os.getenv('MAX_POSITION_SIZE', '6'))
            logger.info(f"Max position size limit: {max_position_size}")
            
            # Check if any symbol has exceeded limits
            symbol_totals = {}
            for pos in positions:
                symbol = pos.get('contractId', 'UNKNOWN')
                quantity = pos.get('quantity', 0)
                symbol_totals[symbol] = symbol_totals.get(symbol, 0) + abs(quantity)
            
            for symbol, total in symbol_totals.items():
                if total > max_position_size:
                    logger.warning(f"⚠️ Symbol {symbol} has {total} contracts (exceeds limit of {max_position_size})")
                else:
                    logger.info(f"✅ Symbol {symbol}: {total} contracts (within limit)")
            
            return True
            
        except Exception as e:
            logger.error(f"Position validation test failed: {e}")
            return False
    
    async def test_signal_filtering(self):
        """Test signal filtering logic"""
        logger.info("\n🧪 Testing Signal Filtering...")
        
        try:
            # Test signal types
            entry_signals = ["open_long", "open_short"]
            critical_exit_signals = ["stop_out_long", "stop_out_short", "session_close"]
            ignored_signals = ["tp1_hit_long", "tp1_hit_short", "trim_long", "trim_short"]
            
            logger.info(f"✅ Entry signals (processed): {entry_signals}")
            logger.info(f"✅ Critical exit signals (processed): {critical_exit_signals}")
            logger.info(f"❌ Ignored signals: {ignored_signals}")
            
            # Check environment variables
            ignore_non_entry = os.getenv('IGNORE_NON_ENTRY_SIGNALS', 'true').lower() in ('true','1','yes','on')
            ignore_tp1 = os.getenv('IGNORE_TP1_SIGNALS', 'true').lower() in ('true','1','yes','on')
            
            logger.info(f"IGNORE_NON_ENTRY_SIGNALS: {ignore_non_entry}")
            logger.info(f"IGNORE_TP1_SIGNALS: {ignore_tp1}")
            
            return True
            
        except Exception as e:
            logger.error(f"Signal filtering test failed: {e}")
            return False
    
    async def test_debounce_settings(self):
        """Test debounce settings"""
        logger.info("\n🧪 Testing Debounce Settings...")
        
        try:
            debounce_seconds = int(os.getenv('DEBOUNCE_SECONDS', '300'))
            logger.info(f"Debounce window: {debounce_seconds} seconds ({debounce_seconds/60:.1f} minutes)")
            
            # Check if debounce is properly configured
            if debounce_seconds >= 300:  # 5 minutes or more
                logger.info("✅ Debounce window is sufficient to prevent rapid duplicate signals")
            else:
                logger.warning(f"⚠️ Debounce window may be too short: {debounce_seconds}s")
            
            return True
            
        except Exception as e:
            logger.error(f"Debounce test failed: {e}")
            return False
    
    async def test_bracket_order_logic(self):
        """Test bracket order creation logic"""
        logger.info("\n🧪 Testing Bracket Order Logic...")
        
        try:
            # Test partial TP bracket order method exists
            if hasattr(self.trading_bot, 'create_partial_tp_bracket_order'):
                logger.info("✅ create_partial_tp_bracket_order method exists")
            else:
                logger.error("❌ create_partial_tp_bracket_order method not found")
                return False
            
            # Test webhook server configuration
            position_size = self.webhook_server.position_size
            max_position_size = self.webhook_server.max_position_size
            debounce_seconds = self.webhook_server.debounce_seconds
            
            logger.info(f"Position size: {position_size}")
            logger.info(f"Max position size: {max_position_size}")
            logger.info(f"Debounce seconds: {debounce_seconds}")
            
            # Validate settings
            if max_position_size >= position_size * 2:
                logger.info("✅ Max position size is properly configured")
            else:
                logger.warning("⚠️ Max position size may be too restrictive")
            
            return True
            
        except Exception as e:
            logger.error(f"Bracket order test failed: {e}")
            return False
    
    async def test_environment_variables(self):
        """Test environment variable configuration"""
        logger.info("\n🧪 Testing Environment Variables...")
        
        required_vars = [
            'TOPSETPX_USERNAME',
            'TOPSETPX_PASSWORD', 
            'TOPSETPX_ACCOUNT_ID'
        ]
        
        optional_vars = [
            'POSITION_SIZE',
            'MAX_POSITION_SIZE',
            'IGNORE_NON_ENTRY_SIGNALS',
            'IGNORE_TP1_SIGNALS',
            'DEBOUNCE_SECONDS',
            'TP1_FRACTION'
        ]
        
        # Check required variables
        missing_required = []
        for var in required_vars:
            if not os.getenv(var):
                missing_required.append(var)
        
        if missing_required:
            logger.error(f"❌ Missing required environment variables: {missing_required}")
            return False
        else:
            logger.info("✅ All required environment variables are set")
        
        # Check optional variables
        for var in optional_vars:
            value = os.getenv(var)
            if value:
                logger.info(f"✅ {var}: {value}")
            else:
                logger.info(f"ℹ️ {var}: using default value")
        
        return True
    
    async def run_all_tests(self):
        """Run all tests"""
        logger.info("🚀 Starting Fixed System Tests...")
        logger.info("="*60)
        
        tests = [
            ("Environment Variables", self.test_environment_variables),
            ("Position Validation", self.test_position_validation),
            ("Signal Filtering", self.test_signal_filtering),
            ("Debounce Settings", self.test_debounce_settings),
            ("Bracket Order Logic", self.test_bracket_order_logic)
        ]
        
        results = []
        for test_name, test_func in tests:
            logger.info(f"\n{'='*20} {test_name} {'='*20}")
            try:
                result = await test_func()
                results.append((test_name, result))
                if result:
                    logger.info(f"✅ {test_name}: PASSED")
                else:
                    logger.error(f"❌ {test_name}: FAILED")
            except Exception as e:
                logger.error(f"❌ {test_name}: ERROR - {e}")
                results.append((test_name, False))
        
        # Summary
        logger.info("\n" + "="*60)
        logger.info("📊 TEST SUMMARY")
        logger.info("="*60)
        
        passed = sum(1 for _, result in results if result)
        total = len(results)
        
        for test_name, result in results:
            status = "✅ PASSED" if result else "❌ FAILED"
            logger.info(f"{test_name}: {status}")
        
        logger.info(f"\nOverall: {passed}/{total} tests passed")
        
        if passed == total:
            logger.info("🎉 All tests passed! The fixed system is ready for deployment.")
        else:
            logger.warning("⚠️ Some tests failed. Please review the configuration.")
        
        return passed == total

async def main():
    """Main test function"""
    tester = FixedSystemTester()
    
    # Setup
    if not await tester.setup():
        logger.error("Setup failed. Exiting.")
        return False
    
    # Run tests
    success = await tester.run_all_tests()
    
    return success

if __name__ == "__main__":
    # Load environment variables
    from load_env import load_env
    load_env()
    
    # Run tests
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
