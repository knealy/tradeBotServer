"""
WebSocket server for real-time dashboard updates
Handles WebSocket connections, authentication, and broadcasting updates
"""

import asyncio
import json
import logging
import os
import time
from datetime import datetime
from typing import Set, Dict, Any, Optional
import websockets
from websockets.server import WebSocketServerProtocol
from auth import extract_token_from_request, validate_token

logger = logging.getLogger(__name__)

class WebSocketServer:
    """WebSocket server for real-time dashboard updates"""
    
    def __init__(self, trading_bot, webhook_server, host: str = "0.0.0.0", port: int = None):
        self.trading_bot = trading_bot
        self.webhook_server = webhook_server
        self.host = host
        self.port = port or 8080  # Use same port as HTTP server
        self.clients: Set[WebSocketServerProtocol] = set()
        self.server = None
        self.running = False
        
    async def start(self):
        """Start the WebSocket server"""
        try:
            self.server = await websockets.serve(
                self.handle_client,
                self.host,
                self.port,
                ping_interval=30,
                ping_timeout=10
            )
            self.running = True
            logger.info(f"WebSocket server started on {self.host}:{self.port}")
            
            # Start periodic broadcasts
            asyncio.create_task(self.periodic_broadcasts())
            
        except Exception as e:
            logger.error(f"Failed to start WebSocket server: {e}")
            raise
    
    async def stop(self):
        """Stop the WebSocket server"""
        self.running = False
        if self.server:
            self.server.close()
            await self.server.wait_closed()
        logger.info("WebSocket server stopped")
    
    async def handle_client(self, websocket: WebSocketServerProtocol, path: str):
        """Handle a new WebSocket client connection"""
        client_ip = websocket.remote_address[0] if websocket.remote_address else "unknown"
        logger.info(f"WebSocket client connected from {client_ip}")
        
        try:
            # Extract token from path query parameters
            if '?' in path:
                path_part, query_part = path.split('?', 1)
                query_params = dict(param.split('=') for param in query_part.split('&') if '=' in param)
            else:
                query_params = {}
            
            # Authenticate client
            token = query_params.get('token')
            if not token or not validate_token(token):
                await websocket.send(json.dumps({
                    "type": "auth_error",
                    "message": "Authentication required",
                    "timestamp": datetime.now().isoformat()
                }))
                await websocket.close(code=1008, reason="Authentication required")
                return
            
            # Add client to active connections
            self.clients.add(websocket)
            logger.info(f"Authenticated WebSocket client from {client_ip}")
            
            # Send initial connection confirmation
            await websocket.send(json.dumps({
                "type": "connected",
                "message": "WebSocket connection established",
                "timestamp": datetime.now().isoformat()
            }))
            
            # Handle client messages
            async for message in websocket:
                try:
                    data = json.loads(message)
                    await self.handle_client_message(websocket, data)
                except json.JSONDecodeError:
                    await websocket.send(json.dumps({
                        "type": "error",
                        "message": "Invalid JSON format",
                        "timestamp": datetime.now().isoformat()
                    }))
                except Exception as e:
                    logger.error(f"Error handling client message: {e}")
                    await websocket.send(json.dumps({
                        "type": "error",
                        "message": str(e),
                        "timestamp": datetime.now().isoformat()
                    }))
                    
        except websockets.exceptions.ConnectionClosed:
            logger.info(f"WebSocket client disconnected from {client_ip}")
        except Exception as e:
            logger.error(f"WebSocket client error: {e}")
        finally:
            # Remove client from active connections
            self.clients.discard(websocket)
    
    async def handle_client_message(self, websocket: WebSocketServerProtocol, data: Dict[str, Any]):
        """Handle incoming messages from clients"""
        message_type = data.get('type')
        
        if message_type == 'ping':
            # Respond to ping with pong
            await websocket.send(json.dumps({
                "type": "pong",
                "timestamp": datetime.now().isoformat()
            }))
            
        elif message_type == 'subscribe':
            # Client wants to subscribe to specific updates
            subscriptions = data.get('subscriptions', [])
            logger.info(f"Client subscribed to: {subscriptions}")
            # For now, we broadcast everything to all clients
            # In the future, we could implement selective subscriptions
            
        elif message_type == 'unsubscribe':
            # Client wants to unsubscribe from specific updates
            subscriptions = data.get('subscriptions', [])
            logger.info(f"Client unsubscribed from: {subscriptions}")
            
        else:
            logger.warning(f"Unknown message type from client: {message_type}")
    
    async def broadcast(self, message_type: str, data: Any):
        """Broadcast a message to all connected clients"""
        if not self.clients:
            return
        
        message = {
            "type": message_type,
            "data": data,
            "timestamp": datetime.now().isoformat()
        }
        
        message_json = json.dumps(message)
        disconnected_clients = set()
        
        for client in self.clients:
            try:
                await client.send(message_json)
            except websockets.exceptions.ConnectionClosed:
                disconnected_clients.add(client)
            except Exception as e:
                logger.warning(f"Failed to send message to client: {e}")
                disconnected_clients.add(client)
        
        # Remove disconnected clients
        self.clients -= disconnected_clients
        
        if disconnected_clients:
            logger.info(f"Removed {len(disconnected_clients)} disconnected clients")
    
    async def broadcast_account_update(self, account_data: Dict[str, Any]):
        """Broadcast account information update"""
        await self.broadcast("account_update", account_data)
    
    async def broadcast_position_update(self, positions: list):
        """Broadcast positions update"""
        await self.broadcast("position_update", positions)
    
    async def broadcast_order_update(self, orders: list):
        """Broadcast orders update"""
        await self.broadcast("order_update", orders)
    
    async def broadcast_trade_fill(self, trade_data: Dict[str, Any]):
        """Broadcast trade execution notification"""
        await self.broadcast("trade_fill", trade_data)
    
    async def broadcast_stats_update(self, stats_data: Dict[str, Any]):
        """Broadcast performance statistics update"""
        await self.broadcast("stats_update", stats_data)
    
    async def broadcast_log_message(self, log_data: Dict[str, Any]):
        """Broadcast system log message"""
        await self.broadcast("log_message", log_data)
    
    async def broadcast_health_update(self, health_data: Dict[str, Any]):
        """Broadcast server health update"""
        await self.broadcast("health_update", health_data)
    
    async def periodic_broadcasts(self):
        """Send periodic updates to all connected clients"""
        while self.running:
            try:
                if self.clients:
                    # Get current account info
                    account = self.trading_bot.selected_account
                    if account:
                        await self.broadcast_account_update({
                            "account_id": account.get('id'),
                            "account_name": account.get('name'),
                            "balance": account.get('balance', 0),
                            "status": account.get('status'),
                            "currency": account.get('currency', 'USD')
                        })
                    
                    # Get current positions (simplified for now)
                    await self.broadcast_position_update([])
                    
                    # Get current orders (simplified for now)
                    await self.broadcast_order_update([])
                    
                    # Send health update
                    await self.broadcast_health_update({
                        "status": "healthy",
                        "connected_clients": len(self.clients),
                        "server_time": datetime.now().isoformat()
                    })
                
                # Wait 30 seconds before next broadcast
                await asyncio.sleep(30)
                
            except Exception as e:
                logger.error(f"Error in periodic broadcasts: {e}")
                await asyncio.sleep(5)  # Wait before retrying
    
    def get_connection_count(self) -> int:
        """Get the number of active WebSocket connections"""
        return len(self.clients)
    
    def is_running(self) -> bool:
        """Check if the WebSocket server is running"""
        return self.running
