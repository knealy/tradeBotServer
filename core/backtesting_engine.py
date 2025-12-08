"""
Backtesting Simulation Engine

Provides a comprehensive backtesting framework for strategy testing.
Supports order simulation, position tracking, P&L calculation, and performance metrics.
"""

import logging
from typing import Dict, List, Optional, Any, Callable
from datetime import datetime, timezone
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class OrderSide(Enum):
    """Order side enumeration."""
    BUY = "BUY"
    SELL = "SELL"


class OrderType(Enum):
    """Order type enumeration."""
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"


@dataclass
class SimulatedOrder:
    """Represents a simulated order in backtesting."""
    order_id: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: int
    price: float
    timestamp: datetime
    filled: bool = False
    fill_price: Optional[float] = None
    fill_time: Optional[datetime] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None


@dataclass
class SimulatedPosition:
    """Represents a simulated position in backtesting."""
    symbol: str
    quantity: int  # Positive for long, negative for short
    entry_price: float
    entry_time: datetime
    current_price: float
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    
    @property
    def unrealized_pnl(self) -> float:
        """Calculate unrealized P&L."""
        if self.quantity == 0:
            return 0.0
        price_diff = self.current_price - self.entry_price
        return price_diff * self.quantity
    
    @property
    def is_long(self) -> bool:
        """Check if position is long."""
        return self.quantity > 0
    
    @property
    def is_short(self) -> bool:
        """Check if position is short."""
        return self.quantity < 0


@dataclass
class Trade:
    """Represents a completed trade."""
    trade_id: str
    symbol: str
    entry_time: datetime
    exit_time: datetime
    entry_price: float
    exit_price: float
    quantity: int
    side: OrderSide
    pnl: float
    pnl_percent: float
    stop_loss_hit: bool = False
    take_profit_hit: bool = False


@dataclass
class BacktestResults:
    """Backtest results and performance metrics."""
    initial_capital: float
    final_capital: float
    total_pnl: float
    total_return: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    average_win: float
    average_loss: float
    profit_factor: float
    max_drawdown: float
    max_drawdown_percent: float
    sharpe_ratio: float
    sortino_ratio: float
    trades: List[Trade] = field(default_factory=list)
    equity_curve: List[Dict[str, Any]] = field(default_factory=list)


class BacktestingEngine:
    """
    Backtesting simulation engine for strategy testing.
    
    Simulates order execution, position tracking, and calculates performance metrics.
    """
    
    def __init__(
        self,
        symbol: str,
        initial_capital: float = 50000.0,
        slippage_ticks: int = 1,
        commission_per_contract: float = 0.0,
        point_value: Optional[float] = None,
        tick_size: Optional[float] = None
    ):
        """
        Initialize backtesting engine.
        
        Args:
            symbol: Trading symbol
            initial_capital: Starting capital
            slippage_ticks: Slippage in ticks (default: 1)
            commission_per_contract: Commission per contract (default: 0)
            point_value: Point value for P&L calculation (auto-detected if None)
            tick_size: Tick size for price rounding (auto-detected if None)
        """
        self.symbol = symbol.upper()
        self.initial_capital = initial_capital
        self.current_capital = initial_capital
        self.slippage_ticks = slippage_ticks
        self.commission_per_contract = commission_per_contract
        self.point_value = point_value or self._get_default_point_value()
        self.tick_size = tick_size or self._get_default_tick_size()
        
        # State tracking
        self.positions: Dict[str, SimulatedPosition] = {}
        self.orders: List[SimulatedOrder] = []
        self.trades: List[Trade] = []
        self.equity_curve: List[Dict[str, Any]] = []
        
        # Performance tracking
        self.peak_capital = initial_capital
        self.max_drawdown = 0.0
        self.max_drawdown_percent = 0.0
        
        logger.info(f"BacktestingEngine initialized for {symbol} with ${initial_capital:,.2f}")
    
    def _get_default_point_value(self) -> float:
        """Get default point value based on symbol."""
        defaults = {
            "MNQ": 2.0,
            "MES": 5.0,
            "MYM": 0.5,
            "M2K": 5.0,
            "ES": 50.0,
            "NQ": 20.0,
            "YM": 5.0,
        }
        return defaults.get(self.symbol, 1.0)
    
    def _get_default_tick_size(self) -> float:
        """Get default tick size based on symbol."""
        defaults = {
            "MNQ": 0.25,
            "MES": 0.25,
            "MYM": 1.0,
            "M2K": 0.25,
            "ES": 0.25,
            "NQ": 0.25,
            "YM": 1.0,
        }
        return defaults.get(self.symbol, 0.25)
    
    def place_order(
        self,
        side: str,
        quantity: int,
        order_type: str = "market",
        limit_price: Optional[float] = None,
        stop_price: Optional[float] = None,
        current_price: float = 0.0,
        timestamp: Optional[datetime] = None,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None
    ) -> SimulatedOrder:
        """
        Place a simulated order.
        
        Args:
            side: "BUY" or "SELL"
            quantity: Number of contracts
            order_type: "market", "limit", or "stop"
            limit_price: Limit price (for limit orders)
            stop_price: Stop price (for stop orders)
            current_price: Current market price
            timestamp: Order timestamp
            stop_loss: Stop loss price
            take_profit: Take profit price
            
        Returns:
            SimulatedOrder object
        """
        timestamp = timestamp or datetime.now(timezone.utc)
        order_id = f"order_{len(self.orders) + 1}_{timestamp.timestamp()}"
        
        # Determine fill price based on order type
        fill_price = None
        if order_type == "market":
            # Market order fills at current price with slippage
            slippage = self.slippage_ticks * self.tick_size
            if side.upper() == "BUY":
                fill_price = current_price + slippage  # Buy at ask (higher)
            else:
                fill_price = current_price - slippage  # Sell at bid (lower)
        elif order_type == "limit":
            fill_price = limit_price
        elif order_type == "stop":
            fill_price = stop_price
        
        order = SimulatedOrder(
            order_id=order_id,
            symbol=self.symbol,
            side=OrderSide.BUY if side.upper() == "BUY" else OrderSide.SELL,
            order_type=OrderType(order_type),
            quantity=quantity,
            price=fill_price or current_price,
            timestamp=timestamp,
            stop_loss=stop_loss,
            take_profit=take_profit
        )
        
        # For market orders, fill immediately
        if order_type == "market" and fill_price:
            order.filled = True
            order.fill_price = fill_price
            order.fill_time = timestamp
            self._execute_order(order, current_price)
        
        self.orders.append(order)
        return order
    
    def _execute_order(self, order: SimulatedOrder, current_price: float):
        """Execute a filled order and update positions."""
        symbol = order.symbol
        
        # Calculate commission
        commission = self.commission_per_contract * order.quantity
        self.current_capital -= commission
        
        # Update or create position
        if symbol not in self.positions:
            self.positions[symbol] = SimulatedPosition(
                symbol=symbol,
                quantity=0,
                entry_price=0.0,
                entry_time=order.fill_time or datetime.now(timezone.utc),
                current_price=current_price,
                stop_loss=order.stop_loss,
                take_profit=order.take_profit
            )
        
        position = self.positions[symbol]
        
        # Calculate quantity change
        quantity_change = order.quantity if order.side == OrderSide.BUY else -order.quantity
        
        # If closing or reversing position
        if (position.quantity > 0 and quantity_change < 0) or (position.quantity < 0 and quantity_change > 0):
            # Calculate P&L for closed portion
            closed_quantity = min(abs(position.quantity), abs(quantity_change))
            pnl = (order.fill_price - position.entry_price) * closed_quantity * self.point_value
            
            # Create trade record
            trade = Trade(
                trade_id=f"trade_{len(self.trades) + 1}",
                symbol=symbol,
                entry_time=position.entry_time,
                exit_time=order.fill_time or datetime.now(timezone.utc),
                entry_price=position.entry_price,
                exit_price=order.fill_price,
                quantity=closed_quantity,
                side=OrderSide.BUY if position.quantity > 0 else OrderSide.SELL,
                pnl=pnl,
                pnl_percent=(pnl / (position.entry_price * closed_quantity * self.point_value)) * 100
            )
            self.trades.append(trade)
            self.current_capital += pnl
            
            # Update position
            position.quantity += quantity_change
            if position.quantity == 0:
                # Position closed
                del self.positions[symbol]
            else:
                # Partial close or reversal
                position.entry_price = order.fill_price
                position.entry_time = order.fill_time or datetime.now(timezone.utc)
        else:
            # Opening or adding to position
            if position.quantity == 0:
                # New position
                position.entry_price = order.fill_price
                position.entry_time = order.fill_time or datetime.now(timezone.utc)
            
            position.quantity += quantity_change
            # Update average entry price
            total_cost = (position.entry_price * abs(position.quantity - quantity_change) * self.point_value) + \
                        (order.fill_price * abs(quantity_change) * self.point_value)
            position.entry_price = total_cost / (abs(position.quantity) * self.point_value)
        
        # Update stop loss and take profit
        if order.stop_loss:
            position.stop_loss = order.stop_loss
        if order.take_profit:
            position.take_profit = order.take_profit
    
    def update_price(self, price: float, timestamp: Optional[datetime] = None):
        """
        Update current price and check for stop loss/take profit hits.
        
        Args:
            price: Current market price
            timestamp: Price timestamp
        """
        timestamp = timestamp or datetime.now(timezone.utc)
        
        for symbol, position in list(self.positions.items()):
            position.current_price = price
            
            # Check stop loss
            if position.stop_loss:
                if position.is_long and price <= position.stop_loss:
                    # Stop loss hit on long
                    self._close_position(symbol, position.stop_loss, timestamp, stop_loss_hit=True)
                    continue
                elif position.is_short and price >= position.stop_loss:
                    # Stop loss hit on short
                    self._close_position(symbol, position.stop_loss, timestamp, stop_loss_hit=True)
                    continue
            
            # Check take profit
            if position.take_profit:
                if position.is_long and price >= position.take_profit:
                    # Take profit hit on long
                    self._close_position(symbol, position.take_profit, timestamp, take_profit_hit=True)
                    continue
                elif position.is_short and price <= position.take_profit:
                    # Take profit hit on short
                    self._close_position(symbol, position.take_profit, timestamp, take_profit_hit=True)
                    continue
            
            # Update equity curve
            unrealized_pnl = position.unrealized_pnl * self.point_value
            equity = self.current_capital + unrealized_pnl
            
            # Track drawdown
            if equity > self.peak_capital:
                self.peak_capital = equity
            
            drawdown = self.peak_capital - equity
            drawdown_percent = (drawdown / self.peak_capital) * 100 if self.peak_capital > 0 else 0
            
            if drawdown > self.max_drawdown:
                self.max_drawdown = drawdown
                self.max_drawdown_percent = drawdown_percent
            
            self.equity_curve.append({
                'timestamp': timestamp,
                'equity': equity,
                'drawdown': drawdown,
                'drawdown_percent': drawdown_percent
            })
    
    def _close_position(self, symbol: str, exit_price: float, timestamp: datetime, stop_loss_hit: bool = False, take_profit_hit: bool = False):
        """Close a position."""
        if symbol not in self.positions:
            return
        
        position = self.positions[symbol]
        
        # Calculate P&L
        pnl = (exit_price - position.entry_price) * position.quantity * self.point_value
        
        # Create trade record
        trade = Trade(
            trade_id=f"trade_{len(self.trades) + 1}",
            symbol=symbol,
            entry_time=position.entry_time,
            exit_time=timestamp,
            entry_price=position.entry_price,
            exit_price=exit_price,
            quantity=abs(position.quantity),
            side=OrderSide.BUY if position.is_long else OrderSide.SELL,
            pnl=pnl,
            pnl_percent=(pnl / (position.entry_price * abs(position.quantity) * self.point_value)) * 100,
            stop_loss_hit=stop_loss_hit,
            take_profit_hit=take_profit_hit
        )
        self.trades.append(trade)
        self.current_capital += pnl
        
        # Remove position
        del self.positions[symbol]
    
    def close_all_positions(self, current_price: float, timestamp: Optional[datetime] = None):
        """Close all open positions."""
        timestamp = timestamp or datetime.now(timezone.utc)
        for symbol in list(self.positions.keys()):
            self._close_position(symbol, current_price, timestamp)
    
    def get_results(self) -> BacktestResults:
        """
        Calculate and return backtest results.
        
        Returns:
            BacktestResults object with all performance metrics
        """
        # Close any remaining positions at final price
        if self.positions:
            final_price = self.equity_curve[-1]['equity'] / (sum(abs(p.quantity) for p in self.positions.values()) * self.point_value) if self.positions else 0
            self.close_all_positions(final_price)
        
        # Calculate metrics
        total_pnl = self.current_capital - self.initial_capital
        total_return = (total_pnl / self.initial_capital) * 100 if self.initial_capital > 0 else 0
        
        winning_trades = [t for t in self.trades if t.pnl > 0]
        losing_trades = [t for t in self.trades if t.pnl < 0]
        
        win_rate = (len(winning_trades) / len(self.trades) * 100) if self.trades else 0
        average_win = sum(t.pnl for t in winning_trades) / len(winning_trades) if winning_trades else 0
        average_loss = abs(sum(t.pnl for t in losing_trades) / len(losing_trades)) if losing_trades else 0
        profit_factor = (sum(t.pnl for t in winning_trades) / abs(sum(t.pnl for t in losing_trades))) if losing_trades else float('inf') if winning_trades else 0
        
        # Calculate Sharpe ratio (simplified)
        if len(self.equity_curve) > 1:
            returns = [(self.equity_curve[i]['equity'] - self.equity_curve[i-1]['equity']) / self.equity_curve[i-1]['equity'] 
                      for i in range(1, len(self.equity_curve))]
            if returns:
                import statistics
                mean_return = statistics.mean(returns)
                std_return = statistics.stdev(returns) if len(returns) > 1 else 0
                sharpe_ratio = (mean_return / std_return * (252 ** 0.5)) if std_return > 0 else 0
                sortino_ratio = sharpe_ratio  # Simplified (would need downside deviation)
            else:
                sharpe_ratio = 0
                sortino_ratio = 0
        else:
            sharpe_ratio = 0
            sortino_ratio = 0
        
        return BacktestResults(
            initial_capital=self.initial_capital,
            final_capital=self.current_capital,
            total_pnl=total_pnl,
            total_return=total_return,
            total_trades=len(self.trades),
            winning_trades=len(winning_trades),
            losing_trades=len(losing_trades),
            win_rate=win_rate,
            average_win=average_win,
            average_loss=average_loss,
            profit_factor=profit_factor,
            max_drawdown=self.max_drawdown,
            max_drawdown_percent=self.max_drawdown_percent,
            sharpe_ratio=sharpe_ratio,
            sortino_ratio=sortino_ratio,
            trades=self.trades,
            equity_curve=self.equity_curve
        )

