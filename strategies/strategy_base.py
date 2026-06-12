"""
Base Strategy Framework for Modular Trading Strategies

This module provides the foundation for creating pluggable trading strategies
that can be dynamically loaded and managed based on market conditions.
"""

import asyncio
import logging
import os
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Tuple, Any, TYPE_CHECKING
from dataclasses import dataclass
from datetime import datetime, time
from enum import Enum

if TYPE_CHECKING:  # pragma: no cover — type-only import to keep runtime cost zero
    import pandas as pd

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


# Sentinel used by ``_resolve_live_breaker_enabled`` to distinguish
# "TOML omitted the key" from "TOML set it to False".  Both are valid
# states with different runtime semantics (env-default vs hard-off).
_LIVE_BREAKER_SENTINEL = object()


def _resolve_live_breaker_enabled(cfg) -> Optional[bool]:
    """Return per-strategy live-breaker flag or ``None`` for env-default."""
    raw = cfg.get("meta.live_breaker_enabled", _LIVE_BREAKER_SENTINEL)
    if raw is _LIVE_BREAKER_SENTINEL:
        return None
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        return raw.strip().lower() in ("1", "true", "yes", "on")
    return bool(raw)


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

    # Live consec-loss breaker bridge (off = process-wide
    # ``STRATEGY_LIVE_BREAKER`` env var decides).  Per-strategy TOML can
    # opt-in or opt-out via ``[meta].live_breaker_enabled`` regardless of
    # the env default — useful during early rollout when one strategy is
    # validated for live and others stay in shadow-only mode.
    live_breaker_enabled: Optional[bool] = None
    
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
            # ``cfg.get`` returns the sentinel default unchanged when the key
            # is absent, so we use a unique sentinel to distinguish
            # "missing in TOML" (→ ``None`` → bridge follows env default)
            # from "explicitly set to false in TOML" (→ ``False`` → never
            # bridge regardless of env).
            live_breaker_enabled=_resolve_live_breaker_enabled(cfg),
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

        # ── Live trade history (lazy) ────────────────────────────────────────
        # Per-symbol bounded buffer fed by ``EventType.TRADE_CLOSED`` events
        # published from ``core/user_hub_handlers.on_trade``. Read by
        # strategy-specific circuit breakers (e.g. consec-loss) when there is
        # no ``_replay_engine`` available (i.e. live mode).
        # The attribute is created on first ``record_trade_outcome`` call so
        # strategies that never use the breaker pay nothing for it.
        self._live_trade_history = None  # type: ignore[var-annotated]
        # Unsubscribe handle for the TRADE_CLOSED bus subscription (set when
        # ``start_live_trade_bridge`` is called by the strategy_manager).
        self._live_trade_bridge_unsub = None  # type: ignore[var-annotated]

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
    
    # ── Backtest performance hooks (opt-in) ─────────────────────────────────
    # ``replay_active_window_et``: when a subclass overrides this to return
    # one or more (start_et, end_et) ``datetime.time`` ranges, the
    # ``StrategyReplayEngine`` will SKIP ``analyze()`` for bars whose ET
    # timestamp falls outside ALL windows AND that have no open position
    # for any symbol. Used by ``StrategyReplayEngine.replay()`` in fast mode.
    #
    # Semantics:
    #   - Returns None → strategy is "always active"; engine never skips analyze.
    #   - Returns [] → also treated as "always active" (defensive, matches None).
    #   - Returns [(time(7,0), time(16,0))] → analyze() is called only on bars
    #     whose ET time is in [07:00, 16:00). Outside that, the engine still
    #     processes per-bar fills (TP/SL hits on existing positions) but the
    #     strategy callback is suppressed when no positions are open. When a
    #     position IS open the strategy is called regardless of window so it
    #     can manage exits (break-even, scratch, etc.).
    #   - Multiple windows allowed for split sessions (e.g. premarket build +
    #     RTH fade): returning [(time(7,0), time(8,0)), (time(8,0), time(16,0))]
    #     is equivalent to one merged [(7,16)] window.
    #
    # Why a class attribute and not a method: it's read once per replay (at
    # the top of the bar loop) so even a hot getattr is cheap. Use a property
    # if the window needs to be config-driven (see
    # ``MorningRangeReversionStrategy.replay_active_window_et``).
    #
    # NOTE: This is a backtest-only hint. Live mode never consults this
    # attribute. If you set it incorrectly you risk silently dropping signals
    # at the boundary — the parity test in
    # ``tests/test_backtest_active_window_parity.py`` is the regression net.
    replay_active_window_et: Optional[List[Tuple["time", "time"]]] = None

    def replay_precompute_indicators(self, df: "pd.DataFrame") -> None:
        """Optional hook to precompute indicators once before the bar loop.

        Called once by ``StrategyReplayEngine.replay()`` just before the bar
        iteration starts (after `_install_fast_strategy_mocks` in fast mode).
        The default is a no-op; strategies that compute the same indicator
        (e.g. ATR / EMA / session ranges) on every ``analyze()`` call can
        override this to vectorize the work, store the result on
        ``self.trading_bot._precomputed`` (or similar), and consume it from
        ``analyze()``.

        Args:
            df: The full OHLCV DataFrame for the replay (sorted, naive-UTC
                index, lowercase ``open/high/low/close/volume`` columns).
                Mutating this DataFrame is undefined behaviour — the engine
                may iterate it concurrently with strategy callbacks.

        The hook is **best-effort**: any exception is caught and logged by
        the engine, then the replay falls back to the per-bar path so a
        bad precompute can't tank a run.
        """
        return None

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

    # ── Live trade-history bridge (consumed by consec-loss / similar breakers) ──

    def record_trade_outcome(
        self,
        symbol: str,
        pnl: float,
        exit_time: Optional[datetime] = None,
        side: str = "",
        **extras: Any,
    ) -> None:
        """Append a completed trade to the strategy's live history buffer.

        Called by ``_on_trade_closed_event`` (live mode) and may also be
        called by tests.  Subclasses that maintain their own history can
        override; the default implementation simply appends to
        ``self._live_trade_history`` (constructed lazily).

        ``extras`` keys correspond to ``LiveTradeRecord`` fields
        (``trade_id``, ``quantity``, ``entry_price``, ``exit_price``,
        ``entry_time``, ``session_id``).
        """
        if not symbol:
            return
        try:
            pnl_f = float(pnl)
        except (TypeError, ValueError):
            return
        if self._live_trade_history is None:
            from core.live_trade_history import LiveTradeHistory
            self._live_trade_history = LiveTradeHistory()
        from core.live_trade_history import LiveTradeRecord
        rec = LiveTradeRecord(
            symbol=symbol,
            pnl=pnl_f,
            entry_time=extras.get("entry_time"),
            exit_time=exit_time,
            side=side or "",
            trade_id=extras.get("trade_id"),
            quantity=int(extras.get("quantity") or 0),
            entry_price=float(extras.get("entry_price") or 0.0),
            exit_price=float(extras.get("exit_price") or 0.0),
            session_id=extras.get("session_id"),
        )
        self._live_trade_history.record(rec)

    async def _on_trade_closed_event(self, event: Any) -> None:
        """``EventType.TRADE_CLOSED`` subscriber — forwards into history.

        Strategy filtering happens here: a strategy only records trades
        whose ``symbol`` is in ``self.config.symbols``.  This is a
        coarse-grained filter (two strategies trading the same symbol
        will both see the trade; the consec-loss breaker treats that as
        a regime signal, which is the safer-of-the-two failure mode).
        """
        try:
            data = getattr(event, "data", None) or {}
        except Exception:
            return
        sym = str(data.get("symbol") or "").upper()
        if not sym:
            return
        try:
            cfg_syms = {str(s).upper() for s in (self.config.symbols or [])}
        except Exception:
            cfg_syms = set()
        if cfg_syms and sym not in cfg_syms:
            return
        from core.live_trade_history import trade_record_from_event
        rec = trade_record_from_event(data)
        if rec is None:
            return
        self.record_trade_outcome(
            symbol=rec.symbol,
            pnl=rec.pnl,
            exit_time=rec.exit_time,
            side=rec.side,
            trade_id=rec.trade_id,
            quantity=rec.quantity,
            entry_price=rec.entry_price,
            exit_price=rec.exit_price,
            entry_time=rec.entry_time,
            session_id=rec.session_id,
        )

    async def start_live_trade_bridge(self) -> bool:
        """Subscribe to ``TRADE_CLOSED`` so live fills feed the breaker.

        Returns True when a subscription was established.  Idempotent: a
        second call is a no-op.  Called by ``StrategyManager.start_strategy``
        when both the bot's ``event_bus`` exists and the live breaker is
        enabled (env ``STRATEGY_LIVE_BREAKER=1`` or per-strategy override).
        """
        if self._live_trade_bridge_unsub is not None:
            return True
        bus = getattr(self.trading_bot, "event_bus", None)
        if bus is None:
            return False
        try:
            from core.events import EventType
            bus.subscribe(EventType.TRADE_CLOSED, self._on_trade_closed_event)
        except Exception as exc:
            logger.debug("Could not subscribe %s to TRADE_CLOSED: %s", self.config.name, exc)
            return False
        # ``EventBus.subscribe`` does not return an unsub handle; capture the
        # parameters needed to call ``bus.unsubscribe`` later as a closure.
        from core.events import EventType as _ET

        def _unsub() -> None:
            try:
                bus.unsubscribe(_ET.TRADE_CLOSED, self._on_trade_closed_event)
            except Exception as exc:  # noqa: BLE001
                logger.debug(
                    "Could not unsubscribe %s from TRADE_CLOSED: %s",
                    self.config.name, exc,
                )

        self._live_trade_bridge_unsub = _unsub
        logger.info("🔗 %s subscribed to TRADE_CLOSED for live consec-loss breaker", self.config.name)
        return True

    async def stop_live_trade_bridge(self) -> None:
        """Tear down the ``TRADE_CLOSED`` subscription if active."""
        unsub = self._live_trade_bridge_unsub
        self._live_trade_bridge_unsub = None
        if unsub is None:
            return
        try:
            if callable(unsub):
                unsub()
        except Exception as exc:
            logger.debug("Could not unsubscribe %s from TRADE_CLOSED: %s", self.config.name, exc)

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
                                  breakeven_profit_threshold: Optional[float] = None,
                                  breakeven_offset: float = 0.0,
                                  *,
                                  partial_tp_enabled: bool = False,
                                  partial_tp_scalp_r: float = 1.0) -> Dict:
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
            breakeven_offset: Optional offset (price points, ≥ 0) applied to the moved
                breakeven stop **in the trade's favour** — LONG snaps to ``entry +
                breakeven_offset``, SHORT to ``entry − breakeven_offset``. Used to
                lock in a couple of ticks above commission + slippage. Default 0
                preserves legacy snap-to-entry behaviour.
        
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

        # ── Data-feed health gate ─────────────────────────────────────────
        # 2026-06-11 post-mortem: bot placed an order with both SignalR hubs
        # zombied → had no idea it filled or stopped out.  We DO NOT block
        # the trade by default — the strategy's signal logic is the source
        # of truth, and the rest of the safety net (watchdog, place-and-
        # verify, cancel-on-staleness) ensures the bot can SEE and MANAGE
        # the trade even if SignalR is degraded.  This gate exists to
        # surface the degraded condition loudly so the operator knows.
        #
        # Modes (env: ``DATA_FEED_HEALTH_GATE_MODE``):
        #   * ``warn``   — log a WARNING and proceed (DEFAULT)
        #   * ``off``    — silent
        #   * ``refuse`` — log ERROR + return ``gated_health`` (legacy)
        try:
            import os as _os
            _gate_mode = _os.getenv("DATA_FEED_HEALTH_GATE_MODE", "warn").lower()
            # Back-compat with the original env var: ``DATA_FEED_HEALTH_GATE=false`` → off.
            if _os.getenv("DATA_FEED_HEALTH_GATE", "true").lower() in ("false", "0", "no"):
                _gate_mode = "off"
            if _gate_mode != "off":
                from core.data_feed_health import get_monitor as _get_health_monitor
                _verdict = _get_health_monitor().is_safe_to_trade(symbol)
                if not _verdict.ok:
                    if _gate_mode == "refuse":
                        logger.error(
                            "🛑 %s: REFUSING to place %s %s order — data feed unhealthy: %s",
                            self.config.name, side, symbol, _verdict.reason,
                        )
                        return {
                            "error": f"data feed unhealthy: {_verdict.reason}",
                            "orderId": None,
                            "method": "gated_health",
                        }
                    # Default: warn and proceed.  The watchdog + verifier
                    # + cancel-on-staleness collectively keep us safe.
                    logger.warning(
                        "⚠️  %s: data feed degraded but placing %s %s anyway "
                        "(strategy logic is source of truth): %s",
                        self.config.name, side, symbol, _verdict.reason,
                    )
        except Exception as _exc:
            # Never let the gate ITSELF block a trade due to a bug.
            logger.warning(
                "%s: health-gate check raised %s — proceeding with order",
                self.config.name, type(_exc).__name__,
            )

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
        
        # ── Per-trade $ loss cap + adaptive sizing ─────────────────────────
        # Hard ceiling on $-at-risk independent of the strategy's internal
        # sizing logic.  When the SL distance would push the trade above the
        # cap, contracts get trimmed until risk ≤ cap (min 1 ct unless
        # ``MAX_DOLLAR_RISK_STRICT=true``).  Default DISABLED (0 = no cap).
        #
        # Config precedence (highest first):
        #   1. ``self.config.params['max_dollar_risk_per_trade']`` — per-strategy TOML
        #   2. ``MAX_DOLLAR_RISK_PER_TRADE`` env var — global default
        try:
            import os as _os
            from core.risk_sizer import cap_quantity_by_dollar_risk
            _cap = 0.0
            _params = getattr(self.config, "params", None) or {}
            if isinstance(_params, dict):
                _cap = float(_params.get("max_dollar_risk_per_trade", 0) or 0)
            if _cap <= 0:
                _cap = float(_os.getenv("MAX_DOLLAR_RISK_PER_TRADE", "0") or 0)
            if _cap > 0:
                _strict = _os.getenv("MAX_DOLLAR_RISK_STRICT", "false").lower() in ("true", "1", "yes")
                _adj, _why = cap_quantity_by_dollar_risk(
                    symbol=symbol,
                    entry_price=float(entry_price),
                    stop_loss_price=float(stop_loss_price),
                    requested_quantity=int(quantity),
                    max_dollar_risk=_cap,
                    min_quantity=1,
                    strict=_strict,
                )
                if _adj == 0:
                    err = f"per-trade $ cap exceeded: {_why}"
                    logger.warning("⚠️  %s: %s", self.config.name, err)
                    return {"success": False, "error": err, "orderId": None}
                if _adj != int(quantity):
                    logger.info(
                        "💵 %s: $-risk cap adjusted quantity %d → %d (%s)",
                        self.config.name, int(quantity), _adj, _why,
                    )
                    quantity = _adj
        except Exception as _exc:
            logger.warning(
                "%s: $-cap sizer raised %s — using strategy-requested quantity",
                self.config.name, type(_exc).__name__,
            )

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
        
        # ── E2: Fire-and-forget order POST (opt-in) ──────────────────────────────────
        # When ``STRATEGY_FIRE_AND_FORGET_ORDERS=true``, dispatch the broker call as a
        # background task and return immediately with ``{"success": True, "orderId": None,
        # "fire_and_forget": True}``. The strategy's loop can iterate while the broker
        # round-trip completes in parallel. Tradeoffs:
        #   • The order ID is unavailable to the synchronous caller. Downstream features
        #     that need it (breakeven monitor, BONGO §1B, breakout_active_orders cache)
        #     skip registration when the result has ``fire_and_forget=True``. They still
        #     work normally when the toggle is off (default).
        #   • Order rejections arrive asynchronously via the User Hub instead of through
        #     the synchronous ``error`` field. The background task logs them.
        #   • The risk manager has already approved the size synchronously above; F&F
        #     does not bypass risk checks.
        # This is EXPERIMENTAL — keep off until you've validated the live path.
        ff_enabled = os.environ.get("STRATEGY_FIRE_AND_FORGET_ORDERS", "false").strip().lower() in (
            "1", "true", "yes", "on"
        )

        async def _do_place() -> Dict:
            if (
                partial_tp_enabled
                and int(quantity) >= 2
                and hasattr(bot, "place_oco_bracket_with_stop_entry_partial_tp")
            ):
                return await bot.place_oco_bracket_with_stop_entry_partial_tp(
                    symbol=symbol,
                    side=side,
                    quantity=quantity,
                    entry_price=entry_price,
                    stop_loss_price=stop_loss_price,
                    take_profit_full_price=take_profit_price,
                    enable_breakeven=enable_breakeven,
                    strategy_name=self.config.name,
                    scalp_r_multiple=float(partial_tp_scalp_r or 1.0),
                )
            return await bot.place_oco_bracket_with_stop_entry(
                symbol=symbol,
                side=side,
                quantity=quantity,
                entry_price=entry_price,
                stop_loss_price=stop_loss_price,
                take_profit_price=take_profit_price,
                enable_breakeven=enable_breakeven,
                strategy_name=self.config.name,
            )

        if ff_enabled:
            async def _bg_place() -> None:
                try:
                    res = await _do_place()
                    if res and res.get("error"):
                        logger.error(
                            "❌ %s: F&F bracket POST failed asynchronously - %s",
                            self.config.name, res.get("error"),
                        )
                    else:
                        logger.info(
                            "✅ %s: F&F bracket POST resolved - ID: %s",
                            self.config.name, (res or {}).get("orderId"),
                        )
                except Exception as exc:
                    logger.error("❌ %s: F&F bracket POST raised: %s", self.config.name, exc)

            asyncio.create_task(_bg_place())
            logger.info(
                "📨 %s: F&F bracket dispatched (orderId resolves async via User Hub)",
                self.config.name,
            )
            return {
                "success": True,
                "orderId": None,
                "method": "fire_and_forget",
                "fire_and_forget": True,
            }

        result = await _do_place()
        
        if result.get("error"):
            logger.error(f"❌ {self.config.name}: Bracket order failed - {result.get('error')}")
        else:
            order_id = result.get('orderId')
            method = result.get('method', 'unknown')
            logger.info(f"✅ {self.config.name}: Bracket order placed - ID: {order_id}, Method: {method}")

            # Mark the moment on the health monitor + schedule REST verification.
            # Both are no-ops in backtest/replay (monitor is paused or unused).
            try:
                from core.data_feed_health import get_monitor as _get_health_monitor
                from core.order_verifier import schedule_order_verification
                from core.working_order_registry import get_registry as _get_wo_registry
                _monitor = _get_health_monitor()
                _monitor.record_order_placed()
                # Register the entry order so the data-feed watchdog can
                # cancel-on-staleness if SignalR goes zombie AFTER the place.
                _acct_id = (
                    getattr(bot, "account_id", None)
                    or (bot.selected_account or {}).get("id") if getattr(bot, "selected_account", None) else None
                )
                if order_id:
                    _siblings = []
                    for k in ("stopOrderId", "limitOrderId", "stopLossOrderId", "takeProfitOrderId"):
                        v = result.get(k)
                        if v:
                            _siblings.append(str(v))
                    _get_wo_registry().register(
                        order_id=str(order_id),
                        account_id=str(_acct_id) if _acct_id else "",
                        symbol=symbol,
                        side=side,
                        strategy_name=self.config.name,
                        oco_sibling_ids=_siblings,
                    )
                if order_id and getattr(bot, "broker_adapter", None) is not None:
                    schedule_order_verification(
                        order_id=str(order_id),
                        account_id=_acct_id,
                        broker_adapter=bot.broker_adapter,
                        monitor=_monitor,
                        strategy_name=self.config.name,
                        symbol=symbol,
                    )
            except Exception as _exc:
                logger.debug(
                    "%s: post-place verification scheduling skipped: %s",
                    self.config.name, _exc,
                )

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
                    try:
                        be_offset = max(0.0, float(breakeven_offset or 0.0))
                    except (TypeError, ValueError):
                        be_offset = 0.0
                    if thr > 0:
                        bot.register_generic_breakeven_watch(
                            str(order_id),
                            symbol=symbol,
                            side=side,
                            entry_price=float(entry_price),
                            profit_threshold=thr,
                            breakeven_offset=be_offset,
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

