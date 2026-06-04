"""User Hub SignalR callbacks (extracted from trading_bot)."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict

logger = logging.getLogger(__name__)


class UserHubHandlers:
    """Holds User Hub callbacks; registered on ``TopStepXTradingBot.user_hub_manager``."""

    __slots__ = ("_bot",)

    def __init__(self, bot):
        self._bot = bot

    def _hub_loop(self):
        """Event loop captured when User Hub started (SignalR threads use this to schedule)."""
        return getattr(self._bot.user_hub_manager, "_event_loop", None)

    def _defer_coro_from_sync(self, coro) -> None:
        """Run async tail work on the bot loop without unbounded ``create_task`` fan-out."""
        loop = self._hub_loop()
        q = getattr(self._bot, "hub_deferred_queue", None)
        if q is not None and loop is not None:
            q.schedule_from_thread(loop, coro)
            return
        if loop is not None and loop.is_running():

            async def _wrap():
                try:
                    await coro
                except Exception:
                    logger.debug("User hub async tail failed", exc_info=True)

            try:
                asyncio.run_coroutine_threadsafe(_wrap(), loop)
            except Exception:
                if asyncio.iscoroutine(coro):
                    coro.close()
        elif asyncio.iscoroutine(coro):
            coro.close()

    async def on_account(self, data: Dict):
        """User Hub account callback — keep thin; heavy work runs on `hub_deferred_queue`."""
        try:
            payload = dict(data) if isinstance(data, dict) else {}
            q = getattr(self._bot, "hub_deferred_queue", None)
            if q is not None:
                await q.ensure_started()
                await q.schedule(self._on_account_deferred(payload))
            else:
                await self._on_account_deferred(payload)
        except Exception as e:
            logger.error("Error handling User Hub account update: %s", e)

    async def _on_account_deferred(self, data: Dict):
        """PnL enrichment, EventBus publish, and GUI broadcast (sequenced by `HubDeferredWorkQueue`)."""
        unrealized_pnl = 0.0
        realized_pnl = 0.0
        account_id_str = str(data.get("id", ""))

        if hasattr(self._bot, "account_tracker") and self._bot.account_tracker:
            try:
                account_state = self._bot.account_tracker.get_state(account_id=account_id_str)
                if account_state:
                    realized_pnl = float(account_state.get("realized_pnl", 0))
                    unrealized_pnl = float(account_state.get("unrealized_pnl", 0))
                    logger.debug(
                        "Account state PnL — realized=%.2f unrealized=%.2f",
                        realized_pnl,
                        unrealized_pnl,
                    )

                if unrealized_pnl == 0.0 and account_id_str:
                    try:
                        positions = await self._bot.get_open_positions(account_id=account_id_str)
                        if positions:
                            for pos in positions:
                                upnl = pos.get("unrealizedPnL") or pos.get("unrealized_pnl") or pos.get("unrealizedPnl") or 0
                                if upnl:
                                    unrealized_pnl += float(upnl)
                    except Exception as pos_err:
                        logger.debug("Could not fetch positions for PnL: %s", pos_err)

                try:
                    if self._bot.account_tracker and account_id_str:
                        positions = await self._bot.get_open_positions(account_id=account_id_str)
                        if positions:
                            current_prices = {}
                            for pos in positions:
                                symbol = pos.get("symbol", "")
                                if not symbol:
                                    continue
                                try:
                                    quote = await self._bot.get_market_quote(symbol)
                                    if quote and "error" not in quote:
                                        current_prices[symbol] = float(
                                            quote.get("last") or quote.get("bid") or quote.get("ask") or 0
                                        )
                                except Exception:
                                    pass

                            if current_prices:
                                self._bot.account_tracker.update_unrealised_pnl(account_id_str, positions, current_prices)
                                account_state = self._bot.account_tracker.get_state(account_id=account_id_str)
                                if account_state:
                                    realized_pnl = float(account_state.get("realized_pnl", 0))
                                    unrealized_pnl = float(account_state.get("unrealized_pnl", 0))
                except Exception as update_err:
                    logger.debug("Could not update account tracker PnL: %s", update_err)
            except Exception as e:
                logger.debug("Error calculating PnL for account update: %s", e, exc_info=True)

        if getattr(self._bot, "event_bus", None):
            from core.events import Event, EventType

            try:
                await self._bot.event_bus.publish(
                    Event(
                        type=EventType.ACCOUNT_UPDATED,
                        data={
                            "account": data,
                            "account_id": account_id_str,
                            "unrealized_pnl": unrealized_pnl,
                            "realized_pnl": realized_pnl,
                        },
                        source="signalr_user_hub",
                    )
                )
            except Exception as e:
                logger.debug("Could not emit account event: %s", e)

        try:
            try:
                from gui.chart_html import broadcast_update
            except ImportError:
                broadcast_update = None

            if not broadcast_update:
                return

            account_update = {
                "account_id": data.get("id"),
                "account_name": data.get("name"),
                "balance": data.get("balance", 0),
                "unrealized_pnl": unrealized_pnl,
                "realized_pnl": realized_pnl,
                "canTrade": data.get("canTrade", True),
                "isVisible": data.get("isVisible", True),
                "simulated": data.get("simulated", False),
            }
            await broadcast_update({"type": "account", "data": account_update}, immediate=False)
            logger.debug(
                "Broadcast account update name=%s balance=%s",
                account_update.get("account_name"),
                account_update.get("balance"),
            )
        except Exception as e:
            logger.debug("Could not broadcast account update to GUI: %s", e)
    
    def on_position(self, data: Dict):
        """Callback for User Hub position updates (sync); invalidates cache then defers I/O."""
        try:
            account_id_str = str(data.get("accountId", ""))
            if account_id_str and getattr(self._bot, "state_cache", None):
                self._bot.state_cache.invalidate_positions(account_id_str)
                logger.debug("Invalidated positions cache for account %s", account_id_str)

            self._defer_coro_from_sync(self._on_position_async_tail(dict(data), account_id_str))
        except Exception as e:
            logger.error("Error handling User Hub position update: %s", e)

    async def _on_position_async_tail(self, data: Dict, account_id_str: str) -> None:
        """EventBus, chained account refresh, and GUI (runs on `HubDeferredWorkQueue`)."""
        from core.events import Event, EventType

        if self._bot.event_bus:
            try:
                await self._bot.event_bus.publish(
                    Event(
                        type=EventType.POSITION_UPDATED,
                        data={"position": data, "account_id": account_id_str},
                        source="signalr_user_hub",
                    )
                )
            except Exception as e:
                logger.debug("Could not emit position event: %s", e)

        if account_id_str and self._bot.account_tracker:
            try:
                await self.on_account({"id": account_id_str})
            except Exception as update_err:
                logger.debug("Could not trigger account update after position change: %s", update_err)

        try:
            try:
                from gui.chart_html import broadcast_update
            except ImportError:
                broadcast_update = None
            if not broadcast_update:
                return

            contract_id = data.get("contractId", "") or ""
            symbol = ""
            if contract_id:
                symbol = self._bot.contract_manager.get_symbol_from_contract_id(contract_id) or ""

            position_data = {
                "id": data.get("id"),
                "accountId": data.get("accountId"),
                "contractId": contract_id,
                "symbol": symbol,
                "side": "LONG" if data.get("type") == 1 else "SHORT",
                "quantity": data.get("size", 0),
                "entryPrice": data.get("averagePrice", 0),
                "creationTimestamp": data.get("creationTimestamp"),
            }
            await broadcast_update(
                {
                    "type": "position_opened" if data.get("size", 0) > 0 else "position_closed",
                    "data": {"positions": [position_data]},
                },
                immediate=True,
            )

            account_id = account_id_str
            if account_id and self._bot.account_tracker:
                positions = await self._bot.get_open_positions(account_id=account_id)
                unrealized_pnl = sum(
                    float(p.get("unrealizedPnL") or p.get("unrealized_pnl") or 0) for p in positions
                )
                daily_pnl = self._bot.account_tracker.get_daily_pnl(account_id=account_id) or 0.0
                realized_pnl = float(daily_pnl)
                balance = 0.0
                acct = self._bot.selected_account
                if isinstance(acct, dict):
                    balance = float(acct.get("balance", 0.0))
                name = acct.get("name", "") if isinstance(acct, dict) else ""
                await broadcast_update(
                    {
                        "type": "account",
                        "data": {
                            "account_id": account_id,
                            "account_name": name,
                            "balance": balance,
                            "unrealized_pnl": unrealized_pnl,
                            "realized_pnl": realized_pnl,
                        },
                    },
                    immediate=False,
                )
                logger.debug(
                    "Updated account PnL after position change unrealized=%.2f realized=%.2f",
                    unrealized_pnl,
                    realized_pnl,
                )
        except Exception as e:
            logger.debug("Could not broadcast position update to GUI: %s", e)
    
    def on_order(self, data: Dict):
        """Callback for User Hub order updates."""
        try:
            # SignalR can deliver payload as [dict] or [dict, ...]
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict):
                        self.on_order(item)
                return
            
            account_id_str = str(data.get("accountId", ""))
            if account_id_str and getattr(self._bot, "state_cache", None):
                self._bot.state_cache.invalidate_orders(account_id_str)
                logger.debug("Invalidated orders cache for account %s", account_id_str)

            self._defer_coro_from_sync(self._on_order_async_tail(dict(data), account_id_str))
        except Exception as e:
            logger.error("Error handling User Hub order update: %s", e)

    async def _on_order_async_tail(self, data: Dict, account_id_str: str) -> None:
        from core.events import Event, EventType

        if self._bot.event_bus:
            try:
                status = data.get("status", "")
                if status == "Filled" or status == 2:
                    event_type = EventType.ORDER_FILLED
                elif status == "Cancelled" or status == 3:
                    event_type = EventType.ORDER_CANCELLED
                elif status == "Rejected" or status == 4:
                    event_type = EventType.ORDER_REJECTED
                else:
                    event_type = EventType.ORDER_UPDATED
                await self._bot.event_bus.publish(
                    Event(
                        type=event_type,
                        data={"order": data, "account_id": account_id_str},
                        source="signalr_user_hub",
                    )
                )
            except Exception as e:
                logger.debug("Could not emit order event: %s", e)

        try:
            try:
                from gui.chart_html import broadcast_update
            except ImportError:
                broadcast_update = None
            if not broadcast_update:
                return

            order_data = {
                "id": data.get("id"),
                "accountId": data.get("accountId"),
                "contractId": data.get("contractId"),
                "symbolId": data.get("symbolId"),
                "status": data.get("status"),
                "type": data.get("type"),
                "side": "BUY" if data.get("side") == 0 else "SELL",
                "size": data.get("size", 0),
                "limitPrice": data.get("limitPrice"),
                "stopPrice": data.get("stopPrice"),
                "fillVolume": data.get("fillVolume", 0),
                "filledPrice": data.get("filledPrice"),
                "customTag": data.get("customTag"),
            }
            status = data.get("status")
            is_critical = status in (2, 3, 4)
            gui_type = (
                "order_filled"
                if status == 2
                else "order_canceled"
                if status == 3
                else "order_rejected"
                if status == 4
                else "order_updated"
            )
            await broadcast_update({"type": gui_type, "data": {"orders": [order_data]}}, immediate=is_critical)
        except Exception as e:
            logger.debug("Could not broadcast order update to GUI: %s", e)
    
    def on_trade(self, data: Dict):
        """Callback for User Hub trade updates."""
        try:
            # SignalR can deliver payload as [dict] or [dict, ...]
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict):
                        self.on_trade(item)
                return
    
            account_id = str(data.get('accountId', ''))
            if not account_id:
                return
            
            # Process fill through session trade tracker (FIFO matching)
            if getattr(self._bot, "session_trade_tracker", None):
                try:
                    # Extract fill data from trade update
                    fill_id = str(data.get('fillId') or data.get('id') or f"fill_{int(datetime.now(timezone.utc).timestamp() * 1000)}")
                    order_id = str(data.get('orderId') or data.get('order_id', ''))
                    symbol = str(data.get('symbol') or data.get('contractSymbol', ''))
                    side = int(data.get('side', 0))  # 0=BUY, 1=SELL
                    quantity = int(data.get('quantity') or data.get('qty', 0))
                    price = float(data.get('price') or data.get('fillPrice', 0))
                    commission = float(data.get('commission', 0))
                    fee = float(data.get('fee', 0))
                    
                    # Parse timestamp if available
                    timestamp = None
                    if 'timestamp' in data:
                        try:
                            if isinstance(data['timestamp'], str):
                                timestamp = datetime.fromisoformat(data['timestamp'].replace('Z', '+00:00'))
                            else:
                                timestamp = datetime.fromtimestamp(data['timestamp'], tz=timezone.utc)
                        except:
                            pass
                    
                    if fill_id and order_id and symbol and quantity > 0 and price > 0:
                        # Process fill through session tracker
                        completed_trades = self._bot.session_trade_tracker.process_fill(
                            fill_id=fill_id,
                            order_id=order_id,
                            account_id=account_id,
                            symbol=symbol,
                            side=side,
                            quantity=quantity,
                            price=price,
                            commission=commission,
                            fee=fee,
                            timestamp=timestamp
                        )
                        
                        # If trades were completed, update AccountTracker with realized PnL
                        if completed_trades:
                            total_realized_pnl = sum(t.net_pnl for t in completed_trades)
                            total_commission = sum(t.commission for t in completed_trades)
                            total_fee = sum(t.fee for t in completed_trades)
                            
                            fill_data = {
                                'pnl': total_realized_pnl,
                                'commission': total_commission,
                                'fee': total_fee
                            }
                            
                            if self._bot.account_tracker:
                                self._bot.account_tracker.update_from_fill(account_id, fill_data)
                            
                            logger.info(f"✅ Processed {len(completed_trades)} completed trade(s), Realized PnL: ${total_realized_pnl:.2f}")

                            # Publish TRADE_CLOSED per completed trade so strategies
                            # (e.g. consec-loss breaker) can react to live PnL the
                            # same way the backtest engine drives them from
                            # ``_replay_engine.trades``.  Best-effort: a missing
                            # event_bus must NOT block the fill-processing path.
                            if self._bot.event_bus:
                                from core.events import Event, EventType
                                for t in completed_trades:
                                    try:
                                        await self._bot.event_bus.publish(
                                            Event(
                                                type=EventType.TRADE_CLOSED,
                                                data={
                                                    "trade_id": t.trade_id,
                                                    "account_id": account_id,
                                                    "symbol": t.symbol,
                                                    "side": t.side,
                                                    "quantity": t.quantity,
                                                    "entry_price": t.entry_price,
                                                    "exit_price": t.exit_price,
                                                    "entry_time": t.entry_time,
                                                    "exit_time": t.exit_time,
                                                    "gross_pnl": t.gross_pnl,
                                                    "net_pnl": t.net_pnl,
                                                    "commission": t.commission,
                                                    "fee": t.fee,
                                                    "duration_seconds": t.duration_seconds,
                                                    "session_id": t.session_id,
                                                },
                                                source="user_hub_handlers",
                                            )
                                        )
                                    except Exception as exc:
                                        logger.debug(
                                            "Could not publish TRADE_CLOSED for trade %s: %s",
                                            t.trade_id, exc,
                                        )
                            
                            # Broadcast trade updates to GUI
                            try:
                                try:
                                    from gui.chart_html import broadcast_update
                                except ImportError:
                                    broadcast_update = None
                                
                                if broadcast_update:
                                    session_pnl = self._bot.session_trade_tracker.get_session_pnl(account_id)
                                    broadcast_update({
                                        'type': 'session_trades',
                                        'data': {
                                            'completed_trades': [t.to_dict() for t in completed_trades],
                                            'session_pnl': session_pnl,
                                            'realized_pnl': total_realized_pnl
                                        }
                                    })
                            except Exception as e:
                                logger.debug(f"Could not broadcast trade update to GUI: {e}")
                except Exception as e:
                    logger.error(f"Error processing fill in session tracker: {e}", exc_info=True)
            
            # Legacy: Update AccountTracker with trade PnL (fallback if session tracker not available)
            if data.get("profitAndLoss") and self._bot.account_tracker:
                try:
                    fill_data = {
                        'pnl': float(data.get('profitAndLoss', 0)),
                        'commission': float(data.get('commission', 0)),
                        'fee': float(data.get('fee', 0))
                    }
                    self._bot.account_tracker.update_from_fill(account_id, fill_data)
                    logger.debug(f"✅ Updated AccountTracker with trade PnL: ${fill_data['pnl']:.2f}")
                except Exception as e:
                    logger.debug(f"Could not update AccountTracker with trade: {e}")
    
            # Update account PnL from trade
            if data.get('profitAndLoss'):
                try:
                    try:
                        from gui.chart_html import broadcast_update
                    except ImportError:
                        broadcast_update = None
                    
                    if not broadcast_update:
                        return  # GUI not available
                        
                    broadcast_update({
                        'type': 'account',
                        'data': {
                            'realized_pnl': data.get('profitAndLoss', 0)
                        }
                    })
                except Exception as e:
                    logger.debug(f"Could not broadcast trade update to GUI: {e}")
        except Exception as e:
            logger.error(f"Error handling User Hub trade update: {e}", exc_info=True)
