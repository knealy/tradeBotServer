"""
Dashboard backend for TopStepX Trading Bot
Provides API endpoints and WebSocket for real-time dashboard
"""

import json
import base64
import asyncio
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
import math
from typing import Dict, List, Optional, Any
from collections import defaultdict

# Add project root to Python path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from auth import require_auth, get_cors_headers

logger = logging.getLogger(__name__)

class DashboardAPI:
    """Dashboard API endpoints and WebSocket handling"""
    
    def __init__(self, trading_bot, webhook_server):
        self.trading_bot = trading_bot
        self.webhook_server = webhook_server
        self.websocket_clients = set()
        self._order_history_cache: Dict[str, Dict[str, Any]] = {}
        self._order_history_locks: Dict[str, asyncio.Lock] = {}
        self._positions_cache: Dict[str, Dict[str, Any]] = {}
        self._positions_locks: Dict[str, asyncio.Lock] = {}
        self._order_history_ttl = float(os.getenv("DASHBOARD_ORDER_HISTORY_TTL", "30"))
        self._positions_ttl = float(os.getenv("DASHBOARD_POSITIONS_TTL", "2"))
        
    # ------------------------------------------------------------------
    # Utility Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_iso_datetime(value: Optional[str], default: Optional[datetime] = None) -> datetime:
        """Parse ISO-8601 string into timezone-aware UTC datetime."""
        if value:
            try:
                dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(timezone.utc)
            except Exception:
                logger.debug(f"Failed to parse datetime '{value}', falling back to default")
        return default.astimezone(timezone.utc) if default else datetime.now(timezone.utc)

    @staticmethod
    def _format_iso(dt: datetime) -> str:
        """Return ISO formatted string in UTC."""
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')

    @staticmethod
    def _encode_cursor(timestamp: str, trade_id: str) -> str:
        payload = json.dumps({"t": timestamp, "id": trade_id})
        return base64.urlsafe_b64encode(payload.encode()).decode()

    @staticmethod
    def _decode_cursor(cursor: Optional[str]) -> Optional[Dict[str, Any]]:
        if not cursor:
            return None
        try:
            decoded = base64.urlsafe_b64decode(cursor.encode()).decode()
            data = json.loads(decoded)
            return data
        except Exception:
            logger.warning(f"Invalid cursor provided: {cursor}")
            return None

    @staticmethod
    def _extract_trade_timestamp(trade: Dict[str, Any]) -> Optional[datetime]:
        """Extract timestamp from TopStepX order data"""
        # TopStepX uses these timestamp fields
        for key in ['updateTimestamp', 'creationTimestamp', 'timestamp', 'fillTime', 'exit_time', 'entry_time', 'created', 'updateTime']:
            value = trade.get(key)
            if not value:
                continue
            try:
                # Handle both string and datetime objects
                if isinstance(value, datetime):
                    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
                return DashboardAPI._parse_iso_datetime(str(value))
            except Exception as e:
                logger.debug(f"Failed to parse timestamp from {key}={value}: {e}")
                continue
        
        # If no timestamp found, log the trade structure for debugging
        logger.warning(f"No timestamp found in trade: {list(trade.keys())}")
        return None

    @staticmethod
    def _extract_trade_pnl(trade: Dict[str, Any]) -> float:
        """
        Extract P&L from TopStepX order data.
        TopStepX doesn't provide direct PnL in order history - need to calculate from fills.
        
        IMPORTANT: For consolidated trades (with entry_price/exit_price), always recalculate
        P&L using the correct point value, rather than trusting pre-calculated 'pnl' field,
        which may have been calculated with incorrect point values.
        """
        # PRIORITY 1: Recalculate for consolidated trades (ensures correct point value)
        if 'entry_price' in trade and 'exit_price' in trade:
            try:
                entry = float(trade['entry_price'])
                exit_price = float(trade['exit_price'])
                quantity = float(trade.get('quantity', 0))
                side = str(trade.get('side', '')).upper()
                
                # Get point value for symbol
                symbol = DashboardAPI._extract_trade_symbol(trade)
                point_value = DashboardAPI._get_point_value(symbol)
                
                if side == 'BUY' or side == 'LONG' or side == '0':
                    pnl = (exit_price - entry) * quantity * point_value
                else:  # SELL/SHORT
                    pnl = (entry - exit_price) * quantity * point_value
                
                return float(pnl)
            except Exception as e:
                logger.debug(f"Failed to calculate PnL from entry/exit: {e}")
        
        # PRIORITY 2: Check for direct PnL fields (for non-consolidated trades)
        for key in ['pnl', 'PnL', 'realizedPnl', 'realized_pnl', 'profitLoss', 'result', 'netPnl']:
            if key in trade and trade[key] is not None:
                try:
                    return float(trade[key])
                except Exception:
                    continue
        
        # PRIORITY 3: TopStepX order format: calculate from fills
        # For filled orders, we need to look at the fill data
        # This is a simplified calculation - real P&L needs paired entry/exit
        fills = trade.get('fills', [])
        if fills:
            # If we have fill data with PnL
            total_pnl = sum(fill.get('pnl', 0) for fill in fills if isinstance(fill, dict))
            if total_pnl != 0:
                return float(total_pnl)
        
        # Parse from description as last resort
        description = trade.get('description') or trade.get('details') or trade.get('text')
        if description and isinstance(description, str):
            import re
            # Look for patterns like "P&L: $123.45" or "PnL: -$50.00"
            match = re.search(r"(?:p&l|pnl|profit|loss):\s*\$?([-+]?\d+\.?\d*)", description, re.IGNORECASE)
            if match:
                try:
                    return float(match.group(1))
                except Exception:
                    pass
        
        return 0.0
    
    @staticmethod
    def _get_point_value(symbol: str) -> float:
        """Get point value for a symbol ($ per point movement)"""
        symbol = symbol.upper()
        if 'MNQ' in symbol or 'NQ' in symbol:
            return 2.0  # Micro NQ: $2 per point
        elif 'MES' in symbol or 'ES' in symbol:
            return 5.0  # Micro ES: $5 per point
        elif 'MYM' in symbol or 'YM' in symbol:
            return 0.5  # Micro YM: $0.50 per point
        elif 'M2K' in symbol or 'RTY' in symbol:
            return 5.0  # Micro Russell: $5 per point
        elif 'MGC' in symbol:
            return 10.0  # Micro Gold: $10 per point
        elif 'GC' in symbol:
            return 100.0  # Gold: $100 per point
        else:
            return 1.0  # Default

    @staticmethod
    def _extract_trade_fees(trade: Dict[str, Any]) -> float:
        for key in ['fees', 'commission', 'fee', 'commissions']:
            if key in trade and trade[key] is not None:
                try:
                    return float(trade[key])
                except Exception:
                    continue
        return 0.0

    @staticmethod
    def _extract_trade_symbol(trade: Dict[str, Any]) -> str:
        """Extract symbol from trade/position data, handling contractId format."""
        # Try symbol field first
        symbol = trade.get('symbol')
        if symbol:
            return str(symbol).upper()
        
        # Try contractId (format: CON.F.US.MNQ.Z25 -> extract MNQ)
        contract_id = trade.get('contractId', '')
        if contract_id:
            if '.' in contract_id:
                # Extract symbol from contract ID (e.g., CON.F.US.MNQ.Z25 -> MNQ)
                parts = contract_id.split('.')
                if len(parts) >= 4:
                    return str(parts[-2]).upper()  # Second to last part is usually the symbol
            return str(contract_id).upper()
        
        # Try other fields
        for key in ['instrument', 'product']:
            value = trade.get(key)
            if value:
                return str(value).upper()
        
        return 'UNKNOWN'

    @staticmethod
    def _extract_trade_side(trade: Dict[str, Any]) -> str:
        """Extract side from TopStepX order data (0=BUY, 1=SELL)"""
        for key in ['side', 'orderSide', 'direction']:
            value = trade.get(key)
            if value is not None:
                # TopStepX uses numeric codes: 0=BUY, 1=SELL
                if isinstance(value, int):
                    return 'BUY' if value == 0 else 'SELL'
                # Handle string values
                value_str = str(value).upper()
                if value_str in ['BUY', 'SELL', 'LONG', 'SHORT', '0', '1']:
                    if value_str == '0' or value_str == 'BUY' or value_str == 'LONG':
                        return 'BUY'
                    elif value_str == '1' or value_str == 'SELL' or value_str == 'SHORT':
                        return 'SELL'
                    return value_str
        return 'UNKNOWN'

    @staticmethod
    def _extract_trade_quantity(trade: Dict[str, Any]) -> float:
        """Extract quantity from TopStepX order data"""
        # TopStepX uses: size (order size), fillVolume (filled amount)
        for key in ['fillVolume', 'filledQuantity', 'size', 'quantity', 'qty', 'orderQty']:
            value = trade.get(key)
            if value is not None:
                try:
                    return float(value)
                except Exception:
                    continue
        return 0.0

    @staticmethod
    def _extract_trade_price(trade: Dict[str, Any]) -> Optional[float]:
        """Extract price from TopStepX order data"""
        # TopStepX uses: limitPrice (limit orders), fillPrice (filled price), avgFillPrice
        for key in ['avgFillPrice', 'fillPrice', 'limitPrice', 'price', 'avgPrice', 'executionPrice']:
            value = trade.get(key)
            if value is not None:
                try:
                    return float(value)
                except Exception:
                    continue
        return None

    @staticmethod
    def _normalize_trade_status(trade: Dict[str, Any]) -> str:
        status = trade.get('status') or trade.get('orderStatus') or trade.get('state')
        if status is None:
            return 'unknown'
        if isinstance(status, int):
            status_map = {
                0: 'pending',
                1: 'pending',
                2: 'filled',
                3: 'filled',
                4: 'filled',
                5: 'cancelled',
                6: 'cancelled',
                7: 'rejected',
            }
            return status_map.get(status, 'unknown')
        status_str = str(status).lower()
        if 'fill' in status_str or status_str in ['complete', 'executed', 'closed']:
            return 'filled'
        if 'cancel' in status_str:
            return 'cancelled'
        if 'reject' in status_str or 'error' in status_str:
            return 'rejected'
        if 'working' in status_str or 'open' in status_str or 'pending' in status_str:
            return 'pending'
        return status_str

    @staticmethod
    def _bucket_timestamp(dt: datetime, interval: str) -> datetime:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        interval = interval.lower()
        if interval in ['trade', 'none']:
            return dt
        if interval in ['hour', '1h']:
            return dt.replace(minute=0, second=0, microsecond=0)
        if interval in ['week', '1w']:
            start_of_week = dt - timedelta(days=dt.weekday())
            return start_of_week.replace(hour=0, minute=0, second=0, microsecond=0)
        if interval in ['month', '1m']:
            return dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        # Default to day
        return dt.replace(hour=0, minute=0, second=0, microsecond=0)

    @staticmethod
    def _allowed_performance_intervals() -> List[str]:
        return ['trade', 'hour', 'day', 'week', 'month']

    @staticmethod
    def _fallback_interval(interval: str) -> str:
        interval = (interval or 'day').lower()
        if interval not in DashboardAPI._allowed_performance_intervals():
            return 'day'
        return interval

    def _get_lock(self, lock_dict: Dict[str, asyncio.Lock], key: str) -> asyncio.Lock:
        lock = lock_dict.get(key)
        if lock is None:
            lock = asyncio.Lock()
            lock_dict[key] = lock
        return lock

    async def _get_cached_order_history(
        self,
        account: str,
        start_dt: datetime,
        end_dt: datetime,
        limit: int,
    ) -> List[Dict[str, Any]]:
        """Return cached order history when possible to avoid hammering the API."""
        cache_key = str(account) if account is not None else 'default'
        lock = self._get_lock(self._order_history_locks, cache_key)
        now = time.monotonic()

        async with lock:
            # Check memory cache first
            entry = self._order_history_cache.get(cache_key)
            if (
                entry
                and entry['expires'] > now
                and entry['start'] <= start_dt
                and entry['end'] >= end_dt
                and entry['limit'] >= limit
            ):
                logger.info(f"💾 Memory cache HIT for account {account}")
                return entry['data']

            # Try database cache (24 hour TTL)
            if hasattr(self.trading_bot, 'db') and self.trading_bot.db:
                try:
                    db_cached = self.trading_bot.db.get_cached_order_history(
                        account_id=account,
                        start_time=start_dt,
                        end_time=end_dt,
                        limit=limit * 2,  # Fetch more for better cache coverage
                        max_age_hours=24
                    )
                    if db_cached:
                        # Store in memory cache too
                        self._order_history_cache[cache_key] = {
                            'data': db_cached,
                            'start': start_dt,
                            'end': end_dt,
                            'limit': len(db_cached),
                            'expires': now + self._order_history_ttl,
                        }
                        return db_cached
                except Exception as e:
                    logger.warning(f"DB cache lookup failed: {e}")

            # Expand the request range to reuse results for downstream calls
            request_start = min(start_dt, entry['start']) if entry else start_dt
            request_end = max(end_dt, entry['end']) if entry else end_dt
            fetch_limit = max(limit, entry['limit'] if entry else limit, 500)

            # Fetch from API
            history = await self.trading_bot.get_order_history(
                account_id=account,
                limit=fetch_limit,
                start_timestamp=self._format_iso(request_start),
                end_timestamp=self._format_iso(request_end),
            )

            # Cache in database for long-term storage
            if hasattr(self.trading_bot, 'db') and self.trading_bot.db and history:
                try:
                    self.trading_bot.db.cache_order_history(account, history)
                except Exception as e:
                    logger.warning(f"Failed to cache orders in DB: {e}")

            # Store in memory cache
            self._order_history_cache[cache_key] = {
                'data': history,
                'start': request_start,
                'end': request_end,
                'limit': fetch_limit,
                'expires': now + self._order_history_ttl,
            }
            return history

    async def _get_cached_positions(self, account: Optional[str]) -> List[Dict[str, Any]]:
        """Return cached open positions for a short TTL to smooth API latency."""
        cache_key = str(account) if account is not None else 'default'
        lock = self._get_lock(self._positions_locks, cache_key)
        now = time.monotonic()

        async with lock:
            entry = self._positions_cache.get(cache_key)
            if entry and entry['expires'] > now:
                # Cache still valid, but update current prices for real-time P&L
                positions = entry['data']
                await self._update_position_prices(positions)
                return positions

            positions = await self.trading_bot.get_open_positions(account_id=account)
            # Update current prices from live market data for real-time P&L
            await self._update_position_prices(positions)
            self._positions_cache[cache_key] = {
                'data': positions,
                'expires': now + self._positions_ttl,
            }
            return positions
    
    async def _update_position_prices(self, positions: List[Dict]) -> None:
        """Update current prices for positions using live market quotes for real-time P&L calculation"""
        for pos in positions:
            symbol = pos.get('symbol')
            if not symbol:
                # Try to extract from contractId
                contract_id = pos.get('contractId', '')
                if contract_id:
                    symbol = self._extract_trade_symbol({'contractId': contract_id})
                    pos['symbol'] = symbol
            
            if symbol:
                try:
                    # Get current market quote for real-time price
                    quote = await self.trading_bot.get_market_quote(symbol)
                    if quote and "error" not in quote:
                        # Use last price, or bid/ask midpoint
                        current_price = quote.get('last')
                        if not current_price:
                            bid = quote.get('bid')
                            ask = quote.get('ask')
                            if bid and ask:
                                current_price = (bid + ask) / 2
                        
                        if current_price:
                            pos['currentPrice'] = current_price
                            pos['current_price'] = current_price
                            pos['markPrice'] = current_price
                except Exception as e:
                    logger.debug(f"Failed to update price for {symbol}: {e}")

    async def get_accounts(self) -> List[Dict[str, Any]]:
        """Get all available accounts"""
        try:
            # Get accounts list (this already includes balances from the API)
            accounts = await self.trading_bot.list_accounts()
            formatted_accounts = []
            
            # list_accounts() already includes balances in the account dict
            # No need to make individual balance calls
            for account in accounts:
                formatted_accounts.append({
                    "id": account.get('id'),
                    "name": account.get('name'),
                    "status": account.get('status'),
                    "balance": account.get('balance', 0.0),  # Already included from list_accounts()
                    "currency": account.get('currency', 'USD'),
                    "account_type": account.get('account_type', 'unknown')
                })
            
            return formatted_accounts
        except Exception as e:
            logger.error(f"Error getting accounts: {e}")
            return []

    async def get_account_info(self) -> Dict[str, Any]:
        """Get current account information"""
        try:
            account = self.trading_bot.selected_account
            if not account:
                return {"error": "No account selected"}
            
            # Get current balance
            balance = await self.trading_bot.get_account_balance()
            
            return {
                "account_id": account.get('id'),
                "account_name": account.get('name'),
                "status": account.get('status'),
                "balance": balance,
                "currency": account.get('currency', 'USD'),
                "account_type": account.get('account_type', 'unknown')
            }
        except Exception as e:
            logger.error(f"Error getting account info: {e}")
            return {"error": str(e)}
    
    async def switch_account(self, account_id: str) -> Dict[str, Any]:
        """Switch to a different account"""
        try:
            # Find the account
            accounts = await self.trading_bot.list_accounts()
            target_account = None
            for account in accounts:
                if account.get('id') == account_id:
                    target_account = account
                    break
            
            if not target_account:
                return {"error": "Account not found"}
            
            # Switch to the account
            self.trading_bot.selected_account = target_account
            cache_key = str(account_id)
            self._order_history_cache.pop(cache_key, None)
            self._positions_cache.pop(cache_key, None)
            
            # Get updated account info
            account_info = await self.trading_bot.get_account_info()
            
            return {
                "success": True,
                "account": {
                    "id": account_id,
                    "accountId": target_account.get('name', account_id),
                    "name": target_account.get('name', account_id),
                    "balance": account_info.get('balance'),
                    "equity": account_info.get('equity'),
                    "dailyPnL": account_info.get('daily_pnl'),
                    "status": target_account.get('status', 'active'),
                    "currency": target_account.get('currency', 'USD'),
                    "account_type": target_account.get('account_type', 'unknown'),
                },
                "message": f"Switched to account: {target_account.get('name', account_id)}"
            }
        except Exception as e:
            logger.error(f"Error switching account: {e}")
            return {"error": str(e)}
    
    async def get_positions(self) -> List[Dict[str, Any]]:
        """Get open positions"""
        try:
            account = self.trading_bot.selected_account.get('id') if self.trading_bot.selected_account else None
            positions = await self._get_cached_positions(account)
            formatted_positions = []
            
            for pos in positions:
                # Log raw position data for debugging
                logger.debug(f"Raw position data: {pos}")
                
                # Extract position data - try multiple field name variations
                entry_price = (pos.get('entryPrice') or pos.get('entry_price') or 
                              pos.get('averagePrice') or pos.get('avgPrice') or
                              pos.get('openPrice') or pos.get('price') or 0)
                
                # Try to get current/mark price
                current_price = (pos.get('currentPrice') or pos.get('current_price') or 
                                pos.get('markPrice') or pos.get('mark_price') or
                                pos.get('lastPrice') or pos.get('last_price') or entry_price)
                
                # Quantity/size
                quantity = (pos.get('quantity') or pos.get('size') or 
                           pos.get('qty') or pos.get('contracts') or 0)
                
                # Side - handle both string and integer (1=LONG, 2=SHORT)
                side_raw = pos.get('side') or pos.get('positionSide') or pos.get('type') or pos.get('direction')
                if isinstance(side_raw, int):
                    side = 'LONG' if side_raw == 1 else 'SHORT'
                elif isinstance(side_raw, str):
                    side = side_raw.upper()
                else:
                    side = 'LONG'  # Default
                
                symbol = pos.get('symbol') or self._extract_trade_symbol(pos)
                
                # Calculate unrealized P&L if we have entry and current price
                unrealized_pnl = 0
                unrealized_pnl_pct = 0
                realized_pnl = pos.get('realizedPnl') or pos.get('realized_pnl', 0)
                
                if entry_price and current_price and quantity:
                    try:
                        # Get point value for symbol
                        point_value = self.trading_bot._get_point_value(symbol)
                        
                        # Calculate P&L based on side
                        if side.upper() in ['LONG', 'BUY', '0']:
                            # Long: profit when current > entry
                            price_diff = current_price - entry_price
                            unrealized_pnl = price_diff * quantity * point_value
                        else:
                            # Short: profit when current < entry
                            price_diff = entry_price - current_price
                            unrealized_pnl = price_diff * quantity * point_value
                        
                        # Calculate percentage
                        if entry_price > 0:
                            unrealized_pnl_pct = (unrealized_pnl / (entry_price * quantity * point_value)) * 100
                    except Exception as calc_err:
                        logger.debug(f"Error calculating P&L for {symbol}: {calc_err}")
                
                # Extract additional position info
                position_id = pos.get('id') or pos.get('positionId') or pos.get('position_id')
                stop_loss = pos.get('stopLoss') or pos.get('stop_loss') or pos.get('stopPrice')
                take_profit = pos.get('takeProfit') or pos.get('take_profit') or pos.get('targetPrice')
                opened_at = pos.get('timestamp') or pos.get('createdAt') or pos.get('openedAt') or pos.get('openTime')
                
                try:
                    tick_size = await self.trading_bot._get_tick_size(symbol)
                except Exception as tick_err:
                    logger.debug(f"Tick size lookup failed for {symbol}: {tick_err}")
                    tick_size = None
                try:
                    point_value_hint = self.trading_bot._get_point_value(symbol)
                except Exception as pv_err:
                    logger.debug(f"Point value lookup failed for {symbol}: {pv_err}")
                    point_value_hint = None
                
                formatted_positions.append({
                    "id": str(position_id) if position_id else None,
                    "symbol": symbol,
                    "side": side,
                    "quantity": float(quantity),
                    "entry_price": float(entry_price) if entry_price else 0,
                    "current_price": float(current_price) if current_price else float(entry_price) if entry_price else 0,
                    "unrealized_pnl": round(unrealized_pnl, 2),
                    "unrealized_pnl_pct": round(unrealized_pnl_pct, 2),
                    "realized_pnl": round(float(realized_pnl), 2),
                    "stop_loss": float(stop_loss) if stop_loss else None,
                    "take_profit": float(take_profit) if take_profit else None,
                    "timestamp": opened_at,
                    "tick_size": tick_size,
                    "point_value": point_value_hint,
                    "min_quantity": pos.get('minQuantity') or pos.get('minQty') or 1,
                    "account_id": pos.get('accountId') or pos.get('account_id'),
                    "brackets": pos.get('bracketOrders') or pos.get('ocoOrders') or pos.get('linkedOrders'),
                    # Additional fields for debugging
                    "_raw": pos  # Include raw data for debugging
                })
            
            return formatted_positions
        except Exception as e:
            logger.error(f"Error getting positions: {e}")
            return []
    
    async def get_orders(self) -> List[Dict[str, Any]]:
        """Get open orders"""
        try:
            orders = await self.trading_bot.get_open_orders()
            formatted_orders = []
            
            for order in orders:
                raw_type = order.get('type')
                type_map = {
                    1: 'LIMIT',
                    2: 'MARKET',
                    3: 'MARKET',
                    4: 'STOP',
                    5: 'STOP_LIMIT',
                    6: 'TRAILING_STOP',
                }
                status = order.get('status')
                status_map = {
                    0: 'PENDING',
                    1: 'PENDING',
                    2: 'FILLED',
                    3: 'PARTIALLY_FILLED',
                    4: 'CANCELLED',
                    5: 'REJECTED',
                    6: 'EXPIRED',
                }
                
                symbol = (
                    order.get('symbol') or
                    order.get('contractSymbol') or
                    order.get('instrumentSymbol')
                )
                if not symbol and order.get('contractId'):
                    try:
                        symbol = self.trading_bot._get_symbol_from_contract_id(order.get('contractId'))
                    except Exception:
                        symbol = str(order.get('contractId'))
                
                formatted_price = (
                    order.get('limitPrice') or
                    order.get('price') or
                    order.get('averagePrice') or
                    order.get('avgPrice')
                )
                stop_price = order.get('stopPrice') or order.get('triggerPrice')
                
                bracket = order.get('bracket') or {}
                stop_loss = bracket.get('stopLossPrice') or order.get('stopLossPrice')
                take_profit = bracket.get('takeProfitPrice') or order.get('takeProfitPrice')
                
                tif = order.get('timeInForce') or order.get('time_in_force') or 'DAY'
                
                formatted_orders.append({
                    "id": order.get('id'),
                    "symbol": symbol,
                    "side": (order.get('side') or '').upper() if isinstance(order.get('side'), str) else 'BUY' if order.get('side') in (0, '0') else 'SELL',
                    "type": type_map.get(raw_type, str(raw_type).upper() if raw_type is not None else 'UNKNOWN'),
                    "raw_type": raw_type,
                    "quantity": order.get('size') or order.get('quantity'),
                    "price": formatted_price,
                    "stop_price": stop_price,
                    "status": status_map.get(status, str(status).upper() if status is not None else 'PENDING'),
                    "raw_status": status,
                    "time_in_force": tif,
                    "reduce_only": order.get('reduceOnly'),
                    "stop_loss": stop_loss,
                    "take_profit": take_profit,
                    "custom_tag": order.get('customTag'),
                    "timestamp": order.get('createdAt') or order.get('timestamp'),
                    "_raw": order,
                })
            
            return formatted_orders
        except Exception as e:
            logger.error(f"Error getting orders: {e}")
            return []
    
    async def get_trade_history_paginated(
        self,
        account_id: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        symbol: Optional[str] = None,
        trade_type: str = 'filled',
        limit: int = 50,
        cursor: Optional[str] = None,
        consolidate: bool = True,  # Enable trade consolidation by default
    ) -> Dict[str, Any]:
        """Return paginated trade history with filters and summary.
        
        If consolidate=True, uses trading_bot's logic to pair entry/exit orders
        and calculate accurate P&L for complete trades."""
        try:
            account = account_id or (self.trading_bot.selected_account.get('id') if self.trading_bot.selected_account else None)
            if not account:
                return {"error": "No account selected"}

            limit = max(1, min(limit, 500))
            now = datetime.now(timezone.utc)
            end_dt = self._parse_iso_datetime(end_date, now)

            cursor_payload = self._decode_cursor(cursor)
            if cursor_payload and cursor_payload.get('t'):
                end_dt = self._parse_iso_datetime(cursor_payload['t'], end_dt) - timedelta(microseconds=1)

            # Default lookback window scales with requested limit (more rows => wider window)
            window_multiplier = max(1, math.ceil(limit / 20))
            default_window_days = min(90, 7 * window_multiplier)
            default_start = end_dt - timedelta(days=default_window_days)
            start_dt = self._parse_iso_datetime(start_date, default_start)
            if start_dt >= end_dt:
                start_dt = end_dt - timedelta(days=1)

            history = await self._get_cached_order_history(
                account=account,
                start_dt=start_dt,
                end_dt=end_dt,
                limit=limit * 3,
            )

            # If consolidate=True, use trading_bot's consolidation logic to pair orders and calculate P&L
            if consolidate and hasattr(self.trading_bot, '_consolidate_orders_into_trades'):
                try:
                    consolidated_trades = self.trading_bot._consolidate_orders_into_trades(history)
                    logger.info(f"✅ Consolidated {len(history)} orders into {len(consolidated_trades)} complete trades")
                    
                    # Convert consolidated trades to normalized format
                    trades_normalized: List[Dict[str, Any]] = []
                    for trade in consolidated_trades:
                        trade_ts = self._parse_iso_datetime(trade.get('exit_time'))
                        if not trade_ts or trade_ts < start_dt or trade_ts > end_dt:
                            continue
                        
                        if symbol and trade.get('symbol', '').upper() != symbol.upper():
                            continue
                        
                        # Create unique ID from entry+exit order IDs to avoid duplicates
                        # (one exit order can close multiple partial positions)
                        unique_id = f"{trade.get('entry_order_id')}-{trade.get('exit_order_id')}"
                        
                        normalized = {
                            "id": unique_id,
                            "order_id": unique_id,
                            "symbol": trade.get('symbol', 'UNKNOWN').upper(),
                            "side": trade.get('side', 'UNKNOWN').upper(),
                            "quantity": float(trade.get('quantity', 0)),
                            "price": float(trade.get('entry_price', 0)),
                            "exit_price": float(trade.get('exit_price', 0)),
                            "pnl": float(trade.get('pnl', 0)),
                            "fees": 0.0,  # TopStepX doesn't provide fees in order history
                            "net_pnl": float(trade.get('pnl', 0)),
                            "status": "filled",
                            "strategy": trade.get('strategy'),
                            "timestamp": self._format_iso(trade_ts),
                            "entry_time": self._format_iso(self._parse_iso_datetime(trade.get('entry_time'))),
                        }
                        trades_normalized.append(normalized)
                    
                    logger.info(f"📊 Normalized {len(trades_normalized)} consolidated trades")
                except Exception as e:
                    logger.error(f"❌ Trade consolidation failed: {e}, falling back to raw orders")
                    consolidate = False  # Fall back to raw order processing
            
            # If not consolidating or consolidation failed, process raw orders
            if not consolidate:
                trades_normalized: List[Dict[str, Any]] = []
                for idx, trade in enumerate(history):
                    trade_ts = self._extract_trade_timestamp(trade)
                    if not trade_ts:
                        if idx < 3:  # Log first few for debugging
                            logger.warning(f"Skipping trade {idx} - no timestamp. Keys: {list(trade.keys())}")
                        continue
                    if trade_ts < start_dt or trade_ts > end_dt:
                        continue

                    normalized_status = self._normalize_trade_status(trade)
                    if trade_type != 'all' and normalized_status != trade_type.lower():
                        continue

                    trade_symbol = self._extract_trade_symbol(trade)
                    if symbol and trade_symbol.upper() != symbol.upper():
                        continue

                    pnl = self._extract_trade_pnl(trade)
                    fees = self._extract_trade_fees(trade)
                    quantity = self._extract_trade_quantity(trade)
                    price = self._extract_trade_price(trade)

                    normalized = {
                        "id": str(trade.get('id') or trade.get('orderId') or trade.get('fillId') or int(trade_ts.timestamp())),
                        "order_id": str(trade.get('orderId') or trade.get('id') or ''),
                        "symbol": trade_symbol.upper(),
                        "side": self._extract_trade_side(trade),
                        "quantity": quantity,
                        "price": price,
                        "pnl": pnl,
                        "fees": fees,
                        "net_pnl": pnl - fees,
                        "status": normalized_status,
                        "strategy": trade.get('strategy') or trade.get('strategyName'),
                        "timestamp": self._format_iso(trade_ts),
                    }
                    trades_normalized.append(normalized)
                
                logger.info(f"📊 Normalized {len(trades_normalized)} trades from {len(history)} raw orders")

            # Sort trades newest first
            trades_normalized.sort(key=lambda x: x['timestamp'], reverse=True)

            items = trades_normalized[:limit]
            next_cursor = None
            if len(trades_normalized) > limit:
                last_item = trades_normalized[limit]
                next_cursor = self._encode_cursor(last_item['timestamp'], last_item['id'])

            # Calculate summary ONLY for displayed items, not all trades
            totals = defaultdict(int)
            gross_pnl = 0.0
            net_pnl = 0.0
            total_fees = 0.0
            for trade in items:  # ✅ FIX: Only sum the displayed trades
                totals['total'] += 1
                totals[trade['status']] += 1
                gross_pnl += trade['pnl']
                net_pnl += trade['net_pnl']
                total_fees += trade['fees']

            summary = {
                "total": totals['total'],
                "filled": totals['filled'],
                "cancelled": totals['cancelled'],
                "pending": totals['pending'],
                "rejected": totals['rejected'],
                "gross_pnl": round(gross_pnl, 2),
                "net_pnl": round(net_pnl, 2),
                "fees": round(total_fees, 2),
                "displayed_count": len(items),
                "total_in_period": len(trades_normalized),
            }

            return {
                "account_id": str(account),
                "start": self._format_iso(start_dt),
                "end": self._format_iso(end_dt),
                "items": items,
                "next_cursor": next_cursor,
                "summary": summary,
            }
        except Exception as e:
            logger.error(f"Error building trade history: {e}")
            return {"error": str(e)}

    async def get_trade_history(self, start_date: str = None, end_date: str = None) -> List[Dict[str, Any]]:
        """Legacy helper returning just the trade list."""
        result = await self.get_trade_history_paginated(
            start_date=start_date,
            end_date=end_date,
            trade_type='filled',
            limit=200,
        )
        if isinstance(result, dict) and 'items' in result:
            return result['items']
        if isinstance(result, dict) and 'error' in result:
            logger.error(f"Trade history error: {result['error']}")
        return []
    
    async def get_performance_stats(self) -> Dict[str, Any]:
        """Get performance statistics"""
        try:
            # Get recent trades for stats
            history = await self.get_trade_history()
            
            total_trades = len(history)
            winning_trades = len([t for t in history if t.get('pnl', 0) > 0])
            losing_trades = len([t for t in history if t.get('pnl', 0) < 0])
            
            total_pnl = sum(t.get('pnl', 0) for t in history)
            win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0
            
            return {
                "total_trades": total_trades,
                "winning_trades": winning_trades,
                "losing_trades": losing_trades,
                "win_rate": round(win_rate, 2),
                "total_pnl": total_pnl,
                "avg_win": sum(t.get('pnl', 0) for t in history if t.get('pnl', 0) > 0) / max(winning_trades, 1),
                "avg_loss": sum(t.get('pnl', 0) for t in history if t.get('pnl', 0) < 0) / max(losing_trades, 1)
            }
        except Exception as e:
            logger.error(f"Error getting performance stats: {e}")
            return {"error": str(e)}
    
    async def get_system_logs(self, level: str = None, limit: int = 100) -> List[Dict[str, Any]]:
        """Get recent system logs"""
        try:
            # This is a simplified implementation
            # In production, you'd want to read from actual log files
            logs = []
            
            # Add some sample log entries for now
            logs.append({
                "timestamp": datetime.now().isoformat(),
                "level": "INFO",
                "message": "Dashboard API initialized",
                "source": "dashboard"
            })
            
            return logs[-limit:] if limit else logs
        except Exception as e:
            logger.error(f"Error getting system logs: {e}")
            return []
    
    async def place_order(
        self,
        symbol: str,
        side: str,
        quantity: int,
        order_type: str = "market",
        limit_price: Optional[float] = None,
        stop_price: Optional[float] = None,
        stop_loss_ticks: Optional[int] = None,
        take_profit_ticks: Optional[int] = None,
        stop_loss_price: Optional[float] = None,
        take_profit_price: Optional[float] = None,
        account_id: Optional[str] = None,
        enable_bracket: bool = False,
        enable_breakeven: bool = False,
        time_in_force: Optional[str] = None,
        reduce_only: bool = False,
    ) -> Dict[str, Any]:
        """Place a new order using the trading bot helper methods."""
        try:
            if not symbol or not side:
                return {"error": "symbol and side are required"}
            
            symbol = symbol.upper().strip()
            side = side.upper().strip()
            
            try:
                quantity = int(quantity)
            except (TypeError, ValueError):
                return {"error": "quantity must be an integer"}
            
            if quantity <= 0:
                return {"error": "quantity must be greater than 0"}
            
            normalized_type = (order_type or "market").lower()
            
            # Route to appropriate trading bot helper
            if normalized_type in ("market", "limit", "bracket"):
                result = await self.trading_bot.place_market_order(
                    symbol=symbol,
                    side=side,
                    quantity=quantity,
                    account_id=account_id,
                    stop_loss_ticks=stop_loss_ticks if enable_bracket else stop_loss_ticks,
                    take_profit_ticks=take_profit_ticks if enable_bracket else take_profit_ticks,
                    order_type=normalized_type,
                    limit_price=limit_price,
                )
            elif normalized_type == "stop":
                if stop_price is None:
                    return {"error": "stop_price required for stop orders"}
                
                # If we have explicit stop-loss/take-profit prices, treat as OCO bracket entry
                if enable_bracket and stop_loss_price and take_profit_price:
                    result = await self.trading_bot.place_oco_bracket_with_stop_entry(
                        symbol=symbol,
                        side=side,
                        quantity=quantity,
                        entry_price=float(stop_price),
                        stop_loss_price=float(stop_loss_price),
                        take_profit_price=float(take_profit_price),
                        account_id=account_id,
                        enable_breakeven=enable_breakeven,
                    )
                else:
                    result = await self.trading_bot.place_stop_order(
                        symbol=symbol,
                        side=side,
                        quantity=quantity,
                        stop_price=float(stop_price),
                        account_id=account_id,
                    )
            else:
                return {"error": f"Unsupported order_type '{order_type}'"}
            
            if isinstance(result, dict):
                # Enrich response with metadata we know from request context
                result.setdefault("requested", {})
                result["requested"].update({
                    "symbol": symbol,
                    "side": side,
                    "quantity": quantity,
                    "order_type": normalized_type,
                    "limit_price": limit_price,
                    "stop_price": stop_price,
                    "stop_loss_ticks": stop_loss_ticks,
                    "take_profit_ticks": take_profit_ticks,
                    "stop_loss_price": stop_loss_price,
                    "take_profit_price": take_profit_price,
                    "enable_bracket": enable_bracket,
                    "enable_breakeven": enable_breakeven,
                    "time_in_force": time_in_force,
                    "reduce_only": reduce_only,
                    "account_id": account_id,
                })
            
            return result
        except Exception as e:
            logger.error(f"Error placing order: {e}")
            logger.exception(e)
            return {"error": str(e)}
    
    async def cancel_order(self, order_id: str) -> Dict[str, Any]:
        """Cancel an order"""
        try:
            result = await self.trading_bot.cancel_order(order_id)
            return result
        except Exception as e:
            logger.error(f"Error canceling order: {e}")
            return {"error": str(e)}
    
    async def close_position(self, position_id: str, quantity: Optional[int] = None) -> Dict[str, Any]:
        """Close a position"""
        try:
            result = await self.trading_bot.close_position(position_id, quantity=quantity)
            return result
        except Exception as e:
            logger.error(f"Error closing position: {e}")
            return {"error": str(e)}
    
    async def flatten_all_positions(self) -> Dict[str, Any]:
        """Emergency flatten all positions"""
        try:
            result = await self.trading_bot.flatten_all_positions(interactive=False)
            return result
        except Exception as e:
            logger.error(f"Error flattening positions: {e}")
            return {"error": str(e)}
    
    async def cancel_all_orders(self, account_id: Optional[str] = None) -> Dict[str, Any]:
        """Cancel all open orders"""
        try:
            orders = await self.trading_bot.get_open_orders(account_id=account_id)
            canceled_count = 0
            
            for order in orders:
                try:
                    await self.trading_bot.cancel_order(order.get('id'), account_id=account_id)
                    canceled_count += 1
                except Exception as e:
                    logger.warning(f"Failed to cancel order {order.get('id')}: {e}")
            
            return {
                "canceled": canceled_count,
                "total": len(orders),
                "account_id": account_id or (self.trading_bot.selected_account.get('id') if self.trading_bot.selected_account else None)
            }
        except Exception as e:
            logger.error(f"Error canceling all orders: {e}")
            return {"error": str(e)}
    
    async def broadcast_update(self, update_type: str, data: Any):
        """Broadcast update to all connected WebSocket clients"""
        if not self.websocket_clients:
            return
        
        message = {
            "type": update_type,
            "data": data,
            "timestamp": datetime.now().isoformat()
        }
        
        message_json = json.dumps(message)
        disconnected_clients = set()
        
        for client in self.websocket_clients:
            try:
                await client.send(message_json)
            except Exception as e:
                logger.warning(f"Failed to send update to client: {e}")
                disconnected_clients.add(client)
        
        # Remove disconnected clients
        self.websocket_clients -= disconnected_clients

    async def get_performance_history(
        self,
        account_id: Optional[str] = None,
        interval: str = 'day',
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        try:
            account = account_id or (self.trading_bot.selected_account.get('id') if self.trading_bot.selected_account else None)
            if not account:
                return {"error": "No account selected"}

            interval = self._fallback_interval(interval)
            now = datetime.now(timezone.utc)
            end_dt = self._parse_iso_datetime(end_date, now)
            default_start = end_dt - timedelta(days=90)
            start_dt = self._parse_iso_datetime(start_date, default_start)
            if start_dt >= end_dt:
                start_dt = end_dt - timedelta(days=1)

            raw_orders = await self._get_cached_order_history(
                account=account,
                start_dt=start_dt,
                end_dt=end_dt,
                limit=1000,
            )

            # Use trade consolidation for accurate P&L calculation
            if hasattr(self.trading_bot, '_consolidate_orders_into_trades'):
                try:
                    trades = self.trading_bot._consolidate_orders_into_trades(raw_orders)
                    logger.info(f"✅ Consolidated {len(raw_orders)} orders into {len(trades)} trades for performance history")
                    # Log first 5 trades for debugging with full details
                    for i, trade in enumerate(trades[:5]):
                        logger.info(f"  Trade {i+1}: symbol={trade.get('symbol')}, side={trade.get('side')}, qty={trade.get('quantity')}, "
                                  f"entry=${trade.get('entry_price')}, exit=${trade.get('exit_price')}, pnl=${trade.get('pnl')}, exit_time={trade.get('exit_time')}")
                except Exception as e:
                    logger.error(f"❌ Trade consolidation failed in performance history: {e}, using raw orders")
                    logger.exception(e)  # Show full stack trace
                    trades = raw_orders
            else:
                logger.warning("⚠️ _consolidate_orders_into_trades not available, using raw orders")
                trades = raw_orders

            buckets: Dict[str, Dict[str, Any]] = {}
            total_pnl = 0.0
            total_wins = 0
            total_losses = 0
            win_pnl_total = 0.0
            loss_pnl_total = 0.0
            trade_count = 0

            # Track trades for specific days for debugging
            nov3_trades = []
            nov5_trades = []
            
            for trade in trades:
                # Handle both consolidated trades (with 'exit_time') and raw orders
                if 'exit_time' in trade:
                    trade_ts = self._parse_iso_datetime(trade.get('exit_time'))
                else:
                    trade_ts = self._extract_trade_timestamp(trade)
                
                if not trade_ts:
                    continue
                if trade_ts < start_dt or trade_ts > end_dt:
                    continue

                # Handle both consolidated trades (with 'pnl') and raw orders
                if 'pnl' in trade and isinstance(trade.get('pnl'), (int, float)):
                    pnl = float(trade['pnl'])
                else:
                    pnl = self._extract_trade_pnl(trade)
                
                # Debug logging for trades
                if trade_count < 10:
                    logger.info(f"  Trade {trade_count+1}: timestamp={trade_ts}, pnl=${pnl:.2f}, has_exit_time={'exit_time' in trade}, has_pnl_field={'pnl' in trade}")
                
                bucket_dt = self._bucket_timestamp(trade_ts, interval)
                bucket_key = self._format_iso(bucket_dt)
                
                # Track trades for Nov 3 and Nov 5 specifically
                if bucket_key.startswith('2025-11-03'):
                    nov3_trades.append({
                        'timestamp': str(trade_ts),
                        'pnl': pnl,
                        'symbol': trade.get('symbol', '?'),
                        'side': trade.get('side', '?'),
                        'qty': trade.get('quantity', 0),
                        'entry': trade.get('entry_price', 0),
                        'exit': trade.get('exit_price', 0),
                    })
                elif bucket_key.startswith('2025-11-05'):
                    nov5_trades.append({
                        'timestamp': str(trade_ts),
                        'pnl': pnl,
                        'symbol': trade.get('symbol', '?'),
                        'side': trade.get('side', '?'),
                        'qty': trade.get('quantity', 0),
                        'entry': trade.get('entry_price', 0),
                        'exit': trade.get('exit_price', 0),
                    })

                bucket = buckets.setdefault(
                    bucket_key,
                    {
                        "timestamp": bucket_key,
                        "period_pnl": 0.0,
                        "cumulative_pnl": 0.0,
                        "trade_count": 0,
                        "winning_trades": 0,
                        "losing_trades": 0,
                        "max_drawdown": 0.0,
                    },
                )

                bucket['period_pnl'] += pnl
                bucket['trade_count'] += 1
                if pnl > 0:
                    bucket['winning_trades'] += 1
                    total_wins += 1
                    win_pnl_total += pnl
                elif pnl < 0:
                    bucket['losing_trades'] += 1
                    total_losses += 1
                    loss_pnl_total += pnl
                total_pnl += pnl
                trade_count += 1

            # Sort buckets chronologically and compute cumulative PnL/drawdown
            points = []
            cumulative = 0.0
            peak = 0.0
            max_drawdown = 0.0
            logger.info(f"📊 Processing {len(buckets)} time buckets for performance chart")
            for key in sorted(buckets.keys()):
                bucket = buckets[key]
                cumulative += bucket['period_pnl']
                bucket['cumulative_pnl'] = round(cumulative, 2)
                if cumulative > peak:
                    peak = cumulative
                drawdown = peak - cumulative
                if drawdown > max_drawdown:
                    max_drawdown = drawdown
                bucket['max_drawdown'] = round(drawdown, 2)
                bucket['period_pnl'] = round(bucket['period_pnl'], 2)
                
                # Log each day's data for debugging
                logger.info(f"  {key[:10]}: period_pnl=${bucket['period_pnl']:.2f}, cumulative=${bucket['cumulative_pnl']:.2f}, trades={bucket['trade_count']}")
                
                points.append(bucket)

            balance_source = "api"
            current_balance = await self.trading_bot.get_account_balance(account)
            tracker_state = None
            tracker = getattr(self.trading_bot, 'account_tracker', None)
            if tracker:
                tracker_state = tracker.accounts.get(str(account))
            if not current_balance and tracker_state:
                current_balance = tracker_state.current_balance or tracker_state.starting_balance
                balance_source = "account_tracker"
            if not current_balance:
                account_info = await self.trading_bot.get_account_info(account)
                if account_info:
                    balance_value = account_info.get('balance') or account_info.get('equity') or account_info.get('cash')
                    if balance_value:
                        current_balance = float(balance_value)
                        balance_source = "account_info"
            if not current_balance and tracker_state:
                current_balance = tracker_state.starting_balance
                balance_source = "tracker_start"
            if not current_balance and points:
                current_balance = total_pnl
                balance_source = "pnl_fallback"
            current_balance = float(current_balance or 0.0)
            start_balance = tracker_state.starting_balance if tracker_state and tracker_state.starting_balance else current_balance - total_pnl
            if not math.isfinite(start_balance):
                start_balance = current_balance - total_pnl
            
            # Log summary for debugging
            logger.info(f"📊 Performance Summary: total_trades={trade_count}, total_pnl={round(total_pnl, 2)}, wins={total_wins}, losses={total_losses}, current_balance={current_balance}")
            
            # Log detailed breakdown for Nov 3 and Nov 5
            if nov3_trades:
                nov3_total = sum(t['pnl'] for t in nov3_trades)
                logger.info(f"🔍 NOV 3 DEBUG: {len(nov3_trades)} trades, total_pnl=${nov3_total:.2f}")
                logger.info(f"   Expected: +$489.12 with 18 trades")
                for i, t in enumerate(nov3_trades[:5]):  # First 5 trades
                    logger.info(f"   Trade {i+1}: {t['symbol']} {t['side']} x{t['qty']} @ ${t['entry']:.2f}→${t['exit']:.2f}, pnl=${t['pnl']:.2f}, ts={t['timestamp']}")
            
            if nov5_trades:
                nov5_total = sum(t['pnl'] for t in nov5_trades)
                logger.info(f"🔍 NOV 5 DEBUG: {len(nov5_trades)} trades, total_pnl=${nov5_total:.2f}")
                logger.info(f"   Expected: +$1,821.32 with 45 trades")
                for i, t in enumerate(nov5_trades[:5]):  # First 5 trades
                    logger.info(f"   Trade {i+1}: {t['symbol']} {t['side']} x{t['qty']} @ ${t['entry']:.2f}→${t['exit']:.2f}, pnl=${t['pnl']:.2f}, ts={t['timestamp']}")

            summary = {
                "start_balance": round(start_balance, 2),
                "end_balance": round(current_balance, 2),
                "total_pnl": round(total_pnl, 2),
                "win_rate": round((total_wins / trade_count * 100) if trade_count else 0.0, 2),
                "avg_win": round(win_pnl_total / total_wins, 2) if total_wins else 0.0,
                "avg_loss": round(loss_pnl_total / total_losses, 2) if total_losses else 0.0,
                "max_drawdown": round(max_drawdown, 2),
                "trade_count": trade_count,
                "winning_trades": total_wins,
                "losing_trades": total_losses,
                "balance_source": balance_source,
            }

            return {
                "account_id": str(account),
                "interval": interval,
                "start": self._format_iso(start_dt),
                "end": self._format_iso(end_dt),
                "points": points,
                "summary": summary,
            }
        except Exception as e:
            logger.error(f"Error building performance history: {e}")
            return {"error": str(e)}

    async def get_historical_data(
        self,
        symbol: str,
        timeframe: str = '5m',
        limit: int = 300,
        end_time: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not symbol:
            return {"error": "Symbol is required"}
        try:
            limit = max(1, min(limit, 1500))
            end_dt = self._parse_iso_datetime(end_time) if end_time else None
            bars = await self.trading_bot.get_historical_data(
                symbol=symbol.upper(),
                timeframe=timeframe,
                limit=limit,
                end_time=end_dt,
            )

            formatted_bars = []
            for bar in bars:
                ts = bar.get('timestamp') or bar.get('time')
                if isinstance(ts, datetime):
                    ts_str = self._format_iso(ts)
                else:
                    ts_str = str(ts)
                formatted_bars.append({
                    "timestamp": ts_str,
                    "open": float(bar.get('open', 0.0) or 0.0),
                    "high": float(bar.get('high', 0.0) or 0.0),
                    "low": float(bar.get('low', 0.0) or 0.0),
                    "close": float(bar.get('close', 0.0) or 0.0),
                    "volume": float(bar.get('volume', 0.0) or 0.0),
                })

            return {
                "symbol": symbol.upper(),
                "timeframe": timeframe,
                "count": len(formatted_bars),
                "bars": formatted_bars,
            }
        except Exception as e:
            logger.error(f"Error fetching historical data: {e}")
            return {"error": str(e)}
