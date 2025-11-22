"""
Dashboard REST API Server for React Frontend

Provides comprehensive REST API endpoints for the React dashboard.
Integrates with trading bot, strategies, and metrics.
"""

import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from aiohttp import web
from aiohttp.web import Request, Response
from aiohttp_cors import setup as cors_setup, ResourceOptions
import aiohttp

# Add project root to Python path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trading_bot import TopStepXTradingBot
from servers.dashboard import DashboardAPI
from infrastructure.performance_metrics import get_metrics_tracker
from infrastructure.database import get_database

logger = logging.getLogger(__name__)
if os.getenv("ACCESS_LOG_VERBOSE", "false").lower() not in ("1", "true", "yes", "on"):
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)


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


class DashboardAPIServer:
    """
    REST API server for React dashboard.
    
    Provides endpoints for:
    - Account management
    - Positions & orders
    - Strategy control
    - Performance metrics
    - Real-time WebSocket
    """
    
    def __init__(self, trading_bot: TopStepXTradingBot, host: str = '0.0.0.0', port: int = 8080):
        self.trading_bot = trading_bot
        self.host = host
        self.port = port
        # Create application with no-cache middleware to prevent stale data in frontend
        self.app = web.Application(middlewares=[no_cache_middleware])
        self.dashboard_api = DashboardAPI(trading_bot, None)
        self.metrics = get_metrics_tracker(db=getattr(trading_bot, 'db', None))
        
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
        
        logger.info(f"✅ Dashboard API server initialized ({host}:{port})")
    
    def _setup_routes(self):
        """Setup all API routes."""
        
        # Health & Status
        self.app.router.add_get('/health', self.handle_health)
        self.app.router.add_get('/api/health', self.handle_health)
        
        # Account Management
        self.app.router.add_get('/api/accounts', self.handle_get_accounts)
        self.app.router.add_get('/api/account/info', self.handle_get_account_info)
        self.app.router.add_post('/api/account/switch', self.handle_switch_account)
        
        # Positions
        self.app.router.add_get('/api/positions', self.handle_get_positions)
        self.app.router.add_post('/api/positions/{position_id}/close', self.handle_close_position)
        self.app.router.add_post('/api/positions/flatten', self.handle_flatten_positions)
        
        # Orders
        self.app.router.add_get('/api/orders', self.handle_get_orders)
        self.app.router.add_post('/api/orders/{order_id}/cancel', self.handle_cancel_order)
        self.app.router.add_post('/api/orders/cancel-all', self.handle_cancel_all_orders)
        self.app.router.add_post('/api/orders/place', self.handle_place_order)
        
        # Strategies
        self.app.router.add_get('/api/strategies', self.handle_get_strategies)
        self.app.router.add_get('/api/strategies/status', self.handle_get_strategy_status)
        self.app.router.add_post('/api/strategies/{name}/start', self.handle_start_strategy)
        self.app.router.add_post('/api/strategies/{name}/stop', self.handle_stop_strategy)
        
        # Performance Metrics
        self.app.router.add_get('/api/metrics', self.handle_get_metrics)
        
        # Trade History
        self.app.router.add_get('/api/trades', self.handle_get_trades)
        self.app.router.add_get('/api/performance', self.handle_get_performance)
        self.app.router.add_get('/api/performance/history', self.handle_get_performance_history)
        self.app.router.add_get('/api/history', self.handle_get_historical_data)
        
        logger.info("✅ Dashboard API routes configured")
    
    # Health Check
    async def handle_health(self, request: Request) -> Response:
        """Health check endpoint."""
        try:
            is_authenticated = self.trading_bot.session_token is not None
            selected_account = self.trading_bot.selected_account
            
            return web.json_response({
                "status": "healthy" if is_authenticated else "degraded",
                "authenticated": is_authenticated,
                "selected_account": selected_account.get('name') if selected_account else None,
                "account_id": selected_account.get('id') if selected_account else None,
                "timestamp": datetime.now().isoformat()
            })
        except Exception as e:
            logger.error(f"Health check error: {e}")
            return web.json_response({"status": "unhealthy", "error": str(e)}, status=500)
    
    # Account Endpoints
    async def handle_get_accounts(self, request: Request) -> Response:
        """Get all available accounts."""
        try:
            accounts = await self.dashboard_api.get_accounts()
            return web.json_response(accounts)
        except Exception as e:
            logger.error(f"Error getting accounts: {e}")
            return web.json_response({"error": str(e)}, status=500)
    
    async def handle_get_account_info(self, request: Request) -> Response:
        """Get current account information."""
        try:
            account_info = await self.dashboard_api.get_account_info()
            if "error" in account_info:
                return web.json_response(account_info, status=400)
            return web.json_response(account_info)
        except Exception as e:
            logger.error(f"Error getting account info: {e}")
            return web.json_response({"error": str(e)}, status=500)
    
    async def handle_switch_account(self, request: Request) -> Response:
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
    
    # Position Endpoints
    async def handle_get_positions(self, request: Request) -> Response:
        """Get open positions."""
        try:
            positions = await self.dashboard_api.get_positions()
            return web.json_response(positions)
        except Exception as e:
            logger.error(f"Error getting positions: {e}")
            return web.json_response({"error": str(e)}, status=500)
    
    async def handle_close_position(self, request: Request) -> Response:
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
    
    async def handle_flatten_positions(self, request: Request) -> Response:
        """Flatten all positions."""
        try:
            result = await self.dashboard_api.flatten_all_positions()
            if "error" in result:
                return web.json_response(result, status=400)
            return web.json_response(result)
        except Exception as e:
            logger.error(f"Error flattening positions: {e}")
            return web.json_response({"error": str(e)}, status=500)
    
    # Order Endpoints
    async def handle_get_orders(self, request: Request) -> Response:
        """Get open orders."""
        try:
            orders = await self.dashboard_api.get_orders()
            return web.json_response(orders)
        except Exception as e:
            logger.error(f"Error getting orders: {e}")
            return web.json_response({"error": str(e)}, status=500)
    
    async def handle_cancel_order(self, request: Request) -> Response:
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
    
    async def handle_cancel_all_orders(self, request: Request) -> Response:
        """Cancel all open orders."""
        try:
            result = await self.dashboard_api.cancel_all_orders()
            if "error" in result:
                return web.json_response(result, status=400)
            return web.json_response(result)
        except Exception as e:
            logger.error(f"Error canceling all orders: {e}")
            return web.json_response({"error": str(e)}, status=500)
    
    async def handle_place_order(self, request: Request) -> Response:
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
    
    # Strategy Endpoints
    async def handle_get_strategies(self, request: Request) -> Response:
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
    
    async def handle_get_strategy_status(self, request: Request) -> Response:
        """Get status of all strategies."""
        try:
            if not hasattr(self.trading_bot, 'strategy_manager'):
                return web.json_response({"error": "Strategy manager not available"}, status=503)
            
            status = self.trading_bot.strategy_manager.get_status()
            return web.json_response(status)
        except Exception as e:
            logger.error(f"Error getting strategy status: {e}")
            return web.json_response({"error": str(e)}, status=500)
    
    async def handle_start_strategy(self, request: Request) -> Response:
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
    
    async def handle_stop_strategy(self, request: Request) -> Response:
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
    
    # Metrics Endpoint
    async def handle_get_metrics(self, request: Request) -> Response:
        """Get performance metrics."""
        try:
            metrics = self.metrics.get_full_report()
            return web.json_response(metrics)
        except Exception as e:
            logger.error(f"Error getting metrics: {e}")
            return web.json_response({"error": str(e)}, status=500)
    
    # Trade History
    async def handle_get_trades(self, request: Request) -> Response:
        """Get trade history."""
        try:
            params = request.rel_url.query
            account_id = params.get('account_id')
            start_date = params.get('start') or params.get('start_date')
            end_date = params.get('end') or params.get('end_date')
            symbol = params.get('symbol')
            trade_type = params.get('type', 'filled')
            limit = int(params.get('limit', '50'))
            cursor = params.get('cursor')

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
    
    # Performance Stats
    async def handle_get_performance(self, request: Request) -> Response:
        """Get performance statistics."""
        try:
            stats = await self.dashboard_api.get_performance_stats()
            if "error" in stats:
                return web.json_response(stats, status=400)
            return web.json_response(stats)
        except Exception as e:
            logger.error(f"Error getting performance: {e}")
            return web.json_response({"error": str(e)}, status=500)

    async def handle_get_performance_history(self, request: Request) -> Response:
        try:
            params = request.rel_url.query
            account_id = params.get('account_id')
            interval = params.get('interval', 'day')
            start_date = params.get('start') or params.get('start_date')
            end_date = params.get('end') or params.get('end_date')
            history = await self.dashboard_api.get_performance_history(
                account_id=account_id,
                interval=interval,
                start_date=start_date,
                end_date=end_date,
            )
            status = 200 if 'error' not in history else 400
            return web.json_response(history, status=status)
        except Exception as e:
            logger.error(f"Error getting performance history: {e}")
            return web.json_response({"error": str(e)}, status=500)

    async def handle_get_historical_data(self, request: Request) -> Response:
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

    async def run(self):
        """Run the API server."""
        runner = web.AppRunner(self.app)
        await runner.setup()
        site = web.TCPSite(runner, self.host, self.port)
        await site.start()
        
        logger.info(f"🚀 Dashboard API server running on http://{self.host}:{self.port}")
        logger.info("📡 Available endpoints:")
        logger.info("   GET  /health")
        logger.info("   GET  /api/accounts")
        logger.info("   GET  /api/account/info")
        logger.info("   GET  /api/positions")
        logger.info("   GET  /api/orders")
        logger.info("   GET  /api/strategies")
        logger.info("   GET  /api/strategies/status")
        logger.info("   GET  /api/metrics")
        logger.info("   POST /api/account/switch")
        logger.info("   POST /api/strategies/{name}/start")
        logger.info("   POST /api/strategies/{name}/stop")
        
        # Keep server running
        try:
            await asyncio.Future()  # Run forever
        except KeyboardInterrupt:
            logger.info("Shutting down API server...")
            await runner.cleanup()


async def main():
    """Main entry point for dashboard API server."""
    import load_env
    load_env.load_env_file()
    
    # Initialize logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # Initialize trading bot
    api_key = os.getenv('PROJECT_X_API_KEY') or os.getenv('TOPSETPX_API_KEY')
    username = os.getenv('PROJECT_X_USERNAME') or os.getenv('TOPSETPX_USERNAME')
    
    if not api_key or not username:
        logger.error("PROJECT_X_API_KEY and PROJECT_X_USERNAME must be set")
        return
    
    bot = TopStepXTradingBot(api_key=api_key, username=username)
    
    # Authenticate
    if not await bot.authenticate():
        logger.error("Failed to authenticate")
        return
    
    # Select account (env override > persisted default > first account)
    accounts = await bot.list_accounts()
    if not accounts:
        logger.error("❌ No accounts found")
        return
    
    env_account_id = os.getenv('TOPSTEPX_ACCOUNT_ID')
    persisted_account_id = None
    if getattr(bot, 'db', None):
        try:
            settings = bot.db.get_dashboard_settings()
            persisted_account_id = settings.get('defaultAccount') or settings.get('default_account')
            if persisted_account_id:
                logger.info(f"📝 Persisted default account from settings: {persisted_account_id}")
        except Exception as settings_err:
            logger.warning(f"⚠️  Failed to load persisted settings: {settings_err}")
    
    account_choice = env_account_id or persisted_account_id
    if account_choice:
        selected_account = next((acc for acc in accounts if str(acc['id']) == str(account_choice)), None)
        if selected_account:
            bot.selected_account = selected_account
            logger.info(f"✅ Selected account: {selected_account['name']} (source={'env' if env_account_id else 'settings'})")
        else:
            logger.error(f"❌ Preferred account ID {account_choice} not found among available accounts")
            bot.selected_account = accounts[0]
            logger.info(f"✅ Fallback to first account: {accounts[0]['name']}")
    else:
        bot.selected_account = accounts[0]
        logger.info(f"✅ Auto-selected first account: {accounts[0]['name']}")
    
    # Start API server
    server = DashboardAPIServer(bot, host='0.0.0.0', port=8080)
    await server.run()


if __name__ == "__main__":
    asyncio.run(main())

