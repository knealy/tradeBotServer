"""Case-insensitive OHLCV → LWC bar dicts (broker PascalCase vs Databento lowercase)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import StringIO

import pandas as pd

from core.backtest.ohlcv import (
    dataframe_to_chart_bars_unix,
    deroll_dual_contract_bars,
    sanitize_ohlcv_ohlc,
    snap_trade_unix_to_chart_bar_open,
)


def test_pascal_case_topstep_style_columns():
    buf = StringIO(
        "Time,Open,High,Low,Close,Volume\n"
        "2026-05-01T12:00:00+00:00,100.0,101.0,99.0,100.5,10\n"
    )
    df = pd.read_csv(buf, parse_dates=["Time"])
    df = df.set_index("Time")
    bars, times = dataframe_to_chart_bars_unix(df)
    assert len(bars) == 1
    assert bars[0]["open"] == 100.0
    assert bars[0]["high"] == 101.0
    assert bars[0]["low"] == 99.0
    assert bars[0]["close"] == 100.5
    assert bars[0]["volume"] == 10
    assert times[0] == bars[0]["timestamp"]


def test_lowercase_databento_style_columns():
    buf = StringIO(
        "timestamp,open,high,low,close,volume\n"
        "2023-05-03 00:00:00,1.0,2.0,0.5,1.5,5\n"
    )
    df = pd.read_csv(buf, parse_dates=["timestamp"])
    df = df.set_index("timestamp")
    bars, _ = dataframe_to_chart_bars_unix(df)
    assert bars[0]["open"] == 1.0
    assert bars[0]["close"] == 1.5


def test_duplicate_index_timestamps_last_row_wins():
    buf = StringIO(
        "timestamp,open,high,low,close,volume\n"
        "2023-05-03 00:00:00,1.0,2.0,0.5,1.5,5\n"
        "2023-05-03 00:00:00,9.0,9.0,9.0,9.0,99\n"
    )
    df = pd.read_csv(buf, parse_dates=["timestamp"])
    df = df.set_index("timestamp")
    bars, times = dataframe_to_chart_bars_unix(df)
    assert len(bars) == 1
    assert len(times) == 1
    assert bars[0]["open"] == 9.0
    assert bars[0]["close"] == 9.0
    assert bars[0]["volume"] == 99


def test_snap_trade_unix_to_containing_bar_open_not_nearest_neighbor():
    """Fills mid-minute must map to the bar that covers that minute, not the next open."""
    bar_times = [1_000_000, 1_000_060, 1_000_120]
    assert snap_trade_unix_to_chart_bar_open(1_000_030, bar_times) == 1_000_000
    assert snap_trade_unix_to_chart_bar_open(1_000_060, bar_times) == 1_000_060
    assert snap_trade_unix_to_chart_bar_open(999_000, bar_times) == 1_000_000
    assert snap_trade_unix_to_chart_bar_open(9_999_999, bar_times) == 1_000_120


def test_snap_trade_empty_bar_times_returns_target():
    assert snap_trade_unix_to_chart_bar_open(42, []) == 42


def test_sanitize_ohlcv_clips_deep_lower_wick_beyond_body():
    o, h, lo, c = 25025.0, 25150.0, 24799.0, 25010.0
    o2, h2, lo2, c2 = sanitize_ohlcv_ohlc(o, h, lo, c, max_body_wick_pt=200.0)
    assert lo2 >= min(o2, c2) - 200.0 - 1e-6
    assert lo2 > 24799.0  # pulled up from bogus deep print


def test_sanitize_ohlcv_preserves_normal_inside_bar_wick():
    o, h, lo, c = 25000.0, 25040.0, 24985.0, 25010.0
    o2, h2, lo2, c2 = sanitize_ohlcv_ohlc(o, h, lo, c, max_body_wick_pt=200.0)
    assert (o2, h2, lo2, c2) == (o, h, lo, c)


def _bar(ts: datetime, close: float, *, vol: int = 100) -> dict:
    """Test helper: minimal OHLC bar around a given close."""
    return {
        "timestamp": ts,
        "open": close,
        "high": close + 1.0,
        "low": close - 1.0,
        "close": close,
        "volume": vol,
    }


def test_deroll_passes_through_single_contract_day():
    """A clean single-contract session: nothing should be dropped."""
    base = datetime(2026, 5, 1, 0, 0, tzinfo=timezone.utc)
    bars = []
    # Smooth random-walk-style sequence, 200 bars, no >100pt minute jumps.
    closes = [24500.0]
    for i in range(199):
        closes.append(closes[-1] + (5.0 if i % 2 == 0 else -3.0))
    bars = [_bar(base + timedelta(minutes=i), closes[i]) for i in range(200)]
    out = deroll_dual_contract_bars(bars)
    assert len(out) == len(bars), "clean day should pass through unchanged"


def test_deroll_drops_phantom_track_in_alternating_session():
    """Mixed-contract day where the CSV alternates between two tracks 200pt apart."""
    # Anchor (clean) session on day 2 around 24800 — continuing track.
    base_day1 = datetime(2026, 3, 16, 0, 0, tzinfo=timezone.utc)
    base_day2 = datetime(2026, 3, 17, 0, 0, tzinfo=timezone.utc)

    # Day 1: ~200 bars, alternating between 24500 (phantom) and 24800 (real).
    day1: list = []
    for i in range(200):
        c = 24500.0 if (i % 3) != 0 else 24800.0  # phantom dominant
        day1.append(_bar(base_day1 + timedelta(minutes=i), c))

    # Day 2: clean, all bars near 24820 (real-track continuation).
    day2 = [_bar(base_day2 + timedelta(minutes=i), 24820.0 + (i % 5)) for i in range(200)]

    bars = day1 + day2
    out = deroll_dual_contract_bars(bars)

    day1_kept = [b for b in out if b["timestamp"].date() == base_day1.date()]
    day2_kept = [b for b in out if b["timestamp"].date() == base_day2.date()]
    assert len(day2_kept) == 200, "anchor day must pass through untouched"
    # Phantom (24500) bars should be gone, real (24800) ones kept.
    assert day1_kept, "should keep the continuing track, not drop everything"
    assert all(b["close"] > 24700.0 for b in day1_kept), \
        f"deroller kept phantom track: closes={[b['close'] for b in day1_kept[:5]]}"
    assert len(day1_kept) < 200, "phantom-track bars should have been dropped"


def test_deroll_does_not_drop_high_volatility_single_contract_day():
    """A volatile single-contract day (CPI / FOMC) must not be flagged as roll.

    Wide intraday range, but each bar moves smoothly from the previous one
    (no large adjacent-bar jumps). Deroller must not touch it.
    """
    base = datetime(2026, 4, 30, 12, 0, tzinfo=timezone.utc)
    # 250 bars, monotonic drift of ~500pt with normal noise, no 100pt jumps.
    bars = []
    c = 24000.0
    for i in range(250):
        c += 2.0 + (5 if i % 7 == 0 else 0)  # small steps
        bars.append(_bar(base + timedelta(minutes=i), c))
    out = deroll_dual_contract_bars(bars)
    assert len(out) == len(bars), "smooth high-vol day must pass through"


def test_deroll_drops_isolated_single_bar_phantom_on_clean_day():
    """Second-pass: a single off-track bar inside an otherwise-clean session must be removed."""
    base = datetime(2026, 3, 17, 0, 0, tzinfo=timezone.utc)
    # 300 smooth bars near 24820, with three isolated 210pt dips at indexes 50/100/200.
    bars = []
    for i in range(300):
        c = 24820.0 + (i % 7) * 0.5
        if i in (50, 100, 200):
            c -= 210.0  # phantom spike down
        bars.append(_bar(base + timedelta(minutes=i), c))
    out = deroll_dual_contract_bars(bars)
    assert len(out) == 297, f"expected 3 isolated phantoms removed, got {len(bars) - len(out)}"
    # Confirm the surviving bars never dip 200pt below the neighbours
    for i in range(1, len(out) - 1):
        c_prev = float(out[i - 1]["close"])
        c_curr = float(out[i]["close"])
        c_next = float(out[i + 1]["close"])
        assert not (
            abs(c_curr - c_prev) > 100 and abs(c_next - c_curr) > 100
            and abs(c_next - c_prev) < 50
        ), f"isolated phantom survived at index {i}: {c_prev} {c_curr} {c_next}"


def test_deroll_preserves_bar_order():
    """Output must remain time-ordered (replay engines rely on this)."""
    base = datetime(2026, 3, 16, 0, 0, tzinfo=timezone.utc)
    bars = []
    for i in range(200):
        c = 24500.0 if (i % 3) != 0 else 24800.0
        bars.append(_bar(base + timedelta(minutes=i), c))
    # Append a clean day so the deroller has an anchor.
    base2 = datetime(2026, 3, 17, 0, 0, tzinfo=timezone.utc)
    for i in range(200):
        bars.append(_bar(base2 + timedelta(minutes=i), 24820.0))
    out = deroll_dual_contract_bars(bars)
    for a, b in zip(out, out[1:]):
        assert a["timestamp"] <= b["timestamp"]
