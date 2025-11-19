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
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, List, Any
from uuid import uuid4
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
from infrastructure.database import get_database

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
        self._settings_cache: Dict[str, Dict[str, Any]] = {}
        self._risk_events: Dict[str, List[Dict[str, Any]]] = {}
        self._risk_flags: Dict[str, Dict[str, bool]] = {}
        self._notifications: Dict[str, List[Dict[str, Any]]] = {}  # Account ID -> list of notifications
        
        # WebSocket clients (integrated into main server)
        self.websocket_clients = set()
        
        # Initialize WebSocket server (port 8081) - kept for backward compatibility
        websocket_port = int(os.getenv('WEBSOCKET_PORT', '8081'))
        self.websocket_server = WebSocketServer(trading_bot, self, host=host, port=websocket_port)
        
        # Initialize scheduled task manager (for strategy restarts)
        try:
            from servers.scheduled_tasks import ScheduledTaskManager
            self.scheduled_tasks = ScheduledTaskManager(trading_bot)
            logger.info("📅 Scheduled task manager initialized")
        except Exception as e:
            logger.warning(f"⚠️  Failed to initialize scheduled task manager: {e}")
            self.scheduled_tasks = None
        
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
        # Favicon placeholder to prevent 404 noise
        self.app.router.add_get('/favicon.ico', self.handle_favicon)
        
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
        self.app.router.add_post('/api/positions/{position_id}/trailing-stop', self.handle_trailing_stop)
        self.app.router.add_post('/api/positions/{position_id}/breakeven', self.handle_breakeven_toggle)
        
        self.app.router.add_get('/api/orders', self.handle_get_orders)
        self.app.router.add_post('/api/orders/{order_id}/cancel', self.handle_cancel_order)
        self.app.router.add_post('/api/orders/cancel-all', self.handle_cancel_all_orders)
        self.app.router.add_post('/api/orders/place', self.handle_place_order)
        self.app.router.add_get('/api/settings', self.handle_get_settings)
        self.app.router.add_post('/api/settings', self.handle_save_settings)
        self.app.router.add_get('/api/scheduled-tasks', self.handle_get_scheduled_tasks)
        self.app.router.add_get('/api/risk', self.handle_get_risk)
        self.app.router.add_get('/api/notifications', self.handle_get_notifications)
        
        self.app.router.add_get('/api/strategies', self.handle_get_strategies)
        self.app.router.add_get('/api/strategies/status', self.handle_get_strategy_status)
        self.app.router.add_post('/api/strategies/{name}/start', self.handle_start_strategy)
        self.app.router.add_post('/api/strategies/{name}/stop', self.handle_stop_strategy)
        self.app.router.add_get('/api/strategies/{name}/stats', self.handle_get_strategy_stats)
        self.app.router.add_get('/api/strategies/{name}/logs', self.handle_get_strategy_logs)
        self.app.router.add_post('/api/strategies/{name}/test', self.handle_test_strategy)
        self.app.router.add_put('/api/strategies/{name}/config', self.handle_update_strategy_config)

        self.app.router.add_get('/api/trades', self.handle_get_trades)
        self.app.router.add_get('/api/trades/export', self.handle_export_trades_csv)
        self.app.router.add_get('/api/performance', self.handle_get_performance)
        self.app.router.add_get('/api/performance/history', self.handle_get_performance_history)
        self.app.router.add_get('/api/performance/export', self.handle_export_performance_csv)
        self.app.router.add_get('/api/history', self.handle_get_historical_data)
        
        # Test endpoint for trade execution testing
        self.app.router.add_post('/api/test/overnight-breakout', self.handle_test_overnight_breakout)
        
        # Order modification endpoint
        self.app.router.add_post('/api/orders/{order_id}/modify', self.handle_modify_order)
        
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
                
                risk_snapshot = self._collect_risk_snapshot(account_id=None, emit_events=False)
                if risk_snapshot:
                    await ws.send_json({
                        "type": "risk_update",
                        "data": risk_snapshot,
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
            
            if hasattr(self.trading_bot, 'strategy_manager'):
                await self.trading_bot.strategy_manager.apply_persisted_states()
                # Auto-start strategies enabled via environment variables
                await self.trading_bot.strategy_manager.auto_start_enabled_strategies()

            await self._broadcast_risk_snapshot(account_id)
            
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
            
            await self.broadcast_to_websockets({
                "type": "position_update",
                "data": await self.dashboard_api.get_positions(),
                "timestamp": time.time()
            })
            await self._broadcast_risk_snapshot()
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
            
            # Broadcast update via WebSocket
            await self.broadcast_to_websockets({
                "type": "order_update",
                "data": await self.dashboard_api.get_orders(),
                "timestamp": time.time()
            })
            await self._broadcast_risk_snapshot()
            
            return web.json_response(result)
        except Exception as e:
            logger.error(f"Error canceling order: {e}")
            return web.json_response({"error": str(e)}, status=500)
    
    async def handle_modify_order(self, request: web.Request) -> web.Response:
        """Modify an order (price and/or quantity)."""
        try:
            order_id = request.match_info.get('order_id')
            if not order_id:
                return web.json_response({"error": "order_id required"}, status=400)
            
            data = await request.json() if request.content_length else {}
            new_price = data.get('price')
            new_quantity = data.get('quantity')
            order_type = data.get('order_type')  # Optional: 1=Limit, 4=Stop, etc.
            
            if new_price is None and new_quantity is None:
                return web.json_response({"error": "Either 'price' or 'quantity' must be provided"}, status=400)
            
            logger.info(f"Modifying order {order_id}: price={new_price}, quantity={new_quantity}, type={order_type}")
            
            result = await self.trading_bot.modify_order(
                order_id=order_id,
                new_price=float(new_price) if new_price is not None else None,
                new_quantity=int(new_quantity) if new_quantity is not None else None,
                order_type=int(order_type) if order_type is not None else None
            )
            
            if "error" in result:
                return web.json_response(result, status=400)
            
            # Broadcast update via WebSocket
            await self.broadcast_to_websockets({
                "type": "order_update",
                "data": await self.dashboard_api.get_orders(),
                "timestamp": time.time()
            })
            await self._broadcast_risk_snapshot()
            
            return web.json_response(result)
        except Exception as e:
            logger.error(f"Error modifying order: {e}")
            logger.exception(e)
            return web.json_response({"error": str(e)}, status=500)
    
    async def handle_close_position(self, request: web.Request) -> web.Response:
        """Close a position (full or partial)."""
        try:
            position_id = request.match_info.get('position_id')
            if not position_id:
                return web.json_response({"error": "position_id required"}, status=400)
            
            data = await request.json() if request.content_length else {}
            quantity = data.get('quantity')  # Optional: close partial position
            
            logger.info(f"Closing position {position_id}, quantity: {quantity}")
            
            result = await self.trading_bot.close_position(
                position_id=position_id,
                quantity=int(quantity) if quantity else None
            )
            
            if "error" in result:
                return web.json_response(result, status=400)
            
            # Broadcast update via WebSocket
            await self.broadcast_to_websockets({
                "type": "position_update",
                "data": await self.dashboard_api.get_positions(),
                "timestamp": time.time()
            })
            await self._broadcast_risk_snapshot()
            
            # Record notification
            account_id = self._get_selected_account_id()
            if account_id:
                qty_msg = f" ({quantity} contracts)" if quantity else ""
                self._record_notification(
                    account_id,
                    'position_close',
                    f"Position closed{qty_msg}",
                    level='info',
                    meta={"position_id": position_id, "quantity": quantity}
                )
            
            return web.json_response(result)
        except Exception as e:
            logger.error(f"Error closing position: {e}")
            logger.exception(e)
            return web.json_response({"error": str(e)}, status=500)
    
    async def handle_modify_position_stop(self, request: web.Request) -> web.Response:
        """Modify stop loss for a position."""
        try:
            position_id = request.match_info.get('position_id')
            if not position_id:
                return web.json_response({"error": "position_id required"}, status=400)
            
            data = await request.json()
            new_stop_price = data.get('stop_price')
            
            if new_stop_price is None:
                return web.json_response({"error": "stop_price required"}, status=400)
            
            logger.info(f"Modifying stop loss for position {position_id}: ${new_stop_price}")
            
            result = await self.trading_bot.modify_stop_loss(
                position_id=position_id,
                new_stop_price=float(new_stop_price)
            )
            
            if "error" in result:
                return web.json_response(result, status=400)
            
            # Broadcast update via WebSocket
            await self.broadcast_to_websockets({
                "type": "position_update",
                "data": await self.dashboard_api.get_positions(),
                "timestamp": time.time()
            })
            await self._broadcast_risk_snapshot()
            
            return web.json_response(result)
        except Exception as e:
            logger.error(f"Error modifying position stop: {e}")
            logger.exception(e)
            return web.json_response({"error": str(e)}, status=500)
    
    async def handle_modify_position_take_profit(self, request: web.Request) -> web.Response:
        """Modify take profit for a position."""
        try:
            position_id = request.match_info.get('position_id')
            if not position_id:
                return web.json_response({"error": "position_id required"}, status=400)
            
            data = await request.json()
            new_tp_price = data.get('take_profit')
            
            if new_tp_price is None:
                return web.json_response({"error": "take_profit required"}, status=400)
            
            logger.info(f"Modifying take profit for position {position_id}: ${new_tp_price}")
            
            result = await self.trading_bot.modify_take_profit(
                position_id=position_id,
                new_tp_price=float(new_tp_price)
            )
            
            if "error" in result:
                return web.json_response(result, status=400)
            
            await self.broadcast_to_websockets({
                "type": "position_update",
                "data": await self.dashboard_api.get_positions(),
                "timestamp": time.time()
            })
            await self._broadcast_risk_snapshot()
            
            return web.json_response(result)
        except Exception as e:
            logger.error(f"Error modifying position take profit: {e}")
            logger.exception(e)
            return web.json_response({"error": str(e)}, status=500)
    
    async def handle_trailing_stop(self, request: web.Request) -> web.Response:
        """Enable or disable trailing stop for a position."""
        try:
            position_id = request.match_info.get('position_id')
            if not position_id:
                return web.json_response({"error": "position_id required"}, status=400)
            
            data = await request.json() if request.content_length else {}
            enabled = data.get('enabled', True)
            trail_amount = data.get('trail_amount')  # Optional: trail amount in price units
            
            if enabled and trail_amount is None:
                return web.json_response({"error": "trail_amount required when enabling trailing stop"}, status=400)
            
            # Get position details
            positions = await self.dashboard_api.get_positions()
            position = next((p for p in positions if str(p.get('id')) == str(position_id)), None)
            
            if not position:
                return web.json_response({"error": "Position not found"}, status=404)
            
            symbol = position.get('symbol')
            side = position.get('side')
            quantity = position.get('quantity')
            
            if not all([symbol, side, quantity]):
                return web.json_response({"error": "Position missing required fields"}, status=400)
            
            if enabled:
                # Place trailing stop order
                logger.info(f"Enabling trailing stop for position {position_id}: {symbol} {side} x{quantity}, trail=${trail_amount}")
                result = await self.trading_bot.place_trailing_stop_order(
                    symbol=symbol,
                    side='BUY' if side == 'LONG' else 'SELL',
                    quantity=int(quantity),
                    trail_amount=float(trail_amount)
                )
                
                if "error" in result:
                    return web.json_response(result, status=400)
                
                # Record notification
                account_id = self._get_selected_account_id()
                if account_id:
                    self._record_notification(
                        account_id,
                        'automation',
                        f"Trailing stop enabled: {symbol} {side} x{quantity} (trail: ${trail_amount})",
                        level='info',
                        meta={"position_id": position_id, "symbol": symbol, "trail_amount": trail_amount}
                    )
            else:
                # Disable trailing stop (cancel trailing stop orders)
                logger.info(f"Disabling trailing stop for position {position_id}")
                # Find and cancel trailing stop orders for this position
                orders = await self.dashboard_api.get_orders()
                trailing_orders = [o for o in orders if 
                                  o.get('symbol') == symbol and 
                                  o.get('type') == 'TRAILING_STOP' and
                                  str(o.get('position_id', '')) == str(position_id)]
                
                cancelled_count = 0
                for order in trailing_orders:
                    cancel_result = await self.dashboard_api.cancel_order(str(order.get('id')))
                    if "error" not in cancel_result:
                        cancelled_count += 1
                
                result = {
                    "success": True,
                    "message": f"Trailing stop disabled, cancelled {cancelled_count} trailing stop orders",
                    "cancelled_orders": cancelled_count
                }
            
            # Broadcast updates
            await self.broadcast_to_websockets({
                "type": "position_update",
                "data": await self.dashboard_api.get_positions(),
                "timestamp": time.time()
            })
            await self.broadcast_to_websockets({
                "type": "order_update",
                "data": await self.dashboard_api.get_orders(),
                "timestamp": time.time()
            })
            
            return web.json_response(result)
        except Exception as e:
            logger.error(f"Error managing trailing stop: {e}")
            logger.exception(e)
            return web.json_response({"error": str(e)}, status=500)
    
    async def handle_breakeven_toggle(self, request: web.Request) -> web.Response:
        """Enable or disable breakeven monitoring for a position."""
        try:
            position_id = request.match_info.get('position_id')
            if not position_id:
                return web.json_response({"error": "position_id required"}, status=400)
            
            data = await request.json() if request.content_length else {}
            enabled = data.get('enabled', True)
            
            # Get position details
            positions = await self.dashboard_api.get_positions()
            position = next((p for p in positions if str(p.get('id')) == str(position_id)), None)
            
            if not position:
                return web.json_response({"error": "Position not found"}, status=404)
            
            symbol = position.get('symbol')
            entry_price = position.get('entry_price')
            
            if not entry_price:
                return web.json_response({"error": "Position missing entry price"}, status=400)
            
            # Move stop to breakeven if enabling
            if enabled:
                logger.info(f"Enabling breakeven for position {position_id}: {symbol} @ ${entry_price}")
                result = await self.trading_bot.modify_stop_loss(
                    position_id=position_id,
                    new_stop_price=float(entry_price)
                )
                
                if "error" in result:
                    return web.json_response(result, status=400)
                
                # Record notification
                account_id = self._get_selected_account_id()
                if account_id:
                    self._record_notification(
                        account_id,
                        'automation',
                        f"Breakeven enabled: {symbol} stop moved to entry ${entry_price}",
                        level='success',
                        meta={"position_id": position_id, "symbol": symbol, "entry_price": entry_price}
                    )
            else:
                result = {
                    "success": True,
                    "message": "Breakeven monitoring disabled (stop remains at current level)"
                }
            
            # Broadcast updates
            await self.broadcast_to_websockets({
                "type": "position_update",
                "data": await self.dashboard_api.get_positions(),
                "timestamp": time.time()
            })
            
            return web.json_response(result)
        except Exception as e:
            logger.error(f"Error toggling breakeven: {e}")
            logger.exception(e)
            return web.json_response({"error": str(e)}, status=500)
    
    async def handle_get_strategy_stats(self, request: web.Request) -> web.Response:
        """Get performance statistics for a specific strategy."""
        try:
            strategy_name = request.match_info.get('name')
            if not strategy_name:
                return web.json_response({"error": "strategy name required"}, status=400)
            
            account_id = request.rel_url.query.get('account_id') or self._get_selected_account_id()
            stats = await self.dashboard_api.get_strategy_stats(strategy_name, account_id)
            
            if "error" in stats:
                return web.json_response(stats, status=500)
            
            return web.json_response(stats)
        except Exception as e:
            logger.error(f"Error getting strategy stats: {e}")
            return web.json_response({"error": str(e)}, status=500)
    
    async def handle_get_strategy_logs(self, request: web.Request) -> web.Response:
        """Get logs for a specific strategy."""
        try:
            strategy_name = request.match_info.get('name')
            if not strategy_name:
                return web.json_response({"error": "strategy name required"}, status=400)
            
            limit = int(request.rel_url.query.get('limit', 100))
            logs = await self.dashboard_api.get_strategy_logs(strategy_name, limit)
            
            return web.json_response({"logs": logs, "strategy_name": strategy_name, "count": len(logs)})
        except Exception as e:
            logger.error(f"Error getting strategy logs: {e}")
            return web.json_response({"error": str(e)}, status=500)
    
    async def handle_test_strategy(self, request: web.Request) -> web.Response:
        """Test/trigger a strategy manually."""
        try:
            strategy_name = request.match_info.get('name')
            if not strategy_name:
                return web.json_response({"error": "strategy name required"}, status=400)
            
            data = await request.json() if request.content_length else {}
            account_id = data.get('account_id') or self._get_selected_account_id()
            
            # Switch account if provided
            if account_id:
                await self.dashboard_api.switch_account(str(account_id))
            
            # For now, just trigger a test run by starting the strategy temporarily
            # This is a simplified test - in production you'd want a dedicated test mode
            logger.info(f"🧪 Testing strategy: {strategy_name}")
            
            # Check if strategy exists
            if not hasattr(self.trading_bot, 'strategy_manager'):
                return web.json_response({"error": "Strategy manager not available"}, status=503)
            
            strategy_exists = (
                strategy_name in self.trading_bot.strategy_manager.available_strategies or
                strategy_name in self.trading_bot.strategy_manager.strategies
            )
            
            if not strategy_exists:
                return web.json_response({
                    "error": f"Strategy '{strategy_name}' not found"
                }, status=404)
            
            # Return strategy info for testing
            strategy = self.trading_bot.strategy_manager.strategies.get(strategy_name)
            if strategy:
                return web.json_response({
                    "success": True,
                    "message": f"Strategy '{strategy_name}' test initiated",
                    "strategy": {
                        "name": strategy_name,
                        "status": strategy.status.value if hasattr(strategy.status, 'value') else str(strategy.status),
                        "symbols": strategy.config.symbols,
                        "enabled": strategy.config.enabled,
                    }
                })
            else:
                return web.json_response({
                    "success": True,
                    "message": f"Strategy '{strategy_name}' is available but not instantiated",
                    "strategy": {
                        "name": strategy_name,
                        "status": "available",
                    }
                })
        except Exception as e:
            logger.error(f"Error testing strategy: {e}")
            logger.exception(e)
            return web.json_response({"error": str(e)}, status=500)

    async def handle_update_strategy_config(self, request: web.Request) -> web.Response:
        """Update strategy configuration (symbols, position_size, etc.)."""
        try:
            strategy_name = request.match_info.get('name')
            if not strategy_name:
                return web.json_response({"error": "strategy name required"}, status=400)

            data = await request.json() if request.content_length else {}

            if not hasattr(self.trading_bot, 'strategy_manager'):
                return web.json_response({"error": "Strategy manager not available"}, status=503)

            # Extract config updates
            symbols = data.get('symbols')
            position_size = data.get('position_size')
            max_positions = data.get('max_positions')
            enabled = data.get('enabled')

            # Validate symbols if provided
            if symbols is not None:
                if not isinstance(symbols, list) or not all(isinstance(s, str) for s in symbols):
                    return web.json_response({"error": "symbols must be a list of strings"}, status=400)
                if len(symbols) == 0:
                    return web.json_response({"error": "at least one symbol required"}, status=400)

            # Validate position_size if provided
            if position_size is not None:
                try:
                    position_size = int(position_size)
                    if position_size < 1:
                        return web.json_response({"error": "position_size must be at least 1"}, status=400)
                except (TypeError, ValueError):
                    return web.json_response({"error": "position_size must be a number"}, status=400)

            # Validate max_positions if provided
            if max_positions is not None:
                try:
                    max_positions = int(max_positions)
                    if max_positions < 1:
                        return web.json_response({"error": "max_positions must be at least 1"}, status=400)
                except (TypeError, ValueError):
                    return web.json_response({"error": "max_positions must be a number"}, status=400)

            # Update the configuration
            success, message = await self.trading_bot.strategy_manager.update_strategy_config(
                strategy_name,
                symbols=symbols,
                position_size=position_size,
                max_positions=max_positions,
                enabled=enabled
            )

            if not success:
                return web.json_response({"error": message}, status=400)

            # Get updated strategy info
            strategy = self.trading_bot.strategy_manager.strategies.get(strategy_name)
            if strategy:
                return web.json_response({
                    "success": True,
                    "message": message,
                    "config": {
                        "name": strategy_name,
                        "symbols": strategy.config.symbols,
                        "position_size": strategy.config.position_size,
                        "max_positions": strategy.config.max_positions,
                        "enabled": strategy.config.enabled,
                    }
                })
            else:
                return web.json_response({
                    "success": True,
                    "message": message
                })

        except Exception as e:
            logger.error(f"Error updating strategy config: {e}")
            logger.exception(e)
            return web.json_response({"error": str(e)}, status=500)

    async def handle_cancel_all_orders(self, request: web.Request) -> web.Response:
        """Cancel all open orders."""
        try:
            data = await request.json() if request.content_length else {}
            account_id = data.get('account_id')
            result = await self.dashboard_api.cancel_all_orders(account_id=account_id)
            if "error" in result:
                return web.json_response(result, status=400)
            
            # Broadcast updated orders list
            await self.broadcast_to_websockets({
                "type": "order_update",
                "data": await self.dashboard_api.get_orders(),
                "timestamp": time.time()
            })
            await self._broadcast_risk_snapshot()
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
            order_type = data.get('order_type') or data.get('type', 'market')
            limit_price = data.get('limit_price') or data.get('price')
            stop_price = data.get('stop_price')
            stop_loss_ticks = data.get('stop_loss_ticks')
            take_profit_ticks = data.get('take_profit_ticks')
            stop_loss_price = data.get('stop_loss_price')
            take_profit_price = data.get('take_profit_price')
            enable_bracket = bool(data.get('enable_bracket'))
            enable_breakeven = bool(data.get('enable_breakeven'))
            time_in_force = data.get('time_in_force')
            reduce_only = bool(data.get('reduce_only'))
            account_id = data.get('account_id')
            
            # Normalize numeric fields
            def _to_int(value):
                try:
                    return int(value) if value is not None else None
                except (TypeError, ValueError):
                    return None
            
            def _to_float(value):
                try:
                    return float(value) if value is not None else None
                except (TypeError, ValueError):
                    return None
            
            quantity = _to_int(quantity)
            stop_loss_ticks = _to_int(stop_loss_ticks)
            take_profit_ticks = _to_int(take_profit_ticks)
            limit_price = _to_float(limit_price)
            stop_price = _to_float(stop_price)
            stop_loss_price = _to_float(stop_loss_price)
            take_profit_price = _to_float(take_profit_price)
            
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
                limit_price=limit_price,
                stop_price=stop_price,
                stop_loss_ticks=stop_loss_ticks,
                take_profit_ticks=take_profit_ticks,
                stop_loss_price=stop_loss_price,
                take_profit_price=take_profit_price,
                account_id=account_id,
                enable_bracket=enable_bracket,
                enable_breakeven=enable_breakeven,
                time_in_force=time_in_force,
                reduce_only=reduce_only,
            )
            
            if "error" in result:
                return web.json_response(result, status=400)
            
            # Broadcast latest orders snapshot
            await self.broadcast_to_websockets({
                "type": "order_update",
                "data": await self.dashboard_api.get_orders(),
                "timestamp": time.time()
            })
            
            # Record notification
            account_id = self._get_selected_account_id()
            if account_id and result and not result.get('error'):
                self._record_notification(
                    account_id,
                    'order_placed',
                    f"Order placed: {symbol} {side} x{quantity}",
                    level='info',
                    meta={"symbol": symbol, "side": side, "quantity": quantity, "order_type": order_type}
                )
            
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
            "account_name": "PRAC"  // Default: PRAC (searches for accounts containing "PRAC" in name)
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
            
            summaries = self.trading_bot.strategy_manager.get_strategy_summaries()
            return web.json_response(summaries)
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
            account_id = data.get('accountId') or data.get('account_id')

            logger.info(f"📋 Starting strategy: {strategy_name}, symbols: {symbols}")
            if account_id:
                logger.info(f"Switching account to {account_id} before starting strategy")
                switch_result = await self.dashboard_api.switch_account(str(account_id))
                if 'error' in switch_result:
                    return web.json_response({"error": switch_result['error']}, status=400)
            
            if not hasattr(self.trading_bot, 'strategy_manager'):
                logger.error("Strategy manager not available")
                return web.json_response({"error": "Strategy manager not available"}, status=503)
            
            # Check if strategy exists
            if not hasattr(self.trading_bot.strategy_manager, 'available_strategies'):
                logger.error("Strategy manager has no available_strategies")
                return web.json_response({"error": "Strategy manager not properly initialized"}, status=503)
            
            # Check both available_strategies (classes) and strategies (instances)
            available_classes = list(self.trading_bot.strategy_manager.available_strategies.keys()) if hasattr(self.trading_bot.strategy_manager, 'available_strategies') else []
            available_instances = list(self.trading_bot.strategy_manager.strategies.keys()) if hasattr(self.trading_bot.strategy_manager, 'strategies') else []
            available = list(set(available_classes + available_instances))
            
            logger.info(f"Available strategy classes: {available_classes}")
            logger.info(f"Available strategy instances: {available_instances}")
            logger.info(f"All available strategies: {available}")
            
            # Check if strategy exists in either dict
            strategy_exists = (
                strategy_name in self.trading_bot.strategy_manager.available_strategies or
                strategy_name in self.trading_bot.strategy_manager.strategies
            )
            
            if not strategy_exists:
                logger.error(f"Strategy '{strategy_name}' not found. Available: {available}")
                return web.json_response({
                    "error": f"Strategy '{strategy_name}' not found. Available strategies: {', '.join(available) if available else 'none'}",
                    "available_strategies": available
                }, status=404)
            
            # CRITICAL FIX: await the async method
            result = await self.trading_bot.strategy_manager.start_strategy(
                strategy_name,
                symbols=symbols
            )

            # start_strategy may itself return a coroutine (older implementations)
            if asyncio.iscoroutine(result):
                result = await result

            success, message = result
            
            logger.info(f"Strategy start result: success={success}, message={message}")
            
            if success:
                return web.json_response({
                    "success": True,
                    "message": message
                })
            
                return web.json_response({"error": message}, status=400)
        except Exception as e:
            logger.error(f"Error starting strategy: {e}")
            logger.exception(e)  # Full stack trace
            return web.json_response({"error": str(e)}, status=500)
    
    async def handle_stop_strategy(self, request: web.Request) -> web.Response:
        """Stop a strategy."""
        try:
            strategy_name = request.match_info.get('name')
            if not strategy_name:
                return web.json_response({"error": "strategy name required"}, status=400)
            
            data = await request.json() if request.content_length else {}
            account_id = data.get('accountId') or data.get('account_id')

            if account_id:
                logger.info(f"Switching account to {account_id} before stopping strategy")
                switch_result = await self.dashboard_api.switch_account(str(account_id))
                if 'error' in switch_result:
                    return web.json_response({"error": switch_result['error']}, status=400)
            
            if not hasattr(self.trading_bot, 'strategy_manager'):
                return web.json_response({"error": "Strategy manager not available"}, status=503)
            
            # CRITICAL FIX: await the async method
            result = await self.trading_bot.strategy_manager.stop_strategy(strategy_name)

            if asyncio.iscoroutine(result):
                result = await result

            success, message = result
            
            if success:
                return web.json_response({
                    "success": True,
                    "message": message
                })
            
                return web.json_response({"error": message}, status=400)
        except Exception as e:
            logger.error(f"Error stopping strategy: {e}")
            return web.json_response({"error": str(e)}, status=500)
    
    async def handle_get_settings(self, request: web.Request) -> web.Response:
        """Fetch dashboard/settings preferences."""
        try:
            account_scope = request.rel_url.query.get('account_id')
            if account_scope == 'current':
                selected = getattr(self.trading_bot, 'selected_account', None)
                if selected:
                    account_scope = str(selected.get('id') or selected.get('account_id') or selected.get('accountId'))
                else:
                    account_scope = None
            scope_key = account_scope or "__global__"
            db = self._ensure_database()
            if not db:
                cached = self._settings_cache.get(scope_key, {})
                logger.warning("⚠️  Database unavailable when requesting settings - serving from cache")
                return web.json_response({
                    "settings": cached,
                    "scope": scope_key,
                    "warning": "database unavailable (serving cached settings)"
                })
            
            settings = db.get_dashboard_settings(account_scope)
            scope_label = account_scope or "global"
            cache_key = account_scope or "__global__"
            self._settings_cache[cache_key] = settings or {}
            
            return web.json_response({
                "settings": settings,
                "scope": scope_label
            })
        except Exception as e:
            logger.error(f"Error getting dashboard settings: {e}")
            return web.json_response({"error": str(e)}, status=500)
    
    async def handle_save_settings(self, request: web.Request) -> web.Response:
        """Persist dashboard/settings preferences."""
        try:
            payload = await request.json()
            if not isinstance(payload, dict):
                return web.json_response({"error": "payload must be a JSON object"}, status=400)
            
            account_scope = payload.get('account_id')
            if account_scope == 'current':
                selected = getattr(self.trading_bot, 'selected_account', None)
                account_scope = str(selected.get('id') or selected.get('account_id') or selected.get('accountId')) if selected else None
            
            # Remove routing keys from settings blob
            settings_to_store = {
                k: v for k, v in payload.items()
                if k not in ('account_id', 'scope') and not k.startswith('_')
            }
            scope_label = account_scope or "global"
            scope_key = account_scope or "__global__"
            
            db = self._ensure_database()
            if not db:
                self._settings_cache[scope_key] = settings_to_store
                logger.warning("⚠️  Database unavailable - settings stored in memory cache")
                return web.json_response({
                    "success": True,
                    "scope": scope_label,
                    "warning": "database unavailable - settings stored in memory"
                })
            
            if not db.save_dashboard_settings(settings_to_store, account_scope):
                self._settings_cache[scope_key] = settings_to_store
                logger.error("❌ Failed to persist settings to database - stored in memory instead")
                return web.json_response({
                    "success": True,
                    "scope": scope_label,
                    "warning": "failed to persist to database - settings stored in memory"
                })
            
            logger.info(f"💾 Saved dashboard settings ({scope_label})")
            cache_key = account_scope or "__global__"
            self._settings_cache[cache_key] = settings_to_store
            
            return web.json_response({"success": True, "scope": scope_label})
        except Exception as e:
            logger.error(f"Error saving dashboard settings: {e}")
            return web.json_response({"error": str(e)}, status=500)
    
    async def handle_get_scheduled_tasks(self, request: web.Request) -> web.Response:
        """Get scheduled task information."""
        try:
            if not hasattr(self, 'scheduled_tasks') or not self.scheduled_tasks:
                return web.json_response({
                    "enabled": False,
                    "message": "Scheduled task manager not available"
                })
            
            next_restart = self.scheduled_tasks.get_next_restart_time()
            
            return web.json_response({
                "enabled": True,
                "restart_time": self.scheduled_tasks.restart_time.strftime('%H:%M'),
                "timezone": str(self.scheduled_tasks.timezone),
                "next_restart": next_restart.isoformat() if next_restart else None,
                "last_restart_date": self.scheduled_tasks._last_restart_date,
            })
        except Exception as e:
            logger.error(f"Error getting scheduled tasks info: {e}")
            return web.json_response({"error": str(e)}, status=500)
    
    def _ensure_database(self):
        """Ensure we have an active database connection, attempting lazy re-init if necessary."""
        db = getattr(self.trading_bot, 'db', None)
        if db:
            return db
        try:
            logger.info("🔄 Reinitializing database connection for dashboard settings")
            self.trading_bot.db = get_database()
            return self.trading_bot.db
        except Exception as db_err:
            logger.error(f"❌ Failed to initialize database: {db_err}")
            self.trading_bot.db = None
            return None
    
    def _get_selected_account_id(self) -> Optional[str]:
        """Return the currently selected account ID as a string."""
        selected = getattr(self.trading_bot, 'selected_account', None)
        if selected:
            for key in ('id', 'account_id', 'accountId'):
                if selected.get(key) is not None:
                    return str(selected.get(key))
        tracker = getattr(self.trading_bot, 'account_tracker', None)
        if tracker and tracker.current_account_id:
            return str(tracker.current_account_id)
        return None
    
    def _get_selected_account_name(self) -> Optional[str]:
        """Return the currently selected account name if available."""
        selected = getattr(self.trading_bot, 'selected_account', None)
        if selected and selected.get('name'):
            return str(selected.get('name'))
        tracker = getattr(self.trading_bot, 'account_tracker', None)
        if tracker and tracker.current_account_id and tracker.current_account_id in tracker.accounts:
            return tracker.accounts[tracker.current_account_id].account_name
        return None
    
    def _record_risk_event(self, account_id: str, message: str, level: str = "info", meta: Optional[Dict[str, Any]] = None) -> None:
        """Record a risk-related event for the specified account."""
        events = self._risk_events.setdefault(account_id, [])
        event = {
            "id": str(uuid4()),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "message": message,
            "level": level,
        }
        if meta:
            event["meta"] = meta
        events.append(event)
        if len(events) > 50:
            del events[:-50]
    
    def _collect_risk_snapshot(self, account_id: Optional[str] = None, emit_events: bool = False) -> Optional[Dict[str, Any]]:
        """Collect current risk metrics for the specified (or active) account."""
        tracker = getattr(self.trading_bot, 'account_tracker', None)
        if not tracker:
            logger.warning("Risk snapshot requested but account tracker unavailable")
            return None
        
        account_id = account_id or self._get_selected_account_id()
        if not account_id:
            logger.warning("Risk snapshot requested but no account selected")
            return None
        
        state = tracker.get_state(account_id)
        compliance = tracker.check_compliance(account_id)
        account_name = state.get('account_name') or self._get_selected_account_name() or 'Unknown'
        
        dll_limit = float(compliance.get('dll_limit') or 0.0)
        dll_used = float(compliance.get('dll_used') or 0.0)
        dll_remaining = float(compliance.get('dll_remaining') or 0.0)
        dll_pct = min(1.0, dll_used / dll_limit) if dll_limit else 0.0
        
        mll_limit = float(compliance.get('mll_limit') or 0.0)
        mll_used = float(compliance.get('mll_used') or 0.0)
        mll_remaining = float(compliance.get('mll_remaining') or 0.0)
        mll_pct = min(1.0, mll_used / mll_limit) if mll_limit else 0.0
        
        trailing_loss = float(compliance.get('trailing_loss') or 0.0)
        violations = compliance.get('violations') or []
        is_compliant = bool(compliance.get('is_compliant', True))
        dll_violated = bool(compliance.get('dll_violated', False))
        mll_violated = bool(compliance.get('mll_violated', False))
        
        snapshot = {
            "account_id": str(state.get('account_id') or account_id),
            "account_name": account_name,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "balance": float(state.get('current_balance') or 0.0),
            "start_balance": float(state.get('starting_balance') or 0.0),
            "highest_eod_balance": float(state.get('highest_eod_balance') or 0.0),
            "realized_pnl": float(state.get('realized_pnl') or 0.0),
            "unrealized_pnl": float(state.get('unrealized_pnl') or 0.0),
            "total_pnl": float(state.get('total_pnl') or 0.0),
            "trailing_loss": trailing_loss,
            "compliance": is_compliant,
            "dll": {
                "limit": dll_limit if dll_limit else None,
                "used": dll_used,
                "remaining": dll_remaining,
                "violated": dll_violated,
                "pct": dll_pct,
            },
            "mll": {
                "limit": mll_limit if mll_limit else None,
                "used": mll_used,
                "remaining": mll_remaining,
                "violated": mll_violated,
                "pct": mll_pct,
            },
            "violations": violations,
        }
        
        # Initialize or update last known flags
        last_flags = self._risk_flags.setdefault(str(account_id), {
            "dll_violated": dll_violated,
            "mll_violated": mll_violated,
            "is_compliant": is_compliant,
        })
        
        if emit_events:
            if last_flags["dll_violated"] != dll_violated:
                if dll_violated:
                    self._record_risk_event(str(account_id), "Daily loss limit violated", level="error", meta={"used": dll_used, "limit": dll_limit})
                    self._record_notification(str(account_id), "risk_alert", "Daily loss limit violated", level="error", meta={"used": dll_used, "limit": dll_limit})
                else:
                    self._record_risk_event(str(account_id), "Daily loss back within limit", level="success")
                    self._record_notification(str(account_id), "risk_alert", "Daily loss back within limit", level="success")
            if last_flags["mll_violated"] != mll_violated:
                if mll_violated:
                    self._record_risk_event(str(account_id), "Maximum loss limit violated", level="error", meta={"used": mll_used, "limit": mll_limit})
                    self._record_notification(str(account_id), "risk_alert", "Maximum loss limit violated", level="error", meta={"used": mll_used, "limit": mll_limit})
                else:
                    self._record_risk_event(str(account_id), "Maximum loss back within limit", level="success")
                    self._record_notification(str(account_id), "risk_alert", "Maximum loss back within limit", level="success")
            if last_flags["is_compliant"] != is_compliant:
                if is_compliant:
                    self._record_risk_event(str(account_id), "Account returned to compliant status", level="success")
                    self._record_notification(str(account_id), "risk_alert", "Account returned to compliant status", level="success")
                else:
                    self._record_risk_event(str(account_id), "Account marked non-compliant", level="warning")
                    self._record_notification(str(account_id), "risk_alert", "Account marked non-compliant", level="warning")
        
        # Update cached flags
        self._risk_flags[str(account_id)] = {
            "dll_violated": dll_violated,
            "mll_violated": mll_violated,
            "is_compliant": is_compliant,
        }
        
        events = self._risk_events.get(str(account_id), [])
        snapshot["events"] = events[-10:]
        return snapshot
    
    async def _broadcast_risk_snapshot(self, account_id: Optional[str] = None) -> None:
        """Broadcast current risk snapshot over WebSocket."""
        snapshot = self._collect_risk_snapshot(account_id, emit_events=True)
        if not snapshot:
            return
        await self.broadcast_to_websockets({
            "type": "risk_update",
            "data": snapshot,
            "timestamp": time.time()
        })
    
    async def handle_get_risk(self, request: web.Request) -> web.Response:
        """Return current risk metrics for the selected or requested account."""
        account_scope = request.rel_url.query.get('account_id')
        snapshot = self._collect_risk_snapshot(account_scope, emit_events=False)
        if not snapshot:
            return web.json_response({"error": "risk data unavailable"}, status=503)
        return web.json_response(snapshot)
    
    async def handle_get_notifications(self, request: web.Request) -> web.Response:
        """Get recent notifications for the selected or requested account."""
        try:
            account_id = request.rel_url.query.get('account_id') or self._get_selected_account_id()
            if not account_id:
                return web.json_response({"notifications": [], "account_id": None})
            
            notifications = self._notifications.get(str(account_id), [])
            # Return most recent 50 notifications
            recent = notifications[-50:] if len(notifications) > 50 else notifications
            return web.json_response({
                "notifications": recent,
                "account_id": str(account_id),
                "total": len(notifications)
            })
        except Exception as e:
            logger.error(f"Error getting notifications: {e}")
            return web.json_response({"error": str(e)}, status=500)
    
    def _record_notification(self, account_id: str, notification_type: str, message: str, 
                             level: str = "info", meta: Optional[Dict[str, Any]] = None) -> None:
        """Record a notification for the specified account."""
        notifications = self._notifications.setdefault(str(account_id), [])
        notification = {
            "id": str(uuid4()),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "type": notification_type,  # 'order_fill', 'position_close', 'risk_alert', 'strategy_status', etc.
            "message": message,
            "level": level,  # 'info', 'success', 'warning', 'error'
        }
        if meta:
            notification["meta"] = meta
        notifications.append(notification)
        # Keep only last 100 notifications per account
        if len(notifications) > 100:
            del notifications[:-100]
        
        # Broadcast via WebSocket
        asyncio.create_task(self._broadcast_notification(str(account_id), notification))
    
    async def _broadcast_notification(self, account_id: str, notification: Dict[str, Any]) -> None:
        """Broadcast a single notification over WebSocket."""
        await self.broadcast_to_websockets({
            "type": "notification",
            "data": {
                "account_id": account_id,
                "notification": notification
            },
            "timestamp": time.time()
        })
    
    async def handle_favicon(self, request: web.Request) -> web.Response:
        """Return an empty favicon to silence browser 404s."""
        return web.Response(status=204)
    
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
    
    async def handle_export_trades_csv(self, request: web.Request) -> web.Response:
        """Export trades to CSV."""
        try:
            account_id = request.rel_url.query.get('account_id') or self._get_selected_account_id()
            start_date = request.rel_url.query.get('start')
            end_date = request.rel_url.query.get('end')
            
            csv_content = await self.dashboard_api.export_trades_to_csv(
                account_id=account_id,
                start_date=start_date,
                end_date=end_date
            )
            
            if not csv_content:
                return web.json_response({"error": "No trades to export"}, status=404)
            
            # Generate filename
            from datetime import datetime
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"trades_export_{timestamp}.csv"
            
            # Return CSV as download
            response = web.Response(
                body=csv_content.encode('utf-8'),
                headers={
                    'Content-Type': 'text/csv',
                    'Content-Disposition': f'attachment; filename="{filename}"',
                }
            )
            return response
        except Exception as e:
            logger.error(f"Error exporting trades CSV: {e}")
            return web.json_response({"error": str(e)}, status=500)
    
    async def handle_export_performance_csv(self, request: web.Request) -> web.Response:
        """Export performance history to CSV."""
        try:
            account_id = request.rel_url.query.get('account_id') or self._get_selected_account_id()
            interval = request.rel_url.query.get('interval', 'day')
            start_date = request.rel_url.query.get('start')
            end_date = request.rel_url.query.get('end')
            
            csv_content = await self.dashboard_api.export_performance_to_csv(
                account_id=account_id,
                interval=interval,
                start_date=start_date,
                end_date=end_date
            )
            
            if not csv_content:
                return web.json_response({"error": "No performance data to export"}, status=404)
            
            # Generate filename
            from datetime import datetime
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"performance_{interval}_{timestamp}.csv"
            
            # Return CSV as download
            response = web.Response(
                body=csv_content.encode('utf-8'),
                headers={
                    'Content-Type': 'text/csv',
                    'Content-Disposition': f'attachment; filename="{filename}"',
                }
            )
            return response
        except Exception as e:
            logger.error(f"Error exporting performance CSV: {e}")
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
                    result = await self.trading_bot.check_order_fills()
                    # Record notifications for new fills
                    if result and result.get('new_fills'):
                        account_id = self._get_selected_account_id()
                        if account_id:
                            for order_id in result['new_fills']:
                                self._record_notification(
                                    account_id,
                                    'order_fill',
                                    f"Order {order_id} filled",
                                    level='success'
                                )
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
            
            # Start scheduled task manager (for strategy restarts)
            if self.scheduled_tasks:
                await self.scheduled_tasks.start()
                next_restart = self.scheduled_tasks.get_next_restart_time()
                if next_restart:
                    logger.info(f"📅 Next strategy restart scheduled: {next_restart.strftime('%Y-%m-%d %H:%M:%S %Z')}")
            
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
            
            # Stop scheduled task manager
            if hasattr(self, 'scheduled_tasks') and self.scheduled_tasks:
                await self.scheduled_tasks.stop()
            
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
    
    # Determine preferred account order: env override > persisted default > first account
    env_account_id = os.getenv('TOPSETPX_ACCOUNT_ID')
    persisted_account_id = None
    if getattr(trading_bot, 'db', None):
        try:
            settings = trading_bot.db.get_dashboard_settings()
            persisted_account_id = settings.get('defaultAccount') or settings.get('default_account')
            if persisted_account_id:
                logger.info(f"📝 Persisted default account from settings: {persisted_account_id}")
        except Exception as settings_err:
            logger.warning(f"⚠️  Failed to load persisted settings: {settings_err}")
    
    account_choice = env_account_id or persisted_account_id
    if account_choice:
        selected_account = next((acc for acc in accounts if str(acc['id']) == str(account_choice)), None)
        if selected_account:
            trading_bot.selected_account = selected_account
            logger.info(f"✅ Selected account: {selected_account['name']} (source={'env' if env_account_id else 'settings'})")
        else:
            logger.error(f"❌ Preferred account ID {account_choice} not found among available accounts")
            sys.exit(1)
    else:
        # Fallback: select first account
        trading_bot.selected_account = accounts[0]
        logger.info(f"✅ Auto-selected account: {accounts[0]['name']}")
    
    # Apply persisted strategy state (if available)
    if hasattr(trading_bot, 'strategy_manager'):
        await trading_bot.strategy_manager.apply_persisted_states()
        # Auto-start strategies enabled via environment variables
        await trading_bot.strategy_manager.auto_start_enabled_strategies()

    # Start webhook server
    server = AsyncWebhookServer(trading_bot, host=host, port=port)
    await server.run()


if __name__ == '__main__':
    asyncio.run(main())

