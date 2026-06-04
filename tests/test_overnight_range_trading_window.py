"""Overnight range executor window (cross-midnight wrap in session TZ)."""

from __future__ import annotations

from datetime import datetime

import pytest

pytest.importorskip("pytz")


class _Bot:
    _is_strategy_replay = False


def test_in_trading_window_wrap_evening_and_early_morning():
    from strategies.overnight_range_strategy import OvernightRangeStrategy
    from strategies.strategy_base import StrategyConfig

    cfg = StrategyConfig(
        name="overnight_range",
        enabled=True,
        symbols=["MNQ"],
        max_positions=2,
        position_size=1,
        risk_per_trade_percent=0.5,
        max_daily_trades=4,
        preferred_conditions=[],
        avoid_conditions=[],
        trading_start_time="00:00",
        trading_end_time="23:59",
        no_trade_start="",
        no_trade_end="",
    )
    strat = OvernightRangeStrategy(_Bot(), cfg)
    tz = strat.timezone
    # Round 21 committed: 19:00 → 10:00 + gap filter + breaker (2x5).
    assert strat.overnight_end == "10:00"

    evening = tz.localize(datetime(2026, 1, 5, 20, 0, 0))
    assert strat._in_trading_window_at(evening) is True

    afternoon = tz.localize(datetime(2026, 1, 5, 14, 0, 0))
    assert strat._in_trading_window_at(afternoon) is False

    early_am = tz.localize(datetime(2026, 1, 5, 8, 0, 0))
    assert strat._in_trading_window_at(early_am) is True


def test_in_trading_window_replay_skips_gate():
    from strategies.overnight_range_strategy import OvernightRangeStrategy
    from strategies.strategy_base import StrategyConfig

    cfg = StrategyConfig(
        name="overnight_range",
        enabled=True,
        symbols=["MNQ"],
        max_positions=2,
        position_size=1,
        risk_per_trade_percent=0.5,
        max_daily_trades=4,
        preferred_conditions=[],
        avoid_conditions=[],
        trading_start_time="00:00",
        trading_end_time="23:59",
        no_trade_start="",
        no_trade_end="",
    )
    b = _Bot()
    b._is_strategy_replay = True
    strat = OvernightRangeStrategy(b, cfg)
    assert strat._in_trading_window() is True


def test_replay_order_window_zero_requires_market_open_et():
    """replay_order_window_minutes <= 0 still requires bar time >= timing.market_open (ET)."""
    from strategies.overnight_range_strategy import OvernightRangeStrategy
    from strategies.strategy_base import StrategyConfig

    cfg = StrategyConfig(
        name="overnight_range",
        enabled=True,
        symbols=["MNQ"],
        max_positions=2,
        position_size=1,
        risk_per_trade_percent=0.5,
        max_daily_trades=4,
        preferred_conditions=[],
        avoid_conditions=[],
        trading_start_time="00:00",
        trading_end_time="23:59",
        no_trade_start="",
        no_trade_end="",
    )
    strat = OvernightRangeStrategy(_Bot(), cfg)
    strat.replay_order_window_minutes = 0
    tz = strat.timezone
    # Round 21 market_open is 10:00 ET (was 9:29 prior to the R19g + R21c flip).
    pre_open = tz.localize(datetime(2026, 1, 5, 3, 55, 0))
    assert strat._replay_in_order_placement_window(pre_open) is False
    just_before_open = tz.localize(datetime(2026, 1, 5, 9, 30, 0))
    assert strat._replay_in_order_placement_window(just_before_open) is False
    at_open = tz.localize(datetime(2026, 1, 5, 10, 0, 0))
    assert strat._replay_in_order_placement_window(at_open) is True
    noon = tz.localize(datetime(2026, 1, 5, 12, 0, 0))
    assert strat._replay_in_order_placement_window(noon) is True

    # Evening same calendar day: in-range building for session ending *tomorrow* — must not
    # treat as "after today's market open" (that was this morning's open, not tomorrow's).
    evening = tz.localize(datetime(2026, 1, 5, 20, 0, 0))
    assert strat._replay_in_order_placement_window(evening) is False


def test_filter_pct_matches_shipped_overnight_range_toml():
    """Stock ``overnight_range.toml`` filter percentages match the committed values.

    Round 13: legacy MNQ-pt @ 21k defaults for range / gap / (atr_min, atr_max).
    Round 14 (2026-06-01): ATR band tightened to 0.08-0.60% and ``volatility``
    filter flipped ON — the band sits at the historic 25-percentile / 90-percentile
    of overnight ATR%, gating out (a) dead-range sessions and (b) high-vol cluster
    sessions whose breakouts are statistically fakeouts.
    """
    from strategies.overnight_range_strategy import OvernightRangeStrategy, _OVERNIGHT_FILTER_LEGACY_REF_PX
    from strategies.strategy_base import StrategyConfig

    cfg = StrategyConfig(
        name="overnight_range",
        enabled=True,
        symbols=["MNQ"],
        max_positions=2,
        position_size=1,
        risk_per_trade_percent=0.5,
        max_daily_trades=4,
        preferred_conditions=[],
        avoid_conditions=[],
        trading_start_time="00:00",
        trading_end_time="23:59",
        no_trade_start="",
        no_trade_end="",
    )
    strat = OvernightRangeStrategy(_Bot(), cfg)
    ref = _OVERNIGHT_FILTER_LEGACY_REF_PX
    assert strat.filter_range_min_pct == pytest.approx((15.0 / ref) * 100.0)
    assert strat.filter_range_max_pct == pytest.approx((600.0 / ref) * 100.0)
    # Round 22 (2026-06-03 PM) — post-fix re-tune after the BACKTEST_FAST_LOOP
    # _BarRow EOD-flat bypass fix invalidated the round-21 buggy-baseline
    # calibration.  Clean 12-trial filter sweep on the corrected baseline
    # (docs/perf/_opt_runs/overnight_range_postfix/r2 → r3 → r5) put
    # gap_max_pct = 1.20 as Pareto-best across 3m / 6m / 9m horizons.
    # See config/strategies/overnight_range.toml [filters] comments for
    # the full per-threshold sweep table.
    assert strat.filter_gap_max_pct == pytest.approx(1.20)
    # Round 14 ATR-band values (see config/strategies/overnight_range.toml).
    assert strat.filter_atr_min_pct == pytest.approx(0.08)
    assert strat.filter_atr_max_pct == pytest.approx(0.60)
    assert strat.filter_volatility_enabled is True


def test_filter_pct_for_symbol_override_pct_and_legacy_pts():
    from strategies.overnight_range_strategy import OvernightRangeStrategy, _OVERNIGHT_FILTER_LEGACY_REF_PX
    from strategies.strategy_base import StrategyConfig

    cfg = StrategyConfig(
        name="overnight_range",
        enabled=True,
        symbols=["MNQ"],
        max_positions=2,
        position_size=1,
        risk_per_trade_percent=0.5,
        max_daily_trades=4,
        preferred_conditions=[],
        avoid_conditions=[],
        trading_start_time="00:00",
        trading_end_time="23:59",
        no_trade_start="",
        no_trade_end="",
    )
    strat = OvernightRangeStrategy(_Bot(), cfg)
    g = strat.filter_range_min_pct
    strat._cfg._data.setdefault("symbols", {})["MES"] = {"filters": {"range_min_pct": 0.5}}
    assert strat._filter_pct_for_symbol("MES", "range_min_pct", "range_min_pts", g) == pytest.approx(0.5)

    strat._cfg._data["symbols"]["MGC"] = {"filters": {"range_min_pts": 2100.0}}
    assert strat._filter_pct_for_symbol("MGC", "range_min_pct", "range_min_pts", g) == pytest.approx(
        (2100.0 / _OVERNIGHT_FILTER_LEGACY_REF_PX) * 100.0
    )


def test_replay_cancel_pending_stop_entries_removes_tagged_bracket():
    """Stale simulator stop entries must be droppable without broker get_open_orders."""
    from core.backtest.engine import BacktestEngine
    from core.backtest.models import BacktestOrder, OrderSide, OrderStatus, OrderType
    from strategies.overnight_range_strategy import OvernightRangeStrategy
    from strategies.strategy_base import StrategyConfig

    cfg = StrategyConfig(
        name="overnight_range",
        enabled=True,
        symbols=["MNQ"],
        max_positions=2,
        position_size=1,
        risk_per_trade_percent=0.5,
        max_daily_trades=4,
        preferred_conditions=[],
        avoid_conditions=[],
        trading_start_time="00:00",
        trading_end_time="23:59",
        no_trade_start="",
        no_trade_end="",
    )
    engine = BacktestEngine()
    o = BacktestOrder(
        order_id="BT000001",
        timestamp=datetime(2026, 1, 5, 14, 30, 0),
        symbol="MNQ",
        side=OrderSide.SELL,
        order_type=OrderType.STOP,
        quantity=1,
        price=100.0,
        stop_price=100.0,
        status=OrderStatus.PENDING,
    )
    o.stop_loss_price = 110.0
    o.take_profit_price = 90.0
    o.custom_tag = "TB-stop_bracket-overnight_range-replay"
    engine.pending_orders.append(o)

    bot = _Bot()
    bot.backtest_engine = engine
    strat = OvernightRangeStrategy(bot, cfg)
    n = strat._replay_cancel_pending_stop_entries(["MNQ"])
    assert n == 1
    assert engine.pending_orders == []


@pytest.mark.asyncio
async def test_overnight_range_replay_before_bar_fills_purges_evening():
    """Outside post-open window, purge hook clears tagged resting stop entries."""
    pytest.importorskip("pytz")
    from datetime import timezone

    from strategies.overnight_range_strategy import OvernightRangeStrategy
    from strategies.strategy_base import StrategyConfig
    from core.backtest.engine import BacktestEngine
    from core.backtest.models import BacktestOrder, OrderSide, OrderStatus, OrderType

    cfg = StrategyConfig(
        name="overnight_range",
        enabled=True,
        symbols=["MNQ"],
        max_positions=2,
        position_size=1,
        risk_per_trade_percent=0.5,
        max_daily_trades=4,
        preferred_conditions=[],
        avoid_conditions=[],
        trading_start_time="00:00",
        trading_end_time="23:59",
        no_trade_start="",
        no_trade_end="",
    )
    engine = BacktestEngine()
    o = BacktestOrder(
        order_id="BT000001",
        timestamp=datetime(2026, 1, 5, 14, 30, 0),
        symbol="MNQ",
        side=OrderSide.SELL,
        order_type=OrderType.STOP,
        quantity=1,
        price=100.0,
        stop_price=100.0,
        status=OrderStatus.PENDING,
    )
    o.stop_loss_price = 110.0
    o.take_profit_price = 90.0
    o.custom_tag = "TB-stop_bracket-overnight_range-replay"
    engine.pending_orders.append(o)

    bot = _Bot()
    bot._is_strategy_replay = True
    bot.backtest_engine = engine
    # 2026-01-05 20:00 US/Eastern -> 2026-01-06 01:00 UTC (EST)
    bot._current_bar_timestamp = datetime(2026, 1, 6, 1, 0, 0, tzinfo=timezone.utc)
    strat = OvernightRangeStrategy(bot, cfg)
    await strat.replay_before_bar_fills()
    assert engine.pending_orders == []


@pytest.mark.asyncio
async def test_check_market_conditions_compares_pct_of_midpoint():
    from strategies.overnight_range_strategy import ATRData, OvernightRange, OvernightRangeStrategy
    from strategies.strategy_base import StrategyConfig
    from datetime import datetime, timezone

    cfg = StrategyConfig(
        name="overnight_range",
        enabled=True,
        symbols=["MNQ"],
        max_positions=2,
        position_size=1,
        risk_per_trade_percent=0.5,
        max_daily_trades=4,
        preferred_conditions=[],
        avoid_conditions=[],
        trading_start_time="00:00",
        trading_end_time="23:59",
        no_trade_start="",
        no_trade_end="",
    )
    strat = OvernightRangeStrategy(_Bot(), cfg)
    strat._cfg._data.setdefault("filters", {})["range_size"] = True
    strat._cfg._data["filters"]["gap"] = False
    strat._cfg._data["filters"]["volatility"] = False
    strat.filter_range_size_enabled = True
    strat.filter_gap_enabled = False
    strat.filter_volatility_enabled = False
    strat.filter_range_min_pct = 1.0
    strat.filter_range_max_pct = 3.0
    now = datetime.now(timezone.utc)
    rd = OvernightRange(
        symbol="MNQ",
        high=10100.0,
        low=9900.0,
        open=10000.0,
        close=10050.0,
        start_time=now,
        end_time=now,
        range_size=50.0,
        midpoint=10000.0,
    )
    atr = ATRData(
        current_atr=1.0,
        daily_atr=1.0,
        atr_zone_high=0.0,
        atr_zone_low=0.0,
        period=14,
    )
    ok, msg = await strat.check_market_conditions("MNQ", rd, atr)
    assert ok is False
    assert "Range too small" in msg

    rd2 = OvernightRange(
        symbol="MNQ",
        high=10200.0,
        low=9800.0,
        open=10000.0,
        close=10050.0,
        start_time=now,
        end_time=now,
        range_size=200.0,
        midpoint=10000.0,
    )
    ok2, _ = await strat.check_market_conditions("MNQ", rd2, atr)
    assert ok2 is True
