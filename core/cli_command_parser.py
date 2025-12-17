"""
CLI Command Parser for Trading Bot

Parses and executes commands from CLI arguments for non-interactive operation.
"""

import logging
import asyncio
from typing import Dict, Any, Optional, List
import re

logger = logging.getLogger(__name__)


class CLICommandParser:
    """Parse and execute CLI commands."""
    
    def __init__(self, trading_bot):
        """Initialize command parser with trading bot instance."""
        self.trading_bot = trading_bot
        self.command_handlers = {
            'place_order': self._handle_place_order,
            'market': self._handle_place_order,
            'limit': self._handle_limit_order,
            'bracket': self._handle_bracket_order,
            'stop_bracket': self._handle_stop_bracket,
            'stop': self._handle_stop_order,
            'trail': self._handle_trailing_stop,
            'positions': self._handle_positions,
            'orders': self._handle_orders,
            'close': self._handle_close,
            'cancel': self._handle_cancel,
            'modify': self._handle_modify,
            'quote': self._handle_quote,
            'depth': self._handle_depth,
            'history': self._handle_history,
            'chart': self._handle_chart,
            'master': self._handle_master_gui,
            'gui': self._handle_master_gui,
            'flatten': self._handle_flatten,
            'accounts': self._handle_accounts,
            'switch_account': self._handle_switch_account,
            'account_info': self._handle_account_info,
            'trades': self._handle_trades,
            'strategy_start': self._handle_strategy_start,
            'strategy_stop': self._handle_strategy_stop,
            'strategy_status': self._handle_strategy_status,
            'disable_strategy': self._handle_disable_strategy,
            'strategies': self._handle_strategies,  # Added: multi-command handler
        }
    
    async def execute_command(self, command_str: str) -> Dict[str, Any]:
        """
        Execute a command string.
        
        Args:
            command_str: Command string (e.g., "stop_bracket mnq buy 1 25000 24980 25020")
        
        Returns:
            Dict with success status and result/error
        """
        if not command_str:
            return {"success": False, "error": "Empty command"}
        
        # Parse command
        parts = command_str.strip().split()
        if not parts:
            return {"success": False, "error": "Empty command"}
        
        command_name = parts[0].lower()
        args = parts[1:] if len(parts) > 1 else []
        
        # Find handler
        handler = self.command_handlers.get(command_name)
        if not handler:
            return {
                "success": False,
                "error": f"Unknown command: {command_name}",
                "available_commands": list(self.command_handlers.keys())
            }
        
        try:
            result = await handler(args)
            return {"success": True, "result": result}
        except Exception as e:
            logger.error(f"Error executing command {command_name}: {e}")
            return {"success": False, "error": str(e)}
    
    async def _handle_place_order(self, args: List[str]) -> Dict[str, Any]:
        """Handle place_order/market command: market SYMBOL SIDE QUANTITY"""
        if len(args) < 3:
            return {"error": "Usage: market SYMBOL SIDE QUANTITY [account_id]"}
        
        symbol = args[0].upper()
        side = args[1].upper()
        quantity = int(args[2])
        account_id = args[3] if len(args) > 3 else None
        
        result = await self.trading_bot.place_market_order(
            symbol=symbol,
            side=side,
            quantity=quantity,
            account_id=account_id
        )
        return result
    
    async def _handle_limit_order(self, args: List[str]) -> Dict[str, Any]:
        """Handle limit order: limit SYMBOL SIDE QUANTITY PRICE"""
        if len(args) < 4:
            return {"error": "Usage: limit SYMBOL SIDE QUANTITY PRICE [account_id]"}
        
        symbol = args[0].upper()
        side = args[1].upper()
        quantity = int(args[2])
        price = float(args[3])
        account_id = args[4] if len(args) > 4 else None
        
        result = await self.trading_bot.place_market_order(
            symbol=symbol,
            side=side,
            quantity=quantity,
            limit_price=price,
            order_type="limit",
            account_id=account_id
        )
        return result
    
    async def _handle_bracket_order(self, args: List[str]) -> Dict[str, Any]:
        """Handle bracket order: bracket SYMBOL SIDE QUANTITY STOP_LOSS TAKE_PROFIT"""
        if len(args) < 5:
            return {"error": "Usage: bracket SYMBOL SIDE QUANTITY STOP_LOSS TAKE_PROFIT [account_id]"}
        
        symbol = args[0].upper()
        side = args[1].upper()
        quantity = int(args[2])
        stop_loss = float(args[3])
        take_profit = float(args[4])
        account_id = args[5] if len(args) > 5 else None
        
        result = await self.trading_bot.create_bracket_order(
            symbol=symbol,
            side=side,
            quantity=quantity,
            stop_loss_price=stop_loss,
            take_profit_price=take_profit,
            account_id=account_id
        )
        return result
    
    async def _handle_stop_bracket(self, args: List[str]) -> Dict[str, Any]:
        """Handle stop bracket: stop_bracket SYMBOL SIDE QUANTITY ENTRY STOP_LOSS TAKE_PROFIT"""
        if len(args) < 6:
            return {"error": "Usage: stop_bracket SYMBOL SIDE QUANTITY ENTRY STOP_LOSS TAKE_PROFIT [account_id]"}
        
        symbol = args[0].upper()
        side = args[1].upper()
        quantity = int(args[2])
        entry = float(args[3])
        stop_loss = float(args[4])
        take_profit = float(args[5])
        account_id = args[6] if len(args) > 6 else None
        
        result = await self.trading_bot.place_oco_bracket_with_stop_entry(
            symbol=symbol,
            side=side,
            quantity=quantity,
            entry_price=entry,
            stop_loss_price=stop_loss,
            take_profit_price=take_profit,
            account_id=account_id
        )
        return result
    
    async def _handle_stop_order(self, args: List[str]) -> Dict[str, Any]:
        """Handle stop order: stop SYMBOL SIDE QUANTITY STOP_PRICE"""
        if len(args) < 4:
            return {"error": "Usage: stop SYMBOL SIDE QUANTITY STOP_PRICE [account_id]"}
        
        symbol = args[0].upper()
        side = args[1].upper()
        quantity = int(args[2])
        stop_price = float(args[3])
        account_id = args[4] if len(args) > 4 else None
        
        result = await self.trading_bot.place_stop_order(
            symbol=symbol,
            side=side,
            quantity=quantity,
            stop_price=stop_price,
            account_id=account_id
        )
        return result
    
    async def _handle_trailing_stop(self, args: List[str]) -> Dict[str, Any]:
        """Handle trailing stop: trail SYMBOL SIDE QUANTITY TRAIL_AMOUNT"""
        if len(args) < 4:
            return {"error": "Usage: trail SYMBOL SIDE QUANTITY TRAIL_AMOUNT [account_id]"}
        
        symbol = args[0].upper()
        side = args[1].upper()
        quantity = int(args[2])
        trail_amount = float(args[3])
        account_id = args[4] if len(args) > 4 else None
        
        result = await self.trading_bot.place_trailing_stop_order(
            symbol=symbol,
            side=side,
            quantity=quantity,
            trail_amount=trail_amount,
            account_id=account_id
        )
        return result
    
    async def _handle_positions(self, args: List[str]) -> Dict[str, Any]:
        """Handle positions command: positions [account_id]"""
        account_id = args[0] if args else None
        result = await self.trading_bot.get_positions(account_id=account_id)
        return result
    
    async def _handle_orders(self, args: List[str]) -> Dict[str, Any]:
        """Handle orders command: orders [account_id]"""
        account_id = args[0] if args else None
        result = await self.trading_bot.get_open_orders(account_id=account_id)
        return result
    
    async def _handle_close(self, args: List[str]) -> Dict[str, Any]:
        """Handle close command: close SYMBOL [account_id]"""
        if not args:
            return {"error": "Usage: close SYMBOL [account_id]"}
        
        symbol = args[0].upper()
        account_id = args[1] if len(args) > 1 else None
        result = await self.trading_bot.close_position(symbol=symbol, account_id=account_id)
        return result
    
    async def _handle_cancel(self, args: List[str]) -> Dict[str, Any]:
        """Handle cancel command: cancel ORDER_ID [account_id]"""
        if not args:
            return {"error": "Usage: cancel ORDER_ID [account_id]"}
        
        order_id = args[0]
        account_id = args[1] if len(args) > 1 else None
        result = await self.trading_bot.cancel_order(order_id=order_id, account_id=account_id)
        return result
    
    async def _handle_modify(self, args: List[str]) -> Dict[str, Any]:
        """Handle modify command: modify ORDER_ID PRICE [account_id]"""
        if len(args) < 2:
            return {"error": "Usage: modify ORDER_ID PRICE [account_id]"}
        
        order_id = args[0]
        price = float(args[1])
        account_id = args[2] if len(args) > 2 else None
        result = await self.trading_bot.modify_order(order_id=order_id, new_price=price, account_id=account_id)
        return result
    
    async def _handle_quote(self, args: List[str]) -> Dict[str, Any]:
        """Handle quote command: quote SYMBOL"""
        if not args:
            return {"error": "Usage: quote SYMBOL"}
        
        symbol = args[0].upper()
        result = await self.trading_bot.get_market_quote(symbol)
        return result
    
    async def _handle_depth(self, args: List[str]) -> Dict[str, Any]:
        """Handle depth command: depth SYMBOL"""
        if not args:
            return {"error": "Usage: depth SYMBOL"}
        
        symbol = args[0].upper()
        result = await self.trading_bot.get_market_depth(symbol)
        return result
    
    async def _handle_history(self, args: List[str]) -> Dict[str, Any]:
        """Handle history command: history <symbol> [timeframe] [limit] [raw] [csv]
        
        Supports:
        - history MNQ                    -> symbol=MNQ, timeframe=1m, limit=20
        - history MNQ 5m                 -> symbol=MNQ, timeframe=5m, limit=20
        - history MNQ 5m 30              -> symbol=MNQ, timeframe=5m, limit=30
        - history MNQ 1m 50 raw          -> symbol=MNQ, timeframe=1m, limit=50, raw=True
        - history MNQ 1h 100 csv         -> symbol=MNQ, timeframe=1h, limit=100, csv=True
        """
        if not args:
            return {"error": "Usage: history <symbol> [timeframe] [limit] [raw] [csv]"}
        
        symbol = args[0].upper()
        
        # Check for raw and csv flags (can be anywhere after symbol)
        raw_flags = ("raw", "--raw", "-q", "-r")
        csv_flags = ("csv", "--csv", "-c", "--export")
        raw = any(p.lower() in raw_flags for p in args[1:])
        csv = any(p.lower() in csv_flags for p in args[1:])
        
        # Filter out flags when parsing other args
        clean_parts = [p for p in args[1:] if p.lower() not in raw_flags and p.lower() not in csv_flags]
        
        # Parse timeframe and limit
        import re
        timeframe = "1m"  # default
        limit = 20  # default
        
        if len(clean_parts) == 0:
            # No additional args - use defaults
            pass
        elif len(clean_parts) == 1:
            # Single arg - could be timeframe or limit
            if re.match(r'^\d+[smhdwM]$', clean_parts[0]):
                # It's a timeframe
                timeframe = clean_parts[0]
            else:
                # Try to parse as limit (no timeframe provided)
                try:
                    limit = int(clean_parts[0])
                except ValueError:
                    return {"error": f"Invalid argument: {clean_parts[0]}. Expected timeframe (e.g., 1m, 5m) or limit (number)."}
        else:
            # Two or more args - first should be timeframe, second should be limit
            if re.match(r'^\d+[smhdwM]$', clean_parts[0]):
                # First is timeframe
                timeframe = clean_parts[0]
                # Second should be limit
                try:
                    limit = int(clean_parts[1])
                except ValueError:
                    return {"error": f"Invalid limit: {clean_parts[1]}. Expected a number."}
            else:
                # First doesn't look like timeframe - might be limit without timeframe
                # But this is ambiguous, so require timeframe format
                return {"error": f"Invalid timeframe format: {clean_parts[0]}. Expected format: 1m, 5m, 1h, etc."}
        
        # Get historical data
        result = await self.trading_bot.get_historical_data(
            symbol=symbol,
            timeframe=timeframe,
            limit=limit
        )
        
        # Handle output formatting
        if raw:
            # Return raw data for tab-separated output
            return {"raw": True, "data": result}
        elif csv:
            # Export to CSV (handled by trading_bot if it has the method)
            if hasattr(self.trading_bot, '_export_to_csv'):
                csv_filename = self.trading_bot._export_to_csv(result, symbol, timeframe)
                return {"csv": True, "filename": csv_filename, "bars": len(result)}
            else:
                return {"error": "CSV export not available"}
        
        return {"bars": result, "count": len(result) if isinstance(result, list) else 0}
    
    async def _handle_chart(self, args: List[str]) -> Dict[str, Any]:
        """Handle chart command: chart <symbol> [timeframe] [limit] [--realtime] [--backtest start_date end_date [--speed N]]
        
        Supports:
        - chart MNQ                    -> symbol=MNQ, timeframe=5m, limit=100
        - chart MNQ 5m                -> symbol=MNQ, timeframe=5m, limit=100
        - chart MNQ 5m 300            -> symbol=MNQ, timeframe=5m, limit=300
        - chart MNQ 5m 300 --realtime -> symbol=MNQ, timeframe=5m, limit=300, realtime=True
        - chart MNQ 5m --backtest 2025-12-01 2025-12-07 --speed 2.0 -> backtest mode
        """
        if not args:
            symbol = "MNQ"
        else:
            symbol = args[0].upper()
        
        # Parse flags first
        realtime = '--realtime' in args
        backtest = '--backtest' in args
        backtest_start = None
        backtest_end = None
        backtest_speed = 1.0
        
        # Parse backtest dates if in backtest mode
        if backtest:
            try:
                backtest_idx = args.index('--backtest')
                if len(args) > backtest_idx + 2:
                    backtest_start = args[backtest_idx + 1]
                    backtest_end = args[backtest_idx + 2]
                else:
                    return {"error": "Backtest mode requires start and end dates. Usage: chart MNQ 5m --backtest 2025-12-01 2025-12-07 [--speed 2.0]"}
            except (ValueError, IndexError):
                return {"error": "Invalid backtest dates"}
        
        # Parse backtest speed if provided
        if '--speed' in args:
            try:
                speed_idx = args.index('--speed')
                if len(args) > speed_idx + 1:
                    backtest_speed = float(args[speed_idx + 1])
            except (ValueError, IndexError):
                pass
        
        # Filter out flags and their arguments when parsing timeframe and limit
        flag_set = {'--realtime', '--backtest', '--speed'}
        clean_parts = []
        i = 1  # Start after symbol
        while i < len(args):
            if args[i] in flag_set:
                if args[i] == '--backtest':
                    # Skip --backtest and its two date arguments
                    i += 3
                    continue
                elif args[i] == '--speed':
                    # Skip --speed and its value
                    i += 2
                    continue
                else:
                    # Skip --realtime
                    i += 1
                    continue
            clean_parts.append(args[i])
            i += 1
        
        # Parse timeframe and limit from clean_parts
        import re
        timeframe = "5m"  # default
        limit = 100  # default
        
        if len(clean_parts) == 0:
            # No additional args - use defaults
            pass
        elif len(clean_parts) == 1:
            # Single arg - could be timeframe or limit
            if re.match(r'^\d+[smhdwM]$', clean_parts[0]):
                # It's a timeframe
                timeframe = clean_parts[0]
            else:
                # Try to parse as limit (no timeframe provided)
                try:
                    limit = int(clean_parts[0])
                except ValueError:
                    pass  # Ignore invalid values, use defaults
        else:
            # Two or more args - first should be timeframe, second should be limit
            if re.match(r'^\d+[smhdwM]$', clean_parts[0]):
                # First is timeframe
                timeframe = clean_parts[0]
                # Second should be limit
                try:
                    limit = int(clean_parts[1])
                except ValueError:
                    pass  # Ignore invalid limit, use default
            else:
                # First doesn't look like timeframe - might be limit without timeframe
                # But this is ambiguous, so try to parse as limit
                try:
                    limit = int(clean_parts[0])
                except ValueError:
                    pass
        
        # Generate chart
        try:
            from gui.chart_html import open_chart_html_async
            html_path = await open_chart_html_async(
                self.trading_bot,
                symbol,
                timeframe,
                limit,
                realtime=realtime,
                backtest=backtest,
                backtest_start=backtest_start,
                backtest_end=backtest_end,
                backtest_speed=backtest_speed
            )
            result = {
                "success": True,
                "html_path": html_path,
                "symbol": symbol,
                "timeframe": timeframe,
                "limit": limit,
                "realtime": realtime,
                "backtest": backtest
            }
            
            # If realtime mode, signal that bot should keep running
            if realtime:
                result["keep_running"] = True
                result["message"] = "Chart opened with real-time updates. Bot will keep running. Press Ctrl+C to stop."
            
            return result
        except Exception as e:
            logger.error(f"Error generating chart: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return {"error": f"Failed to generate chart: {str(e)}"}
    
    async def _handle_master_gui(self, args: List[str]) -> Dict[str, Any]:
        """Handle master GUI command: master [symbol] [timeframe] [limit]"""
        symbol = 'MNQ'
        timeframe = '5m'
        limit = 300
        
        # Parse optional arguments
        if args:
            symbol = args[0].upper()
        if len(args) > 1:
            timeframe = args[1].lower()
        if len(args) > 2:
            try:
                limit = int(args[2])
            except ValueError:
                pass
        
        # Start chart server with master GUI
        try:
            from gui.chart_html import _start_chart_server
            import webbrowser
            
            # Start server
            port = await _start_chart_server(self.trading_bot, symbol)
            
            # Open master GUI in browser
            master_url = f"http://127.0.0.1:{port}/master"
            webbrowser.open(master_url)
            
            result = {
                "success": True,
                "url": master_url,
                "port": port,
                "symbol": symbol,
                "message": "Master GUI opened in browser. Bot will keep running. Press Ctrl+C to stop."
            }
            
            # Return keep_running flag so bot stays alive
            result["keep_running"] = True
            
            return result
        except Exception as e:
            logger.error(f"Error opening master GUI: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return {"error": f"Failed to open master GUI: {e}"}
    
    async def _handle_flatten(self, args: List[str]) -> Dict[str, Any]:
        """Handle flatten command: flatten [SYMBOL] [account_id]"""
        symbol = args[0].upper() if args else None
        account_id = args[1] if len(args) > 1 else None
        if symbol:
            result = await self.trading_bot.flatten_symbol(symbol, account_id=account_id)
        else:
            result = await self.trading_bot.flatten_all_positions(account_id=account_id)
        return result
    
    async def _handle_accounts(self, args: List[str]) -> Dict[str, Any]:
        """Handle accounts command: accounts"""
        result = await self.trading_bot.list_accounts()
        return {"accounts": result}
    
    async def _handle_switch_account(self, args: List[str]) -> Dict[str, Any]:
        """Handle switch_account command: switch_account ACCOUNT_ID"""
        if not args:
            return {"error": "Usage: switch_account ACCOUNT_ID"}
        
        account_id = args[0]
        result = await self.trading_bot.switch_account(account_id)
        return result
    
    async def _handle_account_info(self, args: List[str]) -> Dict[str, Any]:
        """Handle account_info command: account_info [account_id]"""
        account_id = args[0] if args else None
        result = await self.trading_bot.get_account_info(account_id=account_id)
        return result
    
    async def _handle_trades(self, args: List[str]) -> Dict[str, Any]:
        """Handle trades command: trades [start_date] [end_date]
        
        If no dates provided, uses current trading session.
        """
        start_date_str = args[0] if len(args) > 0 else None
        end_date_str = args[1] if len(args) > 1 else None
        
        account_id = None
        if isinstance(self.trading_bot.selected_account, dict):
            account_id = self.trading_bot.selected_account.get('id')
        else:
            account_id = self.trading_bot.selected_account
        
        if not account_id:
            return {"error": "No account selected"}
        
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
            if hasattr(self.trading_bot, '_get_trading_session_dates'):
                session_start, session_end = self.trading_bot._get_trading_session_dates()
                start_date_str = session_start.isoformat()
                end_date_str = session_end.isoformat()
        else:
            # Parse user-provided dates
            if start_date_str:
                start_date_str = parse_date_to_iso(start_date_str)
            if end_date_str:
                end_date_str = parse_date_to_iso(end_date_str)
        
        # Get order history
        orders = await self.trading_bot.get_order_history(
            account_id=account_id,
            limit=1000,
            start_timestamp=start_date_str,
            end_timestamp=end_date_str
        )
        
        if orders and hasattr(self.trading_bot, '_consolidate_orders_into_trades'):
            consolidated_trades = self.trading_bot._consolidate_orders_into_trades(orders)
            if consolidated_trades and hasattr(self.trading_bot, '_calculate_trade_statistics'):
                stats = self.trading_bot._calculate_trade_statistics(consolidated_trades)
                return {
                    "trades": consolidated_trades,
                    "statistics": stats
                }
            return {"trades": consolidated_trades}
        
        return {"orders": orders, "count": len(orders) if isinstance(orders, list) else 0}
    
    async def _handle_strategy_start(self, args: List[str]) -> Dict[str, Any]:
        """Handle strategy_start command: strategy_start NAME [--symbols=SYM1,SYM2]"""
        if not args:
            return {"error": "Usage: strategy_start NAME [--symbols=SYM1,SYM2]"}
        
        strategy_name = args[0]
        symbols = None
        
        for arg in args[1:]:
            if arg.startswith('--symbols='):
                symbols = arg.split('=', 1)[1].split(',')
        
        if hasattr(self.trading_bot, 'strategy_manager'):
            success, message = await self.trading_bot.strategy_manager.start_strategy(strategy_name, symbols=symbols)
            return {"success": success, "message": message}
        else:
            return {"error": "Strategy manager not available"}
    
    async def _handle_strategy_stop(self, args: List[str]) -> Dict[str, Any]:
        """Handle strategy_stop command: strategy_stop NAME"""
        if not args:
            return {"error": "Usage: strategy_stop NAME"}
        
        strategy_name = args[0]
        if hasattr(self.trading_bot, 'strategy_manager'):
            success, message = await self.trading_bot.strategy_manager.stop_strategy(strategy_name)
            return {"success": success, "message": message}
        else:
            return {"error": "Strategy manager not available"}
    
    async def _handle_strategy_status(self, args: List[str]) -> Dict[str, Any]:
        """Handle strategy_status command: strategy_status"""
        if hasattr(self.trading_bot, 'strategy_manager'):
            status = await self.trading_bot.strategy_manager.get_all_strategy_status()
            return {"strategies": status}
        else:
            return {"error": "Strategy manager not available"}
    
    async def _handle_disable_strategy(self, args: List[str]) -> Dict[str, Any]:
        """Handle disable_strategy command: disable_strategy NAME1,NAME2"""
        if not args:
            return {"error": "Usage: disable_strategy NAME1,NAME2"}

        strategy_names = args[0].split(',')
        account_id = self.trading_bot.selected_account.get('id') if isinstance(self.trading_bot.selected_account, dict) else self.trading_bot.selected_account

        if not account_id:
            return {"error": "No account selected"}

        if hasattr(self.trading_bot, 'db') and self.trading_bot.db:
            from datetime import datetime, timezone
            results = []
            for name in strategy_names:
                result = self.trading_bot.db.save_strategy_state(
                    account_id=account_id,
                    strategy_name=name.strip(),
                    enabled=False,
                    last_stopped=datetime.now(timezone.utc)
                )
                results.append({"strategy": name.strip(), "success": result})
            return {"results": results}
        else:
            return {"error": "Database not available"}
    
    async def _handle_strategies(self, args: List[str]) -> Dict[str, Any]:
        """Handle strategies commands: strategies list|status|start|stop|start_all|stop_all"""
        if not args:
            return {"error": "Usage: strategies <command> [args]"}
        
        subcommand = args[0].lower()
        
        if not hasattr(self.trading_bot, 'strategy_manager'):
            return {"error": "Strategy manager not available"}
        
        strategy_manager = self.trading_bot.strategy_manager
        
        if subcommand == "list":
            # List all available strategies
            strategies = []
            for name in strategy_manager.available_strategies.keys():
                status = "loaded" if name in strategy_manager.strategies else "available"
                enabled = strategy_manager.strategies[name].config.enabled if name in strategy_manager.strategies else False
                strategies.append({
                    "name": name,
                    "status": status,
                    "enabled": enabled
                })
            return {"success": True, "strategies": strategies}
        
        elif subcommand == "status":
            # Show status of all strategies
            status = strategy_manager.get_status()
            return {"success": True, "status": status}
        
        elif subcommand == "start":
            # Start a specific strategy
            if len(args) < 2:
                return {"error": "Usage: strategies start <name> [symbols]"}
            strategy_name = args[1]
            symbols = args[2].split(',') if len(args) > 2 else None
            success, message = await strategy_manager.start_strategy(strategy_name, symbols)
            return {"success": success, "message": message}
        
        elif subcommand == "stop":
            # Stop a specific strategy
            if len(args) < 2:
                return {"error": "Usage: strategies stop <name>"}
            strategy_name = args[1]
            success, message = await strategy_manager.stop_strategy(strategy_name)
            return {"success": success, "message": message}
        
        elif subcommand == "start_all":
            # Start all enabled strategies
            results = await strategy_manager.start_all_strategies()
            return {"success": True, "results": results}
        
        elif subcommand == "stop_all":
            # Stop all strategies
            results = await strategy_manager.stop_all_strategies()
            return {"success": True, "results": results}
        
        else:
            return {"error": f"Unknown strategies subcommand: {subcommand}. Available: list, status, start, stop, start_all, stop_all"}
