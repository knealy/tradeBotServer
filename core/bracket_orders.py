"""
Bracket order and position-monitoring flows (extracted from trading_bot).
Calls TopStepXTradingBot for stop/trailing/OCO helpers that remain on the bot.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# ============================================================================
# NATIVE TOPSTEPX API METHODS - BRACKET ORDER SYSTEM
# ============================================================================

async def create_bracket_order_improved(bot, symbol: str, side: str, quantity: int,
                                       entry_stop_price: float, stop_loss_price: float,
                                       take_profit_price: float, account_id: str = None,
                                       strategy_name: Optional[str] = None) -> Dict:
    """
    Create an improved bracket order using stop order for entry, then modifying stop/take profit.
    This approach places a stop order for entry, then after fill, creates/modifies stop loss and take profit.
    
    Args:
        symbol: Trading symbol
        side: "BUY" or "SELL"
        quantity: Number of contracts
        entry_stop_price: Stop price for entry (triggers when price reaches this level)
        stop_loss_price: Stop loss price (after entry)
        take_profit_price: Take profit price (after entry)
        account_id: Account ID (uses selected account if not provided)
        strategy_name: Optional strategy name for custom tagging
        
    Returns:
        Dict: Bracket order response or error
    """
    try:
        target_account = account_id or (bot.selected_account['id'] if bot.selected_account else None)
        
        if not target_account:
            return {"error": "No account selected"}
        
        if not bot.session_token:
            return {"error": "No session token available. Please authenticate first."}
        
        if side.upper() not in ["BUY", "SELL"]:
            return {"error": "Side must be 'BUY' or 'SELL'"}
        
        logger.info(f"Creating improved bracket order: {side} {quantity} {symbol}")
        logger.info(f"Entry Stop: ${entry_stop_price}, Stop Loss: ${stop_loss_price}, Take Profit: ${take_profit_price}")
        
        # Step 1: Place stop order for entry
        entry_result = await bot.place_stop_order(
            symbol=symbol,
            side=side,
            quantity=quantity,
            stop_price=entry_stop_price,
            account_id=target_account,
            strategy_name=strategy_name
        )
        
        if "error" in entry_result:
            return {"error": f"Entry stop order failed: {entry_result['error']}"}
        
        entry_order_id = entry_result.get('orderId') or entry_result.get('id') or entry_result.get('order_id')
        logger.info(f"Entry stop order placed: {entry_order_id}")
        
        # Step 2: Monitor for fill, then attach stop loss and take profit
        # We'll use a background task to monitor the order fill
        import asyncio
        
        async def _attach_brackets_after_fill():
            """Monitor entry order and attach brackets after fill"""
            max_wait = 300  # 5 minutes max wait
            check_interval = 2  # Check every 2 seconds
            elapsed = 0
            
            while elapsed < max_wait:
                await asyncio.sleep(check_interval)
                elapsed += check_interval
                
                # Check if entry order is filled
                orders = await bot.get_open_orders(target_account)
                entry_filled = True
                for order in orders:
                    if str(order.get('id')) == str(entry_order_id):
                        entry_filled = False
                        break
                
                if entry_filled:
                    logger.info("Entry stop order filled, attaching stop loss and take profit")
                    
                    # Wait a moment for position to be established
                    await asyncio.sleep(1)
                    
                    # Get the position
                    positions = await bot.get_open_positions(target_account)
                    contract_id = bot._get_contract_id(symbol)
                    position_id = None
                    
                    for pos in positions:
                        if pos.get('contractId') == contract_id:
                            position_id = pos.get('id')
                            break
                    
                    if position_id:
                        # Modify stop loss and take profit using existing methods
                        try:
                            # Use modify_stop_loss and modify_take_profit if they exist
                            # Otherwise, create new orders
                            stop_result = await bot.modify_stop_loss(position_id, stop_loss_price)
                            tp_result = await bot.modify_take_profit(position_id, take_profit_price)
                            
                            logger.info(f"Brackets attached: Stop Loss={stop_result}, Take Profit={tp_result}")
                            return {"success": True, "position_id": position_id}
                        except Exception as e:
                            logger.error(f"Failed to attach brackets: {e}")
                            # Fallback: create new stop loss and take profit orders
                            try:
                                # Create stop loss order
                                stop_side = "SELL" if side.upper() == "BUY" else "BUY"
                                stop_result = await bot.place_stop_order(
                                    symbol=symbol,
                                    side=stop_side,
                                    quantity=quantity,
                                    stop_price=stop_loss_price,
                                    account_id=target_account,
                                    strategy_name=strategy_name
                                )
                                
                                # Create take profit limit order
                                tp_side = "SELL" if side.upper() == "BUY" else "BUY"
                                tp_result = await bot.place_market_order(
                                    symbol=symbol,
                                    side=tp_side,
                                    quantity=quantity,
                                    order_type="limit",
                                    limit_price=take_profit_price,
                                    account_id=target_account
                                )
                                
                                logger.info(f"Brackets created via orders: Stop={stop_result}, TP={tp_result}")
                                return {"success": True, "position_id": position_id}
                            except Exception as e2:
                                logger.error(f"Fallback bracket creation failed: {e2}")
                                return {"error": f"Failed to attach brackets: {e2}"}
                    else:
                        logger.warning("Position not found after entry fill")
                        return {"error": "Position not found after entry fill"}
            
            return {"error": "Entry order did not fill within timeout"}
        
        # Start monitoring task (fire and forget)
        asyncio.create_task(_attach_brackets_after_fill())
        
        return {
            "success": True,
            "entry_order_id": entry_order_id,
            "message": "Entry stop order placed. Brackets will be attached after fill.",
            "entry_stop_price": entry_stop_price,
            "stop_loss_price": stop_loss_price,
            "take_profit_price": take_profit_price
        }
        
    except Exception as e:
        logger.error(f"Failed to create improved bracket order: {str(e)}")
        return {"error": str(e)}

async def create_bracket_order(bot, symbol: str, side: str, quantity: int, 
                             stop_loss_price: float = None, take_profit_price: float = None,
                             stop_loss_ticks: int = None, take_profit_ticks: int = None,
                             account_id: str = None, strategy_name: str = None) -> Dict:
    """
    Create a native TopStepX bracket order with linked stop loss and take profit.
    
    Now uses TopStepXAdapter for bracket order creation, maintaining backward compatibility.
    
    **CENTRALIZED RISK MANAGEMENT**: When called from strategies (strategy_name provided),
    orders are automatically checked for position limits, cooldowns, and time restrictions.
    
    Args:
        symbol: Trading symbol (e.g., "ES", "NQ", "MNQ", "YM")
        side: "BUY" or "SELL"
        quantity: Number of contracts
        stop_loss_price: Stop loss price (optional if stop_loss_ticks provided)
        take_profit_price: Take profit price (optional if take_profit_price provided)
        stop_loss_ticks: Stop loss in ticks (optional if stop_loss_price provided)
        take_profit_ticks: Take profit in ticks (optional if take_profit_price provided)
        account_id: Account ID (uses selected account if not provided)
        strategy_name: Optional strategy name for tracking (triggers risk checks if provided)
        
    Returns:
        Dict: Bracket order response or error
    """
    try:
        target_account = account_id or (bot.selected_account['id'] if bot.selected_account else None)
        
        if not target_account:
            return {"error": "No account selected"}
        
        if side.upper() not in ["BUY", "SELL"]:
            return {"error": "Side must be 'BUY' or 'SELL'"}
        
        # CENTRALIZED RISK MANAGEMENT: Check if order is allowed (when called from strategies)
        if strategy_name:
            if bot._strategy_risk_manager is None:
                from core.risk_management import StrategyRiskManager
                bot._strategy_risk_manager = StrategyRiskManager(bot)
            
            allowed, reason = await bot._strategy_risk_manager.check_order_allowed(
                symbol=symbol,
                side=side,
                quantity=quantity
            )
            
            if not allowed:
                error_msg = f"Risk management blocked order: {reason}"
                logger.warning(f"⚠️  Strategy {strategy_name}: {error_msg}")
                return {
                    "success": False,
                    "error": error_msg,
                    "orderId": None
                }
        
        # Use TopStepXAdapter for bracket order creation
        result = await bot.broker_adapter.create_bracket_order(
            symbol=symbol,
            side=side,
            quantity=quantity,
            stop_loss_price=stop_loss_price,
            take_profit_price=take_profit_price,
            stop_loss_ticks=stop_loss_ticks,
            take_profit_ticks=take_profit_ticks,
            account_id=target_account,
            strategy_name=strategy_name
        )
        
        # Convert OrderResponse to dict for backward compatibility
        if result.success:
            # Record successful order placement for cooldown tracking (when from strategy)
            if strategy_name and bot._strategy_risk_manager:
                await bot._strategy_risk_manager.record_order_placement(symbol, side)
            
            return {
                "success": True,
                "orderId": result.order_id,
                "message": result.message,
                **({"raw_response": result.raw_response} if result.raw_response else {})
            }
        else:
            return {"error": result.error}
        
    except Exception as e:
        logger.error(f"Failed to create bracket order: {str(e)}")
        return {"error": str(e)}

async def create_partial_tp_bracket_order(bot, symbol: str, side: str, quantity: int,
                                         stop_loss_price: float = None, take_profit_1_price: float = None,
                                         take_profit_2_price: float = None, tp1_quantity: int = None,
                                         account_id: str = None) -> Dict:
    """
    Create a bracket order with partial TP1 and full TP2 exits.
    This places the entry order, then creates separate TP1 and TP2 orders.
    
    Args:
        symbol: Trading symbol
        side: "BUY" or "SELL"
        quantity: Total number of contracts
        stop_loss_price: Stop loss price
        take_profit_1_price: TP1 price (partial exit)
        take_profit_2_price: TP2 price (full exit)
        tp1_quantity: Number of contracts for TP1 (default: 1)
        account_id: Account ID (uses selected account if not provided)
        
    Returns:
        Dict: Bracket order response or error
    """
    try:
        target_account = account_id or (bot.selected_account['id'] if bot.selected_account else None)
        
        if not target_account:
            return {"error": "No account selected"}
        
        if not bot.session_token:
            return {"error": "No session token available. Please authenticate first."}
        
        if side.upper() not in ["BUY", "SELL"]:
            return {"error": "Side must be 'BUY' or 'SELL'"}
        
        if tp1_quantity is None:
            tp1_quantity = 1  # Default to 1 contract for TP1
        
        if tp1_quantity >= quantity:
            return {"error": "TP1 quantity must be less than total quantity"}
        
        logger.info(f"Creating partial TP bracket order for {side} {quantity} {symbol} on account {target_account}")
        logger.info(f"TP1: {tp1_quantity} contracts at {take_profit_1_price}, TP2: {quantity} contracts at {take_profit_2_price}")
        
        # First, place the entry order
        entry_result = await bot.place_market_order(
            symbol=symbol,
            side=side,
            quantity=quantity,
            account_id=target_account
        )
        
        if "error" in entry_result:
            return {"error": f"Entry order failed: {entry_result['error']}"}
        
        logger.info(f"Entry order placed successfully: {entry_result}")
        
        # Wait a moment for the position to be established
        await asyncio.sleep(1)
        
        # Get the position ID for the new position
        positions = await bot.get_open_positions(target_account)
        position_id = None
        try:
            symbol_contract_id = bot._get_contract_id(symbol)
            for pos in positions:
                if pos.get('contractId') == symbol_contract_id:
                    position_id = pos.get('id')
                    break
        except ValueError as e:
            logger.error(f"❌ Cannot find position: {e}. Please fetch contracts first.")
            return {"error": str(e)}
        
        if not position_id:
            return {"error": "Could not find position after entry order"}
        
        # Create stop loss order
        stop_result = None
        if stop_loss_price:
            # Round stop loss price to valid tick size
            tick_size = await bot._get_tick_size(symbol)
            rounded_stop_price = bot._round_to_tick_size(stop_loss_price, tick_size)
            logger.info(f"Stop loss price: {stop_loss_price} -> {rounded_stop_price} (tick_size: {tick_size})")
            
            stop_side = "SELL" if side.upper() == "BUY" else "BUY"
            stop_result = await bot.place_stop_order(
                symbol=symbol,
                side=stop_side,
                quantity=quantity,
                stop_price=rounded_stop_price,
                account_id=target_account,
                strategy_name=None  # This function doesn't have strategy_name parameter
            )
            if "error" in stop_result:
                logger.warning(f"Stop loss order failed: {stop_result['error']}")
            else:
                logger.info(f"Stop loss order placed: {stop_result}")
        
        # FIXED: Create TP1 order (partial exit) using proper limit order
        tp1_result = None
        if take_profit_1_price:
            # Round TP1 price to valid tick size
            tick_size = await bot._get_tick_size(symbol)
            rounded_tp1_price = bot._round_to_tick_size(take_profit_1_price, tick_size)
            logger.info(f"TP1 price: {take_profit_1_price} -> {rounded_tp1_price} (tick_size: {tick_size})")
            
            tp1_side = "SELL" if side.upper() == "BUY" else "BUY"
            tp1_result = await bot.place_market_order(
                symbol=symbol,
                side=tp1_side,
                quantity=tp1_quantity,
                order_type="limit",
                limit_price=rounded_tp1_price,
                account_id=target_account
            )
            if "error" in tp1_result:
                logger.warning(f"TP1 order failed: {tp1_result['error']}")
            else:
                logger.info(f"TP1 limit order placed: {tp1_result}")
        
        # FIXED: Create TP2 order (remaining position exit) using proper limit order
        tp2_result = None
        if take_profit_2_price:
            # Round TP2 price to valid tick size
            tick_size = await bot._get_tick_size(symbol)
            rounded_tp2_price = bot._round_to_tick_size(take_profit_2_price, tick_size)
            logger.info(f"TP2 price: {take_profit_2_price} -> {rounded_tp2_price} (tick_size: {tick_size})")
            
            tp2_side = "SELL" if side.upper() == "BUY" else "BUY"
            tp2_quantity = quantity - tp1_quantity  # Remaining contracts after TP1
            if tp2_quantity > 0:
                tp2_result = await bot.place_market_order(
                    symbol=symbol,
                    side=tp2_side,
                    quantity=tp2_quantity,
                    order_type="limit",
                    limit_price=rounded_tp2_price,
                    account_id=target_account
                )
                if "error" in tp2_result:
                    logger.warning(f"TP2 order failed: {tp2_result['error']}")
                else:
                    logger.info(f"TP2 limit order placed: {tp2_result}")
            else:
                logger.info("No TP2 order needed (TP1 covers entire position)")
        else:
            logger.info("No TP2 order created (full exit at TP1)")
        
        # Create appropriate message based on TP2 presence
        if take_profit_2_price and tp2_quantity > 0:
            message = f"Staged TP bracket created: {tp1_quantity}@TP1, {tp2_quantity}@TP2"
        else:
            message = f"Full TP1 exit created: {tp1_quantity}@TP1 (no TP2)"
        
        # Start position monitoring for this bracket order
        if position_id:
            await _start_bracket_monitoring(
                bot,
                position_id, symbol, target_account,
                side=side, stop_loss_price=stop_loss_price, take_profit_price=take_profit_1_price
            )
        
        return {
            "success": True,
            "entry_order": entry_result,
            "stop_order": stop_result,
            "tp1_order": tp1_result,
            "tp2_order": tp2_result,
            "position_id": position_id,
            "message": message
        }
        
    except Exception as e:
        logger.error(f"Failed to create partial TP bracket order: {str(e)}")
        return {"error": str(e)}

async def _start_bracket_monitoring(bot, position_id: str, symbol: str, account_id: str, 
                                  side: str = None, stop_loss_price: float = None, take_profit_price: float = None) -> None:
    """
    Start monitoring a position for bracket order management.
    This ensures orders are adjusted when position size changes.
    """
    try:
        # Store monitoring info for this position
        if not hasattr(bot, '_bracket_monitoring'):
            bot._bracket_monitoring = {}
        
        bot._bracket_monitoring[position_id] = {
            'symbol': symbol,
            'account_id': account_id,
            'side': side,  # Original trade direction
            'stop_loss_price': stop_loss_price,  # Original stop loss price
            'take_profit_price': take_profit_price,  # Original take profit price
            'original_quantity': None,  # Will be set when we first check
            'last_check': None,
            'active': True
        }
        
        logger.info(f"Started bracket monitoring for position {position_id} ({symbol}) - Side: {side}, SL: {stop_loss_price}, TP: {take_profit_price}")
        
    except Exception as e:
        logger.error(f"Failed to start bracket monitoring: {str(e)}")

async def _manage_bracket_orders(bot, position_id: str) -> Dict:
    """
    Manage bracket orders for a specific position.
    Adjusts orders when position size changes and cancels conflicting orders.
    """
    try:
        if not hasattr(bot, '_bracket_monitoring') or position_id not in bot._bracket_monitoring:
            return {"error": "Position not being monitored"}
        
        monitoring_info = bot._bracket_monitoring[position_id]
        if not monitoring_info['active']:
            return {"message": "Monitoring stopped for this position"}
        
        symbol = monitoring_info['symbol']
        account_id = monitoring_info['account_id']
        
        # Get current position
        positions = await bot.get_open_positions(account_id)
        current_position = None
        for pos in positions:
            if str(pos.get('id', '')) == str(position_id):
                current_position = pos
                break
        
        if not current_position:
            logger.info(f"Position {position_id} no longer exists, stopping monitoring")
            monitoring_info['active'] = False
            return {"message": "Position closed, monitoring stopped"}
        
        current_quantity = current_position.get('size', 0)
        original_quantity = monitoring_info.get('original_quantity')
        
        # Set original quantity on first check
        if original_quantity is None:
            monitoring_info['original_quantity'] = current_quantity
            original_quantity = current_quantity
            logger.info(f"Set original quantity for position {position_id}: {original_quantity}")
        
        # Check if position size has changed
        if current_quantity == original_quantity:
            return {"message": "Position size unchanged"}
        
        logger.info(f"Position {position_id} size changed: {original_quantity} → {current_quantity}")
        
        # Get all open orders for this symbol (using individual call since we only need orders)
        orders = await bot.get_open_orders(account_id)
        symbol_orders = []
        try:
            symbol_contract_id = bot._get_contract_id(symbol)
            for order in orders:
                order_contract = order.get('contractId', '')
                if order_contract == symbol_contract_id:
                    symbol_orders.append(order)
        except ValueError as e:
            logger.error(f"❌ Cannot filter orders: {e}. Please fetch contracts first.")
            return {"error": str(e)}
        
        # Cancel all existing TP and SL orders for this symbol
        canceled_orders = []
        for order in symbol_orders:
            order_id = order.get('id')
            order_type = order.get('type', 0)
            custom_tag = order.get('customTag', '')
            
            # Cancel stop loss and take profit orders
            if (order_type in [1, 4] or  # Limit or Stop orders
                'AutoBracket' in custom_tag or
                '-SL' in custom_tag or '-TP' in custom_tag):
                
                cancel_result = await bot.cancel_order(order_id, account_id)
                if "error" not in cancel_result:
                    canceled_orders.append(order_id)
                    logger.info(f"Canceled order {order_id} (type: {order_type}, tag: {custom_tag})")
                else:
                    logger.warning(f"Failed to cancel order {order_id}: {cancel_result.get('error')}")
        
        # If position is still open, create new orders based on remaining quantity
        if current_quantity > 0:
            # CRITICAL: All positions must have protection - never leave positions unhedged
            logger.warning(f"Position {position_id} has {current_quantity} contracts remaining - creating new protection orders")
            
            # Get the original trade parameters from monitoring info
            original_side = monitoring_info.get('side', 'BUY')
            original_stop_loss = monitoring_info.get('stop_loss_price')
            original_take_profit = monitoring_info.get('take_profit_price')
            
            if original_stop_loss and original_take_profit:
                # Create new bracket order for remaining position
                new_side = "SELL" if original_side == "BUY" else "BUY"
                
                logger.info(f"Creating new protection for {current_quantity} contracts: {new_side} with SL={original_stop_loss}, TP={original_take_profit}")
                
                new_bracket_result = await create_bracket_order(
                    bot,
                    symbol=symbol,
                    side=new_side,
                    quantity=current_quantity,
                    stop_loss_price=original_stop_loss,
                    take_profit_price=original_take_profit,
                    account_id=account_id
                )
                
                if "error" in new_bracket_result:
                    logger.error(f"Failed to create new protection orders: {new_bracket_result['error']}")
                    logger.error("⚠️ POSITION IS UNPROTECTED - MANUAL INTERVENTION REQUIRED")
                else:
                    logger.info(f"Successfully created new protection orders: {new_bracket_result}")
                    # Update monitoring info with new order details
                    monitoring_info['original_quantity'] = current_quantity
            else:
                logger.error(f"Cannot create protection orders - missing original parameters for position {position_id}")
                logger.error("⚠️ POSITION IS UNPROTECTED - MANUAL INTERVENTION REQUIRED")
        else:
            logger.info(f"Position {position_id} fully closed - all orders canceled")
            monitoring_info['active'] = False
        
        return {
            "success": True,
            "position_quantity": current_quantity,
            "canceled_orders": canceled_orders,
            "monitoring_active": monitoring_info['active']
        }
        
    except Exception as e:
        logger.error(f"Failed to manage bracket orders: {str(e)}")
        return {"error": str(e)}

async def monitor_all_bracket_positions(bot, account_id: str = None) -> Dict:
    """
    Monitor all positions with active bracket orders.
    This should be called periodically to manage order adjustments.
    """
    try:
        target_account = account_id or (bot.selected_account['id'] if bot.selected_account else None)
        
        if not target_account:
            return {"error": "No account selected"}
        
        # First, check for any unprotected positions
        await _check_unprotected_positions(bot, target_account)
        
        if not hasattr(bot, '_bracket_monitoring'):
            return {"message": "No positions being monitored"}
        
        results = {}
        positions_to_remove = []
        
        for position_id, monitoring_info in bot._bracket_monitoring.items():
            if not monitoring_info['active']:
                positions_to_remove.append(position_id)
                continue
            
            result = await _manage_bracket_orders(bot, position_id)
            results[position_id] = result
            
            # If monitoring stopped, mark for removal
            if not monitoring_info['active']:
                positions_to_remove.append(position_id)
        
        # Clean up stopped monitoring
        for position_id in positions_to_remove:
            del bot._bracket_monitoring[position_id]
            logger.info(f"Removed monitoring for position {position_id}")
        
        return {
            "success": True,
            "monitored_positions": len(bot._bracket_monitoring),
            "results": results,
            "removed_positions": len(positions_to_remove)
        }
        
    except Exception as e:
        logger.error(f"Failed to monitor bracket positions: {str(e)}")
        return {"error": str(e)}

async def _check_unprotected_positions(bot, account_id: str) -> None:
    """
    Check for positions that don't have proper stop/target protection.
    This is a safety mechanism to prevent orphaned positions.
    """
    try:
        # Use batch API call for efficiency (reduces round-trips by 50%)
        batch_result = await bot.get_positions_and_orders_batch(account_id)
        positions = batch_result.get("positions", [])
        orders = batch_result.get("orders", [])
        
        if not positions:
            return
        
        for position in positions:
            position_id = str(position.get('id'))
            symbol = position.get('contractId', '')
            size = position.get('size', 0)
            
            if size == 0:
                continue
            
            # Check if this position has any protective orders
            has_protection = False
            for order in orders:
                order_contract = order.get('contractId', '')
                if order_contract == symbol:
                    order_type = order.get('type', 0)
                    custom_tag = order.get('customTag', '')
                    
                    # Check for stop loss or take profit orders
                    if (order_type in [1, 4] or  # Limit or Stop orders
                        'AutoBracket' in custom_tag or
                        '-SL' in custom_tag or '-TP' in custom_tag):
                        has_protection = True
                        break
            
            if not has_protection:
                logger.error(f"⚠️ UNPROTECTED POSITION DETECTED: {position_id} - {symbol} size {size}")
                logger.error("This position has no stop loss or take profit orders!")
                logger.error("Manual intervention required to add protection")
                
                # If this position is not being monitored, start monitoring it
                if not hasattr(bot, '_bracket_monitoring') or position_id not in bot._bracket_monitoring:
                    logger.warning(f"Starting emergency monitoring for unprotected position {position_id}")
                    # We can't start proper monitoring without original trade parameters
                    # But we can at least track it
                    if not hasattr(bot, '_bracket_monitoring'):
                        bot._bracket_monitoring = {}
                    
                    bot._bracket_monitoring[position_id] = {
                        'symbol': symbol,
                        'account_id': account_id,
                        'side': 'UNKNOWN',  # We don't know the original direction
                        'stop_loss_price': None,  # We don't have original parameters
                        'take_profit_price': None,
                        'original_quantity': size,
                        'last_check': None,
                        'active': True,
                        'emergency': True  # Mark as emergency monitoring
                    }
                    logger.warning(f"Emergency monitoring started for position {position_id}")
        
    except Exception as e:
        logger.error(f"Failed to check unprotected positions: {str(e)}")

async def get_linked_orders(bot, position_id: str, account_id: str = None) -> List[Dict]:
    """
    Get all orders linked to a specific position.
    
    Args:
        position_id: Position ID
        account_id: Account ID (uses selected account if not provided)
        
    Returns:
        List[Dict]: List of linked orders
    """
    try:
        target_account = account_id or (bot.selected_account['id'] if bot.selected_account else None)
        
        if not target_account:
            return {"error": "No account selected"}
        
        if not bot.session_token:
            return {"error": "No session token available. Please authenticate first."}
        
        logger.info(f"Fetching linked orders for position {position_id}")
        
        headers = {
            "accept": "text/plain",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {bot.session_token}"
        }
        
        # Use the official TopStepX Gateway API for orders
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        start_time = now.replace(hour=0, minute=0, second=0, microsecond=0)
        
        search_data = {
            "accountId": int(target_account),
            "startTimestamp": start_time.isoformat(),
            "endTimestamp": now.isoformat(),
            "request": {
                "accountId": int(target_account),
                "status": "Open"
            }
        }
        
        logger.info(f"Requesting linked orders for position {position_id} using TopStepX Gateway API")
        logger.info(f"Request data: {search_data}")
        
        # Call the official TopStepX Gateway API
        response = await bot._make_http_request("POST", "/api/Order/search", data=search_data, headers=headers)
        
        if "error" in response:
            logger.error(f"TopStepX Gateway API failed: {response['error']}")
            return []
        
        if not response.get("success"):
            logger.error(f"TopStepX Gateway API returned error: {response}")
            return []
        
        # Check for different possible order data fields
        orders = []
        for field in ["orders", "data", "result", "items", "list"]:
            if field in response and isinstance(response[field], list):
                orders = response[field]
                break
        
        # Filter orders that are linked to this position
        # Since we don't have direct position linking, we'll find orders for the same contract
        # that are likely bracket orders (stop loss and take profit)
        linked_orders = []
        position_contract = None
        
        # Get the contract ID for the position
        positions = await bot.get_open_positions(target_account)
        for pos in positions:
            if str(pos.get('id', '')) == str(position_id):
                position_contract = pos.get('contractId')
                break
        
        if not position_contract:
            logger.error(f"Could not find contract for position {position_id}")
            return []
        
        for order in orders:
            order_contract = order.get('contractId', '')
            order_status = order.get('status', 0)
            custom_tag = order.get('customTag', '') or ''  # Ensure it's never None
            order_type = order.get('type', 0)
            
            # Only process open orders for the same contract
            if order_contract == position_contract and order_status == 1:  # Status 1 = Open
                # Check for bracket orders using customTag
                if custom_tag and "AutoBracket" in custom_tag:
                    if "-SL" in custom_tag or "-TP" in custom_tag:
                        linked_orders.append(order)
                        logger.info(f"Found bracket order: {order.get('id')} tag: {custom_tag}")
                # Also check by order type (4 = Stop orders, 1 = Limit orders)
                elif order_type == 4:  # Stop orders
                    linked_orders.append(order)
                    logger.info(f"Found stop order: {order.get('id')} type: {order_type}")
                elif order_type == 1:  # Limit orders that might be take profit
                    # Only consider it a take profit if it's actually linked via customTag
                    # OR if it's in the correct direction relative to position entry price
                    custom_tag = order.get('customTag', '') or ''
                    if "AutoBracket" in custom_tag and ("-TP" in custom_tag or "TP" in custom_tag):
                        linked_orders.append(order)
                        logger.info(f"Found take profit order (via bracket tag): {order.get('id')} type: {order_type}")
                    else:
                        # Only link if we can verify it's actually a TP by checking position entry price
                        # This requires position data, so we'll be conservative and only link if explicitly tagged
                        # For now, skip linking standalone limit orders unless they're bracket-linked
                        pass
        
        logger.info(f"Found {len(linked_orders)} linked orders for position {position_id}")
        return linked_orders
        
    except Exception as e:
        logger.error(f"Failed to fetch linked orders: {str(e)}")
        return []

async def adjust_bracket_orders(bot, position_id: str, new_quantity: int, 
                               account_id: str = None) -> Dict:
    """
    Adjust bracket orders when position size changes.
    This method automatically finds and adjusts all linked stop loss and take profit orders.
    
    Args:
        position_id: Position ID
        new_quantity: New total quantity for the position
        account_id: Account ID (uses selected account if not provided)
        
    Returns:
        Dict: Adjustment response or error
    """
    try:
        target_account = account_id or (bot.selected_account['id'] if bot.selected_account else None)
        
        if not target_account:
            return {"error": "No account selected"}
        
        if not bot.session_token:
            return {"error": "No session token available. Please authenticate first."}
        
        logger.info(f"Adjusting bracket orders for position {position_id} to quantity {new_quantity}")
        
        # Get current open orders to find linked ones
        open_orders = await bot.get_open_orders(target_account)
        
        if not open_orders:
            logger.warning("No open orders found")
            return {"error": "No open orders found"}
        
        # Find orders that are linked to this position
        # Look for bracket orders using customTag and contract matching
        linked_orders = []
        position_contract = None
        
        # Get the contract ID for the position
        positions = await bot.get_open_positions(target_account)
        for pos in positions:
            if str(pos.get('id', '')) == str(position_id):
                position_contract = pos.get('contractId')
                break
        
        if not position_contract:
            logger.error(f"Could not find contract for position {position_id}")
            return {"error": f"Could not find contract for position {position_id}"}
        
        for order in open_orders:
            order_contract = order.get('contractId', '')
            custom_tag = order.get('customTag', '')
            order_type = order.get('type', 0)
            order_status = order.get('status', 0)
            order_side = order.get('side', -1)
            
            # Only process open orders for the same contract
            if order_contract == position_contract and order_status == 1:  # Status 1 = Open
                # Check for bracket orders using customTag
                if "AutoBracket" in custom_tag:
                    if "-SL" in custom_tag or "-TP" in custom_tag:
                        linked_orders.append(order)
                        logger.info(f"Found bracket order: {order.get('id')} tag: {custom_tag}")
                # Also check by order type (4 = Stop orders, 1 = Limit orders)
                elif order_type == 4:  # Stop orders
                    linked_orders.append(order)
                    logger.info(f"Found stop order: {order.get('id')} type: {order_type}")
                elif order_type == 1:  # Limit orders that might be take profit
                    # Check if this is a take profit order (opposite side from position)
                    # For long positions, take profit should be SELL (side=1)
                    # For short positions, take profit should be BUY (side=0)
                    linked_orders.append(order)
                    logger.info(f"Found limit order (potential TP): {order.get('id')} type: {order_type} side: {order_side}")
                # Also check for any orders with our custom tag prefix
                elif "TradingBot-v1.0" in custom_tag:
                    linked_orders.append(order)
                    logger.info(f"Found bot order: {order.get('id')} tag: {custom_tag}")
        
        if not linked_orders:
            logger.warning("No linked orders found for position")
            return {"error": "No linked orders found for position"}
        
        # Adjust each linked order
        adjustment_results = []
        for order in linked_orders:
            order_id = order.get("id")
            current_quantity = order.get("size", 0)
            custom_tag = order.get("customTag", "")
            
            if order_id and current_quantity != new_quantity:
                try:
                    logger.info(f"Adjusting order {order_id} from {current_quantity} to {new_quantity} (tag: {custom_tag})")
                    # Get order type for proper price field handling
                    order_type = order.get("type", 1)  # Default to limit order
                    # Modify the order with new quantity
                    modify_result = await bot.modify_order(order_id, new_quantity=new_quantity, account_id=target_account, order_type=order_type)
                    if "error" not in modify_result:
                        adjustment_results.append({"order_id": order_id, "success": True, "result": modify_result})
                        logger.info(f"Successfully adjusted order {order_id} to quantity {new_quantity}")
                    else:
                        adjustment_results.append({"order_id": order_id, "success": False, "error": modify_result["error"]})
                        logger.error(f"Failed to adjust order {order_id}: {modify_result['error']}")
                except Exception as e:
                    logger.error(f"Exception adjusting order {order_id}: {e}")
                    adjustment_results.append({"order_id": order_id, "success": False, "error": str(e)})
            else:
                logger.info(f"Order {order_id} already has correct quantity {current_quantity}, skipping")
        
        successful_adjustments = [r for r in adjustment_results if r.get("success")]
        logger.info(f"Adjusted {len(successful_adjustments)} out of {len(adjustment_results)} linked orders")
        
        return {
            "success": True, 
            "adjusted_orders": len(successful_adjustments),
            "total_orders": len(adjustment_results),
            "results": adjustment_results
        }
        
    except Exception as e:
        logger.error(f"Failed to adjust bracket orders: {str(e)}")
        return {"error": str(e)}

async def monitor_position_changes(bot, account_id: str = None) -> Dict:
    """
    Monitor position changes and automatically adjust bracket orders.
    This method should be called periodically to track position size changes.
    Only monitors if a market order was recently placed to avoid interfering with concurrent bracket orders.
    
    Args:
        account_id: Account ID (uses selected account if not provided)
        
    Returns:
        Dict: Monitoring results
    """
    try:
        target_account = account_id or (bot.selected_account['id'] if bot.selected_account else None)
        
        if not target_account:
            return {"error": "No account selected"}
        
        # Check if monitoring should be active
        if not bot._monitoring_active:
            logger.info("Monitoring not active - no recent market orders placed")
            return {"positions": 0, "adjustments": 0, "message": "Monitoring not active - no recent market orders"}
        
        # Check if monitoring should timeout (after 30 seconds)
        if bot._last_order_time:
            time_since_order = (datetime.now() - bot._last_order_time).total_seconds()
            if time_since_order > 30:  # 30 seconds
                bot._monitoring_active = False
                logger.info("Monitoring deactivated - timeout after 30 seconds")
                return {"positions": 0, "adjustments": 0, "message": "Monitoring timeout - no recent market orders"}
        
        logger.info(f"Monitoring position changes for account {target_account}")
        
        # Get current positions
        positions = await bot.get_open_positions(target_account)
        
        if not positions:
            logger.info("No open positions found - checking for orphaned orders")
            
            # Get all open orders
            orders = await bot.get_open_orders(target_account)
            if orders:
                logger.info(f"Found {len(orders)} open orders with no positions - these may be orphaned")
                
                # Cancel all open orders since there are no positions
                cancelled_orders = 0
                for order in orders:
                    order_id = order.get("id")
                    if order_id:
                        try:
                            cancel_result = await bot.cancel_order(order_id, target_account)
                            if "error" not in cancel_result:
                                cancelled_orders += 1
                                logger.info(f"Cancelled orphaned order {order_id}")
                            else:
                                logger.warning(f"Failed to cancel order {order_id}: {cancel_result['error']}")
                        except Exception as e:
                            logger.error(f"Exception cancelling order {order_id}: {e}")
                
                if cancelled_orders > 0:
                    logger.info(f"Cancelled {cancelled_orders} orphaned orders")
                    return {"positions": 0, "adjustments": 0, "cancelled_orders": cancelled_orders}
            
            return {"positions": 0, "adjustments": 0}
        
        adjustments_made = 0
        
        for position in positions:
            position_id = position.get("id")
            current_quantity = position.get("size", 0)  # Use 'size' field for position quantity
            symbol = position.get("symbol", "Unknown")
            
            logger.info(f"Checking position {position_id}: {current_quantity} {symbol}")
            
            # Check if we have tracked this position before
            # In a real implementation, you'd store the previous quantity
            # For now, we'll just log the current state
            
            # Get linked orders for this position
            linked_orders = await get_linked_orders(bot, position_id, target_account)
            
            if linked_orders:
                logger.info(f"Found {len(linked_orders)} linked orders for position {position_id}")
                
                # Check if any linked orders need quantity adjustment
                # Only adjust if position quantity is greater than 0
                if current_quantity > 0:
                    for order in linked_orders:
                        order_quantity = order.get("size", 0)  # Use 'size' field for order quantity
                        if order_quantity != current_quantity:
                            logger.info(f"Order {order.get('id')} quantity {order_quantity} != position quantity {current_quantity}")
                            
                            # Adjust the order quantity to match position
                            adjust_result = await adjust_bracket_orders(bot, position_id, current_quantity, target_account)
                            if adjust_result.get("success"):
                                adjustments_made += 1
                                logger.info(f"Successfully adjusted bracket orders for position {position_id}")
                            else:
                                logger.error(f"Failed to adjust bracket orders for position {position_id}: {adjust_result.get('error')}")
                            break  # Only adjust once per position
                else:
                    logger.info(f"Position {position_id} has zero quantity, skipping bracket order adjustments")
        
        return {
            "success": True,
            "positions": len(positions),
            "adjustments": adjustments_made
        }
        
    except Exception as e:
        logger.error(f"Failed to monitor position changes: {str(e)}")
        return {"error": str(e)}
