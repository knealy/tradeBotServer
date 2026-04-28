"""Interactive CLI loop extracted from trading_bot (plan: shrink god module)."""
from __future__ import annotations

import asyncio
import logging
import os
import readline
from datetime import datetime, timezone, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from trading_bot import TopStepXTradingBot

logger = logging.getLogger(__name__)

def setup_readline_for_bot() -> None:
    """
    Set up readline for command history and arrow key navigation.
    """
    # Set up history file
    history_file = os.path.expanduser("~/.topstepx_trading_history")
    
    # Load existing history
    try:
        readline.read_history_file(history_file)
    except FileNotFoundError:
        pass
    
    # Set history length
    readline.set_history_length(100)
    
    # Set up tab completion for commands
    def completer(text, state):
        # Get current line to understand context
        line = readline.get_line_buffer()
        words = line.split()
        
        # If we're completing the first word (command)
        if len(words) == 1:
            commands = [
                "trade", "limit", "bracket", "native_bracket", "stop", "stop_buy", "stop_sell", "trail",
                "positions", "orders", "close", "cancel", "modify", "modify_stop", "modify_tp", 
                "quote", "depth", "history", "monitor", "bracket_monitor", "account_info", "flatten", 
                "contracts", "accounts", "help", "quit"
            ]
            matches = [cmd for cmd in commands if cmd.startswith(text.lower())]
            if state < len(matches):
                return matches[state]
        
        # If we're completing after a command, suggest common symbols
        elif len(words) >= 2 and words[0] in ["trade", "limit", "bracket", "native_bracket", "stop", "stop_buy", "stop_sell", "trail", "quote", "depth", "history"]:
            symbols = ["MNQ", "MES", "MYM", "MGC", "ES", "NQ", "YM", "GC"]
            matches = [sym for sym in symbols if sym.lower().startswith(text.lower())]
            if state < len(matches):
                return matches[state]
        
        # If we're completing after symbol, suggest sides
        elif len(words) >= 3 and words[1].upper() in ["MNQ", "MES", "MYM", "MGC", "ES", "NQ", "YM", "GC"]:
            sides = ["buy", "sell"]
            matches = [side for side in sides if side.startswith(text.lower())]
            if state < len(matches):
                return matches[state]
        
        return None
    
    readline.set_completer(completer)
    readline.parse_and_bind("tab: complete")
    
    # Save history on exit
    def save_history():
        try:
            readline.write_history_file(history_file)
        except:
            pass
    
    import atexit
    atexit.register(save_history)

async def async_cli_input(prompt: str = "") -> str:
    """Non-blocking input helper so background tasks remain active."""
    return await asyncio.to_thread(input, prompt)

async def run_trading_interface(bot: "TopStepXTradingBot") -> None:
    """
    Interactive trading interface with command history.
    """
    # Set up readline for command history
    setup_readline_for_bot()
    
    print("\n" + "="*50)
    print("TRADING INTERFACE")
    print("="*50)
    print("Commands:")
    print("  trade <symbol> <side> <quantity> - Place market order")
    print("  limit <symbol> <side> <quantity> <price> - Place limit order")
    print("  bracket <symbol> <side> <quantity> <stop_ticks> <profit_ticks> - Place bracket order")
    print("  native_bracket <symbol> <side> <quantity> <stop_price> <profit_price> - Native bracket order")
    print("  stop <symbol> <side> <quantity> <price> - Place stop order")
    print("  trail <symbol> <side> <quantity> <trail_amount> - Place trailing stop")
    print("  positions - Show open positions")
    print("  metrics - Show performance metrics and system stats")
    print("  orders - Show open orders")
    print("  close <position_id> [quantity] - Close position")
    print("  cancel <order_id> - Cancel order")
    print("  modify <order_id> <new_quantity> [new_price] - Modify order")
    print("  quote <symbol> - Get market quote")
    print("  depth <symbol> - Get market depth")
    print("  history <symbol> [timeframe] [limit] [raw] [csv] - Get historical data")
    print("    Add 'raw' for fast tab-separated output (e.g., history MNQ 5m 20 raw)")
    print("    Add 'csv' to export data to CSV file (e.g., history MNQ 5m 20 csv)")
    print("  chart [symbol] [timeframe] [limit] - Open chart window GUI")
    print("    Example: chart MNQ 5m 100")
    print("  monitor - Monitor position changes and adjust bracket orders")
    print("  bracket_monitor - Monitor bracket positions and manage orders")
    print("  activate_monitor - Manually activate monitoring for testing")
    print("  deactivate_monitor - Manually deactivate monitoring")
    print("  check_fills - Check for filled orders and send Discord notifications")
    print("  test_fills - Test fill checking with detailed output")
    print("  clear_notifications - Clear notification cache to re-check all orders")
    print("  auto_fills - Enable automatic fill checking every 30 seconds")
    print("  stop_auto_fills - Disable automatic fill checking")
    print("  account_info - Get detailed account information")
    print("  account_state - Show real-time account state (balance, PnL, positions)")
    print("  compliance - Check account compliance status (DLL, MLL, trailing drawdown)")
    print("  risk - Show current risk metrics and limits")
    print("  drawdown - Show max loss limit and drawdown information (also: max_loss, risk)")
    print("  switch_account [account_id] - Switch to a different account without restarting")
    print("  trades [start_date] [end_date] - List trades between dates (default: current session)")
    print("  flatten - Close all positions and cancel all orders")
    print("  contracts - List available contracts")
    print("  accounts - List accounts again")
    print("  strategy_start [symbols] - Start overnight range breakout strategy (default)")
    print("  strategy_stop - Stop the overnight strategy")
    print("  strategy_status - Show overnight strategy status and configuration")
    print("  strategy_test <symbol> - Test strategy components (ATR, ranges, orders)")
    print("  strategy_execute [symbols] - Manually trigger market open sequence (for testing)")
    print("  ")
    print("  📦 Modular Strategy System:")
    print("  strategies list - List all available strategies")
    print("  strategies status - Show all strategies status")
    print("  strategies start <name> [--symbols=SYM1,SYM2] [--timeframe=TIMEFRAME] - Start a specific strategy")
    print("  strategies stop <name> - Stop a specific strategy")
    print("  strategies start_all - Start all enabled strategies")
    print("  strategies stop_all - Stop all strategies")
    print("  ")
    print("  help - Show this help message")
    print("  quit - Exit trading interface")
    print("="*50)
    print("💡 Use ↑/↓ arrows to navigate command history, Tab for completion")
    print("="*50)
    print("💡 Use 'auto_fills' command to enable automatic fill checking if needed")
    print("="*50)
    
    while True:
        try:
            command = (await async_cli_input("\nEnter command: ")).strip()
            
            # Convert to lowercase for processing but keep original for history
            command_lower = command.lower()
            
            if command_lower == "quit" or command_lower == "q":
                print("👋 Exiting trading interface.")
                break
            elif command_lower == "master" or command_lower.startswith("master ") or command_lower == "gui" or command_lower.startswith("gui "):
                # Open unified master dashboard (chart server + master page) from interactive CLI
                try:
                    from core.cli_command_parser import CLICommandParser
                    parser = CLICommandParser(self)
                    resp = await parser.execute_command(command)
                    if resp.get("success"):
                        result = resp.get("result") or {}
                        if isinstance(result, dict) and result.get("url"):
                            print(f"✅ Master dashboard opened: {result['url']}")
                        else:
                            print("✅ Master dashboard command executed.")
                    else:
                        print(f"❌ Failed to open master dashboard: {resp.get('error')}")
                except Exception as e:
                    print(f"❌ Failed to open master dashboard: {e}")
            elif command_lower == "flatten":
                await bot.flatten_all_positions()
            elif command_lower == "contracts":
                contracts = await bot.get_available_contracts()
                if contracts:
                    print(f"\n📋 Available Contracts ({len(contracts)}):")
                    for contract in contracts:
                        # API returns: name (symbol), description (full name), tickSize, tickValue, activeContract
                        symbol = contract.get('name', 'Unknown')
                        description = contract.get('description', 'No description')
                        tick_size = contract.get('tickSize')
                        tick_value = contract.get('tickValue')
                        active = contract.get('activeContract', False)
                        
                        # Format display with symbol and description
                        status = "✓" if active else "○"
                        desc_short = description[:60] + "..." if len(description) > 60 else description
                        print(f"  {status} {symbol:8s} - {desc_short}")
                        
                        # Show tick info if available
                        if tick_size and tick_value:
                            print(f"    {'':8s}   Tick: ${tick_size:.4f} = ${tick_value:.2f}")
                else:
                    print("❌ No contracts available")
            elif command_lower == "accounts":
                accounts = await bot.list_accounts()
                bot.display_accounts(accounts)
            
            elif command_lower == "contracts":
                print("📋 Fetching available contracts...")
                contracts = await bot.get_available_contracts(use_cache=False)
                if contracts:
                    print(f"\n✅ Found {len(contracts)} available contracts:\n")
                    # Group by symbol prefix
                    by_symbol = {}
                    for c in contracts:
                        symbol = c.get('symbol') or c.get('Symbol') or 'Unknown'
                        contract_id = c.get('contractId') or c.get('ContractId') or c.get('id') or 'Unknown'
                        description = c.get('description') or c.get('Description') or ''
                        
                        if symbol not in by_symbol:
                            by_symbol[symbol] = []
                        by_symbol[symbol].append({'id': contract_id, 'desc': description})
                    
                    # Display grouped by symbol
                    for symbol in sorted(by_symbol.keys()):
                        items = by_symbol[symbol]
                        if len(items) == 1:
                            print(f"  {symbol:8} → {items[0]['id']}")
                            if items[0]['desc']:
                                print(f"           {items[0]['desc']}")
                        else:
                            print(f"  {symbol} ({len(items)} contracts):")
                            for item in items:
                                print(f"    → {item['id']}")
                                if item['desc']:
                                    print(f"      {item['desc']}")
                else:
                    print("❌ No contracts available")
            
            elif command_lower == "metrics":
                # Display performance metrics
                try:
                    metrics_tracker = get_metrics_tracker(db=bot.db)
                    metrics_tracker.print_report()
                    
                    # Also print JSON summary for programmatic access
                    report = metrics_tracker.get_full_report()
                    print(f"\n📊 Summary: {report['api']['total_calls']} API calls, "
                          f"{report['system']['memory_mb']} MB memory, "
                          f"{report['system']['cpu_percent']}% CPU")
                    
                    # Show cache performance if available
                    if report['cache']:
                        for cache_name, metrics in report['cache'].items():
                            print(f"💾 {cache_name}: {metrics['hit_rate']} hit rate")
                except Exception as e:
                    print(f"❌ Failed to display metrics: {e}")
                    logger.error(f"Metrics display error: {e}")
            
            elif command_lower == "switch_account" or command_lower.startswith("switch_account "):
                # Switch to a different account without closing the bot
                accounts = await bot.list_accounts()
                if not accounts:
                    print("❌ No accounts available")
                    continue
                
                # Check if account ID provided as argument
                parts = command.split()
                if len(parts) > 1:
                    # Try to find account by ID or name
                    search_term = parts[1].strip()
                    target_account = None
                    
                    # Try by ID first
                    try:
                        account_id = int(search_term)
                        for acc in accounts:
                            if acc.get('id') == account_id:
                                target_account = acc
                                break
                    except ValueError:
                        # Try by name
                        for acc in accounts:
                            if search_term.lower() in acc.get('name', '').lower():
                                target_account = acc
                                break
                    
                    if target_account:
                        old_account = bot.selected_account
                        bot.selected_account = target_account
                        # Clear account-specific caches
                        bot._cached_order_ids = {}
                        bot._cached_position_ids = {}
                        
                        # Reinitialize account tracker for new account
                        balance = await bot.get_account_balance()
                        if balance:
                            bot.account_tracker.initialize(
                                account_id=target_account['id'],
                                starting_balance=balance,
                                account_type=target_account.get('type', 'unknown')
                            )
                            logger.info(f"Account tracker reinitialized for {target_account.get('name')} (${balance:,.2f})")
                        
                        print(f"✅ Switched from {old_account.get('name', 'N/A')} to {target_account.get('name')}")
                        print(f"   Account ID: {target_account.get('id')}")
                        if balance:
                            print(f"   Balance: ${balance:,.2f}")
                    else:
                        print(f"❌ Account not found: {search_term}")
                        print("   Use 'accounts' to list available accounts")
                else:
                    # Interactive selection
                    print("\n📋 Available Accounts:")
                    bot.display_accounts(accounts)
                    print("\n💡 Enter account number or account ID to switch")
                    choice = input("Switch to account (number/ID or 'c' to cancel): ").strip()
                    
                    if choice.lower() == 'c':
                        print("❌ Account switch cancelled")
                        continue
                    
                    target_account = None
                    # Try by number first
                    try:
                        account_index = int(choice) - 1
                        if 0 <= account_index < len(accounts):
                            target_account = accounts[account_index]
                    except ValueError:
                        # Try by ID
                        try:
                            account_id = int(choice)
                            for acc in accounts:
                                if acc.get('id') == account_id:
                                    target_account = acc
                                    break
                        except ValueError:
                            pass
                    
                    if target_account:
                        old_account = bot.selected_account
                        bot.selected_account = target_account
                        # Clear account-specific caches
                        bot._cached_order_ids = {}
                        bot._cached_position_ids = {}
                        
                        # Reinitialize account tracker for new account
                        balance = await bot.get_account_balance()
                        if balance:
                            bot.account_tracker.initialize(
                                account_id=target_account['id'],
                                starting_balance=balance,
                                account_type=target_account.get('type', 'unknown')
                            )
                            logger.info(f"Account tracker reinitialized for {target_account.get('name')} (${balance:,.2f})")
                        
                        print(f"\n✅ Switched from {old_account.get('name', 'N/A') if old_account else 'None'} to {target_account.get('name')}")
                        print(f"   Account ID: {target_account.get('id')}")
                        if balance:
                            print(f"   Balance: ${balance:,.2f}")
                    else:
                        print(f"❌ Invalid selection: {choice}")
            elif command_lower == "help":
                print("\n" + "="*70)
                print("TRADING INTERFACE - COMPLETE COMMAND REFERENCE")
                print("="*70)
                print()
                print("📊 MARKET DATA COMMANDS:")
                print("  quote <symbol>")
                print("    Get real-time market quote (bid, ask, last, volume)")
                print("    Example: quote MNQ")
                print()
                print("  depth <symbol>")
                print("    Get market depth (order book) with bids and asks")
                print("    Example: depth MNQ")
                print()
                print("  history <symbol> [timeframe] [limit] [raw] [csv]")
                print("    Get historical price data (bars)")
                print("    Timeframes: 1s, 5s, 10s, 15s, 30s, 1m, 2m, 3m, 5m, 10m, 15m, 30m, 1h, 2h, 4h, 1d")
                print("    Examples: history MNQ 5m 100")
                print("              history MNQ 1m 50 raw    (tab-separated output)")
                print("              history MNQ 5m 100 csv (export to CSV file)")
                print()
                print("  chart [symbol] [timeframe] [limit]")
                print("    Open interactive chart window GUI")
                print("    Example: chart MNQ 5m 100")
                print()
                print("  contracts")
                print("    List all available trading contracts with details")
                print()
                print()
                print("💰 ACCOUNT & RISK COMMANDS:")
                print("  account_info")
                print("    Get detailed account information (balance, equity, margin, etc.)")
                print()
                print("  account_state")
                print("    Show real-time account state (balance, PnL, positions summary)")
                print()
                print("  compliance")
                print("    Check account compliance status (DLL, MLL, trailing drawdown)")
                print()
                print("  risk")
                print("    Show current risk metrics and limits")
                print()
                print("  drawdown (also: max_loss)")
                print("    Show max loss limit and drawdown information")
                print()
                print("  trades [start_date] [end_date]")
                print("    List trades with FIFO consolidation and statistics")
                print("    Example: trades 2025-12-01 2025-12-04")
                print()
                print("  accounts")
                print("    List all your trading accounts")
                print()
                print("  switch_account [account_id]")
                print("    Switch to a different account without restarting")
                print("    Example: switch_account 12694476")
                print()
                print("  metrics")
                print("    Show performance metrics and system stats (API calls, cache, memory, CPU)")
                print()
                print()
                print("📈 TRADING ORDER COMMANDS:")
                print("  trade <symbol> <side> <quantity>")
                print("    Place a market order")
                print("    Example: trade MNQ BUY 1")
                print()
                print("  limit <symbol> <side> <quantity> <price>")
                print("    Place a limit order at specified price")
                print("    Example: limit MNQ BUY 1 19500.50")
                print()
                print("  bracket <symbol> <side> <quantity> <stop_ticks> <profit_ticks>")
                print("    Place a bracket order with stop loss and take profit (in ticks)")
                print("    Example: bracket MNQ BUY 1 80 80")
                print()
                print("  native_bracket <symbol> <side> <quantity> <stop_price> <profit_price>")
                print("    Place a native TopStepX bracket order with linked stop/take profit")
                print("    Example: native_bracket MNQ BUY 1 19400.00 19600.00")
                print()
                print("  stop_bracket <symbol> <side> <quantity> <entry_price> <stop_price> <profit_price>")
                print("    Place a stop entry order with stop loss and take profit prices defined")
                print("    Example: stop_bracket MNQ BUY 1 25800.00 25750.00 25900.00")
                print()
                print("  stop_buy <symbol> <quantity> <stop_price>")
                print("    Place a stop buy order (triggers market buy when price reaches stop_price)")
                print("    Example: stop_buy MNQ 1 25900.00")
                print()
                print("  stop_sell <symbol> <quantity> <stop_price>")
                print("    Place a stop sell order (triggers market sell when price reaches stop_price)")
                print("    Example: stop_sell MNQ 1 25900.00")
                print()
                print("  stop <symbol> <side> <quantity> <price>")
                print("    Place a stop order (legacy command, use stop_buy/stop_sell)")
                print("    Example: stop MNQ BUY 1 19400.00")
                print()
                print("  trail <symbol> <side> <quantity> <trail_amount>")
                print("    Place a trailing stop order")
                print("    Example: trail MNQ BUY 1 25.00")
                print()
                print()
                print("📦 POSITION MANAGEMENT COMMANDS:")
                print("  positions")
                print("    Show all open positions with P&L, entry price, current price")
                print()
                print("  orders")
                print("    Show all open orders with status and details")
                print()
                print("  close <position_id> [quantity]")
                print("    Close a position (entire or partial)")
                print("    Example: close 425682864 1")
                print()
                print("  cancel <order_id>")
                print("    Cancel an order")
                print("    Example: cancel 12345")
                print()
                print("  modify <order_id> <new_quantity> [new_price]")
                print("    Modify an existing order")
                print("    Example: modify 12345 2 19500.00")
                print()
                print("  modify_stop <position_id> <new_stop_price>")
                print("    Modify the stop loss order attached to a position")
                print("    Example: modify_stop 425682864 25800.00")
                print()
                print("  modify_tp <position_id> <new_tp_price>")
                print("    Modify the take profit order attached to a position")
                print("    Example: modify_tp 425682864 26100.00")
                print()
                print("  flatten")
                print("    Close all positions and cancel all orders")
                print("    Requires confirmation by typing 'FLATTEN'")
                print()
                print()
                print("🔄 MONITORING & AUTOMATION COMMANDS:")
                print("  monitor")
                print("    Monitor position changes and automatically adjust bracket orders")
                print("    Use this after adding/subtracting contracts to existing positions")
                print()
                print("  bracket_monitor")
                print("    Monitor bracket positions and manage orders")
                print()
                print("  activate_monitor")
                print("    Manually activate monitoring for testing")
                print()
                print("  deactivate_monitor")
                print("    Manually deactivate monitoring")
                print()
                print("  check_fills")
                print("    Manually check for filled orders and send Discord notifications")
                print()
                print("  test_fills")
                print("    Test fill checking with detailed output")
                print()
                print("  auto_fills")
                print("    Enable automatic fill checking every 30 seconds")
                print()
                print("  stop_auto_fills")
                print("    Disable automatic fill checking")
                print()
                print("  clear_notifications")
                print("    Clear notification cache to re-check all orders")
                print()
                print()
                print("🎯 STRATEGY MANAGEMENT COMMANDS:")
                print("  strategies list (or: strategies)")
                print("    List all available strategies")
                print()
                print("  strategies status")
                print("    Show status of all strategies")
                print()
                print("  strategies start <name> [--symbols=SYM1,SYM2] [--timeframe=TIMEFRAME]")
                print("    Start a specific strategy")
                print("    Example: strategies start simple_candle --timeframe=1m --symbols=MNQ")
                print("    Example: strategies start overnight_range --symbols=MNQ,MES")
                print()
                print("  strategies stop <name>")
                print("    Stop a specific strategy")
                print("    Example: strategies stop overnight_range")
                print()
                print("  strategies start_all")
                print("    Start all enabled strategies")
                print()
                print("  strategies stop_all")
                print("    Stop all strategies")
                print()
                print("  strategy_start [symbols]")
                print("    Start overnight range breakout strategy (legacy command)")
                print()
                print("  strategy_stop")
                print("    Stop the overnight strategy (legacy command)")
                print()
                print("  strategy_status")
                print("    Show overnight strategy status and configuration (legacy command)")
                print()
                print("  strategy_test <symbol>")
                print("    Test strategy components (ATR, ranges, orders) (legacy command)")
                print()
                print("  strategy_execute [symbols]")
                print("    Manually trigger market open sequence for testing (legacy command)")
                print()
                print()
                print("⚙️  SYSTEM COMMANDS:")
                print("  help")
                print("    Show this help message")
                print()
                print("  quit (or: q)")
                print("    Exit trading interface")
                print()
                print("="*70)
                print("💡 TIPS:")
                print("   - Use ↑/↓ arrows to navigate command history")
                print("   - Use Tab key for command completion")
                print("   - All commands are case-insensitive")
                print("   - Symbol names are automatically converted to uppercase")
                print("   - Use 'raw' flag with history for fast tab-separated output")
                print("   - Use 'csv' flag with history to export data to CSV file")
                print("="*70)
            elif command_lower.startswith("trade "):
                parts = command.split()
                if len(parts) != 4:
                    print("❌ Usage: trade <symbol> <side> <quantity>")
                    print("   Example: trade MNQ BUY 1")
                    continue
                
                symbol, side, quantity = parts[1], parts[2], parts[3]
                
                try:
                    quantity = int(quantity)
                except ValueError:
                    print("❌ Quantity must be a number")
                    continue
                
                if side.upper() not in ["BUY", "SELL"]:
                    print("❌ Side must be BUY or SELL")
                    continue
                
                # Confirm the trade
                print(f"\n⚠️  CONFIRM TRADE:")
                print(f"   Symbol: {symbol.upper()}")
                print(f"   Side: {side.upper()}")
                print(f"   Quantity: {quantity}")
                print(f"   Account: {bot.selected_account['name']}")
                
                confirm = input("   Confirm? (y/N): ").strip().lower()
                if confirm != 'y':
                    print("❌ Trade cancelled")
                    continue
                
                # Place the order
                result = await bot.place_market_order(symbol, side, quantity)
                if "error" in result:
                    print(f"❌ Order failed: {result['error']}")
                else:
                    print(f"✅ Order placed successfully!")
                    print(f"   Order ID: {result.get('orderId', 'Unknown')}")
                    print(f"   Status: {result.get('status', 'Unknown')}")
            
            elif command_lower.startswith("limit "):
                parts = command.split()
                
                # Check for --reduce-only or -r flag
                reduce_only = False
                if '--reduce-only' in parts:
                    reduce_only = True
                    parts.remove('--reduce-only')
                elif '-r' in parts:
                    reduce_only = True
                    parts.remove('-r')
                
                if len(parts) != 5:
                    print("❌ Usage: limit <symbol> <side> <quantity> <price> [--reduce-only|-r]")
                    print("   Example: limit MNQ SELL 1 19500.50 --reduce-only")
                    print("   --reduce-only: Order auto-cancels when position closes (for TP)")
                    continue
                
                symbol, side, quantity, price = parts[1], parts[2], parts[3], parts[4]
                
                try:
                    quantity = int(quantity)
                    price = float(price)
                except ValueError:
                    print("❌ Quantity must be a number and price must be a decimal number")
                    continue
                
                if side.upper() not in ["BUY", "SELL"]:
                    print("❌ Side must be BUY or SELL")
                    continue
                
                # Confirm the limit order
                print(f"\n⚠️  CONFIRM LIMIT ORDER:")
                print(f"   Symbol: {symbol.upper()}")
                print(f"   Side: {side.upper()}")
                print(f"   Quantity: {quantity}")
                print(f"   Price: {price}")
                if reduce_only:
                    print(f"   🛡️  Reduce-Only: YES (auto-cancels when position closes)")
                print(f"   Account: {bot.selected_account['name']}")
                
                confirm = input("   Confirm? (y/N): ").strip().lower()
                if confirm != 'y':
                    print("❌ Order cancelled")
                    continue
                
                # Place the limit order
                result = await bot.place_market_order(symbol, side, quantity, order_type="limit", limit_price=price, reduce_only=reduce_only)
                if "error" in result:
                    print(f"❌ Order failed: {result['error']}")
                else:
                    print(f"✅ Limit order placed successfully!")
                    print(f"   Order ID: {result.get('orderId', 'Unknown')}")
                    print(f"   Status: {result.get('status', 'Unknown')}")
                    if reduce_only:
                        print(f"   🛡️  Reduce-only: Will auto-cancel when position closes")
            
            elif command_lower.startswith("bracket "):
                parts = command.split()
                if len(parts) != 6:
                    print("❌ Usage: bracket <symbol> <side> <quantity> <stop_ticks> <profit_ticks>")
                    print("   Example: bracket MNQ BUY 1 80 80  (stop down 80, profit up 80)")
                    print("   Example: bracket MNQ SELL 1 80 80  (stop up 80, profit down 80)")
                    print("")
                    print("   💡 Tip: Use positive numbers - the bot auto-corrects signs based on side")
                    continue

                symbol, side, quantity, stop_ticks, profit_ticks = parts[1], parts[2], parts[3], parts[4], parts[5]

                try:
                    quantity = int(quantity)
                    stop_ticks = int(stop_ticks)
                    profit_ticks = int(profit_ticks)
                except ValueError:
                    print("❌ Quantity, stop_ticks, and profit_ticks must be numbers")
                    continue

                if side.upper() not in ["BUY", "SELL"]:
                    print("❌ Side must be BUY or SELL")
                    continue
                
                # Auto-correct tick signs based on side for better UX
                # TopStepX requires specific signs based on position direction
                if side.upper() == "BUY":
                    # For LONG: stop loss below entry (negative), profit above entry (positive)
                    stop_ticks_corrected = -abs(stop_ticks)
                    profit_ticks_corrected = abs(profit_ticks)
                else:  # SELL
                    # For SHORT: stop loss above entry (positive), profit below entry (negative)
                    stop_ticks_corrected = abs(stop_ticks)
                    profit_ticks_corrected = -abs(profit_ticks)
                
                # Show corrected values if they changed
                if stop_ticks != stop_ticks_corrected or profit_ticks != profit_ticks_corrected:
                    print(f"\n💡 Auto-corrected tick signs for {side.upper()} order:")
                    print(f"   Stop Loss: {stop_ticks} → {stop_ticks_corrected} ticks")
                    print(f"   Take Profit: {profit_ticks} → {profit_ticks_corrected} ticks")
                
                # Update with corrected values
                stop_ticks = stop_ticks_corrected
                profit_ticks = profit_ticks_corrected
                
                # Validate minimum tick distance (TopStepX requires at least 4 ticks)
                if abs(stop_ticks) < 4:
                    print(f"❌ Stop loss must be at least 4 ticks away from entry (you entered {abs(stop_ticks)})")
                    continue
                if abs(profit_ticks) < 4:
                    print(f"❌ Take profit must be at least 4 ticks away from entry (you entered {abs(profit_ticks)})")
                    continue

                # Confirm the bracket trade
                print(f"\n⚠️  CONFIRM BRACKET TRADE:")
                print(f"   Symbol: {symbol.upper()}")
                print(f"   Side: {side.upper()}")
                print(f"   Quantity: {quantity}")
                print(f"   Stop Loss: {stop_ticks} ticks {'(below entry)' if stop_ticks < 0 else '(above entry)'}")
                print(f"   Take Profit: {profit_ticks} ticks {'(below entry)' if profit_ticks < 0 else '(above entry)'}")
                print(f"   Account: {bot.selected_account['name']}")

                confirm = input("   Confirm? (y/N): ").strip().lower()
                if confirm != 'y':
                    print("❌ Trade cancelled")
                    continue

                # Place the bracket order
                result = await bot.place_market_order(symbol, side, quantity,
                                                    stop_loss_ticks=stop_ticks,
                                                    take_profit_ticks=profit_ticks,
                                                    order_type="bracket")
                if "error" in result:
                    print(f"❌ Order failed: {result['error']}")
                else:
                    print(f"✅ Bracket order placed successfully!")
                    print(f"   Order ID: {result.get('orderId', 'Unknown')}")
                    print(f"   Status: {result.get('status', 'Unknown')}")
            
            elif command_lower.startswith("native_bracket "):
                parts = command.split()
                if len(parts) != 6:
                    print("❌ Usage: native_bracket <symbol> <side> <quantity> <stop_price> <profit_price>")
                    print("   Example: native_bracket MNQ BUY 1 19400.00 19600.00")
                    continue
                
                symbol, side, quantity, stop_price, profit_price = parts[1], parts[2], parts[3], parts[4], parts[5]
                
                try:
                    quantity = int(quantity)
                    stop_price = float(stop_price)
                    profit_price = float(profit_price)
                except ValueError:
                    print("❌ Quantity must be a number and prices must be decimal numbers")
                    continue
                
                if side.upper() not in ["BUY", "SELL"]:
                    print("❌ Side must be BUY or SELL")
                    continue
                
                # Show OCO bracket warning
                print(f"\n⚠️  IMPORTANT: Bracket orders require 'Auto OCO Brackets' to be enabled in your TopStepX account settings.")
                print(f"   If this order fails with 'Brackets cannot be used with Position Brackets', please enable Auto OCO Brackets in your account.")
                
                # Confirm the native bracket order
                print(f"\n⚠️  CONFIRM NATIVE BRACKET ORDER:")
                print(f"   Symbol: {symbol.upper()}")
                print(f"   Side: {side.upper()}")
                print(f"   Quantity: {quantity}")
                print(f"   Stop Loss: ${stop_price}")
                print(f"   Take Profit: ${profit_price}")
                print(f"   Account: {bot.selected_account['name']}")
                
                confirm = input("   Confirm? (y/N): ").strip().lower()
                if confirm != 'y':
                    print("❌ Order cancelled")
                    continue
                
                # Place the native bracket order
                result = await bot.create_bracket_order(symbol, side, quantity, 
                                                      stop_loss_price=stop_price, 
                                                      take_profit_price=profit_price)
                if "error" in result:
                    print(f"❌ Order failed: {result['error']}")
                else:
                    print(f"✅ Native bracket order placed successfully!")
                    print(f"   Order ID: {result.get('orderId', 'Unknown')}")
                    print(f"   Status: {result.get('status', 'Unknown')}")
            
            elif command_lower.startswith("stop_bracket "):
                parts = command.split()
                
                # Check for --breakeven flag
                enable_breakeven = "--breakeven" in parts
                if enable_breakeven:
                    parts.remove("--breakeven")
                
                if len(parts) != 7:
                    print("❌ Usage: stop_bracket <symbol> <side> <quantity> <entry_price> <stop_price> <profit_price> [--breakeven]")
                    print("   Example: stop_bracket MNQ BUY 1 25800.00 25750.00 25900.00")
                    print("   Example: stop_bracket MNQ BUY 1 25800.00 25750.00 25900.00 --breakeven")
                    print("   Places a stop order at entry_price with bracket SL/TP attached")
                    print("   --breakeven: Automatically move stop to breakeven after profit threshold")
                    print(f"   Profit threshold: {os.getenv('MANUAL_BREAKEVEN_PROFIT_POINTS', '15.0')} points")
                    continue
                
                symbol, side, quantity, entry_price, stop_price, profit_price = parts[1], parts[2], parts[3], parts[4], parts[5], parts[6]
                
                try:
                    quantity = int(quantity)
                    entry_price = float(entry_price)
                    stop_price = float(stop_price)
                    profit_price = float(profit_price)
                except ValueError:
                    print("❌ Quantity must be a number and prices must be decimal numbers")
                    continue
                
                if side.upper() not in ["BUY", "SELL"]:
                    print("❌ Side must be BUY or SELL")
                    continue
                
                # Validate bracket prices
                if side.upper() == "BUY":
                    if stop_price >= entry_price:
                        print("❌ For BUY orders, stop loss must be below entry price")
                        continue
                    if profit_price <= entry_price:
                        print("❌ For BUY orders, take profit must be above entry price")
                        continue
                else:  # SELL
                    if stop_price <= entry_price:
                        print("❌ For SELL orders, stop loss must be above entry price")
                        continue
                    if profit_price >= entry_price:
                        print("❌ For SELL orders, take profit must be below entry price")
                        continue
                
                # Check env variable for default breakeven behavior
                if not enable_breakeven and os.getenv('MANUAL_BREAKEVEN_ENABLED', 'false').lower() in ('true', '1', 'yes', 'on'):
                    enable_breakeven = True
                
                # Confirm the stop bracket order
                print(f"\n⚠️  CONFIRM STOP BRACKET ORDER:")
                print(f"   Symbol: {symbol.upper()}")
                print(f"   Side: {side.upper()}")
                print(f"   Quantity: {quantity}")
                print(f"   Entry (Stop) Price: ${entry_price}")
                print(f"   Stop Loss: ${stop_price}")
                print(f"   Take Profit: ${profit_price}")
                print(f"   Breakeven: {'✅ Enabled' if enable_breakeven else '❌ Disabled'}")
                if enable_breakeven:
                    breakeven_points = float(os.getenv('MANUAL_BREAKEVEN_PROFIT_POINTS', '15.0'))
                    print(f"   Breakeven Threshold: {breakeven_points} points profit")
                print(f"   Account: {bot.selected_account['name']}")
                print(f"   ⚠️  Entry triggers when price {'rises to' if side.upper() == 'BUY' else 'falls to'} ${entry_price}")
                
                confirm = input("   Confirm? (y/N): ").strip().lower()
                if confirm != 'y':
                    print("❌ Order cancelled")
                    continue
                
                # Place the OCO bracket with stop entry
                print(f"\n🚀 Placing OCO bracket order with stop entry...")
                result = await bot.place_oco_bracket_with_stop_entry(
                    symbol=symbol,
                    side=side,
                    quantity=quantity,
                    entry_price=entry_price,
                    stop_loss_price=stop_price,
                    take_profit_price=profit_price,
                    enable_breakeven=enable_breakeven
                )
                
                if "error" in result:
                    print(f"❌ Stop bracket failed: {result['error']}")
                    continue
                
                entry_order_id = result.get('orderId')
                method = result.get('method', 'unknown')
                
                if method == "oco_native":
                    print(f"✅ OCO bracket order placed successfully!")
                    print(f"   Order ID: {entry_order_id}")
                    print(f"   Method: Native OCO (atomic)")
                    print(f"   Entry: ${entry_price} (stop order)")
                    print(f"   Stop Loss: ${stop_price}")
                    print(f"   Take Profit: ${profit_price}")
                    print(f"   📝 All orders linked - one fills, others cancel automatically")
                    if enable_breakeven:
                        breakeven_pts = float(os.getenv('MANUAL_BREAKEVEN_PROFIT_POINTS', '15.0'))
                        print(f"   🔄 Breakeven enabled: Stop will move to entry after +{breakeven_pts} pts profit")
                elif method == "hybrid_auto_bracket":
                    print(f"✅ Stop entry order placed with auto-bracketing!")
                    print(f"   Order ID: {entry_order_id}")
                    print(f"   Method: Hybrid (auto-bracket on fill)")
                    print(f"   Entry: ${entry_price} (stop order)")
                    print(f"   Stop Loss: ${stop_price}")
                    print(f"   Take Profit: ${profit_price}")
                    print(f"   📝 Brackets will be placed automatically when stop order fills")
                    if enable_breakeven:
                        breakeven_pts = float(os.getenv('MANUAL_BREAKEVEN_PROFIT_POINTS', '15.0'))
                        print(f"   🔄 Breakeven enabled: Stop will move to entry after +{breakeven_pts} pts profit")
                else:
                    print(f"✅ Stop bracket order placed!")
                    print(f"   Order ID: {entry_order_id}")
                    print(f"   Entry: ${entry_price}")
                    print(f"   Stop Loss: ${stop_price}")
                    print(f"   Take Profit: ${profit_price}")
                    if enable_breakeven:
                        breakeven_pts = float(os.getenv('MANUAL_BREAKEVEN_PROFIT_POINTS', '15.0'))
                        print(f"   🔄 Breakeven enabled: Stop will move to entry after +{breakeven_pts} pts profit")
            
            elif command_lower == "strategy_start" or command_lower.startswith("strategy_start "):
                # Start overnight range breakout strategy
                parts = command.split()
                symbols = parts[1:] if len(parts) > 1 else None
                
                print(f"\n🎯 Starting Overnight Range Breakout Strategy...")
                print(f"   Symbols: {symbols or os.getenv('STRATEGY_SYMBOLS', 'MNQ,MES')}")
                print(f"   Overnight: {bot.overnight_strategy.overnight_start} - {bot.overnight_strategy.overnight_end}")
                print(f"   Market Open: {bot.overnight_strategy.market_open_time}")
                print(f"   ATR Period: {bot.overnight_strategy.atr_period} ({bot.overnight_strategy.atr_timeframe})")
                if bot.overnight_strategy.breakeven_enabled:
                    print(f"   Breakeven: ENABLED (+{bot.overnight_strategy.breakeven_profit_points} pts)")
                else:
                    print(f"   Breakeven: DISABLED")
                
                confirm = input("\n   Start strategy? (y/N): ").strip().lower()
                if confirm != 'y':
                    print("❌ Strategy start cancelled")
                    continue
                
                await bot.overnight_strategy.start(symbols)
                print("✅ Strategy started! It will place orders at market open.")
            
            elif command_lower == "strategy_stop":
                # Stop overnight range breakout strategy
                if not bot.overnight_strategy.is_trading:
                    print("❌ Strategy is not running")
                    continue
                
                confirm = input("\n   Stop strategy? (y/N): ").strip().lower()
                if confirm != 'y':
                    print("❌ Strategy stop cancelled")
                    continue
                
                await bot.overnight_strategy.stop()
                print("✅ Strategy stopped!")
            
            elif command_lower == "strategy_status":
                # Show strategy status
                status = bot.overnight_strategy.get_status()
                
                print(f"\n📊 Overnight Range Strategy Status:")
                print(f"   Active: {'✅ YES' if status['is_trading'] else '❌ NO'}")
                print(f"\n   Configuration:")
                for key, value in status['config'].items():
                    key_display = key.replace('_', ' ').title()
                    print(f"     {key_display}: {value}")
                
                if status['active_ranges']:
                    print(f"\n   📈 Tracked Ranges:")
                    for symbol, range_data in status['active_ranges'].items():
                        print(f"     {symbol}: High={range_data['high']:.2f}, Low={range_data['low']:.2f}, Range={range_data['range_size']:.2f}")
                
                if status['active_orders']:
                    print(f"\n   📝 Active Orders:")
                    for symbol, order_ids in status['active_orders'].items():
                        print(f"     {symbol}: {len(order_ids)} orders")
                
                if status['config']['breakeven_enabled']:
                    if status['breakeven_monitoring']:
                        print(f"\n   🎯 Breakeven Monitoring:")
                        for order_id, monitor_data in status['breakeven_monitoring'].items():
                            filled = monitor_data.get('filled', False)
                            triggered = monitor_data['triggered']
                            
                            if triggered:
                                status_str = "✓ At Breakeven"
                            elif filled:
                                status_str = "⏳ Monitoring (position filled)"
                            else:
                                status_str = "⏸ Waiting for fill"
                            
                            print(f"     {monitor_data['symbol']} {monitor_data['side']}: {status_str}")
                    else:
                        print(f"\n   🎯 Breakeven Monitoring: No active positions")
                else:
                    print(f"\n   🎯 Breakeven Monitoring: DISABLED")
            
            elif command_lower.startswith("strategy_test "):
                # Test strategy components (ATR, overnight range, order calculation)
                parts = command.split()
                if len(parts) != 2:
                    print("❌ Usage: strategy_test <symbol>")
                    print("   Example: strategy_test MNQ")
                    continue
                
                symbol = parts[1].upper()
                
                print(f"\n🔬 Testing strategy components for {symbol}...")
                
                # Test ATR calculation
                print(f"\n1️⃣ Calculating ATR...")
                atr_data = await bot.overnight_strategy.calculate_atr(symbol)
                if atr_data:
                    print(f"   ✅ Current ATR: {atr_data.current_atr:.2f}")
                    print(f"   ✅ Daily ATR: {atr_data.daily_atr:.2f}")
                    print(f"   ✅ ATR Zone High: {atr_data.atr_zone_high:.2f}")
                    print(f"   ✅ ATR Zone Low: {atr_data.atr_zone_low:.2f}")
                else:
                    print(f"   ❌ ATR calculation failed")
                
                # Test overnight range tracking
                print(f"\n2️⃣ Tracking overnight range...")
                range_data = await bot.overnight_strategy.track_overnight_range(symbol)
                if range_data:
                    print(f"   ✅ High: {range_data.high:.2f}")
                    print(f"   ✅ Low: {range_data.low:.2f}")
                    print(f"   ✅ Range Size: {range_data.range_size:.2f}")
                    print(f"   ✅ Midpoint: {range_data.midpoint:.2f}")
                    print(f"   ✅ Time: {range_data.start_time} to {range_data.end_time}")
                else:
                    print(f"   ❌ Range tracking failed")
                
                # Test order calculation
                print(f"\n3️⃣ Calculating breakout orders...")
                long_order, short_order = await bot.overnight_strategy.calculate_range_break_orders(symbol)
                if long_order and short_order:
                    print(f"   ✅ LONG Order:")
                    print(f"      Entry: {long_order.entry_price:.2f}")
                    print(f"      Stop:  {long_order.stop_loss:.2f}")
                    print(f"      TP:    {long_order.take_profit:.2f}")
                    print(f"   ✅ SHORT Order:")
                    print(f"      Entry: {short_order.entry_price:.2f}")
                    print(f"      Stop:  {short_order.stop_loss:.2f}")
                    print(f"      TP:    {short_order.take_profit:.2f}")
                else:
                    print(f"   ❌ Order calculation failed")
                
                print(f"\n✅ Strategy test complete!")
            
            elif command_lower == "strategy_execute" or command_lower.startswith("strategy_execute "):
                # Manually trigger market open sequence (for testing)
                if not bot.overnight_strategy.is_trading:
                    print("❌ Strategy is not running. Start it first with 'strategy_start'")
                    continue
                
                parts = command.split()
                symbols = parts[1:] if len(parts) > 1 else None
                
                print(f"\n🚀 Manually executing market open sequence...")
                print(f"   Symbols: {symbols or 'default from config'}")
                confirm = input("   Execute now? (y/N): ").strip().lower()
                if confirm != 'y':
                    print("❌ Execution cancelled")
                    continue
                
                try:
                    await bot.overnight_strategy._execute_market_open_sequence(symbols)
                    print("✅ Market open sequence executed!")
                except Exception as e:
                    print(f"❌ Error executing sequence: {e}")
                    logger.error(f"Error in manual strategy execution: {e}")
                    import traceback
                    logger.error(f"Traceback: {traceback.format_exc()}")
            
            # Modular Strategy System Commands
            elif command_lower == "strategies list" or command_lower == "strategies":
                # List all available strategies
                print(f"\n📦 Available Strategies:")
                print(f"="*60)
                
                _names = (
                    bot.strategy_manager.catalog_strategy_names()
                    if hasattr(bot.strategy_manager, "catalog_strategy_names")
                    else bot.strategy_manager.registered_strategy_names()
                )
                for name in _names:
                    strategy_instance = bot.strategy_manager.strategies.get(name)
                    
                    if strategy_instance:
                        status = strategy_instance.status.value
                        enabled = strategy_instance.config.enabled
                        symbols = ", ".join(strategy_instance.config.symbols)
                        status_emoji = "✅" if status == "active" else "⏸️" if status == "paused" else "⚪"
                    else:
                        status = "not loaded"
                        enabled = False
                        symbols = "N/A"
                        status_emoji = "⚪"
                    
                    print(f"{status_emoji} {name.replace('_', ' ').title()}")
                    print(f"   Status: {status}")
                    print(f"   Enabled in Config: {enabled}")
                    print(f"   Symbols: {symbols}")
                    print()
                
                print(f"💡 Use 'strategies start <name>' to start a strategy")
                print(f"💡 Use 'strategies status' for detailed metrics")
            
            elif command_lower == "strategies status":
                # Show detailed status of all strategies
                status = bot.strategy_manager.get_status()

                print(f"\n📊 Strategy Manager Status:")
                print(f"="*60)
                print(f"Active Strategies: {status['active_strategies']}/{status['total_strategies']}")
                print(f"Running Tasks: {status.get('running_tasks', 0)}")
                print(f"Total Positions: {status['total_positions']}")
                print()

                for strategy_name, strategy_status in status['strategies'].items():
                    # Show monitoring status with icon
                    monitoring = strategy_status.get('monitoring', False)
                    monitoring_icon = "🟢" if monitoring else "⚪"
                    
                    print(f"{monitoring_icon} {strategy_name.replace('_', ' ').title()}:")
                    print(f"   Status: {strategy_status['status']}")
                    print(f"   Enabled: {strategy_status['enabled']}")
                    print(f"   Monitoring: {'YES - Actively Trading' if monitoring else 'NO - Not Running'}")
                    print(f"   Symbols: {', '.join(strategy_status['symbols'])}")
                    print(f"   Active Positions: {strategy_status['active_positions']}")
                    print(f"   Daily Trades: {strategy_status['daily_trades']}")

                    metrics = strategy_status.get('metrics', {})
                    if metrics.get('total_trades', 0) > 0:
                        print(f"   Metrics:")
                        print(f"     Total Trades: {metrics['total_trades']}")
                        print(f"     Win Rate: {metrics['win_rate']}")
                        print(f"     Total P&L: {metrics['total_pnl']}")
                        print(f"     Profit Factor: {metrics['profit_factor']}")
                    print()
            
            elif command_lower.startswith("strategies start "):
                # Start a specific strategy
                # Parse command: strategies start <name> [--symbols=SYM1,SYM2] [--timeframe=TIMEFRAME]
                parts = command.split()
                if len(parts) < 3:
                    print("❌ Usage: strategies start <name> [--symbols=SYM1,SYM2] [--timeframe=TIMEFRAME]")
                    print("   Example: strategies start simple_candle --timeframe=1m --symbols=MNQ")
                    print("   Available strategies:")
                    _avail = (
                        bot.strategy_manager.catalog_strategy_names()
                        if hasattr(bot.strategy_manager, "catalog_strategy_names")
                        else bot.strategy_manager.registered_strategy_names()
                    )
                    for name in _avail:
                        print(f"     - {name}")
                    continue
                
                strategy_name = parts[2]
                symbols = None
                timeframe = None
                
                # Parse remaining arguments for flags
                for arg in parts[3:]:
                    if arg.startswith('--symbols='):
                        symbols_str = arg.split('=', 1)[1].strip("'\"")
                        symbols = [s.strip().upper() for s in symbols_str.split(',')]
                    elif arg.startswith('--timeframe='):
                        timeframe = arg.split('=', 1)[1].strip("'\"")
                    elif not arg.startswith('--'):
                        # Legacy support: treat non-flag arguments as symbols
                        if symbols is None:
                            symbols = [s.strip().upper() for s in arg.split(',')]
                
                # Set timeframe environment variable if provided (for simple_candle strategy)
                if timeframe:
                    os.environ['SIMPLE_CANDLE_TIMEFRAME'] = timeframe
                    print(f"⏰ Timeframe set to: {timeframe}")
                
                print(f"\n🚀 Starting {strategy_name.replace('_', ' ').title()} Strategy...")
                success, message = await bot.strategy_manager.start_strategy(strategy_name, symbols)
                
                if success:
                    print(f"✅ {message}")
                else:
                    print(f"❌ {message}")
            
            elif command_lower.startswith("strategies stop "):
                # Stop a specific strategy
                parts = command.split()
                if len(parts) != 3:
                    print("❌ Usage: strategies stop <name>")
                    print("   Example: strategies stop mean_reversion")
                    continue
                
                strategy_name = parts[2]
                
                print(f"\n🛑 Stopping {strategy_name.replace('_', ' ').title()} Strategy...")
                success, message = await bot.strategy_manager.stop_strategy(strategy_name)
                
                if success:
                    print(f"✅ {message}")
                else:
                    print(f"❌ {message}")
            
            elif command_lower == "strategies start_all":
                # Start all enabled strategies
                print(f"\n🚀 Starting all enabled strategies...")
                results = await bot.strategy_manager.start_all_strategies()
                
                for strategy_name, (success, message) in results.items():
                    emoji = "✅" if success else "❌"
                    print(f"{emoji} {strategy_name.replace('_', ' ').title()}: {message}")
            
            elif command_lower == "strategies stop_all":
                # Stop all strategies
                confirm = input("\n⚠️  Stop all active strategies? (y/N): ").strip().lower()
                if confirm != 'y':
                    print("❌ Operation cancelled")
                    continue
                
                print(f"\n🛑 Stopping all strategies...")
                results = await bot.strategy_manager.stop_all_strategies()
                
                for strategy_name, (success, message) in results.items():
                    emoji = "✅" if success else "❌"
                    print(f"{emoji} {strategy_name.replace('_', ' ').title()}: {message}")
            
            elif command_lower.startswith("backtest "):
                # Run backtest: backtest <strategy> <symbol> [options]
                print("\n🔬 Launching backtest...")
                print("   Use: python core/backtest_executor.py --strategy=<name> --symbol=<sym> --days=<N>")
                print("   Example: python core/backtest_executor.py --strategy=ma_crossover --symbol=MNQ --days=30 --sample")
                print("\n   See docs/BACKTEST_CLI_GUIDE.md for full documentation")
            
            elif command_lower == "backtest":
                # Show backtest help
                print("\n🔬 Backtesting Commands:")
                print("   backtest <strategy> <symbol> - Quick info on how to run")
                print("\n   For full backtesting, use:")
                print("   python core/backtest_executor.py --strategy=<name> --symbol=<sym> [options]")
                print("\n   Available strategies: ma_crossover, rsi_mean_reversion, ema_trend")
                print("   Options: --days, --timeframe, --sample, --monte-carlo, --optimize")
                print("\n   See docs/BACKTEST_CLI_GUIDE.md for examples")
            
            elif command_lower.startswith("stop "):
                parts = command.split()
                
                # Check for --reduce-only or -r flag
                reduce_only = False
                if '--reduce-only' in parts:
                    reduce_only = True
                    parts.remove('--reduce-only')
                elif '-r' in parts:
                    reduce_only = True
                    parts.remove('-r')
                
                if len(parts) != 5:
                    print("❌ Usage: stop <symbol> <side> <quantity> <price> [--reduce-only|-r]")
                    print("   Example: stop MNQ SELL 1 19400.00 --reduce-only")
                    print("   --reduce-only: Order auto-cancels when position closes (for SL/TP)")
                    continue
                
                symbol, side, quantity, price = parts[1], parts[2], parts[3], parts[4]
                
                try:
                    quantity = int(quantity)
                    price = float(price)
                except ValueError:
                    print("❌ Quantity must be a number and price must be a decimal number")
                    continue
                
                if side.upper() not in ["BUY", "SELL"]:
                    print("❌ Side must be BUY or SELL")
                    continue
                
                # Confirm the stop order
                print(f"\n⚠️  CONFIRM STOP ORDER:")
                print(f"   Symbol: {symbol.upper()}")
                print(f"   Side: {side.upper()}")
                print(f"   Quantity: {quantity}")
                print(f"   Stop Price: ${price}")
                if reduce_only:
                    print(f"   🛡️  Reduce-Only: YES (auto-cancels when position closes)")
                print(f"   Account: {bot.selected_account['name']}")
                
                confirm = input("   Confirm? (y/N): ").strip().lower()
                if confirm != 'y':
                    print("❌ Order cancelled")
                    continue
                
                # Place the stop order
                result = await bot.place_stop_order(symbol, side, quantity, price, reduce_only=reduce_only)
                if "error" in result:
                    print(f"❌ Order failed: {result['error']}")
                else:
                    print(f"✅ Stop {side} order placed successfully!")
                    print(f"   Order ID: {result.get('orderId', 'Unknown')}")
                    print(f"   Status: {result.get('status', 'Unknown')}")
                    if reduce_only:
                        print(f"   🛡️  Reduce-only: Will auto-cancel when position closes")
                    print(f"   ⚠️  This order will trigger a market order when price reaches ${price}")
            
            elif command_lower.startswith("stop_buy "):
                parts = command.split()
                if len(parts) != 4:
                    print("❌ Usage: stop_buy <symbol> <quantity> <stop_price>")
                    print("   Example: stop_buy MNQ 1 25900.00")
                    print("   Places a stop buy order (triggers market buy when price reaches stop_price)")
                    continue
                
                symbol, quantity, stop_price = parts[1], parts[2], parts[3]
                
                try:
                    quantity = int(quantity)
                    stop_price = float(stop_price)
                except ValueError:
                    print("❌ Quantity must be a number and stop_price must be a decimal number")
                    continue
                
                # Confirm the stop buy order
                print(f"\n⚠️  CONFIRM STOP BUY ORDER:")
                print(f"   Symbol: {symbol.upper()}")
                print(f"   Quantity: {quantity}")
                print(f"   Stop Price: ${stop_price}")
                print(f"   Account: {bot.selected_account['name']}")
                print(f"   ⚠️  This will trigger a market BUY when price reaches ${stop_price}")
                
                confirm = input("   Confirm? (y/N): ").strip().lower()
                if confirm != 'y':
                    print("❌ Order cancelled")
                    continue
                
                # Place the stop buy order
                result = await bot.place_stop_order(symbol, "BUY", quantity, stop_price)
                if "error" in result:
                    print(f"❌ Order failed: {result['error']}")
                else:
                    print(f"✅ Stop buy order placed successfully!")
                    print(f"   Order ID: {result.get('orderId', 'Unknown')}")
                    print(f"   Status: {result.get('status', 'Unknown')}")
            
            elif command_lower.startswith("stop_sell "):
                parts = command.split()
                if len(parts) != 4:
                    print("❌ Usage: stop_sell <symbol> <quantity> <stop_price>")
                    print("   Example: stop_sell MNQ 1 25900.00")
                    print("   Places a stop sell order (triggers market sell when price reaches stop_price)")
                    continue
                
                symbol, quantity, stop_price = parts[1], parts[2], parts[3]
                
                try:
                    quantity = int(quantity)
                    stop_price = float(stop_price)
                except ValueError:
                    print("❌ Quantity must be a number and stop_price must be a decimal number")
                    continue
                
                # Confirm the stop sell order
                print(f"\n⚠️  CONFIRM STOP SELL ORDER:")
                print(f"   Symbol: {symbol.upper()}")
                print(f"   Quantity: {quantity}")
                print(f"   Stop Price: ${stop_price}")
                print(f"   Account: {bot.selected_account['name']}")
                print(f"   ⚠️  This will trigger a market SELL when price reaches ${stop_price}")
                
                confirm = input("   Confirm? (y/N): ").strip().lower()
                if confirm != 'y':
                    print("❌ Order cancelled")
                    continue
                
                # Place the stop sell order
                result = await bot.place_stop_order(symbol, "SELL", quantity, stop_price)
                if "error" in result:
                    print(f"❌ Order failed: {result['error']}")
                else:
                    print(f"✅ Stop sell order placed successfully!")
                    print(f"   Order ID: {result.get('orderId', 'Unknown')}")
                    print(f"   Status: {result.get('status', 'Unknown')}")
            
            elif command_lower.startswith("modify_stop "):
                parts = command.split()
                if len(parts) != 3:
                    print("❌ Usage: modify_stop <position_id> <new_stop_price>")
                    print("   Example: modify_stop 425682864 25800.00")
                    print("   Modifies the stop loss order attached to a position")
                    continue
                
                position_id, new_stop_price = parts[1], parts[2]
                
                try:
                    new_stop_price = float(new_stop_price)
                except ValueError:
                    print("❌ new_stop_price must be a decimal number")
                    continue
                
                # Confirm the stop loss modification
                print(f"\n⚠️  CONFIRM MODIFY STOP LOSS:")
                print(f"   Position ID: {position_id}")
                print(f"   New Stop Price: ${new_stop_price}")
                print(f"   Account: {bot.selected_account['name']}")
                
                confirm = input("   Confirm? (y/N): ").strip().lower()
                if confirm != 'y':
                    print("❌ Modification cancelled")
                    continue
                
                # Modify the stop loss
                result = await bot.modify_stop_loss(position_id, new_stop_price)
                if "error" in result:
                    print(f"❌ Modify failed: {result['error']}")
                else:
                    print(f"✅ Stop loss modified successfully!")
                    print(f"   Position ID: {position_id}")
                    print(f"   New Stop Price: ${new_stop_price}")
                    print(f"   Stop Order ID: {result.get('stop_order_id', 'Unknown')}")
            
            elif command_lower.startswith("modify_tp "):
                parts = command.split()
                if len(parts) != 3:
                    print("❌ Usage: modify_tp <position_id> <new_tp_price>")
                    print("   Example: modify_tp 425682864 26100.00")
                    print("   Modifies the take profit order attached to a position")
                    continue
                
                position_id, new_tp_price = parts[1], parts[2]
                
                try:
                    new_tp_price = float(new_tp_price)
                except ValueError:
                    print("❌ new_tp_price must be a decimal number")
                    continue
                
                # Confirm the take profit modification
                print(f"\n⚠️  CONFIRM MODIFY TAKE PROFIT:")
                print(f"   Position ID: {position_id}")
                print(f"   New Take Profit Price: ${new_tp_price}")
                print(f"   Account: {bot.selected_account['name']}")
                
                confirm = input("   Confirm? (y/N): ").strip().lower()
                if confirm != 'y':
                    print("❌ Modification cancelled")
                    continue
                
                # Modify the take profit
                result = await bot.modify_take_profit(position_id, new_tp_price)
                if "error" in result:
                    print(f"❌ Modify failed: {result['error']}")
                else:
                    print(f"✅ Take profit modified successfully!")
                    print(f"   Position ID: {position_id}")
                    print(f"   New Take Profit Price: ${new_tp_price}")
                    print(f"   TP Order ID: {result.get('tp_order_id', 'Unknown')}")
            
            elif command_lower.startswith("trail "):
                parts = command.split()
                if len(parts) != 5:
                    print("❌ Usage: trail <symbol> <side> <quantity> <trail_amount>")
                    print("   Example: trail MNQ BUY 1 25.00")
                    print("   Places a trailing stop order (uses SDK native trailing stop)")
                    print("   ⚠️  Requires USE_PROJECTX_SDK=1 in .env file")
                    continue
                
                symbol, side, quantity, trail_amount = parts[1], parts[2], parts[3], parts[4]
                
                try:
                    quantity = int(quantity)
                    trail_amount = float(trail_amount)
                except ValueError:
                    print("❌ Quantity must be a number and trail_amount must be a decimal number")
                    continue
                
                if side.upper() not in ["BUY", "SELL"]:
                    print("❌ Side must be BUY or SELL")
                    continue
                
                # Check if SDK is available
                use_sdk = os.getenv("USE_PROJECTX_SDK", "0").lower() in ("1", "true", "yes")
                if not use_sdk or sdk_adapter is None or not sdk_adapter.is_sdk_available():
                    print("❌ Trailing stop requires SDK. Please:")
                    print("   1. Install: pip install 'project-x-py[realtime]'")
                    print("   2. Set USE_PROJECTX_SDK=1 in your .env file")
                    continue
                
                # Confirm the trailing stop order
                print(f"\n⚠️  CONFIRM TRAILING STOP ORDER:")
                print(f"   Symbol: {symbol.upper()}")
                print(f"   Side: {side.upper()}")
                print(f"   Quantity: {quantity}")
                print(f"   Trail Amount: ${trail_amount}")
                print(f"   Account: {bot.selected_account['name']}")
                print(f"   ⚠️  This uses SDK native trailing stop (automatically adjusts with price)")
                
                confirm = input("   Confirm? (y/N): ").strip().lower()
                if confirm != 'y':
                    print("❌ Order cancelled")
                    continue
                
                # Place the trailing stop order via SDK
                result = await bot.place_trailing_stop_order(symbol, side, quantity, trail_amount)
                if "error" in result:
                    print(f"❌ Order failed: {result['error']}")
                else:
                    print(f"✅ Trailing stop order placed successfully!")
                    print(f"   Order ID: {result.get('orderId', 'Unknown')}")
                    print(f"   Method: SDK native trailing stop")
                    print(f"   ⚠️  Stop will automatically adjust as price moves in your favor")
            
            elif command_lower == "positions":
                positions = await bot.get_open_positions()
                if positions:
                    print(f"\n📊 Open Positions ({len(positions)}):")
                    print(f"{'ID':<12} {'Symbol':<8} {'Side':<6} {'Quantity':<10} {'Price':<12} {'Stop':<12} {'TP':<12} {'P&L':<12}")
                    print("-" * 90)
                    
                    # OPTIMIZATION: Fetch orders AND quotes in PARALLEL before loop
                    account_id = bot.selected_account['id'] if bot.selected_account else None
                    all_orders = None
                    all_quotes = {}
                    
                    if account_id and bot.broker_adapter:
                        try:
                            # Extract unique symbols from positions
                            symbols = set()
                            for pos in positions:
                                contract_id = pos.get('contractId', '')
                                if contract_id:
                                    symbol = contract_id.split('.')[-2] if '.' in contract_id else contract_id
                                    symbols.add(symbol)
                                elif pos.get('symbol'):
                                    symbols.add(pos.get('symbol'))
                            
                            # Fetch orders and all quotes in parallel (concurrent API calls)
                            import asyncio as aio  # Local import to avoid any shadowing issues
                            fetch_tasks = [
                                bot.broker_adapter.get_open_orders(account_id=account_id),
                            ]
                            for symbol in symbols:
                                fetch_tasks.append(bot.get_market_quote(symbol))
                            
                            results = await aio.gather(*fetch_tasks, return_exceptions=True)
                            
                            # First result is orders
                            if not isinstance(results[0], Exception):
                                all_orders = results[0]
                            
                            # Remaining results are quotes (same order as symbols list)
                            for i, symbol in enumerate(symbols, start=1):
                                if i < len(results) and not isinstance(results[i], Exception):
                                    all_quotes[symbol] = results[i]
                            
                            logger.debug(f"⚡ Parallel fetch: {len(all_orders) if all_orders else 0} orders + {len(all_quotes)} quotes in one batch")
                        except Exception as e:
                            logger.warning(f"Could not fetch data for batch optimization: {e}")
                    
                    for pos in positions:
                        pos_id = pos.get('id', 'N/A')
                        # Get symbol from contractId or symbol field
                        contract_id = pos.get('contractId', '')
                        if contract_id:
                            # Extract symbol from contract ID (e.g., CON.F.US.MNQ.Z25 -> MNQ)
                            symbol = contract_id.split('.')[-2] if '.' in contract_id else contract_id
                        else:
                            symbol = pos.get('symbol', 'N/A')
                        
                        # Determine side (0 = Long, 1 = Short per TopStepX API)
                        position_side = pos.get('side', 0)
                        if position_side == 0:
                            side = "LONG"
                            position_type = 1  # For compatibility with linked orders check below
                        elif position_side == 1:
                            side = "SHORT"
                            position_type = 2  # For compatibility with linked orders check below
                        else:
                            side = "UNKNOWN"
                            position_type = 0
                        
                        quantity = pos.get('size', 0)
                        price = pos.get('averagePrice', 0.0)
                        
                        # Get stop loss and take profit prices from linked orders
                        stop_price = None
                        tp_price = None
                        try:
                            # Use the adapter's get_linked_orders method with pre-fetched orders (optimization!)
                            if bot.broker_adapter and hasattr(bot.broker_adapter, 'get_linked_orders'):
                                linked_orders = await bot.broker_adapter.get_linked_orders(
                                    str(pos_id), 
                                    account_id,
                                    all_orders=all_orders,  # Pass pre-fetched orders
                                    position_data=pos  # Pass position data to avoid re-query
                                )
                            else:
                                linked_orders = await bot.get_linked_orders(str(pos_id))
                            
                            if linked_orders and isinstance(linked_orders, list):
                                logger.debug(f"Found {len(linked_orders)} linked orders for position {pos_id}")
                                for order in linked_orders:
                                    order_type = order.get('type', 0)
                                    order_side = order.get('side', -1)
                                    # Type 4 = Stop orders (stop loss), Type 1 = Limit orders (take profit)
                                    # Also check prices directly: stopPrice for stop loss, limitPrice for take profit
                                    has_stop_price = order.get('stopPrice') is not None
                                    has_limit_price = order.get('limitPrice') is not None
                                    
                                    # For long positions: stop loss is a sell stop, TP is a sell limit
                                    # For short positions: stop loss is a buy stop, TP is a buy limit
                                    if position_type == 1:  # LONG position
                                        if order_side == 1:  # SELL orders
                                            if order_type == 4 or has_stop_price:  # Stop order
                                                stop_price = order.get('stopPrice') or order.get('limitPrice')
                                                logger.debug(f"Found stop loss for long: {stop_price}")
                                            elif order_type == 1 and has_limit_price:  # Limit order for TP
                                                tp_price = order.get('limitPrice')
                                                logger.debug(f"Found take profit for long: {tp_price}")
                                    elif position_type == 2:  # SHORT position
                                        if order_side == 0:  # BUY orders
                                            if order_type == 4 or has_stop_price:  # Stop order
                                                stop_price = order.get('stopPrice') or order.get('limitPrice')
                                                logger.debug(f"Found stop loss for short: {stop_price}")
                                            elif order_type == 1 and has_limit_price:  # Limit order for TP
                                                tp_price = order.get('limitPrice')
                                                logger.debug(f"Found take profit for short: {tp_price}")
                            else:
                                logger.debug(f"No linked orders found for position {pos_id}")
                        except Exception as e:
                            logger.warning(f"Could not fetch linked orders for position {pos_id}: {e}")
                        
                        # Get P&L from API, or calculate it if not provided
                        pnl = pos.get('unrealizedPnl')
                        if pnl is None:
                            # Calculate P&L from current market price
                            # P&L = (price_difference) * quantity * point_value
                            try:
                                # OPTIMIZATION: Use pre-fetched quote if available
                                quote = all_quotes.get(symbol)
                                if not quote or "error" in quote:
                                    # Fallback: fetch quote if not in batch
                                    quote = await bot.get_market_quote(symbol)
                                
                                if "error" not in quote and quote.get('last'):
                                    current_price = float(quote['last'])
                                    point_value = bot._get_point_value(symbol)
                                    
                                    if position_type == 1:  # LONG
                                        price_diff = current_price - price
                                    else:  # SHORT
                                        price_diff = price - current_price
                                    
                                    # Calculate P&L: price difference * quantity * point value per contract
                                    pnl = price_diff * quantity * point_value
                                else:
                                    pnl = 0.0
                            except Exception as e:
                                logger.debug(f"Could not calculate P&L for {symbol}: {e}")
                                pnl = 0.0
                        else:
                            pnl = float(pnl)
                        
                        # Format prices for display
                        stop_str = f"${stop_price:.2f}" if stop_price else "N/A"
                        tp_str = f"${tp_price:.2f}" if tp_price else "N/A"
                        
                        print(f"{pos_id:<12} {symbol:<8} {side:<6} {quantity:<10} ${price:<11.2f} {stop_str:<12} {tp_str:<12} ${pnl:<11.2f}")
                else:
                    print("❌ No open positions found")
            
            elif command_lower == "orders":
                orders = await bot.get_open_orders()
                if orders:
                    print(f"\n📋 Open Orders ({len(orders)}):")
                    print(f"{'ID':<12} {'Symbol':<8} {'Side':<6} {'Type':<8} {'Quantity':<10} {'Price':<12} {'Status':<10}")
                    print("-" * 80)
                    for order in orders:
                        order_id = order.get('id', 'N/A')
                        # Get symbol from contractId or symbol field
                        contract_id = order.get('contractId', '')
                        if contract_id:
                            # Extract symbol from contract ID (e.g., CON.F.US.MNQ.Z25 -> MNQ)
                            symbol = contract_id.split('.')[-2] if '.' in contract_id else contract_id
                        else:
                            symbol = order.get('symbol', 'N/A')
                        
                        # Determine side from side field (0 = BUY, 1 = SELL)
                        side_num = order.get('side', -1)
                        if side_num == 0:
                            side = "BUY"
                        elif side_num == 1:
                            side = "SELL"
                        else:
                            side = "UNKNOWN"
                        
                        # Determine order type from type field
                        type_num = order.get('type', -1)
                        if type_num == 1:
                            order_type = "LIMIT"
                        elif type_num == 2:
                            order_type = "MARKET"
                        elif type_num == 4:
                            order_type = "STOP"
                        else:
                            order_type = f"TYPE{type_num}"
                        
                        # Determine status from status field
                        status_num = order.get('status', -1)
                        if status_num == 1:
                            status = "OPEN"
                        elif status_num == 2:
                            status = "FILLED"
                        elif status_num == 3:
                            status = "PENDING"
                        elif status_num == 5:
                            status = "CANCELLED"
                        else:
                            status = f"STATUS{status_num}"
                        
                        quantity = order.get('size', 0)
                        price = order.get('limitPrice') or order.get('stopPrice') or 0.0
                        custom_tag = order.get('customTag', '')
                        
                        print(f"{order_id:<12} {symbol:<8} {side:<6} {order_type:<8} {quantity:<10} ${price:<11.2f} {status:<10}")
                        if custom_tag:
                            print(f"             Tag: {custom_tag}")
                else:
                    print("❌ No open orders found")
            
            elif command_lower.startswith("close "):
                parts = command.split()
                if len(parts) < 2 or len(parts) > 3:
                    print("❌ Usage: close <position_id> [quantity]")
                    print("   Example: close 12345 1")
                    continue
                
                position_id = parts[1]
                quantity = int(parts[2]) if len(parts) == 3 else None
                
                # Confirm the close
                print(f"\n⚠️  CONFIRM CLOSE POSITION:")
                print(f"   Position ID: {position_id}")
                if quantity:
                    print(f"   Quantity: {quantity}")
                else:
                    print(f"   Quantity: Entire position")
                print(f"   Account: {bot.selected_account['name']}")
                
                confirm = input("   Confirm? (y/N): ").strip().lower()
                if confirm != 'y':
                    print("❌ Close cancelled")
                    continue
                
                # Close the position
                result = await bot.close_position(position_id, quantity)
                if "error" in result:
                    print(f"❌ Close failed: {result['error']}")
                else:
                    print(f"✅ Position closed successfully!")
                    print(f"   Position ID: {position_id}")
            
            elif command_lower.startswith("cancel "):
                parts = command.split()
                if len(parts) != 2:
                    print("❌ Usage: cancel <order_id>")
                    print("   Example: cancel 12345")
                    continue
                
                order_id = parts[1]
                
                # Confirm the cancel
                print(f"\n⚠️  CONFIRM CANCEL ORDER:")
                print(f"   Order ID: {order_id}")
                print(f"   Account: {bot.selected_account['name']}")
                
                confirm = input("   Confirm? (y/N): ").strip().lower()
                if confirm != 'y':
                    print("❌ Cancel cancelled")
                    continue
                
                # Cancel the order
                result = await bot.cancel_order(order_id)
                if "error" in result:
                    print(f"❌ Cancel failed: {result['error']}")
                else:
                    print(f"✅ Order cancelled successfully!")
                    print(f"   Order ID: {order_id}")
            
            elif command_lower.startswith("modify "):
                parts = command.split()
                if len(parts) < 2 or len(parts) > 4:
                    print("❌ Usage: modify <order_id> [new_quantity] [new_price]")
                    print("   Example: modify 12345 2 19500.00  (modify quantity and price)")
                    print("   Example: modify 12345 2           (modify quantity only)")
                    print("   Example: modify 12345 19500.00    (modify price only - for bracket orders)")
                    continue
                
                order_id = parts[1]
                new_quantity = None
                new_price = None
                
                # Parse arguments: could be quantity only, price only, or both
                if len(parts) == 3:
                    # Could be quantity or price - check if it looks like a price (has decimal)
                    arg = parts[2]
                    if '.' in arg:
                        # Likely a price (decimal number)
                        try:
                            new_price = float(arg)
                        except ValueError:
                            print("❌ Invalid price format")
                            continue
                    else:
                        # Likely a quantity (integer)
                        try:
                            new_quantity = int(arg)
                        except ValueError:
                            print("❌ Invalid quantity format")
                            continue
                elif len(parts) == 4:
                    # Both quantity and price
                    try:
                        new_quantity = int(parts[2])
                        new_price = float(parts[3])
                    except ValueError:
                        print("❌ Quantity must be an integer and price must be a decimal number")
                        continue
                
                # Confirm the modify
                print(f"\n⚠️  CONFIRM MODIFY ORDER:")
                print(f"   Order ID: {order_id}")
                if new_quantity is not None:
                    print(f"   New Quantity: {new_quantity}")
                if new_price is not None:
                    print(f"   New Price: ${new_price}")
                if new_quantity is None and new_price is None:
                    print("❌ Must specify either quantity or price (or both)")
                    continue
                print(f"   Account: {bot.selected_account['name']}")
                
                confirm = input("   Confirm? (y/N): ").strip().lower()
                if confirm != 'y':
                    print("❌ Modify cancelled")
                    continue
                
                # Get order type first to help with modification
                orders = await bot.get_open_orders()
                order_type = None
                for order in orders:
                    if str(order.get('id', '')) == str(order_id):
                        order_type = order.get('type')
                        break
                
                # Modify the order
                result = await bot.modify_order(order_id, new_quantity, new_price, order_type=order_type)
                if "error" in result:
                    print(f"❌ Modify failed: {result['error']}")
                    # Provide helpful context for bracket orders
                    if "bracket order" in result['error'].lower():
                        print(f"\n💡 Tip: Bracket orders (stop loss/take profit) attached to positions")
                        print(f"   cannot have their size changed. You can:")
                        print(f"   - Modify the price only: modify {order_id} <quantity> <new_price>")
                        print(f"   - Close the position to remove bracket orders: close <position_id>")
                else:
                    print(f"✅ Order modified successfully!")
                    print(f"   Order ID: {order_id}")
            
            elif command_lower.startswith("quote "):
                parts = command.split()
                if len(parts) != 2:
                    print("❌ Usage: quote <symbol>")
                    print("   Example: quote MNQ")
                    continue
                
                symbol = parts[1]
                
                # Get market quote
                result = await bot.get_market_quote(symbol)
                if "error" in result:
                    print(f"❌ Quote failed: {result['error']}")
                else:
                    print(f"\n📈 Market Quote for {symbol.upper()}:")
                    bid = result.get('bid', 'N/A')
                    ask = result.get('ask', 'N/A')
                    last = result.get('last', 'N/A')
                    volume = result.get('volume', 'N/A')
                    source = result.get('source', 'N/A')
                    print(f"   Bid: ${bid}")
                    print(f"   Ask: ${ask}")
                    print(f"   Last: ${last}")
                    print(f"   Volume: {volume}")
                    print(f"   Source: {source}")
            
            elif command_lower.startswith("depth "):
                parts = command.split()
                if len(parts) != 2:
                    print("❌ Usage: depth <symbol>")
                    print("   Example: depth MNQ")
                    continue
                
                symbol = parts[1]
                
                # Get market depth
                result = await bot.get_market_depth(symbol)
                if "error" in result:
                    print(f"❌ Depth failed: {result['error']}")
                else:
                    print(f"\n📊 Market Depth for {symbol.upper()}:")
                    bids = result.get('bids', [])
                    asks = result.get('asks', [])
                    
                    if not bids and not asks:
                        print("   No market depth data available")
                        print("   This might indicate:")
                        print("   - Market is closed")
                        print("   - Symbol is not actively traded")
                        print("   - Market depth data is not available for this symbol")
                    else:
                        if asks:
                            print("   Asks (Sell):")
                            for ask in asks[:5]:  # Show top 5
                                price = ask.get('price', 0)
                                size = ask.get('size', 0)
                                print(f"     ${price:.2f} x {size}")
                        
                        if bids:
                            print("   Bids (Buy):")
                            for bid in bids[:5]:  # Show top 5
                                price = bid.get('price', 0)
                                size = bid.get('size', 0)
                                print(f"     ${price:.2f} x {size}")
            
            elif command_lower.startswith("chart"):
                # Open chart in browser using TradingView Lightweight Charts
                # Usage: chart <symbol> [timeframe] [limit] [--realtime] [--backtest start_date end_date [--speed N]]
                parts = command.split()
                symbol = parts[1] if len(parts) > 1 else "MNQ"
                timeframe = parts[2] if len(parts) > 2 and not parts[2].startswith('--') else "5m"
                
                # Parse flags
                realtime = '--realtime' in parts
                backtest = '--backtest' in parts
                backtest_start = None
                backtest_end = None
                backtest_speed = 1.0
                limit = 100
                
                if backtest:
                    try:
                        backtest_idx = parts.index('--backtest')
                        if len(parts) > backtest_idx + 2:
                            backtest_start = parts[backtest_idx + 1]
                            backtest_end = parts[backtest_idx + 2]
                        else:
                            print("❌ Backtest mode requires start and end dates")
                            print("   Usage: chart MNQ 5m --backtest 2025-12-01 2025-12-07 [--speed 2.0]")
                            continue
                    except (ValueError, IndexError):
                        print("❌ Invalid backtest dates")
                        continue
                
                if '--speed' in parts:
                    try:
                        speed_idx = parts.index('--speed')
                        if len(parts) > speed_idx + 1:
                            backtest_speed = float(parts[speed_idx + 1])
                    except (ValueError, IndexError):
                        pass
                
                # Parse limit (if not in backtest mode)
                if not backtest:
                    for i, part in enumerate(parts):
                        if part.isdigit() and i > 0 and not parts[i-1].startswith('--'):
                            limit = int(part)
                            break
                
                try:
                    from gui.chart_html import open_chart_html_async
                    mode_str = ""
                    if realtime:
                        mode_str = " (Real-Time Mode)"
                    elif backtest:
                        mode_str = f" (Backtest Mode: {backtest_start} to {backtest_end}, {backtest_speed}x speed)"
                    
                    print(f"📊 Generating chart for {symbol} {timeframe} ({limit} bars){mode_str}...")
                    html_path = await open_chart_html_async(
                        self, 
                        symbol, 
                        timeframe, 
                        limit,
                        realtime=realtime,
                        backtest=backtest,
                        backtest_start=backtest_start,
                        backtest_end=backtest_end,
                        backtest_speed=backtest_speed
                    )
                    print(f"✅ Chart opened in browser: {html_path}")
                    print("💡 Chart is saved locally - you can open it anytime or share it")
                    if realtime:
                        print("🔄 Real-time updates are active - chart will update automatically")
                    elif backtest:
                        print("▶️  Backtest mode - click 'Start Backtest' button to begin replay")
                except Exception as e:
                    print(f"❌ Error generating chart: {e}")
                    import traceback
                    traceback.print_exc()
            
            elif command_lower.startswith("history "):
                parts = command.split()
                if len(parts) < 2:
                    print("❌ Usage: history <symbol> [timeframe] [limit|start_date end_date] [raw] [csv]")
                    print("   Bar count mode:")
                    print("     history MNQ 1m 50")
                    print("     history MNQ 5m 20 raw")
                    print("   Date range mode (gets ALL bars between dates):")
                    print("     history MNQ 1m 2024-11-01 2024-11-08")
                    print("     history MNQ 5m 2024-11-08T09:30 2024-11-08T16:00")
                    print("     history MNQ 1h 2024-11-01 2024-11-08 csv")
                    continue
                
                symbol = parts[1]
                # Check for raw and csv flags (can be anywhere after symbol)
                raw_flags = ("raw", "--raw", "-q", "-r")
                csv_flags = ("csv", "--csv", "-c", "--export")
                raw = any(p.lower() in raw_flags for p in parts[2:])
                csv = any(p.lower() in csv_flags for p in parts[2:])
                # Filter out flags when parsing other args
                clean_parts = [p for p in parts[2:] if p.lower() not in raw_flags and p.lower() not in csv_flags]
                timeframe = clean_parts[0] if len(clean_parts) > 0 else "1m"
                
                # Parse limit or date range
                start_dt = None
                end_dt = None
                limit = 20  # Default
                
                if len(clean_parts) >= 3:
                    # Might be date range mode: history MNQ 1m 2024-11-01 2024-11-08
                    try:
                        from datetime import datetime as dt_parser
                        # Try common date formats
                        date_formats = [
                            "%Y-%m-%d",           # 2024-11-01
                            "%Y-%m-%dT%H:%M",     # 2024-11-01T09:30
                            "%Y-%m-%dT%H:%M:%S",  # 2024-11-01T09:30:00
                            "%Y/%m/%d",           # 2024/11/01
                            "%m/%d/%Y",           # 11/01/2024
                        ]
                        start_dt = None
                        end_dt = None
                        for fmt in date_formats:
                            try:
                                start_dt = dt_parser.strptime(clean_parts[1], fmt)
                                end_dt = dt_parser.strptime(clean_parts[2], fmt)
                                break
                            except:
                                continue
                        
                        if start_dt and end_dt:
                            print(f"📅 Date range mode: {start_dt} to {end_dt}")
                        else:
                            # Not valid dates, try as bar count
                            limit = int(clean_parts[1])
                    except:
                        # Not a valid date, fall back to bar count
                        try:
                            limit = int(clean_parts[1])
                        except:
                            limit = 20
                elif len(clean_parts) >= 2:
                    # Bar count mode
                    try:
                        limit = int(clean_parts[1])
                    except:
                        limit = 20
                
                # Measure duration for performance insight
                import time as _t
                _t0 = _t.time()
                # Get historical data
                result = await bot.get_historical_data(symbol, timeframe, limit, 
                                                       start_time=start_dt, end_time=end_dt)
                _elapsed_ms = int((_t.time() - _t0) * 1000)
                
                # Check for error response
                if isinstance(result, dict) and "error" in result:
                    print(f"❌ Historical data fetch failed: {result['error']}")
                    print(f"   Elapsed time: {_elapsed_ms} ms")
                    print("   Check logs for detailed error information")
                elif not result or (isinstance(result, list) and len(result) == 0):
                    print(f"❌ No historical data available for {symbol}")
                    print(f"   Elapsed time: {_elapsed_ms} ms")
                    print("   This might indicate:")
                    print("   - Symbol is not available for historical data")
                    print("   - Timeframe is not supported")
                    print("   - Market is closed or data is not available")
                    print("   - Try a different symbol or timeframe")
                else:
                    # In date range mode, show ALL bars. In bar count mode, show last N bars
                    display_bars = result if start_dt is not None else result[-limit:]
                    
                    # Handle CSV export
                    if csv:
                        csv_filename = bot._export_to_csv(display_bars, symbol, timeframe)
                        if csv_filename:
                            print(f"✅ Exported {len(display_bars)} bars to {csv_filename}")
                            print(f"   Elapsed time: {_elapsed_ms} ms")
                        else:
                            print(f"❌ Failed to export CSV file")
                    
                    if raw:
                        for bar in display_bars:
                            time = bar.get('time', 'N/A')
                            open_price = bar.get('open', 0)
                            high = bar.get('high', 0)
                            low = bar.get('low', 0)
                            close = bar.get('close', 0)
                            volume = bar.get('volume', 0)
                            print(f"{time} {open_price} {high} {low} {close} {volume}")
                        print(f"fetched={len(display_bars)} elapsed_ms={_elapsed_ms}")
                    elif not csv:  # Only show formatted output if not CSV-only export
                        _count = len(display_bars)
                        mode_str = f"date range" if start_dt else f"last {limit} bars"
                        print(f"\n📊 Historical Data for {symbol.upper()} ({timeframe}) - {mode_str}, fetched={_count} in {_elapsed_ms} ms:")
                        # Only show psutil tip if slow AND psutil not installed
                        if _elapsed_ms > 10000:
                            try:
                                import psutil
                                _has_psutil = True
                            except ImportError:
                                _has_psutil = False
                            if not _has_psutil:
                                print(f"💡 Tip: Install 'psutil' to improve SDK performance: pip install psutil")
                            elif _elapsed_ms > 15000:
                                print(f"⚠️  Performance note: Consider caching SDK client connections for faster fetches")
                        # Align headers properly - Time column needs 26 chars for ISO timestamps with timezone
                        print(f"{'Time':<26} {'Open':<12} {'High':<12} {'Low':<12} {'Close':<12} {'Volume':<10}")
                        print("-" * 100)
                        for bar in display_bars:  # Show bars based on mode (all for date range, last N for bar count)
                            # Get timestamp - prioritize parsed 'time'/'timestamp' keys
                            time = bar.get('time') or bar.get('timestamp') or ''
                            
                            # Format timestamp nicely (convert UTC to ET for display)
                            if time:
                                if len(str(time)) > 19:
                                    try:
                                        # Parse ISO format and convert to ET for display
                                        from datetime import datetime as _dt
                                        import pytz
                                        dt = _dt.fromisoformat(str(time).replace('Z', '+00:00'))
                                        # Ensure timezone-aware (UTC)
                                        if dt.tzinfo is None:
                                            dt = dt.replace(tzinfo=timezone.utc)
                                        # Convert to ET
                                        et_tz = pytz.timezone('America/New_York')
                                        dt_et = dt.astimezone(et_tz)
                                        time = dt_et.strftime('%Y-%m-%d %H:%M:%S ET')
                                    except Exception as e:
                                        # Fallback: show UTC time if conversion fails
                                        try:
                                            dt = _dt.fromisoformat(str(time).replace('Z', '+00:00'))
                                            if dt.tzinfo is None:
                                                dt = dt.replace(tzinfo=timezone.utc)
                                            time = dt.strftime('%Y-%m-%d %H:%M:%S UTC')
                                        except:
                                            time = str(time)[:19] if len(str(time)) > 19 else str(time)
                                        logger.debug(f"Failed to convert timestamp to ET {time}: {e}")
                            else:
                                time = "N/A"
                                logger.debug(f"Empty timestamp in bar display. Bar keys: {list(bar.keys())}")
                            
                            open_price = bar.get('open', 0)
                            high = bar.get('high', 0)
                            low = bar.get('low', 0)
                            close = bar.get('close', 0)
                            volume = bar.get('volume', 0)
                            print(f"{time:<26} ${open_price:<11.2f} ${high:<11.2f} ${low:<11.2f} ${close:<11.2f} {volume:<10}")
            
            elif command_lower == "monitor":
                # Monitor position changes and adjust bracket orders
                result = await bot.monitor_position_changes()
                if "error" in result:
                    print(f"❌ Monitor failed: {result['error']}")
                else:
                    if result.get('message'):
                        print(f"ℹ️  {result['message']}")
                    else:
                        print(f"✅ Position monitoring completed!")
                        print(f"   Positions checked: {result.get('positions', 0)}")
                        print(f"   Adjustments made: {result.get('adjustments', 0)}")
                        if result.get('cancelled_orders', 0) > 0:
                            print(f"   Orphaned orders cancelled: {result.get('cancelled_orders', 0)}")
            
            elif command_lower == "bracket_monitor":
                # Monitor bracket positions and manage orders
                result = await bot.monitor_all_bracket_positions()
                if "error" in result:
                    print(f"❌ Bracket monitor failed: {result['error']}")
                else:
                    print(f"✅ Bracket monitoring completed!")
                    print(f"   Monitored positions: {result.get('monitored_positions', 0)}")
                    print(f"   Removed positions: {result.get('removed_positions', 0)}")
                    if result.get('results'):
                        for pos_id, pos_result in result['results'].items():
                            print(f"   Position {pos_id}: {pos_result.get('message', 'No changes')}")
            
            elif command_lower == "activate_monitor":
                # Manually activate monitoring for testing
                bot._monitoring_active = True
                bot._last_order_time = datetime.now()
                print("✅ Monitoring manually activated")
                print("   Monitoring will be active for 30 seconds")
            
            elif command_lower == "deactivate_monitor":
                # Manually deactivate monitoring
                bot._monitoring_active = False
                bot._last_order_time = None
                print("✅ Monitoring manually deactivated")
            
            elif command_lower == "check_fills":
                # Check for filled orders and send notifications
                result = await bot.check_order_fills()
                if "error" in result:
                    print(f"❌ Check fills failed: {result['error']}")
                else:
                    print(f"✅ Order fill check completed!")
                    print(f"   Orders checked: {result.get('checked_orders', 0)}")
                    print(f"   New fills found: {result.get('filled_orders', 0)}")
                    if result.get('new_fills'):
                        print(f"   Fill notifications sent for: {', '.join(result['new_fills'])}")
            
            elif command_lower == "auto_fills":
                # Enable automatic fill checking every 30 seconds
                print("✅ Automatic fill checking enabled")
                print("   Checking for fills every 30 seconds...")
                print("   Use 'stop_auto_fills' to disable")
                
                # Start background task for auto fills
                import asyncio
                asyncio.create_task(bot._auto_fill_checker())
            
            elif command_lower == "stop_auto_fills":
                # Disable automatic fill checking
                bot._auto_fills_enabled = False
                print("✅ Automatic fill checking disabled")
            
            elif command_lower == "clear_notifications":
                # Clear notification cache to re-check all orders
                bot._notified_orders.clear()
                bot._notification_warmup_done.clear()
                bot._notified_positions.clear()
                if hasattr(self, '_tracked_positions'):
                    bot._tracked_positions.clear()
                print("✅ Notification cache cleared - will re-check all orders and positions")
            
            elif command_lower == "test_fills":
                # Test fill checking with detailed output
                print("🔄 Testing fill checking...")
                result = await bot.check_order_fills()
                if "error" in result:
                    print(f"❌ Test failed: {result['error']}")
                else:
                    print(f"✅ Fill check completed!")
                    print(f"   Orders checked: {result.get('checked_orders', 0)}")
                    print(f"   New fills found: {result.get('filled_orders', 0)}")
                    if result.get('new_fills'):
                        print(f"   Fill notifications sent for: {', '.join(result['new_fills'])}")
                    else:
                        print("   No new fills found")
            
            elif command_lower == "account_info":
                # Get detailed account information
                result = await bot.get_account_info()
                if "error" in result:
                    print(f"❌ Account info failed: {result['error']}")
                else:
                    print(f"\n📊 Account Information:")
                    print(f"   Account ID: {result.get('id', 'N/A')}")
                    print(f"   Name: {result.get('name', 'N/A')}")
                    print(f"   Balance: ${result.get('balance', 0):,.2f}")
                    print(f"   Status: {result.get('status', 'unknown')}")
                    print(f"   Type: {result.get('type', 'unknown')}")
                    if 'note' in result:
                        print(f"\n   ℹ️  {result['note']}")
                    # Show any additional fields that were returned
                    extra_fields = {k: v for k, v in result.items() 
                                  if k not in ['id', 'name', 'balance', 'status', 'type', 'note', 'error']}
                    if extra_fields:
                        print(f"\n   Additional Info:")
                        for key, value in extra_fields.items():
                            print(f"   - {key}: {value}")
            
            elif command_lower == "account_state":
                # Update account tracker with current positions before displaying
                account_id = bot.selected_account['id'] if bot.selected_account else None
                if account_id:
                    try:
                        # Get current positions
                        positions = await bot.get_open_positions(account_id=account_id)
                        
                        # Prepare position data for tracker update
                        position_data = []
                        current_prices = {}
                        
                        for pos in positions:
                            symbol = pos.get('symbol', '')
                            if not symbol:
                                # Extract from contractId
                                contract_id = pos.get('contractId', '')
                                if '.' in contract_id:
                                    symbol = contract_id.split('.')[-2]
                            
                            # Get current price for the symbol
                            try:
                                quote = await bot.get_market_quote(symbol)
                                if 'error' not in quote and quote.get('last'):
                                    current_prices[symbol] = float(quote['last'])
                            except:
                                pass
                            
                            # Determine side
                            position_type = pos.get('type', 0)
                            side = 'LONG' if position_type == 1 else 'SHORT'
                            
                            position_data.append({
                                'symbol': symbol,
                                'qty': pos.get('size', 0),
                                'entry_price': pos.get('averagePrice', 0.0),
                                'side': side
                            })
                        
                        # Update unrealized PnL in tracker
                        bot.account_tracker.update_unrealised_pnl(
                            str(account_id),
                            position_data,
                            current_prices
                        )
                    except Exception as e:
                        logger.debug(f"Could not update account tracker with positions: {e}")
                
                # Show real-time account state from tracker
                state = bot.account_tracker.get_state()

                print(f"\n📊 Real-Time Account State:")
                print(f"   Account ID: {state['account_id']}")
                print(f"   Starting Balance: ${state['starting_balance']:,.2f}")
                print(f"   Current Balance: ${state['current_balance']:,.2f}")
                print(f"   Realized PnL: ${state['realized_pnl']:,.2f}")
                print(f"   Unrealized PnL: ${state['unrealized_pnl']:,.2f}")
                print(f"   Total PnL: ${state['total_pnl']:,.2f}")
                print(f"   Highest EOD Balance: ${state['highest_eod_balance']:,.2f}")
                print(f"\n   Open Positions: {state['position_count']}")
                if state['positions']:
                    print(f"   Position Details:")
                    for symbol, pos in state['positions'].items():
                        print(f"      {symbol}: {pos['quantity']} @ ${pos['entry_price']:.2f} (PnL: ${pos['unrealized_pnl']:.2f})")

                print(f"\n   Last Updated: {state['last_update']}")
                print(f"   📝 Note: Real-time tracking based on local state + API data")
            
            elif command_lower == "compliance":
                # Update account tracker with current positions before checking compliance
                account_id = bot.selected_account['id'] if bot.selected_account else None
                if account_id:
                    try:
                        # Get current positions
                        positions = await bot.get_open_positions(account_id=account_id)
                        
                        # Prepare position data for tracker update
                        position_data = []
                        current_prices = {}
                        
                        for pos in positions:
                            symbol = pos.get('symbol', '')
                            if not symbol:
                                # Extract from contractId
                                contract_id = pos.get('contractId', '')
                                if '.' in contract_id:
                                    symbol = contract_id.split('.')[-2]
                            
                            # Get current price for the symbol
                            try:
                                quote = await bot.get_market_quote(symbol)
                                if 'error' not in quote and quote.get('last'):
                                    current_prices[symbol] = float(quote['last'])
                            except:
                                pass
                            
                            # Determine side
                            position_type = pos.get('type', 0)
                            side = 'LONG' if position_type == 1 else 'SHORT'
                            
                            position_data.append({
                                'symbol': symbol,
                                'qty': pos.get('size', 0),
                                'entry_price': pos.get('averagePrice', 0.0),
                                'side': side
                            })
                        
                        # Update unrealized PnL in tracker
                        bot.account_tracker.update_unrealised_pnl(
                            str(account_id),
                            position_data,
                            current_prices
                        )
                    except Exception as e:
                        logger.debug(f"Could not update account tracker with positions: {e}")
                
                # Check compliance status
                compliance = bot.account_tracker.check_compliance()
                state = bot.account_tracker.get_state()
                
                print(f"\n✅ Compliance Status:")
                print(f"   Account Type: {state['account_type']}")
                print(f"   Is Compliant: {'✓ YES' if compliance['is_compliant'] else '❌ NO'}")
                
                print(f"\n   Daily Loss Limit (DLL):")
                if compliance['dll_limit']:
                    print(f"      Limit: ${compliance['dll_limit']:,.2f}")
                    print(f"      Used: ${compliance['dll_used']:,.2f}")
                    print(f"      Remaining: ${compliance['dll_remaining']:,.2f}")
                    print(f"      Status: {'✓ OK' if not compliance['dll_violated'] else '❌ VIOLATED'}")
                else:
                    print(f"      No DLL limit set")
                
                print(f"\n   Maximum Loss Limit (MLL):")
                if compliance['mll_limit']:
                    print(f"      Limit: ${compliance['mll_limit']:,.2f}")
                    print(f"      Used: ${compliance['mll_used']:,.2f}")
                    print(f"      Remaining: ${compliance['mll_remaining']:,.2f}")
                    print(f"      Status: {'✓ OK' if not compliance['mll_violated'] else '❌ VIOLATED'}")
                else:
                    print(f"      No MLL limit set")
                
                print(f"\n   Trailing Drawdown:")
                print(f"      Highest EOD Balance: ${state['highest_eod_balance']:,.2f}")
                print(f"      Current Balance: ${state['current_balance']:,.2f}")
                print(f"      Trailing Loss: ${compliance['trailing_loss']:,.2f}")
                
                if compliance['violations']:
                    print(f"\n   ⚠️  Violations:")
                    for violation in compliance['violations']:
                        print(f"      - {violation}")
            
            elif command_lower == "risk":
                # Show risk metrics
                state = bot.account_tracker.get_state()
                compliance = bot.account_tracker.check_compliance()
                
                print(f"\n⚠️  Risk Metrics:")
                print(f"   Account: {bot.selected_account.get('name', 'N/A')}")
                print(f"   Current Balance: ${state['current_balance']:,.2f}")
                print(f"   Total PnL: ${state['total_pnl']:,.2f} ({(state['total_pnl'] / state['starting_balance'] * 100):.2f}%)")
                
                print(f"\n   Daily Loss:")
                if compliance['dll_limit']:
                    dll_pct = (compliance['dll_used'] / compliance['dll_limit'] * 100) if compliance['dll_limit'] else 0
                    print(f"      Used: ${compliance['dll_used']:,.2f} / ${compliance['dll_limit']:,.2f} ({dll_pct:.1f}%)")
                    print(f"      Remaining: ${compliance['dll_remaining']:,.2f}")
                else:
                    print(f"      No limit set")
                
                print(f"\n   Maximum Loss:")
                if compliance['mll_limit']:
                    mll_pct = (compliance['mll_used'] / compliance['mll_limit'] * 100) if compliance['mll_limit'] else 0
                    print(f"      Used: ${compliance['mll_used']:,.2f} / ${compliance['mll_limit']:,.2f} ({mll_pct:.1f}%)")
                    print(f"      Remaining: ${compliance['mll_remaining']:,.2f}")
                else:
                    print(f"      No limit set")
                
                print(f"\n   Open Positions Risk:")
                print(f"      Position Count: {state['position_count']}")
                print(f"      Unrealized PnL: ${state['unrealized_pnl']:,.2f}")
                
                if state['position_count'] > 0:
                    total_exposure = sum(abs(pos['quantity'] * pos['entry_price']) for pos in state['positions'].values())
                    print(f"      Total Exposure: ${total_exposure:,.2f}")
                    if state['current_balance'] > 0:
                        leverage = total_exposure / state['current_balance']
                        print(f"      Leverage: {leverage:.2f}x")
            
            elif command_lower in ["drawdown", "max_loss"]:
                # Show max loss limit and drawdown information
                account_id = bot.selected_account['id'] if bot.selected_account else None
                if not account_id:
                    print("❌ No account selected")
                    continue
                
                # Get account info and balance
                account_info = await bot.get_account_info(account_id)
                balance = await bot.get_account_balance(account_id)
                
                if balance is None:
                    print("❌ Could not retrieve account balance")
                    continue
                
                # Extract max loss limit from account info if available
                max_loss_limit = None
                starting_balance = None
                daily_loss_limit = None
                
                # Account info might not be available due to API limitations
                if isinstance(account_info, dict) and "error" not in account_info:
                    max_loss_limit = account_info.get('maxLossLimit') or account_info.get('maxLoss') or account_info.get('maxDailyLoss')
                    starting_balance = account_info.get('startingBalance') or account_info.get('initialBalance') or account_info.get('accountBalance')
                    daily_loss_limit = account_info.get('dailyLossLimit') or account_info.get('maxDailyLoss')
                else:
                    # Use reasonable defaults based on account type
                    account_name = bot.selected_account.get('name', '')
                    account_type = bot.selected_account.get('type', '')
                    
                    # Estimate limits based on account type/name
                    if 'PRAC' in account_name or account_type == 'practice':
                        max_loss_limit = 2500.00  # Common practice account limit
                        daily_loss_limit = 1000.00
                    elif '50K' in account_name:
                        max_loss_limit = 2000.00
                        daily_loss_limit = 1000.00
                    elif '150K' in account_name:
                        max_loss_limit = 3000.00
                        daily_loss_limit = 1500.00
                    elif 'EXPRESS' in account_name:
                        max_loss_limit = 500.00
                        daily_loss_limit = 250.00
                    
                    logger.info(f"Account info API unavailable, using estimated limits for {account_name}")
                
                # Calculate drawdown
                drawdown = None
                drawdown_percent = None
                if starting_balance and starting_balance > 0:
                    drawdown = starting_balance - balance
                    drawdown_percent = (drawdown / starting_balance) * 100
                
                # Calculate remaining loss capacity
                remaining_loss = None
                if max_loss_limit:
                    remaining_loss = max_loss_limit - (drawdown if drawdown else 0)
                
                # Display information
                print(f"\n📉 Risk & Drawdown Information:")
                print(f"   Account: {bot.selected_account.get('name', account_id)}")
                print(f"   Current Balance: ${balance:,.2f}")
                
                if starting_balance:
                    print(f"   Starting Balance: ${starting_balance:,.2f}")
                
                if drawdown is not None:
                    print(f"   Drawdown: ${drawdown:,.2f} ({drawdown_percent:.2f}%)")
                    if drawdown > 0:
                        print(f"   ⚠️  Account is down ${drawdown:,.2f}")
                    else:
                        print(f"   ✅ Account is up ${abs(drawdown):,.2f}")
                
                if max_loss_limit:
                    print(f"   Max Loss Limit: ${max_loss_limit:,.2f}")
                    if remaining_loss is not None:
                        if remaining_loss > 0:
                            print(f"   Remaining Loss Capacity: ${remaining_loss:,.2f}")
                        else:
                            print(f"   ⚠️  Max loss limit reached!")
                
                if daily_loss_limit:
                    print(f"   Daily Loss Limit: ${daily_loss_limit:,.2f}")
                
                # Show note if using estimated limits
                if isinstance(account_info, dict) and "error" in account_info:
                    print(f"\n   ℹ️  Note: Loss limits are estimated based on account type")
                    print(f"   API endpoints for detailed account info are not available")
                
                # Show positions P&L if available
                try:
                    positions = await bot.get_open_positions(account_id)
                    if positions:
                        total_unrealized_pnl = sum(float(p.get('unrealizedPnl', 0)) for p in positions)
                        if total_unrealized_pnl != 0:
                            print(f"   Open Positions P&L: ${total_unrealized_pnl:,.2f}")
                except Exception:
                    pass
            
            elif command_lower == "trades" or command_lower.startswith("trades "):
                # List trades between optional dates, default to current session
                parts = command.split()
                start_date_str = None
                end_date_str = None
                
                if len(parts) > 1:
                    start_date_str = parts[1]
                if len(parts) > 2:
                    end_date_str = parts[2]
                
                account_id = bot.selected_account['id'] if bot.selected_account else None
                if not account_id:
                    print("❌ No account selected")
                    continue
                
                # Parse and normalize dates to ISO format
                def parse_date_to_iso(date_str):
                    """Parse various date formats and convert to ISO format."""
                    from datetime import datetime
                    import pytz
                    
                    # Common date formats
                    formats = [
                        '%m/%d/%y', '%m/%d/%Y',  # 12/15/25, 12/15/2025
                        '%m-%d-%y', '%m-%d-%Y',  # 12-15-25, 12-15-2025
                        '%Y-%m-%d',               # 2025-12-15
                        '%Y/%m/%d',               # 2025/12/15
                        '%m.%d.%y', '%m.%d.%Y',  # 12.15.25, 12.15.2025
                    ]
                    
                    for fmt in formats:
                        try:
                            dt = datetime.strptime(date_str, fmt)
                            # Set to UTC
                            dt = pytz.UTC.localize(dt)
                            return dt.isoformat()
                        except ValueError:
                            continue
                    
                    # If no format matched, try to parse as ISO (let it fail if invalid)
                    try:
                        dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
                        return dt.isoformat()
                    except:
                        return date_str  # Return as-is and let API handle error
                
                # If no dates provided, use current trading session
                if not start_date_str and not end_date_str:
                    from datetime import datetime
                    import pytz
                    session_start, session_end = bot._get_trading_session_dates()
                    start_date_str = session_start.isoformat()
                    end_date_str = session_end.isoformat()
                    print(f"\n📊 Trades for Current Trading Session:")
                    print(f"   Session: {session_start.strftime('%Y-%m-%d %H:%M:%S %Z')} to {session_end.strftime('%Y-%m-%d %H:%M:%S %Z')}")
                else:
                    # Parse user-provided dates
                    if start_date_str:
                        start_date_str = parse_date_to_iso(start_date_str)
                    if end_date_str:
                        end_date_str = parse_date_to_iso(end_date_str)
                    print(f"\n📊 Trades from {start_date_str} to {end_date_str}")
                
                # Get order history (filled orders only)
                orders = await bot.get_order_history(
                    account_id=account_id,
                    limit=1000,
                    start_timestamp=start_date_str,
                    end_timestamp=end_date_str
                )
                
                if orders:
                    # Consolidate individual orders into completed trades
                    consolidated_trades = bot._consolidate_orders_into_trades(orders)
                    
                    if consolidated_trades:
                        # Calculate statistics
                        stats = bot._calculate_trade_statistics(consolidated_trades)
                        
                        # Display consolidated trades
                        print(f"\n{'Symbol':<8} {'Side':<6} {'Qty':<5} {'Entry':<12} {'Exit':<12} {'P&L':<12} {'Entry Time':<20} {'Exit Time':<20}")
                        print("-" * 110)
                        for trade in consolidated_trades:
                            symbol = trade.get('symbol', 'N/A')
                            side = trade.get('side', 'N/A')
                            quantity = trade.get('quantity', 0)
                            entry_price = trade.get('entry_price', 0.0)
                            exit_price = trade.get('exit_price', 0.0)
                            pnl = trade.get('pnl', 0.0)
                            entry_time = trade.get('entry_time', 'N/A')
                            exit_time = trade.get('exit_time', 'N/A')
                            
                            # Format timestamps - handle both datetime objects and strings
                            if entry_time and entry_time != 'N/A':
                                try:
                                    from datetime import datetime
                                    if isinstance(entry_time, datetime):
                                        entry_time = entry_time.strftime('%Y-%m-%d %H:%M:%S')
                                    elif isinstance(entry_time, str):
                                        dt = datetime.fromisoformat(entry_time.replace('Z', '+00:00'))
                                        entry_time = dt.strftime('%Y-%m-%d %H:%M:%S')
                                except Exception as e:
                                    logger.debug(f"Could not format entry_time: {e}")
                                    entry_time = str(entry_time) if entry_time else 'N/A'
                            
                            if exit_time and exit_time != 'N/A':
                                try:
                                    from datetime import datetime
                                    if isinstance(exit_time, datetime):
                                        exit_time = exit_time.strftime('%Y-%m-%d %H:%M:%S')
                                    elif isinstance(exit_time, str):
                                        dt = datetime.fromisoformat(exit_time.replace('Z', '+00:00'))
                                        exit_time = dt.strftime('%Y-%m-%d %H:%M:%S')
                                except Exception as e:
                                    logger.debug(f"Could not format exit_time: {e}")
                                    exit_time = str(exit_time) if exit_time else 'N/A'
                            
                            # Color code P&L (green for positive, red for negative)
                            pnl_str = f"${pnl:>11.2f}"
                            
                            print(f"{symbol:<8} {side:<6} {quantity:<5} ${entry_price:<11.2f} ${exit_price:<11.2f} {pnl_str:<12} {entry_time:<20} {exit_time:<20}")
                        
                        # Display statistics
                        print(f"\n📈 Trade Statistics:")
                        print(f"   Total Trades: {stats['total_trades']}")
                        print(f"   Winning: {stats['winning_trades']} | Losing: {stats['losing_trades']} | Break Even: {stats['break_even_trades']}")
                        print(f"   Win Rate: {stats['win_rate']}%")
                        print(f"   Total P&L: ${stats['total_pnl']:,.2f}")
                        print(f"   Average P&L: ${stats['average_pnl']:,.2f}")
                        if stats['average_win'] > 0:
                            print(f"   Average Win: ${stats['average_win']:,.2f}")
                        if stats['average_loss'] < 0:
                            print(f"   Average Loss: ${stats['average_loss']:,.2f}")
                        if stats['largest_win'] > 0:
                            print(f"   Largest Win: ${stats['largest_win']:,.2f}")
                        if stats['largest_loss'] < 0:
                            print(f"   Largest Loss: ${stats['largest_loss']:,.2f}")
                    else:
                        print("\n⚠️  No completed trades found")
                        print(f"   Found {len(orders)} individual filled orders, but no matching entry/exit pairs")
                        print("   Trades are shown only when entry and exit orders can be matched using FIFO.")
                else:
                    print("❌ No orders found for this period")
            
            else:
                # Fallback: try the modular command parser so newer commands work in interactive mode
                try:
                    from core.cli_command_parser import CLICommandParser
                    parser = CLICommandParser(self)
                    resp = await parser.execute_command(command)
                    if resp.get("success"):
                        result = resp.get("result")
                        if isinstance(result, dict):
                            import json
                            print(json.dumps(result, indent=2))
                        else:
                            print(result)
                    else:
                        print("❌ Unknown command. Available commands:")
                        print("   trade, limit, bracket, native_bracket, stop_bracket, stop, trail, positions, orders,")
                        print("   close, cancel, modify, quote, depth, history, monitor, flatten, contracts, accounts,")
                        print("   switch_account, account_info, account_state, compliance, risk, drawdown, trades,")
                        print("   strategies, backtest, master, gui, help, quit")
                        print("   Use ↑/↓ arrows for command history, Tab for completion")
                        print("   Type 'help' for detailed command information")
                except Exception as e:
                    print(f"❌ Command error: {e}")
                
        except KeyboardInterrupt:
            print("\n👋 Exiting trading interface.")
            break
        except Exception as e:
            print(f"❌ Error: {str(e)}")
            logger.error(f"Trading interface error: {str(e)}")
