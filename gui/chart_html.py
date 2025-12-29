"""
Local TradingView Lightweight Charts - HTML Generator
Creates a standalone HTML file with TradingView Lightweight Charts that can be opened in any browser.
Supports real-time updates and backtesting mode.
"""

import json
import os
import asyncio
from datetime import datetime, timezone
from typing import List, Dict, Optional
from pathlib import Path
from aiohttp import web
import logging

logger = logging.getLogger(__name__)

# Global server instance for real-time charts
_chart_server = None
_chart_server_port = None
_chart_server_trading_bot = None


async def _start_chart_server(trading_bot, symbol: str) -> int:
    """Start a simple HTTP server for real-time chart updates."""
    global _chart_server, _chart_server_port, _chart_server_trading_bot
    
    if _chart_server is not None:
        # Server already running
        _chart_server_trading_bot = trading_bot
        return _chart_server_port
    
    app = web.Application()
    
    # Reduce aiohttp access log verbosity (only log warnings/errors, not every request)
    import logging
    import os
    if os.getenv("ACCESS_LOG_VERBOSE", "false").lower() not in ("1", "true", "yes", "on"):
        logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
    
    # Cache for latest bar to avoid rate limiting
    _latest_bar_cache = {}
    _last_bar_fetch_time = {}
    
    # Cache for account state to reduce slow API calls
    _account_state_cache = {}
    _account_state_cache_time = {}
    _account_state_cache_ttl = 10000  # 10 seconds cache TTL
    
    async def handle_account_state(request):
        """Get account state - balance, P&L, compliance - lightweight with caching."""
        try:
            account_id = None
            if hasattr(trading_bot, 'selected_account') and trading_bot.selected_account:
                if isinstance(trading_bot.selected_account, dict):
                    account_id = trading_bot.selected_account.get('id')
                else:
                    account_id = str(trading_bot.selected_account)
            
            # Check cache first
            cache_key = f"account_state_{account_id}"
            import time
            current_time = time.time() * 1000  # milliseconds
            
            if cache_key in _account_state_cache:
                cache_age = current_time - _account_state_cache_time.get(cache_key, 0)
                if cache_age < _account_state_cache_ttl:
                    # Return cached data
                    response = web.json_response(_account_state_cache[cache_key])
                    response.headers['Access-Control-Allow-Origin'] = '*'
                    return response
            
            # Get account info and balance efficiently (only if cache expired)
            # Use balance from selected_account if available (faster)
            balance = 0.0
            account_name = ''
            if hasattr(trading_bot, 'selected_account') and trading_bot.selected_account:
                if isinstance(trading_bot.selected_account, dict):
                    balance = float(trading_bot.selected_account.get('balance', 0.0))
                    account_name = trading_bot.selected_account.get('name', '')
            
            # Only fetch account_info if we don't have cached balance
            account_info = None
            if not balance or balance == 0.0:
                account_info = await trading_bot.get_account_info(account_id=account_id)
                balance_data = await trading_bot.get_account_balance(account_id=account_id)
                
                if balance_data is not None:
                    balance = float(balance_data)
            
            # Get P&L from positions (lighter than account_info)
            unrealized_pnl = 0.0
            realized_pnl = 0.0
            try:
                positions = await trading_bot.get_open_positions(account_id=account_id)
                if positions:
                    for pos in positions:
                        unrealized_pnl += float(pos.get('unrealizedPnL', pos.get('unrealized_pnl', 0)))
            except:
                pass
            
            # Get compliance status if available
            compliance = {}
            if hasattr(trading_bot, 'account_tracker') and trading_bot.account_tracker:
                try:
                    compliance = trading_bot.account_tracker.check_compliance()
                except:
                    pass
            
            state = {
                'account_id': account_id,
                'account_name': account_name,
                'balance': balance,
                'unrealized_pnl': unrealized_pnl,
                'realized_pnl': realized_pnl,
                'compliance': compliance
            }
            
            # Cache the result
            _account_state_cache[cache_key] = state
            _account_state_cache_time[cache_key] = current_time
            
            response = web.json_response(state)
            response.headers['Access-Control-Allow-Origin'] = '*'
            return response
        except Exception as e:
            logger.error(f"Error fetching account state: {e}")
            import traceback
            logger.error(traceback.format_exc())
            response = web.json_response({'error': str(e)}, status=500)
            response.headers['Access-Control-Allow-Origin'] = '*'
            return response
    
    async def handle_quote(request):
        """Handle quote requests for real-time updates."""
        try:
            # Get symbol from query parameter, fallback to server's default symbol
            quote_symbol = request.query.get('symbol') or symbol
            quote = await trading_bot.get_market_quote(quote_symbol)
            if quote and "error" not in quote:
                # Use quote data to build bar instead of fetching historical data
                # This avoids rate limiting from too many historical data requests
                current_price = float(quote.get('last') or quote.get('lastPrice') or quote.get('bid') or 0)
                current_volume = int(quote.get('volume', 0))  # Total daily volume
                current_time = datetime.now(timezone.utc)
                
                # Round timestamp down to minute boundary (for 1m bars)
                # This prevents gaps in the data due to sub-minute timing differences
                timestamp_sec = int(current_time.timestamp() // 60 * 60)
                
                # Get or create latest bar from cache
                cache_key = f"{quote_symbol}_latest"
                volume_cache_key = f"{quote_symbol}_last_volume"
                
                if cache_key not in _latest_bar_cache:
                    # Initialize with current price
                    _latest_bar_cache[cache_key] = {
                        'time': timestamp_sec,
                        'open': current_price,
                        'high': current_price,
                        'low': current_price,
                        'close': current_price,
                        'volume': 0  # Start at 0, will be calculated from volume delta
                    }
                    _last_bar_fetch_time[cache_key] = current_time
                    # Initialize last known volume for delta calculation
                    _latest_bar_cache[volume_cache_key] = current_volume
                else:
                    # Update existing bar
                    cached_bar = _latest_bar_cache[cache_key]
                    last_volume = _latest_bar_cache.get(volume_cache_key, current_volume)
                    
                    # Check if we're in the same minute (for 1m bars) - if not, start new bar
                    if cached_bar['time'] != timestamp_sec:
                        # New bar - reset
                        cached_bar = {
                            'time': timestamp_sec,
                            'open': current_price,
                            'high': current_price,
                            'low': current_price,
                            'close': current_price,
                            'volume': max(0, current_volume - last_volume)  # Volume delta for this bar
                        }
                        # Update last volume reference
                        _latest_bar_cache[volume_cache_key] = current_volume
                    else:
                        # Update current bar
                        cached_bar['high'] = max(cached_bar['high'], current_price)
                        cached_bar['low'] = min(cached_bar['low'], current_price)
                        cached_bar['close'] = current_price
                        # Add volume delta since last update
                        volume_delta = max(0, current_volume - last_volume)
                        if volume_delta > 0:
                            cached_bar['volume'] = cached_bar.get('volume', 0) + volume_delta
                            _latest_bar_cache[volume_cache_key] = current_volume
                    
                    _latest_bar_cache[cache_key] = cached_bar
                
                latest_bar = _latest_bar_cache[cache_key]
                
                # Create response with CORS headers
                response = web.json_response({
                    'quote': quote,
                    'latest_bar': latest_bar,
                    'timestamp': datetime.now().isoformat()
                })
                response.headers['Access-Control-Allow-Origin'] = '*'
                response.headers['Access-Control-Allow-Methods'] = 'GET, OPTIONS'
                response.headers['Access-Control-Allow-Headers'] = '*'
                return response
            else:
                response = web.json_response({'error': 'No quote available'}, status=404)
                response.headers['Access-Control-Allow-Origin'] = '*'
                return response
        except Exception as e:
            logger.error(f"Error fetching quote: {e}")
            import traceback
            logger.error(traceback.format_exc())
            response = web.json_response({'error': str(e)}, status=500)
            response.headers['Access-Control-Allow-Origin'] = '*'
            return response
    
    async def handle_options(request):
        """Handle CORS preflight requests."""
        response = web.Response()
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Methods'] = 'GET, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = '*'
        return response
    
    async def handle_place_order(request):
        """Handle order placement requests."""
        try:
            data = await request.json()
            order_symbol = data.get('symbol', symbol)  # Use order_symbol to avoid scope conflict
            side = data.get('side')  # 'BUY' or 'SELL'
            quantity = int(data.get('quantity', 1))
            order_type = data.get('order_type', 'market')  # 'market', 'limit', 'stop'
            limit_price = data.get('limit_price')
            stop_price = data.get('stop_price')
            stop_loss_price = data.get('stop_loss_price')
            take_profit_price = data.get('take_profit_price')
            enable_bracket = data.get('enable_bracket', False)
            
            # Place order via trading bot
            # Check if this is a bracket order (either order_type is 'bracket' or enable_bracket is True with prices)
            # Distinguish between bracket order type (uses ticks) vs orders with bracket enabled (uses prices)
            is_bracket_order_type = (order_type == 'bracket')
            has_bracket_enabled = (enable_bracket and stop_loss_price and take_profit_price)
            
            if is_bracket_order_type:
                # Standalone bracket order - expects ticks, not prices
                # Convert prices to ticks if provided
                try:
                    quote = await trading_bot.get_market_quote(order_symbol)
                    if quote and 'last' in quote:
                        current_price = float(quote['last'])
                        tick_size = await trading_bot._get_tick_size(order_symbol)
                        
                        # Convert prices to ticks
                        if side.upper() == 'BUY':
                            stop_loss_ticks = int((current_price - float(stop_loss_price)) / tick_size) if stop_loss_price else None
                            take_profit_ticks = int((float(take_profit_price) - current_price) / tick_size) if take_profit_price else None
                            # Ensure correct signs: BUY stop loss should be negative, TP positive
                            if stop_loss_ticks and stop_loss_ticks > 0:
                                stop_loss_ticks = -stop_loss_ticks
                            if take_profit_ticks and take_profit_ticks < 0:
                                take_profit_ticks = -take_profit_ticks
                        else:  # SELL
                            stop_loss_ticks = int((float(stop_loss_price) - current_price) / tick_size) if stop_loss_price else None
                            take_profit_ticks = int((current_price - float(take_profit_price)) / tick_size) if take_profit_price else None
                            # Ensure correct signs: SELL stop loss should be positive, TP negative
                            if stop_loss_ticks and stop_loss_ticks < 0:
                                stop_loss_ticks = -stop_loss_ticks
                            if take_profit_ticks and take_profit_ticks > 0:
                                take_profit_ticks = -take_profit_ticks
                        
                        if not stop_loss_ticks or not take_profit_ticks:
                            result = {'error': 'Bracket orders require stop loss and take profit values'}
                        else:
                            # Use create_bracket_order with ticks
                            result = await trading_bot.create_bracket_order(
                                symbol=order_symbol,
                                side=side,
                                quantity=quantity,
                                stop_loss_ticks=stop_loss_ticks,
                                take_profit_ticks=take_profit_ticks
                            )
                    else:
                        result = {'error': 'Could not get market price for bracket order'}
                except Exception as e:
                    logger.error(f"Error creating bracket order: {e}")
                    import traceback
                    logger.error(traceback.format_exc())
                    result = {'error': f'Failed to create bracket order: {str(e)}'}
            elif has_bracket_enabled:
                # Order with bracket enabled - use prices directly
                if order_type == 'market':
                    # Market order with brackets - use prices
                    if not stop_loss_price or not take_profit_price:
                        result = {'error': 'Bracket orders require stop loss and take profit prices'}
                    else:
                        # Validate prices are positive (absolute prices, not relative)
                        if float(stop_loss_price) <= 0 or float(take_profit_price) <= 0:
                            result = {'error': 'Stop loss and take profit must be positive absolute prices'}
                        else:
                            try:
                                result = await trading_bot.create_bracket_order(
                                    symbol=order_symbol,
                                    side=side,
                                    quantity=quantity,
                                    stop_loss_price=float(stop_loss_price),
                                    take_profit_price=float(take_profit_price)
                                )
                            except Exception as e:
                                logger.error(f"Error creating market order with brackets: {e}")
                                import traceback
                                logger.error(traceback.format_exc())
                                result = {'error': f'Failed to create market order with brackets: {str(e)}'}
                elif order_type == 'stop' and enable_bracket:
                    # Stop order with brackets - use stop_price as entry, convert prices to ticks
                    if not stop_loss_price or not take_profit_price or not stop_price:
                        result = {'error': 'Stop bracket orders require stop price, stop loss price, and take profit price'}
                    else:
                        # Validate prices are positive (absolute prices, not relative)
                        if float(stop_price) <= 0 or float(stop_loss_price) <= 0 or float(take_profit_price) <= 0:
                            result = {'error': 'Stop price, stop loss, and take profit must be positive absolute prices'}
                        else:
                            try:
                                # Use stop_price as entry price, pass prices to place_oco_bracket_with_stop_entry
                                # It will convert prices to ticks using stop_price as entry
                                result = await trading_bot.place_oco_bracket_with_stop_entry(
                                    symbol=order_symbol,
                                    side=side,
                                    quantity=quantity,
                                    entry_price=float(stop_price),
                                    stop_loss_price=float(stop_loss_price),
                                    take_profit_price=float(take_profit_price)
                                )
                            except Exception as e:
                                logger.error(f"Error placing stop bracket order: {e}")
                                import traceback
                                logger.error(traceback.format_exc())
                                result = {'error': f'Failed to place stop bracket order: {str(e)}'}
                elif order_type == 'limit' and enable_bracket:
                    # Limit order with brackets - use limit_price as entry price, convert prices to ticks
                    if not stop_loss_price or not take_profit_price:
                        result = {'error': 'Limit orders with brackets require stop loss and take profit prices'}
                    else:
                        # Validate prices are positive (absolute prices, not relative)
                        if float(stop_loss_price) <= 0 or float(take_profit_price) <= 0:
                            result = {'error': 'Stop loss and take profit must be positive absolute prices'}
                        else:
                            try:
                                # Use limit_price as entry price for tick calculation
                                entry_price = float(limit_price)
                                tick_size = await trading_bot._get_tick_size(order_symbol)
                                
                                # Calculate ticks from prices using limit_price as entry
                                if side.upper() == 'BUY':
                                    # BUY limit: stop loss below entry, TP above entry
                                    stop_loss_ticks = int((entry_price - float(stop_loss_price)) / tick_size)
                                    take_profit_ticks = int((float(take_profit_price) - entry_price) / tick_size)
                                    # Ensure correct signs
                                    if stop_loss_ticks > 0:
                                        stop_loss_ticks = -stop_loss_ticks
                                    if take_profit_ticks < 0:
                                        take_profit_ticks = -take_profit_ticks
                                else:  # SELL
                                    # SELL limit: stop loss above entry, TP below entry
                                    stop_loss_ticks = int((float(stop_loss_price) - entry_price) / tick_size)
                                    take_profit_ticks = int((entry_price - float(take_profit_price)) / tick_size)
                                    # Ensure correct signs
                                    if stop_loss_ticks < 0:
                                        stop_loss_ticks = -stop_loss_ticks
                                    if take_profit_ticks > 0:
                                        take_profit_ticks = -take_profit_ticks
                                
                                result = await trading_bot.place_market_order(
                                    symbol=order_symbol,
                                    side=side,
                                    quantity=quantity,
                                    order_type='limit',
                                    limit_price=limit_price,
                                    stop_loss_ticks=stop_loss_ticks,
                                    take_profit_ticks=take_profit_ticks
                                )
                            except Exception as e:
                                logger.error(f"Error placing limit order with brackets: {e}")
                                import traceback
                                logger.error(traceback.format_exc())
                                result = {'error': f'Failed to place limit order with brackets: {str(e)}'}
                else:
                    result = {'error': 'Bracket orders require stop loss and take profit prices'}
            elif order_type == 'market':
                # Market order - if brackets enabled, use prices (handled above in has_bracket_enabled)
                if enable_bracket and stop_loss_price and take_profit_price:
                    # This should have been handled in the has_bracket_enabled block above
                    # But if we reach here, validate and use create_bracket_order
                    if float(stop_loss_price) <= 0 or float(take_profit_price) <= 0:
                        result = {'error': 'Stop loss and take profit must be positive absolute prices'}
                    else:
                        try:
                            result = await trading_bot.create_bracket_order(
                                symbol=order_symbol,
                                side=side,
                                quantity=quantity,
                                stop_loss_price=float(stop_loss_price),
                                take_profit_price=float(take_profit_price)
                            )
                        except Exception as e:
                            logger.error(f"Error placing market order with brackets: {e}")
                            import traceback
                            logger.error(traceback.format_exc())
                            result = {'error': f'Failed to place market order with brackets: {str(e)}'}
                else:
                    result = await trading_bot.place_market_order(
                        symbol=order_symbol,
                        side=side,
                        quantity=quantity,
                        order_type='market'
                    )
            elif order_type == 'limit':
                # Limit order - if brackets enabled, use limit_price as entry, convert prices to ticks
                if enable_bracket and stop_loss_price and take_profit_price:
                    # Validate prices are positive (absolute prices, not relative)
                    if float(stop_loss_price) <= 0 or float(take_profit_price) <= 0:
                        result = {'error': 'Stop loss and take profit must be positive absolute prices'}
                    else:
                        try:
                            # Use limit_price as entry price for tick calculation
                            entry_price = float(limit_price)
                            tick_size = await trading_bot._get_tick_size(order_symbol)
                            
                            # Calculate ticks from prices using limit_price as entry
                            if side.upper() == 'BUY':
                                # BUY limit: stop loss below entry, TP above entry
                                stop_loss_ticks = int((entry_price - float(stop_loss_price)) / tick_size)
                                take_profit_ticks = int((float(take_profit_price) - entry_price) / tick_size)
                                # Ensure correct signs
                                if stop_loss_ticks > 0:
                                    stop_loss_ticks = -stop_loss_ticks
                                if take_profit_ticks < 0:
                                    take_profit_ticks = -take_profit_ticks
                            else:  # SELL
                                # SELL limit: stop loss above entry, TP below entry
                                stop_loss_ticks = int((float(stop_loss_price) - entry_price) / tick_size)
                                take_profit_ticks = int((entry_price - float(take_profit_price)) / tick_size)
                                # Ensure correct signs
                                if stop_loss_ticks < 0:
                                    stop_loss_ticks = -stop_loss_ticks
                                if take_profit_ticks > 0:
                                    take_profit_ticks = -take_profit_ticks
                            
                            result = await trading_bot.place_market_order(
                                symbol=order_symbol,
                                side=side,
                                quantity=quantity,
                                order_type='limit',
                                limit_price=limit_price,
                                stop_loss_ticks=stop_loss_ticks,
                                take_profit_ticks=take_profit_ticks
                            )
                        except Exception as e:
                            logger.error(f"Error placing limit order with brackets: {e}")
                            import traceback
                            logger.error(traceback.format_exc())
                            result = {'error': f'Failed to place limit order with brackets: {str(e)}'}
                else:
                    result = await trading_bot.place_market_order(
                        symbol=order_symbol,
                        side=side,
                        quantity=quantity,
                        order_type='limit',
                        limit_price=limit_price
                    )
            elif order_type == 'stop':
                if enable_bracket and stop_loss_price and take_profit_price:
                    result = await trading_bot.place_oco_bracket_with_stop_entry(
                        symbol=order_symbol,
                        side=side,
                        quantity=quantity,
                        entry_price=float(stop_price),
                        stop_loss_price=float(stop_loss_price),
                        take_profit_price=float(take_profit_price)
                    )
                else:
                    result = await trading_bot.place_stop_order(
                        symbol=order_symbol,
                        side=side,
                        quantity=quantity,
                        stop_price=float(stop_price)
                    )
            else:
                result = {'error': f'Unsupported order type: {order_type}'}
            
            response = web.json_response(result)
            response.headers['Access-Control-Allow-Origin'] = '*'
            return response
        except Exception as e:
            logger.error(f"Error placing order: {e}")
            import traceback
            logger.error(traceback.format_exc())
            response = web.json_response({'error': str(e)}, status=500)
            response.headers['Access-Control-Allow-Origin'] = '*'
            return response
    
    async def handle_get_positions(request):
        """Handle position requests - canonicalize to match TradingChart.tsx format."""
        try:
            # Use get_open_positions() - same method as CLI, returns List[Dict]
            positions = await trading_bot.get_open_positions()
            logger.debug(f"✅ Fetched {len(positions) if positions else 0} positions for chart")
            
            def extract_symbol_from_contract(contract_str):
                """Extract symbol from contractId or symbolId (e.g., 'CON.F.US.MNQ.Z25' -> 'MNQ')."""
                if not contract_str:
                    return None
                parts = str(contract_str).split('.')
                # ContractManager uses parts[-2] for contractId like "CON.F.US.MNQ.Z25"
                # For symbolId like "F.US.MNQ", we want parts[-1]
                if len(parts) >= 2:
                    # Try parts[-2] first (for contractId format like "CON.F.US.MNQ.Z25")
                    if len(parts) >= 4:
                        candidate = parts[-2]
                        if candidate.isalpha() and candidate.isupper():
                            return candidate
                    # Fallback to parts[-1] (for symbolId format like "F.US.MNQ")
                    candidate = parts[-1]
                    if candidate.isalpha() and candidate.isupper():
                        return candidate
                return None
            
            positions_list = []
            for pos in positions or []:
                if isinstance(pos, dict):
                    pos_dict = pos.copy()
                    
                    # Extract symbol from contractId/symbolId if missing
                    symbol = pos_dict.get('symbol')
                    if not symbol:
                        contract_id = pos_dict.get('contractId') or pos_dict.get('contract_id')
                        symbol_id = pos_dict.get('symbolId') or pos_dict.get('symbol_id')
                        symbol = extract_symbol_from_contract(symbol_id) or extract_symbol_from_contract(contract_id)
                        if symbol:
                            pos_dict['symbol'] = symbol
                    
                    # Set side from type if missing (1 = LONG, 2 = SHORT in TopStepX)
                    side = pos_dict.get('side')
                    pos_type = pos_dict.get('type')
                    if not isinstance(side, str):
                        if pos_type == 1:
                            side = 'LONG'
                        elif pos_type == 2:
                            side = 'SHORT'
                        else:
                            side = 'LONG'  # Default
                        pos_dict['side'] = side
                    
                    # Set entry_price from averagePrice if missing
                    entry_price = pos_dict.get('entry_price') or pos_dict.get('entryPrice')
                    if not entry_price:
                        entry_price = pos_dict.get('averagePrice')
                    if entry_price is not None:
                        pos_dict['entry_price'] = float(entry_price)
                        pos_dict['entryPrice'] = float(entry_price)
                    
                    # Ensure quantity/size
                    quantity = pos_dict.get('quantity') or pos_dict.get('size') or 0
                    pos_dict['quantity'] = quantity
                    pos_dict['size'] = quantity
                    
                    # Fetch linked orders to get stopLoss/takeProfit if not already present
                    # Only use linked orders that are explicitly bracket-linked (not standalone orders)
                    if not pos_dict.get('stopLoss') and not pos_dict.get('stop_loss'):
                        try:
                            pos_id = pos_dict.get('id') or pos_dict.get('position_id')
                            if pos_id:
                                linked_orders = await trading_bot.get_linked_orders(pos_id)
                                if linked_orders and not isinstance(linked_orders, dict):
                                    # Extract stop loss and take profit from linked orders
                                    # Only process orders that are explicitly bracket-linked (have AutoBracket tag)
                                    for order in linked_orders:
                                        custom_tag = order.get('customTag', '') or ''
                                        # Only process bracket-linked orders
                                        if 'AutoBracket' not in custom_tag:
                                            continue  # Skip standalone orders
                                        
                                        order_type = order.get('type', 0)
                                        order_side = order.get('side', 0)
                                        pos_side_val = 0 if side == 'LONG' else 1
                                        
                                        # For LONG positions: stop loss is SELL stop (type 4) with -SL tag, TP is SELL limit (type 1) with -TP tag
                                        # For SHORT positions: stop loss is BUY stop (type 4) with -SL tag, TP is BUY limit (type 1) with -TP tag
                                        if pos_side_val == 0:  # LONG
                                            if order_side == 1:  # SELL
                                                if order_type == 4 and ('-SL' in custom_tag or 'SL' in custom_tag):  # Stop loss
                                                    stop_price = order.get('stopPrice') or order.get('limitPrice')
                                                    if stop_price:
                                                        pos_dict['stopLoss'] = float(stop_price)
                                                        pos_dict['stop_loss'] = float(stop_price)
                                                elif order_type == 1 and ('-TP' in custom_tag or 'TP' in custom_tag):  # Take profit
                                                    tp_price = order.get('limitPrice')
                                                    if tp_price:
                                                        pos_dict['takeProfit'] = float(tp_price)
                                                        pos_dict['take_profit'] = float(tp_price)
                                        elif pos_side_val == 1:  # SHORT
                                            if order_side == 0:  # BUY
                                                if order_type == 4 and ('-SL' in custom_tag or 'SL' in custom_tag):  # Stop loss
                                                    stop_price = order.get('stopPrice') or order.get('limitPrice')
                                                    if stop_price:
                                                        pos_dict['stopLoss'] = float(stop_price)
                                                        pos_dict['stop_loss'] = float(stop_price)
                                                elif order_type == 1 and ('-TP' in custom_tag or 'TP' in custom_tag):  # Take profit
                                                    tp_price = order.get('limitPrice')
                                                    if tp_price:
                                                        pos_dict['takeProfit'] = float(tp_price)
                                                        pos_dict['take_profit'] = float(tp_price)
                        except Exception as e:
                            logger.debug(f"Could not fetch linked orders for position enrichment: {e}")
                    
                    # Ensure all field aliases exist
                    if 'entryPrice' not in pos_dict and 'entry_price' in pos_dict:
                        pos_dict['entryPrice'] = pos_dict['entry_price']
                    if 'entry_price' not in pos_dict and 'entryPrice' in pos_dict:
                        pos_dict['entry_price'] = pos_dict['entryPrice']
                    if 'stopLoss' not in pos_dict and 'stop_loss' in pos_dict:
                        pos_dict['stopLoss'] = pos_dict['stop_loss']
                    if 'stop_loss' not in pos_dict and 'stopLoss' in pos_dict:
                        pos_dict['stop_loss'] = pos_dict['stopLoss']
                    if 'takeProfit' not in pos_dict and 'take_profit' in pos_dict:
                        pos_dict['takeProfit'] = pos_dict['take_profit']
                    if 'take_profit' not in pos_dict and 'takeProfit' in pos_dict:
                        pos_dict['take_profit'] = pos_dict['takeProfit']
                    
                    positions_list.append(pos_dict)
                else:
                    # Fallback: Convert Position object to dict (shouldn't happen with current implementation)
                    pos_dict = {
                        'position_id': getattr(pos, 'position_id', None),
                        'id': getattr(pos, 'position_id', None),
                        'symbol': getattr(pos, 'symbol', None),
                        'side': getattr(pos, 'side', None),
                        'quantity': getattr(pos, 'quantity', 0),
                        'entryPrice': getattr(pos, 'entry_price', None),
                        'entry_price': getattr(pos, 'entry_price', None),
                        'currentPrice': getattr(pos, 'current_price', None),
                        'current_price': getattr(pos, 'current_price', None),
                        'stopLoss': getattr(pos, 'stop_loss', None),
                        'stop_loss': getattr(pos, 'stop_loss', None),
                        'takeProfit': getattr(pos, 'take_profit', None),
                        'take_profit': getattr(pos, 'take_profit', None),
                        'unrealizedPnl': getattr(pos, 'unrealized_pnl', None),
                        'unrealized_pnl': getattr(pos, 'unrealized_pnl', None),
                        'account_id': getattr(pos, 'account_id', None),
                        'contractId': getattr(pos, 'contract_id', None),
                        'contract_id': getattr(pos, 'contract_id', None),
                    }
                    if hasattr(pos, 'raw_data') and pos.raw_data:
                        pos_dict.update(pos.raw_data)
                    positions_list.append(pos_dict)
            
            response = web.json_response({'positions': positions_list})
            response.headers['Access-Control-Allow-Origin'] = '*'
            return response
        except Exception as e:
            logger.error(f"❌ Error fetching positions for chart: {e}")
            import traceback
            logger.error(traceback.format_exc())
            # Return empty positions array instead of 500 error to keep chart functional
            response = web.json_response({'error': str(e), 'positions': []}, status=200)
            response.headers['Access-Control-Allow-Origin'] = '*'
            return response
    
    async def handle_get_orders(request):
        """Handle order requests - canonicalize to match TradingChart.tsx format."""
        try:
            # Get account ID from query or use default
            account_id = request.query.get('account_id')
            orders = await trading_bot.get_open_orders(account_id=account_id)
            logger.debug(f"✅ Fetched {len(orders) if orders else 0} orders for chart")
            
            def extract_symbol_from_contract(contract_str):
                """Extract symbol from contractId or symbolId (e.g., 'CON.F.US.MNQ.Z25' -> 'MNQ')."""
                if not contract_str:
                    return None
                parts = str(contract_str).split('.')
                # ContractManager uses parts[-2] for contractId like "CON.F.US.MNQ.Z25"
                # For symbolId like "F.US.MNQ", we want parts[-1]
                if len(parts) >= 2:
                    # Try parts[-2] first (for contractId format like "CON.F.US.MNQ.Z25")
                    if len(parts) >= 4:
                        candidate = parts[-2]
                        if candidate.isalpha() and candidate.isupper():
                            return candidate
                    # Fallback to parts[-1] (for symbolId format like "F.US.MNQ")
                    candidate = parts[-1]
                    if candidate.isalpha() and candidate.isupper():
                        return candidate
                return None
            
            orders_list = []
            for order in orders or []:
                if isinstance(order, dict):
                    order_dict = order.copy()
                    
                    # Extract symbol from contractId/symbolId if missing
                    symbol = order_dict.get('symbol')
                    if not symbol:
                        contract_id = order_dict.get('contractId') or order_dict.get('contract_id')
                        symbol_id = order_dict.get('symbolId') or order_dict.get('symbol_id')
                        symbol = extract_symbol_from_contract(symbol_id) or extract_symbol_from_contract(contract_id)
                        if symbol:
                            order_dict['symbol'] = symbol
                    
                    # Convert side: 0 = BUY, 1 = SELL (TopStepX numeric)
                    side = order_dict.get('side')
                    if not isinstance(side, str):
                        side = 'BUY' if (side == 0 or side is None) else 'SELL'
                        order_dict['side'] = side
                    
                    # Convert status: 1 = OPEN/PENDING (TopStepX numeric)
                    status = order_dict.get('status')
                    if isinstance(status, int):
                        if status == 1:
                            order_dict['status'] = 'PENDING'  # Also accept 'OPEN' for filtering
                        elif status == 0:
                            order_dict['status'] = 'PENDING'
                    elif isinstance(status, str) and status.upper() == 'OPEN':
                        order_dict['status'] = 'PENDING'  # Normalize to PENDING
                    
                    # Set price and stop_price from limitPrice/stopPrice
                    # For LIMIT orders: use limitPrice as price
                    # For STOP orders: use stopPrice as stop_price, and also set price if limitPrice exists
                    order_type_num = order_dict.get('type', 0)
                    limit_price = order_dict.get('limitPrice') or order_dict.get('limit_price')
                    stop_price = order_dict.get('stopPrice') or order_dict.get('stop_price')
                    
                    # Set price field (for LIMIT orders or orders with limitPrice)
                    if limit_price is not None:
                        order_dict['price'] = float(limit_price)
                        if 'limitPrice' not in order_dict:
                            order_dict['limitPrice'] = float(limit_price)
                    
                    # Set stop_price field (for STOP orders)
                    if stop_price is not None:
                        order_dict['stop_price'] = float(stop_price)
                        if 'stopPrice' not in order_dict:
                            order_dict['stopPrice'] = float(stop_price)
                    
                    # For STOP orders, also set price from stopPrice if no limitPrice
                    if order_type_num == 4 and stop_price is not None and order_dict.get('price') is None:
                        order_dict['price'] = float(stop_price)
                    
                    # Ensure quantity
                    quantity = order_dict.get('quantity') or order_dict.get('size') or 0
                    order_dict['quantity'] = quantity
                    
                    orders_list.append(order_dict)
                else:
                    # Fallback: Convert order object to dict
                    orders_list.append({
                        'id': getattr(order, 'id', None),
                        'symbol': getattr(order, 'symbol', None),
                        'side': getattr(order, 'side', None),
                        'quantity': getattr(order, 'quantity', None),
                        'type': getattr(order, 'type', None),
                        'status': getattr(order, 'status', None),
                        'price': getattr(order, 'price', None),
                        'limitPrice': getattr(order, 'limit_price', None),
                        'limit_price': getattr(order, 'limit_price', None),
                        'stopPrice': getattr(order, 'stop_price', None),
                        'stop_price': getattr(order, 'stop_price', None),
                        'contractId': getattr(order, 'contract_id', None),
                    })
            
            response = web.json_response({'orders': orders_list})
            response.headers['Access-Control-Allow-Origin'] = '*'
            return response
        except Exception as e:
            logger.error(f"❌ Error fetching orders for chart: {e}")
            import traceback
            logger.error(traceback.format_exc())
            response = web.json_response({'error': str(e), 'orders': []}, status=200)
            response.headers['Access-Control-Allow-Origin'] = '*'
            return response
    
    async def handle_get_contracts(request):
        """Handle contract list requests."""
        try:
            # Force fresh fetch
            contracts = await trading_bot.get_available_contracts(use_cache=False)
            logger.info(f"📋 Fetched {len(contracts)} contracts for chart dropdown")
            
            if not contracts:
                logger.warning("⚠️ No contracts returned - contract cache may be empty. Try running 'contracts' command in CLI first.")
            
            # Group by symbol
            by_symbol = {}
            for c in contracts:
                sym = c.get('symbol') or c.get('Symbol') or 'Unknown'
                contract_id = c.get('contractId') or c.get('ContractId') or c.get('id') or ''
                # Skip 'Unknown' symbols and empty symbols
                if sym and sym != 'Unknown' and sym.strip():
                    if sym not in by_symbol:
                        by_symbol[sym] = []
                    by_symbol[sym].append({'id': contract_id, 'description': c.get('description', '')})
            
            symbols_list = sorted(list(by_symbol.keys()))
            logger.info(f"✅ Grouped into {len(symbols_list)} unique symbols: {symbols_list[:10] if len(symbols_list) > 10 else symbols_list}")
            
            response = web.json_response({'contracts': by_symbol, 'symbols': symbols_list})
            response.headers['Access-Control-Allow-Origin'] = '*'
            return response
        except Exception as e:
            logger.error(f"❌ Error fetching contracts: {e}")
            import traceback
            logger.error(traceback.format_exc())
            response = web.json_response({'error': str(e), 'contracts': {}, 'symbols': []}, status=500)
            response.headers['Access-Control-Allow-Origin'] = '*'
            return response
    
    async def handle_reload_data(request):
        """Handle chart data reload requests."""
        try:
            # Get symbol and timeframe from query params, fallback to outer scope
            reload_symbol = request.query.get('symbol') or symbol
            reload_timeframe = request.query.get('timeframe') or timeframe
            limit = int(request.query.get('limit', 100))
            
            # Fetch fresh historical data
            bars = await trading_bot.get_historical_data(
                symbol=reload_symbol,
                timeframe=reload_timeframe,
                limit=limit
            )
            
            # Convert bars to chart format
            chart_data = []
            for bar in bars:
                try:
                    # Handle timestamp - can be datetime object or string
                    if hasattr(bar, 'timestamp'):
                        if isinstance(bar.timestamp, datetime):
                            ts = int(bar.timestamp.timestamp())
                        elif isinstance(bar.timestamp, str):
                            # Parse ISO string
                            from datetime import datetime as dt
                            dt_obj = dt.fromisoformat(bar.timestamp.replace('Z', '+00:00'))
                            ts = int(dt_obj.timestamp())
                        else:
                            ts = int(bar.timestamp)
                    elif isinstance(bar, dict):
                        ts_val = bar.get('timestamp')
                        if isinstance(ts_val, datetime):
                            ts = int(ts_val.timestamp())
                        elif isinstance(ts_val, str):
                            from datetime import datetime as dt
                            dt_obj = dt.fromisoformat(ts_val.replace('Z', '+00:00'))
                            ts = int(dt_obj.timestamp())
                        else:
                            ts = int(ts_val) if ts_val else 0
                    else:
                        continue
                    
                    chart_data.append({
                        'time': ts,
                        'open': float(bar.open if hasattr(bar, 'open') else bar.get('open', 0)),
                        'high': float(bar.high if hasattr(bar, 'high') else bar.get('high', 0)),
                        'low': float(bar.low if hasattr(bar, 'low') else bar.get('low', 0)),
                        'close': float(bar.close if hasattr(bar, 'close') else bar.get('close', 0)),
                        'volume': float(bar.volume if hasattr(bar, 'volume') else bar.get('volume', 0))
                    })
                except Exception as e:
                    logger.warning(f"Failed to convert bar: {e}")
                    continue
            
            response = web.json_response({'bars': chart_data, 'symbol': reload_symbol, 'timeframe': reload_timeframe})
            response.headers['Access-Control-Allow-Origin'] = '*'
            return response
        except Exception as e:
            logger.error(f"Error reloading chart data: {e}")
            import traceback
            logger.error(traceback.format_exc())
            response = web.json_response({'error': str(e), 'bars': []}, status=500)
            response.headers['Access-Control-Allow-Origin'] = '*'
            return response
    
    async def handle_strategy_status(request):
        """Get strategy status - includes all available strategies, not just active ones."""
        try:
            if not hasattr(trading_bot, 'strategy_manager'):
                return web.json_response({'strategies': {}, 'available': [], 'error': 'Strategy manager not available'})
            
            # Get all loaded strategies
            statuses = {}
            for name, strategy in trading_bot.strategy_manager.strategies.items():
                if strategy:
                    statuses[name] = {
                        'name': name,
                        'status': getattr(strategy, 'status', {}).name if hasattr(getattr(strategy, 'status', None), 'name') else str(getattr(strategy, 'status', 'unknown')),
                        'active': getattr(strategy, 'status', None) == getattr(strategy.__class__, 'Status', type('Status', (), {'ACTIVE': 'active'}))().ACTIVE if hasattr(strategy.__class__, 'Status') else False,
                        'symbols': getattr(strategy.config, 'symbols', []) if hasattr(strategy, 'config') else [],
                        'positions': len(getattr(strategy, 'active_positions', [])) if hasattr(strategy, 'active_positions') else 0
                    }
            
            # Get all available strategies (registered but not necessarily loaded)
            available_strategies = list(trading_bot.strategy_manager.available_strategies.keys()) if hasattr(trading_bot.strategy_manager, 'available_strategies') else []
            
            # Add available strategies that aren't loaded yet
            for name in available_strategies:
                if name not in statuses:
                    statuses[name] = {
                        'name': name,
                        'status': 'idle',
                        'active': False,
                        'symbols': [],
                        'positions': 0,
                        'available': True
                    }
            
            response = web.json_response({
                'strategies': statuses,
                'available': available_strategies,
                'active': list(trading_bot.strategy_manager.active_strategies) if hasattr(trading_bot.strategy_manager, 'active_strategies') else []
            })
            response.headers['Access-Control-Allow-Origin'] = '*'
            return response
        except Exception as e:
            logger.error(f"Error fetching strategy status: {e}")
            import traceback
            logger.error(traceback.format_exc())
            response = web.json_response({'error': str(e), 'strategies': {}, 'available': []}, status=500)
            response.headers['Access-Control-Allow-Origin'] = '*'
            return response
    
    async def handle_strategy_start(request):
        """Start a strategy."""
        try:
            data = await request.json()
            strategy_name = data.get('strategy')
            symbols = data.get('symbols', [])
            
            if not hasattr(trading_bot, 'strategy_manager'):
                return web.json_response({'success': False, 'error': 'Strategy manager not available'})
            
            success, message = await trading_bot.strategy_manager.start_strategy(strategy_name, symbols)
            response = web.json_response({'success': success, 'message': message})
            response.headers['Access-Control-Allow-Origin'] = '*'
            return response
        except Exception as e:
            logger.error(f"Error starting strategy: {e}")
            response = web.json_response({'success': False, 'error': str(e)}, status=500)
            response.headers['Access-Control-Allow-Origin'] = '*'
            return response
    
    async def handle_strategy_stop(request):
        """Stop a strategy."""
        try:
            data = await request.json()
            strategy_name = data.get('strategy')
            
            if not hasattr(trading_bot, 'strategy_manager'):
                return web.json_response({'success': False, 'error': 'Strategy manager not available'})
            
            success, message = await trading_bot.strategy_manager.stop_strategy(strategy_name)
            response = web.json_response({'success': success, 'message': message})
            response.headers['Access-Control-Allow-Origin'] = '*'
            return response
        except Exception as e:
            logger.error(f"Error stopping strategy: {e}")
            response = web.json_response({'success': False, 'error': str(e)}, status=500)
            response.headers['Access-Control-Allow-Origin'] = '*'
            return response
    
    async def handle_flatten(request):
        """Handle flatten command - close all positions and cancel all orders."""
        try:
            # Call trading bot's flatten_all_positions method
            result = await trading_bot.flatten_all_positions(interactive=False)
            
            # Parse result based on format (could be dict or string)
            if isinstance(result, dict):
                response = web.json_response(result)
            elif isinstance(result, str):
                # Try to parse JSON string
                import json
                try:
                    result_dict = json.loads(result)
                    response = web.json_response(result_dict)
                except json.JSONDecodeError:
                    response = web.json_response({'success': False, 'error': result})
            else:
                response = web.json_response({'success': True, 'message': 'Flatten completed'})
            
            response.headers['Access-Control-Allow-Origin'] = '*'
            return response
        except Exception as e:
            logger.error(f"Error flattening positions: {e}")
            response = web.json_response({'success': False, 'error': str(e)}, status=500)
            response.headers['Access-Control-Allow-Origin'] = '*'
            return response
    
    async def handle_cancel_all(request):
        """Handle cancel all orders command."""
        try:
            # Get all orders
            orders = await trading_bot.get_orders(account_id=trading_bot.selected_account)
            
            if not orders:
                return web.json_response({'success': True, 'message': 'No orders to cancel', 'canceled': 0})
            
            # Cancel each order
            canceled = []
            failed = []
            for order in orders:
                try:
                    order_id = order.get('orderId') or order.get('id')
                    if order_id:
                        await trading_bot.cancel_order(order_id, account_id=trading_bot.selected_account)
                        canceled.append(order_id)
                except Exception as e:
                    logger.error(f"Failed to cancel order {order_id}: {e}")
                    failed.append({'order_id': order_id, 'error': str(e)})
            
            response = web.json_response({
                'success': len(failed) == 0,
                'canceled': len(canceled),
                'failed': len(failed),
                'canceled_orders': canceled,
                'failed_orders': failed
            })
            response.headers['Access-Control-Allow-Origin'] = '*'
            return response
        except Exception as e:
            logger.error(f"Error canceling orders: {e}")
            response = web.json_response({'success': False, 'error': str(e)}, status=500)
            response.headers['Access-Control-Allow-Origin'] = '*'
            return response

    app.router.add_get('/api/chart/quote', handle_quote)
    app.router.add_options('/api/chart/quote', handle_options)
    app.router.add_post('/api/chart/order', handle_place_order)
    app.router.add_options('/api/chart/order', handle_options)
    app.router.add_get('/api/chart/positions', handle_get_positions)
    app.router.add_options('/api/chart/positions', handle_options)
    app.router.add_get('/api/chart/orders', handle_get_orders)
    app.router.add_options('/api/chart/orders', handle_options)
    app.router.add_get('/api/chart/contracts', handle_get_contracts)
    app.router.add_options('/api/chart/contracts', handle_options)
    app.router.add_get('/api/chart/reload', handle_reload_data)
    app.router.add_options('/api/chart/reload', handle_options)
    # Strategy endpoints
    app.router.add_get('/api/chart/strategy/status', handle_strategy_status)
    app.router.add_options('/api/chart/strategy/status', handle_options)
    app.router.add_post('/api/chart/strategy/start', handle_strategy_start)
    app.router.add_options('/api/chart/strategy/start', handle_options)
    app.router.add_post('/api/chart/strategy/stop', handle_strategy_stop)
    app.router.add_options('/api/chart/strategy/stop', handle_options)
    # Account state endpoint
    app.router.add_get('/api/chart/account/state', handle_account_state)
    app.router.add_options('/api/chart/account/state', handle_options)
    # Flatten and cancel endpoints
    app.router.add_post('/api/chart/flatten', handle_flatten)
    app.router.add_options('/api/chart/flatten', handle_options)
    app.router.add_post('/api/chart/cancel_all', handle_cancel_all)
    app.router.add_options('/api/chart/cancel_all', handle_options)
    
    # Serve master control HTML
    async def handle_master_control(request):
        """Serve the master control HTML page."""
        try:
            # Read master control HTML file
            from pathlib import Path
            master_html_path = Path(__file__).parent / 'master_control.html'
            if not master_html_path.exists():
                # Return a simple HTML that loads from the generated file
                html_content = f"""<!DOCTYPE html>
<html>
<head>
    <title>Master Trading Control</title>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; margin: 0; padding: 20px; background: #1a1a1a; color: #fff; }}
        .error {{ color: #ff4444; padding: 20px; text-align: center; }}
    </style>
</head>
<body>
    <div class="error">
        <h1>Master Control HTML Not Found</h1>
        <p>Please create gui/master_control.html</p>
    </div>
</body>
</html>"""
            else:
                with open(master_html_path, 'r', encoding='utf-8') as f:
                    html_content = f.read()
            
            # Replace placeholder with actual server port
            html_content = html_content.replace('{{SERVER_PORT}}', str(port))
            html_content = html_content.replace('{{SYMBOL}}', symbol)
            
            response = web.Response(text=html_content, content_type='text/html')
            response.headers['Access-Control-Allow-Origin'] = '*'
            return response
        except Exception as e:
            logger.error(f"Error serving master control: {e}")
            response = web.Response(text=f"Error: {e}", status=500)
            return response
    
    app.router.add_get('/', handle_master_control)
    app.router.add_get('/master', handle_master_control)
    
    # Find available port
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    sock.close()
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', port)
    await site.start()
    
    _chart_server = runner
    _chart_server_port = port
    _chart_server_trading_bot = trading_bot
    
    logger.info(f"📡 Chart server started on http://127.0.0.1:{port}")
    return port


def generate_chart_html(
    symbol: str,
    timeframe: str,
    bars: List[Dict],
    output_path: Optional[str] = None,
    realtime: bool = False,
    backtest: bool = False,
    backtest_speed: float = 1.0,
    server_port: Optional[int] = None
) -> str:
    """
    Generate standalone HTML file with TradingView Lightweight Charts.
    
    Args:
        symbol: Trading symbol (e.g., 'MNQ')
        timeframe: Timeframe (e.g., '5m')
        bars: List of bar dictionaries with keys: timestamp, open, high, low, close, volume
        output_path: Optional path to save HTML file
        realtime: Enable real-time updates
        backtest: Enable backtesting mode
        backtest_speed: Playback speed multiplier
        server_port: Port for real-time server (if realtime=True)
    
    Returns:
        Path to generated HTML file
    """
    # Prepare data for TradingView format
    chart_data = []
    for bar in bars:
        # Convert timestamp to seconds (TradingView expects Unix timestamp in seconds)
        if isinstance(bar.get('timestamp'), str):
            from datetime import datetime as dt
            try:
                ts = dt.fromisoformat(bar['timestamp'].replace('Z', '+00:00'))
                timestamp_sec = int(ts.timestamp())
            except:
                continue
        elif isinstance(bar.get('timestamp'), (int, float)):
            # If in milliseconds, convert to seconds
            timestamp_sec = int(bar['timestamp'] / 1000) if bar['timestamp'] > 1e12 else int(bar['timestamp'])
        else:
            continue
        
        chart_data.append({
            'time': timestamp_sec,
            'open': float(bar.get('open', 0)),
            'high': float(bar.get('high', 0)),
            'low': float(bar.get('low', 0)),
            'close': float(bar.get('close', 0)),
            'volume': int(bar.get('volume', 0))
        })
    
    if not chart_data:
        raise ValueError("No valid bar data provided")
    
    # Sort chart_data by time to ensure chronological order
    chart_data.sort(key=lambda x: x['time'])
    
    # Generate HTML with TradingView Lightweight Charts
    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{symbol} {timeframe} Chart</title>
    <script src="https://unpkg.com/lightweight-charts@4.1.3/dist/lightweight-charts.standalone.production.js"></script>
    <style>
        body {{
            margin: 0;
            padding: 20px;
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
            background: #1a1a1a;
            color: #ffffff;
        }}
        .container {{
            max-width: 1400px;
            margin: 0 auto;
        }}
        .header {{
            margin-bottom: 20px;
        }}
        .header h1 {{
            margin: 0;
            font-size: 24px;
            color: #ffffff;
        }}
        .header .info {{
            margin-top: 5px;
            color: #888;
            font-size: 14px;
        }}
        #chart-container {{
            width: 100%;
            height: 600px;
            background: #1a1a1a;
            border-radius: 8px;
            overflow: hidden;
        }}
        .controls {{
            margin-top: 20px;
            padding: 15px;
            background: #2a2a2a;
            border-radius: 8px;
        }}
        .controls button {{
            background: #2962ff;
            color: white;
            border: none;
            padding: 10px 20px;
            border-radius: 4px;
            cursor: pointer;
            font-size: 14px;
            margin-right: 10px;
        }}
        .controls button:hover {{
            background: #1e53e5;
        }}
        .controls button.active {{
            background: #26a69a;
        }}
        .controls button:disabled {{
            background: #555;
            cursor: not-allowed;
        }}
        .status {{
            margin-top: 10px;
            color: #888;
            font-size: 12px;
        }}
        .toast {{
            position: fixed;
            top: 20px;
            right: 20px;
            background: #2a2a2a;
            color: #ffffff;
            padding: 16px 24px;
            border-radius: 8px;
            box-shadow: 0 4px 12px rgba(0, 0, 0, 0.5);
            z-index: 10000;
            min-width: 300px;
            max-width: 500px;
            border-left: 4px solid #2962ff;
            animation: slideIn 0.3s ease-out;
        }}
        .toast.success {{
            border-left-color: #26a69a;
        }}
        .toast.error {{
            border-left-color: #ef5350;
        }}
        .toast.warning {{
            border-left-color: #ffa726;
        }}
        @keyframes slideIn {{
            from {{
                transform: translateX(400px);
                opacity: 0;
            }}
            to {{
                transform: translateX(0);
                opacity: 1;
            }}
        }}
        .toast-title {{
            font-weight: bold;
            margin-bottom: 4px;
            font-size: 14px;
        }}
        .toast-message {{
            font-size: 12px;
            color: #d1d5db;
            line-height: 1.4;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>{symbol} {timeframe} Chart</h1>
            <div class="info">
                {len(chart_data)} bars | Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
            </div>
        </div>
        <div id="chart-container"></div>
        {(f'''
        <div class="trading-panel" id="tradingPanel" style="background: #2a2a2a; padding: 10px; margin: 10px 0; border-radius: 8px; border: 1px solid #444;">
            <div style="display: flex; gap: 8px; align-items: center; flex-wrap: wrap;">
                <select id="symbolSelect" onchange="updateSymbol()" style="background: #1a1a1a; color: #d1d5db; border: 1px solid #444; padding: 6px; border-radius: 4px; font-size: 12px;">
                    <option value="{symbol.upper()}" selected>{symbol.upper()}</option>
                </select>
                <select id="timeframeSelect" onchange="updateTimeframe()" style="background: #1a1a1a; color: #d1d5db; border: 1px solid #444; padding: 6px; border-radius: 4px; font-size: 12px;">
                    <option value="30s"{' selected' if timeframe == '30s' else ''}>30s</option>
                    <option value="1m"{' selected' if timeframe == '1m' else ''}>1m</option>
                    <option value="5m"{' selected' if timeframe == '5m' else ''}>5m</option>
                    <option value="15m"{' selected' if timeframe == '15m' else ''}>15m</option>
                    <option value="1h"{' selected' if timeframe == '1h' else ''}>1h</option>
                    <option value="4h"{' selected' if timeframe == '4h' else ''}>4h</option>
                    <option value="1d"{' selected' if timeframe == '1d' else ''}>1d</option>
                </select>
                <select id="orderTypeSelect" onchange="updateOrderType()" style="background: #1a1a1a; color: #d1d5db; border: 1px solid #444; padding: 6px; border-radius: 4px; font-size: 12px; width: 80px;">
                    <option value="market" selected>Market</option>
                    <option value="limit">Limit</option>
                    <option value="stop">Stop</option>
                    <option value="bracket">Bracket</option>
                </select>
                <input type="number" id="quantityInput" value="1" min="1" placeholder="Qty" style="background: #1a1a1a; color: #d1d5db; border: 1px solid #444; padding: 6px; border-radius: 4px; font-size: 12px; width: 60px;">
                <input type="number" id="limitPriceInput" step="0.25" placeholder="Limit" style="background: #1a1a1a; color: #d1d5db; border: 1px solid #444; padding: 6px; border-radius: 4px; font-size: 12px; width: 80px; display: none;">
                <input type="number" id="stopPriceInput" step="0.25" placeholder="Stop" style="background: #1a1a1a; color: #d1d5db; border: 1px solid #444; padding: 6px; border-radius: 4px; font-size: 12px; width: 80px; display: none;">
                <label style="display: flex; align-items: center; gap: 4px; font-size: 11px; color: #888;">
                    <input type="checkbox" id="enableBracketCheck" onchange="updateBracket()" style="margin: 0;">
                    <span>Bracket</span>
                </label>
                <input type="number" id="stopLossPriceInput" step="0.25" placeholder="SL" style="background: #1a1a1a; color: #d1d5db; border: 1px solid #444; padding: 6px; border-radius: 4px; font-size: 12px; width: 70px; display: none;">
                <input type="number" id="takeProfitPriceInput" step="0.25" placeholder="TP" style="background: #1a1a1a; color: #d1d5db; border: 1px solid #444; padding: 6px; border-radius: 4px; font-size: 12px; width: 70px; display: none;">
                <button onclick="placeOrder('BUY')" style="background: #26a69a; color: white; border: none; padding: 6px 12px; border-radius: 4px; cursor: pointer; font-weight: bold; font-size: 12px;">BUY</button>
                <button onclick="placeOrder('SELL')" style="background: #ef5350; color: white; border: none; padding: 6px 12px; border-radius: 4px; cursor: pointer; font-weight: bold; font-size: 12px;">SELL</button>
            </div>
        </div>
        ''') if not backtest else ''}
        <div class="controls">
            <button onclick="refreshChart()">Refresh Data</button>
            <button onclick="exportData()">Export CSV</button>
            <button onclick="updatePositionLines(); updateOrderLines();" style="background: #26a69a; color: white; border: none; padding: 6px 12px; border-radius: 4px; cursor: pointer; font-size: 12px;">Refresh Lines</button>
            {'<button id="realtimeBtn" onclick="toggleRealtime()">Start Real-Time</button>' if not backtest else ''}
            {'<div style="display: inline-block; margin-left: 10px;">Refresh: <select id="refreshRateSelect" onchange="updateRefreshRate()" style="background: #2a2a2a; color: #d1d5db; border: 1px solid #444; padding: 5px;"><option value="1" selected>1x/sec</option><option value="3">3x/sec</option><option value="6">6x/sec</option><option value="12">12x/sec</option></select></div>' if not backtest else ''}
            {'<button id="backtestBtn" onclick="toggleBacktest()">Start Backtest</button>' if backtest else ''}
            {'<button id="pauseBtn" onclick="togglePause()" style="background: #666; display: none;">Pause</button>' if backtest else ''}
            {'<button onclick="loadAllBars()" style="background: #26a69a;">Load All Bars (Test)</button>' if backtest else ''}
            {'<button onclick="exitTestMode()" style="background: #666;">Exit Test Mode</button>' if backtest else ''}
            {'<div style="display: inline-block; margin-left: 10px;">Speed: <select id="speedSelect" onchange="updateBacktestSpeed()" style="background: #2a2a2a; color: #d1d5db; border: 1px solid #444; padding: 5px;"><option value="1">1x</option><option value="2">2x</option><option value="5">5x</option><option value="10">10x</option><option value="25">25x</option><option value="50" selected>50x</option><option value="100">100x</option><option value="150">150x</option><option value="200">200x</option><option value="250">250x</option><option value="300">300x</option><option value="400">400x</option><option value="500">500x</option><option value="750">750x</option><option value="1000">1000x</option></select></div>' if backtest else ''}
            <div class="status" id="status">Chart loaded</div>
        </div>
    </div>

    <script>
        let chart = null;
        let candlestickSeries = null;
        let volumeSeries = null;
        let chartContainer = null; // Global scope for resize handler
        let positionPriceLines = []; // Position price lines (entry, stop loss, take profit)
        let orderPriceLines = []; // Order price lines (limit/stop orders)
        let lastPositionUpdate = 0; // Throttle position updates (max once per 5 seconds)
        let lastOrderUpdate = 0; // Throttle order updates (max once per 5 seconds)
        const POSITION_UPDATE_INTERVAL = 3000; // 3 seconds (reduced for faster updates)
        const ORDER_UPDATE_INTERVAL = 3000; // 3 seconds (reduced for faster updates)
        let chartData = {json.dumps(chart_data)};
        let realtimeActive = {'true' if realtime else 'false'};
        let backtestMode = {'true' if backtest else 'false'}; // Mode enabled, not necessarily running
        let backtestActive = false; // Actually running
        let backtestPaused = false; // Paused state
        let backtestIndex = 0;
        let backtestInterval = null;
        let realtimeInterval = null;
        let realtimeRefreshRate = 1; // Updates per second (default: 1x/sec)
        let serverPort = {server_port if server_port else 'null'};
        let backtestSpeed = {backtest_speed}; // Speed multiplier (can be changed via dropdown)
        const baseBacktestIntervalMs = 150000; // Base interval (2.5 minutes for 5m bars)
        const symbol = '{symbol}';
        const timeframe = '{timeframe}';
        let testModeActive = false; // Flag to prevent backtest from interfering with test mode
        
        // Debug: Log initial state
        console.log('Chart initialized:', {{
            chartDataLength: chartData.length,
            backtestMode: backtestMode,
            backtestActive: backtestActive,
            firstBar: chartData[0],
            lastBar: chartData[chartData.length - 1]
        }});
        
        // Calculate interval based on timeframe (in seconds)
        function getTimeframeSeconds(tf) {{
            const match = tf.match(/(\\d+)([mhd])/);
            if (!match) return 60;
            const value = parseInt(match[1]);
            const unit = match[2];
            if (unit === 'm') return value * 60;
            if (unit === 'h') return value * 3600;
            if (unit === 'd') return value * 86400;
            return 60;
        }}
        
        const timeframeSeconds = getTimeframeSeconds(timeframe);
        const backtestIntervalMs = (timeframeSeconds * 1000) / backtestSpeed;
        
        function showToast(title, message, type = 'info') {{
            // Remove existing toasts
            const existingToasts = document.querySelectorAll('.toast');
            existingToasts.forEach(toast => toast.remove());
            
            const toast = document.createElement('div');
            toast.className = `toast ${{type}}`;
            toast.innerHTML = `
                <div class="toast-title">${{title}}</div>
                <div class="toast-message">${{message}}</div>
            `;
            document.body.appendChild(toast);
            
            // Auto-remove after 5 seconds
            setTimeout(() => {{
                toast.style.animation = 'slideIn 0.3s ease-out reverse';
                setTimeout(() => toast.remove(), 300);
            }}, 5000);
        }}
        
        function showError(message) {{
            const statusEl = document.getElementById('status');
            if (statusEl) {{
                statusEl.textContent = 'Error: ' + message;
                statusEl.style.color = '#ef5350';
            }}
            showToast('Error', message, 'error');
            console.error(message);
        }}
        
        function updateStatus(message) {{
            const statusEl = document.getElementById('status');
            if (statusEl) {{
                statusEl.textContent = message;
                statusEl.style.color = '#888';
            }}
        }}
        
        // Initialize chart after library loads
        function initChart() {{
            try {{
                // Prevent double initialization
                if (chart) {{
                    console.log('Chart already initialized, skipping');
                    return;
                }}
                
                // Check if library loaded
                if (typeof LightweightCharts === 'undefined') {{
                    showError('TradingView library failed to load');
                    return;
                }}
                
                if (!chartData || chartData.length === 0) {{
                    showError('No chart data available');
                    return;
                }}
                
                // Get chart container (assign to global variable)
                chartContainer = document.getElementById('chart-container');
                if (!chartContainer) {{
                    showError('Chart container not found');
                    return;
                }}
                
                // Ensure container has dimensions
                const containerWidth = chartContainer.clientWidth || chartContainer.offsetWidth || 1200;
                const containerHeight = 600;
                
                console.log('Chart container dimensions:', containerWidth, 'x', containerHeight);
                
                // Create chart
                chart = LightweightCharts.createChart(chartContainer, {{
                    layout: {{
                        background: {{ color: '#1a1a1a' }},
                        textColor: '#d1d5db',
                    }},
                    grid: {{
                        vertLines: {{ color: '#2a2a2a' }},
                        horzLines: {{ color: '#2a2a2a' }},
                    }},
                    crosshair: {{
                        mode: LightweightCharts.CrosshairMode.Normal,
                    }},
                    rightPriceScale: {{
                        borderColor: '#2a2a2a',
                        autoScale: true,
                    }},
                    timeScale: {{
                        borderColor: '#2a2a2a',
                        timeVisible: true,
                        secondsVisible: true,
                    }},
                    localization: {{
                        timeFormatter: (timestamp) => {{
                            // Convert UTC timestamp to local time for display
                            const date = new Date(timestamp * 1000);
                            return date.toLocaleTimeString('en-US', {{
                                hour: '2-digit',
                                minute: '2-digit',
                                hour12: false
                            }});
                        }},
                    }},
                    timeScale: {{
                        borderColor: '#2a2a2a',
                        timeVisible: true,
                        secondsVisible: false,
                    }},
                    width: containerWidth,
                    height: containerHeight,
                }});
                
                console.log('Chart created successfully');
                
                // Create candlestick series
                if (typeof chart.addCandlestickSeries !== 'function') {{
                    showError('addCandlestickSeries not available');
                    console.error('Available methods:', Object.getOwnPropertyNames(chart));
                    return;
                }}
                
                candlestickSeries = chart.addCandlestickSeries({{
                    upColor: '#26a69a',
                    downColor: '#ef5350',
                    borderVisible: false,
                    wickUpColor: '#26a69a',
                    wickDownColor: '#ef5350',
                    priceScaleId: 'right',
                    visible: true,
                }});
                
                // Create volume series
                volumeSeries = chart.addHistogramSeries({{
                    color: '#26a69a',
                    priceFormat: {{
                        type: 'volume',
                    }},
                    priceScaleId: 'volume',
                    scaleMargins: {{
                        top: 0.8,
                        bottom: 0,
                    }},
                }});
                
                // Set up volume price scale
                chart.priceScale('volume').applyOptions({{
                    scaleMargins: {{
                        top: 0.8,
                        bottom: 0,
                    }},
                }});
                
                // Prepare initial data
                if (!backtestMode) {{
                    // Normal mode: load all data immediately
                    const candlestickData = chartData.map(bar => ({{
                        time: bar.time,
                        open: parseFloat(bar.open),
                        high: parseFloat(bar.high),
                        low: parseFloat(bar.low),
                        close: parseFloat(bar.close),
                    }}));
                    
                    const volumeData = chartData.map(bar => ({{
                        time: bar.time,
                        value: parseInt(bar.volume) || 0,
                        color: parseFloat(bar.close) >= parseFloat(bar.open) ? '#26a69a80' : '#ef535080',
                    }}));
                    
                    // Set data
                    candlestickSeries.setData(candlestickData);
                    volumeSeries.setData(volumeData);
                    
                    // Explicitly set visible range to show all data
                    // Use requestAnimationFrame to ensure chart has processed setData()
                    if (candlestickData.length > 0) {{
                        const firstBar = candlestickData[0];
                        const lastBar = candlestickData[candlestickData.length - 1];
                        
                        requestAnimationFrame(() => {{
                            try {{
                                chart.timeScale().setVisibleRange({{
                                    from: firstBar.time,
                                    to: lastBar.time
                                }});
                                console.log('Normal mode: Set visible range from', firstBar.time, 'to', lastBar.time);
                            }} catch (e) {{
                                console.error('Error setting visible range:', e);
                                chart.timeScale().fitContent();
                            }}
                        }});
                    }} else {{
                        chart.timeScale().fitContent();
                    }}
                    
                    updateStatus('Chart ready - ' + chartData.length + ' bars loaded');
                }} else {{
                    // Backtest mode: initialize with empty arrays, ready for replay
                    candlestickSeries.setData([]);
                    volumeSeries.setData([]);
                    
                    updateStatus('Chart ready - ' + chartData.length + ' bars loaded. Click "Start Backtest" to begin replay.');
                    console.log('Backtest mode: Chart initialized with empty series, ready for replay');
                }}
                
                // Load contracts and initialize UI
                if (serverPort) {{
                    loadContracts();
                    updateOrderType(); // Initialize order type visibility
                    updateBracket(); // Initialize bracket visibility
                    
                    // Initialize position/order lines after chart is ready
                    setTimeout(async () => {{
                        console.log('Chart initialized, updating position/order lines...');
                        lastPositionUpdate = 0; // Reset throttle to force update
                        lastOrderUpdate = 0; // Reset throttle to force update
                        await updatePositionLines();
                        await updateOrderLines();
                        
                        // Note: Position/order lines are now event-based only (refresh on order placement/fill)
                        // No periodic refresh interval - lines update when orders are placed or filled
                    }}, 1500);
                }}
                
                // Auto-start real-time if enabled (start immediately)
                if (realtimeActive && serverPort) {{
                    // Small delay to ensure everything is initialized
                    setTimeout(() => {{
                        toggleRealtime();
                    }}, 100);
                }}
            }} catch (error) {{
                showError('Chart error: ' + error.message);
                console.error('Full error:', error);
            }}
        }}
        
        // Real-time update function
        async function updateRealtime() {{
            if (!serverPort) {{
                updateStatus('Real-time server not available');
                return;
            }}
            
            try {{
                // Get current symbol from dropdown
                const currentSymbol = document.getElementById('symbolSelect')?.value || symbol;
                const response = await fetch(`http://127.0.0.1:${{serverPort}}/api/chart/quote?symbol=${{encodeURIComponent(currentSymbol)}}`);
                if (!response.ok) {{
                    throw new Error(`HTTP ${{response.status}}`);
                }}
                const data = await response.json();
                
                // Update position and order lines periodically (throttled)
                const now = Date.now();
                if (now - lastPositionUpdate > POSITION_UPDATE_INTERVAL) {{
                    lastPositionUpdate = now;
                    updatePositionLines().catch(err => console.debug('Position update error:', err));
                }}
                if (now - lastOrderUpdate > ORDER_UPDATE_INTERVAL) {{
                    lastOrderUpdate = now;
                    updateOrderLines().catch(err => console.debug('Order update error:', err));
                }}
                
                if (data.latest_bar) {{
                    const bar = data.latest_bar;
                    if (!chartData || chartData.length === 0) {{
                        console.warn('No chart data available for real-time update');
                        return;
                    }}
                    
                    const lastBar = chartData[chartData.length - 1];
                    
                    // Normalize timestamps to numbers for comparison
                    // TradingView expects timestamps as Unix seconds (number)
                    const barTime = typeof bar.time === 'number' ? bar.time : (typeof bar.time === 'string' ? parseInt(bar.time) : null);
                    const lastBarTime = typeof lastBar.time === 'number' ? lastBar.time : (typeof lastBar.time === 'string' ? parseInt(lastBar.time) : null);
                    
                    if (barTime === null || lastBarTime === null) {{
                        console.warn('Invalid timestamp format in real-time update:', {{ barTime, lastBarTime, bar, lastBar }});
                        return;
                    }}
                    
                    // Check if this is a new bar or update to current bar
                    if (barTime === lastBarTime) {{
                        // Update current bar
                        lastBar.high = Math.max(lastBar.high, bar.close);
                        lastBar.low = Math.min(lastBar.low, bar.close);
                        lastBar.close = bar.close;
                        lastBar.volume = bar.volume;
                        
                        // Update chart
                        try {{
                            candlestickSeries.update({{
                                time: barTime,
                                open: lastBar.open,
                                high: lastBar.high,
                                low: lastBar.low,
                                close: lastBar.close,
                            }});
                            
                            volumeSeries.update({{
                                time: barTime,
                                value: bar.volume,
                                color: bar.close >= lastBar.open ? '#26a69a80' : '#ef535080',
                            }});
                        }} catch (updateError) {{
                            console.warn('Chart update error (same time):', updateError);
                        }}
                    }} else if (barTime > lastBarTime) {{
                        // New bar - ensure timestamp is normalized
                        const newBar = {{
                            ...bar,
                            time: barTime
                        }};
                        chartData.push(newBar);
                        
                        try {{
                            candlestickSeries.update({{
                                time: barTime,
                                open: bar.open,
                                high: bar.high,
                                low: bar.low,
                                close: bar.close,
                            }});
                            
                            volumeSeries.update({{
                                time: barTime,
                                value: bar.volume,
                                color: bar.close >= bar.open ? '#26a69a80' : '#ef535080',
                            }});
                        }} catch (updateError) {{
                            console.warn('Chart update error (new bar):', updateError);
                            // Remove the bar we just added if update failed
                            chartData.pop();
                        }}
                    }} else {{
                        // barTime < lastBarTime - this should not happen, but handle gracefully
                        console.warn(`Skipping real-time update: new bar time (${{barTime}}) is older than last bar time (${{lastBarTime}}). This may indicate clock skew or delayed data.`);
                        // Don't update the chart - TradingView doesn't allow updating older bars
                    }}
                    
                    updateStatus(`Real-time: ${{data.quote.last || data.quote.bid || 'N/A'}} | ${{new Date().toLocaleTimeString()}}`);
                }}
            }} catch (error) {{
                console.error('Real-time update error:', error);
                updateStatus('Real-time update failed: ' + error.message);
            }}
        }}
        
        // Toggle real-time updates
        function toggleRealtime() {{
            const btn = document.getElementById('realtimeBtn');
            if (!btn) return;
            
            if (realtimeActive) {{
                // Stop
                if (realtimeInterval) {{
                    clearInterval(realtimeInterval);
                    realtimeInterval = null;
                }}
                // Note: positionOrderRefreshInterval continues running even when real-time is stopped
                // This ensures lines update when positions/orders are closed
                realtimeActive = false;
                btn.textContent = 'Start Real-Time';
                btn.classList.remove('active');
                updateStatus('Real-time updates stopped');
            }} else {{
                // Start
                if (!serverPort) {{
                    showError('Real-time server not available. Start chart with --realtime flag.');
                    return;
                }}
                realtimeActive = true;
                btn.textContent = 'Stop Real-Time';
                btn.classList.add('active');
                updateRealtime(); // Immediate update
                
                // Calculate interval based on refresh rate (updates per second)
                const intervalMs = 1000 / realtimeRefreshRate;
                realtimeInterval = setInterval(updateRealtime, intervalMs);
                updateStatus(`Real-time updates started at ${{realtimeRefreshRate}}x/sec`);
            }}
        }}
        
        // Backtesting function
        function playBacktestBar() {{
            // Don't play if paused - keep all rendered candles visible
            if (backtestPaused) {{
                return;
            }}
            
            if (backtestIndex >= chartData.length) {{
                // Finished
                toggleBacktest();
                updateStatus('Backtest complete - ' + chartData.length + ' bars replayed');
                return;
            }}
            
            const bar = chartData[backtestIndex];
            
            try {{
                if (!candlestickSeries || !volumeSeries) {{
                    console.error('Chart series not initialized');
                    showError('Chart series not initialized');
                    return;
                }}
                
                // Prepare bar data
                const barData = {{
                    time: bar.time,
                    open: parseFloat(bar.open),
                    high: parseFloat(bar.high),
                    low: parseFloat(bar.low),
                    close: parseFloat(bar.close),
                }};
                
                const volumeData = {{
                    time: bar.time,
                    value: parseInt(bar.volume) || 0,
                    color: parseFloat(bar.close) >= parseFloat(bar.open) ? '#26a69a80' : '#ef535080',
                }};
                
                // Use update() to add/update bars
                candlestickSeries.update(barData);
                volumeSeries.update(volumeData);
                
                // Verify update worked
                const currentData = candlestickSeries.data();
                if (backtestIndex < 5) {{
                    console.log('Backtest bar', backtestIndex + 1, ':', barData);
                    console.log('Total bars in series:', currentData.length);
                }}
                
                // Don't auto-fit content - let user control zoom/pan manually
                // Removed fitContent() calls to allow manual zoom control
                
                const date = new Date(bar.time * 1000);
                updateStatus(`Backtest: ${{backtestIndex + 1}}/${{chartData.length}} | Speed: ${{backtestSpeed}}x | ${{date.toLocaleString()}}`);
            }} catch (error) {{
                console.error('Error playing backtest bar:', error);
                console.error('Bar data:', bar);
                showError('Error playing bar ' + (backtestIndex + 1) + ': ' + error.message);
            }}
            
            backtestIndex++;
        }}
        
        // Toggle backtesting
        function toggleBacktest() {{
            console.log('🔔 toggleBacktest() called');
            console.trace('Call stack:');
            
            // Don't allow backtest to start if we're in test mode
            if (testModeActive && !backtestActive) {{
                console.log('⚠️  Ignoring toggleBacktest() call - test mode is active');
                return;
            }}
            
            const btn = document.getElementById('backtestBtn');
            if (!btn) {{
                console.error('Backtest button not found');
                return;
            }}
            
            if (backtestActive) {{
                // Stop
                if (backtestInterval) {{
                    clearInterval(backtestInterval);
                    backtestInterval = null;
                }}
                backtestActive = false;
                backtestPaused = false; // Reset pause state when stopping
                btn.textContent = 'Start Backtest';
                btn.classList.remove('active');
                
                // Hide pause button
                const pauseBtn = document.getElementById('pauseBtn');
                if (pauseBtn) {{
                    pauseBtn.style.display = 'none';
                }}
                
                updateStatus('Backtest stopped at bar ' + backtestIndex + '/' + chartData.length);
            }} else {{
                // Start
                if (!chart || !candlestickSeries || !volumeSeries) {{
                    showError('Chart not initialized. Please wait...');
                    return;
                }}
                
                if (!chartData || chartData.length === 0) {{
                    showError('No chart data available for backtest');
                    return;
                }}
                
                backtestActive = true;
                backtestPaused = false; // Reset pause state when starting
                backtestIndex = 0;
                btn.textContent = 'Stop Backtest';
                btn.classList.add('active');
                
                // Show pause button
                const pauseBtn = document.getElementById('pauseBtn');
                if (pauseBtn) {{
                    pauseBtn.style.display = 'inline-block';
                    pauseBtn.textContent = 'Pause';
                    pauseBtn.style.background = '#666';
                }}
                
                // Clear existing data and start fresh
                try {{
                    console.log('Starting backtest with', chartData.length, 'bars');
                    console.log('Backtest interval:', backtestIntervalMs, 'ms');
                    console.log('First bar:', chartData[0]);
                    
                    // Reset index
                    backtestIndex = 0;
                    
                    // Clear series
                    candlestickSeries.setData([]);
                    volumeSeries.setData([]);
                    
                    // Small delay to ensure chart is ready, then start
                    setTimeout(() => {{
                        if (chartData.length > 0) {{
                            // Set first bar using setData() to initialize the series
                            const firstBar = chartData[0];
                            const firstBarData = {{
                                time: firstBar.time,
                                open: parseFloat(firstBar.open),
                                high: parseFloat(firstBar.high),
                                low: parseFloat(firstBar.low),
                                close: parseFloat(firstBar.close),
                            }};
                            
                            const firstVolumeData = {{
                                time: firstBar.time,
                                value: parseInt(firstBar.volume) || 0,
                                color: parseFloat(firstBar.close) >= parseFloat(firstBar.open) ? '#26a69a80' : '#ef535080',
                            }};
                            
                            console.log('Setting first bar:', firstBarData);
                            
                            try {{
                            candlestickSeries.setData([firstBarData]);
                            volumeSeries.setData([firstVolumeData]);
                            
                            console.log('First bar set, data count:', candlestickSeries.data().length);
                            
                            // Set visible range to show first ~100 bars (zoomed out for better overview)
                            const timeframeSeconds = getTimeframeSeconds(timeframe);
                            const visibleBars = 100; // Increased from 50 for better zoom out
                            const lastVisibleBar = chartData[Math.min(visibleBars - 1, chartData.length - 1)];
                            chart.timeScale().setVisibleRange({{
                                from: firstBar.time,
                                to: lastVisibleBar.time + (timeframeSeconds * 10) // Add some padding
                            }});
                            console.log('Set visible range to show first', visibleBars, 'bars (zoomed out)');
                                
                                // Verify data was set
                                const verifyData = candlestickSeries.data();
                                console.log('Verified data after setData:', verifyData.length, 'bars');
                                if (verifyData.length > 0) {{
                                    console.log('First bar in series:', verifyData[0]);
                                }}
                                
                                // Update index and status
                                backtestIndex = 1;
                                const date = new Date(firstBar.time * 1000);
                                const intervalMs = baseBacktestIntervalMs / backtestSpeed;
                                updateStatus(`Backtest: 1/${{chartData.length}} | Speed: ${{backtestSpeed}}x | ${{date.toLocaleString()}}`);
                                
                                // Start interval for remaining bars
                                if (chartData.length > 1) {{
                                    backtestInterval = setInterval(() => {{
                                        playBacktestBar();
                                    }}, intervalMs);
                                    console.log('Backtest interval started, next bar in', intervalMs, 'ms (speed:', backtestSpeed + 'x)');
                                }} else {{
                                    // Only one bar, already shown
                                    toggleBacktest();
                                    updateStatus('Backtest complete - 1 bar displayed');
                                }}
                                
                                updateStatus('Backtest started - ' + chartData.length + ' bars to replay');
                            }} catch (setError) {{
                                console.error('Error setting first bar:', setError);
                                showError('Error setting first bar: ' + setError.message);
                                backtestActive = false;
                                btn.textContent = 'Start Backtest';
                                btn.classList.remove('active');
                            }}
                        }} else {{
                            showError('No chart data available');
                        }}
                    }}, 100);
                }} catch (error) {{
                    showError('Error starting backtest: ' + error.message);
                    console.error('Backtest start error:', error);
                    console.error('Error stack:', error.stack);
                    backtestActive = false;
                    btn.textContent = 'Start Backtest';
                    btn.classList.remove('active');
                }}
            }}
        }}
        
        // Wait for DOM and library, ensure container is visible
        function tryInitChart() {{
            const container = document.getElementById('chart-container');
            if (container && (container.clientWidth > 0 || container.offsetWidth > 0)) {{
                initChart();
            }} else {{
                console.log('Container not ready, retrying...');
                setTimeout(tryInitChart, 100);
            }}
        }}
        
        if (document.readyState === 'loading') {{
            document.addEventListener('DOMContentLoaded', function() {{
                setTimeout(tryInitChart, 200);
            }});
        }} else {{
            setTimeout(tryInitChart, 200);
        }}
        
        window.addEventListener('load', function() {{
            if (!chart) {{
                setTimeout(tryInitChart, 200);
            }}
        }});
        
        // Refresh chart
        function refreshChart() {{
            updateStatus('Refresh not available. Re-run chart command to update.');
        }}
        
        // Exit test mode and allow backtest to run
        function exitTestMode() {{
            testModeActive = false;
            console.log('🚪 Test mode deactivated - backtest can now run');
            updateStatus('Test mode exited - you can now start backtest');
        }}
        
        // Update real-time refresh rate from dropdown
        function updateRefreshRate() {{
            const select = document.getElementById('refreshRateSelect');
            if (select) {{
                realtimeRefreshRate = parseInt(select.value);
                console.log('Real-time refresh rate updated to', realtimeRefreshRate, 'updates/sec');
                
                // If real-time is running, restart with new rate
                if (realtimeActive && realtimeInterval) {{
                    clearInterval(realtimeInterval);
                    const intervalMs = 1000 / realtimeRefreshRate;
                    realtimeInterval = setInterval(updateRealtime, intervalMs);
                    updateStatus(`Real-time refresh rate: ${{realtimeRefreshRate}}x/sec`);
                }}
            }}
        }}
        
        // Toggle pause/play for backtest
        function togglePause() {{
            const pauseBtn = document.getElementById('pauseBtn');
            if (!pauseBtn) {{
                console.error('Pause button not found');
                return;
            }}
            
            if (!backtestActive) {{
                console.log('Cannot pause - backtest is not running');
                return;
            }}
            
            if (backtestPaused) {{
                // Resume
                backtestPaused = false;
                pauseBtn.textContent = 'Pause';
                pauseBtn.style.background = '#666';
                updateStatus('Backtest resumed at bar ' + (backtestIndex + 1) + '/' + chartData.length);
                console.log('▶️  Backtest resumed at bar', backtestIndex + 1);
            }} else {{
                // Pause
                backtestPaused = true;
                pauseBtn.textContent = '▶ Play';
                pauseBtn.style.background = '#26a69a';
                updateStatus('Backtest paused at bar ' + backtestIndex + '/' + chartData.length + ' - All candles remain visible');
                console.log('⏸️  Backtest paused at bar', backtestIndex);
            }}
        }}
        
        // Update backtest speed from dropdown
        function updateBacktestSpeed() {{
            const select = document.getElementById('speedSelect');
            if (select) {{
                backtestSpeed = parseInt(select.value);
                console.log('Backtest speed updated to', backtestSpeed + 'x');
                
                // If backtest is running, restart with new speed
                if (backtestActive) {{
                    clearInterval(backtestInterval);
                    const intervalMs = baseBacktestIntervalMs / backtestSpeed;
                    backtestInterval = setInterval(() => {{
                        playBacktestBar();
                    }}, intervalMs);
                    console.log('Backtest interval updated to', intervalMs, 'ms');
                    updateStatus('Backtest speed: ' + backtestSpeed + 'x');
                }}
            }}
        }}
        
        // Test function: Load all bars at once to verify chart works
        function loadAllBars() {{
            if (!chart || !candlestickSeries || !volumeSeries) {{
                showError('Chart not initialized');
                return;
            }}
            
            // Set test mode flag to prevent backtest from interfering
            testModeActive = true;
            console.log('🧪 Test mode activated');
            
            try {{
                console.log('Loading all', chartData.length, 'bars at once (test)...');
                
                // Sort data by time to ensure chronological order
                const sortedChartData = [...chartData].sort((a, b) => a.time - b.time);
                
                const candlestickData = sortedChartData.map(bar => ({{
                    time: Number(bar.time), // Ensure it's a number (TradingView expects Unix timestamp in seconds)
                    open: parseFloat(bar.open),
                    high: parseFloat(bar.high),
                    low: parseFloat(bar.low),
                    close: parseFloat(bar.close),
                }}));
                
                const volumeData = sortedChartData.map(bar => ({{
                    time: Number(bar.time), // Ensure it's a number
                    value: parseInt(bar.volume) || 0,
                    color: parseFloat(bar.close) >= parseFloat(bar.open) ? '#26a69a80' : '#ef535080',
                }}));
                
                // Verify time format - must be numbers, not strings
                const invalidTimes = candlestickData.filter(bar => typeof bar.time !== 'number' || isNaN(bar.time));
                if (invalidTimes.length > 0) {{
                    console.error('Invalid time format found:', invalidTimes.slice(0, 3));
                }}
                
                console.log('First 3 bars sample:', candlestickData.slice(0, 3));
                console.log('Last 3 bars sample:', candlestickData.slice(-3));
                console.log('Time range:', candlestickData[0].time, 'to', candlestickData[candlestickData.length - 1].time);
                console.log('All times are numbers:', candlestickData.every(bar => typeof bar.time === 'number' && !isNaN(bar.time)));
                
                // Log price range for debugging
                const minPrice = Math.min(...candlestickData.map(b => b.low));
                const maxPrice = Math.max(...candlestickData.map(b => b.high));
                console.log('Price range:', minPrice, 'to', maxPrice);
                
                candlestickSeries.setData(candlestickData);
                volumeSeries.setData(volumeData);
                
                console.log('Data set. Series data count:', candlestickSeries.data().length);
                
                // Force price axis to auto-scale
                candlestickSeries.priceScale().applyOptions({{
                    autoScale: true,
                }});
                volumeSeries.priceScale().applyOptions({{
                    autoScale: true,
                }});
                
                // Force resize and fit
                const container = document.getElementById('chart-container');
                if (container) {{
                    const width = container.clientWidth || container.offsetWidth || 1200;
                    chart.applyOptions({{ width: width, height: 600 }});
                    console.log('Chart resized to:', width, 'x 600');
                }}
                
                // Verify data before fitting
                const verifyCount = candlestickSeries.data().length;
                const firstBar = candlestickSeries.data()[0];
                const lastBar = candlestickSeries.data()[verifyCount - 1];
                console.log('All bars loaded. Series has', verifyCount, 'bars');
                console.log('First bar:', firstBar);
                console.log('Last bar:', lastBar);
                
                // Explicitly set visible range to show all data
                // The issue: fitContent() seems to fit to wrong range, so we MUST use setVisibleRange
                if (firstBar && lastBar) {{
                    const firstTime = Number(firstBar.time);
                    const lastTime = Number(lastBar.time);
                    
                    console.log('Attempting to set visible range from', firstTime, 'to', lastTime);
                    
                    // Use requestAnimationFrame to ensure chart has processed setData() first
                    requestAnimationFrame(() => {{
                        try {{
                            chart.timeScale().setVisibleRange({{
                                from: firstTime,
                                to: lastTime
                            }});
                            console.log('✅ setVisibleRange called');
                            
                            // Verify after a frame
                            requestAnimationFrame(() => {{
                                const range = chart.timeScale().getVisibleRange();
                                console.log('Visible range after setVisibleRange:', range);
                                if (range && (range.from > firstTime || range.to < lastTime)) {{
                                    console.error('❌ Range still wrong! Trying scrollToRealTime...');
                                    // Last resort: try scrolling to the first bar
                                    chart.timeScale().scrollToRealTime();
                                    // Then set range again
                                    setTimeout(() => {{
                                        chart.timeScale().setVisibleRange({{ from: firstTime, to: lastTime }});
                                    }}, 100);
                                }} else if (range) {{
                                    console.log('✅ Range is correct!');
                                    
                                    // Force chart to redraw by applying options
                                    chart.applyOptions({{
                                        timeScale: {{
                                            visible: true,
                                        }},
                                    }});
                                    
                                    // Force price scale to autoscale
                                    candlestickSeries.priceScale().applyOptions({{
                                        autoScale: true,
                                    }});
                                    
                                    console.log('Forced chart redraw and autoscale');
                                }}
                            }});
                        }} catch (e) {{
                            console.error('Error in setVisibleRange:', e);
                        }}
                    }});
                }}
                
                updateStatus('All ' + verifyCount + ' bars loaded (test mode)');
                console.log('✅ All bars loaded successfully in test mode');
            }} catch (error) {{
                console.error('Error loading all bars:', error);
                console.error('Error stack:', error.stack);
                showError('Error: ' + error.message);
            }} finally {{
                // Keep test mode active so backtest doesn't interfere
                console.log('🧪 Test mode remains active (bars should be visible)');
            }}
        }}
        
        // Place order from chart
        async function placeOrder(side) {{
            if (!serverPort) {{
                showError('Trading server not available');
                return;
            }}
            
            try {{
                const orderType = document.getElementById('orderTypeSelect').value;
                const quantity = parseInt(document.getElementById('quantityInput').value) || 1;
                const limitPrice = parseFloat(document.getElementById('limitPriceInput').value) || null;
                const stopPrice = parseFloat(document.getElementById('stopPriceInput').value) || null;
                const enableBracket = document.getElementById('enableBracketCheck').checked;
                const stopLossPrice = parseFloat(document.getElementById('stopLossPriceInput').value) || null;
                const takeProfitPrice = parseFloat(document.getElementById('takeProfitPriceInput').value) || null;
                
                // Validate required fields
                if (orderType === 'limit' && !limitPrice) {{
                    showError('Limit price required for limit orders');
                    return;
                }}
                if (orderType === 'stop' && !stopPrice) {{
                    showError('Stop price required for stop orders');
                    return;
                }}
                // Validate bracket prices - check both enableBracket checkbox and orderType === 'bracket'
                if ((enableBracket || orderType === 'bracket') && (!stopLossPrice || !takeProfitPrice)) {{
                    showError('Stop loss and take profit prices required for bracket orders');
                    return;
                }}
                
                const symbolSelect = document.getElementById('symbolSelect');
                const orderSymbol = symbolSelect?.value || symbol;
                
                if (!orderSymbol || orderSymbol.trim() === '' || orderSymbol === 'UNKNOWN') {{
                    showError('Please select a symbol first');
                    return;
                }}
                
                updateStatus(`Placing ${{side}} ${{orderType}} order...`);
                
                const response = await fetch(`http://127.0.0.1:${{serverPort}}/api/chart/order`, {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{
                        symbol: orderSymbol,
                        side: side,
                        quantity: quantity,
                        order_type: orderType,
                        limit_price: limitPrice,
                        stop_price: stopPrice,
                        stop_loss_price: stopLossPrice,
                        take_profit_price: takeProfitPrice,
                        enable_bracket: enableBracket
                    }})
                }});
                
                const result = await response.json();
                
                if (result.error) {{
                    showError(`Order failed: ${{result.error}}`);
                }} else {{
                    const orderId = result.get?.('order_id') || result.order_id || result.id || 'N/A';
                    const orderTypeDisplay = orderType === 'bracket' ? 'Bracket' : orderType.charAt(0).toUpperCase() + orderType.slice(1);
                    updateStatus(`✅ Order placed successfully!`);
                    showToast('Order Placed', `${{side}} ${{orderTypeDisplay}} order placed successfully${{orderId !== 'N/A' ? ' (ID: ' + orderId + ')' : ''}}`, 'success');
                    // Update position lines and order lines (force update, ignore throttle)
                    lastPositionUpdate = 0; // Reset throttle to force update
                    lastOrderUpdate = 0; // Reset throttle to force update
                    await updatePositionLines();
                    await updateOrderLines();
                }}
            }} catch (error) {{
                showError(`Order error: ${{error.message}}`);
                console.error('Order placement error:', error);
            }}
        }}
        
        // Pre-fill prices with last traded price
        async function prefillPrices() {{
            if (!serverPort) return;
            
            try {{
                const currentSymbol = document.getElementById('symbolSelect')?.value || symbol;
                const response = await fetch(`http://127.0.0.1:${{serverPort}}/api/chart/quote?symbol=${{currentSymbol}}`);
                if (!response.ok) return;
                
                const data = await response.json();
                const quote = data.quote;
                if (!quote) return;
                
                const lastPrice = parseFloat(quote.last || quote.lastPrice || quote.bid || quote.ask || 0);
                if (!lastPrice || lastPrice === 0) return;
                
                // Pre-fill all price inputs with last traded price
                const limitInput = document.getElementById('limitPriceInput');
                const stopInput = document.getElementById('stopPriceInput');
                const stopLossInput = document.getElementById('stopLossPriceInput');
                const takeProfitInput = document.getElementById('takeProfitPriceInput');
                
                // Only pre-fill if field is visible and empty
                if (limitInput && limitInput.style.display !== 'none' && !limitInput.value) {{
                    limitInput.value = lastPrice.toFixed(2);
                }}
                if (stopInput && stopInput.style.display !== 'none' && !stopInput.value) {{
                    stopInput.value = lastPrice.toFixed(2);
                }}
                if (stopLossInput && stopLossInput.style.display !== 'none' && !stopLossInput.value) {{
                    stopLossInput.value = lastPrice.toFixed(2);
                }}
                if (takeProfitInput && takeProfitInput.style.display !== 'none' && !takeProfitInput.value) {{
                    takeProfitInput.value = lastPrice.toFixed(2);
                }}
            }} catch (error) {{
                console.debug('Failed to pre-fill prices:', error);
            }}
        }}
        
        // Update order type UI visibility
        function updateOrderType() {{
            const orderType = document.getElementById('orderTypeSelect').value;
            const limitInput = document.getElementById('limitPriceInput');
            const stopInput = document.getElementById('stopPriceInput');
            const bracketCheck = document.getElementById('enableBracketCheck');
            
            if (orderType === 'limit') {{
                limitInput.style.display = 'inline-block';
                stopInput.style.display = 'none';
                bracketCheck.disabled = false;
            }} else if (orderType === 'stop') {{
                limitInput.style.display = 'none';
                stopInput.style.display = 'inline-block';
                bracketCheck.disabled = false;
            }} else if (orderType === 'bracket') {{
                limitInput.style.display = 'none';
                stopInput.style.display = 'none';
                bracketCheck.checked = true;
                bracketCheck.disabled = true; // Bracket type always has brackets enabled
            }} else {{
                limitInput.style.display = 'none';
                stopInput.style.display = 'none';
                bracketCheck.disabled = false;
            }}
            updateBracket(); // Also update bracket visibility
            prefillPrices(); // Pre-fill prices when order type changes
        }}
        
        // Update bracket UI visibility
        function updateBracket() {{
            const enableBracket = document.getElementById('enableBracketCheck').checked;
            const slInput = document.getElementById('stopLossPriceInput');
            const tpInput = document.getElementById('takeProfitPriceInput');
            
            if (enableBracket) {{
                slInput.style.display = 'inline-block';
                tpInput.style.display = 'inline-block';
            }} else {{
                slInput.style.display = 'none';
                tpInput.style.display = 'none';
            }}
            prefillPrices(); // Pre-fill prices when bracket visibility changes
        }}
        
        // Load available contracts and populate symbol dropdown
        async function loadContracts() {{
            if (!serverPort) {{
                console.log('No server port, skipping contract load');
                return;
            }}
            
            try {{
                console.log('Loading contracts from server...');
                const response = await fetch(`http://127.0.0.1:${{serverPort}}/api/chart/contracts`);
                if (!response.ok) {{
                    console.error('Failed to fetch contracts:', response.status);
                    return;
                }}
                const data = await response.json();
                console.log('Contracts response:', data);
                const symbols = data.symbols || [];
                
                if (symbols.length === 0) {{
                    console.warn('No symbols returned from contracts endpoint');
                    return;
                }}
                
                const select = document.getElementById('symbolSelect');
                if (!select) {{
                    console.error('Symbol select element not found');
                    return;
                }}
                
                const currentValue = select.value || '{symbol}';
                select.innerHTML = '';
                
                symbols.forEach(sym => {{
                    const option = document.createElement('option');
                    option.value = sym;
                    option.textContent = sym;
                    if (sym === currentValue || (currentValue === '{symbol}' && sym === '{symbol}')) {{
                        option.selected = true;
                    }}
                    select.appendChild(option);
                }});
                
                // If no symbol was selected and we have symbols, select the first one or the default
                if (select.selectedIndex === -1 && symbols.length > 0) {{
                    const defaultSymbol = '{symbol}';
                    const foundDefault = symbols.find(s => s === defaultSymbol);
                    if (foundDefault) {{
                        select.value = foundDefault;
                    }} else {{
                        select.selectedIndex = 0;
                    }}
                }}
                
                console.log('Contracts loaded:', symbols.length, 'symbols');
                // Pre-fill prices after contracts are loaded
                prefillPrices();
            }} catch (error) {{
                console.error('Error loading contracts:', error);
                updateStatus('Failed to load contracts: ' + error.message);
            }}
        }}
        
        // Pre-fill prices when symbol changes
        if (document.readyState === 'loading') {{
            document.addEventListener('DOMContentLoaded', function() {{
                const symbolSelect = document.getElementById('symbolSelect');
                if (symbolSelect) {{
                    symbolSelect.addEventListener('change', prefillPrices);
                }}
                // Initial pre-fill after a short delay
                setTimeout(prefillPrices, 1000);
            }});
        }} else {{
            // DOM already loaded
            const symbolSelect = document.getElementById('symbolSelect');
            if (symbolSelect) {{
                symbolSelect.addEventListener('change', prefillPrices);
            }}
            setTimeout(prefillPrices, 1000);
        }}
        
        // Reload chart data for new symbol/timeframe
        async function reloadChartData() {{
            if (!serverPort || !chart || !candlestickSeries) return;
            
            const symbolSelect = document.getElementById('symbolSelect');
            const newSymbol = symbolSelect?.value || '{symbol}';
            const newTimeframe = document.getElementById('timeframeSelect').value || '{timeframe}';
            
            if (!newSymbol || newSymbol.trim() === '') {{
                showError('Please select a symbol first');
                return;
            }}
            
            updateStatus(`Loading ${{newSymbol}} ${{newTimeframe}}...`);
            
            try {{
                const response = await fetch(`http://127.0.0.1:${{serverPort}}/api/chart/reload?symbol=${{encodeURIComponent(newSymbol)}}&timeframe=${{encodeURIComponent(newTimeframe)}}&limit=100`);
                const data = await response.json();
                
                if (data.error) {{
                    showError(`Failed to reload: ${{data.error}}`);
                    return;
                }}
                
                if (!data.bars || data.bars.length === 0) {{
                    showError('No data available for this symbol/timeframe');
                    return;
                }}
                
                // Update chart data
                const chartBars = data.bars.map(bar => ({{
                    time: bar.time,
                    open: bar.open,
                    high: bar.high,
                    low: bar.low,
                    close: bar.close
                }}));
                
                candlestickSeries.setData(chartBars);
                
                // Update volume if available
                if (volumeSeries && data.bars[0].volume !== undefined) {{
                    const volumeBars = data.bars.map(bar => ({{
                        time: bar.time,
                        value: bar.volume,
                        color: bar.close >= bar.open ? '#26a69a80' : '#ef535080'
                    }}));
                    volumeSeries.setData(volumeBars);
                }}
                
                // Update chart title
                document.querySelector('.header h1').textContent = `${{newSymbol}} ${{newTimeframe}} Chart`;
                
                // Fit content to show all bars
                chart.timeScale().fitContent();
                
                // Update position and order lines after chart is initialized
                setTimeout(async () => {{
                    console.log('Initializing position/order lines after chart load...');
                    lastPositionUpdate = 0; // Reset throttle to force update
                    lastOrderUpdate = 0; // Reset throttle to force update
                    await updatePositionLines();
                    await updateOrderLines();
                    
                    // Note: Position/order lines are event-based only (refresh on order placement/fill)
                }}, 1000);
                
                updateStatus(`✅ Chart updated: ${{data.bars.length}} bars`);
            }} catch (error) {{
                showError(`Error reloading chart: ${{error.message}}`);
                console.error('Reload error:', error);
            }}
        }}
        
        // Update symbol (reload chart data)
        function updateSymbol() {{
            reloadChartData();
        }}
        
        // Update timeframe (reload chart data)
        function updateTimeframe() {{
            reloadChartData();
        }}
        
        // Update position lines on chart (using price lines like TradingChart.tsx)
        async function updatePositionLines() {{
            if (!serverPort || !chart || !candlestickSeries) {{
                console.log('updatePositionLines: Missing requirements', {{
                    serverPort: !!serverPort,
                    chart: !!chart,
                    candlestickSeries: !!candlestickSeries
                }});
                return;
            }}
            
            // Throttle updates to prevent rate limiting
            const now = Date.now();
            if (now - lastPositionUpdate < POSITION_UPDATE_INTERVAL) {{
                console.debug('updatePositionLines: Throttled');
                return; // Skip if called too soon
            }}
            lastPositionUpdate = now;
            
            try {{
                // Remove existing position price lines
                positionPriceLines.forEach(line => {{
                    try {{
                        candlestickSeries.removePriceLine(line);
                    }} catch (e) {{
                        console.debug('Error removing position price line:', e);
                    }}
                }});
                positionPriceLines = [];
                
                console.log('Fetching positions from server...');
                const response = await fetch(`http://127.0.0.1:${{serverPort}}/api/chart/positions`);
                if (!response.ok) {{
                    console.error('Failed to fetch positions:', response.status, response.statusText);
                    return;
                }}
                const data = await response.json();
                const positions = data.positions || [];
                console.log('📋 Raw positions from API:', positions.length, positions);
                
                // Find position for current symbol (exact match like TradingChart.tsx)
                const currentSymbol = document.getElementById('symbolSelect')?.value || symbol;
                console.log('🔍 Looking for position with symbol:', currentSymbol);
                console.log('📊 Available positions:', positions.map(p => ({{
                    id: p.id,
                    symbol: p.symbol,
                    side: p.side,
                    quantity: p.quantity,
                    entry_price: p.entry_price,
                    contractId: p.contractId
                }})));
                
                // Simple exact match like TradingChart.tsx: pos.symbol === symbol
                const position = positions.find(p => {{
                    const posSymbol = p.symbol;
                    const matchesSymbol = posSymbol && posSymbol.toUpperCase() === currentSymbol.toUpperCase();
                    const hasQuantity = (p.quantity || p.size || 0) !== 0;
                    
                    if (matchesSymbol && hasQuantity) {{
                        console.log('Found matching position:', p);
                        return true;
                    }}
                    return false;
                }});
                
                if (!position) {{
                    console.log('No position found for symbol:', currentSymbol, 'Available positions:', positions.map(p => ({{
                        symbol: p.symbol,
                        contractId: p.contractId,
                        contract_id: p.contract_id,
                        quantity: p.quantity,
                        size: p.size
                    }})));
                    return;
                }}
                
                console.log('Processing position:', position);
                
                // Extract values exactly like TradingChart.tsx
                const entryPrice = Number(position.entry_price || position.entryPrice || 0);
                if (!entryPrice || !isFinite(entryPrice)) {{
                    console.warn('Entry price is invalid:', entryPrice, 'Position:', position);
                    return;
                }}
                
                const isLong = position.side === 'LONG';
                const quantity = Number(position.quantity ?? position.size ?? 0);
                
                console.log('Position values:', {{
                    entryPrice,
                    quantity,
                    isLong,
                    side: position.side
                }});
                
                // Extract stop loss and take profit if available
                const stopLoss = position.stop_loss || position.stopLoss;
                const takeProfit = position.take_profit || position.takeProfit;
                
                // Add entry price line (exactly like TradingChart.tsx)
                try {{
                    console.log('Creating entry price line at:', entryPrice);
                    const entryLine = candlestickSeries.createPriceLine({{
                        price: entryPrice,
                        color: isLong ? '#26A69A' : '#EF5350',
                        lineWidth: 2,
                        lineStyle: LightweightCharts.LineStyle.Solid,
                        axisLabelVisible: true,
                        title: `${{position.side}} ${{quantity}}@${{entryPrice.toFixed(2)}}`
                    }});
                    positionPriceLines.push(entryLine);
                    console.log('✅ Entry price line created successfully');
                }} catch (error) {{
                    console.error('❌ Error creating entry price line:', error);
                }}
                
                // Add stop loss price line if available
                if (stopLoss && Number(stopLoss) > 0) {{
                    try {{
                        const stopLine = candlestickSeries.createPriceLine({{
                            price: Number(stopLoss),
                            color: '#EF5350',
                            lineWidth: 2,
                            lineStyle: LightweightCharts.LineStyle.Dashed,
                            axisLabelVisible: true,
                            title: `Stop Loss: ${{Number(stopLoss).toFixed(2)}}`
                        }});
                        positionPriceLines.push(stopLine);
                        console.log('✅ Stop loss line created at:', stopLoss);
                    }} catch (error) {{
                        console.error('❌ Error creating stop loss price line:', error);
                    }}
                }}
                
                // Add take profit price line if available
                if (takeProfit && Number(takeProfit) > 0) {{
                    try {{
                        const tpLine = candlestickSeries.createPriceLine({{
                            price: Number(takeProfit),
                            color: '#26A69A',
                            lineWidth: 2,
                            lineStyle: LightweightCharts.LineStyle.Dashed,
                            axisLabelVisible: true,
                            title: `Take Profit: ${{Number(takeProfit).toFixed(2)}}`
                        }});
                        positionPriceLines.push(tpLine);
                        console.log('✅ Take profit line created at:', takeProfit);
                    }} catch (error) {{
                        console.error('❌ Error creating take profit price line:', error);
                    }}
                }}
            }} catch (error) {{
                console.error('Error updating position lines:', error);
            }}
        }}
        
        // Update order lines on chart
        async function updateOrderLines() {{
            if (!serverPort || !chart || !candlestickSeries) return;
            
            // Throttle updates to prevent rate limiting
            const now = Date.now();
            if (now - lastOrderUpdate < ORDER_UPDATE_INTERVAL) {{
                return; // Skip if called too soon
            }}
            lastOrderUpdate = now;
            
            try {{
                // Remove existing order price lines
                orderPriceLines.forEach(line => {{
                    try {{
                        candlestickSeries.removePriceLine(line);
                    }} catch (e) {{
                        console.debug('Error removing order price line:', e);
                    }}
                }});
                orderPriceLines = [];
                
                // Fetch open orders
                const response = await fetch(`http://127.0.0.1:${{serverPort}}/api/chart/orders`);
                if (!response.ok) {{
                    console.debug('Failed to fetch orders:', response.status);
                    return;
                }}
                
                const data = await response.json();
                const orders = data.orders || [];
                
                console.log('📋 Raw orders from API:', orders.length, orders);
                
                // Find orders for current symbol (exactly like TradingChart.tsx)
                const currentSymbol = document.getElementById('symbolSelect')?.value || symbol;
                console.log('🔍 Filtering orders for symbol:', currentSymbol);
                
                const relevantOrders = orders.filter(o => {{
                    // Extract symbol from multiple possible fields
                    const orderSymbol = o.symbol || o.contractId?.split('.')?.[2] || o.symbolId?.split('.')?.[2] || '';
                    const matchesSymbol = orderSymbol && orderSymbol.toUpperCase() === currentSymbol.toUpperCase();
                    
                    // Accept PENDING, OPEN, or status 1 (all mean open/pending)
                    // Also accept numeric status 1 or string '1'
                    const status = o.status;
                    const isPending = status === 'PENDING' || 
                                    status === 'OPEN' || 
                                    status === 'Open' || 
                                    status === 1 || 
                                    status === '1' ||
                                    String(status).toUpperCase() === 'OPEN' ||
                                    String(status).toUpperCase() === 'PENDING';
                    
                    // Check for any price field (price, stop_price, limitPrice, stopPrice)
                    // Also check for null/undefined explicitly
                    const price = o.price;
                    const stop_price = o.stop_price;
                    const limitPrice = o.limitPrice || o.limit_price;
                    const stopPrice = o.stopPrice;
                    const hasPrice = (price != null && price !== 0 && price !== '0') || 
                                   (stop_price != null && stop_price !== 0 && stop_price !== '0') || 
                                   (limitPrice != null && limitPrice !== 0 && limitPrice !== '0') || 
                                   (stopPrice != null && stopPrice !== 0 && stopPrice !== '0');
                    
                    const shouldInclude = matchesSymbol && isPending && hasPrice;
                    
                    if (!shouldInclude) {{
                        console.warn('❌ Filtered out order:', {{
                            id: o.id,
                            symbol: orderSymbol,
                            currentSymbol: currentSymbol,
                            matchesSymbol,
                            status: status,
                            isPending,
                            price: price,
                            stop_price: stop_price,
                            limitPrice: limitPrice,
                            stopPrice: stopPrice,
                            hasPrice,
                            fullOrder: o
                        }});
                    }} else {{
                        console.log('✅ Including order:', {{
                            id: o.id,
                            symbol: orderSymbol,
                            side: o.side,
                            type: o.type,
                            price: price || stop_price || limitPrice || stopPrice
                        }});
                    }}
                    return shouldInclude;
                }});
                
                console.log('📊 Relevant orders for', currentSymbol, ':', relevantOrders.length, 'out of', orders.length);
                console.log('📋 Relevant orders details:', relevantOrders.map(o => ({{
                    id: o.id,
                    symbol: o.symbol,
                    side: o.side,
                    type: o.type,
                    status: o.status,
                    price: o.price,
                    stop_price: o.stop_price,
                    limitPrice: o.limitPrice,
                    stopPrice: o.stopPrice
                }})));
                
                // Add price lines for each order (exactly like TradingChart.tsx)
                relevantOrders.forEach(order => {{
                    // Use stopPrice/stop_price for STOP orders, price/limitPrice for LIMIT orders
                    const orderTypeNum = order.type;
                    let orderPrice = null;
                    
                    console.log('🔍 Processing order:', {{
                        id: order.id,
                        type: orderTypeNum,
                        price: order.price,
                        stop_price: order.stop_price,
                        limitPrice: order.limitPrice,
                        stopPrice: order.stopPrice
                    }});
                    
                    if (orderTypeNum === 4 || orderTypeNum === 'STOP' || order.type === 'STOP') {{
                        // STOP order - use stopPrice/stop_price first
                        orderPrice = order.stopPrice || order.stop_price || order.price || order.limitPrice || order.limit_price;
                    }} else {{
                        // LIMIT or other order - use price/limitPrice first
                        orderPrice = order.price || order.limitPrice || order.limit_price || order.stopPrice || order.stop_price;
                    }}
                    
                    // Convert to number and validate
                    const priceNum = Number(orderPrice);
                    if (!orderPrice || priceNum === 0 || !isFinite(priceNum) || isNaN(priceNum)) {{
                        console.warn('❌ Skipping order with no valid price:', {{
                            id: order.id,
                            orderPrice: orderPrice,
                            priceNum: priceNum,
                            order: order
                        }});
                        return;
                    }}
                    
                    console.log('✅ Order has valid price:', {{
                        id: order.id,
                        orderPrice: priceNum,
                        type: orderTypeNum
                    }});
                    
                    const isLongOrder = order.side === 'BUY';
                    // Convert numeric order type to string (1=LIMIT, 4=STOP, etc.)
                    let orderType = order.type;
                    if (typeof orderType === 'number') {{
                        orderType = orderType == 1 ? 'LIMIT' : orderType == 4 ? 'STOP' : 'ORDER';
                    }} else {{
                        orderType = orderType || 'LIMIT';
                    }}
                    const isStopOrder = orderType === 'STOP' || order.stop_price || order.stopPrice;
                    
                    try {{
                        // Use the validated numeric price
                        const finalPrice = priceNum;
                        console.log('🎨 Creating order price line at:', finalPrice, 'Type:', orderType);
                        
                        // Ensure quantity is a number and format label correctly
                        const qty = Number(order.quantity || order.size || 1);
                        // orderType is already converted to string above, so just use it directly
                        const label = `${{order.side}} ${{qty}} ${{orderType}}`;
                        console.log('📝 Order label:', label, 'qty:', qty, 'orderType:', orderType, 'price:', finalPrice);
                        
                        const priceLine = candlestickSeries.createPriceLine({{
                            price: finalPrice,
                            color: isLongOrder ? '#10B981' : '#F59E0B',
                            lineWidth: 2,
                            lineStyle: isStopOrder ? LightweightCharts.LineStyle.Dotted : LightweightCharts.LineStyle.Dashed,
                            axisLabelVisible: true,
                            title: label
                        }});
                        orderPriceLines.push(priceLine);
                        console.log('✅ Order price line created successfully:', {{
                            id: order.id,
                            price: finalPrice,
                            label: label,
                            side: order.side,
                            type: orderType
                        }});
                    }} catch (error) {{
                        console.error('❌ Error creating order price line:', error, 'Order:', order);
                    }}
                }});
            }} catch (error) {{
                console.error('Error updating order lines:', error);
            }}
        }}
        
        // Export data to CSV
        function exportData() {{
            try {{
                const csv = [
                    ['Time', 'Open', 'High', 'Low', 'Close', 'Volume'].join(','),
                    ...chartData.map(bar => {{
                        const date = new Date(bar.time * 1000);
                        return [
                            date.toISOString(),
                            bar.open,
                            bar.high,
                            bar.low,
                            bar.close,
                            bar.volume
                        ].join(',');
                    }})
                ].join('\\n');
                
                const blob = new Blob([csv], {{ type: 'text/csv' }});
                const url = window.URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = '{symbol}_{timeframe}_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv';
                a.click();
                window.URL.revokeObjectURL(url);
                updateStatus('CSV exported');
            }} catch (error) {{
                showError('Export error: ' + error.message);
            }}
        }}
        
        // Handle window resize
        window.addEventListener('resize', () => {{
            if (chart && chartContainer) {{
                chart.applyOptions({{ width: chartContainer.clientWidth }});
            }} else if (chart) {{
                // Fallback: get container again if not in scope
                const container = document.getElementById('chart-container');
                if (container) {{
                    chart.applyOptions({{ width: container.clientWidth }});
                }}
            }}
        }});
    </script>
</body>
</html>"""
    
    # Determine output path
    if output_path is None:
        charts_dir = Path("charts")
        charts_dir.mkdir(exist_ok=True)
        output_path = str(charts_dir / f"{symbol}_{timeframe}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html")
    
    # Write HTML file
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html_content)
    
    return output_path


async def open_chart_html_async(
    trading_bot, 
    symbol: str = 'MNQ', 
    timeframe: str = '5m', 
    limit: int = 100,
    realtime: bool = False,
    backtest: bool = False,
    backtest_start: Optional[str] = None,
    backtest_end: Optional[str] = None,
    backtest_speed: float = 1.0
):
    """
    Generate and open TradingView Lightweight Charts HTML file (async version).
    
    Args:
        trading_bot: TopStepXTradingBot instance
        symbol: Trading symbol
        timeframe: Timeframe
        limit: Number of bars
        realtime: Enable real-time updates (polls for new quotes)
        backtest: Enable backtesting mode (replays historical bars)
        backtest_start: Start date for backtesting (YYYY-MM-DD or YYYY-MM-DD HH:MM:SS)
        backtest_end: End date for backtesting (YYYY-MM-DD or YYYY-MM-DD HH:MM:SS)
        backtest_speed: Playback speed multiplier (1.0 = real-time, 2.0 = 2x speed, etc.)
    
    Returns:
        Path to generated HTML file
    """
    # For backtesting, fetch all bars in the date range
    if backtest and backtest_start and backtest_end:
        from datetime import datetime
        try:
            start_dt = datetime.fromisoformat(backtest_start.replace('Z', '+00:00'))
            end_dt = datetime.fromisoformat(backtest_end.replace('Z', '+00:00'))
        except:
            try:
                start_dt = datetime.strptime(backtest_start, '%Y-%m-%d')
                end_dt = datetime.strptime(backtest_end, '%Y-%m-%d')
            except:
                raise ValueError(f"Invalid date format. Use YYYY-MM-DD or YYYY-MM-DD HH:MM:SS")
        
        bars = await trading_bot.get_historical_data(
            symbol=symbol,
            timeframe=timeframe,
            start_time=start_dt,
            end_time=end_dt
        )
    else:
        # Fetch historical data (already in async context)
        bars = await trading_bot.get_historical_data(
            symbol=symbol,
            timeframe=timeframe,
            limit=limit
        )
    
    if not bars:
        raise ValueError(f"No data available for {symbol} {timeframe}")
    
    # Convert to dict format if needed
    if bars and hasattr(bars[0], 'timestamp'):
        from datetime import timezone as tz
        bars = [
            {
                # Convert timestamp to Unix seconds directly to avoid timezone issues
                # If timestamp is naive, assume UTC
                'timestamp': int(bar.timestamp.replace(tzinfo=tz.utc).timestamp()) if bar.timestamp.tzinfo is None else int(bar.timestamp.timestamp()),
                'open': bar.open,
                'high': bar.high,
                'low': bar.low,
                'close': bar.close,
                'volume': bar.volume,
            }
            for bar in bars
        ]
    
    if not bars:
        raise ValueError(f"No data available for {symbol} {timeframe}")
    
    # Get trading bot reference for real-time updates (store in a way the HTML can access)
    # We'll create a simple HTTP server endpoint if realtime mode is enabled
    server_port = None
    if realtime:
        # Start a simple HTTP server for real-time updates
        server_port = await _start_chart_server(trading_bot, symbol)
    
    # Generate HTML
    html_path = generate_chart_html(
        symbol, 
        timeframe, 
        bars, 
        realtime=realtime,
        backtest=backtest,
        backtest_speed=backtest_speed,
        server_port=server_port
    )
    
    # Open in browser
    import webbrowser
    import os
    file_url = f"file://{os.path.abspath(html_path)}"
    webbrowser.open(file_url)
    
    return html_path


def open_chart_html(trading_bot, symbol: str = 'MNQ', timeframe: str = '5m', limit: int = 100):
    """
    Synchronous wrapper for open_chart_html_async.
    For use in non-async contexts (creates new event loop).
    
    Args:
        trading_bot: TopStepXTradingBot instance
        symbol: Trading symbol
        timeframe: Timeframe
        limit: Number of bars
    
    Returns:
        Path to generated HTML file
    """
    import asyncio
    
    # Check if we're in an async context
    try:
        loop = asyncio.get_running_loop()
        # If we get here, we're in an async context - use create_task
        # But this is a sync function, so we need to handle it differently
        raise RuntimeError("Use open_chart_html_async() in async contexts")
    except RuntimeError:
        # No running loop - create new one
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(
                open_chart_html_async(trading_bot, symbol, timeframe, limit)
            )
        finally:
            loop.close()
