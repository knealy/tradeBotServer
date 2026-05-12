"""Intrabar 1m alignment helpers for StrategyReplayEngine."""

from datetime import datetime, timezone

import pandas as pd

from core.backtest.strategy_replay import (
    intrabar_series_iter,
    replay_timeframe_to_minutes,
    sort_bars_1m_for_replay,
)


def _ts(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def test_replay_timeframe_to_minutes():
    assert replay_timeframe_to_minutes("5m") == 5
    assert replay_timeframe_to_minutes("1h") == 60
    assert replay_timeframe_to_minutes(None) == 1


def test_intrabar_falls_back_to_aggregate_when_no_1m():
    t0 = pd.Timestamp("2024-01-02 14:00:00", tz="UTC")
    agg = pd.Series({"open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 0}, name=t0)
    out = list(intrabar_series_iter(t0, agg, [], [], 5))
    assert len(out) == 1
    assert out[0]["high"] == 2


def test_intrabar_five_one_minute_rows_inside_five_minute_window():
    t0 = pd.Timestamp("2024-01-02 14:00:00", tz="UTC")
    agg = pd.Series({"open": 1, "high": 5, "low": 1, "close": 3, "volume": 0}, name=t0)
    bars_1m = []
    for i in range(5):
        ts = t0 + pd.Timedelta(minutes=i)
        bars_1m.append(
            {
                "timestamp": _ts(ts.to_pydatetime()),
                "open": 1.0 + i,
                "high": 1.1 + i,
                "low": 0.9 + i,
                "close": 1.0 + i,
                "volume": 1,
            }
        )
    s1, ns = sort_bars_1m_for_replay(bars_1m)
    sub = list(intrabar_series_iter(t0, agg, s1, ns, 5))
    assert len(sub) == 5
    assert sub[0]["open"] == 1.0
    assert sub[4]["close"] == 5.0


def test_intrabar_gap_yields_aggregate_bar():
    """No 1m rows in [t0, t0+5m) → single aggregate series (same as 5m-only replay)."""
    t0 = pd.Timestamp("2024-01-02 14:00:00", tz="UTC")
    agg = pd.Series({"open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 0}, name=t0)
    bars_1m = [
        {
            "timestamp": _ts((t0 + pd.Timedelta(hours=2)).to_pydatetime()),
            "open": 9,
            "high": 9,
            "low": 9,
            "close": 9,
            "volume": 1,
        }
    ]
    s1, ns = sort_bars_1m_for_replay(bars_1m)
    out = list(intrabar_series_iter(t0, agg, s1, ns, 5))
    assert len(out) == 1
    assert out[0]["high"] == agg["high"]
