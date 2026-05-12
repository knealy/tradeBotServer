"""Smoke tests for morning_range_reversion (sieve + wiring)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pandas as pd


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
    monkeypatch.setenv("MORNING_RANGE_REVERSION_SIGNAL_REQUIRE_REENTRY_CLOSE", "true")
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
