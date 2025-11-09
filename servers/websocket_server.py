import asyncio
import json
import logging
import os
import sys
import time
from typing import Set, Dict, Any
import websockets
from websockets.server import WebSocketServerProtocol

# Add project root to Python path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from auth import validate_token

logger = logging.getLogger(__name__)

class WebSocketServer:
    """Professional WebSocket server for real-time trading dashboard updates"""
    
    def __init__(self, trading_bot, webhook_server, host: str = "0.0.0.0", port: int = 8081):
        self.trading_bot = trading_bot
        self.webhook_server = webhook_server
        self.host = host
        self.port = port
        self.clients: Set[WebSocketServerProtocol] = set()
        self.server = None
        self.running = False
        self.broadcast_queue = asyncio.Queue()
        self.last_broadcast = {}
        
    async def start(self):
        """Start the WebSocket server with professional features"""
        self.running = True
        logger.info(f"🚀 Starting professional WebSocket server on {self.host}:{self.port}")
        
        try:
            self.server = await websockets.serve(
                self._websocket_handler,
                self.host,
                self.port,
                subprotocols=["trading-dashboard-v1"],
                ping_interval=30,
                ping_timeout=10,
                close_timeout=10
            )
            logger.info("✅ WebSocket server started successfully!")
            
            # Start background tasks
            asyncio.create_task(self._broadcast_worker())
            asyncio.create_task(self._periodic_updates())
            
            await self.server.wait_closed()
        except Exception as e:
            logger.error(f"❌ WebSocket server failed to start: {e}")
            raise

    async def stop(self):
        """Stop the WebSocket server gracefully"""
        self.running = False
        if self.server:
            self.server.close()
            await self.server.wait_closed()
            logger.info("🛑 WebSocket server stopped")

    async def _websocket_handler(self, websocket: WebSocketServerProtocol, path: str):
        """Handle new WebSocket connections with professional authentication"""
        client_ip = websocket.remote_address[0] if websocket.remote_address else "unknown"
        logger.info(f"🔌 New WebSocket connection from {client_ip} to {path}")

        # Extract token from query parameters
        token = None
        if '?' in path:
            query_string = path.split('?', 1)[1]
            query_params = dict(param.split('=') for param in query_string.split('&') if '=' in param)
            token = query_params.get('token')

        # Authenticate connection (optional for local dev)
        # For local development, allow connections without token
        # For production, require token
        require_auth = os.getenv('WEBSOCKET_REQUIRE_AUTH', 'false').lower() in ('true', '1', 'yes')
        if require_auth:
            if not token or not validate_token(token):
                logger.warning(f"🚫 WebSocket connection denied from {client_ip}: Invalid token")
                await websocket.send(json.dumps({
                    "type": "auth_error", 
                    "message": "Authentication failed",
                    "timestamp": time.time()
                }))
                await websocket.close(code=1008, reason="Authentication failed")
                return
        else:
            logger.info(f"🔓 WebSocket authentication disabled (local dev mode)")

        # Add client to active connections
        self.clients.add(websocket)
        logger.info(f"✅ WebSocket client authenticated: {client_ip}. Total clients: {len(self.clients)}")
        
        # Send welcome message with current data
        await self._send_welcome_data(websocket)

        try:
            # Handle incoming messages
            async for message in websocket:
                await self._handle_client_message(websocket, message)
                
        except websockets.exceptions.ConnectionClosedOK:
            logger.info(f"🔌 WebSocket connection closed normally for {client_ip}")
        except websockets.exceptions.ConnectionClosedError as e:
            logger.warning(f"⚠️ WebSocket connection closed with error for {client_ip}: {e}")
        except Exception as e:
            logger.error(f"❌ Unexpected WebSocket error for {client_ip}: {e}")
        finally:
            self.clients.discard(websocket)
            logger.info(f"👋 WebSocket client disconnected: {client_ip}. Total clients: {len(self.clients)}")

    async def _send_welcome_data(self, websocket: WebSocketServerProtocol):
        """Send initial dashboard data to new client"""
        try:
            # Send connection confirmation
            await websocket.send(json.dumps({
                "type": "connected",
                "message": "Connected to TopStepX Trading Dashboard",
                "timestamp": time.time(),
                "server_time": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
            }))
            
            # Send current account info
            if self.trading_bot.selected_account:
                await websocket.send(json.dumps({
                    "type": "account_update",
                    "data": {
                        "account_id": self.trading_bot.selected_account.get('id'),
                        "account_name": self.trading_bot.selected_account.get('name'),
                        "balance": self.trading_bot.selected_account.get('balance', 0),
                        "status": self.trading_bot.selected_account.get('status'),
                        "currency": self.trading_bot.selected_account.get('currency', 'USD')
                    },
                    "timestamp": time.time()
                }))
            
            # Send current positions
            try:
                positions = await self.trading_bot.get_open_positions(
                    account_id=self.trading_bot.selected_account.get('id') if self.trading_bot.selected_account else None
                )
                await websocket.send(json.dumps({
                    "type": "position_update",
                    "data": positions,
                    "timestamp": time.time()
                }))
            except Exception as e:
                logger.warning(f"Failed to get positions for welcome data: {e}")
            
            # Send current orders
            try:
                orders = await self.trading_bot.get_open_orders(
                    account_id=self.trading_bot.selected_account.get('id') if self.trading_bot.selected_account else None
                )
                await websocket.send(json.dumps({
                    "type": "order_update", 
                    "data": orders,
                    "timestamp": time.time()
                }))
            except Exception as e:
                logger.warning(f"Failed to get orders for welcome data: {e}")
                
        except Exception as e:
            logger.error(f"Error sending welcome data: {e}")

    async def _handle_client_message(self, websocket: WebSocketServerProtocol, message: str):
        """Handle messages from dashboard clients"""
        try:
            data = json.loads(message)
            action = data.get('action')
            payload = data.get('payload', {})

            if action == 'ping':
                await websocket.send(json.dumps({
                    "type": "pong",
                    "timestamp": time.time()
                }))
            elif action == 'subscribe':
                # Client wants to subscribe to specific updates
                await websocket.send(json.dumps({
                    "type": "subscription_confirmed",
                    "subscribed_to": payload.get('types', ['all']),
                    "timestamp": time.time()
                }))
            else:
                logger.info(f"Received unknown action from client: {action}")
                
        except json.JSONDecodeError:
            logger.warning(f"Received invalid JSON from client: {message}")
        except Exception as e:
            logger.error(f"Error handling client message: {e}")

    async def _broadcast_worker(self):
        """Background worker to process broadcast queue"""
        while self.running:
            try:
                # Wait for broadcast messages
                message = await asyncio.wait_for(self.broadcast_queue.get(), timeout=1.0)
                
                if self.clients:
                    # Create tasks for all clients
                    tasks = []
                    for client in list(self.clients):
                        if not client.closed:
                            tasks.append(self._send_to_client(client, message))
                    
                    # Send to all clients concurrently
                    if tasks:
                        await asyncio.gather(*tasks, return_exceptions=True)
                        
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                logger.error(f"Error in broadcast worker: {e}")

    async def _send_to_client(self, client: WebSocketServerProtocol, message: Dict[str, Any]):
        """Send message to a specific client"""
        try:
            await client.send(json.dumps(message))
        except websockets.exceptions.ConnectionClosed:
            # Remove closed connections
            self.clients.discard(client)
        except Exception as e:
            logger.warning(f"Failed to send message to client: {e}")

    async def _periodic_updates(self):
        """Send periodic updates to all connected clients"""
        while self.running:
            try:
                if self.clients:
                    # Send comprehensive account update
                    if self.trading_bot.selected_account:
                        try:
                            # Get current balance
                            balance = await self.trading_bot.get_account_balance()
                            await self.broadcast({
                                "type": "account_update",
                                "data": {
                                    "account_id": self.trading_bot.selected_account.get('id'),
                                    "account_name": self.trading_bot.selected_account.get('name'),
                                    "balance": balance,
                                    "status": self.trading_bot.selected_account.get('status'),
                                    "currency": self.trading_bot.selected_account.get('currency', 'USD'),
                                    "account_type": self.trading_bot.selected_account.get('account_type', 'unknown')
                                },
                                "timestamp": time.time()
                            })
                        except Exception as e:
                            logger.warning(f"Failed to get account balance for broadcast: {e}")
                    
                    # Send positions update
                    try:
                        positions = await self.trading_bot.get_open_positions()
                        await self.broadcast({
                            "type": "position_update",
                            "data": positions,
                            "timestamp": time.time()
                        })
                    except Exception as e:
                        logger.warning(f"Failed to get positions for broadcast: {e}")
                    
                    # Send orders update
                    try:
                        orders = await self.trading_bot.get_open_orders()
                        await self.broadcast({
                            "type": "order_update",
                            "data": orders,
                            "timestamp": time.time()
                        })
                    except Exception as e:
                        logger.warning(f"Failed to get orders for broadcast: {e}")
                    
                    # Send performance stats
                    try:
                        # Get recent trade history for stats
                        history = await self.trading_bot.get_order_history(limit=50)
                        total_trades = len(history)
                        winning_trades = len([t for t in history if t.get('pnl', 0) > 0])
                        total_pnl = sum(t.get('pnl', 0) for t in history)
                        win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0
                        
                        await self.broadcast({
                            "type": "stats_update",
                            "data": {
                                "total_trades": total_trades,
                                "winning_trades": winning_trades,
                                "win_rate": round(win_rate, 2),
                                "total_pnl": total_pnl,
                                "daily_pnl": total_pnl  # Simplified for now
                            },
                            "timestamp": time.time()
                        })
                    except Exception as e:
                        logger.warning(f"Failed to get performance stats for broadcast: {e}")
                    
                    # Send health status
                    health_status = {"status": "healthy", "uptime": time.time()}
                    if hasattr(self.webhook_server, 'server_start_time') and self.webhook_server.server_start_time:
                        from datetime import datetime
                        health_status["uptime_seconds"] = (datetime.now() - self.webhook_server.server_start_time).total_seconds()
                    await self.broadcast({
                        "type": "health_update",
                        "data": health_status,
                        "timestamp": time.time()
                    })
                
                # Wait 10 seconds before next update (more frequent updates)
                await asyncio.sleep(10)
                
            except Exception as e:
                logger.error(f"Error in periodic updates: {e}")
                await asyncio.sleep(5)

    async def broadcast(self, message: Dict[str, Any]):
        """Broadcast message to all connected clients"""
        if self.running:
            await self.broadcast_queue.put(message)

    async def broadcast_trade_fill(self, trade_data: Dict[str, Any]):
        """Broadcast trade fill notification"""
        await self.broadcast({
            "type": "trade_fill",
            "data": trade_data,
            "timestamp": time.time()
        })

    async def broadcast_position_update(self, positions: list):
        """Broadcast position updates"""
        await self.broadcast({
            "type": "position_update",
            "data": positions,
            "timestamp": time.time()
        })

    async def broadcast_order_update(self, orders: list):
        """Broadcast order updates"""
        await self.broadcast({
            "type": "order_update",
            "data": orders,
            "timestamp": time.time()
        })

    async def broadcast_account_update(self, account_data: Dict[str, Any]):
        """Broadcast account updates"""
        await self.broadcast({
            "type": "account_update",
            "data": account_data,
            "timestamp": time.time()
        })

    async def broadcast_log_message(self, log_data: Dict[str, Any]):
        """Broadcast log messages"""
        await self.broadcast({
            "type": "log_message",
            "data": log_data,
            "timestamp": time.time()
        })

    def get_connection_count(self) -> int:
        """Get number of active connections"""
        return len(self.clients)

    def is_running(self) -> bool:
        """Check if WebSocket server is running"""
        return self.running