"""
Base Strategy Framework for Modular Trading Strategies

This module provides the foundation for creating pluggable trading strategies
that can be dynamically loaded and managed based on market conditions.
"""

import os
import logging
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

logger = logging.getLogger(__name__)


class MarketCondition(Enum):
    """Market condition classifications."""
    TRENDING_UP = "trending_up"
    TRENDING_DOWN = "trending_down"
    RANGING = "ranging"
    HIGH_VOLATILITY = "high_volatility"
    LOW_VOLATILITY = "low_volatility"
    BREAKOUT = "breakout"
    REVERSAL = "reversal"
    UNKNOWN = "unknown"


class StrategyStatus(Enum):
    """Strategy execution status."""
    IDLE = "idle"
    ACTIVE = "active"
    PAUSED = "paused"
    ERROR = "error"


@dataclass
class StrategyConfig:
    """Base configuration for all strategies."""
    name: str
    enabled: bool
    symbols: List[str]
    max_positions: int
    position_size: int
    risk_per_trade_percent: float
    max_daily_trades: int
    
    # Market condition filters
    preferred_conditions: List[MarketCondition]
    avoid_conditions: List[MarketCondition]
    
    # Time filters
    trading_start_time: str  # "09:30"
    trading_end_time: str    # "15:45"
    no_trade_start: str      # "15:30"
    no_trade_end: str        # "16:00"
    
    # TopStepX compliance
    respect_dll: bool = True
    respect_mll: bool = True
    max_dll_usage_percent: float = 0.75  # Use max 75% of DLL
    
    @classmethod
    def from_env(cls, strategy_name: str) -> 'StrategyConfig':
        """Load strategy config from environment variables."""
        prefix = f"{strategy_name.upper()}_"
        
        return cls(
            name=strategy_name,
            enabled=os.getenv(f"{prefix}ENABLED", "false").lower() == "true",
            symbols=os.getenv(f"{prefix}SYMBOLS", "MNQ").split(","),
            max_positions=int(os.getenv(f"{prefix}MAX_POSITIONS", "2")),
            position_size=int(os.getenv(f"{prefix}POSITION_SIZE", "1")),
            risk_per_trade_percent=float(os.getenv(f"{prefix}RISK_PERCENT", "0.5")),
            max_daily_trades=int(os.getenv(f"{prefix}MAX_DAILY_TRADES", "10")),
            preferred_conditions=[
                MarketCondition(c.strip()) for c in 
                os.getenv(f"{prefix}PREFERRED_CONDITIONS", "breakout,trending").split(",")
            ],
            avoid_conditions=[
                MarketCondition(c.strip()) for c in
                os.getenv(f"{prefix}AVOID_CONDITIONS", "high_volatility").split(",")
            ],
            trading_start_time=os.getenv(f"{prefix}START_TIME", "09:30"),
            trading_end_time=os.getenv(f"{prefix}END_TIME", "15:45"),
            no_trade_start=os.getenv(f"{prefix}NO_TRADE_START", "15:30"),
            no_trade_end=os.getenv(f"{prefix}NO_TRADE_END", "16:00"),
            respect_dll=os.getenv(f"{prefix}RESPECT_DLL", "true").lower() == "true",
            respect_mll=os.getenv(f"{prefix}RESPECT_MLL", "true").lower() == "true",
            max_dll_usage_percent=float(os.getenv(f"{prefix}MAX_DLL_USAGE", "0.75"))
        )


@dataclass
class StrategyMetrics:
    """Performance metrics for a strategy."""
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    total_pnl: float = 0.0
    best_trade: float = 0.0
    worst_trade: float = 0.0
    win_rate: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    profit_factor: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    
    # TopStepX specific
    daily_pnl: float = 0.0
    best_day_pnl: float = 0.0
    consistency_ratio: float = 0.0
    dll_violations: int = 0
    mll_violations: int = 0
    
    def update(self, trade_pnl: float):
        """Update metrics with new trade result."""
        self.total_trades += 1
        self.total_pnl += trade_pnl
        
        if trade_pnl > 0:
            self.winning_trades += 1
            self.best_trade = max(self.best_trade, trade_pnl)
        else:
            self.losing_trades += 1
            self.worst_trade = min(self.worst_trade, trade_pnl)
        
        # Recalculate derived metrics
        if self.total_trades > 0:
            self.win_rate = self.winning_trades / self.total_trades
        
        if self.winning_trades > 0:
            self.avg_win = sum([t for t in self._trade_history if t > 0]) / self.winning_trades
        
        if self.losing_trades > 0:
            self.avg_loss = sum([t for t in self._trade_history if t < 0]) / self.losing_trades
        
        if self.avg_loss != 0:
            self.profit_factor = abs(self.avg_win * self.winning_trades / (self.avg_loss * self.losing_trades))


class BaseStrategy(ABC):
    """
    Abstract base class for all trading strategies.
    
    All strategies must implement:
    - analyze(): Analyze market and generate signals
    - execute(): Execute trades based on signals
    - manage_positions(): Manage open positions
    - cleanup(): Clean up resources
    """
    
    def __init__(self, trading_bot, config: StrategyConfig):
        """
        Initialize strategy.
        
        Args:
            trading_bot: Reference to main TradingBot instance
            config: Strategy configuration
        """
        self.trading_bot = trading_bot
        self.config = config
        self.status = StrategyStatus.IDLE
        self.metrics = StrategyMetrics()
        
        # State tracking
        self.active_positions: Dict[str, Dict] = {}
        self.pending_orders: Dict[str, Dict] = {}
        self.daily_trades: int = 0
        self.last_trade_time: Dict[str, datetime] = {}
        
        logger.info(f"✨ Initialized {self.config.name} strategy")
    
    @abstractmethod
    async def analyze(self, symbol: str) -> Optional[Dict]:
        """
        Analyze market conditions and generate trading signals.
        
        Args:
            symbol: Trading symbol to analyze
        
        Returns:
            Dict with signal data or None if no signal
            {
                "action": "LONG" | "SHORT" | "CLOSE",
                "symbol": str,
                "entry_price": float,
                "stop_loss": float,
                "take_profit": float,
                "confidence": float (0.0-1.0),
                "reason": str
            }
        """
        pass
    
    @abstractmethod
    async def execute(self, signal: Dict) -> bool:
        """
        Execute a trading signal.
        
        Args:
            signal: Signal dictionary from analyze()
        
        Returns:
            bool: True if execution successful
        """
        pass
    
    @abstractmethod
    async def manage_positions(self):
        """
        Manage open positions (breakeven, trailing stops, etc.).
        """
        pass
    
    @abstractmethod
    async def cleanup(self):
        """
        Clean up strategy resources.
        """
        pass
    
    # Common utility methods all strategies can use
    
    def get_market_condition(self, symbol: str) -> MarketCondition:
        """
        Determine current market condition for symbol.
        
        Override this in specific strategies for custom logic.
        """
        # Default implementation - can be overridden
        return MarketCondition.UNKNOWN
    
    def should_trade(self, symbol: str) -> Tuple[bool, str]:
        """
        Check if strategy should trade based on all filters.
        
        Returns:
            (should_trade: bool, reason: str)
        """
        # Check if enabled
        if not self.config.enabled:
            return False, "Strategy disabled"
        
        # Check daily trade limit
        if self.daily_trades >= self.config.max_daily_trades:
            return False, f"Daily trade limit reached ({self.daily_trades}/{self.config.max_daily_trades})"
        
        # Check max positions
        if len(self.active_positions) >= self.config.max_positions:
            return False, f"Max positions reached ({len(self.active_positions)}/{self.config.max_positions})"
        
        # Check time window
        if not self._in_trading_window():
            return False, "Outside trading hours"
        
        # Check market condition
        market_condition = self.get_market_condition(symbol)
        if market_condition in self.config.avoid_conditions:
            return False, f"Avoiding market condition: {market_condition.value}"
        
        # Check TopStepX compliance
        if self.config.respect_dll:
            dll_check, dll_reason = self._check_dll_compliance()
            if not dll_check:
                return False, dll_reason
        
        if self.config.respect_mll:
            mll_check, mll_reason = self._check_mll_compliance()
            if not mll_check:
                return False, mll_reason
        
        return True, "All checks passed"
    
    def _in_trading_window(self) -> bool:
        """Check if current time is within trading window."""
        now = datetime.now()
        current_time = now.hour * 60 + now.minute
        
        start_hour, start_min = map(int, self.config.trading_start_time.split(':'))
        end_hour, end_min = map(int, self.config.trading_end_time.split(':'))
        no_trade_start_h, no_trade_start_m = map(int, self.config.no_trade_start.split(':'))
        no_trade_end_h, no_trade_end_m = map(int, self.config.no_trade_end.split(':'))
        
        start_time = start_hour * 60 + start_min
        end_time = end_hour * 60 + end_min
        no_trade_start = no_trade_start_h * 60 + no_trade_start_m
        no_trade_end = no_trade_end_h * 60 + no_trade_end_m
        
        # Check if in trading window
        if not (start_time <= current_time <= end_time):
            return False
        
        # Check if in no-trade window
        if no_trade_start <= current_time <= no_trade_end:
            return False
        
        return True
    
    def _check_dll_compliance(self) -> Tuple[bool, str]:
        """Check Daily Loss Limit compliance."""
        if not hasattr(self.trading_bot, 'account_tracker'):
            return True, "Account tracker not available"
        
        tracker = self.trading_bot.account_tracker
        current_daily_pnl = tracker.get_daily_pnl()
        dll = tracker.daily_loss_limit
        
        if current_daily_pnl < 0:
            dll_usage = abs(current_daily_pnl) / dll
            max_usage = self.config.max_dll_usage_percent
            
            if dll_usage >= max_usage:
                return False, f"DLL usage {dll_usage:.1%} >= {max_usage:.1%} limit"
        
        return True, "DLL compliant"
    
    def _check_mll_compliance(self) -> Tuple[bool, str]:
        """Check Maximum Loss Limit compliance."""
        if not hasattr(self.trading_bot, 'account_tracker'):
            return True, "Account tracker not available"
        
        tracker = self.trading_bot.account_tracker
        current_balance = tracker.current_balance
        mll_threshold = tracker.highest_EOD_balance - tracker.maximum_loss_limit
        
        # Check if close to threshold (within 10%)
        buffer = (tracker.maximum_loss_limit * 0.10)
        if current_balance < (mll_threshold + buffer):
            return False, f"Too close to MLL threshold (${current_balance:.2f} vs ${mll_threshold:.2f})"
        
        return True, "MLL compliant"
    
    def calculate_position_size(self, symbol: str, entry_price: float, stop_price: float) -> int:
        """
        Calculate position size based on risk management rules.
        
        Args:
            symbol: Trading symbol
            entry_price: Proposed entry price
            stop_price: Proposed stop loss price
        
        Returns:
            int: Number of contracts to trade
        """
        # Get account balance
        if hasattr(self.trading_bot, 'account_tracker'):
            account_balance = self.trading_bot.account_tracker.current_balance
        else:
            account_balance = 150000  # Default
        
        # Calculate risk per trade in dollars
        risk_dollars = account_balance * (self.config.risk_per_trade_percent / 100)
        
        # Get point value for symbol
        point_value = self._get_point_value(symbol)
        
        # Calculate price difference
        price_diff = abs(entry_price - stop_price)
        
        # Calculate position size
        if price_diff > 0 and point_value > 0:
            contracts = int(risk_dollars / (price_diff * point_value))
        else:
            contracts = self.config.position_size
        
        # Cap at configured position size
        contracts = min(contracts, self.config.position_size, 10)
        contracts = max(contracts, 1)
        
        return contracts
    
    def _get_point_value(self, symbol: str) -> float:
        """Get point value for symbol."""
        point_values = {
            'MNQ': 2.0, 'NQ': 20.0,
            'MES': 5.0, 'ES': 50.0,
            'MYM': 0.5, 'YM': 5.0,
            'M2K': 5.0, 'RTY': 50.0,
        }
        return point_values.get(symbol.upper(), 2.0)
    
    def log_trade(self, trade_data: Dict):
        """Log trade for metrics tracking."""
        pnl = trade_data.get('pnl', 0.0)
        self.metrics.update(pnl)
        self.daily_trades += 1
        
        logger.info(f"📊 {self.config.name} Trade Logged: PnL={pnl:.2f}, Total Trades={self.metrics.total_trades}, Win Rate={self.metrics.win_rate:.1%}")
    
    def get_status(self) -> Dict:
        """Get strategy status and metrics."""
        return {
            "name": self.config.name,
            "status": self.status.value,
            "enabled": self.config.enabled,
            "symbols": self.config.symbols,
            "active_positions": len(self.active_positions),
            "daily_trades": self.daily_trades,
            "metrics": {
                "total_trades": self.metrics.total_trades,
                "win_rate": f"{self.metrics.win_rate:.1%}",
                "total_pnl": f"${self.metrics.total_pnl:.2f}",
                "profit_factor": f"{self.metrics.profit_factor:.2f}",
                "best_trade": f"${self.metrics.best_trade:.2f}",
                "worst_trade": f"${self.metrics.worst_trade:.2f}"
            }
        }

