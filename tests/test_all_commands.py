#!/usr/bin/env python3
"""
Comprehensive test suite for all trading bot commands.

This script tests all available commands in trading_bot.py, measures execution times,
and reports results. It's designed to be run in a test environment with mock data
or against a real API (with appropriate credentials).

Usage:
    python tests/test_all_commands.py [--account-id ACCOUNT_ID] [--symbol SYMBOL]
"""

import os
import sys
import asyncio
import time
import json
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from trading_bot import TopStepXTradingBot
import logging

# Configure logging for tests
logging.basicConfig(
    level=logging.WARNING,  # Only show warnings/errors during tests
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class CommandTester:
    """Test all trading bot commands and measure performance."""
    
    def __init__(self, bot: TopStepXTradingBot, account_id: Optional[str] = None, test_symbol: str = "MNQ"):
        self.bot = bot
        self.account_id = account_id
        self.test_symbol = test_symbol.upper()
        self.results: List[Dict[str, Any]] = []
        self.errors: List[Dict[str, Any]] = []
        
    async def run_test(self, command_name: str, test_func, *args, **kwargs) -> Dict[str, Any]:
        """Run a single test and measure execution time."""
        start_time = time.time()
        success = False
        error_msg = None
        result_data = None
        
        try:
            result = await test_func(*args, **kwargs)
            success = True
            result_data = result
            if isinstance(result, dict) and "error" in result:
                success = False
                error_msg = result.get("error", "Unknown error")
        except Exception as e:
            error_msg = str(e)
            logger.debug(f"Test {command_name} failed: {e}", exc_info=True)
        
        elapsed_ms = (time.time() - start_time) * 1000
        
        result = {
            "command": command_name,
            "success": success,
            "elapsed_ms": round(elapsed_ms, 2),
            "error": error_msg,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        
        if success:
            self.results.append(result)
        else:
            self.errors.append(result)
        
        return result
    
    # Test functions for each command
    async def test_contracts(self):
        """Test: contracts"""
        return await self.bot.get_available_contracts(use_cache=True)
    
    async def test_accounts(self):
        """Test: accounts"""
        return await self.bot.list_accounts()
    
    async def test_account_info(self):
        """Test: account_info"""
        if not self.account_id:
            return {"error": "No account ID provided"}
        return await self.bot.get_account_info(self.account_id)
    
    async def test_account_balance(self):
        """Test: account balance"""
        if not self.account_id:
            return {"error": "No account ID provided"}
        balance = await self.bot.get_account_balance(self.account_id)
        return {"balance": balance} if balance else {"error": "Could not fetch balance"}
    
    async def test_quote(self):
        """Test: quote <symbol>"""
        return await self.bot.get_market_quote(self.test_symbol)
    
    async def test_depth(self):
        """Test: depth <symbol>"""
        return await self.bot.get_market_depth(self.test_symbol)
    
    async def test_positions(self):
        """Test: positions"""
        if not self.account_id:
            return {"error": "No account ID provided"}
        return await self.bot.get_open_positions(self.account_id)
    
    async def test_orders(self):
        """Test: orders"""
        if not self.account_id:
            return {"error": "No account ID provided"}
        return await self.bot.get_open_orders(self.account_id)
    
    async def test_history(self):
        """Test: history <symbol> <timeframe> <limit>"""
        return await self.bot.get_historical_data(
            symbol=self.test_symbol,
            timeframe="5m",
            limit=10
        )
    
    async def test_compliance(self):
        """Test: compliance"""
        if not self.account_id:
            return {"error": "No account ID provided"}
        # This would call a compliance check method if it exists
        # For now, we'll test account_info which includes compliance data
        return await self.bot.get_account_info(self.account_id)
    
    async def test_risk(self):
        """Test: risk"""
        if not self.account_id:
            return {"error": "No account ID provided"}
        # Similar to compliance
        return await self.bot.get_account_info(self.account_id)
    
    async def test_trades(self):
        """Test: trades"""
        if not self.account_id:
            return {"error": "No account ID provided"}
        return await self.bot.get_order_history(
            account_id=self.account_id,
            limit=10
        )
    
    async def run_all_tests(self) -> Dict[str, Any]:
        """Run all command tests."""
        print("\n" + "="*70)
        print("TRADING BOT COMMAND TEST SUITE")
        print("="*70)
        print(f"Test Symbol: {self.test_symbol}")
        print(f"Account ID: {self.account_id or 'Not provided (some tests will skip)'}")
        print("="*70 + "\n")
        
        # Define all test commands
        tests = [
            ("contracts", self.test_contracts),
            ("accounts", self.test_accounts),
            ("account_info", self.test_account_info),
            ("account_balance", self.test_account_balance),
            ("quote", self.test_quote),
            ("depth", self.test_depth),
            ("positions", self.test_positions),
            ("orders", self.test_orders),
            ("history", self.test_history),
            ("compliance", self.test_compliance),
            ("risk", self.test_risk),
            ("trades", self.test_trades),
        ]
        
        # Run tests
        total_start = time.time()
        for command_name, test_func in tests:
            print(f"Testing: {command_name:20s} ... ", end="", flush=True)
            result = await self.run_test(command_name, test_func)
            
            if result["success"]:
                print(f"✅ {result['elapsed_ms']:7.2f} ms")
            else:
                print(f"❌ {result['elapsed_ms']:7.2f} ms - {result['error']}")
        
        total_elapsed = (time.time() - total_start) * 1000
        
        # Generate report
        return self.generate_report(total_elapsed)
    
    def generate_report(self, total_elapsed_ms: float) -> Dict[str, Any]:
        """Generate test report."""
        total_tests = len(self.results) + len(self.errors)
        success_count = len(self.results)
        error_count = len(self.errors)
        success_rate = (success_count / total_tests * 100) if total_tests > 0 else 0
        
        # Calculate statistics
        if self.results:
            elapsed_times = [r["elapsed_ms"] for r in self.results]
            avg_time = sum(elapsed_times) / len(elapsed_times)
            min_time = min(elapsed_times)
            max_time = max(elapsed_times)
        else:
            avg_time = min_time = max_time = 0
        
        # Find slowest and fastest commands
        slowest = max(self.results, key=lambda x: x["elapsed_ms"]) if self.results else None
        fastest = min(self.results, key=lambda x: x["elapsed_ms"]) if self.results else None
        
        report = {
            "summary": {
                "total_tests": total_tests,
                "successful": success_count,
                "failed": error_count,
                "success_rate": round(success_rate, 2),
                "total_elapsed_ms": round(total_elapsed_ms, 2),
                "avg_time_ms": round(avg_time, 2),
                "min_time_ms": round(min_time, 2),
                "max_time_ms": round(max_time, 2),
            },
            "results": self.results,
            "errors": self.errors,
            "slowest": slowest,
            "fastest": fastest,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        
        return report
    
    def print_report(self, report: Dict[str, Any]):
        """Print formatted test report."""
        print("\n" + "="*70)
        print("TEST REPORT")
        print("="*70)
        
        summary = report["summary"]
        print(f"\n📊 Summary:")
        print(f"   Total Tests:     {summary['total_tests']}")
        print(f"   Successful:      {summary['successful']} ✅")
        print(f"   Failed:          {summary['failed']} ❌")
        print(f"   Success Rate:    {summary['success_rate']:.1f}%")
        print(f"\n⏱️  Performance:")
        print(f"   Total Time:      {summary['total_elapsed_ms']:.2f} ms")
        print(f"   Average Time:    {summary['avg_time_ms']:.2f} ms")
        print(f"   Fastest:         {summary['min_time_ms']:.2f} ms")
        print(f"   Slowest:         {summary['max_time_ms']:.2f} ms")
        
        if report.get("fastest"):
            print(f"\n⚡ Fastest Command:")
            print(f"   {report['fastest']['command']:20s} - {report['fastest']['elapsed_ms']:.2f} ms")
        
        if report.get("slowest"):
            print(f"\n🐌 Slowest Command:")
            print(f"   {report['slowest']['command']:20s} - {report['slowest']['elapsed_ms']:.2f} ms")
        
        if report["errors"]:
            print(f"\n❌ Failed Tests ({len(report['errors'])}):")
            for error in report["errors"]:
                print(f"   {error['command']:20s} - {error['error']}")
        
        print("\n" + "="*70)
        
        # Detailed results table
        if report["results"]:
            print("\n📋 Detailed Results:")
            print(f"{'Command':<20} {'Time (ms)':<12} {'Status':<10}")
            print("-" * 42)
            for result in sorted(report["results"], key=lambda x: x["elapsed_ms"], reverse=True):
                status = "✅" if result["success"] else "❌"
                print(f"{result['command']:<20} {result['elapsed_ms']:>10.2f}   {status}")


async def main():
    """Main test execution."""
    import argparse
    
    parser = argparse.ArgumentParser(description='Test all trading bot commands')
    parser.add_argument('--account-id', type=str, help='Account ID to use for tests')
    parser.add_argument('--symbol', type=str, default='MNQ', help='Symbol to use for tests (default: MNQ)')
    parser.add_argument('--output', type=str, help='Output JSON report file')
    parser.add_argument('--verbose', action='store_true', help='Enable verbose logging')
    
    args = parser.parse_args()
    
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    # Initialize bot
    print("🔧 Initializing trading bot...")
    bot = TopStepXTradingBot()
    
    # Authenticate
    print("🔐 Authenticating...")
    if not await bot.authenticate():
        print("❌ Authentication failed. Please check your API credentials.")
        return 1
    
    print("✅ Authentication successful")
    
    # Get accounts if account_id not provided
    account_id = args.account_id
    if not account_id:
        print("📋 Fetching accounts...")
        accounts = await bot.list_accounts()
        if accounts:
            # Use first account
            account_id = accounts[0].get('id')
            print(f"✅ Using account: {accounts[0].get('name', 'Unknown')} ({account_id})")
            bot.selected_account = accounts[0]
        else:
            print("⚠️  No accounts found - some tests will be skipped")
    
    # Run tests
    tester = CommandTester(bot, account_id=account_id, test_symbol=args.symbol)
    report = await tester.run_all_tests()
    
    # Print report
    tester.print_report(report)
    
    # Save report if requested
    if args.output:
        with open(args.output, 'w') as f:
            json.dump(report, f, indent=2)
        print(f"\n💾 Report saved to: {args.output}")
    
    # Return exit code based on success rate
    if report["summary"]["success_rate"] >= 80:
        return 0
    elif report["summary"]["success_rate"] >= 50:
        return 1
    else:
        return 2


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)

