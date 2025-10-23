"""
TradingView Webhook Server for TopStepX Trading Bot

This module handles incoming webhook requests from TradingView
and executes trades based on the JSON payloads.
"""

import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime
from typing import Dict, Optional, List
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
import urllib.parse

# Import the trading bot
from trading_bot import TopStepXTradingBot
from discord_notifier import DiscordNotifier

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('webhook_server.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class WebhookHandler(BaseHTTPRequestHandler):
    """HTTP request handler for TradingView webhooks"""
    
    def __init__(self, trading_bot, webhook_server, *args, **kwargs):
        self.trading_bot = trading_bot
        self.webhook_server = webhook_server
        super().__init__(*args, **kwargs)
    
    def do_GET(self):
        """Handle GET requests for health checks"""
        if self.path == '/health':
            # Health check endpoint
            try:
                # Check if trading bot is authenticated
                is_authenticated = self.trading_bot.session_token is not None
                selected_account = self.trading_bot.selected_account
                # Consistency insights
                env_account_id = getattr(self.webhook_server, 'env_account_id', None)
                selected_account_id = selected_account.get('id') if selected_account else None
                consistent = (str(env_account_id) == str(selected_account_id)) if env_account_id and selected_account_id else None
                
                health_data = {
                    "status": "healthy" if (is_authenticated and consistent and not getattr(self.webhook_server, '_startup_blocked', False)) else "unhealthy",
                    "authenticated": is_authenticated,
                    "selected_account": selected_account.get('name') if selected_account else None,
                    "selected_account_id": selected_account_id,
                    "env_account_id": env_account_id,
                    "account_consistent": consistent,
                    "fills_worker_active": self.webhook_server._fills_worker_active,
                    "last_fill_check_ts": self.webhook_server._last_fill_check_ts,
                    "server_start_ts": getattr(self.webhook_server, 'server_start_ts', None),
                    "startup_blocked": getattr(self.webhook_server, '_startup_blocked', False),
                    "startup_block_reason": getattr(self.webhook_server, '_startup_block_reason', None),
                    "timestamp": datetime.now().isoformat(),
                    "uptime": "running"
                }
                
                status_code = 200 if is_authenticated else 503
                self._send_response(status_code, health_data)
                
            except Exception as e:
                logger.error(f"Health check failed: {str(e)}")
                self._send_response(500, {"status": "unhealthy", "error": str(e)})
                
        elif self.path == '/status':
            # Status endpoint
            try:
                status_data = {
                    "service": "TopStepX Trading Bot",
                    "version": "1.0.0",
                    "status": "running",
                    "timestamp": datetime.now().isoformat()
                }
                self._send_response(200, status_data)
                
            except Exception as e:
                logger.error(f"Status check failed: {str(e)}")
                self._send_response(500, {"error": str(e)})
                
        elif self.path == '/debug':
            # Debug endpoint to show account selection details
            try:
                debug_data = {
                    "selected_account": self.trading_bot.selected_account,
                    "selected_account_id": self.trading_bot.selected_account.get('id') if self.trading_bot.selected_account else None,
                    "selected_account_name": self.trading_bot.selected_account.get('name') if self.trading_bot.selected_account else None,
                    "env_account_id": getattr(self.webhook_server, 'env_account_id', None),
                    "account_consistent": (str(getattr(self.webhook_server, 'env_account_id', None)) == str(self.trading_bot.selected_account.get('id'))) if self.trading_bot.selected_account else False,
                    "startup_blocked": getattr(self.webhook_server, '_startup_blocked', False),
                    "startup_block_reason": getattr(self.webhook_server, '_startup_block_reason', None),
                    "timestamp": datetime.now().isoformat()
                }
                self._send_response(200, debug_data)
                
            except Exception as e:
                logger.error(f"Debug check failed: {str(e)}")
                self._send_response(500, {"error": str(e)})
                
        elif self.path.startswith('/dashboard'):
            # Dashboard endpoint (handle query parameters)
            try:
                # Check if dashboard is enabled
                dashboard_enabled = os.getenv('DASHBOARD_ENABLED', 'true').lower() in ('true', '1', 'yes', 'on')
                if not dashboard_enabled:
                    self._send_response(503, {"error": "Dashboard is disabled"})
                    return
                
                self._serve_dashboard()
            except Exception as e:
                logger.error(f"Dashboard error: {str(e)}")
                self._send_response(500, {"error": str(e)})
                
        elif self.path.startswith('/ws/dashboard'):
            # WebSocket endpoint for dashboard - return 426 Upgrade Required
            self._send_response(426, {"error": "WebSocket upgrade required"})
                
        elif self.path.startswith('/api/stream'):
            # Server-Sent Events (SSE) stream for real-time updates on same HTTP port
            try:
                self._handle_sse_stream()
            except Exception as e:
                logger.error(f"SSE error: {str(e)}")
                # If streaming fails, return a 500 once
                try:
                    self._send_response(500, {"error": str(e)})
                except Exception:
                    pass

        elif self.path.startswith('/api/'):
            # API endpoints
            try:
                self._handle_api_request()
            except Exception as e:
                logger.error(f"API error: {str(e)}")
                self._send_response(500, {"error": str(e)})
                
        elif self.path.startswith('/static/'):
            # Static files
            try:
                self._serve_static_file()
            except Exception as e:
                logger.error(f"Static file error: {str(e)}")
                self._send_response(404, {"error": "File not found"})
                
        else:
            # Default response for other GET requests
            self._send_response(404, {"error": "Not found"})
    
    def do_POST(self):
        """Handle POST requests from TradingView and API"""
        try:
            # If startup is blocked, reject POSTs
            if getattr(self.webhook_server, '_startup_blocked', False):
                self._send_response(503, {"error": "Service not ready - startup blocked", "reason": self.webhook_server._startup_block_reason})
                return
            
            # Route API requests to API handler
            if self.path.startswith('/api/'):
                self._handle_api_request()
                return
            
            # Handle webhook requests
            # Get content length
            content_length = int(self.headers.get('Content-Length', 0))
            
            # Read the request body
            post_data = self.rfile.read(content_length)
            
            # Parse JSON payload
            try:
                payload = json.loads(post_data.decode('utf-8'))
                logger.info(f"Received webhook payload: {json.dumps(payload, indent=2)}")
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse JSON payload: {e}")
                self._send_response(400, {"error": "Invalid JSON payload"})
                return
            
            # Process the webhook
            result = self._process_webhook(payload)
            
            if result.get("success"):
                # Broadcast trade event to WebSocket clients
                if hasattr(self.webhook_server, 'websocket_server') and self.webhook_server.websocket_server:
                    try:
                        # Get or create event loop for this thread
                        loop = asyncio.get_event_loop()
                        if loop.is_running():
                            asyncio.create_task(self._broadcast_trade_event(result))
                        else:
                            # If no running loop, run in a new thread
                            import threading
                            def run_broadcast():
                                new_loop = asyncio.new_event_loop()
                                asyncio.set_event_loop(new_loop)
                                new_loop.run_until_complete(self._broadcast_trade_event(result))
                                new_loop.close()
                            threading.Thread(target=run_broadcast, daemon=True).start()
                    except Exception as e:
                        logger.warning(f"Failed to broadcast trade event: {e}")
                
                self._send_response(200, {"message": "Webhook processed successfully", "result": result})
            else:
                self._send_response(400, {"error": result.get("error", "Unknown error")})
                
        except Exception as e:
            logger.error(f"Error processing POST request: {str(e)}")
            self._send_response(500, {"error": "Internal server error"})
    
    def do_DELETE(self):
        """Handle DELETE requests for dashboard actions"""
        try:
            # Route DELETE requests to API handler
            self._handle_api_request()
        except Exception as e:
            logger.error(f"Error processing DELETE request: {str(e)}")
            self._send_response(500, {"error": "Internal server error"})
    
    def _handle_sse_stream(self):
        """Stream real-time updates using Server-Sent Events (SSE)."""
        # Basic token check (optional auth)
        try:
            # Parse query string for token
            token = None
            if '?' in self.path:
                path_only, query = self.path.split('?', 1)
                for kv in query.split('&'):
                    if '=' in kv:
                        k, v = kv.split('=', 1)
                        if k == 'token':
                            token = v
                            break

            expected = os.getenv('DASHBOARD_AUTH_TOKEN')
            if expected:
                # Require token when configured
                if not token or token != expected:
                    self._send_response(401, {"error": "Unauthorized"})
                    return

            # Establish SSE response
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Cache-Control', 'no-cache')
            self.send_header('Connection', 'keep-alive')
            self.end_headers()

            def send_event(event_type: str, data_obj: Dict):
                try:
                    payload = json.dumps(data_obj)
                    self.wfile.write(f"event: {event_type}\n".encode('utf-8'))
                    self.wfile.write(f"data: {payload}\n\n".encode('utf-8'))
                    try:
                        self.wfile.flush()
                    except Exception:
                        pass
                except BrokenPipeError:
                    raise
                except Exception as e:
                    logger.warning(f"SSE send error for {event_type}: {e}")

            # Initial burst + short periodic updates without blocking forever
            account_id = self.trading_bot.selected_account.get('id') if self.trading_bot.selected_account else None

            # Send initial snapshots
            account_info = {
                "account_id": account_id,
                "account_name": self.trading_bot.selected_account.get('name') if self.trading_bot.selected_account else None,
                "balance": self.trading_bot.selected_account.get('balance', 0) if self.trading_bot.selected_account else 0,
                "status": self.trading_bot.selected_account.get('status') if self.trading_bot.selected_account else None,
                "currency": self.trading_bot.selected_account.get('currency', 'USD') if self.trading_bot.selected_account else 'USD'
            }
            send_event('account_update', account_info)

            try:
                positions = asyncio.run(self.trading_bot.get_open_positions(account_id=account_id)) if account_id else []
            except Exception as e:
                logger.warning(f"SSE positions fetch error: {e}")
                positions = []
            send_event('position_update', positions)

            try:
                orders = asyncio.run(self.trading_bot.get_open_orders(account_id=account_id)) if account_id else []
            except Exception as e:
                logger.warning(f"SSE orders fetch error: {e}")
                orders = []
            send_event('order_update', orders)

            # Health
            health = self.webhook_server.get_health_status() if hasattr(self.webhook_server, 'get_health_status') else {"status": "healthy"}
            send_event('health_update', health)

            # Periodic updates for a while (e.g., 120 seconds)
            import time as _time
            start_ts = _time.time()
            while True:
                # Stop after 2 minutes to avoid holding the handler forever
                if _time.time() - start_ts > 120:
                    break
                try:
                    if account_id:
                        positions = asyncio.run(self.trading_bot.get_open_positions(account_id=account_id))
                        send_event('position_update', positions)
                        orders = asyncio.run(self.trading_bot.get_open_orders(account_id=account_id))
                        send_event('order_update', orders)
                    # health
                    health = self.webhook_server.get_health_status() if hasattr(self.webhook_server, 'get_health_status') else {"status": "healthy"}
                    send_event('health_update', health)
                except BrokenPipeError:
                    # Client disconnected
                    return
                except Exception as e:
                    logger.warning(f"SSE loop error: {e}")
                # 10s cadence
                try:
                    _time.sleep(10)
                except Exception:
                    break

            # Graceful end of stream
            try:
                self.wfile.write(b":\n\n")  # comment/keepalive end
                self.wfile.flush()
            except Exception:
                pass

        except Exception as e:
            logger.error(f"SSE handler error: {e}")

    async def _broadcast_trade_event(self, result: Dict):
        """Broadcast trade event to WebSocket clients"""
        try:
            if hasattr(self.webhook_server, 'websocket_server') and self.webhook_server.websocket_server:
                # Broadcast trade fill notification
                await self.webhook_server.websocket_server.broadcast_trade_fill({
                    "symbol": result.get("symbol", "Unknown"),
                    "side": result.get("side", "Unknown"),
                    "quantity": result.get("quantity", 0),
                    "price": result.get("price", 0),
                    "order_id": result.get("order_id", "Unknown"),
                    "timestamp": time.time()
                })
                
                # Broadcast position update
                if self.trading_bot.selected_account:
                    positions = await self.trading_bot.get_open_positions(
                        account_id=self.trading_bot.selected_account.get('id')
                    )
                    await self.webhook_server.websocket_server.broadcast_position_update(positions)
                
                # Broadcast order update
                if self.trading_bot.selected_account:
                    orders = await self.trading_bot.get_open_orders(
                        account_id=self.trading_bot.selected_account.get('id')
                    )
                    await self.webhook_server.websocket_server.broadcast_order_update(orders)
                    
        except Exception as e:
            logger.error(f"Error broadcasting trade event: {e}")
    
    
    def _process_webhook(self, payload: Dict) -> Dict:
        """Process the TradingView webhook payload"""
        try:
            # Extract trade information from the payload
            trade_info = self._extract_trade_info(payload)
            
            if not trade_info:
                return {"success": False, "error": "Could not extract trade information"}
            
            # Parse signal type
            signal_type = self._parse_signal_type(trade_info.get("title", ""))
            
            if signal_type == "unknown":
                logger.warning(f"Unknown signal type: {trade_info.get('title', 'Unknown')}")
                return {
                    "success": False,
                    "error": "Unknown signal type",
                    "signal_type": "unknown",
                    "title": trade_info.get("title", "Unknown")
                }
            
            logger.info(f"Processing {signal_type} signal: {trade_info}")

            # Send Discord signal notification
            try:
                account_name = self.trading_bot.selected_account.get('name', 'Unknown') if self.trading_bot.selected_account else 'Unknown'
                symbol = trade_info.get('symbol', 'Unknown')
                self.trading_bot.discord_notifier.send_signal_notification(signal_type, symbol, account_name, trade_info)
            except Exception as notif_err:
                logger.warning(f"Failed to send Discord signal notification: {notif_err}")

            # Dispatch asynchronously to avoid HTTP read timeouts; reply immediately
            self._run_async_background(self._execute_signal_action(signal_type, dict(trade_info)),
                                       description=f"signal:{signal_type}")
            return {"success": True, "queued": True, "action": signal_type}
            
        except Exception as e:
            logger.error(f"Error processing webhook: {str(e)}")
            return {"success": False, "error": str(e)}

    def _run_async_background(self, coro, description: str = "task") -> None:
        """Run an async coroutine in a background thread without blocking the request."""
        def runner():
            try:
                logger.info(f"Starting background {description}")
                asyncio.run(coro)
                logger.info(f"Completed background {description}")
            except Exception as e:
                logger.error(f"Background {description} failed: {e}")
        t = threading.Thread(target=runner, daemon=True)
        t.start()
    
    def _extract_trade_info(self, payload: Dict) -> Optional[Dict]:
        """Extract trade information from TradingView payload"""
        try:
            # Handle the specific format you provided
            if "embeds" in payload and len(payload["embeds"]) > 0:
                embed = payload["embeds"][0]
                
                # Extract basic info
                title = embed.get("title", "")
                description = embed.get("description", "")
                fields = embed.get("fields", [])
                
                # Parse title for direction and symbol
                direction = self._parse_direction_from_title(title)
                symbol = self._parse_symbol_from_title(title)
                
                # Extract trade levels from fields
                entry = None
                stop_loss = None
                take_profit_1 = None
                take_profit_2 = None
                
                for field in fields:
                    name = field.get("name", "").lower()
                    value = field.get("value", "")
                    
                    if "entry" in name:
                        entry = self._parse_price(value)
                    elif "stop" in name:
                        stop_loss = self._parse_price(value)
                    elif "target 1" in name or "takeprofit1" in name:
                        take_profit_1 = self._parse_price(value)
                    elif "target 2" in name or "takeprofit2" in name:
                        take_profit_2 = self._parse_price(value)
                
                # Extract PnL from description
                pnl = self._parse_pnl_from_description(description)
                
                return {
                    "symbol": symbol,
                    "direction": direction,
                    "entry": entry,
                    "stop_loss": stop_loss,
                    "take_profit_1": take_profit_1,
                    "take_profit_2": take_profit_2,
                    "pnl": pnl,
                    "title": title,
                    "description": description,
                    "timestamp": datetime.now().isoformat()
                }
            
            return None
            
        except Exception as e:
            logger.error(f"Error extracting trade info: {str(e)}")
            return None
    
    def _parse_direction_from_title(self, title: str) -> str:
        """Parse direction from title (e.g., 'TP2 hit for short' -> 'SELL')"""
        title_lower = title.lower()
        if "short" in title_lower or "sell" in title_lower:
            return "SELL"
        elif "long" in title_lower or "buy" in title_lower:
            return "BUY"
        return "UNKNOWN"
    
    def _parse_signal_type(self, title: str) -> str:
        """Parse signal type from title and return action type"""
        title_lower = title.lower()
        
        # Open signals - create new positions
        if "open long" in title_lower:
            return "open_long"
        elif "open short" in title_lower:
            return "open_short"
        elif "open" in title_lower and "long" in title_lower:
            return "open_long"
        elif "open" in title_lower and "short" in title_lower:
            return "open_short"
        
        # Stop out signals - flatten positions
        elif "stop out long" in title_lower:
            return "stop_out_long"
        elif "stop out short" in title_lower:
            return "stop_out_short"
        elif "stop out" in title_lower and "long" in title_lower:
            return "stop_out_long"
        elif "stop out" in title_lower and "short" in title_lower:
            return "stop_out_short"
        
        # Trim signals - partially close positions (Pine Script uses "trim/close")
        # Use market orders for all trim/close signals
        elif "trim/close long" in title_lower or "trim long" in title_lower:
            return "tp1_hit_long"
        elif "trim/close short" in title_lower or "trim short" in title_lower:
            return "tp1_hit_short"
        elif "trim" in title_lower and "long" in title_lower:
            return "trim_long"
        elif "trim" in title_lower and "short" in title_lower:
            return "trim_short"
        elif "trim" in title_lower:
            return "trim_position"  # Generic trim
        
        # Target hit signals - close positions (Pine Script uses "TP2 hit for long/short")
        elif "tp2 hit long" in title_lower or "tp2 hit for long" in title_lower:
            return "tp2_hit_long"
        elif "tp2 hit short" in title_lower or "tp2 hit for short" in title_lower:
            return "tp2_hit_short"
        elif "tp2 hit" in title_lower and "long" in title_lower:
            return "tp2_hit_long"
        elif "tp2 hit" in title_lower and "short" in title_lower:
            return "tp2_hit_short"
        elif "tp1 hit" in title_lower and "long" in title_lower:
            return "tp1_hit_long"
        elif "tp1 hit" in title_lower and "short" in title_lower:
            return "tp1_hit_short"
        elif "tp3 hit" in title_lower and "long" in title_lower:
            return "tp3_hit_long"
        elif "tp3 hit" in title_lower and "short" in title_lower:
            return "tp3_hit_short"
        
        # Session close signals
        elif "session close" in title_lower:
            return "session_close"
        
        # Generic close signals
        elif "close long" in title_lower:
            return "close_long"
        elif "close short" in title_lower:
            return "close_short"
        elif "exit long" in title_lower:
            return "exit_long"
        elif "exit short" in title_lower:
            return "exit_short"
        
        return "unknown"
    
    
    def _is_tp1_hit(self, direction: str) -> bool:
        """Determine if a trim/close signal is a TP1 hit or manual trim"""
        # For Pine Script, "trim/close" signals are typically TP1 hits
        # This is a simplified approach - in production you might want to track
        # position state more precisely
        return True  # Assume all trim/close signals from Pine Script are TP1 hits
    
    def _parse_symbol_from_title(self, title: str) -> str:
        """Parse symbol from title (e.g., '[MNQ1!]' -> 'MNQ')"""
        # Look for pattern like [MNQ1!] or [ES1!]
        match = re.search(r'\[([A-Z]+)\d*!?\]', title)
        if match:
            return match.group(1)
        return "UNKNOWN"
    
    def _parse_price(self, value: str) -> Optional[float]:
        """Parse price from field value"""
        if not value or value.lower() == "null":
            return None
        
        try:
            # Remove any non-numeric characters except decimal point
            cleaned = re.sub(r'[^\d.]', '', value)
            if cleaned:
                return float(cleaned)
        except (ValueError, TypeError):
            pass
        
        return None
    
    def _parse_pnl_from_description(self, description: str) -> Optional[float]:
        """Parse PnL from description"""
        # Look for pattern like "$ +80.9 points" or "$ -45.2 points"
        match = re.search(r'\$\s*([+-]?\d+\.?\d*)\s*points?', description)
        if match:
            try:
                return float(match.group(1))
            except ValueError:
                pass
        return None
    
    async def _execute_trade(self, trade_info: Dict) -> Dict:
        """Execute the trade based on extracted information"""
        try:
            symbol = trade_info.get("symbol", "").upper()
            direction = trade_info.get("direction", "").upper()
            entry = trade_info.get("entry")
            stop_loss = trade_info.get("stop_loss")
            take_profit_1 = trade_info.get("take_profit_1")
            take_profit_2 = trade_info.get("take_profit_2")
            
            if symbol == "UNKNOWN" or direction == "UNKNOWN":
                return {"success": False, "error": "Could not determine symbol or direction"}
            
            if not entry:
                return {"success": False, "error": "No entry price provided"}
            
            # For now, we'll place a simple market order
            # In the future, we can implement more sophisticated order types
            quantity = 1  # Default quantity
            
            logger.info(f"Executing trade: {direction} {quantity} {symbol} @ {entry}")
            
            # Place the order using the trading bot
            result = await self.trading_bot.place_market_order(
                symbol=symbol,
                side=direction,
                quantity=quantity,
                stop_loss_ticks=self._calculate_ticks(entry, stop_loss) if stop_loss else None,
                take_profit_ticks=self._calculate_ticks(entry, take_profit_1) if take_profit_1 else None
            )
            
            if "error" in result:
                return {"success": False, "error": result["error"]}
            
            return {
                "success": True,
                "order_result": result,
                "trade_info": trade_info
            }
            
        except Exception as e:
            logger.error(f"Error executing trade: {str(e)}")
            return {"success": False, "error": str(e)}
    
    def _calculate_ticks(self, entry_price: float, target_price: float, symbol: str = "MNQ") -> int:
        """Calculate ticks between entry and target price"""
        if not entry_price or not target_price:
            return 0
        
        # Get tick value for symbol
        tick_values = {
            "MNQ": 0.25,    # Micro E-mini NASDAQ-100
            "ES": 0.25,     # E-mini S&P 500
            "NQ": 0.25,     # E-mini NASDAQ-100
            "YM": 1.0,      # E-mini Dow Jones
            "MYM": 0.5,     # Micro E-mini Dow Jones
            "RTY": 0.1,     # E-mini Russell 2000
            "M2K": 0.1,     # Micro E-mini Russell 2000
        }
        
        tick_value = tick_values.get(symbol.upper(), 0.25)  # Default to 0.25
        price_diff = abs(target_price - entry_price)
        
        if price_diff > 0:
            return int(price_diff / tick_value)
        return 0
    
    def _calculate_stop_ticks(self, entry_price: float, stop_price: float, side: str, symbol: str = "MNQ") -> int:
        """Calculate stop loss ticks (negative for stop loss)"""
        if not entry_price or not stop_price:
            return 0
        
        tick_value = self._get_tick_value(symbol)
        price_diff = stop_price - entry_price
        ticks = int(price_diff / tick_value)
        
        # For stop loss, we want negative ticks (stop is below entry for long, above entry for short)
        return ticks
    
    def _calculate_profit_ticks(self, entry_price: float, profit_price: float, side: str, symbol: str = "MNQ") -> int:
        """Calculate take profit ticks (positive for long, negative for short)"""
        if not entry_price or not profit_price:
            return 0
        
        tick_value = self._get_tick_value(symbol)
        
        # For long positions, profit is above entry (positive ticks)
        # For short positions, profit is below entry (negative ticks)
        if side.upper() == "BUY":
            price_diff = profit_price - entry_price  # Should be positive for profit
            ticks = int(price_diff / tick_value)
            return max(0, ticks)  # Positive ticks for long positions
        else:  # SELL
            price_diff = entry_price - profit_price  # Should be positive for profit
            ticks = int(price_diff / tick_value)
            return -max(0, ticks)  # Negative ticks for short positions
    
    def _get_tick_value(self, symbol: str) -> float:
        """Get tick value for symbol - comprehensive futures and micro futures support"""
        tick_values = {
            # === E-MINI INDEX FUTURES ===
            "ES": 0.25,     # E-mini S&P 500
            "NQ": 0.25,     # E-mini NASDAQ-100
            "YM": 1.0,      # E-mini Dow Jones
            "RTY": 0.1,     # E-mini Russell 2000
            "2K": 0.1,      # E-mini Russell 2000 (alternative symbol)
            
            # === MICRO E-MINI INDEX FUTURES ===
            "MES": 0.25,    # Micro E-mini S&P 500
            "MNQ": 0.25,    # Micro E-mini NASDAQ-100
            "MYM": 0.5,     # Micro E-mini Dow Jones
            "M2K": 0.1,     # Micro E-mini Russell 2000
            
            # === ENERGY FUTURES ===
            "CL": 0.01,     # Crude Oil (WTI)
            "MCL": 0.01,    # Micro Crude Oil
            "NG": 0.001,    # Natural Gas
            "MNG": 0.001,   # Micro Natural Gas
            "RB": 0.0001,   # RBOB Gasoline
            "HO": 0.0001,   # Heating Oil
            
            # === METALS FUTURES ===
            "GC": 0.1,      # Gold
            "MGC": 0.1,     # Micro Gold
            "SI": 0.005,    # Silver
            "MSI": 0.005,   # Micro Silver
            "PL": 0.1,      # Platinum
            "PA": 0.1,      # Palladium
            "HG": 0.0005,   # Copper
            
            # === AGRICULTURAL FUTURES ===
            "ZC": 0.25,     # Corn
            "ZS": 0.25,     # Soybeans
            "ZW": 0.25,     # Wheat
            "KC": 0.05,     # Coffee
            "SB": 0.01,     # Sugar
            "CT": 0.01,     # Cotton
            "CC": 1.0,      # Cocoa
            
            # === CURRENCY FUTURES ===
            "6E": 0.0001,   # Euro
            "6J": 0.0001,   # Japanese Yen
            "6B": 0.0001,   # British Pound
            "6A": 0.0001,   # Australian Dollar
            "6C": 0.0001,   # Canadian Dollar
            "6S": 0.0001,   # Swiss Franc
            
            # === BOND FUTURES ===
            "ZB": 0.03125,  # 30-Year Treasury Bond
            "ZN": 0.015625, # 10-Year Treasury Note
            "ZF": 0.0078125, # 5-Year Treasury Note
            "ZT": 0.0078125, # 2-Year Treasury Note
            
            # === VOLATILITY FUTURES ===
            "VX": 0.05,     # VIX Volatility Index
            
            # === CRYPTOCURRENCY FUTURES ===
            "BTC": 5.0,     # Bitcoin
            "ETH": 0.1,     # Ethereum
        }
        return tick_values.get(symbol.upper(), 0.25)  # Default to 0.25 for unknown symbols
    
    async def _get_position_size(self, side: str, symbol: str = "") -> int:
        """Get current position size for a specific side and symbol"""
        try:
            logger.info(f"Getting position size for {side} {symbol}")
            
            # Get all open positions
            positions = await self.trading_bot.get_open_positions()
            
            if not positions:
                logger.info("No open positions found")
                return 0
            
            # Find positions matching the side and symbol
            total_size = 0
            for position in positions:
                position_side = "BUY" if position.get("type") == 1 else "SELL"  # 1 = Long, 2 = Short
                position_symbol = self._extract_symbol_from_contract_id(position.get("contractId", ""))
                
                # Check if this position matches our criteria
                if position_side == side and (not symbol or position_symbol == symbol.upper()):
                    size = position.get("size", 0)
                    total_size += size
                    logger.info(f"Found {side} position: {size} contracts of {position_symbol}")
            
            logger.info(f"Total {side} position size: {total_size} contracts")
            return total_size
            
        except Exception as e:
            logger.error(f"Error getting position size: {str(e)}")
            return 0
    
    def _extract_symbol_from_contract_id(self, contract_id: str) -> str:
        """Extract symbol from contract ID (e.g., CON.F.US.MNQ.Z25 -> MNQ)"""
        if not contract_id:
            return ""
        
        # Split by dots and get the symbol part
        parts = contract_id.split('.')
        if len(parts) >= 4:
            return parts[3]  # CON.F.US.MNQ.Z25 -> MNQ
        return contract_id
    
    def _send_response(self, status_code: int, data: Dict):
        """Send HTTP response"""
        self.send_response(status_code)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(data).encode('utf-8'))
    
    def _serve_dashboard(self):
        """Serve the dashboard HTML page"""
        try:
            # Try multiple possible paths for the dashboard file
            possible_paths = [
                'static/dashboard.html',
                '/app/static/dashboard.html',
                './static/dashboard.html',
                'static/dashboard.html'
            ]
            
            content = None
            for path in possible_paths:
                try:
                    logger.info(f"Trying to open dashboard file: {path}")
                    with open(path, 'r') as f:
                        content = f.read()
                        logger.info(f"Successfully loaded dashboard from: {path}")
                        break
                except FileNotFoundError:
                    logger.warning(f"Dashboard file not found at: {path}")
                    continue
                except Exception as e:
                    logger.error(f"Error reading dashboard file {path}: {e}")
                    continue
            
            if content is None:
                logger.error("Dashboard file not found in any location")
                self._send_response(404, {"error": "Dashboard file not found"})
                return
            
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            self.wfile.write(content.encode('utf-8'))
        except Exception as e:
            logger.error(f"Error serving dashboard: {e}")
            self._send_response(500, {"error": f"Dashboard error: {str(e)}"})
    
    def _serve_static_file(self):
        """Serve static files (CSS, JS, etc.)"""
        import mimetypes
        import os
        
        file_path = self.path[1:]  # Remove leading slash
        
        # Try multiple possible paths for static files
        possible_paths = [
            file_path,
            f'/app/{file_path}',
            f'./{file_path}'
        ]
        
        actual_path = None
        for path in possible_paths:
            logger.info(f"Trying to find static file: {path}")
            if os.path.exists(path):
                actual_path = path
                logger.info(f"Found static file at: {path}")
                break
            else:
                logger.warning(f"Static file not found at: {path}")
        
        if actual_path is None:
            logger.error(f"Static file not found: {file_path}")
            self._send_response(404, {"error": "File not found"})
            return
        
        # Get MIME type
        mime_type, _ = mimetypes.guess_type(actual_path)
        if not mime_type:
            mime_type = 'application/octet-stream'
        
        try:
            with open(actual_path, 'rb') as f:
                content = f.read()
            
            self.send_response(200)
            self.send_header('Content-type', mime_type)
            self.send_header('Content-Length', str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        except Exception as e:
            logger.error(f"Error reading static file {actual_path}: {e}")
            self._send_response(500, {"error": str(e)})
    
    def _handle_api_request(self):
        """Handle API requests"""
        try:
            # Import dashboard API
            from dashboard import DashboardAPI
            from auth import extract_token_from_request, validate_token
            import asyncio
            import os
            
            # Extract token from request
            headers = dict(self.headers) if hasattr(self, 'headers') else {}
            query_params = {}
            
            # Parse query string if present
            if '?' in self.path:
                path, query = self.path.split('?', 1)
                query_params = dict(param.split('=') for param in query.split('&') if '=' in param)
            
            token = extract_token_from_request(headers, query_params)
            
            # Check authentication
            if not token or not validate_token(token):
                logger.warning("Dashboard API access denied: Invalid or missing token")
                self._send_response(401, {"error": "Authentication required"})
                return
            
            logger.info("Dashboard API access allowed (authentication successful)")
            
            # Initialize dashboard API
            dashboard_api = DashboardAPI(self.trading_bot, self.webhook_server)
            
            # Handle different HTTP methods
            method = self.command if hasattr(self, 'command') else 'GET'
            
            # Route API requests
            if self.path == '/api/accounts':
                # Get all available accounts using async approach
                try:
                    import asyncio
                    import threading
                    import concurrent.futures
                    
                    def run_async():
                        new_loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(new_loop)
                        try:
                            return new_loop.run_until_complete(self.trading_bot.list_accounts())
                        finally:
                            new_loop.close()
                    
                    with concurrent.futures.ThreadPoolExecutor() as executor:
                        future = executor.submit(run_async)
                        accounts = future.result(timeout=15)
                    
                    # Format accounts for dashboard
                    formatted_accounts = []
                    for account in accounts:
                        formatted_accounts.append({
                            "id": account.get('id'),
                            "name": account.get('name'),
                            "status": account.get('status', 'active'),
                            "balance": account.get('balance', 0),
                            "currency": account.get('currency', 'USD'),
                            "account_type": account.get('account_type', 'trading')
                        })
                    
                    self._send_response(200, formatted_accounts)
                except Exception as e:
                    logger.error(f"Error getting accounts: {e}")
                    # Fallback to current account if API fails
                    if self.trading_bot.selected_account:
                        current_account = self.trading_bot.selected_account
                        accounts = [{
                            "id": current_account.get('id'),
                            "name": current_account.get('name'),
                            "status": current_account.get('status', 'active'),
                            "balance": current_account.get('balance', 0),
                            "currency": current_account.get('currency', 'USD'),
                            "account_type": current_account.get('account_type', 'trading')
                        }]
                        self._send_response(200, accounts)
                    else:
                        self._send_response(500, {"error": str(e)})
                    
            elif self.path.startswith('/api/accounts/') and self.path.endswith('/switch') and method == 'POST':
                # Switch to a different account
                try:
                    account_id = self.path.split('/')[-2]  # Get account ID from path
                    
                    # Get all accounts to find the target account
                    import asyncio
                    import threading
                    import concurrent.futures
                    
                    def run_async():
                        new_loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(new_loop)
                        try:
                            return new_loop.run_until_complete(self.trading_bot.list_accounts())
                        finally:
                            new_loop.close()
                    
                    with concurrent.futures.ThreadPoolExecutor() as executor:
                        future = executor.submit(run_async)
                        accounts = future.result(timeout=15)
                    
                    # Find the target account
                    target_account = None
                    for account in accounts:
                        if str(account.get('id')) == str(account_id):
                            target_account = account
                            break
                    
                    if not target_account:
                        self._send_response(404, {"error": "Account not found"})
                        return
                    
                    # Switch to the account
                    self.trading_bot.selected_account = target_account
                    
                    result = {
                        "success": True,
                        "account_id": account_id,
                        "account_name": target_account.get('name'),
                        "message": f"Switched to account: {target_account.get('name', account_id)}"
                    }
                    
                    self._send_response(200, result)
                except Exception as e:
                    logger.error(f"Error switching account: {e}")
                    self._send_response(500, {"error": str(e)})
                    
            elif self.path == '/api/account':
                # Get account info synchronously
                account = self.trading_bot.selected_account
                if not account:
                    self._send_response(200, {"error": "No account selected"})
                    return
                
                data = {
                    "account_id": account.get('id'),
                    "account_name": account.get('name'),
                    "status": account.get('status'),
                    "balance": account.get('balance', 0),
                    "currency": account.get('currency', 'USD'),
                    "account_type": account.get('account_type', 'unknown')
                }
                self._send_response(200, data)
                
            elif self.path == '/api/positions':
                # Get positions from trading bot
                try:
                    account_id = self.trading_bot.selected_account.get('id') if self.trading_bot.selected_account else None
                    logger.info(f"Dashboard requesting positions for account: {account_id}")
                    
                    if not account_id:
                        logger.warning("No account ID available for positions request")
                        self._send_response(200, [])
                        return
                    
                    if not self.trading_bot.session_token:
                        logger.warning("No session token available for positions request")
                        self._send_response(200, [])
                        return
                    
                    # Use asyncio.create_task to avoid event loop conflicts
                    try:
                        loop = asyncio.get_event_loop()
                        if loop.is_running():
                            # If loop is running, create a task
                            import threading
                            import concurrent.futures
                            
                            def run_async():
                                new_loop = asyncio.new_event_loop()
                                asyncio.set_event_loop(new_loop)
                                try:
                                    return new_loop.run_until_complete(self.trading_bot.get_open_positions(account_id=account_id))
                                finally:
                                    new_loop.close()
                            
                            with concurrent.futures.ThreadPoolExecutor() as executor:
                                future = executor.submit(run_async)
                                positions = future.result(timeout=10)
                        else:
                            positions = asyncio.run(self.trading_bot.get_open_positions(account_id=account_id))
                    except Exception as async_error:
                        logger.error(f"Async error fetching positions: {async_error}")
                        positions = []
                    
                    logger.info(f"Fetched {len(positions)} positions for dashboard")
                    logger.info(f"Positions data: {positions}")
                    self._send_response(200, positions)
                except Exception as e:
                    logger.error(f"Error fetching positions: {e}")
                    self._send_response(200, [])
                
            elif self.path == '/api/orders':
                # Get orders from trading bot
                try:
                    account_id = self.trading_bot.selected_account.get('id') if self.trading_bot.selected_account else None
                    if account_id:
                        orders = asyncio.run(self.trading_bot.get_open_orders(account_id=account_id))
                        self._send_response(200, orders)
                    else:
                        self._send_response(200, [])
                except Exception as e:
                    logger.error(f"Error fetching orders: {e}")
                    self._send_response(200, [])
                
            elif self.path.startswith('/api/history'):
                # Get trade history with date range support
                try:
                    account_id = self.trading_bot.selected_account.get('id') if self.trading_bot.selected_account else None
                    logger.info(f"Dashboard requesting history for account: {account_id}")
                    
                    if not account_id:
                        logger.warning("No account ID available for history request")
                        self._send_response(200, [])
                        return
                    
                    if not self.trading_bot.session_token:
                        logger.warning("No session token available for history request")
                        self._send_response(200, [])
                        return
                    
                    # Parse date range from query parameters
                    start_date = query_params.get('start')
                    end_date = query_params.get('end')
                    
                    if start_date and end_date:
                        history = asyncio.run(self.trading_bot.get_order_history(
                            account_id=account_id,
                            start_timestamp=start_date,
                            end_timestamp=end_date,
                            limit=100
                        ))
                    else:
                        # Default to last 7 days
                        from datetime import datetime, timedelta
                        end_date = datetime.now().isoformat()
                        start_date = (datetime.now() - timedelta(days=7)).isoformat()
                        history = asyncio.run(self.trading_bot.get_order_history(
                            account_id=account_id,
                            start_timestamp=start_date,
                            end_timestamp=end_date,
                            limit=100
                        ))
                    
                    logger.info(f"Fetched {len(history)} history records for dashboard")
                    logger.info(f"History data: {history}")
                    self._send_response(200, history)
                except Exception as e:
                    logger.error(f"Error fetching history: {e}")
                    self._send_response(200, [])
                
            elif self.path == '/api/stats':
                # Get performance stats from trading bot
                try:
                    account_id = self.trading_bot.selected_account.get('id') if self.trading_bot.selected_account else None
                    if not account_id:
                        self._send_response(200, {
                            "total_trades": 0,
                            "winning_trades": 0,
                            "losing_trades": 0,
                            "win_rate": 0,
                            "total_pnl": 0
                        })
                        return
                    
                    # Get recent trade history to calculate stats
                    from datetime import datetime, timedelta
                    end_date = datetime.now().isoformat()
                    start_date = (datetime.now() - timedelta(days=30)).isoformat()
                    
                    history = asyncio.run(self.trading_bot.get_order_history(
                        account_id=account_id,
                        start_timestamp=start_date,
                        end_timestamp=end_date,
                        limit=1000
                    ))
                    
                    # Calculate stats from history
                    total_trades = len(history)
                    winning_trades = 0
                    losing_trades = 0
                    total_pnl = 0
                    
                    for trade in history:
                        if trade.get('status') == 'filled':
                            pnl = trade.get('realized_pnl', 0)
                            total_pnl += pnl
                            if pnl > 0:
                                winning_trades += 1
                            elif pnl < 0:
                                losing_trades += 1
                    
                    win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0
                    
                    data = {
                        "total_trades": total_trades,
                        "winning_trades": winning_trades,
                        "losing_trades": losing_trades,
                        "win_rate": round(win_rate, 2),
                        "total_pnl": round(total_pnl, 2)
                    }
                    self._send_response(200, data)
                except Exception as e:
                    logger.error(f"Error calculating stats: {e}")
                    self._send_response(200, {
                        "total_trades": 0,
                        "winning_trades": 0,
                        "losing_trades": 0,
                        "win_rate": 0,
                        "total_pnl": 0
                    })
                
            elif self.path.startswith('/api/logs'):
                # Get system logs from webhook server log file
                try:
                    import os
                    import re
                    from datetime import datetime
                    
                    # Parse query parameters
                    level_filter = query_params.get('level', 'ALL')
                    
                    logs = []
                    log_file = 'webhook_server.log'
                    
                    if os.path.exists(log_file):
                        with open(log_file, 'r') as f:
                            lines = f.readlines()
                            # Get last 50 lines
                            for line in lines[-50:]:
                                # Parse log line format: timestamp - level - message
                                match = re.match(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}) - (\w+) - (.*)', line.strip())
                                if match:
                                    log_level = match.group(2)
                                    # Filter by level if specified
                                    if level_filter == 'ALL' or log_level.upper() == level_filter.upper():
                                        logs.append({
                                            "timestamp": match.group(1),
                                            "level": log_level,
                                        "message": match.group(3),
                                        "source": "server"
                                    })
                    
                    # If no logs found, return a default message
                    if not logs:
                        logs = [{
                            "timestamp": datetime.now().isoformat(),
                            "level": "INFO",
                            "message": "No log file found or empty logs",
                            "source": "dashboard"
                        }]
                    
                    self._send_response(200, logs)
                except Exception as e:
                    logger.error(f"Error reading logs: {e}")
                    self._send_response(200, [{
                        "timestamp": datetime.now().isoformat(),
                        "level": "ERROR",
                        "message": f"Error reading logs: {str(e)}",
                        "source": "dashboard"
                    }])
                
            elif self.path == '/api/flatten':
                # Flatten all positions
                try:
                    account_id = self.trading_bot.selected_account.get('id') if self.trading_bot.selected_account else None
                    if not account_id:
                        self._send_response(200, {"error": "No account selected"})
                        return
                    
                    result = asyncio.run(self.trading_bot.flatten_all_positions(interactive=False))
                    self._send_response(200, result)
                except Exception as e:
                    logger.error(f"Error flattening positions: {e}")
                    self._send_response(200, {"error": str(e)})
                    
            elif self.path == '/api/orders/all' and method == 'DELETE':
                # Cancel all orders (DELETE method as called by frontend)
                try:
                    account_id = self.trading_bot.selected_account.get('id') if self.trading_bot.selected_account else None
                    if not account_id:
                        self._send_response(200, {"error": "No account selected"})
                        return
                    
                    result = asyncio.run(self.trading_bot.cancel_cached_orders(account_id=account_id))
                    self._send_response(200, result)
                except Exception as e:
                    logger.error(f"Error canceling orders: {e}")
                    self._send_response(200, {"error": str(e)})
                    
            elif self.path.startswith('/api/position/') and method == 'DELETE':
                # Close specific position (DELETE method as called by frontend)
                try:
                    position_id = self.path.split('/')[-1]
                    if not position_id:
                        self._send_response(200, {"error": "Position ID required"})
                        return
                    
                    result = asyncio.run(self.trading_bot.close_position(position_id))
                    self._send_response(200, result)
                except Exception as e:
                    logger.error(f"Error closing position: {e}")
                    self._send_response(200, {"error": str(e)})
                    
            elif self.path.startswith('/api/order/') and method == 'DELETE':
                # Cancel specific order (DELETE method as called by frontend)
                try:
                    order_id = self.path.split('/')[-1]
                    if not order_id:
                        self._send_response(200, {"error": "Order ID required"})
                        return
                    
                    result = asyncio.run(self.trading_bot.cancel_order(order_id))
                    self._send_response(200, result)
                except Exception as e:
                    logger.error(f"Error canceling order: {e}")
                    self._send_response(200, {"error": str(e)})
                    
            elif self.path == '/api/orders/place' and method == 'POST':
                # Place new order
                try:
                    import json
                    content_length = int(self.headers.get('Content-Length', 0))
                    post_data = self.rfile.read(content_length)
                    order_data = json.loads(post_data.decode('utf-8'))
                    
                    account_id = self.trading_bot.selected_account.get('id') if self.trading_bot.selected_account else None
                    
                    result = asyncio.run(
                        self.trading_bot.place_market_order(
                            symbol=order_data.get('symbol'),
                            side=order_data.get('side'),
                            quantity=order_data.get('quantity'),
                            account_id=account_id,
                            stop_loss_ticks=order_data.get('stop_loss_ticks'),
                            take_profit_ticks=order_data.get('take_profit_ticks'),
                            order_type=order_data.get('order_type', 'market'),
                            limit_price=order_data.get('price')
                        )
                    )
                    self._send_response(200, result)
                except Exception as e:
                    logger.error(f"Error placing order: {e}")
                    self._send_response(500, {"error": str(e)})
                    
            elif self.path.startswith('/api/market/') and method == 'GET':
                # Get market data for symbol
                try:
                    symbol = self.path.split('/')[-1]
                    
                    result = asyncio.run(
                        self.trading_bot.get_market_quote(symbol)
                    )
                    self._send_response(200, result)
                except Exception as e:
                    logger.error(f"Error getting market data: {e}")
                    self._send_response(500, {"error": str(e)})
                    
            else:
                self._send_response(404, {"error": "API endpoint not found"})
                
        except Exception as e:
            logger.error(f"API request error: {e}")
            self._send_response(500, {"error": str(e)})
    
    async def _trigger_fill_checks(self, account_id: str = None):
        """Trigger immediate fill checks after position changes"""
        try:
            target_account = account_id or self.webhook_server.account_id
            if target_account and self.trading_bot:
                # Run immediate fill checks
                await self.trading_bot.check_order_fills(target_account)
                await self.trading_bot._check_position_closes(target_account)
                await self.trading_bot._check_order_fills_for_closes(target_account)
                logger.info("Triggered immediate fill checks after position change")
        except Exception as e:
            logger.error(f"Failed to trigger fill checks: {e}")
    
    def log_message(self, format, *args):
        """Override to use our logger"""
        logger.info(f"{self.address_string()} - {format % args}")
    
    async def _execute_signal_action(self, signal_type: str, trade_info: Dict) -> Dict:
        """Execute the appropriate action based on signal type with enhanced filtering"""
        try:
            # FIXED: Enhanced signal filtering to prevent unwanted trades
            # Only process entry signals and critical exit signals
            entry_signals = ["open_long", "open_short"]
            critical_exit_signals = ["stop_out_long", "stop_out_short", "session_close"]
            
            # Check if we should ignore non-entry signals
            ignore_non_entry = os.getenv('IGNORE_NON_ENTRY_SIGNALS', 'true').lower() in ('true','1','yes','on')
            logger.info(f"IGNORE_NON_ENTRY_SIGNALS environment variable: {os.getenv('IGNORE_NON_ENTRY_SIGNALS')}")
            logger.info(f"ignore_non_entry setting: {ignore_non_entry}")
            logger.info(f"Processing signal: {signal_type}")
            
            if ignore_non_entry and signal_type not in entry_signals + critical_exit_signals:
                logger.info(f"Ignoring non-entry signal: {signal_type} (IGNORE_NON_ENTRY_SIGNALS=true)")
                return {"success": True, "action": signal_type, "ignored": True, "reason": "non-entry signal ignored"}
            
            # Process entry signals with enhanced validation
            if signal_type == "open_long":
                return await self._execute_open_long(trade_info)
            elif signal_type == "open_short":
                return await self._execute_open_short(trade_info)
            # Process critical exit signals
            elif signal_type == "stop_out_long":
                return await self._execute_stop_out_long(trade_info)
            elif signal_type == "stop_out_short":
                return await self._execute_stop_out_short(trade_info)
            elif signal_type == "session_close":
                return await self._execute_session_close(trade_info)
            # Process other signals only if not ignoring them
            elif signal_type == "trim_long":
                return await self._execute_trim_long(trade_info)
            elif signal_type == "trim_short":
                return await self._execute_trim_short(trade_info)
            elif signal_type == "trim_position":
                return await self._execute_trim_position(trade_info)
            elif signal_type == "tp2_hit_long":
                return await self._execute_tp2_hit_long(trade_info)
            elif signal_type == "tp2_hit_short":
                return await self._execute_tp2_hit_short(trade_info)
            elif signal_type == "tp1_hit_long":
                return await self._execute_tp1_hit_long(trade_info)
            elif signal_type == "tp1_hit_short":
                return await self._execute_tp1_hit_short(trade_info)
            elif signal_type == "tp3_hit_long":
                return await self._execute_tp3_hit_long(trade_info)
            elif signal_type == "tp3_hit_short":
                return await self._execute_tp3_hit_short(trade_info)
            elif signal_type == "close_long":
                return await self._execute_close_long(trade_info)
            elif signal_type == "close_short":
                return await self._execute_close_short(trade_info)
            elif signal_type == "exit_long":
                return await self._execute_exit_long(trade_info)
            elif signal_type == "exit_short":
                return await self._execute_exit_short(trade_info)
            elif signal_type == "ignore_signal":
                return await self._execute_ignore_signal(trade_info)
            else:
                return {"success": False, "error": f"Unknown signal type: {signal_type}"}
        except Exception as e:
            logger.error(f"Error executing signal action: {str(e)}")
            return {"success": False, "error": str(e)}
    
    async def _execute_open_long(self, trade_info: Dict) -> Dict:
        """Execute open long position using single position with proper staged exits"""
        try:
            symbol = trade_info.get("symbol", "").upper()
            entry = trade_info.get("entry")
            stop_loss = trade_info.get("stop_loss")
            take_profit_1 = trade_info.get("take_profit_1")
            take_profit_2 = trade_info.get("take_profit_2")
            
            if not entry:
                return {"success": False, "error": "No entry price provided"}
            
            # Get configuration from webhook server
            position_size = self.webhook_server.position_size
            close_entire_at_tp1 = self.webhook_server.close_entire_position_at_tp1
            
            logger.info(f"Executing open long: {symbol} @ {entry}, stop: {stop_loss}, tp1: {take_profit_1}, tp2: {take_profit_2}")
            logger.info(f"Position size: {position_size}, Close entire at TP1: {close_entire_at_tp1}")
            
            # Validate price levels for debugging
            if entry and take_profit_1 and take_profit_2:
                tp1_distance = abs(take_profit_1 - entry)
                tp2_distance = abs(take_profit_2 - entry)
                logger.info(f"Price validation: Entry={entry}, TP1={take_profit_1} (distance={tp1_distance:.2f}), TP2={take_profit_2} (distance={tp2_distance:.2f})")
                
                # Check for suspicious TP2 values (too far from entry)
                if tp2_distance > tp1_distance * 2:
                    logger.warning(f"⚠️ TP2 distance ({tp2_distance:.2f}) is more than 2x TP1 distance ({tp1_distance:.2f}) - this may be incorrect!")
            
            # Enhanced debounce with position size validation
            from datetime import datetime, timezone, timedelta
            now = datetime.now(timezone.utc)
            
            # Check for existing positions first
            existing_positions = await self.trading_bot.get_open_positions(account_id=self.webhook_server.account_id)
            symbol_positions = [pos for pos in existing_positions if pos.get('contractId') == self.trading_bot._get_contract_id(symbol)]
            
            # Only debounce if there are existing positions OR if we recently placed an order
            last_ts = self.webhook_server._last_open_signal_ts.get((symbol, "LONG"))
            if last_ts and (now - last_ts).total_seconds() < self.webhook_server.debounce_seconds:
                # If no existing positions, allow the trade (debounce only applies when positions exist)
                if not symbol_positions:
                    logger.info(f"Allowing open_long for {symbol} - no existing positions despite recent signal")
                else:
                    wait_left = int(self.webhook_server.debounce_seconds - (now - last_ts).total_seconds())
                    logger.warning(f"Debounced duplicate open_long for {symbol}; received too soon. Wait {wait_left}s")
                    return {"success": True, "action": "open_long", "debounced": True, "reason": "duplicate within debounce window"}
            
            if symbol_positions:
                total_existing = sum(pos.get('quantity', 0) for pos in symbol_positions)
                logger.warning(f"Found existing {symbol} positions: {total_existing} contracts")
                
                # Check against maximum position size limit
                if total_existing >= self.webhook_server.max_position_size:
                    logger.warning(f"Maximum position size limit reached for {symbol} ({total_existing} >= {self.webhook_server.max_position_size}). Ignoring new entry signal.")
                    return {"success": True, "action": "open_long", "ignored": True, "reason": "maximum position size limit reached"}
                
                # Check if adding new position would exceed limit
                if total_existing + position_size > self.webhook_server.max_position_size:
                    logger.warning(f"Adding {position_size} contracts would exceed max position size for {symbol} ({total_existing} + {position_size} > {self.webhook_server.max_position_size}). Ignoring new entry signal.")
                    return {"success": True, "action": "open_long", "ignored": True, "reason": "would exceed maximum position size"}
            
            self.webhook_server._last_open_signal_ts[(symbol, "LONG")] = now
            
            # Check bracket type configuration
            use_native_brackets = os.getenv('USE_NATIVE_BRACKETS', 'false').lower() in ('true', '1', 'yes', 'on')
            
            if use_native_brackets:
                # Using TopStepX Auto OCO Brackets - create bracket orders
                if close_entire_at_tp1:
                    # Close entire position at TP1 (single native bracket with TP1)
                    result = await self.trading_bot.create_bracket_order(
                        symbol=symbol,
                        side="BUY",
                        quantity=position_size,
                        stop_loss_price=stop_loss,
                        take_profit_price=take_profit_1,
                        account_id=self.webhook_server.account_id
                    )
                    logger.info(f"Placed single OCO bracket: size={position_size}, SL={stop_loss}, TP={take_profit_1}")
                else:
                    # Create single position with staged exit management
                    result = await self.trading_bot.create_partial_tp_bracket_order(
                        symbol=symbol,
                        side="BUY",
                        quantity=position_size,
                        stop_loss_price=stop_loss,
                        take_profit_1_price=take_profit_1,
                        take_profit_2_price=take_profit_2,
                        tp1_quantity=max(1, int(round(position_size * float(os.getenv('TP1_FRACTION', '0.75'))))),  # Use TP1_FRACTION env var
                        account_id=self.webhook_server.account_id
                    )
                    logger.info(f"Created single position with staged exits: {position_size} contracts, TP1: {take_profit_1}, TP2: {take_profit_2}")
            else:
                # Using TopStepX Position Brackets - place simple market order
                # TopStepX will automatically manage brackets based on account settings
                result = await self.trading_bot.place_market_order(
                    symbol=symbol,
                    side="BUY",
                    quantity=position_size,
                    account_id=self.webhook_server.account_id
                )
                logger.info(f"Placed simple market order for Position Brackets mode: {position_size} contracts")
                logger.info("TopStepX will automatically manage stop loss and take profit based on account settings")
            
            if "error" in result:
                return {"success": False, "error": result["error"]}
            
            logger.info(f"Open long executed: {result}")
            
            # Start bracket monitoring for the new position
            logger.info("Starting bracket monitoring for new position...")
            bracket_monitor_result = await self.trading_bot.monitor_all_bracket_positions(account_id=self.webhook_server.account_id)
            logger.info(f"Bracket monitor result: {bracket_monitor_result}")
            
            # Trigger immediate fill checks to catch fast fills and initialize position tracking
            await self._trigger_fill_checks()
            
            return {
                "success": True,
                "action": "open_long",
                "order_result": result,
                "trade_info": trade_info,
                "configuration": {
                    "position_size": position_size,
                    "close_entire_at_tp1": close_entire_at_tp1,
                    "staged_exits": not close_entire_at_tp1
                },
                "monitor_result": bracket_monitor_result
            }
        except Exception as e:
            logger.error(f"Error executing open long: {str(e)}")
            return {"success": False, "error": str(e)}
    
    async def _execute_open_short(self, trade_info: Dict) -> Dict:
        """Execute open short position using single position with proper staged exits"""
        try:
            symbol = trade_info.get("symbol", "").upper()
            entry = trade_info.get("entry")
            stop_loss = trade_info.get("stop_loss")
            take_profit_1 = trade_info.get("take_profit_1")
            take_profit_2 = trade_info.get("take_profit_2")
            
            if not entry:
                return {"success": False, "error": "No entry price provided"}
            
            # Get configuration from webhook server
            position_size = self.webhook_server.position_size
            close_entire_at_tp1 = self.webhook_server.close_entire_position_at_tp1
            
            logger.info(f"Executing open short: {symbol} @ {entry}, stop: {stop_loss}, tp1: {take_profit_1}, tp2: {take_profit_2}")
            logger.info(f"Position size: {position_size}, Close entire at TP1: {close_entire_at_tp1}")
            
            # Validate price levels for debugging
            if entry and take_profit_1 and take_profit_2:
                tp1_distance = abs(take_profit_1 - entry)
                tp2_distance = abs(take_profit_2 - entry)
                logger.info(f"Price validation: Entry={entry}, TP1={take_profit_1} (distance={tp1_distance:.2f}), TP2={take_profit_2} (distance={tp2_distance:.2f})")
                
                # Check for suspicious TP2 values (too far from entry)
                if tp2_distance > tp1_distance * 2:
                    logger.warning(f"⚠️ TP2 distance ({tp2_distance:.2f}) is more than 2x TP1 distance ({tp1_distance:.2f}) - this may be incorrect!")
            
            # Enhanced debounce with position size validation
            from datetime import datetime, timezone, timedelta
            now = datetime.now(timezone.utc)
            
            # Check for existing positions first
            existing_positions = await self.trading_bot.get_open_positions(account_id=self.webhook_server.account_id)
            symbol_positions = [pos for pos in existing_positions if pos.get('contractId') == self.trading_bot._get_contract_id(symbol)]
            
            # Only debounce if there are existing positions OR if we recently placed an order
            last_ts = self.webhook_server._last_open_signal_ts.get((symbol, "SHORT"))
            if last_ts and (now - last_ts).total_seconds() < self.webhook_server.debounce_seconds:
                # If no existing positions, allow the trade (debounce only applies when positions exist)
                if not symbol_positions:
                    logger.info(f"Allowing open_short for {symbol} - no existing positions despite recent signal")
                else:
                    wait_left = int(self.webhook_server.debounce_seconds - (now - last_ts).total_seconds())
                    logger.warning(f"Debounced duplicate open_short for {symbol}; received too soon. Wait {wait_left}s")
                    return {"success": True, "action": "open_short", "debounced": True, "reason": "duplicate within debounce window"}
            
            if symbol_positions:
                total_existing = sum(pos.get('quantity', 0) for pos in symbol_positions)
                logger.warning(f"Found existing {symbol} positions: {total_existing} contracts")
                
                # Check against maximum position size limit
                if total_existing >= self.webhook_server.max_position_size:
                    logger.warning(f"Maximum position size limit reached for {symbol} ({total_existing} >= {self.webhook_server.max_position_size}). Ignoring new entry signal.")
                    return {"success": True, "action": "open_short", "ignored": True, "reason": "maximum position size limit reached"}
                
                # Check if adding new position would exceed limit
                if total_existing + position_size > self.webhook_server.max_position_size:
                    logger.warning(f"Adding {position_size} contracts would exceed max position size for {symbol} ({total_existing} + {position_size} > {self.webhook_server.max_position_size}). Ignoring new entry signal.")
                    return {"success": True, "action": "open_short", "ignored": True, "reason": "would exceed maximum position size"}
            
            self.webhook_server._last_open_signal_ts[(symbol, "SHORT")] = now
            
            # Check bracket type configuration
            use_native_brackets = os.getenv('USE_NATIVE_BRACKETS', 'false').lower() in ('true', '1', 'yes', 'on')
            
            if use_native_brackets:
                # Using TopStepX Auto OCO Brackets - create bracket orders
                if close_entire_at_tp1:
                    # Close entire position at TP1 (single native bracket with TP1)
                    result = await self.trading_bot.create_bracket_order(
                        symbol=symbol,
                        side="SELL",
                        quantity=position_size,
                        stop_loss_price=stop_loss,
                        take_profit_price=take_profit_1,
                        account_id=self.webhook_server.account_id
                    )
                    logger.info(f"Placed single OCO bracket: size={position_size}, SL={stop_loss}, TP={take_profit_1}")
                else:
                    # Create single position with staged exit management
                    result = await self.trading_bot.create_partial_tp_bracket_order(
                        symbol=symbol,
                        side="SELL",
                        quantity=position_size,
                        stop_loss_price=stop_loss,
                        take_profit_1_price=take_profit_1,
                        take_profit_2_price=take_profit_2,
                        tp1_quantity=max(1, int(round(position_size * float(os.getenv('TP1_FRACTION', '0.75'))))),  # Use TP1_FRACTION env var
                        account_id=self.webhook_server.account_id
                    )
                    logger.info(f"Created single position with staged exits: {position_size} contracts, TP1: {take_profit_1}, TP2: {take_profit_2}")
            else:
                # Using TopStepX Position Brackets - place simple market order
                # TopStepX will automatically manage brackets based on account settings
                result = await self.trading_bot.place_market_order(
                    symbol=symbol,
                    side="SELL",
                    quantity=position_size,
                    account_id=self.webhook_server.account_id
                )
                logger.info(f"Placed simple market order for Position Brackets mode: {position_size} contracts")
                logger.info("TopStepX will automatically manage stop loss and take profit based on account settings")
            
            if "error" in result:
                return {"success": False, "error": result["error"]}
            
            logger.info(f"Open short executed: {result}")
            
            # Start bracket monitoring for the new position
            logger.info("Starting bracket monitoring for new position...")
            bracket_monitor_result = await self.trading_bot.monitor_all_bracket_positions(account_id=self.webhook_server.account_id)
            logger.info(f"Bracket monitor result: {bracket_monitor_result}")
            
            # Trigger immediate fill checks to catch fast fills and initialize position tracking
            await self._trigger_fill_checks()
            
            return {
                "success": True,
                "action": "open_short",
                "order_result": result,
                "trade_info": trade_info,
                "configuration": {
                    "position_size": position_size,
                    "close_entire_at_tp1": close_entire_at_tp1,
                    "staged_exits": not close_entire_at_tp1
                },
                "monitor_result": bracket_monitor_result
            }
        except Exception as e:
            logger.error(f"Error executing open short: {str(e)}")
            return {"success": False, "error": str(e)}
    
    async def _execute_stop_out_long(self, trade_info: Dict) -> Dict:
        """Execute stop out long: close all open buy positions and cancel all corresponding stop/take profit orders"""
        try:
            logger.info("Executing stop out long: closing all buy positions and canceling orders")
            
            # Get all open positions
            positions = await self.trading_bot.get_open_positions()
            if not positions:
                logger.info("No open positions found")
                return {
                    "success": True,
                    "action": "stop_out_long",
                    "result": {"message": "No positions to close"},
                    "trade_info": trade_info
                }
            
            # First try fast-path: cancel cached orders and close cached positions
            fast_cancel = await self.trading_bot.cancel_cached_orders()
            fast_close = await self.trading_bot.close_cached_positions()
            if fast_cancel.get("canceled") or fast_close.get("closed"):
                logger.info(f"Fast-path: canceled {len(fast_cancel.get('canceled', []))} orders, closed {len(fast_close.get('closed', []))} positions")

            # Close all long positions (detect by size>0 or type==1)
            closed_positions = []
            for position in positions:
                pos_id = position.get("id") or position.get("positionId") or position.get("PositionId")
                pos_size = position.get("size")
                pos_type = position.get("type")
                is_long = (isinstance(pos_size, (int, float)) and pos_size > 0) or pos_type == 1
                if is_long and pos_id is not None:
                    logger.info(f"Closing long position {pos_id} with size {pos_size}")
                    result = await self.trading_bot.close_position(pos_id)
                    if "error" not in result:
                        closed_positions.append(pos_id)
                    else:
                        logger.warning(f"Failed to close position {pos_id}: {result['error']}")
            
            # Cancel all open orders
            logger.info("Canceling all open orders")
            open_orders = await self.trading_bot.get_open_orders()
            canceled_orders = []
            if open_orders:
                for order in open_orders:
                    # Be defensive about field names from API
                    order_id = order.get("id") or order.get("orderId") or order.get("OrderId") or order.get("order_id")
                    symbol = order.get("symbol") or order.get("Symbol") or order.get("instrument") or order.get("Instrument") or "unknown"
                    if not order_id:
                        logger.warning(f"Skipping cancel for order without id: {order}")
                        continue
                    logger.info(f"Canceling order {order_id} for {symbol}")
                    result = await self.trading_bot.cancel_order(order_id)
                    if "error" not in result:
                        canceled_orders.append(order_id)
                    else:
                        logger.warning(f"Failed to cancel order {order_id}: {result['error']}")
            
            if closed_positions or canceled_orders:
                logger.info(f"Successfully closed {len(closed_positions)} long positions and canceled {len(canceled_orders)} orders")
                return {
                    "success": True,
                    "action": "stop_out_long",
                    "result": {
                        "closed_positions": closed_positions, 
                        "canceled_orders": canceled_orders,
                        "positions_count": len(closed_positions),
                        "orders_count": len(canceled_orders)
                    },
                    "trade_info": trade_info
                }
            else:
                logger.info("No long positions or orders found to close/cancel")
                return {
                    "success": True,
                    "action": "stop_out_long",
                    "result": {"message": "No long positions or orders found to close/cancel"},
                    "trade_info": trade_info
                }
            
        except Exception as e:
            logger.error(f"Error executing stop out long: {str(e)}")
            return {"success": False, "error": str(e)}
    
    async def _execute_stop_out_short(self, trade_info: Dict) -> Dict:
        """Execute stop out short: close all open sell positions and cancel all corresponding stop/take profit orders"""
        try:
            logger.info("Executing stop out short: closing all sell positions and canceling orders")
            
            # Get all open positions
            positions = await self.trading_bot.get_open_positions()
            if not positions:
                logger.info("No open positions found")
                return {
                    "success": True,
                    "action": "stop_out_short",
                    "result": {"message": "No positions to close"},
                    "trade_info": trade_info
                }
            
            # First try fast-path: cancel cached orders and close cached positions
            fast_cancel = await self.trading_bot.cancel_cached_orders()
            fast_close = await self.trading_bot.close_cached_positions()
            if fast_cancel.get("canceled") or fast_close.get("closed"):
                logger.info(f"Fast-path: canceled {len(fast_cancel.get('canceled', []))} orders, closed {len(fast_close.get('closed', []))} positions")

            # Close all short positions (detect by size<0 or type==2)
            closed_positions = []
            for position in positions:
                pos_id = position.get("id") or position.get("positionId") or position.get("PositionId")
                pos_size = position.get("size")
                pos_type = position.get("type")
                is_short = (isinstance(pos_size, (int, float)) and pos_size < 0) or pos_type == 2
                if is_short and pos_id is not None:
                    logger.info(f"Closing short position {pos_id} with size {pos_size}")
                    result = await self.trading_bot.close_position(pos_id)
                    if "error" not in result:
                        closed_positions.append(pos_id)
                    else:
                        logger.warning(f"Failed to close position {pos_id}: {result['error']}")
            
            # Cancel all open orders
            logger.info("Canceling all open orders")
            open_orders = await self.trading_bot.get_open_orders()
            canceled_orders = []
            if open_orders:
                for order in open_orders:
                    # Be defensive about field names from API
                    order_id = order.get("id") or order.get("orderId") or order.get("OrderId") or order.get("order_id")
                    symbol = order.get("symbol") or order.get("Symbol") or order.get("instrument") or order.get("Instrument") or "unknown"
                    if not order_id:
                        logger.warning(f"Skipping cancel for order without id: {order}")
                        continue
                    logger.info(f"Canceling order {order_id} for {symbol}")
                    result = await self.trading_bot.cancel_order(order_id)
                    if "error" not in result:
                        canceled_orders.append(order_id)
                    else:
                        logger.warning(f"Failed to cancel order {order_id}: {result['error']}")
            
            if closed_positions or canceled_orders:
                logger.info(f"Successfully closed {len(closed_positions)} short positions and canceled {len(canceled_orders)} orders")
                return {
                    "success": True,
                    "action": "stop_out_short",
                    "result": {
                        "closed_positions": closed_positions, 
                        "canceled_orders": canceled_orders,
                        "positions_count": len(closed_positions),
                        "orders_count": len(canceled_orders)
                    },
                    "trade_info": trade_info
                }
            else:
                logger.info("No short positions or orders found to close/cancel")
                return {
                    "success": True,
                    "action": "stop_out_short",
                    "result": {"message": "No short positions or orders found to close/cancel"},
                    "trade_info": trade_info
                }
            
        except Exception as e:
            logger.error(f"Error executing stop out short: {str(e)}")
            return {"success": False, "error": str(e)}
    
    async def _execute_trim_long(self, trade_info: Dict) -> Dict:
        """Execute trim long: if >1 contract, partially close, otherwise flatten"""
        try:
            logger.info("Executing trim long: checking position size and trimming")
            
            # Get current position size
            position_size = await self._get_position_size("BUY", trade_info.get("symbol", ""))
            
            if position_size > 1:
                # Get TP1 fraction from environment variable
                try:
                    tp1_fraction = float(os.getenv('TP1_FRACTION', '0.75'))
                    if not (0.0 < tp1_fraction < 1.0):
                        tp1_fraction = 0.75
                        logger.warning("Invalid TP1_FRACTION, using default 0.75")
                except (ValueError, TypeError):
                    tp1_fraction = 0.75
                    logger.warning("Invalid TP1_FRACTION, using default 0.75")
                
                # Partial close: close TP1_FRACTION of the position (round up)
                contracts_to_close = int(position_size * tp1_fraction)
                if contracts_to_close == 0:
                    contracts_to_close = 1  # At least close 1 contract
                logger.info(f"Trimming {contracts_to_close} contracts from {position_size} total long position ({tp1_fraction*100:.0f}% close)")
                
                # Place sell order to close partial position
                result = await self.trading_bot.place_market_order(
                    symbol=trade_info.get("symbol", ""),
                    side="SELL",
                    quantity=contracts_to_close,
                    account_id=self.trading_bot.selected_account.get("id")
                )
                
                if "error" in result:
                    logger.warning(f"Partial close failed, flattening all positions: {result['error']}")
                    result = await self.trading_bot.flatten_all_positions(interactive=False)
            else:
                # Single contract or no position, flatten all
                logger.info("Single contract or no position, flattening all positions")
                result = await self.trading_bot.flatten_all_positions(interactive=False)
            
            if "error" in result:
                return {"success": False, "error": result["error"]}
            
            # Run monitor to check for any position/order adjustments needed after trim
            logger.info("Running position monitor after trim long...")
            monitor_result = await self.trading_bot.monitor_position_changes()
            logger.info(f"Monitor result: {monitor_result}")
            
            # Trigger immediate fill checks to detect position closes
            await self._trigger_fill_checks()
            
            return {
                "success": True,
                "action": "trim_long",
                "result": result,
                "trade_info": trade_info,
                "monitor_result": monitor_result
            }
        except Exception as e:
            logger.error(f"Error executing trim long: {str(e)}")
            return {"success": False, "error": str(e)}
    
    async def _execute_trim_short(self, trade_info: Dict) -> Dict:
        """Execute trim short: if >1 contract, partially close, otherwise flatten"""
        try:
            logger.info("Executing trim short: checking position size and trimming")
            
            # Get current position size
            position_size = await self._get_position_size("SELL", trade_info.get("symbol", ""))
            
            if position_size > 1:
                # Get TP1 fraction from environment variable
                try:
                    tp1_fraction = float(os.getenv('TP1_FRACTION', '0.75'))
                    if not (0.0 < tp1_fraction < 1.0):
                        tp1_fraction = 0.75
                        logger.warning("Invalid TP1_FRACTION, using default 0.75")
                except (ValueError, TypeError):
                    tp1_fraction = 0.75
                    logger.warning("Invalid TP1_FRACTION, using default 0.75")
                
                # Partial close: close TP1_FRACTION of the position (round up)
                contracts_to_close = int(position_size * tp1_fraction)
                if contracts_to_close == 0:
                    contracts_to_close = 1  # At least close 1 contract
                logger.info(f"Trimming {contracts_to_close} contracts from {position_size} total short position ({tp1_fraction*100:.0f}% close)")
                
                # Place buy order to close partial position
                result = await self.trading_bot.place_market_order(
                    symbol=trade_info.get("symbol", ""),
                    side="BUY",
                    quantity=contracts_to_close,
                    account_id=self.trading_bot.selected_account.get("id")
                )
                
                if "error" in result:
                    logger.warning(f"Partial close failed, flattening all positions: {result['error']}")
                    result = await self.trading_bot.flatten_all_positions(interactive=False)
            else:
                # Single contract or no position, flatten all
                logger.info("Single contract or no position, flattening all positions")
                result = await self.trading_bot.flatten_all_positions(interactive=False)
            
            if "error" in result:
                return {"success": False, "error": result["error"]}
            
            # Run monitor to check for any position/order adjustments needed after trim
            logger.info("Running position monitor after trim short...")
            monitor_result = await self.trading_bot.monitor_position_changes()
            logger.info(f"Monitor result: {monitor_result}")
            
            # Trigger immediate fill checks to detect position closes
            await self._trigger_fill_checks()
            
            return {
                "success": True,
                "action": "trim_short",
                "result": result,
                "trade_info": trade_info,
                "monitor_result": monitor_result
            }
        except Exception as e:
            logger.error(f"Error executing trim short: {str(e)}")
            return {"success": False, "error": str(e)}
    
    async def _execute_trim_position(self, trade_info: Dict) -> Dict:
        """Execute generic trim: check position and trim accordingly"""
        try:
            logger.info("Executing trim position: checking position and trimming")
            
            result = await self.trading_bot.flatten_all_positions(interactive=False)
            
            if "error" in result:
                return {"success": False, "error": result["error"]}
            
            return {
                "success": True,
                "action": "trim_position",
                "result": result,
                "trade_info": trade_info
            }
        except Exception as e:
            logger.error(f"Error executing trim position: {str(e)}")
            return {"success": False, "error": str(e)}
    
    async def _execute_tp2_hit_long(self, trade_info: Dict) -> Dict:
        """Execute tp2 hit long: close all open buy positions and cancel all corresponding stop/take profit orders"""
        try:
            logger.info("Executing tp2 hit long: closing all buy positions and canceling orders")
            
            # Get all open positions
            positions = await self.trading_bot.get_open_positions()
            if not positions:
                logger.info("No open positions found")
                return {
                    "success": True,
                    "action": "tp2_hit_long",
                    "result": {"message": "No positions to close"},
                    "trade_info": trade_info
                }
            
            # First try fast-path: cancel cached orders and close cached positions
            fast_cancel = await self.trading_bot.cancel_cached_orders()
            fast_close = await self.trading_bot.close_cached_positions()
            if fast_cancel.get("canceled") or fast_close.get("closed"):
                logger.info(f"Fast-path: canceled {len(fast_cancel.get('canceled', []))} orders, closed {len(fast_close.get('closed', []))} positions")

            # Close all long positions (detect by size>0 or type==1)
            closed_positions = []
            for position in positions:
                pos_id = position.get("id") or position.get("positionId") or position.get("PositionId")
                pos_size = position.get("size")
                pos_type = position.get("type")
                is_long = (isinstance(pos_size, (int, float)) and pos_size > 0) or pos_type == 1
                if is_long and pos_id is not None:
                    logger.info(f"Closing long position {pos_id} with size {pos_size}")
                    result = await self.trading_bot.close_position(pos_id)
                    if "error" not in result:
                        closed_positions.append(pos_id)
                    else:
                        logger.warning(f"Failed to close position {pos_id}: {result['error']}")
            
            # Cancel all open orders
            logger.info("Canceling all open orders")
            open_orders = await self.trading_bot.get_open_orders()
            canceled_orders = []
            if open_orders:
                for order in open_orders:
                    # Be defensive about field names from API
                    order_id = order.get("id") or order.get("orderId") or order.get("OrderId") or order.get("order_id")
                    symbol = order.get("symbol") or order.get("Symbol") or order.get("instrument") or order.get("Instrument") or "unknown"
                    if not order_id:
                        logger.warning(f"Skipping cancel for order without id: {order}")
                        continue
                    logger.info(f"Canceling order {order_id} for {symbol}")
                    result = await self.trading_bot.cancel_order(order_id)
                    if "error" not in result:
                        canceled_orders.append(order_id)
                    else:
                        logger.warning(f"Failed to cancel order {order_id}: {result['error']}")
            
            if closed_positions or canceled_orders:
                logger.info(f"Successfully closed {len(closed_positions)} long positions and canceled {len(canceled_orders)} orders")
                
                # Trigger immediate fill checks to detect position closes
                await self._trigger_fill_checks()
                
                return {
                    "success": True,
                    "action": "tp2_hit_long",
                    "result": {
                        "closed_positions": closed_positions, 
                        "canceled_orders": canceled_orders,
                        "positions_count": len(closed_positions),
                        "orders_count": len(canceled_orders)
                    },
                    "trade_info": trade_info
                }
            else:
                logger.info("No long positions or orders found to close/cancel")
                return {
                    "success": True,
                    "action": "tp2_hit_long",
                    "result": {"message": "No long positions or orders found to close/cancel"},
                    "trade_info": trade_info
                }
            
        except Exception as e:
            logger.error(f"Error executing tp2 hit long: {str(e)}")
            return {"success": False, "error": str(e)}
    
    async def _execute_tp2_hit_short(self, trade_info: Dict) -> Dict:
        """Execute tp2 hit short: close all open sell positions and cancel all corresponding stop/take profit orders"""
        try:
            logger.info("Executing tp2 hit short: closing all sell positions and canceling orders")
            
            # Get all open positions
            positions = await self.trading_bot.get_open_positions()
            if not positions:
                logger.info("No open positions found")
                return {
                    "success": True,
                    "action": "tp2_hit_short",
                    "result": {"message": "No positions to close"},
                    "trade_info": trade_info
                }
            
            # First try fast-path: cancel cached orders and close cached positions
            fast_cancel = await self.trading_bot.cancel_cached_orders()
            fast_close = await self.trading_bot.close_cached_positions()
            if fast_cancel.get("canceled") or fast_close.get("closed"):
                logger.info(f"Fast-path: canceled {len(fast_cancel.get('canceled', []))} orders, closed {len(fast_close.get('closed', []))} positions")

            # Close all short positions (detect by size<0 or type==2)
            closed_positions = []
            for position in positions:
                pos_id = position.get("id") or position.get("positionId") or position.get("PositionId")
                pos_size = position.get("size")
                pos_type = position.get("type")
                is_short = (isinstance(pos_size, (int, float)) and pos_size < 0) or pos_type == 2
                if is_short and pos_id is not None:
                    logger.info(f"Closing short position {pos_id} with size {pos_size}")
                    result = await self.trading_bot.close_position(pos_id)
                    if "error" not in result:
                        closed_positions.append(pos_id)
                    else:
                        logger.warning(f"Failed to close position {pos_id}: {result['error']}")
            
            # Cancel all open orders
            logger.info("Canceling all open orders")
            open_orders = await self.trading_bot.get_open_orders()
            canceled_orders = []
            if open_orders:
                for order in open_orders:
                    # Be defensive about field names from API
                    order_id = order.get("id") or order.get("orderId") or order.get("OrderId") or order.get("order_id")
                    symbol = order.get("symbol") or order.get("Symbol") or order.get("instrument") or order.get("Instrument") or "unknown"
                    if not order_id:
                        logger.warning(f"Skipping cancel for order without id: {order}")
                        continue
                    logger.info(f"Canceling order {order_id} for {symbol}")
                    result = await self.trading_bot.cancel_order(order_id)
                    if "error" not in result:
                        canceled_orders.append(order_id)
                    else:
                        logger.warning(f"Failed to cancel order {order_id}: {result['error']}")
            
            if closed_positions or canceled_orders:
                logger.info(f"Successfully closed {len(closed_positions)} short positions and canceled {len(canceled_orders)} orders")
                return {
                    "success": True,
                    "action": "tp2_hit_short",
                    "result": {
                        "closed_positions": closed_positions, 
                        "canceled_orders": canceled_orders,
                        "positions_count": len(closed_positions),
                        "orders_count": len(canceled_orders)
                    },
                    "trade_info": trade_info
                }
            else:
                logger.info("No short positions or orders found to close/cancel")
                return {
                    "success": True,
                    "action": "tp2_hit_short",
                    "result": {"message": "No short positions or orders found to close/cancel"},
                    "trade_info": trade_info
                }
            
        except Exception as e:
            logger.error(f"Error executing tp2 hit short: {str(e)}")
            return {"success": False, "error": str(e)}
    
    async def _execute_tp1_hit_long(self, trade_info: Dict) -> Dict:
        """Execute tp1 hit long: handle based on bracket type"""
        try:
            use_native_brackets = os.getenv('USE_NATIVE_BRACKETS', 'false').lower() in ('true', '1', 'yes', 'on')
            
            if use_native_brackets:
                # Using native OCO brackets - execute TP1 signal
                logger.info("Processing TP1 LONG signal (USE_NATIVE_BRACKETS=true)")
                close_entire_at_tp1 = self.webhook_server.close_entire_position_at_tp1
                
                if close_entire_at_tp1:
                    # Conservative mode: flatten all positions at TP1
                    logger.info("Executing tp1 hit long: flattening all positions (--close-entire-at-tp1=True)")
                    result = await self.trading_bot.flatten_all_positions(interactive=False)
                else:
                    # Profit-taking mode: partial close for initial profits
                    logger.info("Executing tp1 hit long: checking position size and partial closing (--close-entire-at-tp1=False)")
                    
                    # Get current position size
                    position_size = await self._get_position_size("BUY", trade_info.get("symbol", ""))
                    
                    if position_size > 1:
                        # Partial close: close TP1_FRACTION of the position (round up)
                        tp1_fraction = float(os.getenv('TP1_FRACTION', '0.75'))
                        contracts_to_close = int(position_size * tp1_fraction)
                        if contracts_to_close == 0:
                            contracts_to_close = 1  # At least close 1 contract
                        logger.info(f"TP1 hit: closing {contracts_to_close} contracts from {position_size} total long position ({tp1_fraction*100:.0f}% close)")
                        
                        # Place sell order to close partial position
                        result = await self.trading_bot.place_market_order(
                            symbol=trade_info.get("symbol", ""),
                            side="SELL",
                            quantity=contracts_to_close,
                            account_id=self.trading_bot.selected_account.get("id")
                        )
                        
                        if "error" in result:
                            logger.warning(f"TP1 partial close failed, flattening all positions: {result['error']}")
                            result = await self.trading_bot.flatten_all_positions(interactive=False)
                    else:
                        # Single contract or no position, flatten all
                        logger.info("TP1 hit: single contract or no position, flattening all positions")
                        result = await self.trading_bot.flatten_all_positions(interactive=False)
            else:
                # Using separate limit orders - ignore TP1 signal
                logger.info("Ignoring TP1 LONG signal (USE_NATIVE_BRACKETS=false). Limit orders manage exits.")
                return {"success": True, "action": "tp1_hit_long", "ignored": True, "reason": "Using separate limit orders"}
            
            if "error" in result:
                return {"success": False, "error": result["error"]}
            
            # Run monitor to check for any position/order adjustments needed after TP1 hit
            logger.info("Running position monitor after TP1 hit long...")
            monitor_result = await self.trading_bot.monitor_position_changes()
            logger.info(f"Monitor result: {monitor_result}")
            
            return {
                "success": True,
                "action": "tp1_hit_long",
                "result": result,
                "trade_info": trade_info,
                "monitor_result": monitor_result
            }
        except Exception as e:
            logger.error(f"Error executing tp1 hit long: {str(e)}")
            return {"success": False, "error": str(e)}
    
    async def _execute_tp1_hit_short(self, trade_info: Dict) -> Dict:
        """Execute tp1 hit short: handle based on bracket type"""
        try:
            use_native_brackets = os.getenv('USE_NATIVE_BRACKETS', 'false').lower() in ('true', '1', 'yes', 'on')
            
            if use_native_brackets:
                # Using native OCO brackets - execute TP1 signal
                logger.info("Processing TP1 SHORT signal (USE_NATIVE_BRACKETS=true)")
                close_entire_at_tp1 = self.webhook_server.close_entire_position_at_tp1
            
                if close_entire_at_tp1:
                    # Conservative mode: flatten all positions at TP1
                    logger.info("Executing tp1 hit short: flattening all positions (--close-entire-at-tp1=True)")
                    result = await self.trading_bot.flatten_all_positions(interactive=False)
                else:
                    # Profit-taking mode: partial close for initial profits
                    logger.info("Executing tp1 hit short: checking position size and partial closing (--close-entire-at-tp1=False)")
                    
                    # Get current position size
                    position_size = await self._get_position_size("SELL", trade_info.get("symbol", ""))
                    
                    if position_size > 1:
                        # Partial close: close TP1_FRACTION of the position (round up)
                        tp1_fraction = float(os.getenv('TP1_FRACTION', '0.75'))
                        contracts_to_close = int(position_size * tp1_fraction)
                        if contracts_to_close == 0:
                            contracts_to_close = 1  # At least close 1 contract
                        logger.info(f"TP1 hit: closing {contracts_to_close} contracts from {position_size} total short position ({tp1_fraction*100:.0f}% close)")
                        
                        # Place buy order to close partial position
                        result = await self.trading_bot.place_market_order(
                            symbol=trade_info.get("symbol", ""),
                            side="BUY",
                            quantity=contracts_to_close,
                            account_id=self.trading_bot.selected_account.get("id")
                        )
                        
                        if "error" in result:
                            logger.warning(f"TP1 partial close failed, flattening all positions: {result['error']}")
                            result = await self.trading_bot.flatten_all_positions(interactive=False)
                    else:
                        # Single contract or no position, flatten all
                        logger.info("TP1 hit: single contract or no position, flattening all positions")
                        result = await self.trading_bot.flatten_all_positions(interactive=False)
            else:
                # Using separate limit orders - ignore TP1 signal
                logger.info("Ignoring TP1 SHORT signal (USE_NATIVE_BRACKETS=false). Limit orders manage exits.")
                return {"success": True, "action": "tp1_hit_short", "ignored": True, "reason": "Using separate limit orders"}
            
            if "error" in result:
                return {"success": False, "error": result["error"]}
            
            # Run monitor to check for any position/order adjustments needed after TP1 hit
            logger.info("Running position monitor after TP1 hit short...")
            monitor_result = await self.trading_bot.monitor_position_changes()
            logger.info(f"Monitor result: {monitor_result}")
            
            return {
                "success": True,
                "action": "tp1_hit_short",
                "result": result,
                "trade_info": trade_info,
                "monitor_result": monitor_result
            }
        except Exception as e:
            logger.error(f"Error executing tp1 hit short: {str(e)}")
            return {"success": False, "error": str(e)}
    
    async def _execute_tp3_hit_long(self, trade_info: Dict) -> Dict:
        """Execute tp3 hit long: close all open buy positions and cancel all corresponding stop/take profit orders"""
        try:
            logger.info("Executing tp3 hit long: closing all buy positions")
            
            result = await self.trading_bot.flatten_all_positions(interactive=False)
            
            if "error" in result:
                return {"success": False, "error": result["error"]}
            
            return {
                "success": True,
                "action": "tp3_hit_long",
                "result": result,
                "trade_info": trade_info
            }
        except Exception as e:
            logger.error(f"Error executing tp3 hit long: {str(e)}")
            return {"success": False, "error": str(e)}
    
    async def _execute_tp3_hit_short(self, trade_info: Dict) -> Dict:
        """Execute tp3 hit short: close all open sell positions and cancel all corresponding stop/take profit orders"""
        try:
            logger.info("Executing tp3 hit short: closing all sell positions")
            
            result = await self.trading_bot.flatten_all_positions(interactive=False)
            
            if "error" in result:
                return {"success": False, "error": result["error"]}
            
            return {
                "success": True,
                "action": "tp3_hit_short",
                "result": result,
                "trade_info": trade_info
            }
        except Exception as e:
            logger.error(f"Error executing tp3 hit short: {str(e)}")
            return {"success": False, "error": str(e)}
    
    async def _execute_close_long(self, trade_info: Dict) -> Dict:
        """Execute close long: close all open buy positions"""
        try:
            logger.info("Executing close long: closing all buy positions")
            
            result = await self.trading_bot.flatten_all_positions(interactive=False)
            
            if "error" in result:
                return {"success": False, "error": result["error"]}
            
            # Trigger immediate fill checks to detect position closes
            await self._trigger_fill_checks()
            
            return {
                "success": True,
                "action": "close_long",
                "result": result,
                "trade_info": trade_info
            }
        except Exception as e:
            logger.error(f"Error executing close long: {str(e)}")
            return {"success": False, "error": str(e)}
    
    async def _execute_close_short(self, trade_info: Dict) -> Dict:
        """Execute close short: close all open sell positions"""
        try:
            logger.info("Executing close short: closing all sell positions")
            
            result = await self.trading_bot.flatten_all_positions(interactive=False)
            
            if "error" in result:
                return {"success": False, "error": result["error"]}
            
            # Trigger immediate fill checks to detect position closes
            await self._trigger_fill_checks()
            
            return {
                "success": True,
                "action": "close_short",
                "result": result,
                "trade_info": trade_info
            }
        except Exception as e:
            logger.error(f"Error executing close short: {str(e)}")
            return {"success": False, "error": str(e)}
    
    async def _execute_exit_long(self, trade_info: Dict) -> Dict:
        """Execute exit long: close all open buy positions"""
        try:
            logger.info("Executing exit long: closing all buy positions")
            
            result = await self.trading_bot.flatten_all_positions(interactive=False)
            
            if "error" in result:
                return {"success": False, "error": result["error"]}
            
            # Trigger immediate fill checks to detect position closes
            await self._trigger_fill_checks()
            
            return {
                "success": True,
                "action": "exit_long",
                "result": result,
                "trade_info": trade_info
            }
        except Exception as e:
            logger.error(f"Error executing exit long: {str(e)}")
            return {"success": False, "error": str(e)}
    
    async def _execute_exit_short(self, trade_info: Dict) -> Dict:
        """Execute exit short: close all open sell positions"""
        try:
            logger.info("Executing exit short: closing all sell positions")
            
            result = await self.trading_bot.flatten_all_positions(interactive=False)
            
            if "error" in result:
                return {"success": False, "error": result["error"]}
            
            # Trigger immediate fill checks to detect position closes
            await self._trigger_fill_checks()
            
            return {
                "success": True,
                "action": "exit_short",
                "result": result,
                "trade_info": trade_info
            }
        except Exception as e:
            logger.error(f"Error executing exit short: {str(e)}")
            return {"success": False, "error": str(e)}
    
    async def _execute_session_close(self, trade_info: Dict) -> Dict:
        """Execute session close: flatten all positions at end of session"""
        try:
            logger.info("Executing session close: flattening all positions")
            
            result = await self.trading_bot.flatten_all_positions(interactive=False)
            
            if "error" in result:
                return {"success": False, "error": result["error"]}
            
            # Trigger immediate fill checks to detect position closes
            await self._trigger_fill_checks()
            
            return {
                "success": True,
                "action": "session_close",
                "result": result,
                "trade_info": trade_info
            }
        except Exception as e:
            logger.error(f"Error executing session close: {str(e)}")
            return {"success": False, "error": str(e)}
    
    async def _execute_ignore_signal(self, trade_info: Dict) -> Dict:
        """Execute ignore signal: do nothing (signal is handled by limit orders)"""
        try:
            logger.info("Ignoring signal - using limit orders for partial closes")
            
            return {
                "success": True,
                "action": "ignore_signal",
                "result": "Signal ignored - using limit orders for TP1 partial closes",
                "trade_info": trade_info
            }
        except Exception as e:
            logger.error(f"Error executing ignore signal: {str(e)}")
            return {"success": False, "error": str(e)}

class WebhookServer:
    """TradingView webhook server"""
    
    def __init__(self, trading_bot: TopStepXTradingBot, host: str = "localhost", port: int = 8080, 
                 account_id: str = None, position_size: int = None, close_entire_position_at_tp1: bool = False):
        self.trading_bot = trading_bot
        self.host = host
        self.port = port
        self.server = None
        self.server_thread = None
        
        # Trading configuration - use environment variables if not provided
        self.account_id = account_id
        self.position_size = position_size if position_size is not None else int(os.getenv('POSITION_SIZE', '1'))
        self.close_entire_position_at_tp1 = close_entire_position_at_tp1
        
        # FIXED: Enhanced debounce control to prevent duplicate opens
        self._last_open_signal_ts = {}
        # Increased debounce window to 5 minutes (300 seconds) to prevent rapid duplicate signals
        self.debounce_seconds = int(os.getenv('DEBOUNCE_SECONDS', '300'))
        
        # Initialize WebSocket server
        self.websocket_server = None
        self._init_websocket_server()
        
        # Position size validation
        self.max_position_size = int(os.getenv('MAX_POSITION_SIZE', str(position_size * 2)))
        
        # Bracket monitoring control
        self._bracket_monitoring_enabled = True
        self._last_bracket_check = None
        
        # Background fill checker
        self._fills_worker_active = False
        self._fills_worker_thread = None
        self._last_fill_check_ts = None
        # Track server start and env/account consistency
        try:
            self.server_start_ts = __import__('time').time()
        except Exception:
            self.server_start_ts = None
        # Cache intended env account id for consistency checks
        try:
            self.env_account_id = os.getenv('TOPSTEPX_ACCOUNT_ID') or os.getenv('PROJECT_X_ACCOUNT_ID')
            if isinstance(self.env_account_id, str):
                self.env_account_id = self.env_account_id.strip().strip('\'"')
        except Exception:
            self.env_account_id = None
        # Startup block controls
        self._startup_blocked = False
        self._startup_block_reason = None
    
    def _init_websocket_server(self):
        """Initialize WebSocket server for real-time updates"""
        try:
            # Check if WebSocket is enabled via environment variable
            websocket_enabled = os.getenv('WEBSOCKET_ENABLED', 'false').lower() in ('true', '1', 'yes', 'on')
            if not websocket_enabled:
                logger.info("WebSocket server disabled via WEBSOCKET_ENABLED=false")
                self.websocket_server = None
                return
                
            from websocket_server import WebSocketServer
            # Use the same port as the main server for Railway compatibility
            websocket_port = int(os.getenv('WEBSOCKET_PORT', str(self.port)))
            self.websocket_server = WebSocketServer(
                trading_bot=self.trading_bot,
                webhook_server=self,
                host=self.host,
                port=websocket_port
            )
            logger.info(f"WebSocket server initialized on port {websocket_port}")
        except Exception as e:
            logger.warning(f"Failed to initialize WebSocket server: {e}")
            self.websocket_server = None
    
    def start(self):
        """Start the webhook server"""
        try:
            # Create handler with trading bot and webhook server
            handler = lambda *args, **kwargs: WebhookHandler(self.trading_bot, self, *args, **kwargs)
            
            # Create HTTP server
            self.server = HTTPServer((self.host, self.port), handler)
            
            logger.info(f"Starting webhook server on {self.host}:{self.port}")
            logger.info(f"Webhook URL: http://{self.host}:{self.port}/")
            
            # Start server in a separate thread
            self.server_thread = threading.Thread(target=self.server.serve_forever)
            self.server_thread.daemon = True
            self.server_thread.start()
            
            logger.info("Webhook server started successfully!")
            
            # Start WebSocket server if available
            if self.websocket_server:
                try:
                    # Start WebSocket server in a separate thread
                    def start_websocket():
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                        loop.run_until_complete(self.websocket_server.start())
                        loop.run_forever()
                    
                    websocket_thread = threading.Thread(target=start_websocket)
                    websocket_thread.daemon = True
                    websocket_thread.start()
                    logger.info("WebSocket server started")
                except Exception as e:
                    logger.warning(f"Failed to start WebSocket server: {e}")
            
            # Start periodic bracket monitoring only if not startup blocked
            if not self._startup_blocked and self._bracket_monitoring_enabled:
                self._start_bracket_monitoring_task()
            
            # Start background fill checker only when consistent and not blocked
            try:
                sel_id = str((self.trading_bot.selected_account or {}).get('id')) if self.trading_bot and self.trading_bot.selected_account else None
            except Exception:
                sel_id = None
            env_id = str(self.env_account_id) if self.env_account_id else None
            if not self._startup_blocked and sel_id and env_id and sel_id == env_id:
                self._start_fills_worker()
            else:
                logger.warning("Fills worker not started due to startup_blocked or account inconsistency")
            
        except Exception as e:
            logger.error(f"Failed to start webhook server: {str(e)}")
            raise
    
    def _start_bracket_monitoring_task(self):
        """Start background task for periodic bracket monitoring"""
        import threading
        import time
        
        def monitor_brackets():
            while True:
                try:
                    if hasattr(self, 'trading_bot') and self.trading_bot:
                        # Run bracket monitoring every 30 seconds
                        time.sleep(30)
                        if self._bracket_monitoring_enabled:
                            logger.info("Running periodic bracket monitoring...")
                            # This would need to be run in an async context
                            # For now, we'll rely on manual calls after each signal
                except Exception as e:
                    logger.error(f"Bracket monitoring task error: {e}")
                    time.sleep(60)  # Wait longer on error
        
        monitor_thread = threading.Thread(target=monitor_brackets, daemon=True)
        monitor_thread.start()
        logger.info("Started background bracket monitoring task")
    
    def _start_fills_worker(self):
        """Start background task for periodic fill checking"""
        import threading
        import time
        import asyncio
        
        def fills_worker():
            self._fills_worker_active = True
            logger.info("Started background fill checker")
            
            # Create new event loop for this thread
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            async def check_fills():
                while self._fills_worker_active:
                    try:
                        if hasattr(self, 'trading_bot') and self.trading_bot and self.account_id:
                            # Enforce account consistency before running
                            sel = None
                            try:
                                sel = (self.trading_bot.selected_account or {}).get('id')
                            except Exception:
                                sel = None
                            sel_id = str(sel) if sel is not None else None
                            env_id = str(self.env_account_id) if self.env_account_id else None
                            acc_id = str(self.account_id)
                            if env_id and acc_id != env_id:
                                # Server configured with mismatched account; skip and warn
                                logger.warning(f"Fills worker skipped: server account_id={acc_id} != env TOPSTEPX_ACCOUNT_ID={env_id}")
                            elif sel_id and sel_id != acc_id:
                                # Selected account mismatch; skip to avoid wrong-account notifications
                                logger.warning(f"Fills worker skipped: selected_account={sel_id} != server account_id={acc_id}")
                            else:
                                await self.trading_bot.check_order_fills(self.account_id)
                            self._last_fill_check_ts = time.time()
                    except Exception as e:
                        logger.error(f"Fill checker error: {e}")
                    
                    # Wait 30 seconds between checks
                    await asyncio.sleep(30)
            
            try:
                loop.run_until_complete(check_fills())
            except Exception as e:
                logger.error(f"Fills worker loop error: {e}")
            finally:
                loop.close()
        
        self._fills_worker_thread = threading.Thread(target=fills_worker, daemon=True)
        self._fills_worker_thread.start()
        logger.info("Started background fill checker")
    
    def stop(self):
        """Stop the webhook server"""
        if self.server:
            logger.info("Stopping webhook server...")
            
            # Stop fills worker
            self._fills_worker_active = False
            if self._fills_worker_thread:
                self._fills_worker_thread.join(timeout=5)
            
            # Stop WebSocket server if running
            if self.websocket_server:
                try:
                    asyncio.create_task(self.websocket_server.stop())
                    logger.info("WebSocket server stopped")
                except Exception as e:
                    logger.warning(f"Error stopping WebSocket server: {e}")
            
            self.server.shutdown()
            self.server.server_close()
            if self.server_thread:
                self.server_thread.join()
            logger.info("Webhook server stopped")


async def select_account_interactive(bot):
    """Interactive account selection for webhook server"""
    accounts = await bot.list_accounts()
    if not accounts:
        logger.error("No accounts available")
        return None
    
    print("\n" + "="*60)
    print("SELECT ACCOUNT FOR WEBHOOK TRADING")
    print("="*60)
    
    # Display accounts
    print(f"{'#':<3} {'Account Name':<25} {'ID':<12} {'Status':<8} {'Balance':<15} {'Type':<10}")
    print("-" * 80)
    
    for i, account in enumerate(accounts, 1):
        print(f"{i:<3} {account['name']:<25} {account['id']:<12} {account['status']:<8} "
              f"${account['balance']:>12,.2f} {account['account_type']:<10}")
    
    print("="*60)
    
    while True:
        try:
            choice = input(f"\nSelect account for webhook trading (1-{len(accounts)}, or 'q' to quit): ").strip()
            
            if choice.lower() == 'q':
                return None
            
            account_index = int(choice) - 1
            if 0 <= account_index < len(accounts):
                selected_account = accounts[account_index]
                print(f"✅ Selected account: {selected_account['name']}")
                return selected_account
            else:
                print("❌ Invalid selection. Please try again.")
        except ValueError:
            print("❌ Invalid input. Please enter a number or 'q' to quit.")
        except KeyboardInterrupt:
            print("\n👋 Exiting account selection.")
            return None

async def main():
    """Main function to run the webhook server"""
    import os
    
    # Load environment variables - handle Railway deployment
    try:
        import load_env
        logger.info("✅ Environment variables loaded from .env file")
    except Exception as e:
        logger.warning(f"⚠️ Could not load .env file: {e}")
        logger.info("Using system environment variables (Railway deployment)")
    
    # Read configuration from environment variables
    api_key = os.getenv('TOPSTEPX_API_KEY') or os.getenv('PROJECT_X_API_KEY')
    username = os.getenv('TOPSTEPX_USERNAME') or os.getenv('PROJECT_X_USERNAME')
    
    # Enhanced account ID parsing with robust normalization for Railway
    raw_account_id = os.getenv('TOPSTEPX_ACCOUNT_ID') or os.getenv('PROJECT_X_ACCOUNT_ID')
    logger.info(f"Raw account ID from environment: {repr(raw_account_id)}")
    logger.info(f"TOPSTEPX_ACCOUNT_ID: {repr(os.getenv('TOPSTEPX_ACCOUNT_ID'))}")
    logger.info(f"PROJECT_X_ACCOUNT_ID: {repr(os.getenv('PROJECT_X_ACCOUNT_ID'))}")
    
    account_id = None
    if raw_account_id:
        # Railway stringifies all env vars, so we need robust normalization
        # Strip all whitespace and quotes, convert to string, then to int and back to string for consistency
        account_id_str = str(raw_account_id).strip().strip('\'"').strip()
        try:
            # Convert to int first to handle any type issues, then back to string
            account_id_int = int(account_id_str)
            account_id = str(account_id_int)  # Ensure it's a clean string
            logger.info(f"Normalized account ID: {repr(account_id)} (converted from {repr(account_id_str)})")
        except (ValueError, TypeError) as e:
            logger.error(f"Invalid account ID format: {repr(raw_account_id)} - {e}")
            account_id = account_id_str  # Fallback to string
        logger.info(f"Account ID type: {type(account_id)}, length: {len(account_id)}")
    else:
        logger.warning("No account ID found in environment variables")
    # Debug environment variables
    pos_size_env = os.getenv('POSITION_SIZE')
    logger.info(f"POSITION_SIZE environment variable: {repr(pos_size_env)}")
    position_size = int(os.getenv('POSITION_SIZE', '1'))
    logger.info(f"Final position_size: {position_size}")
    
    close_entire_at_tp1 = os.getenv('CLOSE_ENTIRE_POSITION_AT_TP1', 'false').lower() in ('true', '1', 'yes', 'on')
    use_native_brackets = os.getenv('USE_NATIVE_BRACKETS', 'false').lower() in ('true', '1', 'yes', 'on')
    # TP1 split fraction (0.0-1.0). Default 0.75
    try:
        tp1_fraction_env = os.getenv('TP1_FRACTION', '0.75')
        tp1_fraction = float(tp1_fraction_env)
        if not (0.0 < tp1_fraction < 1.0):
            raise ValueError("TP1_FRACTION must be between 0 and 1")
    except Exception:
        tp1_fraction = 0.75
        logger.warning("Invalid or missing TP1_FRACTION; defaulting to 0.75")
    host = os.getenv('WEBHOOK_HOST', '0.0.0.0')
    port = int(os.getenv('PORT', '8080'))
    
    # Log configuration
    logger.info("=== WEBHOOK SERVER CONFIGURATION ===")
    logger.info(f"API Key: {'***' + api_key[-4:] if api_key else 'Not set'}")
    logger.info(f"Username: {username}")
    logger.info(f"Account ID: {account_id or 'Auto-select'}")
    logger.info(f"Position Size: {position_size} contracts")
    logger.info(f"Close Entire at TP1: {close_entire_at_tp1}")
    logger.info(f"TP1 Fraction: {tp1_fraction}")
    logger.info(f"Host: {host}")
    logger.info(f"Port: {port}")
    logger.info("=====================================")
    
    if not api_key or not username:
        logger.error("Missing API credentials. Please set PROJECT_X_API_KEY and PROJECT_X_USERNAME")
        return
    
    # Create trading bot
    bot = TopStepXTradingBot(api_key=api_key, username=username)
    
    # Authenticate
    if not await bot.authenticate():
        logger.error("Failed to authenticate with TopStepX API")
        return
    
    # Get accounts
    accounts = await bot.list_accounts()
    if not accounts:
        logger.error("No accounts available")
        return
    
    # Log all accounts returned for diagnostics
    try:
        logger.info("Accounts returned by API:")
        for a in accounts:
            logger.info(f" - {a.get('name')} (ID: {a.get('id')})")
    except Exception:
        pass

    # Enhanced account selection with detailed logging
    selected_account = None
    if account_id:
        logger.info(f"Searching for account ID: {repr(account_id)}")
        logger.info(f"Target account ID type: {type(account_id)}")
        
        for i, account in enumerate(accounts):
            account_id_from_api = account.get('id')
            account_name = account.get('name', 'Unknown')
            
            # Normalize both sides for comparison - Railway stringifies everything
            api_id_str = str(account_id_from_api).strip()
            target_id_str = str(account_id).strip()
            
            # Convert both to integers for comparison (Railway env vars are strings)
            api_id_int = None
            target_id_int = None
            try:
                api_id_int = int(api_id_str)
                target_id_int = int(target_id_str)
            except (ValueError, TypeError):
                pass
            
            logger.info(f"Comparing account {i+1}: {account_name}")
            logger.info(f"  API ID: {repr(api_id_str)} (type: {type(api_id_str)})")
            logger.info(f"  Target ID: {repr(target_id_str)} (type: {type(target_id_str)})")
            logger.info(f"  String match: {api_id_str == target_id_str}")
            if api_id_int is not None and target_id_int is not None:
                logger.info(f"  Integer match: {api_id_int == target_id_int}")
            
            # Primary comparison: integer comparison (most reliable for Railway)
            # Fallback: string comparison
            match_found = False
            if api_id_int is not None and target_id_int is not None and api_id_int == target_id_int:
                match_found = True
                logger.info(f"  ✅ Integer comparison match: {api_id_int} == {target_id_int}")
            elif api_id_str == target_id_str:
                match_found = True
                logger.info(f"  ✅ String comparison match: {repr(api_id_str)} == {repr(target_id_str)}")
            
            if match_found:
                selected_account = account
                logger.info(f"✅ MATCH FOUND: {account_name} (ID: {account_id_from_api})")
                break
            else:
                logger.info(f"❌ No match for {account_name}")
        
        if not selected_account:
            logger.error(f"❌ Account ID {repr(account_id)} not found in returned accounts; startup blocked")
            logger.error("Available account IDs:")
            for a in accounts:
                logger.error(f"  - {a.get('name')}: {repr(str(a.get('id')))}")
            # Do not bind a placeholder account; keep selected_account as None for health to reflect reality
    else:
        logger.error("❌ TOPSTEPX_ACCOUNT_ID missing; startup blocked")
        selected_account = None
    
    bot.selected_account = selected_account
    if bot.selected_account:
        logger.info(f"✅ Using account: {bot.selected_account['name']} (ID: {bot.selected_account['id']})")
        logger.info(f"✅ Bot selected_account set to: {bot.selected_account}")
    else:
        logger.error("❌ No selected account. Service will report unhealthy until correct account is available.")
    logger.info(f"Position size: {position_size} contracts")
    logger.info(f"Close entire position at TP1: {close_entire_at_tp1}")
    
    # Hard account guard: ensure env TOPSTEPX_ACCOUNT_ID matches selected account
    env_account_id = os.getenv('TOPSTEPX_ACCOUNT_ID') or os.getenv('PROJECT_X_ACCOUNT_ID')
    if env_account_id and (not bot.selected_account or str(env_account_id) != str(bot.selected_account.get('id'))):
        logger.error(f"Account guard failed: env TOPSTEPX_ACCOUNT_ID={env_account_id} != selected_account.id={bot.selected_account.get('id')}")
        logger.error("Refusing to start to avoid operating on the wrong account.")
        # proceed to start server but with startup_blocked for health and blocked POSTs
    
    # Seed notified orders to suppress historical notifications on cold start
    # Only do this if we have a valid selected account
    if bot.selected_account:
        try:
            recent_filled = await bot.get_order_history(account_id=bot.selected_account.get('id'), limit=50)
            if hasattr(bot, '_notified_orders') and isinstance(recent_filled, list):
                pre_count = len(bot._notified_orders)
                for o in recent_filled:
                    oid = o.get('id')
                    if oid is not None:
                        bot._notified_orders.add(str(oid))
                logger.info(f"Seeded notified orders with {len(bot._notified_orders) - pre_count} historical fills to prevent startup spam")
        except Exception as seed_err:
            logger.warning(f"Failed to seed notified orders: {seed_err}")
    else:
        logger.warning("Skipping order history seeding - no selected account")
    
    # Start webhook server with configuration
    webhook_server = WebhookServer(
        bot, 
        host=host, 
        port=port,
        account_id=account_id,
        position_size=position_size,  # This will use env var if None
        close_entire_position_at_tp1=close_entire_at_tp1
    )

    # If strict selection failed, set startup_blocked and reason
    if account_id and (not selected_account or str(selected_account.get('id')) != str(account_id)):
        webhook_server._startup_blocked = True
        webhook_server._startup_block_reason = f"Configured account {account_id} not found in API accounts"
        logger.error(webhook_server._startup_block_reason)
    elif not account_id:
        webhook_server._startup_blocked = True
        webhook_server._startup_block_reason = "TOPSTEPX_ACCOUNT_ID missing"
        logger.error(webhook_server._startup_block_reason)
    webhook_server.start()
    
    try:
        # Keep the server running
        logger.info("Webhook server is running. Press Ctrl+C to stop.")
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        webhook_server.stop()

if __name__ == "__main__":
    import os
    asyncio.run(main())
