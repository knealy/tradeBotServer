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
from aiohttp import web, WSMsgType
import logging

logger = logging.getLogger(__name__)

# Global server instance for real-time charts
_chart_server = None
_chart_server_port = None
_chart_server_trading_bot = None
_chart_server_symbol = None
_chart_server_timeframe = None


async def _start_chart_server(trading_bot, symbol: str, timeframe: str = '5m') -> int:
    """Start a simple HTTP server for real-time chart updates."""
    global _chart_server, _chart_server_port, _chart_server_trading_bot, _chart_server_symbol, _chart_server_timeframe
    
    if _chart_server is not None:
        # Server already running
        _chart_server_trading_bot = trading_bot
        _chart_server_symbol = symbol
        _chart_server_timeframe = timeframe
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
            
            # Check cache first (but reduce TTL for PnL updates)
            cache_key = f"account_state_{account_id}"
            import time
            current_time = time.time() * 1000  # milliseconds
            
            # Reduce cache TTL to 1 second for real-time PnL updates
            cache_ttl = 1000  # 1 second instead of default
            
            if cache_key in _account_state_cache:
                cache_age = current_time - _account_state_cache_time.get(cache_key, 0)
                if cache_age < cache_ttl:
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
            
            # Get P&L from positions (aggregate from all positions as per accountInfo.md)
            unrealized_pnl = 0.0
            realized_pnl = 0.0
            
            # First, try to get PnL from account tracker (most accurate)
            try:
                if hasattr(trading_bot, 'account_tracker') and trading_bot.account_tracker:
                    if account_id:
                        # Use get_state() which returns both realized and unrealized PnL
                        account_state = trading_bot.account_tracker.get_state(account_id=str(account_id))
                        if account_state:
                            # get_state() returns 'realized_pnl' and 'unrealized_pnl' (American spelling)
                            realized_pnl = float(account_state.get('realized_pnl', 0.0) or 0.0)
                            unrealized_pnl = float(account_state.get('unrealized_pnl', 0.0) or 0.0)
                            logger.debug(f"Got PnL from account tracker: realized={realized_pnl}, unrealized={unrealized_pnl}")
            except Exception as e:
                logger.debug(f"Could not get PnL from account tracker: {e}")
            
            # If account tracker doesn't have PnL, aggregate from positions
            if unrealized_pnl == 0.0:
                try:
                    positions = await trading_bot.get_open_positions(account_id=account_id)
                    if positions:
                        for pos in positions:
                            # Try multiple field names for unrealized PnL
                            pos_unrealized = pos.get('unrealizedPnL') or pos.get('unrealized_pnl') or pos.get('unrealizedPnl') or 0
                            if pos_unrealized:
                                unrealized_pnl += float(pos_unrealized)
                        logger.debug(f"Aggregated unrealized PnL from {len(positions)} positions: {unrealized_pnl}")
                except Exception as e:
                    logger.debug(f"Could not aggregate PnL from positions: {e}")
            
            # If still no realized PnL, try to get from account tracker's daily PnL
            if realized_pnl == 0.0:
                try:
                    if hasattr(trading_bot, 'account_tracker') and trading_bot.account_tracker:
                        if account_id:
                            daily_pnl = trading_bot.account_tracker.get_daily_pnl(account_id=str(account_id))
                            if daily_pnl:
                                # Daily PnL includes both realized and unrealized, but we want only realized
                                # For now, use daily PnL as realized (this is approximate)
                                realized_pnl = float(daily_pnl)
                                logger.debug(f"Got realized PnL from daily PnL: {realized_pnl}")
                except Exception as e:
                    logger.debug(f"Could not get realized PnL from daily PnL: {e}")
            
            # Get compliance status if available
            compliance = {}
            if hasattr(trading_bot, 'account_tracker') and trading_bot.account_tracker:
                try:
                    compliance = trading_bot.account_tracker.check_compliance()
                except:
                    pass
            
            # Try to detect bracket mode by attempting a test bracket order or checking account settings
            bracket_mode = 'unknown'  # 'position', 'oco', or 'unknown'
            bracket_warning = None
            try:
                # Check if we've seen bracket order errors recently
                # This is a heuristic - if bracket orders fail with "Brackets cannot be used with Position Brackets"
                # then we know it's Position Brackets mode
                # We'll track this in a simple way by checking recent errors
                if hasattr(trading_bot, '_last_bracket_error'):
                    if 'Position Brackets' in str(trading_bot._last_bracket_error):
                        bracket_mode = 'position'
                        bracket_warning = '⚠️ Position Brackets mode detected. Enable Auto OCO Brackets in TopStepX account settings for bracket orders.'
                    elif 'Auto OCO Brackets' in str(trading_bot._last_bracket_error):
                        bracket_mode = 'position'
                        bracket_warning = '⚠️ Auto OCO Brackets not enabled. Bracket orders will fail.'
            except:
                pass
            
            state = {
                'account_id': account_id,
                'account_name': account_name,
                'balance': balance,
                'unrealized_pnl': unrealized_pnl,
                'realized_pnl': realized_pnl,
                'compliance': compliance,
                'bracket_mode': bracket_mode,
                'bracket_warning': bracket_warning
            }
            
            # Cache the result (with shorter TTL for PnL)
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
                # Handle None volume (can be None even if key exists)
                volume_raw = quote.get('volume', 0)
                current_volume = int(volume_raw) if volume_raw is not None else 0
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
                                error_str = str(e)
                                # Track bracket errors for mode detection
                                if hasattr(trading_bot, '_last_bracket_error'):
                                    trading_bot._last_bracket_error = error_str
                                else:
                                    trading_bot._last_bracket_error = error_str
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
            elif order_type == 'stop' or order_type == 'stop_market':
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
                    if not stop_price:
                        result = {'error': 'Stop orders require stop_price'}
                    else:
                        result = await trading_bot.place_stop_order(
                            symbol=order_symbol,
                            side=side,
                            quantity=quantity,
                            stop_price=float(stop_price)
                        )
            elif order_type == 'stop-limit' or order_type == 'stop_limit':
                # Stop limit order: requires both stop_price and limit_price
                if not stop_price or not limit_price:
                    result = {'error': 'Stop limit orders require both stop_price and limit_price'}
                else:
                    try:
                        # Stop limit orders use type 5 in TopStepX API
                        # We need to call the broker adapter directly with type 5
                        target_account = trading_bot.selected_account['id'] if trading_bot.selected_account else None
                        if not target_account:
                            result = {'error': 'No account selected'}
                        else:
                            # Get contract ID
                            contract_id = trading_bot._get_contract_id(order_symbol)
                            side_value = 0 if side.upper() == "BUY" else 1
                            
                            # Round prices to tick size
                            tick_size = await trading_bot._get_tick_size(order_symbol)
                            rounded_stop = trading_bot._round_to_tick_size(float(stop_price), tick_size)
                            rounded_limit = trading_bot._round_to_tick_size(float(limit_price), tick_size)
                            
                            # Create stop limit order (type 5)
                            order_data = {
                                "accountId": int(target_account),
                                "contractId": contract_id,
                                "type": 5,  # Stop Limit order type
                                "side": side_value,
                                "size": quantity,
                                "stopPrice": rounded_stop,
                                "limitPrice": rounded_limit,
                                "customTag": trading_bot._generate_unique_custom_tag("stop_limit")
                            }
                            
                            # Use broker adapter to place order
                            from brokers.topstepx_adapter import TopStepXAdapter
                            if hasattr(trading_bot, 'broker_adapter'):
                                adapter = trading_bot.broker_adapter
                            else:
                                adapter = TopStepXAdapter(trading_bot.auth_manager)
                            
                            # Place via adapter's _make_request
                            response = await adapter._make_request(
                                'POST',
                                '/api/Order',
                                json=order_data
                            )
                            
                            if response and 'id' in response:
                                result = {'success': True, 'orderId': response.get('id'), 'order_id': response.get('id')}
                            else:
                                result = {'error': f'Stop limit order failed: {response}'}
                    except Exception as e:
                        logger.error(f"Error placing stop limit order: {e}")
                        import traceback
                        logger.error(traceback.format_exc())
                        result = {'error': f'Failed to place stop limit order: {str(e)}'}
            elif order_type == 'trailing-stop' or order_type == 'trailing_stop':
                # Trailing stop order: requires trail_amount
                trail_amount = data.get('trail_amount') or data.get('trailAmount')
                if not trail_amount:
                    result = {'error': 'Trailing stop orders require trail_amount'}
                else:
                    try:
                        result = await trading_bot.place_trailing_stop_order(
                            symbol=order_symbol,
                            side=side,
                            quantity=quantity,
                            trail_amount=float(trail_amount)
                        )
                    except Exception as e:
                        logger.error(f"Error placing trailing stop order: {e}")
                        result = {'error': f'Failed to place trailing stop order: {str(e)}'}
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
                    
                    # Ensure quantity/size (always use absolute value for quantity)
                    quantity_raw = pos_dict.get('quantity') or pos_dict.get('size') or 0
                    quantity = abs(float(quantity_raw)) if quantity_raw else 0
                    pos_dict['quantity'] = quantity
                    pos_dict['size'] = quantity
                    
                    # Calculate unrealized PnL if not provided or is 0
                    unrealized_pnl = pos_dict.get('unrealizedPnL') or pos_dict.get('unrealized_pnl') or pos_dict.get('unrealizedPnl')
                    if unrealized_pnl is None or unrealized_pnl == 0:
                        # Try to calculate from current price and entry price
                        try:
                            entry_price = pos_dict.get('entryPrice') or pos_dict.get('entry_price')
                            current_price = pos_dict.get('currentPrice') or pos_dict.get('current_price') or pos_dict.get('markPrice')
                            
                            # If no current price, fetch it from market quote
                            if not current_price and symbol:
                                try:
                                    quote = await trading_bot.get_market_quote(symbol)
                                    if quote and "error" not in quote:
                                        current_price = quote.get('last') or quote.get('lastPrice') or quote.get('bid')
                                        if current_price:
                                            pos_dict['currentPrice'] = float(current_price)
                                            pos_dict['current_price'] = float(current_price)
                                except Exception as e:
                                    logger.debug(f"Could not fetch quote for {symbol}: {e}")
                            
                            if entry_price and current_price and quantity and symbol:
                                # Get tick size and point value for symbol
                                if hasattr(trading_bot, 'risk_manager'):
                                    tick_size = trading_bot.risk_manager.get_tick_size(symbol)
                                    point_value = trading_bot.risk_manager.get_point_value(symbol)
                                elif hasattr(trading_bot, '_get_tick_size') and hasattr(trading_bot, '_get_point_value'):
                                    tick_size = trading_bot._get_tick_size(symbol)
                                    point_value = trading_bot._get_point_value(symbol)
                                else:
                                    # Default values (correct point values for micro contracts)
                                    tick_size_map = {'MNQ': 0.25, 'MES': 0.25, 'NQ': 0.25, 'ES': 0.25, 'YM': 1.0}
                                    point_value_map = {'MNQ': 2.0, 'MES': 5.0, 'NQ': 20.0, 'ES': 50.0, 'YM': 5.0}
                                    tick_size = tick_size_map.get(symbol.upper(), 0.25)
                                    point_value = point_value_map.get(symbol.upper(), 2.0)
                                
                                # Calculate tick value: dollar value per tick
                                # tick_value = point_value * tick_size
                                # For MNQ: $5 per point * 0.25 points per tick = $1.25 per tick
                                tick_value = point_value * tick_size
                                
                                # Calculate price difference
                                entry_price_float = float(entry_price)
                                current_price_float = float(current_price)
                                
                                # Determine direction and calculate ticks
                                if side.upper() in ['LONG', 'BUY', '0']:
                                    # LONG: profit when current > entry
                                    price_diff = current_price_float - entry_price_float
                                    direction = 1 if price_diff >= 0 else -1  # 1 if favorable, -1 if unfavorable
                                else:  # SHORT
                                    # SHORT: profit when current < entry
                                    price_diff = entry_price_float - current_price_float
                                    direction = 1 if price_diff >= 0 else -1  # 1 if favorable, -1 if unfavorable
                                
                                # Calculate ticks from entry
                                ticks_from_entry = abs(price_diff) / tick_size
                                
                                # Calculate PnL: tick_value * ticks_from_entry * direction * quantity
                                unrealized_pnl = tick_value * ticks_from_entry * direction * float(quantity)
                                
                                pos_dict['unrealizedPnL'] = unrealized_pnl
                                pos_dict['unrealized_pnl'] = unrealized_pnl
                                pos_dict['unrealizedPnl'] = unrealized_pnl
                            else:
                                unrealized_pnl = 0.0
                        except Exception as e:
                            logger.debug(f"Could not calculate PnL for position {pos_dict.get('id')}: {e}")
                            unrealized_pnl = 0.0
                    
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
                    
                    # Ensure all field aliases exist (both camelCase and snake_case)
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
                    if 'unrealizedPnL' not in pos_dict and 'unrealized_pnl' in pos_dict:
                        pos_dict['unrealizedPnL'] = pos_dict['unrealized_pnl']
                    if 'unrealized_pnl' not in pos_dict and 'unrealizedPnL' in pos_dict:
                        pos_dict['unrealized_pnl'] = pos_dict['unrealizedPnL']
                    if 'unrealizedPnl' not in pos_dict:
                        pos_dict['unrealizedPnl'] = pos_dict.get('unrealizedPnL') or pos_dict.get('unrealized_pnl', 0)
                    
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
            # Get account ID from query or use default (handle None request for WebSocket broadcasts)
            account_id = request.query.get('account_id') if request else None
            orders = await trading_bot.get_open_orders(account_id=account_id)
            logger.debug(f"✅ Fetched {len(orders) if orders else 0} standalone orders for chart")
            
            # Also fetch linked orders (stop/take profit) for all positions
            # These are typically not returned by get_open_orders() but are linked to positions
            try:
                positions = await trading_bot.get_open_positions(account_id=account_id)
                linked_orders_all = []
                for pos in positions or []:
                    pos_id = pos.get('id') or pos.get('position_id')
                    if pos_id:
                        try:
                            linked_orders = await trading_bot.get_linked_orders(pos_id, account_id=account_id)
                            if linked_orders and isinstance(linked_orders, list):
                                # Include all linked orders (stop/take profit), not just AutoBracket ones
                                linked_orders_all.extend(linked_orders)
                        except Exception as e:
                            logger.debug(f"Could not fetch linked orders for position {pos_id}: {e}")
                
                # Combine standalone orders with linked orders
                if linked_orders_all:
                    logger.debug(f"✅ Found {len(linked_orders_all)} linked orders (stop/take profit)")
                    # Initialize orders list if None
                    if orders is None:
                        orders = []
                    # Add linked orders to the orders list (avoid duplicates by order ID)
                    existing_order_ids = {str(order.get('id') or order.get('orderId')) for order in orders}
                    for linked_order in linked_orders_all:
                        order_id = str(linked_order.get('id') or linked_order.get('orderId'))
                        if order_id and order_id not in existing_order_ids and order_id != 'None':
                            orders.append(linked_order)
                            existing_order_ids.add(order_id)
                    logger.debug(f"✅ Total orders (standalone + linked): {len(orders)}")
            except Exception as e:
                logger.debug(f"Could not fetch linked orders: {e}")
            
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
                            order_dict['status'] = 'OPEN'  # Keep as OPEN for display
                        elif status == 0:
                            order_dict['status'] = 'PENDING'
                    elif isinstance(status, str) and status.upper() == 'PENDING':
                        order_dict['status'] = 'OPEN'  # Normalize to OPEN for display
                    
                    # Convert order type: 1 = LIMIT, 2 = MARKET, 4 = STOP
                    order_type_num = order_dict.get('type', 0)
                    if isinstance(order_type_num, int):
                        type_map = {1: 'LIMIT', 2: 'MARKET', 4: 'STOP'}
                        order_dict['type'] = type_map.get(order_type_num, str(order_type_num))
                    elif not isinstance(order_dict.get('type'), str):
                        order_dict['type'] = str(order_type_num)
                    
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
                    
                    # For STOP orders, set price from stopPrice if no limitPrice
                    if order_dict.get('type') == 'STOP' or order_type_num == 4:
                        if stop_price is not None:
                            # For STOP orders, use stopPrice as the display price
                            order_dict['price'] = float(stop_price)
                        elif not order_dict.get('price'):
                            # If STOP order has no price set, try to get from triggerPrice
                            trigger_price = order_dict.get('triggerPrice') or order_dict.get('trigger_price')
                            if trigger_price:
                                order_dict['price'] = float(trigger_price)
                            else:
                                # Last resort: use 0.00 (will be displayed)
                                order_dict['price'] = 0.0
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
            # Force fresh fetch to ensure we have latest contracts
            contracts = await trading_bot.get_available_contracts(use_cache=False)
            logger.info(f"📋 Fetched {len(contracts)} contracts for chart dropdown")
            
            if not contracts:
                logger.warning("⚠️ No contracts returned - attempting re-fetch...")
                # Try one more time with a slight delay
                import asyncio
                await asyncio.sleep(0.5)
                contracts = await trading_bot.get_available_contracts(use_cache=False)
                logger.info(f"📋 Re-fetched {len(contracts)} contracts")
            
            def extract_root_symbol(contract_id: str) -> Optional[str]:
                """
                TopStepX contract IDs often look like: CON.F.US.MNQ.H26
                We want MNQ as the dropdown symbol.
                """
                if not contract_id:
                    return None
                try:
                    parts = str(contract_id).split(".")
                    # CON.F.US.<SYM>.<EXP>
                    if len(parts) >= 5:
                        return parts[3].strip().upper()
                except Exception:
                    pass
                return None

            # Group by symbol
            by_symbol = {}
            for c in contracts:
                contract_id = c.get('contractId') or c.get('ContractId') or c.get('id') or c.get('contract_id') or ''
                
                # ALWAYS try to extract root symbol from contract ID FIRST (most reliable)
                sym = extract_root_symbol(contract_id)
                
                # Only if that fails, fall back to name/symbol fields
                if not sym:
                    sym = (
                        c.get('symbol')
                        or c.get('Symbol')
                        or c.get('name')
                        or 'Unknown'
                    )
                # Skip 'Unknown' symbols and empty symbols
                if sym and sym != 'Unknown' and sym.strip():
                    if sym not in by_symbol:
                        by_symbol[sym] = []
                    by_symbol[sym].append({'id': contract_id, 'description': c.get('description', '')})
            
            symbols_list = sorted(list(by_symbol.keys()))
            logger.info(f"✅ Grouped into {len(symbols_list)} unique symbols: {symbols_list[:10] if len(symbols_list) > 10 else symbols_list}")
            
            # Find the active MNQ contract (most recent or highest volume)
            default_symbol = None
            if 'MNQ' in by_symbol:
                mnq_contracts = by_symbol['MNQ']
                if mnq_contracts:
                    # Sort by contractId to get the most recent expiration (e.g., H26 > G26)
                    sorted_mnq = sorted(mnq_contracts, key=lambda x: x.get('id', ''), reverse=True)
                    default_symbol = sorted_mnq[0].get('id') if sorted_mnq else None
                    logger.info(f"📌 Default MNQ contract: {default_symbol}")
            
            response = web.json_response({
                'contracts': by_symbol, 
                'symbols': symbols_list,
                'default_symbol': default_symbol  # Return the active MNQ contract ID
            })
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
                    # Get strategy config
                    config = getattr(strategy, 'config', None)
                    symbols = getattr(config, 'symbols', []) if config else []
                    timeframe = getattr(config, 'timeframe', None) or getattr(strategy, 'timeframe', None) or 'N/A'
                    
                    # Get start time if available
                    start_time = None
                    if hasattr(strategy, 'start_time'):
                        start_time = strategy.start_time
                    elif hasattr(strategy, '_start_time'):
                        start_time = strategy._start_time
                    
                    # Format start time
                    start_time_str = 'N/A'
                    if start_time:
                        if isinstance(start_time, str):
                            start_time_str = start_time
                        else:
                            from datetime import datetime
                            if isinstance(start_time, datetime):
                                start_time_str = start_time.strftime('%Y-%m-%d %H:%M:%S')
                            else:
                                start_time_str = str(start_time)
                    
                    statuses[name] = {
                        'name': name,
                        'status': getattr(strategy, 'status', {}).name if hasattr(getattr(strategy, 'status', None), 'name') else str(getattr(strategy, 'status', 'unknown')),
                        'active': getattr(strategy, 'status', None) == getattr(strategy.__class__, 'Status', type('Status', (), {'ACTIVE': 'active'}))().ACTIVE if hasattr(strategy.__class__, 'Status') else False,
                        'symbols': symbols,
                        'timeframe': timeframe,
                        'start_time': start_time_str,
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
            timeframe = data.get('timeframe')
            
            if not hasattr(trading_bot, 'strategy_manager'):
                return web.json_response({'success': False, 'error': 'Strategy manager not available'})
            
            # Use CLI command parser for consistency (handles --timeframe and --symbols)
            from core.cli_command_parser import CLICommandParser
            parser = CLICommandParser(trading_bot)
            
            # Build command string
            cmd_parts = ['strategies', 'start', strategy_name]
            if symbols:
                symbols_str = ','.join(symbols) if isinstance(symbols, list) else str(symbols)
                cmd_parts.append(f'--symbols={symbols_str}')
            if timeframe:
                cmd_parts.append(f'--timeframe={timeframe}')
            
            command = ' '.join(cmd_parts)
            logger.info(f"Executing strategy start command: {command}")
            
            # Execute via CLI parser
            result = await parser.parse_and_execute(command, interactive=False)
            
            if result.get('success'):
                response = web.json_response({'success': True, 'message': result.get('message', 'Strategy started')})
            else:
                response = web.json_response({'success': False, 'error': result.get('error', 'Failed to start strategy')})
            
            response.headers['Access-Control-Allow-Origin'] = '*'
            return response
        except Exception as e:
            logger.error(f"Error starting strategy: {e}")
            import traceback
            logger.error(traceback.format_exc())
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
            
            # Use CLI command parser for consistency
            from core.cli_command_parser import CLICommandParser
            parser = CLICommandParser(trading_bot)
            
            command = f'strategies stop {strategy_name}'
            logger.info(f"Executing strategy stop command: {command}")
            
            # Execute via CLI parser
            result = await parser.parse_and_execute(command, interactive=False)
            
            if result.get('success'):
                response = web.json_response({'success': True, 'message': result.get('message', 'Strategy stopped')})
            else:
                response = web.json_response({'success': False, 'error': result.get('error', 'Failed to stop strategy')})
            
            response.headers['Access-Control-Allow-Origin'] = '*'
            return response
        except Exception as e:
            logger.error(f"Error stopping strategy: {e}")
            import traceback
            logger.error(traceback.format_exc())
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
            # Get selected account id (selected_account may be a dict)
            account_id = None
            if hasattr(trading_bot, "selected_account") and trading_bot.selected_account:
                if isinstance(trading_bot.selected_account, dict):
                    account_id = trading_bot.selected_account.get("id")
                else:
                    account_id = trading_bot.selected_account

            # Get all open orders
            orders = await trading_bot.get_open_orders(account_id=account_id)
            
            if not orders:
                return web.json_response({'success': True, 'message': 'No orders to cancel', 'canceled': 0})
            
            # Cancel each order
            canceled = []
            failed = []
            for order in orders:
                try:
                    order_id = order.get('orderId') or order.get('id')
                    if order_id:
                        await trading_bot.cancel_order(order_id=str(order_id), account_id=account_id)
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
    
    async def handle_get_accounts(request):
        """Handle get accounts request."""
        try:
            # Ensure accounts are loaded - use list_accounts() if not available
            if not hasattr(trading_bot, 'accounts') or not trading_bot.accounts:
                # Try to fetch accounts using list_accounts() method
                try:
                    accounts = await trading_bot.list_accounts()
                    if accounts:
                        trading_bot.accounts = accounts
                        logger.info(f"✅ Loaded {len(accounts)} accounts for GUI")
                    else:
                        logger.warning("No accounts returned from list_accounts()")
                except Exception as e:
                    logger.warning(f"Could not fetch accounts via list_accounts(): {e}")
                    # Try auth_manager as fallback
                    if hasattr(trading_bot, 'auth_manager') and hasattr(trading_bot.auth_manager, 'accounts'):
                        trading_bot.accounts = trading_bot.auth_manager.accounts
            
            accounts_list = []
            if hasattr(trading_bot, 'accounts') and trading_bot.accounts:
                for idx, acc in enumerate(trading_bot.accounts):
                    account_data = {
                        'index': idx,
                        'name': acc.get('name', acc.get('accountName', 'Unknown')),
                        'id': acc.get('id', acc.get('accountId', '')),
                        'balance': float(acc.get('balance', acc.get('currentBalance', 0))),
                        'status': acc.get('status', 'unknown'),
                        'type': acc.get('accountType', 'unknown'),
                        'selected': False
                    }
                    # Check if this is the currently selected account
                    if hasattr(trading_bot, 'selected_account'):
                        if isinstance(trading_bot.selected_account, dict):
                            if trading_bot.selected_account.get('id') == account_data['id']:
                                account_data['selected'] = True
                        elif str(trading_bot.selected_account) == str(account_data['id']):
                            account_data['selected'] = True
                    accounts_list.append(account_data)
            
            return web.json_response({'accounts': accounts_list})
        except Exception as e:
            logger.error(f"❌ Error fetching accounts: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return web.json_response({'accounts': [], 'error': str(e)})
    
    async def handle_select_account(request):
        """Handle account selection request."""
        try:
            data = await request.json()
            account_index = data.get('account_index')
            
            if account_index is None:
                return web.json_response({'success': False, 'error': 'Invalid account index'})
            
            # Ensure accounts are loaded
            if not hasattr(trading_bot, 'accounts') or not trading_bot.accounts:
                # Fetch accounts if not loaded using list_accounts()
                try:
                    accounts = await trading_bot.list_accounts()
                    if accounts:
                        trading_bot.accounts = accounts
                        logger.info(f"✅ Loaded {len(accounts)} accounts for account selection")
                    else:
                        return web.json_response({'success': False, 'error': 'No accounts available'})
                except Exception as e:
                    logger.error(f"Error fetching accounts: {e}")
                    import traceback
                    logger.error(traceback.format_exc())
                    return web.json_response({'success': False, 'error': f'Failed to load accounts: {e}'})
            
            if account_index < 0 or account_index >= len(trading_bot.accounts):
                return web.json_response({'success': False, 'error': f'Account index out of range (0-{len(trading_bot.accounts)-1})'})
            
            # Select the account using the bot's own logic (ensures caches/trackers update consistently)
            selected_account = trading_bot.accounts[account_index]
            account_id = selected_account.get('id', selected_account.get('accountId'))
            if account_id is None:
                return web.json_response({'success': False, 'error': 'Selected account has no id'})
            await trading_bot.switch_account(str(account_id))
            
            # Update account tracker if available
            if hasattr(trading_bot, 'account_tracker'):
                if account_id:
                    account_name = selected_account.get('name', selected_account.get('accountName', f'Account-{account_id}'))
                    account_type = selected_account.get('type', selected_account.get('accountType', 'unknown'))
                    account_balance = float(selected_account.get('balance', selected_account.get('currentBalance', 0)))
                    
                    # Check if account is already tracked
                    if str(account_id) not in trading_bot.account_tracker.accounts:
                        logger.info(f"Initializing account tracker for {account_name} (ID: {account_id})")
                        trading_bot.account_tracker.initialize_account(
                            account_id=str(account_id),
                            account_name=account_name,
                            account_type=account_type,
                            starting_balance=account_balance
                        )
                    else:
                        # Update current account ID in tracker
                        trading_bot.account_tracker.current_account_id = str(account_id)
                        logger.info(f"Switched tracker to account {account_name} (ID: {account_id})")
            
            account_name = selected_account.get('name', selected_account.get('accountName', 'Unknown'))
            logger.info(f"✅ Switched to account: {account_name}")
            
            return web.json_response({
                'success': True,
                'account_name': account_name,
                'account_id': selected_account.get('id', selected_account.get('accountId'))
            })
        except Exception as e:
            logger.error(f"❌ Error selecting account: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return web.json_response({'success': False, 'error': str(e)})
    
    app.router.add_get('/api/accounts', handle_get_accounts)
    app.router.add_post('/api/select_account', handle_select_account)
    app.router.add_options('/api/select_account', handle_options)
    
    async def handle_execute_command(request):
        """Handle command execution from the GUI."""
        try:
            data = await request.json()
            command = data.get('command', '').strip()
            
            if not command:
                return web.json_response({'success': False, 'error': 'No command provided'})
            
            logger.info(f"🎮 GUI command execution: {command}")
            
            # Import CLI command parser
            from core.cli_command_parser import CLICommandParser
            
            # Create parser instance
            parser = CLICommandParser(trading_bot)
            
            # Execute command
            resp = await parser.execute_command(command)
            if resp.get("success"):
                result = resp.get("result")
                if result is None:
                    return web.json_response({'success': True, 'output': 'Command executed successfully'})
                if isinstance(result, (dict, list)):
                    import json
                    output = json.dumps(result, indent=2)
                    return web.json_response({'success': True, 'output': output, 'result': result})
                # Convert result to string, handling None and other types
                output = str(result) if result is not None else 'Command executed successfully'
                return web.json_response({'success': True, 'output': output, 'result': result})
            # Return error with helpful message
            error_msg = resp.get("error", "Command failed")
            available = resp.get("available_commands", [])
            if available:
                error_msg += f"\n\nAvailable commands: {', '.join(available[:20])}"  # Limit to first 20
            return web.json_response({'success': False, 'error': error_msg, 'result': resp})
        except Exception as e:
            logger.error(f"❌ Error executing command: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return web.json_response({'success': False, 'error': str(e)})
    
    app.router.add_post('/api/execute_command', handle_execute_command)
    app.router.add_options('/api/execute_command', handle_options)
    
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
            
            # Replace placeholders with actual values
            html_content = html_content.replace('{{SERVER_PORT}}', str(port))
            html_content = html_content.replace('{{SYMBOL}}', _chart_server_symbol or symbol)
            timeframe_value = _chart_server_timeframe or '5m'
            html_content = html_content.replace('{{TIMEFRAME}}', timeframe_value)
            
            # Replace timeframe selection in dropdown (simple string replacement)
            # Remove all selected attributes first
            import re
            html_content = re.sub(r'<option value="([^"]+)"([^>]*)\s+selected>', r'<option value="\1"\2>', html_content)
            # Add selected to the matching timeframe option
            html_content = re.sub(
                rf'<option value="{re.escape(timeframe_value)}"([^>]*)>',
                rf'<option value="{timeframe_value}"\1 selected>',
                html_content
            )
            
            response = web.Response(text=html_content, content_type='text/html')
            response.headers['Access-Control-Allow-Origin'] = '*'
            return response
        except Exception as e:
            logger.error(f"Error serving master control: {e}")
            response = web.Response(text=f"Error: {e}", status=500)
            return response
    
    async def handle_popout(request):
        """Handle popout widget requests."""
        try:
            widget_id = request.query.get('widget', 'chart-panel')
            port = request.query.get('port', _chart_server_port)
            symbol = request.query.get('symbol', _chart_server_symbol or 'MNQ')
            timeframe = request.query.get('timeframe', _chart_server_timeframe or '5m')
            
            # Read master control HTML and extract the specific widget
            from pathlib import Path
            master_html_path = Path(__file__).parent / 'master_control.html'
            if not master_html_path.exists():
                return web.Response(text="Master control HTML not found", status=404)
            
            with open(master_html_path, 'r', encoding='utf-8') as f:
                html_content = f.read()
            
            # Create a simplified popout page with just the widget
            # This is a simplified version - in production you'd want a dedicated popout template
            popout_html = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Widget Popout</title>
    <script src="https://unpkg.com/lightweight-charts@4.1.3/dist/lightweight-charts.standalone.production.js"></script>
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #0a0a0a;
            color: #e0e0e0;
            padding: 20px;
            height: 100vh;
            overflow: hidden;
        }}
        .panel {{
            background: #1a1a1a;
            border: 2px solid transparent;
            border-radius: 4px;
            padding: 8px;
            overflow: hidden;
            position: relative;
            border-image: linear-gradient(135deg, #a855f7, #ef4444, #3b82f6, #f97316, #eab308, #a855f7) 1;
            box-shadow: 0 0 12px rgba(168, 85, 247, 0.06), 0 0 8px rgba(59, 130, 246, 0.04);
            height: 100%;
            display: flex;
            flex-direction: column;
        }}
        .panel h2 {{
            font-size: 13px;
            margin-bottom: 10px;
            color: #fff;
            border-bottom: 1px solid #333;
            padding-bottom: 6px;
            font-weight: 600;
        }}
        .panel-content {{
            flex: 1;
            overflow: auto;
        }}
        #chart-container {{
            flex: 1;
            min-height: 600px;
            height: 100%;
            background: #0a0a0a;
            border-radius: 4px;
            margin-top: 8px;
        }}
        .chart-controls {{
            display: flex;
            gap: 8px;
            margin-bottom: 12px;
            flex-wrap: wrap;
            padding: 12px;
            background: #151515;
            border-radius: 4px;
        }}
        .chart-controls select, .chart-controls input, .chart-controls button {{
            background: #222;
            border: 1px solid #444;
            color: #fff;
            padding: 6px 10px;
            border-radius: 4px;
            font-size: 12px;
        }}
        .btn-primary {{ background: #4a9eff; color: #fff; }}
        .btn-buy {{ background: #26a69a; color: #fff; font-weight: bold; }}
        .btn-sell {{ background: #ef5350; color: #fff; font-weight: bold; }}
        .btn-danger {{ background: #f44336; color: #fff; }}
        table {{ width: 100%; border-collapse: collapse; font-size: 11px; }}
        th {{ text-align: left; padding: 4px 6px; background: #151515; color: #888; font-size: 10px; }}
        td {{ padding: 5px 6px; border-bottom: 1px solid #222; font-size: 11px; }}
    </style>
</head>
<body>
    <div class="panel" id="{widget_id}">
        <!-- Widget content will be loaded via JavaScript -->
    </div>
    <script>
        const SERVER_PORT = {port};
        const BASE_URL = `http://127.0.0.1:${{SERVER_PORT}}`;
        const SYMBOL = '{symbol}';
        const TIMEFRAME = '{timeframe}';
        const WIDGET_ID = '{widget_id}';
        
        // Load widget content from parent page via postMessage
        window.addEventListener('message', function(event) {{
            if (event.data.type === 'widget-content' && event.data.widgetId === WIDGET_ID) {{
                document.getElementById(WIDGET_ID).innerHTML = event.data.html;
                // Reinitialize any widgets that need it
                if (WIDGET_ID === 'chart-panel' && typeof LightweightCharts !== 'undefined') {{
                    // Chart initialization code would go here
                }}
            }}
        }});
        
        // Request widget content from opener and set up communication
        if (window.opener) {{
            // Request widget content
            window.opener.postMessage({{type: 'request-widget', widgetId: WIDGET_ID}}, '*');
            
            // Set up WebSocket connection for real-time updates
            let ws = null;
            function connectWebSocket() {{
                try {{
                    ws = new WebSocket(`ws://127.0.0.1:${{SERVER_PORT}}/ws`);
                    ws.onmessage = function(event) {{
                        try {{
                            const message = JSON.parse(event.data);
                            if (message.type === 'signal' && WIDGET_ID === 'signal-feed-panel') {{
                                if (typeof addSignal === 'function') {{
                                    addSignal(message.data);
                                }}
                            }} else if (message.type === 'positions' && WIDGET_ID === 'positions-panel') {{
                                if (typeof updatePositionsDisplay === 'function') {{
                                    updatePositionsDisplay(message.data.positions || []);
                                }}
                            }} else if (message.type === 'orders' && WIDGET_ID === 'orders-panel') {{
                                if (typeof updateOrdersDisplay === 'function') {{
                                    updateOrdersDisplay(message.data.orders || []);
                                }}
                            }} else if (message.type === 'strategies' && WIDGET_ID === 'strategy-panel') {{
                                if (typeof updateStrategiesDisplay === 'function') {{
                                    updateStrategiesDisplay(message.data);
                                }}
                            }} else if (message.type === 'account' && WIDGET_ID === 'account-bar') {{
                                if (typeof updateAccountDisplay === 'function') {{
                                    updateAccountDisplay(message.data);
                                }}
                            }}
                        }} catch (e) {{
                            console.error('Error handling WebSocket message:', e);
                        }}
                    }};
                    ws.onopen = function() {{
                        console.log('✅ Popout WebSocket connected');
                        // Send ping to keep connection alive
                        setInterval(() => {{
                            if (ws && ws.readyState === WebSocket.OPEN) {{
                                ws.send(JSON.stringify({{ type: 'ping' }}));
                            }}
                        }}, 30000);
                    }};
                    ws.onerror = function(error) {{
                        console.error('WebSocket error:', error);
                    }};
                    ws.onclose = function() {{
                        console.log('WebSocket closed, reconnecting...');
                        setTimeout(connectWebSocket, 3000);
                    }};
                }} catch (e) {{
                    console.error('Failed to create WebSocket:', e);
                }}
            }}
            connectWebSocket();
            
            // Load initial data
            setTimeout(() => {{
                if (WIDGET_ID === 'chart-panel' && typeof LightweightCharts !== 'undefined') {{
                    // Chart will be initialized by opener
                }} else if (WIDGET_ID === 'positions-panel') {{
                    fetch(`${{BASE_URL}}/api/chart/positions`).then(r => r.json()).then(d => {{
                        if (d.positions && typeof updatePositionsDisplay === 'function') {{
                            updatePositionsDisplay(d.positions);
                        }}
                    }});
                }} else if (WIDGET_ID === 'orders-panel') {{
                    fetch(`${{BASE_URL}}/api/chart/orders`).then(r => r.json()).then(d => {{
                        if (d.orders && typeof updateOrdersDisplay === 'function') {{
                            updateOrdersDisplay(d.orders);
                        }}
                    }});
                }} else if (WIDGET_ID === 'strategy-panel') {{
                    fetch(`${{BASE_URL}}/api/chart/strategy/status`).then(r => r.json()).then(d => {{
                        if (typeof updateStrategiesDisplay === 'function') {{
                            updateStrategiesDisplay(d);
                        }}
                    }});
                }} else if (WIDGET_ID === 'signal-feed-panel') {{
                    // Signal feed will receive updates via WebSocket
                    const container = document.getElementById('signal-feed-container');
                    if (container && container.children.length === 0) {{
                        container.innerHTML = '<div style="color: #888; text-align: center; padding: 20px;">Waiting for signals...</div>';
                    }}
                }}
            }}, 500);
        }}
        
        // Include all necessary JavaScript functions for popout widgets
        // These functions are needed for widgets to function independently
        function addSignal(signal) {{
            const container = document.getElementById('signal-feed-container');
            if (!container) return;
            
            if (container.children.length === 1 && (container.firstChild.textContent.includes('Waiting') || container.firstChild.textContent.includes('No signals'))) {{
                container.innerHTML = '';
            }}
            
            const signalEntry = document.createElement('div');
            signalEntry.style.marginBottom = '8px';
            signalEntry.style.padding = '8px';
            signalEntry.style.background = '#1a1a1a';
            signalEntry.style.border = '1px solid #333';
            signalEntry.style.borderRadius = '4px';
            signalEntry.style.fontSize = '11px';
            
            const timestamp = signal.timestamp ? new Date(signal.timestamp).toLocaleTimeString() : new Date().toLocaleTimeString();
            const signalType = signal.type || signal.direction || 'SIGNAL';
            const signalColor = signalType === 'BUY' || signalType === 'LONG' ? '#4caf50' : signalType === 'SELL' || signalType === 'SHORT' ? '#f44336' : '#ff9800';
            
            const entryPrice = signal.entry_price ? parseFloat(signal.entry_price) || 0 : null;
            const stopLoss = signal.stop_loss ? parseFloat(signal.stop_loss) || 0 : null;
            const takeProfit = signal.take_profit ? parseFloat(signal.take_profit) || 0 : null;
            
            let priceHtml = '';
            if (entryPrice !== null) {{
                priceHtml += `<div style="color: #888; font-size: 10px;">Entry: $${entryPrice.toFixed(2)}</div>`;
            }}
            if (stopLoss !== null) {{
                priceHtml += `<div style="color: #f44336; font-size: 10px;">Stop: $${stopLoss.toFixed(2)}</div>`;
            }}
            if (takeProfit !== null) {{
                priceHtml += `<div style="color: #4caf50; font-size: 10px;">Target: $${takeProfit.toFixed(2)}</div>`;
            }}
            
            signalEntry.innerHTML = `
                <div style="display: flex; justify-content: space-between; margin-bottom: 4px;">
                    <span style="color: ${{signalColor}}; font-weight: 600;">${{signalType}}</span>
                    <span style="color: #888; font-size: 10px;">${{timestamp}}</span>
                </div>
                <div style="color: #d1d5db; margin-bottom: 2px;"><strong>${{signal.strategy || 'Unknown'}}</strong> - ${{signal.symbol || 'N/A'}}</div>
                ${{priceHtml}}
                <div style="color: #888; font-size: 10px; margin-top: 4px;">${{signal.message || 'No details'}}</div>
            `;
            
            container.insertBefore(signalEntry, container.firstChild);
            while (container.children.length > 20) {{
                container.removeChild(container.lastChild);
            }}
        }}
        
        function updatePositionsDisplay(positions) {{
            const tbody = document.getElementById('positions-body');
            if (!tbody) return;
            
            if (positions.length === 0) {{
                tbody.innerHTML = '<tr><td colspan="5" class="empty-state">No positions</td></tr>';
            }} else {{
                tbody.innerHTML = positions.map(pos => {{
                    const pnl = pos.unrealizedPnL || pos.unrealized_pnl || pos.unrealizedPnl || 0;
                    const entryPrice = pos.entryPrice || pos.entry_price || 0;
                    const quantity = pos.quantity || pos.size || 0;
                    return `
                        <tr>
                            <td>${{pos.symbol || 'N/A'}}</td>
                            <td>${{pos.side || 'N/A'}}</td>
                            <td>${{quantity}}</td>
                            <td>$${{entryPrice.toFixed(2)}}</td>
                            <td class="${{pnl >= 0 ? 'positive' : 'negative'}}">$${{pnl.toFixed(2)}}</td>
                        </tr>
                    `;
                }}).join('');
            }}
        }}
        
        function updateOrdersDisplay(orders) {{
            const tbody = document.getElementById('orders-body');
            if (!tbody) return;
            
            if (orders.length === 0) {{
                tbody.innerHTML = '<tr><td colspan="5" class="empty-state">No orders</td></tr>';
            }} else {{
                tbody.innerHTML = orders.map(order => {{
                    const price = order.price || order.limitPrice || order.limit_price || 
                                 order.stopPrice || order.stop_price || 
                                 order.triggerPrice || order.trigger_price || 0;
                    const priceNum = parseFloat(price) || 0;
                    return `
                        <tr>
                            <td>${{order.symbol || 'N/A'}}</td>
                            <td>${{order.side || 'N/A'}}</td>
                            <td>${{order.quantity || 0}}</td>
                            <td>$${{priceNum.toFixed(2)}}</td>
                            <td>${{order.type || 'N/A'}}</td>
                        </tr>
                    `;
                }}).join('');
            }}
        }}
        
        function updateStrategiesDisplay(data) {{
            const grid = document.getElementById('strategy-grid');
            if (!grid) return;
            
            const strategies = Object.values(data.strategies || {{}});
            const active = data.active || [];
            
            if (strategies.length === 0) {{
                grid.innerHTML = '<div class="empty-state">No strategies available</div>';
            }} else {{
                grid.innerHTML = strategies.map(strategy => {{
                    const isActive = active.includes(strategy.name);
                    return `
                        <div class="strategy-card">
                            <div class="strategy-header">
                                <span class="strategy-name">${{strategy.name}}</span>
                                <span class="strategy-status ${{isActive ? 'active' : 'idle'}}">
                                    ${{isActive ? 'ACTIVE' : 'IDLE'}}
                                </span>
                            </div>
                            <div class="strategy-info">
                                ${{strategy.symbols && strategy.symbols.length > 0 ? strategy.symbols.join(', ') : 'No symbols'}} | 
                                ${{strategy.timeframe || 'N/A'}} | 
                                ${{strategy.positions || 0}} pos
                                ${{strategy.start_time && strategy.start_time !== 'N/A' ? '<br><span style="font-size: 9px; color: #888;">Started: ' + strategy.start_time + '</span>' : ''}}
                            </div>
                        </div>
                    `;
                }}).join('');
            }}
        }}
        
        function updateAccountDisplay(data) {{
            const accountNameEl = document.getElementById('account-name');
            if (accountNameEl) accountNameEl.textContent = data.account_name || '--';
            
            const balanceEl = document.getElementById('balance');
            if (balanceEl) balanceEl.textContent = `$${{(data.balance || 0).toFixed(2)}}`;
            
            const unrealized = data.unrealized_pnl || 0;
            const unrealizedEl = document.getElementById('unrealized-pnl');
            if (unrealizedEl) {{
                unrealizedEl.textContent = `$${{unrealized.toFixed(2)}}`;
                unrealizedEl.className = `status-value ${{unrealized >= 0 ? 'positive' : 'negative'}}`;
            }}
            
            const realized = data.realized_pnl || 0;
            const realizedEl = document.getElementById('realized-pnl');
            if (realizedEl) {{
                realizedEl.textContent = `$${{realized.toFixed(2)}}`;
                realizedEl.className = `status-value ${{realized >= 0 ? 'positive' : 'negative'}}`;
            }}
        }}
        
        // Add "Pop Back In" button
        const popBackBtn = document.createElement('button');
        popBackBtn.textContent = '↩ Pop Back In';
        popBackBtn.style.cssText = 'position: fixed; top: 10px; right: 10px; z-index: 10000; background: #4caf50; color: #fff; border: none; padding: 8px 16px; border-radius: 4px; font-size: 12px; cursor: pointer;';
        popBackBtn.onclick = function() {{
            if (window.opener) {{
                window.opener.postMessage({{type: 'pop-back-in', widgetId: WIDGET_ID}}, '*');
                window.close();
            }}
        }};
        document.body.appendChild(popBackBtn);
    </script>
</body>
</html>
            """
            
            return web.Response(text=popout_html, content_type='text/html')
        except Exception as e:
            logger.error(f"Error handling popout: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return web.Response(text=f"Error: {e}", status=500)
    
    # WebSocket support for real-time updates
    _ws_clients = set()
    _ws_broadcast_task = None
    
    async def handle_websocket(request):
        """Handle WebSocket connections for real-time updates."""
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        
        _ws_clients.add(ws)
        logger.info(f"📡 WebSocket client connected (total: {len(_ws_clients)})")
        
        try:
            async for msg in ws:
                if msg.type == web.WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                        # Handle client requests (subscribe/unsubscribe)
                        if data.get('type') == 'ping':
                            await ws.send_json({'type': 'pong'})
                    except json.JSONDecodeError:
                        pass
                elif msg.type == web.WSMsgType.ERROR:
                    logger.error(f'WebSocket error: {ws.exception()}')
        finally:
            _ws_clients.discard(ws)
            logger.info(f"📡 WebSocket client disconnected (remaining: {len(_ws_clients)})")
        
        return ws
    
    async def broadcast_update(data: dict):
        """Broadcast update to all connected WebSocket clients."""
        if not _ws_clients:
            return
        
        # Send to all clients, remove dead connections
        dead_clients = set()
        for client in _ws_clients:
            try:
                await client.send_json(data)
            except Exception as e:
                logger.debug(f"Failed to send to client: {e}")
                dead_clients.add(client)
        
        # Clean up dead connections
        for client in dead_clients:
            _ws_clients.discard(client)
    
    # Make broadcast_update globally accessible for strategy signals
    # Store it in the module namespace so it can be imported
    import sys
    current_module = sys.modules[__name__]
    current_module.broadcast_update = broadcast_update
    
    async def tail_log_file():
        """Tail the trading_bot.log file and broadcast new lines."""
        log_file_path = Path("trading_bot.log")
        
        # Open file and seek to end
        try:
            with open(log_file_path, 'r') as f:
                # Seek to end minus last 10KB for recent history
                f.seek(0, os.SEEK_END)
                file_size = f.tell()
                if file_size > 10240:  # 10KB
                    f.seek(file_size - 10240)
                    f.readline()  # Skip partial line
                else:
                    f.seek(0)
                
                # Send recent lines
                recent_lines = f.readlines()
                for line in recent_lines[-50:]:  # Last 50 lines
                    log_data = parse_log_line(line)
                    if log_data:
                        await broadcast_update({'type': 'log', 'data': log_data})
                
                # Now tail for new lines
                while True:
                    line = f.readline()
                    if line:
                        log_data = parse_log_line(line)
                        if log_data:
                            await broadcast_update({'type': 'log', 'data': log_data})
                    else:
                        await asyncio.sleep(0.1)  # Wait for new content
        except FileNotFoundError:
            logger.warning("trading_bot.log not found, log streaming disabled")
        except Exception as e:
            logger.error(f"Error tailing log file: {e}")
    
    def parse_log_line(line: str) -> Optional[Dict]:
        """Parse a log line into structured data."""
        try:
            # Format: "2025-12-29 12:34:56,789 - module - LEVEL - message"
            parts = line.split(' - ', 3)
            if len(parts) >= 3:
                timestamp = parts[0].strip()
                level = parts[2].strip()
                message = parts[3].strip() if len(parts) >= 4 else parts[2].strip()
                
                return {
                    'timestamp': timestamp,
                    'level': level,
                    'message': message
                }
        except Exception:
            pass
        return None
    
    async def websocket_broadcast_loop():
        """Background task to broadcast updates to WebSocket clients."""
        logger.info("📡 WebSocket broadcast loop started")
        
        # Start log tailing in separate task
        log_task = asyncio.create_task(tail_log_file())
        
        while True:
            try:
                if _ws_clients:
                    # Broadcast account state (clear cache to force fresh calculation)
                    try:
                        # Clear cache to ensure fresh PnL calculation
                        account_id = None
                        if hasattr(trading_bot, 'selected_account') and trading_bot.selected_account:
                            if isinstance(trading_bot.selected_account, dict):
                                account_id = trading_bot.selected_account.get('id')
                            else:
                                account_id = str(trading_bot.selected_account)
                        
                        if account_id:
                            cache_key = f"account_state_{account_id}"
                            if cache_key in _account_state_cache:
                                del _account_state_cache[cache_key]
                        
                        response = await handle_account_state(None)
                        if hasattr(response, 'text'):
                            account_data = json.loads(response.text)
                            await broadcast_update({'type': 'account', 'data': account_data})
                    except Exception as e:
                        logger.debug(f"Error broadcasting account state: {e}")
                    
                    # Broadcast positions
                    try:
                        response = await handle_get_positions(None)
                        if hasattr(response, 'text'):
                            positions_data = json.loads(response.text)
                            await broadcast_update({'type': 'positions', 'data': positions_data})
                    except Exception as e:
                        logger.debug(f"Error broadcasting positions: {e}")
                    
                    # Broadcast orders
                    try:
                        response = await handle_get_orders(None)
                        if hasattr(response, 'text'):
                            orders_data = json.loads(response.text)
                            await broadcast_update({'type': 'orders', 'data': orders_data})
                    except Exception as e:
                        logger.debug(f"Error broadcasting orders: {e}")
                    
                    # Broadcast strategy status
                    try:
                        response = await handle_strategy_status(None)
                        if hasattr(response, 'text'):
                            strategy_data = json.loads(response.text)
                            await broadcast_update({'type': 'strategies', 'data': strategy_data})
                    except Exception as e:
                        logger.debug(f"Error broadcasting strategy status: {e}")
                
                # Note: Strategy signals are broadcast directly from strategy_manager.py
                # when signals are generated, not from this loop
                
                await asyncio.sleep(2)  # Broadcast every 2 seconds
            except asyncio.CancelledError:
                logger.info("📡 WebSocket broadcast loop cancelled")
                log_task.cancel()
                break
            except Exception as e:
                logger.error(f"Error in WebSocket broadcast loop: {e}")
                await asyncio.sleep(5)
    
    # Register WebSocket route (only if not already registered)
    # Check if route already exists to avoid duplicate registration
    ws_route_exists = any(
        route.method == 'GET' and str(route.resource) == '/ws'
        for route in app.router.routes()
    )
    if not ws_route_exists:
        app.router.add_get('/ws', handle_websocket)
    
    # Register main page routes
    app.router.add_get('/', handle_master_control)
    app.router.add_get('/master', handle_master_control)
    app.router.add_get('/popout', handle_popout)
    
    # Add favicon handler to prevent 404 errors
    async def handle_favicon(request):
        """Handle favicon requests."""
        return web.Response(status=204)  # No content
    
    app.router.add_get('/favicon.ico', handle_favicon)
    
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
    _chart_server_symbol = symbol
    _chart_server_timeframe = timeframe
    
    # Start WebSocket broadcast loop
    _ws_broadcast_task = asyncio.create_task(websocket_broadcast_loop())
    
    logger.info(f"📡 Chart server started on http://127.0.0.1:{port}")
    logger.info(f"📡 WebSocket endpoint: ws://127.0.0.1:{port}/ws")
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
