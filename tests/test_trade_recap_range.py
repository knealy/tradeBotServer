"""Trade recap range overlay — session anchor parity with walkforward."""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from core.range_history_backfill import range_anchor_for_trade_session


def _bars_df(rows):
    idx = pd.DatetimeIndex([r[0] for r in rows])
    return pd.DataFrame(
        {
            "open": [r[1] for r in rows],
            "high": [r[2] for r in rows],
            "low": [r[3] for r in rows],
            "close": [r[4] for r in rows],
        },
        index=idx,
    )


def test_mrr_range_anchor_7_8am_et_from_5m_df():
    # 2026-06-10 7:00–7:55 ET = 11:00–11:55 UTC (EDT)
    rows = [
        ("2026-06-10 11:00:00", 100, 101, 99, 100.5),
        ("2026-06-10 11:05:00", 100.5, 102, 100, 101),
        ("2026-06-10 11:30:00", 101, 103, 100.5, 102),
        ("2026-06-10 12:00:00", 102, 110, 101, 109),  # after 8am ET — excluded
    ]
    df = _bars_df(rows)
    entry = datetime(2026, 6, 10, 12, 30, tzinfo=timezone.utc)  # 8:30 ET
    anchor = range_anchor_for_trade_session(
        entry, "MNQ", "morning_range_reversion", df=df,
    )
    assert anchor is not None
    assert anchor["high"] == 103.0
    assert anchor["low"] == 99.0
    assert anchor["strategy_name"] == "morning_range_reversion"
    assert anchor.get("session_date") == "2026-06-10"
    assert anchor.get("derived") == "databento_ohlcv_anchor"


def test_strategy_candidates_prefers_hint():
    from gui.chart_html import _strategy_candidates_for_recap

    order = _strategy_candidates_for_recap("morning_range_reversion")
    assert order[0] == "morning_range_reversion"
    assert len(order) == 3
