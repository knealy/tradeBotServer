"""
TradingView Webhook Server for TopStepX Trading Bot

This module handles incoming webhook requests from TradingView
and executes trades based on the JSON payloads.
"""

import asyncio
import json
import logging
import re
from datetime import datetime
from typing import Dict, Optional, List
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
import urllib.parse

# Import the trading bot
from trading_bot import TopStepXTradingBot

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
    
    def do_POST(self):
        """Handle POST requests from TradingView"""
        try:
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
                self._send_response(200, {"message": "Webhook processed successfully", "result": result})
            else:
                self._send_response(400, {"error": result.get("error", "Unknown error")})
                
        except Exception as e:
            logger.error(f"Error processing webhook: {str(e)}")
            self._send_response(500, {"error": "Internal server error"})
    
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
    
    def log_message(self, format, *args):
        """Override to use our logger"""
        logger.info(f"{self.address_string()} - {format % args}")
    
    async def _execute_signal_action(self, signal_type: str, trade_info: Dict) -> Dict:
        """Execute the appropriate action based on signal type"""
        try:
            if signal_type == "open_long":
                return await self._execute_open_long(trade_info)
            elif signal_type == "open_short":
                return await self._execute_open_short(trade_info)
            elif signal_type == "stop_out_long":
                return await self._execute_stop_out_long(trade_info)
            elif signal_type == "stop_out_short":
                return await self._execute_stop_out_short(trade_info)
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
            elif signal_type == "session_close":
                return await self._execute_session_close(trade_info)
            elif signal_type == "ignore_signal":
                return await self._execute_ignore_signal(trade_info)
            else:
                return {"success": False, "error": f"Unknown signal type: {signal_type}"}
        except Exception as e:
            logger.error(f"Error executing signal action: {str(e)}")
            return {"success": False, "error": str(e)}
    
    async def _execute_open_long(self, trade_info: Dict) -> Dict:
        """Execute open long position using native bracket orders"""
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

            # Debounce duplicate open signals per symbol
            from datetime import datetime, timezone, timedelta
            now = datetime.now(timezone.utc)
            last_ts = self.webhook_server._last_open_signal_ts.get((symbol, "LONG"))
            if last_ts and (now - last_ts).total_seconds() < self.webhook_server.debounce_seconds:
                wait_left = int(self.webhook_server.debounce_seconds - (now - last_ts).total_seconds())
                logger.warning(f"Debounced duplicate open_long for {symbol}; received too soon. Wait {wait_left}s")
                return {"success": True, "action": "open_long", "debounced": True, "reason": "duplicate within debounce window"}
            self.webhook_server._last_open_signal_ts[(symbol, "LONG")] = now
            
            # Choose between full TP1 exit or staged TP1/TP2 exits
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
                # Staged exit: Single entry + Two separate OCO exit orders for SAME position
                tp1_fraction = float(os.getenv('TP1_FRACTION', '0.75')) if os.getenv('TP1_FRACTION') else 0.75
                try:
                    if not (0.0 < tp1_fraction < 1.0):
                        tp1_fraction = 0.75
                except Exception:
                    tp1_fraction = 0.75
                tp1_quantity = max(1, int(round(position_size * tp1_fraction)))
                tp2_quantity = max(0, position_size - tp1_quantity)
                logger.info(f"Staged exit setup: Single entry {position_size}, then two OCO orders: -{tp1_quantity}@TP1, -{tp2_quantity}@TP2, both with SL {stop_loss}")

                # Step 1: Place the entry order (market buy) - SINGLE ENTRY
                entry_result = await self.trading_bot.place_market_order(
                    symbol=symbol,
                    side="BUY",
                    quantity=position_size,
                    account_id=self.webhook_server.account_id
                )
                if "error" in entry_result:
                    return {"success": False, "error": entry_result["error"]}
                
                logger.info(f"Single entry order placed: BUY {position_size}")

                # Step 2: Create first OCO exit order (-2 contracts with SL + TP1)
                result_tp1 = {"success": True}
                if tp1_quantity > 0:
                    result_tp1 = await self.trading_bot.create_bracket_order(
                        symbol=symbol,
                        side="SELL",  # Exit order for TP1 portion
                        quantity=tp1_quantity,
                        stop_loss_price=stop_loss,
                        take_profit_price=take_profit_1,
                        account_id=self.webhook_server.account_id
                    )
                    if "error" in result_tp1:
                        logger.error(f"TP1 OCO bracket failed: {result_tp1['error']}")
                        return {"success": False, "error": result_tp1["error"]}
                    logger.info(f"TP1 OCO created: SELL {tp1_quantity} with SL {stop_loss} + TP {take_profit_1}")

                # Step 3: Create second OCO exit order (-1 contract with SL + TP2)
                result_tp2 = {"success": True}
                if tp2_quantity > 0:
                    result_tp2 = await self.trading_bot.create_bracket_order(
                        symbol=symbol,
                        side="SELL",  # Exit order for TP2 portion
                        quantity=tp2_quantity,
                        stop_loss_price=stop_loss,
                        take_profit_price=take_profit_2,
                        account_id=self.webhook_server.account_id
                    )
                    if "error" in result_tp2:
                        logger.error(f"TP2 OCO bracket failed: {result_tp2['error']}")
                        return {"success": False, "error": result_tp2["error"]}
                    logger.info(f"TP2 OCO created: SELL {tp2_quantity} with SL {stop_loss} + TP {take_profit_2}")

                # Consolidated result
                result = {
                    "success": True, 
                    "entry": entry_result,
                    "tp1_oco": result_tp1, 
                    "tp2_oco": result_tp2,
                    "message": f"Staged exit: Single entry {position_size}, two OCO orders: -{tp1_quantity}@TP1, -{tp2_quantity}@TP2"
                }
            
            if "error" in result:
                return {"success": False, "error": result["error"]}
            
            logger.info(f"Open long executed: {result}")
            
            # Run monitor to check for any position/order adjustments needed
            logger.info("Running position monitor after open long...")
            monitor_result = await self.trading_bot.monitor_position_changes()
            logger.info(f"Monitor result: {monitor_result}")
            
            # Also run bracket monitoring for position management
            logger.info("Running bracket position monitoring...")
            bracket_monitor_result = await self.trading_bot.monitor_all_bracket_positions()
            logger.info(f"Bracket monitor result: {bracket_monitor_result}")
            
            return {
                "success": True,
                "action": "open_long",
                "order_result": result,
                "trade_info": trade_info,
                "configuration": {
                    "position_size": position_size,
                    "close_entire_at_tp1": close_entire_at_tp1,
                    "take_profit_level_used": "TP2"
                },
                "monitor_result": monitor_result
            }
        except Exception as e:
            logger.error(f"Error executing open long: {str(e)}")
            return {"success": False, "error": str(e)}
    
    async def _execute_open_short(self, trade_info: Dict) -> Dict:
        """Execute open short position using native bracket orders"""
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

            # Debounce duplicate open signals per symbol
            from datetime import datetime, timezone, timedelta
            now = datetime.now(timezone.utc)
            last_ts = self.webhook_server._last_open_signal_ts.get((symbol, "SHORT"))
            if last_ts and (now - last_ts).total_seconds() < self.webhook_server.debounce_seconds:
                wait_left = int(self.webhook_server.debounce_seconds - (now - last_ts).total_seconds())
                logger.warning(f"Debounced duplicate open_short for {symbol}; received too soon. Wait {wait_left}s")
                return {"success": True, "action": "open_short", "debounced": True, "reason": "duplicate within debounce window"}
            self.webhook_server._last_open_signal_ts[(symbol, "SHORT")] = now
            
            # Choose between full TP1 exit or staged TP1/TP2 exits
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
                # Staged exit: Single entry + Two separate OCO exit orders for SAME position
                tp1_fraction = float(os.getenv('TP1_FRACTION', '0.75')) if os.getenv('TP1_FRACTION') else 0.75
                try:
                    if not (0.0 < tp1_fraction < 1.0):
                        tp1_fraction = 0.75
                except Exception:
                    tp1_fraction = 0.75
                tp1_quantity = max(1, int(round(position_size * tp1_fraction)))
                tp2_quantity = max(0, position_size - tp1_quantity)
                logger.info(f"Staged exit setup: Single entry {position_size}, then two OCO orders: +{tp1_quantity}@TP1, +{tp2_quantity}@TP2, both with SL {stop_loss}")

                # Step 1: Place the entry order (market sell) - SINGLE ENTRY
                entry_result = await self.trading_bot.place_market_order(
                    symbol=symbol,
                    side="SELL",
                    quantity=position_size,
                    account_id=self.webhook_server.account_id
                )
                if "error" in entry_result:
                    return {"success": False, "error": entry_result["error"]}
                
                logger.info(f"Single entry order placed: SELL {position_size}")

                # Step 2: Create first OCO exit order (+2 contracts with SL + TP1)
                result_tp1 = {"success": True}
                if tp1_quantity > 0:
                    result_tp1 = await self.trading_bot.create_bracket_order(
                        symbol=symbol,
                        side="BUY",  # Exit order for TP1 portion
                        quantity=tp1_quantity,
                        stop_loss_price=stop_loss,
                        take_profit_price=take_profit_1,
                        account_id=self.webhook_server.account_id
                    )
                    if "error" in result_tp1:
                        logger.error(f"TP1 OCO bracket failed: {result_tp1['error']}")
                        return {"success": False, "error": result_tp1["error"]}
                    logger.info(f"TP1 OCO created: BUY {tp1_quantity} with SL {stop_loss} + TP {take_profit_1}")

                # Step 3: Create second OCO exit order (+1 contract with SL + TP2)
                result_tp2 = {"success": True}
                if tp2_quantity > 0:
                    result_tp2 = await self.trading_bot.create_bracket_order(
                        symbol=symbol,
                        side="BUY",  # Exit order for TP2 portion
                        quantity=tp2_quantity,
                        stop_loss_price=stop_loss,
                        take_profit_price=take_profit_2,
                        account_id=self.webhook_server.account_id
                    )
                    if "error" in result_tp2:
                        logger.error(f"TP2 OCO bracket failed: {result_tp2['error']}")
                        return {"success": False, "error": result_tp2["error"]}
                    logger.info(f"TP2 OCO created: BUY {tp2_quantity} with SL {stop_loss} + TP {take_profit_2}")

                # Consolidated result
                result = {
                    "success": True, 
                    "entry": entry_result,
                    "tp1_oco": result_tp1, 
                    "tp2_oco": result_tp2,
                    "message": f"Staged exit: Single entry {position_size}, two OCO orders: +{tp1_quantity}@TP1, +{tp2_quantity}@TP2"
                }
            
            if "error" in result:
                return {"success": False, "error": result["error"]}
            
            logger.info(f"Open short executed: {result}")
            
            # Run monitor to check for any position/order adjustments needed
            logger.info("Running position monitor after open short...")
            monitor_result = await self.trading_bot.monitor_position_changes()
            logger.info(f"Monitor result: {monitor_result}")
            
            # Also run bracket monitoring for position management
            logger.info("Running bracket position monitoring...")
            bracket_monitor_result = await self.trading_bot.monitor_all_bracket_positions()
            logger.info(f"Bracket monitor result: {bracket_monitor_result}")
            
            return {
                "success": True,
                "action": "open_short",
                "order_result": result,
                "trade_info": trade_info,
                "configuration": {
                    "position_size": position_size,
                    "close_entire_at_tp1": close_entire_at_tp1,
                    "take_profit_level_used": "TP2"
                },
                "monitor_result": monitor_result
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
                # Partial close: close 75% of the position (round up)
                contracts_to_close = int(position_size * 0.75)
                if contracts_to_close == 0:
                    contracts_to_close = 1  # At least close 1 contract
                logger.info(f"Trimming {contracts_to_close} contracts from {position_size} total long position (75% close)")
                
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
                # Partial close: close 75% of the position (round up)
                contracts_to_close = int(position_size * 0.75)
                if contracts_to_close == 0:
                    contracts_to_close = 1  # At least close 1 contract
                logger.info(f"Trimming {contracts_to_close} contracts from {position_size} total short position (75% close)")
                
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
        """Execute tp1 hit long: partial close or flatten all based on --close-entire-at-tp1 setting"""
        try:
            # Optional ignore of TP1 signals (OCO-managed exits)
            ignore_tp1 = os.getenv('IGNORE_TP1_SIGNALS', 'true').lower() in ('true','1','yes','on')
            if ignore_tp1:
                logger.info("Ignoring TP1 LONG signal (IGNORE_TP1_SIGNALS=true). OCO manages exits; logging only.")
                return {"success": True, "action": "tp1_hit_long", "ignored": True, "reason": "IGNORE_TP1_SIGNALS"}
            # Get configuration from webhook server
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
                    # Partial close: close 75% of the position (round up)
                    contracts_to_close = int(position_size * 0.75)
                    if contracts_to_close == 0:
                        contracts_to_close = 1  # At least close 1 contract
                    logger.info(f"TP1 hit: closing {contracts_to_close} contracts from {position_size} total long position (75% close)")
                    
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
        """Execute tp1 hit short: partial close or flatten all based on --close-entire-at-tp1 setting"""
        try:
            # Optional ignore of TP1 signals (OCO-managed exits)
            ignore_tp1 = os.getenv('IGNORE_TP1_SIGNALS', 'true').lower() in ('true','1','yes','on')
            if ignore_tp1:
                logger.info("Ignoring TP1 SHORT signal (IGNORE_TP1_SIGNALS=true). OCO manages exits; logging only.")
                return {"success": True, "action": "tp1_hit_short", "ignored": True, "reason": "IGNORE_TP1_SIGNALS"}
            # Get configuration from webhook server
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
                    # Partial close: close 75% of the position (round up)
                    contracts_to_close = int(position_size * 0.75)
                    if contracts_to_close == 0:
                        contracts_to_close = 1  # At least close 1 contract
                    logger.info(f"TP1 hit: closing {contracts_to_close} contracts from {position_size} total short position (75% close)")
                    
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
                 account_id: str = None, position_size: int = 1, close_entire_position_at_tp1: bool = False):
        self.trading_bot = trading_bot
        self.host = host
        self.port = port
        self.server = None
        self.server_thread = None
        
        # Trading configuration
        self.account_id = account_id
        self.position_size = position_size
        self.close_entire_position_at_tp1 = close_entire_position_at_tp1
        # Debounce control: prevent duplicate opens within a short window per symbol
        self._last_open_signal_ts = {}
        self.debounce_seconds = 90
        
        # Bracket monitoring control
        self._bracket_monitoring_enabled = True
        self._last_bracket_check = None
    
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
            
            # Start periodic bracket monitoring
            if self._bracket_monitoring_enabled:
                self._start_bracket_monitoring_task()
            
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
    
    def stop(self):
        """Stop the webhook server"""
        if self.server:
            logger.info("Stopping webhook server...")
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
    
    # Load environment variables
    import load_env
    
    # Read configuration from environment variables
    api_key = os.getenv('PROJECT_X_API_KEY')
    username = os.getenv('PROJECT_X_USERNAME')
    account_id = os.getenv('PROJECT_X_ACCOUNT_ID')
    position_size = int(os.getenv('POSITION_SIZE', '1'))
    close_entire_at_tp1 = os.getenv('CLOSE_ENTIRE_POSITION_AT_TP1', 'false').lower() in ('true', '1', 'yes', 'on')
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
    
    # Select account
    selected_account = None
    if account_id:
        # Find account by ID
        for account in accounts:
            if str(account['id']) == str(account_id):
                selected_account = account
                break
        if not selected_account:
            logger.error(f"Account ID {account_id} not found")
            return
    else:
        # Use the first account
        selected_account = accounts[0]
    
    bot.selected_account = selected_account
    logger.info(f"Using account: {bot.selected_account['name']} (ID: {bot.selected_account['id']})")
    logger.info(f"Position size: {position_size} contracts")
    logger.info(f"Close entire position at TP1: {close_entire_at_tp1}")
    
    # Start webhook server with configuration
    webhook_server = WebhookServer(
        bot, 
        host=host, 
        port=port,
        account_id=account_id,
        position_size=position_size,
        close_entire_position_at_tp1=close_entire_at_tp1
    )
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
