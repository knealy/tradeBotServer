"""Smoke tests for the research-grade VWAP Z-score reversion strategy.

We only verify that:
- the module imports cleanly,
- it is registered in `strategies.strategy_manager.BUILTIN_STRATEGY_SPECS`
  and `core.backtest_executor` (`--list-strategies` view + dispatch),
- the class can be instantiated against a minimal mock bot,
- ``analyze`` short-circuits to ``None`` when there are not enough bars,
- the LLM helpers in ``core.llm`` import without contacting Ollama.

This keeps the test fast (no network, no Ollama, no full replay) while
still failing loudly if any of the structural wiring regresses.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone


def test_strategy_registered_in_manager():
    from strategies.strategy_manager import BUILTIN_STRATEGY_SPECS

    assert "vwap_zscore_reversion" in BUILTIN_STRATEGY_SPECS
    module, cls, _description = BUILTIN_STRATEGY_SPECS["vwap_zscore_reversion"]
    assert module == "strategies.vwap_zscore_reversion_strategy"
    assert cls == "VwapZscoreReversionStrategy"


def test_strategy_registered_in_backtest_executor():
    from core.backtest_executor import BacktestExecutor

    klass = BacktestExecutor()._get_strategy_class("vwap_zscore_reversion")
    assert klass is not None
    assert klass.__name__ == "VwapZscoreReversionStrategy"


def test_strategy_imports_cleanly():
    from strategies.vwap_zscore_reversion_strategy import (
        VwapZscoreReversionStrategy,
    )

    assert VwapZscoreReversionStrategy.NAME == "vwap_zscore_reversion"


class _MockBot:
    """Minimal stand-in for the live trading bot used in unit-tests only."""

    def __init__(self, bars):
        self.bars = bars
        self.selected_account = {"id": "test", "name": "TEST"}
        self._is_strategy_replay = True
        self._current_bar_timestamp = bars[-1]["timestamp"] if bars else None

    async def get_historical_data(self, symbol, timeframe=None, limit=None, **_):
        if limit:
            return self.bars[-limit:]
        return self.bars

    async def get_market_quote(self, symbol):
        if not self.bars:
            return {"bid": 0, "ask": 0, "last": 0}
        return {"bid": self.bars[-1]["close"], "ask": self.bars[-1]["close"], "last": self.bars[-1]["close"]}

    async def get_open_positions(self, account_id=None):
        return []

    async def calculate_atr(self, symbol, period=14):
        return 5.0


def _bar(ts: datetime, c: float) -> dict:
    return {
        "timestamp": ts.astimezone(timezone.utc),
        "open": c,
        "high": c + 0.5,
        "low": c - 0.5,
        "close": c,
        "volume": 100,
    }


def test_analyze_returns_none_when_too_few_bars():
    from strategies.strategy_base import StrategyConfig
    from strategies.vwap_zscore_reversion_strategy import (
        VwapZscoreReversionStrategy,
    )

    cfg = StrategyConfig(
        name="vwap_zscore_reversion",
        enabled=True,
        symbols=["MNQ"],
        max_positions=1,
        position_size=1,
        risk_per_trade_percent=0.5,
        max_daily_trades=4,
        preferred_conditions=[],
        avoid_conditions=[],
        trading_start_time="09:30",
        trading_end_time="15:30",
        no_trade_start="",
        no_trade_end="",
    )

    start = datetime(2026, 4, 1, 13, 35, tzinfo=timezone.utc)  # 09:35 ET
    bars = [_bar(start + timedelta(minutes=i), 17000.0 + i * 0.25) for i in range(10)]
    bot = _MockBot(bars)
    strat = VwapZscoreReversionStrategy(bot, cfg)

    signal = asyncio.run(strat.analyze("MNQ"))
    assert signal is None  # below min_session_bars / window_bars


def test_llm_module_imports_without_network():
    from core.llm import (
        OllamaClient,
        OllamaError,
        OllamaUnavailableError,
    )

    client = OllamaClient(host="http://127.0.0.1:1")
    assert client.host == "http://127.0.0.1:1"
    assert issubclass(OllamaUnavailableError, OllamaError)
