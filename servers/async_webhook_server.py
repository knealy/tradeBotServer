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
from infrastructure.task_queue import get_task_queue, TaskPriority
from infrastructure.performance_metrics import get_metrics_tracker

logger = logging.getLogger(__name__)


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
        self.app = web.Application()
        self.task_queue = get_task_queue(max_concurrent=20)  # 20 concurrent background tasks
        self.metrics = get_metrics_tracker(db=getattr(trading_bot, 'db', None))
        self.dashboard_api = DashboardAPI(trading_bot, None)
        
        # Setup CORS for React frontend
        cors = cors_setup(self.app, defaults={
            "*": ResourceOptions(
                allow_credentials=True,
                expose_headers="*",
                allow_headers="*",
                allow_methods="*"
            )
        })
        
        # Setup routes
        self._setup_routes()
        
        # Server state
        self.server_start_time = None
        self.request_count = 0
        self.webhook_count = 0
        
        logger.info(f"✅ Async webhook server initialized ({host}:{port})")
    
    def _setup_routes(self):
        """Setup HTTP routes."""
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
        
        logger.debug("Routes configured: /health, /status, /metrics, /webhook, /api/*")
    
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
            
            return web.json_response(metrics_data)
        
        except Exception as e:
            logger.error(f"Metrics endpoint failed: {e}")
            return web.json_response({"error": str(e)}, status=500)
    
    # Dashboard API Handlers
    async def handle_get_accounts(self, request: web.Request) -> web.Response:
        """Get all available accounts."""
        try:
            accounts = await self.dashboard_api.get_accounts()
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
        """Get trade history."""
        try:
            start_date = request.query.get('start_date')
            end_date = request.query.get('end_date')
            
            trades = await self.dashboard_api.get_trade_history(
                start_date=start_date,
                end_date=end_date
            )
            return web.json_response(trades)
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
        """Submit periodic background tasks."""
        # Check order fills every 30 seconds (CRITICAL priority)
        async def check_fills():
            while True:
                try:
                    await self.trading_bot.check_order_fills()
                    await asyncio.sleep(30)
                except Exception as e:
                    logger.error(f"Fill check error: {e}")
                    await asyncio.sleep(60)
        
        await self.task_queue.submit_critical(
            check_fills(),
            task_id="periodic_fill_check"
        )
        
        # Update account balance every 60 seconds (HIGH priority)
        async def update_balance():
            while True:
                try:
                    await self.trading_bot.get_account_balance()
                    await asyncio.sleep(60)
                except Exception as e:
                    logger.error(f"Balance update error: {e}")
                    await asyncio.sleep(120)
        
        await self.task_queue.submit_high(
            update_balance(),
            task_id="periodic_balance_update"
        )
        
        # Print task queue stats every 5 minutes (LOW priority)
        async def print_stats():
            while True:
                await asyncio.sleep(300)
                self.task_queue.print_stats()
        
        await self.task_queue.submit_low(
            print_stats(),
            task_id="periodic_stats"
        )
    
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
            
            # Start web server
            runner = web.AppRunner(self.app)
            await runner.setup()
            site = web.TCPSite(runner, self.host, self.port)
            await site.start()
            
            logger.info(f"✅ Async webhook server running on http://{self.host}:{self.port}")
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

