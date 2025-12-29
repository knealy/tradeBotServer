"""
Backtesting Engine

Event-driven backtesting engine for trading strategies.
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any, Callable
import logging
from .models import (
    BacktestOrder, BacktestTrade, BacktestPosition, BacktestResult,
    OrderSide, OrderType, OrderStatus
)

logger = logging.getLogger(__name__)


class BacktestEngine:
    """
    Event-driven backtesting engine.
    
    Features:
    - Realistic order simulation (market, limit, stop)
    - Slippage modeling
    - Commission calculation
    - Position tracking
    - P&L calculation
    - Trade logging
    """
    
    def __init__(
        self,
        initial_capital: float = 50000.0,
        commission_per_contract: float = 2.50,
        slippage_ticks: float = 0.5,
        point_value: float = 2.0  # MNQ = $2 per point
    ):
        """
        Initialize backtest engine.
        
        Args:
            initial_capital: Starting capital
            commission_per_contract: Commission per contract (round-trip)
            slippage_ticks: Average slippage in ticks
            point_value: Point value for symbol (MNQ=$2, MES=$5, etc.)
        """
        self.initial_capital = initial_capital
        self.commission_per_contract = commission_per_contract
        self.slippage_ticks = slippage_ticks
        self.point_value = point_value
        
        # Current state
        self.capital = initial_capital
        self.positions: Dict[str, BacktestPosition] = {}
        self.pending_orders: List[BacktestOrder] = []
        self.filled_orders: List[BacktestOrder] = []
        self.trades: List[BacktestTrade] = []
        self.equity_curve: List[tuple] = []
        
        # Tracking
        self.current_bar_index = 0
        self.current_timestamp: Optional[datetime] = None
        self.trade_counter = 0
        self.order_counter = 0
    
    def reset(self):
        """Reset engine state for new backtest."""
        self.capital = self.initial_capital
        self.positions.clear()
        self.pending_orders.clear()
        self.filled_orders.clear()
        self.trades.clear()
        self.equity_curve.clear()
        self.current_bar_index = 0
        self.current_timestamp = None
        self.trade_counter = 0
        self.order_counter = 0
    
    def place_order(
        self,
        symbol: str,
        side: OrderSide,
        quantity: int,
        order_type: OrderType = OrderType.MARKET,
        price: Optional[float] = None,
        stop_price: Optional[float] = None,
        limit_price: Optional[float] = None
    ) -> str:
        """
        Place an order in the backtest.
        
        Args:
            symbol: Trading symbol
            side: BUY or SELL
            quantity: Number of contracts
            order_type: Market, limit, stop, etc.
            price: Entry price (for market orders, filled at next bar open)
            stop_price: Stop trigger price
            limit_price: Limit price
            
        Returns:
            Order ID
        """
        self.order_counter += 1
        order_id = f"BT{self.order_counter:06d}"
        
        order = BacktestOrder(
            order_id=order_id,
            timestamp=self.current_timestamp,
            symbol=symbol,
            side=side,
            order_type=order_type,
            quantity=quantity,
            price=price,
            stop_price=stop_price,
            limit_price=limit_price
        )
        
        self.pending_orders.append(order)
        logger.debug(f"Placed {order_type.value} order: {side.value} {quantity} {symbol} @ {price}")
        
        return order_id
    
    def _check_order_fill(
        self,
        order: BacktestOrder,
        bar: pd.Series,
        tick_size: float = 0.25
    ) -> bool:
        """
        Check if an order should fill on this bar.
        
        Args:
            order: Order to check
            bar: Current OHLCV bar
            tick_size: Minimum price increment
            
        Returns:
            True if order filled
        """
        if order.order_type == OrderType.MARKET:
            # Market orders fill at next bar open (with slippage)
            slippage_amount = self.slippage_ticks * tick_size
            if order.side == OrderSide.BUY:
                fill_price = bar['open'] + slippage_amount
            else:
                fill_price = bar['open'] - slippage_amount
            
            order.filled_price = fill_price
            order.filled_timestamp = bar.name  # Index is timestamp
            order.slippage = slippage_amount
            order.status = OrderStatus.FILLED
            return True
        
        elif order.order_type == OrderType.LIMIT:
            # Limit buy fills if low <= limit_price
            # Limit sell fills if high >= limit_price
            if order.side == OrderSide.BUY:
                if bar['low'] <= order.limit_price:
                    order.filled_price = order.limit_price
                    order.filled_timestamp = bar.name
                    order.status = OrderStatus.FILLED
                    return True
            else:  # SELL
                if bar['high'] >= order.limit_price:
                    order.filled_price = order.limit_price
                    order.filled_timestamp = bar.name
                    order.status = OrderStatus.FILLED
                    return True
        
        elif order.order_type == OrderType.STOP:
            # Stop buy triggers if high >= stop_price
            # Stop sell triggers if low <= stop_price
            if order.side == OrderSide.BUY:
                if bar['high'] >= order.stop_price:
                    # Fills at stop price + slippage
                    slippage_amount = self.slippage_ticks * tick_size
                    order.filled_price = order.stop_price + slippage_amount
                    order.filled_timestamp = bar.name
                    order.slippage = slippage_amount
                    order.status = OrderStatus.FILLED
                    return True
            else:  # SELL
                if bar['low'] <= order.stop_price:
                    # Fills at stop price - slippage
                    slippage_amount = self.slippage_ticks * tick_size
                    order.filled_price = order.stop_price - slippage_amount
                    order.filled_timestamp = bar.name
                    order.slippage = slippage_amount
                    order.status = OrderStatus.FILLED
                    return True
        
        return False
    
    def _update_position(
        self,
        filled_order: BacktestOrder,
        current_price: float
    ):
        """
        Update position after order fill.
        
        Args:
            filled_order: Order that just filled
            current_price: Current market price
        """
        symbol = filled_order.symbol
        
        # Calculate commission
        commission = self.commission_per_contract * filled_order.quantity
        filled_order.commission = commission
        self.capital -= commission
        
        # Check if we have existing position
        if symbol in self.positions:
            pos = self.positions[symbol]
            
            # Check if this closes/reduces position
            if pos.side != filled_order.side:
                # Closing or reducing position
                qty_to_close = min(pos.quantity, filled_order.quantity)
                
                # Calculate P&L for closed portion
                if pos.side == OrderSide.BUY:
                    pnl = (filled_order.filled_price - pos.entry_price) * qty_to_close * self.point_value
                else:
                    pnl = (pos.entry_price - filled_order.filled_price) * qty_to_close * self.point_value
                
                # Subtract commission and slippage
                pnl -= commission
                pnl -= filled_order.slippage * qty_to_close * self.point_value
                
                self.capital += pnl
                
                # Create trade record
                self.trade_counter += 1
                trade = BacktestTrade(
                    trade_id=f"T{self.trade_counter:06d}",
                    symbol=symbol,
                    side=pos.side,
                    entry_time=pos.entry_time,
                    exit_time=filled_order.filled_timestamp,
                    entry_price=pos.entry_price,
                    exit_price=filled_order.filled_price,
                    quantity=qty_to_close,
                    pnl=pnl,
                    pnl_percent=(pnl / pos.entry_price / qty_to_close) * 100,
                    commission=commission,
                    slippage=filled_order.slippage * qty_to_close * self.point_value,
                    bars_held=0,  # Calculate later
                    exit_reason="signal",
                    max_favorable_excursion=pos.max_favorable_excursion,
                    max_adverse_excursion=pos.max_adverse_excursion
                )
                self.trades.append(trade)
                
                # Update or close position
                pos.quantity -= qty_to_close
                if pos.quantity == 0:
                    del self.positions[symbol]
                
                # If order quantity > position quantity, open reverse position
                if filled_order.quantity > qty_to_close:
                    remaining_qty = filled_order.quantity - qty_to_close
                    self.positions[symbol] = BacktestPosition(
                        symbol=symbol,
                        side=filled_order.side,
                        quantity=remaining_qty,
                        entry_price=filled_order.filled_price,
                        entry_time=filled_order.filled_timestamp,
                        current_price=current_price
                    )
            else:
                # Adding to position (scale-in)
                total_qty = pos.quantity + filled_order.quantity
                # Weighted average entry price
                pos.entry_price = (
                    (pos.entry_price * pos.quantity + filled_order.filled_price * filled_order.quantity) 
                    / total_qty
                )
                pos.quantity = total_qty
        else:
            # Opening new position
            self.positions[symbol] = BacktestPosition(
                symbol=symbol,
                side=filled_order.side,
                quantity=filled_order.quantity,
                entry_price=filled_order.filled_price,
                entry_time=filled_order.filled_timestamp,
                current_price=current_price
            )
    
    def _update_unrealized_pnl(self, bar: pd.Series):
        """
        Update unrealized P&L for all open positions.
        
        Args:
            bar: Current OHLCV bar
        """
        for pos in self.positions.values():
            current_price = bar['close']
            pos.current_price = current_price
            
            if pos.side == OrderSide.BUY:
                pnl = (current_price - pos.entry_price) * pos.quantity * self.point_value
                # Track MFE/MAE
                favorable = (bar['high'] - pos.entry_price) * pos.quantity * self.point_value
                adverse = (bar['low'] - pos.entry_price) * pos.quantity * self.point_value
            else:  # SHORT
                pnl = (pos.entry_price - current_price) * pos.quantity * self.point_value
                # Track MFE/MAE
                favorable = (pos.entry_price - bar['low']) * pos.quantity * self.point_value
                adverse = (pos.entry_price - bar['high']) * pos.quantity * self.point_value
            
            pos.unrealized_pnl = pnl
            pos.max_favorable_excursion = max(pos.max_favorable_excursion, favorable)
            pos.max_adverse_excursion = min(pos.max_adverse_excursion, adverse)
    
    def _calculate_equity(self) -> float:
        """Calculate total equity (capital + unrealized P&L)."""
        unrealized = sum(pos.unrealized_pnl for pos in self.positions.values())
        return self.capital + unrealized
    
    async def run(
        self,
        strategy_func: Callable,
        data: pd.DataFrame,
        symbol: str,
        strategy_name: str = "UnnamedStrategy",
        tick_size: float = 0.25
    ) -> BacktestResult:
        """
        Run backtest on historical data.
        
        Args:
            strategy_func: Async function(bar_index, data, engine) -> signals
            data: Historical OHLCV DataFrame
            symbol: Trading symbol
            strategy_name: Name of strategy
            tick_size: Minimum price increment
            
        Returns:
            BacktestResult with performance metrics
        """
        logger.info(f"🔬 Running backtest: {strategy_name} on {symbol}")
        logger.info(f"   Period: {data.index[0]} to {data.index[-1]}")
        logger.info(f"   Bars: {len(data)}")
        logger.info(f"   Initial Capital: ${self.initial_capital:,.2f}")
        
        self.reset()
        
        # Iterate through each bar
        for i, (timestamp, bar) in enumerate(data.iterrows()):
            self.current_bar_index = i
            self.current_timestamp = timestamp
            
            # Process pending orders (check fills)
            filled_this_bar = []
            for order in self.pending_orders[:]:
                if self._check_order_fill(order, bar, tick_size):
                    filled_this_bar.append(order)
                    self.pending_orders.remove(order)
                    self.filled_orders.append(order)
                    self._update_position(order, bar['close'])
            
            # Update unrealized P&L for open positions
            if self.positions:
                self._update_unrealized_pnl(bar)
            
            # Record equity
            equity = self._calculate_equity()
            self.equity_curve.append((timestamp, equity))
            
            # Call strategy logic
            try:
                # Strategy returns signals: {"action": "BUY"|"SELL"|None, "quantity": 1, ...}
                signals = await strategy_func(i, data.iloc[:i+1], self)
                
                if signals and signals.get('action'):
                    action = signals['action']
                    quantity = signals.get('quantity', 1)
                    order_type = signals.get('order_type', OrderType.MARKET)
                    
                    if action == "BUY":
                        self.place_order(
                            symbol=symbol,
                            side=OrderSide.BUY,
                            quantity=quantity,
                            order_type=order_type,
                            price=bar['close']  # Reference price
                        )
                    elif action == "SELL":
                        self.place_order(
                            symbol=symbol,
                            side=OrderSide.SELL,
                            quantity=quantity,
                            order_type=order_type,
                            price=bar['close']
                        )
            except Exception as e:
                logger.error(f"Strategy error at bar {i}: {e}")
        
        # Close any remaining positions at end
        if self.positions:
            final_bar = data.iloc[-1]
            for pos in list(self.positions.values()):
                close_order = self.place_order(
                    symbol=pos.symbol,
                    side=OrderSide.SELL if pos.side == OrderSide.BUY else OrderSide.BUY,
                    quantity=pos.quantity,
                    order_type=OrderType.MARKET,
                    price=final_bar['close']
                )
                # Force fill at final price
                for order in self.pending_orders:
                    if order.order_id == close_order:
                        order.filled_price = final_bar['close']
                        order.filled_timestamp = final_bar.name
                        order.status = OrderStatus.FILLED
                        self._update_position(order, final_bar['close'])
                        self.filled_orders.append(order)
                        self.pending_orders.remove(order)
                        break
        
        # Build result
        result = self._build_result(symbol, strategy_name, data.index[0], data.index[-1])
        
        logger.info(f"✅ Backtest complete:")
        logger.info(f"   Total Trades: {result.total_trades}")
        logger.info(f"   Win Rate: {result.win_rate:.1f}%")
        logger.info(f"   Total P&L: ${result.total_pnl:,.2f}")
        logger.info(f"   Return: {result.total_return_pct:.2f}%")
        logger.info(f"   Max Drawdown: {result.max_drawdown_pct:.2f}%")
        logger.info(f"   Sharpe Ratio: {result.sharpe_ratio:.2f}")
        
        return result
    
    def _build_result(
        self,
        symbol: str,
        strategy_name: str,
        start_date: datetime,
        end_date: datetime
    ) -> BacktestResult:
        """Build backtest result from trade history."""
        # Calculate statistics
        total_trades = len(self.trades)
        winning_trades = sum(1 for t in self.trades if t.pnl > 0)
        losing_trades = sum(1 for t in self.trades if t.pnl < 0)
        
        total_pnl = sum(t.pnl for t in self.trades)
        wins = [t.pnl for t in self.trades if t.pnl > 0]
        losses = [t.pnl for t in self.trades if t.pnl < 0]
        
        # Calculate metrics
        win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0.0
        average_win = np.mean(wins) if wins else 0.0
        average_loss = np.mean(losses) if losses else 0.0
        largest_win = max(wins) if wins else 0.0
        largest_loss = min(losses) if losses else 0.0
        
        gross_profit = sum(wins) if wins else 0.0
        gross_loss = abs(sum(losses)) if losses else 0.0
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0.0
        
        expectancy = (average_win * (winning_trades / total_trades) + 
                     average_loss * (losing_trades / total_trades)) if total_trades > 0 else 0.0
        
        # Calculate drawdown
        equity_values = [eq[1] for eq in self.equity_curve]
        max_dd, max_dd_pct = self._calculate_max_drawdown(equity_values)
        
        # Calculate Sharpe ratio
        returns = pd.Series([t.pnl / self.initial_capital for t in self.trades])
        sharpe = self._calculate_sharpe_ratio(returns)
        
        # Calculate Sortino ratio
        sortino = self._calculate_sortino_ratio(returns)
        
        return BacktestResult(
            symbol=symbol,
            strategy_name=strategy_name,
            start_date=start_date,
            end_date=end_date,
            initial_capital=self.initial_capital,
            final_capital=self.capital,
            total_trades=total_trades,
            winning_trades=winning_trades,
            losing_trades=losing_trades,
            win_rate=win_rate,
            total_pnl=total_pnl,
            total_return_pct=(total_pnl / self.initial_capital) * 100,
            average_win=average_win,
            average_loss=average_loss,
            largest_win=largest_win,
            largest_loss=largest_loss,
            max_drawdown=max_dd,
            max_drawdown_pct=max_dd_pct,
            sharpe_ratio=sharpe,
            sortino_ratio=sortino,
            profit_factor=profit_factor,
            expectancy=expectancy,
            total_commission=sum(t.commission for t in self.trades),
            total_slippage=sum(t.slippage for t in self.trades),
            average_bars_held=np.mean([t.bars_held for t in self.trades]) if self.trades else 0.0,
            trades=self.trades,
            equity_curve=self.equity_curve,
            drawdown_curve=self._calculate_drawdown_curve(equity_values)
        )
    
    def _calculate_max_drawdown(self, equity_curve: List[float]) -> tuple:
        """Calculate maximum drawdown."""
        if not equity_curve:
            return 0.0, 0.0
        
        peak = equity_curve[0]
        max_dd = 0.0
        max_dd_pct = 0.0
        
        for value in equity_curve:
            if value > peak:
                peak = value
            dd = peak - value
            dd_pct = (dd / peak) * 100 if peak > 0 else 0.0
            
            if dd > max_dd:
                max_dd = dd
                max_dd_pct = dd_pct
        
        return max_dd, max_dd_pct
    
    def _calculate_drawdown_curve(self, equity_curve: List[float]) -> List[tuple]:
        """Calculate drawdown curve over time."""
        if not equity_curve or not self.equity_curve:
            return []
        
        peak = equity_curve[0]
        drawdowns = []
        
        for i, value in enumerate(equity_curve):
            if value > peak:
                peak = value
            dd_pct = ((peak - value) / peak) * 100 if peak > 0 else 0.0
            drawdowns.append((self.equity_curve[i][0], dd_pct))
        
        return drawdowns
    
    def _calculate_sharpe_ratio(self, returns: pd.Series, risk_free_rate: float = 0.0) -> float:
        """Calculate Sharpe ratio."""
        if len(returns) < 2:
            return 0.0
        
        excess_returns = returns - risk_free_rate
        if excess_returns.std() == 0:
            return 0.0
        
        return (excess_returns.mean() / excess_returns.std()) * np.sqrt(252)  # Annualized
    
    def _calculate_sortino_ratio(self, returns: pd.Series, risk_free_rate: float = 0.0) -> float:
        """Calculate Sortino ratio (only penalizes downside volatility)."""
        if len(returns) < 2:
            return 0.0
        
        excess_returns = returns - risk_free_rate
        downside_returns = excess_returns[excess_returns < 0]
        
        if len(downside_returns) == 0 or downside_returns.std() == 0:
            return 0.0
        
        return (excess_returns.mean() / downside_returns.std()) * np.sqrt(252)  # Annualized
