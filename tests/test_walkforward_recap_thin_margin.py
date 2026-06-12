"""Pinning tests for the thin-margin diagnostic + 1m intrabar wiring added to
``scripts/walkforward_trade_recap_report.py`` after the 2026-06-11 MGC
backtest-vs-live discrepancy investigation.

Background:
    On 2026-06-11 the live ``morning_range_reversion`` MGC trade exited at the
    stop loss for −$534, while the walk-forward recap reported the same trade as
    a +$297 take_profit. Investigation showed (a) the recap was running the
    replay without ``--csv-1m`` (no intrabar truth) and (b) the trade hit
    97.8% of its SL distance before running to TP — a razor-thin margin the
    HTML report was not surfacing.

Both fixes live in ``scripts/walkforward_trade_recap_report.py``:
    * ``_auto_discover_1m_sibling`` finds a ``*_1m_*`` CSV next to a ``*_5m_*``
      CSV and is wired into ``_run_backtest_json``.
    * ``compute_trade_margin`` derives a "fraction of opposite-leg distance
      consumed" pct from MAE / MFE / initial_risk and flags trades >= 85%.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "walkforward_trade_recap_report.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("walkforward_trade_recap_report", SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["walkforward_trade_recap_report"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def mod():
    return _load_module()


# ─────────────────────────── _auto_discover_1m_sibling ──────────────────────────

def test_auto_discover_finds_databento_sibling(tmp_path: Path, mod):
    p5 = tmp_path / "MGC_5m_databento.csv"
    p1 = tmp_path / "MGC_1m_databento.csv"
    p5.write_text("ts,o,h,l,c,v\n")
    p1.write_text("ts,o,h,l,c,v\n")
    assert mod._auto_discover_1m_sibling(p5) == p1


def test_auto_discover_finds_lowercase_sibling(tmp_path: Path, mod):
    p5 = tmp_path / "mgc_5m.csv"
    p1 = tmp_path / "mgc_1m.csv"
    p5.write_text("ts,o,h,l,c,v\n")
    p1.write_text("ts,o,h,l,c,v\n")
    assert mod._auto_discover_1m_sibling(p5) == p1


def test_auto_discover_prefers_databento_over_plain(tmp_path: Path, mod):
    p5 = tmp_path / "MGC_5m_databento.csv"
    p1_db = tmp_path / "MGC_1m_databento.csv"
    p1_plain = tmp_path / "MGC_1m.csv"
    p5.write_text("ts,o,h,l,c,v\n")
    p1_db.write_text("ts,o,h,l,c,v\n")
    p1_plain.write_text("ts,o,h,l,c,v\n")
    # The first candidate is the suffix-replacement (databento → databento), so
    # we should land on the databento file rather than the bare ``MGC_1m.csv``.
    result = mod._auto_discover_1m_sibling(p5)
    assert result == p1_db


def test_auto_discover_returns_none_when_no_sibling(tmp_path: Path, mod):
    p5 = tmp_path / "MGC_5m_databento.csv"
    p5.write_text("ts,o,h,l,c,v\n")
    assert mod._auto_discover_1m_sibling(p5) is None


def test_auto_discover_returns_none_for_missing_5m(tmp_path: Path, mod):
    p5 = tmp_path / "does_not_exist_5m.csv"
    assert mod._auto_discover_1m_sibling(p5) is None


def test_auto_discover_handles_none(mod):
    assert mod._auto_discover_1m_sibling(None) is None  # type: ignore[arg-type]


# ─────────────────────────── compute_trade_margin ──────────────────────────────

def test_margin_thin_tp_exit_flags_the_2026_06_11_trade(mod):
    """The actual recap record for the 2026-06-11 MGC trade — MAE consumed 97.8%
    of the stop distance before running to TP. Must be flagged as thin."""
    trade = {
        "trade_id": "T000004",
        "exit_reason": "take_profit",
        "max_favorable_excursion": 290.99,
        "max_adverse_excursion": -541.0,
        "initial_risk_dollars": 553.0,
    }
    m = mod.compute_trade_margin(trade)
    assert m["kind"] == "sl_consumed"
    assert m["thin"] is True
    assert 95 < m["pct"] < 100


def test_margin_safe_tp_exit_is_not_thin(mod):
    trade = {
        "exit_reason": "take_profit",
        "max_adverse_excursion": -50.0,
        "initial_risk_dollars": 500.0,
    }
    m = mod.compute_trade_margin(trade)
    assert m["kind"] == "sl_consumed"
    assert m["thin"] is False
    assert m["pct"] == 10.0


def test_margin_sl_exit_uses_mfe(mod):
    trade = {
        "exit_reason": "stop_loss",
        "max_favorable_excursion": 450.0,
        "initial_risk_dollars": 500.0,
    }
    m = mod.compute_trade_margin(trade)
    assert m["kind"] == "tp_run"
    assert m["thin"] is True
    assert m["pct"] == 90.0


def test_margin_timeout_exit_is_na(mod):
    trade = {
        "exit_reason": "timeout",
        "max_adverse_excursion": -100.0,
        "max_favorable_excursion": 100.0,
        "initial_risk_dollars": 500.0,
    }
    m = mod.compute_trade_margin(trade)
    assert m["kind"] == "n/a"
    assert m["thin"] is False


def test_margin_zero_risk_returns_na(mod):
    trade = {
        "exit_reason": "take_profit",
        "max_adverse_excursion": -100.0,
        "initial_risk_dollars": 0.0,
    }
    m = mod.compute_trade_margin(trade)
    assert m["kind"] == "n/a"


def test_margin_missing_fields_safe(mod):
    trade = {"exit_reason": "take_profit"}
    m = mod.compute_trade_margin(trade)
    assert m["kind"] == "n/a"
    assert m["thin"] is False


def test_margin_boundary_exactly_85_pct_is_thin(mod):
    """The 85% threshold is INCLUSIVE; a trade that left exactly 15% of the
    SL distance unused (85% consumed) is still thin."""
    trade = {
        "exit_reason": "take_profit",
        "max_adverse_excursion": -85.0,
        "initial_risk_dollars": 100.0,
    }
    m = mod.compute_trade_margin(trade)
    assert m["pct"] == pytest.approx(85.0)
    assert m["thin"] is True


def test_margin_boundary_below_85_pct_not_thin(mod):
    trade = {
        "exit_reason": "take_profit",
        "max_adverse_excursion": -84.999,
        "initial_risk_dollars": 100.0,
    }
    m = mod.compute_trade_margin(trade)
    assert m["thin"] is False


# ─────────────────────────── HTML cell formatting ──────────────────────────────

def test_format_trade_margin_cell_thin(mod):
    trade = {
        "exit_reason": "take_profit",
        "max_adverse_excursion": -97.0,
        "initial_risk_dollars": 100.0,
    }
    cell = mod._format_trade_margin_cell(trade)
    assert "97.0" in cell
    assert "SL→" in cell
    assert "⚠" in cell
    assert "#b00020" in cell  # the red color used for thin trades


def test_format_trade_margin_cell_safe(mod):
    trade = {
        "exit_reason": "take_profit",
        "max_adverse_excursion": -10.0,
        "initial_risk_dollars": 100.0,
    }
    cell = mod._format_trade_margin_cell(trade)
    assert "10.0" in cell
    assert "⚠" not in cell
    assert "#b00020" not in cell


def test_format_trade_margin_cell_na(mod):
    cell = mod._format_trade_margin_cell({"exit_reason": "timeout"})
    assert "—" in cell


# ─────────────────────────── CLI surface ───────────────────────────────────────

def test_cli_advertises_csv_1m_template():
    import subprocess
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 0, r.stderr
    assert "--csv-1m-template" in r.stdout
