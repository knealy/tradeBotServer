"""Smoke tests for morning_range_reversion (sieve + wiring)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest


def test_morning_range_toml_mes_tp_mult_override():
    from core.strategy_config import load_strategy_config

    cfg = load_strategy_config("morning_range_reversion")
    assert float(cfg.symbol_override("MES", "signal.tp_mult", default=1.0)) == pytest.approx(0.7)
    assert float(cfg.symbol_override("MNQ", "signal.tp_mult", default=1.0)) == pytest.approx(1.0)


def test_strategy_registered_in_manager():
    from strategies.strategy_manager import BUILTIN_STRATEGY_SPECS

    assert "morning_range_reversion" in BUILTIN_STRATEGY_SPECS
    module, cls, _d = BUILTIN_STRATEGY_SPECS["morning_range_reversion"]
    assert module == "strategies.morning_range_reversion_strategy"
    assert cls == "MorningRangeReversionStrategy"


def test_strategy_registered_in_backtest_executor():
    from core.backtest_executor import BacktestExecutor

    klass = BacktestExecutor()._get_strategy_class("morning_range_reversion")
    assert klass is not None
    assert klass.__name__ == "MorningRangeReversionStrategy"


def test_sieve_immediate_mode_does_not_churn_while_close_stays_outside():
    """Regression: consecutive closes beyond the range arm at most once until a close inside [L,H]."""
    from strategies.morning_range_reversion_strategy import sieve_simulate_from_ohlcv

    start = pd.Timestamp("2026-01-05 12:00", tz="UTC")
    idx = [start + pd.Timedelta(minutes=5 * i) for i in range(16)]
    rows = []
    for i in range(16):
        if i < 12:
            rows.append({"open": 105, "high": 110, "low": 100, "close": 105})
        elif i == 12:
            # First close above H — immediate SHORT; stop tags 115 same bar → one loss trade
            rows.append({"open": 105, "high": 115, "low": 104, "close": 111})
        elif i in (13, 14):
            # Stay outside without re-entering the box — must not create new sieves
            rows.append({"open": 112, "high": 116, "low": 111, "close": 114})
        else:
            rows.append({"open": 112, "high": 112, "low": 105, "close": 106})
    df = pd.DataFrame(rows, index=[t.tz_convert(None) for t in idx])
    tr = sieve_simulate_from_ohlcv(df, require_reentry_close=False)
    assert len(tr) == 1
    assert tr[0].outcome == "loss"


def test_sieve_range_effectiveness_hours_blocks_late_sweep():
    """After range_end_open + N hours ET, no new fade from a sweep (default 4h → 12:00 ET cutoff)."""
    from strategies.morning_range_reversion_strategy import sieve_simulate_from_ohlcv

    start = pd.Timestamp("2026-01-05 12:00", tz="UTC")
    idx = [start + pd.Timedelta(minutes=5 * i) for i in range(62)]
    rows = []
    for i in range(62):
        if i < 12:
            rows.append({"open": 105, "high": 110, "low": 100, "close": 105})
        elif i < 61:
            rows.append({"open": 105, "high": 110, "low": 100, "close": 105})
        else:
            rows.append({"open": 105, "high": 115, "low": 104, "close": 111})
    df = pd.DataFrame(rows, index=[t.tz_convert(None) for t in idx])
    assert len(sieve_simulate_from_ohlcv(df, require_reentry_close=False)) == 0
    assert len(sieve_simulate_from_ohlcv(df, require_reentry_close=False, range_effectiveness_hours=0)) >= 1


def test_sieve_max_fades_per_session_blocks_second_arm_same_day():
    """After one fade + inside reset, a second arm is skipped when max_fades_per_session=1."""
    from strategies.morning_range_reversion_strategy import sieve_simulate_from_ohlcv

    start = pd.Timestamp("2026-01-05 12:00", tz="UTC")
    idx = [start + pd.Timedelta(minutes=5 * i) for i in range(20)]
    rows = []
    for i in range(12):
        rows.append({"open": 105, "high": 110, "low": 100, "close": 105})
    rows.append({"open": 105, "high": 115, "low": 104, "close": 111})
    rows.append({"open": 112, "high": 116, "low": 111, "close": 114})
    rows.append({"open": 112, "high": 112, "low": 105, "close": 106})
    rows.append({"open": 106, "high": 112, "low": 105, "close": 111})
    for _ in range(15, 19):
        rows.append({"open": 106, "high": 108, "low": 104, "close": 105})
    df = pd.DataFrame(rows, index=[t.tz_convert(None) for t in idx])
    tr0 = sieve_simulate_from_ohlcv(df, require_reentry_close=False)
    tr1 = sieve_simulate_from_ohlcv(df, require_reentry_close=False, max_fades_per_session=1)
    assert len(tr0) >= 2
    assert len(tr1) == 1


def test_sieve_high_sweep_short_win():
    from strategies.morning_range_reversion_strategy import sieve_simulate_from_ohlcv

    start = pd.Timestamp("2026-01-05 12:00", tz="UTC")
    idx = [start + pd.Timedelta(minutes=5 * i) for i in range(15)]
    rows = []
    for i in range(15):
        if i < 12:
            rows.append({"open": 105, "high": 110, "low": 100, "close": 105})
        elif i == 12:
            rows.append({"open": 105, "high": 112, "low": 104, "close": 111})
        elif i == 13:
            rows.append({"open": 111, "high": 111, "low": 105, "close": 105})
        else:
            rows.append({"open": 105, "high": 108, "low": 104, "close": 107})
    df = pd.DataFrame(rows, index=[t.tz_convert(None) for t in idx])
    tr = sieve_simulate_from_ohlcv(df, require_reentry_close=True)
    assert len(tr) == 1
    assert tr[0].outcome == "win"
    assert tr[0].side == "SHORT"
    assert tr[0].sweep == "high"


def test_sieve_pre_range_overnight_bars_do_not_skip_morning_window():
    """Regression: first bars of the ET day before 07:00 must not finalize to idle."""
    from strategies.morning_range_reversion_strategy import sieve_simulate_from_ohlcv

    # Jan 5 2026: 05:00 UTC = midnight ET (EST); then 7am ET = 12:00 UTC same as other tests.
    t_midnight_et = pd.Timestamp("2026-01-05 05:00", tz="UTC")
    t_range0 = pd.Timestamp("2026-01-05 12:00", tz="UTC")
    idx = [t_midnight_et + pd.Timedelta(minutes=5 * i) for i in range(3)]
    idx += [t_range0 + pd.Timedelta(minutes=5 * j) for j in range(15)]
    rows = []
    for _ in range(3):
        rows.append({"open": 99, "high": 100, "low": 98, "close": 99})
    for j in range(15):
        if j < 12:
            rows.append({"open": 105, "high": 110, "low": 100, "close": 105})
        elif j == 12:
            rows.append({"open": 105, "high": 112, "low": 104, "close": 111})
        elif j == 13:
            rows.append({"open": 111, "high": 111, "low": 105, "close": 105})
        else:
            rows.append({"open": 105, "high": 108, "low": 104, "close": 107})
    df = pd.DataFrame(rows, index=[t.tz_convert(None) for t in idx])
    tr = sieve_simulate_from_ohlcv(df, require_reentry_close=True)
    assert len(tr) == 1
    assert tr[0].outcome == "win"


def test_sieve_1m_path_tp_before_stop_same_5m_bar():
    """1m path hits target before stop on a 5m bar where both extremes touch."""
    from strategies.morning_range_reversion_strategy import sieve_simulate_from_ohlcv

    start = pd.Timestamp("2026-01-06 12:00", tz="UTC")
    idx = [start + pd.Timedelta(minutes=5 * i) for i in range(15)]
    rows = []
    for i in range(15):
        if i < 12:
            rows.append({"open": 105, "high": 110, "low": 100, "close": 105})
        elif i == 12:
            rows.append({"open": 105, "high": 112, "low": 104, "close": 111})
        elif i == 13:
            rows.append({"open": 111, "high": 116, "low": 104, "close": 105})
        else:
            rows.append({"open": 105, "high": 106, "low": 105, "close": 105})
    df5 = pd.DataFrame(rows, index=[t.tz_convert(None) for t in idx])
    t5 = idx[13].tz_convert(None)
    # Five 1m bars covering the ambiguous 5m [t5, t5+5m)
    im = []
    for k in range(5):
        ts = t5 + pd.Timedelta(minutes=k)
        if k == 0:
            im.append((ts, {"open": 111, "high": 111, "low": 104, "close": 104}))
        elif k == 1:
            im.append((ts, {"open": 104, "high": 116, "low": 104, "close": 110}))
        else:
            im.append((ts, {"open": 110, "high": 110, "low": 109, "close": 109}))
    df1 = pd.DataFrame([x[1] for x in im], index=[x[0] for x in im])
    tr = sieve_simulate_from_ohlcv(
        df5,
        one_minute_df=df1,
        stop_before_target_same_bar=True,
        require_reentry_close=True,
    )
    assert len(tr) == 1
    assert tr[0].outcome == "win"


def test_sieve_conservative_same_bar_stop_wins():
    from strategies.morning_range_reversion_strategy import sieve_simulate_from_ohlcv

    start = pd.Timestamp("2026-01-06 12:00", tz="UTC")
    idx = [start + pd.Timedelta(minutes=5 * i) for i in range(15)]
    rows = []
    for i in range(15):
        if i < 12:
            rows.append({"open": 105, "high": 110, "low": 100, "close": 105})
        elif i == 12:
            rows.append({"open": 105, "high": 112, "low": 104, "close": 111})
        elif i == 13:
            # re-entry bar: spikes both stop (115) and target (105) — conservative loss
            rows.append({"open": 111, "high": 116, "low": 104, "close": 105})
        else:
            rows.append({"open": 105, "high": 106, "low": 105, "close": 105})
    df = pd.DataFrame(rows, index=[t.tz_convert(None) for t in idx])
    tr = sieve_simulate_from_ohlcv(df, stop_before_target_same_bar=True, require_reentry_close=True)
    assert len(tr) == 1
    assert tr[0].outcome == "loss"


def test_sieve_tp_mult_changes_take_profit_target():
    from strategies.morning_range_reversion_strategy import sieve_simulate_from_ohlcv

    start = pd.Timestamp("2026-01-05 12:00", tz="UTC")
    idx = [start + pd.Timedelta(minutes=5 * i) for i in range(15)]
    rows = []
    for i in range(15):
        if i < 12:
            rows.append({"open": 105, "high": 110, "low": 100, "close": 105})
        elif i == 12:
            rows.append({"open": 105, "high": 112, "low": 104, "close": 111})
        elif i == 13:
            rows.append({"open": 111, "high": 111, "low": 105, "close": 105})
        else:
            rows.append({"open": 105, "high": 108, "low": 104, "close": 107})
    df = pd.DataFrame(rows, index=[t.tz_convert(None) for t in idx])
    tr1 = sieve_simulate_from_ohlcv(df, require_reentry_close=True, tp_mult=1.0)
    tr07 = sieve_simulate_from_ohlcv(df, require_reentry_close=True, tp_mult=0.7)
    assert len(tr1) == 1 and len(tr07) == 1
    assert abs(tr1[0].target - tr07[0].target) > 0.25


def test_sieve_reentry_frac_blocks_shallow_close_inside_box():
    """BONGO §4.2: inner band [L+frac*W, H-frac*W] must match live analyze."""
    from strategies.morning_range_reversion_strategy import sieve_simulate_from_ohlcv

    start = pd.Timestamp("2026-01-05 12:00", tz="UTC")

    def make_df(reentry_close: float, n_bars: int):
        idx = [start + pd.Timedelta(minutes=5 * i) for i in range(n_bars)]
        rows = []
        for i in range(n_bars):
            if i < 12:
                rows.append({"open": 105, "high": 110, "low": 100, "close": 105})
            elif i == 12:
                rows.append({"open": 105, "high": 112, "low": 104, "close": 111})
            elif i == 13:
                rows.append(
                    {"open": 111, "high": 112, "low": 100, "close": reentry_close}
                )
            else:
                rows.append({"open": 105, "high": 108, "low": 104, "close": 107})
        return pd.DataFrame(rows, index=[t.tz_convert(None) for t in idx])

    # H=110 L=100 W=10; frac=0.15 → inner [101.5, 108.5]. Close 101 is inside [L,H] but too shallow.
    # Stop after the shallow re-entry bar so a later "deep by accident" bar cannot arm.
    df_shallow = make_df(101.0, n_bars=14)
    assert len(sieve_simulate_from_ohlcv(df_shallow, require_reentry_close=True, reentry_frac=0.15)) == 0

    df_ok = make_df(102.0, n_bars=15)
    assert len(sieve_simulate_from_ohlcv(df_ok, require_reentry_close=True, reentry_frac=0.15)) == 1


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


def _utc(*args, **kwargs):
    return datetime(*args, tzinfo=timezone.utc, **kwargs)


def test_analyze_emits_short_immediate_on_first_close_outside_high(monkeypatch):
    """Immediate-fade path (legacy default): stop-entry on the sweep bar."""
    # The validated TOML default is now require_reentry_close=true. This test
    # pins the original immediate-fade behaviour, so opt in via env override.
    monkeypatch.setenv("MORNING_RANGE_REVERSION_SIGNAL_REQUIRE_REENTRY_CLOSE", "false")
    monkeypatch.setenv("MORNING_RANGE_REVERSION_SIGNAL_REENTRY_THRESHOLD_POINTS", "0")
    from strategies.morning_range_reversion_strategy import MorningRangeReversionStrategy
    from strategies.strategy_base import StrategyConfig

    cfg = StrategyConfig(
        name="morning_range_reversion",
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
    t0 = _utc(2026, 1, 7, 12, 0)
    bars = []
    for i in range(12):
        bars.append(
            {
                "timestamp": t0 + timedelta(minutes=5 * i),
                "open": 105,
                "high": 110,
                "low": 100,
                "close": 105,
                "volume": 1,
            }
        )
    bars.append(
        {
            "timestamp": t0 + timedelta(minutes=5 * 12),
            "open": 105,
            "high": 112,
            "low": 104,
            "close": 111,
            "volume": 1,
        }
    )
    bot = _MockBot(bars[:1])
    strat = MorningRangeReversionStrategy(bot, cfg)
    sig = None
    k_at = None
    for k in range(1, len(bars) + 1):
        bot.bars = bars[:k]
        bot._current_bar_timestamp = bars[k - 1]["timestamp"]
        sig = asyncio.run(strat.analyze("MNQ"))
        if sig is not None:
            k_at = k
            break
    assert sig is not None and k_at == len(bars)
    assert sig["action"] == "SHORT"
    assert sig["entry_price"] == 110.0
    assert "immediate_stop" in sig["reason"]


def test_analyze_emits_short_after_high_sweep_reentry(monkeypatch):
    # Legacy candle-close re-entry path: pin reentry_threshold_points=0 so the new
    # points-threshold default does not steal the trigger from the close check.
    monkeypatch.setenv("MORNING_RANGE_REVERSION_SIGNAL_REQUIRE_REENTRY_CLOSE", "true")
    monkeypatch.setenv("MORNING_RANGE_REVERSION_SIGNAL_REENTRY_THRESHOLD_POINTS", "0")
    from strategies.morning_range_reversion_strategy import MorningRangeReversionStrategy
    from strategies.strategy_base import StrategyConfig

    cfg = StrategyConfig(
        name="morning_range_reversion",
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
    # 2026-01-07 7:00 ET = 12:00 UTC (EST)
    t0 = _utc(2026, 1, 7, 12, 0)
    bars = []
    for i in range(12):
        bars.append(
            {
                "timestamp": t0 + timedelta(minutes=5 * i),
                "open": 105,
                "high": 110,
                "low": 100,
                "close": 105,
                "volume": 1,
            }
        )
    bars.append(
        {
            "timestamp": t0 + timedelta(minutes=5 * 12),
            "open": 105,
            "high": 112,
            "low": 104,
            "close": 111,
            "volume": 1,
        }
    )
    bars.append(
        {
            "timestamp": t0 + timedelta(minutes=5 * 13),
            "open": 111,
            "high": 111,
            "low": 105,
            "close": 105,
            "volume": 1,
        }
    )
    # Replay calls analyze once per bar with a growing history (see strategy_replay.py).
    bot = _MockBot(bars[:1])
    strat = MorningRangeReversionStrategy(bot, cfg)
    sig = None
    for k in range(1, len(bars) + 1):
        bot.bars = bars[:k]
        bot._current_bar_timestamp = bars[k - 1]["timestamp"]
        sig = asyncio.run(strat.analyze("MNQ"))
        if sig is not None:
            break
    assert sig is not None
    assert sig["action"] == "SHORT"
    assert sig["entry_price"] == 110.0
    assert sig["take_profit"] == 105.0
    assert sig["stop_loss"] == 115.0
    assert "reentry_confirm" in sig["reason"]


def test_sieve_threshold_mode_arms_on_sweep_bar_advance_stop(monkeypatch):
    """``reentry_threshold_points=7`` places advance stop-entry on the sweep bar close (not a later
    retrace bar) at L+7 / H-7 inside the box (overnight_range style)."""
    from strategies.morning_range_reversion_strategy import sieve_simulate_from_ohlcv

    # Use a wider range (H=130, L=100, width=30) so a 7pt threshold fits comfortably inside
    # half-width. The cap inside ``_fade_signal_after_sweep`` clamps depth at half-width.
    start = pd.Timestamp("2026-01-05 12:00", tz="UTC")
    idx = [start + pd.Timedelta(minutes=5 * i) for i in range(15)]
    rows = []
    for i in range(15):
        if i < 12:
            rows.append({"open": 115, "high": 130, "low": 100, "close": 115})
        elif i == 12:
            # High sweep close above the range — advance stop arms on this bar
            rows.append({"open": 115, "high": 135, "low": 114, "close": 133})
        elif i == 13:
            rows.append({"open": 133, "high": 133, "low": 121, "close": 132})
        else:
            rows.append({"open": 133, "high": 134, "low": 132, "close": 133})
    df = pd.DataFrame(rows, index=[t.tz_convert(None) for t in idx])
    tr_legacy = sieve_simulate_from_ohlcv(df, require_reentry_close=True)
    assert len(tr_legacy) == 0, [t.entry for t in tr_legacy]
    tr_thr = sieve_simulate_from_ohlcv(df, require_reentry_close=True, reentry_threshold_points=7.0)
    assert len(tr_thr) >= 1
    t0 = tr_thr[0]
    assert t0.side == "SHORT"
    assert t0.sweep == "high"
    assert t0.entry == pytest.approx(123.0, abs=0.01)
    assert t0.stop == pytest.approx(145.0, abs=0.01)
    assert t0.entry_ts == pd.Timestamp(idx[12].tz_convert(None))


def test_analyze_threshold_mode_default_emits_on_sweep_bar(monkeypatch):
    """Default TOML (reentry_threshold_points=7) places advance stop on the sweep bar close at H-7."""
    monkeypatch.setenv("MORNING_RANGE_REVERSION_SIGNAL_REQUIRE_REENTRY_CLOSE", "true")
    # Explicit env so the test is robust against future TOML default changes.
    monkeypatch.setenv("MORNING_RANGE_REVERSION_SIGNAL_REENTRY_THRESHOLD_POINTS", "7")
    from strategies.morning_range_reversion_strategy import MorningRangeReversionStrategy
    from strategies.strategy_base import StrategyConfig

    cfg = StrategyConfig(
        name="morning_range_reversion",
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
    t0 = _utc(2026, 1, 7, 12, 0)
    bars = []
    for i in range(12):
        bars.append({
            "timestamp": t0 + timedelta(minutes=5 * i),
            "open": 115, "high": 130, "low": 100, "close": 115, "volume": 1,
        })
    # Sweep bar: close above H=130 → immediate advance stop at H-7
    bars.append({
        "timestamp": t0 + timedelta(minutes=5 * 12),
        "open": 115, "high": 135, "low": 114, "close": 133, "volume": 1,
    })
    bars.append({
        "timestamp": t0 + timedelta(minutes=5 * 13),
        "open": 133, "high": 133, "low": 121, "close": 132, "volume": 1,
    })

    bot = _MockBot(bars[:1])
    strat = MorningRangeReversionStrategy(bot, cfg)
    sig = None
    k_at = None
    for k in range(1, len(bars) + 1):
        bot.bars = bars[:k]
        bot._current_bar_timestamp = bars[k - 1]["timestamp"]
        sig = asyncio.run(strat.analyze("MNQ"))
        if sig is not None:
            k_at = k
            break
    assert sig is not None and k_at == 13
    assert sig["action"] == "SHORT"
    assert sig["entry_price"] == pytest.approx(123.0, abs=0.01)
    assert sig["stop_loss"] == pytest.approx(145.0, abs=0.01)
    assert "sweep_advance_stop" in sig["reason"]


def test_backtest_engine_rejects_wrong_side_buy_stop():
    """A BUY STOP placed *below* the current market must not fill — the old simulator filled it
    whenever a subsequent bar's high was above the stop, which is exactly the bug that produced
    the impossible mode-A entries on MNQ 2026-05-19."""
    from core.backtest.engine import BacktestEngine
    from core.backtest.models import OrderSide, OrderType

    eng = BacktestEngine(initial_capital=10000.0)
    eng.set_last_close(28910.0)  # Market is well above where we'd want to "BUY STOP"
    order_id = eng.place_order(
        symbol="MNQ",
        side=OrderSide.BUY,
        quantity=1,
        order_type=OrderType.STOP,
        stop_price=28853.75,  # Wrong side — below market
        price=28853.75,
    )
    pending = next(o for o in eng.pending_orders if o.order_id == order_id)
    assert pending.placement_price == pytest.approx(28910.0)

    bar = pd.Series({"open": 28910.75, "high": 28911.50, "low": 28868.75, "close": 28892.50},
                    name=pd.Timestamp("2026-05-19 13:45", tz="UTC"))
    # bar.high (28911.5) > stop (28853.75) so the old simulator would have filled here.
    filled = eng._check_order_fill(pending, bar, tick_size=0.25)
    assert filled is False
    assert pending.filled_price is None


def test_backtest_engine_allows_valid_stop_direction():
    """Sanity: a normal SELL STOP placed *below* current market still fills when bar.low <= stop."""
    from core.backtest.engine import BacktestEngine
    from core.backtest.models import OrderSide, OrderType

    eng = BacktestEngine(initial_capital=10000.0)
    eng.set_last_close(28910.0)
    order_id = eng.place_order(
        symbol="MNQ",
        side=OrderSide.SELL,
        quantity=1,
        order_type=OrderType.STOP,
        stop_price=28902.5,  # Below current market — valid SELL STOP
        price=28902.5,
    )
    pending = next(o for o in eng.pending_orders if o.order_id == order_id)
    bar = pd.Series({"open": 28910.0, "high": 28912.0, "low": 28890.0, "close": 28895.0},
                    name=pd.Timestamp("2026-05-19 13:45", tz="UTC"))
    filled = eng._check_order_fill(pending, bar, tick_size=0.25)
    assert filled is True
    assert pending.filled_price == pytest.approx(28902.5 - 0.125, abs=0.001)


def test_morning_range_toml_has_et_executor_window():
    """Stock TOML widens ``should_trade`` before 07:00 ET and uses empty no-trade band."""
    from pathlib import Path
    import tomllib

    path = Path(__file__).resolve().parent.parent / "config/strategies/morning_range_reversion.toml"
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    assert data.get("start_time") == "06:55"
    assert data.get("end_time") == "16:00"
    assert data.get("no_trade_start") == ""
    assert data.get("no_trade_end") == ""
    assert isinstance(data.get("meta", {}).get("enabled"), bool)


def test_in_trading_window_uses_session_timezone_not_local_naive_clock(monkeypatch):
    """Regression: executor gate uses ``_session_tz_wall_now`` (session TZ), not naive local clock."""
    from zoneinfo import ZoneInfo

    from strategies.morning_range_reversion_strategy import MorningRangeReversionStrategy
    from strategies.strategy_base import StrategyConfig

    z = ZoneInfo("America/New_York")
    cfg = StrategyConfig(
        name="morning_range_reversion",
        enabled=True,
        symbols=["MNQ"],
        max_positions=1,
        position_size=1,
        risk_per_trade_percent=0.5,
        max_daily_trades=4,
        preferred_conditions=[],
        avoid_conditions=[],
        trading_start_time="06:55",
        trading_end_time="16:00",
        no_trade_start="",
        no_trade_end="",
    )
    bot = _MockBot([])
    bot._is_strategy_replay = False
    strat = MorningRangeReversionStrategy(bot, cfg)

    monkeypatch.setattr(strat, "_session_tz_wall_now", lambda: datetime(2026, 5, 12, 7, 30, tzinfo=z))
    assert strat._in_trading_window() is True
    monkeypatch.setattr(strat, "_session_tz_wall_now", lambda: datetime(2026, 5, 12, 6, 30, tzinfo=z))
    assert strat._in_trading_window() is False
    monkeypatch.setattr(strat, "_session_tz_wall_now", lambda: datetime(2026, 5, 12, 16, 1, tzinfo=z))
    assert strat._in_trading_window() is False

    bot._is_strategy_replay = True
    assert strat._in_trading_window() is True


# ── R-geometry / range-width filter tests ────────────────────────────────────

def _make_range_df(range_width: float, sweep: str = "low"):
    """Minimal sieve DataFrame: range bars then a sweep bar."""
    import numpy as np

    rows = []
    # 7:00–7:55 ET = 11:00–11:55 UTC on a winter day
    t0 = pd.Timestamp("2026-01-07 12:00:00", tz="UTC").tz_localize(None)
    mid_price = 200.0
    H = mid_price + range_width / 2
    L = mid_price - range_width / 2
    for i in range(12):  # 12 × 5m = 60 min range window
        rows.append({"open": mid_price, "high": H, "low": L, "close": mid_price})
    # First close outside: sweep bar
    if sweep == "low":
        rows.append({"open": L - 1, "high": L - 0.5, "low": L - 5, "close": L - 4})
    else:
        rows.append({"open": H + 1, "high": H + 5, "low": H + 0.5, "close": H + 4})
    # A few more bars so the stop-entry can fill
    for _ in range(5):
        rows.append({"open": mid_price, "high": H + 1, "low": L - 1, "close": mid_price})
    df = pd.DataFrame(rows)
    idx = [t0 + pd.Timedelta(minutes=5 * i) for i in range(len(rows))]
    df.index = pd.DatetimeIndex(idx)
    return df, H, L


def test_sieve_min_range_width_skips_narrow_range():
    """Trades with width < min_range_width_points are skipped entirely (fixes TP-in-loss bug)."""
    from strategies.morning_range_reversion_strategy import sieve_simulate_from_ohlcv

    df, _, _ = _make_range_df(range_width=10.0)
    # Without filter: should produce a trade (even though TP ≈ entry for 10pt range)
    trades_no_filter = sieve_simulate_from_ohlcv(df, reentry_threshold_points=7.0)
    assert len(trades_no_filter) >= 1

    # With filter requiring ≥ 20 pts: must skip this 10pt range day entirely
    trades_filtered = sieve_simulate_from_ohlcv(
        df, reentry_threshold_points=7.0, min_range_width_points=20.0
    )
    assert len(trades_filtered) == 0


def test_sieve_max_range_width_skips_wide_range():
    """Trades with width > max_range_width_points are skipped (caps oversized risk)."""
    from strategies.morning_range_reversion_strategy import sieve_simulate_from_ohlcv

    df, _, _ = _make_range_df(range_width=200.0)
    trades_no_filter = sieve_simulate_from_ohlcv(df, reentry_threshold_points=7.0)
    assert len(trades_no_filter) >= 1

    trades_filtered = sieve_simulate_from_ohlcv(
        df, reentry_threshold_points=7.0, max_range_width_points=100.0
    )
    assert len(trades_filtered) == 0


def test_sieve_sl_fixed_pts_places_stop_relative_to_entry():
    """sl_fixed_pts places stop sl_fixed_pts away from entry, not at L-half."""
    from strategies.morning_range_reversion_strategy import sieve_simulate_from_ohlcv

    df, H, L = _make_range_df(range_width=60.0, sweep="low")
    threshold = 7.0
    sl_pts = 14.0

    # Without sl_fixed_pts: stop at L - half = L - 30
    trades_legacy = sieve_simulate_from_ohlcv(
        df, reentry_threshold_points=threshold, sl_fixed_pts=0.0
    )
    # With sl_fixed_pts: stop at entry - sl_pts = (L + 7) - 14 = L - 7
    trades_fixed = sieve_simulate_from_ohlcv(
        df, reentry_threshold_points=threshold, sl_fixed_pts=sl_pts
    )

    assert len(trades_legacy) == len(trades_fixed) == 1
    t_legacy = trades_legacy[0]
    t_fixed = trades_fixed[0]

    half = 30.0
    expected_stop_legacy = pytest.approx(L - half, abs=0.5)
    expected_stop_fixed = pytest.approx(t_fixed.entry - sl_pts, abs=0.5)

    assert t_legacy.stop == expected_stop_legacy
    assert t_fixed.stop == expected_stop_fixed
    # Fixed stop is much tighter (closer to entry) than legacy
    assert abs(t_fixed.stop - t_fixed.entry) < abs(t_legacy.stop - t_legacy.entry)


# ── Live data-freshness guard tests ──────────────────────────────────────────

class _LiveMockBot:
    """Mock bot that does NOT set ``_is_strategy_replay`` — guard runs against wall-clock."""

    def __init__(self, bars):
        self.bars = bars
        self.selected_account = {"id": "test", "name": "TEST"}
        self._is_strategy_replay = False
        self._current_bar_timestamp = None

    async def get_historical_data(self, symbol, timeframe=None, limit=None, **_):
        if limit:
            return self.bars[-limit:]
        return self.bars

    async def get_open_positions(self, account_id=None, **_):
        return []


def _live_cfg():
    from strategies.strategy_base import StrategyConfig

    return StrategyConfig(
        name="morning_range_reversion",
        enabled=True,
        symbols=["MNQ"],
        max_positions=1,
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


def _bars_ending_at(end_ts, n=20, step_minutes=5):
    """Make ``n`` bars whose last timestamp is ``end_ts`` (i.e., walking backward from end)."""
    return [
        {
            "timestamp": end_ts - timedelta(minutes=step_minutes * (n - 1 - i)),
            "open": 100, "high": 110, "low": 90, "close": 100, "volume": 1,
        }
        for i in range(n)
    ]


def test_freshness_guard_aborts_when_last_bar_older_than_threshold(monkeypatch, caplog):
    """Recreates 2026-05-21 outage: REST returned bars frozen 70+ min behind wall clock."""
    monkeypatch.setenv("MORNING_RANGE_REVERSION_SIGNAL_MAX_BAR_STALENESS_SECONDS", "600")
    from strategies.morning_range_reversion_strategy import MorningRangeReversionStrategy

    last_ts = datetime.now(timezone.utc) - timedelta(hours=1, minutes=10)  # 70 min behind
    bars = _bars_ending_at(last_ts)
    bot = _LiveMockBot(bars)
    strat = MorningRangeReversionStrategy(bot, _live_cfg())

    with caplog.at_level("ERROR", logger="strategies.morning_range_reversion_strategy"):
        result = asyncio.run(strat.analyze("MNQ"))

    assert result is None
    assert any("STALE DATA" in r.getMessage() for r in caplog.records)


def test_freshness_guard_throttles_repeated_log_within_60s(monkeypatch, caplog):
    """Three back-to-back analyze calls with stale data → only one ERROR line (60s throttle)."""
    monkeypatch.setenv("MORNING_RANGE_REVERSION_SIGNAL_MAX_BAR_STALENESS_SECONDS", "600")
    from strategies.morning_range_reversion_strategy import MorningRangeReversionStrategy

    last_ts = datetime.now(timezone.utc) - timedelta(hours=1, minutes=10)
    bars = _bars_ending_at(last_ts)
    bot = _LiveMockBot(bars)
    strat = MorningRangeReversionStrategy(bot, _live_cfg())

    with caplog.at_level("ERROR", logger="strategies.morning_range_reversion_strategy"):
        for _ in range(3):
            assert asyncio.run(strat.analyze("MNQ")) is None

    stale_records = [r for r in caplog.records if "STALE DATA" in r.getMessage()]
    assert len(stale_records) == 1, "guard should throttle to 1 ERROR within 60s"


def test_freshness_guard_passes_through_fresh_bars(monkeypatch):
    """Bars within the threshold do not trip the guard."""
    monkeypatch.setenv("MORNING_RANGE_REVERSION_SIGNAL_MAX_BAR_STALENESS_SECONDS", "600")
    from strategies.morning_range_reversion_strategy import MorningRangeReversionStrategy

    last_ts = datetime.now(timezone.utc) - timedelta(minutes=2)  # within 600s threshold
    bars = _bars_ending_at(last_ts)
    bot = _LiveMockBot(bars)
    strat = MorningRangeReversionStrategy(bot, _live_cfg())

    assert strat._bars_are_stale("MNQ", bars) is False


def test_freshness_guard_bypassed_during_replay(monkeypatch):
    """Replay (``_is_strategy_replay=True``) bypasses the guard even on bars that are hours old."""
    monkeypatch.setenv("MORNING_RANGE_REVERSION_SIGNAL_MAX_BAR_STALENESS_SECONDS", "600")
    from strategies.morning_range_reversion_strategy import MorningRangeReversionStrategy

    last_ts = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    bars = _bars_ending_at(last_ts)
    bot = _MockBot(bars)  # _MockBot sets _is_strategy_replay = True
    strat = MorningRangeReversionStrategy(bot, _live_cfg())

    assert strat._bars_are_stale("MNQ", bars) is False


def test_freshness_guard_disabled_when_threshold_zero(monkeypatch):
    """Setting threshold = 0 disables the guard (legacy behaviour for opt-out)."""
    monkeypatch.setenv("MORNING_RANGE_REVERSION_SIGNAL_MAX_BAR_STALENESS_SECONDS", "0")
    from strategies.morning_range_reversion_strategy import MorningRangeReversionStrategy

    last_ts = datetime.now(timezone.utc) - timedelta(hours=6)
    bars = _bars_ending_at(last_ts)
    bot = _LiveMockBot(bars)
    strat = MorningRangeReversionStrategy(bot, _live_cfg())

    assert strat._bars_are_stale("MNQ", bars) is False
