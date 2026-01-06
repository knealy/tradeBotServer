"""
Strategy Replay Engine - Replays historical data through actual strategy classes.

This module allows backtesting using the same strategy code that runs live,
ensuring identical logic between backtest and production.
"""

import logging
import asyncio
from typing import Optional, Dict, Any, List
from datetime import datetime
import pandas as pd

from .engine import BacktestEngine
from .models import BacktestResult, OrderSide, OrderType, OrderStatus

logger = logging.getLogger(__name__)


class StrategyReplayEngine:
    """
    Replay engine that runs actual strategy classes on historical data.
    
    Intercepts strategy order placement calls and simulates execution
    using the BacktestEngine.
    """
    
    def __init__(
        self,
        strategy_instance,
        trading_bot,
        initial_capital: float = 50000.0,
        commission_per_contract: float = 2.50,
        slippage_ticks: float = 0.5,
        point_value: float = 2.0
    ):
        """
        Initialize strategy replay engine.
        
        Args:
            strategy_instance: Actual strategy instance (e.g., SimpleCandleStrategy)
            trading_bot: Trading bot instance (for data fetching)
            initial_capital: Starting capital
            commission_per_contract: Commission per contract
            slippage_ticks: Slippage in ticks
            point_value: Point value for symbol
        """
        self.strategy = strategy_instance
        self.trading_bot = trading_bot
        self.backtest_engine = BacktestEngine(
            initial_capital=initial_capital,
            commission_per_contract=commission_per_contract,
            slippage_ticks=slippage_ticks,
            point_value=point_value
        )
        
        # Track original methods to restore later
        self._original_place_bracket_order = None
        self._original_place_market_order = None
        
        # Track current bar data for strategy
        self._current_bars: List[Dict] = []
        self._current_symbol: Optional[str] = None
    
    async def replay(
        self,
        symbol: str,
        bars: List[Dict],
        tick_size: float = 0.25
    ) -> BacktestResult:
        """
        Replay strategy on historical bars.
        
        Args:
            symbol: Trading symbol
            bars: List of historical bar dicts with OHLCV data (or DataFrame)
            tick_size: Minimum price increment
            
        Returns:
            BacktestResult with performance metrics
        """
        # Handle DataFrame input (from sample data)
        if isinstance(bars, pd.DataFrame):
            # Convert DataFrame to list of dicts
            bars_list = []
            for idx, row in bars.iterrows():
                bars_list.append({
                    'timestamp': idx if isinstance(idx, datetime) else pd.to_datetime(idx),
                    'open': float(row.get('open', row.get('Open', 0))),
                    'high': float(row.get('high', row.get('High', 0))),
                    'low': float(row.get('low', row.get('Low', 0))),
                    'close': float(row.get('close', row.get('Close', 0))),
                    'volume': int(row.get('volume', row.get('Volume', 0)))
                })
            bars = bars_list
        
        logger.info(f"🔄 Starting strategy replay: {self.strategy.config.name} on {symbol}")
        logger.info(f"   Bars: {len(bars)}")
        if bars and isinstance(bars[0], dict):
            first_ts = bars[0].get('timestamp', 'N/A')
            last_ts = bars[-1].get('timestamp', 'N/A')
            logger.info(f"   Period: {first_ts} to {last_ts}")
        
        self._current_symbol = symbol
        self._current_bars = bars
        
        # Convert bars to DataFrame for BacktestEngine
        df = self._bars_to_dataframe(bars)
        
        # Intercept strategy's order placement methods
        self._intercept_strategy_methods()
        
        try:
            # Run strategy on each bar
            for i, (timestamp, bar) in enumerate(df.iterrows()):
                self.backtest_engine.current_bar_index = i
                self.backtest_engine.current_timestamp = timestamp
                
                # Process pending orders (check fills)
                filled_this_bar = []
                for order in self.backtest_engine.pending_orders[:]:
                    if self.backtest_engine._check_order_fill(order, bar, tick_size):
                        filled_this_bar.append(order)
                        self.backtest_engine.pending_orders.remove(order)
                        self.backtest_engine.filled_orders.append(order)
                        self.backtest_engine._update_position(order, bar['close'])

                # OCO handling: if an exit order fills, cancel the sibling order(s)
                if filled_this_bar and self.backtest_engine.pending_orders:
                    for filled in filled_this_bar:
                        oco_group = getattr(filled, "oco_group", None)
                        if not oco_group:
                            continue
                        # Cancel any remaining orders in the same OCO group
                        for pending in self.backtest_engine.pending_orders[:]:
                            if getattr(pending, "oco_group", None) == oco_group:
                                pending.status = OrderStatus.CANCELLED
                                self.backtest_engine.pending_orders.remove(pending)

                # If an entry order filled and carried bracket prices, place TP/SL exit orders
                # Note: we intentionally do NOT allow these exits to fill in the *same* bar to avoid look-ahead bias.
                for filled in filled_this_bar:
                    stop_loss_price = getattr(filled, "stop_loss_price", None)
                    take_profit_price = getattr(filled, "take_profit_price", None)
                    if stop_loss_price is None or take_profit_price is None:
                        continue

                    # Create OCO group using the entry order id
                    oco_group = f"{filled.order_id}_BRACKET"

                    # Attach bracket levels to the open position (for reference)
                    pos = self.backtest_engine.positions.get(filled.symbol)
                    if pos:
                        pos.stop_loss = float(stop_loss_price)
                        pos.take_profit = float(take_profit_price)

                    exit_side = OrderSide.SELL if filled.side == OrderSide.BUY else OrderSide.BUY

                    # Stop-loss exit (STOP)
                    sl_order_id = self.backtest_engine.place_order(
                        symbol=filled.symbol,
                        side=exit_side,
                        quantity=filled.quantity,
                        order_type=OrderType.STOP,
                        stop_price=float(stop_loss_price),
                        price=float(stop_loss_price)
                    )
                    for o in self.backtest_engine.pending_orders:
                        if o.order_id == sl_order_id:
                            o.oco_group = oco_group
                            o.exit_reason = "stop_loss"
                            break

                    # Take-profit exit (LIMIT)
                    tp_order_id = self.backtest_engine.place_order(
                        symbol=filled.symbol,
                        side=exit_side,
                        quantity=filled.quantity,
                        order_type=OrderType.LIMIT,
                        limit_price=float(take_profit_price),
                        price=float(take_profit_price)
                    )
                    for o in self.backtest_engine.pending_orders:
                        if o.order_id == tp_order_id:
                            o.oco_group = oco_group
                            o.exit_reason = "take_profit"
                            break
                
                # Update unrealized P&L
                if self.backtest_engine.positions:
                    self.backtest_engine._update_unrealized_pnl(bar)
                
                # Record equity
                equity = self.backtest_engine._calculate_equity()
                self.backtest_engine.equity_curve.append((timestamp, equity))
                
                # Call strategy's analyze() method with current bar data
                try:
                    # Update strategy's active_positions from backtest engine
                    self._sync_strategy_positions()
                    
                    # Get bars up to current point for strategy analysis
                    current_bars_for_strategy = bars[:i+1]
                    
                    # Update mock trading bot's bars to current set
                    if hasattr(self.trading_bot, 'bars'):
                        self.trading_bot.bars = current_bars_for_strategy
                    
                    # Call strategy analyze (it will use trading_bot.get_historical_data internally)
                    # Also mock get_open_positions / get_market_quote so the strategy behaves like live
                    signal = await self._call_strategy_analyze(symbol, current_bars_for_strategy)
                    
                    if signal:
                        # Strategy wants to place an order - call execute which uses place_bracket_order
                        # This will be intercepted by our _simulate_place_bracket_order
                        try:
                            await self.strategy.execute(signal)
                        except Exception as exec_err:
                            logger.error(f"Error in strategy.execute: {exec_err}", exc_info=True)
                
                except Exception as e:
                    logger.error(f"Strategy error at bar {i}: {e}", exc_info=True)
            
            # Close any remaining positions at end
            if self.backtest_engine.positions:
                final_bar = df.iloc[-1]
                for pos in list(self.backtest_engine.positions.values()):
                    close_order_id = self.backtest_engine.place_order(
                        symbol=pos.symbol,
                        side=OrderSide.SELL if pos.side == OrderSide.BUY else OrderSide.BUY,
                        quantity=pos.quantity,
                        order_type=OrderType.MARKET,
                        price=final_bar['close']
                    )
                    # Force fill at final price
                    for order in self.backtest_engine.pending_orders:
                        if order.order_id == close_order_id:
                            order.filled_price = final_bar['close']
                            order.filled_timestamp = final_bar.name
                            order.status = OrderStatus.FILLED
                            self.backtest_engine._update_position(order, final_bar['close'])
                            self.backtest_engine.filled_orders.append(order)
                            self.backtest_engine.pending_orders.remove(order)
                            break
            
            # Build result
            result = self.backtest_engine._build_result(
                symbol=symbol,
                strategy_name=self.strategy.config.name,
                start_date=df.index[0],
                end_date=df.index[-1]
            )
            
            logger.info(f"✅ Strategy replay complete:")
            logger.info(f"   Total Trades: {result.total_trades}")
            logger.info(f"   Win Rate: {result.win_rate:.1f}%")
            logger.info(f"   Total P&L: ${result.total_pnl:,.2f}")
            
            return result
        
        finally:
            # Restore original methods
            self._restore_strategy_methods()
    
    def _bars_to_dataframe(self, bars: List[Dict]) -> pd.DataFrame:
        """Convert bar dicts to pandas DataFrame."""
        data = []
        for bar in bars:
            # Handle different timestamp formats
            timestamp = bar.get('timestamp')
            if isinstance(timestamp, str):
                timestamp = pd.to_datetime(timestamp)
            elif isinstance(timestamp, (int, float)):
                timestamp = pd.to_datetime(timestamp, unit='s')
            
            data.append({
                'open': float(bar.get('open', bar.get('o', 0))),
                'high': float(bar.get('high', bar.get('h', 0))),
                'low': float(bar.get('low', bar.get('l', 0))),
                'close': float(bar.get('close', bar.get('c', 0))),
                'volume': int(bar.get('volume', bar.get('v', 0)))
            })
        
        df = pd.DataFrame(data, index=[pd.to_datetime(b.get('timestamp')) for b in bars])
        return df
    
    def _intercept_strategy_methods(self):
        """Intercept strategy's order placement methods to simulate execution."""
        # Intercept place_bracket_order
        if hasattr(self.strategy, 'place_bracket_order'):
            self._original_place_bracket_order = self.strategy.place_bracket_order
            self.strategy.place_bracket_order = self._simulate_place_bracket_order
        
        # Intercept place_market_order if it exists
        if hasattr(self.strategy, 'place_market_order'):
            self._original_place_market_order = self.strategy.place_market_order
            self.strategy.place_market_order = self._simulate_place_market_order
    
    def _restore_strategy_methods(self):
        """Restore original strategy methods."""
        if self._original_place_bracket_order:
            self.strategy.place_bracket_order = self._original_place_bracket_order
        if self._original_place_market_order:
            self.strategy.place_market_order = self._original_place_market_order
    
    async def _simulate_place_bracket_order(
        self,
        symbol: str,
        side: str,
        quantity: int,
        entry_price: float,
        stop_loss_price: float,
        take_profit_price: float,
        enable_breakeven: bool = False
    ) -> Dict[str, Any]:
        """
        Simulate bracket order placement in backtest.
        
        This intercepts the strategy's place_bracket_order call and
        simulates execution using the BacktestEngine.
        """
        logger.debug(f"📝 Simulating bracket order: {side} {quantity} {symbol} @ {entry_price:.2f}")
        
        # Convert side to OrderSide
        order_side = OrderSide.BUY if side.upper() == "BUY" else OrderSide.SELL
        
        # Place stop order for entry
        entry_order_id = self.backtest_engine.place_order(
            symbol=symbol,
            side=order_side,
            quantity=quantity,
            order_type=OrderType.STOP,
            stop_price=entry_price,
            price=entry_price
        )
        
        # Track bracket orders for stop loss and take profit
        # These will be placed when entry order fills
        # Store bracket info in order metadata
        entry_order = None
        for order in self.backtest_engine.pending_orders:
            if order.order_id == entry_order_id:
                entry_order = order
                # Store bracket prices in order (we'll use these when entry fills)
                entry_order.stop_loss_price = stop_loss_price
                entry_order.take_profit_price = take_profit_price
                break
        
        return {
            'success': True,
            'orderId': entry_order_id,
            'message': 'Bracket order simulated in backtest',
            'method': 'backtest_simulation'
        }
    
    async def _simulate_place_market_order(
        self,
        symbol: str,
        side: str,
        quantity: int,
        **kwargs
    ) -> Dict[str, Any]:
        """Simulate market order placement in backtest."""
        order_side = OrderSide.BUY if side.upper() == "BUY" else OrderSide.SELL
        
        # Get current price from latest bar
        if self.backtest_engine.current_timestamp and self._current_bars:
            current_bar = self._current_bars[self.backtest_engine.current_bar_index]
            current_price = float(current_bar.get('close', current_bar.get('c', 0)))
        else:
            current_price = 0.0
        
        order_id = self.backtest_engine.place_order(
            symbol=symbol,
            side=order_side,
            quantity=quantity,
            order_type=OrderType.MARKET,
            price=current_price
        )
        
        return {
            'success': True,
            'orderId': order_id,
            'message': 'Market order simulated in backtest',
            'method': 'backtest_simulation'
        }
    
    async def _call_strategy_analyze(self, replay_symbol: str, bars_list: List[Dict]) -> Optional[Dict]:
        """
        Call strategy's analyze method with mocked historical data.
        
        We need to mock trading_bot.get_historical_data to return our bars.
        This prevents look-ahead bias by only returning bars up to the current point.
        
        Args:
            replay_symbol: The symbol being backtested (e.g., "MNQ")
            bars_list: List of bars available up to current replay position
        """
        # Store original methods
        original_get_historical = getattr(self.trading_bot, "get_historical_data", None)
        original_get_open_positions = getattr(self.trading_bot, "get_open_positions", None)
        original_get_market_quote = getattr(self.trading_bot, "get_market_quote", None)
        
        # Mock get_historical_data to return only bars up to current point
        # This is a plain function (not bound method), so no 'self' parameter
        # Parameter names MUST match the keyword arguments passed by strategies
        async def mock_get_historical_data(
            symbol: str,
            timeframe: str = "1m",
            limit: int = 100,
            start_time: Optional[datetime] = None,
            end_time: Optional[datetime] = None
        ) -> List[Dict]:
            """Mock that returns historical bars only up to current replay position."""
            if symbol.upper() == replay_symbol.upper():
                # Return bars up to current point (last N bars if limit specified)
                if limit and limit < len(bars_list):
                    return bars_list[-limit:]
                return bars_list
            # If different symbol requested (shouldn't happen in backtest)
            logger.warning(f"Backtest requested different symbol: {symbol} != {replay_symbol}")
            return []

        async def mock_get_open_positions(account_id=None):
            """
            Return positions derived from the backtest engine.
            This is critical because many live strategies (including simple_candle)
            gate signals based on current positions.
            """
            positions = []
            for sym, pos in self.backtest_engine.positions.items():
                positions.append({
                    "symbol": sym,
                    "side": "LONG" if pos.side == OrderSide.BUY else "SHORT",
                    "quantity": pos.quantity,
                    "entry_price": pos.entry_price,
                })
            return positions

        async def mock_get_market_quote(symbol: str):
            """Return quote derived from the current replay bar (close)."""
            if self.backtest_engine.current_bar_index is not None and bars_list:
                last_bar = bars_list[-1]
                close_price = float(last_bar.get("close", last_bar.get("c", 0)) or 0)
                return {"bid": close_price, "ask": close_price, "last": close_price}
            return {"bid": 0, "ask": 0, "last": 0}
        
        # Temporarily replace methods with our mocks
        if original_get_historical is not None:
            self.trading_bot.get_historical_data = mock_get_historical_data
        if original_get_open_positions is not None or hasattr(self.trading_bot, "get_open_positions"):
            self.trading_bot.get_open_positions = mock_get_open_positions
        if original_get_market_quote is not None or hasattr(self.trading_bot, "get_market_quote"):
            self.trading_bot.get_market_quote = mock_get_market_quote
        
        try:
            # Call strategy's analyze method
            signal = await self.strategy.analyze(replay_symbol)
            return signal
        finally:
            # Restore original methods
            if original_get_historical is not None:
                self.trading_bot.get_historical_data = original_get_historical
            if original_get_open_positions is not None:
                self.trading_bot.get_open_positions = original_get_open_positions
            if original_get_market_quote is not None:
                self.trading_bot.get_market_quote = original_get_market_quote
    
    async def _execute_strategy_signal(self, signal: Dict, current_bar: pd.Series, tick_size: float):
        """Execute a strategy signal by placing orders in backtest engine."""
        action = signal.get('action')
        symbol = signal.get('symbol')
        entry_price = signal.get('entry_price')
        stop_loss = signal.get('stop_loss')
        take_profit = signal.get('take_profit')
        quantity = signal.get('quantity', 1)
        
        if action == "LONG":
            side = OrderSide.BUY
        elif action == "SHORT":
            side = OrderSide.SELL
        else:
            return
        
        # Place stop order for entry
        entry_order_id = self.backtest_engine.place_order(
            symbol=symbol,
            side=side,
            quantity=quantity,
            order_type=OrderType.STOP,
            stop_price=entry_price,
            price=entry_price
        )
        
        logger.debug(f"📊 Signal executed: {action} {quantity} {symbol} @ {entry_price:.2f}")
    
    def _sync_strategy_positions(self):
        """Sync strategy's active_positions from backtest engine positions."""
        if not hasattr(self.strategy, 'active_positions'):
            return
        
        # Convert backtest positions to strategy format
        strategy_positions = []
        for pos in self.backtest_engine.positions.values():
            strategy_positions.append({
                'symbol': pos.symbol,
                'side': 'LONG' if pos.side == OrderSide.BUY else 'SHORT',
                'quantity': pos.quantity,
                'entry_price': pos.entry_price,
                'unrealized_pnl': pos.unrealized_pnl
            })
        
        self.strategy.active_positions = strategy_positions

