"""Range history backfill helpers."""

from core.range_history_backfill import (
    attach_range_window_et,
    chart_bars_to_ohlcv_df,
    merge_ohlcv_dataframes,
    merge_session_maps,
)


def test_merge_session_maps_later_overrides():
    a = {"2026-06-14": {"MNQ": {"high": 21000.0, "low": 20900.0}}}
    b = {"2026-06-14": {"MNQ": {"high": 21100.0, "low": 21000.0}}}
    merged = merge_session_maps(a, b)
    assert merged[0][0] == "2026-06-14"
    assert merged[0][1]["MNQ"]["high"] == 21100.0


def test_attach_range_window_et_mrr_seven_am_et():
    blob = {
        "session_date": "2026-06-15",
        "high": 30623.0,
        "low": 30554.0,
        # Legacy bug: naive UTC stored in *_et fields (11:00 UTC = 7am ET).
        "session_start_et": "2026-06-15T11:00:00",
        "session_end_et": "2026-06-15T11:59:59.999999",
    }
    out = attach_range_window_et(blob, "morning_range_reversion")
    assert "07:00" in out["session_start_et"]
    assert "-04:00" in out["session_start_et"] or "-05:00" in out["session_start_et"]


def test_attach_range_window_et_orb_nine_thirty_et():
    blob = {"session_date": "2026-06-15", "high": 1.0, "low": 0.0}
    out = attach_range_window_et(blob, "opening_range_breakout")
    assert "09:30" in out["session_start_et"]
    assert "10:29" in out["session_end_et"] or "10:30" in out["session_end_et"]

    a = {"2026-06-12": {"MNQ": {"high": 1.0, "low": 0.0}}}
    b = {"2026-06-15": {"MNQ": {"high": 2.0, "low": 1.0}}}
    merged = merge_session_maps(a, b)
    assert [sd for sd, _ in merged] == ["2026-06-15", "2026-06-12"]


def test_chart_bars_to_ohlcv_df_unix_time():
    df = chart_bars_to_ohlcv_df([
        {"time": 1_700_000_000, "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 10},
    ])
    assert df is not None
    assert len(df) == 1
    assert float(df.iloc[0]["high"]) == 2.0


def test_merge_ohlcv_dataframes_api_wins_on_overlap():
    import pandas as pd
    from core.range_history_backfill import _ohlcv_index_naive_utc, merge_ohlcv_dataframes

    idx = pd.to_datetime(["2026-06-15 11:00:00", "2026-06-15 11:01:00"])
    csv_df = pd.DataFrame(
        {"open": [1.0, 1.0], "high": [2.0, 2.0], "low": [0.5, 0.5], "close": [1.5, 1.5], "volume": [1, 1]},
        index=idx,
    )
    aware_idx = pd.to_datetime(["2026-06-15 11:00:00"], utc=True)
    api_df = pd.DataFrame(
        {"open": [1.0], "high": [3.0], "low": [0.5], "close": [2.5], "volume": [2]},
        index=aware_idx,
    )
    merged = merge_ohlcv_dataframes(csv_df, api_df)
    assert merged is not None
    assert float(merged.iloc[0]["high"]) == 3.0
    assert merged.index.tz is None
