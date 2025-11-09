"""
Dashboard backend for TopStepX Trading Bot
Provides API endpoints and WebSocket for real-time dashboard
"""

import json
import asyncio
import logging
import os
import sys
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any

# Add project root to Python path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from auth import require_auth, get_cors_headers

logger = logging.getLogger(__name__)

class DashboardAPI:
    """Dashboard API endpoints and WebSocket handling"""
    
    def __init__(self, trading_bot, webhook_server):
        self.trading_bot = trading_bot
        self.webhook_server = webhook_server
        self.websocket_clients = set()
    
    async def get_accounts(self) -> List[Dict[str, Any]]:
        """Get all available accounts"""
        try:
            accounts = await self.trading_bot.list_accounts()
            formatted_accounts = []
            
            for account in accounts:
                # Get current balance for each account
                try:
                    balance = await self.trading_bot.get_account_balance(account.get('id'))
                except:
                    balance = account.get('balance', 0)
                
                formatted_accounts.append({
                    "id": account.get('id'),
                    "name": account.get('name'),
                    "status": account.get('status'),
                    "balance": balance,
                    "currency": account.get('currency', 'USD'),
                    "account_type": account.get('account_type', 'unknown')
                })
            
            return formatted_accounts
        except Exception as e:
            logger.error(f"Error getting accounts: {e}")
            return []

    async def get_account_info(self) -> Dict[str, Any]:
        """Get current account information"""
        try:
            account = self.trading_bot.selected_account
            if not account:
                return {"error": "No account selected"}
            
            # Get current balance
            balance = await self.trading_bot.get_account_balance()
            
            return {
                "account_id": account.get('id'),
                "account_name": account.get('name'),
                "status": account.get('status'),
                "balance": balance,
                "currency": account.get('currency', 'USD'),
                "account_type": account.get('account_type', 'unknown')
            }
        except Exception as e:
            logger.error(f"Error getting account info: {e}")
            return {"error": str(e)}
    
    async def switch_account(self, account_id: str) -> Dict[str, Any]:
        """Switch to a different account"""
        try:
            # Find the account
            accounts = await self.trading_bot.list_accounts()
            target_account = None
            for account in accounts:
                if account.get('id') == account_id:
                    target_account = account
                    break
            
            if not target_account:
                return {"error": "Account not found"}
            
            # Switch to the account
            self.trading_bot.selected_account = target_account
            
            return {
                "success": True,
                "account_id": account_id,
                "account_name": target_account.get('name'),
                "message": f"Switched to account: {target_account.get('name', account_id)}"
            }
        except Exception as e:
            logger.error(f"Error switching account: {e}")
            return {"error": str(e)}
    
    async def get_positions(self) -> List[Dict[str, Any]]:
        """Get open positions"""
        try:
            positions = await self.trading_bot.get_open_positions()
            formatted_positions = []
            
            for pos in positions:
                # Calculate unrealized P&L if we have current price
                unrealized_pnl = 0
                unrealized_pnl_pct = 0
                
                formatted_positions.append({
                    "id": pos.get('id'),
                    "symbol": pos.get('symbol'),
                    "side": pos.get('side'),
                    "quantity": pos.get('quantity'),
                    "entry_price": pos.get('entryPrice'),
                    "current_price": pos.get('currentPrice'),
                    "unrealized_pnl": unrealized_pnl,
                    "unrealized_pnl_pct": unrealized_pnl_pct,
                    "stop_loss": pos.get('stopLoss'),
                    "take_profit": pos.get('takeProfit'),
                    "timestamp": pos.get('timestamp')
                })
            
            return formatted_positions
        except Exception as e:
            logger.error(f"Error getting positions: {e}")
            return []
    
    async def get_orders(self) -> List[Dict[str, Any]]:
        """Get open orders"""
        try:
            orders = await self.trading_bot.get_open_orders()
            formatted_orders = []
            
            for order in orders:
                formatted_orders.append({
                    "id": order.get('id'),
                    "symbol": order.get('symbol'),
                    "side": order.get('side'),
                    "type": order.get('type'),
                    "quantity": order.get('quantity'),
                    "price": order.get('price'),
                    "status": order.get('status'),
                    "timestamp": order.get('timestamp')
                })
            
            return formatted_orders
        except Exception as e:
            logger.error(f"Error getting orders: {e}")
            return []
    
    async def get_trade_history(self, start_date: str = None, end_date: str = None) -> List[Dict[str, Any]]:
        """Get trade history with date filtering"""
        try:
            # Default to last 7 days if no dates provided
            if not start_date:
                start_date = (datetime.now() - timedelta(days=7)).isoformat()
            if not end_date:
                end_date = datetime.now().isoformat()
            
            history = await self.trading_bot.get_order_history(
                account_id=self.trading_bot.selected_account.get('id'),
                start_timestamp=start_date,
                end_timestamp=end_date,
                limit=100
            )
            
            formatted_history = []
            for trade in history:
                formatted_history.append({
                    "id": trade.get('id'),
                    "symbol": trade.get('symbol'),
                    "side": trade.get('side'),
                    "quantity": trade.get('quantity'),
                    "price": trade.get('price'),
                    "pnl": trade.get('pnl', 0),
                    "timestamp": trade.get('timestamp'),
                    "status": trade.get('status')
                })
            
            return formatted_history
        except Exception as e:
            logger.error(f"Error getting trade history: {e}")
            return []
    
    async def get_performance_stats(self) -> Dict[str, Any]:
        """Get performance statistics"""
        try:
            # Get recent trades for stats
            history = await self.get_trade_history()
            
            total_trades = len(history)
            winning_trades = len([t for t in history if t.get('pnl', 0) > 0])
            losing_trades = len([t for t in history if t.get('pnl', 0) < 0])
            
            total_pnl = sum(t.get('pnl', 0) for t in history)
            win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0
            
            return {
                "total_trades": total_trades,
                "winning_trades": winning_trades,
                "losing_trades": losing_trades,
                "win_rate": round(win_rate, 2),
                "total_pnl": total_pnl,
                "avg_win": sum(t.get('pnl', 0) for t in history if t.get('pnl', 0) > 0) / max(winning_trades, 1),
                "avg_loss": sum(t.get('pnl', 0) for t in history if t.get('pnl', 0) < 0) / max(losing_trades, 1)
            }
        except Exception as e:
            logger.error(f"Error getting performance stats: {e}")
            return {"error": str(e)}
    
    async def get_system_logs(self, level: str = None, limit: int = 100) -> List[Dict[str, Any]]:
        """Get recent system logs"""
        try:
            # This is a simplified implementation
            # In production, you'd want to read from actual log files
            logs = []
            
            # Add some sample log entries for now
            logs.append({
                "timestamp": datetime.now().isoformat(),
                "level": "INFO",
                "message": "Dashboard API initialized",
                "source": "dashboard"
            })
            
            return logs[-limit:] if limit else logs
        except Exception as e:
            logger.error(f"Error getting system logs: {e}")
            return []
    
    async def place_order(self, symbol: str, side: str, quantity: int, order_type: str = "market", price: float = None) -> Dict[str, Any]:
        """Place a new order"""
        try:
            if order_type == "market":
                result = await self.trading_bot.place_market_order(
                    symbol=symbol,
                    side=side,
                    quantity=quantity
                )
            elif order_type == "limit":
                if not price:
                    return {"error": "Price required for limit orders"}
                result = await self.trading_bot.place_limit_order(
                    symbol=symbol,
                    side=side,
                    quantity=quantity,
                    price=price
                )
            else:
                return {"error": "Unsupported order type"}
            
            return result
        except Exception as e:
            logger.error(f"Error placing order: {e}")
            return {"error": str(e)}
    
    async def cancel_order(self, order_id: str) -> Dict[str, Any]:
        """Cancel an order"""
        try:
            result = await self.trading_bot.cancel_order(order_id)
            return result
        except Exception as e:
            logger.error(f"Error canceling order: {e}")
            return {"error": str(e)}
    
    async def close_position(self, position_id: str) -> Dict[str, Any]:
        """Close a position"""
        try:
            result = await self.trading_bot.close_position(position_id)
            return result
        except Exception as e:
            logger.error(f"Error closing position: {e}")
            return {"error": str(e)}
    
    async def flatten_all_positions(self) -> Dict[str, Any]:
        """Emergency flatten all positions"""
        try:
            result = await self.trading_bot.flatten_all_positions(interactive=False)
            return result
        except Exception as e:
            logger.error(f"Error flattening positions: {e}")
            return {"error": str(e)}
    
    async def cancel_all_orders(self) -> Dict[str, Any]:
        """Cancel all open orders"""
        try:
            orders = await self.trading_bot.get_open_orders()
            canceled_count = 0
            
            for order in orders:
                try:
                    await self.trading_bot.cancel_order(order.get('id'))
                    canceled_count += 1
                except Exception as e:
                    logger.warning(f"Failed to cancel order {order.get('id')}: {e}")
            
            return {"canceled": canceled_count, "total": len(orders)}
        except Exception as e:
            logger.error(f"Error canceling all orders: {e}")
            return {"error": str(e)}
    
    async def broadcast_update(self, update_type: str, data: Any):
        """Broadcast update to all connected WebSocket clients"""
        if not self.websocket_clients:
            return
        
        message = {
            "type": update_type,
            "data": data,
            "timestamp": datetime.now().isoformat()
        }
        
        message_json = json.dumps(message)
        disconnected_clients = set()
        
        for client in self.websocket_clients:
            try:
                await client.send(message_json)
            except Exception as e:
                logger.warning(f"Failed to send update to client: {e}")
                disconnected_clients.add(client)
        
        # Remove disconnected clients
        self.websocket_clients -= disconnected_clients
