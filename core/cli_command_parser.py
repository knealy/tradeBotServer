"""
CLI Command Parser for Trading Bot

Parses and executes commands from CLI arguments for non-interactive operation.
"""

import logging
import asyncio
import os
import json
from typing import Dict, Any, Optional, List, Tuple
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
            'analyze_date': self._handle_analyze_date,
            'analyze': self._handle_analyze_date,
            'simulate': self._handle_simulate,
            'backtest_date': self._handle_simulate,
            'backtest_range': self._handle_backtest_range,
            'replay_session': self._handle_replay_session,
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

    async def parse_and_execute(self, command_str: str) -> Any:
        """
        Backwards-compatible helper used by GUI / legacy callers.
        Returns the handler result on success, raises on failure.
        """
        resp = await self.execute_command(command_str)
        if resp.get("success"):
            return resp.get("result")
        raise RuntimeError(resp.get("error") or "Command failed")
    
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
        result = await self.trading_bot.get_open_positions(account_id=account_id)
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
            
            # Ensure contracts are loaded BEFORE starting GUI
            logger.info("📋 Pre-loading contracts for GUI...")
            try:
                contracts = await self.trading_bot.get_available_contracts(use_cache=False)
                logger.info(f"✅ Pre-loaded {len(contracts)} contracts")
            except Exception as contract_err:
                logger.warning(f"⚠️ Could not pre-load contracts: {contract_err}")
            
            # Start server with symbol and timeframe
            port = await _start_chart_server(self.trading_bot, symbol, timeframe)
            
            # Open master GUI in browser
            master_url = f"http://127.0.0.1:{port}/master"
            webbrowser.open(master_url)
            
            result = {
                "success": True,
                "url": master_url,
                "port": port,
                "symbol": symbol,
                "timeframe": timeframe,
                "message": "Master GUI opened in browser. Bot will keep running. Press Ctrl+C to stop.",
                "selected_account": self.trading_bot.selected_account.get('name') if self.trading_bot.selected_account else None
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
        """Handle flatten command: flatten [SYMBOL]"""
        symbol = args[0].upper() if args else None
        if symbol:
            # Check if flatten_symbol method exists
            if hasattr(self.trading_bot, 'flatten_symbol'):
                result = await self.trading_bot.flatten_symbol(symbol)
            else:
                return {"error": "flatten_symbol method not available"}
        else:
            # flatten_all_positions doesn't take account_id - it uses selected_account
            result = await self.trading_bot.flatten_all_positions(interactive=False)
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
        
        # Try Trade/search API first (more accurate, pre-calculated PnL)
        trades = await self.trading_bot.get_trades_from_api(
            account_id=account_id,
            start_date=start_date_str,
            end_date=end_date_str
        )
        
        if trades:
            # Filter out half-turn trades (open positions) for completed trades only
            completed_trades = [t for t in trades if not t.get('is_half_turn', False)]
            
            if completed_trades and hasattr(self.trading_bot, '_calculate_trade_statistics'):
                # Convert to format expected by _calculate_trade_statistics
                # Include both gross pnl and net_pnl so statistics can use net_pnl
                formatted_trades = []
                for trade in completed_trades:
                    formatted_trades.append({
                        'symbol': trade.get('symbol', 'UNKNOWN'),
                        'side': trade.get('side', 'UNKNOWN'),
                        'quantity': trade.get('quantity', 0),
                        'entry_price': trade.get('price', 0),  # Trade/search gives single price
                        'exit_price': trade.get('price', 0),
                        'pnl': trade.get('profitAndLoss') or trade.get('pnl', 0),  # Gross PnL
                        'net_pnl': trade.get('net_pnl'),  # Net PnL after fees
                        'fees': trade.get('fees', 0),  # Fees for this trade
                        'entry_time': trade.get('timestamp') or trade.get('creationTimestamp', ''),
                        'exit_time': trade.get('timestamp') or trade.get('creationTimestamp', ''),
                    })
                
                stats = self.trading_bot._calculate_trade_statistics(formatted_trades)
                return {
                    "trades": completed_trades,
                    "statistics": stats
                }
            return {"trades": completed_trades}
        
        # Fallback to Order/search + consolidation if Trade/search not available
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
            status = self.trading_bot.strategy_manager.get_status()
            return {"strategies": status}
        else:
            return {"error": "Strategy manager not available"}
    
    def _parse_strategy_args(self, args: List[str]) -> Tuple[str, Optional[List[str]], Optional[str], Optional[Dict]]:
        """
        Parse strategy start arguments with support for flags.
        
        Supports:
        - strategies start <name> [symbols]
        - strategies start <name> --symbols=SYM1,SYM2
        - strategies start <name> --timeframe=1m --symbols=MNQ
        - strategies start <name> --risk-config='{"MNQ":{"max_quantity":10,"cooldown":60.0,"max_pending":1}}'
        - strategies start <name> --max-quantity=10 --cooldown=60.0 --max-pending=1
        
        Returns:
            tuple: (strategy_name, symbols, timeframe, risk_config)
        """
        if not args:
            return None, None, None, None
        
        strategy_name = args[0]
        symbols = None
        timeframe = None
        risk_config = None
        
        # Parse remaining arguments for flags
        for arg in args[1:]:
            if arg.startswith('--symbols='):
                symbols_str = arg.split('=', 1)[1].strip("'\"")
                symbols = [s.strip().upper() for s in symbols_str.split(',')]
            elif arg.startswith('--timeframe='):
                timeframe = arg.split('=', 1)[1].strip("'\"")
            elif arg.startswith('--risk-config='):
                risk_config_str = arg.split('=', 1)[1].strip("'\"")
                try:
                    risk_config = json.loads(risk_config_str)
                except json.JSONDecodeError as e:
                    logger.error(f"Invalid risk-config JSON: {e}")
                    risk_config = None
            elif arg.startswith('--max-quantity='):
                max_qty = int(arg.split('=', 1)[1])
                if risk_config is None:
                    risk_config = {}
                # Apply to all symbols if symbols are known, otherwise will be applied when symbols are set
                if symbols:
                    for sym in symbols:
                        if sym not in risk_config:
                            risk_config[sym] = {}
                        risk_config[sym]['max_quantity'] = max_qty
            elif arg.startswith('--cooldown='):
                cooldown = float(arg.split('=', 1)[1])
                if risk_config is None:
                    risk_config = {}
                if symbols:
                    for sym in symbols:
                        if sym not in risk_config:
                            risk_config[sym] = {}
                        risk_config[sym]['cooldown'] = cooldown
            elif arg.startswith('--max-pending='):
                max_pending = int(arg.split('=', 1)[1])
                if risk_config is None:
                    risk_config = {}
                if symbols:
                    for sym in symbols:
                        if sym not in risk_config:
                            risk_config[sym] = {}
                        risk_config[sym]['max_pending'] = max_pending
            elif not arg.startswith('--'):
                # Legacy support: treat non-flag arguments as symbols
                if symbols is None:
                    symbols = [s.strip().upper() for s in arg.split(',')]
        
        return strategy_name, symbols, timeframe, risk_config
    
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
            names = (
                strategy_manager.catalog_strategy_names()
                if hasattr(strategy_manager, "catalog_strategy_names")
                else strategy_manager.registered_strategy_names()
            )
            for name in names:
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
                return {"error": "Usage: strategies start <name> [--symbols=SYM1,SYM2] [--timeframe=TIMEFRAME] [--risk-config=JSON] [--max-quantity=N] [--cooldown=N] [--max-pending=N]"}
            
            strategy_name, symbols, timeframe, risk_config = self._parse_strategy_args(args[1:])
            
            if not strategy_name:
                return {"error": "Usage: strategies start <name> [--symbols=SYM1,SYM2] [--timeframe=TIMEFRAME] [--risk-config=JSON] [--max-quantity=N] [--cooldown=N] [--max-pending=N]"}
            
            # Set timeframe environment variable if provided (for simple_candle strategy)
            if timeframe:
                os.environ['SIMPLE_CANDLE_TIMEFRAME'] = timeframe
                logger.info(f"⏰ Timeframe set to: {timeframe}")
            
            # If risk_config was built from individual args but symbols weren't set yet, apply defaults
            if risk_config and symbols:
                for sym in symbols:
                    if sym not in risk_config:
                        risk_config[sym] = {}
                    # Fill in defaults if missing
                    if 'max_quantity' not in risk_config[sym]:
                        risk_config[sym]['max_quantity'] = 10
                    if 'cooldown' not in risk_config[sym]:
                        risk_config[sym]['cooldown'] = 60.0
                    if 'max_pending' not in risk_config[sym]:
                        risk_config[sym]['max_pending'] = 1
            
            if risk_config:
                logger.info(f"📊 Risk config: {risk_config}")
            
            success, message = await strategy_manager.start_strategy(strategy_name, symbols, risk_config=risk_config)
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
    
    async def _handle_analyze_date(self, args: List[str]) -> Dict[str, Any]:
        """
        Analyze a specific date using the actual strategy code on historical data.
        
        This runs the OvernightRangeStrategy on historical data for the target date,
        ensuring calculations match exactly what the strategy would do live.
        
        Usage: analyze_date <symbol> <date> [--timeframe=5m]
        Example: analyze_date MNQ 2026-01-08
        Example: analyze_date MNQ 2026-01-08 --timeframe=5m
        """
        from datetime import datetime, time, timedelta, timezone
        from types import MethodType
        from strategies.overnight_range_strategy import OvernightRangeStrategy
        
        try:
            import pytz
            et_tz = pytz.timezone('US/Eastern')
        except ImportError:
            et_tz = timezone(timedelta(hours=-5))
        
        if len(args) < 2:
            return {"error": "Usage: analyze_date <symbol> <date> [--timeframe=5m]\nExample: analyze_date MNQ 2026-01-08"}
        
        symbol = args[0].upper()
        date_str = args[1]
        
        # Parse timeframe if provided
        timeframe = "5m"
        if '--timeframe=' in ' '.join(args):
            for arg in args:
                if arg.startswith('--timeframe='):
                    timeframe = arg.split('=')[1]
                    break
        
        # Parse date
        try:
            target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            return {"error": f"Invalid date format: {date_str}. Use YYYY-MM-DD format (e.g., 2026-01-08)"}
        
        logger.info(f"📊 Analyzing {symbol} for date {target_date} using strategy code")
        
        saved_ts = getattr(self.trading_bot, '_current_bar_timestamp', None)
        try:
            # Store original get_historical_data method
            original_get_historical = self.trading_bot.get_historical_data
            
            # Calculate cutoff time: end of target date (23:59:59 ET)
            # This ensures we only get data up to and including the target date.
            # The strategy will determine the date from the latest bar, so if the latest bar
            # is from 8:00 AM or later on the target date, it will calculate the overnight
            # session that ended on the target date (18:00 previous day to 8:00 target date).
            cutoff_time = datetime.combine(
                target_date,
                time(23, 59, 59)
            ).replace(tzinfo=et_tz).astimezone(timezone.utc)
            
            # Mock get_historical_data to filter out any data after the target date
            async def mock_get_historical_data(
                self_param,
                symbol: str,
                timeframe: str = "1m",
                limit: int = 100,
                start_time: Optional[datetime] = None,
                end_time: Optional[datetime] = None,
                **kwargs
            ) -> List[Dict]:
                """Mock that filters data to only include bars up to target date."""
                # If end_time is provided and it's after cutoff_time, cap it at cutoff_time
                # This ensures we don't get data from after the target date
                # If end_time is None, set it to cutoff_time so we get historical data up to that point
                effective_end_time = end_time
                if effective_end_time is None:
                    effective_end_time = cutoff_time
                elif effective_end_time > cutoff_time:
                    effective_end_time = cutoff_time
                
                logger.debug(f"Mock get_historical_data: symbol={symbol}, timeframe={timeframe}, limit={limit}, "
                           f"end_time={effective_end_time}, cutoff={cutoff_time}")
                
                # Call original method with capped end_time
                bars = await original_get_historical(
                    symbol,
                    timeframe,
                    limit,
                    start_time,
                    effective_end_time,
                    **kwargs
                )
                
                logger.debug(f"Mock get_historical_data: received {len(bars) if bars else 0} bars from original method")
                
                # Filter bars to only include those up to cutoff_time (safety check)
                def _bar_time_utc_obj(b: Any) -> Optional[datetime]:
                    ts = None
                    if isinstance(b, dict):
                        ts = b.get("timestamp") or b.get("time")
                    else:
                        ts = getattr(b, "timestamp", None) or getattr(b, "time", None)
                    if isinstance(ts, str):
                        try:
                            return datetime.fromisoformat(ts.replace("Z", "+00:00"))
                        except Exception:
                            return None
                    if isinstance(ts, datetime):
                        return ts
                    return None

                filtered_bars = []
                for bar in bars:
                    bar_time = _bar_time_utc_obj(bar)
                    if not bar_time:
                        continue
                    
                    if bar_time.tzinfo is None:
                        bar_time = bar_time.replace(tzinfo=timezone.utc)
                    
                    # Only include bars up to cutoff time
                    if bar_time <= cutoff_time:
                        filtered_bars.append(bar)
                
                logger.debug(f"Mock get_historical_data: filtered to {len(filtered_bars)} bars up to cutoff")
                
                # If we have fewer bars than requested and we set end_time, try fetching without end_time
                # then filter. This handles cases where the API doesn't return enough bars when end_time is set.
                if len(filtered_bars) < limit and effective_end_time == cutoff_time and end_time is None:
                    logger.debug(f"Only got {len(filtered_bars)} bars, trying without end_time constraint")
                    try:
                        # Fetch without end_time to get more historical data, then filter
                        more_bars = await original_get_historical(
                            symbol,
                            timeframe,
                            limit * 2,  # Request more to account for filtering
                            start_time,
                            None,  # No end_time - let API return most recent bars
                            **kwargs
                        )
                        # Filter to only include bars up to cutoff
                        for bar in more_bars:
                            bar_time = _bar_time_utc_obj(bar)
                            if not bar_time:
                                continue
                            
                            if bar_time.tzinfo is None:
                                bar_time = bar_time.replace(tzinfo=timezone.utc)
                            
                            if bar_time <= cutoff_time:
                                filtered_bars.append(bar)
                        
                        # Remove duplicates and sort by timestamp
                        seen_timestamps = set()
                        unique_bars = []
                        for bar in filtered_bars:
                            bar_time = _bar_time_utc_obj(bar)
                            if not bar_time:
                                continue
                            
                            if bar_time.tzinfo is None:
                                bar_time = bar_time.replace(tzinfo=timezone.utc)
                            
                            ts_key = bar_time.isoformat()
                            if ts_key not in seen_timestamps:
                                seen_timestamps.add(ts_key)
                                unique_bars.append(bar)
                        
                        # Sort by timestamp
                        unique_bars.sort(key=lambda b: (_bar_time_utc_obj(b) or datetime.min.replace(tzinfo=timezone.utc)))
                        filtered_bars = unique_bars[-limit:] if len(unique_bars) > limit else unique_bars
                        logger.debug(f"After fallback fetch: {len(filtered_bars)} bars")
                    except Exception as e:
                        logger.debug(f"Error in fallback fetch: {e}")
                
                return filtered_bars
            
            # Bind the mock function as a method to self.trading_bot
            # This ensures it receives 'self' (self.trading_bot) as the first parameter
            self.trading_bot.get_historical_data = MethodType(mock_get_historical_data, self.trading_bot)
            
            # Create strategy instance
            strategy = OvernightRangeStrategy(self.trading_bot)
            
            # Set as-of timestamp so track_overnight_range uses the overnight session that
            # *ended* on the target date (not "today"). Otherwise it uses now or latest bar
            # and requests a session that may be entirely after the cutoff, yielding 0 bars.
            end_h, end_m = map(int, strategy.overnight_end.split(':'))
            as_of_naive = datetime.combine(target_date, time(end_h, end_m, 0)) + timedelta(minutes=1)
            if hasattr(et_tz, 'localize'):
                as_of = et_tz.localize(as_of_naive)
            else:
                as_of = as_of_naive.replace(tzinfo=et_tz)
            self.trading_bot._current_bar_timestamp = as_of
            
            # Run strategy calculations for the target date
            # The strategy will use the mocked get_historical_data which filters to target date
            range_data = await strategy.track_overnight_range(symbol)
            if not range_data:
                return {"error": f"Could not calculate overnight range for {symbol} on {target_date}"}
            
            atr_data = await strategy.calculate_atr(symbol, timeframe=timeframe)
            if not atr_data:
                return {"error": f"Could not calculate ATR for {symbol}"}
            
            long_order, short_order = await strategy.calculate_range_break_orders(symbol)
            if not long_order or not short_order:
                return {"error": f"Could not calculate breakout orders for {symbol}"}
            
            # Format results
            result = {
                "date": date_str,
                "symbol": symbol,
                "overnight_range": {
                    "high": round(range_data.high, 2),
                    "low": round(range_data.low, 2),
                    "open": round(range_data.open, 2),
                    "close": round(range_data.close, 2),
                    "size": round(range_data.range_size, 2),
                    "midpoint": round(range_data.midpoint, 2),
                    "start_time": range_data.start_time.strftime('%Y-%m-%d %H:%M ET'),
                    "end_time": range_data.end_time.strftime('%Y-%m-%d %H:%M ET'),
                },
                "atr": {
                    "current": round(atr_data.current_atr, 2),
                    "daily": round(atr_data.daily_atr, 2),
                    "timeframe": timeframe,
                },
                "market_open": {
                    "price": round(atr_data.market_open_price, 2),
                },
                "daily_bar": {
                    "open": round(atr_data.market_open_price, 2),  # Strategy uses market open for zones
                },
                "daily_atr_zones": {
                    "upper": {
                        "lower_bound": round(atr_data.day_bull_price, 2),
                        "upper_bound": round(atr_data.day_bull_price1, 2),
                        "midpoint": round((atr_data.day_bull_price + atr_data.day_bull_price1) / 2.0, 2),
                    },
                    "lower": {
                        "lower_bound": round(atr_data.day_bear_price1, 2),
                        "upper_bound": round(atr_data.day_bear_price, 2),
                        "midpoint": round((atr_data.day_bear_price + atr_data.day_bear_price1) / 2.0, 2),
                    },
                },
                "breakout_levels": {
                    "long": {
                        "entry": round(long_order.entry_price, 2),
                        "stop_loss": round(long_order.stop_loss, 2),
                        "take_profit": round(long_order.take_profit, 2),
                        "risk": round(long_order.entry_price - long_order.stop_loss, 2),
                        "reward": round(long_order.take_profit - long_order.entry_price, 2),
                    },
                    "short": {
                        "entry": round(short_order.entry_price, 2),
                        "stop_loss": round(short_order.stop_loss, 2),
                        "take_profit": round(short_order.take_profit, 2),
                        "risk": round(short_order.stop_loss - short_order.entry_price, 2),
                        "reward": round(short_order.entry_price - short_order.take_profit, 2),
                    },
                },
            }
            
            return result
            
        except Exception as e:
            logger.error(f"Error analyzing date: {e}", exc_info=True)
            return {"error": f"Error analyzing date: {str(e)}"}
        finally:
            # Always restore get_historical_data and _current_bar_timestamp
            if 'original_get_historical' in locals():
                self.trading_bot.get_historical_data = original_get_historical
            self.trading_bot._current_bar_timestamp = saved_ts
    
    async def _handle_simulate(self, args: List[str]) -> Dict[str, Any]:
        """
        Simulate running a strategy on historical data for a specific date.
        
        This combines history and analyze_date to effectively simulate strategy execution,
        showing what signals/orders would have been generated.
        
        Usage: simulate <strategy> <symbol> <date> [timeframe] [--settings=key:value,key:value]
        Example: simulate overnight_range MNQ 2026-01-08 5m
        Example: simulate simple_candle MNQ 2026-01-08 1m --settings=quantity:2,stop_atr_multiplier:1.5
        
        Available strategies:
        - overnight_range: Overnight Range Breakout Strategy
        - simple_candle: Simple Candle Strategy
        - mean_reversion: Mean Reversion Strategy
        - trend_following: Trend Following Strategy
        - simple_momentum: Simple Momentum Strategy
        - trend_scalping: Trend Scalping Strategy
        """
        from datetime import datetime, time, timedelta, timezone
        from types import MethodType
        from core.backtest.strategy_replay import StrategyReplayEngine
        
        try:
            import pytz
            et_tz = pytz.timezone('US/Eastern')
        except ImportError:
            et_tz = timezone(timedelta(hours=-5))
        
        if len(args) < 3:
            return {
                "error": "Usage: simulate <strategy> <symbol> <date> [timeframe] [--settings=key:value,key:value]\n"
                        "Example: simulate overnight_range MNQ 2026-01-08 5m"
            }
        
        strategy_name = args[0].lower()
        symbol = args[1].upper()
        date_str = args[2]
        
        # Parse timeframe (optional, defaults to strategy's default)
        timeframe = None
        settings_str = None
        for i, arg in enumerate(args[3:], start=3):
            if arg.startswith('--settings='):
                settings_str = arg.split('=', 1)[1]
            elif not arg.startswith('--'):
                timeframe = arg
                break
        
        # Parse date
        try:
            target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            return {"error": f"Invalid date format: {date_str}. Use YYYY-MM-DD format (e.g., 2026-01-08)"}
        
        # Parse settings if provided
        settings = {}
        if settings_str:
            for pair in settings_str.split(','):
                if ':' in pair:
                    key, value = pair.split(':', 1)
                    key = key.strip()
                    # Try to parse value as appropriate type
                    try:
                        if '.' in value:
                            settings[key] = float(value)
                        else:
                            settings[key] = int(value)
                    except ValueError:
                        settings[key] = value.strip()
        
        logger.info(f"🎯 Simulating {strategy_name} strategy on {symbol} for date {target_date}")
        if timeframe:
            logger.info(f"   Timeframe: {timeframe}")
        if settings:
            logger.info(f"   Settings: {settings}")
        
        try:
            # Get strategy class
            strategy_class = None
            if hasattr(self.trading_bot, 'strategy_manager'):
                strategy_class = self.trading_bot.strategy_manager.get_strategy_class(strategy_name)
            
            if not strategy_class:
                # Fallback to direct import
                strategy_map = {
                    'overnight_range': 'strategies.overnight_range_strategy.OvernightRangeStrategy',
                    'simple_candle': 'strategies.simple_candle_strategy.SimpleCandleStrategy',
                    'mean_reversion': 'strategies.mean_reversion_strategy.MeanReversionStrategy',
                    'trend_following': 'strategies.trend_following_strategy.TrendFollowingStrategy',
                    'simple_momentum': 'strategies.simple_momentum_strategy.SimpleMomentumStrategy',
                    'trend_scalping': 'strategies.trend_scalping_strategy.TrendScalpingStrategy',
                }
                
                if strategy_name in strategy_map:
                    module_path, class_name = strategy_map[strategy_name].rsplit('.', 1)
                    module = __import__(module_path, fromlist=[class_name])
                    strategy_class = getattr(module, class_name)
            
            if not strategy_class:
                sm = self.trading_bot.strategy_manager if hasattr(self.trading_bot, 'strategy_manager') else None
                available = (
                    sm.catalog_strategy_names()
                    if sm and hasattr(sm, "catalog_strategy_names")
                    else (sm.registered_strategy_names() if sm else [])
                )
                return {"error": f"Strategy '{strategy_name}' not found. Available: {', '.join(available) if available else 'none'}"}
            
            # Store original get_historical_data method
            original_get_historical = self.trading_bot.get_historical_data
            
            # Calculate cutoff time: end of target date (23:59:59 ET)
            cutoff_time = datetime.combine(
                target_date,
                time(23, 59, 59)
            ).replace(tzinfo=et_tz).astimezone(timezone.utc)
            
            # Mock get_historical_data to filter data to target date (same as analyze_date)
            async def mock_get_historical_data(
                self_param,
                symbol: str,
                timeframe: str = "1m",
                limit: int = 100,
                start_time: Optional[datetime] = None,
                end_time: Optional[datetime] = None,
                **kwargs
            ) -> List[Dict]:
                """Mock that filters data to only include bars up to target date."""
                # If both start_time and end_time are provided, respect them (strategy knows what it needs)
                # but cap end_time at cutoff_time to prevent future data
                # If only end_time is provided, cap it at cutoff_time
                # If neither is provided, set end_time to cutoff_time to get historical data up to target date
                
                effective_start_time = start_time
                effective_end_time = end_time
                
                if effective_end_time is None:
                    # No end_time specified - allow data up to cutoff_time
                    effective_end_time = cutoff_time
                elif effective_end_time > cutoff_time:
                    # Cap end_time at cutoff_time to prevent future data
                    effective_end_time = cutoff_time
                
                # If start_time is provided and it's after cutoff_time, return empty
                if effective_start_time and effective_start_time > cutoff_time:
                    return []
                
                # Call original method with effective times
                bars = await original_get_historical(
                    symbol,
                    timeframe,
                    limit,
                    effective_start_time,
                    effective_end_time,
                    **kwargs
                )
                
                if not bars:
                    logger.debug(f"Mock get_historical_data: No bars returned from API for {symbol} {timeframe} "
                               f"(start={effective_start_time}, end={effective_end_time}, limit={limit})")
                    return []
                
                # Log first and last bar timestamps for debugging
                if bars:
                    first_bar_ts = bars[0].get('timestamp') or bars[0].get('time')
                    last_bar_ts = bars[-1].get('timestamp') or bars[-1].get('time')
                    logger.debug(f"Mock get_historical_data: Received {len(bars)} bars from API "
                               f"(first={first_bar_ts}, last={last_bar_ts}), "
                               f"filtering for range [{effective_start_time}, {effective_end_time}] up to cutoff {cutoff_time}")
                
                # Filter bars based on the request type
                def _bar_time_utc_obj(b: Any) -> Optional[datetime]:
                    ts = None
                    if isinstance(b, dict):
                        ts = b.get("timestamp") or b.get("time")
                    else:
                        ts = getattr(b, "timestamp", None) or getattr(b, "time", None)
                    if isinstance(ts, str):
                        try:
                            return datetime.fromisoformat(ts.replace("Z", "+00:00"))
                        except Exception:
                            return None
                    if isinstance(ts, datetime):
                        return ts
                    return None

                filtered_bars = []
                for bar in bars:
                    bar_time = _bar_time_utc_obj(bar)
                    if not bar_time:
                        continue
                    
                    if bar_time.tzinfo is None:
                        bar_time = bar_time.replace(tzinfo=timezone.utc)
                    
                    # Always exclude future data (beyond cutoff_time)
                    if bar_time > cutoff_time:
                        continue
                    
                    # If start_time and end_time are provided, return bars within that exact range
                    # The strategy will do its own filtering, but we should return bars that could match
                    if effective_start_time is not None and effective_end_time is not None:
                        # Return bars within the requested range (strategy will do its own filtering)
                        # Use <= for end_time to include bars at the exact end time
                        if effective_start_time <= bar_time <= effective_end_time:
                            filtered_bars.append(bar)
                    elif effective_start_time is not None:
                        # Only start_time provided
                        if bar_time >= effective_start_time:
                            filtered_bars.append(bar)
                    else:
                        # No time constraints - return all bars up to cutoff_time
                        filtered_bars.append(bar)
                
                # Special case: if strategy is calling with just limit=1 to get latest bar for date determination,
                # ensure we return a bar from the target date (or as close as possible)
                if limit == 1 and effective_start_time is None:
                    if filtered_bars:
                        # Return the bar closest to cutoff_time (most recent)
                        # Sort by timestamp descending to get the most recent bar
                        filtered_bars.sort(key=lambda b: (_bar_time_utc_obj(b) or datetime.min.replace(tzinfo=timezone.utc)), reverse=True)
                        latest_bar = filtered_bars[0]
                        latest_bar_time = latest_bar.get('timestamp')
                        # Convert to ET to check if it's from the target date
                        if isinstance(latest_bar_time, str):
                            latest_bar_dt = datetime.fromisoformat(latest_bar_time.replace('Z', '+00:00'))
                        elif isinstance(latest_bar_time, datetime):
                            latest_bar_dt = latest_bar_time
                        else:
                            latest_bar_dt = None
                        
                        if latest_bar_dt and latest_bar_dt.tzinfo is None:
                            latest_bar_dt = latest_bar_dt.replace(tzinfo=timezone.utc)
                        
                        if latest_bar_dt:
                            latest_bar_et = latest_bar_dt.astimezone(et_tz)
                            logger.info(f"Mock get_historical_data: Returning latest bar for date determination: "
                                      f"{latest_bar_time} (ET: {latest_bar_et.date()}, target: {target_date.date()}) "
                                      f"(limit=1, no time constraints)")
                        else:
                            logger.info(f"Mock get_historical_data: Returning latest bar for date determination: "
                                      f"{latest_bar_time} (limit=1, no time constraints)")
                        return [latest_bar]
                    else:
                        logger.warning(f"Mock get_historical_data: No bars found for limit=1 date determination")
                        return []
                
                logger.debug(f"Mock get_historical_data: Returning {len(filtered_bars)} filtered bars")
                return filtered_bars
            
            # Bind the mock function
            self.trading_bot.get_historical_data = MethodType(mock_get_historical_data, self.trading_bot)
            
            # Create strategy instance with settings
            strategy_config = None
            if hasattr(self.trading_bot.strategy_manager, 'get_strategy_config'):
                strategy_config = self.trading_bot.strategy_manager.get_strategy_config(strategy_name)
            
            if strategy_config is None:
                from strategies.strategy_base import StrategyConfig
                strategy_config = StrategyConfig.from_env(strategy_name.upper())
            
            # Apply custom settings
            for key, value in settings.items():
                if hasattr(strategy_config, key):
                    setattr(strategy_config, key, value)
                # Also try setting on strategy instance after creation
                logger.debug(f"Setting {key}={value} on strategy config")
            
            # Override timeframe if provided
            if timeframe:
                if hasattr(strategy_config, 'timeframe'):
                    strategy_config.timeframe = timeframe
                # Also set environment variable for strategies that read it
                import os
                os.environ[f'{strategy_name.upper()}_TIMEFRAME'] = timeframe
            
            strategy = strategy_class(self.trading_bot, strategy_config)
            
            # Apply settings directly to strategy instance if needed
            for key, value in settings.items():
                if hasattr(strategy, key):
                    setattr(strategy, key, value)
                    logger.debug(f"Applied setting {key}={value} to strategy instance")
            
            # Fetch historical bars for the simulation period
            # For overnight_range, we need data from previous day 18:00 to target date 23:59
            # For other strategies, we might need a different period
            if strategy_name == 'overnight_range':
                # Overnight range needs: previous day 18:00 to target date 23:59
                start_date = target_date - timedelta(days=1)
                start_time_et = datetime.combine(start_date, time(18, 0)).replace(tzinfo=et_tz)
                end_time_et = datetime.combine(target_date, time(23, 59, 59)).replace(tzinfo=et_tz)
            else:
                # Other strategies: start of target date to end of target date
                start_time_et = datetime.combine(target_date, time(0, 0)).replace(tzinfo=et_tz)
                end_time_et = datetime.combine(target_date, time(23, 59, 59)).replace(tzinfo=et_tz)
            
            start_time_utc = start_time_et.astimezone(timezone.utc)
            end_time_utc = end_time_et.astimezone(timezone.utc)
            
            # Determine timeframe for fetching bars
            fetch_timeframe = timeframe or (strategy_config.timeframe if hasattr(strategy_config, 'timeframe') else '1m')
            
            # Calculate how many bars we need
            duration = end_time_utc - start_time_utc
            if fetch_timeframe.endswith('m'):
                minutes = int(fetch_timeframe[:-1])
                bars_needed = int(duration.total_seconds() / 60 / minutes) + 100  # Extra buffer
            elif fetch_timeframe.endswith('h'):
                hours = int(fetch_timeframe[:-1])
                bars_needed = int(duration.total_seconds() / 3600 / hours) + 50
            elif fetch_timeframe.endswith('d'):
                bars_needed = 30
            else:
                bars_needed = 1000  # Default for 1m
            
            logger.info(f"📊 Fetching {bars_needed} {fetch_timeframe} bars from {start_time_et} to {end_time_et}")
            
            # Fetch bars
            bars = await self.trading_bot.get_historical_data(
                symbol=symbol,
                timeframe=fetch_timeframe,
                limit=bars_needed,
                start_time=start_time_utc,
                end_time=end_time_utc
            )
            
            if not bars or len(bars) < 10:
                return {"error": f"Insufficient historical data: {len(bars) if bars else 0} bars found"}
            
            logger.info(f"✅ Fetched {len(bars)} bars for simulation")
            
            # Use StrategyReplayEngine to simulate strategy execution
            replay_engine = StrategyReplayEngine(
                strategy_instance=strategy,
                trading_bot=self.trading_bot,
                initial_capital=50000.0,
                commission_per_contract=2.50,
                slippage_ticks=0.5,
                point_value=2.0  # MNQ point value
            )
            
            # Get tick size
            tick_size = 0.25  # Default for MNQ
            if hasattr(strategy, 'get_tick_size'):
                tick_size = await strategy.get_tick_size(symbol)
            
            # Run simulation
            logger.info(f"🔄 Running strategy simulation...")
            result = await replay_engine.replay(
                symbol=symbol,
                bars=bars,
                tick_size=tick_size,
                replay_timeframe=fetch_timeframe
            )
            
            # Restore original method
            self.trading_bot.get_historical_data = original_get_historical
            
            # Format results
            return {
                "strategy": strategy_name,
                "symbol": symbol,
                "date": date_str,
                "timeframe": fetch_timeframe,
                "settings": settings,
                "simulation": {
                    "total_trades": result.total_trades,
                    "winning_trades": result.winning_trades,
                    "losing_trades": result.losing_trades,
                    "win_rate": round(result.win_rate, 2),
                    "total_pnl": round(result.total_pnl, 2),
                    "max_drawdown": round(result.max_drawdown, 2),
                    "sharpe_ratio": round(result.sharpe_ratio, 2) if result.sharpe_ratio else None,
                    "profit_factor": round(result.profit_factor, 2) if result.profit_factor else None,
                    "initial_capital": result.initial_capital,
                    "final_capital": round(result.final_capital, 2),
                    "return_pct": round(result.total_return_pct, 2),
                },
                "trades": [
                    {
                        "entry_time": trade.entry_time.isoformat() if trade.entry_time else None,
                        "exit_time": trade.exit_time.isoformat() if trade.exit_time else None,
                        "side": trade.side.value if hasattr(trade.side, 'value') else str(trade.side),
                        "entry_price": round(trade.entry_price, 2),
                        "exit_price": round(trade.exit_price, 2) if trade.exit_price else None,
                        "quantity": trade.quantity,
                        "pnl": round(trade.pnl, 2),
                        "exit_reason": trade.exit_reason if hasattr(trade, 'exit_reason') else None,
                    }
                    for trade in result.trades
                ] if hasattr(result, 'trades') and result.trades else [],
                "bars_analyzed": len(bars),
            }
            
        except Exception as e:
            # Restore original method on error
            if 'original_get_historical' in locals():
                self.trading_bot.get_historical_data = original_get_historical
            logger.error(f"Error simulating strategy: {e}", exc_info=True)
            return {"error": f"Error simulating strategy: {str(e)}"}
    
    async def _handle_backtest_range(self, args: List[str]) -> Dict[str, Any]:
        """
        Backtest a strategy over a date range.
        
        This runs the strategy simulation for each day in the range and aggregates results.
        
        Usage: backtest_range <strategy> <symbol> <start_date> <end_date> [timeframe] [--settings=key:value,key:value]
        Example: backtest_range overnight_range MNQ 2026-01-01 2026-01-08 5m
        Example: backtest_range simple_candle MNQ 2026-01-01 2026-01-08 1m --settings=quantity:2
        """
        from datetime import datetime, timedelta
        
        if len(args) < 4:
            return {
                "error": "Usage: backtest_range <strategy> <symbol> <start_date> <end_date> [timeframe] [--settings=key:value,key:value]\n"
                        "Example: backtest_range overnight_range MNQ 2026-01-01 2026-01-08 5m"
            }
        
        strategy_name = args[0].lower()
        symbol = args[1].upper()
        start_date_str = args[2]
        end_date_str = args[3]
        
        # Parse timeframe and settings (same logic as simulate)
        timeframe = None
        settings_str = None
        for i, arg in enumerate(args[4:], start=4):
            if arg.startswith('--settings='):
                settings_str = arg.split('=', 1)[1]
            elif not arg.startswith('--'):
                timeframe = arg
                break
        
        # Parse dates
        try:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
        except ValueError:
            return {"error": "Invalid date format. Use YYYY-MM-DD format (e.g., 2026-01-01)"}
        
        if start_date > end_date:
            return {"error": "Start date must be before or equal to end date"}
        
        # Parse settings
        settings = {}
        if settings_str:
            for pair in settings_str.split(','):
                if ':' in pair:
                    key, value = pair.split(':', 1)
                    key = key.strip()
                    try:
                        if '.' in value:
                            settings[key] = float(value)
                        else:
                            settings[key] = int(value)
                    except ValueError:
                        settings[key] = value.strip()
        
        logger.info(f"📊 Backtesting {strategy_name} on {symbol} from {start_date} to {end_date}")
        
        # Run simulation for each day
        daily_results = []
        current_date = start_date
        total_trades = 0
        total_pnl = 0.0
        winning_days = 0
        losing_days = 0
        
        while current_date <= end_date:
            # Skip weekends (Saturday=5, Sunday=6)
            if current_date.weekday() >= 5:
                current_date += timedelta(days=1)
                continue
            
            logger.info(f"  Simulating {current_date}...")
            
            # Use simulate command logic for each day
            simulate_args = [strategy_name, symbol, current_date.strftime('%Y-%m-%d')]
            if timeframe:
                simulate_args.append(timeframe)
            if settings_str:
                simulate_args.append(f'--settings={settings_str}')
            
            result = await self._handle_simulate(simulate_args)
            
            if "error" in result:
                logger.warning(f"  ⚠️  Error on {current_date}: {result['error']}")
                current_date += timedelta(days=1)
                continue
            
            sim_data = result.get("simulation", {})
            daily_results.append({
                "date": current_date.strftime('%Y-%m-%d'),
                "trades": sim_data.get("total_trades", 0),
                "pnl": sim_data.get("total_pnl", 0),
                "win_rate": sim_data.get("win_rate", 0),
            })
            
            total_trades += sim_data.get("total_trades", 0)
            total_pnl += sim_data.get("total_pnl", 0)
            if sim_data.get("total_pnl", 0) > 0:
                winning_days += 1
            elif sim_data.get("total_pnl", 0) < 0:
                losing_days += 1
            
            current_date += timedelta(days=1)
        
        # Calculate aggregate metrics
        days_tested = len(daily_results)
        avg_daily_pnl = total_pnl / days_tested if days_tested > 0 else 0
        day_win_rate = (winning_days / days_tested * 100) if days_tested > 0 else 0
        
        return {
            "strategy": strategy_name,
            "symbol": symbol,
            "start_date": start_date_str,
            "end_date": end_date_str,
            "timeframe": timeframe or "default",
            "settings": settings,
            "summary": {
                "days_tested": days_tested,
                "total_trades": total_trades,
                "total_pnl": round(total_pnl, 2),
                "avg_daily_pnl": round(avg_daily_pnl, 2),
                "winning_days": winning_days,
                "losing_days": losing_days,
                "day_win_rate": round(day_win_rate, 2),
            },
            "daily_results": daily_results,
        }

    async def _handle_replay_session(self, args: List[str]) -> Dict[str, Any]:
        """
        Replay a single regular trading session bar-by-bar (market open -> close) for a given date,
        using analyze_date to seed the correct overnight range + daily ATR context.
        
        Keeps it simple: fetch correct historical data by explicit date/time ranges and record order
        events when price crosses strategy levels. No live execution pathways are used.
        
        Usage:
          replay_session <strategy> <symbol> <date> [timeframe] [--settings=key:value,key:value]
        
        Example:
          replay_session overnight_range MNQ 2026-01-09 1m
        """
        from datetime import datetime, time, timedelta, timezone
        from dataclasses import asdict, is_dataclass
        from types import MethodType

        try:
            import pytz
            et_tz = pytz.timezone('US/Eastern')
        except ImportError:
            et_tz = timezone(timedelta(hours=-5))

        if len(args) < 3:
            return {
                "error": "Usage: replay_session <strategy> <symbol> <date> [timeframe] [--settings=key:value,key:value]\n"
                         "Example: replay_session overnight_range MNQ 2026-01-09 1m"
            }

        strategy_name = args[0].lower()
        symbol = args[1].upper()
        date_str = args[2]

        timeframe = "1m"
        if len(args) >= 4 and not args[3].startswith("--"):
            timeframe = args[3]

        # Parse optional settings
        settings: Dict[str, Any] = {}
        if "--settings=" in " ".join(args):
            for a in args:
                if a.startswith("--settings="):
                    raw = a.split("=", 1)[1].strip()
                    if raw:
                        for pair in raw.split(","):
                            if ":" in pair:
                                k, v = pair.split(":", 1)
                                settings[k.strip()] = v.strip()
                    break

        try:
            target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            return {"error": f"Invalid date format: {date_str}. Use YYYY-MM-DD (e.g., 2026-01-09)"}

        if not hasattr(self.trading_bot, "strategy_manager"):
            return {"error": "Strategy manager not available"}
        strategy_manager = self.trading_bot.strategy_manager
        strategy_class = strategy_manager.get_strategy_class(strategy_name)
        if not strategy_class:
            available = (
                strategy_manager.catalog_strategy_names()
                if hasattr(strategy_manager, "catalog_strategy_names")
                else strategy_manager.registered_strategy_names()
            )
            return {"error": f"Strategy '{strategy_name}' not found. Available: {', '.join(available)}"}

        # 1) Seed correct context via analyze_date (single source of truth for range + ATR zones)
        analysis = await self._handle_analyze_date([symbol, date_str, "--timeframe=5m"])
        if "error" in analysis:
            return {"error": f"analyze_date failed: {analysis['error']}"}

        # 2) Build strategy instance (we only call analyze(); we do not execute)
        strategy_config = None
        if hasattr(strategy_manager, "get_strategy_config"):
            strategy_config = strategy_manager.get_strategy_config(strategy_name)
        if strategy_config is None:
            from strategies.strategy_base import StrategyConfig
            strategy_config = StrategyConfig.from_env(strategy_name.upper())

        for k, v in settings.items():
            if hasattr(strategy_config, k):
                try:
                    existing = getattr(strategy_config, k)
                    if isinstance(existing, int):
                        setattr(strategy_config, k, int(v))
                    elif isinstance(existing, float):
                        setattr(strategy_config, k, float(v))
                    else:
                        setattr(strategy_config, k, v)
                except Exception:
                    setattr(strategy_config, k, v)

        strategy = strategy_class(self.trading_bot, strategy_config)

        # 3) Session window (08:00 -> 16:00 ET)
        if hasattr(et_tz, "localize"):
            session_open_et = et_tz.localize(datetime.combine(target_date, time(8, 0, 0)))
            session_close_et = et_tz.localize(datetime.combine(target_date, time(16, 0, 0)))
            overnight_start_et = et_tz.localize(datetime.combine(target_date - timedelta(days=1), time(18, 0, 0)))
            overnight_end_et = session_open_et
        else:
            session_open_et = datetime.combine(target_date, time(8, 0, 0)).replace(tzinfo=et_tz)
            session_close_et = datetime.combine(target_date, time(16, 0, 0)).replace(tzinfo=et_tz)
            overnight_start_et = datetime.combine(target_date - timedelta(days=1), time(18, 0, 0)).replace(tzinfo=et_tz)
            overnight_end_et = session_open_et

        # Stop new entries at/after 16:00 ET (live-like session cutoff)
        entry_cutoff_et = session_close_et

        session_open_utc = session_open_et.astimezone(timezone.utc)
        session_close_utc = session_close_et.astimezone(timezone.utc)

        # 4) Fetch intraday bars for the session explicitly (correct data)
        bars = await self.trading_bot.get_historical_data(
            symbol=symbol,
            timeframe=timeframe,
            start_time=session_open_utc,
            end_time=session_close_utc,
            limit=5000
        )
        if not bars:
            return {"error": f"No bars for {symbol} {timeframe} between {session_open_et} and {session_close_et}"}

        def _bar_dt_utc(bar: Dict[str, Any]) -> Optional[datetime]:
            ts = bar.get("timestamp") or bar.get("time") or bar.get("t")
            if ts is None:
                return None
            if isinstance(ts, datetime):
                dt = ts
            elif isinstance(ts, str):
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            elif isinstance(ts, (int, float)):
                dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            else:
                return None
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)

        # Sort bars chronologically (oldest first) for proper simulation
        bars_sorted = sorted(bars, key=lambda b: _bar_dt_utc(b) or datetime.min.replace(tzinfo=timezone.utc))

        # Defensive: enforce hard session window (adapter may still return extra bars)
        bars_in_session: List[Dict[str, Any]] = []
        for bar in bars_sorted:
            bt_utc = _bar_dt_utc(bar)
            if not bt_utc:
                continue
            if session_open_utc <= bt_utc <= session_close_utc:
                bars_in_session.append(bar)
        bars_sorted = bars_in_session
        
        # Log first and last bar times for verification
        if bars_sorted:
            first_bar_time = _bar_dt_utc(bars_sorted[0])
            last_bar_time = _bar_dt_utc(bars_sorted[-1])
            if first_bar_time and last_bar_time:
                logger.debug(f"Replay session: {len(bars_sorted)} bars from {first_bar_time.astimezone(et_tz).strftime('%Y-%m-%d %H:%M:%S ET')} to {last_bar_time.astimezone(et_tz).strftime('%Y-%m-%d %H:%M:%S ET')}")

        # 5) Seed strategy context for overnight_range so range/ATR/open price match analyze_date
        try:
            from strategies.overnight_range_strategy import OvernightRange, ATRData

            if "overnight_range" in analysis and "atr" in analysis and "daily_atr_zones" in analysis:
                r = analysis["overnight_range"]
                range_data = OvernightRange(
                    symbol=symbol,
                    high=float(r["high"]),
                    low=float(r["low"]),
                    open=float(r["open"]),
                    close=float(r["close"]),
                    start_time=overnight_start_et,
                    end_time=overnight_end_et,
                    range_size=float(r["size"]),
                    midpoint=float(r["midpoint"]),
                )

                a = analysis["atr"]
                zones = analysis["daily_atr_zones"]
                upper = zones["upper"]
                lower = zones["lower"]
                market_open_price = float(analysis.get("market_open", {}).get("price", 0.0))

                atr_data = ATRData(
                    current_atr=float(a["current"]),
                    daily_atr=float(a["daily"]),
                    atr_zone_high=0.0,
                    atr_zone_low=0.0,
                    period=int(getattr(strategy, "atr_period", 14)),
                    market_open_price=market_open_price,
                    day_bull_price=float(upper["lower_bound"]),
                    day_bull_price1=float(upper["upper_bound"]),
                    day_bear_price=float(lower["upper_bound"]),
                    day_bear_price1=float(lower["lower_bound"]),
                )

                # Seed caches so analyze() doesn't re-fetch
                if hasattr(strategy, "active_ranges"):
                    strategy.active_ranges[symbol] = range_data
                if hasattr(strategy, "_overnight_range_cache"):
                    strategy._overnight_range_cache[(symbol, target_date)] = range_data
                if hasattr(strategy, "_tick_size_cache"):
                    strategy._tick_size_cache[symbol] = 0.25

                async def _mock_track_overnight_range(self_param, sym: str):
                    return range_data if sym.upper() == symbol else None

                async def _mock_calculate_atr(self_param, sym: str, period: int = None, timeframe: str = None):
                    return atr_data if sym.upper() == symbol else None

                strategy.track_overnight_range = MethodType(_mock_track_overnight_range, strategy)
                strategy.calculate_atr = MethodType(_mock_calculate_atr, strategy)
        except Exception:
            pass

        # 6) Call analyze() at session open (setup)
        self.trading_bot._current_bar_timestamp = session_open_utc
        signal = await strategy.analyze(symbol)

        def _to_jsonable(x: Any) -> Any:
            if is_dataclass(x):
                return asdict(x)
            if isinstance(x, dict):
                return {k: _to_jsonable(v) for k, v in x.items()}
            if isinstance(x, list):
                return [_to_jsonable(v) for v in x]
            return x

        events: List[Dict[str, Any]] = []
        position = None
        pending = None  # legacy; replay_session uses staged_orders instead
        breakout_templates = None  # {"long": {...}, "short": {...}}
        staged_orders = {"long": None, "short": None}  # stop-entry orders that have been "placed" (staged)

        # If this is an overnight_range-style signal, simulate bracket triggers (simple)
        if isinstance(signal, dict) and "long_order" in signal and "short_order" in signal:
            long_o = signal["long_order"]
            short_o = signal["short_order"]

            def _get(o, name: str, default: float = 0.0) -> float:
                if o is None:
                    return float(default)
                if hasattr(o, name):
                    return float(getattr(o, name))
                if isinstance(o, dict):
                    return float(o.get(name, default))
                return float(default)

            breakout_templates = {
                "long": {"entry": _get(long_o, "entry_price"), "sl": _get(long_o, "stop_loss"), "tp": _get(long_o, "take_profit")},
                "short": {"entry": _get(short_o, "entry_price"), "sl": _get(short_o, "stop_loss"), "tp": _get(short_o, "take_profit")},
            }
            events.append({
                "type": "setup",
                "time": session_open_et.isoformat(),
                "symbol": symbol,
                "strategy": strategy_name,
                "long": breakout_templates["long"],
                "short": breakout_templates["short"],
                "notes": "Orders are NOT assumed placed at 08:00. replay_session stages stop entries only when price is within breakout proximity (like monitor_breakout_levels).",
            })
        else:
            # Generic: just record the signal once
            if signal:
                events.append({"type": "signal", "time": session_open_et.isoformat(), "signal": _to_jsonable(signal)})

        # Proximity rule (match OvernightRangeStrategy.monitor_breakout_levels):
        # threshold_points = max(range_size * (breakout_proximity_percent/100), breakout_min_proximity_points)
        range_size_points = None
        try:
            if "overnight_range" in analysis:
                range_size_points = float(analysis["overnight_range"].get("size", 0.0))
        except Exception:
            range_size_points = None
        if range_size_points is None or range_size_points <= 0:
            range_size_points = 0.0

        proximity_percent = float(getattr(strategy, "breakout_proximity_percent", 10.0))
        proximity_min_points = float(getattr(strategy, "breakout_min_proximity_points", 5.0))
        threshold_points = max(range_size_points * (proximity_percent / 100.0), proximity_min_points)
        
        for bar in bars_sorted:
            bt_utc = _bar_dt_utc(bar)
            if not bt_utc:
                continue
            self.trading_bot._current_bar_timestamp = bt_utc

            o = float(bar.get("open", bar.get("o", 0)) or 0)
            h = float(bar.get("high", bar.get("h", 0)) or 0)
            l = float(bar.get("low", bar.get("l", 0)) or 0)
            c = float(bar.get("close", bar.get("c", 0)) or 0)

            # Stage stop-entry orders ONLY when price is within proximity (live-like).
            # Use bar OPEN as "current price" for staging (order placement before intrabar highs/lows).
            bt_et = bt_utc.astimezone(et_tz)
            current_price_for_staging = o
            if position is None and breakout_templates and bt_et < entry_cutoff_et:
                # LONG stop entry must be ABOVE current price at placement
                if staged_orders["long"] is None:
                    long_entry = breakout_templates["long"]["entry"]
                    distance = long_entry - current_price_for_staging
                    if 0 <= distance <= threshold_points and current_price_for_staging < long_entry:
                        staged_orders["long"] = breakout_templates["long"].copy()
                        events.append({
                            "type": "order_placed",
                            "time": bt_utc.isoformat(),
                            "side": "LONG",
                            "order_type": "STOP_ENTRY",
                            "entry": long_entry,
                            "stop_loss": staged_orders["long"]["sl"],
                            "take_profit": staged_orders["long"]["tp"],
                            "current_price": current_price_for_staging,
                            "distance_points": round(distance, 2),
                            "threshold_points": round(threshold_points, 2),
                        })

                # SHORT stop entry must be BELOW current price at placement
                if staged_orders["short"] is None:
                    short_entry = breakout_templates["short"]["entry"]
                    distance = current_price_for_staging - short_entry
                    if 0 <= distance <= threshold_points and current_price_for_staging > short_entry:
                        staged_orders["short"] = breakout_templates["short"].copy()
                        events.append({
                            "type": "order_placed",
                            "time": bt_utc.isoformat(),
                            "side": "SHORT",
                            "order_type": "STOP_ENTRY",
                            "entry": short_entry,
                            "stop_loss": staged_orders["short"]["sl"],
                            "take_profit": staged_orders["short"]["tp"],
                            "current_price": current_price_for_staging,
                            "distance_points": round(distance, 2),
                            "threshold_points": round(threshold_points, 2),
                        })

            # Check for exit FIRST (before entry) to handle same-bar entry+exit
            exit_triggered = False
            if position is not None:
                if position["side"] == "LONG":
                    sl_hit = l <= position["sl"]
                    tp_hit = h >= position["tp"]
                    if sl_hit or tp_hit:
                        reason = "stop_loss" if sl_hit else "take_profit"
                        if sl_hit and tp_hit:
                            # Both hit in same bar - conservative: use stop loss
                            reason = "stop_loss"
                        exit_price = position["sl"] if reason == "stop_loss" else position["tp"]
                        events.append({"type": "exit", "time": bt_utc.isoformat(), "reason": reason, "price": exit_price})
                        position = None
                        exit_triggered = True
                        # After exit, allow strategy to stage new stop orders again later
                        staged_orders = {"long": None, "short": None}
                else:  # SHORT
                    sl_hit = h >= position["sl"]
                    tp_hit = l <= position["tp"]
                    if sl_hit or tp_hit:
                        reason = "stop_loss" if sl_hit else "take_profit"
                        if sl_hit and tp_hit:
                            # Both hit in same bar - conservative: use stop loss
                            reason = "stop_loss"
                        exit_price = position["sl"] if reason == "stop_loss" else position["tp"]
                        events.append({"type": "exit", "time": bt_utc.isoformat(), "reason": reason, "price": exit_price})
                        position = None
                        exit_triggered = True
                        # After exit, allow strategy to stage new stop orders again later
                        staged_orders = {"long": None, "short": None}

            # Entry (OCO) - only if an order has been staged/placed
            if position is None and bt_et < entry_cutoff_et:
                long_hit = staged_orders["long"] is not None and h >= staged_orders["long"]["entry"]
                short_hit = staged_orders["short"] is not None and l <= staged_orders["short"]["entry"]
                chosen = None
                
                if long_hit and short_hit:
                    # Both levels hit - choose based on close direction
                    chosen = "LONG" if c >= o else "SHORT"
                elif long_hit:
                    chosen = "LONG"
                elif short_hit:
                    chosen = "SHORT"

                if chosen == "LONG":
                    position = {"side": "LONG", "entry": staged_orders["long"]["entry"], "sl": staged_orders["long"]["sl"], "tp": staged_orders["long"]["tp"]}
                    events.append({"type": "entry", "time": bt_utc.isoformat(), "side": "LONG", "price": position["entry"]})
                    # OCO cancel opposite staged order
                    staged_orders = {"long": None, "short": None}
                elif chosen == "SHORT":
                    position = {"side": "SHORT", "entry": staged_orders["short"]["entry"], "sl": staged_orders["short"]["sl"], "tp": staged_orders["short"]["tp"]}
                    events.append({"type": "entry", "time": bt_utc.isoformat(), "side": "SHORT", "price": position["entry"]})
                    # OCO cancel opposite staged order
                    staged_orders = {"long": None, "short": None}
            elif position is None and bt_et >= entry_cutoff_et:
                # At/after cutoff, cancel any staged-but-unfilled orders (log once)
                if (staged_orders["long"] is not None or staged_orders["short"] is not None) and not any(
                    e.get("type") == "order_cancelled" and e.get("reason") == "session_close_cutoff" for e in events
                ):
                    events.append({
                        "type": "order_cancelled",
                        "time": bt_utc.isoformat(),
                        "reason": "session_close_cutoff",
                        "message": f"New entries stopped at {entry_cutoff_et.strftime('%H:%M')} ET; cancelling staged stop entries",
                    })
                staged_orders = {"long": None, "short": None}

        # Force-close any open position at session close (16:00 ET)
        if position is not None and bars_sorted:
            last = bars_sorted[-1]
            last_close = float(last.get("close", last.get("c", 0)) or 0)
            events.append({"type": "exit", "time": session_close_utc.isoformat(), "reason": "session_close", "price": last_close})
            position = None

        # Calculate performance statistics
        def _get_point_value(sym: str) -> float:
            """Get point value for symbol ($ per point)."""
            sym_upper = sym.upper()
            point_values = {
                'MNQ': 2.0, 'NQ': 20.0,
                'MES': 5.0, 'ES': 50.0,
                'MYM': 0.5, 'YM': 5.0,
                'M2K': 5.0, 'RTY': 50.0,
                'MGC': 10.0, 'GC': 100.0,
            }
            return point_values.get(sym_upper, 2.0)

        def _utc_to_et(dt_utc: Optional[datetime]) -> Optional[str]:
            """Convert UTC datetime to ET string for display."""
            if dt_utc is None:
                return None
            if dt_utc.tzinfo is None:
                dt_utc = dt_utc.replace(tzinfo=timezone.utc)
            dt_et = dt_utc.astimezone(et_tz)
            return dt_et.strftime("%Y-%m-%d %H:%M:%S ET")

        point_value = _get_point_value(symbol)
        quantity = 1  # Default quantity per trade

        # Process events into trades
        trades = []
        current_trade = None

        for event in events:
            if event["type"] == "entry":
                current_trade = {
                    "side": event["side"],
                    "entry_time": event["time"],
                    "entry_time_et": _utc_to_et(datetime.fromisoformat(event["time"].replace("Z", "+00:00"))),
                    "entry_price": event["price"],
                    "quantity": quantity,
                }
            elif event["type"] == "exit" and current_trade is not None:
                exit_time_utc = datetime.fromisoformat(event["time"].replace("Z", "+00:00"))
                exit_price = event["price"]
                reason = event.get("reason", "unknown")
                
                # Calculate P&L
                if current_trade["side"] == "LONG":
                    points = exit_price - current_trade["entry_price"]
                else:  # SHORT
                    points = current_trade["entry_price"] - exit_price
                
                pnl_points = points
                pnl_dollars = pnl_points * point_value * quantity
                
                trade = {
                    **current_trade,
                    "exit_time": event["time"],
                    "exit_time_et": _utc_to_et(exit_time_utc),
                    "exit_price": exit_price,
                    "exit_reason": reason,
                    "pnl_points": round(pnl_points, 2),
                    "pnl_dollars": round(pnl_dollars, 2),
                    "duration_minutes": round((exit_time_utc - datetime.fromisoformat(current_trade["entry_time"].replace("Z", "+00:00"))).total_seconds() / 60, 1),
                }
                trades.append(trade)
                current_trade = None

        # Calculate summary statistics
        total_trades = len(trades)
        winning_trades = [t for t in trades if t["pnl_dollars"] > 0]
        losing_trades = [t for t in trades if t["pnl_dollars"] < 0]
        breakeven_trades = [t for t in trades if t["pnl_dollars"] == 0]
        
        total_pnl_points = sum(t["pnl_points"] for t in trades)
        total_pnl_dollars = sum(t["pnl_dollars"] for t in trades)
        
        win_rate = (len(winning_trades) / total_trades * 100) if total_trades > 0 else 0.0
        
        avg_win_points = sum(t["pnl_points"] for t in winning_trades) / len(winning_trades) if winning_trades else 0.0
        avg_loss_points = sum(t["pnl_points"] for t in losing_trades) / len(losing_trades) if losing_trades else 0.0
        avg_win_dollars = sum(t["pnl_dollars"] for t in winning_trades) / len(winning_trades) if winning_trades else 0.0
        avg_loss_dollars = sum(t["pnl_dollars"] for t in losing_trades) / len(losing_trades) if losing_trades else 0.0
        
        largest_win_points = max((t["pnl_points"] for t in winning_trades), default=0.0)
        largest_loss_points = min((t["pnl_points"] for t in losing_trades), default=0.0)
        largest_win_dollars = max((t["pnl_dollars"] for t in winning_trades), default=0.0)
        largest_loss_dollars = min((t["pnl_dollars"] for t in losing_trades), default=0.0)
        
        profit_factor = abs(avg_win_dollars / avg_loss_dollars) if avg_loss_dollars != 0 else float('inf') if avg_win_dollars > 0 else 0.0
        
        # Convert event times to ET for display
        events_with_et = []
        for event in events:
            event_copy = event.copy()
            if "time" in event_copy:
                try:
                    dt_utc = datetime.fromisoformat(event_copy["time"].replace("Z", "+00:00"))
                    event_copy["time_et"] = _utc_to_et(dt_utc)
                except:
                    pass
            events_with_et.append(event_copy)

        return {
            "strategy": strategy_name,
            "symbol": symbol,
            "date": date_str,
            "timeframe": timeframe,
            "session": {
                "open_et": session_open_et.isoformat(),
                "close_et": session_close_et.isoformat(),
                "open_et_display": session_open_et.strftime("%Y-%m-%d %H:%M:%S ET"),
                "close_et_display": session_close_et.strftime("%Y-%m-%d %H:%M:%S ET"),
            },
            "analysis": analysis,
            "signal": _to_jsonable(signal),
            "events": events_with_et,
            "bars_count": len(bars_sorted),
            "trades": trades,
            "performance": {
                "total_trades": total_trades,
                "winning_trades": len(winning_trades),
                "losing_trades": len(losing_trades),
                "breakeven_trades": len(breakeven_trades),
                "win_rate_pct": round(win_rate, 2),
                "total_pnl_points": round(total_pnl_points, 2),
                "total_pnl_dollars": round(total_pnl_dollars, 2),
                "avg_win_points": round(avg_win_points, 2),
                "avg_loss_points": round(avg_loss_points, 2),
                "avg_win_dollars": round(avg_win_dollars, 2),
                "avg_loss_dollars": round(avg_loss_dollars, 2),
                "largest_win_points": round(largest_win_points, 2),
                "largest_loss_points": round(largest_loss_points, 2),
                "largest_win_dollars": round(largest_win_dollars, 2),
                "largest_loss_dollars": round(largest_loss_dollars, 2),
                "profit_factor": round(profit_factor, 2) if profit_factor != float('inf') else "N/A",
                "point_value": point_value,
                "quantity_per_trade": quantity,
            },
        }