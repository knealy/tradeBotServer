"""
Discord Notifier for Trading Bot

This module handles sending trading notifications to Discord webhooks
when orders are executed or errors occur.
"""

import os
import logging
import time
import asyncio
from typing import Dict, Optional, List
import aiohttp
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

class DiscordNotifier:
    """Send trading notifications to Discord webhook"""
    
    def __init__(self, webhook_url: Optional[str] = None):
        self.webhook_url = webhook_url or os.getenv('DISCORD_WEBHOOK_URL')
        self.enabled = bool(self.webhook_url)
        self._last_notification_time = 0
        self._rate_limit_delay = 0.5  # 0.5 seconds between notifications
        self._session: Optional[aiohttp.ClientSession] = None
        self._session_lock = asyncio.Lock()
        
        if not self.enabled:
            logger.warning("Discord notifications disabled - DISCORD_WEBHOOK_URL not set")

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session and not self._session.closed:
            return self._session
        async with self._session_lock:
            if self._session and not self._session.closed:
                return self._session
            timeout = aiohttp.ClientTimeout(total=5, connect=3)
            connector = aiohttp.TCPConnector(limit=16, keepalive_timeout=30, enable_cleanup_closed=True)
            self._session = aiohttp.ClientSession(timeout=timeout, connector=connector)
            return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
    
    def _rate_limit_check(self) -> bool:
        """Check if we can send a notification (rate limiting)"""
        current_time = time.time()
        if current_time - self._last_notification_time < self._rate_limit_delay:
            logger.warning("Discord notification rate limited - skipping")
            return False
        self._last_notification_time = current_time
        return True
    
    async def _post(self, payload: Dict) -> bool:
        if not self.enabled:
            return False
        try:
            session = await self._get_session()
            async with session.post(self.webhook_url, json=payload) as resp:
                # Discord webhooks return 204 on success
                if resp.status == 204:
                    return True
                logger.warning(f"Discord webhook failed: HTTP {resp.status}")
                return False
        except Exception as e:
            logger.error(f"Failed to send Discord webhook: {e}")
            return False

    async def send_order_notification(self, order_data: Dict, account_name: str) -> bool:
        """Send order execution notification to Discord"""
        if not self.enabled:
            return False
        
        if not self._rate_limit_check():
            return False
        
        try:
            # Extract order details
            symbol = order_data.get('symbol', 'Unknown')
            side = order_data.get('side', 'Unknown')
            quantity = order_data.get('quantity', 0)
            price = order_data.get('price', 'Market')
            order_type = order_data.get('order_type', 'Market')
            order_id = order_data.get('order_id', 'Unknown')
            status = order_data.get('status', 'Placed')
            account_id = order_data.get('account_id', 'Unknown')
            stop_loss = order_data.get('stop_loss')
            take_profit = order_data.get('take_profit')
            
            # Create embed
            embed = {
                "title": f"🤖 Trading Bot Order {status}",
                "color": 3066993 if side == "BUY" else 15158332,  # Green for buy, red for sell
                "fields": [
                    {"name": "Account", "value": f"{account_name}\n(ID: {account_id})", "inline": True},
                    {"name": "Symbol", "value": symbol, "inline": True},
                    {"name": "Side", "value": side, "inline": True},
                    {"name": "Quantity", "value": str(quantity), "inline": True},
                    {"name": "Type", "value": order_type, "inline": True},
                    {"name": "Fill Price", "value": str(price), "inline": True},
                    {"name": "Order ID", "value": str(order_id), "inline": True},
                    {"name": "Status", "value": status, "inline": True},
                    {"name": "Timestamp", "value": datetime.now(timezone.utc).strftime("%H:%M:%S UTC"), "inline": True}
                ],
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            
            # Add bracket order details if available
            if stop_loss is not None or take_profit is not None:
                bracket_fields = []
                if stop_loss is not None:
                    bracket_fields.append({"name": "Stop Loss", "value": f"${stop_loss:.2f}", "inline": True})
                if take_profit is not None:
                    bracket_fields.append({"name": "Take Profit", "value": f"${take_profit:.2f}", "inline": True})
                embed["fields"].extend(bracket_fields)
            
            payload = {"embeds": [embed]}
            ok = await self._post(payload)
            if ok:
                logger.debug(f"Discord notification sent for {side} {quantity} {symbol}")
            return ok
                
        except Exception as e:
            logger.error(f"Failed to send Discord notification: {e}")
            return False
    
    async def send_status_digest(self, title: str, lines: List[str]) -> bool:
        """Short plaintext status (periodic heartbeat). Uses webhook ``content`` (max 2000 chars)."""
        if not self.enabled:
            return False
        try:
            body = "\n".join(lines)
            content = f"**{title}**\n{body}"
            if len(content) > 2000:
                content = content[:1997] + "..."
            return await self._post({"content": content})
        except Exception as e:
            logger.error("Failed to send Discord status digest: %s", e)
            return False

    async def send_error_notification(self, error_message: str, context: str = "") -> bool:
        """Send error notification to Discord"""
        if not self.enabled:
            return False
        
        try:
            embed = {
                "title": "⚠️ Trading Bot Error",
                "description": error_message,
                "color": 15158332,  # Red
                "fields": [
                    {"name": "Context", "value": context or "N/A", "inline": False}
                ],
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            
            payload = {"embeds": [embed]}
            return await self._post(payload)
            
        except Exception as e:
            logger.error(f"Failed to send Discord error notification: {e}")
            return False
    
    async def send_order_fill_notification(self, order_data: Dict, account_name: str) -> bool:
        """Send order fill notification to Discord"""
        if not self.enabled:
            return False
        
        if not self._rate_limit_check():
            return False
        
        try:
            # Extract order details
            symbol = order_data.get('symbol', 'Unknown')
            side = order_data.get('side', 'Unknown')
            quantity = order_data.get('quantity', 0)
            fill_price = order_data.get('fill_price', 'Unknown')
            order_type = order_data.get('order_type', 'Unknown')
            order_id = order_data.get('order_id', 'Unknown')
            position_id = order_data.get('position_id', 'Unknown')
            
            # Create embed
            embed = {
                "title": f"🎯 Trading Bot Order Filled",
                "color": 16776960,  # Yellow for fill
                "fields": [
                    {"name": "Account", "value": account_name, "inline": True},
                    {"name": "Symbol", "value": symbol, "inline": True},
                    {"name": "Side", "value": side, "inline": True},
                    {"name": "Quantity", "value": str(quantity), "inline": True},
                    {"name": "Type", "value": order_type, "inline": True},
                    {"name": "Fill Price", "value": str(fill_price), "inline": True},
                    {"name": "Order ID", "value": str(order_id), "inline": True},
                    {"name": "Position ID", "value": str(position_id), "inline": True},
                    {"name": "Timestamp", "value": datetime.now(timezone.utc).strftime("%H:%M:%S UTC"), "inline": True}
                ],
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            
            payload = {"embeds": [embed]}
            ok = await self._post(payload)
            if ok:
                logger.debug(f"Discord order fill notification sent for {side} {quantity} {symbol}")
            return ok
                
        except Exception as e:
            logger.error(f"Failed to send Discord order fill notification: {e}")
            return False
    
    async def send_position_close_notification(self, position_data: Dict, account_name: str) -> bool:
        """Send position close notification to Discord"""
        if not self.enabled:
            return False
        
        if not self._rate_limit_check():
            return False
        
        try:
            # Extract position details
            symbol = position_data.get('symbol', 'Unknown')
            side = position_data.get('side', 'Unknown')
            quantity = position_data.get('quantity', 0)
            entry_price = position_data.get('entry_price', 0)
            exit_price = position_data.get('exit_price', 0)
            close_method = position_data.get('close_method', 'Market Close')
            position_id = position_data.get('position_id', 'Unknown')
            
            # Calculate P&L if we have both prices
            if entry_price and exit_price and entry_price != 0:
                if side.upper() == 'LONG':
                    pnl = (exit_price - entry_price) * quantity
                else:  # SHORT
                    pnl = (entry_price - exit_price) * quantity
            else:
                pnl = 0
            
            # Create embed
            embed = {
                "title": f"🔴 Trading Bot Position Closed",
                "color": 15158332,  # Red for close
                "fields": [
                    {"name": "Account", "value": account_name, "inline": True},
                    {"name": "Symbol", "value": symbol, "inline": True},
                    {"name": "Side", "value": side, "inline": True},
                    {"name": "Quantity", "value": str(quantity), "inline": True},
                    {"name": "Entry Price", "value": f"${entry_price:.2f}" if entry_price else "Unknown", "inline": True},
                    {"name": "Exit Price", "value": f"${exit_price:.2f}" if exit_price else "Unknown", "inline": True},
                    {"name": "P&L", "value": f"${pnl:.2f}", "inline": True},
                    {"name": "Close Method", "value": str(close_method), "inline": True},
                    {"name": "Position ID", "value": str(position_id), "inline": True},
                    {"name": "Timestamp", "value": datetime.now(timezone.utc).strftime("%H:%M:%S UTC"), "inline": True}
                ],
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            
            payload = {"embeds": [embed]}
            ok = await self._post(payload)
            if ok:
                logger.debug(f"Discord position close notification sent for {side} {quantity} {symbol}")
            return ok
                
        except Exception as e:
            logger.error(f"Failed to send Discord position close notification: {e}")
            return False
    
    async def send_signal_notification(self, signal_type: str, symbol: str, account_name: str, details: Dict = None) -> bool:
        """Send signal processing notification to Discord"""
        if not self.enabled:
            return False
        
        try:
            # Map signal types to emojis
            signal_emojis = {
                "open_long": "🚀",
                "open_short": "📉", 
                "close_long": "🔴",
                "close_short": "🟢",
                "trim_long": "✂️",
                "trim_short": "✂️",
                "tp1_hit_long": "🎯",
                "tp1_hit_short": "🎯",
                "tp2_hit_long": "🎯",
                "tp2_hit_short": "🎯",
                "stop_out_long": "🛑",
                "stop_out_short": "🛑"
            }
            
            emoji = signal_emojis.get(signal_type, "📊")
            
            embed = {
                "title": f"{emoji} Trading Signal: {signal_type.replace('_', ' ').title()}",
                "color": 3447003,  # Blue
                "fields": [
                    {"name": "Account", "value": account_name, "inline": True},
                    {"name": "Symbol", "value": symbol, "inline": True},
                    {"name": "Signal", "value": signal_type, "inline": True}
                ],
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            
            # Add details if provided
            if details:
                for key, value in details.items():
                    if key not in ['account', 'symbol', 'signal']:
                        embed["fields"].append({
                            "name": key.replace('_', ' ').title(),
                            "value": str(value),
                            "inline": True
                        })
            
            payload = {"embeds": [embed]}
            ok = await self._post(payload)
            if ok:
                logger.info(f"Discord signal notification sent for {signal_type} {symbol}")
            return ok
                
        except Exception as e:
            logger.error(f"Failed to send Discord signal notification: {e}")
            return False

    async def send_inactivity_alert(
        self,
        account_name: str,
        strategy: str,
        zero_trade_sessions: int,
        extra: Optional[Dict] = None,
    ) -> bool:
        """Alert when a strategy has had N consecutive sessions with zero completed trades."""
        if not self.enabled:
            return False
        if not self._rate_limit_check():
            return False
        try:
            embed = {
                "title": "⚠️ Strategy inactivity (session streak)",
                "color": 15105570,
                "fields": [
                    {"name": "Account", "value": account_name, "inline": True},
                    {"name": "Strategy", "value": strategy, "inline": True},
                    {
                        "name": "Zero-trade sessions (streak)",
                        "value": str(zero_trade_sessions),
                        "inline": True,
                    },
                    {
                        "name": "Note",
                        "value": "No completed trades since this many session boundaries — check filters, regime, or data.",
                        "inline": False,
                    },
                ],
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            if extra:
                for k, v in extra.items():
                    embed["fields"].append(
                        {"name": str(k).replace("_", " ").title(), "value": str(v), "inline": True}
                    )
            ok = await self._post({"embeds": [embed]})
            if ok:
                logger.info("Discord inactivity alert sent for %s %s", strategy, account_name)
            return ok
        except Exception as e:
            logger.error("Failed to send Discord inactivity alert: %s", e)
            return False
