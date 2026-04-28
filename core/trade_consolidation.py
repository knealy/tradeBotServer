"""FIFO order consolidation and trade statistics."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Dict, List

def consolidate_orders_into_trades(
    orders: List[Dict],
    get_point_value,
    calculate_commission,
    *,
    log: logging.Logger,
) -> List[Dict]:
    """
    Consolidate individual filled orders into completed trades using FIFO methodology.
    Matches entry and exit orders to calculate P&L for each trade.
    
    Args:
        orders: List of filled order dictionaries
        
    Returns:
        List[Dict]: List of consolidated trades with entry/exit info and P&L
    """
    if not orders:
        return []
    
    # Group orders by symbol
    by_symbol = {}
    for order in orders:
        contract_id = order.get('contractId', '')
        if contract_id:
            symbol = contract_id.split('.')[-2] if '.' in contract_id else contract_id
        else:
            symbol = order.get('symbol', 'UNKNOWN')
        
        if symbol not in by_symbol:
            by_symbol[symbol] = []
        by_symbol[symbol].append(order)
    
    # Sort orders by execution timestamp for each symbol
    for symbol in by_symbol:
        by_symbol[symbol].sort(key=lambda x: x.get('executionTimestamp') or x.get('creationTimestamp') or '')
    
    # Process each symbol's orders using FIFO to match entries and exits
    consolidated_trades = []
    
    for symbol, symbol_orders in by_symbol.items():
        # Track open positions using a queue (FIFO)
        open_positions = []  # List of (entry_order, remaining_quantity)
        
        for order in symbol_orders:
            side = order.get('side', -1)  # 0=BUY, 1=SELL
            quantity = order.get('size', 0)
            # Try multiple field names for price (API returns different fields)
            # Note: 'filledPrice' is the correct field from TopStepX API for filled orders
            price = (order.get('filledPrice') or  # ← Primary field for filled orders
                    order.get('executionPrice') or 
                    order.get('averagePrice') or 
                    order.get('price') or 
                    order.get('limitPrice') or 
                    order.get('stopPrice') or 0.0)
            timestamp_raw = order.get('executionTimestamp') or order.get('creationTimestamp') or ''
            
            # Convert timestamp to datetime if it's a string, otherwise keep as-is
            from datetime import datetime, timezone
            if isinstance(timestamp_raw, str) and timestamp_raw:
                try:
                    # Try parsing ISO format timestamp
                    timestamp = datetime.fromisoformat(timestamp_raw.replace('Z', '+00:00'))
                except (ValueError, AttributeError):
                    # If parsing fails, keep as string but log warning
                    log.warning(f"Could not parse timestamp '{timestamp_raw}', keeping as string")
                    timestamp = timestamp_raw
            elif isinstance(timestamp_raw, datetime):
                timestamp = timestamp_raw
            else:
                # Fallback to current time if no timestamp
                timestamp = datetime.now(timezone.utc)
            
            # Debug logging for order details (only if verbose logging enabled)
            if log.level <= logging.DEBUG:
                log.debug(f"Processing order: side={side}, qty={quantity}, price={price}, timestamp={timestamp}")
            
            # Log warning if price is still 0 after all attempts
            if price == 0.0 or price is None:
                log.warning(f"⚠️  Order {order.get('id')} has no price in any expected field. Available fields: {list(order.keys())}")
            
            if side == 0:  # BUY
                # First, try to close short positions
                remaining_qty = quantity
                
                while remaining_qty > 0 and open_positions and open_positions[0]['side'] == 'SHORT':
                    position = open_positions[0]
                    closed_qty = min(remaining_qty, position['remaining_qty'])
                    
                    # Calculate P&L for closing short: profit when buy price < sell price
                    entry_price = position['entry_price']
                    exit_price = price
                    point_value = get_point_value(symbol)
                    gross_pnl = (entry_price - exit_price) * closed_qty * point_value  # Reversed for short
                    # Subtract commission (round trip: open + close)
                    commission = calculate_commission(closed_qty)
                    pnl = gross_pnl - commission
                    
                    # Extract strategy from entry order's custom tag
                    entry_order = position['entry_order']
                    strategy_name = None
                    custom_tag = entry_order.get('customTag') or entry_order.get('custom_tag')
                    if custom_tag and '-strategy-' in str(custom_tag):
                        try:
                            # Format: TradingBot-v1.0-strategy-{strategy_name}-{order_type}-...
                            parts = str(custom_tag).split('-strategy-')
                            if len(parts) >= 2:
                                strategy_name = parts[1].split('-')[0]
                        except Exception:
                            pass
                    
                    # Create consolidated trade
                    trade = {
                        'symbol': symbol,
                        'side': 'SHORT',
                        'quantity': closed_qty,
                        'entry_price': entry_price,
                        'exit_price': exit_price,
                        'entry_time': position['entry_time'],
                        'exit_time': timestamp,
                        'pnl': pnl,
                        'entry_order_id': position['entry_order'].get('id'),
                        'exit_order_id': order.get('id'),
                        'strategy': strategy_name  # Add strategy name from custom tag
                    }
                    consolidated_trades.append(trade)
                    log.debug(f"Created SHORT trade: {closed_qty} @ ${entry_price:.2f} → ${exit_price:.2f}, P&L: ${pnl:.2f}")
                    
                    # Update remaining quantities
                    position['remaining_qty'] -= closed_qty
                    remaining_qty -= closed_qty
                    
                    # Remove position if fully closed
                    if position['remaining_qty'] <= 0:
                        open_positions.pop(0)
                
                # If there's still quantity remaining after closing shorts, it's a new long position
                if remaining_qty > 0:
                    open_positions.append({
                        'entry_order': order,
                        'entry_price': price,
                        'entry_time': timestamp,
                        'remaining_qty': remaining_qty,
                        'side': 'LONG'
                    })
                    log.debug(f"Opened LONG position: {remaining_qty} @ ${price:.2f}")
                    
            elif side == 1:  # SELL
                # First, try to close long positions
                remaining_qty = quantity
                
                while remaining_qty > 0 and open_positions and open_positions[0]['side'] == 'LONG':
                    position = open_positions[0]
                    closed_qty = min(remaining_qty, position['remaining_qty'])
                    
                    # Calculate P&L for closing long: profit when sell price > buy price
                    entry_price = position['entry_price']
                    exit_price = price
                    point_value = get_point_value(symbol)
                    gross_pnl = (exit_price - entry_price) * closed_qty * point_value
                    # Subtract commission (round trip: open + close)
                    commission = calculate_commission(closed_qty)
                    pnl = gross_pnl - commission
                    
                    # Extract strategy from entry order's custom tag
                    entry_order = position['entry_order']
                    strategy_name = None
                    custom_tag = entry_order.get('customTag') or entry_order.get('custom_tag')
                    if custom_tag and '-strategy-' in str(custom_tag):
                        try:
                            # Format: TradingBot-v1.0-strategy-{strategy_name}-{order_type}-...
                            parts = str(custom_tag).split('-strategy-')
                            if len(parts) >= 2:
                                strategy_name = parts[1].split('-')[0]
                        except Exception:
                            pass
                    
                    # Create consolidated trade
                    trade = {
                        'symbol': symbol,
                        'side': 'LONG',
                        'quantity': closed_qty,
                        'entry_price': entry_price,
                        'exit_price': exit_price,
                        'entry_time': position['entry_time'],
                        'exit_time': timestamp,
                        'pnl': pnl,
                        'entry_order_id': position['entry_order'].get('id'),
                        'exit_order_id': order.get('id'),
                        'strategy': strategy_name  # Add strategy name from custom tag
                    }
                    consolidated_trades.append(trade)
                    log.debug(f"Created LONG trade: {closed_qty} @ ${entry_price:.2f} → ${exit_price:.2f}, P&L: ${pnl:.2f}")
                    
                    # Update remaining quantities
                    position['remaining_qty'] -= closed_qty
                    remaining_qty -= closed_qty
                    
                    # Remove position if fully closed
                    if position['remaining_qty'] <= 0:
                        open_positions.pop(0)
                
                # If there's still quantity remaining after closing longs, it's a new short position
                if remaining_qty > 0:
                    open_positions.append({
                        'entry_order': order,
                        'entry_price': price,
                        'entry_time': timestamp,
                        'remaining_qty': remaining_qty,
                        'side': 'SHORT'
                    })
                    log.debug(f"Opened SHORT position: {remaining_qty} @ ${price:.2f}")
    
    # Sort consolidated trades by exit time
    consolidated_trades.sort(key=lambda x: x.get('exit_time', ''))
    
    # Deduplicate trades: if multiple trades have same entry_order_id, exit_order_id, entry_price, exit_price, and times,
    # they are likely duplicates from the same order pair being processed multiple times
    seen_trades = {}
    deduplicated_trades = []
    for trade in consolidated_trades:
        # Create a unique key from trade characteristics
        entry_id = trade.get('entry_order_id')
        exit_id = trade.get('exit_order_id')
        entry_price = trade.get('entry_price')
        exit_price = trade.get('exit_price')
        entry_time = trade.get('entry_time')
        exit_time = trade.get('exit_time')
        
        # Use a combination of order IDs and prices as the key
        # If same entry/exit order IDs with same prices, it's likely the same trade
        trade_key = (entry_id, exit_id, entry_price, exit_price)
        
        if trade_key not in seen_trades:
            seen_trades[trade_key] = trade
            deduplicated_trades.append(trade)
        else:
            # Check if times are very close (within 1 second) - likely same trade
            existing_trade = seen_trades[trade_key]
            existing_entry_time = existing_trade.get('entry_time')
            existing_exit_time = existing_trade.get('exit_time')
            
            # Convert times to comparable format if needed
            try:
                from datetime import datetime
                if isinstance(entry_time, str):
                    entry_time_dt = datetime.fromisoformat(entry_time.replace('Z', '+00:00'))
                elif isinstance(entry_time, datetime):
                    entry_time_dt = entry_time
                else:
                    entry_time_dt = None
                
                if isinstance(existing_entry_time, str):
                    existing_entry_time_dt = datetime.fromisoformat(existing_entry_time.replace('Z', '+00:00'))
                elif isinstance(existing_entry_time, datetime):
                    existing_entry_time_dt = existing_entry_time
                else:
                    existing_entry_time_dt = None
                
                if isinstance(exit_time, str):
                    exit_time_dt = datetime.fromisoformat(exit_time.replace('Z', '+00:00'))
                elif isinstance(exit_time, datetime):
                    exit_time_dt = exit_time
                else:
                    exit_time_dt = None
                
                if isinstance(existing_exit_time, str):
                    existing_exit_time_dt = datetime.fromisoformat(existing_exit_time.replace('Z', '+00:00'))
                elif isinstance(existing_exit_time, datetime):
                    existing_exit_time_dt = existing_exit_time
                else:
                    existing_exit_time_dt = None
                
                # Compare times if both are available
                if entry_time_dt and existing_entry_time_dt:
                    entry_time_diff = abs((entry_time_dt - existing_entry_time_dt).total_seconds())
                else:
                    entry_time_diff = 999  # Can't compare, assume different
                
                if exit_time_dt and existing_exit_time_dt:
                    exit_time_diff = abs((exit_time_dt - existing_exit_time_dt).total_seconds())
                else:
                    exit_time_diff = 999  # Can't compare, assume different
                
                # If times are very close (within 1 second), it's likely a duplicate
                if entry_time_diff < 1.0 and exit_time_diff < 1.0:
                    log.debug(f"Skipping duplicate trade: entry={entry_id}, exit={exit_id}, entry_price={entry_price}, exit_price={exit_price}")
                    continue
                else:
                    # Different times, might be legitimate separate trades (e.g., partial fills)
                    deduplicated_trades.append(trade)
            except Exception as e:
                log.debug(f"Could not compare times for deduplication: {e}, keeping trade")
                deduplicated_trades.append(trade)
    
    # Log summary of consolidation
    duplicates_removed = len(consolidated_trades) - len(deduplicated_trades)
    if duplicates_removed > 0:
        log.info(f"Consolidated {len(orders)} orders into {len(consolidated_trades)} trades, removed {duplicates_removed} duplicates, final count: {len(deduplicated_trades)}")
    else:
        log.info(f"Consolidated {len(orders)} orders into {len(deduplicated_trades)} completed trades")
    if deduplicated_trades:
        log.debug(f"Trades summary: {[(t['symbol'], t['side'], t['quantity'], t['pnl']) for t in deduplicated_trades]}")
    
    return deduplicated_trades

def calculate_trade_statistics(trades: List[Dict]) -> Dict:
    """
    Calculate statistics from a list of trades.
    Groundwork for statistical analysis.
    
    Args:
        trades: List of trade dictionaries
        
    Returns:
        Dict: Statistics including win rate, total P&L, average win/loss, etc.
    """
    if not trades:
        return {
            "total_trades": 0,
            "winning_trades": 0,
            "losing_trades": 0,
            "win_rate": 0.0,
            "total_pnl": 0.0,
            "average_pnl": 0.0,
            "average_win": 0.0,
            "average_loss": 0.0,
            "largest_win": 0.0,
            "largest_loss": 0.0
        }
    
    winning_trades = []
    losing_trades = []
    
    for trade in trades:
        # Prefer net_pnl (after fees) if available, otherwise use gross pnl
        net_pnl = trade.get('net_pnl')
        if net_pnl is not None:
            pnl = float(net_pnl)
        else:
            # Calculate net_pnl from gross pnl and fees if not provided
            gross_pnl = float(trade.get('pnl', 0) or trade.get('unrealizedPnl', 0) or trade.get('realizedPnl', 0))
            fees = float(trade.get('fees', 0) or trade.get('commission', 0) or 0)
            pnl = gross_pnl - fees
        if pnl > 0:
            winning_trades.append(pnl)
        elif pnl < 0:
            losing_trades.append(pnl)
    
    # Calculate total_pnl using net_pnl (after fees)
    total_pnl = 0.0
    for trade in trades:
        net_pnl = trade.get('net_pnl')
        if net_pnl is not None:
            total_pnl += float(net_pnl)
        else:
            # Calculate net_pnl from gross pnl and fees if not provided
            gross_pnl = float(trade.get('pnl', 0) or trade.get('unrealizedPnl', 0) or trade.get('realizedPnl', 0))
            fees = float(trade.get('fees', 0) or trade.get('commission', 0) or 0)
            total_pnl += (gross_pnl - fees)
    win_rate = (len(winning_trades) / len(trades) * 100) if trades else 0.0
    
    # Calculate profit factor
    total_wins = sum(winning_trades) if winning_trades else 0.0
    total_losses = abs(sum(losing_trades)) if losing_trades else 0.0
    profit_factor = total_wins / total_losses if total_losses > 0 else (99.99 if total_wins > 0 else 0.0)
    
    # Calculate Max Drawdown from trades
    max_drawdown = 0.0
    peak = 0.0
    current_pnl = 0.0
    
    # Sort trades by time if possible, otherwise assume order is chronological
    try:
        sorted_trades = sorted(trades, key=lambda x: x.get('exit_time') or x.get('timestamp') or '')
    except:
        sorted_trades = trades
        
    for t in sorted_trades:
        pnl = float(t.get('net_pnl') or (float(t.get('pnl', 0)) - float(t.get('fees', 0))))
        current_pnl += pnl
        if current_pnl > peak:
            peak = current_pnl
        drawdown = peak - current_pnl
        if drawdown > max_drawdown:
            max_drawdown = drawdown
    
    return {
        "total_trades": len(trades),
        "winning_trades": len(winning_trades),
        "losing_trades": len(losing_trades),
        "break_even_trades": len(trades) - len(winning_trades) - len(losing_trades),
        "win_rate": round(win_rate, 2),
        "total_pnl": round(total_pnl, 2),
        "profit_factor": round(profit_factor, 2),
        "max_drawdown": round(max_drawdown, 2),
        "max_drawdown_pct": 0.0, # Placeholder until we have account balance context
        "average_pnl": round(total_pnl / len(trades), 2) if trades else 0.0,
        "average_win": round(sum(winning_trades) / len(winning_trades), 2) if winning_trades else 0.0,
        "average_loss": round(sum(losing_trades) / len(losing_trades), 2) if losing_trades else 0.0,
        "largest_win": round(max(winning_trades), 2) if winning_trades else 0.0,
        "largest_loss": round(min(losing_trades), 2) if losing_trades else 0.0
    }
