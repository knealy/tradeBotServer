"""Rolling range history appended on persist_range_snapshot writes."""

from strategies.strategy_base import append_range_history


def test_append_range_history_dedupes_by_session_date():
    settings = {}
    snap_a = {"MNQ": {"high": 21000.0, "low": 20900.0, "session_date": "2026-06-14"}}
    append_range_history(settings, "or_ranges", snap_a, "2026-06-14T12:00:00+00:00")
    assert len(settings["or_ranges_history"]) == 1
    snap_b = {"MNQ": {"high": 21100.0, "low": 21000.0, "session_date": "2026-06-14"}}
    append_range_history(settings, "or_ranges", snap_b, "2026-06-14T18:00:00+00:00")
    assert len(settings["or_ranges_history"]) == 1
    assert settings["or_ranges_history"][0]["ranges"]["MNQ"]["high"] == 21100.0


def test_append_range_history_keeps_multiple_sessions():
    settings = {}
    append_range_history(
        settings,
        "mrr_ranges",
        {"MES": {"high": 5800.0, "low": 5790.0, "session_date": "2026-06-13"}},
        "2026-06-13T14:00:00+00:00",
    )
    append_range_history(
        settings,
        "mrr_ranges",
        {"MES": {"high": 5810.0, "low": 5800.0, "session_date": "2026-06-14"}},
        "2026-06-14T14:00:00+00:00",
    )
    assert len(settings["mrr_ranges_history"]) == 2
    assert settings["mrr_ranges_history"][0]["session_date"] == "2026-06-14"


def test_append_range_history_caps_entries():
    settings = {}
    for day in range(25):
        sd = f"2026-01-{day + 1:02d}"
        append_range_history(
            settings,
            "orb_ranges",
            {"MNQ": {"high": 20000.0 + day, "low": 19900.0 + day, "session_date": sd}},
            f"{sd}T15:00:00+00:00",
        )
    assert len(settings["orb_ranges_history"]) == 20
