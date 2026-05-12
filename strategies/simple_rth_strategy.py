"""
Simple RTH (regular trading hours) momentum strategy.

Reuses the same signal engine as ``SimpleMomentumStrategy`` while using
``config/strategies/simple_rth.toml`` for symbols, risk, and session times.
Enable in TOML when you want cash-session momentum alongside overnight_range.
"""

import logging

from strategies.simple_momentum_strategy import SimpleMomentumStrategy

logger = logging.getLogger(__name__)


class SimpleRthStrategy(SimpleMomentumStrategy):
    """Momentum during configured RTH window (see ``simple_rth.toml``)."""

    STRATEGY_ID = "simple_rth"

    def __init__(self, trading_bot, config=None):
        super().__init__(trading_bot, config)
        logger.info("Simple RTH momentum strategy ready (inherits simple_momentum logic)")
