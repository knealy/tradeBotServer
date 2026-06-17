"""Tests for ``core.anchor_persistence`` — the session anchor recorder added
after the 2026-06-11 MGC backtest-vs-live discrepancy. The investigation
showed live and databento anchors differing by ~0.5 pt; this module is what
lets the diff tool catch that divergence in seconds."""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from core import anchor_persistence
from core.anchor_persistence import (
    anchor_json_path,
    load_anchor,
    load_session_activity,
    patch_session_activity,
    record_anchor,
)


# ─── path resolution ─────────────────────────────────────────────────────────

def test_anchor_json_path_uses_iso_session_date(tmp_path: Path):
    p = anchor_json_path(
        strategy="morning_range_reversion",
        symbol="MGC",
        session_date_et=date(2026, 6, 11),
        base_dir=tmp_path,
    )
    assert p == tmp_path / "morning_range_reversion_MGC_2026-06-11.json"


def test_anchor_json_path_honors_env_var(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("ANCHOR_PERSISTENCE_DIR", str(tmp_path / "from_env"))
    p = anchor_json_path(
        strategy="x", symbol="MGC", session_date_et=date(2026, 1, 2),
    )
    assert p == tmp_path / "from_env" / "x_MGC_2026-01-02.json"


def test_anchor_json_path_explicit_override_wins(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("ANCHOR_PERSISTENCE_DIR", "/nonexistent/env")
    explicit = tmp_path / "explicit"
    p = anchor_json_path(
        strategy="x", symbol="MGC", session_date_et=date(2026, 1, 2),
        base_dir=explicit,
    )
    assert p == explicit / "x_MGC_2026-01-02.json"


# ─── record_anchor ───────────────────────────────────────────────────────────

def test_record_anchor_writes_full_payload(tmp_path: Path):
    """Record the actual 2026-06-11 MGC databento anchor and verify the JSON
    shape downstream tools depend on."""
    first_bar = datetime(2026, 6, 11, 11, 0, 0, tzinfo=timezone.utc)
    last_bar = datetime(2026, 6, 11, 11, 55, 0, tzinfo=timezone.utc)
    p = record_anchor(
        strategy="morning_range_reversion",
        symbol="MGC",
        session_date_et=date(2026, 6, 11),
        high=4116.80,
        low=4100.10,
        window_start_et="07:00",
        window_end_et="08:00",
        n_bars_used=12,
        first_bar_ts_utc=first_bar,
        last_bar_ts_utc=last_bar,
        base_dir=tmp_path,
    )
    assert p is not None
    payload = json.loads(p.read_text())
    assert payload["strategy"] == "morning_range_reversion"
    assert payload["symbol"] == "MGC"
    assert payload["session_date_et"] == "2026-06-11"
    a = payload["anchor"]
    assert a["high"] == pytest.approx(4116.80)
    assert a["low"] == pytest.approx(4100.10)
    assert a["width"] == pytest.approx(16.70)
    assert a["mid"] == pytest.approx(4108.45)
    w = payload["window"]
    assert w["start_et"] == "07:00"
    assert w["end_et"] == "08:00"
    assert w["n_bars_used"] == 12
    assert w["first_bar_ts_utc"].startswith("2026-06-11T11:00:00")
    assert w["last_bar_ts_utc"].startswith("2026-06-11T11:55:00")
    # computed_at_utc must be ISO with a tz suffix so timezone-aware tools can parse it
    assert payload["computed_at_utc"].endswith(("+00:00", "Z"))


def test_record_anchor_no_overwrite_by_default(tmp_path: Path):
    p1 = record_anchor(
        strategy="s", symbol="X", session_date_et=date(2026, 1, 1),
        high=10.0, low=5.0, window_start_et="07:00", window_end_et="08:00",
        base_dir=tmp_path,
    )
    assert p1 is not None
    # Second call with different values should NOT overwrite
    p2 = record_anchor(
        strategy="s", symbol="X", session_date_et=date(2026, 1, 1),
        high=99.0, low=0.0, window_start_et="07:00", window_end_et="08:00",
        base_dir=tmp_path,
    )
    assert p2 is None
    payload = json.loads(p1.read_text())
    assert payload["anchor"]["high"] == 10.0
    assert payload["anchor"]["low"] == 5.0


def test_record_anchor_overwrite_flag(tmp_path: Path):
    record_anchor(
        strategy="s", symbol="X", session_date_et=date(2026, 1, 1),
        high=10.0, low=5.0, window_start_et="07:00", window_end_et="08:00",
        base_dir=tmp_path,
    )
    p2 = record_anchor(
        strategy="s", symbol="X", session_date_et=date(2026, 1, 1),
        high=99.0, low=0.0, window_start_et="07:00", window_end_et="08:00",
        base_dir=tmp_path, overwrite=True,
    )
    assert p2 is not None
    payload = json.loads(p2.read_text())
    assert payload["anchor"]["high"] == 99.0


def test_record_anchor_rejects_inverted_range(tmp_path: Path, caplog):
    p = record_anchor(
        strategy="s", symbol="X", session_date_et=date(2026, 1, 1),
        high=5.0, low=10.0,  # inverted
        window_start_et="07:00", window_end_et="08:00",
        base_dir=tmp_path,
    )
    assert p is None
    assert not anchor_json_path(
        strategy="s", symbol="X",
        session_date_et=date(2026, 1, 1), base_dir=tmp_path,
    ).exists()


def test_record_anchor_extra_merges_top_level(tmp_path: Path):
    extra = {
        "data_feed_health": {"available": True, "safe": False, "reason": "stale_market_hub"},
        "regime": "trend_up",
    }
    p = record_anchor(
        strategy="s", symbol="X", session_date_et=date(2026, 1, 1),
        high=10.0, low=5.0, window_start_et="07:00", window_end_et="08:00",
        extra=extra, base_dir=tmp_path,
    )
    payload = json.loads(p.read_text())
    assert payload["data_feed_health"]["safe"] is False
    assert payload["regime"] == "trend_up"


def test_record_anchor_extra_cannot_clobber_core(tmp_path: Path):
    p = record_anchor(
        strategy="s", symbol="X", session_date_et=date(2026, 1, 1),
        high=10.0, low=5.0, window_start_et="07:00", window_end_et="08:00",
        extra={"anchor": {"high": 999.0}, "regime": "ok"},
        base_dir=tmp_path,
    )
    payload = json.loads(p.read_text())
    # Core anchor must NOT have been overwritten by extra
    assert payload["anchor"]["high"] == 10.0
    assert payload["regime"] == "ok"


def test_record_anchor_io_failure_returns_none(monkeypatch, tmp_path: Path, caplog):
    """If the destination dir can't be created we must NOT raise — strategies
    keep trading even if persistence breaks."""
    def boom(*args, **kwargs):  # noqa: ARG001
        raise OSError("disk full")

    monkeypatch.setattr(Path, "mkdir", boom)
    p = record_anchor(
        strategy="s", symbol="X", session_date_et=date(2026, 1, 1),
        high=10.0, low=5.0, window_start_et="07:00", window_end_et="08:00",
        base_dir=tmp_path / "nope",
    )
    assert p is None


def test_record_anchor_zero_width_is_valid(tmp_path: Path):
    """Edge case: ``high == low`` is unusual but technically not an inverted
    range. We accept it and let downstream tools flag it."""
    p = record_anchor(
        strategy="s", symbol="X", session_date_et=date(2026, 1, 1),
        high=10.0, low=10.0,
        window_start_et="07:00", window_end_et="08:00",
        base_dir=tmp_path,
    )
    assert p is not None
    payload = json.loads(p.read_text())
    assert payload["anchor"]["width"] == 0.0


# ─── load_anchor ─────────────────────────────────────────────────────────────

def test_load_anchor_round_trip(tmp_path: Path):
    record_anchor(
        strategy="s", symbol="X", session_date_et=date(2026, 1, 1),
        high=10.0, low=5.0, window_start_et="07:00", window_end_et="08:00",
        n_bars_used=12, base_dir=tmp_path,
    )
    payload = load_anchor(
        strategy="s", symbol="X", session_date_et=date(2026, 1, 1),
        base_dir=tmp_path,
    )
    assert payload is not None
    assert payload["anchor"]["high"] == 10.0
    assert payload["window"]["n_bars_used"] == 12


def test_load_anchor_returns_none_when_missing(tmp_path: Path):
    payload = load_anchor(
        strategy="s", symbol="X", session_date_et=date(2026, 1, 1),
        base_dir=tmp_path,
    )
    assert payload is None


def test_load_anchor_handles_corrupt_file(tmp_path: Path):
    p = anchor_json_path(
        strategy="s", symbol="X", session_date_et=date(2026, 1, 1),
        base_dir=tmp_path,
    )
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("not json {")
    payload = load_anchor(
        strategy="s", symbol="X", session_date_et=date(2026, 1, 1),
        base_dir=tmp_path,
    )
    assert payload is None


def test_patch_session_activity_creates_stub_and_merges(tmp_path: Path):
    ok = patch_session_activity(
        strategy="morning_range_reversion",
        symbol="MGC",
        session_date_et=date(2026, 6, 17),
        patch={"sweep_fired_high": True, "fades_this_session": 1},
        base_dir=tmp_path,
    )
    assert ok is True
    activity = load_session_activity(
        strategy="morning_range_reversion",
        symbol="MGC",
        session_date_et=date(2026, 6, 17),
        base_dir=tmp_path,
    )
    assert activity["sweep_fired_high"] is True
    assert activity["fades_this_session"] == 1
    patch_session_activity(
        strategy="morning_range_reversion",
        symbol="MGC",
        session_date_et=date(2026, 6, 17),
        patch={"immediate_block_until_inside": True},
        base_dir=tmp_path,
    )
    activity2 = load_session_activity(
        strategy="morning_range_reversion",
        symbol="MGC",
        session_date_et=date(2026, 6, 17),
        base_dir=tmp_path,
    )
    assert activity2["sweep_fired_high"] is True
    assert activity2["immediate_block_until_inside"] is True


def test_mrr_blocks_repeat_sweep_after_session_restore(tmp_path: Path, monkeypatch):
    """Mid-session restart must not re-fire immediate_stop on the same sweep."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from strategies.morning_range_reversion_strategy import MorningRangeReversionStrategy
    from strategies.strategy_base import StrategyConfig

    cfg = StrategyConfig(
        name="morning_range_reversion", enabled=True, symbols=["MGC"],
        max_positions=2, position_size=1, risk_per_trade_percent=0.5,
        max_daily_trades=12, preferred_conditions=[], avoid_conditions=[],
        trading_start_time="00:00", trading_end_time="23:59",
        no_trade_start="", no_trade_end="",
    )
    bot = type("B", (), {"_is_strategy_replay": True})()
    strat = MorningRangeReversionStrategy(bot, cfg)
    monkeypatch.setenv("ANCHOR_PERSISTENCE_DIR", str(tmp_path))
    st = strat._get_state("MGC")
    st["sweep_fired_high"] = True
    st["immediate_block_until_inside"] = True
    st["fades_this_session"] = 1
    strat._persist_session_activity("MGC", date(2026, 6, 17), st)

    strat._state["MGC"] = strat._get_state("MGC")
    strat._state["MGC"]["fades_this_session"] = 0
    strat._state["MGC"]["sweep_fired_high"] = False
    strat._state["MGC"]["sweep_fired_low"] = False
    strat._state["MGC"]["immediate_block_until_inside"] = False
    strat._restore_session_activity("MGC", date(2026, 6, 17), strat._state["MGC"])
    assert strat._state["MGC"]["sweep_fired_high"] is True

    sig = strat._fade_signal_after_sweep(
        "MGC",
        datetime(2026, 6, 17, 11, 20, tzinfo=ZoneInfo("America/New_York")),
        "high",
        4351.60,
        4340.00,
        4345.80,
        11.60,
        "immediate_stop",
        bars=[{"close": 4370.0, "high": 4370.0, "low": 4360.0, "open": 4365.0}],
    )
    assert sig is None


def test_mrr_apply_sweep_guard_from_price_when_still_outside_range():
    from strategies.morning_range_reversion_strategy import MorningRangeReversionStrategy
    from strategies.strategy_base import StrategyConfig

    cfg = StrategyConfig(
        name="morning_range_reversion", enabled=True, symbols=["MGC"],
        max_positions=2, position_size=1, risk_per_trade_percent=0.5,
        max_daily_trades=12, preferred_conditions=[], avoid_conditions=[],
        trading_start_time="00:00", trading_end_time="23:59",
        no_trade_start="", no_trade_end="",
    )
    bot = type("B", (), {"_is_strategy_replay": True})()
    strat = MorningRangeReversionStrategy(bot, cfg)
    st = strat._get_state("MGC")
    strat._apply_sweep_guard_from_price("MGC", 4351.60, 4340.00, 4378.30, st)
    assert st["sweep_fired_high"] is True
    assert st["immediate_block_until_inside"] is True
