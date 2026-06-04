"""Smoke tests for the research-grade body-reversion strategy.

Verifies wiring (registration + import) and that ``analyze`` short-circuits
when the body threshold is not met. Keeps the test fast — no full replay,
no Ollama, no network.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone


def test_strategy_registered_in_manager():
    from strategies.strategy_manager import BUILTIN_STRATEGY_SPECS

    assert "body_reversion" in BUILTIN_STRATEGY_SPECS
    module, cls, _description = BUILTIN_STRATEGY_SPECS["body_reversion"]
    assert module == "strategies.body_reversion_strategy"
    assert cls == "BodyReversionStrategy"


def test_strategy_registered_in_backtest_executor():
    from core.backtest_executor import BacktestExecutor

    klass = BacktestExecutor()._get_strategy_class("body_reversion")
    assert klass is not None
    assert klass.__name__ == "BodyReversionStrategy"


def test_strategy_imports_cleanly():
    from strategies.body_reversion_strategy import BodyReversionStrategy

    assert BodyReversionStrategy.NAME == "body_reversion"


class _MockBot:
    def __init__(self, bars):
        self.bars = bars
        self.selected_account = {"id": "test", "name": "TEST"}
        self._is_strategy_replay = True
        self._current_bar_timestamp = bars[-1]["timestamp"] if bars else None

    async def get_historical_data(self, symbol, timeframe=None, limit=None, **_):
        if limit:
            return self.bars[-limit:]
        return self.bars

    async def calculate_atr(self, symbol, period=14):
        return 5.0


def _bar(ts: datetime, o: float, h: float, l: float, c: float) -> dict:
    return {
        "timestamp": ts.astimezone(timezone.utc),
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "volume": 100,
    }


def test_analyze_skips_when_body_below_threshold():
    from strategies.body_reversion_strategy import BodyReversionStrategy
    from strategies.strategy_base import StrategyConfig

    cfg = StrategyConfig(
        name="body_reversion",
        enabled=True,
        symbols=["MNQ"],
        max_positions=1,
        position_size=1,
        risk_per_trade_percent=0.5,
        max_daily_trades=12,
        preferred_conditions=[],
        avoid_conditions=[],
        trading_start_time="00:00",
        trading_end_time="23:59",
        no_trade_start="",
        no_trade_end="",
    )

    start = datetime(2026, 4, 1, 13, 35, tzinfo=timezone.utc)
    bars = [
        _bar(start + timedelta(minutes=5 * i), 17000.0, 17005.0, 16995.0, 17002.0)
        for i in range(40)
    ]
    bot = _MockBot(bars)
    strat = BodyReversionStrategy(bot, cfg)

    signal = asyncio.run(strat.analyze("MNQ"))
    assert signal is None  # body=2/10=0.2 < 0.9 (v2 default)


def test_analyze_emits_long_after_big_bear_bar():
    """Body-pct trigger emits LONG when v3 regime gates are disabled."""
    from strategies.body_reversion_strategy import BodyReversionStrategy
    from strategies.strategy_base import StrategyConfig

    cfg = StrategyConfig(
        name="body_reversion",
        enabled=True,
        symbols=["MNQ"],
        max_positions=1,
        position_size=1,
        risk_per_trade_percent=0.5,
        max_daily_trades=12,
        preferred_conditions=[],
        avoid_conditions=[],
        trading_start_time="00:00",
        trading_end_time="23:59",
        no_trade_start="",
        no_trade_end="",
    )

    start = datetime(2026, 4, 1, 13, 35, tzinfo=timezone.utc)
    bars = [
        _bar(start + timedelta(minutes=5 * i), 17000.0, 17005.0, 16995.0, 17002.0)
        for i in range(70)
    ]
    # Big bear bar: body = 10 / 11 ≈ 0.91 (clears v2 default 0.90)
    bars.append(
        _bar(
            start + timedelta(minutes=5 * 70),
            17010.0,
            17010.5,
            16999.5,
            17000.0,
        )
    )
    bot = _MockBot(bars)
    strat = BodyReversionStrategy(bot, cfg)
    # Disable v3 regime gates to isolate the body-pct trigger.
    strat.require_high_atr = False
    strat.require_range_expand = False

    signal = asyncio.run(strat.analyze("MNQ"))
    assert signal is not None
    assert signal["action"] == "LONG"
    assert signal["entry_price"] > 17000.0
    assert signal["stop_loss"] < signal["entry_price"]
    assert signal["take_profit"] > signal["entry_price"]


def test_analyze_skipped_when_v3_regime_gates_active_and_no_expansion():
    """v3 default: a big-body bar in a NORMAL-vol regime is filtered out."""
    from strategies.body_reversion_strategy import BodyReversionStrategy
    from strategies.strategy_base import StrategyConfig

    cfg = StrategyConfig(
        name="body_reversion",
        enabled=True,
        symbols=["MES"],
        max_positions=1,
        position_size=1,
        risk_per_trade_percent=0.5,
        max_daily_trades=12,
        preferred_conditions=[],
        avoid_conditions=[],
        trading_start_time="00:00",
        trading_end_time="23:59",
        no_trade_start="",
        no_trade_end="",
    )
    start = datetime(2026, 4, 1, 13, 35, tzinfo=timezone.utc)
    # Long boring history with constant 10-pt range
    bars = [
        _bar(start + timedelta(minutes=5 * i), 17000.0, 17005.0, 16995.0, 17002.0)
        for i in range(80)
    ]
    # Big-body bar with 11-pt range — only ~10% larger than the 10-pt MA.
    # range_expand_1.5x requires > 15-pt range, so this fails the gate.
    bars.append(
        _bar(start + timedelta(minutes=5 * 80), 17010.0, 17010.5, 16999.5, 17000.0)
    )
    bot = _MockBot(bars)
    strat = BodyReversionStrategy(bot, cfg)
    # MES TOML enables range_expand + optional BB OR; isolate range-only reject.
    strat.require_bb_touch = False
    # MES has a per-symbol TOML override enabling range expansion.
    signal = asyncio.run(strat.analyze("MES"))
    assert signal is None  # filtered by range_expand gate


def test_analyze_emits_long_when_v3_regime_gates_satisfied():
    """v3 default: a big-body bar that ALSO clears the regime gates emits."""
    from strategies.body_reversion_strategy import BodyReversionStrategy
    from strategies.strategy_base import StrategyConfig

    cfg = StrategyConfig(
        name="body_reversion",
        enabled=True,
        symbols=["MES"],
        max_positions=1,
        position_size=1,
        risk_per_trade_percent=0.5,
        max_daily_trades=12,
        preferred_conditions=[],
        avoid_conditions=[],
        trading_start_time="00:00",
        trading_end_time="23:59",
        no_trade_start="",
        no_trade_end="",
    )
    start = datetime(2026, 4, 1, 13, 35, tzinfo=timezone.utc)
    # 50 quiet bars (range = 10 pts → ATR ≈ 10) — sets the lower tail of the
    # ATR distribution.
    bars = [
        _bar(start + timedelta(minutes=5 * i), 17000.0, 17005.0, 16995.0, 17002.0)
        for i in range(50)
    ]
    # 14 high-vol bars (range = 20 pts) so the current 14-bar ATR ≈ 20 lands
    # above the Q75 cutoff of the lookback distribution. Also sets
    # range_ma20 ≈ 17 (mix of quiet + high-vol).
    for j in range(14):
        bars.append(
            _bar(
                start + timedelta(minutes=5 * (50 + j)),
                17000.0, 17010.0, 16990.0, 17005.0,
            )
        )
    # Big bear capitulation bar: 35-pt range, 33-pt body (≈ 0.94), well over
    # 1.5 × range_ma20 (= 25.5).
    bars.append(
        _bar(
            start + timedelta(minutes=5 * 64),
            17033.0, 17035.0, 17000.0, 17000.0,
        )
    )
    bot = _MockBot(bars)
    strat = BodyReversionStrategy(bot, cfg)
    signal = asyncio.run(strat.analyze("MES"))
    assert signal is not None, "v3 should emit when regime gates are satisfied"
    assert signal["action"] == "LONG"


def test_analyze_skips_when_position_open():
    """Lock-out: no new signal while a position or pending entry exists."""
    from strategies.body_reversion_strategy import BodyReversionStrategy
    from strategies.strategy_base import StrategyConfig

    cfg = StrategyConfig(
        name="body_reversion",
        enabled=True,
        symbols=["MNQ"],
        max_positions=1,
        position_size=1,
        risk_per_trade_percent=0.5,
        max_daily_trades=12,
        preferred_conditions=[],
        avoid_conditions=[],
        trading_start_time="00:00",
        trading_end_time="23:59",
        no_trade_start="",
        no_trade_end="",
    )
    start = datetime(2026, 4, 1, 13, 35, tzinfo=timezone.utc)
    bars = [
        _bar(start + timedelta(minutes=5 * i), 17000.0, 17005.0, 16995.0, 17002.0)
        for i in range(30)
    ]
    bars.append(
        _bar(
            start + timedelta(minutes=5 * 30),
            17010.0, 17010.5, 16999.5, 17000.0,  # body 0.91 bear bar
        )
    )
    bot = _MockBot(bars)
    bot.active_positions = [{"symbol": "MNQ", "side": "LONG", "quantity": 1}]
    strat = BodyReversionStrategy(bot, cfg)
    # Disable v3 regime gates so the lockout is the only thing blocking the
    # signal — otherwise the gate would block first and this test would
    # become a no-op (assert None when None for the wrong reason).
    strat.require_high_atr = False
    strat.require_range_expand = False

    signal = asyncio.run(strat.analyze("MNQ"))
    assert signal is None  # locked out


def test_v3_defaults_load_from_toml():
    """v3 defaults: body_pct_min=0.90, stop_atr=0.5, max_hold=6, ATR gate ON."""
    from strategies.body_reversion_strategy import BodyReversionStrategy
    from strategies.strategy_base import StrategyConfig

    cfg = StrategyConfig(
        name="body_reversion",
        enabled=True,
        symbols=["MNQ"],
        max_positions=1,
        position_size=1,
        risk_per_trade_percent=0.5,
        max_daily_trades=12,
        preferred_conditions=[],
        avoid_conditions=[],
        trading_start_time="00:00",
        trading_end_time="23:59",
        no_trade_start="",
        no_trade_end="",
    )
    bars = [
        _bar(datetime(2026, 4, 1, 13, 35, tzinfo=timezone.utc), 17000, 17005, 16995, 17002)
    ]
    strat = BodyReversionStrategy(_MockBot(bars), cfg)
    assert strat.body_pct_min == 0.90
    # Round-1 (2026-06-01) tightened the stop to 0.35×ATR + boosted TP to 3.0R
    # for 9m DD 82% → 11.33% / RF 6.21 → 29.27. Stop=0.5 was the legacy R0 default.
    assert strat.stop_atr_multiplier == 0.35
    assert strat.tp_r_multiple == 3.0
    assert strat.max_hold_bars == 6
    assert strat.min_bars_between_signals == 6
    assert strat.allow_long is True
    assert strat.allow_short is True
    assert strat.require_high_atr is True
    # v3.1 default is range gate OFF globally; MES overrides it ON.
    assert strat.require_range_expand is False
    assert strat.atr_regime_quantile == 0.75
    assert strat.range_expand_mult == 1.5
    assert strat.require_bb_touch is False
    assert strat.lookback_bars == 500


def test_analyze_mes_emits_long_when_bb_touch_or_range_expand_bb_leg():
    """MES OR co-trigger: range MA fails but lower Bollinger is tagged."""
    from strategies.body_reversion_strategy import BodyReversionStrategy
    from strategies.strategy_base import StrategyConfig

    cfg = StrategyConfig(
        name="body_reversion",
        enabled=True,
        symbols=["MES"],
        max_positions=1,
        position_size=1,
        risk_per_trade_percent=0.5,
        max_daily_trades=12,
        preferred_conditions=[],
        avoid_conditions=[],
        trading_start_time="00:00",
        trading_end_time="23:59",
        no_trade_start="",
        no_trade_end="",
    )
    start = datetime(2026, 4, 1, 13, 35, tzinfo=timezone.utc)
    # Steady 10-pt range bars so range_ma20 ≈ 10; last bar fails 1.5× expand (<15).
    bars = [
        _bar(start + timedelta(minutes=5 * i), 17000.0, 17010.0, 17000.0, 17005.0)
        for i in range(82)
    ]
    bars.append(
        _bar(
            start + timedelta(minutes=5 * 82),
            17010.0,
            17010.1,
            16999.5,
            17000.0,
        )
    )
    bot = _MockBot(bars)
    strat = BodyReversionStrategy(bot, cfg)
    strat.require_high_atr = False
    signal = asyncio.run(strat.analyze("MES"))
    assert signal is not None
    assert signal["action"] == "LONG"


def test_generic_breakeven_position_symbol_matches():
    """BONGO §1B: broker position symbols vs plain roots (MNQ, F.US.MNQ, …)."""
    from trading_bot import TopStepXTradingBot

    m = TopStepXTradingBot._position_symbol_matches
    assert m("F.US.MNQ", "MNQ")
    assert m("MNQM5", "MNQ")
    assert m("MNQ", "MNQ")
    assert not m("MES", "MNQ")


def test_in_trading_window_uses_session_timezone(monkeypatch):
    """Executor gate uses ``signal.session_timezone``, not naive local wall clock."""
    from zoneinfo import ZoneInfo

    from strategies.body_reversion_strategy import BodyReversionStrategy
    from strategies.strategy_base import StrategyConfig

    z = ZoneInfo("America/New_York")
    cfg = StrategyConfig(
        name="body_reversion",
        enabled=True,
        symbols=["MNQ"],
        max_positions=1,
        position_size=1,
        risk_per_trade_percent=0.5,
        max_daily_trades=12,
        preferred_conditions=[],
        avoid_conditions=[],
        trading_start_time="09:30",
        trading_end_time="16:00",
        no_trade_start="",
        no_trade_end="",
    )
    bot = _MockBot([])
    bot._is_strategy_replay = False
    strat = BodyReversionStrategy(bot, cfg)

    monkeypatch.setattr(strat, "_session_tz_wall_now", lambda: datetime(2026, 5, 12, 10, 0, tzinfo=z))
    assert strat._in_trading_window() is True
    monkeypatch.setattr(strat, "_session_tz_wall_now", lambda: datetime(2026, 5, 12, 9, 0, tzinfo=z))
    assert strat._in_trading_window() is False

    bot._is_strategy_replay = True
    assert strat._in_trading_window() is True
