"""
Event System for Trading Bot

Provides event types and event classes for the event-driven architecture.
Allows components to react to events without tight coupling.
"""

from enum import Enum
from dataclasses import dataclass, field
from typing import Any, Dict, Optional
from datetime import datetime, timezone


class EventType(Enum):
    """All possible system events."""
    # Order events
    ORDER_PLACED = "order_placed"
    ORDER_FILLED = "order_filled"
    ORDER_CANCELLED = "order_cancelled"
    ORDER_REJECTED = "order_rejected"
    ORDER_UPDATED = "order_updated"
    
    # Position events
    POSITION_OPENED = "position_opened"
    POSITION_CLOSED = "position_closed"
    POSITION_UPDATED = "position_updated"

    # Trade events (entry + exit pair, with realized PnL).
    # Distinct from ``POSITION_CLOSED`` because the latter fires whenever a
    # position's qty drops to zero (could be a partial close stack); ``TRADE_CLOSED``
    # is the per-(entry,exit) Trade record from ``SessionTradeTracker.process_fill``
    # carrying ``symbol`` + ``net_pnl`` + ``side`` + entry/exit timestamps.
    # Strategies that need to react to their own realised PnL (e.g.
    # consec-loss breaker) subscribe to this rather than building a fill diff
    # manually.
    TRADE_CLOSED = "trade_closed"
    
    # Account events
    ACCOUNT_UPDATED = "account_updated"
    BALANCE_CHANGED = "balance_changed"
    PNL_UPDATED = "pnl_updated"
    
    # Market events
    QUOTE_UPDATED = "quote_updated"
    BAR_COMPLETED = "bar_completed"
    
    # Strategy events
    STRATEGY_STARTED = "strategy_started"
    STRATEGY_STOPPED = "strategy_stopped"
    SIGNAL_GENERATED = "signal_generated"
    STRATEGY_CONFIG_RELOADED = "strategy_config_reloaded"

    # Regime / portfolio events.
    # ``REGIME_UPDATE`` is emitted by ``core.regime`` consumers when the
    # market regime label changes (trend ↔ mixed ↔ chop).  ``PORTFOLIO_KILL``
    # is emitted by ``core.portfolio_daily_breaker`` when total realised PnL
    # for the current ET session crosses the operator's daily cap; the
    # subscriber is ``StrategyManager``, which flat-files every open
    # position and disables all strategies until the next session
    # rollover (18:00 ET futures session reset).
    REGIME_UPDATE = "regime_update"
    PORTFOLIO_KILL = "portfolio_kill"

    # GUI events
    GUI_REFRESH_REQUESTED = "gui_refresh_requested"
    CHART_SYMBOL_CHANGED = "chart_symbol_changed"
    CHART_TIMEFRAME_CHANGED = "chart_timeframe_changed"
    
    # System events
    SHUTDOWN_REQUESTED = "shutdown_requested"


@dataclass(slots=True)
class Event:
    """
    System event with metadata.
    
    Attributes:
        type: Type of event (from EventType enum)
        data: Event-specific data
        timestamp: When the event occurred (UTC)
        source: Component that generated the event
    """
    type: EventType
    data: Dict[str, Any]
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    source: str = "system"
    
    def __str__(self) -> str:
        """String representation for logging."""
        return f"Event({self.type.value}, source={self.source}, data_keys={list(self.data.keys())})"
    
    def __repr__(self) -> str:
        """Detailed representation."""
        return f"Event(type={self.type.value}, source={self.source}, timestamp={self.timestamp.isoformat()}, data={self.data})"
