"""
Async TradingView Webhook Server with Priority Task Queue

High-performance async webhook server using aiohttp for 10x better concurrency.
Integrates with priority task queue for optimal resource utilization.
"""

import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta
from typing import Dict, Optional, List, Any
from aiohttp import web
from aiohttp_cors import setup as cors_setup, ResourceOptions
import aiohttp

# Add project root to Python path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trading_bot import TopStepXTradingBot
from servers.dashboard import DashboardAPI
from servers.websocket_server import WebSocketServer
from infrastructure.task_queue import get_task_queue, TaskPriority
from infrastructure.performance_metrics import get_metrics_tracker

logger = logging.getLogger(__name__)


# Middleware to add no-cache headers to prevent stale data
@web.middleware
async def no_cache_middleware(request: web.Request, handler):
    """Add no-cache headers to all responses to ensure fresh data."""
    response = await handler(request)
    # Only add headers to API endpoints
    if request.path.startswith('/api/'):
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
    return response


class AsyncWebhookServer:
    """
    Async webhook server for TradingView integration.
    
    Features:
    - Non-blocking I/O with aiohttp
    - Priority task queue for background operations
    - 10x more concurrent request handling
    - Automatic request metrics tracking
    - Health and status endpoints
    """
    
    def __init__(self, trading_bot: TopStepXTradingBot, 
                 host: str = '0.0.0.0', 
                 port: int = 8080):
        """
        Initialize async webhook server.
        
        Args:
            trading_bot: Trading bot instance
            host: Server host
            port: Server port
        """
        self.trading_bot = trading_bot
        self.host = host
        self.port = port
        # Create application with no-cache middleware to prevent stale data in frontend
        self.app = web.Application(middlewares=[no_cache_middleware])
        self.task_queue = get_task_queue(max_concurrent=20)  # 20 concurrent background tasks
        self.metrics = get_metrics_tracker(db=getattr(trading_bot, 'db', None))
        self.dashboard_api = DashboardAPI(trading_bot, None)
        
        # WebSocket clients (integrated into main server)
        self.websocket_clients = set()
        
        # Initialize WebSocket server (port 8081) - kept for backward compatibility
        websocket_port = int(os.getenv('WEBSOCKET_PORT', '8081'))
        self.websocket_server = WebSocketServer(trading_bot, self, host=host, port=websocket_port)
        
        # Setup CORS for React frontend (before routes)
        self.cors = cors_setup(self.app, defaults={
            "*": ResourceOptions(
                allow_credentials=True,
                expose_headers="*",
                allow_headers="*",
                allow_methods="*"
            )
        })
        
        # Setup routes
        self._setup_routes()
        
        # Setup static file serving for frontend
        self._setup_static_routes()
        
        # Apply CORS to all routes
        for route in list(self.app.router.routes()):
            self.cors.add(route)
        
        # Server state
        self.server_start_time = None
        self.request_count = 0
        self.webhook_count = 0
        
        logger.info(f"✅ Async webhook server initialized ({host}:{port})")
    
    def _setup_static_routes(self):
        """Setup static file serving for React frontend."""
        import os
        from pathlib import Path
        
        # Determine the path to static files
        project_root = Path(__file__).parent.parent
        static_dir = project_root / 'static' / 'dashboard'
        
        if static_dir.exists():
            logger.info(f"📂 Serving frontend from: {static_dir}")
            
            # Serve static files (JS, CSS, images, etc.)
            self.app.router.add_static('/assets', static_dir / 'assets', name='assets')
            
            # Serve index.html for all frontend routes (React Router support)
            async def serve_frontend(request):
                """Serve index.html for all frontend routes."""
                index_path = static_dir / 'index.html'
                if index_path.exists():
                    return web.FileResponse(index_path)
                else:
                    return web.Response(text="Frontend not built. Run: cd frontend && npm run build", status=404)
            
            # Add routes for frontend pages
            self.app.router.add_get('/', serve_frontend)
            self.app.router.add_get('/dashboard', serve_frontend)
            self.app.router.add_get('/dashboard/', serve_frontend)
            self.app.router.add_get('/dashboard/{path:.*}', serve_frontend)
            self.app.router.add_get('/positions', serve_frontend)
            self.app.router.add_get('/strategies', serve_frontend)
            self.app.router.add_get('/settings', serve_frontend)
            
            logger.info("✅ Frontend routes configured")
        else:
            logger.warning(f"⚠️  Frontend not built at {static_dir}")
            logger.warning("   Run: cd frontend && npm run build")
    
    def _setup_routes(self):
        """Setup HTTP routes."""
        # WebSocket endpoint (must be first to avoid conflicts)
        self.app.router.add_get('/ws', self.handle_websocket)
        
        # Health & Status
        self.app.router.add_get('/health', self.handle_health)
        self.app.router.add_get('/api/health', self.handle_health)
        self.app.router.add_get('/status', self.handle_status)
        self.app.router.add_get('/api/status', self.handle_status)
        self.app.router.add_get('/metrics', self.handle_metrics)
        self.app.router.add_get('/api/metrics', self.handle_metrics)
        
        # Webhook endpoints
        self.app.router.add_post('/webhook', self.handle_webhook)
        self.app.router.add_post('/api/webhook', self.handle_webhook)
        
        # Dashboard API endpoints
        self.app.router.add_get('/api/accounts', self.handle_get_accounts)
        self.app.router.add_get('/api/account/info', self.handle_get_account_info)
        self.app.router.add_post('/api/account/switch', self.handle_switch_account)
        
        self.app.router.add_get('/api/positions', self.handle_get_positions)
        self.app.router.add_post('/api/positions/{position_id}/close', self.handle_close_position)
        self.app.router.add_post('/api/positions/flatten', self.handle_flatten_positions)
        
        self.app.router.add_get('/api/orders', self.handle_get_orders)
        self.app.router.add_post('/api/orders/{order_id}/cancel', self.handle_cancel_order)
        self.app.router.add_post('/api/orders/cancel-all', self.handle_cancel_all_orders)
        self.app.router.add_post('/api/orders/place', self.handle_place_order)
        
        self.app.router.add_get('/api/strategies', self.handle_get_strategies)
        self.app.router.add_get('/api/strategies/status', self.handle_get_strategy_status)
        self.app.router.add_post('/api/strategies/{name}/start', self.handle_start_strategy)
        self.app.router.add_post('/api/strategies/{name}/stop', self.handle_stop_strategy)
        
        self.app.router.add_get('/api/trades', self.handle_get_trades)
        self.app.router.add_get('/api/performance', self.handle_get_performance)
        self.app.router.add_get('/api/performance/history', self.handle_get_performance_history)
        self.app.router.add_get('/api/history', self.handle_get_historical_data)
        
        # Test endpoint for trade execution testing
        self.app.router.add_post('/api/test/overnight-breakout', self.handle_test_overnight_breakout)
        
        logger.debug("Routes configured: /health, /status, /metrics, /webhook, /api/*")
    
    async def handle_websocket(self, request: web.Request) -> web.WebSocketResponse:
        """
        WebSocket handler for real-time dashboard updates.
        
        Integrates WebSocket into the main HTTP server on the same port.
        """
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        
        client_ip = request.remote
        logger.info(f"🔌 WebSocket connected from {client_ip}. Total clients: {len(self.websocket_clients) + 1}")
        
        # Add client to active connections
        self.websocket_clients.add(ws)
        
        try:
            # Send welcome message with current data
            await ws.send_json({
                "type": "connected",
                "message": "Connected to trading dashboard",
                "timestamp": time.time()
            })
            
            # Send initial data
            try:
                accounts = await self.dashboard_api.get_accounts()
                await ws.send_json({
                    "type": "accounts_update",
                    "data": accounts,
                    "timestamp": time.time()
                })
                
                account_info = await self.dashboard_api.get_account_info()
                if "error" not in account_info:
                    await ws.send_json({
                        "type": "account_update",
                        "data": account_info,
                        "timestamp": time.time()
                    })
                
                positions = await self.dashboard_api.get_positions()
                await ws.send_json({
                    "type": "position_update",
                    "data": positions,
                    "timestamp": time.time()
                })
                
                orders = await self.dashboard_api.get_orders()
                await ws.send_json({
                    "type": "order_update",
                    "data": orders,
                    "timestamp": time.time()
                })
            except Exception as e:
                logger.error(f"Error sending initial WebSocket data: {e}")
            
            # Keep connection alive and handle messages
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                        # Handle ping/pong for keep-alive
                        if data.get('type') == 'ping':
                            await ws.send_json({"type": "pong", "timestamp": time.time()})
                    except json.JSONDecodeError:
                        logger.warning(f"Invalid JSON from WebSocket client: {msg.data}")
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    logger.error(f"WebSocket error: {ws.exception()}")
                    break
        
        except Exception as e:
            logger.error(f"WebSocket handler error: {e}")
        
        finally:
            # Remove client from active connections
            self.websocket_clients.discard(ws)
            logger.info(f"🔌 WebSocket disconnected from {client_ip}. Total clients: {len(self.websocket_clients)}")
        
        return ws
    
    async def broadcast_to_websockets(self, message: dict):
        """Broadcast a message to all connected WebSocket clients."""
        if not self.websocket_clients:
            return
        
        disconnected = set()
        for ws in self.websocket_clients:
            try:
                await ws.send_json(message)
            except Exception as e:
                logger.warning(f"Failed to send WebSocket message: {e}")
                disconnected.add(ws)
        
        # Clean up disconnected clients
        self.websocket_clients -= disconnected
    
    async def handle_health(self, request: web.Request) -> web.Response:
        """
        Health check endpoint.
        
        Returns server health status and trading bot state.
        """
        try:
            is_authenticated = self.trading_bot.session_token is not None
            selected_account = self.trading_bot.selected_account
            
            health_data = {
                "status": "healthy" if is_authenticated else "degraded",
                "authenticated": is_authenticated,
                "selected_account": selected_account.get('name') if selected_account else None,
                "account_id": selected_account.get('id') if selected_account else None,
                "server_uptime": str(datetime.now() - self.server_start_time).split('.')[0] if self.server_start_time else None,
                "requests_processed": self.request_count,
                "webhooks_processed": self.webhook_count,
                "task_queue": self.task_queue.get_stats(),
                "timestamp": datetime.now().isoformat()
            }
            
            status_code = 200 if is_authenticated else 503
            return web.json_response(health_data, status=status_code)
        
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            return web.json_response(
                {"status": "unhealthy", "error": str(e)},
                status=500
            )
    
    async def handle_status(self, request: web.Request) -> web.Response:
        """
        Status endpoint with detailed server information.
        """
        try:
            status_data = {
                "service": "TopStepX Trading Bot (Async)",
                "version": "2.0.0",
                "status": "running",
                "server_start_time": self.server_start_time.isoformat() if self.server_start_time else None,
                "uptime_seconds": (datetime.now() - self.server_start_time).total_seconds() if self.server_start_time else 0,
                "requests_processed": self.request_count,
                "webhooks_processed": self.webhook_count,
                "task_queue_stats": self.task_queue.get_stats(),
                "timestamp": datetime.now().isoformat()
            }
            
            return web.json_response(status_data)
        
        except Exception as e:
            logger.error(f"Status check failed: {e}")
            return web.json_response({"error": str(e)}, status=500)
    
    async def handle_metrics(self, request: web.Request) -> web.Response:
        """
        Metrics endpoint showing performance data.
        """
        try:
            metrics_data = {
                "server": {
                    "requests": self.request_count,
                    "webhooks": self.webhook_count,
                    "uptime_seconds": (datetime.now() - self.server_start_time).total_seconds() if self.server_start_time else 0,
                },
                "task_queue": self.task_queue.get_stats(),
                "performance": self.metrics.get_full_report(),
                "timestamp": datetime.now().isoformat()
            }
            
            # Broadcast metrics update via WebSocket
            await self.broadcast_to_websockets({
                "type": "metrics_update",
                "data": metrics_data,
                "timestamp": time.time()
            })
            
            return web.json_response(metrics_data)
        
        except Exception as e:
            logger.error(f"Metrics endpoint failed: {e}")
            return web.json_response({"error": str(e)}, status=500)
    
    # Dashboard API Handlers
    async def handle_get_accounts(self, request: web.Request) -> web.Response:
        """Get all available accounts."""
        try:
            accounts = await self.dashboard_api.get_accounts()
            # Broadcast update via WebSocket
            await self.broadcast_to_websockets({
                "type": "accounts_update",
                "data": accounts,
                "timestamp": time.time()
            })
            return web.json_response(accounts)
        except Exception as e:
            logger.error(f"Error getting accounts: {e}")
            return web.json_response({"error": str(e)}, status=500)
    
    async def handle_get_account_info(self, request: web.Request) -> web.Response:
        """Get current account information."""
        try:
            account_info = await self.dashboard_api.get_account_info()
            if "error" in account_info:
                return web.json_response(account_info, status=400)
            # Broadcast update via WebSocket
            await self.broadcast_to_websockets({
                "type": "account_update",
                "data": account_info,
                "timestamp": time.time()
            })
            return web.json_response(account_info)
        except Exception as e:
            logger.error(f"Error getting account info: {e}")
            return web.json_response({"error": str(e)}, status=500)
    
    async def handle_switch_account(self, request: web.Request) -> web.Response:
        """Switch to a different account."""
        try:
            data = await request.json()
            account_id = data.get('account_id')
            if not account_id:
                return web.json_response({"error": "account_id required"}, status=400)
            
            result = await self.dashboard_api.switch_account(account_id)
            if "error" in result:
                return web.json_response(result, status=400)
            return web.json_response(result)
        except Exception as e:
            logger.error(f"Error switching account: {e}")
            return web.json_response({"error": str(e)}, status=500)
    
    async def handle_get_positions(self, request: web.Request) -> web.Response:
        """Get open positions."""
        try:
            positions = await self.dashboard_api.get_positions()
            # Broadcast update via WebSocket
            await self.broadcast_to_websockets({
                "type": "position_update",
                "data": positions,
                "timestamp": time.time()
            })
            return web.json_response(positions)
        except Exception as e:
            logger.error(f"Error getting positions: {e}")
            return web.json_response({"error": str(e)}, status=500)
    
    async def handle_close_position(self, request: web.Request) -> web.Response:
        """Close a position."""
        try:
            position_id = request.match_info.get('position_id')
            if not position_id:
                return web.json_response({"error": "position_id required"}, status=400)
            
            data = await request.json() if request.content_length else {}
            quantity = data.get('quantity')
            
            result = await self.dashboard_api.close_position(position_id)
            if "error" in result:
                return web.json_response(result, status=400)
            return web.json_response(result)
        except Exception as e:
            logger.error(f"Error closing position: {e}")
            return web.json_response({"error": str(e)}, status=500)
    
    async def handle_flatten_positions(self, request: web.Request) -> web.Response:
        """Flatten all positions."""
        try:
            result = await self.dashboard_api.flatten_all_positions()
            if "error" in result:
                return web.json_response(result, status=400)
            return web.json_response(result)
        except Exception as e:
            logger.error(f"Error flattening positions: {e}")
            return web.json_response({"error": str(e)}, status=500)
    
    async def handle_get_orders(self, request: web.Request) -> web.Response:
        """Get open orders."""
        try:
            orders = await self.dashboard_api.get_orders()
            # Broadcast update via WebSocket
            await self.broadcast_to_websockets({
                "type": "order_update",
                "data": orders,
                "timestamp": time.time()
            })
            return web.json_response(orders)
        except Exception as e:
            logger.error(f"Error getting orders: {e}")
            return web.json_response({"error": str(e)}, status=500)
    
    async def handle_cancel_order(self, request: web.Request) -> web.Response:
        """Cancel an order."""
        try:
            order_id = request.match_info.get('order_id')
            if not order_id:
                return web.json_response({"error": "order_id required"}, status=400)
            
            result = await self.dashboard_api.cancel_order(order_id)
            if "error" in result:
                return web.json_response(result, status=400)
            return web.json_response(result)
        except Exception as e:
            logger.error(f"Error canceling order: {e}")
            return web.json_response({"error": str(e)}, status=500)
    
    async def handle_cancel_all_orders(self, request: web.Request) -> web.Response:
        """Cancel all open orders."""
        try:
            result = await self.dashboard_api.cancel_all_orders()
            if "error" in result:
                return web.json_response(result, status=400)
            return web.json_response(result)
        except Exception as e:
            logger.error(f"Error canceling all orders: {e}")
            return web.json_response({"error": str(e)}, status=500)
    
    async def handle_place_order(self, request: web.Request) -> web.Response:
        """Place a new order."""
        try:
            data = await request.json()
            symbol = data.get('symbol')
            side = data.get('side')
            quantity = data.get('quantity')
            order_type = data.get('type', 'market')
            price = data.get('price')
            
            if not all([symbol, side, quantity]):
                return web.json_response(
                    {"error": "symbol, side, and quantity required"},
                    status=400
                )
            
            result = await self.dashboard_api.place_order(
                symbol=symbol,
                side=side,
                quantity=quantity,
                order_type=order_type,
                price=price
            )
            
            if "error" in result:
                return web.json_response(result, status=400)
            return web.json_response(result)
        except Exception as e:
            logger.error(f"Error placing order: {e}")
            return web.json_response({"error": str(e)}, status=500)
    
    async def handle_test_overnight_breakout(self, request: web.Request) -> web.Response:
        """
        Test endpoint to simulate overnight breakout trades on PRACTICE account.
        
        Places stop orders with brackets (typical overnight breakout strategy):
        - Gets current market price
        - Places long breakout (stop above current price)
        - Places short breakout (stop below current price)
        - Both with stop loss and take profit brackets
        
        Request body (optional):
        {
            "symbol": "MNQ",  // Default: MNQ
            "quantity": 1,     // Default: 1
            "account_name": "PRACTICE"  // Default: looks for PRACTICE account
        }
        """
        try:
            data = await request.json() if request.content_length else {}
            symbol = data.get('symbol', 'MNQ').upper()
            quantity = data.get('quantity', 1)
            account_name_filter = data.get('account_name', 'PRAC').upper()
            
            # Map common names to search patterns
            if account_name_filter == 'PRACTICE':
                account_name_filter = 'PRAC'
            
            logger.info(f"🧪 TEST: Overnight breakout trade test for {symbol}")
            
            # Find account (searches for accounts containing the filter in name or ID)
            accounts = await self.trading_bot.list_accounts()
            practice_account = None
            for acc in accounts:
                acc_name = acc.get('name', '').upper()
                acc_id = str(acc.get('id', '')).upper()
                # Check if filter is in account name or ID
                if account_name_filter in acc_name or account_name_filter in acc_id:
                    practice_account = acc
                    logger.info(f"   Found matching account: {acc.get('name')} (ID: {acc.get('id')})")
                    break
            
            if not practice_account:
                return web.json_response({
                    "error": f"Account matching '{account_name_filter}' not found",
                    "available_accounts": [{"id": acc.get('id'), "name": acc.get('name')} for acc in accounts]
                }, status=404)
            
            account_id = str(practice_account.get('id'))
            logger.info(f"✅ Found account: {practice_account.get('name')} (ID: {account_id})")
            
            # Switch to practice account
            await self.dashboard_api.switch_account(account_id)
            
            # Get current market price
            quote = await self.trading_bot.get_market_quote(symbol)
            if "error" in quote:
                return web.json_response({
                    "error": f"Failed to get market quote: {quote['error']}"
                }, status=400)
            
            current_price = quote.get('last') or quote.get('bid') or quote.get('ask')
            if not current_price:
                return web.json_response({
                    "error": "Could not determine current market price",
                    "quote": quote
                }, status=400)
            
            logger.info(f"📊 Current {symbol} price: ${current_price:.2f}")
            
            # Calculate overnight breakout levels (typical: 0.1-0.3% above/below)
            tick_size = await self.trading_bot._get_tick_size(symbol)
            breakout_distance = max(tick_size * 10, current_price * 0.002)  # At least 10 ticks or 0.2%
            stop_loss_distance = breakout_distance * 1.5  # Stop loss 1.5x breakout distance
            take_profit_distance = breakout_distance * 2.5  # Take profit 2.5x breakout distance
            
            # Round to valid tick sizes
            breakout_distance = self.trading_bot._round_to_tick_size(breakout_distance, tick_size)
            stop_loss_distance = self.trading_bot._round_to_tick_size(stop_loss_distance, tick_size)
            take_profit_distance = self.trading_bot._round_to_tick_size(take_profit_distance, tick_size)
            
            # Long breakout: stop order above current price
            long_entry = current_price + breakout_distance
            long_stop_loss = long_entry - stop_loss_distance
            long_take_profit = long_entry + take_profit_distance
            
            # Short breakout: stop order below current price
            short_entry = current_price - breakout_distance
            short_stop_loss = short_entry + stop_loss_distance
            short_take_profit = short_entry - take_profit_distance
            
            results = {
                "account": {
                    "id": account_id,
                    "name": practice_account.get('name')
                },
                "symbol": symbol,
                "current_price": round(current_price, 2),
                "tick_size": tick_size,
                "trades": []
            }
            
            # Place long breakout trade
            logger.info(f"📈 Placing LONG breakout: Entry=${long_entry:.2f}, SL=${long_stop_loss:.2f}, TP=${long_take_profit:.2f}")
            long_result = await self.trading_bot.place_oco_bracket_with_stop_entry(
                symbol=symbol,
                side="BUY",
                quantity=quantity,
                entry_price=long_entry,
                stop_loss_price=long_stop_loss,
                take_profit_price=long_take_profit,
                account_id=account_id
            )
            results["trades"].append({
                "side": "LONG",
                "entry_price": round(long_entry, 2),
                "stop_loss": round(long_stop_loss, 2),
                "take_profit": round(long_take_profit, 2),
                "result": long_result
            })
            
            # Place short breakout trade
            logger.info(f"📉 Placing SHORT breakout: Entry=${short_entry:.2f}, SL=${short_stop_loss:.2f}, TP=${short_take_profit:.2f}")
            short_result = await self.trading_bot.place_oco_bracket_with_stop_entry(
                symbol=symbol,
                side="SELL",
                quantity=quantity,
                entry_price=short_entry,
                stop_loss_price=short_stop_loss,
                take_profit_price=short_take_profit,
                account_id=account_id
            )
            results["trades"].append({
                "side": "SHORT",
                "entry_price": round(short_entry, 2),
                "stop_loss": round(short_stop_loss, 2),
                "take_profit": round(short_take_profit, 2),
                "result": short_result
            })
            
            # Get current positions and orders to verify
            positions = await self.trading_bot.get_open_positions(account_id=account_id)
            orders = await self.trading_bot.get_open_orders(account_id=account_id)
            
            results["verification"] = {
                "positions_count": len(positions),
                "orders_count": len(orders),
                "positions": positions[:5],  # First 5 positions
                "orders": orders[:10]  # First 10 orders
            }
            
            logger.info(f"✅ Test complete. Positions: {len(positions)}, Orders: {len(orders)}")
            
            return web.json_response(results, status=200)
            
        except Exception as e:
            logger.error(f"❌ Error in test overnight breakout: {e}")
            logger.exception(e)
            return web.json_response({
                "error": str(e),
                "traceback": str(e.__traceback__) if hasattr(e, '__traceback__') else None
            }, status=500)
    
    async def handle_get_strategies(self, request: web.Request) -> web.Response:
        """Get all available strategies."""
        try:
            if not hasattr(self.trading_bot, 'strategy_manager'):
                return web.json_response({"error": "Strategy manager not available"}, status=503)
            
            strategies = []
            for name, strategy_class in self.trading_bot.strategy_manager.available_strategies.items():
                strategies.append({
                    "name": name,
                    "description": getattr(strategy_class, '__doc__', ''),
                    "enabled": name in self.trading_bot.strategy_manager.active_strategies
                })
            
            return web.json_response(strategies)
        except Exception as e:
            logger.error(f"Error getting strategies: {e}")
            return web.json_response({"error": str(e)}, status=500)
    
    async def handle_get_strategy_status(self, request: web.Request) -> web.Response:
        """Get status of all strategies."""
        try:
            if not hasattr(self.trading_bot, 'strategy_manager'):
                return web.json_response({"error": "Strategy manager not available"}, status=503)
            
            status = self.trading_bot.strategy_manager.get_status()
            return web.json_response(status)
        except Exception as e:
            logger.error(f"Error getting strategy status: {e}")
            return web.json_response({"error": str(e)}, status=500)
    
    async def handle_start_strategy(self, request: web.Request) -> web.Response:
        """Start a strategy."""
        try:
            strategy_name = request.match_info.get('name')
            if not strategy_name:
                return web.json_response({"error": "strategy name required"}, status=400)
            
            data = await request.json() if request.content_length else {}
            symbols = data.get('symbols')
            
            if not hasattr(self.trading_bot, 'strategy_manager'):
                return web.json_response({"error": "Strategy manager not available"}, status=503)
            
            success, message = self.trading_bot.strategy_manager.start_strategy(
                strategy_name,
                symbols=symbols
            )
            
            if success:
                return web.json_response({"success": True, "message": message})
            else:
                return web.json_response({"error": message}, status=400)
        except Exception as e:
            logger.error(f"Error starting strategy: {e}")
            return web.json_response({"error": str(e)}, status=500)
    
    async def handle_stop_strategy(self, request: web.Request) -> web.Response:
        """Stop a strategy."""
        try:
            strategy_name = request.match_info.get('name')
            if not strategy_name:
                return web.json_response({"error": "strategy name required"}, status=400)
            
            if not hasattr(self.trading_bot, 'strategy_manager'):
                return web.json_response({"error": "Strategy manager not available"}, status=503)
            
            success, message = self.trading_bot.strategy_manager.stop_strategy(strategy_name)
            
            if success:
                return web.json_response({"success": True, "message": message})
            else:
                return web.json_response({"error": message}, status=400)
        except Exception as e:
            logger.error(f"Error stopping strategy: {e}")
            return web.json_response({"error": str(e)}, status=500)
    
    async def handle_get_trades(self, request: web.Request) -> web.Response:
        """Get trade history with filters and pagination."""
        try:
            params = request.rel_url.query
            account_id = params.get('account_id')
            start_date = params.get('start') or params.get('start_date')
            end_date = params.get('end') or params.get('end_date')
            symbol = params.get('symbol')
            trade_type = params.get('type', 'filled')
            limit = int(params.get('limit', '50'))
            cursor = params.get('cursor')
            refresh = params.get('refresh', '0') == '1'

            # Clear cache if refresh requested
            if refresh and account_id:
                cache_key = str(account_id)
                self.dashboard_api._order_history_cache.pop(cache_key, None)
                logger.info(f"🔄 Cache cleared for account {account_id} (refresh=1)")

            trades = await self.dashboard_api.get_trade_history_paginated(
                account_id=account_id,
                start_date=start_date,
                end_date=end_date,
                symbol=symbol,
                trade_type=trade_type,
                limit=limit,
                cursor=cursor,
            )
            status = 200 if 'error' not in trades else 400
            return web.json_response(trades, status=status)
        except Exception as e:
            logger.error(f"Error getting trades: {e}")
            return web.json_response({"error": str(e)}, status=500)
    
    async def handle_get_performance(self, request: web.Request) -> web.Response:
        """Get performance statistics."""
        try:
            stats = await self.dashboard_api.get_performance_stats()
            if "error" in stats:
                return web.json_response(stats, status=400)
            return web.json_response(stats)
        except Exception as e:
            logger.error(f"Error getting performance: {e}")
            return web.json_response({"error": str(e)}, status=500)
    
    async def handle_get_performance_history(self, request: web.Request) -> web.Response:
        """Get cumulative performance history for charting."""
        try:
            params = request.rel_url.query
            account_id = params.get('account_id')
            interval = params.get('interval', 'day')
            start_date = params.get('start') or params.get('start_date')
            end_date = params.get('end') or params.get('end_date')
            refresh = params.get('refresh', '0') == '1'
            
            logger.info(f"📊 PERFORMANCE HISTORY REQUEST: account_id={account_id}, interval={interval}, start={start_date}, end={end_date}")

            # Clear cache if refresh requested
            if refresh and account_id:
                cache_key = str(account_id)
                self.dashboard_api._order_history_cache.pop(cache_key, None)
                logger.info(f"🔄 Cache cleared for account {account_id} (refresh=1)")

            logger.info(f"🔍 Calling dashboard_api.get_performance_history...")
            history = await self.dashboard_api.get_performance_history(
                account_id=account_id,
                interval=interval,
                start_date=start_date,
                end_date=end_date,
            )
            logger.info(f"✅ Performance history returned {len(history.get('points', []))} data points")
            status = 200 if 'error' not in history else 400
            return web.json_response(history, status=status)
        except Exception as e:
            logger.error(f"❌ Error getting performance history: {e}")
            logger.exception(e)  # Print full stack trace
            return web.json_response({"error": str(e)}, status=500)
    
    async def handle_get_historical_data(self, request: web.Request) -> web.Response:
        """Fetch historical OHLCV bars for charts."""
        try:
            params = request.rel_url.query
            symbol = params.get('symbol')
            timeframe = params.get('timeframe', '5m')
            limit = int(params.get('limit', '300'))
            end_time = params.get('end') or params.get('end_time')
            data = await self.dashboard_api.get_historical_data(
                symbol=symbol,
                timeframe=timeframe,
                limit=limit,
                end_time=end_time,
            )
            status = 200 if 'error' not in data else 400
            return web.json_response(data, status=status)
        except Exception as e:
            logger.error(f"Error getting historical data: {e}")
            return web.json_response({"error": str(e)}, status=500)
    
    async def handle_webhook(self, request: web.Request) -> web.Response:
        """
        Main webhook handler for TradingView signals.
        
        Processes webhook payload and submits trading tasks to priority queue.
        """
        start_time = time.time()
        self.request_count += 1
        self.webhook_count += 1
        
        try:
            # Parse JSON payload
            try:
                payload = await request.json()
                logger.info(f"📨 Webhook received: {json.dumps(payload, indent=2)}")
            except json.JSONDecodeError as e:
                logger.error(f"Invalid JSON payload: {e}")
                return web.json_response(
                    {"success": False, "error": "Invalid JSON payload"},
                    status=400
                )
            
            # Validate required fields
            if not isinstance(payload, dict):
                return web.json_response(
                    {"success": False, "error": "Payload must be a JSON object"},
                    status=400
                )
            
            # Submit webhook processing to task queue (HIGH priority)
            task_id = await self.task_queue.submit_high(
                self._process_webhook(payload),
                task_id=f"webhook_{int(time.time() * 1000)}",
                timeout=60.0
            )
            
            # Return immediately (non-blocking)
            duration_ms = (time.time() - start_time) * 1000
            logger.info(f"✅ Webhook queued: {task_id} ({duration_ms:.0f}ms)")
            
            return web.json_response({
                "success": True,
                "message": "Webhook received and queued for processing",
                "task_id": task_id,
                "processing_time_ms": duration_ms
            })
        
        except Exception as e:
            logger.error(f"Webhook handler error: {e}")
            return web.json_response(
                {"success": False, "error": str(e)},
                status=500
            )
    
    async def _process_webhook(self, payload: Dict):
        """
        Process webhook payload (runs in task queue).
        
        This is the actual trading logic execution.
        """
        try:
            logger.info(f"🔄 Processing webhook: {payload.get('action', 'unknown')}")
            
            # Extract signal data
            action = payload.get('action', '').upper()
            symbol = payload.get('symbol', 'MNQ')
            quantity = int(payload.get('quantity', 1))
            price = payload.get('price')
            stop_loss = payload.get('stop_loss')
            take_profit = payload.get('take_profit')
            
            # Validate action
            if action not in ['BUY', 'SELL', 'LONG', 'SHORT', 'CLOSE', 'FLATTEN']:
                logger.warning(f"⚠️  Unknown action: {action}")
                return {"success": False, "error": f"Unknown action: {action}"}
            
            # Ensure bot is authenticated
            if not self.trading_bot.session_token:
                logger.error("❌ Bot not authenticated")
                return {"success": False, "error": "Bot not authenticated"}
            
            # Ensure account is selected
            if not self.trading_bot.selected_account:
                logger.error("❌ No account selected")
                return {"success": False, "error": "No account selected"}
            
            # Execute trade based on action
            if action in ['CLOSE', 'FLATTEN']:
                # Close all positions
                logger.info(f"🔴 Closing all positions for {symbol}")
                result = await self.trading_bot.flatten_symbol(symbol)
            elif action in ['BUY', 'LONG']:
                # Open long position
                logger.info(f"🟢 Opening LONG position: {symbol} x{quantity}")
                if stop_loss and take_profit:
                    # Use bracket order
                    result = await self.trading_bot.create_bracket_order(
                        symbol=symbol,
                        side='BUY',
                        quantity=quantity,
                        stop_loss_price=stop_loss,
                        take_profit_price=take_profit
                    )
                else:
                    # Simple market order
                    result = await self.trading_bot.place_market_order(
                        symbol=symbol,
                        side='BUY',
                        quantity=quantity
                    )
            elif action in ['SELL', 'SHORT']:
                # Open short position
                logger.info(f"🔴 Opening SHORT position: {symbol} x{quantity}")
                if stop_loss and take_profit:
                    # Use bracket order
                    result = await self.trading_bot.create_bracket_order(
                        symbol=symbol,
                        side='SELL',
                        quantity=quantity,
                        stop_loss_price=stop_loss,
                        take_profit_price=take_profit
                    )
                else:
                    # Simple market order
                    result = await self.trading_bot.place_market_order(
                        symbol=symbol,
                        side='SELL',
                        quantity=quantity
                    )
            
            logger.info(f"✅ Trade executed: {action} {symbol} x{quantity}")
            return {
                "success": True,
                "action": action,
                "symbol": symbol,
                "quantity": quantity,
                "result": result
            }
        
        except Exception as e:
            logger.error(f"❌ Webhook processing error: {e}")
            return {"success": False, "error": str(e)}
    
    async def start_background_tasks(self):
        """Start background tasks (fills monitoring, metrics, etc)."""
        logger.info("🚀 Starting background tasks...")
        
        # Start task queue workers
        await self.task_queue.start(num_workers=5)
        
        # Submit periodic tasks
        await self._submit_periodic_tasks()
        
        logger.info("✅ Background tasks started")
    
    async def _submit_periodic_tasks(self):
        """Start periodic background tasks as independent coroutines."""
        # Check order fills every 30 seconds (runs as background task)
        async def check_fills():
            while True:
                try:
                    await self.trading_bot.check_order_fills()
                    await asyncio.sleep(30)
                except Exception as e:
                    logger.error(f"Fill check error: {e}")
                    await asyncio.sleep(60)
        
        # Start as background task (not in task queue - infinite loops don't work there)
        asyncio.create_task(check_fills())
        logger.debug("✅ Started periodic fill check task")
        
        # Update account balance every 60 seconds
        async def update_balance():
            while True:
                try:
                    await self.trading_bot.get_account_balance()
                    await asyncio.sleep(60)
                except Exception as e:
                    logger.error(f"Balance update error: {e}")
                    await asyncio.sleep(120)
        
        asyncio.create_task(update_balance())
        logger.debug("✅ Started periodic balance update task")
        
        # Print task queue stats every 5 minutes
        async def print_stats():
            while True:
                await asyncio.sleep(300)
                self.task_queue.print_stats()
        
        asyncio.create_task(print_stats())
        logger.debug("✅ Started periodic stats task")
    
    async def stop_background_tasks(self):
        """Stop all background tasks."""
        logger.info("🛑 Stopping background tasks...")
        await self.task_queue.stop(timeout=30.0)
        logger.info("✅ Background tasks stopped")
    
    async def run(self):
        """Run the async webhook server."""
        try:
            self.server_start_time = datetime.now()
            logger.info(f"🚀 Starting async webhook server on {self.host}:{self.port}")
            
            # Start background tasks
            await self.start_background_tasks()
            
            # Start WebSocket server
            logger.info(f"🚀 Starting WebSocket server on {self.websocket_server.host}:{self.websocket_server.port}")
            asyncio.create_task(self.websocket_server.start())
            
            # Start web server
            runner = web.AppRunner(self.app)
            await runner.setup()
            site = web.TCPSite(runner, self.host, self.port)
            await site.start()
            
            logger.info(f"✅ Async webhook server running on http://{self.host}:{self.port}")
            logger.info(f"✅ WebSocket server running on ws://{self.websocket_server.host}:{self.websocket_server.port}")
            logger.info(f"   Health check: http://{self.host}:{self.port}/health")
            logger.info(f"   Status: http://{self.host}:{self.port}/status")
            logger.info(f"   Metrics: http://{self.host}:{self.port}/metrics")
            logger.info(f"   Webhook: http://{self.host}:{self.port}/webhook")
            logger.info(f"   Dashboard API: http://{self.host}:{self.port}/api/*")
            logger.info(f"   - Accounts: GET /api/accounts")
            logger.info(f"   - Positions: GET /api/positions")
            logger.info(f"   - Orders: GET /api/orders")
            logger.info(f"   - Strategies: GET /api/strategies")
            
            # Keep server running
            try:
                await asyncio.Event().wait()
            except KeyboardInterrupt:
                logger.info("🛑 Received shutdown signal")
        
        finally:
            # Cleanup
            logger.info("🧹 Cleaning up...")
            await self.stop_background_tasks()
            if hasattr(self, 'websocket_server') and self.websocket_server:
                await self.websocket_server.stop()
            await runner.cleanup()
            logger.info("✅ Server shutdown complete")


async def main():
    """Main entry point for async webhook server."""
    import sys
    
    # Get configuration from environment
    host = os.getenv('WEBHOOK_HOST', '0.0.0.0')
    port = int(os.getenv('WEBHOOK_PORT', '8080'))
    api_key = os.getenv('PROJECT_X_API_KEY') or os.getenv('TOPSETPX_API_KEY')
    username = os.getenv('PROJECT_X_USERNAME') or os.getenv('TOPSETPX_USERNAME')
    
    if not api_key or not username:
        logger.error("❌ Missing API credentials in environment")
        sys.exit(1)
    
    # Initialize trading bot
    logger.info("🤖 Initializing trading bot...")
    trading_bot = TopStepXTradingBot(api_key=api_key, username=username)
    
    # Authenticate
    logger.info("🔐 Authenticating...")
    if not await trading_bot.authenticate():
        logger.error("❌ Authentication failed")
        sys.exit(1)
    
    # Select account
    logger.info("📋 Listing accounts...")
    accounts = await trading_bot.list_accounts()
    if not accounts:
        logger.error("❌ No accounts found")
        sys.exit(1)
    
    # Auto-select first account or use env variable
    account_id = os.getenv('TOPSETPX_ACCOUNT_ID')
    if account_id:
        selected_account = next((acc for acc in accounts if str(acc['id']) == str(account_id)), None)
        if selected_account:
            trading_bot.selected_account = selected_account
            logger.info(f"✅ Selected account: {selected_account['name']}")
        else:
            logger.error(f"❌ Account ID {account_id} not found")
            sys.exit(1)
    else:
        # Select first account
        trading_bot.selected_account = accounts[0]
        logger.info(f"✅ Auto-selected account: {accounts[0]['name']}")
    
    # Start webhook server
    server = AsyncWebhookServer(trading_bot, host=host, port=port)
    await server.run()


if __name__ == '__main__':
    asyncio.run(main())

