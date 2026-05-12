"""
Base Strategy Framework for Modular Trading Strategies

This module provides the foundation for creating pluggable trading strategies
that can be dynamically loaded and managed based on market conditions.
"""

import logging
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

logger = logging.getLogger(__name__)
from core.strategy_config import load_strategy_config


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
    
    # Per-instrument risk configuration (optional)
    risk_config: Optional[Dict[str, Dict[str, Any]]] = None
    # Format: {'SYMBOL': {'max_quantity': int, 'cooldown': float, 'max_pending': int}}
    
    @staticmethod
    def _parse_conditions(conditions_str: str) -> List[MarketCondition]:
        """
        Parse market conditions from comma-separated string.
        Returns empty list if string is empty or None.
        """
        if not conditions_str or not conditions_str.strip():
            return []
        
        result = []
        for condition in conditions_str.split(","):
            condition = condition.strip()
            if not condition:
                continue
            try:
                result.append(MarketCondition(condition))
            except ValueError:
                logger.warning(f"Invalid market condition '{condition}', skipping. Valid values: {[c.value for c in MarketCondition]}")
        
        return result
    
    @classmethod
    def from_env(cls, strategy_name: str) -> 'StrategyConfig':
        """Load strategy config from TOML + env + defaults."""
        # Keep legacy env prefix behavior (e.g. MEAN_REV_), but prefer the TOML schema:
        # - [meta].enabled / [meta].symbols
        # - [risk].position_size / [risk].max_positions / [risk].max_daily_trades / [risk].risk_per_trade_pct
        prefix = f"{strategy_name.upper()}_"
        cfg = load_strategy_config(strategy_name.lower(), env_prefix=prefix)

        return cls(
            name=strategy_name.lower(),
            enabled=cfg.get_bool("meta.enabled", False),
            symbols=[s.strip().upper() for s in cfg.get_list("meta.symbols", ["MNQ"])],
            max_positions=int(cfg.get_int("risk.max_positions", 2)),
            position_size=int(cfg.get_int("risk.position_size", 1)),
            risk_per_trade_percent=float(cfg.get_float("risk.risk_per_trade_pct", 0.5)),
            max_daily_trades=int(cfg.get_int("risk.max_daily_trades", 10)),
            preferred_conditions=cls._parse_conditions(cfg.get_str("preferred_conditions", "")),
            avoid_conditions=cls._parse_conditions(cfg.get_str("avoid_conditions", "")),
            trading_start_time=cfg.get_str("start_time", "09:30"),
            trading_end_time=cfg.get_str("end_time", "15:45"),
            no_trade_start=cfg.get_str("no_trade_start", "15:30"),
            no_trade_end=cfg.get_str("no_trade_end", "16:00"),
            respect_dll=cfg.get_bool("respect_dll", True),
            respect_mll=cfg.get_bool("respect_mll", True),
            max_dll_usage_percent=float(cfg.get_float("max_dll_usage", 0.75)),
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
        
        # Running state (some strategies use is_trading, some use is_running)
        # This provides a unified interface for strategy executors
        self.is_trading = False
        
        # Centralized risk manager (lazy initialization)
        self._risk_manager = None
        self._risk_config = getattr(config, 'risk_config', None)  # Per-instrument risk config
        
        logger.info(f"✨ Initialized {self.config.name} strategy")
    
    @property
    def is_running(self) -> bool:
        """
        Check if strategy is currently running.
        
        This provides a unified interface for checking strategy status.
        Checks multiple indicators: is_trading flag, ACTIVE status, etc.
        """
        return self.is_trading or self.status == StrategyStatus.ACTIVE
    
    @property
    def risk_manager(self):
        """Get or create the centralized risk manager."""
        if self._risk_manager is None:
            from core.risk_management import StrategyRiskManager
            self._risk_manager = StrategyRiskManager(self.trading_bot, risk_config=self._risk_config)
        return self._risk_manager
    
    def set_risk_config(self, risk_config: Optional[Dict[str, Dict[str, Any]]]):
        """
        Set per-instrument risk configuration.
        
        Args:
            risk_config: Dict mapping symbol to risk config, e.g.:
                {'MNQ': {'max_quantity': 10, 'cooldown': 60.0, 'max_pending': 1}}
        """
        self._risk_config = risk_config
        # Reset risk manager so it gets recreated with new config
        self._risk_manager = None
    
    def print_initialization_message(self, strategy_name: str, symbols: List[str], 
                                    strategy_details: Optional[List[str]] = None):
        """
        Print a standardized initialization message for all strategies.
        
        Args:
            strategy_name: Display name for the strategy
            symbols: List of symbols being traded
            strategy_details: Optional list of strategy-specific detail lines to display
        """
        print("\n" + "="*80)
        print(f"🎯 {strategy_name.upper()} - INITIAL CONFIGURATION")
        print("="*80)
        print(f"📊 Symbols: {', '.join(symbols)}")
        
        # Print strategy-specific details if provided
        if strategy_details:
            for detail in strategy_details:
                print(detail)
        
        # Print risk management configuration
        print("\n🛡️  RISK MANAGEMENT CONFIGURATION:")
        print("-"*80)
        
        # Get risk manager config (will be created if needed)
        risk_mgr = self.risk_manager
        has_per_instrument_config = bool(risk_mgr._risk_config)
        
        if has_per_instrument_config:
            print("📊 Per-Instrument Risk Limits:")
            for symbol in symbols:
                config = risk_mgr._get_config_for_symbol(symbol)
                print(f"  {symbol}: Max Qty={config['max_quantity']}, Cooldown={config['cooldown']}s, Max Pending={config['max_pending']}")
        else:
            # Show defaults
            default_config = risk_mgr._get_config_for_symbol(symbols[0] if symbols else "MNQ")
            print(f"📊 Default Risk Limits (applies to all symbols):")
            print(f"  Max Quantity: {default_config['max_quantity']} contracts")
            print(f"  Order Cooldown: {default_config['cooldown']}s")
            print(f"  Max Pending Orders: {default_config['max_pending']} per symbol/side")
        
        print("\n" + "="*80 + "\n")
    
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
        
        # Handle empty no_trade_start/end (means no restriction)
        if self.config.no_trade_start and self.config.no_trade_start.strip():
            no_trade_start_h, no_trade_start_m = map(int, self.config.no_trade_start.split(':'))
            no_trade_start = no_trade_start_h * 60 + no_trade_start_m
        else:
            no_trade_start = -1  # Disabled
        
        if self.config.no_trade_end and self.config.no_trade_end.strip():
            no_trade_end_h, no_trade_end_m = map(int, self.config.no_trade_end.split(':'))
            no_trade_end = no_trade_end_h * 60 + no_trade_end_m
        else:
            no_trade_end = -1  # Disabled
        
        start_time = start_hour * 60 + start_min
        end_time = end_hour * 60 + end_min
        
        # Check if in trading window
        if not (start_time <= current_time <= end_time):
            return False
        
        # Check if in no-trade window (only if configured)
        if no_trade_start >= 0 and no_trade_end >= 0:
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
    
    async def place_bracket_order(self, symbol: str, side: str, quantity: int,
                                  entry_price: float, stop_loss_price: float, 
                                  take_profit_price: float, enable_breakeven: bool = False,
                                  breakeven_profit_threshold: Optional[float] = None) -> Dict:
        """
        Place a bracket order using the verified working method (same as CLI stop_bracket command).
        
        This is the standard way for ALL strategies to place bracket orders.
        Uses place_oco_bracket_with_stop_entry which is proven to work reliably.
        
        **CENTRALIZED RISK MANAGEMENT**: All orders are automatically checked for:
        - Position quantity limits per symbol
        - Order cooldown periods
        - Pending order limits
        - Time-based restrictions
        
        Args:
            symbol: Trading symbol (e.g., "MNQ", "ES")
            side: "BUY" or "SELL"
            quantity: Number of contracts
            entry_price: Entry/stop price for the order
            stop_loss_price: Stop loss price
            take_profit_price: Take profit price
            enable_breakeven: Passed to broker OCO path (reserved / hybrid; default False).
            breakeven_profit_threshold: When > 0, registers bot-level generic breakeven
                (BONGO §1B): after this much favourable **price** move, SL is tightened
                toward ``entry_price``. Typically ``breakeven_trigger_r * |entry - stop|``.
        
        Returns:
            Dict with 'success' bool, 'orderId', 'method', and optional 'error'
        
        Example:
            ```python
            result = await self.place_bracket_order(
                symbol="MNQ",
                side="BUY",
                quantity=1,
                entry_price=25390.0,
                stop_loss_price=25380.0,
                take_profit_price=25400.0
            )
            if not result.get('error'):
                logger.info(f"✅ Order placed: {result.get('orderId')}")
            ```
        """
        logger.info(f"📝 {self.config.name}: Placing bracket order via verified path")
        logger.info(f"   {side} {quantity} {symbol} @ {entry_price:.2f}, SL={stop_loss_price:.2f}, TP={take_profit_price:.2f}")

        # Opt-in equity-tier sizing + daily halt (see core.income_brain, INCOME_BRAIN=true).
        bot = self.trading_bot
        if hasattr(bot, "income_brain_entry_quantity"):
            try:
                q_adj = int(bot.income_brain_entry_quantity(self.config.name, quantity))
            except Exception as exc:
                logger.warning(
                    "%s: income_brain_entry_quantity failed (%s) — using requested qty",
                    self.config.name,
                    exc,
                )
                q_adj = int(quantity)
            if q_adj <= 0:
                msg = "Income brain blocked entry (daily halt or zero size)"
                logger.warning("⚠️  %s: %s", self.config.name, msg)
                return {"success": False, "error": msg, "orderId": None}
            if q_adj != quantity:
                logger.info(
                    "📉 %s: income brain clamped quantity %s → %s",
                    self.config.name,
                    quantity,
                    q_adj,
                )
            quantity = q_adj
        
        # CENTRALIZED RISK MANAGEMENT: Check if order is allowed
        allowed, reason = await self.risk_manager.check_order_allowed(
            symbol=symbol,
            side=side,
            quantity=quantity
        )
        
        if not allowed:
            error_msg = f"Risk management blocked order: {reason}"
            logger.warning(f"⚠️  {self.config.name}: {error_msg}")
            return {
                "success": False,
                "error": error_msg,
                "orderId": None
            }
        
        # IMPORTANT: pass `strategy_name` so downstream (adapter/Rust/Python)
        # can attribute orders and send Discord notifications for strategy orders.
        result = await self.trading_bot.place_oco_bracket_with_stop_entry(
            symbol=symbol,
            side=side,
            quantity=quantity,
            entry_price=entry_price,
            stop_loss_price=stop_loss_price,
            take_profit_price=take_profit_price,
            enable_breakeven=enable_breakeven,
            strategy_name=self.config.name
        )
        
        if result.get("error"):
            logger.error(f"❌ {self.config.name}: Bracket order failed - {result.get('error')}")
        else:
            order_id = result.get('orderId')
            method = result.get('method', 'unknown')
            logger.info(f"✅ {self.config.name}: Bracket order placed - ID: {order_id}, Method: {method}")
            
            # Record successful order placement for cooldown tracking
            if order_id:
                await self.risk_manager.record_order_placement(symbol, side)
                if breakeven_profit_threshold is not None and hasattr(
                    bot, "register_generic_breakeven_watch"
                ):
                    try:
                        thr = float(breakeven_profit_threshold)
                    except (TypeError, ValueError):
                        thr = 0.0
                    if thr > 0:
                        bot.register_generic_breakeven_watch(
                            str(order_id),
                            symbol=symbol,
                            side=side,
                            entry_price=float(entry_price),
                            profit_threshold=thr,
                            strategy_name=self.config.name,
                        )
        
        return result
    
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

