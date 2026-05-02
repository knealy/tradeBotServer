"""
Data models for backtesting

Defines structures for trades, positions, and backtest results.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any, Tuple
from enum import Enum


def _iso_timestamp(ts: Any) -> str:
    """Serialize bar/order timestamps for JSON (UTC ``Z`` when naive)."""
    if ts is None:
        return ""
    if isinstance(ts, datetime):
        dt = ts
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
        return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    # pandas.Timestamp, numpy.datetime64, etc.
    if hasattr(ts, "to_pydatetime"):
        try:
            return _iso_timestamp(ts.to_pydatetime())
        except Exception:
            pass
    if hasattr(ts, "isoformat"):
        return str(ts.isoformat())
    return str(ts)


class OrderSide(Enum):
    """Order side enum."""
    BUY = "BUY"
    SELL = "SELL"


class OrderType(Enum):
    """Order type enum."""
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"


class OrderStatus(Enum):
    """Order status enum."""
    PENDING = "pending"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


@dataclass
class BacktestOrder:
    """Represents an order in backtest."""
    order_id: str
    timestamp: datetime
    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: int
    price: Optional[float] = None  # Entry price
    stop_price: Optional[float] = None  # For stop orders
    limit_price: Optional[float] = None  # For limit orders
    filled_price: Optional[float] = None  # Actual fill price
    filled_timestamp: Optional[datetime] = None
    status: OrderStatus = OrderStatus.PENDING
    slippage: float = 0.0  # Applied slippage
    commission: float = 0.0  # Commission paid


@dataclass
class BacktestTrade:
    """Represents a completed trade (entry + exit)."""
    trade_id: str
    symbol: str
    side: OrderSide
    entry_time: datetime
    exit_time: datetime
    entry_price: float
    exit_price: float
    quantity: int
    pnl: float  # Realized P&L
    pnl_percent: float  # P&L as percentage
    commission: float  # Total commission
    slippage: float  # Total slippage
    bars_held: int  # Duration in bars
    exit_reason: str  # "stop_loss", "take_profit", "signal", "timeout"
    max_favorable_excursion: float = 0.0  # MFE
    max_adverse_excursion: float = 0.0  # MAE

    def to_json_dict(self) -> Dict[str, Any]:
        side_val = self.side.value if isinstance(self.side, OrderSide) else str(self.side)
        return {
            "trade_id": self.trade_id,
            "symbol": self.symbol,
            "side": side_val,
            "entry_time": _iso_timestamp(self.entry_time),
            "exit_time": _iso_timestamp(self.exit_time),
            "entry_price": self.entry_price,
            "exit_price": self.exit_price,
            "quantity": self.quantity,
            "pnl": self.pnl,
            "pnl_percent": self.pnl_percent,
            "commission": self.commission,
            "slippage": self.slippage,
            "bars_held": self.bars_held,
            "exit_reason": self.exit_reason,
            "max_favorable_excursion": self.max_favorable_excursion,
            "max_adverse_excursion": self.max_adverse_excursion,
        }


@dataclass
class BacktestPosition:
    """Represents an open position in backtest."""
    symbol: str
    side: OrderSide
    quantity: int
    entry_price: float
    entry_time: datetime
    current_price: float
    entry_bar_index: int = 0  # Bar index when position was opened
    unrealized_pnl: float = 0.0
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    trailing_stop: Optional[float] = None
    max_favorable_excursion: float = 0.0
    max_adverse_excursion: float = 0.0


@dataclass
class BacktestResult:
    """Complete backtest results."""
    symbol: str
    strategy_name: str
    start_date: datetime
    end_date: datetime
    initial_capital: float
    final_capital: float
    
    # Trade statistics
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    
    # P&L statistics
    total_pnl: float = 0.0
    total_return_pct: float = 0.0
    average_win: float = 0.0
    average_loss: float = 0.0
    largest_win: float = 0.0
    largest_loss: float = 0.0
    
    # Risk metrics
    max_drawdown: float = 0.0
    max_drawdown_pct: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    profit_factor: float = 0.0
    expectancy: float = 0.0
    
    # Execution metrics
    total_commission: float = 0.0
    total_slippage: float = 0.0
    average_bars_held: float = 0.0
    
    # Detailed data
    trades: List[BacktestTrade] = field(default_factory=list)
    equity_curve: List[Tuple[datetime, float]] = field(default_factory=list)
    drawdown_curve: List[Tuple[datetime, float]] = field(default_factory=list)
    
    def to_dict(self, include_trades: bool = False) -> Dict[str, Any]:
        """Convert result to dictionary. Optionally append completed round-trips."""
        out: Dict[str, Any] = {
            'symbol': self.symbol,
            'strategy': self.strategy_name,
            'period': f"{self.start_date.date()} to {self.end_date.date()}",
            'initial_capital': self.initial_capital,
            'final_capital': self.final_capital,
            'total_pnl': self.total_pnl,
            'total_return_pct': self.total_return_pct,
            'total_trades': self.total_trades,
            'win_rate': self.win_rate,
            'profit_factor': self.profit_factor,
            'sharpe_ratio': self.sharpe_ratio,
            'max_drawdown': self.max_drawdown,
            'max_drawdown_pct': self.max_drawdown_pct,
            'average_win': self.average_win,
            'average_loss': self.average_loss,
            'expectancy': self.expectancy
        }
        if include_trades:
            out['trades'] = [t.to_json_dict() for t in self.trades]
        return out
