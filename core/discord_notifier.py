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


def strategy_from_custom_tag(tag: str) -> str:
    """Extract strategy slug from ``TB-{order_type}-{strategy}-...`` custom tags."""
    parts = str(tag or "").strip().split("-")
    if len(parts) >= 3 and parts[0] == "TB":
        return parts[2]
    return ""


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    v = str(raw).strip().strip('"').strip("'").lower()
    if v in ("0", "false", "no", "off"):
        return False
    return v in ("1", "true", "yes", "on")


def notify_signals_enabled() -> bool:
    """Strategy signal / placement hints (default on; set DISCORD_NOTIFY_SIGNALS=0 to mute)."""
    return _env_flag("DISCORD_NOTIFY_SIGNALS", True)


def notify_orders_enabled() -> bool:
    """Working-order placement acks (default off — noisy with bracket legs)."""
    return _env_flag("DISCORD_NOTIFY_ORDERS", False)


def notify_fills_enabled() -> bool:
    """Entry/exit fills and position closes (default on)."""
    return _env_flag("DISCORD_NOTIFY_FILLS", True)


def notify_feed_enabled() -> bool:
    """Market data feed down/recovered alerts (default on)."""
    return _env_flag("DISCORD_NOTIFY_FEED", True)


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
        if not self.enabled or not notify_orders_enabled():
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

    async def send_bracket_mode_notification(
        self,
        *,
        account_name: str,
        source: str,
        detail: str = "",
        symbol: str = "",
        strategy_name: str = "",
    ) -> bool:
        """Alert: Auto OCO Brackets not enabled — user must fix account settings."""
        if not self.enabled:
            return False
        try:
            fields = [
                {"name": "Account", "value": str(account_name)[:256] or "N/A", "inline": True},
                {"name": "Source", "value": str(source)[:128] or "N/A", "inline": True},
                {
                    "name": "Action required",
                    "value": (
                        "In ProjectX / TopStepX → enable **Auto OCO Brackets** "
                        "(turn off Position Brackets). Then restart the strategy."
                    ),
                    "inline": False,
                },
            ]
            if strategy_name:
                fields.append(
                    {"name": "Strategy", "value": str(strategy_name)[:128], "inline": True}
                )
            if symbol:
                fields.append({"name": "Symbol", "value": str(symbol)[:32], "inline": True})
            if detail:
                fields.append(
                    {"name": "Broker detail", "value": str(detail)[:900], "inline": False}
                )
            embed = {
                "title": "🚨 Auto OCO Brackets not enabled",
                "description": (
                    "This account rejected a native OCO bracket order "
                    "(Position Brackets mode). **No order was placed.** "
                    "Enable Auto OCO Brackets in ProjectX so SL/TP stay linked at the broker. "
                    "Hybrid software brackets are opt-in only (`TOPSTEPX_BRACKET_MODE=hybrid`)."
                ),
                "color": 15158332,  # Red
                "fields": fields,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            return await self._post({"embeds": [embed]})
        except Exception as e:
            logger.error("Failed to send Discord bracket-mode notification: %s", e)
            return False

    async def send_order_fill_notification(self, order_data: Dict, account_name: str) -> bool:
        """Send order fill notification to Discord"""
        if not self.enabled or not notify_fills_enabled():
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
            
            tag = order_data.get("custom_tag") or order_data.get("tag") or ""
            strategy = (
                order_data.get("strategy")
                or order_data.get("strategy_name")
                or strategy_from_custom_tag(tag)
            )
            title_strategy = strategy or "Trading Bot"

            # Create embed
            embed = {
                "title": f"🎯 {title_strategy} — Order Filled",
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
            
            if strategy or tag:
                embed["fields"].append(
                    {
                        "name": "Tag",
                        "value": str(tag or strategy)[:256],
                        "inline": False,
                    }
                )

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
        if not self.enabled or not notify_fills_enabled():
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
            exit_reason = position_data.get('exit_reason') or close_method
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
                "title": f"{'🟢' if pnl >= 0 else '🔴'} Position closed — ${pnl:.2f}",
                "color": 3066993 if pnl >= 0 else 15158332,
                "fields": [
                    {"name": "Account", "value": account_name, "inline": True},
                    {"name": "Symbol", "value": symbol, "inline": True},
                    {"name": "Side", "value": side, "inline": True},
                    {"name": "Qty", "value": str(quantity), "inline": True},
                    {"name": "Entry", "value": f"${entry_price:.2f}" if entry_price else "—", "inline": True},
                    {"name": "Exit", "value": f"${exit_price:.2f}" if exit_price else "—", "inline": True},
                    {"name": "P&L", "value": f"${pnl:.2f}", "inline": True},
                    {"name": "Exit reason", "value": str(exit_reason), "inline": True},
                    {"name": "Time (UTC)", "value": datetime.now(timezone.utc).strftime("%H:%M:%S"), "inline": True},
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
        """Send signal processing notification to Discord (opt-in via DISCORD_NOTIFY_SIGNALS)."""
        if not self.enabled or not notify_signals_enabled():
            return False
        
        if not self._rate_limit_check():
            return False

        details = details or {}
        # Skip generic analyze heartbeats — only actionable placement hints.
        st = str(signal_type or "").lower()
        if st in ("signal", "analyze", "heartbeat", "status"):
            return False
        
        try:
            emoji = "📊"
            if "long" in st or "buy" in st:
                emoji = "🚀"
            elif "short" in st or "sell" in st:
                emoji = "📉"
            elif "stop" in st:
                emoji = "🛑"

            reason = details.get("reason") or details.get("message") or ""
            strategy = details.get("strategy") or details.get("strategy_name") or details.get("name") or ""
            qty = details.get("quantity") or details.get("qty")

            embed = {
                "title": f"{emoji} {strategy or 'Signal'} — {symbol}",
                "description": (str(reason)[:500] if reason else st.replace("_", " ").title()),
                "color": 3447003,
                "fields": [
                    {"name": "Account", "value": account_name, "inline": True},
                    {"name": "Type", "value": st, "inline": True},
                ],
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            if qty is not None:
                embed["fields"].append({"name": "Qty", "value": str(qty), "inline": True})
            for key in ("entry_price", "stop_loss", "take_profit", "confidence"):
                if key in details and details[key] not in (None, ""):
                    embed["fields"].append({
                        "name": key.replace("_", " ").title(),
                        "value": str(details[key])[:128],
                        "inline": True,
                    })
            
            payload = {"embeds": [embed]}
            ok = await self._post(payload)
            if ok:
                logger.info(f"Discord signal notification sent for {signal_type} {symbol}")
            return ok
                
        except Exception as e:
            logger.error(f"Failed to send Discord signal notification: {e}")
            return False

    async def send_data_feed_alert(
        self,
        *,
        status: str,
        symbol: str,
        silence_s: float,
        threshold_s: float,
        account_name: str = "",
        reconnect_count: int = 0,
        cancel_on_staleness: bool = False,
        detail: str = "",
    ) -> bool:
        """Alert when market data paths go zombie (down) or recover (recovered)."""
        if not self.enabled or not notify_feed_enabled():
            return False
        if not self._rate_limit_check():
            return False
        try:
            is_down = status.strip().lower() == "down"
            title = (
                "🧟 Market data feed DOWN"
                if is_down
                else "✅ Market data feed recovered"
            )
            color = 15158332 if is_down else 3066993
            fields = [
                {"name": "Symbol", "value": symbol or "n/a", "inline": True},
                {
                    "name": "Silence",
                    "value": f"{silence_s:.0f}s (threshold {threshold_s:.0f}s)",
                    "inline": True,
                },
                {
                    "name": "Reconnect #",
                    "value": str(reconnect_count),
                    "inline": True,
                },
                {
                    "name": "Cancel on staleness",
                    "value": "yes" if cancel_on_staleness else "no",
                    "inline": True,
                },
            ]
            if account_name:
                fields.insert(0, {"name": "Account", "value": account_name, "inline": True})
            if detail:
                fields.append({"name": "Detail", "value": detail[:1024], "inline": False})
            embed = {
                "title": title,
                "color": color,
                "fields": fields,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            ok = await self._post({"embeds": [embed]})
            if ok:
                logger.info(
                    "Discord data-feed alert sent (%s %s %.0fs)",
                    status, symbol, silence_s,
                )
            return ok
        except Exception as e:
            logger.error("Failed to send Discord data-feed alert: %s", e)
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
