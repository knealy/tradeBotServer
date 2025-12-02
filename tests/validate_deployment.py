#!/usr/bin/env python3
"""
Deployment Validation Script for FIXED Trading Bot
Validates that all critical fixes are properly deployed
"""

import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime

# Add the current directory to Python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from trading_bot import TopStepXTradingBot
from servers.webhook_server import WebhookServer

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class DeploymentValidator:
    """Validate the fixed trading bot deployment"""
    
    def __init__(self):
        self.trading_bot = None
        self.webhook_server = None
        self.validation_results = {}
        
    async def setup(self):
        """Setup the trading bot and webhook server"""
        try:
            # Initialize trading bot
            self.trading_bot = TopStepXTradingBot()
            
            # Authenticate
            auth_result = await self.trading_bot.authenticate()
            if not auth_result:
                logger.error("❌ Failed to authenticate with TopStepX")
                return False
                
            logger.info("✅ Successfully authenticated with TopStepX")
            
            # Initialize webhook server with fixed settings
            self.webhook_server = WebhookServer(
                trading_bot=self.trading_bot,
                host="localhost",
                port=8080,
                account_id=os.getenv('TOPSTEPX_ACCOUNT_ID', '11481693'),
                position_size=int(os.getenv('POSITION_SIZE', '6')),
                close_entire_position_at_tp1=False  # Use staged exits
            )
            
            logger.info("✅ Webhook server initialized with fixed settings")
            return True
            
        except Exception as e:
            logger.error(f"❌ Setup failed: {e}")
            return False
    
    def validate_environment_variables(self):
        """Validate environment variables are set correctly"""
        logger.info("\n🔍 Validating Environment Variables...")
        
        required_vars = {
            'TOPSETPX_USERNAME': 'TopStepX username',
            'TOPSETPX_PASSWORD': 'TopStepX password',
            'TOPSTEPX_ACCOUNT_ID': 'TopStepX account ID'
        }
        
        fixed_vars = {
            'POSITION_SIZE': '3',
            'MAX_POSITION_SIZE': '6',
            'IGNORE_NON_ENTRY_SIGNALS': 'true',
            'IGNORE_TP1_SIGNALS': 'true',
            'DEBOUNCE_SECONDS': '300'
        }
        
        results = {}
        
        # Check required variables
        for var, description in required_vars.items():
            value = os.getenv(var)
            if value:
                logger.info(f"✅ {var}: Set ({description})")
                results[var] = True
            else:
                logger.error(f"❌ {var}: Missing ({description})")
                results[var] = False
        
        # Check fixed system variables
        for var, expected in fixed_vars.items():
            value = os.getenv(var, expected)
            if value == expected:
                logger.info(f"✅ {var}: {value} (correct)")
                results[var] = True
            else:
                logger.warning(f"⚠️ {var}: {value} (expected: {expected})")
                results[var] = value == expected
        
        self.validation_results['environment'] = results
        return all(results.values())
    
    def validate_webhook_server_config(self):
        """Validate webhook server configuration"""
        logger.info("\n🔍 Validating Webhook Server Configuration...")
        
        results = {}
        
        # Check position size
        position_size = self.webhook_server.position_size
        if position_size == 3:
            logger.info(f"✅ Position size: {position_size} (correct)")
            results['position_size'] = True
        else:
            logger.warning(f"⚠️ Position size: {position_size} (expected: 3)")
            results['position_size'] = False
        
        # Check max position size
        max_position_size = self.webhook_server.max_position_size
        if max_position_size == 6:
            logger.info(f"✅ Max position size: {max_position_size} (correct)")
            results['max_position_size'] = True
        else:
            logger.warning(f"⚠️ Max position size: {max_position_size} (expected: 6)")
            results['max_position_size'] = False
        
        # Check debounce settings
        debounce_seconds = self.webhook_server.debounce_seconds
        if debounce_seconds >= 300:
            logger.info(f"✅ Debounce seconds: {debounce_seconds} (sufficient)")
            results['debounce'] = True
        else:
            logger.warning(f"⚠️ Debounce seconds: {debounce_seconds} (should be >= 300)")
            results['debounce'] = False
        
        # Check close entire at TP1 setting
        close_entire = self.webhook_server.close_entire_position_at_tp1
        if not close_entire:
            logger.info(f"✅ Close entire at TP1: {close_entire} (staged exits enabled)")
            results['staged_exits'] = True
        else:
            logger.warning(f"⚠️ Close entire at TP1: {close_entire} (staged exits disabled)")
            results['staged_exits'] = False
        
        self.validation_results['webhook_config'] = results
        return all(results.values())
    
    def validate_signal_filtering(self):
        """Validate signal filtering logic"""
        logger.info("\n🔍 Validating Signal Filtering Logic...")
        
        results = {}
        
        # Check if signal filtering is enabled
        ignore_non_entry = os.getenv('IGNORE_NON_ENTRY_SIGNALS', 'true').lower() in ('true','1','yes','on')
        ignore_tp1 = os.getenv('IGNORE_TP1_SIGNALS', 'true').lower() in ('true','1','yes','on')
        
        if ignore_non_entry:
            logger.info("✅ IGNORE_NON_ENTRY_SIGNALS: true (non-entry signals will be ignored)")
            results['ignore_non_entry'] = True
        else:
            logger.warning("⚠️ IGNORE_NON_ENTRY_SIGNALS: false (non-entry signals will be processed)")
            results['ignore_non_entry'] = False
        
        if ignore_tp1:
            logger.info("✅ IGNORE_TP1_SIGNALS: true (TP1 signals will be ignored)")
            results['ignore_tp1'] = True
        else:
            logger.warning("⚠️ IGNORE_TP1_SIGNALS: false (TP1 signals will be processed)")
            results['ignore_tp1'] = False
        
        self.validation_results['signal_filtering'] = results
        return all(results.values())
    
    def validate_bracket_order_methods(self):
        """Validate bracket order methods exist"""
        logger.info("\n🔍 Validating Bracket Order Methods...")
        
        results = {}
        
        # Check if partial TP bracket order method exists
        if hasattr(self.trading_bot, 'create_partial_tp_bracket_order'):
            logger.info("✅ create_partial_tp_bracket_order method exists")
            results['partial_tp_method'] = True
        else:
            logger.error("❌ create_partial_tp_bracket_order method not found")
            results['partial_tp_method'] = False
        
        # Check if bracket monitoring methods exist
        if hasattr(self.trading_bot, 'monitor_all_bracket_positions'):
            logger.info("✅ monitor_all_bracket_positions method exists")
            results['bracket_monitoring'] = True
        else:
            logger.error("❌ monitor_all_bracket_positions method not found")
            results['bracket_monitoring'] = False
        
        self.validation_results['bracket_methods'] = results
        return all(results.values())
    
    async def validate_position_management(self):
        """Validate position management logic"""
        logger.info("\n🔍 Validating Position Management...")
        
        try:
            # Get current positions
            positions = await self.trading_bot.get_open_positions()
            logger.info(f"Current positions: {len(positions)}")
            
            # Check position size limits
            max_position_size = int(os.getenv('MAX_POSITION_SIZE', '6'))
            symbol_totals = {}
            
            for pos in positions:
                symbol = pos.get('contractId', 'UNKNOWN')
                quantity = pos.get('quantity', 0)
                symbol_totals[symbol] = symbol_totals.get(symbol, 0) + abs(quantity)
            
            results = {}
            for symbol, total in symbol_totals.items():
                if total <= max_position_size:
                    logger.info(f"✅ {symbol}: {total} contracts (within limit)")
                    results[symbol] = True
                else:
                    logger.warning(f"⚠️ {symbol}: {total} contracts (exceeds limit of {max_position_size})")
                    results[symbol] = False
            
            self.validation_results['position_management'] = results
            return all(results.values()) if results else True
            
        except Exception as e:
            logger.error(f"❌ Position management validation failed: {e}")
            return False
    
    def generate_validation_report(self):
        """Generate comprehensive validation report"""
        logger.info("\n" + "="*60)
        logger.info("📊 DEPLOYMENT VALIDATION REPORT")
        logger.info("="*60)
        
        total_checks = 0
        passed_checks = 0
        
        for category, results in self.validation_results.items():
            logger.info(f"\n📋 {category.upper().replace('_', ' ')}:")
            for check, result in results.items():
                total_checks += 1
                if result:
                    passed_checks += 1
                    logger.info(f"  ✅ {check}")
                else:
                    logger.info(f"  ❌ {check}")
        
        logger.info(f"\n📈 SUMMARY:")
        logger.info(f"  Total Checks: {total_checks}")
        logger.info(f"  Passed: {passed_checks}")
        logger.info(f"  Failed: {total_checks - passed_checks}")
        logger.info(f"  Success Rate: {(passed_checks/total_checks)*100:.1f}%")
        
        if passed_checks == total_checks:
            logger.info("\n🎉 ALL VALIDATIONS PASSED! The fixed system is ready for deployment.")
            return True
        else:
            logger.warning(f"\n⚠️ {total_checks - passed_checks} VALIDATIONS FAILED. Please review the issues above.")
            return False
    
    async def run_all_validations(self):
        """Run all validations"""
        logger.info("🚀 Starting Deployment Validation...")
        logger.info("="*60)
        
        # Setup
        if not await self.setup():
            logger.error("❌ Setup failed. Cannot proceed with validation.")
            return False
        
        # Run validations
        validations = [
            ("Environment Variables", self.validate_environment_variables),
            ("Webhook Server Config", self.validate_webhook_server_config),
            ("Signal Filtering", self.validate_signal_filtering),
            ("Bracket Order Methods", self.validate_bracket_order_methods),
            ("Position Management", self.validate_position_management)
        ]
        
        for validation_name, validation_func in validations:
            try:
                if asyncio.iscoroutinefunction(validation_func):
                    result = await validation_func()
                else:
                    result = validation_func()
                
                if result:
                    logger.info(f"✅ {validation_name}: PASSED")
                else:
                    logger.warning(f"⚠️ {validation_name}: ISSUES FOUND")
            except Exception as e:
                logger.error(f"❌ {validation_name}: ERROR - {e}")
        
        # Generate report
        return self.generate_validation_report()

async def main():
    """Main validation function"""
    validator = DeploymentValidator()
    success = await validator.run_all_validations()
    return success

if __name__ == "__main__":
    # Load environment variables
    import load_env  # noqa: F401 (side-effect: loads .env automatically)
    
    # Run validations
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
